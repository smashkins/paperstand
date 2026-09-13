<script lang="ts">
	import { resolve } from '$app/paths';
	import { formatNumber } from '$lib/format';
	import { hasMoreIssues, loadMoreIssues, type IssueRun } from '$lib/paging';
	import { m } from '$lib/paraglide/messages.js';
	import { locale } from '$lib/stores/locale.svelte';
	import Button from '$lib/components/ui/Button.svelte';
	import ErrorState from '$lib/components/ui/ErrorState.svelte';
	import Icon from '$lib/components/ui/Icon.svelte';
	import InlineError from '$lib/components/ui/InlineError.svelte';
	import SectionHeader from '$lib/components/ui/SectionHeader.svelte';
	import Skeleton from '$lib/components/ui/Skeleton.svelte';
	import UnreadableRow from '$lib/components/maintenance/UnreadableRow.svelte';
	import type { PageProps } from './$types';

	let { data }: PageProps = $props();

	let run = $state<IssueRun | null>(null);
	let error = $state<unknown>(null);
	let busy = $state(false);
	let loadMoreError = $state<unknown>(null);

	$effect(() => {
		let live = true;
		data.unreadable
			.then((page) => {
				if (!live) return;
				run = page;
				// A successful retry replaces the stale error from the attempt
				// that failed — otherwise the page would keep showing it forever.
				error = null;
			})
			.catch((reason) => {
				if (live) error = reason;
			});
		return () => {
			live = false;
		};
	});

	async function loadMore() {
		if (busy || run === null) return;
		busy = true;
		try {
			run = await loadMoreIssues({ unreadable: true }, run);
			loadMoreError = null;
		} catch (reason) {
			loadMoreError = reason;
		} finally {
			busy = false;
		}
	}
</script>

<svelte:head>
	<title>{m.maintenance_unreadable_heading()} · {m.maintenance_title()} · {m.app_name()}</title>
</svelte:head>

<a
	class="inline-flex w-fit items-center gap-1.5 text-[13px] font-medium text-muted transition-colors hover:text-ink"
	href={resolve('/maintenance')}
>
	<Icon name="left" size={14} />
	{m.maintenance_back()}
</a>

<section class="grid min-w-0 gap-4">
	{#if error !== null}
		<ErrorState {error} />
	{:else if run === null}
		<SectionHeader heading={m.maintenance_unreadable_heading()} />
		<Skeleton width="100%" height="88px" />
	{:else}
		<SectionHeader
			heading={m.maintenance_unreadable_heading()}
			count={formatNumber(run.total, locale.intl)}
		/>
		{#if run.items.length === 0}
			<p class="text-muted">{m.maintenance_unreadable_empty()}</p>
		{:else}
			<ul class="grid gap-3">
				{#each run.items as issue (issue.id)}
					<UnreadableRow {issue} />
				{/each}
			</ul>
			{#if loadMoreError !== null}
				<InlineError error={loadMoreError} onretry={loadMore} retrying={busy} />
			{:else if hasMoreIssues(run)}
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
