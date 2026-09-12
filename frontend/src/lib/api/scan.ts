/**
 * Asking for a scan, the one way Settings and Maintenance both do it.
 *
 * A `409` is not a failure here — it means a scan was already running, which
 * is worth telling a person about, but not worth throwing over. Any other
 * error propagates as the `ApiError` it already is.
 */

import { ApiError, api, type CallOptions } from './client';

export type ScanRequest = 'started' | 'running';

export async function requestScan(options?: CallOptions): Promise<ScanRequest> {
	try {
		await api.scan(options);
		return 'started';
	} catch (error) {
		if (error instanceof ApiError && error.status === 409) return 'running';
		throw error;
	}
}
