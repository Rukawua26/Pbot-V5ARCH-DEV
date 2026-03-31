import ccxt
import time
import logging
from typing import Optional
from config import Config
from core.types import CCXTOrder, CCXTBalanceResponse

class ExecutionService:
    """
    [V116-ULTIMATE] EXECUTION SERVICE
    =================================
    Encapsula toda la comunicación con Binance Futures.
    Implementa el "Liquidity Guard" mediante órdenes LIMIT IOC.
    """
    def __init__(self, api_key, api_secret):
        self.exchange = ccxt.binance({
            'apiKey': api_key,
            'secret': api_secret,
            'enableRateLimit': True,
            'options': {'defaultType': 'future'}
        })
        self.logger = logging.getLogger("Execution")

    def set_leverage(self, leverage, symbol):
        try:
            return self.exchange.set_leverage(leverage, symbol)
        except Exception as e:
            self.logger.error(f"Error setting leverage for {symbol}: {e}")
            return None

    def create_precision_order(self, symbol: str, side: str, amount: float, price: float, slippage_pct: float = 0.1) -> Optional[CCXTOrder]:
        """
        Ejecución quirúrgica: LIMIT IOC.
        Si no se llena al precio límite (con slippage), se cancela automáticamente.
        """
        try:
            # Calcular precio límite basado en slippage permitido
            limit_price = price * (1 + (slippage_pct/100)) if side.lower() == 'buy' else price * (1 - (slippage_pct/100))
            
            # Formatear precio para Binance
            limit_price_str = self.exchange.price_to_precision(symbol, limit_price)
            
            self.logger.info(f"🎯 Precio Base: {price} | Slippage: {slippage_pct}% | Límite IOC: {limit_price_str}")
            
            params = {
                'timeInForce': 'IOC', # Immediate or Cancel
                'postOnly': False
            }
            
            self.logger.info(f"🚀 Enviando LIMIT IOC {symbol} {side} @ {limit_price_str}")
            
            order: CCXTOrder = self.exchange.create_order(
                symbol, 
                type='limit', 
                side=side.lower(), 
                amount=amount, 
                price=float(limit_price_str), 
                params=params
            )
            
            return order
        except Exception as e:
            self.logger.error(f"❌ Error en Ejecución Quirúrgica {symbol}: {e}")
            return None

    def get_balance(self) -> float:
        try:
            balance: CCXTBalanceResponse = self.exchange.fetch_balance()
            
            # [FIX] Prioridad a 'totalWalletBalance' nativo de Futuros para mayor precisión
            info = balance.get('info', {})
            total_wallet = info.get('totalWalletBalance')
            
            if total_wallet is not None:
                return float(total_wallet)
            
            # Fallback a lectura estándar de CCXT
            total = balance.get('total', {})
            return float(total.get('USDT', 0.0))
        except Exception as e:
            self.logger.error(f"Error fetching balance: {e}")
            return 0.0

    def place_hard_sl(self, symbol: str, side: str, amount: float, stop_price: float) -> Optional[CCXTOrder]:
        """Coloca un STOP_MARKET real en Binance para seguridad extrema."""
        try:
            sl_side = 'sell' if side.lower() == 'buy' else 'buy'
            params = {
                'stopPrice': self.exchange.price_to_precision(symbol, stop_price),
                'reduceOnly': True
            }
            return self.exchange.create_order(
                symbol, 
                'STOP_MARKET', 
                sl_side, 
                amount, 
                None, 
                params
            )
        except Exception as e:
            self.logger.error(f"⚠️ Error colocando Hard SL {symbol}: {e}")
            return None

    def close_position(self, symbol: str, side: str, amount: float) -> Optional[CCXTOrder]:
        """Cierra una posición abierta inmediatamente vía MARKET order."""
        try:
            exit_side = 'sell' if side.lower() == 'buy' else 'buy'
            params = {'reduceOnly': True}
            
            # Cancelar órdenes pendientes antes de cerrar
            try:
                self.exchange.cancel_all_orders(symbol)
            except Exception:
                pass
                
            return self.exchange.create_order(
                symbol, 
                'market', 
                exit_side, 
                amount, 
                None, 
                params
            )
        except Exception as e:
            self.logger.error(f"❌ Error cerrando posición {symbol}: {e}")
            raise e

    def close_due_to_degradation(self, symbol: str, side: str, amount: float) -> Optional[CCXTOrder]:
        """
        [V116-SMART-EXIT]
        Cierra una posición inmediatamente cuando la confianza predictiva de la IA decae 
        por debajo de niveles operativos. Cancela toda orden latente (SL/TP) y lanza MARKET.
        """
        self.logger.warning(f"⚠️ [SMART EXIT] Forzando cierre MARKET por degradación neuronal en {symbol} ({side})")
        try:
            exit_side = 'sell' if side.lower() == 'buy' else 'buy'
            params = {'reduceOnly': True}
            
            # Limpieza exhaustiva de la orden (Hard Reset)
            try:
                self.exchange.cancel_all_orders(symbol)
            except Exception as e:
                self.logger.error(f"Error cancelando órdenes previas al SMART EXIT {symbol}: {e}")
                
            # Cierre Definitivo Táctico
            return self.exchange.create_order(
                symbol, 
                'market', 
                exit_side, 
                amount, 
                None, 
                params
            )
        except Exception as e:
            self.logger.critical(f"❌ FATAL ERROR ejecutando Salida por Degradación en {symbol}: {e}")
            return None
