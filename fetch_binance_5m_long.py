"""BTC/ETH Binance 1m klines since 2017-09, resampled to 5m, into logs/binance/long/. Read-only research.

Reuses fetch_binance_1m.fetch_month (public data.binance.vision, no API key).

    py fetch_binance_5m_long.py --start 2017-09 --end 2026-09
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from fetch_binance_1m import fetch_month, months

OUT_DIR = Path("logs/binance/long")


def to_5m(a: np.ndarray) -> pd.DataFrame:
    df = pd.DataFrame(a, columns=["time", "open", "high", "low", "close", "volume"])
    df.index = pd.to_datetime(df.time.astype(np.int64), unit="s")
    return df.resample("5min").agg({"open": "first", "high": "max", "low": "min",
                                    "close": "last", "volume": "sum"}).dropna()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2017-09")
    ap.add_argument("--end", default="2026-09")
    ap.add_argument("--pairs", default="BTCUSDT,ETHUSDT")
    args = ap.parse_args()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for pair in args.pairs.split(","):
        out = OUT_DIR / f"{pair}_5m.csv.gz"
        if out.exists():
            print(f"skip {pair}", flush=True)
            continue
        parts, missing = [], []
        for ym in months(args.start, args.end):
            try:
                a = fetch_month(pair, ym)
            except Exception as exc:
                print(f"  {pair} {ym} error: {type(exc).__name__} {exc}", file=sys.stderr, flush=True)
                missing.append(ym)
                continue
            if a is None:
                missing.append(ym)
                continue
            parts.append(to_5m(a))
            print(f"  {pair} {ym} ok", flush=True)
        df = pd.concat(parts).sort_index()
        df = df[~df.index.duplicated()]
        df.to_csv(out, index_label="date")
        print(f"{pair}: {len(df)} velas 5m {df.index[0]} -> {df.index[-1]}, faltan {missing}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
