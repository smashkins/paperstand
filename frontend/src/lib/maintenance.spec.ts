import { describe, expect, it } from 'vitest';
import type { TitleGaps } from './api/client';
import { daysUntilForgotten, rankGaps } from './maintenance';

function titleGaps(overrides: Partial<TitleGaps> = {}): TitleGaps {
	return {
		date_gap_count: 0,
		date_gaps: [],
		first_date: '2024-01-01',
		frequency: 'weekly',
		issue_count: 10,
		issue_key: 'date',
		kind: 'magazine',
		last_date: '2026-01-01',
		number_gap_count: 0,
		number_gaps: [],
		overdue_days: null,
		title_id: 't',
		title_name: 'Confini',
		...overrides
	};
}

describe('rankGaps', () => {
	it('puts an overdue title ahead of one with a bigger count', () => {
		const overdue = titleGaps({
			title_id: 'a',
			title_name: 'Confini',
			overdue_days: 3,
			date_gap_count: 1
		});
		const bigger = titleGaps({
			title_id: 'b',
			title_name: 'Bright Meadows',
			overdue_days: null,
			date_gap_count: 9
		});
		expect(rankGaps([bigger, overdue]).map((title) => title.title_id)).toEqual(['a', 'b']);
	});

	it('falls back to the name when two titles rank equal', () => {
		const cronaca = titleGaps({
			title_id: 'c',
			title_name: 'Cronaca 24 Pagine',
			date_gap_count: 2
		});
		const bright = titleGaps({ title_id: 'b', title_name: 'Bright Meadows', date_gap_count: 2 });
		expect(rankGaps([cronaca, bright]).map((title) => title.title_id)).toEqual(['b', 'c']);
	});

	it('ranks an empty list as empty', () => {
		expect(rankGaps([])).toEqual([]);
	});

	it('counts a title that only has number gaps', () => {
		const withNumbers = titleGaps({ title_id: 'a', title_name: 'Confini', number_gap_count: 4 });
		const withNone = titleGaps({ title_id: 'b', title_name: 'TDL', number_gap_count: 0 });
		expect(rankGaps([withNone, withNumbers]).map((title) => title.title_id)).toEqual(['a', 'b']);
	});

	it('leaves the input array untouched', () => {
		const titles = [
			titleGaps({ title_id: 'b', title_name: 'Bright Meadows' }),
			titleGaps({ title_id: 'a', title_name: 'Confini' })
		];
		const original = [...titles];
		rankGaps(titles);
		expect(titles).toEqual(original);
	});
});

describe('daysUntilForgotten', () => {
	it('reads an unparseable date as 0', () => {
		expect(daysUntilForgotten('not-a-date', 30)).toBe(0);
	});

	it('reads a date past the grace period as 0 or less', () => {
		const longAgo = new Date(Date.now() - 60 * 86_400_000).toISOString();
		expect(daysUntilForgotten(longAgo, 30)).toBeLessThanOrEqual(0);
	});
});
