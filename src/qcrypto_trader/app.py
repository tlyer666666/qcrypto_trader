from __future__ import annotations

from pathlib import Path

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
    async function post(url) {
      await fetch(url, { method: 'POST' });
      await refresh();
    }
    async function setLeverage() {
      const symbol = document.getElementById('levSymbol').value;
      const value = document.getElementById('levValue').value;
      const res = await fetch(`/api/leverage/${encodeURIComponent(symbol)}/${encodeURIComponent(value)}`, { method: 'POST' });
      document.getElementById('leverage').textContent = JSON.stringify(await res.json(), null, 2);
      await refresh();
    }
    async function refresh() {
      const data = await (await fetch('/api/state')).json();
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
                            {"filterType": "MIN_NOTIONAL", "minNotional": "5"},
                            {"filterType": "PRICE_FILTER", "tickSize": "0.01"},
                        ],
                    }
                ]
            },
            futures=True,
        )
    )
    return engine


def create_app(config_path: str | Path | None = None):
    try:
        from fastapi import FastAPI
        from fastapi.responses import HTMLResponse
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "FastAPI is not installed. Run `pip install -e .` inside qcrypto_trader."
        ) from exc

    config = load_config(config_path)
    credentials = load_binance_credentials()
    engine = create_engine(config)
    app = FastAPI(title="QCrypto Trader", version="0.1.0")

    @app.get("/", response_class=HTMLResponse)
    async def index():
        return INDEX_HTML

    @app.get("/api/state")
    async def state():
        return {
            "status": {
                "running": engine.state.running,
                "mode": engine.state.mode,
                "credentials_loaded": credentials.available,
                "last_rejection": engine.state.last_rejection,
                "last_receipt": engine.state.last_receipt,
                "last_candidate_refresh": engine.state.last_candidate_refresh,
                "last_market_error": engine.state.last_market_error,
            },
            "risk": {
                "paper_trading": config.app.paper_trading,
                "allow_live_trading": config.app.allow_live_trading,
                "hedge_mode": config.futures.hedge_mode,
                "risk_mode": config.risk.risk_mode,
                "default_leverage": config.futures.default_leverage,
                "kill_switch": config.risk.kill_switch,
            },
            "leverage": dict(sorted(config.leverage.items())),
            "candidates": [candidate.__dict__ for candidate in engine.state.candidates],
        }

    @app.post("/api/start")
    async def start():
        await engine.start()
        return {"running": engine.state.running}

    @app.post("/api/stop")
    async def stop():
        await engine.stop()
        return {"running": engine.state.running}

    @app.post("/api/refresh")
    async def refresh():
        await engine.refresh_market_data()
        return {
            "candidate_count": len(engine.state.candidates),
            "last_market_error": engine.state.last_market_error,
        }

    @app.post("/api/leverage/{symbol}/{leverage}")
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
