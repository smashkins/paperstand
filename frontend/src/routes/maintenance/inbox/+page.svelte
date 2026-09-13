<script lang="ts">
	import { resolve } from '$app/paths';
	import { formatNumber, formatWhen } from '$lib/format';
	import { m } from '$lib/paraglide/messages.js';
	import { locale } from '$lib/stores/locale.svelte';
	import ErrorState from '$lib/components/ui/ErrorState.svelte';
	import Icon from '$lib/components/ui/Icon.svelte';
	import ParkedTable from '$lib/components/maintenance/ParkedTable.svelte';
	import SectionHeader from '$lib/components/ui/SectionHeader.svelte';
	import Skeleton from '$lib/components/ui/Skeleton.svelte';
	import type { PageProps } from './$types';

	let { data }: PageProps = $props();

	const modeLabels: Record<'apply' | 'dry-run', () => string> = {
		apply: m.maintenance_mode_apply,
		'dry-run': m.maintenance_mode_dry_run
	};
</script>

<svelte:head>
	<title>{m.maintenance_inbox_heading()} · {m.maintenance_title()} · {m.app_name()}</title>
</svelte:head>

<a
	class="inline-flex w-fit items-center gap-1.5 text-[13px] font-medium text-muted transition-colors hover:text-ink"
	href={resolve('/maintenance')}
>
	<Icon name="left" size={14} />
	{m.maintenance_back()}
</a>

<section class="grid gap-4">
	{#await data.summary}
		<SectionHeader heading={m.maintenance_inbox_heading()} />
		<Skeleton width="100%" height="140px" />
	{:then summary}
		{@const unsortedParked =
			summary.organizer?.parked.filter((file) => file.folder === 'unsorted') ?? []}
		{@const duplicateParked =
			summary.organizer?.parked.filter((file) => file.folder === 'duplicates') ?? []}
		<SectionHeader
			heading={m.maintenance_inbox_heading()}
			count={summary.organizer
				? formatNumber(summary.organizer.parked.length, locale.intl)
				: undefined}
		/>
		{#if summary.organizer === null}
			<p class="text-muted">{m.maintenance_inbox_never_run()}</p>
		{:else}
			{@const run = summary.organizer}
			<p class="text-[13px] text-muted">{m.maintenance_inbox_note()}</p>
			<div class="grid gap-1 rounded-cover border border-hairline bg-surface p-4 text-[13px]">
				<span class="font-medium">
					{m.maintenance_inbox_last({ when: formatWhen(run.finished_at, locale.intl) })} ·
					{modeLabels[run.mode]()}
				</span>
				<span class="tabular text-muted">
					{m.maintenance_inbox_summary({
						moved: formatNumber(run.moved, locale.intl),
						duplicate: formatNumber(run.duplicate, locale.intl),
						unsorted: formatNumber(run.unsorted, locale.intl),
						skipped: formatNumber(run.skipped, locale.intl),
						failed: formatNumber(run.failed, locale.intl)
					})}
				</span>
				{#if run.refused}
					<span class="text-accent-text">{m.maintenance_inbox_refused()}</span>
				{/if}
			</div>

			<div class="grid gap-4 sm:grid-cols-2">
				<ParkedTable heading={m.maintenance_inbox_unsorted_table()} rows={unsortedParked} />
				<ParkedTable heading={m.maintenance_inbox_duplicates_table()} rows={duplicateParked} />
			</div>
		{/if}
	{:catch error}
		<ErrorState {error} />
	{/await}
</section>
