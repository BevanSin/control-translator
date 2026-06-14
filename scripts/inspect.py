"""Quick inspection of a ct run output bundle.

Usage (from the project root, venv active):
    python scripts/inspect.py                          # uses out/ auto-discovered
    python scripts/inspect.py out/nzism-3.9           # specific bundle
    python scripts/inspect.py --store data/mappings/nzism.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def _load(path: str | Path) -> dict | list:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def inspect_bundle(bundle_dir: Path) -> None:
    ps_path = bundle_dir / "policySet.json"
    if not ps_path.exists():
        print(f"  no policySet.json in {bundle_dir}")
        return

    ps   = _load(ps_path)["properties"]
    defs = ps.get("policyDefinitions", [])
    grps = ps.get("policyDefinitionGroups", [])
    multi = [d for d in defs if len(d.get("groupNames", [])) > 1]

    print(f"\n{'='*55}")
    print(f"Bundle: {bundle_dir.name}")
    print(f"{'='*55}")
    print(f"  Controls covered          : {len(grps)}")
    print(f"  Policy definitions        : {len(defs)}")
    print(f"  Policies → multiple ctrls : {len(multi)}")
    print(f"  Top-level parameters      : {len(ps.get('parameters', {}))}")

    oos_path = bundle_dir / "out-of-scope.json"
    if oos_path.exists():
        oos = _load(oos_path)
        human = [r for r in oos if r.get("source") != "auto-preview"]
        preview = [r for r in oos if r.get("source") == "auto-preview"]
        print(f"  OOS (human decisions)     : {len(human)}")
        print(f"  OOS (auto-preview)        : {len(preview)}")

    cand_path = bundle_dir / "oos-candidates.json"
    if cand_path.exists():
        cands = _load(cand_path)
        print(f"\n  OOS candidates ({len(cands)} — review and promote to global-ignore if agreed):")
        for r in cands[:10]:
            name = r.get("display_name", r.get("policy_id", "?"))[:62]
            reason = r.get("oos_reason", "")[:55]
            print(f"    • {name}")
            print(f"      {reason}")
        if len(cands) > 10:
            print(f"    ... and {len(cands) - 10} more (see {cand_path})")

    recon_path = bundle_dir / "oos-reconsidered.json"
    if recon_path.exists():
        recon = _load(recon_path)
        print(f"\n  ⚠  OOS reconsidered ({len(recon)}) — check and update your OOS register:")
        for r in recon[:5]:
            print(f"    • {r.get('display_name', '?')[:62]}")
            print(f"      {r.get('reconsideration_reason', '')[:55]}")


def inspect_store(store_path: Path) -> None:
    store  = _load(store_path)
    counts: dict[str, int] = {}
    for m in store.get("mappings", {}).values():
        d = m.get("decision", "?")
        counts[d] = counts.get(d, 0) + 1
    total = sum(counts.values())

    print(f"\n{'='*55}")
    print(f"Mapping store: {store_path.name}  "
          f"(framework={store.get('framework_id')}  v{store.get('version')})")
    print(f"{'='*55}")
    for k in ("include", "review", "ignore"):
        v = counts.get(k, 0)
        bar = "█" * int(30 * v / total) if total else ""
        print(f"  {k:8}: {v:>5}  {bar}")
    print(f"  {'total':8}: {total:>5}")
    coverage = counts.get("include", 0)
    print(f"\n  Coverage: {coverage}/{total} = {coverage/total*100:.1f}%")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("bundle", nargs="?", help="path to output bundle folder")
    ap.add_argument("--store", help="path to mapping store JSON")
    args = ap.parse_args()

    # auto-discover bundle if not specified
    bundle_dir: Path | None = None
    if args.bundle:
        bundle_dir = Path(args.bundle)
    else:
        out = Path("out")
        if out.exists():
            candidates = [d for d in out.iterdir()
                          if d.is_dir() and (d / "policySet.json").exists()]
            if candidates:
                bundle_dir = max(candidates, key=lambda d: d.stat().st_mtime)

    if bundle_dir:
        inspect_bundle(bundle_dir)
    else:
        print("No bundle found. Run ct first, or pass a bundle path.")

    # store
    store_path: Path | None = None
    if args.store:
        store_path = Path(args.store)
    else:
        # try to find one matching the bundle name
        if bundle_dir:
            slug = bundle_dir.name  # e.g. nzism-3.9
            guess = Path("data/mappings") / f"{slug.split('-')[0]}.json"
            if guess.exists():
                store_path = guess

    if store_path and store_path.exists():
        inspect_store(store_path)

    print()


if __name__ == "__main__":
    main()
