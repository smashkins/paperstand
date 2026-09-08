import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
	attachGestures,
	distance,
	DOUBLE_TAP_TIME,
	EDGE_FRACTION,
	focalPan,
	isInteractiveTarget,
	midpoint,
	SWIPE_DISTANCE,
	swipeDirection,
	tapZone,
	wheelFactor,
	type Direction,
	type Point
} from './gestures';

describe('tapZone', () => {
	it('turns back in the left tenth and forward in the right tenth', () => {
		expect(tapZone(20, 1000)).toBe('prev');
		expect(tapZone(980, 1000)).toBe('next');
	});

	it('leaves the middle to the chrome', () => {
		expect(tapZone(500, 1000)).toBe('centre');
		expect(tapZone(1000 * EDGE_FRACTION + 1, 1000)).toBe('centre');
	});

	it('is safe on a stage that has not been measured yet', () => {
		expect(tapZone(0, 0)).toBe('centre');
	});
});

describe('swipeDirection', () => {
	it('turns forward when the paper is dragged to the left', () => {
		expect(swipeDirection(-SWIPE_DISTANCE, 0, 200)).toBe('next');
	});

	it('turns back when it is dragged to the right', () => {
		expect(swipeDirection(SWIPE_DISTANCE, 4, 200)).toBe('prev');
	});

	it('ignores a drag that is mostly vertical', () => {
		expect(swipeDirection(-80, -120, 200)).toBeNull();
	});

	it('ignores a short slow drag', () => {
		expect(swipeDirection(-20, 0, 600)).toBeNull();
	});

	it('accepts a short flick, because speed says the same thing as distance', () => {
		expect(swipeDirection(-24, 0, 40)).toBe('next');
	});
});

describe('distance and midpoint', () => {
	it('measure a pinch', () => {
		expect(distance({ x: 0, y: 0 }, { x: 3, y: 4 })).toBe(5);
		expect(midpoint({ x: 0, y: 0 }, { x: 10, y: 20 })).toEqual({ x: 5, y: 10 });
	});
});

describe('wheelFactor', () => {
	it('zooms in when the wheel goes up and out when it goes down', () => {
		expect(wheelFactor(-100)).toBeGreaterThan(1);
		expect(wheelFactor(100)).toBeLessThan(1);
		expect(wheelFactor(0)).toBe(1);
	});

	it('stays a gentle multiplier even for a coarse mouse wheel', () => {
		expect(wheelFactor(-240)).toBeLessThan(3);
	});
});

describe('focalPan', () => {
	const centre: Point = { x: 500, y: 400 };

	/** Where the content that a point shows ends up after the transform. */
	function shows(at: Point, pan: Point, zoom: number): Point {
		return {
			x: centre.x + (at.x - centre.x - pan.x) / zoom,
			y: centre.y + (at.y - centre.y - pan.y) / zoom
		};
	}

	it('keeps what is under the fingers under the fingers', () => {
		const at: Point = { x: 800, y: 250 };
		const pan: Point = { x: 0, y: 0 };
		const before = shows(at, pan, 1);
		const after = shows(at, focalPan(at, centre, pan, 1, 2.5), 2.5);
		expect(after.x).toBeCloseTo(before.x, 6);
		expect(after.y).toBeCloseTo(before.y, 6);
	});

	it('does the same again from a stage that is already panned and zoomed', () => {
		const at: Point = { x: 210, y: 640 };
		const pan: Point = { x: -140, y: 75 };
		const before = shows(at, pan, 2);
		const after = shows(at, focalPan(at, centre, pan, 2, 1.4), 1.4);
		expect(after.x).toBeCloseTo(before.x, 6);
		expect(after.y).toBeCloseTo(before.y, 6);
	});

	it('leaves a zoom about the centre alone', () => {
		expect(focalPan(centre, centre, { x: 0, y: 0 }, 1, 3)).toEqual({ x: 0, y: 0 });
	});

	it('answers the pan it was given when the zoom it came from is nonsense', () => {
		expect(focalPan({ x: 1, y: 2 }, centre, { x: 7, y: 8 }, 0, 2)).toEqual({ x: 7, y: 8 });
	});
});

describe('isInteractiveTarget', () => {
	const element = (tagName: string, attributes: Record<string, string> = {}) => ({
		tagName,
		isContentEditable: false,
		getAttribute: (name: string) => attributes[name] ?? null
	});

	it('recognises the controls that have their own idea about a key', () => {
		for (const tag of ['A', 'BUTTON', 'INPUT', 'SELECT', 'TEXTAREA', 'SUMMARY', 'OPTION']) {
			expect(isInteractiveTarget(element(tag))).toBe(true);
		}
	});

	it('recognises one by its role as well as by its tag', () => {
		expect(isInteractiveTarget(element('DIV', { role: 'button' }))).toBe(true);
		expect(isInteractiveTarget(element('DIV', { role: 'presentation' }))).toBe(false);
	});

	it('recognises something being typed into', () => {
		expect(isInteractiveTarget({ ...element('DIV'), isContentEditable: true })).toBe(true);
	});

	it('leaves the page itself to the reader', () => {
		expect(isInteractiveTarget(element('DIV'))).toBe(false);
		expect(isInteractiveTarget(element('CANVAS'))).toBe(false);
		expect(isInteractiveTarget(null)).toBe(false);
		expect(isInteractiveTarget(undefined)).toBe(false);
	});
});

/**
 * A stage, with just enough of an element to be listened to.
 *
 * `attachGestures` wants `addEventListener`, a box and a width; a real DOM adds
 * nothing to what these tests are about.
 */
function stage(width = 1000, height = 800) {
	const listeners = new Map<string, (event: never) => void>();
	const node = {
		clientWidth: width,
		clientHeight: height,
		getBoundingClientRect: () => ({ left: 0, top: 0, width, height }),
		addEventListener: (type: string, handler: (event: never) => void) => {
			listeners.set(type, handler);
		},
		removeEventListener: (type: string) => {
			listeners.delete(type);
		}
	};
	const send = (type: string, event: Record<string, unknown>) => {
		listeners.get(type)?.({
			pointerId: 1,
			pointerType: 'touch',
			button: 0,
			...event
		} as never);
	};
	/** One tap: down and up in the same place, quickly. */
	const tap = (x: number, at: number) => {
		send('pointerdown', { clientX: x, clientY: 400, timeStamp: at });
		send('pointerup', { clientX: x, clientY: 400, timeStamp: at + 20 });
	};
	return { node: node as unknown as HTMLElement, send, tap };
}

describe('attachGestures', () => {
	// The deferred centre tap resolves on a real setTimeout; fake timers let the
	// tests that wait for it advance the clock instead of the wall.
	beforeEach(() => {
		vi.useFakeTimers();
	});

	afterEach(() => {
		vi.useRealTimers();
	});

	it('a centre tap toggles the chrome without reporting activity first', () => {
		const onActivity = vi.fn();
		const onTap = vi.fn();
		const { node, tap } = stage();
		const detach = attachGestures(node, { onActivity, onTap });

		tap(500, 1000);
		expect(onActivity).not.toHaveBeenCalled();
		expect(onTap).not.toHaveBeenCalled();

		vi.advanceTimersByTime(DOUBLE_TAP_TIME);

		expect(onTap).toHaveBeenCalledTimes(1);
		expect(onActivity).not.toHaveBeenCalled();
		detach();
	});

	it('a pending centre tap asks the chrome to hold still', () => {
		const onTapPending = vi.fn();
		const onTap = vi.fn();
		const { node, tap } = stage();
		const detach = attachGestures(node, { onTapPending, onTap });

		tap(500, 1000);
		expect(onTapPending).toHaveBeenCalledTimes(1);
		expect(onTap).not.toHaveBeenCalled();

		vi.advanceTimersByTime(DOUBLE_TAP_TIME);

		expect(onTap).toHaveBeenCalledTimes(1);
		detach();
	});

	it('reports activity for an edge tap', () => {
		const onActivity = vi.fn();
		const { node, tap } = stage();
		const detach = attachGestures(node, { onActivity, onTurn: vi.fn() });

		tap(980, 1000);

		expect(onActivity).toHaveBeenCalledTimes(1);
		detach();
	});

	it('reports activity for a swipe', () => {
		const onActivity = vi.fn();
		const { node, send } = stage();
		const detach = attachGestures(node, { onActivity, onTurn: vi.fn() });

		send('pointerdown', { clientX: 800, clientY: 400, timeStamp: 0 });
		send('pointermove', { clientX: 800 - SWIPE_DISTANCE, clientY: 400, timeStamp: 50 });
		send('pointerup', { clientX: 800 - SWIPE_DISTANCE, clientY: 400, timeStamp: 60 });

		expect(onActivity).toHaveBeenCalledTimes(1);
		detach();
	});

	it('reports activity for a double tap', () => {
		const onActivity = vi.fn();
		const { node, tap } = stage();
		const detach = attachGestures(node, { onActivity, onDoubleTap: vi.fn() });

		tap(500, 1000);
		tap(500, 1100);

		expect(onActivity).toHaveBeenCalledTimes(1);
		detach();
	});

	it('reports activity for a pan while zoomed', () => {
		const onActivity = vi.fn();
		const onPan = vi.fn();
		const { node, send } = stage();
		const detach = attachGestures(node, { onActivity, onPan }, { isZoomed: () => true });

		send('pointerdown', { clientX: 500, clientY: 400, timeStamp: 0 });
		send('pointermove', { clientX: 520, clientY: 410, timeStamp: 20 });

		expect(onActivity).toHaveBeenCalledTimes(1);
		expect(onPan).toHaveBeenCalledTimes(1);
		detach();
	});

	it('turns the page twice for two quick taps at the same edge', () => {
		const turns: Direction[] = [];
		const onDoubleTap = vi.fn();
		const { node, tap } = stage();
		const detach = attachGestures(node, { onTurn: (d) => turns.push(d), onDoubleTap });

		tap(980, 1000);
		tap(980, 1100); // well inside the double-tap window

		expect(turns).toEqual(['next', 'next']);
		expect(onDoubleTap).not.toHaveBeenCalled();
		detach();
	});

	it('still zooms on two quick taps in the middle', () => {
		const onDoubleTap = vi.fn();
		const onTurn = vi.fn();
		const { node, tap } = stage();
		const detach = attachGestures(node, { onTurn, onDoubleTap });

		tap(500, 1000);
		tap(500, 1100);

		expect(onDoubleTap).toHaveBeenCalledTimes(1);
		expect(onTurn).not.toHaveBeenCalled();
		detach();
	});

	it('does not let an edge tap prime a double tap in the middle', () => {
		const onDoubleTap = vi.fn();
		const { node, tap } = stage();
		const detach = attachGestures(node, { onDoubleTap, onTurn: vi.fn() });

		tap(980, 1000);
		tap(980, 1080);
		tap(980, 1160);

		expect(onDoubleTap).not.toHaveBeenCalled();
		detach();
	});

	it('reports the midpoint of a pinch, so the settle can be centred on it', () => {
		const pinches: { scale: number; centre: Point }[] = [];
		const { node, send } = stage();
		const detach = attachGestures(node, {
			onPinch: (scale, centre) => pinches.push({ scale, centre })
		});

		send('pointerdown', { pointerId: 1, clientX: 300, clientY: 400, timeStamp: 0 });
		send('pointerdown', { pointerId: 2, clientX: 500, clientY: 400, timeStamp: 10 });
		send('pointermove', { pointerId: 2, clientX: 700, clientY: 400, timeStamp: 30 });

		expect(pinches).toHaveLength(1);
		expect(pinches[0].scale).toBeCloseTo(2, 6);
		expect(pinches[0].centre).toEqual({ x: 500, y: 400 });
		detach();
	});
});
