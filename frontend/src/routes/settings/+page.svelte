<script lang="ts">
	import { resolve } from '$app/paths';
	import { api, type ScanRecord, type ScanStatus } from '$lib/api/client';
	import { requestScan } from '$lib/api/scan';
	import { formatDateTime, formatElapsed, formatNumber, formatSize } from '$lib/format';
	import { m } from '$lib/paraglide/messages.js';
	import { locales, type Locale } from '$lib/paraglide/runtime';
	import { attention } from '$lib/stores/attention.svelte';
	import { locale } from '$lib/stores/locale.svelte';
	import {
		IMAGE_MODES,
		SPREAD_MODES,
		prefs,
		type ImageMode,
		type SpreadMode
	} from '$lib/stores/prefs.svelte';
	import { THEMES, theme, type Theme } from '$lib/stores/theme.svelte';
	import Button from '$lib/components/ui/Button.svelte';
	import ErrorState from '$lib/components/ui/ErrorState.svelte';
	import Icon from '$lib/components/ui/Icon.svelte';
	import ProgressBar from '$lib/components/ui/ProgressBar.svelte';
	import Skeleton from '$lib/components/ui/Skeleton.svelte';
	import type { PageProps } from './$types';

	let { data }: PageProps = $props();

	/** The scan status, refreshed on its own once the page has its first one. */
	let scan = $state<ScanStatus | null>(null);
	/** The last finished scan's row, the only place the timestamps live. */
	let lastRun = $state<ScanRecord | null>(null);
	/**
	 * The root marker's state: `null` reads as "not set up" until `/api/health`
	 * has actually answered, exactly like `lastRun` reads as "no scan yet" in
	 * the same window — both settle within one round trip.
	 */
	let libraryMarker = $state<boolean | null>(null);
	let scanMessage = $state<string | null>(null);
	let starting = $state(false);

	// The first status comes from the route's `load`; after that the page owns it.
	$effect(() => {
		let live = true;
		data.scan
			.then((status) => {
				if (live) scan = status;
			})
			.catch(() => {});
		data.health
			.then((health) => {
				if (live) {
					lastRun = health.last_scan;
					libraryMarker = health.library_marker;
				}
			})
			.catch(() => {});
		return () => {
			live = false;
		};
	});

	// While a scan runs the status is worth two seconds; the rest of the time
	// polling an idle scheduler is just noise in the log.
	$effect(() => {
		if (!scan?.running) return;
		const timer = setInterval(async () => {
			try {
				const status = await api.scanStatus();
				scan = status;
				// The scan has just landed, so its row now carries a finish time,
				// and the marker may have changed along with it — and so may the
				// maintenance badge, so the top bar and Maintenance stay current.
				if (!status.running) {
					const health = await api.health();
					lastRun = health.last_scan;
					libraryMarker = health.library_marker;
					attention.refresh();
				}
			} catch {
				// A blip is not worth showing: the next tick tries again.
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
			scan = await api.scanStatus();
		} catch {
			scanMessage = m.scan_failed();
		} finally {
			starting = false;
		}
	}

	const themeLabels: Record<Theme, () => string> = {
		system: m.theme_system,
		light: m.theme_light,
		dark: m.theme_dark
	};

	const spreadLabels: Record<SpreadMode, () => string> = {
		auto: m.spread_auto,
		single: m.spread_single,
		double: m.spread_double
	};

	const imageLabels: Record<ImageMode, () => string> = {
		client: m.images_client,
		server: m.images_server
	};

	const segment =
		'px-3 py-1.5 text-[13px] font-medium transition-colors border-r border-hairline last:border-r-0';

	const summary = $derived(scan?.last ?? null);

	// `null` reads as an indeterminate sweep: the catalogue phase's total is
	// never known until the walk is done, and `covers_total` is `null` until
	// the first `covers` snapshot. The numerator is done *and* failed covers:
	// a broken PDF still counts against the total, or the bar — and the "N of
	// M" beside it — would never reach it.
	const coversPercent = $derived.by(() => {
		const current = scan?.current;
		if (!current || current.phase !== 'covers' || !current.covers_total) return null;
		return ((current.covers_done + current.covers_failed) / current.covers_total) * 100;
	});
</script>

<svelte:head><title>{m.settings_title()} · {m.app_name()}</title></svelte:head>

<h1 class="opsz-title font-serif text-[clamp(28px,4vw,40px)] font-semibold tracking-[-0.02em]">
	{m.settings_title()}
</h1>

<a
	href={resolve('/maintenance')}
	class="flex items-center gap-3 rounded-cover border border-hairline bg-surface px-4 py-3 transition-colors hover:border-accent"
>
	<Icon name="maintenance" size={16} />
	<span class="font-medium">{m.nav_maintenance()}</span>
	{#if attention.count !== null}
		<span class="tabular ml-auto text-muted">{formatNumber(attention.count, locale.intl)}</span>
	{/if}
</a>

{#await data.stats}
	<Skeleton width="100%" height="200px" />
	<Skeleton width="100%" height="140px" />
{:then stats}
	<section class="grid gap-4">
		<h2 class="opsz-title font-serif text-2xl font-semibold">{m.settings_libraries()}</h2>
		<p class="text-[13px] text-muted">
			<span class="text-xs tracking-[0.06em] text-muted uppercase">{m.marker_label()}:</span>
			{#if libraryMarker === true}
				{m.marker_protecting()}
			{:else if libraryMarker === false}
				{m.marker_missing()}
			{:else}
				{m.marker_not_set_up()}
			{/if}
		</p>
		{#if stats.libraries.length === 0}
			<p class="text-muted">{m.libraries_none()}</p>
		{:else}
			<div class="overflow-x-auto rounded-cover border border-hairline bg-surface">
				<table class="w-full min-w-[640px] border-collapse text-left">
					<thead>
						<tr class="border-b border-hairline text-xs tracking-[0.06em] text-muted uppercase">
							<th class="px-4 py-2.5 font-medium">{m.column_name()}</th>
							<th class="px-4 py-2.5 font-medium">{m.column_kind()}</th>
							<th class="px-4 py-2.5 font-medium">{m.column_path()}</th>
							<th class="px-4 py-2.5 text-right font-medium">{m.column_titles()}</th>
							<th class="px-4 py-2.5 text-right font-medium">{m.column_issues()}</th>
							<th class="px-4 py-2.5 text-right font-medium">{m.column_unsorted()}</th>
						</tr>
					</thead>
					<tbody>
						{#each stats.libraries as library (library.id)}
							<tr class="border-b border-hairline last:border-b-0">
								<td class="px-4 py-2.5 font-medium">{library.name}</td>
								<td class="px-4 py-2.5 text-muted">
									{library.kind === 'newspaper' ? m.kind_newspaper() : m.kind_magazine()}
								</td>
								<td class="px-4 py-2.5 font-mono text-xs break-all text-muted">{library.path}</td>
								<td class="tabular px-4 py-2.5 text-right">
									{formatNumber(library.title_count, locale.intl)}
								</td>
								<td class="tabular px-4 py-2.5 text-right">
									{formatNumber(library.issue_count, locale.intl)}
								</td>
								<td class="tabular px-4 py-2.5 text-right text-muted">
									{formatNumber(library.unsorted_count, locale.intl)}
								</td>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
		{/if}
	</section>

	<section class="grid gap-4">
		<h2 class="opsz-title font-serif text-2xl font-semibold">{m.settings_scanning()}</h2>
		<div
			class="flex flex-wrap items-center gap-4 rounded-cover border border-hairline bg-surface p-4"
		>
			<Button variant="primary" onclick={rescan} disabled={starting || scan?.running}>
				<Icon name="refresh" size={14} />
				{m.scan_rescan()}
			</Button>

			<div class="grid gap-1 text-[13px]">
				{#if scan?.running && scan.current}
					{@const current = scan.current}
					<span class="flex items-center gap-2 font-medium">
						<span class="inline-block h-2 w-2 rounded-full bg-accent" aria-hidden="true"></span>
						{current.phase === 'covers' ? m.scan_phase_covers() : m.scan_phase_catalogue()}
					</span>
					<span class="tabular text-muted">
						{m.scan_summary({
							files: formatNumber(current.files_seen, locale.intl),
							added: formatNumber(current.added, locale.intl),
							updated: formatNumber(current.updated, locale.intl),
							removed: formatNumber(current.removed, locale.intl),
							missing: formatNumber(current.missing, locale.intl)
						})}
					</span>
					{#if current.errors}
						<span class="tabular text-accent-text">
							{m.scan_errors({ count: formatNumber(current.errors, locale.intl) })}
						</span>
					{/if}
					<span class="tabular text-muted">
						{m.scan_elapsed({ time: formatElapsed(current.elapsed) })}
					</span>
					<div class="mt-1 flex items-center gap-2">
						<ProgressBar percent={coversPercent} aria-label={m.scan_running()} class="max-w-56" />
						{#if current.phase === 'covers' && current.covers_total !== null}
							<span class="tabular text-xs whitespace-nowrap text-muted">
								{m.scan_covers_progress({
									done: formatNumber(current.covers_done + current.covers_failed, locale.intl),
									total: formatNumber(current.covers_total, locale.intl)
								})}
							</span>
						{/if}
					</div>
				{:else}
					<span class="flex items-center gap-2 font-medium">{m.scan_idle()}</span>
					{#if lastRun}
						<span class="tabular text-muted">
							{m.scan_last({
								when: formatDateTime(lastRun.finished_at ?? lastRun.started_at, locale.intl)
							})}
						</span>
					{/if}
					{#if summary}
						<span class="tabular text-muted">
							{m.scan_summary({
								files: formatNumber(summary.files_seen, locale.intl),
								added: formatNumber(summary.added, locale.intl),
								updated: formatNumber(summary.updated, locale.intl),
								removed: formatNumber(summary.removed, locale.intl),
								missing: formatNumber(summary.missing, locale.intl)
							})}
						</span>
						{#if summary.errors}
							<span class="tabular text-accent-text">
								{m.scan_errors({ count: formatNumber(summary.errors, locale.intl) })}
							</span>
						{/if}
						{#if summary.message}
							<span class="text-muted">{summary.message}</span>
						{/if}
					{:else if !lastRun}
						<span class="text-muted">{m.scan_never()}</span>
					{/if}
				{/if}
			</div>

			{#if scanMessage}
				<p class="w-full text-[13px] text-muted" role="status">{scanMessage}</p>
			{/if}
		</div>
	</section>

	<section class="grid gap-4">
		<h2 class="opsz-title font-serif text-2xl font-semibold">{m.settings_storage()}</h2>
		<dl class="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-7">
			{#each [{ label: m.stat_titles(), value: formatNumber(stats.title_count, locale.intl) }, { label: m.stat_issues(), value: formatNumber(stats.issue_count, locale.intl) }, { label: m.stat_duplicates(), value: formatNumber(stats.duplicate_count, locale.intl) }, { label: m.stat_missing(), value: formatNumber(stats.missing_count, locale.intl) }, { label: m.stat_covers(), value: formatSize(stats.covers_bytes, locale.intl) }, { label: m.stat_pages(), value: formatSize(stats.pages_bytes, locale.intl) }, { label: m.stat_db(), value: formatSize(stats.db_bytes, locale.intl) }] as stat (stat.label)}
				<div class="rounded-cover border border-hairline bg-surface px-4 py-3">
					<dt class="text-xs tracking-[0.06em] text-muted uppercase">{stat.label}</dt>
					<dd class="tabular opsz-text mt-1 font-serif text-xl font-semibold">{stat.value}</dd>
				</div>
			{/each}
		</dl>
	</section>
{:catch error}
	<ErrorState {error} />
{/await}

<section class="grid gap-4">
	<h2 class="opsz-title font-serif text-2xl font-semibold">{m.settings_appearance()}</h2>
	<div class="grid gap-4 rounded-cover border border-hairline bg-surface p-4 sm:grid-cols-2">
		<div class="grid gap-2">
			<span class="text-xs tracking-[0.06em] text-muted uppercase">{m.theme_label()}</span>
			<div
				class="inline-flex w-fit overflow-hidden rounded-cover border border-hairline"
				role="group"
				aria-label={m.theme_label()}
			>
				{#each THEMES as option (option)}
					<button
						type="button"
						class={segment}
						class:bg-ink={theme.theme === option}
						class:text-bg={theme.theme === option}
						aria-pressed={theme.theme === option}
						onclick={() => theme.set(option)}
					>
						{themeLabels[option]()}
					</button>
				{/each}
			</div>
		</div>

		<div class="grid gap-2">
			<span class="text-xs tracking-[0.06em] text-muted uppercase">{m.language_label()}</span>
			<div
				class="inline-flex w-fit overflow-hidden rounded-cover border border-hairline"
				role="group"
				aria-label={m.language_label()}
			>
				{#each locales as code (code)}
					<button
						type="button"
						class="{segment} uppercase"
						class:bg-ink={locale.current === code}
						class:text-bg={locale.current === code}
						aria-pressed={locale.current === code}
						onclick={() => locale.set(code as Locale)}
					>
						{code}
					</button>
				{/each}
			</div>
		</div>
	</div>
</section>

<section class="grid gap-4">
	<h2 class="opsz-title font-serif text-2xl font-semibold">{m.settings_reader()}</h2>
	<div class="grid gap-4 rounded-cover border border-hairline bg-surface p-4 sm:grid-cols-2">
		<div class="grid gap-2">
			<label class="text-xs tracking-[0.06em] text-muted uppercase" for="pref-spread">
				{m.pref_spread()}
			</label>
			<select
				id="pref-spread"
				class="w-fit rounded-cover border border-hairline bg-bg px-2.5 py-1.5 text-[13px]"
				value={prefs.spread}
				onchange={(event) => prefs.set('spread', event.currentTarget.value as SpreadMode)}
			>
				{#each SPREAD_MODES as option (option)}
					<option value={option}>{spreadLabels[option]()}</option>
				{/each}
			</select>
		</div>

		<div class="grid gap-2">
			<label class="text-xs tracking-[0.06em] text-muted uppercase" for="pref-images">
				{m.pref_images()}
			</label>
			<select
				id="pref-images"
				class="w-fit rounded-cover border border-hairline bg-bg px-2.5 py-1.5 text-[13px]"
				value={prefs.images}
				onchange={(event) => prefs.set('images', event.currentTarget.value as ImageMode)}
			>
				{#each IMAGE_MODES as option (option)}
					<option value={option}>{imageLabels[option]()}</option>
				{/each}
			</select>
		</div>

		<p class="text-[13px] text-muted sm:col-span-2">{m.settings_reader_note()}</p>
	</div>
</section>
