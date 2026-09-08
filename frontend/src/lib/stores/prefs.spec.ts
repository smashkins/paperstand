import { describe, expect, it } from 'vitest';
import { DEFAULT_PREFS, parsePrefs } from './prefs.svelte';

describe('parsePrefs', () => {
	it('reads back what was written', () => {
		expect(parsePrefs('{"spread":"double","images":"server"}')).toEqual({
			spread: 'double',
			images: 'server'
		});
	});

	it('falls back for anything it does not recognise', () => {
		expect(parsePrefs(null)).toEqual(DEFAULT_PREFS);
		expect(parsePrefs('not json')).toEqual(DEFAULT_PREFS);
		expect(parsePrefs('"a string"')).toEqual(DEFAULT_PREFS);
		expect(parsePrefs('{"spread":"triple"}')).toEqual(DEFAULT_PREFS);
	});

	it('keeps the half of a stored blob that still makes sense', () => {
		expect(parsePrefs('{"spread":"single","images":"telepathy"}')).toEqual({
			spread: 'single',
			images: DEFAULT_PREFS.images
		});
	});
});
