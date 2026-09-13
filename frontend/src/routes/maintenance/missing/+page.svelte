<script lang="ts">
	import { resolve } from '$app/paths';
	import { formatNumber } from '$lib/format';
	import { hasMoreIssues, loadMoreIssues, type IssueRun } from '$lib/paging';
	import { m } from '$lib/paraglide/messages.js';
	import { locale } from '$lib/stores/locale.svelte';
	import { attention } from '$lib/stores/attention.svelte';
	import Button from '$lib/components/ui/Button.svelte';
	import ErrorState from '$lib/components/ui/ErrorState.svelte';
	import Icon from '$lib/components/ui/Icon.svelte';
	import MissingRow from '$lib/components/maintenance/MissingRow.svelte';
	import SectionHeader from '$lib/components/ui/SectionHeader.svelte';
	import Skeleton from '$lib/components/ui/Skeleton.svelte';
	import type { PageProps } from './$types';

	let { data }: PageProps = $props();

	let run = $state<IssueRun | null>(null);
	let graceDays = $state<number | null>(null);
	let error = $state<unknown>(null);
	let busy = $state(false);

	// `graceDays` and the first page settle together, so the "forgotten in N
	// days" figure below is never shown against the wrong grace period —
	// exactly why the overview waits on both before rendering either.
	$effect(() => {
		let live = true;
		Promise.all([data.missing, data.summary])
			.then(([missing, summary]) => {
				if (!live) return;
				run = missing;
				graceDays = summary.missing_grace_days;
			})
			.catch((reason) => {
				if (live) error = reason;
			});
		return () => {
			live = false;
		};
	});

	// The page's own summary sets the shared badge, exactly as the overview does.
	$effect(() => {
		let live = true;
		data.summary
			.then((summary) => {
				if (live) attention.set(summary.attention);
			})
			.catch(() => {});
		return () => {
			live = false;
		};
	});

	async function loadMore() {
		if (busy || run === null) return;
		busy = true;
		try {
			run = await loadMoreIssues({ missing: true }, run);
		} finally {
			busy = false;
		}
	}
</script>

<svelte:head>
	<title>{m.maintenance_missing_heading()} · {m.maintenance_title()} · {m.app_name()}</title>
</svelte:head>

<a
	class="inline-flex w-fit items-center gap-1.5 text-[13px] font-medium text-muted transition-colors hover:text-ink"
	href={resolve('/maintenance')}
>
	<Icon name="left" size={14} />
	{m.maintenance_back()}
</a>

<section class="grid gap-4">
	{#if error !== null}
		<ErrorState {error} />
	{:else if run === null}
		<SectionHeader heading={m.maintenance_missing_heading()} />
		<Skeleton width="100%" height="88px" />
	{:else}
		<SectionHeader
			heading={m.maintenance_missing_heading()}
			count={formatNumber(run.total, locale.intl)}
		/>
		<p class="text-[13px] text-muted">{m.maintenance_missing_note()}</p>
		{#if run.items.length === 0}
			<p class="text-muted">{m.maintenance_missing_empty()}</p>
		{:else}
			<ul class="grid gap-3">
				{#each run.items as issue (issue.id)}
					<MissingRow {issue} graceDays={graceDays ?? 0} />
				{/each}
			</ul>
			{#if hasMoreIssues(run)}
				<div class="flex flex-wrap items-center gap-3">
					<Button variant="secondary" onclick={loadMore} disabled={busy}>{m.load_more()}</Button>
					<span class="tabular text-[13px] text-muted">
						{m.shown_of({
							shown: formatNumber(run.items.length, locale.intl),
							total: formatNumber(run.total, locale.intl)
						})}
					</span>
				</div>
			{/if}
		{/if}
	{/if}
</section>
