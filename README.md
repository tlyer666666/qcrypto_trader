# QCrypto Trader

Local Binance crypto quant trading backend for Windows.

Defaults:

- Paper trading is on by default.
- Live trading is off by default and needs explicit opt-in.
- API keys are read from environment variables, not from `config.toml`.
- Spot and USD-M futures share one internal order model.
- Futures orders support hedge mode through `positionSide=LONG|SHORT`.

## Quick Start

```bash
cd qcrypto_trader
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"
python -m qcrypto_trader --config config.example.toml
```

Open `http://127.0.0.1:8765` after the server starts.

## Binance Keys

Do not put keys in `config.toml`. For local testing, use environment variables:

```bash
set BINANCE_API_KEY=your_key
set BINANCE_API_SECRET=your_secret
```

The app still starts without keys, but only public market data and paper trading are available.

## Build EXE

```bash
pip install -e ".[build]"
pyinstaller qcrypto_trader.spec
```

The executable is created under `dist/QCryptoTrader/`.

Windows helpers are also available:

```bat
scripts\run_local.bat
scripts\build_exe.bat
```

`build_exe.bat` installs the build dependency before packaging.

## Current Scope

Crypto-first system, not a fork of Qbot. It includes Binance public market data, 30-second candidate refresh, hedge-mode futures order parameters, configurable leverage checks, risk modes, paper execution, and a local panel.

Live order submission is disabled in this version.

## Strategy Optimization Loop

To compare strategies:

- Export or collect historical snapshots with `symbol,last_price,quote_volume,price_change_percent,spread_bps,book_imbalance,funding_rate,volatility`.
- Load them with `qcrypto_trader.backtest.load_snapshots_csv`.
- Run `Backtester` to compare accepted orders, rejection rate, gross notional, and estimated fees.
- Add new strategies behind the same `Strategy.on_market(ctx, snapshot) -> list[Signal]` shape.

This keeps strategy work separate from Binance credentials and live execution.
