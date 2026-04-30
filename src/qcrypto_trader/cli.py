from __future__ import annotations

import argparse
import webbrowser

from .app import create_app
from .config import load_config
from .engine import TradingEngine
from .simple_server import run_simple_panel


def main() -> None:
    parser = argparse.ArgumentParser(description="Run QCrypto Trader")
    parser.add_argument("--config", default="config.example.toml")
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()

    config = load_config(args.config)
    try:
        import uvicorn
    except ModuleNotFoundError:
        run_simple_panel(
            TradingEngine(config),
            config,
            open_browser=not args.no_browser,
        )
        return

    app = create_app(args.config)

    url = f"http://{config.app.host}:{config.app.port}"
    if not args.no_browser:
        webbrowser.open(url)
    uvicorn.run(app, host=config.app.host, port=config.app.port)
