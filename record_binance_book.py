"""Graba el libro de órdenes público de Binance spot para medir el desbalance más adelante.

Solo lectura: un GET a /api/v3/depth por segundo (peso 1 de 6000 por minuto), sin
clave ni órdenes. Un archivo JSONL comprimido por día UTC en ``logs/binance/book/``:
cada línea es ``[ts_ms, last_update_id, bids, asks]`` con 20 niveles ``[precio, cantidad]``.

    python record_binance_book.py                  # BTCUSDT, 1 s
    python record_binance_book.py --pair ETHUSDT --every 2
"""

from __future__ import annotations

import argparse
import gzip
import json
import logging
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

URL = "https://api.binance.com/api/v3/depth?symbol={pair}&limit={levels}"
OUT_DIR = Path("logs/binance/book")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pair", default="BTCUSDT")
    ap.add_argument("--every", type=float, default=1.0)
    ap.add_argument("--levels", type=int, default=20)
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    url = URL.format(pair=args.pair, levels=args.levels)
    day, fh, rows, errors = None, None, 0, 0
    next_t = time.monotonic()
    try:
        while True:
            now = datetime.now(timezone.utc)
            if now.date() != day:
                if fh:
                    fh.close()
                    logging.info("dia %s cerrado: %d filas, %d errores", day, rows, errors)
                day, rows, errors = now.date(), 0, 0
                fh = gzip.open(OUT_DIR / f"{args.pair}_{day.isoformat()}.jsonl.gz", "at", encoding="utf-8")
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "meta-trader-research/1.0"})
                book = json.loads(urllib.request.urlopen(req, timeout=5).read())
                fh.write(json.dumps([int(time.time() * 1000), book["lastUpdateId"], book["bids"], book["asks"]],
                                    separators=(",", ":")) + "\n")
                rows += 1
                if rows % 3600 == 0:
                    fh.flush()
                    logging.info("%s %d filas hoy", args.pair, rows)
            except (urllib.error.URLError, TimeoutError, ValueError, KeyError) as exc:
                errors += 1
                if isinstance(exc, urllib.error.HTTPError) and exc.code in (418, 429):
                    logging.warning("rate limit %s, espero 60 s", exc.code)
                    time.sleep(60)
                elif errors % 60 == 1:
                    logging.warning("error %s (%d hoy)", exc, errors)
            next_t += args.every
            time.sleep(max(0.0, next_t - time.monotonic()))
            if next_t < time.monotonic() - 10:
                next_t = time.monotonic()
    except KeyboardInterrupt:
        pass
    finally:
        if fh:
            fh.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
