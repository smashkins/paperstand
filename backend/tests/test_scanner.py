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

import pymupdf
import pytest
import yaml
from PIL import Image

from paperstand.config import Settings
from paperstand.db import open_database
from paperstand.scanner.covers import (
    CoverError,
    CoverResult,
    cover_paths,
    has_cover,
    page_cache_dir,
    render_cover,
)
from paperstand.scanner.hashing import content_hash
from paperstand.scanner.scanner import Scanner, ScanProgress, ScanResult, scan_once
from tests.conftest import SampleLibrary, quiet_settings, sample_issue_id, write_sample_config

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
    # Every file is new, so the hashing pre-pass reads every one of them.
    assert result.hashed == library.catalogued_files
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
    identifier = sample_issue_id(sample_library.root, A_NEWSPAPER)
    assert has_cover(scan_settings.cache_path, identifier)
    sample_library.path(A_NEWSPAPER).unlink()

    result = scan_once(scan_settings)

    assert result.removed == 1
    assert A_NEWSPAPER not in rel_paths(scan_settings)
    assert not has_cover(scan_settings.cache_path, identifier)


def test_a_changed_file_is_a_replacement_with_a_new_id_and_no_progress(
    scan_settings: Settings,
    sample_library: SampleLibrary,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Content identity, no exception: different bytes at the same path is a
    new issue, not the old one re-rendered. The old id's cache and reading
    position are gone with it; the new id starts with neither."""
    scan_once(scan_settings)
    old_identifier = sample_issue_id(sample_library.root, A_NEWSPAPER)
    set_progress(scan_settings, old_identifier, 3)
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
    new_identifier = sample_issue_id(sample_library.root, A_NEWSPAPER)

    assert new_identifier != old_identifier
    assert (result.added, result.updated, result.removed) == (1, 0, 1)
    assert rendered == [new_identifier]
    assert not query(scan_settings, "SELECT id FROM issues WHERE id = ?", (old_identifier,))
    row = query(
        scan_settings, "SELECT size, cover_status FROM issues WHERE id = ?", (new_identifier,)
    )[0]
    assert row["size"] == info.st_size
    assert row["cover_status"] == "ok"
    assert has_cover(scan_settings.cache_path, new_identifier)
    assert not has_cover(scan_settings.cache_path, old_identifier)
    # The old position is orphaned, not deleted — where every orphaned
    # position stays, in case those exact bytes ever come back — and the new
    # issue starts with none of its own.
    assert progress_of(scan_settings, old_identifier) == 3
    assert progress_of(scan_settings, new_identifier) is None


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


# --------------------------------------------------------------- content identity


def test_a_rename_within_the_folder_keeps_identity(
    scan_settings: Settings, sample_library: SampleLibrary
) -> None:
    scan_once(scan_settings)
    identifier = sample_issue_id(sample_library.root, A_NEWSPAPER)
    set_progress(scan_settings, identifier, 3)
    cover, _ = cover_paths(scan_settings.cache_path, identifier)
    cover_bytes = cover.read_bytes()
    pages = page_cache_dir(scan_settings.cache_path, identifier)
    pages.mkdir(parents=True, exist_ok=True)
    (pages / "1-900.webp").write_bytes(b"cached page")

    original = sample_library.path(A_NEWSPAPER)
    renamed = original.with_name("Corriere_del_Ponte_renamed.pdf")
    original.rename(renamed)

    result = scan_once(scan_settings)

    assert (result.added, result.removed) == (0, 0)
    new_rel_path = f"{A_NEWSPAPER.rsplit('/', 1)[0]}/{renamed.name}"
    row = query(scan_settings, "SELECT id, rel_path FROM issues WHERE id = ?", (identifier,))[0]
    assert row["rel_path"] == new_rel_path
    assert cover.read_bytes() == cover_bytes
    assert (pages / "1-900.webp").is_file()
    assert progress_of(scan_settings, identifier) == 3


def test_a_move_to_another_folder_keeps_identity_and_reparses_the_title(
    tmp_path: Path,
) -> None:
    """A move is the same issue wherever it lands: its title comes from
    wherever it lands now, exactly as a fresh file's would."""
    root = tmp_path / "library"
    first_folder = root / "Zines" / "First Folder"
    first_folder.mkdir(parents=True)
    pdf_path = first_folder / "issue.pdf"
    document = pymupdf.open()
    document.new_page()
    document.save(pdf_path)
    document.close()

    title_query = (
        "SELECT i.id AS id, i.rel_path AS rel_path, t.name AS title_name "
        "FROM issues i JOIN titles t ON t.id = i.title_id"
    )
    settings = quiet_settings(root, tmp_path / "data")
    first_result = scan_once(settings)
    assert first_result.added == 1
    before = query(settings, title_query)[0]
    identifier = str(before["id"])
    assert before["title_name"] == "First Folder"
    set_progress(settings, identifier, 2)
    cover, _ = cover_paths(settings.cache_path, identifier)
    cover_bytes = cover.read_bytes()

    second_folder = root / "Zines" / "Second Folder"
    second_folder.mkdir(parents=True)
    new_path = second_folder / "issue.pdf"
    pdf_path.rename(new_path)

    result = scan_once(settings)

    assert (result.added, result.removed) == (0, 0)
    assert result.updated == 1
    row = query(settings, f"{title_query} WHERE i.id = ?", (identifier,))[0]
    assert row["rel_path"] == "Zines/Second Folder/issue.pdf"
    assert row["title_name"] == "Second Folder"
    assert cover.read_bytes() == cover_bytes
    assert progress_of(settings, identifier) == 2


def test_a_touch_reports_updated_and_renders_nothing(
    scan_settings: Settings, sample_library: SampleLibrary, monkeypatch: pytest.MonkeyPatch
) -> None:
    scan_once(scan_settings)
    identifier = sample_issue_id(sample_library.root, A_NEWSPAPER)
    cover, _ = cover_paths(scan_settings.cache_path, identifier)
    cover_mtime = cover.stat().st_mtime_ns

    target = sample_library.path(A_NEWSPAPER)
    payload = target.read_bytes()
    future = target.stat().st_mtime + 120
    os.utime(target, (future, future))
    assert target.read_bytes() == payload  # only the stat moved

    def never(*args: object, **kwargs: object) -> object:
        raise AssertionError("a touch must never render a cover")

    monkeypatch.setattr("paperstand.scanner.scanner.render_cover", never)
    result = scan_once(scan_settings)

    assert (result.added, result.removed) == (0, 0)
    assert result.updated == 1
    assert cover.stat().st_mtime_ns == cover_mtime
    row = query(scan_settings, "SELECT mtime_ns FROM issues WHERE id = ?", (identifier,))[0]
    assert row["mtime_ns"] == target.stat().st_mtime_ns


def test_two_files_swapping_paths_keep_their_identities(
    scan_settings: Settings, sample_library: SampleLibrary, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A path swap (or a rotation) looks, to pass one, like two independent
    replacements: each old row is deleted the moment its path is found to
    hold different bytes. Pass two must still tell that apart from an actual
    replacement — the bytes it lost are exactly the bytes another queued file
    gained — and reunite each with its old id, cache and reading position
    rather than adding two fresh rows and removing two old ones."""
    scan_once(scan_settings)
    rows = query(
        scan_settings,
        "SELECT i.id, i.rel_path FROM issues i JOIN titles t ON t.id = i.title_id "
        "WHERE t.name = 'Corriere del Ponte' ORDER BY i.rel_path LIMIT 2",
    )
    assert len(rows) == 2
    id_a, rel_a = str(rows[0]["id"]), str(rows[0]["rel_path"])
    id_b, rel_b = str(rows[1]["id"]), str(rows[1]["rel_path"])
    set_progress(scan_settings, id_a, 3)
    set_progress(scan_settings, id_b, 7)
    cover_a, _ = cover_paths(scan_settings.cache_path, id_a)
    cover_b, _ = cover_paths(scan_settings.cache_path, id_b)
    mtime_a = cover_a.stat().st_mtime_ns
    mtime_b = cover_b.stat().st_mtime_ns

    path_a = sample_library.path(rel_a)
    path_b = sample_library.path(rel_b)
    temp = path_a.with_name("swap-temp.pdf")
    path_a.rename(temp)
    path_b.rename(path_a)
    temp.rename(path_b)

    def never(*args: object, **kwargs: object) -> object:
        raise AssertionError("a path swap must never render a cover")

    monkeypatch.setattr("paperstand.scanner.scanner.render_cover", never)
    result = scan_once(scan_settings)

    assert (result.added, result.removed) == (0, 0)
    now_at_a = query(scan_settings, "SELECT id FROM issues WHERE rel_path = ?", (rel_a,))[0]
    now_at_b = query(scan_settings, "SELECT id FROM issues WHERE rel_path = ?", (rel_b,))[0]
    assert now_at_a["id"] == id_b
    assert now_at_b["id"] == id_a
    assert progress_of(scan_settings, id_a) == 3
    assert progress_of(scan_settings, id_b) == 7
    assert cover_a.stat().st_mtime_ns == mtime_a
    assert cover_b.stat().st_mtime_ns == mtime_b
    assert has_cover(scan_settings.cache_path, id_a)
    assert has_cover(scan_settings.cache_path, id_b)


def _make_legacy(settings: Settings) -> dict[str, str]:
    """Rewrite every catalogued issue's id to a ``legacy-<n>`` placeholder
    with no content hash, renaming its cache files to match — a stand-in for
    a database written before schema 3, without running a migration on it."""
    connection = sqlite3.connect(settings.db_path)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute("SELECT id, rel_path FROM issues ORDER BY rel_path").fetchall()
        mapping = {str(row["id"]): f"legacy-{index}" for index, row in enumerate(rows)}
        with connection:
            # Cleared before any id moves: SQLite refuses to change a primary
            # key another row's `duplicate_of` still references.
            connection.execute("UPDATE issues SET duplicate_of = NULL")
            for old, new in mapping.items():
                connection.execute(
                    "UPDATE issues SET id = ?, content_hash = NULL WHERE id = ?", (new, old)
                )
    finally:
        connection.close()

    for old, new in mapping.items():
        old_cover, old_thumb = cover_paths(settings.cache_path, old)
        new_cover, new_thumb = cover_paths(settings.cache_path, new)
        for source, target in ((old_cover, new_cover), (old_thumb, new_thumb)):
            if source.is_file():
                target.parent.mkdir(parents=True, exist_ok=True)
                source.rename(target)
        old_pages = page_cache_dir(settings.cache_path, old)
        if old_pages.is_dir():
            new_pages = page_cache_dir(settings.cache_path, new)
            new_pages.parent.mkdir(parents=True, exist_ok=True)
            old_pages.rename(new_pages)
    return mapping


def test_a_legacy_row_whose_file_changed_meanwhile_is_rendered_again(
    scan_settings: Settings, sample_library: SampleLibrary
) -> None:
    """A legacy row has no hash to compare against, so a file whose stat moved
    since the last pre-upgrade scan may well hold different bytes: its cover
    is rendered again, while every other legacy row keeps the cover it had."""
    scan_once(scan_settings)
    _make_legacy(scan_settings)
    target = sample_library.path(A_NEWSPAPER)
    with target.open("ab") as handle:
        handle.write(b"\n%% changed before the upgrade scan\n")

    result = scan_once(scan_settings)

    assert result.status == "ok"
    assert result.hashed == sample_library.catalogued_files
    assert result.covers_done == 1
    identifier = sample_issue_id(sample_library.root, A_NEWSPAPER)
    assert has_cover(scan_settings.cache_path, identifier)
    row = query(scan_settings, "SELECT cover_status FROM issues WHERE id = ?", (identifier,))[0]
    assert row["cover_status"] == "ok"


def test_a_legacy_catalogue_is_backfilled_once_and_stays_quick_after(
    scan_settings: Settings, sample_library: SampleLibrary, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The one-time migration: every legacy row gets the id its content always
    implied, its cache is moved rather than re-rendered, a reading position
    and an already-resolved duplicate pair both survive the rewrite, and a
    second scan — with nothing left to hash — is as quick as any other
    unchanged one."""
    first = scan_once(scan_settings)
    assert first.added == sample_library.catalogued_files
    duplicate_pairs = {
        str(row["rel_path"]): str(row["duplicate_of"])
        for row in query(
            scan_settings,
            "SELECT rel_path, duplicate_of FROM issues WHERE duplicate_of IS NOT NULL",
        )
    }
    assert duplicate_pairs, "the sample library always has at least one dedup-suffixed copy"
    loser_rel, winner_id_before = next(iter(duplicate_pairs.items()))
    winner_rel = str(
        query(scan_settings, "SELECT rel_path FROM issues WHERE id = ?", (winner_id_before,))[0][
            "rel_path"
        ]
    )

    mapping = _make_legacy(scan_settings)
    loser_legacy = mapping[sample_issue_id(sample_library.root, loser_rel)]
    winner_legacy = mapping[sample_issue_id(sample_library.root, winner_rel)]
    progress_legacy = mapping[sample_issue_id(sample_library.root, A_NEWSPAPER)]
    set_progress(scan_settings, progress_legacy, 4)

    # Re-established on the legacy ids: exactly what an upgraded catalogue
    # would hold, and the one case `_rewrite_id` has to clear a `duplicate_of`
    # for before it can rename the row it points at.
    connection = sqlite3.connect(scan_settings.db_path)
    try:
        with connection:
            connection.execute(
                "UPDATE issues SET duplicate_of = ? WHERE id = ?", (winner_legacy, loser_legacy)
            )
    finally:
        connection.close()

    def never(*args: object, **kwargs: object) -> object:
        raise AssertionError("a legacy row's bytes did not change; nothing should be rendered")

    monkeypatch.setattr("paperstand.scanner.scanner.render_cover", never)
    result = scan_once(scan_settings)

    assert result.status == "ok"
    assert result.added == 0
    assert result.hashed == sample_library.catalogued_files
    assert result.updated == sample_library.catalogued_files

    for row in query(scan_settings, "SELECT id, rel_path, content_hash FROM issues"):
        expected = sample_issue_id(sample_library.root, str(row["rel_path"]))
        assert str(row["id"]) == expected
        assert row["content_hash"] is not None and len(row["content_hash"]) == 64

    new_progress_id = sample_issue_id(sample_library.root, A_NEWSPAPER)
    assert progress_of(scan_settings, new_progress_id) == 4
    assert progress_of(scan_settings, progress_legacy) is None
    assert has_cover(scan_settings.cache_path, new_progress_id)

    new_winner_id = sample_issue_id(sample_library.root, winner_rel)
    after = query(
        scan_settings, "SELECT duplicate_of FROM issues WHERE rel_path = ?", (loser_rel,)
    )[0]
    assert after["duplicate_of"] == new_winner_id

    started = time.perf_counter()

    def unexpected(*args: object, **kwargs: object) -> str:
        raise AssertionError("a fully hashed catalogue must never rehash a file")

    monkeypatch.setattr("paperstand.scanner.scanner.content_hash", unexpected)
    again = scan_once(scan_settings)
    elapsed = time.perf_counter() - started

    assert (again.added, again.updated, again.removed, again.hashed) == (0, 0, 0, 0)
    assert elapsed < 0.5


def test_hashing_a_legacy_backfill_never_holds_the_write_lock(
    scan_settings: Settings, sample_library: SampleLibrary, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every hash the fast phase needs is computed before its transaction
    opens, so a whole library's worth of legacy rows can be read without ever
    holding SQLite's writer lock — a concurrent write, `PUT
    /api/issues/{id}/progress` among them, must never wait behind it."""
    scan_once(scan_settings)
    _make_legacy(scan_settings)

    calls = 0
    locked: list[str] = []

    def checked(path: Path) -> str:
        nonlocal calls
        calls += 1
        probe = sqlite3.connect(scan_settings.db_path)
        try:
            probe.execute("PRAGMA busy_timeout = 200")
            try:
                probe.execute("BEGIN IMMEDIATE")
                probe.execute("ROLLBACK")
            except sqlite3.OperationalError as error:
                locked.append(str(error))
        finally:
            probe.close()
        return content_hash(path)

    monkeypatch.setattr("paperstand.scanner.scanner.content_hash", checked)
    result = scan_once(scan_settings)

    assert calls == sample_library.catalogued_files
    assert not locked, f"the write lock was held during {len(locked)} of {calls} hash(es)"
    assert result.status == "ok"
    assert result.hashed == sample_library.catalogued_files


# ------------------------------------------------------------ two-level duplicates


def test_a_byte_identical_copy_elsewhere_is_duplicate_of_the_original(
    scan_settings: Settings, sample_library: SampleLibrary
) -> None:
    """Identity is content: a plain-named copy dropped in an unrelated
    folder is still resolved as a duplicate of the original — and needed a
    `_unique`-suffixed id of its own in the first place, since the bytes are
    identical."""
    original = sample_library.path(A_NEWSPAPER)
    copy_rel = "Zines/elsewhere.pdf"
    shutil.copyfile(original, sample_library.path(copy_rel))

    result = scan_once(scan_settings)

    assert result.status == "ok"
    original_id = sample_issue_id(sample_library.root, A_NEWSPAPER)
    copy_row = query(
        scan_settings, "SELECT id, duplicate_of FROM issues WHERE rel_path = ?", (copy_rel,)
    )[0]
    assert copy_row["id"] == f"{original_id}-2"
    assert copy_row["duplicate_of"] == original_id
    winner_row = query(
        scan_settings, "SELECT duplicate_of FROM issues WHERE id = ?", (original_id,)
    )[0]
    assert winner_row["duplicate_of"] is None


def test_a_chain_of_duplicates_is_flattened_and_progress_walks_it(tmp_path: Path) -> None:
    """A row that wins the content level can still lose the title level: the
    stored pointer always skips straight to the final winner, and a reading
    position set on the deepest loser walks the whole chain within one scan."""
    root = tmp_path / "library"
    folder = root / "Zines" / "Chain"
    folder.mkdir(parents=True)
    (folder / "publication.yml").write_text("issue_key: number\n", encoding="utf-8")
    payload = b"%PDF-1.7\n" + b"x" * 300
    (folder / "Chain - 2026-01-01 - n1.pdf").write_bytes(payload)
    (folder / "Chain - 2026-01-03 - n1.pdf").write_bytes(payload + b"a bigger, different file")

    settings = quiet_settings(root, tmp_path / "data")
    first_scan = scan_once(settings)
    assert first_scan.status == "ok"
    rows = {
        str(row["rel_path"]): dict(row)
        for row in query(settings, "SELECT rel_path, id, duplicate_of FROM issues")
    }
    first_id = str(rows["Zines/Chain/Chain - 2026-01-01 - n1.pdf"]["id"])
    bigger_id = str(rows["Zines/Chain/Chain - 2026-01-03 - n1.pdf"]["id"])
    # The number alone decides the group: the bigger file already wins it.
    assert rows["Zines/Chain/Chain - 2026-01-01 - n1.pdf"]["duplicate_of"] == bigger_id
    assert rows["Zines/Chain/Chain - 2026-01-03 - n1.pdf"]["duplicate_of"] is None

    # A byte-identical copy of the level-one loser, at a path sorting after
    # it: `_unique` gives it the next free suffix on `first`'s own id.
    second_id = f"{first_id}-2"
    set_progress(settings, second_id, 2)
    (folder / "Chain - 2026-01-02 - n1.pdf").write_bytes(payload)

    second_scan = scan_once(settings)

    assert second_scan.status == "ok"
    row = query(
        settings,
        "SELECT id, duplicate_of FROM issues WHERE rel_path = ?",
        ("Zines/Chain/Chain - 2026-01-02 - n1.pdf",),
    )[0]
    assert row["id"] == second_id
    # Flattened: the copy points straight at the title-level winner, never at
    # `first`, which only ever won the content level.
    assert row["duplicate_of"] == bigger_id
    assert progress_of(settings, bigger_id) == 2
    assert progress_of(settings, first_id) is None
    assert progress_of(settings, second_id) is None


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
    assert all(
        has_cover(scan_settings.cache_path, sample_issue_id(sample_library.root, path))
        for path in hidden
    )


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
    the same issue turn up. A trailing comment keeps the copy's bytes — and so
    its id — distinct from the original's: what makes the two duplicates is
    the date and the title they share, not identical content.
    """
    original = sample_library.path(A_NEWSPAPER)
    copy = original.with_name(f"{original.stem} (1){original.suffix}")
    shutil.copyfile(original, copy)
    with copy.open("ab") as handle:
        handle.write(b"\n% a distinct copy\n")
    copy_rel = f"{A_NEWSPAPER.rsplit('/', 1)[0]}/{copy.name}"
    loser = sample_issue_id(sample_library.root, copy_rel)
    winner = sample_issue_id(sample_library.root, A_NEWSPAPER)
    parked = data_dir / "parked.pdf"
    shutil.move(str(original), parked)
    return original, parked, loser, winner


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


def test_a_declaration_does_not_cross_into_an_explicitly_configured_nested_library(
    tmp_path: Path,
) -> None:
    """`Collection/publication.yml` belongs to the `Collection` library. A
    PDF under the explicitly configured nested library `Collection/Magazines`
    is inside the folder the walker still notices the yml in — nested
    folders inherit a declaration by construction — but `library_for`'s
    longest-prefix semantics put the file in the *other* library, and the
    declaration must not cross that boundary."""
    root = tmp_path / "library"
    (root / "Collection").mkdir(parents=True)
    (root / "Collection" / "publication.yml").write_text("id: whole-collection\n", encoding="utf-8")
    folder = root / "Collection" / "Magazines" / "Confini" / "2026"
    folder.mkdir(parents=True)
    document = pymupdf.open()
    document.new_page()
    document.save(folder / "Confini - 2026-03.pdf")
    document.close()

    data = tmp_path / "data"
    settings = quiet_settings(root, data)
    settings.config_path.parent.mkdir(parents=True, exist_ok=True)
    settings.config_path.write_text(
        yaml.safe_dump(
            {
                "libraries": [
                    {"name": "Collection", "path": "Collection", "kind": "newspaper"},
                    {
                        "name": "Collection Magazines",
                        "path": "Collection/Magazines",
                        "kind": "magazine",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    result = scan_once(settings)

    assert result.status == "ok"
    row = query(
        settings,
        "SELECT t.name AS title, i.matched_rule FROM issues i "
        "JOIN titles t ON t.id = i.title_id WHERE i.library_id = 'collection-magazines'",
    )[0]
    assert row["title"] == "Confini"
    assert "title:publication" not in str(row["matched_rule"])


def test_a_malformed_nested_yml_falls_back_to_the_valid_declaration_above_it(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """`A/publication.yml` is valid, `A/B/publication.yml` is malformed: the
    walker only ever notices the nearer file, so `A/B/x.pdf` must not simply
    lose its declaration — it is filed under `A`'s, with one warning naming
    `A/B`."""
    root = tmp_path / "library"
    folder_a = root / "Zines" / "A"
    folder_b = folder_a / "B"
    folder_b.mkdir(parents=True)
    (folder_a / "publication.yml").write_text("id: a\n", encoding="utf-8")
    (folder_b / "publication.yml").write_text("frequenzy: monthly\n", encoding="utf-8")
    document = pymupdf.open()
    document.new_page()
    document.save(folder_b / "x.pdf")
    document.close()

    settings = quiet_settings(root, tmp_path / "data")
    with caplog.at_level(logging.WARNING, logger="paperstand"):
        result = scan_once(settings)

    assert result.status == "ok"
    assert result.errors == 0
    row = query(
        settings,
        "SELECT t.name AS title, t.slug AS slug, i.matched_rule FROM issues i "
        "JOIN titles t ON t.id = i.title_id WHERE i.rel_path = 'Zines/A/B/x.pdf'",
    )[0]
    assert row["slug"] == "a"
    assert "title:publication" in str(row["matched_rule"])
    warnings = [
        record.message
        for record in caplog.records
        if record.levelno >= logging.WARNING and "Zines/A/B" in record.message
    ]
    assert len(warnings) == 1
    assert "frequenzy" in warnings[0]


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


def test_an_undecodable_publication_yml_is_a_warning_not_a_failed_scan(tmp_path: Path) -> None:
    """`path.read_text("utf-8")` raising `UnicodeDecodeError` must land the
    same way an invalid yml does: one warning, the folder left undeclared,
    the scan still `ok` with no error charged to it."""
    root = tmp_path / "library"
    folder = root / "Zines" / "Weekly"
    folder.mkdir(parents=True)
    (folder / "publication.yml").write_bytes(b"\xff\xfe")
    document = pymupdf.open()
    document.new_page()
    document.save(folder / "Weekly - 2026-01-01.pdf")
    document.close()

    settings = quiet_settings(root, tmp_path / "data")
    result = scan_once(settings)

    assert result.status == "ok"
    assert result.errors == 0


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
