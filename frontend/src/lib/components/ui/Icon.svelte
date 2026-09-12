<script lang="ts" module>
	/** One primitive of an icon, kept as data so nothing is injected as markup. */
	type Shape =
		| { kind: 'path'; d: string }
		| { kind: 'circle'; cx: number; cy: number; r: number }
		| { kind: 'rect'; x: number; y: number; width: number; height: number; rx: number };

	const path = (d: string): Shape => ({ kind: 'path', d });
	const circle = (cx: number, cy: number, r: number): Shape => ({ kind: 'circle', cx, cy, r });
	const rect = (x: number, y: number, width: number, height: number, rx = 1): Shape => ({
		kind: 'rect',
		x,
		y,
		width,
		height,
		rx
	});

	/**
	 * The whole icon set, as stroked 24×24 shapes.
	 *
	 * A couple of dozen icons is not a reason to take on an icon package and
	 * keep it up to date forever; it is a reason to write down a couple of
	 * dozen shapes.
	 */
	export const ICONS = {
		today: [
			{ kind: 'rect', x: 3, y: 4, width: 18, height: 17, rx: 2 } as Shape,
			path('M3 9h18M8 2v4m8-4v4')
		],
		newspapers: [path('M4 4h16v16H4z'), path('M8 8h8M8 12h8M8 16h5')],
		magazines: [path('M5 3h11l3 3v15H5z'), path('M9 12h6M9 16h6')],
		settings: [
			circle(12, 12, 3),
			path(
				'M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z'
			)
		],
		search: [circle(11, 11, 7), path('m20 20-3.5-3.5')],
		maintenance: [
			path(
				'M9 3h6a1 1 0 0 1 1 1v1h2a1 1 0 0 1 1 1v14a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1h2V4a1 1 0 0 1 1-1z'
			),
			path('M8 11h8M8 15h5')
		],
		theme: [
			circle(12, 12, 4),
			path('M12 2v2m0 16v2M2 12h2m16 0h2M5 5l1.4 1.4M17.6 17.6 19 19M5 19l1.4-1.4M17.6 6.4 19 5')
		],
		left: [path('m15 5-7 7 7 7')],
		right: [path('m9 5 7 7-7 7')],
		alert: [path('M12 3 2 20h20L12 3z'), path('M12 10v4m0 3v.5')],
		tab_settings: [circle(12, 12, 3), path('M4 12h2m12 0h2M12 4v2m0 12v2')],
		refresh: [path('M20 12a8 8 0 1 1-2.3-5.6'), path('M20 4v5h-5')],
		close: [path('m6 6 12 12M18 6 6 18')],
		page_single: [rect(7, 3, 10, 18, 1)],
		page_double: [rect(3, 3, 8, 18, 1), rect(13, 3, 8, 18, 1)],
		zoom_in: [circle(11, 11, 7), path('m20 20-3.5-3.5'), path('M11 8v6M8 11h6')],
		zoom_out: [circle(11, 11, 7), path('m20 20-3.5-3.5'), path('M8 11h6')],
		zoom_fit: [path('M3 12h18'), path('M7 8 3 12l4 4'), path('m17 8 4 4-4 4')],
		thumbs: [rect(3, 6, 4, 12, 1), rect(10, 6, 4, 12, 1), rect(17, 6, 4, 12, 1)],
		fullscreen: [path('M8 3H3v5M16 3h5v5M8 21H3v-5M16 21h5v-5')],
		fullscreen_exit: [path('M3 8h5V3M21 8h-5V3M3 16h5v5M21 16h-5v5')],
		help: [
			circle(12, 12, 9),
			path('M9.6 9.2a2.5 2.5 0 1 1 3.1 2.4c-.7.2-1.2.9-1.2 1.6v.4'),
			path('M12 17v.5')
		]
	} satisfies Record<string, Shape[]>;

	export type IconName = keyof typeof ICONS;
</script>

<script lang="ts">
	interface Props {
		name: IconName;
		size?: number;
		class?: string;
	}

	let { name, size = 16, class: klass = '' }: Props = $props();
</script>

<svg
	class={klass}
	width={size}
	height={size}
	viewBox="0 0 24 24"
	fill="none"
	stroke="currentColor"
	stroke-width="2"
	stroke-linecap="round"
	stroke-linejoin="round"
	aria-hidden="true"
	focusable="false"
>
	{#each ICONS[name] as shape, index (index)}
		{#if shape.kind === 'path'}
			<path d={shape.d} />
		{:else if shape.kind === 'circle'}
			<circle cx={shape.cx} cy={shape.cy} r={shape.r} />
		{:else}
			<rect x={shape.x} y={shape.y} width={shape.width} height={shape.height} rx={shape.rx} />
		{/if}
	{/each}
</svg>
