"""Runtime settings and the ``paperstand.yml`` configuration.

Two different things live here. :class:`Settings` is the container contract: the
environment variables, every Paperstand-specific one prefixed with
``PAPERSTAND_`` (``PORT`` and ``TZ`` keep their conventional names so that
container platforms can set them). :class:`PaperstandConfig` is the optional
``paperstand.yml``: the libraries, their titles, the parser profiles and the
ignore list. Both are optional — with no configuration at all, every top-level
folder of the library root becomes a library and titles are derived from the
names.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping, Sequence
from fnmatch import fnmatch
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PrivateAttr,
    ValidationError,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict

from paperstand.logging import get_logger
from paperstand.parsing.normalize import slugify
from paperstand.parsing.profile import DEFAULT_PROFILE, Profile, ProfileError, load_profiles

log = get_logger(__name__)


class Settings(BaseSettings):
    """Application settings.

    Values come from the environment (and from a local ``.env`` file during
    development). Every one of them is read by something; a setting that does
    nothing does not belong in the container contract.
    ``docs/configuration.md`` is the reference.

    The settings, in the order they are declared: ``library``, ``inbox``, ``data``,
    ``config``, ``static``, ``scan_on_start``, ``scan_interval``, ``missing_grace_days``,
    ``cover_workers``, ``render_workers``, ``page_cache_max_mb``, ``base_url``,
    ``log_level``, ``trusted_proxies``, ``port``, ``host``, ``tz``.
    """

    model_config = SettingsConfigDict(
        env_prefix="PAPERSTAND_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    library: Path = Path("/library")
    """Read-only root of the PDF collection."""

    inbox: Path = Path("/inbox")
    """Writable folder ``paperstand organize`` imports PDFs from, into the library."""

    data: Path = Path("/data")
    """Writable directory for the database and the caches."""

    config: Path | None = None
    """Optional ``paperstand.yml``. Defaults to ``<data>/paperstand.yml``."""

    static: Path | None = None
    """Directory holding the built frontend. Unset means "no frontend"."""

    scan_on_start: bool = True
    scan_interval: int = 900

    missing_grace_days: int = 7
    """How long a catalogued issue whose file has vanished is kept, hidden,
    before its row is forgotten. ``0`` restores the earlier behaviour: gone
    the first scan that does not find it. A negative value is clamped to
    zero rather than rejected — there is no sane reading of "keep it for
    minus three days"."""

    @field_validator("missing_grace_days")
    @classmethod
    def _clamp_missing_grace_days(cls, value: int) -> int:
        return max(0, value)

    cover_workers: int = 2
    render_workers: int = 2
    page_cache_max_mb: int = 2048
    base_url: str | None = None
    log_level: str = "info"

    trusted_proxies: str = "127.0.0.1"
    """Clients whose ``X-Forwarded-*`` headers are believed.

    A comma-separated list of IP addresses or CIDR blocks, passed to Uvicorn as
    ``forwarded_allow_ips``. The default trusts loopback only, which is right when
    the port is reached directly. Behind a reverse proxy set it to the proxy's
    address, or to ``*`` when the proxy is the only thing that can reach the port.
    """

    port: int = Field(default=8080, validation_alias="PORT")
    host: str = "0.0.0.0"
    tz: str | None = Field(default=None, validation_alias="TZ")

    @property
    def config_path(self) -> Path:
        """Path of the optional YAML configuration file."""
        return self.config if self.config is not None else self.data / "paperstand.yml"

    @property
    def db_path(self) -> Path:
        """Path of the SQLite database."""
        return self.data / "paperstand.db"

    @property
    def cache_path(self) -> Path:
        """Root of the on-disk caches (covers, rendered pages)."""
        return self.data / "cache"

    @property
    def organizer_report_path(self) -> Path:
        """Path of the organizer's last-run report, written after every run."""
        return self.data / ORGANIZER_REPORT

    @property
    def migration_reports_path(self) -> Path:
        """Folder holding one report per ``migrate --apply`` run that moved a file."""
        return self.data / MIGRATION_REPORTS

    @property
    def scan_trigger_path(self) -> Path:
        """Path of the trigger file the scheduler polls for.

        Touched by the organizer after an apply run that moved at least one
        file, and by hand with ``touch``: a third way to ask for a scan, next
        to the Settings button and ``POST /api/scan``, that needs no server
        URL.
        """
        return self.data / SCAN_TRIGGER


#: Where the organizer writes what its last run did, relative to ``data``.
ORGANIZER_REPORT = "organizer/last-run.json"

#: Folder holding one timestamped report per ``migrate --apply`` run, relative to ``data``.
MIGRATION_REPORTS = "organizer/migrations"

#: The scheduler's poll trigger, relative to ``data``.
SCAN_TRIGGER = "scan.request"


def get_settings() -> Settings:
    """Build a fresh :class:`Settings` instance from the current environment."""
    return Settings()


#: Folder names that make an auto-discovered library one of dailies.
NEWSPAPER_FOLDER = re.compile(
    r"newspapers?|dailies|giornali|quotidiani|journaux|zeitungen|periodicos|diarios",
    re.IGNORECASE,
)

#: Prefixes that make a *folder* invisible: thumbnail caches, hidden folders,
#: recycle bins. They are skipped everywhere, in every library, always.
IGNORED_PREFIXES: tuple[str, ...] = ("@", ".", "#")

#: Prefixes that make a *file* invisible. ``@`` is deliberately not one of them:
#: on a file, a leading ``@something_`` is a handle prefix — a naming shape the
#: parser strips — while on a folder it is metadata nobody wants catalogued.
IGNORED_FILE_PREFIXES: tuple[str, ...] = (".", "#")

LibraryKind = Literal["newspaper", "magazine"]


class ConfigError(ValueError):
    """The configuration file could not be loaded."""


class TitleConfig(BaseModel):
    """One configured title, with its alternative spellings.

    ``pattern`` is an escape hatch for a title whose files need a shape of their
    own: it is tried, on the spaced form, before the profile's generic rules.
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    aliases: list[str] = []
    pattern: str | None = None

    @field_validator("pattern")
    @classmethod
    def _check_pattern(cls, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            re.compile(value)
        except re.error as error:
            raise ValueError(f"title pattern '{value}' is not a valid regex: {error}") from error
        return value


class LibraryConfig(BaseModel):
    """One library: a folder under the library root, and how to read it."""

    model_config = ConfigDict(extra="forbid")

    name: str
    path: str
    kind: LibraryKind = "magazine"
    parser: str = DEFAULT_PROFILE
    titles: list[TitleConfig] = []

    @field_validator("titles", mode="before")
    @classmethod
    def _accept_plain_names(cls, value: object) -> object:
        """Allow ``- Corriere del Ponte`` as well as ``- {name: …}``."""
        if isinstance(value, list):
            return [{"name": item} if isinstance(item, str) else item for item in value]
        return value

    @property
    def title_names(self) -> tuple[str, ...]:
        """Canonical names of the configured titles."""
        return tuple(title.name for title in self.titles)


class PaperstandConfig(BaseModel):
    """The contents of ``paperstand.yml``."""

    model_config = ConfigDict(extra="forbid")

    libraries: list[LibraryConfig] = []
    parsers: dict[str, dict[str, Any]] = {}
    ignore: list[str] = []

    _profiles: dict[str, Profile] = PrivateAttr(default_factory=dict)

    @model_validator(mode="after")
    def _check_library_identifiers(self) -> PaperstandConfig:
        """Refuse two libraries whose names reduce to the same identifier.

        The identifier is what rows, cache paths and URLs are keyed by, so
        ``Città`` and ``Citta`` side by side are not a detail to paper over with
        a suffix: one of them would silently swallow the other's files.
        """
        seen: dict[str, str] = {}
        for library in self.libraries:
            slug = slugify(library.name)
            first = seen.get(slug)
            if first is not None:
                raise ValueError(
                    f"libraries {first!r} and {library.name!r} both reduce to the "
                    f"identifier {slug!r}; give one of them a different name"
                )
            seen[slug] = library.name
        return self

    @model_validator(mode="after")
    def _check_profiles(self) -> PaperstandConfig:
        profiles = load_profiles(self.parsers)
        self._profiles = profiles
        for library in self.libraries:
            if library.parser not in profiles:
                known = ", ".join(sorted(profiles))
                raise ValueError(
                    f"library {library.name!r} uses parser {library.parser!r}, "
                    f"which is not defined (known profiles: {known})"
                )
        return self

    def profiles(self) -> dict[str, Profile]:
        """Every profile available: the bundled one plus the configured ones.

        Resolved once, when the configuration is validated: the parser compiled
        from a profile is cached by identity, and a fresh profile object per file
        would throw that cache away.
        """
        if not self._profiles:
            self._profiles = load_profiles(self.parsers)
        return self._profiles

    def profile_for(self, library: LibraryConfig) -> Profile:
        """The resolved profile a library parses with."""
        return self.profiles()[library.parser]

    def library_for(self, rel_path: str) -> LibraryConfig | None:
        """The library a path — relative to the library root — belongs to."""
        best: LibraryConfig | None = None
        for library in self.libraries:
            prefix = library.path.strip("/")
            if not prefix:
                continue
            if not (rel_path == prefix or rel_path.startswith(prefix + "/")):
                continue
            if best is None or len(prefix) > len(best.path.strip("/")):
                best = library
        return best

    def is_ignored(self, name: str, *, directory: bool = True) -> bool:
        """Whether a file or folder name is skipped everywhere."""
        return is_ignored(name, self.ignore, directory=directory)

    @property
    def config_hash(self) -> str:
        """Stable hash of the configuration, so a scan can tell it changed."""
        canonical = json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def is_ignored(name: str, ignore: Sequence[str] = (), *, directory: bool = True) -> bool:
    """Whether a name is skipped: a hidden prefix, or a configured pattern.

    Folders starting with ``@``, ``.`` or ``#`` are skipped; files only when
    they start with ``.`` or ``#``, because a file called ``@handle_Title.pdf``
    is a periodical with a prefix on it, not metadata.
    """
    prefixes = IGNORED_PREFIXES if directory else IGNORED_FILE_PREFIXES
    if not name or name.startswith(prefixes):
        return True
    lowered = name.lower()
    return any(fnmatch(lowered, pattern.lower()) for pattern in ignore)


def guess_kind(folder_name: str) -> LibraryKind:
    """Guess a library's kind from its folder name."""
    return "newspaper" if NEWSPAPER_FOLDER.fullmatch(folder_name) else "magazine"


def discover_libraries(
    folder_names: Iterable[str],
    ignore: Sequence[str] = (),
) -> list[LibraryConfig]:
    """Auto-discovery: one library per top-level folder, no configuration.

    The folder names are injected rather than read from disk, so discovery is
    testable — and usable — without a library to walk.
    """
    libraries = []
    taken: set[str] = set()
    for name in sorted(folder_names):
        if is_ignored(name, ignore):
            continue
        slug = slugify(name)
        if slug in taken:
            # Nobody chose these names for Paperstand, so a collision between two
            # folders is skipped with a warning rather than raised: a library that
            # cannot be named is still no reason to refuse to start.
            log.warning("skipping the folder %r: another one already uses the id %r", name, slug)
            continue
        taken.add(slug)
        libraries.append(LibraryConfig(name=name, path=name, kind=guess_kind(name)))
    return libraries


def config_from_folders(
    folder_names: Iterable[str],
    ignore: Sequence[str] = (),
) -> PaperstandConfig:
    """The configuration Paperstand uses when there is no ``paperstand.yml``."""
    return PaperstandConfig(
        libraries=discover_libraries(folder_names, ignore),
        ignore=list(ignore),
    )


def load_config(
    path: Path | None,
    *,
    folder_names: Iterable[str] | None = None,
) -> PaperstandConfig:
    """Load ``paperstand.yml``, or auto-discover from ``folder_names``.

    A missing file is not an error: it means "figure it out from the folders".
    A malformed one is, and says which file and which key is wrong.
    """
    if path is None or not path.is_file():
        return config_from_folders(folder_names or [])
    try:
        raw = yaml.safe_load(path.read_text("utf-8"))
    except yaml.YAMLError as error:
        raise ConfigError(f"{path}: not valid YAML: {error}") from error
    if raw is None:
        raw = {}
    if not isinstance(raw, Mapping):
        raise ConfigError(f"{path}: expected a mapping at the top level")
    try:
        return PaperstandConfig.model_validate(dict(raw))
    except ProfileError as error:
        raise ConfigError(f"{path}: {error}") from error
    except ValidationError as error:
        raise ConfigError(f"{path}: {validation_message(error)}") from error


def validation_message(error: ValidationError) -> str:
    """A single ``key: message; key: message`` line out of a pydantic error.

    Shared by every YAML file Paperstand validates with pydantic —
    ``paperstand.yml`` here, ``publication.yml`` in
    :mod:`paperstand.publication` — so the two report a malformed file in the
    same shape.
    """
    messages = []
    for item in error.errors():
        location = ".".join(str(part) for part in item["loc"]) or "config"
        messages.append(f"{location}: {item['msg'].removeprefix('Value error, ')}")
    return "; ".join(messages)
