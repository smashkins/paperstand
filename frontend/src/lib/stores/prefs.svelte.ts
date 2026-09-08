/**
 * Reader preferences, chosen in Settings and read by the reader.
 *
 * They are stored per browser, in `localStorage`, and nothing else depends on
 * them yet: the settings page writes them so the reader can be built against a
 * preference that already exists rather than inventing one.
 *
 * As everywhere else, storage access is guarded: `localStorage` throws — not
 * returns `null` — in a browser that blocks site data.
 */

/** How two pages sit next to each other. */
export const SPREAD_MODES = ['auto', 'single', 'double'] as const;
export type SpreadMode = (typeof SPREAD_MODES)[number];

/** Where the pixels of a page come from. */
export const IMAGE_MODES = ['client', 'server'] as const;
export type ImageMode = (typeof IMAGE_MODES)[number];

export interface Prefs {
	spread: SpreadMode;
	images: ImageMode;
}

export const DEFAULT_PREFS: Prefs = { spread: 'auto', images: 'client' };

const STORAGE_KEY = 'paperstand:prefs';

function isSpread(value: unknown): value is SpreadMode {
	return typeof value === 'string' && (SPREAD_MODES as readonly string[]).includes(value);
}

function isImages(value: unknown): value is ImageMode {
	return typeof value === 'string' && (IMAGE_MODES as readonly string[]).includes(value);
}

/** Read a stored blob, keeping only the fields that still make sense. */
export function parsePrefs(raw: string | null): Prefs {
	if (!raw) return { ...DEFAULT_PREFS };
	let parsed: unknown;
	try {
		parsed = JSON.parse(raw);
	} catch {
		return { ...DEFAULT_PREFS };
	}
	if (typeof parsed !== 'object' || parsed === null) return { ...DEFAULT_PREFS };
	const value = parsed as Partial<Record<keyof Prefs, unknown>>;
	return {
		spread: isSpread(value.spread) ? value.spread : DEFAULT_PREFS.spread,
		images: isImages(value.images) ? value.images : DEFAULT_PREFS.images
	};
}

class PrefsStore {
	#prefs = $state<Prefs>({ ...DEFAULT_PREFS });

	get spread(): SpreadMode {
		return this.#prefs.spread;
	}

	get images(): ImageMode {
		return this.#prefs.images;
	}

	/** Load what the browser remembers. Safe to call more than once. */
	init(): void {
		let raw: string | null = null;
		try {
			raw = localStorage.getItem(STORAGE_KEY);
		} catch {
			// Storage unavailable: the defaults stand for this session.
		}
		this.#prefs = parsePrefs(raw);
	}

	set<K extends keyof Prefs>(key: K, value: Prefs[K]): void {
		this.#prefs = { ...this.#prefs, [key]: value };
		try {
			localStorage.setItem(STORAGE_KEY, JSON.stringify(this.#prefs));
		} catch {
			// The choice simply does not outlive the tab.
		}
	}
}

export const prefs = new PrefsStore();
