import unittest
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from core.bot_facade import BotFacade
from core.bot_guardian import run_guardian_loop
from core.bot_misc_ops import handle_command


class _FacadeBot(BotFacade):
    pass


class RegressionFlowsTest(unittest.TestCase):
    def test_command_router_export_path(self):
        bot = SimpleNamespace()
        basic = MagicMock(return_value=False)
        export = MagicMock()
        notify = MagicMock()

        handle_command(bot, "/export_data", basic, export, notify)

        basic.assert_called_once_with(bot, "/export_data")
        export.assert_called_once()
        notify.assert_called_once()
        self.assertIn("Dataset Maestro exportado", notify.call_args[0][0])

    def test_command_router_stops_when_basic_handler_consumes(self):
        bot = SimpleNamespace()
        basic = MagicMock(return_value=True)
        export = MagicMock()
        notify = MagicMock()

        handle_command(bot, "/audit", basic, export, notify)

        basic.assert_called_once_with(bot, "/audit")
        export.assert_not_called()
        notify.assert_not_called()

    @patch("notifier.send_telegram_msg")
    @patch("core.reconciliation.recover_halt_if_exchange_consistent")
    def test_recover_halt_command_delegates_to_safe_recovery(self, mocked_recover, mocked_tg):
        from core.command_router import handle_basic_command

        mocked_recover.return_value = (True, "RECOVERY_OK")
        bot = SimpleNamespace()

        handled = handle_basic_command(bot, "/recover_halt")

        self.assertTrue(handled)
        mocked_recover.assert_called_once_with(bot)
        mocked_tg.assert_called_once()

    @patch("core.bot_facade.run_execute_order")
    def test_facade_execute_order_delegates(self, mocked_exec):
        mocked_exec.return_value = "OK"
        bot = _FacadeBot()

        result = bot.execute_order("BTC/USDT", "BUY", 100.0, 1.0)

        self.assertEqual(result, "OK")
        mocked_exec.assert_called_once()

    @patch("core.bot_facade.tm_close_trade")
    def test_facade_close_trade_delegates(self, mocked_close):
        bot = _FacadeBot()

        bot.close_trade("BTC/USDT", "TEST", 100.0)

        mocked_close.assert_called_once()

    def test_guardian_loop_exits_clean_when_not_running(self):
        bot = SimpleNamespace(
            is_running=False,
            log=MagicMock(),
            _guardian_stats={"loops": 0, "work_s": 0.0, "sleep_s": 0.0, "bailout_count": 0},
            active_trades={},
        )

        run_guardian_loop(bot)

        bot.log.assert_called_once()


class TradeManagerHelpersTest(unittest.TestCase):
    def test_validate_entry_preconditions_shutdown(self):
        from core.trade_manager import _validate_entry_preconditions

        bot = SimpleNamespace(
            stop_requested=True,
            shutdown_in_progress=False,
            active_trades={},
            log=MagicMock(),
            confidence_stagnation_lock_active=False,
        )
        result = _validate_entry_preconditions(bot, "BTC/USDT", False)
        self.assertEqual(result, "SHUTDOWN_IN_PROGRESS")

    def test_validate_entry_preconditions_recovery_pending(self):
        from core.trade_manager import _validate_entry_preconditions

        bot = SimpleNamespace(
            stop_requested=False,
            shutdown_in_progress=False,
            active_trades={"BTC/USDT": {"status": "PENDING_SEND"}},
            log=MagicMock(),
            confidence_stagnation_lock_active=False,
        )
        result = _validate_entry_preconditions(bot, "BTC/USDT", False)
        self.assertEqual(result, "RECOVERY_PENDING_STATE")

    def test_validate_entry_preconditions_pass(self):
        from core.trade_manager import _validate_entry_preconditions

        bot = SimpleNamespace(
            stop_requested=False,
            shutdown_in_progress=False,
            active_trades={},
            log=MagicMock(),
            confidence_stagnation_lock_active=False,
        )
        result = _validate_entry_preconditions(bot, "BTC/USDT", False)
        self.assertIsNone(result)

    def test_validate_symbol_entry_blocked(self):
        from core.trade_manager import _validate_symbol_entry

        bot = SimpleNamespace(log=MagicMock())
        bot._load_runtime_symbol_controls = MagicMock(
            return_value={"blocked": {"BTC"}, "reduced": set()}
        )
        result = _validate_symbol_entry(bot, "BTC/USDT", False)
        self.assertEqual(result, "SYMBOL_BLOCKED_MATRIX")

    def test_validate_symbol_entry_quarantined(self):
        from core.trade_manager import _validate_symbol_entry

        mock_exec = MagicMock()
        mock_exec.is_symbol_quarantined = MagicMock(return_value=True)
        mock_exec.get_symbol_quarantine_remaining_seconds = MagicMock(
            return_value=300
        )
        bot = SimpleNamespace(execution=mock_exec, log=MagicMock())
        bot._load_runtime_symbol_controls = MagicMock(
            return_value={"blocked": set(), "reduced": set()}
        )
        result = _validate_symbol_entry(bot, "BTC/USDT", False)
        self.assertEqual(result, "SYMBOL_QUARANTINED")

    def test_calculate_pnl_and_metrics_buy(self):
        from core.trade_manager import _calculate_pnl_and_metrics

        trade = {
            "entry": 50000.0,
            "amount": 0.1,
            "mae_price": 49000.0,
            "mfe_price": 51000.0,
        }
        result = _calculate_pnl_and_metrics(trade, 50500.0, 10.0, "BUY")

        self.assertEqual(result["amt"], 0.1)
        self.assertEqual(result["pnl_bruto_usd"], 50.0)
        self.assertEqual(result["pnl_neto_usd"], 40.0)
        self.assertGreater(result["mfe_percent"], 0)
        self.assertGreater(result["mae_percent"], 0)

    def test_calculate_pnl_and_metrics_sell(self):
        from core.trade_manager import _calculate_pnl_and_metrics

        trade = {
            "entry": 50000.0,
            "amount": 0.1,
            "mae_price": 51000.0,
            "mfe_price": 49000.0,
        }
        result = _calculate_pnl_and_metrics(trade, 49500.0, 10.0, "SELL")

        self.assertEqual(result["amt"], 0.1)
        self.assertEqual(result["pnl_bruto_usd"], 50.0)


class GuardianHelpersTest(unittest.TestCase):
    def test_fetch_prices_with_fallback_ws(self):
        from core.bot_guardian import _fetch_prices_with_fallback

        mock_bot = MagicMock()
        mock_bot.live_prices.copy.return_value = {
            "BTC/USDT": 50000.0,
            "ETH/USDT": 3000.0,
        }
        mock_bot.execution.fetch_all_prices = MagicMock(
            side_effect=Exception("fail")
        )
        mock_bot.price_lock = nullcontext()

        price_map = _fetch_prices_with_fallback(mock_bot)
        self.assertEqual(price_map, {"BTC/USDT": 50000.0, "ETH/USDT": 3000.0})

    def test_prioritize_symbols_reals_first(self):
        from core.bot_guardian import _prioritize_symbols

        snapshot = {
            "BTC/USDT": {"is_shadow": False},
            "ETH/USDT": {"is_shadow": True},
            "SOL/USDT": {"is_shadow": False},
        }
        result = _prioritize_symbols(snapshot)
        self.assertEqual(result, ["BTC/USDT", "SOL/USDT", "ETH/USDT"])


class ExecutionServiceHelpersTest(unittest.TestCase):
    def test_with_exit_state_enriches_order(self):
        from core.execution_service import _with_exit_state

        order = {"id": "123", "status": "open"}
        result = _with_exit_state(order, "FILLED")

        self.assertEqual(result["exit_state"], "FILLED")
        self.assertEqual(result["id"], "123")

    def test_with_exit_state_returns_none_for_none(self):
        from core.execution_service import _with_exit_state

        result = _with_exit_state(None, "FILLED")
        self.assertIsNone(result)


class RuntimeSafetyCriticalRegressionTest(unittest.TestCase):
    def test_recover_halt_checks_open_orders(self):
        from core.reconciliation import recover_halt_if_exchange_consistent
        from unittest.mock import MagicMock, patch

        mock_exec = MagicMock()
        mock_exec.fetch_positions.return_value = []
        mock_exec.fetch_open_orders.return_value = [
            {"symbol": "BTC/USDT", "id": "order-123", "side": "buy", "amount": 0.1}
        ]

        bot = MagicMock()
        bot.execution = mock_exec
        bot.active_trades = {}
        bot.get_current_balance = MagicMock(return_value=1000.0)
        bot.lock = MagicMock()
        bot.integrity_lock_active = True
        bot.halt_system_active = True
        bot._halt_recovery_state = {}

        ok, msg = recover_halt_if_exchange_consistent(bot)

        self.assertFalse(ok)
        self.assertIn("OPEN_ORDERS", msg)

    def test_validate_entry_blocks_order_lookup_failed(self):
        from core.trade_manager import _validate_entry_preconditions

        bot = MagicMock()
        bot.stop_requested = False
        bot.shutdown_in_progress = False
        bot.active_trades = {"BTC/USDT": {"status": "ORDER_LOOKUP_FAILED"}}
        bot.confidence_stagnation_lock_active = False

        result = _validate_entry_preconditions(bot, "BTC/USDT", False)

        self.assertEqual(result, "RECOVERY_PENDING_STATE")


if __name__ == "__main__":
    unittest.main()
