<script lang="ts">
	import { dev } from '$app/environment';
	import { resolve } from '$app/paths';
	import { page } from '$app/state';
	import { m } from '$lib/paraglide/messages.js';
	import Button from '$lib/components/ui/Button.svelte';
	import Icon from '$lib/components/ui/Icon.svelte';

	/**
	 * Anything the router could not render: an address that is not a route, and
	 * a `load` that threw where the route had no `ErrorState` of its own.
	 *
	 * A single page covers both because the difference to the reader is one line
	 * of copy — a 404 is "that page is not part of Paperstand", anything else is
	 * "something went wrong" — and both end the same way, with the road back to
	 * Today. It sits inside the storefront layout, so the bar, the tabs and the
	 * locale are the ones the reader already had.
	 *
	 * The body always comes from the message catalogue. What reaches here is a
	 * browser, framework or network error: English whatever the reader's
	 * language, phrased for whoever wrote the framework, and free to change with
	 * the next dependency bump. It is still worth having, so it is kept — behind
	 * a disclosure, and only while developing.
	 */
	const notFound = $derived(page.status === 404);
	const title = $derived(notFound ? m.not_found_title() : m.error_title());
	const body = $derived(notFound ? m.not_found_body() : m.error_body());
	const detail = $derived(dev ? (page.error?.message ?? null) : null);
</script>

<svelte:head><title>{title} · {m.app_name()}</title></svelte:head>

<section class="flex flex-col items-start gap-4 py-10">
	<p class="tabular font-serif text-[64px] leading-none font-semibold text-accent-text">
		{page.status}
	</p>
	<h1 class="opsz-display font-serif text-3xl font-semibold">{title}</h1>
	<p class="max-w-[60ch] text-muted">{body}</p>

	{#if detail}
		<details class="max-w-[60ch] text-[13px] text-muted">
			<summary class="cursor-pointer">{m.error_detail()}</summary>
			<pre class="mt-2 overflow-x-auto rounded-cover bg-surface p-3 wrap-anywhere">{detail}</pre>
		</details>
	{/if}

	<Button href={resolve('/')} variant="primary" class="mt-2">
		<Icon name="today" size={14} />
		{m.back_home()}
	</Button>
</section>
