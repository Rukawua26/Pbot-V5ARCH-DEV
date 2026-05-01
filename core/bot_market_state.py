import threading
import time

import pandas as pd

from config import Config
from core.strategy.regime_hmm import DynamicHMMRegime


hmm_filter = DynamicHMMRegime(
    n_states=3,
    lookback_candles=int(getattr(Config, "HMM_LOOKBACK_CANDLES", 336)),
)
_last_hmm_retrain_ts = 0.0
_hmm_retrain_lock = threading.Lock()
_hmm_retrain_in_progress = False


def _get_cached_btc_1h(bot):
    data_service = getattr(bot, "data_service", None)
    data_cache = getattr(data_service, "data_cache", None)
    if not isinstance(data_cache, dict):
        return None

    cached = data_cache.get("BTC/USDT_1h")
    if cached is None or getattr(cached, "empty", True):
        return None
    if len(cached) < int(getattr(Config, "MIN_CANDLE_HISTORY", 200)):
        return None
    return cached


def warmup_hmm_regime(bot) -> bool:
    """Entrena el HMM durante bootstrap para evitar ceguera heurística inicial."""
    if not bool(getattr(Config, "HMM_REGIME_ENABLED", True)):
        return False
    if hmm_filter.is_ready:
        return True

    try:
        data_service = getattr(bot, "data_service", None)
        exchange = getattr(data_service, "exchange", None)
        if data_service is None or exchange is None:
            bot.log("⚠️ HMM warmup omitido: data_service no disponible")
            return False

        limit = int(getattr(Config, "HMM_BOOTSTRAP_CANDLES", 1000))
        ohlcv = exchange.fetch_ohlcv("BTC/USDT", "1h", limit=limit)
        if hasattr(data_service, "_track_api_weight"):
            data_service._track_api_weight("fetch_ohlcv", 1, "market")
        if not ohlcv:
            bot.log("⚠️ HMM warmup omitido: BTC/USDT sin velas")
            return False

        columns = ["time", "open", "high", "low", "close", "volume"]
        btc_data = pd.DataFrame(ohlcv, columns=columns)
        if hasattr(data_service, "_clean_df"):
            btc_data = data_service._clean_df(btc_data)
        if len(btc_data) < int(getattr(Config, "HMM_LOOKBACK_CANDLES", 336)):
            bot.log(f"⚠️ HMM warmup omitido: solo {len(btc_data)} velas BTC/USDT")
            return False

        cache_key = "BTC/USDT_1h"
        if hasattr(data_service, "data_cache"):
            data_service.data_cache[cache_key] = btc_data.tail(limit).copy()
        if hasattr(data_service, "last_ohlcv_fetch"):
            data_service.last_ohlcv_fetch[cache_key] = time.time()

        global _last_hmm_retrain_ts
        if hmm_filter.dynamic_retrain(btc_data):
            _last_hmm_retrain_ts = time.monotonic()
            bot.log(f"✅ HMM regime warmup listo con {len(btc_data)} velas BTC/USDT")
            return True

        reason = getattr(hmm_filter, "last_error", "desconocido")
        bot.log(f"⚠️ HMM warmup fallback: {reason}")
        return False
    except Exception as error:
        bot.log(f"⚠️ HMM warmup fallback: {error}")
        return False


def _schedule_hmm_retrain(bot, btc_data, now) -> bool:
    global _hmm_retrain_in_progress

    with _hmm_retrain_lock:
        if _hmm_retrain_in_progress:
            return False
        _hmm_retrain_in_progress = True

    retrain_data = btc_data.copy()

    def _run_retrain():
        global _last_hmm_retrain_ts, _hmm_retrain_in_progress
        try:
            if hmm_filter.dynamic_retrain(retrain_data):
                _last_hmm_retrain_ts = now
            else:
                reason = getattr(hmm_filter, "last_error", "desconocido")
                bot.log(f"⚠️ HMM regime fallback: {reason}")
        finally:
            with _hmm_retrain_lock:
                _hmm_retrain_in_progress = False

    threading.Thread(
        target=_run_retrain,
        daemon=True,
        name="hmm-regime-retrain",
    ).start()
    return True


def _detect_market_regime_heuristic(bot, btc_data=None) -> str:
    try:
        if not hasattr(bot, "market_btc_price") or bot.market_btc_price == 0:
            bot.market_regime_source = "HEURISTIC"
            bot.market_regime_confidence = None
            return "RANGE"

        if btc_data is None:
            btc_data = bot.data_service.fetch_and_update_data("BTC/USDT", "1h")
        if btc_data is None or len(btc_data) < 200:
            bot.market_regime_source = "HEURISTIC"
            bot.market_regime_confidence = None
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
            bot.market_regime_source = "HEURISTIC"
            bot.market_regime_confidence = None
            return "RANGE"

        adx = adx_values.iloc[-1]
        btc_price = bot.market_btc_price

        if adx < float(getattr(Config, "ADX_TREND_THRESHOLD", 20)):
            bot.market_regime_source = "HEURISTIC"
            bot.market_regime_confidence = None
            return "RANGE"
        if btc_price > ema_200:
            bot.market_regime_source = "HEURISTIC"
            bot.market_regime_confidence = None
            return "BULL_TREND"
        bot.market_regime_source = "HEURISTIC"
        bot.market_regime_confidence = None
        return "BEAR_TREND"
    except Exception as error:
        bot.log(f"⚠️ Error detecting market regime: {error}")
        bot.market_regime_source = "HEURISTIC_ERROR"
        bot.market_regime_confidence = None
        return "RANGE"


def detect_market_regime(bot) -> str:
    if not bool(getattr(Config, "HMM_REGIME_ENABLED", True)):
        regime = _detect_market_regime_heuristic(bot)
        bot.market_regime = regime
        return regime

    try:
        btc_data = _get_cached_btc_1h(bot)
        if btc_data is None:
            btc_data = bot.data_service.fetch_and_update_data("BTC/USDT", "1h")
        if btc_data is None or len(btc_data) < 200:
            regime = _detect_market_regime_heuristic(bot, btc_data)
            bot.market_regime = regime
            return regime

        global _last_hmm_retrain_ts
        now = time.monotonic()
        interval = float(getattr(Config, "HMM_RETRAIN_INTERVAL_SECONDS", 4 * 60 * 60))
        if not hmm_filter.is_ready or now - _last_hmm_retrain_ts >= interval:
            scheduled = _schedule_hmm_retrain(bot, btc_data, now)
            if not hmm_filter.is_ready:
                if scheduled:
                    bot.log("⚠️ HMM regime fallback: reentrenamiento en progreso")
                regime = _detect_market_regime_heuristic(bot, btc_data)
                bot.market_regime = regime
                return regime

        regime, confidence = hmm_filter.predict_regime(btc_data)
        min_confidence = float(getattr(Config, "HMM_MIN_CONFIDENCE", 0.55))
        if regime == "UNKNOWN" or confidence < min_confidence:
            bot.log(
                f"⚠️ HMM regime fallback: regime={regime} confidence={confidence:.2f}"
            )
            regime = _detect_market_regime_heuristic(bot, btc_data)
            bot.market_regime = regime
            return regime

        bot.market_regime_confidence = confidence
        bot.market_regime_source = "HMM"
        bot.market_regime = regime
        return regime
    except Exception as error:
        bot.log(f"⚠️ Error detecting HMM market regime: {error}")
        regime = _detect_market_regime_heuristic(bot)
        bot.market_regime = regime
        return regime
