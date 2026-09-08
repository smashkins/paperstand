// Registering the locale strategy is a side effect of importing this module,
// and `+layout.ts` is evaluated before anything renders, so it happens before
// the first `getLocale()`.
import '$lib/locale';

// Paperstand ships as a single page app: the backend serves the built assets and
// falls back to `index.html`, so nothing is rendered or prerendered ahead of time.
export const ssr = false;
export const prerender = false;
