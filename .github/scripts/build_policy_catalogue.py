"""Build a normalized catalogue snapshot from an Azure/azure-policy checkout."""
from __future__ import annotations

import argparse
from datetime import date
import gzip
import json
from pathlib import Path

from control_translator.catalogue.snapshot import build_snapshot


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--generated-at", default=date.today().isoformat())
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    payload = build_snapshot(args.source, args.source_commit, args.generated_at)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    serialized = (json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True) + "\n").encode("utf-8")
    if args.output.name.endswith(".gz"):
        args.output.write_bytes(gzip.compress(serialized, mtime=0))
    else:
        args.output.write_bytes(serialized)
    print(f"Wrote {payload['policy_count']} policies to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
