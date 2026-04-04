import random
import time
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

from core.execution_adapters import ShadowExecutionAdapter, build_execution_gateway


class _FakeExecutionService:
    def __init__(self, _api_key, _api_secret):
        self.logger = MagicMock()
        self.exchange = SimpleNamespace(set_sandbox_mode=MagicMock())
        self.last_hard_sl_error = ""

    def fetch_ticker(self, _symbol):
        return {"last": 100.0}


class ExecutionAdapterContractTest(unittest.TestCase):
    def test_factory_builds_shadow_adapter(self):
        config = SimpleNamespace(
            BINANCE_API_KEY="k",
            BINANCE_API_SECRET="s",
            USE_TESTNET=False,
            EXECUTION_BACKEND="shadow_live",
            SHADOW_SIM_LATENCY_MIN_MS=0,
            SHADOW_SIM_LATENCY_MAX_MS=0,
            SHADOW_SIM_REJECT_RATE=0.0,
            SHADOW_SIM_PARTIAL_FILL_RATE=0.0,
            SHADOW_SIM_MIN_PARTIAL_RATIO=0.3,
        )

        execution = build_execution_gateway(config, _FakeExecutionService)

        self.assertIsInstance(execution, ShadowExecutionAdapter)

    def test_factory_enables_testnet_on_base_execution(self):
        config = SimpleNamespace(
            BINANCE_API_KEY="k",
            BINANCE_API_SECRET="s",
            USE_TESTNET=True,
            EXECUTION_BACKEND="live",
        )

        execution = build_execution_gateway(config, _FakeExecutionService)

        execution.exchange.set_sandbox_mode.assert_called_once_with(True)

    def test_shadow_adapter_simulates_partial_fills(self):
        live = _FakeExecutionService("k", "s")
        adapter = ShadowExecutionAdapter(
            live,
            min_latency_ms=0,
            max_latency_ms=0,
            reject_rate=0.0,
            partial_fill_rate=1.0,
            partial_fill_complete_rate=0.0,
            min_partial_ratio=0.5,
            random_source=random.Random(1),
            sleep_fn=lambda _s: None,
        )

        order = adapter.create_precision_order(
            "BTC/USDT", "BUY", amount=2.0, price=100.0, client_order_id="cid-1"
        )
        self.assertIsInstance(order, dict)
        order = order or {}

        self.assertEqual(order.get("status"), "open")
        self.assertLess(float(order.get("filled") or 0.0), 2.0)
        self.assertEqual(order.get("clientOrderId"), "cid-1")

    def test_shadow_adapter_latency_is_non_blocking_for_caller(self):
        live = _FakeExecutionService("k", "s")
        adapter = ShadowExecutionAdapter(
            live,
            min_latency_ms=400,
            max_latency_ms=400,
            reject_rate=0.0,
            partial_fill_rate=1.0,
            partial_fill_complete_rate=1.0,
            min_partial_ratio=0.6,
            random_source=random.Random(3),
        )

        started = time.perf_counter()
        order = adapter.create_precision_order(
            "BTC/USDT", "BUY", amount=2.0, price=100.0, client_order_id="cid-2"
        )
        elapsed = time.perf_counter() - started

        self.assertIsInstance(order, dict)
        self.assertLess(elapsed, 0.1)

    def test_partial_fill_can_finalize_async(self):
        live = _FakeExecutionService("k", "s")
        adapter = ShadowExecutionAdapter(
            live,
            min_latency_ms=40,
            max_latency_ms=40,
            reject_rate=0.0,
            partial_fill_rate=1.0,
            partial_fill_complete_rate=1.0,
            min_partial_ratio=0.5,
            random_source=random.Random(4),
        )

        order = adapter.create_precision_order(
            "BTC/USDT", "BUY", amount=2.0, price=100.0, client_order_id="cid-3"
        )
        self.assertIsInstance(order, dict)
        order = order or {}
        self.assertEqual(order.get("status"), "open")
        open_now = adapter.fetch_open_orders("BTC/USDT")
        self.assertTrue(any(o.get("id") == order.get("id") for o in open_now))

        time.sleep(0.08)
        open_later = adapter.fetch_open_orders("BTC/USDT")
        self.assertFalse(any(o.get("id") == order.get("id") for o in open_later))

    def test_shadow_adapter_sets_immediate_trigger_error_for_invalid_sl(self):
        live = _FakeExecutionService("k", "s")
        adapter = ShadowExecutionAdapter(
            live,
            min_latency_ms=0,
            max_latency_ms=0,
            reject_rate=0.0,
            partial_fill_rate=0.0,
            random_source=random.Random(7),
            sleep_fn=lambda _s: None,
        )

        sl_order = adapter.place_hard_sl("BTC/USDT", "BUY", 1.0, stop_price=101.0)

        self.assertIsNone(sl_order)
        self.assertIn("-2021", adapter.last_hard_sl_error)


if __name__ == "__main__":
    unittest.main()
