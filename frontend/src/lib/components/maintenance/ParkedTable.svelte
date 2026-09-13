<script lang="ts">
	import type { OrganizerParked } from '$lib/api/client';
	import { formatSize, formatWhen } from '$lib/format';
	import { m } from '$lib/paraglide/messages.js';
	import { locale } from '$lib/stores/locale.svelte';

	/**
	 * One of the organizer's parked-file tables — `unsorted/` or
	 * `duplicates/` — sharing one shape between the two.
	 */
	interface Props {
		heading: string;
		rows: OrganizerParked[];
	}

	let { heading, rows }: Props = $props();
</script>

<div class="grid gap-2">
	<h3 class="text-xs tracking-[0.06em] text-muted uppercase">{heading}</h3>
	{#if rows.length === 0}
		<p class="text-[13px] text-muted">{m.maintenance_inbox_parked_empty()}</p>
	{:else}
		<div class="overflow-x-auto rounded-cover border border-hairline bg-surface">
			<table class="w-full min-w-[420px] border-collapse text-left text-[13px]">
				<thead>
					<tr class="border-b border-hairline text-xs tracking-[0.06em] text-muted uppercase">
						<th class="px-3 py-2 font-medium">{m.column_name()}</th>
						<th class="px-3 py-2 font-medium">{m.maintenance_column_reason()}</th>
						<th class="px-3 py-2 text-right font-medium">{m.maintenance_column_size()}</th>
						<th class="px-3 py-2 text-right font-medium">{m.maintenance_column_modified()}</th>
					</tr>
				</thead>
				<tbody>
					{#each rows as file (file.name)}
						<tr class="border-b border-hairline last:border-b-0">
							<td class="px-3 py-2 font-mono text-xs break-all">{file.name}</td>
							<td class="px-3 py-2 wrap-anywhere text-muted">{file.reason ?? ''}</td>
							<td class="tabular px-3 py-2 text-right text-muted">
								{formatSize(file.size, locale.intl)}
							</td>
							<td class="tabular px-3 py-2 text-right text-muted">
								{formatWhen(file.modified, locale.intl)}
							</td>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
	{/if}
</div>
