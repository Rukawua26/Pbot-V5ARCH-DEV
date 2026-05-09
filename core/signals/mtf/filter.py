from __future__ import annotations

import pandas as pd

from config import Config
from core.execution_telemetry import append_execution_event
from core.signals.mtf.analyzer import analyze_mtf_alignment
from core.signals.mtf.data import fetch_mtf_data


def apply_mtf_filter(
    bot,
    symbol: str,
    signal: str,
    prob_final: float,
    ctx: dict,
    df_main: pd.DataFrame,
) -> tuple[float, bool, str]:
    if not bool(getattr(Config, "MTF_FILTER_ENABLED", False)):
        return prob_final, True, "MTF_DISABLED"

    mtf_data = fetch_mtf_data(bot, symbol)
    weight, reason = analyze_mtf_alignment(
        df_main,
        mtf_data.get("15m"),
        mtf_data.get("5m"),
        signal,
    )

    if isinstance(ctx, dict):
        ctx["mtf_weight"] = float(weight)
        ctx["mtf_reason"] = reason

    append_execution_event(
        bot,
        "MTF_FILTER",
        {
            "symbol": symbol,
            "side": signal,
            "weight": float(weight),
            "reason": reason,
            "prob_before": float(prob_final),
        },
    )

    if weight <= 0.0:
        return prob_final, False, f"MTF_VETO: {reason}"

    adjusted_prob = min(float(prob_final) * float(weight), 100.0)
    if adjusted_prob != prob_final:
        log = getattr(bot, "log", None)
        if callable(log):
            log(
                f"📊 {symbol}: MTF {reason} x{weight:.2f} "
                f"Prob {prob_final:.1f} → {adjusted_prob:.1f}"
            )
    return adjusted_prob, True, reason
