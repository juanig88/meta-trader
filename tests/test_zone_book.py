"""Zone book: replay of touches/bounces/breaks, role flip, expiry, weakness, merge and sync."""

from __future__ import annotations

from datetime import datetime, timedelta

from trading.domain.rebound_zones import CEILING, FLOOR, Zone
from trading.domain.zone_book import (
    ACTIVE, EXPIRED, FLIPPED, WEAK, H1, ZoneRecord, blocking_zones, change_messages, merge_candidates, prune,
    replay, sync_manual,
)

T0 = datetime(2026, 10, 1)


def path(*closes: float, wick: float = 50.0) -> list[H1]:
    return [H1(T0 + timedelta(hours=i), c + wick, c - wick, c) for i, c in enumerate(closes)]


def rec(low=76000.0, high=76500.0, origin="manual") -> ZoneRecord:
    return ZoneRecord(f"{origin}:x", low, high, origin, T0.isoformat())


def test_bounces_on_a_floor():
    r = replay(rec(), path(77000, 76400, 76800, 77200, 76300, 76900), now=T0 + timedelta(hours=6))
    assert (r.touches, r.bounces, r.breaks) == (2, 2, 0)
    assert r.role == FLOOR and r.status == ACTIVE and r.bounce_rate == 1.0


def test_break_flips_floor_into_ceiling_and_measures_the_drop():
    r = replay(rec(), path(77000, 76400, 75800, 75200, 75600), now=T0 + timedelta(hours=5))
    assert r.breaks == 1 and r.role == CEILING and r.status == FLIPPED
    assert r.last_break_dir == "abajo"
    assert r.move_after_break_pct == round((75150 / 76000 - 1) * 100, 2)


def test_sitting_on_the_floor_is_one_bounce_not_many():
    # one touch, then three closes above while wicks keep tagging the zone
    r = replay(rec(), path(77000, 76400, 76540, 76530, 76545, 77200), now=T0 + timedelta(hours=6))
    assert r.touches == 1 and r.bounces == 1


def test_old_break_is_active_again_after_a_week():
    r = replay(rec(), path(77000, 76400, 75800, 75200, 75600), now=T0 + timedelta(days=10))
    assert r.breaks == 1 and r.role == CEILING and r.status == ACTIVE


def test_expires_when_untouched_and_far():
    r = replay(rec(), path(76400, 77000, *[80000] * 200), now=T0 + timedelta(days=9))
    assert r.status == EXPIRED and blocking_zones({"a": r}) == []


def test_weak_when_it_breaks_more_than_it_holds():
    closes = [77000, 75500, 77000, 75500, 77000, 75500]  # crosses every hour
    r = replay(rec(), path(*closes), now=T0 + timedelta(hours=6))
    assert r.breaks == 5 and r.bounces == 0 and r.status == WEAK


def test_merge_skips_mostly_overlapping_but_keeps_neighbours():
    book = {"manual:75500-76500": rec(75500, 76500)}
    added = merge_candidates(book, [Zone(75933, 76453, FLOOR, 5, "h1_60d"),           # 100% inside manual
                                    Zone(76321, 77103, FLOOR, 3, "m30_48h_reciente"),  # 23% overlap
                                    Zone(77738, 78660, CEILING, 3, "m30_48h_reciente")], T0)
    assert added == ["m30_48h_reciente:76321-77103", "m30_48h_reciente:77738-78660"]
    assert book["m30_48h_reciente:77738-78660"].role == CEILING


def test_manual_sync_follows_the_file_and_prune_keeps_manual():
    book: dict = {}
    added, removed = sync_manual(book, [Zone(75500, 76500, "manual"), Zone(81000, 81500, "manual")], T0)
    assert len(added) == 2 and removed == []
    added, removed = sync_manual(book, [Zone(75500, 76500, "manual")], T0)
    assert added == [] and removed == ["manual:81000-81500"]
    book["manual:75500-76500"].status = EXPIRED
    assert prune(book, T0 + timedelta(days=90)) == []


def test_change_messages_only_on_change():
    r = replay(rec(), path(77000, 76400, 75800, 75200), now=T0 + timedelta(hours=4))
    msgs = change_messages({r.zid: (ACTIVE, FLOOR)}, {r.zid: r})
    assert len(msgs) == 1 and "ROTA hacia abajo" in msgs[0] and "techo" in msgs[0]
    assert change_messages({r.zid: (r.status, r.role)}, {r.zid: r}) == []


def test_expired_zone_is_not_announced_again():
    book = {"h1_60d:82512-83118": rec(82512, 83118, "h1_60d")}
    book["h1_60d:82512-83118"].zid = "h1_60d:82512-83118"
    book["h1_60d:82512-83118"].status = EXPIRED
    assert merge_candidates(book, [Zone(82512, 83118, CEILING, 3, "h1_60d")], T0) == []
