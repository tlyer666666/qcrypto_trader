from __future__ import annotations

import math
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
    min_quantity: float = 0.0
    default_quantity: float | None = None
    target_notional: float = 100.0
    hedge_mode: bool = True


class ShortMomentumStrategy:
    def __init__(self, config: StrategyConfig, hedge_mode: bool = True):
        self.config = config
        self.hedge_mode = hedge_mode

    def on_market(
        self, ctx: StrategyContext, snapshot: MarketSnapshot
    ) -> list[Signal]:
        if not math.isfinite(snapshot.last_price) or snapshot.last_price <= 0:
            return []
        if not math.isfinite(snapshot.price_change_percent) or snapshot.price_change_percent == 0:
            return []

        confidence = self._confidence(snapshot)
        if confidence < self.config.min_confidence:
            return []

        use_hedge_mode = self.hedge_mode and ctx.hedge_mode

        if snapshot.price_change_percent > 0:
            side = OrderSide.BUY
            if snapshot.market_type == MarketType.FUTURES:
                position_side = PositionSide.LONG if use_hedge_mode else PositionSide.BOTH
            else:
                position_side = PositionSide.LONG
            stop_loss = snapshot.last_price * 0.992
            take_profit = snapshot.last_price * 1.012
        else:
            side = OrderSide.SELL
            if snapshot.market_type == MarketType.SPOT:
                return []
            position_side = PositionSide.SHORT if use_hedge_mode else PositionSide.BOTH
            stop_loss = snapshot.last_price * 1.008
            take_profit = snapshot.last_price * 0.988

        if ctx.target_notional > 0:
            quantity = ctx.target_notional / snapshot.last_price
        elif ctx.default_quantity is not None and ctx.default_quantity > 0:
            quantity = ctx.default_quantity
        else:
            quantity = 0.0

        if ctx.min_quantity > 0:
            quantity = max(quantity, ctx.min_quantity)
        if not math.isfinite(quantity) or quantity <= 0:
            return []

        return [
            Signal(
                symbol=snapshot.symbol,
                market_type=snapshot.market_type,
                side=side,
                position_side=position_side,
                confidence=confidence,
                entry_type=EntryType.MARKET,
                quantity=quantity,
                stop_loss=round(stop_loss, 8),
                take_profit=round(take_profit, 8),
                max_hold_seconds=180,
                reason="short_momentum",
            )
        ]

    def _confidence(self, snapshot: MarketSnapshot) -> float:
        momentum = min(abs(snapshot.price_change_percent) / 4, 1.0)
        volume = min(max(snapshot.quote_volume, 0.0) / 1_000_000_000, 1.0)
        book = min(abs(snapshot.book_imbalance), 1.0)

        # Missing market microstructure fields receive neutral (0.5) rather than full 1.0 scores.
        if snapshot.funding_rate is None or not math.isfinite(snapshot.funding_rate):
            funding = 0.5
        else:
            funding = 1.0 - min(abs(snapshot.funding_rate) / 0.002, 1.0)

        if (
            snapshot.volatility is None
            or not math.isfinite(snapshot.volatility)
            or snapshot.volatility <= 0
        ):
            volatility = 0.5
        else:
            volatility = 1.0 - min(snapshot.volatility / 0.04, 1.0)

        if (
            snapshot.spread_bps is None
            or not math.isfinite(snapshot.spread_bps)
            or snapshot.spread_bps < 0
        ):
            spread = 0.5
        else:
            spread = 1.0 - min(snapshot.spread_bps / 20, 1.0)

        score = (
            momentum * self.config.momentum_weight
            + volume * self.config.volume_weight
            + book * self.config.book_imbalance_weight
            + funding * self.config.funding_weight
            + volatility * self.config.volatility_weight
        )
        return round(max(0.0, min(score * spread, 1.0)), 6)
