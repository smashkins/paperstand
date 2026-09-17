<script lang="ts">
	import type { Issue } from '$lib/api/client';
	import { formatWhen } from '$lib/format';
	import { m } from '$lib/paraglide/messages.js';
	import { locale } from '$lib/stores/locale.svelte';

	/** One catalogued file whose cover could not be rendered. */
	interface Props {
		issue: Issue;
	}

	let { issue }: Props = $props();
</script>

<li class="grid min-w-0 gap-0.5 rounded-cover border border-hairline bg-surface p-3 text-[13px]">
	<span class="font-medium break-all">{issue.filename}</span>
	<span class="font-mono text-xs break-all text-muted">{issue.rel_path}</span>
	{#if issue.cover_status === 'error'}
		<span class="text-muted">{m.maintenance_unreadable_reason_error()}</span>
	{:else}
		<span class="text-muted">{m.maintenance_unreadable_reason_unavailable()}</span>
	{/if}
	{#if issue.cover_error}
		<span class="wrap-anywhere text-muted">{issue.cover_error}</span>
	{/if}
	<span class="tabular text-muted">
		{m.added_at({ when: formatWhen(issue.added_at, locale.intl) })}
	</span>
</li>
