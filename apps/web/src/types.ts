import type { components, paths } from './generated/api-schema';

type Schemas = components['schemas'];
export type Project = Schemas['ProjectDTO'];
export type ProjectDetail = Schemas['ProjectStateDTO'];
export type Source = Schemas['SourceDTO'];
export type Asset = Schemas['AssetDTO'];
export type Artifact = Schemas['ArtifactDTO'];
export type Candidate = Schemas['CandidateDTO'];
export type Edge = Schemas['EdgeDTO'];
export type Job = Schemas['JobDTO'];
export type Message = Schemas['MessageDTO'];
export type Proposal = Schemas['ProposalDTO'];
export type SettingsDTO = Schemas['SettingsDTO'];
export type Health = Schemas['HealthDTO'];
export type DeleteResult = Schemas['DeleteDTO'];
export type ErrorResponse = Schemas['ErrorResponse'];
export type ProviderProbe = Schemas['ProviderProbe'];
export type ProviderModels = Schemas['ProviderModelsDTO'];
export type ProviderTest = Schemas['ProviderTestDTO'];
export type ProviderKind = paths['/api/v1/providers/{kind}/models']['post']['parameters']['path']['kind'];
export type Revision = paths['/api/v1/assets/{asset_id}/history']['get']['responses'][200]['content']['application/json'][number];

export type ProjectCreate = Schemas['ProjectCreate'];
export type ProjectUpdate = Schemas['ProjectUpdate'];
export type ProjectFork = Schemas['ProjectFork'];
export type AssetCreate = Schemas['AssetCreate'];
export type AssetUpdate = Schemas['AssetUpdate'];
export type AssetFork = Schemas['AssetFork'];
export type AssetRestore = Schemas['AssetRestore'];
export type AssetRun = Schemas['AssetRun'];
export type AssetSelect = Schemas['AssetSelect'];
export type EdgeCreate = Schemas['EdgeCreate'];
export type ChatCreate = Schemas['ChatCreate'];
export type ProposalApply = Schemas['ProposalApply'];
export type SettingsUpdate = Schemas['SettingsUpdate'];
export type AssetKind = Asset['kind'];
export type RunScope = NonNullable<AssetRun['scope']>;
export type RunAction = NonNullable<AssetRun['action']>;
export type JsonObject = Record<string, unknown>;

// A settings form temporarily contains both redacted read-only metadata and
// writable fields. Its transport is SettingsUpdate, never this view state.
export type Settings = Partial<Pick<SettingsDTO, 'budget_usd' | 'spent' | 'reserved' | 'available'>> & {
  text?: Partial<SettingsDTO['text']> & Pick<NonNullable<SettingsUpdate['text']>, 'api_key' | 'clear_api_key'>;
  image?: Partial<SettingsDTO['image']> & Pick<NonNullable<SettingsUpdate['image']>, 'api_key' | 'clear_api_key'>;
};
