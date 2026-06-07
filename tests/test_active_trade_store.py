import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from tools.learning import Brain


class ActiveTradeStoreTest(unittest.TestCase):
    def test_save_load_delete_active_trade_state_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            brain = Brain(str(Path(tmp) / "brain.db"))
            open_time = datetime(2026, 1, 2, 3, 4, 5)

            saved = brain.save_active_trade_state(
                "BTC/USDT",
                {
                    "symbol": "BTC/USDT",
                    "side": "buy",
                    "open_time": open_time,
                    "entry_price": 100.0,
                },
            )

            self.assertTrue(saved)
            loaded = brain.load_active_trade_states()
            self.assertEqual(loaded["BTC/USDT"]["open_time"], open_time)
            self.assertEqual(loaded["BTC/USDT"]["entry_price"], 100.0)

            brain.delete_active_trade_state("BTC/USDT")

            self.assertEqual(brain.load_active_trade_states(), {})


if __name__ == "__main__":
    unittest.main()
