<script lang="ts">
	import { resolve } from '$app/paths';
	import type { Issue } from '$lib/api/client';
	import { coverAspect, formatIssueLabel, formatWhen, pluralise } from '$lib/format';
	import { daysUntilForgotten } from '$lib/maintenance';
	import { m } from '$lib/paraglide/messages.js';
	import { locale } from '$lib/stores/locale.svelte';
	import Cover from '$lib/components/covers/Cover.svelte';

	/** One catalogued issue whose file has gone missing from the library. */
	interface Props {
		issue: Issue;
		graceDays: number;
	}

	let { issue, graceDays }: Props = $props();
</script>

<li class="flex gap-3 rounded-cover border border-hairline bg-surface p-3">
	<Cover
		src={issue.thumb_url}
		alt={issue.title_name}
		aspect={coverAspect(issue.aspect)}
		class="w-16 shrink-0"
	/>
	<div class="grid min-w-0 gap-0.5 text-[13px]">
		<a class="font-medium hover:underline" href={resolve('/title/[id]', { id: issue.title_id })}>
			{issue.title_name}
		</a>
		<span class="text-muted">{formatIssueLabel(issue, locale.intl)}</span>
		<span class="font-mono text-xs break-all text-muted">{issue.rel_path}</span>
		{#if issue.missing_since}
			{@const remaining = daysUntilForgotten(issue.missing_since, graceDays)}
			<span class="tabular text-muted">
				{m.maintenance_missing_since({ when: formatWhen(issue.missing_since, locale.intl) })}
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
