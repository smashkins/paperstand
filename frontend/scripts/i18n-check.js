/**
 * Two rules about UI strings, enforced cheaply.
 *
 * 1. The catalogues agree: `en.json` and `it.json` carry exactly the same keys
 *    and the same placeholders, so a string added in one language is never
 *    missing in the other.
 * 2. No hardcoded English in the markup: a text node in a `.svelte` file that
 *    is three letters or more has to come from `messages/`.
 *
 * The second rule is a heuristic, not a parser. When it is wrong,
 * `<!-- i18n-check-ignore -->` on the line before silences it — and that
 * comment is meant to be rare enough to be worth reading.
 *
 * The rules themselves live in `i18n-rules.js`, where they are unit tested;
 * this file only walks the tree and prints.
 */

import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join, relative } from 'node:path';
import { fileURLToPath } from 'node:url';

import { compareCatalogues, findHardcodedText } from './i18n-rules.js';

const root = fileURLToPath(new URL('..', import.meta.url));
const messages = join(root, 'messages');
const source = join(root, 'src');

function walk(directory, out = []) {
	for (const entry of readdirSync(directory)) {
		const path = join(directory, entry);
		if (statSync(path).isDirectory()) {
			// The compiled catalogue is generated: it is full of English by design.
			if (entry === 'paraglide') continue;
			walk(path, out);
		} else if (entry.endsWith('.svelte')) {
			out.push(path);
		}
	}
	return out;
}

function checkCatalogues() {
	const en = JSON.parse(readFileSync(join(messages, 'en.json'), 'utf8'));
	const it = JSON.parse(readFileSync(join(messages, 'it.json'), 'utf8'));
	return compareCatalogues(en, it);
}

function checkMarkup() {
	const problems = [];
	for (const file of walk(source)) {
		for (const { line, text } of findHardcodedText(readFileSync(file, 'utf8'))) {
			problems.push(`${relative(root, file)}:${line} hardcoded text: ${text}`);
		}
	}
	return problems;
}

const problems = [...checkCatalogues(), ...checkMarkup()];

if (problems.length > 0) {
	for (const problem of problems) console.error(problem);
	console.error(`\ni18n:check found ${problems.length} problem(s).`);
	process.exit(1);
}

console.log('i18n:check: catalogues agree and no hardcoded UI text found.');
