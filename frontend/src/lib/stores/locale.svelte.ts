/**
 * The active locale, as something Svelte can react to.
 *
 * Paraglide's messages are plain functions that read the locale when they are
 * called, so switching language changes nothing a component has already
 * rendered. The store keeps the current locale in `$state`; the root layout
 * keys the whole application on it, so a switch re-renders everything without
 * the full page reload Paraglide's `setLocale` does by default.
 */

import { getLocale, setLocale as setParaglideLocale, type Locale } from '$lib/paraglide/runtime';

/** The BCP 47 tag `Intl` should use for each of our locales. */
const INTL_TAGS: Record<string, string> = { en: 'en-GB', it: 'it-IT' };

class LocaleStore {
	#current = $state<Locale>('en');

	/** The active Paraglide locale. */
	get current(): Locale {
		return this.#current;
	}

	/** The tag to hand `Intl`, which wants a region for sensible date order. */
	get intl(): string {
		return INTL_TAGS[this.#current] ?? this.#current;
	}

	/** Adopt whatever the Paraglide strategy chain resolved on boot. */
	init(): void {
		this.#current = getLocale();
		this.applyLang();
	}

	/** Switch language without reloading the document. */
	set(locale: Locale): void {
		if (locale === this.#current) return;
		// `reload: false` is the browser-only escape hatch: nothing about our
		// URLs carries the locale, so there is no navigation to perform.
		void setParaglideLocale(locale, { reload: false });
		this.#current = locale;
		this.applyLang();
	}

	private applyLang(): void {
		if (typeof document === 'undefined') return;
		document.documentElement.lang = this.#current;
	}
}

export const locale = new LocaleStore();
