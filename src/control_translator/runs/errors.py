"""Typed errors for the run service."""
from __future__ import annotations


class RunServiceError(Exception):
    """Base error for pipeline run service operations."""


class RunNotFoundError(RunServiceError):
    """The requested run does not exist for this project."""


class RunMalformedError(RunServiceError):
    """Run metadata is missing or not valid run metadata."""


class UnsupportedRunSchemaError(RunServiceError):
    """Run metadata uses a schema version this release cannot read."""


class ProjectRunConflictError(RunServiceError):
    """Another run already holds the project's mutation lock."""


class InvalidRunStateTransitionError(RunServiceError):
    """A run state transition was attempted that is not valid from its current state."""
