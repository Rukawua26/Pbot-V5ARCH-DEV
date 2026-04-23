import threading
import time
from typing import Dict, Any, Optional
from datetime import datetime, timedelta
from config import Config
from core.time_utils import utc_now


class PostMortemCleanuper:
    _pending_trades: Dict[str, Dict[str, Any]] = {}
    _running = False

    @classmethod
    def schedule_update(
        cls,
        symbol: str,
        trade_id: int,
        entry_price: float,
        side: str,
        sl_price: float,
        brain,
    ):
        cls._pending_trades[symbol] = {
            "trade_id": trade_id,
            "entry_price": entry_price,
            "side": side,
            "sl_price": sl_price,
            "brain": brain,
            "scheduled_at": time.time() + 900,
        }

        if not cls._running:
            cls._running = True
            t = threading.Thread(target=cls._worker, daemon=True)
            t.start()

    @classmethod
    def _worker(cls):
        while True:
            time.sleep(30)
            now = time.time()

            to_process = []
            for symbol, data in list(cls._pending_trades.items()):
                if data["scheduled_at"] <= now:
                    to_process.append((symbol, data))

            for symbol, data in to_process:
                try:
                    del cls._pending_trades[symbol]
                except KeyError:
                    pass

                cls._process_trade(symbol, data)

    @classmethod
    def _process_trade(cls, symbol: str, data: Dict[str, Any]):
        brain = data.get("brain")
        if not brain:
            return

        try:
            conn = brain._get_conn()
            c = conn.cursor()

            c.execute(
                "SELECT entry_price, side, exit_reason FROM trades WHERE id = ?",
                (data["trade_id"],),
            )
            row = c.fetchone()
            if not row:
                return

            entry_price, side, exit_reason = row
            if exit_reason and exit_reason != "UNKNOWN":
                return

            c.execute(
                "SELECT open_time FROM trades WHERE id = ?",
                (data["trade_id"],)
            )
            row = c.fetchone()
            if not row:
                return

            open_time_str = row[0]
            if not open_time_str:
                return

            conn.close()

        except Exception as e:
            return

    @classmethod
    def get_pending_count(cls) -> int:
        return len(cls._pending_trades)