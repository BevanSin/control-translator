"""Run configuration loader. Supports JSON natively; YAML if PyYAML is installed."""
from __future__ import annotations

import json
import os


def load_config(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    if path.endswith((".yaml", ".yml")):
        try:
            import yaml  # optional dependency
        except ModuleNotFoundError as exc:
            raise SystemExit(
                f"{path} is YAML but PyYAML is not installed. "
                "Install with `pip install control-translator[yaml]` or use a JSON config."
            ) from exc
        return yaml.safe_load(text)
    return json.loads(text)


def resolve(config: dict, root: str) -> dict:
    """Resolve relative paths in the config against the config file's directory."""
    def fix(p):
        return p if not p or os.path.isabs(p) else os.path.join(root, p)

    config = json.loads(json.dumps(config))  # deep copy
    config["ingest"]["source"] = fix(config["ingest"].get("source"))
    if config["catalogue"].get("source"):
        config["catalogue"]["source"] = fix(config["catalogue"]["source"])
    config["mapping"]["store"] = fix(config["mapping"].get("store"))
    gi = config["mapping"].get("global_ignore")
    if gi:
        # support single path (str) or list of paths
        config["mapping"]["global_ignore"] = (
            [fix(p) for p in gi] if isinstance(gi, list) else fix(gi)
        )
    if config.get("build", {}).get("parameter_overrides"):
        config["build"]["parameter_overrides"] = fix(config["build"]["parameter_overrides"])
    config["out_dir"] = fix(config.get("out_dir", "out"))
    return config
