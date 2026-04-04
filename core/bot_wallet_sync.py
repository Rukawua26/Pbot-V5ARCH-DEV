from datetime import datetime

from config import Config
from core.reconciliation import generate_child_client_order_id


def _bool_reduce_only(order: dict) -> bool:
    info = order.get("info") or {}
    raw = info.get("reduceOnly", order.get("reduceOnly", False))
    if isinstance(raw, bool):
        return raw
    return str(raw).lower() in {"true", "1", "yes"}


def _is_protective_stop_for_trade(order: dict, trade: dict) -> bool:
    order_type = str(
        order.get("type") or (order.get("info") or {}).get("type") or ""
    ).upper()
    if "STOP" not in order_type:
        return False
    if not _bool_reduce_only(order):
        return False
    order_side = str(order.get("side") or "").upper()
    trade_side = str(trade.get("side") or "").upper()
    if trade_side == "BUY":
        return order_side == "SELL"
    if trade_side == "SELL":
        return order_side == "BUY"
    return False


def _find_existing_hard_sl_order(bot, symbol: str, trade: dict):
    fetch_open_orders = getattr(bot.execution, "fetch_open_orders", None)
    if not callable(fetch_open_orders):
        return None
    try:
        open_orders = fetch_open_orders(symbol) or []
        orders_iter = open_orders if isinstance(open_orders, (list, tuple)) else []
        for order in orders_iter:
            if _is_protective_stop_for_trade(order, trade):
                return order
    except Exception as error:
        bot.log(f"⚠️ No se pudo inspeccionar open orders de {symbol} para SL: {error}")
    return None


def _is_immediate_trigger_rejection(error_text: str) -> bool:
    msg = str(error_text or "").lower()
    return (
        "trigger immediately" in msg
        or "would immediately trigger" in msg
        or "order would trigger" in msg
        or "-2021" in msg
    )


def _emergency_market_close_unprotected(
    bot, symbol: str, trade: dict, amount: float, sl_error: str
):
    trade["status"] = "CLOSING_INITIATED"
    trade["closing_in_progress"] = True
    with bot.db_lock:
        bot.brain.save_active_trade_state(symbol, trade)

    try:
        bot.execution.close_position(symbol, str(trade.get("side") or "BUY"), amount)
        with bot.db_lock:
            bot.brain.save_error_snapshot(
                symbol,
                "EMERGENCY_CLOSE_NO_VALID_SL",
                {"sl_error": str(sl_error)[:200]},
            )
            bot.brain.delete_active_trade_state(symbol)
        with bot.lock:
            if symbol in bot.active_trades:
                del bot.active_trades[symbol]
        bot.log(
            f"🧯 EMERGENCY CLOSE {symbol}: SL inválido por gap, cierre MARKET ejecutado"
        )
    except Exception as close_error:
        bot.log(
            f"☢️ FALLO CRÍTICO {symbol}: no se pudo adjuntar SL ni cerrar por mercado: {close_error}"
        )


def _ensure_hard_sl_attached(bot, symbol: str, trade: dict, info: dict):
    if trade.get("is_shadow") or Config.PAPER_MODE:
        return False
    if trade.get("sl_exchange_order_id"):
        return False

    existing_sl = _find_existing_hard_sl_order(bot, symbol, trade)
    if existing_sl:
        trade["sl_exchange_order_id"] = existing_sl.get("id")
        trade["status"] = "OPEN"
        with bot.db_lock:
            bot.brain.save_active_trade_state(symbol, trade)
        bot.log(f"🛡️ SL existente detectado para {symbol}: {existing_sl.get('id')}")
        return False

    sl_price = float(trade.get("sl") or 0.0)
    if sl_price <= 0:
        entry = float(trade.get("entry") or info.get("entry") or 0.0)
        side = str(trade.get("side") or info.get("side") or "BUY")
        sl_price = entry * (0.995 if side == "BUY" else 1.005)
        trade["sl"] = sl_price

    amount = float(info.get("amount") or trade.get("amount") or 0.0)
    if amount <= 0:
        return False

    entry_coid = str(trade.get("entry_client_order_id") or "")
    sl_coid = str(trade.get("sl_client_order_id") or "")
    if not sl_coid and entry_coid:
        sl_coid = generate_child_client_order_id(entry_coid, "SL")
        trade["sl_client_order_id"] = sl_coid

    sl_order = bot.execution.place_hard_sl(
        symbol,
        str(trade.get("side") or info.get("side") or "BUY"),
        amount,
        sl_price,
        client_order_id=sl_coid or None,
    )
    if sl_order:
        trade["sl_exchange_order_id"] = sl_order.get("id")
        trade["status"] = "OPEN"
        with bot.db_lock:
            bot.brain.save_active_trade_state(symbol, trade)
        bot.log(f"🛡️ HARD SL recuperado para {symbol}: {sl_order.get('id')}")
    else:
        sl_error = str(getattr(bot.execution, "last_hard_sl_error", "") or "")
        if _is_immediate_trigger_rejection(sl_error):
            _emergency_market_close_unprotected(bot, symbol, trade, amount, sl_error)
            return True
        bot.log(f"⚠️ Riesgo crítico: {symbol} sigue sin HARD SL en exchange")
    return False


def sync_wallet(bot):
    try:
        # Usamos fetch_positions para obtener datos precisos y unificados
        # [FIX] Race Condition: Snapshot de active_trades antes de la llamada de red
        with bot.lock:
            active_trades_snapshot = bot.active_trades.copy()

        positions = bot.execution.fetch_positions()
        real_active_on_binance = {}

        # PROTECCIÓN DE INTEGRIDAD: Si Binance devuelve lista vacía pero tenemos trades REALES activos,
        # podría ser un error de API. Verificamos balance para confirmar que no es un error de conexión.
        if not positions and any(
            not trade.get("is_shadow") for trade in active_trades_snapshot.values()
        ):
            if bot.get_current_balance() == 0:
                return  # Si balance es 0 y pos es 0, ok. Si no, sospechoso.

        for pos in positions:
            amount = float(pos.get("contracts") or 0)
            if abs(amount) > 0:
                # Determinación robusta del lado (Long/Short)
                side = "BUY"
                if pos.get("side") == "short":
                    side = "SELL"
                elif pos.get("side") == "long":
                    side = "BUY"
                else:
                    # Fallback a raw info si ccxt no normalizó el side
                    raw_amt = float(pos["info"].get("positionAmt", 0))
                    side = "BUY" if raw_amt > 0 else "SELL"

                # Normalización robusta para evitar purgas erróneas
                raw_symbol = pos["symbol"].split(":")[0]
                # FIX: Usamos slicing negativo ([:-4]) para no romper BTC (3 letras)
                symbol = (
                    raw_symbol
                    if "/" in raw_symbol
                    else (
                        f"{raw_symbol[:-4]}/{raw_symbol[-4:]}"
                        if raw_symbol.endswith("USDT")
                        else raw_symbol
                    )
                )
                if symbol == "WLF I/USDT":
                    symbol = "WLFI/USDT"  # Corrección específica

                real_active_on_binance[symbol] = {
                    "amount": abs(amount),
                    "side": side,
                    "entry": float(pos.get("entryPrice") or 0),
                    "pnl": float(pos.get("unrealizedPnl") or 0),
                }

        # LOG DE DIAGNÓSTICO: Ver qué detecta Binance
        if real_active_on_binance:
            bot.log(
                f"🔍 Wallet Sync: Binance reporta {list(real_active_on_binance.keys())}"
            )

        with bot.lock:
            emergency_closed_symbols = set()
            # Aseguramos actualización de saldo (ATÓMICO v106.0)
            bot.balance = bot.get_current_balance()

            # A. ACTUALIZACIÓN DE PRECIOS REALES (Corrige el PnL)
            for symbol, info in real_active_on_binance.items():
                if symbol in bot.active_trades and not bot.active_trades[symbol].get(
                    "is_shadow"
                ):
                    # Sincronizamos el precio de entrada del bot con el de Binance
                    # Validamos que el precio sea > 0 para evitar errores de API
                    if (
                        info["entry"] > 0
                        and bot.active_trades[symbol]["entry"] != info["entry"]
                    ):
                        bot.log(
                            f"⚖️ Sincronizando precio {symbol}: {bot.active_trades[symbol]['entry']} -> {info['entry']}"
                        )
                        bot.active_trades[symbol]["entry"] = info["entry"]
                        bot.active_trades[symbol]["amount"] = info["amount"]
                        bot.active_trades[symbol]["size_usd"] = (
                            info["entry"] * info["amount"]
                        )

                    emergency_closed = _ensure_hard_sl_attached(
                        bot, symbol, bot.active_trades[symbol], info
                    )
                    if emergency_closed:
                        emergency_closed_symbols.add(symbol)

            # B. PURGAR trades huerfanos (No están en Binance pero sí en el bot)
            for symbol in list(bot.active_trades.keys()):
                trade = bot.active_trades[symbol]
                if (
                    not trade.get("is_shadow")
                    and symbol not in real_active_on_binance
                    and not Config.PAPER_MODE
                ):
                    # PROTECCIÓN DE LATENCIA: No purgar si el trade tiene menos de 60 segundos
                    open_time = trade.get("open_time")
                    if isinstance(open_time, str):
                        open_time = datetime.fromisoformat(open_time)
                    if (datetime.now() - open_time).total_seconds() < 120:
                        continue

                    bot.log(f"🧹 Purgando manual: {symbol}")
                    del bot.active_trades[symbol]
                    with bot.db_lock:
                        bot.brain.delete_active_trade_state(symbol)

            # C. ADOPTAR trades nuevos (Si abres algo manual en Binance)
            for symbol, info in real_active_on_binance.items():
                if symbol in emergency_closed_symbols:
                    continue
                if symbol not in bot.active_trades:
                    bot.log(
                        f"📥 CARTERA: Detectado nuevo trade en Binance: {symbol}. Sincronizando..."
                    )
                    base = symbol.split("/")[0]
                    sector = next(
                        (
                            key
                            for key, values in Config.SECTORS.items()
                            if any(item.lower() in base.lower() for item in values)
                        ),
                        "OTHE",
                    )
                    sl = (
                        info["entry"] * 0.95
                        if info["side"] == "BUY"
                        else info["entry"] * 1.05
                    )

                    bot.active_trades[symbol] = {
                        "symbol": symbol,
                        "side": info["side"],
                        "entry": info["entry"],
                        "amount": info["amount"],
                        "size_usd": info["entry"] * info["amount"],
                        "open_time": datetime.now(),
                        "pnl": 0.0,
                        "is_shadow": False,
                        "simulated_real": False,
                        "sector": sector,
                        "sl": sl,
                        "tp": 0.0,
                        "trailing_active": False,
                        "early_be_armed": False,
                        "mae_price": info["entry"],
                        "mfe_price": info["entry"],
                        "market_snapshot": {
                            "prob_final": 99.0,
                            "votos": {"G": 99.0},
                            "is_adopted": True,
                        },
                        "status": "OPEN",
                        "sl_exchange_order_id": None,
                    }
                    with bot.db_lock:
                        bot.brain.save_active_trade_state(
                            symbol, bot.active_trades[symbol]
                        )
                    _ensure_hard_sl_attached(
                        bot, symbol, bot.active_trades[symbol], info
                    )
    except Exception as error:
        bot.log(f"⚠️ Error Sync: {error}")
