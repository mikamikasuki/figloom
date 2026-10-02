import { useEffect, useState } from 'react';
import { Check, ChevronLeft, ChevronRight, ExternalLink, GitBranch, Link2, LoaderCircle, Maximize2, Play, RefreshCw, Trash2, X } from 'lucide-react';
import { api, artifactUrl, candidateUrl, sourceUrl, previewArtifacts, columnNames, errorText, workflowLayout } from './api';
import type { Asset, Candidate, Edge, Job, JsonObject, Revision, Source } from './api';

export function DataPreview({ source }: { source: Source }) {
  const preview = source.preview;
  const rows = Array.isArray(preview) ? preview : (preview && typeof preview === 'object' && 'rows' in preview ? (preview as { rows: unknown[] }).rows : []);
  const columns = columnNames(source).length ? columnNames(source) : rows[0] && typeof rows[0] === 'object' && !Array.isArray(rows[0]) ? Object.keys(rows[0]) : [];
  if (!rows.length) return <p className="muted">No tabular preview is available for this source.</p>;
  return <div className="data-scroll"><table className="data-table"><thead><tr>{columns.map(column => <th key={column}>{column}</th>)}</tr></thead><tbody>{rows.map((row, index) => <tr key={index}>{columns.map((column, cell) => <td key={column}>{String((Array.isArray(row) ? row[cell] : (row as JsonObject)?.[column]) ?? '')}</td>)}</tr>)}</tbody></table></div>;
}

export function MappingEditor({ asset, spec, sources, update }: { asset: Asset; spec: JsonObject; sources: Source[]; update: (field: string, value: unknown) => void }) {
  const selected = sources.find(source => source.id === spec.source_id);
  const columns = columnNames(selected);
  const groupValues = selected?.unique_values?.[String(spec.group)] || (Array.isArray(selected?.preview) ? [...new Set(selected.preview.map(row => (row as JsonObject)[String(spec.group)]).filter(value => value !== undefined).map(String))] : []);
  const select = (field: string, label: string, values: string[], empty = 'None') => <label key={field}>{label}<select value={String(spec[field] || '')} onChange={event => update(field, event.target.value || null)}><option value="">{empty}</option>{values.map(value => <option value={value} key={value}>{value}</option>)}</select></label>;
  return <div className="mapping-editor">
    <div className="editor-intro"><h3>Data mapping</h3></div>
    <div className="form-grid three">
      <label className="full">Data source<select value={String(spec.source_id || '')} onChange={event => update('source_id', event.target.value || null)}><option value="">Choose an uploaded data source</option>{sources.filter(source => columnNames(source).length || source.kind === 'data').map(source => <option key={source.id} value={source.id}>{source.name}</option>)}</select></label>
      {select('x', 'X / category', columns, 'Choose a column')}
      {select('y', 'Y / value', columns, 'Choose a column')}
      {select('group', 'Series / group', columns)}
      {select('facet', 'Facet', columns)}
      {select('chart_type', 'Chart', ['line', 'bar', 'scatter', 'heatmap', 'forest'])}
      {select('aggregation', 'Aggregation', ['mean', 'none'])}
      {select('interval', 'Uncertainty', ['none', 'sd', 'se', 'ci95'])}
      {select('unit_id', 'Independent unit', columns)}
      {select('width', 'Print width', ['column', 'wide'])}
      <label>X axis label<input value={String(spec.xlabel || '')} onChange={event => update('xlabel', event.target.value)} /></label>
      <label>Y axis label<input value={String(spec.ylabel || '')} onChange={event => update('ylabel', event.target.value)} /></label>
      <label>Decimal places<input type="number" min="0" max="12" value={Number(spec.precision ?? 3)} onChange={event => update('precision', Number(event.target.value))} /></label>
      <label>Unit<input value={String(spec.unit || '')} onChange={event => update('unit', event.target.value)} /></label>
      {select('direction', 'Metric direction', ['none', 'higher', 'lower'])}
      <label>Palette<input value={Array.isArray(spec.palette) ? spec.palette.join(', ') : ''} onChange={event => update('palette', event.target.value.split(',').map(value => value.trim()).filter(Boolean))} placeholder="#0072B2, #D55E00" /></label>
      <label>Baseline group · optional<input list="observed-group-values" value={String(spec.baseline || '')} onChange={event => update('baseline', event.target.value || null)} placeholder="Group value" /></label><label>Candidate group · optional<input list="observed-group-values" value={String(spec.candidate || '')} onChange={event => update('candidate', event.target.value || null)} placeholder="Group value" /></label><datalist id="observed-group-values">{groupValues.map(value => <option key={String(value)} value={String(value)} />)}</datalist>
    </div>
    {asset.kind === 'table' && <fieldset className="source-checks"><legend>Table columns</legend>{columns.map(column => <label key={column}><input type="checkbox" checked={Array.isArray(spec.columns) && spec.columns.includes(column)} onChange={event => update('columns', event.target.checked ? [...(Array.isArray(spec.columns) ? spec.columns : []), column] : (Array.isArray(spec.columns) ? spec.columns : []).filter(value => value !== column))} />{column}</label>)}</fieldset>}
    {selected && <details className="details"><summary>Inspect source rows · {selected.name}</summary><DataPreview source={selected} /></details>}
    {!columns.length && <p className="form-help">Select a data source to choose columns.</p>}
  </div>;
}

export function WorkflowEditor({ assets, edges, selectedId, disabled, onSelect, onAdd, onRemove }: {
  assets: Asset[]; edges: Edge[]; selectedId?: string; disabled: boolean;
  onSelect: (id: string) => void; onAdd: (source: string, target: string) => void; onRemove: (id: string) => void;
}) {
  const [from, setFrom] = useState(''); const [to, setTo] = useState('');
  const nodes = workflowLayout(assets, edges);
  const width = Math.max(500, ...nodes.map(node => node.x + 220));
  const height = Math.max(180, ...nodes.map(node => node.y + 95));
  return <div className="workflow-editor">
    <div className="editor-intro"><h3>Figure dependencies</h3><p>Changes mark downstream figures stale. Render one figure, its inputs, or everything affected.</p></div>
    <div className="workflow-scroll"><div className="workflow-canvas" style={{ width, height }}>
      <svg width={width} height={height} aria-label="Asset dependency graph"><defs><marker id="dependency-arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0,0 L8,4 L0,8" fill="#9ba5b5" /></marker></defs>{edges.map(edge => { const source = nodes.find(node => node.asset.id === edge.source); const target = nodes.find(node => node.asset.id === edge.target); if (!source || !target) return null; return <path key={edge.id} d={`M ${source.x + 190},${source.y + 36} C ${source.x + 220},${source.y + 36} ${target.x - 30},${target.y + 36} ${target.x},${target.y + 36}`} fill="none" stroke="#9ba5b5" strokeWidth="1.6" markerEnd="url(#dependency-arrow)" />; })}</svg>
      {nodes.map(({ asset, x, y }) => <button key={asset.id} className={`workflow-node ${asset.id === selectedId ? 'selected' : ''}`} style={{ left: x, top: y }} onClick={() => onSelect(asset.id)}><span className="kind-label">{asset.kind} · r{asset.revision}</span><strong>{asset.title}</strong><span className={`status-text ${asset.status}`}>{asset.status}</span></button>)}
    </div></div>
    <div className="dependency-form"><select aria-label="Dependency input" value={from} onChange={event => setFrom(event.target.value)}><option value="">Input figure</option>{assets.map(asset => <option key={asset.id} value={asset.id}>{asset.title}</option>)}</select><Link2 size={16} /><select aria-label="Dependency output" value={to} onChange={event => setTo(event.target.value)}><option value="">Dependent figure</option>{assets.filter(asset => asset.id !== from).map(asset => <option key={asset.id} value={asset.id}>{asset.title}</option>)}</select><button className="button" disabled={disabled || !from || !to || from === to || edges.some(edge => edge.source === from && edge.target === to)} onClick={() => onAdd(from, to)}>Add dependency</button></div>
    <div className="edge-list">{edges.map(edge => <div key={edge.id}><span>{assets.find(asset => asset.id === edge.source)?.title} <span className="muted">→</span> {assets.find(asset => asset.id === edge.target)?.title}</span><button className="icon-button" aria-label="Remove dependency" disabled={disabled} onClick={() => onRemove(edge.id)}><Trash2 size={14} /></button></div>)}</div>
  </div>;
}

export function HistoryEditor({ asset, disabled, onRestore, onFork }: { asset: Asset; disabled: boolean; onRestore: (revision: number) => void; onFork: () => void }) {
  const [history, setHistory] = useState<Revision[]>([]); const [error, setError] = useState(''); const [loading, setLoading] = useState(true);
  useEffect(() => { const controller = new AbortController(); setLoading(true); setError(''); api.history(asset.id, controller.signal).then(setHistory).catch(error => { if (!controller.signal.aborted) setError(errorText(error)); }).finally(() => { if (!controller.signal.aborted) setLoading(false); }); return () => controller.abort(); }, [asset.id, asset.revision]);
  return <div className="history-editor"><div className="editor-intro"><div><h3>Revision history</h3><p>Restore a saved revision or branch into another figure.</p></div><button className="button" disabled={disabled} onClick={onFork}><GitBranch size={15} />Fork figure</button></div>{loading && <p className="muted"><LoaderCircle className="spin" size={14} /> Loading revisions…</p>}{error && <p className="inline-error" role="alert">{error}</p>}{history.map(revision => <div className="revision-row" key={revision.revision}><div className="revision-dot"><GitBranch size={15} /></div><div><strong>Revision {revision.revision}</strong><p>{revision.title || asset.title} · {revision.created_at ? new Date(revision.created_at).toLocaleString() : 'Saved revision'}</p><details><summary>Inspect saved specification</summary><pre>{JSON.stringify({ spec: revision.spec, caption: revision.caption }, null, 2)}</pre></details></div>{revision.revision === asset.revision ? <span className="badge ready"><Check size={12} />Current</span> : <button className="button small" disabled={disabled} onClick={() => onRestore(revision.revision)}><RefreshCw size={13} />Restore</button>}</div>)}</div>;
}

export function JobList({ jobs, onCancel, onInspect, disabled }: { jobs: Job[]; onCancel: (id: string) => void; onInspect: (id: string) => void; disabled: boolean }) {
  if (!jobs.length) return <div className="compact-empty"><Play size={22} /><h3>No jobs</h3></div>;
  return <div className="job-list">{[...jobs].sort((a, b) => b.created_at.localeCompare(a.created_at)).map(job => <div className="job-row" key={job.id}><div className={`job-dot ${job.status}`}>{['queued', 'running'].includes(job.status) ? <LoaderCircle size={16} className="spin" /> : job.status === 'completed' ? <Check size={16} /> : <Play size={14} />}</div><div className="job-info"><strong>{job.action} <span className="muted">· {job.scope}</span></strong><p>{job.stage || job.status} · {new Date(job.created_at).toLocaleTimeString()}</p>{job.error && <p className="job-error">{job.error}</p>}<details><summary onClick={() => onInspect(job.id)}>Job details</summary><pre>{JSON.stringify(job, null, 2)}</pre></details></div><span className={`badge ${job.status}`}>{job.status}</span>{['queued', 'running'].includes(job.status) && <button className="button small" disabled={disabled} onClick={() => onCancel(job.id)}>Cancel</button>}</div>)}</div>;
}

export function CandidateEditor({ asset, candidateId, disabled, onPreview, onApply }: { asset: Asset; candidateId: string | null; disabled: boolean; onPreview: (id: string | null) => void; onApply: (id: string) => void }) {
  if (!asset.candidates.length) return <div className="compact-empty"><h3>No candidates</h3></div>;
  const candidate = asset.candidates.find(item => item.id === candidateId);
  const review = candidate ? candidate.review : asset.review;
  return <div className="candidate-details">
    <div className="candidate-detail-heading"><label>Output<select aria-label="Candidate details" value={candidate?.id || ''} onChange={event => onPreview(event.target.value || null)}><option value="">Saved output</option>{asset.candidates.map((item, index) => <option key={item.id} value={item.id}>{index + 1}. {item.label || item.id}{item.selected ? ' · Applied' : ''}</option>)}</select></label>{candidate && <button className="button primary small" disabled={disabled || Boolean(candidate.selected)} onClick={() => onApply(candidate.id)}>{candidate.selected ? <><Check size={13} />Applied</> : 'Apply candidate'}</button>}</div>
    {candidate && <p className="candidate-id">{candidate.id}</p>}
    <div className="candidate-files">{previewArtifacts(asset, candidate).map(file => <a className="text-link" key={file.name} href={artifactUrl(asset, file)} download>{file.name}</a>)}</div>
    {review ? <details className="details" open><summary>Review</summary><pre>{JSON.stringify(review, null, 2)}</pre></details> : <p className="muted">No review recorded.</p>}
    {candidate?.caption && <details className="details"><summary>Caption</summary><p className="candidate-caption">{candidate.caption}</p></details>}
    {candidate?.spec && <details className="details"><summary>Specification</summary><pre>{JSON.stringify(candidate.spec, null, 2)}</pre></details>}
  </div>;
}

export function SourceInspector({ source }: { source: Source }) {
  const extension = source.name.split('.').pop()?.toLowerCase();
  const url = sourceUrl(source);
  return <div className="source-inspector"><div className="editor-intro"><div><h3>{source.name}</h3><p>{source.kind} · {(source.size / 1024).toFixed(1)} KB · original uploaded file</p></div><a className="button" href={url} download>Download source</a></div>{extension === 'pdf' ? <iframe title={source.name} src={url} /> : ['png', 'jpg', 'jpeg', 'svg'].includes(extension || '') ? <img src={url} alt={source.name} /> : columnNames(source).length ? <DataPreview source={source} /> : <pre className="source-text">{source.text || 'No extracted text is available. Download the original file to inspect it.'}</pre>}</div>;
}

export function Preview({ asset, candidateId = null, onPreview, onApply, applyDisabled = false, format, setFormat }: {
  asset: Asset; candidateId?: string | null; onPreview?: (id: string | null) => void; onApply?: (id: string) => void;
  applyDisabled?: boolean; format: string; setFormat: (value: string) => void;
}) {
  const [expanded, setExpanded] = useState(false); const [zoom, setZoom] = useState('fit'); const [naturalWidth, setNaturalWidth] = useState(0);
  const [failedUrl, setFailedUrl] = useState(''); const [failedThumbs, setFailedThumbs] = useState<Record<string, boolean>>({});
  const candidate = asset.candidates.find(item => item.id === candidateId);
  const candidateIndex = candidate ? asset.candidates.indexOf(candidate) : -1;
  const files = previewArtifacts(asset, candidate);
  const available = files.filter(file => ['svg', 'png', 'pdf'].includes(file.format.toLowerCase()));
  const artifact = available.find(file => file.name === candidate?.outputs?.[format]) || available.find(file => file.name === candidate?.preview_name && file.format.toLowerCase() === format) || available.find(file => file.format.toLowerCase() === format) || available.find(file => file.name === candidate?.preview_name) || available.find(file => file.format.toLowerCase() === 'png') || available.find(file => file.format.toLowerCase() === 'svg') || available[0];
  const url = artifact ? artifactUrl(asset, artifact) : '';
  const title = candidate ? `${asset.title} · ${candidate.label || candidate.id}` : asset.title;
  const choices = [null, ...asset.candidates.map(item => item.id)];
  const currentIndex = candidateIndex + 1;
  function step(direction: number) { onPreview?.(choices[(currentIndex + direction + choices.length) % choices.length]); }
  useEffect(() => { setNaturalWidth(0); setFailedUrl(''); }, [url]);
  useEffect(() => { const close = (event: KeyboardEvent) => { if (event.key === 'Escape') setExpanded(false); }; window.addEventListener('keydown', close); return () => window.removeEventListener('keydown', close); }, []);
  return <div className={`preview-region ${expanded ? 'expanded-preview' : ''}`} tabIndex={0} aria-label="Figure preview" onKeyDown={event => {
    if (!onPreview || !asset.candidates.length || event.altKey || event.ctrlKey || event.metaKey || ['INPUT', 'TEXTAREA', 'SELECT'].includes((event.target as HTMLElement).tagName)) return;
    if (event.key === 'ArrowRight' || event.key === 'ArrowLeft') { event.preventDefault(); step(event.key === 'ArrowRight' ? 1 : -1); }
  }}>
    <div className="preview-toolbar"><span>{candidate ? `Candidate ${candidateIndex + 1} / ${asset.candidates.length}${candidate.selected ? ' · Applied' : ''}` : asset.status === 'stale' ? 'Saved output · stale' : 'Saved output'}</span>
      <div className="format-switch">{Array.from(new Set(available.map(file => file.format.toLowerCase()))).map(value => <button key={value} className={artifact?.format.toLowerCase() === value ? 'active' : ''} onClick={() => setFormat(value)}>{value.toUpperCase()}</button>)}</div>
      {artifact && <><select className="preview-zoom" aria-label="Preview zoom" value={zoom} onChange={event => setZoom(event.target.value)} disabled={artifact.format.toLowerCase() === 'pdf'}><option value="fit">Fit</option><option value="100">100%</option><option value="150">150%</option><option value="200">200%</option></select><a className="icon-button" title="Open original artifact" aria-label="Open original artifact" href={url} target="_blank" rel="noreferrer"><ExternalLink size={14} /></a><a className="text-link" href={url} download>Download {artifact.format.toUpperCase()}</a></>}
      {candidate && onApply && <button className="button primary small candidate-apply" disabled={applyDisabled || Boolean(candidate.selected)} onClick={() => onApply(candidate.id)}>{candidate.selected ? 'Applied' : 'Apply candidate'}</button>}
      <button className="icon-button" aria-label={expanded ? 'Close large preview' : 'Large preview'} onClick={() => setExpanded(!expanded)}>{expanded ? <X size={15} /> : <Maximize2 size={14} />}</button>
    </div>
    <div className="preview-body"><div className="preview-canvas">{artifact && failedUrl !== url ? <div className={`print-sheet ${artifact.format.toLowerCase() === 'pdf' ? 'pdf-sheet' : ''} ${zoom !== 'fit' && artifact.format.toLowerCase() !== 'pdf' ? 'zoom-sheet' : ''}`}>{artifact.format.toLowerCase() === 'pdf' ? <iframe title={`${title} PDF`} src={url} /> : <img key={url} alt={title} src={url} onError={() => setFailedUrl(url)} onLoad={event => setNaturalWidth(event.currentTarget.naturalWidth)} style={zoom !== 'fit' && naturalWidth ? { width: naturalWidth * Number(zoom) / 100, height: 'auto', maxWidth: 'none' } : undefined} />}</div> : <div className="canvas-empty"><h2>{failedUrl === url && url ? 'Preview file unavailable' : candidate ? 'No preview file' : 'No output'}</h2>{!candidate && !artifact && <p>{asset.kind === 'diagram' ? 'Add a brief or scene, then generate or render.' : 'Select a data source and columns, then render.'}</p>}</div>}</div>
      {asset.candidates.length > 0 && <div className="candidate-rail" aria-label="Candidate previews"><div className="candidate-rail-heading"><span>Candidates</span><div><button className="icon-button" aria-label="Previous preview" onClick={() => step(-1)} disabled={!onPreview}><ChevronLeft size={14} /></button><button className="icon-button" aria-label="Next preview" onClick={() => step(1)} disabled={!onPreview}><ChevronRight size={14} /></button></div></div><button className={`saved-preview ${!candidate ? 'active' : ''}`} onClick={() => onPreview?.(null)} aria-pressed={!candidate}>Saved output</button>
      <div className="candidate-thumbnails">{asset.candidates.map((item, index) => { const thumbnail = candidateUrl(asset, item); return <button key={item.id} className={`candidate-thumbnail ${candidate?.id === item.id ? 'active' : ''}`} aria-label={`Preview candidate ${index + 1}: ${item.label || item.id}`} aria-pressed={candidate?.id === item.id} onClick={() => onPreview?.(item.id)}><div>{thumbnail && !failedThumbs[thumbnail] ? <img src={thumbnail} alt="" loading="lazy" onError={() => setFailedThumbs(previous => ({ ...previous, [thumbnail]: true }))} /> : <span>No preview</span>}</div><span>{index + 1}. {item.label || item.id}{item.selected && <Check size={11} aria-label="Applied" />}</span></button>; })}</div></div>}
    </div>
  </div>;
}
