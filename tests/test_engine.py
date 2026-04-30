import unittest

from qcrypto_trader.config import FuturesConfig, TradingConfig
from qcrypto_trader.engine import TradingEngine
from qcrypto_trader.models import SymbolRule


class EngineTests(unittest.TestCase):
    def test_set_leverage_accepts_exchange_allowed_value(self):
        config = TradingConfig(
            futures=FuturesConfig(default_leverage=5, max_configurable_leverage=125)
        )
        engine = TradingEngine(config)
        engine.set_symbol_rules(
            {"BTCUSDT": SymbolRule("BTCUSDT", 0.001, 0.001, 5, 0.01, max_leverage=50)}
        )

        error = engine.set_leverage("btcusdt", 25)

        self.assertIsNone(error)
        self.assertEqual(config.leverage["BTCUSDT"], 25)

    def test_set_leverage_rejects_exchange_limit(self):
        config = TradingConfig(
            futures=FuturesConfig(default_leverage=5, max_configurable_leverage=125)
        )
        engine = TradingEngine(config)
        engine.set_symbol_rules(
            {"BTCUSDT": SymbolRule("BTCUSDT", 0.001, 0.001, 5, 0.01, max_leverage=20)}
        )

        self.assertEqual(engine.set_leverage("BTCUSDT", 25), "leverage_above_exchange_limit")


if __name__ == "__main__":
    unittest.main()
