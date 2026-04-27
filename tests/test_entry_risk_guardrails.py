import unittest

from core.config.operational import OperationalConfig
from strategy import Strategy


class EntryRiskGuardrailsTest(unittest.TestCase):
    def test_kava_veto_uses_configurable_max_entry_sl_pct(self):
        original_max_entry_sl_pct = getattr(OperationalConfig, "MAX_ENTRY_SL_PCT", 1.2)
        original_sl_modifier = getattr(OperationalConfig, "STOP_LOSS_ATR_MODIFIER", 1.5)
        OperationalConfig.MAX_ENTRY_SL_PCT = 2.5
        OperationalConfig.STOP_LOSS_ATR_MODIFIER = 1.5

        try:
            passed, reason, *_ = Strategy.check_entry_filters(
                rsi=55,
                adx=25,
                current_time=None,
                audit_signal="BUY",
                volatility=0.0,
                vol_rel=1.2,
                is_shadow=False,
                price=100.0,
                atr=1.5,
                side="BUY",
                regime="RANGO",
            )
            self.assertTrue(passed)
            self.assertEqual(reason, "Filter Pass (v118-PRO)")
        finally:
            OperationalConfig.MAX_ENTRY_SL_PCT = original_max_entry_sl_pct
            OperationalConfig.STOP_LOSS_ATR_MODIFIER = original_sl_modifier

    def test_kava_veto_uses_runtime_sl_modifier_and_genes(self):
        original_max_entry_sl_pct = getattr(OperationalConfig, "MAX_ENTRY_SL_PCT", 1.2)
        original_sl_modifier = getattr(OperationalConfig, "STOP_LOSS_ATR_MODIFIER", 1.5)
        OperationalConfig.MAX_ENTRY_SL_PCT = 1.2
        OperationalConfig.STOP_LOSS_ATR_MODIFIER = 1.5

        try:
            passed, reason, *_ = Strategy.check_entry_filters(
                rsi=55,
                adx=25,
                current_time=None,
                audit_signal="BUY",
                volatility=0.0,
                vol_rel=1.2,
                is_shadow=False,
                price=100.0,
                atr=1.0,
                side="BUY",
                regime="RANGO",
                modifier=0.8,
                genes={"sl_multiplier": 0.5},
            )
            self.assertTrue(passed)
            self.assertEqual(reason, "Filter Pass (v118-PRO)")
        finally:
            OperationalConfig.MAX_ENTRY_SL_PCT = original_max_entry_sl_pct
            OperationalConfig.STOP_LOSS_ATR_MODIFIER = original_sl_modifier
