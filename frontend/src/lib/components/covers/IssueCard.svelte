<script lang="ts">
	import { resolve } from '$app/paths';
	import { isUnsortedIssue, type Issue } from '$lib/api/client';
	import {
		coverAspect,
		formatIssueLabel,
		formatPageOf,
		formatWhen,
		progressPercent
	} from '$lib/format';
	import { m } from '$lib/paraglide/messages.js';
	import { locale } from '$lib/stores/locale.svelte';
	import { coverToken, coverTransition } from '$lib/stores/transition.svelte';
	import Badge from '$lib/components/ui/Badge.svelte';
	import Cover from './Cover.svelte';

	/**
	 * One issue on a shelf: its cover, its name and one line of context.
	 *
	 * What that line says depends on where the card is. On the Today shelf it is
	 * the issue's own label, on "Continue reading" it is how far in the reader
	 * got, and on "Recently added" it is when the scan found it.
	 *
	 * An issue in the `Unsorted` bucket is named by its file instead: every card
	 * there would otherwise read "Unsorted", which identifies nothing.
	 */
	interface Props {
		issue: Issue;
		/** A fixed card width, e.g. `226px`; omit inside a grid. */
		width?: string;
		/** What the second line carries. */
		secondary?: 'label' | 'progress' | 'added';
		/** Warn when the date was guessed, or the title could not be worked out. */
		showChips?: boolean;
		eager?: boolean;
	}

	let { issue, width, secondary = 'label', showChips = false, eager = false }: Props = $props();

	/**
	 * This card's own identity, not the issue's.
	 *
	 * The same issue is often on two shelves at once, and two elements carrying
	 * the same `view-transition-name` make the browser skip the transition.
	 */
	const token = coverToken('card');

	const percent = $derived(
		issue.progress
			? progressPercent(issue.progress.page, issue.progress.page_count ?? issue.page_count)
			: null
	);

	const line = $derived.by(() => {
		if (secondary === 'progress' && issue.progress) {
			return formatPageOf(
				issue.progress.page,
				issue.progress.page_count ?? issue.page_count,
				locale.intl
			);
		}
		if (secondary === 'added') {
			return m.added_at({ when: formatWhen(issue.added_at, locale.intl) });
		}
		return formatIssueLabel(issue, locale.intl);
	});

	// `folder` and `mtime` mean the date was inferred, not read: the chip says so.
	const guessed = $derived(issue.date_source === 'mtime' || issue.date_source === 'folder');

	const unsorted = $derived(isUnsortedIssue(issue));

	/** The file name without its extension. */
	const stem = $derived(issue.filename.replace(/\.[^.]+$/, ''));
	const name = $derived(unsorted ? stem : issue.title_name);
</script>

<a
	class="group grid min-w-0 gap-2.5"
	class:w-full={!width}
	style:width
	href={resolve('/read/[issueId]', { issueId: issue.id })}
	onclick={() => coverTransition.claim(token)}
>
	<Cover
		src={issue.thumb_url}
		alt={name}
		aspect={coverAspect(issue.aspect)}
		progress={percent}
		transitionName={coverTransition.nameFor(token, issue.id)}
		{eager}
	/>
	<span class="grid gap-0.5">
		<!-- `wrap-anywhere`, not `break-words`: an unsorted issue is named by its
		     file, and one long underscored name would otherwise set the grid
		     column's minimum width and blow the whole wall out. -->
		<span
			class="opsz-text font-serif text-[14.5px] leading-tight font-semibold wrap-anywhere"
			title={name}
		>
			{name}
		</span>
		<span class="tabular text-[12.5px] text-muted">{line}</span>
		{#if showChips}
			<span class="mt-0.5 flex flex-wrap gap-1">
				{#if unsorted}
					<Badge>{m.chip_unsorted()}</Badge>
				{/if}
				{#if guessed}
					<Badge variant="warning" title={m.chip_date_guessed_title()}>
						{m.chip_date_guessed()}
					</Badge>
				{/if}
				{#if issue.is_duplicate}
					<Badge>{m.chip_duplicate()}</Badge>
				{/if}
			</span>
		{/if}
	</span>
</a>
