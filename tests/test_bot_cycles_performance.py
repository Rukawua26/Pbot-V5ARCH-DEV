import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pandas as pd

from core.bot_cycles import _get_cached_btc_indicator, _resolve_btc_market_indicators


class _FakeSeries:
    def __init__(self, value):
        self._value = value

    @property
    def iloc(self):
        return self

    def __getitem__(self, index):
        return self._value


class _FakeEMAIndicator:
    call_count = 0

    def __init__(self, close_vals, window):
        type(self).call_count += 1
        self.close_vals = close_vals
        self.window = window

    def ema_indicator(self):
        return _FakeSeries(123.45)


class BotCyclesPerformanceTest(unittest.TestCase):
    def test_resolve_btc_indicators_prefers_precomputed_columns(self):
        bot = SimpleNamespace(log=MagicMock())
        df = pd.DataFrame(
            {
                "time": [1, 2],
                "close": [100.0, 101.0],
                "high": [101.0, 102.0],
                "low": [99.0, 100.0],
                "EMA_200": [99.0, 100.0],
                "ADX_14": [19.0, 21.0],
            }
        )

        ema_200, adx_14 = _resolve_btc_market_indicators(bot, df)

        self.assertEqual(ema_200, 100.0)
        self.assertEqual(adx_14, 21.0)
        self.assertFalse(hasattr(bot, "_btc_indicator_fallback_cache"))

    def test_cached_btc_indicator_avoids_recomputing_same_candle(self):
        bot = SimpleNamespace(log=MagicMock())
        df = pd.DataFrame(
            {
                "time": list(range(250)),
                "close": [100.0 + i for i in range(250)],
            }
        )
        _FakeEMAIndicator.call_count = 0

        with patch("core.bot_cycles.ta_trend.EMAIndicator", _FakeEMAIndicator):
            first = _get_cached_btc_indicator(bot, df, "EMA_200")
            second = _get_cached_btc_indicator(bot, df, "EMA_200")

        self.assertEqual(first, 123.45)
        self.assertEqual(second, 123.45)
        self.assertEqual(_FakeEMAIndicator.call_count, 1)


if __name__ == "__main__":
    unittest.main()
