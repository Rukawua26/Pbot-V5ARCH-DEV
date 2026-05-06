import threading
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd

from core import bot_balance_ops
from core import bot_market_state
from core import market_intelligence
from core.strategy.regime_hmm import DynamicHMMRegime


def _ticker(symbol, *, volume=50_000_000, price=1.0, percentage=1.0):
    return {
        "symbol": symbol,
        "quoteVolume": float(volume),
        "last": float(price),
        "percentage": float(percentage),
    }


def _build_market_bot(tickers, *, fetch_tickers_error=None):
    execution = SimpleNamespace(
        fetch_tickers=MagicMock(side_effect=fetch_tickers_error)
        if fetch_tickers_error
        else MagicMock(return_value=tickers),
        fetch_ticker=MagicMock(side_effect=RuntimeError("fallback unavailable")),
    )
    brain = SimpleNamespace(
        get_symbol_performance=MagicMock(
            side_effect=lambda sym: {"wr": 85, "trades": 10}
            if sym.startswith("ALPHA")
            else {"wr": 45, "trades": 10}
        ),
        get_symbol_blacklist=MagicMock(return_value=[]),
    )
    return SimpleNamespace(
        execution=execution,
        brain=brain,
        data_service=SimpleNamespace(audit_symbol_maturity=MagicMock(return_value=True)),
        risk_engine=SimpleNamespace(
            check_anti_revenge_blacklist=MagicMock(return_value=(True, ""))
        ),
        blacklist={},
        restricted_sectors=set(),
        pairs_to_scan=[],
        scanner_history=[],
        market_btc_price=0.0,
        lock=threading.RLock(),
        log=MagicMock(),
        _load_runtime_symbol_controls=MagicMock(
            return_value={"blocked": set(), "preferred": set()}
        ),
        _get_active_market_snapshot=MagicMock(return_value=[]),
    )


class MarketIntelligencePipelineTests(unittest.TestCase):
    @patch.object(market_intelligence.Config, "MAX_REAL_PAIRS", 10)
    @patch.object(market_intelligence.Config, "MAX_SHADOW_PAIRS", 10)
    @patch.object(market_intelligence.Config, "TOP_TRIAGE_COUNT", 2)
    @patch.object(market_intelligence.Config, "TRIAGE_MIN_VOL_24H", 10_000_000)
    @patch.object(market_intelligence.Config, "MIN_VOLUME_24H", 15_000_000)
    @patch.object(market_intelligence.Config, "PRICE_PRIORITY_LIMIT", 3.0)
    @patch.object(market_intelligence.Config, "RADAR_PRIORITY_HIGH_VOL_LOW_PRICE", 1.0)
    @patch.object(market_intelligence.Config, "RADAR_PRIORITY_HIGH_WR", 1.0)
    @patch.object(market_intelligence.Config, "RADAR_PRIORITY_OTHERS", 1.0)
    def test_acquire_targets_prioritizes_high_wr_liquid_symbols(self):
        tickers = {
            "ALPHA/USDT": _ticker("ALPHA/USDT", volume=90_000_000, price=1.0),
            "BETA/USDT": _ticker("BETA/USDT", volume=85_000_000, price=1.0),
            "LOWVOL/USDT": _ticker("LOWVOL/USDT", volume=1_000_000, price=1.0),
            "BTC/USDT": _ticker("BTC/USDT", volume=100_000_000, price=65_000.0),
        }
        bot = _build_market_bot(tickers)

        result = market_intelligence.acquire_targets(bot)

        self.assertIs(result, tickers)
        self.assertIn("ALPHA/USDT", bot.pairs_to_scan)
        self.assertNotIn("LOWVOL/USDT", bot.pairs_to_scan)
        self.assertLess(
            bot.pairs_to_scan.index("ALPHA/USDT"),
            bot.pairs_to_scan.index("BETA/USDT"),
        )
        self.assertEqual(bot.market_btc_price, 65_000.0)
        self.assertTrue(
            any(item["symbol"] == "ALPHA/USDT" for item in bot.scanner_history)
        )

    @patch.object(market_intelligence.Config, "MAX_REAL_PAIRS", 10)
    @patch.object(market_intelligence.Config, "MAX_SHADOW_PAIRS", 10)
    @patch.object(market_intelligence.Config, "TOP_TRIAGE_COUNT", 1)
    @patch.object(market_intelligence.Config, "TRIAGE_MIN_VOL_24H", 10_000_000)
    @patch.object(market_intelligence.Config, "MIN_VOLUME_24H", 15_000_000)
    @patch.object(market_intelligence.Config, "PRICE_PRIORITY_LIMIT", 3.0)
    @patch.object(market_intelligence.Config, "RADAR_PRIORITY_HIGH_VOL_LOW_PRICE", 1.0)
    @patch.object(market_intelligence.Config, "RADAR_PRIORITY_HIGH_WR", 1.0)
    @patch.object(market_intelligence.Config, "RADAR_PRIORITY_OTHERS", 1.0)
    def test_acquire_targets_filters_extreme_pump_symbols(self):
        tickers = {
            "PUMPED/USDT": _ticker("PUMPED/USDT", volume=80_000_000, percentage=50.0),
            "NORMAL/USDT": _ticker("NORMAL/USDT", volume=80_000_000, percentage=5.0),
            "BTC/USDT": _ticker("BTC/USDT", volume=100_000_000, price=65_000.0),
        }
        bot = _build_market_bot(tickers)

        market_intelligence.acquire_targets(bot)

        self.assertNotIn("PUMPED/USDT", bot.pairs_to_scan)
        self.assertIn("NORMAL/USDT", bot.pairs_to_scan)

    def test_acquire_targets_returns_empty_when_ticker_fetch_fails(self):
        bot = _build_market_bot({}, fetch_tickers_error=RuntimeError("API down"))
        bot.pairs_to_scan = []

        result = market_intelligence.acquire_targets(bot)

        self.assertEqual(result, {})
        bot._get_active_market_snapshot.assert_called_once()
        bot.execution.fetch_ticker.assert_called_once_with("BTC/USDT")

    @patch.object(market_intelligence.Config, "TRIAGE_MIN_VOL_24H", 10_000_000)
    @patch.object(market_intelligence.Config, "TRIAGE_SPREAD_MAX", 0.002)
    @patch.object(market_intelligence.Config, "TRIAGE_RVOL_EMA_ALPHA", 0.5)
    def test_get_active_market_snapshot_builds_ranked_pairs_from_stream_snapshot(self):
        tickers = {
            "ALPHA/USDT": {
                **_ticker("ALPHA/USDT", volume=80_000_000, price=1.0),
                "bid": 0.999,
                "ask": 1.0,
            },
            "BETA/USDT": {
                **_ticker("BETA/USDT", volume=60_000_000, price=2.0),
                "bid": 1.999,
                "ask": 2.0,
            },
            "BULL/USDT": {
                **_ticker("BULL/USDT", volume=90_000_000, price=1.0),
                "bid": 0.999,
                "ask": 1.0,
            },
            "LOWVOL/USDT": {
                **_ticker("LOWVOL/USDT", volume=1_000_000, price=1.0),
                "bid": 0.999,
                "ask": 1.0,
            },
        }
        execution = SimpleNamespace(
            fetch_book_tickers=MagicMock(
                return_value=[
                    {"symbol": "ALPHAUSDT", "bidPrice": "0.999", "askPrice": "1.0"},
                    {"symbol": "BETAUSDT", "bidPrice": "1.999", "askPrice": "2.0"},
                ]
            ),
            has_markets_loaded=MagicMock(return_value=True),
            load_markets=MagicMock(),
            fetch_tickers=MagicMock(return_value=tickers),
        )
        bot = SimpleNamespace(execution=execution, weight_tracker=None, log=MagicMock())

        ranked = market_intelligence.get_active_market_snapshot(bot)

        symbols = [item["symbol"] for item in ranked]
        self.assertIn("ALPHA/USDT", symbols)
        self.assertIn("BETA/USDT", symbols)
        self.assertNotIn("BULL/USDT", symbols)
        self.assertNotIn("LOWVOL/USDT", symbols)
        self.assertEqual(len(bot._dynamic_pair_list), 2)
        execution.load_markets.assert_not_called()

    @patch.object(market_intelligence.Config, "TRIAGE_MIN_VOL_24H", 10_000_000)
    @patch.object(market_intelligence.Config, "TRIAGE_SPREAD_MAX", 0.002)
    def test_get_active_market_snapshot_removes_stale_or_wide_spread_pairs(self):
        tickers = {
            "KEEP/USDT": {
                **_ticker("KEEP/USDT", volume=50_000_000, price=1.0),
                "bid": 0.999,
                "ask": 1.0,
            },
            "WIDE/USDT": {
                **_ticker("WIDE/USDT", volume=50_000_000, price=1.0),
                "bid": 0.90,
                "ask": 1.0,
            },
        }
        execution = SimpleNamespace(
            fetch_book_tickers=MagicMock(return_value=[]),
            has_markets_loaded=MagicMock(return_value=True),
            load_markets=MagicMock(),
            fetch_tickers=MagicMock(return_value=tickers),
        )
        bot = SimpleNamespace(
            execution=execution,
            weight_tracker=None,
            _dynamic_pair_list=["KEEP/USDT", "WIDE/USDT", "MISSING/USDT"],
            _market_scan_offset=0,
            _market_cache={},
            _market_cache_ts=0,
            _vol_ema={},
            log=MagicMock(),
        )

        ranked = market_intelligence.get_active_market_snapshot(bot)

        symbols = [item["symbol"] for item in ranked]
        self.assertIn("KEEP/USDT", symbols)
        self.assertNotIn("WIDE/USDT", symbols)
        self.assertNotIn("MISSING/USDT", symbols)


class BalanceOpsPressureTests(unittest.TestCase):
    def test_get_current_balance_returns_cached_balance_on_api_failure(self):
        bot = SimpleNamespace(
            execution=SimpleNamespace(get_balance=MagicMock(side_effect=RuntimeError("API Error"))),
            available_balance=100.0,
            log=MagicMock(),
        )

        self.assertEqual(bot_balance_ops.get_current_balance(bot), 100.0)
        bot.log.assert_called_once()

    @patch.object(bot_balance_ops.Config, "PAPER_MODE", True)
    @patch.object(bot_balance_ops.Config, "PAPER_INITIAL_BALANCE", 1_000.0)
    def test_start_silent_sync_uses_paper_initial_balance_when_zeroed(self):
        bot = SimpleNamespace(
            is_running=True,
            balance=0.0,
            available_balance=0.0,
            daily_initial_balance=0.0,
            lock=threading.RLock(),
            log=MagicMock(),
        )

        def _stop_after_first_sleep(_seconds):
            bot.is_running = False

        with patch.object(bot_balance_ops.time, "sleep", side_effect=_stop_after_first_sleep):
            bot_balance_ops.start_silent_sync(bot)

        self.assertEqual(bot.balance, 1_000.0)
        self.assertEqual(bot.available_balance, 1_000.0)
        self.assertEqual(bot.daily_initial_balance, 1_000.0)


class RegimePipelineTests(unittest.TestCase):
    def test_hmm_predicts_range_with_deterministic_model_state(self):
        np.random.seed(42)
        close = pd.Series(100.0 + np.sin(np.arange(240) / 8.0) * 0.2)
        df_sideways = pd.DataFrame({"close": close})
        hmm = DynamicHMMRegime(lookback_candles=200)
        hmm._fit_features(df_sideways)
        hmm.model = SimpleNamespace(predict_proba=MagicMock(return_value=np.array([[0.1, 0.8, 0.1]])))
        hmm.state_map = {0: "BEAR_TREND", 1: "RANGE", 2: "BULL_TREND"}
        hmm.is_ready = True

        regime, confidence = hmm.predict_regime(df_sideways)

        self.assertEqual(regime, "RANGE")
        self.assertEqual(confidence, 0.8)

    def test_hmm_returns_unknown_when_not_ready_with_insufficient_data(self):
        hmm = DynamicHMMRegime(lookback_candles=200)
        short_df = pd.DataFrame({"close": [100.0] * 50})

        self.assertFalse(hmm.dynamic_retrain(short_df))
        self.assertEqual(hmm.predict_regime(short_df), ("UNKNOWN", 0.0))

    def test_heuristic_market_regime_returns_range_without_btc_price(self):
        bot = SimpleNamespace(
            market_btc_price=0,
            data_service=SimpleNamespace(fetch_and_update_data=MagicMock()),
            log=MagicMock(),
        )

        result = bot_market_state._detect_market_regime_heuristic(bot, None)

        self.assertEqual(result, "RANGE")
        self.assertEqual(bot.market_regime_source, "HEURISTIC")
        bot.data_service.fetch_and_update_data.assert_not_called()


if __name__ == "__main__":
    unittest.main()
