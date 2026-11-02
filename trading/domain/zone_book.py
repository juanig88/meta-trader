"""Zone book: every rebound zone with its history, current role and whether it still counts.

The bot keeps one book of zones (manual, 60-day H1, recent M30) and re-derives each one's
story from closed H1 candles every refresh, so nothing has to be maintained by hand:

- touch: an H1 candle reaches into the zone after not touching it
- bounce: the first close outside after a touch is on the side price came from (one per touch)
- break: price closes out on the other side; the role flips (broken floor -> ceiling)
- invertida: broken within the last ``flipped_days`` (still blocks, with the new role)
- vencida: no touch for ``expire_days`` and price more than ``expire_far_pct`` away
- debil: enough history and it breaks more than it holds (bounce rate below ``weak_rate``)

Only ``activa`` and ``invertida`` zones block entries. Runbook: trading/docs/IMPULSO_M1.md
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from typing import Optional, Sequence

from trading.domain.rebound_zones import CEILING, FLOOR, Zone

ACTIVE = "activa"
FLIPPED = "invertida"
EXPIRED = "vencida"
WEAK = "debil"
BLOCKING = (ACTIVE, FLIPPED)


@dataclass(frozen=True)
class H1:
    time: datetime
    high: float
    low: float
    close: float


@dataclass
class ZoneRecord:
    zid: str
    low: float
    high: float
    origin: str
    created: str
    touches: int = 0
    bounces: int = 0
    breaks: int = 0
    role: str = FLOOR
    status: str = ACTIVE
    last_touch: Optional[str] = None
    last_break: Optional[str] = None
    last_break_dir: Optional[str] = None
    move_after_break_pct: Optional[float] = None
    history: list = field(default_factory=list)

    @property
    def bounce_rate(self) -> Optional[float]:
        n = self.bounces + self.breaks
        return round(self.bounces / n, 2) if n else None

    def as_zone(self) -> Zone:
        return Zone(self.low, self.high, self.role, self.touches, self.origin)

    def as_dict(self) -> dict:
        d = asdict(self)
        d["bounce_rate"] = self.bounce_rate
        return d


def zone_id(low: float, high: float, origin: str) -> str:
    return f"{origin}:{round(low)}-{round(high)}"


def overlaps(a_low: float, a_high: float, b_low: float, b_high: float, min_frac: float = 0.5) -> bool:
    """True when the shared stretch is at least ``min_frac`` of the narrower zone."""
    shared = min(a_high, b_high) - max(a_low, b_low)
    narrow = max(min(a_high - a_low, b_high - b_low), 1e-9)
    return shared >= 0 and shared / narrow >= min_frac


def replay(rec: ZoneRecord, bars: Sequence[H1], now: datetime, *, expire_days: float = 7,
           expire_far_pct: float = 3.0, weak_rate: float = 0.4, weak_min_events: int = 3,
           history_keep: int = 12, flipped_days: float = 7) -> ZoneRecord:
    """Rebuild ``rec``'s counters, role and status from ``bars`` (closed H1, oldest first)."""
    touches = bounces = breaks = 0
    last_outside: Optional[str] = None  # "above" | "below"
    episode = False  # a touch started and has not been resolved by a close outside yet
    prev_touch = False
    last_touch = last_break = None
    break_dir = None
    break_ref = None
    extreme = None
    events: list = []
    for b in bars:
        touching = b.low <= rec.high and b.high >= rec.low
        if touching and not prev_touch:
            touches += 1
            last_touch = b.time
            episode = True
        elif touching:
            last_touch = b.time
        prev_touch = touching
        side = "above" if b.close > rec.high else "below" if b.close < rec.low else None
        if side is not None:
            if last_outside is not None and side != last_outside:
                breaks += 1
                last_break, break_dir = b.time, ("abajo" if side == "below" else "arriba")
                break_ref, extreme = (rec.low if side == "below" else rec.high), b.close
                events.append({"t": b.time.isoformat(), "evento": f"ruptura_{break_dir}", "cierre": round(b.close)})
            elif last_outside is not None and episode:
                bounces += 1
                events.append({"t": b.time.isoformat(), "evento": "rebote", "cierre": round(b.close)})
            last_outside = side
            episode = False
        if break_ref is not None and extreme is not None:
            extreme = min(extreme, b.low) if break_dir == "abajo" else max(extreme, b.high)
    rec.touches, rec.bounces, rec.breaks = touches, bounces, breaks
    if last_outside is not None:
        rec.role = FLOOR if last_outside == "above" else CEILING
    rec.last_touch = last_touch.isoformat() if last_touch else None
    rec.last_break = last_break.isoformat() if last_break else None
    rec.last_break_dir = break_dir
    rec.move_after_break_pct = (round((extreme / break_ref - 1) * 100, 2)
                                if break_ref and extreme is not None else None)
    rec.history = events[-history_keep:]
    price = bars[-1].close if bars else None
    stale = last_touch is None or now - last_touch > timedelta(days=expire_days)
    far = price is not None and min(abs(price - rec.low), abs(price - rec.high)) / price * 100 > expire_far_pct
    if stale and far:
        rec.status = EXPIRED
    elif bounces + breaks >= weak_min_events and (rec.bounce_rate or 0) < weak_rate:
        rec.status = WEAK
    elif last_break is not None and now - last_break <= timedelta(days=flipped_days):
        rec.status = FLIPPED
    else:
        rec.status = ACTIVE
    return rec


def merge_candidates(book: dict[str, ZoneRecord], candidates: Sequence[Zone], now: datetime) -> list[str]:
    """Add detected zones that overlap nothing already in the book (manual zones win). New ids back."""
    added = []
    for z in candidates:
        if any(overlaps(z.low, z.high, r.low, r.high) and r.status != EXPIRED for r in book.values()):
            continue
        zid = zone_id(z.low, z.high, z.origin)
        if zid in book:  # the same zone, already known (expired ones too): not new
            continue
        book[zid] = ZoneRecord(zid, z.low, z.high, z.origin, now.isoformat(timespec="seconds"), role=z.kind
                               if z.kind in (FLOOR, CEILING) else FLOOR)
        added.append(zid)
    return added


def sync_manual(book: dict[str, ZoneRecord], manual: Sequence[Zone], now: datetime) -> tuple[list[str], list[str]]:
    """Manual zones mirror the user's file: lines added appear, lines removed disappear."""
    want = {zone_id(z.low, z.high, "manual"): z for z in manual}
    removed = [k for k, r in book.items() if r.origin == "manual" and k not in want]
    for k in removed:
        del book[k]
    added = []
    for k, z in want.items():
        if k not in book:
            book[k] = ZoneRecord(k, z.low, z.high, "manual", now.isoformat(timespec="seconds"))
            added.append(k)
    return added, removed


def prune(book: dict[str, ZoneRecord], now: datetime, drop_after_days: float = 30) -> list[str]:
    """Forget automatic zones expired and untouched for ``drop_after_days`` (manual stay until the user removes them)."""
    gone = []
    for k, r in list(book.items()):
        if r.origin == "manual" or r.status != EXPIRED:
            continue
        ref = datetime.fromisoformat(r.last_touch or r.created)
        if now - ref > timedelta(days=drop_after_days):
            del book[k]
            gone.append(k)
    return gone


def blocking_zones(book: dict[str, ZoneRecord]) -> list[Zone]:
    return [r.as_zone() for r in book.values() if r.status in BLOCKING]


def change_messages(before: dict[str, tuple[str, str]], book: dict[str, ZoneRecord]) -> list[str]:
    """Human lines for status/role changes since ``before`` ({zid: (status, role)})."""
    out = []
    for k, r in book.items():
        old = before.get(k)
        if old is None or old == (r.status, r.role):
            continue
        rate = f"{r.bounces} rebotes / {r.breaks} rupturas"
        if r.status != old[0] and r.status == EXPIRED:
            what = "vencida (ya no bloquea)"
        elif r.status != old[0] and r.status == WEAK:
            what = "débil: se rompe más de lo que rebota (ya no bloquea)"
        elif r.role != old[1]:
            what = f"ROTA hacia {r.last_break_dir}: ahora es {r.role}"
            if r.move_after_break_pct is not None:
                what += f" (movimiento desde la ruptura {r.move_after_break_pct:+.2f}%)"
        else:
            what = f"{old[0]} → {r.status}"
        out.append(f"Zona {round(r.low):,}–{round(r.high):,} ({r.origin}): {what}. Historial: {rate}.")
    return out
