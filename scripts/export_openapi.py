#!/usr/bin/env python3
"""Dump the OpenAPI document the frontend's typed client is generated from.

``frontend/src/lib/api/openapi.json`` and the ``types.gen.ts`` beside it are
committed, and CI regenerates both and fails on a diff. That is the whole point:
a field renamed in ``schemas.py`` and not regenerated is a build failure, not a
runtime surprise three screens away.

The export needs neither a library nor a database. The application is built
around a throwaway data directory and its lifespan is never entered, so nothing
is scanned, no cover is rendered and nothing is written outside the temporary
directory this script cleans up after itself.

Usage::

    uv run --project backend python scripts/export_openapi.py [--check]

``--check`` writes nothing and exits non-zero when the committed file is stale,
which is what a pre-commit hook wants.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = REPO_ROOT / "frontend" / "src" / "lib" / "api" / "openapi.json"

sys.path.insert(0, str(REPO_ROOT / "backend"))


def document() -> dict[str, Any]:
    """The OpenAPI document of an application built for this one purpose."""
    from paperstand.config import Settings
    from paperstand.main import create_app

    with tempfile.TemporaryDirectory(prefix="paperstand-openapi-") as temporary:
        root = Path(temporary)
        settings = Settings(
            library=root / "library",
            data=root / "data",
            config=None,
            static=None,
            scan_on_start=False,
            scan_interval=0,
        )
        app = create_app(settings)
        try:
            return dict(app.openapi())
        finally:
            database = app.state.database
            if database is not None:
                database.close()


def serialise(spec: dict[str, Any]) -> str:
    """The document as it is committed: sorted keys, tabs, one trailing newline.

    Sorted so that two runs of two different FastAPI builds cannot differ over
    the order a dictionary happened to be filled in; tab-indented because that
    is what the frontend's Prettier configuration formats JSON with, and the
    generated files are checked like every other file there.
    """
    return json.dumps(spec, indent="\t", sort_keys=True, ensure_ascii=False) + "\n"


def main(argv: list[str] | None = None) -> int:
    """Write — or check — the committed OpenAPI document."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="where to write the document")
    parser.add_argument(
        "--check",
        action="store_true",
        help="write nothing; fail when the committed document is out of date",
    )
    args = parser.parse_args(argv)

    rendered = serialise(document())
    out: Path = args.out
    if args.check:
        current = out.read_text("utf-8") if out.is_file() else ""
        if current == rendered:
            print(f"{out} is up to date")
            return 0
        print(f"{out} is out of date; run `make gen-api`", file=sys.stderr)
        return 1

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(rendered, "utf-8")
    print(f"wrote {out} ({len(rendered)} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
