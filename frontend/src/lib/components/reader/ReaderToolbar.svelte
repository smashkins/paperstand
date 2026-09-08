<script lang="ts">
	import { resolve } from '$app/paths';
	import { formatIssueLabel, formatNumber, formatPageOf } from '$lib/format';
	import { m } from '$lib/paraglide/messages.js';
	import { locale } from '$lib/stores/locale.svelte';
	import type { IssueDetail } from '$lib/api/client';
	import type { Layout } from '$lib/reader/spreads';
	import Icon, { type IconName } from '$lib/components/ui/Icon.svelte';

	/**
	 * Everything the reader can be told to do, in two bars that get out of the
	 * way.
	 *
	 * They sit over the page rather than beside it — a newspaper wants the whole
	 * screen — and the reader hides them after a few seconds of nothing
	 * happening. Which is why every one of these buttons also has a key: the
	 * `?` panel is not documentation, it is how the controls stay reachable
	 * once they are invisible.
	 */
	interface Props {
		issue: IssueDetail;
		page: number;
		pageCount: number;
		layout: Layout;
		zoom: number;
		visible: boolean;
		thumbsOpen: boolean;
		shortcutsOpen: boolean;
		fullscreen: boolean;
		canPrev: boolean;
		canNext: boolean;
		onback: () => void;
		onprev: () => void;
		onnext: () => void;
		onlayout: () => void;
		onzoom: (factor: number) => void;
		onzoomreset: () => void;
		onthumbs: () => void;
		onshortcuts: () => void;
		onfullscreen: () => void;
	}

	let {
		issue,
		page,
		pageCount,
		layout,
		zoom,
		visible,
		thumbsOpen,
		shortcutsOpen,
		fullscreen,
		canPrev,
		canNext,
		onback,
		onprev,
		onnext,
		onlayout,
		onzoom,
		onzoomreset,
		onthumbs,
		onshortcuts,
		onfullscreen
	}: Props = $props();

	const counter = $derived(formatPageOf(page, pageCount, locale.intl));
	const label = $derived(formatIssueLabel(issue, locale.intl));
	const zoomLabel = $derived(
		m.reader_zoom_level({ percent: formatNumber(Math.round(zoom * 100), locale.intl) })
	);

	/** The `?` panel, as pairs of keys and what they do. */
	const shortcuts: { keys: () => string; what: () => string }[] = [
		{ keys: m.reader_keys_turn, what: m.reader_shortcut_turn },
		{ keys: m.reader_keys_ends, what: m.reader_shortcut_ends },
		{ keys: m.reader_keys_zoom, what: m.reader_shortcut_zoom },
		{ keys: m.reader_keys_thumbs, what: m.reader_shortcut_thumbs },
		{ keys: m.reader_keys_fullscreen, what: m.reader_shortcut_fullscreen },
		{ keys: m.reader_keys_close, what: m.reader_shortcut_close }
	];

	const BUTTON =
		'inline-grid h-9 w-9 place-content-center rounded-cover border border-transparent text-ink/85 transition-colors hover:border-hairline hover:text-ink disabled:opacity-35 disabled:hover:border-transparent';
</script>

{#snippet control(
	name: IconName,
	title: string,
	action: () => void,
	options: { disabled?: boolean; pressed?: boolean } = {}
)}
	<button
		type="button"
		class={BUTTON}
		class:border-hairline={options.pressed}
		class:bg-bg={options.pressed}
		{title}
		aria-label={title}
		aria-pressed={options.pressed === undefined ? undefined : options.pressed}
		disabled={options.disabled ?? false}
		onclick={action}
	>
		<Icon {name} size={17} />
	</button>
{/snippet}

<!-- The bars are always in the tree so that a screen reader keeps them; only
     their opacity and their reachability change. -->
<div
	class="pointer-events-none fixed inset-x-0 top-0 z-20 transition-opacity duration-200"
	class:opacity-0={!visible}
	inert={!visible}
>
	<div
		class="pointer-events-auto flex items-center gap-3 border-b border-hairline bg-surface/92 px-3 py-2 backdrop-blur"
	>
		<button
			type="button"
			class="inline-flex items-center gap-1.5 rounded-cover border border-transparent px-2 py-1.5 text-[13px] text-ink/85 transition-colors hover:border-hairline hover:text-ink"
			onclick={onback}
			title={m.reader_back()}
		>
			<Icon name="left" size={15} />
			<span class="sr-only sm:not-sr-only">{m.reader_back()}</span>
		</button>

		<div class="min-w-0 flex-1">
			<p class="opsz-text truncate font-serif text-[15px] font-semibold">{issue.title_name}</p>
			<p class="tabular truncate text-[12px] text-muted">{label}</p>
		</div>

		<div class="flex items-center gap-0.5">
			{#if issue.prev_issue_id}
				<a
					href={resolve('/read/[issueId]', { issueId: issue.prev_issue_id })}
					class={BUTTON}
					title={m.reader_prev_issue()}
					aria-label={m.reader_prev_issue()}
				>
					<Icon name="left" size={17} />
				</a>
			{/if}
			{#if issue.next_issue_id}
				<a
					href={resolve('/read/[issueId]', { issueId: issue.next_issue_id })}
					class={BUTTON}
					title={m.reader_next_issue()}
					aria-label={m.reader_next_issue()}
				>
					<Icon name="right" size={17} />
				</a>
			{/if}

			{@render control(
				layout === 'double' ? 'page_double' : 'page_single',
				m.reader_layout(),
				onlayout
			)}
			{@render control('thumbs', m.reader_thumbnails(), onthumbs, { pressed: thumbsOpen })}
			{@render control(
				fullscreen ? 'fullscreen_exit' : 'fullscreen',
				fullscreen ? m.reader_fullscreen_exit() : m.reader_fullscreen(),
				onfullscreen,
				{ pressed: fullscreen }
			)}

			<div class="relative">
				{@render control('help', m.reader_shortcuts(), onshortcuts, { pressed: shortcutsOpen })}
				{#if shortcutsOpen}
					<div
						class="absolute right-0 z-30 mt-1 w-[15.5rem] rounded-cover border border-hairline bg-surface p-3 shadow-cover-lift"
						role="dialog"
						aria-label={m.reader_shortcuts()}
					>
						<dl class="grid grid-cols-[auto_1fr] items-baseline gap-x-3 gap-y-1.5 text-[12px]">
							{#each shortcuts as shortcut (shortcut.what())}
								<dt
									class="tabular rounded-[3px] border border-hairline bg-bg px-1.5 py-0.5 text-center whitespace-nowrap"
								>
									{shortcut.keys()}
								</dt>
								<dd class="text-muted">{shortcut.what()}</dd>
							{/each}
						</dl>
					</div>
				{/if}
			</div>
		</div>
	</div>
</div>

<div
	class="pointer-events-none fixed inset-x-0 bottom-0 z-20 transition-opacity duration-200"
	class:opacity-0={!visible}
	inert={!visible}
>
	<div
		class="pointer-events-auto flex items-center justify-center gap-1 border-t border-hairline bg-surface/92 px-3 py-2 backdrop-blur"
		aria-label={m.reader_controls()}
		role="group"
	>
		{@render control('left', m.reader_prev_page(), onprev, { disabled: !canPrev })}
		<p class="tabular min-w-[6.5rem] text-center text-[13px] sm:min-w-[7.5rem]" aria-live="polite">
			{counter}
		</p>
		{@render control('right', m.reader_next_page(), onnext, { disabled: !canNext })}

		<span class="mx-1 h-5 w-px bg-hairline sm:mx-2" aria-hidden="true"></span>

		{@render control('zoom_out', m.reader_zoom_out(), () => onzoom(1 / 1.25), {
			disabled: zoom <= 1
		})}
		<!-- The percentage is the first thing to go on a narrow phone: the two
		     magnifiers already say which way the zoom is about to move. -->
		<p class="tabular hidden w-[3.25rem] text-center text-[12px] text-muted sm:block">
			{zoomLabel}
		</p>
		{@render control('zoom_in', m.reader_zoom_in(), () => onzoom(1.25))}
		{@render control('zoom_fit', m.reader_zoom_fit(), onzoomreset, { disabled: zoom === 1 })}
	</div>
</div>
