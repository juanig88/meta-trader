from __future__ import annotations

import pytest

from trading.infrastructure.manual_whitelist_file import reset_manual_exception_cache


@pytest.fixture(autouse=True)
def _isolate_manual_exceptions_file(tmp_path, monkeypatch):
    """Keep live operator tickets from leaking into unit tests."""
    isolated = tmp_path / "manual_exceptions.txt"
    monkeypatch.setenv("BOT_MANUAL_EXCEPTIONS_FILE", str(isolated))
    reset_manual_exception_cache()
    yield
    reset_manual_exception_cache()
