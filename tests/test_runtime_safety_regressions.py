import unittest
from threading import RLock
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from core.trade_manager import close_trade


class RuntimeSafetyRegressionTest(unittest.TestCase):
    @patch("core.trade_exit.Config.PAPER_MODE", False)
    @patch("core.trade_exit.send_telegram_msg")
    def test_real_close_keeps_trade_when_exchange_position_not_flat(self, _mocked_tg):
        trade = {
            "symbol": "BTC/USDT",
            "side": "BUY",
            "entry": 100.0,
            "amount": 0.2,
            "is_shadow": False,
            "open_time": "2026-01-01T00:00:00+00:00",
            "market_snapshot": {},
            "status": "OPEN",
        }
        bot = SimpleNamespace(
            lock=RLock(),
            db_lock=RLock(),
            active_trades={"BTC/USDT": trade},
            recent_closed_trades=[],
            execution=SimpleNamespace(
                close_position=MagicMock(
                    return_value={"id": "exit-1", "status": "open", "exit_state": "STUCK"}
                ),
                fetch_positions=MagicMock(
                    return_value=[
                        {
                            "symbol": "BTC/USDT:USDT",
                            "contracts": 0.2,
                            "side": "long",
                            "entryPrice": 100.0,
                        }
                    ]
                ),
            ),
            brain=SimpleNamespace(
                save_active_trade_state=MagicMock(return_value=True),
                delete_active_trade_state=MagicMock(),
                log_trade=MagicMock(),
                finalize_confidence_exit_audit=MagicMock(),
                evolve_genetics=MagicMock(return_value=False),
            ),
            risk_engine=SimpleNamespace(record_trade_result=MagicMock()),
            log=MagicMock(),
            _get_market_regime=MagicMock(return_value="TEST"),
            _check_recent_mfe_health=MagicMock(),
            _update_dynamic_risk=MagicMock(),
        )

        close_trade(bot, "BTC/USDT", "TEST_EXIT", 101.0)

        self.assertIn("BTC/USDT", bot.active_trades)
        self.assertEqual(bot.active_trades["BTC/USDT"].get("status"), "EXIT_STUCK")
        self.assertTrue(bot.integrity_lock_active)
        self.assertTrue(bot.halt_system_active)
        bot.brain.delete_active_trade_state.assert_not_called()
        bot.brain.log_trade.assert_not_called()


if __name__ == "__main__":
    unittest.main()
