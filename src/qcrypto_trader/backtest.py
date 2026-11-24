from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .config import TradingConfig
from .models import (
    AccountState,
    ApprovedOrder,
    EntryType,
    MarketSnapshot,
    MarketType,
    OrderSide,
    SymbolRule,
    apply_filled_order,
    check_position_exit,
)
from .profiles import effective_risk_limits
from .risk import CandidateSet, RiskEngine, RuleMap
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
    exit_fills: int = 0
    realized_pnl: float = 0.0
    ending_equity: float = 1000.0

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
        symbol_rules: RuleMap,
        candidate_symbols: CandidateSet,
    ):
        self.config = config
        self.strategy = ShortMomentumStrategy(
            config.strategy, hedge_mode=config.futures.hedge_mode
        )
        self.risk = RiskEngine(config, symbol_rules, candidate_symbols)

    def run(
        self,
        snapshots: list[MarketSnapshot],
        fee_rate: float = 0.0004,
        starting_equity: float = 1000.0,
    ) -> BacktestMetrics:
        account = AccountState(equity=starting_equity, available_balance=starting_equity)
        limits = effective_risk_limits(self.config.risk)
        signals_count = 0
        accepted = 0
        rejected = 0
        fills = 0
        exit_fills = 0
        gross_notional = 0.0
        estimated_fees = 0.0
        realized_pnl = 0.0

        for snapshot in snapshots:
            # 1. Evaluate stop-loss / take-profit / max-hold-seconds exits first.
            for position in list(account.open_positions.values()):
                exit_reason = check_position_exit(position, snapshot)
                if exit_reason is None:
                    continue
                exit_side = OrderSide.BUY if position.is_short else OrderSide.SELL
                exit_order = ApprovedOrder(
                    symbol=position.symbol,
                    market_type=position.market_type,
                    side=exit_side,
                    position_side=position.position_side,
                    order_type=EntryType.MARKET,
                    quantity=position.quantity,
                    leverage=position.leverage,
                    reduce_only=True,
                )
                pnl = apply_filled_order(
                    account,
                    exit_order,
                    snapshot.last_price,
                    timestamp=snapshot.timestamp,
                    fee_rate=fee_rate,
                )
                notional = exit_order.quantity * snapshot.last_price
                gross_notional += notional
                estimated_fees += notional * fee_rate
                realized_pnl += pnl
                fills += 1
                exit_fills += 1

            # 2. Evaluate entry signals.
            ctx = StrategyContext(
                target_notional=limits.max_notional_per_position,
                hedge_mode=self.config.futures.hedge_mode,
            )
            signals = self.strategy.on_market(ctx, snapshot)
            signals_count += len(signals)
            for signal in signals:
                position_key = f"{signal.market_type}:{signal.symbol}:{signal.position_side}"
                if position_key in account.open_positions:
                    rejected += 1
                    continue
                decision = self.risk.validate(signal, account, snapshot)
                if hasattr(decision, "reason"):
                    rejected += 1
                    continue
                accepted += 1
                entry_pnl = apply_filled_order(
                    account,
                    decision,
                    snapshot.last_price,
                    timestamp=snapshot.timestamp,
                    fee_rate=fee_rate,
                )
                notional = decision.quantity * snapshot.last_price
                gross_notional += notional
                estimated_fees += notional * fee_rate
                realized_pnl += entry_pnl
                fills += 1

        return BacktestMetrics(
            snapshots=len(snapshots),
            signals=signals_count,
            accepted_orders=accepted,
            rejected_orders=rejected,
            simulated_fills=fills,
            gross_notional=round(gross_notional, 8),
            estimated_fees=round(estimated_fees, 8),
            exit_fills=exit_fills,
            realized_pnl=round(realized_pnl, 8),
            ending_equity=round(account.equity, 8),
        )


def _optional_float(row: dict[str, str], key: str) -> float | None:
    raw = row.get(key)
    if raw is None or str(raw).strip() == "":
        return None
    return float(raw)


def load_snapshots_csv(
    path: str | Path, market_type: MarketType = MarketType.FUTURES
) -> list[MarketSnapshot]:
    snapshots: list[MarketSnapshot] = []
    with Path(path).open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            raw_ts = row.get("timestamp")
            kwargs: dict = {}
            if raw_ts and str(raw_ts).strip():
                kwargs["timestamp"] = datetime.fromisoformat(str(raw_ts).strip())
            snapshots.append(
                MarketSnapshot(
                    symbol=str(row["symbol"]).upper(),
                    market_type=market_type,
                    last_price=float(row["last_price"]),
                    quote_volume=float(row.get("quote_volume", 0) or 0),
                    price_change_percent=float(row.get("price_change_percent", 0) or 0),
                    spread_bps=_optional_float(row, "spread_bps"),
                    book_imbalance=float(row.get("book_imbalance", 0) or 0),
                    funding_rate=_optional_float(row, "funding_rate"),
                    volatility=_optional_float(row, "volatility"),
                    **kwargs,
                )
            )
    return snapshots
