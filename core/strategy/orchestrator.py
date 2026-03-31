import logging
import numpy as np
import pandas as pd
from collections import deque
from typing import Dict, Any, List, Optional, Tuple

from core.strategy.agents.mt_agent import MTAgent
from core.strategy.agents.sr_agent import SRAgent
from core.strategy.agents.ghost_agent import GhostAgent
from core.strategy.consensus_nn import AgentConsensusNN
from learning import shadow_logger
from core.config.hyperopt_loader import HyperoptConfigLoader

logger = logging.getLogger("SniperAI")


class StrategyOrchestrator:
    """
    [ESTRATEGIA ORQUESTADA v118-TRINITY]
    Coordina la Trinidad de agentes:
    - MT (Tendencia)
    - SR (Estructura)
    - G (IA)
    Aplica pesos adaptativos y veto de correlación.
    """

    def __init__(self):
        self.adx_threshold = float(
            HyperoptConfigLoader.get_param("adx_threshold", 25.0)
        )
        self.agents = {
            "MT": MTAgent(),
            "SR": SRAgent(),
            "G": GhostAgent(),
        }
        self.consensus_nn = AgentConsensusNN()
        self._base_weights = self._initialize_base_weights()
        # Historial para cálculo de correlación de Pearson (Ventana dinámica: 7 votos v116.1)
        self.vote_history = {name: deque(maxlen=7) for name in self.agents}

    def _initialize_base_weights(self) -> Dict[str, Dict[str, float]]:
        """Pesos base para la Trinidad (MT/SR/G)."""
        return {
            "CHAOS": {
                "MT": 0.25,
                "SR": 0.35,
                "G": 0.40,
            },
            "TREND": {
                "MT": 0.45,
                "SR": 0.20,
                "G": 0.35,
            },
            "CALM": {
                "MT": 0.20,
                "SR": 0.45,
                "G": 0.35,
            },
        }

    def _apply_correlation_veto(
        self,
        weights: Dict[str, float],
        votes: Dict[str, float],
        agent_performances: Optional[Dict[str, float]] = None,
    ) -> Dict[str, float]:
        """
        Hard-Veto de Correlación v116.1: Si la correlación entre dos agentes
        supera 0.90 en una ventana de 7 votos, el de menor WR histórico queda en peso 0.
        """
        # Actualizar historial
        for name, vote in votes.items():
            self.vote_history[name].append(vote)

        # Si no hay suficiente historial, no aplicar veto (v116.1: ventana 7)
        if len(list(self.vote_history.values())[0]) < 7:
            return weights

        adjusted_weights = weights.copy()
        agent_names = list(self.agents.keys())

        # Calcular correlación cruzada
        try:
            for i in range(len(agent_names)):
                for j in range(i + 1, len(agent_names)):
                    a1, a2 = agent_names[i], agent_names[j]
                    h1, h2 = list(self.vote_history[a1]), list(self.vote_history[a2])

                    if len(h1) >= 7 and len(h2) >= 7:
                        corr = np.corrcoef(h1, h2)[0, 1]
                        if not np.isnan(corr) and abs(corr) > 0.90:
                            # [FIX v116.1] EXCLUSIÓN: Peso 0 al de menor rendimiento
                            perf1 = (
                                agent_performances.get(a1, 100.0)
                                if agent_performances
                                else 100.0
                            )
                            perf2 = (
                                agent_performances.get(a2, 100.0)
                                if agent_performances
                                else 100.0
                            )

                            if perf1 < perf2:
                                adjusted_weights[a1] = 0.0
                            else:
                                adjusted_weights[a2] = 0.0
        except Exception as e:
            logger.error(f"Error en Veto de Correlación: {e}")

        return adjusted_weights

    def get_adaptive_weights(
        self,
        regime: str,
        agent_performances: Optional[Dict[str, float]] = None,
        adx: Optional[float] = None,
    ) -> Dict[str, float]:
        """Calcula los pesos finales basados en el régimen y rendimiento."""
        # Mapear regímenes extendidos a los 3 básicos si es necesario
        regime_map = {
            "BULL_TREND": "TREND",
            "BEAR_TREND": "TREND",
            "CHAOS": "CHAOS",
            "CALM": "CALM",
        }
        target_regime = regime_map.get(regime, "CALM")

        # Árbitro de régimen por ADX optimizado
        adx_value = float(adx) if adx is not None else None
        if adx_value is not None:
            if adx_value > self.adx_threshold:
                target_regime = "TREND"
            elif adx_value < 20.0:
                target_regime = "CALM"

        weights = self._base_weights.get(
            target_regime, self._base_weights["CALM"]
        ).copy()

        # Ajuste explícito MT/SR según árbitro ADX
        if adx_value is not None:
            if adx_value > self.adx_threshold:
                weights["MT"] = 0.35
                weights["SR"] = 0.02
            elif adx_value < 20.0:
                weights["MT"] = 0.05
                weights["SR"] = 0.30

            total = sum(weights.values())
            if total > 0:
                for k in weights:
                    weights[k] = weights[k] / total

        if not agent_performances:
            return weights

        perf_factor: Dict[str, float] = {}
        for agent in weights:
            perf = agent_performances.get(agent, 100.0)
            if perf > 120:
                perf_factor[agent] = 1.3
            elif perf < 60:
                perf_factor[agent] = 0.1
            else:
                perf_factor[agent] = 1.0

        total_adjusted = sum(weights[a] * perf_factor.get(a, 1.0) for a in weights)
        if total_adjusted > 0:
            for agent in weights:
                weights[agent] = (
                    weights[agent] * perf_factor.get(agent, 1.0)
                ) / total_adjusted

        return weights

    def calculate_consensus(
        self,
        context: Dict[str, Any],
        agent_performances: Optional[Dict[str, float]] = None,
    ) -> Tuple[float, Dict[str, float]]:
        """Ejecuta los 3 agentes, aplica pesos y consenso neuronal."""
        votes: Dict[str, float] = {}

        # Ejecución de agentes
        for name, agent in self.agents.items():
            try:
                votes[name] = agent.vote(context)
            except Exception as e:
                logger.error(f"Error en agente {name}: {e}")
                votes[name] = 50.0

        # Pesos adaptativos por régimen
        regime = context.get("regime", "CALM")
        adx = context.get("adx")
        weights = self.get_adaptive_weights(regime, agent_performances, adx)

        # Telemetría Asíncrona (Shadow Logging v116)
        shadow_logger.log(
            {
                "type": "AGENT_VOTES",
                "data": {
                    "symbol": context.get("symbol", "UNKNOWN"),
                    "votes": votes,
                    "regime": regime,
                },
            }
        )

        # Telemetria granular para auditoria de correlacion (MT/SR/G)
        ts_now = pd.Timestamp.now().isoformat()
        for agent_name, vote_value in votes.items():
            shadow_logger.log(
                {
                    "type": "AGENT_VOTE",
                    "data": {"agent": agent_name, "vote": vote_value, "ts": ts_now},
                }
            )
        # APLICAR VETO DE CORRELACIÓN (v116.1)
        final_weights = self._apply_correlation_veto(weights, votes, agent_performances)

        # Media pesada con pesos de veto
        p_final = sum(votes[a] * final_weights[a] for a in votes)
        p_final = max(0.0, min(100.0, p_final))

        # Consenso Neuronal
        nn_prob, nn_conf = self.consensus_nn.predict(votes)
        if nn_conf > 0.4:  # Aumentamos influencia si hay confianza
            p_final = (p_final * 0.6) + (nn_prob * 100 * 0.4)

        return float(p_final), votes
