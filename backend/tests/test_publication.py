"""``publication.yml``: loading, the index's caching and its digest."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from paperstand.publication import (
    PUBLICATION_FILE,
    DeclaredPublication,
    PublicationConfig,
    PublicationError,
    PublicationIndex,
    load_publication,
)


def write(root: Path, folder: str, content: str) -> Path:
    """Write ``publication.yml`` under ``root/folder`` and return its path."""
    directory = root / folder if folder else root
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / PUBLICATION_FILE
    path.write_text(content, encoding="utf-8")
    return path


# ------------------------------------------------------------------- loading


def test_an_empty_file_is_the_minimal_declaration(tmp_path: Path) -> None:
    path = write(tmp_path, "Corriere del Ponte", "")
    config = load_publication(path)
    assert config == PublicationConfig()


def test_an_empty_mapping_is_the_minimal_declaration(tmp_path: Path) -> None:
    path = write(tmp_path, "Corriere del Ponte", "{}\n")
    config = load_publication(path)
    assert config == PublicationConfig()


def test_every_field(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        "Corriere del Ponte",
        """
        id: corriere-del-ponte
        title: Corriere del Ponte
        kind: newspaper
        frequency: daily
        language: it
        issue_key: date
        supplements: [Weekend]
        parent: la-gazzetta-del-lago
        """,
    )
    config = load_publication(path)
    assert config.id == "corriere-del-ponte"
    assert config.title == "Corriere del Ponte"
    assert config.kind == "newspaper"
    assert config.frequency == "daily"
    assert config.language == "it"
    assert config.issue_key == "date"
    assert config.supplements == ["Weekend"]
    assert config.parent == "la-gazzetta-del-lago"


def test_issue_key_defaults_to_date_plus_number(tmp_path: Path) -> None:
    path = write(tmp_path, "Corriere del Ponte", "{}\n")
    assert load_publication(path).issue_key == "date+number"


def test_an_unknown_key_names_the_file_and_the_key(tmp_path: Path) -> None:
    path = write(tmp_path, "Corriere del Ponte", "frequenzy: monthly\n")
    with pytest.raises(PublicationError) as error:
        load_publication(path)
    message = str(error.value)
    assert str(path) in message
    assert "frequenzy" in message


def test_not_valid_utf8(tmp_path: Path) -> None:
    directory = tmp_path / "Corriere del Ponte"
    directory.mkdir()
    path = directory / PUBLICATION_FILE
    path.write_bytes(b"\xff\xfe")
    with pytest.raises(PublicationError, match="not valid UTF-8"):
        load_publication(path)


def test_cannot_be_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = write(tmp_path, "Corriere del Ponte", "id: corriere-del-ponte\n")

    def forbidden(self: Path, encoding: str) -> str:
        raise OSError("permission denied")

    monkeypatch.setattr(Path, "read_text", forbidden)
    with pytest.raises(PublicationError, match="cannot be read"):
        load_publication(path)


def test_not_valid_yaml(tmp_path: Path) -> None:
    path = write(tmp_path, "Corriere del Ponte", "id: [unterminated\n")
    with pytest.raises(PublicationError, match="not valid YAML"):
        load_publication(path)


def test_not_a_mapping(tmp_path: Path) -> None:
    path = write(tmp_path, "Corriere del Ponte", "- one\n- two\n")
    with pytest.raises(PublicationError, match="expected a mapping"):
        load_publication(path)


@pytest.mark.parametrize(
    ("content", "match"),
    [
        ("id: Not A Slug\n", "id"),
        ("parent: Not A Slug\n", "parent"),
        ("language: english\n", "language"),
        ("language: 1t\n", "language"),
        ("kind: tabloid\n", "kind"),
        ("frequency: fortnightly\n", "frequency"),
        ("issue_key: title\n", "issue_key"),
        ("supplements: ['']\n", "supplement"),
        ("supplements: ['Weekend - Extra']\n", "supplement"),
        ("supplements: [Weekend, Weekend]\n", "unique"),
    ],
)
def test_an_invalid_field_names_the_key(tmp_path: Path, content: str, match: str) -> None:
    path = write(tmp_path, "Corriere del Ponte", content)
    with pytest.raises(PublicationError, match=match):
        load_publication(path)


def test_supplements_are_stripped() -> None:
    config = PublicationConfig.model_validate({"supplements": ["  Weekend  "]})
    assert config.supplements == ["Weekend"]


# --------------------------------------------------------------- DeclaredPublication


def test_title_and_slug_are_derived_when_not_given() -> None:
    declared = DeclaredPublication(
        folder="Newspapers/Corriere del Ponte", config=PublicationConfig()
    )
    assert declared.title == "Corriere del Ponte"
    assert declared.slug == "corriere-del-ponte"


def test_title_and_slug_come_from_the_config_when_given() -> None:
    declared = DeclaredPublication(
        folder="Newspapers/La Gazzetta del Lago (Valdora)",
        config=PublicationConfig.model_validate(
            {"id": "la-gazzetta-del-lago-valdora", "title": "La Gazzetta del Lago Valdora"}
        ),
    )
    assert declared.title == "La Gazzetta del Lago Valdora"
    assert declared.slug == "la-gazzetta-del-lago-valdora"


def test_kind_for_falls_back_to_the_library() -> None:
    declared = DeclaredPublication(folder="Magazines/Bright Meadows", config=PublicationConfig())
    assert declared.kind_for("magazine") == "magazine"
    declared_kind = DeclaredPublication(
        folder="Magazines/Bright Meadows",
        config=PublicationConfig.model_validate({"kind": "newspaper"}),
    )
    assert declared_kind.kind_for("magazine") == "newspaper"


def test_declares() -> None:
    declared = DeclaredPublication(
        folder="Newspapers/Corriere del Ponte",
        config=PublicationConfig.model_validate({"supplements": ["Weekend"]}),
    )
    assert declared.declares("Weekend") is True
    assert declared.declares("Speciale") is False


# ------------------------------------------------------------------- the index


def test_index_reads_a_declared_folder(tmp_path: Path) -> None:
    write(tmp_path, "Corriere del Ponte", "id: corriere-del-ponte\n")
    index = PublicationIndex(tmp_path)
    declared = index.get("Corriere del Ponte")
    assert declared is not None
    assert declared.slug == "corriere-del-ponte"


def test_index_returns_none_for_an_undeclared_folder(tmp_path: Path) -> None:
    index = PublicationIndex(tmp_path)
    assert index.get("Nothing Here") is None


def test_index_warns_once_and_caches(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    write(tmp_path, "Circuito", "frequenzy: monthly\n")
    index = PublicationIndex(tmp_path)
    with caplog.at_level(logging.WARNING, logger="paperstand"):
        first = index.get("Circuito")
        second = index.get("Circuito")
    assert first is None
    assert second is None
    warnings = [record for record in caplog.records if record.levelno >= logging.WARNING]
    assert len(warnings) == 1
    assert "frequenzy" in warnings[0].message


def test_index_warns_once_on_undecodable_bytes(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    directory = tmp_path / "Corriere del Ponte"
    directory.mkdir()
    (directory / PUBLICATION_FILE).write_bytes(b"\xff\xfe")
    index = PublicationIndex(tmp_path)
    with caplog.at_level(logging.WARNING, logger="paperstand"):
        first = index.get("Corriere del Ponte")
        second = index.get("Corriere del Ponte")
    assert first is None
    assert second is None
    warnings = [record for record in caplog.records if record.levelno >= logging.WARNING]
    assert len(warnings) == 1
    assert "not valid UTF-8" in warnings[0].message


def test_a_yml_at_the_library_root_is_ignored_with_a_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    write(tmp_path, "", "id: whole-library\n")
    index = PublicationIndex(tmp_path)
    with caplog.at_level(logging.WARNING, logger="paperstand"):
        declared = index.get("")
    assert declared is None
    assert any("library root" in record.message for record in caplog.records)


def test_nearest_probes_ancestors_on_disk(tmp_path: Path) -> None:
    write(tmp_path, "Newspapers/Corriere del Ponte", "id: corriere-del-ponte\n")
    (tmp_path / "Newspapers/Corriere del Ponte/2026").mkdir(parents=True)
    index = PublicationIndex(tmp_path)
    found = index.nearest("Newspapers/Corriere del Ponte/2026/Corriere del Ponte - 2026-09-06.pdf")
    assert found is not None
    assert found.folder == "Newspapers/Corriere del Ponte"


def test_nearest_returns_none_with_no_declaration(tmp_path: Path) -> None:
    (tmp_path / "Newspapers/Cronaca").mkdir(parents=True)
    index = PublicationIndex(tmp_path)
    found = index.nearest("Newspapers/Cronaca/Cronaca - 2026-09-06.pdf")
    assert found is None


def test_nearest_wins_the_closest_declaration(tmp_path: Path) -> None:
    """Nested folders: the nearest declaring ancestor wins, by construction."""
    write(tmp_path, "Newspapers", "id: newspapers-wide\n")
    write(tmp_path, "Newspapers/Corriere del Ponte", "id: corriere-del-ponte\n")
    index = PublicationIndex(tmp_path)
    found = index.nearest("Newspapers/Corriere del Ponte/Corriere del Ponte - 2026-09-06.pdf")
    assert found is not None
    assert found.slug == "corriere-del-ponte"


# --------------------------------------------------------------------- the digest


def test_digest_is_stable_across_calls(tmp_path: Path) -> None:
    write(tmp_path, "Corriere del Ponte", "id: corriere-del-ponte\n")
    index = PublicationIndex(tmp_path)
    first = index.load_all(["Corriere del Ponte"])
    second = PublicationIndex(tmp_path).load_all(["Corriere del Ponte"])
    assert first == second


def test_digest_changes_when_a_yml_changes(tmp_path: Path) -> None:
    write(tmp_path, "Corriere del Ponte", "id: corriere-del-ponte\n")
    before = PublicationIndex(tmp_path).load_all(["Corriere del Ponte"])
    write(tmp_path, "Corriere del Ponte", "id: corriere-del-ponte\nlanguage: it\n")
    after = PublicationIndex(tmp_path).load_all(["Corriere del Ponte"])
    assert before != after


def test_digest_changes_when_a_broken_yml_is_fixed(tmp_path: Path) -> None:
    """A yml that stays invalid but changes byte for byte still moves the
    digest: the fast phase must re-parse every path under it either way."""
    write(tmp_path, "Circuito", "frequenzy: monthly\n")
    before = PublicationIndex(tmp_path).load_all(["Circuito"])
    write(tmp_path, "Circuito", "frequenzy: weekly\n")
    after = PublicationIndex(tmp_path).load_all(["Circuito"])
    assert before != after


def test_digest_ignores_an_undeclared_folder(tmp_path: Path) -> None:
    (tmp_path / "Zines").mkdir()
    index = PublicationIndex(tmp_path)
    assert index.load_all(["Zines"]) == index.load_all([])


def test_digest_is_order_independent(tmp_path: Path) -> None:
    write(tmp_path, "Corriere del Ponte", "id: corriere-del-ponte\n")
    write(tmp_path, "Bright Meadows", "id: bright-meadows\n")
    index = PublicationIndex(tmp_path)
    assert index.load_all(["Corriere del Ponte", "Bright Meadows"]) == index.load_all(
        ["Bright Meadows", "Corriere del Ponte"]
    )


# ------------------------------------------------------------------------ no write


def test_reading_a_publication_never_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write(tmp_path, "Corriere del Ponte", "id: corriere-del-ponte\nsupplements: [Weekend]\n")
    write(tmp_path, "Circuito", "frequenzy: monthly\n")

    def forbidden(*args: object, **kwargs: object) -> object:
        raise AssertionError("reading publication.yml must never write")

    monkeypatch.setattr(Path, "write_text", forbidden)
    monkeypatch.setattr(Path, "write_bytes", forbidden)
    monkeypatch.setattr(Path, "unlink", forbidden)
    monkeypatch.setattr(Path, "mkdir", forbidden)

    index = PublicationIndex(tmp_path)
    index.get("Corriere del Ponte")
    index.get("Circuito")
    index.nearest("Corriere del Ponte/2026/x.pdf")
    index.load_all(["Corriere del Ponte", "Circuito"])
