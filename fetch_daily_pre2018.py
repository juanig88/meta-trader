"""Daily history before Binance for an extra out-of-sample cycle -> logs/binance/daily_pre2018/. Read-only research.

BTC: Bitstamp public OHLC (2011 ->). LTC/XRP/DOGE/DASH: Yahoo (2014-09 ->). Survivors only.

    py fetch_daily_pre2018.py
"""

from __future__ import annotations

import json
import time
import urllib.request
from pathlib import Path

import pandas as pd
import yfinance as yf

OUT_DIR = Path("logs/binance/daily_pre2018")
BITSTAMP = "https://www.bitstamp.net/api/v2/ohlc/btcusd/?step=86400&limit=1000&start={start}"
YAHOO = {"LTCUSDT": "LTC-USD", "XRPUSDT": "XRP-USD", "DOGEUSDT": "DOGE-USD", "DASHUSDT": "DASH-USD"}
END = "2026-09-30"


def bitstamp_btc() -> pd.DataFrame:
    rows, start = [], 1314835200  # 2011-09-01
    end = int(pd.Timestamp(END).timestamp())
    while start < end:
        req = urllib.request.Request(BITSTAMP.format(start=start), headers={"User-Agent": "meta-trader-research/1.0"})
        data = json.loads(urllib.request.urlopen(req, timeout=60).read())["data"]["ohlc"]
        if not data:
            break
        rows += data
        nxt = int(data[-1]["timestamp"]) + 86400
        if nxt <= start:
            break
        start = nxt
        time.sleep(0.3)
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df.timestamp.astype(int), unit="s")
    df = df.set_index("date")[["open", "high", "low", "close", "volume"]].astype(float)
    return df[~df.index.duplicated()].sort_index()


def yahoo(tk: str) -> pd.DataFrame:
    d = yf.download(tk, start="2014-09-01", end=END, interval="1d", progress=False, auto_adjust=False)
    if isinstance(d.columns, pd.MultiIndex):
        d.columns = d.columns.get_level_values(0)
    d.index = pd.to_datetime(d.index).tz_localize(None)
    d.columns = [c.lower() for c in d.columns]
    return d[["open", "high", "low", "close", "volume"]].dropna()


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    b = bitstamp_btc()
    b.to_csv(OUT_DIR / "BTCUSDT_1d.csv", index_label="date")
    print("BTC bitstamp", len(b), b.index[0].date(), b.index[-1].date())
    for pair, tk in YAHOO.items():
        d = yahoo(tk)
        d.to_csv(OUT_DIR / f"{pair}_1d.csv", index_label="date")
        print(pair, len(d), d.index[0].date(), d.index[-1].date())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
