# The organizer

Two commands share one canonical layout: `organize` imports PDFs from a writable **inbox**
into the library, atomically, never overwriting anything already there; `organize-plan` is
its read-only preview, run directly against a library, that writes, moves, renames and
deletes nothing.

## The canonical layout

```
<Title>/<YYYY>/<Title> - <ISO date>[ - [v<volume> ]n<number>][ - <variant>].pdf
```

- `Title` — the configured title, used verbatim as both the folder name and the start of the
  file name.
- `ISO date` — ISO 8601, at the precision actually found: `2026-09-10`, `2026-09` or `2026`.
  A precision the name and the folders do not support is never guessed at.
- `n<number>` — the issue number, with no zero padding (`n8`, `n1655`), present only when the
  parser resolved one.
- `v<volume>` — a volume, written only alongside a number: a volume with no issue number to
  go with it would produce a name the parser could not read back, so it is dropped instead.
- `<variant>` — a supplement or an edition, appended verbatim after the number when the name
  carries one, declared in a `publication.yml` or not.

An issue that would need a reason to stay put — an unmatched title, no date anywhere, a date
that would come only from the file's modification time, an unsafe title, a variant containing
` - ` — is never guessed at either; both commands report why instead.

## `organize`

Reads every PDF in the inbox, in this order, per file:

1. **Settle** — a file whose modification time is less than `--settle` seconds old (default
   `60`) is left exactly where it is, and tried again on the next run.
2. **Verify** — the file is opened; one that fails to open, needs a password, or has no pages
   is unsorted, with the reason.
3. **Hash** — the SHA-256 of the whole file. One the catalogue already knows, or one already
   moved earlier in this same run, makes it a duplicate — whatever it is called. A catalogued
   hash is trusted only while the row's own file is still on disk with exactly that content:
   removed or replaced since the last scan, it is ignored and the file is resolved fresh
   instead of being parked as a duplicate of something that is gone.
4. **Resolve** — the file's own name, never a folder of the inbox, is parsed against every
   configured library exactly as if it sat at that library's root, augmented with every title
   a `publication.yml` declares somewhere beneath it. It resolves when exactly one library
   reads it as a *configured* title.
5. **Claim** — a destination another file of this same run already claimed, with different
   bytes, makes this one unsorted too, naming the file that claimed it first — the file
   earlier in the walk always wins.
6. **Move** — atomic, and never overwriting a file already there; a destination that turns
   out to be occupied on disk at move time is hashed too: equal bytes make it a duplicate,
   different bytes leave it unsorted.

Where a resolved file lands follows the exact rule [`organize-plan` previews](#declared-publications)
below, declared folders included — the only difference is that `organize` actually performs
the move.

```bash
uv run --project backend python -m paperstand organize --inbox ./inbox --library ./library --data ./data
```

Without `--apply` this is a dry run: the report is identical, and nothing is written —
useful for seeing what a first run would do before trusting it with real files. Everything
that cannot be placed goes to `<inbox>/unsorted/` with the reason; a byte-identical copy of a
file already in the library goes to `<inbox>/duplicates/`.

### `unsorted/` is re-read; `duplicates/` never

A file parked under `unsorted/` is a source too, re-evaluated from step 1 on every run:
declaring its title in `paperstand.yml`, or giving it its own `publication.yml` folder, is
enough to make it move out on the very next run. A file that is still unsorted is left
exactly where it is — its sidecar is rewritten only if the reason actually changed. Nothing
under `duplicates/` is ever read again.

### Sidecars

Every parked file gets a one-line sidecar next to it, `<name>.pdf.txt`, holding the reason
(or `duplicate of <rel path>`) and a trailing newline — the only file the organizer ever
writes that is not a PDF, and never yielded by a walk itself, since it is not one. It is
deleted the moment its file moves on, which is the only delete the organizer ever performs.

### Atomic, and never a partial file

Moving a file never overwrites one already at the destination — a destination already there
turns into a duplicate or an unsorted outcome instead, and the source is left exactly as it
was. On the same filesystem a move is a hard link followed by an unlink: the file exists at
both names for no observable instant, and at neither for none either. Across filesystems —
the ordinary case in the container, where the inbox and the library are two separate mounts —
the bytes are copied into a dot-prefixed `.part` file next to the destination first, fsynced,
and only then linked into place. A crash partway through a cross-filesystem move can leave
one such `.part` file behind: invisible to any listing that skips dot-prefixed files, and
safe to delete by hand. Nothing else is ever left behind.

### The lock, and why the inbox must be local

The whole run holds an exclusive lock on `<inbox>/.organizer.lock`, so two organizer processes
can never race over the same inbox and claim the same destination twice. A run that finds the
lock already held prints one line and exits `0` — nothing is read, nothing is written. This
is why the inbox has to be a local filesystem of the host running the organizer: `flock` is
unreliable over NFS and SMB, the same way SQLite's own locking is for `/data`. The library
itself may still be remote, as long as it supports hard links — required either way, since the
cross-filesystem copy above still finishes with a hard link into place, never a plain replace.
A library filesystem that does not support hard links fails every move into it; each one is
reported `Failed` (exit code `1`) rather than being written non-atomically.

### The root marker

Before reading anything, `organize` checks the same thing a scan does — see
[The root marker](folder-layout.md#the-root-marker): if an earlier scan has remembered a
`.paperstand-library` marker at the library root and that file is not there now, the run
touches nothing. It prints one line naming the marker, moves nothing, and exits `1` — in both
modes, since a dry run's destinations would be exactly as wrong as `--apply`'s moves would be
real: a file landed on the host directory behind a failed mount is invisible to the share.
Under `--every` this skips just that iteration, with one warning, and the loop waits for the
next.

### `--every`, and exit codes

`--every SECONDS` repeats the run on that interval — re-reading `paperstand.yml` and every
declared folder each time, so a configuration change or a new `publication.yml` takes effect
on the next run without a restart — until `SIGTERM` or `SIGINT`, the signal `docker stop`
sends.

| Exit code | Meaning |
| --- | --- |
| `0` | Every file has an outcome — moved, duplicate and unsorted all count as success — including a run that found the lock held. |
| `1` | A move failed on an unexpected error, or the library's root marker is remembered but not on disk; either way, the file(s) involved were left exactly where they were. |
| `2` | A usage error: the inbox or the library is not a directory, one is inside the other, or an explicit `--config` does not exist. |

The inbox and the library must be two entirely separate trees — neither inside the other, and
never the same directory — checked before anything is read: a `publication.yml`, an `unsorted/`
or a `duplicates/` folder is meaningful in one and not the other, and letting them overlap would
let the organizer read its own output back as new input.

### The compose profile

In the container this is a second, optional service, off by default:

```bash
docker compose --profile organizer up -d
```

It mounts the library **read-write** — the only container that ever does, and all it ever
adds is a file moved in from the inbox — and the inbox at `INBOX_PATH`, running `organize
--apply --every ${PAPERSTAND_ORGANIZE_INTERVAL:-300}` on a loop. See
[`configuration.md`](configuration.md#organizer). The main `paperstand` service is unchanged,
`/library:ro` included.

### Nothing stays unresolvable

A file the organizer cannot place is not a dead end: declaring its title in `paperstand.yml`,
or creating its own `publication.yml` folder, is enough to make it move on the very next run
— there is nothing to delete or re-copy by hand. A `publication.yml` dropped into the inbox
itself is ignored: the organizer never reads a folder of the inbox, only a file's own name,
so one at the inbox root only logs the usual warning about a declaration at a root.

## `organize-plan`

`organize-plan` prints where every PDF under a directory *would* live under the canonical
layout, without writing, moving, renaming or deleting a single file. It runs the same parser
as `parse-report`, over the same walk, and is safe to run against a real collection: it never
opens a file for writing.

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

### Declared publications

A folder holding a `publication.yml` — see
[`folder-layout.md`](folder-layout.md#declared-publications) — changes where its title
plans to. Instead of `<Title>/<YYYY>`, an issue whose title a declaration owns plans into
`<publication folder>/<YYYY>`, named after the *folder's own basename*, not the yml's
`title:`, which may read differently: the folder stays the identity, the yml only adds
metadata to it. That reaches every file the title's name is resolved to, not only the ones
physically inside the declared folder — a date-folder file elsewhere in the library that a
configured title already joins to the same declaration plans into its folder too, the same
way the scanner already catalogues it under one title row. A configured title with no
declared folder of its own plans inside its own library, at `<library path>/<Title>/<YYYY>`,
never at the library root.

A file already named the way its folder requires — the common case, once a collection has
been organized once — prints `-> in place` instead of a destination:

```
Newspapers/Corriere del Ponte/2026/Corriere del Ponte - 2026-03-17.pdf -> in place
```

Two folders declaring the same title is a configuration mistake, not something to pick
silently: `organize-plan` logs a warning naming both, and the first one found while walking
— folders are visited in name order — keeps the title; every file that resolves to it,
from either folder, plans there.

### Reading the output

One line per file, in the walker's deterministic order, followed by any collisions and a
summary:

```
Newspapers/2026/03/17/Corriere_del_Ponte_17_Marzo_2026.pdf -> Newspapers/Corriere del Ponte/2026/Corriere del Ponte - 2026-03-17.pdf
Newspapers/2026/03/17/La_Gazzetta_del_Lago_Sud_17_Marzo_2026.pdf -> unsorted: no configured title matches "La Gazzetta del Lago Sud"
Magazines/Orizzonte/Orizzonte_1630_-_5_settembre_2026.pdf -> Magazines/Orizzonte/2026/Orizzonte - 2026-09-05 - n1630.pdf
Newspapers/Corriere del Ponte/2026/Corriere del Ponte - 2026-03-16.pdf -> in place

2 planned, 1 in place, 1 unsorted, 0 collision(s)
```

When two or more files resolve to the same canonical path — a real duplicate under a
different name, a `(1)` or `-1` dedup copy, or two distinct issues the parser happens to
conflate — they are printed together under a `COLLISION` marker instead of one silently
winning:

```
COLLISION Newspapers/Corriere del Ponte/2026/Corriere del Ponte - 2026-03-17.pdf
  Newspapers/2026/03/17/Corriere_del_Ponte_17_Marzo_2026.pdf
  Newspapers/2026/03/17/Corriere_del_Ponte_2026-03-17.pdf
```

A file already `in place` still takes part in collision detection: it is a collision, not a
free pass, when another file's plan lands on the exact path it already occupies.

## What it never does

- **Modify or delete a file already in the library.** `organize` only ever adds a file moved
  in from the inbox; a file already at a destination is a duplicate or an unsorted outcome,
  never something to overwrite. `organize-plan` opens nothing for writing at all.
- **Write XMP, or any other metadata, into a PDF.** An issue's bytes never change — the bytes
  *are* its identity, and its duplicate check.
- **Create a folder for an unknown title.** An unmatched name goes to `unsorted/` in the
  inbox, not into a guessed folder the scanner would never catalogue.
- **Move a file already inside the library.** Both commands work inbox-to-library, or
  read-only inside the library; moving a file already catalogued to its canonical name is
  later work on the same mover.
