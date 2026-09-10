"""``organize-plan``: the header, the per-file lines, collisions, and no writes."""

from __future__ import annotations

import io
import os
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
    assert "3 planned, 1 unsorted, 1 collision(s)" in text


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


def test_it_never_writes_anything(sample_library: SampleLibrary) -> None:
    """Every file's mtime and bytes, before and after a full run, must match exactly."""
    before = _fingerprint(sample_library.root)
    assert before, "the sample library fixture produced no files to check"
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
