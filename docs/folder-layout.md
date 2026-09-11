# Folder layout

Paperstand reads one read-only root — `/library` in the container — and treats each
top-level folder as a **library**. Inside a library it does not care how the files are
arranged: the file name is read first, the folders are read only when the name is silent.

That root is the `PAPERSTAND_LIBRARY` setting, and `/library` is only its default: it can
be pointed at any directory. [`configuration.md`](configuration.md) has the whole list of
settings.

Nothing is ever written inside the library.

## Supported layouts

All four arrangements below are understood, and they can be mixed inside the same library.

| Layout | Example |
| --- | --- |
| One folder per title | `Magazines/Orizzonte/Orizzonte_1655_-_6_Marzo_2026.pdf` |
| Date folders, mixed titles per day | `Newspapers/2026/03/17/Corriere_del_Ponte_17_Marzo_2026.pdf` |
| Year, title, month | `Magazines/2026/Orizzonte/03/Orizzonte_N.1655_-_6_Marzo_2026.pdf` |
| Flat | `Zines/Random_Mag_March_2026.pdf` |

The names inside them can be as inconsistent as they usually are in the wild: separators of
every kind, a numeric or handle-like prefix, a duplicate suffix (`-1`, `(1)`), a missing
year, a month with no day, a date with no separators at all. What is recognised, and how to
change it, is [`parser-profiles.md`](parser-profiles.md).

## Declared publications

A fifth arrangement, additive to the four above and to whatever else the library already
holds: a folder anywhere under a library that contains a `publication.yml` is a **declared
publication**, and every PDF beneath it, at any depth, belongs to the title it declares —
never to Unsorted, whatever its own name looks like. Nested declarations: the nearest
ancestor wins.

```
Newspapers/Corriere del Ponte/publication.yml
Newspapers/Corriere del Ponte/2026/Corriere del Ponte - 2026-03-17.pdf
Newspapers/Corriere del Ponte/2026/Corriere del Ponte - 2026-03-17 - Weekend.pdf
```

Written the same way it is read, the grammar a declared folder's own files follow is:

```
<Title>/<YYYY>/<Title> - <ISO date>[ - [v<volume> ]n<number>][ - <variant>].pdf
```

`<Title>` here is the declaring folder's own name, not necessarily the yml's `title:` — see
[`configuration.md`](configuration.md#publicationyml) for the full field reference; every key
is optional, and `{}` or an empty file already declares "this folder is a publication,
defaults everywhere". A file under the folder does not have to follow the grammar at all —
an old-shaped name is still catalogued under the declared title, exactly like every other
file beneath it.

A few things worth knowing:

- **A variant not listed under the yml's `supplements` is still accepted.** It is
  catalogued under the title with that variant, and `matched_rule` says `variant:undeclared`
  instead of `variant:declared` — a strict check belongs to the organizer's write mode, not
  the scanner. `label` carries the variant too: `17 March 2026 · Weekend`.
- **The kind is per-title.** A folder's `kind:` overrides the library's own, so one title
  declared `kind: magazine` inside a newspaper library shows up among the magazines.
- **The title is optional.** With no `title:` key the declaring folder's own basename is the
  display title too — `title_source` is `publication` either way.
- **An invalid `publication.yml` is one warning, never a failed scan.** It names the file and
  the offending key, and the folder is then treated exactly as if it held no declaration at
  all — nothing beneath it is skipped, it is simply parsed by the usual rules.
- **A yml at the library root is ignored**, with a warning: a whole library cannot itself be
  "a publication".

## Libraries

With a `paperstand.yml`, each library is declared explicitly:

```yaml
libraries:
  - name: Newspapers
    path: Newspapers # relative to the library root
    kind: newspaper # newspaper | magazine
    titles: [Corriere del Ponte, La Gazzetta del Lago]
```

With **no configuration file at all**, every top-level folder becomes a library:

- its name and its path are the folder name;
- its kind is `newspaper` when the folder name matches
  `newspapers?|dailies|giornali|quotidiani|journaux|zeitungen|periodicos|diarios`,
  and `magazine` otherwise;
- folders starting with `@`, `.` or `#` are skipped.

`kind` is not cosmetic: in a `newspaper` library the parser refuses to read a bare integer
as an issue number, so a title that carries a number of its own keeps it.

## Where the date comes from

The issue date is the axis the whole catalogue turns on, so two fields describe how much of
it is real.

`date_source` — where it was found:

| Value | Meaning |
| --- | --- |
| `filename` | entirely from the file name |
| `mixed` | from the file name, with the year filled in from the folders or the mtime |
| `folder` | the name carried no date; the `YYYY/MM/DD` folders did (a year outside the profile's `year_range` does not count as one) |
| `mtime` | nothing anywhere; the file's modification time was used |
| `none` | no date at all (only possible with a profile that disables the fallbacks) |

`date_precision` — how much of it is known:

| Value | Meaning | Stored as |
| --- | --- | --- |
| `day` | full date | that day |
| `month` | month and year | the first of the month |
| `year` | year only | 1 January |
| `none` | nothing | no date |

The file name is always tried first, and that matters: in most collections the folders
record when a file *arrived*, not when the issue was *published*. A file that landed late
in `2026/03/18/` but says `17 Marzo 2026` in its name is catalogued as the 17th.

`label` puts the two together into an English string — `17 March 2026`,
`No. 1655 · 6 March 2026`, `March 2026`, `No. 8 · 2026`.

## Where the title comes from

In order (the order itself is configurable — see `title_source`):

1. **Configured titles** — matched on a separator-free form of the name, anchored at its
   start, with an optional leading article on either side. The longest configured name
   wins, so `La Gazzetta del Lago Valdora` is never folded into `La Gazzetta del Lago`.
2. **A pattern's `title` group**, when the profile or the title declares one.
3. **The nearest ancestor folder that is not a date.**
4. **The file name**, up to the first date or issue number.

`title_source` on the result says which one it was. `derived_title` always carries what the
file name itself suggests, whatever won.

### The Unsorted bucket

When a library has configured titles and a file matches none of them, the file is **not**
discarded and **not** filed under a guessed name: it goes to that library's **Unsorted**
title, keeping its `derived_title` beside it. That is what makes a regional edition, an
insert or a supplement visible instead of silently merged into the wrong title:

```
Newspapers/2026/03/17/Corriere_del_Ponte_Weekend_17_Marzo_2026.pdf
  -> Unsorted, derived title "Corriere del Ponte Weekend"
```

Adding `Corriere del Ponte Weekend` to the library's `titles` is all it takes to move it
out of Unsorted. A profile with `unsorted: false` uses the derived title instead of the
bucket.

## Duplicates

A copy suffix — `Something-1`, `Something (1)`, `Something_(1)` — is stripped before
parsing and recorded as `has_dedup_suffix`, so both copies parse to the same issue and the
catalogue can tell which one is the extra. A trailing `-03` right after a year is treated
as a month, not as a copy counter: `Circuito_2026-03.pdf` is a March issue.

## What is skipped

Everywhere in the library root, always:

- any folder whose name starts with `@`, `.` or `#` — thumbnail caches, hidden folders,
  recycle bins;
- any file whose name starts with `.` or `#`. A file starting with `@` is kept: a leading
  `@handle_` is a naming shape the parser strips, not metadata;
- anything that is not a `.pdf`.

Plus whatever the `ignore` list holds. Entries are shell-style patterns, matched
case-insensitively against a single file or folder name (not a path):

```yaml
ignore:
  - comics
  - "*.tmp"
```

An ignored folder is not descended into at all.

## Seeing what it does

```bash
paperstand parse-report /library                  # one line per file
paperstand parse-report /library --config /data/paperstand.yml
paperstand parse-explain /library/Newspapers/2026/03/17/Corriere_del_Ponte_17_Marzo.pdf
```

`parse-report` prints the whole tree with the title, date, source and rule of every file;
`parse-explain` prints every step taken on a single one. Neither needs a database, and
both work with no configuration at all.
