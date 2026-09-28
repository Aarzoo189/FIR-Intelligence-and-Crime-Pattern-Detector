-- store/schema.sql
-- SQLite DDL for the FIR Intel case store.
--
-- Design rules (from project_rules.md):
--   Rule 5: pipeline result tables (extractions, links, clusters, decisions,
--            offenders, station_stats) are INSERT-only with a version column.
--            Never UPDATE or DELETE these rows.
--   Rule 4: verbatim quotes are stored in extractions.quotes_json.
--   firs:   source data — uses INSERT OR REPLACE so re-ingest refreshes the
--            row without touching result tables.
--   audit_log: event log — insert-only but no version column needed.

-- ---------------------------------------------------------------------------
-- firs: one row per unique FIR (source data, not a pipeline result)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS firs (
    fir_id            TEXT    NOT NULL PRIMARY KEY,
    district          TEXT    NOT NULL,
    police_station    TEXT    NOT NULL,
    date_reported     TEXT    NOT NULL,
    time_reported     TEXT,
    date_occurred     TEXT    NOT NULL,
    time_occurred     TEXT,
    -- list fields serialised as JSON strings
    sections_json     TEXT    NOT NULL,   -- JSON array of section strings
    complainant_json  TEXT    NOT NULL,   -- JSON object
    accused_json      TEXT    NOT NULL,   -- JSON array
    property_json     TEXT    NOT NULL,   -- JSON object
    narrative         TEXT    NOT NULL,
    language          TEXT,
    status            TEXT    NOT NULL DEFAULT 'awaiting_extraction',
    ingested_at       TEXT    NOT NULL
);

-- ---------------------------------------------------------------------------
-- extractions: versioned LLM / rule extraction results (insert-only)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS extractions (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    fir_id           TEXT    NOT NULL REFERENCES firs(fir_id),
    version          INTEGER NOT NULL DEFAULT 1,
    stage            TEXT    NOT NULL,   -- e.g. 'rule_baseline', 'llm_v1'
    crime_type       TEXT,
    crime_type_conf  REAL,
    mo_slots_json    TEXT,               -- JSON object: slot_name -> {value, quote}
    identifiers_json TEXT,               -- JSON object: phones, upis, vehicle_regs, …
    quotes_json      TEXT,               -- JSON array of {field, quote} — Rule 4
    produced_by      TEXT,               -- prompt/stage version string
    accepted         INTEGER NOT NULL DEFAULT 0,   -- 1 = accepted for use by engine
    created_at       TEXT    NOT NULL
);

-- ---------------------------------------------------------------------------
-- links: versioned FIR–FIR link records (insert-only)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS links (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    fir_a         TEXT    NOT NULL REFERENCES firs(fir_id),
    fir_b         TEXT    NOT NULL REFERENCES firs(fir_id),
    score         REAL    NOT NULL,
    evidence_json TEXT    NOT NULL,   -- JSON array of evidence feature dicts
    version       INTEGER NOT NULL DEFAULT 1,
    produced_by   TEXT,
    created_at    TEXT    NOT NULL
);

-- ---------------------------------------------------------------------------
-- clusters: versioned cluster membership (insert-only)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS clusters (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    cluster_label TEXT    NOT NULL,   -- e.g. 'C01'
    fir_id        TEXT    NOT NULL REFERENCES firs(fir_id),
    role          TEXT    NOT NULL DEFAULT 'member',   -- 'member' | 'suggested'
    version       INTEGER NOT NULL DEFAULT 1,
    produced_by   TEXT,
    created_at    TEXT    NOT NULL
);

-- ---------------------------------------------------------------------------
-- decisions: analyst decisions on links / clusters (insert-only)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS decisions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_type TEXT    NOT NULL,   -- 'link' | 'cluster' | 'suggestion'
    entity_id   TEXT    NOT NULL,   -- stringified link id or cluster label
    decision    TEXT    NOT NULL,   -- 'confirmed' | 'rejected' | 'needs_review'
    reason      TEXT,
    analyst     TEXT,
    version     INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT    NOT NULL
);

-- ---------------------------------------------------------------------------
-- offenders: versioned offender signals (insert-only)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS offenders (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    kind           TEXT    NOT NULL,   -- 'Phone' | 'UPI ID' | 'Vehicle' | 'Person'
    value          TEXT    NOT NULL,
    cluster_label  TEXT    NOT NULL,
    firs_json      TEXT    NOT NULL,   -- JSON array of fir_id strings
    fir_count      INTEGER NOT NULL,
    districts_json TEXT    NOT NULL,   -- JSON array of district strings
    last_seen      TEXT    NOT NULL,   -- YYYY-MM-DD
    loss_inr       INTEGER NOT NULL DEFAULT 0,
    version        INTEGER NOT NULL DEFAULT 1,
    produced_by    TEXT,
    created_at     TEXT    NOT NULL
);

-- ---------------------------------------------------------------------------
-- station_stats: versioned per-station analytics (insert-only)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS station_stats (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    district       TEXT    NOT NULL,
    station        TEXT    NOT NULL,
    series_json    TEXT    NOT NULL,   -- JSON array of monthly counts
    total          INTEGER NOT NULL,
    spikes_json    TEXT    NOT NULL,   -- JSON array of spike dicts
    mix_json       TEXT    NOT NULL,   -- JSON object: crime_type -> count
    loss_inr       INTEGER NOT NULL DEFAULT 0,
    summary        TEXT,               -- narrated summary string
    summary_source TEXT,               -- e.g. 'engine_v1/narrate'
    version        INTEGER NOT NULL DEFAULT 1,
    produced_by    TEXT,
    created_at     TEXT    NOT NULL
);

-- ---------------------------------------------------------------------------
-- audit_log: event log for status changes and validation outcomes (insert-only)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS audit_log (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    action       TEXT    NOT NULL,   -- e.g. 'status_change', 'validation_fail'
    target       TEXT    NOT NULL,   -- fir_id or other entity id
    ok           INTEGER NOT NULL,   -- 1 = success, 0 = failure
    details_json TEXT,               -- JSON object with extra context
    actor        TEXT,               -- e.g. 'engine_v1', 'analyst'
    created_at   TEXT    NOT NULL
);
