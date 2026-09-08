<script lang="ts">
	import { DEFAULT_ASPECT } from '$lib/format';
	import Skeleton from '$lib/components/ui/Skeleton.svelte';

	/** A shelf's worth of placeholders, the same size as the covers they stand in for. */
	interface Props {
		count?: number;
		width?: string;
		heading?: boolean;
	}

	let { count = 6, width = '168px', heading = true }: Props = $props();
</script>

<section class="min-w-0" aria-busy="true">
	{#if heading}
		<Skeleton width="180px" height="24px" class="mb-4" />
	{/if}
	<div class="shelf-row gap-[22px] pb-4.5">
		{#each { length: count }, index (index)}
			<div class="grid gap-2.5" style:width>
				<Skeleton aspect={DEFAULT_ASPECT} />
				<Skeleton width="70%" height="14px" />
				<Skeleton width="45%" height="12px" />
			</div>
		{/each}
	</div>
</section>
