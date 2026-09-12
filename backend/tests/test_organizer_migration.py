"""``migrate``: planning and performing an in-library move to the canonical layout.

Most scenarios run against ``catalogue_settings``: a private, already scanned
copy of the sample library, so the catalogue's content hashes are real and
match the library's own bytes exactly — the same fixture
``test_organizer_inbox.py`` uses. The move-resolution scenarios that need
precise control over what is "current" and what is "destination" (a chain, a
cycle, an occupied destination, a same-file spelling, an unexpected failure)
build a tiny, hand-planned :class:`~paperstand.organizer.migration.LibraryPlan`
against a throwaway ``tmp_path`` library instead, with ``plan_library``
monkeypatched to return it — so those scenarios never depend on how the
parser reads a file name.
"""

from __future__ import annotations

import errno
import fcntl
import io
import logging
import shutil
import time
from pathlib import Path

import pytest

from paperstand.__main__ import main
from paperstand.cache import cover_paths
from paperstand.cli.migrate import migrate
from paperstand.config import PaperstandConfig, Settings, load_config
from paperstand.db import issue_id, open_database
from paperstand.organizer.migration import (
    Collided,
    Collision,
    Duplicate,
    Failed,
    InPlace,
    LibraryPlan,
    MigrationReport,
    Move,
    Moved,
    Unplaced,
    migrate_once,
    plan_library,
)
from paperstand.organizer.mover import move_file as real_move_file
from paperstand.scanner.hashing import content_hash
from paperstand.scanner.scanner import scan_once
from paperstand.scanner.walker import Walk
from paperstand.schemas import MigrationRun
from tests.conftest import SampleLibrary
from tests.test_cli_organize import _fingerprint
from tests.test_organizer_inbox import _write_pdf

#: A movable date-folder file of the sample library: not in place, not
#: unsorted, not part of any collision.
CORRIERE_17 = "Newspapers/2026/03/17/Corriere_del_Ponte_17_Marzo_2026.pdf"
CORRIERE_17_DESTINATION = "Newspapers/Corriere del Ponte/2026/Corriere del Ponte - 2026-03-17.pdf"


@pytest.fixture
def config(catalogue_settings: Settings) -> PaperstandConfig:
    return load_config(catalogue_settings.config_path)


def _run(
    library: Path,
    config: PaperstandConfig,
    *,
    db_path: Path,
    apply: bool,
    prune_empty: bool = True,
    reports_dir: Path | None = None,
    trigger_path: Path | None = None,
) -> tuple[MigrationReport, str]:
    stream = io.StringIO()
    report = migrate_once(
        library,
        config,
        db_path=db_path,
        apply=apply,
        prune_empty=prune_empty,
        out=stream,
        reports_dir=reports_dir,
        trigger_path=trigger_path,
    )
    return report, stream.getvalue()


def _set_content_hash(db_path: Path, rel_path: str, value: str | None) -> None:
    database = open_database(db_path)
    with database.transaction() as connection:
        connection.execute(
            "UPDATE issues SET content_hash = ? WHERE rel_path = ?", (value, rel_path)
        )
    database.close()


def _delete_issue(db_path: Path, rel_path: str) -> None:
    database = open_database(db_path)
    with database.transaction() as connection:
        connection.execute("DELETE FROM issues WHERE rel_path = ?", (rel_path,))
    database.close()


# ------------------------------------------------------------------ dry run


def test_dry_run_touches_neither_the_library_nor_anything_else(
    catalogue_settings: Settings, config: PaperstandConfig
) -> None:
    before = _fingerprint(catalogue_settings.library)
    reports_dir = catalogue_settings.data / "organizer" / "migrations"
    trigger_path = catalogue_settings.data / "scan.request"

    report, text = _run(
        catalogue_settings.library,
        config,
        db_path=catalogue_settings.db_path,
        apply=False,
        reports_dir=reports_dir,
        trigger_path=trigger_path,
    )

    assert _fingerprint(catalogue_settings.library) == before
    assert report.moved > 0, "the sample library produced nothing to move"
    assert not reports_dir.exists()
    assert not trigger_path.exists()
    assert "removed empty folder" not in text
    assert "scan requested" not in text
    assert "to move" in text.splitlines()[-1]


def test_dry_run_lines_match_organize_plan_plus_the_header(
    catalogue_settings: Settings, config: PaperstandConfig
) -> None:
    """Every per-file and ``COLLISION`` line is the same computation ``organize-plan`` prints.

    A ``Move`` entry contested by a collision is the one exception: ``migrate``
    resolves and reports it as a ``collision:`` line, where ``organize-plan``
    (which never checks whether a plan is *safe* to act on) simply prints its
    would-be destination.
    """
    from paperstand.organizer.migration import _collision_reason

    plan_text_lines: list[str] = []
    plan = plan_library(catalogue_settings.library, config)
    contested = {group.destination: group for group in plan.collisions}
    for entry in plan.entries:
        if isinstance(entry, InPlace):
            plan_text_lines.append(f"{entry.rel_path} -> in place")
        elif isinstance(entry, Unplaced):
            plan_text_lines.append(f"{entry.rel_path} -> unsorted: {entry.reason}")
        else:
            group = contested.get(entry.destination)
            if group is not None:
                plan_text_lines.append(f"{entry.rel_path} -> collision: {_collision_reason(group)}")
            else:
                plan_text_lines.append(f"{entry.rel_path} -> {entry.destination}")
    if plan.collisions:
        plan_text_lines.append("")
        for group in plan.collisions:
            plan_text_lines.append(f"COLLISION {group.destination}")
            for source in group.sources:
                plan_text_lines.append(f"  {source}")

    _report, text = _run(
        catalogue_settings.library, config, db_path=catalogue_settings.db_path, apply=False
    )
    lines = text.splitlines()
    # Drop the header (four lines + a blank one) and the final blank + summary.
    body = lines[5:-2]

    assert body == plan_text_lines


# -------------------------------------------------------------------- apply


def test_apply_moves_every_movable_file_and_keeps_everything_else_by_fingerprint(
    catalogue_settings: Settings, config: PaperstandConfig
) -> None:
    plan = plan_library(catalogue_settings.library, config)
    untouched_rel_paths = {
        entry.rel_path for entry in plan.entries if isinstance(entry, (InPlace, Unplaced))
    } | {source for group in plan.collisions for source in group.sources}
    before_fingerprint = _fingerprint(catalogue_settings.library)
    untouched_before = {
        path: value
        for path, value in before_fingerprint.items()
        if any(path.endswith(rel_path) for rel_path in untouched_rel_paths)
    }
    assert len(untouched_before) == len(untouched_rel_paths)

    report, _text = _run(
        catalogue_settings.library, config, db_path=catalogue_settings.db_path, apply=True
    )

    assert report.moved == len(plan.movable())
    after_fingerprint = _fingerprint(catalogue_settings.library)
    for path, value in untouched_before.items():
        assert after_fingerprint[path] == value


def test_apply_every_movable_files_bytes_survive_at_its_destination(
    catalogue_settings: Settings, config: PaperstandConfig
) -> None:
    plan = plan_library(catalogue_settings.library, config)
    before_bytes = {
        move.rel_path: (catalogue_settings.library / move.rel_path).read_bytes()
        for move in plan.movable()
    }

    _run(catalogue_settings.library, config, db_path=catalogue_settings.db_path, apply=True)

    for move in plan.movable():
        destination = catalogue_settings.library / move.destination
        assert destination.is_file()
        assert destination.read_bytes() == before_bytes[move.rel_path]
        assert not (catalogue_settings.library / move.rel_path).exists()


def test_apply_removes_emptied_date_folder_chains_but_keeps_library_and_declared_folders(
    catalogue_settings: Settings, config: PaperstandConfig
) -> None:
    report, _text = _run(
        catalogue_settings.library, config, db_path=catalogue_settings.db_path, apply=True
    )

    assert report.removed_folders
    assert any(path.startswith("Newspapers/2026/03/") for path in report.removed_folders)

    # The configured library roots are never removed.
    assert (catalogue_settings.library / "Newspapers").is_dir()
    assert (catalogue_settings.library / "Magazines").is_dir()

    # Every declared publication folder — still holding its own publication.yml —
    # is never emptied by a migration that moves its own files onto themselves.
    for declared in (
        "Newspapers/Corriere del Ponte",
        "Newspapers/La Gazzetta del Lago (Valdora)",
        "Magazines/Bright Meadows",
        "Magazines/Circuito",
    ):
        assert (catalogue_settings.library / declared / "publication.yml").is_file()

    # A folder holding a stray non-PDF (the sample library's own noise for
    # "today") is kept, and that file is untouched.
    noise = catalogue_settings.library / "Newspapers/2026/03/17/.hidden.pdf"
    assert noise.is_file()
    assert "Newspapers/2026/03/17" not in report.removed_folders


def test_keep_empty_folders_leaves_every_folder_in_place(
    catalogue_settings: Settings, config: PaperstandConfig
) -> None:
    report, _text = _run(
        catalogue_settings.library,
        config,
        db_path=catalogue_settings.db_path,
        apply=True,
        prune_empty=False,
    )

    assert report.removed_folders == []
    assert (catalogue_settings.library / "Newspapers/2026/03/04").is_dir()


# --------------------------------------------------------------- idempotence


def test_a_second_apply_moves_nothing_and_writes_no_report(
    catalogue_settings: Settings, config: PaperstandConfig
) -> None:
    reports_dir = catalogue_settings.data / "organizer" / "migrations"
    trigger_path = catalogue_settings.data / "scan.request"
    _run(
        catalogue_settings.library,
        config,
        db_path=catalogue_settings.db_path,
        apply=True,
        reports_dir=reports_dir,
        trigger_path=trigger_path,
    )
    assert reports_dir.is_dir()
    first_run_reports = sorted(reports_dir.iterdir())
    trigger_path.unlink()

    report, text = _run(
        catalogue_settings.library,
        config,
        db_path=catalogue_settings.db_path,
        apply=True,
        reports_dir=reports_dir,
        trigger_path=trigger_path,
    )

    assert report.moved == 0
    plan = plan_library(catalogue_settings.library, config)
    unsorted_and_collision_rel_paths = {
        entry.rel_path for entry in plan.entries if isinstance(entry, Unplaced)
    } | {source for group in plan.collisions for source in group.sources}
    for entry in plan.entries:
        if entry.rel_path in unsorted_and_collision_rel_paths:
            continue
        assert isinstance(entry, InPlace), f"{entry.rel_path} was expected in place"
    assert sorted(reports_dir.iterdir()) == first_run_reports
    assert not trigger_path.exists()
    assert "0 moved" in text.splitlines()[-1]


# ----------------------------------------------------------- catalogue continuity


def test_a_rescan_after_apply_keeps_ids_covers_and_reading_progress(
    catalogue_settings: Settings, config: PaperstandConfig
) -> None:
    plan = plan_library(catalogue_settings.library, config)
    database = open_database(catalogue_settings.db_path)
    a_move = next(
        move
        for move in plan.movable()
        if database.connection.execute(
            "SELECT cover_status FROM issues WHERE rel_path = ?", (move.rel_path,)
        ).fetchone()["cover_status"]
        == "ok"
    )
    digest = content_hash(catalogue_settings.library / a_move.rel_path)
    moved_id = issue_id(digest)

    before_row = database.connection.execute(
        "SELECT cover_status FROM issues WHERE id = ?", (moved_id,)
    ).fetchone()
    assert before_row is not None
    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO reading_progress (issue_id, page, page_count, updated_at) "
            "VALUES (?, 3, 10, '2026-03-17T00:00:00+00:00')",
            (moved_id,),
        )
    database.close()

    cover_path, _thumbnail_path = cover_paths(catalogue_settings.cache_path, moved_id)
    assert cover_path.is_file(), "the sample scan produced no cover to check"
    cover_mtime_before = cover_path.stat().st_mtime_ns

    _run(catalogue_settings.library, config, db_path=catalogue_settings.db_path, apply=True)

    settings = Settings(
        library=catalogue_settings.library,
        data=catalogue_settings.data,
        config=None,
        static=None,
        scan_on_start=False,
        scan_interval=0,
    )
    result = scan_once(settings)
    assert result.status == "ok"
    assert result.added == 0
    assert result.removed == 0

    database = open_database(catalogue_settings.db_path)
    after_row = database.connection.execute(
        "SELECT rel_path, cover_status FROM issues WHERE id = ?", (moved_id,)
    ).fetchone()
    progress_row = database.connection.execute(
        "SELECT page FROM reading_progress WHERE issue_id = ?", (moved_id,)
    ).fetchone()
    database.close()

    assert after_row is not None
    assert after_row["rel_path"] == a_move.destination
    assert after_row["cover_status"] == before_row["cover_status"]
    assert progress_row is not None
    assert progress_row["page"] == 3
    assert cover_path.stat().st_mtime_ns == cover_mtime_before


# -------------------------------------------------------------------- the guard


def test_a_null_content_hash_row_refuses_apply_and_moves_nothing(
    catalogue_settings: Settings, config: PaperstandConfig
) -> None:
    _set_content_hash(catalogue_settings.db_path, CORRIERE_17, None)
    before = _fingerprint(catalogue_settings.library)

    report, text = _run(
        catalogue_settings.library, config, db_path=catalogue_settings.db_path, apply=True
    )

    assert report.refused == "catalogue"
    assert report.outcomes == []
    assert "content hash" in text
    assert CORRIERE_17 in text
    assert _fingerprint(catalogue_settings.library) == before


def test_a_null_content_hash_row_is_only_a_warning_in_a_dry_run(
    catalogue_settings: Settings, config: PaperstandConfig
) -> None:
    _set_content_hash(catalogue_settings.db_path, CORRIERE_17, None)

    report, text = _run(
        catalogue_settings.library, config, db_path=catalogue_settings.db_path, apply=False
    )

    assert report.refused is None
    assert report.moved > 0
    assert "content hash" in text


def test_a_schema_2_catalogue_refuses_apply(
    catalogue_settings: Settings, config: PaperstandConfig, tmp_path: Path
) -> None:
    from tests.test_db import _populated_schema_2

    path = tmp_path / "schema2.db"
    _populated_schema_2(path)

    report, text = _run(catalogue_settings.library, config, db_path=path, apply=True)

    assert report.refused == "catalogue"
    assert "cannot be read" in text


def test_no_catalogue_at_all_still_moves_everything(
    catalogue_settings: Settings, config: PaperstandConfig, tmp_path: Path
) -> None:
    report, text = _run(
        catalogue_settings.library, config, db_path=tmp_path / "absent.db", apply=True
    )

    assert report.refused is None
    assert report.moved > 0
    assert "none — nothing to preserve" in text


def test_a_source_absent_from_the_catalogue_still_moves(
    catalogue_settings: Settings, config: PaperstandConfig
) -> None:
    fresh_rel_path = "Newspapers/2026/03/20/Il_Mattutino_20_Marzo_2026.pdf"
    _write_pdf(catalogue_settings.library / fresh_rel_path, "not yet catalogued")

    report, _text = _run(
        catalogue_settings.library, config, db_path=catalogue_settings.db_path, apply=True
    )

    assert report.refused is None
    moved_sources = {outcome.rel_path for outcome in report.outcomes if isinstance(outcome, Moved)}
    assert fresh_rel_path in moved_sources


def test_deleting_a_movable_files_row_entirely_still_moves_it(
    catalogue_settings: Settings, config: PaperstandConfig
) -> None:
    _delete_issue(catalogue_settings.db_path, CORRIERE_17)

    report, _text = _run(
        catalogue_settings.library, config, db_path=catalogue_settings.db_path, apply=True
    )

    assert report.refused is None
    moved_sources = {outcome.rel_path for outcome in report.outcomes if isinstance(outcome, Moved)}
    assert CORRIERE_17 in moved_sources


# ------------------------------------------------------ hand-planned move resolution


def _empty_config() -> PaperstandConfig:
    return PaperstandConfig.model_validate({"libraries": []})


def _plan_of(
    *entries: InPlace | Move | Unplaced, collisions: list[Collision] | None = None
) -> LibraryPlan:
    return LibraryPlan(entries=list(entries), collisions=collisions or [], walk_complete=True)


def test_an_occupied_destination_with_equal_bytes_is_a_duplicate_and_both_survive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    library = tmp_path / "library"
    library.mkdir()
    _write_pdf(library / "A.pdf", "same content")
    shutil.copy2(library / "A.pdf", library / "B.pdf")
    monkeypatch.setattr(
        "paperstand.organizer.migration.plan_library",
        lambda root, cfg: _plan_of(Move("A.pdf", "B.pdf")),
    )

    report, _text = _run(
        library, _empty_config(), db_path=tmp_path / "absent.db", apply=True, prune_empty=False
    )

    assert report.outcomes == [Duplicate("A.pdf", "B.pdf")]
    assert (library / "A.pdf").is_file()
    assert (library / "B.pdf").is_file()


def test_an_occupied_destination_with_different_bytes_is_a_collision_and_both_survive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    library = tmp_path / "library"
    library.mkdir()
    _write_pdf(library / "A.pdf", "one thing")
    _write_pdf(library / "B.pdf", "something else entirely")
    monkeypatch.setattr(
        "paperstand.organizer.migration.plan_library",
        lambda root, cfg: _plan_of(Move("A.pdf", "B.pdf")),
    )

    report, _text = _run(
        library, _empty_config(), db_path=tmp_path / "absent.db", apply=True, prune_empty=False
    )

    assert report.outcomes == [Collided("A.pdf", "destination B.pdf exists with different content")]
    assert (library / "A.pdf").read_bytes() != b""
    assert (library / "B.pdf").is_file()


def test_an_in_place_member_of_a_collision_group_is_reported_as_a_collision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An ``InPlace`` entry whose path is also a collision's destination is unsafe to leave

    reported as settled: it becomes ``Collided``, with the same reason a
    ``Move`` member of the same group gets, counted as a collision rather
    than as in place, and — like every collision member — never touched.
    """
    library = tmp_path / "library"
    in_place_rel = "Newspapers/Corriere del Ponte/2026/Corriere del Ponte - 2026-03-17.pdf"
    move_source_rel = "Newspapers/2026/03/17/Corriere_del_Ponte_17_Marzo_2026.pdf"
    _write_pdf(library / in_place_rel, "already at its canonical place")
    _write_pdf(library / move_source_rel, "a different issue planning to the same path")
    collision = Collision(
        destination=in_place_rel, sources=tuple(sorted((in_place_rel, move_source_rel)))
    )
    monkeypatch.setattr(
        "paperstand.organizer.migration.plan_library",
        lambda root, cfg: _plan_of(
            InPlace(in_place_rel), Move(move_source_rel, in_place_rel), collisions=[collision]
        ),
    )

    report, text = _run(
        library, _empty_config(), db_path=tmp_path / "absent.db", apply=True, prune_empty=False
    )

    from paperstand.organizer.migration import _collision_reason, _left_in_place

    reason = _collision_reason(collision)
    assert f"{in_place_rel} -> collision: {reason}" in text
    assert f"{move_source_rel} -> collision: {reason}" in text
    assert report.in_place == 0
    assert report.collision == 2
    assert {outcome.rel_path for outcome in report.outcomes if isinstance(outcome, Collided)} == {
        in_place_rel,
        move_source_rel,
    }
    assert {entry.rel_path for entry in _left_in_place(report.outcomes)} == {
        in_place_rel,
        move_source_rel,
    }
    assert (library / in_place_rel).is_file()
    assert (library / move_source_rel).is_file()


def test_a_chain_resolves_in_one_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    library = tmp_path / "library"
    library.mkdir()
    _write_pdf(library / "A.pdf", "content of A")
    _write_pdf(library / "B.pdf", "content of B")
    a_bytes = (library / "A.pdf").read_bytes()
    b_bytes = (library / "B.pdf").read_bytes()
    monkeypatch.setattr(
        "paperstand.organizer.migration.plan_library",
        lambda root, cfg: _plan_of(Move("A.pdf", "B.pdf"), Move("B.pdf", "C.pdf")),
    )

    report, _text = _run(
        library, _empty_config(), db_path=tmp_path / "absent.db", apply=True, prune_empty=False
    )

    outcomes_by_rel_path = {outcome.rel_path: outcome for outcome in report.outcomes}
    assert outcomes_by_rel_path["A.pdf"] == Moved("A.pdf", "B.pdf")
    assert outcomes_by_rel_path["B.pdf"] == Moved("B.pdf", "C.pdf")
    assert not (library / "A.pdf").exists()
    assert (library / "B.pdf").read_bytes() == a_bytes
    assert (library / "C.pdf").read_bytes() == b_bytes


def test_a_swap_cycle_leaves_both_files_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    library = tmp_path / "library"
    library.mkdir()
    _write_pdf(library / "A.pdf", "content of A")
    _write_pdf(library / "B.pdf", "content of B")
    monkeypatch.setattr(
        "paperstand.organizer.migration.plan_library",
        lambda root, cfg: _plan_of(Move("A.pdf", "B.pdf"), Move("B.pdf", "A.pdf")),
    )
    before = _fingerprint(library)

    report, _text = _run(
        library, _empty_config(), db_path=tmp_path / "absent.db", apply=True, prune_empty=False
    )

    outcomes_by_rel_path = {outcome.rel_path: outcome for outcome in report.outcomes}
    assert outcomes_by_rel_path["A.pdf"] == Collided(
        "A.pdf", "destination is the current path of B.pdf"
    )
    assert outcomes_by_rel_path["B.pdf"] == Collided(
        "B.pdf", "destination is the current path of A.pdf"
    )
    assert _fingerprint(library) == before


def test_samefile_is_left_in_place(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    library = tmp_path / "library"
    library.mkdir()
    _write_pdf(library / "A.pdf", "content")
    _write_pdf(library / "a.PDF", "content, under another spelling of its case")
    monkeypatch.setattr(
        "paperstand.organizer.migration.plan_library",
        lambda root, cfg: _plan_of(Move("A.pdf", "a.PDF")),
    )
    monkeypatch.setattr(Path, "samefile", lambda self, other: True)
    before = _fingerprint(library)

    report, _text = _run(
        library, _empty_config(), db_path=tmp_path / "absent.db", apply=True, prune_empty=False
    )

    assert report.outcomes == [
        Collided("A.pdf", "the destination is this same file under another spelling of its case")
    ]
    assert _fingerprint(library) == before


def test_a_failed_move_is_reported_and_left_in_place_while_others_still_move(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    library = tmp_path / "library"
    library.mkdir()
    _write_pdf(library / "A.pdf", "will fail")
    _write_pdf(library / "Z.pdf", "will succeed")
    monkeypatch.setattr(
        "paperstand.organizer.migration.plan_library",
        lambda root, cfg: _plan_of(Move("A.pdf", "A2.pdf"), Move("Z.pdf", "Z2.pdf")),
    )

    def flaky_move_file(source: Path, destination: Path) -> None:
        if source.name == "A.pdf":
            raise OSError(errno.EIO, "input/output error")
        real_move_file(source, destination)

    monkeypatch.setattr("paperstand.organizer.migration.move_file", flaky_move_file)

    report, _text = _run(
        library, _empty_config(), db_path=tmp_path / "absent.db", apply=True, prune_empty=False
    )

    outcomes_by_rel_path = {outcome.rel_path: outcome for outcome in report.outcomes}
    assert isinstance(outcomes_by_rel_path["A.pdf"], Failed)
    assert outcomes_by_rel_path["Z.pdf"] == Moved("Z.pdf", "Z2.pdf")
    assert (library / "A.pdf").is_file()
    assert not (library / "Z.pdf").exists()
    assert (library / "Z2.pdf").is_file()
    assert report.failed == 1


# ---------------------------------------------------------------- the root marker


def _remembered_marker_settings(tmp_path: Path) -> Settings:
    library = tmp_path / "library"
    from paperstand.scanner.walker import MARKER_FILE

    (library / MARKER_FILE).parent.mkdir(parents=True, exist_ok=True)
    (library / MARKER_FILE).touch()
    settings = Settings(
        library=library,
        data=tmp_path / "data",
        config=None,
        static=None,
        scan_on_start=False,
        scan_interval=0,
    )
    scan_once(settings)
    return settings


def test_migrate_once_refuses_when_the_marker_is_remembered_but_gone(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    from paperstand.scanner.walker import MARKER_FILE

    settings = _remembered_marker_settings(tmp_path)
    (settings.library / MARKER_FILE).unlink()

    with caplog.at_level(logging.WARNING, logger="paperstand"):
        report, text = _run(
            settings.library,
            PaperstandConfig.model_validate({"libraries": []}),
            db_path=settings.db_path,
            apply=True,
        )

    assert report.refused == "marker"
    assert report.outcomes == []
    assert f"marker {MARKER_FILE} missing" in text
    warnings = [record for record in caplog.records if record.levelno >= logging.WARNING]
    assert any(MARKER_FILE in record.message for record in warnings)


def test_migrate_cli_exits_1_when_the_marker_is_remembered_but_gone(tmp_path: Path) -> None:
    from paperstand.scanner.walker import MARKER_FILE

    settings = _remembered_marker_settings(tmp_path)
    (settings.library / MARKER_FILE).unlink()

    code = migrate(settings.library, settings.data, None, apply=True, out=io.StringIO())

    assert code == 1


# --------------------------------------------------------------------- the walk


class _AlwaysIncompleteWalk(Walk):
    """A ``Walk`` stand-in whose ``complete`` is ``False`` whatever it actually saw.

    ``complete`` is a property computed from ``root_ok`` and ``unreadable``,
    not a plain attribute set once in ``_walk`` — overriding it here is the
    least invasive way to simulate a directory that could not be listed, or
    the depth limit being reached, without touching permissions on disk.
    """

    @property
    def complete(self) -> bool:
        return False


def test_migrate_once_refuses_apply_when_the_walk_is_incomplete(
    catalogue_settings: Settings, config: PaperstandConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("paperstand.organizer.migration.Walk", _AlwaysIncompleteWalk)
    reports_dir = catalogue_settings.data / "organizer" / "migrations"
    trigger_path = catalogue_settings.data / "scan.request"
    before = _fingerprint(catalogue_settings.library)

    report, text = _run(
        catalogue_settings.library,
        config,
        db_path=catalogue_settings.db_path,
        apply=True,
        reports_dir=reports_dir,
        trigger_path=trigger_path,
    )

    assert report.refused == "walk"
    assert report.outcomes == []
    assert "migrate: the library could not be walked completely; nothing moved" in text
    assert _fingerprint(catalogue_settings.library) == before
    assert not reports_dir.exists()
    assert not trigger_path.exists()


def test_migrate_cli_exits_1_when_the_walk_is_incomplete(
    catalogue_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("paperstand.organizer.migration.Walk", _AlwaysIncompleteWalk)

    code = migrate(
        catalogue_settings.library,
        catalogue_settings.data,
        catalogue_settings.config_path if catalogue_settings.config_path.is_file() else None,
        apply=True,
        out=io.StringIO(),
    )

    assert code == 1


def test_a_dry_run_only_warns_and_still_exits_clean_when_the_walk_is_incomplete(
    catalogue_settings: Settings, config: PaperstandConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("paperstand.organizer.migration.Walk", _AlwaysIncompleteWalk)

    report, text = _run(
        catalogue_settings.library, config, db_path=catalogue_settings.db_path, apply=False
    )

    assert report.refused is None
    lines = text.splitlines()
    assert lines[-1] == "migrate: the library could not be walked completely; nothing moved"
    assert "to move" in lines[-2]

    code = migrate(
        catalogue_settings.library,
        catalogue_settings.data,
        catalogue_settings.config_path if catalogue_settings.config_path.is_file() else None,
        apply=False,
        out=io.StringIO(),
    )
    assert code == 0


# ------------------------------------------------------------------------ lock


def test_the_lock_is_respected_and_exits_clean(
    catalogue_settings: Settings, config: PaperstandConfig
) -> None:
    lock_dir = catalogue_settings.db_path.parent / "organizer"
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_path = lock_dir / ".migrate.lock"
    with lock_path.open("a+") as held:
        fcntl.flock(held.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

        report, text = _run(
            catalogue_settings.library, config, db_path=catalogue_settings.db_path, apply=True
        )

        assert report.outcomes == []
        assert report.refused is None
        assert f"another migrate run holds {catalogue_settings.library}; nothing done" in text


# ----------------------------------------------------------------------- report


def test_the_report_is_written_on_apply_only_with_version_1(
    catalogue_settings: Settings, config: PaperstandConfig
) -> None:
    plan = plan_library(catalogue_settings.library, config)
    expected_destinations = {move.rel_path: move.destination for move in plan.movable()}
    reports_dir = catalogue_settings.data / "organizer" / "migrations"

    _run(
        catalogue_settings.library,
        config,
        db_path=catalogue_settings.db_path,
        apply=True,
        reports_dir=reports_dir,
    )

    written = list(reports_dir.iterdir())
    assert len(written) == 1
    run = MigrationRun.model_validate_json(written[0].read_text("utf-8"))
    assert run.version == 1
    assert run.moved > 0

    moves_on_disk = {move.source: move.destination for move in run.moves}
    assert moves_on_disk == expected_destinations
    for move in run.moves:
        assert (catalogue_settings.library / move.destination).is_file()
        assert not (catalogue_settings.library / move.source).exists()


def test_a_second_run_in_the_same_second_gets_a_dash_2_suffix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    library = tmp_path / "library"
    library.mkdir()
    _write_pdf(library / "A.pdf", "first run")
    _write_pdf(library / "B.pdf", "second run")
    reports_dir = tmp_path / "data" / "organizer" / "migrations"
    monkeypatch.setattr(time, "time", lambda: 1_800_000_000.0)

    monkeypatch.setattr(
        "paperstand.organizer.migration.plan_library",
        lambda root, cfg: _plan_of(Move("A.pdf", "A2.pdf")),
    )
    _run(
        library,
        _empty_config(),
        db_path=tmp_path / "absent.db",
        apply=True,
        prune_empty=False,
        reports_dir=reports_dir,
    )

    monkeypatch.setattr(
        "paperstand.organizer.migration.plan_library",
        lambda root, cfg: _plan_of(Move("B.pdf", "B2.pdf")),
    )
    _run(
        library,
        _empty_config(),
        db_path=tmp_path / "absent.db",
        apply=True,
        prune_empty=False,
        reports_dir=reports_dir,
    )

    names = {path.name for path in reports_dir.iterdir()}
    assert len(names) == 2
    plain = next(name for name in names if not name.endswith("-2.json"))
    assert plain.replace(".json", "-2.json") in names


# ------------------------------------------------------------------------- CLI


def test_it_is_wired_into_the_cli(
    sample_library: SampleLibrary, data_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["migrate", "--library", str(sample_library.root), "--data", str(data_dir)]) == 0
    assert "to move" in capsys.readouterr().out


def test_migrate_function_apply_moves_the_sample_library(
    catalogue_settings: Settings,
) -> None:
    code = migrate(
        catalogue_settings.library,
        catalogue_settings.data,
        catalogue_settings.config_path if catalogue_settings.config_path.is_file() else None,
        apply=True,
        out=io.StringIO(),
    )
    assert code == 0
