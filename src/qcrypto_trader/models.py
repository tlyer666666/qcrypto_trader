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

    @property
    def spread_bps(self) -> float:
        if not self.bid_price or not self.ask_price or self.last_price <= 0:
            return 0.0
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


@dataclass
class Candidate:
    symbol: str
    score: float
    last_price: float
    quote_volume: float
    price_change_percent: float
    spread_bps: float
    reasons: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class MarketSnapshot:
    symbol: str
    market_type: MarketType
    last_price: float
    quote_volume: float
    price_change_percent: float
    spread_bps: float = 0.0
    book_imbalance: float = 0.0
    funding_rate: float = 0.0
    volatility: float = 0.0
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

    @property
    def key(self) -> str:
        return f"{self.market_type}:{self.symbol}:{self.position_side}"

    @property
    def notional(self) -> float:
        return abs(self.quantity * self.entry_price)


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
            if self.reduce_only:
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
