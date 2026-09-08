<script lang="ts">
	/**
	 * A shimmering placeholder.
	 *
	 * `aspect` draws a cover-shaped one, which is what keeps a loading shelf the
	 * same height as the shelf that replaces it.
	 */
	interface Props {
		width?: string;
		height?: string;
		aspect?: number;
		class?: string;
	}

	let { width, height, aspect, class: klass = '' }: Props = $props();
</script>

<div
	class="shimmer rounded-cover {klass}"
	style:width
	style:height
	style:aspect-ratio={aspect}
	aria-hidden="true"
></div>
