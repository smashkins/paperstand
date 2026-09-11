"""The ``parse-report`` and ``parse-explain`` commands and their walker."""

from __future__ import annotations

import io
import re
from pathlib import Path

import pytest

from paperstand.__main__ import main
from paperstand.cli.parse import (
    format_table,
    iter_library_files,
    load_cli_config,
    parse_explain,
    parse_report,
    resolve_library_root,
)
from paperstand.config import config_from_folders
from tests.conftest import SampleLibrary, write_sample_config
from tests.fixtures.filenames import IGNORED_PATHS, example_config_path

LIBRARY_FILES: tuple[str, ...] = (
    "Newspapers/2026/03/17/Corriere_del_Ponte_17_Marzo_2026.pdf",
    "Newspapers/2026/03/17/La_Gazzetta_del_Lago_Sud_17_Marzo_2026.pdf",
    "Magazines/Confini/Confini_n._8_2026.pdf",
)


@pytest.fixture
def library(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A small library on disk, plus the files that must be skipped."""
    root = tmp_path / "library"
    for rel_path in (*LIBRARY_FILES, *IGNORED_PATHS):
        target = root / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"%PDF-1.7\n")
    # The environment must not leak into the tests: no configuration, no root.
    monkeypatch.setenv("PAPERSTAND_CONFIG", str(tmp_path / "absent.yml"))
    monkeypatch.setenv("PAPERSTAND_LIBRARY", str(root))
    return root


def test_the_walker_skips_what_it_must(library: Path) -> None:
    config = config_from_folders(["Newspapers", "Magazines", "comics"], ignore=["comics"])
    found = list(iter_library_files(library, config))
    assert found == sorted(LIBRARY_FILES)


def test_the_walker_is_deterministic(library: Path) -> None:
    config = load_cli_config(example_config_path(), library)
    assert list(iter_library_files(library, config)) == list(iter_library_files(library, config))


def test_report_without_a_configuration(library: Path) -> None:
    out = io.StringIO()
    assert parse_report(library, None, out) == 0
    text = out.getvalue()
    assert "configuration: auto-discovered" in text
    assert "rel_path" in text and "matched_rule" not in text
    assert "Corriere del Ponte" in text
    assert "3 file(s)" in text
    # comics is a library of its own without a configuration to ignore it.
    assert "comics" in text


def test_report_with_a_configuration(library: Path) -> None:
    out = io.StringIO()
    assert parse_report(library, example_config_path(), out) == 0
    text = out.getvalue()
    assert "Newspapers (newspaper)" in text
    assert "Unsorted" in text
    assert "3 file(s), 1 unsorted" in text
    assert "Girandola" not in text


def test_report_refuses_a_file(library: Path) -> None:
    assert parse_report(library / "Newspapers", None, io.StringIO()) == 0
    assert parse_report(library / LIBRARY_FILES[0], None, io.StringIO()) == 2


def test_explain_prints_every_step(library: Path) -> None:
    out = io.StringIO()
    target = library / "Newspapers/2026/03/17/Corriere_del_Ponte_17_Marzo_2026.pdf"
    assert parse_explain(target, example_config_path(), library, out) == 0
    text = out.getvalue()
    for expected in (
        "library root:",
        "rel path:      Newspapers/2026/03/17/Corriere_del_Ponte_17_Marzo_2026.pdf",
        "library:       Newspapers (kind newspaper, parser default)",
        "publication:   none",
        "steps:",
        "publication",
        "replace",
        "spaced",
        "squashed",
        "date D3",
        "title T1",
        "result:",
        "title          Corriere del Ponte",
        "matched_rule   D3 title:config",
    ):
        assert expected in text, expected


def test_explain_finds_the_library_root_on_its_own(library: Path) -> None:
    out = io.StringIO()
    target = library / "Magazines/Confini/Confini_n._8_2026.pdf"
    assert parse_explain(target, example_config_path(), None, out) == 0
    assert f"library root:  {library}" in out.getvalue()


# ------------------------------------------------------- declared publications


def test_report_marks_a_declared_folders_files_title_publication(
    sample_library: SampleLibrary,
) -> None:
    """Every file beneath a declared folder — the three Corriere del Ponte
    ones, the La Gazzetta del Lago (Valdora) one and the Bright Meadows one —
    is walked with the index, so its rule says `title:publication`."""
    out = io.StringIO()
    assert parse_report(sample_library.root, example_config_path(), out) == 0
    assert out.getvalue().count("title:publication") == 5


def test_explain_prints_the_publication_header_and_step(sample_library: SampleLibrary) -> None:
    target = next((sample_library.root / "Newspapers/Corriere del Ponte").rglob("*.pdf"))
    out = io.StringIO()
    assert parse_explain(target, example_config_path(), sample_library.root, out) == 0
    text = out.getvalue()
    assert "publication:   Newspapers/Corriere del Ponte" in text
    assert "declared at 'Newspapers/Corriere del Ponte'" in text


def test_explain_says_none_for_a_file_outside_any_declaration(library: Path) -> None:
    out = io.StringIO()
    target = library / "Newspapers/2026/03/17/Corriere_del_Ponte_17_Marzo_2026.pdf"
    assert parse_explain(target, example_config_path(), library, out) == 0
    text = out.getvalue()
    assert "publication:   none" in text
    assert re.search(r"^\s*publication\s+none\s*$", text, re.MULTILINE)


def test_explain_rejects_a_file_outside_the_library(library: Path, tmp_path: Path) -> None:
    stray = tmp_path / "stray.pdf"
    stray.write_bytes(b"%PDF-1.7\n")
    assert parse_explain(stray, example_config_path(), library, io.StringIO()) == 2


def test_resolve_library_root_prefers_the_override(library: Path) -> None:
    target = library / LIBRARY_FILES[0]
    assert resolve_library_root(target, None, library) == library


def test_resolve_library_root_uses_a_configured_library_path(library: Path) -> None:
    config = load_cli_config(example_config_path(), library)
    target = library / LIBRARY_FILES[0]
    assert resolve_library_root(target, config) == library


def test_format_table_aligns_and_survives_an_empty_body() -> None:
    table = format_table(("a", "bbb"), [("xx", "y")])
    assert table.splitlines() == ["a   bbb", "--  ---", "xx  y"]
    assert format_table(("a", "bbb"), []).splitlines() == ["a  bbb", "-  ---"]


def test_the_commands_are_circuito_into_the_cli(
    library: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["parse-report", str(library)]) == 0
    assert "rel_path" in capsys.readouterr().out
    target = str(library / LIBRARY_FILES[0])
    assert main(["parse-explain", target, "--library-root", str(library)]) == 0
    assert "result:" in capsys.readouterr().out


def test_an_explicit_configuration_that_is_not_there_is_an_error(
    library: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Silently auto-discovering would report on rules the user never asked for."""
    absent = tmp_path / "absent.yml"
    assert parse_report(library, absent, io.StringIO()) == 2
    assert "no configuration file at" in capsys.readouterr().err
    target = library / LIBRARY_FILES[0]
    assert parse_explain(target, absent, library, io.StringIO()) == 2
    assert "no configuration file at" in capsys.readouterr().err


def test_a_missing_default_configuration_still_auto_discovers(library: Path) -> None:
    out = io.StringIO()
    assert parse_report(library, None, out) == 0
    assert "configuration: auto-discovered" in out.getvalue()


def test_the_scan_command_reports_its_counters(
    sample_library: SampleLibrary, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    data = tmp_path / "scan-data"
    write_sample_config(data / "paperstand.yml")

    assert main(["scan", "--library", str(sample_library.root), "--data", str(data)]) == 0

    out = capsys.readouterr().out
    assert f"files_seen   {sample_library.catalogued_files}" in out
    assert f"added        {sample_library.catalogued_files}" in out
    assert "errors       0" in out
    assert (data / "paperstand.db").is_file()

    assert main(["scan", "--library", str(sample_library.root), "--data", str(data)]) == 0
    assert "added        0" in capsys.readouterr().out
