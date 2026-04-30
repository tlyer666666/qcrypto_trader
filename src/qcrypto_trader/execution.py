from __future__ import annotations

from dataclasses import dataclass, field

from .binance import build_order_params, order_endpoint
from .config import BinanceCredentials
from .models import (
    ApprovedOrder,
    OrderReceipt,
    OrderStatus,
)


class ExecutionEngine:
    async def submit(self, order: ApprovedOrder) -> OrderReceipt:
        raise NotImplementedError


@dataclass
class PaperExecutionEngine(ExecutionEngine):
    receipts: list[OrderReceipt] = field(default_factory=list)

    async def submit(self, order: ApprovedOrder) -> OrderReceipt:
        receipt = OrderReceipt(
            client_order_id=order.client_order_id,
            symbol=order.symbol,
            status=OrderStatus.FILLED,
            message="paper_fill",
            raw=order.to_binance_params(),
        )
        self.receipts.append(receipt)
        return receipt


@dataclass
class LiveExecutionEngine(ExecutionEngine):
    enabled: bool = False
    credentials: BinanceCredentials | None = None

    async def submit(self, order: ApprovedOrder) -> OrderReceipt:
        if not self.enabled:
            return OrderReceipt(
                client_order_id=order.client_order_id,
                symbol=order.symbol,
                status=OrderStatus.REJECTED,
                message="live_trading_disabled",
                raw=order.to_binance_params(),
            )
        if not self.credentials or not self.credentials.available:
            return OrderReceipt(
                client_order_id=order.client_order_id,
                symbol=order.symbol,
                status=OrderStatus.REJECTED,
                message="missing_binance_credentials",
                raw=build_order_params(order),
            )
        return OrderReceipt(
            client_order_id=order.client_order_id,
            symbol=order.symbol,
            status=OrderStatus.REJECTED,
            message="live_execution_requires_explicit_adapter",
            raw={
                "endpoint": order_endpoint(order),
                "params": build_order_params(order),
            },
        )
