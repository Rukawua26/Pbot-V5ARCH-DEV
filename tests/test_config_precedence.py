import unittest

from core.config.manager import Config, _env_bool, _env_float, _env_int
from core.config.operational import OperationalConfig
from core.config.strategy import StrategyConfig


class ConfigPrecedenceTest(unittest.TestCase):
    def test_operational_stop_loss_atr_modifier_overrides_strategy_default(self):
        original_operational = getattr(OperationalConfig, "STOP_LOSS_ATR_MODIFIER", 1.5)
        original_operational_alias = getattr(OperationalConfig, "ATR_SL_MULTIPLIER", 1.5)
        original_strategy = getattr(StrategyConfig, "STOP_LOSS_ATR_MODIFIER", 1.5)
        original_strategy_alias = getattr(StrategyConfig, "ATR_SL_MULTIPLIER", 1.5)

        OperationalConfig.STOP_LOSS_ATR_MODIFIER = 2.25
        OperationalConfig.ATR_SL_MULTIPLIER = 2.25
        StrategyConfig.STOP_LOSS_ATR_MODIFIER = 1.5
        StrategyConfig.ATR_SL_MULTIPLIER = 1.5

        try:
            self.assertEqual(Config.STOP_LOSS_ATR_MODIFIER, 2.25)
            self.assertEqual(Config.ATR_SL_MULTIPLIER, 2.25)
        finally:
            OperationalConfig.STOP_LOSS_ATR_MODIFIER = original_operational
            OperationalConfig.ATR_SL_MULTIPLIER = original_operational_alias
            StrategyConfig.STOP_LOSS_ATR_MODIFIER = original_strategy
            StrategyConfig.ATR_SL_MULTIPLIER = original_strategy_alias

    def test_default_max_shadow_trades_allows_broader_exploration(self):
        self.assertEqual(Config.MAX_SHADOW_TRADES, 20)

    def test_config_env_parsers_fallback_on_invalid_values(self):
        import os
        from unittest.mock import patch

        with patch.dict(
            os.environ,
            {
                "TEST_FLOAT_SETTING": "bad",
                "TEST_INT_SETTING": "also-bad",
                "TEST_BOOL_SETTING": "off",
            },
        ):
            self.assertEqual(_env_float("TEST_FLOAT_SETTING", 1.5), 1.5)
            self.assertEqual(_env_int("TEST_INT_SETTING", 4), 4)
            self.assertFalse(_env_bool("TEST_BOOL_SETTING", True))
