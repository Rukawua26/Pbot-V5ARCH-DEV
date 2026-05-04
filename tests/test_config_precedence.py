import unittest

from core.config.manager import Config
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
