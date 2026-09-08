/**
 * The document wrapper, against a pdf.js that is entirely made up.
 *
 * The two things worth pinning down here are both invisible from the outside
 * and both cost a reader something real: a prefetch that outlives the spread it
 * was for goes on rasterising broadsheet pages nobody is looking at, and a
 * render shared between the prefetcher and the page on screen must not turn one
 * of them's failure into the other's blank frame.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';

// The module sets `GlobalWorkerOptions.workerSrc` when it loads, and pulling
// three megabytes of pdf.js into a unit test to watch it do that is not worth
// it; nothing below touches the real library.
vi.mock('pdfjs-dist', () => ({
	GlobalWorkerOptions: { workerSrc: '' },
	getDocument: vi.fn()
}));

// `vi.mock` is hoisted above this, so the wrapper is loaded against the fake.
import { PdfDocument } from './pdf';

/** A render task that never finishes on its own, and remembers being cancelled. */
interface FakeTask {
	promise: Promise<void>;
	cancel: ReturnType<typeof vi.fn>;
	settle: () => void;
	fail: (error: unknown) => void;
	cancelled: boolean;
}

function fakeTask(): FakeTask {
	let resolve!: () => void;
	let reject!: (error: unknown) => void;
	const promise = new Promise<void>((res, rej) => {
		resolve = res;
		reject = rej;
	});
	// An unobserved rejection in a test run is noise; every path below either
	// awaits this promise or cancels it.
	promise.catch(() => {});
	const task: FakeTask = {
		promise,
		cancelled: false,
		cancel: vi.fn(() => {
			task.cancelled = true;
			const error = new Error('cancelled');
			error.name = 'RenderingCancelledException';
			reject(error);
		}),
		settle: () => resolve(),
		fail: (error: unknown) => reject(error)
	};
	return task;
}

/** The `OffscreenCanvas` the wrapper rasterises into, with nothing behind it. */
class FakeOffscreenCanvas {
	constructor(
		public width: number,
		public height: number
	) {}
	getContext() {
		return { drawImage: vi.fn(), clearRect: vi.fn() };
	}
	transferToImageBitmap() {
		return { close: vi.fn(), width: this.width, height: this.height };
	}
}

interface Harness {
	document: PdfDocument;
	tasks: Map<number, FakeTask>;
	renders: number[];
}

function harness(pageCount = 20): Harness {
	const tasks = new Map<number, FakeTask>();
	const renders: number[] = [];
	const proxy = {
		numPages: pageCount,
		getPage: async (number: number) => ({
			getViewport: ({ scale }: { scale: number }) => ({ width: 100 * scale, height: 140 * scale }),
			render: () => {
				renders.push(number);
				const task = fakeTask();
				tasks.set(number, task);
				return task;
			}
		})
	};
	const loading = { destroy: vi.fn(async () => {}) };
	return {
		// The constructor only ever reads `numPages` and `getPage` off these.
		document: new PdfDocument(loading as never, proxy as never),
		tasks,
		renders
	};
}

/** Let every pending microtask run, which is all these fakes ever wait on. */
const settle = () => new Promise((resolve) => setTimeout(resolve, 0));

beforeEach(() => {
	vi.stubGlobal('OffscreenCanvas', FakeOffscreenCanvas);
});

describe('prefetch', () => {
	it('warms exactly the pages it is given', async () => {
		const { document, renders } = harness();
		document.prefetch([2, 3], 1);
		await settle();
		expect(renders).toEqual([2, 3]);
	});

	it('cancels the pages a later call drops', async () => {
		const { document, tasks } = harness();
		document.prefetch([2, 3], 1);
		await settle();

		document.prefetch([4, 5], 1);
		await settle();

		expect(tasks.get(2)?.cancelled).toBe(true);
		expect(tasks.get(3)?.cancelled).toBe(true);
		expect(tasks.get(4)?.cancelled).toBe(false);
		expect(tasks.get(5)?.cancelled).toBe(false);
	});

	it('keeps a page that stays in the set, rather than restarting it', async () => {
		const { document, tasks, renders } = harness();
		document.prefetch([2, 3], 1);
		await settle();

		document.prefetch([3, 4], 1);
		await settle();

		expect(tasks.get(2)?.cancelled).toBe(true);
		expect(tasks.get(3)?.cancelled).toBe(false);
		expect(renders).toEqual([2, 3, 4]);
	});

	it('never cancels a render the foreground started', async () => {
		const { document, tasks, renders } = harness();
		// The page on screen asks first and owns the task.
		const foreground = document.rasterise(7, 1);
		await settle();
		expect(renders).toEqual([7]);

		document.prefetch([7], 1);
		await settle();
		// No second render for the same page and scale.
		expect(renders).toEqual([7]);

		document.prefetch([8], 1);
		await settle();
		expect(tasks.get(7)?.cancelled).toBe(false);

		tasks.get(7)?.settle();
		await expect(foreground).resolves.not.toBeNull();
	});

	it('stops warming everything when the document is destroyed', async () => {
		const { document, tasks } = harness();
		document.prefetch([2, 3], 1);
		await settle();

		await document.destroy();
		expect(tasks.get(2)?.cancelled).toBe(true);
		expect(tasks.get(3)?.cancelled).toBe(true);
		expect(document.destroyed).toBe(true);
	});
});

describe('a shared rasterisation', () => {
	it('gives a real failure to everybody waiting on it', async () => {
		const { document, tasks } = harness();
		const first = document.rasterise(4, 1);
		await settle();
		// A second caller joins the render already in flight.
		const second = document.rasterise(4, 1);

		tasks.get(4)?.fail(new Error('this page is corrupt'));

		await expect(first).rejects.toThrow('this page is corrupt');
		await expect(second).rejects.toThrow('this page is corrupt');
	});

	it('gives everybody waiting on it `null` when it is only cancelled', async () => {
		const { document, tasks } = harness();
		const first = document.rasterise(4, 1);
		await settle();
		const second = document.rasterise(4, 1);

		document.cancel(4, 1);
		expect(tasks.get(4)?.cancelled).toBe(true);

		await expect(first).resolves.toBeNull();
		await expect(second).resolves.toBeNull();
	});
});
