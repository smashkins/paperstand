"""Date rules D1-D7 and the month tables they are built from.

The rules are generic shapes — "day, month name, optional year", "ISO date",
"month name and year" — compiled from whatever month tables the active profile
enables. Nothing here knows about any particular publication.

Rule identifiers, in the order they are tried:

===== ====================================== ==========
Rule  Shape                                  Precision
===== ====================================== ==========
D1    ``YYYY-MM-DD``                         day
D2    ``DD-MM-YYYY`` (also ``.`` and ``/``)  day
D3    ``D(-D)? <month> YYYY?``               day
D4    ``<month> D, YYYY``                    day
D5    ``YYYY-MM``                            month
D6    ``<month> YYYY`` (or a full month)     month
D7    an isolated year                       year
===== ====================================== ==========
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from paperstand.parsing.normalize import strip_accents

#: Month names per bundled language: full name first, then abbreviations.
BUNDLED_MONTHS: dict[str, tuple[tuple[str, ...], ...]] = {
    "it": (
        ("gennaio", "gen"),
        ("febbraio", "feb"),
        ("marzo", "mar"),
        ("aprile", "apr"),
        ("maggio", "mag"),
        ("giugno", "giu"),
        ("luglio", "lug"),
        ("agosto", "ago"),
        ("settembre", "set", "sett"),
        ("ottobre", "ott"),
        ("novembre", "nov"),
        ("dicembre", "dic"),
    ),
    "en": (
        ("january", "jan"),
        ("february", "feb"),
        ("march", "mar"),
        ("april", "apr"),
        ("may",),
        ("june", "jun"),
        ("july", "jul"),
        ("august", "aug"),
        ("september", "sep", "sept"),
        ("october", "oct"),
        ("november", "nov"),
        ("december", "dec"),
    ),
    "fr": (
        ("janvier", "janv"),
        ("février", "févr"),
        ("mars",),
        ("avril", "avr"),
        ("mai",),
        ("juin",),
        ("juillet", "juil"),
        ("août",),
        ("septembre", "sept"),
        ("octobre", "oct"),
        ("novembre", "nov"),
        ("décembre", "déc"),
    ),
    "de": (
        ("januar", "jan"),
        ("februar", "feb"),
        ("märz", "mrz"),
        ("april", "apr"),
        ("mai",),
        ("juni", "jun"),
        ("juli", "jul"),
        ("august", "aug"),
        ("september", "sep"),
        ("oktober", "okt"),
        ("november", "nov"),
        ("dezember", "dez"),
    ),
    "es": (
        ("enero", "ene"),
        ("febrero", "feb"),
        ("marzo", "mar"),
        ("abril", "abr"),
        ("mayo", "may"),
        ("junio", "jun"),
        ("julio", "jul"),
        ("agosto", "ago"),
        ("septiembre", "setiembre", "sept"),
        ("octubre", "oct"),
        ("noviembre", "nov"),
        ("diciembre", "dic"),
    ),
    "pt": (
        ("janeiro", "jan"),
        ("fevereiro", "fev"),
        ("março", "mar"),
        ("abril", "abr"),
        ("maio", "mai"),
        ("junho", "jun"),
        ("julho", "jul"),
        ("agosto", "ago"),
        ("setembro", "set"),
        ("outubro", "out"),
        ("novembro", "nov"),
        ("dezembro", "dez"),
    ),
}

#: Every rule this module can compile, in the order they are tried.
DATE_RULE_IDS: tuple[str, ...] = ("D1", "D2", "D3", "D4", "D5", "D6", "D7")

#: Date precisions, from the most to the least specific.
PRECISIONS: tuple[str, ...] = ("day", "month", "year", "none")


@dataclass(frozen=True, slots=True)
class MonthTable:
    """Month names of the active languages, ready for matching."""

    numbers: Mapping[str, int]
    """Lowercase, unaccented month name → month number."""

    all_names: tuple[str, ...]
    """Every spelling, accented and not, longest first."""

    full_names: tuple[str, ...]
    """Full month names only (no abbreviations), longest first."""

    squashed_names: tuple[str, ...]
    """Every spelling in squashed form, used as a title boundary."""


def build_month_table(
    languages: Sequence[str],
    custom: Mapping[str, Mapping[str, int]] | None = None,
) -> MonthTable:
    """Build the month table for ``languages`` plus any custom tables."""
    numbers: dict[str, int] = {}
    spellings: set[str] = set()
    full: set[str] = set()

    def add(name: str, number: int, *, is_full: bool) -> None:
        for variant in {name.lower(), strip_accents(name.lower())}:
            if not variant:
                continue
            numbers[strip_accents(variant)] = number
            spellings.add(variant)
            if is_full:
                full.add(variant)

    custom = custom or {}
    for language in languages:
        if language in custom:
            for name, number in custom[language].items():
                add(name, number, is_full=True)
            continue
        for index, names in enumerate(BUNDLED_MONTHS[language], start=1):
            for position, name in enumerate(names):
                add(name, index, is_full=position == 0)

    by_length = sorted(spellings, key=lambda name: (-len(name), name))
    return MonthTable(
        numbers=numbers,
        all_names=tuple(by_length),
        full_names=tuple(sorted(full, key=lambda name: (-len(name), name))),
        squashed_names=tuple(
            sorted({strip_accents(name) for name in spellings}, key=lambda name: (-len(name), name))
        ),
    )


@dataclass(frozen=True, slots=True)
class DateHit:
    """A date found in a name, with the span it occupies."""

    rule: str
    year: int | None
    month: int | None
    day: int | None
    day_end: int | None
    span: tuple[int, int]
    precision: str


@dataclass(frozen=True, slots=True)
class CompiledDateRule:
    """One compiled date rule."""

    rule_id: str
    regex: re.Pattern[str]
    precision: str
    year_optional: bool = False
    """Whether the rule can stand without a year, so that a year outside the
    profile's ``year_range`` makes the number not-a-year rather than throwing
    the whole date away."""


def _alternation(names: Iterable[str]) -> str:
    escaped = [re.escape(name) for name in names]
    return "(?:" + "|".join(escaped) + ")" if escaped else "(?!)"


def compile_date_rules(
    months: MonthTable,
    rule_ids: Sequence[str],
) -> tuple[CompiledDateRule, ...]:
    """Compile the enabled date rules against a month table."""
    any_month = _alternation(months.all_names)
    full_month = _alternation(months.full_names)
    # Any four digit number is a year candidate; the profile's ``year_range``
    # is what actually decides, when a hit is validated.
    year = r"\d{4}"
    # The separator of a numeric date, repeated identically on both sides. A
    # space belongs in there: by the time the rules run, the profile has already
    # turned the underscores of "Title_17_03_2026" into spaces.
    sep = r"(?P<sep>[-./]|\s)"

    sources: dict[str, tuple[str, str, bool]] = {
        "D1": (
            rf"(?<!\d)(?P<year>{year}){sep}(?P<month>\d{{1,2}})(?P=sep)(?P<day>\d{{1,2}})(?!\d)",
            "day",
            False,
        ),
        "D2": (
            rf"(?<!\d)(?P<day>\d{{1,2}}){sep}(?P<month>\d{{1,2}})(?P=sep)(?P<year>{year})(?!\d)",
            "day",
            False,
        ),
        "D3": (
            r"(?<!\d)(?P<day>\d{1,2})(?!\d)"
            r"(?:\s*[-/]\s*(?P<day_end>\d{1,2})(?!\d))?"
            rf"\s*(?P<month>{any_month})(?![a-z])\.?"
            rf"\s*(?P<year>{year})?(?!\d)",
            "day",
            True,
        ),
        "D4": (
            rf"(?<![a-z])(?P<month>{any_month})(?![a-z])\.?"
            r"\s*(?P<day>\d{1,2})(?!\d)(?:st|nd|rd|th)?,?"
            rf"\s*(?P<year>{year})(?!\d)",
            "day",
            False,
        ),
        "D5": (rf"(?<!\d)(?P<year>{year})[-./](?P<month>\d{{1,2}})(?!\d)", "month", False),
        "D6": (
            rf"(?<![a-z])(?:(?P<month>{any_month})(?![a-z])\.?\s*(?P<year>{year})(?!\d)"
            rf"|(?P<month_alt>{full_month})(?![a-z]))",
            "month",
            False,
        ),
        "D7": (rf"(?<!\d)(?P<year>{year})(?!\d)", "year", False),
    }

    enabled = [rule_id for rule_id in DATE_RULE_IDS if rule_id in rule_ids]
    compiled = []
    for rule_id in enabled:
        pattern, precision, year_optional = sources[rule_id]
        compiled.append(
            CompiledDateRule(
                rule_id,
                re.compile(pattern, re.IGNORECASE),
                precision,
                year_optional,
            ),
        )
    return tuple(compiled)


def month_number(value: str | None, months: MonthTable) -> int | None:
    """Turn a month group — a number or a name — into 1-12."""
    if value is None:
        return None
    text = value.strip().strip(".")
    if not text:
        return None
    if text.isdigit():
        number = int(text)
        return number if 1 <= number <= 12 else None
    return months.numbers.get(strip_accents(text.lower()))


def valid_date(
    year: int | None,
    month: int | None,
    day: int | None,
    year_range: tuple[int, int],
) -> bool:
    """Check the pieces of a date, tolerating a missing year."""
    if year is not None and not year_range[0] <= year <= year_range[1]:
        return False
    if month is not None and not 1 <= month <= 12:
        return False
    if day is not None:
        if month is None:
            return False
        try:
            dt.date(year if year is not None else 2000, month, day)
        except ValueError:
            return False
    return True


def valid_range_end(
    day_end: int | None,
    year: int | None,
    month: int | None,
    day: int | None,
) -> bool:
    """Check the end of a day range: a real day of that month, at or after the start."""
    if day_end is None:
        return True
    if month is None or day is None:
        return False
    try:
        dt.date(year if year is not None else 2000, month, day_end)
    except ValueError:
        return False
    return day_end >= day


def find_date(
    text: str,
    rules: Sequence[CompiledDateRule],
    months: MonthTable,
    year_range: tuple[int, int],
    trace: list[tuple[str, str]] | None = None,
) -> DateHit | None:
    """Return the first valid date in ``text``, trying the rules in order."""
    for rule in rules:
        found = False
        for match in rule.regex.finditer(text):
            found = True
            groups = match.groupdict()
            month = month_number(groups.get("month") or groups.get("month_alt"), months)
            year_text = groups.get("year")
            day_text = groups.get("day")
            end_text = groups.get("day_end")
            year = int(year_text) if year_text else None
            day = int(day_text) if day_text else None
            day_end = int(end_text) if end_text else None
            if rule.precision != "year" and month is None:
                continue
            if not valid_date(year, month, day, year_range):
                # A four digit number the profile does not accept as a year is
                # simply not a year: a rule that can do without one keeps the
                # rest of the date rather than losing the day too.
                if not (rule.year_optional and year is not None):
                    continue
                year = None
                if not valid_date(year, month, day, year_range):
                    continue
            if not valid_range_end(day_end, year, month, day):
                continue
            if trace is not None:
                trace.append(
                    (f"date {rule.rule_id}", f"matched {match.group(0)!r} at {match.span()}")
                )
            return DateHit(
                rule=rule.rule_id,
                year=year,
                month=month if rule.precision != "year" else None,
                day=day if rule.precision == "day" else None,
                day_end=day_end if rule.precision == "day" else None,
                span=match.span(),
                precision=rule.precision,
            )
        if trace is not None and not found:
            trace.append((f"date {rule.rule_id}", "no match"))
    return None


#: A path component holding a four digit year. Whether that year is plausible is
#: the profile's ``year_range`` to say, not this regex's.
YEAR_COMPONENT = re.compile(r"^\d{4}$")
#: A path component holding a month number.
MONTH_COMPONENT = re.compile(r"^(0?[1-9]|1[0-2])$")
#: A path component holding a day number.
DAY_COMPONENT = re.compile(r"^(0?[1-9]|[12][0-9]|3[01])$")


def is_date_component(component: str, months: MonthTable) -> bool:
    """Whether a folder name is a date rather than a title."""
    if YEAR_COMPONENT.match(component) or DAY_COMPONENT.match(component):
        return True
    return strip_accents(component.lower()) in months.numbers


def folder_date(
    components: Sequence[str],
    year_range: tuple[int, int] = (1900, 2099),
) -> DateHit | None:
    """Read ``YYYY``, then ``MM``, then ``DD`` from the folders of a path (F1).

    A four digit folder outside ``year_range`` is not a year, so it does not
    open a date: the profile decides which years its library can hold.
    """
    year: int | None = None
    month: int | None = None
    day: int | None = None
    for component in components:
        if year is None:
            if YEAR_COMPONENT.match(component) and year_range[0] <= int(component) <= year_range[1]:
                year = int(component)
            continue
        if month is None:
            if MONTH_COMPONENT.match(component):
                month = int(component)
            continue
        if day is None and DAY_COMPONENT.match(component):
            day = int(component)
    if year is None:
        return None
    if day is not None and month is not None:
        try:
            dt.date(year, month, day)
        except ValueError:
            day = None
    precision = "day" if day is not None else "month" if month is not None else "year"
    return DateHit(
        rule="F1",
        year=year,
        month=month,
        day=day,
        day_end=None,
        span=(0, 0),
        precision=precision,
    )


def to_date(hit: DateHit) -> dt.date | None:
    """Materialise a hit into a real date, padding the missing pieces."""
    if hit.year is None:
        return None
    try:
        return dt.date(hit.year, hit.month or 1, hit.day or 1)
    except ValueError:
        return None
