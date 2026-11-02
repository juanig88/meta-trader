"""Crypto headline alerts for the user: what might push BTC up or down.

Pure helpers for ``news_watch.py``: parse RSS, keep BTC-relevant headlines, read the
model's verdict (or a keyword fallback when the model is unavailable) and format the
Telegram text. It notifies only; nothing here trades. Runbook: trading/docs/IMPULSO_M1.md
"""

from __future__ import annotations

import hashlib
import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Optional, Sequence

UP = "sube"
DOWN = "baja"
NEUTRAL = "neutral"

RELEVANT = re.compile(
    r"\b(bitcoin|btc|crypto|binance|coinbase|exchange|etf|sec|cftc|fed|fomc|rate|inflation|cpi|"
    r"hack|exploit|breach|stolen|liquidat|stablecoin|tether|usdt|usdc|treasury|tariff|ban|"
    r"strategy|saylor|blackrock|whale|outflow|inflow|halt|withdrawal)",
    re.IGNORECASE,
)
_DOWN_WORDS = re.compile(r"\b(hack|exploit|breach|stolen|ban|lawsuit|sues|crackdown|outflow|selloff|"
                         r"plunge|crash|liquidat|insolven|halt|freez)", re.IGNORECASE)
_UP_WORDS = re.compile(r"\b(approv|inflow|buy(s|ing)?|adopt|rate cut|reserve|rall(y|ies)|surge|record high)",
                       re.IGNORECASE)


@dataclass(frozen=True)
class Headline:
    source: str
    title: str
    link: str
    published: Optional[datetime]
    summary: str = ""

    @property
    def key(self) -> str:
        return hashlib.sha1((self.link or self.title).encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class Verdict:
    key: str
    impact: str
    strength: int
    reason: str
    by_model: bool


def parse_rss(source: str, raw: bytes) -> list[Headline]:
    out = []
    for it in ET.fromstring(raw).iter("item"):
        title = (it.findtext("title") or "").strip()
        if not title:
            continue
        when = None
        try:
            when = parsedate_to_datetime(it.findtext("pubDate") or "")
            if when.tzinfo is None:
                when = when.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            pass
        summary = re.sub(r"<[^>]+>", " ", it.findtext("description") or "")
        out.append(Headline(source, title, (it.findtext("link") or "").strip(), when,
                            re.sub(r"\s+", " ", summary).strip()[:300]))
    return out


def fresh_relevant(items: Sequence[Headline], seen: set[str], now: datetime, max_age_min: float) -> list[Headline]:
    """New, recent and BTC-relevant headlines. Undated items count as fresh."""
    out = []
    for h in items:
        if h.key in seen:
            continue
        if h.published is not None and (now - h.published).total_seconds() > max_age_min * 60:
            continue
        if RELEVANT.search(f"{h.title} {h.summary}"):
            out.append(h)
    return out


def keyword_verdict(h: Headline) -> Verdict:
    text = f"{h.title} {h.summary}"
    down, up = len(_DOWN_WORDS.findall(text)), len(_UP_WORDS.findall(text))
    if down > up:
        return Verdict(h.key, DOWN, 1, "palabras de riesgo en el titular", False)
    if up > down:
        return Verdict(h.key, UP, 1, "palabras positivas en el titular", False)
    return Verdict(h.key, NEUTRAL, 0, "", False)


def model_prompt(items: Sequence[Headline], already_alerted: Sequence[str] = ()) -> str:
    rows = [{"id": h.key, "fuente": h.source, "titulo": h.title, "resumen": h.summary} for h in items]
    prev = ("\nYa avisados hace poco (si un titular nuevo es la misma noticia sin un dato nuevo, "
            "poné neutral):\n- " + "\n- ".join(already_alerted) + "\n") if already_alerted else ""
    return ("Titulares de cripto de los últimos minutos. Para cada uno, ¿qué efecto probable tiene "
            "sobre el precio de BTC en las próximas horas?\n"
            "Respondé SOLO un array JSON: "
            '[{"id": "...", "impacto": "sube|baja|neutral", "fuerza": 1-3, "por_que": "una frase en castellano"}]\n'
            "fuerza 3 = puede mover BTC más de 2%; 1 = efecto chico. Si es de opinión, sin efecto en el precio, "
            "o la misma noticia que otro titular de la lista, poné neutral (dejá solo uno).\n"
            + prev + "\n" + json.dumps(rows, ensure_ascii=False))


MODEL_SYSTEM = ("Sos analista de mercado cripto. Sos prudente: la mayoría de los titulares son neutrales. "
                "Nunca prometés una dirección, decís lo probable.")


def parse_model_verdicts(text: str, keys: Sequence[str]) -> dict[str, Verdict]:
    """Verdicts by key; anything malformed or unknown is dropped (caller falls back)."""
    m = re.search(r"\[.*\]", text or "", re.DOTALL)
    if not m:
        return {}
    try:
        rows = json.loads(m.group(0))
    except ValueError:
        return {}
    out = {}
    for r in rows if isinstance(rows, list) else ():
        if not isinstance(r, dict) or r.get("id") not in keys:
            continue
        impact = str(r.get("impacto", "")).strip().lower()
        if impact not in (UP, DOWN, NEUTRAL):
            continue
        try:
            strength = max(0, min(3, int(r.get("fuerza", 1))))
        except (TypeError, ValueError):
            strength = 1
        out[r["id"]] = Verdict(r["id"], impact, strength, str(r.get("por_que", ""))[:200], True)
    return out


def alert_text(pairs: Sequence[tuple[Headline, Verdict]], btc_price: Optional[float] = None) -> str:
    head = "📰 Noticias BTC" + (f" (BTC {btc_price:,.0f})" if btc_price else "")
    lines = [head]
    for h, v in pairs:
        arrow = "🔻 probablemente BAJA" if v.impact == DOWN else "🔺 probablemente SUBE"
        tag = "" if v.by_model else " (sin IA)"
        lines.append(f"\n{arrow} · fuerza {v.strength}/3{tag}\n{h.title}\n{v.reason}\n{h.source} · {h.link}")
    lines.append("\nSolo aviso, no es señal de entrada.")
    return "\n".join(lines)
