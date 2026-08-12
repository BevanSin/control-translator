"""Versioned local API routes: projects, runs, review/mutations, artifacts.

Every route that reads or mutates project data requires the ephemeral local
session token (see ``security.py``); only the health check is unauthenticated.
Handlers are thin: they validate/translate transport-level input, call into
the shared Phase 2 ``ControlTranslatorService`` / ``PipelineService``, and
shape the typed response — no pipeline or storage logic is duplicated here.
"""
from __future__ import annotations

import os

from fastapi import APIRouter, Depends, HTTPException

from ..application import ApplicationServiceError, ControlTranslatorService, to_dict
from ..projects import Project
from ..runs import RunNotFoundError
from . import models
from .security import SessionTokenAuth

# Artifact resources are served from an explicit allow-list of known bundle
# files rather than an arbitrary client-supplied filename, so this route can
# never be used to read anything else out of a project's bundle directory.
_ARTIFACT_RESOURCES = frozenset({"oos-candidates.json", "oos-reconsidered.json"})


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
            raise HTTPException(status_code=403, detail="Project id does not match the supplied configuration.")

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
        project = service.project_store.create(name=body.name)
        return _project_response(project)

    @router.post(
        "/projects/{project_id}/open",
        response_model=models.ProjectStatusResponse,
        dependencies=auth_dep,
    )
    def open_project(project_id: str, body: models.OpenProjectRequest) -> models.ProjectStatusResponse:
        _require_matching_project(project_id, body.config_path)
        status = service.status(config_path=body.config_path, resolution_root=_resolution_root())
        return models.ProjectStatusResponse(project_id=project_id, status=status)

    @router.delete("/projects/{project_id}", status_code=204, dependencies=auth_dep)
    def delete_project(project_id: str) -> None:
        service.project_store.delete(project_id)

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
    def get_run_events(project_id: str, run_id: str) -> models.RunEventsResponse:
        events = service.pipeline_service.events(project_id, run_id)
        return models.RunEventsResponse(count=len(events), events=events)

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
    def pending_review(project_id: str, config_path: str | None = None) -> models.ReviewResponse:
        _require_matching_project(project_id, config_path)
        payload = service.pending_review(config_path=config_path, resolution_root=_resolution_root())
        return models.ReviewResponse(count=payload["count"], items=payload["items"])

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

    @router.get(
        "/projects/{project_id}/mappings/search",
        response_model=models.SearchControlsResponse,
        dependencies=auth_dep,
    )
    def search_controls(project_id: str, query: str, status: str | None = None, limit: int = 20,
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
        return models.ArtifactSummaryResponse(summary=summary)

    @router.get(
        "/projects/{project_id}/artifacts/{resource_name}",
        response_model=models.ArtifactResourceResponse,
        dependencies=auth_dep,
    )
    def artifact_resource(project_id: str, resource_name: str,
                          config_path: str | None = None) -> models.ArtifactResourceResponse:
        if resource_name not in _ARTIFACT_RESOURCES:
            raise HTTPException(status_code=404, detail="Unknown artifact resource.")
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
