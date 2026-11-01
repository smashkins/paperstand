<script lang="ts">
	import type { FullAutoFill } from 'svelte/elements';

	/**
	 * A labelled text or password input, the shape of every account form.
	 *
	 * The label sits above the field in the small capitals Settings uses for
	 * every other control. The text is 16 px whatever the screen: anything
	 * smaller and Safari on a phone zooms the page in when the field takes focus.
	 */
	interface Props {
		id: string;
		label: string;
		value: string;
		type?: 'text' | 'password';
		/** Say what goes in, so a password manager fills — or leaves — the right field. */
		autocomplete: FullAutoFill;
		name?: string;
		class?: string;
	}

	let {
		id,
		label,
		value = $bindable(),
		type = 'text',
		autocomplete,
		name,
		class: klass = ''
	}: Props = $props();
</script>

<div class="grid min-w-0 gap-1.5 {klass}">
	<label for={id} class="text-xs tracking-[0.06em] text-muted uppercase">{label}</label>
	<input
		{id}
		{name}
		{type}
		{autocomplete}
		bind:value
		autocapitalize="none"
		spellcheck={false}
		class="w-full min-w-0 rounded-cover border border-hairline bg-bg px-2.5 py-1.5 text-base transition-colors hover:border-accent/60"
	/>
</div>
