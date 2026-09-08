<script lang="ts">
	import { onDestroy } from 'svelte';
	import { goto } from '$app/navigation';
	import { resolve } from '$app/paths';
	import { api, type IssueDetail } from '$lib/api/client';
	import { coverAspect } from '$lib/format';
	import { m } from '$lib/paraglide/messages.js';
	import { prefs } from '$lib/stores/prefs.svelte';
	import { coverTransitionName } from '$lib/stores/transition.svelte';
	import { attachGestures, focalPan, isInteractiveTarget, type Point } from '$lib/reader/gestures';
	import { fitScale, renderScale as scaleFor } from '$lib/reader/pages';
	import { openDocument, pixelRatio, type PdfDocument } from '$lib/reader/pdf';
	import {
		clampPage,
		lastSpread,
		nextSpread,
		prefetchPages,
		prevSpread,
		resolveLayout,
		spreadPages,
		spreadStart
	} from '$lib/reader/spreads';
	import Button, { buttonClass } from '$lib/components/ui/Button.svelte';
	import Icon from '$lib/components/ui/Icon.svelte';
	import PdfPage from './PdfPage.svelte';
	import ReaderToolbar from './ReaderToolbar.svelte';
	import ThumbStrip from './ThumbStrip.svelte';

	/**
	 * The reader itself: one issue, the whole screen, and as little else as
	 * possible.
	 *
	 * It owns the state the pages and the chrome are drawn from — which page,
	 * one page or two, how far in, whether the bars are showing — and it owns
	 * the document, which is the part that matters: a PDF worker and a handful
	 * of rasterised broadsheet pages are tens of megabytes, so leaving the route
	 * destroys them rather than trusting the garbage collector to notice.
	 *
	 * Zoom is deliberately split in two. `zoom` is settled and the pages are
	 * rasterised for it; `live` is what a pinch is doing right now and is only
	 * ever a CSS transform. When the fingers lift the two are folded together
	 * and the pages come back sharp.
	 */
	interface Props {
		issue: IssueDetail;
	}

	let { issue }: Props = $props();

	/** How long the bars stay up after the last thing anyone did. */
	const CHROME_TIMEOUT = 2500;
	/** How long after the last wheel notch a zoom counts as settled. */
	const WHEEL_SETTLE = 220;
	/** How long the reader waits before writing a new position to the server. */
	const SAVE_DEBOUNCE = 1000;
	const MIN_ZOOM = 1;
	const MAX_ZOOM = 6;
	/** The gutter between two pages of a spread. */
	const GUTTER = 2;
	/** The breathing room around the stage. */
	const MARGIN = 8;

	let root = $state<HTMLElement | null>(null);
	let stage = $state<HTMLElement | null>(null);
	let stageWidth = $state(0);
	let stageHeight = $state(0);
	let windowWidth = $state(0);
	let windowHeight = $state(0);

	let document_ = $state<PdfDocument | null>(null);
	let documentError = $state<unknown>(null);
	let attempt = $state(0);
	let ready = $state(false);

	/**
	 * Where the reader is, before the layout has had its say.
	 *
	 * The page on screen is {@link page}: the *start* of the spread this sits
	 * in. Keeping the raw number apart matters when the layout changes under it
	 — page 3 in single-page mode is the 2-3 spread in double, and comparing
	 * navigation against the raw 3 would leave "next" lit on the last spread.
	 */
	let position = $state(1);
	let moved = $state(false);
	let zoom = $state(1);
	let live = $state(1);
	let pan = $state<Point>({ x: 0, y: 0 });
	let chrome = $state(true);
	let thumbsOpen = $state(false);
	let shortcutsOpen = $state(false);
	let fullscreen = $state(false);

	const server = $derived(prefs.images === 'server');
	const aspect = $derived(coverAspect(issue.aspect));
	const pageCount = $derived(document_?.pageCount ?? issue.page_count ?? 1);
	const layout = $derived(
		resolveLayout(prefs.spread, { width: windowWidth, height: windowHeight })
	);
	/** The spread the reader is on: the only page number anything else uses. */
	const page = $derived(spreadStart(position, pageCount, layout));
	const visible = $derived(spreadPages(page, pageCount, layout));
	const canPrev = $derived(page > 1);
	const canNext = $derived(nextSpread(page, pageCount, layout) !== page);

	/** The box one page gets. Two pages' worth even when the cover is alone. */
	const pageBox = $derived.by(() => {
		const width = Math.max(0, stageWidth - MARGIN * 2);
		const height = Math.max(0, stageHeight - MARGIN * 2);
		return {
			width: layout === 'double' ? Math.max(0, (width - GUTTER) / 2) : width,
			height
		};
	});

	// ------------------------------------------------------------ the document

	$effect(() => {
		const url = issue.file_url;
		// `attempt` is read so that the retry button re-runs this effect.
		void attempt;
		if (server || !url) return;
		let opened: PdfDocument | null = null;
		let live_ = true;
		documentError = null;
		openDocument(url)
			.then((doc) => {
				opened = doc;
				if (!live_) {
					void doc.destroy();
					return;
				}
				document_ = doc;
			})
			.catch((error: unknown) => {
				if (live_) documentError = error;
			});
		return () => {
			live_ = false;
			document_ = null;
			void opened?.destroy();
		};
	});

	// Keep the neighbouring spread warm, at the scale it will be drawn at.
	$effect(() => {
		const doc = document_;
		const box = pageBox;
		const currentZoom = zoom;
		if (!doc || server || box.width <= 0) return;
		// Only the neighbours: the spread on screen belongs to its `PdfPage`s,
		// and warming a page they are already rendering would hand this effect
		// the right to cancel their work.
		const here = new Set(spreadPages(page, pageCount, layout));
		const wanted = prefetchPages(page, pageCount, layout).filter((number) => !here.has(number));
		let cancelled = false;
		void doc
			.size(1)
			.then((natural) => {
				// The reader may have moved on while the page was being fetched;
				// starting six high-resolution renders for a spread nobody is
				// looking at any more is exactly what this guard is for.
				if (cancelled || fitScale(natural, box) <= 0) return;
				const scale = scaleFor(natural, box, currentZoom, pixelRatio());
				// One call, one set: this supersedes and cancels the last one.
				doc.prefetch(wanted, scale);
			})
			.catch(() => null);
		return () => {
			cancelled = true;
		};
	});

	// ------------------------------------------------------------- navigation

	function show(next: number, byHand = true) {
		const target = spreadStart(clampPage(next, pageCount), pageCount, layout);
		if (byHand) moved = true;
		if (target === page) return;
		position = target;
		resetZoom();
	}

	function turn(direction: 'prev' | 'next') {
		show(
			direction === 'next'
				? nextSpread(page, pageCount, layout)
				: prevSpread(page, pageCount, layout)
		);
	}

	function back() {
		if (typeof history !== 'undefined' && history.length > 1) {
			history.back();
			return;
		}
		void goto(resolve('/title/[id]', { id: issue.title_id }));
	}

	// ------------------------------------------------------------------- zoom

	function clampZoom(value: number): number {
		return Math.min(Math.max(value, MIN_ZOOM), MAX_ZOOM);
	}

	/** Keep the picture from being dragged off the screen entirely. */
	function clampPan(next: Point, at: number): Point {
		const maxX = Math.max(0, (stageWidth * (at - 1)) / 2);
		const maxY = Math.max(0, (stageHeight * (at - 1)) / 2);
		return {
			x: Math.min(Math.max(next.x, -maxX), maxX),
			y: Math.min(Math.max(next.y, -maxY), maxY)
		};
	}

	function resetZoom() {
		zoom = 1;
		live = 1;
		pan = { x: 0, y: 0 };
	}

	/** Change the zoom around a point, so what is under the finger stays there. */
	function zoomAround(next: number, at: Point | null) {
		const target = clampZoom(next);
		const previous = zoom;
		if (target === previous) return;
		if (at && stageWidth > 0) {
			const centre = { x: stageWidth / 2, y: stageHeight / 2 };
			pan = clampPan(focalPan(at, centre, pan, previous, target), target);
		}
		zoom = target;
		if (target === MIN_ZOOM) pan = { x: 0, y: 0 };
		live = 1;
	}

	let wheelTimer: ReturnType<typeof setTimeout> | null = null;
	let wheelAt: Point | null = null;
	/** Where the fingers were the last time a pinch reported itself. */
	let pinchAt: Point | null = null;

	function wheelZoom(factor: number, at: Point) {
		wheelAt = at;
		live = clampZoom(zoom * live * factor) / zoom;
		if (wheelTimer !== null) clearTimeout(wheelTimer);
		wheelTimer = setTimeout(() => {
			wheelTimer = null;
			// Settling is what puts real pixels behind the transform.
			zoomAround(zoom * live, wheelAt);
		}, WHEEL_SETTLE);
	}

	// --------------------------------------------------------------- chrome

	let hideTimer: ReturnType<typeof setTimeout> | null = null;

	function activity() {
		chrome = true;
		if (hideTimer !== null) clearTimeout(hideTimer);
		// The strip and the shortcut panel are deliberate choices to keep the
		// chrome up; nothing hides underneath a reader who just opened it.
		if (thumbsOpen || shortcutsOpen) return;
		hideTimer = setTimeout(() => {
			hideTimer = null;
			chrome = false;
		}, CHROME_TIMEOUT);
	}

	function toggleChrome() {
		if (chrome) {
			if (hideTimer !== null) clearTimeout(hideTimer);
			hideTimer = null;
			chrome = false;
			return;
		}
		activity();
	}

	async function toggleFullscreen() {
		activity();
		try {
			if (typeof document === 'undefined') return;
			if (document.fullscreenElement) await document.exitFullscreen();
			else await root?.requestFullscreen();
		} catch {
			// A browser that refuses full screen simply stays as it is.
		}
	}

	function toggleLayout() {
		prefs.set('spread', layout === 'double' ? 'single' : 'double');
		activity();
	}

	// ------------------------------------------------------------- progress

	let saveTimer: ReturnType<typeof setTimeout> | null = null;
	/** The page the server already has. Set when the stored position is read. */
	let saved = 0;
	let restored = false;

	/**
	 * Write the position down now.
	 *
	 * `keepalive` is for the flushes that happen as the page is going away: an
	 * ordinary request started during unload is fair game for the browser to
	 * cancel, and the position a reader had just turned to is exactly the one
	 * worth not losing.
	 */
	function flushProgress(keepalive = false) {
		if (saveTimer !== null) {
			clearTimeout(saveTimer);
			saveTimer = null;
		}
		const target = page;
		// Page one is where every issue starts; storing it for a reader who
		// only glanced at the cover would fill "continue reading" with nothing.
		if (target === 1 && !moved) return;
		if (target === saved) return;
		saved = target;
		api.putProgress(issue.id, target, { keepalive }).catch(() => {
			// A position that did not reach the server is worth trying again.
			saved = -1;
		});
	}

	function queueProgress() {
		if (saveTimer !== null) clearTimeout(saveTimer);
		saveTimer = setTimeout(() => {
			saveTimer = null;
			flushProgress();
		}, SAVE_DEBOUNCE);
	}

	/**
	 * Where the reader left off, if it was anywhere worth returning to.
	 *
	 * Once only, and never the first or the last page: landing on the cover is
	 * what happens anyway, and landing on the back page of something finished
	 * is not where anyone wants to start.
	 */
	$effect(() => {
		if (restored) return;
		restored = true;
		const stored = issue.progress?.page ?? 0;
		const total = issue.page_count ?? 0;
		saved = stored;
		if (stored <= 1) return;
		if (total > 0 && stored >= total) return;
		const count = total > 0 ? total : stored;
		position = spreadStart(clampPage(stored, count), count, layout);
	});

	$effect(() => {
		void page;
		queueProgress();
	});

	function onVisibility() {
		// A tab going away is the last chance to write the position down.
		if (document.visibilityState === 'hidden') flushProgress(true);
	}

	onDestroy(() => {
		flushProgress();
		if (hideTimer !== null) clearTimeout(hideTimer);
		if (wheelTimer !== null) clearTimeout(wheelTimer);
	});

	// ------------------------------------------------------------- gestures

	$effect(() => {
		const node = stage;
		if (!node) return;
		return attachGestures(
			node,
			{
				onTurn: turn,
				onTap: toggleChrome,
				onDoubleTap: (at) => zoomAround(zoom > MIN_ZOOM ? MIN_ZOOM : 2, at),
				onPinch: (scale, centre) => {
					// The midpoint travels with the fingers; the settle needs the
					// last one, or the picture springs back to the middle of the
					// screen the moment they lift.
					pinchAt = centre;
					live = clampZoom(zoom * scale) / zoom;
				},
				onPinchEnd: () => zoomAround(zoom * live, pinchAt),
				onWheelZoom: wheelZoom,
				onPan: (dx, dy) => {
					pan = clampPan({ x: pan.x + dx, y: pan.y + dy }, zoom);
				},
				onActivity: activity
			},
			{ isZoomed: () => zoom > MIN_ZOOM || live !== 1 }
		);
	});

	function onWindowPointerMove(event: PointerEvent) {
		// A finger already reports its own activity through the gesture layer;
		// treating its jitter as movement here is what made a tap flash the chrome.
		if (event.pointerType === 'touch') return;
		activity();
	}

	function onKeydown(event: KeyboardEvent) {
		if (event.metaKey || event.ctrlKey || event.altKey) return;
		// A key pressed on a focused control belongs to that control. Space
		// would otherwise turn the page *and* swallow the press, so a reader on
		// a keyboard could reach the toolbar and never use it. `Escape` is the
		// one exception: nothing in the toolbar does anything with it, and
		// closing whatever is open is the right answer from anywhere.
		if (event.key !== 'Escape' && isInteractiveTarget(event.target)) return;
		let handled = true;
		switch (event.key) {
			case 'ArrowRight':
			case 'PageDown':
			case ' ':
				turn('next');
				break;
			case 'ArrowLeft':
			case 'PageUp':
				turn('prev');
				break;
			case 'Home':
				show(1);
				break;
			case 'End':
				show(lastSpread(pageCount, layout));
				break;
			case '+':
			case '=':
				zoomAround(zoom * 1.25, null);
				break;
			case '-':
			case '_':
				zoomAround(zoom / 1.25, null);
				break;
			case '0':
				resetZoom();
				break;
			case 'f':
			case 'F':
				void toggleFullscreen();
				break;
			case 't':
			case 'T':
				thumbsOpen = !thumbsOpen;
				break;
			case 'Escape':
				// In full screen the browser's own Escape is the one that counts.
				if (document.fullscreenElement) handled = false;
				else if (shortcutsOpen) shortcutsOpen = false;
				else if (thumbsOpen) thumbsOpen = false;
				else back();
				break;
			default:
				handled = false;
		}
		if (handled) {
			event.preventDefault();
			activity();
		}
	}

	// Opening either panel pins the chrome; closing one starts the clock again.
	$effect(() => {
		void thumbsOpen;
		void shortcutsOpen;
		activity();
	});
</script>

<svelte:window
	bind:innerWidth={windowWidth}
	bind:innerHeight={windowHeight}
	onkeydown={onKeydown}
	onpointermove={onWindowPointerMove}
	onfullscreenchange={() => (fullscreen = Boolean(document.fullscreenElement))}
	onpagehide={() => flushProgress(true)}
/>
<svelte:document onvisibilitychange={onVisibility} />

<div
	bind:this={root}
	class="fixed inset-0 flex flex-col overflow-hidden bg-bg"
	class:cursor-none={!chrome}
>
	<ReaderToolbar
		{issue}
		page={visible[0] ?? 1}
		{pageCount}
		{layout}
		{zoom}
		visible={chrome}
		{thumbsOpen}
		{shortcutsOpen}
		{fullscreen}
		{canPrev}
		{canNext}
		onback={back}
		onprev={() => turn('prev')}
		onnext={() => turn('next')}
		onlayout={toggleLayout}
		onzoom={(factor) => zoomAround(zoom * factor, null)}
		onzoomreset={resetZoom}
		onthumbs={() => (thumbsOpen = !thumbsOpen)}
		onshortcuts={() => (shortcutsOpen = !shortcutsOpen)}
		onfullscreen={toggleFullscreen}
	/>

	<div
		bind:this={stage}
		bind:clientWidth={stageWidth}
		bind:clientHeight={stageHeight}
		class="reader-stage relative min-h-0 flex-1 overflow-hidden"
	>
		{#if documentError}
			<div class="grid h-full place-content-center justify-items-center gap-3 px-6 text-center">
				<p class="opsz-title flex items-center gap-2 font-serif text-lg font-semibold">
					<Icon name="alert" size={18} class="text-accent-text" />
					{m.reader_failed()}
				</p>
				<p class="max-w-[46ch] text-muted">{m.reader_failed_body()}</p>
				<div class="mt-1 flex flex-wrap justify-center gap-3">
					<Button variant="primary" onclick={() => (attempt += 1)}>
						<Icon name="refresh" size={14} />
						{m.error_retry()}
					</Button>
					<!-- A document this reader cannot draw is very often one the
					     browser's own viewer can, so the last resort is the file
					     itself. Not a `Button`: its typed `href` is for routes. -->
					<a href={issue.file_url} rel="external" class={buttonClass('secondary')}>
						{m.reader_open_file()}
					</a>
					<Button onclick={back}>{m.reader_back()}</Button>
				</div>
			</div>
		{:else}
			<div
				class="absolute inset-0 flex items-center justify-center will-change-transform"
				style:gap="{GUTTER}px"
				style:transform="translate({pan.x}px, {pan.y}px) scale({zoom * live})"
			>
				{#each visible as number (number)}
					<PdfPage
						document={document_}
						page={number}
						template={issue.pages_url_template}
						boxWidth={pageBox.width}
						boxHeight={pageBox.height}
						{aspect}
						{zoom}
						{server}
						onready={() => (ready = true)}
						onfail={() => (ready = true)}
					/>
				{/each}
			</div>

			{#if !ready}
				<!-- The cover the storefront handed over, holding the frame until
				     the first page is actually drawn. -->
				<div class="pointer-events-none absolute inset-0 grid place-content-center p-4">
					<img
						src={issue.cover_url}
						alt={issue.title_name}
						class="max-h-[80vh] rounded-cover shadow-cover"
						style:view-transition-name={coverTransitionName(issue.id)}
						style:aspect-ratio={aspect}
					/>
				</div>
			{/if}
		{/if}
	</div>

	{#if thumbsOpen}
		<div class="relative z-30 shrink-0 pb-[3.25rem]">
			<ThumbStrip
				{pageCount}
				current={visible}
				template={issue.pages_url_template}
				{aspect}
				onselect={(number) => show(number)}
			/>
		</div>
	{/if}
</div>

<style>
	/* Without this the browser claims the horizontal drag for a scroll and the
	   pinch for its own page zoom, and no gesture ever reaches the stage. */
	.reader-stage {
		touch-action: none;
	}
</style>
