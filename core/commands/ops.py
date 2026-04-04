import importlib.util
import os
import pickle
import subprocess
import sys
from datetime import datetime

from config import Config
from notifier import send_telegram_msg


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
        "📊 *AUDITORÍA*\n"
        "• `/status`: Estado operativo actual\n"
        "• `/audit_report`: Auditoría últimos 100 trades\n"
        "• `/open`: Ver operaciones abiertas\n"
        "• `/targets`: Ver radar de objetivos\n"
        "• `/signals`: Distribución de señales\n"
        "• `/shadow_stats`: Estadísticas modo Shadow\n"
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
            msg += "💎 *ELITE*\n" + "\n".join([f"• {x}" for x in tiers["ELITE"][:10]]) + "\n\n"
        if tiers["GOLD"]:
            msg += "🥇 *GOLD*\n" + "\n".join([f"• {x}" for x in tiers["GOLD"][:10]]) + "\n\n"
        if tiers["SILVER"]:
            msg += "🥈 *SILVER*\n" + "\n".join([f"• {x}" for x in tiers["SILVER"][:10]]) + "\n"

        if not tiers["ELITE"] and not tiers["GOLD"] and not tiers["SILVER"]:
            msg += "⚪ Solo señales IRON detectadas."

        send_telegram_msg(msg)
        return True

    if text == "/dump_db":
        send_telegram_msg("📦 *EXPORTANDO BASE DE DATOS...*\nEsto puede tomar unos segundos.")
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

def _handle_training_and_maintenance_commands(bot, text: str) -> bool:
    if text in ["/train", "/force_train"]:
        send_telegram_msg("🧠 *FORZANDO ENTRENAMIENTO...*")
        try:
            if importlib.util.find_spec("ghost_trainer") is None:
                send_telegram_msg(
                    "ℹ️ Entrenamiento manual no disponible (`ghost_trainer.py` ausente)."
                )
                return True

            last_mtime = 0
            if os.path.exists("ghost_brain.pkl"):
                last_mtime = os.path.getmtime("ghost_brain.pkl")

            from ghost_trainer import train_ghost_brain

            train_ghost_brain()
            if os.path.exists("ghost_brain.pkl") and os.path.getmtime("ghost_brain.pkl") > last_mtime:
                with open("ghost_brain.pkl", "rb") as handle:
                    bot.ghost_model = pickle.load(handle)
                bot.brain.set_metadata("last_ghost_train", datetime.now())
                bot.mandatory_train_pending = False
                send_telegram_msg("✅ *ÉXITO:* Nuevo cerebro cargado y operativo.")
            else:
                send_telegram_msg(
                    "⚠️ Entrenamiento completado sin cambios (¿Datos insuficientes < 100?)."
                )
        except Exception as error:
            send_telegram_msg(f"❌ Error crítico: {error}")
        return True

    if text == "/evolution":
        send_telegram_msg("🧬 Ejecutando AI Coach para optimizar filtros...")
        try:
            root = os.path.dirname(os.path.dirname(__file__))
            coach_candidates = [
                os.path.join(root, "tools", "ai_coach.py"),
                os.path.join(root, "ai_coach.py"),
            ]
            coach_path = next((path for path in coach_candidates if os.path.exists(path)), None)
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
