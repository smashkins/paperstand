<script lang="ts">
	import { resolve } from '$app/paths';
	import type { TitleGaps } from '$lib/api/client';
	import { formatGapPeriod, formatNumber, formatShortDate, pluralise } from '$lib/format';
	import { missingNumbers, numberChip } from '$lib/maintenance';
	import { m } from '$lib/paraglide/messages.js';
	import { locale } from '$lib/stores/locale.svelte';
	import Badge from '$lib/components/ui/Badge.svelte';

	/** One title's holes in its numbering, its declared cadence, or both. */
	interface Props {
		title: TitleGaps;
	}

	let { title }: Props = $props();

	const frequencyLabels: Record<NonNullable<TitleGaps['frequency']>, () => string> = {
		daily: m.frequency_daily,
		weekly: m.frequency_weekly,
		monthly: m.frequency_monthly,
		irregular: m.frequency_irregular
	};
</script>

<div class="grid gap-2 rounded-cover border border-hairline bg-surface p-4">
	<div class="flex flex-wrap items-center gap-2">
		<a
			class="opsz-title font-serif text-lg font-semibold hover:underline"
			href={resolve('/title/[id]', { id: title.title_id })}
		>
			{title.title_name}
		</a>
		<span class="text-xs tracking-[0.06em] text-muted uppercase">
			{title.kind === 'newspaper' ? m.kind_newspaper() : m.kind_magazine()}
			{#if title.frequency}
				· {frequencyLabels[title.frequency]()}
			{/if}
		</span>
		{#if title.overdue_days !== null}
			<Badge variant="warning">
				{pluralise(
					title.overdue_days,
					locale.intl,
					m.maintenance_overdue_days_one,
					m.maintenance_overdue_days_other
				)}
			</Badge>
		{/if}
	</div>

	{#if title.last_date}
		<span class="tabular text-[13px] text-muted">
			{m.maintenance_last_issue({ when: formatShortDate(title.last_date, locale.intl) })}
		</span>
	{/if}

	{#if title.date_gap_count > 0}
		<div class="grid gap-1.5">
			<span class="text-[13px] text-muted">
				{pluralise(
					title.date_gap_count,
					locale.intl,
					m.maintenance_date_gaps_one,
					m.maintenance_date_gaps_other
				)}
			</span>
			<div class="flex flex-wrap gap-1">
				{#each title.date_gaps as label (label)}
					<Badge>{formatGapPeriod(label, locale.intl)}</Badge>
				{/each}
				{#if title.date_gap_count > title.date_gaps.length}
					<Badge>
						{m.maintenance_gaps_more({
							count: formatNumber(title.date_gap_count - title.date_gaps.length, locale.intl)
						})}
					</Badge>
				{/if}
			</div>
		</div>
	{/if}

	{#if title.number_gap_count > 0}
		<div class="grid gap-1.5">
			<span class="text-[13px] text-muted">
				{pluralise(
					title.number_gap_count,
					locale.intl,
					m.maintenance_number_gaps_one,
					m.maintenance_number_gaps_other
				)}
			</span>
			<div class="flex flex-wrap gap-1">
				{#each title.number_gaps as gap (`${gap.volume}-${gap.first}-${gap.last}`)}
					<Badge>{numberChip(gap)}</Badge>
				{/each}
				{#if title.number_gap_count > missingNumbers(title.number_gaps)}
					<Badge>
						{m.maintenance_gaps_more({
							count: formatNumber(
								title.number_gap_count - missingNumbers(title.number_gaps),
								locale.intl
							)
						})}
					</Badge>
				{/if}
			</div>
		</div>
	{/if}
</div>
