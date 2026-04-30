from __future__ import annotations

import asyncio
import json
import threading
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from .app import INDEX_HTML
from .binance import build_futures_leverage_params
from .config import TradingConfig, load_binance_credentials
from .engine import TradingEngine


class EngineLoop:
    def __init__(self):
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self) -> None:
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def run(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(timeout=30)


class LocalPanelHandler(BaseHTTPRequestHandler):
    engine: TradingEngine
    config: TradingConfig
    engine_loop: EngineLoop

    def log_message(self, format: str, *args: Any) -> None:
        return

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/":
            self._html(INDEX_HTML)
            return
        if path == "/api/state":
            self._json(self._state())
            return
        self._json({"error": "not_found"}, HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/api/start":
            self.engine_loop.run(self.engine.start())
            self._json({"running": self.engine.state.running})
            return
        if path == "/api/stop":
            self.engine_loop.run(self.engine.stop())
            self._json({"running": self.engine.state.running})
            return
        if path == "/api/refresh":
            self.engine_loop.run(self.engine.refresh_market_data())
            self._json(
                {
                    "candidate_count": len(self.engine.state.candidates),
                    "last_market_error": self.engine.state.last_market_error,
                }
            )
            return
        if path == "/api/leverage":
            data = parse_qs(parsed.query)
            symbol = data.get("symbol", [""])[0]
            leverage = int(data.get("leverage", ["0"])[0])
            self._json(self._set_leverage(symbol, leverage))
            return
        parts = path.strip("/").split("/")
        if len(parts) == 4 and parts[:2] == ["api", "leverage"]:
            self._json(self._set_leverage(parts[2], int(parts[3])))
            return
        self._json({"error": "not_found"}, HTTPStatus.NOT_FOUND)

    def _state(self) -> dict[str, Any]:
        credentials = load_binance_credentials()
        return {
            "status": {
                "running": self.engine.state.running,
                "mode": self.engine.state.mode,
                "credentials_loaded": credentials.available,
                "last_rejection": self.engine.state.last_rejection,
                "last_receipt": self.engine.state.last_receipt,
                "last_candidate_refresh": self.engine.state.last_candidate_refresh,
                "last_market_error": self.engine.state.last_market_error,
            },
            "risk": {
                "paper_trading": self.config.app.paper_trading,
                "allow_live_trading": self.config.app.allow_live_trading,
                "hedge_mode": self.config.futures.hedge_mode,
                "risk_mode": self.config.risk.risk_mode,
                "default_leverage": self.config.futures.default_leverage,
                "kill_switch": self.config.risk.kill_switch,
            },
            "leverage": dict(sorted(self.config.leverage.items())),
            "candidates": [candidate.__dict__ for candidate in self.engine.state.candidates],
        }

    def _set_leverage(self, symbol: str, leverage: int) -> dict[str, Any]:
        error = self.engine.set_leverage(symbol, leverage)
        if error:
            return {"ok": False, "error": error}
        return {
            "ok": True,
            "symbol": symbol.upper(),
            "leverage": leverage,
            "binance_params": build_futures_leverage_params(symbol, leverage),
            "submitted_to_exchange": False,
        }

    def _html(self, body: str, status: HTTPStatus = HTTPStatus.OK) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(body.encode("utf-8"))

    def _json(self, body: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.end_headers()
        self.wfile.write(json.dumps(body, ensure_ascii=False).encode("utf-8"))


def run_simple_panel(
    engine: TradingEngine,
    config: TradingConfig,
    open_browser: bool = True,
) -> None:
    loop = EngineLoop()

    class Handler(LocalPanelHandler):
        pass

    Handler.engine = engine
    Handler.config = config
    Handler.engine_loop = loop

    server = ThreadingHTTPServer((config.app.host, config.app.port), Handler)
    url = f"http://{config.app.host}:{config.app.port}"
    if open_browser:
        webbrowser.open(url)
    print(f"QCrypto Trader panel running at {url}")
    try:
        server.serve_forever()
    finally:
        server.server_close()
