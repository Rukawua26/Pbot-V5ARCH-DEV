import unittest
from datetime import datetime
from threading import RLock
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from core.bot_wallet_sync import sync_wallet


class WalletSyncSlRecoveryTest(unittest.TestCase):
    def _base_bot(self):
        bot = SimpleNamespace()
        bot.lock = RLock()
        bot.db_lock = RLock()
        bot.log = MagicMock()
        bot.balance = 100.0
        bot.get_current_balance = lambda: 100.0
        bot.brain = SimpleNamespace(
            save_active_trade_state=MagicMock(return_value=True),
            save_error_snapshot=MagicMock(),
            delete_active_trade_state=MagicMock(),
        )
        return bot

    @patch("core.bot_wallet_sync.Config.PAPER_MODE", False)
    def test_attaches_hard_sl_when_missing_for_live_position(self):
        bot = self._base_bot()
        bot.active_trades = {
            "BTC/USDT": {
                "symbol": "BTC/USDT",
                "side": "BUY",
                "entry": 100.0,
                "amount": 1.0,
                "sl": 99.0,
                "is_shadow": False,
                "open_time": datetime.now(),
                "entry_client_order_id": "sai-v118-x",
                "sl_exchange_order_id": None,
            }
        }
        bot.execution = SimpleNamespace(
            fetch_positions=lambda: [
                {
                    "symbol": "BTC/USDT:USDT",
                    "contracts": 1.0,
                    "side": "long",
                    "entryPrice": 100.0,
                    "unrealizedPnl": 0.0,
                    "info": {},
                }
            ],
            fetch_open_orders=lambda _symbol=None: [],
            place_hard_sl=MagicMock(return_value={"id": "sl-123"}),
        )

        sync_wallet(bot)

        self.assertEqual(
            bot.active_trades["BTC/USDT"].get("sl_exchange_order_id"), "sl-123"
        )
        bot.execution.place_hard_sl.assert_called_once()

    @patch("core.bot_wallet_sync.Config.PAPER_MODE", False)
    def test_reuses_existing_exchange_stop_without_duplicating(self):
        bot = self._base_bot()
        bot.active_trades = {
            "ETH/USDT": {
                "symbol": "ETH/USDT",
                "side": "BUY",
                "entry": 2000.0,
                "amount": 0.5,
                "sl": 1980.0,
                "is_shadow": False,
                "open_time": datetime.now(),
                "sl_exchange_order_id": None,
            }
        }
        bot.execution = SimpleNamespace(
            fetch_positions=lambda: [
                {
                    "symbol": "ETH/USDT:USDT",
                    "contracts": 0.5,
                    "side": "long",
                    "entryPrice": 2000.0,
                    "unrealizedPnl": 0.0,
                    "info": {},
                }
            ],
            fetch_open_orders=lambda _symbol=None: [
                {
                    "id": "existing-sl",
                    "type": "STOP_MARKET",
                    "side": "sell",
                    "info": {"reduceOnly": True, "type": "STOP_MARKET"},
                }
            ],
            place_hard_sl=MagicMock(return_value={"id": "should-not-create"}),
        )

        sync_wallet(bot)

        self.assertEqual(
            bot.active_trades["ETH/USDT"].get("sl_exchange_order_id"), "existing-sl"
        )
        bot.execution.place_hard_sl.assert_not_called()

    @patch("core.bot_wallet_sync.Config.PAPER_MODE", False)
    def test_emergency_market_close_when_sl_is_rejected_by_gap(self):
        bot = self._base_bot()
        bot.active_trades = {
            "SOL/USDT": {
                "symbol": "SOL/USDT",
                "side": "BUY",
                "entry": 120.0,
                "amount": 1.0,
                "sl": 118.0,
                "is_shadow": False,
                "open_time": datetime.now(),
                "entry_client_order_id": "sai-v118-sol",
                "sl_exchange_order_id": None,
            }
        }
        bot.execution = SimpleNamespace(
            fetch_positions=lambda: [
                {
                    "symbol": "SOL/USDT:USDT",
                    "contracts": 1.0,
                    "side": "long",
                    "entryPrice": 120.0,
                    "unrealizedPnl": -10.0,
                    "info": {},
                }
            ],
            fetch_open_orders=lambda _symbol=None: [],
            place_hard_sl=MagicMock(return_value=None),
            close_position=MagicMock(return_value={"id": "close-1"}),
            last_hard_sl_error="Order would trigger immediately. (-2021)",
        )

        sync_wallet(bot)

        bot.execution.close_position.assert_called_once()
        self.assertNotIn("SOL/USDT", bot.active_trades)
        bot.brain.delete_active_trade_state.assert_called_once_with("SOL/USDT")


if __name__ == "__main__":
    unittest.main()
