<script lang="ts" module>
	export type ButtonVariant = 'primary' | 'secondary' | 'quiet';

	const BASE =
		'inline-flex items-center justify-center gap-2 rounded-cover px-3.5 py-2 text-[13px] font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-50';

	const VARIANTS: Record<ButtonVariant, string> = {
		primary: 'bg-accent text-accent-ink border border-accent hover:opacity-90',
		secondary: 'bg-surface text-ink border border-hairline hover:border-accent',
		quiet: 'border border-transparent text-muted hover:text-ink'
	};

	/**
	 * The classes of a button, for the rare anchor that cannot be one.
	 *
	 * A link out of the application — to the PDF itself, say — has to carry
	 * `rel="external"` and a plain string href, which this component's typed
	 * `href` deliberately does not accept.
	 */
	export function buttonClass(variant: ButtonVariant = 'secondary', extra = ''): string {
		return `${BASE} ${VARIANTS[variant]} ${extra}`;
	}
</script>

<script lang="ts">
	import type { ResolvedPathname } from '$app/types';
	import type { Snippet } from 'svelte';

	/**
	 * A button, or a link that looks like one when `href` is given.
	 *
	 * `primary` is the only thing on a page allowed to be kiosk red — "Read
	 * latest", "Rescan now" — so there is never a doubt about what the page
	 * wants you to do next.
	 */
	interface Props {
		variant?: ButtonVariant;
		/** Already through `resolve()`: this is an in-application route. */
		href?: ResolvedPathname;
		type?: 'button' | 'submit';
		disabled?: boolean;
		title?: string;
		'aria-label'?: string;
		onclick?: (event: MouseEvent) => void;
		class?: string;
		children: Snippet;
	}

	let {
		variant = 'secondary',
		href,
		type = 'button',
		disabled = false,
		title,
		'aria-label': ariaLabel,
		onclick,
		class: klass = '',
		children
	}: Props = $props();
</script>

{#if href}
	<a {href} {title} aria-label={ariaLabel} {onclick} class={buttonClass(variant, klass)}>
		{@render children()}
	</a>
{:else}
	<button
		{type}
		{disabled}
		{title}
		aria-label={ariaLabel}
		{onclick}
		class={buttonClass(variant, klass)}
	>
		{@render children()}
	</button>
{/if}
