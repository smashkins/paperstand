import { afterEach, describe, expect, it, vi } from 'vitest';
import { ApiError, api, buildQuery, isUnsortedIssue, quiet } from './client';

/** A `fetch` that answers once with the given body and status. */
function stubFetch(body: unknown, init: { status?: number; json?: boolean } = {}) {
	const status = init.status ?? 200;
	return vi.fn(
		async () =>
			new Response(init.json === false ? String(body) : JSON.stringify(body), {
				status,
				headers: { 'content-type': 'application/json' }
			})
	);
}

/** The single URL a stub was called with. */
function calledUrl(stub: ReturnType<typeof vi.fn>): string {
	return String(stub.mock.calls[0][0]);
}

afterEach(() => {
	vi.restoreAllMocks();
});

describe('buildQuery', () => {
	it('is empty when there is nothing to ask for', () => {
		expect(buildQuery()).toBe('');
		expect(buildQuery({ year: undefined, kind: null, title: '' })).toBe('');
	});

	it('keeps false and zero, which mean something', () => {
		expect(buildQuery({ include_unsorted: false, offset: 0 })).toBe(
			'?include_unsorted=false&offset=0'
		);
	});

	it('escapes what it puts in the query string', () => {
		expect(buildQuery({ title: 'la stampa/2026' })).toBe('?title=la+stampa%2F2026');
	});
});

describe('api', () => {
	it('asks for today without a date by default', async () => {
		const fetch = stubFetch({ date: '2026-03-17', newspapers: [] });
		await api.today(undefined, { fetch });
		expect(calledUrl(fetch)).toBe('/api/today');
	});

	it('passes a date through', async () => {
		const fetch = stubFetch({ date: '2026-03-17' });
		await api.today('2026-03-17', { fetch });
		expect(calledUrl(fetch)).toBe('/api/today?date=2026-03-17');
	});

	it('turns title parameters into a query string', async () => {
		const fetch = stubFetch([]);
		await api.titles({ kind: 'newspaper', sort: 'name', include_unsorted: true }, { fetch });
		expect(calledUrl(fetch)).toBe('/api/titles?kind=newspaper&sort=name&include_unsorted=true');
	});

	it('escapes an id into the path', async () => {
		const fetch = stubFetch({ id: 'a/b' });
		await api.title('a/b', { fetch });
		expect(calledUrl(fetch)).toBe('/api/titles/a%2Fb');
	});

	it('sends a progress update as JSON', async () => {
		const fetch = stubFetch({ page: 12, updated_at: '2026-03-17T08:00:00Z' });
		await api.putProgress('abc', 12, { fetch });
		const [url, init] = fetch.mock.calls[0] as unknown as [string, RequestInit];
		expect(url).toBe('/api/issues/abc/progress');
		expect(init.method).toBe('PUT');
		expect(init.body).toBe('{"page":12}');
	});

	it('lets a progress update outlive the page when it is asked to', async () => {
		const fetch = stubFetch({ page: 12, updated_at: '2026-03-17T08:00:00Z' });
		await api.putProgress('abc', 12, { fetch, keepalive: true });
		const [, init] = fetch.mock.calls[0] as unknown as [string, RequestInit];
		// The reader's last write happens as the tab is going away; without
		// this the browser is free to drop it during unload.
		expect(init.keepalive).toBe(true);
	});

	it('asks for nothing of the sort by default', async () => {
		const fetch = stubFetch({ page: 12, updated_at: '2026-03-17T08:00:00Z' });
		await api.putProgress('abc', 12, { fetch });
		const [, init] = fetch.mock.calls[0] as unknown as [string, RequestInit];
		expect(init.keepalive).toBeUndefined();
	});

	it('returns nothing for a 204', async () => {
		const fetch = vi.fn(async () => new Response(null, { status: 204 }));
		await expect(api.deleteProgress('abc', { fetch })).resolves.toBeUndefined();
	});

	it('asks issues for the missing and unreadable maintenance filters', async () => {
		const fetch = stubFetch({ items: [], total: 0 });
		await api.issues({ missing: true }, { fetch });
		expect(calledUrl(fetch)).toBe('/api/issues?missing=true');

		const secondFetch = stubFetch({ items: [], total: 0 });
		await api.issues({ unreadable: true }, { fetch: secondFetch });
		expect(calledUrl(secondFetch)).toBe('/api/issues?unreadable=true');
	});

	it('asks for the maintenance summary with no parameters', async () => {
		const fetch = stubFetch({ attention: 0 });
		await api.maintenance({ fetch });
		expect(calledUrl(fetch)).toBe('/api/maintenance');
	});

	it('asks for the gaps report, with and without a today override', async () => {
		const fetch = stubFetch([]);
		await api.maintenanceGaps({}, { fetch });
		expect(calledUrl(fetch)).toBe('/api/maintenance/gaps');

		const secondFetch = stubFetch([]);
		await api.maintenanceGaps({ today: '2026-04-16' }, { fetch: secondFetch });
		expect(calledUrl(secondFetch)).toBe('/api/maintenance/gaps?today=2026-04-16');
	});
});

describe('ApiError', () => {
	it('carries the status and FastAPI’s detail', async () => {
		const fetch = stubFetch({ detail: 'unknown issue' }, { status: 404 });
		const error = await api.issue('nope', { fetch }).catch((reason: unknown) => reason);
		expect(error).toBeInstanceOf(ApiError);
		expect((error as ApiError).status).toBe(404);
		expect((error as ApiError).detail).toBe('unknown issue');
		expect((error as ApiError).isNotFound).toBe(true);
	});

	it('reads the first message out of a validation error', async () => {
		const fetch = stubFetch(
			{ detail: [{ loc: ['query', 'year'], msg: 'input should be less than 9999' }] },
			{ status: 422 }
		);
		const error = (await api
			.issues({ year: 99999 }, { fetch })
			.catch((r: unknown) => r)) as ApiError;
		expect(error.detail).toBe('input should be less than 9999');
	});

	it('survives an error body that is not JSON at all', async () => {
		const fetch = vi.fn(async () => new Response('<html>502</html>', { status: 502 }));
		const error = (await api.stats({ fetch }).catch((r: unknown) => r)) as ApiError;
		expect(error.status).toBe(502);
		expect(error.detail).toBeUndefined();
	});

	it('reports a request that never landed as status 0', async () => {
		const fetch = vi.fn(async () => {
			throw new TypeError('Failed to fetch');
		});
		const error = (await api.health({ fetch }).catch((r: unknown) => r)) as ApiError;
		expect(error.status).toBe(0);
		expect(error.detail).toBe('Failed to fetch');
	});

	it('refuses a success whose body is not JSON', async () => {
		const fetch = vi.fn(async () => new Response('not json', { status: 200 }));
		const error = (await api.libraries({ fetch }).catch((r: unknown) => r)) as ApiError;
		expect(error.detail).toBe('the response was not JSON');
	});
});

describe('quiet', () => {
	it('leaves the promise rejecting for whoever awaits it', async () => {
		const failing = quiet(Promise.reject(new Error('boom')));
		await expect(failing).rejects.toThrow('boom');
	});
});

describe('isUnsortedIssue', () => {
	it('recognises the parser’s bucket by name', () => {
		expect(isUnsortedIssue({ title_name: 'Unsorted' })).toBe(true);
		expect(isUnsortedIssue({ title_name: 'La Stampa' })).toBe(false);
	});
});
