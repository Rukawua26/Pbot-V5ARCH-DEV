import time
from datetime import datetime

from config import Config
from core.execution_telemetry import append_execution_event
from strategy import Strategy


def run_guardian_loop(bot):
    bot.log("🛡️ Guardián OK.")
    last_heavy = 0
    while bot.is_running:
        loop_started = time.perf_counter()
        try:
            with bot.lock:
                snapshot = bot.active_trades.copy()

            # [v118] BAILOUT PRIORITARIO: Monitoreo de integridad de señales (Smart Exit)
            bot.monitor_open_trades()

            syms = list(snapshot.keys())
            if not syms:
                time.sleep(1)
                continue

            # --- OPTIMIZACIÓN DE LATENCIA (v106.x) ---
            # Prioridad: 1. Websocket (ms) -> 2. REST Mass (s) -> 3. REST Single (lento)
            with bot.price_lock:
                price_map = bot.live_prices.copy()

            if not price_map:
                try:
                    all_prices_raw = bot.execution.fetch_all_prices()
                    price_map = {p["symbol"]: p["price"] for p in all_prices_raw}
                except Exception:
                    # bot.log(f"⚠️ Guardian: fapiPublicGetTickerPrice falló: {e}")
                    price_map = {}

            # PRIORIZACIÓN (v103.7): Reales primero, luego Shadow
            real_syms = [s for s in syms if not snapshot[s].get("is_shadow")]
            shadow_syms = [s for s in syms if snapshot[s].get("is_shadow")]
            sorted_syms = real_syms + shadow_syms

            for s in sorted_syms:
                try:
                    t = snapshot.get(s)
                    if not t:
                        continue
                    if (
                        t.get("closing_in_progress")
                        or t.get("status") == "CLOSING_INITIATED"
                    ):
                        continue
                    if t.get("status") in {"PARTIAL_FILL", "PARTIAL_FILL_PENDING"}:
                        append_execution_event(
                            bot,
                            "GUARDIAN_PARTIAL_OBSERVED",
                            {
                                "symbol": s,
                                "status": t.get("status"),
                                "amount": float(t.get("amount") or 0.0),
                                "remaining_amount": float(
                                    t.get("remaining_amount") or 0.0
                                ),
                            },
                        )
                        bot.log(
                            f"🧭 GUARDIAN PARTIAL {s}: observado {t.get('status')} y omitido para evitar desincronía"
                        )
                        continue

                    # --- [v118-PRO] PRIORIDAD ABSOLUTA: SMART EXIT (BAILOUT) ---
                    # Si la confianza actual < 70% de la inicial, cerrar de inmediato.
                    # Este chequeo ocurre ANTES de cualquier actualización de precios o UI.
                    current_conf = t.get("current_confidence", 50.0)
                    entry_conf = t.get("entry_confidence", 75.0)
                    abort_needed, abort_reason = bot.risk_engine.should_abort_trade(
                        entry_conf, current_conf
                    )

                    if abort_needed:
                        bot.log(
                            f"🚨 [v118-BAILOUT] {s}: Abortando por degradación de señal."
                        )
                        bot._guardian_stats["bailout_count"] += 1
                        bot.close_trade(
                            s,
                            abort_reason,
                            t.get("last_price", 0),
                            latency_context={
                                "trigger": "BAILOUT_GUARDIAN",
                                "signal_ts": time.perf_counter(),
                                "entry_conf": entry_conf,
                                "exit_conf": current_conf,
                            },
                        )
                        continue

                    # Lógica de obtención de precio optimizada
                    binance_symbol = s.replace("/", "")
                    if binance_symbol in price_map:
                        curr = float(price_map[binance_symbol])
                    else:
                        # Fallback a fetch_ticker individual solo si el endpoint masivo falló o el par es nuevo
                        try:
                            curr = float(bot.execution.fetch_ticker(s)["last"])
                        except Exception as fetch_e:
                            bot.log(
                                f"Guardian: No se pudo obtener precio para {s}: {fetch_e}"
                            )
                            continue  # Saltar al siguiente símbolo si no se puede obtener el precio
                    t["last_price"] = curr

                    # MAE/MFE Tracking (Maximum Adverse/Favorable Excursion)
                    side = t.get("side", "BUY")
                    if side == "BUY":
                        if curr < t.get("mae_price", float("inf")):
                            t["mae_price"] = curr
                        if curr > t.get("mfe_price", 0):
                            t["mfe_price"] = curr
                    else:
                        if curr > t.get("mae_price", 0):
                            t["mae_price"] = curr
                        if curr < t.get("mfe_price", float("inf")):
                            t["mfe_price"] = curr

                    # PnL Dinámico
                    ratio = (
                        (curr - t["entry"])
                        / t["entry"]
                        * (1 if t["side"] == "BUY" else -1)
                    )
                    actual_leverage = t.get("leverage", 1)
                    gross_pnl = ratio * 100 * actual_leverage
                    # Restamos comisiones estimadas (Ida + Vuelta) para ser realistas
                    fee_drag = (Config.VIRTUAL_FEE * 2) * actual_leverage * 100
                    t["pnl"] = gross_pnl - fee_drag

                    if t["pnl"] > t.get("peak_pnl", -999):
                        t["peak_pnl"] = t["pnl"]

                    # Exit Engine v118 (dinámico y persistente)
                    if bool(getattr(Config, "EXIT_ENGINE_V1_ENABLED", True)):
                        snap_ctx = t.get("market_snapshot", {}) or {}
                        current_atr = float(
                            t.get("entry_atr")
                            or snap_ctx.get("atr")
                            or snap_ctx.get("atr_pct", 0.0) * t.get("entry", 0.0)
                            or 0.0
                        )

                        exit_eval = bot.exit_engine.evaluate_exit(
                            trade=t,
                            current_price=curr,
                            current_atr=current_atr,
                        )
                        now_ts = time.time()
                        last_log_ts = float(bot._exit_eval_last_log.get(s, 0.0))
                        if now_ts - last_log_ts >= 120:
                            bot._exit_eval_last_log[s] = now_ts
                            bot.log(
                                f"🧭 EXIT_EVAL {s}: reason={exit_eval.get('reason')} pnl={t.get('pnl', 0.0):.2f}%"
                            )
                        if bool(exit_eval.get("should_exit", False)):
                            exit_reason = str(exit_eval.get("reason", "EXIT_ENGINE"))
                            bot.close_trade(s, exit_reason, curr)
                            continue

                    # Fallback legacy de break-even (solo si Exit Engine v1 está desactivado).
                    if (
                        not bool(getattr(Config, "EXIT_ENGINE_V1_ENABLED", True))
                        and t["pnl"] >= Config.EARLY_BREAKEVEN_ACTIVATION_PNL
                        and not t.get("early_be_armed", False)
                    ):
                        be_fee_buffer = max(Config.VIRTUAL_FEE * 2, 0.0)
                        if t["side"] == "BUY":
                            be_sl = t["entry"] * (1.0 + be_fee_buffer)
                            should_tighten = be_sl > t.get("sl", 0)
                        else:
                            be_sl = t["entry"] * (1.0 - be_fee_buffer)
                            current_sl = t.get("sl", float("inf"))
                            should_tighten = be_sl < current_sl

                        if should_tighten:
                            t["sl"] = be_sl
                        t["early_be_armed"] = True
                        bot.log(
                            f"🛡️ EARLY BE {s}: PnL {t['pnl']:.2f}% >= {Config.EARLY_BREAKEVEN_ACTIVATION_PNL:.2f}% | "
                            f"SL ajustado a break-even con fees ({be_sl:.6f})."
                        )

                    # PARÁMETROS UNIFICADOS: Trailing temprano para real y shadow
                    if t["pnl"] > Config.TRAILING_ACTIVATION_PNL:
                        t["trailing_active"] = True

                    # Time Limit
                    ot = t.get("open_time")
                    if isinstance(ot, str):
                        ot = datetime.fromisoformat(ot)
                    # Time limit controlado por Config
                    # [SMART TIME LIMIT v118] No cerrar si va ganando (PnL > 0)
                    duration_mins = (datetime.now() - ot).total_seconds() / 60
                    if duration_mins >= Config.MAX_TRADE_DURATION_MINUTES:
                        if (
                            t["pnl"] <= 0
                            or duration_mins >= Config.MAX_TRADE_DURATION_MINUTES * 2
                        ):
                            bot.close_trade(
                                s,
                                f"Time Limit {Config.MAX_TRADE_DURATION_MINUTES}m{' (Force)' if t['pnl'] > 0 else ''}",
                                curr,
                            )
                            continue
                        else:
                            if not t.get("time_limit_warning"):
                                bot.log(
                                    f"⏳ {s}: Superado Time Limit {Config.MAX_TRADE_DURATION_MINUTES}m pero PnL {t['pnl']:.2f}% > 0. Manteniendo..."
                                )
                                t["time_limit_warning"] = True

                    # --- NUEVO: DYNAMIC TRAILING (GHOST SENSITIVE) ---
                    # Si el trade va ganando (>0.5%) pero el Agente Ghost detecta peligro, apretamos a Break Even.
                    # [FIX] Solo activo para RF. LSTM requiere secuencia de 60 velas no disponible en bucle rápido.
                    if (
                        t["pnl"] > 0.5
                        and not t.get("ghost_checked", False)
                        and bot.ghost_model
                        and bot.ghost_model_type == "RF"
                    ):
                        try:
                            # Reconstruimos features rápidas (aproximación para velocidad)
                            snap = t.get("market_snapshot", {})
                            # Actualizamos precio actual en el snapshot para la IA
                            snap["close"] = curr
                            features = Strategy.prepare_ghost_features(
                                snap.get("rsi", 50),
                                snap.get("adx", 20),
                                snap.get("vol_rel", 0),
                            )

                            if hasattr(bot.ghost_model, "predict_proba"):
                                prob = bot.ghost_model.predict_proba(features)[0][1]

                                if (
                                    prob < 0.48
                                ):  # [v118-RELAXED] Umbral bajado de 0.55 a 0.48 para dar aire
                                    bot.log(
                                        f"👻 GHOST ALERT {s}: Probabilidad cayó a {prob:.2f} (Umbral 0.48). Apretando SL a Break Even."
                                    )
                                    t["sl"] = t["entry"] * (
                                        1.001 if t["side"] == "BUY" else 0.999
                                    )  # Asegurar fees
                                    t["ghost_checked"] = (
                                        True  # Solo chequear una vez para no saturar
                                    )
                        except (AttributeError, KeyError, IndexError) as error:
                            if not t.get("ghost_error_logged", False):
                                bot.log(
                                    f"⚠️ GHOST CHECK omitido en {s}: datos/modelo incompleto ({error})"
                                )
                                t["ghost_error_logged"] = True

                    # HARD STOP LOSS: Límite absoluto de pérdida
                    max_loss = (
                        Config.SHADOW_HARD_SL_PERCENT
                        if t.get("is_shadow", False)
                        else Config.REAL_HARD_SL_PERCENT
                    )
                    if t["pnl"] <= max_loss:
                        bot.close_trade(s, f"Hard SL ({max_loss}%)", curr)
                        continue

                    # === [NUEVO v118] TAKE PROFIT ESCALONADO ===
                    # TP1: Cerrar 50% de la posición a +1%
                    if Config.TP1_ENABLED and not t.get("tp1_triggered", False):
                        if t["pnl"] >= Config.TP1_LEVEL:
                            # Cerrar 50% del tamaño
                            close_amount = t.get("size_usd", 0) * (
                                Config.TP1_PERCENT / 100
                            )
                            if close_amount > 0:
                                bot.log(
                                    f"🎯 TP1 HIT: {s} - Cerrando 50% @ +{Config.TP1_LEVEL}%"
                                )
                                # Cerrar posición parcial
                                try:
                                    params = {"reduceOnly": True}
                                    if bot.is_hedge_mode:
                                        params["positionSide"] = (
                                            "LONG" if t["side"] == "BUY" else "SHORT"
                                        )
                                    bot.execution.create_reduce_only_market_order(
                                        s,
                                        "SELL" if t["side"] == "BUY" else "BUY",
                                        close_amount / curr,
                                        params=params,
                                    )
                                except Exception as e:
                                    bot.log(f"⚠️ Error TP1: {e}")

                                # [FIX v118] Marcar siempre como disparado para evitar bucles infinitos en errores de precisión/min_notional
                                t["tp1_triggered"] = True
                                t["size_usd"] = t.get("size_usd", 0) * (
                                    1 - Config.TP1_PERCENT / 100
                                )
                                t["amount"] = t.get("amount", 0) * (
                                    1 - Config.TP1_PERCENT / 100
                                )
                            else:
                                t["tp1_triggered"] = True

                    # TP2: Cerrar resto a +2% con trailing
                    if (
                        Config.TP2_ENABLED
                        and t.get("tp1_triggered", False)
                        and not t.get("tp2_triggered", False)
                    ):
                        if t["pnl"] >= Config.TP2_LEVEL:
                            bot.log(
                                f"🎯 TP2 HIT: {s} - Cerrando resto @ +{Config.TP2_LEVEL}%"
                            )
                            bot.close_trade(s, f"TP2 ({Config.TP2_LEVEL}%)", curr)
                            continue

                    # Stop Loss Dinámico (secundario)
                    if (t["side"] == "BUY" and curr <= t["sl"]) or (
                        t["side"] == "SELL" and curr >= t["sl"]
                    ):
                        bot.close_trade(s, "Dynamic SL", curr)

                except Exception as e:
                    bot.log(f"Guardian error en {s}: {e}")

            # 15s: Sincronización y Trailing pesado
            if time.time() - last_heavy > 15:
                bot.sync_wallet()

                # --- OPTIMIZACIÓN VIP: Primero REALES, luego SHADOW ---
                # Esto evita que el procesamiento de 30 trades shadow bloquee la protección de tu dinero real.
                sorted_trades = sorted(
                    list(bot.active_trades.keys()),
                    key=lambda k: bot.active_trades.get(k, {}).get("is_shadow", True),
                )

                for s in sorted_trades:
                    t = bot.active_trades.get(s)
                    if not t or not t.get("trailing_active"):
                        continue

                    # === [MEJORADO v118] TRAILING STOP DINÁMICO ===
                    # Si TP1 ya fue ejecutado, usar trailing más agresivo
                    if Config.TRAIL_AFTER_TP1 and t.get("tp1_triggered", False):
                        # Trailing más agresivo después del TP1
                        trail_distance = Config.TRAIL_ENTRY_OFFSET  # 0.5%
                        if t["pnl"] >= Config.TP2_LEVEL:
                            trail_distance = 1.0  # [v118-OPTIMIZED] Subido de 0.3 a 1.0 para evitar asfixia post-TP1
                    else:
                        # Trailing normal basado en ATR
                        try:
                            df_main = bot.data_service.fetch_and_update_data(s, "1h")
                            if df_main is None or df_main.empty:
                                continue
                            atr = df_main.ta.atr(length=14).iloc[-1]
                            # FIX: Multiplicar por LEVERAGE para comparar peras con peras (PnL vs Distancia)
                            leverage_ref = 5  # Referencia estándar
                            dist = (
                                (atr / t["entry"])
                                * 100
                                * Config.TRAILING_ATR_MULTIPLIER
                                * leverage_ref
                            )
                            trail_distance = dist
                        except Exception:
                            trail_distance = Config.TRAILING_ACTIVATION_PNL

                    # Usamos Config.TRAILING_ACTIVATION_PNL para consistencia
                    if (
                        t["pnl"] <= (t.get("peak_pnl", 0) - trail_distance)
                        and t["pnl"] > Config.TRAILING_ACTIVATION_PNL
                    ):
                        bot.close_trade(s, "Trailing (ATR)", t["last_price"])
                    # NUEVO: Protección de breakeven para trades con buen profit
                    if t["pnl"] > Config.TRAILING_BREAKEVEN_PNL and t["pnl"] <= (
                        t.get("peak_pnl", 0) - Config.TRAILING_BREAKEVEN_PULLBACK
                    ):
                        bot.close_trade(
                            s,
                            "Trailing (Breakeven Protection)",
                            t["last_price"],
                        )
                last_heavy = time.time()

        except Exception as e:
            bot.log(f"Err Guardián: {e}")

        # MODULACIÓN DE FRECUENCIA v118: 0.1s para dominio < 500ms (trades activos), 2s tranquilo
        sleep_for = 0.1 if bot.active_trades else 2.0
        work_s = max(time.perf_counter() - loop_started, 0.0)
        bot._guardian_stats["loops"] += 1
        bot._guardian_stats["work_s"] += work_s
        bot._guardian_stats["sleep_s"] += sleep_for
        time.sleep(sleep_for)
