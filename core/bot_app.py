"""
SNIPER AI v118 - Aplicacion principal del bot.
"""

import traceback
import asyncio
import time
import threading
import sys
from functools import lru_cache
import importlib.util
import signal
from typing import Any, Dict, Tuple
import logging
from logging.handlers import RotatingFileHandler

import warnings

warnings.filterwarnings("ignore", category=UserWarning, module="sklearn")

try:
    from core.api_weight_tracker import BinanceWeightTracker

    HAS_WEIGHT_TRACKER = True
except ImportError:
    BinanceWeightTracker = None
    HAS_WEIGHT_TRACKER = False

try:
    import tensorflow as tf
except ImportError:
    tf = None

from config import Config
from ui import UI
from learning import Brain, shadow_logger
from notifier import send_telegram_msg
from ws_manager import BinanceWebSocket
from core.command_router import handle_basic_command
from core.bot_facade import BotFacade
from core.bot_guardian import run_guardian_loop
from core.process_lock import acquire_single_instance_lock
from core.bot_runtime_monitor import (
    append_runtime_metric,
    get_rss_mb,
    run_runtime_monitor_loop,
)
from core.bot_market_state import detect_market_regime, warmup_hmm_regime
from core.bot_telemetry import collect_telemetry
from core.bot_ml_health import check_ml_models_health
from core.bot_wallet_sync import sync_wallet as run_wallet_sync
from core.bot_scorecard import (
    maybe_send_daily_exit_scorecard,
    send_daily_exit_scorecard,
)
from core.bot_connection import connect_to_binance
from core.bot_models_startup import init_models_and_startup_tasks
from core.bot_trade_monitor import monitor_open_trades as run_monitor_open_trades
from core.bot_initialization import (
    init_realtime_and_monitoring,
    init_runtime_state,
)
from core.bot_symbol_controls import (
    get_cached_btc_data,
    get_cached_funding_rate,
    load_runtime_symbol_controls,
    refresh_symbol_controls_if_due,
)
from core.bot_quant import calculate_quant_consensus
from core.bot_runtime_safety import check_safety_and_goals as evaluate_safety_and_goals
from core.bot_pair_fetch import fetch_pair_data as run_fetch_pair_data
from core.bot_io_loops import (
    perform_post_mortem,
    telegram_listener,
    websocket_monitor,
)
from core.bot_cli_ops import prioritize_targets, terminal_command_listener
from core.bot_core_setup import init_core_services_and_engines
from core.bot_ml_runtime import check_recent_mfe_health, init_ml_monitoring
from core.bot_maintenance import backup_database_placeholder, check_for_evolution
from core.bot_consensus_display import (
    render_consensus_telemetry as show_consensus_telemetry,
)
from core.bot_post_exit_analysis import calc_post_exit_drift, load_local_candles
from core.bot_weekly_ops import check_weekly_maintenance_utc, check_weekly_schedule
from core.bot_balance_ops import (
    get_current_balance as fetch_current_balance,
    handle_reset_pnl as run_handle_reset_pnl,
    start_silent_sync as run_start_silent_sync,
)
from core.bot_performance_ops import (
    get_ob_efficiency_report as build_ob_efficiency_report,
    perform_healthcheck as run_healthcheck,
    update_dynamic_risk as run_update_dynamic_risk,
)
from core.bot_runtime_ops import (
    check_instinctive_safety as run_check_instinctive_safety,
    close_all_positions_emergency,
    heartbeat_loop,
)
from core.bot_misc_ops import (
    get_vol_24h as resolve_vol_24h,
    handle_command as dispatch_command,
    load_ai_restrictions,
    self_adjust_exigency as adjust_exigency,
)
from core.bot_shutdown import request_graceful_shutdown

try:
    from export_master_dataset import export_dataset
except ImportError:
    export_dataset = None


def _module_available(module_name: str) -> bool:
    try:
        return importlib.util.find_spec(module_name) is not None
    except Exception:
        return False


logger = logging.getLogger("SniperAI")
logger.setLevel(logging.INFO)
if not logger.handlers:
    log_handler = RotatingFileHandler(
        Config.LOG_FILE,
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    log_formatter = logging.Formatter(
        "%(asctime)s | %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    )
    log_handler.setFormatter(log_formatter)
    logger.addHandler(log_handler)


def _backup_database_placeholder():
    return backup_database_placeholder()


backup_database = _backup_database_placeholder

try:
    import dashboard
except (ImportError, ModuleNotFoundError) as error:
    print(f"⚠️ Dashboard no disponible: {error}")
    dashboard = None

try:
    from ml_monitor import MLMonitor

    ML_MONITOR_AVAILABLE = True
except ImportError:
    ML_MONITOR_AVAILABLE = False
    MLMonitor = None
    print("⚠️ ML Monitor no disponible")


class Bot(BotFacade):
    def __init__(self):
        self.is_running = True
        self.ui = UI()
        self.brain = Brain()
        self.main_loop = None  # [SRE] Referencia al Global Event Loop
        self._backup_database_fn = backup_database
        self._ml_monitor_available = ML_MONITOR_AVAILABLE
        self._dashboard_module = dashboard
        self._logger = logger
        self._shadow_logger = shadow_logger
        self._main_loop_thread = None
        self._main_loop_ready = threading.Event()

        self._bind_main_loop_or_abort()

        self._init_core_services_and_engines()
        self._init_runtime_state()
        self._warmup_hmm_regime()
        self._init_realtime_and_monitoring()
        self._init_models_and_startup_tasks()

    def _bind_main_loop_or_abort(self):
        if (
            getattr(self, "main_loop", None) is not None
            and not self.main_loop.is_closed()
            and self.main_loop.is_running()
        ):
            return

        loop = asyncio.new_event_loop()
        self.main_loop = loop

        def _run_loop_forever():
            try:
                asyncio.set_event_loop(loop)
                self._main_loop_ready.set()
                loop.run_forever()
            except Exception as error:
                logger.critical(
                    f"🚨 FATAL BOOT ERROR: Event Loop thread falló: {error}"
                )
            finally:
                try:
                    loop.close()
                except Exception as error:
                    logger.warning(f"⚠️ No se pudo cerrar event loop principal: {error}")

        self._main_loop_thread = threading.Thread(
            target=_run_loop_forever,
            daemon=True,
            name="sniper-main-loop",
        )
        self._main_loop_thread.start()

        if not self._main_loop_ready.wait(timeout=2.0):
            logger.critical(
                "🚨 FATAL BOOT ERROR: Global Event Loop no pudo inicializarse en tiempo. Abortando arranque."
            )
            raise SystemExit(1)

        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and not loop.is_running():
            time.sleep(0.02)

        if (
            not getattr(self, "main_loop", None)
            or self.main_loop.is_closed()
            or not self.main_loop.is_running()
        ):
            logger.critical(
                "🚨 FATAL BOOT ERROR: Global Event Loop no está enlazado a la instancia del Bot. Abortando arranque."
            )
            raise SystemExit(1)

    def _init_core_services_and_engines(self):
        return init_core_services_and_engines(self)

    def _init_runtime_state(self):
        return init_runtime_state(
            self,
            has_weight_tracker=HAS_WEIGHT_TRACKER,
            weight_tracker_cls=BinanceWeightTracker,
        )

    def _init_realtime_and_monitoring(self):
        return init_realtime_and_monitoring(
            self,
            websocket_cls=BinanceWebSocket,
            ml_monitor_available=ML_MONITOR_AVAILABLE,
            ml_monitor_cls=MLMonitor,
        )

    def _init_models_and_startup_tasks(self):
        return init_models_and_startup_tasks(
            self,
            export_dataset_fn=export_dataset,
            backup_database_fn=backup_database,
            tf_module=tf,
        )

    def render_consensus_telemetry(self, symbol, p_final, modo, votos, regime=None):
        return show_consensus_telemetry(self, symbol, p_final, modo, votos, regime)

    def _load_ai_restrictions(self):
        return load_ai_restrictions(self)

    def self_adjust_exigency(self):
        return adjust_exigency(self)

    @staticmethod
    @lru_cache(maxsize=512)
    def _get_base_coin(symbol):
        clean_symbol = symbol.split(":")[0]
        base = clean_symbol.split("/")[0]
        return base

    def _get_vol_24h(self, symbol, tickers):
        return resolve_vol_24h(symbol, tickers)

    def _init_ml_monitoring(self):
        return init_ml_monitoring(self, ML_MONITOR_AVAILABLE)

    def _check_ml_models_health(self):
        return check_ml_models_health(self, ML_MONITOR_AVAILABLE)

    def _heartbeat_loop(self):
        return heartbeat_loop(self)

    def _websocket_monitor(self):
        return websocket_monitor(self)

    def check_for_evolution(self):
        return check_for_evolution(self)

    def log(self, msg):
        self.logs.append(msg)
        if len(self.logs) > Config.LOG_LIMIT:
            self.logs.pop(0)
        logger.info(msg)

    def _get_rss_mb(self) -> float:
        return get_rss_mb(self)

    def _append_runtime_metric(self, payload: Dict[str, Any]) -> None:
        return append_runtime_metric(self, payload)

    def _runtime_monitor_loop(self):
        return run_runtime_monitor_loop(self)

    def _collect_telemetry(self) -> Dict:
        return collect_telemetry(self, logger)

    def _get_market_regime(self) -> str:
        return detect_market_regime(self)

    def _warmup_hmm_regime(self) -> bool:
        return warmup_hmm_regime(self)

    def connect(self):
        return connect_to_binance(self)

    def sync_wallet(self):
        return run_wallet_sync(self)

    def check_instinctive_safety(self, symbol, context):
        return run_check_instinctive_safety(self, symbol, context)

    def _close_all_positions_emergency(self):
        return close_all_positions_emergency(self)

    def _update_dynamic_risk(self):
        return run_update_dynamic_risk(self)

    def monitor_open_trades(self):
        return run_monitor_open_trades(self)

    def _guardian_loop(self):
        return run_guardian_loop(self)

    def ai_coach_allows_escalation(self):
        if self.current_sentiment[0] == "🔴 TENDENCIA BAJISTA":
            return False
        return True

    def check_safety_and_goals(self, current_pnl=None):
        return evaluate_safety_and_goals(self, current_pnl=current_pnl)

    def start_silent_sync(self):
        return run_start_silent_sync(self)

    def get_current_balance(self):
        return fetch_current_balance(self)

    def handle_reset_pnl(self):
        return run_handle_reset_pnl(self)

    def perform_healthcheck(self):
        return run_healthcheck(self)

    def get_ob_efficiency_report(self):
        return build_ob_efficiency_report(self)

    def check_weekly_schedule(self):
        return check_weekly_schedule(self, _module_available)

    def check_weekly_maintenance_utc(self):
        return check_weekly_maintenance_utc(self)

    def handle_command(self, text: str):
        return dispatch_command(
            self,
            text=text,
            handle_basic_command_fn=handle_basic_command,
            export_dataset_fn=export_dataset,
            notify_fn=send_telegram_msg,
        )

    def _telegram_listener(self):
        return telegram_listener(self)

    def _terminal_command_listener(self):
        return terminal_command_listener(self)

    def _perform_post_mortem(self):
        return perform_post_mortem(self)

    def _calculate_quant_consensus(
        self, visual_prob: float, context: Dict
    ) -> Tuple[float, str]:
        return calculate_quant_consensus(visual_prob, context)

    def _prioritize_targets(self):
        return prioritize_targets(self)

    def _load_runtime_symbol_controls(self):
        return load_runtime_symbol_controls(self)

    def _refresh_symbol_controls_if_due(self):
        return refresh_symbol_controls_if_due(self)

    def _get_cached_funding_rate(self, symbol):
        return get_cached_funding_rate(self, symbol)

    def _get_cached_btc_data(self):
        return get_cached_btc_data(self)

    def _load_local_candles(self, symbol, timeframe="1h"):
        return load_local_candles(symbol, timeframe)

    def _calc_post_exit_drift(
        self, symbol, side, exit_ts_iso, exit_price, lookahead_bars=4
    ):
        return calc_post_exit_drift(
            self,
            symbol=symbol,
            side=side,
            exit_ts_iso=exit_ts_iso,
            exit_price=exit_price,
            lookahead_bars=lookahead_bars,
        )

    def _check_recent_mfe_health(self):
        return check_recent_mfe_health(self)

    def _send_daily_exit_scorecard(self):
        return send_daily_exit_scorecard(self)

    def _maybe_send_daily_exit_scorecard(self):
        return maybe_send_daily_exit_scorecard(self)

    def _fetch_pair_data(self, symbol):
        return run_fetch_pair_data(self, symbol)


def run_entrypoint():
    try:
        if not acquire_single_instance_lock(logger):
            raise SystemExit(1)

        bot = Bot()
        if (
            not getattr(bot, "main_loop", None)
            or bot.main_loop.is_closed()
            or not bot.main_loop.is_running()
        ):
            logger.critical(
                "🚨 FATAL BOOT ERROR: Global Event Loop no está enlazado a la instancia del Bot. Abortando arranque."
            )
            sys.exit(1)

        def _graceful_shutdown(signum, _frame):
            signal_name = (
                "SIGINT" if signum == getattr(signal, "SIGINT", -1) else "SIGTERM"
            )
            logger.warning(
                f"⚠️ Señal {signal_name} recibida. Iniciando apagado ordenado..."
            )
            request_graceful_shutdown(bot, reason=signal_name, logger=logger)

        signal.signal(signal.SIGINT, _graceful_shutdown)
        if hasattr(signal, "SIGTERM"):
            signal.signal(signal.SIGTERM, _graceful_shutdown)

        bot.run()

        if getattr(bot, "shutdown_in_progress", False):
            shutdown_done = bool(
                getattr(bot, "shutdown_complete", None)
                and bot.shutdown_complete.wait(timeout=85)
            )
            if not shutdown_done:
                logger.warning(
                    "⚠️ SHUTDOWN_SEQUENCE excedió ventana de espera local; saliendo para evitar SIGKILL de systemd."
                )
    except Exception as error:
        logger.critical(f"❌ FATAL ERROR: {error}\n{traceback.format_exc()}")
        sys.exit(1)
