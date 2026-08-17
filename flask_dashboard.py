from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import telegram_notify

from flask import Flask, redirect, render_template, request, Response, url_for

from path_utils import safe_under_root

try:
    from dotenv import load_dotenv

    load_dotenv(override=False)
except ImportError:
    pass


STATE_PATH = Path("bot_state.json")
BACKTEST_PATH = Path("backtest_results.jsonl")
WALKFORWARD_PATH = Path("walkforward.jsonl")
SCAN_PATH = Path("scan_results_run.jsonl")
SCAN_PATH_ALT = Path("scan_results.jsonl")
PORTFOLIO_RESULTS_PATH = Path("portfolio_results.jsonl")
PORTFOLIO_GRID_PATH = Path("portfolio_grid.jsonl")
FORWARD_RESULTS_PATH = Path("forward_results.jsonl")

ROOT = Path(__file__).resolve().parent
FORWARD_SCRIPT = ROOT / "forward_metrics.py"
TRADES_LOG_PATH = ROOT / "trades.log"
_EVAL_EVENT_NAMES = frozenset(
    {
        "EVAL_CYCLE",
        "EVAL_SKIP",
        "EVAL_ENTRY_ATTEMPT",
        "EVAL_TRACKS_BOOT",
        "XAUUSD_PARALLEL_SKIP",
        "XAUUSD_PARALLEL_SIGNAL",
        "XAUUSD_PARALLEL_OPEN",
        "XAUUSD_PARALLEL_CLOSE",
        "XAUUSD_PARALLEL_BROKER_CLOSE",
    }
)


def parse_recent_eval_events(
    path: Path | None = None,
    *,
    limit: int = 40,
    max_bytes: int = 400_000,
) -> list[dict]:
    """
    Tail-parse trades.log for EVAL_* / XAUUSD_PARALLEL_* JSON lines (newest last).
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
    if (os.getenv("BOT_XAUUSD_PARALLEL_ENABLED") or "").strip().lower() in ("1", "true", "yes", "on"):
        sessions = (os.getenv("BOT_XAUUSD_PARALLEL_SESSIONS") or "").strip()
        if sessions:
            window = sessions.replace(",", " · ")
        else:
            start_h = int(float(os.getenv("BOT_XAUUSD_PARALLEL_START_HOUR", "10")))
            end_h = int(float(os.getenv("BOT_XAUUSD_PARALLEL_END_HOUR", "14")))
            window = f"{start_h:02d}:00-{end_h:02d}:00"
        tracks.append(
            {
                "track": "xau_parallel_scalp",
                "symbol": (os.getenv("BOT_XAUUSD_PARALLEL_SYMBOL") or "XAUUSD").strip().upper(),
                "path": "parallel",
                "window": window,
                "window_tz": "LOCAL",
                "lot": float(os.getenv("BOT_XAUUSD_PARALLEL_LOT", "0.01")),
                "sl_mode": (os.getenv("BOT_XAUUSD_PARALLEL_SL_MODE") or "atr").strip().lower(),
                "sl_atr_mult": float(os.getenv("BOT_XAUUSD_PARALLEL_SL_ATR_MULT", "1.5")),
                "tp_rr": float(os.getenv("BOT_XAUUSD_PARALLEL_TP_RR", "2.0")),
                "adx_min": float(os.getenv("BOT_XAUUSD_PARALLEL_ADX_MIN", "22")),
                "in_window": None,
                "source": "env",
            }
        )
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


def _forward_latest_summary() -> dict:
    """Última corrida útil en home (desde forward_results.jsonl)."""
    rows = _read_jsonl(FORWARD_RESULTS_PATH)
    if not rows:
        return {}
    last = max(rows, key=lambda r: str(r.get("ts") or ""))
    b = last.get("baseline") or {}
    fl = last.get("from_log") or {}
    return {
        "ts": last.get("ts"),
        "days": last.get("days"),
        "window_start": last.get("window_start"),
        "window_end": last.get("window_end"),
        "magic": last.get("magic"),
        "symbols": last.get("symbols") or [],
        "deals_matched": last.get("deals_matched"),
        "deals_out_matched": last.get("deals_out_matched"),
        "outs_per_day": last.get("outs_per_day"),
        "pnl_total": last.get("pnl_total"),
        "winrate_out_deals": last.get("winrate_out_deals"),
        "trades_per_day_backtest": b.get("trades_per_day_backtest"),
        "opens_log": fl.get("opens_matched"),
    }


def _short_query_msg(msg: str, max_len: int = 200) -> str:
    s = " ".join((msg or "").replace("\r", " ").replace("\n", " ").split())
    if len(s) > max_len:
        return s[: max_len - 1] + "…"
    return s


def _market_regime_export_text(state: dict) -> str:
    """
    Texto plano para copiar y pegar (p. ej. al asistente) con régimen actual y parches sugeridos.
    """
    regimes = state.get("market_regimes") or {}
    if not isinstance(regimes, dict) or not regimes:
        return ""

    ts = state.get("ts") or ""
    lines: list[str] = [
        "--- market_regime_export (meta-trader) ---",
        f"bot_state ts: {ts}",
        "",
        "Régimen detectado por símbolo:",
    ]
    for sym in sorted(regimes.keys()):
        r = regimes[sym]
        if not isinstance(r, dict):
            continue
        lines.append(f"  {sym}: {r.get('regime', '?')}")

    patches: dict[str, dict] = {}
    for sym in sorted(regimes.keys()):
        r = regimes.get(sym)
        if not isinstance(r, dict):
            continue
        p = r.get("suggested_config_patch")
        if isinstance(p, dict) and p:
            patches[sym] = p

    lines += [
        "",
        "Nota: régimen = telemetría. Los filtros NO se mutan en caliente "
        "(BOT_MARKET_REGIME_AUTO_APPLY=0 por defecto). "
        "Lo de abajo es referencia opcional si decidís editar BOT_SYMBOL_CONFIG a mano; "
        "no borres sl_points/tp_points/ema_*/spreads. Reinicio solo si vos cambiaste .env.",
        "",
    ]

    if not patches:
        lines.append(
            "(No hay suggested_config_patch en este bot_state: reiniciá el bot con la versión actual "
            "de market_regime.py para rellenar la referencia.)"
        )
        lines.append("--- fin ---")
        return "\n".join(lines)

    lines.append("REFERENCIA opcional (JSON indentado; NO auto-aplicada):")
    lines.append(json.dumps(patches, indent=2, ensure_ascii=False))
    lines.append("")
    lines.append("REFERENCIA (una línea; solo si editás .env a propósito):")
    lines.append(json.dumps(patches, separators=(",", ":"), ensure_ascii=False))
    lines.append("")
    lines.append("--- fin ---")
    return "\n".join(lines)


@app.get("/")
def index():
    state = get_bot_state()
    health = compute_health(state)

    # Extraemos la data de XAUUSD Paralelo si existe
    xauusd_state = state.get("xauusd_parallel", {}) or {}
    parallel_view = build_parallel_tracks_view(state)

    # Last-trade table: símbolos del bot + XAUUSD paralelo + cualquier key en last_trade_time
    last_trade_time = state.get("last_trade_time") or {}
    last_trade_symbols: list[str] = []
    seen: set[str] = set()
    for sym in list(state.get("symbols") or []):
        s = str(sym or "").strip()
        if s and s not in seen:
            last_trade_symbols.append(s)
            seen.add(s)
    xau_sym = str(xauusd_state.get("symbol") or "").strip() if xauusd_state.get("enabled") else ""
    if xau_sym and xau_sym not in seen:
        last_trade_symbols.append(xau_sym)
        seen.add(xau_sym)
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

    return render_template(
        "index.html",
        state=state,
        health=health,
        now=datetime.now(),
        forward_latest=_forward_latest_summary(),
        forward_ok=request.args.get("forward_ok"),
        forward_err=request.args.get("forward_err", ""),
        forward_rc=request.args.get("forward_rc", type=int),
        telegram_test=request.args.get("telegram_test", ""),
        telegram_test_msg=request.args.get("telegram_test_msg", ""),
        regime_export_text=_market_regime_export_text(state),
        xauusd_state=xauusd_state,
        last_trade_symbols=last_trade_symbols,
        parallel_view=parallel_view,
    )


@app.get("/export/market-regime")
def export_market_regime():
    """Texto plano: copiar desde navegador o curl para pegar en chat / archivo."""
    state = get_bot_state()
    body = _market_regime_export_text(state)
    if not body.strip():
        body = "(Sin market_regimes en bot_state.json o bot detenido / estado vacío.)\n"
    return Response(
        body,
        mimetype="text/plain; charset=utf-8",
        headers={"Content-Disposition": 'inline; filename="market_regime_export.txt"'},
    )


@app.post("/forward/run")
def forward_run():
    """
    Ejecuta forward_metrics.py en un subproceso (nuevo intérprete + MT5).
    Si el bot live también usa la API MT5 desde Python, puede haber conflicto: en ese caso corré el script a mano.
    """
    if not FORWARD_SCRIPT.is_file():
        return redirect(url_for("index", forward_err="missing_script"))

    raw_days = (request.form.get("days") or "14").strip()
    try:
        days = max(1, min(366, int(raw_days)))
    except ValueError:
        days = 14

    cmd = [
        sys.executable,
        str(FORWARD_SCRIPT),
        "--days",
        str(days),
        "--out",
        str(ROOT / FORWARD_RESULTS_PATH),
    ]
    if TRADES_LOG_PATH.is_file():
        cmd.extend(["--log-trades", str(TRADES_LOG_PATH)])
    baseline = ROOT / PORTFOLIO_RESULTS_PATH.name
    if baseline.is_file():
        cmd.extend(["--baseline", str(baseline)])

    try:
        proc = subprocess.run(
            cmd,
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=120,
            encoding="utf-8",
            errors="replace",
        )
    except subprocess.TimeoutExpired:
        return redirect(url_for("index", forward_err="timeout"))
    except OSError as e:
        app.logger.exception("forward/run: could not start subprocess")
        return redirect(url_for("index", forward_err="os", forward_rc=-1))

    if proc.returncode != 0:
        app.logger.warning(
            "forward_metrics exit=%s stderr=%s stdout_tail=%s",
            proc.returncode,
            (proc.stderr or "")[:2000],
            (proc.stdout or "")[-500:],
        )
        return redirect(url_for("index", forward_err="nonzero", forward_rc=proc.returncode))

    return redirect(url_for("index", forward_ok="1"))


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


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            if isinstance(obj, dict):
                out.append(obj)
        except Exception:
            continue
    return out


def _jsonl_line_count(path: Path) -> int:
    if not path.exists():
        return 0
    n = 0
    try:
        with path.open(encoding="utf-8", errors="replace") as f:
            for line in f:
                if line.strip():
                    n += 1
    except OSError:
        return 0
    return n


def _scan_source_path() -> Path:
    """Prefer scan_results_run.jsonl; fall back to scan_results.jsonl if the first is empty/missing."""
    if _jsonl_line_count(SCAN_PATH) > 0:
        return SCAN_PATH
    if _jsonl_line_count(SCAN_PATH_ALT) > 0:
        return SCAN_PATH_ALT
    return SCAN_PATH


def _coerce_float(v, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _has_money_row(r: dict) -> bool:
    return "pnl_money" in r and r.get("pnl_money") is not None


def _backtest_files() -> list[Path]:
    """
    Candidate backtest JSONL files to show in dashboard.
    Includes default backtest_results.jsonl, every backtest*.jsonl in project root
    (new runs like backtest_sl_be_profit_lock.jsonl), and walkforward.jsonl.
    """
    files: list[Path] = [BACKTEST_PATH]
    for p in sorted(ROOT.glob("backtest*.jsonl")):
        if p.name != BACKTEST_PATH.name:
            files.append(Path(p.name))
    files.append(WALKFORWARD_PATH)

    seen: set[str] = set()
    out: list[Path] = []
    for p in files:
        name = p.name
        if name in seen:
            continue
        seen.add(name)
        out.append(p)
    return out


def _reports_backtest_hub_rows() -> list[tuple[str, str, Path]]:
    """Human labels and URLs for /reports hub (one row per selectable backtest file)."""
    rows: list[tuple[str, str, Path]] = []
    for p in _backtest_files():
        name = p.name
        if name == WALKFORWARD_PATH.name:
            label = "Walk-forward"
        elif name == BACKTEST_PATH.name:
            label = "Backtests (default)"
        elif name.startswith("backtest_ab"):
            label = f"Backtests A/B ({name})"
        else:
            label = f"Backtests ({name})"
        rows.append((label, f"/backtests?file={name}", Path(name)))
    return rows


def _select_backtest_path() -> Path:
    """
    Pick which JSONL to render on /backtests via ?file=filename.jsonl.
    Defaults to backtest_results.jsonl.
    """
    allowed = {p.name for p in _backtest_files()}
    requested = (request.args.get("file") or "").strip()
    name = requested or BACKTEST_PATH.name
    try:
        return safe_under_root(name, root=ROOT, allowed_names=allowed)
    except ValueError:
        return safe_under_root(BACKTEST_PATH.name, root=ROOT, allowed_names=allowed)


@app.get("/backtests")
def backtests():
    src = _select_backtest_path()
    rows = _read_jsonl(src)
    for r in rows:
        pnl = _coerce_float(r.get("pnl_points"), 0.0)
        dd = _coerce_float(r.get("max_drawdown_points"), 0.0)  # negative
        r["_pnl"] = pnl
        r["_dd"] = dd
        r["_score"] = pnl + dd  # conservative: drawdown reduces score (dd is negative)

        if _has_money_row(r):
            pnl_money = _coerce_float(r.get("pnl_money"), 0.0)
            dd_money = _coerce_float(r.get("max_drawdown_money"), 0.0)  # negative
            r["_pnl_money"] = pnl_money
            r["_dd_money"] = dd_money
            r["_score_money"] = pnl_money + dd_money
            r["_has_money"] = True
        else:
            r["_pnl_money"] = None
            r["_dd_money"] = None
            r["_score_money"] = None
            r["_has_money"] = False

    # sorting via ?sort=score|pnl|dd|winrate|trades (default score)
    sort_key = (request.args.get("sort") or "score").lower()

    key_map = {
        "score": lambda r: _coerce_float(r.get("_score"), 0.0),
        "pnl": lambda r: _coerce_float(r.get("_pnl"), 0.0),
        "dd": lambda r: _coerce_float(r.get("_dd"), 0.0),
        "score_money": lambda r: (
            float("-inf") if not r.get("_has_money") else _coerce_float(r.get("_score_money"), 0.0)
        ),
        "pnl_money": lambda r: (
            float("-inf") if not r.get("_has_money") else _coerce_float(r.get("_pnl_money"), 0.0)
        ),
        "dd_money": lambda r: (
            float("-inf") if not r.get("_has_money") else _coerce_float(r.get("_dd_money"), 0.0)
        ),
        "winrate": lambda r: _coerce_float(r.get("winrate"), 0.0),
        "trades": lambda r: int(_coerce_float(r.get("trades"), 0.0)),
    }
    key_fn = key_map.get(sort_key, key_map["score"])

    rows_sorted = sorted(rows, key=key_fn, reverse=True)
    return render_template(
        "backtests.html",
        rows=rows_sorted,
        now=datetime.now(),
        file_exists=src.exists(),
        filename=src.name,
        files=[p.name for p in _backtest_files()],
        sort=sort_key,
    )


@app.get("/scans")
def scans():
    src = _scan_source_path()
    rows = _read_jsonl(src)
    for r in rows:
        pnl = _coerce_float(r.get("pnl_points"), 0.0)
        dd = _coerce_float(r.get("max_drawdown_points"), 0.0)
        r["_score"] = pnl + dd
    rows_sorted = sorted(rows, key=lambda r: _coerce_float(r.get("_score"), 0.0), reverse=True)
    return render_template(
        "scans.html",
        rows=rows_sorted,
        now=datetime.now(),
        file_exists=src.exists(),
        source_file=src.name,
    )


@app.get("/reports")
def reports():
    """Hub: todos los JSONL que el proyecto va generando."""
    files = _reports_backtest_hub_rows()
    files.extend(
        [
            ("Scan / ranking", "/scans", _scan_source_path()),
            ("Portfolio backtest", "/portfolio", PORTFOLIO_RESULTS_PATH),
            ("Portfolio grid search", "/portfolio-grid", PORTFOLIO_GRID_PATH),
            ("Forward metrics (live)", "/forward", FORWARD_RESULTS_PATH),
        ]
    )
    items = []
    for label, url, p in files:
        items.append(
            {
                "label": label,
                "url": url,
                "filename": p.name,
                "lines": _jsonl_line_count(p),
                "exists": p.exists(),
            }
        )
    return render_template("reports.html", items=items, now=datetime.now())


@app.get("/portfolio")
def portfolio():
    rows = _read_jsonl(PORTFOLIO_RESULTS_PATH)
    rows_sorted = sorted(rows, key=lambda r: str(r.get("ts") or ""), reverse=True)
    return render_template(
        "portfolio.html",
        rows=rows_sorted,
        now=datetime.now(),
        file_exists=PORTFOLIO_RESULTS_PATH.exists(),
        filename=PORTFOLIO_RESULTS_PATH.name,
    )


@app.get("/portfolio-grid")
def portfolio_grid():
    rows = _read_jsonl(PORTFOLIO_GRID_PATH)
    rows_sorted = sorted(rows, key=lambda r: float(r.get("final_balance") or 0.0), reverse=True)
    return render_template(
        "portfolio_grid.html",
        rows=rows_sorted,
        now=datetime.now(),
        file_exists=PORTFOLIO_GRID_PATH.exists(),
        filename=PORTFOLIO_GRID_PATH.name,
    )


@app.get("/forward")
def forward():
    rows = _read_jsonl(FORWARD_RESULTS_PATH)
    rows_sorted = sorted(rows, key=lambda r: str(r.get("ts") or ""), reverse=True)
    latest_pretty = ""
    if rows_sorted:
        latest_pretty = json.dumps(rows_sorted[0], ensure_ascii=False, indent=2, default=str)
    return render_template(
        "forward.html",
        rows=rows_sorted,
        latest_pretty=latest_pretty,
        now=datetime.now(),
        file_exists=FORWARD_RESULTS_PATH.exists(),
        filename=FORWARD_RESULTS_PATH.name,
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
        return True, ""

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

    return True, ""


def _fetch_positions_snapshot() -> dict:
    """Return a dict with account info + open positions list (or error)."""
    ok, err = _mt5_connect_for_readonly()
    if not ok:
        return {"ok": False, "error": err, "positions": [], "account": None, "symbols_filter": []}

    import MetaTrader5 as mt5

    state = get_bot_state()
    symbols_filter = [str(s).upper() for s in (state.get("symbols") or [])]
    xau_st = state.get("xauusd_parallel") or {}
    if isinstance(xau_st, dict) and xau_st.get("enabled"):
        xau_sym = str(xau_st.get("symbol") or "XAUUSD").strip().upper()
        if xau_sym and xau_sym not in symbols_filter:
            symbols_filter.append(xau_sym)
    # Magics gestionados: loop + scalper XAU
    managed_magics: set[int] = set()
    try:
        env_magic = os.getenv("BOT_MAGIC")
        if env_magic:
            managed_magics.add(int(float(env_magic)))
    except (TypeError, ValueError):
        pass
    try:
        if xau_st.get("enabled") and xau_st.get("magic") is not None:
            managed_magics.add(int(float(xau_st.get("magic"))))
        else:
            xm = os.getenv("BOT_XAUUSD_PARALLEL_MAGIC")
            if xm:
                managed_magics.add(int(float(xm)))
    except (TypeError, ValueError):
        pass

    acc = mt5.account_info()
    account = None
    if acc is not None:
        balance = float(getattr(acc, "balance", 0.0) or 0.0)
        equity = float(getattr(acc, "equity", 0.0) or 0.0)
        margin = float(getattr(acc, "margin", 0.0) or 0.0)
        # Margin level % = equity / margin * 100. Undefined when margin == 0
        # (no open positions requiring collateral). MT5 also exposes
        # margin_so_call / margin_so_so: stop-out thresholds configured by the
        # broker (typical demo accounts: 100%/50%).
        margin_level = (equity / margin * 100.0) if margin > 0 else None
        so_call = float(getattr(acc, "margin_so_call", 0.0) or 0.0) or None
        so_so = float(getattr(acc, "margin_so_so", 0.0) or 0.0) or None
        if margin_level is None:
            margin_status = "none"  # no positions, no risk
        elif so_so and margin_level <= so_so * 1.2:
            margin_status = "critical"  # within 20% of stop-out
        elif so_call and margin_level <= so_call:
            margin_status = "warn"  # below margin-call threshold
        elif margin_level < 300.0:
            margin_status = "caution"  # less than 3x equity/margin
        else:
            margin_status = "ok"
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
        if tick is not None:
            cur_price = float(tick.bid if int(getattr(p, "type", 0)) == 1 else tick.ask)
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
                str(TRADES_LOG_PATH), since=since_dt, until=until_dt, top_n=30
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

