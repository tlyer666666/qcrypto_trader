# QCrypto Trader Design

## Goal

Build a local Windows trading backend for Binance crypto markets. The first version focuses on safe iteration: public market data, paper execution, candidate ranking, risk checks, and a local dashboard.

## Runtime Shape

- `qcrypto_trader.cli` starts the local service.
- `TradingEngine` owns candidate refresh, strategy evaluation, risk checks, and execution.
- `BinanceRestMarketData` fetches public spot and futures market data.
- `RiskEngine` is the only path from strategy signal to order approval.
- `PaperExecutionEngine` fills approved orders locally; live execution is intentionally locked in v1.

## Trading Rules

- Candidate pool refreshes every 30 seconds.
- Strategy loop runs every 5 seconds.
- Spot can only create long-side buy signals.
- Futures supports hedge mode with `positionSide=LONG|SHORT`.
- Leverage is configurable per symbol, bounded by local config and exchange symbol rule.

## Risk Modes

- `capital_protection`: smaller position limits and higher confidence threshold.
- `balanced`: default mode.
- `aggressive`: larger notional allowance and lower confidence threshold.

Risk modes are not profit guarantees. They only change sizing and acceptance thresholds.

