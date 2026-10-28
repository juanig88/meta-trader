from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from trading.domain.manual_whitelist import parse_manual_exception_tickets, parse_manual_exception_tickets_from_lines
from trading.infrastructure.manual_whitelist_file import (
    append_manual_exception_ticket,
    ensure_manual_exceptions_file,
    load_manual_exception_tickets,
    reset_manual_exception_cache,
    ticket_is_manually_excepted,
)



def test_parse_manual_exception_tickets_table():
    cases = [
        (
            "happy path two tickets",
            "123456789\n987654321\n",
            frozenset({123456789, 987654321}),
        ),
        (
            "empty file",
            "",
            frozenset(),
        ),
        (
            "whitespace only",
            "   \n\t\n  ",
            frozenset(),
        ),
        (
            "full-line comments",
            "# ignore me\n# 111\n222\n",
            frozenset({222}),
        ),
        (
            "inline comment",
            "333  # XAU manual\n",
            frozenset({333}),
        ),
        (
            "accidental spaces",
            "  444  \n",
            frozenset({444}),
        ),
        (
            "invalid tokens ignored",
            "abc\n12.5\n-9\n0\n555\n",
            frozenset({555}),
        ),
        (
            "duplicates collapse",
            "10\n10\n10\n",
            frozenset({10}),
        ),
        (
            "hash-only after strip",
            "   # comment\n",
            frozenset(),
        ),
        (
            "ticket then junk words",
            "777 BTCUSD BUY\n",
            frozenset({777}),
        ),
    ]
    for name, raw, want in cases:
        got = parse_manual_exception_tickets(raw)
        assert got == want, name


def test_parse_from_lines_matches_text():
    lines = [" # c", " 88 ", "nope", "99 # ok"]
    assert parse_manual_exception_tickets_from_lines(lines) == frozenset({88, 99})


def test_load_missing_file_is_empty(tmp_path: Path):
    reset_manual_exception_cache()
    missing = tmp_path / "nope.txt"
    assert load_manual_exception_tickets(path=missing) == frozenset()
    assert not missing.exists()


def test_ensure_creates_header_without_tickets(tmp_path: Path):
    reset_manual_exception_cache()
    target = tmp_path / "manual_exceptions.txt"
    ensure_manual_exceptions_file(path=target)
    assert target.is_file()
    text = target.read_text(encoding="utf-8")
    assert text.startswith("#")
    assert load_manual_exception_tickets(path=target) == frozenset()


def test_load_reads_tickets_and_cache_until_mtime_changes(tmp_path: Path):
    reset_manual_exception_cache()
    target = tmp_path / "manual_exceptions.txt"
    target.write_text("11\n22\n", encoding="utf-8")
    first = load_manual_exception_tickets(path=target)
    assert first == frozenset({11, 22})
    target.write_text("11\n22\n33\n", encoding="utf-8")
    # Force a distinct mtime on coarse filesystems.
    st = target.stat()
    os_utime = __import__("os").utime
    os_utime(target, ns=(st.st_atime_ns + 1_000_000, st.st_mtime_ns + 1_000_000))
    second = load_manual_exception_tickets(path=target)
    assert second == frozenset({11, 22, 33})


def test_append_manual_exception_ticket(tmp_path: Path):
    reset_manual_exception_cache()
    target = tmp_path / "manual_exceptions.txt"
    cases = [
        ("missing file creates header and ticket", None, 1940642770, "added", frozenset({1940642770})),
        ("duplicate", "1940642770\n", 1940642770, "duplicate", frozenset({1940642770})),
        ("keeps existing lines", "# note\n11\n", 22, "added", frozenset({11, 22})),
        ("zero", "11\n", 0, "invalid", frozenset({11})),
        ("negative", "11\n", -5, "invalid", frozenset({11})),
    ]
    for name, seed, ticket, want_status, want_tickets in cases:
        reset_manual_exception_cache()
        if seed is None:
            if target.exists():
                target.unlink()
        else:
            target.write_text(seed, encoding="utf-8")
        status = append_manual_exception_ticket(ticket, path=target)
        assert status == want_status, name
        assert load_manual_exception_tickets(path=target) == want_tickets, name


def test_append_preserves_file_when_read_fails(tmp_path: Path):
    reset_manual_exception_cache()
    target = tmp_path / "manual_exceptions.txt"
    target.write_text("1001\n", encoding="utf-8")
    real_read = Path.read_text

    def boom(self, *args, **kwargs):
        if Path(self) == target:
            raise PermissionError("locked")
        return real_read(self, *args, **kwargs)

    with patch.object(Path, "read_text", boom):
        with patch("trading.infrastructure.manual_whitelist_file._RETRY_SLEEP_S", 0):
            status = append_manual_exception_ticket(2002, path=target)
    assert status == "error"
    assert target.read_text(encoding="utf-8") == "1001\n"


def test_ticket_is_manually_excepted(tmp_path: Path):
    reset_manual_exception_cache()
    target = tmp_path / "manual_exceptions.txt"
    target.write_text("42\n", encoding="utf-8")
    assert ticket_is_manually_excepted(42, path=target) is True
    assert ticket_is_manually_excepted(43, path=target) is False
    assert ticket_is_manually_excepted(0, path=target) is False
    assert ticket_is_manually_excepted(-1, path=target) is False


def test_load_keeps_last_snapshot_when_read_fails(tmp_path: Path):
    reset_manual_exception_cache()
    target = tmp_path / "manual_exceptions.txt"
    target.write_text("1001\n", encoding="utf-8")
    assert load_manual_exception_tickets(path=target) == frozenset({1001})

    real_read = Path.read_text

    def boom(self, *args, **kwargs):
        if Path(self) == target:
            raise PermissionError("locked")
        return real_read(self, *args, **kwargs)

    with patch.object(Path, "read_text", boom):
        with patch(
            "trading.infrastructure.manual_whitelist_file._RETRY_SLEEP_S",
            0,
        ):
            # bump mtime so cache is not reused before the read attempt
            st = target.stat()
            __import__("os").utime(target, ns=(st.st_atime_ns + 2_000_000, st.st_mtime_ns + 2_000_000))
            got = load_manual_exception_tickets(path=target)
    assert got == frozenset({1001})
