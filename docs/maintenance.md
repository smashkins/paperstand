# The maintenance view

`/maintenance` is one page for everything routine scanning and the organizer could not
settle on their own: an issue whose file has gone missing, a PDF the renderer could not open
at all, what the organizer parked under `unsorted/` and `duplicates/` in the inbox, and holes
in a title's numbering or its declared cadence. It is an admin view, not a shelf — nothing on
it reads or downloads an issue, and the page itself never writes anything. A number in the
top bar, next to the wrench icon, says how many of the first three there are right now; gaps
are not counted in it, because a gap is information about a title's history, not something
waiting to be fixed.

Settings keeps its own counters and its *Rescan now* button — nothing moved out of it — and
carries a link card to this page with the same number. The two pages share the same button:
asking for a scan from either one goes through the same request, and a scan already running
answers the same way on both.

## Missing

An issue whose file the last scan could not find, hidden everywhere else — the storefront,
the calendar, search, OPDS — for `PAPERSTAND_MISSING_GRACE_DAYS` (seven, by default) before
its row is forgotten for good. Each row shows the cover from cache, the title, the label, the
path the file used to be at, when it went missing and how many days it has left. **What to
do:** put the file back anywhere in the library — the exact path does not matter, only the
bytes — and rescan; it reappears exactly where it left off, cover, reading position and all.
See [Identity](folder-layout.md#identity) for the full rule.

## Unreadable

A file the renderer opened and could not make sense of: encrypted, truncated, not really a
PDF. Each row shows the file name, its path and the renderer's own message, which may name
the file directly. **What to do:** look at the file itself — a password-protected PDF needs
its password removed, a truncated one needs to be re-copied. There is nothing to fix from
inside Paperstand; a corrected file replaces the broken one on the next scan, the same way
any change would.

## Inbox

What `paperstand organize` last did, read from the report it writes to
`<data>/organizer/last-run.json` — see
[the organizer's own docs](organizer.md#the-run-report-and-the-scan-trigger). If the
organizer has never run against this `<data>`, the section says so; there is nothing to
mount or configure to find out.

Otherwise: when the last run happened, whether it was an apply run or a dry run, and its
counts — moved, duplicate, unsorted, skipped, failed — followed by a warning when the run was
refused because the library's root marker had gone missing. Two tables follow, one per inbox
folder the organizer parks files into:

- **`unsorted/`** — a file no configured title matched, or whose destination collided with
  another file's, each with the reason from its sidecar. **What to do:** declare the title in
  `paperstand.yml`, or give it its own `publication.yml` folder; the very next organizer run
  moves it out on its own.
- **`duplicates/`** — a file byte-identical to one already in the library. **What to do:**
  these are safe to delete; the organizer never reads `duplicates/` back.

A different kind of leftover sits in the same section, for the same reason a person looking
for "what did not make it onto a shelf" wants to see it here too: the catalogue's own
*Unsorted* bucket, one per library, for a file the parser found inside the library itself but
could not place under any configured title. It is not the organizer's `unsorted/` folder —
the file is already inside the library, not stuck in the inbox — but it is the same kind of
question, so it is answered in the same place, with a link to the bucket's own title page.

## Gaps

A title has a numbering or a date it never had a row for. Two rules, independent of each
other, and any title can trigger either or both:

- **Numbering** — every title that carries an issue number, declared cadence or not, is
  checked for a hole between two consecutive catalogued numbers, counted per volume when the
  title has one. A weekly like *Orizzonte* going `…, 1652, 1653, 1655, 1656` is missing
  `1654`; a monthly like *Confini* going `…, 3, 8` is missing `4` through `7`. A hole wider
  than twenty is read as a change of numbering — a relaunch, a new volume that restarts — not
  a gap, and is not reported. A variant or a supplement sharing its parent's number never
  creates a hole on either side of it.
- **Cadence** — only for a title whose `publication.yml` declares `frequency: daily`,
  `weekly` or `monthly`; `irregular` and an undeclared title get numbering gaps only, never
  date ones. A daily like *Corriere del Ponte* is checked day by day, a weekly by ISO week, a
  monthly by calendar month, between its earliest and its latest catalogued issue. A day
  covered only by a supplement — a Weekend edition sharing the daily's own date — still
  counts as covered: the gap is about the day having *an* issue, not a specific one. A daily
  whose real-world edition skips a weekday shows that weekday as a gap every week; a
  `days:` field to declare a regular rest day is a later refinement, not this one.

A title that is overdue — its latest catalogued issue further behind today than one period —
carries a badge saying by how many days, worked out from the same cadence rule. A title with
no gap and no overdue flag never appears on this page at all; a title whose only issues have
gone missing does not either; the *Unsorted* bucket is never analysed this way; a missing
issue is never counted as a gap, since the catalogue already has a row for it — a gap is a
date or a number the series never had a row for at all.

## The badge

The number by the wrench icon in the top bar is `missing_count + unreadable_count + parked`,
where `parked` is how many files sit under the organizer's `unsorted/` and `duplicates/`
combined — the same arithmetic `GET /api/maintenance` answers with as `attention`. It is
fetched once per page load of the application, refreshed again when the maintenance page
loads its own summary, and again by Settings once a scan it started has finished.
