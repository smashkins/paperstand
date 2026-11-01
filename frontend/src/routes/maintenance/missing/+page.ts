import { api, quiet } from '$lib/api/client';
import { loadMoreIssues } from '$lib/paging';
import type { PageLoad } from './$types';

/**
 * The Missing section in full: the first page of issues whose file has gone
 * missing, and the summary `missing_grace_days` comes from.
 */
export const load: PageLoad = async ({ fetch, parent }) => {
	// Maintenance's `+layout.ts` turns a non-administrator away before anything is asked.
	await parent();
	return {
		summary: quiet(api.maintenance({ fetch })),
		missing: quiet(loadMoreIssues({ missing: true }, null, { fetch }))
	};
};
