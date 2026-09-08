/**
 * Formatting shared by the UI.
 *
 * The backend's own `label` is English by design — it is what OPDS and the
 * command line print. Anything a person reads in the browser is built here
 * instead, from `issue_date`, `date_precision` and `issue_number`, through
 * `Intl` with the active locale.
 */

import { m } from '$lib/paraglide/messages.js';
import type { Locale } from '$lib/paraglide/runtime';
import type { DatePrecision } from '$lib/api/client';

/** The em dash a missing value renders as. */
export const NO_VALUE = '—';

/**
 * The Paraglide locale behind an `Intl` tag.
 *
 * The two are not the same string: `Intl` wants a region to get the date order
 * and the decimal separator right (`en-GB`, `it-IT`), while Paraglide keys its
 * catalogue on the bare language. Everything in this module takes the `Intl`
 * tag and narrows it here when it needs a message.
 */
export function messageLocale(locale: string): Locale {
	return locale.toLowerCase().startsWith('it') ? 'it' : 'en';
}

/** Bytes rendered with a binary unit, e.g. `12.4 MB`. */
export function formatSize(bytes: number, locale = 'en'): string {
	if (!Number.isFinite(bytes) || bytes < 0) return NO_VALUE;
	const units = ['B', 'KB', 'MB', 'GB', 'TB'];
	let value = bytes;
	let unit = 0;
	while (value >= 1024 && unit < units.length - 1) {
		value /= 1024;
		unit += 1;
	}
	const digits = unit === 0 || value >= 100 ? 0 : 1;
	const formatted = new Intl.NumberFormat(locale, {
		minimumFractionDigits: digits,
		maximumFractionDigits: digits
	}).format(value);
	return `${formatted} ${units[unit]}`;
}

/** A whole number in the active locale. */
export function formatNumber(value: number, locale = 'en'): string {
	if (!Number.isFinite(value)) return NO_VALUE;
	return new Intl.NumberFormat(locale).format(value);
}

/**
 * Parse an ISO `YYYY-MM-DD` into a UTC date, or `null` if it is not one.
 *
 * `Date.UTC` happily turns 2026-02-31 into 3 March, so the parts are read back
 * and a date that did not survive the round trip is rejected rather than
 * silently rolled over.
 */
export function parseIsoDate(isoDate: string): Date | null {
	const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(isoDate);
	if (!match) return null;
	const [, year, month, day] = match;
	const date = new Date(Date.UTC(Number(year), Number(month) - 1, Number(day)));
	if (
		date.getUTCFullYear() !== Number(year) ||
		date.getUTCMonth() !== Number(month) - 1 ||
		date.getUTCDate() !== Number(day)
	) {
		return null;
	}
	return date;
}

/**
 * An ISO `YYYY-MM-DD` string rendered in the given locale.
 *
 * A string that is not a real calendar date is returned untouched.
 */
export function formatIssueDate(
	isoDate: string,
	locale = 'en',
	options: Intl.DateTimeFormatOptions = { day: 'numeric', month: 'long', year: 'numeric' }
): string {
	const date = parseIsoDate(isoDate);
	if (date === null) return isoDate;
	return new Intl.DateTimeFormat(locale, { ...options, timeZone: 'UTC' }).format(date);
}

/** `17 Mar 2026` / `17 mar 2026` — the short form a card carries. */
export function formatShortDate(isoDate: string, locale = 'en'): string {
	return formatIssueDate(isoDate, locale, { day: 'numeric', month: 'short', year: 'numeric' });
}

/**
 * A date at the precision the parser managed to reach.
 *
 * `day` gives `17 mar 2026`, `month` gives `marzo 2026`, `year` gives `2026`,
 * and `none` gives nothing at all, because an invented day would be a lie.
 */
export function formatDateAtPrecision(
	isoDate: string | null | undefined,
	precision: DatePrecision,
	locale = 'en'
): string {
	if (!isoDate || precision === 'none') return '';
	if (precision === 'year') return isoDate.slice(0, 4);
	if (precision === 'month') {
		return formatIssueDate(isoDate, locale, { month: 'long', year: 'numeric' });
	}
	return formatShortDate(isoDate, locale);
}

/**
 * The line under a cover: `N. 8 · 17 mar 2026`, either half optional.
 *
 * An issue with neither a number nor a date falls back to its file name, the
 * only thing left that identifies it.
 */
export function formatIssueLabel(
	issue: {
		issue_date?: string | null;
		date_precision: DatePrecision;
		issue_number?: string | null;
		filename?: string;
	},
	locale = 'en'
): string {
	const parts: string[] = [];
	if (issue.issue_number) {
		parts.push(m.issue_number({ number: issue.issue_number }, { locale: messageLocale(locale) }));
	}
	const date = formatDateAtPrecision(issue.issue_date, issue.date_precision, locale);
	if (date) parts.push(date);
	if (parts.length === 0) return issue.filename ?? '';
	return parts.join(' · ');
}

/**
 * The span a title covers, without inventing a day it does not have.
 *
 * The backend normalises a month- or year-precise date to the first day of that
 * period, so `first_date` and `last_date` are `2026-03-01` for an issue that
 * only ever said "March 2026". A `Title` carries the precision of its *latest*
 * issue and of nothing else, so:
 *
 * * a run that spans more than one year is shown as years, which every
 *   precision agrees on;
 * * a run inside one year is shown as the latest issue's label, at the
 *   precision that issue actually reached.
 */
export function formatTitleSpan(
	firstDate: string | null | undefined,
	lastDate: string | null | undefined,
	latestPrecision: DatePrecision,
	locale = 'en'
): string {
	if (!lastDate) return '';
	const firstYear = firstDate?.slice(0, 4);
	const lastYear = lastDate.slice(0, 4);
	if (firstYear && firstYear !== lastYear) {
		return m.title_range({ first: firstYear, last: lastYear }, { locale: messageLocale(locale) });
	}
	return formatDateAtPrecision(lastDate, latestPrecision, locale) || lastYear;
}

/** `Page 30 of 48`, with the numbers through `Intl`. */
export function formatPageOf(
	page: number,
	pageCount: number | null | undefined,
	locale = 'en'
): string {
	const tag = messageLocale(locale);
	if (!pageCount || pageCount < 1) {
		return m.page_only({ page: formatNumber(page, locale) }, { locale: tag });
	}
	return m.page_of(
		{ page: formatNumber(page, locale), total: formatNumber(pageCount, locale) },
		{ locale: tag }
	);
}

/**
 * A count with the noun that goes with it, picked by the locale's plural rules.
 *
 * The message catalogue carries a `_one` and an `_other` form of every counted
 * noun; `Intl.PluralRules` decides which one this number wants.
 */
export function pluralise(
	count: number,
	locale: string,
	one: (inputs: { count: string }, options?: { locale?: Locale }) => string,
	other: (inputs: { count: string }, options?: { locale?: Locale }) => string
): string {
	const tag = messageLocale(locale);
	const inputs = { count: formatNumber(count, locale) };
	const rule = new Intl.PluralRules(locale).select(count);
	return rule === 'one' ? one(inputs, { locale: tag }) : other(inputs, { locale: tag });
}

/** How far through an issue the reader is, as a percentage between 0 and 100. */
export function progressPercent(page: number, pageCount: number | null | undefined): number {
	if (!pageCount || pageCount < 1) return 0;
	return Math.min(100, Math.max(0, (page / pageCount) * 100));
}

/**
 * The default aspect ratio of a cover, width over height.
 *
 * Roughly a broadsheet page, and what an issue the scanner has not opened yet
 * is drawn at; reserving that space is what keeps the grid from jumping when
 * the images arrive.
 */
export const DEFAULT_ASPECT = 0.72;

/** The aspect ratio of a cover, width over height. */
export function coverAspect(aspect?: { page_w: number; page_h: number } | null): number {
	if (!aspect || !(aspect.page_h > 0) || !(aspect.page_w > 0)) return DEFAULT_ASPECT;
	const ratio = aspect.page_w / aspect.page_h;
	// A ratio outside this range is a page box no renderer would have produced.
	if (!Number.isFinite(ratio) || ratio < 0.2 || ratio > 5) return DEFAULT_ASPECT;
	return ratio;
}

/** A timestamp rendered as a date and a time in the active locale. */
export function formatDateTime(value: string | null | undefined, locale = 'en'): string {
	if (!value) return NO_VALUE;
	const date = new Date(value);
	if (Number.isNaN(date.getTime())) return value;
	return new Intl.DateTimeFormat(locale, { dateStyle: 'medium', timeStyle: 'short' }).format(date);
}

/**
 * A timestamp as a card wants it: the time if it happened today, the date if not.
 *
 * A shelf of things added this morning is a shelf of identical dates; the hour
 * is the only part of the stamp that tells them apart.
 */
export function formatWhen(
	value: string | null | undefined,
	locale = 'en',
	now: Date = new Date()
): string {
	if (!value) return NO_VALUE;
	const date = new Date(value);
	if (Number.isNaN(date.getTime())) return value;
	const sameDay =
		date.getFullYear() === now.getFullYear() &&
		date.getMonth() === now.getMonth() &&
		date.getDate() === now.getDate();
	return new Intl.DateTimeFormat(
		locale,
		sameDay ? { timeStyle: 'short' } : { day: 'numeric', month: 'short' }
	).format(date);
}

/**
 * Elapsed time as `m:ss`, e.g. `1:23`, `12:04`.
 *
 * Minutes are not padded and never roll over into hours — a scan running
 * that long is already the exceptional case the progress block exists for —
 * and a negative or non-finite input reads as `0:00` rather than crashing
 * the block that shows it.
 */
export function formatElapsed(seconds: number): string {
	const total = Number.isFinite(seconds) ? Math.max(0, Math.floor(seconds)) : 0;
	const minutes = Math.floor(total / 60);
	const secs = total % 60;
	return `${minutes}:${String(secs).padStart(2, '0')}`;
}

/** Capitalise the first letter, which Italian month and weekday names need. */
export function capitalise(value: string): string {
	if (!value) return value;
	return value.charAt(0).toLocaleUpperCase() + value.slice(1);
}
