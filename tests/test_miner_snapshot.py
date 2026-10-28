"""Dashboard view of the manual miner. No database required."""

from __future__ import annotations

from datetime import datetime, timezone


from trading.infrastructure.data_layer_config import DataLayerConfigError
from trading.infrastructure.persistence.miner_snapshot import (
    empty_miner_snapshot,
    fetch_miner_snapshot,
    journal_view_row,
    report_view_row,
)


def test_journal_view_row_table():
    when = datetime(2026, 9, 28, 15, 4, tzinfo=timezone.utc)
    cases = [
        (
            "winner with factors",
            (when, "BTCUSD!", "BUY", 0.01, 2.5, True, 15, 1.25, 1.8, False, "BUY", 0.62),
            "BTCUSD!",
            "2.50",
            "1.25",
            "no",
            "BUY",
        ),
        (
            "missing context",
            (None, "EURUSD", "SELL", None, -0.4, False, None, None, None, None, None, None),
            "EURUSD",
            "-0.40",
            "—",
            "—",
            "—",
        ),
    ]
    for name, raw, symbol, net, zscore, squeeze, alpha in cases:
        got = journal_view_row(raw)
        assert got["symbol"] == symbol, name
        assert got["net_text"] == net, name
        assert got["zscore"] == zscore, name
        assert got["squeeze"] == squeeze, name
        assert got["alpha_direction"] == alpha, name


def test_report_view_row_formats_agreement():
    row = report_view_row(
        (
            datetime(2026, 9, 28, 15, 20, tzinfo=timezone.utc),
            "every_n",
            18,
            12,
            10,
            "manual trades: 18",
            {"agreement": 0.5, "refit_applied": False},
        )
    )
    assert row["trigger"] == "every_n"
    assert row["agreement"] == "50.0%"
    assert row["refit_applied"] is False
    missing = report_view_row((None, "stat_min", 30, 30, 2, "", None))
    assert missing["agreement"] == "n/a"


def test_fetch_miner_snapshot_fail_open(monkeypatch):
    def _boom():
        raise DataLayerConfigError("MT5_PG_PASSWORD is not set")

    monkeypatch.setattr(
        "trading.infrastructure.persistence.miner_snapshot.PgSettings.from_env",
        _boom,
    )
    snap = fetch_miner_snapshot()
    assert snap["ok"] is False
    assert snap["rows"] == []
    assert "PASSWORD" in (snap["error"] or "")
    assert empty_miner_snapshot()["n_trades"] == 0


def test_fetch_miner_snapshot_reads_rows(monkeypatch):
    class _Cur:
        def __init__(self):
            self.calls = 0

        def execute(self, sql, params=None):
            self.sql = sql
            self.params = params
            self.calls += 1

        def fetchone(self):
            if "count(*)" in self.sql:
                return (2, 1)
            return (
                datetime(2026, 9, 28, 12, 20, tzinfo=timezone.utc),
                "every_n",
                2,
                1,
                2,
                "resumen",
                {"agreement": None, "refit_applied": False},
            )

        def fetchall(self):
            return [
                (
                    datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc),
                    "BTCUSD!",
                    "BUY",
                    0.1,
                    1.0,
                    True,
                    12,
                    None,
                    None,
                    None,
                    "NONE",
                    None,
                )
            ]

    class _Conn:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def cursor(self):
            return _CurCtx()

    class _CurCtx:
        def __enter__(self):
            return _Cur()

        def __exit__(self, *args):
            return False

    class _Psycopg:
        @staticmethod
        def connect(dsn, connect_timeout=2):
            assert "password" not in str(dsn).lower() or "***" in str(dsn) or "postgresql://" in str(dsn)
            return _Conn()

    class _Settings:
        port = 5433

        def conninfo(self):
            return "postgresql://metatrader:***@127.0.0.1:5433/metatrader"

    monkeypatch.setattr(
        "trading.infrastructure.persistence.miner_snapshot.PgSettings.from_env",
        lambda: _Settings(),
    )
    monkeypatch.setitem(__import__("sys").modules, "psycopg", _Psycopg)
    snap = fetch_miner_snapshot(limit=5)
    assert snap["ok"] is True
    assert snap["n_trades"] == 2
    assert snap["n_winners"] == 1
    assert snap["win_rate"] == "50.0%"
    assert snap["rows"][0]["symbol"] == "BTCUSD!"
    assert snap["rows"][0]["alpha_direction"] == "NONE"
    assert snap["report"]["summary"] == "resumen"
