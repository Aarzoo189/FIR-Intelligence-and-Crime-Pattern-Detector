"""engine/cluster.py — cluster building and offender signature generation.

Suggested attachments are always review-only: they are stored under
role='suggested' and never automatically joined to a cluster (Rule 3).

Exports
-------
build_clusters(firs, links, sim) -> list[dict]
    Build connected-component clusters from links whose score >= REVIEW,
    then compute suggested attachments for unlinked FIRs.

signatures(cl, idx) -> list[dict]
    Extract offender-level signals (phones, UPI IDs, vehicle fragments,
    named accused) that appear in 2+ FIRs within the same cluster.
"""
import collections
from datetime import datetime

import networkx as nx

from engine.config import REVIEW, ATTACH_MO_MIN


def build_clusters(
    firs: list[dict],
    links: list[dict],
    sim: dict[tuple[str, str], float],
) -> list[dict]:
    """Build clusters from evidence links and compute review-only suggestions.

    Parameters
    ----------
    firs  : list[dict]  Normalised FIR dicts with .x, .crime_type, .occurred.
    links : list[dict]  Output of engine.link.link().
    sim   : dict        Pairwise MO similarity from engine.mo.mo_similarity().

    Returns
    -------
    list of cluster dicts, each with keys:
        id, label, members (set), crime_type, edges, suggested
    """
    idx = {f["id"]: f for f in firs}
    G: nx.Graph = nx.Graph()
    G.add_nodes_from(idx)

    for lnk in links:
        if lnk["score"] >= REVIEW:
            G.add_edge(lnk["a"], lnk["b"], **lnk)

    # Connected components with >= 2 nodes, sorted largest-first then by min id
    comps = sorted(
        [c for c in nx.connected_components(G) if len(c) >= 2],
        key=lambda c: (-len(c), min(c)),
    )

    clusters: list[dict] = []
    for i, c in enumerate(comps):
        members = [idx[n] for n in c]
        clusters.append({
            "id": i,
            "label": f"C{i + 1:02d}",
            "members": set(c),
            "crime_type": collections.Counter(
                x["crime_type"] for x in members
            ).most_common(1)[0][0],
            "edges": [G.edges[e] for e in G.subgraph(c).edges],
            "suggested": [],
        })

    # Suggested attachments — always sent for review, never auto-joined
    for f in firs:
        if G.degree(f["id"]) > 0:
            continue  # already in a cluster

        best: tuple | None = None
        for cl in clusters:
            members = [idx[n] for n in cl["members"]]

            if f["crime_type"] != cl["crime_type"]:
                continue

            d = datetime.fromisoformat(f["occurred"])
            cl_dates = [datetime.fromisoformat(x["occurred"]) for x in members]
            if not (min(cl_dates) <= d <= max(cl_dates)):
                continue

            if f["district"] not in {x["district"] for x in members}:
                continue

            vd = {x["x"]["vehicle_desc"] for x in members} - {None}
            if f["x"]["vehicle_desc"] and vd and f["x"]["vehicle_desc"] not in vd:
                continue

            s = max(sim.get((f["id"], n), 0) for n in cl["members"])
            if s >= ATTACH_MO_MIN and (best is None or s > best[0]):
                best = (s, cl)

        if best:
            s, cl = best
            why = [
                "same crime type",
                "inside the cluster's date range",
                f"district {f['district']} already in cluster",
                f"MO wording similarity {s:.2f}",
            ]
            if f["x"]["vehicle_desc"]:
                why.append(f"same getaway vehicle: {f['x']['vehicle_desc']}")
            cl["suggested"].append({
                "fir": f["id"],
                "mo_sim": round(s, 2),
                "reasons": why,
            })

    return clusters


def signatures(cl: dict, idx: dict) -> list[dict]:
    """Extract offender signals that appear in 2+ FIRs of a cluster.

    Parameters
    ----------
    cl  : dict  A cluster dict produced by build_clusters().
    idx : dict  Mapping fir_id -> normalised FIR dict.

    Returns
    -------
    list of signal dicts, each with keys:
        kind, value, cluster, firs, fir_count, districts, last_seen, loss_inr
    """
    sig: dict[tuple, set] = collections.defaultdict(set)

    for n in cl["members"]:
        x = idx[n]["x"]
        a = idx[n]["accused"]

        for p in x["phones"]:
            sig[("Phone", p)].add(n)
        for u in x["upis"]:
            sig[("UPI ID", u)].add(n)
        for v in x["vehicle_last4"]:
            prefix = (x["vehicle_desc"] + " ") if x["vehicle_desc"] else ""
            sig[("Vehicle", f"{prefix}ending {v}")].add(n)
        if a["name"]:
            label = a["name"]
            if a["alias"]:
                label += f" urf {a['alias']}"
            if a["father"]:
                label += f", s/o {a['father']}"
            sig[("Person", label)].add(n)

    out: list[dict] = []
    for (kind, val), fir_ids in sig.items():
        if len(fir_ids) >= 2:
            out.append({
                "kind": kind,
                "value": val,
                "cluster": cl["label"],
                "firs": sorted(fir_ids),
                "fir_count": len(fir_ids),
                "districts": sorted({idx[n]["district"] for n in fir_ids}),
                "last_seen": max(idx[n]["occurred"] for n in fir_ids)[:10],
                "loss_inr": sum(
                    idx[n]["property"].get("value_inr") or 0 for n in fir_ids
                ),
            })

    return out
