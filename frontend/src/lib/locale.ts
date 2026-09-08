/**
 * Locale persistence that survives a browser with storage switched off.
 *
 * Paraglide's built-in `localStorage` strategy touches storage directly, and
 * `localStorage` throws — not returns `null` — in a browser that blocks site
 * data. That would take the whole SPA down on the first `getLocale()`. This
 * custom strategy wraps both ends in a `try`/`catch` and reports "no stored
 * locale" on failure, so the chain falls through to `preferredLanguage` and
 * then to `baseLocale`.
 *
 * Importing this module registers the strategy; it has to happen before the
 * first `getLocale()` call, which is why `+layout.ts` imports it.
 */

import { defineCustomClientStrategy, locales, localStorageKey } from '$lib/paraglide/runtime';

/** Name of the strategy, as listed in `vite.config.ts` and in `package.json`. */
export const LOCALE_STRATEGY = 'custom-localStorage';

/** Storage key. Paraglide's own, so an already stored preference is kept. */
export const LOCALE_STORAGE_KEY = localStorageKey;

function isKnownLocale(value: string | null): value is string {
	return value !== null && (locales as readonly string[]).includes(value);
}

defineCustomClientStrategy(LOCALE_STRATEGY, {
	getLocale: () => {
		try {
			const stored = localStorage.getItem(LOCALE_STORAGE_KEY);
			// An unknown value would make Paraglide throw on assertion, so a
			// stale or hand-edited entry is treated as absent.
			return isKnownLocale(stored) ? stored : undefined;
		} catch {
			return undefined;
		}
	},
	setLocale: (locale: string) => {
		try {
			localStorage.setItem(LOCALE_STORAGE_KEY, locale);
		} catch {
			// Storage is unavailable: the choice simply does not outlive the tab.
		}
	}
});
