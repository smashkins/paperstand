# Paperstand frontend

The SvelteKit single page app. See the [repository README](../README.md) for the project,
and [`../CONTRIBUTING.md`](../CONTRIBUTING.md) for how to run it.

```bash
npm install
npm run dev      # or `make dev-web` from the repository root
```

The app talks to the backend on `http://localhost:8000` through the Vite dev proxy
(`/api`, `/opds`, `/openapi.json`). `npm run build` writes the SPA to `build/`, which the
backend serves in production.
