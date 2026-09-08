import { api } from '$lib/api/client';
import type { PageLoad } from './$types';

/**
 * The front page's data.
 *
 * The promise is returned rather than awaited, so the route renders at once and
 * the page shows skeletons in the shape of the shelves that are coming. The
 * `fetch` is SvelteKit's, which is what makes `invalidateAll()` — the retry
 * button — re-run this.
 */
export const load: PageLoad = ({ fetch }) => {
	return { today: api.today(undefined, { fetch }) };
};
