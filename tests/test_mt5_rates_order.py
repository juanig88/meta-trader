"""copy_rates_from_pos order is broker-dependent; always pick last-closed by time."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_ROOT = str(Path(__file__).resolve().parents[1])
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from bot_mt5 import mt5_rates_oldest_to_newest  # noqa: E402


def _bar(ts: int, o: float, h: float, l: float, c: float) -> dict:
    return {"time": ts, "open": o, "high": h, "low": l, "close": c}


class TestMt5RatesOldestToNewest(unittest.TestCase):
    def test_table(self) -> None:
        oldest = _bar(1_000, 10, 12, 9, 11)
        mid = _bar(1_900, 11, 13, 10, 12)
        newest = _bar(2_800, 12, 20, 12, 19)
        cases = [
            ("chronological", [oldest, mid, newest], [1_000, 1_900, 2_800]),
            ("newest_first", [newest, mid, oldest], [1_000, 1_900, 2_800]),
            ("shuffled", [mid, newest, oldest], [1_000, 1_900, 2_800]),
            ("single", [newest], [2_800]),
            ("empty", [], []),
            ("none", None, []),
        ]
        for name, raw, want_times in cases:
            with self.subTest(name=name):
                got = mt5_rates_oldest_to_newest(raw)
                self.assertEqual([int(r["time"]) for r in got], want_times)

    def test_last_two_match_newest_closed_pair(self) -> None:
        """get_signal_m15 must compare the latest closed bar vs the one before it."""
        older = _bar(1_000, 4368, 4374, 4366, 4369)
        last = _bar(1_900, 4369, 4421, 4369, 4418)  # Aug 19 12:30-style BUY
        newest_first = [last, older]
        ordered = mt5_rates_oldest_to_newest(newest_first)
        prev, last_bar = ordered[-2], ordered[-1]
        self.assertEqual(int(last_bar["time"]), 1_900)
        self.assertEqual(int(prev["time"]), 1_000)
        self.assertGreater(float(last_bar["close"]), float(prev["high"]))
        chrono = mt5_rates_oldest_to_newest([older, last])
        self.assertEqual(int(chrono[-1]["time"]), int(last_bar["time"]))
        self.assertEqual(float(chrono[-1]["close"]), float(last_bar["close"]))


if __name__ == "__main__":
    unittest.main()
