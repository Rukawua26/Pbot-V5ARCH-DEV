from core.config.operational import OperationalConfig
from core.config.strategy import StrategyConfig


class Config(OperationalConfig, StrategyConfig):
    """
    Clase de configuración unificada.
    Hereda de Operational y Strategy para mantener la compatibilidad con el resto del código.
    """

    MAX_SPREAD_THRESHOLD = 0.008  # Valor específico v115
    MAX_SLIPPAGE = 0.001
    VIRTUAL_FEE = 0.001

    # --- Umbrales de operación 1H (fase final) ---
    REAL_MODE_THRESHOLD = 70.0
    SHADOW_MODE_MIN = 55.0
    SHADOW_MODE_MAX = 69.9

    # --- Mapa de SHOCKS (filtro de espacio operativo) ---
    SHOCK_MIN_DIST_PCT = 1.0
    SHOCK_PIVOT_WINDOW = 3
    SHOCK_LOOKBACK_BARS = 240

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
