import { api, quiet } from '$lib/api/client';
import type { PageLoad } from './$types';

/**
 * What Settings shows: the libraries, what they cost on disk and the scanner.
 *
 * The scan status says what the scheduler is doing right now; `/api/health`
 * carries the only copy of *when* the last scan finished, which is why both are
 * asked for.
 */
export const load: PageLoad = ({ fetch }) => {
	return {
		stats: quiet(api.stats({ fetch })),
		scan: quiet(api.scanStatus({ fetch })),
		health: quiet(api.health({ fetch }))
	};
};
