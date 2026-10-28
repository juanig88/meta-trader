"""XBTFX terminal rules. Pure: no MetaTrader5, no network, no orders.

The live process may attach only to an XBTFX server. Symbol roots strip the
suffixes this broker actually uses (`!`, a trailing dot, `.NAS`, `.NYSE-24`)
so BTCUSD! and USDJPY. share one point map. An alias is reported, never
guessed into an order symbol.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Optional, Sequence

# MetaTrader5 last_error codes. IPC / timeout are the disconnect class.
_RETRYABLE_CODES = frozenset({-1, -10000, -10001, -10002, -10003, -10004, -10005, -10006})
_AUTH_CODES = frozenset({-6})
_FATAL_CODES = frozenset({-2, -3, -5, -7, -8})

_VENUE_SUFFIXES = (".NYSE-24", ".NYSE", ".NASDAQ", ".NAS", ".LSE", ".XETR")
_PREFIXES = ("XBTFX.", "XBTFX-")

_CRYPTO_ROOTS = frozenset(
    {
        "BTCUSD",
        "ETHUSD",
        "LTCUSD",
        "XRPUSD",
        "BCHUSD",
        "SOLUSD",
        "ADAUSD",
        "DOGEUSD",
        "BNBUSD",
        "DOTUSD",
        "AVAXUSD",
    }
)
_METAL_ROOTS = frozenset({"XAUUSD", "XAGUSD", "XPTUSD", "XPDUSD"})

_CALC_MODE = {
    0: "forex",
    1: "futures",
    2: "cfd",
    3: "cfdindex",
    4: "cfdleverage",
    5: "forex_no_leverage",
    32: "exchange_stocks",
    33: "exchange_futures",
}
_TRADE_MODE = {
    0: "disabled",
    1: "longonly",
    2: "shortonly",
    3: "closeonly",
    4: "full",
}
_EXEC_MODE = {0: "request", 1: "instant", 2: "market", 3: "exchange"}
_FILL_BITS = ((1, "FOK"), (2, "IOC"), (4, "BOC"))


def parse_server_markers(raw: str) -> tuple[str, ...]:
    """Comma-separated substrings. Blank input stays on the XBTFX default."""
    parts = [p.strip().upper() for p in str(raw or "").split(",") if p.strip()]
    return tuple(parts) if parts else ("XBTFX",)


def account_is_xbtfx(server: str, company: str, markers: Sequence[str]) -> bool:
    """True when server or company contains one of the markers."""
    blob = f"{server or ''} {company or ''}".upper()
    marks = [str(m).strip().upper() for m in markers if str(m).strip()]
    if not marks or not blob.strip():
        return False
    return any(mark in blob for mark in marks)


def classify_terminal_error(code: object, message: str = "") -> str:
    """retryable (IPC, timeout), auth, or fatal (bad params, trade disabled)."""
    text = str(message or "").lower()
    try:
        numeric = int(code)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        numeric = None
    if numeric in _AUTH_CODES or "authori" in text or "invalid account" in text:
        return "auth"
    if numeric in _FATAL_CODES:
        return "fatal"
    if numeric in _RETRYABLE_CODES or any(tok in text for tok in ("timeout", "ipc", "connect", "no connection")):
        return "retryable"
    if numeric in (0, 1):
        return "ok"
    return "retryable"


def is_link_failure(kind: str) -> bool:
    return kind in {"retryable", "auth"}


def should_retry(kind: str, attempt: int, max_attempts: int) -> bool:
    """Whether another initialize is worth it after this failed attempt."""
    if int(attempt) >= int(max_attempts):
        return False
    if kind == "fatal":
        return False
    if kind == "auth" and int(attempt) >= 2:
        return False
    return True


def reconnect_sleep_seconds(attempt: int, base: float, kind: str) -> float:
    """Auth stays on the base delay. Disconnects back off, capped at 30s."""
    delay = max(0.0, float(base))
    if kind != "auth":
        delay *= 2 ** max(0, int(attempt) - 1)
    return min(30.0, delay)


def connection_decision(
    *,
    initialized: bool,
    server: str,
    company: str,
    markers: Sequence[str],
    enforced: bool,
    error_code: object = None,
    error_message: str = "",
    attempt: int = 1,
    max_attempts: int = 5,
    base_sleep: float = 2.0,
) -> tuple[str, float]:
    """Return (accept | reject_broker | retry | stop, sleep_seconds)."""
    if initialized:
        if not enforced or account_is_xbtfx(server, company, markers):
            return "accept", 0.0
        return "reject_broker", 0.0
    kind = classify_terminal_error(error_code, error_message)
    if not should_retry(kind, attempt, max_attempts):
        return "stop", 0.0
    return "retry", reconnect_sleep_seconds(attempt, base_sleep, kind)


def root_symbol(symbol: str) -> str:
    """Canonical root. Does not invent a tradable name."""
    sym = str(symbol or "").strip().upper()
    if sym.startswith("#"):
        sym = sym[1:]
    for prefix in _PREFIXES:
        if sym.startswith(prefix):
            sym = sym[len(prefix) :]
            break
    if sym.endswith("!"):
        sym = sym[:-1]
    for suffix in _VENUE_SUFFIXES:
        if sym.endswith(suffix):
            sym = sym[: -len(suffix)]
            break
    if sym.endswith(".") and "." not in sym[:-1]:
        sym = sym[:-1]
    return sym


def candidate_names(symbol: str) -> tuple[str, ...]:
    """Configured name first, then the root and the two XBTFX quote suffixes."""
    raw = str(symbol or "").strip().upper()
    if not raw:
        return ()
    root = root_symbol(raw)
    ordered: list[str] = []
    for name in (raw, root, f"{root}!", f"{root}."):
        if name and name not in ordered:
            ordered.append(name)
    return tuple(ordered)


def alias_hint(requested: str, available: Sequence[str]) -> str:
    """Human hint when the configured name is absent. Empty if nothing matches."""
    raw = str(requested or "").strip().upper()
    have = {str(name).strip().upper() for name in available if str(name).strip()}
    hits = [name for name in candidate_names(raw) if name in have and name != raw]
    if not hits:
        return ""
    if len(hits) == 1:
        return f" broker lists {hits[0]}"
    return " ambiguous aliases: " + ", ".join(hits)


def asset_class(symbol: str, calc_mode: Optional[int] = None) -> str:
    raw = str(symbol or "").strip().upper()
    root = root_symbol(raw)
    if raw.endswith("!") or root in _CRYPTO_ROOTS:
        return "crypto"
    if root in _METAL_ROOTS or root.startswith(("XAU", "XAG", "XPT", "XPD")):
        return "metal"
    if any(raw.endswith(suffix) for suffix in _VENUE_SUFFIXES):
        return "equity_cfd"
    if calc_mode in (0, 5) or (len(root) == 6 and root.isalpha()):
        return "forex"
    if calc_mode in (2, 3, 4, 32, 33):
        return "cfd"
    return "other"


def execution_style(trade_exemode: int, spread_float: bool) -> str:
    mode = _EXEC_MODE.get(int(trade_exemode), "unknown")
    if mode == "market" and spread_float:
        return "ecn"
    return mode


def filling_labels(bitmask: int) -> tuple[str, ...]:
    flags = int(bitmask or 0)
    labels = tuple(name for bit, name in _FILL_BITS if flags & bit)
    return labels or ("unknown",)


def margin_band(
    equity: float,
    margin: float,
    so_call: Optional[float],
    so_so: Optional[float],
) -> tuple[Optional[float], str]:
    """(margin level %, band). Band matches the dashboard thresholds."""
    try:
        eq = float(equity)
        used = float(margin)
    except (TypeError, ValueError):
        return None, "unknown"
    if used <= 0.0:
        return None, "none"
    level = eq / used * 100.0
    stop = float(so_so) if so_so else 0.0
    call = float(so_call) if so_call else 0.0
    if stop and level <= stop * 1.2:
        return level, "critical"
    if call and level <= call:
        return level, "warn"
    if level < 300.0:
        return level, "caution"
    return level, "ok"


@dataclass(frozen=True)
class ContractSnapshot:
    symbol: str
    asset_class: str
    execution: str
    calc_mode: str
    trade_mode: str
    point: float
    digits: int
    contract_size: float
    volume_min: float
    volume_step: float
    volume_max: float
    filling: tuple[str, ...]
    currency_base: str
    currency_profit: str
    currency_margin: str
    margin_initial: float
    spread_float: bool
    tradable: bool
    reason: str

    def as_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "asset_class": self.asset_class,
            "execution": self.execution,
            "calc_mode": self.calc_mode,
            "trade_mode": self.trade_mode,
            "point": self.point,
            "digits": self.digits,
            "contract_size": self.contract_size,
            "volume_min": self.volume_min,
            "volume_step": self.volume_step,
            "volume_max": self.volume_max,
            "filling": list(self.filling),
            "currency_base": self.currency_base,
            "currency_profit": self.currency_profit,
            "currency_margin": self.currency_margin,
            "margin_initial": self.margin_initial,
            "spread_float": self.spread_float,
            "tradable": self.tradable,
            "reason": self.reason,
        }

    def summary(self) -> str:
        return (
            f"{self.symbol} class={self.asset_class} exec={self.execution} "
            f"calc={self.calc_mode} trade={self.trade_mode} point={self.point} "
            f"contract={self.contract_size} vol={self.volume_min}/{self.volume_step}/{self.volume_max} "
            f"fill={','.join(self.filling)} margin_ccy={self.currency_margin} "
            f"tradable={self.tradable} ({self.reason})"
        )


def _num(info: Mapping, key: str, default: float = 0.0) -> float:
    try:
        return float(info.get(key, default) or default)
    except (TypeError, ValueError):
        return default


def _text(info: Mapping, key: str) -> str:
    return str(info.get(key) or "").strip().upper()


def contract_snapshot(info: Mapping) -> ContractSnapshot:
    """Classify one symbol_info mapping. Missing point or disabled trade is not tradable."""
    symbol = _text(info, "name") or _text(info, "symbol")
    point = _num(info, "point")
    trade_mode = int(_num(info, "trade_mode"))
    calc = int(_num(info, "calc_mode"))
    exe = int(_num(info, "trade_exemode"))
    spread_float = bool(info.get("spread_float"))
    reason = "ok"
    tradable = True
    if point <= 0.0:
        tradable = False
        reason = "point"
    elif trade_mode == 0:
        tradable = False
        reason = "trade_disabled"
    elif trade_mode == 3:
        tradable = False
        reason = "closeonly"
    elif _num(info, "volume_min") <= 0.0 or _num(info, "volume_step") <= 0.0:
        tradable = False
        reason = "volume_constraints"
    elif _num(info, "trade_contract_size") <= 0.0:
        tradable = False
        reason = "contract_size"
    return ContractSnapshot(
        symbol=symbol,
        asset_class=asset_class(symbol, calc),
        execution=execution_style(exe, spread_float),
        calc_mode=_CALC_MODE.get(calc, f"mode_{calc}"),
        trade_mode=_TRADE_MODE.get(trade_mode, f"mode_{trade_mode}"),
        point=point,
        digits=int(_num(info, "digits")),
        contract_size=_num(info, "trade_contract_size"),
        volume_min=_num(info, "volume_min"),
        volume_step=_num(info, "volume_step"),
        volume_max=_num(info, "volume_max"),
        filling=filling_labels(int(_num(info, "filling_mode"))),
        currency_base=_text(info, "currency_base"),
        currency_profit=_text(info, "currency_profit"),
        currency_margin=_text(info, "currency_margin"),
        margin_initial=_num(info, "margin_initial"),
        spread_float=spread_float,
        tradable=tradable,
        reason=reason,
    )
