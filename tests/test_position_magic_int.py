from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

_ROOT = str(Path(__file__).resolve().parents[1])
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import bot_mt5  # noqa: E402


def test_position_magic_int_keeps_zero():
    assert bot_mt5._position_magic_int(SimpleNamespace(magic=0)) == 0
    assert bot_mt5._position_magic_int(SimpleNamespace(magic=None)) == -1
    assert bot_mt5._position_magic_int(SimpleNamespace()) == -1

