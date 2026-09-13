<script lang="ts">
	import { resolve } from '$app/paths';
	import { formatNumber } from '$lib/format';
	import { rankGaps } from '$lib/maintenance';
	import { m } from '$lib/paraglide/messages.js';
	import { locale } from '$lib/stores/locale.svelte';
	import ErrorState from '$lib/components/ui/ErrorState.svelte';
	import GapCard from '$lib/components/maintenance/GapCard.svelte';
	import Icon from '$lib/components/ui/Icon.svelte';
	import SectionHeader from '$lib/components/ui/SectionHeader.svelte';
	import Skeleton from '$lib/components/ui/Skeleton.svelte';
	import type { PageProps } from './$types';

	let { data }: PageProps = $props();
</script>

<svelte:head>
	<title>{m.maintenance_gaps_heading()} · {m.maintenance_title()} · {m.app_name()}</title>
</svelte:head>

<a
	class="inline-flex w-fit items-center gap-1.5 text-[13px] font-medium text-muted transition-colors hover:text-ink"
	href={resolve('/maintenance')}
>
	<Icon name="left" size={14} />
	{m.maintenance_back()}
</a>

<section class="grid gap-4">
	{#await data.gaps}
		<SectionHeader heading={m.maintenance_gaps_heading()} />
		<Skeleton width="100%" height="140px" />
	{:then gaps}
		<SectionHeader
			heading={m.maintenance_gaps_heading()}
			count={formatNumber(gaps.length, locale.intl)}
		/>
		<p class="text-[13px] text-muted">{m.maintenance_gaps_note()}</p>
		{#if gaps.length === 0}
			<p class="text-muted">{m.maintenance_gaps_empty()}</p>
		{:else}
			<div class="grid gap-4">
				{#each rankGaps(gaps) as title (title.title_id)}
					<GapCard {title} />
				{/each}
			</div>
		{/if}
	{:catch error}
		<ErrorState {error} />
	{/await}
</section>
