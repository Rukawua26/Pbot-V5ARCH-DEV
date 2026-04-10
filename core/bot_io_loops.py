import json
import random
import time

import requests

from config import Config
from core.time_utils import monotonic_now, parse_datetime_utc, utc_now


def websocket_monitor(bot):
    """Hilo dedicado a escuchar precios en tiempo real vía Websockets (v106.5)."""
    try:
        import websocket
    except ImportError:
        bot.log("⚠️ 'websocket-client' no instalado. Usando polling REST (más lento).")
        return

    def on_message(ws, message):
        try:
            data = json.loads(message)
            # Formato !ticker@arr: [{'s': 'BTCUSDT', 'c': '60000.00'}, ...]
            with bot.price_lock:
                for ticker in data:
                    bot.live_prices[ticker["s"]] = ticker["c"]
        except (KeyError, ValueError, json.JSONDecodeError):
            return  # Mensaje malformado, ignorar
        except Exception as error:
            bot.log(f"⚠️ Error procesando mensaje WS: {error}")

    is_reconnecting = False
    reconnect_delay = 5.0
    while bot.is_running:
        try:
            if is_reconnecting:
                bot.log(
                    "⚡ WEBSOCKET: Reconectado exitosamente. Precios en tiempo real restaurados."
                )
                is_reconnecting = False
                reconnect_delay = 5.0

            websocket.enableTrace(False)
            ws = websocket.WebSocketApp(
                "wss://fstream.binance.com/ws/!ticker@arr", on_message=on_message
            )
            ws.run_forever()
            if bot.is_running:
                is_reconnecting = True
                wait_s = reconnect_delay + random.uniform(0.0, 1.0)
                if reconnect_delay <= 5.1:
                    bot.log(
                        f"🔌 WEBSOCKET: Conexión cerrada. Reintentando en {wait_s:.1f}s..."
                    )
                time.sleep(wait_s)
                reconnect_delay = min(reconnect_delay * 1.8, 60.0)
        except Exception as error:
            if not is_reconnecting:
                bot.log(f"🔌 WEBSOCKET: Desconectado. Reintentando... (Error: {error})")
            is_reconnecting = True
            wait_s = reconnect_delay + random.uniform(0.0, 1.0)
            time.sleep(wait_s)
            reconnect_delay = min(reconnect_delay * 1.8, 60.0)


def telegram_listener(bot):
    """Escucha comandos como /report o /train desde Telegram."""
    last_update_id = 0
    backoff_seconds = 5
    while bot.is_running:
        try:
            if not Config.TELEGRAM_TOKEN:
                time.sleep(10)
                continue

            url = f"https://api.telegram.org/bot{Config.TELEGRAM_TOKEN}/getUpdates?offset={last_update_id}&timeout=30"
            response = requests.get(url, timeout=35).json()

            for update in response.get("result", []):
                last_update_id = update["update_id"] + 1
                text = update.get("message", {}).get("text", "").strip()

                # Lógica centralizada
                bot.handle_command(text)

            backoff_seconds = 5

        except Exception as error:
            now_ts = monotonic_now()
            if now_ts - float(getattr(bot, "_telegram_last_err_log", 0.0)) > 120:
                bot._telegram_last_err_log = now_ts
                bot.log(f"Telegram Error: {error}")
            time.sleep(backoff_seconds)
            backoff_seconds = min(backoff_seconds * 2, 60)
            continue
        time.sleep(1)


def perform_post_mortem(bot):
    """Analiza trades cerrados hace 15m para etiquetar falsos positivos."""
    try:
        with bot.db_lock:
            pending = bot.brain.get_trades_pending_post_mortem()
        now = utc_now()
        for trade in pending:
            try:
                close_time = parse_datetime_utc(trade["timestamp"])
                if (now - close_time).total_seconds() < 900:
                    continue  # Esperar 15 min

                ticker = bot.execution.fetch_ticker(trade["symbol"])
                curr_price = float(ticker["last"])

                verdict = "NEUTRAL"
                if trade["pnl_percent"] < 0:
                    # Si perdimos y el precio siguió en contra -> La señal fue un Falso Positivo
                    if trade["side"] == "BUY" and curr_price < trade["exit_price"]:
                        verdict = "FALSE_POSITIVE"
                    elif trade["side"] == "SELL" and curr_price > trade["exit_price"]:
                        verdict = "FALSE_POSITIVE"
                    else:
                        verdict = "BAD_TIMING"

                with bot.db_lock:
                    bot.brain.update_post_mortem(
                        trade["id"], {"price_15m": curr_price, "verdict": verdict}
                    )
                if verdict == "FALSE_POSITIVE":
                    bot.log(
                        f"💀 Post-Mortem {trade['symbol']}: Confirmado Falso Positivo. Aprendiendo..."
                    )
            except Exception:
                continue
    except Exception as error:
        bot.log(f"⚠️ Error Post-Mortem: {error}")
