import { error } from '@sveltejs/kit';
import type { LayoutLoad } from './$types';

/**
 * Maintenance is an administrator's: the server answers everybody else's
 * requests for it with a `403`, so nobody else is shown the pages either.
 *
 * Every page under here awaits `parent()` before it asks for anything — loads
 * otherwise run side by side — so a reader who follows an old link gets the
 * error page without the SPA sending one request it knows will be refused.
 * A `null` user means the server could not be asked who this is at all; the
 * page's own requests are left to say so, with a retry.
 */
export const load: LayoutLoad = async ({ parent }) => {
	const { user } = await parent();
	if (user && !user.is_admin) error(403, 'maintenance is for administrators');
};
