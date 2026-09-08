import { api, quiet } from '$lib/api/client';
import type { PageLoad } from './$types';

/**
 * A title, its recent issues and — for a daily — a year of its calendar.
 *
 * The two follow-ups are chained off the title rather than awaited, so the page
 * can start drawing its header as soon as the first response lands. `quiet`
 * keeps a failed title from also showing up as an unhandled rejection through
 * the branches nothing awaits.
 */
export const load: PageLoad = ({ fetch, params }) => {
	const title = api.title(params.id, { fetch });
	return {
		title: quiet(title),
		recent: quiet(
			title.then((detail) =>
				api.issues({ title: detail.id, sort: 'date_desc', limit: 24 }, { fetch })
			)
		),
		calendar: quiet(
			title.then((detail) =>
				detail.kind === 'newspaper' ? api.calendar(detail.id, undefined, { fetch }) : null
			)
		)
	};
};
