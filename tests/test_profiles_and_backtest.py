import unittest
import tempfile
from pathlib import Path

from qcrypto_trader.backtest import Backtester, load_snapshots_csv
from qcrypto_trader.config import RiskConfig, StrategyConfig, TradingConfig
from qcrypto_trader.models import MarketSnapshot, MarketType, SymbolRule
from qcrypto_trader.profiles import effective_risk_limits, resolve_profile


class ProfileAndBacktestTests(unittest.TestCase):
    def test_risk_profile_resolves_aggressive(self):
        profile = resolve_profile(RiskConfig(risk_mode="aggressive"))

        self.assertEqual(profile.name, "aggressive")
        self.assertLess(profile.min_confidence_offset, 0)

    def test_effective_risk_limits_do_not_raise_user_caps(self):
        limits = effective_risk_limits(
            RiskConfig(
                risk_mode="aggressive",
                max_notional_per_position=25,
                max_total_notional=50,
                max_daily_loss=10,
                max_consecutive_losses=1,
            )
        )

        self.assertEqual(limits.max_notional_per_position, 25)
        self.assertEqual(limits.max_total_notional, 50)
        self.assertEqual(limits.max_daily_loss, 10)
        self.assertEqual(limits.max_consecutive_losses, 1)

    def test_backtester_counts_accepted_signals(self):
        config = TradingConfig(
            strategy=StrategyConfig(min_confidence=0.1),
            risk=RiskConfig(max_notional_per_position=200, max_total_notional=500),
            leverage={"BTCUSDT": 5},
        )
        backtester = Backtester(
            config,
            {"BTCUSDT": SymbolRule("BTCUSDT", 0.001, 0.001, 5, 0.01, max_leverage=125)},
            {"BTCUSDT"},
        )
        metrics = backtester.run(
            [
                MarketSnapshot(
                    symbol="BTCUSDT",
                    market_type=MarketType.FUTURES,
                    last_price=100000,
                    quote_volume=1_000_000_000,
                    price_change_percent=4,
                    spread_bps=1,
                    book_imbalance=0.8,
                    volatility=0.01,
                )
            ]
        )

        self.assertEqual(metrics.snapshots, 1)
        self.assertEqual(metrics.signals, 1)
        self.assertEqual(metrics.accepted_orders, 1)
        self.assertEqual(metrics.rejected_orders, 0)
        self.assertEqual(metrics.acceptance_rate, 1.0)
        self.assertGreater(metrics.gross_notional, 0)
        self.assertGreater(metrics.estimated_fees, 0)

    def test_load_snapshots_csv(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sample.csv"
            path.write_text(
                "symbol,last_price,quote_volume,price_change_percent,spread_bps,book_imbalance,volatility\n"
                "btcusdt,100000,1000000000,4,1,0.8,0.01\n",
                encoding="utf-8",
            )

            snapshots = load_snapshots_csv(path)

        self.assertEqual(len(snapshots), 1)
        self.assertEqual(snapshots[0].symbol, "BTCUSDT")
        self.assertEqual(snapshots[0].last_price, 100000)


if __name__ == "__main__":
    unittest.main()
