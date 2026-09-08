<script lang="ts">
	import { DEFAULT_ASPECT } from '$lib/format';

	/**
	 * One cover, at its real aspect ratio.
	 *
	 * The ratio comes from the API, so the box is the right shape before the
	 * image exists: nothing on the page moves when it arrives. Until then a
	 * shimmer fills it, and an image that fails to load simply leaves the
	 * shimmer's frame behind rather than a broken-image glyph.
	 */
	interface Props {
		src?: string | null;
		alt?: string;
		aspect?: number;
		/** 0–100, or null for an issue that has not been opened. */
		progress?: number | null;
		/** Set only on the one cover carrying a view transition. */
		transitionName?: string;
		/** Eager for the first covers on the page; everything else is lazy. */
		eager?: boolean;
		class?: string;
	}

	let {
		src,
		alt = '',
		aspect = DEFAULT_ASPECT,
		progress = null,
		transitionName,
		eager = false,
		class: klass = ''
	}: Props = $props();

	let loaded = $state(false);
	let failed = $state(false);

	// A new src is a new image: the placeholder comes back until it loads.
	$effect(() => {
		void src;
		loaded = false;
		failed = false;
	});
</script>

<div
	class="relative overflow-hidden rounded-cover border border-hairline bg-surface shadow-cover transition-[transform,box-shadow] duration-200 ease-out group-hover:-translate-y-1 group-hover:shadow-cover-lift {klass}"
	style:aspect-ratio={aspect}
	style:view-transition-name={transitionName}
>
	{#if !loaded}
		<div class="shimmer absolute inset-0" aria-hidden="true"></div>
	{/if}

	{#if src && !failed}
		<img
			{src}
			{alt}
			loading={eager ? 'eager' : 'lazy'}
			decoding="async"
			fetchpriority={eager ? 'high' : 'auto'}
			class="absolute inset-0 h-full w-full object-cover transition-opacity duration-200"
			class:opacity-0={!loaded}
			onload={() => (loaded = true)}
			onerror={() => {
				failed = true;
				loaded = true;
			}}
		/>
	{/if}

	{#if progress !== null && progress > 0}
		<div
			class="absolute bottom-0 left-0 h-[3px] bg-accent"
			style:width="{progress}%"
			aria-hidden="true"
		></div>
	{/if}
</div>
