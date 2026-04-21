import io
import unittest
from unittest.mock import MagicMock, patch

from notifier import NotificationQueue, send_telegram_photo


class _Response:
    def __init__(self, status_code, text=""):
        self.status_code = status_code
        self.text = text


class BootAndNotifierTest(unittest.TestCase):
    @patch("core.bot_app.acquire_single_instance_lock", return_value=True)
    @patch("core.bot_app.Bot", side_effect=RuntimeError("boot failed"))
    def test_run_entrypoint_exits_non_zero_on_fatal_boot(
        self, _mocked_bot, _mocked_lock
    ):
        from core.bot_app import run_entrypoint

        with self.assertRaises(SystemExit) as ctx:
            run_entrypoint()

        self.assertEqual(ctx.exception.code, 1)

    @patch("notifier.telegram_post")
    def test_notification_queue_falls_back_without_markdown(self, mocked_post):
        mocked_post.side_effect = [
            _Response(400, "Bad Request: can't parse entities"),
            _Response(200, "ok"),
        ]
        queue = NotificationQueue(max_retries=1, rate_limit_seconds=0)
        queue.running = False

        ok = queue._send_with_retry(
            "sendMessage",
            {"chat_id": "1", "text": "BTC_[test]", "parse_mode": "Markdown"},
            1,
        )

        self.assertTrue(ok)
        self.assertEqual(mocked_post.call_count, 2)
        self.assertEqual(mocked_post.call_args_list[1].kwargs["json"], {"chat_id": "1", "text": "BTC_[test]"})
        queue.stop()

    @patch("notifier.Config.TELEGRAM_CHAT_ID", "1")
    @patch("notifier.Config.TELEGRAM_TOKEN", "token")
    @patch("notifier.telegram_post")
    def test_send_telegram_photo_falls_back_without_markdown(self, mocked_post):
        mocked_post.side_effect = [
            _Response(400, "Bad Request: can't parse entities"),
            _Response(200, "ok"),
        ]

        send_telegram_photo("caption_[x]", io.BytesIO(b"png"))

        self.assertEqual(mocked_post.call_count, 2)
        self.assertNotIn("parse_mode", mocked_post.call_args_list[1].kwargs["data"])


if __name__ == "__main__":
    unittest.main()
