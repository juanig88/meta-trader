"""Distance below the all-time high (telemetry for the swing and Impulso logs)."""

from __future__ import annotations

import pytest

from trading.domain.ath import drawdown_from_ath


@pytest.mark.parametrize("name,highs,price,expected", [
    ("half of the ATH", [100.0, 200.0, 150.0], 100.0, 0.5),
    ("at the ATH", [100.0, 200.0], 200.0, 0.0),
    ("new ATH clips to zero", [100.0, 200.0], 250.0, 0.0),
    ("BTC 2026-11-03", [126199.63], 77394.01, 0.3867),
    ("no history", [], 100.0, None),
    ("bad price", [100.0], 0.0, None),
])
def test_drawdown_from_ath(name, highs, price, expected):
    assert drawdown_from_ath(highs, price) == expected, name
