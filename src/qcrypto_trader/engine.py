from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .binance import BinanceRestMarketData
from .candidate_pool import CandidatePool
from .config import TradingConfig
from .execution import ExecutionEngine, PaperExecutionEngine
from .models import AccountState, Candidate, MarketSnapshot, MarketType, SymbolRule, Ticker
from .risk import RiskEngine
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


class TradingEngine:
    def __init__(
        self,
        config: TradingConfig,
        execution: ExecutionEngine | None = None,
        market_data: BinanceRestMarketData | None = None,
    ):
        self.config = config
        self.execution = execution or PaperExecutionEngine()
        self.market_data = market_data or BinanceRestMarketData()
        self.strategy = ShortMomentumStrategy(config.strategy)
        self.candidate_pool = CandidatePool(config.market)
        self.state = EngineState(mode="paper" if config.app.paper_trading else "live_locked")
        self.account = AccountState(equity=1000, available_balance=1000)
        self.symbol_rules: dict[str, SymbolRule] = {}
        self._task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()
        self._last_refresh_loop = 0.0

    def set_symbol_rules(self, rules: dict[str, SymbolRule]) -> None:
        self.symbol_rules = rules

    def update_candidates(self, tickers: list[Ticker]) -> list[Candidate]:
        self.state.candidates = self.candidate_pool.build(tickers)
        self.state.last_candidate_refresh = datetime.now(timezone.utc).isoformat()
        return self.state.candidates

    async def refresh_market_data(self) -> None:
        try:
            tickers: list[Ticker] = []
            rules: dict[str, SymbolRule] = {}
            if self.config.spot.enabled:
                tickers.extend(await self.market_data.spot_tickers())
                rules.update(await self.market_data.spot_rules())
            if self.config.futures.enabled:
                tickers.extend(await self.market_data.futures_tickers())
                rules.update(await self.market_data.futures_rules())
            self.set_symbol_rules(rules)
            self.update_candidates(tickers)
            self.state.last_market_error = None
        except Exception as exc:
            self.state.last_market_error = f"{type(exc).__name__}: {exc}"

    def set_leverage(self, symbol: str, leverage: int) -> str | None:
        normalized = symbol.upper()
        rule = self.symbol_rules.get(normalized)
        if leverage < 1:
            return "leverage_below_one"
        if leverage > self.config.futures.max_configurable_leverage:
            return "leverage_above_configurable_limit"
        if rule and leverage > rule.max_leverage:
            return "leverage_above_exchange_limit"
        self.config.leverage[normalized] = leverage
        return None

    async def run_once(self, market_type: MarketType | None = None) -> None:
        market_types = [market_type] if market_type else self._enabled_market_types()
        candidate_symbols = {candidate.symbol for candidate in self.state.candidates}
        risk = RiskEngine(self.config, self.symbol_rules, candidate_symbols)
        for active_market_type in market_types:
            for candidate in self.state.candidates:
                snapshot = MarketSnapshot(
                    symbol=candidate.symbol,
                    market_type=active_market_type,
                    last_price=candidate.last_price,
                    quote_volume=candidate.quote_volume,
                    price_change_percent=candidate.price_change_percent,
                    spread_bps=candidate.spread_bps,
                )
                signals = self.strategy.on_market(StrategyContext(), snapshot)
                for signal in signals:
                    decision = risk.validate(signal, self.account, snapshot)
                    if hasattr(decision, "reason"):
                        self.state.last_rejection = f"{decision.symbol}:{decision.reason}"
                        continue
                    receipt = await self.execution.submit(decision)
                    self.state.last_receipt = f"{receipt.symbol}:{receipt.status}"

    def _enabled_market_types(self) -> list[MarketType]:
        market_types: list[MarketType] = []
        if self.config.futures.enabled:
            market_types.append(MarketType.FUTURES)
        if self.config.spot.enabled:
            market_types.append(MarketType.SPOT)
        return market_types

    async def start(self) -> None:
        if self.state.running:
            return
        self.state.running = True
        self._stop_event.clear()
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        self.state.running = False
        self._stop_event.set()
        if self._task:
            await self._task

    async def _loop(self) -> None:
        while not self._stop_event.is_set():
            now = asyncio.get_running_loop().time()
            if now - self._last_refresh_loop >= self.config.market.candidate_refresh_seconds:
                await self.refresh_market_data()
                self._last_refresh_loop = now
            await self.run_once()
            await asyncio.sleep(self.config.market.strategy_interval_seconds)
