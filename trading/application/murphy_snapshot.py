"""Turn closed bars into a chapter-4 reading. No terminal and no orders."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Optional, Sequence

from trading.domain.murphy_trend import read_degrees


def snapshot_from_bars(
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    dates: Optional[Sequence[Any]] = None,
) -> dict[str, Any]:
    reading = read_degrees(highs, lows, closes)
    reading["envia_ordenes"] = False
    if dates is not None and len(dates) == len(closes) and closes:
        _stamp(reading, dates)
    return reading


def _stamp(reading: dict[str, Any], dates: Sequence[Any]) -> None:
    reading["ultima_barra"] = _iso(dates[-1])
    for grado in reading.get("grados", {}).values():
        offset = grado.get("desde_indice")
        linea = grado.get("linea")
        if offset is None or not linea:
            continue
        for anchor in linea.get("anclas") or []:
            idx = int(offset) + int(anchor["index"])
            if 0 <= idx < len(dates):
                anchor["fecha"] = _iso(dates[idx])


def _iso(value: Any) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)
