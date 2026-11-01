import { api, quiet } from '$lib/api/client';
import type { PageLoad } from './$types';

/** The Gaps section in full: every title with a hole in its numbering or its declared cadence. */
export const load: PageLoad = async ({ fetch, parent }) => {
	// Maintenance's `+layout.ts` turns a non-administrator away before anything is asked.
	await parent();
	return {
		gaps: quiet(api.maintenanceGaps({}, { fetch }))
	};
};
