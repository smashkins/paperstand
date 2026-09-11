"""Content identity: the SHA-256 of a file's bytes.

The only thing in Paperstand that hashes a whole PDF. Everything an issue's
identity is derived from — :func:`paperstand.db.issue_id`, a cover's cache
path, a reading position's key — starts here.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

#: The digest every issue's identity is derived from.
ALGORITHM = "sha256"


def content_hash(path: Path) -> str:
    """The SHA-256 of ``path``'s whole content, as 64 lowercase hex digits.

    Read through :func:`hashlib.file_digest`, whose C loop reads and hashes a
    file without a Python-level chunking loop. SHA-256 is hardware-accelerated
    by OpenSSL on both amd64 and arm64, so hashing a library costs little more
    than reading it once.
    """
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, ALGORITHM).hexdigest()
