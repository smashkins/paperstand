<script lang="ts">
	import { capitalise, formatIssueDate, formatShortDate, pluralise } from '$lib/format';
	import { m } from '$lib/paraglide/messages.js';
	import { locale } from '$lib/stores/locale.svelte';
	import Badge from '$lib/components/ui/Badge.svelte';

	/**
	 * The top of the front page: what day it is, and how much of it arrived.
	 *
	 * The badge is the one honest thing here. It says "Today" only when the
	 * papers on the shelf below really are today's; when the morning's files
	 * have not landed the shelf shows the most recent day that has any, and the
	 * badge names that day instead of pretending.
	 */
	interface Props {
		/** The day that was asked for, `YYYY-MM-DD`. */
		date: string;
		/** The day the shelf below actually shows, if any. */
		newspapersDate?: string | null;
		isToday: boolean;
		count: number;
	}

	let { date, newspapersDate, isToday, count }: Props = $props();

	const shown = $derived(newspapersDate ?? date);
	// A null `newspapersDate` means there are no newspapers at all, anywhere — so
	// the headline is the day that was asked for, and naming it "the latest" would
	// be inventing an issue that does not exist.
	const isCurrent = $derived(isToday && (newspapersDate === null || newspapersDate === date));

	const weekday = $derived(capitalise(formatIssueDate(shown, locale.intl, { weekday: 'long' })));
	const dayMonth = $derived(formatIssueDate(shown, locale.intl, { day: 'numeric', month: 'long' }));
	const longDate = $derived(
		capitalise(
			formatIssueDate(shown, locale.intl, {
				weekday: 'long',
				day: 'numeric',
				month: 'long',
				year: 'numeric'
			})
		)
	);
	const newspapers = $derived(
		pluralise(count, locale.intl, m.newspaper_count_one, m.newspaper_count_other)
	);
</script>

<div class="grid gap-3.5">
	<p
		class="flex flex-wrap items-center gap-3 text-xs font-medium tracking-[0.08em] text-muted uppercase"
	>
		<Badge variant="accent">
			{#if isCurrent}
				{m.today_badge()}
			{:else}
				{m.today_latest({ date: formatShortDate(shown, locale.intl) })}
			{/if}
		</Badge>
		<span class="tabular">{longDate}</span>
		{#if count > 0}
			<!-- The separator travels with the count, so a wrap never leaves a
			     dot stranded at the end of a line. -->
			<span class="tabular"><span aria-hidden="true" class="mr-1">·</span>{newspapers}</span>
		{/if}
	</p>

	<h1
		class="opsz-display font-serif text-[clamp(38px,6vw,72px)] leading-none font-medium tracking-[-0.02em] text-balance"
	>
		{weekday}
		<em class="font-normal text-muted italic">{dayMonth}</em>
	</h1>

	<p class="max-w-[60ch] text-[15px] text-muted">{m.today_lede()}</p>
</div>
