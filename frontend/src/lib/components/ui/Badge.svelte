<script lang="ts">
	import type { Snippet } from 'svelte';

	/**
	 * A small label. `accent` is the Today badge and nothing else; `outline` is
	 * the neutral chip under a cover; `warning` marks a guess the parser made.
	 */
	interface Props {
		variant?: 'accent' | 'outline' | 'warning';
		title?: string;
		class?: string;
		children: Snippet;
	}

	let { variant = 'outline', title, class: klass = '', children }: Props = $props();

	const styles: Record<NonNullable<Props['variant']>, string> = {
		accent: 'bg-accent text-accent-ink tracking-[0.1em]',
		outline: 'border border-hairline text-muted',
		warning: 'border border-accent/45 text-accent-text'
	};
</script>

<span
	{title}
	class="inline-block w-fit rounded-cover px-1.5 py-px text-[10.5px] leading-[1.5] font-medium tracking-[0.06em] uppercase {styles[
		variant
	]} {klass}"
>
	{@render children()}
</span>
