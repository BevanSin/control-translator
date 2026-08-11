"""Local, filesystem-backed project workspace storage."""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import tempfile
from uuid import UUID, uuid4


PROJECT_SCHEMA_VERSION = 1
WORKSPACE_DIRECTORIES = ("source", "config", "mappings", "guidance", "runs", "artifacts")


class ProjectStoreError(Exception):
    """Base error for local project workspace operations."""


class ProjectNotFoundError(ProjectStoreError):
    """The requested project does not exist."""


class ProjectAlreadyExistsError(ProjectStoreError):
    """A project already uses the requested identifier."""


class ProjectMalformedError(ProjectStoreError):
    """Project metadata is missing or not valid project metadata."""


class UnsupportedProjectSchemaError(ProjectStoreError):
    """Project metadata uses a schema version this release cannot read."""


class ProjectPathError(ProjectStoreError):
    """A project identifier or path is outside its project workspace."""


@dataclass(frozen=True)
class Project:
    """Schema-versioned metadata for one isolated local project."""

    id: str
    name: str
    created_at: str
    updated_at: str
    schema_version: int = PROJECT_SCHEMA_VERSION

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "id": self.id,
            "name": self.name,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: object) -> "Project":
        if not isinstance(data, dict):
            raise ProjectMalformedError("Project metadata must be a JSON object.")
        version = data.get("schema_version")
        if not isinstance(version, int) or isinstance(version, bool):
            raise ProjectMalformedError("Project metadata has no integer schema_version.")
        if version != PROJECT_SCHEMA_VERSION:
            raise UnsupportedProjectSchemaError(
                f"Project schema version {version} is unsupported "
                f"(expected {PROJECT_SCHEMA_VERSION})."
            )
        project_id = data.get("id")
        name = data.get("name")
        created_at = data.get("created_at")
        updated_at = data.get("updated_at")
        if not all(isinstance(value, str) and value for value in (
            project_id, name, created_at, updated_at
        )):
            raise ProjectMalformedError("Project metadata has missing or invalid fields.")
        _validate_project_id(project_id)
        return cls(project_id, name, created_at, updated_at, version)


def default_data_root() -> Path:
    """Return the platform-appropriate user data location for the application."""
    configured = os.environ.get("CONTROL_TRANSLATOR_DATA_ROOT")
    if configured:
        return Path(configured).expanduser()
    if os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "control-translator"
    if sys_platform() == "darwin":
        return Path.home() / "Library" / "Application Support" / "control-translator"
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "control-translator"


def sys_platform() -> str:
    """Keep the platform dependency small and easy to test."""
    import sys
    return sys.platform


class ProjectStore:
    """Manage projects beneath one application-controlled data root."""

    def __init__(self, data_root: str | Path | None = None):
        self.data_root = Path(data_root or default_data_root()).expanduser().resolve()

    def create(self, name: str, project_id: str | None = None) -> Project:
        if not isinstance(name, str) or not name.strip():
            raise ProjectMalformedError("Project name must be a non-empty string.")
        project_id = project_id or str(uuid4())
        _validate_project_id(project_id)
        path = self._project_path(project_id)
        if path.exists() or path.is_symlink():
            raise ProjectAlreadyExistsError(f"Project {project_id} already exists.")
        self._ensure_root()
        try:
            path.mkdir(mode=0o700)
            for directory in WORKSPACE_DIRECTORIES:
                (path / directory).mkdir(mode=0o700)
            now = _timestamp()
            project = Project(project_id, name.strip(), now, now)
            self._write_metadata(path, project)
            return project
        except Exception:
            if path.exists() and not path.is_symlink():
                shutil.rmtree(path)
            raise

    def list(self) -> list[Project]:
        if not self.data_root.exists():
            return []
        projects: list[Project] = []
        for path in sorted(self.data_root.iterdir()):
            if not path.is_dir() or path.is_symlink():
                raise ProjectMalformedError(f"Invalid project entry: {path.name}.")
            projects.append(self.load(path.name))
        return projects

    def load(self, project_id: str) -> Project:
        path = self._existing_project_path(project_id)
        metadata = path / "project.json"
        try:
            with metadata.open(encoding="utf-8") as file:
                project = Project.from_dict(json.load(file))
        except FileNotFoundError as exc:
            raise ProjectMalformedError(f"Project {project_id} has no metadata.") from exc
        except json.JSONDecodeError as exc:
            raise ProjectMalformedError(f"Project {project_id} has invalid JSON metadata.") from exc
        if project.id != project_id:
            raise ProjectMalformedError(
                f"Project {project_id} metadata has a different project identifier."
            )
        return project

    def update(self, project_id: str, *, name: str | None = None) -> Project:
        project = self.load(project_id)
        if name is None:
            return project
        if not isinstance(name, str) or not name.strip():
            raise ProjectMalformedError("Project name must be a non-empty string.")
        updated = replace(project, name=name.strip(), updated_at=_timestamp())
        self._write_metadata(self._existing_project_path(project_id), updated)
        return updated

    def delete(self, project_id: str) -> None:
        path = self._existing_project_path(project_id)
        shutil.rmtree(path)

    def resolve_path(self, project_id: str, relative_path: str | Path) -> Path:
        root = self._existing_project_path(project_id)
        candidate = Path(relative_path)
        if candidate.is_absolute():
            raise ProjectPathError("Project paths must be relative.")
        resolved = (root / candidate).resolve()
        try:
            resolved.relative_to(root)
        except ValueError as exc:
            raise ProjectPathError("Project path escapes its workspace.") from exc
        return resolved

    def _ensure_root(self) -> None:
        self.data_root.mkdir(parents=True, exist_ok=True, mode=0o700)

    def _project_path(self, project_id: str) -> Path:
        _validate_project_id(project_id)
        path = self.data_root / project_id
        try:
            path.resolve().relative_to(self.data_root)
        except ValueError as exc:
            raise ProjectPathError("Project identifier escapes the data root.") from exc
        return path

    def _existing_project_path(self, project_id: str) -> Path:
        path = self._project_path(project_id)
        if not path.exists():
            raise ProjectNotFoundError(f"Project {project_id} does not exist.")
        if not path.is_dir() or path.is_symlink():
            raise ProjectMalformedError(f"Project {project_id} is not a directory.")
        return path

    def _write_metadata(self, project_path: Path, project: Project) -> None:
        target = project_path / "project.json"
        fd, temporary = tempfile.mkstemp(prefix=".project-", suffix=".tmp", dir=project_path)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as file:
                json.dump(project.to_dict(), file, indent=2, ensure_ascii=False)
                file.write("\n")
                file.flush()
                os.fsync(file.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, target)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _validate_project_id(project_id: object) -> None:
    if not isinstance(project_id, str):
        raise ProjectPathError("Project identifier must be a UUID string.")
    try:
        parsed = UUID(project_id)
    except (ValueError, AttributeError) as exc:
        raise ProjectPathError("Project identifier must be a UUID string.") from exc
    if str(parsed) != project_id.lower():
        raise ProjectPathError("Project identifier must be a canonical UUID string.")
