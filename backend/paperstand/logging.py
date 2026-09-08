"""Logging setup.

One place decides what a Paperstand log line looks like, so that the server, the
scanner and the command line all sound the same. The level comes from
``PAPERSTAND_LOG_LEVEL`` — the same setting Uvicorn is given — and defaults to
``info``.

Configuration is applied once per process: calling it again only adjusts the
level, so an embedding application keeps its own handlers.
"""

from __future__ import annotations

import logging
import os
import sys
import threading

#: Format of a Paperstand log line.
LOG_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
DATE_FORMAT = "%Y-%m-%dT%H:%M:%S"

DEFAULT_LEVEL = "info"

_lock = threading.Lock()
_configured = False


def resolve_level(level: str | None = None) -> int:
    """Turn a level name into a :mod:`logging` level, tolerating nonsense."""
    name = (level or os.environ.get("PAPERSTAND_LOG_LEVEL") or DEFAULT_LEVEL).strip().upper()
    resolved = logging.getLevelNamesMapping().get(name)
    return resolved if resolved is not None else logging.INFO


def configure_logging(level: str | None = None) -> None:
    """Attach a stream handler to the ``paperstand`` logger, once."""
    global _configured
    logger = logging.getLogger("paperstand")
    with _lock:
        if not _configured:
            handler = logging.StreamHandler(sys.stderr)
            handler.setFormatter(logging.Formatter(LOG_FORMAT, DATE_FORMAT))
            logger.addHandler(handler)
            logger.propagate = False
            _configured = True
        logger.setLevel(resolve_level(level))


def get_logger(name: str) -> logging.Logger:
    """Logger for a module, always under the ``paperstand`` root."""
    if name == "paperstand" or name.startswith("paperstand."):
        return logging.getLogger(name)
    return logging.getLogger(f"paperstand.{name}")
