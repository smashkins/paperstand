<script lang="ts">
	import { resolve } from '$app/paths';
	import { m } from '$lib/paraglide/messages.js';
	import CoverGrid from '$lib/components/covers/CoverGrid.svelte';
	import GridSkeleton from '$lib/components/covers/GridSkeleton.svelte';
	import IssueCard from '$lib/components/covers/IssueCard.svelte';
	import Shelf from '$lib/components/covers/Shelf.svelte';
	import ShelfSkeleton from '$lib/components/covers/ShelfSkeleton.svelte';
	import HeroToday from '$lib/components/today/HeroToday.svelte';
	import EmptyState from '$lib/components/ui/EmptyState.svelte';
	import ErrorState from '$lib/components/ui/ErrorState.svelte';
	import Skeleton from '$lib/components/ui/Skeleton.svelte';
	import type { PageProps } from './$types';

	let { data }: PageProps = $props();
</script>

<svelte:head><title>{m.nav_today()} · {m.app_name()}</title></svelte:head>

{#await data.today}
	<div class="grid gap-3.5">
		<Skeleton width="260px" height="16px" />
		<Skeleton width="min(520px, 90%)" height="64px" />
		<Skeleton width="min(460px, 100%)" height="18px" />
	</div>
	<ShelfSkeleton width="226px" count={5} heading={false} />
	<GridSkeleton count={6} />
{:then today}
	<HeroToday
		date={today.date}
		newspapersDate={today.newspapers_date}
		isToday={today.is_today}
		count={today.newspapers.length}
	/>

	{#if today.newspapers.length > 0}
		<Shelf>
			{#each today.newspapers as issue, index (issue.id)}
				<IssueCard {issue} width="clamp(160px, 46vw, 226px)" eager={index < 3} />
			{/each}
		</Shelf>
	{:else if today.magazines.length === 0 && today.recently_added.length === 0}
		<EmptyState
			title={m.empty_library_title()}
			body={m.empty_library_body()}
			actionLabel={m.empty_library_action()}
			actionHref={resolve('/settings')}
		/>
	{:else}
		<p class="text-muted">{m.today_no_newspapers()}</p>
	{/if}

	{#if today.continue_reading.length > 0}
		<Shelf heading={m.shelf_continue()} count={today.continue_reading.length}>
			{#each today.continue_reading as issue (issue.id)}
				<IssueCard {issue} width="132px" secondary="progress" />
			{/each}
		</Shelf>
	{/if}

	{#if today.magazines.length > 0}
		<CoverGrid
			heading={m.shelf_latest_magazines()}
			count={today.magazines.length}
			moreHref={resolve('/magazines')}
			moreLabel={m.shelf_all()}
		>
			{#each today.magazines as issue (issue.id)}
				<IssueCard {issue} />
			{/each}
		</CoverGrid>
	{/if}

	{#if today.recently_added.length > 0}
		<Shelf heading={m.shelf_recently_added()} count={today.recently_added.length}>
			{#each today.recently_added as issue (issue.id)}
				<IssueCard {issue} width="150px" secondary="added" showChips />
			{/each}
		</Shelf>
	{/if}
{:catch error}
	<ErrorState {error} />
{/await}
