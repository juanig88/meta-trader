"""Local Flask dashboard. Rotation mode: 5 modules / 4/5 + HTF veto (see trading/docs/ROTATION_ENGINE.md)."""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

import telegram_notify

from flask import Flask, redirect, render_template, request, url_for

from trading.application.account_pnl import lab_epoch_start, mt5_history_end, rotation_epoch_start, summarize_account_pnl
from trading.domain.rotation_stops import spread_usd_for_lot
from trading.domain.xbtfx import margin_band
from trading.infrastructure.xbtfx_session import rejection_reason

try:
    from dotenv import load_dotenv

    load_dotenv(override=False)
except ImportError:
    pass


STATE_PATH = Path("bot_state.json")

ROOT = Path(__file__).resolve().parent
TRADES_LOG_PATH = ROOT / "trades.log"
_EVAL_EVENT_NAMES = frozenset(
    {
        "EVAL_CYCLE",
        "EVAL_SKIP",
        "EVAL_ENTRY_ATTEMPT",
        "EVAL_TRACKS_BOOT",
    }
)


def parse_recent_eval_events(
    path: Path | None = None,
    *,
    limit: int = 40,
    max_bytes: int = 400_000,
) -> list[dict]:
    """
    Tail-parse trades.log for EVAL_* JSON lines (newest last).
    Pure-ish helper (filesystem only) for dashboard + tests.
    """
    log_path = path or TRADES_LOG_PATH
    if not log_path.is_file():
        return []
    try:
        size = log_path.stat().st_size
        with log_path.open("rb") as f:
            if size > max_bytes:
                f.seek(size - max_bytes)
                f.readline()  # discard partial first line
            raw = f.read().decode("utf-8", errors="replace")
    except OSError:
        return []

    out: list[dict] = []
    for line in raw.splitlines():
        s = line.strip()
        if not s:
            continue
        # logging format: "2026-.. | INFO | {json}"
        if " | " in s:
            s = s.split(" | ", 2)[-1].strip()
        if not (s.startswith("{") and s.endswith("}")):
            continue
        try:
            obj = json.loads(s)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict):
            continue
        ev = str(obj.get("event") or "")
        if ev not in _EVAL_EVENT_NAMES:
            continue
        out.append(obj)
    if limit > 0 and len(out) > limit:
        out = out[-limit:]
    return out


def _tracks_from_env_fallback() -> list[dict]:
    """Si el bot aún no escribió eval_tracks, armar filas desde .env."""
    tracks: list[dict] = []
    raw_syms = (os.getenv("BOT_SYMBOLS") or "").strip()
    symbols = [p.strip() for p in raw_syms.split(",") if p.strip()]
    try:
        cfg_map = json.loads(os.getenv("BOT_SYMBOL_CONFIG") or "{}")
    except json.JSONDecodeError:
        cfg_map = {}
    if not isinstance(cfg_map, dict):
        cfg_map = {}
    for sym in symbols:
        d = cfg_map.get(sym) if isinstance(cfg_map.get(sym), dict) else {}
        tracks.append(
            {
                "track": str(d.get("eval_track") or "main_loop"),
                "symbol": sym,
                "path": "main_loop",
                "window": str(d.get("trading_window_sessions") or "(global/24x5)"),
                "window_tz": os.getenv("BOT_TRADING_WINDOW_TIMEZONE") or "LOCAL",
                "lot": float(d.get("lot") or os.getenv("BOT_LOT") or 0.01),
                "sl_atr_mult": float(d.get("sl_atr_mult") or 0),
                "tp_atr_mult": float(d.get("tp_atr_mult") or 0),
                "adx_min": float(d.get("adx_min") or 0),
                "in_window": None,
                "source": "env",
            }
        )
    return tracks


def build_parallel_tracks_view(state: dict) -> dict:
    """Vista unificada tracks + últimos eventos EVAL para la home del dashboard."""
    et = state.get("eval_tracks") if isinstance(state.get("eval_tracks"), dict) else {}
    tracks = list(et.get("tracks") or [])
    if not tracks:
        tracks = _tracks_from_env_fallback()
    recent = parse_recent_eval_events(limit=50)
    return {
        "tracks": tracks,
        "meta": {
            "ts": et.get("ts"),
            "crypto_sell_requires_h4_down": et.get(
                "crypto_sell_requires_h4_down",
                (os.getenv("BOT_CRYPTO_SELL_REQUIRES_H4_DOWN") or "1").strip() not in ("0", "false", "no"),
            ),
            "daily_loss_limit": et.get("daily_loss_limit", os.getenv("BOT_DAILY_LOSS_LIMIT")),
            "max_open_trades_total": et.get(
                "max_open_trades_total", os.getenv("BOT_MAX_OPEN_TRADES_TOTAL")
            ),
            "from_bot_state": bool(et.get("tracks")),
        },
        "recent_events": list(reversed(recent)),  # newest first in UI
    }

try:
    import analyze_trades  # local module, same directory
except ImportError:
    analyze_trades = None  # type: ignore[assignment]

app = Flask(__name__, template_folder="bot/templates", static_folder="bot/static")
# Sin esto, con debug=False Flask puede cachear plantillas hasta reiniciar el proceso.
app.config["TEMPLATES_AUTO_RELOAD"] = True
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0


def get_bot_state() -> dict:
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _pid_exists_win(pid: int) -> bool:
    """
    Windows: os.kill(pid, 0) is not reliable for existence checks.
    OpenProcess + PROCESS_QUERY_LIMITED_INFORMATION is the usual pattern.
    """
    import ctypes

    k = ctypes.windll.kernel32
    k.SetLastError(0)
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    h = k.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, ctypes.c_uint32(pid))
    if h:
        k.CloseHandle(h)
        return True
    err = k.GetLastError()
    # 87 = ERROR_INVALID_PARAMETER → PID no existe
    if err == 87:
        return False
    # 5 = ACCESS_DENIED → el proceso existe pero no podemos abrir handle (contamos como vivo)
    if err == 5:
        return True
    return False


def _pid_exists(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        return _pid_exists_win(pid)
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    except TypeError:
        return False
    return True


def _coerce_pid(value) -> int | None:
    if value is None:
        return None
    try:
        pid = int(float(value))
    except (TypeError, ValueError):
        return None
    return pid if pid > 0 else None


def compute_health(state: dict) -> dict:
    """
    `bot_state.json` alone cannot prove the process is alive (file can be stale).
    We combine: running flag + heartbeat age + optional PID probe.
    """
    out: dict = {
        "heartbeat_age_sec": None,
        "heartbeat_stale": False,
        "pid": None,
        "pid_alive": None,
    }
    if not state:
        return out

    out["pid"] = _coerce_pid(state.get("pid"))
    ts_raw = state.get("ts")
    if isinstance(ts_raw, str):
        try:
            hb = datetime.fromisoformat(ts_raw)
            age = (datetime.now() - hb).total_seconds()
            out["heartbeat_age_sec"] = round(age, 1)
        except ValueError:
            out["heartbeat_age_sec"] = None

    poll = float(state.get("poll_seconds") or 30)
    threshold = max(90.0, 2.0 * poll + 30.0)
    running = bool(state.get("running"))
    age = out["heartbeat_age_sec"]
    if running and age is not None and age > threshold:
        out["heartbeat_stale"] = True

    pid = out["pid"]
    if pid is not None:
        out["pid_alive"] = _pid_exists(pid)

    return out


def _broker_symbols() -> set[str]:
    """Symbols of the broker we actually trade. Empty set means do not filter."""
    return {
        part.strip().upper()
        for part in (os.getenv("BOT_SYMBOLS") or "").split(",")
        if part.strip()
    }


def _rotation_active(state: dict | None = None) -> bool:
    st = state if isinstance(state, dict) else get_bot_state()
    return bool(st.get("rotation_enabled") or st.get("ensemble"))


def _trade_universe(state: dict | None = None) -> set[str]:
    """Symbols the live dashboard should show. Rotation uses bot_state, not BOT_SYMBOLS leftovers."""
    st = state if isinstance(state, dict) else get_bot_state()
    if _rotation_active(st):
        found = {str(s).strip().upper() for s in (st.get("symbols") or []) if str(s).strip()}
        if found:
            return found
        from trading.domain.crypto_universe import CRYPTO_UNIVERSE

        return {str(s).strip().upper() for s in CRYPTO_UNIVERSE if str(s).strip()}
    return _broker_symbols()


def _rotation_magics(state: dict | None = None) -> set[int]:
    """Single magic the rotation engine sends with. Do not mix eval-track magics into the 60/h count."""
    st = state if isinstance(state, dict) else get_bot_state()
    for raw in (st.get("magic"), os.getenv("BOT_MAGIC"), 260414):
        if raw is None or raw == "":
            continue
        try:
            return {int(float(raw))}
        except (TypeError, ValueError):
            continue
    return {260414}


def _lab_vote_rows(ensemble_pg: dict | None, ensemble_live: dict | None) -> list:
    """PG first if it has vote payloads; else fired/near-miss/module skips from bot_state; else slim chips."""
    pg = list((ensemble_pg or {}).get("latest") or [])
    if pg and any((row.get("votes") or []) for row in pg):
        return pg
    live = ensemble_live or {}
    hot = (
        list(live.get("fired") or [])
        + list(live.get("near_miss") or [])
        + list(live.get("module_skips") or [])
    )
    if hot:
        return hot
    if pg:
        return pg
    return list(live.get("symbols") or [])


def _short_query_msg(msg: str, max_len: int = 200) -> str:
    s = " ".join((msg or "").replace("\r", " ").replace("\n", " ").split())
    if len(s) > max_len:
        return s[: max_len - 1] + "…"
    return s


@app.get("/")
def index():
    state = get_bot_state()
    health = compute_health(state)

    parallel_view = build_parallel_tracks_view(state)

    # Last-trade table: símbolos del bot + tracks + cualquier key en last_trade_time
    last_trade_time = state.get("last_trade_time") or {}
    last_trade_symbols: list[str] = []
    seen: set[str] = set()
    for sym in list(state.get("symbols") or []):
        s = str(sym or "").strip()
        if s and s not in seen:
            last_trade_symbols.append(s)
            seen.add(s)
    for t in parallel_view.get("tracks") or []:
        s = str((t or {}).get("symbol") or "").strip()
        if s and s not in seen:
            last_trade_symbols.append(s)
            seen.add(s)
    for sym in sorted(last_trade_time.keys()):
        s = str(sym or "").strip()
        if s and s not in seen:
            last_trade_symbols.append(s)
            seen.add(s)

    from trading.application.rotation_metrics import rotation_close_rows, summarize_rotation_closes
    from trading.domain.crypto_universe import CRYPTO_UNIVERSE
    from trading.infrastructure.persistence.miner_snapshot import fetch_miner_snapshot

    rotation_on = _rotation_active(state)
    if rotation_on:
        rot_since = rotation_epoch_start()
        today_pnl = _fetch_account_pnl_view(
            since=rot_since,
            window_label=f"desde {rot_since.strftime('%Y-%m-%d')} (rotación)",
        )
        day_pnl = today_pnl
        miner = {"ok": False, "error": ""}
    else:
        today_pnl = _fetch_account_pnl_view(hours=None)
        day_pnl = _fetch_account_pnl_view(since=lab_epoch_start())
        miner = fetch_miner_snapshot(limit=8)

    uni = tuple(str(s).strip() for s in (state.get("symbols") or CRYPTO_UNIVERSE) if str(s).strip())
    rotation_stats = summarize_rotation_closes(
        today_pnl.get("closed") or [],
        today_pnl.get("floating") or [],
        universe=uni or CRYPTO_UNIVERSE,
    )
    rotation_closed = rotation_close_rows(
        today_pnl.get("closed") or [],
        universe=uni or CRYPTO_UNIVERSE,
    )
    cap = int(
        state.get("max_opens_per_hour")
        or (state.get("ensemble") or {}).get("hour_cap")
        or 60
    )
    hourly_opens = _fetch_hourly_opens(
        cap=cap,
        magics=_rotation_magics(state) if rotation_on else _managed_bot_magics(state),
    )

    return render_template(
        "index.html",
        state=state,
        health=health,
        now=datetime.now(),
        telegram_test=request.args.get("telegram_test", ""),
        telegram_test_msg=request.args.get("telegram_test_msg", ""),
        last_trade_symbols=last_trade_symbols,
        parallel_view=parallel_view,
        day_pnl=day_pnl,
        today_pnl=today_pnl,
        miner=miner,
        rotation_stats=rotation_stats,
        rotation_closed=rotation_closed,
        hourly_opens=hourly_opens,
    )


@app.post("/telegram/test")
def telegram_test():
    """Envía un mensaje de prueba al chat configurado en .env (Telegram)."""
    default = f"Test Telegram desde dashboard · {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    text = (request.form.get("text") or "").strip() or default
    ok, err = telegram_notify.send_message_now(text)
    if ok:
        return redirect(url_for("index", telegram_test="ok"))
    return redirect(
        url_for(
            "index",
            telegram_test="err",
            telegram_test_msg=_short_query_msg(err),
        )
    )


@app.get("/telegram/chats")
def telegram_chat_list():
    """Lista chat_id vistos vía getUpdates (solo token; útil si no sabés TELEGRAM_CHAT_ID)."""
    ok, err, chats = telegram_notify.fetch_recent_chat_hints()
    return render_template(
        "telegram_chats.html",
        ok=ok,
        err=err,
        chats=chats,
        now=datetime.now(),
    )


def _mt5_connect_for_readonly() -> tuple[bool, str]:
    """
    Connect this Flask process to MT5 just to read account/positions.
    The bot terminal must be running; both processes can attach to the same terminal.
    Returns (ok, error_message).
    """
    try:
        import MetaTrader5 as mt5
    except ImportError as e:
        return False, f"MetaTrader5 no instalado: {e}"

    if mt5.terminal_info() is not None:
        reason = rejection_reason(mt5)
        return (False, reason) if reason else (True, "")

    login = os.getenv("MT5_LOGIN")
    password = os.getenv("MT5_PASSWORD")
    server = os.getenv("MT5_SERVER")
    path = os.getenv("MT5_PATH") or os.getenv("MT5_TERMINAL_PATH")

    kwargs: dict = {}
    if path:
        kwargs["path"] = path
    try:
        ok = bool(mt5.initialize(**kwargs))
    except Exception as e:
        return False, f"mt5.initialize raised: {e}"
    if not ok:
        return False, f"mt5.initialize returned False: {mt5.last_error()}"

    if login and password and server:
        try:
            ok = bool(mt5.login(int(login), password=password, server=server))
        except (TypeError, ValueError) as e:
            return False, f"mt5.login bad params: {e}"
        if not ok:
            return False, f"mt5.login falló: {mt5.last_error()}"

    reason = rejection_reason(mt5)
    if reason:
        try:
            mt5.shutdown()
        except Exception:
            pass
        return False, reason
    return True, ""


def _int_attr(obj: object, name: str, default: int = 0) -> int:
    """Read an int attribute without collapsing 0 via `or`."""
    raw = getattr(obj, name, default)
    if raw is None:
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def _managed_bot_magics(state: dict | None = None) -> set[int]:
    """Magics the bot owns (main loop + eval tracks). Used to label bot vs manual."""
    st = state if state is not None else get_bot_state()
    managed_magics: set[int] = {260414}
    try:
        env_magic = os.getenv("BOT_MAGIC")
        if env_magic:
            managed_magics.add(int(float(env_magic)))
    except (TypeError, ValueError):
        pass
    et = st.get("eval_tracks") if isinstance(st.get("eval_tracks"), dict) else {}
    for track in et.get("tracks") or []:
        if not isinstance(track, dict):
            continue
        try:
            magic = track.get("magic")
            if magic is not None:
                managed_magics.add(int(float(magic)))
        except (TypeError, ValueError):
            pass
    return managed_magics


def _deal_to_dict(d: object) -> dict:
    t = _int_attr(d, "time", 0)
    return {
        "time": datetime.fromtimestamp(t).isoformat(timespec="seconds") if t > 0 else None,
        "deal": _int_attr(d, "ticket", 0),
        "ticket": _int_attr(d, "ticket", 0),
        "position_id": _int_attr(d, "position_id", 0),
        "symbol": str(getattr(d, "symbol", "") or ""),
        "magic": _int_attr(d, "magic", 0),
        "entry": _int_attr(d, "entry", -1),
        "type": _int_attr(d, "type", -1),
        "volume": float(getattr(d, "volume", 0.0) or 0.0),
        "price": float(getattr(d, "price", 0.0) or 0.0),
        "profit": float(getattr(d, "profit", 0.0) or 0.0),
        "swap": float(getattr(d, "swap", 0.0) or 0.0),
        "commission": float(getattr(d, "commission", 0.0) or 0.0),
        "reason": _int_attr(d, "reason", -1),
        "comment": str(getattr(d, "comment", "") or ""),
    }


def _fetch_hourly_opens(*, cap: int, magics: set[int]) -> dict:
    """Rolling 60m ENTRY_IN deals from the broker, not the in-memory bot counter."""
    from trading.application.rotation_metrics import summarize_hourly_opens

    now = datetime.now()
    start = now - timedelta(hours=1)
    empty = {
        "ok": False,
        "error": "",
        "n": 0,
        "cap": int(cap),
        "remaining": int(cap),
        "at_cap": False,
        "rows": [],
        "window_from": start.isoformat(timespec="seconds"),
        "window_to": now.isoformat(timespec="seconds"),
        "magics": sorted(int(m) for m in magics),
    }
    ok, err = _mt5_connect_for_readonly()
    if not ok:
        empty["error"] = err
        return empty
    import MetaTrader5 as mt5

    deals = mt5.history_deals_get(start, mt5_history_end(now)) or []
    rows = [_deal_to_dict(d) for d in deals]
    out = summarize_hourly_opens(rows, magics=sorted(magics), cap=int(cap))
    out["error"] = ""
    out["ok"] = True
    out["window_from"] = start.isoformat(timespec="seconds")
    out["window_to"] = now.isoformat(timespec="seconds")
    out["magics"] = sorted(int(m) for m in magics)
    return out


def _fetch_account_pnl_view(
    *, hours: float | None = None, since: datetime | None = None, window_label: str | None = None
) -> dict:
    """Live MT5 realized+floating snapshot.

    Priority: explicit ``since`` → local midnight if hours is None → last N hours.
    """
    empty = {
        "ok": False,
        "error": "",
        "window_label": "hoy",
        "realized_profit": 0.0,
        "realized_net": 0.0,
        "bot_realized_net": 0.0,
        "manual_realized_net": 0.0,
        "floating_net": 0.0,
        "account_net": 0.0,
        "n_closed": 0,
        "n_wins": 0,
        "n_losses": 0,
        "closed": [],
        "wins": [],
        "losses": [],
        "floating": [],
        "by_symbol": [],
    }
    ok, err = _mt5_connect_for_readonly()
    if not ok:
        empty["error"] = err
        return empty

    import MetaTrader5 as mt5

    now = datetime.now()
    if since is not None:
        start = since
        empty["window_label"] = f"desde {since.strftime('%Y-%m-%d')} (momento cero)"
    elif hours is None:
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        empty["window_label"] = "hoy (medianoche local)"
    else:
        start = now - timedelta(hours=float(hours))
        empty["window_label"] = f"últimas {int(hours)}h"
    if window_label:
        empty["window_label"] = window_label

    deals = mt5.history_deals_get(start, mt5_history_end(now)) or []
    positions = mt5.positions_get() or []
    allowed = _trade_universe()
    pos_rows = []
    for p in positions:
        pos_rows.append(
            {
                "symbol": str(getattr(p, "symbol", "") or ""),
                "side": "BUY" if int(getattr(p, "type", 0) or 0) == 0 else "SELL",
                "profit": float(getattr(p, "profit", 0.0) or 0.0),
                "swap": float(getattr(p, "swap", 0.0) or 0.0),
                "magic": int(getattr(p, "magic", 0) or 0),
            }
        )
    deal_rows = [_deal_to_dict(d) for d in deals]
    if allowed:
        deal_rows = [
            row
            for row in deal_rows
            if not str(row.get("symbol") or "").strip()
            or str(row.get("symbol")).strip().upper() in allowed
        ]
        pos_rows = [
            row
            for row in pos_rows
            if str(row.get("symbol") or "").strip().upper() in allowed
        ]
    summary = summarize_account_pnl(
        deal_rows,
        pos_rows,
        bot_magics=sorted(_managed_bot_magics()),
    )
    summary["ok"] = True
    summary["error"] = ""
    summary["window_label"] = empty["window_label"]
    return summary


def _fetch_positions_snapshot() -> dict:
    """Return a dict with account info + open positions list (or error)."""
    ok, err = _mt5_connect_for_readonly()
    if not ok:
        return {"ok": False, "error": err, "positions": [], "account": None, "symbols_filter": []}

    import MetaTrader5 as mt5

    state = get_bot_state()
    symbols_filter = [str(s).upper() for s in (state.get("symbols") or [])]
    managed_magics = _managed_bot_magics(state)

    acc = mt5.account_info()
    account = None
    if acc is not None:
        balance = float(getattr(acc, "balance", 0.0) or 0.0)
        equity = float(getattr(acc, "equity", 0.0) or 0.0)
        margin = float(getattr(acc, "margin", 0.0) or 0.0)
        so_call = float(getattr(acc, "margin_so_call", 0.0) or 0.0) or None
        so_so = float(getattr(acc, "margin_so_so", 0.0) or 0.0) or None
        margin_level, margin_status = margin_band(equity, margin, so_call, so_so)
        account = {
            "login": int(getattr(acc, "login", 0) or 0),
            "name": str(getattr(acc, "name", "") or ""),
            "server": str(getattr(acc, "server", "") or ""),
            "currency": str(getattr(acc, "currency", "") or "USD"),
            "balance": balance,
            "equity": equity,
            "margin": margin,
            "margin_free": float(getattr(acc, "margin_free", 0.0) or 0.0),
            "profit": float(getattr(acc, "profit", 0.0) or 0.0),
            "margin_level": margin_level,
            "margin_so_call": so_call,
            "margin_so_so": so_so,
            "margin_status": margin_status,
        }

    rows: list[dict] = []
    positions = mt5.positions_get() or []
    for p in positions:
        sym = str(getattr(p, "symbol", "") or "")
        if symbols_filter and sym.upper() not in symbols_filter:
            continue
        sinfo = mt5.symbol_info(sym)
        digits = int(getattr(sinfo, "digits", 2)) if sinfo is not None else 2
        tick = mt5.symbol_info_tick(sym)
        cur_price = None
        spread_pts = None
        spread_usd = None
        if tick is not None:
            cur_price = float(tick.bid if int(getattr(p, "type", 0)) == 1 else tick.ask)
            bid = float(getattr(tick, "bid", 0.0) or 0.0)
            ask = float(getattr(tick, "ask", 0.0) or 0.0)
            point = float(getattr(sinfo, "point", 0.0) or 0.0) if sinfo is not None else 0.0
            if ask > 0.0 and bid > 0.0 and point > 0.0:
                spread_pts = (ask - bid) / point
            if sinfo is not None and ask > 0.0 and bid > 0.0:
                hole = spread_usd_for_lot(
                    spread_px=ask - bid,
                    lot=float(getattr(p, "volume", 0.0) or 0.0),
                    tick_value=float(getattr(sinfo, "trade_tick_value", 0.0) or 0.0),
                    tick_size=float(getattr(sinfo, "trade_tick_size", 0.0) or 0.0),
                )
                if hole is not None:
                    spread_usd = round(float(hole), 4)
        try:
            magic = int(getattr(p, "magic", 0) or 0)
        except (TypeError, ValueError):
            magic = 0
        managed = (magic in managed_magics) or (magic == 0 and bool(managed_magics))
        rows.append(
            {
                "ticket": int(getattr(p, "ticket", 0) or 0),
                "symbol": sym,
                "side": "BUY" if int(getattr(p, "type", 0)) == 0 else "SELL",
                "volume": float(getattr(p, "volume", 0.0) or 0.0),
                "price_open": float(getattr(p, "price_open", 0.0) or 0.0),
                "price_current": cur_price,
                "sl": float(getattr(p, "sl", 0.0) or 0.0),
                "tp": float(getattr(p, "tp", 0.0) or 0.0),
                "profit": float(getattr(p, "profit", 0.0) or 0.0),
                "swap": float(getattr(p, "swap", 0.0) or 0.0),
                "spread_pts": spread_pts,
                "spread_usd": spread_usd,
                "magic": magic,
                "managed_by_bot": bool(managed),
                "digits": digits,
                "time": int(getattr(p, "time", 0) or 0),
            }
        )

    rows.sort(key=lambda r: (r["symbol"], r["ticket"]))

    group_exposure = _compute_group_exposure(rows)
    return {
        "ok": True,
        "error": "",
        "positions": rows,
        "account": account,
        "symbols_filter": symbols_filter,
        "group_exposure": group_exposure,
    }


def _load_correlation_groups_env() -> tuple[dict[str, list[str]], int]:
    """Read BOT_CORRELATION_GROUPS + BOT_MAX_POSITIONS_PER_GROUP from the environment.

    Returns ({group: [symbols]}, cap). Empty dict on missing/invalid JSON.
    """
    raw = os.getenv("BOT_CORRELATION_GROUPS", "").strip()
    groups: dict[str, list[str]] = {}
    if raw:
        try:
            data = json.loads(raw)
            if isinstance(data, dict):
                for k, v in data.items():
                    if isinstance(v, list):
                        groups[str(k)] = [str(x).strip() for x in v if str(x).strip()]
        except Exception:
            groups = {}
    try:
        cap = int(float(os.getenv("BOT_MAX_POSITIONS_PER_GROUP", "0") or "0"))
    except (TypeError, ValueError):
        cap = 0
    return groups, cap


def _compute_group_exposure(positions: list[dict]) -> list[dict]:
    """Return per-group exposure stats based on currently open positions.

    Each row: {group, members, cap, open_count, symbols_open, blocked, net}.
    """
    groups, cap = _load_correlation_groups_env()
    if not groups:
        return []
    out: list[dict] = []
    for gname, members in groups.items():
        memberset = {m.upper() for m in members}
        in_group = [r for r in positions if str(r.get("symbol", "")).upper() in memberset]
        net = sum(float(r.get("profit", 0.0) or 0.0) + float(r.get("swap", 0.0) or 0.0) for r in in_group)
        out.append(
            {
                "group": gname,
                "members": members,
                "cap": cap,
                "open_count": len(in_group),
                "symbols_open": sorted({str(r.get("symbol", "")) for r in in_group}),
                "blocked": bool(cap > 0 and len(in_group) >= cap),
                "net": net,
            }
        )
    out.sort(key=lambda r: (-r["open_count"], r["group"]))
    return out


@app.get("/positions")
def positions_view():
    snap = _fetch_positions_snapshot()
    total_profit = sum(float(r.get("profit", 0.0)) + float(r.get("swap", 0.0)) for r in snap["positions"])
    return render_template(
        "positions.html",
        now=datetime.now(),
        ok=snap["ok"],
        error=snap["error"],
        positions=snap["positions"],
        account=snap["account"],
        symbols_filter=snap["symbols_filter"],
        total_profit=total_profit,
        group_exposure=snap.get("group_exposure", []),
    )


_SESSION_ORDER = ["LONDON", "LON_NY_OVERLAP", "NY", "QUIET", "ASIA", "unknown"]
_ADX_ORDER = [
    "range (<15)",
    "weak (15-20)",
    "developing (20-25)",
    "strong (25-35)",
    "very strong (>=35)",
    "unknown",
]
_WEEKDAY_NAMES = {
    "0": "Mon",
    "1": "Tue",
    "2": "Wed",
    "3": "Thu",
    "4": "Fri",
    "5": "Sat",
    "6": "Sun",
}
_EVENT_ORDER = [
    "SIGNAL",
    "TRADE_OPEN",
    "TRADE_CLOSE",
    "BREAK_EVEN",
    "TRAILING_UPDATE",
    "TIGHT_TRAIL",
    "FILTER_EXIT",
    "BAR_EXHAUSTION_SKIP",
    "DUMP_LOCK_SKIP",
    "LOT_SIZE_SKIP",
    "QUIET_SESSION_SKIP",
    "CORRELATION_SKIP",
    "PORTFOLIO_REBALANCE",
    "RECONCILE_POSITIONS",
]


def _parse_journal_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(s, fmt)
        except Exception:
            continue
    return None


def _ordered_bucket_list(
    bucket_dict: dict[str, dict], order: list[str] | None = None
) -> list[tuple[str, dict]]:
    """Return list of (label, bucket) sorted by `order` (missing appended at end), else by -net."""
    if not bucket_dict:
        return []
    if order:
        ordered = [(k, bucket_dict[k]) for k in order if k in bucket_dict]
        extra = [(k, v) for k, v in bucket_dict.items() if k not in order]
        return ordered + sorted(extra, key=lambda x: -float(x[1].get("net", 0) or 0))
    return sorted(bucket_dict.items(), key=lambda x: -float(x[1].get("net", 0) or 0))


@app.get("/lab")
def lab_view():
    """Cinta de mercado + scores del alpha sombra (Postgres). Fail-open si Docker está down."""
    from trading.infrastructure.persistence.lab_snapshot import fetch_lab_snapshot
    from trading.infrastructure.persistence.ensemble_store import fetch_latest_ensemble

    state = get_bot_state()
    rotation_on = _rotation_active(state)
    epoch = lab_epoch_start()
    if rotation_on:
        snap = {
            "ok": True,
            "error": "",
            "pg": "",
            "counts": {"ticks": 0, "bars": 0, "scores": 0},
            "latest": [],
            "history": [],
            "ticks": [],
            "bars": [],
            "losers": {},
            "partitions": [],
        }
        recent_pnl = {}
    else:
        snap = fetch_lab_snapshot(history_limit=60, since=epoch, symbols=_broker_symbols())
        recent_pnl = _fetch_account_pnl_view(since=epoch)
    ensemble_live = state.get("ensemble") or {}
    ensemble_pg = fetch_latest_ensemble()
    lab_vote_rows = _lab_vote_rows(ensemble_pg, ensemble_live)
    try:
        from trading.application.four_five_lab import four_five_rows

        fires = four_five_rows(TRADES_LOG_PATH, limit=400, max_bytes=16 * 1024 * 1024)
    except Exception:
        fires = []
    fires_sent = [r for r in fires if r.get("action") != "skip"][:30]
    fires_skip_counts: dict[str, int] = {}
    for r in fires:
        if r.get("action") == "skip":
            key = str(r.get("reason") or "skip")
            fires_skip_counts[key] = fires_skip_counts.get(key, 0) + 1
    return render_template(
        "lab.html",
        now=datetime.now(),
        snap=snap,
        recent_pnl=recent_pnl,
        ensemble_live=ensemble_live,
        ensemble_pg=ensemble_pg,
        rotation_on=rotation_on,
        lab_vote_rows=lab_vote_rows,
        fires_n=len(fires),
        fires_sent=fires_sent,
        fires_skip_counts=sorted(fires_skip_counts.items(), key=lambda kv: -kv[1]),
        kill_switch=state.get("kill_switch") or {},
    )


@app.get("/miner")
def miner_view():
    """Cierres manuales y el último resumen del minero. Fail-open si Postgres está down."""
    from trading.infrastructure.persistence.miner_snapshot import fetch_miner_snapshot

    return render_template("miner.html", now=datetime.now(), snap=fetch_miner_snapshot())


@app.get("/journal")
def journal_view():
    """
    Render the trading journal report from trades.log.
    Query params:
      - days=N     : last N days window (takes precedence over since/until)
      - since=YYYY-MM-DD (or full ISO)
      - until=YYYY-MM-DD (or full ISO)
    """
    from datetime import timedelta as _td

    error: str | None = None
    summary: dict | None = None
    log_exists = TRADES_LOG_PATH.exists()

    days_raw = request.args.get("days", "").strip()
    since_raw = request.args.get("since", "").strip()
    until_raw = request.args.get("until", "").strip()

    since_dt: datetime | None = None
    until_dt: datetime | None = None
    if days_raw:
        try:
            n = int(days_raw)
            if n > 0:
                since_dt = datetime.now() - _td(days=n)
        except ValueError:
            error = f"Parámetro 'days' inválido: {days_raw!r}"
    if since_dt is None and since_raw:
        since_dt = _parse_journal_dt(since_raw)
        if since_raw and since_dt is None:
            error = f"Parámetro 'since' inválido: {since_raw!r}"
    if until_raw:
        until_dt = _parse_journal_dt(until_raw)
        if until_raw and until_dt is None:
            error = f"Parámetro 'until' inválido: {until_raw!r}"

    if analyze_trades is None:
        error = error or "No se pudo importar analyze_trades.py. ¿Está en la misma carpeta que flask_dashboard.py?"
    elif not log_exists:
        error = error or f"No existe {TRADES_LOG_PATH}. El bot todavía no generó trades."
    elif error is None:
        try:
            summary = analyze_trades.analyze_log(
                str(TRADES_LOG_PATH),
                since=since_dt,
                until=until_dt,
                top_n=30,
                symbols=_broker_symbols() or None,
            )
        except Exception as exc:
            error = f"Error procesando trades.log: {exc}"

    # Pre-arrange buckets in stable order for the template.
    ctx_lists: dict[str, list] = {}
    if summary:
        by_hour_raw = summary.get("by_hour", {})
        by_hour_sorted = sorted(
            by_hour_raw.items(),
            key=lambda x: (x[0] in ("None", "unknown"), int(x[0]) if x[0].isdigit() else 99),
        )
        ctx_lists["by_hour"] = [
            (f"{int(k):02d}h" if k.isdigit() else k, v) for k, v in by_hour_sorted
        ]
        ctx_lists["by_symbol"] = _ordered_bucket_list(summary.get("by_symbol", {}))
        ctx_lists["by_side"] = _ordered_bucket_list(summary.get("by_side", {}))
        ctx_lists["by_session"] = _ordered_bucket_list(
            summary.get("by_session", {}), _SESSION_ORDER
        )
        ctx_lists["by_adx_bucket"] = _ordered_bucket_list(
            summary.get("by_adx_bucket", {}), _ADX_ORDER
        )
        ctx_lists["by_weekday"] = [
            (_WEEKDAY_NAMES.get(k, k), v)
            for k, v in sorted(
                summary.get("by_weekday", {}).items(),
                key=lambda x: (x[0] == "None", int(x[0]) if x[0].isdigit() else 9),
            )
        ]
        ctx_lists["by_close_reason"] = sorted(
            summary.get("by_close_reason", {}).items(),
            key=lambda x: -int(x[1].get("n", 0)),
        )

        funnel_rows = []
        for sym, d in sorted(summary.get("signal_funnel", {}).items()):
            total = int(d.get("total", 0) or 0)
            if total <= 0:
                continue
            buys = int(d.get("BUY", 0) or 0)
            sells = int(d.get("SELL", 0) or 0)
            nones = int(d.get("NONE", 0) or 0)
            act_rate = (buys + sells) / total * 100.0
            funnel_rows.append(
                {
                    "symbol": sym,
                    "total": total,
                    "NONE": nones,
                    "BUY": buys,
                    "SELL": sells,
                    "act_rate": act_rate,
                }
            )
        ctx_lists["signal_funnel"] = funnel_rows

        ev_counts = summary.get("event_counts", {})
        ctx_lists["event_counts"] = [(e, int(ev_counts.get(e, 0) or 0)) for e in _EVENT_ORDER]

    return render_template(
        "journal.html",
        now=datetime.now(),
        summary=summary,
        error=error,
        log_path=str(TRADES_LOG_PATH),
        days=days_raw,
        since=since_raw,
        until=until_raw,
        ctx=ctx_lists,
    )


def main() -> None:
    # Local-only for safety
    app.run(host="127.0.0.1", port=8000, debug=False)


if __name__ == "__main__":
    main()

