"""Parse operator ticket exceptions. Pure: no I/O."""

from __future__ import annotations

from collections.abc import Iterable


def parse_manual_exception_tickets(text: str) -> frozenset[int]:
    """Return positive MT5 position tickets listed in a whitelist file body.

    Rules:
    - empty lines and whitespace-only lines are ignored
    - ``#`` starts a comment (full-line or inline)
    - the first whitespace-separated token on a line is the ticket
    - non-integer / non-positive tokens are ignored
    """
    tickets: set[int] = set()
    for raw in str(text or "").splitlines():
        ticket = _parse_ticket_line(raw)
        if ticket is not None:
            tickets.add(ticket)
    return frozenset(tickets)


def parse_manual_exception_tickets_from_lines(lines: Iterable[str]) -> frozenset[int]:
    tickets: set[int] = set()
    for raw in lines:
        ticket = _parse_ticket_line(raw)
        if ticket is not None:
            tickets.add(ticket)
    return frozenset(tickets)


def _parse_ticket_line(raw: str) -> int | None:
    line = str(raw or "").strip()
    if not line or line.startswith("#"):
        return None
    if "#" in line:
        line = line.split("#", 1)[0].strip()
        if not line:
            return None
    token = line.split()[0]
    if not token.isdigit():
        return None
    ticket = int(token)
    if ticket <= 0:
        return None
    return ticket
