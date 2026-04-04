import json
import os
import tempfile
import unittest
from types import SimpleNamespace

from core.watchdog import write_watchdog_heartbeat


class WatchdogHeartbeatTest(unittest.TestCase):
    def test_writes_heartbeat_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            hb = os.path.join(tmp, "heartbeat.json")
            bot = SimpleNamespace(_watchdog_last_write_ts=0.0)

            write_watchdog_heartbeat(bot, path=hb, min_interval_s=0.0)

            self.assertTrue(os.path.exists(hb))
            with open(hb, "r", encoding="utf-8") as handle:
                payload = json.loads(handle.read())
            self.assertIn("ts", payload)
            self.assertIn("pid", payload)

    def test_respects_min_interval(self):
        with tempfile.TemporaryDirectory() as tmp:
            hb = os.path.join(tmp, "heartbeat.json")
            bot = SimpleNamespace(_watchdog_last_write_ts=0.0)

            write_watchdog_heartbeat(bot, path=hb, min_interval_s=9999.0)
            with open(hb, "r", encoding="utf-8") as handle:
                first = json.loads(handle.read())["ts"]

            write_watchdog_heartbeat(bot, path=hb, min_interval_s=9999.0)
            with open(hb, "r", encoding="utf-8") as handle:
                second = json.loads(handle.read())["ts"]

            self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
