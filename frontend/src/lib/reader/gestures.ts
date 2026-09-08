/**
 * The reader's touch and pointer language.
 *
 * One set of pointer events covers a finger, a pen and a mouse, so there is no
 * touch path and no mouse path to keep in step: a swipe is a swipe. What the
 * stage understands is
 *
 * * a horizontal **swipe** — far enough, or fast enough — to turn the page;
 * * a **pinch** with two pointers, applied as a CSS transform while the fingers
 *   are down and re-rasterised once they lift, because rasterising at sixty
 *   frames a second is how a reader turns into a slideshow;
 * * a **double tap** to go between fit-to-screen and 2×, centred on the tap;
 * * **ctrl/⌘ + wheel** for the same zoom with a trackpad;
 * * a tap in the outer tenth of either edge to turn the page, and a tap in the
 *   middle to show or hide the chrome.
 *
 * Every one of those reports activity to keep the chrome's auto-hide timer
 * fresh, except the tap in the middle: it is held back to see whether a
 * second tap turns it into a double tap, and reporting activity early would
 * only show the chrome for `toggleChrome` to hide again a moment later.
 *
 * The keyboard is the reader's own concern, but the two rules that decide
 * whether a key *reaches* it — is this target something that has its own idea
 * about the space bar — and the arithmetic a zoom does to the offset live here
 * too, next to everything else that turns input into intent.
 *
 * The geometry is pure functions at the top of the file so the rules can be
 * tested; only the listener plumbing needs a browser.
 */

/** A point in the stage's own coordinates. */
export interface Point {
	x: number;
	y: number;
}

/** Which way a gesture is going, in reading order. */
export type Direction = 'prev' | 'next';

/** Where along the width of the stage a tap landed. */
export type Zone = Direction | 'centre';

/** The fraction of the width at each edge that turns the page. */
export const EDGE_FRACTION = 0.1;

/** How far a swipe has to travel before it counts, in CSS pixels. */
export const SWIPE_DISTANCE = 56;

/** …or how fast, in pixels per millisecond, for a short flick to count. */
export const SWIPE_VELOCITY = 0.35;

/** A pointer that moved less than this, and lifted quickly, was a tap. */
export const TAP_SLOP = 10;

/** The longest a press can last and still be a tap, in milliseconds. */
export const TAP_TIME = 400;

/** How long a second tap has to arrive within to be a double tap. */
export const DOUBLE_TAP_TIME = 280;

/** …and how close to the first one. */
export const DOUBLE_TAP_SLOP = 36;

/** Where a tap at `x` landed across a stage `width` wide. */
export function tapZone(x: number, width: number, fraction = EDGE_FRACTION): Zone {
	if (width <= 0) return 'centre';
	if (x <= width * fraction) return 'prev';
	if (x >= width * (1 - fraction)) return 'next';
	return 'centre';
}

/**
 * The page turn a drag asks for, or `null` if it was not a swipe.
 *
 * A swipe has to be more horizontal than vertical — otherwise every scroll of
 * a zoomed page would turn one — and then either long enough or quick enough.
 */
export function swipeDirection(dx: number, dy: number, elapsed: number): Direction | null {
	if (Math.abs(dx) <= Math.abs(dy)) return null;
	const fast = elapsed > 0 && Math.abs(dx) / elapsed >= SWIPE_VELOCITY && Math.abs(dx) >= TAP_SLOP;
	if (Math.abs(dx) < SWIPE_DISTANCE && !fast) return null;
	// Dragging the paper to the left brings the next page in from the right.
	return dx < 0 ? 'next' : 'prev';
}

/** The distance between two pointers. */
export function distance(a: Point, b: Point): number {
	return Math.hypot(a.x - b.x, a.y - b.y);
}

/** The point halfway between two pointers: where a pinch is centred. */
export function midpoint(a: Point, b: Point): Point {
	return { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 };
}

/**
 * The zoom factor one wheel notch asks for.
 *
 * A trackpad pinch arrives as `ctrl` + wheel with a small delta and a mouse
 * wheel as a large one; the exponential keeps both feeling like the same
 * gesture instead of one of them jumping the whole range at once.
 */
export function wheelFactor(deltaY: number): number {
	return Math.exp(-deltaY / 300);
}

/**
 * The pan that keeps whatever is under `at` exactly where it is.
 *
 * The stage scales about its own centre, so a point `p` shows the content that
 * sits at `centre + (p − centre − pan) / z`. Holding that content still while
 * `z` becomes `z'` is this one line, and it is what makes a pinch feel attached
 * to the fingers rather than to the middle of the screen.
 */
export function focalPan(at: Point, centre: Point, pan: Point, from: number, to: number): Point {
	if (!(from > 0)) return pan;
	const factor = to / from;
	return {
		x: (at.x - centre.x) * (1 - factor) + pan.x * factor,
		y: (at.y - centre.y) * (1 - factor) + pan.y * factor
	};
}

/** Elements that have their own idea about a key press. */
const INTERACTIVE = new Set(['A', 'BUTTON', 'INPUT', 'SELECT', 'TEXTAREA', 'SUMMARY', 'OPTION']);

/** Roles that make something behave like one of them. */
const INTERACTIVE_ROLES = new Set(['button', 'link', 'checkbox', 'menuitem', 'tab', 'textbox']);

/**
 * True when a key press belongs to the thing that has focus, not to the reader.
 *
 * A reader who has tabbed to the toolbar and presses the space bar means "press
 * this button", not "turn the page" — and the global handler, which calls
 * `preventDefault`, would otherwise take the key and do neither.
 *
 * Duck-typed rather than `instanceof HTMLElement` — the argument is an
 * `EventTarget`, which promises none of the properties this reads — so the rule
 * can be tested without a document.
 */
export function isInteractiveTarget(target: unknown): boolean {
	const element = target as
		| { tagName?: unknown; isContentEditable?: unknown; getAttribute?: (name: string) => unknown }
		| null
		| undefined;
	if (!element || typeof element.tagName !== 'string') return false;
	if (element.isContentEditable === true) return true;
	if (INTERACTIVE.has(element.tagName.toUpperCase())) return true;
	const role = element.getAttribute?.('role');
	return typeof role === 'string' && INTERACTIVE_ROLES.has(role);
}

/** What the stage does when a gesture completes. Every one is optional. */
export interface GestureHandlers {
	/** A swipe, or a tap in one of the edge zones. */
	onTurn?(direction: Direction): void;
	/** A tap in the middle: show or hide the chrome. */
	onTap?(): void;
	/** Two taps in the same place: toggle the zoom around that point. */
	onDoubleTap?(point: Point): void;
	/** A pinch in progress. `scale` is relative to where the pinch started. */
	onPinch?(scale: number, centre: Point): void;
	/** The fingers lifted: settle the zoom and rasterise at the new scale. */
	onPinchEnd?(): void;
	/** ctrl/⌘ + wheel, as a multiplier around a point. */
	onWheelZoom?(factor: number, point: Point): void;
	/** A one-finger drag while zoomed in. */
	onPan?(dx: number, dy: number): void;
	/** Anything but a centre tap happened: the chrome's auto-hide timer restarts. */
	onActivity?(): void;
}

export interface GestureOptions {
	/** True while the page is zoomed in, when a drag pans instead of turning. */
	isZoomed?(): boolean;
}

interface Tracked {
	id: number;
	start: Point;
	last: Point;
	startedAt: number;
	moved: boolean;
}

/**
 * Listen for the reader's gestures on `node` until the returned function is
 * called.
 *
 * The node needs `touch-action: none` for any of this to be reachable: without
 * it the browser takes the horizontal drag for a scroll and the pinch for a
 * page zoom before a single event is dispatched.
 */
export function attachGestures(
	node: HTMLElement,
	handlers: GestureHandlers,
	options: GestureOptions = {}
): () => void {
	const pointers = new Map<number, Tracked>();
	let pinchStartDistance = 0;
	let pinching = false;
	let lastTapAt = 0;
	let lastTapPoint: Point = { x: 0, y: 0 };
	let pendingTap: ReturnType<typeof setTimeout> | null = null;

	const local = (event: PointerEvent): Point => {
		const box = node.getBoundingClientRect();
		return { x: event.clientX - box.left, y: event.clientY - box.top };
	};

	/**
	 * Capture the pointer, or shrug.
	 *
	 * A pointer that has already been released — or one that never was a real
	 * pointer — makes the capture calls throw `InvalidPointerId`, and losing a
	 * capture is never worth losing the gesture over.
	 */
	const capture = (id: number, on: boolean) => {
		try {
			if (on) node.setPointerCapture?.(id);
			else node.releasePointerCapture?.(id);
		} catch {
			// The gesture is tracked in this closure either way.
		}
	};

	const clearPendingTap = () => {
		if (pendingTap === null) return;
		clearTimeout(pendingTap);
		pendingTap = null;
	};

	function onPointerDown(event: PointerEvent) {
		if (event.pointerType === 'mouse' && event.button !== 0) return;
		const point = local(event);
		pointers.set(event.pointerId, {
			id: event.pointerId,
			start: point,
			last: point,
			startedAt: event.timeStamp,
			moved: false
		});
		capture(event.pointerId, true);
		if (pointers.size === 2) {
			const [a, b] = [...pointers.values()];
			pinchStartDistance = distance(a.last, b.last);
			pinching = pinchStartDistance > 0;
			clearPendingTap();
		}
	}

	function onPointerMove(event: PointerEvent) {
		const tracked = pointers.get(event.pointerId);
		if (!tracked) return;
		const point = local(event);
		const previous = tracked.last;
		tracked.last = point;
		if (distance(tracked.start, point) > TAP_SLOP) tracked.moved = true;

		if (pinching && pointers.size >= 2) {
			const [a, b] = [...pointers.values()];
			const spread = distance(a.last, b.last);
			if (pinchStartDistance > 0) {
				handlers.onActivity?.();
				handlers.onPinch?.(spread / pinchStartDistance, midpoint(a.last, b.last));
			}
			return;
		}

		if (pointers.size === 1 && tracked.moved && options.isZoomed?.()) {
			handlers.onActivity?.();
			handlers.onPan?.(point.x - previous.x, point.y - previous.y);
		}
	}

	function finish(event: PointerEvent, cancelled: boolean) {
		const tracked = pointers.get(event.pointerId);
		pointers.delete(event.pointerId);
		capture(event.pointerId, false);
		if (!tracked) return;

		if (pinching) {
			// The pinch is over as soon as one finger leaves; the other one is
			// not the start of a swipe, so it is dropped with it.
			if (pointers.size < 2) {
				pinching = false;
				pinchStartDistance = 0;
				pointers.clear();
				handlers.onPinchEnd?.();
			}
			return;
		}
		if (cancelled) return;

		const dx = tracked.last.x - tracked.start.x;
		const dy = tracked.last.y - tracked.start.y;
		const elapsed = event.timeStamp - tracked.startedAt;

		if (tracked.moved) {
			handlers.onActivity?.();
			// A drag on a zoomed page has already panned; it never turns.
			if (options.isZoomed?.()) return;
			const direction = swipeDirection(dx, dy, elapsed);
			if (direction) handlers.onTurn?.(direction);
			return;
		}
		if (elapsed > TAP_TIME) return;

		const point = tracked.last;
		const zone = tapZone(point.x, node.clientWidth);
		if (zone !== 'centre') {
			// An edge tap answers at once — nobody double-taps a page-turn
			// target — and it takes no part in the double-tap bookkeeping
			// either, or two quick taps at the same edge would turn the page
			// once and then zoom.
			clearPendingTap();
			lastTapAt = 0;
			handlers.onActivity?.();
			handlers.onTurn?.(zone);
			return;
		}

		const isDouble =
			event.timeStamp - lastTapAt <= DOUBLE_TAP_TIME &&
			distance(point, lastTapPoint) <= DOUBLE_TAP_SLOP;
		lastTapAt = event.timeStamp;
		lastTapPoint = point;
		if (isDouble) {
			clearPendingTap();
			lastTapAt = 0;
			handlers.onActivity?.();
			handlers.onDoubleTap?.(point);
			return;
		}

		// A tap in the middle waits, because it is the one that a double tap
		// starts with — and it reports no activity of its own, so `onTap` is
		// free to hide an already-visible chrome instead of finding it just
		// shown and undoing itself.
		clearPendingTap();
		pendingTap = setTimeout(() => {
			pendingTap = null;
			handlers.onTap?.();
		}, DOUBLE_TAP_TIME);
	}

	function onPointerUp(event: PointerEvent) {
		finish(event, false);
	}

	function onPointerCancel(event: PointerEvent) {
		finish(event, true);
	}

	function onWheel(event: WheelEvent) {
		if (!event.ctrlKey && !event.metaKey) return;
		// The browser's own page zoom is not what a reader means here.
		event.preventDefault();
		handlers.onActivity?.();
		const box = node.getBoundingClientRect();
		handlers.onWheelZoom?.(wheelFactor(event.deltaY), {
			x: event.clientX - box.left,
			y: event.clientY - box.top
		});
	}

	node.addEventListener('pointerdown', onPointerDown, { passive: true });
	node.addEventListener('pointermove', onPointerMove, { passive: true });
	node.addEventListener('pointerup', onPointerUp, { passive: true });
	node.addEventListener('pointercancel', onPointerCancel, { passive: true });
	// Not passive: this one has a `preventDefault` to make.
	node.addEventListener('wheel', onWheel, { passive: false });

	return () => {
		clearPendingTap();
		node.removeEventListener('pointerdown', onPointerDown);
		node.removeEventListener('pointermove', onPointerMove);
		node.removeEventListener('pointerup', onPointerUp);
		node.removeEventListener('pointercancel', onPointerCancel);
		node.removeEventListener('wheel', onWheel);
	};
}
