"""Command line entry point: ``paperstand <command>``."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

from paperstand import __version__
from paperstand.config import Settings, get_settings
from paperstand.logging import configure_logging


def build_parser() -> argparse.ArgumentParser:
    """Build the top level argument parser."""
    parser = argparse.ArgumentParser(prog="paperstand", description="Paperstand server")
    parser.add_argument("--version", action="version", version=f"paperstand {__version__}")
    subparsers = parser.add_subparsers(dest="command")

    serve = subparsers.add_parser("serve", help="run the HTTP server")
    serve.add_argument("--host", default=None, help="bind address (default: PAPERSTAND_HOST)")
    serve.add_argument("--port", type=int, default=None, help="TCP port (default: PORT)")

    report = subparsers.add_parser(
        "parse-report",
        help="show what every PDF under a directory would be catalogued as",
    )
    report.add_argument("directory", type=Path, help="library root to walk")
    report.add_argument(
        "--config",
        type=Path,
        default=None,
        help="paperstand.yml to use (default: the configured one, else auto-discovery)",
    )

    organize = subparsers.add_parser(
        "organize-plan",
        help="show where every PDF under a directory would live in the canonical layout",
    )
    organize.add_argument("directory", type=Path, help="library root to walk")
    organize.add_argument(
        "--config",
        type=Path,
        default=None,
        help="paperstand.yml to use (default: the configured one, else auto-discovery)",
    )

    explain = subparsers.add_parser(
        "parse-explain",
        help="show every step the parser takes on a single file",
    )
    explain.add_argument("file", type=Path, help="PDF to explain")
    explain.add_argument("--config", type=Path, default=None, help="paperstand.yml to use")
    explain.add_argument(
        "--library-root",
        type=Path,
        default=None,
        help="root the file's path is relative to (default: worked out from the path)",
    )

    scan_command = subparsers.add_parser(
        "scan",
        help="scan the library once, in the foreground, and print the counters",
    )
    scan_command.add_argument(
        "--library",
        type=Path,
        default=None,
        help="library root to walk (default: PAPERSTAND_LIBRARY)",
    )
    scan_command.add_argument(
        "--data",
        type=Path,
        default=None,
        help="directory holding the database and the caches (default: PAPERSTAND_DATA)",
    )
    scan_command.add_argument(
        "--config",
        type=Path,
        default=None,
        help="paperstand.yml to use (default: <data>/paperstand.yml)",
    )
    return parser


def serve(host: str | None = None, port: int | None = None) -> None:
    """Run the ASGI server."""
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "paperstand.main:app",
        host=host or settings.host,
        port=port or settings.port,
        log_level=settings.log_level.lower(),
        # The application reads `X-Forwarded-*` itself, from the clients
        # `PAPERSTAND_TRUSTED_PROXIES` names — Uvicorn's own middleware ignores
        # `X-Forwarded-Host`, which is half of an absolute URL, and sits outside
        # the application, where no test ever goes through it. Leaving both
        # switched on would mean two implementations disagreeing about which
        # hop to believe. See `paperstand.proxy`.
        proxy_headers=False,
    )


def scan(
    library: Path | None = None,
    data: Path | None = None,
    config: Path | None = None,
) -> int:
    """Run one complete scan in the foreground and report what it did."""
    from paperstand.scanner.scanner import scan_once

    overrides = {
        key: value
        for key, value in (("library", library), ("data", data), ("config", config))
        if value is not None
    }
    settings = get_settings()
    if overrides:
        settings = Settings(**{**settings.model_dump(), **overrides})
    result = scan_once(settings)
    print(f"scan {result.scan_id} {result.status} in {result.duration:.2f}s")
    print(f"  library      {settings.library}")
    print(f"  database     {settings.db_path}")
    for field in ("files_seen", "added", "updated", "removed", "covers_done", "errors", "hashed"):
        print(f"  {field:<12} {getattr(result, field)}")
    if result.message:
        print(f"  message      {result.message}")
    return 0 if result.status == "ok" else 1


def main(argv: Sequence[str] | None = None) -> int:
    """Parse the command line and dispatch."""
    parser = build_parser()
    args = parser.parse_args(argv)
    configure_logging()
    if args.command == "serve":
        serve(host=args.host, port=args.port)
        return 0
    if args.command == "parse-report":
        from paperstand.cli.parse import parse_report

        return parse_report(args.directory, args.config)
    if args.command == "organize-plan":
        from paperstand.cli.organize import organize_plan

        return organize_plan(args.directory, args.config)
    if args.command == "parse-explain":
        from paperstand.cli.parse import parse_explain

        return parse_explain(args.file, args.config, args.library_root)
    if args.command == "scan":
        return scan(args.library, args.data, args.config)
    parser.print_help()
    return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
