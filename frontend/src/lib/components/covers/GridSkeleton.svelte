<script lang="ts">
	import { DEFAULT_ASPECT } from '$lib/format';
	import { m } from '$lib/paraglide/messages.js';
	import Skeleton from '$lib/components/ui/Skeleton.svelte';

	/** A wall of placeholders, laid out exactly like the grid it precedes. */
	interface Props {
		count?: number;
		min?: string;
		heading?: boolean;
	}

	let { count = 12, min = '160px', heading = true }: Props = $props();
</script>

<!-- The placeholders themselves are `aria-hidden`, so without a label here a
     screen reader is handed silence for as long as the request takes. -->
<section class="min-w-0" aria-busy="true" aria-label={m.loading()}>
	{#if heading}
		<Skeleton width="180px" height="24px" class="mb-4" />
	{/if}
	<div
		class="grid gap-x-[18px] gap-y-[22px]"
		style:grid-template-columns="repeat(auto-fill, minmax({min}, 1fr))"
	>
		{#each { length: count }, index (index)}
			<div class="grid gap-2.5">
				<Skeleton aspect={DEFAULT_ASPECT} />
				<Skeleton width="70%" height="14px" />
				<Skeleton width="45%" height="12px" />
			</div>
		{/each}
	</div>
</section>
