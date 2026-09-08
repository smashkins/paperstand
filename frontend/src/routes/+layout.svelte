<script lang="ts">
	import '../app.css';
	import { onNavigate } from '$app/navigation';
	import { page } from '$app/state';
	import { m } from '$lib/paraglide/messages.js';
	import { locale } from '$lib/stores/locale.svelte';
	import { prefs } from '$lib/stores/prefs.svelte';
	import { theme } from '$lib/stores/theme.svelte';
	import { coverTransition } from '$lib/stores/transition.svelte';
	import TopBar from '$lib/components/layout/TopBar.svelte';
	import BottomNav from '$lib/components/layout/BottomNav.svelte';

	let { children } = $props();

	// The reader takes the whole screen; its own layout draws whatever chrome it
	// needs, so the storefront's bar and tabs stay out of the way. An error
	// under `/read/` is the exception: `+error.svelte` renders here, and a page
	// whose only way out is the browser's back button is not one to strip the
	// navigation from.
	const chromeless = $derived(page.error === null && page.url.pathname.startsWith('/read/'));

	locale.init();
	prefs.init();
	$effect(() => theme.init());

	// A cover carries a view transition into the reader, where the browser has one.
	// `prefers-reduced-motion` skips the transition outright rather than running
	// it with its animations zeroed by CSS: the reader asked for no motion, not
	// for motion that finishes quickly.
	onNavigate((navigation) => {
		// Either way the claim has to be dropped, or the cover that made it keeps
		// a `view-transition-name` that nothing is transitioning to.
		const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
		if (!document.startViewTransition || reduced) {
			coverTransition.release();
			return;
		}
		return new Promise((resolve) => {
			document.startViewTransition(async () => {
				resolve();
				await navigation.complete;
				// The claim lasts exactly one navigation, so no cover on the page
				// we land on keeps a name nothing is transitioning to.
				coverTransition.release();
			});
		});
	});
</script>

{#key locale.current}
	{#if chromeless}
		{@render children()}
	{:else}
		<a
			href="#content"
			class="sr-only rounded-cover bg-accent px-3 py-2 text-accent-ink focus:not-sr-only focus:absolute focus:top-2 focus:left-2 focus:z-20"
		>
			{m.skip_to_content()}
		</a>

		<TopBar />

		{#key page.route.id}
			<main
				id="content"
				class="route-enter mx-auto grid max-w-[1280px] gap-10 overflow-x-clip px-4.5 pt-6 pb-28 sm:gap-13 sm:px-7 sm:pt-9 sm:pb-24"
			>
				{@render children()}
			</main>
		{/key}

		<BottomNav />
	{/if}
{/key}
