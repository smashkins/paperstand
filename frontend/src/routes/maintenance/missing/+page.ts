import { api, quiet } from '$lib/api/client';
import { loadMoreIssues } from '$lib/paging';
import type { PageLoad } from './$types';

/**
 * The Missing section in full: the first page of issues whose file has gone
 * missing, and the summary `missing_grace_days` comes from.
 */
export const load: PageLoad = ({ fetch }) => {
	return {
		summary: quiet(api.maintenance({ fetch })),
		missing: quiet(loadMoreIssues({ missing: true }, null, { fetch }))
	};
};
