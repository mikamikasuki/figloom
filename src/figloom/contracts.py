"""Version 1 HTTP contracts, independent of any frontend implementation.

Request envelopes are strict. Scientific specifications and response metadata
retain their open JSON content so clients can edit and inspect complete assets.
"""
from __future__ import annotations

import json
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, StringConstraints, model_validator

JsonObject = dict[str, JsonValue]
ResourceID = Annotated[str, StringConstraints(strict=True, pattern=r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")]
Revision = Annotated[int, Field(strict=True, ge=1)]
Name = Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=200)]
Title = Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=500)]
Caption = Annotated[str, StringConstraints(strict=True, max_length=20_000)]
Money = Annotated[float, Field(ge=0, allow_inf_nan=False)]
AssetKind = Literal["diagram", "plot", "table"]
AssetStatus = Literal["draft", "stale", "ready", "failed"]
JobStatus = Literal["queued", "running", "completed", "failed", "cancelled"]
RunAction = Literal["render", "generate", "review"]
RunScope = Literal["single", "ancestors", "affected"]
ModelAPI = Literal["responses", "chat_completions", "ollama", "images"]
ProviderKind = Literal["text", "image"]


class RequestBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)

    @model_validator(mode="before")
    @classmethod
    def finite_body(cls, value):
        if isinstance(value, dict):
            try:
                encoded = json.dumps(value, allow_nan=False)
            except (TypeError, ValueError):
                raise ValueError("Request must contain finite JSON values") from None
            if len(encoded) > 1_000_000:
                raise ValueError("Request exceeds the JSON size limit")
        return value


class NonNullablePatch(RequestBody):
    @classmethod
    def __get_pydantic_json_schema__(cls, core_schema, handler):
        schema = handler(core_schema)
        resolved = handler.resolve_ref_schema(schema)
        # None is only an internal omitted-field default. Do not tell generated
        # clients that sending null is valid. Nested scientific JSON is untouched.
        for field in resolved.get("properties", {}).values():
            if field.get("default", object()) is None:
                field.pop("default", None)
            options = field.get("anyOf")
            if options:
                options = [x for x in options if x.get("type") != "null"]
                if len(options) == 1:
                    field.pop("anyOf", None)
                    field.update(options[0])
                else:
                    field["anyOf"] = options
        return schema

    @model_validator(mode="before")
    @classmethod
    def reject_null_edits(cls, value):
        if isinstance(value, dict) and any(v is None for v in value.values()):
            raise ValueError("Omit unchanged fields instead of supplying null")
        return value


class ProjectCreate(RequestBody):
    name: Name
    description: Annotated[str, Field(max_length=20_000)] = ""


class ProjectUpdate(NonNullablePatch):
    expected_revision: Revision
    name: Name | None = None
    description: Annotated[str, Field(max_length=20_000)] | None = None


class ProjectFork(NonNullablePatch):
    name: Name | None = None


class AssetCreate(NonNullablePatch):
    kind: AssetKind
    title: Title
    source_ids: list[ResourceID] = Field(default_factory=list, max_length=100)
    spec: JsonObject | None = None
    caption: Caption = ""

    @model_validator(mode="before")
    @classmethod
    def omitted_spec_only(cls, value):
        if isinstance(value, dict) and value.get("spec", {}) is None:
            raise ValueError("Omit spec to use the default specification")
        return value


class AssetUpdate(NonNullablePatch):
    """spec and source_ids replace their complete previous values."""
    expected_revision: Revision
    title: Title | None = None
    caption: Caption | None = None
    spec: JsonObject | None = None
    source_ids: list[ResourceID] | None = Field(default=None, max_length=100)


class AssetFork(NonNullablePatch):
    title: Title | None = None


class AssetRestore(RequestBody):
    revision: Revision
    expected_revision: Revision


class AssetRun(RequestBody):
    action: RunAction = "render"
    scope: RunScope = "single"


class AssetSelect(RequestBody):
    candidate_id: Annotated[str, StringConstraints(strict=True, min_length=1, max_length=500)]
    expected_revision: Revision


class EdgeCreate(RequestBody):
    source: ResourceID
    target: ResourceID


class ChatCreate(RequestBody):
    message: Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=12_000)]
    asset_id: ResourceID | None = None
    expected_revision: Revision | None = None

    @model_validator(mode="after")
    def bound_revision(self):
        if self.asset_id is not None and self.expected_revision is None:
            raise ValueError("An asset-scoped chat requires expected_revision")
        return self


class ProposalApply(RequestBody):
    expected_revision: Revision


class TokenPricing(NonNullablePatch):
    input_per_million: Money | None = None
    output_per_million: Money | None = None
    cached_input_per_million: Money | None = None
    currency: Literal["USD"] = "USD"


class EndpointFields(NonNullablePatch):
    base_url: Annotated[str, Field(max_length=4000)] | None = None
    model: Annotated[str, Field(max_length=4000)] | None = None
    api: ModelAPI | None = None
    local: bool | None = None
    input_price_per_million: Money | None = None
    output_price_per_million: Money | None = None
    cached_input_price_per_million: Money | None = None
    pricing: TokenPricing | None = None
    max_request_usd: Money | None = None
    max_output_tokens: Annotated[int, Field(ge=1, le=16_384)] | None = None
    context_length: Annotated[int, Field(ge=1, le=128_000)] | None = None
    timeout: Annotated[float, Field(ge=1, le=300)] | None = None
    temperature: Money | None = None
    max_retries: Annotated[int, Field(ge=0, le=2)] | None = None
    size: Annotated[str, Field(max_length=100)] | None = None
    quality: Annotated[str, Field(max_length=100)] | None = None
    format: Annotated[str, Field(max_length=100)] | None = None
    count: Annotated[int, Field(ge=1, le=5)] | None = None
    max_candidates: Annotated[int, Field(ge=1, le=5)] | None = None
    model_parameters: JsonObject | None = None


class EndpointUpdate(EndpointFields):
    api_key: Annotated[str, Field(max_length=4000, json_schema_extra={"writeOnly": True})] | None = None
    clear_api_key: bool | None = None


class ProviderProbe(NonNullablePatch):
    config: EndpointUpdate | None = None


class SettingsUpdate(NonNullablePatch):
    """Merge supplied fields; preserve keys only for the same provider connection.

    clear_api_key removes an endpoint's own key. Omitted image endpoints inherit
    text; explicit image endpoints use only their own image credentials.
    """
    text: EndpointUpdate | None = None
    image: EndpointUpdate | None = None
    budget_usd: Money | None = None
    # Compatibility fields accepted by the existing API and explicitly documented.
    text_base_url: Annotated[str, Field(max_length=4000)] | None = None
    text_model: Annotated[str, Field(max_length=4000)] | None = None
    text_api_key: Annotated[str, Field(max_length=4000, json_schema_extra={"writeOnly": True})] | None = None
    text_api: ModelAPI | None = None
    text_input_price_per_million: Money | None = None
    text_output_price_per_million: Money | None = None
    text_max_request_usd: Money | None = None
    image_base_url: Annotated[str, Field(max_length=4000)] | None = None
    image_model: Annotated[str, Field(max_length=4000)] | None = None
    image_api_key: Annotated[str, Field(max_length=4000, json_schema_extra={"writeOnly": True})] | None = None
    image_api: ModelAPI | None = None
    image_input_price_per_million: Money | None = None
    image_output_price_per_million: Money | None = None
    image_max_request_usd: Money | None = None


class DTO(BaseModel):
    model_config = ConfigDict(extra="allow", allow_inf_nan=False)


class HealthDTO(DTO):
    status: Literal["ok"]


class ProjectDTO(DTO):
    id: str
    name: str
    description: str
    revision: int
    created_at: str
    updated_at: str


class SourceDTO(DTO):
    id: str
    project_id: str
    name: str
    kind: str
    size: int
    revision: int
    created_at: str
    updated_at: str
    text: str | None = None
    columns: list[str] | None = None
    column_types: dict[str, str] | None = None
    preview: list[JsonObject] | str | None = None
    unique_values: dict[str, list[JsonValue]] | None = None
    row_count: int | None = None
    page_count: int | None = None
    width: int | None = None
    height: int | None = None
    image_format: str | None = None


class ArtifactDTO(DTO):
    name: str
    format: str
    url: str | None = None


class CandidateDTO(DTO):
    id: str
    label: str
    preview_name: str
    preview_url: str | None = None
    files: list[ArtifactDTO]
    selected: bool | None = None
    spec: JsonObject | None = None
    caption: str | None = None
    review: JsonObject | None = None
    style: JsonObject | None = None
    outputs: dict[str, str] | None = None


class ForkReferenceDTO(DTO):
    asset_id: str
    revision: int


class AssetDTO(DTO):
    id: str
    project_id: str
    kind: AssetKind
    title: str
    caption: str
    source_ids: list[str]
    spec: JsonObject
    revision: int
    status: AssetStatus
    artifacts: list[ArtifactDTO]
    candidates: list[CandidateDTO]
    review: JsonObject | None = None
    forked_from: ForkReferenceDTO | None = None
    created_at: str
    updated_at: str


class EdgeDTO(DTO):
    id: str
    project_id: str
    source: str
    target: str
    created_at: str


class MessageDTO(DTO):
    id: str
    project_id: str
    role: Literal["user", "assistant"]
    content: str
    asset_id: str | None = None
    created_at: str


class ProposalDTO(DTO):
    id: str
    project_id: str
    asset_id: str
    base_revision: int
    changes: JsonObject
    summary: str
    status: Literal["pending", "applied"]
    created_at: str
    applied_revision: int | None = None


class RenderedAssetDTO(DTO):
    asset_id: str
    revision: int


class JobResultDTO(DTO):
    assets: list[RenderedAssetDTO] | None = None
    message: MessageDTO | None = None
    proposal: ProposalDTO | None = None


class JobDTO(DTO):
    id: str
    project_id: str
    asset_id: str | None = None
    action: Literal["render", "generate", "review", "chat"]
    scope: RunScope
    status: JobStatus
    stage: str
    error: str | None = None
    result: JobResultDTO | None = None
    created_at: str
    updated_at: str


class ProjectStateDTO(DTO):
    project: ProjectDTO
    assets: list[AssetDTO]
    sources: list[SourceDTO]
    edges: list[EdgeDTO]
    jobs: list[JobDTO]
    messages: list[MessageDTO]
    proposals: list[ProposalDTO]


class TokenPricingDTO(DTO):
    input_per_million: float | None = None
    output_per_million: float | None = None
    cached_input_per_million: float | None = None
    currency: str | None = None


class EndpointDTO(DTO):
    has_api_key: bool
    base_url: str | None = None
    model: str | None = None
    api: ModelAPI | None = None
    local: bool | None = None
    input_price_per_million: float | None = None
    output_price_per_million: float | None = None
    cached_input_price_per_million: float | None = None
    pricing: TokenPricingDTO | None = None
    max_request_usd: float | None = None
    max_output_tokens: int | None = None
    context_length: int | None = None
    timeout: float | None = None
    temperature: float | None = None
    max_retries: int | None = None
    size: str | None = None
    quality: str | None = None
    format: str | None = None
    count: int | None = None
    max_candidates: int | None = None
    model_parameters: JsonObject | None = None


class SettingsDTO(DTO):
    text: EndpointDTO
    image: EndpointDTO
    budget_usd: float
    spent: float
    reserved: float
    available: float


class DeleteDTO(DTO):
    deleted: Literal[True]
    id: str | None = None


class ProviderModelDTO(DTO):
    id: str


class ProviderModelsDTO(DTO):
    models: list[ProviderModelDTO]
    checked_at: str


class ProviderTestDTO(DTO):
    reachable: Literal[True]
    model: str | None
    model_available: bool | None
    checked_at: str


ErrorCode = Literal["invalid_request", "validation_error", "revision_conflict", "resource_conflict", "forbidden", "not_found",
                    "payload_too_large", "internal_error", "service_unavailable", "budget_exceeded", "method_not_allowed",
                    "provider_authentication_failed", "provider_endpoint_not_found", "provider_unavailable", "provider_invalid_response"]


class ValidationIssue(BaseModel):
    location: list[str | int]
    type: str
    message: str


class ErrorDetail(BaseModel):
    code: ErrorCode
    message: str
    details: list[ValidationIssue] | None = None


class ErrorResponse(BaseModel):
    detail: str
    error: ErrorDetail
    request_id: str
