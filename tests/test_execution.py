import unittest

from qcrypto_trader.config import BinanceCredentials
from qcrypto_trader.execution import LiveExecutionEngine, PaperExecutionEngine
from qcrypto_trader.models import (
    ApprovedOrder,
    EntryType,
    MarketType,
    OrderSide,
    OrderStatus,
    PositionSide,
)


def _futures_order():
    return ApprovedOrder(
        symbol="BTCUSDT",
        market_type=MarketType.FUTURES,
        side=OrderSide.BUY,
        position_side=PositionSide.LONG,
        order_type=EntryType.MARKET,
        quantity=0.001,
        leverage=10,
    )


class ExecutionTests(unittest.IsolatedAsyncioTestCase):
    async def test_paper_execution_records_fill(self):
        engine = PaperExecutionEngine()

        receipt = await engine.submit(_futures_order())

        self.assertEqual(receipt.status, OrderStatus.FILLED)
        self.assertEqual(receipt.message, "paper_fill")
        self.assertEqual(len(engine.receipts), 1)

    async def test_live_execution_rejects_when_disabled(self):
        receipt = await LiveExecutionEngine(enabled=False).submit(_futures_order())

        self.assertEqual(receipt.status, OrderStatus.REJECTED)
        self.assertEqual(receipt.message, "live_trading_disabled")

    async def test_live_execution_requires_credentials(self):
        receipt = await LiveExecutionEngine(enabled=True).submit(_futures_order())

        self.assertEqual(receipt.status, OrderStatus.REJECTED)
        self.assertEqual(receipt.message, "missing_binance_credentials")

    async def test_live_execution_builds_endpoint_preview_without_sending(self):
        receipt = await LiveExecutionEngine(
            enabled=True,
            credentials=BinanceCredentials("key", "secret"),
        ).submit(_futures_order())

        self.assertEqual(receipt.status, OrderStatus.REJECTED)
        self.assertEqual(receipt.message, "live_execution_requires_explicit_adapter")
        self.assertEqual(receipt.raw["endpoint"], "/fapi/v1/order")
        self.assertEqual(receipt.raw["params"]["positionSide"], "LONG")


if __name__ == "__main__":
    unittest.main()
