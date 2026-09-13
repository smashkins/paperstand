<script lang="ts">
	import type { OrganizerParked } from '$lib/api/client';
	import { m } from '$lib/paraglide/messages.js';

	/**
	 * One of the organizer's parked-file tables — `unsorted/` or
	 * `duplicates/` — sharing one shape between the two.
	 *
	 * Two columns, the name and why the file stopped here. The size and the
	 * modification time were dropped: their headings were the widest thing in
	 * the row and left the name a narrow strip, and neither answers the
	 * question the table is for. Both are still in the run's own report under
	 * `<data>/organizer/`.
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
			<table class="w-full min-w-[280px] border-collapse text-left text-[13px]">
				<thead>
					<tr class="border-b border-hairline text-xs tracking-[0.06em] text-muted uppercase">
						<th class="px-3 py-2 font-medium">{m.column_name()}</th>
						<th class="px-3 py-2 font-medium">{m.maintenance_column_reason()}</th>
					</tr>
				</thead>
				<tbody>
					{#each rows as file (file.name)}
						<tr class="border-b border-hairline last:border-b-0">
							<td class="px-3 py-2 font-mono text-xs break-all">{file.name}</td>
							<td class="px-3 py-2 wrap-anywhere text-muted">{file.reason ?? ''}</td>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
	{/if}
</div>
