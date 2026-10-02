import type {
  Artifact, Asset, AssetCreate, AssetFork, AssetRestore, AssetRun, AssetSelect,
  AssetUpdate, Candidate, ChatCreate, DeleteResult, Edge, EdgeCreate, Health,
  Job, Project, ProjectCreate, ProjectDetail, ProjectFork, ProjectUpdate,
  ProposalApply, ProviderKind, ProviderModels, ProviderProbe, ProviderTest,
  Revision, SettingsDTO, SettingsUpdate, Source,
} from './types';

export class ApiError extends Error {
  readonly code: string;
  readonly requestId?: string;
  readonly details: unknown;
  constructor(message: string, public readonly status: number, options: { code?: string; requestId?: string; details?: unknown } = {}) {
    super(message);
    this.name = 'ApiError';
    this.code = options.code || (status ? 'http_error' : 'network_error');
    this.requestId = options.requestId;
    this.details = options.details;
  }
}

function object(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {};
}
function text(value: unknown): string | undefined { return typeof value === 'string' && value ? value : undefined; }
function segment(value: string): string {
  if (!value || value === '.' || value === '..') throw new TypeError('A resource identifier must be nonempty and cannot be a path traversal segment.');
  return encodeURIComponent(value).replace(/[!'()*]/g, character => `%${character.charCodeAt(0).toString(16).toUpperCase()}`);
}
function filePath(name: string): string { return name.split('/').map(segment).join('/'); }
function revisionQuery(url: string, revision?: number): string {
  if (!Number.isInteger(revision) || revision! < 1) return url;
  const hashIndex = url.indexOf('#');
  const hash = hashIndex < 0 ? '' : url.slice(hashIndex);
  const beforeHash = hashIndex < 0 ? url : url.slice(0, hashIndex);
  const queryIndex = beforeHash.indexOf('?');
  const path = queryIndex < 0 ? beforeHash : beforeHash.slice(0, queryIndex);
  const query = new URLSearchParams(queryIndex < 0 ? '' : beforeHash.slice(queryIndex + 1));
  query.set('revision', String(revision));
  return `${path}?${query}${hash}`;
}

/** Framework-independent client. Browser deployments use a same-origin API proxy. */
export function createApiClient(baseUrl = '/api/v1', fetchImpl: typeof fetch = globalThis.fetch) {
  let root = baseUrl.replace(/\/+$/, '');
  let backendOrigin: string | undefined;
  if (/^https?:\/\//i.test(root)) {
    const parsed = new URL(root);
    if (parsed.username || parsed.password || parsed.search || parsed.hash) throw new TypeError('The API base URL cannot include credentials, a query or a fragment.');
    root = parsed.href.replace(/\/+$/, '');
    backendOrigin = parsed.origin;
  } else if (!root.startsWith('/') || root.startsWith('//') || root.includes('?') || root.includes('#')) {
    throw new TypeError('The API base URL must be root-relative or an HTTP(S) URL.');
  }
  const endpoint = (path: string) => `${root}/${path.replace(/^\/+/, '')}`;
  const resolveUrl = (url: string): string => {
    if (/^https?:\/\//i.test(url)) {
      const parsed = new URL(url);
      if (parsed.username || parsed.password) throw new TypeError('File URLs cannot contain credentials.');
      return parsed.href;
    }
    if (url.startsWith('//') || /^[a-z][a-z\d+.-]*:/i.test(url)) throw new TypeError('File URLs must be relative or HTTP(S).');
    // Server-generated links use its canonical API path. Rebase those links on
    // the configured proxy prefix as well as the configured backend origin.
    const apiPath = url.match(/^\/api(?:\/v1)?(?=\/|\?|#|$)/);
    if (apiPath) return endpoint(url.slice(apiPath[0].length));
    if (url.startsWith('/')) return backendOrigin ? new URL(url, backendOrigin).href : url;
    const resolved = endpoint(url);
    return backendOrigin ? new URL(resolved).href : resolved;
  };
  async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
    const headers = new Headers(options.headers);
    if (typeof options.body === 'string' && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json');
    let response: Response;
    try { response = await fetchImpl(endpoint(path), { ...options, headers }); }
    catch (error) {
      if (options.signal?.aborted || object(error).name === 'AbortError') throw error;
      throw new ApiError('Cannot reach Figloom. Check that the local server is running.', 0, { code: 'network_error' });
    }
    const headerRequestId = response.headers.get('X-Request-ID') || undefined;
    if (!response.ok) {
      let value: Record<string, unknown> = {};
      try { value = object(await response.json()); } catch { /* HTTP status remains useful for non-JSON failures. */ }
      const error = object(value.error);
      throw new ApiError(text(error.message) || text(value.message) || text(value.detail) || `Request failed (${response.status}).`, response.status, {
        code: text(error.code) || text(value.code) || 'http_error',
        requestId: text(value.request_id) || headerRequestId || text(error.request_id),
        details: error.details ?? value.details ?? (typeof value.detail === 'string' ? undefined : value.detail),
      });
    }
    if (response.status === 204) return undefined as T;
    try { return await response.json() as T; }
    catch { throw new ApiError('The API returned an unreadable JSON response.', response.status, { code: 'invalid_response', requestId: headerRequestId }); }
  }
  const json = <T>(path: string, method: string, value: unknown) => request<T>(path, { method, body: JSON.stringify(value) });
  const assetPath = (id: string) => `assets/${segment(id)}`;
  const projectPath = (id: string) => `projects/${segment(id)}`;
  const urls = {
    resolve: resolveUrl,
    file: (assetId: string, name: string, revision?: number) => revisionQuery(endpoint(`${assetPath(assetId)}/files/${filePath(name)}`), revision),
    artifact: (asset: Pick<Asset, 'id' | 'revision'>, artifact: Artifact) => revisionQuery(artifact.url ? resolveUrl(artifact.url) : endpoint(`${assetPath(asset.id)}/files/${filePath(artifact.name)}`), asset.revision),
    candidate: (asset: Pick<Asset, 'id' | 'revision'>, candidate: Candidate) => candidate.preview_url ? revisionQuery(resolveUrl(candidate.preview_url), asset.revision) : candidate.preview_name ? revisionQuery(endpoint(`${assetPath(asset.id)}/files/${filePath(candidate.preview_name)}`), asset.revision) : undefined,
    sourceFile: (source: Pick<Source, 'id' | 'revision'>) => revisionQuery(endpoint(`sources/${segment(source.id)}/file`), source.revision),
    assetExport: (id: string) => endpoint(`${assetPath(id)}/export`),
    projectExport: (id: string) => endpoint(`${projectPath(id)}/export`),
  };
  return {
    baseUrl: root, request, urls,
    health: (signal?: AbortSignal) => request<Health>('health', { signal }),
    projects: (signal?: AbortSignal) => request<Project[]>('projects', { signal }),
    project: (id: string, signal?: AbortSignal) => request<ProjectDetail>(projectPath(id), { signal }),
    createProject: (name: string, description = '') => json<Project>('projects', 'POST', { name, description } satisfies ProjectCreate),
    updateProject: (id: string, value: ProjectUpdate) => json<Project>(projectPath(id), 'PATCH', value),
    forkProject: (id: string, value: ProjectFork = {}) => json<Project>(`${projectPath(id)}/fork`, 'POST', value),
    deleteProject: (id: string) => request<DeleteResult>(projectPath(id), { method: 'DELETE' }),
    importProject: (file: File) => { const data = new FormData(); data.append('file', file); return request<Project>('projects/import', { method: 'POST', body: data }); },
    upload: (id: string, file: File) => { const data = new FormData(); data.append('file', file); return request<Source>(`${projectPath(id)}/sources`, { method: 'POST', body: data }); },
    source: (id: string, signal?: AbortSignal) => request<Source>(`sources/${segment(id)}`, { signal }),
    replaceSource: (id: string, file: File, revision: number) => { const data = new FormData(); data.append('file', file); data.append('expected_revision', String(revision)); return request<Source>(`sources/${segment(id)}`, { method: 'PUT', body: data }); },
    deleteSource: (id: string) => request<DeleteResult>(`sources/${segment(id)}`, { method: 'DELETE' }),
    asset: (id: string, signal?: AbortSignal) => request<Asset>(assetPath(id), { signal }),
    createAsset: (id: string, value: AssetCreate) => json<Asset>(`${projectPath(id)}/assets`, 'POST', value),
    updateAsset: (id: string, value: AssetUpdate) => json<Asset>(assetPath(id), 'PATCH', value),
    deleteAsset: (id: string) => request<DeleteResult>(assetPath(id), { method: 'DELETE' }),
    fork: (id: string, value: AssetFork = {}) => json<Asset>(`${assetPath(id)}/fork`, 'POST', value),
    history: (id: string, signal?: AbortSignal) => request<Revision[]>(`${assetPath(id)}/history`, { signal }),
    restore: (id: string, revision: number, expected_revision: number) => json<Asset>(`${assetPath(id)}/restore`, 'POST', { revision, expected_revision } satisfies AssetRestore),
    edge: (id: string, source: string, target: string) => json<Edge>(`${projectPath(id)}/edges`, 'POST', { source, target } satisfies EdgeCreate),
    deleteEdge: (id: string, edge: string) => request<DeleteResult>(`${projectPath(id)}/edges/${segment(edge)}`, { method: 'DELETE' }),
    run: (id: string, scope: NonNullable<AssetRun['scope']>, action: NonNullable<AssetRun['action']>) => json<Job>(`${assetPath(id)}/run`, 'POST', { scope, action } satisfies AssetRun),
    job: (id: string) => request<Job>(`jobs/${segment(id)}`),
    cancel: (id: string) => json<Job>(`jobs/${segment(id)}/cancel`, 'POST', {}),
    select: (id: string, candidate_id: string, expected_revision: number) => json<Asset>(`${assetPath(id)}/select`, 'POST', { candidate_id, expected_revision } satisfies AssetSelect),
    chat: (id: string, value: ChatCreate) => json<Job>(`${projectPath(id)}/chat`, 'POST', value),
    apply: (id: string, expected_revision: number) => json<Asset>(`proposals/${segment(id)}/apply`, 'POST', { expected_revision } satisfies ProposalApply),
    settings: () => request<SettingsDTO>('settings'),
    saveSettings: (value: SettingsUpdate) => json<SettingsDTO>('settings', 'PUT', value),
    providerModels: (kind: ProviderKind, value: ProviderProbe = {}, signal?: AbortSignal) => request<ProviderModels>(`providers/${segment(kind)}/models`, { method: 'POST', body: JSON.stringify(value), signal }),
    providerTest: (kind: ProviderKind, value: ProviderProbe = {}, signal?: AbortSignal) => request<ProviderTest>(`providers/${segment(kind)}/test`, { method: 'POST', body: JSON.stringify(value), signal }),
  };
}

export type ApiClient = ReturnType<typeof createApiClient>;
// Defer resolving global fetch so a browser host or test can install its own
// transport without rebuilding the UI or importing any React code.
export const api = createApiClient('/api/v1', (input, init) => globalThis.fetch(input, init));
