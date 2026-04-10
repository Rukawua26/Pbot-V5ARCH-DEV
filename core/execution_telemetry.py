import json
import os
from datetime import UTC, datetime


def _rotate_jsonl(path: str, max_bytes: int, backups: int) -> None:
    if max_bytes <= 0 or backups <= 0:
        return
    if not os.path.exists(path):
        return
    if os.path.getsize(path) < max_bytes:
        return

    oldest = f"{path}.{backups}"
    if os.path.exists(oldest):
        os.remove(oldest)

    for idx in range(backups - 1, 0, -1):
        src = f"{path}.{idx}"
        dst = f"{path}.{idx + 1}"
        if os.path.exists(src):
            os.replace(src, dst)

    os.replace(path, f"{path}.1")


def append_execution_event(bot, event: str, payload: dict) -> None:
    try:
        os.makedirs("logs", exist_ok=True)
        record = {
            "ts": datetime.now(UTC).isoformat(),
            "event": str(event),
            "payload": payload or {},
        }
        events_path = "logs/execution_events.jsonl"
        max_bytes = int(os.getenv("EXECUTION_EVENTS_MAX_BYTES", "5242880"))
        backups = int(os.getenv("EXECUTION_EVENTS_BACKUPS", "3"))
        _rotate_jsonl(events_path, max_bytes=max_bytes, backups=backups)
        with open(events_path, "a", encoding="utf-8") as file_obj:
            file_obj.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as error:
        bot.log(f"⚠️ Error guardando execution event: {error}")
