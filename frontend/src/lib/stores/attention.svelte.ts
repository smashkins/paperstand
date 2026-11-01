/**
 * How many items the maintenance view has for a person to look at.
 *
 * `null` until the first `GET /api/maintenance` answers — the top bar shows
 * nothing rather than a wrong zero in the meantime — refreshed once per page
 * load by `TopBar`, again by the maintenance page from its own summary, and
 * again by Settings once a running scan finishes.
 *
 * Maintenance is an administrator's: for anybody else the server answers
 * `/api/maintenance` with a `403`, so the store does not ask, and holds
 * `null` — no badge — instead.
 */

import { api } from '$lib/api/client';
import { session } from './session.svelte';

class AttentionStore {
	#count = $state<number | null>(null);

	get count(): number | null {
		return this.#count;
	}

	/** Ask the server how many items need attention right now. */
	async refresh(): Promise<void> {
		// Read before the first `await`, so an effect that calls this runs again
		// when somebody else signs in.
		if (!session.isAdmin) {
			this.#count = null;
			return;
		}
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
