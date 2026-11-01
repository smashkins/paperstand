/**
 * What an account's name and password must look like, and what to say when
 * the server refuses a change to one.
 *
 * The rules are the server's own (`validate_username` and `validate_password`
 * in the backend), checked here first so that a form can say what is wrong
 * before anything is sent. Lengths count code points, as Python does, not the
 * UTF-16 units of `String.length`.
 */

import { ApiError } from '$lib/api/client';
import { m } from '$lib/paraglide/messages.js';

export const USERNAME_MAX = 64;
export const PASSWORD_MIN = 8;
export const PASSWORD_MAX = 1024;

/**
 * Control and format characters, lone surrogates and the line and paragraph
 * separators: the categories the server refuses in a name.
 */
const FORBIDDEN_IN_NAME = /[\p{Cc}\p{Cf}\p{Cs}\p{Zl}\p{Zp}]/u;
const LONE_SURROGATE = /\p{Cs}/u;

function codePoints(value: string): number {
	return Array.from(value).length;
}

/** 1 to 64 characters once trimmed, no `:` (Basic splits on it), nothing invisible. */
export function isValidUsername(name: string): boolean {
	const trimmed = name.trim();
	const length = codePoints(trimmed);
	return (
		length >= 1 &&
		length <= USERNAME_MAX &&
		!trimmed.includes(':') &&
		!FORBIDDEN_IN_NAME.test(trimmed)
	);
}

/** 8 to 1024 characters, never trimmed. */
export function isValidPassword(password: string): boolean {
	const length = codePoints(password);
	return length >= PASSWORD_MIN && length <= PASSWORD_MAX && !LONE_SURROGATE.test(password);
}

/** The localized sentence for each status a form expects from its request. */
export type ExpectedProblems = Partial<Record<number, () => string>>;

/**
 * What to tell a person about a refused account request.
 *
 * Never the server's `detail`: that is English, whatever the reader's
 * language. A status the form expected has its own sentence; a request that
 * never landed says so; anything else is a plain "that did not work".
 */
export function problemMessage(error: unknown, expected: ExpectedProblems): string {
	if (error instanceof ApiError) {
		const known = expected[error.status];
		if (known) return known();
		if (error.status === 0) return m.error_unreachable();
	}
	return m.account_error_failed();
}
