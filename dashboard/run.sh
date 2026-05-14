#!/bin/bash
cd "$(dirname "$0")/.."
SNIPER_API_KEY="${SNIPER_API_KEY:-sniper-local-2026}" exec ./.venv/bin/uvicorn \
  dashboard.api_server:app --host 127.0.0.1 --port 8000 --reload
