import { describe, expect, it } from 'vitest';
import {
	capitalise,
	coverAspect,
	DEFAULT_ASPECT,
	formatDateAtPrecision,
	formatDateTime,
	formatWhen,
	formatIssueDate,
	formatIssueLabel,
	formatNumber,
	formatPageOf,
	formatShortDate,
	formatSize,
	formatTitleSpan,
	messageLocale,
	parseIsoDate,
	pluralise,
	progressPercent
} from './format';
import { m } from './paraglide/messages.js';

describe('formatSize', () => {
	it('keeps plain bytes without decimals', () => {
		expect(formatSize(512)).toBe('512 B');
	});

	it('scales to binary units', () => {
		expect(formatSize(1024)).toBe('1.0 KB');
		expect(formatSize(42 * 1024 * 1024)).toBe('42.0 MB');
	});

	it('drops the decimal once the number is large enough not to need it', () => {
		expect(formatSize(120 * 1024 * 1024)).toBe('120 MB');
	});

	it('rejects nonsense input', () => {
		expect(formatSize(Number.NaN)).toBe('—');
		expect(formatSize(-1)).toBe('—');
	});
});

describe('formatIssueDate', () => {
	it('renders an ISO date in the requested locale', () => {
		expect(formatIssueDate('2026-03-17', 'en')).toBe('March 17, 2026');
		expect(formatIssueDate('2026-03-17', 'it')).toBe('17 marzo 2026');
	});

	it('returns the input unchanged when it is not an ISO date', () => {
		expect(formatIssueDate('not-a-date')).toBe('not-a-date');
	});

	it('renders a real leap day', () => {
		expect(formatIssueDate('2024-02-29', 'en')).toBe('February 29, 2024');
	});

	it('returns the input unchanged for a day that does not exist', () => {
		expect(formatIssueDate('2026-02-31')).toBe('2026-02-31');
		expect(formatIssueDate('2023-02-29')).toBe('2023-02-29');
	});
});

describe('parseIsoDate', () => {
	it('parses a real date as UTC midnight', () => {
		expect(parseIsoDate('2026-03-17')?.toISOString()).toBe('2026-03-17T00:00:00.000Z');
	});

	it('rejects a rolled-over date rather than shifting it', () => {
		expect(parseIsoDate('2026-02-31')).toBeNull();
		expect(parseIsoDate('2026-3-1')).toBeNull();
	});
});

describe('formatShortDate', () => {
	it('uses the abbreviated month of the locale', () => {
		expect(formatShortDate('2026-03-17', 'en-GB')).toBe('17 Mar 2026');
		expect(formatShortDate('2026-03-17', 'it-IT')).toBe('17 mar 2026');
	});
});

describe('formatDateAtPrecision', () => {
	it('renders a day, a month and a year each at its own precision', () => {
		expect(formatDateAtPrecision('2026-03-17', 'day', 'en-GB')).toBe('17 Mar 2026');
		expect(formatDateAtPrecision('2026-03-01', 'month', 'it-IT')).toBe('marzo 2026');
		expect(formatDateAtPrecision('2026-01-01', 'year', 'en-GB')).toBe('2026');
	});

	it('says nothing at all when the parser found no date', () => {
		expect(formatDateAtPrecision('2026-01-01', 'none', 'en-GB')).toBe('');
		expect(formatDateAtPrecision(null, 'day', 'en-GB')).toBe('');
	});
});

describe('formatIssueLabel', () => {
	const base = { filename: 'something.pdf' };

	it('puts the number before the date', () => {
		expect(
			formatIssueLabel(
				{ ...base, issue_number: '8', issue_date: '2026-03-17', date_precision: 'day' },
				'en-GB'
			)
		).toBe('No. 8 · 17 Mar 2026');
	});

	it('translates the number prefix', () => {
		expect(
			formatIssueLabel(
				{ ...base, issue_number: '8', issue_date: '2026-03-01', date_precision: 'month' },
				'it-IT'
			)
		).toBe('N. 8 · marzo 2026');
	});

	it('keeps either half on its own', () => {
		expect(formatIssueLabel({ ...base, issue_number: '8', date_precision: 'none' }, 'en-GB')).toBe(
			'No. 8'
		);
		expect(
			formatIssueLabel({ ...base, issue_date: '2026-03-17', date_precision: 'day' }, 'en-GB')
		).toBe('17 Mar 2026');
	});

	it('falls back to the file name when there is nothing else', () => {
		expect(formatIssueLabel({ ...base, date_precision: 'none' }, 'en-GB')).toBe('something.pdf');
	});
});

describe('formatTitleSpan', () => {
	it('shows years when the run crosses more than one', () => {
		expect(formatTitleSpan('2024-01-01', '2026-03-17', 'day', 'en-GB')).toBe('2024 – 2026');
	});

	it('shows the latest issue at its own precision inside one year', () => {
		expect(formatTitleSpan('2026-01-09', '2026-03-17', 'day', 'en-GB')).toBe('17 Mar 2026');
	});

	it('never invents a day the parser did not find', () => {
		// The backend normalises a month-precise issue to the first of the month,
		// so `1 Mar 2026` would be a date no file ever carried.
		expect(formatTitleSpan('2026-01-01', '2026-03-01', 'month', 'en-GB')).toBe('March 2026');
		expect(formatTitleSpan('2026-01-01', '2026-01-01', 'year', 'en-GB')).toBe('2026');
	});

	it('falls back to the year when the parser found no date at all', () => {
		expect(formatTitleSpan('2026-01-01', '2026-01-01', 'none', 'en-GB')).toBe('2026');
	});

	it('says nothing without a latest date', () => {
		expect(formatTitleSpan('2026-01-01', null, 'day', 'en-GB')).toBe('');
		expect(formatTitleSpan(null, undefined, 'day', 'en-GB')).toBe('');
	});

	it('copes with a title whose first date is unknown', () => {
		expect(formatTitleSpan(null, '2026-03-17', 'day', 'en-GB')).toBe('17 Mar 2026');
	});

	it('follows the locale', () => {
		expect(formatTitleSpan('2026-01-09', '2026-03-01', 'month', 'it-IT')).toBe('marzo 2026');
	});
});

describe('formatPageOf', () => {
	it('names both numbers when the page count is known', () => {
		expect(formatPageOf(30, 48, 'en-GB')).toBe('Page 30 of 48');
		expect(formatPageOf(30, 48, 'it-IT')).toBe('Pagina 30 di 48');
	});

	it('names only the page when it is not', () => {
		expect(formatPageOf(30, null, 'en-GB')).toBe('Page 30');
		expect(formatPageOf(30, 0, 'en-GB')).toBe('Page 30');
	});
});

describe('pluralise', () => {
	it('picks the singular for one and the plural for anything else', () => {
		expect(pluralise(1, 'en-GB', m.issue_count_one, m.issue_count_other)).toBe('1 issue');
		expect(pluralise(2, 'en-GB', m.issue_count_one, m.issue_count_other)).toBe('2 issues');
		expect(pluralise(0, 'en-GB', m.issue_count_one, m.issue_count_other)).toBe('0 issues');
	});

	it('follows the locale into Italian', () => {
		expect(pluralise(1, 'it-IT', m.issue_count_one, m.issue_count_other)).toBe('1 uscita');
		expect(pluralise(3, 'it-IT', m.issue_count_one, m.issue_count_other)).toBe('3 uscite');
	});

	it('groups a large count the way the locale does', () => {
		// Italian only starts grouping at five digits.
		expect(pluralise(12000, 'it-IT', m.issue_count_one, m.issue_count_other)).toBe('12.000 uscite');
	});
});

describe('progressPercent', () => {
	it('is a percentage of the page count', () => {
		expect(progressPercent(24, 48)).toBe(50);
	});

	it('clamps rather than overflowing the strip', () => {
		expect(progressPercent(60, 48)).toBe(100);
	});

	it('is nothing at all without a page count', () => {
		expect(progressPercent(24, null)).toBe(0);
	});
});

describe('coverAspect', () => {
	it('divides the page box', () => {
		expect(coverAspect({ page_w: 595, page_h: 842 })).toBeCloseTo(0.7066, 3);
	});

	it('falls back when the scanner has not opened the file', () => {
		expect(coverAspect(null)).toBe(DEFAULT_ASPECT);
		expect(coverAspect(undefined)).toBe(DEFAULT_ASPECT);
	});

	it('rejects a page box no renderer would have produced', () => {
		expect(coverAspect({ page_w: 0, page_h: 842 })).toBe(DEFAULT_ASPECT);
		expect(coverAspect({ page_w: 5000, page_h: 10 })).toBe(DEFAULT_ASPECT);
	});
});

describe('formatDateTime', () => {
	it('renders an ISO timestamp', () => {
		expect(formatDateTime('2026-03-17T08:15:00Z', 'en-GB')).toMatch(/2026/);
	});

	it('has an em dash for nothing and passes rubbish through', () => {
		expect(formatDateTime(null)).toBe('—');
		expect(formatDateTime('later')).toBe('later');
	});
});

describe('formatWhen', () => {
	const now = new Date('2026-09-08T20:00:00Z');

	it('is a time for something that arrived today', () => {
		expect(formatWhen('2026-09-08T08:15:00Z', 'en-GB', now)).toMatch(/^\d{2}:\d{2}$/);
	});

	it('is a date for something older', () => {
		expect(formatWhen('2026-09-01T08:15:00Z', 'en-GB', now)).toBe('1 Sept');
	});

	it('has an em dash for nothing', () => {
		expect(formatWhen(null, 'en-GB', now)).toBe('—');
	});
});

describe('messageLocale', () => {
	it('narrows an Intl tag to a catalogue locale', () => {
		expect(messageLocale('it-IT')).toBe('it');
		expect(messageLocale('en-GB')).toBe('en');
		expect(messageLocale('de-DE')).toBe('en');
	});
});

describe('formatNumber and capitalise', () => {
	it('groups by locale', () => {
		expect(formatNumber(1234, 'en-GB')).toBe('1,234');
		expect(formatNumber(12345, 'it-IT')).toBe('12.345');
	});

	it('capitalises only the first letter', () => {
		expect(capitalise('martedì 8 settembre')).toBe('Martedì 8 settembre');
		expect(capitalise('')).toBe('');
	});
});
