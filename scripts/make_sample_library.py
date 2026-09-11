#!/usr/bin/env python3
"""Generate a deterministic sample library of fake PDF periodicals.

The output mirrors the folder layouts and the filename shapes that Paperstand has
to survive in the wild: date folders, one folder per title, mixed layouts, flat
directories, dedup suffixes, numeric prefixes, handle prefixes, month-only dates
and a few files that must be ignored altogether.

Usage::

    uv run --project backend python scripts/make_sample_library.py ./library

Everything is derived from the title and the date, so two runs with the same
``--today`` produce byte-identical filenames and colours.
"""

from __future__ import annotations

import argparse
import colorsys
import datetime as dt
import hashlib
import shutil
import sys
import time
from collections import Counter
from collections.abc import Iterable
from pathlib import Path

import pymupdf

# Broadsheet-ish and A4 page boxes, in points.
BROADSHEET = (1000.0, 1400.0)
A4 = (595.0, 842.0)

MONTHS_IT = [
    "Gennaio",
    "Febbraio",
    "Marzo",
    "Aprile",
    "Maggio",
    "Giugno",
    "Luglio",
    "Agosto",
    "Settembre",
    "Ottobre",
    "Novembre",
    "Dicembre",
]
MONTHS_EN = [
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
]

# The seven daily titles used for the date-folder layout.
NEWSPAPERS = [
    "Corriere del Ponte",
    "La Gazzetta del Lago",
    "Cronaca 24 Pagine",
    "Il Mattutino",
    "La Gazzetta del Lago Valdora",
    "Giornale della Costa Alta",
    "W La Gazzetta del Lago",
]

LOREM = (
    "Nothing in this file is real. It exists so that a parser, a scanner and a "
    "reader can be exercised end to end without shipping copyrighted material. "
    "The page below carries enough text to make extraction and rendering "
    "measurable."
)


# Written into every generated library. `--clean` deletes a directory only when
# it finds this file, so pointing the generator at a real collection by mistake
# costs nothing.
MARKER_NAME = ".paperstand-sample-library"
MARKER_TEXT = (
    "Written by scripts/make_sample_library.py.\n"
    "Everything beside this file is generated and disposable: its presence is\n"
    "what allows --clean to delete this directory. Never add it to a real\n"
    "library.\n"
)


def protected_paths() -> set[Path]:
    """Directories that are never a sample library, marker or not."""
    repo_root = Path(__file__).resolve().parent.parent
    return {
        Path(repo_root.anchor),
        repo_root,
        Path.home().resolve(),
        Path("/library"),
    }


def clean_refusal(out: Path) -> str | None:
    """Reason not to ``--clean`` ``out``, or ``None`` when it is safe to delete.

    ``--clean`` is an ``rmtree``, so it is allowed only on a directory this
    script wrote itself, and never on a path that could plausibly be a typo for
    something valuable.
    """
    resolved = out.resolve()
    if resolved in protected_paths():
        return f"{resolved} is a protected directory"
    if not (resolved / MARKER_NAME).is_file():
        return f"{resolved} has no {MARKER_NAME} marker, so it was not generated here"
    return None


def digest(value: str) -> int:
    """Stable integer digest of a string (``hash()`` is salted per process)."""
    return int.from_bytes(hashlib.sha1(value.encode("utf-8")).digest()[:8], "big")


def title_color(title: str) -> tuple[float, float, float]:
    """Deterministic, legible background colour for a title."""
    hue = (digest(title) % 360) / 360.0
    return colorsys.hls_to_rgb(hue, 0.82, 0.55)


def underscore(name: str) -> str:
    """Filename-friendly form of a title."""
    return name.replace(" ", "_")


def squash(name: str) -> str:
    """Separator-free camel-ish form, as produced by some naming schemes."""
    parts = name.split()
    return parts[0].lower() + "".join(part.capitalize() for part in parts[1:])


def numeric_prefix(seed: str) -> str:
    """A ``4820117639_258259_`` style prefix."""
    value = digest(seed)
    return f"{1000000000 + value % 3000000000}_{10000 + value % 900000}_"


class LibraryBuilder:
    """Writes the fake PDFs and keeps a per-layout tally."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.counts: Counter[str] = Counter()

    def pdf(
        self,
        rel_path: str,
        *,
        title: str,
        subtitle: str,
        size: tuple[float, float],
        group: str,
    ) -> None:
        """Render one fake periodical to ``rel_path``."""
        path = self.root / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)

        width, height = size
        pages = 4 + digest(rel_path) % 5  # 4..8
        doc = pymupdf.open()

        cover = doc.new_page(width=width, height=height)
        cover.draw_rect(pymupdf.Rect(0, 0, width, height), color=None, fill=title_color(title))
        cover.draw_rect(
            pymupdf.Rect(width * 0.06, height * 0.06, width * 0.94, height * 0.94),
            color=(0.1, 0.1, 0.1),
            width=1.5,
        )
        cover.insert_textbox(
            pymupdf.Rect(width * 0.08, height * 0.12, width * 0.92, height * 0.45),
            title,
            fontname="hebo",
            fontsize=width * 0.075,
            color=(0.06, 0.05, 0.04),
            align=pymupdf.TEXT_ALIGN_CENTER,
        )
        cover.insert_textbox(
            pymupdf.Rect(width * 0.08, height * 0.48, width * 0.92, height * 0.62),
            subtitle,
            fontname="helv",
            fontsize=width * 0.035,
            color=(0.06, 0.05, 0.04),
            align=pymupdf.TEXT_ALIGN_CENTER,
        )
        cover.insert_textbox(
            pymupdf.Rect(width * 0.08, height * 0.86, width * 0.92, height * 0.93),
            "SAMPLE LIBRARY - GENERATED CONTENT",
            fontname="helv",
            fontsize=width * 0.018,
            color=(0.2, 0.18, 0.16),
            align=pymupdf.TEXT_ALIGN_CENTER,
        )

        for number in range(2, pages + 1):
            page = doc.new_page(width=width, height=height)
            page.insert_textbox(
                pymupdf.Rect(width * 0.08, height * 0.08, width * 0.92, height * 0.16),
                f"{title} - page {number} of {pages}",
                fontname="hebo",
                fontsize=width * 0.028,
                color=(0.06, 0.05, 0.04),
            )
            page.insert_textbox(
                pymupdf.Rect(width * 0.08, height * 0.2, width * 0.92, height * 0.9),
                (LOREM + " ") * 4,
                fontname="helv",
                fontsize=width * 0.018,
                color=(0.15, 0.14, 0.12),
            )

        doc.save(path, deflate=True, garbage=3)
        doc.close()
        self.counts[group] += 1

    def raw(self, rel_path: str, content: bytes, group: str) -> None:
        """Write a non-PDF file (or a file that only pretends to be one)."""
        path = self.root / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        self.counts[group] += 1


def newspaper_filename(title: str, day: dt.date, variant: int) -> str:
    """Pick one of the naming shapes seen in real collections."""
    stem = underscore(title)
    month_it = MONTHS_IT[day.month - 1]
    if variant == 0:
        return f"{stem}_{day.day}_{month_it}_{day.year}.pdf"
    if variant == 1:
        return f"{stem}_-_{day.day}_{month_it}_{day.year}.pdf"
    if variant == 2:
        prefix = numeric_prefix(f"{title}{day.isoformat()}")
        return f"{prefix}{stem}_{day.day}_{month_it}_{day.year}.pdf"
    if variant == 3:
        return f"{stem}_{day.isoformat()}.pdf"
    if variant == 4:
        return f"{squash(title)}{day.day}{month_it}{day.year}.pdf"
    return f"{stem}_{day.day}{month_it}_{day.year}.pdf"


def build_newspapers(builder: LibraryBuilder, today: dt.date, days: int = 14) -> None:
    """``Newspapers/YYYY/MM/DD/`` — mixed titles inside date folders."""
    for offset in range(days):
        day = today - dt.timedelta(days=offset)
        folder = f"Newspapers/{day.year}/{day.month:02d}/{day.day:02d}"
        label = f"{day.day} {MONTHS_IT[day.month - 1]} {day.year}"
        for index, title in enumerate(NEWSPAPERS):
            # A weekly supplement and a local edition that do not appear daily.
            if title == "W La Gazzetta del Lago" and day.weekday() != 5:
                continue
            if title == "Giornale della Costa Alta" and offset % 3 == 2:
                continue
            variant = (offset + index) % 6
            name = newspaper_filename(title, day, variant)
            builder.pdf(
                f"{folder}/{name}",
                title=title,
                subtitle=label,
                size=BROADSHEET,
                group="Newspapers/YYYY/MM/DD",
            )

    # Edge cases pinned to specific days so that the set stays reproducible.
    newest = today
    label_new = f"{newest.day} {MONTHS_IT[newest.month - 1]} {newest.year}"
    folder_new = f"Newspapers/{newest.year}/{newest.month:02d}/{newest.day:02d}"

    # A handle prefix left over from wherever the file came from.
    builder.pdf(
        f"{folder_new}/@handle_Il_Mattutino_{newest.day}_"
        f"{MONTHS_IT[newest.month - 1]}_{newest.year}.pdf",
        title="Il Mattutino",
        subtitle=label_new,
        size=BROADSHEET,
        group="Newspapers/YYYY/MM/DD",
    )

    older = today - dt.timedelta(days=4)
    label_old = f"{older.day} {MONTHS_IT[older.month - 1]} {older.year}"
    folder_old = f"Newspapers/{older.year}/{older.month:02d}/{older.day:02d}"
    month_old = MONTHS_IT[older.month - 1]

    # Two same-day duplicates, with the two dedup suffix shapes.
    builder.pdf(
        f"{folder_old}/Corriere_del_Ponte_{older.day}_{month_old}_{older.year}-1.pdf",
        title="Corriere del Ponte",
        subtitle=label_old,
        size=BROADSHEET,
        group="Newspapers/YYYY/MM/DD",
    )
    builder.pdf(
        f"{folder_old}/La_Gazzetta_del_Lago_{older.day}_{month_old}_{older.year}_(1).pdf",
        title="La Gazzetta del Lago",
        subtitle=label_old,
        size=BROADSHEET,
        group="Newspapers/YYYY/MM/DD",
    )
    # A filename with no year at all: the year has to come from the folders.
    builder.pdf(
        f"{folder_old}/Cronaca_24_Pagine_{older.day}_{month_old}.pdf",
        title="Cronaca 24 Pagine",
        subtitle=label_old,
        size=BROADSHEET,
        group="Newspapers/YYYY/MM/DD",
    )
    # A supplement that looks like a daily but is not one of the configured titles.
    builder.pdf(
        f"{folder_old}/Corriere_del_Ponte_Weekend_{older.day}_{month_old}_{older.year}.pdf",
        title="Corriere del Ponte Weekend",
        subtitle=label_old,
        size=BROADSHEET,
        group="Newspapers/YYYY/MM/DD",
    )
    # An English daily, to exercise the English month table.
    builder.pdf(
        f"{folder_old}/The_Daily_Ledger_{older.day}_{MONTHS_EN[older.month - 1]}_{older.year}.pdf",
        title="The Daily Ledger",
        subtitle=f"{older.day} {MONTHS_EN[older.month - 1]} {older.year}",
        size=BROADSHEET,
        group="Newspapers/YYYY/MM/DD",
    )


def build_magazines(builder: LibraryBuilder, year: int) -> None:
    """``Magazines/<Title>/`` and ``Magazines/YYYY/<Title>/MM/`` in A4."""
    weekly = [
        (1650, 1, 9),
        (1651, 1, 16),
        (1652, 2, 6),
        (1653, 2, 20),
        (1655, 3, 6),
        (1656, 3, 20),
    ]
    for number, month, day in weekly:
        month_it = MONTHS_IT[month - 1]
        label = f"{day} {month_it} {year}"
        if number % 2 == 0:
            rel = f"Magazines/Orizzonte/Orizzonte_{number}_-_{day}_{month_it.lower()}_{year}.pdf"
            group = "Magazines/<Title>"
        else:
            prefix = numeric_prefix(f"Orizzonte{number}")
            rel = (
                f"Magazines/{year}/Orizzonte/{month:02d}/"
                f"{prefix}Orizzonte_N.{number}_-_{day}_{month_it}_{year}.pdf"
            )
            group = "Magazines/YYYY/<Title>/MM"
        builder.pdf(rel, title="Orizzonte", subtitle=label, size=A4, group=group)

    # An issue with no number at all.
    builder.pdf(
        f"Magazines/{year}/Orizzonte/03/"
        f"{numeric_prefix('Orizzonte-nodate')}Orizzonte_-_20_Marzo_{year}.pdf",
        title="Orizzonte",
        subtitle=f"20 Marzo {year}",
        size=A4,
        group="Magazines/YYYY/<Title>/MM",
    )

    # Two titles that merely start like a configured one and must not be folded into it.
    builder.pdf(
        f"Magazines/{year}/Orizzonte/03/{numeric_prefix('Kids')}Orizzonte_Kids_-_Marzo_{year}.pdf",
        title="Orizzonte Kids",
        subtitle=f"Marzo {year}",
        size=A4,
        group="Magazines/YYYY/<Title>/MM",
    )
    builder.pdf(
        f"Magazines/{year}/Orizzonte/03/"
        # The en dash is deliberate: it shows up in real file names.
        f"{numeric_prefix('OPQ')}OPQ_The_Other_Orizzonte_N.4_–_6-19_Marzo_{year}.pdf",  # noqa: RUF001
        title="OPQ The Other Orizzonte",
        subtitle=f"6-19 Marzo {year}",
        size=A4,
        group="Magazines/YYYY/<Title>/MM",
    )

    # A monthly with a subtitle after the issue number, and month-only dates.
    monthly = [
        (1, 1, "Il_grande_mare"),
        (2, 2, None),
        (3, 3, "Il_mare_che_ci_separa"),
        (8, 8, None),
    ]
    for number, month, subtitle in monthly:
        month_it = MONTHS_IT[month - 1]
        middle = f"_{subtitle}" if subtitle else "_"
        if number % 2 == 0:
            rel = f"Magazines/Confini/Confini_n._{number}_{year}.pdf"
            group = "Magazines/<Title>"
        else:
            prefix = numeric_prefix(f"Confini{number}")
            rel = (
                f"Magazines/{year}/Confini/{month:02d}/"
                f"{prefix}Confini_N_{number}{middle}_{month_it}_{year}.pdf"
            )
            group = "Magazines/YYYY/<Title>/MM"
        builder.pdf(rel, title="Confini", subtitle=f"{month_it} {year}", size=A4, group=group)

    # The same title spelled three ways, apostrophe included.
    for index, stem in enumerate(["L'Almanacco", "LAlmanacco", "L'_Almanacco"]):
        number = 34 + index
        builder.pdf(
            f"Magazines/Almanacco/{stem}_{number}_{year}.pdf",
            title="L'Almanacco",
            subtitle=f"n. {number} / {year}",
            size=A4,
            group="Magazines/<Title>",
        )

    # A title that only exists as a folder, with no configured entry.
    builder.pdf(
        f"Magazines/Circuito/Circuito_#12_{year}.pdf",
        title="Circuito",
        subtitle=f"{year}",
        size=A4,
        group="Magazines/<Title>",
    )


def build_zines(builder: LibraryBuilder, year: int) -> None:
    """``Zines/`` — a mostly flat library discovered without any configuration."""
    builder.pdf(
        f"Zines/Circuito/Circuito_{year}-03.pdf",
        title="Circuito",
        subtitle=f"Marzo {year}",
        size=A4,
        group="Zines (flat)",
    )
    builder.pdf(
        f"Zines/Random_Mag_March_{year}.pdf",
        title="Random Mag",
        subtitle=f"March {year}",
        size=A4,
        group="Zines (flat)",
    )
    builder.pdf(
        "Zines/Something.pdf",
        title="Something",
        subtitle="no date anywhere",
        size=A4,
        group="Zines (flat)",
    )

    # Volume-and-issue numbering: a year-stamped volume and a running issue
    # number, in either order. `default` reads both with a pattern of its own,
    # no naming rule required. A trailing month name still yields month
    # precision; without one, the volume year stands alone at year precision.
    month_it = MONTHS_IT[1]
    builder.pdf(
        f"Zines/Bright_Meadows_v{year}_c02_{month_it}_{year}.pdf",
        title="Bright Meadows",
        subtitle=f"Vol. {year} No. 2 - {month_it} {year}",
        size=A4,
        group="Zines (flat)",
    )
    builder.pdf(
        f"Zines/Bright_Meadows_c15_-_v{year - 1}.pdf",
        title="Bright Meadows",
        subtitle=f"Vol. {year - 1} No. 15",
        size=A4,
        group="Zines (flat)",
    )


def build_publications(builder: LibraryBuilder, today: dt.date) -> None:
    """``publication.yml`` folders — additive to every layout above.

    Dated 30 and 31 days before ``today``, outside the 14-day date-folder
    window ``build_newspapers`` fills, so nothing here collides with a file
    the date-folder layout already wrote for the same title and day.
    """
    d1 = today - dt.timedelta(days=30)
    d2 = today - dt.timedelta(days=31)
    label1 = f"{d1.day} {MONTHS_IT[d1.month - 1]} {d1.year}"
    label2 = f"{d2.day} {MONTHS_IT[d2.month - 1]} {d2.year}"

    corriere = "Newspapers/Corriere del Ponte"
    builder.raw(
        f"{corriere}/publication.yml",
        (
            b"id: corriere-del-ponte\n"
            b"title: Corriere del Ponte\n"
            b"kind: newspaper\n"
            b"frequency: daily\n"
            b"language: it\n"
            b"issue_key: date\n"
            b"supplements: [Weekend]\n"
        ),
        group="publication.yml",
    )
    builder.pdf(
        f"{corriere}/{d1.year}/Corriere del Ponte - {d1.isoformat()}.pdf",
        title="Corriere del Ponte",
        subtitle=label1,
        size=BROADSHEET,
        group="Newspapers/<Title> (declared)",
    )
    builder.pdf(
        f"{corriere}/{d1.year}/Corriere del Ponte - {d1.isoformat()} - Weekend.pdf",
        title="Corriere del Ponte",
        subtitle=f"{label1} - Weekend",
        size=BROADSHEET,
        group="Newspapers/<Title> (declared)",
    )
    builder.pdf(
        f"{corriere}/{d1.year}/Corriere del Ponte - {d2.isoformat()} - Speciale.pdf",
        title="Corriere del Ponte",
        subtitle=f"{label2} - Speciale",
        size=BROADSHEET,
        group="Newspapers/<Title> (declared)",
    )

    # The declared title drops the parenthesised edition; the folder — and
    # the file name, which repeats it — keeps it. A folder differing from the
    # declared title is exactly the point: this file joins the *configured*
    # "La Gazzetta del Lago Valdora" title row, which the date-folder files
    # elsewhere in Newspapers/ already feed.
    valdora = "Newspapers/La Gazzetta del Lago (Valdora)"
    builder.raw(
        f"{valdora}/publication.yml",
        (b"title: La Gazzetta del Lago Valdora\nparent: la-gazzetta-del-lago\nlanguage: it\n"),
        group="publication.yml",
    )
    builder.pdf(
        f"{valdora}/{d1.year}/La Gazzetta del Lago (Valdora) - {d1.isoformat()}.pdf",
        title="La Gazzetta del Lago Valdora",
        subtitle=label1,
        size=BROADSHEET,
        group="Newspapers/<Title> (declared)",
    )

    # No `titles:` entry names "Bright Meadows" in the configured Magazines
    # library — without the declaration this file would be Unsorted there.
    bright_meadows = "Magazines/Bright Meadows"
    builder.raw(
        f"{bright_meadows}/publication.yml",
        b"kind: magazine\nfrequency: monthly\nlanguage: en\n",
        group="publication.yml",
    )
    builder.pdf(
        f"{bright_meadows}/2024/Bright Meadows - 2024-03 - v2024 n03.pdf",
        title="Bright Meadows",
        subtitle="Vol. 2024 No. 3 - March 2024",
        size=A4,
        group="Magazines/<Title> (declared)",
    )

    # Invalid: `frequenzy` is a typo `publication.yml` refuses. Circuito's own
    # PDF, written by build_magazines, stays exactly as it is today — this
    # only exercises the one-warning-per-folder path on every sample scan.
    builder.raw(
        "Magazines/Circuito/publication.yml", b"frequenzy: monthly\n", group="publication.yml"
    )


def build_noise(builder: LibraryBuilder, today: dt.date) -> None:
    """Files and folders that the walker has to skip."""
    folder = f"Newspapers/{today.year}/{today.month:02d}/{today.day:02d}"
    builder.pdf(
        f"{folder}/@eaDir/SYNOPHOTO_THUMB.pdf",
        title="Thumbnail junk",
        subtitle="ignored",
        size=A4,
        group="ignored",
    )
    builder.pdf(
        f"{folder}/.hidden.pdf",
        title="Hidden",
        subtitle="ignored",
        size=A4,
        group="ignored",
    )
    builder.pdf("#recycle/x.pdf", title="Recycled", subtitle="ignored", size=A4, group="ignored")
    builder.raw(
        "Newspapers/notes.txt",
        b"Scratch notes that are not a periodical.\n",
        group="ignored",
    )
    builder.raw(
        "comics/Girandola/Girandola v2026 c3668.cbr",
        b"Rar!\x1a\x07\x00not-a-real-archive\n",
        group="ignored",
    )


def summarise(counts: Counter[str], order: Iterable[str]) -> str:
    lines = []
    for group in order:
        lines.append(f"  {group:<28} {counts[group]:>4}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("out", type=Path, help="directory to fill with the sample library")
    parser.add_argument("--days", type=int, default=14, help="days of newspapers (default: 14)")
    parser.add_argument(
        "--today",
        type=dt.date.fromisoformat,
        default=dt.date.today(),
        help="most recent newspaper date, YYYY-MM-DD (default: today)",
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        help=f"delete the output directory first (only if it holds a {MARKER_NAME} marker)",
    )
    args = parser.parse_args(argv)

    out: Path = args.out
    if args.clean and out.exists():
        refusal = clean_refusal(out)
        if refusal is not None:
            print(f"refusing to --clean: {refusal}", file=sys.stderr)
            return 2
        shutil.rmtree(out)
    out.mkdir(parents=True, exist_ok=True)
    (out / MARKER_NAME).write_text(MARKER_TEXT, encoding="utf-8")

    started = time.perf_counter()
    builder = LibraryBuilder(out)
    build_newspapers(builder, args.today, args.days)
    build_magazines(builder, args.today.year)
    build_zines(builder, args.today.year)
    build_publications(builder, args.today)
    build_noise(builder, args.today)
    elapsed = time.perf_counter() - started

    order = [
        "Newspapers/YYYY/MM/DD",
        "Magazines/<Title>",
        "Magazines/YYYY/<Title>/MM",
        "Zines (flat)",
        "Newspapers/<Title> (declared)",
        "Magazines/<Title> (declared)",
        "publication.yml",
        "ignored",
    ]
    pdf_count = sum(1 for _ in out.rglob("*.pdf"))
    print(f"Sample library written to {out}")
    print(summarise(builder.counts, order))
    print(f"  {'total PDFs':<28} {pdf_count:>4}")
    print(f"Generated in {elapsed:.2f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
