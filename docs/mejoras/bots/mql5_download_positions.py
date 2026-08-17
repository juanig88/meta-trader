"""
Descarga CSVs /export/positions usando cookies de sesión MQL5.

Requiere: docs/mejoras/bots/mql5_crawl/cookies.txt (gitignored)

  python docs/mejoras/bots/mql5_download_positions.py --top 40
  python docs/mejoras/bots/mql5_download_positions.py --ids 2234595,2329290
"""
from __future__ import annotations

import argparse
import json
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
CRAWL = HERE / "mql5_crawl"
COOKIES = CRAWL / "cookies.txt"
OUT = CRAWL / "positions_csv"
INBOX = HERE / "inbox_positions"
REPORT = CRAWL / "signals_public_report.md"
JSONL = CRAWL / "signals_public.jsonl"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"


def load_cookie_header() -> str:
    if not COOKIES.exists():
        raise SystemExit(f"Missing {COOKIES} — pegá Cookie header ahí.")
    parts: list[str] = []
    for line in COOKIES.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.lower().startswith("cookie:"):
            line = line.split(":", 1)[1].strip()
        parts.append(line)
    if not parts:
        raise SystemExit("cookies.txt vacío")
    return "; ".join(parts)


def ids_from_report(n: int) -> list[int]:
    if not REPORT.exists():
        return []
    ids = [int(x) for x in re.findall(r"/signals/(\d{5,})", REPORT.read_text(encoding="utf-8"))]
    # report table order ≈ ranked; keep unique preserving order
    out: list[int] = []
    seen: set[int] = set()
    for i in ids:
        if i in seen:
            continue
        seen.add(i)
        out.append(i)
        if len(out) >= n:
            break
    return out


def download(signal_id: int, cookie: str, sleep: float, mirror_inbox: bool = False) -> Path | None:
    OUT.mkdir(parents=True, exist_ok=True)
    url = f"https://www.mql5.com/en/signals/{signal_id}/export/positions"
    dest = OUT / f"{signal_id}.positions.csv"
    if dest.exists() and dest.stat().st_size > 200:
        print(f"skip existing {dest.name}")
        return dest
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": UA,
            "Cookie": cookie,
            "Accept": "text/csv,application/octet-stream,*/*",
            "Referer": f"https://www.mql5.com/en/signals/{signal_id}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            data = r.read()
            final = str(r.geturl())
            ctype = r.headers.get("Content-Type", "")
    except urllib.error.HTTPError as e:
        print(f"{signal_id} HTTP {e.code}")
        time.sleep(sleep)
        return None
    except Exception as e:
        print(f"{signal_id} ERR {e}")
        time.sleep(sleep * 2)
        return None

    if "auth_login" in final or b"Sign in" in data[:500]:
        print(f"{signal_id} LOGIN WALL — cookies vencidas?")
        return None
    if b"Time;Type;Volume" not in data[:200] and b"Time,Type,Volume" not in data[:200]:
        # BOM + Time
        head = data[:120].decode("utf-8", "replace")
        if "Time" not in head or "Symbol" not in head:
            print(f"{signal_id} not CSV ({ctype}) head={head[:80]!r}")
            time.sleep(sleep)
            return None

    dest.write_bytes(data)
    if mirror_inbox:
        INBOX.mkdir(parents=True, exist_ok=True)
        (INBOX / dest.name).write_bytes(data)
    print(f"OK {signal_id} {len(data)} bytes -> {dest.name}")
    time.sleep(sleep)
    return dest


def ids_from_seeds_and_jsonl() -> list[int]:
    ids: list[int] = []
    seeds = CRAWL / "seeds.json"
    if seeds.exists():
        ids.extend(int(x) for x in json.loads(seeds.read_text(encoding="utf-8")))
    if JSONL.exists():
        for line in JSONL.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                ids.append(int(json.loads(line)["signal_id"]))
            except Exception:
                continue
    return ids


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=30)
    ap.add_argument("--ids", type=str, default="")
    ap.add_argument("--sleep", type=float, default=2.5)
    ap.add_argument("--xau-only", action="store_true")
    ap.add_argument("--all", action="store_true", help="todos los IDs de seeds.json + jsonl")
    ap.add_argument("--no-inbox", action="store_true", help="solo positions_csv/")
    args = ap.parse_args()

    cookie = load_cookie_header()
    ids: list[int] = []
    if args.ids.strip():
        ids.extend(int(x) for x in args.ids.split(",") if x.strip().isdigit())
    if args.all:
        ids.extend(ids_from_seeds_and_jsonl())
    else:
        ids.extend(ids_from_report(args.top))
        # research priors
        ids.extend([2234595, 2362973, 2381521, 2278370, 2329290])

    if args.xau_only and JSONL.exists():
        xau_ids = []
        for line in JSONL.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            top = str(r.get("top_symbol") or "")
            if "XAU" in top.upper():
                xau_ids.append(int(r["signal_id"]))
        ids = xau_ids[: max(args.top, 500)] + ids

    # unique preserve order
    seen: set[int] = set()
    uniq: list[int] = []
    for i in ids:
        if i not in seen:
            seen.add(i)
            uniq.append(i)

    # skip already downloaded
    pending = []
    for sid in uniq:
        dest = OUT / f"{sid}.positions.csv"
        if dest.exists() and dest.stat().st_size > 200:
            continue
        pending.append(sid)

    print(f"queue {len(pending)} pending / {len(uniq)} total (have {len(uniq)-len(pending)})")
    ok = 0
    fail = 0
    mirror = not args.no_inbox
    for i, sid in enumerate(pending, 1):
        path = download(sid, cookie, args.sleep, mirror_inbox=mirror)
        if path:
            ok += 1
            fail = 0
        else:
            fail += 1
            if fail >= 12:
                print("too many consecutive failures — abort (cookies/ban?)")
                break
        if i % 25 == 0:
            print(f"progress {i}/{len(pending)} ok={ok}")
    print(f"done ok={ok}/{len(pending)} -> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
