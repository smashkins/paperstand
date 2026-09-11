"""Moving a file: atomic, occupied, the cross-device fallback, and parking."""

from __future__ import annotations

import errno
import os
from pathlib import Path

import pytest

from paperstand.organizer.mover import (
    DestinationOccupied,
    move_file,
    park,
    remove_sidecar,
    sidecar_path,
)

REFERENCE_MTIME = 1_700_000_000


def _write(path: Path, content: bytes = b"%PDF-1.7\n") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    os.utime(path, (REFERENCE_MTIME, REFERENCE_MTIME))
    return path


# --------------------------------------------------------------------- move_file


def test_a_same_filesystem_move_links_then_unlinks_the_source(tmp_path: Path) -> None:
    source = _write(tmp_path / "inbox" / "Corriere del Ponte - 2026-03-17.pdf", b"hello world")
    destination = tmp_path / "library" / "Corriere del Ponte" / "2026" / source.name

    move_file(source, destination)

    assert not source.exists()
    assert destination.read_bytes() == b"hello world"
    assert destination.stat().st_mtime == REFERENCE_MTIME


def test_the_destinations_parents_are_created(tmp_path: Path) -> None:
    source = _write(tmp_path / "inbox" / "Orizzonte - 2026-09-05 - n1630.pdf")
    destination = tmp_path / "library" / "Orizzonte" / "2026" / source.name

    move_file(source, destination)

    assert destination.is_file()


def test_a_cross_device_move_copies_and_leaves_no_part_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _write(tmp_path / "inbox" / "Confini - 2026.pdf", b"cross device bytes")
    destination = tmp_path / "library" / "Confini" / "2026" / source.name
    destination.parent.mkdir(parents=True)

    real_link = os.link
    calls = {"n": 0}

    def flaky_link(src: object, dst: object, *args: object, **kwargs: object) -> None:
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError(errno.EXDEV, "cross-device link")
        real_link(src, dst, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr("paperstand.organizer.mover.os.link", flaky_link)

    move_file(source, destination)

    assert not source.exists()
    assert destination.read_bytes() == b"cross device bytes"
    assert destination.stat().st_mtime == REFERENCE_MTIME
    assert list(destination.parent.glob(".*")) == []


def test_occupied_at_the_first_link_leaves_everything_untouched(tmp_path: Path) -> None:
    source = _write(tmp_path / "inbox" / "Corriere del Ponte - 2026-03-17.pdf", b"new bytes")
    destination = tmp_path / "library" / "Corriere del Ponte" / "2026" / source.name
    _write(destination, b"occupant bytes")
    occupant_mtime_ns = destination.stat().st_mtime_ns

    with pytest.raises(DestinationOccupied):
        move_file(source, destination)

    assert source.read_bytes() == b"new bytes"
    assert destination.read_bytes() == b"occupant bytes"
    assert destination.stat().st_mtime_ns == occupant_mtime_ns


def test_occupied_at_the_final_link_after_a_cross_device_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The destination is only occupied *after* the first attempt goes down
    the copy path — a competitor winning the race while the copy runs."""
    source = _write(tmp_path / "inbox" / "Confini - 2026.pdf", b"new bytes")
    destination = tmp_path / "library" / "Confini" / "2026" / source.name
    _write(destination, b"occupant bytes")
    occupant_mtime_ns = destination.stat().st_mtime_ns

    real_link = os.link
    calls = {"n": 0}

    def flaky_link(src: object, dst: object, *args: object, **kwargs: object) -> None:
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError(errno.EXDEV, "cross-device link")
        real_link(src, dst, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr("paperstand.organizer.mover.os.link", flaky_link)

    with pytest.raises(DestinationOccupied):
        move_file(source, destination)

    assert source.read_bytes() == b"new bytes"
    assert destination.read_bytes() == b"occupant bytes"
    assert destination.stat().st_mtime_ns == occupant_mtime_ns
    assert list(destination.parent.glob(".*")) == []


def test_link_unsupported_everywhere_raises_and_removes_the_temp_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _write(tmp_path / "inbox" / "Confini - 2026.pdf", b"original bytes")
    destination = tmp_path / "library" / "Confini" / "2026" / source.name

    def always_eperm(*_args: object, **_kwargs: object) -> None:
        raise OSError(errno.EPERM, "operation not permitted")

    monkeypatch.setattr("paperstand.organizer.mover.os.link", always_eperm)

    with pytest.raises(OSError, match="does not support hard links"):
        move_file(source, destination)

    assert source.read_bytes() == b"original bytes"
    assert not destination.exists()
    assert list(destination.parent.glob(".*")) == []


# -------------------------------------------------------------------------- park


def test_park_moves_the_file_and_writes_the_sidecar(tmp_path: Path) -> None:
    source = _write(tmp_path / "inbox" / "Something.pdf", b"bytes")
    folder = tmp_path / "inbox" / "unsorted"
    folder.mkdir()

    destination = park(source, folder, 'no declared title matches "Something"')

    assert destination == folder / "Something.pdf"
    assert not source.exists()
    assert destination.read_bytes() == b"bytes"
    assert sidecar_path(destination).read_text("utf-8") == 'no declared title matches "Something"\n'


def test_park_suffixes_a_name_already_taken_by_a_different_file(tmp_path: Path) -> None:
    folder = tmp_path / "inbox" / "unsorted"
    _write(folder / "Something.pdf", b"first")

    second = _write(tmp_path / "inbox" / "Something.pdf", b"second")
    destination2 = park(second, folder, "reason two")
    assert destination2 == folder / "Something (2).pdf"

    third = _write(tmp_path / "inbox" / "Something.pdf", b"third")
    destination3 = park(third, folder, "reason three")
    assert destination3 == folder / "Something (3).pdf"

    assert (folder / "Something.pdf").read_bytes() == b"first"
    assert destination2.read_bytes() == b"second"
    assert destination3.read_bytes() == b"third"


def test_the_sidecar_is_refreshed_only_when_the_reason_changes(tmp_path: Path) -> None:
    """A file already parked — walked in place on a later run — is re-parked
    onto itself: nothing moves, and the sidecar is rewritten only when its
    reason actually changed."""
    source = _write(tmp_path / "inbox" / "Something.pdf")
    folder = tmp_path / "inbox" / "unsorted"
    folder.mkdir()

    destination = park(source, folder, "reason one")
    sidecar = sidecar_path(destination)
    first_mtime_ns = sidecar.stat().st_mtime_ns

    same = park(destination, folder, "reason one")
    assert same == destination
    assert destination.is_file()
    assert sidecar.stat().st_mtime_ns == first_mtime_ns
    assert sidecar.read_text("utf-8") == "reason one\n"

    changed = park(destination, folder, "reason two")
    assert changed == destination
    assert sidecar.read_text("utf-8") == "reason two\n"
    assert sidecar.stat().st_mtime_ns != first_mtime_ns


def test_remove_sidecar_deletes_it_and_is_harmless_when_absent(tmp_path: Path) -> None:
    file = _write(tmp_path / "unsorted" / "Something.pdf")
    sidecar_path(file).write_text("a reason\n", encoding="utf-8")

    remove_sidecar(file)
    assert not sidecar_path(file).exists()
    remove_sidecar(file)
