from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class AppConfig:
    host: str = "127.0.0.1"
    port: int = 8765
    paper_trading: bool = True
    allow_live_trading: bool = False


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
        return int(self.leverage.get(symbol, self.futures.default_leverage))


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


def load_config(path: str | Path | None = None) -> TradingConfig:
    raw: dict = {}
    if path:
        config_path = Path(path)
        if config_path.exists():
            with config_path.open("rb") as handle:
                raw = tomllib.load(handle)

    return TradingConfig(
        app=AppConfig(**_section(raw, "app")),
        market=MarketConfig(**_section(raw, "market")),
        futures=FuturesConfig(**_section(raw, "futures")),
        spot=SpotConfig(**_section(raw, "spot")),
        risk=RiskConfig(**_section(raw, "risk")),
        strategy=StrategyConfig(**_section(raw, "strategy")),
        leverage={k.upper(): int(v) for k, v in _section(raw, "leverage").items()},
    )


def load_binance_credentials() -> BinanceCredentials:
    return BinanceCredentials(
        api_key=os.getenv("BINANCE_API_KEY"),
        api_secret=os.getenv("BINANCE_API_SECRET"),
    )

