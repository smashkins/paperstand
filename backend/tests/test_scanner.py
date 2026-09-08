"""The two-phase scan, against the generated sample library.

Every count asserted here is derived from the generator's own per-layout tally
(see ``tests/conftest.py``) or from the configuration the scan was given, never
typed out by hand.
"""

from __future__ import annotations

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
from paperstand.db import issue_id
from paperstand.scanner.covers import CoverError, CoverResult, cover_paths, has_cover, render_cover
from paperstand.scanner.scanner import ScanResult, scan_once
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
MAGAZINE_TITLES = {"Orizzonte", "Confini", "L'Almanacco", "Unsorted"}
ZINE_TITLES = {"Random Mag", "Something", "Circuito"}


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

    assert sources == {"Circuito": "folder", "Random Mag": "filename", "Something": "filename"}


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
    first = scan_once(scan_settings)
    assert "La Gazzetta del Lago Valdora" in title_names(scan_settings, "newspapers")
    moved = issues_in(scan_settings, "newspapers")

    config = yaml.safe_load(scan_settings.config_path.read_text("utf-8"))
    for library in config["libraries"]:
        if library["name"] == "Newspapers":
            library["titles"] = [
                title for title in library["titles"] if title != "La Gazzetta del Lago Valdora"
            ]
    scan_settings.config_path.write_text(yaml.safe_dump(config, sort_keys=False), "utf-8")

    def never(*args: object, **kwargs: object) -> object:
        raise AssertionError("a cover was rendered while only the configuration changed")

    monkeypatch.setattr("paperstand.scanner.scanner.render_cover", never)
    result = scan_once(scan_settings)

    assert (result.added, result.removed, result.covers_done) == (0, 0, 0)
    assert result.updated == first.added
    assert "La Gazzetta del Lago Valdora" not in title_names(scan_settings, "newspapers")
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
