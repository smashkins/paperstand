// Registering the locale strategy is a side effect of importing this module,
// and `+layout.ts` is evaluated before anything renders, so it happens before
// the first `getLocale()`.
import '$lib/locale';
import { redirect } from '@sveltejs/kit';
import { ApiError, api } from '$lib/api/client';
import { LOGIN_PATH, loginHref } from '$lib/login';
import { session } from '$lib/stores/session.svelte';
import type { LayoutLoad } from './$types';

// Paperstand ships as a single page app: the backend serves the built assets and
// falls back to `index.html`, so nothing is rendered or prerendered ahead of time.
export const ssr = false;
export const prerender = false;

/**
 * Who is signed in, asked before any page renders.
 *
 * Signed in, this never reads `url`, so SvelteKit keeps its answer for every
 * navigation after it until something calls `invalidateAll`. Signed out it
 * does read it, which makes it run again on the next navigation — and that is
 * what sends Back, after signing out, to the sign-in page rather than to a
 * page nobody may see any more.
 *
 * A `401` anywhere but on the sign-in page goes there, with the address it
 * came from as `next`. Any other failure is not a verdict on the session: the
 * last known user stays, and the page's own requests say what went wrong.
 */
export const load: LayoutLoad = async ({ fetch, url }) => {
	try {
		const user = await api.me({ fetch });
		session.set(user);
		return { user };
	} catch (error) {
		const pathname = url.pathname;
		if (!(error instanceof ApiError && error.status === 401)) return { user: session.user };
		session.clear();
		if (pathname === LOGIN_PATH) return { user: null };
		redirect(307, loginHref(`${pathname}${url.search}`));
	}
};
