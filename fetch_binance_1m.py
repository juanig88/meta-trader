"""Download Binance monthly 1m klines into logs/binance/ as compressed npz. Read-only research.

Public bulk endpoint (data.binance.vision), no API key. One npz per symbol with
time (unix seconds), open, high, low, close, volume. Missing months are skipped
with a warning so a symbol listed mid-range still works.

    py fetch_binance_1m.py --start 2024-10 --end 2026-09
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

URL = "https://data.binance.vision/data/spot/monthly/klines/{sym}/1m/{sym}-1m-{ym}.zip"
OUT_DIR = Path("logs/binance")
# XBTFX name -> Binance spot USDT. Taken from the broker descriptions, not from returns.
# A 404 leaves the symbol out of the cross-section (it was not listed for the whole sample).
PAIRS = {
    "ADAUSD!": "ADAUSDT", "ALGUSD!": "ALGOUSDT", "ATMUSD!": "ATOMUSDT", "AVAUSD!": "AVAXUSDT",
    "AXSUSD!": "AXSUSDT", "BATUSD!": "BATUSDT", "BCHUSD!": "BCHUSDT", "BTCUSD!": "BTCUSDT",
    "CRVUSD!": "CRVUSDT", "DOGUSD!": "DOGEUSDT", "DOTUSD!": "DOTUSDT", "DSHUSD!": "DASHUSDT",
    "ETCUSD!": "ETCUSDT", "ETHUSD!": "ETHUSDT", "FILUSD!": "FILUSDT", "INCUSD!": "1INCHUSDT",
    "IOTUSD!": "IOTAUSDT", "LNKUSD!": "LINKUSDT", "LTCUSD!": "LTCUSDT", "NEOUSD!": "NEOUSDT",
    "NERUSD!": "NEARUSDT", "SANUSD!": "SANDUSDT", "SHBUSD!": "SHIBUSDT", "SOLUSD!": "SOLUSDT",
    "SUSUSD!": "SUSHIUSDT", "TRXUSD!": "TRXUSDT", "UNIUSD!": "UNIUSDT", "VECUSD!": "VETUSDT",
    "XLMUSD!": "XLMUSDT", "XMRUSD!": "XMRUSDT", "XRPUSD!": "XRPUSDT", "XTZUSD!": "XTZUSDT",
    "ZECUSD!": "ZECUSDT", "CAKUSD!": "CAKEUSDT", "GRMUSD!": "GRAMUSDT", "HYPUSD!": "HYPEUSDT",
    "AAVUSD!": "AAVEUSDT", "ALIUSD!": "ALICEUSDT", "ANKUSD!": "ANKRUSDT", "APEUSD!": "APEUSDT",
    "ARWUSD!": "ARUSDT", "BARUSD!": "HBARUSDT", "CEOUSD!": "CELOUSDT", "CHRUSD!": "CHRUSDT",
    "CHZUSD!": "CHZUSDT", "CNEUSD!": "C98USDT", "COMUSD!": "COMPUSDT", "COTUSD!": "COTIUSDT",
    "DYDUSD!": "DYDXUSDT", "EGLUSD!": "EGLDUSDT", "ENJUSD!": "ENJUSDT", "FLWUSD!": "FLOWUSDT",
    "GALUSD!": "GALAUSDT", "GMTUSD!": "GMTUSDT", "GRTUSD!": "GRTUSDT", "IMXUSD!": "IMXUSDT",
    "IOSUSD!": "IOSTUSDT", "KAVUSD!": "KAVAUSDT", "KNCUSD!": "KNCUSDT", "KSMUSD!": "KSMUSDT",
    "LPTUSD!": "LPTUSDT", "MANUSD!": "MANAUSDT", "ONTUSD!": "ONTUSDT", "PEPUSD!": "PEOPLEUSDT",
    "QTMUSD!": "QTUMUSDT", "RSRUSD!": "RSRUSDT", "RVNUSD!": "RVNUSDT", "SKLUSD!": "SKLUSDT",
    "SNXUSD!": "SNXUSDT", "TETUSD!": "THETAUSDT", "WOOUSD!": "WOOUSDT", "YFIUSD!": "YFIUSDT",
    "ZENUSD!": "ZENUSDT", "ZILUSD!": "ZILUSDT", "ZRXUSD!": "ZRXUSDT", "ICPUSD!": "ICPUSDT",
    "APTUSD!": "APTUSDT", "MLNUSD!": "MELANIAUSDT", "TRUUSD!": "TRUMPUSDT",
}


def months(start: str, end: str) -> list[str]:
    y0, m0 = (int(x) for x in start.split("-"))
    y1, m1 = (int(x) for x in end.split("-"))
    out = []
    y, m = y0, m0
    while (y, m) <= (y1, m1):
        out.append(f"{y:04d}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def to_unix_seconds(raw: int) -> int:
    """Binance switched open_time to microseconds; older dumps are milliseconds."""
    v = int(raw)
    if v > 10**15:
        return v // 1_000_000
    if v > 10**12:
        return v // 1_000
    return v


def fetch_month(pair: str, ym: str) -> np.ndarray | None:
    url = URL.format(sym=pair, ym=ym)
    req = urllib.request.Request(url, headers={"User-Agent": "meta-trader-research/1.0"})
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
        parts = line.split(",")
        if len(parts) < 6 or not parts[0].strip().lstrip("-").isdigit():
            continue
        rows.append((to_unix_seconds(parts[0]), float(parts[1]), float(parts[2]),
                     float(parts[3]), float(parts[4]), float(parts[5])))
    if not rows:
        return None
    return np.array(rows, dtype=np.float64)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2024-10")
    ap.add_argument("--end", default="2026-09")
    args = ap.parse_args()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    yms = months(args.start, args.end)

    for symbol, pair in sorted(PAIRS.items()):
        out = OUT_DIR / f"{pair}_1m.npz"
        if out.exists():
            print(f"skip {pair} (ya esta)", flush=True)
            continue
        chunks, missing = [], []
        for ym in yms:
            try:
                part = fetch_month(pair, ym)
            except Exception as exc:
                print(f"  {pair} {ym} error: {type(exc).__name__} {exc}", file=sys.stderr, flush=True)
                missing.append(ym)
                continue
            if part is None:
                missing.append(ym)
                continue
            chunks.append(part)
        if not chunks:
            print(f"FAIL {pair}: sin datos", file=sys.stderr, flush=True)
            continue
        a = np.concatenate(chunks)
        a = a[np.argsort(a[:, 0])]
        _, keep = np.unique(a[:, 0], return_index=True)
        a = a[np.sort(keep)]
        np.savez_compressed(
            out,
            time=a[:, 0].astype(np.int64),
            open=a[:, 1].astype(np.float32), high=a[:, 2].astype(np.float32),
            low=a[:, 3].astype(np.float32), close=a[:, 4].astype(np.float32),
            volume=a[:, 5].astype(np.float32),
        )
        mb = out.stat().st_size / 1e6
        print(f"{pair}: {len(a)} velas, {mb:.1f} MB, faltan {len(missing)} meses {missing[:3]}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
