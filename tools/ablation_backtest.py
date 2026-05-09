#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from core.backtester import VectorBacktester
from tools.walk_forward_backtest import BacktestParams, load_candles_csv


DEFAULT_MODES = ("mt_sr_regime", "mt_only", "sr_only", "equal_weight")


def run_ablation_backtest(candles, params: BacktestParams, modes=DEFAULT_MODES) -> dict:
    rows = []
    for mode in modes:
        result = VectorBacktester(candles).evaluate(**asdict(params), strategy_mode=mode)
        rows.append({"mode": mode, **asdict(result)})
    rows.sort(key=lambda item: item["objective"], reverse=True)
    baseline = next(row for row in rows if row["mode"] == "mt_sr_regime")
    return {
        "baseline_mode": "mt_sr_regime",
        "baseline": baseline,
        "best": rows[0],
        "rows": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Vector backtest ablation runner")
    parser.add_argument("--candles", required=True, help="CSV with time,open,high,low,close,volume")
    parser.add_argument("--output", default="reports/ablation_backtest.json")
    parser.add_argument("--z-score-threshold", type=float, default=1.6)
    parser.add_argument("--adx-threshold", type=float, default=25.0)
    parser.add_argument("--stop-loss-pct", type=float, default=1.2)
    parser.add_argument("--take-profit-pct", type=float, default=2.0)
    args = parser.parse_args()

    params = BacktestParams(
        alma_offset=0.85,
        alma_sigma=6.0,
        z_score_threshold=args.z_score_threshold,
        entropy_bins=8,
        adx_threshold=args.adx_threshold,
        stop_loss_pct=args.stop_loss_pct,
        take_profit_pct=args.take_profit_pct,
    )
    report = run_ablation_backtest(load_candles_csv(Path(args.candles)), params)
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({"baseline": report["baseline"], "best": report["best"]}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
