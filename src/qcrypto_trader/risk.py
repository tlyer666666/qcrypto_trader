from __future__ import annotations

from dataclasses import dataclass

from .config import TradingConfig
from .models import (
    AccountState,
    ApprovedOrder,
    MarketSnapshot,
    MarketType,
    PositionSide,
    RejectedOrder,
    Signal,
    SymbolRule,
)
from .profiles import effective_risk_limits


@dataclass
class RiskEngine:
    config: TradingConfig
    symbol_rules: dict[str, SymbolRule]
    candidate_symbols: set[str]

    def validate(
        self, signal: Signal, account: AccountState, market: MarketSnapshot
    ) -> ApprovedOrder | RejectedOrder:
        limits = effective_risk_limits(self.config.risk)
        if self.config.risk.kill_switch:
            return RejectedOrder(signal.symbol, "kill_switch_enabled")
        if signal.confidence < self.config.strategy.min_confidence + limits.min_confidence_offset:
            return RejectedOrder(signal.symbol, "confidence_below_risk_mode_threshold")
        if signal.symbol not in self.candidate_symbols:
            return RejectedOrder(signal.symbol, "symbol_not_in_candidate_pool")
        if account.daily_pnl <= -abs(limits.max_daily_loss):
            return RejectedOrder(signal.symbol, "max_daily_loss_reached")
        if account.consecutive_losses >= limits.max_consecutive_losses:
            return RejectedOrder(signal.symbol, "max_consecutive_losses_reached")

        current_positions = len(account.open_positions)
        position_key = f"{signal.market_type}:{signal.symbol}:{signal.position_side}"
        if (
            position_key not in account.open_positions
            and current_positions >= self.config.market.max_open_positions
        ):
            return RejectedOrder(signal.symbol, "max_open_positions_reached")

        rule = self.symbol_rules.get(signal.symbol)
        if not rule or rule.status != "TRADING":
            return RejectedOrder(signal.symbol, "missing_or_inactive_symbol_rule")

        notional = signal.quantity * market.last_price
        if notional < rule.min_notional:
            return RejectedOrder(signal.symbol, "below_min_notional")
        if notional > limits.max_notional_per_position:
            return RejectedOrder(signal.symbol, "max_position_notional_exceeded")

        total_notional = sum(position.notional for position in account.open_positions.values())
        if total_notional + notional > limits.max_total_notional:
            return RejectedOrder(signal.symbol, "max_total_notional_exceeded")

        leverage = self.config.leverage_for(signal.symbol)
        if signal.market_type == MarketType.FUTURES:
            leverage_check = self.validate_leverage(signal.symbol, leverage)
            if leverage_check:
                return RejectedOrder(signal.symbol, leverage_check)
            if self.config.futures.hedge_mode and signal.position_side == PositionSide.BOTH:
                return RejectedOrder(signal.symbol, "hedge_mode_requires_long_or_short")
        else:
            leverage = 1

        return ApprovedOrder(
            symbol=signal.symbol,
            market_type=signal.market_type,
            side=signal.side,
            position_side=signal.position_side,
            order_type=signal.entry_type,
            quantity=self._round_quantity(signal.quantity, rule.step_size),
            leverage=leverage,
        )

    def validate_leverage(self, symbol: str, leverage: int) -> str | None:
        if leverage < 1:
            return "leverage_below_one"
        if leverage > self.config.futures.max_configurable_leverage:
            return "leverage_above_configurable_limit"
        rule = self.symbol_rules.get(symbol)
        if rule and leverage > rule.max_leverage:
            return "leverage_above_exchange_limit"
        return None

    @staticmethod
    def _round_quantity(quantity: float, step_size: float) -> float:
        if step_size <= 0:
            return quantity
        steps = int(quantity / step_size)
        return round(steps * step_size, 12)
