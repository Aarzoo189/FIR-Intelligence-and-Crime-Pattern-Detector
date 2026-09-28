"""engine/validator.py — extraction payload validator.

Enforces Rules 2, 4 and the reference-file constraints on every extraction
payload before it is accepted into the store.

Returns *all* failure reasons, not just the first, so callers can give
useful feedback when submitting corrections.

Exports
-------
check(fir_id, data, conn, source="llm") -> (ok: bool, reasons: list[str])

    fir_id : str            FIR to validate against.
    data   : dict           Extraction payload matching extraction_schema.json.
    conn   : sqlite3.Connection  Open DB connection (read-only use).
    source : str            "llm" (default) or "rule_baseline".
                            When source == "rule_baseline", the quote check is
                            skipped (rule extraction produces no quotes), but
                            all other checks still apply.
"""
import json
import re
import unicodedata
from pathlib import Path

# ---------------------------------------------------------------------------
# Reference data — loaded once at import time
# ---------------------------------------------------------------------------

_DATA_DIR = Path(__file__).parents[1] / "data"

# Valid crime type IDs
with open(_DATA_DIR / "taxonomy.json", encoding="utf-8") as _f:
    _VALID_CRIME_TYPES: set[str] = {
        ct["id"] for ct in json.load(_f)["crime_types"]
    }

# Valid slot names per crime type
with open(_DATA_DIR / "mo_schema.json", encoding="utf-8") as _f:
    _raw_schema = json.load(_f)["slots_by_crime_type"]
    _VALID_SLOTS: dict[str, set[str]] = {
        ct: {s["slot"] for s in slots}
        for ct, slots in _raw_schema.items()
    }

# Stoplist phone numbers (raw strings from the file, not canonicalised)
with open(_DATA_DIR / "stoplist.json", encoding="utf-8") as _f:
    _STOPLIST: set[str] = {e["number"] for e in json.load(_f)["stoplist"]}

# Unknown-accused name placeholders — Rule 2
_UNKNOWN_NAMES: set[str] = {"unknown", "unknown person", "अज्ञात", "n/a", "not known", ""}

# Canonical phone pattern
_PHONE_RE = re.compile(r"^[6-9]\d{9}$")
# UPI ID pattern (lowercase enforcement checked separately)
_UPI_RE = re.compile(r"^[\w.]+@[a-z]+$")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _norm(text: str) -> str:
    """NFC-normalise and collapse runs of whitespace."""
    text = unicodedata.normalize("NFC", text)
    return re.sub(r"\s+", " ", text).strip()


def _quote_present(quote: str, narrative: str) -> bool:
    """Return True if the normalised quote appears word-for-word in the normalised narrative."""
    return _norm(quote) in _norm(narrative)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def check(
    fir_id: str,
    data: dict,
    conn,
    source: str = "llm",
) -> tuple[bool, list[str]]:
    """Validate an extraction payload against its FIR narrative and reference files.

    Parameters
    ----------
    fir_id  : str                FIR identifier.
    data    : dict               Extraction payload (see extraction_schema.json).
    conn    : sqlite3.Connection Open DB connection; used to fetch the narrative.
    source  : str                "llm" or "rule_baseline".  When "rule_baseline",
                                 the verbatim-quote check (Rule 4) is skipped.

    Returns
    -------
    ok      : bool          True only when no failures were found.
    reasons : list[str]     All failure messages (empty when ok is True).
    """
    from store.db import get_fir  # imported here to avoid circular import at module load

    reasons: list[str] = []
    skip_quotes = (source == "rule_baseline")

    # --- Fetch narrative from store ---
    row = get_fir(conn, fir_id)
    if row is None:
        return False, [f"fir_id '{fir_id}' not found in store"]
    narrative = row["narrative"]

    # --- Required top-level keys ---
    required_keys = {"crime_type", "crime_type_conf", "mo_slots", "identifiers", "people"}
    missing = required_keys - set(data.keys())
    if missing:
        reasons.append(f"missing required keys: {sorted(missing)}")

    # --- crime_type ---
    ct = data.get("crime_type")
    if ct is not None:
        if ct not in _VALID_CRIME_TYPES:
            reasons.append(f"unknown crime_type '{ct}' — not in taxonomy")
    else:
        ct = None  # already reported as missing above

    # --- MO slots ---
    mo_slots = data.get("mo_slots") or {}
    if not isinstance(mo_slots, dict):
        reasons.append("mo_slots must be an object")
    else:
        allowed_slots = _VALID_SLOTS.get(ct, set()) if ct else set()
        for slot_name, slot_val in mo_slots.items():
            if ct and slot_name not in allowed_slots:
                reasons.append(
                    f"slot '{slot_name}' is not allowed for crime_type '{ct}'"
                )
            if isinstance(slot_val, dict):
                quote = slot_val.get("quote")
                if not skip_quotes:
                    if not quote:
                        reasons.append(f"slot '{slot_name}' is missing a quote (Rule 4)")
                    elif not _quote_present(quote, narrative):
                        reasons.append(
                            f"slot '{slot_name}' quote not found in narrative: «{quote[:80]}»"
                        )
            else:
                reasons.append(f"slot '{slot_name}' value must be an object with 'value' and 'quote'")

    # --- Identifiers ---
    identifiers = data.get("identifiers") or {}
    if not isinstance(identifiers, dict):
        reasons.append("identifiers must be an object")
    else:
        # Phones
        for phone in identifiers.get("phones") or []:
            if not isinstance(phone, str) or not _PHONE_RE.fullmatch(phone):
                reasons.append(
                    f"phone '{phone}' is not a valid 10-digit Indian mobile (must start 6–9)"
                )
            elif phone in _STOPLIST:
                reasons.append(f"phone '{phone}' is in the stoplist")

        # UPI IDs
        for upi in identifiers.get("upis") or []:
            if not isinstance(upi, str):
                reasons.append(f"UPI ID {upi!r} must be a string")
            elif upi != upi.lower():
                reasons.append(f"UPI ID '{upi}' must be lowercase")
            elif not _UPI_RE.fullmatch(upi):
                reasons.append(f"UPI ID '{upi}' does not match the expected UPI pattern")

        # Vehicle registrations
        for vreg in identifiers.get("vehicle_regs") or []:
            if not isinstance(vreg, str):
                reasons.append(f"vehicle_reg {vreg!r} must be a string")
            elif " " in vreg:
                reasons.append(f"vehicle_reg '{vreg}' must not contain spaces")
            elif vreg != vreg.upper():
                reasons.append(f"vehicle_reg '{vreg}' must be uppercase")

    # --- People ---
    people = data.get("people") or []
    if not isinstance(people, list):
        reasons.append("people must be an array")
    else:
        for i, person in enumerate(people):
            if not isinstance(person, dict):
                reasons.append(f"people[{i}] must be an object")
                continue
            name = person.get("name") or ""
            # Rule 2: unknown-person placeholders must never be stored as people
            if name.strip().lower() in _UNKNOWN_NAMES:
                reasons.append(
                    f"people[{i}] has an unknown-person name '{name}' (Rule 2)"
                )
            if not skip_quotes:
                quote = person.get("quote")
                if not quote:
                    reasons.append(f"people[{i}] ('{name}') is missing a quote (Rule 4)")
                elif not _quote_present(quote, narrative):
                    reasons.append(
                        f"people[{i}] ('{name}') quote not found in narrative: «{quote[:80]}»"
                    )

    return len(reasons) == 0, reasons
