from __future__ import annotations

from dataclasses import dataclass

from .config import StrategyConfig
from .models import (
    EntryType,
    MarketSnapshot,
    MarketType,
    OrderSide,
    PositionSide,
    Signal,
)


@dataclass(frozen=True)
class StrategyContext:
    min_quantity: float = 0.001
    default_quantity: float = 0.001


class ShortMomentumStrategy:
    def __init__(self, config: StrategyConfig):
        self.config = config

    def on_market(
        self, ctx: StrategyContext, snapshot: MarketSnapshot
    ) -> list[Signal]:
        confidence = self._confidence(snapshot)
        if confidence < self.config.min_confidence:
            return []

        if snapshot.price_change_percent >= 0:
            side = OrderSide.BUY
            position_side = PositionSide.LONG
            stop_loss = snapshot.last_price * 0.992
            take_profit = snapshot.last_price * 1.012
        else:
            side = OrderSide.SELL
            position_side = (
                PositionSide.SHORT
                if snapshot.market_type == MarketType.FUTURES
                else PositionSide.LONG
            )
            stop_loss = snapshot.last_price * 1.008
            take_profit = snapshot.last_price * 0.988

        if snapshot.market_type == MarketType.SPOT and side == OrderSide.SELL:
            return []

        return [
            Signal(
                symbol=snapshot.symbol,
                market_type=snapshot.market_type,
                side=side,
                position_side=position_side,
                confidence=confidence,
                entry_type=EntryType.MARKET,
                quantity=max(ctx.default_quantity, ctx.min_quantity),
                stop_loss=round(stop_loss, 8),
                take_profit=round(take_profit, 8),
                max_hold_seconds=180,
                reason="short_momentum",
            )
        ]

    def _confidence(self, snapshot: MarketSnapshot) -> float:
        momentum = min(abs(snapshot.price_change_percent) / 4, 1.0)
        volume = min(snapshot.quote_volume / 1_000_000_000, 1.0)
        book = min(abs(snapshot.book_imbalance), 1.0)
        funding = 1.0 - min(abs(snapshot.funding_rate) / 0.002, 1.0)
        volatility = 1.0 - min(snapshot.volatility / 0.04, 1.0)
        spread = 1.0 - min(snapshot.spread_bps / 20, 1.0)

        score = (
            momentum * self.config.momentum_weight
            + volume * self.config.volume_weight
            + book * self.config.book_imbalance_weight
            + funding * self.config.funding_weight
            + volatility * self.config.volatility_weight
        )
        return round(max(0.0, min(score * spread, 1.0)), 6)

