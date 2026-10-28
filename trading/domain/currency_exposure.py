"""Base/quote currency decomposition and net/gross exposure caps.

BOT_CORRELATION_GROUPS counts open positions per named group. That cannot
see that USDJPY BUY and USDCHF BUY both stack long USD (or that a USDJPY
long and a EURUSD short cancel USD). This module splits each symbol into
(base, quote) and tracks signed lot exposure per currency.

    BUY  BASEQUOTE  ->  +lots base, -lots quote
    SELL BASEQUOTE  ->  -lots base, +lots quote

Net exposure is the signed sum (positive = net long). Gross is the sum of
absolute legs. Caps are in lots; 0 means that cap is off.

Unknown symbols (indices, unmapped CFDs) are ignored: fail-open, never
block a trade we cannot decompose honestly.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Mapping, Optional, Sequence

SIDE_BUY = "BUY"
SIDE_SELL = "SELL"


@dataclass(frozen=True)
class CurrencyPair:
    base: str
    quote: str


@dataclass(frozen=True)
class PositionLot:
    symbol: str
    side: str
    volume: float


@dataclass(frozen=True)
class CurrencyBook:
    currency: str
    long_lots: float = 0.0
    short_lots: float = 0.0

    @property
    def net(self) -> float:
        """Signed net lots: positive = net long."""
        return float(self.long_lots) - float(self.short_lots)

    @property
    def gross(self) -> float:
        return float(self.long_lots) + float(self.short_lots)


@dataclass(frozen=True)
class ExposureLimits:
    max_net: float = 0.0
    max_gross: float = 0.0

    @property
    def any_cap(self) -> bool:
        return float(self.max_net) > 0.0 or float(self.max_gross) > 0.0


@dataclass(frozen=True)
class ExposureVerdict:
    allowed: bool
    reason: str
    currency: str = ""
    net: float = 0.0
    gross: float = 0.0
    cap: float = 0.0
    books: dict[str, CurrencyBook] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "allowed": self.allowed,
            "reason": self.reason,
            "currency": self.currency,
            "net": self.net,
            "gross": self.gross,
            "cap": self.cap,
        }


def parse_currency_overrides(raw: str | Mapping | None) -> dict[str, tuple[str, str]]:
    """Parse BOT_CURRENCY_SYMBOL_OVERRIDES JSON or a mapping.

    Expected shape: {"BTCUSD": ["BTC", "USD"], "XAUUSD": ["XAU", "USD"]}.
    Invalid input returns {} (fail-open).
    """
    data: object
    if raw is None or raw == "":
        return {}
    if isinstance(raw, Mapping):
        data = raw
    else:
        try:
            data = json.loads(str(raw))
        except Exception:
            return {}
    if not isinstance(data, Mapping):
        return {}
    out: dict[str, tuple[str, str]] = {}
    for key, value in data.items():
        symbol = str(key or "").strip().upper()
        if not symbol:
            continue
        if isinstance(value, (list, tuple)) and len(value) >= 2:
            base = str(value[0] or "").strip().upper()
            quote = str(value[1] or "").strip().upper()
            if base and quote:
                out[symbol] = (base, quote)
    return out


def parse_symbol(
    symbol: str,
    overrides: Mapping[str, Sequence[str]] | None = None,
) -> Optional[CurrencyPair]:
    """Decompose a broker symbol into (base, quote), or None if unknown."""
    raw = str(symbol or "").strip().upper()
    if not raw:
        return None
    if overrides:
        hit = overrides.get(raw)
        if hit is None:
            hit = overrides.get(str(symbol).strip())
        if hit is not None and len(hit) >= 2:
            base = str(hit[0] or "").strip().upper()
            quote = str(hit[1] or "").strip().upper()
            if base and quote:
                return CurrencyPair(base, quote)

    letters = "".join(ch for ch in raw if ch.isalpha())
    if len(letters) >= 7 and letters.endswith("M"):
        letters = letters[:-1]
    if len(letters) == 6:
        return CurrencyPair(letters[:3], letters[3:])
    return None


def signed_legs(pair: CurrencyPair, side: str, volume: float) -> dict[str, float]:
    """Currency -> signed lots for one fill. BUY is long the base."""
    try:
        lots = abs(float(volume))
    except (TypeError, ValueError):
        return {}
    if lots <= 0.0 or lots != lots:
        return {}
    side_u = str(side or "").strip().upper()
    if side_u == SIDE_BUY:
        return {pair.base: lots, pair.quote: -lots}
    if side_u == SIDE_SELL:
        return {pair.base: -lots, pair.quote: lots}
    return {}


def _add_signed(books: dict[str, list[float]], currency: str, signed: float) -> None:
    bucket = books.setdefault(currency, [0.0, 0.0])
    if signed > 0.0:
        bucket[0] += signed
    elif signed < 0.0:
        bucket[1] += -signed


def aggregate_exposure(
    positions: Sequence[PositionLot],
    overrides: Mapping[str, Sequence[str]] | None = None,
) -> dict[str, CurrencyBook]:
    raw: dict[str, list[float]] = {}
    for pos in positions or ():
        pair = parse_symbol(pos.symbol, overrides)
        if pair is None:
            continue
        for currency, signed in signed_legs(pair, pos.side, pos.volume).items():
            _add_signed(raw, currency, signed)
    return {
        currency: CurrencyBook(currency=currency, long_lots=long, short_lots=short)
        for currency, (long, short) in raw.items()
    }


def check_new_trade(
    positions: Sequence[PositionLot],
    symbol: str,
    side: str,
    volume: float,
    *,
    limits: ExposureLimits = ExposureLimits(),
    overrides: Mapping[str, Sequence[str]] | None = None,
) -> ExposureVerdict:
    """Would adding this fill breach a net or gross cap?

    Unparseable symbols and empty/invalid volume are allowed (fail-open).
    Caps of 0 are off. A cap is breached only after the *proposed* fill is
    included, so a book already at the limit still blocks the next layer.
    """
    if not limits.any_cap:
        books = aggregate_exposure(positions, overrides)
        return ExposureVerdict(allowed=True, reason="no_cap", books=books)

    pair = parse_symbol(symbol, overrides)
    if pair is None:
        books = aggregate_exposure(positions, overrides)
        return ExposureVerdict(allowed=True, reason="unknown_symbol", books=books)

    proposed = list(positions or ()) + [
        PositionLot(symbol=str(symbol), side=str(side), volume=float(volume) if volume else 0.0)
    ]
    books = aggregate_exposure(proposed, overrides)
    max_net = float(limits.max_net)
    max_gross = float(limits.max_gross)

    for currency, book in books.items():
        if max_net > 0.0 and abs(book.net) - max_net > 1e-12:
            return ExposureVerdict(
                allowed=False,
                reason="net_cap",
                currency=currency,
                net=book.net,
                gross=book.gross,
                cap=max_net,
                books=books,
            )
        if max_gross > 0.0 and book.gross - max_gross > 1e-12:
            return ExposureVerdict(
                allowed=False,
                reason="gross_cap",
                currency=currency,
                net=book.net,
                gross=book.gross,
                cap=max_gross,
                books=books,
            )
    return ExposureVerdict(allowed=True, reason="ok", books=books)
