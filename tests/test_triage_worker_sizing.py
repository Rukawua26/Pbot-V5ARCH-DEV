import unittest
from unittest.mock import patch

from core.bot_cycles import _resolve_triage_worker_count


class TriageWorkerSizingTest(unittest.TestCase):
    @patch("core.bot_cycles.Config.TRIAGE_MAX_WORKERS", 16)
    def test_worker_count_never_exceeds_triage_count(self):
        self.assertEqual(_resolve_triage_worker_count(1), 1)
        self.assertEqual(_resolve_triage_worker_count(3), 3)

    @patch("core.bot_cycles.Config.TRIAGE_MAX_WORKERS", 16)
    def test_worker_count_respects_configured_cap(self):
        self.assertEqual(_resolve_triage_worker_count(50), 16)

    @patch("core.bot_cycles.Config.TRIAGE_MAX_WORKERS", 100)
    def test_worker_count_has_hard_safety_cap(self):
        self.assertEqual(_resolve_triage_worker_count(50), 32)

    @patch("core.bot_cycles.Config.TRIAGE_MAX_WORKERS", 0)
    def test_worker_count_handles_invalid_low_cap(self):
        self.assertGreaterEqual(_resolve_triage_worker_count(50), 1)

    def test_worker_count_handles_empty_input(self):
        self.assertEqual(_resolve_triage_worker_count(0), 1)


if __name__ == "__main__":
    unittest.main()
