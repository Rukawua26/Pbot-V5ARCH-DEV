import unittest
import threading
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import ccxt

from core.execution_service import ExecutionService


class _FlakyCancelExchange:
    def __init__(self):
        self.timeout = 0
        self.cancel_attempts = 0

    def cancel_order(self, order_id, symbol):
        self.cancel_attempts += 1
        if self.cancel_attempts == 1:
            raise ccxt.NetworkError("temporary network issue")
        return {"id": order_id, "symbol": symbol, "status": "canceled"}


class _FlakyHardSlExchange:
    def __init__(self):
        self.timeout = 0
        self.create_attempts = 0

    def price_to_precision(self, _symbol, stop_price):
        return str(stop_price)

    def create_order(self, symbol, order_type, side, amount, price, params):
        self.create_attempts += 1
        if self.create_attempts == 1:
            raise ccxt.RateLimitExceeded("rate limited")
        return {
            "id": "sl-1",
            "symbol": symbol,
            "type": order_type,
            "side": side,
            "amount": amount,
            "price": price,
            "params": params,
        }


class _TimeoutProbeExchange:
    def __init__(self):
        self.timeout = 9000

    def cancel_order(self, order_id, symbol):
        return {
            "id": order_id,
            "symbol": symbol,
            "status": "canceled",
            "timeout_seen": self.timeout,
        }


class _NoPriceExchange:
    def __init__(self):
        self.timeout = 9000
        self.market_exit_calls = 0

    def cancel_all_orders(self, _symbol):
        return []

    def fetch_ticker(self, _symbol):
        return {"last": 0}

    def create_order(self, symbol, order_type, side, amount, price, params):
        self.market_exit_calls += 1
        return {
            "id": "mkt-1",
            "symbol": symbol,
            "type": order_type,
            "side": side,
            "amount": amount,
            "price": price,
            "params": params,
            "status": "closed",
        }


class _ConcurrentTimeoutExchange:
    def __init__(self):
        self.timeout = 9000
        self._lock = threading.Lock()
        self.seen_timeouts = []

    def cancel_order(self, order_id, symbol):
        with self._lock:
            self.seen_timeouts.append(self.timeout)
        return {
            "id": order_id,
            "symbol": symbol,
            "status": "canceled",
            "timeout_seen": self.timeout,
        }


class ExecutionServiceResilienceTest(unittest.TestCase):
    @patch("core.execution_service.time.sleep", return_value=None)
    def test_cancel_order_retries_on_network_error(self, _sleep_mock):
        service = ExecutionService("k", "s")
        service.exchange = _FlakyCancelExchange()
        service.set_weight_tracker(None)

        result = service.cancel_order("BTC/USDT", "order-123")

        self.assertEqual(result.get("status"), "canceled")
        self.assertEqual(service.exchange.cancel_attempts, 2)

    @patch("core.execution_service.time.sleep", return_value=None)
    def test_place_hard_sl_retries_on_rate_limit(self, _sleep_mock):
        service = ExecutionService("k", "s")
        service.exchange = _FlakyHardSlExchange()
        service.set_weight_tracker(None)

        result = service.place_hard_sl(
            "BTC/USDT", side="BUY", amount=1.0, stop_price=99.5
        )

        self.assertIsNotNone(result)
        self.assertEqual(service.exchange.create_attempts, 2)
        self.assertEqual(service.last_hard_sl_error, "")

    def test_call_exchange_restores_timeout_after_operation(self):
        service = ExecutionService("k", "s")
        service.exchange = _TimeoutProbeExchange()
        service.set_weight_tracker(None)

        result = service.cancel_order("BTC/USDT", "order-timeout")

        self.assertEqual(result.get("status"), "canceled")
        self.assertEqual(result.get("timeout_seen"), 20000)
        self.assertEqual(service.exchange.timeout, 9000)

    @patch("core.execution_service.Config.NO_PRICE_ALLOW_MARKET_EXIT", True)
    @patch("core.execution_service.Config.NO_PRICE_EXIT_ESCALATION_SECONDS", 1)
    @patch("core.execution_service.Config.NO_PRICE_EXIT_MIN_ESCALATION_SECONDS", 1)
    @patch(
        "core.execution_service.time.monotonic",
        side_effect=[10.0, 10.2, 12.5, 12.5, 12.5, 12.5],
    )
    def test_no_price_escalates_to_market_exit_after_threshold(self, _mono_mock):
        service = ExecutionService("k", "s")
        service.exchange = _NoPriceExchange()
        service.set_weight_tracker(None)

        first = service.close_position("BTC/USDT", side="BUY", amount=0.1)
        second = service.close_position("BTC/USDT", side="BUY", amount=0.1)
        third = service.close_position("BTC/USDT", side="BUY", amount=0.1)

        self.assertIsNone(first)
        self.assertEqual(service.exchange.market_exit_calls, 1)
        escalated = second if second is not None else third
        self.assertIsNotNone(escalated)
        self.assertEqual(escalated.get("type"), "market")
        self.assertEqual(service.exchange.market_exit_calls, 1)

    @patch("core.execution_service.Config.NO_PRICE_ALLOW_MARKET_EXIT", False)
    @patch("core.execution_service.Config.NO_PRICE_EXIT_ESCALATION_SECONDS", 1)
    @patch("core.execution_service.Config.NO_PRICE_EXIT_MIN_ESCALATION_SECONDS", 1)
    @patch(
        "core.execution_service.time.monotonic",
        side_effect=[10.0, 10.2, 12.5, 12.5, 12.5, 12.5],
    )
    def test_no_price_does_not_market_exit_when_disabled(self, _mono_mock):
        service = ExecutionService("k", "s")
        service.exchange = _NoPriceExchange()
        service.set_weight_tracker(None)

        first = service.close_position("BTC/USDT", side="BUY", amount=0.1)
        second = service.close_position("BTC/USDT", side="BUY", amount=0.1)
        third = service.close_position("BTC/USDT", side="BUY", amount=0.1)

        self.assertIsNone(first)
        self.assertIsNone(second)
        self.assertIsNone(third)
        self.assertEqual(service.exchange.market_exit_calls, 0)

    def test_dynamic_no_price_threshold_tunes_with_daily_exit_count(self):
        service = ExecutionService("k", "s")
        service.exchange = _NoPriceExchange()
        service.set_weight_tracker(None)

        with (
            patch(
                "core.execution_service.Config.NO_PRICE_EXIT_ESCALATION_SECONDS", 180
            ),
            patch(
                "core.execution_service.Config.NO_PRICE_EXIT_MIN_ESCALATION_SECONDS", 45
            ),
        ):
            base = service._resolve_no_price_threshold("BTC/USDT")
            service._record_no_price_market_exit("BTC/USDT")
            tuned_once = service._resolve_no_price_threshold("BTC/USDT")
            for _ in range(10):
                service._record_no_price_market_exit("BTC/USDT")
            tuned_floor = service._resolve_no_price_threshold("BTC/USDT")

        self.assertEqual(base, 180)
        self.assertLess(tuned_once, base)
        self.assertGreaterEqual(tuned_floor, 45)

    def test_cancel_all_degraded_activates_symbol_quarantine(self):
        service = ExecutionService("k", "s")
        service.exchange = _TimeoutProbeExchange()
        service.set_weight_tracker(None)

        with (
            patch(
                "core.execution_service.Config.CANCEL_ALL_DEGRADED_WINDOW_SECONDS", 300
            ),
            patch(
                "core.execution_service.Config.CANCEL_ALL_DEGRADED_QUARANTINE_EVENTS", 3
            ),
            patch(
                "core.execution_service.Config.CANCEL_ALL_DEGRADED_QUARANTINE_SECONDS",
                600,
            ),
            patch(
                "core.execution_service.time.time",
                return_value=30.0,
            ),
        ):
            service._record_cancel_all_orders_failure("BTC/USDT", RuntimeError("e1"))
            service._record_cancel_all_orders_failure("BTC/USDT", RuntimeError("e2"))
            service._record_cancel_all_orders_failure("BTC/USDT", RuntimeError("e3"))
            self.assertTrue(service.is_symbol_quarantined("BTC/USDT"))
            remaining = service.get_symbol_quarantine_remaining_seconds("BTC/USDT")

        self.assertGreater(remaining, 0)
