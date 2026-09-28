"""ingest/normalize.py — canonical normalisation of a raw FIR dict.

Exports
-------
canon_phone(s)      Normalise a phone string to a 10-digit Indian mobile number or None.
age_band(a)         Map an integer age to a display band string.
normalize_fir(raw)  Transform a validated raw FIR dict into the canonical in-memory shape.

Rule 2 (unknown accused → None) is enforced here.
"""
import re
from datetime import datetime

# Names that mean "identity unknown" — Rule 2.
_UNKNOWN_NAMES = {"unknown", "unknown person", "अज्ञात", "n/a", "not known", ""}


def canon_phone(s: str | None) -> str | None:
    """Return a normalised 10-digit Indian mobile number, or None if invalid / not a mobile."""
    if not s:
        return None
    d = re.sub(r"\D", "", s)
    if len(d) > 10 and d.startswith("91"):
        d = d[2:]
    if len(d) == 11 and d.startswith("0"):
        d = d[1:]
    return d if re.fullmatch(r"[6-9]\d{9}", d) else None


def age_band(a: int | None) -> str:
    """Map an integer age to a display band string."""
    if a is None:
        return "unknown"
    for lo, hi in [(0, 17), (18, 29), (30, 44), (45, 59)]:
        if a <= hi:
            return f"{lo}-{hi}"
    return "60+"


def normalize_fir(raw: dict) -> dict:
    """Return the canonical in-memory FIR dict from a validated raw record.

    The canonical shape is what the rest of the pipeline (classify, link, …) works with.
    Rule 2: accused name is set to None when it signals an unknown person.
    """
    occ = datetime.fromisoformat(
        f"{raw['date_occurred']}T{raw.get('time_occurred') or '00:00'}"
    )
    rep = datetime.fromisoformat(
        f"{raw['date_reported']}T{raw.get('time_reported') or '00:00'}"
    )

    # First accused entry (pipeline currently works with a single accused)
    a = raw["accused"][0] if raw["accused"] else {}
    status = a.get("status", "unknown")
    name = a.get("name")
    # Rule 2: normalise unknown-person placeholders to None
    if status == "unknown" or (name or "").strip().lower() in _UNKNOWN_NAMES:
        name = None

    c = raw["complainant"]
    return {
        "id": raw["fir_id"],
        "district": raw["district"],
        "station": raw["police_station"],
        "occurred": occ.isoformat(),
        "reported": rep.isoformat(),
        "delay_h": round((rep - occ).total_seconds() / 3600, 1),
        "date_conflict": rep < occ,
        "sections": raw["sections"],
        "language": raw.get("language"),
        "victim": {
            "name": c.get("name"),
            "age": c.get("age"),
            "age_band": age_band(c.get("age")),
            "gender": c.get("gender"),
            "occupation": c.get("occupation"),
            "area": c.get("address"),
            "phone": canon_phone(c.get("phone")),
        },
        "accused": {
            "name": name,
            "alias": a.get("alias"),
            "father": a.get("father_name"),
            "phone": canon_phone(a.get("phone")),
            "status": status,
        },
        "property": raw["property"],
        "narrative": raw["narrative"],
    }
