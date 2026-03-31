import pandas as pd
import pandas_ta as ta
import numpy as np
from datetime import datetime
from typing import Dict, Any, Optional
import logging
from config import Config

logger = logging.getLogger("SniperAI")

class StrategyUtils:
    """
    Utilidades estáticas para el motor de estrategia.
    Maneja indicadores, preprocesamiento y detección de estructuras.
    """
    _ob_cache: Dict[str, str] = {}

    @staticmethod
    def calculate_z_score(df: pd.DataFrame, window: int = 20) -> float:
        """Calcula el Z-Score de la volatilidad para detectar irracionalidad."""
        if df is None or len(df) < window:
            return 0.0
        try:
            returns = df["close"].pct_change().dropna()
            if len(returns) < window:
                return 0.0
                
            rolling_mean = returns.rolling(window=window).mean()
            rolling_std = returns.rolling(window=window).std()
            
            if rolling_std.iloc[-1] == 0:
                return 0.0
                
            z = (returns.iloc[-1] - rolling_mean.iloc[-1]) / rolling_std.iloc[-1]
            return float(z)
        except Exception:
            return 0.0

    @staticmethod
    def get_market_context(adx: float, rsi: float) -> str:
        """Define el estado del mercado para ajustar pesos de agentes."""
        if adx > 25:
            return "TREND"
        elif rsi < 30 or rsi > 70:
            return "VOLATILE"
        return "CALM"

    @staticmethod
    def detect_market_regime(atr_pct: float, adx: float, trend_direction: str = "UP") -> str:
        """Detecta el régimen actual del mercado (BULL_TREND, BEAR_TREND, CHAOS, CALM)."""
        if adx > 25:
            return "BULL_TREND" if trend_direction == "UP" else "BEAR_TREND"
        if atr_pct > 0.035:
            return "CHAOS"
        return "CALM"

    @staticmethod
    def detect_order_block(df: pd.DataFrame, symbol: str) -> str:
        """Detecta bloques de órdenes con confirmación de volumen y mitigación."""
        if df is None or len(df) < 30:
            return "⚪"

        last_ts = str(df["time"].iloc[-1])
        cache_key = f"{symbol}_{last_ts}"
        if cache_key in StrategyUtils._ob_cache:
            return StrategyUtils._ob_cache[cache_key]

        last_20 = df.tail(20).copy()
        avg_body = abs(last_20["close"] - last_20["open"]).mean()
        avg_volume = last_20["volume"].mean() if "volume" in last_20.columns else 1.0

        result = "⚪"
        for i in range(len(df) - 2, len(df) - 22, -1):
            candle = df.iloc[i]
            body = abs(candle["close"] - candle["open"])
            vol = candle["volume"] if "volume" in df.columns else 1.0

            if float(body) > (float(avg_body) * 1.6) and float(vol) > (float(avg_volume) * 1.2):
                is_bullish_ob = candle["close"] < candle["open"]
                is_bearish_ob = candle["close"] > candle["open"]
                current_price = df["close"].iloc[-1]

                if is_bullish_ob:
                    ob_low, ob_high = candle["low"], candle["high"]
                    if ob_low * 0.999 <= current_price <= ob_high * 1.002:
                        since_ob = df.iloc[i + 1 : -1]
                        if not since_ob.empty and (since_ob["close"] < ob_low).any():
                            continue
                        result = "🟢"
                        break
                elif is_bearish_ob:
                    ob_low, ob_high = candle["low"], candle["high"]
                    if ob_low * 0.998 <= current_price <= ob_high * 1.001:
                        since_ob = df.iloc[i + 1 : -1]
                        if not since_ob.empty and (since_ob["close"] > ob_high).any():
                            continue
                        result = "🔴"
                        break

        if len(StrategyUtils._ob_cache) > 100:
            StrategyUtils._ob_cache.clear()
        StrategyUtils._ob_cache[cache_key] = result
        return result

    @staticmethod
    def preprocess_data(df: pd.DataFrame, mode: str = "full") -> Optional[pd.DataFrame]:
        """Punto único de cálculo de indicadores con Data Gate."""
        if df is None or len(df) < 50:
            return None

        try:
            if mode == "full":
                if "ema" not in df.columns: df.ta.ema(length=50, append=True)
                if "rsi" not in df.columns: df.ta.rsi(length=14, append=True)
                if "atr" not in df.columns: df.ta.atr(length=14, append=True)
                if "adx" not in df.columns: df.ta.adx(length=14, append=True)
                if "bb_lower" not in df.columns: df.ta.bbands(length=20, append=True)
                if "stoch_k" not in df.columns: df.ta.stoch(k=14, d=3, append=True)
                if "volume_ma" not in df.columns and "volume" in df.columns:
                    df["volume_ma"] = df["volume"].rolling(window=20).mean()
                if "ema_200" not in df.columns: df.ta.ema(length=200, append=True)

                rename_map = {
                    "EMA_50": "ema", "RSI_14": "rsi", "ATRr_14": "atr",
                    "ADX_14": "adx", "BBL_20_2.0": "bb_lower", "BBU_20_2.0": "bb_upper",
                    "STOCHk_14_3_3": "stoch_k", "STOCHd_14_3_3": "stoch_d", "EMA_200": "ema_200",
                }
                df.rename(columns={k: v for k, v in rename_map.items() if k in df.columns}, inplace=True)
            else:
                if "ema" not in df.columns:
                    df.ta.ema(length=50, append=True)
                    df.rename(columns={"EMA_50": "ema"}, inplace=True)
                if "ema_200" not in df.columns and len(df) >= 200:
                    df.ta.ema(length=200, append=True)
                    df.rename(columns={"EMA_200": "ema_200"}, inplace=True)

            if mode == "full":
                if "rsi" not in df.columns or len(df) == 0: return None
                last_rsi = df["rsi"].iloc[-1]
                if last_rsi == 0 or pd.isna(last_rsi): return None
            
            df.fillna(0, inplace=True)
            return df
        except Exception as e:
            logger.error(f"❌ Error en preprocess_data (mode={mode}): {e}", exc_info=True)
            return None
