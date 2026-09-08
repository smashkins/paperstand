<script lang="ts">
	import type { ResolvedPathname } from '$app/types';
	import type { Snippet } from 'svelte';

	/**
	 * A titled, horizontally scrolling row of covers.
	 *
	 * The row snaps and hides its scrollbar, which is what makes it feel like a
	 * shelf on a phone; on a wide screen the same row simply does not overflow.
	 */
	interface Props {
		heading?: string;
		count?: number;
		moreHref?: ResolvedPathname;
		moreLabel?: string;
		gap?: string;
		children: Snippet;
	}

	let { heading, count, moreHref, moreLabel, gap = '22px', children }: Props = $props();
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

	<!-- The negative margin gives the hover lift and the focus ring room to
	     escape without opening a scrollbar of their own. -->
	<div class="shelf-row -mx-1 -mt-1.5 px-1 pt-1.5 pb-4.5" style:gap>
		{@render children()}
	</div>
</section>
