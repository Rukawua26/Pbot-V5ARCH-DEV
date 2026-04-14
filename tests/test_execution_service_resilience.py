import unittest
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
    @patch("core.execution_service.time.monotonic", side_effect=[10.0, 10.2, 12.5])
    def test_no_price_escalates_to_market_exit_after_threshold(self, _mono_mock):
        service = ExecutionService("k", "s")
        service.exchange = _NoPriceExchange()
        service.set_weight_tracker(None)

        first = service.close_position("BTC/USDT", side="BUY", amount=0.1)
        second = service.close_position("BTC/USDT", side="BUY", amount=0.1)
        third = service.close_position("BTC/USDT", side="BUY", amount=0.1)

        self.assertIsNone(first)
        self.assertIsNone(second)
        self.assertIsNotNone(third)
        self.assertEqual(third.get("type"), "market")
        self.assertEqual(service.exchange.market_exit_calls, 1)

    @patch("core.execution_service.Config.NO_PRICE_ALLOW_MARKET_EXIT", False)
    @patch("core.execution_service.Config.NO_PRICE_EXIT_ESCALATION_SECONDS", 1)
    @patch("core.execution_service.time.monotonic", side_effect=[10.0, 10.2, 12.5])
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


if __name__ == "__main__":
    unittest.main()
