<script lang="ts">
	import { resolve } from '$app/paths';
	import type { Title } from '$lib/api/client';
	import { pluralise } from '$lib/format';
	import { m } from '$lib/paraglide/messages.js';
	import { locale } from '$lib/stores/locale.svelte';
	import CoverGrid from '$lib/components/covers/CoverGrid.svelte';
	import TitleCard from '$lib/components/covers/TitleCard.svelte';
	import EmptyState from '$lib/components/ui/EmptyState.svelte';

	/**
	 * Every title of one kind, as a wall of latest covers.
	 *
	 * `Unsorted` is sorted to the end whatever the API's order: it is a bucket,
	 * not a periodical, and it should not sit between two real names.
	 */
	interface Props {
		heading: string;
		titles: Title[];
	}

	let { heading, titles }: Props = $props();

	const ordered = $derived(
		[...titles].sort((a, b) => Number(a.source === 'unsorted') - Number(b.source === 'unsorted'))
	);

	const count = $derived(
		pluralise(titles.length, locale.intl, m.title_count_one, m.title_count_other)
	);
</script>

<div class="grid gap-6">
	<header class="flex items-baseline gap-3.5">
		<h1 class="opsz-title font-serif text-[clamp(28px,4vw,40px)] font-semibold tracking-[-0.02em]">
			{heading}
		</h1>
		<span class="tabular text-muted">{count}</span>
	</header>

	{#if ordered.length === 0}
		<EmptyState
			title={m.titles_none()}
			body={m.titles_none_body()}
			actionLabel={m.empty_library_action()}
			actionHref={resolve('/settings')}
		/>
	{:else}
		<CoverGrid>
			{#each ordered as title, index (title.id)}
				<TitleCard {title} eager={index < 6} />
			{/each}
		</CoverGrid>
	{/if}
</div>
