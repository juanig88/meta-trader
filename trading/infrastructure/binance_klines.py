"""Public Binance spot klines. No API key, no orders.

Each row is open time (ms), open, high, low, close, base volume, trade count, quote volume
(USDT traded) and taker-buy quote volume. ``update_csv`` keeps a local history file current.
"""

from __future__ import annotations

import csv
import json
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

KLINES_URL = "https://api.binance.com/api/v3/klines"
LIMIT = 1000


def fetch_klines(
    symbol: str,
    interval: str,
    start_ms: int,
    end_ms: int,
    *,
    pause_s: float = 0.15,
    timeout_s: float = 20.0,
) -> list[dict[str, Any]]:
    """Closed candles from ``start_ms`` up to, but not past, ``end_ms``."""
    if int(end_ms) <= int(start_ms):
        return []
    sym = str(symbol).upper()
    out: list[dict[str, Any]] = []
    cursor = int(start_ms)
    while cursor < int(end_ms):
        query = (
            f"{KLINES_URL}?symbol={sym}&interval={interval}"
            f"&startTime={cursor}&endTime={int(end_ms)}&limit={LIMIT}"
        )
        batch = _get(query, timeout_s)
        if not batch:
            break
        for row in batch:
            open_ms = int(row[0])
            if open_ms >= int(end_ms):
                continue
            out.append({
                "open_ms": open_ms,
                "open": float(row[1]),
                "high": float(row[2]),
                "low": float(row[3]),
                "close": float(row[4]),
                "volume": float(row[5]),
                "trades": int(row[8]),
                "quote_volume": float(row[7]),
                "taker_buy_quote": float(row[10]),
            })
        next_open = int(batch[-1][0]) + 1
        if next_open <= cursor:
            break
        cursor = next_open
        if len(batch) < LIMIT:
            break
        time.sleep(pause_s)
    dedup: dict[int, dict[str, Any]] = {}
    for row in out:
        dedup[int(row["open_ms"])] = row
    return [dedup[k] for k in sorted(dedup)]


def _get(url: str, timeout_s: float) -> list[Any]:
    request = urllib.request.Request(url, headers={"User-Agent": "meta-trader-research"})
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:200]
        raise RuntimeError(f"binance klines {exc.code}: {detail}") from exc
    if not isinstance(payload, list):
        raise RuntimeError("binance klines: respuesta inesperada")
    return payload


FIELDS = ("open_ms", "open", "high", "low", "close", "volume", "trades", "quote_volume", "taker_buy_quote")
INTERVAL_MS = {"15m": 900_000, "1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000}


def read_csv(path: Path) -> list[dict[str, Any]]:
    try:
        with open(path, newline="", encoding="utf-8") as fh:
            return [{k: (int(float(v)) if k in ("open_ms", "trades") else float(v)) for k, v in r.items()}
                    for r in csv.DictReader(fh)]
    except FileNotFoundError:
        return []


def update_csv(path: Path, symbol: str, interval: str, since_ms: int, now_ms: int | None = None) -> list[dict[str, Any]]:
    """Append the closed candles missing from ``path`` (from ``since_ms`` on first run); returns all rows."""
    rows = read_csv(path)
    step = INTERVAL_MS[interval]
    now = int(now_ms if now_ms is not None else time.time() * 1000)
    start = rows[-1]["open_ms"] + step if rows else int(since_ms)
    end = now - now % step  # only candles already closed
    new = fetch_klines(symbol, interval, start, end) if end > start else []
    if new:
        path.parent.mkdir(parents=True, exist_ok=True)
        fresh = not path.exists() or not rows
        with open(path, "a", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=FIELDS)
            if fresh:
                w.writeheader()
            for r in new:
                w.writerow({k: r[k] for k in FIELDS})
        rows += new
    return rows
