import { quiet } from '$lib/api/client';
import { loadMoreIssues } from '$lib/paging';
import type { PageLoad } from './$types';

/** The Unreadable section in full: the first page of issues the renderer could not open. */
export const load: PageLoad = ({ fetch }) => {
	return {
		unreadable: quiet(loadMoreIssues({ unreadable: true }, null, { fetch }))
	};
};
