import { api, quiet } from '$lib/api/client';
import type { PageLoad } from './$types';

/**
 * What Settings shows: the libraries, what they cost on disk, the scanner and,
 * for an administrator, the accounts.
 *
 * The scan status says what the scheduler is doing right now; `/api/health`
 * carries the only copy of *when* the last scan finished, which is why both are
 * asked for. Those three go out at once; only the accounts wait to hear who is
 * asking, since anybody but an administrator would get a `403` for them.
 */
export const load: PageLoad = async ({ fetch, parent }) => {
	const stats = quiet(api.stats({ fetch }));
	const scan = quiet(api.scanStatus({ fetch }));
	const health = quiet(api.health({ fetch }));
	const { user } = await parent();
	return {
		stats,
		scan,
		health,
		users: user?.is_admin ? quiet(api.users({ fetch })) : null
	};
};
