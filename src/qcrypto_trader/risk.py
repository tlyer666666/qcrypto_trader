from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN
from typing import Mapping

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


RuleMap = Mapping[tuple[MarketType, str], SymbolRule] | Mapping[str, SymbolRule]
CandidateSet = set[tuple[MarketType, str]] | set[str]


@dataclass
class RiskEngine:
    config: TradingConfig
    symbol_rules: RuleMap
    candidate_symbols: CandidateSet

    def _get_rule(self, market_type: MarketType, symbol: str) -> SymbolRule | None:
        normalized = symbol.upper()
        rule = self.symbol_rules.get((market_type, normalized))  # type: ignore[arg-type]
        if rule is not None:
            return rule
        legacy_rule = self.symbol_rules.get(normalized)  # type: ignore[arg-type]
        if legacy_rule is not None:
            return legacy_rule
        return self.symbol_rules.get(symbol)  # type: ignore[arg-type]

    def _is_candidate(self, market_type: MarketType, symbol: str) -> bool:
        normalized = symbol.upper()
        return (
            (market_type, normalized) in self.candidate_symbols
            or normalized in self.candidate_symbols
            or symbol in self.candidate_symbols
        )

    def validate(
        self, signal: Signal, account: AccountState, market: MarketSnapshot
    ) -> ApprovedOrder | RejectedOrder:
        limits = effective_risk_limits(self.config.risk)
        if self.config.risk.kill_switch:
            return RejectedOrder(signal.symbol, "kill_switch_enabled")
        if signal.confidence < self.config.strategy.min_confidence + limits.min_confidence_offset:
            return RejectedOrder(signal.symbol, "confidence_below_risk_mode_threshold")
        if not self._is_candidate(signal.market_type, signal.symbol):
            return RejectedOrder(signal.symbol, "symbol_not_in_candidate_pool")
        if account.daily_pnl <= -abs(limits.max_daily_loss):
            return RejectedOrder(signal.symbol, "max_daily_loss_reached")
        if account.consecutive_losses >= limits.max_consecutive_losses:
            return RejectedOrder(signal.symbol, "max_consecutive_losses_reached")

        if not math.isfinite(market.last_price) or market.last_price <= 0:
            return RejectedOrder(signal.symbol, "invalid_market_price")
        if not math.isfinite(signal.quantity) or signal.quantity <= 0:
            return RejectedOrder(signal.symbol, "invalid_quantity")

        current_positions = len(account.open_positions)
        position_key = f"{signal.market_type}:{signal.symbol}:{signal.position_side}"
        if (
            position_key not in account.open_positions
            and current_positions >= self.config.market.max_open_positions
        ):
            return RejectedOrder(signal.symbol, "max_open_positions_reached")

        rule = self._get_rule(signal.market_type, signal.symbol)
        if not rule or rule.status != "TRADING":
            return RejectedOrder(signal.symbol, "missing_or_inactive_symbol_rule")

        rounded_qty = self._round_quantity(signal.quantity, rule.step_size)
        if rounded_qty <= 0:
            return RejectedOrder(signal.symbol, "quantity_rounded_to_zero")
        if rule.min_qty > 0 and Decimal(str(rounded_qty)) < Decimal(str(rule.min_qty)):
            return RejectedOrder(signal.symbol, "below_min_qty")

        notional_dec = Decimal(str(rounded_qty)) * Decimal(str(market.last_price))
        notional = float(notional_dec)
        if notional_dec < Decimal(str(rule.min_notional)):
            return RejectedOrder(signal.symbol, "below_min_notional")

        existing_position = account.open_positions.get(position_key)
        existing_pos_notional = existing_position.notional if existing_position else 0.0
        if existing_pos_notional + notional > limits.max_notional_per_position:
            return RejectedOrder(signal.symbol, "max_position_notional_exceeded")

        total_notional = sum(position.notional for position in account.open_positions.values())
        if total_notional + notional > limits.max_total_notional:
            return RejectedOrder(signal.symbol, "max_total_notional_exceeded")

        if signal.market_type == MarketType.FUTURES:
            leverage = self.config.leverage_for(signal.symbol)
            leverage_check = self.validate_leverage(signal.symbol, leverage)
            if leverage_check:
                return RejectedOrder(signal.symbol, leverage_check)
            if self.config.futures.hedge_mode and signal.position_side == PositionSide.BOTH:
                return RejectedOrder(signal.symbol, "hedge_mode_requires_long_or_short")
            if not self.config.futures.hedge_mode and signal.position_side != PositionSide.BOTH:
                return RejectedOrder(signal.symbol, "one_way_mode_requires_both_position_side")
        else:
            leverage = 1

        required_margin = notional / leverage
        if not math.isfinite(account.available_balance) or account.available_balance < required_margin:
            return RejectedOrder(signal.symbol, "insufficient_available_balance")

        return ApprovedOrder(
            symbol=signal.symbol,
            market_type=signal.market_type,
            side=signal.side,
            position_side=signal.position_side,
            order_type=signal.entry_type,
            quantity=rounded_qty,
            leverage=leverage,
            stop_loss=signal.stop_loss,
            take_profit=signal.take_profit,
            max_hold_seconds=signal.max_hold_seconds,
        )

    def validate_leverage(self, symbol: str, leverage: int) -> str | None:
        if leverage < 1:
            return "leverage_below_one"
        if leverage > self.config.futures.max_configurable_leverage:
            return "leverage_above_configurable_limit"
        rule = self._get_rule(MarketType.FUTURES, symbol)
        if not rule or rule.status != "TRADING":
            return "missing_or_inactive_symbol_rule"
        if leverage > rule.max_leverage:
            return "leverage_above_exchange_limit"
        return None

    @staticmethod
    def _round_quantity(quantity: float, step_size: float) -> float:
        if not math.isfinite(quantity) or quantity <= 0:
            return 0.0
        qty_dec = Decimal(str(quantity))
        if not math.isfinite(step_size) or step_size <= 0:
            return float(qty_dec)
        step_dec = Decimal(str(step_size))
        steps = (qty_dec / step_dec).to_integral_value(rounding=ROUND_DOWN)
        return float(steps * step_dec)
