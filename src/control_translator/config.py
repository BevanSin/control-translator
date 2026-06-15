"""Run configuration loader. Supports JSON natively; YAML if PyYAML is installed.

Secrets and environment-specific values should never be written into config files
committed to source control. Instead use ${VAR_NAME} placeholders in config files
and supply real values via:

  1. A .env file in the project root (gitignored, loaded automatically)
  2. Shell environment variables (set before running ct)

Example config fragment:
    "mapping": {
        "classifier":       "azure-openai",
        "model":            "gpt-4o-mini",
        "foundry_base_url": "${AZURE_OPENAI_ENDPOINT}"
    }

Example .env file:
    AZURE_OPENAI_ENDPOINT=https://myresource.services.ai.azure.com/openai/v1
    ANTHROPIC_API_KEY=sk-ant-...
"""
from __future__ import annotations

import json
import os
import re


# ── .env file loader ─────────────────────────────────────────────────────────

def _load_dotenv(root: str) -> None:
    """Load a .env file from `root` into os.environ if it exists.

    Handles KEY=VALUE, KEY="VALUE", and KEY='VALUE' lines.
    Lines starting with # are comments. Existing env vars are not overwritten.
    """
    env_path = os.path.join(root, ".env")
    if not os.path.exists(env_path):
        return
    with open(env_path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            # strip surrounding quotes
            value = value.strip()
            if (value.startswith('"') and value.endswith('"')) or \
               (value.startswith("'") and value.endswith("'")):
                value = value[1:-1]
            if key and key not in os.environ:
                os.environ[key] = value


# ── env var substitution ─────────────────────────────────────────────────────

_ENV_RE = re.compile(r"\$\{([^}]+)\}")


def _expand(obj):
    """Recursively substitute ${VAR_NAME} in string config values.

    Uses environment variables. If a variable is not set the placeholder is
    preserved so ct fails with a clear "placeholder not replaced" error rather
    than silently using an empty string.
    """
    if isinstance(obj, dict):
        return {k: _expand(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_expand(i) for i in obj]
    if isinstance(obj, str) and "${" in obj:
        def _sub(m):
            var = m.group(1)
            val = os.environ.get(var)
            if val is None:
                raise SystemExit(
                    f"Config references ${{{var}}} but that environment variable is "
                    "not set. Add it to your .env file or set it in your shell.")
            return val
        return _ENV_RE.sub(_sub, obj)
    return obj


# ── public API ────────────────────────────────────────────────────────────────

def load_config(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    if path.endswith((".yaml", ".yml")):
        try:
            import yaml
        except ModuleNotFoundError as exc:
            raise SystemExit(
                f"{path} is YAML but PyYAML is not installed. "
                "Install with `pip install control-translator[yaml]` or use a JSON config."
            ) from exc
        raw = yaml.safe_load(text)
    else:
        raw = json.loads(text)

    # load .env from the config file's directory (or cwd as fallback)
    _load_dotenv(os.path.dirname(os.path.abspath(path)))
    _load_dotenv(os.getcwd())

    return _expand(raw)


def resolve(config: dict, root: str) -> dict:
    """Resolve relative paths in the config against the working directory."""
    def fix(p):
        return p if not p or os.path.isabs(p) else os.path.join(root, p)

    config = json.loads(json.dumps(config))   # deep copy
    config["ingest"]["source"] = fix(config["ingest"].get("source"))
    if config["catalogue"].get("source"):
        config["catalogue"]["source"] = fix(config["catalogue"]["source"])
    config["mapping"]["store"] = fix(config["mapping"].get("store"))
    gi = config["mapping"].get("global_ignore")
    if gi:
        config["mapping"]["global_ignore"] = (
            [fix(p) for p in gi] if isinstance(gi, list) else fix(gi)
        )
    if config.get("build", {}).get("parameter_overrides"):
        config["build"]["parameter_overrides"] = fix(config["build"]["parameter_overrides"])
    if config["mapping"].get("corrections"):
        config["mapping"]["corrections"] = fix(config["mapping"]["corrections"])
    if config["mapping"].get("embedding_cache"):
        config["mapping"]["embedding_cache"] = fix(config["mapping"]["embedding_cache"])
    config["out_dir"] = fix(config.get("out_dir", "out"))
    return config
