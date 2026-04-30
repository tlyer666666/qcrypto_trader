import tempfile
import unittest
from pathlib import Path

from qcrypto_trader.config import load_config


class ConfigTests(unittest.TestCase):
    def test_config_loads_leverage_and_candidate_interval(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_file = Path(tmp) / "config.toml"
            config_file.write_text(
                """
                [market]
                candidate_refresh_seconds = 30

                [futures]
                default_leverage = 7

                [leverage]
                BTCUSDT = 25
                """,
                encoding="utf-8",
            )

            config = load_config(config_file)

        self.assertEqual(config.market.candidate_refresh_seconds, 30)
        self.assertEqual(config.leverage_for("BTCUSDT"), 25)
        self.assertEqual(config.leverage_for("ETHUSDT"), 7)


if __name__ == "__main__":
    unittest.main()
