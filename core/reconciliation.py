import hashlib
import time
from datetime import datetime

from notifier import send_telegram_msg


CLIENT_ORDER_PREFIX = "sai-v118"


def generate_client_order_id(symbol: str, side: str, signal_ts: float, instance_id: str) -> str:
    """Genera un client_order_id determinista y trazable."""
    raw = f"{signal_ts:.6f}|{symbol}|{side}|{instance_id}"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]
    return f"{CLIENT_ORDER_PREFIX}-{digest}"


def _normalize_position_symbol(pos_symbol: str) -> str:
    raw = pos_symbol.split(":")[0]
    if "/" in raw:
        return raw
    if raw.endswith("USDT") and len(raw) > 4:
        return f"{raw[:-4]}/{raw[-4:]}"
    return raw


def reconcile_bootstrap_state(bot):
    """Sincroniza estado DB <-> Exchange al arrancar para evitar huérfanos/ghosts."""
    try:
        with bot.lock:
            db_snapshot = dict(bot.active_trades)

        positions = bot.execution.fetch_positions() or []
        exchange_positions = {}
        for pos in positions:
            amount = float(pos.get("contracts") or 0)
            if abs(amount) <= 0:
                continue
            symbol = _normalize_position_symbol(pos.get("symbol", ""))
            if not symbol:
                continue
            side = "BUY"
            if pos.get("side") == "short":
                side = "SELL"
            elif pos.get("side") not in ("long", "short"):
                raw_amt = float(pos.get("info", {}).get("positionAmt", 0) or 0)
                side = "BUY" if raw_amt > 0 else "SELL"
            exchange_positions[symbol] = {
                "symbol": symbol,
                "side": side,
                "entry": float(pos.get("entryPrice") or 0),
                "amount": abs(amount),
            }

        db_symbols = {
            s for s, t in db_snapshot.items() if not (t or {}).get("is_shadow", False)
        }
        ex_symbols = set(exchange_positions.keys())

        adopted = 0
        lost = 0

        # Caso 1: posición en Exchange pero no en DB -> adopción forzosa
        missing_in_db = sorted(ex_symbols - db_symbols)
        for symbol in missing_in_db:
            info = exchange_positions[symbol]
            sl = info["entry"] * (0.995 if info["side"] == "BUY" else 1.005)
            adopted_trade = {
                "symbol": symbol,
                "side": info["side"],
                "entry": info["entry"],
                "amount": info["amount"],
                "size_usd": info["entry"] * info["amount"],
                "open_time": datetime.now(),
                "pnl": 0.0,
                "is_shadow": False,
                "simulated_real": False,
                "sector": "OTHE",
                "sl": sl,
                "tp": 0.0,
                "trailing_active": False,
                "early_be_armed": False,
                "mae_price": info["entry"],
                "mfe_price": info["entry"],
                "market_snapshot": {"is_adopted": True, "prob_final": 99.0},
                "adopted_orphan": True,
            }
            with bot.lock:
                bot.active_trades[symbol] = adopted_trade
            with bot.db_lock:
                bot.brain.save_active_trade_state(symbol, adopted_trade)

            try:
                bot.execution.place_hard_sl(symbol, info["side"], info["amount"], sl)
            except Exception as e:
                bot.log(f"⚠️ No se pudo adjuntar SL para huérfana {symbol}: {e}")

            send_telegram_msg(
                f"🚨 *POSICIÓN HUÉRFANA ADOPTADA*\n"
                f"Símbolo: {symbol}\n"
                f"Lado: {info['side']}\n"
                f"Entry: {info['entry']:.6f}\n"
                f"SL adjuntado: {sl:.6f}"
            )
            adopted += 1

        # Caso 2: en DB abierta pero no en Exchange -> LOST_IN_TRANSMISSION
        missing_in_exchange = sorted(db_symbols - ex_symbols)
        for symbol in missing_in_exchange:
            with bot.db_lock:
                bot.brain.save_error_snapshot(
                    symbol,
                    "LOST_IN_TRANSMISSION",
                    {"reconciliation_ts": datetime.now().isoformat()},
                )
                bot.brain.delete_active_trade_state(symbol)
            with bot.lock:
                if symbol in bot.active_trades:
                    del bot.active_trades[symbol]
            lost += 1

        # Integrity lock por discrepancia de balance
        exchange_balance = float(bot.get_current_balance() or 0.0)
        local_balance = float(getattr(bot, "balance", 0.0) or 0.0)
        diff_pct = 0.0
        if exchange_balance > 0:
            diff_pct = abs(local_balance - exchange_balance) / exchange_balance * 100.0

        if diff_pct > 0.1:
            bot.integrity_lock_active = True
            bot.is_paused = True
            send_telegram_msg(
                f"🛑 *INTEGRITY LOCK*\nDiscrepancia balance {diff_pct:.3f}% (>0.1%).\n"
                f"Local: ${local_balance:.2f} | Exchange: ${exchange_balance:.2f}\n"
                f"Use /rebase_capital para reanclar capital."
            )
            bot.log(
                f"🛑 INTEGRITY_LOCK activado: diff={diff_pct:.3f}% local={local_balance:.2f} ex={exchange_balance:.2f}"
            )

        if adopted or lost:
            bot.log(
                f"🔁 Reconciliación bootstrap: adoptadas={adopted} | lost_in_tx={lost}"
            )

    except Exception as e:
        bot.log(f"⚠️ Error en reconciliación de arranque: {e}")


def allocate_signal_timestamp() -> float:
    return time.time()
