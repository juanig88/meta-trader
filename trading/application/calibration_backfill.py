"""Rebuild alpha / Murphy at each historical rotation fill, plus broker net. No orders.

Only bars whose close time is <= the open deal time enter a fill's features,
so the reconstruction cannot see the future. MT5 access is injected; the job
writes a JSON cache that the calibration report reads offline.
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence

from trading.application.log_events import iter_events_files, rotation_log_paths
from trading.domain.alpha_shadow import alpha_shadow_from_bars
from trading.domain.ensemble import Ohlcv
from trading.domain.swing_structure import murphy_snapshot

ROTATION_MAGIC = 260414
M15_SECONDS = 900
H4_SECONDS = 14_400
M15_BARS = 192
H4_BARS = 72
DEAL_ENTRY_IN = 0
CLOSE_ENTRIES = frozenset({1, 2, 3})
DEFAULT_CACHE = Path("trading/docs/reports/calibration_backfill.json")

# Bar rows are (open_time_server_epoch, open, high, low, close, tick_volume).
BarRow = tuple[int, float, float, float, float, float]
FetchBars = Callable[[str, str, int], Sequence[BarRow]]


def rotation_opens(paths: Iterable[Path | str]) -> dict[int, dict[str, Any]]:
    """TRADE_OPEN rows of the rotation magic keyed by order (== MT5 position id)."""
    out: dict[int, dict[str, Any]] = {}
    for ev in iter_events_files(paths, ("TRADE_OPEN",)):
        try:
            magic = int(ev.get("magic") or 0)
            order = int(ev.get("order") or ev.get("position_id") or 0)
        except (TypeError, ValueError):
            continue
        if magic != ROTATION_MAGIC or order <= 0:
            continue
        out[order] = ev
    return out


def _flip(side: str) -> str:
    return {"BUY": "SELL", "SELL": "BUY"}.get(side, side)


def decision_side(open_ev: Mapping[str, Any], exec_side: str) -> tuple[str, bool]:
    """Consensus side the shadow flags were scored against, and whether the fill was inverted.

    2026-10-01 14:36 .. 2026-10-03 09:10 the bot sent the opposite of the consensus.
    """
    exec_up = str(exec_side or "").upper()
    dec = str(open_ev.get("side_decision") or "").upper()
    if dec in ("BUY", "SELL"):
        if open_ev.get("side_inverted") is not None:
            return dec, bool(open_ev.get("side_inverted"))
        return dec, bool(exec_up and dec != exec_up)
    murphy = open_ev.get("murphy") if isinstance(open_ev.get("murphy"), Mapping) else {}
    m_side = str((murphy or {}).get("side") or "").upper()
    if m_side in ("BUY", "SELL") and exec_up in ("BUY", "SELL"):
        return m_side, m_side != exec_up
    if open_ev.get("side_inverted"):
        return _flip(exec_up), True
    return exec_up, False


def positions_from_deals(deals: Sequence[Mapping[str, Any]]) -> dict[int, dict[str, Any]]:
    """Per rotation position: open time (server epoch), side, closed flag, broker net."""
    grouped: dict[int, list[Mapping[str, Any]]] = defaultdict(list)
    for d in deals:
        try:
            pid = int(d.get("position_id") or 0)
        except (TypeError, ValueError):
            continue
        if pid > 0:
            grouped[pid].append(d)
    out: dict[int, dict[str, Any]] = {}
    for pid, rows in grouped.items():
        ins = [r for r in rows if int(r.get("entry", -1)) == DEAL_ENTRY_IN]
        if not ins or int(ins[0].get("magic") or 0) != ROTATION_MAGIC:
            continue
        outs = [r for r in rows if int(r.get("entry", -1)) in CLOSE_ENTRIES]
        net = sum(
            float(r.get("profit") or 0.0) + float(r.get("swap") or 0.0) + float(r.get("commission") or 0.0)
            for r in rows
        )
        open_t = int(ins[0].get("time_secs") or 0)
        close_t = max((int(r.get("time_secs") or 0) for r in outs), default=0)
        out[pid] = {
            "position_id": pid,
            "symbol": str(ins[0].get("symbol") or ""),
            "side": "BUY" if int(ins[0].get("type", -1)) == 0 else "SELL",
            "open_time": open_t,
            "close_time": close_t or None,
            "closed": bool(outs),
            "net": round(net, 6) if outs else None,
            "close_comment": str(outs[-1].get("comment") or "") if outs else "",
            "duration_seconds": (close_t - open_t) if outs and open_t and close_t else None,
        }
    return out


def _ohlcv(rows: Sequence[BarRow]) -> Ohlcv:
    return Ohlcv.from_lists(
        [r[1] for r in rows],
        [r[2] for r in rows],
        [r[3] for r in rows],
        [r[4] for r in rows],
        [r[5] for r in rows],
    )


def closed_before(rows: Sequence[BarRow], cutoff_epoch: int, tf_seconds: int, count: int) -> list[BarRow]:
    """Last ``count`` bars whose close (open + tf) is <= cutoff. Assumes rows sorted by time."""
    keep = [r for r in rows if int(r[0]) + int(tf_seconds) <= int(cutoff_epoch)]
    return keep[-int(count):] if count > 0 else keep


def features_at(
    *,
    symbol: str,
    side: str,
    open_epoch: int,
    m15_rows: Sequence[BarRow],
    h4_rows: Sequence[BarRow],
) -> dict[str, Any]:
    m15 = closed_before(m15_rows, open_epoch, M15_SECONDS, M15_BARS)
    h4 = closed_before(h4_rows, open_epoch, H4_SECONDS, H4_BARS)
    if len(m15) < 50:
        return {"ok": False, "reason": "pocas_velas_m15", "n_m15": len(m15), "n_h4": len(h4)}
    m15_o = _ohlcv(m15)
    h4_o = _ohlcv(h4) if h4 else None
    alpha = alpha_shadow_from_bars(symbol, side, m15_o)
    murphy = murphy_snapshot(
        side=side,
        m15_highs=m15_o.highs,
        m15_lows=m15_o.lows,
        m15_closes=m15_o.closes,
        h4_highs=h4_o.highs if h4_o else (),
        h4_lows=h4_o.lows if h4_o else (),
        h4_closes=h4_o.closes if h4_o else (),
    )
    return {
        "ok": True,
        "n_m15": len(m15),
        "n_h4": len(h4),
        "last_m15_close_epoch": int(m15[-1][0]) + M15_SECONDS,
        "alpha": alpha,
        "murphy": {
            "would_veto": bool((murphy or {}).get("would_veto")),
            "tag_m15": (murphy or {}).get("tag_m15"),
            "tag_h4": (murphy or {}).get("tag_h4"),
        },
    }


def build_backfill(
    *,
    opens: Mapping[int, Mapping[str, Any]],
    positions: Mapping[int, Mapping[str, Any]],
    fetch_bars: FetchBars,
    m15_count: int,
    h4_count: int,
) -> dict[str, Any]:
    symbols = sorted({str(p.get("symbol") or "") for p in positions.values() if p.get("symbol")})
    bars: dict[str, tuple[list[BarRow], list[BarRow]]] = {}
    for sym in symbols:
        m15 = sorted(fetch_bars(sym, "M15", m15_count) or [], key=lambda r: r[0])
        h4 = sorted(fetch_bars(sym, "H4", h4_count) or [], key=lambda r: r[0])
        bars[sym] = (list(m15), list(h4))
    rows: dict[str, Any] = {}
    n_ok = 0
    for pid, pos in positions.items():
        sym = str(pos.get("symbol") or "")
        m15, h4 = bars.get(sym, ([], []))
        side_dec, inverted = decision_side(opens.get(int(pid)) or {}, str(pos.get("side") or ""))
        feat = features_at(
            symbol=sym,
            side=side_dec,
            open_epoch=int(pos.get("open_time") or 0),
            m15_rows=m15,
            h4_rows=h4,
        )
        n_ok += 1 if feat.get("ok") else 0
        rows[str(pid)] = {
            **dict(pos),
            "in_log": int(pid) in opens,
            "side_decision": side_dec,
            "inverted": inverted,
            "features": feat,
        }
    return {
        "generated_utc": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds"),
        "n_positions": len(positions),
        "n_features_ok": n_ok,
        "n_symbols": len(symbols),
        "rows": rows,
    }


def load_backfill(path: Path | str = DEFAULT_CACHE) -> dict[str, Any]:
    p = Path(path)
    if not p.exists():
        return {}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def main(argv: Optional[list[str]] = None) -> int:
    import argparse

    from trading.infrastructure.mt5_calibration_source import fetch_bars, fetch_rotation_deals

    ap = argparse.ArgumentParser(description="Backfill alpha/Murphy + broker net for rotation fills.")
    ap.add_argument("--since", default="2026-09-29")
    ap.add_argument("--out", default=str(DEFAULT_CACHE))
    args = ap.parse_args(argv)
    try:
        from dotenv import load_dotenv

        load_dotenv(Path(".env"), override=False)
    except ImportError:
        pass

    start = datetime.fromisoformat(args.since)
    deals, err = fetch_rotation_deals(start, datetime.now() + timedelta(days=1))
    if err:
        print("mt5 error:", err)
        return 2
    positions = positions_from_deals(deals)
    opens = rotation_opens(rotation_log_paths(".", since=args.since))
    days = max(1.0, (datetime.now() - start).total_seconds() / 86_400.0)
    m15_count = int(days * 96) + M15_BARS + 50
    h4_count = int(days * 6) + H4_BARS + 20
    report = build_backfill(
        opens=opens, positions=positions, fetch_bars=fetch_bars, m15_count=m15_count, h4_count=h4_count
    )
    dest = Path(args.out)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(report, ensure_ascii=False, default=str), encoding="utf-8")
    print(
        json.dumps(
            {k: report[k] for k in ("n_positions", "n_features_ok", "n_symbols")}
            | {"n_log_opens": len(opens), "out": str(dest)},
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
