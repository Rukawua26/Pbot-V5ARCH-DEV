import unittest
from unittest.mock import MagicMock, patch

from core.risk_engine import RiskEngine


class DummyExchange:
    def __init__(self, precision_decimals=3):
        self.markets = {"BTC/USDT": {}}
        self.precision_decimals = precision_decimals
        self.load_markets = MagicMock()

    def amount_to_precision(self, _symbol, amount):
        return f"{float(amount):.{self.precision_decimals}f}"


class RiskPositionSizingTest(unittest.TestCase):
    def _engine(self):
        with patch("core.risk_engine.CrashPredictor"):
            return RiskEngine(brain=object())

    @patch("core.risk_engine.Config.MIN_NOTIONAL_VALUE", 5.0)
    @patch("core.risk_engine.Config.MAX_MARGIN_PERCENT", 100.0)
    @patch("core.risk_engine.Config.MAX_RISK_USD", 1000.0)
    @patch("core.risk_engine.Config.RISK_PER_TRADE_PCT", 0.01)
    def test_size_uses_one_percent_risk_divided_by_stop_distance(self):
        engine = self._engine()
        amount, notional = engine.calculate_position_size_by_stop(
            balance=1000.0,
            symbol="BTC/USDT",
            entry_price=100.0,
            stop_loss_price=98.0,
            leverage=1,
            exchange=DummyExchange(precision_decimals=3),
        )

        self.assertEqual(amount, 5.0)
        self.assertEqual(notional, 500.0)

    @patch("core.risk_engine.Config.MIN_NOTIONAL_VALUE", 12.0)
    @patch("core.risk_engine.Config.MAX_MARGIN_PERCENT", 100.0)
    @patch("core.risk_engine.Config.MAX_RISK_USD", 1000.0)
    @patch("core.risk_engine.Config.RISK_PER_TRADE_PCT", 0.01)
    def test_size_rejects_when_notional_is_below_minimum(self):
        engine = self._engine()
        amount, code = engine.calculate_position_size_by_stop(
            balance=100.0,
            symbol="BTC/USDT",
            entry_price=10.0,
            stop_loss_price=5.0,
            leverage=1,
            exchange=DummyExchange(precision_decimals=3),
        )

        self.assertEqual(amount, 0.0)
        self.assertEqual(code, -1)

    @patch("core.risk_engine.Config.MIN_NOTIONAL_VALUE", 5.0)
    @patch("core.risk_engine.Config.MAX_MARGIN_PERCENT", 5.0)
    @patch("core.risk_engine.Config.MAX_RISK_USD", 1000.0)
    @patch("core.risk_engine.Config.RISK_PER_TRADE_PCT", 0.01)
    def test_size_respects_margin_and_leverage_cap(self):
        engine = self._engine()
        amount, notional = engine.calculate_position_size_by_stop(
            balance=1000.0,
            symbol="BTC/USDT",
            entry_price=100.0,
            stop_loss_price=99.0,
            leverage=2,
            exchange=DummyExchange(precision_decimals=3),
        )

        self.assertEqual(amount, 1.0)
        self.assertEqual(notional, 100.0)

    @patch("core.risk_engine.Config.MIN_NOTIONAL_VALUE", 5.0)
    @patch("core.risk_engine.Config.MAX_MARGIN_PERCENT", 100.0)
    @patch("core.risk_engine.Config.MAX_RISK_USD", 2.0)
    @patch("core.risk_engine.Config.RISK_PER_TRADE_PCT", 0.01)
    def test_size_respects_absolute_max_risk_cap(self):
        engine = self._engine()
        amount, notional = engine.calculate_position_size_by_stop(
            balance=1000.0,
            symbol="BTC/USDT",
            entry_price=100.0,
            stop_loss_price=98.0,
            leverage=1,
            exchange=DummyExchange(precision_decimals=3),
        )

        self.assertEqual(amount, 1.0)
        self.assertEqual(notional, 100.0)

    def test_size_rejects_invalid_stop_distance(self):
        engine = self._engine()
        amount, code = engine.calculate_position_size_by_stop(
            balance=1000.0,
            symbol="BTC/USDT",
            entry_price=100.0,
            stop_loss_price=100.0,
            leverage=1,
            exchange=DummyExchange(),
        )

        self.assertEqual(amount, 0.0)
        self.assertEqual(code, -5)


if __name__ == "__main__":
    unittest.main()
