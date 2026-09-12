/**
 * How many items the maintenance view has for a person to look at.
 *
 * `null` until the first `GET /api/maintenance` answers — the top bar shows
 * nothing rather than a wrong zero in the meantime — refreshed once per page
 * load by `TopBar`, again by the maintenance page from its own summary, and
 * again by Settings once a running scan finishes.
 */

import { api } from '$lib/api/client';

class AttentionStore {
	#count = $state<number | null>(null);

	get count(): number | null {
		return this.#count;
	}

	/** Ask the server how many items need attention right now. */
	async refresh(): Promise<void> {
		try {
			const summary = await api.maintenance();
			this.#count = summary.attention;
		} catch {
			// A blip is not worth surfacing on a badge; it keeps its last value.
		}
	}

	/** Set directly from a summary a caller already fetched. */
	set(count: number): void {
		this.#count = count;
	}
}

export const attention = new AttentionStore();
