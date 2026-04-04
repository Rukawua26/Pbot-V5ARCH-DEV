import json
import os
import time


def write_watchdog_heartbeat(
    bot,
    path: str = "/dev/shm/sniper_ai_heartbeat.json",
    min_interval_s: float = 15.0,
):
    """Escribe heartbeat de vida para watchdog externo (idempotente por intervalo)."""
    now = time.time()
    last = float(getattr(bot, "_watchdog_last_write_ts", 0.0) or 0.0)
    if now - last < min_interval_s:
        return

    target_path = path
    target_dir = os.path.dirname(target_path) or "."
    if not os.path.isdir(target_dir):
        target_path = "/tmp/sniper_ai_heartbeat.json"
        target_dir = "/tmp"

    payload = {
        "ts": now,
        "pid": os.getpid(),
        "status": "alive",
    }

    tmp_path = os.path.join(target_dir, ".sniper_ai_heartbeat.tmp")
    with open(tmp_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle)
        handle.flush()
        os.fsync(handle.fileno())

    os.replace(tmp_path, target_path)
    bot._watchdog_last_write_ts = now
