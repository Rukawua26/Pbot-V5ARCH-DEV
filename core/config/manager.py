import os

from core.config.operational import OperationalConfig
from core.config.strategy import StrategyConfig


_CONFIG_ENV_WARNINGS: list[str] = []


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        _CONFIG_ENV_WARNINGS.append(f"{name}={raw!r} inválido; usando default {default!r}")
        return default


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        return int(float(raw))
    except (TypeError, ValueError):
        _CONFIG_ENV_WARNINGS.append(f"{name}={raw!r} inválido; usando default {default!r}")
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return default
    normalized = str(raw).strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    _CONFIG_ENV_WARNINGS.append(f"{name}={raw!r} inválido; usando default {default!r}")
    return default


class Config(OperationalConfig, StrategyConfig):
    """
    Clase de configuración unificada.
    Hereda de Operational y Strategy para mantener la compatibilidad con el resto del código.
    """

    MAX_SPREAD_THRESHOLD = _env_float("MAX_SPREAD_THRESHOLD", 0.008)
    MAX_SLIPPAGE = _env_float("MAX_SLIPPAGE", 0.001)
    VIRTUAL_FEE = _env_float("VIRTUAL_FEE", 0.001)
    ENTRY_IOC_CONFIRM_TIMEOUT_SECONDS = _env_float(
        "ENTRY_IOC_CONFIRM_TIMEOUT_SECONDS", 2.0
    )
    BTC_RISK_MAX_PRICE_AGE_SECONDS = _env_float("BTC_RISK_MAX_PRICE_AGE_SECONDS", 90.0)
    HALT_RECOVERY_MAX_ATTEMPTS = _env_int("HALT_RECOVERY_MAX_ATTEMPTS", 5)

    # --- Risk overrides ---
    RISK_PER_TRADE_PERCENT = _env_float("RISK_PER_TRADE_PERCENT", 1.2)
    RISK_PER_TRADE = RISK_PER_TRADE_PERCENT
    RISK_PER_TRADE_PCT = RISK_PER_TRADE_PERCENT / 100.0
    MAX_RISK_USD = _env_float("MAX_RISK_USD", 2.0)
    MAX_OPEN_TRADES = _env_int("MAX_OPEN_TRADES", 3)
    MAX_DIRECTIONAL_TRADES = _env_int("MAX_DIRECTIONAL_TRADES", 2)
    DAILY_LOSS_LIMIT = _env_float("DAILY_LOSS_LIMIT", 2.0)

    # --- ML weight overrides ---
    XGB_WEIGHT = _env_float("XGB_WEIGHT", 0.30)
    LGB_WEIGHT = _env_float("LGB_WEIGHT", 0.30)
    RF_WEIGHT = _env_float("RF_WEIGHT", 0.20)
    GB_WEIGHT = _env_float("GB_WEIGHT", 0.15)
    LR_WEIGHT = _env_float("LR_WEIGHT", 0.05)

    # --- Umbrales de operación 1H (fase final) ---
    REAL_MODE_THRESHOLD = _env_float("REAL_MODE_THRESHOLD", 70.0)
    SHADOW_MODE_MIN = _env_float("SHADOW_MODE_MIN", 55.0)
    SHADOW_MODE_MAX = _env_float("SHADOW_MODE_MAX", 69.9)

    # --- HMM/Markov regime probability controls ---
    MARKOV_BREAKOUT_MIN = _env_float("MARKOV_BREAKOUT_MIN", 75.0)
    MARKOV_DEAD_ZONE_MAX = _env_float("MARKOV_DEAD_ZONE_MAX", 30.0)
    MARKOV_RANGE_BREAKOUT_WEIGHT = _env_float("MARKOV_RANGE_BREAKOUT_WEIGHT", 0.90)
    MARKOV_RANGE_STANDARD_WEIGHT = _env_float("MARKOV_RANGE_STANDARD_WEIGHT", 0.75)
    MARKOV_BULL_STRONG_WEIGHT = _env_float("MARKOV_BULL_STRONG_WEIGHT", 1.10)
    MARKOV_BEAR_STRONG_WEIGHT = _env_float("MARKOV_BEAR_STRONG_WEIGHT", 1.10)
    MARKOV_SNAPSHOT_MAX_AGE_SECONDS = _env_float(
        "MARKOV_SNAPSHOT_MAX_AGE_SECONDS", 2 * 60 * 60
    )
    MARKOV_SNAPSHOT_STALE_SECONDS = _env_float(
        "MARKOV_SNAPSHOT_STALE_SECONDS", 6 * 60 * 60
    )
    MARKOV_SNAPSHOT_PERSIST_INTERVAL_SECONDS = _env_float(
        "MARKOV_SNAPSHOT_PERSIST_INTERVAL_SECONDS", 5 * 60
    )
    MARKOV_PREVETO_BEARISH_REVERSAL_MIN = _env_float(
        "MARKOV_PREVETO_BEARISH_REVERSAL_MIN", 85.0
    )
    BEAR_COUNTER_WEIGHT = _env_float("BEAR_COUNTER_WEIGHT", 0.70)

    # --- BEAR_TREND pair universe reduction ---
    BEAR_TREND_MAX_PAIRS = _env_int("BEAR_TREND_MAX_PAIRS", 15)
    BEAR_TREND_MIN_VOL = _env_float("BEAR_TREND_MIN_VOL", 50_000_000)
    BEAR_TREND_CONFIDENCE_BOOST = _env_float("BEAR_TREND_CONFIDENCE_BOOST", 10.0)

    # --- Mapa de SHOCKS (filtro de espacio operativo) ---
    SHOCK_MIN_DIST_PCT = _env_float("SHOCK_MIN_DIST_PCT", 0.4)
    SHOCK_PIVOT_WINDOW = _env_int("SHOCK_PIVOT_WINDOW", 3)
    SHOCK_LOOKBACK_BARS = _env_int("SHOCK_LOOKBACK_BARS", 240)

    # --- Breakout Hunter (pasivo) ---
    BREAKOUT_WATCH_ENABLED = _env_bool("BREAKOUT_WATCH_ENABLED", True)
    BREAKOUT_MIN_IA_PROB = _env_float("BREAKOUT_MIN_IA_PROB", 55.0)
    BREAKOUT_SHOCK_MIN_IA_PROB = _env_float("BREAKOUT_SHOCK_MIN_IA_PROB", 50.0)
    BREAKOUT_WATCH_COHERENCE_ENABLED = _env_bool(
        "BREAKOUT_WATCH_COHERENCE_ENABLED", True
    )
    BREAKOUT_COHERENCE_MIN_IA_PROB = _env_float(
        "BREAKOUT_COHERENCE_MIN_IA_PROB", 50.0
    )
    BREAKOUT_BUFFER_PCT = _env_float("BREAKOUT_BUFFER_PCT", 0.5)
    BREAKOUT_VOLUME_MULT = _env_float("BREAKOUT_VOLUME_MULT", 1.5)
    BREAKOUT_TIMEOUT_MINUTES = _env_int("BREAKOUT_TIMEOUT_MINUTES", 60)
    BREAKOUT_SEMI_ACTIVE_SHADOW = _env_bool("BREAKOUT_SEMI_ACTIVE_SHADOW", True)
    BREAKOUT_EXTREME_IA_PROB = _env_float("BREAKOUT_EXTREME_IA_PROB", 75.0)
    DIRECTIONAL_COHERENCE_FILTER = _env_bool("DIRECTIONAL_COHERENCE_FILTER", True)

    # --- Open Interest Delta Filter (v118.3) ---
    OI_FILTER_ENABLED = _env_bool("OI_FILTER_ENABLED", True)
    OI_DELTA_THRESHOLD = _env_float("OI_DELTA_THRESHOLD", 0.005)
    OI_CACHE_TTL_SECONDS = _env_int("OI_CACHE_TTL_SECONDS", 60)

    # --- Exit Engine v118 (dinámico) ---
    EXIT_ENGINE_V1_ENABLED = _env_bool("EXIT_ENGINE_V1_ENABLED", True)
    EXIT_TIME_DECAY_BARS = _env_int("EXIT_TIME_DECAY_BARS", 4)
    EXIT_ESCAPE_VELOCITY_PCT = _env_float("EXIT_ESCAPE_VELOCITY_PCT", 0.2)
    EXIT_STRUCTURAL_ATR_BUFFER = _env_float("EXIT_STRUCTURAL_ATR_BUFFER", 0.25)
    EXIT_STRUCTURAL_MIN_BUFFER_PCT = _env_float("EXIT_STRUCTURAL_MIN_BUFFER_PCT", 0.05)
    EXIT_STRUCTURAL_MIN_HOLD_SECONDS = _env_int("EXIT_STRUCTURAL_MIN_HOLD_SECONDS", 120)
    EXIT_TRAILING_ACTIVATION_PCT = _env_float("EXIT_TRAILING_ACTIVATION_PCT", 0.9)
    EXIT_TRAILING_ATR_MULT = _env_float("EXIT_TRAILING_ATR_MULT", 3.0)
    EXIT_TRAILING_ATR_MULT_TIGHT = _env_float("EXIT_TRAILING_ATR_MULT_TIGHT", 1.5)
    EXIT_TRAILING_TIGHTEN_PNL_PCT = _env_float("EXIT_TRAILING_TIGHTEN_PNL_PCT", 2.0)
    EXIT_TRAILING_MIN_DISTANCE_PCT = _env_float("EXIT_TRAILING_MIN_DISTANCE_PCT", 0.3)
    EXIT_BREAKEVEN_TRIGGER_PCT = _env_float("EXIT_BREAKEVEN_TRIGGER_PCT", 1.2)
    EXIT_BREAKEVEN_ATR_MULT = _env_float("EXIT_BREAKEVEN_ATR_MULT", 1.2)
    EXIT_BREAKEVEN_LOCK_PCT = _env_float("EXIT_BREAKEVEN_LOCK_PCT", 0.1)
    EXIT_FLAT_TIME_DECAY_BARS = _env_int("EXIT_FLAT_TIME_DECAY_BARS", 3)
    EXIT_FLAT_TIME_DECAY_ATR_MULT = _env_float("EXIT_FLAT_TIME_DECAY_ATR_MULT", 0.5)

    # Compatibilidad con rutas actuales de decisión (0-1)
    REAL_CONFIDENCE_MIN = REAL_MODE_THRESHOLD / 100.0
    REAL_CONFIDENCE_THRESHOLD = REAL_CONFIDENCE_MIN
    SHADOW_PROB_MIN = SHADOW_MODE_MIN / 100.0

    @staticmethod
    def sanitize_symbol(sym: str) -> str:
        """Normalización estricta a SYMBOL/USDT."""
        if not sym:
            return ""
        clean = str(sym).split(":")[0].strip().upper()

        # Símbolo con barra
        if "/" in clean:
            parts = clean.split("/")
            base = parts[0]
            if len(base) < 2:
                return ""
            return f"{base}/USDT"

        # Sufijos pegados
        for suffix in ["USDT", "BUSD", "USDC", "USD"]:
            if clean.endswith(suffix):
                base = clean[: -len(suffix)]
                if len(base) > 1:
                    return f"{base}/USDT"

        # Alfanumérico simple
        if len(clean) > 1 and clean.isalnum():
            return f"{clean}/USDT"

        return ""

    @classmethod
    def sanitize_pairs(cls, pairs: list) -> list:
        """Sanitizador de símbolos deduplicado."""
        sanitized = []
        for p in pairs:
            cleaned = cls.sanitize_symbol(p)
            if cleaned and cleaned.endswith("/USDT"):
                sanitized.append(cleaned)
        return list(dict.fromkeys(sanitized))

    @classmethod
    def env_warnings(cls) -> list[str]:
        return list(_CONFIG_ENV_WARNINGS)

    @classmethod
    def validate(cls) -> list[str]:
        errors = []
        if not (0.0 < float(cls.RISK_PER_TRADE_PERCENT) <= 5.0):
            errors.append("RISK_PER_TRADE_PERCENT debe estar en (0, 5]")
        if int(cls.MAX_OPEN_TRADES) < 0 or int(cls.MAX_OPEN_TRADES) > 20:
            errors.append("MAX_OPEN_TRADES debe estar entre 0 y 20")
        if int(cls.MAX_DIRECTIONAL_TRADES) < 0 or int(cls.MAX_DIRECTIONAL_TRADES) > int(cls.MAX_OPEN_TRADES):
            errors.append("MAX_DIRECTIONAL_TRADES debe estar entre 0 y MAX_OPEN_TRADES")
        if float(cls.SHADOW_MODE_MIN) >= float(cls.REAL_MODE_THRESHOLD):
            errors.append("SHADOW_MODE_MIN debe ser menor que REAL_MODE_THRESHOLD")
        if float(cls.SHADOW_MODE_MAX) < float(cls.SHADOW_MODE_MIN):
            errors.append("SHADOW_MODE_MAX debe ser >= SHADOW_MODE_MIN")
        if float(cls.MAX_SLIPPAGE) < 0 or float(cls.MAX_SLIPPAGE) > 0.05:
            errors.append("MAX_SLIPPAGE debe estar entre 0 y 0.05")
        if float(cls.BTC_RISK_MAX_PRICE_AGE_SECONDS) <= 0:
            errors.append("BTC_RISK_MAX_PRICE_AGE_SECONDS debe ser positivo")
        if int(cls.HALT_RECOVERY_MAX_ATTEMPTS) < 1:
            errors.append("HALT_RECOVERY_MAX_ATTEMPTS debe ser >= 1")

        total_weight = cls.XGB_WEIGHT + cls.LGB_WEIGHT + cls.RF_WEIGHT + cls.GB_WEIGHT + cls.LR_WEIGHT
        if not (0.99 <= float(total_weight) <= 1.01):
            errors.append("La suma de pesos ML debe estar cerca de 1.0")
        return errors
