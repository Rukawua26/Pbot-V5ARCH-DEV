import unittest

import pandas as pd

from core.backtester import VectorBacktester, VectorBacktestResult


def _candles(rows=80):
    base = pd.Timestamp("2026-01-01T00:00:00Z")
    data = []
    price = 100.0
    for idx in range(rows):
        price += 0.2 if idx % 7 else -0.1
        data.append({
            "time": base + pd.Timedelta(hours=idx),
            "open": price - 0.1,
            "high": price + 0.8,
            "low": price - 0.8,
            "close": price,
            "volume": 1000.0 + idx,
        })
    return pd.DataFrame(data)


class VectorBacktesterTest(unittest.TestCase):
    def test_requires_ohlcv_columns(self):
        candles = _candles().drop(columns=["volume"])

        with self.assertRaisesRegex(ValueError, "Missing candle columns"):
            VectorBacktester(candles)

    def test_sorts_and_deduplicates_candles_by_time(self):
        candles = _candles(4)
        duplicated = pd.concat([candles.iloc[[2]], candles.iloc[[3, 1, 0, 2]]])

        backtester = VectorBacktester(duplicated)

        self.assertEqual(len(backtester.df), 4)
        self.assertTrue(backtester.df["time"].is_monotonic_increasing)

    def test_accepts_numeric_millisecond_timestamps(self):
        candles = _candles(3)
        candles["time"] = candles["time"].astype("int64") // 1_000_000

        backtester = VectorBacktester(candles)

        self.assertTrue(str(backtester.df["time"].dtype).startswith("datetime64"))

    def test_rejects_unknown_strategy_mode(self):
        with self.assertRaisesRegex(ValueError, "Unsupported strategy_mode"):
            VectorBacktester(_candles()).evaluate(
                alma_offset=0.85,
                alma_sigma=6.0,
                z_score_threshold=1.6,
                entropy_bins=8,
                adx_threshold=25.0,
                stop_loss_pct=1.2,
                take_profit_pct=2.0,
                strategy_mode="unknown",
            )

    def test_evaluate_returns_result_contract(self):
        result = VectorBacktester(_candles()).evaluate(
            alma_offset=0.85,
            alma_sigma=6.0,
            z_score_threshold=1.6,
            entropy_bins=8,
            adx_threshold=25.0,
            stop_loss_pct=1.2,
            take_profit_pct=2.0,
        )

        self.assertIsInstance(result, VectorBacktestResult)
        self.assertGreaterEqual(result.trades, 0)
        self.assertGreaterEqual(result.max_drawdown, 0.0)


if __name__ == "__main__":
    unittest.main()
