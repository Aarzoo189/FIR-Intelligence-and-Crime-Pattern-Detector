# Plan: Split pipeline.py ingest stages + store layer

## Overview

`pipeline.py` is a monolith. The request is to extract its first three stages into proper
modules and build a persistent SQLite store so every loaded FIR lands in the database with
`status = 'awaiting_extraction'`.

**Scope**

| New file | Source in pipeline.py | Responsibility |
|---|---|---|
| `ingest/loader.py` | `load()` + constants `REQUIRED` | Open `firs.json`, validate required fields, deduplicate, return raw dicts + reject list |
| `ingest/normalize.py` | `canon_phone()`, `age_band()`, normalization block inside `load()` | Transform a raw FIR dict into the canonical in-memory shape |
| `ingest/rule_extract.py` | `rule_extract()` + all regexes + constants | Extract phones, UPIs, vehicle refs, amounts from a normalised FIR |
| `store/schema.sql` | (new) | DDL for all tables: `firs`, `extractions`, `links`, `clusters`, `decisions` |
| `store/db.py` | (new) | `init_db()`, `upsert_fir()`, `get_fir()`, `get_firs_by_status()` |

`pipeline.py` must be updated to import from the new modules instead of defining those functions itself. Its later stages (`classify`, `mo_similarity`, `link`, `build_clusters`, `signatures`, `analytics`, `narrate`, `evaluate`, `main`) are **not** moved — they stay in `pipeline.py`.

---

## Sub-task 1 — `ingest/__init__.py` + `ingest/loader.py`

**Intent**  
Extract `load()` and the `REQUIRED` constant from `pipeline.py` into `ingest/loader.py`.
The function signature and return value stay identical so `pipeline.py` keeps working after
a one-line import change.

**Expected Outcomes**
- `ingest/loader.py` contains `REQUIRED` list and `load(path) -> (firs, rejects)`.
- `ingest/__init__.py` exists (empty, makes `ingest` a package).
- `pipeline.py` replaces the `load` definition with `from ingest.loader import load`.

**Todo List**
1. Create `ingest/__init__.py` (empty).
2. Create `ingest/loader.py`:
   - Import `json`, `os`, `datetime`.
   - Import `normalize` from `ingest.normalize` (will exist after sub-task 2 — loader calls `normalize` internally, so implement the call but keep it simple: loader can just return the raw-but-validated dict for now, and normalize will be called as a post-step; OR loader imports normalize directly. Choose: **loader calls `ingest.normalize.normalize_fir(r)` for each validated record**).
   - Define `REQUIRED` list.
   - Define `load(path)`: open firs.json, validate, deduplicate, call `normalize_fir` on each valid record, return `(firs, rejects)`.
3. Update `pipeline.py`: remove `load()` definition and `REQUIRED`; add `from ingest.loader import load`.

**Relevant Context**
- `pipeline.py` lines 19–20: `REQUIRED` list.
- `pipeline.py` lines 43–73: `load()` function — the normalization block is lines 53–72.
- The normalized shape (dict keys `id`, `district`, `station`, `occurred`, etc.) is what the rest of `pipeline.py` expects; that shape must not change.

**Status** `[ ] pending`

---

## Sub-task 2 — `ingest/normalize.py`

**Intent**  
Extract the two helper functions (`canon_phone`, `age_band`) and the per-FIR normalization
logic from inside `load()` into `ingest/normalize.py` as a standalone `normalize_fir(raw)`
function.

**Expected Outcomes**
- `ingest/normalize.py` exports `canon_phone(s)`, `age_band(a)`, `normalize_fir(raw)`.
- `normalize_fir` takes a raw FIR dict (already validated for required keys) and returns the canonical dict shape.
- `pipeline.py` removes `canon_phone` and `age_band` definitions; they are imported from `ingest.normalize`.

**Todo List**
1. Create `ingest/normalize.py`:
   - Import `re`, `datetime`.
   - Define `canon_phone(s)` (copy from pipeline.py lines 31–35).
   - Define `age_band(a)` (copy from pipeline.py lines 37–41).
   - Define `normalize_fir(raw)` — contains the transformation block currently at pipeline.py lines 53–72. Takes a single raw FIR dict; returns the canonical dict.
2. Update `pipeline.py`: remove `canon_phone`, `age_band` definitions; add `from ingest.normalize import canon_phone, age_band` (only needed if any remaining pipeline code calls them directly — check and only import what is still used).

**Relevant Context**
- `canon_phone` is used in `rule_extract` (line 89, 106) and in `normalize_fir`.
- After the split, `rule_extract.py` will also import `canon_phone` from `ingest.normalize`.
- Accused "unknown" name logic (Rule 2) lives in the normalize block — keep it there.

**Status** `[ ] pending`

---

## Sub-task 3 — `ingest/rule_extract.py`

**Intent**  
Move the regex constants and `rule_extract()` function to `ingest/rule_extract.py`.

**Expected Outcomes**
- `ingest/rule_extract.py` exports `rule_extract(fir)`.
- All regex patterns (`PHONE_RX`, `UPI_RX`, `VREG_RX`, `VLAST_RX`, `AMT_RX`, `VDESC_RX`) live in this module.
- `pipeline.py` imports `rule_extract` from `ingest.rule_extract` and removes its own definition.

**Todo List**
1. Create `ingest/rule_extract.py`:
   - Import `re` and `canon_phone` from `ingest.normalize`.
   - Copy all regex constants (pipeline.py lines 76–83).
   - Copy `rule_extract(f)` (pipeline.py lines 85–109).
2. Update `pipeline.py`: remove regex constants and `rule_extract` definition; add `from ingest.rule_extract import rule_extract`.

**Relevant Context**
- `rule_extract` uses `canon_phone` (line 89, 106) and `PHONE_RX` (line 128 in `mo_tokens`) — `mo_tokens` stays in `pipeline.py` so it must import `PHONE_RX` too, or re-declare it. Simplest: also export `PHONE_RX`, `UPI_RX`, `VREG_RX` from `rule_extract.py` and import them in `pipeline.py` for `mo_tokens`.

**Status** `[ ] pending`

---

## Sub-task 4 — `store/schema.sql`

**Intent**  
Define the SQLite DDL for all persistent tables. The schema must cover what the architecture
describes: a raw FIR record table, an extractions table (versioned, immutable per Rule 5), a
links table, a clusters table, and a decisions table.

**Expected Outcomes**
- `store/schema.sql` can be executed with `sqlite3` or `db.py`'s `init_db()` to create all tables from scratch.
- Every table that records pipeline results has a `version` column and uses `INSERT` (never `UPDATE`) per Rule 5.
- The `firs` table has a `status` column; newly ingested rows get `status = 'awaiting_extraction'`.

**Todo List**
1. Create `store/schema.sql` with the following tables:

   **`firs`** — one row per FIR (upsert on re-ingest bumps `ingested_at`):
   `fir_id TEXT PK`, `district`, `police_station`, `date_reported`, `time_reported`,
   `date_occurred`, `time_occurred`, `sections_json`, `complainant_json`, `accused_json`,
   `property_json`, `narrative`, `language`, `status TEXT DEFAULT 'awaiting_extraction'`,
   `ingested_at TEXT`

   **`extractions`** — versioned LLM/rule extraction results (insert-only):
   `id INTEGER PK AUTOINCREMENT`, `fir_id TEXT`, `version INTEGER`, `stage TEXT`,
   `crime_type TEXT`, `crime_type_conf REAL`, `mo_slots_json TEXT`, `identifiers_json TEXT`,
   `quotes_json TEXT`, `produced_by TEXT`, `created_at TEXT`

   **`links`** — versioned FIR–FIR link records (insert-only):
   `id INTEGER PK AUTOINCREMENT`, `fir_a TEXT`, `fir_b TEXT`, `score REAL`,
   `evidence_json TEXT`, `version INTEGER`, `produced_by TEXT`, `created_at TEXT`

   **`clusters`** — versioned cluster membership (insert-only):
   `id INTEGER PK AUTOINCREMENT`, `cluster_label TEXT`, `fir_id TEXT`, `role TEXT`,
   `version INTEGER`, `produced_by TEXT`, `created_at TEXT`

   **`decisions`** — analyst decisions on links/clusters (insert-only):
   `id INTEGER PK AUTOINCREMENT`, `entity_type TEXT`, `entity_id TEXT`, `decision TEXT`,
   `reason TEXT`, `analyst TEXT`, `version INTEGER`, `created_at TEXT`

**Relevant Context**
- Rule 5: never UPDATE/DELETE existing result rows — all tables use INSERT + version increment.
- Rule 4: quotes must be stored — `quotes_json` in `extractions` covers this.
- `firs` is the only table that gets effectively "upserted" (raw source data, not a pipeline result), using `INSERT OR REPLACE` keyed on `fir_id`.

**Status** `[ ] pending`

---

## Sub-task 5 — `store/db.py`

**Intent**  
Provide a thin Python API over the SQLite store: initialise the schema, write FIRs, and read
them back. This is the only file the rest of the project should use to touch the database.

**Expected Outcomes**
- `store/__init__.py` exists (empty).
- `store/db.py` exports: `init_db(db_path)`, `upsert_fir(conn, fir_raw)`, `get_fir(conn, fir_id)`, `get_firs_by_status(conn, status)`.
- `upsert_fir` stores the raw FIR dict and sets `status = 'awaiting_extraction'`.
- All JSON columns are serialised with `json.dumps` / deserialised with `json.loads`.
- No code outside `store/` reads or writes the database directly.

**Todo List**
1. Create `store/__init__.py` (empty).
2. Create `store/db.py`:
   - `init_db(db_path) -> sqlite3.Connection`: creates the file (or opens it), runs `schema.sql`, returns the connection.
   - `upsert_fir(conn, raw_fir_dict)`: uses `INSERT OR REPLACE INTO firs` with `status = 'awaiting_extraction'` and `ingested_at = datetime.utcnow().isoformat()`. Serialises list/dict fields to JSON strings.
   - `get_fir(conn, fir_id) -> dict | None`: fetches one row, deserialises JSON columns back to Python objects.
   - `get_firs_by_status(conn, status) -> list[dict]`: fetches all FIRs with a given status.

**Relevant Context**
- `schema.sql` path is relative to `db.py`; use `Path(__file__).parent / "schema.sql"`.
- Raw FIR dict keys (from `firs.json`) map directly to `firs` table columns — `sections`, `complainant`, `accused`, `property` must be JSON-serialised.
- `pipeline.py` does not need to call `store/db.py` yet — that is wired in a later task.

**Status** `[ ] pending`

---

## Sub-task 6 — Wire loader → store in `pipeline.py`

**Intent**  
After loading and before any other processing, persist every validated FIR to SQLite with
`status = 'awaiting_extraction'`. This closes the loop: running `pipeline.py` now both
produces `results.json` and populates the store.

**Expected Outcomes**
- `pipeline.py`'s `main()` calls `init_db`, then calls `upsert_fir` for each raw record from `firs.json` **before** the normalization step, so the raw source is what is stored.
- The DB file is written alongside `results.json` (default path: `fir_intel.db` in the working directory, or alongside `OUT`).
- Existing `results.json` output is unchanged.

**Todo List**
1. In `pipeline.py`'s `main()`, after `firs, rejects = load(DATA)`:
   - Open a raw JSON file to get the original records (or adjust `loader.py` to also return the raw list alongside the normalised list — simpler: have `load()` return a third value `raw_records` for the store step).
   - Call `init_db(db_path)` and `upsert_fir(conn, raw)` for each raw record.
2. Decide db path: use same directory as `OUT`, filename `fir_intel.db`.

**Relevant Context**
- The store receives the **raw** dict from `firs.json` (as-is), not the normalised dict — the normalised dict is what the pipeline works with in memory.
- `loader.py` currently returns the normalised list; the cleanest change is to also return the raw validated list as a third return value: `return firs, rejects, raw_valid`.

**Status** `[ ] pending`

---

## Dependency order

```
sub-task 2 (normalize.py)
    └── sub-task 1 (loader.py imports normalize)
    └── sub-task 3 (rule_extract.py imports canon_phone from normalize)
sub-task 4 (schema.sql)
    └── sub-task 5 (db.py reads schema.sql)
sub-tasks 1-3 + sub-task 5
    └── sub-task 6 (pipeline.py wired together)
```
