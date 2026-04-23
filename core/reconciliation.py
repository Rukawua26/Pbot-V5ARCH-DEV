import hashlib

from config import Config
from core.execution_telemetry import append_execution_event
from notifier import send_telegram_msg
from core.time_utils import parse_datetime_utc, utc_now, utc_now_iso


CLIENT_ORDER_PREFIX = "sai-v118"
PENDING_SEND_STALE_SECONDS = 90


def generate_client_order_id(
    symbol: str, side: str, signal_ts: float, instance_id: str
) -> str:
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


def _extract_client_order_id(order: dict) -> str:
    if not isinstance(order, dict):
        return ""
    direct = order.get("clientOrderId")
    if direct:
        return str(direct)
    info = order.get("info") or {}
    if not isinstance(info, dict):
        return ""
    return str(info.get("clientOrderId") or info.get("origClientOrderId") or "")


def _build_open_order_index(open_orders):
    by_client_order_id = {}
    by_symbol = {}
    for order in open_orders or []:
        if not isinstance(order, dict):
            continue
        symbol = _normalize_position_symbol(order.get("symbol", ""))
        if symbol:
            by_symbol.setdefault(symbol, []).append(order)
        coid = _extract_client_order_id(order)
        if coid:
            by_client_order_id[coid] = order
    return by_client_order_id, by_symbol


def _normalize_order_status(raw_status: str) -> str:
    status = str(raw_status or "").upper()
    if status in {"NEW", "OPEN", "PARTIALLY_FILLED"}:
        return "OPEN"
    if status in {"FILLED", "CLOSED"}:
        return "FILLED"
    if status in {"CANCELED", "CANCELLED", "REJECTED", "EXPIRED"}:
        return "CANCELED"
    return status or "UNKNOWN"


def generate_child_client_order_id(entry_client_order_id: str, leg: str) -> str:
    leg_safe = str(leg or "LEG").upper()[:6]
    digest = hashlib.sha256(
        f"{entry_client_order_id}|{leg_safe}".encode("utf-8")
    ).hexdigest()[:10]
    return f"{entry_client_order_id}-{leg_safe}-{digest}"


def reconcile_bootstrap_state(bot):
    """Sincroniza estado DB <-> Exchange al arrancar para evitar huérfanos/ghosts."""
    try:
        with bot.lock:
            db_snapshot = dict(bot.active_trades)

        try:
            positions = bot.execution.fetch_positions() or []
        except Exception as error:
            bot.log(
                f"⚠️ Reconciliación abortada: no se pudieron consultar posiciones del exchange: {error}"
            )
            return
        open_orders = []
        fetch_open_orders = getattr(bot.execution, "fetch_open_orders", None)
        if callable(fetch_open_orders):
            try:
                open_orders = fetch_open_orders() or []
            except Exception as error:
                bot.log(
                    f"⚠️ No se pudieron consultar open orders en reconciliación: {error}"
                )
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

        open_orders_by_coid, open_orders_by_symbol = _build_open_order_index(
            open_orders
        )

        db_symbols = {
            s for s, t in db_snapshot.items() if not (t or {}).get("is_shadow", False)
        }
        position_symbols = set(exchange_positions.keys())
        order_symbols = set(open_orders_by_symbol.keys())
        ex_symbols = position_symbols | order_symbols

        adopted = 0
        lost = 0
        pending_open = 0
        intent_expired = 0

        # Caso 1: posición en Exchange pero no en DB -> adopción forzosa
        missing_in_db = sorted(position_symbols - db_symbols)
        for symbol in missing_in_db:
            info = exchange_positions[symbol]
            sl = info["entry"] * (0.995 if info["side"] == "BUY" else 1.005)
            adopted_trade = {
                "symbol": symbol,
                "side": info["side"],
                "entry": info["entry"],
                "amount": info["amount"],
                "size_usd": info["entry"] * info["amount"],
                "open_time": utc_now_iso(),
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
                sl_order = bot.execution.place_hard_sl(
                    symbol, info["side"], info["amount"], sl
                )
                if sl_order:
                    with bot.lock:
                        bot.active_trades[symbol]["sl_exchange_order_id"] = (
                            sl_order.get("id")
                        )
                    with bot.db_lock:
                        bot.brain.save_active_trade_state(
                            symbol, bot.active_trades[symbol]
                        )
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
        safe_pending_symbols = set()
        expired_symbols = set()
        for symbol in sorted(db_symbols - position_symbols):
            state = db_snapshot.get(symbol) or {}
            if not isinstance(state, dict):
                continue
            entry_coid = str(state.get("entry_client_order_id") or "")
            if not entry_coid:
                continue

            status = str(state.get("status") or "").upper()
            intent_created = state.get("intent_created_at_utc") or state.get(
                "open_time"
            )
            intent_age_seconds = None
            if intent_created:
                try:
                    intent_age_seconds = max(
                        0.0,
                        (
                            utc_now() - parse_datetime_utc(intent_created)
                        ).total_seconds(),
                    )
                except Exception:
                    intent_age_seconds = None

            exchange_order = None
            fetch_by_coid = getattr(bot.execution, "fetch_order_by_client_id", None)
            if callable(fetch_by_coid):
                try:
                    exchange_order = fetch_by_coid(symbol, entry_coid)
                except Exception as error:
                    bot.log(
                        f"⚠️ Consulta order-by-client-id falló {symbol}/{entry_coid}: {error}"
                    )

            if exchange_order is None and entry_coid in open_orders_by_coid:
                exchange_order = open_orders_by_coid[entry_coid]

            state["intent_last_check_at_utc"] = utc_now_iso()
            state["intent_check_attempts"] = (
                int(state.get("intent_check_attempts", 0) or 0) + 1
            )

            if exchange_order is None:
                if status == "PENDING_SEND":
                    stale_limit = float(
                        getattr(
                            bot,
                            "pending_send_stale_seconds",
                            PENDING_SEND_STALE_SECONDS,
                        )
                    )
                    age = intent_age_seconds if intent_age_seconds is not None else 0.0

                    if age < stale_limit:
                        state.setdefault("intent_created_at_utc", utc_now_iso())
                        safe_pending_symbols.add(symbol)
                        with bot.lock:
                            bot.active_trades[symbol] = state
                        with bot.db_lock:
                            bot.brain.save_active_trade_state(symbol, state)
                        continue

                    with bot.db_lock:
                        bot.brain.save_error_snapshot(
                            symbol,
                            "INTENT_EXPIRED",
                            {
                                "entry_client_order_id": entry_coid,
                                "age_seconds": round(float(age), 3),
                                "stale_limit_seconds": stale_limit,
                                "reconciliation_ts": utc_now_iso(),
                            },
                        )
                        bot.brain.delete_active_trade_state(symbol)
                    append_execution_event(
                        bot,
                        "INTENT_EXPIRED",
                        {
                            "symbol": symbol,
                            "entry_client_order_id": entry_coid,
                            "age_seconds": round(float(age), 3),
                            "stale_limit_seconds": stale_limit,
                        },
                    )
                    with bot.lock:
                        bot.active_trades.pop(symbol, None)
                    intent_expired += 1
                    expired_symbols.add(symbol)
                continue
            if not isinstance(exchange_order, dict):
                continue

            status_raw = str(exchange_order.get("status") or "")
            normalized_status = _normalize_order_status(status_raw)
            if normalized_status == "OPEN":
                state["status"] = "PENDING_EXCHANGE_OPEN"
                state["exchange_open_order_id"] = exchange_order.get("id")
                state["exchange_open_order_status"] = exchange_order.get("status")
                state["reconciled_at"] = utc_now_iso()
                state["intent_created_at_utc"] = (
                    state.get("intent_created_at_utc") or utc_now_iso()
                )
                with bot.lock:
                    bot.active_trades[symbol] = state
                with bot.db_lock:
                    bot.brain.save_active_trade_state(symbol, state)
                safe_pending_symbols.add(symbol)
                pending_open += 1
            elif normalized_status == "FILLED":
                state["status"] = "ENTRY_FILLED_AWAITING_POSITION_SYNC"
                state["exchange_entry_order_id"] = exchange_order.get("id")
                state["exchange_open_order_status"] = exchange_order.get("status")
                state["reconciled_at"] = utc_now_iso()
                state["intent_created_at_utc"] = (
                    state.get("intent_created_at_utc") or utc_now_iso()
                )
                with bot.lock:
                    bot.active_trades[symbol] = state
                with bot.db_lock:
                    bot.brain.save_active_trade_state(symbol, state)
                safe_pending_symbols.add(symbol)

        missing_in_exchange = sorted(
            ((db_symbols - ex_symbols) - safe_pending_symbols) - expired_symbols
        )
        for symbol in missing_in_exchange:
            with bot.db_lock:
                bot.brain.save_error_snapshot(
                    symbol,
                    "LOST_IN_TRANSMISSION",
                    {"reconciliation_ts": utc_now_iso()},
                )
                bot.brain.delete_active_trade_state(symbol)
            with bot.lock:
                if symbol in bot.active_trades:
                    del bot.active_trades[symbol]
            lost += 1

        # En PAPER_MODE el balance es virtual; no se compara contra custodia real.
        if bool(getattr(Config, "PAPER_MODE", False)):
            paper_balance = float(getattr(Config, "PAPER_INITIAL_BALANCE", 1000.0))
            if not float(getattr(bot, "balance", 0.0) or 0.0):
                bot.balance = paper_balance
            if not float(getattr(bot, "available_balance", 0.0) or 0.0):
                bot.available_balance = paper_balance
            if not float(getattr(bot, "daily_initial_balance", 0.0) or 0.0):
                bot.daily_initial_balance = paper_balance
            bot.integrity_lock_active = False
            return

        # Integrity lock por discrepancia de balance
        try:
            exchange_balance = float(bot.get_current_balance() or 0.0)
        except Exception as error:
            bot.log(
                f"⚠️ Reconciliación: no se pudo obtener balance del exchange para integrity lock: {error}"
            )
            exchange_balance = 0.0
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

        if adopted or lost or pending_open or intent_expired:
            bot.log(
                "🔁 Reconciliación bootstrap: "
                f"adoptadas={adopted} | pending_open={pending_open} | intent_expired={intent_expired} | lost_in_tx={lost}"
            )

    except Exception as e:
        bot.log(f"⚠️ Error en reconciliación de arranque: {e}")


def allocate_signal_timestamp() -> float:
    return utc_now().timestamp()
