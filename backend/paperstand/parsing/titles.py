"""Title rules T1 (configured titles), T2 (folder) and T3 (file name prefix).

T1 works on the squashed form, so ``La_Gazzetta_del_Lago``, ``la gazzetta del lago`` and
``laGazzettadelLago`` are the same name. A configured name matches when it sits at the
start of the name and what follows it is a boundary: the end of the string, a
digit, a month name or an ``n`` immediately followed by a digit. That is what
keeps a base title from swallowing a longer one — a regional edition, an insert,
a supplement — and the **longest** configured name always wins.

The leading article is optional in both directions: a configured
``Il Mattutino`` matches a file called ``Mattutino_…`` and the
other way round.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass

from paperstand.parsing.normalize import Squashed, smart_title_case, squash_text, trim_separators

#: ``n`` followed by a digit, as in ``Confini n. 8`` once squashed.
_NUMBER_PREFIX = re.compile(r"n\d")


@dataclass(frozen=True, slots=True)
class TitleKey:
    """One squashed spelling of a configured title."""

    key: str
    canonical: str


@dataclass(frozen=True, slots=True)
class TitleMatch:
    """A configured title recognised in a name."""

    canonical: str
    key: str
    end: int
    """Index in the source string just past the matched name."""


def _article_variants(squashed: str, articles: Sequence[str]) -> set[str]:
    """The name with and without a leading article."""
    variants = {squashed}
    for article in articles:
        if article and squashed.startswith(article) and len(squashed) > len(article):
            variants.add(squashed[len(article) :])
    return variants


def squash_articles(articles: Sequence[str]) -> tuple[str, ...]:
    """Squash an article list once, longest first."""
    return tuple(sorted({squash_text(a) for a in articles if a}, key=len, reverse=True))


def build_title_index(
    entries: Iterable[tuple[str, Sequence[str]]],
    articles: Sequence[str],
) -> tuple[TitleKey, ...]:
    """Build the T1 lookup table from ``(canonical name, aliases)`` pairs.

    Longer keys come first, so that the longest configured name wins.
    """
    ordered_articles = squash_articles(articles)
    keys: dict[str, str] = {}
    for canonical, aliases in entries:
        for spelling in (canonical, *aliases):
            squashed = squash_text(spelling)
            if not squashed:
                continue
            for variant in _article_variants(squashed, ordered_articles):
                keys.setdefault(variant, canonical)
    return tuple(
        TitleKey(key=key, canonical=canonical)
        for key, canonical in sorted(keys.items(), key=lambda item: (-len(item[0]), item[0]))
    )


def _boundary_ok(rest: str, month_names: Sequence[str]) -> bool:
    if not rest:
        return True
    if rest[0].isdigit():
        return True
    if _NUMBER_PREFIX.match(rest):
        return True
    return any(rest.startswith(name) for name in month_names)


def match_title(
    squashed: Squashed,
    index: Sequence[TitleKey],
    articles: Sequence[str],
    month_names: Sequence[str],
) -> TitleMatch | None:
    """Run T1 against a squashed name; the longest configured name wins.

    ``articles`` must already be squashed — see :func:`squash_articles`.
    """
    text = squashed.text
    if not text:
        return None
    candidates = _article_variants(text, articles)
    for entry in index:
        for candidate in candidates:
            if not candidate.startswith(entry.key):
                continue
            if not _boundary_ok(candidate[len(entry.key) :], month_names):
                continue
            # The offset map belongs to the full text, so an article-stripped
            # candidate has to be shifted back by what was removed from it.
            consumed = len(entry.key) + (len(text) - len(candidate))
            return TitleMatch(
                canonical=entry.canonical,
                key=entry.key,
                end=squashed.source_end(consumed),
            )
    return None


def title_from_folder(
    components: Sequence[str],
    is_date: Callable[[str], bool],
) -> str | None:
    """T2 — the nearest ancestor folder that is not a date component."""
    for component in reversed(components):
        if is_date(component):
            continue
        name = trim_separators(component.replace("_", " ")).strip()
        if not name:
            continue
        return re.sub(r"\s+", " ", name)
    return None


def title_from_filename(
    spaced: str,
    cut: int | None,
    lowercase_words: Sequence[str] = (),
) -> str:
    """T3 — the file name up to the first date or number span, smart-cased."""
    prefix = spaced if cut is None else spaced[:cut]
    trimmed = trim_separators(prefix)
    if not trimmed:
        trimmed = trim_separators(spaced)
    return smart_title_case(re.sub(r"\s+", " ", trimmed), lowercase_words)
