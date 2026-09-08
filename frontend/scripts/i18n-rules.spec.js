import { describe, expect, it } from 'vitest';
import { compareCatalogues, findHardcodedText, markerLines, stripNonMarkup } from './i18n-rules.js';

describe('findHardcodedText', () => {
	it('finds prose sitting in the markup', () => {
		const found = findHardcodedText('<p>Hello there</p>\n');
		expect(found).toEqual([{ line: 1, text: 'Hello there' }]);
	});

	it('leaves a message call alone', () => {
		expect(findHardcodedText('<p>{m.today_badge()}</p>\n')).toEqual([]);
	});

	it('ignores script, style and comments', () => {
		const source = [
			'<script lang="ts">',
			"	const label = 'Hello there';",
			'</script>',
			'<style>',
			'	.a::after { content: "Hello there"; }',
			'</style>',
			'<!-- Hello there -->',
			'<p>{m.today_badge()}</p>'
		].join('\n');
		expect(findHardcodedText(source)).toEqual([]);
	});

	it('is silenced by the marker on the line before', () => {
		const source = ['<!-- i18n-check-ignore -->', '<p>Hello there</p>'].join('\n');
		expect(findHardcodedText(source)).toEqual([]);
	});

	it('still reports the same snippet without the marker', () => {
		const source = ['<!-- an ordinary comment -->', '<p>Hello there</p>'].join('\n');
		expect(findHardcodedText(source)).toEqual([{ line: 2, text: 'Hello there' }]);
	});

	it('silences only the line the marker precedes', () => {
		const source = [
			'<!-- i18n-check-ignore -->',
			'<p>Hello there</p>',
			'<p>Another sentence</p>'
		].join('\n');
		expect(findHardcodedText(source)).toEqual([{ line: 3, text: 'Another sentence' }]);
	});

	it('accepts a marker on the last line of a multi-line comment', () => {
		const source = ['<!--', '     i18n-check-ignore -->', '<p>Hello there</p>'].join('\n');
		expect(findHardcodedText(source)).toEqual([]);
	});

	it('lets punctuation and the wordmark through', () => {
		expect(findHardcodedText('<span>·</span>\n<span>Paper</span><span>stand</span>')).toEqual([]);
	});
});

describe('markerLines', () => {
	it('reads the marker from the source, before the comment is blanked', () => {
		const source = ['<!-- i18n-check-ignore -->', '<p>x</p>'].join('\n');
		expect([...markerLines(source)]).toEqual([0]);
		// The comment is gone by the time the markup is scanned, which is exactly
		// why the positions have to be taken first.
		expect(stripNonMarkup(source).split('\n')[0].trim()).toBe('');
	});
});

describe('compareCatalogues', () => {
	it('is happy when the two agree', () => {
		expect(compareCatalogues({ $schema: 'x', a: 'A {n}' }, { $schema: 'x', a: 'A {n}' })).toEqual(
			[]
		);
	});

	it('names a key missing from either side', () => {
		expect(compareCatalogues({ a: 'A', b: 'B' }, { a: 'A' })).toEqual([
			'messages/it.json is missing "b"'
		]);
		expect(compareCatalogues({ a: 'A' }, { a: 'A', b: 'B' })).toEqual([
			'messages/en.json is missing "b"'
		]);
	});

	it('catches placeholders that have drifted apart', () => {
		expect(compareCatalogues({ a: '{one} {two}' }, { a: '{one}' })).toEqual([
			'"a" does not use the same placeholders in both languages'
		]);
	});

	it('does not care what order the placeholders appear in', () => {
		expect(compareCatalogues({ a: '{one} {two}' }, { a: '{two} · {one}' })).toEqual([]);
	});
});
