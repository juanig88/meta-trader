import json
from datetime import datetime, timezone

import pytest

import swing_daily


@pytest.mark.parametrize(
    "now, last_run_day, expected",
    [
        (datetime(2026, 10, 7, 18, 0, tzinfo=timezone.utc), "2026-10-07", "proxima 2026-10-08 00:10 UTC"),
        (datetime(2026, 10, 8, 0, 5, tzinfo=timezone.utc), "2026-10-07", "proxima 2026-10-08 00:10 UTC"),
        (datetime(2026, 10, 8, 3, 0, tzinfo=timezone.utc), "2026-10-07", "proxima en el proximo minuto"),
        (datetime(2026, 10, 8, 3, 0, tzinfo=timezone.utc), None, "ultima decision -"),
    ],
)
def test_heartbeat_reports_next_decision(tmp_path, monkeypatch, now, last_run_day, expected):
    state = tmp_path / "swing_state.json"
    if last_run_day:
        state.write_text(json.dumps({"last_run_day": last_run_day,
                                     "last_summary": {"opened": 2, "closed": 1}}), encoding="utf-8")
    line = swing_daily._heartbeat(now, 10, state)
    assert line.startswith("swing vivo")
    assert expected in line


class _Mt5:
    def __init__(self, server, names):
        self._server, self._names = server, names

    def account_info(self):
        from types import SimpleNamespace as NS
        return NS(server=self._server, company="")

    def symbols_get(self):
        from types import SimpleNamespace as NS
        return [NS(name=n) for n in self._names]


def test_exness_reads_btc_eth_with_its_own_files(monkeypatch):
    for name in ("BOT_SWING_STATE", "BOT_SWING_HALT", "BOT_SWING_LOG"):
        monkeypatch.delenv(name, raising=False)
    mt5 = _Mt5("Exness-MT5Trial11", ["BTCUSDm", "BTCUSDTm", "ETHUSDm", "XAUUSDm"])
    assert swing_daily._is_exness(mt5) is True
    assert swing_daily._default_symbols(mt5, True) == ["BTCUSDm", "ETHUSDm"]
    state, halt, log = swing_daily._paths(True)
    assert (state.name, halt.name, log.name) == (
        "swing_exness_state.json", "swing_exness_kill_switch.halt", "swing_exness_trades.log")
    assert swing_daily._paths(False)[0].name == "swing_state.json"
    assert swing_daily._is_exness(_Mt5("XBTFX-MetaTrader5", [])) is False


def test_exness_forces_dry_run_even_with_live_env(monkeypatch, tmp_path):
    """The .env of the XBTFX demo has DRY_RUN=0 and DEMO_LIVE=1; on Exness no order may leave."""
    from datetime import date
    monkeypatch.setenv("BOT_SWING_DRY_RUN", "0")
    monkeypatch.setenv("BOT_SWING_DEMO_LIVE", "1")
    monkeypatch.setenv("BOT_SWING_STATE", str(tmp_path / "s.json"))
    monkeypatch.setenv("BOT_SWING_HALT", str(tmp_path / "h.halt"))
    monkeypatch.setenv("BOT_SWING_LOG", str(tmp_path / "l.log"))
    seen = {}

    class Broker:
        def __init__(self, mt5, symbols):
            seen["symbols"] = symbols

        def account(self):
            from trading.application.swing_engine import AccountView
            return AccountView(login=1, server="Exness-MT5Trial11", trade_mode=0, equity=200.0, is_xbtfx=False)

        def magic_active(self, magic, hours):
            return False

    def fake_run(broker, **kw):
        seen["send"] = kw["send_orders"]
        return {"ran": False}

    monkeypatch.setattr(swing_daily, "Mt5SwingBroker", Broker)
    monkeypatch.setattr(swing_daily, "run_swing_day", fake_run)
    swing_daily.run_once(_Mt5("Exness-MT5Trial11", ["BTCUSDm", "ETHUSDm"]), force_dry=False)
    assert seen == {"symbols": ["BTCUSDm", "ETHUSDm"], "send": False}
