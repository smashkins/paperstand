"""The two-phase scan, against the generated sample library.

Every count asserted here is derived from the generator's own per-layout tally
(see ``tests/conftest.py``) or from the configuration the scan was given, never
typed out by hand.
"""

from __future__ import annotations

import itertools
import logging
import os
import shutil
import sqlite3
import sys
import time
from collections.abc import Sequence
from pathlib import Path

import pytest
import yaml
from PIL import Image

from paperstand.config import Settings
from paperstand.db import issue_id, open_database
from paperstand.scanner.covers import CoverError, CoverResult, cover_paths, has_cover, render_cover
from paperstand.scanner.scanner import Scanner, ScanProgress, ScanResult, scan_once
from tests.conftest import SampleLibrary, quiet_settings, write_sample_config

A_NEWSPAPER = "Newspapers/2026/03/17/Corriere_del_Ponte_17_Marzo_2026.pdf"

#: Titles the sample configuration must produce, per library.
NEWSPAPER_TITLES = {
    "Corriere del Ponte",
    "W La Gazzetta del Lago",
    "Giornale della Costa Alta",
    "Il Mattutino",
    "Cronaca 24 Pagine",
    "La Gazzetta del Lago",
    "La Gazzetta del Lago Valdora",
    "The Daily Ledger",
    "Unsorted",
}
MAGAZINE_TITLES = {"Orizzonte", "Confini", "L'Almanacco", "Bright Meadows", "Unsorted"}
ZINE_TITLES = {"Random Mag", "Something", "Circuito", "Bright Meadows"}


def query(settings: Settings, sql: str, params: Sequence[object] = ()) -> list[sqlite3.Row]:
    """Read from the catalogue without going through the scanner."""
    connection = sqlite3.connect(settings.db_path)
    connection.row_factory = sqlite3.Row
    try:
        return connection.execute(sql, tuple(params)).fetchall()
    finally:
        connection.close()


def rel_paths(settings: Settings) -> list[str]:
    return [str(row["rel_path"]) for row in query(settings, "SELECT rel_path FROM issues")]


def title_names(settings: Settings, library: str) -> set[str]:
    return {
        str(row["name"])
        for row in query(settings, "SELECT name FROM titles WHERE library_id = ?", (library,))
    }


def issues_in(settings: Settings, library: str) -> int:
    return int(
        query(settings, "SELECT count(*) AS n FROM issues WHERE library_id = ?", (library,))[0]["n"]
    )


def prepare(root: Path, data: Path) -> Settings:
    """Settings for a sample library that has its configuration written out."""
    settings = quiet_settings(root, data)
    write_sample_config(settings.config_path)
    return settings


@pytest.fixture
def scan_settings(sample_library: SampleLibrary, data_dir: Path) -> Settings:
    return prepare(sample_library.root, data_dir)


@pytest.fixture(scope="module")
def scanned(
    tmp_path_factory: pytest.TempPathFactory, sample_source: SampleLibrary
) -> tuple[Settings, ScanResult, SampleLibrary]:
    """One full scan of an untouched sample library, shared by the read-only tests."""
    base = tmp_path_factory.mktemp("scanned")
    root = base / "library"
    shutil.copytree(sample_source.root, root)
    library = SampleLibrary(root=root, counts=sample_source.counts, today=sample_source.today)
    settings = prepare(root, base / "data")
    return settings, scan_once(settings), library


# --------------------------------------------------------------------- first scan


def test_the_first_scan_catalogues_every_file(
    scanned: tuple[Settings, ScanResult, SampleLibrary],
) -> None:
    settings, result, library = scanned

    assert result.status == "ok"
    assert result.errors == 0
    assert result.files_seen == library.catalogued_files
    assert result.added == library.catalogued_files
    assert (result.updated, result.removed) == (0, 0)
    assert len(rel_paths(settings)) == library.catalogued_files


def test_the_issues_are_split_across_the_configured_libraries(
    scanned: tuple[Settings, ScanResult, SampleLibrary],
) -> None:
    settings, _, library = scanned

    assert issues_in(settings, "newspapers") == library.newspaper_files
    assert issues_in(settings, "magazines") == library.magazine_files
    assert issues_in(settings, "zines") == library.zine_files


def test_the_titles_are_the_configured_ones_plus_unsorted(
    scanned: tuple[Settings, ScanResult, SampleLibrary],
) -> None:
    settings, _, _ = scanned

    assert title_names(settings, "newspapers") == NEWSPAPER_TITLES
    assert title_names(settings, "magazines") == MAGAZINE_TITLES
    assert title_names(settings, "zines") == ZINE_TITLES


def test_the_foreign_magazines_land_in_unsorted(
    scanned: tuple[Settings, ScanResult, SampleLibrary],
) -> None:
    """An insert, a supplement and an unconfigured title are kept, not dropped."""
    settings, _, _ = scanned
    rows = query(
        settings,
        "SELECT i.derived_title FROM issues i JOIN titles t ON t.id = i.title_id "
        "WHERE t.name = 'Unsorted' AND i.library_id = 'magazines'",
    )

    assert {str(row["derived_title"]) for row in rows} == {
        "Orizzonte Kids",
        "OPQ The Other Orizzonte",
        "Circuito",
    }


def test_nothing_comes_from_the_ignored_folders(
    scanned: tuple[Settings, ScanResult, SampleLibrary],
) -> None:
    settings, _, _ = scanned
    paths = rel_paths(settings)

    assert paths
    assert all(path.lower().endswith(".pdf") for path in paths)
    assert not [path for path in paths if path.startswith("comics/")]
    assert not [path for path in paths if "@eaDir" in path or "#recycle" in path]
    assert not [path for path in paths if any(part.startswith(".") for part in path.split("/"))]


def test_the_titles_carry_where_they_came_from(
    scanned: tuple[Settings, ScanResult, SampleLibrary],
) -> None:
    settings, _, _ = scanned
    sources = {
        str(row["name"]): str(row["source"])
        for row in query(settings, "SELECT name, source FROM titles WHERE library_id = 'zines'")
    }

    assert sources == {
        "Circuito": "folder",
        "Random Mag": "filename",
        "Something": "filename",
        "Bright Meadows": "pattern",
    }


def test_the_scan_is_recorded(scanned: tuple[Settings, ScanResult, SampleLibrary]) -> None:
    settings, result, library = scanned
    row = query(settings, "SELECT * FROM scans WHERE id = ?", (result.scan_id,))[0]

    assert row["status"] == "ok"
    assert row["finished_at"] is not None
    assert row["files_seen"] == library.catalogued_files
    assert row["covers_done"] == library.catalogued_files


# ---------------------------------------------------------------------- covers


def test_every_issue_gets_a_cover_and_a_thumbnail(
    scanned: tuple[Settings, ScanResult, SampleLibrary],
) -> None:
    settings, result, library = scanned
    rows = query(settings, "SELECT id, cover_status, page_count, page_w, page_h FROM issues")

    assert result.covers_done == library.catalogued_files
    assert {str(row["cover_status"]) for row in rows} == {"ok"}
    for row in rows:
        assert row["page_count"] and row["page_count"] > 0
        assert row["page_w"] and row["page_h"]
        assert has_cover(settings.cache_path, str(row["id"]))


def test_the_cover_images_are_jpegs_of_the_documented_widths(
    scanned: tuple[Settings, ScanResult, SampleLibrary],
) -> None:
    settings, _, _ = scanned
    identifier = str(query(settings, "SELECT id FROM issues LIMIT 1")[0]["id"])
    cover, thumbnail = cover_paths(settings.cache_path, identifier)

    for path, width in ((cover, 900), (thumbnail, 300)):
        with Image.open(path) as image:
            assert image.format == "JPEG"
            assert image.width == width


def test_the_first_page_text_is_stored(
    scanned: tuple[Settings, ScanResult, SampleLibrary],
) -> None:
    settings, _, _ = scanned
    row = query(settings, "SELECT first_page_text FROM issues WHERE rel_path = ?", (A_NEWSPAPER,))[
        0
    ]

    assert "Corriere del Ponte" in str(row["first_page_text"])


# ------------------------------------------------------------------ duplicates


def test_the_dedup_suffixed_copies_lose_to_the_plain_ones(
    scanned: tuple[Settings, ScanResult, SampleLibrary],
) -> None:
    settings, _, _ = scanned
    duplicates = {
        str(row["rel_path"]): str(row["duplicate_of"])
        for row in query(
            settings, "SELECT rel_path, duplicate_of FROM issues WHERE duplicate_of IS NOT NULL"
        )
    }
    suffixed = [path for path in duplicates if path.endswith(("-1.pdf", "_(1).pdf"))]

    assert len(suffixed) == 2
    for path in suffixed:
        winner = query(
            settings,
            "SELECT rel_path, has_dedup_suffix FROM issues WHERE id = ?",
            (duplicates[path],),
        )[0]
        assert winner["has_dedup_suffix"] == 0
        assert str(winner["rel_path"]) != path


def test_a_lone_issue_is_not_a_duplicate_of_anything(
    scanned: tuple[Settings, ScanResult, SampleLibrary],
) -> None:
    settings, _, _ = scanned
    row = query(settings, "SELECT duplicate_of FROM issues WHERE rel_path = ?", (A_NEWSPAPER,))[0]

    assert row["duplicate_of"] is None


# ----------------------------------------------------------------- second scan


def test_a_second_scan_changes_nothing_and_is_quick(scan_settings: Settings) -> None:
    scan_once(scan_settings)

    started = time.perf_counter()
    again = scan_once(scan_settings)
    elapsed = time.perf_counter() - started

    assert (again.added, again.updated, again.removed) == (0, 0, 0)
    assert again.covers_done == 0
    assert again.status == "ok"
    assert elapsed < 0.5


def test_deleting_a_file_removes_its_row_and_its_cache(
    scan_settings: Settings, sample_library: SampleLibrary
) -> None:
    scan_once(scan_settings)
    identifier = issue_id(A_NEWSPAPER)
    assert has_cover(scan_settings.cache_path, identifier)
    sample_library.path(A_NEWSPAPER).unlink()

    result = scan_once(scan_settings)

    assert result.removed == 1
    assert A_NEWSPAPER not in rel_paths(scan_settings)
    assert not has_cover(scan_settings.cache_path, identifier)


def test_a_changed_file_is_reparsed_and_its_cover_regenerated(
    scan_settings: Settings,
    sample_library: SampleLibrary,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scan_once(scan_settings)
    identifier = issue_id(A_NEWSPAPER)
    target = sample_library.path(A_NEWSPAPER)
    with target.open("ab") as handle:
        handle.write(b"\n% one more byte\n")
    info = target.stat()

    rendered: list[str] = []

    def counting(path: Path, cover_id: str, cache_root: Path) -> CoverResult | CoverError:
        rendered.append(cover_id)
        return render_cover(path, cover_id, cache_root)

    monkeypatch.setattr("paperstand.scanner.scanner.render_cover", counting)
    result = scan_once(scan_settings)

    assert (result.added, result.updated, result.removed) == (0, 1, 0)
    assert rendered == [identifier]
    row = query(scan_settings, "SELECT size, cover_status FROM issues WHERE id = ?", (identifier,))[
        0
    ]
    assert row["size"] == info.st_size
    assert row["cover_status"] == "ok"
    assert has_cover(scan_settings.cache_path, identifier)


def test_changing_the_configuration_reassigns_titles_without_rendering(
    scan_settings: Settings, sample_library: SampleLibrary, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ "Giornale della Costa Alta", not "La Gazzetta del Lago Valdora" or
    "Corriere del Ponte": those two are also declared publications now, and a
    declared title is never Unsorted regardless of `titles:`."""
    first = scan_once(scan_settings)
    assert "Giornale della Costa Alta" in title_names(scan_settings, "newspapers")
    moved = issues_in(scan_settings, "newspapers")

    config = yaml.safe_load(scan_settings.config_path.read_text("utf-8"))
    for library in config["libraries"]:
        if library["name"] == "Newspapers":
            library["titles"] = [
                title for title in library["titles"] if title != "Giornale della Costa Alta"
            ]
    scan_settings.config_path.write_text(yaml.safe_dump(config, sort_keys=False), "utf-8")

    def never(*args: object, **kwargs: object) -> object:
        raise AssertionError("a cover was rendered while only the configuration changed")

    monkeypatch.setattr("paperstand.scanner.scanner.render_cover", never)
    result = scan_once(scan_settings)

    assert (result.added, result.removed, result.covers_done) == (0, 0, 0)
    assert result.updated == first.added
    assert "Giornale della Costa Alta" not in title_names(scan_settings, "newspapers")
    assert issues_in(scan_settings, "newspapers") == moved
    unsorted = query(
        scan_settings,
        "SELECT count(*) AS n FROM issues i JOIN titles t ON t.id = i.title_id "
        "WHERE t.name = 'Unsorted' AND i.library_id = 'newspapers'",
    )[0]["n"]
    assert unsorted > 1


def test_dropping_a_library_from_the_configuration_forgets_its_issues(
    scan_settings: Settings,
) -> None:
    scan_once(scan_settings)
    config = yaml.safe_load(scan_settings.config_path.read_text("utf-8"))
    config["libraries"] = [lib for lib in config["libraries"] if lib["name"] != "Zines"]
    scan_settings.config_path.write_text(yaml.safe_dump(config, sort_keys=False), "utf-8")

    scan_once(scan_settings)

    assert issues_in(scan_settings, "zines") == 0
    assert title_names(scan_settings, "zines") == set()
    assert not query(scan_settings, "SELECT id FROM libraries WHERE id = 'zines'")


# ------------------------------------------------------------------- resilience


def test_a_broken_pdf_costs_one_row_not_the_scan(
    scan_settings: Settings, sample_library: SampleLibrary
) -> None:
    broken = sample_library.path("Zines/Broken_Mag_March_2026.pdf")
    broken.write_bytes(b"not a PDF, not even close\n" * 40)

    result = scan_once(scan_settings)

    assert result.status == "ok"
    assert result.errors == 1
    row = query(
        scan_settings,
        "SELECT cover_status, cover_error FROM issues WHERE rel_path = ?",
        ("Zines/Broken_Mag_March_2026.pdf",),
    )[0]
    assert row["cover_status"] == "error"
    assert row["cover_error"]


def test_an_empty_library_scans_to_nothing(tmp_path: Path) -> None:
    root = tmp_path / "library"
    root.mkdir()
    settings = quiet_settings(root, tmp_path / "data")

    result = scan_once(settings)

    assert result.status == "ok"
    assert (result.files_seen, result.added, result.removed) == (0, 0, 0)


def test_a_missing_library_does_not_empty_the_catalogue(
    scan_settings: Settings, sample_library: SampleLibrary
) -> None:
    """A mount that failed to come up must not be read as "everything is gone"."""
    first = scan_once(scan_settings)
    shutil.rmtree(sample_library.root)

    result = scan_once(scan_settings)

    assert result.status == "ok"
    assert (result.files_seen, result.removed) == (0, 0)
    assert len(rel_paths(scan_settings)) == first.added


# ------------------------------------------------- an incomplete walk deletes nothing


def title_row(settings: Settings, library: str, name: str) -> sqlite3.Row:
    return query(
        settings,
        "SELECT * FROM titles WHERE library_id = ? AND name = ?",
        (library, name),
    )[0]


def test_a_missing_root_keeps_an_auto_discovered_catalogue(
    sample_library: SampleLibrary, data_dir: Path
) -> None:
    """With no `paperstand.yml`, an absent mount discovers nothing — and must delete nothing."""
    settings = quiet_settings(sample_library.root, data_dir)
    first = scan_once(settings)
    assert first.added > 0
    libraries_before = len(query(settings, "SELECT id FROM libraries"))
    shutil.rmtree(sample_library.root)

    result = scan_once(settings)

    assert result.status == "ok"
    assert (result.files_seen, result.added, result.updated, result.removed) == (0, 0, 0, 0)
    assert result.errors >= 1
    assert "could not be read" in (result.message or "")
    assert len(rel_paths(settings)) == first.added
    assert len(query(settings, "SELECT id FROM libraries")) == libraries_before
    assert query(settings, "SELECT id FROM titles")


@pytest.mark.skipif(
    sys.platform == "win32" or os.geteuid() == 0,
    reason="an unreadable directory needs POSIX permissions and a non-root user",
)
def test_an_unreadable_folder_keeps_the_rows_underneath_it(
    scan_settings: Settings, sample_library: SampleLibrary
) -> None:
    scan_once(scan_settings)
    before = rel_paths(scan_settings)
    locked = sample_library.path("Magazines/Orizzonte")
    hidden = [path for path in before if path.startswith("Magazines/Orizzonte/")]
    assert hidden

    locked.chmod(0o000)
    try:
        result = scan_once(scan_settings)
    finally:
        locked.chmod(0o755)

    assert result.status == "ok"
    assert result.removed == 0
    assert result.errors >= 1
    assert "could not be read" in (result.message or "")
    assert sorted(rel_paths(scan_settings)) == sorted(before)
    assert all(has_cover(scan_settings.cache_path, issue_id(path)) for path in hidden)


def test_colliding_library_names_fail_the_scan_instead_of_misfiling(
    scan_settings: Settings,
) -> None:
    config = yaml.safe_load(scan_settings.config_path.read_text("utf-8"))
    config["libraries"].append({"name": "Zines!", "path": "Zines"})
    scan_settings.config_path.write_text(yaml.safe_dump(config, sort_keys=False), "utf-8")

    result = scan_once(scan_settings)

    assert result.status == "error"
    assert "identifier" in (result.message or "")
    assert rel_paths(scan_settings) == []


def test_a_derived_title_that_becomes_configured_is_reclassified(
    scan_settings: Settings,
) -> None:
    scan_once(scan_settings)
    before = title_row(scan_settings, "zines", "Random Mag")
    assert (before["source"], before["kind"]) == ("filename", "magazine")

    config = yaml.safe_load(scan_settings.config_path.read_text("utf-8"))
    for library in config["libraries"]:
        if library["name"] == "Zines":
            library["titles"] = ["Random Mag"]
            library["kind"] = "newspaper"
    scan_settings.config_path.write_text(yaml.safe_dump(config, sort_keys=False), "utf-8")

    scan_once(scan_settings)

    after = title_row(scan_settings, "zines", "Random Mag")
    assert after["id"] == before["id"]
    assert (after["source"], after["kind"]) == ("config", "newspaper")


@pytest.mark.skipif(
    sys.platform == "win32" or os.geteuid() == 0,
    reason="an unreadable directory needs POSIX permissions and a non-root user",
)
def test_an_unreadable_root_keeps_the_auto_discovered_libraries(
    sample_library: SampleLibrary, data_dir: Path
) -> None:
    """The root exists but will not list itself: discovery sees nothing either."""
    settings = quiet_settings(sample_library.root, data_dir)
    first = scan_once(settings)
    libraries_before = len(query(settings, "SELECT id FROM libraries"))

    sample_library.root.chmod(0o000)
    try:
        result = scan_once(settings)
    finally:
        sample_library.root.chmod(0o755)

    assert result.status == "ok"
    assert result.removed == 0
    assert result.errors >= 1
    assert len(rel_paths(settings)) == first.added
    assert len(query(settings, "SELECT id FROM libraries")) == libraries_before


def set_progress(settings: Settings, identifier: str, page: int) -> None:
    """Write a reading position without going through the API."""
    connection = sqlite3.connect(settings.db_path)
    try:
        connection.execute(
            "INSERT INTO reading_progress (issue_id, page, page_count, updated_at) "
            "VALUES (?, ?, 8, '2026-03-17T00:00:00+00:00')",
            (identifier, page),
        )
        connection.commit()
    finally:
        connection.close()


def progress_of(settings: Settings, identifier: str) -> int | None:
    rows = query(settings, "SELECT page FROM reading_progress WHERE issue_id = ?", (identifier,))
    return int(rows[0]["page"]) if rows else None


def stage_a_copy(sample_library: SampleLibrary, data_dir: Path) -> tuple[Path, Path, str, str]:
    """A file and a ``(1)`` copy of it, with only the copy in the library.

    The copy carries a duplicate suffix, so once the original is back the
    original wins and the copy becomes the duplicate — which is exactly the
    order of events a reader would hit: read a file, then have a better copy of
    the same issue turn up.
    """
    original = sample_library.path(A_NEWSPAPER)
    copy = original.with_name(f"{original.stem} (1){original.suffix}")
    shutil.copyfile(original, copy)
    parked = data_dir / "parked.pdf"
    shutil.move(str(original), parked)
    copy_rel = f"{A_NEWSPAPER.rsplit('/', 1)[0]}/{copy.name}"
    return original, parked, issue_id(copy_rel), issue_id(A_NEWSPAPER)


def test_a_reading_position_follows_a_copy_onto_the_issue_that_wins(
    sample_library: SampleLibrary, data_dir: Path
) -> None:
    settings = quiet_settings(sample_library.root, data_dir)
    write_sample_config(settings.config_path)
    original, parked, loser, winner = stage_a_copy(sample_library, data_dir)

    scan_once(settings)
    assert query(settings, "SELECT duplicate_of FROM issues WHERE id = ?", (loser,))[0][0] is None
    set_progress(settings, loser, 5)

    shutil.move(str(parked), original)
    scan_once(settings)

    assert query(settings, "SELECT duplicate_of FROM issues WHERE id = ?", (loser,))[0][0] == winner
    assert progress_of(settings, winner) == 5
    assert progress_of(settings, loser) is None


def test_a_position_already_set_on_the_winner_is_never_overwritten(
    sample_library: SampleLibrary, data_dir: Path
) -> None:
    """The copy keeps its own row rather than clobbering the one that stayed."""
    settings = quiet_settings(sample_library.root, data_dir)
    write_sample_config(settings.config_path)
    original, parked, loser, winner = stage_a_copy(sample_library, data_dir)

    scan_once(settings)
    set_progress(settings, loser, 5)
    set_progress(settings, winner, 3)

    shutil.move(str(parked), original)
    scan_once(settings)

    assert progress_of(settings, winner) == 3
    assert progress_of(settings, loser) == 5


# --------------------------------------------------------------------- progress


def run_with_progress(settings: Settings) -> tuple[ScanResult, list[ScanProgress]]:
    """A full scan, with every ``on_progress`` snapshot kept in order."""
    snapshots: list[ScanProgress] = []
    database = open_database(settings.db_path)
    try:
        result = Scanner(settings, database, on_progress=snapshots.append).scan()
    finally:
        database.close()
    return result, snapshots


def test_a_scan_without_a_callback_behaves_exactly_as_scan_once(
    sample_library: SampleLibrary, data_dir: Path
) -> None:
    """``on_progress`` defaults to ``None``, and nothing changes when it is."""
    settings = prepare(sample_library.root, data_dir)
    database = open_database(settings.db_path)
    try:
        result = Scanner(settings, database).scan()
    finally:
        database.close()

    assert result.status == "ok"
    assert result.added == sample_library.catalogued_files
    assert result.covers_done == sample_library.catalogued_files


def test_progress_snapshots_move_through_the_catalogue_phase_first(
    sample_library: SampleLibrary, data_dir: Path
) -> None:
    settings = prepare(sample_library.root, data_dir)

    _, snapshots = run_with_progress(settings)

    catalogue = [snap for snap in snapshots if snap.phase == "catalogue"]
    assert catalogue, "the fast phase must publish at least one snapshot"
    assert catalogue[0].files_seen == 0
    assert catalogue[0].added == 0
    assert all(snap.covers_total is None for snap in catalogue)
    # Every snapshot published inside the fast phase comes before every one
    # published inside the slow phase: the two never interleave.
    phases = [snap.phase for snap in snapshots]
    assert phases == sorted(phases, key=lambda phase: phase != "catalogue")


def test_progress_counters_never_go_backwards(
    sample_library: SampleLibrary, data_dir: Path
) -> None:
    settings = prepare(sample_library.root, data_dir)

    _, snapshots = run_with_progress(settings)

    for previous, current in itertools.pairwise(snapshots):
        assert current.files_seen >= previous.files_seen
        assert current.added >= previous.added
        assert current.updated >= previous.updated
        assert current.errors >= previous.errors
        if previous.phase == "covers" and current.phase == "covers":
            assert current.covers_done >= previous.covers_done


def test_covers_total_is_known_from_the_first_covers_snapshot_and_never_exceeded(
    sample_library: SampleLibrary, data_dir: Path
) -> None:
    settings = prepare(sample_library.root, data_dir)

    _, snapshots = run_with_progress(settings)

    covers = [snap for snap in snapshots if snap.phase == "covers"]
    assert covers, "the sample library always has covers to render"
    total = covers[0].covers_total
    assert total is not None and total > 0
    for snap in covers:
        assert snap.covers_total == total
        assert snap.covers_done <= total


def test_the_last_snapshot_agrees_with_the_scan_result(
    sample_library: SampleLibrary, data_dir: Path
) -> None:
    result, snapshots = run_with_progress(settings=prepare(sample_library.root, data_dir))

    last = snapshots[-1]
    assert last.scan_id == result.scan_id
    assert last.files_seen == result.files_seen
    assert last.added == result.added
    assert last.updated == result.updated
    assert last.removed == result.removed
    assert last.errors == result.errors
    assert last.covers_done == result.covers_done


def test_a_failed_cover_still_lets_the_bar_reach_the_total(
    sample_library: SampleLibrary, data_dir: Path
) -> None:
    """``covers_done`` alone stalls short of the total; add ``covers_failed``."""
    broken = sample_library.path("Zines/Broken_Mag_March_2026.pdf")
    broken.write_bytes(b"not a PDF, not even close\n" * 40)

    result, snapshots = run_with_progress(prepare(sample_library.root, data_dir))

    assert result.errors == 1
    covers = [snap for snap in snapshots if snap.phase == "covers"]
    assert covers
    total = covers[0].covers_total
    assert total is not None
    last = covers[-1]
    assert last.covers_failed == 1
    # covers_done counts successes only, so it still equals the finished
    # scan's own covers_done — the bar and the "N of M" count next to it are
    # what add covers_failed back in.
    assert last.covers_done == result.covers_done
    assert last.covers_done + last.covers_failed == total


def test_started_at_is_read_from_the_scans_row_not_regenerated(
    sample_library: SampleLibrary, data_dir: Path
) -> None:
    """``run`` reads the timestamp ``begin`` wrote rather than taking a new one.

    Deterministic, unlike a timing-based test: ``started_at`` is truncated to
    the second, so two independent ``utc_now()`` calls a few milliseconds
    apart would agree unless they happened to straddle a second boundary —
    reading the same row instead makes agreement certain rather than likely.
    """
    settings = prepare(sample_library.root, data_dir)
    database = open_database(settings.db_path)
    try:
        scanner = Scanner(settings, database)
        scan_id = scanner.begin()
        expected = scanner.started_at(scan_id)

        snapshots: list[ScanProgress] = []
        scanner.on_progress = snapshots.append
        scanner.run(scan_id)
    finally:
        database.close()

    assert snapshots
    assert all(snap.started_at == expected for snap in snapshots)


def test_the_final_catalogue_snapshot_counts_the_unreadable_path_error(
    sample_library: SampleLibrary, data_dir: Path
) -> None:
    """An incomplete walk's synthetic error reaches the last snapshot too.

    Nothing changed on disk, so the second scan's slow phase has no cover
    left to render and publishes nothing — the last snapshot the scan
    publishes at all is the fast phase's own, and it must already carry the
    error the walk could not avoid.
    """
    settings = prepare(sample_library.root, data_dir)
    scan_once(settings)  # first pass: catalogue and render every cover

    locked = sample_library.path("Magazines/Orizzonte")
    locked.chmod(0o000)
    try:
        result, snapshots = run_with_progress(settings)
    finally:
        locked.chmod(0o755)

    assert result.status == "ok"
    assert result.errors >= 1
    assert not any(snap.phase == "covers" for snap in snapshots)
    last = snapshots[-1]
    assert last.phase == "catalogue"
    assert last.errors == result.errors


# ------------------------------------------------------------- declared publications


def test_declared_rows_carry_the_publications_metadata(
    scanned: tuple[Settings, ScanResult, SampleLibrary],
) -> None:
    settings, _, _ = scanned
    row = query(
        settings,
        "SELECT source, kind, slug, frequency, language, issue_key, supplements FROM titles "
        "WHERE library_id = 'newspapers' AND name = 'Corriere del Ponte'",
    )[0]

    assert row["source"] == "publication"
    assert row["kind"] == "newspaper"
    assert row["slug"] == "corriere-del-ponte"
    assert row["frequency"] == "daily"
    assert row["language"] == "it"
    assert row["issue_key"] == "date"
    assert row["supplements"] == '["Weekend"]'


def test_weekend_and_speciale_are_not_duplicates_of_the_plain_issue(
    scanned: tuple[Settings, ScanResult, SampleLibrary],
) -> None:
    settings, _, _ = scanned
    rows = {
        str(row["rel_path"]): row["duplicate_of"]
        for row in query(
            settings,
            "SELECT rel_path, duplicate_of FROM issues "
            "WHERE rel_path LIKE 'Newspapers/Corriere del Ponte/%'",
        )
    }
    assert len(rows) == 3
    assert all(duplicate_of is None for duplicate_of in rows.values())


def test_the_valdora_files_share_the_configured_title_and_upgrade_it(
    scanned: tuple[Settings, ScanResult, SampleLibrary],
) -> None:
    """The declared folder differs from the declared title: the file still
    joins the row the date-folder files already feed — a canonical name
    migrates a title one file at a time, not by starting a new one — and,
    once processed, the row itself says "publication" with a parent_slug."""
    settings, _, _ = scanned
    title = query(
        settings,
        "SELECT id, source, parent_slug FROM titles WHERE library_id = 'newspapers' "
        "AND name = 'La Gazzetta del Lago Valdora'",
    )[0]
    rel_paths_for_title = [
        str(row["rel_path"])
        for row in query(settings, "SELECT rel_path FROM issues WHERE title_id = ?", (title["id"],))
    ]
    declared_prefix = "Newspapers/La Gazzetta del Lago (Valdora)/"
    declared = [path for path in rel_paths_for_title if path.startswith(declared_prefix)]
    dated = [path for path in rel_paths_for_title if not path.startswith(declared_prefix)]

    assert declared, "the declared file did not join the configured title"
    assert dated, "the date-folder files should still be under the same title"
    assert title["source"] == "publication"
    assert title["parent_slug"] == "la-gazzetta-del-lago"


def test_bright_meadows_is_declared_not_unsorted(
    scanned: tuple[Settings, ScanResult, SampleLibrary],
) -> None:
    """No `titles:` entry names it in the configured Magazines library — only
    the declaration keeps it out of Unsorted."""
    settings, _, _ = scanned
    row = query(
        settings,
        "SELECT t.name, t.source, i.volume FROM issues i JOIN titles t ON t.id = i.title_id "
        "WHERE i.rel_path LIKE 'Magazines/Bright Meadows/%'",
    )[0]

    assert row["name"] == "Bright Meadows"
    assert row["source"] == "publication"
    assert row["volume"] == 2024


def test_an_invalid_publication_yml_warns_naming_the_file_and_the_key(
    sample_library: SampleLibrary, data_dir: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Circuito's own file stays exactly as it always has: the warning is the
    only visible effect, and it costs the scan no error."""
    settings = prepare(sample_library.root, data_dir)
    with caplog.at_level(logging.WARNING, logger="paperstand"):
        result = scan_once(settings)

    assert result.status == "ok"
    assert result.errors == 0
    matches = [
        record.message
        for record in caplog.records
        if record.levelno >= logging.WARNING
        and "Circuito" in record.message
        and "frequenzy" in record.message
    ]
    assert matches, "expected a warning naming the invalid yml's file and its offending key"
    unsorted = {
        str(row["derived_title"])
        for row in query(
            settings,
            "SELECT i.derived_title FROM issues i JOIN titles t ON t.id = i.title_id "
            "WHERE t.name = 'Unsorted' AND i.library_id = 'magazines'",
        )
    }
    assert "Circuito" in unsorted


def test_editing_a_publication_yml_reparses_without_rendering_a_cover(
    scan_settings: Settings, sample_library: SampleLibrary, monkeypatch: pytest.MonkeyPatch
) -> None:
    scan_once(scan_settings)
    rel_path = str(
        query(scan_settings, "SELECT rel_path FROM issues WHERE variant = 'Weekend'")[0]["rel_path"]
    )
    before = query(scan_settings, "SELECT matched_rule FROM issues WHERE rel_path = ?", (rel_path,))
    assert "variant:declared" in str(before[0]["matched_rule"])

    yml_path = sample_library.path("Newspapers/Corriere del Ponte/publication.yml")
    edited = yml_path.read_text("utf-8").replace("supplements: [Weekend]", "supplements: []")
    yml_path.write_text(edited, "utf-8")

    def never(*args: object, **kwargs: object) -> object:
        raise AssertionError("a cover was rendered while only a publication.yml changed")

    monkeypatch.setattr("paperstand.scanner.scanner.render_cover", never)
    result = scan_once(scan_settings)

    assert result.status == "ok"
    assert result.added == 0
    after = query(scan_settings, "SELECT matched_rule FROM issues WHERE rel_path = ?", (rel_path,))
    assert "variant:undeclared" in str(after[0]["matched_rule"])


def test_removing_a_publication_yml_reverts_to_the_configured_title(
    scan_settings: Settings, sample_library: SampleLibrary, monkeypatch: pytest.MonkeyPatch
) -> None:
    scan_once(scan_settings)
    rel_path = str(
        query(
            scan_settings,
            "SELECT rel_path FROM issues WHERE rel_path LIKE "
            "'Newspapers/Corriere del Ponte/%' AND variant IS NULL",
        )[0]["rel_path"]
    )
    before = query(scan_settings, "SELECT matched_rule FROM issues WHERE rel_path = ?", (rel_path,))
    assert "title:publication" in str(before[0]["matched_rule"])

    sample_library.path("Newspapers/Corriere del Ponte/publication.yml").unlink()

    def never(*args: object, **kwargs: object) -> object:
        raise AssertionError("a cover was rendered while only a publication.yml was removed")

    monkeypatch.setattr("paperstand.scanner.scanner.render_cover", never)
    result = scan_once(scan_settings)

    assert result.status == "ok"
    assert result.added == 0
    after = query(scan_settings, "SELECT matched_rule FROM issues WHERE rel_path = ?", (rel_path,))
    assert "title:config" in str(after[0]["matched_rule"])


def test_issue_key_number_groups_same_numbered_issues_and_never_numberless_ones(
    tmp_path: Path,
) -> None:
    """A title declared `issue_key: number`: two files sharing a number are
    duplicates of one another no matter their dates, but two files that both
    lack a number are never folded together just because neither has one —
    a missing key component falls back to the full key."""
    root = tmp_path / "library"
    folder = root / "Zines" / "Weekly"
    folder.mkdir(parents=True)
    (folder / "publication.yml").write_text("issue_key: number\n", encoding="utf-8")
    (folder / "Weekly - 2026-01-01 - n1.pdf").write_bytes(b"%PDF-1.7\n" + b"a" * 200)
    (folder / "Weekly - 2026-02-01 - n1.pdf").write_bytes(b"%PDF-1.7\n" + b"b" * 100)
    (folder / "Weekly - 2026-03-01.pdf").write_bytes(b"%PDF-1.7\n" + b"c" * 200)
    (folder / "Weekly - 2026-04-01.pdf").write_bytes(b"%PDF-1.7\n" + b"d" * 200)

    settings = quiet_settings(root, tmp_path / "data")
    result = scan_once(settings)

    assert result.status == "ok"
    rows = {
        str(row["rel_path"]): (str(row["id"]), row["duplicate_of"])
        for row in query(settings, "SELECT id, rel_path, duplicate_of FROM issues")
    }
    n1a = "Zines/Weekly/Weekly - 2026-01-01 - n1.pdf"
    n1b = "Zines/Weekly/Weekly - 2026-02-01 - n1.pdf"
    plain_a = "Zines/Weekly/Weekly - 2026-03-01.pdf"
    plain_b = "Zines/Weekly/Weekly - 2026-04-01.pdf"

    # Same number, different dates: one wins, the other is its duplicate.
    id_a, dup_a = rows[n1a]
    id_b, dup_b = rows[n1b]
    assert (dup_a is None) != (dup_b is None)
    winner_id = id_a if dup_a is None else id_b
    loser_dup = dup_b if dup_a is None else dup_a
    assert loser_dup == winner_id

    # Both numberless: never grouped together on that basis alone.
    assert rows[plain_a][1] is None
    assert rows[plain_b][1] is None


def test_a_scan_never_writes_inside_the_library(
    sample_library: SampleLibrary, data_dir: Path
) -> None:
    before = _fingerprint(sample_library.root)
    assert before, "the sample library fixture produced no files to check"
    settings = prepare(sample_library.root, data_dir)

    result = scan_once(settings)

    assert result.status == "ok"
    assert _fingerprint(sample_library.root) == before


def _fingerprint(root: Path) -> dict[str, tuple[int, bytes]]:
    """Every file under ``root``, keyed by path, to its mtime and its bytes."""
    fingerprints: dict[str, tuple[int, bytes]] = {}
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            path = Path(dirpath) / name
            fingerprints[str(path)] = (path.stat().st_mtime_ns, path.read_bytes())
    return fingerprints
