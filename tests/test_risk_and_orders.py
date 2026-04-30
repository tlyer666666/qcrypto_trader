import unittest

from qcrypto_trader.config import FuturesConfig, MarketConfig, RiskConfig, TradingConfig
from qcrypto_trader.binance import (
    BinanceSigner,
    build_futures_leverage_params,
    build_futures_position_mode_params,
    build_order_params,
    order_endpoint,
)
from qcrypto_trader.config import BinanceCredentials
from qcrypto_trader.models import (
    AccountState,
    EntryType,
    MarketSnapshot,
    MarketType,
    OrderSide,
    PositionSide,
    Signal,
    SymbolRule,
)
from qcrypto_trader.risk import RiskEngine


def _signal(symbol="BTCUSDT", quantity=0.001, position_side=PositionSide.LONG):
    return Signal(
        symbol=symbol,
        market_type=MarketType.FUTURES,
        side=OrderSide.BUY,
        position_side=position_side,
        confidence=0.8,
        entry_type=EntryType.MARKET,
        quantity=quantity,
        stop_loss=99000,
        take_profit=101000,
        max_hold_seconds=180,
    )


def _market(symbol="BTCUSDT", price=100000):
    return MarketSnapshot(
        symbol=symbol,
        market_type=MarketType.FUTURES,
        last_price=price,
        quote_volume=1_000_000_000,
        price_change_percent=3,
    )


def _risk(config=None, max_leverage=125):
    return RiskEngine(
        config
        or TradingConfig(
            market=MarketConfig(max_open_positions=3),
            futures=FuturesConfig(default_leverage=10, max_configurable_leverage=125),
            risk=RiskConfig(max_notional_per_position=200, max_total_notional=500),
            leverage={"BTCUSDT": 10},
        ),
        {"BTCUSDT": SymbolRule("BTCUSDT", 0.001, 0.001, 5, 0.01, max_leverage=max_leverage)},
        {"BTCUSDT"},
    )


class RiskAndOrderTests(unittest.TestCase):
    def test_futures_hedge_order_contains_position_side(self):
        decision = _risk().validate(
            _signal(position_side=PositionSide.SHORT), AccountState(1000, 1000), _market()
        )

        params = decision.to_binance_params()

        self.assertEqual(params["positionSide"], "SHORT")
        self.assertEqual(params["side"], "BUY")

    def test_risk_rejects_leverage_above_exchange_limit(self):
        config = TradingConfig(
            futures=FuturesConfig(default_leverage=20, max_configurable_leverage=125),
            risk=RiskConfig(max_notional_per_position=200, max_total_notional=500),
            leverage={"BTCUSDT": 20},
        )

        decision = _risk(config=config, max_leverage=10).validate(
            _signal(), AccountState(1000, 1000), _market()
        )

        self.assertEqual(decision.reason, "leverage_above_exchange_limit")

    def test_risk_rejects_symbol_outside_candidate_pool(self):
        decision = RiskEngine(
            TradingConfig(),
            {"BTCUSDT": SymbolRule("BTCUSDT", 0.001, 0.001, 5, 0.01, max_leverage=125)},
            set(),
        ).validate(_signal(), AccountState(1000, 1000), _market())

        self.assertEqual(decision.reason, "symbol_not_in_candidate_pool")

    def test_risk_rejects_kill_switch(self):
        config = TradingConfig(risk=RiskConfig(kill_switch=True))

        decision = _risk(config=config).validate(_signal(), AccountState(1000, 1000), _market())

        self.assertEqual(decision.reason, "kill_switch_enabled")

    def test_build_futures_leverage_params(self):
        self.assertEqual(
            build_futures_leverage_params("btcusdt", 25),
            {"symbol": "BTCUSDT", "leverage": 25},
        )

    def test_build_futures_position_mode_params(self):
        self.assertEqual(build_futures_position_mode_params(True), {"dualSidePosition": "true"})
        self.assertEqual(build_futures_position_mode_params(False), {"dualSidePosition": "false"})

    def test_build_order_params_rejects_both_side_for_futures(self):
        decision = _risk().validate(
            _signal(position_side=PositionSide.SHORT), AccountState(1000, 1000), _market()
        )

        params = build_order_params(decision)

        self.assertEqual(params["positionSide"], "SHORT")
        self.assertEqual(order_endpoint(decision), "/fapi/v1/order")

    def test_signer_adds_timestamp_and_signature(self):
        signed = BinanceSigner(BinanceCredentials("key", "secret")).signed_params(
            {"symbol": "BTCUSDT", "leverage": 10}
        )

        self.assertIn("timestamp", signed)
        self.assertIn("signature", signed)
        self.assertNotEqual(signed["signature"], "secret")


if __name__ == "__main__":
    unittest.main()
