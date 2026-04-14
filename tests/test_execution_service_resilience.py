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


if __name__ == "__main__":
    unittest.main()
