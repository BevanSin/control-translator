"""Typed request/response models for the local API.

Every route accepts and returns one of these models rather than raw dicts, so
the transport contract is explicit and independently validated by pydantic
before any handler touches application/service code.
"""
from __future__ import annotations

from typing import Annotated

from pydantic import Base64Bytes, BaseModel, Field

_MAX_NAME_LENGTH = 200
_MAX_CONFIG_PATH_LENGTH = 4096
_MAX_IDENTIFIER_LENGTH = 200
_MAX_REASON_LENGTH = 500
MAX_QUERY_LENGTH = 200
_MAX_BATCH_ITEMS = 100
_MAX_FILENAME_LENGTH = 255
_MAX_URL_LENGTH = 2048
_MAX_CONTENT_TYPE_LENGTH = 200
_MIN_HTTPS_URL_LENGTH = len("https://")

_Identifier = Annotated[str, Field(min_length=1, max_length=_MAX_IDENTIFIER_LENGTH)]
_Reason = Annotated[str, Field(min_length=1, max_length=_MAX_REASON_LENGTH)]


class HealthResponse(BaseModel):
    status: str = "ok"


class ProjectResponse(BaseModel):
    id: str
    name: str
    created_at: str
    updated_at: str


class ProjectListResponse(BaseModel):
    count: int
    projects: list[ProjectResponse]


class CreateProjectRequest(BaseModel):
    name: str = Field(min_length=1, max_length=_MAX_NAME_LENGTH)
    config_path: str | None = Field(default=None, max_length=_MAX_CONFIG_PATH_LENGTH)


class OpenProjectRequest(BaseModel):
    config_path: str | None = Field(default=None, max_length=_MAX_CONFIG_PATH_LENGTH)


class ProjectStatusResponse(BaseModel):
    project_id: str
    framework: str
    display_name: str
    store_exists: bool
    has_bundle: bool
    total_mappings: int | None = None
    approved: int | None = None
    pending_review: int | None = None
    ignored: int | None = None
    last_run: dict | None = None


class StartRunRequest(BaseModel):
    config_path: str | None = Field(default=None, max_length=_MAX_CONFIG_PATH_LENGTH)
    distribute: bool = True


class RunResponse(BaseModel):
    run: dict


class RunListResponse(BaseModel):
    count: int
    runs: list[dict]


class PipelineEventResponse(BaseModel):
    schema_version: int
    type: str
    run_id: str
    sequence: int
    timestamp: str
    stage: str | None = None
    message: str
    summary: dict[str, bool | int | float | str | None]


class RunEventsResponse(BaseModel):
    count: int
    events: list[PipelineEventResponse]
    dropped_event_count: int = 0
    latest_sequence: int | None = None
    terminal_state: str | None = None


class ReviewResponse(BaseModel):
    count: int
    items: list[dict]
    total: int | None = None
    page: int | None = None
    page_size: int | None = None


class ControlIdsRequest(BaseModel):
    control_ids: list[_Identifier] = Field(min_length=1, max_length=_MAX_BATCH_ITEMS)


class MappingMutationResponse(BaseModel):
    updated: list[str]
    already_updated: list[str]
    not_found: list[str]


class OOSRegisterRequest(BaseModel):
    policy_ids: list[_Identifier] = Field(min_length=1, max_length=_MAX_BATCH_ITEMS)
    reasons: list[_Reason] = Field(min_length=1, max_length=_MAX_BATCH_ITEMS)
    register_name: str = Field(default="global", pattern="^(global|framework)$")


class OOSMutationResponse(BaseModel):
    added: list[str]


class OOSReconsiderRequest(BaseModel):
    policy_ids: list[_Identifier] = Field(min_length=1, max_length=_MAX_BATCH_ITEMS)


class OOSReconsiderResponse(BaseModel):
    removed: list[str]
    not_found: list[str]


class MappingDetailsResponse(BaseModel):
    mapping: dict


class SearchControlsResponse(BaseModel):
    count: int
    results: list[dict]


class GuidanceRequest(BaseModel):
    id: str | None = Field(default=None, max_length=_MAX_IDENTIFIER_LENGTH)
    control_id: _Identifier
    policy_id: _Identifier
    display_name: str = Field(default="", max_length=_MAX_NAME_LENGTH)
    guidance: str = Field(min_length=1, max_length=2000)
    source: str = Field(min_length=1, max_length=_MAX_NAME_LENGTH)
    provenance: str = Field(min_length=1, max_length=_MAX_NAME_LENGTH)


class DeleteGuidanceRequest(BaseModel):
    ids: list[_Identifier] = Field(min_length=1, max_length=_MAX_BATCH_ITEMS)


class GuidanceResponse(BaseModel):
    count: int | None = None
    items: list[dict] = []
    guidance: dict | None = None
    deleted: list[str] = []
    affects_future_runs: bool


class ArtifactSummaryResponse(BaseModel):
    files: list[str]
    policy_definitions: int | None = None
    control_groups: int | None = None
    multi_control_policies: int | None = None
    parameters: int | None = None


class ArtifactResourceResponse(BaseModel):
    count: int
    items: list[dict]


class ArtifactInventoryResponse(BaseModel):
    count: int
    items: list[dict]


class ArtifactPreviewResponse(BaseModel):
    name: str
    content_type: str
    text: str
    json: object | None = None
    truncated: bool


class UploadSourceRequest(BaseModel):
    config_path: str | None = Field(default=None, max_length=_MAX_CONFIG_PATH_LENGTH)
    filename: str = Field(min_length=1, max_length=_MAX_FILENAME_LENGTH)
    content_type: str | None = Field(default=None, max_length=_MAX_CONTENT_TYPE_LENGTH)
    content: Base64Bytes


class IngestUrlRequest(BaseModel):
    config_path: str | None = Field(default=None, max_length=_MAX_CONFIG_PATH_LENGTH)
    url: str = Field(min_length=_MIN_HTTPS_URL_LENGTH, max_length=_MAX_URL_LENGTH)
    timeout_seconds: int = Field(default=10, ge=1, le=30)


class IngestSourceResponse(BaseModel):
    source_id: str
    filename: str
    content_type: str
    size_bytes: int
    rows: int
    columns: int
    sha256: str
    project_path: str
