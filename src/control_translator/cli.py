"""Command-line entrypoint: `ct run --config <path>` (and per-stage commands)."""
from __future__ import annotations

import argparse
import os
import sys

from .config import load_config, resolve
from .pipeline import run_pipeline


def _load(path: str) -> dict:
    # config paths are relative to the working directory (run from the repo root)
    return resolve(load_config(path), os.getcwd())


def cmd_run(args: argparse.Namespace) -> int:
    config = _load(args.config)
    result = run_pipeline(config, do_distribute=not args.no_distribute)

    n_controls = sum(1 for _ in result.catalog.controls())
    approved = result.mapping.approved()
    pending = result.mapping.pending_review()
    print(f"framework      : {result.mapping.framework_id} v{result.mapping.version}")
    print(f"controls       : {n_controls}")
    print(f"approved       : {len(approved)} control(s) with built-in coverage")
    print(f"pending review : {len(pending)} (awaiting authority sign-off)")

    if result.lint_errors:
        print("\nlint errors:")
        for e in result.lint_errors:
            print(f"  - {e}")

    if result.published_to:
        print(f"\npublished -> {result.published_to}")
    if pending and not approved:
        print("\nNote: nothing auto-approved. Review pending mappings, set decisions to "
              "'include', and re-run (or enable auto_approve for the baseline).")
    return 1 if result.lint_errors else 0


def cmd_review(args: argparse.Namespace) -> int:
    """List proposals awaiting sign-off, OOS candidates, and reconsidered OOS entries."""
    config = _load(args.config)
    result = run_pipeline(config, do_distribute=False)

    # --- OOS reconsidered (highest priority — needs action before next deploy) ---
    recon_json = result.bundle and result.bundle.files.get("oos-reconsidered.json")
    if recon_json:
        import json as _json
        recon = _json.loads(recon_json)
        print(f"\n{'='*60}")
        print(f"OOS RECONSIDERED ({len(recon)}) — review and remove from global-ignore.json if appropriate")
        print('='*60)
        for r in recon:
            print(f"  [{r.get('display_name',r.get('policy_id','?'))}]")
            print(f"    Reason: {r.get('reconsideration_reason','')}")
            if r.get("current_display_name"):
                print(f"    Now named: {r['current_display_name']}")

    # --- Preview auto-excluded ---
    prev = result.mapping.preview_excluded
    if prev:
        print(f"\n{'='*60}")
        print(f"PREVIEW-EXCLUDED ({len(prev)}) — auto-filtered; will be reconsidered when GA")
        print('='*60)
        for p in prev[:5]:
            print(f"  {p['display_name']}")
        if len(prev) > 5:
            print(f"  ... and {len(prev)-5} more (see out-of-scope.json)")

    # --- Pending mapping proposals (authority sign-off gate) ---
    pending = result.mapping.pending_review()
    print(f"\n{'='*60}")
    print(f"PENDING REVIEW ({len(pending)}) — approve by setting decision=include in mapping store")
    print('='*60)
    if not pending:
        print("  nothing pending review.")
    for m in pending:
        pols = ", ".join(p.display_name or p.policy_id for p in m.policies)
        print(f"  [{m.confidence:.2f}] {m.control_id}: {pols}\n          {m.rationale}")

    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ct", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="run the full pipeline")
    p_run.add_argument("--config", required=True)
    p_run.add_argument("--no-distribute", action="store_true",
                       help="build + validate but do not publish")
    p_run.set_defaults(func=cmd_run)

    p_rev = sub.add_parser("review", help="list mappings awaiting authority sign-off")
    p_rev.add_argument("--config", required=True)
    p_rev.set_defaults(func=cmd_review)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
