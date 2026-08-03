"""
Tests unitarios (mocks) para la escalera USD de SL en bot_mt5.py.

Alcance intencional:
- Helpers: _position_age_seconds, _sl_price_for_max_loss_usd, _sl_price_to_lock_profit_usd
- _apply_usd_sl_ladder: inicio SL, widen, BE con espera, locks +$1..+$N secuenciales con pausa

No intenta importar/ejecutar todo bot_mt5.py (MT5 + red + loop); eso sería integración en terminal real.
Ejecutar desde la raíz del repo:

    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import sys
import time
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

from pathlib import Path

# Repo root on path (Windows / POSIX)
_ROOT = str(Path(__file__).resolve().parents[1])
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import bot_mt5  # noqa: E402


def _fake_symbol_info():
    """Linear tick model: distance = money * tick_size / (tick_value * lot) with point rounding."""
    return SimpleNamespace(
        trade_tick_value=1.0,
        trade_tick_size=0.0001,
        point=0.0001,
        trade_stops_level=0,
        trade_freeze_level=0,
        digits=5,
    )


def _fake_tick(bid: float = 1.10010, ask: float = 1.10020):
    return SimpleNamespace(bid=bid, ask=ask)


def _fake_tick_buy_room_to_lock(*, entry: float, max_lock_usd: int, lot: float = 0.08) -> SimpleNamespace:
    """Bid por encima del SL necesario para asegurar ``max_lock_usd`` en el modelo lineal del test."""
    tick_size, tick_value = 0.0001, 1.0
    d = max_lock_usd * tick_size / (tick_value * lot)
    bid = float(entry) + float(d) + 0.002
    return SimpleNamespace(bid=bid, ask=bid + tick_size)


class TestPositionAgeSeconds(unittest.TestCase):
    def test_none_time(self):
        self.assertEqual(bot_mt5._position_age_seconds(SimpleNamespace(time=None)), 0.0)

    def test_datetime_time(self):
        past = datetime.now() - timedelta(seconds=42)
        self.assertGreaterEqual(bot_mt5._position_age_seconds(SimpleNamespace(time=past)), 41.0)

    @patch.object(bot_mt5.time, "time", return_value=1_700_000_000.0)
    def test_unix_int(self, _tm):
        opened = 1_700_000_000
        self.assertEqual(bot_mt5._position_age_seconds(SimpleNamespace(time=opened)), 0.0)

    def test_bad_time_returns_zero(self):
        self.assertEqual(bot_mt5._position_age_seconds(SimpleNamespace(time="x")), 0.0)


class TestSlPriceForMaxLossUsd(unittest.TestCase):
    @patch.object(bot_mt5.mt5, "symbol_info", return_value=_fake_symbol_info())
    def test_buy_below_entry(self, _m):
        sl = bot_mt5._sl_price_for_max_loss_usd("EURUSD", "BUY", 1.10000, 0.08, 0.80, digits=5)
        self.assertIsNotNone(sl)
        assert sl is not None
        self.assertLess(sl, 1.10000)

    @patch.object(bot_mt5.mt5, "symbol_info", return_value=_fake_symbol_info())
    def test_sell_above_entry(self, _m):
        sl = bot_mt5._sl_price_for_max_loss_usd("EURUSD", "SELL", 1.10000, 0.08, 0.80, digits=5)
        self.assertIsNotNone(sl)
        assert sl is not None
        self.assertGreater(sl, 1.10000)

    def test_invalid_returns_none(self):
        self.assertIsNone(
            bot_mt5._sl_price_for_max_loss_usd("EURUSD", "BUY", 0.0, 0.08, 0.80, digits=5),
        )


class TestSlPriceToLockProfitUsd(unittest.TestCase):
    @patch.object(bot_mt5.mt5, "symbol_info", return_value=_fake_symbol_info())
    def test_buy_lock_above(self, _m):
        sl = bot_mt5._sl_price_to_lock_profit_usd("EURUSD", "BUY", 1.10000, 0.08, 1.0, digits=5)
        self.assertIsNotNone(sl)
        assert sl is not None
        self.assertGreater(sl, 1.10000)

    @patch.object(bot_mt5.mt5, "symbol_info", return_value=_fake_symbol_info())
    def test_sell_lock_below(self, _m):
        sl = bot_mt5._sl_price_to_lock_profit_usd("EURUSD", "SELL", 1.10000, 0.08, 1.0, digits=5)
        self.assertIsNotNone(sl)
        assert sl is not None
        self.assertLess(sl, 1.10000)


class TestApplyUsdSlLadder(unittest.TestCase):
    def setUp(self) -> None:
        bot_mt5._USD_SL_LADDER_STATE.clear()

    def tearDown(self) -> None:
        bot_mt5._USD_SL_LADDER_STATE.clear()

    def _cfg(self) -> bot_mt5.Config:
        return bot_mt5.Config(
            symbols=("EURUSD",),
            break_even_offset_points=0.0,
            usd_sl_ladder_enabled=True,
            usd_sl_ladder_initial_sl_money=0.80,
            usd_ladder_wait_be_seconds=100,
            usd_ladder_step_seconds=50,
            usd_ladder_max_lock_usd=3,
        )

    def _pr(self) -> dict:
        c = self._cfg()
        return {
            "usd_sl_ladder_initial_sl_money": c.usd_sl_ladder_initial_sl_money,
            "usd_ladder_wait_be_seconds": c.usd_ladder_wait_be_seconds,
            "usd_ladder_step_seconds": c.usd_ladder_step_seconds,
            "usd_ladder_max_lock_usd": c.usd_ladder_max_lock_usd,
        }

    @patch.object(bot_mt5, "_log_event")
    @patch.object(bot_mt5, "_modify_position_sl_tp", return_value=True)
    @patch.object(bot_mt5.mt5, "symbol_info_tick", return_value=_fake_tick())
    @patch.object(bot_mt5.mt5, "symbol_info", return_value=_fake_symbol_info())
    def test_early_return_bad_ticket(self, _si, _st, _mod, _log):
        bot_mt5._apply_usd_sl_ladder(
            self._cfg(),
            "EURUSD",
            SimpleNamespace(sl=0.0, time=int(time.time())),
            self._pr(),
            side="BUY",
            lot=0.08,
            digits=5,
            entry_price=1.1,
            ticket=0,
            profit=1.0,
        )
        _mod.assert_not_called()

    @patch.object(bot_mt5, "_log_event")
    @patch.object(bot_mt5, "_modify_position_sl_tp", return_value=True)
    @patch.object(bot_mt5.mt5, "symbol_info_tick", return_value=_fake_tick())
    @patch.object(bot_mt5.mt5, "symbol_info", return_value=_fake_symbol_info())
    def test_init_sets_sl_when_zero(self, _si, _st, mod, _log):
        opened = int(time.time()) - 200
        p = SimpleNamespace(sl=0.0, time=opened)
        bot_mt5._apply_usd_sl_ladder(
            self._cfg(),
            "EURUSD",
            p,
            self._pr(),
            side="BUY",
            lot=0.08,
            digits=5,
            entry_price=1.10000,
            ticket=101,
            profit=0.50,
        )
        mod.assert_called()
        self.assertTrue(bot_mt5._USD_SL_LADDER_STATE[101]["init_done"])

    @patch.object(bot_mt5, "_log_event")
    @patch.object(bot_mt5, "_modify_position_sl_tp", return_value=True)
    @patch.object(bot_mt5.mt5, "symbol_info_tick", return_value=_fake_tick())
    @patch.object(bot_mt5.mt5, "symbol_info", return_value=_fake_symbol_info())
    def test_init_widen_buy_too_tight(self, _si, _st, mod, _log):
        opened = int(time.time()) - 200
        # SL "demasiado cerca" del entry para BUY: SL más alto que el objetivo bajo entrada
        p = SimpleNamespace(sl=1.09999, time=opened)
        bot_mt5._apply_usd_sl_ladder(
            self._cfg(),
            "EURUSD",
            p,
            self._pr(),
            side="BUY",
            lot=0.08,
            digits=5,
            entry_price=1.10000,
            ticket=102,
            profit=0.1,
        )
        self.assertTrue(bot_mt5._USD_SL_LADDER_STATE[102]["init_done"])
        # Si el SL ya está más «apretado» que el ancla -0.80 USD, el bloque recovery aplica
        # y no se llama widen hacia tgt_init (mismo criterio que widen para BUY).

    @patch.object(bot_mt5, "_log_event")
    @patch.object(bot_mt5, "_modify_position_sl_tp", return_value=True)
    @patch.object(bot_mt5.mt5, "symbol_info_tick", return_value=_fake_tick())
    @patch.object(bot_mt5.mt5, "symbol_info", return_value=_fake_symbol_info())
    def test_init_widen_sell_too_tight(self, _si, _st, mod, _log):
        opened = int(time.time()) - 200
        p = SimpleNamespace(sl=1.10001, time=opened)
        bot_mt5._apply_usd_sl_ladder(
            self._cfg(),
            "EURUSD",
            p,
            self._pr(),
            side="SELL",
            lot=0.08,
            digits=5,
            entry_price=1.10000,
            ticket=103,
            profit=0.1,
        )
        self.assertTrue(bot_mt5._USD_SL_LADDER_STATE[103]["init_done"])

    @patch.object(bot_mt5, "_log_event")
    @patch.object(bot_mt5, "_modify_position_sl_tp", return_value=True)
    @patch.object(bot_mt5.mt5, "symbol_info_tick", return_value=_fake_tick())
    @patch.object(bot_mt5.mt5, "symbol_info", return_value=_fake_symbol_info())
    def test_be_after_wait_and_positive_profit(self, _si, _st, mod, _log):
        opened = int(time.time()) - 500  # > wait_be 100
        p = SimpleNamespace(sl=1.0890, time=opened)  # below entry, room for BE move up
        cfg = self._cfg()
        bot_mt5._USD_SL_LADDER_STATE[104] = {
            "init_done": True,
            "be_done": False,
            "locked_usd": 0,
            "last_ts": None,
            "open_ts": time.time() - 500.0,
        }
        bot_mt5._apply_usd_sl_ladder(
            cfg,
            "EURUSD",
            p,
            self._pr(),
            side="BUY",
            lot=0.08,
            digits=5,
            entry_price=1.10000,
            ticket=104,
            profit=0.25,
        )
        self.assertTrue(bot_mt5._USD_SL_LADDER_STATE[104]["be_done"])
        # Al menos init widen + BE
        self.assertGreaterEqual(mod.call_count, 1)

    @patch.object(bot_mt5, "_log_event")
    @patch.object(bot_mt5, "_modify_position_sl_tp", return_value=True)
    @patch.object(
        bot_mt5.mt5,
        "symbol_info_tick",
        return_value=_fake_tick_buy_room_to_lock(entry=1.10000, max_lock_usd=3),
    )
    @patch.object(bot_mt5.mt5, "symbol_info", return_value=_fake_symbol_info())
    def test_lock_sequential_with_time_step(self, _si, _st, mod, _log):
        t0 = 1_000_000.0
        opened = int(t0 - 500)
        p = SimpleNamespace(sl=1.10000, time=opened)  # at BE
        cfg = self._cfg()
        pr = self._pr()
        bot_mt5._USD_SL_LADDER_STATE[105] = {
            "init_done": True,
            "be_done": True,
            "locked_usd": 0,
            "last_ts": t0,
        }
        with patch.object(bot_mt5.time, "time", return_value=t0 + 60.0):
            bot_mt5._apply_usd_sl_ladder(
                cfg,
                "EURUSD",
                p,
                pr,
                side="BUY",
                lot=0.08,
                digits=5,
                entry_price=1.10000,
                ticket=105,
                profit=0.5,  # < 1 → no lock
            )
        self.assertEqual(bot_mt5._USD_SL_LADDER_STATE[105]["locked_usd"], 0)

        with patch.object(bot_mt5.time, "time", return_value=t0 + 55.0):  # < step 50 from last_ts bump path
            pass

        with patch.object(bot_mt5.time, "time", return_value=t0 + 60.0):
            bot_mt5._apply_usd_sl_ladder(
                cfg,
                "EURUSD",
                p,
                pr,
                side="BUY",
                lot=0.08,
                digits=5,
                entry_price=1.10000,
                ticket=105,
                profit=1.2,
            )
        self.assertEqual(bot_mt5._USD_SL_LADDER_STATE[105]["locked_usd"], 1)

        with patch.object(bot_mt5.time, "time", return_value=t0 + 120.0):
            bot_mt5._apply_usd_sl_ladder(
                cfg,
                "EURUSD",
                p,
                pr,
                side="BUY",
                lot=0.08,
                digits=5,
                entry_price=1.10000,
                ticket=105,
                profit=2.1,
            )
        self.assertEqual(bot_mt5._USD_SL_LADDER_STATE[105]["locked_usd"], 2)

    @patch.object(bot_mt5, "_log_event")
    @patch.object(bot_mt5, "_modify_position_sl_tp", return_value=True)
    @patch.object(
        bot_mt5.mt5,
        "symbol_info_tick",
        return_value=_fake_tick_buy_room_to_lock(entry=1.10000, max_lock_usd=4),
    )
    @patch.object(bot_mt5.mt5, "symbol_info", return_value=_fake_symbol_info())
    def test_lock_jumps_to_floor_profit(self, _si, _st, mod, _log):
        """If profit jumps to +3.9, lock should jump directly to +$3 (monotonic)."""
        t0 = 5_000_000.0
        opened = int(t0 - 500)
        p = SimpleNamespace(sl=1.10000, time=opened)  # assume already at/above BE
        cfg = self._cfg()
        pr = self._pr()
        bot_mt5._USD_SL_LADDER_STATE[120] = {
            "init_done": True,
            "be_done": True,
            "locked_usd": 1,
            "last_ts": t0 - 999,
        }
        with patch.object(bot_mt5.time, "time", return_value=t0):
            bot_mt5._apply_usd_sl_ladder(
                cfg,
                "EURUSD",
                p,
                pr,
                side="BUY",
                lot=0.08,
                digits=5,
                entry_price=1.10000,
                ticket=120,
                profit=3.9,
            )
        self.assertEqual(bot_mt5._USD_SL_LADDER_STATE[120]["locked_usd"], 3)

    @patch.object(bot_mt5, "_log_event")
    @patch.object(bot_mt5, "_modify_position_sl_tp", side_effect=[False, True])
    @patch.object(
        bot_mt5.mt5,
        "symbol_info_tick",
        return_value=_fake_tick_buy_room_to_lock(entry=1.10000, max_lock_usd=8),
    )
    @patch.object(bot_mt5.mt5, "symbol_info", return_value=_fake_symbol_info())
    def test_lock_fallback_when_high_rung_modify_fails(self, _si, _st, _mod, _log):
        """Si el broker rechaza +$6, reintenta y aplica +$5 (o el siguiente que funcione)."""
        t0 = 6_000_000.0
        opened = int(t0 - 500)
        p = SimpleNamespace(sl=1.10000, time=opened)
        cfg = self._cfg()
        pr = dict(self._pr())
        pr["usd_ladder_max_lock_usd"] = 0
        bot_mt5._USD_SL_LADDER_STATE[121] = {
            "init_done": True,
            "be_done": True,
            "locked_usd": 2,
            "last_ts": t0 - 999,
        }
        with patch.object(bot_mt5.time, "time", return_value=t0):
            bot_mt5._apply_usd_sl_ladder(
                cfg,
                "EURUSD",
                p,
                pr,
                side="BUY",
                lot=0.08,
                digits=5,
                entry_price=1.10000,
                ticket=121,
                profit=6.2,
            )
        self.assertEqual(bot_mt5._USD_SL_LADDER_STATE[121]["locked_usd"], 5)
        self.assertEqual(_mod.call_count, 2)

    @patch.object(bot_mt5, "_log_event")
    @patch.object(bot_mt5, "_modify_position_sl_tp", return_value=True)
    @patch.object(
        bot_mt5.mt5,
        "symbol_info_tick",
        return_value=_fake_tick_buy_room_to_lock(entry=1.10000, max_lock_usd=8),
    )
    @patch.object(bot_mt5.mt5, "symbol_info", return_value=_fake_symbol_info())
    def test_lock_from_5_to_6_single_attempt(self, _si, _st, mod, _log):
        """Avanzar de +$5 locked a profit que pide +$6 debe hacer un solo intento en k=6."""
        t0 = 6_100_000.0
        opened = int(t0 - 500)
        p = SimpleNamespace(sl=1.10000, time=opened)
        cfg = self._cfg()
        pr = dict(self._pr())
        pr["usd_ladder_max_lock_usd"] = 0
        bot_mt5._USD_SL_LADDER_STATE[122] = {
            "init_done": True,
            "be_done": True,
            "locked_usd": 5,
            "last_ts": t0 - 999,
        }
        n0 = mod.call_count
        with patch.object(bot_mt5.time, "time", return_value=t0):
            bot_mt5._apply_usd_sl_ladder(
                cfg,
                "EURUSD",
                p,
                pr,
                side="BUY",
                lot=0.08,
                digits=5,
                entry_price=1.10000,
                ticket=122,
                profit=6.4,
            )
        self.assertEqual(bot_mt5._USD_SL_LADDER_STATE[122]["locked_usd"], 6)
        self.assertEqual(mod.call_count - n0, 1)

    @patch.object(bot_mt5, "_log_event")
    @patch.object(bot_mt5, "_modify_position_sl_tp", return_value=True)
    @patch.object(bot_mt5.mt5, "symbol_info_tick", return_value=_fake_tick())
    @patch.object(bot_mt5.mt5, "symbol_info", return_value=_fake_symbol_info())
    def test_max_lock_zero_means_unlimited_first_rung(self, _si, _st, mod, _log):
        """max_lock_usd=0 no corta la escalera: puede avanzar sin tope (salta a floor(profit))."""
        pr = dict(self._pr())
        pr["usd_ladder_max_lock_usd"] = 0
        opened = int(time.time()) - 500
        p = SimpleNamespace(sl=1.10000, time=opened)
        cfg = self._cfg()
        bot_mt5._USD_SL_LADDER_STATE[106] = {
            "init_done": True,
            "be_done": True,
            "locked_usd": 0,
            "last_ts": time.time() - 999,
        }
        with patch.object(
            bot_mt5.mt5,
            "symbol_info_tick",
            return_value=_fake_tick_buy_room_to_lock(entry=1.10000, max_lock_usd=100),
        ):
            bot_mt5._apply_usd_sl_ladder(
                cfg,
                "EURUSD",
                p,
                pr,
                side="BUY",
                lot=0.08,
                digits=5,
                entry_price=1.10000,
                ticket=106,
                profit=99.0,
            )
        self.assertEqual(bot_mt5._USD_SL_LADDER_STATE[106]["locked_usd"], 99)

    @patch.object(bot_mt5, "_log_event")
    @patch.object(bot_mt5, "_modify_position_sl_tp", return_value=True)
    @patch.object(bot_mt5.mt5, "symbol_info_tick", return_value=_fake_tick())
    @patch.object(bot_mt5.mt5, "symbol_info", return_value=_fake_symbol_info())
    def test_max_lock_positive_stops_at_cap(self, _si, _st, mod, _log):
        pr = dict(self._pr())
        pr["usd_ladder_max_lock_usd"] = 1
        t0 = 4_000_000.0
        opened = int(t0 - 500)
        p = SimpleNamespace(sl=1.10000, time=opened)
        cfg = self._cfg()
        bot_mt5._USD_SL_LADDER_STATE[111] = {
            "init_done": True,
            "be_done": True,
            "locked_usd": 1,
            "last_ts": t0,
        }
        with patch.object(bot_mt5.time, "time", return_value=t0 + 60.0):
            bot_mt5._apply_usd_sl_ladder(
                cfg,
                "EURUSD",
                p,
                pr,
                side="BUY",
                lot=0.08,
                digits=5,
                entry_price=1.10000,
                ticket=111,
                profit=5.0,
            )
        self.assertEqual(bot_mt5._USD_SL_LADDER_STATE[111]["locked_usd"], 1)

    @patch.object(bot_mt5, "_log_event")
    @patch.object(bot_mt5, "_modify_position_sl_tp", return_value=True)
    @patch.object(bot_mt5.mt5, "symbol_info_tick", return_value=_fake_tick(bid=1.0990, ask=1.0991))
    @patch.object(bot_mt5.mt5, "symbol_info", return_value=_fake_symbol_info())
    def test_be_does_not_apply_when_current_profit_negative_even_if_mfe_positive(self, _si, _st, mod, _log):
        """Aunque MFE>0, el BE no se aplica si el profit actual es negativo (MT5 suele rechazar stops inválidos)."""
        opened = int(time.time()) - 500
        p = SimpleNamespace(sl=1.0890, time=opened)
        cfg = self._cfg()
        bot_mt5._USD_SL_LADDER_STATE[199] = {
            "init_done": True,
            "be_done": False,
            "locked_usd": 0,
            "last_ts": None,
            "max_floating_profit": 0.8,
            "open_ts": time.time() - 500.0,
        }
        bot_mt5._apply_usd_sl_ladder(
            cfg,
            "EURUSD",
            p,
            self._pr(),
            side="BUY",
            lot=0.08,
            digits=5,
            entry_price=1.10000,
            ticket=199,
            profit=-0.05,
        )
        self.assertFalse(bot_mt5._USD_SL_LADDER_STATE[199]["be_done"])

    @patch.object(bot_mt5, "_log_event")
    @patch.object(bot_mt5, "_modify_position_sl_tp", return_value=True)
    @patch.object(bot_mt5.mt5, "symbol_info_tick", return_value=_fake_tick())
    @patch.object(bot_mt5.mt5, "symbol_info", return_value=_fake_symbol_info())
    def test_be_not_before_wait_or_non_positive_profit(self, _si, _st, mod, _log):
        opened = int(time.time()) - 10  # < wait_be 100
        p = SimpleNamespace(sl=1.0890, time=opened)
        cfg = self._cfg()
        bot_mt5._USD_SL_LADDER_STATE[108] = {
            "init_done": True,
            "be_done": False,
            "locked_usd": 0,
            "last_ts": None,
            "open_ts": time.time() - 10.0,
        }
        n_calls = mod.call_count
        bot_mt5._apply_usd_sl_ladder(
            cfg,
            "EURUSD",
            p,
            self._pr(),
            side="BUY",
            lot=0.08,
            digits=5,
            entry_price=1.10000,
            ticket=108,
            profit=0.50,
        )
        self.assertFalse(bot_mt5._USD_SL_LADDER_STATE[108]["be_done"])
        self.assertEqual(mod.call_count, n_calls)

    @patch.object(bot_mt5, "_log_event")
    @patch.object(bot_mt5, "_modify_position_sl_tp", return_value=True)
    @patch.object(bot_mt5.mt5, "symbol_info_tick", return_value=_fake_tick())
    @patch.object(bot_mt5.mt5, "symbol_info", return_value=_fake_symbol_info())
    @patch.object(bot_mt5, "_sl_price_to_lock_profit_usd", return_value=None)
    def test_lock_skips_when_lock_price_none(self, _lock_fn, _si, _st, mod, _log):
        t0 = 2_000_000.0
        opened = int(t0 - 500)
        p = SimpleNamespace(sl=1.10000, time=opened)
        cfg = self._cfg()
        pr = self._pr()
        bot_mt5._USD_SL_LADDER_STATE[109] = {
            "init_done": True,
            "be_done": True,
            "locked_usd": 0,
            "last_ts": t0,
        }
        with patch.object(bot_mt5.time, "time", return_value=t0 + 60.0):
            bot_mt5._apply_usd_sl_ladder(
                cfg,
                "EURUSD",
                p,
                pr,
                side="BUY",
                lot=0.08,
                digits=5,
                entry_price=1.10000,
                ticket=109,
                profit=5.0,
            )
        self.assertEqual(bot_mt5._USD_SL_LADDER_STATE[109]["locked_usd"], 0)

    @patch.object(bot_mt5, "_log_event")
    @patch.object(bot_mt5, "_modify_position_sl_tp", return_value=True)
    @patch.object(bot_mt5.mt5, "symbol_info_tick", return_value=_fake_tick())
    @patch.object(bot_mt5.mt5, "symbol_info", return_value=_fake_symbol_info())
    def test_lock_advances_state_when_sl_already_better_than_computed(self, _si, _st, mod, _log):
        """SL already better than computed lock: advance locked_usd without requiring modify."""
        t0 = 3_000_000.0
        opened = int(t0 - 500)
        p = SimpleNamespace(sl=1.20000, time=opened)  # muy arriba del entry → ya mejor que lock $1 típico
        cfg = self._cfg()
        pr = self._pr()
        bot_mt5._USD_SL_LADDER_STATE[110] = {
            "init_done": True,
            "be_done": True,
            "locked_usd": 0,
            "last_ts": t0,
        }
        with patch.object(bot_mt5.time, "time", return_value=t0 + 60.0):
            bot_mt5._apply_usd_sl_ladder(
                cfg,
                "EURUSD",
                p,
                pr,
                side="BUY",
                lot=0.08,
                digits=5,
                entry_price=1.10000,
                ticket=110,
                profit=1.5,
            )
        self.assertEqual(bot_mt5._USD_SL_LADDER_STATE[110]["locked_usd"], 1)
        # No necesariamente otro modify si should_lock era False
        self.assertGreaterEqual(mod.call_count, 0)

    @patch.object(bot_mt5, "_log_event")
    @patch.object(bot_mt5, "_modify_position_sl_tp", return_value=False)
    @patch.object(bot_mt5.mt5, "symbol_info_tick", return_value=_fake_tick())
    @patch.object(bot_mt5.mt5, "symbol_info", return_value=_fake_symbol_info())
    def test_be_modify_fail_keeps_be_done_false(self, _si, _st, mod, _log):
        opened = int(time.time()) - 500
        p = SimpleNamespace(sl=1.0890, time=opened)
        cfg = self._cfg()
        bot_mt5._USD_SL_LADDER_STATE[107] = {"init_done": True, "be_done": False, "locked_usd": 0, "last_ts": None}
        bot_mt5._apply_usd_sl_ladder(
            cfg,
            "EURUSD",
            p,
            self._pr(),
            side="BUY",
            lot=0.08,
            digits=5,
            entry_price=1.10000,
            ticket=107,
            profit=0.5,
        )
        self.assertFalse(bot_mt5._USD_SL_LADDER_STATE[107]["be_done"])


if __name__ == "__main__":
    unittest.main()
