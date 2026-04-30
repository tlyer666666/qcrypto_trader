from __future__ import annotations

from dataclasses import dataclass

from .config import MarketConfig
from .models import Candidate, Ticker


STABLE_BASES = {
    "USDC",
    "FDUSD",
    "TUSD",
    "USDP",
    "DAI",
    "BUSD",
    "EUR",
    "TRY",
    "BRL",
}


@dataclass
class CandidatePool:
    config: MarketConfig

    def build(self, tickers: list[Ticker]) -> list[Candidate]:
        candidates_by_symbol: dict[str, Candidate] = {}
        excluded = {symbol.upper() for symbol in self.config.exclude_symbols}

        for ticker in tickers:
            symbol = ticker.symbol.upper()
            if not symbol.endswith(self.config.quote_asset):
                continue
            if symbol in excluded or self._stable_pair(symbol):
                continue
            if ticker.status != "TRADING":
                continue
            if ticker.quote_volume < self.config.min_quote_volume:
                continue
            if ticker.spread_bps and ticker.spread_bps > self.config.max_spread_bps:
                continue
            if ticker.intraday_range_percent > 45:
                continue

            score = self._score(ticker)
            candidate = Candidate(
                symbol=symbol,
                score=score,
                last_price=ticker.last_price,
                quote_volume=ticker.quote_volume,
                price_change_percent=ticker.price_change_percent,
                spread_bps=ticker.spread_bps,
                reasons=self._reasons(ticker),
            )
            current = candidates_by_symbol.get(symbol)
            if current is None or candidate.score > current.score:
                candidates_by_symbol[symbol] = candidate

        return sorted(candidates_by_symbol.values(), key=lambda item: item.score, reverse=True)[
            : self.config.max_candidates
        ]

    def _stable_pair(self, symbol: str) -> bool:
        base = symbol[: -len(self.config.quote_asset)]
        return base in STABLE_BASES

    @staticmethod
    def _score(ticker: Ticker) -> float:
        volume_score = min(ticker.quote_volume / 1_000_000_000, 1.0)
        momentum_score = min(abs(ticker.price_change_percent) / 12, 1.0)
        spread_penalty = min(ticker.spread_bps / 20, 0.5) if ticker.spread_bps else 0
        return round((volume_score * 0.55) + (momentum_score * 0.45) - spread_penalty, 6)

    @staticmethod
    def _reasons(ticker: Ticker) -> list[str]:
        reasons = []
        if ticker.quote_volume >= 500_000_000:
            reasons.append("high_volume")
        if abs(ticker.price_change_percent) >= 3:
            reasons.append("active_momentum")
        if ticker.spread_bps and ticker.spread_bps <= 5:
            reasons.append("tight_spread")
        return reasons
