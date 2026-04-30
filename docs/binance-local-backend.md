# Binance Local Crypto Backend

This note documents the local Binance-backed crypto backend for `qcrypto_trader`.
It is intended for development and paper-trading workflows before any live
exchange credentials are connected.

## Purpose

- Keep exchange-specific behavior isolated from strategy and UI code.
- Make local development reproducible without requiring live orders.
- Prefer dry-run, sandbox, and read-only workflows by default.
- Make the operational risks of live Binance connectivity explicit.

## Configuration checklist

Before enabling a Binance backend locally:

1. Install the project using the packaging instructions in the repository
   README or `pyproject.toml`.
2. Create a local environment file that is not committed to source control.
3. Provide Binance API credentials only through environment variables or a
   local secret manager.
4. Start with testnet/sandbox or read-only keys where possible.
5. Enable live trading only after validating balances, symbols, order sizing,
   precision handling, and cancellation behavior.

Suggested environment variable names:

```text
BINANCE_API_KEY=
BINANCE_API_SECRET=
BINANCE_TESTNET=true
QCRYPTO_TRADER_DRY_RUN=true
```

If the implementation uses different names, keep this document in sync with the
actual configuration module and sample environment file.

## Development workflow

Recommended local workflow:

```bash
python -m venv .venv
source .venv/bin/activate  # Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install -U pip
python -m pip install -e ".[dev]"
python -m pytest
```

For a production-like package smoke test:

```bash
python -m pip install build
python -m build
python -m pip install dist/*.whl
```

Adjust extras and commands to match the project metadata if optional dependency
groups differ.

## Backend design notes

A safe Binance backend should keep these concerns separate:

- **Client construction:** reads configuration, selects testnet/live endpoint,
  and creates the exchange client.
- **Market metadata:** loads symbols, precision, min-notional limits, step
  sizes, and quote/base asset details before order creation.
- **Account state:** reads balances and open orders with rate-limit-aware calls.
- **Order planning:** validates strategy intent against exchange filters before
  any request can become a live order.
- **Execution adapter:** submits, cancels, and reconciles orders. This layer
  should be the only code path allowed to use trading credentials.
- **Persistence/logging:** records request intent, exchange response IDs, errors,
  and reconciliation status without storing secrets.

## Safety notes

- Default to dry-run mode. Live trading should require an explicit opt-in.
- Never commit API keys, account IDs, signed requests, or exported trade logs
  containing sensitive information.
- Prefer Binance testnet or read-only API keys during development.
- Restrict API keys by IP and disable withdrawals for trading keys.
- Validate order size, precision, min notional, and symbol status immediately
  before sending an order.
- Treat network failures and timeouts as unknown execution state until orders are
  reconciled by ID from the exchange.
- Log enough information to audit decisions, but redact secrets and signatures.
- Add kill-switch behavior for repeated API errors, rejected orders, unexpected
  balance deltas, or stale market data.
- This repository is software tooling, not financial advice. Users are
  responsible for compliance, taxes, and trading risk.

## Documentation maintenance

When the Binance backend changes, update:

- README quick-start/backend summary.
- Sample environment variables.
- Packaging/install commands.
- Any operational runbook or deployment notes.
- Tests or smoke-test instructions that prove dry-run behavior still works.
