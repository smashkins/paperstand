<script lang="ts">
	import { invalidateAll } from '$app/navigation';
	import { ApiError } from '$lib/api/client';
	import { m } from '$lib/paraglide/messages.js';
	import Button from './Button.svelte';
	import Icon from './Icon.svelte';

	/**
	 * A request that failed, in place of the content it would have produced.
	 *
	 * Retrying re-runs the route's `load`, which is the only thing that can
	 * actually fix it, so the button does that rather than reloading the page.
	 */
	interface Props {
		error: unknown;
	}

	let { error }: Props = $props();

	let retrying = $state(false);

	const detail = $derived.by(() => {
		if (error instanceof ApiError) {
			// A status of 0 means the request never reached a server at all.
			if (error.status === 0) return m.error_unreachable();
			return error.detail ?? `HTTP ${error.status}`;
		}
		if (error instanceof Error) return error.message;
		return m.error_unreachable();
	});

	async function retry() {
		retrying = true;
		try {
			await invalidateAll();
		} finally {
			retrying = false;
		}
	}
</script>

<div
	class="flex flex-col items-start gap-3 rounded-cover border border-accent/35 bg-surface px-6 py-8"
	role="alert"
>
	<p class="opsz-title flex items-center gap-2 font-serif text-lg font-semibold">
		<Icon name="alert" size={18} class="text-accent-text" />
		{m.error_title()}
	</p>
	<p class="max-w-[60ch] text-muted">{detail}</p>
	<Button variant="primary" onclick={retry} disabled={retrying} class="mt-1">
		<Icon name="refresh" size={14} />
		{m.error_retry()}
	</Button>
</div>
