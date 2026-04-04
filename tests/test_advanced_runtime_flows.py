import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from core.bot_runtime_ops import check_instinctive_safety
from core.trade_manager import execute_order


class AdvancedRuntimeFlowsTest(unittest.TestCase):
    @patch("core.trade_manager.shadow_logger.is_trading_halted", return_value=True)
    def test_execute_order_blocks_real_when_shadow_logger_halted(self, _mock_halted):
        bot = SimpleNamespace(log=MagicMock())

        result = execute_order(
            bot,
            symbol="BTC/USDT",
            side="BUY",
            price=100.0,
            atr=1.0,
            is_shadow=False,
            context={},
        )

        self.assertEqual(result, "TRADING_HALTED_DB_ERROR")
        bot.log.assert_called_once()

    @patch("core.trade_manager.shadow_logger.is_trading_halted", return_value=False)
    def test_execute_order_blocks_symbol_from_tactical_matrix(self, _mock_halted):
        bot = SimpleNamespace(
            log=MagicMock(),
            _load_runtime_symbol_controls=lambda: {"blocked": {"BTC"}},
        )

        result = execute_order(
            bot,
            symbol="BTC/USDT",
            side="BUY",
            price=100.0,
            atr=1.0,
            is_shadow=False,
            context={},
        )

        self.assertEqual(result, "SYMBOL_BLOCKED_MATRIX")

    @patch("core.trade_manager.shadow_logger.is_trading_halted", return_value=False)
    def test_execute_order_rejects_when_balance_below_min_notional(self, _mock_halted):
        bot = SimpleNamespace(
            log=MagicMock(),
            _load_runtime_symbol_controls=lambda: {
                "blocked": set(),
                "preferred": set(),
                "reduced": set(),
            },
            balance=0.0,
        )

        result = execute_order(
            bot,
            symbol="ETH/USDT",
            side="BUY",
            price=100.0,
            atr=1.0,
            is_shadow=False,
            context={},
        )

        self.assertEqual(result, "INSUFFICIENT_BALANCE_MIN_NOTIONAL")

    def test_instinctive_safety_forces_shadow_on_extreme_volatility(self):
        bot = SimpleNamespace(log=MagicMock())

        decision = check_instinctive_safety(bot, "SOL/USDT", {"atr_pct": 0.06})

        self.assertEqual(decision, "FORCE_SHADOW")
        bot.log.assert_called_once()

    def test_instinctive_safety_returns_ok_on_normal_context(self):
        bot = SimpleNamespace(log=MagicMock())

        decision = check_instinctive_safety(bot, "SOL/USDT", {"atr_pct": 0.01})

        self.assertEqual(decision, "OK")


if __name__ == "__main__":
    unittest.main()
