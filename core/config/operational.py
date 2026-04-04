import os
from dotenv import load_dotenv
from typing import List, Dict

load_dotenv()


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


class OperationalConfig:
    """Configuración de infraestructura, conectividad y sistema."""

    VERSION = "v118-PRO"
    BINANCE_API_KEY = os.getenv("BINANCE_API_KEY")
    BINANCE_API_SECRET = os.getenv("BINANCE_API_SECRET")

    PAPER_MODE = _env_bool("PAPER_MODE", True)
    USE_TESTNET = _env_bool("USE_TESTNET", False)
    EXECUTION_BACKEND = os.getenv("EXECUTION_BACKEND", "live")
    SHADOW_SIM_LATENCY_MIN_MS = int(os.getenv("SHADOW_SIM_LATENCY_MIN_MS", "200"))
    SHADOW_SIM_LATENCY_MAX_MS = int(os.getenv("SHADOW_SIM_LATENCY_MAX_MS", "500"))
    SHADOW_SIM_REJECT_RATE = float(os.getenv("SHADOW_SIM_REJECT_RATE", "0.03"))
    SHADOW_SIM_PARTIAL_FILL_RATE = float(
        os.getenv("SHADOW_SIM_PARTIAL_FILL_RATE", "0.25")
    )
    SHADOW_SIM_MIN_PARTIAL_RATIO = float(
        os.getenv("SHADOW_SIM_MIN_PARTIAL_RATIO", "0.30")
    )
    PARTIAL_FILL_TIMEOUT_SECONDS = int(os.getenv("PARTIAL_FILL_TIMEOUT_SECONDS", "300"))

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
    TOP_TRIAGE_COUNT = 50
    TRIAGE_SPREAD_MAX = 0.005
    TRIAGE_TIMEOUT_SECONDS = 4
    TRIAGE_MIN_VOL_24H = 10_000_000
    TRIAGE_RVOL_EMA_ALPHA = 0.02
    LATENCY_VETO_MS = 4500
    LATENCY_QUARANTINE_SECONDS = 300

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
