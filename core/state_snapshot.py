"""Snapshot de estado del bot para dashboard externo. Zero acoplamiento al bot."""
import json
import logging
import os
import time

from config import Config

STATE_FILE = "/dev/shm/sniper_state.json"
_INTERVAL = 2.0
logger = logging.getLogger(__name__)


def _write_state_snapshot(bot):
    try:
        ts = time.time()
        mode = "REAL" if not Config.PAPER_MODE else "PAPER"
        with bot.balance_lock:
            bal = float(getattr(bot, "balance", 0.0) or 0.0)
            avail = float(getattr(bot, "available_balance", 0.0) or 0.0)
        with bot.lock:
            raw_trades = dict(getattr(bot, "active_trades", {}))
        real_trades = []
        shadow_trades = []
        for t in raw_trades.values():
            entry_price = t.get("entry_price", t.get("entry", 0))
            size = t.get("size", t.get("size_usd", 0))
            pnl_pct = t.get("pnl_pct", t.get("pnl", 0))
            confidence = t.get(
                "confidence",
                t.get("current_confidence", t.get("entry_confidence", 0)),
            )
            t_dict = {
                "symbol": t.get("symbol", "?"),
                "side": t.get("side", "?"),
                "entry_price": round(float(entry_price or 0), 8),
                "size": round(float(size or 0), 2),
                "pnl_pct": round(float(pnl_pct or 0), 2),
                "confidence": round(float(confidence or 0), 1),
                "is_shadow": bool(t.get("is_shadow", False)),
            }
            if t_dict["is_shadow"]:
                shadow_trades.append(t_dict)
            else:
                real_trades.append(t_dict)
        daily_pnl_pct = 0.0
        daily_pnl_usd = 0.0
        daily_initial_balance = float(getattr(bot, "daily_initial_balance", 0.0) or 0.0)
        if bal > 0 and daily_initial_balance > 0:
            daily_pnl_pct = round(
                ((bal - daily_initial_balance) / daily_initial_balance) * 100, 2
            )
            daily_pnl_usd = round(bal - daily_initial_balance, 2)
        snapshot = {
            "ts": ts,
            "mode": mode,
            "balance": bal,
            "available_balance": avail,
            "daily_pnl_pct": daily_pnl_pct,
            "daily_pnl_usd": daily_pnl_usd,
            "halt_system_active": bool(getattr(bot, "halt_system_active", False)),
            "integrity_lock_active": bool(getattr(bot, "integrity_lock_active", False)),
            "circuit_breaker_active": bool(getattr(bot, "circuit_breaker_active", False)),
            "is_paused": bool(getattr(bot, "is_paused", False)),
            "stop_requested": bool(getattr(bot, "stop_requested", False)),
            "active_trades_count": len(real_trades),
            "shadow_trades_count": len(shadow_trades),
            "active_trades": real_trades[:15],
            "shadow_trades": shadow_trades[:15],
            "regime": str(getattr(bot, "current_regime", "N/A")),
            "sentiment": str(getattr(bot, "current_sentiment", "NEUTRAL")),
            "uptime_seconds": round(ts - getattr(bot, "_start_ts", ts), 1),
            "scanner_pairs": len(getattr(bot, "pairs_to_scan", [])),
            "guardian_stats": getattr(bot, "_guardian_stats", {}),
        }
        slock = getattr(bot, "scanner_lock", None)
        history = getattr(bot, "scanner_history", [])
        if slock and history:
            with slock:
                snapshot["radar"] = [
                    {
                        "symbol": e.get("symbol", "?"),
                        "signal": e.get("signal", "WAIT"),
                        "prob": e.get("ml_score", -1),
                        "rsi": e.get("rsi_val", 0),
                        "trend": e.get("trend_val", "N/A"),
                        "result": e.get("result", ""),
                        "tier": e.get("tier", "IRON"),
                    }
                    for e in history[:36]
                ]
        elif history:
            snapshot["radar"] = [
                {
                    "symbol": e.get("symbol", "?"),
                    "signal": e.get("signal", "WAIT"),
                    "prob": e.get("ml_score", -1),
                    "rsi": e.get("rsi_val", 0),
                    "trend": e.get("trend_val", "N/A"),
                    "result": e.get("result", ""),
                    "tier": e.get("tier", "IRON"),
                }
                for e in history[:36]
            ]
        tmp = STATE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(snapshot, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, STATE_FILE)
    except Exception as error:
        logger.warning("state snapshot write failed: %s", error)


def start_state_snapshot_loop(bot):
    bot._start_ts = time.time()
    while getattr(bot, "is_running", True):
        _write_state_snapshot(bot)
        time.sleep(_INTERVAL)
