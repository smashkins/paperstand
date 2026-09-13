<script lang="ts">
	import type { ResolvedPathname } from '$app/types';

	/**
	 * A section's title row: a heading, an optional count next to it, and an
	 * optional link to the rest, pushed to the end.
	 *
	 * Mirrors `covers/Shelf.svelte`'s own header without touching that
	 * component — Shelf wraps a row of covers, this wraps nothing, so a page
	 * with more beneath the heading renders it itself.
	 */
	interface Props {
		heading: string;
		count?: number | string;
		moreHref?: ResolvedPathname;
		moreLabel?: string;
	}

	let { heading, count, moreHref, moreLabel }: Props = $props();
</script>

<div class="flex items-baseline gap-3">
	<h2 class="opsz-title font-serif text-2xl font-semibold">{heading}</h2>
	{#if count !== undefined}
		<span class="tabular text-muted">{count}</span>
	{/if}
	{#if moreHref && moreLabel}
		<a class="ml-auto font-medium text-muted transition-colors hover:text-ink" href={moreHref}>
			{moreLabel}
		</a>
	{/if}
</div>
