from trading.domain.position_sizing import clamp_lot_volume


def test_clamp_lot_volume_table():
    cases = [
        ("min floor", 0.004, 0.01, 0.01, 100.0, 0.01),
        ("step round 0.02", 0.018, 0.01, 0.01, 100.0, 0.02),
        ("max cap", 5.0, 0.01, 0.01, 1.0, 1.0),
        ("zero volume uses min", 0.0, 0.01, 0.01, 100.0, 0.01),
    ]
    for name, vol, vmin, step, vmax, want in cases:
        got = clamp_lot_volume(vol, volume_min=vmin, volume_step=step, volume_max=vmax)
        assert got == want, f"{name}: {got} != {want}"
