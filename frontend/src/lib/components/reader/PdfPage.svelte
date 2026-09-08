<script lang="ts">
	import { m } from '$lib/paraglide/messages.js';
	import {
		type Box,
		fittedSize,
		pageImageUrl,
		renderScale as scaleFor,
		snapWidth
	} from '$lib/reader/pages';
	import { isCancellation, pixelRatio, type PdfDocument } from '$lib/reader/pdf';

	/**
	 * One page of the document, drawn at the size it is shown at.
	 *
	 * The frame is never empty and never the wrong shape. The server's own WebP
	 * of the page — the same image the thumbnail strip uses, a size or two up —
	 * fills it immediately, and the canvas takes over the moment pdf.js has
	 * rasterised the real thing. On a slow tablet, or when the preference says
	 * so, the WebP simply stays and no PDF is parsed in the browser at all.
	 *
	 * The canvas has two sizes and they are deliberately different. Its CSS box
	 * is the page at fit size, which is what the layout is built from; its
	 * backing store is that multiplied by the zoom and the device pixel ratio,
	 * which is what makes a zoomed page sharp instead of a blur — the parent
	 * scales the whole stage with a transform, and finds the pixels already
	 * there.
	 */
	interface Props {
		/** The open document, or null in server-images mode. */
		document: PdfDocument | null;
		page: number;
		/** `pages_url_template` from the API, version and all. */
		template: string;
		/** The box this page has to fit into, in CSS pixels. */
		boxWidth: number;
		boxHeight: number;
		/** The page's own ratio, width over height, until the PDF says otherwise. */
		aspect: number;
		/** The settled zoom. Pixels are rendered for it; the transform is the parent's. */
		zoom?: number;
		/** Draw with the server's images instead of pdf.js. */
		server?: boolean;
		/** Fired the first time this page is actually on screen. */
		onready?: () => void;
		/**
		 * Fired when this page will not be drawn at all.
		 *
		 * The caller needs both: something covers this frame until the page
		 * arrives, and a page that is never arriving has to lift it too, or the
		 * reader looks stuck instead of saying what went wrong.
		 */
		onfail?: () => void;
	}

	let {
		document: doc,
		page,
		template,
		boxWidth,
		boxHeight,
		aspect,
		zoom = 1,
		server = false,
		onready,
		onfail
	}: Props = $props();

	let canvas = $state<HTMLCanvasElement | null>(null);
	let painted = $state(false);
	let placeholderLoaded = $state(false);
	let failed = $state(false);

	/** This page will not be drawn. Said once, and said upwards. */
	function fail() {
		if (failed) return;
		failed = true;
		onfail?.();
	}
	/** The page's true size at scale 1, once the document has been asked. */
	let natural = $state<Box | null>(null);

	/** The page's own box, or a page-shaped stand-in until the PDF says more. */
	const measured = $derived<Box>(natural ?? { width: aspect, height: 1 });
	const box = $derived<Box>({ width: boxWidth, height: boxHeight });
	const fitted = $derived(fittedSize(measured, box));
	const renderScale = $derived(natural ? scaleFor(natural, box, zoom, pixelRatio()) : 0);

	/** The server image behind the canvas, at a width the server renders. */
	const placeholderWidth = $derived(snapWidth(Math.max(1, fitted.width * (server ? zoom : 1))));
	const placeholderSrc = $derived(template ? pageImageUrl(template, page, placeholderWidth) : '');

	// The real size of the page, which the layout waits for in pdf.js mode.
	$effect(() => {
		const document_ = doc;
		if (!document_ || server) return;
		let live = true;
		document_
			.size(page)
			.then((size) => {
				if (live) natural = size;
			})
			.catch(() => {
				if (live) fail();
			});
		return () => {
			live = false;
		};
	});

	// Rasterise, and keep rasterising as the zoom or the window changes.
	$effect(() => {
		const document_ = doc;
		const target = canvas;
		const scale = renderScale;
		const number = page;
		if (server || !document_ || !target || scale <= 0) return;
		let live = true;
		document_
			.render(number, target, scale)
			.then((drawn) => {
				if (!live || !drawn) return;
				failed = false;
				if (!painted) {
					painted = true;
					onready?.();
				}
			})
			.catch((error: unknown) => {
				if (!live || isCancellation(error)) return;
				fail();
			});
		return () => {
			live = false;
			// A render still running for a page that has left the screen is
			// work the worker can stop doing.
			document_.cancel(number, scale);
		};
	});
</script>

<div
	class="relative bg-white shadow-cover"
	style:width="{fitted.width}px"
	style:height="{fitted.height}px"
>
	{#if placeholderSrc}
		<!-- The placeholder stays under the canvas rather than being removed:
		     swapping it out would flash the background between the two. -->
		<img
			src={placeholderSrc}
			alt=""
			decoding="async"
			fetchpriority={page === 1 ? 'high' : 'auto'}
			class="absolute inset-0 h-full w-full object-contain transition-opacity duration-150"
			class:opacity-0={!placeholderLoaded}
			onload={() => {
				placeholderLoaded = true;
				if (server) {
					failed = false;
					if (!painted) {
						painted = true;
						onready?.();
					}
				}
			}}
			onerror={() => {
				if (server) fail();
			}}
		/>
	{/if}

	{#if !server}
		<canvas
			bind:this={canvas}
			class="absolute inset-0 h-full w-full transition-opacity duration-150"
			class:opacity-0={!painted}
			aria-hidden="true"
		></canvas>
	{/if}

	{#if failed && !painted}
		<p class="absolute inset-0 grid place-content-center px-4 text-center text-[13px] text-muted">
			{m.reader_page_failed()}
		</p>
	{/if}
</div>
