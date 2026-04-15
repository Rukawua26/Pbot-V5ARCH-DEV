import ccxt
import time
import logging
import random
import threading
from datetime import datetime, timezone
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
        self.last_entry_reject_error = ""
        self._last_valid_balance: Optional[float] = None
        self._exchange_call_lock = threading.RLock()
        self._cancel_all_failures = {}
        self._cancel_all_failure_events = {}
        self._symbol_quarantine_until = {}
        self._no_price_exit_state = {}
        self._no_price_exit_daily_metrics = {}

    def set_weight_tracker(self, tracker):
        self.weight_tracker = tracker

    def _track_api_weight(self, endpoint: str, weight: int, category: str):
        if self.weight_tracker:
            self.weight_tracker.track(endpoint, weight, category)

    def _call_exchange(
        self, op_name: str, fn, *, retries: int = 2, timeout_s: float = 0.0
    ):
        last_error = None
        for attempt in range(1, retries + 1):
            with self._exchange_call_lock:
                previous_timeout = getattr(self.exchange, "timeout", None)
                timeout_overridden = False
                try:
                    if timeout_s > 0:
                        self.exchange.timeout = int(timeout_s * 1000)
                        timeout_overridden = True
                    return fn()
                except ccxt.RateLimitExceeded as error:
                    last_error = error
                    if attempt >= retries:
                        break
                    sleep_s = (0.6 * attempt) + random.uniform(0.0, 0.3)
                    self.logger.warning(
                        f"⚠️ {op_name} rate-limit retry {attempt}/{retries}: {error}"
                    )
                except (ccxt.NetworkError, ccxt.RequestTimeout) as error:
                    last_error = error
                    if attempt >= retries:
                        break
                    sleep_s = (0.35 * attempt) + random.uniform(0.0, 0.2)
                    self.logger.warning(
                        f"⚠️ {op_name} network timeout/retry {attempt}/{retries}: {error}"
                    )
                except Exception as error:
                    last_error = error
                    break
                finally:
                    if timeout_overridden:
                        self.exchange.timeout = previous_timeout
            if attempt < retries:
                time.sleep(sleep_s)
        if last_error is not None:
            raise last_error
        raise RuntimeError(f"{op_name} failed without captured error")

    def _track_emergency_stuck(
        self, symbol: str, side: str, amount: float, order: dict
    ):
        """Emite telemetría de emergencia cuando posición queda atrapada en libro."""
        self.logger.critical(
            f"🚨 EMERGENCY_EXIT_STUCK | {symbol} | {side} | "
            f"amount={amount} | order_id={order.get('id', 'N/A')}"
        )
        # Notificación Telegram de emergencia
        try:
            from notifier import send_telegram_msg

            send_telegram_msg(
                f"🚨 *EMERGENCY_EXIT_STUCK*\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"🔹 *{symbol}* ({side})\n"
                f"💰 Amount: {amount}\n"
                f"📋 Order ID: {order.get('id', 'N/A')}\n"
                f"⚠️ *INTERVENCIÓN MANUAL REQUERIDA*"
            )
        except Exception as error:
            self.logger.warning(
                f"⚠️ No se pudo enviar alerta EMERGENCY_EXIT_STUCK: {error}"
            )

    def _wait_order_filled(self, symbol: str, order_id: str, timeout_s: int) -> bool:
        start_wait = time.time()
        while time.time() - start_wait < timeout_s:
            try:
                status = self._call_exchange(
                    "fetch_order",
                    lambda: self.exchange.fetch_order(order_id, symbol),
                    retries=2,
                    timeout_s=15.0,
                )
                if status.get("status") in ["closed", "filled"]:
                    return True
            except Exception as poll_error:
                self.logger.warning(
                    f"⚠️ Error consultando estado de orden {order_id} en {symbol}: {poll_error}"
                )
                return False
            time.sleep(0.5)
        return False

    def _record_cancel_all_orders_success(self, symbol: str):
        self._cancel_all_failures.pop(symbol, None)
        self._cancel_all_failure_events.pop(symbol, None)

    def _is_quarantine_active(self, symbol: str) -> bool:
        until = float(self._symbol_quarantine_until.get(symbol) or 0.0)
        if until <= 0:
            return False
        if time.time() >= until:
            self._symbol_quarantine_until.pop(symbol, None)
            return False
        return True

    def is_symbol_quarantined(self, symbol: str) -> bool:
        return self._is_quarantine_active(symbol)

    def get_symbol_quarantine_remaining_seconds(self, symbol: str) -> int:
        until = float(self._symbol_quarantine_until.get(symbol) or 0.0)
        if until <= 0:
            return 0
        remaining = int(max(0.0, until - time.time()))
        if remaining <= 0:
            self._symbol_quarantine_until.pop(symbol, None)
            return 0
        return remaining

    def _active_no_price_day_key(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def get_no_price_market_exit_count(
        self, symbol: str, day_key: Optional[str] = None
    ) -> int:
        key = day_key or self._active_no_price_day_key()
        return int((self._no_price_exit_daily_metrics.get(key) or {}).get(symbol, 0))

    def _resolve_no_price_threshold(self, symbol: str) -> int:
        base_threshold = int(
            getattr(Config, "NO_PRICE_EXIT_ESCALATION_SECONDS", 180) or 180
        )
        min_threshold = int(
            getattr(Config, "NO_PRICE_EXIT_MIN_ESCALATION_SECONDS", 45) or 45
        )
        daily_count = self.get_no_price_market_exit_count(symbol)
        dynamic_factor = 1.0 + (0.4 * min(daily_count, 5))
        dynamic_threshold = int(round(base_threshold / dynamic_factor))
        return max(min_threshold, dynamic_threshold)

    def _record_no_price_market_exit(self, symbol: str) -> int:
        day_key = self._active_no_price_day_key()
        day_metrics = self._no_price_exit_daily_metrics.setdefault(day_key, {})
        day_metrics[symbol] = int(day_metrics.get(symbol, 0)) + 1
        return day_metrics[symbol]

    def export_runtime_state(self) -> dict:
        now_ts = time.time()
        with self._exchange_call_lock:
            quarantines = {}
            for symbol, until in (self._symbol_quarantine_until or {}).items():
                try:
                    until_ts = float(until)
                except (TypeError, ValueError):
                    continue
                if until_ts > now_ts:
                    quarantines[str(symbol)] = until_ts

            day_key = self._active_no_price_day_key()
            daily_counts = dict((self._no_price_exit_daily_metrics.get(day_key) or {}))

            return {
                "version": 1,
                "saved_at": now_ts,
                "quarantines": quarantines,
                "no_price_exit_daily": {day_key: daily_counts},
            }

    def import_runtime_state(self, state: dict) -> None:
        if not isinstance(state, dict):
            return

        now_ts = time.time()
        with self._exchange_call_lock:
            loaded_quarantines = {}
            for symbol, until in (state.get("quarantines") or {}).items():
                try:
                    until_ts = float(until)
                except (TypeError, ValueError):
                    continue
                if until_ts > now_ts:
                    loaded_quarantines[str(symbol)] = until_ts
            self._symbol_quarantine_until = loaded_quarantines

            persisted_daily = state.get("no_price_exit_daily") or {}
            day_key = self._active_no_price_day_key()
            day_metrics = persisted_daily.get(day_key) or {}
            if isinstance(day_metrics, dict):
                self._no_price_exit_daily_metrics = {
                    day_key: {
                        str(symbol): int(value)
                        for symbol, value in day_metrics.items()
                        if isinstance(value, (int, float)) and int(value) >= 0
                    }
                }
            else:
                self._no_price_exit_daily_metrics = {}

    def _record_cancel_all_orders_failure(self, symbol: str, error):
        now_ts = time.time()
        state = self._cancel_all_failures.get(symbol, {"count": 0})
        state["count"] = int(state.get("count") or 0) + 1
        state["last_error"] = str(error)
        state["last_ts"] = now_ts
        self._cancel_all_failures[symbol] = state

        events = self._cancel_all_failure_events.get(symbol, [])
        events.append(now_ts)
        window_s = int(
            getattr(Config, "CANCEL_ALL_DEGRADED_WINDOW_SECONDS", 300) or 300
        )
        cutoff = now_ts - window_s
        events = [evt for evt in events if evt >= cutoff]
        self._cancel_all_failure_events[symbol] = events

        count = state["count"]
        self.logger.warning(
            f"⚠️ cancel_all_orders fallo {symbol}: intento consecutivo {count}, error={error}"
        )

        quarantine_events = int(
            getattr(Config, "CANCEL_ALL_DEGRADED_QUARANTINE_EVENTS", 3) or 3
        )
        if len(events) >= quarantine_events:
            quarantine_s = int(
                getattr(Config, "CANCEL_ALL_DEGRADED_QUARANTINE_SECONDS", 900) or 900
            )
            quarantine_until = now_ts + quarantine_s
            previous_until = float(self._symbol_quarantine_until.get(symbol) or 0.0)
            self._symbol_quarantine_until[symbol] = max(
                previous_until, quarantine_until
            )
            remaining_s = int(max(0.0, self._symbol_quarantine_until[symbol] - now_ts))
            self.logger.critical(
                f"🚫 SYMBOL_QUARANTINE_ACTIVATED {symbol}: {len(events)} fallos cancel_all en {window_s}s. "
                f"Quarantined {remaining_s}s."
            )

        if count >= 3:
            self.logger.critical(
                f"🚨 CANCEL_ALL_ORDERS_DEGRADED {symbol}: {count} fallos consecutivos"
            )
            try:
                from notifier import send_telegram_msg

                send_telegram_msg(
                    f"🚨 *CANCEL_ALL_ORDERS_DEGRADED*\n"
                    f"Símbolo: {symbol}\n"
                    f"Fallos consecutivos: {count}\n"
                    f"Error: {str(error)[:180]}"
                )
            except Exception as notify_error:
                self.logger.warning(
                    f"⚠️ No se pudo notificar CANCEL_ALL_ORDERS_DEGRADED: {notify_error}"
                )

    def _handle_no_price_exit(self, symbol: str, exit_side: str, amount: float):
        now_mono = time.monotonic()
        state = self._no_price_exit_state.get(symbol) or {
            "first_seen": now_mono,
            "last_warn": 0.0,
        }

        threshold_s = self._resolve_no_price_threshold(symbol)
        allow_market = bool(getattr(Config, "NO_PRICE_ALLOW_MARKET_EXIT", False))
        elapsed_s = now_mono - float(state.get("first_seen") or now_mono)

        if elapsed_s < threshold_s or not allow_market:
            remaining = max(0.0, float(threshold_s) - elapsed_s)
            if now_mono - float(state.get("last_warn") or 0.0) > 30.0:
                self.logger.critical(
                    f"🛑 NO_PRICE {symbol}: salida bloqueada. "
                    f"Escalado en {remaining:.1f}s (allow_market={allow_market})."
                )
                state["last_warn"] = now_mono
            self._no_price_exit_state[symbol] = state
            return None

        try:
            order = self._call_exchange(
                "no_price_market_emergency_exit",
                lambda: self.exchange.create_order(
                    symbol, "market", exit_side, amount, None, {"reduceOnly": True}
                ),
                retries=2,
                timeout_s=20.0,
            )
            self._track_api_weight("create_order", 1, "trading")
            daily_count = self._record_no_price_market_exit(symbol)
            self.logger.critical(
                f"🚨 NO_PRICE_ESCALATED_MARKET_EXIT {symbol}: market reduce-only ejecutada "
                f"(daily_count={daily_count}, threshold_s={threshold_s})"
            )
            self._no_price_exit_state.pop(symbol, None)
            return order
        except Exception as error:
            self.logger.critical(
                f"❌ NO_PRICE_ESCALATED_MARKET_EXIT_FAILED {symbol}: {error}"
            )
            self._no_price_exit_state[symbol] = state
            return None

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
        canceled = self._call_exchange(
            "cancel_order",
            lambda: self.exchange.cancel_order(order_id, symbol),
            retries=3,
            timeout_s=20.0,
        )
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

    def fetch_book_ticker(self, symbol: str):
        market_id = self.exchange.market_id(symbol)
        book = self.exchange.fapiPublicGetTickerBookTicker({"symbol": market_id})
        self._track_api_weight("fapiPublicGetTickerBookTicker", 1, "market")
        if isinstance(book, list):
            return (book[0] if book else {}) or {}
        return book or {}

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
            requested_leverage = leverage
            try:
                bounded_leverage = int(float(leverage))
            except (TypeError, ValueError):
                bounded_leverage = int(getattr(Config, "LEVERAGE", 10))
            bounded_leverage = max(1, min(bounded_leverage, 10))
            if str(bounded_leverage) != str(requested_leverage):
                self.logger.warning(
                    f"⚠️ Leverage ajustado por guardrail: {requested_leverage}x -> {bounded_leverage}x ({symbol})"
                )

            result = self.exchange.set_leverage(bounded_leverage, symbol)
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
        if self._is_quarantine_active(symbol):
            remaining_s = self.get_symbol_quarantine_remaining_seconds(symbol)
            self.last_entry_reject_error = (
                f"SYMBOL_QUARANTINED_CANCEL_ALL_DEGRADED ({remaining_s}s)"
            )
            self.logger.warning(
                f"🚫 ENTRY_BLOCKED_QUARANTINE {symbol}: {remaining_s}s restantes"
            )
            return None

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

            order: CCXTOrder = self._call_exchange(
                "create_precision_order",
                lambda: self.exchange.create_order(
                    symbol,
                    type="limit",
                    side=side.lower(),
                    amount=amount,
                    price=float(limit_price_str),
                    params=params,
                ),
                retries=3,
                timeout_s=25.0,
            )
            self._track_api_weight("create_order", 1, "trading")

            return order
        except Exception as e:
            self.logger.error(f"❌ Error en Ejecución Quirúrgica {symbol}: {e}")
            return None

    def get_balance(self) -> float:
        last_error = None

        for attempt in range(2):
            try:
                balance: CCXTBalanceResponse = self.exchange.fetch_balance()
                self._track_api_weight("fetch_balance", 5, "account")

                info = balance.get("info", {})
                total_wallet = info.get("totalWalletBalance")
                if total_wallet is not None:
                    parsed = float(total_wallet)
                    self._last_valid_balance = parsed
                    return parsed

                total = balance.get("total", {})
                parsed = float(total.get("USDT", 0.0))
                self._last_valid_balance = parsed
                return parsed
            except Exception as e:
                last_error = e
                msg = str(e)
                timestamp_error = (
                    "-1021" in msg
                    or "recvWindow" in msg
                    or "Timestamp for this request is outside of the recvWindow" in msg
                )

                if timestamp_error and attempt == 0:
                    self.logger.warning(
                        "⚠️ Error de timestamp detectado al leer balance. Re-sincronizando reloj con Binance y reintentando..."
                    )
                    try:
                        if hasattr(self.exchange, "load_time_difference"):
                            self.exchange.load_time_difference()
                        elif hasattr(self.exchange, "fetch_time"):
                            self.exchange.fetch_time()
                    except Exception as sync_error:
                        self.logger.warning(
                            f"⚠️ No se pudo sincronizar diferencia horaria: {sync_error}"
                        )
                    time.sleep(0.35)
                    continue

                break

        self.logger.error(f"Error fetching balance: {last_error}")
        if self._last_valid_balance is not None:
            self.logger.warning(
                f"⚠️ Usando último balance válido en caché: ${self._last_valid_balance:.2f}"
            )
            return float(self._last_valid_balance)
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
            order = self._call_exchange(
                "place_hard_sl",
                lambda: self.exchange.create_order(
                    symbol, "STOP_MARKET", sl_side, amount, None, params
                ),
                retries=3,
                timeout_s=25.0,
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
        """
        [v119-CHASE-LIMIT] Cierra posición con Chase Limit + Hard Floor.
        - -2% inicial, persigue hasta -5% (Hard Floor)
        - Si Hard Floor no llena: deja orden en libro + EMERGENCY_EXIT_STUCK
        - NUNCA fallback a MARKET
        """
        try:
            exit_side = "sell" if side.lower() == "buy" else "buy"
            params = {"reduceOnly": True}

            # Cancelar órdenes pendientes antes de cerrar
            try:
                self._call_exchange(
                    "cancel_all_orders",
                    lambda: self.exchange.cancel_all_orders(symbol),
                    retries=3,
                    timeout_s=20.0,
                )
                self._track_api_weight("cancel_all_orders", 1, "trading")
                self._record_cancel_all_orders_success(symbol)
            except Exception as error:
                self._record_cancel_all_orders_failure(symbol, error)

            # [v119] Chase Limit: -2%, -3%, -4%, -5% (Hard Floor)
            current_price = float(
                (
                    self._call_exchange(
                        "fetch_ticker",
                        lambda: self.exchange.fetch_ticker(symbol),
                        retries=2,
                        timeout_s=15.0,
                    )
                    or {}
                ).get("last", 0)
                or 0
            )

            if current_price > 0:
                # Estrategia de persecución
                CHASE_STEPS = [0.98, 0.97, 0.96, 0.95]  # -2%, -3%, -4%, -5%
                HARD_FLOOR_PCT = 0.05  # -5% máximo
                TIMEOUT_PER_STEP = 2  # segundos por paso

                last_order = None
                for step_idx, step_mult in enumerate(CHASE_STEPS):
                    limit_price = (
                        current_price * step_mult
                        if exit_side == "sell"
                        else current_price * (2 - step_mult)
                    )
                    try:
                        limit_price = self.exchange.price_to_precision(
                            symbol, limit_price
                        )
                        order = self._call_exchange(
                            "close_position_create_order",
                            lambda: self.exchange.create_order(
                                symbol, "limit", exit_side, amount, limit_price, params
                            ),
                            retries=3,
                            timeout_s=20.0,
                        )
                        self._track_api_weight("create_order", 1, "trading")
                        last_order = order

                        # Esperar fill con timeout
                        if self._wait_order_filled(
                            symbol, order["id"], timeout_s=TIMEOUT_PER_STEP
                        ):
                            self.logger.info(
                                f"✅ CHASE_LIMIT OK {symbol} @ {limit_price} "
                                f"(step {step_idx + 1}/{len(CHASE_STEPS)})"
                            )
                            self._no_price_exit_state.pop(symbol, None)
                            return order

                        # Timeout en este step → siguiente persecución
                        self.logger.warning(
                            f"⏳ Chase step {step_idx + 1} timeout {symbol} @ {limit_price}, "
                            f"persiguiendo..."
                        )
                        self._call_exchange(
                            "close_position_cancel_order",
                            lambda: self.exchange.cancel_order(order["id"], symbol),
                            retries=2,
                            timeout_s=15.0,
                        )

                    except Exception as step_err:
                        self.logger.warning(
                            f"⚠️ Chase step {step_idx + 1} falló {symbol}: {step_err}"
                        )
                        continue

                # [HARD FLOOR] Si llegó aquí, ningún step llenó
                # Dejar la última orden en el libro y marcar EMERGENCY_EXIT_STUCK
                if last_order:
                    self.logger.critical(
                        f"🚨 HARD_FLOOR_REACHED {symbol}: posición atrapada en libro @ "
                        f"{last_order.get('price', 'N/A')}. Alerta manual requerida."
                    )
                    # Emitir evento de telemetría de emergencia
                    self._track_emergency_stuck(symbol, exit_side, amount, last_order)
                    return last_order
                else:
                    # Si ninguna orden se creó, intentar una última orden al precio actual
                    # como último recurso (sin slippage protection, pero sin MARKET)
                    self.logger.warning(
                        f"⚠️ Sin fill tras persecución {symbol}, ordenando al precio actual"
                    )
                    try:
                        market = self.exchange.market(symbol)
                        emergency_price = self.exchange.price_to_precision(
                            symbol, current_price
                        )
                        order = self._call_exchange(
                            "close_position_emergency_create_order",
                            lambda: self.exchange.create_order(
                                symbol,
                                "limit",
                                exit_side,
                                amount,
                                emergency_price,
                                params,
                            ),
                            retries=3,
                            timeout_s=20.0,
                        )
                        self._track_api_weight("create_order", 1, "trading")
                        self._no_price_exit_state.pop(symbol, None)
                        return order
                    except Exception as emergency_err:
                        self.logger.critical(
                            f"❌ EMERGENCY_EXIT_FAILED {symbol}: {emergency_err}"
                        )
                        return None
            else:
                # Sin precio disponible → NO ejecutar (evitar slippage ciego)
                return self._handle_no_price_exit(symbol, exit_side, amount)

        except Exception as e:
            self.logger.error(f"❌ Error cerrando posición {symbol}: {e}")
            raise e

    def close_due_to_degradation(
        self, symbol: str, side: str, amount: float
    ) -> Optional[CCXTOrder]:
        """
        [v119-CHASE-LIMIT] Cierra por degradación neuronal con Chase Limit + Hard Floor.
        - -2% inicial, persigue hasta -5% (Hard Floor)
        - Si Hard Floor no llena: deja orden en libro + EMERGENCY_EXIT_STUCK
        - NUNCA fallback a MARKET
        """
        self.logger.warning(
            f"⚠️ [SMART EXIT] Chase Limit (-2%→-5%) por degradación neuronal en {symbol} ({side})"
        )
        try:
            exit_side = "sell" if side.lower() == "buy" else "buy"
            params = {"reduceOnly": True}

            # Limpieza exhaustiva de la orden (Hard Reset)
            try:
                self._call_exchange(
                    "cancel_all_orders",
                    lambda: self.exchange.cancel_all_orders(symbol),
                    retries=3,
                    timeout_s=20.0,
                )
                self._track_api_weight("cancel_all_orders", 1, "trading")
                self._record_cancel_all_orders_success(symbol)
            except Exception as e:
                self._record_cancel_all_orders_failure(symbol, e)

            # [v119] Chase Limit: -2%, -3%, -4%, -5% (Hard Floor)
            current_price = float(
                (
                    self._call_exchange(
                        "fetch_ticker",
                        lambda: self.exchange.fetch_ticker(symbol),
                        retries=2,
                        timeout_s=15.0,
                    )
                    or {}
                ).get("last", 0)
                or 0
            )

            if current_price > 0:
                CHASE_STEPS = [0.98, 0.97, 0.96, 0.95]
                HARD_FLOOR_PCT = 0.05
                TIMEOUT_PER_STEP = 2

                last_order = None
                for step_idx, step_mult in enumerate(CHASE_STEPS):
                    limit_price = (
                        current_price * step_mult
                        if exit_side == "sell"
                        else current_price * (2 - step_mult)
                    )
                    try:
                        limit_price = self.exchange.price_to_precision(
                            symbol, limit_price
                        )
                        order = self._call_exchange(
                            "close_degradation_create_order",
                            lambda: self.exchange.create_order(
                                symbol, "limit", exit_side, amount, limit_price, params
                            ),
                            retries=3,
                            timeout_s=20.0,
                        )
                        self._track_api_weight("create_order", 1, "trading")
                        last_order = order

                        if self._wait_order_filled(
                            symbol, order["id"], timeout_s=TIMEOUT_PER_STEP
                        ):
                            self.logger.info(
                                f"✅ CHASE_DEGRADATION OK {symbol} @ {limit_price} "
                                f"(step {step_idx + 1}/{len(CHASE_STEPS)})"
                            )
                            self._no_price_exit_state.pop(symbol, None)
                            return order

                        self.logger.warning(
                            f"⏳ Chase step {step_idx + 1} timeout {symbol} @ {limit_price}"
                        )
                        self._call_exchange(
                            "close_degradation_cancel_order",
                            lambda: self.exchange.cancel_order(order["id"], symbol),
                            retries=2,
                            timeout_s=15.0,
                        )

                    except Exception as step_err:
                        self.logger.warning(
                            f"⚠️ Chase step {step_idx + 1} falló {symbol}: {step_err}"
                        )
                        continue

                # [HARD FLOOR] Ningún step llenó
                if last_order:
                    self.logger.critical(
                        f"🚨 HARD_FLOOR_REACHED (degradation) {symbol}: posición atrapada @ "
                        f"{last_order.get('price', 'N/A')}. Intervención manual."
                    )
                    self._track_emergency_stuck(symbol, exit_side, amount, last_order)
                    return last_order
                else:
                    # Último recurso: precio actual
                    try:
                        emergency_price = self.exchange.price_to_precision(
                            symbol, current_price
                        )
                        order = self._call_exchange(
                            "close_degradation_emergency_create_order",
                            lambda: self.exchange.create_order(
                                symbol,
                                "limit",
                                exit_side,
                                amount,
                                emergency_price,
                                params,
                            ),
                            retries=3,
                            timeout_s=20.0,
                        )
                        self._track_api_weight("create_order", 1, "trading")
                        self._no_price_exit_state.pop(symbol, None)
                        return order
                    except Exception as emergency_err:
                        self.logger.critical(
                            f"❌ EMERGENCY_EXIT_FAILED (degradation) {symbol}: {emergency_err}"
                        )
                        return None
            else:
                return self._handle_no_price_exit(symbol, exit_side, amount)

        except Exception as e:
            self.logger.critical(
                f"❌ FATAL ERROR ejecutando Salida por Degradación en {symbol}: {e}"
            )
            return None
