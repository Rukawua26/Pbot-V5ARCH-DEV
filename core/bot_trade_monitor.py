import time
from datetime import datetime, timedelta

from strategy import Strategy


def monitor_open_trades(bot):
    """[FASE 3: BAILOUT] Auditoría continua de posiciones abiertas (Inteligencia Activa)."""
    with bot.lock:
        symbols = list(bot.active_trades.keys())

    if not symbols:
        return

    for symbol in symbols:
        try:
            # [v118.6] IGNITION COOLDOWN: No bailouts en los primeros 15 minutos (micro-ruido inicial)
            # Permite que el trade respire y absorba el ruido de ejecución/spread.
            trade = bot.active_trades.get(symbol)
            if not trade:
                continue

            open_time = trade.get("open_time")
            if isinstance(open_time, str):
                open_time = datetime.fromisoformat(open_time)

            if datetime.now() - open_time < timedelta(minutes=15):
                # bot.log(f"⏳ COOLDOWN ({symbol}): Ignorando bailout por juventud del trade.")
                continue
            # 1. Obtener datos frescos (Sello Institucional: solo 1H + 4H)
            df_main = bot.data_service.fetch_and_update_data(symbol, "1h")
            df_4h = bot.data_service.fetch_and_update_data(symbol, "4h")

            if df_main is None or df_main.empty:
                continue

            # 2. Re-evaluar con la IA (Consenso de los 14 Agentes)
            with bot.db_lock:
                res = Strategy.analyze(
                    df_main,
                    df_main,
                    bot.brain,
                    symbol=symbol,
                    ghost_model=bot.ghost_model,
                    scaler=bot.scaler,
                    btc_delta_tf=getattr(
                        bot,
                        "market_btc_change_tf",
                        0.0,
                    ),
                    df_4h=df_4h,
                    funding_rate=0.0,  # Simplificado para monitoreo
                )

            # res return: (signal, mode, exit_price, prob_final, indicators, votos)
            prob_final = res[3]
            duration = datetime.now() - open_time
            elapsed_mins = duration.total_seconds() / 60

            # --- [V118] SMART EXIT: SALIDA POR DEGRADACIÓN ---
            is_degraded, deg_reason = bot.risk_engine.check_signal_integrity(
                trade, prob_final, elapsed_mins
            )

            if is_degraded:
                entry_conf = trade.get("entry_confidence", 0)
                bot.log(
                    f"🚨 DEGRADED EXIT ({symbol}): {deg_reason} | EntryConf: {entry_conf:.1f} -> ExitConf: {prob_final:.1f}"
                )

                # Cierre inmediato ignorando TP/SL mediante ExecutionService
                bot.close_trade(
                    symbol,
                    reason=f"DEGRADED_{deg_reason}",
                    exit_price=float(df_main["close"].iloc[-1]),
                    exit_confidence=prob_final,
                    latency_context={
                        "trigger": "DEGRADED_EXIT",
                        "signal_ts": time.perf_counter(),
                        "entry_conf": entry_conf,
                        "exit_conf": prob_final,
                    },
                )
                continue

        except Exception as error:
            # Solo loguear errores importantes, no spam
            err_str = str(error)
            if "symbol" in err_str.lower() or "not found" in err_str.lower():
                bot.log(
                    f"⚠️ Error monitoreando {symbol}: Símbolo no disponible en Binance"
                )
            else:
                bot.log(f"⚠️ Error monitoreando {symbol}: {error}")
