<script lang="ts">
	import { resolve } from '$app/paths';
	import {
		api,
		quiet,
		type IssuePage,
		type MaintenanceSummary,
		type TitleGaps,
		type UnsortedBucket
	} from '$lib/api/client';
	import { requestScan } from '$lib/api/scan';
	import { formatNumber, formatWhen, messageLocale } from '$lib/format';
	import { OVERVIEW_LIMIT, rankGaps } from '$lib/maintenance';
	import { m } from '$lib/paraglide/messages.js';
	import { locale } from '$lib/stores/locale.svelte';
	import { attention } from '$lib/stores/attention.svelte';
	import Button from '$lib/components/ui/Button.svelte';
	import ErrorState from '$lib/components/ui/ErrorState.svelte';
	import GapCard from '$lib/components/maintenance/GapCard.svelte';
	import Icon from '$lib/components/ui/Icon.svelte';
	import MissingRow from '$lib/components/maintenance/MissingRow.svelte';
	import ParkedTable from '$lib/components/maintenance/ParkedTable.svelte';
	import SectionHeader from '$lib/components/ui/SectionHeader.svelte';
	import Skeleton from '$lib/components/ui/Skeleton.svelte';
	import UnreadableRow from '$lib/components/maintenance/UnreadableRow.svelte';
	import type { PageProps } from './$types';

	let { data }: PageProps = $props();

	// Each source starts as the promise `load` handed in. A rescan this page
	// started (or found already running) replaces it with a fresh one once
	// the scan finishes — otherwise every section here would read stale
	// until a full reload.
	let refreshedSummary = $state<Promise<MaintenanceSummary> | undefined>(undefined);
	let refreshedGaps = $state<Promise<TitleGaps[]> | undefined>(undefined);
	let refreshedMissing = $state<Promise<IssuePage> | undefined>(undefined);
	let refreshedUnreadable = $state<Promise<IssuePage> | undefined>(undefined);

	const summaryData = $derived(refreshedSummary ?? data.summary);
	const gapsData = $derived(refreshedGaps ?? data.gaps);
	const missingData = $derived(refreshedMissing ?? data.missing);
	const unreadableData = $derived(refreshedUnreadable ?? data.unreadable);

	let scanMessage = $state<string | null>(null);
	let starting = $state(false);
	let scanning = $state(false);

	// The Missing section's "forgotten in N days" needs `missing_grace_days`
	// from the summary; the two settle together so the number is never wrong.
	const missingSection = $derived(Promise.all([missingData, summaryData]));

	// The page's own summary sets the shared badge, so the number a person
	// just looked at and the one in the top bar never disagree.
	$effect(() => {
		let live = true;
		summaryData
			.then((summary) => {
				if (live) attention.set(summary.attention);
			})
			.catch(() => {});
		return () => {
			live = false;
		};
	});

	/** Every section, fetched again now that a scan this page waited on has finished. */
	function refreshAll(): void {
		refreshedSummary = quiet(api.maintenance());
		refreshedGaps = quiet(api.maintenanceGaps({}));
		refreshedMissing = quiet(api.issues({ missing: true, limit: OVERVIEW_LIMIT }));
		refreshedUnreadable = quiet(api.issues({ unreadable: true, limit: OVERVIEW_LIMIT }));
	}

	// While `scanning` is set — a scan this page started, or found already
	// running — poll its status every two seconds, exactly as Settings does.
	// Once it stops running, every section here is stale by definition, so
	// all four are fetched again and the shared badge is refreshed with them.
	$effect(() => {
		if (!scanning) return;
		const timer = setInterval(async () => {
			try {
				const status = await api.scanStatus();
				if (!status.running) {
					scanning = false;
					refreshAll();
					attention.refresh();
				}
			} catch {
				// A blip is not worth stopping the poll over: the next tick retries.
			}
		}, 2000);
		return () => clearInterval(timer);
	});

	async function rescan() {
		starting = true;
		scanMessage = null;
		try {
			const result = await requestScan();
			scanMessage = result === 'started' ? m.scan_started() : m.scan_already_running();
			scanning = true;
		} catch {
			scanMessage = m.scan_failed();
		} finally {
			starting = false;
		}
	}

	/** A plural message that also needs the library's name, which `pluralise` does not carry. */
	function unsortedBucketLabel(bucket: UnsortedBucket): string {
		const tag = messageLocale(locale.intl);
		const rule = new Intl.PluralRules(locale.intl).select(bucket.count);
		const inputs = { count: formatNumber(bucket.count, locale.intl), library: bucket.library_name };
		return rule === 'one'
			? m.maintenance_unsorted_bucket_one(inputs, { locale: tag })
			: m.maintenance_unsorted_bucket_other(inputs, { locale: tag });
	}

	const modeLabels: Record<'apply' | 'dry-run', () => string> = {
		apply: m.maintenance_mode_apply,
		'dry-run': m.maintenance_mode_dry_run
	};
</script>

<svelte:head><title>{m.maintenance_title()} · {m.app_name()}</title></svelte:head>

<div class="grid gap-2">
	<h1 class="opsz-title font-serif text-[clamp(28px,4vw,40px)] font-semibold tracking-[-0.02em]">
		{m.maintenance_title()}
	</h1>
	<p class="max-w-[60ch] text-muted">{m.maintenance_lede()}</p>
</div>

<div class="flex flex-wrap items-center gap-3">
	<Button variant="primary" onclick={rescan} disabled={starting || scanning}>
		<Icon name="refresh" size={14} />
		{scanning ? m.scan_scanning() : m.scan_rescan()}
	</Button>
	{#if scanMessage}
		<p class="text-[13px] text-muted" role="status">{scanMessage}</p>
	{/if}
</div>

<section class="grid gap-4">
	{#await missingSection}
		<SectionHeader heading={m.maintenance_missing_heading()} />
		<Skeleton width="100%" height="88px" />
	{:then [missing, summary]}
		<SectionHeader
			heading={m.maintenance_missing_heading()}
			count={formatNumber(missing.total, locale.intl)}
			moreHref={missing.total > OVERVIEW_LIMIT ? resolve('/maintenance/missing') : undefined}
			moreLabel={m.maintenance_see_all()}
		/>
		<p class="text-[13px] text-muted">{m.maintenance_missing_note()}</p>
		{#if missing.items.length === 0}
			<p class="text-muted">{m.maintenance_missing_empty()}</p>
		{:else}
			<ul class="grid gap-3">
				{#each missing.items as issue (issue.id)}
					<MissingRow {issue} graceDays={summary.missing_grace_days} />
				{/each}
			</ul>
		{/if}
	{:catch error}
		<ErrorState {error} />
	{/await}
</section>

<section class="grid gap-4">
	{#await unreadableData}
		<SectionHeader heading={m.maintenance_unreadable_heading()} />
		<Skeleton width="100%" height="88px" />
	{:then unreadable}
		<SectionHeader
			heading={m.maintenance_unreadable_heading()}
			count={formatNumber(unreadable.total, locale.intl)}
			moreHref={unreadable.total > OVERVIEW_LIMIT ? resolve('/maintenance/unreadable') : undefined}
			moreLabel={m.maintenance_see_all()}
		/>
		{#if unreadable.items.length === 0}
			<p class="text-muted">{m.maintenance_unreadable_empty()}</p>
		{:else}
			<ul class="grid gap-3">
				{#each unreadable.items as issue (issue.id)}
					<UnreadableRow {issue} />
				{/each}
			</ul>
		{/if}
	{:catch error}
		<ErrorState {error} />
	{/await}
</section>

<section class="grid gap-4">
	{#await summaryData}
		<SectionHeader heading={m.maintenance_inbox_heading()} />
		<Skeleton width="100%" height="140px" />
	{:then summary}
		{@const unsortedParked =
			summary.organizer?.parked.filter((file) => file.folder === 'unsorted') ?? []}
		{@const duplicateParked =
			summary.organizer?.parked.filter((file) => file.folder === 'duplicates') ?? []}
		<SectionHeader
			heading={m.maintenance_inbox_heading()}
			moreHref={unsortedParked.length > OVERVIEW_LIMIT || duplicateParked.length > OVERVIEW_LIMIT
				? resolve('/maintenance/inbox')
				: undefined}
			moreLabel={m.maintenance_see_all()}
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
				<ParkedTable
					heading={m.maintenance_inbox_unsorted_table()}
					rows={unsortedParked.slice(0, OVERVIEW_LIMIT)}
				/>
				<ParkedTable
					heading={m.maintenance_inbox_duplicates_table()}
					rows={duplicateParked.slice(0, OVERVIEW_LIMIT)}
				/>
			</div>
		{/if}

		{#if summary.unsorted.length > 0}
			<ul class="grid gap-1 text-[13px]">
				{#each summary.unsorted as bucket (bucket.title_id)}
					<li>
						<a
							class="text-muted hover:text-ink hover:underline"
							href={resolve('/title/[id]', { id: bucket.title_id })}
						>
							{unsortedBucketLabel(bucket)}
						</a>
					</li>
				{/each}
			</ul>
		{/if}
	{:catch error}
		<ErrorState {error} />
	{/await}
</section>

<section class="grid gap-4">
	{#await gapsData}
		<SectionHeader heading={m.maintenance_gaps_heading()} />
		<Skeleton width="100%" height="140px" />
	{:then gaps}
		<SectionHeader
			heading={m.maintenance_gaps_heading()}
			count={formatNumber(gaps.length, locale.intl)}
			moreHref={gaps.length > OVERVIEW_LIMIT ? resolve('/maintenance/gaps') : undefined}
			moreLabel={m.maintenance_see_all()}
		/>
		<p class="text-[13px] text-muted">{m.maintenance_gaps_note()}</p>
		{#if gaps.length === 0}
			<p class="text-muted">{m.maintenance_gaps_empty()}</p>
		{:else}
			<div class="grid gap-4">
				{#each rankGaps(gaps).slice(0, OVERVIEW_LIMIT) as title (title.title_id)}
					<GapCard {title} />
				{/each}
			</div>
		{/if}
	{:catch error}
		<ErrorState {error} />
	{/await}
</section>
