"""Filling the catalogue: walking, scanning, rendering covers, scheduling.

The scan is deliberately two-phased. The **fast phase** only looks at names,
sizes and modification times, so a library of thousands of files is catalogued
in seconds and the API has something to serve. The **slow phase** opens the
PDFs — page count, page size, first page text, cover and thumbnail — on a small
worker pool, and a file it cannot read costs one row's ``cover_status``, never
the scan.

Each module is imported by its full path (``paperstand.scanner.scanner``,
``paperstand.scanner.covers``…): the package re-exports nothing, so that
monkeypatching a name affects exactly the module that uses it.
"""
