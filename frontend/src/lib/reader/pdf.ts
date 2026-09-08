/**
 * The reader's only door onto pdf.js.
 *
 * A 40 MB broadsheet is not downloaded to be read. pdf.js asks for the first
 * few kilobytes, finds the cross-reference table at the end, and then fetches
 * the objects the page in front of the reader actually needs — which is exactly
 * what `/api/issues/{id}/file` is built to answer, one 206 at a time. The three
 * options that make that happen are `rangeChunkSize`, `disableAutoFetch` and
 * `disableStream`, and they are set here so nothing else has to know about
 * them.
 *
 * The wrapper also carries the two pieces of bookkeeping that a canvas reader
 * cannot do without: a render task is cancelled when the thing it was drawing
 * for has moved on, and a handful of rasterised pages are kept so that turning
 * back one spread does not rasterise anything at all.
 */

import type { Box } from './pages';
import {
	GlobalWorkerOptions,
	getDocument,
	type PDFDocumentLoadingTask,
	type PDFDocumentProxy,
	type PDFPageProxy,
	type RenderTask
} from 'pdfjs-dist/legacy/build/pdf.mjs';

// The `legacy` build, not the default one. pdf.js states a minimum browser
// for this build only; the default build leans on whatever the newest engines
// ship — `Uint8Array.prototype.toHex` to fingerprint every document,
// `Math.sumPrecise` to rebuild a font — and is meant for the latest browser
// versions alone. A reader opened from a tablet a few updates behind is
// exactly what the legacy build is for, and it costs about a hundred
// kilobytes over the whole bundle.
//
// The worker is a real asset of this bundle, resolved and fingerprinted by
// Vite, so it is served from our own origin like everything else: Paperstand
// never reaches out to a CDN.
GlobalWorkerOptions.workerSrc = new URL(
	'pdfjs-dist/legacy/build/pdf.worker.min.mjs',
	import.meta.url
).href;

/** 256 KB: the chunk pdf.js asks the server for. */
export const RANGE_CHUNK_SIZE = 262144;

/** How many rasterised pages are kept. Roughly two spreads either side. */
export const BITMAP_CACHE_SIZE = 6;

/** The largest device pixel ratio worth rendering at. */
export const MAX_PIXEL_RATIO = 3;

/** Anything `drawImage` accepts and that this module knows how to free. */
type Bitmap = ImageBitmap | HTMLCanvasElement;

/** A page rasterised once, ready to be blitted onto any canvas. */
interface Rasterised {
	image: Bitmap;
	width: number;
	height: number;
}

/** Rounded so that two scales a hair apart share one cache entry. */
function cacheKey(page: number, scale: number): string {
	return `${page}@${scale.toFixed(3)}`;
}

/** A canvas to rasterise into: off-screen where the browser has one. */
function scratchCanvas(width: number, height: number): OffscreenCanvas | HTMLCanvasElement {
	if (typeof OffscreenCanvas === 'function') return new OffscreenCanvas(width, height);
	const canvas = document.createElement('canvas');
	canvas.width = width;
	canvas.height = height;
	return canvas;
}

/** True for the rejection pdf.js raises when a render task was cancelled. */
export function isCancellation(error: unknown): boolean {
	return error instanceof Error && error.name === 'RenderingCancelledException';
}

/**
 * One open document.
 *
 * Created by {@link openDocument} and destroyed by {@link PdfDocument.destroy},
 * which is not optional: the document owns a worker, and a reader that opened
 * ten issues without closing them would be holding ten of them.
 */
export class PdfDocument {
	readonly #document: PDFDocumentProxy;
	readonly #loading: PDFDocumentLoadingTask;
	readonly #pages = new Map<number, Promise<PDFPageProxy>>();
	/** Insertion-ordered, which is what makes a `Map` an LRU. */
	readonly #bitmaps = new Map<string, Rasterised>();
	/** In-flight rasterisations, so the same page is never started twice. */
	readonly #inFlight = new Map<string, { task: RenderTask; promise: Promise<Rasterised> }>();
	/** The keys this document is warming on its own account, and may abandon. */
	readonly #warming = new Set<string>();
	#destroyed = false;

	constructor(loading: PDFDocumentLoadingTask, document: PDFDocumentProxy) {
		this.#loading = loading;
		this.#document = document;
	}

	get pageCount(): number {
		return this.#document.numPages;
	}

	get destroyed(): boolean {
		return this.#destroyed;
	}

	/** One page of the document, fetched once and remembered. */
	page(number: number): Promise<PDFPageProxy> {
		const existing = this.#pages.get(number);
		if (existing) return existing;
		const promise = this.#document.getPage(number);
		this.#pages.set(number, promise);
		// A page that failed to load is not a page that will fail forever.
		promise.catch(() => this.#pages.delete(number));
		return promise;
	}

	/** The size of a page at scale 1, in CSS pixels. */
	async size(number: number): Promise<Box> {
		const page = await this.page(number);
		const viewport = page.getViewport({ scale: 1 });
		return { width: viewport.width, height: viewport.height };
	}

	/**
	 * Draw a page onto `canvas` at `scale`, rasterising it if it is not in hand.
	 *
	 * The canvas is sized to the rendered bitmap, so the caller decides how big
	 * the page is on screen with CSS and this decides how many pixels are in it.
	 * Answers `false` when the render was cancelled or the document was closed
	 * underneath it, which is a normal thing to happen and not an error.
	 */
	async render(number: number, canvas: HTMLCanvasElement, scale: number): Promise<boolean> {
		const rasterised = await this.rasterise(number, scale);
		if (rasterised === null || this.#destroyed) return false;
		const context = canvas.getContext('2d');
		if (!context) return false;
		if (canvas.width !== rasterised.width || canvas.height !== rasterised.height) {
			canvas.width = rasterised.width;
			canvas.height = rasterised.height;
		}
		context.clearRect(0, 0, canvas.width, canvas.height);
		context.drawImage(rasterised.image, 0, 0);
		return true;
	}

	/** True when this page at this scale is already rasterised. */
	has(number: number, scale: number): boolean {
		return this.#bitmaps.has(cacheKey(number, scale));
	}

	/**
	 * Rasterise a page, or hand back the copy already in the cache.
	 *
	 * `null` means the work was cancelled — by {@link cancel}, or by the
	 * document being destroyed while the worker was busy.
	 */
	async rasterise(number: number, scale: number): Promise<Rasterised | null> {
		if (this.#destroyed) return null;
		const key = cacheKey(number, scale);
		const cached = this.#bitmaps.get(key);
		if (cached) {
			// Touching an entry moves it to the young end of the map.
			this.#bitmaps.delete(key);
			this.#bitmaps.set(key, cached);
			return cached;
		}
		const running = this.#inFlight.get(key);
		if (running) {
			// Joining somebody else's render must not turn their failure into a
			// blank page: only a cancellation is an answer of "nothing happened".
			try {
				return await running.promise;
			} catch (error) {
				if (isCancellation(error)) return null;
				throw error;
			}
		}

		const page = await this.page(number);
		if (this.#destroyed) return null;
		const viewport = page.getViewport({ scale });
		const width = Math.max(1, Math.round(viewport.width));
		const height = Math.max(1, Math.round(viewport.height));
		const target = scratchCanvas(width, height);
		const context = target.getContext('2d') as
			CanvasRenderingContext2D | OffscreenCanvasRenderingContext2D | null;
		if (!context) return null;

		const task = page.render({
			// An explicit `null` canvas is how pdf.js is told to render through
			// the context it was given, which is the only way to reach an
			// `OffscreenCanvas`. Its types only name the DOM 2D context, which
			// is the single cast this module needs.
			canvas: null,
			canvasContext: context as CanvasRenderingContext2D,
			viewport
		});
		const promise = task.promise.then((): Rasterised => {
			// Feature, not class: `HTMLCanvasElement` does not exist outside a
			// document, and this module is worth being able to test without one.
			const image: Bitmap =
				'transferToImageBitmap' in target
					? target.transferToImageBitmap()
					: (target as HTMLCanvasElement);
			return { image, width, height };
		});
		this.#inFlight.set(key, { task, promise });
		try {
			const rasterised = await promise;
			if (this.#destroyed) {
				close(rasterised);
				return null;
			}
			this.#remember(key, rasterised);
			return rasterised;
		} catch (error) {
			if (isCancellation(error)) return null;
			throw error;
		} finally {
			this.#inFlight.delete(key);
		}
	}

	/** Cancel a rasterisation that is still running, if there is one. */
	cancel(number: number, scale: number): void {
		const running = this.#inFlight.get(cacheKey(number, scale));
		running?.task.cancel();
	}

	/** Cancel everything in flight. Called whenever the reader jumps. */
	cancelAll(): void {
		for (const { task } of this.#inFlight.values()) task.cancel();
	}

	/**
	 * Warm exactly these pages at this scale, and nothing else.
	 *
	 * Each call *supersedes* the one before it: a page that was being warmed for
	 * a spread the reader has already left is cancelled, which is the difference
	 * between a courtesy and a pile-up. Ten thumbnail jumps in a row on a
	 * broadsheet would otherwise leave ten sets of high-resolution renders
	 * competing for the worker with the page somebody is actually looking at.
	 *
	 * Only renders this document started for itself are ever cancelled; a page
	 * the foreground is waiting on is left alone even when it drops out of the
	 * set.
	 */
	prefetch(pages: readonly number[], scale: number): void {
		if (this.#destroyed) return;
		const wanted = new Set(pages.map((number) => cacheKey(number, scale)));
		for (const key of [...this.#warming]) {
			if (wanted.has(key)) continue;
			this.#abandon(key);
		}
		for (const number of pages) {
			const key = cacheKey(number, scale);
			// Already in hand, already ours, or already somebody else's: the
			// last case matters, because cancelling it later would cancel theirs.
			if (this.#bitmaps.has(key) || this.#warming.has(key) || this.#inFlight.has(key)) continue;
			this.#warming.add(key);
			void this.#warm(number, scale, key);
		}
	}

	/** Rasterise one page for the prefetcher, giving up if it is abandoned. */
	async #warm(number: number, scale: number, key: string): Promise<void> {
		try {
			// Fetching the page is itself a round trip; the set may have moved on
			// before there is anything to cancel.
			await this.page(number);
			if (this.#destroyed || !this.#warming.has(key)) return;
			await this.rasterise(number, scale);
		} catch {
			// A page that will not rasterise now makes its own noise when the
			// reader turns to it; a prefetch never reports anything.
		} finally {
			this.#warming.delete(key);
		}
	}

	/** Stop warming one key, cancelling its render if it has started. */
	#abandon(key: string): void {
		this.#warming.delete(key);
		this.#inFlight.get(key)?.task.cancel();
	}

	/** Close the worker, the document and every bitmap held. */
	async destroy(): Promise<void> {
		if (this.#destroyed) return;
		this.#destroyed = true;
		this.#warming.clear();
		this.cancelAll();
		for (const rasterised of this.#bitmaps.values()) close(rasterised);
		this.#bitmaps.clear();
		this.#pages.clear();
		this.#inFlight.clear();
		try {
			await this.#loading.destroy();
		} catch {
			// A document already torn down by a failed load has nothing to close.
		}
	}

	/** Store a bitmap, evicting the oldest once the cache is full. */
	#remember(key: string, rasterised: Rasterised): void {
		this.#bitmaps.set(key, rasterised);
		while (this.#bitmaps.size > BITMAP_CACHE_SIZE) {
			const oldest = this.#bitmaps.keys().next();
			if (oldest.done) break;
			const evicted = this.#bitmaps.get(oldest.value);
			this.#bitmaps.delete(oldest.value);
			if (evicted) close(evicted);
		}
	}
}

/** Release the memory behind a rasterised page. */
function close(rasterised: Rasterised): void {
	const image = rasterised.image as Partial<ImageBitmap> & Partial<HTMLCanvasElement>;
	if (typeof image.close === 'function') {
		image.close();
		return;
	}
	// A canvas is freed by shrinking it: the browser drops the backing store.
	if (typeof image.width === 'number') {
		image.width = 0;
		image.height = 0;
	}
}

/**
 * Open a PDF over HTTP, reading it in ranges.
 *
 * The two options are one decision. `disableStream: true` is what makes pdf.js
 * *abandon* the opening request the moment it has read the headers and seen
 * `Accept-Ranges: bytes`, and ask for ranges from then on; left streaming, it
 * would happily read the whole 50 MB body it had already been handed — on a
 * fast link that is the entire newspaper, downloaded to show page one.
 * `disableAutoFetch: true` then stops it quietly pulling the rest of the
 * document in the background once that first page is up.
 */
export async function openDocument(url: string): Promise<PdfDocument> {
	const loading = getDocument({
		url,
		rangeChunkSize: RANGE_CHUNK_SIZE,
		disableAutoFetch: true,
		disableStream: true
	});
	const document = await loading.promise;
	return new PdfDocument(loading, document);
}

/** The pixel ratio worth rendering at on this screen. */
export function pixelRatio(): number {
	const ratio = typeof window === 'undefined' ? 1 : window.devicePixelRatio || 1;
	return Math.min(ratio, MAX_PIXEL_RATIO);
}
