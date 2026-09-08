"""The library walker: what it yields, and what it refuses to look at."""

from __future__ import annotations

import os
from pathlib import Path

from paperstand.config import PaperstandConfig, load_config
from paperstand.scanner.walker import top_level_folders, walk_library
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
