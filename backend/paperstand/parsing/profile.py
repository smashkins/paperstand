"""The profile schema, its ``extends`` resolution and its validation.

A profile is the declarative description of how a file name becomes a title, an
issue date and an issue number. Paperstand ships one, ``default``; a user can
take it as it is, extend it or write one from scratch, and never has to touch
Python to describe a new naming shape.

Resolution rules:

* a profile without ``extends`` starts from an **empty** profile, not from
  ``default``;
* a key the child does not set is inherited from the parent;
* a list the child sets **replaces** the parent's, unless its first element is
  the string ``"+"``, which appends the rest to the parent's list instead.

Everything is validated when it is loaded — regexes must compile, named groups
must be ones the engine knows, languages must have a month table, ``extends``
must point at an existing profile — and a failure names the profile and the
offending pattern.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from functools import lru_cache
from importlib import resources
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError, field_validator, model_validator

from paperstand.parsing.dates import BUNDLED_MONTHS, DATE_RULE_IDS

#: Name of the profile bundled with Paperstand.
DEFAULT_PROFILE = "default"

#: Named groups a pattern may capture.
ALLOWED_GROUPS: frozenset[str] = frozenset(
    {
        "title",
        "day",
        "month",
        "year",
        "date",
        "date_end",
        "number",
        "subtitle",
        "dedup",
        "volume",
        "variant",
    }
)

#: Marker that makes a child list append to the parent's instead of replacing it.
APPEND = "+"

DateFallback = Literal["folder", "mtime"]
TitleSource = Literal["config", "pattern", "folder", "filename"]


class ProfileError(ValueError):
    """A profile could not be loaded."""


def _compile(pattern: str, kind: str) -> re.Pattern[str]:
    try:
        return re.compile(pattern)
    except re.error as error:
        raise ValueError(
            f"{kind} {pattern!r} is not a valid regular expression: {error}"
        ) from error


class Profile(BaseModel):
    """A validated, fully resolved parser profile."""

    model_config = ConfigDict(extra="forbid")

    extends: str | None = None
    """Profile this one starts from. Resolved away once the profile is loaded."""

    strip: list[str] = []
    """Regexes removed from the stem, in order, before anything else."""

    replace: list[tuple[str, str]] = []
    """``[regex, replacement]`` pairs applied after the strips."""

    languages: list[str] = []
    """Month tables to enable: ``it``, ``en``, ``fr``, ``de``, ``es``, ``pt``."""

    months: dict[str, dict[str, int]] = {}
    """Custom month tables, ``language: {name: number}``, usable in ``languages``."""

    articles: list[str] = []
    """Leading articles that are optional when a title is matched."""

    lowercase_words: list[str] = []
    """Words a derived title keeps lowercase unless they open it."""

    patterns: list[str] = []
    """Regexes with named groups, tried in order against the spaced form."""

    date_rules: list[str] = list(DATE_RULE_IDS)
    """Generic date rules to try, by identifier."""

    date_fallback: list[DateFallback] = ["folder", "mtime"]
    """Where the date comes from when no rule yields one."""

    title_source: list[TitleSource] = ["config", "pattern", "folder", "filename"]
    """Order in which the title is resolved."""

    number: bool = True
    """Whether issue numbers are read at all."""

    number_tokens: list[str] = []
    """Tokens that introduce an issue number (``n``, ``no``, ``#``…)."""

    year_range: tuple[int, int] = (1900, 2099)
    """Years accepted as a date, and rejected as an issue number."""

    unsorted: bool = True
    """Send a file to *Unsorted* when titles are configured and none matches."""

    @field_validator("strip")
    @classmethod
    def _check_strip(cls, value: list[str]) -> list[str]:
        for pattern in value:
            groups = _compile(pattern, "strip pattern").groupindex
            unknown = set(groups) - ALLOWED_GROUPS
            if unknown:
                raise ValueError(
                    f"strip pattern '{pattern}' captures unknown group(s): "
                    f"{', '.join(sorted(unknown))}"
                )
        return value

    @field_validator("replace")
    @classmethod
    def _check_replace(cls, value: list[tuple[str, str]]) -> list[tuple[str, str]]:
        for pattern, _ in value:
            _compile(pattern, "replace pattern")
        return value

    @field_validator("patterns")
    @classmethod
    def _check_patterns(cls, value: list[str]) -> list[str]:
        for pattern in value:
            groups = _compile(pattern, "pattern").groupindex
            unknown = set(groups) - ALLOWED_GROUPS
            if unknown:
                raise ValueError(
                    f"pattern '{pattern}' captures unknown group(s): "
                    f"{', '.join(sorted(unknown))}; allowed: "
                    f"{', '.join(sorted(ALLOWED_GROUPS))}"
                )
        return value

    @field_validator("date_rules")
    @classmethod
    def _check_date_rules(cls, value: list[str]) -> list[str]:
        unknown = [rule for rule in value if rule not in DATE_RULE_IDS]
        if unknown:
            raise ValueError(
                f"unknown date rule(s): {', '.join(unknown)}; known: {', '.join(DATE_RULE_IDS)}"
            )
        return value

    @field_validator("months")
    @classmethod
    def _check_months(cls, value: dict[str, dict[str, int]]) -> dict[str, dict[str, int]]:
        for language, table in value.items():
            for name, number in table.items():
                if not 1 <= number <= 12:
                    raise ValueError(
                        f"custom month table {language!r} maps {name!r} to {number}, "
                        "which is not a month number between 1 and 12"
                    )
        return value

    @model_validator(mode="after")
    def _check_languages(self) -> Profile:
        known = set(BUNDLED_MONTHS) | set(self.months)
        unknown = [language for language in self.languages if language not in known]
        if unknown:
            raise ValueError(
                f"unknown language(s): {', '.join(unknown)}; bundled: "
                f"{', '.join(sorted(BUNDLED_MONTHS))}. Add a `months:` table to use another one."
            )
        return self


def _merge_lists(parent: list[Any], child: list[Any]) -> list[Any]:
    if child and child[0] == APPEND:
        return [*parent, *child[1:]]
    return child


def merge_profiles(parent: Mapping[str, Any], child: Mapping[str, Any]) -> dict[str, Any]:
    """Merge a child profile onto its parent."""
    merged = dict(parent)
    for key, value in child.items():
        if key == "extends":
            continue
        if isinstance(value, list):
            existing = merged.get(key)
            base = existing if isinstance(existing, list) else []
            merged[key] = _merge_lists(base, value)
        else:
            merged[key] = value
    return merged


@lru_cache(maxsize=1)
def bundled_default() -> dict[str, Any]:
    """The raw ``default`` profile shipped inside the package."""
    text = resources.files("paperstand.parsing").joinpath("profiles/default.yml").read_text("utf-8")
    loaded = yaml.safe_load(text)
    if not isinstance(loaded, dict):
        raise ProfileError("the bundled default profile is not a mapping")
    return loaded


@lru_cache(maxsize=1)
def canonical_pattern() -> str:
    """The declared-publication grammar: the bundled profile's first pattern.

    Read from the shipped YAML rather than duplicated as a Python constant, so
    the naming rule still lives only in the profile (``AGENTS.md`` rule 2). A
    custom profile that replaces ``patterns:`` without a leading ``"+"`` loses
    this pattern along with the volume ones; the scanner uses this helper to
    warn when that happens to a library holding a declared publication.
    """
    patterns = bundled_default().get("patterns")
    if not isinstance(patterns, list) or not patterns:
        raise ProfileError("the bundled default profile defines no patterns")
    return str(patterns[0])


def _resolve_raw(
    name: str,
    registry: Mapping[str, Mapping[str, Any]],
    seen: tuple[str, ...],
) -> dict[str, Any]:
    if name in seen:
        chain = " -> ".join([*seen, name])
        raise ProfileError(f"profile {name!r}: `extends` forms a cycle ({chain})")
    raw = registry.get(name)
    if raw is None:
        known = ", ".join(sorted(registry)) or "none"
        raise ProfileError(f"profile {name!r}: no such profile (known profiles: {known})")
    parent_name = raw.get("extends")
    if parent_name is None:
        return dict(raw)
    if not isinstance(parent_name, str):
        raise ProfileError(f"profile {name!r}: `extends` must be a profile name")
    parent = _resolve_raw(parent_name, registry, (*seen, name))
    return merge_profiles(parent, raw)


def build_registry(
    parsers: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Mapping[str, Any]]:
    """The raw profiles available: the bundled one plus the configured ones."""
    registry: dict[str, Mapping[str, Any]] = {DEFAULT_PROFILE: bundled_default()}
    for name, raw in (parsers or {}).items():
        if not isinstance(raw, Mapping):
            raise ProfileError(f"profile {name!r}: expected a mapping of profile keys")
        registry[name] = raw
    return registry


def load_profile(name: str, registry: Mapping[str, Mapping[str, Any]]) -> Profile:
    """Resolve and validate a single profile by name."""
    merged = _resolve_raw(name, registry, ())
    try:
        return Profile.model_validate(merged)
    except ValidationError as error:
        raise ProfileError(f"profile {name!r}: {_first_message(error)}") from error


def load_profiles(
    parsers: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Profile]:
    """Resolve and validate every profile, bundled and configured."""
    registry = build_registry(parsers)
    return {name: load_profile(name, registry) for name in registry}


def _first_message(error: ValidationError) -> str:
    """A single readable line out of a pydantic validation error."""
    messages: list[str] = []
    for item in error.errors():
        location = ".".join(str(part) for part in item["loc"]) or "profile"
        message = item["msg"].removeprefix("Value error, ")
        messages.append(f"{location}: {message}")
    return "; ".join(messages)
