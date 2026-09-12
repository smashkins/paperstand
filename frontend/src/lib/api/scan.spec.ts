import { afterEach, describe, expect, it, vi } from 'vitest';
import { ApiError } from './client';
import { requestScan } from './scan';

function stubFetch(status: number, body: unknown = {}) {
	return vi.fn(
		async () =>
			new Response(JSON.stringify(body), {
				status,
				headers: { 'content-type': 'application/json' }
			})
	);
}

afterEach(() => {
	vi.restoreAllMocks();
});

describe('requestScan', () => {
	it('reports started once the request is accepted', async () => {
		const fetch = stubFetch(202, { scan_id: 7 });
		await expect(requestScan({ fetch })).resolves.toBe('started');
	});

	it('reports running instead of throwing on a 409', async () => {
		const fetch = stubFetch(409, { detail: 'scan running', scan_id: 3 });
		await expect(requestScan({ fetch })).resolves.toBe('running');
	});

	it('lets any other error propagate', async () => {
		const fetch = stubFetch(503, { detail: 'scanning is unavailable' });
		await expect(requestScan({ fetch })).rejects.toBeInstanceOf(ApiError);
	});
});
