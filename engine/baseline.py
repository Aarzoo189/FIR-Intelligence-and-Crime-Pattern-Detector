"""engine/baseline.py — rule-based crime-type classifier.

This is the stand-in for Bob's LLM extraction stage. It is used only for
testing and the initial baseline seed run. Logic is intentionally kept
identical to the original pipeline.py implementation so the acceptance
numbers remain stable.

Exports
-------
classify(fir) -> (crime_type: str, confidence: float, conflict: bool)
"""
import collections


def classify(fir: dict) -> tuple[str, float, bool]:
    """Classify a normalised FIR dict into one of the taxonomy crime types.

    Parameters
    ----------
    fir : dict
        A normalised FIR dict (as returned by ingest.normalize.normalize_fir).
        Must have keys: 'sections', 'narrative', 'property'.

    Returns
    -------
    crime_type : str    — taxonomy id, e.g. 'cyber_fraud_kyc'
    confidence : float  — share of total votes going to the top type (0.0–1.0)
    conflict   : bool   — True when a second type tied with the top vote count
    """
    s = set(x.upper() for x in fir["sections"])
    t = fir["narrative"].lower()
    p = fir["property"]["type"].lower()

    v: collections.Counter = collections.Counter()

    if "BNS 304(2)" in s or "chain" in p:
        v["chain_snatching"] += 2
    if {"BNS 331(4)", "BNS 305(A)"} & s:
        v["house_burglary"] += 2
    if "atm" in p or "atm card" in t:
        v["atm_card_swap"] += 2
    if s == {"BNS 303(2)"} or any(k in p for k in ["pulsar", "splendor", "activa", "motorcycle", "scooty"]):
        v["vehicle_theft"] += 2
    if "BNS 308(2)" in s or any(k in t for k in ["police", "cbi", "customs", "officer", "arrest", "laundering"]):
        v["fake_police_call"] += 2
    if any(k in t for k in ["job", "task", "telegram", "work from home", "security deposit"]):
        v["job_scam"] += 2
    if any(k in t for k in ["kyc", "anydesk", "bank executive", "reward points", "card block"]):
        v["cyber_fraud_kyc"] += 2

    if not v:
        return "other", 0.0, False

    (top, n), *rest = v.most_common()
    confidence = round(n / sum(v.values()), 2)
    conflict = bool(rest) and rest[0][1] == n
    return top, confidence, conflict
