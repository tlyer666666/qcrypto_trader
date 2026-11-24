from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .binance import BinanceRestMarketData
from .candidate_pool import CandidatePool
from .config import BinanceCredentials, TradingConfig, load_binance_credentials
from .execution import ExecutionEngine, LiveExecutionEngine, PaperExecutionEngine
from .models import (
    AccountState,
    ApprovedOrder,
    Candidate,
    EntryType,
    MarketSnapshot,
    MarketType,
    OrderSide,
    OrderStatus,
    SymbolRule,
    Ticker,
    apply_filled_order,
    check_position_exit,
    utc_now,
)
from .profiles import effective_risk_limits
from .risk import RiskEngine, RuleMap
from .storage import Storage
from .strategy import ShortMomentumStrategy, StrategyContext


@dataclass
class EngineState:
    running: bool = False
    mode: str = "paper"
    candidates: list[Candidate] = field(default_factory=list)
    last_rejection: str | None = None
    last_receipt: str | None = None
    last_candidate_refresh: str | None = None
    last_market_error: str | None = None
    last_engine_error: str | None = None


class TradingEngine:
    def __init__(
        self,
        config: TradingConfig,
        execution: ExecutionEngine | None = None,
        market_data: BinanceRestMarketData | None = None,
        credentials: BinanceCredentials | None = None,
        storage: Storage | None = None,
    ):
        self.config = config
        self._credentials = credentials
        if config.app.paper_trading:
            mode = "paper"
        elif config.app.allow_live_trading:
            mode = "live"
        else:
            mode = "live_locked"

        if execution is not None:
            self.execution = execution
        elif config.app.paper_trading:
            self.execution = PaperExecutionEngine()
        else:
            creds = credentials if credentials is not None else load_binance_credentials()
            self.execution = LiveExecutionEngine(
                enabled=config.app.allow_live_trading,
                credentials=creds,
                hedge_mode=config.futures.hedge_mode,
            )

        self.storage = (
            storage
            if storage is not None
            else (Storage(config.app.db_path) if config.app.db_path else None)
        )
        self.market_data = market_data or BinanceRestMarketData()
        self.strategy = ShortMomentumStrategy(
            config.strategy, hedge_mode=config.futures.hedge_mode
        )
        self.candidate_pool = CandidatePool(config.market)
        self.state = EngineState(mode=mode)
        self.account = AccountState(equity=1000.0, available_balance=1000.0)
        self.symbol_rules: dict[tuple[MarketType, str], SymbolRule] = {}
        self._latest_prices: dict[tuple[MarketType, str], float] = {}
        self._state_lock = threading.RLock()
        self._op_lock: asyncio.Lock | None = None
        self._op_lock_loop: asyncio.AbstractEventLoop | None = None
        self._in_flight: set[tuple[MarketType, str]] = set()
        self._task: asyncio.Task | None = None
        self._stop_event: asyncio.Event | None = None
        self._last_refresh_loop = 0.0

    def _get_op_lock(self) -> asyncio.Lock:
        loop = asyncio.get_running_loop()
        if self._op_lock is None or self._op_lock_loop is not loop:
            self._op_lock = asyncio.Lock()
            self._op_lock_loop = loop
        return self._op_lock

    def set_symbol_rules(
        self,
        rules: RuleMap,
        market_type: MarketType | None = None,
    ) -> None:
        normalized: dict[tuple[MarketType, str], SymbolRule] = {}
        for key, rule in rules.items():
            if isinstance(key, tuple):
                mt, sym = key
                normalized[(mt, sym.upper())] = rule
            else:
                mt = market_type or rule.market_type
                normalized[(mt, str(key).upper())] = rule
        with self._state_lock:
            self.symbol_rules = normalized

    def update_candidates(
        self,
        tickers: list[Ticker],
        market_type: MarketType | None = None,
    ) -> list[Candidate]:
        built = self.candidate_pool.build(tickers, market_type=market_type)
        with self._state_lock:
            self.state.candidates = built
            self.state.last_candidate_refresh = datetime.now(timezone.utc).isoformat()
            return list(self.state.candidates)

    async def refresh_market_data(self) -> None:
        try:
            rules: dict[tuple[MarketType, str], SymbolRule] = {}
            latest_prices: dict[tuple[MarketType, str], float] = {}
            candidates: list[Candidate] = []
            if self.config.spot.enabled:
                spot_tickers = await self.market_data.spot_tickers()
                spot_rules = await self.market_data.spot_rules()
                for ticker in spot_tickers:
                    if ticker.last_price > 0:
                        latest_prices[(MarketType.SPOT, ticker.symbol.upper())] = ticker.last_price
                for key, rule in spot_rules.items():
                    if isinstance(key, tuple):
                        rules[(key[0], key[1].upper())] = rule
                    else:
                        rules[(MarketType.SPOT, str(key).upper())] = rule
                candidates.extend(
                    self.candidate_pool.build(spot_tickers, market_type=MarketType.SPOT)
                )
            if self.config.futures.enabled:
                futures_tickers = await self.market_data.futures_tickers()
                futures_rules = await self.market_data.futures_rules()
                for ticker in futures_tickers:
                    if ticker.last_price > 0:
                        latest_prices[(MarketType.FUTURES, ticker.symbol.upper())] = ticker.last_price
                for key, rule in futures_rules.items():
                    if isinstance(key, tuple):
                        rules[(key[0], key[1].upper())] = rule
                    else:
                        rules[(MarketType.FUTURES, str(key).upper())] = rule
                candidates.extend(
                    self.candidate_pool.build(futures_tickers, market_type=MarketType.FUTURES)
                )
            candidates.sort(key=lambda item: item.score, reverse=True)
            with self._state_lock:
                self.symbol_rules = rules
                self._latest_prices.update(latest_prices)
                self.state.candidates = candidates
                self.state.last_candidate_refresh = datetime.now(timezone.utc).isoformat()
                self.state.last_market_error = None
        except Exception as exc:
            with self._state_lock:
                self.state.last_market_error = f"{type(exc).__name__}: {exc}"

    def set_leverage(self, symbol: str, leverage: int) -> str | None:
        normalized = symbol.upper().strip()
        if not normalized:
            return "invalid_symbol"
        with self._state_lock:
            rule = self.symbol_rules.get((MarketType.FUTURES, normalized))
            if rule is None:
                return "unknown_symbol"
            if rule.status != "TRADING":
                return "inactive_symbol"
            if leverage < 1:
                return "leverage_below_one"
            if leverage > self.config.futures.max_configurable_leverage:
                return "leverage_above_configurable_limit"
            if leverage > rule.max_leverage:
                return "leverage_above_exchange_limit"
            self.config.leverage[normalized] = leverage
            return None

    async def run_once(
        self,
        market_type: MarketType | None = None,
        now: datetime | None = None,
    ) -> None:
        async with self._get_op_lock():
            enabled_markets = self._enabled_market_types()
            allowed_markets = {market_type} if market_type else set(enabled_markets)
            if not allowed_markets:
                return

            with self._state_lock:
                candidates_snapshot = list(self.state.candidates)
                rules_snapshot = dict(self.symbol_rules)

            limits = effective_risk_limits(self.config.risk)
            candidate_keys: set[tuple[MarketType, str]] = set()
            resolved_pairs: list[tuple[MarketType, Candidate]] = []
            for candidate in candidates_snapshot:
                if candidate.market_type is not None:
                    if candidate.market_type not in allowed_markets:
                        continue
                    active_market = candidate.market_type
                else:
                    active_market = market_type if market_type else enabled_markets[0]
                    if active_market not in allowed_markets:
                        continue
                candidate_keys.add((active_market, candidate.symbol.upper()))
                resolved_pairs.append((active_market, candidate))

            risk = RiskEngine(self.config, rules_snapshot, candidate_keys)
            ctx = StrategyContext(
                target_notional=limits.max_notional_per_position,
                hedge_mode=self.config.futures.hedge_mode,
            )
            snapshot_ts = now or utc_now()
            evaluated_exit_keys: set[tuple[MarketType, str]] = set()

            for active_market_type, candidate in resolved_pairs:
                snapshot = MarketSnapshot(
                    symbol=candidate.symbol,
                    market_type=active_market_type,
                    last_price=candidate.last_price,
                    quote_volume=candidate.quote_volume,
                    price_change_percent=candidate.price_change_percent,
                    spread_bps=candidate.spread_bps,
                    timestamp=snapshot_ts,
                )

                # 1. Evaluate exit triggers for existing open positions on this market/symbol.
                evaluated_exit_keys.add((active_market_type, candidate.symbol))
                await self._evaluate_exits(snapshot)

                # 2. Evaluate new entry signals.
                signals = self.strategy.on_market(ctx, snapshot)
                for signal in signals:
                    pos_key = f"{signal.market_type}:{signal.symbol}:{signal.position_side}"
                    flight_key = (signal.market_type, signal.symbol)
                    with self._state_lock:
                        if pos_key in self.account.open_positions:
                            self.state.last_rejection = f"{signal.symbol}:position_already_open"
                            continue
                        if flight_key in self._in_flight:
                            self.state.last_rejection = f"{signal.symbol}:order_in_flight"
                            continue
                        decision = risk.validate(signal, self.account, snapshot)
                        if hasattr(decision, "reason"):
                            self.state.last_rejection = f"{decision.symbol}:{decision.reason}"
                            continue
                        self._in_flight.add(flight_key)

                    try:
                        receipt = await self.execution.submit(decision)
                        if self.storage is not None:
                            self.storage.save_order_receipt(receipt)
                        with self._state_lock:
                            self.state.last_receipt = f"{receipt.symbol}:{receipt.status}"
                            if receipt.status == OrderStatus.FILLED:
                                fill_price = (
                                    receipt.filled_price
                                    if receipt.filled_price is not None
                                    else snapshot.last_price
                                )
                                apply_filled_order(
                                    self.account,
                                    decision,
                                    fill_price,
                                    timestamp=snapshot.timestamp,
                                )
                    finally:
                        with self._state_lock:
                            self._in_flight.discard(flight_key)

            # 3. Also evaluate exits for open positions that dropped out of the candidate pool.
            with self._state_lock:
                remaining_positions = [
                    pos
                    for pos in self.account.open_positions.values()
                    if pos.market_type in allowed_markets
                    and (pos.market_type, pos.symbol) not in evaluated_exit_keys
                ]
                prices_snapshot = dict(self._latest_prices)
            for position in remaining_positions:
                mark_price = prices_snapshot.get(
                    (position.market_type, position.symbol), position.entry_price
                )
                fallback_snapshot = MarketSnapshot(
                    symbol=position.symbol,
                    market_type=position.market_type,
                    last_price=mark_price,
                    quote_volume=0.0,
                    price_change_percent=0.0,
                    timestamp=snapshot_ts,
                )
                await self._evaluate_exits(fallback_snapshot)

    async def _evaluate_exits(self, snapshot: MarketSnapshot) -> None:
        with self._state_lock:
            matching_positions = [
                pos
                for pos in self.account.open_positions.values()
                if pos.market_type == snapshot.market_type and pos.symbol == snapshot.symbol
            ]

        for position in matching_positions:
            exit_reason = check_position_exit(position, snapshot)
            if exit_reason is None:
                continue
            flight_key = (position.market_type, position.symbol)
            with self._state_lock:
                if position.key not in self.account.open_positions:
                    continue
                if flight_key in self._in_flight:
                    continue
                self._in_flight.add(flight_key)

            exit_order = ApprovedOrder(
                symbol=position.symbol,
                market_type=position.market_type,
                side=OrderSide.BUY if position.is_short else OrderSide.SELL,
                position_side=position.position_side,
                order_type=EntryType.MARKET,
                quantity=position.quantity,
                leverage=position.leverage,
                reduce_only=True,
            )
            try:
                receipt = await self.execution.submit(exit_order)
                if self.storage is not None:
                    self.storage.save_order_receipt(receipt)
                with self._state_lock:
                    self.state.last_receipt = f"{receipt.symbol}:{receipt.status}:{exit_reason}"
                    if receipt.status == OrderStatus.FILLED:
                        fill_price = (
                            receipt.filled_price
                            if receipt.filled_price is not None
                            else snapshot.last_price
                        )
                        apply_filled_order(
                            self.account,
                            exit_order,
                            fill_price,
                            timestamp=snapshot.timestamp,
                        )
            finally:
                with self._state_lock:
                    self._in_flight.discard(flight_key)

    def snapshot_state(self, credentials_available: bool) -> dict[str, Any]:
        with self._state_lock:
            return {
                "status": {
                    "running": self.state.running,
                    "mode": self.state.mode,
                    "credentials_loaded": credentials_available,
                    "last_rejection": self.state.last_rejection,
                    "last_receipt": self.state.last_receipt,
                    "last_candidate_refresh": self.state.last_candidate_refresh,
                    "last_market_error": self.state.last_market_error,
                    "last_engine_error": self.state.last_engine_error,
                },
                "account": {
                    "equity": round(self.account.equity, 8),
                    "available_balance": round(self.account.available_balance, 8),
                    "daily_pnl": round(self.account.daily_pnl, 8),
                    "consecutive_losses": self.account.consecutive_losses,
                    "open_positions": len(self.account.open_positions),
                },
                "risk": {
                    "paper_trading": self.config.app.paper_trading,
                    "allow_live_trading": self.config.app.allow_live_trading,
                    "hedge_mode": self.config.futures.hedge_mode,
                    "risk_mode": self.config.risk.risk_mode,
                    "default_leverage": self.config.futures.default_leverage,
                    "kill_switch": self.config.risk.kill_switch,
                },
                "leverage": dict(sorted(self.config.leverage.items())),
                "candidates": [candidate.__dict__.copy() for candidate in self.state.candidates],
            }

    def _enabled_market_types(self) -> list[MarketType]:
        market_types: list[MarketType] = []
        if self.config.futures.enabled:
            market_types.append(MarketType.FUTURES)
        if self.config.spot.enabled:
            market_types.append(MarketType.SPOT)
        return market_types

    async def start(self) -> None:
        with self._state_lock:
            if self.state.running:
                return
            self.state.running = True
            self.state.last_engine_error = None
        self._stop_event = asyncio.Event()
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._stop_event is not None:
            self._stop_event.set()
        task = self._task
        self._task = None
        if task is not None:
            try:
                await task
            except asyncio.CancelledError:
                pass
        with self._state_lock:
            self.state.running = False

    async def _loop(self) -> None:
        stop_event = self._stop_event or asyncio.Event()
        try:
            while not stop_event.is_set():
                try:
                    now = asyncio.get_running_loop().time()
                    if now - self._last_refresh_loop >= self.config.market.candidate_refresh_seconds:
                        await self.refresh_market_data()
                        self._last_refresh_loop = now
                    await self.run_once()
                    with self._state_lock:
                        self.state.last_engine_error = None
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    with self._state_lock:
                        self.state.last_engine_error = f"{type(exc).__name__}: {exc}"

                try:
                    await asyncio.wait_for(
                        stop_event.wait(),
                        timeout=self.config.market.strategy_interval_seconds,
                    )
                except TimeoutError:
                    pass
        finally:
            with self._state_lock:
                self.state.running = False
