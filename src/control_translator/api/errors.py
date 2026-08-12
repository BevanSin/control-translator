"""Map application/project/run domain errors to stable, sanitized HTTP responses.

No handler here ever forwards ``str(exc)`` — every response body is a
hand-written, allow-listed message keyed off the exception type, so a stray
filesystem path, hostname, or credential fragment embedded in an underlying
exception can never leak into an HTTP response.
"""
from __future__ import annotations

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from ..application import (
    ApplicationServiceError,
    InvalidIdentifierError,
    PipelineExecutionError,
    PipelineInProgressError,
    ProjectConfigError,
    SourceIngestionFailedError,
    SourceLimitError,
    SourceUnsafeURLError,
    SourceUnsupportedError,
)
from ..projects import (
    ProjectAlreadyExistsError,
    ProjectMalformedError,
    ProjectNotFoundError,
    ProjectPathError,
    ProjectStoreError,
    UnsupportedProjectSchemaError,
)
from ..runs import (
    InvalidRunStateTransitionError,
    ProjectRunConflictError,
    RunMalformedError,
    RunNotFoundError,
    RunServiceError,
    UnsupportedRunSchemaError,
)


class ProjectMismatchError(Exception):
    """The URL's project id does not match the id derived from ``config_path``."""


class UnknownArtifactResourceError(Exception):
    """The requested artifact resource name is not on the allow-list."""


# (exception type, http status, stable code, sanitized message)
_ERROR_MAP: tuple[tuple[type[Exception], int, str, str], ...] = (
    (InvalidIdentifierError, 400, "invalid_identifier", "Invalid request identifier."),
    (ProjectConfigError, 400, "invalid_project_or_config", "Invalid project or configuration."),
    (PipelineInProgressError, 409, "pipeline_in_progress", "Project state is busy with an active pipeline run."),
    (PipelineExecutionError, 502, "pipeline_failed", "Pipeline execution failed."),
    (SourceUnsupportedError, 400, "source_unsupported", "Source file type or content is not supported."),
    (SourceLimitError, 413, "source_limit_exceeded", "Source exceeds configured size limits."),
    (SourceUnsafeURLError, 400, "source_unsafe_url", "Source URL is not permitted."),
    (SourceIngestionFailedError, 400, "source_ingestion_failed", "Source could not be ingested."),
    (ApplicationServiceError, 400, "application_error", "Request could not be completed."),
    (ProjectNotFoundError, 404, "project_not_found", "Project not found."),
    (ProjectAlreadyExistsError, 409, "project_conflict", "Project already exists."),
    (ProjectPathError, 400, "invalid_project_path", "Invalid project path or identifier."),
    (UnsupportedProjectSchemaError, 409, "unsupported_project_schema", "Project data is from an unsupported schema."),
    (ProjectMalformedError, 500, "project_malformed", "Project data is malformed."),
    (ProjectStoreError, 400, "project_error", "Project request could not be completed."),
    (RunNotFoundError, 404, "run_not_found", "Run not found."),
    (ProjectRunConflictError, 409, "run_conflict", "Project already has a run in progress."),
    (InvalidRunStateTransitionError, 409, "invalid_run_transition", "Run cannot transition to that state."),
    (UnsupportedRunSchemaError, 409, "unsupported_run_schema", "Run data is from an unsupported schema."),
    (RunMalformedError, 500, "run_malformed", "Run data is malformed."),
    (RunServiceError, 400, "run_error", "Run request could not be completed."),
    (ProjectMismatchError, 403, "project_mismatch", "Project id does not match the supplied configuration."),
    (UnknownArtifactResourceError, 404, "artifact_not_found", "Unknown artifact resource."),
)


def _error_body(code: str, message: str) -> dict:
    return {"error": {"code": code, "message": message}}


async def domain_error_handler(_request: Request, exc: Exception) -> JSONResponse:
    for exc_type, status_code, code, message in _ERROR_MAP:
        if isinstance(exc, exc_type):
            return JSONResponse(status_code=status_code, content=_error_body(code, message))
    # Unexpected/unmapped exception: never echo it back to the client.
    return JSONResponse(status_code=500, content=_error_body("internal_error", "An internal error occurred."))


async def validation_error_handler(_request: Request, exc: RequestValidationError) -> JSONResponse:
    """Return a sanitized 422 that never echoes the client-submitted value.

    FastAPI's default handler includes an ``input`` field with the raw,
    rejected value for every error (for example an overlong filesystem path
    or other private data submitted in the request body, query, or path) —
    that field is dropped here, keeping only the non-sensitive location and
    message describing why validation failed.
    """
    details = [
        {"loc": list(error.get("loc", ())), "msg": error.get("msg", ""), "type": error.get("type", "")}
        for error in exc.errors()
    ]
    return JSONResponse(
        status_code=422,
        content={"error": {"code": "validation_error", "message": "Request validation failed.", "details": details}},
    )


DOMAIN_EXCEPTIONS: tuple[type[Exception], ...] = tuple(entry[0] for entry in _ERROR_MAP)
