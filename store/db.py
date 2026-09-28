"""store/db.py — SQLite persistence layer for FIR Intel.

This is the only module that reads from or writes to the SQLite database.
No other module in the project should open the database directly.

Public API
----------
Core
  init_db(db_path)                    -> sqlite3.Connection
  upsert_fir(conn, raw_fir)           -> None
  get_fir(conn, fir_id)               -> dict | None
  get_firs_by_status(conn, status)    -> list[dict]
  set_fir_status(conn, fir_id, status, actor) -> None  [only UPDATE allowed]

Extractions
  save_extraction(conn, fir_id, data, stage, produced_by, accepted, version) -> int
  latest_extractions(conn)            -> dict[fir_id, dict]

Links / clusters
  save_links(conn, links, version, produced_by)    -> None
  save_clusters(conn, clusters, version, produced_by) -> None

Offenders / station stats
  save_offenders(conn, offenders, version, produced_by)    -> None
  save_station_stats(conn, stats, version, produced_by)    -> None

Read back
  latest_run_results(conn)  -> dict with latest links, clusters, offenders, station_stats

Utilities
  next_version(conn, table) -> int
  audit(conn, action, target, ok, details, actor) -> None
"""
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

_SCHEMA_PATH = Path(__file__).parent / "schema.sql"

# JSON-serialised columns in the firs table
_FIRS_JSON_COLS = ("sections_json", "complainant_json", "accused_json", "property_json")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _row_to_dict(row: sqlite3.Row) -> dict:
    """Convert a sqlite3.Row to a plain dict, deserialising JSON columns."""
    d = dict(row)
    for col in _FIRS_JSON_COLS:
        if col in d and d[col] is not None:
            d[col] = json.loads(d[col])
    return d


def _add_column_if_missing(conn: sqlite3.Connection, table: str, col_def: str) -> None:
    """ALTER TABLE … ADD COLUMN, silently ignoring 'duplicate column name' errors."""
    col_name = col_def.split()[0]
    existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    if col_name not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {col_def}")


# ---------------------------------------------------------------------------
# Core API
# ---------------------------------------------------------------------------

def init_db(db_path: str) -> sqlite3.Connection:
    """Open (or create) the SQLite database at *db_path* and apply the schema.

    The schema uses CREATE TABLE IF NOT EXISTS so calling this on an existing
    database is safe.  New columns added to existing tables are applied via
    ALTER TABLE ADD COLUMN (idempotent).

    Returns
    -------
    sqlite3.Connection  with row_factory set to sqlite3.Row.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    ddl = _SCHEMA_PATH.read_text(encoding="utf-8")
    conn.executescript(ddl)
    # Migrate: add accepted column if upgrading from an older schema
    _add_column_if_missing(conn, "extractions", "accepted INTEGER NOT NULL DEFAULT 0")
    conn.commit()
    return conn


def upsert_fir(conn: sqlite3.Connection, raw: dict) -> None:
    """Persist a raw FIR dict with status = 'awaiting_extraction'.

    Uses INSERT OR REPLACE so re-ingesting the same fir_id updates source
    columns without affecting result tables that reference fir_id by FK.
    """
    conn.execute(
        """
        INSERT OR REPLACE INTO firs (
            fir_id, district, police_station,
            date_reported, time_reported,
            date_occurred, time_occurred,
            sections_json, complainant_json, accused_json, property_json,
            narrative, language,
            status, ingested_at
        ) VALUES (
            :fir_id, :district, :police_station,
            :date_reported, :time_reported,
            :date_occurred, :time_occurred,
            :sections_json, :complainant_json, :accused_json, :property_json,
            :narrative, :language,
            'awaiting_extraction', :ingested_at
        )
        """,
        {
            "fir_id": raw["fir_id"],
            "district": raw["district"],
            "police_station": raw["police_station"],
            "date_reported": raw["date_reported"],
            "time_reported": raw.get("time_reported"),
            "date_occurred": raw["date_occurred"],
            "time_occurred": raw.get("time_occurred"),
            "sections_json": json.dumps(raw["sections"], ensure_ascii=False),
            "complainant_json": json.dumps(raw["complainant"], ensure_ascii=False),
            "accused_json": json.dumps(raw["accused"], ensure_ascii=False),
            "property_json": json.dumps(raw["property"], ensure_ascii=False),
            "narrative": raw["narrative"],
            "language": raw.get("language"),
            "ingested_at": _now_utc(),
        },
    )
    conn.commit()


def get_fir(conn: sqlite3.Connection, fir_id: str) -> dict | None:
    """Fetch a single FIR row by fir_id; JSON columns are deserialised.

    Returns None if no row is found.
    """
    row = conn.execute(
        "SELECT * FROM firs WHERE fir_id = ?", (fir_id,)
    ).fetchone()
    return _row_to_dict(row) if row is not None else None


def get_firs_by_status(conn: sqlite3.Connection, status: str) -> list[dict]:
    """Return all FIR rows with the given status; JSON columns are deserialised."""
    rows = conn.execute(
        "SELECT * FROM firs WHERE status = ? ORDER BY date_reported, fir_id",
        (status,),
    ).fetchall()
    return [_row_to_dict(r) for r in rows]


def set_fir_status(
    conn: sqlite3.Connection,
    fir_id: str,
    status: str,
    actor: str = "system",
) -> None:
    """Update a FIR's status and record the change in audit_log.

    This is the only UPDATE permitted in the store.  It touches firs (source
    data), not any result table, and every change is logged.
    """
    conn.execute(
        "UPDATE firs SET status = ? WHERE fir_id = ?",
        (status, fir_id),
    )
    audit(conn, "status_change", fir_id, True, {"new_status": status}, actor)
    conn.commit()


# ---------------------------------------------------------------------------
# Extractions
# ---------------------------------------------------------------------------

def save_extraction(
    conn: sqlite3.Connection,
    fir_id: str,
    data: dict,
    stage: str,
    produced_by: str,
    accepted: int = 0,
    version: int | None = None,
) -> int:
    """Insert an extraction row and return its row id.

    Parameters
    ----------
    fir_id      : str   FIR this extraction belongs to.
    data        : dict  Extraction payload (crime_type, crime_type_conf,
                        mo_slots, identifiers, people).
    stage       : str   e.g. 'rule_baseline', 'llm_v1'.
    produced_by : str   Version string, e.g. ENGINE_VERSION.
    accepted    : int   1 = accepted for use by the engine, 0 = pending.
    version     : int   If None, next_version(conn, 'extractions') is used.
    """
    if version is None:
        version = next_version(conn, "extractions")

    identifiers = data.get("identifiers") or {}
    mo_slots = data.get("mo_slots") or {}
    people = data.get("people") or []

    # Build quotes_json from mo_slots and people quotes
    quotes: list[dict] = []
    for slot_name, slot_val in mo_slots.items():
        if isinstance(slot_val, dict) and slot_val.get("quote"):
            quotes.append({"field": f"mo_slot.{slot_name}", "quote": slot_val["quote"]})
    for person in people:
        if isinstance(person, dict) and person.get("quote"):
            quotes.append({"field": f"person.{person.get('name', '')}", "quote": person["quote"]})

    cur = conn.execute(
        """
        INSERT INTO extractions (
            fir_id, version, stage,
            crime_type, crime_type_conf,
            mo_slots_json, identifiers_json, quotes_json,
            produced_by, accepted, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            fir_id,
            version,
            stage,
            data.get("crime_type"),
            data.get("crime_type_conf"),
            json.dumps(mo_slots, ensure_ascii=False),
            json.dumps(identifiers, ensure_ascii=False),
            json.dumps(quotes, ensure_ascii=False),
            produced_by,
            accepted,
            _now_utc(),
        ),
    )
    conn.commit()
    return cur.lastrowid


def latest_extractions(conn: sqlite3.Connection) -> dict[str, dict]:
    """Return the latest accepted extraction for each fir_id.

    Returns a dict mapping fir_id -> extraction dict (with JSON columns
    deserialised).  FIRs with no accepted extraction are not included.
    """
    rows = conn.execute(
        """
        SELECT e.*
        FROM extractions e
        INNER JOIN (
            SELECT fir_id, MAX(version) AS max_ver
            FROM extractions
            WHERE accepted = 1
            GROUP BY fir_id
        ) latest ON e.fir_id = latest.fir_id AND e.version = latest.max_ver
        WHERE e.accepted = 1
        """
    ).fetchall()

    result: dict[str, dict] = {}
    for row in rows:
        d = dict(row)
        for col in ("mo_slots_json", "identifiers_json", "quotes_json"):
            if d.get(col) is not None:
                d[col] = json.loads(d[col])
        result[d["fir_id"]] = d
    return result


# ---------------------------------------------------------------------------
# Links / clusters
# ---------------------------------------------------------------------------

def save_links(
    conn: sqlite3.Connection,
    links: list[dict],
    version: int,
    produced_by: str,
) -> None:
    """Bulk-insert link rows (insert-only, Rule 5)."""
    now = _now_utc()
    conn.executemany(
        """
        INSERT INTO links (fir_a, fir_b, score, evidence_json, version, produced_by, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                lnk["a"],
                lnk["b"],
                lnk["score"],
                json.dumps(lnk["evidence"], ensure_ascii=False),
                version,
                produced_by,
                now,
            )
            for lnk in links
        ],
    )
    conn.commit()


def save_clusters(
    conn: sqlite3.Connection,
    clusters: list[dict],
    version: int,
    produced_by: str,
) -> None:
    """Bulk-insert cluster membership rows (insert-only, Rule 5).

    Members are stored with role='member'; suggested attachments with role='suggested'.
    """
    now = _now_utc()
    rows: list[tuple] = []
    for cl in clusters:
        for fir_id in cl["members"]:
            rows.append((cl["label"], fir_id, "member", version, produced_by, now))
        for sugg in cl.get("suggested") or []:
            rows.append((cl["label"], sugg["fir"], "suggested", version, produced_by, now))

    conn.executemany(
        """
        INSERT INTO clusters (cluster_label, fir_id, role, version, produced_by, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Offenders / station stats
# ---------------------------------------------------------------------------

def save_offenders(
    conn: sqlite3.Connection,
    offenders: list[dict],
    version: int,
    produced_by: str,
) -> None:
    """Bulk-insert offender signal rows (insert-only, Rule 5)."""
    now = _now_utc()
    conn.executemany(
        """
        INSERT INTO offenders (
            kind, value, cluster_label,
            firs_json, fir_count,
            districts_json, last_seen, loss_inr,
            version, produced_by, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                o["kind"],
                o["value"],
                o["cluster"],
                json.dumps(o["firs"], ensure_ascii=False),
                o["fir_count"],
                json.dumps(o["districts"], ensure_ascii=False),
                o["last_seen"],
                o.get("loss_inr") or 0,
                version,
                produced_by,
                now,
            )
            for o in offenders
        ],
    )
    conn.commit()


def save_station_stats(
    conn: sqlite3.Connection,
    stats: list[dict],
    version: int,
    produced_by: str,
) -> None:
    """Bulk-insert station analytics rows (insert-only, Rule 5)."""
    now = _now_utc()
    conn.executemany(
        """
        INSERT INTO station_stats (
            district, station,
            series_json, total,
            spikes_json, mix_json, loss_inr,
            summary, summary_source,
            version, produced_by, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                s["district"],
                s["station"],
                json.dumps(s["series"], ensure_ascii=False),
                s["total"],
                json.dumps(s["spikes"], ensure_ascii=False),
                json.dumps(s["mix"], ensure_ascii=False),
                s.get("loss_inr") or 0,
                s.get("summary"),
                produced_by,
                version,
                produced_by,
                now,
            )
            for s in stats
        ],
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Read back
# ---------------------------------------------------------------------------

def latest_run_results(conn: sqlite3.Connection) -> dict:
    """Fetch the latest version's links, clusters, offenders and station_stats.

    Returns a dict with keys 'version', 'links', 'clusters', 'offenders',
    'station_stats'.  Returns empty lists when no results exist.
    """
    def _max_ver(table: str) -> int:
        row = conn.execute(f"SELECT MAX(version) FROM {table}").fetchone()
        return row[0] or 0

    ver = _max_ver("links")
    if ver == 0:
        return {"version": 0, "links": [], "clusters": [], "offenders": [], "station_stats": []}

    links = [
        dict(r) for r in conn.execute(
            "SELECT * FROM links WHERE version = ?", (ver,)
        ).fetchall()
    ]
    for lnk in links:
        lnk["evidence_json"] = json.loads(lnk["evidence_json"])

    clusters = [
        dict(r) for r in conn.execute(
            "SELECT * FROM clusters WHERE version = ?", (ver,)
        ).fetchall()
    ]

    offenders = [
        dict(r) for r in conn.execute(
            "SELECT * FROM offenders WHERE version = ?", (ver,)
        ).fetchall()
    ]
    for o in offenders:
        o["firs_json"] = json.loads(o["firs_json"])
        o["districts_json"] = json.loads(o["districts_json"])

    station_stats = [
        dict(r) for r in conn.execute(
            "SELECT * FROM station_stats WHERE version = ?", (ver,)
        ).fetchall()
    ]
    for s in station_stats:
        for col in ("series_json", "spikes_json", "mix_json"):
            if s.get(col):
                s[col] = json.loads(s[col])

    return {
        "version": ver,
        "links": links,
        "clusters": clusters,
        "offenders": offenders,
        "station_stats": station_stats,
    }


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def next_version(conn: sqlite3.Connection, table: str) -> int:
    """Return MAX(version)+1 for the given table, or 1 if the table is empty."""
    row = conn.execute(f"SELECT MAX(version) FROM {table}").fetchone()
    return (row[0] or 0) + 1


def audit(
    conn: sqlite3.Connection,
    action: str,
    target: str,
    ok: bool,
    details: dict | None = None,
    actor: str = "system",
) -> None:
    """Insert one audit_log row."""
    conn.execute(
        """
        INSERT INTO audit_log (action, target, ok, details_json, actor, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            action,
            target,
            1 if ok else 0,
            json.dumps(details, ensure_ascii=False) if details else None,
            actor,
            _now_utc(),
        ),
    )
    # Note: caller is responsible for conn.commit() when batching; audit itself does not commit
    # to allow batching. For standalone audit calls, commit after.
    conn.commit()


# ---------------------------------------------------------------------------
# MCP server read helpers
# ---------------------------------------------------------------------------

def load_reference(data_dir: str) -> dict:
    """Return taxonomy, MO schema and extraction schema for the reference tool.

    Parameters
    ----------
    data_dir : str  Absolute path to the project's data/ directory.
    """
    import json as _json
    from pathlib import Path as _Path
    d = _Path(data_dir)
    engine_dir = d.parent / "engine"
    return {
        "taxonomy": _json.loads((d / "taxonomy.json").read_text(encoding="utf-8")),
        "mo_schema": _json.loads((d / "mo_schema.json").read_text(encoding="utf-8")),
        "extraction_schema": _json.loads(
            (engine_dir / "extraction_schema.json").read_text(encoding="utf-8")
        ),
        "quote_rule": (
            "Every extracted field (MO slots, people) must include a 'quote' key "
            "whose value is a verbatim substring of that FIR's narrative "
            "(Unicode NFC-normalised, whitespace collapsed). "
            "The validator rejects any field whose quote is not found word-for-word."
        ),
    }


def pending_firs(conn: sqlite3.Connection, n: int = 10) -> list[dict]:
    """Return up to *n* FIRs with status 'awaiting_extraction'.

    Includes fir_id, district, station, dates, sections, property, language,
    narrative, accused list (unknown normalised to null) and rule-found
    identifiers from the latest accepted extraction (if any).
    """
    rows = conn.execute(
        """
        SELECT fir_id, district, police_station,
               date_reported, time_reported, date_occurred, time_occurred,
               sections_json, accused_json, property_json,
               narrative, language
        FROM firs
        WHERE status = 'awaiting_extraction'
        ORDER BY date_reported, fir_id
        LIMIT ?
        """,
        (n,),
    ).fetchall()

    # Latest accepted extractions keyed by fir_id (for identifier preview)
    ext_map = latest_extractions(conn)

    result: list[dict] = []
    for row in rows:
        d = dict(row)
        d["sections"] = json.loads(d.pop("sections_json"))
        d["accused"] = _normalise_accused(json.loads(d.pop("accused_json")))
        d["property"] = json.loads(d.pop("property_json"))
        # Inject rule-found identifiers if a baseline extraction exists
        ext = ext_map.get(d["fir_id"])
        if ext and ext.get("identifiers_json"):
            d["rule_identifiers"] = ext["identifiers_json"]
        else:
            d["rule_identifiers"] = {}
        result.append(d)
    return result


def _normalise_accused(accused_list: list) -> list:
    """Apply Rule 2: replace unknown-person name placeholders with null."""
    _unknown = {"unknown", "unknown person", "अज्ञात", "n/a", "not known", ""}
    out = []
    for a in accused_list:
        entry = dict(a)
        if (entry.get("name") or "").strip().lower() in _unknown:
            entry["name"] = None
        out.append(entry)
    return out


def get_fir_detail(conn: sqlite3.Connection, fir_id: str) -> dict | None:
    """Return the full FIR row plus its latest accepted extraction, cluster
    membership and links with evidence.

    Returns None if the fir_id is not found.
    """
    row = conn.execute("SELECT * FROM firs WHERE fir_id = ?", (fir_id,)).fetchone()
    if row is None:
        return None

    d = dict(row)
    d["sections"] = json.loads(d.pop("sections_json"))
    d["complainant"] = json.loads(d.pop("complainant_json"))
    d["accused"] = _normalise_accused(json.loads(d.pop("accused_json")))
    d["property"] = json.loads(d.pop("property_json"))

    # Latest accepted extraction
    ext_map = latest_extractions(conn)
    ext = ext_map.get(fir_id)
    if ext:
        d["extraction"] = {
            "stage": ext.get("stage"),
            "crime_type": ext.get("crime_type"),
            "crime_type_conf": ext.get("crime_type_conf"),
            "mo_slots": ext.get("mo_slots_json") or {},
            "identifiers": ext.get("identifiers_json") or {},
            "produced_by": ext.get("produced_by"),
            "accepted": ext.get("accepted"),
        }
    else:
        d["extraction"] = None

    # Cluster membership (latest version)
    ver_row = conn.execute("SELECT MAX(version) FROM clusters").fetchone()
    ver = ver_row[0] or 0
    cluster_rows = conn.execute(
        "SELECT cluster_label, role FROM clusters WHERE fir_id = ? AND version = ?",
        (fir_id, ver),
    ).fetchall()
    d["clusters"] = [{"label": r["cluster_label"], "role": r["role"]} for r in cluster_rows]

    # Links with evidence (latest version)
    lver_row = conn.execute("SELECT MAX(version) FROM links").fetchone()
    lver = lver_row[0] or 0
    link_rows = conn.execute(
        """
        SELECT fir_a, fir_b, score, evidence_json
        FROM links
        WHERE (fir_a = ? OR fir_b = ?) AND version = ?
        ORDER BY score DESC
        """,
        (fir_id, fir_id, lver),
    ).fetchall()
    d["links"] = [
        {
            "fir_a": r["fir_a"],
            "fir_b": r["fir_b"],
            "score": r["score"],
            "evidence": json.loads(r["evidence_json"]),
        }
        for r in link_rows
    ]

    return d


def search_firs(
    conn: sqlite3.Connection,
    district: str | None = None,
    station: str | None = None,
    crime_type: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    text: str | None = None,
    limit: int = 20,
) -> list[dict]:
    """Return FIR summaries matching the given filters.  No bulk narratives.

    crime_type filters against the latest accepted extraction.
    text is a case-insensitive substring match against the narrative.
    """
    clauses: list[str] = []
    params: list = []

    if district:
        clauses.append("f.district = ?")
        params.append(district)
    if station:
        clauses.append("f.police_station = ?")
        params.append(station)
    if date_from:
        clauses.append("f.date_occurred >= ?")
        params.append(date_from)
    if date_to:
        clauses.append("f.date_occurred <= ?")
        params.append(date_to)
    if text:
        clauses.append("f.narrative LIKE ?")
        params.append(f"%{text}%")

    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""

    rows = conn.execute(
        f"""
        SELECT f.fir_id, f.district, f.police_station,
               f.date_reported, f.date_occurred,
               f.sections_json, f.property_json, f.language, f.status
        FROM firs f
        {where}
        ORDER BY f.date_reported DESC
        LIMIT ?
        """,
        params + [limit],
    ).fetchall()

    ext_map = latest_extractions(conn)

    result: list[dict] = []
    for row in rows:
        d = dict(row)
        d["sections"] = json.loads(d.pop("sections_json"))
        d["property"] = json.loads(d.pop("property_json"))
        ext = ext_map.get(d["fir_id"])
        d["crime_type"] = ext.get("crime_type") if ext else None
        # crime_type filter applied after join (SQLite has no JSON extract)
        if crime_type and d["crime_type"] != crime_type:
            continue
        result.append(d)

    return result


def get_link(conn: sqlite3.Connection, fir_a: str, fir_b: str) -> dict | None:
    """Return the latest link between two FIRs in either order.

    Returns None if no link exists.
    """
    ver_row = conn.execute("SELECT MAX(version) FROM links").fetchone()
    ver = ver_row[0] or 0
    if ver == 0:
        return None

    row = conn.execute(
        """
        SELECT fir_a, fir_b, score, evidence_json
        FROM links
        WHERE version = ?
          AND ((fir_a = ? AND fir_b = ?) OR (fir_a = ? AND fir_b = ?))
        ORDER BY score DESC
        LIMIT 1
        """,
        (ver, fir_a, fir_b, fir_b, fir_a),
    ).fetchone()

    if row is None:
        return None

    return {
        "fir_a": row["fir_a"],
        "fir_b": row["fir_b"],
        "score": row["score"],
        "evidence": json.loads(row["evidence_json"]),
    }


def list_clusters(conn: sqlite3.Connection) -> list[dict]:
    """Return cluster summaries for the latest engine run version."""
    ver_row = conn.execute("SELECT MAX(version) FROM clusters").fetchone()
    ver = ver_row[0] or 0
    if ver == 0:
        return []

    # Gather members and suggested per cluster
    rows = conn.execute(
        "SELECT cluster_label, fir_id, role FROM clusters WHERE version = ?",
        (ver,),
    ).fetchall()

    from collections import defaultdict
    members: dict[str, list] = defaultdict(list)
    suggested: dict[str, list] = defaultdict(list)
    for r in rows:
        if r["role"] == "member":
            members[r["cluster_label"]].append(r["fir_id"])
        else:
            suggested[r["cluster_label"]].append(r["fir_id"])

    # Enrich with FIR metadata
    ext_map = latest_extractions(conn)
    result: list[dict] = []
    for label in sorted(members.keys()):
        m_ids = members[label]
        fir_rows = conn.execute(
            f"SELECT fir_id, district, police_station, date_occurred FROM firs "
            f"WHERE fir_id IN ({','.join('?'*len(m_ids))})",
            m_ids,
        ).fetchall()
        districts = sorted({r["district"] for r in fir_rows})
        stations = sorted({r["police_station"] for r in fir_rows})
        dates = sorted(r["date_occurred"] for r in fir_rows)
        crime_types = [
            ext_map[fid]["crime_type"]
            for fid in m_ids
            if fid in ext_map and ext_map[fid].get("crime_type")
        ]
        from collections import Counter
        ct = Counter(crime_types).most_common(1)

        # Strength: mean score of internal links
        lver_row = conn.execute("SELECT MAX(version) FROM links").fetchone()
        lver = lver_row[0] or 0
        edges = conn.execute(
            f"""
            SELECT score FROM links
            WHERE version = ?
              AND fir_a IN ({','.join('?'*len(m_ids))})
              AND fir_b IN ({','.join('?'*len(m_ids))})
            """,
            [lver] + m_ids + m_ids,
        ).fetchall()
        strength = round(sum(r["score"] for r in edges) / len(edges), 2) if edges else 0.0

        result.append({
            "label": label,
            "crime_type": ct[0][0] if ct else None,
            "size": len(m_ids),
            "districts": districts,
            "stations": stations,
            "first": dates[0] if dates else None,
            "last": dates[-1] if dates else None,
            "strength": strength,
            "members": m_ids,
            "suggested": suggested.get(label, []),
        })

    return result


def list_offenders(conn: sqlite3.Connection, limit: int = 20) -> list[dict]:
    """Return offender signals from the latest engine run, ranked by fir_count,
    then district count, then most recent last_seen."""
    ver_row = conn.execute("SELECT MAX(version) FROM offenders").fetchone()
    ver = ver_row[0] or 0
    if ver == 0:
        return []

    rows = conn.execute(
        """
        SELECT kind, value, cluster_label, firs_json, fir_count,
               districts_json, last_seen, loss_inr
        FROM offenders
        WHERE version = ?
        ORDER BY fir_count DESC, last_seen DESC
        LIMIT ?
        """,
        (ver, limit),
    ).fetchall()

    return [
        {
            "kind": r["kind"],
            "value": r["value"],
            "cluster": r["cluster_label"],
            "firs": json.loads(r["firs_json"]),
            "fir_count": r["fir_count"],
            "districts": json.loads(r["districts_json"]),
            "last_seen": r["last_seen"],
            "loss_inr": r["loss_inr"],
        }
        for r in rows
    ]


def station_fact_sheet(
    conn: sqlite3.Connection,
    district: str,
    station: str,
) -> dict | None:
    """Return the latest station_stats row for (district, station).

    Returns None if no stats exist for this station.
    """
    ver_row = conn.execute("SELECT MAX(version) FROM station_stats").fetchone()
    ver = ver_row[0] or 0
    if ver == 0:
        return None

    row = conn.execute(
        """
        SELECT district, station, series_json, total, spikes_json,
               mix_json, loss_inr, summary, summary_source
        FROM station_stats
        WHERE district = ? AND station = ? AND version = ?
        LIMIT 1
        """,
        (district, station, ver),
    ).fetchone()

    if row is None:
        return None

    d = dict(row)
    d["series"] = json.loads(d.pop("series_json"))
    d["spikes"] = json.loads(d.pop("spikes_json"))
    d["mix"] = json.loads(d.pop("mix_json"))

    # Clustered FIR count at this station
    cver_row = conn.execute("SELECT MAX(version) FROM clusters").fetchone()
    cver = cver_row[0] or 0
    cluster_fir_ids = {
        r["fir_id"]
        for r in conn.execute(
            "SELECT fir_id FROM clusters WHERE version = ? AND role = 'member'",
            (cver,),
        ).fetchall()
    }
    station_fir_ids = {
        r["fir_id"]
        for r in conn.execute(
            "SELECT fir_id FROM firs WHERE district = ? AND police_station = ?",
            (district, station),
        ).fetchall()
    }
    d["clustered_firs"] = len(cluster_fir_ids & station_fir_ids)

    # Victim profile from FIRs directly (latest snapshot)
    from ingest.normalize import age_band
    vic_rows = conn.execute(
        """
        SELECT complainant_json FROM firs
        WHERE district = ? AND police_station = ?
        """,
        (district, station),
    ).fetchall()
    from collections import Counter
    vic_counter: Counter = Counter()
    for vr in vic_rows:
        c = json.loads(vr["complainant_json"])
        g = c.get("gender") or "?"
        ab = age_band(c.get("age"))
        vic_counter[f"{g} {ab}"] += 1
    d["victim_profile"] = dict(vic_counter.most_common(3))

    return d

