from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import csv

from .config import TradingConfig
from .models import AccountState, MarketSnapshot, MarketType, OrderStatus, SymbolRule
from .risk import RiskEngine
from .strategy import ShortMomentumStrategy, StrategyContext


@dataclass(frozen=True)
class BacktestMetrics:
    snapshots: int
    signals: int
    accepted_orders: int
    rejected_orders: int
    simulated_fills: int
    gross_notional: float = 0.0
    estimated_fees: float = 0.0

    @property
    def acceptance_rate(self) -> float:
        if self.signals == 0:
            return 0.0
        return self.accepted_orders / self.signals

    @property
    def rejection_rate(self) -> float:
        if self.signals == 0:
            return 0.0
        return self.rejected_orders / self.signals


class Backtester:
    def __init__(
        self,
        config: TradingConfig,
        symbol_rules: dict[str, SymbolRule],
        candidate_symbols: set[str],
    ):
        self.config = config
        self.strategy = ShortMomentumStrategy(config.strategy)
        self.risk = RiskEngine(config, symbol_rules, candidate_symbols)

    def run(self, snapshots: list[MarketSnapshot], fee_rate: float = 0.0004) -> BacktestMetrics:
        account = AccountState(equity=1000, available_balance=1000)
        signals_count = 0
        accepted = 0
        rejected = 0
        fills = 0
        gross_notional = 0.0

        for snapshot in snapshots:
            signals = self.strategy.on_market(StrategyContext(), snapshot)
            signals_count += len(signals)
            for signal in signals:
                decision = self.risk.validate(signal, account, snapshot)
                if hasattr(decision, "reason"):
                    rejected += 1
                    continue
                accepted += 1
                gross_notional += decision.quantity * snapshot.last_price
                if OrderStatus.FILLED == OrderStatus.FILLED:
                    fills += 1

        return BacktestMetrics(
            snapshots=len(snapshots),
            signals=signals_count,
            accepted_orders=accepted,
            rejected_orders=rejected,
            simulated_fills=fills,
            gross_notional=round(gross_notional, 8),
            estimated_fees=round(gross_notional * fee_rate, 8),
        )


def load_snapshots_csv(path: str | Path, market_type: MarketType = MarketType.FUTURES) -> list[MarketSnapshot]:
    snapshots: list[MarketSnapshot] = []
    with Path(path).open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            snapshots.append(
                MarketSnapshot(
                    symbol=str(row["symbol"]).upper(),
                    market_type=market_type,
                    last_price=float(row["last_price"]),
                    quote_volume=float(row.get("quote_volume", 0) or 0),
                    price_change_percent=float(row.get("price_change_percent", 0) or 0),
                    spread_bps=float(row.get("spread_bps", 0) or 0),
                    book_imbalance=float(row.get("book_imbalance", 0) or 0),
                    funding_rate=float(row.get("funding_rate", 0) or 0),
                    volatility=float(row.get("volatility", 0) or 0),
                )
            )
    return snapshots
