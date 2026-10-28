"""Rotation audit tags for trades.log."""

from __future__ import annotations

from trading.application.rotation_audit import classify_rotation_close, ensemble_vote_payload, open_audit_from_snap


def test_classify_rotation_close_table():
    cases = [
        ("loss comment", dict(comment="rot_stale_loss", reason_code=4), "stale_loss"),
        ("old scratch comment", dict(comment="rot_stale_scratch", reason_code=4), "stale_loss"),
        ("take comment", dict(comment="rot_stale_take", reason_code=4), "stale_take"),
        ("book harvest", dict(comment="rot_book_harvest", reason_code=3), "book_harvest"),
        ("broker SL", dict(comment="", reason_code=4), "stop_loss"),
        ("broker TP", dict(comment="", reason_code=5), "take_profit"),
        ("expert", dict(comment="", reason_code=3), "expert_or_bot"),
        ("empty", dict(comment="", reason_code=None), "unknown"),
    ]
    for name, kwargs, want in cases:
        assert classify_rotation_close(**kwargs) == want, name


def test_ensemble_vote_payload_open_and_skip():
    row = {
        "symbol": "ADAUSD!",
        "action": "LIVE",
        "skip_reason": "",
        "consensus": {"direction": "BUY", "buy": 4, "sell": 0, "none": 1, "fired": True, "agree": 4},
        "size": {"risk_usd": 0.30, "lot": 0.01},
        "votes": [{"name": "htf", "side": "BUY"}, {"name": "vol_bands", "side": "NONE"}],
    }
    got = ensemble_vote_payload(row, ts="2026-09-29T16:00:00", execution_mode="DEMO_XBTFX")
    assert got["event"] == "ENSEMBLE_VOTE"
    assert got["why_open"] == "consenso_4_5"
    assert "htf:BUY" in got["votes"]
    opened = open_audit_from_snap(row)
    assert opened["why_open"] == "consenso_4_5"
    skip = dict(row)
    skip["action"] = "SKIP"
    skip["skip_reason"] = "sin_consenso"
    skip["consensus"] = {"direction": "NONE", "buy": 3, "sell": 2, "none": 7, "fired": False}
    got_skip = ensemble_vote_payload(skip, ts="t")
    assert got_skip["why_open"] == "sin_consenso"
    assert "murphy" not in got_skip
    row["murphy"] = {"tag_m15": "pullback_38_50", "would_keep": True, "would_veto": False}
    with_murphy = ensemble_vote_payload(row, ts="t")
    assert with_murphy["murphy"]["tag_m15"] == "pullback_38_50"
    opened_m = open_audit_from_snap(row)
    assert opened_m["murphy"]["would_keep"] is True
    row["alpha"] = {"code": "alpha_igual", "direction": "BUY", "would_avoid": False}
    with_alpha = ensemble_vote_payload(row, ts="t")
    assert with_alpha["alpha"]["code"] == "alpha_igual"
