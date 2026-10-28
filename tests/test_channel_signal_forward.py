from __future__ import annotations

from trading.infrastructure.channel_signal_forward import (
    entity_id_aliases,
    export_target_candidates,
)


def test_export_target_candidates_table():
    cases = [
        ("username url", "t.me/SantinoCripto", ["t.me/SantinoCripto"]),
        ("marked channel id", "-1001748919669", [-1001748919669, 1748919669]),
        ("plain digits", "1748919669", [1748919669]),
        ("empty uses default", "", ["t.me/SantinoCripto"]),
    ]
    for name, raw, want in cases:
        assert export_target_candidates(raw) == want, name


def test_entity_id_aliases_roundtrip_marked_id():
    marked = -1001748919669
    aliases = entity_id_aliases(marked)
    assert marked in aliases
    assert 1748919669 in aliases
    assert marked in entity_id_aliases(1748919669)
