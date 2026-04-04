from typing import Dict, Any
import numpy as np
import pandas as pd
from core.strategy.base_agent import BaseAgent
from core.strategy.utils import StrategyUtils
from core.config.hyperopt_loader import HyperoptConfigLoader


class SRAgent(BaseAgent):
    """
    [SUPER-AGENTE STATISTICAL-REVERSION (SR)]
    Fusiona F (Fatigue) y E (Structure).
    Utiliza Z-Score y Entropía para identificar puntos de sobreextensión.
    Solo emite señal si el estiramiento es > 2.5 desviaciones estándar.
    """

    def __init__(self, weight: float = 1.0):
        super().__init__(name="SR", weight=weight)
        z_loaded = float(HyperoptConfigLoader.get_param("z_score_threshold", 2.0))
        # [UNLOCK] Limitar umbral para evitar neutralidad excesiva.
        self.z_score_threshold = min(2.0, max(1.4, z_loaded))
        self.entropy_bins = int(HyperoptConfigLoader.get_param("entropy_bins", 10))

    def _calculate_entropy(self, series: Any, window: int = 20) -> float:
        """Calcula la Entropía de Shannon de los retornos."""
        if series is None or not isinstance(series, pd.Series) or len(series) < window:
            return 0.0
        try:
            returns = series.pct_change().dropna().tail(window)
            if len(returns) == 0:
                return 0.0
            bins = max(2, int(self.entropy_bins))
            counts, _ = np.histogram(returns, bins=bins)
            probs = counts / (sum(counts) if sum(counts) > 0 else 1)
            probs = probs[probs > 0]
            if len(probs) == 0:
                return 0.0
            return -np.sum(probs * np.log2(probs))
        except Exception:
            return 0.0

    def vote(self, context: Dict[str, Any]) -> float:
        df = context.get("df")
        z_score = context.get("z_score", 0.0)

        # [AUDIT FIX V118-L4] Z-Score Dinámico vía ATR para evitar Model Drift
        z_score_dinamico = z_score
        if df is not None and len(df) >= 20:
            try:
                # Calculo de TR (True Range) y ATR
                high = df["high"]
                low = df["low"]
                close_prev = df["close"].shift(1)

                tr1 = high - low
                tr2 = (high - close_prev).abs()
                tr3 = (low - close_prev).abs()

                tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
                atr = tr.rolling(14).mean().iloc[-1]

                sma = df["close"].rolling(20).mean().iloc[-1]
                price = df["close"].iloc[-1]

                # Z-Score ajustado suavizado por volatilidad real
                z_score_dinamico = (price - sma) / (atr * 1.5) if atr > 0 else z_score
            except Exception:
                z_score_dinamico = z_score

        # Condición: Solo dispara si Z-Score Dinámico supera umbral optimizado
        if abs(z_score_dinamico) < self.z_score_threshold:
            return 50.0

        entropy = self._calculate_entropy(df["close"]) if df is not None else 0.0

        score = 50.0
        # Reversión estadística con umbral optimizado
        if z_score_dinamico > self.z_score_threshold:
            score = 20.0 + (entropy * 5)  # Voto fuerte a SELL
        elif z_score_dinamico < -self.z_score_threshold:
            score = 80.0 - (entropy * 5)  # Voto fuerte a BUY

        return min(max(score, 0), 100)
