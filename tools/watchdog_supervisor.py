#!/usr/bin/env python3
"""Watchdog externo para Sniper AI.

Si el heartbeat en /dev/shm no se actualiza dentro del umbral, reinicia el servicio.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
from pathlib import Path


def read_heartbeat_ts(path: Path) -> float:
    if not path.exists():
        return 0.0
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return float(payload.get("ts", 0.0) or 0.0)
    except Exception:
        return 0.0


def main() -> int:
    parser = argparse.ArgumentParser(description="Sniper AI external watchdog")
    parser.add_argument("--heartbeat", default="/dev/shm/sniper_ai_heartbeat.json")
    parser.add_argument("--service", default="sniper-ai.service")
    parser.add_argument("--stale-seconds", type=int, default=45)
    args = parser.parse_args()

    heartbeat_path = Path(args.heartbeat)
    ts = read_heartbeat_ts(heartbeat_path)
    now = time.time()

    stale = ts <= 0 or (now - ts) > float(args.stale_seconds)
    if not stale:
        print("[OK] Heartbeat fresco")
        return 0

    print(
        f"[ALERT] Heartbeat stale/missing. ts={ts:.3f}, now={now:.3f}, stale_s={args.stale_seconds}"
    )

    try:
        subprocess.run(
            ["/usr/bin/systemctl", "restart", args.service],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        print(f"[OK] Servicio reiniciado: {args.service}")
        return 0
    except Exception as error:
        print(f"[FAIL] No se pudo reiniciar {args.service}: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
