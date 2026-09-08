<script lang="ts">
	import { resolve } from '$app/paths';
	import type { TitleDetail } from '$lib/api/client';
	import { coverAspect, formatTitleSpan, pluralise, progressPercent } from '$lib/format';
	import { m } from '$lib/paraglide/messages.js';
	import { locale } from '$lib/stores/locale.svelte';
	import { coverToken, coverTransition } from '$lib/stores/transition.svelte';
	import Cover from '$lib/components/covers/Cover.svelte';
	import Badge from '$lib/components/ui/Badge.svelte';
	import Button from '$lib/components/ui/Button.svelte';

	/** The top of a title's page: the latest cover, the counts, and a way in. */
	interface Props {
		title: TitleDetail;
	}

	let { title }: Props = $props();

	/** The header's own cover instance; "Read latest" claims it before it goes. */
	const token = coverToken('header');

	const latest = $derived(title.latest_issue);

	const issues = $derived(
		pluralise(title.issue_count, locale.intl, m.issue_count_one, m.issue_count_other)
	);

	const range = $derived(
		formatTitleSpan(
			title.first_date,
			title.last_date,
			title.latest_issue?.date_precision ?? 'year',
			locale.intl
		)
	);

	const guessed = $derived(latest?.date_source === 'mtime' || latest?.date_source === 'folder');

	const percent = $derived(
		latest?.progress
			? progressPercent(latest.progress.page, latest.progress.page_count ?? latest.page_count)
			: null
	);
</script>

<header class="flex flex-col gap-6 sm:flex-row sm:items-end sm:gap-8">
	<div class="w-[152px] shrink-0 sm:w-[210px]">
		<Cover
			src={latest?.thumb_url}
			alt={title.name}
			aspect={coverAspect(latest?.aspect)}
			progress={percent}
			transitionName={latest ? coverTransition.nameFor(token, latest.id) : undefined}
			eager
		/>
	</div>

	<div class="grid gap-3 sm:pb-1.5">
		<div class="flex flex-wrap items-center gap-2">
			<Badge>{title.kind === 'newspaper' ? m.kind_newspaper() : m.kind_magazine()}</Badge>
			{#if title.source === 'unsorted'}
				<Badge variant="warning">{m.chip_unsorted()}</Badge>
			{/if}
			{#if guessed}
				<Badge variant="warning" title={m.chip_date_guessed_title()}>
					{m.chip_date_guessed()}
				</Badge>
			{/if}
		</div>

		<h1
			class="opsz-display font-serif text-[clamp(30px,5vw,52px)] leading-[1.05] font-semibold tracking-[-0.02em] text-balance"
		>
			{title.name}
		</h1>

		<p class="tabular text-muted">
			{range ? `${issues} · ${range}` : issues}
		</p>

		{#if latest}
			<div class="mt-1">
				<Button
					variant="primary"
					href={resolve('/read/[issueId]', { issueId: latest.id })}
					onclick={() => coverTransition.claim(token)}
				>
					{m.read_latest()}
				</Button>
			</div>
		{/if}
	</div>
</header>
