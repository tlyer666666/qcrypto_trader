import unittest

from qcrypto_trader.candidate_pool import CandidatePool
from qcrypto_trader.config import MarketConfig
from qcrypto_trader.models import Ticker


class CandidatePoolTests(unittest.TestCase):
    def test_candidate_pool_filters_and_sorts_usdt_pairs(self):
        pool = CandidatePool(
            MarketConfig(
                max_candidates=2,
                min_quote_volume=100,
                max_spread_bps=10,
                exclude_symbols=["BADUSDT"],
            )
        )
        tickers = [
            Ticker("BTCUSDT", 100, 1_000_000_000, 4, 105, 95, 99.99, 100.01),
            Ticker("ETHUSDT", 10, 500_000_000, 3, 11, 9, 9.999, 10.001),
            Ticker("LOWUSDT", 10, 10, 1),
            Ticker("USDCUSDT", 1, 9_000_000_000, 0.1),
            Ticker("BADUSDT", 2, 9_000_000_000, 9),
            Ticker("WIDEUSDT", 10, 9_000_000_000, 2, 11, 9, 9.0, 11.0),
        ]

        result = pool.build(tickers)

        self.assertEqual([item.symbol for item in result], ["BTCUSDT", "ETHUSDT"])

    def test_candidate_pool_uses_configured_refresh_interval_default(self):
        self.assertEqual(MarketConfig().candidate_refresh_seconds, 30)

    def test_candidate_pool_deduplicates_spot_and_futures_symbols(self):
        pool = CandidatePool(MarketConfig(max_candidates=5, min_quote_volume=100))
        result = pool.build(
            [
                Ticker("BTCUSDT", 100, 1_000_000, 1),
                Ticker("BTCUSDT", 101, 2_000_000, 4),
            ]
        )

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].last_price, 101)


if __name__ == "__main__":
    unittest.main()
