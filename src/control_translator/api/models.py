"""Typed request/response models for the local API.

Every route accepts and returns one of these models rather than raw dicts, so
the transport contract is explicit and independently validated by pydantic
before any handler touches application/service code.
"""
from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, Field

_MAX_NAME_LENGTH = 200
_MAX_CONFIG_PATH_LENGTH = 4096
_MAX_IDENTIFIER_LENGTH = 200
_MAX_REASON_LENGTH = 500
_MAX_QUERY_LENGTH = 200
_MAX_BATCH_ITEMS = 100

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


class OpenProjectRequest(BaseModel):
    config_path: str | None = Field(default=None, max_length=_MAX_CONFIG_PATH_LENGTH)


class ProjectStatusResponse(BaseModel):
    project_id: str
    status: dict


class StartRunRequest(BaseModel):
    config_path: str | None = Field(default=None, max_length=_MAX_CONFIG_PATH_LENGTH)
    distribute: bool = True


class RunResponse(BaseModel):
    run: dict


class RunListResponse(BaseModel):
    count: int
    runs: list[dict]


class RunEventsResponse(BaseModel):
    count: int
    events: list[dict]


class ReviewResponse(BaseModel):
    count: int
    items: list[dict]


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


class MappingDetailsResponse(BaseModel):
    mapping: dict


class SearchControlsResponse(BaseModel):
    count: int
    results: list[dict]


class ArtifactSummaryResponse(BaseModel):
    summary: dict


class ArtifactResourceResponse(BaseModel):
    count: int
    items: list[dict]
