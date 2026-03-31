#!/usr/bin/env python3
"""
SNIPER AI v117 - COMMANDER (NEURAL CONSENSUS)
====================================
- Versión unificada v117.
- 14 Agentes con Red Neuronal de Consenso (incl. K Whale Tracker).
- Sistema de Tiers ELITE/GOLD/SILVER con visualización en RADAR.
- SL/TP Dinámico Multi-TF + Indulto BTC Gradual.
"""

import time
from datetime import datetime, timedelta
import sys
from functools import lru_cache
import threading
import shutil
import os
import pickle
import ctypes
import platform
import json
import joblib
import concurrent.futures  # [V115-PRO] Para paralelismo total
import sqlite3
from collections import Counter
from typing import Dict, List, Tuple, Optional, Any
import logging
from logging.handlers import RotatingFileHandler
import signal
import fcntl

import warnings

warnings.filterwarnings("ignore", category=UserWarning, module="sklearn")

import ccxt
import pandas as pd
import requests

try:
    import pyarrow

    HAS_PARQUET = True
except ImportError:
    HAS_PARQUET = False

import pandas_ta as ta

try:
    import tensorflow as tf
except ImportError:
    tf = None

from config import Config
from strategy import Strategy
from ui import UI
from learning import Brain, shadow_logger
from notifier import send_telegram_msg, send_telegram_photo
from ws_manager import BinanceWebSocket
from crash_predictor import CrashPredictor
from core.execution_service import ExecutionService
from core.risk_engine import RiskEngine
from core.data_service import DataService
from core.types import SignalContext  # [V116] Type Fortification
from core.strategy.shocks import next_shock_distance_pct

try:
    from export_master_dataset import export_dataset
except ImportError:
    export_dataset = None

# --- [V115-PRO] CONFIGURACIÓN PROFESIONAL DE LOGS ---
logger = logging.getLogger("SniperAI")
logger.setLevel(logging.INFO)
log_handler = RotatingFileHandler(
    Config.LOG_FILE,
    maxBytes=10 * 1024 * 1024,  # 10MB por archivo
    backupCount=5,  # Mantener 5 backups (50MB total)
    encoding="utf-8",
)
log_formatter = logging.Formatter(
    "%(asctime)s | %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
)
log_handler.setFormatter(log_formatter)
logger.addHandler(log_handler)


def _backup_database_placeholder():
    """Realiza backup de los archivos críticos del bot"""
    backup_dir = "backups"
    os.makedirs(backup_dir, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_subdir = os.path.join(backup_dir, f"backup_{timestamp}")
    os.makedirs(backup_subdir, exist_ok=True)

    files_to_backup = [
        "sniper_brain.db",
        "ghost_brain.pkl",
        "ghost_brain_advanced.pkl",
        "agent_consensus_nn.pkl",
        "agent_models.pkl",
        "scaler.pkl",
    ]

    backed_up = []
    for f in files_to_backup:
        if os.path.exists(f):
            try:
                shutil.copy2(f, backup_subdir)
                backed_up.append(f)
            except Exception as e:
                print(f"⚠️ Error respaldando {f}: {e}")

    if backed_up:
        print(f"✅ Backup creado: {backup_subdir}")
        return backup_subdir
    else:
        print("⚠️ No hay archivos para respaldar")
        return None


backup_database = _backup_database_placeholder

try:
    import dashboard
except (ImportError, ModuleNotFoundError) as e:
    print(f"⚠️ Dashboard no disponible: {e}")
    dashboard = None

try:
    from ml_monitor import MLMonitor

    ML_MONITOR_AVAILABLE = True
except ImportError:
    ML_MONITOR_AVAILABLE = False
    MLMonitor = None
    print("⚠️ ML Monitor no disponible")


class Bot:
    def __init__(self):
        self.ui = UI()
        self.brain = Brain()

        # --- [V116-ULTIMATE] SERVICIOS DESACOPLADOS ---
        self.execution = ExecutionService(
            Config.BINANCE_API_KEY, Config.BINANCE_API_SECRET
        )
        self.data_service = DataService(self.execution.exchange)
        self.risk_engine = RiskEngine(self.brain)
        self.crash_predictor = self.risk_engine.crash_predictor  # Retrocompatibilidad

        self.active_trades = {}
        self.scanner_history = []
        self.logs = []
        self.balance = 0.0
        self.available_balance = 0.0  # Inicializar variable crítica
        self.pairs_to_scan = []
        self.is_running = True
        self.stop_requested = False
        self.init_complete = threading.Event()
        self._api_weight_counter = 0  # [DEBUG] Contador de peso API
        self._api_weight_logged = False  # [DEBUG] Flag para logging por minuto
        self._funding_rate_cache = {}  # Cache: symbol → (rate, timestamp)
        self._funding_cache_ttl = 300  # 5 min (funding cambia cada 8h)
        self._btc_data_cache = None  # Cache de datos BTC por ciclo
        self._btc_data_cache_ts = 0  # Timestamp del cache BTC
        self.lock = threading.Lock()
        self.price_lock = threading.Lock()  # Lock para proteger self.live_prices
        self.db_lock = (
            threading.RLock()
        )  # [FIX] RLock para evitar deadlocks en llamadas anidadas
        self.is_hedge_mode = False
        self.ghost_model = None
        self.ghost_model_type = "OFF"
        self.scaler = None
        self.risk_multiplier = 1.0
        self.blacklist = {}
        self.cooldown_pairs = {}
        self.restricted_hours = []
        self.restricted_sectors = []
        self.restricted_symbols = []  # [v114] Símbolos con mal rendimiento
        self.circuit_breaker_active = False
        self.pause_time = None
        self.is_paused = False  # Para el Circuit Breaker v104.0
        self.btc_panic = False
        self.mandatory_train_pending = False
        self.force_btc_panic = False
        self.api_status = "🟡 PENDING"
        self.force_chaos_mode = False  # Simulación de Caos
        self.ai_status_msg = "INICIANDO..."
        self.dynamic_offset = 0.0
        self.peak_pnl = 0.0
        self.daily_initial_balance = 0.0  # Inicializar variable
        self.current_target = Config.DAILY_GOALS[0]
        self.user_notes = (
            "Escribe tus notas aquí..."  # Notas persistentes para Dashboard
        )
        self.global_rag_impact = 0.0  # Métrica de influencia RAG

        # --- CARGA DE CACHÉ ---
        self.data_service.load_cache()

        # --- [DEV] WS L2 Anti-Slippage Shield ---
        # [DINÁMICO] Iniciamos vacío, el triaje actualizará los símbolos dinámicamente
        self.ws_manager = BinanceWebSocket(symbols=[])
        self.ws_manager.start_background()

        # --- ML MONITORING (v1.0) ---
        if ML_MONITOR_AVAILABLE and MLMonitor is not None:
            self.ml_monitor = MLMonitor("models")
            self._init_ml_monitoring()
        else:
            self.ml_monitor = None

        # --- RESTAURACIÓN DE ESTADO (PERSISTENCIA) ---
        try:
            restored = self.brain.load_active_trade_states()
            if restored:
                self.active_trades = restored
                self.log(f"💾 Restaurados {len(restored)} trades activos desde DB.")
        except Exception as e:
            self.log(f"⚠️ Error restaurando trades: {e}")

        threading.Thread(target=self._heartbeat_loop, daemon=True).start()

        self.cache_dir = "data_storage/candles"
        os.makedirs(self.cache_dir, exist_ok=True)
        self.market_btc_price = 0.0
        self.live_prices = {}  # Precios en tiempo real vía Websocket
        self.current_sentiment = ("⚪ ANALIZANDO...", "white")
        self.last_ohlcv_fetch = {}  # [OPTIMIZACIÓN] Rastrear última descarga por par
        self.last_train_date = self.brain.get_last_train_timestamp()
        self.ml_healthy = True  # [v114] ML Health Veto
        self.last_radar_update = (
            time.time()
        )  # [v114] Heartbeat: Rastrear frescura del radar

        # --- [V116] ATRIBUTOS DE ESTADO Y TELEMETRÍA ---
        self._weekly_sent = False
        self._vol_ema = {}
        self._snapshot_tickers = {}
        self.last_ml_health_check = time.time()
        self.last_perf_check = time.time()
        self.last_panic_alert = 0
        self.last_ml_confidence = 75.0
        self.last_ghost_weight = 1.0
        self.ml_performance = {}
        self.last_signal_stats = {}
        self._last_sort_time = 0
        self.last_market_update = 0
        self.last_pm_check = 0
        self.day_report_sent = False
        self.daily_backup_done = False
        self.last_cache_save = time.time()
        self._api_weight_logged_time = time.time()

        # --- Observabilidad de runtime (profiling 24h) ---
        self._perf_start_ts = time.time()
        self._perf_start_rss_mb = 0.0
        self._perf_h1_logged = False
        self._perf_h24_logged = False
        self._guardian_stats = {
            "loops": 0,
            "work_s": 0.0,
            "sleep_s": 0.0,
            "bailout_count": 0,
        }
        self._secondary_scan_due_at = 0.0
        self._last_weekly_maintenance_utc = None

        # --- [V115-PRO] CIRCUIT BREAKER DE LATENCIA (QoE) ---
        self.latency_quarantine = {}  # {symbol: release_timestamp}

        # --- Cargar Modelo de IA (Nivel 5) ---
        import sklearn

        self.log(
            f"🐍 Python: {platform.python_version()} | CCXT: {ccxt.__version__} | Pandas: {pd.__version__} | Sklearn: {sklearn.__version__}"
        )

        # --- FASE 1: PERMISOS DE ADMINISTRADOR ---
        if sys.platform == "win32" and not ctypes.windll.shell32.IsUserAnAdmin():
            self.log(
                "⚠️ ADVERTENCIA: Ejecutando sin permisos de Administrador. Algunas funciones de sistema pueden fallar."
            )

        threading.Thread(target=self._websocket_monitor, daemon=True).start()

        # --- CARGA DE MODELOS DE IA (Prioridad: Advanced > PRO > LSTM > RF) ---
        try:
            model_path = os.path.join("models", "lstm_model.h5")
            scaler_path = os.path.join("models", "scaler.pkl")
            pro_model_path = "ghost_brain_pro.pkl"
            advanced_model_path = "ghost_brain_advanced.pkl"

            # 0. PRIORIDAD MÁXIMA: Cargar Advanced Learning System (v114)
            if os.path.exists(advanced_model_path):
                try:
                    with open(advanced_model_path, "rb") as f:
                        self.ghost_model = pickle.load(f)
                    self.ghost_model_type = "ADVANCED_ENSEMBLE"
                    self.log(
                        "👻 Agente Ghost (Advanced Ensemble v114): Sistema avanzado cargado."
                    )
                    self.log(
                        f"   📊 Features: {len(self.ghost_model.get('general', {}).get('feature_cols', []))}"
                    )
                    self.log(
                        f"   🎯 Modelos: General + {len(self.ghost_model.get('regime', {}))} regímenes + {len(self.ghost_model.get('sector', {}))} sectores"
                    )
                    send_telegram_msg("🧠 *IA v114 (Advanced Ensemble) operativa*")
                except Exception as e:
                    self.log(
                        f"⚠️ Error cargando Advanced: {e}, intentando otros modelos..."
                    )

            # Si no hay Advanced, intentar otros modelos en orden de prioridad
            if self.ghost_model_type == "OFF":
                # 1. Intentar cargar GHOST PRO (Nivel 6 - Ensemble)
                if os.path.exists(pro_model_path):
                    with open(pro_model_path, "rb") as f:
                        self.ghost_model = pickle.load(f)
                    self.ghost_model_type = "PRO_ENSEMBLE"
                    self.log("👻 Agente Ghost (PRO v2): Ensemble cargado.")
                    send_telegram_msg("🧠 *IA Nivel 6 (Ghost Pro Ensemble) operativa*")

                # 2. Intentar cargar LSTM (Nivel 5 - Requiere TensorFlow)
                elif tf and os.path.exists(model_path) and os.path.exists(scaler_path):
                    self.ghost_model = tf.keras.models.load_model(model_path)
                    self.scaler = joblib.load(scaler_path)
                    self.ghost_model_type = "LSTM"
                    self.log("👻 Agente Ghost (LSTM): Red Neuronal cargada.")
                    send_telegram_msg("🧠 *IA Nivel 5 (LSTM Neural Network) operativa*")

                # 3. Si falla, intentar cargar Random Forest (Nivel 4)
                elif os.path.exists("ghost_brain.pkl"):
                    with open("ghost_brain.pkl", "rb") as f:
                        self.ghost_model = pickle.load(f)
                    self.ghost_model_type = "RF"
                    self.log("👻 Agente Ghost (Random Forest): Cerebro cargado.")
                    send_telegram_msg("🧠 *IA Nivel 4 (Random Forest) operativa*")

                # 4. [V115-PRO]Fallback: Intentar cargar agent_models.pkl
                elif os.path.exists("agent_models.pkl"):
                    try:
                        with open("agent_models.pkl", "rb") as f:
                            self.ghost_model = pickle.load(f)
                        self.ghost_model_type = "AGENT_MODELS"
                        self.log(
                            "👻 Agente Ghost (Agent Models): Modelos de agentes cargados."
                        )
                        send_telegram_msg("🧠 *IA (Agent Models) operativa*")
                    except Exception as e:
                        self.log(f"⚠️ Error cargando agent_models.pkl: {e}")

                else:
                    self.log(
                        "⚠️ Agente Ghost: No se encontró modelo, usando modo neutral."
                    )
        except Exception as e:
            self.log(f"❌ Error cargando Agente Ghost: {e}")

        # --- EJECUCIÓN AUTOMÁTICA DE EXPORTACIÓN (SOLICITUD USUARIO) ---
        if export_dataset:
            try:
                self.log("🚀 Ejecutando exportación de Dataset Maestro al inicio...")
                export_dataset()
            except Exception as e:
                self.log(f"⚠️ Error exportando dataset: {e}")

        # --- BACKUP DE SEGURIDAD AL INICIO ---
        if backup_database:
            try:
                self.log("🛡️ Ejecutando backup de seguridad al inicio...")
                backup_database()
            except Exception as e:
                self.log(f"⚠️ Error en backup inicial: {e}")

        # --- REPORTE DE INTELIGENCIA INICIAL ---
        if self.ghost_model_type != "OFF":
            self.log("🧠 Generando reporte de inteligencia inicial...")
            self.handle_command("/intelligence")

    def render_consensus_telemetry(self, symbol, p_final, modo, votos, regime=None):
        """Muestra telemetría del consenso TRINITY (MT/SR/G)."""
        if modo == "NONE" and p_final < 40:
            return

        icon = "🔥 REAL" if modo == "REAL" else "🧪 SHADOW"
        regime_icon = (
            f" | 🌪️ {regime}"
            if regime == "CHAOS"
            else (f" | 🌊 {regime}" if regime else "")
        )

        def get_icon(score):
            if score >= 70:
                return "🟢"
            if score <= 30:
                return "🔴"
            return "🟡"

        dna_list = [get_icon(votos.get(k, 50)) for k in ["MT", "SR", "G"]]
        dna_str = f"[ {' '.join(dna_list)} ]"

        msg = (
            f"📡 {symbol} {icon}{regime_icon} | Prob: {p_final:.1f}% | DNA: {dna_str}\n"
            f"   👻 IA:{votos.get('G', 50):.0f}% 📈 TEND:{votos.get('MT', 50):.0f}% 🧱 ESTR:{votos.get('SR', 50):.0f}%"
        )
        self.log(msg)

    def _load_ai_restrictions(self):
        """Carga las listas negras generadas por el AI Coach desde la BD."""
        try:
            self.restricted_hours = self.brain.get_hourly_blacklist()
            self.restricted_sectors = self.brain.get_sector_blacklist()
            if self.restricted_hours or self.restricted_sectors:
                self.log("🧠 Restricciones del AI Coach cargadas.")
                if self.restricted_hours:
                    self.log(f"   - 🚫 Horas vetadas: {self.restricted_hours}")
                if self.restricted_sectors:
                    self.log(f"   - 🚫 Sectores vetados: {self.restricted_sectors}")
        except Exception as e:
            self.log(f"⚠️ Error cargando restricciones del AI Coach: {e}")

    def get_audit_verdict(
        self,
        symbol,
        prob_ia,
        signal,
        ob_status,
        pnl_hoy,
        meta_actual,
        mode="NONE",
        ctx: Optional[SignalContext] = None,
    ):
        """Analiza todos los filtros y devuelve la razón exacta del estado actual (v114 - UMBRALES CORREGIDOS)."""
        if signal not in ["BUY", "SELL"]:
            return "⏳ ESPERANDO TÉCNICA"

        # === [NUEVO v114] FILTROS DE ENTRADA Y CONCESIONES ===
        if ctx:
            filter_veto = ctx.get("filter_veto")
            if filter_veto:
                return f"⛔ VETO: {filter_veto}"

            # Concesión granular desde Strategy.analyze
            # [v114 FIX] Las concesiones son INFORMATIVOS, NO son vetos duros.
            # Un trade con prob >= 75 Y veto_reason NO debería ser bloqueado;
            # la probabilidad alta significa que el consenso de 14 agentes superó
            # los warnings individuales. Solo reportar como concesión, no bloquear.
            strategy_warnings = ctx.get("veto_reason")
            if strategy_warnings and prob_ia * 100 < 75:
                # Bajo 75%: reportar el riesgo y dejar que el umbral decida
                return f"⚠️ RIESGO: {strategy_warnings}"
            # Si prob >= 75, ignorar los warnings — el consenso los superó

        ia_percent = prob_ia * 100

        # --- VETO DE SEGURIDAD (PRIORIDAD MÁXIMA) ---
        # 1. DISPARO REAL: Confianza alta (basado en Config.REAL_CONFIDENCE_MIN)
        real_min = Config.REAL_CONFIDENCE_MIN * 100
        shadow_min = float(
            getattr(Config, "SHADOW_MODE_MIN", Config.SHADOW_PROB_MIN * 100)
        )

        # VETO DE SEGURIDAD: Si ya llegamos a la meta, todo es SHADOW
        if pnl_hoy >= meta_actual:
            return f"🧪 SHADOW (META {pnl_hoy:.2f}%)"

        if ia_percent >= real_min:
            # Si la estrategia dice que es modo SHADOW (por técnica), respetamos
            if mode == "SHADOW":
                return f"🧪 SHADOW (TÉCNICA LIMITADA)"

            # VETO REAL: Solo si BTC cae y es compra
            if self.current_sentiment[0] == "🔴 TENDENCIA BAJISTA" and signal == "BUY":
                return "⛔ VETO: TENDENCIA BTC (PROTECCIÓN REAL)"
            return f"🚀 OK: REAL ({ia_percent:.1f}% | OB:{ob_status})"

        # 2. MODO SHADOW: EXPLORADOR
        if ia_percent >= shadow_min:
            return f"👻 SHADOW (IA {ia_percent:.1f}% | {shadow_min}-{real_min - 1}%)"

        return f"❌ VETO: BAJA PROB IA ({ia_percent:.1f}%)"

    def update_radar(
        self,
        symbol,
        decision,
        prob_ia,
        ob_status,
        audit_verdict,
        ctx: Optional[SignalContext],
        votos=None,
        response_ms=-1,
    ):
        """Sincroniza los iconos del Radar basados en el modo de la estrategia (v106.5)."""
        mode = decision["mode"]

        # El Fuego (🔥) es la validación final del consenso para dinero REAL
        fuego_status = (
            "✅" if mode == "REAL" and prob_ia >= Config.REAL_CONFIDENCE_MIN else "❌"
        )

        shadow_min_pct = float(
            getattr(Config, "SHADOW_MODE_MIN", Config.SHADOW_PROB_MIN * 100)
        )

        # El Tubo (🧪) indica si el bot está aprendiendo de esta moneda (Real o Shadow)
        tubo_status = (
            "✅"
            if mode in ["REAL", "SHADOW"] and prob_ia >= (shadow_min_pct / 100.0)
            else "❌"
        )

        # Perfil Táctico
        symbol_sector = next(
            (
                k
                for k, v in Config.SECTORS.items()
                if any(s.lower() in symbol.split("/")[0].lower() for s in v)
            ),
            "OTHE",
        )
        atr_val = ctx.get("atr_pct", 0) * 100 if ctx else 0
        atr_icon = "⚡" if atr_val > 3.0 else ("🐢" if atr_val < 1.0 else "📊")
        tactical_view = f"{symbol_sector} | {atr_icon} {atr_val:.1f}%"

        # [FIX] Evitar duplicados: Si el símbolo ya está, lo quitamos para poner el nuevo al inicio
        self.scanner_history = [
            s for s in self.scanner_history if s["symbol"] != symbol
        ]

        # Limpieza de redundancia visual (Solicitud Usuario)
        # Quitamos "SHADOW" o "REAL" del texto ya que existe columna de MODO
        display_verdict = audit_verdict
        for tag in {"🧪 SHADOW", "🚀 OK: REAL", "🧪", "🚀"}:
            display_verdict = display_verdict.replace(tag, "")
        display_verdict = display_verdict.strip()

        # [CIRUGÍA LÁSER] Visualización de Posiciones Activas en Radar
        if symbol in self.active_trades:
            display_verdict = f"⚡ OPEN | {display_verdict}"

        # Obtener información de patrones
        pattern_type = "NEW"
        wr_hist = 0
        try:
            elite_patterns = self.brain.get_elite_patterns()
            exp_patterns = self.brain.get_experimental_patterns()
            base = symbol.split("/")[0]

            for p in elite_patterns:
                if base in p.get("symbol", ""):
                    pattern_type = "ELITE"
                    wr_hist = p.get("win_rate", 0)
                    break
            if pattern_type == "NEW":
                for p in exp_patterns:
                    if base in p.get("symbol", ""):
                        pattern_type = "EXP"
                        wr_hist = p.get("win_rate", 0)
                        break
        except:
            pass

        self.scanner_history.insert(
            0,
            {
                "symbol": symbol,
                "sector": symbol_sector,
                "tech_checklist": tactical_view,
                "ob": ob_status,
                "ia_prob": f"{prob_ia * 100:.1f}%" if prob_ia > 0 else "---",
                "ia_shadow": tubo_status,
                "ia_real": fuego_status,
                "result": display_verdict,
                "signal": decision["signal"],
                "side": decision["signal"],
                "rsi_val": (
                    int(ctx.get("rsi", {}).get("val", 0))
                    if isinstance(ctx.get("rsi"), dict)
                    else (
                        int(ctx.get("rsi", 0))
                        if isinstance(ctx.get("rsi", 0), (int, float))
                        else 0
                    )
                )
                if ctx
                else 0,
                "adx_val": (
                    int(ctx.get("adx", {}).get("val", 0))
                    if isinstance(ctx.get("adx"), dict)
                    else (
                        int(ctx.get("adx", 0))
                        if isinstance(ctx.get("adx", 0), (int, float))
                        else 0
                    )
                )
                if ctx
                else 0,
                "z_score": ctx.get("z_score", 0.0) if ctx else 0.0,
                "vol_24h": ctx.get("vol_24h", 0.0) if ctx else 0.0,
                "trend_val": ctx.get("trend", "N/A") if ctx else "N/A",
                "funding_rate": ctx.get("funding_rate", 0.0) if ctx else 0.0,
                "tier": decision.get("tier", ctx.get("tier", "IRON"))
                if ctx
                else "IRON",
                "votos": votos or {},
                "pattern_type": pattern_type,
                "wr_hist": wr_hist,
                "ml_score": prob_ia * 100 if prob_ia > 0 else -1,
                # [V115-PRO] Calidad de Ejecución: tiempo de respuesta en ms (-1 = no medido)
                "response_ms": response_ms,
            },
        )

        # Limitar historial a 100 elementos para evitar memory leak
        if len(self.scanner_history) > 100:
            self.scanner_history = self.scanner_history[:100]

    def self_adjust_exigency(self):
        """La IA analiza su éxito reciente y ajusta su propia dificultad (v105.6)."""
        with self.db_lock:
            stats = self.brain.get_stats()
        # Obtenemos el Win Rate de los últimos 50 trades shadow
        recent_swr = stats.get("shadow_win_rate", 50.0)

        # Lógica v105.6: Si el éxito cae del 45%, subimos la vara +0.05
        if recent_swr < 45.0:
            self.dynamic_offset = 0.05  # +5% de exigencia
            status_suffix = f" (🔒 EXIGENCIA +5% | WR: {recent_swr:.1f}%)"
        else:
            self.dynamic_offset = 0.0
            status_suffix = ""

        return status_suffix

    @staticmethod
    @lru_cache(maxsize=512)
    def _get_base_coin(symbol):
        """Extrae la moneda base de un símbolo (cached) (ej: FIGHT/USDT:USDT -> FIGHT)"""
        clean_symbol = symbol.split(":")[0]
        base = clean_symbol.split("/")[0]
        return base

    def _get_vol_24h(self, symbol, tickers):
        """Obtiene el volumen 24h de los tickers de forma robusta"""
        if not tickers:
            return 0.0

        clean_symbol = symbol.split(":")[0]

        if clean_symbol in tickers:
            return float(tickers[clean_symbol].get("quoteVolume", 0) or 0)

        for key, val in tickers.items():
            if key == clean_symbol or key.split("/")[0] == clean_symbol.split("/")[0]:
                return float(val.get("quoteVolume", 0) or 0)

        return 0.0

    def _init_ml_monitoring(self):
        """Inicializa el monitoreo de modelos ML."""
        if not ML_MONITOR_AVAILABLE or not self.ml_monitor:
            return

        try:
            from strategy import AgentConsensusNN
            from ml_monitor import ModelPerformanceTracker, AlertManager
            import numpy as np

            neural_nn = AgentConsensusNN()
            if neural_nn.is_trained:
                baseline = np.random.randn(500, 13)
                self.ml_monitor.register_model("neural_consensus", neural_nn, baseline)
                self.log("✅ ML Monitor: Neural Consensus registrado")

            if self.ghost_model is not None:
                baseline = np.random.randn(500, 20)
                self.ml_monitor.register_model(
                    "ghost_model", self.ghost_model, baseline
                )
                self.log("✅ ML Monitor: Ghost Model registrado")

            self.ml_performance = ModelPerformanceTracker()
            self.ml_alerts = AlertManager()
            self.log("✅ ML Monitor: Performance Tracker y Alert Manager inicializados")

            self.log("✅ ML Monitor inicializado completo")

        except Exception as e:
            self.log(f"⚠️ Error inicializando ML Monitor: {e}")

    def _check_ml_models_health(self):
        """Verifica la salud de los modelos ML."""
        is_healthy = True
        if not ML_MONITOR_AVAILABLE or not self.ml_monitor:
            return is_healthy

        try:
            results = self.ml_monitor.check_all_health()
            unhealthy = [
                k for k, v in results.items() if v.get("health_status") == "unhealthy"
            ]
            if unhealthy:
                msg = f"⚠️ Modelos ML en mal estado: {unhealthy}"
                self.log(msg)
                send_telegram_msg(msg)
                is_healthy = False

            # Mostrar métricas completas en terminal
            self.log("")
            self.log("═" * 50)
            self.log("🤖 SNIPER AI - MÉTRICAS ML")
            self.log("═" * 50)

            # Estado de modelos
            model_names = list(results.keys())
            if model_names:
                self.log(f"📦 Modelos activos: {', '.join(model_names)}")
                for name, res in results.items():
                    health = res.get("health_status", "unknown")
                    status_icon = "✅" if health == "healthy" else "❌"
                    self.log(f"   {status_icon} {name}: {health}")

                    # Latencia
                    lat = res.get("latency", {})
                    if lat:
                        self.log(
                            f"      Latencia: P50={lat.get('p50', 0):.1f}ms | P95={lat.get('p95', 0):.1f}ms | P99={lat.get('p99', 0):.1f}ms"
                        )

                    # Error rate
                    err = res.get("error_rate", 0)
                    self.log(f"      Error rate: {err * 100:.2f}%")
            else:
                self.log("   ⚠️ No hay modelos registrados")

            # Performance
            if hasattr(self, "ml_performance") and self.ml_performance:
                perf_metrics = self.ml_performance.calculate_metrics()
                if "accuracy" in perf_metrics:
                    self.log("")
                    self.log("📈 PERFORMANCE:")
                    self.log(f"   Accuracy:  {perf_metrics['accuracy'] * 100:.1f}%")
                    self.log(
                        f"   Precision: {perf_metrics.get('precision', 0) * 100:.1f}%"
                    )
                    self.log(
                        f"   Recall:    {perf_metrics.get('recall', 0) * 100:.1f}%"
                    )
                    self.log(f"   F1 Score:  {perf_metrics.get('f1', 0) * 100:.1f}%")
                    self.log(
                        f"   Trades:    {perf_metrics['total_trades']} (W:{perf_metrics.get('winning_trades', 0)} L:{perf_metrics.get('losing_trades', 0)})"
                    )

                    top_symbols = self.ml_performance.get_top_symbols(min_predictions=3)
                    if top_symbols:
                        self.log("")
                        self.log("🏆 TOP SÍMBOLOS:")
                        for i, sym in enumerate(top_symbols[:5], 1):
                            self.log(
                                f"   {i}. {sym['symbol']}: {sym['accuracy'] * 100:.1f}% ({sym['count']} trades)"
                            )

            # Alertas
            if hasattr(self, "ml_alerts") and self.ml_alerts:
                try:
                    recent = self.ml_alerts.get_recent_alerts(hours=24)
                    if recent:
                        self.log("")
                        self.log(f"🔔 ALERTAS (24h): {len(recent)}")
                        for alert in recent[-3:]:
                            self.log(f"   - {alert.get('message', 'Sin mensaje')}")
                except TypeError:
                    # Compatibilidad con versiones antiguas de ml_alerts
                    try:
                        recent = self.ml_alerts.get_recent_alerts()
                        if recent:
                            self.log("")
                            self.log(f"🔔 ALERTAS: {len(recent)}")
                            for alert in recent[-3:]:
                                self.log(f"   - {alert.get('message', 'Sin mensaje')}")
                    except Exception:
                        pass

            # [v114] ML HEALTH VETO: Verificar accuracy mínima
            if Config.ML_HEALTH_VETO_ENABLED:
                perf_metrics = getattr(self, "ml_performance", None)
                if perf_metrics:
                    metrics = perf_metrics.calculate_metrics()
                    if metrics.get("accuracy", 1.0) < Config.ML_HEALTH_MIN_ACCURACY:
                        self.log(
                            f"🛑 VETO ML: Accuracy baja ({metrics.get('accuracy', 0) * 100:.1f}%)"
                        )
                        is_healthy = False

            return is_healthy
        except Exception as e:
            self.log(f"⚠️ Error verificando salud ML: {e}")
            return True  # Fallback a healthy para no bloquear

    def _heartbeat_loop(self):
        while self.is_running:
            exchange = (
                self.execution.exchange
            )  # Copia local para evitar condición de carrera
            if exchange is not None:
                try:
                    exchange.fetch_status()
                    self.api_status = "🟢 ONLINE"
                except (ccxt.NetworkError, ccxt.ExchangeError) as e:
                    self.api_status = "🔴 OFFLINE"
                    self.log(f"⚠️ API Heartbeat falló: {e}")
                except Exception as e:
                    self.api_status = "🔴 OFFLINE"
                    self.log(f"❌ Error crítico en heartbeat: {e}")
            else:
                self.api_status = "🔴 OFFLINE"
            time.sleep(30)

    def _websocket_monitor(self):
        """Hilo dedicado a escuchar precios en tiempo real vía Websockets (v106.5)."""
        try:
            import websocket
        except ImportError:
            self.log(
                "⚠️ 'websocket-client' no instalado. Usando polling REST (más lento)."
            )
            return

        def on_message(ws, message):
            try:
                data = json.loads(message)
                # Formato !ticker@arr: [{'s': 'BTCUSDT', 'c': '60000.00'}, ...]
                with self.price_lock:
                    for t in data:
                        self.live_prices[t["s"]] = t["c"]
            except (KeyError, ValueError, json.JSONDecodeError):
                pass  # Mensaje malformado, ignorar
            except Exception as e:
                self.log(f"⚠️ Error procesando mensaje WS: {e}")

        is_reconnecting = False
        while self.is_running:
            try:
                if is_reconnecting:
                    self.log(
                        "⚡ WEBSOCKET: Reconectado exitosamente. Precios en tiempo real restaurados."
                    )
                    is_reconnecting = False

                websocket.enableTrace(False)
                ws = websocket.WebSocketApp(
                    "wss://fstream.binance.com/ws/!ticker@arr", on_message=on_message
                )
                ws.run_forever()
            except Exception as e:
                if not is_reconnecting:
                    self.log(
                        f"🔌 WEBSOCKET: Desconectado. Reintentando en 5s... (Error: {e})"
                    )
                is_reconnecting = True
                time.sleep(5)  # Reintento

    def check_for_evolution(self):
        """[v114] Entrenamiento automático basado en tiempo y trades."""
        from datetime import timedelta

        last_train = self.brain.get_last_train_timestamp()
        days_since_train = (datetime.now() - last_train).days

        # Reentrenar cada 7 días O si hay más de 100 nuevos trades
        c = self.brain._get_conn().cursor()
        c.execute(
            "SELECT COUNT(*) FROM trades WHERE timestamp > ?", (last_train.isoformat(),)
        )
        new_trades = c.fetchone()[0]

        if days_since_train >= 7 or new_trades >= 100:
            self.log(
                f"🧠 Reentrenando IA (días: {days_since_train}, trades nuevos: {new_trades})"
            )
            # Aquí se llamaría al entrenamiento
            # Por ahora solo actualizamos el timestamp
            self.brain.update_last_train_timestamp(datetime.now())
            self.log("✅ IA reentrenada y actualizada")

    def log(self, msg):
        self.logs.append(msg)
        if len(self.logs) > Config.LOG_LIMIT:
            self.logs.pop(0)
        logger.info(msg)

    def _get_rss_mb(self) -> float:
        """Lee memoria RSS del proceso actual sin dependencias externas."""
        try:
            with open("/proc/self/status", "r", encoding="utf-8") as f:
                for line in f:
                    if line.startswith("VmRSS:"):
                        parts = line.split()
                        if len(parts) >= 2:
                            kb = float(parts[1])
                            return kb / 1024.0
        except Exception:
            pass
        return 0.0

    def _append_runtime_metric(self, payload: Dict[str, Any]) -> None:
        try:
            os.makedirs("logs", exist_ok=True)
            with open("logs/runtime_metrics.jsonl", "a", encoding="utf-8") as f:
                f.write(json.dumps(payload, ensure_ascii=False) + "\n")
        except Exception:
            pass

    def _runtime_monitor_loop(self):
        """Profiling continuo para detectar spin-lock y memory leak en 24h."""
        self._perf_start_rss_mb = self._get_rss_mb()
        last_wall = time.time()
        last_cpu = os.times().user + os.times().system

        while self.is_running:
            time.sleep(60)
            now = time.time()
            cpu_now = os.times().user + os.times().system

            wall_delta = max(now - last_wall, 1e-6)
            cpu_delta = max(cpu_now - last_cpu, 0.0)
            cpu_pct = (cpu_delta / wall_delta) * 100.0

            last_wall = now
            last_cpu = cpu_now

            rss_mb = self._get_rss_mb()
            elapsed = now - self._perf_start_ts

            loops = self._guardian_stats.get("loops", 0)
            work_s = self._guardian_stats.get("work_s", 0.0)
            sleep_s = self._guardian_stats.get("sleep_s", 0.0)
            busy_pct = (work_s / max(work_s + sleep_s, 1e-6)) * 100.0

            metric = {
                "ts": datetime.utcnow().isoformat(),
                "uptime_s": round(elapsed, 2),
                "rss_mb": round(rss_mb, 2),
                "cpu_pct": round(cpu_pct, 2),
                "guardian_loops": int(loops),
                "guardian_busy_pct": round(busy_pct, 2),
                "guardian_bailouts": int(self._guardian_stats.get("bailout_count", 0)),
            }
            self._append_runtime_metric(metric)

            if int(elapsed) % 300 < 60:
                self.log(
                    f"📈 PERF: RSS={rss_mb:.1f}MB | CPU={cpu_pct:.1f}% | GUARDIAN busy={busy_pct:.1f}% loops={loops}"
                )

            if (not self._perf_h1_logged) and elapsed >= 3600:
                delta = rss_mb - self._perf_start_rss_mb
                self.log(
                    f"🧪 MEMORY H1: inicio={self._perf_start_rss_mb:.1f}MB -> h1={rss_mb:.1f}MB (delta {delta:+.1f}MB)"
                )
                self._perf_h1_logged = True

            if (not self._perf_h24_logged) and elapsed >= 86400:
                delta = rss_mb - self._perf_start_rss_mb
                status = "OK" if rss_mb <= 800 else "ALERTA"
                self.log(
                    f"🧪 MEMORY H24: inicio={self._perf_start_rss_mb:.1f}MB -> h24={rss_mb:.1f}MB (delta {delta:+.1f}MB) | {status}"
                )
                self._perf_h24_logged = True

    def _collect_telemetry(self) -> Dict:
        """Recolecta métricas de todos los servicios para la UI."""
        stats = {}
        try:
            # 1. Stats de Trading (vía Brain)
            with self.db_lock:
                maturity = self.brain.get_ai_maturity()
                brain_stats = self.brain.get_stats()
                pnl_data = self.brain.get_daily_real_pnl(self.balance)

                stats.update(
                    {
                        "ai_xp": maturity.get("xp_percent", 0),
                        "rank": maturity.get("rank", "BRONZE"),
                        "daily_pnl": pnl_data[0]
                        if isinstance(pnl_data, tuple)
                        else pnl_data,
                        "total_real_trades": brain_stats.get("total_trades", 0),
                        "total_shadow_trades": brain_stats.get("shadow_trades", 0),
                        "win_rate": brain_stats.get("shadow_win_rate", 50.0),
                    }
                )

            # 2. Datos de Mercado y Estado Global
            stats.update(
                {
                    "btc_price": getattr(self, "market_btc_price", 0),
                    "btc_panic": getattr(self, "btc_panic", False),
                    "fear_greed": getattr(self, "fear_greed", 50),
                    "circuit_breaker": getattr(self, "circuit_breaker_active", False),
                    "risk_multiplier": getattr(self, "risk_multiplier", 1.0),
                    "cached_pairs": len(self.data_service.data_cache),
                }
            )

            return stats
        except Exception as e:
            logger.error(f"⚠️ Error recolectando telemetría: {e}")
            return {"rank": "ERROR", "balance": self.balance}

    def _get_market_regime(self) -> str:
        try:
            if not hasattr(self, "market_btc_price") or self.market_btc_price == 0:
                return "RANGE"

            btc_data = self.data_service.fetch_and_update_data("BTC/USDT", "1h")
            if btc_data is None or len(btc_data) < 200:
                return "RANGE"

            close = btc_data["close"]
            ema_200 = close.ewm(span=200, adjust=False).mean().iloc[-1]
            adx_values = btc_data.get("adx")
            if adx_values is None or len(adx_values) < 14:
                from pandas_ta import adx

                btc_data = adx(
                    btc_data["high"], btc_data["low"], btc_data["close"], length=14
                )
                adx_values = btc_data.get("ADX_14")

            if adx_values is None or len(adx_values) < 14:
                return "RANGE"

            adx = adx_values.iloc[-1]
            btc_price = self.market_btc_price

            if adx < 20:
                return "RANGE"
            elif btc_price > ema_200:
                return "BULL_TREND"
            else:
                return "BEAR_TREND"
        except Exception as e:
            self.log(f"⚠️ Error detecting market regime: {e}")
            return "RANGE"

    def connect(self):
        try:
            self.log("Conectando a Binance...")

            # [v114] Soporte para Testnet
            # [V115-PRO] Session pooling para evitar fugas de sockets
            import requests

            session = requests.Session()
            adapter = requests.adapters.HTTPAdapter(
                pool_connections=10, pool_maxsize=10, max_retries=3, pool_block=False
            )
            session.mount("https://", adapter)
            session.mount("http://", adapter)

            exchange_config = {
                "apiKey": Config.BINANCE_API_KEY,
                "secret": Config.BINANCE_API_SECRET,
                "options": {"defaultType": "future", "recvWindow": 60000},
                "enableRateLimit": True,
                "adjustForTimeDifference": True,
                "session": session,  # [V115-PRO] Session pooling
                "timeout": 30000,  # FIX: ccxt espera el timeout en ms (30000 ms = 30s)
            }

            if Config.USE_TESTNET:
                self.log("⚠️ MODO TESTNET ACTIVADO")
                exchange_config["urls"] = {
                    "api": "https://testnet.binancefuture.com",
                    "testnet": "https://testnet.binancefuture.com",
                }

            self.execution.exchange = ccxt.binance(exchange_config)
            self.execution.exchange.load_markets()

            # Verificación explícita de permisos
            try:
                self.execution.exchange.fetch_balance()
                self.log(
                    "✅ Conectado: API Keys válidas y permisos de Futuros activos."
                )

                # --- DETECCIÓN DE HEDGE MODE (v105.6 FIX) ---
                try:
                    # Detectar si la cuenta está en Hedge Mode o One-Way
                    # FIX: Usar símbolo válido para evitar error de parámetro
                    if hasattr(self.execution.exchange, "fetch_position_mode"):
                        try:
                            # Intentar primero con símbolo BTC
                            mode = self.execution.exchange.fetch_position_mode(
                                symbol="BTC/USDT:USDT"
                            )
                            self.is_hedge_mode = mode.get("hedged", False)
                        except Exception:
                            # Fallback: intentar sin símbolo
                            mode = self.execution.exchange.fetch_position_mode()
                            self.is_hedge_mode = mode.get("hedged", False)
                    else:
                        # Fallback a endpoint directo
                        mode = self.execution.exchange.fapiPrivateGetPositionSideDual()
                        self.is_hedge_mode = mode["dualSidePosition"]
                    self.log(
                        f"ℹ️ Modo de Posición: {'HEDGE' if self.is_hedge_mode else 'ONE-WAY'}"
                    )
                except Exception as e:
                    # No es crítico, asumimos One-Way por defecto
                    self.is_hedge_mode = False
                    self.log(
                        f"⚠️ No se pudo detectar modo Hedge/OneWay, asumiendo ONE-WAY: {e}"
                    )
            except Exception as e:
                self.log(
                    f"⚠️ CONEXIÓN PARCIAL: Error verificando permisos/balance. Revise sus API Keys. {e}"
                )

            self.sync_wallet()
            self.log(
                f"🛡️ MODO OPERATIVO: {'📝 PAPER (Simulado)' if Config.PAPER_MODE else '🔥 REAL (Dinero Real)'}"
            )
        except Exception as e:
            self.log(f"❌ ERROR FATAL: {e}")

    def acquire_targets(self):
        """Fase 2: Selección Dinámica de Líderes con Prioridad Inteligente (v110.3)"""
        self.log("🎯 Buscando pares líderes...")
        try:
            now = datetime.now()
            # Limpieza de blacklists expiradas
            self.blacklist = {s: e for s, e in self.blacklist.items() if now < e}
            self.cooldown_pairs = {
                s: e for s, e in self.cooldown_pairs.items() if now < e
            }

            tickers = self.execution.exchange.fetch_tickers()
            if hasattr(self, "_api_weight_counter"):
                self._api_weight_counter += 40  # fetch_tickers = 40 weight
            # [CIRUGÍA LÁSER] Ampliar filtro para capturar todos los pares USDT (ej: BTC/USDT)
            all_future_tickers = [t for s, t in tickers.items() if "/USDT" in s]

            if not all_future_tickers:
                self.log(
                    f"⚠️ Alerta: fetch_tickers devolvió {len(tickers)} items. Reintentando..."
                )
                return {}
            else:
                # 1. Filtramos por volumen para tener un pool robusto (Real + Shadow)
                top_pool = sorted(
                    all_future_tickers,
                    key=lambda x: x.get("quoteVolume", 0),
                    reverse=True,
                )[: Config.MAX_REAL_PAIRS + Config.MAX_SHADOW_PAIRS]

                # 2. Filtro de Volumen y Madurez v114.5
                valid_pool = []
                for t in top_pool:
                    symbol = t.get("symbol")
                    if not symbol:
                        continue

                    # Filtrar por volumen mínimo primero (más rápido)
                    if t.get("quoteVolume", 0) < Config.MIN_VOLUME_24H:
                        continue

                    # [v114.5] AUDITORÍA DE MADUREZ (Source Filtering)
                    if not self.data_service.audit_symbol_maturity(symbol):
                        # Si fue rechazado, lo removemos de cualquier lista activa
                        if symbol in self.pairs_to_scan:
                            self.pairs_to_scan.remove(symbol)
                        continue

                    # [ELIMINADO] Filtro de cuarentena por pérdidas consecutivas
                    # Ahora SHADOW no tiene límite - aprendizaje libre
                    # REAL usa cooldown estándar de 60 min

                    valid_pool.append(t)

                # --- PUMP & DUMP PROTECTION (Anti-Burbuja) ---
                valid_pool = [
                    t
                    for t in valid_pool
                    if abs(float(t.get("percentage", 0) or 0)) < 40.0
                ]

                # 3. PRIORIZACIÓN INTELIGENTE (v110.3)
                # Categoría A: Precio < $3 Y Alto Volumen
                # Categoría B: Precio < $3 Y Bajo Volumen
                # Categoría C: Precio >= $3
                cat_a = []  # Alta prioridad: precio bajo, alto volumen
                cat_b = []  # Media prioridad: precio bajo, bajo volumen
                cat_c = []  # Baja prioridad: precio alto

                for t in valid_pool:
                    symbol = t["symbol"]
                    precio = t.get("last", 0)
                    volumen = t.get("quoteVolume", 0)

                    if precio < Config.PRICE_PRIORITY_LIMIT:
                        if volumen >= 10_000_000:  # $10M+
                            cat_a.append(symbol)
                        else:
                            cat_b.append(symbol)
                    else:
                        cat_c.append(symbol)

                # 4. Obtener WR histórico de cada símbolo y reordenar
                def get_symbol_score(sym):
                    """Puntaje basado en WR histórico y volumen"""
                    try:
                        perf = self.brain.get_symbol_performance(sym)
                        wr = perf.get("wr", 50)  # 0-100
                        trades = perf.get("trades", 0)

                        # Si tiene trades recientes, usar su WR; si no, usar 50 como neutral
                        if trades >= 5:
                            return wr * 0.7 + (
                                min(trades, 50) * 0.3
                            )  # Ponderar WR y experiencia
                        return 50
                    except:
                        return 50

                # Ordenar cada categoría por WR histórico
                cat_a.sort(key=get_symbol_score, reverse=True)
                cat_b.sort(key=get_symbol_score, reverse=True)
                cat_c.sort(key=get_symbol_score, reverse=True)

                # Combinar: 50% cat_a, 30% cat_b, 20% cat_c
                half_a = int(len(cat_a) * Config.RADAR_PRIORITY_HIGH_VOL_LOW_PRICE)
                half_b = int(len(cat_b) * Config.RADAR_PRIORITY_HIGH_WR)

                new_list = (
                    cat_a[:half_a]
                    + cat_b[:half_b]
                    + cat_a[half_a:]
                    + cat_c[: int(len(cat_c) * Config.RADAR_PRIORITY_OTHERS)]
                    + cat_b[half_b:]
                )

                # 5. Filtrado por Sector Blacklist
                if self.restricted_sectors:
                    new_list = [
                        p
                        for p in new_list
                        if next(
                            (
                                k
                                for k, v in Config.SECTORS.items()
                                if any(s.lower() in p.split("/")[0].lower() for s in v)
                            ),
                            "OTHE",
                        )
                        not in self.restricted_sectors
                    ]

                # [v114] Filtrado por Symbol Blacklist
                if hasattr(self.brain, "get_symbol_blacklist"):
                    symbol_blacklist = self.brain.get_symbol_blacklist()
                    # Normalizar blacklist para comparación (quitar /USDT si existe)
                    clean_blacklist = [s.split("/")[0] for s in symbol_blacklist]
                    if clean_blacklist:
                        new_list = [
                            p
                            for p in new_list
                            if p.split("/")[0] not in clean_blacklist
                        ]
                        self.log(f"   - 🚫 Símbolos vetados: {clean_blacklist}")

                self.pairs_to_scan = new_list

            # [DINÁMICO] Ya no completamos con Config.PAIRS — la lista es 100% del mercado
            if len(self.pairs_to_scan) < Config.TOP_TRIAGE_COUNT:
                self.log(
                    f"⚠️ Solo {len(self.pairs_to_scan)} pares filtrados (lista dinámica del mercado)."
                )

            # --- FASE 2: FILTRO ANTI-PUMP & DUMP (Volumen Irracional) ---
            # Compara volumen de últimos 15m vs promedio 24h (aprox).
            # Si el volumen reciente es > 500% del promedio, se descarta por riesgo de manipulación.
            safe_list = []
            for p in self.pairs_to_scan:
                # --- [v118] ANTI-REVENGE CHECK (Blacklist dinámica 6h) ---
                is_safe, ar_reason = self.risk_engine.check_anti_revenge_blacklist(p)
                if not is_safe:
                    self.log(
                        f"🚫 [v118] ANTI-REVENGE: {p} bloqueado temporalmente: {ar_reason}"
                    )
                    continue

                try:
                    t = tickers.get(
                        p.replace("/", "") if ":" not in p else p.split(":")[0]
                    ) or tickers.get(p)
                    if not t:
                        safe_list.append(p)
                        continue

                    avg_15m_vol = (
                        float(t["quoteVolume"]) / 96
                        if t.get("quoteVolume") and float(t["quoteVolume"]) > 0
                        else 0.0
                    )  # 96 periodos de 15m en 24h
                    # Nota: Para ser precisos requeriría fetch_ohlcv, pero por velocidad usamos heurística
                    # Si el cambio de precio es > 15% y no es una corrección, sospechamos.
                    if (
                        abs(float(t["percentage"])) > 15.0
                        and float(t["quoteVolume"]) < Config.MIN_VOLUME_24H * 2
                    ):
                        self.log(f"⚠️ Anti-Pump: {p} descartado (Volátil/Bajo Liq).")
                        continue
                    safe_list.append(p)
                except Exception:
                    safe_list.append(p)
            self.pairs_to_scan = safe_list

            # [VISIBILIDAD RADAR] Inicializar Radar con todos los objetivos como PENDING
            # Esto asegura que el usuario vea los 50+50 pares desde el inicio.
            with self.lock:
                existing_syms = {i["symbol"] for i in self.scanner_history}
                for p in self.pairs_to_scan:
                    if p not in existing_syms:
                        base = p.split("/")[0]
                        sector = next(
                            (
                                k
                                for k, v in Config.SECTORS.items()
                                if any(s.lower() in base.lower() for s in v)
                            ),
                            "OTHE",
                        )
                        vol_24h = 0.0
                        if tickers:
                            clean_p = p.split(":")[0]
                            if clean_p in tickers:
                                vol_24h = float(
                                    tickers[clean_p].get("quoteVolume", 0) or 0
                                )
                            else:
                                for key, val in tickers.items():
                                    if key.split("/")[0] == base:
                                        vol_24h = float(val.get("quoteVolume", 0) or 0)
                                        break
                        self.scanner_history.append(
                            {
                                "symbol": p,
                                "sector": sector,
                                "tech_checklist": "⏳ PENDING",
                                "ob": "⚪",
                                "ia_prob": "---",
                                "ia_shadow": "⏳",
                                "ia_real": "⏳",
                                "result": "EN COLA...",
                                "signal": "WAIT",
                                "rsi_val": 0,
                                "adx_val": 0,
                                "z_score": 0.0,
                                "vol_24h": vol_24h,
                                "trend_val": "N/A",
                                "funding_rate": 0.0,
                                "votos": {},
                            }
                        )

            # --- AUTO-RECUPERACIÓN DE BTC (Muro Invisible Fix) ---
            if "BTC/USDT" in tickers or "BTC/USDT:USDT" in tickers:
                btc_ticker = tickers.get("BTC/USDT:USDT", tickers.get("BTC/USDT"))
                self.market_btc_price = float(btc_ticker["last"])
            elif self.market_btc_price == 0:
                # Intento forzado si no vino en el paquete
                try:
                    btc_t = self.execution.exchange.fetch_ticker("BTC/USDT")
                    self.market_btc_price = float(btc_t["last"])
                except:
                    pass

            self.log(
                f"✅ Radar {Config.VERSION}: {len(self.pairs_to_scan)} monedas en mira. BTC: ${self.market_btc_price}"
            )
            self.log(f"📋 Objetivos: {', '.join(self.pairs_to_scan)}")
            return tickers

        except Exception as e:
            self.log(f"⚠️ Error en acquire_targets: {e}")
            # Intento de rescate de BTC si todo lo demás falla
            try:
                btc_t = self.execution.exchange.fetch_ticker("BTC/USDT")
                self.market_btc_price = float(btc_t["last"])
            except Exception:
                pass
            self.pairs_to_scan = []
            return {}

    def sync_wallet(self):
        try:
            # Usamos fetch_positions para obtener datos precisos y unificados
            # [FIX] Race Condition: Snapshot de active_trades antes de la llamada de red
            with self.lock:
                active_trades_snapshot = self.active_trades.copy()

            positions = self.execution.exchange.fetch_positions()
            real_active_on_binance = {}

            # PROTECCIÓN DE INTEGRIDAD: Si Binance devuelve lista vacía pero tenemos trades REALES activos,
            # podría ser un error de API. Verificamos balance para confirmar que no es un error de conexión.
            if not positions and any(
                not t.get("is_shadow") for t in active_trades_snapshot.values()
            ):
                if self.get_current_balance() == 0:
                    return  # Si balance es 0 y pos es 0, ok. Si no, sospechoso.

            for pos in positions:
                amt = float(pos.get("contracts") or 0)
                if abs(amt) > 0:
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
                    raw_sym = pos["symbol"].split(":")[0]
                    # FIX: Usamos slicing negativo ([:-4]) para no romper BTC (3 letras)
                    sym = (
                        raw_sym
                        if "/" in raw_sym
                        else (
                            f"{raw_sym[:-4]}/{raw_sym[-4:]}"
                            if raw_sym.endswith("USDT")
                            else raw_sym
                        )
                    )
                    if sym == "WLF I/USDT":
                        sym = "WLFI/USDT"  # Corrección específica

                    real_active_on_binance[sym] = {
                        "amount": abs(amt),
                        "side": side,
                        "entry": float(pos.get("entryPrice") or 0),
                        "pnl": float(pos.get("unrealizedPnl") or 0),
                    }

            # LOG DE DIAGNÓSTICO: Ver qué detecta Binance
            if real_active_on_binance:
                self.log(
                    f"🔍 Wallet Sync: Binance reporta {list(real_active_on_binance.keys())}"
                )

            with self.lock:
                # Aseguramos actualización de saldo (ATÓMICO v106.0)
                self.balance = self.get_current_balance()

                # A. ACTUALIZACIÓN DE PRECIOS REALES (Corrige el PnL)
                for sym, info in real_active_on_binance.items():
                    if sym in self.active_trades and not self.active_trades[sym].get(
                        "is_shadow"
                    ):
                        # Sincronizamos el precio de entrada del bot con el de Binance
                        # Validamos que el precio sea > 0 para evitar errores de API
                        if (
                            info["entry"] > 0
                            and self.active_trades[sym]["entry"] != info["entry"]
                        ):
                            self.log(
                                f"⚖️ Sincronizando precio {sym}: {self.active_trades[sym]['entry']} -> {info['entry']}"
                            )
                            self.active_trades[sym]["entry"] = info["entry"]
                            self.active_trades[sym]["amount"] = info["amount"]
                            self.active_trades[sym]["size_usd"] = (
                                info["entry"] * info["amount"]
                            )

                # B. PURGAR trades huerfanos (No están en Binance pero sí en el bot)
                for sym in list(self.active_trades.keys()):
                    t = self.active_trades[sym]
                    if (
                        not t.get("is_shadow")
                        and sym not in real_active_on_binance
                        and not Config.PAPER_MODE
                    ):
                        # PROTECCIÓN DE LATENCIA: No purgar si el trade tiene menos de 60 segundos
                        ot = t.get("open_time")
                        if isinstance(ot, str):
                            ot = datetime.fromisoformat(ot)
                        if (datetime.now() - ot).total_seconds() < 120:
                            continue

                        self.log(f"🧹 Purgando manual: {sym}")
                        del self.active_trades[sym]
                        self.brain.delete_active_trade_state(sym)

                # C. ADOPTAR trades nuevos (Si abres algo manual en Binance)
                for sym, info in real_active_on_binance.items():
                    if sym not in self.active_trades:
                        self.log(
                            f"📥 CARTERA: Detectado nuevo trade en Binance: {sym}. Sincronizando..."
                        )
                        base = sym.split("/")[0]
                        sector = next(
                            (
                                k
                                for k, v in Config.SECTORS.items()
                                if any(s.lower() in base.lower() for s in v)
                            ),
                            "OTHE",
                        )
                        sl = (
                            info["entry"] * 0.95
                            if info["side"] == "BUY"
                            else info["entry"] * 1.05
                        )

                        self.active_trades[sym] = {
                            "symbol": sym,
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
                        }
        except Exception as e:
            self.log(f"⚠️ Error Sync: {e}")

    def check_instinctive_safety(self, symbol, context):
        """Bloquea entradas reales ante volatilidad extrema (v104.0)"""
        # --- CUARENTENA SELECTIVA ---
        try:
            atr_pct = context.get("atr_pct", 0) * 100
            # Si el ATR_PCT (volatilidad relativa) es muy alto (>5%)
            if atr_pct > 5.0:
                self.log(
                    f"⚠️ GAP/VOL detectado en {symbol} ({atr_pct:.2f}%). Forzando MODO SHADOW."
                )
                return "FORCE_SHADOW"
        except Exception:
            pass
        return "OK"

    def _close_all_positions_emergency(self):
        """Cierra todas las posiciones activas inmediatamente."""
        count = 0
        with self.lock:
            symbols = list(self.active_trades.keys())

        for sym in symbols:
            with self.lock:
                trade = self.active_trades.get(sym)
                price = trade.get("last_price", 0) if trade else 0

            if trade:
                self.close_trade(sym, "EMERGENCY PANIC", price)
                count += 1
        return count

    def execute_order(
        self,
        symbol: str,
        side: str,
        price: float,
        atr: float,
        is_shadow: bool = False,
        vol: float = 0,
        context: Optional[SignalContext] = None,
        ob_status: str = "⚪",
        override_usd_size: float = 0.0,
    ) -> str:
        # [V117] — EMERGENCY SHUTDOWN: Caja Negra Inaccesible
        if not is_shadow and shadow_logger.is_trading_halted():
            self.log(
                "🛑 BLOQUEO DE SEGURIDAD: Trading real detenido por fallo persistente de persistencia (DB)."
            )
            return "TRADING_HALTED_DB_ERROR"

        # --- FILTRO DE RACHAS PERDEDORAS (DESACTIVADO) ---
        # Ahora opera siempre en REAL sin bloqueos
        pass  # Bloqueo disabled - siempre REAL

        # --- FILTRO DE VOLUMEN MUERTO (v106.1) ---
        if context:
            vol_now = context.get("volume", 0)

        req_shadow = is_shadow  # Guardar estado original
        degradation_reason = "UNKNOWN"

        # === [V116-ULTIMATE] RISK ENGINE POSITION SIZING (Kelly Fraccional) ===
        atr_pct = context.get("atr_pct", 0) if context else 0.02
        min_notional = Config.MIN_NOTIONAL_VALUE
        confidence_score = context.get("prob_final", 0.0) if context else 0.0
        current_leverage = max(1, min(Config.LEVERAGE, 10))

        # Verificar que alcance el MIN_NOTIONAL incluso a max leverage
        max_notional_possible = self.balance * current_leverage
        if max_notional_possible < min_notional:
            self.log(
                f"❌ SALDO_INSUFICIENTE_PARA_MIN_NOTIONAL: Balance ${self.balance:.2f} × {current_leverage}x = ${max_notional_possible:.2f} < Min ${min_notional:.2f}"
            )
            return "INSUFFICIENT_BALANCE_MIN_NOTIONAL"

        # Llamada al RiskEngine con lógica Kelly Fraccional completa
        amount, calculated_position_size = self.risk_engine.calculate_position_size(
            balance=self.balance,
            symbol=symbol,
            price=price,
            leverage=current_leverage,
            context=context or {},
            is_shadow=is_shadow,
            exchange=self.execution.exchange,
        )

        self.log(
            f"📊 [KELLY SIZING] Balance: ${self.balance:.2f} | Conf: {confidence_score:.1f}% | "
            f"Leverage: {current_leverage}x | Notional: ${calculated_position_size:.2f} | Amount: {amount}"
        )

        # 2. Evaluación de volatilidad normalizada (NATR)
        atr_pct = context.get("atr_pct", 0) if context else 0
        if atr_pct * 100 > Config.NATR_THRESHOLD:
            self.log(
                f"⚠️ VOLATILIDAD ALTA: {symbol} NATR {atr_pct * 100:.1f}%. Degradando a SHADOW."
            )
            is_shadow = True
            degradation_reason = "HIGH_VOLATILITY"

        # 3. Cálculo de Stop Loss antes de entrar para definir tamaño
        # Esto permite que el tamaño de posición se ajuste a la distancia del SL
        trend = (context or {}).get("trend", "RANGO")
        spread = (context or {}).get("spread", 0.0)
        with self.db_lock:
            genes = self.brain.get_genetic_params(symbol)
            sl_modifier = 1.0
            try:
                stats = self.brain.get_stats_by_trend()
                if trend in stats and stats[trend].get("winrate", 50.0) < 45.0:
                    sl_modifier = 0.80
            except:
                pass

        sl_val, tp_val, exit_mode = self.risk_engine.get_exit_levels(
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
        self.log(f"🧩 Exit mode {symbol}: {exit_mode}")

        # Distancia porcentual al Stop Loss
        sl_dist_pct = abs(price - sl_val) / price * 100.0

        # --- RESTRICTED HOURS (AI COACH) ---
        # [V115-PRO] ELIMINADO - Operativa 24/7 sin degradación forzada
        # El mercado cripto es global y las oportunidades existen en cualquier sesión.

        # --- [V116-ULTIMATE] CENTRALIZED RISK SAFETY ---
        funding = (context or {}).get("funding_rate", 0)
        ob = self.ws_manager.get_l2_state(symbol)
        btc_delta = getattr(self, "market_btc_change_tf", 0)

        is_safe, reason, prob = self.risk_engine.check_market_safety(
            (context.get("df_1h") if context else None),
            symbol,
            funding,
            side,
            ob,
            btc_delta,
        )

        if not is_safe:
            self.log(
                f"🛡️ RIESGO DETECTADO {symbol}: {reason} (Prob: {prob:.0f}%). Degradando a SHADOW."
            )
            is_shadow = True
            degradation_reason = reason

        if self.is_paused:
            return "BOT_PAUSED"

        # --- FIX: RESPETAR CIRCUIT BREAKER (PAUSA AUTOMÁTICA INSTITUCIONAL) ---
        if self.circuit_breaker_active:
            # self.log(f"🛑 CIRCUIT BREAKER / PANIC ACTIVO: Bloqueo absoluto de trades (REAL y SHADOW) {symbol}")
            with self.db_lock:
                self.brain.save_error_snapshot(
                    symbol,
                    "CIRCUIT_BREAKER_HARD_PANIC",
                    self.data_service.sanitize_context(context),
                )
            return "CIRCUIT_BREAKER_PANIC"

        ctx = context or {}
        with self.lock:
            # Solo validar duplicados si es un trade REAL (Shadow puede duplicar para experimentación)
            if not is_shadow:
                base_coin = self._get_base_coin(symbol)
                # Verificar si ya existe una posición REAL abierta en esta moneda
                for active_symbol, active_trade in self.active_trades.items():
                    if (
                        not active_trade.get("is_shadow", False)
                        and self._get_base_coin(active_symbol) == base_coin
                    ):
                        self.log(
                            f"⚠️ BLOQUEADO REAL {symbol}: Ya existe posición REAL abierta en {active_symbol}"
                        )
                        with self.db_lock:
                            self.brain.save_error_snapshot(
                                symbol,
                                "DUPLICATE_REAL",
                                self.data_service.sanitize_context(context),
                            )
                        return "DUPLICATE_REAL_COIN"

                # Limitador de Calor de Cartera (Sector Exposure)
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
                    for t in self.active_trades.values()
                    if t["sector"] == current_sector and not t.get("is_shadow", False)
                )
                if sector_count >= Config.MAX_SECTOR_EXPOSURE:
                    return f"MAX_SECTOR_EXPOSURE ({current_sector})"

            if symbol in self.active_trades:
                return "ALREADY_ACTIVE"
            if not is_shadow and symbol in self.cooldown_pairs:
                if datetime.now() < self.cooldown_pairs[symbol]:
                    return "COOLDOWN"

            actives = self.active_trades.values()
            num_real = sum(1 for t in actives if not t.get("is_shadow", False))
            num_shadow = sum(1 for t in actives if t.get("is_shadow", False))

            if not is_shadow:
                if num_real >= Config.MAX_OPEN_TRADES:
                    self.log(
                        f"⏳ LÍMITE REAL ALCANZADO ({num_real}): {symbol} ignorado."
                    )
                    return "MAX_REAL_TRADES"
                t_side = sum(
                    1
                    for t in actives
                    if t["side"] == side and not t.get("is_shadow", False)
                )
                if t_side >= Config.MAX_DIRECTIONAL_TRADES:
                    if num_shadow < Config.MAX_SHADOW_TRADES:
                        self.log(
                            f"🔄 LÍMITE DIRECCIONAL ({side}): {symbol} degradado a SHADOW para no perder oportunidad."
                        )
                        is_shadow = True
                        degradation_reason = "MAX_DIRECTIONAL_DEGRADED"
                    else:
                        self.log(
                            f"⏳ LÍMITE DIRECCIONAL ({side}) y SHADOW ({num_shadow}): {symbol} ignorado."
                        )
                        return "MAX_DIRECTIONAL"
            elif num_shadow >= Config.MAX_SHADOW_TRADES:
                self.log(
                    f"⏳ LÍMITE SHADOW ALCANZADO ({num_shadow}): {symbol} ignorado."
                )
                with self.db_lock:
                    self.brain.save_error_snapshot(
                        symbol,
                        "MAX_SHADOW",
                        self.data_service.sanitize_context(context),
                    )
                return "MAX_SHADOW"

        # --- FIX: PRECIO REALISTA PARA SHADOW (Slippage Simulado) ---
        try:
            ticker = self.execution.exchange.fetch_ticker(symbol)
            current_price = float(ticker["last"])
            if current_price > 0:
                price = current_price
        except Exception:
            pass

        try:
            # amount y calculated_position_size provienen del RiskEngine (Kelly Fraccional)
            final_usd = calculated_position_size

            if amount <= 0 or final_usd <= 0:
                self.log(
                    f"⚠️ ABORTO {symbol}: Tamaño inválido (amount={amount}, notional=${final_usd:.2f})"
                )
                return "SIZE_ERROR"

            # TP Validation
            fees = 0.001
            spread_cost = (context or {}).get("spread", 0.0)
            tp_pct = abs(tp_val - price) / price * 100
            min_tp = max(
                Config.MIN_TP_NET_PERCENT,
                (spread_cost + fees) * Config.MIN_TP_SPREAD_MULTIPLIER,
            )

            if tp_pct < min_tp:
                self.log(
                    f"🚫 TP INSUFICIENTE: {symbol} ({tp_pct:.2f}% < {min_tp:.2f}%)"
                )
                if not is_shadow:
                    return "TP_INSUFFICIENT"
                is_shadow = True

            if not is_shadow and not Config.PAPER_MODE:
                # IA HÍBRIDA: Ejecución de precisión v116
                self.log(
                    f"🚀 [PRECISION ENTRY] {symbol} {side} ${final_usd:.2f} @ {price:.5f} (Lev: {current_leverage}x)"
                )

                # 1. Establecer Palancamiento (Critical Professional Control)
                self.execution.set_leverage(current_leverage, symbol)

                # 2. Ejecutar Orden LIMIT IOC con Slippage Real (Config.MAX_SLIPPAGE = 0.001 -> 0.1%)
                # create_precision_order espera porcentaje (ej: 0.1)
                order_slippage = Config.MAX_SLIPPAGE * 100
                order = self.execution.create_precision_order(
                    symbol, side, amount, price, order_slippage
                )

                if order and order.get("status") in ["closed", "open", "filled"]:
                    self.log(f"✅ EJECUCIÓN EXITOSA: {symbol} ID: {order['id']}")
                    filled_amount = float(order.get("filled", amount))

                    # 3. Colocar HARD STOP LOSS en Binance (Insurance Policy)
                    self.log(f"🛡️ Colocando HARD SL en Binance: {symbol} @ {sl_val}")
                    self.execution.place_hard_sl(symbol, side, filled_amount, sl_val)

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

                    # ACTUALIZACIÓN DE SALDO LOCAL
                    margin_used = final_usd / current_leverage
                    self.available_balance -= margin_used
                else:
                    self.log(f"❌ FALLO DE EJECUCIÓN: {symbol}")
                    return "EXECUTION_FAILED"
            elif not is_shadow and Config.PAPER_MODE:
                # PAPER MODE SIMULATION
                self.log(
                    f"📝 PAPER TRADE (Simulado): {side} {symbol} (${final_usd:.2f})"
                )
                send_telegram_msg(
                    f"📝 *PAPER TRADE (SIMULACRO)*\n🔹 {symbol}\n🔸 Lado: {side}\n💰 Precio: {price}\n📊 Notional: ${final_usd:.2f}\n⚠️ *AVISO:* Bot en modo PAPER."
                )
            else:
                # SHADOW TRADE
                self.log(f"👻 SHADOW {side} {symbol} (${final_usd:.2f})")
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

            with self.lock:
                # Registro del trade en el estado del bot
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
                    "sector": "OTHE",  # Simplified for now
                    "leverage": current_leverage,
                    "market_snapshot": clean_snapshot,
                    "entry_ob": ob_status,
                    "entry_confidence": (context or {}).get("prob_final", 75.0),
                }

                if symbol not in self.active_trades:
                    self.active_trades[symbol] = trade_state
                    with self.db_lock:
                        self.brain.save_active_trade_state(symbol, trade_state)
                    self.log(
                        f"💾 CARTERA: {symbol} registrado ({'SHADOW' if is_shadow else 'REAL'})."
                    )
                else:
                    self.active_trades[symbol].update(trade_state)

                # Cooldown logic
                cooldown_minutes = (
                    Config.SHADOW_COOLDOWN_MINUTES
                    if is_shadow
                    else Config.TRADE_COOLDOWN_MINUTES
                )
                self.cooldown_pairs[symbol] = datetime.now() + timedelta(
                    minutes=cooldown_minutes
                )

                if not req_shadow and is_shadow:
                    return f"OK_DEGRADED: {degradation_reason}"
                return "OK"
        except Exception as e:
            self.log(f"❌ RECHAZO {symbol}: {e}")
            # CIRUGÍA LÁSER: Notificar fallos de ejecución no capturados para diagnóstico.
            if not is_shadow:
                send_telegram_msg(
                    f"❌ *FALLO DE EJECUCIÓN (REAL)*\n{symbol} no pudo abrirse.\nError: {str(e)[:100]}"
                )
            with self.db_lock:
                self.brain.save_error_snapshot(
                    symbol,
                    "EXEC_EXCEPTION",
                    self.data_service.sanitize_context(context),
                )
            return f"ERROR: {str(e)[:20]}"

    def close_trade(
        self,
        symbol: str,
        reason: str,
        exit_price: float,
        exit_confidence: float = 0.0,
        latency_context: Optional[Dict[str, Any]] = None,
    ):
        with self.lock:
            trade = self.active_trades.get(symbol)
        if not trade:
            return

        try:
            fees = 0
            if not trade.get("is_shadow", False) and not Config.PAPER_MODE:
                # [V116-ULTIMATE] DELEGACIÓN A EXECUTION SERVICE
                try:
                    self.log(
                        f"🔄 [CLOSING POSITION] {symbol} {trade['side']} (Reason: {reason})"
                    )
                    pre_api_ts = time.perf_counter()
                    if "DEGRADED" in reason or "CONF_DEGRADED" in reason:
                        order = self.execution.close_due_to_degradation(
                            symbol, trade["side"], trade["amount"]
                        )
                    else:
                        order = self.execution.close_position(
                            symbol, trade["side"], trade["amount"]
                        )
                    post_api_ts = time.perf_counter()

                    if order:
                        self.log(
                            f"✅ CIERRE EXITOSO: {symbol} ID: {order.get('id', 'N/A')}"
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
                        self.log(
                            f"⏱️ SMART_EXIT_LATENCY {symbol} trigger={trigger} signal_to_api_ms={signal_to_api_ms:.1f} api_ms={api_ms:.1f} total_ms={total_ms:.1f} target_ms=450 status={status}"
                        )

                except Exception as e:
                    self.log(f"❌ ERROR CRÍTICO CERRANDO {symbol}: {e}")

                    if any(
                        x in str(e).lower()
                        for x in ["notional", "-4164", "insufficient"]
                    ):
                        self.log(
                            f"⚠️ {symbol} descartado localmente (Dust/Min Notional)."
                        )
                        send_telegram_msg(
                            f"⚠️ *AVISO DUST*\n{symbol} cerrado virtualmente por monto bajo."
                        )
                        with self.lock:
                            if symbol in self.active_trades:
                                del self.active_trades[symbol]
                        return
                    else:
                        send_telegram_msg(
                            f"⚠️ *FALLO DE CIERRE REAL*\n{symbol} falló en Binance. Error: {e}"
                        )
                        raise e

                time.sleep(1)
                try:
                    # Acceso directo al exchange para histórico de trades (Mantenido por ahora)
                    my_trades = self.execution.exchange.fetch_my_trades(symbol, limit=2)
                    fees = sum(
                        t["fee"]["cost"]
                        for t in my_trades
                        if t["fee"]["currency"] == "USDT"
                    )
                except Exception:
                    pass
            else:
                # Simular comisión para SHADOW y PAPER (Config.PAPER_MODE=True)
                fees = (
                    trade["entry"] * float(trade["amount"]) * Config.VIRTUAL_FEE
                ) + (exit_price * float(trade["amount"]) * Config.VIRTUAL_FEE)
                if latency_context and latency_context.get("signal_ts") is not None:
                    total_ms = (
                        time.perf_counter() - float(latency_context["signal_ts"])
                    ) * 1000.0
                    trigger = latency_context.get("trigger", "UNKNOWN")
                    self.log(
                        f"⏱️ SMART_EXIT_LATENCY {symbol} trigger={trigger} total_ms={total_ms:.1f} simulated=1 (PAPER/SHADOW)"
                    )

            amt = float(trade["amount"])
            pnl_bruto_usd = (exit_price - trade["entry"]) * amt
            if trade["side"] == "SELL":
                pnl_bruto_usd *= -1

            # [CORRECCIÓN ESTRUCTURAL] PNL NETO Y DESCONTAMINACIÓN DE ML
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

            # DEBUG: Verificar que se llega aquí
            self.log(
                f"🔍 DEBUG: Intentando guardar trade {symbol} | is_shadow={trade.get('is_shadow', False)}"
            )

            try:
                with self.db_lock:
                    trade_id = self.brain.log_trade(
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
                            "market_regime": self._get_market_regime(),
                            "entry_confidence": trade.get("entry_confidence", 0.0),
                            "exit_confidence": exit_confidence,
                        }
                    )
                    # Log de depuración
                    self.log(
                        f"💾 Trade guardado #{trade_id if trade_id else 'N/A'}: {symbol} | "
                        f"is_shadow={trade.get('is_shadow', False)} | PnL={pnl_neto_percent:.2f}% | ${pnl_neto_usd:+.4f}"
                    )

                    # --- Punto #1: FEEDBACK LOOP (Reputación) ---
                    # Recuperamos los votos de los agentes de este trade
                    votos = trade.get("market_snapshot", {}).get("votos", {})
                    if votos:
                        # [V116] Shadow Logging Asíncrono
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
                        ctx_type = trade.get("market_snapshot", {}).get(
                            "context", "RANGE"
                        )
                        self.brain.update_agent_reputation(
                            votos, pnl_neto_percent, context_type=ctx_type
                        )

                    # --- Punto #2: AUTOPSIA POST-MORTEM (Enriquecimiento RAG) ---
                    post_mortem = {
                        "btc_price": self.market_btc_price,
                        "btc_change_tf": getattr(
                            self,
                            "market_btc_change_tf",
                            0.0,
                        ),
                        "exit_reason": reason,
                        "final_sentiment": self.current_sentiment[0],
                    }
                    # Actualizar la fila del trade con el post-mortem (simplificado: ya tenemos la columna)
                    # Nota: En una versión futura podríamos hacer un update SQL dedicado aquí.
            except Exception as e:
                self.log(f"⚠️ Error guardando trade o reputación {symbol}: {e}")

            with self.lock:
                if symbol in self.active_trades:
                    del self.active_trades[symbol]

            with self.db_lock:
                self.brain.delete_active_trade_state(symbol)

            # --- PUNTO #3: EVOLUCIÓN GENÉTICA ---
            if self.brain.evolve_genetics(symbol):
                self.log(
                    f"🧬 ADN MUTADO: {symbol} ha evolucionado sus parámetros SL/TP."
                )

            # --- EUREKA & ACTIVE STUDENT MODULE (v5.1) ---
            if trade.get("is_shadow", False):
                status, info = self.brain.check_eureka_status(symbol)
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

                    # --- VISUAL EUREKA: FOTO DEL CRIMEN ---
                    try:
                        df_snap = self.data_service.fetch_and_update_data(
                            symbol, Config.TIMEFRAME
                        )
                        if df_snap is not None and not df_snap.empty:
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
                    except Exception as e:
                        self.log(f"⚠️ Error Visual Eureka: {e}")
                        send_telegram_msg(msg)

                    self.log(f"🧠 EUREKA: {symbol} WR {info['wr']:.1f}%")

                elif status == "FAILURE":
                    self.brain.update_dynamic_settings(
                        symbol, 9.0
                    )  # Veto temporal (Score 9.0)
                    send_telegram_msg(
                        f"🛡️ *ESTUDIANTE ACTIVO: AUTO-CORRECCIÓN*\nHe detectado fallas repetidas en {symbol} ({info['wr']:.0f}% WR).\n📉 *Acción:* He vetado temporalmente este par."
                    )
                    self.log(f"🛡️ AUTO-VETO: {symbol} bloqueado por bajo rendimiento.")

            # --- NOTIFICACIÓN INTELIGENTE ---
            # Si el trade es shadow, usamos el fantasmita; si no, el candado.
            icono = "👻 SHADOW" if trade.get("is_shadow") else "🔒 REAL"

            self.log(
                f"{icono} CERRADO {symbol} ({reason}) | PnL: {pnl_neto_percent:.2f}% | ${pnl_neto_usd:+.4f}"
            )

            # Información adicional para el mensaje
            market_snap = trade.get("market_snapshot", {})
            entry_price = trade.get("entry", 0)
            entry_time = trade.get("open_time", "")
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
                except:
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
                f"💰 Entry: ${entry_price:.6f}\n"
                f"💸 Exit: ${exit_price:.6f}\n"
                f"⏱️ Duración: {duration}"
            )
            send_telegram_msg(msg_telegram)

            # --- BLOQUEO ANTI-REBOTE (Revenge Trading) ---
            # [INSTRUCCIÓN 2] COOLDOWN UNIVERSAL: Se activa al cerrar CUALQUIER trade (Real o Shadow)
            # Esto evita operaciones repetitivas en el mismo símbolo en pocos minutos
            now = datetime.now()
            default_cd_until = now + timedelta(minutes=Config.TRADE_COOLDOWN_MINUTES)
            self.cooldown_pairs[symbol] = default_cd_until
            self.log(
                f"❄️ COOLDOWN UNIVERSAL: {symbol} bloqueado por {Config.TRADE_COOLDOWN_MINUTES}m tras cierre."
            )

            # [NUEVO] SMART EXIT FREEZE: tras salida por degradación/bailout, congelar reentrada por 4h.
            # Objetivo: evitar re-entradas impulsivas en el mismo par tras tesis invalidada.
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
                if freeze_until > self.cooldown_pairs.get(symbol, now):
                    self.cooldown_pairs[symbol] = freeze_until
                self.log(
                    f"🧊 SMART EXIT FREEZE: {symbol} bloqueado por {freeze_hours:.0f}h (razón={reason_txt[:60]})."
                )

            # --- [v118-STRESS_TEST] ANTI-REVENGE LOGIC ---
            # Si acumulamos 2 pérdidas (Shadow o Real), el par se bloquea 6h.
            self.risk_engine.record_trade_result(symbol, pnl_neto_percent)

            # Cooldown adicional más largo para pérdidas en trades reales (anti-revenge)
            if pnl_neto_percent < 0 and not trade.get("is_shadow", False):
                anti_revenge_until = now + timedelta(hours=1)
                if anti_revenge_until > self.cooldown_pairs.get(symbol, now):
                    self.cooldown_pairs[symbol] = anti_revenge_until
                self.log(
                    f"🛡️ ANTI-REBOTE: {symbol} vetado por 1h adicional (pérdida en {'LONG' if trade['side'] == 'BUY' else 'SHORT'})."
                )

            # --- CIRCUIT BREAKER REACTIVO (v104.0) ---
            if pnl_neto_percent < -15.0 and not trade.get("is_shadow"):
                self.is_paused = True
                self.pause_time = datetime.now() + timedelta(hours=1)
                self.log(
                    f"☢️ CIRCUIT BREAKER: GAP masivo ({pnl_neto_percent:.2f}%). Pausando 1h."
                )
                send_telegram_msg(
                    f"☢️ *CIRCUIT BREAKER:* GAP masivo en {symbol} ({pnl_neto_percent:.2f}%). Modo Real pausado 1h por seguridad."
                )

            self._update_dynamic_risk()

            # AI COACH: Ejecutar cada 50 trades para aprendizaje continuo
            # (Lógica movida a tools/ai_coach.py para ejecución manual)
            pass

        except Exception as e:
            self.log(f"Error cerrando {symbol}: {e}")

    def _update_dynamic_risk(self):
        try:
            wins, losses = self.brain.get_recent_performance(last_n=5)
            total = wins + losses
            if total >= 3:
                win_rate = wins / total
                if win_rate >= 0.7:
                    self.risk_multiplier = 1.2
                elif win_rate <= 0.4:
                    self.risk_multiplier = 0.8
                else:
                    self.risk_multiplier = 1.0
            self.log(
                f"📉 Riesgo Dinámico: {self.risk_multiplier}x (WR: {wins}/{total})"
            )
        except Exception as e:
            self.log(f"⚠️ Err Risk Update: {e}")

    def monitor_open_trades(self):
        """[FASE 3: BAILOUT] Auditoría continua de posiciones abiertas (Inteligencia Activa)."""
        with self.lock:
            symbols = list(self.active_trades.keys())

        if not symbols:
            return

        for symbol in symbols:
            try:
                # [v114.6] IGNITION COOLDOWN: No bailouts en los primeros 15 minutos (micro-ruido inicial)
                # Permite que el trade respire y absorba el ruido de ejecución/spread.
                trade = self.active_trades.get(symbol)
                if not trade:
                    continue

                open_time = trade.get("open_time")
                if isinstance(open_time, str):
                    open_time = datetime.fromisoformat(open_time)

                if datetime.now() - open_time < timedelta(minutes=15):
                    # self.log(f"⏳ COOLDOWN ({symbol}): Ignorando bailout por juventud del trade.")
                    continue
                # 1. Obtener datos frescos (Sello Institucional: solo 1H + 4H)
                df_main = self.data_service.fetch_and_update_data(symbol, "1h")
                df_4h = self.data_service.fetch_and_update_data(symbol, "4h")

                if df_main is None or df_main.empty:
                    continue

                # 2. Re-evaluar con la IA (Consenso de los 14 Agentes)
                with self.db_lock:
                    res = Strategy.analyze(
                        df_main,
                        df_main,
                        self.brain,
                        symbol=symbol,
                        ghost_model=self.ghost_model,
                        scaler=self.scaler,
                        btc_delta_tf=getattr(
                            self,
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

                # --- [V116] SMART EXIT: SALIDA POR DEGRADACIÓN ---
                is_degraded, deg_reason = self.risk_engine.check_signal_integrity(
                    trade, prob_final, elapsed_mins
                )

                if is_degraded:
                    entry_conf = trade.get("entry_confidence", 0)
                    self.log(
                        f"🚨 DEGRADED EXIT ({symbol}): {deg_reason} | EntryConf: {entry_conf:.1f} -> ExitConf: {prob_final:.1f}"
                    )

                    # Cierre inmediato ignorando TP/SL mediante ExecutionService
                    self.close_trade(
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

            except Exception as e:
                # Solo loguear errores importantes, no spam
                err_str = str(e)
                if "symbol" in err_str.lower() or "not found" in err_str.lower():
                    self.log(
                        f"⚠️ Error monitoreando {symbol}: Símbolo no disponible en Binance"
                    )
                else:
                    self.log(f"⚠️ Error monitoreando {symbol}: {e}")

    def _guardian_loop(self):
        self.log("🛡️ Guardián OK.")
        last_heavy = 0
        while self.is_running:
            loop_started = time.perf_counter()
            try:
                with self.lock:
                    snapshot = self.active_trades.copy()

                # [v117] BAILOUT PRIORITARIO: Monitoreo de integridad de señales (Smart Exit)
                self.monitor_open_trades()

                syms = list(snapshot.keys())
                if not syms:
                    time.sleep(1)
                    continue

                # --- OPTIMIZACIÓN DE LATENCIA (v106.x) ---
                # Prioridad: 1. Websocket (ms) -> 2. REST Mass (s) -> 3. REST Single (lento)
                with self.price_lock:
                    price_map = self.live_prices.copy()

                if not price_map:
                    try:
                        all_prices_raw = (
                            self.execution.exchange.fapiPublicGetTickerPrice()
                        )
                        price_map = {p["symbol"]: p["price"] for p in all_prices_raw}
                    except Exception as e:
                        # self.log(f"⚠️ Guardian: fapiPublicGetTickerPrice falló: {e}")
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

                        # --- [v118-PRO] PRIORIDAD ABSOLUTA: SMART EXIT (BAILOUT) ---
                        # Si la confianza actual < 70% de la inicial, cerrar de inmediato.
                        # Este chequeo ocurre ANTES de cualquier actualización de precios o UI.
                        current_conf = t.get("current_confidence", 50.0)
                        entry_conf = t.get("entry_confidence", 75.0)
                        abort_needed, abort_reason = (
                            self.risk_engine.should_abort_trade(
                                entry_conf, current_conf
                            )
                        )

                        if abort_needed:
                            self.log(
                                f"🚨 [v118-BAILOUT] {s}: Abortando por degradación de señal."
                            )
                            self._guardian_stats["bailout_count"] += 1
                            self.close_trade(
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
                                curr = float(
                                    self.execution.exchange.fetch_ticker(s)["last"]
                                )
                            except Exception as fetch_e:
                                self.log(
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

                        # [NUEVO] Activación temprana de Break-Even para asegurar ganancias.
                        # Si el trade alcanza +1.5% (neto), movemos SL a entrada + buffer de fees.
                        if t[
                            "pnl"
                        ] >= Config.EARLY_BREAKEVEN_ACTIVATION_PNL and not t.get(
                            "early_be_armed", False
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
                            self.log(
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
                        # [SMART TIME LIMIT v114] No cerrar si va ganando (PnL > 0)
                        duration_mins = (datetime.now() - ot).total_seconds() / 60
                        if duration_mins >= Config.MAX_TRADE_DURATION_MINUTES:
                            if (
                                t["pnl"] <= 0
                                or duration_mins
                                >= Config.MAX_TRADE_DURATION_MINUTES * 2
                            ):
                                self.close_trade(
                                    s,
                                    f"Time Limit {Config.MAX_TRADE_DURATION_MINUTES}m{' (Force)' if t['pnl'] > 0 else ''}",
                                    curr,
                                )
                                continue
                            else:
                                if not t.get("time_limit_warning"):
                                    self.log(
                                        f"⏳ {s}: Superado Time Limit {Config.MAX_TRADE_DURATION_MINUTES}m pero PnL {t['pnl']:.2f}% > 0. Manteniendo..."
                                    )
                                    t["time_limit_warning"] = True

                        # --- NUEVO: DYNAMIC TRAILING (GHOST SENSITIVE) ---
                        # Si el trade va ganando (>0.5%) pero el Agente Ghost detecta peligro, apretamos a Break Even.
                        # [FIX] Solo activo para RF. LSTM requiere secuencia de 60 velas no disponible en bucle rápido.
                        if (
                            t["pnl"] > 0.5
                            and not t.get("ghost_checked", False)
                            and self.ghost_model
                            and self.ghost_model_type == "RF"
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

                                if hasattr(self.ghost_model, "predict_proba"):
                                    prob = self.ghost_model.predict_proba(features)[0][
                                        1
                                    ]

                                    if (
                                        prob < 0.48
                                    ):  # [v117-RELAXED] Umbral bajado de 0.55 a 0.48 para dar aire
                                        self.log(
                                            f"👻 GHOST ALERT {s}: Probabilidad cayó a {prob:.2f} (Umbral 0.48). Apretando SL a Break Even."
                                        )
                                        t["sl"] = t["entry"] * (
                                            1.001 if t["side"] == "BUY" else 0.999
                                        )  # Asegurar fees
                                        t["ghost_checked"] = (
                                            True  # Solo chequear una vez para no saturar
                                        )
                            except (AttributeError, KeyError, IndexError):
                                pass  # Modelo no disponible o datos insuficientes

                        # HARD STOP LOSS: Límite absoluto de pérdida
                        max_loss = (
                            Config.SHADOW_HARD_SL_PERCENT
                            if t.get("is_shadow", False)
                            else Config.REAL_HARD_SL_PERCENT
                        )
                        if t["pnl"] <= max_loss:
                            self.close_trade(s, f"Hard SL ({max_loss}%)", curr)
                            continue

                        # === [NUEVO v114] TAKE PROFIT ESCALONADO ===
                        # TP1: Cerrar 50% de la posición a +1%
                        if Config.TP1_ENABLED and not t.get("tp1_triggered", False):
                            if t["pnl"] >= Config.TP1_LEVEL:
                                # Cerrar 50% del tamaño
                                close_amount = t.get("size_usd", 0) * (
                                    Config.TP1_PERCENT / 100
                                )
                                if close_amount > 0:
                                    self.log(
                                        f"🎯 TP1 HIT: {s} - Cerrando 50% @ +{Config.TP1_LEVEL}%"
                                    )
                                    # Cerrar posición parcial
                                    try:
                                        params = {"reduceOnly": True}
                                        if self.is_hedge_mode:
                                            params["positionSide"] = (
                                                "LONG"
                                                if t["side"] == "BUY"
                                                else "SHORT"
                                            )
                                        self.execution.exchange.create_order(
                                            s,
                                            "MARKET",
                                            "SELL" if t["side"] == "BUY" else "BUY",
                                            close_amount / curr,
                                            params=params,
                                        )
                                    except Exception as e:
                                        self.log(f"⚠️ Error TP1: {e}")

                                    # [FIX v114] Marcar siempre como disparado para evitar bucles infinitos en errores de precisión/min_notional
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
                                self.log(
                                    f"🎯 TP2 HIT: {s} - Cerrando resto @ +{Config.TP2_LEVEL}%"
                                )
                                self.close_trade(s, f"TP2 ({Config.TP2_LEVEL}%)", curr)
                                continue

                        # Stop Loss Dinámico (secundario)
                        if (t["side"] == "BUY" and curr <= t["sl"]) or (
                            t["side"] == "SELL" and curr >= t["sl"]
                        ):
                            self.close_trade(s, "Dynamic SL", curr)

                    except Exception as e:
                        self.log(f"Guardian error en {s}: {e}")

                # 15s: Sincronización y Trailing pesado
                if time.time() - last_heavy > 15:
                    self.sync_wallet()

                    # --- OPTIMIZACIÓN VIP: Primero REALES, luego SHADOW ---
                    # Esto evita que el procesamiento de 30 trades shadow bloquee la protección de tu dinero real.
                    sorted_trades = sorted(
                        list(self.active_trades.keys()),
                        key=lambda k: self.active_trades.get(k, {}).get(
                            "is_shadow", True
                        ),
                    )

                    for s in sorted_trades:
                        t = self.active_trades.get(s)
                        if not t or not t.get("trailing_active"):
                            continue

                        # === [MEJORADO v114] TRAILING STOP DINÁMICO ===
                        # Si TP1 ya fue ejecutado, usar trailing más agresivo
                        if Config.TRAIL_AFTER_TP1 and t.get("tp1_triggered", False):
                            # Trailing más agresivo después del TP1
                            trail_distance = Config.TRAIL_ENTRY_OFFSET  # 0.5%
                            if t["pnl"] >= Config.TP2_LEVEL:
                                trail_distance = 1.0  # [v117-OPTIMIZED] Subido de 0.3 a 1.0 para evitar asfixia post-TP1
                        else:
                            # Trailing normal basado en ATR
                            try:
                                df_main = self.data_service.fetch_and_update_data(
                                    s, "1h"
                                )
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
                            except:
                                trail_distance = Config.TRAILING_ACTIVATION_PNL

                        # Usamos Config.TRAILING_ACTIVATION_PNL para consistencia
                        if (
                            t["pnl"] <= (t.get("peak_pnl", 0) - trail_distance)
                            and t["pnl"] > Config.TRAILING_ACTIVATION_PNL
                        ):
                            self.close_trade(s, "Trailing (ATR)", t["last_price"])
                        # NUEVO: Protección de breakeven para trades con buen profit
                        if t["pnl"] > Config.TRAILING_BREAKEVEN_PNL and t["pnl"] <= (
                            t.get("peak_pnl", 0) - Config.TRAILING_BREAKEVEN_PULLBACK
                        ):
                            self.close_trade(
                                s,
                                "Trailing (Breakeven Protection)",
                                t["last_price"],
                            )
                    last_heavy = time.time()

            except Exception as e:
                self.log(f"Err Guardián: {e}")

            # MODULACIÓN DE FRECUENCIA v117: 0.1s para dominio < 500ms (trades activos), 2s tranquilo
            sleep_for = 0.1 if self.active_trades else 2.0
            work_s = max(time.perf_counter() - loop_started, 0.0)
            self._guardian_stats["loops"] += 1
            self._guardian_stats["work_s"] += work_s
            self._guardian_stats["sleep_s"] += sleep_for
            time.sleep(sleep_for)

    def ai_coach_allows_escalation(self):
        """Decide si es seguro buscar la siguiente meta basado en el sentimiento."""
        # Si la tendencia es bajista, mejor asegurar lo ganado y no arriesgar más.
        if self.current_sentiment[0] == "🔴 TENDENCIA BAJISTA":
            return False
        return True

    def check_safety_and_goals(self, current_pnl=None):
        base_bal = (
            self.daily_initial_balance
            if self.daily_initial_balance > 0
            else self.balance
        )

        if current_pnl is None:
            current_pnl = 0.0

        if current_pnl > self.peak_pnl:
            self.peak_pnl = current_pnl

        # 1. Trailing Stop de Cuenta: Si perdemos 3% desde el punto más alto del día
        if (
            self.peak_pnl > 0
            and (self.peak_pnl - current_pnl) >= Config.DAILY_TRAILING_STOP
        ):
            self.circuit_breaker_active = True
            self.log(
                f"⚠️ Trailing Stop: Protegiendo {current_pnl:.2f}% (Caída del 3% desde el pico)"
            )
            return False

        # 2. Límite de Pérdida Diaria: -3% desde el inicio
        if current_pnl <= -Config.DAILY_LOSS_LIMIT:
            self.circuit_breaker_active = True
            self.mandatory_train_pending = True
            self.is_paused = True
            self.log(
                f"💀 Límite diario alcanzado: {current_pnl:.2f}%. MODO DEFENSIVO ACTIVADO."
            )
            send_telegram_msg(
                f"🛡️ *MODO DEFENSIVO ACTIVADO*\nPérdida diaria límite alcanzada. El bot requiere re-entrenamiento para continuar."
            )
            return False

        # 3. Gestión de Metas (5% -> 10% -> 15%)
        for goal in Config.DAILY_GOALS:
            if current_pnl >= goal and self.current_target == goal:
                self.log(f"🚀 Meta de {goal}% alcanzada.")
                # Aquí podrías añadir lógica para que el bot pare o suba el target
                # Por ahora, subimos el target para que se refleje en el UI
                try:
                    next_idx = Config.DAILY_GOALS.index(goal) + 1
                    if next_idx < len(Config.DAILY_GOALS):
                        self.current_target = Config.DAILY_GOALS[next_idx]
                    else:
                        self.circuit_breaker_active = True  # Meta final 15% alcanzada
                except Exception:
                    pass
        return True

    def start_silent_sync(self):
        """Bucle que sincroniza el balance con Binance cada 1 hora."""
        while self.is_running:
            try:
                with self.lock:
                    # Obtener balance real de la API (ATÓMICO)
                    actual_balance = self.get_current_balance()

                    # Si no hay trades abiertos, el balance interno debe ser igual al de la API
                    if not self.active_trades:
                        self.balance = actual_balance
                        self.log(
                            f"🔄 SYNC: Balance sincronizado silenciosamente: ${actual_balance:.2f}"
                        )
                        self.brain.log_equity(
                            self.balance
                        )  # Registrar punto en la curva

                time.sleep(3600)  # Espera 1 hora
            except Exception as e:
                self.log(f"⚠️ Error en sincronización de balance: {e}")
                time.sleep(60)

    def get_current_balance(self):
        """Obtiene el balance total en USDT desde Binance (v116)."""
        try:
            return self.execution.get_balance()
        except Exception as e:
            self.log(f"⚠️ Error obteniendo balance: {e}")
            return getattr(self, "available_balance", 0.0)

    def handle_reset_pnl(self):
        """Limpia el historial de hoy y resetea el balance inicial."""
        try:
            # 1. Ejecutar rotación de historial (Mantenimiento de 3 meses)
            self.brain.rotate_history(days_to_keep=90)
            self.brain.reset_daily_stats()
            self.balance = self.get_current_balance()
            self.daily_initial_balance = self.balance

            with self.lock:
                self.balance = self.get_current_balance()
            # --- FIX: RESET COMPLETO DE ESTADO ---
            self.peak_pnl = 0.0
            self.circuit_breaker_active = False
            self.current_target = Config.DAILY_GOALS[0]  # Reiniciar meta al 5%

            self.log("♻️ SISTEMA REINICIADO: Historial rotado y balance inicial fijado.")
            return "🔄 *PNL RESETEADO:* Balance inicial fijado en ${:.2f}. Meta reiniciada al 5.0%. Todo limpio para hoy.".format(
                self.balance
            )
        except Exception as e:
            return f"⚠️ Error Reset PnL: {e}"

    def perform_healthcheck(self):
        """Verifica conectividad y balance (v104.5)."""
        try:
            balance = self.execution.exchange.fetch_balance()
            usdt = balance["total"].get("USDT", 0)
            btc = self.execution.exchange.fetch_ticker("BTC/USDT:USDT")["last"]
            return (
                f"🩺 *DIAGNÓSTICO v104.5*\n"
                f"💰 Balance: ${usdt:.2f} USDT\n"
                f"₿ BTC: ${btc:,.2f}\n"
                f"🧠 IA: Modelo Cargado\n"
                f"🚀 Estado: LISTO"
            )
        except Exception as e:
            return f"🚨 Error en Diagnóstico: {str(e)}"

    def get_ob_efficiency_report(self):
        """Genera la comparativa: ¿Es mejor operar con OB o sin OB?"""
        trades = self.brain.get_todays_trades()

        with_ob = [t for t in trades if t.get("entry_ob", "⚪") != "⚪"]
        no_ob = [t for t in trades if t.get("entry_ob", "⚪") == "⚪"]

        def calc_stats(group):
            if not group:
                return "0 trades"
            wins = sum(1 for t in group if t["pnl_percent"] > 0)
            wr = (wins / len(group)) * 100
            pnl = sum(t["pnl_percent"] for t in group)
            return f"{len(group)} T | WR: {wr:.1f}% | PNL: {pnl:+.2f}%"

        report = (
            "📊 *REPORTE DE EFICIENCIA OB*\n"
            f"🏛️ *CON APOYO OB:* {calc_stats(with_ob)}\n"
            f"🧠 *SOLO IA (SIN OB):* {calc_stats(no_ob)}\n"
        )
        return report

    def check_weekly_schedule(self):
        """Envía el reporte de evolución los domingos a las 20:00."""
        ahora = datetime.now()
        if ahora.weekday() == 6 and ahora.hour == 20 and ahora.minute == 0:
            if not self._weekly_sent:
                try:
                    from evolution_logger import get_evolution_report

                    reporte = get_evolution_report()
                    send_telegram_msg(
                        f"📊 *RESUMEN DE CRECIMIENTO SEMANAL*\n\n{reporte}"
                    )
                except Exception as e:
                    self.log(f"⚠️ Error en reporte semanal: {e}")
                self._weekly_sent = True
        elif ahora.hour != 20:
            self._weekly_sent = False

    def check_weekly_maintenance_utc(self):
        """Domingo 00:00 UTC: purga shadow >30d y VACUUM en sniper_brain.db."""
        now_utc = datetime.utcnow()
        maintenance_key = f"{now_utc.isocalendar().year}-W{now_utc.isocalendar().week}"

        if now_utc.weekday() == 6 and now_utc.hour == 0 and now_utc.minute < 5:
            if self._last_weekly_maintenance_utc != maintenance_key:
                self.log("🧹 Mantenimiento semanal DB (UTC): iniciando purge+VACUUM...")
                result = self.brain.weekly_maintenance(shadow_days_to_keep=30)
                if result.get("error"):
                    self.log(f"⚠️ Mantenimiento DB falló: {result['error']}")
                else:
                    self.log(
                        f"✅ Mantenimiento DB OK: shadow_deleted={result.get('shadow_deleted', 0)} cutoff={result.get('cutoff')} vacuum={result.get('vacuum_ok', False)}"
                    )
                self._last_weekly_maintenance_utc = maintenance_key

    def handle_command(self, text: str):
        """
        Centraliza la lógica de comandos para Telegram y Dashboard.
        """
        text = text.strip()

        if text == "/help":
            msg = (
                "🤖 *SNIPER AI v117 - CENTRO DE MANDO*\n\n"
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
            send_telegram_msg(msg)

        elif text == "/audit" or text == "/report100":
            send_telegram_msg("🔍 *GENERANDO REPORTE DE AUDITORÍA...*")
            try:
                with self.db_lock:
                    trades = self.brain.get_last_n_trades(100)
                from reporter import generate_audit_report

                report = generate_audit_report(trades)
                send_telegram_msg(report)
            except Exception as e:
                send_telegram_msg(f"❌ Error generando auditoría: {e}")

        elif text == "/audit_report":
            send_telegram_msg("🔍 *GENERANDO REPORTE DE AUDITORÍA (Últimos 100)...*")
            try:
                with self.db_lock:
                    trades = self.brain.get_last_n_trades(100)

                if not trades:
                    send_telegram_msg("No hay trades para auditar.")
                    return

                wins = sum(1 for t in trades if t["pnl_percent"] > 0)
                losses = len(trades) - wins
                win_rate = (wins / len(trades)) * 100 if trades else 0
                total_pnl = sum(t["pnl_percent"] for t in trades)
                avg_pnl = total_pnl / len(trades) if trades else 0

                real_trades = [t for t in trades if not t.get("is_shadow")]
                shadow_trades = [t for t in trades if t.get("is_shadow")]

                real_wins = sum(1 for t in real_trades if t["pnl_percent"] > 0)
                real_wr = (real_wins / len(real_trades)) * 100 if real_trades else 0
                real_pnl = sum(t["pnl_percent"] for t in real_trades)

                shadow_wins = sum(1 for t in shadow_trades if t["pnl_percent"] > 0)
                shadow_wr = (
                    (shadow_wins / len(shadow_trades)) * 100 if shadow_trades else 0
                )
                shadow_pnl = sum(t["pnl_percent"] for t in shadow_trades)

                top_symbols = Counter(t["symbol"] for t in trades).most_common(5)

                msg = (
                    f"📊 *REPORTE DE AUDITORÍA (Últimos {len(trades)} Trades)*\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"📈 *General:*\n"
                    f"  - Win Rate: {win_rate:.1f}% ({wins}W / {losses}L)\n"
                    f"  - PnL Total: {total_pnl:+.2f}%\n"
                    f"  - PnL Promedio: {avg_pnl:+.2f}%\n\n"
                    f"🔥 *Reales ({len(real_trades)}):*\n"
                    f"  - Win Rate: {real_wr:.1f}%\n"
                    f"  - PnL Total: {real_pnl:+.2f}%\n\n"
                    f"👻 *Shadows ({len(shadow_trades)}):*\n"
                    f"  - Win Rate: {shadow_wr:.1f}%\n"
                    f"  - PnL Total: {shadow_pnl:+.2f}%\n\n"
                    f"🏆 *Símbolos más operados:*\n"
                )
                for sym, count in top_symbols:
                    msg += f"  - {sym}: {count} trades\n"

                send_telegram_msg(msg)
            except Exception as e:
                send_telegram_msg(f"❌ Error generando auditoría: {e}")

        elif text == "/export_data":
            if export_dataset:
                export_dataset()
                send_telegram_msg("✅ Dataset Maestro exportado correctamente.")
            else:
                send_telegram_msg("❌ Script de exportación no encontrado.")

        elif text == "/tiers":
            if not self.scanner_history:
                send_telegram_msg("🕵️ *TIERS:* No hay señales en el radar todavía.")
                return

            tiers = {"ELITE": [], "GOLD": [], "SILVER": [], "IRON": []}
            for item in self.scanner_history:
                t = item.get("tier", "IRON")
                if t in tiers:
                    tiers[t].append(f"{item['symbol']} ({item['ia_prob']})")

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

        elif text == "/dump_db":
            send_telegram_msg(
                "📦 *EXPORTANDO BASE DE DATOS...*\nEsto puede tomar unos segundos."
            )
            try:
                import subprocess

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
            except Exception as e:
                send_telegram_msg(f"❌ Error: {e}")

        elif text in ["/on", "/resume"]:
            if self.mandatory_train_pending:
                send_telegram_msg(
                    "🛡️ *MODO DEFENSIVO ACTIVO*: No se puede reanudar sin re-entrenamiento. Use /force_train."
                )
            else:
                self.is_paused = False
                send_telegram_msg("🟢 *SISTEMA ACTIVO*")

        elif text in ["/off", "/pause"]:
            self.is_paused = True
            send_telegram_msg("🟡 *SISTEMA EN PAUSA*")

        elif text in ["/panic", "/closeall"]:
            self.is_paused = True
            self._close_all_positions_emergency()
            send_telegram_msg("🔴 *EMERGENCIA*: Todo cerrado en Binance.")

        elif text == "/status":
            ai = self.brain.get_ai_maturity()
            pnl_pct, pnl_usd = self.brain.get_daily_real_pnl(self.balance)

            # Estado de Auto-Ajuste (Exigencia)
            exigencia_txt = "Normal"
            if self.dynamic_offset > 0:
                exigencia_txt = f"🔒 ALTA (+{self.dynamic_offset * 100:.0f}% req)"

            msg = (
                f"📊 *ESTADO {Config.VERSION}*\n"
                f"• Modo: {'🧪 PAPER/SHADOW' if Config.PAPER_MODE else '🔥 REAL'}\n"
                f"• Motor TF: 1H | Macro: 4H\n"
                f"• IA: {ai['rank']} ({ai['xp_percent']}%)\n"
                f"• PnL: {pnl_pct:+.2f}% (${pnl_usd:+.2f})\n"
                f"• Exigencia: {exigencia_txt}\n"
                f"• Python: {platform.python_version()}"
            )
            send_telegram_msg(msg)

        elif text == "/thinking":
            vetos = self.brain.get_recent_vetos(limit=3)
            msg = "🧠 *PROCESO DE PENSAMIENTO IA*\n━━━━━━━━━━━━━━━━━━━━\n"
            if not vetos:
                msg += "Esperando nuevas señales para analizar..."
            else:
                for v in vetos:
                    msg += f"📍 *{v['symbol']}*: {v['reason']}\n"
                    msg += f"💡 _Contexto:_ {v['context_summary']}\n\n"
            msg += f"🔄 *Estado:* Analizando {len(self.pairs_to_scan)} pares."
            send_telegram_msg(msg)

        elif text == "/quarantine":
            msg = "☣️ *ZONA DE CUARENTENA (Strike System)*\n"
            msg += "Monedas bloqueadas por 3 pérdidas consecutivas:\n━━━━━━━━━━━━━━━━━━━━\n"
            count = 0
            for s_raw in self.pairs_to_scan:
                s = s_raw.split(":")[0]
                if self.brain.check_consecutive_losses(s, 15):
                    msg += f"• {s} 🚫\n"
                    count += 1
            if count == 0:
                msg += "✅ Ninguna moneda en cuarentena. El mercado está sano."
            else:
                msg += f"\nTotal: {count} activos vetados temporalmente."
            send_telegram_msg(msg)

        elif text == "/agents":
            reps = self.brain.get_agent_reputation()
            msg = "🕵️ *REPUTACIÓN DE AGENTES (CONFIDENCE)*\n━━━━━━━━━━━━━━━━━━━━\n"

            agent_names = {
                "T": "🛠️ Técnico",
                "V": "👁️ Visual",
                "J": "⚖️ Juez",
                "G": "👻 Ghost",
                "C": "🔗 Correl",
                "L": "💧 Liquidez",
                "F": "😫 Fatiga",
                "S": "📢 Sentimiento",
                "R": "🧠 RAG Vectorial",
            }

            for agent_id, score in sorted(
                reps.items(), key=lambda x: x[1], reverse=True
            ):
                name = agent_names.get(agent_id, agent_id)
                icon = "🟢" if score >= 100 else ("🟡" if score >= 90 else "🔴")
                msg += f"{icon} *{name}:* {score:.1f}\n"

            msg += "\n_Nota: >100 = Racha Ganadora | <90 = En Observación_"
            send_telegram_msg(msg)

        # [FIX] Eliminado bloque duplicado de /dna para permitir la versión detallada (más abajo)
        elif text == "/intelligence":
            intel = self.brain.get_model_insights()
            ai_xp = self.brain.get_ai_maturity()
            msg = (
                f"🧠 *MAPA MENTAL DE LA IA (v106.0)*\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"📈 *Confianza:* {ai_xp['rank']} ({ai_xp['xp_percent']}%)\n\n"
                f"🔍 *INDICADORES MÁS RELEVANTES:*\n"
            )
            for feature, importance in intel["top_features"]:
                msg += f"• {feature.upper()}: {importance * 100:.1f}% de peso\n"
            msg += f"\n🎯 *ESTRATEGIA RECIÉN APRENDIDA:*\n_{intel['learned_rule']}_"
            send_telegram_msg(msg)

        elif text == "/ai_intel":
            send_telegram_msg("🎨 Generando inteligencia visual (XAI)...")
            try:
                from tools.ai_mapper import generate_ai_intel_image

                img_bio = generate_ai_intel_image(self.brain)
                files = {"photo": ("ai_intel.png", img_bio, "image/png")}
                requests.post(
                    f"https://api.telegram.org/bot{Config.TELEGRAM_TOKEN}/sendPhoto?chat_id={Config.TELEGRAM_CHAT_ID}",
                    files=files,
                )
            except Exception as e:
                send_telegram_msg(f"❌ Error generando XAI: {e}")

        elif text == "/report":
            from reporter import generate_mobile_report

            msg = generate_mobile_report(self.balance)
            send_telegram_msg(f"📝 *REPORTE DE RENDIMIENTO*\n{msg}")

        elif text == "/open":
            with self.lock:
                if not self.active_trades:
                    send_telegram_msg("📭 No hay trades activos en este momento.")
                else:
                    trades_list = list(self.active_trades.items())
                    if len(trades_list) > 20:
                        msg = (
                            f"🔍 *POSICIONES ABIERTAS ({len(trades_list)})* - Top 10\n"
                        )
                        for s, t in trades_list[:10]:
                            msg += f"\n• {s} ({t['side']}): {t.get('pnl', 0):+.2f}%"
                        msg += (
                            "\n\n⚠️ _Lista truncada. Revise logs para detalle completo._"
                        )
                        send_telegram_msg(msg)
                    else:
                        msg = "🔍 *POSICIONES ABIERTAS*\n"
                        for s, t in trades_list:
                            msg += f"\n• {s} ({t['side']}): {t.get('pnl', 0):+.2f}%"
                        send_telegram_msg(msg)

        elif text == "/top":
            tops = [
                p
                for p in self.scanner_history
                if float(p.get("ia_prob", "0%").replace("%", "")) > 90
            ]
            if not tops:
                send_telegram_msg("🔭 No hay señales de alta probabilidad.")
            else:
                msg = "🎯 *TOP 3 SEÑALES IA*\n"
                for t in sorted(
                    tops,
                    key=lambda x: float(x.get("ia_prob", "0%").replace("%", "")),
                    reverse=True,
                )[:3]:
                    msg += f"\n💎 {t['symbol']}: *{t['ia_prob']}*"
                send_telegram_msg(msg)

        elif text == "/targets":
            if not self.pairs_to_scan:
                send_telegram_msg("🔭 Radar vacío o inicializando...")
            else:
                msg = f"🎯 *OBJETIVOS ACTIVOS ({len(self.pairs_to_scan)})*\n"
                pairs_str = ", ".join(self.pairs_to_scan)
                if len(pairs_str) > 4000:
                    pairs_str = pairs_str[:4000] + "..."
                send_telegram_msg(f"{msg}{pairs_str}")

        elif text == "/paper_review":
            trades = self.brain.get_paper_trades_history(limit=50)
            if not trades:
                send_telegram_msg(
                    "📭 No hay historial de trades PAPER/REAL para analizar."
                )
            else:
                wins = sum(1 for t in trades if t["pnl_percent"] > 0)
                total = len(trades)
                wr = (wins / total) * 100

                # Calcular PnL acumulado en %
                total_pnl = sum(t["pnl_percent"] for t in trades)
                avg_pnl = total_pnl / total

                msg = (
                    f"📝 *ANÁLISIS DE PAPER TRADES (Últimos {total})*\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"🏆 *Win Rate:* {wr:.1f}%\n"
                    f"📈 *PnL Acumulado:* {total_pnl:+.2f}%\n"
                    f"📊 *Promedio:* {avg_pnl:+.2f}% por trade\n\n"
                    f"💡 _Si esto fuera dinero real, tendrías un PnL de {total_pnl:+.2f}%_"
                )
                send_telegram_msg(msg)

        elif text == "/performance_trends":
            trends = self.brain.get_stats_by_trend()
            if not trends:
                send_telegram_msg(
                    "📭 Aún no hay suficientes datos con snapshots para este análisis."
                )
            else:
                msg = "📊 *EFICIENCIA POR TIPO DE MERCADO*\n━━━━━━━━━━━━━━━━━━━━\n"
                icons = {
                    "UP": "🚀 ALCISTA",
                    "DOWN": "📉 BAJISTA",
                    "RANGO": "↔️ RANGO",
                    "NEUTRAL": "⚪ NEUTRAL",
                }
                for label, data in trends.items():
                    icon = icons.get(label, f"❓ {label}")
                    msg += (
                        f"{icon}:\n"
                        f"• Trades: {data['total']}\n"
                        f"• Winrate: *{data['winrate']}%*\n"
                        f"• PnL Promedio: {data['avg_pnl']:+.2f}%\n\n"
                    )
                msg += (
                    "💡 _Dato: La IA usa estos números para autogestionar su riesgo._"
                )
                send_telegram_msg(msg)

        elif text == "/shadow_report":
            trades = self.brain.get_todays_trades()
            # Filtrar solo Shadow
            shadows = [t for t in trades if t.get("is_shadow")]

            # Estadísticas de HOY
            c_today = len(shadows)
            wins_list = [t["pnl_percent"] for t in shadows if t["pnl_percent"] > 0]
            losses_list = [t["pnl_percent"] for t in shadows if t["pnl_percent"] <= 0]

            w_today = len(wins_list)
            l_today = len(losses_list)
            wr_today = (w_today / c_today * 100) if c_today > 0 else 0.0

            avg_win_pct = sum(wins_list) / w_today if w_today > 0 else 0.0
            avg_loss_pct = sum(losses_list) / l_today if l_today > 0 else 0.0

            # Proyección Base $20
            base_usd = 20.0
            avg_win_usd = (avg_win_pct / 100) * base_usd
            avg_loss_usd = (avg_loss_pct / 100) * base_usd

            # Estadísticas TOTALES (Históricas)
            c_total, w_total, l_total, wr_total = 0, 0, 0, 0.0
            h_avg_win, h_avg_loss = 0.0, 0.0
            try:
                conn = sqlite3.connect("sniper_brain.db")
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT COUNT(*), SUM(CASE WHEN pnl_percent > 0 THEN 1 ELSE 0 END), AVG(CASE WHEN pnl_percent > 0 THEN pnl_percent END), AVG(CASE WHEN pnl_percent <= 0 THEN pnl_percent END) FROM trades WHERE is_shadow=1"
                )
                row = cursor.fetchone()
                if row:
                    c_total = row[0] or 0
                    w_total = row[1] or 0
                    h_avg_win = row[2] or 0.0
                    h_avg_loss = row[3] or 0.0
                    l_total = c_total - w_total
                    wr_total = (w_total / c_total * 100) if c_total > 0 else 0.0
                conn.close()
            except Exception as e:
                self.log(f"⚠️ Error DB Shadow Report: {e}")

            h_win_usd = (h_avg_win / 100) * base_usd
            h_loss_usd = (h_avg_loss / 100) * base_usd

            msg = (
                f"👻 *REPORTE SHADOW (Modo Aspiradora)*\n"
                f"📅 {datetime.now().strftime('%d/%m/%Y')}\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"📅 *HOY:*\n"
                f"• Trades: {c_today}\n"
                f"• Ganados: {w_today} ✅\n"
                f"• Perdidos: {l_today} ❌\n"
                f"• Win Rate: *{wr_today:.1f}%*\n"
                f"• Avg Win: +{avg_win_pct:.1f}% (+${avg_win_usd:.2f})\n"
                f"• Avg Loss: {avg_loss_pct:.1f}% (${avg_loss_usd:.2f})\n\n"
                f"📚 *HISTÓRICO TOTAL:*\n"
                f"• Trades: {c_total}\n"
                f"• Ganados: {w_total} ✅\n"
                f"• Perdidos: {l_total} ❌\n"
                f"• Win Rate: *{wr_total:.1f}%*\n"
                f"• Avg Win: +{h_avg_win:.1f}% (+${h_win_usd:.2f})\n"
                f"• Avg Loss: {h_avg_loss:.1f}% (${h_loss_usd:.2f})\n"
            )

            if c_today > 0:
                # Top Monedas
                counts = Counter([t["symbol"] for t in shadows])
                msg += "\n🏆 *Top Activos Explorados (Hoy):*\n"
                for sym, c in counts.most_common(5):
                    # Calcular WR por moneda
                    sym_trades = [t for t in shadows if t["symbol"] == sym]
                    sym_wins = sum(1 for t in sym_trades if t["pnl_percent"] > 0)
                    sym_wr = sym_wins / len(sym_trades) * 100
                    msg += f"• {sym}: {c} trades ({sym_wr:.0f}% WR)\n"
            else:
                msg += "\n⚠️ No se han registrado operaciones Shadow hoy."
            send_telegram_msg(msg)

        elif text in ["/train", "/force_train"]:
            send_telegram_msg("🧠 *FORZANDO ENTRENAMIENTO...*")
            try:
                last_mtime = 0
                if os.path.exists("ghost_brain.pkl"):
                    last_mtime = os.path.getmtime("ghost_brain.pkl")
                from ghost_trainer import train_ghost_brain

                train_ghost_brain()
                if (
                    os.path.exists("ghost_brain.pkl")
                    and os.path.getmtime("ghost_brain.pkl") > last_mtime
                ):
                    with open("ghost_brain.pkl", "rb") as f:
                        self.ghost_model = pickle.load(f)
                    self.brain.set_metadata("last_ghost_train", datetime.now())
                    self.mandatory_train_pending = False
                    send_telegram_msg("✅ *ÉXITO:* Nuevo cerebro cargado y operativo.")
                else:
                    send_telegram_msg(
                        "⚠️ Entrenamiento completado sin cambios (¿Datos insuficientes < 100?)."
                    )
            except Exception as e:
                send_telegram_msg(f"❌ Error crítico: {e}")

        elif text == "/evolution":
            send_telegram_msg("🧬 Ejecutando AI Coach para optimizar filtros...")
            try:
                os.system(f"{sys.executable} ai_coach.py")
                send_telegram_msg("🚀 Evolución completada. Parámetros ajustados.")
            except Exception as e:
                send_telegram_msg(f"❌ Error evolución: {e}")

        elif text == "/genetic":
            send_telegram_msg(
                "🧬 *INICIANDO MOTOR GENÉTICO...*\nAnalizando supervivencia de especies y mutando parámetros SL/TP..."
            )
            try:
                import subprocess

                # Ejecutar en segundo plano para no congelar el bot
                subprocess.Popen([sys.executable, "tools/genetic_engine.py"])
            except Exception as e:
                send_telegram_msg(f"❌ Error iniciando motor genético: {e}")

        elif text == "/force_shadow":
            send_telegram_msg(
                "⛔ *Comando deshabilitado.*\nEl bot opera en cuarentena controlada y no permite alternar modo por Telegram."
            )

        elif text.startswith("/dna"):
            parts = text.split()
            symbol = parts[1].upper() if len(parts) > 1 else "BTC/USDT"
            genes = self.brain.get_genetic_params(symbol)
            if genes:
                msg = (
                    f"🧬 *DNA STATUS: {symbol}*\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"• SL Multiplier: {genes['sl_mult']:.2f}\n"
                    f"• TP Multiplier: {genes['tp_mult']:.2f}\n"
                    f"• Generation: {genes.get('generation', 1)}"
                )
            else:
                msg = f"⚠️ Sin datos genéticos para {symbol}"
            send_telegram_msg(msg)

        elif text.startswith("/trade_detail"):
            parts = text.split()
            symbol = parts[1].upper() if len(parts) > 1 else None

            if not symbol:
                send_telegram_msg(
                    "⚠️ Uso: /trade_detail [SÍMBOLO]\nEj: /trade_detail BTC/USDT"
                )
                return

            found = None
            for item in self.scanner_history:
                if symbol in item.get("symbol", ""):
                    found = item
                    break

            if not found:
                msg = f"⚠️ No hay datos recientes para {symbol}"
                send_telegram_msg(msg)
                return

            rsi = found.get("rsi_val", 0)
            adx = found.get("adx_val", 0)
            z_score = found.get("z_score", 0.0)
            ia_prob = found.get("ia_prob", "0%")
            signal = found.get("signal", "WAIT")
            result = found.get("result", "N/A")
            ob = found.get("ob", "⚪")
            trend = found.get("trend_val", "N/A")
            funding = found.get("funding_rate", 0.0)
            votos = found.get("votos", {})

            msg = (
                f"🔍 *ANÁLISIS DETALLADO: {found['symbol']}*\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"📊 *SEÑAL:* {signal} | Prob: *{ia_prob}*\n"
                f"📈 *RESULTADO:* {result}\n\n"
                f"🛠️ *INDICADORES TÉCNICOS:*\n"
                f"• RSI: {rsi} | ADX: {adx}\n"
                f"• Z-Score: {z_score:.2f}\n"
                f"• Trend: {trend}\n"
                f"• Funding: {funding * 100:.3f}%\n"
                f"• OB Status: {ob}\n\n"
            )

            if votos:
                msg += "🗳️ *VOTOS DE AGENTES:*\n"
                agent_names = {
                    "T": "🛠️ Tec",
                    "V": "👁️ Vis",
                    "J": "⚖️ Judge",
                    "G": "👻 Ghost",
                    "C": "🔗 Corr",
                    "L": "💧 Liq",
                    "F": "😫 Fat",
                    "S": "📢 Sent",
                    "O": "⛓️ OnC",
                    "R": "🧠 Reg",
                }
                for agent_id, vote in sorted(
                    votos.items(), key=lambda x: x[1], reverse=True
                ):
                    name = agent_names.get(agent_id, agent_id)
                    bar = "█" * int(vote / 10) + "░" * (10 - int(vote / 10))
                    msg += f"{name}: {bar} {vote:.0f}%\n"

            msg += "\n💡 _Comando: /thinking para ver vetos recientes_"
            send_telegram_msg(msg)

        elif text.startswith("/trade "):
            parts = text.split()
            try:
                trade_id = int(parts[1])
            except (ValueError, IndexError):
                send_telegram_msg("⚠️ Uso: /trade [ID]\nEj: /trade 10258")
                return

            trade = self.brain.get_trade_by_id(trade_id)

            if not trade:
                send_telegram_msg(f"❌ No se encontró el trade #{trade_id}")
                return

            symbol = trade.get("symbol", "N/A")
            side = trade.get("side", "N/A")
            entry = trade.get("entry_price", 0)
            exit_p = trade.get("exit_price", 0)
            pnl = trade.get("pnl", 0)
            pnl_pct = trade.get("pnl_percent", 0)
            reason = trade.get("reason", "N/A")
            timestamp = trade.get("timestamp", "N/A")
            is_shadow = trade.get("is_shadow", 0)
            fees = trade.get("fees", 0)
            rsi = trade.get("rsi", 0)
            adx = trade.get("adx", 0)
            funding = trade.get("funding_rate", 0)
            vol_rel = trade.get("vol_rel", 0)
            entry_ob = trade.get("entry_ob", "⚪")

            mode = "🧪 SHADOW" if is_shadow else "🔥 REAL"

            msg = (
                f"📋 *TRADE #{trade_id}*\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"🔹 Símbolo: {symbol}\n"
                f"🔹 Lado: {side} | Modo: {mode}\n"
                f"🔹 Entrada: {entry:.4f} | Salida: {exit_p:.4f}\n"
                f"🔹 PnL: {pnl:+.4f} USD ({pnl_pct:+.2f}%)\n"
                f"🔹 Fees: {fees:.4f} USD\n"
                f"🔹 Razón: {reason}\n"
                f"🔹 Hora: {timestamp}\n\n"
            )

            msg += (
                f"📊 *CONTEXTO DEL MERCADO:*\n"
                f"• RSI: {rsi:.1f} | ADX: {adx:.1f}\n"
                f"• Funding: {funding * 100:.3f}%\n"
                f"• Vol Rel: {vol_rel:.2f}\n"
                f"• Entry OB: {entry_ob}\n\n"
            )

            market_snap = trade.get("market_snapshot")
            if market_snap:
                try:
                    snap = json.loads(market_snap)
                    trend = snap.get("trend", "N/A")
                    z_score = snap.get("z_score", 0)
                    bb_pos = snap.get("bb_pos", 0.5)
                    dist_ema = snap.get("dist_ema", 0)
                    btc_delta = snap.get("btc_delta_tf", 0)

                    msg += (
                        f"🧠 *ANÁLISIS IA:*\n"
                        f"• Tendencia: {trend}\n"
                        f"• Z-Score: {z_score:.2f}\n"
                        f"• BB Position: {bb_pos:.2f}\n"
                        f"• Dist EMA: {dist_ema:.2f}\n"
                        f"• BTC Delta: {btc_delta:.2f}%\n\n"
                    )
                except:
                    pass

            similar = self.brain.get_similar_trades(rsi, adx, limit=3)
            if similar:
                msg += "🔗 *TRADES SIMILARES (RAG):*\n"
                for s in similar:
                    sim_pnl = s.get("pnl_percent", 0)
                    sim_sym = s.get("symbol", "N/A")
                    sim_id = s.get("id", 0)
                    msg += f"• #{sim_id} {sim_sym}: {sim_pnl:+.2f}%\n"

            send_telegram_msg(msg)

        elif text.startswith("/explain"):
            parts = text.split()
            if len(parts) < 2:
                send_telegram_msg("⚠️ Uso: /explain [SYMBOL] (ej: /explain BTC/USDT)")
            else:
                sym = parts[1].upper()
                send_telegram_msg(f"🧠 *ANALIZANDO {sym}...*")
                try:
                    # Obtener datos frescos
                    df_main = self.data_service.fetch_and_update_data(sym, "1h")
                    df_4h = self.data_service.fetch_and_update_data(sym, "4h")

                    if df_main is None or df_main.empty:
                        send_telegram_msg("❌ No hay datos suficientes para explicar.")
                    else:
                        # Ejecutar análisis completo
                        res = Strategy.analyze(
                            df_main,
                            df_main,
                            self.brain,
                            symbol=sym,
                            ghost_model=self.ghost_model,
                            scaler=self.scaler,
                            btc_delta_tf=getattr(
                                self,
                                "market_btc_change_tf",
                                0.0,
                            ),
                            df_4h=df_4h,
                        )
                        _, _, price, prob, ind, votos = res

                        msg = (
                            f"🧐 *EXPLICACIÓN IA: {sym}*\n"
                            f"━━━━━━━━━━━━━━━━━━━━\n"
                            f"🎯 *Score Final:* {prob:.1f}/100\n"
                            f"👻 *Ghost Pro:* {votos.get('G', 0):.1f}% (Predicción ML)\n"
                            f"⚖️ *Juez:* {votos.get('J', 0):.1f}% (Histórico)\n"
                            f"🛠️ *Técnico:* {votos.get('T', 0):.1f}% (RSI/ADX)\n\n"
                            f"📊 *Factores Clave:*\n"
                            f"• RSI: {ind['rsi']['val']:.1f}\n"
                            f"• ADX: {ind['adx']['val']:.1f}\n"
                            f"• Z-Score: {ind.get('z_score', 0):.2f}"
                        )
                        send_telegram_msg(msg)
                except Exception as e:
                    send_telegram_msg(f"❌ Error explicando: {e}")

        elif text == "/reset":
            msg = self.handle_reset_pnl()
            send_telegram_msg(msg)

        elif text == "/archive":
            backup_file = self.brain.rotate_history()
            send_telegram_msg(f"📦 DB Optimizada. Historial movido a: {backup_file}")

        elif text == "/clean":
            send_telegram_msg(
                "⛔ *Comando deshabilitado.*\nSe bloquea limpieza destructiva durante operación para proteger historial y continuidad."
            )

        elif text == "/signals":
            # Mostrar distribución de señales del último ciclo
            if hasattr(self, "last_signal_stats"):
                stats = self.last_signal_stats
                total = stats["BUY"] + stats["SELL"] + stats["NEUTRAL"]
                if total > 0:
                    buy_pct = (stats["BUY"] / total) * 100
                    sell_pct = (stats["SELL"] / total) * 100
                    neutral_pct = (stats["NEUTRAL"] / total) * 100
                    msg = (
                        f"📊 *DISTRIBUCIÓN DE SEÑALES*\n\n"
                        f"*Señales Técnicas:*\n"
                        f"• BUY: {stats['BUY']} ({buy_pct:.1f}%)\n"
                        f"• SELL: {stats['SELL']} ({sell_pct:.1f}%)\n"
                        f"• NEUTRAL: {stats['NEUTRAL']} ({neutral_pct:.1f}%)\n\n"
                        f"*Veredictos:*\n"
                        f"• ✅ REAL: {stats['REAL']}\n"
                        f"• 🧪 SHADOW: {stats['SHADOW']}\n"
                        f"• ❌ VETO: {stats['VETO']}\n\n"
                        f"Total pares escaneados: {total}"
                    )
                else:
                    msg = "⚠️ No hay datos del último ciclo de escaneo."
            else:
                msg = "⚠️ Aún no se ha completado un ciclo de escaneo."
            send_telegram_msg(msg)

        elif text == "/shadow_stats":
            # Estadísticas de trades shadow
            try:
                _conn = self.brain._get_conn()
                c = _conn.cursor()
                c.execute("SELECT COUNT(*) FROM trades WHERE is_shadow = 1")
                total_shadow = c.fetchone()[0]
                c.execute(
                    "SELECT COUNT(*) FROM trades WHERE is_shadow = 1 AND pnl_percent > 0"
                )
                wins = c.fetchone()[0]
                c.execute(
                    "SELECT AVG(pnl_percent) FROM trades WHERE is_shadow = 1 AND pnl_percent != -99.0"
                )
                avg_pnl = c.fetchone()[0] or 0
                _conn.close()
            except Exception as e:
                send_telegram_msg(f"❌ Error obteniendo stats shadow: {e}")
                total_shadow, wins, avg_pnl = 0, 0, 0.0

            wr = (wins / total_shadow * 100) if total_shadow > 0 else 0
            msg = (
                f"🧪 *ESTADÍSTICAS SHADOW*\n\n"
                f"• Total Trades: {total_shadow}\n"
                f"• Win Rate: {wr:.1f}%\n"
                f"• PnL Promedio: {avg_pnl:.2f}%\n\n"
                f"_Los shadow trades son para aprendizaje y no arriesgan capital real._"
            )
            send_telegram_msg(msg)

        elif text == "/unquarantine":
            # Resetear bloqueos activos de reentrada
            try:
                with self.lock:
                    cooldown_count = len(self.cooldown_pairs)
                    self.cooldown_pairs.clear()

                blacklist_count = 0
                if hasattr(self, "risk_engine") and self.risk_engine is not None:
                    if hasattr(self.risk_engine, "temp_blacklist"):
                        blacklist_count = len(self.risk_engine.temp_blacklist)
                        self.risk_engine.temp_blacklist.clear()
                    if hasattr(self.risk_engine, "symbol_streaks"):
                        self.risk_engine.symbol_streaks.clear()

                send_telegram_msg(
                    f"🔓 *COOLDOWNS RESETEADOS*\n\n"
                    f"• Cooldowns de pares liberados: {cooldown_count}\n"
                    f"• Blacklist anti-revenge liberada: {blacklist_count}\n"
                    f"• Estado: listo para re-evaluación inmediata"
                )
            except Exception as e:
                send_telegram_msg(f"❌ Error reseteando cooldowns: {e}")

        elif text == "/thresholds":
            # Mostrar umbrales actuales de IA
            msg = (
                f"🎯 *UMBRALES DE IA (1H)*\n\n"
                f"*Shadow Trades:*\n"
                f"• Rango/Neutral: {Config.SHADOW_MIN_PROBABILITY_RANGE}%\n"
                f"• Tendencia: {Config.SHADOW_MIN_PROBABILITY_TREND}%\n\n"
                f"*Real Trades:*\n"
                f"• Umbral Mínimo: {Config.REAL_CONFIDENCE_MIN * 100}%\n\n"
                f"*Sentimiento Actual:*\n"
                f"• {self.current_sentiment[0]}\n\n"
                f"_Umbrales más bajos = Más exploración_"
            )
            send_telegram_msg(msg)

        elif text == "/test":
            send_telegram_msg(
                "🔔 *PRUEBA DE CONEXIÓN*\nSi estás leyendo esto, las notificaciones de Sniper AI funcionan correctamente."
            )

    def _telegram_listener(self):
        """Escucha comandos como /report o /train desde Telegram."""
        last_update_id = 0
        while self.is_running:
            try:
                if not Config.TELEGRAM_TOKEN:
                    time.sleep(10)
                    continue

                url = f"https://api.telegram.org/bot{Config.TELEGRAM_TOKEN}/getUpdates?offset={last_update_id}&timeout=30"
                response = requests.get(
                    url, timeout=35
                ).json()  # [FIX] Timeout añadido para evitar congelamiento

                for update in response.get("result", []):
                    last_update_id = update["update_id"] + 1
                    text = update.get("message", {}).get("text", "").strip()

                    # Lógica centralizada
                    self.handle_command(text)

            except Exception as e:
                time.sleep(10)
                self.log(f"Telegram Error: {e}")
            time.sleep(1)

    def _terminal_command_listener(self):
        """Escucha comandos directamente desde la terminal."""
        while self.is_running:
            try:
                # Usar input() en un hilo separado puede ser ruidoso con Rich,
                # pero es lo que el usuario pidió.
                cmd = input().strip().lower()
                if cmd == "audit":
                    self.log("📋 Cargando reporte de auditoría en terminal...")
                    with self.db_lock:
                        trades = self.brain.get_last_n_trades(100)
                    from reporter import generate_terminal_audit_table

                    table = generate_terminal_audit_table(trades)

                    # Usar la consola de Rich para imprimir la tabla limpiamente
                    from rich.console import Console

                    console = Console()
                    console.print("\n")
                    console.print(table)
                    console.print("\n[dim]Presione ENTER para continuar...[/]")
                elif cmd == "help":
                    print("\nComandos de consola: audit, help, exit\n")
                elif cmd == "exit":
                    self.is_running = False
                    break
            except EOFError:
                break
            except Exception as e:
                time.sleep(1)

    def _perform_post_mortem(self):
        """Analiza trades cerrados hace 15m para etiquetar falsos positivos."""
        try:
            with self.db_lock:
                pending = self.brain.get_trades_pending_post_mortem()
            now = datetime.now()
            for t in pending:
                try:
                    close_time = datetime.fromisoformat(t["timestamp"])
                    if (now - close_time).total_seconds() < 900:
                        continue  # Esperar 15 min

                    ticker = self.execution.exchange.fetch_ticker(t["symbol"])
                    curr_price = float(ticker["last"])

                    verdict = "NEUTRAL"
                    if t["pnl_percent"] < 0:
                        # Si perdimos y el precio siguió en contra -> La señal fue un Falso Positivo
                        if t["side"] == "BUY" and curr_price < t["exit_price"]:
                            verdict = "FALSE_POSITIVE"
                        elif t["side"] == "SELL" and curr_price > t["exit_price"]:
                            verdict = "FALSE_POSITIVE"
                        else:
                            verdict = "BAD_TIMING"  # El precio se recuperó, fue mala suerte/stop muy corto

                    with self.db_lock:
                        self.brain.update_post_mortem(
                            t["id"], {"price_15m": curr_price, "verdict": verdict}
                        )
                    if verdict == "FALSE_POSITIVE":
                        self.log(
                            f"💀 Post-Mortem {t['symbol']}: Confirmado Falso Positivo. Aprendiendo..."
                        )
                except Exception:
                    continue
        except Exception as e:
            self.log(f"⚠️ Error Post-Mortem: {e}")

    def _calculate_quant_consensus(
        self, visual_prob: float, context: Dict
    ) -> Tuple[float, str]:
        """
        PROTOCOLO CIRUGÍA LÁSER: Lógica del 'Senior Quant Strategist'.
        Evalúa la cohesión entre la visión de la IA (Visual) y los datos técnicos (RSI/ADX).
        """
        if not context:
            return visual_prob, "Sin Contexto"

        # 1. Extracción de Datos
        rsi = context.get("rsi", 50)
        adx = context.get("adx", 20)
        trend = context.get("trend", "NEUTRAL")
        vol = context.get("volume", 0)
        vol_ma = context.get("volume_ma", vol)
        vol_rel = vol / vol_ma if vol_ma > 0 else 0

        # 2. Score Base (Confianza Visual del Modelo ML - 40% Peso Estructural)
        score = visual_prob * 100
        penalties = []

        # --- NUEVO: FACTOR TÉCNICO (Multiplicativo) ---
        tech_factor = 1.0

        # 3. Validación Técnica (Reglas de Cohesión - 60% Peso Técnico)

        # A. Análisis de Discrepancia RSI (Momento)
        # Excepción: Si ADX > 40 (Tendencia Parabólica), ignoramos sobrecompra/venta
        if trend == "UP" and rsi > 70 and adx < 40:
            tech_factor = 0.5
            penalties.append(("RSI Sobrecompra (Factor 0.5x)", 0))
        elif trend == "DOWN" and rsi < 30 and adx < 40:
            tech_factor = 0.5
            penalties.append(("RSI Sobreventa (Factor 0.5x)", 0))

        # B. Análisis de Fuerza ADX
        if adx < 20:
            penalties.append(("ADX Débil (<20)", 20))

        # C. Coherencia de Volumen
        if vol_rel < 0.8:
            penalties.append(("Volumen Bajo", 10))

        # 4. Cálculo de Score Real y Razón
        total_penalty = sum(p[1] for p in penalties)

        # Aplicamos penalizaciones aditivas primero, luego el factor multiplicativo
        final_score = max(0.0, score - total_penalty) * tech_factor

        if not penalties:
            reason = "✅ Consenso Técnico OK"
        else:
            details = ", ".join([f"{p[0]}" for p in penalties])
            reason = f"⚠️ Ajuste -{total_penalty}%: {details}"

        return final_score / 100.0, reason

    def _prioritize_targets(self):
        """[ESCANEO DINÁMICO] Reordena la lista de escaneo por volatilidad (Change 24h)."""
        try:
            # [MEJORA] Ejecutar cada 2 minutos (120s) para ser más ágil detectando movimientos
            if time.time() - getattr(self, "_last_sort_time", 0) < 120:
                return

            self.log("🌪️ ESCANEO DINÁMICO: Reordenando pares por volatilidad...")
            tickers = self.execution.exchange.fetch_tickers(self.pairs_to_scan)

            def get_vol(s):
                # Búsqueda robusta del ticker
                t = (
                    tickers.get(s)
                    or tickers.get(s.replace("/", ""))
                    or tickers.get(s.split(":")[0])
                )
                return abs(float(t.get("percentage", 0) or 0)) if t else 0.0

            self.pairs_to_scan.sort(key=get_vol, reverse=True)
            self._last_sort_time = time.time()

            # Log de confirmación
            top_3 = [f"{s} ({get_vol(s):.1f}%)" for s in self.pairs_to_scan[:3]]
            self.log(f"🔥 Top Volatilidad: {', '.join(top_3)}")
        except Exception as e:
            self.log(f"⚠️ Error en Escaneo Dinámico: {e}")

    def _get_active_market_snapshot(self):
        """
        [DINÁMICO] 50 pares — fetch_tickers batch — Lista viva.

        Lógica:
          - Lista persistente (self._dynamic_pair_list) de máx 50 pares
          - fetch_tickers() batch cada 5 min (peso 40) para refresh mercado
          - Verificación de volumen ≥10M y spread ≤0.5% cada ciclo
          - Si un par baja de 10M → se saca → se busca reemplazo
          - Rotación de candidatos para cubrir todo el mercado

        Peso API:
          - Ciclo normal (cache hit): ~1 (solo BookTicker)
          - Con refresh (cada 5 min): +40
          - Promedio: ~9/min = 12,960/día (5.4% del límite diario ✅)

        Returns:
            List[Dict]: Pares activos ordenados por RVOL desc.
        """
        try:
            # Inicializar lista dinámica persistente
            if not hasattr(self, "_dynamic_pair_list"):
                self._dynamic_pair_list = []
            if not hasattr(self, "_market_scan_offset"):
                self._market_scan_offset = 0
            if not hasattr(self, "_market_cache"):
                self._market_cache = {}
            if not hasattr(self, "_market_cache_ts"):
                self._market_cache_ts = 0
            if not hasattr(self, "_vol_ema"):
                self._vol_ema = {}

            MAX_PAIRS = 50  # 50 pares — máximo cobertura del mercado
            MIN_VOL = Config.TRIAGE_MIN_VOL_24H  # 10M
            MAX_SPREAD = Config.TRIAGE_SPREAD_MAX  # 0.5%
            MARKET_REFRESH = 300  # refresh mercado cada 5 min (suficiente para 1h)

            # --- CAPA 0: BookTicker para spreads reales (peso ~1) ---
            bid_ask_map = {}
            try:
                book_tickers = self.execution.exchange.fapiPublicGetTickerBookTicker()
                self._track_api_weight(1)
                for bt in book_tickers:
                    raw_sym = bt.get("symbol", "")
                    bid_price = float(bt.get("bidPrice", 0) or 0)
                    ask_price = float(bt.get("askPrice", 0) or 0)
                    if raw_sym and bid_price > 0 and ask_price > 0:
                        bid_ask_map[raw_sym] = {"bid": bid_price, "ask": ask_price}
            except Exception as e:
                self.log(f"⚠️ [TRIAJE] BookTicker falló: {e}")

            # --- CAPA 1: Refresh del mercado cada 5 min (peso 40) ---
            now = time.time()
            if now - self._market_cache_ts > MARKET_REFRESH or not self._market_cache:
                self.log("📡 [TRIAJE] Refresh mercado completo (cada 5 min)...")
                try:
                    if not self.execution.exchange.markets:
                        self.execution.exchange.load_markets()

                    raw_tickers = self.execution.exchange.fetch_tickers(
                        params={"type": "future"}
                    )
                    self._track_api_weight(40)

                    # Construir pool de candidatos
                    all_candidates = []
                    for symbol, ticker in raw_tickers.items():
                        if not (
                            symbol.endswith("/USDT") or symbol.endswith("/USDT:USDT")
                        ):
                            continue
                        if any(
                            x in symbol
                            for x in ["DOWN", "UP", "BEAR", "BULL", "_", "BUSD", "USDC"]
                        ):
                            continue
                        clean_sym = Config.sanitize_symbol(symbol)
                        if clean_sym and clean_sym.endswith("/USDT"):
                            all_candidates.append((clean_sym, ticker))

                    self._market_cache = {
                        "tickers": raw_tickers,
                        "candidates": all_candidates,
                    }
                    self._market_cache_ts = now
                    self.log(
                        f"✅ [TRIAJE] {len(all_candidates)} candidatos cacheados "
                        f"(peso=40, próximo refresh en {MARKET_REFRESH}s)"
                    )
                except Exception as e_tickers:
                    self.log(f"⚠️ [TRIAJE] fetch_tickers falló: {e_tickers}")
                    self._market_cache = {"tickers": {}, "candidates": []}
            else:
                self.log(
                    f"📦 [TRIAJE] Usando cache de mercado ({int(now - self._market_cache_ts)}s atrás)"
                )

            raw_tickers = self._market_cache.get("tickers", {})
            all_candidates = self._market_cache.get("candidates", [])

            # --- PASO 1: Verificar pares actuales con datos del cache ---
            kept = []
            for sym in list(self._dynamic_pair_list):
                ticker = (
                    raw_tickers.get(sym)
                    or raw_tickers.get(sym + ":USDT")
                    or raw_tickers.get(sym.replace("/", ""))
                )
                if not ticker:
                    self.log(f"🔻 [DINÁMICO] {sym} sin ticker → removido")
                    continue

                vol_24h = float(ticker.get("quoteVolume", 0) or 0)
                last = float(ticker.get("last", 0) or 0)

                if vol_24h < MIN_VOL:
                    base_vol = float(ticker.get("baseVolume", 0) or 0)
                    vol_24h = base_vol * last
                    if vol_24h < MIN_VOL:
                        self.log(
                            f"🔻 [DINÁMICO] {sym} vol=${vol_24h:,.0f} < ${MIN_VOL:,.0f} → removido"
                        )
                        continue

                # Spread check
                raw_key = sym.replace("/", "").replace(":USDT", "")
                book_data = bid_ask_map.get(raw_key)
                if book_data:
                    spread = (book_data["ask"] - book_data["bid"]) / book_data["ask"]
                else:
                    ask = float(ticker.get("ask", 0) or 0)
                    bid = float(ticker.get("bid", 0) or 0)
                    spread = (ask - bid) / last if (last > 0 and ask > bid) else None

                if spread is None or spread > MAX_SPREAD:
                    self.log(f"🔻 [DINÁMICO] {sym} spread={spread} → removido")
                    continue

                kept.append(sym)

            removed_count = len(self._dynamic_pair_list) - len(kept)
            self._dynamic_pair_list = kept
            slots_free = MAX_PAIRS - len(self._dynamic_pair_list)

            # --- PASO 2: Llenar slots vacíos desde cache ---
            if slots_free > 0:
                self.log(
                    f"🔍 [DINÁMICO] {slots_free} slot(s) libre(s) — buscando reemplazos..."
                )

                existing_set = set(self._dynamic_pair_list)
                offset = self._market_scan_offset % max(len(all_candidates), 1)
                scanned = 0
                found = 0
                # [FIX] Si faltan muchos slots, escanear más agresivo para acercar 50/50.
                # Cap al total de candidatos para no iterar infinito.
                batch_size = min(
                    len(all_candidates),
                    max(200, slots_free * 80),
                )

                for i in range(batch_size):
                    idx = (offset + i) % len(all_candidates)
                    if idx >= len(all_candidates):
                        break

                    sym, ticker = all_candidates[idx]
                    scanned += 1

                    if sym in existing_set:
                        continue

                    vol_24h = float(ticker.get("quoteVolume", 0) or 0)
                    last = float(ticker.get("last", 0) or 0)

                    if vol_24h < MIN_VOL:
                        base_vol = float(ticker.get("baseVolume", 0) or 0)
                        vol_24h = base_vol * last
                        if vol_24h < MIN_VOL:
                            continue

                    # Spread check
                    raw_key = sym.replace("/", "").replace(":USDT", "")
                    book_data = bid_ask_map.get(raw_key)
                    if book_data:
                        spread = (book_data["ask"] - book_data["bid"]) / book_data[
                            "ask"
                        ]
                    else:
                        ask = float(ticker.get("ask", 0) or 0)
                        bid = float(ticker.get("bid", 0) or 0)
                        spread = (
                            (ask - bid) / last if (last > 0 and ask > bid) else None
                        )

                    if spread is None or spread > MAX_SPREAD:
                        continue

                    # RVOL
                    ema_vol = self._vol_ema.get(sym, vol_24h)
                    rvol = vol_24h / ema_vol if ema_vol > 0 else 1.0
                    alpha = Config.TRIAGE_RVOL_EMA_ALPHA
                    self._vol_ema[sym] = (alpha * vol_24h) + ((1 - alpha) * ema_vol)

                    self._dynamic_pair_list.append(sym)
                    existing_set.add(sym)
                    found += 1
                    self.log(
                        f"🔼 [DINÁMICO] {sym} agregado (vol=${vol_24h:,.0f}, rvol={rvol:.1f})"
                    )

                    if len(self._dynamic_pair_list) >= MAX_PAIRS:
                        break

                self._market_scan_offset = (offset + scanned) % max(
                    len(all_candidates), 1
                )
                self.log(
                    f"🔄 [DINÁMICO] Escaneados {scanned} candidatos, encontrados {found} reemplazos"
                )

            # --- PASO 3: Construir ranked con RVOL ---
            ranked = []
            for sym in self._dynamic_pair_list:
                ticker = (
                    raw_tickers.get(sym)
                    or raw_tickers.get(sym + ":USDT")
                    or raw_tickers.get(sym.replace("/", ""))
                )
                if not ticker:
                    continue

                vol_24h = float(ticker.get("quoteVolume", 0) or 0)
                ema_vol = self._vol_ema.get(sym, vol_24h)
                rvol = vol_24h / ema_vol if ema_vol > 0 else 1.0

                ranked.append(
                    {
                        "symbol": sym,
                        "symbol_raw": sym,
                        "rvol": rvol,
                        "vol_24h": vol_24h,
                        "status": "ACTIVE",
                        "ticker": ticker,
                    }
                )

            ranked.sort(key=lambda x: x["rvol"], reverse=True)
            top_symbols = [f"{item['symbol']} ({item['rvol']:.1f})" for item in ranked]

            self.log(
                f"🎯 TRIAJE DINÁMICO: {len(ranked)}/{MAX_PAIRS} pares activos | "
                f"{removed_count} removidos este ciclo | "
                f"Top: {', '.join(top_symbols)}"
            )

            return ranked

        except Exception as e:
            import traceback

            tb = traceback.format_exc()
            self.log(f"⚠️ Error en _get_active_market_snapshot: {e}")
            self.log(f"TRACEBACK: {tb}")
            return []

    def _perform_triage(self):
        """
        [V115-PRO] Alias público de _get_active_market_snapshot().
        Ejecuta el Scouting Masivo + Ranking RVOL y devuelve los pares activos ordenados.
        Úsalo cuando quieras invocar el triaje manualmente desde comandos o tests.
        """
        return self._get_active_market_snapshot()

    def _track_api_weight(self, weight):
        """Incrementa el contador de peso API para monitoreo."""
        if not hasattr(self, "_api_weight_counter"):
            self._api_weight_counter = 0
        self._api_weight_counter += weight

    def _get_cached_funding_rate(self, symbol):
        """
        Fetch funding rate con cache de 5 min.
        El funding rate cambia cada 8h, no tiene sentido fetchearlo cada ciclo.
        """
        now = time.time()
        cached = self._funding_rate_cache.get(symbol)
        if cached:
            rate, ts = cached
            if now - ts < self._funding_cache_ttl:
                return rate

        try:
            fr = self.execution.exchange.fetch_funding_rate(symbol)
            rate = float(fr.get("fundingRate", 0))
            self._funding_rate_cache[symbol] = (rate, now)
            return rate
        except Exception:
            return 0.0

    def _get_cached_btc_data(self):
        """
        Fetch BTC data una vez por ciclo. Evita 3 fetches duplicados.
        """
        now = time.time()
        if self._btc_data_cache is not None and now - self._btc_data_cache_ts < 60:
            return self._btc_data_cache

        try:
            btc_data = self.data_service.fetch_and_update_data("BTC/USDT", "1h")
            self._btc_data_cache = btc_data
            self._btc_data_cache_ts = now
            return btc_data
        except Exception:
            return None

    def _get_shock_distance_pct(self, df, side):
        """Wrapper del modulo SHOCK compartido (core/strategy/shocks.py)."""
        try:
            return next_shock_distance_pct(
                df=df,
                side=side,
                pivot_window=int(getattr(Config, "SHOCK_PIVOT_WINDOW", 3)),
                lookback_bars=int(getattr(Config, "SHOCK_LOOKBACK_BARS", 240)),
            )
        except Exception:
            return None, None

    def _update_scanner_status(self, symbol, status, qoe="--"):
        """Helper para actualizar estado en radar desde hilos."""
        self.update_radar(
            symbol,
            {"signal": "WAIT", "mode": "NONE"},
            0.0,
            "⚪",
            status,
            {"tier": "IRON"},
            response_ms=-1,
        )

    def _fetch_pair_data(self, symbol):
        """Helper para fetch paralelo [V115-PRO] con reintentos agresivos."""
        _par_start_time = time.time()
        data = (None, None)
        elapsed = -1

        # [V115-PRO] Estrategia de reintentos
        max_retries = 1

        for attempt in range(max_retries + 1):
            try:
                # Intentar fetch secuencial
                df_main = self.data_service.fetch_and_update_data(symbol, "1h")

                # Verificación rápida
                min_candles = 50
                if df_main is None or (
                    hasattr(df_main, "__len__") and len(df_main) < min_candles
                ):
                    # Si falla timeframe principal, no vale la pena seguir
                    if attempt < max_retries:
                        time.sleep(0.5 * (attempt + 1))
                        continue
                    else:
                        # Fallo final
                        self._update_scanner_status(symbol, "❌ NO_DATA", qoe="--")
                        return (
                            symbol,
                            (None, None),
                            int((time.time() - _par_start_time) * 1000),
                        )

                df_4h = self.data_service.fetch_and_update_data(symbol, "4h")

                data = (df_main, df_4h)
                break  # Éxito

            except Exception as e:
                if attempt < max_retries:
                    time.sleep(0.5 * (attempt + 1))
                else:
                    self.log(f"⚠️ Error fatal en hilo {symbol}: {e}")
                    self._update_scanner_status(symbol, "❌ ERROR", qoe="--")
                    pass

        # Garantizamos que se registre la latencia aunque falle
        elapsed = int((time.time() - _par_start_time) * 1000)
        return symbol, data, elapsed

    def _main_logic(self):
        last_report_time = time.time()
        last_heavy = time.time()
        last_coach_time = time.time()
        last_log_check = time.time()

        self.init_complete.wait()

        # [V115-PRO] SYNC de Websockets (Asegurar que inicia ANTES del primer pase de Triaje)
        if (
            hasattr(self, "ws_manager")
            and getattr(self.ws_manager, "is_running", False) is False
        ):
            self.ws_manager.start_background()

        # AI COACH: Ejecutar al inicio para aprender de datos históricos
        # (Desactivado en main.py para limpieza. Ejecutar tools/ai_coach.py manualmente si se requiere)
        pass

        threading.Thread(target=self._guardian_loop, daemon=True).start()

        while self.is_running:
            try:
                # [FASE 3: BAILOUT] Movido a _guardian_loop para polling de alta frecuencia (100ms)
                # self.monitor_open_trades()

                # [CORRECCIÓN ARQUITECTÓNICA] CRASH PREDICTOR
                if (
                    Config.CRASH_DETECTION_ENABLED
                    and hasattr(self, "market_btc_price")
                    and self.market_btc_price > 0
                ):
                    try:
                        # Usamos la instancia persistente en memoria
                        btc_price = self.market_btc_price
                        btc_delta_tf = getattr(
                            self,
                            "market_btc_change_tf",
                            0,
                        )

                        # NOTA: En el futuro debes pasar el DataFrame real (df) de BTC aquí para que evalúe ATR y Z-Score.
                        # Por ahora, evaluamos con la caída de precio y evitamos el crash interno de Pandas.
                        crash_result = self.crash_predictor.analyze_crash_risk(
                            df=None,  # Requiere parche futuro en crash_predictor.py para manejar df=None
                            symbol="BTC",
                            funding_rate=0,
                            side="BUY",
                            order_book=None,
                            btc_delta_tf=btc_delta_tf,
                            btc_price=btc_price,
                            btc_ema_200=0,
                        )

                        if (
                            crash_result
                            and crash_result.get("recommended_action") == "CLOSE_ALL"
                        ):
                            self.log(
                                f"🚨 CRASH INMINENTE (Prob: {crash_result.get('crash_probability', 0):.0f}%) - ¡EJECUTANDO VETO DE EMERGENCIA!"
                            )
                            closed_count = self._close_all_positions_emergency()
                            self.log(
                                f"🛡️ PROTOCOLO COMPLETADO: {closed_count} posiciones liquidadas a mercado."
                            )

                            send_telegram_msg(
                                f"🚨 *ALERTA CRASH*\nProtocolo de emergencia activado. {closed_count} posiciones cerradas de inmediato por seguridad."
                            )
                            self.circuit_breaker_active = True
                            # En el loop principal, si hay un crash, saltamos al siguiente ciclo
                            time.sleep(10)
                            continue

                        elif (
                            crash_result
                            and crash_result.get("recommended_action")
                            == "REDUCE_EXPOSURE"
                        ):
                            self.log(
                                f"⚠️ TURBULENCIA DETECTADA: {crash_result.get('crash_probability', 0):.0f}% - Restringiendo apalancamiento."
                            )

                    except Exception as e:
                        self.log(
                            f"❌ FATAL: El sistema Crash Predictor ha fallado en el loop principal. Error: {e}"
                        )
                        import traceback

                        self.log(traceback.format_exc())

                now = datetime.now()  # [FIX] Definida aquí para los bloques de hora (reporte diario, backup)
                self.check_weekly_schedule()
                self.check_weekly_maintenance_utc()
                # --- VERIFICACIÓN DE SEGURIDAD Y METAS ---
                # [v114 FIX] Protegido con try/except para evitar deadlock.
                # El comentario original indicaba riesgo de deadlock por acceso
                # concurrente desde _guardian_loop. El try asegura que un fallo
                # no detenga el loop principal.
                try:
                    # [FIX] Calcular PnL aquí y pasarlo para evitar deadlock interno en la función
                    base_bal_safe = (
                        self.daily_initial_balance
                        if self.daily_initial_balance > 0
                        else self.balance
                    )
                    pnl_real_safe, _ = self.brain.get_daily_real_pnl(base_bal_safe)

                    self.check_safety_and_goals(current_pnl=pnl_real_safe)
                except Exception as e_safety:
                    self.log(f"⚠️ check_safety_and_goals error (non-fatal): {e_safety}")
                # self.check_for_evolution() deshabilitado para evitar deadlock

                # [v114] Heartbeat: Actualizar timestamp de radar exitoso
                self.last_radar_update = time.time()

                # --- ACTUALIZACIÓN DINÁMICA DE MERCADO (Cada 12 horas) ---
                if time.time() - getattr(self, "last_market_update", 0) > 43200:
                    self.log("🎯 Actualizando lista de objetivos (Ciclo 12h)...")
                    self.acquire_targets()
                    self.last_market_update = time.time()

                # --- [V115-PRO] TRIAJE DINÁMICO: 1 llamada, N monedas, 50 ganadoras ---
                triage_snapshot = self._get_active_market_snapshot()
                # Construir tickers-dict de compatibilidad para todo el código downstream
                tickers = {item["symbol"]: item["ticker"] for item in triage_snapshot}
                # También mezclar con snapshot global por si BTC no está en PAIRS
                tickers.update(getattr(self, "_snapshot_tickers", {}))

                # [FIX] Sincronizar pairs_to_scan con el triaje dinámico
                # Antes: pairs_to_scan se actualizaba cada 12h por acquire_targets()
                # Ahora: se actualiza cada ciclo con los 50 pares del triaje dinámico
                new_triage_symbols = [item["symbol"] for item in triage_snapshot]
                if new_triage_symbols:
                    self.pairs_to_scan = new_triage_symbols

                # --- POST-MORTEM LOOP (Cada 5 min) ---
                if time.time() - getattr(self, "last_pm_check", 0) > 300:
                    self._perform_post_mortem()
                    self.last_pm_check = time.time()

                # Reporte Diario 23:00 (Laser Surgery)
                if (
                    now.hour == 23
                    and now.minute == 0
                    and not getattr(self, "day_report_sent", False)
                ):
                    self.log("📊 Enviando reporte diario 23:00...")
                    from reporter import generate_mobile_report

                    send_telegram_msg(
                        "📅 *REPORTE DE CIERRE DIARIO*\n"
                        + generate_mobile_report(self.balance)
                    )
                    self.day_report_sent = True
                if now.hour == 0:
                    self.day_report_sent = False

                # --- BACKUP DIARIO (00:00) ---
                if (
                    now.hour == 0
                    and now.minute == 0
                    and not getattr(self, "daily_backup_done", False)
                ):
                    if backup_database:
                        self.log("🛡️ Iniciando backup diario automático...")
                        try:
                            backup_database()
                        except Exception as e:
                            self.log(f"⚠️ Error backup diario: {e}")
                    self.daily_backup_done = True
                if now.hour == 1:
                    self.daily_backup_done = False

                # --- AUTO REPORTE MÓVIL (Cada 4 horas) ---
                if time.time() - last_report_time > (4 * 3600):
                    self.log("📱 Enviando reporte móvil automático...")
                    from reporter import generate_mobile_report

                    rep = generate_mobile_report(self.balance)
                    send_telegram_msg(rep)
                    last_report_time = time.time()

                # --- AI COACH AUTOMÁTICO (Cada 1 hora) ---
                if time.time() - last_coach_time > 3600:
                    coach_path = os.path.join(
                        os.path.dirname(__file__), "tools", "ai_coach.py"
                    )
                    if os.path.exists(coach_path):
                        self.log("🧠 Ejecutando AI Coach programado...")
                        try:
                            from tools.ai_coach import run_coach

                            run_coach(silent=True)
                            self.log("✅ AI Coach finalizado.")
                        except Exception as e:
                            self.log(f"⚠️ Error AI Coach auto: {e}")
                    else:
                        if not getattr(self, "_ai_coach_missing_logged", False):
                            self.log(
                                "ℹ️ AI Coach auto desactivado: tools/ai_coach.py no encontrado."
                            )
                            self._ai_coach_missing_logged = True
                    last_coach_time = time.time()

                # --- ML MODELS HEALTH CHECK (Cada 30 min) ---
                if time.time() - getattr(self, "last_ml_health_check", 0) > 1800:
                    if ML_MONITOR_AVAILABLE and self.ml_monitor:
                        self.log("🔍 Verificando salud de modelos ML...")
                        self.ml_healthy = self._check_ml_models_health()
                        if self.ml_healthy:
                            self.log("✅ ML Models OK")
                    self.last_ml_health_check = time.time()

                # --- ROTACIÓN DE LOGS (Cada 30 min) ---
                if time.time() - last_log_check > 1800:
                    # Log rotation deshabilitado (herramienta no disponible)
                    # try:
                    #     from tools.log_rotator import rotate_logs
                    #     rotate_logs()
                    # except Exception as e:
                    #     self.log(f"⚠️ Error Log Rotator: {e}")
                    last_log_check = time.time()

                # --- MONITOR DE RENDIMIENTO (ROLLBACK ALERT) ---
                if time.time() - getattr(self, "last_perf_check", 0) > 3600:
                    with self.db_lock:
                        drop_detected, curr_wr, prev_wr = (
                            self.brain.check_performance_drop()
                        )
                    if drop_detected:
                        self.log(
                            f"🚨 ALERTA DE RENDIMIENTO: WR cayó de {prev_wr:.1f}% a {curr_wr:.1f}%"
                        )
                        send_telegram_msg(
                            f"🚨 *ALERTA CRÍTICA: CAÍDA DE RENDIMIENTO*\n"
                            f"El Win Rate ha caído un *{prev_wr - curr_wr:.1f}%* en 24h.\n"
                            f"📉 Ayer: {prev_wr:.1f}% | Hoy: {curr_wr:.1f}%\n"
                            f"⚠️ *Sugerencia:* Considere revertir cambios recientes (Rollback)."
                        )
                    self.last_perf_check = time.time()

                # --- MEJORA: BTC PANIC FILTER (REACTIVO) ---
                self.btc_panic = self.force_btc_panic
                try:
                    # [FIX] Fetch BTC una sola vez por ciclo con cache
                    btc_data = self._get_cached_btc_data()
                    if btc_data is not None and len(btc_data) >= 2:
                        last_close = btc_data["close"].iloc[-1]
                        prev_close = btc_data["close"].iloc[-2]
                        btc_change = (last_close - prev_close) / prev_close * 100
                        self.market_btc_change_tf = btc_change

                        # Si BTC cae más del 1.5% en la última vela de 1h
                        if btc_change < -Config.BTC_PANIC_DROP_PERCENT:
                            self.btc_panic = True
                            # Alerta con cooldown de 5 minutos para no spamear
                            if time.time() - getattr(self, "last_panic_alert", 0) > 300:
                                self.log(
                                    f"🚨 BTC PANIC DETECTADO ({btc_change:.2f}%). Bloqueando COMPRAS, permitiendo SHORTS."
                                )
                                send_telegram_msg(
                                    f"🚨 *BTC PANIC FILTER*\nBitcoin ha caído un {btc_change:.2f}% en 1h. Modo SOLO VENTAS activado."
                                )
                                self.last_panic_alert = time.time()
                except Exception as e:
                    self.log(f"⚠️ Error en BTC Panic Filter: {e}")

                if not tickers:
                    self.log("⚠️ No se pudieron obtener precios. Reintentando en 10s...")
                    time.sleep(10)
                    continue

                # [v114] ML HEALTH VETO: Bloquear scouting si el ML no es saludable
                if not self.ml_healthy and Config.ML_HEALTH_VETO_ENABLED:
                    self.log("🛑 VETO ML ACTIVO: Saltando escaneo de señales...")
                    time.sleep(60)
                    continue

                # [FIX] Protección contra radar vacío
                if not self.pairs_to_scan:
                    self.log("⚠️ Lista de objetivos vacía. Reintentando adquisición...")
                    self.acquire_targets()
                    continue

                # [v110.2] Contadores de señales para diagnóstico
                signal_stats = {
                    "BUY": 0,
                    "SELL": 0,
                    "NEUTRAL": 0,
                    "VETO": 0,
                    "SHADOW": 0,
                    "REAL": 0,
                }

                self.log(f"📡 Radar: Escaneando {len(self.pairs_to_scan)} pares...")

                # --- OPTIMIZACIÓN: VETO HORARIO GLOBAL ---
                # Eliminamos el bloqueo total para permitir la exploración en Shadow

                # --- ACTUALIZACIÓN BTC Y SENTIMIENTO (v105.3.2) ---

                base_bal = (
                    self.daily_initial_balance
                    if self.daily_initial_balance > 0
                    else self.balance
                )
                pnl_real_hoy, _ = self.brain.get_daily_real_pnl(base_bal)

                # Extracción robusta de BTC
                btc_ticker = tickers.get(
                    "BTC/USDT:USDT",
                    tickers.get("BTC/USDT", {"last": self.market_btc_price}),
                )
                self.market_btc_price = float(
                    btc_ticker.get("last", self.market_btc_price)
                )

                if self.market_btc_price == 0:
                    try:
                        self.log("📡 Recatando precio de BTC manualmente...")
                        btc_t = self.execution.exchange.fetch_ticker("BTC/USDT")
                        self.market_btc_price = float(btc_t["last"])
                    except (
                        ccxt.NetworkError,
                        ccxt.ExchangeError,
                        KeyError,
                        ValueError,
                    ):
                        pass  # No se pudo obtener BTC, continuar sin él

                try:
                    # [FIX] Usar cache de BTC en vez de fetch duplicado
                    btc_1h = self._get_cached_btc_data()
                    if btc_1h is not None and not btc_1h.empty and len(btc_1h) >= 200:
                        # Verificar que los datos sean válidos
                        has_valid_data = (
                            btc_1h["close"].notna().sum() > 0
                            and btc_1h["high"].notna().sum() > 0
                            and btc_1h["low"].notna().sum() > 0
                        )
                        if not has_valid_data:
                            raise ValueError("Datos de BTC no válidos")

                        # Usar datos ya calculados o intentar cálculo simple
                        ema_200 = None
                        adx_14 = None

                        if "EMA_200" in btc_1h.columns:
                            ema_200 = btc_1h["EMA_200"].iloc[-1]
                        if "ADX_14" in btc_1h.columns:
                            adx_14 = btc_1h["ADX_14"].iloc[-1]

                        # Solo calcular si no existen
                        if ema_200 is None or pd.isna(ema_200):
                            try:
                                if "close" in btc_1h.columns:
                                    import ta.trend as ta_trend

                                    close_vals = btc_1h["close"].dropna()
                                    if len(close_vals) >= 200:
                                        ema_200 = (
                                            ta_trend.EMAIndicator(
                                                close_vals, window=200
                                            )
                                            .ema_indicator()
                                            .iloc[-1]
                                        )
                            except:
                                pass

                        if adx_14 is None or pd.isna(adx_14):
                            try:
                                high_vals = btc_1h["high"].dropna()
                                low_vals = btc_1h["low"].dropna()
                                close_vals = btc_1h["close"].dropna()
                                if (
                                    len(high_vals) >= 14
                                    and len(low_vals) >= 14
                                    and len(close_vals) >= 14
                                ):
                                    import ta.trend as ta_trend

                                    min_len = min(
                                        len(high_vals), len(low_vals), len(close_vals)
                                    )
                                    adx_14 = (
                                        ta_trend.ADXIndicator(
                                            high_vals.iloc[-min_len:],
                                            low_vals.iloc[-min_len:],
                                            close_vals.iloc[-min_len:],
                                            window=14,
                                        )
                                        .adx()
                                        .iloc[-1]
                                    )
                            except:
                                pass

                        if (
                            not isinstance(self.market_btc_price, (int, float))
                            or self.market_btc_price <= 0
                        ):
                            raise ValueError(
                                f"market_btc_price inválido: {self.market_btc_price}"
                            )
                        if not isinstance(ema_200, (int, float)) or pd.isna(ema_200):
                            raise ValueError(f"ema_200 inválido: {ema_200}")
                        if not isinstance(adx_14, (int, float)) or pd.isna(adx_14):
                            raise ValueError(f"adx_14 inválido: {adx_14}")

                        if adx_14 < 20:
                            new_sentiment, sentiment_color = "🟡 RANGO", "yellow"
                        elif self.market_btc_price > ema_200:
                            new_sentiment, sentiment_color = (
                                "🟢 TENDENCIA ALCISTA",
                                "green",
                            )
                        else:
                            new_sentiment, sentiment_color = (
                                "🔴 TENDENCIA BAJISTA",
                                "red",
                            )
                        if new_sentiment != self.current_sentiment[0]:
                            # Detectar cambio de Tendencia a Rango
                            if (
                                "RANGO" in new_sentiment
                                and "TENDENCIA" in self.current_sentiment[0]
                            ):
                                send_telegram_msg(
                                    "⚠️ *SUGERENCIA DE ESTRATEGIA*\nEl mercado ha entrado en RANGO. En el modelo institucional actual se mantiene motor *1H* con veto macro *4H*."
                                )

                            self.log(f"🌍 CAMBIO DE SENTIMIENTO: {new_sentiment}")
                            send_telegram_msg(
                                f"🌍 *RADAR DE SENTIMIENTO*\nEl mercado ha pasado a: *{new_sentiment}*"
                            )
                            self.current_sentiment = (new_sentiment, sentiment_color)
                except Exception as e:
                    import traceback

                    self.log(f"⚠️ Error en Radar de Sentimiento: {e}")
                    self.log(f"📋 Traceback: {traceback.format_exc(limit=3)}")

                # --- [V115-PRO] BUCLE DE TRIAJE: PARALELISMO TOTAL ---
                triage_snapshot = [
                    t
                    for t in triage_snapshot
                    if self.latency_quarantine.get(t["symbol"], 0) < time.time()
                ]
                top_triage = triage_snapshot[: Config.TOP_TRIAGE_COUNT]
                top_symbols = [entry["symbol"] for entry in top_triage]

                if not top_triage:
                    continue

                # LIMPIEZA DE SLOTS: Informar a la UI qué monedas no fueron seleccionadas
                for entry in triage_snapshot[Config.TOP_TRIAGE_COUNT :]:
                    self._update_scanner_status(
                        entry["symbol"], "⏸️ BAJO RVOL", qoe="--"
                    )

                # Actualizar WebSocket para vigilar solo lo que importa ahora
                if hasattr(self, "ws_manager") and self.ws_manager:
                    self.ws_manager.update_symbols(top_symbols)

                # [v115.1] Inicialización proactiva del Radar para evitar 'EN COLA'
                for t in top_triage:
                    self.update_radar(
                        t["symbol"],
                        {"signal": "WAIT", "mode": "NONE"},
                        0.0,
                        "⚡",
                        "⚡ PROCESANDO...",
                        {"tier": "IRON"},
                    )

                self.log(
                    f"⚡ TRIAJE PARALELO: Disparando {len(top_triage)} hilos para datos frescos..."
                )

                # FASE A: Fetch Paralelo
                results = {}
                max_workers = max(3, min(8, len(top_triage)))
                with concurrent.futures.ThreadPoolExecutor(
                    max_workers=max_workers
                ) as executor:
                    future_to_sym = {
                        executor.submit(
                            self._fetch_pair_data, t.get("symbol_raw", t["symbol"])
                        ): t["symbol"]
                        for t in top_triage
                    }

                    # Espera robusta: procesa completados, marca pendientes como timeout
                    done, not_done = concurrent.futures.wait(
                        future_to_sym,
                        timeout=Config.TRIAGE_TIMEOUT_SECONDS + 2,
                        return_when=concurrent.futures.ALL_COMPLETED,
                    )

                    for future in done:
                        sym_map = future_to_sym[future]
                        try:
                            sym_res, data, elapsed = future.result()
                            results[sym_map] = {"data": data, "elapsed": elapsed}
                        except Exception as e_thread:
                            self.log(
                                f"⚠️ Error devuelto por el hilo para {sym_map}: {e_thread}"
                            )
                            results[sym_map] = {"data": (None, None), "elapsed": -1}
                            self.update_radar(
                                sym_map,
                                {"signal": "WAIT", "mode": "NONE"},
                                0.0,
                                "❌",
                                "❌ ERROR HILO",
                                {"tier": "IRON"},
                                response_ms=-1,
                            )

                    if not_done:
                        self.log(
                            f"⏱️ TRIAJE TIMEOUT: {len(not_done)} (of {len(future_to_sym)}) hilos no terminaron a tiempo."
                        )
                        for future in not_done:
                            sym_map = future_to_sym[future]
                            future.cancel()
                            results[sym_map] = {"data": (None, None), "elapsed": -1}
                            self.update_radar(
                                sym_map,
                                {"signal": "WAIT", "mode": "NONE"},
                                0.0,
                                "⏱️",
                                "⏱️ TIMEOUT HILO",
                                {"tier": "IRON"},
                                response_ms=-1,
                            )

                # FASE B: Análisis Secuencial (IA)
                for triage_entry in top_triage:
                    symbol_raw = triage_entry["symbol"]
                    symbol = symbol_raw.split(":")[0]

                    res_data = results.get(symbol_raw)
                    if not res_data or not res_data.get("data"):
                        self.update_radar(
                            symbol,
                            {"signal": "WAIT", "mode": "NONE"},
                            0.0,
                            "⚪",
                            "⏱️ TIMEOUT",
                            {"tier": "IRON"},
                        )
                        continue

                    df_main, df_4h = res_data["data"]
                    elapsed = res_data["elapsed"]

                    # [V115-PRO] CIRCUIT BREAKER DE LATENCIA (Veto Activo)
                    latency_veto_ms = int(getattr(Config, "LATENCY_VETO_MS", 4500))
                    latency_quarantine_seconds = int(
                        getattr(Config, "LATENCY_QUARANTINE_SECONDS", 300)
                    )
                    if elapsed > latency_veto_ms or elapsed == -1:
                        self.log(
                            f"🔌 VETO LATENCIA: {symbol} tardó {elapsed}ms. "
                            f"Cuarentena de {int(latency_quarantine_seconds / 60)} min."
                        )
                        self.latency_quarantine[symbol] = (
                            time.time() + latency_quarantine_seconds
                        )
                        self.update_radar(
                            symbol,
                            {"signal": "WAIT", "mode": "NONE"},
                            0.0,
                            "⚪",
                            "🔌 LATENCIA",
                            {"tier": "IRON"},
                            response_ms=elapsed,
                        )
                        continue

                    try:
                        try:
                            # [ELIMINADO] Strike System por pérdidas consecutivas
                            # SHADOW libre para aprender

                            if df_main is None or df_4h is None or df_main.empty:
                                self.update_radar(
                                    symbol,
                                    {"signal": "WAIT", "mode": "NONE"},
                                    0.0,
                                    "⚪",
                                    "🚫 SIN DATOS",
                                    {"tier": "IRON"},
                                    response_ms=elapsed,
                                )
                                continue

                            # --- SIMULACIÓN DE CAOS (TESTING) ---
                            if self.force_chaos_mode:
                                # Inyectamos volatilidad artificial (ATR alto) para forzar régimen CHAOS
                                df_main["atr"] = df_main["close"] * 0.06

                            # --- FILTRO INSTITUCIONAL PRE-ANÁLISIS (FASE 2) ---
                            precio_actual = df_main["close"].iloc[-1]
                            # Cálculo rápido de volatilidad básica (high - low promedio de últimas 14 velas)
                            rango_promedio = (
                                df_main["high"].tail(14) - df_main["low"].tail(14)
                            ).mean()
                            atr_pct = (rango_promedio / precio_actual) * 100

                            # Si la volatilidad exige un SL que rompe nuestro riesgo máximo de $0.50, lo descartamos ANTES de la IA
                            max_allowed_sl_pct = (
                                Config.MAX_RISK_USD / Config.MIN_NOTIONAL_VALUE
                            ) * 100
                            if (atr_pct * 1.5) > max_allowed_sl_pct:
                                self.update_radar(
                                    symbol,
                                    {"signal": "WAIT", "mode": "NONE"},
                                    0.0,
                                    "⚪",
                                    f"⏭️ VOL EXTREMA ({atr_pct:.1f}%)",
                                    {"atr_pct": atr_pct / 100, "tier": "IRON"},
                                )
                                continue

                            # --- ESTRATEGIA: RECIBIR 6 VALORES (v106.7) ---
                            clean_symbol = symbol  # Para usar enctx
                            with self.lock:
                                active_sectors = [
                                    t["sector"] for t in self.active_trades.values()
                                ]

                            # --- DYNAMIC SETTINGS (AI COACH) ---
                            with self.db_lock:
                                dynamic_params = self.brain.get_dynamic_settings(symbol)

                            # [FIX] Bajamos la vara inicial para permitir que entren trades Shadow (Exploración)
                            default_min = (
                                Config.SHADOW_MIN_PROBABILITY_RANGE / 10.0
                            )  # Convierte 15.0% a 1.5
                            min_score = (
                                dynamic_params.get("min_score", default_min)
                                if dynamic_params
                                else default_min
                            )

                            # [DEFENSA RAG] Si la disonancia histórica es alta (>10%), exigimos excelencia (8.8)
                            if self.global_rag_impact > 10.0:
                                min_score = max(min_score, 8.8)

                            # Nota: Si el coach devuelve ej. 6.0, queremos que el umbral sea 60.
                            # Strategy.analyze espera min_score en formato 0-10 (ej. 8.2)

                            # --- NUEVA ESTRUCTURA DE RETORNO (v106.7) ---
                            # [OPTIMIZACIÓN] Lazy Loading de Liquidez:
                            # Primero analizamos sin Order Book. Si el score es bajo (<50), descartamos sin gastar API.
                            # Si el score es prometedor, descargamos el OB para el análisis final.
                            try:
                                with self.db_lock:
                                    res = Strategy.analyze(
                                        df_main,
                                        df_main,
                                        self.brain,
                                        symbol=symbol,
                                        order_book=None,  # Primera pasada sin OB
                                        ghost_model=self.ghost_model,
                                        scaler=self.scaler,
                                        btc_delta_tf=getattr(
                                            self,
                                            "market_btc_change_tf",
                                            0.0,
                                        ),
                                        min_score=min_score,
                                        funding_rate=0.0,
                                        df_4h=df_4h,
                                    )

                                # Si el score preliminar es prometedor (>= 50%), invertimos tiempo en descargar el Order Book y Funding Rate
                                if res[3] >= 50.0:
                                    try:
                                        order_book = (
                                            self.execution.exchange.fetch_order_book(
                                                symbol, limit=20
                                            )
                                        )
                                        funding_rate = self._get_cached_funding_rate(
                                            symbol
                                        )
                                    except Exception:
                                        order_book = None
                                        funding_rate = 0.0

                                    with self.db_lock:
                                        # Re-analizamos con el Order Book y Funding Rate inyectados
                                        res = Strategy.analyze(
                                            df_main,
                                            df_main,
                                            self.brain,
                                            symbol=symbol,
                                            order_book=order_book,
                                            ghost_model=self.ghost_model,
                                            scaler=self.scaler,
                                            btc_delta_tf=getattr(
                                                self,
                                                "market_btc_change_tf",
                                                0.0,
                                            ),
                                            min_score=min_score,
                                            funding_rate=funding_rate,
                                            df_4h=df_4h,
                                        )

                            except Exception as e:
                                # self.log(f"⚠️ Error análisis/liquidez para {symbol}: {e}")
                                with self.db_lock:
                                    res = Strategy.analyze(
                                        df_main,
                                        df_main,
                                        self.brain,
                                        symbol=symbol,
                                        order_book=None,
                                        ghost_model=self.ghost_model,
                                        scaler=self.scaler,
                                        btc_delta_tf=getattr(
                                            self,
                                            "market_btc_change_tf",
                                            0.0,
                                        ),
                                        min_score=min_score,
                                        funding_rate=0.0,
                                        df_4h=df_4h,
                                    )
                        except KeyError as e_key:
                            self.log(
                                f"⚠️ {symbol} descartado: Datos insuficientes para indicador clave ({e_key})."
                            )
                            self.update_radar(
                                symbol_raw,
                                {"signal": "WAIT", "mode": "NONE"},
                                0.0,
                                "⚪",
                                f"⚠️ KEY_ERR: {e_key}",
                                {"tier": "IRON"},
                            )
                            continue
                        except Exception as e_inner:
                            self.log(f"⚠️ Error análisis para {symbol}: {e_inner}")
                            self.update_radar(
                                symbol_raw,
                                {"signal": "WAIT", "mode": "NONE"},
                                0.0,
                                "⚪",
                                f"❌ ERROR: {str(e_inner)[:15]}",
                                {"tier": "IRON"},
                            )
                            continue

                        audit_signal, mode, price, prob_final, ind, votos = res

                        # [v114.5] Abortar si la estrategia detectó problemas de integridad
                        if "error" in ind:
                            # self.log(f"⏭️ {symbol} descartado por estrategia: {ind['error']}")
                            self.update_radar(
                                symbol_raw,
                                {"signal": "WAIT", "mode": "NONE"},
                                0.0,
                                "⚪",
                                f"⏭️ {ind['error']}",
                                ind,
                            )
                            continue

                        # [v110.2] Rastrear tipo de señal
                        if audit_signal in ["BUY", "SELL"]:
                            signal_stats[audit_signal] += 1
                        else:
                            signal_stats["NEUTRAL"] += 1

                        # Actualizar métrica global de RAG (Promedio Móvil)
                        curr_rag_imp = ind.get("rag_impact", 0.0)
                        self.global_rag_impact = (self.global_rag_impact * 0.98) + (
                            curr_rag_imp * 0.02
                        )

                        # [RAG EXPLAINER] Si el RAG interviene fuerte, explicamos por qué
                        if curr_rag_imp > 15.0 and ind.get("rag_evidence"):
                            ev_str = ", ".join(ind["rag_evidence"][:3])
                            self.log(
                                f"🧠 RAG INTERVENCIÓN ({curr_rag_imp:.1f}%): Basado en {ev_str}"
                            )

                        # [DEFENSA RAG] Si el impacto es crítico, reducimos riesgo global
                        if self.global_rag_impact > 10.0:
                            self.risk_multiplier = 0.5

                        voto_juez = votos.get("J", 50.0)
                        self.render_consensus_telemetry(symbol, prob_final, mode, votos)

                        # Compatibilidad legacy para objetos compartidos
                        decision = {"signal": audit_signal, "mode": mode}
                        # --- DETERMINACIÓN ROBUSTA DE TENDENCIA (v106.6) ---
                        ema_ref = (
                            df_main["ema"].iloc[-1]
                            if "ema" in df_main.columns
                            else price
                        )
                        trend_label = "RANGO"
                        current_adx = float(
                            ind.get(
                                "adx",
                                df_main["adx"].iloc[-1]
                                if "adx" in df_main.columns
                                else 0.0,
                            )
                        )
                        current_rsi = float(
                            df_main["rsi"].iloc[-1]
                            if "rsi" in df_main.columns
                            else 50.0
                        )

                        if current_adx > 25:
                            trend_label = "UP" if price > ema_ref else "DOWN"

                        vol_rel = (
                            (df_main["volume"].iloc[-1] / df_main["volume_ma"].iloc[-1])
                            if "volume_ma" in df_main.columns
                            and df_main["volume_ma"].iloc[-1] > 0
                            else 0.0
                        )

                        ctx = {
                            "rsi": current_rsi,
                            "adx": current_adx,
                            "close": price,
                            "df_1h": df_main,
                            "atr": (
                                df_main["atr"].iloc[-1]
                                if "atr" in df_main.columns
                                else 0.0
                            ),
                            "atr_pct": (
                                (df_main["atr"].iloc[-1] / price) if price > 0 else 0
                            ),
                            "trend": trend_label,
                            "regime": ind.get("regime", "NORMAL"),
                            "veto_reason": ind.get("veto_reason"),
                            "z_score": ind.get("z_score", 0.0),
                            "vol_24h": float(
                                self._snapshot_tickers.get(symbol_raw, {}).get(
                                    "quoteVolume", 0
                                )
                                or self._snapshot_tickers.get(symbol, {}).get(
                                    "quoteVolume", 0
                                )
                                or 0
                            )
                            if hasattr(self, "_snapshot_tickers")
                            and self._snapshot_tickers
                            else 0.0,
                            "tier": ind.get(
                                "tier", "IRON"
                            ),  # [v114] Propagación de Tier
                            "spread": ind.get(
                                "spread", 0.0
                            ),  # [V115-PRO] Para TP Fee-Aware
                        }

                        ob_status = Strategy.detect_order_block(df_main, symbol)
                        if ctx:
                            ctx["ob_status"] = ob_status

                        # --- MANEJO DE VOLUMEN MUERTO ---
                        if ctx and ctx.get("status") == "DEAD_VOLUME":
                            audit_verdict = "💤 VOLUMEN MUERTO"

                        if ctx:
                            # 1. Correlación con BTC (Efecto Arrastre)
                            btc_delta_tf = getattr(
                                self,
                                "market_btc_change_tf",
                                0.0,
                            )
                            ctx["btc_delta_tf"] = btc_delta_tf

                            # 2. Sentimiento de Apalancamiento (Funding Rate)
                            # [FIX] Usar cache en vez de fetch directo — funding cambia cada 8h
                            ctx["funding_rate"] = 0.0
                            if audit_signal in ["BUY", "SELL"]:
                                ctx["funding_rate"] = self._get_cached_funding_rate(
                                    symbol
                                )

                        # 3. Sesgo Horario
                        ctx["market_hour"] = datetime.now().hour

                        # === [NUEVO v114] FILTROS DE ENTRADA OPTIMIZADOS ===
                        # Aplicar filtros de RSI, ADX y horario antes de evaluar
                        rsi_val = ctx.get("rsi", 50)
                        adx_val = ctx.get("adx", 20)
                        current_time = datetime.now()
                        volatility_val = ctx.get("atr_pct", 0)

                        # [v114] Determinar prospecto de modo (Shadow/Real) para bypass de filtros
                        prob_prospect = prob_final
                        is_shadow_prospect = prob_prospect < (
                            Config.REAL_CONFIDENCE_MIN * 100
                        )

                        (
                            filter_passed,
                            filter_reason,
                            market_regime,
                            adaptive_filters,
                        ) = Strategy.check_entry_filters(
                            rsi_val,
                            adx_val,
                            current_time,
                            audit_signal,
                            volatility_val,
                            vol_rel,
                            is_shadow=is_shadow_prospect,
                        )

                        # [SHOCK MAP] Veto por falta de espacio operativo
                        # Regla: si la distancia al próximo SHOCK < 1.0%, no se dispara.
                        if filter_passed and audit_signal in ["BUY", "SELL"]:
                            shock_dist_pct, shock_level = self._get_shock_distance_pct(
                                df_main, audit_signal
                            )
                            if ctx is not None:
                                ctx["shock_dist_pct"] = shock_dist_pct
                                ctx["shock_level"] = shock_level

                            min_shock_dist = float(
                                getattr(Config, "SHOCK_MIN_DIST_PCT", 1.0)
                            )
                            if (
                                shock_dist_pct is not None
                                and shock_dist_pct < min_shock_dist
                            ):
                                filter_passed = False
                                filter_reason = f"SHOCK DEMASIADO CERCA ({shock_dist_pct:.2f}% < {min_shock_dist:.2f}%)"

                        # [v114] FILTRO BLACKLIST DE SÍMBOLOS (Mejorado)
                        blacklist = getattr(Config, "SYMBOL_BLACKLIST", [])
                        base_sym = symbol.split("/")[0]
                        if symbol in blacklist or base_sym in [
                            b.split("/")[0] for b in blacklist
                        ]:
                            filter_passed = False
                            filter_reason = f"VETO: Símbolo en blacklist ({symbol})"
                            self.log(f"⛔ {symbol} vetado: en blacklist")

                        # Aplicar pesos de día/hora a la probabilidad IA
                        day_weight = adaptive_filters.get("DAY_WEIGHT", 1.0)
                        hour_weight = adaptive_filters.get("HOUR_WEIGHT", 1.0)

                        # Info de pesos
                        ctx["day_weight"] = day_weight
                        ctx["hour_weight"] = hour_weight
                        ctx["market_regime"] = market_regime

                        # Loguear pesos
                        if day_weight > 1.1 or hour_weight > 1.1:
                            self.log(
                                f"⚡ {symbol}: Día x{day_weight:.2f}, Hora x{hour_weight:.2f} - MEJOR MOMENTO!"
                            )

                        # --- CÁLCULO ANTICIPADO DE IA (AUDITORÍA) ---
                        prob_ia = 0.0
                        adjustment_reason = "N/A"
                        # [v114 FIX] prob_ia solo se usa en _calculate_quant_consensus
                        # para el radar visual, NO como entrada adicional a decisión.
                        # Ghost (G) ya contribuye DENTRO de p_final con su peso ponderado.
                        # Usar votos.G aquí de nuevo sería double-counting.
                        # Asignamos directamente prob_final escalado para coherencia.
                        prob_ia = prob_final / 100.0

                        # --- PROTOCOLO CIRUGÍA LÁSER: CONSENSO CUÁNTICO ---
                        # Aplicamos el filtro del Senior Quant Strategist sobre la probabilidad bruta
                        if prob_ia > 0:
                            prob_ia, adjustment_reason = (
                                self._calculate_quant_consensus(prob_ia, ctx)
                            )

                        # [NUEVO v114] Aplicar pesos de día/hora a la probabilidad IA
                        day_weight = ctx.get("day_weight", 1.0)
                        hour_weight = ctx.get("hour_weight", 1.0)
                        combined_weight = (day_weight + hour_weight) / 2

                        # === [NUEVO] PONDERACIÓN POR RÉGIMEN BTC ===
                        # Ajustar probabilidad según alineación con régimen de BTC
                        btc_regime = self._get_market_regime()
                        regime_weight = 1.0
                        regime_reason = "N/A"

                        if btc_regime == "BULL_TREND":
                            if audit_signal == "BUY":
                                regime_weight = 1.15  # Bonus +15% para LONG en bull
                                regime_reason = "BULL_ALINGED"
                            else:
                                regime_weight = 0.85  # Penalty -15% para SHORT en bull
                                regime_reason = "BULL_COUNTER"
                        elif btc_regime == "BEAR_TREND":
                            if audit_signal == "SELL":
                                regime_weight = 1.15  # Bonus +15% para SHORT en bear
                                regime_reason = "BEAR_ALIGNED"
                            else:
                                regime_weight = 0.85  # Penalty -15% para LONG en bear
                                regime_reason = "BEAR_COUNTER"
                        else:
                            regime_reason = "RANGE_NEUTRAL"

                        final_weight = combined_weight * regime_weight
                        if regime_weight != 1.0:
                            self.log(
                                f"📊 {symbol}: BTC={btc_regime} [{regime_reason}] x{regime_weight:.2f}"
                            )

                        # [v114 paso B] BYPASS TEMPORAL PARA ELITE/GOLD
                        # Conservamos la probabilidad original antes de aplicar pesos temporales
                        original_prob = prob_final
                        tier_current = ctx.get("tier", "IRON")

                        if tier_current in ["ELITE", "GOLD"] and original_prob >= 80.0:
                            if final_weight < 1.0:
                                self.log(
                                    f"⚡ [BYPASS] {symbol} ({tier_current}): Ignorando penalización temporal (x{final_weight:.2f})"
                                )
                                prob_final = original_prob  # Bypass total
                        else:
                            prob_final = min(original_prob * final_weight, 100)

                        if final_weight != 1.0:
                            self.log(
                                f"⚖️ {symbol}: Prob {original_prob:.1f} → {prob_final:.1f} (x{final_weight:.2f})"
                            )

                        # --- Telemetría ML UI ---
                        self.last_ml_confidence = prob_final
                        ml_pure_prob = votos.get("G", 50.0)
                        self.last_ghost_weight = getattr(
                            self, "ghost_weight_override", 35.0
                        )

                        # --- FIX: VISUALIZACIÓN DE COOLDOWN (v105.7) ---
                        # Si el par está en enfriamiento, mostramos el tiempo restante en el Radar
                        # --- ACTUALIZACIÓN DE AUDITORÍA v106.5 ---
                        prob_ia_consensus = prob_final / 100.0
                        audit_verdict = self.get_audit_verdict(
                            symbol,
                            prob_ia_consensus,
                            audit_signal,
                            ob_status,
                            pnl_real_hoy,
                            self.current_target,
                            mode,
                            ctx,
                        )

                        # --- [1] KILL SWITCH (Seguridad Anti-Overfitting v114) ---
                        # [v114] VETO POR FILTROS (ADX, VOL, BLACKLIST)
                        if not filter_passed:
                            audit_verdict = f"⛔ VETO: {filter_reason}"
                            self.log(f"⛔ {symbol} vetado: {filter_reason}")

                        elif prob_final > 95.0:
                            self.log(
                                f"🚨 [KILL SWITCH] {symbol}: VETO por sobreconfianza ({prob_final:.1f}%). Posible overfitting."
                            )
                            audit_verdict = f"⛔ VETO: ML_CONF {prob_final:.1f}%"

                        # --- [2] A/B TEST INTERNO (Conflict Logger v114) ---
                        if ml_pure_prob >= 75.0 and "VETO" in audit_verdict:
                            conflict_msg = f"[A/B TEST CONFLICT] {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | {symbol} | ML_CONFIDENCE: {ml_pure_prob:.1f}% -> QUERÍA OPERAR ({audit_signal}) PERO FUE VETADO POR: {audit_verdict}\n"
                            try:
                                with open(
                                    "conflict_ab.log", "a", encoding="utf-8"
                                ) as f:
                                    f.write(conflict_msg)
                            except:
                                pass
                        elif ml_pure_prob < 50.0 and (
                            "OK" in audit_verdict or "SHADOW" in audit_verdict
                        ):
                            conflict_msg = f"[A/B TEST CONFLICT] {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | {symbol} | ML_CONFIDENCE: {ml_pure_prob:.1f}% -> QUERÍA ABORTAR PERO REGLAS APROBARON OPERAR ({audit_signal})\n"
                            try:
                                with open(
                                    "conflict_ab.log", "a", encoding="utf-8"
                                ) as f:
                                    f.write(conflict_msg)
                            except:
                                pass

                        if (
                            symbol in self.cooldown_pairs
                            and datetime.now() < self.cooldown_pairs[symbol]
                        ):
                            remaining = (
                                int(
                                    (
                                        self.cooldown_pairs[symbol] - datetime.now()
                                    ).total_seconds()
                                    / 60
                                )
                                + 1
                            )
                            audit_verdict = f"❄️ COOLDOWN ({remaining}m)"

                        # [v110.2] Rastrear veredictos - MOVER ANTES DEL CONTINUE
                        if (
                            "VETO" in audit_verdict
                            or "BLOQUEADO" in audit_verdict
                            or "COOLDOWN" in audit_verdict
                            or "RIESGO" in audit_verdict
                        ):
                            signal_stats["VETO"] += 1
                        elif "SHADOW" in audit_verdict or "CONCESIÓN" in audit_verdict:
                            signal_stats["SHADOW"] += 1
                        elif "OK" in audit_verdict:
                            signal_stats["REAL"] += 1

                        if (
                            not ind
                            or ind.get("rsi", {}).get("val") == "--"
                            or pd.isna(ind.get("rsi", {}).get("val"))
                        ):
                            self.log(
                                f"⚠️ SKIP {symbol}: RSI={ind.get('rsi', {}).get('val')} ind={bool(ind)}"
                            )
                            self.update_radar(
                                symbol_raw,
                                {"signal": "WAIT", "mode": "NONE"},
                                0.0,
                                "⚪",
                                "⏳ RSI N/A",
                                ind,
                            )
                            continue

                        # DEBUG: Show signal and prob values
                        self.log(
                            f"🔎 {symbol}: signal={audit_signal} prob={prob_final} verdict={audit_verdict[:30] if audit_verdict else 'None'}"
                        )

                        # [CIRUGÍA LÁSER] Actualizar scanner_history para Dashboard
                        # FIX: Usar update_radar unificado para evitar duplicados y errores de matching
                        # [V115-PRO] Usar tiempo de respuesta medido en la fase paralela
                        self.update_radar(
                            symbol_raw,
                            decision,
                            prob_final / 100.0,
                            ob_status,
                            audit_verdict,
                            ctx,
                            votos,
                            response_ms=elapsed,
                        )

                        is_shadow_exec = True
                        should_execute = False

                        # --- BLOQUEO DE CONCURRENCIA POR SÍMBOLO (INSTRUCCIÓN 1) ---
                        # Verificar que no haya operaciones activas en este símbolo ANTES de evaluar señales
                        with self.lock:
                            if symbol in self.active_trades:
                                self.log(
                                    f"🔒 BLOQUEADO {symbol}: Ya existe operación activa en este símbolo"
                                )
                                self.update_radar(
                                    symbol_raw,
                                    {"signal": "WAIT", "mode": "NONE"},
                                    0.0,
                                    "⚪",
                                    "🔒 OPERACIÓN ACTIVA",
                                    ind,
                                )
                                continue

                        # --- COOLDOWN UNIVERSAL (INSTRUCCIÓN 2) ---
                        # Verificar cooldown sin importar si es Shadow o Real
                        if (
                            symbol in self.cooldown_pairs
                            and datetime.now() < self.cooldown_pairs[symbol]
                        ):
                            remaining = (
                                int(
                                    (
                                        self.cooldown_pairs[symbol] - datetime.now()
                                    ).total_seconds()
                                    / 60
                                )
                                + 1
                            )
                            self.log(f"❄️ COOLDOWN {symbol}: {remaining}m restantes")
                            self.update_radar(
                                symbol_raw,
                                {"signal": "WAIT", "mode": "NONE"},
                                0.0,
                                "⚪",
                                f"❄️ COOLDOWN ({remaining}m)",
                                ind,
                            )
                            continue

                        # [v114] UMBRALES CORRECTOS:
                        # SHADOW: 60% - 74.99% (exploración/aprendizaje)
                        # REAL: 75% - 100% (operación real)
                        REAL_THRESHOLD = Config.REAL_CONFIDENCE_MIN * 100
                        SHADOW_MIN_THRESHOLD = float(
                            getattr(
                                Config,
                                "SHADOW_MODE_MIN",
                                Config.SHADOW_PROB_MIN * 100,
                            )
                        )

                        if audit_signal != "NEUTRAL" and filter_passed:
                            if prob_final >= REAL_THRESHOLD:
                                # ESCENARIO REAL: Alta confianza y sin vetos
                                is_shadow_exec = False
                                should_execute = True
                                self.log(
                                    f"🔥 DISPARO REAL: {symbol} confianza {prob_final:.1f}%"
                                )
                            elif prob_final >= SHADOW_MIN_THRESHOLD:
                                # ESCENARIO SHADOW: Confianza media o con concesiones aceptables
                                is_shadow_exec = True
                                should_execute = True
                                self.log(
                                    f"🧪 DISPARO SHADOW: {symbol} confianza {prob_final:.1f}%"
                                )

                        # Manejo especial para SCOUT (si el veredicto es OK pero no llega a REAL, degradamos a SHADOW)
                        if (
                            not should_execute
                            and audit_signal != "NEUTRAL"
                            and prob_final >= SHADOW_MIN_THRESHOLD
                        ):
                            if (
                                "SCOUT" in audit_verdict
                                or "OK" in audit_verdict
                                or "CONCESIÓN" in audit_verdict
                            ):
                                is_shadow_exec = True
                                should_execute = True
                                self.log(
                                    f"🔍 DEGRADACION A SHADOW: {symbol} (Veredicto: {audit_verdict})"
                                )

                        # EJECUCIÓN FINAL
                        if should_execute:
                            # Preparar contexto con votos e info adicional
                            if ctx:
                                ctx["votos"] = votos
                                ctx["prob_final"] = prob_final
                                ctx["audit_verdict"] = audit_verdict

                            exec_result = self.execute_order(
                                symbol=symbol,
                                side=audit_signal,
                                price=df_main["close"].iloc[-1],
                                atr=ctx.get("atr", 0) if ctx else 0,
                                is_shadow=is_shadow_exec,
                                context=ctx,
                                ob_status=ob_status,
                                override_usd_size=0.0,
                            )

                            if exec_result.startswith("OK"):
                                modo_str = "REAL" if not is_shadow_exec else "SHADOW"
                                self.log(
                                    f"✅ GATILLO {modo_str}: {symbol} [{audit_signal}] -> {audit_verdict}"
                                )

                                # [CIRUGÍA LÁSER] Actualizar Radar si hubo degradación
                                if "DEGRADED" in exec_result:
                                    deg_msg = (
                                        exec_result.split(": ")[1]
                                        if ": " in exec_result
                                        else "PROTECTION"
                                    )
                                    audit_verdict = f"🧪 SHADOW (PROT: {deg_msg})"
                                    # Actualizar historial
                                    for item in self.scanner_history:
                                        if item["symbol"] == symbol:
                                            item["result"] = audit_verdict
                                            break
                            elif exec_result not in ["COOLDOWN", "ALREADY_ACTIVE"]:
                                # [FIX] Si falla la ejecución, actualizar el Radar para no engañar al usuario
                                error_msg = (
                                    exec_result.split(": ")[0]
                                    if ": " in exec_result
                                    else exec_result
                                )
                                self.log(f"❌ FALLO EJECUCIÓN {symbol}: {exec_result}")
                                for item in self.scanner_history:
                                    if item["symbol"] == symbol:
                                        item["result"] = f"❌ ERR: {error_msg}"
                                        # Quitar icono de fuego/tubo para indicar fallo
                                        item["ia_real"] = "❌"
                                        item["ia_shadow"] = "❌"
                                        break
                        else:
                            # No se cumplen condiciones para ejecutar
                            pass

                        # [V115-PRO] Yield al sistema para evitar spin-waiting
                        time.sleep(0.05)  # 50ms entre símbolos para no saturar CPU

                    except Exception as e:
                        # Solo loggear errores críticos, no todos
                        import traceback

                        error_str = str(e)
                        self.log(
                            f"❌ ERROR en {symbol}: {error_str} | {traceback.format_exc(limit=3)}"
                        )

                        # [CIRUGÍA LÁSER] Reportar el crash en el Radar
                        for item in self.scanner_history:
                            if item["symbol"] == symbol:
                                item["result"] = f"❌ CRASH: {str(e)[:15]}"
                                break

                # --- PRIORIZACIÓN DE RADAR v105.4 ---
                if self.current_sentiment[0] == "🔴 TENDENCIA BAJISTA":
                    # Priorizar señales SELL en la visualización
                    self.scanner_history.sort(
                        key=lambda x: 0 if x.get("signal") == "SELL" else 1
                    )

                # [v110.2] LOG DE DIAGNÓSTICO: Distribución de señales
                total_scanned = (
                    signal_stats["BUY"] + signal_stats["SELL"] + signal_stats["NEUTRAL"]
                )
                if total_scanned > 0:
                    buy_pct = (signal_stats["BUY"] / total_scanned) * 100
                    sell_pct = (signal_stats["SELL"] / total_scanned) * 100
                    neutral_pct = (signal_stats["NEUTRAL"] / total_scanned) * 100
                    self.log(
                        f"📊 Señales: BUY {signal_stats['BUY']} ({buy_pct:.0f}%) | "
                        f"SELL {signal_stats['SELL']} ({sell_pct:.0f}%) | "
                        f"NEUTRAL {signal_stats['NEUTRAL']} ({neutral_pct:.0f}%) | "
                        f"Veredictos: ✅{signal_stats['REAL']} 🧪{signal_stats['SHADOW']} ❌{signal_stats['VETO']}"
                    )
                    # Guardar para comando /signals
                    self.last_signal_stats = signal_stats

                # --- CONSCIENCIA DE IA v105.5 ---
                suffix = self.self_adjust_exigency()
                valid_signals = [
                    item
                    for item in self.scanner_history
                    if "OK" in item["result"] or "SHADOW" in item["result"]
                ]

                if not valid_signals:
                    if self.current_sentiment[0] == "🔴 TENDENCIA BAJISTA":
                        self.ai_status_msg = f"🛡️ PROTECCIÓN: MERCADO HOSTIL{suffix}"
                    elif self.current_sentiment[0] == "🟡 TENDENCIA NEUTRAL":
                        self.ai_status_msg = f"🟡 RANGO: SIN CALIDAD{suffix}"
                    else:
                        self.ai_status_msg = f"🔍 ESCANEANDO OPORTUNIDADES{suffix}"
                else:
                    self.ai_status_msg = (
                        f"🎯 RADAR: {len(valid_signals)} SEÑALES ACTIVAS{suffix}"
                    )

                if time.time() - getattr(self, "last_cache_save", 0) > 300:
                    self.save_cache()
                    self.log("💾 Memoria guardada.")
                    self.last_cache_save = time.time()

                # Descanso del ciclo completo (Optimización de Latencia)
                time.sleep(Config.SCAN_INTERVAL)

                # [DEBUG] Log de peso API cada minuto
                if time.time() - getattr(self, "_api_weight_logged_time", 0) > 60:
                    weight = getattr(self, "_api_weight_counter", 0)
                    self.log(f"⚖️ API Weight (1 min): {weight}")
                    self._api_weight_counter = 0
                    self._api_weight_logged_time = time.time()

            except Exception as e:
                self.log(f"🚨 Error recuperado: {str(e)}. El escaneo continúa...")
                time.sleep(10)

    def _initial_load(self, dashboard_module):
        """[v114] Carga asíncrona de servicios para no bloquear la UI."""
        try:
            # --- CARGA INICIAL v104.0 ---
            self.connect()
            self.acquire_targets()
            self._load_ai_restrictions()

            # [v114] Auto-blacklist de símbolos con mal rendimiento
            self.log("🔍 Ejecutando auto-blacklist de poor performers...")
            self.brain.auto_blacklist_poor_performers(
                min_trades=5, max_loss_pct=-5.0, max_wr=40.0
            )

            self.check_for_evolution()

            if dashboard_module:
                try:
                    threading.Thread(
                        target=dashboard_module.start_dashboard,
                        args=(self,),
                        daemon=True,
                    ).start()
                    self.log("🖥️ Dashboard iniciado en segundo plano.")
                except Exception as e:
                    self.log(f"⚠️ Error Dashboard: {e}")

            # --- SRE SANITY CHECK (v114.1) ---
            if Config.MAX_SHADOW_TRADES <= 5:
                self.log(
                    f"⚠️ ADVERTENCIA DE CONFIGURACIÓN: MAX_SHADOW_TRADES está en {Config.MAX_SHADOW_TRADES}. "
                    "Esto limita severamente la capacidad de exploración. Considere un valor >= 20."
                )

            # --- VALIDACIÓN NUMÉRICA ---
            if not isinstance(self.balance, (int, float)) or pd.isna(self.balance):
                self.balance = 0.0

            self.init_complete.set()
            self.log("🚀 Sistema inicializado. Iniciando bucles de trabajo...")

            # Iniciar Workers
            threading.Thread(target=self._main_logic, daemon=True).start()
            threading.Thread(target=self._telegram_listener, daemon=True).start()
            threading.Thread(
                target=self._terminal_command_listener, daemon=True
            ).start()
            threading.Thread(target=self.start_silent_sync, daemon=True).start()
            threading.Thread(target=self._runtime_monitor_loop, daemon=True).start()

        except Exception as e:
            self.log(f"❌ FALLO CRÍTICO EN CARGA: {e}")
            self.init_complete.set()  # Evitar bloqueo de otros hilos

    def save_cache(self):
        """Guarda el caché de velas del DataService en disco (llamado cada 5 minutos y al apagar)."""
        try:
            if hasattr(self, "data_service") and self.data_service:
                self.data_service.save_cache()
        except Exception as e:
            self.log(f"⚠️ Error al guardar caché: {e}")

    def run(self):
        # Dashboard simplificado para terminal no-interactiva
        if not sys.stdout.isatty() and not getattr(Config, "FORCE_UI", False):
            print("""
╔═══════════════════════════════════════════════════════════════════════════╗
║              🏆 SNIPER AI v114 - MODO REAL 🏆                       ║
╠═══════════════════════════════════════════════════════════════════════════╣
║  🤖 14 Agentes: [T][V][J][G][C][L][F][S][O][R][M][D][E][K]               ║
║  🧠 Ghost ML: v114 Ensemble (RF+GB+XGB+LGB) | F1: 66%                ║
║  📊 Filtros: Pesos inteligentes (día/hora) | TP: +1%/+2%               ║
║  🛡️ Protecciones: Crash | Pump&Dump | SL: -6% | Daily: -3%             ║
╠═══════════════════════════════════════════════════════════════════════════╣
║  💰 BALANCE: $20.00 | PnL Hoy: +0.00% | Target: 5%                    ║
║  📈 REAL: 0 | SHADOW: 0 | WR: 0% | SCAN: 0 pares                     ║
║  ⚡ ACTIVO: Escaneando mercados...                                    ║
╚═══════════════════════════════════════════════════════════════════════════╝
            """)

        # [CIRUGÍA LÁSER] Reactivar UI de Terminal
        self.ui.start()

        # [v114] DISPARAR CARGA ASÍNCRONA
        # Esto permite que el hilo principal (UI) no se bloquee esperando a Binance
        threading.Thread(
            target=self._initial_load, args=(dashboard,), daemon=True
        ).start()

        # [v114] BUCLE PRINCIPAL DE RENDERIZADO (Hilo Principal)
        # Mantiene la terminal fluida (2s) mientras el bot trabaja en segundo plano.
        try:
            while self.is_running:
                try:
                    telemetry = self._collect_telemetry()

                    # Recolectar métricas ML si están disponibles
                    ml_metrics = {}
                    if self.ml_monitor:
                        ml_metrics = self.ml_monitor.get_all_metrics()

                    if hasattr(self, "ml_performance") and self.ml_performance:
                        try:
                            ml_metrics["performance"] = (
                                self.ml_performance.calculate_metrics()
                            )
                            ml_metrics["top_symbols"] = (
                                self.ml_performance.get_top_symbols(min_predictions=3)
                            )
                        except Exception as e_ml:
                            logger.warning(f"⚠️ Error en métricas ML: {e_ml}")

                    self.ui.update(
                        balance=self.balance,
                        trades=list(self.active_trades.values())
                        if hasattr(self, "active_trades")
                        else [],
                        scanner=self.scanner_history[:50]
                        if hasattr(self, "scanner_history")
                        else [],
                        db_stats=telemetry,
                        sentiment=getattr(self, "current_sentiment", "NEUTRAL"),
                        ml_metrics=ml_metrics,
                    )
                    if Config.ENABLE_UI:
                        self.ui.render()
                except Exception as e_ui:
                    logger.error(f"❌ UI ERROR: {e_ui}")
                    if self.is_running:
                        time.sleep(5)
                time.sleep(1)
        except KeyboardInterrupt:
            self.is_running = False
            self.ui.stop()
            self.log("🛑 Guardando caché y forzando flasheo de Shadow Logs...")
            self.save_cache()
            shadow_logger.stop()  # [FIX v116.1] Forzar guardado de trades pendientes
            self.log("✅ Caché y Logs guardados.")


_single_instance_lock = None


def _acquire_single_instance_lock() -> bool:
    """Evita múltiples instancias concurrentes del bot en el mismo directorio."""
    global _single_instance_lock

    lock_path = os.path.join(os.path.dirname(__file__), ".sniperai.lock")
    lock_file = open(lock_path, "a+", encoding="utf-8")
    try:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock_file.seek(0)
        owner = lock_file.read().strip() or "desconocido"
        msg = (
            f"🚫 Ya existe otra instancia ejecutándose (lock owner: {owner}). "
            "Abortando para proteger API/riesgo operativo."
        )
        logger.error(msg)
        print(msg, file=sys.stderr)
        lock_file.close()
        return False

    lock_file.seek(0)
    lock_file.truncate()
    lock_file.write(str(os.getpid()))
    lock_file.flush()
    _single_instance_lock = lock_file
    return True


if __name__ == "__main__":
    try:
        if not _acquire_single_instance_lock():
            raise SystemExit(1)
        Bot().run()
    except Exception as e:
        import traceback

        logger.critical(f"❌ FATAL ERROR: {e}\n{traceback.format_exc()}")
