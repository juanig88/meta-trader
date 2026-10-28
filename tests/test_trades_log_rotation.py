import logging
import os

import pytest

import bot_mt5


@pytest.fixture
def in_tmp(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    root = logging.getLogger()
    saved = root.handlers[:]
    root.handlers = []
    yield tmp_path
    for h in root.handlers:
        h.close()
    root.handlers = saved


def _write_log(text: str = "old session\n") -> None:
    with open(bot_mt5._TRADES_LOG_FILENAME, "w", encoding="utf-8") as f:
        f.write(text)


@pytest.mark.parametrize(
    "content, expect_archive",
    [
        (None, False),
        ("", False),
        ("old session\n", True),
    ],
)
def test_rotation_cases(in_tmp, content, expect_archive):
    if content is not None:
        _write_log(content)

    assert bot_mt5._rotate_trades_log_on_start(retry_sleep_s=0) is None

    archived = list((in_tmp / "logs").glob("trades_*.log")) if (in_tmp / "logs").exists() else []
    assert bool(archived) is expect_archive
    if expect_archive:
        assert archived[0].read_text(encoding="utf-8") == content
        assert os.path.getsize(bot_mt5._TRADES_LOG_FILENAME) == 0


def test_rotation_retries_while_old_process_holds_file(in_tmp, monkeypatch):
    _write_log()
    real_replace = os.replace
    calls = {"n": 0}

    def flaky_replace(src, dst):
        calls["n"] += 1
        if calls["n"] < 3:
            raise PermissionError("in use by another process")
        real_replace(src, dst)

    monkeypatch.setattr(bot_mt5.os, "replace", flaky_replace)

    assert bot_mt5._rotate_trades_log_on_start(retry_sleep_s=0) is None
    assert calls["n"] == 3
    assert len(list((in_tmp / "logs").glob("trades_*.log"))) == 1


def test_failed_rotation_still_attaches_trades_log_handler(in_tmp, monkeypatch):
    root = logging.getLogger()
    root.handlers = [h for h in root.handlers if type(h).__name__ != "LogCaptureHandler"]
    _write_log()

    def locked(src, dst):
        raise PermissionError("in use by another process")

    monkeypatch.setattr(bot_mt5.os, "replace", locked)

    err = bot_mt5._rotate_trades_log_on_start(attempts=2, retry_sleep_s=0)
    assert err and "PermissionError" in err
    assert root.handlers == []

    bot_mt5._setup_logging()
    file_handlers = [h for h in root.handlers if isinstance(h, logging.FileHandler)]
    assert len(file_handlers) == 1
    assert os.path.basename(file_handlers[0].baseFilename) == bot_mt5._TRADES_LOG_FILENAME
