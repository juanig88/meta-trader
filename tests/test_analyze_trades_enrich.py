"""Tests for offline TRADE_CLOSE enrichment from TRADE_OPEN by position_id."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_ROOT = str(Path(__file__).resolve().parents[1])
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import analyze_trades  # noqa: E402


class TestEnrichClosesFromOpens(unittest.TestCase):
    def test_fills_null_close_from_open(self) -> None:
        events = [
            {
                "event": "TRADE_OPEN",
                "symbol": "EURUSD",
                "position_id": 9001,
                "price": 1.1,
                "price_open": 1.1,
                "opened_at": "2026-05-01T10:00:00",
                "ts": "2026-05-01T10:00:00",
                "lot": 0.08,
            },
            {
                "event": "TRADE_CLOSE",
                "symbol": "EURUSD",
                "side": "BUY",
                "position_id": 9001,
                "price_open": None,
                "opened_at": None,
                "price_close": 1.1005,
                "closed_at": "2026-05-01T11:00:00",
                "profit": 1.0,
                "swap": 0,
                "commission": 0,
                "net": 1.0,
                "volume": 0.08,
            },
        ]
        analyze_trades.enrich_trade_closes_from_logged_opens(events)
        close = events[1]
        self.assertEqual(close.get("price_open"), 1.1)
        self.assertEqual(close.get("opened_at"), "2026-05-01T10:00:00")
        self.assertEqual(close.get("duration_seconds"), 3600)
        self.assertEqual(close.get("volume_in"), 0.08)

    def test_first_open_wins_same_pid(self) -> None:
        events = [
            {
                "event": "TRADE_OPEN",
                "position_id": 2,
                "price": 1.0,
                "ts": "2026-05-01T09:00:00",
            },
            {
                "event": "TRADE_OPEN",
                "position_id": 2,
                "price": 9.99,
                "ts": "2026-05-01T12:00:00",
            },
            {
                "event": "TRADE_CLOSE",
                "position_id": 2,
                "price_open": None,
                "opened_at": None,
                "closed_at": "2026-05-01T13:00:00",
            },
        ]
        analyze_trades.enrich_trade_closes_from_logged_opens(events)
        self.assertEqual(events[2].get("price_open"), 1.0)


if __name__ == "__main__":
    unittest.main()
