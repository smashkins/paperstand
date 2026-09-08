"""``parse-report`` and ``parse-explain``.

Both work with or without a configuration file: with none, the libraries are
auto-discovered from the top-level folders, exactly as a scan would do it.
"""

from __future__ import annotations

import datetime as dt
import sys
from collections.abc import Sequence
from pathlib import Path, PurePosixPath
from typing import TextIO

from paperstand.config import PaperstandConfig, get_settings, load_config
from paperstand.parsing import ParsedIssue, explain_path, parse_path
from paperstand.scanner.walker import iter_library_files, top_level_folders

__all__ = [
    "iter_library_files",
    "parse_explain",
    "parse_report",
    "top_level_folders",
]

REPORT_COLUMNS: tuple[str, ...] = ("rel_path", "title", "date", "source", "number", "rule")


def default_config_path() -> Path | None:
    """The configuration file the server would use, when it exists."""
    path = get_settings().config_path
    return path if path.is_file() else None


def missing_explicit_config(command: str, config_path: Path | None) -> bool:
    """Report an explicit ``--config`` that is not there.

    A *default* configuration file that does not exist simply means "discover
    the libraries"; one the user named on the command line does not, and
    silently reporting on an auto-discovered configuration instead would be a
    lie about which rules produced the output.
    """
    if config_path is not None and not config_path.is_file():
        print(
            f"{command}: no configuration file at {config_path}",
            file=sys.stderr,
        )
        return True
    return False


def library_root_setting() -> Path | None:
    """The configured library root, when it exists."""
    library = get_settings().library
    return library if library.is_dir() else None


def load_cli_config(config_path: Path | None, root: Path) -> PaperstandConfig:
    """Load a configuration file, or auto-discover the libraries under ``root``."""
    return load_config(config_path, folder_names=top_level_folders(root))


def parse_file(rel_path: str, root: Path, config: PaperstandConfig) -> ParsedIssue | None:
    """Parse one file of a library root; ``None`` when it belongs to no library."""
    library = config.library_for(rel_path)
    if library is None:
        return None
    mtime = _mtime(root / rel_path)
    return parse_path(rel_path, library, config.profile_for(library), mtime)


def _mtime(path: Path) -> dt.datetime:
    try:
        return dt.datetime.fromtimestamp(path.stat().st_mtime)
    except OSError:
        return dt.datetime.now()


def report_rows(root: Path, config: PaperstandConfig) -> list[tuple[str, ...]]:
    """The body of the ``parse-report`` table."""
    rows: list[tuple[str, ...]] = []
    for rel_path in iter_library_files(root, config):
        issue = parse_file(rel_path, root, config)
        if issue is None:
            continue
        rows.append(
            (
                rel_path,
                issue.title_name,
                issue.issue_date.isoformat() if issue.issue_date else "-",
                f"{issue.date_source}/{issue.date_precision}",
                str(issue.issue_number) if issue.issue_number is not None else "-",
                issue.matched_rule,
            )
        )
    return rows


def format_table(header: Sequence[str], rows: Sequence[Sequence[str]]) -> str:
    """Render an aligned plain text table."""
    columns = list(zip(header, *rows, strict=False)) if rows else [(name,) for name in header]
    widths = [max(len(str(cell)) for cell in column) for column in columns]
    lines = [
        "  ".join(str(cell).ljust(width) for cell, width in zip(header, widths, strict=False)),
        "  ".join("-" * width for width in widths),
    ]
    lines.extend(
        "  ".join(str(cell).ljust(width) for cell, width in zip(row, widths, strict=False)).rstrip()
        for row in rows
    )
    return "\n".join(lines)


def parse_report(
    directory: Path,
    config_path: Path | None = None,
    out: TextIO | None = None,
) -> int:
    """Print what every PDF under ``directory`` would be catalogued as."""
    stream = out or sys.stdout
    root = directory.resolve()
    if not root.is_dir():
        print(f"parse-report: {directory} is not a directory", file=sys.stderr)
        return 2
    if missing_explicit_config("parse-report", config_path):
        return 2
    resolved_config = config_path if config_path is not None else default_config_path()
    config = load_cli_config(resolved_config, root)
    rows = report_rows(root, config)
    print(f"library root: {root}", file=stream)
    print(f"configuration: {resolved_config or 'auto-discovered'}", file=stream)
    print(
        "libraries: "
        + (", ".join(f"{lib.name} ({lib.kind})" for lib in config.libraries) or "none"),
        file=stream,
    )
    print(file=stream)
    print(format_table(REPORT_COLUMNS, rows), file=stream)
    print(file=stream)
    unsorted = sum(1 for row in rows if row[1] == "Unsorted")
    print(f"{len(rows)} file(s), {unsorted} unsorted", file=stream)
    return 0


def resolve_library_root(
    path: Path,
    config: PaperstandConfig | None,
    override: Path | None = None,
    settings_library: Path | None = None,
) -> Path:
    """Work out which directory is the library root for a single file.

    In order: an explicit ``--library-root``; a configured library path found
    among the file's ancestors; the configured library root, when the file lives
    inside it; the first folder below the working directory; and finally the
    file's own folder.
    """
    if override is not None:
        return override.resolve()
    resolved = path.resolve()
    parts = resolved.parts
    if config is not None:
        for library in config.libraries:
            prefix = tuple(PurePosixPath(library.path.strip("/")).parts)
            if not prefix:
                continue
            for index in range(len(parts) - len(prefix)):
                if parts[index : index + len(prefix)] == prefix:
                    return Path(*parts[:index])
    if settings_library is not None and _is_relative_to(resolved, settings_library):
        return settings_library.resolve()
    cwd = Path.cwd().resolve()
    if _is_relative_to(resolved, cwd):
        relative = resolved.relative_to(cwd)
        if len(relative.parts) > 2:
            return cwd / relative.parts[0]
    return resolved.parent


def _is_relative_to(path: Path, other: Path) -> bool:
    try:
        path.relative_to(other.resolve())
    except ValueError:
        return False
    return True


def parse_explain(
    file: Path,
    config_path: Path | None = None,
    library_root: Path | None = None,
    out: TextIO | None = None,
) -> int:
    """Print every step the parser takes on a single file."""
    stream = out or sys.stdout
    target = file.resolve()
    if missing_explicit_config("parse-explain", config_path):
        return 2
    resolved_config = config_path if config_path is not None else default_config_path()
    explicit = (
        load_config(resolved_config, folder_names=[])
        if resolved_config is not None and resolved_config.is_file()
        else None
    )
    root = resolve_library_root(target, explicit, library_root, library_root_setting())
    config = explicit if explicit is not None else load_cli_config(None, root)
    try:
        rel_path = target.relative_to(root).as_posix()
    except ValueError:
        print(f"parse-explain: {file} is not inside the library root {root}", file=sys.stderr)
        return 2
    library = config.library_for(rel_path)
    if library is None:
        known = ", ".join(lib.path for lib in config.libraries) or "none"
        print(
            f"parse-explain: {rel_path} belongs to no library (known: {known}). "
            "Point --library-root at the root of the collection.",
            file=sys.stderr,
        )
        return 2
    profile = config.profile_for(library)
    mtime = _mtime(target)
    issue, trace = explain_path(rel_path, library, profile, mtime)

    print(f"file:          {target}", file=stream)
    print(f"configuration: {resolved_config or 'auto-discovered'}", file=stream)
    print(f"library root:  {root}", file=stream)
    print(f"rel path:      {rel_path}", file=stream)
    print(
        f"library:       {library.name} (kind {library.kind}, parser {library.parser})", file=stream
    )
    print(f"titles:        {len(library.titles)} configured", file=stream)
    print(f"mtime:         {mtime.isoformat(timespec='seconds')}", file=stream)
    if not target.exists():
        print("note:          this file does not exist; the current time stands in", file=stream)
    print(file=stream)
    print("steps:", file=stream)
    width = max((len(name) for name, _ in trace), default=0)
    for name, detail in trace[:-1]:
        print(f"  {name.ljust(width)}  {detail}", file=stream)
    print(file=stream)
    print("result:", file=stream)
    for field, value in _result_fields(issue):
        print(f"  {field.ljust(15)}{value}", file=stream)
    return 0


def _result_fields(issue: ParsedIssue) -> list[tuple[str, str]]:
    return [
        ("title", issue.title_name),
        ("title_source", issue.title_source),
        ("derived_title", issue.derived_title),
        ("issue_date", issue.issue_date.isoformat() if issue.issue_date else "-"),
        ("date_precision", issue.date_precision),
        ("date_source", issue.date_source),
        ("issue_number", str(issue.issue_number) if issue.issue_number is not None else "-"),
        ("dedup_suffix", "yes" if issue.has_dedup_suffix else "no"),
        ("label", issue.label),
        ("matched_rule", issue.matched_rule),
    ]
