# Parser profiles

No naming rule lives in Paperstand's code. The catalogue is built by a generic engine that
executes a **profile**: a declarative, YAML description of how a file name is turned into a
title, an issue date and an issue number. Paperstand ships a `default` profile that covers
common real-world naming; a library can use it as it is, extend it (`extends: default`) or
replace it entirely, without touching Python.

## How a name is parsed

One pass, always in this order:

1. **strip** — regexes removed from the stem: prefixes some tools add, duplicate suffixes.
2. **replace** — separators unified: underscores to spaces, every dash to `-`, every
   apostrophe to `'`, runs of whitespace collapsed. Two forms come out of this: `spaced`
   (readable) and `squashed` (`[a-z0-9]` only, accents removed).
3. **patterns** — the profile's own regexes, in order, first match wins; then any
   per-title pattern. Whatever named groups they capture is taken as given.
4. **generic rules** — for everything a pattern did not capture: dates D1–D7, numbers
   N1–N2, titles T1–T3.
5. **fallbacks** — a date the name does not carry comes from the folders (F1) and then
   from the file's modification time (F2).

The result carries `title_name`, `title_source`, `derived_title`, `issue_date`,
`date_precision`, `date_source`, `issue_number`, `has_dedup_suffix`, `label` and
`matched_rule`. `matched_rule` is the audit trail — `D3 title:config`,
`D6 N1 title:config`, `pattern[0] title:config`, `D3 F1 title:config`, `F2 title:filename`.

## Where a profile lives

Profiles go under `parsers:` in `paperstand.yml`, and a library picks one with `parser:`.
A library with no `parser:` uses `default`.

```yaml
parsers:
  my-dailies:
    extends: default
    # …

libraries:
  - name: Newspapers
    path: Newspapers
    kind: newspaper
    parser: my-dailies
```

`extends` resolution:

- a profile **without** `extends` starts from an empty profile, **not** from `default`;
- a key the child does not set is inherited;
- a list the child sets **replaces** the parent's — unless its first element is `"+"`,
  which **appends** the rest instead:

```yaml
strip: ['^\d+_\d+_'] #  replaces the parent's list
strip: ["+", '^scan_'] #  keeps the parent's and adds one
```

`patterns:` is a list like any other, so the same rule applies to it: a child that sets
`patterns:` **without** a leading `"+"` starts from nothing, losing `default`'s
volume-and-issue patterns exactly as it always has — and, since the canonical grammar
(below) is itself the first entry of `default`'s own `patterns:`, losing that too. A
declared publication's own folder still wins the title on such a profile — that comes from
the `publication.yml` sitting in the folder, not from the pattern — but nothing reads its
name the way the grammar means it to be read: the volume and the variant, which only the
canonical pattern's own groups ever capture, come back empty, and a file the grammar would
date to the month or the year falls back to whatever the generic date rules make of it
instead. The scanner logs one warning per scan for a library whose profile lacks the
bundled pattern and holds a publication folder, so a profile missing the `"+"` does not
fail silently.

Every profile is validated when it is loaded: regexes must compile, named groups must be
ones the engine knows, languages must have a month table, `extends` must point at an
existing profile. A failure names the profile and the offending pattern and stops the load:

```
paperstand.yml: config: profile 'my-dailies': patterns: pattern
'^(?P<publication>.+)$' captures unknown group(s): publication; allowed: date,
date_end, day, dedup, month, number, subtitle, title, year
```

## Schema

Every key is optional. No key outside this table is allowed: a profile refuses extra keys,
so a misspelt `lowercase_word` is not quietly ignored — it stops the load with
`lowercase_word: Extra inputs are not permitted`.

| Key | Type | Default | What it does |
| --- | --- | --- | --- |
| `extends` | string | — | profile to start from |
| `strip` | list of regexes | `[]` | removed from the stem, in order, before anything else. A `dedup` group marks a duplicate copy |
| `replace` | list of `[regex, replacement]` | `[]` | applied after the strips |
| `languages` | list | `[]` | month tables to enable: `it`, `en`, `fr`, `de`, `es`, `pt`, plus any key of `months` |
| `months` | map | `{}` | custom month tables, `language: {name: number}` |
| `articles` | list | `[]` | leading articles that may be present on one side only when a title is matched |
| `lowercase_words` | list | `[]` | words a derived title keeps lowercase unless they open it |
| `patterns` | list of regexes | `[]` | tried in order on the spaced form; first match wins |
| `date_rules` | list | `D1`…`D7` | which generic date rules to try |
| `date_fallback` | list | `[folder, mtime]` | where a missing date comes from |
| `title_source` | list | `[config, pattern, folder, filename]` | order of title resolution |
| `number` | bool | `true` | read issue numbers at all |
| `number_tokens` | list | `[]` | tokens that introduce a number (`n`, `no`, `#`…) |
| `year_range` | `[int, int]` | `[1900, 2099]` | accepted as a date, refused as an issue number |
| `unsorted` | bool | `true` | send a file to *Unsorted* when titles are configured and none matches |

### Named groups a pattern may capture

`title`, `day`, `month` (a number or a name in the active languages), `year`,
`date` (`YYYYMMDD`, `YYYY-MM-DD` or `YYYY-MM`), `date_end` (the end of a range),
`number`, `subtitle`, `dedup`, `volume`, `variant`.

`volume` and `variant` are read by the bundled canonical grammar (below), which is what a
declared publication's files are named with; a custom pattern may capture them too. A
`volume` is only meaningful together with `number` — the running number a volume groups
issues by — and `variant` is whatever trails a canonical name's last ` - `, read back as
`ParsedIssue.variant`.

A pattern may capture only some of them: the missing pieces fall through to the generic
rules and then to the fallbacks. A pattern that captures only `year` out of
`Title 17 March 2026` still ends up with a full date — D3 reads the day and the month off
what the pattern left behind, and `matched_rule` says `pattern[0] D3`. Patterns are matched
case-insensitively, so `(?P<month>[a-z]+)` matches `Marzo`.

> `date_end` is parsed and validated so that ranges such as `6-19 Marzo 2026` are read
> correctly, but only the start of the range is stored on the issue. An end that is not a
> real day of that month, or that falls before the start, means the pattern has not
> recognised the name at all: the next pattern, and then the generic rules, get their turn.

Every group a pattern captures — `number` and `subtitle` included, not only the date groups
— is invisible to the generic date rules that follow: the text is masked out before D1–D7
run over what the pattern left behind. A pattern that reads a volume year and an issue
number out of `Title v2024 c02 Febbraio 2024` still leaves `02` out of D3's reach, so it
stays the issue number rather than becoming the 2nd day of a February date.

### The generic date rules

| Rule | Shape | Precision |
| --- | --- | --- |
| `D1` | `YYYY-MM-DD` (also `.`, `/` and a space) | day |
| `D2` | `DD-MM-YYYY` (also `.`, `/` and a space) | day |
| `D3` | `D(-D)? <month> YYYY?` | day |
| `D4` | `<month> D, YYYY` | day |
| `D5` | `YYYY-MM` | month |
| `D6` | `<month> YYYY`, or a full month name on its own | month |
| `D7` | an isolated year | year |

They are tried in that order and the first valid match wins; the span it occupies is
removed before the number and title rules run. A missing year is filled from the folders or
the mtime, which is what makes `date_source: mixed`. `D6` accepts an abbreviation only when
a year follows it — `Mag` is an Italian abbreviation for May *and* an English word, and a
title is a likelier reading than a date.

The separator of `D1` and `D2` is whatever sits between the numbers, as long as it is the
same on both sides — including a space, because by the time the rules run the profile has
already turned the underscores of `Title_17_03_2026` into spaces.

`year_range` governs all of them: a four digit number outside it is not a year. `D3` then
keeps the day and the month it did find and treats the number as something else, and a
four digit *folder* outside the range does not open a date folder either.

### The generic number rules

| Rule | Shape |
| --- | --- |
| `N1` | one of `number_tokens`, an optional dot, then 1–5 digits: `N.1655`, `n. 8`, `#12` |
| `N2` | a standalone 1–5 digit integer that is not a year and not part of the matched title. Standalone means what it says: `Formula1` is a title, not issue 1 of `Formula` |

`N2` is switched off on its own for libraries of `kind: newspaper`. `number: false`
switches off N1 and N2 both, and a pattern's own `number` group as well — a date the same
pattern captured still stands, only the value is not read as an issue number.

### The generic title rules

| Rule | What it is |
| --- | --- |
| `T1` | a configured title, matched on the squashed form, anchored at the start |
| `T2` | the nearest ancestor folder that is not a date |
| `T3` | the file name up to the first date or number span, smart-cased |

`T1` accepts a name when what follows it is the end of the string, a digit, a month name or
an `n` followed by a digit — and the **longest** configured name wins. That pair of rules is
what keeps `La Gazzetta del Lago Valdora`, `W La Gazzetta del Lago` and `La Gazzetta del Lago` apart, and what
sends `La Gazzetta del Lago Sud` to Unsorted rather than into `La Gazzetta del Lago`.

## The bundled `default` profile

```yaml
# Regexes removed from the stem, in order, before anything else.
strip:
  # A numeric prefix some tools put in front of the name.
  - '^\d+_\d+_'
  # A handle-like prefix, "@something_" or "@something ".
  - '^@[A-Za-z0-9_]+?[_\s]+'
  # Duplicate copies: " (1)" and "_(1)".
  - '(?P<dedup>[ _]\(\d{1,2}\))$'
  # Duplicate copies: a trailing "-1", when it cannot be the tail of a date.
  # The two lookbehinds keep "2026-03-17" and "2026-3-17" intact, and the
  # digit alternation refuses "-01".."-12" right after a year, which is a
  # month, not a copy counter. A file that really is the tenth to twelfth
  # duplicate of the same name keeps its suffix; that is the safe way round.
  - '(?<!\d{4}-\d)(?<!\d{4}-\d{2})(?P<dedup>-(?:[1-9]|1[3-9]|[2-9]\d))$'

# Regex -> replacement pairs, applied in order after the strips.
replace:
  - ['[_]+', ' '] # underscores are separators
  - ["[‐‑‒–—―−]", '-'] # every kind of dash becomes a plain hyphen
  - ["[‘’ʼ´]", "'"] # every kind of apostrophe becomes a plain one
  - ['\s+', ' '] # collapse runs of whitespace

# Month tables to enable. Bundled: it, en, fr, de, es, pt.
languages: [it, en]

# Articles that may be present on one side only when a title is matched, so a
# configured "Il Mattutino" still recognises "Mattutino_...".
articles: [il, lo, la, le, gli, l, the]

# Words a derived title keeps lowercase unless they open it. Only entirely
# lowercase words are ever touched: anything already capitalised is left as is.
lowercase_words: [
  'il', 'lo', 'la', 'i', 'gli', 'le', 'l', 'un', 'uno', 'una',
  'di', 'del', 'dello', 'della', 'dei', 'degli', 'delle', 'da', 'dal', 'dalla',
  'in', 'nel', 'nella', 'con', 'su', 'sul', 'per', 'tra', 'fra', 'e',
  'ed', 'a', 'al', 'alla', 'the', 'of', 'and', 'or', 'on', 'for',
  'to', 'at', 'by', 'from', 'an',
]

# The declared-publication grammar: `<Title> - <ISO date>[ - [v<volume> ]n<number>][ -
# <variant>]`, tried first. `date` is day or month precision; a bare `year` is year
# precision.
#
# Volume-and-issue numbering: a year-stamped volume and a running issue
# number, in either order. Neither is anchored at the end, so a trailing
# "<Month> YYYY" still reaches the generic date rules below.
patterns:
  - '^(?P<title>.+?) - (?:(?P<date>\d{4}-\d{2}(?:-\d{2})?)|(?P<year>\d{4}))(?: - (?:v(?P<volume>\d{1,5}) )?n(?P<number>\d{1,5}))?(?: - (?P<variant>[^-\s][^\n]*?))?$'
  - '^(?P<title>.+?)\s+v(?P<year>\d{4})(?!\d)\s+c(?P<number>\d{1,5})(?!\d)'
  - '^(?P<title>.+?)\s+c(?P<number>\d{1,5})(?!\d)\s*-\s*v(?P<year>\d{4})(?!\d)'

date_rules: [D1, D2, D3, D4, D5, D6, D7]
date_fallback: [folder, mtime]
title_source: [config, pattern, folder, filename]

number: true
# ('no' is quoted: unquoted, YAML would read it as the boolean false.)
number_tokens: ['numero', 'num', 'issue', 'nr', 'no', 'n', '#']

year_range: [1900, 2099]
unsorted: true
```

The file itself is `backend/paperstand/parsing/profiles/default.yml`. Every key it sets is
above, with every value it gives them; only the comments are shorter here than there.

## Worked examples

### 1. Italian dailies in date folders

```
Newspapers/2026/03/17/4820117639_258259_Corriere_del_Ponte_17_Marzo_2026.pdf
Newspapers/2026/03/17/Corriere_del_Ponte_17_Marzo.pdf
Newspapers/2026/03/17/Cronaca_24_Pagine_17_Marzo_2026.pdf
Newspapers/2026/03/18/La_Gazzetta_del_Lago_Valdora_18_Marzo_2026.pdf
```

```yaml
libraries:
  - name: Newspapers
    path: Newspapers
    kind: newspaper # bare integers are not issue numbers here
    titles:
      - Corriere del Ponte
      - name: Cronaca 24 Pagine
        aliases: [Cronaca 24 Pagine, Cronaca24Pagine]
      - La Gazzetta del Lago
      - La Gazzetta del Lago Valdora
```

No profile at all: `default` handles the numeric prefix, the Italian month, the missing
year (taken from the folders, `date_source: mixed`), the alias and the regional edition.
`Cronaca 24 Pagine` keeps its `24` because the library is of kind `newspaper`.

### 2. English monthlies, one folder per title

```
Magazines/Circuito/Circuito_2026-03.pdf
Magazines/Circuito/Circuito_March_2026.pdf
Magazines/Bright Meadows/Bright_Meadows_March_17_2026.pdf
```

```yaml
parsers:
  english:
    extends: default
    languages: [en] # Italian month names would only add ambiguity

libraries:
  - name: Magazines
    path: Magazines
    kind: magazine
    parser: english
    titles: [Circuito, Bright Meadows]
```

`D5` reads `2026-03`, `D6` reads `March 2026` and `D4` reads `March 17 2026`; the first two
give `date_precision: month`, stored as 1 March. With the `titles` list left out, the folder
name becomes the title (`title_source: folder`).

### 3. Short codes and compact dates

```
Codes/ABC_20260317.pdf
Codes/XYZ-2026-03-17.pdf
```

```yaml
parsers:
  codes:
    # No `extends`: this profile starts empty and does exactly one thing.
    patterns:
      - '^(?P<title>[A-Z]{3})[_-](?P<date>\d{8}|\d{4}-\d{2}-\d{2})$'

libraries:
  - name: Codes
    path: Codes
    kind: newspaper
    parser: codes
    titles:
      - name: Alpha Daily
        aliases: [ABC]
      - name: Zeta Times
        aliases: [XYZ]
```

Both files come out as `Alpha Daily` and `Zeta Times`, dated 2026-03-17, with
`matched_rule: pattern[0] title:config`: the pattern supplies the date and the code, and
the aliases turn the code into the real title.

### 4. Volume-and-issue numbering

```
Zines/Bright_Meadows_v2024_c02_Febbraio_2024.pdf
Zines/Bright_Meadows_c15_-_v2023.pdf
```

No configuration at all: `default` carries both shapes, below the canonical grammar, which
does not recognise either — neither has a ` - ` before its date. `parse-explain` on the
first shows what the masking pays for — the `02` of `c02` stays out of D3's reach because
the pattern captured it, not because it happens to be a date group:

```
steps:
  replace '[_]+' -> ' '  'Bright_Meadows_v2024_c02_Febbraio_2024' -> 'Bright Meadows v2024 c02 Febbraio 2024'
  spaced                 'Bright Meadows v2024 c02 Febbraio 2024'
  squashed               'brightmeadowsv2024c02febbraio2024'
  folders                []
  pattern[0]             no match
  pattern[1]             matched, groups {'title': 'Bright Meadows', 'year': '2024', 'number': '02'}
  masked                 '               v     c   Febbraio 2024'
  date D1                no match
  date D2                no match
  date D3                no match
  date D4                no match
  date D5                no match
  date D6                matched 'Febbraio 2024' at (25, 38)

result:
  issue_date     2024-02-01
  date_precision month
  issue_number   2
  matched_rule   pattern[1] D6 title:pattern
```

The `masked` step is the pattern's own spans blanked out of `spaced`: `Bright Meadows`,
`2024` and `02` are gone, but the `v` and `c` that are not part of any group survive, and so
does `Febbraio 2024`, which D6 then reads at month precision. The second file has no
trailing month, so `c15 - v2023` gives issue number 15 and the year 2023 alone, at year
precision, `matched_rule: pattern[2] title:pattern`.

### A pattern for one title only

When a single title needs a shape of its own, it does not need a profile:

```yaml
titles:
  - name: Confini
    pattern: '^Confini\s*N?\s*(?P<number>\d+)'
```

The pattern is tried on the spaced form before the generic rules; whatever it does not
capture — here, the date — still falls through to them.

## Tuning a profile

```bash
paperstand parse-report ./library --config /data/paperstand.yml
```

```
library root: /library
configuration: /data/paperstand.yml
libraries: Newspapers (newspaper), Magazines (magazine)

rel_path                                               title               date        source         number  rule
-----------------------------------------------------  ------------------  ----------  -------------  ------  ------------------
Magazines/Confini/Confini_n._8_2026.pdf                Confini             2026-01-01  filename/year  8       D7 N1 title:config
Newspapers/2026/03/17/Corriere_del_Ponte_17_Marzo.pdf  Corriere del Ponte  2026-03-17  mixed/day      -       D3 F1 title:config

2 file(s), 0 unsorted
```

Read it top to bottom and look for two things: rows filed under `Unsorted`, and rows whose
`source` is `folder` or `mtime` when the name clearly holds a date. Then take one of them
apart:

```bash
paperstand parse-explain "./library/Newspapers/2026/03/17/Corriere_del_Ponte_17_Marzo.pdf"
```

```
file:          /library/Newspapers/2026/03/17/Corriere_del_Ponte_17_Marzo.pdf
configuration: /data/paperstand.yml
library root:  /library
rel path:      Newspapers/2026/03/17/Corriere_del_Ponte_17_Marzo.pdf
library:       Newspapers (kind newspaper, parser default)
titles:        4 configured
mtime:         2026-03-17T06:12:03

steps:
  replace '[_]+' -> ' '  'Corriere_del_Ponte_17_Marzo' -> 'Corriere del Ponte 17 Marzo'
  spaced                 'Corriere del Ponte 17 Marzo'
  squashed               'corrieredelponte17marzo'
  folders                ['2026', '03', '17']
  pattern[0]             no match
  pattern[1]             no match
  pattern[2]             no match
  date D1                no match
  date D2                no match
  date D3                matched '17 Marzo' at (19, 27)
  title T1               matched 'Corriere del Ponte' on 'corrieredelponte'
  number N1              no match
  number N2              disabled
  fallback F1            year from the folders: 2026

result:
  title          Corriere del Ponte
  title_source   config
  derived_title  Corriere del Ponte
  issue_date     2026-03-17
  date_precision day
  date_source    mixed
  issue_number   -
  dedup_suffix   no
  label          17 March 2026
  matched_rule   D3 F1 title:config
```

The usual fixes, in the order they are usually needed:

1. **A file went to Unsorted** — add the name (or an alias) to the library's `titles`.
2. **`title T1` says "no configured title matches"** but the name looks right — the
   boundary rule refused it: something that is neither a digit, a month nor `n<digit>`
   follows the name. Add the longer name as a title of its own.
3. **The date came from the folders or the mtime** — the shape is not covered by D1–D7.
   Add a `pattern` capturing `day`, `month` and `year`, or a `strip` rule removing whatever
   sits in the way.
4. **A number was read out of a title** — set `kind: newspaper` on the library, or
   `number: false` on the profile.
5. **A prefix keeps ending up in the title** — add it to `strip` with a leading `"+"` so
   the default's own rules stay in place.

After every change, run `parse-report` again and compare the two outputs: the whole point
of the report is that it is diffable.
