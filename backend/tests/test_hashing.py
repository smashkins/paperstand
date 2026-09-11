"""Content hashing: the identity every issue's id is derived from."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from paperstand.scanner.hashing import content_hash

#: The well-known SHA-256 of the eleven ASCII bytes ``hello world``.
HELLO_WORLD_SHA256 = "b94d27b9934d3e08a52e52d7da7dabfac484efe37a5380ee9088f7ace2efcde9"

#: The SHA-256 of zero bytes: every hasher's fixed starting point.
EMPTY_SHA256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


def test_a_known_files_digest_matches_the_known_sha256(tmp_path: Path) -> None:
    path = tmp_path / "hello.txt"
    path.write_bytes(b"hello world")

    digest = content_hash(path)

    assert digest == HELLO_WORLD_SHA256
    assert len(digest) == 64
    assert digest == digest.lower()


def test_an_empty_file_hashes_to_the_empty_digest(tmp_path: Path) -> None:
    path = tmp_path / "empty.pdf"
    path.write_bytes(b"")

    assert content_hash(path) == EMPTY_SHA256


def test_a_large_file_matches_reading_it_whole(tmp_path: Path) -> None:
    path = tmp_path / "large.pdf"
    # A few times past `hashlib.file_digest`'s internal buffer, to exercise more
    # than a single read.
    path.write_bytes(os.urandom(3_000_000))

    assert content_hash(path) == hashlib.sha256(path.read_bytes()).hexdigest()


def test_different_bytes_hash_differently(tmp_path: Path) -> None:
    first = tmp_path / "a.pdf"
    second = tmp_path / "b.pdf"
    first.write_bytes(b"%PDF-1.7\nfirst\n")
    second.write_bytes(b"%PDF-1.7\nsecond\n")

    assert content_hash(first) != content_hash(second)


def test_the_same_bytes_hash_the_same_wherever_they_live(tmp_path: Path) -> None:
    first = tmp_path / "one" / "issue.pdf"
    second = tmp_path / "two" / "same-bytes-different-name.pdf"
    first.parent.mkdir()
    second.parent.mkdir()
    payload = b"%PDF-1.7\nidentical content\n"
    first.write_bytes(payload)
    second.write_bytes(payload)

    assert content_hash(first) == content_hash(second)
