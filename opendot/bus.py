"""In-process pub/sub: everything the UI shows live flows through here."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any


class EventBus:
    def __init__(self) -> None:
        self._subs: set[asyncio.Queue] = set()
        self._listeners: list = []
        self._loop: asyncio.AbstractEventLoop | None = None

    def listen(self, callback) -> None:
        """Python-side subscriber: ``callback(kind: str, data: dict)`` (sync or async).
        Used by extensions, e.g. channels mirror agent messages to Telegram, push sends APNs."""
        self._listeners.append(callback)

    def bind(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def subscribe(self) -> asyncio.Queue:
        self._loop = asyncio.get_running_loop()
        q: asyncio.Queue = asyncio.Queue(maxsize=2000)
        self._subs.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subs.discard(q)

    def emit(self, kind: str, **data: Any) -> None:
        try:
            on_loop = asyncio.get_running_loop() is not None
        except RuntimeError:
            on_loop = False
        if not on_loop:
            # called from a worker thread (asyncio.to_thread): asyncio queues and
            # listener tasks aren't thread-safe, so hop onto the server loop
            if self._loop and not self._loop.is_closed():
                self._loop.call_soon_threadsafe(lambda: self.emit(kind, **data))
            return
        for cb in list(self._listeners):
            try:
                r = cb(kind, data)
                if asyncio.iscoroutine(r):
                    asyncio.get_running_loop().create_task(r)
            except Exception:  # an extension must never break the core
                logging.getLogger("opendot.bus").exception("listener failed")
        msg = json.dumps({"kind": kind, "ts": time.time(), **data}, default=str)
        for q in list(self._subs):
            try:
                q.put_nowait(msg)
            except asyncio.QueueFull:
                # slow client fell behind: drop the backlog and tell it to reload
                # from REST, rather than silently losing e.g. the final message
                while not q.empty():
                    q.get_nowait()
                q.put_nowait('{"kind":"resync"}')


bus = EventBus()
