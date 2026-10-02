import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { SettingsModal } from './SettingsModal';
import { settingsPayload } from './api';
import type { Settings } from './api';

const settings: Settings = { text: { base_url: 'https://saved.example/v1', model: 'saved-model', api: 'chat_completions', has_api_key: true, input_price_per_million: 2 }, image: { api: 'images', model: 'saved-image', has_api_key: false }, budget_usd: 10, spent: 1, reserved: 2, available: 7 };
const response = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });
function show(initial = settings) { const save = vi.fn().mockResolvedValue(true); const close = vi.fn(); render(<SettingsModal settings={initial} pending={false} onSave={save} onClose={close} />); return { save, close }; }
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe('provider metadata settings', () => {
  it('loads real model IDs and checks temporary configuration without saving or generating', async () => {
    const transport = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(response({ models: [{ id: 'provider-model' }, { id: 'second-model' }], checked_at: '2026-01-01T12:00:00Z' }))
      .mockResolvedValueOnce(response({ reachable: true, model: 'provider-model', model_available: true, checked_at: '2026-01-01T12:01:00Z' }));
    vi.stubGlobal('fetch', transport); const { save } = show();
    expect(transport).not.toHaveBeenCalled();
    fireEvent.change(screen.getByLabelText('Endpoint'), { target: { value: 'https://temporary.example/v1' } });
    fireEvent.change(screen.getByLabelText('Text API key'), { target: { value: 'temporary-key' } });
    fireEvent.click(screen.getByRole('button', { name: 'Show text API key' }));
    expect((screen.getByLabelText('Text API key') as HTMLInputElement).type).toBe('text');
    fireEvent.click(screen.getByRole('button', { name: 'Hide text API key' }));
    expect((screen.getByLabelText('Text API key') as HTMLInputElement).type).toBe('password');
    fireEvent.click(screen.getByRole('button', { name: 'Load models' }));
    await screen.findByLabelText('Listed models');
    expect(transport.mock.calls[0][0]).toBe('/api/v1/providers/text/models');
    expect(JSON.parse(String(transport.mock.calls[0][1]?.body))).toEqual({ config: { base_url: 'https://temporary.example/v1', model: 'saved-model', api: 'chat_completions', api_key: 'temporary-key', input_price_per_million: 2, max_output_tokens: 4096, timeout: 120 } });
    fireEvent.change(screen.getByLabelText('Listed models'), { target: { value: 'provider-model' } });
    expect((screen.getByLabelText('Text model') as HTMLInputElement).value).toBe('provider-model');
    fireEvent.click(screen.getByRole('button', { name: 'Check connection' }));
    await screen.findByText('Endpoint reachable');
    expect(screen.getByText('Model listed: provider-model')).toBeTruthy();
    expect(transport.mock.calls[1][0]).toBe('/api/v1/providers/text/test');
    expect(save).not.toHaveBeenCalled();
    expect(transport.mock.calls.every(([url]) => String(url).includes('/providers/text/'))).toBe(true);
    fireEvent.change(screen.getByLabelText('Text model'), { target: { value: 'manual-model' } });
    expect(screen.queryByText('Endpoint reachable')).toBeNull();
    expect(screen.getByLabelText('Listed models')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Save settings' }));
    await waitFor(() => expect(save).toHaveBeenCalledOnce());
    expect(save.mock.calls[0][0].text.model).toBe('manual-model');
  });
  it('keeps manual model entry available when listing metadata is unsupported', async () => {
    const transport = vi.fn<typeof fetch>().mockResolvedValue(response({ detail: 'No model listing endpoint.', request_id: 'probe-request', error: { code: 'provider_endpoint_not_found', message: 'No model listing endpoint.' } }, 502));
    vi.stubGlobal('fetch', transport); const { save } = show();
    fireEvent.click(screen.getByRole('button', { name: 'Load models' }));
    await screen.findByText(/Model listing unavailable.*Enter a model ID manually/);
    fireEvent.change(screen.getByLabelText('Text model'), { target: { value: 'unlisted-model' } });
    expect((screen.getByRole('button', { name: 'Save settings' }) as HTMLButtonElement).disabled).toBe(false);
    fireEvent.click(screen.getByRole('button', { name: 'Save settings' }));
    await waitFor(() => expect(save).toHaveBeenCalledOnce());
    expect(save.mock.calls[0][0].text.model).toBe('unlisted-model');
  });
  it('invalidates late probe responses and saved-key hints after an endpoint edit', async () => {
    let resolve!: (response: Response) => void;
    const pending = new Promise<Response>(done => { resolve = done; });
    const transport = vi.fn<typeof fetch>().mockReturnValueOnce(pending);
    vi.stubGlobal('fetch', transport); show();
    expect((screen.getByLabelText('Text API key') as HTMLInputElement).placeholder).toContain('Saved for this endpoint');
    fireEvent.click(screen.getByRole('button', { name: 'Check connection' }));
    const signal = transport.mock.calls[0][1]?.signal;
    fireEvent.change(screen.getByLabelText('Endpoint'), { target: { value: 'https://new.example/v1' } });
    expect(signal?.aborted).toBe(true);
    expect((screen.getByLabelText('Text API key') as HTMLInputElement).placeholder).toBe('API key');
    resolve(response({ reachable: true, model: 'saved-model', model_available: true, checked_at: '2026-01-01T12:00:00Z' }));
    await waitFor(() => expect((screen.getByRole('button', { name: 'Check connection' }) as HTMLButtonElement).disabled).toBe(false));
    expect(screen.queryByText('Endpoint reachable')).toBeNull();
  });
  it('presets only change endpoint/protocol and preserve entered model, key and prices', async () => {
    const transport = vi.fn<typeof fetch>().mockResolvedValue(response({ models: [], checked_at: '2026-01-01T12:00:00Z' }));
    vi.stubGlobal('fetch', transport); show();
    fireEvent.change(screen.getByLabelText('Text API key'), { target: { value: 'entered-key' } });
    fireEvent.change(screen.getByLabelText('Protocol preset'), { target: { value: 'ollama' } });
    expect((screen.getByLabelText('Endpoint') as HTMLInputElement).value).toBe('http://localhost:11434');
    expect((screen.getByLabelText('Text model') as HTMLInputElement).value).toBe('saved-model');
    expect((screen.getByLabelText('Text API key') as HTMLInputElement).value).toBe('entered-key');
    fireEvent.click(screen.getByRole('button', { name: 'Load models' }));
    await screen.findByText('No models returned. Enter a model ID manually.');
    expect(JSON.parse(String(transport.mock.calls[0][1]?.body)).config).toMatchObject({ api: 'ollama', base_url: 'http://localhost:11434', model: 'saved-model', api_key: 'entered-key', input_price_per_million: 2 });
  });
  it('checks image metadata without claiming generation support or disabling an unlisted model', async () => {
    const transport = vi.fn<typeof fetch>().mockResolvedValue(response({ reachable: true, model: 'saved-image', model_available: false, checked_at: '2026-01-01T12:00:00Z' }));
    vi.stubGlobal('fetch', transport); const { save } = show();
    fireEvent.click(screen.getByRole('tab', { name: 'Image' }));
    expect(screen.getByText('Save text changes before checking an inherited endpoint.')).toBeTruthy();
    expect((screen.getByLabelText('Image API key') as HTMLInputElement).placeholder).toBe('Blank uses the matching saved text key');
    fireEvent.change(screen.getByLabelText('Endpoint'), { target: { value: 'https://saved.example/v1' } });
    expect((screen.getByLabelText('Image API key') as HTMLInputElement).placeholder).toBe('API key');
    fireEvent.click(screen.getByRole('button', { name: 'Check connection' }));
    await screen.findByText('Model not listed: saved-image');
    expect(transport.mock.calls[0][0]).toBe('/api/v1/providers/image/test');
    expect(screen.queryByText(/generation supported/i)).toBeNull();
    expect(save).not.toHaveBeenCalled();
    expect((screen.getByRole('button', { name: 'Save settings' }) as HTMLButtonElement).disabled).toBe(false);
  });
  it('sends the displayed protocol, token and timeout defaults without inventing free pricing', async () => {
    const { save } = show({ text: { api: null, max_output_tokens: null, timeout: null, input_price_per_million: null }, image: {}, budget_usd: 0 });
    expect((screen.getByLabelText('Maximum output tokens') as HTMLInputElement).value).toBe('4096');
    expect((screen.getByLabelText('Timeout · seconds') as HTMLInputElement).value).toBe('120');
    for (const name of ['Input · USD / 1M tokens', 'Output · USD / 1M tokens', 'Maximum USD / request']) {
      expect((screen.getByLabelText(name) as HTMLInputElement).value).toBe('');
      expect((screen.getByLabelText(name) as HTMLInputElement).placeholder).toBe('Not configured');
    }
    fireEvent.change(screen.getByLabelText('Input · USD / 1M tokens'), { target: { value: '0' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save settings' }));
    await waitFor(() => expect(save).toHaveBeenCalledOnce());
    expect(settingsPayload(save.mock.calls[0][0])).toEqual({ text: { api: 'chat_completions', max_output_tokens: 4096, timeout: 120, input_price_per_million: 0 }, image: { api: 'images', timeout: 120 }, budget_usd: 0 });
  });
  it('recognizes the same saved connection when the endpoint includes a full operation suffix', () => {
    show();
    fireEvent.change(screen.getByLabelText('Endpoint'), { target: { value: 'https://SAVED.example:443/v1/responses/' } });
    expect((screen.getByLabelText('Text API key') as HTMLInputElement).placeholder).toContain('Saved for this endpoint');
    fireEvent.change(screen.getByLabelText('Endpoint'), { target: { value: 'https://saved.example/other/v1/responses' } });
    expect((screen.getByLabelText('Text API key') as HTMLInputElement).placeholder).toBe('API key');
  });
});
