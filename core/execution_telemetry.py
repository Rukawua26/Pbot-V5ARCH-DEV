import json
import os
from datetime import UTC, datetime


def append_execution_event(bot, event: str, payload: dict) -> None:
    try:
        os.makedirs("logs", exist_ok=True)
        record = {
            "ts": datetime.now(UTC).isoformat(),
            "event": str(event),
            "payload": payload or {},
        }
        with open("logs/execution_events.jsonl", "a", encoding="utf-8") as file_obj:
            file_obj.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as error:
        bot.log(f"⚠️ Error guardando execution event: {error}")
