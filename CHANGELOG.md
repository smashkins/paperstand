# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- A dry-run `organize-plan` command that prints where each file would live
  under a canonical `<Title>/<YYYY>/<Title> - <ISO date>[ - n<number>].pdf`
  layout; nothing it does reaches the running server yet.
- `publication.yml`: a folder anywhere under a library can now declare itself
  a title — a slug, a display title, a kind, a frequency, a language, what
  counts as a duplicate, its declared supplements, a parent — and every PDF
  beneath it, at any depth, is catalogued under that title, never Unsorted.
  See [`folder-layout.md`](docs/folder-layout.md#declared-publications) and
  [`configuration.md`](docs/configuration.md#publicationyml).
- The canonical grammar now reads a volume and a variant off a name —
  `<Title> - <ISO date>[ - [v<volume> ]n<number>][ - <variant>]` — exposed as
  `volume` and `variant` on an issue through the API; the variant is also part
  of the issue label, which OPDS entries show.
- `Title` gains `slug`, `frequency`, `language`, `issue_key`, `parent_slug`
  and `supplements`; `Issue` gains `variant` and `volume`; all nullable, and
  set only for a declared publication.
- OPDS entries carry `dc:language` when their title declares one.
- `organize-plan` plans a declared title's files into its own folder, named
  after the folder's own basename, and prints `-> in place` for a file
  already shaped that way; the summary gains that count.
- `Issue` gains `content_hash`, the file's full SHA-256; `ScanProgress` gains
  `hashed`, how many files the running scan has read in full to compute a
  content hash — new, touched or genuinely changed, and a legacy row
  backfilled once.
- `paperstand organize` and the writable inbox it reads: imports PDFs into the
  canonical layout, atomically and never overwriting a file already in the
  library; a file it cannot place is parked under `unsorted/` and re-read on
  every run, a byte-identical copy of a catalogued file under `duplicates/`.
  `--every` repeats the run on an interval until stopped. See
  [`organizer.md`](docs/organizer.md).
- `PAPERSTAND_INBOX` (default `/inbox`), the folder `paperstand organize` reads.
- An optional `organizer` compose service, off by default, mounting the
  library read-write and running `organize --apply --every
  PAPERSTAND_ORGANIZE_INTERVAL` on a loop — the only container that ever
  writes into the library.
- The image now accepts a command after the entrypoint, so `docker run` (or
  the new compose service) can run `organize` instead of the default `serve`.
- A file that vanishes from the library is no longer removed on the spot: a
  scan marks it missing instead, hidden from the storefront, the calendar,
  *Today*, search, OPDS and every count — its row, cover, pages and reading
  position stay — and only actually forgets it once
  `PAPERSTAND_MISSING_GRACE_DAYS` (default 7, `0` restores the previous
  immediate removal) has passed since the scan that first noticed. The same
  bytes turning up again, at the same path or anywhere else, clears the mark
  without any re-render. `Issue.missing_since`, `GET /api/issues?missing=true`
  and `Stats.missing_count` expose the state; `missing` on the scan progress,
  the scan summary and the `scans` table counts it. See
  [`folder-layout.md`](docs/folder-layout.md#identity).
- A `.paperstand-library` marker at the library root, created by hand and
  remembered for good by the first scan that sees it: once remembered, a
  scan whose root can be listed but has lost the file is refused outright —
  nothing is touched, the `scans` row closes as an error naming the marker —
  telling an unmounted share apart from a library emptied on purpose.
  `HealthResponse.library_marker` reports the same three states (never set
  up, present, lost); `paperstand organize --apply` makes the same check
  before it moves a single file. See
  [The root marker](docs/folder-layout.md#the-root-marker).

### Changed

- Every cover, thumbnail and rendered page now lives under a version
  directory — `<data>/cache/covers/v1/…`, `<data>/cache/pages/v1/…` — and
  every image URL carries `?v=<version>` instead of the file's own
  modification time, so a touch of a PDF changes neither the URL nor the
  ETag. Bumping the version, the whole of a cache invalidation, deletes the
  previous version's directory and lets the next scan or request re-render;
  a cache in the earlier, unversioned layout is moved into `v1/` in place at
  start-up, file by file, without re-rendering anything.

- `organize-plan` now plans an undeclared configured title inside its own
  library, at `<library path>/<Title>/<YYYY>`, rather than at the library
  root — a spot the scanner would never have catalogued.
- The bundled profile now tries the canonical declared-publication grammar
  before its other patterns, so a name shaped `<Title> - <ISO date>` is read
  by it first even outside a declared folder. A file whose name disagrees
  with the folder it sits in now has the name win: `Title - 2026-03.pdf` in a
  folder-titled layout used to take its title from the folder, and now takes
  it from the name, matching every other layout.
- An issue's id now derives from its content, not its path — see
  [`folder-layout.md`](docs/folder-layout.md#identity). The first scan after
  upgrading rewrites every id once: a bookmarked reader URL and an OPDS
  entry's `urn:paperstand:issue:` change once, but every cover is renamed
  rather than re-rendered. After that, renaming or moving a file anywhere in
  its own library keeps its id, its cover, its cached pages and its reading
  position; a byte-identical copy is a duplicate of the original wherever it
  sits in the library, whatever it is called; touching a file without
  changing its bytes no longer re-renders anything; a file replaced with
  different bytes is a new issue.

## [0.2.1] - 2026-09-08

The reader moves to pdf.js 6, and a tap in the middle of a page does what a tap should.

### Changed

- The reader renders with pdf.js 6, shipped as its `legacy` build — the one
  pdf.js states a minimum browser for. The in-browser reader now needs Chrome
  125, Safari 18 or a browser of the same generation or newer; the storefront
  and the OPDS feed are unaffected. Range requests, rendering and memory
  behaviour were checked against the previous major and are unchanged.

### Fixed

- A tap in the middle of the page no longer flashes the reader toolbar before
  hiding it again: it now shows the toolbar when it is hidden and hides it
  when it is shown, as a toggle should. An edge tap, a swipe, a pinch, a pan
  while zoomed, a double tap and a moving mouse or pen still bring the toolbar
  up as before.

## [0.2.0] - 2026-09-08

The catalogue tells you what it is doing, and the parser reads two more numbering shapes.

### Added

- Live scan progress: `GET /api/scan/status` reports the running scan's phase,
  its counters and the elapsed time while it works, refreshed every two seconds;
  Settings shows the phase, the live counters, the elapsed time and a progress
  bar — indeterminate while cataloguing, a fraction of the covers rendered once
  the total is known.
- `POST /api/scan` and `GET /api/scan/status` now document their bodies with
  named OpenAPI schemas, and `GET /api/health` with its own; the frontend's
  hand-written scan and health types are gone in favour of the generated ones.
- The `default` profile reads two more real-world shapes: a year-stamped volume and a
  running issue number, `Title vYYYY cNNN` and `Title cNNN - vYYYY`, in either order.

### Fixed

- A pattern that captures an issue number, or any other named group, no longer has it read
  as a day by the generic date rules: every span the pattern captured is masked before they
  run, not only its date groups.

## [0.1.0] - 2026-09-09

The first release. Paperstand reads a read-only folder of PDF periodicals and turns it into
a newsstand: a catalogue by title and issue date, a cover-first web interface in English and
Italian, an in-browser reader, and an OPDS 1.2 feed for mobile reading apps.

### Added

- Repository scaffold: license, contribution guide, editor and ignore rules, `Makefile`.
- Backend skeleton: FastAPI application factory, `GET /api/health`, SPA static mount,
  settings from `PAPERSTAND_*` environment variables, `paperstand serve` entry point.
- Frontend skeleton: SvelteKit 2 / Svelte 5 SPA with Tailwind v4, self-hosted fonts,
  English and Italian messages, theme toggle and language switch on a placeholder page.
- Sample library generator producing a deterministic set of fake PDF periodicals across
  the supported folder layouts.
- Container image (multi-stage build, `PUID`/`PGID` support) and `docker-compose.yml`.
- `PAPERSTAND_TRUSTED_PROXIES`: the clients whose `X-Forwarded-*` headers are believed.
  Loopback only by default; set it to the reverse proxy's address, or to `*` when the
  proxy is the only thing that can reach the port.
- Parsing core: a generic engine executing declarative YAML **parser profiles**, with a
  bundled `default` profile covering common real-world naming — date folders, numeric and
  handle-like prefixes, duplicate suffixes, Italian and English month names, issue numbers,
  configured titles with aliases and per-title patterns, and an *Unsorted* bucket for the
  files that match none of them.
- `paperstand.yml`: libraries, titles, parser profiles and an ignore list, with
  auto-discovery of one library per top-level folder when the file is absent.
- `paperstand parse-report <dir>` and `paperstand parse-explain <file>`: see what the
  parser makes of a collection, and why, without a database.
- Catalogue database: SQLite in `/data`, WAL, versioned schema. Reading progress is kept
  apart from the issues, so it survives a file disappearing and coming back.
- Background scanner in two phases: a fast walk that only reads names, sizes and
  modification times and catalogues what changed, then a small worker pool that opens the
  new PDFs for their page count, page size, first page text, a 900 px cover and a 300 px
  thumbnail. A PDF that cannot be read costs its own row an error, never the scan.
- Duplicate resolution: same title, same date and same issue number are folded together,
  the copy without a duplicate suffix winning, the largest file breaking a tie.
- A scan that could not read the whole library — an absent mount, a folder that refuses to
  be listed — removes nothing and says so, instead of reading silence as deletion.
- Two libraries whose names reduce to the same identifier are refused when the configuration
  is loaded, naming both, rather than one quietly swallowing the other's files.
- Scan scheduling: one scan on start-up and one every `PAPERSTAND_SCAN_INTERVAL` seconds,
  never two at a time.
- `POST /api/scan` (202, or 409 while a scan runs) and `GET /api/scan/status`.
- `GET /api/health` now reports the database, the last scan and the number of issues, and
  keeps answering during a scan, with an empty library and with no library at all.
- `paperstand scan`: one scan in the foreground, printing its counters.
- Files whose name starts with `@` are catalogued instead of skipped: on a file that is a
  handle prefix the parser strips, while `@`, `.` and `#` still hide a whole folder.
- REST API over the catalogue: `GET /api/libraries`, `/api/stats`, `/api/titles`
  (with `kind`, `library`, `sort` and an `Unsorted` bucket that stays out of the way),
  `/api/titles/{id}` and its per-year counts, `/api/titles/{id}/calendar`,
  `/api/issues` with filters, sorting and pagination, and `/api/issues/{id}` with the
  issues either side of it.
- `GET /api/today`: the day's newspapers — one per title, falling back to the most recent
  day that has any and saying which — the latest issue of every magazine, what is part-read
  and what arrived last. The `Unsorted` bucket stays off the shelves; it is where a file
  goes when the parser cannot place it, and it is browsed deliberately or not at all.
- Reading progress: `GET /api/progress` and `GET|PUT|DELETE /api/issues/{id}/progress`,
  with the page clamped to the document.
- `GET|HEAD /api/issues/{id}/file`: the PDF, with byte ranges, `Accept-Ranges`, a strong
  `ETag`, `If-Range`, `304` and `416` — everything an in-browser reader needs to open a
  large document without downloading it. Never compressed, and never served from outside
  the library root.
- Covers and thumbnails on demand at `/api/issues/{id}/cover.jpg` and `thumb.jpg`, so a
  wiped cache costs a render rather than a blank shelf.
- Server-side page rendering at `/api/issues/{id}/pages/{n}.webp?w=&v=`, at a fixed ladder
  of widths, cached on disk and swept back under `PAPERSTAND_PAGE_CACHE_MAX_MB` least
  recently used first, after every scan and every so many renders. Images are only
  promised to a browser for a year when their `v` still matches the file they came from,
  so a PDF replaced where an old one was cannot leave a stale page on screen. A sweep
  never takes a page that is on its way to a response.
- A reading position moves with its issue: a file read and then found to be a duplicate of
  a better copy hands its bookmark to the copy that stayed, and duplicates never appear
  twice in "continue reading" or in `/api/progress`.
- **OPDS 1.2 catalogue at `/opds`**, so that the collection can be read from a phone or an
  e-reader: Today, Newspapers, Magazines, Recently added and — only when it has something
  in it — Unsorted; one feed per title, fifty issues to a page with `first`, `previous`,
  `next` and `last`; a cover, a thumbnail and one acquisition link to the PDF on every
  entry. Navigation and acquisition feeds carry the content type OPDS says they should, and
  the rules the web interface follows apply here too: duplicates never appear, and *Today*
  falls back to the most recent day that has newspapers.
- OPDS search at `/opds/search?q=`, over the title's name, the file name and the derived
  title, with the OpenSearch description at `/opds/opensearch.xml` that a client builds its
  own search box from.
- In the feed, a file the parser could not place is named after itself — what the parser
  derived from the file name, or the file name — rather than after the *Unsorted* bucket it
  is filed in, which would have made every entry on that shelf read the same.
- Absolute URLs in the feed, from `PAPERSTAND_BASE_URL` when it is set and otherwise from
  the request — including the `X-Forwarded-Proto` and `X-Forwarded-Host` of a reverse proxy
  listed in `PAPERSTAND_TRUSTED_PROXIES`. The forwarded headers are now read by the
  application itself rather than by the server in front of it, so a proxy that rewrites the
  host is honoured, and every deployment gets the URLs the tests describe.
- `docs/opds.md`: the feed tree, how to add the catalogue in Panels, Chunky, Moon+ Reader,
  KOReader and Librera, and how to put the whole application behind basic authentication
  with Caddy or with nginx — Paperstand has no authentication of its own in 0.1.0.
- A committed OpenAPI document and the TypeScript client generated from it
  (`make gen-api`); CI regenerates both and fails if either has moved. The OPDS routes stay
  out of it deliberately: their contract is the OPDS specification, not Paperstand's.
- The storefront: a cover-first web interface over the catalogue. `/` opens on the day's
  newspapers at full size under a large localised date, with "continue reading", the latest
  magazines and what arrived last below it; `/newspapers` and `/magazines` are walls of
  latest covers; `/title/{id}` gives a daily a month calendar of mini front pages and a
  magazine a section per year; `/settings` shows the libraries, the scanner with a live
  "Rescan now", and what the caches cost on disk.
- Covers are drawn at the real page ratio the scanner measured, so a shelf reserves the
  right space before an image exists and nothing on the page moves when it arrives; a
  part-read issue carries a 3 px progress stripe.
- Every string in the interface is in English and Italian, and every date, number and file
  size goes through `Intl` with the active language. `npm run i18n:check` fails the build on
  a catalogue that has drifted or on text hardcoded in a component.
- Reader preferences — spread and where page images come from — are chosen in Settings and
  kept per browser, ready for the reader itself.
- A year of a magazine is walked a page at a time, so an archive with more issues in one
  year than a single request will carry is still reachable in full; a year that fails to
  load stops and offers a retry rather than asking again forever.
- A date is never shown at a finer precision than the file actually carried: a title whose
  latest issue only said "March 2026" says "March 2026", not the first of the month.
- The in-browser reader at `/read/{id}`: pdf.js over the range endpoint, so a 56 MB
  broadsheet shows its front page having fetched about 7% of the file and never a whole
  copy of it. One page or two, the cover always alone; swipe, pinch, double tap, ctrl or
  ⌘ with the wheel, a tenth of each edge to turn the page and the middle to hide the
  chrome, which hides itself anyway after two and a half seconds. Arrow keys, space,
  PageUp/PageDown, Home/End, `+`/`−`/`0`, `f`, `t` and `Esc`, all listed in the toolbar's
  `?` panel.
- A thumbnail strip of the whole issue, lazily fetched from the server's 200 px page
  images, with the current page highlighted and scrolled into view.
- Reading position is restored when an issue is reopened and written back a second after
  the page changes, when the tab is hidden and when the reader is closed — never for a
  cover nobody read past.
- "Rendered by the server" in Settings now does something: the reader draws the backend's
  WebP pages instead of parsing the PDF, for a tablet that cannot afford a worker.
- Leaving the reader destroys the document and its worker: ten issues opened and closed in
  a row leave the heap where they found it. Pages warmed for a spread the reader has already
  left are cancelled rather than left to finish, so a run of thumbnail jumps does not queue
  up work behind the page somebody is actually looking at.
- The reader's last position survives the tab closing: the flush on the way out is a
  keepalive request, and it happens on `pagehide` as well as on a tab being hidden.
- Covers, thumbnails and rendered pages answer `HEAD` as well as `GET`, so a reading app
  can probe a stored catalogue without downloading it. The PDF endpoint and every OPDS feed
  already did.
- An application icon and a web manifest, so "Add to Home Screen" gives Paperstand its own
  icon and name on iOS and Android, and the browser's chrome follows the theme.
- The theme is applied before the first paint: a dark-mode reader no longer gets a flash of
  paper white on every cold load.
- An error page in both languages for an address that is not a route, or a page that would
  not load, with the way back to Today.
- `docs/configuration.md`: every environment variable and the whole `paperstand.yml` schema.
  `docs/sample-library.md` describes the fictional publications the examples use.
- CI on push and pull request, and a release workflow publishing multi-architecture images
  to `ghcr.io/smashkins/paperstand` with OCI metadata labels.

### Known limitations

- No authentication. Put Paperstand behind a reverse proxy, or on a network you trust;
  [`docs/opds.md`](docs/opds.md) has a basic-auth example that a reading app can still use.
- Search is in the OPDS feed only; the web interface has the box disabled.
- Serving Paperstand under a sub-path is not supported.


[Unreleased]: https://github.com/smashkins/paperstand/compare/v0.2.1...HEAD
[0.2.1]: https://github.com/smashkins/paperstand/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/smashkins/paperstand/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/smashkins/paperstand/releases/tag/v0.1.0
