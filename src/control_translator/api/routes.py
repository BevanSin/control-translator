"""Versioned local API routes: projects, runs, review/mutations, artifacts.

Every route that reads or mutates project data requires the ephemeral local
session token (see ``security.py``); only the health check is unauthenticated.
Handlers are thin: they validate/translate transport-level input, call into
the shared Phase 2 ``ControlTranslatorService`` / ``PipelineService``, and
shape the typed response — no pipeline or storage logic is duplicated here.
"""
from __future__ import annotations

import os
from typing import Annotated, Iterable
from uuid import uuid4

from fastapi import APIRouter, Depends, Query
from fastapi.responses import FileResponse

from ..application import ApplicationServiceError, ControlTranslatorService, to_dict
from ..projects import Project
from ..runs import RunNotFoundError
from ..runs.lock import ProjectMutationLock
from . import models
from .errors import ProjectMismatchError, UnknownArtifactResourceError
from .security import SessionTokenAuth

# Artifact resources are served from an explicit allow-list of known bundle
# files rather than an arbitrary client-supplied filename, so this route can
# never be used to read anything else out of a project's bundle directory.
_ARTIFACT_RESOURCES = frozenset({"oos-candidates.json", "oos-reconsidered.json"})
_SAFE_EVENT_KINDS = frozenset({"validation", "oos-staleness", "interrupted"})
_EVENT_ACTIONS = {
    "run.started": "Pipeline started",
    "run.completed": "Pipeline completed",
    "run.failed": "Pipeline failed",
    "run.cancelled": "Pipeline cancelled",
    "run.warning": "Pipeline warning",
    "stage.started": "started",
    "stage.progress": "in progress",
    "stage.completed": "completed",
    "stage.failed": "failed",
    "stage.cancelled": "cancelled",
}


def build_router(service: ControlTranslatorService, require_token: SessionTokenAuth) -> APIRouter:
    router = APIRouter(prefix="/api/v1")
    auth_dep = [Depends(require_token)]

    def _resolution_root() -> str:
        return os.getcwd()

    def _require_matching_project(project_id: str, config_path: str | None) -> None:
        """Confirm ``config_path`` actually belongs to ``project_id``.

        This is the sole gate against cross-project access: a caller cannot
        operate on another project's data by naming its id in the URL while
        supplying a different (or that project's own) config path — the
        derived id must match exactly.
        """
        derived = service.project_id_for_config(config_path, resolution_root=_resolution_root())
        if derived != project_id:
            raise ProjectMismatchError("Project id does not match the supplied configuration.")

    @router.get("/health", response_model=models.HealthResponse)
    def health() -> models.HealthResponse:
        return models.HealthResponse()

    @router.get("/projects", response_model=models.ProjectListResponse, dependencies=auth_dep)
    def list_projects() -> models.ProjectListResponse:
        projects = service.project_store.list()
        return models.ProjectListResponse(
            count=len(projects),
            projects=[_project_response(p) for p in projects],
        )

    @router.post("/projects", response_model=models.ProjectResponse, dependencies=auth_dep, status_code=201)
    def create_project(body: models.CreateProjectRequest) -> models.ProjectResponse:
        # A project's id is always derived from the config it will be operated
        # on with (see project_id_for_config / _require_matching_project) — so
        # creation must bind to that same derived id up front. Otherwise a
        # freshly created project could never pass the config-match check any
        # other route enforces, and would be permanently unusable.
        project_id = service.project_id_for_config(body.config_path, resolution_root=_resolution_root())
        project = service.project_store.create(name=body.name, project_id=project_id)
        return _project_response(project)

    @router.post(
        "/projects/{project_id}/open",
        response_model=models.ProjectStatusResponse,
        dependencies=auth_dep,
    )
    def open_project(project_id: str, body: models.OpenProjectRequest) -> models.ProjectStatusResponse:
        _require_matching_project(project_id, body.config_path)
        status = service.status(config_path=body.config_path, resolution_root=_resolution_root())
        return _status_response(project_id, status)

    @router.delete("/projects/{project_id}", status_code=204, dependencies=auth_dep)
    def delete_project(project_id: str) -> None:
        lock = ProjectMutationLock(service.project_store, project_id)
        deleted = False
        try:
            lock.acquire(run_id=uuid4().hex)
            service.project_store.delete(project_id)
            deleted = True
        finally:
            if not deleted:
                lock.release()

    @router.post(
        "/projects/{project_id}/sources/upload",
        response_model=models.IngestSourceResponse,
        dependencies=auth_dep,
        status_code=201,
    )
    def upload_source(project_id: str, body: models.UploadSourceRequest) -> models.IngestSourceResponse:
        _require_matching_project(project_id, body.config_path)
        ingested = service.ingest_uploaded_source(
            config_path=body.config_path,
            resolution_root=_resolution_root(),
            filename=body.filename,
            payload=bytes(body.content),
            content_type=body.content_type,
        )
        return models.IngestSourceResponse(**to_dict(ingested))

    @router.post(
        "/projects/{project_id}/sources/url",
        response_model=models.IngestSourceResponse,
        dependencies=auth_dep,
        status_code=201,
    )
    def ingest_url_source(project_id: str, body: models.IngestUrlRequest) -> models.IngestSourceResponse:
        _require_matching_project(project_id, body.config_path)
        ingested = service.ingest_url_source(
            config_path=body.config_path,
            resolution_root=_resolution_root(),
            url=body.url,
            timeout_seconds=body.timeout_seconds,
        )
        return models.IngestSourceResponse(**to_dict(ingested))

    @router.post(
        "/projects/{project_id}/runs",
        response_model=models.RunResponse,
        dependencies=auth_dep,
        status_code=202,
    )
    def start_run(project_id: str, body: models.StartRunRequest) -> models.RunResponse:
        _require_matching_project(project_id, body.config_path)
        record = service.start_run(
            config_path=body.config_path, resolution_root=_resolution_root(),
            do_distribute=body.distribute,
        )
        return models.RunResponse(run=record.to_dict())

    @router.get("/projects/{project_id}/runs", response_model=models.RunListResponse, dependencies=auth_dep)
    def list_runs(project_id: str) -> models.RunListResponse:
        records = service.pipeline_service.list(project_id)
        return models.RunListResponse(count=len(records), runs=[r.to_dict() for r in records])

    @router.get(
        "/projects/{project_id}/runs/{run_id}",
        response_model=models.RunResponse,
        dependencies=auth_dep,
    )
    def get_run(project_id: str, run_id: str) -> models.RunResponse:
        record = service.pipeline_service.get(project_id, run_id)
        return models.RunResponse(run=record.to_dict())

    @router.get(
        "/projects/{project_id}/runs/{run_id}/events",
        response_model=models.RunEventsResponse,
        dependencies=auth_dep,
    )
    def get_run_events(
        project_id: str,
        run_id: str,
        after_sequence: Annotated[int | None, Query(ge=0)] = None,
    ) -> models.RunEventsResponse:
        record = service.pipeline_service.get(project_id, run_id)
        events = service.pipeline_service.events(project_id, run_id)
        safe_events = _ordered_unique_events(_event_response(event) for event in events)
        latest_sequence = safe_events[-1].sequence if safe_events else None
        if after_sequence is not None:
            safe_events = [event for event in safe_events if event.sequence > after_sequence]
        terminal_state = record.state.value if record.state.is_terminal else None
        return models.RunEventsResponse(
            count=len(safe_events),
            events=safe_events,
            dropped_event_count=service.pipeline_service.dropped_event_count(project_id, run_id),
            latest_sequence=latest_sequence,
            terminal_state=terminal_state,
        )

    @router.post(
        "/projects/{project_id}/runs/{run_id}/cancel",
        status_code=204,
        dependencies=auth_dep,
    )
    def cancel_run(project_id: str, run_id: str) -> None:
        # get() first: confirms this run belongs to this project (raises
        # RunNotFoundError for a cross-project run id) before requesting
        # cancellation, which is otherwise scoped only by the in-memory key.
        service.pipeline_service.get(project_id, run_id)
        try:
            service.pipeline_service.cancel(project_id, run_id)
        except RunNotFoundError:
            # Not actively tracked in this process (already terminal, or the
            # process restarted) — nothing more to do; the state is already
            # observable via get_run.
            pass

    @router.get(
        "/projects/{project_id}/review",
        response_model=models.ReviewResponse,
        dependencies=auth_dep,
    )
    def pending_review(
        project_id: str,
        query: Annotated[str, Query(max_length=models.MAX_QUERY_LENGTH)] = "",
        status: str | None = "review",
        page: Annotated[int, Query(ge=1)] = 1,
        page_size: Annotated[int, Query(ge=1, le=100)] = 20,
        config_path: str | None = None,
    ) -> models.ReviewResponse:
        _require_matching_project(project_id, config_path)
        if not query and status == "review" and page == 1 and page_size == 20:
            payload = service.pending_review(config_path=config_path, resolution_root=_resolution_root())
            return models.ReviewResponse(count=payload["count"], items=payload["items"],
                                         total=payload["count"], page=1, page_size=20)
        payload = service.list_mappings(
            query=query, status=status, page=page, page_size=page_size,
            config_path=config_path, resolution_root=_resolution_root(),
        )
        return models.ReviewResponse(**payload)

    @router.post(
        "/projects/{project_id}/review/approve",
        response_model=models.MappingMutationResponse,
        dependencies=auth_dep,
    )
    def approve(project_id: str, body: models.ControlIdsRequest,
                config_path: str | None = None) -> models.MappingMutationResponse:
        _require_matching_project(project_id, config_path)
        result = service.approve_controls(
            control_ids=body.control_ids, config_path=config_path, resolution_root=_resolution_root(),
        )
        return models.MappingMutationResponse(**to_dict(result))

    @router.post(
        "/projects/{project_id}/review/reject",
        response_model=models.MappingMutationResponse,
        dependencies=auth_dep,
    )
    def reject(project_id: str, body: models.ControlIdsRequest,
               config_path: str | None = None) -> models.MappingMutationResponse:
        _require_matching_project(project_id, config_path)
        result = service.reject_controls(
            control_ids=body.control_ids, config_path=config_path, resolution_root=_resolution_root(),
        )
        return models.MappingMutationResponse(**to_dict(result))

    @router.post(
        "/projects/{project_id}/oos",
        response_model=models.OOSMutationResponse,
        dependencies=auth_dep,
    )
    def add_to_oos(project_id: str, body: models.OOSRegisterRequest,
                   config_path: str | None = None) -> models.OOSMutationResponse:
        _require_matching_project(project_id, config_path)
        if len(body.policy_ids) != len(body.reasons):
            raise ApplicationServiceError("policy_ids and reasons must contain the same number of items.")
        result = service.add_to_oos_register(
            policy_ids=body.policy_ids, reasons=body.reasons, register=body.register_name,
            config_path=config_path, resolution_root=_resolution_root(),
        )
        return models.OOSMutationResponse(added=result.added)

    @router.post(
        "/projects/{project_id}/oos/reconsider",
        response_model=models.OOSReconsiderResponse,
        dependencies=auth_dep,
    )
    def reconsider_oos(project_id: str, body: models.OOSReconsiderRequest,
                       config_path: str | None = None) -> models.OOSReconsiderResponse:
        _require_matching_project(project_id, config_path)
        result = service.remove_from_oos_register(
            policy_ids=body.policy_ids, config_path=config_path, resolution_root=_resolution_root(),
        )
        return models.OOSReconsiderResponse(**to_dict(result))

    @router.get(
        "/projects/{project_id}/guidance",
        response_model=models.GuidanceResponse,
        dependencies=auth_dep,
    )
    def list_guidance(project_id: str, config_path: str | None = None) -> models.GuidanceResponse:
        _require_matching_project(project_id, config_path)
        payload = service.list_guidance(config_path=config_path, resolution_root=_resolution_root())
        return models.GuidanceResponse(**payload)

    @router.post(
        "/projects/{project_id}/guidance",
        response_model=models.GuidanceResponse,
        dependencies=auth_dep,
    )
    def save_guidance(project_id: str, body: models.GuidanceRequest,
                      config_path: str | None = None) -> models.GuidanceResponse:
        _require_matching_project(project_id, config_path)
        result = service.save_guidance(
            guidance_id=body.id,
            control_id=body.control_id,
            policy_id=body.policy_id,
            display_name=body.display_name,
            guidance=body.guidance,
            source=body.source,
            provenance=body.provenance,
            config_path=config_path,
            resolution_root=_resolution_root(),
        )
        return models.GuidanceResponse(**to_dict(result))

    @router.post(
        "/projects/{project_id}/guidance/delete",
        response_model=models.GuidanceResponse,
        dependencies=auth_dep,
    )
    def delete_guidance(project_id: str, body: models.DeleteGuidanceRequest,
                        config_path: str | None = None) -> models.GuidanceResponse:
        _require_matching_project(project_id, config_path)
        result = service.delete_guidance(
            guidance_ids=body.ids, config_path=config_path, resolution_root=_resolution_root(),
        )
        return models.GuidanceResponse(**to_dict(result))

    @router.get(
        "/projects/{project_id}/mappings/search",
        response_model=models.SearchControlsResponse,
        dependencies=auth_dep,
    )
    def search_controls(project_id: str, query: Annotated[str, Query(min_length=1, max_length=models.MAX_QUERY_LENGTH)],
                        status: str | None = None, limit: int = 20,
                        config_path: str | None = None) -> models.SearchControlsResponse:
        _require_matching_project(project_id, config_path)
        payload = service.search_controls(
            query=query, status=status, limit=limit,
            config_path=config_path, resolution_root=_resolution_root(),
        )
        return models.SearchControlsResponse(count=payload["count"], results=payload["results"])

    @router.get(
        "/projects/{project_id}/mappings/{control_id}",
        response_model=models.MappingDetailsResponse,
        dependencies=auth_dep,
    )
    def mapping_details(project_id: str, control_id: str,
                        config_path: str | None = None) -> models.MappingDetailsResponse:
        _require_matching_project(project_id, config_path)
        payload = service.mapping_details(
            control_id=control_id, config_path=config_path, resolution_root=_resolution_root(),
        )
        return models.MappingDetailsResponse(mapping=payload)

    @router.get(
        "/projects/{project_id}/artifacts",
        response_model=models.ArtifactSummaryResponse,
        dependencies=auth_dep,
    )
    def artifact_summary(project_id: str, config_path: str | None = None) -> models.ArtifactSummaryResponse:
        _require_matching_project(project_id, config_path)
        summary = service.bundle_summary(config_path=config_path, resolution_root=_resolution_root())
        return _artifact_summary_response(summary)

    @router.get(
        "/projects/{project_id}/artifacts/inventory",
        response_model=models.ArtifactInventoryResponse,
        dependencies=auth_dep,
    )
    def artifact_inventory(project_id: str, config_path: str | None = None) -> models.ArtifactInventoryResponse:
        _require_matching_project(project_id, config_path)
        payload = service.artifact_inventory(config_path=config_path, resolution_root=_resolution_root())
        return models.ArtifactInventoryResponse(**payload)

    @router.get(
        "/projects/{project_id}/artifacts/{resource_name}/preview",
        response_model=models.ArtifactPreviewResponse,
        dependencies=auth_dep,
    )
    def artifact_preview(project_id: str, resource_name: str,
                         config_path: str | None = None) -> models.ArtifactPreviewResponse:
        _require_matching_project(project_id, config_path)
        payload = service.artifact_preview(
            config_path=config_path, resolution_root=_resolution_root(), name=resource_name,
        )
        return models.ArtifactPreviewResponse(**payload)

    @router.get(
        "/projects/{project_id}/artifacts/{resource_name}/download",
        dependencies=auth_dep,
    )
    def artifact_download(project_id: str, resource_name: str,
                          config_path: str | None = None) -> FileResponse:
        _require_matching_project(project_id, config_path)
        path, content_type = service.artifact_download(
            config_path=config_path, resolution_root=_resolution_root(), name=resource_name,
        )
        return FileResponse(
            path,
            media_type=content_type,
            filename=resource_name,
            headers={"Content-Disposition": f'attachment; filename="{resource_name}"'},
        )

    @router.get(
        "/projects/{project_id}/artifacts/{resource_name}",
        response_model=models.ArtifactResourceResponse,
        dependencies=auth_dep,
    )
    def artifact_resource(project_id: str, resource_name: str,
                          config_path: str | None = None) -> models.ArtifactResourceResponse:
        if resource_name not in _ARTIFACT_RESOURCES:
            raise UnknownArtifactResourceError("Unknown artifact resource.")
        _require_matching_project(project_id, config_path)
        payload = service.bundle_json_resource(
            config_path=config_path, resolution_root=_resolution_root(), filename=resource_name,
        )
        return models.ArtifactResourceResponse(count=payload["count"], items=payload["items"])

    return router


def _project_response(project: Project) -> models.ProjectResponse:
    return models.ProjectResponse(
        id=project.id, name=project.name,
        created_at=project.created_at, updated_at=project.updated_at,
    )


def _status_response(project_id: str, status: dict) -> models.ProjectStatusResponse:
    # ``status`` also carries local filesystem paths (mapping_store,
    # latest_bundle) that must never cross the transport boundary — only the
    # explicit safe fields below are copied into the response model.
    return models.ProjectStatusResponse(
        project_id=project_id,
        framework=status["framework"],
        display_name=status["display_name"],
        store_exists=status["store_exists"],
        has_bundle=bool(status.get("latest_bundle")),
        total_mappings=status.get("total_mappings"),
        approved=status.get("approved"),
        pending_review=status.get("pending_review"),
        ignored=status.get("ignored"),
        last_run=status.get("last_run"),
    )


def _artifact_summary_response(summary: dict) -> models.ArtifactSummaryResponse:
    # ``summary`` also carries ``bundle_path`` (a local filesystem path) that
    # must never cross the transport boundary — it is intentionally dropped.
    return models.ArtifactSummaryResponse(
        files=summary.get("files", []),
        policy_definitions=summary.get("policy_definitions"),
        control_groups=summary.get("control_groups"),
        multi_control_policies=summary.get("multi_control_policies"),
        parameters=summary.get("parameters"),
    )


def _event_response(event: dict) -> models.PipelineEventResponse:
    event_type = str(event.get("type", ""))
    stage_value = event.get("stage")
    stage = str(stage_value) if isinstance(stage_value, str) else None
    action = _EVENT_ACTIONS.get(event_type, "Pipeline event")
    message = f"{stage.title()} {action}" if stage and event_type.startswith("stage.") else action

    summary: dict[str, bool | int | float | str | None] = {}
    raw_summary = event.get("summary")
    if isinstance(raw_summary, dict):
        for key, value in raw_summary.items():
            if value is None or isinstance(value, (bool, int, float)):
                summary[str(key)] = value
            elif key == "kind" and value in _SAFE_EVENT_KINDS:
                summary["kind"] = str(value)

    return models.PipelineEventResponse(
        schema_version=int(event.get("schema_version", 1)),
        type=event_type,
        run_id=str(event.get("run_id", "")),
        sequence=int(event.get("sequence", 0)),
        timestamp=str(event.get("timestamp", "")),
        stage=stage,
        message=message,
        summary=summary,
    )


def _ordered_unique_events(
    events: Iterable[models.PipelineEventResponse],
) -> list[models.PipelineEventResponse]:
    ordered: list[models.PipelineEventResponse] = []
    seen: set[int] = set()
    for event in sorted(events, key=lambda item: item.sequence):
        if event.sequence in seen:
            continue
        seen.add(event.sequence)
        ordered.append(event)
    return ordered
