import { afterEach, describe, expect, expectTypeOf, it, vi } from 'vitest';
import { ApiError, api, columnNames, createApiClient, parseSpec, settingsPayload, workflowLayout } from './api';
import type { Asset, AssetCreate, AssetUpdate, Edge, ProviderProbe, SettingsUpdate, Source } from './api';

const reply = (value: unknown, status = 200, headers?: HeadersInit) => new Response(JSON.stringify(value), { status, headers });
const observedSource: Source = { id: 'source', project_id: 'project', name: 'observations.csv', kind: 'data', size: 24, revision: 1, created_at: '2026-01-01', updated_at: '2026-01-01', columns: ['actual_x', 'actual_y'], unique_values: { included: [true, false, null] } };
const makeAsset = (id: string): Asset => ({ id, project_id: 'project', kind: 'plot', title: id, caption: '', source_ids: [], spec: {}, revision: 3, status: 'draft', artifacts: [], candidates: [], created_at: '2026-01-01', updated_at: '2026-01-01' });

afterEach(() => vi.unstubAllGlobals());

describe('versioned, framework-independent API client', () => {
  it('encodes each resource identifier and keeps independent client transports', async () => {
    const first = vi.fn<typeof fetch>().mockResolvedValue(reply({}));
    const second = vi.fn<typeof fetch>().mockResolvedValue(reply([]));
    const client = createApiClient('/paper/api/v1/', first);
    await client.updateAsset('id/with ?#é', { expected_revision: 3, caption: 'Measured caption' });
    await createApiClient('https://backend.example/studio/api/v1', second).projects();
    expect(first.mock.calls[0][0]).toBe('/paper/api/v1/assets/id%2Fwith%20%3F%23%C3%A9');
    expect(first.mock.calls[0][1]?.method).toBe('PATCH');
    expect(JSON.parse(String(first.mock.calls[0][1]?.body))).toEqual({ expected_revision: 3, caption: 'Measured caption' });
    expect(new Headers(first.mock.calls[0][1]?.headers).get('Content-Type')).toBe('application/json');
    expect(second.mock.calls[0][0]).toBe('https://backend.example/studio/api/v1/projects');
    expect(first).toHaveBeenCalledTimes(1);
  });
  it('reads a current asset directly and forwards its cancellation signal', async () => {
    const asset = makeAsset('a/b');
    const transport = vi.fn<typeof fetch>().mockResolvedValue(reply(asset));
    const client = createApiClient('/paper/api/v1', transport);
    const controller = new AbortController();
    expectTypeOf(client.asset).returns.toEqualTypeOf<Promise<Asset>>();
    expect(await client.asset(asset.id, controller.signal)).toEqual(asset);
    expect(transport.mock.calls[0][0]).toBe('/paper/api/v1/assets/a%2Fb');
    expect(transport.mock.calls[0][1]?.signal).toBe(controller.signal);
    expect(transport.mock.calls[0][1]?.method).toBeUndefined();
  });
  it('rebases server links through the configured API prefix without losing cache identity', () => {
    const client = createApiClient('https://backend.example/studio/api/v1'); const asset = makeAsset('a/b');
    expect(client.urls.artifact(asset, { name: 'figure.svg', format: 'svg', url: '/api/v1/assets/a/files/figure.svg?generation=g%2F2&revision=1#layer' })).toBe('https://backend.example/studio/api/v1/assets/a/files/figure.svg?generation=g%2F2&revision=3#layer');
    expect(client.urls.resolve('/api/assets/a/export')).toBe('https://backend.example/studio/api/v1/assets/a/export');
    expect(client.urls.resolve('/public/palette.json')).toBe('https://backend.example/public/palette.json');
    expect(client.urls.resolve('assets/a/files/figure.png?generation=7')).toBe('https://backend.example/studio/api/v1/assets/a/files/figure.png?generation=7');
    expect(client.urls.resolve('https://cdn.example/figure.png?signature=a')).toBe('https://cdn.example/figure.png?signature=a');
    expect(createApiClient('/paper/api/v1').urls.resolve('/api/v1/assets/a/export')).toBe('/paper/api/v1/assets/a/export');
  });
  it('builds preview, source and export URLs with encoded nested filenames', () => {
    const client = createApiClient(); const asset = makeAsset('a/b');
    expect(client.urls.file(asset.id, "candidates/figure (final)#1's.png", 3)).toBe('/api/v1/assets/a%2Fb/files/candidates/figure%20%28final%29%231%27s.png?revision=3');
    expect(client.urls.artifact(asset, { name: 'candidates/figure.png', format: 'png' })).toBe('/api/v1/assets/a%2Fb/files/candidates/figure.png?revision=3');
    expect(client.urls.candidate(asset, { id: 'actual', label: 'Actual', preview_name: 'candidates/actual.png', preview_url: '/api/v1/assets/a/files/candidates/actual.png?generation=7', files: [] })).toBe('/api/v1/assets/a/files/candidates/actual.png?generation=7&revision=3');
    expect(client.urls.sourceFile({ id: 'source/a', revision: 2 })).toBe('/api/v1/sources/source%2Fa/file?revision=2');
    expect(client.urls.assetExport('asset/a')).toBe('/api/v1/assets/asset%2Fa/export');
    expect(client.urls.projectExport('project/a')).toBe('/api/v1/projects/project%2Fa/export');
    expect(() => client.urls.file('a', '../secret')).toThrow(TypeError);
    expect(() => client.urls.file('a', 'dir//figure.png')).toThrow(TypeError);
    expect(() => client.urls.resolve('javascript:alert(1)')).toThrow(TypeError);
    expect(() => client.urls.resolve('//other.example/file')).toThrow(TypeError);
    expect(() => createApiClient('https://user:secret@example/api/v1')).toThrow(TypeError);
    expect(() => createApiClient('/api/v1?key=secret')).toThrow(TypeError);
  });
  it('preserves structured revision conflicts and the server request identifier', async () => {
    const details = [{ location: ['body', 'expected_revision'], type: 'revision_conflict', message: 'Current revision is 4.' }];
    const transport = vi.fn<typeof fetch>().mockResolvedValue(reply({ detail: 'Legacy text', error: { code: 'revision_conflict', message: 'Revision changed.', details }, request_id: 'body-request' }, 409, { 'X-Request-ID': 'header-request' }));
    await expect(createApiClient('/api/v1', transport).updateAsset('asset', { expected_revision: 3 })).rejects.toMatchObject({ name: 'ApiError', status: 409, code: 'revision_conflict', message: 'Revision changed.', requestId: 'body-request', details });
    expect(transport).toHaveBeenCalledTimes(1);
  });
  it('supports header identifiers, nested fallbacks and legacy FastAPI errors', async () => {
    const transport = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(reply({ error: { code: 'forbidden', message: 'Blocked.' } }, 403, { 'X-Request-ID': 'header-request' }))
      .mockResolvedValueOnce(reply({ error: { code: 'not_found', message: 'Missing.', request_id: 'nested-request' } }, 404))
      .mockResolvedValueOnce(reply({ detail: 'Saved revision changed.' }, 409))
      .mockResolvedValueOnce(reply({ detail: [{ loc: ['body', 'x'], msg: 'Required' }] }, 422))
      .mockResolvedValueOnce(new Response('Bad gateway', { status: 502, headers: { 'X-Request-ID': 'gateway-request' } }));
    const client = createApiClient('/api/v1', transport);
    await expect(client.projects()).rejects.toMatchObject({ status: 403, requestId: 'header-request', code: 'forbidden' });
    await expect(client.projects()).rejects.toMatchObject({ status: 404, requestId: 'nested-request' });
    await expect(client.projects()).rejects.toMatchObject({ status: 409, message: 'Saved revision changed.', code: 'http_error' });
    await expect(client.projects()).rejects.toMatchObject({ status: 422, details: [{ loc: ['body', 'x'], msg: 'Required' }] });
    await expect(client.projects()).rejects.toMatchObject({ status: 502, requestId: 'gateway-request' });
  });
  it('transports original uploads and replacement revision as multipart without a JSON header', async () => {
    const transport = vi.fn<typeof fetch>().mockImplementation(async () => reply(observedSource));
    const client = createApiClient('/api/v1', transport);
    const file = new File(['x,y\n1,2'], 'measurements.csv', { type: 'text/csv' });
    await client.upload('project/a', file); await client.replaceSource('source/a', file, 4); await client.importProject(new File(['archive'], 'study.zip'));
    for (const [, init] of transport.mock.calls) { expect(init?.body).toBeInstanceOf(FormData); expect(new Headers(init?.headers).has('Content-Type')).toBe(false); }
    expect((transport.mock.calls[0][1]?.body as FormData).get('file')).toBe(file);
    expect(transport.mock.calls[0][0]).toBe('/api/v1/projects/project%2Fa/sources');
    expect(transport.mock.calls[1][1]?.method).toBe('PUT');
    expect((transport.mock.calls[1][1]?.body as FormData).get('expected_revision')).toBe('4');
    expect(transport.mock.calls[2][0]).toBe('/api/v1/projects/import');
  });
  it('keeps caller headers and aborts, and never retries a failed transport', async () => {
    const abort = new DOMException('Cancelled', 'AbortError');
    const transport = vi.fn<typeof fetch>().mockResolvedValueOnce(reply({ status: 'ok' })).mockRejectedValueOnce(abort).mockRejectedValueOnce(new TypeError('Offline'));
    const client = createApiClient('/api/v1', transport);
    await client.request('health', { headers: new Headers({ 'X-Client': 'other-ui' }) });
    expect(new Headers(transport.mock.calls[0][1]?.headers).get('X-Client')).toBe('other-ui');
    await expect(client.project('project')).rejects.toBe(abort);
    await expect(client.project('project')).rejects.toMatchObject({ name: 'ApiError', status: 0, code: 'network_error' });
    expect(transport).toHaveBeenCalledTimes(3);
  });
  it('does not treat an unreadable success body as a valid response', async () => {
    const transport = vi.fn<typeof fetch>().mockResolvedValueOnce(new Response('not JSON', { status: 200, headers: { 'X-Request-ID': 'response-request' } })).mockResolvedValueOnce(new Response(null, { status: 204 }));
    const client = createApiClient('/api/v1', transport);
    await expect(client.projects()).rejects.toMatchObject({ status: 200, code: 'invalid_response', requestId: 'response-request' });
    expect(await client.request('optional-empty')).toBeUndefined();
    expect(new ApiError('Conflict', 409)).toBeInstanceOf(Error);
  });
  it('retains the compatibility API while defaulting to the versioned routes', async () => {
    const transport = vi.fn<typeof fetch>().mockResolvedValue(reply([])); vi.stubGlobal('fetch', transport);
    await api.projects(); expect(transport.mock.calls[0][0]).toBe('/api/v1/projects');
  });
  it('sends typed temporary provider configurations and preserves provider error codes', async () => {
    const transport = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(reply({ models: [{ id: 'actual-provider-id' }], checked_at: '2026-01-01' }))
      .mockResolvedValueOnce(reply({ request_id: 'provider-request', error: { code: 'provider_authentication_failed', message: 'Provider rejected the credentials.' } }, 502));
    const client = createApiClient('/proxy/api/v1', transport); const config = { base_url: 'https://temporary.example/v1', model: 'typed-model', clear_api_key: true };
    expectTypeOf(client.providerModels).parameter(1).toEqualTypeOf<ProviderProbe | undefined>();
    const result = await client.providerModels('text', { config });
    expect(result.models).toEqual([{ id: 'actual-provider-id' }]);
    expect(transport.mock.calls[0][0]).toBe('/proxy/api/v1/providers/text/models');
    expect(JSON.parse(String(transport.mock.calls[0][1]?.body))).toEqual({ config });
    await expect(client.providerTest('image', {})).rejects.toMatchObject({ code: 'provider_authentication_failed', requestId: 'provider-request', status: 502 });
    expect(transport.mock.calls[1][0]).toBe('/proxy/api/v1/providers/image/test');
  });
  it('uses generated request types for writes without admitting read-only metadata', () => {
    const client = createApiClient();
    expectTypeOf(client.updateAsset).parameter(1).toEqualTypeOf<AssetUpdate>();
    expectTypeOf(client.createAsset).parameter(1).toEqualTypeOf<AssetCreate>();
    expectTypeOf(client.saveSettings).parameter(0).toEqualTypeOf<SettingsUpdate>();
    // Compiled by tsc: another UI cannot send incomplete or mismatched DTOs.
    const invalidWrites = () => {
      // @ts-expect-error Expected revision is mandatory on an asset edit.
      void client.updateAsset('asset', { caption: 'Caption' });
      // @ts-expect-error Asset creation must provide a title.
      void client.createAsset('project', { kind: 'plot' });
      // @ts-expect-error A run action is a closed contract enum.
      void client.run('asset', 'single', 'publish');
      // @ts-expect-error Server spending metadata cannot be saved as settings.
      void client.saveSettings({ spent: 20 });
      // @ts-expect-error Redacted key metadata belongs to the read DTO only.
      void client.saveSettings({ text: { has_api_key: true } });
    };
    expect(typeof invalidWrites).toBe('function');
  });
});

describe('UI data helpers preserve scientific source content', () => {
  it('removes redacted credential and budget metadata while retaining explicit key clearing', () => {
    const payload = settingsPayload({ text: { model: 'local', api_key: '', has_api_key: true, clear_api_key: true }, image: { model: '', has_api_key: false }, budget_usd: 10, spent: 1, reserved: 2, available: 7 });
    expect(payload).toEqual({ text: { model: 'local', clear_api_key: true }, image: { model: '' }, budget_usd: 10 });
    expect(JSON.stringify(payload)).not.toContain('has_api_key');
  });
  it('omits nullable read fields from settings patches and preserves provider JSON parameters', () => {
    const payload = settingsPayload({ text: { has_api_key: true, api: null, model: null, pricing: { input_per_million: null, output_per_million: 2, currency: 'USD' }, model_parameters: { stop: null, seed: 7 }, provider_receipt: 'read-only metadata' }, image: { has_api_key: false, base_url: null } });
    expect(payload).toEqual({ text: { pricing: { output_per_million: 2, currency: 'USD' }, model_parameters: { stop: null, seed: 7 } }, image: {} });
  });
  it('keeps source column names and category values, and rejects array/scalar specifications', () => {
    expect(columnNames(observedSource)).toEqual(['actual_x', 'actual_y']);
    expect(observedSource.unique_values?.included).toEqual([true, false, null]);
    expect(() => parseSpec('[]')).toThrow('JSON object'); expect(() => parseSpec('null')).toThrow('JSON object');
    expect(parseSpec('{"formula":"actual source formula","shape":[2,3]}')).toEqual({ formula: 'actual source formula', shape: [2, 3] });
  });
  it('keeps the actual workflow dependency order', () => {
    const assets = ['a', 'b', 'c'].map(makeAsset);
    const edges: Edge[] = [{ id: 'ab', project_id: 'project', source: 'a', target: 'b', created_at: '2026-01-01' }, { id: 'bc', project_id: 'project', source: 'b', target: 'c', created_at: '2026-01-01' }];
    const nodes = workflowLayout(assets, edges); expect(nodes[0].x).toBeLessThan(nodes[1].x); expect(nodes[1].x).toBeLessThan(nodes[2].x);
  });
});
