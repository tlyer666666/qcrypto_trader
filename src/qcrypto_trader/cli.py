from __future__ import annotations

import argparse
import os
import webbrowser

from .app import create_app, create_engine, resolve_api_token
from .config import load_config
from .simple_server import browser_url, run_simple_panel


def main() -> None:
    parser = argparse.ArgumentParser(description="Run QCrypto Trader")
    parser.add_argument("--config", default="config.example.toml")
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()

    config = load_config(args.config)
    api_token = resolve_api_token()
    try:
        import uvicorn
    except ModuleNotFoundError:
        run_simple_panel(
            create_engine(config),
            config,
            open_browser=not args.no_browser,
            api_token=api_token,
        )
        return

    app = create_app(args.config, api_token=api_token)
    url = browser_url(config.app.host, config.app.port)
    if not os.getenv("QCRYPTO_API_TOKEN"):
        print(f"QCrypto Trader API token: {api_token}")
    if not args.no_browser:
        webbrowser.open(url)
    uvicorn.run(app, host=config.app.host, port=config.app.port)
