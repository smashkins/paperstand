<script lang="ts">
	import type { IssuePage } from '$lib/api/client';
	import { m } from '$lib/paraglide/messages.js';
	import CoverGrid from '$lib/components/covers/CoverGrid.svelte';
	import GridSkeleton from '$lib/components/covers/GridSkeleton.svelte';
	import IssueCard from '$lib/components/covers/IssueCard.svelte';
	import IssueCalendar from '$lib/components/title/IssueCalendar.svelte';
	import TitleHeader from '$lib/components/title/TitleHeader.svelte';
	import YearSection from '$lib/components/title/YearSection.svelte';
	import ErrorState from '$lib/components/ui/ErrorState.svelte';
	import Skeleton from '$lib/components/ui/Skeleton.svelte';
	import type { PageProps } from './$types';

	let { data }: PageProps = $props();

	// `<svelte:head>` cannot live inside an `{#await}`, so the tab title is
	// state the resolved title fills in.
	let pageTitle = $state<string>(m.app_name());

	$effect(() => {
		let live = true;
		data.title
			.then((title) => {
				if (live) pageTitle = `${title.name} · ${m.app_name()}`;
			})
			.catch(() => {});
		return () => {
			live = false;
		};
	});
</script>

<svelte:head><title>{pageTitle}</title></svelte:head>

{#snippet recentIssues(recent: IssuePage)}
	{#if recent.items.length > 0}
		<CoverGrid heading={m.title_recent_issues()} count={recent.total}>
			{#each recent.items as issue (issue.id)}
				<IssueCard {issue} showChips />
			{/each}
		</CoverGrid>
	{:else}
		<p class="text-muted">{m.title_no_issues()}</p>
	{/if}
{/snippet}

{#await data.title}
	<div class="flex flex-col gap-6 sm:flex-row sm:items-end sm:gap-8">
		<Skeleton width="152px" aspect={0.72} class="sm:!w-[210px]" />
		<div class="grid gap-3">
			<Skeleton width="120px" height="18px" />
			<Skeleton width="min(420px, 80vw)" height="48px" />
			<Skeleton width="200px" height="16px" />
		</div>
	</div>
	<GridSkeleton />
{:then title}
	<TitleHeader {title} />

	{#if title.kind === 'newspaper'}
		<!-- Every secondary request gets its own `catch`: the title arrived, and a
		     calendar that did not should not take the page down with it. `quiet()`
		     stops the *unhandled* copy, it does not make the promise succeed. -->
		{#await data.calendar}
			<Skeleton width="min(520px, 100%)" height="420px" />
		{:then calendar}
			{#if calendar}
				<IssueCalendar titleId={title.id} initial={calendar} />
			{/if}
		{:catch error}
			<ErrorState {error} />
		{/await}

		{#await data.recent}
			<GridSkeleton count={8} />
		{:then recent}
			{@render recentIssues(recent)}
		{:catch error}
			<ErrorState {error} />
		{/await}
	{:else if title.years.length > 0}
		<div class="grid gap-5">
			{#each title.years as entry, index (entry.year)}
				<YearSection titleId={title.id} year={entry.year} count={entry.count} open={index === 0} />
			{/each}
		</div>
	{:else}
		{#await data.recent}
			<GridSkeleton count={8} />
		{:then recent}
			{@render recentIssues(recent)}
		{:catch error}
			<ErrorState {error} />
		{/await}
	{/if}
{:catch error}
	<ErrorState {error} />
{/await}
