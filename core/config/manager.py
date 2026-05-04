import os

from core.config.operational import OperationalConfig
from core.config.strategy import StrategyConfig


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        return int(float(raw))
    except (TypeError, ValueError):
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "y", "on"}


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

    # --- Umbrales de operación 1H (fase final) ---
    REAL_MODE_THRESHOLD = _env_float("REAL_MODE_THRESHOLD", 70.0)
    SHADOW_MODE_MIN = _env_float("SHADOW_MODE_MIN", 55.0)
    SHADOW_MODE_MAX = _env_float("SHADOW_MODE_MAX", 69.9)

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
