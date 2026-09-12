<script lang="ts">
	import { resolve } from '$app/paths';
	import type { NumberGap, TitleGaps, UnsortedBucket } from '$lib/api/client';
	import { requestScan } from '$lib/api/scan';
	import {
		coverAspect,
		formatGapPeriod,
		formatIssueLabel,
		formatNumber,
		formatShortDate,
		formatSize,
		formatWhen,
		messageLocale,
		pluralise
	} from '$lib/format';
	import { m } from '$lib/paraglide/messages.js';
	import { locale } from '$lib/stores/locale.svelte';
	import { attention } from '$lib/stores/attention.svelte';
	import Badge from '$lib/components/ui/Badge.svelte';
	import Button from '$lib/components/ui/Button.svelte';
	import Cover from '$lib/components/covers/Cover.svelte';
	import ErrorState from '$lib/components/ui/ErrorState.svelte';
	import Icon from '$lib/components/ui/Icon.svelte';
	import Skeleton from '$lib/components/ui/Skeleton.svelte';
	import type { PageProps } from './$types';

	let { data }: PageProps = $props();

	let scanMessage = $state<string | null>(null);
	let starting = $state(false);

	// The Missing section's "forgotten in N days" needs `missing_grace_days`
	// from the summary; the two settle together so the number is never wrong.
	const missingSection = $derived(Promise.all([data.missing, data.summary]));

	// The page's own summary sets the shared badge, so the number a person
	// just looked at and the one in the top bar never disagree.
	$effect(() => {
		let live = true;
		data.summary
			.then((summary) => {
				if (live) attention.set(summary.attention);
			})
			.catch(() => {});
		return () => {
			live = false;
		};
	});

	async function rescan() {
		starting = true;
		scanMessage = null;
		try {
			const result = await requestScan();
			scanMessage = result === 'started' ? m.scan_started() : m.scan_already_running();
		} catch {
			scanMessage = m.scan_failed();
		} finally {
			starting = false;
			attention.refresh();
		}
	}

	/** Days until a missing row is forgotten; `0` or less reads as "the next scan". */
	function daysUntilForgotten(missingSince: string, graceDays: number): number {
		const since = new Date(missingSince).getTime();
		if (Number.isNaN(since)) return 0;
		return Math.ceil((since + graceDays * 86_400_000 - Date.now()) / 86_400_000);
	}

	/** `n1652`, `n1660–1663`, or `v2024 n02` when the title carries a volume. */
	function numberChip(gap: NumberGap): string {
		const range = gap.first === gap.last ? `n${gap.first}` : `n${gap.first}–${gap.last}`;
		return gap.volume !== null ? `v${gap.volume} ${range}` : range;
	}

	function missingNumbers(gaps: NumberGap[]): number {
		return gaps.reduce((total, gap) => total + (gap.last - gap.first + 1), 0);
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

	const frequencyLabels: Record<NonNullable<TitleGaps['frequency']>, () => string> = {
		daily: m.frequency_daily,
		weekly: m.frequency_weekly,
		monthly: m.frequency_monthly,
		irregular: m.frequency_irregular
	};

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
	<Button variant="primary" onclick={rescan} disabled={starting}>
		<Icon name="refresh" size={14} />
		{m.scan_rescan()}
	</Button>
	{#if scanMessage}
		<p class="text-[13px] text-muted" role="status">{scanMessage}</p>
	{/if}
</div>

<section class="grid gap-4">
	{#await missingSection}
		<div class="flex items-baseline gap-3">
			<h2 class="opsz-title font-serif text-2xl font-semibold">
				{m.maintenance_missing_heading()}
			</h2>
		</div>
		<Skeleton width="100%" height="88px" />
	{:then [missing, summary]}
		<div class="flex items-baseline gap-3">
			<h2 class="opsz-title font-serif text-2xl font-semibold">
				{m.maintenance_missing_heading()}
			</h2>
			<span class="tabular text-muted">{formatNumber(missing.total, locale.intl)}</span>
		</div>
		<p class="text-[13px] text-muted">{m.maintenance_missing_note()}</p>
		{#if missing.items.length === 0}
			<p class="text-muted">{m.maintenance_missing_empty()}</p>
		{:else}
			<ul class="grid gap-3">
				{#each missing.items as issue (issue.id)}
					<li class="flex gap-3 rounded-cover border border-hairline bg-surface p-3">
						<Cover
							src={issue.thumb_url}
							alt={issue.title_name}
							aspect={coverAspect(issue.aspect)}
							class="w-16 shrink-0"
						/>
						<div class="grid min-w-0 gap-0.5 text-[13px]">
							<a
								class="font-medium hover:underline"
								href={resolve('/title/[id]', { id: issue.title_id })}
							>
								{issue.title_name}
							</a>
							<span class="text-muted">{formatIssueLabel(issue, locale.intl)}</span>
							<span class="font-mono text-xs break-all text-muted">{issue.rel_path}</span>
							{#if issue.missing_since}
								{@const remaining = daysUntilForgotten(
									issue.missing_since,
									summary.missing_grace_days
								)}
								<span class="tabular text-muted">
									{m.maintenance_missing_since({
										when: formatWhen(issue.missing_since, locale.intl)
									})}
								</span>
								<span class="tabular text-muted">
									{#if remaining <= 0}
										{m.maintenance_forgotten_next_scan()}
									{:else}
										{pluralise(
											remaining,
											locale.intl,
											m.maintenance_forgotten_days_one,
											m.maintenance_forgotten_days_other
										)}
									{/if}
								</span>
							{/if}
						</div>
					</li>
				{/each}
			</ul>
		{/if}
	{:catch error}
		<ErrorState {error} />
	{/await}
</section>

<section class="grid gap-4">
	{#await data.unreadable}
		<h2 class="opsz-title font-serif text-2xl font-semibold">
			{m.maintenance_unreadable_heading()}
		</h2>
		<Skeleton width="100%" height="88px" />
	{:then unreadable}
		<div class="flex items-baseline gap-3">
			<h2 class="opsz-title font-serif text-2xl font-semibold">
				{m.maintenance_unreadable_heading()}
			</h2>
			<span class="tabular text-muted">{formatNumber(unreadable.total, locale.intl)}</span>
		</div>
		{#if unreadable.items.length === 0}
			<p class="text-muted">{m.maintenance_unreadable_empty()}</p>
		{:else}
			<ul class="grid gap-3">
				{#each unreadable.items as issue (issue.id)}
					<li class="grid gap-0.5 rounded-cover border border-hairline bg-surface p-3 text-[13px]">
						<span class="font-medium">{issue.filename}</span>
						<span class="font-mono text-xs break-all text-muted">{issue.rel_path}</span>
						{#if issue.cover_error}
							<span class="text-accent-text">{issue.cover_error}</span>
						{/if}
						<span class="tabular text-muted">
							{m.added_at({ when: formatWhen(issue.added_at, locale.intl) })}
						</span>
					</li>
				{/each}
			</ul>
		{/if}
	{:catch error}
		<ErrorState {error} />
	{/await}
</section>

<section class="grid gap-4">
	{#await data.summary}
		<h2 class="opsz-title font-serif text-2xl font-semibold">{m.maintenance_inbox_heading()}</h2>
		<Skeleton width="100%" height="140px" />
	{:then summary}
		<h2 class="opsz-title font-serif text-2xl font-semibold">{m.maintenance_inbox_heading()}</h2>
		{#if summary.organizer === null}
			<p class="text-muted">{m.maintenance_inbox_never_run()}</p>
		{:else}
			{@const run = summary.organizer}
			{@const unsortedParked = run.parked.filter((file) => file.folder === 'unsorted')}
			{@const duplicateParked = run.parked.filter((file) => file.folder === 'duplicates')}
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
				<div class="grid gap-2">
					<h3 class="text-xs tracking-[0.06em] text-muted uppercase">
						{m.maintenance_inbox_unsorted_table()}
					</h3>
					{#if unsortedParked.length === 0}
						<p class="text-[13px] text-muted">{m.maintenance_inbox_parked_empty()}</p>
					{:else}
						<div class="overflow-x-auto rounded-cover border border-hairline bg-surface">
							<table class="w-full min-w-[420px] border-collapse text-left text-[13px]">
								<thead>
									<tr
										class="border-b border-hairline text-xs tracking-[0.06em] text-muted uppercase"
									>
										<th class="px-3 py-2 font-medium">{m.column_name()}</th>
										<th class="px-3 py-2 font-medium">{m.maintenance_column_reason()}</th>
										<th class="px-3 py-2 text-right font-medium">{m.maintenance_column_size()}</th>
										<th class="px-3 py-2 text-right font-medium"
											>{m.maintenance_column_modified()}</th
										>
									</tr>
								</thead>
								<tbody>
									{#each unsortedParked as file (file.name)}
										<tr class="border-b border-hairline last:border-b-0">
											<td class="px-3 py-2 font-mono text-xs break-all">{file.name}</td>
											<td class="px-3 py-2 text-muted">{file.reason ?? ''}</td>
											<td class="tabular px-3 py-2 text-right text-muted">
												{formatSize(file.size, locale.intl)}
											</td>
											<td class="tabular px-3 py-2 text-right text-muted">
												{formatWhen(file.modified, locale.intl)}
											</td>
										</tr>
									{/each}
								</tbody>
							</table>
						</div>
					{/if}
				</div>

				<div class="grid gap-2">
					<h3 class="text-xs tracking-[0.06em] text-muted uppercase">
						{m.maintenance_inbox_duplicates_table()}
					</h3>
					{#if duplicateParked.length === 0}
						<p class="text-[13px] text-muted">{m.maintenance_inbox_parked_empty()}</p>
					{:else}
						<div class="overflow-x-auto rounded-cover border border-hairline bg-surface">
							<table class="w-full min-w-[280px] border-collapse text-left text-[13px]">
								<thead>
									<tr
										class="border-b border-hairline text-xs tracking-[0.06em] text-muted uppercase"
									>
										<th class="px-3 py-2 font-medium">{m.column_name()}</th>
										<th class="px-3 py-2 font-medium">{m.maintenance_column_reason()}</th>
									</tr>
								</thead>
								<tbody>
									{#each duplicateParked as file (file.name)}
										<tr class="border-b border-hairline last:border-b-0">
											<td class="px-3 py-2 font-mono text-xs break-all">{file.name}</td>
											<td class="px-3 py-2 text-muted">{file.reason ?? ''}</td>
										</tr>
									{/each}
								</tbody>
							</table>
						</div>
					{/if}
				</div>
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
	{#await data.gaps}
		<h2 class="opsz-title font-serif text-2xl font-semibold">{m.maintenance_gaps_heading()}</h2>
		<Skeleton width="100%" height="140px" />
	{:then gaps}
		<h2 class="opsz-title font-serif text-2xl font-semibold">{m.maintenance_gaps_heading()}</h2>
		{#if gaps.length === 0}
			<p class="text-muted">{m.maintenance_gaps_empty()}</p>
		{:else}
			<div class="grid gap-4">
				{#each gaps as title (title.title_id)}
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
												count: formatNumber(
													title.date_gap_count - title.date_gaps.length,
													locale.intl
												)
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
				{/each}
			</div>
		{/if}
	{:catch error}
		<ErrorState {error} />
	{/await}
</section>
