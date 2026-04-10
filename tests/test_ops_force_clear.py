import unittest
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from core.commands.ops import (
    _handle_misc_commands,
    _handle_training_and_maintenance_commands,
)


class OpsForceClearTest(unittest.TestCase):
    @patch("core.commands.ops.send_telegram_msg")
    def test_sre_intent_reports_ratio(self, mocked_tg):
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "execution_events.jsonl"
            now = datetime.now(timezone.utc).isoformat()
            rows = [
                {"ts": now, "event": "ENTRY_ORDER_ACK", "payload": {}},
                {"ts": now, "event": "ENTRY_ORDER_ACK", "payload": {}},
                {"ts": now, "event": "INTENT_EXPIRED", "payload": {}},
            ]
            log_path.write_text(
                "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8"
            )

            bot = SimpleNamespace(
                weight_tracker=SimpleNamespace(
                    get_status=lambda: {
                        "current_weight": 120,
                        "limit": 2400,
                        "usage_pct": 5.0,
                    }
                )
            )

            with patch("core.commands.ops.Path", side_effect=lambda _p: log_path):
                handled = _handle_misc_commands(bot, "/sre_intent")

            self.assertTrue(handled)
            mocked_tg.assert_called_once()
            msg = mocked_tg.call_args[0][0]
            self.assertIn("RATIO=50.00%", msg)
            self.assertIn("120/2400", msg)

    @patch("core.commands.ops.send_telegram_msg")
    def test_force_clear_removes_state_when_no_exchange_evidence(self, mocked_tg):
        bot = SimpleNamespace(
            lock=RLock(),
            db_lock=RLock(),
            active_trades={
                "BTC/USDT": {
                    "symbol": "BTC/USDT",
                    "status": "PENDING_SEND",
                    "entry_client_order_id": "sai-v118-abc",
                }
            },
            execution=SimpleNamespace(
                fetch_open_orders=lambda _symbol: [],
                fetch_order_by_client_id=lambda _symbol, _coid: None,
                fetch_positions=lambda: [],
            ),
            brain=SimpleNamespace(delete_active_trade_state=MagicMock()),
        )

        handled = _handle_training_and_maintenance_commands(
            bot, "/force_clear BTC/USDT"
        )

        self.assertTrue(handled)
        self.assertNotIn("BTC/USDT", bot.active_trades)
        bot.brain.delete_active_trade_state.assert_called_once_with("BTC/USDT")
        mocked_tg.assert_called()

    @patch("core.commands.ops.send_telegram_msg")
    def test_force_clear_does_not_remove_state_when_exchange_has_position(
        self, mocked_tg
    ):
        bot = SimpleNamespace(
            lock=RLock(),
            db_lock=RLock(),
            active_trades={
                "BTC/USDT": {
                    "symbol": "BTC/USDT",
                    "status": "PENDING_SEND",
                    "entry_client_order_id": "sai-v118-abc",
                }
            },
            execution=SimpleNamespace(
                fetch_open_orders=lambda _symbol: [],
                fetch_order_by_client_id=lambda _symbol, _coid: None,
                fetch_positions=lambda: [{"symbol": "BTC/USDT:USDT", "contracts": 0.1}],
            ),
            brain=SimpleNamespace(delete_active_trade_state=MagicMock()),
        )

        handled = _handle_training_and_maintenance_commands(
            bot, "/force_clear BTC/USDT"
        )

        self.assertTrue(handled)
        self.assertIn("BTC/USDT", bot.active_trades)
        bot.brain.delete_active_trade_state.assert_not_called()
        mocked_tg.assert_called()


if __name__ == "__main__":
    unittest.main()
