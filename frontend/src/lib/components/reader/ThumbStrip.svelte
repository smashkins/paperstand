<script lang="ts">
	import { formatNumber } from '$lib/format';
	import { m } from '$lib/paraglide/messages.js';
	import { locale } from '$lib/stores/locale.svelte';
	import { pageImageUrl, THUMB_WIDTH } from '$lib/reader/pages';

	/**
	 * Every page of the issue, small, in a row.
	 *
	 * The images are the server's 200 px WebPs — the cheapest thing the backend
	 * renders — and they are lazy, so opening the strip on a 200-page magazine
	 * fetches the dozen that are actually on screen and nothing else. The strip
	 * scrolls the current page into the middle whenever the reader moves, which
	 * is what makes it usable as a map rather than just a list.
	 */
	interface Props {
		pageCount: number;
		/** The pages on screen right now: one, or the two of a spread. */
		current: number[];
		template: string;
		aspect: number;
		onselect: (page: number) => void;
	}

	let { pageCount, current, template, aspect, onselect }: Props = $props();

	let strip = $state<HTMLDivElement | null>(null);
	const active = $derived(current[0] ?? 1);
	const pages = $derived(Array.from({ length: Math.max(0, pageCount) }, (_, i) => i + 1));

	// Follow the reader: the active thumbnail is brought to the middle.
	$effect(() => {
		const container = strip;
		const page = active;
		if (!container) return;
		const thumb = container.querySelector<HTMLElement>(`[data-page="${page}"]`);
		if (!thumb) return;
		const reduced = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false;
		thumb.scrollIntoView({
			behavior: reduced ? 'auto' : 'smooth',
			inline: 'center',
			block: 'nearest'
		});
	});
</script>

<div
	bind:this={strip}
	class="shelf-row gap-2 border-t border-hairline bg-surface/95 px-3 py-2.5 backdrop-blur"
	role="group"
	aria-label={m.reader_pages()}
>
	{#each pages as page (page)}
		{@const label = m.page_only({ page: formatNumber(page, locale.intl) })}
		<button
			type="button"
			data-page={page}
			title={label}
			aria-label={label}
			aria-current={current.includes(page) ? 'true' : undefined}
			class="relative h-[88px] shrink-0 overflow-hidden rounded-cover border bg-bg transition-colors"
			class:border-accent={current.includes(page)}
			class:border-hairline={!current.includes(page)}
			style:aspect-ratio={aspect}
			onclick={() => onselect(page)}
		>
			<img
				src={pageImageUrl(template, page, THUMB_WIDTH)}
				alt=""
				loading="lazy"
				decoding="async"
				class="h-full w-full object-cover"
			/>
			<span
				class="tabular absolute inset-x-0 bottom-0 bg-black/55 py-[1px] text-center text-[10px] text-white"
			>
				{formatNumber(page, locale.intl)}
			</span>
		</button>
	{/each}
</div>
