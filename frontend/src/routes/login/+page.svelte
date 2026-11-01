<script lang="ts">
	import { goto } from '$app/navigation';
	import { api } from '$lib/api/client';
	import { problemMessage } from '$lib/accounts';
	import { m } from '$lib/paraglide/messages.js';
	import { session } from '$lib/stores/session.svelte';
	import Button from '$lib/components/ui/Button.svelte';
	import TextField from '$lib/components/ui/TextField.svelte';
	import type { PageProps } from './$types';

	/**
	 * The way in: a name, a password, and back to wherever the browser was
	 * sent from.
	 *
	 * The server sets the session cookie; the token in its answer is for native
	 * clients and is dropped on the floor here. Going on with `invalidateAll`
	 * makes every `load` ask again as the account that has just signed in.
	 */
	let { data }: PageProps = $props();

	let username = $state('');
	let password = $state('');
	let sending = $state(false);
	let problem = $state<string | null>(null);

	async function submit(event: SubmitEvent) {
		event.preventDefault();
		if (sending) return;
		if (!username.trim() || !password) {
			problem = m.login_error_missing();
			return;
		}
		sending = true;
		problem = null;
		try {
			const { user } = await api.login({ username, password, device: null });
			session.set(user);
		} catch (error) {
			problem = problemMessage(error, { 401: m.login_error_wrong });
			sending = false;
			return;
		}
		// eslint-disable-next-line svelte/no-navigation-without-resolve -- `next` went through `safeNext`
		await goto(data.next, { replaceState: true, invalidateAll: true });
	}
</script>

<svelte:head><title>{m.login_title()} · {m.app_name()}</title></svelte:head>

<main id="content" class="grid min-h-dvh place-items-center px-4.5 py-10">
	<div class="grid w-full max-w-[380px] gap-6">
		<p class="opsz-title text-center font-serif text-[30px] font-semibold tracking-tight">
			Paper<span class="text-accent-text">stand</span>
		</p>

		<form
			class="grid gap-4 rounded-cover border border-hairline bg-surface p-5 sm:p-6"
			novalidate
			onsubmit={submit}
		>
			<h1 class="opsz-title font-serif text-2xl font-semibold">{m.login_title()}</h1>

			<TextField
				id="login-username"
				name="username"
				label={m.account_username()}
				autocomplete="username"
				bind:value={username}
			/>
			<TextField
				id="login-password"
				name="password"
				type="password"
				label={m.account_password()}
				autocomplete="current-password"
				bind:value={password}
			/>

			{#if problem}
				<p class="text-[13px] text-accent-text" role="alert">{problem}</p>
			{/if}

			<Button type="submit" variant="primary" disabled={sending} class="mt-1">
				{sending ? m.login_sending() : m.login_submit()}
			</Button>
		</form>
	</div>
</main>
