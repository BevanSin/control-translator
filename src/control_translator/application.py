"""Shared application services for CLI, MCP, and future API adapters."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
import json
import os
from pathlib import Path
import re
import threading
from typing import Literal
from uuid import NAMESPACE_URL, uuid5

from .catalogue import get_catalogue
from .config import load_config, resolve
from .mapping import MappingStore, check_oos_staleness, load_oos_records
from .models.mapping import Decision
from .projects import ProjectAlreadyExistsError, ProjectNotFoundError, ProjectStore
from .runs import PipelineService, RunState

_MAX_RATIONALE_CHARS = 200
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
class OOSMutationResult:
    added: list[str]
    register_path: str


class ApplicationServiceError(Exception):
    code = "application_error"


class InvalidIdentifierError(ApplicationServiceError):
    code = "invalid_identifier"


class ProjectConfigError(ApplicationServiceError):
    code = "invalid_project_or_config"


class PipelineExecutionError(ApplicationServiceError):
    code = "pipeline_failed"


class ControlTranslatorService:
    """Project/config, pipeline, and review/mutation operations for adapters."""

    def __init__(
        self,
        *,
        repo_root: str | Path | None = None,
        project_store: ProjectStore | None = None,
        pipeline_service: PipelineService | None = None,
    ):
        self.repo_root = Path(repo_root or Path(__file__).resolve().parent.parent.parent).resolve()
        self.project_store = project_store or ProjectStore()
        self.pipeline_service = pipeline_service or PipelineService(self.project_store)

    def run(self, *, config_path: str | None, do_distribute: bool, resolution_root: str | Path) -> RunSummary:
        project_id, config = self._load_project_config(config_path, resolution_root=resolution_root)
        handle = self.pipeline_service.start(project_id, config, do_distribute=do_distribute)
        record = self.pipeline_service.wait(project_id, handle.run_id, timeout=_RUN_WAIT_TIMEOUT_SECONDS)
        events = self.pipeline_service.events(project_id, handle.run_id)

        if record.state is RunState.FAILED:
            detail = f"{record.error_type}: {record.error_message}" if record.error_type else "unknown error"
            raise PipelineExecutionError(f"Pipeline run failed ({detail}).")
        if record.state is RunState.CANCELLED:
            raise PipelineExecutionError("Pipeline run was cancelled before completion.")

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

    def review(self, *, config_path: str | None, resolution_root: str | Path) -> ReviewSummary:
        project_id, config = self._load_project_config(config_path, resolution_root=resolution_root)
        record = self.pipeline_service.wait(
            project_id,
            self.pipeline_service.start(project_id, config, do_distribute=False).run_id,
            timeout=_RUN_WAIT_TIMEOUT_SECONDS,
        )
        if record.state is RunState.FAILED:
            detail = f"{record.error_type}: {record.error_message}" if record.error_type else "unknown error"
            raise PipelineExecutionError(f"Pipeline review refresh failed ({detail}).")
        if record.state is RunState.CANCELLED:
            raise PipelineExecutionError("Pipeline review refresh was cancelled before completion.")

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

        _, config = self._load_project_config(config_path, resolution_root=resolution_root)
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

        return OOSMutationResult(added=added, register_path=selected)

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

    def status(self, *, config_path: str | None, resolution_root: str | Path) -> dict:
        _, config = self._load_project_config(config_path, resolution_root=resolution_root)
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

        if bundle_dir:
            log_path = bundle_dir / "run-log.jsonl"
            if log_path.exists():
                lines = log_path.read_text(encoding="utf-8").strip().splitlines()
                if lines:
                    info["last_run"] = json.loads(lines[-1])

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

    def run_history(self, *, config_path: str | None, resolution_root: str | Path) -> dict:
        _, config = self._load_project_config(config_path, resolution_root=resolution_root)
        bundle_dir = self._bundle_dir(config)
        if bundle_dir is None:
            raise ProjectConfigError("No bundle found.")
        log_path = bundle_dir / "run-log.jsonl"
        if not log_path.exists():
            return {"runs": []}
        lines = log_path.read_text(encoding="utf-8").strip().splitlines()
        runs = [json.loads(line) for line in lines[-20:]]
        return {"count": len(runs), "runs": runs}

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

        _, config = self._load_project_config(config_path, resolution_root=resolution_root)
        fw = config["framework"]
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

        return MappingMutationResult(updated=updated, already_updated=already_updated, not_found=not_found)

    def _load_project_config(
        self,
        config_path: str | None,
        *,
        resolution_root: str | Path,
        default_config_relative: str = "config/nzism-azure.json",
    ) -> tuple[str, dict]:
        root = Path(resolution_root).resolve()
        target = Path(config_path) if config_path else Path(default_config_relative)
        if not target.is_absolute():
            target = root / target
        target = target.resolve()

        if not target.exists():
            raise ProjectConfigError(f"Config file does not exist: {target}")
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
