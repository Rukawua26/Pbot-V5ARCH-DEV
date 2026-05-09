from __future__ import annotations

from typing import Optional

import pandas as pd


def _is_usable_df(df: Optional[pd.DataFrame], min_rows: int = 5) -> bool:
    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        return False
    return "close" in df.columns and len(df) >= min_rows


def _infer_direction(df: Optional[pd.DataFrame]) -> str:
    if not _is_usable_df(df):
        return "UNKNOWN"
    closes = pd.to_numeric(df["close"], errors="coerce").dropna()
    if len(closes) < 5:
        return "UNKNOWN"

    first = float(closes.iloc[-5])
    last = float(closes.iloc[-1])
    if first <= 0:
        return "UNKNOWN"

    change_pct = (last - first) / first
    if change_pct >= 0.002:
        return "BUY"
    if change_pct <= -0.002:
        return "SELL"
    return "NEUTRAL"


def _opposes_signal(direction: str, signal: str) -> bool:
    return (signal == "BUY" and direction == "SELL") or (
        signal == "SELL" and direction == "BUY"
    )


def _confirms_signal(direction: str, signal: str) -> bool:
    return direction == signal


def analyze_mtf_alignment(
    df_1h: pd.DataFrame,
    df_15m: Optional[pd.DataFrame],
    df_5m: Optional[pd.DataFrame],
    signal: str,
) -> tuple[float, str]:
    """Evaluate 15m/5m alignment as confirmation for the 1h signal owner.

    The 15m timeframe can veto because it represents setup quality. The 5m
    timeframe only adjusts confidence because it is used as timing context.
    """
    signal = str(signal or "").upper()
    if signal not in {"BUY", "SELL"}:
        return 1.0, "MTF_PASSTHROUGH_UNSUPPORTED_SIGNAL"

    has_15m = _is_usable_df(df_15m)
    has_5m = _is_usable_df(df_5m)
    if not has_15m and not has_5m:
        return 1.0, "MTF_PASSTHROUGH_NO_INTRADAY_DATA"

    direction_15m = _infer_direction(df_15m)
    direction_5m = _infer_direction(df_5m)

    if _opposes_signal(direction_15m, signal):
        return 0.0, f"MTF_VETO_15M_{direction_15m}_VS_{signal}"

    if has_15m and direction_15m == "NEUTRAL":
        if has_5m:
            if _confirms_signal(direction_5m, signal):
                return 0.95, "MTF_PARTIAL_15M_NEUTRAL_5M_ALIGNED"
            if _opposes_signal(direction_5m, signal):
                return 0.60, "MTF_PARTIAL_15M_NEUTRAL_5M_CONFLICT"
        return 0.85, "MTF_PARTIAL_15M_NEUTRAL"

    confirms_15m = _confirms_signal(direction_15m, signal)
    confirms_5m = _confirms_signal(direction_5m, signal)

    if confirms_15m and confirms_5m:
        return 1.05, "MTF_ALIGNED_15M_5M"

    if confirms_15m and _opposes_signal(direction_5m, signal):
        return 0.75, f"MTF_TIMING_5M_{direction_5m}_VS_{signal}"

    if confirms_15m:
        return 1.0, "MTF_ALIGNED_15M"

    if confirms_5m:
        return 0.90, "MTF_PARTIAL_5M_ONLY"

    return 1.0, "MTF_PASSTHROUGH_INCONCLUSIVE"
