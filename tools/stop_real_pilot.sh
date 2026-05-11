#!/usr/bin/env bash
set -euo pipefail

# =============================================================================
# Emergency Stop & Rollback — detiene REAL pilot y restaura PAPER mode
# Uso: bash tools/stop_real_pilot.sh [--emergency]
# =============================================================================

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "=== REAL PILOT SHUTDOWN ==="
echo ""

# 1. Find and kill bot
PID=""
if [ -f logs/bot.pid ]; then
    PID=$(cat logs/bot.pid)
fi

if [ -z "$PID" ] || ! kill -0 "$PID" 2>/dev/null; then
    PID=$(pgrep -f "python.*main.py" || true)
fi

if [ -n "$PID" ] && kill -0 "$PID" 2>/dev/null; then
    echo "Deteniendo bot (PID $PID)..."
    
    # Graceful shutdown
    kill -15 "$PID" 2>/dev/null || true
    sleep 5
    
    # Force kill if still alive
    if kill -0 "$PID" 2>/dev/null; then
        if [ "${1:-}" = "--emergency" ]; then
            echo "⚠️ EMERGENCY: SIGKILL forzado."
            kill -9 "$PID" 2>/dev/null || true
        else
            echo "⚠️ Graceful shutdown timeout. Forzando..."
            kill -9 "$PID" 2>/dev/null || true
        fi
    fi
    echo "✓ Bot detenido"
else
    echo "No hay bot activo."
fi

# 2. Restore PAPER backup
if [ -f .env.paper.backup ]; then
    cp .env.paper.backup .env
    echo "✓ .env restaurado desde .env.paper.backup"
else
    echo "⚠️ No hay backup de .env. Debes restaurarlo manualmente."
fi

# 3. Remove PID file
rm -f logs/bot.pid
echo "✓ PID file cleaned"

# 4. Verify
echo ""
echo "=== POST-MORTEM ==="
if [ -f logs/real_pilot.log ]; then
    echo "Últimas líneas del log REAL:"
    tail -5 logs/real_pilot.log
fi

echo ""
echo "Bot detenido y PAPER mode restaurado."
echo "Para reanudar PAPER: python main.py"
