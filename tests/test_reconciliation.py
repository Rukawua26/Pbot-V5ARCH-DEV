import unittest
from datetime import datetime
from threading import RLock
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from core.reconciliation import generate_client_order_id, reconcile_bootstrap_state


class ReconciliationTest(unittest.TestCase):
    def test_client_order_id_is_deterministic(self):
        a = generate_client_order_id("BTC/USDT", "BUY", 1712222222.123, "abc123")
        b = generate_client_order_id("BTC/USDT", "BUY", 1712222222.123, "abc123")
        self.assertEqual(a, b)
        self.assertTrue(a.startswith("sai-v118-"))

    @patch("core.reconciliation.send_telegram_msg")
    def test_integrity_lock_is_enabled_when_balance_diff_is_high(self, mocked_tg):
        bot = SimpleNamespace()
        bot.lock = RLock()
        bot.db_lock = RLock()
        bot.active_trades = {}
        bot.balance = 100.0
        bot.is_paused = False
        bot.integrity_lock_active = False
        bot.log = MagicMock()
        bot.execution = SimpleNamespace(fetch_positions=lambda: [])
        bot.get_current_balance = lambda: 80.0
        bot.brain = SimpleNamespace(
            save_active_trade_state=MagicMock(),
            save_error_snapshot=MagicMock(),
            delete_active_trade_state=MagicMock(),
        )

        reconcile_bootstrap_state(bot)

        self.assertTrue(bot.integrity_lock_active)
        self.assertTrue(bot.is_paused)
        mocked_tg.assert_called()

    @patch("core.reconciliation.send_telegram_msg")
    def test_adopts_exchange_orphan_position(self, mocked_tg):
        bot = SimpleNamespace()
        bot.lock = RLock()
        bot.db_lock = RLock()
        bot.active_trades = {}
        bot.balance = 100.0
        bot.is_paused = False
        bot.integrity_lock_active = False
        bot.log = MagicMock()
        bot.execution = SimpleNamespace(
            fetch_positions=lambda: [
                {
                    "symbol": "ETH/USDT:USDT",
                    "contracts": 0.5,
                    "side": "long",
                    "entryPrice": 3000,
                }
            ],
            place_hard_sl=MagicMock(),
        )
        bot.get_current_balance = lambda: 100.0
        bot.brain = SimpleNamespace(
            save_active_trade_state=MagicMock(),
            save_error_snapshot=MagicMock(),
            delete_active_trade_state=MagicMock(),
        )

        reconcile_bootstrap_state(bot)

        self.assertIn("ETH/USDT", bot.active_trades)
        trade = bot.active_trades["ETH/USDT"]
        self.assertTrue(trade.get("adopted_orphan", False))
        bot.execution.place_hard_sl.assert_called_once()
        mocked_tg.assert_called()


if __name__ == "__main__":
    unittest.main()
