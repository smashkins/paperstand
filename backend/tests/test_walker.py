"""The library walker: what it yields, and what it refuses to look at."""

from __future__ import annotations

import logging
import os
from pathlib import Path

import pytest

from paperstand.config import PaperstandConfig, load_config
from paperstand.scanner.walker import Walk, top_level_folders, walk_library
from tests.conftest import SampleLibrary, write_sample_config


def sample_config(sample_library: SampleLibrary, tmp_path: Path) -> PaperstandConfig:
    config_path = tmp_path / "paperstand.yml"
    write_sample_config(config_path)
    return load_config(config_path)


def test_the_walker_finds_every_catalogued_pdf(
    sample_library: SampleLibrary, tmp_path: Path
) -> None:
    config = sample_config(sample_library, tmp_path)
    found = list(walk_library(sample_library.root, config))

    assert len(found) == sample_library.catalogued_files


def test_the_walker_skips_metadata_folders_and_hidden_files(
    sample_library: SampleLibrary, tmp_path: Path
) -> None:
    config = sample_config(sample_library, tmp_path)
    paths = [found.rel_path for found in walk_library(sample_library.root, config)]

    assert all(path.lower().endswith(".pdf") for path in paths)
    assert not [path for path in paths if "@eaDir" in path or "#recycle" in path]
    assert not [path for path in paths if any(part.startswith(".") for part in path.split("/"))]
    assert not [path for path in paths if path.startswith("comics/")]
    assert "Newspapers/notes.txt" not in paths


def test_a_handle_prefix_is_a_file_name_not_metadata(
    sample_library: SampleLibrary, tmp_path: Path
) -> None:
    """``@eaDir`` is a folder to skip; ``@handle_Title.pdf`` is a periodical."""
    config = sample_config(sample_library, tmp_path)
    paths = [found.rel_path for found in walk_library(sample_library.root, config)]

    assert [path for path in paths if path.rsplit("/", 1)[-1].startswith("@handle_")]


def test_the_walker_reports_the_real_size_and_mtime(
    sample_library: SampleLibrary, tmp_path: Path
) -> None:
    config = sample_config(sample_library, tmp_path)
    for found in walk_library(sample_library.root, config):
        info = os.stat(sample_library.path(found.rel_path))
        assert (found.size, found.mtime_ns) == (info.st_size, info.st_mtime_ns)
        assert found.filename == found.rel_path.rsplit("/", 1)[-1]


def test_the_walker_is_deterministic(sample_library: SampleLibrary, tmp_path: Path) -> None:
    config = sample_config(sample_library, tmp_path)
    first = [found.rel_path for found in walk_library(sample_library.root, config)]
    second = [found.rel_path for found in walk_library(sample_library.root, config)]

    assert first == second


def test_the_configured_ignore_list_applies_to_folders(tmp_path: Path) -> None:
    root = tmp_path / "library"
    (root / "Keep").mkdir(parents=True)
    (root / "Drop").mkdir()
    (root / "Keep" / "a.pdf").write_bytes(b"%PDF-1.7\n")
    (root / "Drop" / "b.pdf").write_bytes(b"%PDF-1.7\n")
    config = PaperstandConfig.model_validate(
        {"libraries": [{"name": "All", "path": "."}], "ignore": ["drop"]}
    )

    assert [found.rel_path for found in walk_library(root, config)] == ["Keep/a.pdf"]


def test_a_symlink_leading_out_of_the_library_is_skipped(tmp_path: Path) -> None:
    root = tmp_path / "library"
    outside = tmp_path / "outside"
    (root / "Zines").mkdir(parents=True)
    outside.mkdir()
    (outside / "secret.pdf").write_bytes(b"%PDF-1.7\n")
    (root / "Zines" / "here.pdf").write_bytes(b"%PDF-1.7\n")
    (root / "Zines" / "there.pdf").symlink_to(outside / "secret.pdf")
    (root / "escape").symlink_to(outside, target_is_directory=True)
    config = PaperstandConfig.model_validate({"libraries": [{"name": "All", "path": "."}]})

    assert [found.rel_path for found in walk_library(root, config)] == ["Zines/here.pdf"]


def test_a_symlink_staying_inside_the_library_is_followed(tmp_path: Path) -> None:
    root = tmp_path / "library"
    (root / "Real").mkdir(parents=True)
    (root / "Real" / "a.pdf").write_bytes(b"%PDF-1.7\n")
    (root / "Alias").symlink_to(root / "Real", target_is_directory=True)
    config = PaperstandConfig.model_validate({"libraries": [{"name": "All", "path": "."}]})

    assert [found.rel_path for found in walk_library(root, config)] == [
        "Alias/a.pdf",
        "Real/a.pdf",
    ]


def test_a_missing_root_yields_nothing(tmp_path: Path) -> None:
    config = PaperstandConfig.model_validate({"libraries": [{"name": "All", "path": "."}]})

    assert list(walk_library(tmp_path / "absent", config)) == []
    assert top_level_folders(tmp_path / "absent") == []


def test_top_level_folders_lists_directories_only(sample_library: SampleLibrary) -> None:
    folders = top_level_folders(sample_library.root)

    assert {"Newspapers", "Magazines", "Zines", "comics"} <= set(folders)
    assert folders == sorted(folders)


# --------------------------------------------------------------- publication.yml


def _config(root: Path) -> PaperstandConfig:
    return PaperstandConfig.model_validate({"libraries": [{"name": "All", "path": "."}]})


def test_a_declaring_folder_sets_publication_dir_on_its_own_files(tmp_path: Path) -> None:
    root = tmp_path / "library"
    (root / "Corriere del Ponte").mkdir(parents=True)
    (root / "Corriere del Ponte" / "publication.yml").write_text("id: corriere-del-ponte\n")
    (root / "Corriere del Ponte" / "a.pdf").write_bytes(b"%PDF-1.7\n")
    walk = Walk(root, _config(root))

    found = list(walk)

    assert len(found) == 1
    assert found[0].publication_dir == "Corriere del Ponte"
    assert walk.publications == {"Corriere del Ponte": "Corriere del Ponte/publication.yml"}


def test_a_declaration_reaches_a_nested_folder(tmp_path: Path) -> None:
    root = tmp_path / "library"
    (root / "Corriere del Ponte" / "2026").mkdir(parents=True)
    (root / "Corriere del Ponte" / "publication.yml").write_text("id: corriere-del-ponte\n")
    (root / "Corriere del Ponte" / "2026" / "a.pdf").write_bytes(b"%PDF-1.7\n")
    walk = Walk(root, _config(root))

    found = list(walk)

    assert found[0].rel_path == "Corriere del Ponte/2026/a.pdf"
    assert found[0].publication_dir == "Corriere del Ponte"


def test_a_nested_declaration_wins_over_its_ancestor(tmp_path: Path) -> None:
    """Nested folders: the nearest declaring one wins, by construction — the
    walk only ever passes down its own current declaration as it recurses."""
    root = tmp_path / "library"
    (root / "Newspapers" / "Corriere del Ponte").mkdir(parents=True)
    (root / "Newspapers" / "publication.yml").write_text("id: newspapers-wide\n")
    (root / "Newspapers" / "Corriere del Ponte" / "publication.yml").write_text(
        "id: corriere-del-ponte\n"
    )
    (root / "Newspapers" / "outer.pdf").write_bytes(b"%PDF-1.7\n")
    (root / "Newspapers" / "Corriere del Ponte" / "inner.pdf").write_bytes(b"%PDF-1.7\n")
    walk = Walk(root, _config(root))

    found = {found.rel_path: found.publication_dir for found in walk}

    assert found["Newspapers/outer.pdf"] == "Newspapers"
    assert found["Newspapers/Corriere del Ponte/inner.pdf"] == "Newspapers/Corriere del Ponte"
    assert walk.publications == {
        "Newspapers": "Newspapers/publication.yml",
        "Newspapers/Corriere del Ponte": "Newspapers/Corriere del Ponte/publication.yml",
    }


def test_an_at_folder_yml_is_never_noticed(tmp_path: Path) -> None:
    """A folder the walk already skips is never scanned for a publication.yml."""
    root = tmp_path / "library"
    (root / "Newspapers" / "@eaDir").mkdir(parents=True)
    (root / "Newspapers" / "@eaDir" / "publication.yml").write_text("id: hidden\n")
    (root / "Newspapers" / "a.pdf").write_bytes(b"%PDF-1.7\n")
    walk = Walk(root, _config(root))

    found = list(walk)

    assert found[0].publication_dir is None
    assert walk.publications == {}


def test_a_yml_at_the_library_root_is_ignored_with_a_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    root = tmp_path / "library"
    root.mkdir()
    (root / "publication.yml").write_text("id: whole-library\n")
    (root / "Zines").mkdir()
    (root / "Zines" / "a.pdf").write_bytes(b"%PDF-1.7\n")
    walk = Walk(root, _config(root))

    with caplog.at_level(logging.WARNING, logger="paperstand"):
        found = list(walk)

    assert found[0].publication_dir is None
    assert walk.publications == {}
    assert any("library root" in record.message for record in caplog.records)


def test_the_walk_opens_no_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The walk notices a publication.yml by name alone; it never reads one —
    reading and validating is `paperstand.publication`'s job."""
    root = tmp_path / "library"
    (root / "Corriere del Ponte").mkdir(parents=True)
    (root / "Corriere del Ponte" / "publication.yml").write_text("id: corriere-del-ponte\n")
    (root / "Corriere del Ponte" / "a.pdf").write_bytes(b"%PDF-1.7\n")

    def forbidden(*args: object, **kwargs: object) -> object:
        raise AssertionError("the walk must never open a file")

    monkeypatch.setattr(Path, "read_text", forbidden)
    monkeypatch.setattr(Path, "read_bytes", forbidden)
    monkeypatch.setattr("builtins.open", forbidden)

    found = list(Walk(root, _config(root)))

    assert found[0].publication_dir == "Corriere del Ponte"
