<script lang="ts">
	import { goto } from '$app/navigation';
	import { resolve } from '$app/paths';
	import { api, type Calendar } from '$lib/api/client';
	import { latestMonth, monthGrid, shiftMonth, weekdayNames } from '$lib/calendar';
	import { capitalise, formatIssueDate } from '$lib/format';
	import { m } from '$lib/paraglide/messages.js';
	import { locale } from '$lib/stores/locale.svelte';
	import { coverTransition } from '$lib/stores/transition.svelte';
	import Icon from '$lib/components/ui/Icon.svelte';
	import InlineError from '$lib/components/ui/InlineError.svelte';

	/**
	 * A year of a daily, one month at a time.
	 *
	 * A day that has an issue shows its thumbnail, so the grid reads as a wall
	 * of tiny front pages rather than a table of numbers. It is a real grid
	 * widget: one tab stop, arrow keys to move — across a month boundary if you
	 * keep going — and Enter to open.
	 *
	 * Every year that is not the one the page loaded with has to be fetched, and
	 * those fetches happen inside DOM handlers. A rejection there would go
	 * unobserved, so `fetchYear` never throws: it records the failure in `error`,
	 * the month you were looking at stays on screen, and an inline retry asks for
	 * the same year again.
	 */
	interface Props {
		titleId: string;
		initial: Calendar;
	}

	let { titleId, initial }: Props = $props();

	/** A year fetched after the one the page loaded with, if any. */
	let fetched = $state<Calendar | null>(null);
	/** The month being looked at, once it stops being the one we opened on. */
	let view = $state<{ year: number; month: number } | null>(null);
	let loading = $state(false);
	let error = $state<unknown>(null);
	/** The year a failed fetch was after, so the retry knows what to ask for. */
	let failedYear = $state<number | null>(null);
	/** The day the roving tabindex sits on. */
	let focused = $state<string | null>(null);

	const calendar = $derived(fetched ?? initial);
	const year = $derived(view?.year ?? calendar.year);
	// A title's newest issues are the ones worth opening on.
	const month = $derived(view?.month ?? latestMonth(calendar.days) ?? 12);

	const cells = $derived(monthGrid(year, month));
	const weekdays = $derived(weekdayNames(locale.intl, 1, 'short'));
	const monthLabel = $derived(
		capitalise(
			formatIssueDate(`${year}-${String(month).padStart(2, '0')}-01`, locale.intl, {
				month: 'long',
				year: 'numeric'
			})
		)
	);

	/** Every year the title has issues in, newest first. */
	const years = $derived(calendar.years.map((entry) => entry.year));

	/** The tab stop: the focused day, else the first day of the month that has one. */
	const tabStop = $derived(
		focused ??
			cells.find((cell) => cell.inMonth && calendar.days[cell.iso])?.iso ??
			cells.find((cell) => cell.inMonth)?.iso ??
			null
	);

	/**
	 * The calendar for a year, or `null` when it could not be fetched.
	 *
	 * It resolves rather than rejects on purpose: every caller is a DOM handler
	 * whose returned promise nobody observes.
	 */
	async function fetchYear(next: number): Promise<Calendar | null> {
		if (next === calendar.year) return calendar;
		loading = true;
		error = null;
		try {
			fetched = await api.calendar(titleId, next);
			failedYear = null;
			return fetched;
		} catch (reason) {
			error = reason;
			failedYear = next;
			return null;
		} finally {
			loading = false;
		}
	}

	/** The year picker: jump to a year and open on its newest month. */
	async function selectYear(next: number) {
		const wanted = await fetchYear(next);
		if (wanted === null) return;
		view = { year: wanted.year, month: latestMonth(wanted.days) ?? 12 };
		focused = null;
	}

	async function step(delta: number) {
		const next = shiftMonth(year, month, delta);
		if (next.year !== year) {
			if (!years.includes(next.year)) return;
			if ((await fetchYear(next.year)) === null) return;
		}
		view = next;
		focused = null;
	}

	/** Ask for the year that failed again, from the same place in the interface. */
	function retry() {
		if (failedYear === null) return;
		void selectYear(failedYear);
	}

	/**
	 * The transition token of one day's cell.
	 *
	 * A calendar renders each day once, so the day itself is already a unique
	 * identity — no counter needed.
	 */
	function dayToken(iso: string): string {
		return `cal-${iso}`;
	}

	function open(iso: string) {
		const issueId = calendar.days[iso];
		if (!issueId) return;
		coverTransition.claim(dayToken(iso));
		// A navigation that fails is the router's business, not a rejection for
		// nobody to catch.
		void goto(resolve('/read/[issueId]', { issueId })).catch(() => {});
	}

	function dayLabel(iso: string): string {
		const date = formatIssueDate(iso, locale.intl);
		return calendar.days[iso] ? m.calendar_day_issue({ date }) : m.calendar_day_empty({ date });
	}

	/** Move the focus by `delta` days, carrying into the neighbouring month. */
	async function move(from: string, delta: number) {
		const stamp = Date.UTC(
			Number(from.slice(0, 4)),
			Number(from.slice(5, 7)) - 1,
			Number(from.slice(8, 10)) + delta
		);
		const next = new Date(stamp);
		const nextIso = next.toISOString().slice(0, 10);
		const nextYear = next.getUTCFullYear();
		const nextMonth = next.getUTCMonth() + 1;
		if (nextYear !== year) {
			if (!years.includes(nextYear)) return;
			if ((await fetchYear(nextYear)) === null) return;
		}
		view = { year: nextYear, month: nextMonth };
		focused = nextIso;
		await Promise.resolve();
		const target = document.querySelector<HTMLElement>(`[data-day="${nextIso}"]`);
		target?.focus();
	}

	function onKeydown(event: KeyboardEvent, iso: string) {
		const steps: Record<string, number> = {
			ArrowLeft: -1,
			ArrowRight: 1,
			ArrowUp: -7,
			ArrowDown: 7,
			PageUp: -28,
			PageDown: 28
		};
		if (event.key in steps) {
			event.preventDefault();
			// `move` resolves even when the fetch inside it fails.
			void move(iso, steps[event.key]);
			return;
		}
		if (event.key === 'Enter' || event.key === ' ') {
			event.preventDefault();
			open(iso);
		}
	}
</script>

<!-- Capped, because seven columns of a page-shaped cell across a desktop
     window is a wall poster, not a calendar. -->
<section class="grid w-full max-w-[520px] gap-4">
	<h2 class="opsz-title font-serif text-2xl font-semibold tracking-tight">
		{m.title_calendar()}
	</h2>

	<div class="flex items-center gap-2">
		<label class="sr-only" for="calendar-year">{m.calendar_year()}</label>
		<select
			id="calendar-year"
			class="tabular h-[30px] rounded-cover border border-hairline bg-surface px-2 text-xs font-medium"
			value={year}
			onchange={(event) => void selectYear(Number(event.currentTarget.value))}
		>
			{#each years as option (option)}
				<option value={option}>{option}</option>
			{/each}
		</select>
		<button
			type="button"
			class="grid h-[30px] w-[30px] place-items-center rounded-cover border border-hairline bg-surface transition-colors hover:border-accent"
			aria-label={m.calendar_prev_month()}
			onclick={() => void step(-1)}
		>
			<Icon name="left" size={14} />
		</button>
		<span class="tabular flex-1 text-center text-sm font-medium">{monthLabel}</span>
		<button
			type="button"
			class="grid h-[30px] w-[30px] place-items-center rounded-cover border border-hairline bg-surface transition-colors hover:border-accent"
			aria-label={m.calendar_next_month()}
			onclick={() => void step(1)}
		>
			<Icon name="right" size={14} />
		</button>
	</div>

	{#if error !== null}
		<InlineError {error} onretry={retry} retrying={loading} />
	{/if}

	<div
		class="grid grid-cols-7 gap-1.5 rounded-cover border border-hairline bg-surface p-3 sm:gap-2 sm:p-4"
		class:opacity-50={loading}
		role="grid"
		aria-label={m.title_calendar()}
		aria-busy={loading}
	>
		{#each weekdays as name, index (index)}
			<div
				class="pb-1 text-center text-[10.5px] font-medium tracking-[0.06em] text-muted uppercase"
				role="columnheader"
				aria-label={name}
			>
				{name}
			</div>
		{/each}

		{#each cells as cell (cell.iso)}
			{@const issueId = cell.inMonth ? calendar.days[cell.iso] : undefined}
			<div role="gridcell" class="min-w-0">
				<button
					type="button"
					data-day={cell.iso}
					class="relative block w-full overflow-hidden rounded-cover border transition-colors"
					class:border-hairline={!issueId}
					class:border-transparent={!!issueId}
					class:opacity-30={!cell.inMonth}
					class:cursor-default={!issueId}
					style:aspect-ratio="0.72"
					tabindex={cell.iso === tabStop ? 0 : -1}
					disabled={!cell.inMonth}
					aria-label={dayLabel(cell.iso)}
					onclick={() => open(cell.iso)}
					onfocus={() => (focused = cell.iso)}
					onkeydown={(event) => onKeydown(event, cell.iso)}
				>
					{#if issueId}
						<img
							src="/api/issues/{issueId}/thumb.jpg"
							alt=""
							loading="lazy"
							decoding="async"
							class="absolute inset-0 h-full w-full object-cover"
							style:view-transition-name={coverTransition.nameFor(dayToken(cell.iso), issueId)}
						/>
						<span
							class="tabular absolute inset-x-0 bottom-0 bg-[color-mix(in_srgb,var(--paperstand-ink)_70%,transparent)] py-px text-center text-[10px] font-semibold text-bg"
						>
							{cell.day}
						</span>
					{:else}
						<span class="tabular absolute inset-0 grid place-items-center text-[11px] text-muted">
							{cell.day}
						</span>
					{/if}
				</button>
			</div>
		{/each}
	</div>

	<p class="text-xs text-muted">{m.calendar_hint()}</p>
</section>
