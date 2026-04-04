import unittest
from threading import RLock
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from core.reconciliation import reconcile_bootstrap_state
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

    @patch("core.trade_manager.Config.PAPER_MODE", False)
    @patch("core.trade_manager.send_telegram_msg")
    @patch("core.trade_manager.shadow_logger.is_trading_halted", return_value=False)
    def test_timeout_after_pending_send_recovers_via_reconciliation(
        self, _mock_halted, _mock_tg
    ):
        bot = SimpleNamespace()
        bot.lock = RLock()
        bot.db_lock = RLock()
        bot.log = MagicMock()
        bot.integrity_lock_active = False
        bot.balance = 500.0
        bot.available_balance = 500.0
        bot.is_paused = False
        bot.circuit_breaker_active = False
        bot.cooldown_pairs = {}
        bot.active_trades = {}
        bot.instance_uuid = "test-inst"
        bot._symbol_reduced_size_mult = 1.0
        bot.market_btc_change_tf = 0.0
        bot._load_runtime_symbol_controls = lambda: {
            "blocked": set(),
            "reduced": set(),
        }
        bot._get_base_coin = lambda s: s.split("/")[0]
        bot.get_current_balance = lambda: 500.0
        bot.ws_manager = SimpleNamespace(get_l2_state=lambda _symbol: {})

        saved_states = {}

        def _save_active(symbol, state):
            saved_states[symbol] = dict(state)
            return True

        bot.brain = SimpleNamespace(
            get_genetic_params=lambda _symbol: {},
            get_stats_by_trend=lambda: {},
            save_active_trade_state=MagicMock(side_effect=_save_active),
            save_error_snapshot=MagicMock(),
            delete_active_trade_state=MagicMock(
                side_effect=lambda symbol: saved_states.pop(symbol, None)
            ),
        )
        bot.data_service = SimpleNamespace(sanitize_context=lambda ctx: ctx or {})
        bot.risk_engine = SimpleNamespace(
            calculate_position_size=lambda **kwargs: (1.0, 100.0),
            get_exit_levels=lambda **kwargs: (99.0, 120.0, "STD"),
            check_market_safety=lambda *_args, **_kwargs: (True, "OK", 80),
        )
        bot.execution = SimpleNamespace(
            exchange=object(),
            fetch_ticker=lambda _symbol: {"last": 100.0},
            set_leverage=MagicMock(),
            create_precision_order=MagicMock(
                side_effect=TimeoutError("network timeout")
            ),
            fetch_positions=lambda: [],
            fetch_open_orders=lambda: [],
            fetch_order_by_client_id=lambda _symbol, _coid: None,
        )

        result = execute_order(
            bot,
            symbol="BTC/USDT",
            side="BUY",
            price=100.0,
            atr=1.0,
            is_shadow=False,
            context={
                "atr_pct": 0.01,
                "trend": "RANGO",
                "spread": 0.0,
                "prob_final": 75.0,
            },
        )

        self.assertTrue(str(result).startswith("ERROR:"))
        self.assertIn("BTC/USDT", saved_states)
        self.assertEqual(saved_states["BTC/USDT"].get("status"), "PENDING_SEND")

        bot.active_trades = {"BTC/USDT": dict(saved_states["BTC/USDT"])}
        reconcile_bootstrap_state(bot)

        self.assertNotIn("BTC/USDT", bot.active_trades)
        bot.brain.delete_active_trade_state.assert_called_once_with("BTC/USDT")
        self.assertGreaterEqual(bot.brain.save_error_snapshot.call_count, 1)
        self.assertEqual(
            bot.brain.save_error_snapshot.call_args_list[-1][0][1],
            "LOST_IN_TRANSMISSION",
        )


if __name__ == "__main__":
    unittest.main()
