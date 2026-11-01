import { quiet } from '$lib/api/client';
import { loadMoreIssues } from '$lib/paging';
import type { PageLoad } from './$types';

/** The Unreadable section in full: the first page of issues the renderer could not open. */
export const load: PageLoad = async ({ fetch, parent }) => {
	// Maintenance's `+layout.ts` turns a non-administrator away before anything is asked.
	await parent();
	return {
		unreadable: quiet(loadMoreIssues({ unreadable: true }, null, { fetch }))
	};
};
