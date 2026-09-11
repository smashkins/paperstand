"""``organize-plan``: the header, the per-file lines, collisions, and no writes."""

from __future__ import annotations

import io
import logging
import os
import re
from pathlib import Path

import pytest

from paperstand.__main__ import main
from paperstand.cli.organize import organize_plan
from tests.conftest import SampleLibrary
from tests.fixtures.filenames import example_config_path

LIBRARY_FILES: tuple[str, ...] = (
    "Newspapers/2026/03/17/Corriere_del_Ponte_17_Marzo_2026.pdf",
    "Newspapers/2026/03/17/La_Gazzetta_del_Lago_Sud_17_Marzo_2026.pdf",
    "Magazines/Confini/Confini_n._8_2026.pdf",
)

#: Same title, same date, same (absent) number, a different source name: this
#: must collide with `LIBRARY_FILES[0]` rather than one silently winning.
COLLIDING_FILE = "Newspapers/2026/03/17/Corriere_del_Ponte_2026-03-17.pdf"

CANONICAL_CORRIERE = "Corriere del Ponte/2026/Corriere del Ponte - 2026-03-17.pdf"


@pytest.fixture
def library(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A small library on disk, with one deliberate collision."""
    root = tmp_path / "library"
    for rel_path in (*LIBRARY_FILES, COLLIDING_FILE):
        target = root / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"%PDF-1.7\n")
    # The environment must not leak into the tests: no configuration, no root.
    monkeypatch.setenv("PAPERSTAND_CONFIG", str(tmp_path / "absent.yml"))
    monkeypatch.setenv("PAPERSTAND_LIBRARY", str(root))
    return root


def test_the_header_names_the_root_and_the_configuration(library: Path) -> None:
    out = io.StringIO()
    assert organize_plan(library, example_config_path(), out) == 0
    text = out.getvalue()
    assert f"library root:  {library.resolve()}" in text
    assert f"configuration: {example_config_path()}" in text


def test_auto_discovery_is_reported_too(library: Path) -> None:
    out = io.StringIO()
    assert organize_plan(library, None, out) == 0
    assert "configuration: auto-discovered" in out.getvalue()


def test_a_canonical_line_per_planned_file(library: Path) -> None:
    out = io.StringIO()
    assert organize_plan(library, example_config_path(), out) == 0
    text = out.getvalue()
    assert f"{LIBRARY_FILES[0]} -> {CANONICAL_CORRIERE}" in text
    assert "Magazines/Confini/Confini_n._8_2026.pdf -> Confini/2026/Confini - 2026 - n8.pdf" in text


def test_an_unsorted_line_carries_its_reason(library: Path) -> None:
    out = io.StringIO()
    assert organize_plan(library, example_config_path(), out) == 0
    text = out.getvalue()
    assert (
        f'{LIBRARY_FILES[1]} -> unsorted: no configured title matches "La Gazzetta del Lago Sud"'
    ) in text


def test_a_collision_is_reported_not_silently_won(library: Path) -> None:
    out = io.StringIO()
    assert organize_plan(library, example_config_path(), out) == 0
    text = out.getvalue()
    assert f"COLLISION {CANONICAL_CORRIERE}" in text
    assert f"  {LIBRARY_FILES[0]}" in text
    assert f"  {COLLIDING_FILE}" in text
    assert "3 planned, 0 in place, 1 unsorted, 1 collision(s)" in text


def test_exit_code_is_zero_even_with_unsorted_files_and_collisions(library: Path) -> None:
    assert organize_plan(library, example_config_path(), io.StringIO()) == 0


def test_a_missing_explicit_configuration_is_an_error(
    library: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    absent = tmp_path / "absent.yml"
    assert organize_plan(library, absent, io.StringIO()) == 2
    assert "no configuration file at" in capsys.readouterr().err


def test_it_refuses_a_file(library: Path) -> None:
    assert organize_plan(library / LIBRARY_FILES[0], None, io.StringIO()) == 2


def test_it_is_wired_into_the_cli(library: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["organize-plan", str(library)]) == 0
    assert "planned" in capsys.readouterr().out


# ------------------------------------------------------- declared publications


def test_a_declared_publications_own_files_plan_in_place(sample_library: SampleLibrary) -> None:
    """A file already named after its declared folder plans onto itself.

    The three Corriere del Ponte files (the daily, the declared Weekend
    supplement and the undeclared Speciale one) and the one La Gazzetta del
    Lago (Valdora) file are all already shaped
    ``<publication folder>/<YYYY>/<folder's own name> - ...pdf``.
    """
    out = io.StringIO()
    assert organize_plan(sample_library.root, example_config_path(), out) == 0
    text = out.getvalue()

    canonical_files = [
        *(sample_library.root / "Newspapers/Corriere del Ponte").rglob("*.pdf"),
        *(sample_library.root / "Newspapers/La Gazzetta del Lago (Valdora)").rglob("*.pdf"),
    ]
    assert len(canonical_files) == 4
    for path in canonical_files:
        rel_path = path.relative_to(sample_library.root).as_posix()
        assert f"{rel_path} -> in place" in text
    assert ", 4 in place," in text


def test_a_zero_padded_number_still_needs_a_move(sample_library: SampleLibrary) -> None:
    """The canonical grammar never zero-pads a number on write: `n03` reads
    back fine, but the file is not yet named `n3`, so it is not in place."""
    out = io.StringIO()
    assert organize_plan(sample_library.root, example_config_path(), out) == 0
    text = out.getvalue()

    bright_meadows = next((sample_library.root / "Magazines/Bright Meadows").rglob("*.pdf"))
    rel_path = bright_meadows.relative_to(sample_library.root).as_posix()
    assert f"{rel_path} -> in place" not in text
    assert (
        f"{rel_path} -> Magazines/Bright Meadows/2024/Bright Meadows - 2024-03 - v2024 n3.pdf"
    ) in text


def test_a_configured_title_elsewhere_plans_into_its_declared_folder(
    sample_library: SampleLibrary,
) -> None:
    """A date-folder file carrying the same configured title as a declared
    publication elsewhere joins that publication's folder, not the default
    ``<Title>/<YYYY>`` scheme — the same title the scanner already joins it
    to, planned the way its declared folder wants it named."""
    rel_path = "Newspapers/2026/03/17/laGazzettaDelLagoValdora17Marzo2026.pdf"
    assert (sample_library.root / rel_path).is_file()

    out = io.StringIO()
    assert organize_plan(sample_library.root, example_config_path(), out) == 0
    text = out.getvalue()

    assert (
        f"{rel_path} -> Newspapers/La Gazzetta del Lago (Valdora)/2026/"
        "La Gazzetta del Lago (Valdora) - 2026-03-17.pdf"
    ) in text


def test_the_summary_separates_in_place_from_planned(sample_library: SampleLibrary) -> None:
    out = io.StringIO()
    assert organize_plan(sample_library.root, example_config_path(), out) == 0
    summary = out.getvalue().strip().splitlines()[-1]
    assert re.fullmatch(r"\d+ planned, \d+ in place, \d+ unsorted, \d+ collision\(s\)", summary)


def test_two_folders_declaring_the_same_title_warn_and_the_first_wins(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A misconfiguration — two folders both claiming "Corriere del Ponte" —
    is a warning naming both folders, not a silent pick; the first one found
    while walking (folders are visited in name order) keeps the title, and
    every file that declares it, from either folder, plans there."""
    root = tmp_path / "library"
    for rel_path, content in (
        ("Newspapers/Corriere del Ponte/publication.yml", "id: corriere-del-ponte\n"),
        ("Newspapers/Il Mattutino/publication.yml", "title: Corriere del Ponte\n"),
    ):
        target = root / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    own_file = root / "Newspapers/Corriere del Ponte/2026/Corriere del Ponte - 2026-03-17.pdf"
    stray_file = root / "Newspapers/Il Mattutino/2026/Il Mattutino - 2026-03-18.pdf"
    for path in (own_file, stray_file):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"%PDF-1.7\n")

    with caplog.at_level(logging.WARNING, logger="paperstand"):
        out = io.StringIO()
        assert organize_plan(root, example_config_path(), out) == 0
    text = out.getvalue()

    assert (
        "Newspapers/Corriere del Ponte/2026/Corriere del Ponte - 2026-03-17.pdf -> in place"
    ) in text
    assert (
        "Newspapers/Il Mattutino/2026/Il Mattutino - 2026-03-18.pdf -> "
        "Newspapers/Corriere del Ponte/2026/Corriere del Ponte - 2026-03-18.pdf"
    ) in text
    warnings = [record.message for record in caplog.records if record.levelno >= logging.WARNING]
    assert any(
        "Corriere del Ponte" in message
        and "Newspapers/Corriere del Ponte" in message
        and "Newspapers/Il Mattutino" in message
        for message in warnings
    )


def test_it_never_writes_anything(sample_library: SampleLibrary) -> None:
    """Every file's mtime and bytes, before and after a full run, must match exactly.

    ``publication.yml`` files are ordinary files under ``os.walk`` and are
    already caught by the fingerprint below; the assertion just makes that
    coverage intentional rather than incidental — ``organize-plan`` only reads
    a declaration, it must never rewrite or touch one.
    """
    before = _fingerprint(sample_library.root)
    assert before, "the sample library fixture produced no files to check"
    assert any(path.endswith("publication.yml") for path in before), (
        "the sample library fixture produced no publication.yml to check"
    )
    assert organize_plan(sample_library.root, None, io.StringIO()) == 0
    after = _fingerprint(sample_library.root)
    assert before == after


def _fingerprint(root: Path) -> dict[str, tuple[int, bytes]]:
    """Every file under ``root``, keyed by path, to its mtime and its bytes."""
    fingerprints: dict[str, tuple[int, bytes]] = {}
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            path = Path(dirpath) / name
            fingerprints[str(path)] = (path.stat().st_mtime_ns, path.read_bytes())
    return fingerprints
