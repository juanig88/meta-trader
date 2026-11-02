"""Market regime for the rejection entries: trend, operating floor/ceiling, and pause after a break.

The user's rule (2026-10-31):

- the trend comes from the swing structure of closed H4, D1, W1 and MN1 candles (higher highs
  and higher lows = up, lower highs and lower lows = down); 3 of the 4 must agree, else range
- the market trend is held: it stays bearish through bounces until 3 of the 4 turn bullish
  (and vice versa); only trade with it, never against (no buys in a bearish market)
- the stop goes beyond the operating floor (buy) / ceiling (sell); skip if that costs more
  than ``max_risk_usd``
- when the operating floor or ceiling breaks (H1 close beyond it) or a trade hits its stop,
  pause everything until a new floor/ceiling forms after that moment (2 touches with a bounce)

False-break rule (2026-10-31, ``on_h1_bar``): an H1 close beyond a zone is only a suspicion;
the bot evaluates and opens nothing. The break is confirmed after ``confirm_hours`` H1 closes
outside with price ``confirm_pct`` beyond the edge (then pause until a new zone). If an H1
closes back inside first, the break was false: trade the return (only with the held trend),
stop beyond the farthest point the false break reached, target the next zone.

Runbook: trading/docs/IMPULSO_M1.md
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Sequence

from trading.domain.impulso_m1 import BUY, SELL, Bar
from trading.domain.rebound_zones import CEILING, FLOOR, Zone, find_zones
from trading.domain.volume import volume_text

UP = "alcista"
DOWN = "bajista"
RANGE = "rango"


def structure_trend(bars: Sequence[Bar], pivot: int = 3) -> int:
    """+1 / -1 / 0 from the last two swing highs and lows of closed ``bars`` (oldest first)."""
    highs, lows = [], []
    for i in range(pivot, len(bars) - pivot):
        window = bars[i - pivot:i + pivot + 1]
        if bars[i].high == max(b.high for b in window):
            highs.append(bars[i].high)
        if bars[i].low == min(b.low for b in window):
            lows.append(bars[i].low)
    if len(highs) < 2 or len(lows) < 2:
        return 0
    if highs[-1] > highs[-2] and lows[-1] > lows[-2]:
        return 1
    if highs[-1] < highs[-2] and lows[-1] < lows[-2]:
        return -1
    return 0


def market_trend(votes: Sequence[int], min_agree: int = 3) -> str:
    if sum(1 for v in votes if v == 1) >= min_agree:
        return UP
    if sum(1 for v in votes if v == -1) >= min_agree:
        return DOWN
    return RANGE


def held_trend(previous: str, now: str) -> str:
    """The trend only changes when 3 of 4 timeframes agree on the other side; a range keeps it."""
    return previous if now == RANGE else now


def initial_trend(votes_by_day: Sequence[Sequence[int]], min_agree: int = 3) -> str:
    """Held trend after replaying daily votes, oldest first (used when the bot has no memory)."""
    held = RANGE
    for votes in votes_by_day:
        held = held_trend(held, market_trend(votes, min_agree))
    return held


def allowed_sides(trend: str) -> tuple[int, ...]:
    """Only with the held trend; with no trend yet, nothing."""
    return {UP: (BUY,), DOWN: (SELL,)}.get(trend, ())


def operating_zones(zones: Sequence[Zone], price: float) -> tuple[Optional[Zone], Optional[Zone]]:
    """Nearest floor under ``price`` and nearest ceiling over it.

    A manual zone (both roles) is a floor while price is above its middle and a ceiling below
    it, so price sitting inside it does not make it both at once.
    """
    mid = lambda z: (z.low + z.high) / 2
    floors = [z for z in zones if z.low < price and (z.kind == FLOOR or (z.kind not in (FLOOR, CEILING)
                                                                         and mid(z) <= price))]
    ceilings = [z for z in zones if z.high > price and (z.kind == CEILING or (z.kind not in (FLOOR, CEILING)
                                                                             and mid(z) > price))]
    return (max(floors, key=lambda z: z.low) if floors else None,
            min(ceilings, key=lambda z: z.high) if ceilings else None)


def zone_stop(side: int, floor: Optional[Zone], ceiling: Optional[Zone], buffer_pct: float = 0.1) -> Optional[float]:
    if side == BUY:
        return floor.low * (1 - buffer_pct / 100) if floor else None
    return ceiling.high * (1 + buffer_pct / 100) if ceiling else None


def risk_cap(profit_usd: float, floor_usd: float = 4.0, share: float = 0.5) -> float:
    """Max USD a trade may lose: half of what the bot has won so far, never under ``floor_usd``."""
    return max(floor_usd, share * profit_usd)


def return_target(side: int, entry: float, zones: Sequence[Zone], gap_pct: float = 0.1) -> Optional[float]:
    """Next zone the return trade aims at: top of the floor under a sell, bottom of the ceiling over a buy."""
    if side == SELL:
        below = [z for z in zones if z.high < entry * (1 - gap_pct / 100)]
        return max(z.high for z in below) if below else None
    above = [z for z in zones if z.low > entry * (1 + gap_pct / 100)]
    return min(z.low for z in above) if above else None


def new_zones(h1_since: Sequence[Bar]) -> list[Zone]:
    """Floors/ceilings built only from H1 candles after a pause: 2 touches 4h apart, 1% bounce in 12h."""
    return find_zones(h1_since, tol_pct=0.8, min_touches=2, pivot=3, min_gap=4, bounce_pct=1.0,
                      bounce_bars=12, origin="nueva_post_pausa")


@dataclass
class Regime:
    paused_since: Optional[str] = None  # ISO time, None = trading
    reason: str = ""
    floor: Optional[Zone] = None
    ceiling: Optional[Zone] = None
    fresh: list = field(default_factory=list)  # zones born after the last pause
    watch: Optional[dict] = None  # suspected break under evaluation: low, high, dir, since, extreme, hours

    @property
    def paused(self) -> bool:
        return self.paused_since is not None

    def zones(self, book_zones: Sequence[Zone]) -> list[Zone]:
        return list(book_zones) + list(self.fresh)

    def pause(self, t: datetime, reason: str) -> None:
        if not self.paused:
            self.paused_since, self.reason = t.isoformat(timespec="seconds"), reason
            self.floor = self.ceiling = None
            self.fresh = []
            self.watch = None

    def on_h1_bar(self, t: datetime, high: float, low: float, close: float, prev_close: float,
                  zones: Sequence[Zone], confirm_pct: float = 2.0, confirm_hours: int = 24) -> Optional[tuple]:
        """False-break rule for one closed H1. Returns None or (event, dir, extreme, watch):

        - ``("sospecha", d, ...)``: closed beyond a zone (d=+1 over a ceiling, -1 under a floor)
        - ``("falsa", d, extreme, ...)``: closed back inside before confirming; the trade side is -d
        - ``("confirmada", d, ...)``: far and long enough outside; the regime is now paused
        """
        if self.paused:
            return None
        w = self.watch
        if w is not None:
            d = w["dir"]
            w["extreme"] = max(w["extreme"], high) if d > 0 else min(w["extreme"], low)
            if (close <= w["high"]) if d > 0 else (close >= w["low"]):
                self.watch = None
                self.floor, self.ceiling = operating_zones(self.zones(zones), close)
                return ("falsa", d, w["extreme"], w)
            w["hours"] += 1
            edge = w["high"] if d > 0 else w["low"]
            if w["hours"] >= confirm_hours and (close - edge) * d >= edge * confirm_pct / 100:
                self.pause(t, f"ruptura_{'techo' if d > 0 else 'piso'}_confirmada {round(w['low'])}-{round(w['high'])}")
                return ("confirmada", d, w["extreme"], w)
            return None
        zs = self.zones(zones)
        def came_from(z: Zone) -> int:  # +1 price was over/at a floor, -1 under/at a ceiling
            if prev_close > z.high:
                return 1
            if prev_close < z.low:
                return -1
            if z.kind in (FLOOR, CEILING):
                return 1 if z.kind == FLOOR else -1
            return 1 if prev_close >= (z.low + z.high) / 2 else -1  # manual zone: both roles
        down = [z for z in zs if came_from(z) > 0 and close < z.low]
        up = [z for z in zs if came_from(z) < 0 and close > z.high]
        if down or up:
            z, d = (max(down, key=lambda z: z.low), -1) if down else (min(up, key=lambda z: z.high), 1)
            self.watch = {"low": z.low, "high": z.high, "dir": d, "origin": z.origin,
                          "since": t.isoformat(timespec="seconds"), "extreme": high if d > 0 else low, "hours": 1}
            return ("sospecha", d, self.watch["extreme"], self.watch)
        self.floor, self.ceiling = operating_zones(zs, close)
        return None

    def on_h1_close(self, t: datetime, close: float, zones: Sequence[Zone]) -> Optional[str]:
        """Pause when the close goes beyond the floor/ceiling we were trading against; then re-anchor."""
        if self.paused:
            return None
        why = None
        if self.floor is not None and close < self.floor.low:
            why = f"ruptura_piso {round(self.floor.low)}-{round(self.floor.high)}"
        elif self.ceiling is not None and close > self.ceiling.high:
            why = f"ruptura_techo {round(self.ceiling.low)}-{round(self.ceiling.high)}"
        if why:
            self.pause(t, why)
            return why
        self.floor, self.ceiling = operating_zones(self.zones(zones), close)
        return None

    def try_resume(self, h1_since_pause: Sequence[Bar], price: float) -> list[Zone]:
        """Resume once a new floor or ceiling formed after the pause; returns those zones."""
        if not self.paused:
            return []
        found = new_zones(h1_since_pause)
        if found:
            self.paused_since, self.reason = None, ""
            self.fresh = found
            self.floor, self.ceiling = operating_zones(found, price)
        return found

    def as_dict(self) -> dict:
        return {"paused_since": self.paused_since, "reason": self.reason,
                "floor": self.floor.as_dict() if self.floor else None,
                "ceiling": self.ceiling.as_dict() if self.ceiling else None,
                "fresh": [z.as_dict() for z in self.fresh], "watch": self.watch}

    @classmethod
    def from_dict(cls, d: dict) -> "Regime":
        z = lambda x: Zone(x["low"], x["high"], x["kind"], x.get("touches", 0), x.get("origin", "")) if x else None
        return cls(d.get("paused_since"), d.get("reason", ""), z(d.get("floor")), z(d.get("ceiling")),
                   [z(x) for x in d.get("fresh", [])], d.get("watch"))


TF_NAMES = ("H4", "D1", "W1", "MN1")
_ARROW = {1: "sube", -1: "baja", 0: "lateral"}


def status_text(price: float, trend: str, votes: Sequence[int], regime: Regime, header: str = "",
                since: str = "") -> str:
    """Telegram line set: where price sits between floor and ceiling, the trend and whether we trade."""
    def zone(z: Optional[Zone], label: str) -> str:
        if z is None:
            return f"{label}: ninguno definido"
        edge = z.high if label == "Piso" else z.low
        dist = abs(price - edge) / price * 100
        where = "adentro" if z.low <= price <= z.high else f"a {dist:.2f}%"
        return f"{label}: {round(z.low):,}–{round(z.high):,} ({z.origin}, {where})"
    tfs = ", ".join(f"{n} {_ARROW.get(v, '?')}" for n, v in zip(TF_NAMES, votes))
    side = {UP: "solo compras (BUY), en la vuelta de una ruptura falsa del piso",
            DOWN: "solo ventas (SELL), en la vuelta de una ruptura falsa del techo"}.get(
        trend, "nada hasta que haya tendencia")
    w = regime.watch
    if regime.paused:
        state = f"⏸️ EN PAUSA desde {regime.paused_since[11:16]} UTC: {regime.reason}. Espera un piso o techo nuevo."
    elif w:
        what = "techo" if w["dir"] > 0 else "piso"
        state = (f"🔎 EVALUANDO posible ruptura del {what} {round(w['low']):,}–{round(w['high']):,} desde "
                 f"{w['since'][11:16]} UTC ({w['hours']}h afuera, extremo {round(w['extreme']):,}). No abre nada.\n"
                 + volume_text(w.get("vol_rel")))
    else:
        state = f"▶️ Operando: {side}."
    lines = [header] if header else []
    held = f" desde {since[:10]}" if since else ""
    lines += [f"📊 BTC {price:,.0f} · Tendencia de fondo: {trend.upper()}{held}", f"Velas grandes ahora: {tfs}",
              zone(regime.ceiling, "Techo"), zone(regime.floor, "Piso"), state]
    return "\n".join(lines)


def status_key(trend: str, regime: Regime) -> tuple:
    """What a status message is about; a new message only when this changes."""
    z = lambda x: (round(x.low), round(x.high)) if x else None
    w = regime.watch
    return (trend, z(regime.floor), z(regime.ceiling), regime.paused,
            (round(w["low"]), round(w["high"]), w["dir"]) if w else None)
