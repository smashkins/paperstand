import { api, quiet } from '$lib/api/client';
import type { PageLoad } from './$types';

/** The issue the reader will open, which the stub shows the cover of. */
export const load: PageLoad = ({ fetch, params }) => {
	return { issue: quiet(api.issue(params.issueId, { fetch })) };
};
