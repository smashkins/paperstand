# The sample library

Every example in this repository — the documentation, the test fixtures, the screenshots,
`paperstand.example.yml` — uses the same set of **fictional** publications. None of them
exists; none of the places they are named after exists either. They are not decoration:
each one is in the set because it covers a naming shape the parser has to get right, and
several of them are there to be got *wrong* in a specific, checkable way.

Generate a library of them with:

```bash
make sample-library OUT=./library
```

That writes about 110 PDFs, all generated: a coloured cover with the masthead and the date,
a few pages of filler text, and `SAMPLE LIBRARY · GENERATED CONTENT` printed at the foot of
every page. It also writes a `.paperstand-sample-library` marker, which is the only thing
`--clean` will delete a directory for.

## Newspapers

| Publication | What it is there for |
| --- | --- |
| **Corriere del Ponte** | The plain case: a three-word masthead, no article, no number. Its weekend supplement, `Corriere_del_Ponte_Weekend_…`, is the *Unsorted* case — a file whose name starts with a configured title but is not that title. |
| **La Gazzetta del Lago** | The leading article is optional, so `La_Gazzetta_del_Lago_…` and `Gazzetta_del_Lago_…` are the same publication, and so is the squashed `laGazzettaDelLago30Agosto2026`. |
| **La Gazzetta del Lago Valdora** | A regional edition, and the case for configuring one. A title only matches when what follows it is a number, a month or nothing, so `…del_Lago_Valdora_…` is *not* swallowed by the shorter `La Gazzetta del Lago`: unconfigured it lands in *Unsorted*, and adding it to the list promotes it to a title of its own. |
| **La Gazzetta del Lago Sud** | A second regional edition, deliberately *not* configured, so it has to land in *Unsorted* rather than in the national title. Fixture table only. |
| **W La Gazzetta del Lago** | A supplement whose name is a letter in front of another title. It must not match the title it contains. |
| **Giornale della Costa Alta** | A four-word masthead with no article, to check that a long name survives the date rules intact. |
| **Il Mattutino** | Another optional article, on a two-word name: `Il_Mattutino_…`, `Mattutino_…` and `ilMattutino17Marzo2026` are all it. Also the publication used for the `@handle_` prefix case. |
| **Cronaca 24 Pagine** | A number *inside* the masthead, which must be read as neither an issue number nor a day. The bundled profile keeps `number: true`; what protects the 24 is that the engine switches off the bare-integer rule N2 for a library of kind `newspaper`, and that the configured title matches the whole masthead before any number rule runs. The number is not the last word either, so no date rule can take it for a day. |
| **The Daily Ledger** | English month names, and `Ledger_March_17_2026` as well as `Ledger_17_March_2026`. |
| **TDL** | A short all-capitals code, which title casing must leave exactly as it is. Fixture table only. |

## Magazines

| Publication | What it is there for |
| --- | --- |
| **Orizzonte** | The base case: a weekly with a high issue number and a date, `Orizzonte_N.1655_-_6_Marzo_2026`. |
| **Orizzonte Kids** | A suffix. `Orizzonte` must not match it, or the children's edition disappears into the parent. |
| **OPQ The Other Orizzonte** | A prefix. Same rule from the other side: a title that *contains* a configured name is not that name. |
| **Confini** | Issue numbers in two spellings, `Confini_n._8_2026` and `Confini_N_3_…`, and a subtitle after the number that has to be dropped from the title. |
| **L'Almanacco** | The apostrophe, in every spelling that turns up: `L'Almanacco`, `LAlmanacco` and `L'_Almanacco`, the last with a space the parser has to close up. |
| **Circuito** | A `#` before the number, and a flat folder with no year in it. |
| **Bright Meadows** | Volume-and-issue numbering, in either order: `Bright_Meadows_v2024_c02_Febbraio_2024` (month precision, the volume year and a trailing month) and `Bright_Meadows_c15_-_v2023` (year precision, no month). The pattern's `number` group is what keeps the issue number out of the date — without it, `c02` reads as day 2. |
| **Random Mag**, **Something** | No date in the name at all. One falls back to the folder, the other to the file's modification time. |

## Places

`Valdora`, `Costa Alta`, `Ponte`, `Lago`, `Sud` are invented. Nothing in this repository
names a real city, region or country, in a file name, a fixture, a screenshot or a
configuration example. When you need another one, invent another one.

## Where the set is defined

| File | What it holds |
| --- | --- |
| `scripts/make_sample_library.py` | The generator: the titles, the layouts, the file name variants, the covers |
| `backend/tests/fixtures/filenames.py` | The fixture table: one row per naming shape, with the exact result the parser must produce |
| `paperstand.example.yml` | The configuration those titles are catalogued with |

The generator and the fixture table are deliberately separate. The generator produces a
library you can look at; the fixture table pins down what each name must parse to, down to
the date precision and the rule that decided. A new naming shape belongs in both.
