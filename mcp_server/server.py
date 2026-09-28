"""mcp_server/server.py — FIR Intelligence MCP server (Layer 4).

This module is a thin transport layer.  All logic lives in store/ and engine/.

Setup notes
-----------
- Project root is added to sys.path so store/ and engine/ are importable.
- Database path: absolute project root / fir_intel.db, overridable via
  the FIR_INTEL_DB environment variable.
- Each tool opens and closes its own connection; no shared global connection.
- NEVER print to stdout (breaks MCP); all diagnostic output goes to stderr.
- All tool return values are JSON-safe (sets/tuples converted to lists).
- Errors are returned as {"error": "..."}, never as uncaught exceptions.
- Every write operation calls db.audit().

Tool inventory (read)
---------------------
1.  get_reference()
2.  get_pending_firs(n=10)
3.  get_fir(fir_id)
4.  search_firs(district, station, crime_type, date_from, date_to, text, limit=20)
5.  list_clusters()
6.  explain_link(fir_a, fir_b)
7.  list_offenders(limit=20)
8.  station_fact_sheet(district, station)

Tool inventory (write)
-----------------------
9.  submit_extraction(fir_id, data, force=False)
10. run_linking()
11. submit_summary(district, station, text)
12. review_suggestion(cluster_label, fir_id, decision, reason)
"""
import os
import sys
import json
import re
import traceback
from pathlib import Path

# ---------------------------------------------------------------------------
# Add project root to sys.path so store/, engine/, ingest/ are importable
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# ---------------------------------------------------------------------------
# Database path — absolute, overridable via environment variable
# ---------------------------------------------------------------------------
_DB_PATH = os.environ.get("FIR_INTEL_DB") or str(_PROJECT_ROOT / "fir_intel.db")
_DATA_DIR = str(_PROJECT_ROOT / "data")

# ---------------------------------------------------------------------------
# Imports (after sys.path is set)
# ---------------------------------------------------------------------------
import store.db as db
from engine.config import ENGINE_VERSION, STRONG, REVIEW
from engine.validator import check as validator_check
from mcp.server.mcpserver import MCPServer

# ---------------------------------------------------------------------------
# Server setup
# ---------------------------------------------------------------------------
mcp = MCPServer("fir-intel")

_ACTOR = "bob"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _open() -> "sqlite3.Connection":
    """Open (and migrate if needed) the database for one tool call."""
    return db.init_db(_DB_PATH)


def _has_firs(conn) -> bool:
    row = conn.execute("SELECT COUNT(*) FROM firs").fetchone()
    return (row[0] or 0) > 0


def _json_safe(obj):
    """Recursively convert sets and tuples to lists for JSON serialisation."""
    if isinstance(obj, (set, frozenset, tuple)):
        return [_json_safe(v) for v in obj]
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_json_safe(v) for v in obj]
    return obj


def _link_tier(score: float) -> str:
    if score >= STRONG:
        return "strong"
    if score >= REVIEW:
        return "review"
    return "weak"


# ---------------------------------------------------------------------------
# Read tool 1: get_reference
# ---------------------------------------------------------------------------

@mcp.tool()
def get_reference() -> dict:
    """Return the crime taxonomy, MO slot schema, extraction JSON schema and
    the verbatim-quote rule.  Call this once at the start of an extraction
    session so you know the allowed crime types and slot names."""
    try:
        conn = _open()
        try:
            if not _has_firs(conn):
                return {"error": "No FIRs in database. Run `py pipeline.py data results.json` first."}
            return _json_safe(db.load_reference(_DATA_DIR))
        finally:
            conn.close()
    except Exception as e:
        print(traceback.format_exc(), file=sys.stderr)
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# Read tool 2: get_pending_firs
# ---------------------------------------------------------------------------

@mcp.tool()
def get_pending_firs(n: int = 10) -> list[dict] | dict:
    """Return up to *n* FIRs awaiting extraction.

    Each entry includes fir_id, district, station, dates, sections, property,
    language, narrative, the accused list (Unknown normalised to null per
    Rule 2) and any rule-found identifiers from the baseline extraction."""
    try:
        conn = _open()
        try:
            if not _has_firs(conn):
                return {"error": "No FIRs in database. Run `py pipeline.py data results.json` first."}
            return _json_safe(db.pending_firs(conn, n))
        finally:
            conn.close()
    except Exception as e:
        print(traceback.format_exc(), file=sys.stderr)
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# Read tool 3: get_fir
# ---------------------------------------------------------------------------

@mcp.tool()
def get_fir(fir_id: str) -> dict:
    """Return the full FIR for *fir_id*, its latest accepted extraction,
    cluster membership and linked FIRs with evidence."""
    try:
        conn = _open()
        try:
            result = db.get_fir_detail(conn, fir_id)
            if result is None:
                return {"error": f"FIR '{fir_id}' not found"}
            return _json_safe(result)
        finally:
            conn.close()
    except Exception as e:
        print(traceback.format_exc(), file=sys.stderr)
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# Read tool 4: search_firs
# ---------------------------------------------------------------------------

@mcp.tool()
def search_firs(
    district: str | None = None,
    station: str | None = None,
    crime_type: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    text: str | None = None,
    limit: int = 20,
) -> list[dict] | dict:
    """Search FIRs by district, station, crime type, date range or narrative
    keyword.  Returns summaries only — no bulk narratives."""
    try:
        conn = _open()
        try:
            return _json_safe(
                db.search_firs(conn, district, station, crime_type,
                               date_from, date_to, text, limit)
            )
        finally:
            conn.close()
    except Exception as e:
        print(traceback.format_exc(), file=sys.stderr)
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# Read tool 5: list_clusters
# ---------------------------------------------------------------------------

@mcp.tool()
def list_clusters() -> list[dict] | dict:
    """List all clusters from the latest engine run.

    Each entry includes label, crime type, size, districts, stations, date
    range, strength score, member FIR IDs and suggested-attachment FIR IDs."""
    try:
        conn = _open()
        try:
            return _json_safe(db.list_clusters(conn))
        finally:
            conn.close()
    except Exception as e:
        print(traceback.format_exc(), file=sys.stderr)
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# Read tool 6: explain_link
# ---------------------------------------------------------------------------

@mcp.tool()
def explain_link(fir_a: str, fir_b: str) -> dict:
    """Return the link score, tier (strong ≥0.80 / review ≥0.50 / weak) and
    evidence features between *fir_a* and *fir_b*.

    If there is no direct link, reports whether they share a cluster."""
    try:
        conn = _open()
        try:
            lnk = db.get_link(conn, fir_a, fir_b)
            if lnk:
                return _json_safe({
                    "fir_a": lnk["fir_a"],
                    "fir_b": lnk["fir_b"],
                    "score": lnk["score"],
                    "tier": _link_tier(lnk["score"]),
                    "evidence": lnk["evidence"],
                })
            # No direct link — check for shared cluster
            ver_row = conn.execute("SELECT MAX(version) FROM clusters").fetchone()
            ver = ver_row[0] or 0
            def _cluster_of(fid):
                r = conn.execute(
                    "SELECT cluster_label FROM clusters WHERE fir_id=? AND version=? AND role='member'",
                    (fid, ver),
                ).fetchone()
                return r["cluster_label"] if r else None
            ca, cb = _cluster_of(fir_a), _cluster_of(fir_b)
            shared = (ca is not None and ca == cb)
            return {
                "linked": False,
                "message": f"No direct link between {fir_a} and {fir_b}.",
                "shared_cluster": shared,
                "cluster_a": ca,
                "cluster_b": cb,
            }
        finally:
            conn.close()
    except Exception as e:
        print(traceback.format_exc(), file=sys.stderr)
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# Read tool 7: list_offenders
# ---------------------------------------------------------------------------

@mcp.tool()
def list_offenders(limit: int = 20) -> list[dict] | dict:
    """Return the top offender signals ranked by FIR count, then number of
    districts, then most-recent last-seen date."""
    try:
        conn = _open()
        try:
            return _json_safe(db.list_offenders(conn, limit))
        finally:
            conn.close()
    except Exception as e:
        print(traceback.format_exc(), file=sys.stderr)
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# Read tool 8: station_fact_sheet
# ---------------------------------------------------------------------------

@mcp.tool()
def station_fact_sheet(district: str, station: str) -> dict:
    """Return computed analytics for one police station: monthly series,
    total, Poisson-detected spikes, crime mix, total loss, clustered-FIR
    count and top-3 victim demographics."""
    try:
        conn = _open()
        try:
            sheet = db.station_fact_sheet(conn, district, station)
            if sheet is None:
                return {"error": f"No stats found for {district} / {station}. Run the engine first."}
            return _json_safe(sheet)
        finally:
            conn.close()
    except Exception as e:
        print(traceback.format_exc(), file=sys.stderr)
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# Write tool 9: submit_extraction
# ---------------------------------------------------------------------------

@mcp.tool()
def submit_extraction(fir_id: str, data: dict, force: bool = False) -> dict:
    """Validate and save an extraction for one FIR.

    Runs engine.validator.check() first.  If it passes, saves with
    stage='bob_v1', accepted=1, and sets FIR status to 'extracted'.

    Refuses FIRs already extracted by bob_v1 unless force=True.
    """
    try:
        conn = _open()
        try:
            # Guard: refuse if already extracted by bob_v1 (unless force)
            if not force:
                existing = conn.execute(
                    "SELECT id FROM extractions WHERE fir_id=? AND stage='bob_v1' AND accepted=1",
                    (fir_id,),
                ).fetchone()
                if existing:
                    db.audit(conn, "submit_extraction_refused", fir_id, False,
                             {"reason": "already extracted by bob_v1; use force=True to override"},
                             _ACTOR)
                    return {
                        "accepted": False,
                        "reasons": ["This FIR already has an accepted bob_v1 extraction. Pass force=True to override."],
                    }

            ok, reasons = validator_check(fir_id, data, conn, source="llm")
            db.audit(conn, "submit_extraction", fir_id, ok,
                     {"reasons": reasons} if not ok else None, _ACTOR)

            if ok:
                db.save_extraction(
                    conn, fir_id, data,
                    stage="bob_v1",
                    produced_by="bob_v1",
                    accepted=1,
                )
                db.set_fir_status(conn, fir_id, "extracted", actor=_ACTOR)

            return {"accepted": ok, "reasons": reasons}
        finally:
            conn.close()
    except Exception as e:
        print(traceback.format_exc(), file=sys.stderr)
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# Write tool 10: run_linking
# ---------------------------------------------------------------------------

@mcp.tool()
def run_linking() -> dict:
    """Re-run the intelligence engine using the latest accepted extractions.

    bob_v1 extractions take priority over rule_baseline for FIRs that have
    both.  Returns clusters, links by tier, offenders, suggestions and the
    run version."""
    try:
        conn = _open()
        try:
            if not _has_firs(conn):
                return {"error": "No FIRs in database."}

            # Reconstruct in-memory FIR list from the store
            from ingest.normalize import normalize_fir, age_band
            all_firs_raw = conn.execute(
                """SELECT fir_id, district, police_station,
                          date_reported, time_reported,
                          date_occurred, time_occurred,
                          sections_json, complainant_json, accused_json,
                          property_json, narrative, language
                   FROM firs ORDER BY fir_id"""
            ).fetchall()

            ext_map = db.latest_extractions(conn)
            firs = []
            for raw_row in all_firs_raw:
                raw = dict(raw_row)
                raw["sections"] = json.loads(raw.pop("sections_json"))
                raw["complainant"] = json.loads(raw.pop("complainant_json"))
                raw["accused"] = json.loads(raw.pop("accused_json"))
                raw["property"] = json.loads(raw.pop("property_json"))
                # Build a shape compatible with normalize_fir's expected input
                raw_for_norm = {
                    "fir_id": raw["fir_id"],
                    "district": raw["district"],
                    "police_station": raw["police_station"],
                    "date_reported": raw["date_reported"],
                    "time_reported": raw.get("time_reported"),
                    "date_occurred": raw["date_occurred"],
                    "time_occurred": raw.get("time_occurred"),
                    "sections": raw["sections"],
                    "complainant": raw["complainant"],
                    "accused": raw["accused"],
                    "property": raw["property"],
                    "narrative": raw["narrative"],
                    "language": raw.get("language"),
                }
                fir = normalize_fir(raw_for_norm)

                ext = ext_map.get(fir["id"])
                if ext:
                    ids = ext.get("identifiers_json") or {}
                    fir["x"] = {
                        "phones": ids.get("phones") or [],
                        "upis": ids.get("upis") or [],
                        "vehicle_regs": ids.get("vehicle_regs") or [],
                        "vehicle_last4": ids.get("vehicle_last4") or [],
                        "vehicle_desc": ids.get("vehicle_desc"),
                        "amounts": [],
                        "evidence": [],
                    }
                    fir["crime_type"] = ext.get("crime_type") or "other"
                else:
                    # Fallback: use rule extraction
                    from ingest.rule_extract import rule_extract
                    from engine.baseline import classify
                    fir["x"] = rule_extract(fir)
                    fir["crime_type"], _, _ = classify(fir)

                firs.append(fir)

            from engine.run import run_engine
            result = run_engine(conn, firs)

            db.audit(conn, "run_linking", "all", True,
                     {"version": db.next_version(conn, "links") - 1,
                      "fir_count": len(firs)}, _ACTOR)

            ver = db.next_version(conn, "links") - 1
            raw_links = result["raw_links"]

            # Build cluster summary
            clusters_out = []
            for cl in result["clusters"]:
                clusters_out.append({
                    "label": cl["label"],
                    "crime_type": cl["crime_type"],
                    "size": len(cl["members"]),
                    "members": list(cl["members"]),
                    "suggested": cl.get("suggested") or [],
                })

            return _json_safe({
                "version": ver,
                "clusters": clusters_out,
                "links": {
                    "total": len(raw_links),
                    "strong": [lnk for lnk in raw_links if lnk["score"] >= STRONG],
                    "review": [lnk for lnk in raw_links if REVIEW <= lnk["score"] < STRONG],
                    "weak": [lnk for lnk in raw_links if lnk["score"] < REVIEW],
                },
                "offenders": result["offenders"],
                "suggestions": [
                    {"cluster": cl["label"], "fir": s["fir"], "mo_sim": s["mo_sim"], "reasons": s["reasons"]}
                    for cl in result["clusters"]
                    for s in (cl.get("suggested") or [])
                ],
            })
        finally:
            conn.close()
    except Exception as e:
        print(traceback.format_exc(), file=sys.stderr)
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# Write tool 11: submit_summary
# ---------------------------------------------------------------------------

@mcp.tool()
def submit_summary(district: str, station: str, text: str) -> dict:
    """Save a Bob-authored station narrative summary.

    Validates that every number in *text* (counts, amounts, months,
    percentages) can be found in the station fact sheet.  If any number
    cannot be accounted for, rejects with the unmatched numbers.
    """
    try:
        conn = _open()
        try:
            sheet = db.station_fact_sheet(conn, district, station)
            if sheet is None:
                return {"error": f"No fact sheet for {district} / {station}. Run the engine first."}

            # Collect all numbers from the submitted text
            submitted_numbers = re.findall(r"\b\d[\d,]*\b", text)

            # Collect all numbers that appear in the fact sheet (as strings)
            fact_values: set[str] = set()
            # Flatten the fact sheet into a big string and extract numbers
            fact_str = json.dumps(sheet)
            for m in re.finditer(r"\b\d[\d,]*\b", fact_str):
                fact_values.add(m.group(0).replace(",", ""))

            # Also check plain-text month names (e.g. "July 2024")
            fact_months = set()
            if sheet.get("spikes"):
                for sp in sheet["spikes"]:
                    fact_months.add(sp["month"])  # YYYY-MM

            unmatched: list[str] = []
            for num in submitted_numbers:
                bare = num.replace(",", "")
                if bare not in fact_values:
                    unmatched.append(num)

            if unmatched:
                db.audit(conn, "submit_summary_rejected", f"{district}/{station}", False,
                         {"unmatched": unmatched}, _ACTOR)
                return {
                    "accepted": False,
                    "reason": "The following numbers in the summary could not be verified against the fact sheet.",
                    "unmatched_numbers": unmatched,
                }

            # Save a new station_stats version with summary_source="bob"
            ver = db.next_version(conn, "station_stats")
            # Clone existing fact sheet into new version with updated summary
            conn.execute(
                """
                INSERT INTO station_stats (
                    district, station,
                    series_json, total, spikes_json, mix_json, loss_inr,
                    summary, summary_source,
                    version, produced_by, created_at
                )
                SELECT district, station,
                       series_json, total, spikes_json, mix_json, loss_inr,
                       ?, 'bob',
                       ?, 'bob', datetime('now')
                FROM station_stats
                WHERE district = ? AND station = ?
                ORDER BY version DESC
                LIMIT 1
                """,
                (text, ver, district, station),
            )
            conn.commit()
            db.audit(conn, "submit_summary", f"{district}/{station}", True,
                     {"version": ver}, _ACTOR)
            return {"accepted": True, "version": ver}
        finally:
            conn.close()
    except Exception as e:
        print(traceback.format_exc(), file=sys.stderr)
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# Write tool 12: review_suggestion
# ---------------------------------------------------------------------------

@mcp.tool()
def review_suggestion(
    cluster_label: str,
    fir_id: str,
    decision: str,
    reason: str = "",
) -> dict:
    """Record an analyst decision on a cluster suggestion.

    *decision* must be 'confirmed' or 'rejected'.
    Inserts a row into the decisions table only; never edits existing rows
    (Rule 5).
    """
    try:
        if decision not in ("confirmed", "rejected"):
            return {"error": "decision must be 'confirmed' or 'rejected'"}

        conn = _open()
        try:
            entity_id = f"{cluster_label}/{fir_id}"
            conn.execute(
                """
                INSERT INTO decisions
                    (entity_type, entity_id, decision, reason, analyst, version, created_at)
                VALUES ('suggestion', ?, ?, ?, ?, 1, datetime('now'))
                """,
                (entity_id, decision, reason, _ACTOR),
            )
            conn.commit()
            db.audit(conn, "review_suggestion", entity_id, True,
                     {"decision": decision, "reason": reason}, _ACTOR)
            return {"recorded": True, "entity_id": entity_id, "decision": decision}
        finally:
            conn.close()
    except Exception as e:
        print(traceback.format_exc(), file=sys.stderr)
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # Validate DB is reachable before handing control to the MCP runtime.
    # Any startup error goes to stderr, never stdout.
    try:
        _conn = db.init_db(_DB_PATH)
        if not _has_firs(_conn):
            print(
                f"WARNING: Database at {_DB_PATH} has no FIRs. "
                "Run `py pipeline.py data results.json` first.",
                file=sys.stderr,
            )
        _conn.close()
    except Exception as _e:
        print(f"ERROR: Could not open database at {_DB_PATH}: {_e}", file=sys.stderr)
        sys.exit(1)

    mcp.run()
