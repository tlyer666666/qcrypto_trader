import unittest

from qcrypto_trader.config import StrategyConfig
from qcrypto_trader.models import MarketSnapshot, MarketType, PositionSide
from qcrypto_trader.strategy import ShortMomentumStrategy, StrategyContext


class StrategyTests(unittest.TestCase):
    def test_strategy_emits_short_signal_for_negative_futures_momentum(self):
        strategy = ShortMomentumStrategy(StrategyConfig(min_confidence=0.1))
        snapshot = MarketSnapshot(
            symbol="BTCUSDT",
            market_type=MarketType.FUTURES,
            last_price=100000,
            quote_volume=1_000_000_000,
            price_change_percent=-4,
            spread_bps=1,
            book_imbalance=0.8,
            volatility=0.01,
        )

        signals = strategy.on_market(StrategyContext(default_quantity=0.001), snapshot)

        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0].position_side, PositionSide.SHORT)

    def test_strategy_does_not_short_spot(self):
        strategy = ShortMomentumStrategy(StrategyConfig(min_confidence=0.1))
        snapshot = MarketSnapshot(
            symbol="BTCUSDT",
            market_type=MarketType.SPOT,
            last_price=100000,
            quote_volume=1_000_000_000,
            price_change_percent=-4,
            spread_bps=1,
            book_imbalance=0.8,
            volatility=0.01,
        )

        self.assertEqual(strategy.on_market(StrategyContext(default_quantity=0.001), snapshot), [])


if __name__ == "__main__":
    unittest.main()
