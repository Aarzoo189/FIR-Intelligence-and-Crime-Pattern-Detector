"""tests/test_validator.py — unit tests for engine.validator.check().

Each test uses an in-memory SQLite DB seeded with a single FIR row so the
validator can fetch the narrative.  The narrative used is based on a real
structure from firs.json but with fictional identifiers (Rule 6).
"""
import sqlite3
import sys
import os

import pytest

# Ensure the project root is on sys.path when running from the repo root
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from engine.validator import check, _STOPLIST
from store.db import init_db, upsert_fir

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_FIR_ID = "TEST-001-2024"

_RAW_FIR = {
    "fir_id": _FIR_ID,
    "district": "Lucknow",
    "police_station": "Hazratganj",
    "date_reported": "2024-08-04",
    "time_reported": "00:55",
    "date_occurred": "2024-08-02",
    "time_occurred": "15:45",
    "sections": ["BNS 308(2)", "BNS 319(2)", "IT Act 66D"],
    "complainant": {
        "name": "Test Complainant",
        "father_or_husband_name": "Test Father",
        "age": 45,
        "gender": "F",
        "occupation": "Doctor",
        "address": "Aliganj, Lucknow",
        "phone": "7000028922",
    },
    "accused": [{"name": "Unknown", "alias": None, "father_name": None, "phone": None, "status": "unknown"}],
    "place_of_occurrence": "Aliganj, Lucknow",
    "property": {"type": "cash (online transfer)", "value_inr": 366500},
    "narrative": (
        "The complainant received a call from mobile number 98765 29973 on 02-08-2024. "
        "The caller posed as a customs officer and said that the complainant's Aadhaar was "
        "linked to a money laundering case. He kept the complainant on a video call and "
        "demanded money for verification. The complainant transferred Rs 366,500 to UPI ID jobs22@ybl."
    ),
    "language": "en",
}

# Minimal valid extraction payload that passes all checks
_VALID_DATA = {
    "crime_type": "fake_police_call",
    "crime_type_conf": 0.9,
    "mo_slots": {
        "authority_claimed": {
            "value": "customs officer",
            "quote": "The caller posed as a customs officer",
        },
        "pretext": {
            "value": "Aadhaar linked to money laundering case",
            "quote": "the complainant's Aadhaar was linked to a money laundering case",
        },
    },
    "identifiers": {
        "phones": ["9876529973"],
        "upis": ["jobs22@ybl"],
        "vehicle_regs": [],
        "vehicle_last4": [],
        "vehicle_desc": None,
    },
    "people": [],
}


@pytest.fixture()
def conn():
    """In-memory SQLite DB with one FIR row."""
    c = init_db(":memory:")
    upsert_fir(c, _RAW_FIR)
    yield c
    c.close()


# ---------------------------------------------------------------------------
# Test 1: quote not in narrative
# ---------------------------------------------------------------------------

def test_quote_not_in_narrative(conn):
    data = {
        **_VALID_DATA,
        "mo_slots": {
            "authority_claimed": {
                "value": "police officer",
                "quote": "The caller claimed to be an IPS officer",  # NOT in narrative
            },
        },
    }
    ok, reasons = check(_FIR_ID, data, conn, source="llm")
    assert not ok
    assert any("quote not found" in r for r in reasons), reasons


# ---------------------------------------------------------------------------
# Test 2: unknown crime type
# ---------------------------------------------------------------------------

def test_unknown_crime_type(conn):
    data = {**_VALID_DATA, "crime_type": "arson"}
    ok, reasons = check(_FIR_ID, data, conn, source="llm")
    assert not ok
    assert any("unknown crime_type" in r for r in reasons), reasons


# ---------------------------------------------------------------------------
# Test 3: bad phone format
# ---------------------------------------------------------------------------

def test_bad_phone(conn):
    data = {
        **_VALID_DATA,
        "identifiers": {
            **_VALID_DATA["identifiers"],
            "phones": ["12345"],  # too short and wrong prefix
        },
    }
    ok, reasons = check(_FIR_ID, data, conn, source="llm")
    assert not ok
    assert any("12345" in r for r in reasons), reasons


# ---------------------------------------------------------------------------
# Test 4: stoplist phone
# The stoplist contains short numbers (3–4 digits) that can never be valid
# 10-digit mobiles.  We test the guard by temporarily injecting a real phone
# into _STOPLIST.
# ---------------------------------------------------------------------------

def test_stoplist_phone(conn, monkeypatch):
    test_phone = "9876529973"
    # Patch the module-level set
    import engine.validator as v_mod
    original = set(v_mod._STOPLIST)
    monkeypatch.setattr(v_mod, "_STOPLIST", original | {test_phone})

    data = {
        **_VALID_DATA,
        "identifiers": {
            **_VALID_DATA["identifiers"],
            "phones": [test_phone],
        },
    }
    ok, reasons = check(_FIR_ID, data, conn, source="llm")
    assert not ok
    assert any("stoplist" in r for r in reasons), reasons


# ---------------------------------------------------------------------------
# Test 5: "Unknown" accused name (Rule 2)
# ---------------------------------------------------------------------------

def test_unknown_accused_name(conn):
    data = {
        **_VALID_DATA,
        "people": [
            {
                "name": "Unknown",
                "alias": None,
                "father_name": None,
                "role": "accused",
                "quote": "the complainant's Aadhaar was linked to a money laundering case",
            }
        ],
    }
    ok, reasons = check(_FIR_ID, data, conn, source="llm")
    assert not ok
    assert any("Rule 2" in r for r in reasons), reasons


# ---------------------------------------------------------------------------
# Test 6: valid extraction — should pass
# ---------------------------------------------------------------------------

def test_valid_extraction_passes(conn):
    ok, reasons = check(_FIR_ID, _VALID_DATA, conn, source="llm")
    assert ok, f"Expected ok=True but got reasons: {reasons}"
    assert reasons == []
