import { useEffect, useRef, useState } from 'react';
import { AlertCircle, Check, Eye, EyeOff, KeyRound, LoaderCircle, Settings2, X } from 'lucide-react';
import { api, errorText, settingsPayload } from './api';
import type { ProviderKind, ProviderModels, ProviderTest, Settings, SettingsUpdate } from './api';

type Preset = 'openai' | 'responses' | 'ollama' | 'custom';
type ProbeState = { models?: ProviderModels; check?: ProviderTest; loading?: 'models' | 'test'; error?: string };
type EndpointUpdate = NonNullable<SettingsUpdate['text']>;
const normalized = (url?: string | null, protocol = 'chat_completions') => {
  try {
    const parsed = new URL(url || '');
    if (!['http:', 'https:'].includes(parsed.protocol) || parsed.username || parsed.password || parsed.search || parsed.hash) return '';
    let path = parsed.pathname.replace(/\/+$/, ''); let operationUrl = false;
    for (const suffix of ['/chat/completions', '/responses', '/images/generations', '/images/edits', '/models', '/api/chat', '/api/tags']) {
      if (path.endsWith(suffix)) { path = path.slice(0, -suffix.length); operationUrl = true; break; }
    }
    if (protocol === 'ollama') path = path.replace(/\/(api|v1)$/, '');
    else if (!path && !operationUrl) path = '/v1';
    return `${parsed.origin}${path}`;
  } catch { return ''; }
};
function currentPreset(kind: ProviderKind, settings: Settings): Preset {
  const endpoint = settings[kind]; const base = normalized(endpoint?.base_url, endpoint?.api || (kind === 'image' ? 'images' : 'chat_completions'));
  if (kind === 'text' && endpoint?.api === 'ollama' && base === 'http://localhost:11434') return 'ollama';
  if (base === 'https://api.openai.com/v1') return endpoint?.api === 'responses' ? 'responses' : 'openai';
  return 'custom';
}

export function SettingsModal({ settings, pending, error, onClose, onSave }: {
  settings: Settings; pending: boolean; error?: string;
  onClose: () => void; onSave: (value: Settings) => Promise<boolean>;
}) {
  const [value, setValue] = useState<Settings>({ ...settings, budget_usd: settings.budget_usd ?? 0, text: { ...settings.text, api: settings.text?.api || 'chat_completions', max_output_tokens: settings.text?.max_output_tokens ?? 4096, timeout: settings.text?.timeout ?? 120, api_key: '' }, image: { ...settings.image, api: settings.image?.api || 'images', timeout: settings.image?.timeout ?? 120, api_key: '' } });
  const [kind, setKind] = useState<ProviderKind>('text');
  const [presets, setPresets] = useState<Record<ProviderKind, Preset>>({ text: currentPreset('text', settings), image: currentPreset('image', settings) });
  const [showKeys, setShowKeys] = useState<Record<ProviderKind, boolean>>({ text: false, image: false });
  const [probes, setProbes] = useState<Record<ProviderKind, ProbeState>>({ text: {}, image: {} });
  const versions = useRef<Record<ProviderKind, number>>({ text: 0, image: 0 });
  const requests = useRef<Partial<Record<ProviderKind, AbortController>>>({});
  useEffect(() => () => { requests.current.text?.abort(); requests.current.image?.abort(); }, []);
  const provider = value[kind] || {};
  const probe = probes[kind];
  const endpoint = provider.base_url || (kind === 'image' ? settings.text?.base_url : '');
  const savedEndpoint = settings[kind]?.base_url || (kind === 'image' ? settings.text?.base_url : '');
  const savedKeyHere = Boolean(settings[kind]?.has_api_key && normalized(endpoint, provider.api || (kind === 'image' ? 'images' : 'chat_completions')) && normalized(endpoint, provider.api || (kind === 'image' ? 'images' : 'chat_completions')) === normalized(savedEndpoint, settings[kind]?.api || (kind === 'image' ? 'images' : 'chat_completions')));
  function invalidate(section: ProviderKind, clearModels = false) {
    versions.current[section]++; requests.current[section]?.abort();
    setProbes(previous => ({ ...previous, [section]: clearModels ? {} : { models: previous[section].models } }));
  }
  function field(section: ProviderKind, key: keyof EndpointUpdate, input: unknown) {
    invalidate(section, ['base_url', 'api', 'api_key', 'clear_api_key'].includes(key));
    if (key === 'base_url' || key === 'api') setPresets(previous => ({ ...previous, [section]: 'custom' }));
    setValue(previous => ({ ...previous, [section]: { ...previous[section], [key]: input } }));
  }
  function preset(selected: Preset) {
    invalidate(kind, true); setPresets(previous => ({ ...previous, [kind]: selected }));
    if (selected === 'custom') return;
    const config = selected === 'ollama' ? { api: 'ollama', base_url: 'http://localhost:11434' } : { api: kind === 'image' ? 'images' : selected === 'responses' ? 'responses' : 'chat_completions', base_url: 'https://api.openai.com/v1' };
    setValue(previous => ({ ...previous, [kind]: { ...previous[kind], ...config } }));
  }
  async function runProbe(action: 'models' | 'test') {
    const section = kind; requests.current[section]?.abort();
    const controller = new AbortController(); requests.current[section] = controller;
    const version = ++versions.current[section];
    setProbes(previous => ({ ...previous, [section]: { ...previous[section], loading: action, error: undefined } }));
    try {
      const config = settingsPayload(value)[section] || {};
      const result = action === 'models' ? await api.providerModels(section, { config }, controller.signal) : await api.providerTest(section, { config }, controller.signal);
      if (controller.signal.aborted || versions.current[section] !== version) return;
      setProbes(previous => ({ ...previous, [section]: { ...previous[section], loading: undefined, ...(action === 'models' ? { models: result as ProviderModels } : { check: result as ProviderTest }) } }));
    } catch (failure) {
      if (controller.signal.aborted || versions.current[section] !== version) return;
      setProbes(previous => ({ ...previous, [section]: { ...previous[section], loading: undefined, error: `${action === 'models' ? 'Model listing unavailable.' : 'Connection check failed.'} ${errorText(failure)}${action === 'models' ? ' Enter a model ID manually.' : ''}` } }));
    }
  }
  return <div className="modal-backdrop" onMouseDown={event => { if (event.target === event.currentTarget && !pending) onClose(); }}>
    <section className="modal settings-modal" role="dialog" aria-modal="true" aria-labelledby="settings-title">
      <header><div className="modal-heading"><Settings2 size={20} /><h2 id="settings-title">Settings</h2></div><button className="icon-button" onClick={onClose} aria-label="Close settings"><X size={18} /></button></header>
      <form onSubmit={async event => { event.preventDefault(); if (await onSave(value)) onClose(); }}>
        <div className="modal-scroll">
          {error && <div className="inline-error" role="alert"><AlertCircle size={16} />{error}</div>}
          <div className="provider-tabs" role="tablist" aria-label="Provider settings">{(['text', 'image'] as const).map(section => <button key={section} type="button" role="tab" aria-selected={kind === section} className={kind === section ? 'active' : ''} onClick={() => setKind(section)}>{section === 'text' ? 'Text' : 'Image'}</button>)}</div>
          <div className="form-grid" role="tabpanel" aria-label={`${kind} provider`}>
            <label className="full">Protocol preset<select aria-label="Protocol preset" value={presets[kind]} onChange={event => preset(event.target.value as Preset)}><option value="openai">OpenAI-compatible</option>{kind === 'text' && <><option value="responses">Responses</option><option value="ollama">Ollama</option></>}<option value="custom">Custom</option></select></label>
            <label className="full">Endpoint<input value={provider.base_url || ''} onChange={event => field(kind, 'base_url', event.target.value)} placeholder={kind === 'image' ? 'Blank uses the saved text endpoint' : 'https://…/v1 or http://localhost:11434'} /></label>
            <label className="full">Model<input aria-label={`${kind === 'text' ? 'Text' : 'Image'} model`} list={`provider-models-${kind}`} value={provider.model || ''} onChange={event => field(kind, 'model', event.target.value)} placeholder="Model ID" /><datalist id={`provider-models-${kind}`}>{probe.models?.models.map(model => <option key={model.id} value={model.id} />)}</datalist></label>
            {probe.models && probe.models.models.length > 0 && <label className="full">Listed models<select aria-label="Listed models" value={probe.models.models.some(model => model.id === provider.model) ? provider.model || '' : ''} onChange={event => { if (event.target.value) field(kind, 'model', event.target.value); }}><option value="">Select a model</option>{probe.models.models.map(model => <option key={model.id} value={model.id}>{model.id}</option>)}</select></label>}
            <label className="full">API key<span className="input-icon password-input"><KeyRound size={15} /><input aria-label={`${kind === 'text' ? 'Text' : 'Image'} API key`} type={showKeys[kind] ? 'text' : 'password'} autoComplete="new-password" value={provider.api_key || ''} onChange={event => field(kind, 'api_key', event.target.value)} placeholder={savedKeyHere && !provider.clear_api_key ? 'Saved for this endpoint · leave blank to keep' : kind === 'text' && provider.api === 'ollama' ? 'Optional for local Ollama' : kind === 'image' && !provider.base_url ? 'Blank uses the matching saved text key' : 'API key'} /><button type="button" className="icon-button" aria-label={`${showKeys[kind] ? 'Hide' : 'Show'} ${kind} API key`} onClick={() => setShowKeys(previous => ({ ...previous, [kind]: !previous[kind] }))}>{showKeys[kind] ? <EyeOff size={15} /> : <Eye size={15} />}</button></span></label>
            {settings[kind]?.has_api_key && <label className="check-label full"><input type="checkbox" checked={Boolean(provider.clear_api_key)} onChange={event => field(kind, 'clear_api_key', event.target.checked)} />Remove saved {kind} API key</label>}
          </div>
          {kind === 'image' && !provider.base_url && <p className="form-help">Save text changes before checking an inherited endpoint.</p>}
          <div className="provider-probe-actions"><button type="button" className="button small" disabled={pending || Boolean(probe.loading)} onClick={() => runProbe('models')}>{probe.loading === 'models' && <LoaderCircle size={13} className="spin" />}Load models</button><button type="button" className="button small" disabled={pending || Boolean(probe.loading)} onClick={() => runProbe('test')}>{probe.loading === 'test' && <LoaderCircle size={13} className="spin" />}Check connection</button></div>
          {probe.error && <div className="inline-error" role="alert"><AlertCircle size={15} />{probe.error}</div>}
          {probe.models?.models.length === 0 && <p className="form-help">No models returned. Enter a model ID manually.</p>}
          {probe.check && <div className="provider-check" role="status"><Check size={14} /><div><strong>Endpoint reachable</strong><p>{probe.check.model === null ? 'No model selected.' : probe.check.model_available === null ? `Model availability unknown: ${probe.check.model}` : probe.check.model_available ? `Model listed: ${probe.check.model}` : `Model not listed: ${probe.check.model}`}</p><time dateTime={probe.check.checked_at}>{new Date(probe.check.checked_at).toLocaleTimeString()}</time></div></div>}
          {kind === 'image' && <div className="form-grid section-space"><label>Image size<input value={provider.size || ''} onChange={event => field(kind, 'size', event.target.value)} placeholder="Provider-supported size" /></label><label>Quality<input value={provider.quality || ''} onChange={event => field(kind, 'quality', event.target.value)} placeholder="Provider-supported quality" /></label></div>}
          <details className="details provider-advanced"><summary>Advanced</summary><div className="form-grid section-space">
            <label>API protocol<select value={provider.api || (kind === 'text' ? 'chat_completions' : 'images')} onChange={event => field(kind, 'api', event.target.value)}>{kind === 'text' ? <><option value="chat_completions">Chat completions</option><option value="responses">Responses</option><option value="ollama">Ollama</option></> : <option value="images">Images API</option>}</select></label>
            <label>Maximum USD / request<input type="number" min="0" step="any" placeholder="Not configured" value={provider.max_request_usd ?? ''} onChange={event => field(kind, 'max_request_usd', event.target.value === '' ? undefined : Number(event.target.value))} /></label>
            {kind === 'text' && <><label>Input · USD / 1M tokens<input type="number" min="0" step="any" placeholder="Not configured" value={provider.input_price_per_million ?? ''} onChange={event => field(kind, 'input_price_per_million', event.target.value === '' ? undefined : Number(event.target.value))} /></label><label>Output · USD / 1M tokens<input type="number" min="0" step="any" placeholder="Not configured" value={provider.output_price_per_million ?? ''} onChange={event => field(kind, 'output_price_per_million', event.target.value === '' ? undefined : Number(event.target.value))} /></label><label>Maximum output tokens<input type="number" min="1" max="16384" value={provider.max_output_tokens ?? 4096} onChange={event => field(kind, 'max_output_tokens', Number(event.target.value))} /></label></>}
            <label>Timeout · seconds<input type="number" min="1" max="300" value={provider.timeout ?? 120} onChange={event => field(kind, 'timeout', Number(event.target.value))} /></label>
          </div></details>
          <div className="section-label section-space">BUDGET</div>
          <label>Total budget · USD<input type="number" min="0" step="any" required value={value.budget_usd ?? 0} onChange={event => setValue(previous => ({ ...previous, budget_usd: Number(event.target.value) }))} /></label>
          {settings.spent !== undefined && <p className="form-help">Spent ${settings.spent.toFixed(4)} · Reserved ${(settings.reserved || 0).toFixed(4)} · Available ${(settings.available || 0).toFixed(4)}</p>}
        </div>
        <footer><button type="button" className="button" onClick={onClose}>Cancel</button><button className="button primary" disabled={pending}>{pending ? 'Saving…' : 'Save settings'}</button></footer>
      </form>
    </section>
  </div>;
}
