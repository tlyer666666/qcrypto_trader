from __future__ import annotations

import sqlite3
from pathlib import Path

from .models import OrderReceipt


SCHEMA = """
CREATE TABLE IF NOT EXISTS orders (
    client_order_id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    status TEXT NOT NULL,
    message TEXT NOT NULL,
    exchange_order_id TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
"""


class Storage:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.execute(SCHEMA)
        self.conn.commit()

    def save_order_receipt(self, receipt: OrderReceipt) -> None:
        self.conn.execute(
            """
            INSERT OR REPLACE INTO orders
                (client_order_id, symbol, status, message, exchange_order_id)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                receipt.client_order_id,
                receipt.symbol,
                receipt.status.value,
                receipt.message,
                receipt.exchange_order_id,
            ),
        )
        self.conn.commit()

    def order_count(self) -> int:
        cursor = self.conn.execute("SELECT COUNT(*) FROM orders")
        return int(cursor.fetchone()[0])

    def close(self) -> None:
        self.conn.close()

