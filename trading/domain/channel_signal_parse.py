"""Parse channel-signal bodies into ChannelSignal. Pure: no file I/O."""

from __future__ import annotations

import math
import re
from dataclasses import replace
from datetime import datetime, timezone
from typing import Optional

from trading.domain.channel_signal import ALLOWED_PAIRS, ChannelSignal

_PIPE_SPLIT = re.compile(r"\s*\|\s*")
_KEYVAL = re.compile(r"^\s*([^:]+):\s*(.+?)\s*$")
_PAIR_TOKEN = re.compile(
    r"(?:#|\$)?(BTC|ETH)(?:[-_/]?(?:USDT|USD))?\b",
    re.IGNORECASE,
)
_SIDE_LONG = re.compile(
    r"\b(LONG|LARGO|COMPRA|BUY|SE[NÑ]AL\s+LARGA)\b",
    re.IGNORECASE,
)
_SIDE_SHORT = re.compile(
    r"\b(SHORT|CORTO|VENTA|SELL|SE[NÑ]AL\s+CORTA)\b",
    re.IGNORECASE,
)
_PRICE_CHUNK = re.compile(
    r"[-+]?\d[\d.,]*",
)
_PCT_THEN_PRICE = re.compile(
    r"([+-]?\d+(?:[.,]\d+)?)\s*%\s*(?:->|→)\s*(" + _PRICE_CHUNK.pattern + r")",
    re.IGNORECASE,
)
_PCT_ONLY = re.compile(r"([+-]?\d+(?:[.,]\d+)?)\s*%")
_SCORE = re.compile(r"\bscore\s*[:=]\s*([+-]?\d+(?:[.,]\d+)?)", re.IGNORECASE)
_CONFIRMS = re.compile(
    r"\bconfirmaci[oó]n(?:es)?\s*[:=]\s*(.+)$",
    re.IGNORECASE | re.MULTILINE,
)
_LEVERAGE = re.compile(r"\b(?:apalancamiento|leverage)\s*[:=]?\s*x?\s*(\d+(?:[.,]\d+)?)", re.IGNORECASE)
_ENTRY_LINE = re.compile(
    r"(?:entrada|entry|precio)\s*[:=]\s*(.+)$",
    re.IGNORECASE | re.MULTILINE,
)
_TP_LINE = re.compile(
    r"(?:\bTP\b|take\s*profit|beneficio\s*objetivo|objetivo)\s*[:=]\s*(.+)$",
    re.IGNORECASE | re.MULTILINE,
)
_SL_LINE = re.compile(
    r"(?:\bSL\b|stop\s*loss|l[ií]mite\s*de\s*parada)\s*[:=]\s*(.+)$",
    re.IGNORECASE | re.MULTILINE,
)
_LIQ_LINE = re.compile(
    r"precio\s+de\s+liquidaci[oó]n\s*[:=]\s*(.+)$",
    re.IGNORECASE | re.MULTILINE,
)
_BULLET_HEAD = re.compile(
    r"[•\-\*]?\s*(BTC|ETH|BITCOIN|ETHEREUM)\s*[-–—]\s*(Largo|Corto|Long|Short)\b",
    re.IGNORECASE,
)
_CLAIMED_PAIR = re.compile(
    r"#(BTC|ETH)/USDT\b",
    re.IGNORECASE,
)
_CLAIMED_TP = re.compile(
    r"take\s*profit\s*target",
    re.IGNORECASE,
)
_LINE_KV = re.compile(r"^\[?([A-Za-zÁÉÍÓÚáéíóúñÑ ]+)\]?:\s*(.+)$")
_NUEVA_SENAL_MARK = re.compile(r"NUEVA\s+SE[NÑ]AL", re.IGNORECASE)
_SCORE_CARD_MARK = re.compile(r"\[SCORE:|SE[NÑ]AL\s+DE\s+ALTA\s+CONFIANZA", re.IGNORECASE)
_NEURAL_MARK = re.compile(
    r"NUEVA\s+SE[NÑ]AL|\[SCORE:|SE[NÑ]AL\s+DE\s+ALTA\s+CONFIANZA",
    re.IGNORECASE,
)
_SPAM_MARKERS = (
    "payout proof",
    "blockchain.com/",
    "@tradeassist01",
    "programa de inversi",
    "plan de inversi",
    "envíame un mensaje",
    "enviame un mensaje",
)


def _to_float(raw: str) -> Optional[float]:
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    s = s.replace("$", "").replace("€", "").replace("USDT", "").replace(" ", "")
    s = s.strip(".,;:")
    if not s:
        return None
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        left, right = s.split(",", 1)
        signed_left = left[1:] if left[:1] in "+-" else left
        if right.isdigit() and len(right) == 3 and signed_left.isdigit():
            s = left + right
        else:
            s = s.replace(",", ".")
    try:
        value = float(s)
    except ValueError:
        return None
    if not math.isfinite(value):
        return None
    return value


def parse_price(raw: str) -> Optional[float]:
    """Parse a single absolute price. Returns None if the token is not a price."""
    value = _to_float(raw)
    if value is None or value <= 0:
        return None
    return value


def parse_price_list(raw: str) -> tuple[float, ...]:
    if raw is None:
        return ()
    found = []
    for token in _PRICE_CHUNK.findall(str(raw)):
        price = parse_price(token)
        if price is not None:
            found.append(price)
    return tuple(found)


def normalize_pair(raw: str) -> Optional[str]:
    if not raw:
        return None
    text = str(raw).upper().replace(" ", "")
    text = text.replace("#", "").replace("$", "").replace("/", "").replace("-", "").replace("_", "")
    if text in ("BITCOIN", "BTC"):
        return "BTCUSDT"
    if text in ("ETHEREUM", "ETH", "ETEREUM", "ETEREUM"):
        return "ETHUSDT"
    if text in ALLOWED_PAIRS:
        return text
    if text.endswith("USD") and not text.endswith("USDT"):
        base = text[:-3]
        if base in ("BTC", "ETH"):
            return base + "USDT"
    match = _PAIR_TOKEN.search(str(raw))
    if not match:
        return None
    base = match.group(1).upper()
    if base in ("BTC", "ETH"):
        return base + "USDT"
    return None


def normalize_side(raw: str) -> Optional[str]:
    if not raw:
        return None
    text = str(raw)
    # SHORT before LONG so "LARGOS VS CORTOS" does not become LONG by accident
    # when both match: prefer the first directional token in the string.
    long_m = _SIDE_LONG.search(text)
    short_m = _SIDE_SHORT.search(text)
    if long_m and short_m:
        return "LONG" if long_m.start() < short_m.start() else "SHORT"
    if short_m:
        return "SHORT"
    if long_m:
        return "LONG"
    return None


def looks_like_spam(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in _SPAM_MARKERS)


def _pct_and_price(raw: str) -> tuple[Optional[float], Optional[float]]:
    match = _PCT_THEN_PRICE.search(raw or "")
    if match:
        return _to_float(match.group(1)), parse_price(match.group(2))
    pct_only = _PCT_ONLY.search(raw or "")
    if pct_only:
        return _to_float(pct_only.group(1)), None
    prices = parse_price_list(raw or "")
    return None, prices[0] if prices else None


def _apply_pct_levels(entry: Optional[float], tp_pct: Optional[float], sl_pct: Optional[float], side: str) -> tuple[Optional[float], Optional[float]]:
    tp = sl = None
    if entry is None or entry <= 0:
        return tp, sl
    if tp_pct is not None:
        frac = abs(tp_pct) / 100.0
        tp = entry * (1.0 + frac) if side == "LONG" else entry * (1.0 - frac)
    if sl_pct is not None:
        frac = abs(sl_pct) / 100.0
        sl = entry * (1.0 - frac) if side == "LONG" else entry * (1.0 + frac)
    return tp, sl


def _parse_confirmations(raw: str) -> tuple[str, ...]:
    text = (raw or "").strip()
    if not text:
        return ()
    if re.match(r"^\d+\s*/\s*\d+$", text):
        return (re.sub(r"\s+", "", text),)
    return tuple(p.strip() for p in re.split(r"[,;]", text) if p.strip())


def _signal_from_fields(
    fields: dict[str, str],
    body: str,
    *,
    message_id: int,
    timestamp_utc: datetime,
    template: str = "neural",
) -> Optional[ChannelSignal]:
    pair_raw = fields.get("par") or fields.get("pair") or fields.get("symbol") or ""
    pair = normalize_pair(pair_raw) or normalize_pair(body)
    if pair not in ALLOWED_PAIRS:
        return None
    side = normalize_side(
        fields.get("direccion") or fields.get("dirección") or fields.get("side") or body
    )
    if side is None:
        return None
    entry = parse_price(fields.get("precio") or fields.get("entrada") or fields.get("entry") or "")
    tp_pct, tp = _pct_and_price(fields.get("tp") or fields.get("take profit") or "")
    sl_pct, sl = _pct_and_price(fields.get("sl") or fields.get("stop loss") or "")
    sl_source = "none"
    if sl_pct is not None:
        sl_source = "percent"
    elif sl is not None:
        sl_source = "explicit"
    if entry is not None and (tp is None or sl is None):
        derived_tp, derived_sl = _apply_pct_levels(entry, tp_pct, sl_pct, side)
        tp = tp if tp is not None else derived_tp
        sl = sl if sl is not None else derived_sl
    score_raw = fields.get("score") or ""
    score = parse_price(score_raw.split("/")[0] if score_raw else "")
    senales_raw = fields.get("senales") or fields.get("señales") or ""
    if senales_raw:
        confirms = tuple(p.strip() for p in senales_raw.split("|") if p.strip())
        ratio = _parse_confirmations(fields.get("confirmaciones") or fields.get("confirmacion") or "")
        if ratio:
            confirms = ratio + confirms
    else:
        confirms = _parse_confirmations(fields.get("confirmaciones") or fields.get("confirmacion") or "")
    if entry is None:
        return None
    if tp is None and sl is None:
        return None
    return ChannelSignal(
        message_id=message_id,
        timestamp_utc=timestamp_utc,
        pair=pair,
        side=side,
        kind="structured_trade",
        entry=entry,
        tp=tp,
        sl=sl,
        tp_pct=tp_pct,
        sl_pct=sl_pct,
        sl_source=sl_source,
        score=score,
        confirmations=confirms,
        template=template,
        raw_excerpt=body.strip()[:240],
    )


def parse_labeled_lines_body(body: str, *, message_id: int, timestamp_utc: datetime) -> Optional[ChannelSignal]:
    if _NUEVA_SENAL_MARK.search(body):
        template = "nueva_senal"
    elif _SCORE_CARD_MARK.search(body):
        template = "score_card"
    else:
        return None
    fields: dict[str, str] = {}
    for raw_line in body.splitlines():
        match = _LINE_KV.match(raw_line.strip())
        if not match:
            continue
        fields[match.group(1).strip().lower()] = match.group(2).strip()
    return _signal_from_fields(
        fields,
        body,
        message_id=message_id,
        timestamp_utc=timestamp_utc,
        template=template,
    )


def parse_pipe_body(body: str, *, message_id: int, timestamp_utc: datetime) -> Optional[ChannelSignal]:
    if "|" not in body:
        return None
    fields: dict[str, str] = {}
    for chunk in _PIPE_SPLIT.split(body.replace("\n", " ")):
        kv = _KEYVAL.match(chunk)
        if kv:
            fields[kv.group(1).strip().lower()] = kv.group(2).strip()
        elif normalize_side(chunk) and "direccion" not in fields and "dirección" not in fields:
            fields["direccion"] = chunk.strip()
    got = _signal_from_fields(fields, body, message_id=message_id, timestamp_utc=timestamp_utc)
    if got is None:
        return None
    return replace(got, template="pipe")


def parse_bullet_body(body: str, *, message_id: int, timestamp_utc: datetime) -> Optional[ChannelSignal]:
    head = _BULLET_HEAD.search(body)
    if not head:
        return None
    pair = normalize_pair(head.group(1))
    if pair not in ALLOWED_PAIRS:
        return None
    side = normalize_side(head.group(2))
    if side is None:
        return None
    entry_m = _ENTRY_LINE.search(body)
    tp_m = _TP_LINE.search(body)
    sl_m = _SL_LINE.search(body)
    liq_m = _LIQ_LINE.search(body)
    entries = parse_price_list(entry_m.group(1)) if entry_m else ()
    tps = parse_price_list(tp_m.group(1)) if tp_m else ()
    sls = parse_price_list(sl_m.group(1)) if sl_m else ()
    liqs = parse_price_list(liq_m.group(1)) if liq_m else ()
    if not entries:
        return None
    entry = entries[0]
    entry_high = entries[1] if len(entries) > 1 else None
    tp = tps[0] if tps else None
    sl_source = "none"
    sl = sls[0] if sls else None
    if sl is not None:
        sl_source = "explicit"
    elif liqs:
        sl = liqs[0]
        sl_source = "liquidation"
    lev_m = _LEVERAGE.search(body)
    leverage = parse_price(lev_m.group(1)) if lev_m else None
    return ChannelSignal(
        message_id=message_id,
        timestamp_utc=timestamp_utc,
        pair=pair,
        side=side,
        kind="structured_trade",
        entry=entry,
        entry_high=entry_high,
        tp=tp,
        sl=sl,
        sl_source=sl_source,
        leverage=leverage,
        template="bullet",
        raw_excerpt=body.strip()[:240],
    )


def parse_claimed_result(body: str, *, message_id: int, timestamp_utc: datetime) -> Optional[ChannelSignal]:
    pair_m = _CLAIMED_PAIR.search(body)
    if not pair_m or not _CLAIMED_TP.search(body):
        return None
    pair = normalize_pair(pair_m.group(0))
    if pair not in ALLOWED_PAIRS:
        return None
    side = normalize_side(body) or "LONG"
    return ChannelSignal(
        message_id=message_id,
        timestamp_utc=timestamp_utc,
        pair=pair,
        side=side,
        kind="claimed_result",
        template="claimed",
        raw_excerpt=body.strip()[:240],
    )


def parse_signal_body(
    body: str,
    *,
    message_id: int,
    timestamp_utc: datetime,
) -> Optional[ChannelSignal]:
    if not body or not body.strip():
        return None
    parsed = parse_labeled_lines_body(body, message_id=message_id, timestamp_utc=timestamp_utc)
    if parsed is not None:
        return parsed
    parsed = parse_pipe_body(body, message_id=message_id, timestamp_utc=timestamp_utc)
    if parsed is not None:
        return parsed
    parsed = parse_bullet_body(body, message_id=message_id, timestamp_utc=timestamp_utc)
    if parsed is not None:
        return parsed
    return parse_claimed_result(body, message_id=message_id, timestamp_utc=timestamp_utc)


def mentions_btc_or_eth_market(body: str) -> bool:
    if not body:
        return False
    if looks_like_spam(body) and not _BULLET_HEAD.search(body) and "|" not in body:
        return False
    return normalize_pair(body) in ALLOWED_PAIRS


def utc_from_export(raw: str) -> datetime:
    text = str(raw).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)
