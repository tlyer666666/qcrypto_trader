import asyncio
import json
import math
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from qcrypto_trader.app import (
    render_index_html,
    resolve_api_token,
    verify_bearer_token,
    verify_csrf_headers,
)
from qcrypto_trader.backtest import Backtester
from qcrypto_trader.binance import (
    BinanceSigner,
    DEFAULT_FUTURES_MAX_LEVERAGE,
    build_order_params,
    parse_futures_ticker,
    parse_spot_ticker,
    parse_symbol_rules,
)
from qcrypto_trader.candidate_pool import CandidatePool
from qcrypto_trader.config import (
    AppConfig,
    BinanceCredentials,
    FuturesConfig,
    MarketConfig,
    RiskConfig,
    SpotConfig,
    StrategyConfig,
    TradingConfig,
    load_config,
)
from qcrypto_trader.engine import TradingEngine
from qcrypto_trader.execution import ExecutionEngine, LiveExecutionEngine, PaperExecutionEngine
from qcrypto_trader.models import (
    AccountState,
    ApprovedOrder,
    EntryType,
    MarketSnapshot,
    MarketType,
    OrderReceipt,
    OrderSide,
    OrderStatus,
    Position,
    PositionSide,
    Signal,
    SymbolRule,
    Ticker,
)
from qcrypto_trader.profiles import resolve_profile
from qcrypto_trader.risk import RiskEngine
from qcrypto_trader.simple_server import browser_url, create_simple_server
from qcrypto_trader.storage import Storage
from qcrypto_trader.strategy import ShortMomentumStrategy, StrategyContext


class FakeMarketData:
    def __init__(self, spot_tickers=None, futures_tickers=None, spot_rules=None, futures_rules=None):
        self._spot_tickers = spot_tickers or []
        self._futures_tickers = futures_tickers or []
        self._spot_rules = spot_rules or {}
        self._futures_rules = futures_rules or {}

    async def spot_tickers(self):
        return list(self._spot_tickers)

    async def futures_tickers(self):
        return list(self._futures_tickers)

    async def spot_rules(self):
        return dict(self._spot_rules)

    async def futures_rules(self):
        return dict(self._futures_rules)


class SlowExecutionEngine(ExecutionEngine):
    def __init__(self):
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.submitted: list[ApprovedOrder] = []

    async def submit(self, order: ApprovedOrder) -> OrderReceipt:
        self.submitted.append(order)
        self.started.set()
        await self.release.wait()
        return OrderReceipt(
            client_order_id=order.client_order_id,
            symbol=order.symbol,
            status=OrderStatus.FILLED,
            message="slow_fill",
            filled_quantity=order.quantity,
        )


class LedgerAndExitTests(unittest.IsolatedAsyncioTestCase):
    async def test_engine_updates_ledger_prevents_duplicates_and_triggers_exits(self):
        config = TradingConfig(
            market=MarketConfig(min_quote_volume=100, max_open_positions=3),
            futures=FuturesConfig(enabled=True, hedge_mode=True, default_leverage=5),
            spot=SpotConfig(enabled=False),
            risk=RiskConfig(max_notional_per_position=100, max_total_notional=300),
            strategy=StrategyConfig(min_confidence=0.1),
            leverage={"BTCUSDT": 5},
        )
        with tempfile.TemporaryDirectory() as tmp:
            storage = Storage(Path(tmp) / "audit.db")
            paper = PaperExecutionEngine()
            engine = TradingEngine(config, execution=paper, storage=storage)
            engine.set_symbol_rules(
                {
                    (MarketType.FUTURES, "BTCUSDT"): SymbolRule(
                        "BTCUSDT", 0.001, 0.001, 5, 0.01, max_leverage=20, market_type=MarketType.FUTURES
                    )
                }
            )
            t0 = datetime(2025, 1, 1, 0, 0, tzinfo=timezone.utc)
            engine.update_candidates(
                [Ticker("BTCUSDT", 100000.0, 1_000_000_000, 4.0, 102000, 98000, 99995, 100005, market_type=MarketType.FUTURES)]
            )

            await engine.run_once(now=t0)
            self.assertEqual(len(paper.receipts), 1)
            pos_key = f"{MarketType.FUTURES}:BTCUSDT:{PositionSide.LONG}"
            self.assertIn(pos_key, engine.account.open_positions)
            pos = engine.account.open_positions[pos_key]
            self.assertEqual(pos.quantity, 0.001)
            self.assertIsNotNone(pos.stop_loss)
            self.assertIsNotNone(pos.take_profit)
            self.assertEqual(pos.max_hold_seconds, 180)
            # Margin = 100 / 5 = 20 deducted from 1000 -> 980
            self.assertAlmostEqual(engine.account.available_balance, 980.0, places=6)
            self.assertEqual(storage.order_count(), 1)
            self.assertEqual(storage.audit_count(), 1)

            # Second run at same price must NOT duplicate the open position
            await engine.run_once(now=t0 + timedelta(seconds=10))
            self.assertEqual(len(paper.receipts), 1)
            self.assertEqual(engine.state.last_rejection, "BTCUSDT:position_already_open")

            # Price drops below stop-loss (99200) -> triggers stop-loss exit & updates PnL / consecutive_losses
            engine.update_candidates(
                [Ticker("BTCUSDT", 99000.0, 1_000_000_000, -1.0, 100000, 98500, 98995, 99005, market_type=MarketType.FUTURES)]
            )
            await engine.run_once(now=t0 + timedelta(seconds=20))
            # Exit closed LONG at 99000 (-1.0 PnL) and possibly opened SHORT
            self.assertNotIn(pos_key, engine.account.open_positions)
            self.assertAlmostEqual(engine.account.daily_pnl, -1.0, places=6)
            self.assertEqual(engine.account.consecutive_losses, 1)
            self.assertGreaterEqual(storage.audit_count(), 2)
            storage.close()

    async def test_engine_triggers_max_hold_seconds_exit_and_resets_consecutive_losses_on_profit(self):
        config = TradingConfig(
            market=MarketConfig(min_quote_volume=100),
            futures=FuturesConfig(enabled=True, hedge_mode=True, default_leverage=5),
            spot=SpotConfig(enabled=False),
            strategy=StrategyConfig(min_confidence=0.99),  # prevent re-entry after exit
        )
        paper = PaperExecutionEngine()
        engine = TradingEngine(config, execution=paper)
        engine.account.consecutive_losses = 2
        t0 = datetime(2025, 1, 1, 0, 0, tzinfo=timezone.utc)
        pos = Position(
            symbol="ETHUSDT",
            market_type=MarketType.FUTURES,
            position_side=PositionSide.LONG,
            quantity=0.1,
            entry_price=2000.0,
            leverage=5,
            stop_loss=1900.0,
            take_profit=2200.0,
            max_hold_seconds=180,
            opened_at=t0,
        )
        engine.account.open_positions[pos.key] = pos
        engine.account.available_balance = 960.0
        engine.update_candidates(
            [Ticker("ETHUSDT", 2050.0, 1_000_000_000, 0.5, market_type=MarketType.FUTURES)]
        )

        # After 200 seconds, max_hold_seconds triggers profitable exit (+5.0)
        await engine.run_once(now=t0 + timedelta(seconds=200))
        self.assertEqual(len(engine.account.open_positions), 0)
        self.assertAlmostEqual(engine.account.daily_pnl, 5.0, places=6)
        self.assertAlmostEqual(engine.account.available_balance, 1005.0, places=6)
        self.assertEqual(engine.account.consecutive_losses, 0)

    async def test_engine_prevents_in_flight_duplicate(self):
        config = TradingConfig(
            market=MarketConfig(min_quote_volume=100),
            futures=FuturesConfig(enabled=True, hedge_mode=True, default_leverage=5),
            spot=SpotConfig(enabled=False),
            strategy=StrategyConfig(min_confidence=0.1),
        )
        paper = PaperExecutionEngine()
        engine = TradingEngine(config, execution=paper)
        engine.set_symbol_rules(
            {(MarketType.FUTURES, "BTCUSDT"): SymbolRule("BTCUSDT", 0.001, 0.001, 5, 0.01, max_leverage=20)}
        )
        engine.update_candidates(
            [Ticker("BTCUSDT", 100000.0, 1_000_000_000, 4.0, market_type=MarketType.FUTURES)]
        )
        engine._in_flight.add((MarketType.FUTURES, "BTCUSDT"))
        await engine.run_once()
        self.assertEqual(len(paper.receipts), 0)
        self.assertEqual(engine.state.last_rejection, "BTCUSDT:order_in_flight")

    async def test_separate_spot_and_futures_rules_and_no_double_order(self):
        spot_rule_payload = {
            "symbols": [
                {
                    "symbol": "BTCUSDT",
                    "status": "TRADING",
                    "filters": [
                        {"filterType": "LOT_SIZE", "minQty": "0.0001", "stepSize": "0.0001"},
                        {"filterType": "MIN_NOTIONAL", "minNotional": "10"},
                        {"filterType": "PRICE_FILTER", "tickSize": "0.01"},
                    ],
                }
            ]
        }
        futures_rule_payload = {
            "symbols": [
                {
                    "symbol": "BTCUSDT",
                    "status": "TRADING",
                    "filters": [
                        {"filterType": "LOT_SIZE", "minQty": "0.001", "stepSize": "0.001"},
                        {"filterType": "MIN_NOTIONAL", "notional": "5"},
                        {"filterType": "PRICE_FILTER", "tickSize": "0.1"},
                    ],
                }
            ]
        }
        spot_rules = parse_symbol_rules(spot_rule_payload, futures=False)
        futures_rules = parse_symbol_rules(futures_rule_payload, futures=True)

        # Conservative default max_leverage instead of blindly 125
        self.assertEqual(futures_rules[(MarketType.FUTURES, "BTCUSDT")].max_leverage, DEFAULT_FUTURES_MAX_LEVERAGE)
        self.assertEqual(futures_rules[(MarketType.FUTURES, "BTCUSDT")].min_notional, 5.0)
        self.assertEqual(spot_rules[(MarketType.SPOT, "BTCUSDT")].min_notional, 10.0)
        self.assertEqual(spot_rules[(MarketType.SPOT, "BTCUSDT")].max_leverage, 1)

        # Only spot ticker present -> only spot order placed, NOT doubled on futures
        market_data = FakeMarketData(
            spot_tickers=[parse_spot_ticker({"symbol": "BTCUSDT", "lastPrice": "100000", "quoteVolume": "900000000", "priceChangePercent": "4", "bidPrice": "99999", "askPrice": "100001"})],
            futures_tickers=[],
            spot_rules=spot_rules,
            futures_rules=futures_rules,
        )
        config = TradingConfig(
            market=MarketConfig(min_quote_volume=100),
            spot=SpotConfig(enabled=True),
            futures=FuturesConfig(enabled=True),
            strategy=StrategyConfig(min_confidence=0.1),
        )
        paper = PaperExecutionEngine()
        engine = TradingEngine(config, execution=paper, market_data=market_data)
        await engine.refresh_market_data()

        # Both spot and futures rules preserved without overwriting
        self.assertIn((MarketType.SPOT, "BTCUSDT"), engine.symbol_rules)
        self.assertIn((MarketType.FUTURES, "BTCUSDT"), engine.symbol_rules)
        self.assertEqual(engine.symbol_rules[(MarketType.SPOT, "BTCUSDT")].min_notional, 10.0)
        self.assertEqual(engine.symbol_rules[(MarketType.FUTURES, "BTCUSDT")].min_notional, 5.0)

        await engine.run_once()
        self.assertEqual(len(paper.receipts), 1)
        self.assertIn(f"{MarketType.SPOT}:BTCUSDT:{PositionSide.LONG}", engine.account.open_positions)
        self.assertNotIn(f"{MarketType.FUTURES}:BTCUSDT:{PositionSide.LONG}", engine.account.open_positions)

    async def test_loop_catches_errors_and_resets_running_flag(self):
        config = TradingConfig(
            market=MarketConfig(candidate_refresh_seconds=999, strategy_interval_seconds=1),
        )
        engine = TradingEngine(config)

        async def exploding_run_once(*args, **kwargs):
            engine._stop_event.set()
            raise RuntimeError("boom_in_loop")

        engine.run_once = exploding_run_once  # type: ignore[method-assign]
        await engine.start()
        await asyncio.sleep(0.05)
        await engine.stop()
        self.assertFalse(engine.state.running)
        self.assertIn("RuntimeError: boom_in_loop", engine.state.last_engine_error or "")

    def test_live_execution_wiring(self):
        cfg = TradingConfig(app=AppConfig(paper_trading=False, allow_live_trading=True))
        engine = TradingEngine(cfg, credentials=BinanceCredentials("k", "s"))
        self.assertIsInstance(engine.execution, LiveExecutionEngine)
        self.assertTrue(engine.execution.enabled)
        self.assertEqual(engine.state.mode, "live")


class RiskPrecisionAndApiComplianceTests(unittest.TestCase):
    def _make_risk(self, hedge_mode=True, min_qty=0.001, step_size=0.001, min_notional=5.0, max_pos=100.0):
        config = TradingConfig(
            futures=FuturesConfig(hedge_mode=hedge_mode, default_leverage=5),
            risk=RiskConfig(max_notional_per_position=max_pos, max_total_notional=300.0),
            strategy=StrategyConfig(min_confidence=0.1),
            leverage={"BTCUSDT": 5},
        )
        rules = {
            (MarketType.FUTURES, "BTCUSDT"): SymbolRule(
                "BTCUSDT", min_qty, step_size, min_notional, 0.01, max_leverage=20, market_type=MarketType.FUTURES
            )
        }
        return RiskEngine(config, rules, {(MarketType.FUTURES, "BTCUSDT")})

    def test_decimal_rounding_before_validation(self):
        # Float 0.3 / 0.1 = 2.9999999999999996 would round to 0.2 with naive int(); Decimal gives exact 0.3!
        self.assertEqual(RiskEngine._round_quantity(0.3, 0.1), 0.3)
        self.assertEqual(RiskEngine._round_quantity(0.0019, 0.001), 0.001)

        risk = self._make_risk(min_qty=0.01, step_size=0.01, min_notional=5.0)
        sig = Signal("BTCUSDT", MarketType.FUTURES, OrderSide.BUY, PositionSide.LONG, 0.9, EntryType.MARKET, 0.009, 99, 101, 180)
        snap = MarketSnapshot("BTCUSDT", MarketType.FUTURES, 1000.0, 1e9, 3.0)
        decision = risk.validate(sig, AccountState(1000, 1000), snap)
        self.assertEqual(decision.reason, "quantity_rounded_to_zero")

    def test_rejects_non_finite_price_and_insufficient_balance_and_cumulative_notional(self):
        risk = self._make_risk(max_pos=100.0)
        sig = Signal("BTCUSDT", MarketType.FUTURES, OrderSide.BUY, PositionSide.LONG, 0.9, EntryType.MARKET, 0.001, 99000, 101000, 180)

        # NaN price rejected
        bad_snap = MarketSnapshot("BTCUSDT", MarketType.FUTURES, math.nan, 1e9, 3.0)
        self.assertEqual(risk.validate(sig, AccountState(1000, 1000), bad_snap).reason, "invalid_market_price")

        # Insufficient margin (notional 100 / leverage 5 = 20 > balance 10)
        snap = MarketSnapshot("BTCUSDT", MarketType.FUTURES, 100000.0, 1e9, 3.0)
        self.assertEqual(risk.validate(sig, AccountState(10, 10), snap).reason, "insufficient_available_balance")

        # Cumulative per-position notional (existing 60 + new 50 = 110 > 100)
        acct = AccountState(1000, 1000)
        existing = Position("BTCUSDT", MarketType.FUTURES, PositionSide.LONG, 0.0006, 100000.0, leverage=5)
        acct.open_positions[existing.key] = existing
        sig_half = Signal("BTCUSDT", MarketType.FUTURES, OrderSide.BUY, PositionSide.LONG, 0.9, EntryType.MARKET, 0.0005, 99000, 101000, 180)
        risk_fine = self._make_risk(min_qty=0.0001, step_size=0.0001, max_pos=100.0)
        self.assertEqual(risk_fine.validate(sig_half, acct, snap).reason, "max_position_notional_exceeded")

    def test_hedge_mode_omits_reduce_only_and_one_way_mode_uses_both(self):
        hedge_close = ApprovedOrder(
            symbol="BTCUSDT",
            market_type=MarketType.FUTURES,
            side=OrderSide.SELL,
            position_side=PositionSide.LONG,
            order_type=EntryType.MARKET,
            quantity=0.001,
            leverage=5,
            reduce_only=True,
        )
        params = build_order_params(hedge_close, hedge_mode=True)
        self.assertEqual(params["positionSide"], "LONG")
        self.assertNotIn("reduceOnly", params)

        one_way_close = ApprovedOrder(
            symbol="BTCUSDT",
            market_type=MarketType.FUTURES,
            side=OrderSide.SELL,
            position_side=PositionSide.BOTH,
            order_type=EntryType.MARKET,
            quantity=0.001,
            leverage=5,
            reduce_only=True,
        )
        one_way_params = build_order_params(one_way_close, hedge_mode=False)
        self.assertEqual(one_way_params["positionSide"], "BOTH")
        self.assertEqual(one_way_params["reduceOnly"], "true")

        # Strategy respects hedge_mode=False
        strategy = ShortMomentumStrategy(StrategyConfig(min_confidence=0.1), hedge_mode=False)
        snap = MarketSnapshot("BTCUSDT", MarketType.FUTURES, 50000.0, 1e9, -4.0, spread_bps=1.0, volatility=0.01)
        signals = strategy.on_market(StrategyContext(target_notional=100.0, hedge_mode=False), snap)
        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0].position_side, PositionSide.BOTH)
        self.assertAlmostEqual(signals[0].quantity, 0.002, places=6)

    def test_strategy_ignores_zero_change_and_does_not_give_full_score_for_missing_fields(self):
        strategy = ShortMomentumStrategy(StrategyConfig(min_confidence=0.1))
        zero_snap = MarketSnapshot("BTCUSDT", MarketType.FUTURES, 50000.0, 1e9, 0.0, spread_bps=1.0, volatility=0.01)
        self.assertEqual(strategy.on_market(StrategyContext(), zero_snap), [])

        missing_snap = MarketSnapshot("BTCUSDT", MarketType.FUTURES, 50000.0, 1e9, 4.0, book_imbalance=1.0)
        full_snap = MarketSnapshot(
            "BTCUSDT", MarketType.FUTURES, 50000.0, 1e9, 4.0,
            spread_bps=0.0, book_imbalance=1.0, funding_rate=0.0, volatility=0.0001,
        )
        self.assertLess(strategy._confidence(missing_snap), strategy._confidence(full_snap))
        self.assertLess(strategy._confidence(missing_snap), 0.68)

    def test_profiles_fail_closed_and_config_bounds_and_recv_window(self):
        with self.assertRaises(ValueError):
            resolve_profile(RiskConfig(risk_mode="typo_mode"))

        with tempfile.TemporaryDirectory() as tmp:
            bad_cfg = Path(tmp) / "bad.toml"
            bad_cfg.write_text("[risk]\nrisk_mode = 'invalid_mode'\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                load_config(bad_cfg)

            bad_port = Path(tmp) / "bad_port.toml"
            bad_port.write_text("[app]\nport = 70000\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                load_config(bad_port)

        signed = BinanceSigner(BinanceCredentials("k", "s")).signed_params({"symbol": "BTCUSDT"})
        self.assertEqual(signed["recvWindow"], 5000)
        self.assertEqual(browser_url("0.0.0.0", 8765), "http://127.0.0.1:8765")

    def test_backtester_exits_position_on_take_profit_and_tracks_pnl(self):
        config = TradingConfig(
            strategy=StrategyConfig(min_confidence=0.1),
            risk=RiskConfig(max_notional_per_position=100, max_total_notional=300),
            leverage={"BTCUSDT": 5},
        )
        backtester = Backtester(
            config,
            {(MarketType.FUTURES, "BTCUSDT"): SymbolRule("BTCUSDT", 0.001, 0.001, 5, 0.01, max_leverage=20)},
            {(MarketType.FUTURES, "BTCUSDT")},
        )
        t0 = datetime(2025, 1, 1, 0, 0, tzinfo=timezone.utc)
        metrics = backtester.run(
            [
                MarketSnapshot("BTCUSDT", MarketType.FUTURES, 100000.0, 1e9, 4.0, spread_bps=1.0, book_imbalance=0.8, volatility=0.01, timestamp=t0),
                # Above take-profit (101200) -> triggers exit!
                MarketSnapshot("BTCUSDT", MarketType.FUTURES, 102000.0, 1e9, 0.0, spread_bps=1.0, book_imbalance=0.0, volatility=0.01, timestamp=t0 + timedelta(seconds=30)),
            ],
            fee_rate=0.0,
        )
        self.assertEqual(metrics.accepted_orders, 1)
        self.assertEqual(metrics.exit_fills, 1)
        self.assertEqual(metrics.simulated_fills, 2)
        self.assertAlmostEqual(metrics.realized_pnl, 2.0, places=6)
        self.assertAlmostEqual(metrics.ending_equity, 1002.0, places=6)


class ControlPanelSecurityTests(unittest.TestCase):
    def setUp(self):
        self.config = TradingConfig(app=AppConfig(host="127.0.0.1", port=0))
        self.engine = TradingEngine(self.config)
        self.engine.set_symbol_rules(
            {(MarketType.FUTURES, "BTCUSDT"): SymbolRule("BTCUSDT", 0.001, 0.001, 5, 0.01, max_leverage=20)}
        )
        self.server, self.engine_loop, self.token = create_simple_server(
            self.engine, self.config, api_token="test-secret-token"
        )
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.engine_loop.stop()

    def _request(self, method: str, path: str, headers: dict[str, str] | None = None):
        req = Request(f"http://127.0.0.1:{self.port}{path}", method=method, headers=headers or {})
        try:
            with urlopen(req, timeout=5) as resp:
                return resp.status, resp.read().decode("utf-8")
        except HTTPError as err:
            return err.code, err.read().decode("utf-8")

    def test_index_injects_token_and_api_requires_bearer_and_csrf(self):
        status, html = self._request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn("test-secret-token", html)

        # Missing auth -> 401
        status, _ = self._request("GET", "/api/state")
        self.assertEqual(status, 401)
        status, _ = self._request("POST", "/api/start")
        self.assertEqual(status, 401)

        # Valid auth -> 200 on state
        auth = {"Authorization": f"Bearer {self.token}"}
        status, body = self._request("GET", "/api/state", headers=auth)
        self.assertEqual(status, 200)
        self.assertIn("account", json.loads(body))

        # Valid auth + evil Origin -> 403 CSRF rejected
        status, _ = self._request(
            "POST",
            "/api/leverage/BTCUSDT/10",
            headers={**auth, "Origin": "https://evil.example"},
        )
        self.assertEqual(status, 403)

        # Valid auth + matching Origin + invalid leverage -> 400
        good_headers = {**auth, "Origin": f"http://127.0.0.1:{self.port}"}
        status, body = self._request("POST", "/api/leverage/BTCUSDT/notanint", headers=good_headers)
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(body)["error"], "invalid_leverage")

        # Unknown symbol rejected
        status, body = self._request("POST", "/api/leverage/UNKNOWNUSDT/10", headers=good_headers)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["ok"], False)
        self.assertEqual(json.loads(body)["error"], "unknown_symbol")

        # Known symbol accepted
        status, body = self._request("POST", "/api/leverage/BTCUSDT/10", headers=good_headers)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["ok"], True)
        self.assertEqual(self.config.leverage["BTCUSDT"], 10)

    def test_auth_and_csrf_helpers(self):
        self.assertTrue(verify_bearer_token("Bearer abc", "abc"))
        self.assertFalse(verify_bearer_token("Bearer xyz", "abc"))
        self.assertFalse(verify_bearer_token("Basic abc", "abc"))
        self.assertTrue(verify_csrf_headers("http://127.0.0.1:8765", None, "127.0.0.1:8765"))
        self.assertFalse(verify_csrf_headers("http://evil.com", None, "127.0.0.1:8765"))
        self.assertFalse(verify_csrf_headers(None, "http://evil.com/page", "127.0.0.1:8765"))
        self.assertIn('"tok"', render_index_html("tok"))
        self.assertTrue(len(resolve_api_token()) >= 16)


if __name__ == "__main__":
    unittest.main()
