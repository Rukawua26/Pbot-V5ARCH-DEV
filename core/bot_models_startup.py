import os
import pickle
import platform
import sys
import threading
import ctypes

import ccxt
import joblib
import pandas as pd

from notifier import send_telegram_msg


def init_models_and_startup_tasks(
    bot, export_dataset_fn, backup_database_fn, tf_module
):
    import sklearn

    bot.log(
        f"🐍 Python: {platform.python_version()} | CCXT: {ccxt.__version__} | Pandas: {pd.__version__} | Sklearn: {sklearn.__version__}"
    )

    if sys.platform == "win32" and not ctypes.windll.shell32.IsUserAnAdmin():
        bot.log(
            "⚠️ ADVERTENCIA: Ejecutando sin permisos de Administrador. Algunas funciones de sistema pueden fallar."
        )

    threading.Thread(target=bot._websocket_monitor, daemon=True).start()

    try:
        model_path = os.path.join("models", "lstm_model.h5")
        scaler_path = os.path.join("models", "scaler.pkl")
        pro_model_path = "ghost_brain_pro.pkl"
        advanced_model_path = "ghost_brain_advanced.pkl"

        if os.path.exists(advanced_model_path):
            try:
                with open(advanced_model_path, "rb") as file_obj:
                    bot.ghost_model = pickle.load(file_obj)
                bot.ghost_model_type = "ADVANCED_ENSEMBLE"
                bot.log(
                    "👻 Agente Ghost (Advanced Ensemble v118): Sistema avanzado cargado."
                )
                bot.log(
                    f"   📊 Features: {len(bot.ghost_model.get('general', {}).get('feature_cols', []))}"
                )
                bot.log(
                    f"   🎯 Modelos: General + {len(bot.ghost_model.get('regime', {}))} regímenes + {len(bot.ghost_model.get('sector', {}))} sectores"
                )
                send_telegram_msg("🧠 *IA v118 (Advanced Ensemble) operativa*")
            except Exception as error:
                bot.log(
                    f"⚠️ Error cargando Advanced: {error}, intentando otros modelos..."
                )

        if bot.ghost_model_type == "OFF":
            if os.path.exists(pro_model_path):
                with open(pro_model_path, "rb") as file_obj:
                    bot.ghost_model = pickle.load(file_obj)
                bot.ghost_model_type = "PRO_ENSEMBLE"
                bot.log("👻 Agente Ghost (PRO v2): Ensemble cargado.")
                send_telegram_msg("🧠 *IA Nivel 6 (Ghost Pro Ensemble) operativa*")
            elif (
                tf_module and os.path.exists(model_path) and os.path.exists(scaler_path)
            ):
                bot.ghost_model = tf_module.keras.models.load_model(model_path)
                bot.scaler = joblib.load(scaler_path)
                bot.ghost_model_type = "LSTM"
                bot.log("👻 Agente Ghost (LSTM): Red Neuronal cargada.")
                send_telegram_msg("🧠 *IA Nivel 5 (LSTM Neural Network) operativa*")
            elif os.path.exists("ghost_brain.pkl"):
                with open("ghost_brain.pkl", "rb") as file_obj:
                    bot.ghost_model = pickle.load(file_obj)
                bot.ghost_model_type = "RF"
                bot.log("👻 Agente Ghost (Random Forest): Cerebro cargado.")
                send_telegram_msg("🧠 *IA Nivel 4 (Random Forest) operativa*")
            elif os.path.exists("agent_models.pkl"):
                try:
                    with open("agent_models.pkl", "rb") as file_obj:
                        bot.ghost_model = pickle.load(file_obj)
                    bot.ghost_model_type = "AGENT_MODELS"
                    bot.log(
                        "👻 Agente Ghost (Agent Models): Modelos de agentes cargados."
                    )
                    send_telegram_msg("🧠 *IA (Agent Models) operativa*")
                except Exception as error:
                    bot.log(f"⚠️ Error cargando agent_models.pkl: {error}")
            else:
                bot.log("⚠️ Agente Ghost: No se encontró modelo, usando modo neutral.")
    except Exception as error:
        bot.log(f"❌ Error cargando Agente Ghost: {error}")

    if export_dataset_fn:
        try:
            bot.log("🚀 Ejecutando exportación de Dataset Maestro al inicio...")
            export_dataset_fn()
        except Exception as error:
            bot.log(f"⚠️ Error exportando dataset: {error}")

    if backup_database_fn:
        try:
            bot.log("🛡️ Ejecutando backup de seguridad al inicio...")
            backup_database_fn()
        except Exception as error:
            bot.log(f"⚠️ Error en backup inicial: {error}")

    if bot.ghost_model_type != "OFF":
        bot.log("🧠 Generando reporte de inteligencia inicial...")
        bot.handle_command("/intelligence")
