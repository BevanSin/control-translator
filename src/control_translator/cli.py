"""Command-line entrypoint: `ct run --config <path>` (and per-stage commands)."""
from __future__ import annotations

import argparse
import json
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


def cmd_export_review(args: argparse.Namespace) -> int:
    """Export pending review items and OOS candidates to Excel for authority sign-off."""
    config = _load(args.config)
    fw = config["framework"]

    # Default output path: out/<framework-id>-<version>/review.xlsx
    out_dir = config.get("out_dir", "out")
    slug    = f"{fw['id']}-{fw['version']}"
    output_path = args.output or os.path.join(out_dir, slug, "review.xlsx")
    parent = os.path.dirname(os.path.abspath(output_path))
    os.makedirs(parent, exist_ok=True)

    # Run pipeline to get current mapping state (mostly carry-forward — fast)
    print("Collecting review state...")
    result = run_pipeline(config, do_distribute=False)
    pending = result.mapping.pending_review()

    # OOS candidates: prefer the latest bundle file on disk over the pipeline result
    # (the pipeline re-run may have 0 new candidates if everything was carry-forward)
    out_dir  = config.get("out_dir", "out")
    slug     = f"{fw['id']}-{fw['version']}"
    oos_path = os.path.join(out_dir, slug, "oos-candidates.json")
    if result.bundle and result.bundle.files.get("oos-candidates.json"):
        oos_cands = json.loads(result.bundle.files["oos-candidates.json"])
    elif os.path.exists(oos_path):
        with open(oos_path, encoding="utf-8") as fh:
            oos_cands = json.load(fh)
        print(f"  (OOS candidates loaded from existing bundle: {oos_path})")
    else:
        oos_cands = []

    # For preview excluded, also check bundle file on disk as fallback
    preview = result.mapping.preview_excluded or []
    if not preview:
        prev_path = os.path.join(out_dir, slug, "out-of-scope.json")
        if os.path.exists(prev_path):
            with open(prev_path, encoding="utf-8") as fh:
                oos_data = json.load(fh)
            preview = [e for e in oos_data if e.get("source") == "auto-preview"]

    print(f"  {len(pending)} pending review  |  {len(oos_cands)} OOS candidates  |  {len(preview)} preview-excluded")

    if not pending and not oos_cands:
        print("\n  Nothing to review right now.")
        print("  This happens when auto_approve=true and all decisions are carry-forward.")
        print("  To generate pending reviews: set auto_approve=false in your config and re-run.")
        print("  OOS candidates are generated during classification runs, not carry-forward runs.")
        if os.path.exists(oos_path):
            print(f"\n  Tip: OOS candidates from the last full run exist at:")
            print(f"    {oos_path}")
            print(f"  Re-run ct export-review — the updated command reads this file automatically.")
        return 0

    from .review.excel import export_review
    export_review(result, output_path=output_path,
                  framework_id=fw["id"], version=fw["version"],
                  oos_register_path=config["mapping"].get("global_ignore"),
                  oos_candidates=oos_cands,
                  preview_excluded=preview)
    print(f"\nReview workbook written → {output_path}")
    print("Open in Excel, fill the Decision columns (yellow cells), save, then:")
    print(f"  ct import-review --config {args.config} --input {output_path}")
    return 0


def cmd_import_review(args: argparse.Namespace) -> int:
    """Read decisions from a completed review workbook and update the mapping store."""
    config = _load(args.config)
    mcfg = config["mapping"]
    gi = mcfg.get("global_ignore", [])
    if isinstance(gi, str): gi = [gi]

    from .review.excel import import_review
    summary = import_review(
        args.input,
        mapping_store_path=mcfg["store"],
        oos_register_paths=gi,
        corrections_path=mcfg.get("corrections"),
    )
    total = summary["include"] + summary["ignore"] + summary["skipped"]
    print(f"\nImport complete:")
    print(f"  {summary['include']:>4} decisions set to include")
    print(f"  {summary['ignore']:>4} decisions set to ignore")
    print(f"  {summary['skipped']:>4} skipped (blank or 'Skip')")
    if summary["oos_added"]:
        print(f"  {summary['oos_added']:>4} OOS candidates added to nzism-ignore")
    if summary["oos_global"]:
        print(f"  {summary['oos_global']:>4} OOS candidates added to global-ignore")
    if summary.get("revoked"):
        print(f"  {summary['revoked']:>4} approved controls revoked → back to review")
    print(f"\nRe-run to rebuild the initiative with updated decisions:")
    print(f"  ct run --config {args.config}")
    return 0


def cmd_seed(args: argparse.Namespace) -> int:
    """Seed the mapping store from an existing initiative or CSV."""
    config   = _load(args.config)
    fw       = config["framework"]
    mcfg     = config["mapping"]
    dry_run  = args.dry_run
    overwrite = args.overwrite

    from .mapping.store import MappingStore
    from .seeds import seed_from_initiative, seed_from_csv
    store = MappingStore(mcfg["store"])

    if not args.from_initiative and not args.from_csv:
        print("Error: supply at least one of --from-initiative or --from-csv")
        return 1

    total_seeded = 0

    if args.from_initiative:
        prefix = config.get("build", {}).get("group_prefix", "")
        print(f"Seeding from initiative: {args.from_initiative}"
              + (" [DRY RUN]" if dry_run else ""))
        s = seed_from_initiative(
            args.from_initiative, store=store,
            framework_id=fw["id"], version=fw["version"],
            group_prefix=prefix, overwrite_human=overwrite, dry_run=dry_run)
        print(f"  Groups found        : {s.get('groups_found', '?')}")
        print(f"  Pairs extracted     : {s.get('pairs_extracted', '?')}")
        print(f"  Seeded              : {s['seeded']} controls → include")
        if s["already_seeded"]: print(f"  Already seeded      : {s['already_seeded']} (skipped)")
        if s["skipped_human"]:  print(f"  Human decisions kept: {s['skipped_human']} (protected)")
        total_seeded += s["seeded"]

    if args.from_csv:
        print(f"Seeding from CSV: {args.from_csv}"
              + (" [DRY RUN]" if dry_run else ""))
        s = seed_from_csv(
            args.from_csv, store=store,
            framework_id=fw["id"], version=fw["version"],
            overwrite_human=overwrite, dry_run=dry_run)
        print(f"  Seeded              : {s['seeded']} controls")
        if s["already_seeded"]:  print(f"  Already seeded      : {s['already_seeded']} (skipped)")
        if s["skipped_human"]:   print(f"  Human decisions kept: {s['skipped_human']} (protected)")
        if s["skipped_bad_row"]: print(f"  Bad rows skipped    : {s['skipped_bad_row']}")
        total_seeded += s["seeded"]

    if dry_run:
        print(f"\n  [DRY RUN] {total_seeded} decisions would be seeded — nothing written.")
    else:
        print(f"\n  {total_seeded} decisions written to {mcfg['store']}")
        print(f"  Next ct run will carry-forward these decisions without LLM calls.")
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

    p_exp = sub.add_parser("export-review",
                            help="export pending review items to Excel")
    p_exp.add_argument("--config", required=True)
    p_exp.add_argument("--output", default=None,
                       help="output Excel file (default: out/<framework>-<version>/review.xlsx)")
    p_exp.set_defaults(func=cmd_export_review)

    p_seed = sub.add_parser("seed",
                             help="seed mapping store from an existing initiative or CSV")
    p_seed.add_argument("--config",           required=True)
    p_seed.add_argument("--from-initiative",  metavar="PATH",
                        help="path to a policySet.json to extract decisions from")
    p_seed.add_argument("--from-csv",         metavar="PATH",
                        help="path to a CSV (columns: control_id, policy_id, decision, reason)")
    p_seed.add_argument("--dry-run",          action="store_true",
                        help="show what would be seeded without writing to the store")
    p_seed.add_argument("--overwrite",        action="store_true",
                        help="overwrite existing seeded decisions (never overwrites human decisions)")
    p_seed.set_defaults(func=cmd_seed)

    p_imp = sub.add_parser("import-review",
                            help="import decisions from completed review workbook")
    p_imp.add_argument("--config", required=True)
    p_imp.add_argument("--input", required=True,
                       help="completed review Excel file")
    p_imp.set_defaults(func=cmd_import_review)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
