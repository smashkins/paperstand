<script lang="ts">
	import { ApiError, api, type ScanRecord, type ScanStatus } from '$lib/api/client';
	import { formatDateTime, formatNumber, formatSize } from '$lib/format';
	import { m } from '$lib/paraglide/messages.js';
	import { locales, type Locale } from '$lib/paraglide/runtime';
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
	import Skeleton from '$lib/components/ui/Skeleton.svelte';
	import type { PageProps } from './$types';

	let { data }: PageProps = $props();

	/** The scan status, refreshed on its own once the page has its first one. */
	let scan = $state<ScanStatus | null>(null);
	/** The last finished scan's row, the only place the timestamps live. */
	let lastRun = $state<ScanRecord | null>(null);
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
				if (live) lastRun = health.last_scan;
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
				// The scan has just landed, so its row now carries a finish time.
				if (!status.running) lastRun = (await api.health()).last_scan;
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
			await api.scan();
			scanMessage = m.scan_started();
			scan = await api.scanStatus();
		} catch (error) {
			if (error instanceof ApiError && error.status === 409) {
				scanMessage = m.scan_already_running();
				scan = await api.scanStatus().catch(() => scan);
			} else {
				scanMessage = m.scan_failed();
			}
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
</script>

<svelte:head><title>{m.settings_title()} · {m.app_name()}</title></svelte:head>

<h1 class="opsz-title font-serif text-[clamp(28px,4vw,40px)] font-semibold tracking-[-0.02em]">
	{m.settings_title()}
</h1>

{#await data.stats}
	<Skeleton width="100%" height="200px" />
	<Skeleton width="100%" height="140px" />
{:then stats}
	<section class="grid gap-4">
		<h2 class="opsz-title font-serif text-2xl font-semibold">{m.settings_libraries()}</h2>
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
				<span class="flex items-center gap-2 font-medium">
					{#if scan?.running}
						<span class="inline-block h-2 w-2 rounded-full bg-accent" aria-hidden="true"></span>
						{m.scan_running()}
					{:else}
						{m.scan_idle()}
					{/if}
				</span>
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
							removed: formatNumber(summary.removed, locale.intl)
						})}
					</span>
					{#if summary.errors}
						<span class="tabular text-accent-text">
							{m.scan_errors({ count: formatNumber(summary.errors, locale.intl) })}
						</span>
					{/if}
				{:else if !lastRun}
					<span class="text-muted">{m.scan_never()}</span>
				{/if}
			</div>

			{#if scanMessage}
				<p class="w-full text-[13px] text-muted" role="status">{scanMessage}</p>
			{/if}
		</div>
	</section>

	<section class="grid gap-4">
		<h2 class="opsz-title font-serif text-2xl font-semibold">{m.settings_storage()}</h2>
		<dl class="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
			{#each [{ label: m.stat_titles(), value: formatNumber(stats.title_count, locale.intl) }, { label: m.stat_issues(), value: formatNumber(stats.issue_count, locale.intl) }, { label: m.stat_duplicates(), value: formatNumber(stats.duplicate_count, locale.intl) }, { label: m.stat_covers(), value: formatSize(stats.covers_bytes, locale.intl) }, { label: m.stat_pages(), value: formatSize(stats.pages_bytes, locale.intl) }, { label: m.stat_db(), value: formatSize(stats.db_bytes, locale.intl) }] as stat (stat.label)}
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
