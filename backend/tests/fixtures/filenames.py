"""The file name fixture table.

One row per file, with everything the parser is expected to produce for it. The
shapes are the ones common real-world naming throws at a catalogue: date folders,
numeric prefixes, handle prefixes, duplicate suffixes, missing years, month-only
dates, regional editions, inserts and supplements that merely start like another
title, apostrophes spelled three different ways.

The rows marked ``discovered`` are parsed without any configuration at all, so
that auto-discovery is exercised by the same table.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from pathlib import Path

from paperstand.config import PaperstandConfig, config_from_folders, load_config

#: Injected modification time, so that the mtime fallback is deterministic.
MTIME = dt.date(2026, 4, 1)

#: Top-level folders auto-discovery sees when there is no configuration.
DISCOVERED_FOLDERS: tuple[str, ...] = (
    "Newspapers",
    "Magazines",
    "Zines",
    "Dailies",
    "comics",
    "@eaDir",
    "#recycle",
)


@dataclass(frozen=True)
class Expected:
    """One file and everything the parser must make of it."""

    rel_path: str
    title: str
    date: str | None
    precision: str
    source: str
    number: int | None
    derived: str
    rule: str
    dedup: bool = False
    title_source: str = "config"

    @property
    def id(self) -> str:
        """Readable pytest identifier."""
        return self.rel_path.rsplit("/", 1)[-1]

    @property
    def issue_date(self) -> dt.date | None:
        return dt.date.fromisoformat(self.date) if self.date else None


def example_config_path() -> Path:
    """``paperstand.example.yml`` at the root of the repository.

    The example file doubles as the test configuration: if it drifts from what
    the parser expects, this table fails, which is exactly the point.
    """
    return Path(__file__).resolve().parents[3] / "paperstand.example.yml"


def example_config() -> PaperstandConfig:
    """The configured libraries the table is parsed with."""
    return load_config(example_config_path())


def discovered_config() -> PaperstandConfig:
    """What Paperstand uses when there is no configuration file at all."""
    return config_from_folders(DISCOVERED_FOLDERS)


#: Files parsed with `paperstand.example.yml`.
CONFIGURED: tuple[Expected, ...] = (
    Expected(
        rel_path="Newspapers/2026/03/17/4820117639_258259_Corriere_del_Ponte_17_Marzo_2026.pdf",
        title="Corriere del Ponte",
        date="2026-03-17",
        precision="day",
        source="filename",
        number=None,
        derived="Corriere del Ponte",
        rule="D3 title:config",
    ),
    Expected(
        rel_path="Newspapers/2026/08/30/La_Gazzetta_del_Lago_-_30_Agosto_2026.pdf",
        title="La Gazzetta del Lago",
        date="2026-08-30",
        precision="day",
        source="filename",
        number=None,
        derived="La Gazzetta del Lago",
        rule="D3 title:config",
    ),
    Expected(
        rel_path="Newspapers/2026/08/30/laGazzettadelLago30Agosto2026.pdf",
        title="La Gazzetta del Lago",
        date="2026-08-30",
        precision="day",
        source="filename",
        number=None,
        derived="laGazzettadelLago",
        rule="D3 title:config",
    ),
    Expected(
        rel_path="Newspapers/2026/08/23/Cronaca_24_Pagine_23Agosto_2026.pdf",
        title="Cronaca 24 Pagine",
        date="2026-08-23",
        precision="day",
        source="filename",
        number=None,
        derived="Cronaca 24 Pagine",
        rule="D3 title:config",
    ),
    Expected(
        rel_path="Newspapers/2026/09/05/@handle_Il_Mattutino_5_Settembre_2026.pdf",
        title="Il Mattutino",
        date="2026-09-05",
        precision="day",
        source="filename",
        number=None,
        derived="Il Mattutino",
        rule="D3 title:config",
    ),
    Expected(
        rel_path="Newspapers/2026/03/21/4820117639_258786_W_La_Gazzetta_del_Lago_21_Marzo_2026.pdf",
        title="W La Gazzetta del Lago",
        date="2026-03-21",
        precision="day",
        source="filename",
        number=None,
        derived="W La Gazzetta del Lago",
        rule="D3 title:config",
    ),
    Expected(
        rel_path="Newspapers/2026/03/18/La_Gazzetta_del_Lago_Valdora_18_Marzo_2026.pdf",
        title="La Gazzetta del Lago Valdora",
        date="2026-03-18",
        precision="day",
        source="filename",
        number=None,
        derived="La Gazzetta del Lago Valdora",
        rule="D3 title:config",
    ),
    Expected(
        rel_path=(
            "Newspapers/2026/03/17/4820117640_92574_Giornale_della_Costa_Alta_17_Marzo_2026-1.pdf"
        ),
        title="Giornale della Costa Alta",
        date="2026-03-17",
        precision="day",
        source="filename",
        number=None,
        derived="Giornale della Costa Alta",
        rule="D3 title:config",
        dedup=True,
    ),
    Expected(
        rel_path="Newspapers/2026/03/21/4820117640_92921_Corriere_del_Ponte_21_Marzo_2026_(1).pdf",
        title="Corriere del Ponte",
        date="2026-03-21",
        precision="day",
        source="filename",
        number=None,
        derived="Corriere del Ponte",
        rule="D3 title:config",
        dedup=True,
    ),
    Expected(
        rel_path="Newspapers/2026/03/17/Mattutino_17_Marzo_2026.pdf",
        title="Il Mattutino",
        date="2026-03-17",
        precision="day",
        source="filename",
        number=None,
        derived="Mattutino",
        rule="D3 title:config",
    ),
    Expected(
        rel_path="Newspapers/2026/03/17/Cronaca_24_Pagine_17_Marzo_2026.pdf",
        title="Cronaca 24 Pagine",
        date="2026-03-17",
        precision="day",
        source="filename",
        number=None,
        derived="Cronaca 24 Pagine",
        rule="D3 title:config",
    ),
    Expected(
        rel_path="Newspapers/2026/03/17/Corriere_del_Ponte_17_Marzo.pdf",
        title="Corriere del Ponte",
        date="2026-03-17",
        precision="day",
        source="mixed",
        number=None,
        derived="Corriere del Ponte",
        rule="D3 F1 title:config",
    ),
    Expected(
        rel_path="Newspapers/2026/03/17/Corriere_del_Ponte.pdf",
        title="Corriere del Ponte",
        date="2026-03-17",
        precision="day",
        source="folder",
        number=None,
        derived="Corriere del Ponte",
        rule="F1 title:config",
    ),
    Expected(
        rel_path="Newspapers/2026/03/17/Corriere_del_Ponte_Weekend_17_Marzo_2026.pdf",
        title="Unsorted",
        date="2026-03-17",
        precision="day",
        source="filename",
        number=None,
        derived="Corriere del Ponte Weekend",
        rule="D3 title:unsorted",
        title_source="unsorted",
    ),
    Expected(
        rel_path="Newspapers/2026/03/17/La_Gazzetta_del_Lago_Sud_17_Marzo_2026.pdf",
        title="Unsorted",
        date="2026-03-17",
        precision="day",
        source="filename",
        number=None,
        derived="La Gazzetta del Lago Sud",
        rule="D3 title:unsorted",
        title_source="unsorted",
    ),
    Expected(
        rel_path="Newspapers/2026/03/17/Corriere_del_Ponte_2026-03-17.pdf",
        title="Corriere del Ponte",
        date="2026-03-17",
        precision="day",
        source="filename",
        number=None,
        derived="Corriere del Ponte",
        rule="D1 title:config",
    ),
    Expected(
        rel_path="Newspapers/2026/03/17/Corriere_del_Ponte_17-03-2026.pdf",
        title="Corriere del Ponte",
        date="2026-03-17",
        precision="day",
        source="filename",
        number=None,
        derived="Corriere del Ponte",
        rule="D2 title:config",
    ),
    Expected(
        rel_path="Newspapers/2026/03/17/Corriere_del_Ponte_17.03.2026.pdf",
        title="Corriere del Ponte",
        date="2026-03-17",
        precision="day",
        source="filename",
        number=None,
        derived="Corriere del Ponte",
        rule="D2 title:config",
    ),
    # A numeric date written with the separator the profile turns into a space:
    # both orders, and neither may leave a "17" or a "3" behind as a number.
    Expected(
        rel_path="Newspapers/2026/03/17/Corriere_del_Ponte_17_03_2026.pdf",
        title="Corriere del Ponte",
        date="2026-03-17",
        precision="day",
        source="filename",
        number=None,
        derived="Corriere del Ponte",
        rule="D2 title:config",
    ),
    Expected(
        rel_path="Newspapers/2026/03/17/Corriere_del_Ponte_2026_03_17.pdf",
        title="Corriere del Ponte",
        date="2026-03-17",
        precision="day",
        source="filename",
        number=None,
        derived="Corriere del Ponte",
        rule="D1 title:config",
    ),
    Expected(
        rel_path="Newspapers/2026/03/17/The_Daily_Ledger_17_March_2026.pdf",
        title="The Daily Ledger",
        date="2026-03-17",
        precision="day",
        source="filename",
        number=None,
        derived="The Daily Ledger",
        rule="D3 title:config",
    ),
    Expected(
        rel_path="Newspapers/2026/03/17/The_Daily_Ledger_March_17_2026.pdf",
        title="The Daily Ledger",
        date="2026-03-17",
        precision="day",
        source="filename",
        number=None,
        derived="The Daily Ledger",
        rule="D4 title:config",
    ),
    Expected(
        rel_path="Newspapers/2026/03/17/Daily_Ledger_17_March_2026.pdf",
        title="The Daily Ledger",
        date="2026-03-17",
        precision="day",
        source="filename",
        number=None,
        derived="Daily Ledger",
        rule="D3 title:config",
    ),
    Expected(
        rel_path=(
            "Magazines/2026/Orizzonte/03/9315066428_29685_Orizzonte_N.1655_-_6_Marzo_2026.pdf"
        ),
        title="Orizzonte",
        date="2026-03-06",
        precision="day",
        source="filename",
        number=1655,
        derived="Orizzonte",
        rule="D3 N1 title:config",
    ),
    Expected(
        rel_path="Magazines/Orizzonte/Orizzonte_1630_-_5_settembre_2026.pdf",
        title="Orizzonte",
        date="2026-09-05",
        precision="day",
        source="filename",
        number=1630,
        derived="Orizzonte",
        rule="D3 N2 title:config",
    ),
    Expected(
        rel_path=("Magazines/2026/Orizzonte/03/9315066428_29845_Orizzonte_-_20_Marzo_2026.pdf"),
        title="Orizzonte",
        date="2026-03-20",
        precision="day",
        source="filename",
        number=None,
        derived="Orizzonte",
        rule="D3 title:config",
    ),
    Expected(
        rel_path=("Magazines/2026/Orizzonte/03/9315066428_29573_Orizzonte_Kids_-_Marzo_2026.pdf"),
        title="Unsorted",
        date="2026-03-01",
        precision="month",
        source="filename",
        number=None,
        derived="Orizzonte Kids",
        rule="D6 title:unsorted",
        title_source="unsorted",
    ),
    Expected(
        rel_path=(
            "Magazines/2026/Orizzonte/03/"
            # The en dash is deliberate: it turns up in real file names.
            "9315066428_29715_OPQ_The_Other_Orizzonte_N.4_–_6-19_Marzo_2026.pdf"  # noqa: RUF001
        ),
        title="Unsorted",
        date="2026-03-06",
        precision="day",
        source="filename",
        number=4,
        derived="OPQ The Other Orizzonte",
        rule="D3 N1 title:unsorted",
        title_source="unsorted",
    ),
    Expected(
        rel_path="Magazines/Confini/Confini_n._8_2026.pdf",
        title="Confini",
        date="2026-01-01",
        precision="year",
        source="filename",
        number=8,
        derived="Confini",
        rule="D7 N1 title:config",
    ),
    Expected(
        rel_path=(
            "Magazines/2026/Confini/03/9315066428_29483_Confini_N_1_Il_grande_mare_Gennaio_2026.pdf"
        ),
        title="Confini",
        date="2026-01-01",
        precision="month",
        source="filename",
        number=1,
        derived="Confini",
        rule="D6 N1 title:config",
    ),
    Expected(
        rel_path="Magazines/2026/Confini/03/9315066428_29767_Confini__Febbraio_2026.pdf",
        title="Confini",
        date="2026-02-01",
        precision="month",
        source="filename",
        number=None,
        derived="Confini",
        rule="D6 title:config",
    ),
    Expected(
        rel_path="Magazines/Almanacco/L'Almanacco_36_2026.pdf",
        title="L'Almanacco",
        date="2026-01-01",
        precision="year",
        source="filename",
        number=36,
        derived="L'Almanacco",
        rule="D7 N2 title:config",
    ),
    Expected(
        rel_path="Magazines/Almanacco/LAlmanacco_36_2026.pdf",
        title="L'Almanacco",
        date="2026-01-01",
        precision="year",
        source="filename",
        number=36,
        derived="LAlmanacco",
        rule="D7 N2 title:config",
    ),
    Expected(
        rel_path="Magazines/Almanacco/L'_Almanacco_36_2026.pdf",
        title="L'Almanacco",
        date="2026-01-01",
        precision="year",
        source="filename",
        number=36,
        derived="L' Almanacco",
        rule="D7 N2 title:config",
    ),
    Expected(
        rel_path="Magazines/2026/Orizzonte/03/Confini_n._8_2026.pdf",
        title="Confini",
        date="2026-01-01",
        precision="year",
        source="filename",
        number=8,
        derived="Confini",
        rule="D7 N1 title:config",
    ),
    Expected(
        rel_path="Magazines/Circuito/Circuito_#12_2026.pdf",
        title="Unsorted",
        date="2026-01-01",
        precision="year",
        source="filename",
        number=12,
        derived="Circuito",
        rule="D7 N1 title:unsorted",
        title_source="unsorted",
    ),
)

#: Files parsed with no configuration at all: auto-discovery does the work.
DISCOVERED: tuple[Expected, ...] = (
    Expected(
        rel_path="Zines/Circuito/Circuito_2026-03.pdf",
        title="Circuito",
        date="2026-03-01",
        precision="month",
        source="filename",
        number=None,
        derived="Circuito",
        rule="D5 title:folder",
        title_source="folder",
    ),
    Expected(
        rel_path="Zines/Random_Mag_March_2026.pdf",
        title="Random Mag",
        date="2026-03-01",
        precision="month",
        source="filename",
        number=None,
        derived="Random Mag",
        rule="D6 title:filename",
        title_source="filename",
    ),
    # The same numeric shapes with a derived title, in a library where the bare
    # number rule is enabled: the date must not leave an issue number behind.
    Expected(
        rel_path="Zines/Random_Mag_17_03_2026.pdf",
        title="Random Mag",
        date="2026-03-17",
        precision="day",
        source="filename",
        number=None,
        derived="Random Mag",
        rule="D2 title:filename",
        title_source="filename",
    ),
    Expected(
        rel_path="Zines/Random_Mag_2026_03_17.pdf",
        title="Random Mag",
        date="2026-03-17",
        precision="day",
        source="filename",
        number=None,
        derived="Random Mag",
        rule="D1 title:filename",
        title_source="filename",
    ),
    # A digit glued to a word is part of the title, not an issue number.
    Expected(
        rel_path="Zines/Formula1_2026.pdf",
        title="Formula1",
        date="2026-01-01",
        precision="year",
        source="filename",
        number=None,
        derived="Formula1",
        rule="D7 title:filename",
        title_source="filename",
    ),
    Expected(
        rel_path="Zines/Something.pdf",
        title="Something",
        date="2026-04-01",
        precision="day",
        source="mtime",
        number=None,
        derived="Something",
        rule="F2 title:filename",
        title_source="filename",
    ),
    # Volume-and-issue numbering: the pattern captures the volume year and the
    # issue number, so neither leaks into the generic date rules. A trailing
    # month name still reaches D6 and yields month precision; without one, the
    # pattern's own year stands alone at year precision.
    Expected(
        rel_path="Zines/Bright_Meadows_v2024_c02_Febbraio_2024.pdf",
        title="Bright Meadows",
        date="2024-02-01",
        precision="month",
        source="filename",
        number=2,
        derived="Bright Meadows",
        rule="pattern[0] D6 title:pattern",
        title_source="pattern",
    ),
    Expected(
        rel_path="Zines/Bright_Meadows_c15_-_v2023.pdf",
        title="Bright Meadows",
        date="2023-01-01",
        precision="year",
        source="filename",
        number=15,
        derived="Bright Meadows",
        rule="pattern[1] title:pattern",
        title_source="pattern",
    ),
    Expected(
        rel_path="Dailies/2026/03/17/TDL_2026-03-17.pdf",
        title="TDL",
        date="2026-03-17",
        precision="day",
        source="filename",
        number=None,
        derived="TDL",
        rule="D1 title:filename",
        title_source="filename",
    ),
    Expected(
        rel_path="Dailies/2026/03/17/Cronaca_24_Pagine_17_Marzo_2026.pdf",
        title="Cronaca 24 Pagine",
        date="2026-03-17",
        precision="day",
        source="filename",
        number=None,
        derived="Cronaca 24 Pagine",
        rule="D3 title:filename",
        title_source="filename",
    ),
)

#: Paths the walker has to skip: an ignored folder, thumbnails, notes, hidden
#: files and a recycle bin.
IGNORED_PATHS: tuple[str, ...] = (
    "comics/Girandola/Girandola v2026 c3668.cbr",
    "Newspapers/2026/03/17/@eaDir/x.pdf",
    "Newspapers/notes.txt",
    "Newspapers/2026/03/17/.hidden.pdf",
    "#recycle/x.pdf",
)
