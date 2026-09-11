"""Resolving an inbox file's own name: the library it belongs to, or why not."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

import paperstand.parsing.parse as parse_module
from paperstand.config import PaperstandConfig, load_config
from paperstand.organizer.naming import Unsorted
from paperstand.organizer.resolve import Resolved, Resolver
from tests.conftest import SampleLibrary, write_sample_config

#: A fixed instant, used wherever the filename itself carries the whole date
#: and the modification time is only there to satisfy the signature.
MTIME = dt.datetime(2026, 4, 1)


@pytest.fixture
def resolver(sample_library: SampleLibrary, tmp_path: Path) -> Resolver:
    """A resolver over the sample library, with the example configuration
    plus the undeclared `Zines` library `write_sample_config` adds."""
    config_path = tmp_path / "paperstand.yml"
    write_sample_config(config_path)
    return Resolver(sample_library.root, load_config(config_path))


def test_a_configured_declared_title_resolves_into_its_declared_folder(
    resolver: Resolver,
) -> None:
    """Corriere del Ponte is both configured *and* declared: an old-shaped
    inbox name resolves into its own declared folder under `Newspapers/`."""
    resolved = resolver.resolve("Corriere_del_Ponte_17_Marzo_2026.pdf", MTIME)

    assert isinstance(resolved, Resolved)
    assert resolved.library.name == "Newspapers"
    assert resolved.destination.rel_path == (
        "Newspapers/Corriere del Ponte/2026/Corriere del Ponte - 2026-03-17.pdf"
    )


def test_a_configured_undeclared_title_resolves_inside_its_library(
    resolver: Resolver, sample_library: SampleLibrary
) -> None:
    """Orizzonte is configured but has no `publication.yml` of its own: it
    still resolves, into `Magazines/`, never at the library root."""
    orizzonte_file = next((sample_library.root / "Magazines/Orizzonte").glob("*.pdf"))

    resolved = resolver.resolve(orizzonte_file.name, MTIME)

    assert isinstance(resolved, Resolved)
    assert resolved.library.name == "Magazines"
    assert resolved.destination.folder.startswith("Magazines/Orizzonte/")


def test_a_declared_but_unconfigured_title_resolves_into_its_folder(
    resolver: Resolver, sample_library: SampleLibrary
) -> None:
    """Bright Meadows is declared under `Magazines/Bright Meadows` by a
    `publication.yml` alone — no `titles:` entry names it — yet an old,
    flat-layout name still resolves there, via the resolver's augmentation."""
    bright_meadows_file = next((sample_library.root / "Zines").glob("Bright_Meadows_*.pdf"))

    resolved = resolver.resolve(bright_meadows_file.name, MTIME)

    assert isinstance(resolved, Resolved)
    assert resolved.library.name == "Magazines"
    assert resolved.destination.folder.startswith("Magazines/Bright Meadows/")
    # `Resolved.library` is the configuration's own object, never the
    # resolver's augmented copy: "Bright Meadows" is not one of its titles.
    assert "Bright Meadows" not in resolved.library.title_names


def test_a_declared_folder_basename_differing_from_the_title_works_as_an_alias(
    tmp_path: Path,
) -> None:
    """A `publication.yml`'s `title:` need not equal its own folder's name:
    the resolver aliases the folder's basename for a title no
    `paperstand.yml` entry already configures, so a name spelled after the
    folder — the way the canonical layout always writes it — still resolves."""
    root = tmp_path / "library"
    folder = root / "Magazines" / "Orizzonte (Sud)"
    folder.mkdir(parents=True)
    (folder / "publication.yml").write_text("title: Orizzonte\n", encoding="utf-8")
    config = PaperstandConfig.model_validate(
        {"libraries": [{"name": "Magazines", "path": "Magazines", "kind": "magazine"}]}
    )

    resolved = Resolver(root, config).resolve("Orizzonte (Sud) - 2026-03-17.pdf", MTIME)

    assert isinstance(resolved, Resolved)
    assert resolved.issue.title_name == "Orizzonte"
    assert resolved.destination.rel_path == (
        "Magazines/Orizzonte (Sud)/2026/Orizzonte (Sud) - 2026-03-17.pdf"
    )


def test_the_same_title_configured_in_two_libraries_is_ambiguous(tmp_path: Path) -> None:
    root = tmp_path / "library"
    root.mkdir()
    config = PaperstandConfig.model_validate(
        {
            "libraries": [
                {
                    "name": "Newspapers",
                    "path": "Newspapers",
                    "kind": "newspaper",
                    "titles": ["Corriere del Ponte"],
                },
                {
                    "name": "Extra",
                    "path": "Extra",
                    "kind": "newspaper",
                    "titles": ["Corriere del Ponte"],
                },
            ]
        }
    )

    resolved = Resolver(root, config).resolve("Corriere_del_Ponte_17_Marzo_2026.pdf", MTIME)

    assert resolved == Unsorted(
        reason='"Corriere del Ponte" is declared in more than one library (Newspapers, Extra)'
    )


def test_a_name_no_title_matches_gets_the_hint_reason(resolver: Resolver) -> None:
    resolved = resolver.resolve("Zonda_Herald_17_Marzo_2026.pdf", MTIME)

    assert resolved == Unsorted(
        reason='no declared title matches "Zonda Herald" '
        "(declare it in paperstand.yml or in a publication.yml)"
    )


def test_a_name_with_no_libraries_at_all_hints_at_the_file_stem(tmp_path: Path) -> None:
    root = tmp_path / "library"
    root.mkdir()
    config = PaperstandConfig.model_validate({"libraries": []})

    resolved = Resolver(root, config).resolve("Zonda_Herald_17_Marzo_2026.pdf", MTIME)

    assert resolved == Unsorted(
        reason='no declared title matches "Zonda_Herald_17_Marzo_2026" '
        "(declare it in paperstand.yml or in a publication.yml)"
    )


def test_a_name_with_only_an_mtime_date_keeps_the_mtime_reason(resolver: Resolver) -> None:
    """Orizzonte carries no date of its own here: the existing `plan_issue`
    reason for a date that would come from the file's modification time
    applies exactly as it does for a file already in the library."""
    resolved = resolver.resolve("Orizzonte.pdf", MTIME)

    assert resolved == Unsorted(reason="date would come from the file's modification time")


def test_the_resolver_never_opens_a_pdf(
    sample_library: SampleLibrary, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("the resolver must never open a PDF")

    monkeypatch.setattr("pymupdf.open", refuse)

    config_path = tmp_path / "paperstand.yml"
    write_sample_config(config_path)
    resolver = Resolver(sample_library.root, load_config(config_path))

    for name in (
        "Corriere_del_Ponte_17_Marzo_2026.pdf",
        "Orizzonte.pdf",
        "Bright_Meadows_v2024_c02_Febbraio_2024.pdf",
        "Zonda_Herald_17_Marzo_2026.pdf",
    ):
        resolver.resolve(name, MTIME)


def test_each_augmented_library_is_built_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`parse.get_parser` caches by `(id(profile), id(library))`: a resolver
    that rebuilt its augmented copy per call would defeat that cache."""
    root = tmp_path / "library"
    root.mkdir()
    config = PaperstandConfig.model_validate(
        {
            "libraries": [
                {
                    "name": "Magazines",
                    "path": "Magazines",
                    "kind": "magazine",
                    "titles": ["Orizzonte"],
                }
            ]
        }
    )

    seen_ids: list[int] = []
    original_get_parser = parse_module.get_parser

    def spy(profile: object, library: object) -> object:
        seen_ids.append(id(library))
        return original_get_parser(profile, library)  # type: ignore[arg-type]

    monkeypatch.setattr(parse_module, "get_parser", spy)

    resolver = Resolver(root, config)
    resolver.resolve("Orizzonte_1650_-_9_gennaio_2026.pdf", MTIME)
    resolver.resolve("Orizzonte_1651_-_16_gennaio_2026.pdf", MTIME)

    assert seen_ids
    assert len(set(seen_ids)) == 1
