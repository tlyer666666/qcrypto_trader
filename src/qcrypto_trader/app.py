from __future__ import annotations

import hmac
import json
import os
import secrets
from pathlib import Path
from urllib.parse import urlparse

from .binance import build_futures_leverage_params, parse_symbol_rules
from .config import TradingConfig, load_binance_credentials, load_config
from .engine import TradingEngine


INDEX_HTML = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>QCrypto Trader</title>
  <style>
    :root { color-scheme: light; font-family: Inter, Segoe UI, Arial, sans-serif; }
    body { margin: 0; background: #f6f7f9; color: #18202a; }
    header { padding: 18px 24px; background: #111827; color: white; }
    main { padding: 24px; display: grid; gap: 16px; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); }
    section { background: white; border: 1px solid #d7dce3; border-radius: 8px; padding: 16px; }
    h1 { margin: 0; font-size: 22px; }
    h2 { margin: 0 0 12px; font-size: 16px; }
    button { border: 0; border-radius: 6px; padding: 9px 12px; background: #2563eb; color: white; cursor: pointer; }
    button.stop { background: #dc2626; }
    pre { white-space: pre-wrap; font-size: 13px; }
    .muted { color: #667085; }
  </style>
</head>
<body>
  <header>
    <h1>QCrypto Trader</h1>
    <div class="muted">Local Binance quant backend</div>
  </header>
  <main>
    <section>
      <h2>Controls</h2>
      <button onclick="post('/api/start')">Start</button>
      <button class="stop" onclick="post('/api/stop')">Stop</button>
      <button onclick="post('/api/refresh')">Refresh Candidates</button>
    </section>
    <section>
      <h2>Leverage</h2>
      <input id="levSymbol" value="BTCUSDT" aria-label="Symbol">
      <input id="levValue" value="10" type="number" min="1" aria-label="Leverage">
      <button onclick="setLeverage()">Set</button>
      <pre id="leverage">Loading...</pre>
    </section>
    <section>
      <h2>Status</h2>
      <pre id="status">Loading...</pre>
    </section>
    <section>
      <h2>Risk</h2>
      <pre id="risk">Loading...</pre>
    </section>
    <section>
      <h2>Candidates</h2>
      <pre id="candidates">Loading...</pre>
    </section>
  </main>
  <script>
    const API_TOKEN = __API_TOKEN__;
    function authHeaders() {
      return { 'Authorization': 'Bearer ' + API_TOKEN };
    }
    async function post(url) {
      await fetch(url, { method: 'POST', headers: authHeaders() });
      await refresh();
    }
    async function setLeverage() {
      const symbol = document.getElementById('levSymbol').value;
      const value = document.getElementById('levValue').value;
      const res = await fetch(
        `/api/leverage/${encodeURIComponent(symbol)}/${encodeURIComponent(value)}`,
        { method: 'POST', headers: authHeaders() }
      );
      document.getElementById('leverage').textContent = JSON.stringify(await res.json(), null, 2);
      await refresh();
    }
    async function refresh() {
      const res = await fetch('/api/state', { headers: authHeaders() });
      const data = await res.json();
      document.getElementById('status').textContent = JSON.stringify(data.status, null, 2);
      document.getElementById('risk').textContent = JSON.stringify(data.risk, null, 2);
      document.getElementById('leverage').textContent = JSON.stringify(data.leverage, null, 2);
      document.getElementById('candidates').textContent = JSON.stringify(data.candidates, null, 2);
    }
    refresh();
    setInterval(refresh, 3000);
  </script>
</body>
</html>
"""


def resolve_api_token(explicit_token: str | None = None) -> str:
    if explicit_token and explicit_token.strip():
        return explicit_token.strip()
    env_token = os.getenv("QCRYPTO_API_TOKEN", "").strip()
    if env_token:
        return env_token
    return secrets.token_urlsafe(32)


def render_index_html(api_token: str) -> str:
    return INDEX_HTML.replace("__API_TOKEN__", json.dumps(api_token))


def verify_bearer_token(auth_header: str | None, expected_token: str) -> bool:
    if not auth_header or not expected_token:
        return False
    scheme, _, token = auth_header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return False
    return hmac.compare_digest(token.strip(), expected_token)


def verify_csrf_headers(
    origin: str | None,
    referer: str | None,
    host: str | None,
) -> bool:
    if not host or not host.strip():
        return False
    normalized_host = host.strip().lower()
    if origin:
        parsed = urlparse(origin)
        return parsed.scheme in {"http", "https"} and parsed.netloc.lower() == normalized_host
    if referer:
        parsed = urlparse(referer)
        return parsed.scheme in {"http", "https"} and parsed.netloc.lower() == normalized_host
    return True


def create_engine(config: TradingConfig | None = None) -> TradingEngine:
    engine = TradingEngine(config or load_config())
    engine.set_symbol_rules(
        parse_symbol_rules(
            {
                "symbols": [
                    {
                        "symbol": "BTCUSDT",
                        "status": "TRADING",
                        "filters": [
                            {"filterType": "LOT_SIZE", "minQty": "0.001", "stepSize": "0.001"},
                            {"filterType": "MIN_NOTIONAL", "notional": "5"},
                            {"filterType": "PRICE_FILTER", "tickSize": "0.01"},
                        ],
                    }
                ]
            },
            futures=True,
        )
    )
    return engine


def create_app(
    config_path: str | Path | None = None,
    api_token: str | None = None,
):
    try:
        from fastapi import Depends, FastAPI, HTTPException, Request
        from fastapi.responses import HTMLResponse
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "FastAPI is not installed. Run `pip install -e .` inside qcrypto_trader."
        ) from exc

    config = load_config(config_path)
    credentials = load_binance_credentials()
    engine = create_engine(config)
    token = resolve_api_token(api_token)
    app = FastAPI(title="QCrypto Trader", version="0.1.0")
    app.state.api_token = token
    app.state.engine = engine

    def require_auth(request: Request) -> None:
        if not verify_bearer_token(request.headers.get("authorization"), token):
            raise HTTPException(status_code=401, detail="unauthorized")

    def require_post_guard(request: Request) -> None:
        require_auth(request)
        if not verify_csrf_headers(
            request.headers.get("origin"),
            request.headers.get("referer"),
            request.headers.get("host"),
        ):
            raise HTTPException(status_code=403, detail="csrf_origin_mismatch")

    @app.get("/", response_class=HTMLResponse)
    async def index():
        return render_index_html(token)

    @app.get("/api/state", dependencies=[Depends(require_auth)])
    async def state():
        return engine.snapshot_state(credentials.available)

    @app.post("/api/start", dependencies=[Depends(require_post_guard)])
    async def start():
        await engine.start()
        return {"running": engine.state.running}

    @app.post("/api/stop", dependencies=[Depends(require_post_guard)])
    async def stop():
        await engine.stop()
        return {"running": engine.state.running}

    @app.post("/api/refresh", dependencies=[Depends(require_post_guard)])
    async def refresh():
        await engine.refresh_market_data()
        snapshot = engine.snapshot_state(credentials.available)
        return {
            "candidate_count": len(snapshot["candidates"]),
            "last_market_error": snapshot["status"]["last_market_error"],
        }

    @app.post("/api/leverage/{symbol}/{leverage}", dependencies=[Depends(require_post_guard)])
    async def set_leverage(symbol: str, leverage: int):
        error = engine.set_leverage(symbol, leverage)
        if error:
            return {"ok": False, "error": error}
        return {
            "ok": True,
            "symbol": symbol.upper(),
            "leverage": leverage,
            "binance_params": build_futures_leverage_params(symbol, leverage),
            "submitted_to_exchange": False,
        }

    return app
