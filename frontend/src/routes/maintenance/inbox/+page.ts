import { api, quiet } from '$lib/api/client';
import type { PageLoad } from './$types';

/** The Inbox section in full: the organizer's last run and everything it parked. */
export const load: PageLoad = async ({ fetch, parent }) => {
	// Maintenance's `+layout.ts` turns a non-administrator away before anything is asked.
	await parent();
	return {
		summary: quiet(api.maintenance({ fetch }))
	};
};
