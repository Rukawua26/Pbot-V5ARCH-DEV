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