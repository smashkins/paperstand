<script lang="ts">
	import { resolve } from '$app/paths';
	import type { ResolvedPathname } from '$app/types';
	import { page } from '$app/state';
	import { m } from '$lib/paraglide/messages.js';
	import Icon, { type IconName } from '$lib/components/ui/Icon.svelte';

	/** The phone's navigation: four tabs, thumb height, out of the way on desktop. */
	const tabs = $derived<{ href: ResolvedPathname; label: string; icon: IconName }[]>([
		{ href: resolve('/'), label: m.nav_today(), icon: 'today' },
		{ href: resolve('/newspapers'), label: m.nav_newspapers(), icon: 'newspapers' },
		{ href: resolve('/magazines'), label: m.nav_magazines(), icon: 'magazines' },
		{ href: resolve('/settings'), label: m.nav_settings(), icon: 'tab_settings' }
	]);

	function isActive(href: string): boolean {
		if (href === '/') return page.url.pathname === '/';
		return page.url.pathname === href || page.url.pathname.startsWith(`${href}/`);
	}
</script>

<nav
	class="fixed inset-x-0 bottom-0 z-10 grid h-16 grid-cols-4 border-t border-hairline bg-[color-mix(in_srgb,var(--paperstand-bg)_92%,transparent)] pb-[env(safe-area-inset-bottom)] backdrop-blur-[10px] sm:hidden"
	aria-label={m.nav_main()}
>
	{#each tabs as tab (tab.href)}
		<a
			href={tab.href}
			class="grid place-items-center gap-0.5 text-[10.5px] font-medium tracking-[0.02em] text-muted"
			class:text-accent-text={isActive(tab.href)}
			aria-current={isActive(tab.href) ? 'page' : undefined}
		>
			<Icon name={tab.icon} size={20} />
			<span>{tab.label}</span>
		</a>
	{/each}
</nav>
