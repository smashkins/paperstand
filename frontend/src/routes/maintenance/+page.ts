import { api, quiet } from '$lib/api/client';
import type { PageLoad } from './$types';

/** A maintenance page, not a shelf: the total is shown, only the first page of it. */
const LIST_LIMIT = 200;

/**
 * Everything the maintenance view shows: four independent calls, each
 * rendering as soon as its own promise settles.
 */
export const load: PageLoad = ({ fetch }) => {
	return {
		summary: quiet(api.maintenance({ fetch })),
		gaps: quiet(api.maintenanceGaps({}, { fetch })),
		missing: quiet(api.issues({ missing: true, limit: LIST_LIMIT }, { fetch })),
		unreadable: quiet(api.issues({ unreadable: true, limit: LIST_LIMIT }, { fetch }))
	};
};
