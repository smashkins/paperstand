/**
 * Walking `/api/issues` one page at a time.
 *
 * The endpoint caps a page at 200 issues, and a magazine with a weekly run has
 * years that exceed that. The logic lives here rather than in the component so
 * it can be tested against a mocked `fetch`: getting the offset wrong is the
 * kind of bug that only shows up on somebody else's very large library.
 */

import { api, type CallOptions, type Issue, type IssueParams } from './api/client';

/** The largest page `/api/issues` will hand out. */
export const ISSUE_PAGE_SIZE = 200;

/**
 * Whether a section should start a request for `key` right now.
 *
 * The rule the `YearSection` effect runs on, kept here because getting it wrong
 * once cost an unbounded stream of requests: a failed year used to leave
 * `items` null and flip `loading` back, which re-triggered the effect, which
 * asked again, for as long as the server kept failing fast.
 *
 * A failure therefore blocks everything until `error` is cleared, and clearing
 * it is something only an explicit retry does — and a retry changes `key`, so
 * the same answer is not mistaken for a fresh one.
 */
export function shouldFetch(
	key: string,
	loadedKey: string | null,
	error: unknown,
	inFlight: string | null
): boolean {
	if (loadedKey === key) return false;
	if (error !== null && error !== undefined) return false;
	return inFlight !== key;
}

/** What has been loaded so far, and how many there are in total. */
export interface IssueRun {
	items: Issue[];
	total: number;
}

/** Append `incoming`, skipping anything already in hand. */
export function mergeIssues(current: Issue[], incoming: Issue[]): Issue[] {
	const seen = new Set(current.map((issue) => issue.id));
	const merged = [...current];
	for (const issue of incoming) {
		if (seen.has(issue.id)) continue;
		seen.add(issue.id);
		merged.push(issue);
	}
	return merged;
}

/** True when the catalogue holds issues past the ones already loaded. */
export function hasMoreIssues(run: IssueRun | null): boolean {
	return run !== null && run.items.length < run.total;
}

/**
 * Fetch the page that follows `run` and append it.
 *
 * A page that adds nothing new ends the run whatever `total` claims: a stale or
 * inconsistent total would otherwise leave a "load more" button that can never
 * be satisfied.
 */
export async function loadMoreIssues(
	params: IssueParams,
	run: IssueRun | null,
	options?: CallOptions
): Promise<IssueRun> {
	const before = run?.items ?? [];
	const page = await api.issues(
		{ ...params, limit: ISSUE_PAGE_SIZE, offset: before.length },
		options
	);
	const items = mergeIssues(before, page.items);
	return { items, total: items.length === before.length ? items.length : page.total };
}
