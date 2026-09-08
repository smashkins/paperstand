import { describe, expect, it } from 'vitest';
import {
	autoLayout,
	clampPage,
	lastSpread,
	nextSpread,
	prefetchPages,
	prevSpread,
	resolveLayout,
	spreadCount,
	spreadIndex,
	spreadPages,
	spreadStart,
	spreads
} from './spreads';

describe('autoLayout', () => {
	it('gives two pages to a wide landscape window', () => {
		expect(autoLayout({ width: 1440, height: 900 })).toBe('double');
	});

	it('keeps a single page when the window is not wide enough', () => {
		expect(autoLayout({ width: 1023, height: 700 })).toBe('single');
	});

	it('keeps a single page in portrait, however tall the window is', () => {
		expect(autoLayout({ width: 1024, height: 1366 })).toBe('single');
	});

	it('treats exactly 1024 in landscape as wide enough', () => {
		expect(autoLayout({ width: 1024, height: 800 })).toBe('double');
	});
});

describe('resolveLayout', () => {
	it('lets an explicit preference override the window', () => {
		expect(resolveLayout('single', { width: 1600, height: 900 })).toBe('single');
		expect(resolveLayout('double', { width: 390, height: 844 })).toBe('double');
	});

	it('falls back to the window when the preference is automatic', () => {
		expect(resolveLayout('auto', { width: 1600, height: 900 })).toBe('double');
		expect(resolveLayout('auto', { width: 390, height: 844 })).toBe('single');
	});
});

describe('clampPage', () => {
	it('keeps a page inside the document', () => {
		expect(clampPage(0, 48)).toBe(1);
		expect(clampPage(99, 48)).toBe(48);
		expect(clampPage(12, 48)).toBe(12);
	});

	it('answers 1 for a document with no pages or a page that is not a number', () => {
		expect(clampPage(3, 0)).toBe(1);
		expect(clampPage(Number.NaN, 48)).toBe(1);
	});
});

describe('spreadStart', () => {
	it('is the page itself in single-page layout', () => {
		expect(spreadStart(7, 48, 'single')).toBe(7);
	});

	it('leaves the cover on its own', () => {
		expect(spreadStart(1, 48, 'double')).toBe(1);
	});

	it('pairs an odd page with the even page before it', () => {
		expect(spreadStart(2, 48, 'double')).toBe(2);
		expect(spreadStart(3, 48, 'double')).toBe(2);
		expect(spreadStart(4, 48, 'double')).toBe(4);
		expect(spreadStart(5, 48, 'double')).toBe(4);
	});
});

describe('spreadPages', () => {
	it('shows one page at a time in single-page layout', () => {
		expect(spreadPages(4, 9, 'single')).toEqual([4]);
	});

	it('shows the cover alone, then pairs', () => {
		expect(spreadPages(1, 9, 'double')).toEqual([1]);
		expect(spreadPages(2, 9, 'double')).toEqual([2, 3]);
		expect(spreadPages(3, 9, 'double')).toEqual([2, 3]);
	});

	it('shows a lone last page when the count is even', () => {
		// 8 pages: 1 | 2-3 | 4-5 | 6-7 | 8.
		expect(spreadPages(8, 8, 'double')).toEqual([8]);
	});

	it('has nothing to show for a document with no pages', () => {
		expect(spreadPages(1, 0, 'double')).toEqual([]);
	});
});

describe('spreads', () => {
	it('covers every page exactly once, in order', () => {
		expect(spreads(9, 'double')).toEqual([[1], [2, 3], [4, 5], [6, 7], [8, 9]]);
		expect(spreads(8, 'double')).toEqual([[1], [2, 3], [4, 5], [6, 7], [8]]);
		expect(spreads(1, 'double')).toEqual([[1]]);
		expect(spreads(3, 'single')).toEqual([[1], [2], [3]]);
	});

	it('agrees with spreadCount and spreadIndex at every page', () => {
		for (const pageCount of [1, 2, 3, 8, 9, 56, 57]) {
			for (const layout of ['single', 'double'] as const) {
				const all = spreads(pageCount, layout);
				expect(all.length).toBe(spreadCount(pageCount, layout));
				expect(all.flat()).toEqual(
					Array.from({ length: pageCount }, (_unused, index) => index + 1)
				);
				for (let page = 1; page <= pageCount; page += 1) {
					expect(all[spreadIndex(page, pageCount, layout)]).toContain(page);
				}
			}
		}
	});
});

describe('nextSpread and prevSpread', () => {
	it('walk a single-page document one page at a time', () => {
		expect(nextSpread(1, 3, 'single')).toBe(2);
		expect(nextSpread(3, 3, 'single')).toBe(3);
		expect(prevSpread(2, 3, 'single')).toBe(1);
		expect(prevSpread(1, 3, 'single')).toBe(1);
	});

	it('walk a double-page document spread by spread', () => {
		expect(nextSpread(1, 9, 'double')).toBe(2);
		expect(nextSpread(2, 9, 'double')).toBe(4);
		expect(nextSpread(3, 9, 'double')).toBe(4);
		expect(nextSpread(8, 9, 'double')).toBe(8);
		expect(prevSpread(4, 9, 'double')).toBe(2);
		expect(prevSpread(2, 9, 'double')).toBe(1);
		expect(prevSpread(1, 9, 'double')).toBe(1);
	});

	it('never leave the document from either end', () => {
		for (const pageCount of [1, 2, 7, 8]) {
			for (const layout of ['single', 'double'] as const) {
				expect(prevSpread(1, pageCount, layout)).toBe(1);
				const last = lastSpread(pageCount, layout);
				expect(nextSpread(last, pageCount, layout)).toBe(last);
			}
		}
	});
});

describe('a layout that changes under the reader', () => {
	/**
	 * The reader keeps a raw position and shows the spread it falls in. Every
	 * navigation question has to be asked of the *spread*, not the raw number,
	 * or a single-page reader on page 3 of 3 who switches to two pages is shown
	 * the 2-3 spread with "next" still lit.
	 */
	it('has nothing after the spread an odd last page falls into', () => {
		const raw = 3;
		const start = spreadStart(raw, 3, 'double');
		expect(start).toBe(2);
		expect(spreadPages(start, 3, 'double')).toEqual([2, 3]);
		// The bug: `nextSpread(3, …)` answers 2, which differs from the raw 3.
		expect(nextSpread(raw, 3, 'double')).not.toBe(raw);
		// The rule: asked of the canonical start, there is nowhere further to go.
		expect(nextSpread(start, 3, 'double')).toBe(start);
	});

	it('is idempotent, so canonicalising twice changes nothing', () => {
		for (const pageCount of [1, 3, 8, 9, 57]) {
			for (const layout of ['single', 'double'] as const) {
				for (let page = 1; page <= pageCount; page += 1) {
					const start = spreadStart(page, pageCount, layout);
					expect(spreadStart(start, pageCount, layout)).toBe(start);
				}
			}
		}
	});

	it('carries an odd single page into the spread that contains it', () => {
		// Reading page 7 one page at a time, then turning the window sideways.
		expect(spreadStart(7, 20, 'single')).toBe(7);
		expect(spreadStart(7, 20, 'double')).toBe(6);
		expect(spreadPages(spreadStart(7, 20, 'double'), 20, 'double')).toEqual([6, 7]);
	});
});

describe('lastSpread', () => {
	it('starts the final spread of the document', () => {
		expect(lastSpread(9, 'double')).toBe(8);
		expect(lastSpread(8, 'double')).toBe(8);
		expect(lastSpread(9, 'single')).toBe(9);
		expect(lastSpread(0, 'double')).toBe(1);
	});
});

describe('prefetchPages', () => {
	it('holds this spread and one either side, without repeats', () => {
		expect(prefetchPages(4, 9, 'double')).toEqual([2, 3, 4, 5, 6, 7]);
		expect(prefetchPages(1, 9, 'double')).toEqual([1, 2, 3]);
		expect(prefetchPages(8, 9, 'double')).toEqual([6, 7, 8, 9]);
		expect(prefetchPages(5, 9, 'single')).toEqual([4, 5, 6]);
	});

	it('has nothing to prefetch in an empty document', () => {
		expect(prefetchPages(1, 0, 'single')).toEqual([]);
	});
});
