from __future__ import annotations

import json
import sqlite3
import threading
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
CREATE TABLE IF NOT EXISTS order_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    client_order_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    status TEXT NOT NULL,
    message TEXT NOT NULL,
    exchange_order_id TEXT,
    raw_json TEXT NOT NULL,
    recorded_at TEXT DEFAULT CURRENT_TIMESTAMP
);
"""


class Storage:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.execute("PRAGMA journal_mode=WAL;")
        self.conn.execute("PRAGMA synchronous=NORMAL;")
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def save_order_receipt(self, receipt: OrderReceipt) -> None:
        raw_json = json.dumps(receipt.raw, ensure_ascii=False, default=str)
        with self._lock:
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
            self.conn.execute(
                """
                INSERT INTO order_audit
                    (client_order_id, symbol, status, message, exchange_order_id, raw_json)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    receipt.client_order_id,
                    receipt.symbol,
                    receipt.status.value,
                    receipt.message,
                    receipt.exchange_order_id,
                    raw_json,
                ),
            )
            self.conn.commit()

    def order_count(self) -> int:
        with self._lock:
            cursor = self.conn.execute("SELECT COUNT(*) FROM orders")
            return int(cursor.fetchone()[0])

    def audit_count(self) -> int:
        with self._lock:
            cursor = self.conn.execute("SELECT COUNT(*) FROM order_audit")
            return int(cursor.fetchone()[0])

    def close(self) -> None:
        with self._lock:
            self.conn.close()
