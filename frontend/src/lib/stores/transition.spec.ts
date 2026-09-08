import { describe, expect, it } from 'vitest';
import { coverToken, coverTransitionName, transitionNameFor } from './transition.svelte';

describe('coverTransitionName', () => {
	it('is a valid custom identifier', () => {
		expect(coverTransitionName('3195558528f6ba6f')).toBe('cover-3195558528f6ba6f');
	});

	it('drops anything a CSS identifier cannot carry', () => {
		expect(coverTransitionName('a/b c.d')).toBe('cover-abcd');
	});
});

describe('coverToken', () => {
	it('never hands out the same token twice', () => {
		const tokens = Array.from({ length: 50 }, () => coverToken('card'));
		expect(new Set(tokens).size).toBe(50);
	});

	it('carries the prefix it was given', () => {
		expect(coverToken('header')).toMatch(/^header-\d+$/);
	});
});

describe('transitionNameFor', () => {
	const issueId = 'abc123';

	it('names only the instance that claimed it', () => {
		const shelf = coverToken('card');
		const recent = coverToken('card');
		expect(transitionNameFor(shelf, shelf, issueId)).toBe('cover-abc123');
		expect(transitionNameFor(shelf, recent, issueId)).toBeUndefined();
	});

	it('gives the same issue on two shelves exactly one name', () => {
		// The bug this replaced: claiming by issue id named both copies, and a
		// duplicate `view-transition-name` makes the browser skip the transition.
		const onToday = coverToken('card');
		const onRecentlyAdded = coverToken('card');
		const claimed = onRecentlyAdded;
		const names = [onToday, onRecentlyAdded]
			.map((token) => transitionNameFor(claimed, token, issueId))
			.filter((name) => name !== undefined);
		expect(names).toEqual(['cover-abc123']);
	});

	it('names nothing while no instance has claimed', () => {
		expect(transitionNameFor(null, coverToken('card'), issueId)).toBeUndefined();
	});

	it('lets the far side of the navigation arrive at the same name', () => {
		// The reader stub sets the name unconditionally, so the two halves have to
		// agree on the string even though the claim is per instance.
		const token = coverToken('card');
		expect(transitionNameFor(token, token, issueId)).toBe(coverTransitionName(issueId));
	});
});
