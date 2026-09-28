"""engine/link.py — FIR-pair linking with noisy-OR evidence combination.

Rules enforced here
-------------------
Rule 2: accused names that are null (already normalised by ingest) are never
        used for linking — the `if n:` guard in the person block covers this.
Rule 3: MO similarity is added to `feats` only when the pair already has at
        least one hard-evidence feature. It can never be the sole reason for a
        link.

Additionally:
- Hub identifiers (seen in >= HUB_MIN_TYPES distinct crime types) are
  down-weighted to 0.3× their nominal weight.
- vehicle_reg matches are skipped when either FIR is a vehicle_theft (the
  victim's registration is the stolen vehicle, not an accused asset).

Exports
-------
link(firs, sim) -> (links: list[dict], hubs: set[tuple])
"""
import collections
import itertools
import math
import re

from rapidfuzz import fuzz

from engine.config import W, HUB_MIN_TYPES, ATTACH_MO_MIN


def link(
    firs: list[dict],
    sim: dict[tuple[str, str], float],
) -> tuple[list[dict], set[tuple]]:
    """Compute pairwise FIR links from shared hard identifiers.

    Parameters
    ----------
    firs : list[dict]
        Normalised FIR dicts with ``f["x"]`` extraction sub-dict and
        ``f["crime_type"]`` already set.
    sim  : dict
        Pairwise MO cosine similarity from engine.mo.mo_similarity().

    Returns
    -------
    links : list[dict]
        Each link has keys: a, b, score, kind, evidence.
    hubs  : set[tuple[str, str]]
        Identifiers treated as hubs, formatted as (key_type, value).
    """
    idx = {f["id"]: f for f in firs}

    # Detect hub identifiers: phones and UPI IDs seen across >= HUB_MIN_TYPES crime types
    id_types: dict[tuple, set] = collections.defaultdict(set)
    for f in firs:
        for k in ("phones", "upis"):
            for v in f["x"][k]:
                id_types[(k, v)].add(f["crime_type"])
    hubs = {k for k, v in id_types.items() if len(v) >= HUB_MIN_TYPES}

    # Build blocking index
    blocks: dict[tuple, set] = collections.defaultdict(set)
    for f in firs:
        for p in f["x"]["phones"]:
            blocks[("phone", p)].add(f["id"])
        for u in f["x"]["upis"]:
            blocks[("upi", u)].add(f["id"])
        for v in f["x"]["vehicle_regs"]:
            blocks[("vehicle_reg", v)].add(f["id"])
        for v in f["x"]["vehicle_last4"]:
            blocks[("vehicle_last4", v)].add(f["id"])
        n = f["accused"]["name"]
        if n:  # Rule 2: null accused names are never used for linking
            blocks[("person", re.sub(r"[^a-z ]", "", n.lower()).strip())].add(f["id"])

    pairs: dict[tuple, dict] = {}

    def add(a: str, b: str, feat: str, w: float, detail: str) -> None:
        pairs.setdefault(tuple(sorted((a, b))), {}).setdefault(feat, (w, detail))

    for (kind, val), ids in blocks.items():
        for a, b in itertools.combinations(sorted(ids), 2):
            fa, fb = idx[a], idx[b]

            if kind in ("phone", "upi"):
                hub = ((kind + "s"), val) in hubs
                weight = W[kind] * (0.3 if hub else 1)
                label = (
                    f"same {'UPI ID' if kind == 'upi' else 'phone'} {val}"
                    + (" (hub, down-weighted)" if hub else "")
                )
                add(a, b, kind, weight, label)

            elif kind == "vehicle_reg":
                # Skip: victim's own plate registered in a theft FIR is not an accused asset
                if "vehicle_theft" in (fa["crime_type"], fb["crime_type"]):
                    continue
                add(a, b, kind, W[kind], f"same vehicle {val}")

            elif kind == "vehicle_last4":
                da, db = fa["x"]["vehicle_desc"], fb["x"]["vehicle_desc"]
                if da and db and fuzz.token_set_ratio(da, db) >= 80:
                    add(a, b, "vehicle_last4_desc", W["vehicle_last4_desc"], f"{da} ending {val}")
                else:
                    add(a, b, "vehicle_last4", W["vehicle_last4"], f"vehicle ending {val}")

            else:  # person
                ca, cb = fa["accused"], fb["accused"]
                strong = (
                    (ca["father"] and cb["father"] and fuzz.ratio(ca["father"], cb["father"]) >= 85)
                    or (ca["alias"] and cb["alias"] and fuzz.ratio(ca["alias"].lower(), cb["alias"].lower()) >= 85)
                )
                feat = "person_strong" if strong else "person_name"
                detail = (
                    f"accused {ca['name']}"
                    + (f", father {ca['father']}" if strong and ca["father"] else "")
                    + ("" if strong else " (name only)")
                )
                add(a, b, feat, W[feat], detail)

    links: list[dict] = []
    for (a, b), feats in pairs.items():
        # Rule 3: add MO support only when a hard-evidence link already exists
        if idx[a]["crime_type"] == idx[b]["crime_type"] and sim.get((a, b), 0) >= ATTACH_MO_MIN:
            feats["mo_support"] = (W["mo_support"], f"similar MO wording ({sim[(a, b)]:.2f})")

        score = 1 - math.prod(1 - w for w, _ in feats.values())
        links.append({
            "a": a,
            "b": b,
            "score": round(score, 3),
            "kind": "evidence",
            "evidence": [
                {"feature": k, "weight": round(w, 2), "detail": d}
                for k, (w, d) in feats.items()
            ],
        })

    return links, hubs
