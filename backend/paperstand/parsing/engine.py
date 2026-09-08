"""The parsing pipeline.

One pass over a file name, in this order:

1. **strip** and **replace** the stem down to its ``spaced`` and ``squashed``
   forms (rule A);
2. try the profile's **patterns**, then any per-title pattern, and take whatever
   named groups they capture;
3. fill what is still missing with the **generic rules** — dates D1-D7 (rule B),
   numbers N1-N2 (rule C), titles T1-T3 (rule D);
4. fall back to the **folders** and then to the file's **mtime** for a date that
   the name does not carry (F1, F2).

Which rules fired is recorded in ``matched_rule``, and — when a trace is asked
for — every step is recorded, which is what ``parse-explain`` prints.

The pipeline never touches the filesystem: the modification time is passed in and
the folder components are read off the relative path.
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import TYPE_CHECKING

from paperstand.parsing.dates import (
    DateHit,
    build_month_table,
    compile_date_rules,
    find_date,
    folder_date,
    is_date_component,
    month_number,
    to_date,
    valid_date,
    valid_range_end,
)
from paperstand.parsing.normalize import (
    clean_stem,
    mask,
    smart_title_case,
    squash,
    trim_separators,
)
from paperstand.parsing.numbers import compile_number_rules, find_number
from paperstand.parsing.profile import Profile
from paperstand.parsing.titles import (
    TitleMatch,
    build_title_index,
    match_title,
    squash_articles,
    title_from_filename,
    title_from_folder,
)

if TYPE_CHECKING:  # pragma: no cover - import cycle only matters to type checkers
    from paperstand.config import LibraryConfig

#: Title a file is filed under when titles are configured and none of them match.
UNSORTED_TITLE = "Unsorted"

#: English month names, used to build the human readable label.
LABEL_MONTHS: tuple[str, ...] = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)

#: One recorded step of a parse: ``(name, detail)``.
Step = tuple[str, str]
Trace = list[Step]

_ISO_DATE = re.compile(r"^(?P<year>\d{4})-(?P<month>\d{1,2})(?:-(?P<day>\d{1,2}))?$")
_COMPACT_DATE = re.compile(r"^(?P<year>\d{4})(?P<month>\d{2})(?P<day>\d{2})$")


@dataclass(frozen=True, slots=True)
class ParsedIssue:
    """Everything the catalogue needs to know about one file.

    ``title_name`` is what the issue is filed under, ``derived_title`` what the
    name itself suggests: they differ when a configured title wins, and when a
    file with no configured title lands in *Unsorted*.
    """

    title_name: str
    title_source: str
    derived_title: str
    issue_date: dt.date | None
    date_precision: str
    date_source: str
    issue_number: int | None
    has_dedup_suffix: bool
    label: str
    matched_rule: str


@dataclass(frozen=True, slots=True)
class PatternHit:
    """What a pattern — a profile's or a title's — captured."""

    rule: str
    groups: dict[str, str]
    spans: dict[str, tuple[int, int]]
    title: str | None = None


class Parser:
    """A profile compiled against one library, ready to parse names.

    Compiling is the expensive part, so a parser is built once per
    ``(profile, library)`` pair and reused; see
    :func:`paperstand.parsing.parse.parse_path`.
    """

    def __init__(self, profile: Profile, library: LibraryConfig) -> None:
        self.profile = profile
        self.library = library
        self._strip = [re.compile(pattern) for pattern in profile.strip]
        self._replace = [(re.compile(pattern), value) for pattern, value in profile.replace]
        self._patterns = [re.compile(pattern, re.IGNORECASE) for pattern in profile.patterns]
        self.months = build_month_table(profile.languages, profile.months)
        self._date_rules = compile_date_rules(self.months, profile.date_rules)
        self._numbers = compile_number_rules(profile.number_tokens)
        self._titles = build_title_index(
            [(title.name, title.aliases) for title in library.titles],
            profile.articles,
        )
        self._articles = squash_articles(profile.articles)
        self._title_patterns = tuple(
            (title.name, re.compile(title.pattern, re.IGNORECASE))
            for title in library.titles
            if title.pattern
        )
        self._library_parts = tuple(
            part for part in PurePosixPath(library.path).parts if part not in (".", "/")
        )
        self.allow_bare_number = profile.number and library.kind != "newspaper"

    # ------------------------------------------------------------------ public

    def parse(
        self,
        rel_path: str,
        mtime: dt.datetime | dt.date,
        *,
        trace: Trace | None = None,
    ) -> ParsedIssue:
        """Parse one path, relative to the **library root**, into an issue."""
        path = PurePosixPath(rel_path)
        folders = self._folder_components(path)
        clean = clean_stem(path.stem, self._strip, self._replace, trace)
        spaced = clean.spaced
        if trace is not None:
            trace.append(("spaced", repr(spaced)))
            trace.append(("squashed", repr(clean.squashed.text)))
            trace.append(("folders", repr(list(folders))))

        pattern, date_hit, date_spans = self._match_patterns(spaced, trace)
        pattern_rule = pattern.rule if pattern is not None else None
        date_rule: str | None = None
        # Every span the pattern captured — not just its date groups — is masked
        # before the generic date rules run, so a captured `number` or
        # `subtitle` cannot be misread as a day or a month. Masking group spans,
        # rather than the whole match, leaves alone whatever a pattern that only
        # matches a prefix never captured in the first place.
        pattern_spans = list(pattern.spans.values()) if pattern is not None else []
        if date_hit is None:
            generic_source = mask(spaced, pattern_spans)
            if trace is not None and pattern_spans:
                trace.append(("masked", repr(generic_source)))
            date_hit = find_date(
                generic_source, self._date_rules, self.months, self.profile.year_range, trace
            )
            if date_hit is not None:
                date_rule = date_hit.rule
                date_spans = [date_hit.span]
        elif not _is_complete(date_hit):
            # A pattern may capture only part of a date. What it did not capture
            # falls through to the generic rules, on what the pattern's own
            # spans leave behind.
            generic_source = mask(spaced, pattern_spans)
            if trace is not None:
                trace.append(("masked", repr(generic_source)))
            generic = find_date(
                generic_source,
                self._date_rules,
                self.months,
                self.profile.year_range,
                trace,
            )
            merged = _merge_dates(date_hit, generic, self.profile.year_range)
            if merged is not None and generic is not None:
                date_hit = merged
                date_rule = generic.rule
                date_spans = [*date_spans, generic.span]

        # The number and title work below still masks the date groups alone: a
        # captured `number` is already read straight off the pattern in
        # `_resolve_number`, and widening this mask too would blank out title
        # text the title rules still need.
        masked = mask(spaced, date_spans)
        squashed = squash(masked)
        config_match = (
            match_title(squashed, self._titles, self._articles, self.months.squashed_names)
            if self._titles
            else None
        )
        if trace is not None and self._titles:
            trace.append(
                (
                    "title T1",
                    f"matched {config_match.canonical!r} on {config_match.key!r}"
                    if config_match is not None
                    else "no configured title matches",
                )
            )

        number, number_rule, number_span = self._resolve_number(
            pattern, masked, config_match, trace
        )
        cut = self._cut(date_spans, number_span)
        candidates = self._candidates(pattern, folders, spaced, cut)
        derived = self._derived_title(candidates, spaced)
        title_name, title_source = self._resolve_title(pattern, config_match, candidates, derived)

        date_hit, date_source, fallback_rule = self._apply_fallbacks(
            date_hit, folders, mtime, trace
        )
        issue_date = to_date(date_hit) if date_hit is not None else None
        if issue_date is None:
            date_source, precision = "none", "none"
        else:
            precision = date_hit.precision if date_hit is not None else "none"

        rules = [
            part
            for part in (pattern_rule, date_rule, fallback_rule, number_rule)
            if part is not None
        ]
        rules.append(f"title:{title_source}")
        issue = ParsedIssue(
            title_name=title_name,
            title_source=title_source,
            derived_title=derived,
            issue_date=issue_date,
            date_precision=precision,
            date_source=date_source,
            issue_number=number,
            has_dedup_suffix=clean.has_dedup_suffix
            or "dedup" in (pattern.groups if pattern else {}),
            label=format_label(issue_date, precision, number),
            matched_rule=" ".join(rules),
        )
        if trace is not None:
            trace.append(("result", repr(issue)))
        return issue

    # ----------------------------------------------------------------- private

    def _folder_components(self, path: PurePosixPath) -> tuple[str, ...]:
        parts = path.parts[:-1]
        prefix = self._library_parts
        if prefix and parts[: len(prefix)] == prefix:
            parts = parts[len(prefix) :]
        return tuple(parts)

    def _match_patterns(
        self, spaced: str, trace: Trace | None
    ) -> tuple[PatternHit | None, DateHit | None, list[tuple[int, int]]]:
        """First pattern that matches *and* whose date groups are a real date.

        A pattern that captures an impossible date — a range ending before it
        starts, a 40th of March — has not recognised this name, so the next
        pattern gets its turn.
        """
        candidates: list[tuple[str, re.Pattern[str], str | None]] = [
            (f"pattern[{index}]", regex, None) for index, regex in enumerate(self._patterns)
        ]
        candidates += [(f"pattern[{name}]", regex, name) for name, regex in self._title_patterns]
        for rule, regex, title in candidates:
            match = regex.search(spaced)
            if match is None:
                if trace is not None:
                    trace.append((rule, "no match"))
                continue
            hit = _pattern_hit(rule, match, title=title)
            date_hit, spans, impossible = self._date_from_pattern(hit)
            if impossible:
                if trace is not None:
                    trace.append((rule, f"matched {hit.groups}, but that is not a date; skipped"))
                continue
            if trace is not None:
                trace.append((rule, f"matched, groups {hit.groups}"))
            return hit, date_hit, spans
        return None, None, []

    def _date_from_pattern(
        self, pattern: PatternHit | None
    ) -> tuple[DateHit | None, list[tuple[int, int]], bool]:
        """Turn a pattern's date groups into a hit, with the spans they cover.

        Returns the hit, the spans it occupies, and whether the pattern carried
        date groups that are **not** a date — which is what makes the pattern
        itself not match.
        """
        if pattern is None:
            return None, [], False
        groups = pattern.groups
        spans = [
            pattern.spans[name]
            for name in ("date", "date_end", "day", "month", "year")
            if name in pattern.spans
        ]
        if not spans:
            return None, [], False
        text = groups.get("date")
        year: int | None = None
        month: int | None = None
        day: int | None = None
        if text is not None:
            parsed = _parse_date_group(text)
            if parsed is None:
                return None, [], True
            year, month, day = parsed
        else:
            month = month_number(groups.get("month"), self.months)
            year = int(groups["year"]) if groups.get("year") else None
            day = int(groups["day"]) if groups.get("day") else None
        if year is None and month is None and day is None:
            return None, [], True
        if not valid_date(year, month, day, self.profile.year_range):
            return None, [], True
        day_end = _range_end_day(groups.get("date_end"))
        if groups.get("date_end") is not None and not valid_range_end(day_end, year, month, day):
            return None, [], True
        precision = "day" if day is not None else "month" if month is not None else "year"
        return (
            DateHit(
                rule=pattern.rule,
                year=year,
                month=month,
                day=day,
                day_end=day_end,
                span=(min(start for start, _ in spans), max(end for _, end in spans)),
                precision=precision,
            ),
            spans,
            False,
        )

    def _resolve_number(
        self,
        pattern: PatternHit | None,
        masked: str,
        config_match: TitleMatch | None,
        trace: Trace | None,
    ) -> tuple[int | None, str | None, tuple[int, int] | None]:
        if pattern is not None and pattern.groups.get("number"):
            span = pattern.spans.get("number")
            if not self.profile.number:
                # `number: false` suppresses a pattern's own `number` group too:
                # the span stays excluded from the title work below, but the
                # value itself is not read as an issue number.
                if trace is not None:
                    trace.append(("number", "captured by the pattern, but disabled by the profile"))
                return None, None, span
            return int(pattern.groups["number"]), None, span
        if not self.profile.number:
            if trace is not None:
                trace.append(("number", "disabled by the profile"))
            return None, None, None
        end = config_match.end if config_match is not None else 0
        hit = find_number(
            masked,
            self._numbers,
            allow_bare=self.allow_bare_number,
            bare_from=end,
            year_range=self.profile.year_range,
            trace=trace,
        )
        if hit is None:
            return None, None, None
        return hit.value, hit.rule, hit.span

    @staticmethod
    def _cut(date_spans: list[tuple[int, int]], number_span: tuple[int, int] | None) -> int | None:
        starts = [start for start, _ in date_spans]
        if number_span is not None:
            starts.append(number_span[0])
        return min(starts) if starts else None

    def _candidates(
        self,
        pattern: PatternHit | None,
        folders: tuple[str, ...],
        spaced: str,
        cut: int | None,
    ) -> dict[str, str | None]:
        """The title each non-configured source suggests."""
        return {
            "pattern": _pattern_title(pattern, self.profile.lowercase_words),
            "folder": title_from_folder(folders, lambda name: is_date_component(name, self.months)),
            "filename": title_from_filename(spaced, cut, self.profile.lowercase_words),
        }

    def _derived_title(self, candidates: Mapping[str, str | None], spaced: str) -> str:
        """The title the name itself suggests.

        The file name comes first here, whatever the profile's ``title_source``
        order is: this is what a file lands under in *Unsorted*, and a folder
        name is a poor description of a file that disagrees with it.
        """
        for source in ("pattern", "filename", "folder"):
            value = candidates.get(source)
            if value:
                return value
        return trim_separators(spaced) or spaced

    def _resolve_title(
        self,
        pattern: PatternHit | None,
        config_match: TitleMatch | None,
        candidates: Mapping[str, str | None],
        derived: str,
    ) -> tuple[str, str]:
        """Walk the profile's ``title_source`` order and pick a title."""
        for source in self.profile.title_source:
            if source != "config":
                value = candidates.get(source)
                if value:
                    return value, source
                continue
            if not self._titles:
                continue
            canonical = self._configured_title(pattern, config_match)
            if canonical is not None:
                return canonical, "config"
            if self.profile.unsorted:
                # Titles are configured and none of them matches: the file is
                # kept, under Unsorted, with its derived title beside it.
                return UNSORTED_TITLE, "unsorted"
        return derived, "filename"

    def _configured_title(
        self,
        pattern: PatternHit | None,
        config_match: TitleMatch | None,
    ) -> str | None:
        """T1, either on the whole name or on what a pattern captured."""
        if config_match is not None:
            return config_match.canonical
        captured = _pattern_title(pattern, self.profile.lowercase_words)
        if not captured:
            return None
        from_capture = match_title(
            squash(captured), self._titles, self._articles, self.months.squashed_names
        )
        return from_capture.canonical if from_capture is not None else None

    def _apply_fallbacks(
        self,
        date_hit: DateHit | None,
        folders: tuple[str, ...],
        mtime: dt.datetime | dt.date,
        trace: Trace | None,
    ) -> tuple[DateHit | None, str, str | None]:
        """Fill in a missing date (F1, F2) or a missing year (`mixed`)."""
        moment = mtime.date() if isinstance(mtime, dt.datetime) else mtime
        if date_hit is not None and date_hit.year is not None:
            return date_hit, "filename", None
        for fallback in self.profile.date_fallback:
            if fallback == "folder":
                from_folders = folder_date(folders, self.profile.year_range)
                if from_folders is None:
                    if trace is not None:
                        trace.append(("fallback F1", "no date in the folders"))
                    continue
                if date_hit is None:
                    if trace is not None:
                        trace.append(("fallback F1", f"date from the folders: {from_folders}"))
                    return from_folders, "folder", "F1"
                if trace is not None:
                    trace.append(("fallback F1", f"year from the folders: {from_folders.year}"))
                return _with_year(date_hit, from_folders.year), "mixed", "F1"
            if fallback == "mtime":
                if date_hit is None:
                    if trace is not None:
                        trace.append(("fallback F2", f"date from the mtime: {moment}"))
                    return (
                        DateHit("F2", moment.year, moment.month, moment.day, None, (0, 0), "day"),
                        "mtime",
                        "F2",
                    )
                if trace is not None:
                    trace.append(("fallback F2", f"year from the mtime: {moment.year}"))
                return _with_year(date_hit, moment.year), "mixed", "F2"
        return date_hit, "none" if date_hit is None else "filename", None


def _with_year(hit: DateHit, year: int | None) -> DateHit:
    if year is None:
        return hit
    day = hit.day
    if (
        day is not None
        and hit.month is not None
        and not valid_date(year, hit.month, day, (1, 9999))
    ):
        day = None
    return DateHit(
        rule=hit.rule,
        year=year,
        month=hit.month,
        day=day,
        day_end=hit.day_end,
        span=hit.span,
        precision=hit.precision if day is not None or hit.precision != "day" else "month",
    )


def _is_complete(hit: DateHit) -> bool:
    """Whether a date needs nothing more from the generic rules."""
    return hit.year is not None and hit.month is not None and hit.day is not None


def _merge_dates(
    primary: DateHit,
    extra: DateHit | None,
    year_range: tuple[int, int],
) -> DateHit | None:
    """Fill the gaps of ``primary`` from ``extra``; ``None`` when nothing fits."""
    if extra is None:
        return None
    year = primary.year if primary.year is not None else extra.year
    month = primary.month if primary.month is not None else extra.month
    day = primary.day if primary.day is not None else extra.day
    if (year, month, day) == (primary.year, primary.month, primary.day):
        return None
    if not valid_date(year, month, day, year_range):
        return None
    precision = "day" if day is not None else "month" if month is not None else "year"
    return DateHit(
        rule=primary.rule,
        year=year,
        month=month,
        day=day,
        day_end=primary.day_end if primary.day_end is not None else extra.day_end,
        span=primary.span,
        precision=precision,
    )


def _range_end_day(text: str | None) -> int | None:
    """The day a ``date_end`` group ends on, or ``None`` when it is not one."""
    if text is None:
        return None
    stripped = text.strip()
    if stripped.isdigit() and len(stripped) <= 2:
        return int(stripped)
    parsed = _parse_date_group(stripped)
    return parsed[2] if parsed is not None else None


def _pattern_hit(rule: str, match: re.Match[str], title: str | None = None) -> PatternHit:
    groups = {name: value for name, value in match.groupdict().items() if value is not None}
    spans = {name: match.span(name) for name in groups if match.span(name) != (-1, -1)}
    return PatternHit(rule=rule, groups=groups, spans=spans, title=title)


def _pattern_title(pattern: PatternHit | None, lowercase_words: Sequence[str] = ()) -> str | None:
    if pattern is None:
        return None
    if pattern.title is not None:
        return pattern.title
    captured = pattern.groups.get("title")
    if not captured:
        return None
    return smart_title_case(trim_separators(captured), lowercase_words)


def _parse_date_group(text: str) -> tuple[int, int | None, int | None] | None:
    """Parse a ``date`` group: ``YYYYMMDD``, ``YYYY-MM-DD`` or ``YYYY-MM``."""
    compact = _COMPACT_DATE.match(text)
    if compact is not None:
        return int(compact["year"]), int(compact["month"]), int(compact["day"])
    iso = _ISO_DATE.match(text)
    if iso is not None:
        day = iso["day"]
        return int(iso["year"]), int(iso["month"]), int(day) if day else None
    return None


def format_label(date: dt.date | None, precision: str, number: int | None) -> str:
    """The human readable label of an issue, in English."""
    parts: list[str] = []
    if number is not None:
        parts.append(f"No. {number}")
    if date is not None:
        if precision == "day":
            parts.append(f"{date.day} {LABEL_MONTHS[date.month - 1]} {date.year}")
        elif precision == "month":
            parts.append(f"{LABEL_MONTHS[date.month - 1]} {date.year}")
        elif precision == "year":
            parts.append(str(date.year))
    return " · ".join(parts)
