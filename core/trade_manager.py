import importlib.util
import time
from contextlib import nullcontext
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from config import Config
from core.cooldown_state import is_symbol_in_cooldown, set_symbol_cooldown
from core.execution_telemetry import append_execution_event
from core.postmortem import label_exit_reason
from core.reconciliation import (
    allocate_signal_timestamp,
    generate_order_ids,
)
from core.symbol_utils import normalize_position_symbol
from core.time_utils import parse_datetime_utc, utc_now, utc_now_iso
from learning import shadow_logger
from notifier import Priority, send_telegram_msg, send_telegram_photo


def _module_available(module_name: str) -> bool:
    return importlib.util.find_spec(module_name) is not None


def _clamp_leverage_1_to_10(raw_leverage) -> int:
    try:
        lev = int(float(raw_leverage))
    except (TypeError, ValueError):
        lev = 10
    return max(1, min(lev, 10))


def _fail_safe_close_when_sl_missing(
    bot, symbol: str, side: str, amount: float
) -> bool:
    for attempt in range(1, 4):
        try:
            bot.execution.close_position(symbol, side, amount)
            return True
        except Exception as error:
            bot.log(
                f"⚠️ FAIL_SAFE_CLOSE intento {attempt}/3 fallido en {symbol}: {error}"
            )
            if attempt < 3:
                time.sleep(2 ** (attempt - 1))
    return False


def _validate_entry_preconditions(bot, symbol: str, is_shadow: bool) -> Optional[str]:
    if bool(getattr(bot, "stop_requested", False)) or bool(
        getattr(bot, "shutdown_in_progress", False)
    ):
        bot.log("🛑 SHUTDOWN_SEQUENCE: nueva entrada rechazada.")
        return "SHUTDOWN_IN_PROGRESS"

    existing_state = (getattr(bot, "active_trades", {}) or {}).get(symbol)
    if isinstance(existing_state, dict):
        existing_status = str(existing_state.get("status") or "").upper()
        if existing_status in {
            "PENDING_SEND",
            "PENDING_EXCHANGE_OPEN",
            "ENTRY_FILLED_AWAITING_POSITION_SYNC",
            "PARTIAL_FILL_PENDING",
            "PARTIAL_FILL",
            "ORDER_LOOKUP_FAILED",
        }:
            bot.log(
                f"🧷 RECOVERY_GUARD {symbol}: estado pendiente detectado ({existing_status}). Se bloquea nueva apertura para evitar duplicado tras reinicio."
            )
            return "RECOVERY_PENDING_STATE"

    if not is_shadow and shadow_logger.is_trading_halted():
        bot.log(
            "🛑 BLOQUEO DE SEGURIDAD: Trading real detenido por fallo persistente de persistencia (DB)."
        )
        return "TRADING_HALTED_DB_ERROR"

    if not is_shadow and bool(getattr(bot, "integrity_lock_active", False)):
        bot.log(
            "🛑 INTEGRITY_LOCK activo: se bloquea apertura de nuevas posiciones reales."
        )
        return "INTEGRITY_LOCK_ACTIVE"

    if not is_shadow and bool(getattr(bot, "halt_system_active", False)):
        bot.log("🛑 HALT_SYSTEM activo: bloqueando nuevas posiciones reales.")
        return "HALT_SYSTEM_ACTIVE"

    if bool(getattr(bot, "confidence_stagnation_lock_active", False)):
        bot.log(
            f"🛑 CONFIDENCE_STAGNATION_LOCK activo: bloqueando nueva entrada {symbol}."
        )
        return "CONFIDENCE_STAGNATION_LOCK"

    return None


def _validate_symbol_entry(bot, symbol: str, is_shadow: bool) -> Optional[str]:
    symbol_base = symbol.split("/")[0]
    controls = bot._load_runtime_symbol_controls()
    if symbol_base in controls.get("blocked", set()):
        bot.log(f"🧱 BLOQUEADO por matriz táctica: {symbol}")
        return "SYMBOL_BLOCKED_MATRIX"

    if not is_shadow:
        execution = getattr(bot, "execution", None)
        is_quarantined = getattr(execution, "is_symbol_quarantined", None)
        get_remaining = getattr(
            execution, "get_symbol_quarantine_remaining_seconds", None
        )
        if callable(is_quarantined) and is_quarantined(symbol):
            remaining_s = int(get_remaining(symbol) if callable(get_remaining) else 0)
            bot.log(
                f"🚫 SYMBOL_QUARANTINE_ACTIVE {symbol}: bloqueada apertura real por degradación cancel_all ({remaining_s}s restantes)."
            )
            return "SYMBOL_QUARANTINED"

    return None


def _calculate_pnl_and_metrics(
    trade: Dict[str, Any],
    exit_price: float,
    fees: float,
    side: str,
) -> Dict[str, Any]:
    amt = float(trade["amount"])
    pnl_bruto_usd = (exit_price - trade["entry"]) * amt
    if side == "SELL":
        pnl_bruto_usd *= -1

    pnl_neto_usd = pnl_bruto_usd - fees
    val = trade["entry"] * amt
    pnl_neto_percent = (pnl_neto_usd / val) * 100 if val > 0 else 0

    entry_price = trade["entry"]
    mae_price = trade.get("mae_price", entry_price)
    mfe_price = trade.get("mfe_price", entry_price)

    if side == "BUY":
        mae_percent = ((entry_price - mae_price) / entry_price) * 100 if mae_price else 0
        mfe_percent = ((mfe_price - entry_price) / entry_price) * 100 if mfe_price else 0
    else:
        mae_percent = ((mae_price - entry_price) / entry_price) * 100 if mae_price else 0
        mfe_percent = ((entry_price - mfe_price) / entry_price) * 100 if mfe_price else 0

    return {
        "amt": amt,
        "pnl_bruto_usd": pnl_bruto_usd,
        "pnl_neto_usd": pnl_neto_usd,
        "pnl_neto_percent": pnl_neto_percent,
        "mae_percent": mae_percent,
        "mfe_percent": mfe_percent,
    }


def _safe_log_signal_alert(bot, **kwargs) -> None:
    brain = getattr(bot, "brain", None)
    method = getattr(brain, "log_signal_alert", None)
    lock = getattr(bot, "db_lock", None)
    if not callable(method):
        return
    with (lock or nullcontext()):
        method(**kwargs)


def _safe_update_signal_alert_status(bot, entry_client_order_id, status) -> None:
    brain = getattr(bot, "brain", None)
    method = getattr(brain, "update_signal_alert_status", None)
    lock = getattr(bot, "db_lock", None)
    if not callable(method):
        return
    with (lock or nullcontext()):
        method(entry_client_order_id, status)


def _order_looks_filled(order: dict) -> bool:
    if not isinstance(order, dict):
        return False
    status = str(
        order.get("status") or (order.get("info") or {}).get("status") or ""
    ).lower()
    return status in {"closed", "filled"}


def _exchange_position_is_flat(bot, symbol: str) -> bool:
    fetch_positions = getattr(getattr(bot, "execution", None), "fetch_positions", None)
    if not callable(fetch_positions):
        raise RuntimeError(
            "No se puede confirmar exposición cero: fetch_positions no disponible"
        )

    positions = fetch_positions() or []
    for pos in positions:
        if not isinstance(pos, dict):
            continue
        if normalize_position_symbol(pos.get("symbol", "")) != symbol:
            continue
        contracts = pos.get("contracts")
        if contracts is None:
            contracts = (pos.get("info") or {}).get("positionAmt", 0)
        if abs(float(contracts or 0.0)) > 0.0:
            return False
    return True


def _sanitize_context(bot, context):
    data_service = getattr(bot, "data_service", None)
    sanitizer = getattr(data_service, "sanitize_context", None)
    if callable(sanitizer):
        return sanitizer(context)
    if isinstance(context, dict):
        return dict(context)
    return {}


def _get_local_open_trade_counts(bot):
    open_statuses = {
        "OPEN",
        "PENDING_SEND",
        "PENDING_EXCHANGE_OPEN",
        "ENTRY_FILLED_AWAITING_POSITION_SYNC",
        "PARTIAL_FILL_PENDING",
        "PARTIAL_FILL",
    }
    states = {}
    try:
        states.update(getattr(bot, "active_trades", {}) or {})
    except Exception as error:
        logger = getattr(bot, "log", None)
        if callable(logger):
            logger(f"🛑 No se pudo leer active_trades local para conteo: {error}")
        return int(getattr(Config, "MAX_OPEN_TRADES", 1)), int(
            getattr(Config, "MAX_SHADOW_TRADES", 0)
        )

    brain = getattr(bot, "brain", None)
    loader = getattr(brain, "load_active_trade_states", None)
    if callable(loader):
        try:
            for symbol, state in (loader() or {}).items():
                states.setdefault(symbol, state)
        except Exception as error:
            logger = getattr(bot, "log", None)
            if callable(logger):
                logger(f"🛑 No se pudo cargar estado persistido para conteo: {error}")
            return int(getattr(Config, "MAX_OPEN_TRADES", 1)), int(
                getattr(Config, "MAX_SHADOW_TRADES", 0)
            )

    num_real = 0
    num_shadow = 0
    for state in states.values():
        if not isinstance(state, dict):
            continue
        status = str(state.get("status") or "").upper()
        if status not in open_statuses:
            continue
        if bool(state.get("is_shadow", False)):
            num_shadow += 1
        else:
            num_real += 1
    return num_real, num_shadow


def execute_order(
    bot,
    symbol: str,
    side: str,
    price: float,
    atr: float,
    is_shadow: bool = False,
    vol: float = 0,
    context: Optional[Dict[str, Any]] = None,
    ob_status: str = "⚪",
    override_usd_size: float = 0.0,
) -> str:
    precheck = _validate_entry_preconditions(bot, symbol, is_shadow)
    if precheck:
        return precheck

    req_shadow = is_shadow
    degradation_reason = "UNKNOWN"
    signal_ts = allocate_signal_timestamp()
    instance_id = getattr(bot, "instance_uuid", "default")
    entry_client_order_id, sl_client_order_id, tp_client_order_id = generate_order_ids(
        symbol, side, signal_ts, instance_id
    )
    symbol_check = _validate_symbol_entry(bot, symbol, is_shadow)
    if symbol_check:
        return symbol_check

    symbol_base = symbol.split("/")[0]
    controls = bot._load_runtime_symbol_controls()

    execution_mode = "SHADOW" if is_shadow else ("PAPER" if Config.PAPER_MODE else "REAL")

    if is_shadow:
        shadow_cd = int(getattr(Config, "SIGNAL_COOLDOWN_SHADOW_SECONDS", 60) or 60)
        last_signal = float(getattr(bot, "last_shadow_signal_ts", 0.0) or 0.0)
        if time.time() - last_signal < shadow_cd:
            return f"SHADOW_COOLDOWN ({int(shadow_cd - (time.time() - last_signal))}s)"

    _safe_log_signal_alert(
        bot,
        symbol=symbol,
        alert_type=side,
        execution_mode=execution_mode,
        status="PENDING",
        entry_client_order_id=entry_client_order_id,
        features=_sanitize_context(bot, context),
    )

    atr_pct = context.get("atr_pct", 0) if context else 0.02
    min_notional = Config.MIN_NOTIONAL_VALUE
    confidence_score = context.get("prob_final", 0.0) if context else 0.0
    current_leverage = _clamp_leverage_1_to_10(getattr(Config, "LEVERAGE", 10))

    max_notional_possible = bot.balance * current_leverage
    if max_notional_possible < min_notional:
        bot.log(
            f"❌ SALDO_INSUFICIENTE_PARA_MIN_NOTIONAL: Balance ${bot.balance:.2f} × {current_leverage}x = ${max_notional_possible:.2f} < Min ${min_notional:.2f}"
        )
        return "INSUFFICIENT_BALANCE_MIN_NOTIONAL"

    if bool(getattr(Config, "REQUIRE_GHOST_MODEL_FOR_TRADING", True)) and getattr(
        bot, "ghost_model", None
    ) is None:
        bot.log(
            f"🛑 GHOST_MODEL_MISSING: bloqueando nueva entrada {symbol} hasta restaurar modelo IA."
        )
        return "GHOST_MODEL_MISSING"

    atr_pct = context.get("atr_pct", 0) if context else 0
    if atr_pct * 100 > Config.NATR_THRESHOLD:
        bot.log(
            f"⚠️ VOLATILIDAD ALTA: {symbol} NATR {atr_pct * 100:.1f}%. Degradando a SHADOW."
        )
        is_shadow = True
        degradation_reason = "HIGH_VOLATILITY"

    trend = (context or {}).get("trend", "RANGO")
    spread = (context or {}).get("spread", 0.0)
    with bot.db_lock:
        genes = (context or {}).get("sl_genes")
        sl_modifier = float((context or {}).get("sl_modifier", 1.0) or 1.0)
        try:
            if genes is None:
                genes = bot.brain.get_genetic_params(symbol)
            if "sl_modifier" not in (context or {}):
                stats = bot.brain.get_stats_by_trend()
                if trend in stats and stats[trend].get("winrate", 50.0) < 45.0:
                    sl_modifier = 0.80
        except Exception as error:
            bot.log(f"⚠️ No se pudo ajustar SL por tendencia en {symbol}: {error}")

    sl_val, tp_val, exit_mode = bot.risk_engine.get_exit_levels(
        entry_price=price,
        side=side,
        atr=atr,
        trend=trend,
        is_shadow=is_shadow,
        modifier=sl_modifier,
        genes=genes,
        spread=spread,
        fees=0.001,
    )
    bot.log(f"🧩 Exit mode {symbol}: {exit_mode}")

    size_by_stop = getattr(bot.risk_engine, "calculate_position_size_by_stop", None)
    if callable(size_by_stop):
        amount, calculated_position_size = size_by_stop(
            balance=bot.balance,
            symbol=symbol,
            entry_price=price,
            stop_loss_price=sl_val,
            leverage=current_leverage,
            is_shadow=is_shadow,
            exchange=bot.execution.exchange,
        )
        sizing_label = "RISK SIZING"
    else:
        amount, calculated_position_size = bot.risk_engine.calculate_position_size(
            balance=bot.balance,
            symbol=symbol,
            price=price,
            leverage=current_leverage,
            context=context or {},
            is_shadow=is_shadow,
            exchange=bot.execution.exchange,
        )
        sizing_label = "KELLY SIZING"

    if not is_shadow and symbol_base in controls.get("reduced", set()):
        reduced_mult = max(0.1, min(bot._symbol_reduced_size_mult, 1.0))
        calculated_position_size *= reduced_mult
        amount = calculated_position_size / price if price > 0 else amount
        bot.log(
            f"📉 TACTICAL REDUCE {symbol}: size × {reduced_mult:.2f} (decision matrix)"
        )

    bot.log(
        f"📊 [{sizing_label}] Balance: ${bot.balance:.2f} | Conf: {confidence_score:.1f}% | "
        f"Leverage: {current_leverage}x | SL: ${sl_val:.5f} | "
        f"Notional: ${calculated_position_size:.2f} | Amount: {amount}"
    )

    funding = (context or {}).get("funding_rate", 0)
    ob = bot.ws_manager.get_l2_state(symbol)
    btc_delta = getattr(bot, "market_btc_change_tf", 0)

    is_safe, reason, prob = bot.risk_engine.check_market_safety(
        (context.get("df_1h") if context else None),
        symbol,
        funding,
        side,
        ob,
        btc_delta,
    )

    if not is_safe:
        bot.log(
            f"🛡️ RIESGO DETECTADO {symbol}: {reason} (Prob: {prob:.0f}%). Degradando a SHADOW."
        )
        is_shadow = True
        degradation_reason = reason

    if bot.is_paused:
        return "BOT_PAUSED"

    if bot.circuit_breaker_active:
        with bot.db_lock:
            bot.brain.save_error_snapshot(
                symbol,
                "CIRCUIT_BREAKER_HARD_PANIC",
                bot.data_service.sanitize_context(context),
            )
        return "CIRCUIT_BREAKER_PANIC"

    global_cd = int(getattr(Config, "GLOBAL_ENTRY_COOLDOWN_SECONDS", 300) or 0)
    last_open_ts = float(getattr(bot, "last_entry_open_ts", 0.0) or 0.0)
    if global_cd > 0 and last_open_ts > 0:
        elapsed_global = time.time() - last_open_ts
        if elapsed_global < global_cd:
            remaining = int(global_cd - elapsed_global)
            bot.log(
                f"⏳ GLOBAL_COOLDOWN activo ({remaining}s restantes): {symbol} bloqueado"
            )
            return "GLOBAL_COOLDOWN"

    with bot.lock:
        if not is_shadow:
            base_coin = bot._get_base_coin(symbol)
            for active_symbol, active_trade in bot.active_trades.items():
                if (
                    not active_trade.get("is_shadow", False)
                    and bot._get_base_coin(active_symbol) == base_coin
                ):
                    bot.log(
                        f"⚠️ BLOQUEADO REAL {symbol}: Ya existe posición REAL abierta en {active_symbol}"
                    )
                    with bot.db_lock:
                        bot.brain.save_error_snapshot(
                            symbol,
                            "DUPLICATE_REAL",
                            bot.data_service.sanitize_context(context),
                        )
                    return "DUPLICATE_REAL_COIN"

            current_sector = next(
                (
                    k
                    for k, v in Config.SECTORS.items()
                    if any(s.lower() in symbol.split("/")[0].lower() for s in v)
                ),
                "OTHE",
            )
            sector_count = sum(
                1
                for t in bot.active_trades.values()
                if t["sector"] == current_sector and not t.get("is_shadow", False)
            )
            if sector_count >= Config.MAX_SECTOR_EXPOSURE:
                return f"MAX_SECTOR_EXPOSURE ({current_sector})"

        if symbol in bot.active_trades:
            return "ALREADY_ACTIVE"
        if not is_shadow:
            in_cd, _remaining = is_symbol_in_cooldown(bot, symbol)
            if in_cd:
                return "COOLDOWN"

        actives = list(bot.active_trades.values())
        if Config.PAPER_MODE:
            num_real, num_shadow = _get_local_open_trade_counts(bot)
        else:
            num_real = sum(1 for t in actives if not t.get("is_shadow", False))
            num_shadow = sum(1 for t in actives if t.get("is_shadow", False))

        if not is_shadow:
            if num_real >= Config.MAX_OPEN_TRADES:
                bot.log(f"⏳ LÍMITE REAL ALCANZADO ({num_real}): {symbol} ignorado.")
                return "MAX_REAL_TRADES"
            t_side = sum(
                1
                for t in actives
                if t["side"] == side and not t.get("is_shadow", False)
            )
            if t_side >= Config.MAX_DIRECTIONAL_TRADES:
                if num_shadow < Config.MAX_SHADOW_TRADES:
                    bot.log(
                        f"🔄 LÍMITE DIRECCIONAL ({side}): {symbol} degradado a SHADOW para no perder oportunidad."
                    )
                    is_shadow = True
                    degradation_reason = "MAX_DIRECTIONAL_DEGRADED"
                else:
                    bot.log(
                        f"⏳ LÍMITE DIRECCIONAL ({side}) y SHADOW ({num_shadow}): {symbol} ignorado."
                    )
                    return "MAX_DIRECTIONAL"
        elif num_shadow >= Config.MAX_SHADOW_TRADES:
            bot.log(f"⏳ LÍMITE SHADOW ALCANZADO ({num_shadow}): {symbol} ignorado.")
            with bot.db_lock:
                bot.brain.save_error_snapshot(
                    symbol,
                    "MAX_SHADOW",
                    bot.data_service.sanitize_context(context),
                )
            return "MAX_SHADOW"

    # Persistencia previa al cable de red (journal de intención)
    pending_state = {
        "symbol": symbol,
        "side": side,
        "entry": price,
        "amount": amount,
        "is_shadow": is_shadow,
        "status": "PENDING_SEND",
        "signal_ts": signal_ts,
        "entry_client_order_id": entry_client_order_id,
        "sl_client_order_id": sl_client_order_id,
        "tp_client_order_id": tp_client_order_id,
        "entry_exchange_order_id": None,
        "sl_exchange_order_id": None,
        "tp_exchange_order_id": None,
        "open_time": utc_now_iso(),
        "intent_created_at_utc": utc_now_iso(),
        "intent_last_check_at_utc": None,
        "intent_check_attempts": 0,
    }
    append_execution_event(
        bot,
        "ORDER_INTENT_CREATED",
        {
            "symbol": symbol,
            "side": side,
            "is_shadow": bool(is_shadow),
            "entry_client_order_id": entry_client_order_id,
            "requested_price": float(price),
            "requested_amount": float(amount),
            "notional_usd": float(calculated_position_size),
        },
    )
    with bot.db_lock:
        persisted = bot.brain.save_active_trade_state(symbol, pending_state)
    append_execution_event(
        bot,
        "PENDING_SEND_PERSISTED",
        {
            "symbol": symbol,
            "entry_client_order_id": entry_client_order_id,
            "status": "PENDING_SEND",
        },
    )
    if not persisted:
        bot.log(
            f"❌ IDPOTENCY_GUARD {symbol}: no se pudo persistir intención PENDING_SEND antes de enviar orden"
        )
        return "INTENT_PERSISTENCE_FAILED"

    def _drop_pending_intent():
        with bot.db_lock:
            bot.brain.delete_active_trade_state(symbol)

    try:
        ticker = bot.execution.fetch_ticker(symbol)
        current_price = float(ticker["last"])
        if current_price > 0:
            price = current_price

        # [FASE 2: GATILLO SEGURO] Verificación de spread en tiempo real
        spread_veto_pct = getattr(Config, "ENTRY_SPREAD_VETO_THRESHOLD", 0.0015)
        try:
            fetch_book_ticker = getattr(bot.execution, "fetch_book_ticker", None)
            if callable(fetch_book_ticker):
                book_ticker = fetch_book_ticker(symbol)
            else:
                all_books = bot.execution.fetch_book_tickers() or []
                market_id = symbol.replace("/", "")
                book_ticker = next(
                    (
                        item
                        for item in all_books
                        if str(item.get("symbol") or "").upper() == market_id.upper()
                    ),
                    {},
                )
            bid = float(book_ticker.get("bidPrice", 0) or 0)
            ask = float(book_ticker.get("askPrice", 0) or 0)
            if bid > 0 and ask > 0:
                current_spread = (ask - bid) / ask
                if current_spread > spread_veto_pct:
                    bot.log(
                        f"🚫 VETO_SPREAD {symbol}: spread {current_spread * 100:.3f}% > {spread_veto_pct * 100:.3f}%"
                    )
                    append_execution_event(
                        bot,
                        "ENTRY_ABORTED_HIGH_SPREAD",
                        {
                            "symbol": symbol,
                            "spread_pct": current_spread * 100,
                            "threshold_pct": spread_veto_pct * 100,
                            "bid": bid,
                            "ask": ask,
                        },
                    )
                    _safe_update_signal_alert_status(bot, entry_client_order_id, "VETOED")
                    _drop_pending_intent()
                    return f"HIGH_SPREAD_VETO ({current_spread * 100:.3f}%)"
        except Exception as spread_err:
            bot.log(f"⚠️ No se pudo verificar spread para {symbol}: {spread_err}")

    except Exception as error:
        bot.log(f"⚠️ No se pudo refrescar precio para {symbol}: {error}")

    try:
        final_usd = calculated_position_size
        order = None
        sl_order = None
        if amount <= 0 or final_usd <= 0:
            bot.log(
                f"⚠️ ABORTO {symbol}: Tamaño inválido (amount={amount}, notional=${final_usd:.2f})"
            )
            _drop_pending_intent()
            return "SIZE_ERROR"

        fees = 0.001
        spread_cost = (context or {}).get("spread", 0.0)
        tp_pct = abs(tp_val - price) / price * 100
        requested_amount = float(amount)
        filled_amount = float(amount)
        remaining_amount = 0.0
        avg_fill_price = float(price)
        min_tp = max(
            Config.MIN_TP_NET_PERCENT,
            (spread_cost + fees) * Config.MIN_TP_SPREAD_MULTIPLIER,
        )

        if tp_pct < min_tp:
            bot.log(f"🚫 TP INSUFICIENTE: {symbol} ({tp_pct:.2f}% < {min_tp:.2f}%)")
            if not is_shadow:
                _drop_pending_intent()
                return "TP_INSUFFICIENT"
            is_shadow = True

        if not is_shadow and not Config.PAPER_MODE:
            bot.log(
                f"🚀 [PRECISION ENTRY] {symbol} {side} ${final_usd:.2f} @ {price:.5f} (Lev: {current_leverage}x)"
            )
            bot.execution.set_leverage(current_leverage, symbol)
            order_slippage = Config.MAX_SLIPPAGE * 100
            order = bot.execution.create_precision_order(
                symbol,
                side,
                amount,
                price,
                order_slippage,
                client_order_id=entry_client_order_id,
            )

            if order and order.get("status") in ["closed", "open", "filled"]:
                bot.log(f"✅ EJECUCIÓN EXITOSA: {symbol} ID: {order['id']}")
                requested_amount = float(amount)
                filled_amount = float(order.get("filled", requested_amount) or 0.0)
                remaining_amount = max(0.0, requested_amount - filled_amount)
                avg_fill_price = float(
                    order.get("average") or order.get("price") or price
                )
                if filled_amount <= 0:
                    bot.log(f"❌ FALLO DE EJECUCIÓN: {symbol} sin fills confirmados")
                    _safe_update_signal_alert_status(bot, entry_client_order_id, "REJECTED")
                    _drop_pending_intent()
                    return "EXECUTION_NO_FILL"

                append_execution_event(
                    bot,
                    "ENTRY_ORDER_ACK",
                    {
                        "symbol": symbol,
                        "entry_client_order_id": entry_client_order_id,
                        "exchange_order_id": order.get("id"),
                        "requested_amount": requested_amount,
                        "filled_amount": filled_amount,
                        "remaining_amount": remaining_amount,
                        "requested_price": float(price),
                        "avg_fill_price": avg_fill_price,
                        "slippage_simulated": avg_fill_price - float(price),
                        "status": str(order.get("status") or ""),
                    },
                )
                append_execution_event(
                    bot,
                    "ORDER_FILLED",
                    {
                        "symbol": symbol,
                        "side": side,
                        "is_shadow": False,
                        "entry_client_order_id": entry_client_order_id,
                        "exchange_order_id": order.get("id"),
                        "filled_amount": filled_amount,
                        "avg_fill_price": avg_fill_price,
                    },
                )
                if remaining_amount > 0.0:
                    append_execution_event(
                        bot,
                        "PARTIAL_FILL_DETECTED",
                        {
                            "symbol": symbol,
                            "entry_client_order_id": entry_client_order_id,
                            "requested_amount": requested_amount,
                            "filled_amount": filled_amount,
                            "remaining_amount": remaining_amount,
                        },
                    )

                bot.log(f"🛡️ Colocando HARD SL en Binance: {symbol} @ {sl_val}")
                sl_order = bot.execution.place_hard_sl(
                    symbol,
                    side,
                    filled_amount,
                    sl_val,
                    client_order_id=sl_client_order_id,
                )

                if not sl_order:
                    sl_error = str(
                        getattr(bot.execution, "last_hard_sl_error", "") or ""
                    )
                    bot.log(
                        f"☢️ HARD_SL_ATTACH_FAILED {symbol}: entrada cerrada por fail-safe para evitar posición desnuda. error={sl_error[:180]}"
                    )
                    append_execution_event(
                        bot,
                        "ENTRY_ABORTED_NO_HARD_SL",
                        {
                            "symbol": symbol,
                            "entry_client_order_id": entry_client_order_id,
                            "sl_client_order_id": sl_client_order_id,
                            "sl_error": sl_error[:180],
                        },
                    )

                    closed = _fail_safe_close_when_sl_missing(
                        bot, symbol, side, filled_amount
                    )
                    if not closed:
                        bot.is_paused = True
                        bot.integrity_lock_active = True
                        setattr(bot, "halt_system_active", True)
                        append_execution_event(
                            bot,
                            "FAIL_SAFE_CLOSE_FAILED_HALT",
                            {
                                "symbol": symbol,
                                "entry_client_order_id": entry_client_order_id,
                                "sl_error": sl_error[:180],
                            },
                        )
                    _safe_update_signal_alert_status(bot, entry_client_order_id, "REJECTED")
                    _drop_pending_intent()
                    return "ENTRY_ABORTED_NO_HARD_SL"

                append_execution_event(
                    bot,
                    "ORDER_PROTECTION_ATTACHED",
                    {
                        "symbol": symbol,
                        "side": side,
                        "entry_client_order_id": entry_client_order_id,
                        "sl_client_order_id": sl_client_order_id,
                        "sl_exchange_order_id": sl_order.get("id"),
                        "sl_price": float(sl_val),
                    },
                )

                send_telegram_msg(
                    f"🚀 *🔥 REAL TRADE ABIERTO*\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"🔹 *{symbol}*\n"
                    f"🔸 Lado: {side}\n"
                    f"💰 Precio: ${price}\n"
                    f"📊 Notional: ${final_usd:.2f}\n"
                    f"🆔 ID: {order.get('id', 'N/A')}\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"📈 *MERCADO*\n"
                    f"   RSI: {context.get('rsi', 0) if context else 0:.1f}\n"
                    f"   ADX: {context.get('adx', 0) if context else 0:.1f}\n"
                    f"   Tendencia: {context.get('trend', 'N/A') if context else 'N/A'}\n"
                    f"   SL: {sl_val:.4f} | TP: {tp_val:.4f}"
                )
                margin_used = final_usd / current_leverage
                bot.available_balance -= margin_used
            else:
                bot.log(f"❌ FALLO DE EJECUCIÓN: {symbol}")
                reject_reason = str(
                    getattr(bot.execution, "last_entry_reject_error", "")
                    or "EXECUTION_FAILED"
                )[:220]
                append_execution_event(
                    bot,
                    "ENTRY_ORDER_REJECTED",
                    {
                        "symbol": symbol,
                        "entry_client_order_id": entry_client_order_id,
                        "reason": reject_reason,
                    },
                )
                _safe_update_signal_alert_status(bot, entry_client_order_id, "REJECTED")

                pending_state["status"] = "ENTRY_ACK_UNKNOWN"
                pending_state["entry_reject_reason"] = reject_reason
                pending_state["intent_last_check_at_utc"] = utc_now_iso()
                pending_state["intent_check_attempts"] = int(
                    pending_state.get("intent_check_attempts", 0) or 0
                ) + 1
                with bot.lock:
                    bot.active_trades[symbol] = pending_state
                with bot.db_lock:
                    bot.brain.save_active_trade_state(symbol, pending_state)
                append_execution_event(
                    bot,
                    "ENTRY_ACK_UNKNOWN_PERSISTED",
                    {
                        "symbol": symbol,
                        "entry_client_order_id": entry_client_order_id,
                        "reason": reject_reason,
                    },
                )
                if not Config.PAPER_MODE:
                    bot.integrity_lock_active = True
                    with bot.db_lock:
                        bot.brain.save_active_trade_state(symbol, pending_state)
                    return "ENTRY_ACK_UNKNOWN"
                _drop_pending_intent()
                return "EXECUTION_FAILED"
        elif not is_shadow and Config.PAPER_MODE:
            bot.log(f"📝 PAPER TRADE (Simulado): {side} {symbol} (${final_usd:.2f})")
            append_execution_event(
                bot,
                "ORDER_FILLED",
                {
                    "symbol": symbol,
                    "side": side,
                    "is_shadow": False,
                    "simulated_real": True,
                    "entry_client_order_id": entry_client_order_id,
                    "filled_amount": float(amount),
                    "avg_fill_price": float(price),
                },
            )
            send_telegram_msg(
                f"📝 *PAPER TRADE (SIMULACRO)*\n🔹 {symbol}\n🔸 Lado: {side}\n💰 Precio: {price}\n📊 Notional: ${final_usd:.2f}\n⚠️ *AVISO:* Bot en modo PAPER."
            )
        else:
            bot.log(f"👻 SHADOW {side} {symbol} (${final_usd:.2f})")
            append_execution_event(
                bot,
                "ORDER_FILLED",
                {
                    "symbol": symbol,
                    "side": side,
                    "is_shadow": True,
                    "entry_client_order_id": entry_client_order_id,
                    "filled_amount": float(amount),
                    "avg_fill_price": float(price),
                },
            )
            send_telegram_msg(
                f"👻 *SHADOW TRADE ABIERTO*\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"🔹 *{symbol}*\n"
                f"🔸 Lado: {side}\n"
                f"💰 Precio: ${price}\n"
                f"📊 Notional: ${final_usd:.2f}\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"   SL: {sl_val:.4f} | TP: {tp_val:.4f}"
            )

        _safe_update_signal_alert_status(bot, entry_client_order_id, "EXECUTED")
        bot.last_entry_open_ts = time.time()
        if is_shadow:
            bot.last_shadow_signal_ts = time.time()

        with bot.lock:
            clean_snapshot = (context or {}).copy()
            for heavy_key in ("df_1h", "df_4h", "df"):
                if heavy_key in clean_snapshot:
                    del clean_snapshot[heavy_key]

            trade_state = {
                "symbol": symbol,
                "side": side,
                "entry": float(avg_fill_price if not is_shadow else price),
                "pnl": 0.0,
                "amount": float(filled_amount if not is_shadow else amount),
                "requested_amount": float(
                    requested_amount if not is_shadow else amount
                ),
                "remaining_amount": float(remaining_amount if not is_shadow else 0.0),
                "sl": sl_val,
                "tp": tp_val,
                "trailing_active": False,
                "early_be_armed": False,
                "peak_pnl": 0.0,
                "open_time": utc_now_iso(),
                "is_shadow": is_shadow,
                "simulated_real": Config.PAPER_MODE and not is_shadow,
                "sector": "OTHE",
                "leverage": current_leverage,
                "market_snapshot": clean_snapshot,
                "entry_ob": ob_status,
                "entry_confidence": (context or {}).get("prob_final", 75.0),
                "current_confidence": (context or {}).get("prob_final", 75.0),
                "entry_shock_level": (context or {}).get("shock_level"),
                "entry_atr": (context or {}).get("atr", 0.0),
                "breakout_origin": bool((context or {}).get("breakout_ready", False)),
                "entry_client_order_id": entry_client_order_id,
                "sl_client_order_id": sl_client_order_id,
                "tp_client_order_id": tp_client_order_id,
                "entry_exchange_order_id": (order or {}).get("id")
                if not is_shadow
                else None,
                "sl_exchange_order_id": (sl_order or {}).get("id")
                if not is_shadow
                else None,
                "tp_exchange_order_id": None,
                "status": "PARTIAL_FILL_PENDING"
                if (not is_shadow and remaining_amount > 0.0)
                else "OPEN",
                "partial_fill_pending": (not is_shadow and remaining_amount > 0.0),
                "partial_fill_started_at": utc_now_iso()
                if (not is_shadow and remaining_amount > 0.0)
                else None,
                "signal_ts": signal_ts,
            }

            if symbol not in bot.active_trades:
                bot.active_trades[symbol] = trade_state
                with bot.db_lock:
                    persisted = bot.brain.save_active_trade_state(symbol, trade_state)
                if not persisted:
                    bot.integrity_lock_active = True
                    bot.log(
                        f"🛑 PERSISTENCE_GUARD {symbol}: orden aceptada pero estado activo no persistió. Integrity lock activado."
                    )
                    append_execution_event(
                        bot,
                        "ACTIVE_STATE_PERSIST_FAILED",
                        {
                            "symbol": symbol,
                            "entry_client_order_id": entry_client_order_id,
                            "status": trade_state.get("status"),
                        },
                    )
                    send_telegram_msg(
                        f"🚨 *PERSISTENCE GUARD*\n{symbol}: orden aceptada pero DB no persistió el estado activo. Integrity lock activado.",
                        Priority.CRITICAL,
                    )
                    return "PERSISTENCE_GUARD_ACTIVE"
                bot.log(
                    f"💾 CARTERA: {symbol} registrado ({'SHADOW' if is_shadow else 'REAL'})."
                )
            else:
                bot.active_trades[symbol].update(trade_state)

            cooldown_minutes = (
                Config.SHADOW_COOLDOWN_MINUTES
                if is_shadow
                else Config.TRADE_COOLDOWN_MINUTES
            )
            set_symbol_cooldown(
                bot, symbol, utc_now() + timedelta(minutes=cooldown_minutes)
            )

            if not req_shadow and is_shadow:
                return f"OK_DEGRADED: {degradation_reason}"
            return "OK"
    except Exception as e:
        bot.log(f"❌ RECHAZO {symbol}: {e}")
        if not is_shadow:
            send_telegram_msg(
                f"❌ *FALLO DE EJECUCIÓN (REAL)*\n{symbol} no pudo abrirse.\nError: {str(e)[:100]}"
            )
        _safe_update_signal_alert_status(bot, entry_client_order_id, "ERROR")
        with bot.db_lock:
            bot.brain.save_error_snapshot(
                symbol,
                "EXEC_EXCEPTION",
                bot.data_service.sanitize_context(context),
            )
        return f"ERROR: {str(e)[:20]}"


def close_trade(
    bot,
    symbol: str,
    reason: str,
    exit_price: float,
    exit_confidence: float = 0.0,
    latency_context: Optional[Dict[str, Any]] = None,
):
    with bot.lock:
        trade = bot.active_trades.get(symbol)
        if trade and trade.get("closing_in_progress"):
            return
        if trade:
            trade["closing_in_progress"] = True
            trade["status"] = "CLOSING_INITIATED"
    if not trade:
        return

    with bot.db_lock:
        bot.brain.save_active_trade_state(symbol, trade)

    try:
        fees = 0
        if not trade.get("is_shadow", False) and not Config.PAPER_MODE:
            try:
                bot.log(
                    f"🔄 [CLOSING POSITION] {symbol} {trade['side']} (Reason: {reason})"
                )
                pre_api_ts = time.perf_counter()
                if "DEGRADED" in reason or "CONF_DEGRADED" in reason:
                    order = bot.execution.close_due_to_degradation(
                        symbol, trade["side"], trade["amount"]
                    )
                else:
                    order = bot.execution.close_position(
                        symbol, trade["side"], trade["amount"]
                    )
                post_api_ts = time.perf_counter()

                if order:
                    bot.log(f"✅ CIERRE EXITOSO: {symbol} ID: {order.get('id', 'N/A')}")

                exit_state = str((order or {}).get("exit_state") or "").upper()
                if exit_state in {"STUCK", "FAILED", "OPEN_UNCONFIRMED"}:
                    raise RuntimeError(
                        f"Cierre no finalizado para {symbol}; exit_state={exit_state}"
                    )

                if not _exchange_position_is_flat(bot, symbol):
                    order_status = str((order or {}).get("status") or "UNKNOWN")
                    raise RuntimeError(
                        f"Cierre no confirmado en exchange para {symbol}; "
                        f"order_status={order_status}"
                    )

                if order and not _order_looks_filled(order):
                    bot.log(
                        f"⚠️ {symbol}: exposición remota plana aunque la orden reporta status={order.get('status', 'N/A')}"
                    )

                if latency_context and latency_context.get("signal_ts") is not None:
                    signal_to_api_ms = (
                        pre_api_ts - float(latency_context["signal_ts"])
                    ) * 1000.0
                    api_ms = (post_api_ts - pre_api_ts) * 1000.0
                    total_ms = (
                        post_api_ts - float(latency_context["signal_ts"])
                    ) * 1000.0
                    status = "OK" if total_ms < 450.0 else "SLOW"
                    trigger = latency_context.get("trigger", "UNKNOWN")
                    bot.log(
                        f"⏱️ SMART_EXIT_LATENCY {symbol} trigger={trigger} signal_to_api_ms={signal_to_api_ms:.1f} api_ms={api_ms:.1f} total_ms={total_ms:.1f} target_ms=450 status={status}"
                    )

            except Exception as e:
                bot.log(f"❌ ERROR CRÍTICO CERRANDO {symbol}: {e}")

                if any(
                    x in str(e).lower() for x in ["notional", "-4164", "insufficient"]
                ):
                    try:
                        if not _exchange_position_is_flat(bot, symbol):
                            raise RuntimeError(
                                f"{symbol}: error de dust/min notional pero exposición remota sigue abierta"
                            )
                    except Exception as verify_error:
                        bot.log(f"🚨 DUST_VERIFY_FAILED {symbol}: {verify_error}")
                        send_telegram_msg(
                            f"⚠️ *FALLO DE CIERRE REAL*\n{symbol}: no se pudo confirmar exposición cero tras error dust/minNotional. {verify_error}"
                        )
                        raise verify_error

                    bot.log(f"⚠️ {symbol} descartado localmente (Dust/Min Notional).")
                    send_telegram_msg(
                        f"⚠️ *AVISO DUST*\n{symbol} cerrado virtualmente por monto bajo."
                    )
                    with bot.lock:
                        if symbol in bot.active_trades:
                            del bot.active_trades[symbol]
                    return
                else:
                    send_telegram_msg(
                        f"⚠️ *FALLO DE CIERRE REAL*\n{symbol} falló en Binance. Error: {e}"
                    )
                    raise e

            time.sleep(1)
            try:
                my_trades = bot.execution.fetch_my_trades(symbol, limit=2)
                fees = sum(
                    t["fee"]["cost"]
                    for t in my_trades
                    if t["fee"]["currency"] == "USDT"
                )
            except Exception as error:
                bot.log(
                    f"⚠️ No se pudo calcular fees reales de cierre para {symbol}: {error}"
                )
        else:
            fees = (trade["entry"] * float(trade["amount"]) * Config.VIRTUAL_FEE) + (
                exit_price * float(trade["amount"]) * Config.VIRTUAL_FEE
            )
            if latency_context and latency_context.get("signal_ts") is not None:
                total_ms = (
                    time.perf_counter() - float(latency_context["signal_ts"])
                ) * 1000.0
                trigger = latency_context.get("trigger", "UNKNOWN")
                bot.log(
                    f"⏱️ SMART_EXIT_LATENCY {symbol} trigger={trigger} total_ms={total_ms:.1f} simulated=1 (PAPER/SHADOW)"
                )

        side = trade.get("side", "BUY")
        pnl_metrics = _calculate_pnl_and_metrics(
            trade, exit_price, fees, side
        )
        entry_price = trade["entry"]
        mae_price = trade.get("mae_price", entry_price)
        mfe_price = trade.get("mfe_price", entry_price)
        amt = pnl_metrics["amt"]
        pnl_neto_usd = pnl_metrics["pnl_neto_usd"]
        pnl_neto_percent = pnl_metrics["pnl_neto_percent"]
        mae_percent = pnl_metrics["mae_percent"]
        mfe_percent = pnl_metrics["mfe_percent"]

        pm_data = label_exit_reason(
            reason=reason,
            entry_price=entry_price,
            exit_price=exit_price,
            side=side,
            mae_percent=mae_percent,
            mfe_percent=mfe_percent,
            trade=trade,
            is_adopted=trade.get("adopted_orphan", False),
        )

        bot.log(
            f"🔍 DEBUG: Intentando guardar trade {symbol} | is_shadow={trade.get('is_shadow', False)}"
        )

        try:
            with bot.db_lock:
                trade_id = bot.brain.log_trade(
                    {
                        "symbol": symbol,
                        "side": trade["side"],
                        "entry": trade["entry"],
                        "exit": exit_price,
                        "pnl_usd": pnl_neto_usd,
                        "pnl_percent": pnl_neto_percent,
                        "reason": reason,
                        "is_shadow": trade.get("is_shadow", False),
                        "fees": fees,
                        "market_snapshot": trade.get("market_snapshot", {}),
                        "open_time": trade["open_time"].isoformat()
                        if isinstance(trade["open_time"], datetime)
                        else trade["open_time"],
                        "entry_ob": trade.get("entry_ob", "⚪"),
                        "mae_percent": mae_percent,
                        "mfe_percent": mfe_percent,
                        "market_regime": bot._get_market_regime(),
                        "entry_confidence": trade.get("entry_confidence", 0.0),
                        "exit_confidence": exit_confidence,
                        "entry_shock_level": trade.get("entry_shock_level"),
                        "entry_atr": trade.get("entry_atr"),
                        "breakout_origin": trade.get("breakout_origin", False),
                        "entry_client_order_id": trade.get("entry_client_order_id"),
                        "sl_client_order_id": trade.get("sl_client_order_id"),
                        "tp_client_order_id": trade.get("tp_client_order_id"),
                        "entry_exchange_order_id": trade.get("entry_exchange_order_id"),
                        "sl_exchange_order_id": trade.get("sl_exchange_order_id"),
                        "tp_exchange_order_id": trade.get("tp_exchange_order_id"),
                        "exit_reason": pm_data.get("exit_reason", "UNKNOWN"),
                        "is_adopted": pm_data.get("is_adopted", 0),
                        "is_dirty": pm_data.get("is_dirty", 0),
                        "mae_at_sl": pm_data.get("mae_at_sl", 0.0),
                        "mfe_at_sl": pm_data.get("mfe_at_sl", 0.0),
                    }
                )
                bot.log(
                    f"💾 Trade guardado #{trade_id if trade_id else 'N/A'}: {symbol} | "
                    f"is_shadow={trade.get('is_shadow', False)} | PnL={pnl_neto_percent:.2f}% | ${pnl_neto_usd:+.4f}"
                )
                bot.brain.finalize_confidence_exit_audit(
                    trade.get("entry_client_order_id"),
                    trade_id or 0,
                    reason,
                    pnl_neto_usd,
                    pnl_neto_percent,
                )

                votos = trade.get("market_snapshot", {}).get("votos", {})
                if votos:
                    shadow_logger.log(
                        {
                            "type": "TRADE_FEEDBACK",
                            "data": {
                                "symbol": symbol,
                                "pnl": pnl_neto_percent,
                                "votos": votos,
                            },
                        }
                    )
                    ctx_type = trade.get("market_snapshot", {}).get("context", "RANGE")
                    bot.brain.update_agent_reputation(
                        votos, pnl_neto_percent, context_type=ctx_type
                    )
        except Exception as e:
            bot.log(f"⚠️ Error guardando trade o reputación {symbol}: {e}")

        recent_trade = {
            "symbol": symbol,
            "side": trade.get("side", "?"),
            "entry": trade.get("entry", 0.0),
            "exit": exit_price,
            "pnl": pnl_neto_percent,
            "is_shadow": trade.get("is_shadow", False),
            "reason": reason,
            "closing_in_progress": False,
        }
        with bot.lock:
            recent = list(getattr(bot, "recent_closed_trades", []) or [])
            recent.insert(0, recent_trade)
            bot.recent_closed_trades = recent[:6]

        with bot.lock:
            if symbol in bot.active_trades:
                del bot.active_trades[symbol]

        with bot.db_lock:
            bot.brain.delete_active_trade_state(symbol)

        if bot.brain.evolve_genetics(symbol):
            bot.log(f"🧬 ADN MUTADO: {symbol} ha evolucionado sus parámetros SL/TP.")

        if trade.get("is_shadow", False):
            status, info = bot.brain.check_eureka_status(symbol)
            if status == "EUREKA":
                msg = (
                    f"🧠 *¡EUREKA! NUEVO PATRÓN DETECTADO*\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"💎 *Par:* {symbol}\n"
                    f"📈 *Tendencia:* {info['trend']}\n"
                    f"📊 *Contexto:* {info['context']}\n"
                    f"🎯 *Efectividad:* {info['wr']:.0f}% ({info['count']} pruebas)\n"
                    f"💡 *Lección:* Patrón validado con {info['context']}.\n"
                    f"📝 *Acción:* Priorizando este patrón para entradas reales."
                )

                try:
                    df_snap = bot.data_service.fetch_and_update_data(
                        symbol, Config.TIMEFRAME
                    )
                    if df_snap is not None and not df_snap.empty:
                        if _module_available("tools.ai_mapper"):
                            from tools.ai_mapper import generate_strategy_snapshot

                            img = generate_strategy_snapshot(
                                symbol, df_snap.tail(100).reset_index(drop=True)
                            )
                            if img:
                                send_telegram_photo(msg, img)
                            else:
                                send_telegram_msg(msg)
                        else:
                            send_telegram_msg(msg)
                    else:
                        send_telegram_msg(msg)
                except Exception as e:
                    bot.log(f"⚠️ Error Visual Eureka: {e}")
                    send_telegram_msg(msg)

                bot.log(f"🧠 EUREKA: {symbol} WR {info['wr']:.1f}%")

            elif status == "FAILURE":
                bot.brain.update_dynamic_settings(symbol, 9.0)
                send_telegram_msg(
                    f"🛡️ *ESTUDIANTE ACTIVO: AUTO-CORRECCIÓN*\nHe detectado fallas repetidas en {symbol} ({info['wr']:.0f}% WR).\n📉 *Acción:* He vetado temporalmente este par."
                )
                bot.log(f"🛡️ AUTO-VETO: {symbol} bloqueado por bajo rendimiento.")

        icono = "👻 SHADOW" if trade.get("is_shadow") else "🔒 REAL"

        bot.log(
            f"{icono} CERRADO {symbol} ({reason}) | PnL: {pnl_neto_percent:.2f}% | ${pnl_neto_usd:+.4f}"
        )

        market_snap = trade.get("market_snapshot", {})
        entry_price = trade.get("entry", 0)
        entry_time = trade.get("open_time", "")
        entry_conf = float(trade.get("entry_confidence", 0.0) or 0.0)
        exit_conf = float(exit_confidence or 0.0)
        ia_delta = exit_conf - entry_conf

        shock_level = trade.get("entry_shock_level")
        shock_dist_pct = None
        try:
            if shock_level is not None and float(exit_price) > 0:
                shock_dist_pct = (
                    abs(float(shock_level) - float(exit_price)) / float(exit_price)
                ) * 100.0
        except Exception:
            shock_dist_pct = None

        atr_val = float(
            trade.get("entry_atr")
            or market_snap.get("atr")
            or (market_snap.get("atr_pct", 0.0) * float(entry_price))
            or 0.0
        )
        drift_4h_est_pct = (
            ((atr_val / float(exit_price)) * 100.0 * 4.0)
            if float(exit_price) > 0 and atr_val > 0
            else 0.0
        )
        shock_dist_txt = (
            f"{shock_dist_pct:.2f}%" if shock_dist_pct is not None else "N/A"
        )
        duration = "N/A"
        if entry_time:
            try:
                if isinstance(entry_time, str):
                    entry_dt = parse_datetime_utc(entry_time)
                else:
                    entry_dt = parse_datetime_utc(entry_time)
                duration = utc_now() - entry_dt
                duration_mins = int(duration.total_seconds() / 60)
                duration = f"{duration_mins}m"
            except Exception:
                duration = "N/A"

        emoji_pnl = "🟢" if pnl_neto_percent > 0 else "🔴"

        msg_telegram = (
            f"{icono} *CERRADO* {emoji_pnl}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🔹 *{symbol}*\n"
            f"📈 *PnL:* {pnl_neto_percent:+.2f}% | ${pnl_neto_usd:+.4f}\n"
            f"📝 *Razón:* {reason}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🆔 Trade ID: #{trade_id if 'trade_id' in locals() and trade_id else 'N/A'}\n"
            f"🧠 IA: {entry_conf:.1f}% → {exit_conf:.1f}% (Δ {ia_delta:+.1f}pp)\n"
            f"🏔️ MFE: {mfe_percent:+.2f}%\n"
            f"📏 Distancia SHOCK: {shock_dist_txt}\n"
            f"🌬️ Drift esperado 4h: {drift_4h_est_pct:.2f}%\n"
            f"💰 Entry: ${entry_price:.6f}\n"
            f"💸 Exit: ${exit_price:.6f}\n"
            f"⏱️ Duración: {duration}"
        )
        msg_priority = Priority.INFO
        reason_upper = str(reason or "").upper()
        if "CIRCUIT BREAKER" in reason_upper:
            msg_priority = Priority.CRITICAL
        elif "DEGRADED" in reason_upper or "BAILOUT" in reason_upper:
            msg_priority = Priority.ERROR
        elif pnl_neto_percent < 0:
            msg_priority = Priority.WARNING
        send_telegram_msg(msg_telegram, msg_priority)

        with bot.db_lock:
            stagnation = bot.brain.get_recent_exit_confidence_stagnation(limit=10)
        if stagnation and float(stagnation.get("stddev", 99.0)) < 1.0:
            bot.confidence_stagnation_lock_active = True
            bot.log(
                f"⚠️ CONFIDENCE_STAGNATION: last10 std={stagnation['stddev']:.3f} "
                f"mean={stagnation['mean']:.2f} range=[{stagnation['min']:.2f},{stagnation['max']:.2f}]"
            )
            send_telegram_msg(
                (
                    "⚠️ *CONFIDENCE STAGNATION*\n"
                    f"Últimos {stagnation['count']} cierres con exit_conf muy comprimida.\n"
                    f"StdDev: {stagnation['stddev']:.3f} | Media: {stagnation['mean']:.2f}\n"
                    f"Rango: {stagnation['min']:.2f} - {stagnation['max']:.2f}"
                ),
                Priority.WARNING,
            )
        bot._check_recent_mfe_health()

        now = utc_now()
        default_cd_until = now + timedelta(minutes=Config.TRADE_COOLDOWN_MINUTES)
        set_symbol_cooldown(bot, symbol, default_cd_until)
        bot.log(
            f"❄️ COOLDOWN UNIVERSAL: {symbol} bloqueado por {Config.TRADE_COOLDOWN_MINUTES}m tras cierre."
        )

        reason_txt = str(reason or "")
        smart_exit_abort = (
            reason_txt.startswith("DEGRADED_")
            or reason_txt.startswith("CONF_DEGRADED_")
            or "SHORT_THESIS_INVALIDATED" in reason_txt
            or "CONFIDENCE_FLOOR_VIOLATED" in reason_txt
            or "SUDDEN_CONFIDENCE_CRASH" in reason_txt
        )
        if smart_exit_abort:
            freeze_hours = float(getattr(Config, "SMART_EXIT_COOLDOWN_HOURS", 4))
            freeze_until = now + timedelta(hours=freeze_hours)
            current_until = now
            current_raw = (getattr(bot, "cooldown_pairs", {}) or {}).get(symbol)
            if current_raw is not None:
                try:
                    current_until = parse_datetime_utc(current_raw)
                except Exception:
                    current_until = now
            if freeze_until > current_until:
                set_symbol_cooldown(bot, symbol, freeze_until)
            bot.log(
                f"🧊 SMART EXIT FREEZE: {symbol} bloqueado por {freeze_hours:.0f}h (razón={reason_txt[:60]})."
            )

        bot.risk_engine.record_trade_result(symbol, pnl_neto_percent)

        if pnl_neto_percent < 0 and not trade.get("is_shadow", False):
            anti_revenge_until = now + timedelta(hours=1)
            current_until = now
            current_raw = (getattr(bot, "cooldown_pairs", {}) or {}).get(symbol)
            if current_raw is not None:
                try:
                    current_until = parse_datetime_utc(current_raw)
                except Exception:
                    current_until = now
            if anti_revenge_until > current_until:
                set_symbol_cooldown(bot, symbol, anti_revenge_until)
            bot.log(
                f"🛡️ ANTI-REBOTE: {symbol} vetado por 1h adicional (pérdida en {'LONG' if trade['side'] == 'BUY' else 'SHORT'})."
            )

        if pnl_neto_percent < -15.0 and not trade.get("is_shadow"):
            bot.is_paused = True
            bot.pause_time = utc_now() + timedelta(hours=1)
            bot.log(
                f"☢️ CIRCUIT BREAKER: GAP masivo ({pnl_neto_percent:.2f}%). Pausando 1h."
            )
            send_telegram_msg(
                f"☢️ *CIRCUIT BREAKER:* GAP masivo en {symbol} ({pnl_neto_percent:.2f}%). Modo Real pausado 1h por seguridad."
            )

        bot._update_dynamic_risk()
    except Exception as e:
        error_str = str(e).upper()
        is_stuck_or_unconfirmed = any(
            x in error_str for x in ["STUCK", "OPEN_UNCONFIRMED", "EXIT_STATE="]
        )

        with bot.lock:
            current = bot.active_trades.get(symbol)
            if current:
                current["closing_in_progress"] = False
                if is_stuck_or_unconfirmed and not (trade.get("is_shadow", False) or Config.PAPER_MODE):
                    current["status"] = "EXIT_STUCK"
                    bot.integrity_lock_active = True
                    setattr(bot, "halt_system_active", True)
                    bot.log(
                        f"🛑 CIERRE_STUCK {symbol}: estado EXIT_STUCK, HALT activado. "
                        f"Requiere intervención manual."
                    )
                    send_telegram_msg(
                        f"🛑 *CIERRE_STUCK* {symbol} falló y activó HALT. "
                        f"Error: {str(e)[:100]}. Requiere intervención manual."
                    )
                else:
                    current["status"] = "OPEN"
        with bot.db_lock:
            current = bot.active_trades.get(symbol)
            if current:
                bot.brain.save_active_trade_state(symbol, current)
        bot.log(f"Error cerrando {symbol}: {e}")


def abort_partial_trade(bot, symbol: str, reason: str, exit_price: float):
    append_execution_event(
        bot,
        "PARTIAL_TRADE_ABORT_REQUESTED",
        {
            "symbol": symbol,
            "reason": reason,
            "exit_price": float(exit_price or 0.0),
        },
    )
    close_trade(
        bot,
        symbol=symbol,
        reason=reason,
        exit_price=exit_price,
        latency_context={"trigger": "GUARDIAN_PARTIAL_ABORT"},
    )
