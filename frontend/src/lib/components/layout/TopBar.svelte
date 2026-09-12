<script lang="ts">
	import { resolve } from '$app/paths';
	import { page } from '$app/state';
	import { pluralise } from '$lib/format';
	import { m } from '$lib/paraglide/messages.js';
	import { locales, type Locale } from '$lib/paraglide/runtime';
	import { attention } from '$lib/stores/attention.svelte';
	import { locale } from '$lib/stores/locale.svelte';
	import { theme } from '$lib/stores/theme.svelte';
	import Icon from '$lib/components/ui/Icon.svelte';

	/**
	 * The bar that never moves: wordmark, the three shelves, and the three
	 * controls that change how the whole application looks.
	 *
	 * Search is here as a disabled field rather than absent, because leaving a
	 * hole where it will go is worse than showing where it is going to be.
	 */
	const links = $derived([
		{ href: resolve('/'), label: m.nav_today() },
		{ href: resolve('/newspapers'), label: m.nav_newspapers() },
		{ href: resolve('/magazines'), label: m.nav_magazines() }
	]);

	function isActive(href: string): boolean {
		if (href === '/') return page.url.pathname === '/';
		return page.url.pathname === href || page.url.pathname.startsWith(`${href}/`);
	}

	// One request per page load of the app, not per navigation: the maintenance
	// page and Settings refresh the same store on their own after that.
	$effect(() => {
		attention.refresh();
	});

	const maintenanceLabel = $derived(
		attention.count && attention.count > 0
			? pluralise(
					attention.count,
					locale.intl,
					m.maintenance_badge_aria_one,
					m.maintenance_badge_aria_other
				)
			: m.nav_maintenance()
	);
</script>

<header
	class="sticky top-0 z-10 border-b border-hairline bg-[color-mix(in_srgb,var(--paperstand-bg)_88%,transparent)] backdrop-blur-[10px]"
>
	<div class="mx-auto flex h-14 max-w-[1280px] items-center gap-4 px-4.5 sm:gap-7 sm:px-7">
		<a class="opsz-title font-serif text-[22px] font-semibold tracking-tight" href={resolve('/')}>
			Paper<span class="text-accent-text">stand</span>
		</a>

		<nav class="hidden gap-[22px] font-medium text-muted sm:flex" aria-label={m.nav_main()}>
			{#each links as link (link.href)}
				<a
					href={link.href}
					class="relative transition-colors hover:text-ink"
					class:text-ink={isActive(link.href)}
					aria-current={isActive(link.href) ? 'page' : undefined}
				>
					{link.label}
					{#if isActive(link.href)}
						<span class="absolute right-0 -bottom-[19px] left-0 h-0.5 bg-accent"></span>
					{/if}
				</a>
			{/each}
		</nav>

		<div class="ml-auto flex items-center gap-2.5">
			<span
				class="hidden h-[30px] w-[220px] items-center gap-1.5 rounded-cover border border-hairline bg-surface px-2.5 text-xs font-medium text-muted opacity-60 lg:inline-flex"
				title={m.search_soon()}
			>
				<Icon name="search" size={14} />
				<input
					id="search"
					name="search"
					type="search"
					class="w-full cursor-not-allowed bg-transparent outline-none"
					placeholder={m.search_placeholder()}
					aria-label={m.search_placeholder()}
					disabled
				/>
			</span>

			<span
				class="inline-flex h-[30px] overflow-hidden rounded-cover border border-hairline bg-surface"
				role="group"
				aria-label={m.language_label()}
			>
				{#each locales as code (code)}
					<button
						type="button"
						class="px-2.5 text-[11px] font-medium tracking-[0.06em] uppercase transition-colors"
						class:bg-ink={code === locale.current}
						class:text-bg={code === locale.current}
						aria-pressed={code === locale.current}
						onclick={() => locale.set(code as Locale)}
					>
						{code}
					</button>
				{/each}
			</span>

			<button
				type="button"
				class="inline-flex h-[30px] items-center gap-1.5 rounded-cover border border-hairline bg-surface px-2.5 text-xs font-medium transition-colors hover:border-accent"
				onclick={() => theme.cycle()}
				title={m.theme_toggle()}
			>
				<Icon name="theme" size={14} />
				<span class="hidden md:inline">{m.theme_label()}</span>
			</button>

			<a
				href={resolve('/maintenance')}
				class="relative hidden h-[30px] items-center rounded-cover border border-hairline bg-surface px-2.5 transition-colors hover:border-accent sm:inline-flex"
				class:border-accent={isActive('/maintenance')}
				aria-label={maintenanceLabel}
				title={m.nav_maintenance()}
			>
				<Icon name="maintenance" size={14} />
				{#if attention.count !== null && attention.count > 0}
					<span
						class="tabular absolute -top-1 -right-1 flex h-4 min-w-4 items-center justify-center rounded-full bg-accent px-1 text-[10px] font-semibold text-accent-ink"
						aria-hidden="true"
					>
						{attention.count > 99 ? '99+' : attention.count}
					</span>
				{/if}
			</a>

			<a
				href={resolve('/settings')}
				class="hidden h-[30px] items-center rounded-cover border border-hairline bg-surface px-2.5 transition-colors hover:border-accent sm:inline-flex"
				class:border-accent={isActive('/settings')}
				aria-label={m.nav_settings()}
				title={m.nav_settings()}
			>
				<Icon name="settings" size={14} />
			</a>
		</div>
	</div>
</header>
