import { redirect } from '@sveltejs/kit';
import { safeNext } from '$lib/login';
import type { PageLoad } from './$types';

/**
 * Where to go once signed in — and, when the browser already is, straight
 * there, so a bookmarked sign-in page is never a dead end.
 */
export const load: PageLoad = async ({ parent, url }) => {
	const next = safeNext(url.searchParams.get('next'));
	const { user } = await parent();
	if (user) redirect(307, next);
	return { next };
};
