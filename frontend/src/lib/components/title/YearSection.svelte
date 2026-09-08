<script lang="ts">
	import { formatNumber, pluralise } from '$lib/format';
	import { hasMoreIssues, loadMoreIssues, shouldFetch, type IssueRun } from '$lib/paging';
	import { m } from '$lib/paraglide/messages.js';
	import { locale } from '$lib/stores/locale.svelte';
	import CoverGrid from '$lib/components/covers/CoverGrid.svelte';
	import GridSkeleton from '$lib/components/covers/GridSkeleton.svelte';
	import IssueCard from '$lib/components/covers/IssueCard.svelte';
	import Button from '$lib/components/ui/Button.svelte';
	import Icon from '$lib/components/ui/Icon.svelte';
	import InlineError from '$lib/components/ui/InlineError.svelte';

	/**
	 * One year of a magazine, fetched the first time it is opened.
	 *
	 * A title with fifteen years of back issues is fifteen requests if they all
	 * load at once, and fourteen of them for covers nobody is looking at.
	 *
	 * A failure stops the section instead of retrying it: the effect is gated on
	 * the request key *and* on `error`, and only the retry button clears that
	 * error. Without the gate a fast 5xx would be answered with an unbounded
	 * stream of identical requests.
	 */
	interface Props {
		titleId: string;
		year: number;
		count: number;
		open?: boolean;
	}

	let { titleId, year, count, open = false }: Props = $props();

	/** Null until somebody actually clicks: before that the caller decides. */
	let toggled = $state<boolean | null>(null);
	const expanded = $derived(toggled ?? open);

	let run = $state<IssueRun | null>(null);
	let loadedKey = $state<string | null>(null);
	let error = $state<unknown>(null);
	let busy = $state(false);
	/** Bumped by the retry button; the only thing that revives a failed section. */
	let attempt = $state(0);

	const key = $derived(`${titleId}:${year}:${attempt}`);

	/**
	 * The request in flight, as a plain variable.
	 *
	 * Deliberately not `$state`: the effect reads it to avoid starting a second
	 * request, and a reactive read would make finishing one schedule another —
	 * which is exactly the loop this component used to have.
	 */
	let inFlight: string | null = null;

	async function fetchPage(requestKey: string, more: boolean) {
		inFlight = requestKey;
		busy = true;
		try {
			const next = await loadMoreIssues(
				{ title: titleId, year, sort: 'date_desc' },
				more ? run : null
			);
			// A key that moved while we waited means this answer is stale.
			if (inFlight !== requestKey) return;
			run = next;
			loadedKey = requestKey;
			error = null;
		} catch (reason) {
			if (inFlight !== requestKey) return;
			error = reason;
		} finally {
			if (inFlight === requestKey) {
				inFlight = null;
				busy = false;
			}
		}
	}

	$effect(() => {
		if (!expanded) return;
		const requestKey = key;
		// Already in hand, already failed, or already on its way: do nothing.
		if (!shouldFetch(requestKey, loadedKey, error, inFlight)) return;
		void fetchPage(requestKey, false);
	});

	function retry() {
		error = null;
		attempt += 1;
	}

	function loadMore() {
		if (busy || error !== null) return;
		void fetchPage(key, true);
	}

	const label = $derived(pluralise(count, locale.intl, m.issue_count_one, m.issue_count_other));
	const more = $derived(hasMoreIssues(run));
</script>

<section class="border-t border-hairline pt-5">
	<h3>
		<button
			type="button"
			class="flex w-full items-baseline gap-3.5 text-left"
			aria-expanded={expanded}
			onclick={() => (toggled = !expanded)}
		>
			<span class="tabular opsz-title font-serif text-2xl font-semibold">{year}</span>
			<span class="tabular text-muted">{label}</span>
			<span class="ml-auto self-center text-muted">
				<Icon name={expanded ? 'left' : 'right'} size={14} class={expanded ? 'rotate-90' : ''} />
			</span>
		</button>
	</h3>

	{#if expanded}
		<div class="mt-4 grid gap-4">
			{#if run !== null && run.items.length > 0}
				<CoverGrid>
					{#each run.items as issue (issue.id)}
						<IssueCard {issue} showChips />
					{/each}
				</CoverGrid>
			{:else if run !== null}
				<!-- The year's count comes from the calendar and its issues from a
				     second request; a rescan between the two leaves the header
				     promising issues the grid cannot show. Say so, rather than
				     opening an empty grid under a filled-in count. -->
				<p class="text-muted">{m.title_no_issues()}</p>
			{:else if error === null}
				<GridSkeleton count={Math.min(count, 8)} heading={false} />
			{/if}

			{#if error !== null}
				<InlineError {error} onretry={retry} />
			{:else if more}
				<div class="flex flex-wrap items-center gap-3">
					<Button variant="secondary" onclick={loadMore} disabled={busy}>{m.load_more()}</Button>
					<span class="tabular text-[13px] text-muted">
						{m.shown_of({
							shown: formatNumber(run?.items.length ?? 0, locale.intl),
							total: formatNumber(run?.total ?? 0, locale.intl)
						})}
					</span>
				</div>
			{/if}
		</div>
	{/if}
</section>
