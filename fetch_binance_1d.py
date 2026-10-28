"""Daily Binance spot klines since listing for the XBTFX crypto universe -> logs/binance/daily/. Read-only research.

Public REST /api/v3/klines (no API key). Universe = fetch_binance_1m.PAIRS (today's XBTFX list:
survivorship bias, delisted coins are not in it).

    py fetch_binance_1d.py --end 2026-09-30
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from pathlib import Path

import pandas as pd

from fetch_binance_1m import PAIRS, to_unix_seconds

URL = "https://api.binance.com/api/v3/klines?symbol={sym}&interval=1d&startTime={start}&limit=1000"
OUT_DIR = Path("logs/binance/daily")


def fetch(pair: str, end_ms: int) -> pd.DataFrame | None:
    rows, start = [], 1500000000000
    while start < end_ms:
        req = urllib.request.Request(URL.format(sym=pair, start=start), headers={"User-Agent": "meta-trader-research/1.0"})
        try:
            data = json.loads(urllib.request.urlopen(req, timeout=60).read())
        except urllib.error.HTTPError as exc:
            if exc.code == 400:
                return None
            raise
        if not data:
            break
        rows += data
        start = int(data[-1][0]) + 86_400_000
        time.sleep(0.15)
    if not rows:
        return None
    df = pd.DataFrame([(to_unix_seconds(r[0]), *map(float, r[1:6])) for r in rows],
                      columns=["time", "open", "high", "low", "close", "volume"])
    df["date"] = pd.to_datetime(df.time, unit="s")
    df = df.drop(columns="time").drop_duplicates("date").set_index("date").sort_index()
    return df[df.index <= pd.Timestamp(end_ms, unit="ms")]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--end", default="2026-09-30")
    args = ap.parse_args()
    end_ms = int(pd.Timestamp(args.end).timestamp() * 1000)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for symbol, pair in sorted(PAIRS.items()):
        out = OUT_DIR / f"{pair}_1d.csv"
        if out.exists():
            continue
        try:
            df = fetch(pair, end_ms)
        except Exception as exc:
            print(f"FAIL {pair}: {type(exc).__name__} {exc}", file=sys.stderr, flush=True)
            continue
        if df is None or df.empty:
            print(f"FAIL {pair}: sin datos", file=sys.stderr, flush=True)
            continue
        df.to_csv(out, index_label="date")
        print(f"{pair}: {len(df)} dias {df.index[0].date()} -> {df.index[-1].date()}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
