from __future__ import annotations

import math
import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path


VALID_RISK_MODES = frozenset({"capital_protection", "balanced", "aggressive"})


@dataclass(frozen=True)
class AppConfig:
    host: str = "127.0.0.1"
    port: int = 8765
    paper_trading: bool = True
    allow_live_trading: bool = False
    db_path: str | None = None


@dataclass(frozen=True)
class MarketConfig:
    quote_asset: str = "USDT"
    candidate_refresh_seconds: int = 30
    strategy_interval_seconds: int = 5
    max_candidates: int = 20
    max_open_positions: int = 3
    min_quote_volume: float = 50_000_000
    max_spread_bps: float = 12
    exclude_symbols: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class FuturesConfig:
    enabled: bool = True
    hedge_mode: bool = True
    default_leverage: int = 5
    max_configurable_leverage: int = 125


@dataclass(frozen=True)
class SpotConfig:
    enabled: bool = True


@dataclass(frozen=True)
class RiskConfig:
    risk_mode: str = "balanced"
    max_notional_per_position: float = 100
    max_total_notional: float = 300
    max_daily_loss: float = 50
    max_consecutive_losses: int = 3
    kill_switch: bool = False


@dataclass(frozen=True)
class StrategyConfig:
    name: str = "short_momentum"
    min_confidence: float = 0.68
    momentum_weight: float = 0.35
    volume_weight: float = 0.25
    book_imbalance_weight: float = 0.20
    funding_weight: float = 0.10
    volatility_weight: float = 0.10


@dataclass(frozen=True)
class TradingConfig:
    app: AppConfig = field(default_factory=AppConfig)
    market: MarketConfig = field(default_factory=MarketConfig)
    futures: FuturesConfig = field(default_factory=FuturesConfig)
    spot: SpotConfig = field(default_factory=SpotConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    strategy: StrategyConfig = field(default_factory=StrategyConfig)
    leverage: dict[str, int] = field(default_factory=dict)

    def leverage_for(self, symbol: str) -> int:
        return int(self.leverage.get(symbol.upper(), self.futures.default_leverage))


@dataclass(frozen=True)
class BinanceCredentials:
    api_key: str | None
    api_secret: str | None

    @property
    def available(self) -> bool:
        return bool(self.api_key and self.api_secret)


def _section(data: dict, name: str) -> dict:
    value = data.get(name, {})
    return value if isinstance(value, dict) else {}


def validate_config(config: TradingConfig) -> TradingConfig:
    if not (1 <= config.app.port <= 65535):
        raise ValueError(f"app.port must be between 1 and 65535, got {config.app.port}")
    if config.market.candidate_refresh_seconds < 1:
        raise ValueError("market.candidate_refresh_seconds must be >= 1")
    if config.market.strategy_interval_seconds < 1:
        raise ValueError("market.strategy_interval_seconds must be >= 1")
    if config.market.max_candidates < 1:
        raise ValueError("market.max_candidates must be >= 1")
    if config.market.max_open_positions < 1:
        raise ValueError("market.max_open_positions must be >= 1")
    if not math.isfinite(config.market.min_quote_volume) or config.market.min_quote_volume < 0:
        raise ValueError("market.min_quote_volume must be finite and >= 0")
    if not math.isfinite(config.market.max_spread_bps) or config.market.max_spread_bps < 0:
        raise ValueError("market.max_spread_bps must be finite and >= 0")

    if config.futures.max_configurable_leverage < 1 or config.futures.max_configurable_leverage > 125:
        raise ValueError("futures.max_configurable_leverage must be between 1 and 125")
    if config.futures.default_leverage < 1 or config.futures.default_leverage > config.futures.max_configurable_leverage:
        raise ValueError("futures.default_leverage must be between 1 and futures.max_configurable_leverage")

    if config.risk.risk_mode not in VALID_RISK_MODES:
        raise ValueError(f"Unknown risk.risk_mode {config.risk.risk_mode!r}")
    if not math.isfinite(config.risk.max_notional_per_position) or config.risk.max_notional_per_position <= 0:
        raise ValueError("risk.max_notional_per_position must be > 0")
    if not math.isfinite(config.risk.max_total_notional) or config.risk.max_total_notional < config.risk.max_notional_per_position:
        raise ValueError("risk.max_total_notional must be >= risk.max_notional_per_position")
    if not math.isfinite(config.risk.max_daily_loss) or config.risk.max_daily_loss <= 0:
        raise ValueError("risk.max_daily_loss must be > 0")
    if config.risk.max_consecutive_losses < 1:
        raise ValueError("risk.max_consecutive_losses must be >= 1")

    if not math.isfinite(config.strategy.min_confidence) or not (0.0 <= config.strategy.min_confidence <= 1.0):
        raise ValueError("strategy.min_confidence must be between 0.0 and 1.0")
    weights = [
        config.strategy.momentum_weight,
        config.strategy.volume_weight,
        config.strategy.book_imbalance_weight,
        config.strategy.funding_weight,
        config.strategy.volatility_weight,
    ]
    if any(not math.isfinite(w) or w < 0 for w in weights) or sum(weights) <= 0:
        raise ValueError("strategy weights must be finite, non-negative, and sum to > 0")

    for sym, lev in config.leverage.items():
        if lev < 1 or lev > config.futures.max_configurable_leverage:
            raise ValueError(
                f"leverage.{sym} must be between 1 and {config.futures.max_configurable_leverage}, got {lev}"
            )
    return config


def load_config(path: str | Path | None = None) -> TradingConfig:
    raw: dict = {}
    if path:
        config_path = Path(path)
        if config_path.exists():
            with config_path.open("rb") as handle:
                raw = tomllib.load(handle)

    config = TradingConfig(
        app=AppConfig(**_section(raw, "app")),
        market=MarketConfig(**_section(raw, "market")),
        futures=FuturesConfig(**_section(raw, "futures")),
        spot=SpotConfig(**_section(raw, "spot")),
        risk=RiskConfig(**_section(raw, "risk")),
        strategy=StrategyConfig(**_section(raw, "strategy")),
        leverage={k.upper(): int(v) for k, v in _section(raw, "leverage").items()},
    )
    return validate_config(config)


def load_binance_credentials() -> BinanceCredentials:
    return BinanceCredentials(
        api_key=os.getenv("BINANCE_API_KEY"),
        api_secret=os.getenv("BINANCE_API_SECRET"),
    )
