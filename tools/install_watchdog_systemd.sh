#!/usr/bin/env bash
set -euo pipefail

BOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${BOT_DIR}/.venv/bin/python"
LOCK_FILE="${BOT_DIR}/.sniperai.lock"
UNIT_DIR="${HOME}/.config/systemd/user"
UNIT_FILE="${UNIT_DIR}/sniper-ai.service"

mkdir -p "${UNIT_DIR}"

if ! flock -n "${LOCK_FILE}" true; then
    owner="$(cat "${LOCK_FILE}" 2>/dev/null || true)"
    echo "Another Sniper AI instance is already running (lock owner: ${owner:-unknown})."
    echo "Stop the existing process before installing/restarting the systemd service."
    echo "To inspect: ps -fp ${owner:-<pid>}"
    exit 75
fi

cat > "${UNIT_FILE}" <<EOF
[Unit]
Description=Sniper AI Bot Watchdog Service
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
Environment=PYTHONUNBUFFERED=1
ExecStartPre=/usr/bin/env bash -lc 'flock -n "${LOCK_FILE}" true || exit 75'
ExecStart=/usr/bin/env bash -lc 'cd "${BOT_DIR}" && exec "${PYTHON_BIN}" main.py'
ExecStop=/usr/bin/env bash -lc 'kill -INT "\${MAINPID}" 2>/dev/null || true'
Restart=on-failure
RestartPreventExitStatus=75
RestartSec=10
KillSignal=SIGINT
TimeoutStopSec=30
NoNewPrivileges=true
LimitNOFILE=65535

[Install]
WantedBy=default.target
EOF

echo "Service file created: ${UNIT_FILE}"

systemctl --user daemon-reload
systemctl --user enable --now sniper-ai.service
systemctl --user restart sniper-ai.service
sleep 2
systemctl --user --no-pager --full status sniper-ai.service || true

echo "Done. Watchdog active with automatic restart."
