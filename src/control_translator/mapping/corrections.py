"""Human correction store — policy mappings confirmed by a reviewer.

When a reviewer selects "Include for this control" in the review workbook,
the correction is written here. The classifier picks it up on subsequent runs
as few-shot examples so the LLM applies the same reasoning without being told
explicitly every time.

corrections.json entry format:
  {
    "policy_id":     "/providers/.../policyDefinitions/<guid>",
    "display_name":  "Defender for Containers should be enabled",
    "control_id":    "07.1.7.C.02",
    "chapter":       "07. Network security",
    "compliance":    "Must",
    "include_reasoning": "Defender services audit security tooling — relevant to
                          controls requiring monitoring and detection capability.",
    "added_date":    "2025-09-03",
    "source":        "review-override"
  }
"""
from __future__ import annotations

import json
import os


def load_corrections(path: str | None) -> list[dict]:
    if not path or not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    return [d for d in data if isinstance(d, dict) and d.get("policy_id")]


def save_correction(path: str, entry: dict) -> None:
    existing = load_corrections(path)
    # deduplicate by (policy_id, control_id)
    key = (entry.get("policy_id", ""), entry.get("control_id", ""))
    existing = [e for e in existing
                if (e.get("policy_id",""), e.get("control_id","")) != key]
    existing.append(entry)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(existing, fh, indent=2, ensure_ascii=False)


def corrections_to_prompt_fragment(corrections: list[dict]) -> str:
    """Format corrections as a few-shot example block for the classifier prompt."""
    if not corrections:
        return ""
    lines = [
        "\nHUMAN REVIEWER CORRECTIONS — use these as calibration examples when "
        "evaluating similar policies and controls:",
    ]
    for c in corrections[:20]:   # cap at 20 to avoid bloating the prompt
        lines.append(
            f'- "{c.get("display_name", c.get("policy_id","")[:40])}" '
            f'→ INCLUDE for {c.get("chapter","?")} controls '
            f'({c.get("compliance","?")}). '
            f'Reasoning: {c.get("include_reasoning","reviewer confirmed relevance.")}'
        )
    lines.append(
        "Apply the same reasoning pattern to similar policies and controls "
        "in this session."
    )
    return "\n".join(lines)
