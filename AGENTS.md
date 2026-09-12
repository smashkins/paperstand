# Working on Paperstand

Technical guidance for contributing to this repository, whether by hand or with a coding
agent. [`CONTRIBUTING.md`](CONTRIBUTING.md) covers the same ground for a human reader; this
file is the short, dense version.

## What this is

A self-hosted reader for a folder of PDF periodicals — newspapers and magazines. It reads a
read-only directory, works out each file's title and issue date from its name, and serves a
cover-first web interface, an in-browser reader and an OPDS 1.2 feed.

## Stack and layout

- **Backend** — Python 3.12, FastAPI, PyMuPDF, Pillow, SQLite (stdlib, WAL mode), managed
  by [uv](https://docs.astral.sh/uv/). Lives in `backend/`, package `paperstand`.
- **Frontend** — SvelteKit 2 with Svelte 5 runes, TypeScript, Tailwind v4, Paraglide for
  English and Italian. `adapter-static`, `ssr = false`: a single-page application. Lives in
  `frontend/`, builds to `frontend/build`.
- **Container** — one image. The frontend is built in `node:22-alpine`, the runtime is
  `python:3.12-slim-bookworm`, and `docker/entrypoint.sh` applies `PUID`/`PGID` and drops
  privileges before starting the server.

```
backend/paperstand/
  api/         REST routers, one module per resource
  cli/         parse-report, parse-explain, organize-plan and organize
  opds/        Atom feed builder and the OPDS router
  organizer/   canonical naming, inbox pipeline, never-overwrite mover, run report
  parsing/     the parser: engine, primitives, and profiles/*.yml
  render/      page rasterisation and the page-cache eviction
  scanner/     walker, hashing, two-phase scanner, covers, scheduler
  config.py    Settings (environment) and PaperstandConfig (paperstand.yml)
  cache.py     the versioned layout of <data>/cache
  publication.py  publication.yml, the declaration that turns a folder into a title
  gaps.py      holes in a title's series, by number and by declared cadence
  db.py  queries.py  schemas.py  main.py  static.py  proxy.py  logging.py
backend/tests/            pytest, with the naming fixture table in fixtures/filenames.py
frontend/src/lib/         api client, components, stores, reader, format helpers
frontend/src/routes/      the pages
frontend/messages/        en.json and it.json — every user-visible string
scripts/                  sample library generator, OpenAPI export, icon rasteriser
docs/                     the documentation the README links to
```

## Commands

Everything runs from the repository root.

| Command | What it does |
| --- | --- |
| `make dev` | API on `:8000` and the frontend dev server on `:5173`, `/api` proxied |
| `make test` | pytest and vitest |
| `make lint` | ruff, mypy (strict), prettier, eslint, svelte-check |
| `make fmt` | Format the backend and the frontend in place |
| `make gen-api` | Export `openapi.json` and regenerate the typed client |
| `make sample-library OUT=./library` | Generate ~110 fake PDFs to develop against |
| `make docker-build` / `make docker-run` | Build and run the image |

`make lint` and `make test` must both be green before a commit. CI runs the same commands,
plus `npm run build` and a build of the container image.

## Running against the sample library

Never develop against a real collection. The generator writes a complete, deterministic
library — every folder layout, every naming shape, fictional mastheads, `SAMPLE LIBRARY ·
GENERATED CONTENT` on every page:

```bash
make sample-library OUT=./library
make dev                     # then open http://localhost:5173
```

To exercise the parser without the database or the cache:

```bash
uv run --project backend python -m paperstand parse-report ./library
uv run --project backend python -m paperstand parse-explain ./library/<file>.pdf
```

To preview the canonical layout, or to import files into it — `organize-plan` writes
nothing; `organize` moves files from a writable inbox into the library, and is a dry run
without `--apply`:

```bash
uv run --project backend python -m paperstand organize-plan ./library
uv run --project backend python -m paperstand organize --inbox ./inbox --library ./library --data ./data
```

## Rules

These are not style preferences; a change that breaks one of them is wrong.

1. **The server never writes inside `/library`**; it is mounted read-only. The organizer
   is the only writer: it adds files from its inbox and, only when explicitly asked
   (`migrate --apply`), moves a file already there to its canonical place. It never
   overwrites, never deletes a file, and never changes a PDF's bytes. Everything
   Paperstand produces goes under `/data`.
2. **No naming rule lives in Python.** File name parsing is driven by the declarative
   profiles in `backend/paperstand/parsing/profiles/`. A shape the parser cannot read is a
   change to a profile, or a new profile — never an `if` in the engine.
3. **Examples use the fictional publications** listed in
   [`docs/sample-library.md`](docs/sample-library.md), and never a real publication, a real
   city, region or country. This holds everywhere: code, comments, fixtures, documentation,
   configuration samples and screenshots. Each fictional name exists for a specific parsing
   edge case — reuse them rather than inventing more, and if you must invent, invent the
   place too.
4. **Paperstand is agnostic about where the files came from.** It reads a folder. Never name
   a downloader, a service or a provider anywhere; describe file name shapes as "common
   real-world naming".
5. **Every user-visible string lives in `frontend/messages/{en,it}.json`**, in both
   languages, and is used through `m.<key>()`. `npm run i18n:check` fails on a key that is
   missing from one catalogue, on mismatched placeholders, and on text hardcoded in a
   component. Everything else in the repository — code, comments, docstrings, documentation,
   commit messages — is English.
6. **`openapi.json` and the generated client are committed.** Any change to a request or
   response model must be followed by `make gen-api`, or CI fails on the diff.
7. **Add a fixture row for every naming shape.** `backend/tests/fixtures/filenames.py` pins
   down what each shape must parse to, down to the date precision and the deciding rule.

## Commit messages

[Conventional Commits 1.0](https://www.conventionalcommits.org/): `type(scope): summary`,
imperative, lower case, no full stop. No trailers of any kind.

```
feat(opds): answer HEAD on every feed
build(docker): pin uv and trim the runtime image
docs(readme): add cover banner and badge row
```

Types: `feat`, `fix`, `docs`, `build`, `ci`, `chore`, `refactor`, `test`, `perf`, `style`.
Scopes are parts of the system: `api`, `scanner`, `parser`, `organizer`, `render`,
`reader`, `storefront`, `maintenance`, `opds`, `docker`; a `docs`, `ci` or `chore` commit
may name the thing it touches instead (`readme`, `dependabot`, `release`). Add a body when
the *why* is not obvious, and a `BREAKING CHANGE:` footer when an upgrade needs a hand.
