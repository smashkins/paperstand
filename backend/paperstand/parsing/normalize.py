"""Stem cleaning and the two normalised forms of a file name.

Everything here is generic string work driven by the regexes a profile declares;
no rule about any particular publication lives in this module.

Two forms of a name are kept:

``spaced``
    Readable, close to the original: separators turned into spaces, dashes and
    apostrophes unified, whitespace collapsed. Dates, numbers and the displayed
    title are all read off this form.
``squashed``
    ``[a-z0-9]`` only, accents removed. It is what title matching compares, so
    that ``La_Gazzetta_del_Lago``, ``la gazzetta del lago`` and ``laGazzettadelLago`` are one name.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass

#: Characters trimmed off the edges of a derived title.
SEPARATORS = " \t\u00a0-_.,;:|/\\+&"


def strip_accents(text: str) -> str:
    """Return ``text`` without combining marks (``città`` → ``citta``)."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(char for char in decomposed if not unicodedata.combining(char))


_SLUG_STRIP = re.compile(r"[^a-z0-9]+")

#: What a slug falls back to when a name has nothing sluggable in it.
SLUG_FALLBACK = "item"


def slugify(text: str) -> str:
    """A lowercase, ASCII, hyphenated identifier built from ``text``.

    Used for the database identifiers of libraries and titles, so two names that
    slugify the same way are a collision the caller has to resolve.
    """
    slug = _SLUG_STRIP.sub("-", strip_accents(text).lower()).strip("-")
    return slug or SLUG_FALLBACK


@dataclass(frozen=True, slots=True)
class Squashed:
    """A squashed string plus, per character, its index in the source string."""

    text: str
    offsets: tuple[int, ...]

    def source_end(self, index: int) -> int:
        """Index in the source string just past the squashed character ``index``."""
        if not self.offsets:
            return 0
        position = min(index, len(self.offsets)) - 1
        if position < 0:
            return 0
        return self.offsets[position] + 1


def squash(text: str) -> Squashed:
    """Reduce ``text`` to lowercase ASCII alphanumerics, keeping an index map."""
    chars: list[str] = []
    offsets: list[int] = []
    for index, char in enumerate(text):
        for piece in unicodedata.normalize("NFKD", char):
            if unicodedata.combining(piece):
                continue
            lowered = piece.lower()
            if lowered.isascii() and lowered.isalnum():
                chars.append(lowered)
                offsets.append(index)
    return Squashed("".join(chars), tuple(offsets))


def squash_text(text: str) -> str:
    """Squashed form of ``text``, without the index map."""
    return squash(text).text


@dataclass(frozen=True, slots=True)
class CleanName:
    """The result of cleaning a file name stem."""

    stem: str
    spaced: str
    squashed: Squashed
    has_dedup_suffix: bool


def clean_stem(
    stem: str,
    strip_rules: Sequence[re.Pattern[str]],
    replacements: Sequence[tuple[re.Pattern[str], str]],
    trace: list[tuple[str, str]] | None = None,
) -> CleanName:
    """Apply a profile's ``strip`` and ``replace`` rules to a file name stem.

    A strip rule that captures a group named ``dedup`` marks the name as a
    duplicate copy (``Something-1``, ``Something (1)``) when it fires.
    """
    text = stem
    has_dedup_suffix = False

    for rule in strip_rules:
        matched_dedup = False

        def drop(match: re.Match[str]) -> str:
            nonlocal matched_dedup
            if match.groupdict().get("dedup") is not None:
                matched_dedup = True
            return ""

        stripped, count = rule.subn(drop, text)
        if count:
            has_dedup_suffix = has_dedup_suffix or matched_dedup
            if trace is not None:
                trace.append((f"strip {rule.pattern!r}", f"{text!r} -> {stripped!r}"))
            text = stripped

    for rule, replacement in replacements:
        replaced, count = rule.subn(replacement, text)
        if count and replaced != text:
            if trace is not None:
                trace.append(
                    (f"replace {rule.pattern!r} -> {replacement!r}", f"{text!r} -> {replaced!r}")
                )
            text = replaced

    spaced = text.strip()
    return CleanName(
        stem=stem,
        spaced=spaced,
        squashed=squash(spaced),
        has_dedup_suffix=has_dedup_suffix,
    )


def trim_separators(text: str) -> str:
    """Strip separators and whitespace from both ends of ``text``."""
    return text.strip(SEPARATORS)


def smart_title_case(text: str, lowercase_words: Sequence[str] = ()) -> str:
    """Capitalise words that are entirely lowercase, leave the others alone.

    Names that already carry capitals (``OPQ``, ``iPad``, ``Corriere del
    Ponte``) survive untouched, while a wholly lowercase name gets initials.
    Words listed in ``lowercase_words`` — the articles and prepositions a
    profile declares — keep their case unless they open the name.
    """
    minor = {word.lower() for word in lowercase_words}
    words = []
    for index, word in enumerate(text.split(" ")):
        touchable = bool(word) and word == word.lower() and any(c.isalpha() for c in word)
        keep_lower = index > 0 and word.strip(SEPARATORS) in minor
        words.append(_capitalise(word) if touchable and not keep_lower else word)
    return " ".join(words)


def _capitalise(word: str) -> str:
    for index, char in enumerate(word):
        if char.isalpha():
            return word[:index] + char.upper() + word[index + 1 :]
    return word


def mask(text: str, spans: Sequence[tuple[int, int]]) -> str:
    """Blank out ``spans`` in ``text`` with spaces, preserving every index."""
    if not spans:
        return text
    chars = list(text)
    for start, end in spans:
        for index in range(max(start, 0), min(end, len(chars))):
            chars[index] = " "
    return "".join(chars)
