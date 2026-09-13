import { api, quiet } from '$lib/api/client';
import type { PageLoad } from './$types';

/** The Inbox section in full: the organizer's last run and everything it parked. */
export const load: PageLoad = ({ fetch }) => {
	return {
		summary: quiet(api.maintenance({ fetch }))
	};
};
