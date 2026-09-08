<script lang="ts">
	import { resolve } from '$app/paths';
	import type { Title } from '$lib/api/client';
	import { coverAspect, formatDateAtPrecision, pluralise } from '$lib/format';
	import { m } from '$lib/paraglide/messages.js';
	import { locale } from '$lib/stores/locale.svelte';
	import Badge from '$lib/components/ui/Badge.svelte';
	import Cover from './Cover.svelte';

	/** One periodical, shown by its latest cover. */
	interface Props {
		title: Title;
		eager?: boolean;
	}

	let { title, eager = false }: Props = $props();

	const issues = $derived(
		pluralise(title.issue_count, locale.intl, m.issue_count_one, m.issue_count_other)
	);

	/**
	 * The latest date, never at a finer precision than the parser reached.
	 *
	 * The backend normalises a month-precise issue to the first of that month,
	 * so showing `last_date` as a day would invent "1 Mar 2026" out of what the
	 * file only ever called "March 2026".
	 */
	const latestDate = $derived(
		formatDateAtPrecision(
			title.last_date,
			title.latest_issue?.date_precision ?? 'year',
			locale.intl
		)
	);

	const meta = $derived(latestDate ? m.title_meta({ issues, date: latestDate }) : issues);
</script>

<a class="group grid w-full gap-2.5" href={resolve('/title/[id]', { id: title.id })}>
	<Cover
		src={title.thumb_url}
		alt={title.name}
		aspect={coverAspect(title.latest_issue?.aspect)}
		{eager}
	/>
	<span class="grid gap-0.5">
		<span class="opsz-text font-serif text-[14.5px] leading-tight font-semibold">{title.name}</span>
		<span class="tabular text-[12.5px] text-muted">{meta}</span>
		{#if title.source === 'unsorted'}
			<Badge variant="warning" class="mt-0.5">{m.chip_unsorted()}</Badge>
		{/if}
	</span>
</a>
