# The organizer, in dry-run

`organize-plan` prints where every PDF under a directory *would* live if the library were
laid out under one canonical name, without writing, moving, renaming or deleting a single
file. It runs the same parser as `parse-report`, over the same walk, and is safe to run
against a real collection: it never opens a file for writing.

```bash
docker compose exec paperstand paperstand organize-plan /library
```

Or, outside the container, against a generated sample library:

```bash
make sample-library OUT=./library
uv run --project backend python -m paperstand organize-plan ./library
```

`--config PATH` picks a `paperstand.yml` explicitly, exactly as `parse-report` does; without
it, the libraries are auto-discovered from the top-level folders.

## The canonical layout it targets

```
<Title>/<YYYY>/<Title> - <ISO date>[ - n<number>].pdf
```

- `Title` — the configured title, used verbatim as both the folder name and the start of the
  file name.
- `ISO date` — ISO 8601, at the precision the parser actually found: `2026-09-10`, `2026-09`
  or `2026`. A precision the name and the folders do not support is never guessed at.
- `n<number>` — the issue number, with no zero padding (`n8`, `n1655`), present only when the
  parser resolved one.

A file the parser cannot place with confidence is reported **unsorted**, with a reason,
rather than guessed at:

- no configured title matches the name;
- there is no date anywhere in the name or the folders;
- the only date found would come from the file's modification time — a guess the organizer
  must never bake into a permanent name.

## Reading the output

One line per file, in the walker's deterministic order, followed by any collisions and a
summary:

```
Newspapers/2026/03/17/Corriere_del_Ponte_17_Marzo_2026.pdf -> Corriere del Ponte/2026/Corriere del Ponte - 2026-03-17.pdf
Newspapers/2026/03/17/La_Gazzetta_del_Lago_Sud_17_Marzo_2026.pdf -> unsorted: no configured title matches "La Gazzetta del Lago Sud"
Magazines/Orizzonte/Orizzonte_1630_-_5_settembre_2026.pdf -> Orizzonte/2026/Orizzonte - 2026-09-05 - n1630.pdf

2 planned, 1 unsorted, 0 collision(s)
```

When two or more files resolve to the same canonical path — a real duplicate under a
different name, a `(1)` or `-1` dedup copy, or two distinct issues the parser happens to
conflate — they are printed together under a `COLLISION` marker instead of one silently
winning:

```
COLLISION Corriere del Ponte/2026/Corriere del Ponte - 2026-03-17.pdf
  Newspapers/2026/03/17/Corriere_del_Ponte_17_Marzo_2026.pdf
  Newspapers/2026/03/17/Corriere_del_Ponte_2026-03-17.pdf
```

## What it does not do, yet

This is a preview, nothing else. `organize-plan` never creates, moves, renames or deletes a
file, and what it prints does not reach the running server, the database or the page cache
in any way. A supplement or a variant, an edition folder, and any actual move of a file are
not part of this command; they belong to later work on the canonical library layout.
