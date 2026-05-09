from __future__ import annotations

from typing import Optional

import pandas as pd


def _fetch_timeframe(bot, symbol: str, timeframe: str) -> Optional[pd.DataFrame]:
    try:
        data_service = getattr(bot, "data_service", None)
        if data_service is None:
            return None
        df = data_service.fetch_and_update_data(symbol, timeframe, fast_mode=True)
        if isinstance(df, pd.DataFrame) and not df.empty:
            return df
    except Exception as error:
        log = getattr(bot, "log", None)
        if callable(log):
            log(f"⚠️ {symbol}: MTF fetch {timeframe} ignorado: {error}")
    return None


def fetch_mtf_data(bot, symbol: str) -> dict[str, Optional[pd.DataFrame]]:
    return {
        "15m": _fetch_timeframe(bot, symbol, "15m"),
        "5m": _fetch_timeframe(bot, symbol, "5m"),
    }
