<script lang="ts">
	/**
	 * A thin progress bar.
	 *
	 * Determinate when `percent` is given — a filled track, the width it
	 * carries. Indeterminate when it is `null`, for a phase whose total is
	 * not known yet: a short block sweeps back and forth rather than
	 * claiming a fraction of a total nobody has counted.
	 */
	interface Props {
		/** 0–100, or `null` for an indeterminate sweep. */
		percent?: number | null;
		'aria-label'?: string;
		class?: string;
	}

	let { percent = null, 'aria-label': ariaLabel, class: klass = '' }: Props = $props();

	const clamped = $derived(percent === null ? null : Math.min(100, Math.max(0, percent)));
</script>

<div
	class="relative h-1.5 w-full overflow-hidden rounded-full bg-hairline {klass}"
	role="progressbar"
	aria-label={ariaLabel}
	aria-valuenow={clamped ?? undefined}
	aria-valuemin={0}
	aria-valuemax={100}
>
	{#if clamped === null}
		<div class="progress-sweep absolute inset-y-0 w-1/3 rounded-full bg-accent"></div>
	{:else}
		<div
			class="absolute inset-y-0 left-0 rounded-full bg-accent transition-[width] duration-300 ease-out"
			style:width="{clamped}%"
		></div>
	{/if}
</div>
