# QCrypto Trader

Local Binance crypto quant trading backend for Windows.

This first version is intentionally conservative:

- Paper trading is the default.
- Live trading is locked behind explicit config.
- API keys are not stored in `config.toml`.
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

`build_exe.bat` installs the build dependency before packaging. Review it before running.

## Current Scope

This is a redesigned crypto-first system, not a direct Qbot fork. It includes Binance public market data, 30-second candidate refresh, hedge-mode futures order parameters, configurable leverage checks, risk modes, paper execution, and a local panel.

Live order submission remains intentionally locked in v1.

## Strategy Optimization Loop

The first optimization surface is intentionally simple:

- Export or collect historical snapshots with `symbol,last_price,quote_volume,price_change_percent,spread_bps,book_imbalance,funding_rate,volatility`.
- Load them with `qcrypto_trader.backtest.load_snapshots_csv`.
- Run `Backtester` to compare accepted orders, rejection rate, gross notional, and estimated fees.
- Add new strategies behind the same `Strategy.on_market(ctx, snapshot) -> list[Signal]` shape.

This keeps strategy work separate from Binance credentials and live execution.
