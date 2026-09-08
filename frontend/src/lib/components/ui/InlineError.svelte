<script lang="ts">
	import { ApiError } from '$lib/api/client';
	import { m } from '$lib/paraglide/messages.js';
	import Button from './Button.svelte';
	import Icon from './Icon.svelte';

	/**
	 * A failure inside a page that is otherwise fine.
	 *
	 * `ErrorState` replaces a whole route and retries by re-running its `load`.
	 * This one sits next to the content that did arrive — a title's header, the
	 * month you are already looking at — and hands the retry back to whoever
	 * knows what to try again.
	 */
	interface Props {
		error: unknown;
		onretry: () => void;
		retrying?: boolean;
	}

	let { error, onretry, retrying = false }: Props = $props();

	const detail = $derived.by(() => {
		if (error instanceof ApiError) {
			// A status of 0 means the request never reached a server at all.
			if (error.status === 0) return m.error_unreachable();
			return error.detail ?? `HTTP ${error.status}`;
		}
		if (error instanceof Error) return error.message;
		return m.error_unreachable();
	});
</script>

<div
	class="flex flex-wrap items-center gap-x-3 gap-y-2 rounded-cover border border-accent/35 bg-surface px-4 py-3"
	role="alert"
>
	<Icon name="alert" size={16} class="text-accent-text" />
	<span class="text-[13px] font-medium">{m.error_title()}</span>
	<span class="text-[13px] text-muted">{detail}</span>
	<Button variant="secondary" onclick={onretry} disabled={retrying} class="ml-auto">
		<Icon name="refresh" size={14} />
		{m.error_retry()}
	</Button>
</div>
