"""The configuration model, its loader, auto-discovery and the config hash."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from paperstand.config import (
    ConfigError,
    PaperstandConfig,
    config_from_folders,
    discover_libraries,
    guess_kind,
    is_ignored,
    load_config,
)
from tests.fixtures.filenames import IGNORED_PATHS, example_config_path


def test_the_example_file_loads() -> None:
    config = load_config(example_config_path())
    assert [library.name for library in config.libraries] == ["Newspapers", "Magazines"]
    newspapers = config.libraries[0]
    assert newspapers.kind == "newspaper"
    assert "Cronaca 24 Pagine" in newspapers.title_names
    assert config.ignore == ["comics"]


def test_plain_strings_and_mappings_are_both_titles() -> None:
    config = load_config(example_config_path())
    titles = {title.name: title for title in config.libraries[0].titles}
    assert titles["Corriere del Ponte"].aliases == []
    assert titles["Cronaca 24 Pagine"].aliases == ["La Cronaca 24 Pagine", "Cronaca24Pagine"]


def test_a_missing_file_falls_back_to_discovery(tmp_path: Path) -> None:
    config = load_config(tmp_path / "nope.yml", folder_names=["Newspapers", "Zines"])
    assert [library.name for library in config.libraries] == ["Newspapers", "Zines"]


def test_no_path_at_all_falls_back_to_discovery() -> None:
    config = load_config(None, folder_names=["Giornali"])
    assert config.libraries[0].kind == "newspaper"


@pytest.mark.parametrize(
    ("folder", "kind"),
    [
        ("Newspapers", "newspaper"),
        ("newspaper", "newspaper"),
        ("Dailies", "newspaper"),
        ("Giornali", "newspaper"),
        ("Quotidiani", "newspaper"),
        ("Journaux", "newspaper"),
        ("Zeitungen", "newspaper"),
        ("Periodicos", "newspaper"),
        ("Diarios", "newspaper"),
        ("Magazines", "magazine"),
        ("Zines", "magazine"),
        ("Newspapers Archive", "magazine"),
    ],
)
def test_kind_is_guessed_from_the_folder_name(folder: str, kind: str) -> None:
    assert guess_kind(folder) == kind


def test_discovery_skips_the_folders_that_are_never_libraries() -> None:
    names = ["Newspapers", "@eaDir", ".hidden", "#recycle", "comics"]
    discovered = discover_libraries(names, ignore=["comics"])
    assert [library.name for library in discovered] == ["Newspapers"]


def test_discovery_is_ordered_and_uses_the_folder_as_the_path() -> None:
    discovered = discover_libraries(["Zines", "Dailies"])
    assert [(library.name, library.path) for library in discovered] == [
        ("Dailies", "Dailies"),
        ("Zines", "Zines"),
    ]


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("@eaDir", True),
        (".hidden.pdf", True),
        ("#recycle", True),
        ("comics", True),
        ("Comics", True),
        ("Newspapers", False),
    ],
)
def test_ignore_rules(name: str, expected: bool) -> None:
    assert is_ignored(name, ["comics"]) is expected


def test_ignored_paths_are_all_caught_by_a_component_or_the_suffix() -> None:
    config = config_from_folders(["Newspapers", "comics"], ignore=["comics"])
    for path in IGNORED_PATHS:
        components = path.split("/")
        ignored = any(config.is_ignored(part) for part in components)
        assert ignored or not path.endswith(".pdf"), path


def test_library_for_picks_the_longest_matching_path() -> None:
    config = PaperstandConfig.model_validate(
        {
            "libraries": [
                {"name": "All", "path": "Print"},
                {"name": "Dailies", "path": "Print/Dailies"},
            ]
        }
    )
    assert config.library_for("Print/Dailies/x.pdf") is not None
    assert config.library_for("Print/Dailies/x.pdf").name == "Dailies"  # type: ignore[union-attr]
    assert config.library_for("Print/x.pdf").name == "All"  # type: ignore[union-attr]
    assert config.library_for("Elsewhere/x.pdf") is None


def test_config_hash_is_stable_and_sensitive() -> None:
    first = load_config(example_config_path())
    second = load_config(example_config_path())
    assert first.config_hash == second.config_hash
    changed = second.model_copy(update={"ignore": ["comics", "scans"]})
    assert changed.config_hash != first.config_hash


def test_an_unknown_parser_is_refused() -> None:
    with pytest.raises(ValueError, match="not defined"):
        PaperstandConfig.model_validate(
            {"libraries": [{"name": "N", "path": "N", "parser": "ghost"}]}
        )


def test_an_invalid_title_pattern_is_refused() -> None:
    with pytest.raises(ValueError, match="not a valid regex"):
        PaperstandConfig.model_validate(
            {
                "libraries": [
                    {"name": "N", "path": "N", "titles": [{"name": "X", "pattern": "^(?P<n"}]}
                ]
            }
        )


def test_a_broken_profile_names_the_file_the_profile_and_the_pattern(tmp_path: Path) -> None:
    path = tmp_path / "paperstand.yml"
    path.write_text(
        "parsers:\n  broken:\n    patterns: ['^(?P<title>']\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError) as error:
        load_config(path)
    message = str(error.value)
    assert str(path) in message
    assert "broken" in message
    assert "^(?P<title>" in message


def test_invalid_yaml_is_reported(tmp_path: Path) -> None:
    path = tmp_path / "paperstand.yml"
    path.write_text("libraries: [\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="not valid YAML"):
        load_config(path)


def test_an_empty_file_is_an_empty_configuration(tmp_path: Path) -> None:
    path = tmp_path / "paperstand.yml"
    path.write_text("", encoding="utf-8")
    assert load_config(path).libraries == []


def test_an_unknown_key_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "paperstand.yml"
    path.write_text("librarys: []\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="librarys"):
        load_config(path)


def test_profiles_are_resolved_once() -> None:
    config = load_config(example_config_path())
    library = config.libraries[0]
    assert config.profile_for(library) is config.profile_for(library)


def test_two_libraries_with_the_same_identifier_are_refused() -> None:
    """`Città` and `Citta` would share a row: one would swallow the other's files."""
    with pytest.raises(ValidationError, match="identifier 'citta'"):
        PaperstandConfig.model_validate(
            {
                "libraries": [
                    {"name": "Città", "path": "Citta"},
                    {"name": "Citta", "path": "Other"},
                ]
            }
        )


def test_a_colliding_configuration_file_names_the_offending_libraries(tmp_path: Path) -> None:
    path = tmp_path / "paperstand.yml"
    path.write_text(
        "libraries:\n  - {name: Zines, path: Zines}\n  - {name: 'Zines!', path: More}\n",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="both reduce to the identifier 'zines'"):
        load_config(path)


def test_auto_discovery_skips_a_colliding_folder() -> None:
    """Nobody named these folders for Paperstand, so a collision is skipped, not fatal."""
    libraries = discover_libraries(["Città", "Citta", "Zines"])

    assert [library.name for library in libraries] == ["Citta", "Zines"]
