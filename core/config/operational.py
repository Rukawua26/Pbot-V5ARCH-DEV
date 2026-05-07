import os
from dotenv import load_dotenv
from typing import List, Dict

load_dotenv()


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


class OperationalConfig:
    """Configuración de infraestructura, conectividad y sistema."""

    VERSION = "v118.4-PRO"
    BINANCE_API_KEY = os.getenv("BINANCE_API_KEY")
    BINANCE_API_SECRET = os.getenv("BINANCE_API_SECRET")

    PAPER_MODE = _env_bool("PAPER_MODE", True)
    PAPER_INITIAL_BALANCE = float(os.getenv("PAPER_INITIAL_BALANCE", "1000.0"))
    USE_TESTNET = _env_bool("USE_TESTNET", False)
    EXECUTION_BACKEND = os.getenv("EXECUTION_BACKEND", "live")
    SHADOW_SIM_LATENCY_MIN_MS = int(os.getenv("SHADOW_SIM_LATENCY_MIN_MS", "200"))
    SHADOW_SIM_LATENCY_MAX_MS = int(os.getenv("SHADOW_SIM_LATENCY_MAX_MS", "500"))
    SHADOW_SIM_REJECT_RATE = float(os.getenv("SHADOW_SIM_REJECT_RATE", "0.03"))
    SHADOW_SIM_PARTIAL_FILL_RATE = float(
        os.getenv("SHADOW_SIM_PARTIAL_FILL_RATE", "0.25")
    )
    SHADOW_SIM_PARTIAL_COMPLETE_RATE = float(
        os.getenv("SHADOW_SIM_PARTIAL_COMPLETE_RATE", "0.50")
    )
    SHADOW_SIM_PRICE_OUT_OF_RANGE_RATE = float(
        os.getenv("SHADOW_SIM_PRICE_OUT_OF_RANGE_RATE", "0.05")
    )
    SHADOW_SIM_MIN_PARTIAL_RATIO = float(
        os.getenv("SHADOW_SIM_MIN_PARTIAL_RATIO", "0.30")
    )
    PARTIAL_FILL_TIMEOUT_SECONDS = int(os.getenv("PARTIAL_FILL_TIMEOUT_SECONDS", "300"))
    PENDING_SEND_STALE_SECONDS = int(os.getenv("PENDING_SEND_STALE_SECONDS", "30"))
    GLOBAL_ENTRY_COOLDOWN_SECONDS = int(
        os.getenv("GLOBAL_ENTRY_COOLDOWN_SECONDS", "300")
    )
    SIGNAL_COOLDOWN_SHADOW_SECONDS = int(
        os.getenv("SIGNAL_COOLDOWN_SHADOW_SECONDS", "60")
    )
    HARD_SL_ATTACH_MAX_RETRIES = int(os.getenv("HARD_SL_ATTACH_MAX_RETRIES", "3"))
    CANCEL_ALL_DEGRADED_WINDOW_SECONDS = int(
        os.getenv("CANCEL_ALL_DEGRADED_WINDOW_SECONDS", "300")
    )
    CANCEL_ALL_DEGRADED_QUARANTINE_EVENTS = int(
        os.getenv("CANCEL_ALL_DEGRADED_QUARANTINE_EVENTS", "3")
    )
    CANCEL_ALL_DEGRADED_QUARANTINE_SECONDS = int(
        os.getenv("CANCEL_ALL_DEGRADED_QUARANTINE_SECONDS", "900")
    )
    NO_PRICE_EXIT_ESCALATION_SECONDS = int(
        os.getenv("NO_PRICE_EXIT_ESCALATION_SECONDS", "180")
    )
    NO_PRICE_EXIT_MIN_ESCALATION_SECONDS = int(
        os.getenv("NO_PRICE_EXIT_MIN_ESCALATION_SECONDS", "45")
    )
    NO_PRICE_ALLOW_MARKET_EXIT = _env_bool("NO_PRICE_ALLOW_MARKET_EXIT", False)
    SMART_EXIT_THRESHOLD_REAL = float(os.getenv("SMART_EXIT_THRESHOLD_REAL", "0.80"))
    SMART_EXIT_THRESHOLD_SHADOW = float(
        os.getenv("SMART_EXIT_THRESHOLD_SHADOW", "0.30")
    )
    MAX_ENTRY_SL_PCT = float(os.getenv("MAX_ENTRY_SL_PCT", "1.20"))
    STOP_LOSS_ATR_MODIFIER = float(os.getenv("STOP_LOSS_ATR_MODIFIER", "2.0"))
    ATR_SL_MULTIPLIER = STOP_LOSS_ATR_MODIFIER

    # --- ADOPCIÓN DE HUÉRFANOS ---
    ORPHAN_ADOPTION_MIN_SIZE_USD = float(os.getenv("ORPHAN_ADOPTION_MIN_SIZE_USD", "10.0"))
    ORPHAN_ADOPTION_MAX_SIZE_USD = float(os.getenv("ORPHAN_ADOPTION_MAX_SIZE_USD", "10000.0"))
    ORPHAN_SL_ATR_MULTIPLIER = float(os.getenv("ORPHAN_SL_ATR_MULTIPLIER", "2.0"))
    ORPHAN_SL_PERCENTAGE = float(os.getenv("ORPHAN_SL_PERCENTAGE", "0.02"))

    # Aliases para compatibilidad heredada
    API_KEY = BINANCE_API_KEY
    API_SECRET = BINANCE_API_SECRET

    ENABLE_UI = _env_bool("ENABLE_UI", True)
    FORCE_UI = _env_bool("FORCE_UI", False)
    SCAN_INTERVAL = 60  # 60s optimizado para timeframe 1h
    LOG_FILE = "sniper.log"
    LOG_LIMIT = 100

    TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
    TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
    TELEGRAM_RATE_LIMIT_SECONDS = float(
        os.getenv("TELEGRAM_RATE_LIMIT_SECONDS", "1.2")
    )
    SMART_EXIT_FEE_NOISE_MAX_MINUTES = float(
        os.getenv("SMART_EXIT_FEE_NOISE_MAX_MINUTES", "45")
    )
    SMART_EXIT_FEE_GUARD_ENABLED = _env_bool("SMART_EXIT_FEE_GUARD_ENABLED", False)
    REQUIRE_GHOST_MODEL_FOR_TRADING = _env_bool(
        "REQUIRE_GHOST_MODEL_FOR_TRADING", True
    )

    # --- [DEPRECADO] Lista estática de pares — YA NO SE USA ---
    # El bot ahora escanea TODO el mercado dinámicamente en cada ciclo.
    # Esta variable se mantiene solo para compatibilidad con código legacy.
    PAIRS = []

    # [DEPRECADO] Pares secundarios — YA NO SE USA
    SECONDARY_PAIRS = []
    SECONDARY_SCAN_INTERVAL_SECONDS = 4 * 3600

    SECTORS: Dict[str, List[str]] = {
        "L1": ["BTC", "ETH", "SOL", "ADA", "DOT", "AVAX"],
        "AI": ["FET", "AGIX", "OCEAN", "NEAR", "ICP"],
        "DEFI": ["UNI", "AAVE", "CRV", "MKR", "COMP"],
        "MEME": ["DOGE", "SHIB", "PEPE", "FLOKI"],
        "GAME": ["SAND", "MANA", "AXS", "GALA"],
    }

    # --- SISTEMA DE TRIAJE CINÉTICO ---
    # [FASE 1: ESCUDO TÉRMICO] Filtros de Liquidez
    TOP_TRIAGE_COUNT = 30
    TRIAGE_SPREAD_MAX = 0.0005  # 0.05% max spread (anti-slippage — protege trailing stop 0.3%)
    TRIAGE_TIMEOUT_SECONDS = 4
    TRIAGE_MAX_WORKERS = int(os.getenv("TRIAGE_MAX_WORKERS", "16"))
    TRIAGE_MIN_VOL_24H = 15_000_000  # $15M mínimo (filtro anti-basura)
    TRIAGE_RVOL_EMA_ALPHA = 0.02
    LATENCY_VETO_MS = 4500
    LATENCY_QUARANTINE_SECONDS = 300

    # [FASE 2: GATILLO SEGURO] Filtros de Pre-Ejecución
    ENTRY_SPREAD_VETO_THRESHOLD = 0.0005  # 0.05% - veto si > (protege trailing stop 0.3%)

    # --- CONTROLES TÁCTICOS POR SÍMBOLO (Decision Matrix) ---
    SYMBOL_CONTROLS_REFRESH_SECONDS = int(
        os.getenv("SYMBOL_CONTROLS_REFRESH_SECONDS", "1800")
    )
    SYMBOL_REDUCED_SIZE_MULTIPLIER = float(
        os.getenv("SYMBOL_REDUCED_SIZE_MULTIPLIER", "0.5")
    )

    # --- LÍMITES DE FETCHING ---
    CANDLE_FETCH_LIMIT = 500
    FETCH_TRIAL_LIMIT = 3
    MAX_SYMBOLS = 100
    MIN_CANDLE_HISTORY = 200
    MAX_REAL_PAIRS = 20
    MAX_SHADOW_PAIRS = 0
