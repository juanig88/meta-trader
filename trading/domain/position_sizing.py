"""Broker volume stepping. Pure: no I/O, no broker."""

from __future__ import annotations


def clamp_lot_volume(
    volume: float,
    *,
    volume_min: float = 0.01,
    volume_step: float = 0.01,
    volume_max: float = 100.0,
) -> float:
    vmin = float(volume_min) if float(volume_min) > 0 else 0.01
    vmax = float(volume_max) if float(volume_max) > 0 else 100.0
    if vmax < vmin:
        vmax = vmin
    vstep = float(volume_step) if float(volume_step) > 0 else vmin
    vol = max(vmin, min(float(volume), vmax))
    steps = round(vol / vstep)
    vol = steps * vstep
    if vol + 1e-12 < vmin:
        vol = vmin
    if vol - 1e-12 > vmax:
        vol = vmax
    return round(vol + 1e-12, 2)
