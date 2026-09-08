import { api } from '$lib/api/client';
import type { PageLoad } from './$types';

/** Every magazine title, `Unsorted` included so it can be shown and badged. */
export const load: PageLoad = ({ fetch }) => {
	return {
		titles: api.titles({ kind: 'magazine', sort: 'name', include_unsorted: true }, { fetch })
	};
};
