# Configuration

Paperstand runs with no configuration at all: it discovers one library per top-level folder
under the library root and derives titles from the folder and file names it finds.
Configuration exists to make the catalogue *exact* — to tell "La Gazzetta del Lago" from
"La Gazzetta del Lago Valdora", to skip a folder, to teach the parser a naming shape it has
not met.

There are two separate things to configure, and they do not overlap.

- **Environment variables** are the container contract: where the library is, where the
  data goes, which port, which user. They are set in `.env` or in the compose file.
- **`paperstand.yml`** is the catalogue: libraries, titles, aliases, parser profiles,
  ignore rules. It lives on the data volume.

---

## Environment variables

Everything Paperstand-specific is prefixed `PAPERSTAND_`. Three names are deliberately not —
`PORT`, `TZ` and `PUID`/`PGID` — because container platforms, NAS interfaces and reverse
proxies already know what those mean.

### Paths

| Variable | Default | What it does |
| --- | --- | --- |
| `PAPERSTAND_LIBRARY` | `/library` | The root of the PDF collection. The server never writes inside it; the optional organizer service is the only thing that does, and all it ever adds is a file moved in from the inbox. |
| `PAPERSTAND_INBOX` | `/inbox` | Where `paperstand organize` imports PDFs from — see [Organizer](#organizer) below. Read only by that command; the server never looks at it. |
| `PAPERSTAND_DATA` | `/data` | The writable directory: the database, the covers, the page cache. Everything the server writes is under here. |
| `PAPERSTAND_CONFIG` | `<data>/paperstand.yml` | An explicit path for the configuration file, when it should not live on the data volume. |
| `PAPERSTAND_STATIC` | unset | The directory holding the built web interface. Unset means the API runs without one; the container image sets it to `/app/static`. |

The database is always `<data>/paperstand.db` and the caches are always `<data>/cache`.
Neither has a variable of its own: splitting them across filesystems helps nobody, and
keeping them together is what makes "delete `/data` and rescan" a complete reset.

### Scanning

| Variable | Default | What it does |
| --- | --- | --- |
| `PAPERSTAND_SCAN_ON_START` | `true` | Scan once at start-up. Set it to `false` on a very large library if you would rather trigger the first scan yourself. |
| `PAPERSTAND_SCAN_INTERVAL` | `900` | Seconds between automatic scans. `0` switches the schedule off; *Rescan now* in Settings and `POST /api/scan` still work. |
| `PAPERSTAND_MISSING_GRACE_DAYS` | `7` | How long a catalogued issue whose file has vanished is kept, hidden, before its row is forgotten. `0` restores the earlier behaviour: gone on the very first scan that does not find it. A negative value is clamped to `0`. |
| `PAPERSTAND_COVER_WORKERS` | `2` | Threads rendering covers during the slow phase of a scan. |

A scan has two phases. The fast one walks the library, parses the names and writes the
catalogue — it is what makes a new issue appear, and on a few thousand files it takes
fractions of a second. The slow one rasterises the covers of everything new, and it is the
one worth giving more workers on a machine that has cores to spare. Before either phase, a
root that can be listed but has lost the `.paperstand-library` marker an earlier scan
remembered is refused outright — see [The root marker](folder-layout.md#the-root-marker) —
and neither phase runs.

An issue's id is the hash of its content, not its path (see [Identity](folder-layout.md#identity)).
Computing a hash means reading the whole file, and `hashed` in `GET /api/scan/status` counts
every file a scan reads for one — new, touched or genuinely changed, and a legacy row backfilled
once — before the catalogue is written. The first scan after upgrading to a build with
content-hash identity reads nearly every file in the library this way, and `hashed` is the only
feedback while that one-time backfill works through it. A second scan hashes only what actually
changed: an untouched file costs the fast phase nothing.

A file that disappears is not necessarily removed on the spot either: `missing` in
`GET /api/scan/status` and in the `scans` table counts rows a scan could not find but kept,
hidden, within `PAPERSTAND_MISSING_GRACE_DAYS` of the scan that first noticed — see
[Identity](folder-layout.md#identity) for what "hidden" means and how a row comes back.

### Organizer

`paperstand organize` — [`organizer.md`](organizer.md) — is a second, optional service in the
container, off by default:

```bash
docker compose --profile organizer up -d
```

| Variable | Default | What it does |
| --- | --- | --- |
| `PAPERSTAND_ORGANIZE_INTERVAL` | `300` | Seconds between organizer runs, when that profile is started. Read only by the organizer service, never by the main one. |

It mounts `LIBRARY_PATH` **read-write** — the only service that ever does, and all it ever
adds is a file moved in from the inbox — and `INBOX_PATH` at `/inbox`. The inbox, like `/data`,
has to be a local filesystem of the host running it: the organizer takes an exclusive lock
there for the length of each run, and that lock is as unreliable over NFS or SMB as SQLite's
own locking is. `LIBRARY_PATH`'s own filesystem, wherever it is mounted from, must support hard
links: a move that cannot link a file into place fails, and is reported, rather than falling
back to a plain, non-atomic copy.

### Rendering and the page cache

| Variable | Default | What it does |
| --- | --- | --- |
| `PAPERSTAND_RENDER_WORKERS` | `2` | Threads rasterising pages on demand. Each one holds a document open, so this is memory as much as processor. |
| `PAPERSTAND_PAGE_CACHE_MAX_MB` | `2048` | How large the rendered-page cache may grow, in megabytes. `0` means no limit. |

Pages are rendered at one of six widths — 200, 400, 800, 1200, 1600 and 2000 pixels — and a
request for anything else is snapped up to the next one, so a hundred slightly different
window sizes still share six cached images. Covers are 900 pixels wide and thumbnails 300,
and both are produced by the scanner rather than on demand.

Eviction is not immediate, which is worth knowing before you read a number off the disk and
conclude the limit is broken:

- The cache is swept **every fifty renders**, and again after every scan. Between sweeps it
  overshoots.
- A page rendered **in the last thirty seconds is never evicted**, so that a sweep cannot
  delete the image the request which triggered it is about to serve. A reader turning pages
  as fast as it can therefore holds more than the limit for as long as it keeps going, and
  falls back to the limit once it stops.
- The limit is floored at the size of the largest cached page. A limit smaller than one
  image would otherwise mean an empty cache and a fresh render for every request.

Both caches carry a version in their path — `<data>/cache/covers/v1/<id[:2]>/…` and
`<data>/cache/pages/v1/<id>/…` — because the parameters an image is rendered with (its width,
its quality, PyMuPDF itself) can change between builds even though the PDF behind it did not.
The version, not the file's modification time, is what an image's URL carries as `?v=`, so a
touch of a PDF changes neither the URL nor the `immutable` response a browser already cached.
Bumping the version is the whole of a cache invalidation: at the next start-up, the previous
version's directory is deleted outright, covers are rendered again by the next scan's slow
phase and pages as they are next asked for. The one-time move from a Paperstand that
had no version directory at all is a plain rename of every existing file into
`v1/` — nothing is re-rendered for that alone.

Deleting `<data>/cache` by hand is safe at any time, running or not. Covers come back on
demand and pages come back as they are asked for.

### Addresses and reverse proxies

| Variable | Default | What it does |
| --- | --- | --- |
| `PORT` | `8080` | The port the server listens on. **Not** `PAPERSTAND_PORT`, which is ignored. |
| `PAPERSTAND_HOST` | `0.0.0.0` | The interface it binds to. |
| `PAPERSTAND_TRUSTED_PROXIES` | `127.0.0.1` | Clients whose `X-Forwarded-*` headers are believed: a comma-separated list of addresses or CIDR blocks, or `*`. |
| `PAPERSTAND_BASE_URL` | unset | The address the outside world reaches Paperstand at, e.g. `https://paperstand.example.org`. |

Only the OPDS feed cares about the last two, because it is the only part of Paperstand that
has to write absolute URLs down — a reading app stores them and comes back to them later.
The web interface uses relative paths throughout and works behind anything.

The feed builds its URLs from the request, corrected by `X-Forwarded-Proto` and
`X-Forwarded-Host` **when the request came from a trusted proxy**. So:

- Reached directly on a local network: nothing to set.
- Behind a reverse proxy on the same host: set `PAPERSTAND_TRUSTED_PROXIES` to the proxy's
  address, or to `*` when the proxy is the only thing that can reach the port at all.
- Behind something that cannot tell Paperstand the truth — a tunnel, a name that exists
  only in your own DNS, a published port that differs from the one the proxy talks to —
  set `PAPERSTAND_BASE_URL`, which wins over everything else.

[`opds.md`](opds.md) has the worked examples.

### Container identity and time

| Variable | Default | What it does |
| --- | --- | --- |
| `PUID` | `1000` | The user id the server process runs as. |
| `PGID` | `1000` | The group id it runs as. |
| `TZ` | the system zone | The timezone "today" is decided in, and dates are formatted in on the server. |

`PUID` and `PGID` are not settings of the application: they are read by the container's
entrypoint, which adjusts its own account to match, hands `/data` to it, and then drops
privileges. The server itself never runs as root, and files it writes under `DATA_PATH`
belong to you. Outside the container they mean nothing — `paperstand serve` on a host runs
as whoever started it.

`TZ` decides what "today" means, which matters more than it sounds: the Today page and the
`/opds/today` feed both ask for the current date, and a server left in UTC will disagree
with a reader several time zones away about which paper is this morning's.

### Logging

| Variable | Default | What it does |
| --- | --- | --- |
| `PAPERSTAND_LOG_LEVEL` | `info` | `critical`, `error`, `warning`, `info`, `debug` or `trace`. |

---

## `paperstand.yml`

The file is optional and lives at `<data>/paperstand.yml` — `/data/paperstand.yml` inside
the container, `${DATA_PATH}/paperstand.yml` on the host. A copy to start from is
[`paperstand.example.yml`](../paperstand.example.yml).

Unknown keys are a hard error rather than a warning, at every level of the file: a
misspelled `titels:` should stop the server, not quietly do nothing for a year. The message
names the file, the key, and what was expected.

### Top level

```yaml
libraries: []   # a list of libraries; default: one per top-level folder
parsers: {}     # named parser profiles; default: only the bundled `default`
ignore: []      # folder and file names skipped everywhere
```

`ignore` entries are shell globs — `comics`, `*.partial`, `_incoming` — matched
case-insensitively against a **single name**, not against a path. Folders whose name starts
with `@`, `.` or `#` are skipped whatever this says, and so are files starting with `.` or
`#`: those are thumbnail caches, recycle bins and half-written downloads in every setup that
has ever existed.

### A library

```yaml
libraries:
  - name: Newspapers        # required — what the interface calls it
    path: Newspapers        # required — relative to the library root
    kind: newspaper         # newspaper | magazine   (default: magazine)
    parser: default         # a key of `parsers`, or the bundled `default`
    titles: []              # see below
```

`kind` decides whether the library's issues are "the day's papers" on the Today page or a
shelf of magazines, and whether a title's archive is drawn as a calendar or as year
sections. It is the single most useful thing to set.

Two libraries whose names reduce to the same identifier — `My Papers` and `my-papers` — are
refused at start-up, because their issues would collide in every URL.

### Titles

A title is a bare string or a mapping:

```yaml
    titles:
      - Il Mattutino
      - name: Cronaca 24 Pagine
        aliases: [La Cronaca 24 Pagine, Cronaca24Pagine]
      - name: Confini
        pattern: '^Confini\s*N?\s*(?P<number>\d+)'
```

| Key | Type | What it does |
| --- | --- | --- |
| `name` | string, required | The canonical name, shown everywhere. |
| `aliases` | list of strings | Other spellings that mean the same title, matched exactly as the name is. |
| `pattern` | regex | A shape of its own for this title's files, tried before the profile's generic rules. Named groups only, from: `title`, `day`, `month`, `year`, `date`, `date_end`, `number`, `subtitle`, `dedup`. |

Listing a title does two things at once: it fixes the spelling the interface shows, and it
stops a longer name being swallowed by a shorter one. Without `La Gazzetta del Lago Valdora`
in the list, its files match `La Gazzetta del Lago` and the regional edition disappears into
the national one.

### Parser profiles

A profile is a declarative description of how a file name becomes a title, a date and an
issue number. No naming rule lives in Paperstand's code, which is the point: a shape the
bundled profile has not met is a few lines of YAML rather than a bug report.

[`parser-profiles.md`](parser-profiles.md) is the reference — every key, every date rule,
what `extends` merges and what it replaces. The short version:

```yaml
parsers:
  my-dailies:
    extends: default          # without this a profile starts EMPTY, not from `default`
    strip: ['+', '^scan[_ ]']  # a leading "+" appends to the parent's list
    number: false             # so "Cronaca 24 Pagine" keeps its 24
```

and then `parser: my-dailies` on the library that needs it.

### Changing the file

**No restart is needed.** Every scan reloads `paperstand.yml`, so an edit takes effect at
the next scheduled scan — or immediately, from *Settings → Rescan now*. Its contents are
hashed into the scan state, so a changed file re-parses every issue against the new
configuration rather than only the ones whose PDF moved. Nothing has to be deleted by
hand, and covers are not re-rendered.

---

## `publication.yml`

Where `paperstand.yml` configures the whole library, `publication.yml` declares one title:
dropped into a folder anywhere under the library root, it turns that folder into a
**declared publication** — see [`folder-layout.md`](folder-layout.md#declared-publications)
for what that means for the files beneath it. Every key is optional; `{}` or an empty file
already says "this folder is a publication, defaults everywhere":

```yaml
id: corriere-del-ponte        # slug; default: slugify(title)
title: Corriere del Ponte     # display name; default: the folder's own basename
kind: newspaper                # newspaper | magazine; default: the library's own kind
frequency: daily                # daily | weekly | monthly | irregular
language: it                    # a language tag, e.g. it or en-GB
issue_key: date                 # date | number | date+number (default): what a duplicate is
supplements: [Weekend]          # this title's declared variants
parent: la-gazzetta-del-lago    # the slug of a parent publication, e.g. a regional edition's
```

| Key | Type | What it does |
| --- | --- | --- |
| `id` | slug | The identifying slug stored in the catalogue. Default: `title`, slugified. |
| `title` | string | The display name. Default: the declaring folder's own basename — the folder stays the identity either way. |
| `kind` | `newspaper` \| `magazine` | Overrides the library's own kind for this one title. |
| `frequency` | `daily` \| `weekly` \| `monthly` \| `irregular` | How often it is published. Informational; nothing in Paperstand schedules on it yet. |
| `language` | language tag | Reaches the API's `language` field and OPDS's `dc:language`. |
| `issue_key` | `date` \| `number` \| `date+number` | What makes two files the same issue. Default `date+number`, today's behaviour: same date *and* same number (when both are known) makes a duplicate. |
| `supplements` | list of strings | The variants this title declares, e.g. `[Weekend]`. A variant not listed here is still catalogued, not rejected — only `matched_rule`'s tail says `variant:undeclared` instead of `variant:declared`. |
| `parent` | slug | The slug of a parent publication — a regional edition naming its main title, say. Not validated against an existing folder. |

Unknown keys are refused, the same way `paperstand.yml`'s are. An invalid file is one
`log.warning` naming the file and the offending key; the folder is then treated exactly as
if it declared nothing.

`publication.yml` files are hashed into the scan state the same way `paperstand.yml` is:
editing one — fixing a typo, adding a supplement — re-parses every file beneath the folders
that changed at the next scan, without opening a single PDF; removing one reverts those files
to whatever the usual rules would have made of them.

---

## Running the server

The container's entrypoint runs `paperstand serve`, and so does `make dev-api`. If you start
Uvicorn yourself, **pass `--no-proxy-headers`**:

```bash
uvicorn paperstand.main:app --no-proxy-headers --port 8080
```

Paperstand reads `X-Forwarded-Proto` and `X-Forwarded-Host` itself, from the clients
`PAPERSTAND_TRUSTED_PROXIES` names. Uvicorn will read them too unless told not to, and its
middleware runs *outside* the application: it rewrites the client address from
`X-Forwarded-For` before Paperstand's own check, so Paperstand sees the end user's address
instead of the proxy's, stops believing the forwarded host, and the OPDS feed comes out full
of URLs that only work inside the container's network.

### The other commands

| Command | What it does |
| --- | --- |
| `paperstand serve [--host H] [--port P]` | Run the HTTP server. |
| `paperstand scan [--library P] [--data P] [--config P]` | Scan once, in the foreground, print the counters and exit. Exit code 1 when the scan failed. |
| `paperstand parse-report <directory> [--config P]` | One line per PDF: what it would be catalogued as, and which rule decided. |
| `paperstand parse-explain <file> [--config P] [--library-root P]` | Every step the parser takes on one file. |
| `paperstand --version` | The version. |

`parse-report` and `parse-explain` touch neither the database nor the cache, so they are
safe against a running installation:

```bash
docker compose exec paperstand paperstand parse-report /library/Newspapers
docker compose exec paperstand paperstand parse-explain "/library/Newspapers/2026/03/17/a_file.pdf"
```

[`folder-layout.md`](folder-layout.md) explains what the output means.
