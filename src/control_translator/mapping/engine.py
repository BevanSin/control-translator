"""Mapping engine: orchestrates carry-forward, ignore, proposal, and the review gate.

Per control:
  - if an INCLUDE/IGNORE decision already exists in the store -> carry it forward
  - otherwise ask the Mapper for candidate policies and:
      * auto_approve + confidence >= threshold -> INCLUDE
      * else                                   -> REVIEW (awaits authority sign-off)

New controls are processed in parallel (ThreadPoolExecutor, bounded by `concurrency`)
so the agentic mapper's LLM calls overlap rather than serialise.
"""
from __future__ import annotations

import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

from .base import Mapper
from .store import _norm_id
from ..models import Catalog, Decision, ControlMapping, PolicyRef, MappingSet
from ..catalogue import PolicyDefinition

_ID_W  = 20
_DEC_W = 14


def _progress(n: int, total: int, control_id: str, label: str,
              detail: str = "", lock: threading.Lock | None = None) -> None:
    pct    = int(n / total * 100) if total else 0
    filled = int(20 * n / total)  if total else 0
    bar    = "█" * filled + "░" * (20 - filled)
    suffix = f"  {detail}" if detail else ""
    line   = (f"\r[{n:>5}/{total}] [{bar}] {pct:>3}%  "
              f"{control_id:<{_ID_W}}  {label:<{_DEC_W}}{suffix}")
    with (lock or threading.Lock()):
        print(line, end="", file=sys.stderr, flush=True)


class MappingEngine:
    def __init__(self, mapper: Mapper, *, global_ignore: set[str] | None = None,
                 auto_approve: bool = False, confidence_threshold: float = 0.75,
                 oos_context: list[dict] | None = None,
                 corrections: list[dict] | None = None,
                 preview_filter: bool = True,
                 exclude_patterns: list[str] | None = None,
                 verbose: bool = True,
                 concurrency: int = 5,
                 checkpoint_every: int = 25):
        self.mapper               = mapper
        self.global_ignore        = global_ignore or set()
        self.auto_approve         = auto_approve
        self.confidence_threshold = confidence_threshold
        self.oos_context          = oos_context or []
        self.corrections          = corrections or []
        self.preview_filter       = preview_filter
        self.exclude_patterns     = [p.lower() for p in (exclude_patterns or [])]
        self.verbose              = verbose
        self.concurrency          = max(1, concurrency)
        self.checkpoint_every     = max(1, checkpoint_every)

    def run(self, catalog: Catalog, policies: list[PolicyDefinition],
            existing: MappingSet, *,
            checkpoint_fn=None) -> MappingSet:
        """Run the mapping pipeline.

        checkpoint_fn — optional callable(MappingSet) invoked every
        `checkpoint_every` controls so progress survives a kill/crash.
        """

        result = MappingSet(framework_id=existing.framework_id or catalog.uuid,
                            version=existing.version or catalog.version)

        # ── candidate pool ────────────────────────────────────────────────────
        candidates = [p for p in policies if _norm_id(p.id) not in self.global_ignore]

        preview_seen: dict[str, dict] = {}
        if self.preview_filter:
            live, preview = [], []
            for p in candidates:
                (preview if p.display_name.strip().lower().startswith("[preview]")
                 else live).append(p)
            for p in preview:
                pid = _norm_id(p.id)
                if pid not in preview_seen:
                    preview_seen[pid] = {
                        "policy_id": p.id, "display_name": p.display_name,
                        "reason": "Preview policy — excluded until generally available.",
                        "source": "auto-preview",
                    }
            candidates = live

        # Pattern-based filter — config-driven substring exclusions.
        # Checks both display_name and description so patterns like "gcpol"
        # (which appear in policy descriptions as prerequisite references)
        # are caught even when not in the display name.
        pattern_seen: dict[str, dict] = {}
        if self.exclude_patterns:
            live = []
            for p in candidates:
                search_text = f"{p.display_name} {p.description}".strip().lower()
                matched = next((pat for pat in self.exclude_patterns
                                if pat in search_text), None)
                if matched:
                    pid = _norm_id(p.id)
                    if pid not in pattern_seen:
                        pattern_seen[pid] = {
                            "policy_id":    p.id,
                            "display_name": p.display_name,
                            "reason":       f"Matches exclude pattern: '{matched}'",
                            "source":       "pattern-exclude",
                            "pattern":      matched,
                        }
                else:
                    live.append(p)
            candidates = live

        if self.verbose:
            parts = [f"{len(preview_seen)} preview-excluded",
                     f"{len(self.global_ignore)} OOS-excluded"]
            if pattern_seen:
                parts.append(f"{len(pattern_seen)} pattern-excluded")
            print(f"  Candidates: {len(candidates)} built-in policies "
                  f"({', '.join(parts)})",
                  file=sys.stderr)

        self.mapper.prepare(candidates)
        self.mapper.set_oos_context(self.oos_context)
        self.mapper.set_corrections(self.corrections)
        if self.corrections and self.verbose:
            print(f"  Corrections: {len(self.corrections)} human overrides as few-shot examples",
                  file=sys.stderr)

        # ── split: carry-forward vs needs-classification ──────────────────────
        all_controls  = list(catalog.controls())
        total         = len(all_controls)
        carry_forward = []
        to_classify   = []
        for ctrl in all_controls:
            prior = existing.mappings.get(ctrl.id)
            if prior and prior.decision in (Decision.INCLUDE, Decision.IGNORE):
                carry_forward.append((ctrl, prior))
            else:
                to_classify.append((ctrl, prior))

        if self.verbose:
            mode = (f"{self.concurrency} threads" if self.concurrency > 1
                    else "sequential")
            print(f"  {len(carry_forward)} carry-forward  |  "
                  f"{len(to_classify)} to classify  |  mode: {mode}",
                  file=sys.stderr)

        # ── shared state (written from multiple threads) ──────────────────────
        print_lock   = threading.Lock()
        results_lock = threading.Lock()
        counter      = [0]               # [carry_done + classify_done]
        oos_seen: dict[str, dict] = {}

        # ── 1. carry-forward (sequential — instant, no LLM) ──────────────────
        for ctrl, prior in carry_forward:
            result.mappings[ctrl.id] = prior
            counter[0] += 1
            if self.verbose:
                _progress(counter[0], total, ctrl.id, "carry-forward",
                          f"({prior.decision.value})", print_lock)

        # ── 2. classify new controls (parallel) ──────────────────────────────
        def _classify_one(ctrl, prior):
            """Run proposal + decision for one control. Thread-safe."""
            try:
                proposal = self.mapper.propose(ctrl, candidates)
            except Exception as exc:
                with print_lock:
                    print(f"\n   ✗  error on {ctrl.id}: {exc!s:.120} — skipping",
                          file=sys.stderr, flush=True)
                return None

            refs = [PolicyRef(policy_id=p.id, display_name=p.display_name)
                    for p in proposal.policies]

            if not refs:
                decision = Decision.IGNORE if prior is None else prior.decision
            elif self.auto_approve and proposal.confidence >= self.confidence_threshold:
                decision = Decision.INCLUDE
            else:
                decision = Decision.REVIEW

            entry = ControlMapping(
                control_id=ctrl.id, decision=decision, policies=refs,
                rationale=proposal.rationale, source="auto",
                confidence=proposal.confidence,
            )

            new_oos = []
            for cand in getattr(proposal, "oos_candidates", []):
                pid = _norm_id(cand.get("policy_id", ""))
                if pid:
                    new_oos.append((pid, {**cand, "first_seen_control": ctrl.id}))

            return entry, new_oos, proposal.confidence, len(refs)

        with ThreadPoolExecutor(max_workers=self.concurrency) as pool:
            future_map = {pool.submit(_classify_one, ctrl, prior): ctrl
                          for ctrl, prior in to_classify}

            for fut in as_completed(future_map):
                ctrl = future_map[fut]
                outcome = fut.result()

                with results_lock:
                    counter[0] += 1
                    n = counter[0]

                if outcome is None:
                    continue   # errored control — leave absent → retried next run

                entry, new_oos, conf, n_pol = outcome

                with results_lock:
                    result.mappings[ctrl.id] = entry
                    for pid, rec in new_oos:
                        if pid not in oos_seen:
                            oos_seen[pid] = {**rec, "flagging_controls": [ctrl.id]}
                        else:
                            # accumulate every control that flagged this policy as OOS
                            oos_seen[pid].setdefault("flagging_controls", []).append(ctrl.id)
                    # checkpoint-save every N controls so a kill only loses
                    # up to checkpoint_every decisions rather than everything
                    n_classified = sum(1 for m in result.mappings.values()
                                       if m.source == "auto")
                    if checkpoint_fn and n_classified % self.checkpoint_every == 0:
                        try:
                            checkpoint_fn(result)
                        except Exception:
                            pass   # checkpoint failures are non-fatal

                if self.verbose:
                    oos_tag = f" +{len(new_oos)}OOS" if new_oos else ""
                    conf_str = f"conf:{conf:.2f}" if n_pol else "no match"
                    _progress(n, total, ctrl.id, entry.decision.value,
                              f"({conf_str} · {n_pol} pol){oos_tag}", print_lock)

        # ── summary ──────────────────────────────────────────────────────────
        if self.verbose:
            print(file=sys.stderr)
            approved = sum(1 for m in result.mappings.values()
                           if m.decision == Decision.INCLUDE)
            pending  = sum(1 for m in result.mappings.values()
                           if m.decision == Decision.REVIEW)
            n_carry  = len(carry_forward)
            print(f"  Mapping complete — "
                  f"{n_carry} carry-forward  |  "
                  f"{approved} approved  |  "
                  f"{pending} pending review  |  "
                  f"{len(oos_seen)} OOS candidates",
                  file=sys.stderr)

        # normalise: keep first_seen_control for backward compat, add flagging_controls count
        for rec in oos_seen.values():
            flagging = rec.get("flagging_controls", [])
            if flagging and "first_seen_control" not in rec:
                rec["first_seen_control"] = flagging[0]
            rec["flagged_by_n_controls"] = len(flagging)
        result.oos_suggestions  = list(oos_seen.values())
        result.preview_excluded = list(preview_seen.values())
        result.pattern_excluded = list(pattern_seen.values())
        return result
