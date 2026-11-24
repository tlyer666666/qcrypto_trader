from __future__ import annotations

import asyncio
import json
import os
import threading
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from .app import (
    render_index_html,
    resolve_api_token,
    verify_bearer_token,
    verify_csrf_headers,
)
from .binance import build_futures_leverage_params
from .config import TradingConfig, load_binance_credentials
from .engine import TradingEngine


def browser_url(host: str, port: int) -> str:
    display_host = "127.0.0.1" if host in {"0.0.0.0", "::", ""} else host
    return f"http://{display_host}:{port}"


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

    def stop(self) -> None:
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(timeout=5)
        if not self.loop.is_closed():
            self.loop.close()


class LocalPanelHandler(BaseHTTPRequestHandler):
    engine: TradingEngine
    config: TradingConfig
    engine_loop: EngineLoop
    api_token: str

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _check_auth(self) -> bool:
        if not verify_bearer_token(self.headers.get("Authorization"), self.api_token):
            self._json({"error": "unauthorized"}, HTTPStatus.UNAUTHORIZED)
            return False
        return True

    def _check_csrf(self) -> bool:
        if not verify_csrf_headers(
            self.headers.get("Origin"),
            self.headers.get("Referer"),
            self.headers.get("Host"),
        ):
            self._json({"error": "csrf_origin_mismatch"}, HTTPStatus.FORBIDDEN)
            return False
        return True

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/":
            self._html(render_index_html(self.api_token))
            return
        if path == "/api/state":
            if not self._check_auth():
                return
            self._json(self._state())
            return
        self._json({"error": "not_found"}, HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        if not self._check_auth():
            return
        if not self._check_csrf():
            return

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
            snapshot = self._state()
            self._json(
                {
                    "candidate_count": len(snapshot["candidates"]),
                    "last_market_error": snapshot["status"]["last_market_error"],
                }
            )
            return
        if path == "/api/leverage":
            data = parse_qs(parsed.query)
            symbol = data.get("symbol", [""])[0]
            try:
                leverage = int(data.get("leverage", [""])[0])
            except ValueError:
                self._json({"ok": False, "error": "invalid_leverage"}, HTTPStatus.BAD_REQUEST)
                return
            if not symbol.strip():
                self._json({"ok": False, "error": "invalid_symbol"}, HTTPStatus.BAD_REQUEST)
                return
            self._json(self._set_leverage(symbol, leverage))
            return
        parts = path.strip("/").split("/")
        if len(parts) == 4 and parts[:2] == ["api", "leverage"]:
            try:
                leverage = int(parts[3])
            except ValueError:
                self._json({"ok": False, "error": "invalid_leverage"}, HTTPStatus.BAD_REQUEST)
                return
            self._json(self._set_leverage(parts[2], leverage))
            return
        self._json({"error": "not_found"}, HTTPStatus.NOT_FOUND)

    def _state(self) -> dict[str, Any]:
        credentials = load_binance_credentials()
        return self.engine.snapshot_state(credentials.available)

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


def create_simple_server(
    engine: TradingEngine,
    config: TradingConfig,
    api_token: str | None = None,
) -> tuple[ThreadingHTTPServer, EngineLoop, str]:
    loop = EngineLoop()
    token = resolve_api_token(api_token)

    class Handler(LocalPanelHandler):
        pass

    Handler.engine = engine
    Handler.config = config
    Handler.engine_loop = loop
    Handler.api_token = token

    server = ThreadingHTTPServer((config.app.host, config.app.port), Handler)
    return server, loop, token


def run_simple_panel(
    engine: TradingEngine,
    config: TradingConfig,
    open_browser: bool = True,
    api_token: str | None = None,
) -> None:
    server, loop, token = create_simple_server(engine, config, api_token=api_token)
    url = browser_url(config.app.host, config.app.port)
    if open_browser:
        webbrowser.open(url)
    print(f"QCrypto Trader panel running at {url}")
    if not os.getenv("QCRYPTO_API_TOKEN"):
        print(f"QCrypto Trader API token: {token}")
    try:
        server.serve_forever()
    finally:
        server.server_close()
        loop.stop()
