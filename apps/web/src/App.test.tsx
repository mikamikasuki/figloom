import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import App from './App';
import type { Asset, Candidate, Job, ProjectDetail, Settings } from './api';

const asset: Asset = { id: 'asset-1', project_id: 'project-1', kind: 'plot', title: 'Observed accuracy', caption: 'Original caption', source_ids: ['source-1'], spec: { source_id: 'source-1', x: 'regularization', y: 'accuracy', group: 'method', facet: null, aggregation: 'mean', interval: 'ci95', chart_type: 'line', width: 'wide' }, revision: 1, status: 'ready', artifacts: [{ name: 'figure.svg', format: 'svg', url: '/api/v1/assets/asset-1/files/figure.svg' }], candidates: [], created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z' };
let state: ProjectDetail; let settings: Settings; let calls: { path: string; method: string; body: unknown }[];
let conflict: boolean; let deferredSelect: (() => Promise<Response>) | undefined;
const candidates = (): Candidate[] => Array.from({ length: 5 }, (_, index) => { const id = `candidate-${index + 1}`; return { id, label: `Candidate ${index + 1}`, preview_name: `${id}/figure.png`, preview_url: `/api/v1/assets/asset-1/files/${id}/figure.png?generation=test-${index + 1}`, files: ['component.png', 'figure.png', 'figure.svg', 'figure.pdf'].map(name => ({ name: `${id}/${name}`, format: name.split('.').pop()!, url: `/api/v1/assets/asset-1/files/${id}/${name}?generation=test-${index + 1}` })), outputs: { png: `${id}/figure.png`, svg: `${id}/figure.svg`, pdf: `${id}/figure.pdf` }, caption: `Caption ${index + 1}`, review: { status: 'fixture-review' } }; });
beforeEach(() => {
  localStorage.clear(); calls = []; conflict = false; deferredSelect = undefined;
  settings = { text: {}, image: {}, budget_usd: 10, spent: 0, reserved: 0, available: 10 };
  state = { project: { id: 'project-1', name: 'Measured study', description: '', revision: 1, created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z' }, assets: [structuredClone(asset)], sources: [{ id: 'source-1', project_id: 'project-1', name: 'observations.csv', kind: 'data', size: 100, revision: 1, updated_at: '2026-01-01T00:00:00Z', columns: ['regularization', 'method', 'seed', 'accuracy'], preview: [{ regularization: 1, method: 'observed-model', seed: 0, accuracy: .82 }], created_at: '2026-01-01T00:00:00Z' }], edges: [], jobs: [], messages: [], proposals: [] };
  vi.stubGlobal('fetch', vi.fn(async (path: string, init?: RequestInit) => {
    const method = init?.method || 'GET'; const body = typeof init?.body === 'string' ? JSON.parse(init.body) : init?.body;
    calls.push({ path, method, body });
    const response = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status });
    if (path === '/api/v1/settings') { if (method === 'PUT') settings = body; return response(settings); }
    if (path === '/api/v1/projects') return response([state.project]);
    if (path === '/api/v1/projects/project-1') return response(state);
    if (path === '/api/v1/sources/source-1') return response(state.sources[0]);
    if (path === '/api/v1/assets/asset-1/history') return response([structuredClone(state.assets[0])]);
    if (path === '/api/v1/assets/asset-1' && method === 'PATCH') {
      if (conflict) { state.assets[0].revision = 2; return response({ detail: 'The saved revision changed.' }, 409); }
      state.assets[0] = { ...state.assets[0], ...body, revision: state.assets[0].revision + 1 }; return response(state.assets[0]);
    }
    if (path === '/api/v1/assets/asset-1/select') {
      if (deferredSelect) return deferredSelect();
      const selected = state.assets[0].candidates.find(item => item.id === body.candidate_id)!;
      state.assets[0] = { ...state.assets[0], revision: state.assets[0].revision + 1, artifacts: selected.files, caption: selected.caption || '', review: selected.review, candidates: state.assets[0].candidates.map(item => ({ ...item, selected: item.id === selected.id })) };
      return response(state.assets[0]);
    }
    if (path === '/api/v1/assets/asset-1/run') { const job: Job = { id: 'job-1', project_id: 'project-1', asset_id: 'asset-1', action: body.action, scope: body.scope, status: 'queued', stage: 'queued', created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z' }; state.jobs.push(job); return response(job); }
    if (path === '/api/v1/jobs/job-1/cancel') { state.jobs[0].status = 'cancelled'; return response(state.jobs[0]); }
    if (path === '/api/v1/projects/project-1/chat') { state.messages.push({ id: 'message-1', project_id: 'project-1', role: 'user', content: body.message, asset_id: body.asset_id, created_at: '2026-01-01T00:00:00Z' }); return response({ id: 'chat-job', project_id: 'project-1', asset_id: 'asset-1', action: 'chat', scope: 'single', status: 'queued', stage: 'queued', created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z', result: null }); }
    return response({ detail: `Unhandled test route ${method} ${path}` }, 404);
  }));
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe('usable source-grounded workspace', () => {
  it('shows real previews and only columns from the uploaded source', async () => {
    render(<App />);
    await screen.findByRole('heading', { name: 'Observed accuracy' });
    expect(screen.getByRole('img', { name: 'Observed accuracy' }).getAttribute('src')).toBe('/api/v1/assets/asset-1/files/figure.svg?revision=1');
    const x = await screen.findByLabelText('X / category');
    await waitFor(() => expect(Array.from((x as HTMLSelectElement).options).map(option => option.value)).toEqual(['', 'regularization', 'method', 'seed', 'accuracy']));
    expect((screen.getByRole('button', { name: 'Generate' }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText('Text model not configured.')).toBeTruthy();
  });
  it('saves the actual mapping with optimistic revision control, then queues the chosen scope', async () => {
    render(<App />); await screen.findByLabelText('X / category');
    fireEvent.change(screen.getByLabelText('Uncertainty'), { target: { value: 'se' } });
    fireEvent.change(screen.getByLabelText('Run scope'), { target: { value: 'affected' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save & render' }));
    await screen.findByText('Render queued. See Jobs for status.');
    const patch = calls.find(call => call.method === 'PATCH');
    expect(patch?.body).toMatchObject({ expected_revision: 1, spec: { interval: 'se', source_id: 'source-1' } });
    expect(calls.find(call => call.path.endsWith('/run'))?.body).toEqual({ scope: 'affected', action: 'render' });
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    await waitFor(() => expect(calls.some(call => call.path.endsWith('/cancel'))).toBe(true));
  });
  it('preserves local edits on conflict and requires a deliberate reload', async () => {
    render(<App />); await screen.findByLabelText('X / category');
    fireEvent.click(screen.getByRole('tab', { name: 'Caption' }));
    fireEvent.change(screen.getByLabelText('Figure caption'), { target: { value: 'Keep this local caption.' } });
    conflict = true; fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await screen.findByText(/Your local edits are preserved/);
    expect((screen.getByLabelText('Figure caption') as HTMLTextAreaElement).value).toBe('Keep this local caption.');
    expect(screen.getByRole('button', { name: 'Reload latest' })).toBeTruthy();
    expect((screen.getByRole('button', { name: 'Save' }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getByRole('button', { name: 'Reload latest' }));
    expect((screen.getByLabelText('Figure caption') as HTMLTextAreaElement).value).toBe('Original caption');
  });
  it('sends selected-asset chat context without applying an edit or inventing a reply', async () => {
    settings = { ...settings, text: { api: 'ollama', base_url: 'http://localhost:11434', model: 'unit-model' } };
    render(<App />); await screen.findByLabelText('X / category');
    fireEvent.change(screen.getByLabelText('Chat message'), { target: { value: 'Clarify the caption.' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send message' }));
    await waitFor(() => expect(calls.some(call => call.path.endsWith('/chat'))).toBe(true));
    expect(calls.find(call => call.path.endsWith('/chat'))?.body).toEqual({ message: 'Clarify the caption.', asset_id: 'asset-1', expected_revision: 1 });
    expect(calls.filter(call => call.path.includes('/apply'))).toHaveLength(0);
    expect(state.assets[0].caption).toBe('Original caption');
  });

  it('previews all five candidates without adopting them, and downloads the current candidate format', async () => {
    state.assets[0].candidates = candidates();
    render(<App />); await screen.findByLabelText('X / category');
    fireEvent.click(screen.getByRole('button', { name: 'Preview candidate 2: Candidate 2' }));
    expect(screen.getByRole('img', { name: 'Observed accuracy · Candidate 2' }).getAttribute('src')).toBe('/api/v1/assets/asset-1/files/candidate-2/figure.svg?generation=test-2&revision=1');
    fireEvent.click(screen.getByRole('button', { name: 'PNG' }));
    expect(screen.getByRole('img', { name: 'Observed accuracy · Candidate 2' }).getAttribute('src')).toBe('/api/v1/assets/asset-1/files/candidate-2/figure.png?generation=test-2&revision=1');
    expect(calls.some(call => call.path.endsWith('/select'))).toBe(false);
    expect(state.assets[0].revision).toBe(1);
    fireEvent.click(screen.getByRole('button', { name: 'PDF' }));
    const pdf = screen.getByTitle('Observed accuracy · Candidate 2 PDF');
    expect(pdf.getAttribute('src')).toBe('/api/v1/assets/asset-1/files/candidate-2/figure.pdf?generation=test-2&revision=1');
    expect(screen.getByRole('link', { name: 'Download PDF' }).getAttribute('href')).toBe(pdf.getAttribute('src'));
    fireEvent.keyDown(screen.getByLabelText('Figure preview'), { key: 'ArrowRight' });
    expect(screen.getByTitle('Observed accuracy · Candidate 3 PDF')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Show figure gallery' }));
    expect(screen.getByRole('img', { name: 'Observed accuracy' }).getAttribute('src')).toBe('/api/v1/assets/asset-1/files/candidate-3/figure.png?generation=test-3&revision=1');
    fireEvent.click(screen.getByRole('button', { name: /Observed accuracy.*revision 1/ }));
    expect(screen.getByTitle('Observed accuracy · Candidate 3 PDF')).toBeTruthy();
    expect(screen.getAllByRole('button', { name: /^Preview candidate/ })).toHaveLength(5);
    fireEvent.click(screen.getByRole('button', { name: 'Apply candidate' }));
    await screen.findByText('Candidate applied.');
    expect(calls.find(call => call.path.endsWith('/select'))?.body).toEqual({ candidate_id: 'candidate-3', expected_revision: 1 });
    expect(state.assets[0].revision).toBe(2);
    expect(state.assets[0].candidates).toHaveLength(5);
    expect(state.assets[0].review).toEqual({ status: 'fixture-review' });
    expect(screen.getAllByRole('button', { name: /^Preview candidate/ })).toHaveLength(5);
  });

  it('shows candidate file absence and load failure without substituting the saved output', async () => {
    state.assets[0].candidates = [{ id: 'missing', label: 'Missing file', preview_name: '', files: [] }, candidates()[0]];
    render(<App />); await screen.findByLabelText('X / category');
    fireEvent.click(screen.getByRole('button', { name: 'Preview candidate 1: Missing file' }));
    expect(screen.getByRole('heading', { name: 'No preview file' })).toBeTruthy();
    expect(screen.queryByRole('img', { name: 'Observed accuracy' })).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Preview candidate 2: Candidate 1' }));
    fireEvent.error(screen.getByRole('img', { name: 'Observed accuracy · Candidate 1' }));
    expect(screen.getByRole('heading', { name: 'Preview file unavailable' })).toBeTruthy();
    expect(calls.some(call => call.path.endsWith('/select'))).toBe(false);
  });
  it('keeps candidate preview usable while local edits block adoption', async () => {
    state.assets[0].candidates = candidates();
    render(<App />); await screen.findByLabelText('X / category');
    fireEvent.click(screen.getByRole('tab', { name: 'Caption' }));
    fireEvent.change(screen.getByLabelText('Figure caption'), { target: { value: 'Unsaved caption' } });
    fireEvent.click(screen.getByRole('button', { name: 'Preview candidate 5: Candidate 5' }));
    expect((screen.getByRole('button', { name: 'Apply candidate' }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByRole('img', { name: 'Observed accuracy · Candidate 5' })).toBeTruthy();
    expect(calls.some(call => call.path.endsWith('/select'))).toBe(false);
    expect((screen.getByLabelText('Figure caption') as HTMLTextAreaElement).value).toBe('Unsaved caption');
  });
  it('does not replace a newly selected figure draft with a late candidate adoption response', async () => {
    state.assets[0].candidates = candidates();
    state.assets.push({ ...structuredClone(asset), id: 'asset-2', title: 'Other figure', caption: 'Other caption' });
    let resolve!: (response: Response) => void;
    const selection = new Promise<Response>(done => { resolve = done; });
    deferredSelect = () => selection;
    render(<App />); await screen.findByLabelText('X / category');
    fireEvent.click(screen.getByRole('button', { name: 'Preview candidate 2: Candidate 2' }));
    fireEvent.click(screen.getByRole('button', { name: 'Apply candidate' }));
    await waitFor(() => expect(calls.some(call => call.path.endsWith('/select'))).toBe(true));
    fireEvent.click(screen.getByRole('button', { name: /Other figure.*plot/ }));
    await screen.findByRole('heading', { name: 'Other figure' });
    resolve(new Response(JSON.stringify({ ...state.assets[0], revision: 2, caption: 'Late adopted caption' })));
    await screen.findByText('Candidate applied.');
    expect((screen.getByLabelText('Figure title') as HTMLInputElement).value).toBe('Other figure');
    fireEvent.click(screen.getByRole('tab', { name: 'Caption' }));
    expect((screen.getByLabelText('Figure caption') as HTMLTextAreaElement).value).toBe('Other caption');
  });
  it('keeps invalid JSON local and never posts it as a successful save', async () => {
    render(<App />); await screen.findByLabelText('X / category');
    fireEvent.click(screen.getByRole('button', { name: 'JSON editor' }));
    fireEvent.change(screen.getByLabelText('Full editable specification'), { target: { value: '{bad json' } });
    expect((screen.getByRole('button', { name: 'Save' }) as HTMLButtonElement).disabled).toBe(true);
    expect(calls.some(call => call.method === 'PATCH')).toBe(false);
  });
});
