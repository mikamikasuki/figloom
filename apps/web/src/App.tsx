import { useCallback, useEffect, useRef, useState } from 'react';
import { AlertCircle, ArrowUpRight, Check, ChevronDown, Code2, Database, Download, FileText, FolderOpen, GitBranch, History, LoaderCircle, MoreHorizontal, Network, PanelRightClose, PanelRightOpen, Play, Plus, RefreshCw, Send, Settings2, Sparkles, Table2, Trash2, Upload, Workflow, X, ChartLine } from 'lucide-react';
import { activeJob, api, ApiError, artifactUrl, assetExportUrl, projectExportUrl, columnNames, errorText, previewArtifacts, parseSpec, settingsPayload } from './api';
import type { Asset, AssetKind, Job, JsonObject, Project, ProjectDetail, RunAction, RunScope, Settings, Source } from './api';
import { CandidateEditor, HistoryEditor, JobList, MappingEditor, Preview, SourceInspector, WorkflowEditor } from './Editors';
import { SettingsModal } from './SettingsModal';

type Draft = { id: string; revision: number; title: string; caption: string; spec: string; source_ids: string[]; dirty: boolean };
type Tab = 'spec' | 'caption' | 'sources' | 'candidates' | 'workflow' | 'history' | 'jobs';
const emptyDraft: Draft = { id: '', revision: 0, title: '', caption: '', spec: '{}', source_ids: [], dirty: false };
const draftOf = (asset: Asset): Draft => ({ id: asset.id, revision: asset.revision, title: asset.title, caption: asset.caption || '', spec: JSON.stringify(asset.spec, null, 2), source_ids: asset.source_ids || [], dirty: false });
const KindIcon = ({ kind, size = 17 }: { kind: AssetKind; size?: number }) => kind === 'diagram' ? <Network size={size} /> : kind === 'plot' ? <ChartLine size={size} /> : <Table2 size={size} />;
const kinds: AssetKind[] = ['diagram', 'plot', 'table'];
const tabs: { id: Tab; label: string }[] = [{ id: 'spec', label: 'Specification' }, { id: 'caption', label: 'Caption' }, { id: 'sources', label: 'Sources' }, { id: 'candidates', label: 'Candidates' }, { id: 'workflow', label: 'Workflow' }, { id: 'history', label: 'History' }, { id: 'jobs', label: 'Jobs' }];
const date = (value: string) => new Date(value).toLocaleDateString(undefined, { month: 'short', day: 'numeric' });

export default function App() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [projectId, setProjectId] = useState(() => localStorage.getItem('figloom-project') || '');
  const [detail, setDetail] = useState<ProjectDetail>();
  const [loading, setLoading] = useState(true); const [projectLoading, setProjectLoading] = useState(false);
  const [selectedId, setSelectedId] = useState(''); const [sourceId, setSourceId] = useState('');
  const [sourceCache, setSourceCache] = useState<Record<string, Source>>({});
  const [library, setLibrary] = useState<'figures' | 'sources'>('figures');
  const [previewChoices, setPreviewChoices] = useState<Record<string, string | null>>({});
  const [tab, setTab] = useState<Tab>('spec'); const [format, setFormat] = useState('svg');
  const [draft, setDraft] = useState<Draft>(emptyDraft); const [rawEditor, setRawEditor] = useState(false);
  const [scope, setScope] = useState<RunScope>('single');
  const [pending, setPending] = useState('');
  const [notice, setNotice] = useState<{ type: 'error' | 'success'; text: string }>();
  const [settings, setSettings] = useState<Settings>({}); const [settingsOpen, setSettingsOpen] = useState(false);
  const [chatOpen, setChatOpen] = useState(true); const [chatScope, setChatScope] = useState<'asset' | 'project'>('asset'); const [message, setMessage] = useState('');
  const [modal, setModal] = useState<'project' | 'asset' | 'edit-project' | null>(null);
  const [newName, setNewName] = useState(''); const [description, setDescription] = useState(''); const [newKind, setNewKind] = useState<AssetKind>('diagram'); const [newSources, setNewSources] = useState<string[]>([]);
  const [deletion, setDeletion] = useState<{ kind: 'project' | 'asset' | 'source'; id: string; title: string }>();
  const uploadInput = useRef<HTMLInputElement>(null); const importInput = useRef<HTMLInputElement>(null);
  const replaceInput = useRef<HTMLInputElement>(null); const draftCache = useRef<Record<string, Draft>>({});
  const projectRef = useRef(projectId); projectRef.current = projectId;
  const selectedRef = useRef(selectedId); selectedRef.current = selectedId;
  const asset = detail?.assets.find(item => item.id === selectedId);
  const sources = (detail?.sources || []).map(source => sourceCache[source.id]?.revision === source.revision ? sourceCache[source.id] || source : source);
  const inspectSource = sources.find(source => source.id === sourceId);
  const busy = Boolean(pending);
  const modelAvailable = Boolean(settings.text?.model && settings.text?.base_url);
  const staleDraft = Boolean(asset && draft.id === asset.id && draft.revision !== asset.revision);
  const assetRunning = (detail?.jobs || []).some(job => job.asset_id === selectedId && activeJob(job));
  let parsed: JsonObject = {}; let specError = '';
  try { parsed = parseSpec(draft.spec); } catch (error) { specError = errorText(error); }

  const loadProjects = useCallback(async (signal?: AbortSignal) => {
    const value = await api.projects(signal); setProjects(value);
    setProjectId(current => value.some(project => project.id === current) ? current : value[0]?.id || '');
  }, []);
  const refresh = useCallback(async () => {
    const current = projectRef.current; if (!current) return;
    const [projectResult, settingsResult] = await Promise.allSettled([api.project(current), api.settings()]);
    if (settingsResult.status === 'fulfilled') setSettings(settingsResult.value);
    if (projectResult.status === 'rejected') throw projectResult.reason;
    if (projectRef.current === current) setDetail(projectResult.value);
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    loadProjects(controller.signal).catch(error => { if (!controller.signal.aborted) setNotice({ type: 'error', text: errorText(error) }); }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    api.settings().then(setSettings).catch(error => setNotice({ type: 'error', text: errorText(error) }));
    return () => controller.abort();
  }, [loadProjects]);
  useEffect(() => {
    setDetail(undefined); setSelectedId(''); setSourceId(''); setDraft(emptyDraft);
    localStorage.setItem('figloom-project', projectId);
    if (!projectId) return;
    const controller = new AbortController(); setProjectLoading(true);
    api.project(projectId, controller.signal).then(value => { if (!controller.signal.aborted) { setDetail(value); setSelectedId(value.assets[0]?.id || ''); } }).catch(error => { if (!controller.signal.aborted) setNotice({ type: 'error', text: errorText(error) }); }).finally(() => { if (!controller.signal.aborted) setProjectLoading(false); });
    return () => controller.abort();
  }, [projectId]);
  useEffect(() => {
    if (!asset) return;
    setDraft(previous => previous.id !== asset.id ? draftCache.current[asset.id]?.dirty ? draftCache.current[asset.id] : draftOf(asset) : !previous.dirty ? draftOf(asset) : previous);
  }, [asset]);
  useEffect(() => { if (draft.id) draftCache.current[draft.id] = draft; }, [draft]);
  useEffect(() => {
    if (!detail?.jobs.some(activeJob)) return;
    const timer = window.setInterval(() => { refresh().catch(error => setNotice({ type: 'error', text: errorText(error) })); }, 1800);
    return () => window.clearInterval(timer);
  }, [detail?.jobs, refresh]);
  const mappingSource = typeof parsed.source_id === 'string' ? parsed.source_id : '';
  useEffect(() => {
    const ids = [...new Set([sourceId, mappingSource].filter(Boolean))]; if (!ids.length) return;
    const controller = new AbortController();
    Promise.all(ids.map(id => api.source(id, controller.signal))).then(values => setSourceCache(previous => ({ ...previous, ...Object.fromEntries(values.map(value => [value.id, value])) }))).catch(error => { if (!controller.signal.aborted) setNotice({ type: 'error', text: errorText(error) }); });
    return () => controller.abort();
  }, [sourceId, mappingSource]);
  useEffect(() => { const escape = (event: KeyboardEvent) => { if (event.key === 'Escape' && !busy) { setModal(null); setSettingsOpen(false); setDeletion(undefined); } }; window.addEventListener('keydown', escape); return () => window.removeEventListener('keydown', escape); }, [busy]);

  async function perform<T>(label: string, action: () => Promise<T>, success?: string, refreshAfter = true): Promise<T | undefined> {
    setPending(label); setNotice(undefined);
    try { const value = await action(); if (refreshAfter) await refresh(); if (success) setNotice({ type: 'success', text: success }); return value; }
    catch (error) { setNotice({ type: 'error', text: error instanceof ApiError && error.status === 409 ? `${error.message} Your local edits are preserved. Reload the latest revision before saving.` : errorText(error) }); if (refreshAfter) await refresh().catch(() => undefined); return undefined; }
    finally { setPending(''); }
  }
  function chooseAsset(id: string) { setSelectedId(id); setSourceId(''); setLibrary('figures'); }
  function openCreate(type: 'project' | 'asset') { setNewName(''); setDescription(''); setNewSources([]); setNewKind('diagram'); setModal(type); }
  function editDraft(field: 'title' | 'caption' | 'spec', value: string) { setDraft(previous => ({ ...previous, [field]: value, dirty: true })); }
  function editSpec(field: string, value: unknown) {
    try { setDraft(previous => ({ ...previous, spec: JSON.stringify({ ...parseSpec(previous.spec), [field]: value }, null, 2), source_ids: field === 'source_id' && typeof value === 'string' ? [...new Set([...previous.source_ids, value])] : previous.source_ids, dirty: true })); }
    catch (error) { setNotice({ type: 'error', text: errorText(error) }); }
  }
  async function saveDraft(): Promise<Asset | undefined> {
    if (!asset) return; const value = parseSpec(draft.spec);
    const saved = await api.updateAsset(asset.id, { expected_revision: draft.revision, title: draft.title, caption: draft.caption, spec: value, source_ids: draft.source_ids });
    if (selectedRef.current === saved.id) setDraft(draftOf(saved)); return saved;
  }
  async function run(action: RunAction) {
    if (!asset) return;
    await perform(action, async () => { const current = draft.dirty ? await saveDraft() : asset; if (!current) throw new Error('Save the figure before rendering.'); return api.run(current.id, scope, action); }, `${action === 'render' ? 'Render' : action === 'generate' ? 'Generation' : 'Review'} queued. See Jobs for status.`);
    setTab('jobs');
  }
  async function upload(files: FileList | null) {
    if (!files?.length || !projectId) return;
    await perform('upload', async () => {
      const errors: string[] = []; let completed = 0;
      for (const file of Array.from(files)) { try { await api.upload(projectId, file); completed++; } catch (error) { errors.push(`${file.name}: ${errorText(error)}`); } }
      if (errors.length) throw new Error(`${completed} file(s) uploaded. ${errors.join(' ')}`);
      return completed;
    }, 'Sources uploaded.'); setLibrary('sources');
    if (uploadInput.current) uploadInput.current.value = '';
  }
  async function submitCreate() {
    if (!newName.trim()) return;
    if (modal === 'project') {
      const created = await perform('create-project', () => api.createProject(newName.trim(), description));
      if (created) { await loadProjects(); setProjectId(created.id); setModal(null); }
    } else if (modal === 'edit-project' && detail) {
      const saved = await perform('edit-project', () => api.updateProject(projectId, { name: newName.trim(), description, expected_revision: detail.project.revision }));
      if (saved) { await loadProjects(); setModal(null); }
    } else if (modal === 'asset') {
      const created = await perform('create-asset', () => api.createAsset(projectId, { kind: newKind, title: newName.trim(), source_ids: newSources }));
      if (created) { chooseAsset(created.id); setTab('spec'); setModal(null); }
    }
  }
  async function deleteConfirmed() {
    if (!deletion) return;
    const value = await perform('delete', async () => { if (deletion.kind === 'project') await api.deleteProject(deletion.id); else if (deletion.kind === 'asset') await api.deleteAsset(deletion.id); else await api.deleteSource(deletion.id); return true; }, undefined, deletion.kind !== 'project');
    if (value) { if (deletion.kind === 'project') { setProjectId(''); await loadProjects(); } if (deletion.kind === 'asset' && selectedId === deletion.id) setSelectedId(''); if (deletion.kind === 'source' && sourceId === deletion.id) setSourceId(''); setDeletion(undefined); }
  }
  async function sendMessage() {
    if (!message.trim() || !projectId) return;
    const result = await perform('chat', () => api.chat(projectId, { message: message.trim(), ...(chatScope === 'asset' && asset ? { asset_id: asset.id, expected_revision: asset.revision } : {}) }));
    if (result) setMessage('');
  }
  const effectiveChatScope = asset ? chatScope : 'project';
  const filteredMessages = (detail?.messages || []).filter(item => effectiveChatScope === 'project' ? !item.asset_id : item.asset_id === selectedId).sort((a, b) => a.created_at.localeCompare(b.created_at));
  const proposals = (detail?.proposals || []).filter(proposal => effectiveChatScope === 'project' || proposal.asset_id === selectedId);
  const scopedJobs = (detail?.jobs || []).filter(job => job.action === 'chat' && (effectiveChatScope === 'project' ? !job.asset_id : job.asset_id === selectedId));
  const activeCount = detail?.jobs.filter(activeJob).length || 0;
  const previewChoice = (figure: Asset): string | null => {
    const choice = previewChoices[figure.id];
    if (choice === null || (choice && figure.candidates.some(item => item.id === choice))) return choice;
    return figure.candidates.find(item => item.selected)?.id || (figure.artifacts.length ? null : figure.candidates[0]?.id || null);
  };
  const previewId = asset ? previewChoice(asset) : null;
  const choosePreview = (id: string | null) => { if (asset) setPreviewChoices(previous => ({ ...previous, [asset.id]: id })); };
  const candidateApplyDisabled = busy || draft.dirty || staleDraft || assetRunning;
  const applyCandidate = (id: string) => { if (!asset) return; return perform('select', async () => { const selected = await api.select(asset.id, id, asset.revision); if (selectedRef.current === selected.id) setDraft(draftOf(selected)); return selected; }, 'Candidate applied.'); };
  const primaryPreview = (figure: Asset) => { const candidate = figure.candidates.find(item => item.id === previewChoice(figure)); const files = previewArtifacts(figure, candidate); return files.find(item => item.name === candidate?.preview_name) || files.find(item => item.name === candidate?.outputs?.png) || files.find(item => item.format === 'png') || files.find(item => item.format === 'svg'); };

  return <div className={`app-shell ${chatOpen ? '' : 'chat-hidden'}`}>
    <aside className="sidebar">
      <div className="brand"><div className="brand-mark"><span /><span /><span /></div><span>Fig<strong>loom</strong></span></div>
      <div className="project-control"><label htmlFor="project-select" className="section-label">WORKSPACE</label><div><select id="project-select" value={projectId} onChange={event => setProjectId(event.target.value)} aria-label="Current project"><option value="" disabled>Choose a project</option>{projects.map(project => <option key={project.id} value={project.id}>{project.name}</option>)}</select><button className="icon-button" aria-label="New project" onClick={() => openCreate('project')}><Plus size={17} /></button></div></div>
      <nav className="library-tabs" aria-label="Project library"><button className={library === 'figures' ? 'active' : ''} onClick={() => setLibrary('figures')}><Network size={15} />Figures <span>{detail?.assets.length || 0}</span></button><button className={library === 'sources' ? 'active' : ''} onClick={() => setLibrary('sources')}><Database size={15} />Sources <span>{sources.length}</span></button></nav>
      <div className="library-heading"><span>{library === 'figures' ? 'PROJECT FIGURES' : 'SOURCE LIBRARY'}</span><button className="icon-button" disabled={!projectId || busy} onClick={() => library === 'figures' ? openCreate('asset') : uploadInput.current?.click()} aria-label={library === 'figures' ? 'New figure' : 'Upload source'}><Plus size={15} /></button></div>
      <div className="library-list">{projectLoading ? <div className="sidebar-empty"><LoaderCircle size={19} className="spin" /><p>Opening project…</p></div> : library === 'figures' ? detail?.assets.map(figure => { const preview = primaryPreview(figure); return <button className={`asset-item ${figure.id === selectedId && !sourceId ? 'selected' : ''}`} key={figure.id} onClick={() => chooseAsset(figure.id)}><div className="asset-thumbnail">{preview ? <img src={artifactUrl(figure, preview)} alt="" /> : <KindIcon kind={figure.kind} size={21} />}</div><div><strong>{figure.title}</strong><span><span className={`status-dot ${figure.status}`} />{figure.kind} · r{figure.revision}</span></div></button>; }) : sources.map(source => <button className={`source-item ${source.id === sourceId ? 'selected' : ''}`} key={source.id} onClick={() => { setSourceId(source.id); setTab('sources'); }}><FileText size={18} /><div><strong>{source.name}</strong><span>{source.kind} · {(source.size / 1024).toFixed(1)} KB</span></div></button>)}{detail && !(library === 'figures' ? detail.assets.length : sources.length) && <div className="sidebar-empty"><p>{library === 'figures' ? 'No figures in this project.' : 'No sources uploaded.'}</p><button className="button sidebar-button" onClick={() => library === 'figures' ? openCreate('asset') : uploadInput.current?.click()}>{library === 'figures' ? 'Create a figure' : 'Upload sources'}</button></div>}</div>
      <button className="upload-button" disabled={!projectId || busy} onClick={() => uploadInput.current?.click()}><Upload size={16} />{pending === 'upload' ? 'Uploading…' : 'Upload source files'}<span>PDF, CSV, text & images</span></button>
      <input type="file" multiple ref={uploadInput} className="visually-hidden" accept=".pdf,.md,.txt,.tex,.csv,.tsv,.json,.png,.jpg,.jpeg,.svg" onChange={event => upload(event.target.files)} />
      <input type="file" ref={replaceInput} className="visually-hidden" accept=".pdf,.md,.txt,.tex,.csv,.tsv,.json,.png,.jpg,.jpeg,.svg" onChange={async event => { const file = event.target.files?.[0]; if (!file || !inspectSource) return; const saved = await perform('replace-source', () => api.replaceSource(inspectSource.id, file, inspectSource.revision || 1), 'Source replaced. Bound figures are now stale.'); if (saved) setSourceCache(previous => ({ ...previous, [saved.id]: saved })); event.target.value = ''; }} />
      <input type="file" ref={importInput} className="visually-hidden" accept=".zip" onChange={async event => { const file = event.target.files?.[0]; if (!file) return; const imported = await perform('import', () => api.importProject(file)); if (imported) { await loadProjects(); setProjectId(imported.id); } event.target.value = ''; }} />
      <div className="sidebar-footer"><button onClick={() => setSettingsOpen(true)}><Settings2 size={16} />Settings</button><button onClick={() => importInput.current?.click()} disabled={busy}><FolderOpen size={16} />Import project backup</button><div className="local-label"><span />Local workspace <span className="budget">{settings.available !== undefined ? `$${settings.available.toFixed(2)} available` : ''}</span></div></div>
    </aside>

    <main className="workspace">
      <header className="workspace-header"><div className="breadcrumb">{detail ? <button aria-label="Show figure gallery" onClick={() => { setSelectedId(''); setSourceId(''); }}>{detail.project.name}</button> : <span>Workspace</span>}{asset && <><span>/</span><span>{asset.kind}</span></>}</div><div className="header-actions"><button className="icon-button" onClick={() => perform('refresh', async () => { await loadProjects(); await refresh(); return true; })} aria-label="Refresh workspace" disabled={busy}><RefreshCw size={16} className={pending === 'refresh' ? 'spin' : ''} /></button>{detail && <details className="menu"><summary aria-label="Project actions"><MoreHorizontal size={18} /></summary><div className="menu-items"><button onClick={() => { setNewName(detail.project.name); setDescription(detail.project.description || ''); setModal('edit-project'); }}>Rename project</button><button disabled={busy} onClick={async () => { const fork = await perform('fork-project', () => api.forkProject(projectId)); if (fork) { await loadProjects(); setProjectId(fork.id); } }}>Fork project</button><a href={projectExportUrl(projectId)} download>Export project backup</a><button className="danger-text" onClick={() => setDeletion({ kind: 'project', id: projectId, title: detail.project.name })}>Delete project</button></div></details>}<button className="icon-button" onClick={() => setChatOpen(!chatOpen)} aria-label={chatOpen ? 'Hide chat' : 'Show chat'}>{chatOpen ? <PanelRightClose size={18} /> : <PanelRightOpen size={18} />}</button></div></header>
      {notice && <div className={`notice ${notice.type}`} role={notice.type === 'error' ? 'alert' : 'status'}>{notice.type === 'error' ? <AlertCircle size={16} /> : <Check size={16} />}<span>{notice.text}</span><button className="icon-button" aria-label="Dismiss notification" onClick={() => setNotice(undefined)}><X size={15} /></button></div>}
      {loading || projectLoading ? <div className="workspace-empty"><LoaderCircle className="spin" size={28} /><h2>Loading workspace…</h2></div> : !detail ? <div className="workspace-empty"><div className="welcome-mark"><Network size={32} /></div><h1>No project</h1><button className="button primary" onClick={() => openCreate('project')}><Plus size={16} />Create a project</button><button className="text-link" onClick={() => importInput.current?.click()}>Import project backup</button></div> : inspectSource ? <div className="source-view"><SourceInspector source={inspectSource} /><div className="source-actions"><button className="button" onClick={() => setSourceId('')}>Back to figures</button><button className="button" disabled={busy} onClick={() => replaceInput.current?.click()}><Upload size={14} />Replace source</button><button className="button danger-text" onClick={() => setDeletion({ kind: 'source', id: inspectSource.id, title: inspectSource.name })}><Trash2 size={14} />Delete source</button></div></div> : asset ? <>
        <div className="asset-heading"><div><div className="eyebrow"><KindIcon kind={asset.kind} size={14} />{asset.kind} <span>·</span> REVISION {asset.revision}</div><h1>{asset.title}</h1></div><div className="asset-heading-actions"><span className={`badge ${asset.status}`}>{assetRunning ? <LoaderCircle size={12} className="spin" /> : <span className={`status-dot ${asset.status}`} />}{asset.status}</span><a className="button small" href={assetExportUrl(asset.id)} download><Download size={14} />Export</a><details className="menu"><summary aria-label="Figure actions"><MoreHorizontal size={18} /></summary><div className="menu-items"><button disabled={busy} onClick={async () => { const fork = await perform('fork', () => api.fork(asset.id)); if (fork) chooseAsset(fork.id); }}>Fork figure</button><button className="danger-text" onClick={() => setDeletion({ kind: 'asset', id: asset.id, title: asset.title })}>Delete figure</button></div></details></div></div>
        <div className="render-bar"><div className="scope-control"><Workflow size={15} /><select aria-label="Run scope" value={scope} onChange={event => setScope(event.target.value as RunScope)}><option value="single">This figure only</option><option value="ancestors">Include input figures</option><option value="affected">All affected figures</option></select></div><div className="render-actions"><button className="button small" disabled={busy || assetRunning || specError !== '' || staleDraft} onClick={() => run('review')}>Review</button><button className="button small" disabled={busy || assetRunning || !modelAvailable || specError !== '' || staleDraft} onClick={() => run('generate')} title={!modelAvailable ? 'Configure a text model in Settings.' : 'Generate candidates'}><Sparkles size={14} />Generate</button><button className="button primary small" disabled={busy || assetRunning || specError !== '' || staleDraft} onClick={() => run('render')}><Play size={14} />{draft.dirty ? 'Save & render' : 'Render'}</button></div></div>
        <Preview asset={asset} candidateId={previewId} onPreview={choosePreview} onApply={applyCandidate} applyDisabled={candidateApplyDisabled} format={format} setFormat={setFormat} />
        <section className="editor-panel"><div className="editor-tabs" role="tablist" aria-label="Figure editor">{tabs.map(item => <button key={item.id} role="tab" aria-selected={tab === item.id} className={tab === item.id ? 'active' : ''} onClick={() => setTab(item.id)}>{item.label}{item.id === 'jobs' && activeCount > 0 && <span className="count-badge">{activeCount}</span>}</button>)}<div className="editor-save"><span>{draft.dirty ? 'Unsaved changes' : `Saved · r${draft.revision}`}</span><button className="button small" disabled={busy || !draft.dirty || !!specError || staleDraft || !draft.title.trim()} onClick={() => perform('save', saveDraft, 'Figure revision saved.')}>{pending === 'save' ? 'Saving…' : 'Save'}</button></div></div>
          {staleDraft && <div className="stale-warning"><AlertCircle size={15} /><span>The saved figure is now revision {asset.revision}. Your local edits are based on revision {draft.revision}.</span><button className="button small" onClick={() => setDraft(draftOf(asset))}>Reload latest</button></div>}
          <div className="editor-content" role="tabpanel">
            {tab === 'spec' && <><div className="spec-header"><label>Figure title<input value={draft.title} onChange={event => editDraft('title', event.target.value)} /></label><button className={`button small ${rawEditor ? 'selected' : ''}`} onClick={() => setRawEditor(!rawEditor)}><Code2 size={14} />{rawEditor ? 'Form editor' : 'JSON editor'}</button></div>{specError && <div className="inline-error">{specError}</div>}{rawEditor ? <label className="code-label">Full editable specification<textarea className="code-editor" value={draft.spec} spellCheck={false} onChange={event => editDraft('spec', event.target.value)} /></label> : asset.kind === 'diagram' ? <div className="diagram-editor"><label>Mechanism brief<textarea value={String(parsed.brief || '')} onChange={event => editSpec('brief', event.target.value)} placeholder="Inputs, operations and outputs" rows={4} /></label><div className="form-grid three"><label>Print width<select value={String(parsed.width || 'wide')} onChange={event => editSpec('width', event.target.value)}><option value="column">Single column</option><option value="wide">Page width</option></select></label><label>Height · inches<input type="number" min="1" step="0.1" value={Number(parsed.height ?? 4.4)} onChange={event => editSpec('height', Number(event.target.value))} /></label><label>Minimum type · pt<input type="number" min="8" step="0.5" value={Number(parsed.font_size ?? 9)} onChange={event => editSpec('font_size', Number(event.target.value))} /></label></div><p className="form-help">Render requires scene JSON. Generate uses the brief and attached sources.</p><button className="text-link" onClick={() => setRawEditor(true)}>Edit scene, graph and composition JSON <ArrowUpRight size={12} /></button></div> : <MappingEditor asset={asset} spec={parsed} sources={sources} update={editSpec} />}</>}
            {tab === 'caption' && <div className="caption-editor"><div className="editor-intro"><h3>Caption</h3></div><textarea value={draft.caption} onChange={event => editDraft('caption', event.target.value)} aria-label="Figure caption" placeholder="Caption" rows={7} /><div className="caption-count">{draft.caption.trim() ? draft.caption.trim().split(/\s+/).length : 0} words</div></div>}
            {tab === 'sources' && <><div className="editor-intro"><h3>Attached sources</h3></div><div className="attached-sources">{sources.map(source => <label key={source.id}><input type="checkbox" checked={draft.source_ids.includes(source.id)} onChange={event => setDraft(previous => ({ ...previous, dirty: true, source_ids: event.target.checked ? [...previous.source_ids, source.id] : previous.source_ids.filter(id => id !== source.id) }))} /><FileText size={16} /><div><strong>{source.name}</strong><span>{source.kind} · {columnNames(source).length ? `${columnNames(source).length} columns` : `${(source.size / 1024).toFixed(1)} KB`}</span></div><button type="button" className="text-link" onClick={() => { setSourceId(source.id); setLibrary('sources'); }}>Inspect</button></label>)}{!sources.length && <p className="muted">No sources uploaded.</p>}</div><button className="button" onClick={() => uploadInput.current?.click()} disabled={busy}><Upload size={14} />Upload sources</button></>}
            {tab === 'candidates' && <CandidateEditor asset={asset} candidateId={previewId} disabled={candidateApplyDisabled} onPreview={choosePreview} onApply={applyCandidate} />}
            {tab === 'workflow' && <WorkflowEditor assets={detail.assets} edges={detail.edges} selectedId={selectedId} disabled={busy} onSelect={chooseAsset} onAdd={(source, target) => perform('dependency', () => api.edge(projectId, source, target), 'Dependency added.')} onRemove={id => perform('dependency', () => api.deleteEdge(projectId, id), 'Dependency removed.')} />}
            {tab === 'history' && <HistoryEditor asset={asset} disabled={busy || draft.dirty} onRestore={revision => perform('restore', async () => { const restored = await api.restore(asset.id, revision, asset.revision); if (selectedRef.current === restored.id) setDraft(draftOf(restored)); return restored; }, 'Saved revision restored.')} onFork={async () => { const fork = await perform('fork', () => api.fork(asset.id)); if (fork) chooseAsset(fork.id); }} />}
            {tab === 'jobs' && <JobList jobs={detail.jobs} disabled={busy} onCancel={id => perform('cancel', () => api.cancel(id), 'Cancellation requested.')} onInspect={id => perform('inspect-job', async () => { const job = await api.job(id); setDetail(previous => previous ? { ...previous, jobs: previous.jobs.map(item => item.id === id ? job : item) } : previous); return job; })} />}
          </div>
        </section>
      </> : <div className="project-gallery"><div className="gallery-heading"><div><p className="eyebrow">PROJECT WORKSPACE</p><h1>{detail.project.name}</h1>{detail.project.description && <p>{detail.project.description}</p>}</div><button className="button primary" onClick={() => openCreate('asset')}><Plus size={16} />New figure</button></div>{detail.assets.length ? <div className="gallery-grid">{detail.assets.map(figure => { const preview = primaryPreview(figure); return <button className="gallery-card" key={figure.id} onClick={() => chooseAsset(figure.id)}><div>{preview ? <img alt={figure.title} src={artifactUrl(figure, preview)} /> : <KindIcon kind={figure.kind} size={40} />}</div><h3>{figure.title}</h3><p>{figure.kind} · revision {figure.revision}<span className={`badge ${figure.status}`}>{figure.status}</span></p></button>; })}</div> : <div className="gallery-empty"><div className="welcome-mark"><Network size={28} /></div><h2>No figures</h2><div className="kind-cards">{kinds.map(kind => <button key={kind} onClick={() => { openCreate('asset'); setNewKind(kind); }}><KindIcon kind={kind} size={24} /><strong>{kind[0].toUpperCase() + kind.slice(1)}</strong><Plus size={15} /></button>)}</div></div>}</div>}
    </main>

    {chatOpen && <aside className="assistant"><header><div><Sparkles size={16} /><strong>Chat</strong></div><span className={`connection-dot ${modelAvailable ? 'configured' : ''}`} title={modelAvailable ? 'Provider configured' : 'No text model configured'} /></header><div className="chat-scope"><span>SCOPE</span><select aria-label="Chat scope" value={asset ? chatScope : 'project'} onChange={event => setChatScope(event.target.value as 'asset' | 'project')}><option value="project">Project</option>{asset && <option value="asset">Figure</option>}</select></div><div className="chat-thread">{!filteredMessages.length && <div className="chat-welcome"><p>No messages</p></div>}{filteredMessages.map(item => <article className={`chat-message ${item.role}`} key={item.id}><div className="message-role">{item.role === 'user' ? 'You' : 'Assistant'}<span>{new Date(item.created_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</span></div><div className="message-content">{item.content}</div></article>)}{scopedJobs.filter(activeJob).map(job => <div className="assistant-working" key={job.id}><LoaderCircle className="spin" size={14} /><span>{job.stage || job.status}</span><button className="text-link" onClick={() => perform('cancel', () => api.cancel(job.id))}>Cancel</button></div>)}{scopedJobs.filter(job => job.status === 'failed').slice(-1).map(job => <div className="inline-error" key={job.id}>{job.error || 'Chat request failed. See Jobs.'}</div>)}{proposals.map(proposal => { const current = detail?.assets.find(item => item.id === proposal.asset_id); const stale = !current || current.revision !== proposal.base_revision; const applied = proposal.status === 'applied'; return <article className="proposal" key={proposal.id}><div className="proposal-title"><GitBranch size={15} /><strong>Proposed revision</strong><span>r{proposal.base_revision}</span></div><p>{proposal.summary}</p><details><summary>Inspect changes <Code2 size={12} /></summary>{Object.entries(proposal.changes).map(([field, value]) => <div className="proposal-diff" key={field}><div>{field}</div><label>Current<pre>{JSON.stringify((current as unknown as JsonObject)?.[field], null, 2) ?? '—'}</pre></label><label>Proposed<pre>{typeof value === 'string' ? value : JSON.stringify(value, null, 2)}</pre></label></div>)}</details>{stale && !applied && <p className="proposal-warning">Stale proposal. The figure has a newer revision.</p>}{draft.dirty && current?.id === draft.id && !applied && <p className="proposal-warning">Save or reload your local edits before applying.</p>}<div className="proposal-actions"><button className="button small" disabled={busy || stale || applied || (draft.dirty && current?.id === draft.id)} onClick={() => perform('apply', async () => { const saved = await api.apply(proposal.id, current!.revision); if (selectedRef.current === saved.id) setDraft(draftOf(saved)); return saved; }, 'Proposal applied as a new revision.')}>{applied ? <><Check size={12} />Applied</> : 'Apply'}</button><button className="button primary small" disabled={busy || stale || applied || (draft.dirty && current?.id === draft.id)} onClick={() => perform('apply-render', async () => { const saved = await api.apply(proposal.id, current!.revision); if (saved.id === selectedRef.current) setDraft(draftOf(saved)); return api.run(saved.id, 'single', 'render'); }, 'Proposal applied. Render queued.')}><Play size={12} />Apply & render</button></div></article>; })}</div><div className="chat-composer">{!modelAvailable && <div className="ai-unavailable"><p>Text model not configured.</p><button className="text-link" onClick={() => setSettingsOpen(true)}>Settings <ArrowUpRight size={12} /></button></div>}<form onSubmit={event => { event.preventDefault(); sendMessage(); }}><textarea aria-label="Chat message" value={message} onChange={event => setMessage(event.target.value)} disabled={!projectId || !modelAvailable} placeholder={modelAvailable ? 'Message' : 'Configure a text model in Settings.'} rows={3} onKeyDown={event => { if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); if (!busy && message.trim() && modelAvailable) sendMessage(); } }} /><div><span>{chatScope === 'asset' && asset ? 'Figure' : 'Project'}</span><button className="send-button" disabled={busy || !message.trim() || !modelAvailable || !projectId} aria-label="Send message">{pending === 'chat' ? <LoaderCircle size={16} className="spin" /> : <Send size={16} />}</button></div></form></div></aside>}

    {settingsOpen && <SettingsModal settings={settings} pending={busy} error={notice?.type === 'error' ? notice.text : undefined} onClose={() => setSettingsOpen(false)} onSave={async value => { const safe = settingsPayload(value); const saved = await perform('settings', () => api.saveSettings(safe), 'Settings saved.'); if (saved) setSettings(saved); return Boolean(saved); }} />}
    {modal && <div className="modal-backdrop"><section className="modal create-modal" role="dialog" aria-modal="true" aria-labelledby="create-title"><header><div><h2 id="create-title">{modal === 'asset' ? 'Create a figure' : modal === 'edit-project' ? 'Project details' : 'New project'}</h2></div><button className="icon-button" onClick={() => setModal(null)} aria-label="Close dialog"><X size={17} /></button></header><form onSubmit={event => { event.preventDefault(); submitCreate(); }}><div className="modal-scroll">{notice?.type === 'error' && <p className="inline-error" role="alert">{notice.text}</p>}{modal === 'asset' && <div className="type-select">{kinds.map(kind => <button type="button" className={newKind === kind ? 'active' : ''} key={kind} onClick={() => setNewKind(kind)}><KindIcon kind={kind} size={20} />{kind}</button>)}</div>}<label>{modal === 'asset' ? 'Figure title' : 'Project name'}<input autoFocus required value={newName} onChange={event => setNewName(event.target.value)} placeholder={modal === 'asset' ? 'Figure title' : 'Project name'} /></label>{modal !== 'asset' ? <label>Description<textarea value={description} onChange={event => setDescription(event.target.value)} rows={3} placeholder="Description" /></label> : <fieldset className="source-checks"><legend>Attach original sources</legend>{sources.map(source => <label key={source.id}><input type="checkbox" checked={newSources.includes(source.id)} onChange={event => setNewSources(previous => event.target.checked ? [...previous, source.id] : previous.filter(id => id !== source.id))} /><FileText size={14} />{source.name}</label>)}{!sources.length && <p className="muted">You can upload and attach sources after creating the figure.</p>}</fieldset>}</div><footer><button type="button" className="button" onClick={() => setModal(null)}>Cancel</button><button className="button primary" disabled={busy || !newName.trim()}>{busy ? 'Saving…' : modal === 'edit-project' ? 'Save project' : modal === 'asset' ? 'Create figure' : 'Create project'}</button></footer></form></section></div>}
    {deletion && <div className="modal-backdrop"><section className="modal confirm-modal" role="dialog" aria-modal="true" aria-labelledby="delete-title"><header><h2 id="delete-title">Delete {deletion.kind}?</h2><button className="icon-button" onClick={() => setDeletion(undefined)} aria-label="Close deletion"><X size={17} /></button></header><div className="modal-scroll"><p><strong>{deletion.title}</strong> will be removed.{deletion.kind === 'source' ? ' Figures that use it will become stale.' : deletion.kind === 'project' ? ' Its sources, figures and saved revisions will be deleted. Export a backup first if needed.' : ' Its saved revisions and dependency links will also be removed.'}</p>{notice?.type === 'error' && <p className="inline-error">{notice.text}</p>}</div><footer><button className="button" onClick={() => setDeletion(undefined)}>Keep it</button><button className="button danger" disabled={busy} onClick={deleteConfirmed}>Delete {deletion.kind}</button></footer></section></div>}
  </div>;
}
