"""ingest/loader.py — load and validate firs.json.

Exports
-------
REQUIRED    List of field names that every raw FIR record must contain.
load(path)  Open firs.json in *path*, validate, deduplicate, normalise, and return
            (normalised_firs, rejects, raw_valid).

            normalised_firs  list of canonical in-memory dicts (via normalize_fir)
            rejects          list of {"fir_id": …, "reason": …} dicts
            raw_valid        list of the original raw dicts that passed validation
                             (used by the store layer to persist source data)
"""
import json
import os

from ingest.normalize import normalize_fir

REQUIRED = [
    "fir_id",
    "district",
    "police_station",
    "date_reported",
    "date_occurred",
    "sections",
    "complainant",
    "accused",
    "property",
    "narrative",
]


def load(path: str) -> tuple[list[dict], list[dict], list[dict]]:
    """Load firs.json from *path*, validate, deduplicate and normalise every record.

    Returns
    -------
    normalised_firs : list[dict]
        Canonical in-memory FIR dicts ready for the pipeline stages.
    rejects : list[dict]
        Records that failed validation, each with ``fir_id`` and ``reason``.
    raw_valid : list[dict]
        The original raw dicts (as read from JSON) for every record that passed
        validation — used by the store layer to persist unmodified source data.
    """
    raw_all = json.load(open(os.path.join(path, "firs.json"), encoding="utf-8"))

    firs: list[dict] = []
    rejects: list[dict] = []
    raw_valid: list[dict] = []
    seen: set[str] = set()

    for r in raw_all:
        # Required-field check
        missing = [k for k in REQUIRED if k not in r]
        if missing:
            rejects.append({"fir_id": r.get("fir_id"), "reason": f"missing {missing}"})
            continue

        # Deduplication
        if r["fir_id"] in seen:
            rejects.append({"fir_id": r["fir_id"], "reason": "duplicate fir_id"})
            continue

        seen.add(r["fir_id"])
        raw_valid.append(r)
        firs.append(normalize_fir(r))

    return firs, rejects, raw_valid
