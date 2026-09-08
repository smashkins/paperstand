import { api } from '$lib/api/client';
import type { PageLoad } from './$types';

/** Every newspaper title, `Unsorted` included so it can be shown and badged. */
export const load: PageLoad = ({ fetch }) => {
	return {
		titles: api.titles({ kind: 'newspaper', sort: 'name', include_unsorted: true }, { fetch })
	};
};
