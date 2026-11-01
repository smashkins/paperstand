<script lang="ts">
	import { goto } from '$app/navigation';
	import { resolve } from '$app/paths';
	import { ApiError, api, type User } from '$lib/api/client';
	import { isValidPassword, problemMessage } from '$lib/accounts';
	import { m } from '$lib/paraglide/messages.js';
	import { session } from '$lib/stores/session.svelte';
	import Badge from '$lib/components/ui/Badge.svelte';
	import Button from '$lib/components/ui/Button.svelte';
	import TextField from '$lib/components/ui/TextField.svelte';

	/**
	 * Settings → Account: who you are, a new password, and the way out.
	 *
	 * Everyone has this section. A password change keeps this browser signed in
	 * — the server revokes every other session of the account, not the one that
	 * asked — so it ends with a message rather than a trip to the sign-in page.
	 */
	interface Props {
		user: User;
	}

	let { user }: Props = $props();

	let changing = $state(false);
	let current = $state('');
	let next = $state('');
	let repeat = $state('');
	let saving = $state(false);
	let problem = $state<string | null>(null);
	let notice = $state<string | null>(null);
	let signingOut = $state(false);
	let signOutProblem = $state<string | null>(null);

	function openForm() {
		changing = true;
		notice = null;
		problem = null;
	}

	function closeForm() {
		changing = false;
		current = '';
		next = '';
		repeat = '';
		problem = null;
	}

	/** The first thing wrong with the form, before anything is sent. */
	function check(): string | null {
		if (!current) return m.account_error_current_missing();
		if (!isValidPassword(next)) return m.account_rule_password();
		if (next !== repeat) return m.account_error_mismatch();
		return null;
	}

	async function save(event: SubmitEvent) {
		event.preventDefault();
		if (saving) return;
		problem = check();
		if (problem) return;
		saving = true;
		try {
			await api.changePassword({ current_password: current, new_password: next });
			closeForm();
			notice = m.account_password_changed();
		} catch (error) {
			problem = problemMessage(error, {
				403: m.account_error_current_wrong,
				422: m.account_rule_password
			});
		} finally {
			saving = false;
		}
	}

	async function signOut() {
		signingOut = true;
		signOutProblem = null;
		try {
			await api.logout();
		} catch (error) {
			// A `401` says this session had already ended; the layout's handler is
			// on its way to the sign-in page already.
			if (!(error instanceof ApiError && error.status === 401)) {
				signOutProblem = problemMessage(error, {});
				signingOut = false;
			}
			return;
		}
		session.clear();
		// Every `load` runs again, as nobody: whatever the pages had fetched is
		// gone, and Back lands on the sign-in page rather than on a cached shelf.
		await goto(resolve('/login'), { invalidateAll: true });
	}
</script>

<section class="grid gap-4">
	<h2 class="opsz-title font-serif text-2xl font-semibold">{m.settings_account()}</h2>
	<div class="grid gap-4 rounded-cover border border-hairline bg-surface p-4">
		<div class="flex flex-wrap items-center gap-x-4 gap-y-3">
			<p class="grid min-w-0 gap-0.5">
				<span class="text-xs tracking-[0.06em] text-muted uppercase">
					{m.account_signed_in_as()}
				</span>
				<span class="flex min-w-0 flex-wrap items-center gap-2">
					<span class="opsz-text font-serif text-lg font-semibold wrap-anywhere">
						{user.username}
					</span>
					{#if user.is_admin}
						<Badge>{m.account_admin()}</Badge>
					{/if}
				</span>
			</p>
			<div class="ml-auto flex flex-wrap gap-2">
				{#if !changing}
					<Button variant="secondary" onclick={openForm}>{m.account_change_password()}</Button>
				{/if}
				<Button variant="secondary" onclick={signOut} disabled={signingOut}>
					{m.account_sign_out()}
				</Button>
			</div>
		</div>

		{#if signOutProblem}
			<p class="text-[13px] text-accent-text" role="alert">{signOutProblem}</p>
		{/if}

		{#if changing}
			<form
				class="grid gap-3 border-t border-hairline pt-4 sm:grid-cols-3"
				novalidate
				onsubmit={save}
			>
				<!-- Lets a password manager tell whose password is changing. -->
				<input
					type="text"
					name="username"
					autocomplete="username"
					value={user.username}
					readonly
					hidden
				/>
				<TextField
					id="password-current"
					type="password"
					label={m.account_current_password()}
					autocomplete="current-password"
					bind:value={current}
				/>
				<TextField
					id="password-new"
					type="password"
					label={m.account_new_password()}
					autocomplete="new-password"
					bind:value={next}
				/>
				<TextField
					id="password-repeat"
					type="password"
					label={m.account_repeat_password()}
					autocomplete="new-password"
					bind:value={repeat}
				/>
				<p class="text-[13px] text-muted sm:col-span-3">{m.account_rule_password()}</p>
				{#if problem}
					<p class="text-[13px] text-accent-text sm:col-span-3" role="alert">{problem}</p>
				{/if}
				<div class="flex flex-wrap gap-2 sm:col-span-3">
					<Button type="submit" variant="secondary" disabled={saving}>
						{m.account_save_password()}
					</Button>
					<Button variant="quiet" onclick={closeForm}>{m.action_cancel()}</Button>
				</div>
			</form>
		{/if}

		{#if notice}
			<p class="text-[13px] text-muted" role="status">{notice}</p>
		{/if}
	</div>
</section>
