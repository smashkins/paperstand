/**
 * The one place the frontend talks to the backend.
 *
 * Every response type comes from `types.gen.ts`, which is generated from the
 * backend's own OpenAPI document by `make gen-api`. Nothing here re-declares a
 * shape: if a field moves, this file stops compiling, which is the point.
 *
 * URLs are root-relative. The SPA is served from the same origin as the API in
 * production and Vite proxies `/api` in development, so an absolute base would
 * only be one more thing to get wrong.
 */

import type { components } from './types.gen';

export type Issue = components['schemas']['Issue'];
export type IssueDetail = components['schemas']['IssueDetail'];
export type IssuePage = components['schemas']['IssuePage'];
export type Title = components['schemas']['Title'];
export type TitleDetail = components['schemas']['TitleDetail'];
export type Calendar = components['schemas']['Calendar'];
export type Library = components['schemas']['Library'];
export type Stats = components['schemas']['Stats'];
export type Today = components['schemas']['Today'];
export type Progress = components['schemas']['Progress'];
export type ProgressRecord = components['schemas']['ProgressRecord'];
export type YearCount = components['schemas']['YearCount'];
export type ScanAccepted = components['schemas']['ScanAccepted'];
export type ScanProgress = components['schemas']['ScanProgress'];
export type ScanSummary = components['schemas']['ScanSummary'];
export type ScanStatus = components['schemas']['ScanStatus'];
export type ScanRecord = components['schemas']['ScanRecord'];
export type Health = components['schemas']['HealthResponse'];
export type MaintenanceSummary = components['schemas']['MaintenanceSummary'];
export type TitleGaps = components['schemas']['TitleGaps'];
export type NumberGap = components['schemas']['NumberGap'];
export type UnsortedBucket = components['schemas']['UnsortedBucket'];
export type OrganizerRun = components['schemas']['OrganizerRun'];
export type OrganizerMove = components['schemas']['OrganizerMove'];
export type OrganizerParked = components['schemas']['OrganizerParked'];
export type Kind = Issue['kind'];
export type DatePrecision = Issue['date_precision'];
export type DateSource = Issue['date_source'];

/**
 * The name the parser gives the bucket it drops a file into when it cannot
 * work out what the file is.
 *
 * `Title.source` says `unsorted` outright, but an `Issue` carries only its
 * title's name, and the Today shelves are lists of issues. Matching the name is
 * the only signal available there; it is a deliberate, single-point coupling to
 * `UNSORTED_TITLE` in the backend's parser.
 */
export const UNSORTED_TITLE_NAME = 'Unsorted';

/** True when this issue's title is that bucket. */
export function isUnsortedIssue(issue: Pick<Issue, 'title_name'>): boolean {
	return issue.title_name === UNSORTED_TITLE_NAME;
}

/** The `fetch` a SvelteKit `load` hands us, or the global one. */
export type Fetch = typeof globalThis.fetch;

/** Options every call accepts. */
export interface CallOptions {
	/** The `fetch` from a `load` function, so SvelteKit can track the request. */
	fetch?: Fetch;
	signal?: AbortSignal;
	/**
	 * Let the request outlive the document that started it.
	 *
	 * For the writes that happen as a tab is going away — the reader storing the
	 * page somebody had just turned to — where an ordinary request is fair game
	 * for the browser to cancel during unload.
	 */
	keepalive?: boolean;
}

/** A response the API refused, or a body that was not the JSON we expected. */
export class ApiError extends Error {
	readonly status: number;
	readonly detail: string | undefined;
	readonly url: string;

	constructor(status: number, url: string, detail?: string) {
		super(detail ? `${status} ${detail}` : `HTTP ${status}`);
		this.name = 'ApiError';
		this.status = status;
		this.detail = detail;
		this.url = url;
	}

	/** True when the resource simply is not there, which pages render as empty. */
	get isNotFound(): boolean {
		return this.status === 404;
	}
}

/**
 * Mark a promise's rejection as handled without consuming it.
 *
 * A `load` that returns several promises may have some of them never awaited —
 * a magazine's page never awaits the calendar — and an unawaited rejection is
 * an unhandled one, which the browser reports on the console. Attaching an
 * empty catch to a *copy* of the chain silences that while leaving the original
 * promise free to reject at whatever awaits it.
 */
export function quiet<T>(promise: Promise<T>): Promise<T> {
	promise.catch(() => {});
	return promise;
}

/** Anything that can go into a query string; `undefined` and `null` are dropped. */
type QueryValue = string | number | boolean | undefined | null;

export function buildQuery(params: Record<string, QueryValue> = {}): string {
	const search = new URLSearchParams();
	for (const [key, value] of Object.entries(params)) {
		if (value === undefined || value === null || value === '') continue;
		search.set(key, String(value));
	}
	const query = search.toString();
	return query ? `?${query}` : '';
}

/** Pull FastAPI's `detail` out of an error body, whatever shape it arrived in. */
async function readDetail(response: Response): Promise<string | undefined> {
	let body: unknown;
	try {
		body = await response.json();
	} catch {
		return undefined;
	}
	if (typeof body !== 'object' || body === null) return undefined;
	const detail = (body as { detail?: unknown }).detail;
	if (typeof detail === 'string') return detail;
	// A 422 answers with a list of validation errors; the first message is the
	// only one worth putting in front of a person.
	if (Array.isArray(detail)) {
		const first = detail[0] as { msg?: unknown } | undefined;
		if (first && typeof first.msg === 'string') return first.msg;
	}
	return undefined;
}

async function request<T>(path: string, options: CallOptions = {}, init: RequestInit = {}) {
	const doFetch = options.fetch ?? globalThis.fetch;
	let response: Response;
	try {
		response = await doFetch(path, {
			...init,
			signal: options.signal,
			keepalive: options.keepalive,
			headers: { accept: 'application/json', ...init.headers }
		});
	} catch (error) {
		// A network failure has no status; 0 says "the request never landed".
		throw new ApiError(0, path, error instanceof Error ? error.message : undefined);
	}
	if (!response.ok) {
		throw new ApiError(response.status, path, await readDetail(response));
	}
	if (response.status === 204) return undefined as T;
	try {
		return (await response.json()) as T;
	} catch {
		throw new ApiError(response.status, path, 'the response was not JSON');
	}
}

export interface TitleParams {
	kind?: Kind;
	library?: string;
	sort?: 'name' | 'latest';
	include_unsorted?: boolean;
}

export interface IssueParams {
	title?: string;
	kind?: Kind;
	library?: string;
	from?: string;
	to?: string;
	year?: number;
	sort?: 'date_desc' | 'date_asc' | 'added_desc';
	limit?: number;
	offset?: number;
	include_duplicates?: boolean;
	/** The maintenance filter: issues whose file has gone missing. */
	missing?: boolean;
	/** The maintenance filter: issues the renderer could not open at all. */
	unreadable?: boolean;
}

export interface MaintenanceGapsParams {
	/** `YYYY-MM-DD`; defaults to today in `TZ`, exactly like `/api/today`. */
	today?: string;
}

export const api = {
	today: (date?: string, options?: CallOptions) =>
		request<Today>(`/api/today${buildQuery({ date })}`, options),

	titles: (params: TitleParams = {}, options?: CallOptions) =>
		request<Title[]>(`/api/titles${buildQuery({ ...params })}`, options),

	title: (id: string, options?: CallOptions) =>
		request<TitleDetail>(`/api/titles/${encodeURIComponent(id)}`, options),

	calendar: (id: string, year?: number, options?: CallOptions) =>
		request<Calendar>(
			`/api/titles/${encodeURIComponent(id)}/calendar${buildQuery({ year })}`,
			options
		),

	issues: (params: IssueParams = {}, options?: CallOptions) =>
		request<IssuePage>(`/api/issues${buildQuery({ ...params })}`, options),

	issue: (id: string, options?: CallOptions) =>
		request<IssueDetail>(`/api/issues/${encodeURIComponent(id)}`, options),

	progress: (limit?: number, options?: CallOptions) =>
		request<ProgressRecord[]>(`/api/progress${buildQuery({ limit })}`, options),

	putProgress: (id: string, page: number, options?: CallOptions) =>
		request<Progress>(`/api/issues/${encodeURIComponent(id)}/progress`, options, {
			method: 'PUT',
			headers: { 'content-type': 'application/json' },
			body: JSON.stringify({ page })
		}),

	deleteProgress: (id: string, options?: CallOptions) =>
		request<void>(`/api/issues/${encodeURIComponent(id)}/progress`, options, { method: 'DELETE' }),

	scan: (options?: CallOptions) => request<ScanAccepted>('/api/scan', options, { method: 'POST' }),

	scanStatus: (options?: CallOptions) => request<ScanStatus>('/api/scan/status', options),

	libraries: (options?: CallOptions) => request<Library[]>('/api/libraries', options),

	stats: (options?: CallOptions) => request<Stats>('/api/stats', options),

	health: (options?: CallOptions) => request<Health>('/api/health', options),

	maintenance: (options?: CallOptions) => request<MaintenanceSummary>('/api/maintenance', options),

	maintenanceGaps: (params: MaintenanceGapsParams = {}, options?: CallOptions) =>
		request<TitleGaps[]>(`/api/maintenance/gaps${buildQuery({ ...params })}`, options)
};
