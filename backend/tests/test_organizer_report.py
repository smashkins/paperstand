"""``organizer/report.py``: the inventory, and the report file's read and write."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from paperstand.organizer.report import inventory, read_run, write_run
from paperstand.schemas import OrganizerMove, OrganizerParked, OrganizerRun


def _run(**overrides: object) -> OrganizerRun:
    values: dict[str, object] = {
        "started_at": "2026-09-12T08:00:00+00:00",
        "finished_at": "2026-09-12T08:00:01+00:00",
        "mode": "apply",
        "inbox": "/inbox",
        "refused": False,
        "moved": 1,
        "duplicate": 0,
        "unsorted": 0,
        "skipped": 0,
        "failed": 0,
        "moves": [OrganizerMove(source="a.pdf", destination="Title/2026/Title - 2026-09-12.pdf")],
        "parked": [],
        "scan_requested": True,
    }
    values.update(overrides)
    return OrganizerRun(**values)  # type: ignore[arg-type]


# --------------------------------------------------------------- inventory


def test_inventory_is_empty_when_neither_folder_exists(tmp_path: Path) -> None:
    assert inventory(tmp_path / "inbox") == []


def test_inventory_lists_a_file_without_a_sidecar_and_one_in_a_sub_folder(
    tmp_path: Path,
) -> None:
    inbox = tmp_path / "inbox"
    (inbox / "unsorted" / "nested").mkdir(parents=True)
    (inbox / "unsorted" / "no-sidecar.pdf").write_bytes(b"one")
    (inbox / "unsorted" / "nested" / "reasoned.pdf").write_bytes(b"two")
    (inbox / "unsorted" / "nested" / "reasoned.pdf.txt").write_text(
        "no declared title matches\n", encoding="utf-8"
    )
    (inbox / "duplicates").mkdir(parents=True)
    (inbox / "duplicates" / "copy.pdf").write_bytes(b"three")
    (inbox / "duplicates" / "copy.pdf.txt").write_text("duplicate of X\n", encoding="utf-8")

    entries = inventory(inbox)

    by_name = {(entry.folder, entry.name): entry for entry in entries}
    assert by_name[("unsorted", "no-sidecar.pdf")].reason is None
    assert by_name[("unsorted", "no-sidecar.pdf")].size == 3
    assert by_name[("unsorted", "nested/reasoned.pdf")].reason == "no declared title matches"
    assert by_name[("duplicates", "copy.pdf")].reason == "duplicate of X"
    # Sorted by folder, then by name: duplicates never sort before unsorted.
    assert [entry.folder for entry in entries] == ["unsorted", "unsorted", "duplicates"]


def test_inventory_ignores_a_sidecar_left_without_its_pdf(tmp_path: Path) -> None:
    inbox = tmp_path / "inbox"
    (inbox / "unsorted").mkdir(parents=True)
    (inbox / "unsorted" / "orphan.pdf.txt").write_text("stale\n", encoding="utf-8")

    assert inventory(inbox) == []


# ------------------------------------------------------------- write / read


def test_write_then_read_round_trips(tmp_path: Path) -> None:
    path = tmp_path / "organizer" / "last-run.json"
    run = _run()

    write_run(path, run)

    assert read_run(path) == run


def test_write_creates_the_parent_directory(tmp_path: Path) -> None:
    path = tmp_path / "organizer" / "sub" / "last-run.json"

    write_run(path, _run())

    assert path.is_file()


def test_write_is_atomic_and_leaves_no_temporary_file_behind(tmp_path: Path) -> None:
    directory = tmp_path / "organizer"
    directory.mkdir()
    path = directory / "last-run.json"

    write_run(path, _run())
    write_run(path, _run(moved=2))

    assert [entry.name for entry in directory.iterdir()] == ["last-run.json"]
    assert json.loads(path.read_text("utf-8"))["moved"] == 2


def test_write_that_cannot_create_its_directory_is_a_warning_and_nothing_raises(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    blocked = tmp_path / "not-a-directory"
    blocked.write_text("in the way", encoding="utf-8")
    path = blocked / "organizer" / "last-run.json"

    with caplog.at_level(logging.WARNING, logger="paperstand"):
        write_run(path, _run())

    assert not path.exists()
    assert any("could not write" in record.message for record in caplog.records)


def test_read_run_on_an_absent_file_is_none(tmp_path: Path) -> None:
    assert read_run(tmp_path / "nope.json") is None


def test_read_run_on_invalid_json_is_none_and_warns(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    path = tmp_path / "last-run.json"
    path.write_text("not json at all", encoding="utf-8")

    with caplog.at_level(logging.WARNING, logger="paperstand"):
        assert read_run(path) is None

    assert any("not valid" in record.message for record in caplog.records)


def test_read_run_on_a_future_version_is_none_and_warns(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    path = tmp_path / "last-run.json"
    payload = json.loads(_run().model_dump_json())
    payload["version"] = 2
    path.write_text(json.dumps(payload), encoding="utf-8")

    with caplog.at_level(logging.WARNING, logger="paperstand"):
        assert read_run(path) is None

    assert any("not valid" in record.message for record in caplog.records)


def test_read_run_on_a_good_file(tmp_path: Path) -> None:
    path = tmp_path / "last-run.json"
    run = _run(
        parked=[
            OrganizerParked(
                folder="unsorted",
                name="a.pdf",
                reason="no declared title matches",
                size=123,
                modified="2026-09-12T07:00:00+00:00",
            )
        ]
    )
    write_run(path, run)

    back = read_run(path)

    assert back == run
