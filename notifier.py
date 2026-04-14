"""
SNIPER AI - NOTIFIER MODULE v118.2
===================================
Módulo de notificaciones para Telegram con reintentos y cola.
"""

import requests
import time
import threading
import queue
from enum import Enum
from config import Config


def _sanitize_telegram_error(error) -> str:
    msg = str(error)
    token = str(getattr(Config, "TELEGRAM_TOKEN", "") or "")
    if token:
        msg = msg.replace(token, "***")
    return msg


class Priority(Enum):
    DEBUG = 1
    INFO = 2
    WARNING = 3
    ERROR = 4
    CRITICAL = 5


class NotificationQueue:
    """Cola de notificaciones con rate limiting."""

    def __init__(self, max_retries=3, rate_limit_seconds=1):
        self.queue = queue.Queue()
        self.max_retries = max_retries
        self.rate_limit = rate_limit_seconds
        self.last_sent = 0
        self.running = True
        self.thread = threading.Thread(target=self._worker, daemon=True)
        self.thread.start()

    def _worker(self):
        while self.running:
            try:
                item = self.queue.get(timeout=0.5)
                if item is None:
                    break
                self._send_with_retry(*item)
            except queue.Empty:
                continue

    def _send_with_retry(self, url, payload, retries):
        for attempt in range(retries):
            try:
                # Rate limiting
                now = time.time()
                elapsed = now - self.last_sent
                if elapsed < self.rate_limit:
                    time.sleep(self.rate_limit - elapsed)

                response = requests.post(url, json=payload, timeout=10)
                if response.status_code == 200:
                    self.last_sent = time.time()
                    return True
                elif response.status_code == 429:  # Rate limited
                    time.sleep(2**attempt)  # Exponential backoff
                else:
                    break
            except Exception as e:
                if attempt == retries - 1:
                    print(
                        f"⚠️ Telegram send failed after {retries} attempts: {_sanitize_telegram_error(e)}"
                    )
        return False

    def send(self, message, priority=Priority.INFO):
        if not Config.TELEGRAM_TOKEN or not Config.TELEGRAM_CHAT_ID:
            return

        url = f"https://api.telegram.org/bot{Config.TELEGRAM_TOKEN}/sendMessage"
        payload = {
            "chat_id": Config.TELEGRAM_CHAT_ID,
            "text": message,
            "parse_mode": "Markdown",
        }
        self.queue.put((url, payload, self.max_retries))

    def stop(self):
        self.running = False
        self.queue.put(None)
        self.thread.join()


# Instancia global
_notifier_queue = None


def get_queue():
    global _notifier_queue
    if _notifier_queue is None:
        _notifier_queue = NotificationQueue()
    return _notifier_queue


def send_telegram_msg(message, priority=Priority.INFO):
    """Envía un mensaje a Telegram con cola y reintentos."""
    try:
        get_queue().send(message, priority)
    except Exception as e:
        print(f"⚠️ Telegram Error: {_sanitize_telegram_error(e)}")


def send_telegram_photo(caption, photo_buffer):
    """Envía una foto a Telegram."""
    try:
        if not Config.TELEGRAM_TOKEN or not Config.TELEGRAM_CHAT_ID:
            return

        url = f"https://api.telegram.org/bot{Config.TELEGRAM_TOKEN}/sendPhoto"
        files = {"photo": ("sniper.png", photo_buffer, "image/png")}
        data = {
            "chat_id": Config.TELEGRAM_CHAT_ID,
            "caption": caption,
            "parse_mode": "Markdown",
        }
        response = requests.post(url, data=data, files=files, timeout=15)

        if response.status_code != 200:
            print(f"⚠️ Telegram Photo Error: {response.text}")
    except Exception as e:
        print(f"⚠️ Telegram Photo Error: {_sanitize_telegram_error(e)}")


def notify_trade(symbol, side, pnl_percent, is_shadow):
    """Notifica un trade cerrado."""
    emoji = "🧪" if is_shadow else "🔥"
    sign = "+" if pnl_percent >= 0 else ""
    mode = "SHADOW" if is_shadow else "REAL"
    winner = "✅ WINNER" if pnl_percent > 0 else "❌ STOP LOSS"

    message = f"""
{emoji} *{winner}*
━━━━━━━━━━━━━━━━━━━━
🔹 *Par:* {symbol}
🔹 *Lado:* {side}
🔹 *Modo:* {mode}
📈 *PnL:* {sign}{pnl_percent:.2f}%
━━━━━━━━━━━━━━━━━━━━
"""
    send_telegram_msg(message, Priority.INFO if pnl_percent > 0 else Priority.WARNING)


def notify_panic(reason):
    """Notifica estado de pánico."""
    message = f"""
🚨 *SNIPER PANIC MODE*

*Reason:* {reason}
*Action:* Operaciones reales pausadas
"""
    send_telegram_msg(message, Priority.CRITICAL)


def notify_daily_summary(wins, losses, pnl_percent, target_hit):
    """Resumen diario."""
    total = wins + losses
    wr = (wins / total * 100) if total > 0 else 0

    status = "🎯 *META ALCANZADA*" if target_hit else "⏳ *EN PROGRESO*"

    message = f"""
📊 *DAILY SUMMARY*

*Trades:* {total} ({wins}W / {losses}L)
*Win Rate:* {wr:.1f}%
*PnL:* {pnl_percent:+.2f}%
{status}
"""
    send_telegram_msg(message, Priority.INFO)
