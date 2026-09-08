import { describe, expect, it } from 'vitest';
import { PAGE_WIDTHS, pageImageUrl, snapWidth } from './pages';

const TEMPLATE = '/api/issues/abc/pages/{n}.webp?w={w}&v=17';

describe('snapWidth', () => {
	it('rounds up to a width the server renders', () => {
		expect(snapWidth(1)).toBe(200);
		expect(snapWidth(200)).toBe(200);
		expect(snapWidth(201)).toBe(400);
		expect(snapWidth(1440)).toBe(1600);
	});

	it('stops at the largest one rather than asking for more', () => {
		expect(snapWidth(9000)).toBe(PAGE_WIDTHS[PAGE_WIDTHS.length - 1]);
	});

	it('answers the smallest width for a stage that has no size yet', () => {
		expect(snapWidth(0)).toBe(200);
		expect(snapWidth(Number.NaN)).toBe(200);
	});
});

describe('pageImageUrl', () => {
	it('fills in the page and the snapped width, and keeps the version', () => {
		expect(pageImageUrl(TEMPLATE, 12, 1440)).toBe('/api/issues/abc/pages/12.webp?w=1600&v=17');
	});

	it('leaves a template with no placeholders alone but for the width', () => {
		expect(pageImageUrl('/x.webp', 3, 800)).toBe('/x.webp');
	});
});
