from __future__ import annotations

import ast
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from trading.application.manual_pattern_miner import journal_deals, load_dashboard
from trading.domain.manual_journal import (
    context_from_bars,
    count_open_manual,
    dashboard_slice,
    format_pattern_report,
    is_manual_magic,
    journal_record,
    manual_round_trips,
    pattern_report,
)
from trading.domain.market_data import Bar
from trading.infrastructure.data_layer_config import ManualMinerSettings, parse_correlation_groups
from trading.infrastructure.persistence.pg import _manual_journal_row


def _deal(**kwargs):
    base = {
        "time": "2026-09-26T14:00:00+00:00",
        "deal": 1,
        "position_id": 10,
        "symbol": "EURUSD",
        "magic": 0,
        "entry": 0,
        "type": 0,
        "volume": 0.1,
        "price": 1.1,
        "profit": 0.0,
        "swap": 0.0,
        "commission": -0.2,
        "comment": "",
    }
    base.update(kwargs)
    return base


def _bars(n: int, start: datetime, *, step: float = 0.001, future: int = 0) -> list[Bar]:
    out = []
    price = 1.10
    for i in range(n + future):
        o = price
        c = price + step
        out.append(
            Bar(
                symbol="EURUSD",
                timeframe="M15",
                ts=start + timedelta(minutes=15 * i),
                open=o,
                high=max(o, c) + 0.0004,
                low=min(o, c) - 0.0002,
                close=c,
                tick_volume=100 + i * 3,
            )
        )
        price = c
    return out


def test_manual_round_trips_table():
    opened = "2026-09-26T14:00:00+00:00"
    closed = "2026-09-26T15:00:00+00:00"
    deals = [
        _deal(time=opened, deal=1, position_id=10, entry=0, type=0, profit=0.0),
        _deal(time=closed, deal=2, position_id=10, entry=1, type=1, profit=12.0, price=1.12),
        _deal(time=opened, deal=3, position_id=11, entry=0, type=0, magic=260414, profit=0.0),
        _deal(time=closed, deal=4, position_id=11, entry=1, type=1, magic=260414, profit=9.0),
        _deal(time=opened, deal=5, position_id=12, entry=0, type=1, profit=0.0),
        _deal(time=closed, deal=6, position_id=12, entry=1, type=0, profit=-4.0, price=1.08),
        _deal(time=opened, deal=7, position_id=13, entry=0, type=0, profit=0.0),
        _deal(type=2, position_id=99, profit=100.0, entry=0),
        _deal(time=opened, deal=8, position_id=14, entry=0, type=0, magic=0),
    ]
    trips = manual_round_trips(deals, bot_magics=(260414, 260415))
    by_id = {t.position_id: t for t in trips}
    assert set(by_id) == {10, 12}
    assert by_id[10].side == "BUY"
    assert by_id[10].winner is True
    assert by_id[10].net_profit == pytest.approx(11.6)
    assert by_id[12].side == "SELL"
    assert by_id[12].winner is False
    assert is_manual_magic(0, (260414,)) is True
    assert is_manual_magic(260414, (260414,)) is False
    assert is_manual_magic(7, (260414,)) is False


def test_context_ignores_bars_after_open_and_empty_open():
    opened = datetime(2026, 9, 26, 14, 0, tzinfo=timezone.utc)
    start = opened - timedelta(minutes=15 * 80)
    bars = _bars(80, start, future=10)
    ctx = context_from_bars(
        bars,
        opened_at=opened,
        symbol="EURUSD",
        groups={"USD_BASE": ["EURUSD", "USDJPY"]},
        atr_value=0.001,
        alpha_direction="BUY",
        alpha_score=0.4,
    )
    leaked = context_from_bars(
        [b for b in bars if b.ts <= opened],
        opened_at=opened,
        symbol="EURUSD",
        groups={"USD_BASE": ["EURUSD"]},
        atr_value=0.001,
        alpha_score=0.4,
    )
    assert ctx.zscore == leaked.zscore
    assert ctx.correlation_groups == "USD_BASE"
    assert ctx.hour_of_day == 14
    assert ctx.q == pytest.approx(0.5 * (1.0 + __import__("math").tanh(0.4)))
    assert ctx.rvol is not None
    empty = context_from_bars([], opened_at=None, symbol="EURUSD")
    assert empty.zscore is None
    assert empty.hour_of_day is None
    record = journal_record(
        manual_round_trips(
            [
                _deal(),
                _deal(entry=1, type=1, deal=2, profit=3.0, time="2026-09-26T15:00:00+00:00"),
            ],
            bot_magics=(260414,),
        )[0],
        ctx,
    )
    assert record["winner"] is True
    assert record["position_id"] == 10
    row = _manual_journal_row({**record, "hour_of_day": 0, "squeeze": False})
    assert row[13] == 0
    assert row[20] is False


def test_dashboard_slice_lag_and_secret_keys():
    closed = datetime(2026, 9, 26, 15, 0, tzinfo=timezone.utc)
    near = dashboard_slice(
        {
            "running": True,
            "mode": "CONNECTED",
            "ts": "2026-09-26T15:02:00",
            "symbols": ["EURUSD"],
            "password": "nope",
            "market_regimes": {"EURUSD": {"bias": "UP", "token": "hide-me", "password": "x"}},
        },
        "EURUSD",
        observed_at=closed + timedelta(minutes=2),
        closed_at=closed,
        max_lag_seconds=900,
    )
    assert near is not None
    assert "password" not in near
    assert near["symbol_regime"]["bias"] == "UP"
    assert "password" not in near["symbol_regime"]
    assert "token" not in near["symbol_regime"]
    stale = dashboard_slice(
        {"running": True, "mode": "CONNECTED"},
        "EURUSD",
        observed_at=closed + timedelta(hours=5),
        closed_at=closed,
    )
    assert stale is None
    assert dashboard_slice(None, "EURUSD", observed_at=closed, closed_at=closed) is None


def test_pattern_report_requires_min_n_and_states_it_is_not_an_edge():
    rows = []
    for i in range(4):
        rows.append(
            {
                "symbol": "EURUSD",
                "side": "BUY",
                "winner": True,
                "net_profit": 5.0,
                "zscore": 2.0,
                "rvol": 1.8,
                "squeeze": False,
                "hour_of_day": 14,
            }
        )
    rows.append(
        {
            "symbol": "USDJPY",
            "side": "SELL",
            "winner": False,
            "net_profit": -2.0,
            "zscore": 0.1,
            "rvol": 0.4,
            "squeeze": True,
            "hour_of_day": 3,
        }
    )
    report = pattern_report(rows, min_n=3)
    assert report.n == 5
    assert report.n_winners == 4
    assert len(report.patterns) == 1
    assert report.patterns[0].n_winners == 4
    assert report.patterns[0].lift > 1.0
    assert "not a validated edge" in report.note
    thin = pattern_report(rows[:2], min_n=3)
    assert thin.patterns == []
    text = format_pattern_report(thin)
    assert "none" in text
    assert count_open_manual([{"symbol": "EURUSD", "magic": 0}, {"symbol": "XAUUSD", "magic": 260415}]) == 1


def test_parse_groups_and_dashboard_path():
    groups = parse_correlation_groups('{"CRYPTO":["btcusd","ETHUSD"],"bad":"nope"}')
    assert groups == {"CRYPTO": ("BTCUSD", "ETHUSD")}
    assert parse_correlation_groups("not-json") == {}
    settings = ManualMinerSettings.from_env(
        {
            "BOT_MAGIC": "260414",
            "BOT_CORRELATION_GROUPS": '{"FX":["EURUSD"]}',
            "MT5_MANUAL_MINER_DASHBOARD_PATH": "notes.txt",
            "MT5_MANUAL_MINER_MIN_N": "1",
        }
    )
    assert settings.bot_magics == (260414, 260415)
    assert settings.correlation_groups["FX"] == ("EURUSD",)
    assert settings.dashboard_path == "bot_state.json"
    assert settings.pattern_min_n == 2
    assert settings.report_every_n == 10
    assert settings.stat_min_winners == 30
    assert settings.interval_seconds == 300.0


def test_load_dashboard_rejects_non_json(tmp_path: Path):
    secret = tmp_path / ".env"
    secret.write_text("MT5_PASSWORD=nope", encoding="utf-8")
    assert load_dashboard(str(secret)) == (None, None)
    state = tmp_path / "bot_state.json"
    state.write_text('{"running": true, "mode": "CONNECTED"}', encoding="utf-8")
    payload, observed = load_dashboard(str(state))
    assert payload is not None and payload["running"] is True
    assert observed is not None


class _Store:
    def __init__(self, bars):
        self.bars = bars
        self.written: list[dict] = []

    async def fetch_bars_asof(self, symbol, timeframe, asof, limit):
        return [b for b in self.bars if b.ts <= asof][-int(limit) :]

    async def fetch_alpha_asof(self, symbol, asof):
        return {"direction": "BUY", "score": 0.2, "q": 0.6}

    async def upsert_manual_journal(self, rows):
        self.written.extend(rows)
        return len(rows)


@pytest.mark.asyncio
async def test_journal_deals_skips_bot_magic_and_keeps_open_context():
    opened = datetime(2026, 9, 26, 14, 0, tzinfo=timezone.utc)
    bars = _bars(90, opened - timedelta(hours=20), future=5)
    store = _Store(bars)
    deals = [
        _deal(time=opened.isoformat(), position_id=10),
        _deal(
            time=(opened + timedelta(hours=1)).isoformat(),
            position_id=10,
            entry=1,
            type=1,
            deal=2,
            profit=8.0,
        ),
        _deal(time=opened.isoformat(), position_id=11, magic=260414),
        _deal(
            time=(opened + timedelta(hours=1)).isoformat(),
            position_id=11,
            magic=260414,
            entry=1,
            type=1,
            deal=4,
            profit=8.0,
        ),
    ]
    settings = ManualMinerSettings(bot_magics=(260414,), bars=80, zscore_lookback=20)
    records = await journal_deals(
        store,  # type: ignore[arg-type]
        deals,
        settings=settings,
        dashboard_state={"running": True, "mode": "CONNECTED", "symbols": ["EURUSD"]},
        dashboard_observed_at=opened + timedelta(hours=1, minutes=1),
    )
    assert len(records) == 1
    assert records[0]["winner"] is True
    assert records[0]["zscore"] is not None
    assert records[0]["dashboard"]["mode"] == "CONNECTED"
    assert store.written[0]["position_id"] == 10


def test_miner_has_no_order_path():
    paths = [
        Path("trading/application/manual_pattern_miner.py"),
        Path("trading/infrastructure/mt5_deals.py"),
        Path("trading/domain/manual_journal.py"),
        Path("trading/domain/quant_factors.py"),
        Path("manual_miner_daemon.py"),
    ]
    forbidden_calls = {"order_send", "order_check", "open_trade"}
    forbidden_modules = {"bot_mt5", "watcher_ladder"}
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                assert node.func.id not in forbidden_calls
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                assert node.func.attr not in forbidden_calls
        assert imported.isdisjoint(forbidden_modules)


def test_compare_winners_to_alpha_table():
    from trading.domain.manual_journal import compare_winners_to_alpha

    cases = [
        (
            "agree and oppose",
            [
                {"winner": True, "side": "BUY", "alpha_direction": "BUY", "zscore": 2.0, "rvol": 1.5, "squeeze": True},
                {"winner": True, "side": "SELL", "alpha_direction": "BUY", "zscore": -1.0, "rvol": 0.5, "squeeze": False},
                {"winner": True, "side": "BUY", "alpha_direction": "NONE", "zscore": 0.0, "rvol": 1.0, "squeeze": False},
                {"winner": False, "side": "SELL", "alpha_direction": "SELL", "zscore": 0.2, "rvol": 0.2, "squeeze": False},
            ],
                3,
                3,
                1,
                1,
            0.5,
            False,
        ),
        (
            "no alpha yet",
            [{"winner": True, "side": "BUY", "zscore": None, "rvol": None}],
            1,
            0,
            0,
            0,
            None,
            False,
        ),
        ("empty", [], 0, 0, 0, 0, None, False),
    ]
    for name, rows, n_winners, n_with, n_agree, n_skipped, agreement, refit in cases:
        got = compare_winners_to_alpha(rows)
        assert got.n_winners == n_winners, name
        assert got.n_with_alpha == n_with, name
        assert got.n_agree == n_agree, name
        assert got.n_alpha_skipped == n_skipped, name
        assert got.agreement == agreement, name
        assert got.refit_allowed is refit, name
        assert got.refit_applied is False, name


def test_should_emit_report_table():
    from trading.domain.manual_journal import ReportCursor, should_emit_report

    cases = [
        ("empty", ReportCursor(), 0, 0, 10, 30, ""),
        ("first batch", ReportCursor(), 10, 4, 10, 30, "every_n"),
        ("short of batch", ReportCursor(n_trades=10), 19, 6, 10, 30, ""),
        ("next batch", ReportCursor(n_trades=10), 20, 8, 10, 30, "every_n"),
        ("stat only", ReportCursor(n_trades=28), 30, 30, 10, 30, "stat_min"),
        ("stat and batch", ReportCursor(n_trades=20), 30, 30, 10, 30, "stat_min+every_n"),
        ("stat already sent", ReportCursor(n_trades=30, stat_emitted=True), 35, 32, 10, 30, ""),
        ("stat already sent, next batch", ReportCursor(n_trades=30, stat_emitted=True), 40, 33, 10, 30, "every_n"),
        ("shrink does not emit", ReportCursor(n_trades=40), 10, 4, 10, 30, ""),
    ]
    for name, cursor, n_trades, n_winners, every, stat, want in cases:
        got = should_emit_report(cursor, n_trades, n_winners, every_n=every, stat_min=stat)
        assert got == want, name


@pytest.mark.asyncio
async def test_maybe_publish_report_respects_threshold():
    from trading.application.manual_pattern_miner import maybe_publish_report
    from trading.domain.manual_journal import ReportCursor

    class _Store:
        def __init__(self):
            self.saved = []

        async def count_manual_journal(self):
            return 10, 4

        async def list_manual_journal(self, limit):
            return [
                {"winner": True, "side": "BUY", "alpha_direction": "BUY", "zscore": 1.0, "rvol": 1.2, "net_profit": 3.0},
            ]

        async def insert_miner_report(self, **kwargs):
            self.saved.append(kwargs)

    store = _Store()
    settings = ManualMinerSettings(report_every_n=10, stat_min_winners=30, pattern_min_n=3)
    quiet = await maybe_publish_report(store, settings, ReportCursor(n_trades=10))
    assert quiet.n_trades == 10
    assert store.saved == []
    published = await maybe_publish_report(store, settings, ReportCursor())
    assert published.n_trades == 10
    assert published.stat_emitted is False
    assert store.saved[0]["trigger"] == "every_n"
    assert store.saved[0]["comparison"]["refit_applied"] is False
    assert "password" not in store.saved[0]["summary"].lower()

