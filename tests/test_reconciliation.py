import unittest
from datetime import datetime, timedelta, timezone
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
            fetch_open_orders=lambda: [],
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

    def test_keeps_pending_trade_if_open_order_exists_by_client_order_id(self):
        bot = SimpleNamespace()
        bot.lock = RLock()
        bot.db_lock = RLock()
        bot.active_trades = {
            "BTC/USDT": {
                "symbol": "BTC/USDT",
                "side": "BUY",
                "is_shadow": False,
                "status": "PENDING_SEND",
                "entry_client_order_id": "sai-v118-abc123",
            }
        }
        bot.balance = 100.0
        bot.is_paused = False
        bot.integrity_lock_active = False
        bot.log = MagicMock()
        bot.execution = SimpleNamespace(
            fetch_positions=lambda: [],
            fetch_open_orders=lambda: [
                {
                    "id": "12345",
                    "symbol": "BTC/USDT",
                    "status": "open",
                    "clientOrderId": "sai-v118-abc123",
                }
            ],
            fetch_order_by_client_id=lambda _symbol, _coid: None,
            place_hard_sl=MagicMock(),
        )
        bot.get_current_balance = lambda: 100.0
        bot.brain = SimpleNamespace(
            save_active_trade_state=MagicMock(),
            save_error_snapshot=MagicMock(),
            delete_active_trade_state=MagicMock(),
        )

        reconcile_bootstrap_state(bot)

        self.assertIn("BTC/USDT", bot.active_trades)
        state = bot.active_trades["BTC/USDT"]
        self.assertEqual(state.get("status"), "PENDING_EXCHANGE_OPEN")
        self.assertEqual(state.get("exchange_open_order_id"), "12345")
        bot.brain.save_error_snapshot.assert_not_called()
        bot.brain.delete_active_trade_state.assert_not_called()

    def test_marks_lost_when_no_position_and_no_open_order(self):
        stale_ts = (datetime.now(timezone.utc) - timedelta(seconds=180)).isoformat()
        bot = SimpleNamespace()
        bot.lock = RLock()
        bot.db_lock = RLock()
        bot.active_trades = {
            "BTC/USDT": {
                "symbol": "BTC/USDT",
                "side": "BUY",
                "is_shadow": False,
                "status": "PENDING_SEND",
                "entry_client_order_id": "sai-v118-missing",
                "intent_created_at_utc": stale_ts,
            }
        }
        bot.balance = 100.0
        bot.is_paused = False
        bot.integrity_lock_active = False
        bot.log = MagicMock()
        bot.execution = SimpleNamespace(
            fetch_positions=lambda: [],
            fetch_open_orders=lambda: [],
            fetch_order_by_client_id=lambda _symbol, _coid: None,
            place_hard_sl=MagicMock(),
        )
        bot.get_current_balance = lambda: 100.0
        bot.brain = SimpleNamespace(
            save_active_trade_state=MagicMock(),
            save_error_snapshot=MagicMock(),
            delete_active_trade_state=MagicMock(),
        )

        reconcile_bootstrap_state(bot)

        self.assertNotIn("BTC/USDT", bot.active_trades)
        bot.brain.save_error_snapshot.assert_called_once()
        bot.brain.delete_active_trade_state.assert_called_once_with("BTC/USDT")
        self.assertEqual(
            bot.brain.save_error_snapshot.call_args[0][1], "INTENT_EXPIRED"
        )

    def test_keeps_recent_pending_send_when_exchange_still_has_no_order(self):
        fresh_ts = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()
        bot = SimpleNamespace()
        bot.lock = RLock()
        bot.db_lock = RLock()
        bot.active_trades = {
            "BTC/USDT": {
                "symbol": "BTC/USDT",
                "side": "BUY",
                "is_shadow": False,
                "status": "PENDING_SEND",
                "entry_client_order_id": "sai-v118-fresh",
                "intent_created_at_utc": fresh_ts,
            }
        }
        bot.balance = 100.0
        bot.is_paused = False
        bot.integrity_lock_active = False
        bot.pending_send_stale_seconds = 90
        bot.log = MagicMock()
        bot.execution = SimpleNamespace(
            fetch_positions=lambda: [],
            fetch_open_orders=lambda: [],
            fetch_order_by_client_id=lambda _symbol, _coid: None,
            place_hard_sl=MagicMock(),
        )
        bot.get_current_balance = lambda: 100.0
        bot.brain = SimpleNamespace(
            save_active_trade_state=MagicMock(),
            save_error_snapshot=MagicMock(),
            delete_active_trade_state=MagicMock(),
        )

        reconcile_bootstrap_state(bot)

        self.assertIn("BTC/USDT", bot.active_trades)
        self.assertEqual(bot.active_trades["BTC/USDT"].get("status"), "PENDING_SEND")
        bot.brain.delete_active_trade_state.assert_not_called()
        bot.brain.save_error_snapshot.assert_not_called()

    def test_recovers_pending_trade_using_explicit_order_lookup(self):
        bot = SimpleNamespace()
        bot.lock = RLock()
        bot.db_lock = RLock()
        bot.active_trades = {
            "BTC/USDT": {
                "symbol": "BTC/USDT",
                "side": "BUY",
                "is_shadow": False,
                "status": "PENDING_SEND",
                "entry_client_order_id": "sai-v118-explicit",
            }
        }
        bot.balance = 100.0
        bot.is_paused = False
        bot.integrity_lock_active = False
        bot.log = MagicMock()
        bot.execution = SimpleNamespace(
            fetch_positions=lambda: [],
            fetch_open_orders=lambda: [],
            fetch_order_by_client_id=lambda _symbol, _coid: {
                "id": "777",
                "symbol": "BTC/USDT",
                "status": "new",
                "clientOrderId": "sai-v118-explicit",
            },
            place_hard_sl=MagicMock(),
        )
        bot.get_current_balance = lambda: 100.0
        bot.brain = SimpleNamespace(
            save_active_trade_state=MagicMock(),
            save_error_snapshot=MagicMock(),
            delete_active_trade_state=MagicMock(),
        )

        reconcile_bootstrap_state(bot)

        self.assertIn("BTC/USDT", bot.active_trades)
        state = bot.active_trades["BTC/USDT"]
        self.assertEqual(state.get("status"), "PENDING_EXCHANGE_OPEN")
        self.assertEqual(state.get("exchange_open_order_id"), "777")
        bot.brain.delete_active_trade_state.assert_not_called()

    def test_does_not_mark_lost_when_symbol_exists_in_open_orders(self):
        bot = SimpleNamespace()
        bot.lock = RLock()
        bot.db_lock = RLock()
        bot.active_trades = {
            "XRP/USDT": {
                "symbol": "XRP/USDT",
                "side": "BUY",
                "is_shadow": False,
                "status": "PENDING_SEND",
            }
        }
        bot.balance = 100.0
        bot.is_paused = False
        bot.integrity_lock_active = False
        bot.log = MagicMock()
        bot.execution = SimpleNamespace(
            fetch_positions=lambda: [],
            fetch_open_orders=lambda: [
                {
                    "id": "open-xyz",
                    "symbol": "XRP/USDT",
                    "status": "open",
                }
            ],
            fetch_order_by_client_id=lambda _symbol, _coid: None,
            place_hard_sl=MagicMock(),
        )
        bot.get_current_balance = lambda: 100.0
        bot.brain = SimpleNamespace(
            save_active_trade_state=MagicMock(),
            save_error_snapshot=MagicMock(),
            delete_active_trade_state=MagicMock(),
        )

        reconcile_bootstrap_state(bot)

        self.assertIn("XRP/USDT", bot.active_trades)
        bot.brain.delete_active_trade_state.assert_not_called()


if __name__ == "__main__":
    unittest.main()
