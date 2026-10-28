"""Download Binance monthly 1m klines with taker-buy volume. Read-only research.

Same public endpoint as ``fetch_binance_1m.py`` but keeps the columns the flow
test needs: time, open, close, volume, taker_buy_base, trades. One npz per pair
in ``logs/binance/flow/``.

    py fetch_binance_1m_flow.py --pair BTCUSDT --start 2024-10 --end 2026-09
"""

from __future__ import annotations

import argparse
import io
import sys
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

import numpy as np

from fetch_binance_1m import URL, months, to_unix_seconds

OUT_DIR = Path("logs/binance/flow")


def fetch_month(pair: str, ym: str) -> np.ndarray | None:
    req = urllib.request.Request(URL.format(sym=pair, ym=ym), headers={"User-Agent": "meta-trader-research/1.0"})
    try:
        raw = urllib.request.urlopen(req, timeout=120).read()
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        text = z.read(z.namelist()[0]).decode()
    rows = []
    for line in text.splitlines():
        p = line.split(",")
        if len(p) < 10 or not p[0].strip().lstrip("-").isdigit():
            continue
        rows.append((to_unix_seconds(p[0]), float(p[1]), float(p[4]), float(p[5]), float(p[9]), float(p[8])))
    return np.array(rows, dtype=float) if rows else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pair", default="BTCUSDT")
    ap.add_argument("--start", default="2024-10")
    ap.add_argument("--end", default="2026-09")
    args = ap.parse_args()
    parts = []
    for ym in months(args.start, args.end):
        block = fetch_month(args.pair, ym)
        if block is None:
            print(f"warning: {args.pair} {ym} no existe", file=sys.stderr)
            continue
        parts.append(block)
        print(f"{args.pair} {ym}: {len(block)} velas")
    if not parts:
        return 1
    a = np.concatenate(parts)
    a = a[np.argsort(a[:, 0], kind="stable")]
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"{args.pair}_1m_flow.npz"
    np.savez_compressed(out, time=a[:, 0].astype(np.int64), open=a[:, 1], close=a[:, 2],
                        volume=a[:, 3], taker_buy=a[:, 4], trades=a[:, 5])
    print(f"-> {out} ({len(a)} velas)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
