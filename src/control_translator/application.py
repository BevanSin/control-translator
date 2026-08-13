"""Shared application services for CLI, MCP, and future API adapters."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
import json
import os
from pathlib import Path
import re
import threading
from typing import Callable, Literal
from uuid import NAMESPACE_URL, uuid4, uuid5

from .catalogue import get_catalogue
from .config import load_config, resolve
from .events import EventType, PipelineEvent, Stage
from .mapping import MappingStore, check_oos_staleness, load_oos_records
from .mapping.corrections import load_corrections
from .models.mapping import Decision
from .projects import (
    IngestedSource,
    MalformedSourceError,
    ProjectAlreadyExistsError,
    ProjectNotFoundError,
    ProjectStore,
    SourceIngestionService,
    SourceIngestionError,
    SourceTooLargeError,
    UnsafeSourceURLError,
    UnsupportedSourceError,
)
from .runs import PipelineService, ProjectRunConflictError, RunRecord, RunState
from .runs.lock import ProjectMutationLock

_MAX_RATIONALE_CHARS = 200
_MAX_GUIDANCE_CHARS = 2000
_MAX_ARTIFACT_PREVIEW_BYTES = 128 * 1024
_RUN_WAIT_TIMEOUT_SECONDS = 300
_SENSITIVE_TEXT = re.compile(
    r"(key|token|secret|password|passwd|credential|connection[_-]?string|"
    r"signature|authorization|api[_-]?key|https?://)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class RunSummary:
    framework: str
    controls_total: int
    approved: int
    pending_review: int
    lint_errors: list[str]
    published_to: str | None


@dataclass(frozen=True)
class PendingItem:
    control_id: str
    confidence: float
    policies: list[dict]
    rationale: str


@dataclass(frozen=True)
class ReviewSummary:
    pending: list[PendingItem]
    preview_excluded: list[dict]
    oos_reconsidered: list[dict]


@dataclass(frozen=True)
class MappingMutationResult:
    updated: list[str]
    already_updated: list[str]
    not_found: list[str]


@dataclass(frozen=True)
class GuidanceMutationResult:
    guidance: dict | None
    deleted: list[str]
    affects_future_runs: bool


@dataclass(frozen=True)
class OOSMutationResult:
    added: list[str]
    register_path: str


@dataclass(frozen=True)
class OOSReconsiderationResult:
    removed: list[str]
    not_found: list[str]


class ApplicationServiceError(Exception):
    code = "application_error"


class InvalidIdentifierError(ApplicationServiceError):
    code = "invalid_identifier"


class ProjectConfigError(ApplicationServiceError):
    code = "invalid_project_or_config"


class PipelineExecutionError(ApplicationServiceError):
    code = "pipeline_failed"


class PipelineInProgressError(ApplicationServiceError):
    code = "pipeline_in_progress"


class SourceIngestionFailedError(ApplicationServiceError):
    code = "source_ingestion_failed"


class SourceUnsupportedError(ApplicationServiceError):
    code = "source_unsupported"


class SourceLimitError(ApplicationServiceError):
    code = "source_limit_exceeded"


class SourceUnsafeURLError(ApplicationServiceError):
    code = "source_unsafe_url"


class ControlTranslatorService:
    """Project/config, pipeline, and review/mutation operations for adapters."""

    def __init__(
        self,
        *,
        repo_root: str | Path | None = None,
        project_store: ProjectStore | None = None,
        pipeline_service: PipelineService | None = None,
        source_ingestion_service: SourceIngestionService | None = None,
    ):
        self.repo_root = Path(repo_root or Path(__file__).resolve().parent.parent.parent).resolve()
        self.project_store = project_store or ProjectStore()
        self.pipeline_service = pipeline_service or PipelineService(self.project_store)
        self.source_ingestion_service = source_ingestion_service or SourceIngestionService(self.project_store)

    def ingest_uploaded_source(
        self,
        *,
        config_path: str | None,
        resolution_root: str | Path,
        filename: str,
        payload: bytes,
        content_type: str | None,
    ) -> IngestedSource:
        project_id, _config = self._load_project_config(config_path, resolution_root=resolution_root)
        lock = ProjectMutationLock(self.project_store, project_id)
        try:
            lock.acquire(run_id=uuid4().hex)
            return self.source_ingestion_service.ingest_upload(
                project_id=project_id,
                filename=filename,
                payload=payload,
                content_type=content_type,
            )
        except ProjectRunConflictError as exc:
            raise PipelineInProgressError(
                "Project state is currently being updated by an active run. Retry after it completes."
            ) from exc
        except UnsupportedSourceError as exc:
            raise SourceUnsupportedError("Source upload is not supported.") from exc
        except SourceTooLargeError as exc:
            raise SourceLimitError("Source upload exceeds configured limits.") from exc
        except MalformedSourceError as exc:
            raise SourceIngestionFailedError("Source upload is malformed.") from exc
        except (SourceIngestionError, OSError) as exc:
            raise SourceIngestionFailedError("Source upload could not be stored.") from exc
        finally:
            lock.release()

    def ingest_url_source(
        self,
        *,
        config_path: str | None,
        resolution_root: str | Path,
        url: str,
        timeout_seconds: int,
    ) -> IngestedSource:
        project_id, _config = self._load_project_config(config_path, resolution_root=resolution_root)
        lock = ProjectMutationLock(self.project_store, project_id)
        try:
            lock.acquire(run_id=uuid4().hex)
            return self.source_ingestion_service.ingest_url(
                project_id=project_id,
                url=url,
                timeout_seconds=timeout_seconds,
            )
        except ProjectRunConflictError as exc:
            raise PipelineInProgressError(
                "Project state is currently being updated by an active run. Retry after it completes."
            ) from exc
        except UnsupportedSourceError as exc:
            raise SourceUnsupportedError("URL source is not supported.") from exc
        except SourceTooLargeError as exc:
            raise SourceLimitError("URL source exceeds configured limits.") from exc
        except UnsafeSourceURLError as exc:
            raise SourceUnsafeURLError("URL source destination is not permitted.") from exc
        except (SourceIngestionError, OSError) as exc:
            raise SourceIngestionFailedError("URL source could not be ingested.") from exc
        finally:
            lock.release()

    def run(
        self,
        *,
        config_path: str | None,
        do_distribute: bool,
        resolution_root: str | Path,
        event_sink: Callable[[PipelineEvent], None] | None = None,
    ) -> RunSummary:
        project_id, config = self._load_project_config(config_path, resolution_root=resolution_root)
        handle = self.pipeline_service.start(project_id, config, do_distribute=do_distribute)
        record = self.pipeline_service.wait(project_id, handle.run_id, timeout=_RUN_WAIT_TIMEOUT_SECONDS)
        events = self.pipeline_service.events(project_id, handle.run_id)
        self._ensure_run_succeeded(record, context="run")
        self._replay_events(events, event_sink)

        started = next((e for e in events if e.get("type") == "run.started"), {})
        ingest_done = next(
            (e for e in events if e.get("type") == "stage.completed" and e.get("stage") == "ingest"),
            {},
        )
        completed = next((e for e in reversed(events) if e.get("type") == "run.completed"), {})
        distribute_done = next(
            (e for e in reversed(events) if e.get("type") == "stage.completed" and e.get("stage") == "distribute"),
            {},
        )
        warnings = [
            str(e.get("message", ""))
            for e in events
            if e.get("type") == "run.warning"
            and e.get("summary", {}).get("kind") == "validation"
            and e.get("message")
        ]

        fw_id = started.get("summary", {}).get("framework", config["framework"]["id"])
        fw_version = started.get("summary", {}).get("version", config["framework"]["version"])
        published_to = self._published_target(distribute_done.get("message", ""))

        return RunSummary(
            framework=f"{fw_id} v{fw_version}",
            controls_total=int(ingest_done.get("summary", {}).get("controls", 0)),
            approved=int(completed.get("summary", {}).get("approved", 0)),
            pending_review=int(completed.get("summary", {}).get("pending", 0)),
            lint_errors=warnings,
            published_to=published_to,
        )

    def review(
        self,
        *,
        config_path: str | None,
        resolution_root: str | Path,
        event_sink: Callable[[PipelineEvent], None] | None = None,
    ) -> ReviewSummary:
        project_id, config = self._load_project_config(config_path, resolution_root=resolution_root)
        record = self.pipeline_service.wait(
            project_id,
            self.pipeline_service.start(project_id, config, do_distribute=False).run_id,
            timeout=_RUN_WAIT_TIMEOUT_SECONDS,
        )
        self._ensure_run_succeeded(record, context="review refresh")
        self._replay_events(self.pipeline_service.events(project_id, record.id), event_sink)

        fw = config["framework"]
        mapping = MappingStore(config["mapping"]["store"]).load(fw["id"], fw["version"])
        pending = [
            PendingItem(
                control_id=item.control_id,
                confidence=item.confidence,
                policies=[{"id": p.policy_id, "name": p.display_name} for p in item.policies],
                rationale=self._sanitize_text(item.rationale),
            )
            for item in mapping.pending_review()
        ]

        ccfg = config["catalogue"]
        policies = get_catalogue(ccfg["type"], ccfg.get("source"), ccfg).builtins()
        oos_records = load_oos_records(config["mapping"].get("global_ignore"))
        reconsidered = check_oos_staleness(oos_records, policies)

        preview = self._load_preview_excluded(config)
        return ReviewSummary(pending=pending, preview_excluded=preview, oos_reconsidered=reconsidered)

    def approve_controls(
        self,
        *,
        control_ids: list[str],
        config_path: str | None,
        resolution_root: str | Path,
    ) -> MappingMutationResult:
        return self._update_decisions(
            control_ids=control_ids,
            decision=Decision.INCLUDE,
            config_path=config_path,
            resolution_root=resolution_root,
        )

    def reject_controls(
        self,
        *,
        control_ids: list[str],
        config_path: str | None,
        resolution_root: str | Path,
    ) -> MappingMutationResult:
        return self._update_decisions(
            control_ids=control_ids,
            decision=Decision.IGNORE,
            config_path=config_path,
            resolution_root=resolution_root,
        )

    def add_to_oos_register(
        self,
        *,
        policy_ids: list[str],
        reasons: list[str],
        register: str,
        config_path: str | None,
        resolution_root: str | Path,
    ) -> OOSMutationResult:
        for policy_id in policy_ids:
            self._validate_identifier(policy_id)
        if len(policy_ids) != len(reasons):
            raise InvalidIdentifierError("policy_ids and reasons must contain the same number of items.")

        project_id, config = self._load_project_config(config_path, resolution_root=resolution_root)
        ignore_paths = config["mapping"].get("global_ignore", [])
        if isinstance(ignore_paths, str):
            ignore_paths = [ignore_paths]
        if not ignore_paths:
            raise ProjectConfigError("No global_ignore paths configured for this project.")

        if register == "framework":
            if len(ignore_paths) < 2:
                raise ProjectConfigError(
                    "Framework register requested, but config.global_ignore does not include a framework-specific path."
                )
            selected = ignore_paths[-1]
        elif register == "global":
            selected = ignore_paths[0]
        else:
            raise InvalidIdentifierError("register must be either 'global' or 'framework'.")

        lock = ProjectMutationLock(self.project_store, project_id)
        try:
            lock.acquire(run_id=uuid4().hex)
            existing: list[dict] = []
            if os.path.exists(selected):
                with open(selected, encoding="utf-8") as fh:
                    existing = json.load(fh)

            added: list[str] = []
            for policy_id, reason in zip(policy_ids, reasons):
                existing.append({
                    "policy_id": policy_id,
                    "reason": self._sanitize_text(reason),
                    "oos_date": date.today().isoformat(),
                })
                added.append(policy_id)

            os.makedirs(os.path.dirname(selected) or ".", exist_ok=True)
            with open(selected, "w", encoding="utf-8") as fh:
                json.dump(existing, fh, indent=2, ensure_ascii=False)
        except ProjectRunConflictError as exc:
            raise PipelineInProgressError(
                "Project state is currently being updated by an active run. Retry after it completes."
            ) from exc
        finally:
            lock.release()

        return OOSMutationResult(added=added, register_path=selected)

    def remove_from_oos_register(
        self,
        *,
        policy_ids: list[str],
        config_path: str | None,
        resolution_root: str | Path,
    ) -> OOSReconsiderationResult:
        for policy_id in policy_ids:
            self._validate_identifier(policy_id)

        project_id, config = self._load_project_config(config_path, resolution_root=resolution_root)
        paths = self._oos_register_paths(config)
        lock = ProjectMutationLock(self.project_store, project_id)
        removed: list[str] = []
        try:
            lock.acquire(run_id=uuid4().hex)
            wanted = {self._norm_policy_id(policy_id): policy_id for policy_id in policy_ids}
            for path in paths:
                if not os.path.exists(path):
                    continue
                with open(path, encoding="utf-8") as fh:
                    records = json.load(fh)
                if not isinstance(records, list):
                    continue
                kept: list[object] = []
                changed = False
                for record in records:
                    raw = record if isinstance(record, str) else record.get("policy_id", "")
                    norm = self._norm_policy_id(str(raw))
                    if norm in wanted:
                        if wanted[norm] not in removed:
                            removed.append(wanted[norm])
                        changed = True
                    else:
                        kept.append(record)
                if changed:
                    with open(path, "w", encoding="utf-8") as fh:
                        json.dump(kept, fh, indent=2, ensure_ascii=False)
            not_found = [policy_id for policy_id in policy_ids if policy_id not in removed]
        except ProjectRunConflictError as exc:
            raise PipelineInProgressError(
                "Project state is currently being updated by an active run. Retry after it completes."
            ) from exc
        finally:
            lock.release()

        return OOSReconsiderationResult(removed=removed, not_found=not_found)

    def list_guidance(self, *, config_path: str | None, resolution_root: str | Path) -> dict:
        project_id, config = self._load_project_config(config_path, resolution_root=resolution_root)
        path, affects = self._guidance_path(project_id, config)
        entries = self._load_guidance_entries(path)
        return {"count": len(entries), "items": entries, "affects_future_runs": affects}

    def save_guidance(
        self,
        *,
        guidance_id: str | None,
        control_id: str,
        policy_id: str,
        display_name: str,
        guidance: str,
        source: str,
        provenance: str,
        config_path: str | None,
        resolution_root: str | Path,
    ) -> GuidanceMutationResult:
        self._validate_identifier(control_id)
        self._validate_identifier(policy_id)
        clean_guidance = guidance.strip()
        if not clean_guidance or len(clean_guidance) > _MAX_GUIDANCE_CHARS:
            raise InvalidIdentifierError("guidance must be non-empty and within the configured length limit.")
        if not source.strip() or not provenance.strip():
            raise InvalidIdentifierError("source and provenance are required for local guidance.")

        project_id, config = self._load_project_config(config_path, resolution_root=resolution_root)
        path, affects = self._guidance_path(project_id, config)
        lock = ProjectMutationLock(self.project_store, project_id)
        try:
            lock.acquire(run_id=uuid4().hex)
            entries = self._load_guidance_entries(path)
            now = self._timestamp()
            entry_id = guidance_id.strip() if guidance_id else uuid4().hex
            entry = {
                "id": entry_id,
                "control_id": control_id.strip(),
                "policy_id": policy_id.strip(),
                "display_name": display_name.strip(),
                "include_reasoning": clean_guidance,
                "source": source.strip(),
                "provenance": provenance.strip(),
                "added_date": date.today().isoformat(),
                "updated_at": now,
            }
            replaced = False
            for index, existing in enumerate(entries):
                if existing.get("id") == entry_id:
                    entry["added_date"] = existing.get("added_date", entry["added_date"])
                    entries[index] = entry
                    replaced = True
                    break
            if not replaced:
                entries.append(entry)
            self._save_json_list(path, entries)
        except ProjectRunConflictError as exc:
            raise PipelineInProgressError(
                "Project state is currently being updated by an active run. Retry after it completes."
            ) from exc
        finally:
            lock.release()

        return GuidanceMutationResult(guidance=entry, deleted=[], affects_future_runs=affects)

    def delete_guidance(
        self,
        *,
        guidance_ids: list[str],
        config_path: str | None,
        resolution_root: str | Path,
    ) -> GuidanceMutationResult:
        for guidance_id in guidance_ids:
            self._validate_identifier(guidance_id)
        project_id, config = self._load_project_config(config_path, resolution_root=resolution_root)
        path, affects = self._guidance_path(project_id, config)
        lock = ProjectMutationLock(self.project_store, project_id)
        deleted: list[str] = []
        try:
            lock.acquire(run_id=uuid4().hex)
            entries = self._load_guidance_entries(path)
            wanted = set(guidance_ids)
            kept = []
            for entry in entries:
                entry_id = str(entry.get("id", ""))
                if entry_id in wanted:
                    deleted.append(entry_id)
                else:
                    kept.append(entry)
            if deleted:
                self._save_json_list(path, kept)
        except ProjectRunConflictError as exc:
            raise PipelineInProgressError(
                "Project state is currently being updated by an active run. Retry after it completes."
            ) from exc
        finally:
            lock.release()
        return GuidanceMutationResult(guidance=None, deleted=deleted, affects_future_runs=affects)

    def mapping_details(self, *, control_id: str, config_path: str | None,
                        resolution_root: str | Path) -> dict:
        self._validate_identifier(control_id)
        _, config = self._load_project_config(config_path, resolution_root=resolution_root)
        fw = config["framework"]
        mapping = MappingStore(config["mapping"]["store"]).load(fw["id"], fw["version"])
        item = mapping.mappings.get(control_id)
        if item is None:
            raise InvalidIdentifierError(f"Control {control_id} not found in mapping store.")
        payload = item.to_dict()
        payload["rationale"] = self._sanitize_text(payload.get("rationale", ""))
        return payload

    def search_controls(self, *, query: str, status: str | None, limit: int,
                        config_path: str | None, resolution_root: str | Path) -> dict:
        q = query.strip().lower()
        if not q:
            raise InvalidIdentifierError("query must be a non-empty string.")
        if limit < 1:
            raise InvalidIdentifierError("limit must be at least 1.")
        if status and status not in {"include", "ignore", "review"}:
            raise InvalidIdentifierError("status must be one of include, ignore, review.")

        _, config = self._load_project_config(config_path, resolution_root=resolution_root)
        fw = config["framework"]
        mapping = MappingStore(config["mapping"]["store"]).load(fw["id"], fw["version"])

        results: list[dict] = []
        for control_key, item in mapping.mappings.items():
            if status and item.decision.value != status:
                continue
            if q in control_key.lower() or q in item.rationale.lower() or any(
                q in (policy.display_name or "").lower() for policy in item.policies
            ):
                results.append(
                    {
                        "control_id": control_key,
                        "decision": item.decision.value,
                        "confidence": item.confidence,
                        "policies": [p.display_name or p.policy_id for p in item.policies],
                        "rationale": self._sanitize_text(item.rationale),
                    }
                )
                if len(results) >= limit:
                    break

        return {"count": len(results), "results": results}

    def list_mappings(
        self,
        *,
        query: str,
        status: str | None,
        page: int,
        page_size: int,
        config_path: str | None,
        resolution_root: str | Path,
    ) -> dict:
        q = query.strip().lower()
        if page < 1 or page_size < 1 or page_size > 100:
            raise InvalidIdentifierError("page and page_size must be within the supported range.")
        if status and status not in {"include", "ignore", "review"}:
            raise InvalidIdentifierError("status must be one of include, ignore, review.")

        _, config = self._load_project_config(config_path, resolution_root=resolution_root)
        fw = config["framework"]
        mapping = MappingStore(config["mapping"]["store"]).load(fw["id"], fw["version"])
        all_items: list[dict] = []
        for control_key, item in sorted(mapping.mappings.items()):
            if status and item.decision.value != status:
                continue
            haystack = " ".join(
                [control_key, item.rationale, item.decision.value]
                + [policy.policy_id for policy in item.policies]
                + [policy.display_name for policy in item.policies]
            ).lower()
            if q and q not in haystack:
                continue
            all_items.append({
                "control_id": control_key,
                "decision": item.decision.value,
                "confidence": item.confidence,
                "source": item.source,
                "policies": [{"id": p.policy_id, "name": p.display_name} for p in item.policies],
                "rationale": self._sanitize_text(item.rationale),
            })

        start = (page - 1) * page_size
        end = start + page_size
        return {
            "count": len(all_items[start:end]),
            "total": len(all_items),
            "page": page,
            "page_size": page_size,
            "items": all_items[start:end],
        }

    def status(self, *, config_path: str | None, resolution_root: str | Path) -> dict:
        project_id, config = self._load_project_config(config_path, resolution_root=resolution_root)
        fw = config["framework"]
        mapping_path = config["mapping"]["store"]
        mapping_store = MappingStore(mapping_path)

        info: dict = {
            "framework": f"{fw['id']} v{fw['version']}",
            "display_name": fw.get("display_name", fw["id"]),
            "mapping_store": mapping_path,
            "store_exists": os.path.exists(mapping_path),
        }

        if os.path.exists(mapping_path):
            mapping = mapping_store.load(fw["id"], fw["version"])
            info["total_mappings"] = len(mapping.mappings)
            info["approved"] = len(mapping.approved())
            info["pending_review"] = len(mapping.pending_review())
            info["ignored"] = sum(1 for m in mapping.mappings.values() if m.decision.value == "ignore")

        bundle_dir = self._bundle_dir(config)
        info["latest_bundle"] = str(bundle_dir) if bundle_dir else None
        records = self.pipeline_service.list(project_id)
        if records:
            info["last_run"] = records[-1].to_dict()

        return info

    def pending_review(self, *, config_path: str | None, resolution_root: str | Path) -> dict:
        _, config = self._load_project_config(config_path, resolution_root=resolution_root)
        fw = config["framework"]
        mapping = MappingStore(config["mapping"]["store"]).load(fw["id"], fw["version"])
        items = [
            {
                "control_id": item.control_id,
                "confidence": item.confidence,
                "policies": [{"id": p.policy_id, "name": p.display_name} for p in item.policies],
                "rationale": self._sanitize_text(item.rationale),
            }
            for item in mapping.pending_review()
        ]
        return {"count": len(items), "items": items}

    def bundle_json_resource(self, *, config_path: str | None, resolution_root: str | Path,
                             filename: str) -> dict:
        _, config = self._load_project_config(config_path, resolution_root=resolution_root)
        bundle_dir = self._bundle_dir(config)
        if bundle_dir is None:
            raise ProjectConfigError("No bundle found. Run the pipeline first.")
        path = bundle_dir / filename
        if not path.exists():
            return {"count": 0, "items": []}
        items = json.loads(path.read_text(encoding="utf-8"))
        return {"count": len(items), "items": items}

    def bundle_summary(self, *, config_path: str | None, resolution_root: str | Path) -> dict:
        _, config = self._load_project_config(config_path, resolution_root=resolution_root)
        bundle_dir = self._bundle_dir(config)
        if bundle_dir is None:
            raise ProjectConfigError("No bundle found. Run the pipeline first.")

        files = [f.name for f in bundle_dir.iterdir() if f.is_file()]
        summary: dict = {"bundle_path": str(bundle_dir), "files": files}
        policy_set_path = bundle_dir / "policySet.json"
        if policy_set_path.exists():
            policy_set = json.loads(policy_set_path.read_text(encoding="utf-8"))
            props = policy_set.get("properties", {})
            definitions = props.get("policyDefinitions", [])
            groups = props.get("policyDefinitionGroups", [])
            summary["policy_definitions"] = len(definitions)
            summary["control_groups"] = len(groups)
            summary["multi_control_policies"] = sum(
                1 for definition in definitions if len(definition.get("groupNames", [])) > 1
            )
            summary["parameters"] = len(props.get("parameters", {}))
        return summary

    def artifact_inventory(self, *, config_path: str | None, resolution_root: str | Path) -> dict:
        _, config = self._load_project_config(config_path, resolution_root=resolution_root)
        bundle_dir = self._bundle_dir(config)
        if bundle_dir is None:
            return {"count": 0, "items": []}
        items: list[dict] = []
        for name, content_type in self._artifact_allow_list().items():
            path = self._artifact_path(bundle_dir, name)
            if path and path.exists():
                items.append({
                    "name": name,
                    "size_bytes": path.stat().st_size,
                    "content_type": content_type,
                    "previewable": self._is_previewable_artifact(name, path),
                })
        return {"count": len(items), "items": items}

    def artifact_preview(self, *, config_path: str | None, resolution_root: str | Path,
                         name: str) -> dict:
        path, content_type = self._load_artifact_file(
            config_path=config_path, resolution_root=resolution_root, name=name,
        )
        if not self._is_previewable_artifact(name, path):
            raise InvalidIdentifierError("Artifact is not supported for safe preview.")
        payload = path.read_bytes()[:_MAX_ARTIFACT_PREVIEW_BYTES + 1]
        truncated = len(payload) > _MAX_ARTIFACT_PREVIEW_BYTES
        text = payload[:_MAX_ARTIFACT_PREVIEW_BYTES].decode("utf-8", errors="replace")
        parsed: object | None = None
        if content_type == "application/json":
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                parsed = None
        return {
            "name": name,
            "content_type": content_type,
            "text": text,
            "parsed_json": parsed,
            "truncated": truncated,
        }

    def artifact_download(self, *, config_path: str | None, resolution_root: str | Path,
                          name: str) -> tuple[Path, str]:
        return self._load_artifact_file(config_path=config_path, resolution_root=resolution_root, name=name)

    def run_history(self, *, config_path: str | None, resolution_root: str | Path) -> dict:
        project_id, _config = self._load_project_config(config_path, resolution_root=resolution_root)
        records = self.pipeline_service.list(project_id)
        runs = [record.to_dict() for record in records[-20:]]
        return {"count": len(runs), "runs": runs}

    def start_run(self, *, config_path: str | None, resolution_root: str | Path,
                  do_distribute: bool = True) -> RunRecord:
        """Start a run and return its initial record without waiting for completion.

        Unlike ``run()``, this never blocks for the run to finish — callers (for
        example the local API) poll ``pipeline_service.get``/``events`` instead.
        """
        project_id, config = self._load_project_config(config_path, resolution_root=resolution_root)
        handle = self.pipeline_service.start(project_id, config, do_distribute=do_distribute)
        return self.pipeline_service.get(project_id, handle.run_id)

    def _update_decisions(
        self,
        *,
        control_ids: list[str],
        decision: Decision,
        config_path: str | None,
        resolution_root: str | Path,
    ) -> MappingMutationResult:
        for control_id in control_ids:
            self._validate_identifier(control_id)

        project_id, config = self._load_project_config(config_path, resolution_root=resolution_root)
        fw = config["framework"]
        lock = ProjectMutationLock(self.project_store, project_id)
        try:
            lock.acquire(run_id=uuid4().hex)
            store = MappingStore(config["mapping"]["store"])
            mapping = store.load(fw["id"], fw["version"])

            updated: list[str] = []
            already_updated: list[str] = []
            not_found: list[str] = []

            for control_id in control_ids:
                item = mapping.mappings.get(control_id)
                if item is None:
                    not_found.append(control_id)
                    continue
                if item.decision is decision:
                    already_updated.append(control_id)
                    continue
                item.decision = decision
                item.source = "human"
                updated.append(control_id)

            if updated:
                store.save(mapping)
        except ProjectRunConflictError as exc:
            raise PipelineInProgressError(
                "Project state is currently being updated by an active run. Retry after it completes."
            ) from exc
        finally:
            lock.release()

        return MappingMutationResult(updated=updated, already_updated=already_updated, not_found=not_found)

    @staticmethod
    def _ensure_run_succeeded(record: RunRecord, *, context: str) -> None:
        if record.state is RunState.SUCCEEDED:
            return
        if record.state is RunState.FAILED:
            detail = f"{record.error_type}: {record.error_message}" if record.error_type else "unknown error"
            raise PipelineExecutionError(f"Pipeline {context} failed ({detail}).")
        if record.state is RunState.CANCELLED:
            raise PipelineExecutionError(f"Pipeline {context} was cancelled before completion.")
        raise PipelineInProgressError(
            f"Pipeline {context} is still {record.state.value} after waiting {_RUN_WAIT_TIMEOUT_SECONDS}s."
        )

    @staticmethod
    def _replay_events(events: list[dict], event_sink: Callable[[PipelineEvent], None] | None) -> None:
        if event_sink is None:
            return
        for event in events:
            event_type = event.get("type")
            if not isinstance(event_type, str):
                continue
            try:
                parsed_type = EventType(event_type)
            except ValueError:
                continue
            stage_value = event.get("stage")
            stage = None
            if isinstance(stage_value, str):
                try:
                    stage = Stage(stage_value)
                except ValueError:
                    stage = None
            event_sink(
                PipelineEvent(
                    type=parsed_type,
                    run_id=str(event.get("run_id", "")),
                    sequence=int(event.get("sequence", 0)),
                    timestamp=str(event.get("timestamp", "")),
                    stage=stage,
                    message=str(event.get("message", "")),
                    summary=event.get("summary", {}) if isinstance(event.get("summary"), dict) else {},
                )
            )

    def project_id_for_config(
        self,
        config_path: str | None,
        *,
        resolution_root: str | Path,
        default_config_relative: str = "config/nzism-azure.json",
    ) -> str:
        """Deterministically derive the project id a config file resolves to.

        Adapters (for example the local API) use this to confirm a caller-supplied
        project id actually matches the config it is about to operate on, before
        performing any read or mutation — never trusting the path parameter alone.
        """
        target = self._resolve_config_path(
            config_path, resolution_root=resolution_root,
            default_config_relative=default_config_relative,
        )
        return str(uuid5(NAMESPACE_URL, str(target)))

    @staticmethod
    def _resolve_config_path(
        config_path: str | None,
        *,
        resolution_root: str | Path,
        default_config_relative: str = "config/nzism-azure.json",
    ) -> Path:
        root = Path(resolution_root).resolve()
        target = Path(config_path) if config_path else Path(default_config_relative)
        if not target.is_absolute():
            target = root / target
        target = target.resolve()
        if not target.exists():
            raise ProjectConfigError(f"Config file does not exist: {target}")
        return target

    def _load_project_config(
        self,
        config_path: str | None,
        *,
        resolution_root: str | Path,
        default_config_relative: str = "config/nzism-azure.json",
    ) -> tuple[str, dict]:
        root = Path(resolution_root).resolve()
        target = self._resolve_config_path(
            config_path, resolution_root=resolution_root,
            default_config_relative=default_config_relative,
        )
        try:
            loaded = load_config(str(target))
            config = resolve(loaded, str(root))
        except (OSError, ValueError, json.JSONDecodeError, SystemExit) as exc:
            raise ProjectConfigError(f"Failed to load config {target}: {exc}") from exc

        project_id = str(uuid5(NAMESPACE_URL, str(target)))
        try:
            self.project_store.load(project_id)
        except ProjectNotFoundError:
            try:
                self.project_store.create(name=target.stem, project_id=project_id)
            except ProjectAlreadyExistsError:
                self.project_store.load(project_id)
        if not config["mapping"].get("corrections"):
            config["mapping"]["corrections"] = str(
                self.project_store.resolve_path(project_id, "guidance/guidance.json")
            )
        return project_id, config

    @staticmethod
    def _published_target(message: str) -> str | None:
        if not message:
            return None
        if "→" in message:
            return message.split("→", 1)[1].strip() or None
        if "->" in message:
            return message.split("->", 1)[1].strip() or None
        return None

    @staticmethod
    def _sanitize_text(text: str) -> str:
        clean = text.strip()
        if _SENSITIVE_TEXT.search(clean):
            return "[redacted]"
        return clean[:_MAX_RATIONALE_CHARS]

    @staticmethod
    def _validate_identifier(identifier: str) -> None:
        if not isinstance(identifier, str) or not identifier.strip():
            raise InvalidIdentifierError("Identifier values must be non-empty strings.")

    def _load_preview_excluded(self, config: dict) -> list[dict]:
        bundle = self._bundle_dir(config)
        if bundle is None:
            return []
        path = bundle / "out-of-scope.json"
        if not path.exists():
            return []
        data = json.loads(path.read_text(encoding="utf-8"))
        return [item for item in data if isinstance(item, dict) and item.get("source") == "auto-preview"]

    @staticmethod
    def _oos_register_paths(config: dict) -> list[str]:
        paths = config["mapping"].get("global_ignore", [])
        if isinstance(paths, str):
            paths = [paths]
        if not paths:
            raise ProjectConfigError("No global_ignore paths configured for this project.")
        return [str(path) for path in paths]

    def _guidance_path(self, project_id: str, config: dict) -> tuple[str, bool]:
        configured = config["mapping"].get("corrections")
        if configured:
            selected = configured[-1] if isinstance(configured, list) else configured
            return str(selected), True
        return str(self.project_store.resolve_path(project_id, "guidance/guidance.json")), False

    @staticmethod
    def _load_guidance_entries(path: str) -> list[dict]:
        entries = load_corrections(path)
        normalized: list[dict] = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            item = dict(entry)
            item.setdefault("id", uuid5(NAMESPACE_URL, json.dumps(item, sort_keys=True)).hex)
            item.setdefault("include_reasoning", item.get("guidance", ""))
            normalized.append(item)
        return normalized

    @staticmethod
    def _save_json_list(path: str, entries: list[dict]) -> None:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(entries, fh, indent=2, ensure_ascii=False)

    @staticmethod
    def _timestamp() -> str:
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    @staticmethod
    def _norm_policy_id(policy_id: str) -> str:
        return policy_id.rstrip("/").split("/")[-1].lower()

    @staticmethod
    def _artifact_allow_list() -> dict[str, str]:
        return {
            "policySet.json": "application/json",
            "assignment.json": "application/json",
            "main.bicep": "text/plain; charset=utf-8",
            "deploy.sh": "text/x-shellscript; charset=utf-8",
            "out-of-scope.json": "application/json",
            "oos-candidates.json": "application/json",
            "oos-reconsidered.json": "application/json",
        }

    @staticmethod
    def _artifact_path(bundle_dir: Path, name: str) -> Path | None:
        if name not in ControlTranslatorService._artifact_allow_list() or "/" in name or "\\" in name:
            return None
        path = (bundle_dir / name).resolve()
        try:
            path.relative_to(bundle_dir.resolve())
        except ValueError:
            return None
        if path.is_symlink():
            return None
        return path

    @staticmethod
    def _is_previewable_artifact(name: str, path: Path) -> bool:
        if name not in {"policySet.json", "assignment.json", "main.bicep", "out-of-scope.json",
                        "oos-candidates.json", "oos-reconsidered.json"}:
            return False
        return path.stat().st_size <= (_MAX_ARTIFACT_PREVIEW_BYTES * 4)

    def _load_artifact_file(self, *, config_path: str | None, resolution_root: str | Path,
                            name: str) -> tuple[Path, str]:
        _, config = self._load_project_config(config_path, resolution_root=resolution_root)
        content_type = self._artifact_allow_list().get(name)
        if not content_type:
            raise InvalidIdentifierError("Artifact name is not allowed.")
        bundle_dir = self._bundle_dir(config)
        if bundle_dir is None:
            raise ProjectConfigError("No bundle found. Run the pipeline first.")
        path = self._artifact_path(bundle_dir, name)
        if path is None or not path.exists() or not path.is_file():
            raise InvalidIdentifierError("Artifact name is not allowed.")
        return path, content_type

    @staticmethod
    def _bundle_dir(config: dict) -> Path | None:
        out_dir = Path(config.get("out_dir", "out"))
        framework = config["framework"]
        bundle = out_dir / f"{framework['id']}-{framework['version']}"
        return bundle if bundle.exists() else None


_DEFAULT_SERVICE: ControlTranslatorService | None = None
_SERVICE_LOCK = threading.Lock()


def get_application_service() -> ControlTranslatorService:
    global _DEFAULT_SERVICE
    if _DEFAULT_SERVICE is None:
        with _SERVICE_LOCK:
            if _DEFAULT_SERVICE is None:
                _DEFAULT_SERVICE = ControlTranslatorService()
    return _DEFAULT_SERVICE


def to_dict(dataclass_value: object) -> dict:
    if not hasattr(dataclass_value, "__dataclass_fields__"):
        raise TypeError("Expected dataclass instance.")
    return asdict(dataclass_value)
