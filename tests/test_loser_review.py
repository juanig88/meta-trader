from __future__ import annotations

from trading.domain.loser_review import (
    ALPHA_AVOIDED,
    ALPHA_MISSING,
    ALPHA_OPPOSITE,
    ALPHA_SAME,
    PATH_AGAINST,
    PATH_NO_BARS,
    PATH_WENT_GREEN,
    SEVERITY_DISCUSS,
    SEVERITY_HINT,
    SEVERITY_READY,
    LoserRow,
    classify_alpha,
    classify_path,
    format_share_text,
    propose_improvements,
    ready_to_discuss_change,
    severity_for,
)


def _lr(**kwargs) -> LoserRow:
    base = dict(
        symbol="BTCUSD",
        side="BUY",
        net=-2.0,
        opened_at=None,
        closed_at=None,
        reason_label="Stop loss",
        alpha_code=ALPHA_AVOIDED,
        alpha_direction="NONE",
        alpha_score=0.0,
        path_code=PATH_AGAINST,
        mfe=None,
        mae=None,
        signal_skip="",
    )
    base.update(kwargs)
    return LoserRow(**base)


def test_classify_alpha_table():
    cases = [
        ("none avoids", "BUY", "NONE", ALPHA_AVOIDED),
        ("missing", "BUY", None, ALPHA_MISSING),
        ("same", "BUY", "BUY", ALPHA_SAME),
        ("opposite", "BUY", "SELL", ALPHA_OPPOSITE),
        ("empty dir", "SELL", "", ALPHA_AVOIDED),
    ]
    for name, side, direction, want in cases:
        assert classify_alpha(side, direction) == want, name


def test_classify_path_table():
    against, mfe, mae = classify_path("BUY", 100.0, [99.5, 99.0], [98.5, 98.0])
    assert against == PATH_AGAINST
    assert mfe is not None and mfe <= 0.0
    assert mae is not None and mae > 0.0

    green, mfe2, _ = classify_path("BUY", 100.0, [100.4, 99.2], [99.8, 98.9])
    assert green == PATH_WENT_GREEN
    assert mfe2 is not None and mfe2 > 0.0

    sell_green, _, _ = classify_path("SELL", 100.0, [100.2, 99.1], [99.6, 98.5])
    assert sell_green == PATH_WENT_GREEN

    empty, mfe0, mae0 = classify_path("BUY", 100.0, [], [])
    assert empty == PATH_NO_BARS
    assert mfe0 is None and mae0 is None


def test_ready_to_discuss_change():
    assert ready_to_discuss_change(0) is False
    assert ready_to_discuss_change(14) is False
    assert ready_to_discuss_change(15) is True


def test_severity_for_table():
    cases = [
        ("none", 0, 10, None),
        ("single is hint", 1, 1, SEVERITY_HINT),
        ("2 of 5 too thin", 2, 5, None),
        ("3 of 8 hint", 3, 8, SEVERITY_HINT),
        ("5 discuss", 5, 8, SEVERITY_DISCUSS),
        ("15 ready", 8, 15, SEVERITY_READY),
        ("14 of 14 still discuss", 14, 14, SEVERITY_DISCUSS),
    ]
    for name, support, total, want in cases:
        assert severity_for(support, n_total=total) == want, name


def test_propose_improvements_table():
    cases = [
        ("empty", [], [], None),
        (
            "one avoided is hint",
            [_lr()],
            ["alpha_veto"],
            SEVERITY_HINT,
        ),
        (
            "six avoided is discuss",
            [_lr() for _ in range(6)],
            ["alpha_veto"],
            SEVERITY_DISCUSS,
        ),
        (
            "fifteen avoided is ready",
            [_lr() for _ in range(15)],
            ["alpha_veto"],
            SEVERITY_READY,
        ),
        (
            "giveback cluster",
            [_lr(path_code=PATH_WENT_GREEN, alpha_code=ALPHA_SAME) for _ in range(5)],
            ["giveback"],
            SEVERITY_DISCUSS,
        ),
        (
            "symbol cluster",
            [_lr(symbol="ETHUSD", alpha_code=ALPHA_MISSING, path_code=PATH_NO_BARS) for _ in range(4)],
            ["symbol_cluster"],
            SEVERITY_HINT,
        ),
        (
            "alpha offline",
            [_lr(alpha_code=ALPHA_MISSING, path_code=PATH_NO_BARS, reason_label="Expert / bot") for _ in range(5)],
            ["alpha_offline"],
            SEVERITY_DISCUSS,
        ),
    ]
    for name, rows, must_include, sev in cases:
        got = propose_improvements(rows, n_wins=2)
        codes = [s.code for s in got]
        if not must_include:
            assert got == [], name
            continue
        for code in must_include:
            assert code in codes, f"{name}: missing {code} in {codes}"
        if sev is not None:
            match = next(s for s in got if s.code == must_include[0])
            assert match.severity == sev, f"{name}: {match.severity} != {sev}"


def test_share_text_says_not_applied():
    rows = [_lr() for _ in range(6)]
    sugg = propose_improvements(rows, n_wins=1)
    text = format_share_text(rows, sugg, n_wins=1)
    assert "no aplicada" in text.lower() or "No toqué el bot" in text
    assert "Perdedoras: 6" in text
    assert sugg
    assert sugg[0].title.split()[0] in text or sugg[0].share_line in text


def test_share_text_empty():
    text = format_share_text([], [], n_wins=0)
    assert "Todavía no hay perdedoras" in text
