import { api, quiet } from '$lib/api/client';
import type { PageLoad } from './$types';

/** The Gaps section in full: every title with a hole in its numbering or its declared cadence. */
export const load: PageLoad = ({ fetch }) => {
	return {
		gaps: quiet(api.maintenanceGaps({}, { fetch }))
	};
};
