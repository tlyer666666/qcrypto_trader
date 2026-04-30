# Qbot Review For Crypto Redesign

Qbot is a broad quant research and trading toolbox. Its public README positions it as an AI quant platform with strategy research, backtesting, GUI views, data access, and multiple market adapters.

For this project, Qbot should be treated as reference material only:

- It carries A-share and mixed-market assumptions that do not map cleanly to 24-hour crypto futures trading.
- It is too broad for a reliable first version of a local Binance executor.
- The safer path is an independent crypto-first backend with small strategy plugins.

The redesign in `qcrypto_trader` keeps the useful idea of a strategy/backtest/trading pipeline, but rebuilds the execution core around Binance market data, futures hedge mode, explicit leverage validation, and paper trading by default.

