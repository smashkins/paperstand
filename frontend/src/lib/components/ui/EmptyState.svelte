<script lang="ts">
	import type { ResolvedPathname } from '$app/types';
	import Button from './Button.svelte';

	/** Nothing to show, and what to do about it. */
	interface Props {
		title: string;
		body?: string;
		actionLabel?: string;
		actionHref?: ResolvedPathname;
	}

	let { title, body, actionLabel, actionHref }: Props = $props();
</script>

<div
	class="flex flex-col items-start gap-3 rounded-cover border border-dashed border-hairline bg-surface px-6 py-10"
>
	<h2 class="opsz-title font-serif text-xl font-semibold">{title}</h2>
	{#if body}
		<p class="max-w-[60ch] text-muted">{body}</p>
	{/if}
	{#if actionLabel && actionHref}
		<Button href={actionHref} variant="secondary" class="mt-1">{actionLabel}</Button>
	{/if}
</div>
