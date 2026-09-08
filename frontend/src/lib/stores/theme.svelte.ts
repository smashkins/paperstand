/**
 * Theme preference: `system`, `light` or `dark`.
 *
 * The choice is persisted in `localStorage`, which can throw in private windows
 * or when site data is blocked, so every access is guarded.
 */

export const THEMES = ['system', 'light', 'dark'] as const;

export type Theme = (typeof THEMES)[number];

const STORAGE_KEY = 'paperstand:theme';

export function isTheme(value: unknown): value is Theme {
	return typeof value === 'string' && (THEMES as readonly string[]).includes(value);
}

function readStored(): Theme {
	try {
		const stored = localStorage.getItem(STORAGE_KEY);
		if (isTheme(stored)) return stored;
	} catch {
		// Storage unavailable: fall back to the system preference.
	}
	return 'system';
}

function prefersDark(): boolean {
	if (typeof window === 'undefined' || !window.matchMedia) return false;
	return window.matchMedia('(prefers-color-scheme: dark)').matches;
}

/** Resolve a preference into the theme that is actually applied. */
export function resolveTheme(theme: Theme, systemDark: boolean): 'light' | 'dark' {
	if (theme === 'system') return systemDark ? 'dark' : 'light';
	return theme;
}

class ThemeStore {
	#theme = $state<Theme>('system');
	#systemDark = $state(false);

	get theme(): Theme {
		return this.#theme;
	}

	get resolved(): 'light' | 'dark' {
		return resolveTheme(this.#theme, this.#systemDark);
	}

	/** Read the stored preference and start following the system setting. */
	init(): () => void {
		this.#theme = readStored();
		this.#systemDark = prefersDark();
		this.apply();

		if (typeof window === 'undefined' || !window.matchMedia) return () => {};
		const query = window.matchMedia('(prefers-color-scheme: dark)');
		const onChange = (event: MediaQueryListEvent) => {
			this.#systemDark = event.matches;
			this.apply();
		};
		query.addEventListener('change', onChange);
		return () => query.removeEventListener('change', onChange);
	}

	set(theme: Theme): void {
		this.#theme = theme;
		try {
			localStorage.setItem(STORAGE_KEY, theme);
		} catch {
			// Preference stays for this session only.
		}
		this.apply();
	}

	/** Cycle system → light → dark → system. */
	cycle(): void {
		const next = THEMES[(THEMES.indexOf(this.#theme) + 1) % THEMES.length];
		this.set(next);
	}

	private apply(): void {
		if (typeof document === 'undefined') return;
		const dark = this.resolved === 'dark';
		document.documentElement.classList.toggle('dark', dark);
		// The browser's own chrome — the address bar on Android, the status bar
		// of an installed app — follows this meta tag and nothing else.
		// `app.html` sets it before the first paint; this keeps it right
		// afterwards. Both values are `--paperstand-bg` from `app.css`.
		document
			.querySelector('meta[name="theme-color"]')
			?.setAttribute('content', dark ? '#121212' : '#f5f1ea');
	}
}

export const theme = new ThemeStore();
