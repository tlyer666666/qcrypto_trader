# QCrypto Binance Local Trader Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a Windows-local Binance crypto quant trading backend with a local panel, paper trading, 30-second candidate refresh, adjustable leverage, and futures hedge-mode order support.

**Architecture:** Rebuild independently from Qbot instead of modifying Qbot directly. Qbot is treated as reference material because its A-share and mixed-market assumptions do not fit a crypto-first 24-hour futures system.

**Tech Stack:** Python 3.11+, asyncio, stdlib fallback HTTP panel, optional FastAPI/uvicorn, SQLite, Binance REST/WebSocket-ready adapters, PyInstaller.

---

## Task 1: Core Trading Model

- [x] Define unified market/order models for spot and futures.
- [x] Ensure futures orders emit `positionSide=LONG|SHORT`.
- [x] Keep spot long-only and block spot short signals.
- [x] Add tests for hedge-mode order parameters.

## Task 2: Binance Market Data And Candidate Pool

- [x] Use Binance public market data APIs.
- [x] Refresh candidates every 30 seconds by default.
- [x] Filter USDT pairs by volume, spread, status, stable-pair exclusions, and extreme intraday range.
- [x] Deduplicate spot/futures duplicates by symbol.
- [x] Verify public Binance candidate refresh works without credentials.

## Task 3: Risk, Leverage, And Modes

- [x] Add per-symbol leverage config.
- [x] Validate leverage against local max and exchange symbol max.
- [x] Add risk modes: `capital_protection`, `balanced`, `aggressive`.
- [x] Keep live trading locked by default.

## Task 4: Strategy And Backtesting

- [x] Add short momentum strategy skeleton.
- [x] Add deterministic backtest skeleton for future strategy optimization.
- [x] Expand reporting metrics for acceptance/rejection rates and estimated fees.
- [x] Add CSV historical data loader.

## Task 5: Local Panel And Windows Packaging

- [x] Add local web panel with start/stop/refresh/leverage controls.
- [x] Add stdlib panel fallback when FastAPI is not installed.
- [x] Add Windows run and build scripts.
- [ ] Build final `QCryptoTrader.exe` with PyInstaller after user approves installing build dependency.

## Current Safety Defaults

- Paper mode is on.
- `allow_live_trading` is false.
- API keys are read from environment variables only.
- No command in v1 places a real order unless live execution is explicitly unlocked in code/config later.
