# API v1

Figloom exposes a local HTTP API at `/api/v1`. The Dashboard uses the same API available to other clients. Scientific computation, file storage, revisions, and job execution stay in the backend; clients edit specifications and display returned resources.

The contract is defined in [`contracts.py`](../src/figloom/contracts.py) and exported as [`openapi.json`](openapi.json). With the server running, inspect `/api/v1/docs`, `/api/v1/redoc`, or `/api/v1/openapi.json`.

## Resources

Paths below are relative to `/api/v1`. IDs are opaque strings; encode them as URL path segments. Use returned artifact URLs or the client URL helpers instead of constructing filenames into a URL.

| Resource | Operations | Result |
| --- | --- | --- |
| `/health` | GET | Service health |
| `/projects` | GET, POST | Project list or newly created project |
| `/projects/import` | POST, multipart `file` | Imported project with remapped IDs |
| `/projects/{id}` | GET, PATCH, DELETE | Full project state, updated project, or deletion acknowledgement |
| `/projects/{id}/fork` | POST | Independent project branch |
| `/projects/{id}/export` | GET | Project ZIP with sources, revisions, and dependencies |
| `/projects/{id}/sources` | POST, multipart `file` | Inspected source metadata |
| `/sources/{id}` | GET, PUT, DELETE | Source metadata, replacement, or deletion acknowledgement |
| `/sources/{id}/file` | GET | Original file bytes |
| `/projects/{id}/assets` | POST | Diagram, plot, or table asset |
| `/assets/{id}` | GET, PATCH, DELETE | Current asset or deletion acknowledgement |
| `/assets/{id}/fork` | POST | Independent asset branch |
| `/assets/{id}/history` | GET | Saved asset revisions |
| `/assets/{id}/restore` | POST | Restored specification as a new revision |
| `/assets/{id}/run` | POST | Background job |
| `/assets/{id}/select` | POST | Selected candidate and its specification |
| `/assets/{id}/files/{name}` | GET | Allowlisted artifact bytes |
| `/assets/{id}/export` | GET | Current asset ZIP |
| `/projects/{id}/edges` | POST | Dependency edge |
| `/projects/{id}/edges/{edge_id}` | DELETE | Deletion acknowledgement |
| `/jobs/{id}` | GET | Actual job status and result |
| `/jobs/{id}/cancel` | POST | Cancellation status |
| `/projects/{id}/chat` | POST | Background chat job |
| `/proposals/{id}/apply` | POST | Updated asset |
| `/settings` | GET, PUT | Redacted provider settings and budget summary |
| `/providers/{kind}/models` | POST | Available model IDs for `text` or `image` |
| `/providers/{kind}/test` | POST | Metadata reachability and configured model availability |

JSON resource operations return objects directly. Successful operations use HTTP 200. A run or chat response acknowledges a queued job; it does not mean that rendering or a model request has completed. File and ZIP operations return file contents rather than resource DTOs, including JSON files where applicable.

## Editing and revisions

Project and asset PATCH requests require `expected_revision`. Restoring a revision, selecting a candidate, and applying a proposal also require the current revision. Source replacement uses multipart fields `file` and `expected_revision`.

```json
{
  "expected_revision": 3,
  "title": "Query-aware feature selection",
  "spec": {
    "source_id": "source-id",
    "x": "checkpoint",
    "y": "accuracy",
    "group": "method",
    "unit_id": "seed",
    "aggregation": "mean",
    "interval": "ci95",
    "chart_type": "line"
  }
}
```

Replace `source-id` with the ID returned by source upload.

Omitted PATCH fields retain their values. `spec` replaces the entire specification object, and `source_ids` replaces the entire source list; send the complete value when changing either. Top-level `spec`, `source_ids`, and provider credentials cannot be `null`. Individual optional mappings inside `spec` can be `null`.

Specifications remain open JSON objects so clients can edit scientific graphs, scenes, compositions, and rendering parameters without a new transport schema for every feature. The renderer checks their scientific and layout constraints. A successful save establishes a new revision; rendering and review are separate operations.

On HTTP 409, reload the current resource and compare it with local edits before submitting again. Do not automatically overwrite a newer revision. Editing an input invalidates dependent outputs; currentness is determined by the backend.

## Jobs and chat

Run requests accept `action: render | generate | review` and `scope: single | ancestors | affected`.

```json
{"action": "render", "scope": "affected"}
```

Poll `GET /jobs/{id}` while `status` is `queued` or `running`. Terminal statuses are `completed`, `failed`, and `cancelled`. `stage` describes the actual current operation and is not a percentage. Render results identify the assets produced; chat results contain the assistant message and an optional proposal. A project-level chat has no selected asset and cannot apply asset edits.

Selected-asset chat requests include `asset_id`, `expected_revision`, and `message`. The proposal remains separate from the asset until `POST /proposals/{id}/apply` succeeds. To apply and render, apply first and then enqueue a run using the returned asset ID. Jobs and asset states are independent: a completed render does not certify publication quality; inspect the review separately.

Cancellation stops subsequent stages. An external model request already in flight can finish before cancellation is observed. A cancelled job does not authorize promotion of its uncommitted output.

## Settings and files

Settings PUT merges only provided fields. Text and image configuration use separate objects. Omitted or empty `api_key` preserves the saved key for the same endpoint; changing the endpoint requires a new key. `clear_api_key: true` removes the saved key. GET and PUT responses return `has_api_key`, never the credential. Do not send read-only spending or credential-presence fields in an update.

Provider checks accept `{ "config": { ... } }` with optional endpoint overrides. They use the saved configuration for omitted fields, without saving the overrides. The same key-preservation rule applies. `models` reads model IDs; `test` reports `reachable`, `model`, `model_available`, and `checked_at`. Both read provider metadata without requesting text or image generation. A listed model does not establish image-input or image-generation support. If the provider has no model-list endpoint, enter its model ID manually.

An empty image endpoint uses the text endpoint, with the image key if supplied or the text key otherwise. An explicitly configured image endpoint uses only its own key. Provider checks use the same connection rule as generation.

Source uploads accept PDF, Markdown, text, LaTeX, CSV, TSV, JSON, PNG, JPEG, and SVG, up to 32 MiB per file. Project ZIP imports and uncompressed portable exports are limited to 128 MiB. JSON request objects are limited to 1 MB. An asset export requires a current render; a project backup can retain draft and stale history.

Artifact URLs include a generation query parameter that changes when new files are promoted. Preserve that query when previewing or downloading. File names can contain spaces or reserved URL characters. History snapshots identify retained files; use current asset file URLs for current previews.

## Errors

Errors use one JSON shape and include `X-Request-ID`:

```json
{
  "detail": "Revision changed. Reload the current resource.",
  "error": {
    "code": "revision_conflict",
    "message": "Revision changed. Reload the current resource."
  },
  "request_id": "request-identifier"
}
```

Use `error.code` and the HTTP status for decisions; display `error.message` to the user. `detail` remains available for existing clients. Validation details identify invalid fields without returning the submitted values. Request IDs identify requests; they do not deduplicate repeated POST operations. After an uncertain network failure, inspect project state and jobs before repeating a mutation.

| HTTP | Code | Client action |
| --- | --- | --- |
| 400 | `invalid_request` | Correct the request or resource bindings |
| 403 | `forbidden` | Use the permitted local origin |
| 404 | `not_found` | Refresh the resource list |
| 405 | `method_not_allowed` | Use the documented HTTP method |
| 409 | `revision_conflict` | Reload and reconcile local edits |
| 409 | `resource_conflict` | Resolve the reported state conflict |
| 413 | `payload_too_large` | Reduce the upload or export |
| 422 | `validation_error` | Correct fields indicated by validation details |
| 500 | `internal_error` | Retain the request ID for diagnosis |
| 503 | `service_unavailable` | Restore the missing service or Web build |
| 502 | `provider_authentication_failed` | Correct the provider API key |
| 502 | `provider_endpoint_not_found` | Check the API base or enter a model ID manually |
| 502 | `provider_invalid_response` | Check the provider's metadata API format |
| 503 | `provider_unavailable` | Check connectivity or retry the metadata check later |

## Using another frontend

[`client.ts`](../apps/web/src/client.ts) is independent of React. Its request and response types come from [`api-schema.ts`](../apps/web/src/generated/api-schema.ts), generated from OpenAPI.

```ts
import { createApiClient } from './client';

const studio = createApiClient('/api/v1');
const project = await studio.createProject('Attention study', 'Explain the query mechanism.');
const state = await studio.project(project.id);
```

The client supports a configurable API base and an injected `fetch` implementation. Browser requests must remain same-origin: serve the UI alongside the API, or proxy the API through the UI development server. The local server does not enable arbitrary cross-origin access. The existing Vite proxy forwards `/api` requests while preserving the browser's Host header.

Keep UI components dependent on client operations and returned resources, rather than backend storage paths. Use its URL helpers for source files, previews, and exports. A new frontend can reuse the client or generate a client for another language from `openapi.json`.

## Updating the contract

After changing a backend request or response model:

```bash
.venv/bin/python scripts/sync_api.py
.venv/bin/python scripts/sync_api.py --check
```

Frontend dependencies must be installed to generate TypeScript. `--schema-only` exports or checks OpenAPI without Node.js. CI checks that the application matches the committed OpenAPI and that generated TypeScript matches that schema.

Within v1, preserve operation IDs, existing fields, HTTP semantics, and enum meanings. Add optional fields and new operations compatibly. Removing or renaming fields, requiring new inputs, or changing an existing operation's semantics requires a new major API path. Legacy `/api/*` routes remain compatible aliases; new clients should use `/api/v1/*`.
