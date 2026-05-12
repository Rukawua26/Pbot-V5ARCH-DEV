import json
import unittest
from unittest.mock import MagicMock, patch

from core.strategy.orchestrator import StrategyOrchestrator


class TestGetAgentPerformance(unittest.TestCase):
    """Tests for learning.py::get_agent_performance with primary_ids support."""

    def setUp(self):
        patcher = patch("learning.Brain")  # noqa: F841
        self.addCleanup(patcher.stop)
        patcher2 = patch("learning.shadow_logger")
        self.addCleanup(patcher2.stop)
        patcher2.start()
        self.brain_mock = MagicMock()
        # Prevent _get_conn from actually connecting
        self.brain_mock._get_conn.side_effect = Exception("No DB in test")

    def test_primary_ids_returned(self):
        """When primary_ids=["MT","SR","G"], those keys must be in the result."""
        from learning import Brain
        brain = Brain()
        brain._get_conn = MagicMock(side_effect=Exception("No DB"))
        result = brain.get_agent_performance(primary_ids=["MT", "SR", "G"])
        self.assertIn("MT", result)
        self.assertIn("SR", result)
        self.assertIn("G", result)
        self.assertEqual(len(result), 3)

    def test_legacy_ids_when_no_primary(self):
        """When primary_ids is None, legacy agent IDs are returned."""
        from learning import Brain
        brain = Brain()
        brain._get_conn = MagicMock(side_effect=Exception("No DB"))
        result = brain.get_agent_performance()
        self.assertIn("T", result)
        self.assertIn("V", result)
        self.assertIn("G", result)

    def test_primary_ids_match_orchestrator(self):
        """Verify MT/SR/G are exactly what the orchestrator expects."""
        orch = StrategyOrchestrator()
        expected = set(orch.agents.keys())
        brain = MagicMock()
        brain.get_agent_performance.return_value = {"MT": 100.0, "SR": 100.0, "G": 100.0}
        perf = brain.get_agent_performance(primary_ids=["MT", "SR", "G"])
        self.assertEqual(set(perf.keys()), expected)

    def test_performance_affects_adaptive_weights(self):
        """When MT agent underperforms (<60), its weight factor should drop."""
        orch = StrategyOrchestrator()
        base_weights = orch._base_weights["BULL_TREND"].copy()
        # MT at 50 (below 60) should trigger 0.1x factor
        perf = {"MT": 50.0, "SR": 100.0, "G": 120.0}
        weights = orch.get_adaptive_weights("BULL_TREND", agent_performances=perf)
        # MT weight should be lower than base
        self.assertLess(weights["MT"], base_weights["MT"])

    def test_good_performance_boosts_weight(self):
        """When all agents perform well (>120), high performers get boosted."""
        orch = StrategyOrchestrator()
        perf = {"MT": 150.0, "SR": 45.0, "G": 130.0}
        weights_high = orch.get_adaptive_weights("BULL_TREND", agent_performances={"MT": 150.0, "SR": 100.0, "G": 100.0})
        weights_low = orch.get_adaptive_weights("BULL_TREND", agent_performances={"MT": 50.0, "SR": 100.0, "G": 100.0})
        # MT should have higher weight in high-perf scenario
        self.assertGreater(weights_high["MT"], weights_low["MT"])

    def test_fallback_to_legacy_ids_from_db(self):
        """If DB has snapshot with legacy IDs and we query with primary_ids,
        the function should fall back to matching legacy keys."""
        from learning import Brain
        brain = Brain()
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        snap = json.dumps({"votos": {"T": 80, "J": 60, "G": 90}})
        mock_cursor.fetchall.return_value = [
            {"pnl_percent": 2.0, "market_snapshot": snap},
            {"pnl_percent": -1.5, "market_snapshot": snap},
        ]
        mock_conn.cursor.return_value = mock_cursor
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock()
        brain._get_conn = MagicMock(return_value=mock_conn)
        result = brain.get_agent_performance(primary_ids=["MT", "SR", "G"])
        self.assertIn("MT", result)
        self.assertIn("SR", result)
        self.assertIn("G", result)


if __name__ == "__main__":
    unittest.main()
