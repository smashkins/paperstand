/**
 * The way in: where the sign-in page lives, and where it may send you after.
 *
 * `next` arrives in the query string, so it is whatever anybody put in a link.
 * Following it blindly would make the sign-in page an open redirect — sign in
 * here, land on a look-alike somewhere else — so only a path on this origin
 * survives, and everything else becomes the front page.
 */

/** The sign-in route. */
export const LOGIN_PATH = '/login';

/** Any origin will do, as long as it is one no real address can have. */
const PROBE_ORIGIN = 'http://paperstand.invalid';

/**
 * True for the characters a browser drops from an address before it parses
 * it: tabs and newlines, which would turn `/<tab>/evil` into `//evil`. The rest
 * of the C0 range and DEL are refused with them, as nothing legitimate has them.
 */
function hasControlCharacter(value: string): boolean {
	for (let index = 0; index < value.length; index += 1) {
		const code = value.charCodeAt(index);
		if (code < 0x20 || code === 0x7f) return true;
	}
	return false;
}

/**
 * The path `next` names, when it is a path on this origin; `/` otherwise.
 *
 * It must start with a single `/`: `//host` is another origin to a browser,
 * and anything with a scheme — `https:`, `javascript:` — does not start with a
 * slash at all. A backslash is refused outright, because the URL parser reads
 * it as a slash (`/\host` is `//host`). What is left is still parsed, and must
 * come back on the same origin. The sign-in page itself is no destination, or
 * a signed-in visit to it would redirect to itself forever.
 */
export function safeNext(next: string | null | undefined): string {
	if (!next || !next.startsWith('/') || next.startsWith('//')) return '/';
	if (next.includes('\\') || hasControlCharacter(next)) return '/';
	let url: URL;
	try {
		url = new URL(next, PROBE_ORIGIN);
	} catch {
		return '/';
	}
	if (url.origin !== PROBE_ORIGIN || url.pathname === LOGIN_PATH) return '/';
	return `${url.pathname}${url.search}${url.hash}`;
}

/** The sign-in page, asked to come back to `next` afterwards. */
export function loginHref(next: string): string {
	const target = safeNext(next);
	if (target === '/') return LOGIN_PATH;
	return `${LOGIN_PATH}?${new URLSearchParams({ next: target })}`;
}
