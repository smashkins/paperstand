<script lang="ts">
	import { invalidateAll } from '$app/navigation';
	import { ApiError, api, type User } from '$lib/api/client';
	import {
		isValidPassword,
		isValidUsername,
		problemMessage,
		type ExpectedProblems
	} from '$lib/accounts';
	import { formatDateTime, formatNumber } from '$lib/format';
	import { m } from '$lib/paraglide/messages.js';
	import { locale } from '$lib/stores/locale.svelte';
	import { session } from '$lib/stores/session.svelte';
	import Badge from '$lib/components/ui/Badge.svelte';
	import Button from '$lib/components/ui/Button.svelte';
	import InlineError from '$lib/components/ui/InlineError.svelte';
	import SectionHeader from '$lib/components/ui/SectionHeader.svelte';
	import Skeleton from '$lib/components/ui/Skeleton.svelte';
	import TextField from '$lib/components/ui/TextField.svelte';

	/**
	 * Settings → Users: every account, for an administrator.
	 *
	 * The first list comes from the route's `load`; after that the section owns
	 * it and asks again after every change, so what it shows is what the server
	 * now holds. Each row's change happens in place — a password typed into the
	 * row, a delete confirmed in the row — and so does its outcome. Your own row
	 * has no delete, which the server would refuse anyway.
	 */
	interface Props {
		initial: Promise<User[]>;
	}

	let { initial }: Props = $props();

	let users = $state<User[] | null>(null);
	let loadError = $state<unknown>(null);
	let reloading = $state(false);

	$effect(() => {
		let live = true;
		initial
			.then((list) => {
				if (live) users = list;
			})
			.catch((error: unknown) => {
				if (live) loadError = error;
			});
		return () => {
			live = false;
		};
	});

	async function reload() {
		reloading = true;
		try {
			users = await api.users();
			loadError = null;
		} catch (error) {
			loadError = error;
		} finally {
			reloading = false;
		}
	}

	/** The one row with a step open: a password being typed, or a change to confirm. */
	type Step = 'reset' | 'delete' | 'demote';
	let open = $state<{ id: number; step: Step } | null>(null);
	let password = $state('');
	/** The row with a request in flight. */
	let busy = $state<number | null>(null);
	/**
	 * What the last change came to. `id` is the row it is shown under; `null`
	 * shows it under the list, which is where a deleted row's outcome goes.
	 */
	let outcome = $state<{ id: number | null; message: string; failed: boolean } | null>(null);

	function toggle(user: User, step: Step) {
		open = open?.id === user.id && open.step === step ? null : { id: user.id, step };
		password = '';
		outcome = null;
	}

	function cancel() {
		open = null;
		password = '';
	}

	/** What every change to an existing account may be refused with. */
	const refusals: ExpectedProblems = {
		403: m.users_error_forbidden,
		404: m.users_error_gone,
		409: m.users_error_last_admin,
		422: m.account_rule_password
	};

	/**
	 * Run one change to `user`, then show how it went and fetch the list again.
	 *
	 * A change to your own account — giving up being an administrator — re-runs
	 * every `load` instead, so the session, the top bar and this page follow; a
	 * plain reload would ask for the list as somebody who may no longer see it.
	 */
	async function change(user: User, request: () => Promise<unknown>, done: string, gone = false) {
		busy = user.id;
		outcome = null;
		try {
			await request();
			open = null;
			password = '';
			outcome = { id: gone ? null : user.id, message: done, failed: false };
		} catch (error) {
			// Somebody else deleted the account meanwhile: its row is about to go,
			// so the message goes under the list.
			const vanished = error instanceof ApiError && error.status === 404;
			outcome = {
				id: vanished ? null : user.id,
				message: problemMessage(error, refusals),
				failed: true
			};
			if (!vanished) return;
		} finally {
			busy = null;
		}
		if (user.id === session.user?.id) await invalidateAll();
		else await reload();
	}

	function resetPassword(event: SubmitEvent, user: User) {
		event.preventDefault();
		if (!isValidPassword(password)) {
			outcome = { id: user.id, message: m.account_rule_password(), failed: true };
			return;
		}
		const value = password;
		void change(
			user,
			() => api.updateUser(user.id, { password: value }),
			m.users_password_reset({ name: user.username })
		);
	}

	function setAdmin(user: User, isAdmin: boolean) {
		void change(
			user,
			() => api.updateUser(user.id, { is_admin: isAdmin }),
			isAdmin
				? m.users_made_admin({ name: user.username })
				: m.users_removed_admin({ name: user.username })
		);
	}

	function remove(user: User) {
		void change(
			user,
			() => api.deleteUser(user.id),
			m.users_deleted({ name: user.username }),
			true
		);
	}

	let newName = $state('');
	let newPassword = $state('');
	let newAdmin = $state(false);
	let adding = $state(false);
	let addOutcome = $state<{ message: string; failed: boolean } | null>(null);

	async function add(event: SubmitEvent) {
		event.preventDefault();
		if (adding) return;
		if (!isValidUsername(newName)) {
			addOutcome = { message: m.account_rule_username(), failed: true };
			return;
		}
		if (!isValidPassword(newPassword)) {
			addOutcome = { message: m.account_rule_password(), failed: true };
			return;
		}
		adding = true;
		addOutcome = null;
		try {
			const created = await api.createUser({
				username: newName.trim(),
				password: newPassword,
				is_admin: newAdmin
			});
			newName = '';
			newPassword = '';
			newAdmin = false;
			addOutcome = { message: m.users_added({ name: created.username }), failed: false };
		} catch (error) {
			addOutcome = {
				message: problemMessage(error, {
					403: m.users_error_forbidden,
					409: m.users_error_taken,
					422: () => `${m.account_rule_username()} ${m.account_rule_password()}`
				}),
				failed: true
			};
			return;
		} finally {
			adding = false;
		}
		await reload();
	}
</script>

{#snippet message(text: string, failed: boolean)}
	<p
		class="text-[13px] {failed ? 'text-accent-text' : 'text-muted'}"
		role={failed ? 'alert' : 'status'}
	>
		{text}
	</p>
{/snippet}

<section class="grid gap-4">
	<SectionHeader
		heading={m.settings_users()}
		count={users ? formatNumber(users.length, locale.intl) : undefined}
	/>
	<p class="max-w-[70ch] text-[13px] text-muted">{m.users_note()}</p>

	{#if loadError}
		<InlineError error={loadError} onretry={reload} retrying={reloading} />
	{:else if users === null}
		<Skeleton width="100%" height="120px" />
	{:else}
		<ul class="rounded-cover border border-hairline bg-surface">
			{#each users as user (user.id)}
				{@const self = user.id === session.user?.id}
				{@const step = open?.id === user.id ? open.step : null}
				<li class="grid gap-3 border-b border-hairline px-4 py-3 last:border-b-0">
					<div class="flex flex-wrap items-center gap-x-4 gap-y-2">
						<div class="grid min-w-0 flex-1 basis-48 gap-0.5">
							<p class="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
								<span class="font-medium wrap-anywhere">{user.username}</span>
								{#if user.is_admin}
									<Badge>{m.account_admin()}</Badge>
								{/if}
								{#if self}
									<span class="text-[13px] text-muted">{m.users_you()}</span>
								{/if}
							</p>
							<p class="tabular text-[13px] text-muted">
								{m.users_created({ when: formatDateTime(user.created_at, locale.intl) })}
							</p>
						</div>
						<div class="flex flex-wrap gap-1">
							<Button
								variant="quiet"
								onclick={() => toggle(user, 'reset')}
								disabled={busy === user.id}
								aria-expanded={step === 'reset'}
							>
								{m.users_reset_password()}
							</Button>
							{#if !user.is_admin}
								<Button
									variant="quiet"
									onclick={() => setAdmin(user, true)}
									disabled={busy === user.id}
								>
									{m.users_make_admin()}
								</Button>
							{:else if self}
								<Button
									variant="quiet"
									onclick={() => toggle(user, 'demote')}
									disabled={busy === user.id}
									aria-expanded={step === 'demote'}
								>
									{m.users_remove_admin()}
								</Button>
							{:else}
								<Button
									variant="quiet"
									onclick={() => setAdmin(user, false)}
									disabled={busy === user.id}
								>
									{m.users_remove_admin()}
								</Button>
							{/if}
							{#if !self}
								<Button
									variant="quiet"
									onclick={() => toggle(user, 'delete')}
									disabled={busy === user.id}
									aria-expanded={step === 'delete'}
								>
									{m.users_delete()}
								</Button>
							{/if}
						</div>
					</div>

					{#if step === 'reset'}
						<form
							class="flex flex-wrap items-end gap-3"
							novalidate
							onsubmit={(event) => resetPassword(event, user)}
						>
							<TextField
								id="reset-password-{user.id}"
								type="password"
								label={m.users_new_password_for({ name: user.username })}
								autocomplete="new-password"
								bind:value={password}
								class="flex-1 basis-56"
							/>
							<div class="flex gap-2">
								<Button type="submit" variant="secondary" disabled={busy === user.id}>
									{m.users_save()}
								</Button>
								<Button variant="quiet" onclick={cancel}>{m.action_cancel()}</Button>
							</div>
						</form>
					{:else if step === 'delete' || step === 'demote'}
						<div
							class="flex flex-wrap items-center gap-x-3 gap-y-2 rounded-cover border border-accent/35 bg-bg px-3 py-2.5"
						>
							<p class="min-w-0 flex-1 basis-56 text-[13px]">
								{step === 'delete'
									? m.users_delete_confirm({ name: user.username })
									: m.users_demote_self_confirm()}
							</p>
							<div class="flex gap-2">
								<Button
									variant="primary"
									onclick={() => (step === 'delete' ? remove(user) : setAdmin(user, false))}
									disabled={busy === user.id}
								>
									{step === 'delete' ? m.users_delete() : m.users_remove_admin()}
								</Button>
								<Button variant="secondary" onclick={cancel}>{m.action_cancel()}</Button>
							</div>
						</div>
					{/if}

					{#if outcome && outcome.id === user.id}
						{@render message(outcome.message, outcome.failed)}
					{/if}
				</li>
			{/each}
		</ul>

		{#if outcome && outcome.id === null}
			{@render message(outcome.message, outcome.failed)}
		{/if}
	{/if}

	<form
		class="grid gap-4 rounded-cover border border-hairline bg-surface p-4"
		novalidate
		onsubmit={add}
	>
		<h3 class="font-medium">{m.users_add_heading()}</h3>
		<div class="grid gap-3 sm:grid-cols-2">
			<TextField
				id="new-user-name"
				name="new-account-name"
				label={m.account_username()}
				autocomplete="off"
				bind:value={newName}
			/>
			<TextField
				id="new-user-password"
				name="new-account-password"
				type="password"
				label={m.account_password()}
				autocomplete="new-password"
				bind:value={newPassword}
			/>
		</div>
		<p class="text-[13px] text-muted">
			{m.account_rule_username()}
			{m.account_rule_password()}
		</p>
		<div class="flex flex-wrap items-center gap-x-4 gap-y-3">
			<label class="flex items-center gap-2 text-[13px]">
				<input type="checkbox" class="size-4 accent-accent" bind:checked={newAdmin} />
				{m.users_admin_checkbox()}
			</label>
			<Button type="submit" variant="secondary" disabled={adding} class="ml-auto">
				{m.users_add()}
			</Button>
		</div>
		{#if addOutcome}
			{@render message(addOutcome.message, addOutcome.failed)}
		{/if}
	</form>
</section>
