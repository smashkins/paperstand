import { describe, expect, it } from 'vitest';
import { daysInMonth, latestMonth, monthGrid, shiftMonth, weekdayNames } from './calendar';

describe('daysInMonth', () => {
	it('knows the short months', () => {
		expect(daysInMonth(2026, 2)).toBe(28);
		expect(daysInMonth(2026, 4)).toBe(30);
		expect(daysInMonth(2026, 12)).toBe(31);
	});

	it('knows the leap year rule, century included', () => {
		expect(daysInMonth(2024, 2)).toBe(29);
		expect(daysInMonth(2000, 2)).toBe(29);
		expect(daysInMonth(1900, 2)).toBe(28);
	});
});

describe('monthGrid', () => {
	it('is always whole weeks', () => {
		for (let month = 1; month <= 12; month += 1) {
			expect(monthGrid(2026, month).length % 7).toBe(0);
		}
	});

	it('starts on a Monday by default', () => {
		// 1 March 2026 is a Sunday, so a Monday-first grid leads with six days
		// of February.
		const cells = monthGrid(2026, 3);
		expect(cells[0].iso).toBe('2026-02-23');
		expect(cells[0].inMonth).toBe(false);
		expect(cells[6].iso).toBe('2026-03-01');
		expect(cells[6].inMonth).toBe(true);
	});

	it('starts on a Sunday when asked to', () => {
		const cells = monthGrid(2026, 3, 0);
		expect(cells[0].iso).toBe('2026-03-01');
	});

	it('holds every day of the month exactly once', () => {
		const inMonth = monthGrid(2026, 3).filter((cell) => cell.inMonth);
		expect(inMonth).toHaveLength(31);
		expect(new Set(inMonth.map((cell) => cell.iso)).size).toBe(31);
	});

	it('pads January from December and December into January', () => {
		expect(monthGrid(2026, 1)[0].iso).toBe('2025-12-29');
		const december = monthGrid(2026, 12);
		expect(december[december.length - 1].iso).toBe('2027-01-03');
	});

	it('handles a leap February', () => {
		const inMonth = monthGrid(2024, 2).filter((cell) => cell.inMonth);
		expect(inMonth).toHaveLength(29);
		expect(inMonth[28].iso).toBe('2024-02-29');
	});
});

describe('shiftMonth', () => {
	it('carries into the next and previous year', () => {
		expect(shiftMonth(2026, 12, 1)).toEqual({ year: 2027, month: 1 });
		expect(shiftMonth(2026, 1, -1)).toEqual({ year: 2025, month: 12 });
		expect(shiftMonth(2026, 3, 0)).toEqual({ year: 2026, month: 3 });
		expect(shiftMonth(2026, 6, -18)).toEqual({ year: 2024, month: 12 });
	});
});

describe('weekdayNames', () => {
	it('starts on the requested weekday', () => {
		expect(weekdayNames('en-GB', 1)[0]).toBe('Mon');
		expect(weekdayNames('en-GB', 0)[0]).toBe('Sun');
		expect(weekdayNames('it-IT', 1)).toHaveLength(7);
	});
});

describe('latestMonth', () => {
	it('is the month of the newest day present', () => {
		expect(latestMonth({ '2026-01-04': 'a', '2026-07-19': 'b', '2026-03-02': 'c' })).toBe(7);
	});

	it('is nothing when the year has no issues', () => {
		expect(latestMonth({})).toBeNull();
	});
});
