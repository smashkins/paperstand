import { api, quiet } from '$lib/api/client';
import { OVERVIEW_LIMIT } from '$lib/maintenance';
import type { PageLoad } from './$types';

/**
 * Everything the maintenance view shows: four independent calls, each
 * rendering as soon as its own promise settles.
 */
export const load: PageLoad = ({ fetch }) => {
	return {
		summary: quiet(api.maintenance({ fetch })),
		gaps: quiet(api.maintenanceGaps({}, { fetch })),
		missing: quiet(api.issues({ missing: true, limit: OVERVIEW_LIMIT }, { fetch })),
		unreadable: quiet(api.issues({ unreadable: true, limit: OVERVIEW_LIMIT }, { fetch }))
	};
};
