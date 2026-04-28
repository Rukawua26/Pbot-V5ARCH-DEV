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
        # Nuevo formato: E_{hash}
        self.assertTrue(a.startswith("E_"), f"Expected 'E_' prefix, got: {a}")

    @patch("core.reconciliation.Config")
    @patch("core.reconciliation.send_telegram_msg")
    def test_integrity_lock_is_enabled_when_balance_diff_is_high(self, mocked_tg, mock_config):
        mock_config.PAPER_MODE = False
        bot = SimpleNamespace()
        bot.lock = RLock()
        bot.db_lock = RLock()
        bot.active_trades = {}
        bot.balance = 100.0
        bot.is_paused = False
        bot.integrity_lock_active = False
        bot.log = MagicMock()
        bot.execution = SimpleNamespace(fetch_positions=lambda: [], fetch_open_orders=lambda: [])
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
        # Generar ID con nuevo formato
        entry_coid = generate_client_order_id("BTC/USDT", "BUY", 1712222222.123, "abc123")

        bot = SimpleNamespace()
        bot.lock = RLock()
        bot.db_lock = RLock()
        bot.active_trades = {
            "BTC/USDT": {
                "symbol": "BTC/USDT",
                "side": "BUY",
                "is_shadow": False,
                "status": "PENDING_SEND",
                "entry_client_order_id": entry_coid,
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
                    "clientOrderId": entry_coid,
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
        # Generar ID con nuevo formato
        missing_coid = generate_client_order_id("BTC/USDT", "BUY", 1712222222.123, "missing")

        bot = SimpleNamespace()
        bot.lock = RLock()
        bot.db_lock = RLock()
        bot.active_trades = {
            "BTC/USDT": {
                "symbol": "BTC/USDT",
                "side": "BUY",
                "is_shadow": False,
                "status": "PENDING_SEND",
                "entry_client_order_id": missing_coid,
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
        # Generar ID con nuevo formato
        fresh_coid = generate_client_order_id("BTC/USDT", "BUY", 1712222222.123, "fresh")

        bot = SimpleNamespace()
        bot.lock = RLock()
        bot.db_lock = RLock()
        bot.active_trades = {
            "BTC/USDT": {
                "symbol": "BTC/USDT",
                "side": "BUY",
                "is_shadow": False,
                "status": "PENDING_SEND",
                "entry_client_order_id": fresh_coid,
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

    def test_reconciliation_aborts_without_mutating_state_when_positions_fail(self):
        bot = SimpleNamespace()
        bot.lock = RLock()
        bot.db_lock = RLock()
        bot.active_trades = {
            "BTC/USDT": {"symbol": "BTC/USDT", "status": "OPEN", "is_shadow": False}
        }
        bot.balance = 100.0
        bot.is_paused = False
        bot.integrity_lock_active = False
        bot.log = MagicMock()
        bot.execution = SimpleNamespace(fetch_positions=MagicMock(side_effect=RuntimeError("down")))
        bot.get_current_balance = lambda: 100.0
        bot.brain = SimpleNamespace(
            save_active_trade_state=MagicMock(),
            save_error_snapshot=MagicMock(),
            delete_active_trade_state=MagicMock(),
        )

        reconcile_bootstrap_state(bot)

        self.assertIn("BTC/USDT", bot.active_trades)
        bot.brain.save_active_trade_state.assert_not_called()
        bot.brain.save_error_snapshot.assert_not_called()
        bot.brain.delete_active_trade_state.assert_not_called()

    def test_reconciliation_skips_integrity_lock_when_balance_fetch_fails(self):
        bot = SimpleNamespace()
        bot.lock = RLock()
        bot.db_lock = RLock()
        bot.active_trades = {}
        bot.balance = 100.0
        bot.is_paused = False
        bot.integrity_lock_active = False
        bot.log = MagicMock()
        bot.execution = SimpleNamespace(fetch_positions=lambda: [], fetch_open_orders=lambda: [])
        bot.get_current_balance = MagicMock(side_effect=RuntimeError("balance down"))
        bot.brain = SimpleNamespace(
            save_active_trade_state=MagicMock(),
            save_error_snapshot=MagicMock(),
            delete_active_trade_state=MagicMock(),
        )

        reconcile_bootstrap_state(bot)

        self.assertFalse(bot.integrity_lock_active)
        self.assertFalse(bot.is_paused)


class OrphanAdoptionTest(unittest.TestCase):
    @patch("core.reconciliation.send_telegram_msg")
    def test_orphan_rejected_below_min_size(self, mocked_tg):
        from core.config.operational import OperationalConfig
        original_min = getattr(OperationalConfig, 'ORPHAN_ADOPTION_MIN_SIZE_USD', 10.0)
        original_max = getattr(OperationalConfig, 'ORPHAN_ADOPTION_MAX_SIZE_USD', 10000.0)
        OperationalConfig.ORPHAN_ADOPTION_MIN_SIZE_USD = 10.0
        OperationalConfig.ORPHAN_ADOPTION_MAX_SIZE_USD = 10000.0

        try:
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
                        "contracts": 0.001,
                        "side": "long",
                        "entryPrice": 3000,
                    }
                ],
                fetch_open_orders=lambda: [],
                place_hard_sl=MagicMock(),
                fetch_ticker=lambda s: {"last": 3000},
            )
            bot.get_current_balance = lambda: 100.0
            bot.brain = SimpleNamespace(
                save_active_trade_state=MagicMock(),
                save_error_snapshot=MagicMock(),
                delete_active_trade_state=MagicMock(),
            )

            reconcile_bootstrap_state(bot)

            self.assertNotIn("ETH/USDT", bot.active_trades)
        finally:
            OperationalConfig.ORPHAN_ADOPTION_MIN_SIZE_USD = original_min
            OperationalConfig.ORPHAN_ADOPTION_MAX_SIZE_USD = original_max

    @patch("core.reconciliation.send_telegram_msg")
    def test_orphan_rejected_above_max_size(self, mocked_tg):
        from core.config.operational import OperationalConfig
        original_min = getattr(OperationalConfig, 'ORPHAN_ADOPTION_MIN_SIZE_USD', 10.0)
        original_max = getattr(OperationalConfig, 'ORPHAN_ADOPTION_MAX_SIZE_USD', 10000.0)
        OperationalConfig.ORPHAN_ADOPTION_MIN_SIZE_USD = 10.0
        OperationalConfig.ORPHAN_ADOPTION_MAX_SIZE_USD = 10000.0

        try:
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
                        "contracts": 10.0,
                        "side": "long",
                        "entryPrice": 3000,
                    }
                ],
                fetch_open_orders=lambda: [],
                place_hard_sl=MagicMock(),
                fetch_ticker=lambda s: {"last": 3000},
            )
            bot.get_current_balance = lambda: 100.0
            bot.brain = SimpleNamespace(
                save_active_trade_state=MagicMock(),
                save_error_snapshot=MagicMock(),
                delete_active_trade_state=MagicMock(),
            )

            reconcile_bootstrap_state(bot)

            self.assertNotIn("ETH/USDT", bot.active_trades)
        finally:
            OperationalConfig.ORPHAN_ADOPTION_MIN_SIZE_USD = original_min
            OperationalConfig.ORPHAN_ADOPTION_MAX_SIZE_USD = original_max

    @patch("core.reconciliation.send_telegram_msg")
    def test_orphan_adopted_with_dynamic_sl(self, mocked_tg):
        from core.config.operational import OperationalConfig
        original_min = getattr(OperationalConfig, 'ORPHAN_ADOPTION_MIN_SIZE_USD', 10.0)
        original_max = getattr(OperationalConfig, 'ORPHAN_ADOPTION_MAX_SIZE_USD', 10000.0)
        OperationalConfig.ORPHAN_ADOPTION_MIN_SIZE_USD = 10.0
        OperationalConfig.ORPHAN_ADOPTION_MAX_SIZE_USD = 10000.0

        try:
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
                place_hard_sl=MagicMock(return_value={"id": "sl-123"}),
                fetch_ticker=lambda s: {"last": 2950},
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
            self.assertTrue(trade.get("adopted_orphan"))
            expected_sl = 3000 - (2950 * 0.02)
            self.assertAlmostEqual(trade["sl"], expected_sl, places=2)
        finally:
            OperationalConfig.ORPHAN_ADOPTION_MIN_SIZE_USD = original_min
            OperationalConfig.ORPHAN_ADOPTION_MAX_SIZE_USD = original_max

    @patch("core.reconciliation.send_telegram_msg")
    def test_orphan_fallback_to_fixed_percentage_when_ticker_fails(self, mocked_tg):
        from core.config.operational import OperationalConfig
        original_min = getattr(OperationalConfig, 'ORPHAN_ADOPTION_MIN_SIZE_USD', 10.0)
        original_max = getattr(OperationalConfig, 'ORPHAN_ADOPTION_MAX_SIZE_USD', 10000.0)
        OperationalConfig.ORPHAN_ADOPTION_MIN_SIZE_USD = 10.0
        OperationalConfig.ORPHAN_ADOPTION_MAX_SIZE_USD = 10000.0

        try:
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
                place_hard_sl=MagicMock(return_value={"id": "sl-123"}),
                fetch_ticker=MagicMock(side_effect=RuntimeError("API error")),
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
            expected_sl = 3000 * 0.98
            self.assertAlmostEqual(trade["sl"], expected_sl, places=2)
        finally:
            OperationalConfig.ORPHAN_ADOPTION_MIN_SIZE_USD = original_min
            OperationalConfig.ORPHAN_ADOPTION_MAX_SIZE_USD = original_max


if __name__ == "__main__":
    unittest.main()
