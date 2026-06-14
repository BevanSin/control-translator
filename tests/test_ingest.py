"""Tests for the NZISM ingestor (CSV export -> OSCAL catalogue)."""
import os

from control_translator.ingest.nzism import NzismIngestor, normalise_control_id

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURE = os.path.join(REPO_ROOT, "tests", "fixtures", "nzism_sample.csv")


def test_control_id_normalisation():
    assert normalise_control_id("6.2.5.C.01.") == "06.2.5.C.01"   # zero-pad chapter
    assert normalise_control_id("16.1.32.C.01.") == "16.1.32.C.01"  # already 2 digits
    assert normalise_control_id("11.8.11.C-02") == "11.8.11.C.02"   # fix the hyphen typo


def test_ingest_skips_structural_rows_and_groups_by_chapter():
    catalog = NzismIngestor().ingest(FIXTURE, framework_id="nzism", version="3.9")
    controls = list(catalog.controls())

    # the structural objective row (no CID/text) is skipped; 3 real controls remain
    assert len(controls) == 3
    ids = {c.id for c in controls}
    assert ids == {"06.2.5.C.01", "16.1.32.C.01", "11.8.11.C.02"}

    # grouped by chapter, in first-appearance order
    assert [g.id for g in catalog.groups] == ["06", "16", "11"]

    # props carry the compliance strength and stable CID
    enc = next(c for c in controls if c.id == "06.2.5.C.01")
    assert enc.props["compliance"] == "Should"
    assert enc.props["cid"] == "1066"
    assert enc.prose.startswith("SAMPLE control text")


def test_ingest_roundtrips_through_oscal():
    catalog = NzismIngestor().ingest(FIXTURE, framework_id="nzism", version="3.9")
    from control_translator.models import Catalog
    restored = Catalog.from_oscal(catalog.to_oscal())
    assert len(list(restored.controls())) == 3
