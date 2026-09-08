<script lang="ts">
	import { m } from '$lib/paraglide/messages.js';
	import GridSkeleton from '$lib/components/covers/GridSkeleton.svelte';
	import TitleWall from '$lib/components/title/TitleWall.svelte';
	import ErrorState from '$lib/components/ui/ErrorState.svelte';
	import type { PageProps } from './$types';

	let { data }: PageProps = $props();
</script>

<svelte:head><title>{m.nav_newspapers()} · {m.app_name()}</title></svelte:head>

{#await data.titles}
	<GridSkeleton />
{:then titles}
	<TitleWall heading={m.nav_newspapers()} {titles} />
{:catch error}
	<ErrorState {error} />
{/await}
