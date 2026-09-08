<script lang="ts">
	import type { ResolvedPathname } from '$app/types';
	import type { Snippet } from 'svelte';

	/**
	 * A dense wall of covers that fills whatever width it is given.
	 *
	 * `auto-fill` with a minimum keeps the covers a readable size on a phone and
	 * lets a wide screen show as many as it can, without a breakpoint anywhere.
	 */
	interface Props {
		heading?: string;
		count?: number;
		moreHref?: ResolvedPathname;
		moreLabel?: string;
		/** The narrowest a column may get before the grid drops one. */
		min?: string;
		children: Snippet;
	}

	let { heading, count, moreHref, moreLabel, min = '160px', children }: Props = $props();
</script>

<section class="min-w-0">
	{#if heading}
		<div class="mb-4 flex items-baseline gap-3.5">
			<h2 class="opsz-title font-serif text-2xl font-semibold tracking-tight">{heading}</h2>
			{#if count !== undefined}
				<span class="tabular text-muted">{count}</span>
			{/if}
			{#if moreHref && moreLabel}
				<a class="ml-auto font-medium text-muted transition-colors hover:text-ink" href={moreHref}>
					{moreLabel}
				</a>
			{/if}
		</div>
	{/if}

	<div
		class="grid gap-x-[18px] gap-y-[22px]"
		style:grid-template-columns="repeat(auto-fill, minmax({min}, 1fr))"
	>
		{@render children()}
	</div>
</section>
