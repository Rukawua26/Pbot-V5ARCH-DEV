import importlib.util
import time
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from config import Config
from core.reconciliation import (
    allocate_signal_timestamp,
    generate_child_client_order_id,
    generate_client_order_id,
)
from learning import shadow_logger
from notifier import send_telegram_msg, send_telegram_photo


def _module_available(module_name: str) -> bool:
    return importlib.util.find_spec(module_name) is not None


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

    req_shadow = is_shadow
    degradation_reason = "UNKNOWN"
    signal_ts = allocate_signal_timestamp()
    instance_id = getattr(bot, "instance_uuid", "default")
    entry_client_order_id = generate_client_order_id(
        symbol, side, signal_ts, instance_id
    )
    sl_client_order_id = generate_child_client_order_id(entry_client_order_id, "SL")
    tp_client_order_id = generate_child_client_order_id(entry_client_order_id, "TP")
    symbol_base = symbol.split("/")[0]
    controls = bot._load_runtime_symbol_controls()
    if symbol_base in controls.get("blocked", set()):
        bot.log(f"🧱 BLOQUEADO por matriz táctica: {symbol}")
        return "SYMBOL_BLOCKED_MATRIX"

    atr_pct = context.get("atr_pct", 0) if context else 0.02
    min_notional = Config.MIN_NOTIONAL_VALUE
    confidence_score = context.get("prob_final", 0.0) if context else 0.0
    current_leverage = max(1, min(Config.LEVERAGE, 10))

    max_notional_possible = bot.balance * current_leverage
    if max_notional_possible < min_notional:
        bot.log(
            f"❌ SALDO_INSUFICIENTE_PARA_MIN_NOTIONAL: Balance ${bot.balance:.2f} × {current_leverage}x = ${max_notional_possible:.2f} < Min ${min_notional:.2f}"
        )
        return "INSUFFICIENT_BALANCE_MIN_NOTIONAL"

    amount, calculated_position_size = bot.risk_engine.calculate_position_size(
        balance=bot.balance,
        symbol=symbol,
        price=price,
        leverage=current_leverage,
        context=context or {},
        is_shadow=is_shadow,
        exchange=bot.execution.exchange,
    )

    if not is_shadow and symbol_base in controls.get("reduced", set()):
        reduced_mult = max(0.1, min(bot._symbol_reduced_size_mult, 1.0))
        calculated_position_size *= reduced_mult
        amount = calculated_position_size / price if price > 0 else amount
        bot.log(
            f"📉 TACTICAL REDUCE {symbol}: size × {reduced_mult:.2f} (decision matrix)"
        )

    bot.log(
        f"📊 [KELLY SIZING] Balance: ${bot.balance:.2f} | Conf: {confidence_score:.1f}% | "
        f"Leverage: {current_leverage}x | Notional: ${calculated_position_size:.2f} | Amount: {amount}"
    )

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
        genes = bot.brain.get_genetic_params(symbol)
        sl_modifier = 1.0
        try:
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
        if not is_shadow and symbol in bot.cooldown_pairs:
            if datetime.now() < bot.cooldown_pairs[symbol]:
                return "COOLDOWN"

        actives = bot.active_trades.values()
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
        "open_time": datetime.now().isoformat(),
    }
    with bot.db_lock:
        persisted = bot.brain.save_active_trade_state(symbol, pending_state)
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
                filled_amount = float(order.get("filled", amount))
                bot.log(f"🛡️ Colocando HARD SL en Binance: {symbol} @ {sl_val}")
                sl_order = bot.execution.place_hard_sl(
                    symbol,
                    side,
                    filled_amount,
                    sl_val,
                    client_order_id=sl_client_order_id,
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
                return "EXECUTION_FAILED"
        elif not is_shadow and Config.PAPER_MODE:
            bot.log(f"📝 PAPER TRADE (Simulado): {side} {symbol} (${final_usd:.2f})")
            send_telegram_msg(
                f"📝 *PAPER TRADE (SIMULACRO)*\n🔹 {symbol}\n🔸 Lado: {side}\n💰 Precio: {price}\n📊 Notional: ${final_usd:.2f}\n⚠️ *AVISO:* Bot en modo PAPER."
            )
        else:
            bot.log(f"👻 SHADOW {side} {symbol} (${final_usd:.2f})")
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

        with bot.lock:
            clean_snapshot = (context or {}).copy()
            for heavy_key in ("df_1h", "df_4h", "df"):
                if heavy_key in clean_snapshot:
                    del clean_snapshot[heavy_key]

            trade_state = {
                "symbol": symbol,
                "side": side,
                "entry": price,
                "pnl": 0.0,
                "amount": amount,
                "sl": sl_val,
                "tp": tp_val,
                "trailing_active": False,
                "early_be_armed": False,
                "peak_pnl": 0.0,
                "open_time": datetime.now(),
                "is_shadow": is_shadow,
                "simulated_real": Config.PAPER_MODE and not is_shadow,
                "sector": "OTHE",
                "leverage": current_leverage,
                "market_snapshot": clean_snapshot,
                "entry_ob": ob_status,
                "entry_confidence": (context or {}).get("prob_final", 75.0),
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
                "status": "OPEN",
                "signal_ts": signal_ts,
            }

            if symbol not in bot.active_trades:
                bot.active_trades[symbol] = trade_state
                with bot.db_lock:
                    bot.brain.save_active_trade_state(symbol, trade_state)
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
            bot.cooldown_pairs[symbol] = datetime.now() + timedelta(
                minutes=cooldown_minutes
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

        amt = float(trade["amount"])
        pnl_bruto_usd = (exit_price - trade["entry"]) * amt
        if trade["side"] == "SELL":
            pnl_bruto_usd *= -1

        pnl_neto_usd = pnl_bruto_usd - fees
        val = trade["entry"] * amt
        pnl_neto_percent = (pnl_neto_usd / val) * 100 if val > 0 else 0

        entry_price = trade["entry"]
        mae_price = trade.get("mae_price", entry_price)
        mfe_price = trade.get("mfe_price", entry_price)
        side = trade.get("side", "BUY")

        if side == "BUY":
            mae_percent = (
                ((entry_price - mae_price) / entry_price) * 100 if mae_price else 0
            )
            mfe_percent = (
                ((mfe_price - entry_price) / entry_price) * 100 if mfe_price else 0
            )
        else:
            mae_percent = (
                ((mae_price - entry_price) / entry_price) * 100 if mae_price else 0
            )
            mfe_percent = (
                ((entry_price - mfe_price) / entry_price) * 100 if mfe_price else 0
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
                    }
                )
                bot.log(
                    f"💾 Trade guardado #{trade_id if trade_id else 'N/A'}: {symbol} | "
                    f"is_shadow={trade.get('is_shadow', False)} | PnL={pnl_neto_percent:.2f}% | ${pnl_neto_usd:+.4f}"
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
                    entry_dt = datetime.fromisoformat(entry_time)
                else:
                    entry_dt = entry_time
                duration = datetime.now() - entry_dt
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
        send_telegram_msg(msg_telegram)
        bot._check_recent_mfe_health()

        now = datetime.now()
        default_cd_until = now + timedelta(minutes=Config.TRADE_COOLDOWN_MINUTES)
        bot.cooldown_pairs[symbol] = default_cd_until
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
            if freeze_until > bot.cooldown_pairs.get(symbol, now):
                bot.cooldown_pairs[symbol] = freeze_until
            bot.log(
                f"🧊 SMART EXIT FREEZE: {symbol} bloqueado por {freeze_hours:.0f}h (razón={reason_txt[:60]})."
            )

        bot.risk_engine.record_trade_result(symbol, pnl_neto_percent)

        if pnl_neto_percent < 0 and not trade.get("is_shadow", False):
            anti_revenge_until = now + timedelta(hours=1)
            if anti_revenge_until > bot.cooldown_pairs.get(symbol, now):
                bot.cooldown_pairs[symbol] = anti_revenge_until
            bot.log(
                f"🛡️ ANTI-REBOTE: {symbol} vetado por 1h adicional (pérdida en {'LONG' if trade['side'] == 'BUY' else 'SHORT'})."
            )

        if pnl_neto_percent < -15.0 and not trade.get("is_shadow"):
            bot.is_paused = True
            bot.pause_time = datetime.now() + timedelta(hours=1)
            bot.log(
                f"☢️ CIRCUIT BREAKER: GAP masivo ({pnl_neto_percent:.2f}%). Pausando 1h."
            )
            send_telegram_msg(
                f"☢️ *CIRCUIT BREAKER:* GAP masivo en {symbol} ({pnl_neto_percent:.2f}%). Modo Real pausado 1h por seguridad."
            )

        bot._update_dynamic_risk()
    except Exception as e:
        with bot.lock:
            current = bot.active_trades.get(symbol)
            if current:
                current["closing_in_progress"] = False
                current["status"] = "OPEN"
        with bot.db_lock:
            current = bot.active_trades.get(symbol)
            if current:
                bot.brain.save_active_trade_state(symbol, current)
        bot.log(f"Error cerrando {symbol}: {e}")
