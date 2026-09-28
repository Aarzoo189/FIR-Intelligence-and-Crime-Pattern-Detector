"""engine/run.py — main intelligence engine entry point.

run_engine(conn, firs) orchestrates the full pipeline:
  MO similarity → linking → clustering → signatures → analytics
and saves all results to the SQLite store via store.db helpers.

It returns the same result dict shape that pipeline.py needs to build
results.json, so the output format is unchanged.

Exports
-------
run_engine(conn, firs) -> dict
"""
import collections
import sqlite3

from engine.analytics import analytics, narrate
from engine.cluster import build_clusters, signatures
from engine.config import ENGINE_VERSION, STRONG, REVIEW
from engine.link import link
from engine.mo import mo_similarity
from store.db import (
    next_version,
    save_clusters,
    save_links,
    save_offenders,
    save_station_stats,
)


def run_engine(conn: sqlite3.Connection, firs: list[dict]) -> dict:
    """Run the full intelligence pipeline over *firs* and persist results.

    Parameters
    ----------
    conn : sqlite3.Connection
        Open DB connection returned by store.db.init_db().
    firs : list[dict]
        Normalised FIR dicts.  Each must already have:
          - f["x"]          — extraction sub-dict (from ingest.rule_extract)
          - f["crime_type"] — classified crime type string

    Returns
    -------
    dict with keys:
        links     — link summary counts (total, strong, review, weak)
        raw_links — full link list (for results.json)
        hubs      — hub identifier set
        clusters  — list of cluster dicts (with members set, edges, suggested)
        offenders — sorted list of offender signal dicts
        months    — sorted list of 'YYYY-MM' strings
        stations  — list of station analytics dicts (with summary injected)
    """
    ver = next_version(conn, "links")

    # ---- Stage 6: MO similarity (supporting evidence only — Rule 3) ----
    sim = mo_similarity(firs)

    # ---- Stages 7-8: entity resolution and pair linking ----
    raw_links, hubs = link(firs, sim)

    # ---- Stage 9: clusters, suggested attachments, offender signatures ----
    clusters = build_clusters(firs, raw_links, sim)
    idx = {f["id"]: f for f in firs}
    offenders = [o for c in clusters for o in signatures(c, idx)]
    offenders.sort(key=lambda o: (-o["fir_count"], -len(o["districts"]), o["last_seen"]))

    # ---- Stages 10-11: analytics and narration ----
    months, stations = analytics(firs)
    where = {n: c["label"] for c in clusters for n in c["members"]}
    for s in stations:
        k = sum(
            1 for f in firs
            if f["station"] == s["station"]
            and f["district"] == s["district"]
            and f["id"] in where
        )
        s["clustered_firs"] = k
        s["summary"] = narrate(s, k)

    # ---- Persist to store (Rule 5: insert-only, versioned) ----
    save_links(conn, raw_links, ver, ENGINE_VERSION)
    save_clusters(conn, clusters, ver, ENGINE_VERSION)
    save_offenders(conn, offenders, ver, ENGINE_VERSION)
    save_station_stats(conn, stations, ver, ENGINE_VERSION)

    link_summary = {
        "total": len(raw_links),
        "strong": sum(lnk["score"] >= STRONG for lnk in raw_links),
        "review": sum(REVIEW <= lnk["score"] < STRONG for lnk in raw_links),
        "weak": sum(lnk["score"] < REVIEW for lnk in raw_links),
    }

    return {
        "links": link_summary,
        "raw_links": raw_links,
        "hubs": hubs,
        "clusters": clusters,
        "offenders": offenders,
        "months": months,
        "stations": stations,
    }
