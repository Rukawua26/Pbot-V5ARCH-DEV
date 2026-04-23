#!/usr/bin/env bash
set -euo pipefail

BOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${BOT_DIR}/.venv/bin/python"
UNIT_DIR="${HOME}/.config/systemd/user"
UNIT_FILE="${UNIT_DIR}/sniper-ai.service"

mkdir -p "${UNIT_DIR}"

cat > "${UNIT_FILE}" <<EOF
[Unit]
Description=Sniper AI Bot Watchdog Service
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=/usr/bin/env bash -lc 'cd "${BOT_DIR}" && exec "${PYTHON_BIN}" main.py'
ExecStop=/usr/bin/env bash -lc 'pkill -f "${PYTHON_BIN} main.py" || true'
Restart=always
RestartSec=5
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
