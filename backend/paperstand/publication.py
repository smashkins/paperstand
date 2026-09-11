"""Reading ``publication.yml``, the declaration that turns a folder into a title.

Additive to every layout Paperstand already reads: a folder holding a
``publication.yml`` anywhere under a library is a *declared publication*, and
every PDF beneath it, at any depth, belongs to that title with the yml's
metadata. Every key is optional; ``{}`` or an empty file is the minimal
declaration, "this folder is a publication, defaults everywhere". A malformed
yml is never a failed scan: :class:`PublicationIndex` logs one warning naming
the file and the offending key, and the folder is then treated exactly as an
undeclared one.

This module only reads the file; it never writes. The walker
(:mod:`paperstand.scanner.walker`) only notices *that* a folder has a
``publication.yml``, never opens it — only :class:`PublicationIndex` does that,
so the cost of parsing one is paid once per folder per scan, not once per file.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from paperstand.config import LibraryConfig, PaperstandConfig, validation_message
from paperstand.logging import get_logger
from paperstand.parsing.normalize import slugify

log = get_logger(__name__)

#: Name of the declaration file, looked for in every folder of a library.
PUBLICATION_FILE = "publication.yml"

PublicationKind = Literal["newspaper", "magazine"]
PublicationFrequency = Literal["daily", "weekly", "monthly", "irregular"]
IssueKey = Literal["date", "number", "date+number"]

#: A language tag: 2-3 letter primary subtag, optional 2-8 alphanumeric subtags.
_LANGUAGE_TAG = re.compile(r"^[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})*$")


class PublicationError(ValueError):
    """A ``publication.yml`` could not be loaded."""


class PublicationConfig(BaseModel):
    """The validated contents of one ``publication.yml``.

    Every key is optional: the file is a set of overrides, not a full
    description, and ``{}`` declares nothing beyond "this is a publication".
    """

    model_config = ConfigDict(extra="forbid")

    id: str | None = None
    """Slug identifying the title. Defaults to ``slugify(title)``."""

    title: str | None = None
    """Display title. Defaults to the declaring folder's basename."""

    kind: PublicationKind | None = None
    """Defaults to the library's own kind."""

    frequency: PublicationFrequency | None = None

    language: str | None = None
    """A language tag, e.g. ``it`` or ``en-GB``."""

    issue_key: IssueKey = "date+number"
    """What groups issues into duplicates of one another: today's default."""

    supplements: list[str] = []
    """Variants this title declares; an undeclared variant is still accepted."""

    parent: str | None = None
    """Slug of a parent publication, e.g. a regional edition's main title."""

    @field_validator("id", "parent")
    @classmethod
    def _check_slug(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if slugify(value) != value:
            raise ValueError(f"{value!r} is not a valid slug")
        return value

    @field_validator("language")
    @classmethod
    def _check_language(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not _LANGUAGE_TAG.fullmatch(value):
            raise ValueError(f"{value!r} is not a valid language tag")
        return value

    @field_validator("supplements")
    @classmethod
    def _check_supplements(cls, value: list[str]) -> list[str]:
        stripped = [item.strip() for item in value]
        for item in stripped:
            if not item:
                raise ValueError("a supplement cannot be empty")
            if " - " in item:
                raise ValueError(f"supplement {item!r} cannot contain ' - '")
        if len(set(stripped)) != len(stripped):
            raise ValueError("supplements must be unique")
        return stripped


def load_publication(path: Path) -> PublicationConfig:
    """Load and validate one ``publication.yml``.

    Same idiom as :func:`paperstand.config.load_config`: a message shaped
    ``<path>: <key>: <msg>``, so a caller can log or raise it verbatim.
    """
    try:
        text = path.read_text("utf-8")
    except UnicodeDecodeError as error:
        raise PublicationError(f"{path}: not valid UTF-8: {error}") from error
    except OSError as error:
        raise PublicationError(f"{path}: cannot be read: {error}") from error
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as error:
        raise PublicationError(f"{path}: not valid YAML: {error}") from error
    if raw is None:
        raw = {}
    if not isinstance(raw, Mapping):
        raise PublicationError(f"{path}: expected a mapping at the top level")
    try:
        return PublicationConfig.model_validate(dict(raw))
    except ValidationError as error:
        raise PublicationError(f"{path}: {validation_message(error)}") from error


@dataclass(frozen=True, slots=True)
class DeclaredPublication:
    """A ``publication.yml`` resolved against the folder that declares it."""

    folder: str
    """The declaring folder, relative to the library root (POSIX, no trailing ``/``)."""

    config: PublicationConfig

    @property
    def title(self) -> str:
        """The display title: the configured one, or the folder's own name."""
        return self.config.title or PurePosixPath(self.folder).name

    @property
    def slug(self) -> str:
        """The identifying slug: the configured one, or derived from the title."""
        return self.config.id or slugify(self.title)

    def kind_for(self, library_kind: str) -> str:
        """This publication's kind, falling back to the library's own."""
        return self.config.kind or library_kind

    def declares(self, variant: str) -> bool:
        """Whether ``variant`` is one of this publication's declared supplements."""
        return variant in self.config.supplements


class PublicationIndex:
    """Cached, warn-once reading of every ``publication.yml`` under one root.

    ``root`` is the library root a relative folder is resolved against — the
    same root :class:`~paperstand.scanner.walker.Walk` walks. A folder is
    read at most once per index: an invalid yml logs a single warning and is
    then cached as "undeclared", so a broken file costs one log line per scan,
    not one per file beneath it.
    """

    def __init__(self, root: Path) -> None:
        self.root = root
        self._cache: dict[str, DeclaredPublication | None] = {}

    def get(self, folder: str) -> DeclaredPublication | None:
        """The declared publication at ``folder`` (relative to the root), if any."""
        cached = self._cache.get(folder)
        if folder in self._cache:
            return cached
        result = self._load(folder)
        self._cache[folder] = result
        return result

    def resolve(
        self,
        publication_dir: str | None,
        library: LibraryConfig,
        config: PaperstandConfig,
    ) -> DeclaredPublication | None:
        """The declaration that applies to a file under ``publication_dir``.

        ``publication_dir`` is the nearest ancestor the walk noticed a
        ``publication.yml`` in — whether or not that one turned out to be
        valid, since the walker only records that a file is *there*, never
        opens it. From there, ancestors are walked upward through :meth:`get`
        (cached; it stats the folder) until one loads, so a folder whose own
        yml is malformed still inherits a valid declaration from further up
        instead of losing it outright.

        A declaration found this way only applies inside the library it was
        declared in: when the folder that actually holds it belongs to no
        configured library, or to a library other than ``library``, it
        contributes nothing here — an explicitly configured nested library
        (``Collection/Magazines`` inside ``Collection``) never inherits a
        declaration that belongs to the library above it.
        """
        if not publication_dir:
            return None
        parts = PurePosixPath(publication_dir).parts
        declared: DeclaredPublication | None = None
        for depth in range(len(parts), 0, -1):
            found = self.get("/".join(parts[:depth]))
            if found is not None:
                declared = found
                break
        if declared is None:
            return None
        owner = config.library_for(declared.folder)
        if owner is None or owner.name != library.name:
            return None
        return declared

    def nearest(self, rel_path: str) -> DeclaredPublication | None:
        """The nearest declaring ancestor of a file, probing the folders on disk.

        Used by ``parse-explain``, which explains a single file and has no
        reason to pay for a full walk first. The scanner never calls this: it
        already knows the nearest declaring folder from
        :attr:`~paperstand.scanner.walker.Walk.publications`.
        """
        parts = PurePosixPath(rel_path).parts[:-1]
        for depth in range(len(parts), 0, -1):
            found = self.get("/".join(parts[:depth]))
            if found is not None:
                return found
        return None

    def load_all(self, folders: Iterable[str]) -> str:
        """Load every folder in ``folders``, returning a digest of the set.

        Every folder is read through :meth:`get`, so validation and the
        warn-once logging happen here too. The digest is a sha256 over the
        sorted ``folder\\0sha256(raw bytes)`` pairs of every folder whose
        ``publication.yml`` exists on disk — hashed **whether or not it is
        valid**, so a yml that stays broken but changes still moves the
        digest, and fixing one is exactly such a byte-for-byte change. The
        library root is skipped: a yml there is ignored, not declared, and
        contributes nothing worth re-parsing anything over.
        """
        parts: list[str] = []
        for folder in sorted(set(folders)):
            if not folder:
                continue
            self.get(folder)
            path = self.root / folder / PUBLICATION_FILE
            try:
                raw = path.read_bytes()
            except OSError:
                continue
            parts.append(f"{folder}\0{hashlib.sha256(raw).hexdigest()}")
        return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()

    # ----------------------------------------------------------------- private

    def _load(self, folder: str) -> DeclaredPublication | None:
        path = self.root / folder / PUBLICATION_FILE if folder else self.root / PUBLICATION_FILE
        if not folder:
            if path.is_file():
                log.warning("%s: a publication.yml at the library root is ignored", path)
            return None
        if not path.is_file():
            return None
        try:
            config = load_publication(path)
        except PublicationError as error:
            log.warning(str(error))
            return None
        return DeclaredPublication(folder=folder, config=config)
