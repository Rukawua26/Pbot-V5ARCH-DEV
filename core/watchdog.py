import json
import os

from core.time_utils import monotonic_now, utc_now


DEFAULT_WATCHDOG_HEARTBEAT_PATH = "/dev/shm/sniper_ai_heartbeat.json"
FALLBACK_WATCHDOG_HEARTBEAT_PATH = "/tmp/sniper_ai_heartbeat.json"


def resolve_watchdog_heartbeat_path(path: str | None = None) -> str:
    preferred = (
        path or os.getenv("WATCHDOG_HEARTBEAT_PATH") or DEFAULT_WATCHDOG_HEARTBEAT_PATH
    )
    target_dir = os.path.dirname(preferred) or "."
    if os.path.isdir(target_dir):
        return preferred
    return FALLBACK_WATCHDOG_HEARTBEAT_PATH


def write_watchdog_heartbeat(
    bot,
    path: str | None = None,
    min_interval_s: float = 15.0,
):
    """Escribe heartbeat de vida para watchdog externo (idempotente por intervalo)."""
    now_mono = monotonic_now()
    last_mono = float(getattr(bot, "_watchdog_last_write_mono", 0.0) or 0.0)
    if now_mono - last_mono < min_interval_s:
        return

    target_path = resolve_watchdog_heartbeat_path(path)
    target_dir = os.path.dirname(target_path) or "."

    now_utc = utc_now()
    payload = {
        "ts": now_utc.timestamp(),
        "ts_iso": now_utc.isoformat(),
        "pid": os.getpid(),
        "status": "alive",
    }

    tmp_path = os.path.join(target_dir, ".sniper_ai_heartbeat.tmp")
    with open(tmp_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle)
        handle.flush()
        os.fsync(handle.fileno())

    os.replace(tmp_path, target_path)
    bot._watchdog_last_write_mono = now_mono
