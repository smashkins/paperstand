/**
 * The two rules `i18n-check` enforces, as pure functions over text.
 *
 * Separated from the command so they can be tested: the marker bug this file
 * was created to fix — the suppression comment being blanked out before
 * anything looked for it — was invisible precisely because nothing exercised
 * it.
 */

/** The comment that silences the hardcoded-text rule for the next line. */
export const IGNORE_MARKER = 'i18n-check-ignore';

/** Text that is not prose: punctuation, the wordmark, locale codes. */
const ALLOWED = new Set(['Paper', 'stand', 'Paperstand', 'en', 'it']);

/** Everything outside `<script>`, `<style>` and comments, blanked out in place. */
export function stripNonMarkup(text) {
	return text
		.replace(/<script[\s\S]*?<\/script>/g, (block) => block.replace(/[^\n]/g, ' '))
		.replace(/<style[\s\S]*?<\/style>/g, (block) => block.replace(/[^\n]/g, ' '))
		.replace(/<!--[\s\S]*?-->/g, (block) => block.replace(/[^\n]/g, ' '));
}

/**
 * The 0-based lines a suppression marker sits on.
 *
 * Read from the original text, before `stripNonMarkup` blanks the comment the
 * marker lives in. A marker spanning several lines suppresses from its last
 * line, which is the one immediately above the text it is about.
 */
export function markerLines(text) {
	const lines = text.split('\n');
	const marked = new Set();
	lines.forEach((line, index) => {
		if (line.includes(IGNORE_MARKER)) marked.add(index);
	});
	return marked;
}

/**
 * Hardcoded UI text in one `.svelte` file, as `{ line, text }` records.
 *
 * A heuristic, not a parser: it looks at what sits between a `>` and the next
 * `<`, outside script, style and comments, and reports anything with three
 * letters or more that is not on the allow list.
 */
export function findHardcodedText(source) {
	const marked = markerLines(source);
	const lines = stripNonMarkup(source).split('\n');
	const found = [];
	lines.forEach((line, index) => {
		if (marked.has(index - 1)) return;
		for (const match of line.matchAll(/>([^<>{}]+)</g)) {
			const text = match[1].trim();
			if (text.length < 3 || ALLOWED.has(text)) continue;
			if (!/[A-Za-z]{3,}/.test(text)) continue;
			found.push({ line: index + 1, text });
		}
	});
	return found;
}

/** Placeholders a message uses, sorted, so two languages can be compared. */
function placeholders(value) {
	return (String(value).match(/\{[a-z_]+\}/g) ?? []).sort().join(',');
}

/** Where two catalogues disagree, as a list of sentences. */
export function compareCatalogues(en, it) {
	const keys = (catalogue) => new Set(Object.keys(catalogue).filter((key) => key !== '$schema'));
	const inEn = keys(en);
	const inIt = keys(it);
	const problems = [];
	for (const key of inEn) if (!inIt.has(key)) problems.push(`messages/it.json is missing "${key}"`);
	for (const key of inIt) if (!inEn.has(key)) problems.push(`messages/en.json is missing "${key}"`);
	for (const key of inEn) {
		if (!inIt.has(key)) continue;
		if (placeholders(en[key]) !== placeholders(it[key])) {
			problems.push(`"${key}" does not use the same placeholders in both languages`);
		}
	}
	return problems;
}
