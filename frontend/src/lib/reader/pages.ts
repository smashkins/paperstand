/**
 * One page on screen: how big it is, and where its pixels come from.
 *
 * The geometry — fit a page of this shape into a box of that shape, then decide
 * how many real pixels to rasterise — is the same arithmetic in three places
 * (the page component, the prefetcher, the placeholder), so it lives here once,
 * as pure functions.
 *
 * The pixels themselves come either from pdf.js or from the server's own
 * `pages/{n}.webp?w=…&v=…`. Those images do two jobs: the low-resolution
 * placeholder that fills the frame while pdf.js rasterises the real page, and
 * the "rendered by the server" mode for a tablet that cannot afford a PDF
 * worker at all. Both go through the template the API hands out, which already
 * carries the cache-busting `v`.
 */

/** A width and a height, in whatever unit the caller is working in. */
export interface Box {
	width: number;
	height: number;
}

/** No canvas is allowed an edge longer than this, whatever the zoom asks for. */
export const MAX_CANVAS_EDGE = 4096;

/** How much a page has to shrink — or may grow — to fit inside a box. */
export function fitScale(natural: Box, box: Box): number {
	if (!(natural.width > 0) || !(natural.height > 0)) return 0;
	if (!(box.width > 0) || !(box.height > 0)) return 0;
	return Math.min(box.width / natural.width, box.height / natural.height);
}

/** The page at fit size: as large as the box allows, in its own shape. */
export function fittedSize(natural: Box, box: Box): Box {
	const scale = fitScale(natural, box);
	return { width: natural.width * scale, height: natural.height * scale };
}

/**
 * The scale to rasterise at: the fit, times the zoom, times the screen.
 *
 * Snapped to a twentieth, so a window dragged by one pixel does not throw away
 * a bitmap the cache had just paid for, and capped so that a deep zoom on a
 * broadsheet cannot ask for a canvas no browser will allocate.
 */
export function renderScale(natural: Box, box: Box, zoom: number, ratio: number): number {
	const fit = fitScale(natural, box);
	if (fit <= 0) return 0;
	const cap = MAX_CANVAS_EDGE / Math.max(natural.width, natural.height);
	return Math.round(Math.min(fit * zoom * ratio, cap) * 20) / 20;
}

/**
 * The widths the backend renders at, from `render/pages.py`.
 *
 * A request is snapped up to the next one of these server-side, so asking for
 * exactly one of them is what keeps a browser cache, a proxy cache and the
 * server's own cache all naming the same file.
 */
export const PAGE_WIDTHS: readonly number[] = [200, 400, 800, 1200, 1600, 2000];

/** The width of the thumbnails in the strip; the smallest the server renders. */
export const THUMB_WIDTH = PAGE_WIDTHS[0];

/** The smallest rendered width that is at least `width`. */
export function snapWidth(width: number): number {
	if (!Number.isFinite(width) || width <= 0) return PAGE_WIDTHS[0];
	for (const candidate of PAGE_WIDTHS) {
		if (candidate >= width) return candidate;
	}
	return PAGE_WIDTHS[PAGE_WIDTHS.length - 1];
}

/**
 * Fill in `pages_url_template` for one page at one width.
 *
 * The template is `…/pages/{n}.webp?w={w}&v=<mtime>`; only the two braces are
 * ours to replace, so the version stays exactly as the API wrote it.
 */
export function pageImageUrl(template: string, page: number, width: number): string {
	return template.replace('{n}', String(page)).replace('{w}', String(snapWidth(width)));
}
