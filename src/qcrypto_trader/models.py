from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any
from uuid import uuid4


class MarketType(StrEnum):
    SPOT = "spot"
    FUTURES = "futures"


class OrderSide(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


class PositionSide(StrEnum):
    LONG = "LONG"
    SHORT = "SHORT"
    BOTH = "BOTH"


class EntryType(StrEnum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"


class OrderStatus(StrEnum):
    NEW = "NEW"
    FILLED = "FILLED"
    REJECTED = "REJECTED"
    CANCELED = "CANCELED"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class Ticker:
    symbol: str
    last_price: float
    quote_volume: float
    price_change_percent: float
    high_price: float = 0.0
    low_price: float = 0.0
    bid_price: float | None = None
    ask_price: float | None = None
    status: str = "TRADING"
    market_type: MarketType | None = None

    @property
    def spread_bps(self) -> float | None:
        if (
            self.bid_price is None
            or self.ask_price is None
            or self.bid_price <= 0
            or self.ask_price <= 0
            or self.last_price <= 0
        ):
            return None
        return ((self.ask_price - self.bid_price) / self.last_price) * 10000

    @property
    def intraday_range_percent(self) -> float:
        if self.low_price <= 0:
            return 0.0
        return ((self.high_price - self.low_price) / self.low_price) * 100


@dataclass(frozen=True)
class SymbolRule:
    symbol: str
    min_qty: float
    step_size: float
    min_notional: float
    tick_size: float
    max_leverage: int = 1
    status: str = "TRADING"
    market_type: MarketType = MarketType.FUTURES


@dataclass
class Candidate:
    symbol: str
    score: float
    last_price: float
    quote_volume: float
    price_change_percent: float
    spread_bps: float | None = None
    reasons: list[str] = field(default_factory=list)
    market_type: MarketType | None = None


@dataclass(frozen=True)
class MarketSnapshot:
    symbol: str
    market_type: MarketType
    last_price: float
    quote_volume: float
    price_change_percent: float
    spread_bps: float | None = None
    book_imbalance: float = 0.0
    funding_rate: float | None = None
    volatility: float | None = None
    timestamp: datetime = field(default_factory=utc_now)


@dataclass(frozen=True)
class Signal:
    symbol: str
    market_type: MarketType
    side: OrderSide
    position_side: PositionSide
    confidence: float
    entry_type: EntryType
    quantity: float
    stop_loss: float | None
    take_profit: float | None
    max_hold_seconds: int
    reason: str = ""


@dataclass
class AccountState:
    equity: float
    available_balance: float
    daily_pnl: float = 0.0
    consecutive_losses: int = 0
    open_positions: dict[str, "Position"] = field(default_factory=dict)


@dataclass
class Position:
    symbol: str
    market_type: MarketType
    position_side: PositionSide
    quantity: float
    entry_price: float
    leverage: int = 1
    side: OrderSide = OrderSide.BUY
    stop_loss: float | None = None
    take_profit: float | None = None
    max_hold_seconds: int = 0
    opened_at: datetime = field(default_factory=utc_now)

    @property
    def key(self) -> str:
        return f"{self.market_type}:{self.symbol}:{self.position_side}"

    @property
    def notional(self) -> float:
        return abs(self.quantity * self.entry_price)

    @property
    def margin_used(self) -> float:
        return self.notional / max(self.leverage, 1)

    @property
    def is_short(self) -> bool:
        if self.position_side == PositionSide.SHORT:
            return True
        if self.position_side == PositionSide.BOTH:
            return self.side == OrderSide.SELL
        return False


@dataclass(frozen=True)
class ApprovedOrder:
    symbol: str
    market_type: MarketType
    side: OrderSide
    position_side: PositionSide
    order_type: EntryType
    quantity: float
    leverage: int
    client_order_id: str = field(default_factory=lambda: f"qct-{uuid4().hex[:24]}")
    reduce_only: bool = False
    stop_loss: float | None = None
    take_profit: float | None = None
    max_hold_seconds: int = 0

    @property
    def position_key(self) -> str:
        return f"{self.market_type}:{self.symbol}:{self.position_side}"

    def to_binance_params(self) -> dict[str, Any]:
        params: dict[str, Any] = {
            "symbol": self.symbol,
            "side": self.side.value,
            "type": self.order_type.value,
            "quantity": self.quantity,
            "newClientOrderId": self.client_order_id,
        }
        if self.market_type == MarketType.FUTURES:
            params["positionSide"] = self.position_side.value
            # Binance Futures rejects reduceOnly in Hedge Mode (LONG/SHORT).
            if self.reduce_only and self.position_side == PositionSide.BOTH:
                params["reduceOnly"] = "true"
        return params


@dataclass(frozen=True)
class RejectedOrder:
    symbol: str
    reason: str


@dataclass(frozen=True)
class OrderReceipt:
    client_order_id: str
    symbol: str
    status: OrderStatus
    message: str = ""
    exchange_order_id: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)
    filled_price: float | None = None
    filled_quantity: float | None = None


def check_position_exit(position: Position, snapshot: MarketSnapshot) -> str | None:
    """Evaluate stop-loss, take-profit, and max-hold-seconds exit triggers."""
    if position.symbol != snapshot.symbol or position.market_type != snapshot.market_type:
        return None
    price = snapshot.last_price
    if position.is_short:
        if position.stop_loss is not None and price >= position.stop_loss:
            return "stop_loss"
        if position.take_profit is not None and price <= position.take_profit:
            return "take_profit"
    else:
        if position.stop_loss is not None and price <= position.stop_loss:
            return "stop_loss"
        if position.take_profit is not None and price >= position.take_profit:
            return "take_profit"
    if position.max_hold_seconds > 0:
        elapsed = (snapshot.timestamp - position.opened_at).total_seconds()
        if elapsed >= position.max_hold_seconds:
            return "max_hold_seconds"
    return None


def apply_filled_order(
    account: AccountState,
    order: ApprovedOrder,
    fill_price: float,
    timestamp: datetime | None = None,
    fee_rate: float = 0.0,
) -> float:
    """Update AccountState positions, balance, daily PnL, and consecutive losses after a fill.

    Returns realized net PnL for closing fills, or -entry_fee for opening fills.
    """
    fill_ts = timestamp or utc_now()
    pos_key = order.position_key
    effective_leverage = 1 if order.market_type == MarketType.SPOT else max(order.leverage, 1)
    fee = abs(order.quantity * fill_price) * max(fee_rate, 0.0)

    if order.reduce_only:
        existing = account.open_positions.get(pos_key)
        if existing is None:
            return 0.0
        close_qty = min(order.quantity, existing.quantity)
        if existing.is_short:
            gross_pnl = (existing.entry_price - fill_price) * close_qty
        else:
            gross_pnl = (fill_price - existing.entry_price) * close_qty
        net_pnl = gross_pnl - fee
        released_margin = (existing.entry_price * close_qty) / max(existing.leverage, 1)
        account.available_balance += released_margin + net_pnl
        account.equity += net_pnl
        account.daily_pnl += net_pnl
        if net_pnl < 0:
            account.consecutive_losses += 1
        elif net_pnl > 0:
            account.consecutive_losses = 0

        remaining = existing.quantity - close_qty
        if remaining <= 1e-12:
            del account.open_positions[pos_key]
        else:
            existing.quantity = remaining
        return net_pnl

    margin_required = (order.quantity * fill_price) / effective_leverage
    account.available_balance -= margin_required + fee
    if fee > 0:
        account.equity -= fee
        account.daily_pnl -= fee

    existing = account.open_positions.get(pos_key)
    if existing is None:
        account.open_positions[pos_key] = Position(
            symbol=order.symbol,
            market_type=order.market_type,
            position_side=order.position_side,
            quantity=order.quantity,
            entry_price=fill_price,
            leverage=effective_leverage,
            side=order.side,
            stop_loss=order.stop_loss,
            take_profit=order.take_profit,
            max_hold_seconds=order.max_hold_seconds,
            opened_at=fill_ts,
        )
    else:
        total_qty = existing.quantity + order.quantity
        if total_qty > 0:
            existing.entry_price = (
                (existing.entry_price * existing.quantity) + (fill_price * order.quantity)
            ) / total_qty
        existing.quantity = total_qty
        existing.leverage = effective_leverage
        if order.stop_loss is not None:
            existing.stop_loss = order.stop_loss
        if order.take_profit is not None:
            existing.take_profit = order.take_profit
        if order.max_hold_seconds > 0:
            existing.max_hold_seconds = order.max_hold_seconds
    return -fee
