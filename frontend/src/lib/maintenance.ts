/**
 * Shared pieces of the Maintenance page: the overview's cap, and the small
 * pure helpers the overview and its four detail routes read the same way.
 */

import type { NumberGap, TitleGaps } from '$lib/api/client';

/** Rows per section on the overview, before it hands off to "See all". */
export const OVERVIEW_LIMIT = 5;

/**
 * `titles` ranked the way the overview and the detail route both read them:
 * overdue first, then by how many gaps a title carries (dates and numbers
 * together) in descending order, then by name for a stable order when both
 * are equal. Returns a new array; `titles` itself is never sorted in place.
 */
export function rankGaps(titles: TitleGaps[]): TitleGaps[] {
	return [...titles].sort((a, b) => {
		const overdueA = a.overdue_days !== null ? 0 : 1;
		const overdueB = b.overdue_days !== null ? 0 : 1;
		if (overdueA !== overdueB) return overdueA - overdueB;

		const countA = a.date_gap_count + a.number_gap_count;
		const countB = b.date_gap_count + b.number_gap_count;
		if (countA !== countB) return countB - countA;

		return a.title_name.localeCompare(b.title_name);
	});
}

/** `n1652`, `n1660–1663`, or `v2024 n02` when the title carries a volume. */
export function numberChip(gap: NumberGap): string {
	const range = gap.first === gap.last ? `n${gap.first}` : `n${gap.first}–${gap.last}`;
	return gap.volume !== null ? `v${gap.volume} ${range}` : range;
}

/** How many issue numbers a set of number gaps actually spans. */
export function missingNumbers(gaps: NumberGap[]): number {
	return gaps.reduce((total, gap) => total + (gap.last - gap.first + 1), 0);
}

/** Days until a missing row is forgotten; `0` or less reads as "the next scan". */
export function daysUntilForgotten(missingSince: string, graceDays: number): number {
	const since = new Date(missingSince).getTime();
	if (Number.isNaN(since)) return 0;
	return Math.ceil((since + graceDays * 86_400_000 - Date.now()) / 86_400_000);
}
