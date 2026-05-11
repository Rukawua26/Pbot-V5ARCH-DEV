#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import ccxt
import pandas as pd

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.data_service import DataService


def export_validation_candles(
    *,
    symbol: str,
    timeframe: str,
    days: int,
    output: Path,
) -> Path:
    exchange = ccxt.binance({"enableRateLimit": True})
    data_service = DataService(exchange)
    df = data_service.download_historical_data(symbol, timeframe, days)
    export_df = df.copy()
    if pd.api.types.is_numeric_dtype(export_df["time"]):
        export_df["time"] = pd.to_datetime(export_df["time"], unit="ms", utc=True)
    output.parent.mkdir(parents=True, exist_ok=True)
    export_df.to_csv(output, index=False)
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description="Export OHLCV candles for strategy validation")
    parser.add_argument("--symbol", default="BTC/USDT")
    parser.add_argument("--timeframe", default="1h")
    parser.add_argument("--days", type=int, default=180)
    parser.add_argument("--output", default="data_storage/validation_candles.csv")
    args = parser.parse_args()

    output = export_validation_candles(
        symbol=args.symbol,
        timeframe=args.timeframe,
        days=args.days,
        output=Path(args.output),
    )
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
