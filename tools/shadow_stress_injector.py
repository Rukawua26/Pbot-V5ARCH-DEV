#!/usr/bin/env python3
import argparse
import json
import os
import random
import sys
import time
from datetime import UTC, datetime
from threading import RLock
from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from config import Config
from core.execution_adapters import ShadowExecutionAdapter
from core.trade_manager import execute_order


class _MemoryBrain:
    def __init__(self):
        self._active = {}

    def get_genetic_params(self, _symbol):
        return {}

    def get_stats_by_trend(self):
        return {}

    def save_active_trade_state(self, symbol, state):
        self._active[symbol] = dict(state)
        return True

    def delete_active_trade_state(self, symbol):
        self._active.pop(symbol, None)

    def save_error_snapshot(self, _symbol, _reason, _ctx):
        return None


class _LiveTickerStub:
    def __init__(self, seed: int = 42):
        self._rng = random.Random(seed)
        self._prices = {}
        self.logger = SimpleNamespace(
            info=lambda *_a, **_k: None, warning=lambda *_a, **_k: None
        )
        self.exchange = object()

    def _price(self, symbol: str) -> float:
        p = self._prices.get(symbol, 100.0 + self._rng.uniform(-10, 10))
        p = max(1.0, p * (1.0 + self._rng.uniform(-0.001, 0.001)))
        self._prices[symbol] = p
        return p

    def fetch_ticker(self, symbol: str):
        return {"last": self._price(symbol)}

    def set_leverage(self, _lev, _symbol):
        return {"ok": True}

    def fetch_open_orders(self, _symbol=None):
        return []

    def fetch_order_by_client_id(self, _symbol, _coid):
        return None


def _build_bot(execution):
    bot = SimpleNamespace()
    bot.lock = RLock()
    bot.db_lock = RLock()
    bot.log = lambda msg: None
    bot.integrity_lock_active = False
    bot.halt_system_active = False
    bot.balance = 10_000.0
    bot.available_balance = 10_000.0
    bot.is_paused = False
    bot.circuit_breaker_active = False
    bot.cooldown_pairs = {}
    bot.active_trades = {}
    bot.instance_uuid = "stress-injector"
    bot._symbol_reduced_size_mult = 1.0
    bot.market_btc_change_tf = 0.0
    bot._load_runtime_symbol_controls = lambda: {"blocked": set(), "reduced": set()}
    bot._get_base_coin = lambda s: s.split("/")[0]
    bot.get_current_balance = lambda: 10_000.0
    bot.ws_manager = SimpleNamespace(get_l2_state=lambda _symbol: {})
    bot.brain = _MemoryBrain()
    bot.data_service = SimpleNamespace(sanitize_context=lambda ctx: ctx or {})
    bot.risk_engine = SimpleNamespace(
        calculate_position_size=lambda **kwargs: (1.0, 150.0),
        get_exit_levels=lambda **kwargs: (
            kwargs.get("entry_price", 100.0) * 0.99,
            kwargs.get("entry_price", 100.0) * 1.02,
            "STD",
        ),
        check_market_safety=lambda *_args, **_kwargs: (True, "OK", 80),
    )
    bot.execution = execution
    return bot


def _load_events_since(start_ts: float):
    out = []
    try:
        with open("logs/execution_events.jsonl", "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                ts = datetime.fromisoformat(
                    rec.get("ts").replace("Z", "+00:00")
                ).timestamp()
                if ts >= start_ts:
                    out.append(rec)
    except FileNotFoundError:
        return []
    return out


def main():
    parser = argparse.ArgumentParser(
        description="Inyector de estrés para TradeManager + Shadow adapter"
    )
    parser.add_argument("--minutes", type=float, default=1.0)
    parser.add_argument("--orders-per-minute", type=int, default=20)
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument("--seed", type=int, default=2026)
    args = parser.parse_args()

    Config.PAPER_MODE = False

    rng = random.Random(args.seed)
    live_stub = _LiveTickerStub(seed=args.seed)
    shadow_exec = ShadowExecutionAdapter(
        live_stub,
        min_latency_ms=200,
        max_latency_ms=500,
        reject_rate=0.08,
        partial_fill_rate=0.45,
        partial_fill_complete_rate=0.55,
        min_partial_ratio=0.25,
    )
    bot = _build_bot(shadow_exec)

    symbols = ["SOL/USDT", "ETH/USDT", "BNB/USDT", "XRP/USDT", "ADA/USDT"]
    interval = max(0.05, 60.0 / max(1, args.orders_per_minute))
    total_orders = max(1, int(args.minutes * args.orders_per_minute))

    start = time.time()
    results = []

    def _fire_once(i: int):
        symbol = symbols[i % len(symbols)]
        price = float(live_stub.fetch_ticker(symbol)["last"])
        t0 = time.perf_counter()
        result = execute_order(
            bot,
            symbol=symbol,
            side="BUY" if rng.random() > 0.5 else "SELL",
            price=price,
            atr=max(0.1, price * 0.01),
            is_shadow=False,
            context={"trend": "RANGO", "spread": 0.0002, "prob_final": 76.0},
        )
        dt = time.perf_counter() - t0
        return {"symbol": symbol, "result": result, "latency_s": round(dt, 6)}

    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = []
        next_tick = time.perf_counter()
        for i in range(total_orders):
            futures.append(pool.submit(_fire_once, i))
            next_tick += interval
            sleep_for = next_tick - time.perf_counter()
            if sleep_for > 0:
                time.sleep(sleep_for)
        for fut in futures:
            results.append(fut.result())

    elapsed = time.time() - start
    events = _load_events_since(start)
    ack = [e for e in events if e.get("event") == "ENTRY_ORDER_ACK"]
    partial_timeout = [
        e for e in events if e.get("event") == "PARTIAL_FILL_TIMEOUT_CANCEL"
    ]
    rejects = [
        r
        for r in results
        if str(r.get("result", "")).startswith("EXECUTION")
        or "FAIL" in str(r.get("result", ""))
    ]

    print("=== SHADOW STRESS SUMMARY ===")
    print(
        f"orders_sent={total_orders} elapsed_s={elapsed:.2f} rate={total_orders / max(elapsed, 1e-6):.2f}/s"
    )
    print(f"entry_ack_events={len(ack)} partial_timeout_cancel={len(partial_timeout)}")
    print(f"raw_reject_like_results={len(rejects)}")
    print(f"active_trades_end={len(bot.active_trades)}")
    if ack:
        avg_slippage = sum(
            float((ev.get("payload") or {}).get("slippage_simulated") or 0.0)
            for ev in ack
        ) / len(ack)
        print(f"avg_slippage_simulated={avg_slippage:.6f}")


if __name__ == "__main__":
    main()
