"""Classify a closed loser against shadow alpha and the M15 path after entry.

Pure: no I/O. The lab page uses this so the numbers refresh themselves.
Suggestions are advisory only: they never write config or place orders.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Sequence

ALPHA_AVOIDED = "alpha_habria_evitado"
ALPHA_SAME = "alpha_igual"
ALPHA_OPPOSITE = "alpha_contrario"
ALPHA_MISSING = "sin_alpha"

PATH_AGAINST = "siguio_en_contra"
PATH_WENT_GREEN = "fue_verde_luego_rojo"
PATH_NO_BARS = "sin_velas"

LABELS = {
    ALPHA_AVOIDED: "El alpha no habría entrado (Neutro u otro lado)",
    ALPHA_SAME: "El alpha también iba con el bot",
    ALPHA_OPPOSITE: "El alpha decía el lado contrario",
    ALPHA_MISSING: "No hay score cerca de la apertura (alpha apagado o >15 min)",
    PATH_AGAINST: "Después de entrar, el precio siguió en contra",
    PATH_WENT_GREEN: "Llegó a estar en verde y después cerró en rojo",
    PATH_NO_BARS: "No hay velas M15 después de la apertura (pasados 30 días o daemon off)",
}

MIN_LOSERS_TO_TUNE = 15
HINT_MIN = 3
DISCUSS_MIN = 5

SEVERITY_HINT = "hint"
SEVERITY_DISCUSS = "discuss"
SEVERITY_READY = "ready"

SEVERITY_LABELS = {
    SEVERITY_HINT: "Pista",
    SEVERITY_DISCUSS: "Para decidir",
    SEVERITY_READY: "Lista para acordar",
}


@dataclass(frozen=True)
class LoserRow:
    symbol: str
    side: str
    net: float
    opened_at: Optional[datetime]
    closed_at: Optional[datetime]
    reason_label: str
    alpha_code: str
    alpha_direction: str
    alpha_score: Optional[float]
    path_code: str
    mfe: Optional[float]
    mae: Optional[float]
    signal_skip: str

    @property
    def alpha_label(self) -> str:
        return LABELS.get(self.alpha_code, self.alpha_code)

    @property
    def path_label(self) -> str:
        return LABELS.get(self.path_code, self.path_code)

    def as_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "side": self.side,
            "net": self.net,
            "opened_at": self.opened_at.isoformat(timespec="seconds") if self.opened_at else None,
            "closed_at": self.closed_at.isoformat(timespec="seconds") if self.closed_at else None,
            "reason_label": self.reason_label,
            "alpha_code": self.alpha_code,
            "alpha_label": self.alpha_label,
            "alpha_direction": self.alpha_direction,
            "alpha_score": self.alpha_score,
            "path_code": self.path_code,
            "path_label": self.path_label,
            "mfe": self.mfe,
            "mae": self.mae,
            "signal_skip": self.signal_skip,
        }


def classify_alpha(side: str, alpha_direction: Optional[str]) -> str:
    side_u = str(side or "").strip().upper()
    if alpha_direction is None:
        return ALPHA_MISSING
    ad = str(alpha_direction).strip().upper()
    if ad in ("", "NONE"):
        return ALPHA_AVOIDED
    if ad == side_u:
        return ALPHA_SAME
    if ad in ("BUY", "SELL"):
        return ALPHA_OPPOSITE
    return ALPHA_MISSING


def classify_path(
    side: str,
    entry: Optional[float],
    highs: Sequence[float],
    lows: Sequence[float],
) -> tuple[str, Optional[float], Optional[float]]:
    """MFE/MAE in price units from bars after entry. Sign is favorable-positive."""
    try:
        px = float(entry) if entry is not None else 0.0
    except (TypeError, ValueError):
        px = 0.0
    if px <= 0.0 or not highs or not lows:
        return PATH_NO_BARS, None, None
    side_u = str(side or "").strip().upper()
    n = min(len(highs), len(lows))
    if n <= 0:
        return PATH_NO_BARS, None, None
    if side_u == "BUY":
        mfe = max(float(highs[i]) - px for i in range(n))
        mae = max(px - float(lows[i]) for i in range(n))
    elif side_u == "SELL":
        mfe = max(px - float(lows[i]) for i in range(n))
        mae = max(float(highs[i]) - px for i in range(n))
    else:
        return PATH_NO_BARS, None, None
    if mfe > 0.0:
        return PATH_WENT_GREEN, mfe, mae
    return PATH_AGAINST, mfe, mae


def tally_alpha(rows: Sequence[LoserRow]) -> dict[str, int]:
    out = {ALPHA_AVOIDED: 0, ALPHA_SAME: 0, ALPHA_OPPOSITE: 0, ALPHA_MISSING: 0}
    for row in rows:
        if row.alpha_code in out:
            out[row.alpha_code] += 1
    return out


def ready_to_discuss_change(n_losers: int) -> bool:
    return int(n_losers) >= MIN_LOSERS_TO_TUNE


def severity_for(n_support: int, *, n_total: int) -> Optional[str]:
    """How loud a pattern may speak. One trade is a hint, not a verdict."""
    support = int(n_support)
    total = int(n_total)
    if support <= 0 or total <= 0:
        return None
    if total >= MIN_LOSERS_TO_TUNE and support >= max(HINT_MIN, (total + 2) // 3):
        return SEVERITY_READY
    if support >= DISCUSS_MIN:
        return SEVERITY_DISCUSS
    if support >= HINT_MIN:
        return SEVERITY_HINT
    if support == total:
        return SEVERITY_HINT
    return None


@dataclass(frozen=True)
class Suggestion:
    code: str
    severity: str
    title: str
    why: str
    proposal: str
    n_support: int
    n_total: int

    @property
    def severity_label(self) -> str:
        return SEVERITY_LABELS.get(self.severity, self.severity)

    @property
    def share_line(self) -> str:
        return f"[{self.severity_label}] {self.title} — {self.proposal}"

    def as_dict(self) -> dict:
        return {
            "code": self.code,
            "severity": self.severity,
            "severity_label": self.severity_label,
            "title": self.title,
            "why": self.why,
            "proposal": self.proposal,
            "n_support": self.n_support,
            "n_total": self.n_total,
            "share_line": self.share_line,
        }


def _maybe(
    *,
    code: str,
    n_support: int,
    n_total: int,
    title: str,
    why: str,
    proposal: str,
) -> Optional[Suggestion]:
    sev = severity_for(n_support, n_total=n_total)
    if sev is None:
        return None
    return Suggestion(
        code=code,
        severity=sev,
        title=title,
        why=why,
        proposal=proposal,
        n_support=n_support,
        n_total=n_total,
    )


def propose_improvements(rows: Sequence[LoserRow], *, n_wins: int = 0) -> list[Suggestion]:
    """Pattern hints from losers. Empty until there is at least one red close."""
    items = list(rows)
    n = len(items)
    if n <= 0:
        return []

    alpha = tally_alpha(items)
    path = tally_path(items)
    known_alpha = n - alpha.get(ALPHA_MISSING, 0)
    known_path = n - path.get(PATH_NO_BARS, 0)

    out: list[Suggestion] = []
    avoided = alpha.get(ALPHA_AVOIDED, 0)
    same = alpha.get(ALPHA_SAME, 0)
    opposite = alpha.get(ALPHA_OPPOSITE, 0)
    missing = alpha.get(ALPHA_MISSING, 0)
    green = path.get(PATH_WENT_GREEN, 0)
    against = path.get(PATH_AGAINST, 0)
    no_bars = path.get(PATH_NO_BARS, 0)

    cand = _maybe(
        code="alpha_veto",
        n_support=avoided,
        n_total=n,
        title=f"El alpha no habría entrado en {avoided} de {n} perdedoras",
        why=(
            f"En {avoided}/{n} el score era Neutro (o no superaba el umbral/costo). "
            f"Ganadoras en el log: {int(n_wins)}. El alpha no opera; esto es un veto de entrada."
        ),
        proposal="Si acordamos: no abrir cuando el score sombra sea Neutro. El bot sigue igual hasta que lo digamos.",
    )
    if cand and (known_alpha == 0 or avoided >= max(1, known_alpha / 2)):
        out.append(cand)

    cand = _maybe(
        code="alpha_opposite",
        n_support=opposite,
        n_total=n,
        title=f"El alpha decía el lado contrario en {opposite} de {n}",
        why=f"El bot compró/vendió y el score sombra iba al revés ({opposite}/{n}).",
        proposal="Si acordamos: veto cuando el alpha apunta al lado opuesto (más estricto que solo Neutro).",
    )
    if cand:
        out.append(cand)

    cand = _maybe(
        code="alpha_no_help",
        n_support=same,
        n_total=n,
        title=f"El alpha también iba con el bot en {same} de {n}",
        why="Ahí el score sombra no te habría salvado. El problema es la entrada del bot o la salida (watcher), no el filtro alpha.",
        proposal="Si acordamos: no usar el alpha como veto; mirar filtros de entrada (ADX/slope/breakout) o el ladder.",
    )
    if cand and same >= avoided:
        out.append(cand)

    cand = _maybe(
        code="giveback",
        n_support=green,
        n_total=n if known_path == 0 else known_path,
        title=f"{green} perdedoras llegaron a estar en verde y cerraron en rojo",
        why="Hubo recorrido a favor (MFE > 0) y después se entregó. Eso huele a salida, no a entrada.",
        proposal="Si acordamos: revisar watcher — lock +1 USD o take parcial cuando ya estuvo verde.",
    )
    if cand:
        out.append(cand)

    cand = _maybe(
        code="never_green",
        n_support=against,
        n_total=n if known_path == 0 else known_path,
        title=f"{against} perdedoras nunca fueron a favor",
        why="Después de entrar, el precio siguió en contra. La entrada no tenía recorrido.",
        proposal="Si acordamos: endurecer el filtro de entrada (no romper / ADX plano / techo de compras) en esos símbolos.",
    )
    if cand:
        out.append(cand)

    cand = _maybe(
        code="alpha_offline",
        n_support=missing,
        n_total=n,
        title=f"No hay score cerca de la apertura en {missing} de {n}",
        why="El cruce con alpha no se puede juzgar (engine apagado o más de 15 min de diferencia).",
        proposal="Dejá corriendo `py -m trading.application.alpha_engine`. Sin eso las sugerencias de veto quedan ciegas.",
    )
    if cand:
        out.append(cand)

    cand = _maybe(
        code="no_candles",
        n_support=no_bars,
        n_total=n,
        title=f"No hay velas M15 después de {no_bars} de {n} aperturas",
        why="Sin velas no se ve si fue giveback o nunca-verde. Los ticks se van a las 48 h; las velas duran 30 días.",
        proposal="Dejá Docker + `py mt5_tick_daemon.py`. El juicio de recorrido se calcula solo en /lab; no hace falta pedirlo.",
    )
    if cand:
        out.append(cand)

    by_sym = Counter(r.symbol for r in items)
    if by_sym:
        top_sym, top_n = by_sym.most_common(1)[0]
        cand = _maybe(
            code="symbol_cluster",
            n_support=top_n,
            n_total=n,
            title=f"{top_n} de {n} perdedoras son {top_sym}",
            why=f"La pérdida está concentrada en un símbolo ({top_sym}).",
            proposal=f"Si acordamos: subir la barra o pausar entradas nuevas en {top_sym} (el resto sigue).",
        )
        if cand and top_n >= max(HINT_MIN, (n + 1) // 2):
            out.append(cand)

    by_reason = Counter((r.reason_label or "").strip() or "—" for r in items)
    sl_n = sum(c for label, c in by_reason.items() if "stop loss" in label.lower() or label.lower() == "sl")
    cand = _maybe(
        code="stop_cluster",
        n_support=sl_n,
        n_total=n,
        title=f"{sl_n} de {n} perdedoras cerraron por stop loss",
        why="El stop se está comiendo el libro. Puede ser stop corto, entrada tarde, o ambos.",
        proposal="Si acordamos: no tocar el live todavía — primero mirar si esas mismas fueron never-green o giveback.",
    )
    if cand:
        out.append(cand)

    rank = {SEVERITY_READY: 0, SEVERITY_DISCUSS: 1, SEVERITY_HINT: 2}
    out.sort(key=lambda s: (rank.get(s.severity, 9), -s.n_support, s.code))
    return out[:5]


def format_share_text(rows: Sequence[LoserRow], suggestions: Sequence[Suggestion], *, n_wins: int = 0) -> str:
    """Plain text to paste in chat. No bot change is implied."""
    items = list(rows)
    n = len(items)
    total_net = round(sum(r.net for r in items), 2)
    lines = [
        "Sugerencia Lab (no aplicada al bot)",
        f"Perdedoras: {n} (suma {total_net}) · ganadoras: {int(n_wins)}",
    ]
    if not suggestions:
        if n <= 0:
            lines.append("Todavía no hay perdedoras en el log.")
        else:
            lines.append("Hay rojas, pero ningún patrón llega a pista. Seguir acumulando.")
        lines.append("¿Lo vemos?")
        return "\n".join(lines)
    for i, sug in enumerate(suggestions, start=1):
        lines.append(f"{i}. {sug.share_line}")
    lines.append("No toqué el bot. ¿Cuál miramos?")
    return "\n".join(lines)


def tally_path(rows: Sequence[LoserRow]) -> dict[str, int]:
    out = {PATH_AGAINST: 0, PATH_WENT_GREEN: 0, PATH_NO_BARS: 0}
    for row in rows:
        if row.path_code in out:
            out[row.path_code] += 1
    return out
