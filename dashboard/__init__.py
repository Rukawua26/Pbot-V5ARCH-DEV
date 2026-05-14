from __future__ import annotations

import os
import socket
import threading
import time
from dataclasses import dataclass


_dashboard_lock = threading.Lock()
_dashboard_thread: threading.Thread | None = None


@dataclass(frozen=True)
class DashboardHandle:
    host: str
    port: int
    thread: threading.Thread | None
    already_running: bool = False
    enabled: bool = True


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _is_port_open(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.2)
        return sock.connect_ex((host, port)) == 0


def _log(bot, message: str) -> None:
    logger = getattr(bot, "log", None)
    if callable(logger):
        logger(message)


def start_dashboard(bot=None) -> DashboardHandle:
    """Start the localhost FastAPI dashboard alongside the bot runtime.

    This hook is invoked by ``core.bot_runtime.run_initial_load``. Keep failures
    non-fatal: the dashboard is operational visibility, not a trading dependency.
    """

    enabled = _env_bool("SNIPER_DASHBOARD_AUTOSTART", True)
    host = os.getenv("SNIPER_DASHBOARD_HOST", "127.0.0.1").strip() or "127.0.0.1"
    port = int(os.getenv("SNIPER_DASHBOARD_PORT", "8000"))

    if not enabled:
        _log(bot, "🖥️ Dashboard localhost deshabilitado por SNIPER_DASHBOARD_AUTOSTART.")
        return DashboardHandle(host=host, port=port, thread=None, enabled=False)

    with _dashboard_lock:
        global _dashboard_thread
        if _dashboard_thread and _dashboard_thread.is_alive():
            return DashboardHandle(host=host, port=port, thread=_dashboard_thread)

        if _is_port_open(host, port):
            _log(bot, f"🖥️ Dashboard localhost ya disponible en http://{host}:{port}")
            return DashboardHandle(
                host=host,
                port=port,
                thread=None,
                already_running=True,
            )

        try:
            import uvicorn
        except ImportError as error:
            _log(bot, f"⚠️ Dashboard localhost no disponible: uvicorn no instalado ({error})")
            return DashboardHandle(host=host, port=port, thread=None, enabled=False)

        def _run_server() -> None:
            config = uvicorn.Config(
                "dashboard.api_server:app",
                host=host,
                port=port,
                log_level=os.getenv("SNIPER_DASHBOARD_LOG_LEVEL", "warning"),
                access_log=False,
            )
            server = uvicorn.Server(config)
            server.run()

        _dashboard_thread = threading.Thread(
            target=_run_server,
            name="sniper-dashboard-localhost",
            daemon=True,
        )
        _dashboard_thread.start()

    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        if _is_port_open(host, port):
            _log(bot, f"🖥️ Dashboard localhost disponible en http://{host}:{port}")
            break
        time.sleep(0.1)
    else:
        _log(bot, f"⚠️ Dashboard localhost arrancando lento en http://{host}:{port}")

    return DashboardHandle(host=host, port=port, thread=_dashboard_thread)
