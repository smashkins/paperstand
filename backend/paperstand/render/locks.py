"""One lock per key, created on demand and thrown away when nobody holds it.

Rendering on demand needs a mutex per *issue*, not one for the whole process:
two requests for the same cover must not both render it, while two requests for
two different covers should not queue behind each other. A dictionary of locks
gives that, as long as the dictionary itself is guarded and entries do not
accumulate for every issue the server has ever been asked about.

:class:`KeyedLock` is that dictionary: it hands out a context manager, counts
who is waiting on each key, and drops the entry when the last one leaves.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field


@dataclass(slots=True)
class _Entry:
    """One key's lock, and how many callers are interested in it."""

    lock: threading.Lock = field(default_factory=threading.Lock)
    waiting: int = 0


class KeyedLock:
    """A mutex per key, with no entry left behind once the key is idle."""

    def __init__(self) -> None:
        self._guard = threading.Lock()
        self._entries: dict[str, _Entry] = {}

    @contextmanager
    def __call__(self, key: str) -> Iterator[None]:
        """Hold the lock for ``key`` for the duration of the block."""
        with self._guard:
            entry = self._entries.get(key)
            if entry is None:
                entry = self._entries[key] = _Entry()
            entry.waiting += 1
        entry.lock.acquire()
        try:
            yield
        finally:
            entry.lock.release()
            with self._guard:
                entry.waiting -= 1
                if entry.waiting == 0:
                    self._entries.pop(key, None)

    @property
    def held(self) -> int:
        """How many keys are currently spoken for; for tests and diagnostics."""
        with self._guard:
            return len(self._entries)
