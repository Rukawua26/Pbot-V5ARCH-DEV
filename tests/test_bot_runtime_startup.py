import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from core.bot_runtime import run_initial_load


class BotRuntimeStartupTest(unittest.TestCase):
    @patch("core.bot_runtime.reconcile_bootstrap_state")
    def test_initial_load_preserves_bootstrap_error_for_main_thread(self, _reconcile):
        error = RuntimeError("Credenciales/permisos Binance inválidos")
        bot = SimpleNamespace(
            connect=MagicMock(side_effect=error),
            acquire_targets=MagicMock(),
            _load_ai_restrictions=MagicMock(),
            log=MagicMock(),
            is_running=True,
            init_complete=MagicMock(),
        )

        run_initial_load(bot, dashboard_module=None)

        self.assertIs(bot.startup_error, error)
        self.assertFalse(bot.is_running)
        bot.init_complete.set.assert_called_once()


if __name__ == "__main__":
    unittest.main()
