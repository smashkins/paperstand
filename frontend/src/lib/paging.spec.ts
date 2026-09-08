import { afterEach, describe, expect, it, vi } from 'vitest';
import type { Fetch, Issue } from './api/client';
import { hasMoreIssues, ISSUE_PAGE_SIZE, loadMoreIssues, mergeIssues, shouldFetch } from './paging';

function issue(id: string): Issue {
	return {
		id,
		title_id: 't',
		title_name: 'Confini',
		library_id: 'l',
		kind: 'magazine',
		date_precision: 'day',
		date_source: 'filename',
		label: id,
		filename: `${id}.pdf`,
		rel_path: `${id}.pdf`,
		size: 1,
		cover_url: '',
		thumb_url: '',
		file_url: '',
		added_at: '2026-01-01T00:00:00Z',
		is_duplicate: false
	};
}

/** A `fetch` that answers each call from `pages`, in order. */
function pagedFetch(pages: { items: Issue[]; total: number }[]) {
	let call = 0;
	return vi.fn(async () => {
		const page = pages[Math.min(call, pages.length - 1)];
		call += 1;
		return new Response(JSON.stringify(page), {
			status: 200,
			headers: { 'content-type': 'application/json' }
		});
	});
}

function urls(stub: { mock: { calls: unknown[][] } }): string[] {
	return stub.mock.calls.map((call) => String(call[0]));
}

afterEach(() => {
	vi.restoreAllMocks();
});

describe('mergeIssues', () => {
	it('appends what is new', () => {
		const merged = mergeIssues([issue('a')], [issue('b'), issue('c')]);
		expect(merged.map((entry) => entry.id)).toEqual(['a', 'b', 'c']);
	});

	it('never lets the same issue in twice', () => {
		const merged = mergeIssues([issue('a'), issue('b')], [issue('b'), issue('c')]);
		expect(merged.map((entry) => entry.id)).toEqual(['a', 'b', 'c']);
	});
});

describe('hasMoreIssues', () => {
	it('knows when there is another page behind the one in hand', () => {
		expect(hasMoreIssues({ items: [issue('a')], total: 3 })).toBe(true);
		expect(hasMoreIssues({ items: [issue('a')], total: 1 })).toBe(false);
		expect(hasMoreIssues(null)).toBe(false);
	});
});

describe('loadMoreIssues', () => {
	it('asks for the first page at offset zero', async () => {
		const fetch = pagedFetch([{ items: [issue('a')], total: 1 }]);
		const run = await loadMoreIssues({ title: 't', year: 2026 }, null, { fetch });
		expect(urls(fetch)).toEqual([
			`/api/issues?title=t&year=2026&limit=${ISSUE_PAGE_SIZE}&offset=0`
		]);
		expect(run).toEqual({ items: [issue('a')], total: 1 });
	});

	it('walks a year that holds more issues than one page', async () => {
		const first = Array.from({ length: ISSUE_PAGE_SIZE }, (_, index) => issue(`a${index}`));
		const second = Array.from({ length: 50 }, (_, index) => issue(`b${index}`));
		const total = first.length + second.length;
		const fetch = pagedFetch([
			{ items: first, total },
			{ items: second, total }
		]);

		let run = await loadMoreIssues({ title: 't', year: 2026 }, null, { fetch });
		expect(run.items).toHaveLength(ISSUE_PAGE_SIZE);
		expect(hasMoreIssues(run)).toBe(true);

		run = await loadMoreIssues({ title: 't', year: 2026 }, run, { fetch });
		expect(run.items).toHaveLength(total);
		expect(hasMoreIssues(run)).toBe(false);

		expect(urls(fetch)).toEqual([
			`/api/issues?title=t&year=2026&limit=${ISSUE_PAGE_SIZE}&offset=0`,
			`/api/issues?title=t&year=2026&limit=${ISSUE_PAGE_SIZE}&offset=${ISSUE_PAGE_SIZE}`
		]);
	});

	it('ends the run when a page adds nothing, whatever the total claims', async () => {
		const fetch = pagedFetch([
			{ items: [issue('a')], total: 99 },
			{ items: [issue('a')], total: 99 }
		]);
		let run = await loadMoreIssues({ title: 't' }, null, { fetch });
		expect(hasMoreIssues(run)).toBe(true);
		run = await loadMoreIssues({ title: 't' }, run, { fetch });
		// The second page repeated what we had: there is nothing more to get, and
		// a "load more" that can never be satisfied is worse than none.
		expect(run).toEqual({ items: [issue('a')], total: 1 });
		expect(hasMoreIssues(run)).toBe(false);
	});

	it('lets a failure through to the caller', async () => {
		const fetch = vi.fn(async () => new Response('{"detail":"boom"}', { status: 500 }));
		await expect(loadMoreIssues({ title: 't' }, null, { fetch })).rejects.toThrow();
	});
});

describe('shouldFetch', () => {
	it('asks for a key it does not have', () => {
		expect(shouldFetch('t:2026:0', null, null, null)).toBe(true);
	});

	it('does not ask again for what it already has', () => {
		expect(shouldFetch('t:2026:0', 't:2026:0', null, null)).toBe(false);
	});

	it('does not ask again while the request is in flight', () => {
		expect(shouldFetch('t:2026:0', null, null, 't:2026:0')).toBe(false);
	});

	it('stops dead after a failure until the error is cleared', () => {
		expect(shouldFetch('t:2026:0', null, new Error('boom'), null)).toBe(false);
	});

	it('asks again once a retry has cleared the error and moved the key', () => {
		expect(shouldFetch('t:2026:1', null, null, null)).toBe(true);
	});
});

describe('a year that keeps failing', () => {
	/**
	 * The loop this rule exists to prevent: the effect used to track `loading`,
	 * so a fast 5xx re-triggered it the instant the request settled.
	 */
	async function drive(passes: number, fetch: Fetch & { mock: { calls: unknown[] } }) {
		let loadedKey: string | null = null;
		let error: unknown = null;
		const key = 't:2026:0';
		for (let pass = 0; pass < passes; pass += 1) {
			// Every pass is the effect running again — after the request settles,
			// after `busy` flips, after anything at all. Nothing is in flight here
			// because the driver awaits each request; `shouldFetch` covers that arm
			// on its own.
			if (!shouldFetch(key, loadedKey, error, null)) continue;
			try {
				await loadMoreIssues({ title: 't', year: 2026 }, null, { fetch });
				loadedKey = key;
			} catch (reason) {
				error = reason;
			}
		}
		return { error };
	}

	it('makes exactly one request however often the effect runs again', async () => {
		const fetch = vi.fn(async () => new Response('{"detail":"boom"}', { status: 500 }));
		const { error } = await drive(20, fetch);
		expect(fetch).toHaveBeenCalledTimes(1);
		expect(error).toBeInstanceOf(Error);
	});

	it('makes exactly one request when it succeeds, too', async () => {
		const fetch = pagedFetch([{ items: [issue('a')], total: 1 }]);
		await drive(20, fetch);
		expect(fetch).toHaveBeenCalledTimes(1);
	});
});
