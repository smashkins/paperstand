/**
 * Where one page sits, and which pages sit next to it.
 *
 * A periodical is not a book: page 1 is the front page and it is never half of
 * anything. So the double-page layout is *cover alone*, then (2, 3), (4, 5) and
 * so on — which is also how the paper itself opens on a table.
 *
 * Everything here is a pure function of a page number, a page count and a
 * layout, so the reader can ask "what is next" without keeping a list, and the
 * rules can be tested away from a browser.
 */

import type { SpreadMode } from '$lib/stores/prefs.svelte';

/** How many pages are actually on screen. */
export type Layout = 'single' | 'double';

/** The narrowest viewport that gets two pages side by side on its own. */
export const SPREAD_MIN_WIDTH = 1024;

/** A viewport, as much of it as the rules care about. */
export interface Viewport {
	width: number;
	height: number;
}

/**
 * What `auto` means for this viewport.
 *
 * Two broadsheet pages need width *and* a landscape shape: a tall 1024 px
 * window would give each page a column too narrow to read.
 */
export function autoLayout(viewport: Viewport): Layout {
	const landscape = viewport.width > viewport.height;
	return landscape && viewport.width >= SPREAD_MIN_WIDTH ? 'double' : 'single';
}

/** The layout in force, given the preference and the window it is applied to. */
export function resolveLayout(mode: SpreadMode, viewport: Viewport): Layout {
	if (mode === 'single') return 'single';
	if (mode === 'double') return 'double';
	return autoLayout(viewport);
}

/** A page number that exists in a document of `pageCount` pages. */
export function clampPage(page: number, pageCount: number): number {
	if (!Number.isFinite(page) || pageCount < 1) return 1;
	return Math.min(Math.max(Math.trunc(page), 1), pageCount);
}

/**
 * The first page of the spread that holds `page`.
 *
 * In `double` the cover keeps itself; after it, an odd page belongs to the
 * spread that started on the even page before it.
 */
export function spreadStart(page: number, pageCount: number, layout: Layout): number {
	const current = clampPage(page, pageCount);
	if (layout === 'single' || current === 1) return current;
	return current % 2 === 0 ? current : current - 1;
}

/** The pages on screen when the spread starting at `page` is shown. */
export function spreadPages(page: number, pageCount: number, layout: Layout): number[] {
	if (pageCount < 1) return [];
	const start = spreadStart(page, pageCount, layout);
	if (layout === 'single' || start === 1) return [start];
	return start + 1 <= pageCount ? [start, start + 1] : [start];
}

/** How many spreads the document is made of. */
export function spreadCount(pageCount: number, layout: Layout): number {
	if (pageCount < 1) return 0;
	if (layout === 'single') return pageCount;
	return 1 + Math.ceil((pageCount - 1) / 2);
}

/** Which spread, counting from zero, holds `page`. */
export function spreadIndex(page: number, pageCount: number, layout: Layout): number {
	const start = spreadStart(page, pageCount, layout);
	if (layout === 'single') return start - 1;
	return start === 1 ? 0 : start / 2;
}

/** Every spread of the document, as lists of page numbers. */
export function spreads(pageCount: number, layout: Layout): number[][] {
	const out: number[][] = [];
	for (let page = 1; page <= pageCount;) {
		const pages = spreadPages(page, pageCount, layout);
		out.push(pages);
		page += pages.length;
	}
	return out;
}

/** The spread after this one, or this one when there is nothing after it. */
export function nextSpread(page: number, pageCount: number, layout: Layout): number {
	const start = spreadStart(page, pageCount, layout);
	const step = spreadPages(start, pageCount, layout).length;
	const candidate = start + step;
	return candidate <= pageCount ? candidate : start;
}

/** The spread before this one, or this one when it is already the first. */
export function prevSpread(page: number, pageCount: number, layout: Layout): number {
	const start = spreadStart(page, pageCount, layout);
	if (start <= 1) return 1;
	if (layout === 'single') return start - 1;
	return start === 2 ? 1 : start - 2;
}

/** The first page of the last spread. */
export function lastSpread(pageCount: number, layout: Layout): number {
	if (pageCount < 1) return 1;
	return spreadStart(pageCount, pageCount, layout);
}

/**
 * The pages worth having ready around `page`: this spread and its neighbours.
 *
 * Reading is a walk in one direction with the occasional step back, so one
 * spread either side is the whole of it — enough that a turn is instant,
 * little enough that a 200-page magazine is never rasterised in bulk.
 */
export function prefetchPages(page: number, pageCount: number, layout: Layout): number[] {
	if (pageCount < 1) return [];
	const start = spreadStart(page, pageCount, layout);
	const wanted = [
		...spreadPages(prevSpread(start, pageCount, layout), pageCount, layout),
		...spreadPages(start, pageCount, layout),
		...spreadPages(nextSpread(start, pageCount, layout), pageCount, layout)
	];
	return [...new Set(wanted)];
}
