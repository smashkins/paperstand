<script lang="ts">
	import { m } from '$lib/paraglide/messages.js';
	import ErrorState from '$lib/components/ui/ErrorState.svelte';
	import Reader from '$lib/components/reader/Reader.svelte';
	import Skeleton from '$lib/components/ui/Skeleton.svelte';
	import type { PageProps } from './$types';

	/**
	 * The reader route.
	 *
	 * All it does is wait for the issue and hand it to `Reader`, which takes the
	 * screen; the layout above it draws no chrome at all. While the request is
	 * in flight the frame is a cover-shaped shimmer, so the cover that was
	 * clicked has something the same shape to land on, and an issue that is not
	 * there any more — the file moved between the scan and the click — gets the
	 * ordinary error state with a retry.
	 */
	let { data }: PageProps = $props();

	let pageTitle = $state<string>(m.app_name());

	$effect(() => {
		let live = true;
		data.issue
			.then((issue) => {
				if (live) pageTitle = `${issue.title_name} · ${m.app_name()}`;
			})
			.catch(() => {});
		return () => {
			live = false;
		};
	});
</script>

<svelte:head><title>{pageTitle}</title></svelte:head>

{#await data.issue}
	<div class="fixed inset-0 grid place-content-center bg-bg p-4">
		<Skeleton width="min(320px, 68vw)" aspect={0.72} />
	</div>
{:then issue}
	{#key issue.id}
		<Reader {issue} />
	{/key}
{:catch error}
	<div class="mx-auto grid min-h-screen max-w-[640px] content-center px-6 py-12">
		<ErrorState {error} />
	</div>
{/await}
