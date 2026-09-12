"""The inbox pipeline: settle, verify, hash, resolve, claim, move — and the CLI.

Every scenario runs against ``catalogue_settings``: a private, already scanned
copy of the sample library, so the catalogue's content hashes are real and
match the library's own bytes exactly. ``settle=0`` throughout unless the test
is about settling itself, so freshly written files are never skipped for
being "too new".
"""

from __future__ import annotations

import errno
import fcntl
import io
import logging
import shutil
import tempfile
import threading
from pathlib import Path

import pymupdf
import pytest

from paperstand.__main__ import main
from paperstand.cli.organize import organize
from paperstand.config import PaperstandConfig, Settings, TitleConfig, load_config
from paperstand.db import open_database
from paperstand.organizer.inbox import (
    Duplicate,
    Failed,
    Moved,
    OrganizeReport,
    Parked,
    Skipped,
    organize_forever,
    organize_once,
)
from paperstand.organizer.mover import sidecar_path
from paperstand.scanner.hashing import content_hash
from paperstand.scanner.scanner import scan_once
from paperstand.scanner.walker import MARKER_FILE
from tests.test_cli_organize import _fingerprint

#: An existing, catalogued library file: copying it verbatim into the inbox
#: must be recognised as a duplicate of exactly this rel path.
CORRIERE_16 = "Newspapers/2026/03/16/Corriere_del_Ponte_-_16_Marzo_2026.pdf"

#: "Il Mattutino" is a plain configured title (no declared folder), so a fresh
#: date under it lands at the library's own `<Title>/<YYYY>/` scheme.
IL_MATTUTINO_DESTINATION = "Newspapers/Il Mattutino/2026/Il Mattutino - 2026-03-21.pdf"

#: A name no configured title matches, used exactly as in `test_organizer_resolve.py`.
ZONDA_HERALD = "Zonda_Herald_17_Marzo_2026.pdf"
ZONDA_REASON = (
    'no declared title matches "Zonda Herald" '
    "(declare it in paperstand.yml or in a publication.yml)"
)


def _write_pdf(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    document = pymupdf.open()
    document.new_page()
    document[0].insert_text((72, 72), text)
    document.save(path)
    document.close()
    return path


def _pdf_bytes(text: str) -> bytes:
    """A valid, one-page PDF's bytes, without leaving a file behind."""
    with tempfile.TemporaryDirectory() as scratch:
        return _write_pdf(Path(scratch) / "scratch.pdf", text).read_bytes()


def _write_encrypted_pdf(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    document = pymupdf.open()
    document.new_page()
    document.save(
        path,
        encryption=pymupdf.PDF_ENCRYPT_AES_256,  # type: ignore[attr-defined]
        user_pw="x",
        owner_pw="x",
    )
    document.close()
    return path


def _copy(source: Path, destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    return destination


@pytest.fixture
def inbox(tmp_path: Path) -> Path:
    folder = tmp_path / "inbox"
    folder.mkdir()
    return folder


@pytest.fixture
def config(catalogue_settings: Settings) -> PaperstandConfig:
    return load_config(catalogue_settings.config_path)


def _run(
    inbox: Path,
    settings: Settings,
    config: PaperstandConfig,
    *,
    apply: bool,
    settle: float = 0,
    now: float | None = None,
    out: io.StringIO | None = None,
) -> tuple[OrganizeReport, str]:
    stream = out if out is not None else io.StringIO()
    report = organize_once(
        inbox,
        settings.library,
        config,
        db_path=settings.db_path,
        apply=apply,
        settle=settle,
        now=now,
        out=stream,
    )
    return report, stream.getvalue()


def _without_lock(fingerprint: dict[str, tuple[int, bytes]]) -> dict[str, tuple[int, bytes]]:
    """Every entry but the organizer's own whole-run lock file.

    The lock is taken for the whole run in both modes — even a dry run must
    not race a concurrent apply — so it alone may appear (empty, freshly
    created) after a run that otherwise wrote nothing.
    """
    return {
        path: value for path, value in fingerprint.items() if not path.endswith(".organizer.lock")
    }


# ------------------------------------------------------------------ dry run


def test_a_dry_run_touches_neither_the_inbox_nor_the_library(
    inbox: Path, catalogue_settings: Settings, config: PaperstandConfig
) -> None:
    _write_pdf(inbox / "Il_Mattutino_2026-03-21.pdf", "fresh")
    _copy(catalogue_settings.library / CORRIERE_16, inbox / "Corriere_del_Ponte_16_Marzo_2026.pdf")
    _write_pdf(inbox / ZONDA_HERALD, "unresolvable")
    (inbox / "Broken.pdf").write_bytes(b"not a pdf at all\n")

    before_inbox = _without_lock(_fingerprint(inbox))
    before_library = _fingerprint(catalogue_settings.library)

    report, text = _run(inbox, catalogue_settings, config, apply=False)

    assert _without_lock(_fingerprint(inbox)) == before_inbox
    assert _fingerprint(catalogue_settings.library) == before_library
    kinds = {type(outcome) for outcome in report.outcomes}
    assert kinds == {Moved, Duplicate, Parked}
    assert "1 to move, 1 duplicate, 2 unsorted, 0 skipped" in text


# -------------------------------------------------------------------- apply


def test_apply_moves_a_resolvable_file_and_never_touches_a_pre_existing_library_file(
    inbox: Path, catalogue_settings: Settings, config: PaperstandConfig
) -> None:
    source = _write_pdf(inbox / "Il_Mattutino_2026-03-21.pdf", "fresh issue")
    before_bytes = source.read_bytes()
    before_library = _fingerprint(catalogue_settings.library)

    report, text = _run(inbox, catalogue_settings, config, apply=True)

    destination = catalogue_settings.library / IL_MATTUTINO_DESTINATION
    assert not source.exists()
    assert destination.read_bytes() == before_bytes
    assert report.outcomes == [Moved("Il_Mattutino_2026-03-21.pdf", IL_MATTUTINO_DESTINATION)]
    assert "1 moved, 0 duplicate, 0 unsorted, 0 skipped, 0 failed" in text

    after_library = _fingerprint(catalogue_settings.library)
    assert set(after_library) - set(before_library) == {str(destination)}
    for path, fingerprint in before_library.items():
        assert after_library[path] == fingerprint


def test_a_catalogued_duplicate_is_parked_with_a_sidecar(
    inbox: Path, catalogue_settings: Settings, config: PaperstandConfig
) -> None:
    _copy(
        catalogue_settings.library / CORRIERE_16,
        inbox / "Corriere_del_Ponte_-_16_Marzo_2026.pdf",
    )

    report, _text = _run(inbox, catalogue_settings, config, apply=True)

    assert report.outcomes == [Duplicate("Corriere_del_Ponte_-_16_Marzo_2026.pdf", CORRIERE_16)]
    parked = inbox / "duplicates" / "Corriere_del_Ponte_-_16_Marzo_2026.pdf"
    assert parked.is_file()
    assert sidecar_path(parked).read_text("utf-8") == f"duplicate of {CORRIERE_16}\n"


def test_a_junk_file_is_parked_unsorted(
    inbox: Path, catalogue_settings: Settings, config: PaperstandConfig
) -> None:
    (inbox / "Broken.pdf").write_bytes(b"not a pdf at all\n")

    report, _text = _run(inbox, catalogue_settings, config, apply=True)

    assert len(report.outcomes) == 1
    outcome = report.outcomes[0]
    assert isinstance(outcome, Parked)
    assert outcome.source == "Broken.pdf"
    assert outcome.reason.startswith("not a readable PDF (")
    assert (inbox / "unsorted" / "Broken.pdf").is_file()
    assert (
        sidecar_path(inbox / "unsorted" / "Broken.pdf").read_text("utf-8") == outcome.reason + "\n"
    )


def test_a_password_protected_pdf_is_parked_unsorted(
    inbox: Path, catalogue_settings: Settings, config: PaperstandConfig
) -> None:
    _write_encrypted_pdf(inbox / "Secret.pdf")

    report, _text = _run(inbox, catalogue_settings, config, apply=True)

    assert report.outcomes == [
        Parked("Secret.pdf", "not a readable PDF (the document is encrypted)")
    ]
    assert (inbox / "unsorted" / "Secret.pdf").is_file()


def test_an_unresolvable_name_is_parked_unsorted(
    inbox: Path, catalogue_settings: Settings, config: PaperstandConfig
) -> None:
    _write_pdf(inbox / ZONDA_HERALD, "unresolvable")

    report, _text = _run(inbox, catalogue_settings, config, apply=True)

    assert report.outcomes == [Parked(ZONDA_HERALD, ZONDA_REASON)]
    assert (inbox / "unsorted" / ZONDA_HERALD).is_file()


# --------------------------------------------------------- unsorted, re-read


def test_a_parked_file_moves_on_once_its_title_is_configured(
    inbox: Path, catalogue_settings: Settings, config: PaperstandConfig
) -> None:
    _write_pdf(inbox / ZONDA_HERALD, "zonda")
    _run(inbox, catalogue_settings, config, apply=True)

    parked = inbox / "unsorted" / ZONDA_HERALD
    assert parked.is_file()
    assert sidecar_path(parked).is_file()

    updated = config.model_copy(deep=True)
    newspapers = next(library for library in updated.libraries if library.name == "Newspapers")
    newspapers.titles.append(TitleConfig(name="Zonda Herald"))

    report, _text = _run(inbox, catalogue_settings, updated, apply=True)

    destination_rel = "Newspapers/Zonda Herald/2026/Zonda Herald - 2026-03-17.pdf"
    assert report.outcomes == [Moved(f"unsorted/{ZONDA_HERALD}", destination_rel)]
    assert not parked.exists()
    assert not sidecar_path(parked).exists()
    assert (catalogue_settings.library / destination_rel).is_file()


def test_a_parked_file_whose_move_fails_keeps_its_sidecar(
    inbox: Path,
    catalogue_settings: Settings,
    config: PaperstandConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The old sidecar is removed only once the move onto the library
    actually succeeds: an unexpected error moving a now-resolvable parked
    file leaves it, and its sidecar, exactly where they were."""
    _write_pdf(inbox / ZONDA_HERALD, "zonda")
    _run(inbox, catalogue_settings, config, apply=True)

    parked = inbox / "unsorted" / ZONDA_HERALD
    sidecar = sidecar_path(parked)
    assert parked.is_file()
    assert sidecar.is_file()

    updated = config.model_copy(deep=True)
    newspapers = next(library for library in updated.libraries if library.name == "Newspapers")
    newspapers.titles.append(TitleConfig(name="Zonda Herald"))

    def always_fail(source: Path, destination: Path) -> None:
        raise PermissionError(errno.EACCES, "permission denied")

    monkeypatch.setattr("paperstand.organizer.inbox.move_file", always_fail)

    report, _text = _run(inbox, catalogue_settings, updated, apply=True)

    assert report.outcomes == [Failed(f"unsorted/{ZONDA_HERALD}", "[Errno 13] permission denied")]
    assert parked.is_file()
    assert sidecar.is_file()


def test_a_still_unsorted_file_is_left_untouched_on_a_second_run(
    inbox: Path, catalogue_settings: Settings, config: PaperstandConfig
) -> None:
    _write_pdf(inbox / ZONDA_HERALD, "zonda")
    _run(inbox, catalogue_settings, config, apply=True)

    parked = inbox / "unsorted" / ZONDA_HERALD
    pdf_mtime_ns = parked.stat().st_mtime_ns
    sidecar = sidecar_path(parked)
    sidecar_mtime_ns = sidecar.stat().st_mtime_ns

    report, _text = _run(inbox, catalogue_settings, config, apply=True)

    assert report.outcomes == [Parked(f"unsorted/{ZONDA_HERALD}", ZONDA_REASON)]
    assert parked.stat().st_mtime_ns == pdf_mtime_ns
    assert sidecar.stat().st_mtime_ns == sidecar_mtime_ns


# --------------------------------------------------------------------- settle


def test_settle_skips_a_freshly_modified_file(
    inbox: Path, catalogue_settings: Settings, config: PaperstandConfig
) -> None:
    path = _write_pdf(inbox / "Il_Mattutino_2026-03-21.pdf", "fresh")
    now = path.stat().st_mtime + 30

    report, _text = _run(inbox, catalogue_settings, config, apply=True, settle=60, now=now)

    assert report.outcomes == [Skipped("Il_Mattutino_2026-03-21.pdf", "modified 30 s ago")]
    assert path.exists()


# ------------------------------------------------------------------ occupied


def test_occupied_with_equal_bytes_is_a_duplicate(
    inbox: Path, catalogue_settings: Settings, config: PaperstandConfig
) -> None:
    content = _pdf_bytes("same")
    destination = catalogue_settings.library / IL_MATTUTINO_DESTINATION
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(content)
    (inbox / "Il_Mattutino_2026-03-21.pdf").write_bytes(content)

    report, _text = _run(inbox, catalogue_settings, config, apply=True)

    assert report.outcomes == [Duplicate("Il_Mattutino_2026-03-21.pdf", IL_MATTUTINO_DESTINATION)]
    assert destination.read_bytes() == content
    assert (inbox / "duplicates" / "Il_Mattutino_2026-03-21.pdf").read_bytes() == content


def test_occupied_with_different_bytes_is_parked_unsorted(
    inbox: Path, catalogue_settings: Settings, config: PaperstandConfig
) -> None:
    destination = catalogue_settings.library / IL_MATTUTINO_DESTINATION
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(_pdf_bytes("occupant"))
    occupant_bytes = destination.read_bytes()
    _write_pdf(inbox / "Il_Mattutino_2026-03-21.pdf", "new arrival")

    report, _text = _run(inbox, catalogue_settings, config, apply=True)

    reason = f"destination {IL_MATTUTINO_DESTINATION} exists with different content"
    assert report.outcomes == [Parked("Il_Mattutino_2026-03-21.pdf", reason)]
    assert destination.read_bytes() == occupant_bytes
    assert (inbox / "unsorted" / "Il_Mattutino_2026-03-21.pdf").is_file()


def test_dry_run_reports_the_same_occupied_outcomes_without_writing(
    inbox: Path, catalogue_settings: Settings, config: PaperstandConfig
) -> None:
    content = _pdf_bytes("same")
    destination = catalogue_settings.library / IL_MATTUTINO_DESTINATION
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(content)
    (inbox / "Il_Mattutino_2026-03-21.pdf").write_bytes(content)

    report, _text = _run(inbox, catalogue_settings, config, apply=False)

    assert report.outcomes == [Duplicate("Il_Mattutino_2026-03-21.pdf", IL_MATTUTINO_DESTINATION)]
    assert not (inbox / "duplicates").exists()
    assert (inbox / "Il_Mattutino_2026-03-21.pdf").exists()


# -------------------------------------------------------------------- claims


def test_two_files_claiming_the_same_destination_the_first_wins(
    inbox: Path, catalogue_settings: Settings, config: PaperstandConfig
) -> None:
    # The numeric prefix is stripped before parsing, so both names resolve to
    # the exact same title and date; the prefix only decides the walk order.
    first = _write_pdf(inbox / "1111111111_111111_Il_Mattutino_2026-03-21.pdf", "first")
    first_bytes = first.read_bytes()
    _write_pdf(inbox / "Il_Mattutino_2026-03-21.pdf", "second")

    report, _text = _run(inbox, catalogue_settings, config, apply=True)

    claim_reason = (
        f"destination {IL_MATTUTINO_DESTINATION} is claimed by "
        "1111111111_111111_Il_Mattutino_2026-03-21.pdf"
    )
    assert report.outcomes == [
        Moved("1111111111_111111_Il_Mattutino_2026-03-21.pdf", IL_MATTUTINO_DESTINATION),
        Parked("Il_Mattutino_2026-03-21.pdf", claim_reason),
    ]
    destination = catalogue_settings.library / IL_MATTUTINO_DESTINATION
    assert destination.read_bytes() == first_bytes
    assert (inbox / "unsorted" / "Il_Mattutino_2026-03-21.pdf").is_file()


def test_a_byte_identical_second_file_is_a_duplicate_of_the_one_moved_this_run(
    inbox: Path, catalogue_settings: Settings, config: PaperstandConfig
) -> None:
    content = _pdf_bytes("fresh")
    # "-1" is a dedup suffix the parser strips, so this resolves to the same
    # destination as the plain name — and sorts before it, so it moves first.
    (inbox / "Il_Mattutino_2026-03-21-1.pdf").write_bytes(content)
    (inbox / "Il_Mattutino_2026-03-21.pdf").write_bytes(content)

    report, _text = _run(inbox, catalogue_settings, config, apply=True)

    assert report.outcomes == [
        Moved("Il_Mattutino_2026-03-21-1.pdf", IL_MATTUTINO_DESTINATION),
        Duplicate("Il_Mattutino_2026-03-21.pdf", IL_MATTUTINO_DESTINATION),
    ]


# ----------------------------------------------------------------- catalogue


def test_no_catalogue_warns_in_the_header_and_still_moves(
    inbox: Path, catalogue_settings: Settings, config: PaperstandConfig, tmp_path: Path
) -> None:
    _write_pdf(inbox / "Il_Mattutino_2026-03-21.pdf", "fresh")
    missing_db = tmp_path / "no-such-data" / "paperstand.db"

    out = io.StringIO()
    report = organize_once(
        inbox,
        catalogue_settings.library,
        config,
        db_path=missing_db,
        apply=True,
        settle=0,
        out=out,
    )

    assert "not readable — duplicates are checked against the destination only" in out.getvalue()
    assert report.outcomes == [Moved("Il_Mattutino_2026-03-21.pdf", IL_MATTUTINO_DESTINATION)]


CORRIERE_16_CANONICAL = "Newspapers/Corriere del Ponte/2026/Corriere del Ponte - 2026-03-16.pdf"


def test_a_removed_catalogued_file_is_not_trusted_as_a_duplicate(
    inbox: Path, catalogue_settings: Settings, config: PaperstandConfig
) -> None:
    """A hash read from the catalogue is only trusted while the file it
    names is still on disk: removed since the last scan, a fresh copy of the
    same bytes resolves and moves instead of being parked as a duplicate of
    something that is gone."""
    catalogued = catalogue_settings.library / CORRIERE_16
    content = catalogued.read_bytes()
    catalogued.unlink()
    (inbox / "Corriere_del_Ponte_-_16_Marzo_2026.pdf").write_bytes(content)

    report, _text = _run(inbox, catalogue_settings, config, apply=True)

    assert report.outcomes == [
        Moved("Corriere_del_Ponte_-_16_Marzo_2026.pdf", CORRIERE_16_CANONICAL)
    ]
    assert (catalogue_settings.library / CORRIERE_16_CANONICAL).read_bytes() == content


def test_a_catalogued_file_still_on_disk_is_still_a_duplicate(
    inbox: Path, catalogue_settings: Settings, config: PaperstandConfig
) -> None:
    """The existing behaviour, pinned down again next to the removed case
    above: a catalogued file still there, unchanged, still makes a fresh
    byte-identical copy a duplicate."""
    _copy(
        catalogue_settings.library / CORRIERE_16,
        inbox / "Corriere_del_Ponte_-_16_Marzo_2026.pdf",
    )

    report, _text = _run(inbox, catalogue_settings, config, apply=True)

    assert report.outcomes == [Duplicate("Corriere_del_Ponte_-_16_Marzo_2026.pdf", CORRIERE_16)]


# ------------------------------------------------------------------ escaping


def test_a_destination_escaping_the_library_is_parked_unsorted(
    inbox: Path, catalogue_settings: Settings
) -> None:
    """``LibraryConfig.path`` is a free string: a `..` in it must never let
    a planned destination escape the library root."""
    escaping_config = PaperstandConfig.model_validate(
        {
            "libraries": [
                {
                    "name": "Newspapers",
                    "path": "../outside",
                    "kind": "newspaper",
                    "titles": ["Il Mattutino"],
                }
            ]
        }
    )
    _write_pdf(inbox / "Il_Mattutino_2026-03-21.pdf", "fresh")

    report, _text = _run(inbox, catalogue_settings, escaping_config, apply=True)

    reason = (
        "destination ../outside/Il Mattutino/2026/Il Mattutino - 2026-03-21.pdf "
        "is outside the library"
    )
    assert report.outcomes == [Parked("Il_Mattutino_2026-03-21.pdf", reason)]
    assert (inbox / "unsorted" / "Il_Mattutino_2026-03-21.pdf").is_file()
    assert not (catalogue_settings.library.parent / "outside").exists()


# ---------------------------------------------------------------------- lock


def test_the_lock_is_respected_and_exits_clean(
    inbox: Path, catalogue_settings: Settings, config: PaperstandConfig
) -> None:
    _write_pdf(inbox / "Il_Mattutino_2026-03-21.pdf", "fresh")
    lock_path = inbox / ".organizer.lock"
    with lock_path.open("a+") as held:
        fcntl.flock(held.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

        out = io.StringIO()
        report = organize_once(
            inbox,
            catalogue_settings.library,
            config,
            db_path=catalogue_settings.db_path,
            apply=True,
            settle=0,
            out=out,
        )

        assert report.outcomes == []
        assert f"another organizer run holds {inbox}; nothing done" in out.getvalue()
        assert (inbox / "Il_Mattutino_2026-03-21.pdf").exists()


# ------------------------------------------------------------------- forever


def test_organize_forever_runs_once_when_the_stop_event_is_already_set() -> None:
    stop = threading.Event()
    stop.set()
    calls: list[int] = []

    def fake_run() -> OrganizeReport:
        calls.append(1)
        return OrganizeReport()

    organize_forever(fake_run, 999, stop)

    assert calls == [1]


def test_organize_forever_stops_once_a_later_run_sets_the_event() -> None:
    stop = threading.Event()
    calls: list[int] = []

    def fake_run() -> OrganizeReport:
        calls.append(1)
        if len(calls) == 3:
            stop.set()
        return OrganizeReport()

    organize_forever(fake_run, 0, stop)

    assert calls == [1, 1, 1]


# ------------------------------------------------------------------- failure


def test_an_unexpected_os_error_during_the_move_is_reported_failed(
    inbox: Path,
    catalogue_settings: Settings,
    config: PaperstandConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_pdf(inbox / "Il_Mattutino_2026-03-21.pdf", "fresh")

    def always_fail(source: Path, destination: Path) -> None:
        raise OSError(errno.EACCES, "permission denied")

    monkeypatch.setattr("paperstand.organizer.inbox.move_file", always_fail)

    report, text = _run(inbox, catalogue_settings, config, apply=True)

    assert report.failed == 1
    assert report.outcomes == [
        Failed("Il_Mattutino_2026-03-21.pdf", "[Errno 13] permission denied")
    ]
    assert (inbox / "Il_Mattutino_2026-03-21.pdf").exists()
    assert "0 moved, 0 duplicate, 0 unsorted, 0 skipped, 1 failed" in text


# ------------------------------------------------------------------ vanished


def test_a_file_deleted_mid_processing_is_skipped_and_others_still_move(
    inbox: Path,
    catalogue_settings: Settings,
    config: PaperstandConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A file that vanishes between the walk and its own hashing step must
    not abort the run: it is reported ``Skipped``, and a later candidate —
    walked afterwards, in name order — still moves."""
    doomed = _write_pdf(inbox / "Corriere_del_Ponte_2026-03-01.pdf", "vanishing")
    _write_pdf(inbox / "Il_Mattutino_2026-03-21.pdf", "fresh")
    real_content_hash = content_hash

    def vanish_then_hash(path: Path) -> str:
        if path == doomed:
            path.unlink()
        return real_content_hash(path)

    monkeypatch.setattr("paperstand.organizer.inbox.content_hash", vanish_then_hash)

    report, _text = _run(inbox, catalogue_settings, config, apply=True)

    assert report.outcomes == [
        Skipped("Corriere_del_Ponte_2026-03-01.pdf", "vanished before it was processed"),
        Moved("Il_Mattutino_2026-03-21.pdf", IL_MATTUTINO_DESTINATION),
    ]
    assert report.failed == 0
    assert (catalogue_settings.library / IL_MATTUTINO_DESTINATION).is_file()


def test_an_unexpected_os_error_while_parking_is_reported_failed_not_aborted(
    inbox: Path,
    catalogue_settings: Settings,
    config: PaperstandConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unexpected error raised while parking — never while making the
    primary move itself — is ``Failed``, not a crash that stops the run
    before later candidates are even looked at."""
    _write_pdf(inbox / ZONDA_HERALD, "unresolvable")
    _write_pdf(inbox / "Il_Mattutino_2026-03-21.pdf", "fresh")

    def always_fail(source: Path, folder: Path, reason: str) -> Path:
        raise PermissionError(errno.EACCES, "permission denied")

    monkeypatch.setattr("paperstand.organizer.inbox.park", always_fail)

    report, _text = _run(inbox, catalogue_settings, config, apply=True)

    assert report.outcomes == [
        Moved("Il_Mattutino_2026-03-21.pdf", IL_MATTUTINO_DESTINATION),
        Failed(ZONDA_HERALD, "[Errno 13] permission denied"),
    ]
    assert report.failed == 1


# --------------------------------------------------------------------- scan


def test_a_scan_after_apply_catalogues_the_moved_file_under_its_title(
    inbox: Path, catalogue_settings: Settings, config: PaperstandConfig
) -> None:
    _write_pdf(inbox / "Il_Mattutino_2026-03-21.pdf", "fresh")
    _run(inbox, catalogue_settings, config, apply=True)

    result = scan_once(catalogue_settings)
    assert result.status == "ok"
    assert result.added >= 1

    database = open_database(catalogue_settings.db_path)
    try:
        row = database.connection.execute(
            "SELECT t.name FROM issues i JOIN titles t ON i.title_id = t.id WHERE i.rel_path = ?",
            (IL_MATTUTINO_DESTINATION,),
        ).fetchone()
    finally:
        database.close()
    assert row is not None
    assert row["name"] == "Il Mattutino"


# --------------------------------------------------------------- the root marker


def _remembered_marker_settings(tmp_path: Path) -> Settings:
    """A library that has already had a scan remember its root marker."""
    library = tmp_path / "library"
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


def test_organize_once_refuses_when_the_marker_is_remembered_but_gone(
    inbox: Path, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    settings = _remembered_marker_settings(tmp_path)
    (settings.library / MARKER_FILE).unlink()
    _write_pdf(inbox / "Il_Mattutino_2026-03-21.pdf", "fresh")

    with caplog.at_level(logging.WARNING, logger="paperstand"):
        report, output = _run(
            inbox,
            settings,
            PaperstandConfig.model_validate({"libraries": []}),
            apply=True,
        )

    assert report.refused is True
    assert report.outcomes == []
    assert f"marker {MARKER_FILE} missing" in output
    assert (inbox / "Il_Mattutino_2026-03-21.pdf").exists()
    warnings = [record for record in caplog.records if record.levelno >= logging.WARNING]
    assert any(MARKER_FILE in record.message for record in warnings)


def test_organize_exits_1_when_the_marker_is_remembered_but_gone(
    inbox: Path, tmp_path: Path
) -> None:
    settings = _remembered_marker_settings(tmp_path)
    (settings.library / MARKER_FILE).unlink()
    _write_pdf(inbox / "Il_Mattutino_2026-03-21.pdf", "fresh")

    code = organize(
        inbox,
        settings.library,
        settings.data,
        None,
        apply=True,
        settle=0,
        out=io.StringIO(),
    )

    assert code == 1
    assert (inbox / "Il_Mattutino_2026-03-21.pdf").exists()


def test_organize_forever_keeps_going_through_a_refused_iteration(
    inbox: Path, tmp_path: Path
) -> None:
    settings = _remembered_marker_settings(tmp_path)
    (settings.library / MARKER_FILE).unlink()
    config = PaperstandConfig.model_validate({"libraries": []})
    stop = threading.Event()
    calls: list[OrganizeReport] = []

    def run() -> OrganizeReport:
        report, _ = _run(inbox, settings, config, apply=True)
        calls.append(report)
        if len(calls) == 3:
            stop.set()
        return report

    organize_forever(run, 0, stop)

    assert len(calls) == 3
    assert all(report.refused for report in calls)


# ---------------------------------------------------------------------- CLI


def test_organize_is_a_dry_run_by_default(inbox: Path, catalogue_settings: Settings) -> None:
    _write_pdf(inbox / "Il_Mattutino_2026-03-21.pdf", "fresh")
    out = io.StringIO()

    code = organize(
        inbox,
        catalogue_settings.library,
        catalogue_settings.data,
        catalogue_settings.config_path,
        out=out,
    )

    assert code == 0
    assert "mode:          dry-run" in out.getvalue()
    assert (inbox / "Il_Mattutino_2026-03-21.pdf").exists()


def test_organize_uses_the_data_configuration_when_none_is_given(
    inbox: Path, catalogue_settings: Settings
) -> None:
    _write_pdf(inbox / "Il_Mattutino_2026-03-21.pdf", "fresh")
    out = io.StringIO()

    code = organize(
        inbox,
        catalogue_settings.library,
        catalogue_settings.data,
        None,
        apply=True,
        settle=0,
        out=out,
    )

    assert code == 0
    assert (catalogue_settings.library / IL_MATTUTINO_DESTINATION).is_file()


def test_organize_honours_paperstand_config_without_an_explicit_flag(
    inbox: Path,
    catalogue_settings: Settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``--config`` absent falls back to ``settings.config_path``, which
    honours ``PAPERSTAND_CONFIG`` — not just ``<data>/paperstand.yml``, which
    also exists here and would otherwise silently win."""
    _write_pdf(inbox / "Il_Mattutino_2026-03-21.pdf", "fresh")
    elsewhere_config = tmp_path / "elsewhere" / "paperstand.yml"
    elsewhere_config.parent.mkdir(parents=True)
    elsewhere_config.write_text(catalogue_settings.config_path.read_text("utf-8"), encoding="utf-8")
    monkeypatch.setenv("PAPERSTAND_CONFIG", str(elsewhere_config))
    out = io.StringIO()

    code = organize(
        inbox,
        catalogue_settings.library,
        catalogue_settings.data,
        None,
        apply=True,
        settle=0,
        out=out,
    )

    assert code == 0
    assert f"configuration: {elsewhere_config}" in out.getvalue()
    assert (catalogue_settings.library / IL_MATTUTINO_DESTINATION).is_file()


def test_organize_refuses_a_missing_inbox_directory(
    tmp_path: Path, catalogue_settings: Settings
) -> None:
    missing = tmp_path / "no-inbox"
    code = organize(
        missing, catalogue_settings.library, catalogue_settings.data, None, out=io.StringIO()
    )
    assert code == 2


def test_organize_refuses_a_missing_library_directory(
    inbox: Path, catalogue_settings: Settings, tmp_path: Path
) -> None:
    missing = tmp_path / "no-library"
    code = organize(inbox, missing, catalogue_settings.data, None, out=io.StringIO())
    assert code == 2


def test_organize_refuses_an_inbox_inside_the_library(
    catalogue_settings: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    inbox_inside = catalogue_settings.library / "inbox"
    inbox_inside.mkdir()
    code = organize(
        inbox_inside, catalogue_settings.library, catalogue_settings.data, None, out=io.StringIO()
    )
    assert code == 2
    assert (
        "organize: the inbox must not be inside the library nor contain it"
        in capsys.readouterr().err
    )


def test_organize_refuses_a_library_inside_the_inbox(
    inbox: Path, catalogue_settings: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    library_inside = inbox / "library"
    library_inside.mkdir()
    code = organize(inbox, library_inside, catalogue_settings.data, None, out=io.StringIO())
    assert code == 2
    assert (
        "organize: the inbox must not be inside the library nor contain it"
        in capsys.readouterr().err
    )


def test_organize_refuses_the_inbox_and_the_library_being_the_same_directory(
    catalogue_settings: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    code = organize(
        catalogue_settings.library,
        catalogue_settings.library,
        catalogue_settings.data,
        None,
        out=io.StringIO(),
    )
    assert code == 2
    assert (
        "organize: the inbox must not be inside the library nor contain it"
        in capsys.readouterr().err
    )


def test_organize_refuses_a_missing_explicit_config(
    inbox: Path,
    catalogue_settings: Settings,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    absent = tmp_path / "absent.yml"
    code = organize(
        inbox, catalogue_settings.library, catalogue_settings.data, absent, out=io.StringIO()
    )
    assert code == 2
    assert "no configuration file at" in capsys.readouterr().err


def test_organize_exits_1_when_a_move_fails(
    inbox: Path,
    catalogue_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_pdf(inbox / "Il_Mattutino_2026-03-21.pdf", "fresh")
    monkeypatch.setattr(
        "paperstand.cli.organize.organize_once",
        lambda *args, **kwargs: OrganizeReport(outcomes=[Failed("x", "boom")]),
    )

    code = organize(
        inbox,
        catalogue_settings.library,
        catalogue_settings.data,
        catalogue_settings.config_path,
        apply=True,
        out=io.StringIO(),
    )

    assert code == 1


def test_main_is_wired_to_the_organize_subcommand(
    inbox: Path, catalogue_settings: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_pdf(inbox / "Il_Mattutino_2026-03-21.pdf", "fresh")

    code = main(
        [
            "organize",
            "--inbox",
            str(inbox),
            "--library",
            str(catalogue_settings.library),
            "--data",
            str(catalogue_settings.data),
            "--settle",
            "0",
        ]
    )

    assert code == 0
    out = capsys.readouterr().out
    assert "inbox root:" in out
    assert "Il_Mattutino_2026-03-21.pdf ->" in out
    assert "1 to move, 0 duplicate, 0 unsorted, 0 skipped" in out
