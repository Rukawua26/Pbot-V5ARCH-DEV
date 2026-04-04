import ccxt
import time
import logging
from typing import Optional
from config import Config
from core.types import CCXTOrder, CCXTBalanceResponse


class ExecutionService:
    """
    [V118-ULTIMATE] EXECUTION SERVICE
    =================================
    Encapsula toda la comunicación con Binance Futures.
    Implementa el "Liquidity Guard" mediante órdenes LIMIT IOC.
    """

    def __init__(self, api_key, api_secret):
        self.exchange = ccxt.binance(
            {
                "apiKey": api_key,
                "secret": api_secret,
                "enableRateLimit": True,
                "options": {"defaultType": "future"},
            }
        )
        self.logger = logging.getLogger("Execution")
        self.weight_tracker = None
        self.last_hard_sl_error = ""

    def set_weight_tracker(self, tracker):
        self.weight_tracker = tracker

    def _track_api_weight(self, endpoint: str, weight: int, category: str):
        if self.weight_tracker:
            self.weight_tracker.track(endpoint, weight, category)

    def has_markets_loaded(self) -> bool:
        try:
            return bool(getattr(self.exchange, "markets", None))
        except Exception:
            return False

    def load_markets(self):
        markets = self.exchange.load_markets()
        self._track_api_weight("load_markets", 10, "essential")
        return markets

    def fetch_balance(self):
        balance = self.exchange.fetch_balance()
        self._track_api_weight("fetch_balance", 5, "account")
        return balance

    def fetch_position_mode(self, symbol: Optional[str] = None):
        if symbol:
            mode = self.exchange.fetch_position_mode(symbol=symbol)
        else:
            mode = self.exchange.fetch_position_mode()
        self._track_api_weight("fetch_position_mode", 1, "account")
        return mode

    def get_position_side_dual(self):
        mode = self.exchange.fapiPrivateGetPositionSideDual()
        self._track_api_weight("fapiPrivateGetPositionSideDual", 1, "account")
        return mode

    def fetch_tickers(self, symbols=None, params=None):
        if symbols is None:
            tickers = self.exchange.fetch_tickers(params=params or {"type": "future"})
        else:
            tickers = self.exchange.fetch_tickers(symbols, params=params or {})
        self._track_api_weight("fetch_tickers", 40, "market")
        return tickers

    def fetch_ticker(self, symbol: str):
        ticker = self.exchange.fetch_ticker(symbol)
        self._track_api_weight("fetch_ticker", 1, "market")
        return ticker

    def fetch_positions(self):
        positions = self.exchange.fetch_positions()
        self._track_api_weight("fetch_positions", 5, "account")
        return positions

    def fetch_open_orders(self, symbol: Optional[str] = None):
        if symbol:
            orders = self.exchange.fetch_open_orders(symbol)
        else:
            orders = self.exchange.fetch_open_orders()
        self._track_api_weight("fetch_open_orders", 5, "account")
        return orders

    def fetch_order_by_client_id(self, symbol: str, client_order_id: str):
        if not symbol or not client_order_id:
            return None
        try:
            params = {
                "symbol": self.exchange.market_id(symbol),
                "origClientOrderId": client_order_id,
            }
            order = self.exchange.fapiPrivateGetOrder(params)
            self._track_api_weight("fapiPrivateGetOrder", 1, "account")
            if isinstance(order, dict) and order:
                parsed = {
                    "id": order.get("orderId"),
                    "symbol": symbol,
                    "status": str(order.get("status", "")).lower(),
                    "clientOrderId": order.get("clientOrderId"),
                    "info": order,
                }
                return parsed
        except Exception as error:
            self.logger.warning(
                f"⚠️ No se pudo consultar orden por clientOrderId {symbol}/{client_order_id}: {error}"
            )
        return None

    def fetch_my_trades(self, symbol: str, limit: int = 2):
        trades = self.exchange.fetch_my_trades(symbol, limit=limit)
        self._track_api_weight("fetch_my_trades", 5, "account")
        return trades

    def cancel_order(self, symbol: str, order_id: str):
        if not symbol or not order_id:
            return None
        canceled = self.exchange.cancel_order(order_id, symbol)
        self._track_api_weight("cancel_order", 1, "trading")
        return canceled

    def fetch_all_prices(self):
        prices = self.exchange.fapiPublicGetTickerPrice()
        self._track_api_weight("fapiPublicGetTickerPrice", 1, "market")
        return prices

    def fetch_book_tickers(self):
        books = self.exchange.fapiPublicGetTickerBookTicker()
        self._track_api_weight("fapiPublicGetTickerBookTicker", 1, "market")
        return books

    def fetch_funding_rate(self, symbol: str):
        fr = self.exchange.fetch_funding_rate(symbol)
        self._track_api_weight("fetch_funding_rate", 1, "market")
        return fr

    def fetch_order_book(self, symbol: str, limit: int = 20):
        ob = self.exchange.fetch_order_book(symbol, limit=limit)
        self._track_api_weight("fetch_order_book", 1, "market")
        return ob

    def create_reduce_only_market_order(
        self, symbol: str, side: str, amount: float, params=None
    ):
        order = self.exchange.create_order(
            symbol,
            "MARKET",
            side.lower(),
            amount,
            None,
            params=(params or {"reduceOnly": True}),
        )
        self._track_api_weight("create_order", 1, "trading")
        return order

    def set_leverage(self, leverage, symbol):
        try:
            result = self.exchange.set_leverage(leverage, symbol)
            self._track_api_weight("set_leverage", 1, "trading")
            return result
        except Exception as e:
            self.logger.error(f"Error setting leverage for {symbol}: {e}")
            return None

    def create_precision_order(
        self,
        symbol: str,
        side: str,
        amount: float,
        price: float,
        slippage_pct: float = 0.1,
        client_order_id: Optional[str] = None,
    ) -> Optional[CCXTOrder]:
        """
        Ejecución quirúrgica: LIMIT IOC.
        Si no se llena al precio límite (con slippage), se cancela automáticamente.
        """
        try:
            # Calcular precio límite basado en slippage permitido
            limit_price = (
                price * (1 + (slippage_pct / 100))
                if side.lower() == "buy"
                else price * (1 - (slippage_pct / 100))
            )

            # Formatear precio para Binance
            limit_price_str = self.exchange.price_to_precision(symbol, limit_price)

            self.logger.info(
                f"🎯 Precio Base: {price} | Slippage: {slippage_pct}% | Límite IOC: {limit_price_str}"
            )

            params = {
                "timeInForce": "IOC",  # Immediate or Cancel
                "postOnly": False,
            }
            if client_order_id:
                params["newClientOrderId"] = client_order_id

            self.logger.info(
                f"🚀 Enviando LIMIT IOC {symbol} {side} @ {limit_price_str}"
            )

            order: CCXTOrder = self.exchange.create_order(
                symbol,
                type="limit",
                side=side.lower(),
                amount=amount,
                price=float(limit_price_str),
                params=params,
            )
            self._track_api_weight("create_order", 1, "trading")

            return order
        except Exception as e:
            self.logger.error(f"❌ Error en Ejecución Quirúrgica {symbol}: {e}")
            return None

    def get_balance(self) -> float:
        try:
            balance: CCXTBalanceResponse = self.exchange.fetch_balance()
            self._track_api_weight("fetch_balance", 5, "account")

            # [FIX] Prioridad a 'totalWalletBalance' nativo de Futuros para mayor precisión
            info = balance.get("info", {})
            total_wallet = info.get("totalWalletBalance")

            if total_wallet is not None:
                return float(total_wallet)

            # Fallback a lectura estándar de CCXT
            total = balance.get("total", {})
            return float(total.get("USDT", 0.0))
        except Exception as e:
            self.logger.error(f"Error fetching balance: {e}")
            return 0.0

    def place_hard_sl(
        self,
        symbol: str,
        side: str,
        amount: float,
        stop_price: float,
        client_order_id: Optional[str] = None,
    ) -> Optional[CCXTOrder]:
        """Coloca un STOP_MARKET real en Binance para seguridad extrema."""
        try:
            sl_side = "sell" if side.lower() == "buy" else "buy"
            params = {
                "stopPrice": self.exchange.price_to_precision(symbol, stop_price),
                "reduceOnly": True,
            }
            if client_order_id:
                params["newClientOrderId"] = client_order_id
            order = self.exchange.create_order(
                symbol, "STOP_MARKET", sl_side, amount, None, params
            )
            self._track_api_weight("create_order", 1, "trading")
            self.last_hard_sl_error = ""
            return order
        except Exception as e:
            self.last_hard_sl_error = str(e)
            self.logger.error(f"⚠️ Error colocando Hard SL {symbol}: {e}")
            return None

    def close_position(
        self, symbol: str, side: str, amount: float
    ) -> Optional[CCXTOrder]:
        """Cierra una posición abierta inmediatamente vía MARKET order."""
        try:
            exit_side = "sell" if side.lower() == "buy" else "buy"
            params = {"reduceOnly": True}

            # Cancelar órdenes pendientes antes de cerrar
            try:
                self.exchange.cancel_all_orders(symbol)
                self._track_api_weight("cancel_all_orders", 1, "trading")
            except Exception as error:
                self.logger.warning(
                    f"⚠️ No se pudieron cancelar órdenes previas en {symbol}: {error}"
                )

            order = self.exchange.create_order(
                symbol, "market", exit_side, amount, None, params
            )
            self._track_api_weight("create_order", 1, "trading")
            return order
        except Exception as e:
            self.logger.error(f"❌ Error cerrando posición {symbol}: {e}")
            raise e

    def close_due_to_degradation(
        self, symbol: str, side: str, amount: float
    ) -> Optional[CCXTOrder]:
        """
        [V118-SMART-EXIT]
        Cierra una posición inmediatamente cuando la confianza predictiva de la IA decae
        por debajo de niveles operativos. Cancela toda orden latente (SL/TP) y lanza MARKET.
        """
        self.logger.warning(
            f"⚠️ [SMART EXIT] Forzando cierre MARKET por degradación neuronal en {symbol} ({side})"
        )
        try:
            exit_side = "sell" if side.lower() == "buy" else "buy"
            params = {"reduceOnly": True}

            # Limpieza exhaustiva de la orden (Hard Reset)
            try:
                self.exchange.cancel_all_orders(symbol)
                self._track_api_weight("cancel_all_orders", 1, "trading")
            except Exception as e:
                self.logger.error(
                    f"Error cancelando órdenes previas al SMART EXIT {symbol}: {e}"
                )

            # Cierre Definitivo Táctico
            order = self.exchange.create_order(
                symbol, "market", exit_side, amount, None, params
            )
            self._track_api_weight("create_order", 1, "trading")
            return order
        except Exception as e:
            self.logger.critical(
                f"❌ FATAL ERROR ejecutando Salida por Degradación en {symbol}: {e}"
            )
            return None
