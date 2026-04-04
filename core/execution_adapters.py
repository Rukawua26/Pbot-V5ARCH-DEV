import random
import time
import uuid
from typing import Optional


class ShadowExecutionAdapter:
    """Adapter de ejecución shadow-live con latencia/rechazo/fill parcial simulados."""

    def __init__(
        self,
        live_execution,
        *,
        min_latency_ms: int = 200,
        max_latency_ms: int = 500,
        reject_rate: float = 0.03,
        partial_fill_rate: float = 0.25,
        min_partial_ratio: float = 0.3,
        random_source: Optional[random.Random] = None,
        sleep_fn=time.sleep,
    ):
        self._live = live_execution
        self.exchange = live_execution.exchange
        self.logger = getattr(live_execution, "logger", None)
        self.last_hard_sl_error = ""
        self._min_latency_ms = max(0, int(min_latency_ms))
        self._max_latency_ms = max(self._min_latency_ms, int(max_latency_ms))
        self._reject_rate = max(0.0, min(1.0, float(reject_rate)))
        self._partial_fill_rate = max(0.0, min(1.0, float(partial_fill_rate)))
        self._min_partial_ratio = max(0.05, min(0.95, float(min_partial_ratio)))
        self._rng = random_source or random.Random()
        self._sleep = sleep_fn

    def __getattr__(self, name):
        return getattr(self._live, name)

    def _inject_latency(self):
        latency_ms = self._rng.randint(self._min_latency_ms, self._max_latency_ms)
        self._sleep(latency_ms / 1000.0)

    def _reject(self) -> bool:
        return self._rng.random() < self._reject_rate

    def _mock_order(
        self,
        *,
        symbol: str,
        side: str,
        amount: float,
        price: float,
        client_order_id: Optional[str],
        force_partial: bool = False,
    ):
        partial = force_partial or (self._rng.random() < self._partial_fill_rate)
        if partial:
            ratio = self._rng.uniform(self._min_partial_ratio, 0.95)
            filled = round(max(0.0, amount * ratio), 12)
            status = "open"
        else:
            filled = round(max(0.0, amount), 12)
            status = "closed"
        return {
            "id": f"shadow-{uuid.uuid4().hex[:16]}",
            "symbol": symbol,
            "side": side.lower(),
            "type": "limit",
            "status": status,
            "price": price,
            "average": price,
            "amount": amount,
            "filled": filled,
            "remaining": max(0.0, amount - filled),
            "clientOrderId": client_order_id,
            "info": {
                "shadow": True,
                "latency_profile": [self._min_latency_ms, self._max_latency_ms],
            },
        }

    def create_precision_order(
        self,
        symbol: str,
        side: str,
        amount: float,
        price: float,
        slippage_pct: float = 0.1,
        client_order_id: Optional[str] = None,
    ):
        self._inject_latency()
        if self._reject():
            if self.logger:
                self.logger.warning(
                    f"⚠️ SHADOW EXEC reject {symbol} {side} clientId={client_order_id or 'N/A'}"
                )
            return None
        return self._mock_order(
            symbol=symbol,
            side=side,
            amount=amount,
            price=price,
            client_order_id=client_order_id,
        )

    def place_hard_sl(
        self,
        symbol: str,
        side: str,
        amount: float,
        stop_price: float,
        client_order_id: Optional[str] = None,
    ):
        self._inject_latency()
        ticker = self._live.fetch_ticker(symbol)
        market_price = float(ticker.get("last") or 0.0)
        is_buy_trade = str(side).lower() == "buy"
        invalid_trigger = (is_buy_trade and stop_price >= market_price) or (
            (not is_buy_trade) and stop_price <= market_price
        )
        if invalid_trigger:
            self.last_hard_sl_error = "Order would trigger immediately. (-2021)"
            return None
        self.last_hard_sl_error = ""
        return {
            "id": f"shadow-sl-{uuid.uuid4().hex[:14]}",
            "symbol": symbol,
            "type": "STOP_MARKET",
            "side": "sell" if is_buy_trade else "buy",
            "status": "open",
            "amount": amount,
            "stopPrice": stop_price,
            "clientOrderId": client_order_id,
            "info": {"shadow": True, "reduceOnly": True},
        }

    def close_position(self, symbol: str, side: str, amount: float):
        self._inject_latency()
        if self._reject():
            raise RuntimeError("shadow close rejected")
        price = float((self._live.fetch_ticker(symbol) or {}).get("last") or 0.0)
        return {
            "id": f"shadow-close-{uuid.uuid4().hex[:14]}",
            "symbol": symbol,
            "type": "market",
            "side": "sell" if str(side).lower() == "buy" else "buy",
            "status": "closed",
            "amount": amount,
            "filled": amount,
            "average": price,
            "info": {"shadow": True, "reduceOnly": True},
        }

    def close_due_to_degradation(self, symbol: str, side: str, amount: float):
        return self.close_position(symbol, side, amount)

    def cancel_order(self, symbol: str, order_id: str):
        self._inject_latency()
        if self._reject():
            raise RuntimeError("shadow cancel rejected")
        return {
            "id": order_id,
            "symbol": symbol,
            "status": "canceled",
            "info": {"shadow": True},
        }


def build_execution_gateway(config, execution_service_cls):
    execution = execution_service_cls(config.BINANCE_API_KEY, config.BINANCE_API_SECRET)

    if bool(getattr(config, "USE_TESTNET", False)):
        try:
            execution.exchange.set_sandbox_mode(True)
        except Exception as error:
            if getattr(execution, "logger", None):
                execution.logger.warning(f"⚠️ No se pudo activar sandbox mode: {error}")

    backend = str(getattr(config, "EXECUTION_BACKEND", "live") or "live").lower()
    if backend == "shadow_live":
        return ShadowExecutionAdapter(
            execution,
            min_latency_ms=int(getattr(config, "SHADOW_SIM_LATENCY_MIN_MS", 200)),
            max_latency_ms=int(getattr(config, "SHADOW_SIM_LATENCY_MAX_MS", 500)),
            reject_rate=float(getattr(config, "SHADOW_SIM_REJECT_RATE", 0.03)),
            partial_fill_rate=float(
                getattr(config, "SHADOW_SIM_PARTIAL_FILL_RATE", 0.25)
            ),
            min_partial_ratio=float(
                getattr(config, "SHADOW_SIM_MIN_PARTIAL_RATIO", 0.3)
            ),
        )
    return execution
