"""Issue number rules N1 and N2.

N1
    A number introduced by a token the profile declares (``n``, ``no``, ``nr``,
    ``num``, ``numero``, ``issue``, ``#``…), optionally followed by a dot.
N2
    A standalone integer that is not a year. It is the loosest rule there is, so
    a library of dailies switches it off: a title that carries a number of its
    own would otherwise lose it.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class NumberHit:
    """An issue number found in a name, with the span it occupies."""

    rule: str
    value: int
    span: tuple[int, int]


@dataclass(frozen=True, slots=True)
class CompiledNumberRules:
    """The compiled N1 and N2 regexes of a profile."""

    tokens: re.Pattern[str]
    bare: re.Pattern[str]


#: "Not next to a letter" — a word character that is neither a digit nor an
#: underscore. Written as a lookaround so that accented letters count too.
_NOT_LETTER_BEFORE = r"(?<![^\W\d_])"
_NOT_LETTER_AFTER = r"(?![^\W\d_])"


def compile_number_rules(tokens: Sequence[str], digits: int = 5) -> CompiledNumberRules:
    """Compile the number rules for the given introducing tokens."""
    ordered = sorted({token for token in tokens if token}, key=lambda t: (-len(t), t))
    alternation = "|".join(re.escape(token) for token in ordered) or "(?!)"
    return CompiledNumberRules(
        tokens=re.compile(
            rf"{_NOT_LETTER_BEFORE}(?<!\d)(?:{alternation})"
            rf"\s*\.?\s*(?P<number>\d{{1,{digits}}})(?!\d)",
            re.IGNORECASE,
        ),
        # N2 is a *standalone* integer: a digit run glued to a letter is part of
        # a word — "Formula1" is a title, not issue 1 of "Formula".
        bare=re.compile(
            rf"{_NOT_LETTER_BEFORE}(?<!\d)(?P<number>\d{{1,{digits}}})(?!\d){_NOT_LETTER_AFTER}"
        ),
    )


def find_number(
    text: str,
    rules: CompiledNumberRules,
    *,
    allow_bare: bool,
    bare_from: int = 0,
    year_range: tuple[int, int] = (1900, 2099),
    trace: list[tuple[str, str]] | None = None,
) -> NumberHit | None:
    """Return the issue number of ``text``, N1 first and N2 second.

    ``bare_from`` keeps N2 away from the part of the name that the title
    occupies, so that a number inside a title is never read as an issue number.
    """
    match = rules.tokens.search(text)
    if match is not None:
        if trace is not None:
            trace.append(("number N1", f"matched {match.group(0)!r} at {match.span()}"))
        return NumberHit("N1", int(match.group("number")), match.span())
    if trace is not None:
        trace.append(("number N1", "no match"))
    if not allow_bare:
        if trace is not None:
            trace.append(("number N2", "disabled"))
        return None
    for candidate in rules.bare.finditer(text, bare_from):
        value = int(candidate.group("number"))
        if year_range[0] <= value <= year_range[1]:
            continue
        if trace is not None:
            trace.append(("number N2", f"matched {candidate.group(0)!r} at {candidate.span()}"))
        return NumberHit("N2", value, candidate.span())
    if trace is not None:
        trace.append(("number N2", "no match"))
    return None
