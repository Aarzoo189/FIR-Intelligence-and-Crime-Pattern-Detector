"""engine/seed_baseline.py — populate extractions from rule-based baseline.

Runs rule_extract() and classify() on every FIR that does not yet have an
accepted extraction, validates the result (skipping the quote check because
rule extraction produces no quotes), and saves accepted extractions with
stage='rule_baseline'.

Exports
-------
seed_baseline(conn, firs) -> (passed: int, failed: int)
"""
import sqlite3

from engine.baseline import classify
from engine.config import ENGINE_VERSION
from engine.validator import check
from ingest.rule_extract import rule_extract
from store.db import (
    audit,
    latest_extractions,
    next_version,
    save_extraction,
    set_fir_status,
)

_STAGE = "rule_baseline"


def seed_baseline(
    conn: sqlite3.Connection,
    firs: list[dict],
) -> tuple[int, int]:
    """Run the rule baseline on each FIR and save accepted extractions.

    FIRs that already have an accepted extraction at any stage are skipped
    so re-running is safe and idempotent.

    The quote check (Rule 4) is skipped for this stage because rule extraction
    does not produce narrative quotes.  All other checks apply: phone format,
    stoplist, unknown accused names, crime_type in taxonomy.

    Parameters
    ----------
    conn : sqlite3.Connection  Open DB connection from store.db.init_db().
    firs : list[dict]          Normalised FIR dicts (from ingest.loader.load()).

    Returns
    -------
    passed : int   Number of FIRs whose baseline extraction was saved.
    failed : int   Number of FIRs whose baseline extraction failed validation.
    """
    existing = latest_extractions(conn)
    ver = next_version(conn, "extractions")
    passed = 0
    failed = 0

    for fir in firs:
        fir_id = fir["id"]

        # Skip if this FIR already has an accepted extraction
        if fir_id in existing:
            continue

        # Run rule extraction (attaches to fir["x"] in-memory as well)
        x = rule_extract(fir)
        fir["x"] = x

        # Run rule classifier
        crime_type, conf, _ = classify(fir)
        fir["crime_type"] = crime_type
        fir["ct_conf"] = conf

        # Build extraction payload (no quotes or people — rule baseline)
        data = {
            "crime_type": crime_type,
            "crime_type_conf": conf,
            "mo_slots": {},   # rule baseline produces no MO slots
            "identifiers": {
                "phones": x["phones"],
                "upis": x["upis"],
                "vehicle_regs": x["vehicle_regs"],
                "vehicle_last4": x["vehicle_last4"],
                "vehicle_desc": x["vehicle_desc"],
            },
            "people": [],     # rule baseline produces no named people
        }

        # Validate — source="rule_baseline" skips the quote check only
        ok, reasons = check(fir_id, data, conn, source=_STAGE)

        if ok:
            save_extraction(
                conn,
                fir_id=fir_id,
                data=data,
                stage=_STAGE,
                produced_by=ENGINE_VERSION,
                accepted=1,
                version=ver,
            )
            set_fir_status(conn, fir_id, "extraction_complete", actor=ENGINE_VERSION)
            passed += 1
        else:
            audit(
                conn,
                action="validation_fail",
                target=fir_id,
                ok=False,
                details={"stage": _STAGE, "reasons": reasons},
                actor=ENGINE_VERSION,
            )
            failed += 1

    return passed, failed
