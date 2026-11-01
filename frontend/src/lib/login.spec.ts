import { describe, expect, it } from 'vitest';
import { loginHref, safeNext } from './login';

describe('safeNext', () => {
	it('keeps a path on this origin, query string and all', () => {
		expect(safeNext('/today?x=1')).toBe('/today?x=1');
		expect(safeNext('/title/abc#calendar')).toBe('/title/abc#calendar');
		expect(safeNext('/')).toBe('/');
	});

	it('refuses another origin, however it is spelled', () => {
		expect(safeNext('//evil')).toBe('/');
		expect(safeNext('//evil/today')).toBe('/');
		expect(safeNext('https://evil')).toBe('/');
		expect(safeNext('/\\evil')).toBe('/');
		expect(safeNext('\\\\evil')).toBe('/');
		expect(safeNext('/\t/evil')).toBe('/');
		expect(safeNext('/\n/evil')).toBe('/');
	});

	it('refuses a scheme', () => {
		expect(safeNext('javascript:alert(1)')).toBe('/');
		expect(safeNext('data:text/html,hi')).toBe('/');
	});

	it('falls back to the front page when there is nothing', () => {
		expect(safeNext('')).toBe('/');
		expect(safeNext(null)).toBe('/');
		expect(safeNext(undefined)).toBe('/');
		expect(safeNext('today')).toBe('/');
	});

	it('never sends you back to the sign-in page', () => {
		expect(safeNext('/login')).toBe('/');
		expect(safeNext('/login?next=/settings')).toBe('/');
	});
});

describe('loginHref', () => {
	it('carries where to come back to', () => {
		expect(loginHref('/settings?tab=1')).toBe('/login?next=%2Fsettings%3Ftab%3D1');
	});

	it('says nothing about the front page, or about a destination it would refuse', () => {
		expect(loginHref('/')).toBe('/login');
		expect(loginHref('//evil')).toBe('/login');
	});
});
