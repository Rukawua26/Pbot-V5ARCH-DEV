import unittest
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


if __name__ == "__main__":
    unittest.main()
