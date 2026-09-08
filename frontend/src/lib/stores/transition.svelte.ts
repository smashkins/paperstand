/**
 * Which cover is carrying the view transition into the reader.
 *
 * `view-transition-name` has to be unique in the rendered document, and the
 * same issue can easily be on two shelves at once — the day's paper is also
 * "recently added". Claiming by *issue id* would hand both copies the same
 * name and the browser would skip the transition entirely, so the claim is on
 * the cover **instance** that was activated: one element, one name.
 *
 * The name itself still derives from the issue id, because the page on the far
 * side of the navigation has to arrive at the same string.
 */

/** Turn an issue id into something `view-transition-name` will accept. */
export function coverTransitionName(id: string): string {
	return `cover-${id.replace(/[^A-Za-z0-9_-]/g, '')}`;
}

/** Identifies one rendered cover. Unique for the life of the tab. */
export type CoverToken = string;

let counter = 0;

/** A fresh token for a cover that has no natural identity of its own. */
export function coverToken(prefix = 'cover'): CoverToken {
	counter += 1;
	return `${prefix}-${counter}`;
}

/**
 * Decide the name one cover instance should render with.
 *
 * Split out from the store so it can be tested without a reactive context.
 */
export function transitionNameFor(
	claimed: CoverToken | null,
	token: CoverToken,
	issueId: string
): string | undefined {
	return claimed !== null && claimed === token ? coverTransitionName(issueId) : undefined;
}

class CoverTransition {
	#claimed = $state<CoverToken | null>(null);

	/** The cover instance that currently owns the transition name. */
	get claimed(): CoverToken | null {
		return this.#claimed;
	}

	/** The name this cover instance should render with, if any. */
	nameFor(token: CoverToken, issueId: string): string | undefined {
		return transitionNameFor(this.#claimed, token, issueId);
	}

	/** Called by the cover that was activated, just before it navigates. */
	claim(token: CoverToken): void {
		this.#claimed = token;
	}

	release(): void {
		this.#claimed = null;
	}
}

export const coverTransition = new CoverTransition();
