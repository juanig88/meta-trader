"""Telegram heads-up when a crypto headline may push BTC up or down. Notifies only.

Polls CoinDesk, Cointelegraph, Decrypt and The Block RSS, asks Gemini about new
BTC-relevant headlines (keyword fallback if Gemini fails) and sends one Telegram message
per cycle with the non-neutral ones. Rule helpers: trading/domain/news_alert.py

    python news_watch.py             # loop every BOT_NEWS_POLL_MIN minutes
    python news_watch.py --once      # one cycle
    python news_watch.py --dry-run   # print instead of sending to Telegram
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from trading.domain.news_alert import (
    MODEL_SYSTEM, NEUTRAL, Headline, Verdict, alert_text, fresh_relevant, keyword_verdict, model_prompt,
    parse_model_verdicts, parse_rss,
)

FEEDS = {
    "CoinDesk": "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "Cointelegraph": "https://cointelegraph.com/rss",
    "Decrypt": "https://decrypt.co/feed",
    "The Block": "https://www.theblock.co/rss.xml",
}
PRICE_URL = "https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT"
SEEN_KEEP_DAYS = 3


def _env(name: str, default: Optional[str] = None) -> Optional[str]:
    v = os.environ.get(name)
    return default if v is None or v.strip() == "" else v.strip()


def _env_float(name: str, default: float) -> float:
    try:
        return float(_env(name, str(default)))
    except ValueError:
        return default


def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv

        load_dotenv(override=False)
    except ImportError:
        pass


def _get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (news_watch)"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return resp.read()


def _btc_price() -> Optional[float]:
    try:
        return float(json.loads(_get(PRICE_URL))["price"])
    except Exception:
        return None


def _read_state(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _write_state(path: Path, state: dict) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(state, indent=1, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def _verdicts(items: list[Headline], already: list[str]) -> dict[str, Verdict]:
    key = _env("GEMINI_API_KEY") or _env("GOOGLE_API_KEY")
    got: dict[str, Verdict] = {}
    if key:
        from trading.application.nightly_review import call_gemini

        try:
            reply = call_gemini(MODEL_SYSTEM, model_prompt(items, already), key, _env("GEMINI_MODEL", "gemini-2.5-flash"))
            got = parse_model_verdicts(reply["text"], [h.key for h in items])
        except Exception as exc:
            logging.warning("gemini failed, keyword fallback: %s", exc)
    return {h.key: got.get(h.key) or keyword_verdict(h) for h in items}


def run_cycle(*, dry_run: bool, state_path: Path, log_path: Path) -> int:
    now = datetime.now(timezone.utc)
    state = _read_state(state_path)
    seen: dict[str, str] = state.get("seen") or {}
    items: list[Headline] = []
    for name, url in FEEDS.items():
        try:
            items += parse_rss(name, _get(url))
        except Exception as exc:
            logging.warning("%s feed failed: %s", name, exc)
    new = fresh_relevant(items, set(seen), now, _env_float("BOT_NEWS_MAX_AGE_MIN", 90))
    new = list({h.key: h for h in new}.values())[: int(_env_float("BOT_NEWS_MAX_PER_CYCLE", 25))]
    sent = 0
    if new:
        already = [t for t in state.get("alerted_titles") or []][-15:]
        verdicts = _verdicts(new, already)
        min_strength = int(_env_float("BOT_NEWS_MIN_STRENGTH", 2))
        hits = [(h, v) for h, v in ((h, verdicts[h.key]) for h in new)
                if v.impact != NEUTRAL and (v.strength >= min_strength or not v.by_model)]
        hits.sort(key=lambda hv: -hv[1].strength)
        hits = hits[:5]
        for h, v in ((h, verdicts[h.key]) for h in new):
            with log_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps({"ts": now.isoformat(timespec="seconds"), "source": h.source, "title": h.title,
                                    "impact": v.impact, "strength": v.strength, "by_model": v.by_model,
                                    "reason": v.reason, "alerted": any(h is x for x, _ in hits)},
                                   ensure_ascii=False) + "\n")
        if hits:
            text = alert_text(hits, _btc_price())
            if dry_run:
                print(text)
            else:
                from telegram_notify import send_message_now

                ok, why = send_message_now(text)
                if not ok:
                    logging.warning("telegram failed: %s", why)
            sent = len(hits)
            state["alerted_titles"] = (already + [h.title for h, _ in hits])[-30:]
        for h in new:
            seen[h.key] = now.isoformat(timespec="seconds")
    cutoff = (now - timedelta(days=SEEN_KEEP_DAYS)).isoformat(timespec="seconds")
    state["seen"] = {k: v for k, v in seen.items() if v >= cutoff}
    if not dry_run:
        _write_state(state_path, state)
    logging.info("news cycle: %d titulares, %d nuevos relevantes, %d avisados", len(items), len(new), sent)
    return sent


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--once", action="store_true", help="one cycle and exit")
    ap.add_argument("--dry-run", action="store_true", help="print instead of Telegram; do not mark as seen")
    args = ap.parse_args()

    _load_dotenv()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # Windows console + emojis in --dry-run
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    state_path = Path(_env("BOT_NEWS_STATE", "news_seen.json"))
    log_path = Path(_env("BOT_NEWS_LOG", "news_alerts.log"))
    poll = _env_float("BOT_NEWS_POLL_MIN", 3.0) * 60
    while True:
        try:
            run_cycle(dry_run=args.dry_run, state_path=state_path, log_path=log_path)
        except Exception:
            logging.exception("news cycle failed")
        if args.once:
            return 0
        time.sleep(poll)


if __name__ == "__main__":
    sys.exit(main())
