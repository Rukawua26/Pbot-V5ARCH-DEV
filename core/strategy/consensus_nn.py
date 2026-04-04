from typing import Dict, Tuple, Any, Optional
import os
import pickle
import numpy as np
import logging

try:
    from sklearn.neural_network import MLPClassifier
    from sklearn.preprocessing import StandardScaler

    SKLEARN_AVAILABLE = True
except ImportError:
    SKLEARN_AVAILABLE = False

logger = logging.getLogger("SniperAI")


class AgentConsensusNN:
    """
    [RED NEURONAL DE CONSENSO v118]
    Modelo de consenso neuronal con compatibilidad legacy.
    Input: vector de votos de agentes (0-100)
    Output: Probabilidad de éxito (0-1)
    """

    AGENT_NAMES = ["MT", "SR", "LB", "V", "J", "G", "C", "S"]

    def __init__(self, model_path: str = "v118_1H_consensus.pkl"):
        self.model: Optional[Any] = None
        if SKLEARN_AVAILABLE:
            self.scaler: Optional[Any] = StandardScaler()
        else:
            self.scaler = None
        self.is_trained: bool = False
        self.model_path: str = model_path
        self._debug_count = 0  # [FIX v118.1] Contador para integridad matemática
        self.load()

    def load(self) -> None:
        """Carga el modelo entrenado si existe."""
        if os.path.exists(self.model_path):
            try:
                with open(self.model_path, "rb") as f:
                    data = pickle.load(f)
                    self.model = data["model"]
                    self.scaler = data["scaler"]
                    self.is_trained = True
                    logger.info(
                        f"✅ Neural Consensus 1H cargado: {data.get('n_samples', 0)} muestras"
                    )
            except Exception as e:
                logger.warning(f"⚠️ Error cargando Neural Consensus: {e}")
                self.is_trained = False

    def save(self, n_samples: int) -> None:
        """Guarda el modelo entrenado."""
        if not self.is_trained or self.model is None:
            return
        try:
            with open(self.model_path, "wb") as f:
                pickle.dump(
                    {
                        "model": self.model,
                        "scaler": self.scaler,
                        "n_samples": n_samples,
                    },
                    f,
                )
            logger.info(f"✅ Neural Consensus 1H guardado ({n_samples} muestras)")
        except Exception as e:
            logger.error(f"⚠️ Error guardando Neural Consensus: {e}")

    def prepare_features(self, votes_dict: Dict[str, float]) -> np.ndarray:
        """Convierte el diccionario de votos a vector de features (8D)."""
        features = [votes_dict.get(agent, 50.0) for agent in self.AGENT_NAMES]
        return np.array(features).reshape(1, -1)

    def predict(self, votes_dict: Dict[str, float]) -> Tuple[float, float]:
        """
        Predice la probabilidad de éxito dado los votos de los 8 agentes.
        Retorna: (probabilidad, confianza)
        """
        if (
            not self.is_trained
            or self.model is None
            or self.scaler is None
            or not SKLEARN_AVAILABLE
        ):
            return 0.5, 0.0

        try:
            X = self.prepare_features(votes_dict)

            # [FIX v118.1] Verificación de Integridad Matemática
            if self._debug_count < 5:
                self._debug_count += 1
                X_scaled = self.scaler.transform(X)
                logger.debug(f"🧬 [MATH-FIX] Ciclo {self._debug_count}")
                logger.debug(f"   > Raw: {X[0].tolist()}")
                logger.debug(f"   > Scaled: {X_scaled[0].tolist()}")

                # Validación de rango Z-score
                out_of_range = np.sum((X_scaled[0] < -3) | (X_scaled[0] > 3))
                if out_of_range > 3:
                    logger.critical(
                        "🚨 [ABORT] Integridad Matemática Comprometida: Escalado fuera de rango [-3, 3]."
                    )
                    # No abortamos el proceso entero para evitar crash del bot, pero invalidamos predicción
                    return 0.5, 0.0
            else:
                X_scaled = self.scaler.transform(X)

            prob = self.model.predict_proba(X_scaled)[0][1]
            return float(prob), float(abs(prob - 0.5) * 2)
        except Exception as e:
            # logger.error(f"⚠️ Error en predicción de consenso (V118.1): {e}")
            return 0.5, 0.0

    def train(self, X_train: np.ndarray, y_train: np.ndarray) -> bool:
        """Entrena la red neuronal v118 con 8 entradas."""
        if not SKLEARN_AVAILABLE:
            logger.warning("⚠️ Sklearn no disponible")
            return False

        try:
            X_scaled = self.scaler.fit_transform(X_train)

            self.model = MLPClassifier(
                hidden_layer_sizes=(16, 8),  # REDUCIDO: Evitar overfitting
                activation="tanh",  # MEJOR PARA OSCILACIÓN -1 A 1
                solver="adam",
                alpha=0.01,  # MAYOR REGULARIZACIÓN
                learning_rate="adaptive",
                max_iter=500,
                early_stopping=True,
                validation_fraction=0.15,
                n_iter_no_change=25,
                random_state=42,
                verbose=False,
            )

            self.model.fit(X_scaled, y_train)
            self.is_trained = True

            train_score = self.model.score(X_scaled, y_train)
            logger.info(
                f"✅ Neural Consensus 1H Entrenada - Accuracy: {train_score:.2%}"
            )
            return True
        except Exception as e:
            logger.error(f"❌ Error entrenando Neural Consensus v118: {e}")
            return False
