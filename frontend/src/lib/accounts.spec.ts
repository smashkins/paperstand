import { describe, expect, it } from 'vitest';
import { isValidPassword, isValidUsername } from './accounts';

describe('isValidUsername', () => {
	it('takes an ordinary name, and one with spaces around it', () => {
		expect(isValidUsername('ada')).toBe(true);
		expect(isValidUsername('  ada  ')).toBe(true);
		expect(isValidUsername('Ada Lovelace')).toBe(true);
	});

	it('counts characters, not UTF-16 units', () => {
		expect(isValidUsername('é'.repeat(64))).toBe(true);
		expect(isValidUsername('📰'.repeat(64))).toBe(true);
		expect(isValidUsername('📰'.repeat(65))).toBe(false);
	});

	it('refuses an empty name, a long one, a colon and anything invisible', () => {
		expect(isValidUsername('')).toBe(false);
		expect(isValidUsername('   ')).toBe(false);
		expect(isValidUsername('a'.repeat(65))).toBe(false);
		expect(isValidUsername('ada:lovelace')).toBe(false);
		expect(isValidUsername('ada\tlovelace')).toBe(false);
		expect(isValidUsername('ada​lovelace')).toBe(false);
		expect(isValidUsername('ada lovelace')).toBe(false);
	});
});

describe('isValidPassword', () => {
	it('takes 8 to 1024 characters, untrimmed', () => {
		expect(isValidPassword('12345678')).toBe(true);
		expect(isValidPassword('  1234  ')).toBe(true);
		expect(isValidPassword('x'.repeat(1024))).toBe(true);
	});

	it('refuses a short one, a long one and a lone surrogate', () => {
		expect(isValidPassword('1234567')).toBe(false);
		expect(isValidPassword('x'.repeat(1025))).toBe(false);
		expect(isValidPassword('12345678\ud800')).toBe(false);
	});
});
