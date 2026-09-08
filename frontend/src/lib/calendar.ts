/**
 * The month grid behind `IssueCalendar`.
 *
 * Pure arithmetic on ISO strings, kept out of the component so it can be
 * tested on its own: no `Date` arithmetic in a component means no chance of a
 * daylight-saving hour turning the first of the month into the last of the one
 * before.
 */

/** One cell of the grid. */
export interface CalendarDay {
	/** `YYYY-MM-DD`. */
	iso: string;
	/** Day of the month, 1–31. */
	day: number;
	/** False for the padding days that belong to a neighbouring month. */
	inMonth: boolean;
}

/** Days in a month, with February's leap year rule. */
export function daysInMonth(year: number, month: number): number {
	return new Date(Date.UTC(year, month, 0)).getUTCDate();
}

function iso(year: number, month: number, day: number): string {
	return `${String(year).padStart(4, '0')}-${String(month).padStart(2, '0')}-${String(day).padStart(2, '0')}`;
}

/**
 * A month laid out as whole weeks.
 *
 * `weekStart` is the weekday the grid begins on, 0 for Sunday and 1 for Monday.
 * The result is always a multiple of seven cells, padded from the months on
 * either side, so the grid never has a ragged last row.
 */
export function monthGrid(year: number, month: number, weekStart = 1): CalendarDay[] {
	const total = daysInMonth(year, month);
	const firstWeekday = new Date(Date.UTC(year, month - 1, 1)).getUTCDay();
	const lead = (firstWeekday - weekStart + 7) % 7;

	const prevMonth = month === 1 ? 12 : month - 1;
	const prevYear = month === 1 ? year - 1 : year;
	const prevTotal = daysInMonth(prevYear, prevMonth);

	const cells: CalendarDay[] = [];
	for (let i = lead; i > 0; i -= 1) {
		const day = prevTotal - i + 1;
		cells.push({ iso: iso(prevYear, prevMonth, day), day, inMonth: false });
	}
	for (let day = 1; day <= total; day += 1) {
		cells.push({ iso: iso(year, month, day), day, inMonth: true });
	}
	const nextMonth = month === 12 ? 1 : month + 1;
	const nextYear = month === 12 ? year + 1 : year;
	let day = 1;
	while (cells.length % 7 !== 0) {
		cells.push({ iso: iso(nextYear, nextMonth, day), day, inMonth: false });
		day += 1;
	}
	return cells;
}

/** The weekday names of a locale, starting on `weekStart`. */
export function weekdayNames(locale: string, weekStart = 1, format: 'short' | 'narrow' = 'short') {
	const formatter = new Intl.DateTimeFormat(locale, { weekday: format, timeZone: 'UTC' });
	// 4 January 1970 was a Sunday, so the offset doubles as the weekday index.
	return Array.from({ length: 7 }, (_, index) =>
		formatter.format(new Date(Date.UTC(1970, 0, 4 + ((weekStart + index) % 7))))
	);
}

/** Step a `year-month` pair by a number of months, carrying the year. */
export function shiftMonth(year: number, month: number, delta: number) {
	const zeroBased = year * 12 + (month - 1) + delta;
	return { year: Math.floor(zeroBased / 12), month: (zeroBased % 12) + 1 };
}

/** The month of the newest day in a calendar, so the grid opens where the issues are. */
export function latestMonth(days: Record<string, string>): number | null {
	let latest: string | null = null;
	for (const day of Object.keys(days)) {
		if (latest === null || day > latest) latest = day;
	}
	return latest === null ? null : Number(latest.slice(5, 7));
}
