"""Event-loop setup for the async data layer.

Windows defaults to ProactorEventLoop, which psycopg refuses to run async on:

    Psycopg cannot use the 'ProactorEventLoop' to run in async mode.

Every async entrypoint in this project must therefore go through run(), which
selects a SelectorEventLoop on Windows and leaves other platforms alone.

SelectSelector caps out around 512 sockets. The data layer opens a handful
(one small pool plus one Redis connection), so that ceiling is not a concern.
"""

from __future__ import annotations

import asyncio
import selectors
import sys
from typing import Any, Awaitable, Callable, Optional, TypeVar

T = TypeVar("T")

IS_WINDOWS = sys.platform.startswith("win")


def new_event_loop() -> asyncio.AbstractEventLoop:
    """Return a loop psycopg can use on this platform."""
    if IS_WINDOWS:
        return asyncio.SelectorEventLoop(selectors.SelectSelector())
    return asyncio.new_event_loop()


def run(main: Awaitable[T] | Callable[[], Awaitable[T]], *, debug: Optional[bool] = None) -> T:
    """asyncio.run() on a psycopg-compatible loop.

    Accepts either a coroutine or a zero-arg factory returning one. The
    factory form matters on Windows: asyncio.run(..., loop_factory=...) builds
    the loop before awaiting, and a coroutine created beforehand is still
    fine, but a factory keeps creation inside the right loop context.
    """
    coro: Any = main() if callable(main) else main
    if IS_WINDOWS:
        return asyncio.run(coro, debug=debug, loop_factory=new_event_loop)
    return asyncio.run(coro, debug=debug)
