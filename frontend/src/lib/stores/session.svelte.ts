/**
 * Who the browser is signed in as.
 *
 * Set by the root `+layout.ts` from `GET /api/auth/me`, which every navigation
 * after the first reuses until something invalidates it — signing in, signing
 * out, a change to your own account. `null` before that first answer, while
 * signed out, and when the server could not be asked at all.
 *
 * The session itself is the server's cookie, which this never sees: the token
 * the sign-in answers with is not kept anywhere, so there is nothing here for a
 * script to steal.
 */

import type { User } from '$lib/api/client';

class SessionStore {
	#user = $state<User | null>(null);

	get user(): User | null {
		return this.#user;
	}

	/** True for an administrator: scanning, maintenance and the accounts. */
	get isAdmin(): boolean {
		return this.#user?.is_admin === true;
	}

	set(user: User | null): void {
		this.#user = user;
	}

	clear(): void {
		this.#user = null;
	}
}

export const session = new SessionStore();
