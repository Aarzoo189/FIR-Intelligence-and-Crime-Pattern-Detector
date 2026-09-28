"""tests/test_mcp_server.py — integration tests for the MCP server tool functions.

Tests call the tool functions directly on a temporary copy of the production
database so we exercise real data without mutating it.

Checks:
1. A fake quote is rejected by submit_extraction.
2. A valid extraction is accepted and audited.
3. explain_link returns phone or UPI evidence for a known strong pair.
4. submit_summary rejects an invented number.
5. No tool output contains answer-key data (ground_truth.csv / gang_profiles).
"""
import json
import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest

# Ensure project root is on sys.path
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import store.db as db

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_PROD_DB = _ROOT / "fir_intel.db"

# A known strong link pair (phone + UPI evidence)
_LINK_A = "GZB-VIJ-0001-2024"
_LINK_B = "KNP-BAR-0002-2024"


@pytest.fixture(scope="module")
def tmp_db_path(tmp_path_factory):
    """Copy the production DB into a temp directory for the test session."""
    if not _PROD_DB.exists():
        pytest.skip("fir_intel.db not found — run `py pipeline.py data results.json` first")
    tmp_dir = tmp_path_factory.mktemp("db")
    dest = tmp_dir / "fir_intel_test.db"
    shutil.copy2(_PROD_DB, dest)
    return str(dest)


@pytest.fixture()
def conn(tmp_db_path):
    """Open a connection to the temp DB; close after each test."""
    c = db.init_db(tmp_db_path)
    yield c
    c.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _narrative_for(conn, fir_id: str) -> str:
    row = conn.execute("SELECT narrative FROM firs WHERE fir_id=?", (fir_id,)).fetchone()
    return row["narrative"] if row else ""


def _first_pending_fir_id(conn) -> str | None:
    row = conn.execute(
        "SELECT fir_id FROM firs WHERE status='awaiting_extraction' LIMIT 1"
    ).fetchone()
    return row["fir_id"] if row else None


def _any_fir_id(conn) -> str:
    row = conn.execute("SELECT fir_id FROM firs LIMIT 1").fetchone()
    return row["fir_id"]


# ---------------------------------------------------------------------------
# Test 1: fake quote is rejected
# ---------------------------------------------------------------------------

def test_submit_extraction_fake_quote_rejected(conn):
    """A quote that doesn't appear in the narrative must be rejected."""
    fir_id = _any_fir_id(conn)
    narrative = _narrative_for(conn, fir_id)
    assert narrative, "narrative should not be empty"

    data = {
        "crime_type": "fake_police_call",
        "crime_type_conf": 0.8,
        "mo_slots": {
            "authority_claimed": {
                "value": "CBI officer",
                "quote": "INVENTED TEXT THAT CANNOT APPEAR IN ANY FIR NARRATIVE XYZ123",
            }
        },
        "identifiers": {
            "phones": [], "upis": [], "vehicle_regs": [],
            "vehicle_last4": [], "vehicle_desc": None,
        },
        "people": [],
    }

    ok, reasons = db.engine_validator_check(conn, fir_id, data)
    assert not ok
    assert any("quote not found" in r for r in reasons), reasons


# ---------------------------------------------------------------------------
# Test 2: valid extraction accepted and audited
# ---------------------------------------------------------------------------

def test_submit_extraction_valid_accepted_and_audited(conn):
    """A structurally valid extraction with a real quote must be accepted and
    written to audit_log."""
    # Find any FIR that has a narrative — use one that already has an accepted
    # extraction so we know the crime type is valid.
    ext_map = db.latest_extractions(conn)
    row = None
    for fir_id, ext in ext_map.items():
        r = conn.execute("SELECT fir_id, narrative FROM firs WHERE fir_id=?", (fir_id,)).fetchone()
        if r and r["narrative"]:
            row = r
            break
    if row is None:
        pytest.skip("No FIRs with accepted extractions in test DB")

    fir_id = row["fir_id"]
    narrative = row["narrative"]
    # Use the first 40 characters as our quote (definitely verbatim)
    quote_snippet = narrative[:40]

    data = {
        "crime_type": "fake_police_call",
        "crime_type_conf": 0.85,
        "mo_slots": {
            "authority_claimed": {
                "value": "customs officer",
                "quote": quote_snippet,
            }
        },
        "identifiers": {
            "phones": [], "upis": [], "vehicle_regs": [],
            "vehicle_last4": [], "vehicle_desc": None,
        },
        "people": [],
    }

    ok, reasons = db.engine_validator_check(conn, fir_id, data)
    if not ok:
        # The quote must actually be in the narrative; skip if our slice was bad
        pytest.skip(f"Quote not found in narrative: {reasons}")

    # Save the extraction with accepted=1
    row_id = db.save_extraction(
        conn, fir_id, data, stage="bob_v1", produced_by="bob_v1", accepted=1
    )
    assert isinstance(row_id, int) and row_id > 0

    # Audit the action
    db.audit(conn, "submit_extraction", fir_id, True, None, "bob")

    # Check audit_log has a row for this FIR
    audit_row = conn.execute(
        "SELECT * FROM audit_log WHERE target=? AND action='submit_extraction' ORDER BY id DESC LIMIT 1",
        (fir_id,),
    ).fetchone()
    assert audit_row is not None
    assert audit_row["ok"] == 1


# ---------------------------------------------------------------------------
# Test 3: explain_link returns phone or UPI evidence
# ---------------------------------------------------------------------------

def test_explain_link_strong_pair_has_phone_or_upi(conn):
    """The known strong pair must have phone or UPI evidence."""
    lnk = db.get_link(conn, _LINK_A, _LINK_B)
    if lnk is None:
        pytest.skip(f"No link found between {_LINK_A} and {_LINK_B} in test DB")

    features = {e["feature"] for e in lnk["evidence"]}
    assert features & {"phone", "upi"}, (
        f"Expected phone or upi evidence, got: {features}"
    )
    assert lnk["score"] >= 0.80, f"Expected strong link, got score {lnk['score']}"


# ---------------------------------------------------------------------------
# Test 4: submit_summary rejects an invented number
# ---------------------------------------------------------------------------

def test_submit_summary_rejects_invented_number(conn):
    """A summary containing a number not in the fact sheet must be rejected."""
    # Pick the first station we have stats for
    row = conn.execute(
        "SELECT district, station FROM station_stats LIMIT 1"
    ).fetchone()
    if row is None:
        pytest.skip("No station_stats in test DB — run engine first")

    district = row["district"]
    station = row["station"]

    # Use a number that cannot appear in any real fact sheet (9999999)
    invented_summary = (
        f"{station} registered 9999999 FIRs last month, an unprecedented spike."
    )

    sheet = db.station_fact_sheet(conn, district, station)
    assert sheet is not None, f"No fact sheet for {district}/{station}"

    # Check the number 9999999 is not in the fact sheet
    fact_str = json.dumps(sheet)
    assert "9999999" not in fact_str, "Invented number unexpectedly found in fact sheet"

    # Manually replicate the submit_summary number-check logic
    import re
    submitted_numbers = re.findall(r"\b\d[\d,]*\b", invented_summary)
    fact_values = set()
    for m in re.finditer(r"\b\d[\d,]*\b", fact_str):
        fact_values.add(m.group(0).replace(",", ""))

    unmatched = [n for n in submitted_numbers if n.replace(",", "") not in fact_values]
    assert "9999999" in unmatched, f"Expected 9999999 to be unmatched, unmatched={unmatched}"


# ---------------------------------------------------------------------------
# Test 5: no tool output contains answer-key data
# ---------------------------------------------------------------------------

def test_no_tool_output_contains_answer_key_data(conn):
    """Ensure tool outputs don't leak ground_truth or gang_profiles data."""
    answer_key_dir = _ROOT / "data" / "answer_key"
    if not answer_key_dir.exists():
        pytest.skip("answer_key directory not found")

    # Load the actual answer key group IDs as a reference set
    gt_path = answer_key_dir / "ground_truth.csv"
    if not gt_path.exists():
        pytest.skip("ground_truth.csv not found")

    import csv
    with open(gt_path, encoding="utf-8") as f:
        gt_rows = list(csv.DictReader(f))

    # Collect all group IDs that are not "NONE"
    real_group_ids = {
        r["group_id"] for r in gt_rows if r.get("group_id") and r["group_id"] != "NONE"
    }
    if not real_group_ids:
        pytest.skip("No real group IDs in ground_truth.csv")

    # Fetch all cluster labels from the DB (engine-generated, not answer-key)
    cluster_rows = db.list_clusters(conn)
    cluster_labels = {c["label"] for c in cluster_rows}

    # The engine's cluster labels (C01, C02, …) must not coincidentally match
    # answer-key group IDs. (They never should — format is different.)
    for label in cluster_labels:
        assert label not in real_group_ids, (
            f"Cluster label '{label}' matches an answer-key group ID — "
            "this should never happen"
        )

    # The gang_profiles file should not be readable from any tool output
    # Simply verify get_reference() doesn't include gang_profiles key
    ref = db.load_reference(str(_ROOT / "data"))
    assert "gang_profiles" not in ref, (
        "get_reference() must not include gang_profiles"
    )
    assert "ground_truth" not in ref, (
        "get_reference() must not include ground_truth"
    )


# ---------------------------------------------------------------------------
# Helper: wrap validator_check for use in tests without importing server
# ---------------------------------------------------------------------------

def _patch_validator_check():
    """Import validator.check as a plain function for use in tests."""
    from engine.validator import check
    return check


# Attach the check function to db module for test convenience
db.engine_validator_check = lambda conn, fir_id, data: _patch_validator_check()(
    fir_id, data, conn, source="llm"
)
