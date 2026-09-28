"""ingest/rule_extract.py — rule-based identifier extraction from a FIR narrative.

Exports
-------
PHONE_RX    Compiled regex for phone numbers (also used by pipeline.py's mo_tokens).
UPI_RX      Compiled regex for UPI IDs.
VREG_RX     Compiled regex for UP vehicle registration plates.
rule_extract(fir)  Extract phones, UPI IDs, vehicle refs and amounts from a
                   normalised FIR dict; return an extraction sub-dict.
"""
import re

from ingest.normalize import canon_phone

# ---------------------------------------------------------------------------
# Compiled regular expressions
# ---------------------------------------------------------------------------

PHONE_RX = re.compile(r"(?:\+?91[\s-]?)?[6-9]\d{4}[\s-]?\d{5}")
UPI_RX = re.compile(
    r"\b[\w.]+@(?:ybl|oksbi|okaxis|okhdfcbank|okicici|paytm|upi|apl|ibl|axl)\b",
    re.IGNORECASE,
)
VREG_RX = re.compile(r"\bUP[\s-]?\d{1,2}[\s-]?[A-Z]{1,3}[\s-]?\d{4}\b", re.IGNORECASE)
VLAST_RX = re.compile(r"ending\s+(\d{4})\b", re.IGNORECASE)
AMT_RX = re.compile(r"Rs\.?\s?([\d,]+)")

_COLORS = r"\b(white|black|grey|gray|red|blue|silver|green|yellow)\b"
VDESC_RX = re.compile(
    _COLORS
    + r"\s+((?:swift dzire|dzire|pulsar|splendor|activa|alto|wagonr|bolero|scorpio|apache)"
    r"(?:\s(?:car|motorcycle|bike))?|scooty|scooter|motorcycle|bike|car)\b",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Extraction function
# ---------------------------------------------------------------------------

def rule_extract(f: dict) -> dict:
    """Extract hard identifiers and amounts from a normalised FIR dict.

    Parameters
    ----------
    f : dict
        A normalised FIR dict as returned by ``normalize_fir``.

    Returns
    -------
    dict with keys:
        phones          list[str]  — 10-digit mobile numbers found in narrative
                                     (accused phone appended if not already present)
        upis            list[str]  — UPI IDs found in narrative
        vehicle_regs    list[str]  — Full UP registration plates found
        vehicle_last4   list[str]  — Last-4-digit plate fragments from "ending NNNN"
        vehicle_desc    str|None   — First colour+make description found
        amounts         list[int]  — Rupee amounts found
        evidence        list[dict] — Span-level evidence records for the validator
    """
    text = f["narrative"]
    phones: list[str] = []
    upis: list[str] = []
    vregs: list[str] = []
    vlast: list[str] = []
    evidence: list[dict] = []

    for m in PHONE_RX.finditer(text):
        p = canon_phone(m.group(0))
        if p and p != f["victim"]["phone"]:
            phones.append(p)
            evidence.append({"kind": "phone", "value": p, "start": m.start(), "end": m.end()})

    for m in UPI_RX.finditer(text):
        u = m.group(0).lower()
        upis.append(u)
        evidence.append({"kind": "upi", "value": u, "start": m.start(), "end": m.end()})

    for m in VREG_RX.finditer(text):
        v = re.sub(r"[\s-]", "", m.group(0)).upper()
        vregs.append(v)
        evidence.append({"kind": "vehicle_reg", "value": v, "start": m.start(), "end": m.end()})

    for m in VLAST_RX.finditer(text):
        vlast.append(m.group(1))
        evidence.append({"kind": "vehicle_last4", "value": m.group(1), "start": m.start(), "end": m.end()})

    vdesc: str | None = None
    m = VDESC_RX.search(text)
    if m:
        vdesc = re.sub(r"\s+", " ", m.group(0)).lower()
        evidence.append({"kind": "vehicle_desc", "value": vdesc, "start": m.start(), "end": m.end()})

    # Also include the accused phone (already normalised) if it is not already captured
    accused_phone = f["accused"]["phone"]
    if accused_phone and accused_phone not in phones:
        phones.append(accused_phone)

    amounts = [
        int(a.replace(",", ""))
        for a in AMT_RX.findall(text)
        if a.replace(",", "").isdigit()
    ]

    return {
        "phones": sorted(set(phones)),
        "upis": sorted(set(upis)),
        "vehicle_regs": sorted(set(vregs)),
        "vehicle_last4": sorted(set(vlast)),
        "vehicle_desc": vdesc,
        "amounts": amounts,
        "evidence": evidence,
    }
