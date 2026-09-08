import { paraglideVitePlugin } from '@inlang/paraglide-js';
import tailwindcss from '@tailwindcss/vite';
import { defineConfig } from 'vitest/config';
import adapter from '@sveltejs/adapter-static';
import { sveltekit } from '@sveltejs/kit/vite';

const backend = process.env.PAPERSTAND_API_ORIGIN ?? 'http://localhost:8000';

export default defineConfig({
	plugins: [
		tailwindcss(),
		sveltekit({
			compilerOptions: {
				// Force runes mode for the project, except for libraries. Can be removed in svelte 6.
				runes: ({ filename }) =>
					filename.split(/[/\\]/).includes('node_modules') ? undefined : true
			},
			// Single page app: everything is rendered in the browser and the
			// backend serves `index.html` for unknown routes.
			adapter: adapter({ fallback: 'index.html', strict: false })
		}),

		paraglideVitePlugin({
			project: './project.inlang',
			outdir: './src/lib/paraglide',
			emitTsDeclarations: true,
			// No locale segments in the URL: the SPA picks the locale from the
			// stored preference, then from the browser, then falls back to `en`.
			// `custom-localStorage` is registered in `src/lib/locale.ts`; it is
			// the built-in `localStorage` strategy with the storage access
			// guarded, so a browser that blocks site data cannot break boot.
			strategy: ['custom-localStorage', 'preferredLanguage', 'baseLocale']
		})
	],
	server: {
		proxy: {
			'/api': { target: backend, changeOrigin: true },
			'/opds': { target: backend, changeOrigin: true },
			'/openapi.json': { target: backend, changeOrigin: true }
		}
	},
	test: {
		expect: { requireAssertions: true },
		projects: [
			{
				extends: './vite.config.ts',
				test: {
					name: 'unit',
					environment: 'node',
					include: ['src/**/*.{test,spec}.{js,ts}', 'scripts/**/*.{test,spec}.{js,ts}'],
					exclude: ['src/**/*.svelte.{test,spec}.{js,ts}']
				}
			}
		]
	}
});
