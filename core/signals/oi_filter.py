"""
SNIPER AI v118.3 - OI Delta Filter
==================================
Filtro externo rigido de Open Interest.

Detecta senales falsas (short squeeze, long liquidation) comparando la
direccion del precio con el cambio en Open Interest.
"""

import logging
import time
from typing import Optional, Tuple

from config import Config

logger = logging.getLogger("SniperAI")

# Cache interno: {symbol: {"oi": float, "ts": float}}
_oi_cache: dict = {}


def _get_cached_oi(symbol: str) -> Optional[float]:
    """Retorna el OI anterior cacheado si no ha expirado."""
    entry = _oi_cache.get(symbol)
    if not entry:
        return None
    ttl = float(getattr(Config, "OI_CACHE_TTL_SECONDS", 60))
    if time.time() - entry["ts"] > ttl * 3:
        return None
    return entry["oi"]


def _update_oi_cache(symbol: str, oi_value: float):
    """Actualiza el cache con el OI actual."""
    _oi_cache[symbol] = {"oi": oi_value, "ts": time.time()}


def fetch_oi_delta(bot, symbol: str) -> Tuple[Optional[float], Optional[float]]:
    """
    Obtiene el OI actual y calcula el delta contra el valor cacheado.

    Returns:
        (oi_delta_pct, oi_current) - delta como fraccion (0.01 = 1%),
        o (None, None) si no hay dato util.
    """
    if not bool(getattr(Config, "OI_FILTER_ENABLED", False)):
        return None, None
    try:
        execution = getattr(bot, "execution", None)
        if execution is None:
            return None, None

        weight_tracker = getattr(bot, "weight_tracker", None)
        if weight_tracker and weight_tracker.should_block("market"):
            return None, None

        oi_response = execution.fetch_open_interest(symbol)
        if not isinstance(oi_response, dict):
            return None, None

        oi_current = float(oi_response.get("openInterestAmount", 0) or 0)
        if oi_current <= 0:
            return None, None

        oi_previous = _get_cached_oi(symbol)
        _update_oi_cache(symbol, oi_current)
        if oi_previous is None or oi_previous <= 0:
            return None, oi_current

        oi_delta_pct = (oi_current - oi_previous) / oi_previous
        return oi_delta_pct, oi_current
    except Exception as error:
        logger.warning(f"⚠️ OI delta calc falló para {symbol}: {error}")
        return None, None


def validate_signal_with_oi(
    audit_signal: str, delta_price_pct: float, oi_delta_pct: Optional[float]
) -> str:
    """
    Valida la senal contra el cambio de OI.

    Returns:
        "CONFIRMED" | "VETO" | "NEUTRAL"
    """
    if oi_delta_pct is None:
        return "NEUTRAL"

    threshold = float(getattr(Config, "OI_DELTA_THRESHOLD", 0.005))
    if audit_signal == "BUY":
        if delta_price_pct > 0 and oi_delta_pct > threshold:
            return "CONFIRMED"
        if delta_price_pct > 0 and oi_delta_pct < -threshold:
            return "VETO"
    elif audit_signal == "SELL":
        if delta_price_pct < 0 and oi_delta_pct > threshold:
            return "CONFIRMED"
        if delta_price_pct < 0 and oi_delta_pct < -threshold:
            return "VETO"
    return "NEUTRAL"
