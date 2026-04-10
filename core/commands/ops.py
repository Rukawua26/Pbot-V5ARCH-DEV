import asyncio
import importlib.util
import json
import os
import pickle
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from config import Config
from notifier import send_telegram_msg
from core.cooldown_state import persist_cooldowns


def _help_message() -> str:
    return (
        "🤖 *SNIPER AI v118 - CENTRO DE MANDO*\n\n"
        "🕒 *MARCO OPERATIVO*\n"
        "• Motor principal: *1H*\n"
        "• Filtro macro: *4H (veto direccional)*\n"
        "• Modo actual: *PAPER/SHADOW*\n"
        "• Ejecución: solo setup institucional (sin 5m/15m)\n\n"
        "🕹️ *CONTROL*\n"
        "• `/on` | `/resume`: Activar sistema\n"
        "• `/off` | `/pause`: Pausar sistema\n"
        "• `/panic`: Cierre de emergencia\n"
        "• `/unquarantine`: Resetear cooldown de pares\n\n"
        "• `/force_clear [PAR]`: Liberar recovery bloqueado con verificación\n\n"
        "📊 *AUDITORÍA*\n"
        "• `/status`: Estado operativo actual\n"
        "• `/audit_report`: Auditoría últimos 100 trades\n"
        "• `/open`: Ver operaciones abiertas\n"
        "• `/targets`: Ver radar de objetivos\n"
        "• `/signals`: Distribución de señales\n"
        "• `/shadow_stats`: Estadísticas modo Shadow\n"
        "• `/sre_intent`: SLA intents 1h/24h\n"
        "• `/tiers`: Señales por Tier\n"
        "• `/top`: Top señales por probabilidad\n"
        "• `/thresholds`: Umbrales actuales del motor 1H\n\n"
        "🔍 *ANÁLISIS*\n"
        "• `/trade_detail [PAR]`: Análisis profundo de un par\n"
        "• `/trade [ID]`: Detalle de trade histórico\n"
        "• `/thinking`: Vetos recientes de la IA\n"
        "• `/watchlist`: Estado de acecho breakout\n"
        "• `/intelligence`: Mapa mental del modelo\n"
        "• `/agents`: Reputación de agentes\n"
        "• `/explain [PAR]`: Explicación en tiempo real\n\n"
        "🧠 *INTELIGENCIA*\n"
        "• `/force_train`: Re-entrenar modelo Ghost\n"
        "• `/evolution`: Ejecutar AI Coach\n"
        "• `/genetic`: Estado motor genético\n"
        "• `/dna [PAR]`: Parámetros genéticos\n\n"
        "⚙️ *SISTEMA*\n"
        "• `/reset`: Reiniciar PnL diario\n"
        "• `/dump_db`: Exportar base de datos\n"
        "• `/test`: Test de notificaciones\n\n"
        "🚫 *COMANDOS BLOQUEADOS EN CUARENTENA*\n"
        "• `/force_shadow` y `/clean`"
    )


def _handle_misc_commands(bot, text: str) -> bool:
    if text == "/sre_intent":
        try:
            events_path = Path("logs/execution_events.jsonl")
            if not events_path.exists():
                send_telegram_msg(
                    "ℹ️ SRE Intent: aún no existe logs/execution_events.jsonl"
                )
                return True

            now_utc = datetime.now(timezone.utc)
            cut_1h = now_utc - timedelta(hours=1)
            cut_24h = now_utc - timedelta(hours=24)

            ack_1h = exp_1h = 0
            ack_24h = exp_24h = 0

            with events_path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except Exception:
                        continue

                    event = str(row.get("event") or "")
                    if event not in {"ENTRY_ORDER_ACK", "INTENT_EXPIRED"}:
                        continue

                    raw_ts = row.get("ts")
                    if not raw_ts:
                        continue
                    try:
                        ts = datetime.fromisoformat(str(raw_ts))
                    except Exception:
                        continue
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=timezone.utc)
                    else:
                        ts = ts.astimezone(timezone.utc)

                    if ts >= cut_24h:
                        if event == "ENTRY_ORDER_ACK":
                            ack_24h += 1
                        else:
                            exp_24h += 1
                    if ts >= cut_1h:
                        if event == "ENTRY_ORDER_ACK":
                            ack_1h += 1
                        else:
                            exp_1h += 1

            ratio_1h = (exp_1h / ack_1h * 100.0) if ack_1h > 0 else 0.0
            ratio_24h = (exp_24h / ack_24h * 100.0) if ack_24h > 0 else 0.0

            def _level(ratio: float) -> str:
                if ratio >= 1.0:
                    return "🚨 CRITICAL"
                if ratio >= 0.5:
                    return "⚠️ WARNING"
                return "✅ OK"

            api_weight_txt = "n/a"
            if getattr(bot, "weight_tracker", None):
                try:
                    st = bot.weight_tracker.get_status()
                    api_weight_txt = (
                        f"{st.get('current_weight', 0)}/{st.get('limit', 2400)} "
                        f"({st.get('usage_pct', 0.0):.1f}%)"
                    )
                except Exception:
                    api_weight_txt = "error"

            send_telegram_msg(
                "🛡️ *SRE INTENT SLA*\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                f"1h  • ACK={ack_1h} EXP={exp_1h} RATIO={ratio_1h:.2f}% {_level(ratio_1h)}\n"
                f"24h • ACK={ack_24h} EXP={exp_24h} RATIO={ratio_24h:.2f}% {_level(ratio_24h)}\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                f"⚖️ API Weight (1m): {api_weight_txt}\n"
                "SLO: warning>=0.5% | critical>=1.0%"
            )
        except Exception as error:
            send_telegram_msg(f"❌ Error /sre_intent: {error}")
        return True

    if text == "/tiers":
        if not bot.scanner_history:
            send_telegram_msg("🕵️ *TIERS:* No hay señales en el radar todavía.")
            return True

        tiers = {"ELITE": [], "GOLD": [], "SILVER": [], "IRON": []}
        for item in bot.scanner_history:
            tier = item.get("tier", "IRON")
            if tier in tiers:
                tiers[tier].append(f"{item['symbol']} ({item['ia_prob']})")

        msg = "🏆 *SEÑALES POR TIER*\n\n"
        if tiers["ELITE"]:
            msg += (
                "💎 *ELITE*\n"
                + "\n".join([f"• {x}" for x in tiers["ELITE"][:10]])
                + "\n\n"
            )
        if tiers["GOLD"]:
            msg += (
                "🥇 *GOLD*\n"
                + "\n".join([f"• {x}" for x in tiers["GOLD"][:10]])
                + "\n\n"
            )
        if tiers["SILVER"]:
            msg += (
                "🥈 *SILVER*\n"
                + "\n".join([f"• {x}" for x in tiers["SILVER"][:10]])
                + "\n"
            )

        if not tiers["ELITE"] and not tiers["GOLD"] and not tiers["SILVER"]:
            msg += "⚪ Solo señales IRON detectadas."

        send_telegram_msg(msg)
        return True

    if text == "/dump_db":
        send_telegram_msg(
            "📦 *EXPORTANDO BASE DE DATOS...*\nEsto puede tomar unos segundos."
        )
        try:
            result = subprocess.run(
                [sys.executable, "export_database.py"],
                capture_output=True,
                text=True,
                timeout=60,
            )
            if result.returncode == 0:
                output = result.stdout
                send_telegram_msg(f"✅ *EXPORTACIÓN COMPLETA*\n{output}")
            else:
                send_telegram_msg(f"❌ Error: {result.stderr}")
        except Exception as error:
            send_telegram_msg(f"❌ Error: {error}")
        return True

    return False


import asyncio


def _handle_training_and_maintenance_commands(bot, text: str) -> bool:
    if text in ["/train", "/force_train"]:
        send_telegram_msg("🧠 *FORZANDO ENTRENAMIENTO...* (Background Process)")

        async def run_training():
            try:
                # 1. Ejecución desacoplada con Prioridad Baja (nice -n 15)
                # Esto evita que el entrenamiento asfixie al bot_guardian
                process = await asyncio.create_subprocess_exec(
                    "nice",
                    "-n",
                    "15",
                    "python3",
                    "tools/ghost_trainer.py",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )

                # 2. Espera no bloqueante del Event Loop
                stdout, stderr = await process.communicate()

                if process.returncode == 0:
                    # 3. Notificación de Disponibilidad (No recarga inmediata)
                    bot.brain.pending_model_update = True
                    send_telegram_msg(
                        "✅ *Entrenamiento completo.* El nuevo modelo está listo.\n"
                        "Esperando ventana segura (0 trades activos) para recarga automática."
                    )
                else:
                    error_msg = stderr.decode().strip()
                    send_telegram_msg(
                        f"❌ Fallo en entrenamiento (Cod {process.returncode}).\n"
                        f"El modelo actual sigue intacto.\nError: {error_msg[-200:]}"
                    )
            except Exception as e:
                send_telegram_msg(f"❌ Error crítico en subproceso: {e}")

        try:
            # [SRE] Puente Threadsafe: Inyección de la corrutina en el loop principal
            asyncio.run_coroutine_threadsafe(run_training(), bot.main_loop)
            send_telegram_msg("⚙️ Solicitud de entrenamiento enviada al Loop Principal.")
        except Exception as e:
            send_telegram_msg(f"❌ Error al delegar entrenamiento: {e}")

        return True

    if text == "/evolution":
        send_telegram_msg("🧬 Ejecutando AI Coach para optimizar filtros...")
        try:
            root = os.path.dirname(os.path.dirname(__file__))
            coach_candidates = [
                os.path.join(root, "tools", "ai_coach.py"),
                os.path.join(root, "ai_coach.py"),
            ]
            coach_path = next(
                (path for path in coach_candidates if os.path.exists(path)), None
            )
            if not coach_path:
                send_telegram_msg("ℹ️ AI Coach no disponible en este entorno.")
                return True

            subprocess.run([sys.executable, coach_path], check=False, timeout=900)
            send_telegram_msg("🚀 Evolución completada. Parámetros ajustados.")
        except Exception as error:
            send_telegram_msg(f"❌ Error evolución: {error}")
        return True

    if text == "/genetic":
        send_telegram_msg(
            "🧬 *INICIANDO MOTOR GENÉTICO...*\nAnalizando supervivencia de especies y mutando parámetros SL/TP..."
        )
        try:
            subprocess.Popen([sys.executable, "tools/genetic_engine.py"])
        except Exception as error:
            send_telegram_msg(f"❌ Error iniciando motor genético: {error}")
        return True

    if text == "/force_shadow":
        send_telegram_msg(
            "⛔ *Comando deshabilitado.*\nEl bot opera en cuarentena controlada y no permite alternar modo por Telegram."
        )
        return True

    if text.startswith("/explain"):
        parts = text.split()
        if len(parts) < 2:
            send_telegram_msg("⚠️ Uso: /explain [SYMBOL] (ej: /explain BTC/USDT)")
            return True

        sym = parts[1].upper()
        send_telegram_msg(f"🧠 *ANALIZANDO {sym}...*")
        try:
            from strategy import Strategy

            df_main = bot.data_service.fetch_and_update_data(sym, "1h")
            df_4h = bot.data_service.fetch_and_update_data(sym, "4h")

            if df_main is None or df_main.empty:
                send_telegram_msg("❌ No hay datos suficientes para explicar.")
                return True

            res = Strategy.analyze(
                df_main,
                df_main,
                bot.brain,
                symbol=sym,
                ghost_model=bot.ghost_model,
                scaler=bot.scaler,
                btc_delta_tf=getattr(bot, "market_btc_change_tf", 0.0),
                df_4h=df_4h,
            )
            _, _, _, prob, ind, votos = res

            msg = (
                f"🧐 *EXPLICACIÓN IA: {sym}*\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"🎯 *Score Final:* {prob:.1f}/100\n"
                f"👻 *IA (G):* {votos.get('G', 50):.1f}%\n"
                f"📈 *Tendencia (MT):* {votos.get('MT', 50):.1f}%\n"
                f"🧱 *Estructura (SR):* {votos.get('SR', 50):.1f}%\n\n"
                f"📊 *Factores Clave:*\n"
                f"• RSI: {ind['rsi']['val']:.1f}\n"
                f"• ADX: {ind['adx']['val']:.1f}\n"
                f"• Z-Score: {ind.get('z_score', 0):.2f}"
            )
            send_telegram_msg(msg)
        except Exception as error:
            send_telegram_msg(f"❌ Error explicando: {error}")
        return True

    if text == "/archive":
        backup_file = bot.brain.rotate_history()
        send_telegram_msg(f"📦 DB Optimizada. Historial movido a: {backup_file}")
        return True

    if text == "/clean":
        send_telegram_msg(
            "⛔ *Comando deshabilitado.*\nSe bloquea limpieza destructiva durante operación para proteger historial y continuidad."
        )
        return True

    if text == "/unquarantine":
        try:
            with bot.lock:
                cooldown_count = len(bot.cooldown_pairs)
                bot.cooldown_pairs.clear()
                if hasattr(bot, "cooldown_deadlines_mono"):
                    bot.cooldown_deadlines_mono.clear()
            persist_cooldowns(bot)

            blacklist_count = 0
            if hasattr(bot, "risk_engine") and bot.risk_engine is not None:
                if hasattr(bot.risk_engine, "temp_blacklist"):
                    blacklist_count = len(bot.risk_engine.temp_blacklist)
                    bot.risk_engine.temp_blacklist.clear()
                if hasattr(bot.risk_engine, "symbol_streaks"):
                    bot.risk_engine.symbol_streaks.clear()

            send_telegram_msg(
                f"🔓 *COOLDOWNS RESETEADOS*\n\n"
                f"• Cooldowns de pares liberados: {cooldown_count}\n"
                f"• Blacklist anti-revenge liberada: {blacklist_count}\n"
                f"• Estado: listo para re-evaluación inmediata"
            )
        except Exception as error:
            send_telegram_msg(f"❌ Error reseteando cooldowns: {error}")
        return True

    if text.startswith("/force_clear"):
        parts = text.split(maxsplit=1)
        if len(parts) < 2:
            send_telegram_msg(
                "⚠️ Uso: /force_clear [SYMBOL] (ej: /force_clear BTC/USDT)"
            )
            return True

        symbol = parts[1].strip().upper().replace(":USDT", "")
        if "/" not in symbol:
            if symbol.endswith("USDT") and len(symbol) > 4:
                symbol = f"{symbol[:-4]}/USDT"
            else:
                symbol = f"{symbol}/USDT"

        try:
            with bot.lock:
                state = dict((bot.active_trades or {}).get(symbol) or {})

            if not state:
                send_telegram_msg(
                    f"ℹ️ {symbol}: no existe estado activo local para limpiar."
                )
                return True

            entry_coid = str(state.get("entry_client_order_id") or "")
            order_found = False
            position_found = False

            try:
                for order in bot.execution.fetch_open_orders(symbol) or []:
                    if not isinstance(order, dict):
                        continue
                    if str(order.get("clientOrderId") or "") == entry_coid:
                        order_found = True
                        break
                if not order_found and entry_coid:
                    lookup = getattr(bot.execution, "fetch_order_by_client_id", None)
                    if callable(lookup):
                        found = lookup(symbol, entry_coid)
                        order_found = isinstance(found, dict)
            except Exception:
                order_found = False

            try:
                for pos in bot.execution.fetch_positions() or []:
                    if not isinstance(pos, dict):
                        continue
                    norm = str(pos.get("symbol") or "").replace(":USDT", "")
                    if norm != symbol:
                        continue
                    if abs(float(pos.get("contracts") or 0.0)) > 0:
                        position_found = True
                        break
            except Exception:
                position_found = False

            if order_found or position_found:
                send_telegram_msg(
                    f"🛑 /force_clear cancelado en {symbol}: hay evidencia en Exchange "
                    f"(open_order={int(order_found)} position={int(position_found)}). "
                    "Ejecuta reconciliación, no limpieza manual."
                )
                return True

            with bot.db_lock:
                bot.brain.delete_active_trade_state(symbol)
            with bot.lock:
                bot.active_trades.pop(symbol, None)

            send_telegram_msg(
                f"🧹 FORCE CLEAR aplicado en {symbol}. Estado local y DB liberados sin evidencia en Exchange."
            )
        except Exception as error:
            send_telegram_msg(f"❌ Error en /force_clear {symbol}: {error}")
        return True

    if text == "/thresholds":
        msg = (
            f"🎯 *UMBRALES DE IA (1H)*\n\n"
            f"*Shadow Trades:*\n"
            f"• Rango/Neutral: {Config.SHADOW_MIN_PROBABILITY_RANGE}%\n"
            f"• Tendencia: {Config.SHADOW_MIN_PROBABILITY_TREND}%\n\n"
            f"*Real Trades:*\n"
            f"• Umbral Mínimo: {Config.REAL_CONFIDENCE_MIN * 100}%\n\n"
            f"*Sentimiento Actual:*\n"
            f"• {bot.current_sentiment[0]}\n\n"
            f"_Umbrales más bajos = Más exploración_"
        )
        send_telegram_msg(msg)
        return True

    return False
