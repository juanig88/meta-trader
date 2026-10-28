"""Public Binance spot klines. No API key, no orders.

Each row is open time (ms), open, high, low, close, base volume, trade count.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
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
