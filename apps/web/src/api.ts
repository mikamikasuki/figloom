// Compatibility entry point for the current UI. Transport and DTO aliases are
// independent of React and can be reused by another frontend.
import { api } from './client';
import type { Artifact, Asset, Candidate, Edge, Job, JsonObject, Settings, SettingsUpdate, Source } from './types';
export { api, ApiError, createApiClient } from './client';
export type { ApiClient } from './client';
export type * from './types';
export const request = api.request;
export const artifactUrl = api.urls.artifact;
export const candidateUrl = api.urls.candidate;
export const sourceUrl = api.urls.sourceFile;
export const assetExportUrl = api.urls.assetExport;
export const projectExportUrl = api.urls.projectExport;

export function previewArtifacts(asset: Asset, candidate?: Candidate): Artifact[] {
  if (!candidate) return asset.artifacts;
  const files = candidate.files.map(file => file.name === candidate.preview_name && !file.url && candidate.preview_url ? { ...file, url: candidate.preview_url } : file);
  const previewFormat = candidate.preview_name.split('.').pop()?.toLowerCase();
  if (candidate.preview_name && previewFormat && ['svg', 'png', 'pdf'].includes(previewFormat) && !files.some(file => file.name === candidate.preview_name)) {
    files.unshift({ name: candidate.preview_name, format: previewFormat, url: candidate.preview_url });
  }
  return files;
}

export function columnNames(source?: Source): string[] {
  return source?.columns || [];
}
export function activeJob(job: Job): boolean { return job.status === 'queued' || job.status === 'running'; }
export function errorText(error: unknown): string { return error instanceof Error ? error.message : String(error); }
export function parseSpec(value: string): JsonObject {
  const parsed = JSON.parse(value);
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) throw new Error('The specification must be a JSON object.');
  return parsed;
}
export function settingsPayload(value: Settings): SettingsUpdate {
  type EndpointUpdate = NonNullable<SettingsUpdate['text']>;
  const writableKeys = ['api', 'api_key', 'clear_api_key', 'base_url', 'model', 'local', 'input_price_per_million', 'output_price_per_million', 'cached_input_price_per_million', 'pricing', 'max_request_usd', 'max_output_tokens', 'context_length', 'timeout', 'temperature', 'max_retries', 'size', 'quality', 'format', 'count', 'max_candidates', 'model_parameters'] as const satisfies readonly (keyof EndpointUpdate)[];
  const endpoint = (input: Settings['text']): EndpointUpdate => {
    const result: Record<string, unknown> = {};
    for (const key of writableKeys) {
      const field = input?.[key];
      if (field === undefined || field === null || (key === 'api_key' && !field)) continue;
      // Pricing is a patch envelope; JSON model parameters retain all values.
      result[key] = key === 'pricing' ? Object.fromEntries(Object.entries(field).filter(([, amount]) => amount !== null && amount !== undefined)) : field;
    }
    return result as EndpointUpdate;
  };
  return { text: endpoint(value.text), image: endpoint(value.image), ...(value.budget_usd === undefined ? {} : { budget_usd: value.budget_usd }) };
}
export function workflowLayout(assets: Asset[], edges: Edge[]) {
  const rank = new Map(assets.map(asset => [asset.id, 0]));
  for (let pass = 0; pass < assets.length; pass++) {
    let changed = false;
    for (const edge of edges) {
      if (!rank.has(edge.source) || !rank.has(edge.target)) continue;
      const next = Math.min(assets.length - 1, rank.get(edge.source)! + 1);
      if (next > rank.get(edge.target)!) { rank.set(edge.target, next); changed = true; }
    }
    if (!changed) break;
  }
  const rows = new Map<number, number>();
  return assets.map(asset => { const column = rank.get(asset.id)!; const row = rows.get(column) || 0; rows.set(column, row + 1); return { asset, x: 24 + column * 230, y: 24 + row * 110 }; });
}
