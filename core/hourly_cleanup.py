import time
from threading import Thread
from typing import List, Dict, Any, Optional

from config import Config


class HourlyPostMortemCleanuper:
    def __init__(self, bot):
        self.bot = bot
        self._thread: Optional[Thread] = None
        self._running = False

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def _run_loop(self):
        while self._running:
            time.sleep(3600)
            if not self._running:
                break
            self._cleanup()

    def _cleanup(self):
        try:
            self._process_unclosed_trades()
        except Exception as e:
            pass

    def _process_unclosed_trades(self):
        brain = getattr(self.bot, "brain", None)
        if not brain:
            return

        try:
            conn = brain._get_conn()
            c = conn.cursor()

            c.execute(
                """
                SELECT id, symbol, entry_price, side, exit_reason
                FROM trades
                WHERE exit_reason = 'UNKNOWN'
                AND timestamp < datetime('now', '-1 hour')
                LIMIT 10
                """
            )
            rows = c.fetchall()

            for row in rows:
                trade_id, symbol, entry_price, side, exit_reason = row

                if not exit_reason or exit_reason == "UNKNOWN":
                    c.execute(
                        "UPDATE trades SET exit_reason = 'TIMEOUT_STAGNATION' WHERE id = ?",
                        (trade_id,),
                    )

            conn.commit()
            conn.close()

        except Exception:
            pass

    def stop(self):
        self._running = False