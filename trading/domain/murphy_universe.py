"""BTC and ETH only, for the Murphy reader. Pure: no terminal, no orders.

Exness Standard lists these as BTCUSDm and ETHUSDm. The root is BTCUSD / ETHUSD.
A name matches only when it is exactly the root, or the root plus the single
Standard suffix ``m``. BTCUSDTm and ETHBTCm do not match.
"""

from __future__ import annotations

from typing import Mapping, Optional, Sequence

MURPHY_ROOTS: tuple[str, ...] = ("BTCUSD", "ETHUSD")
_STANDARD_SUFFIX = "m"


def resolve_root(root: str, names: Sequence[str]) -> Optional[str]:
    """Broker name for one root, or None when the terminal does not list it."""
    want = str(root or "").strip().upper()
    if not want:
        return None
    by_upper: dict[str, str] = {}
    for name in names:
        raw = str(name or "").strip()
        if raw:
            by_upper.setdefault(raw.upper(), raw)
    for candidate in (want, want + _STANDARD_SUFFIX):
        found = by_upper.get(candidate.upper())
        if found is not None:
            return found
    return None


def resolve_universe(
    names: Sequence[str],
    *,
    tradable: Optional[Mapping[str, bool]] = None,
) -> dict[str, Optional[str]]:
    """Map each Murphy root to the broker symbol. Missing roots stay None."""
    allowed = names
    if tradable is not None:
        allowed = [n for n in names if tradable.get(n, tradable.get(str(n).upper(), False))]
    return {root: resolve_root(root, allowed) for root in MURPHY_ROOTS}
