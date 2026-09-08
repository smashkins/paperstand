<script lang="ts">
	/**
	 * The reader's shell: nothing.
	 *
	 * A page of a newspaper wants the whole screen, so this layout deliberately
	 * draws no chrome of its own; the root layout already knows to skip the top
	 * bar and the tab bar under `/read`.
	 */
	let { children } = $props();
</script>

{@render children()}
