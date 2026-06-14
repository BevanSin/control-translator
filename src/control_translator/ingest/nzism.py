"""NZISM ingestor: the published NZISM CSV export -> OSCAL catalogue.

Input is the NZISM document exported as CSV (9 columns):
    Chapter, Section, Sub-Section, Block, Paragraph, Classifications, Compliance,
    CID, ControlText

A row is a control iff it has both a CID and ControlText (structural objective/context
rows have neither). Mapping to OSCAL:
    - group   = Chapter (id = zero-padded chapter number, e.g. "06")
    - control id = the Paragraph, normalised to the Azure initiative convention
                   ("6.2.5.C.01." -> "06.2.5.C.01") so the durable mapping and the built
                   policySet groups line up with the existing published initiative.
    - title   = the Section heading (the closest human-readable label NZISM gives)
    - prose   = ControlText
    - props   = compliance (Must/Should/...), classification, cid, paragraph

Classification profiles
-----------------------
NZISM controls carry a Classifications field indicating which security levels the
control applies to. For a cloud deployment approved only up to a certain level (e.g.
NZ Government: Restricted, AU Government: Protected), higher-classification-only controls
are irrelevant and can be filtered at ingest time via `classification_profile`:

  "all"        — include every control regardless of classification (default)
  "restricted" — include only controls applicable at Restricted and below
                 ("All Classifications", "Restricted/Sensitive", "Unclassified/In-Confidence")
  "protected"  — include controls up to Protected (IRAP/ISM equivalent)
                 ("All Classifications", "Restricted/Sensitive", "Unclassified/In-Confidence",
                  "Protected")

Custom profiles can be added to CLASSIFICATION_PROFILES below.
"""
from __future__ import annotations

import csv
import re
import uuid

from .base import FrameworkIngestor
from ..models import Catalog, Group, Control

_SECTION_NUM = re.compile(r"^\d+\.\d+\.\s*")
_MULTI_SPACE = re.compile(r"  +")
_UNICODE_NBSP = "\u00a0"

# Terms that indicate a control applies at or below each profile level.
# A control is IN SCOPE when its Classifications field contains at least one of
# the terms for the chosen profile (case-insensitive substring match).
CLASSIFICATION_PROFILES: dict[str, set[str] | None] = {
    "all":        None,    # include everything — no filter
    "restricted": {"all classifications", "restricted", "unclassified"},
    "protected":  {"all classifications", "restricted", "unclassified", "protected"},
}


def _in_scope(classification: str, profile_terms: set[str] | None) -> bool:
    """Return True when the control's classification is in scope for the profile."""
    if profile_terms is None:
        return True                         # "all" profile — no filter
    if not classification:
        return True                         # no classification field — assume all
    cl = classification.strip().lower()
    return any(term in cl for term in profile_terms)


def _clean_prose(text: str) -> str:
    """Normalise CSV export text artifacts into clean prose.

    The NZISM CSV export preserves the document's original formatting as
    embedded newlines, non-breaking spaces, and typographic (curly) quotes
    from the source document. All are normalised so the output JSON contains
    only standard ASCII punctuation and regular spaces.

    Strategy:
      1. Normalise line endings.
      2. Replace non-breaking spaces with regular spaces.
      3. Replace typographic quotes/apostrophes with ASCII equivalents.
      4. "VERB:\\n+" → "VERB: " (collapse list-intro + newline into inline colon).
      5. Remaining newlines → single space (list items run on as prose).
      6. Collapse multiple spaces and strip.
    """
    if not text:
        return text
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace(_UNICODE_NBSP, " ")
    # typographic quotes → ASCII equivalents
    text = text.replace("‘", "'").replace("’", "'")   # '' → '
    text = text.replace("“", '"').replace("”", '"')   # "" → "
    text = text.replace("–", "-").replace("—", " - ") # – → -  — →  -
    text = re.sub(r":\n+", ": ", text)    # "SHOULD:\n\n..." → "SHOULD: ..."
    text = re.sub(r"\n+", " ", text)      # remaining newlines → space
    text = _MULTI_SPACE.sub(" ", text)    # collapse multiple spaces
    return text.strip()


def normalise_control_id(paragraph: str) -> str:
    """'6.2.5.C.01.' -> '06.2.5.C.01'  (zero-pad chapter; fix the one 'C-02' typo)."""
    p = paragraph.strip().rstrip(".").replace("C-", "C.")
    if not p:
        return ""
    parts = p.split(".")
    if parts[0].isdigit():
        parts[0] = parts[0].zfill(2)
    return ".".join(parts)


def _chapter_id(chapter: str) -> str:
    num = chapter.split(".", 1)[0].strip()
    return num.zfill(2) if num.isdigit() else (num or "00")


def _section_title(section: str, fallback: str) -> str:
    return _SECTION_NUM.sub("", section).strip() or fallback


class NzismIngestor(FrameworkIngestor):
    def ingest(self, source: str, *, framework_id: str, version: str,
               classification_profile: str = "all") -> Catalog:
        profile_terms = CLASSIFICATION_PROFILES.get(
            classification_profile.lower(),
            CLASSIFICATION_PROFILES["all"],
        )
        groups: dict[str, Group] = {}
        order: list[str] = []
        used_ids: set[str] = set()
        n_skipped = 0

        with open(source, encoding="utf-8-sig", newline="") as fh:
            for row in csv.DictReader(fh):
                cid = (row.get("CID") or "").strip()
                text = _clean_prose(row.get("ControlText") or "")
                if not cid or not text:
                    continue                       # structural / heading row

                # classification filter — skip controls outside the deployment scope
                cl = (row.get("Classifications") or "").strip()
                if not _in_scope(cl, profile_terms):
                    n_skipped += 1
                    continue

                chapter = (row.get("Chapter") or "").strip()
                ch_id = _chapter_id(chapter)
                if ch_id not in groups:
                    groups[ch_id] = Group(id=ch_id, title=chapter or ch_id)
                    order.append(ch_id)

                control_id = normalise_control_id(row.get("Paragraph", "")) or f"CID-{cid}"
                if control_id in used_ids:           # rare source duplicate -> disambiguate
                    control_id = f"{control_id}-{cid}"
                used_ids.add(control_id)

                groups[ch_id].controls.append(Control(
                    id=control_id,
                    title=_section_title(row.get("Section", ""), chapter),
                    prose=text,
                    family=chapter,
                    props={
                        "compliance": (row.get("Compliance") or "").strip(),
                        "classification": (row.get("Classifications") or "").strip(),
                        "cid": cid,
                        "paragraph": (row.get("Paragraph") or "").strip(),
                    },
                ))

        if n_skipped:
            import sys
            print(f"  Classification filter ({classification_profile!r}): "
                  f"{n_skipped} controls excluded (outside deployment scope)",
                  file=sys.stderr)

        return Catalog(
            uuid=str(uuid.uuid5(uuid.NAMESPACE_URL, f"nzism:{version}")),
            title=f"New Zealand Information Security Manual (NZISM) v{version}",
            version=version,
            groups=[groups[c] for c in order],
        )
