# Plan: Engine Layer (Step 5)

## Overview

Move all intelligence logic out of `pipeline.py` into a proper `engine/` package that reads
from and writes to the SQLite store. `pipeline.py` becomes a thin orchestrator:
ingest → seed_baseline → run_engine → evaluate → write results.json.
The acceptance numbers must be byte-identical to the current run.

---

## Files created / changed

| File | Action | Notes |
|---|---|---|
| `engine/__init__.py` | create | empty package marker |
| `engine/config.py` | create | W, STRONG, REVIEW, ATTACH_MO_MIN, HUB_MIN_TYPES, ENGINE_VERSION |
| `engine/baseline.py` | create | `classify()` — exact copy from pipeline.py |
| `engine/mo.py` | create | `mo_tokens()`, `mo_similarity()` |
| `engine/link.py` | create | `link()` — exact logic, imports W constants from config |
| `engine/cluster.py` | create | `build_clusters()`, `signatures()` |
| `engine/analytics.py` | create | `analytics()`, `narrate()` |
| `engine/validator.py` | create | `check(fir_id, data, conn, source)` |
| `engine/extraction_schema.json` | create | JSON schema for extraction payloads |
| `engine/run.py` | create | `run_engine(conn, firs)` — orchestrates mo→link→cluster→signatures→analytics, saves to store |
| `engine/seed_baseline.py` | create | runs rule_extract+classify on every FIR, validates, saves extractions with accepted=1 |
| `store/schema.sql` | modify | add `accepted` column to extractions; add offenders, station_stats, audit_log tables |
| `store/db.py` | modify | add save_extraction, latest_extractions, save_links, save_clusters, save_offenders, save_station_stats, latest_run_results, audit, next_version |
| `pipeline.py` | modify | slim to: ingest → seed_baseline → run_engine → evaluate → write results.json |
| `tests/__init__.py` | create | empty |
| `tests/test_validator.py` | create | 6 test cases |

---

## Sub-task 1 — `engine/config.py`

**Intent**  
Single source of truth for all engine tuning constants. Everything that was a module-level
constant in `pipeline.py` lives here. `ENGINE_VERSION` tags every result row written by this
engine version (Rule 5).

**Expected Outcomes**
- All five constants and `ENGINE_VERSION = "engine_v1"` are importable from `engine.config`.
- No logic, no imports beyond stdlib.

**Todo List**
1. Create `engine/__init__.py` (empty).
2. Create `engine/config.py` — copy W dict, STRONG, REVIEW, ATTACH_MO_MIN, HUB_MIN_TYPES verbatim from pipeline.py lines 25–29. Add `ENGINE_VERSION = "engine_v1"`.

**Relevant Context**
- `pipeline.py` lines 25–29.

**Status** `[ ] pending`

---

## Sub-task 2 — `engine/baseline.py`

**Intent**  
Isolate the rule-based crime-type classifier. This is a stand-in for Bob's LLM extraction
and is only used for testing / the baseline seed run.

**Expected Outcomes**
- `classify(fir)` is importable from `engine.baseline`.
- Logic is byte-identical to `pipeline.py` lines 32–44.

**Todo List**
1. Create `engine/baseline.py` — copy `classify()` verbatim; import `collections`.

**Relevant Context**
- `pipeline.py` lines 31–44.

**Status** `[ ] pending`

---

## Sub-task 3 — `engine/mo.py`

**Intent**  
MO similarity is supporting evidence only (Rule 3). Keeping it isolated makes it easy to
replace the TF-IDF approach with an LLM embedding later.

**Expected Outcomes**
- `mo_tokens(text)` and `mo_similarity(firs)` are importable from `engine.mo`.
- Logic is identical to `pipeline.py` lines 47–60.

**Todo List**
1. Create `engine/mo.py` — copy both functions. Import `re`, `collections`, sklearn, and
   the three regex constants from `ingest.rule_extract` (PHONE_RX, UPI_RX, VREG_RX).

**Relevant Context**
- `pipeline.py` lines 46–60.
- `mo_tokens` uses PHONE_RX, UPI_RX, VREG_RX to strip identifiers before tokenising.

**Status** `[ ] pending`

---

## Sub-task 4 — `engine/link.py`

**Intent**  
FIR–FIR pair linking with noisy-OR evidence combination. Must preserve all four Rule 3
constraints: hard evidence required, MO only adds support, hub down-weighting, vehicle_reg
skipped for vehicle_theft.

**Expected Outcomes**
- `link(firs, sim)` returns `(links, hubs)` identical to current pipeline.
- Imports W, HUB_MIN_TYPES from `engine.config`.

**Todo List**
1. Create `engine/link.py` — copy `link()` verbatim from pipeline.py lines 63–109.
   Replace W, HUB_MIN_TYPES with imports from `engine.config`.
   Import `re`, `collections`, `itertools`, `math`, `fuzz` from rapidfuzz.

**Relevant Context**
- `pipeline.py` lines 62–109.
- Rule 3: MO similarity never creates a link (enforced at line 104 — `feats["mo_support"]` only added when a hard-evidence link already exists in `pairs`).

**Status** `[ ] pending`

---

## Sub-task 5 — `engine/cluster.py`

**Intent**  
Cluster building and offender signature generation. Suggested attachments are always review-
only (never auto-joined to a cluster).

**Expected Outcomes**
- `build_clusters(firs, links, sim)` and `signatures(cl, idx)` importable from `engine.cluster`.
- Logic identical to pipeline.py lines 112–162.
- Imports REVIEW, ATTACH_MO_MIN from `engine.config`.

**Todo List**
1. Create `engine/cluster.py` — copy both functions verbatim.
   Replace REVIEW, ATTACH_MO_MIN with imports from `engine.config`.
   Import `collections`, `datetime`, `networkx`.

**Relevant Context**
- `pipeline.py` lines 111–162.

**Status** `[ ] pending`

---

## Sub-task 6 — `engine/analytics.py`

**Intent**  
Station-level crime series analysis and narration. Narration uses only numbers already
present in the station fact sheet — no hallucination risk.

**Expected Outcomes**
- `analytics(firs)` and `narrate(s, clustered)` importable from `engine.analytics`.
- Logic identical to pipeline.py lines 165–196.

**Todo List**
1. Create `engine/analytics.py` — copy both functions verbatim.
   Import `collections`, `datetime`, `scipy.stats.poisson`.

**Relevant Context**
- `pipeline.py` lines 164–196.

**Status** `[ ] pending`

---

## Sub-task 7 — `engine/extraction_schema.json` + `engine/validator.py`

**Intent**  
The validator is the enforcement layer for Rules 2, 4, and the reference files. It returns
*all* failures, not just the first, so callers get actionable feedback.
The JSON schema file is the contract the MCP server will use when asking Bob for extractions.

**Expected Outcomes**
- `engine/extraction_schema.json` describes the expected extraction payload shape.
- `check(fir_id, data, conn, source="llm")` returns `(ok: bool, reasons: list[str])`.
- Validator returns False + reasons for all of: missing required keys, unknown crime_type,
  unknown MO slot, quote not in narrative, bad phone format, stoplist phone, non-lowercase
  UPI, uppercase-with-spaces vehicle reg, "Unknown" accused name (Rule 2).
- When `source == "rule_baseline"`, the quote check is skipped.
- All reference data (taxonomy, mo_schema, stoplist) is loaded once at import time from
  `data/` relative to the project root — use `Path(__file__).parents[1] / "data"`.

**Todo List**
1. Create `engine/extraction_schema.json`:
   ```json
   {
     "crime_type": "string (taxonomy id)",
     "crime_type_conf": "float 0..1",
     "mo_slots": {"slot_name": {"value": "any", "quote": "string"}},
     "identifiers": {
       "phones": ["10-digit strings"],
       "upis": ["lowercase upi strings"],
       "vehicle_regs": ["UPPERCASE no-space strings"],
       "vehicle_last4": ["4-digit strings"],
       "vehicle_desc": "string or null"
     },
     "people": [{"name": "string", "alias": "string|null", "father_name": "string|null",
                 "role": "accused|victim|witness", "quote": "string"}]
   }
   ```
2. Create `engine/validator.py`:
   - Load taxonomy IDs, mo_schema slot names, stoplist numbers at module level.
   - Define `_STOPLIST`, `_VALID_CRIME_TYPES`, `_VALID_SLOTS` (dict crime_type → set of slot names).
   - `_norm(text)` — NFC + collapse whitespace.
   - `check(fir_id, data, conn, source="llm") -> (bool, list[str])`:
     a. Fetch narrative via `store.db.get_fir(conn, fir_id)`.
     b. Check required top-level keys present.
     c. Check crime_type in taxonomy.
     d. For each MO slot: check slot name allowed for that crime type.
     e. For each MO slot (source != "rule_baseline"): check quote appears in _norm(narrative).
     f. Check each phone: 10 digits, starts 6-9, not in stoplist.
     g. Check each UPI: matches UPI pattern, is lowercase.
     h. Check each vehicle_reg: uppercase, no spaces.
     i. For each person in people: check name not in _UNKNOWN_NAMES (Rule 2).
        If source != "rule_baseline": check quote appears in narrative.
   - Collect all failures into `reasons`, return `(len(reasons) == 0, reasons)`.

**Relevant Context**
- Rule 2: _UNKNOWN_NAMES = {"unknown", "unknown person", "अज्ञात", "n/a", "not known", ""}.
- Rule 4: quote check skipped only for source="rule_baseline".
- `data/stoplist.json` stoplist numbers are short (3–4 digits) — only reject canonicalised
  10-digit phones that equal a stoplist number after canonicalisation.
  Since stoplist numbers are not 10-digit mobiles, this is a simple `if p in _STOPLIST` check
  on the already-normalised phone.

**Status** `[ ] pending`

---

## Sub-task 8 — Store additions (`store/schema.sql` + `store/db.py`)

**Intent**  
Add the three new tables and the `accepted` flag to extractions. All result tables remain
insert-only per Rule 5.

**Expected Outcomes**

Schema additions:
- `extractions` gains `accepted INTEGER NOT NULL DEFAULT 0` column.
- New table `offenders` (kind, value, cluster_label, firs_json, fir_count, districts_json, last_seen, loss_inr, version, produced_by, created_at).
- New table `station_stats` (district, station, series_json, total, spikes_json, mix_json, loss_inr, summary, summary_source, version, produced_by, created_at).
- New table `audit_log` (action, target, ok, details_json, actor, created_at) — no version needed, it's an event log.

`db.py` additions:
- `save_extraction(conn, fir_id, data, stage, produced_by, accepted, version) -> int` — inserts, returns row id.
- `latest_extractions(conn) -> dict[fir_id → dict]` — for each fir_id returns the highest-version accepted extraction.
- `save_links(conn, links, version, produced_by)` — bulk insert into links table.
- `save_clusters(conn, clusters, version, produced_by)` — bulk insert membership rows (role=member and role=suggested).
- `save_offenders(conn, offenders, version, produced_by)` — bulk insert.
- `save_station_stats(conn, stats, version, produced_by)` — bulk insert.
- `latest_run_results(conn) -> dict` — fetches latest version's links, clusters, offenders, station_stats.
- `audit(conn, action, target, ok, details, actor)` — inserts one audit_log row.
- `next_version(conn, table) -> int` — returns `MAX(version)+1` for a given table, or 1 if empty.
- `set_fir_status(conn, fir_id, status, actor)` — UPDATE firs SET status=? and then call audit().

**Important**: `set_fir_status` is the only UPDATE allowed — it touches `firs` (source data),
not any result table, and every call is recorded in audit_log.

**Relevant Context**
- `store/schema.sql` — existing 5 tables, need ALTER TABLE or new DDL approach.
  SQLite doesn't support `ADD COLUMN` in `CREATE TABLE IF NOT EXISTS` on an existing DB.
  Strategy: use `ALTER TABLE extractions ADD COLUMN accepted INTEGER NOT NULL DEFAULT 0`
  guarded by a `PRAGMA table_info` check in `init_db`, or simply drop and re-create the
  entire DB when running with a fresh data set. For safety: use
  `CREATE TABLE IF NOT EXISTS` for new tables and `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`
  pattern via a helper that catches the "duplicate column" error.

**Status** `[ ] pending`

---

## Sub-task 9 — `engine/run.py`

**Intent**  
`run_engine(conn, firs)` is the single entry point for the intelligence layer. It takes the
in-memory normalised FIR list (already carrying `.x` extraction dicts and `.crime_type`),
runs mo → link → cluster → signatures → analytics, saves everything to the store, and
returns the full result dict that pipeline.py needs to write results.json.

**Expected Outcomes**
- `run_engine(conn, firs)` returns `{"links": ..., "clusters": ..., "offenders": ..., "months": ..., "stations": ...}`.
- All results written to DB at version = `next_version(conn, "links")`.
- Cluster members and suggestions both saved to the clusters table with correct roles.
- Offenders and station_stats saved.

**Todo List**
1. Create `engine/run.py`:
   - Import from engine.mo, engine.link, engine.cluster, engine.analytics, engine.config.
   - Import store.db helpers.
   - `run_engine(conn, firs)`:
     1. ver = next_version(conn, "links")
     2. sim = mo_similarity(firs)
     3. links, hubs = link(firs, sim)
     4. clusters = build_clusters(firs, links, sim)
     5. idx = {f["id"]: f for f in firs}
     6. offenders = [o for c in clusters for o in signatures(c, idx)]
     7. offenders.sort(key=…)
     8. save_links(conn, links, ver, ENGINE_VERSION)
     9. save_clusters(conn, clusters, ver, ENGINE_VERSION)
     10. save_offenders(conn, offenders, ver, ENGINE_VERSION)
     11. months, stations = analytics(firs)
     12. where = {n: c["label"] for c in clusters for n in c["members"]}
     13. for s in stations: compute clustered_firs, summary = narrate(s, k)
     14. save_station_stats(conn, stations, ver, ENGINE_VERSION)
     15. Return full result dict (same shape as pipeline.py currently builds for these sections).

**Status** `[ ] pending`

---

## Sub-task 10 — `engine/seed_baseline.py`

**Intent**  
Populate the extractions table with rule-based results so `run_engine` has something to work
with on first run. Every FIR goes through `validator.check` with `source="rule_baseline"` —
the quote check is skipped but all other rules (phone format, stoplist, unknown accused, etc.)
are enforced.

**Expected Outcomes**
- `seed_baseline(conn, firs)` inserts one accepted extraction per FIR with stage="rule_baseline".
- Reports how many passed / failed validation.
- Does not re-seed if the FIR already has an accepted extraction at this stage.

**Todo List**
1. Create `engine/seed_baseline.py`:
   - Import `rule_extract` from ingest.rule_extract, `classify` from engine.baseline.
   - Import `check` from engine.validator, `save_extraction`, `latest_extractions`,
     `next_version`, `audit` from store.db.
   - `seed_baseline(conn, firs) -> (int passed, int failed)`:
     1. existing = latest_extractions(conn)  — skip FIRs already seeded
     2. ver = next_version(conn, "extractions")
     3. For each fir not already in existing:
        a. x = rule_extract(fir)
        b. crime_type, conf, _ = classify(fir)
        c. Build extraction data dict (crime_type, crime_type_conf, mo_slots={},
           identifiers={phones, upis, vehicle_regs, vehicle_last4, vehicle_desc},
           people=[] — rule baseline produces no people or quotes)
        d. ok, reasons = check(fir["id"], data, conn, source="rule_baseline")
        e. If ok: save_extraction(conn, ..., accepted=1); update fir status to
           "extraction_complete" via set_fir_status.
        f. Else: audit failure; count as failed.
     4. Return (passed, failed).

**Relevant Context**
- `rule_extract` returns `evidence` list (span records) — not stored in extraction data;
  the identifiers dict is what matters.
- Rule baseline has no quotes — validator skips quote checks for source="rule_baseline".

**Status** `[ ] pending`

---

## Sub-task 11 — Slim down `pipeline.py`

**Intent**  
`pipeline.py` becomes a thin orchestrator. It keeps `evaluate()` (the only function that
touches the answer key) and the `main()` entry point, but delegates all intelligence work
to the engine.

**Expected Outcomes**
- `main()` flow: load → upsert_firs → seed_baseline → run_engine → evaluate → write results.json.
- Output format of results.json is unchanged (same keys, same values).
- The acceptance check numbers must match exactly.
- Lines removed: `classify`, `mo_tokens`, `mo_similarity`, `link`, `build_clusters`,
  `signatures`, `analytics`, `narrate` definitions.

**Todo List**
1. Replace `pipeline.py` body: keep `evaluate()` verbatim and replace `main()` with the
   slimmed version that calls:
   - `load(DATA)` → `upsert_fir` loop → `seed_baseline(conn, firs)` → per-fir `rule_extract`
     and `classify` to attach `.x` and `.crime_type` (run_engine needs these) → `run_engine(conn, firs)`.
   - Unpack engine results to build `cl_out`, `fir_out`, `res` dict — same code as today.
   - Call `evaluate()` and write results.json.
2. Remove the now-extracted function definitions.
3. Update imports: add engine.* imports, remove the functions that moved.

**Note on in-memory state**: `run_engine` receives `firs` with `.x` and `.crime_type`
already attached (set in main before calling run_engine). This matches the current flow
where `pipeline.py` does `f["x"] = rule_extract(f)` and `f["crime_type"] = classify(f)[0]`
before the engine stages. `seed_baseline` also sets these on the fir dicts via the same
functions — they must be called in the same order to guarantee identical results.

**Status** `[ ] pending`

---

## Sub-task 12 — `tests/test_validator.py`

**Intent**  
Six focused test cases that verify the validator enforces all the rules it's responsible for.

**Expected Outcomes**
- All 6 tests pass with `py -m pytest tests/`.
- Tests use an in-memory SQLite DB seeded with a single real-ish FIR narrative.

**Todo List**
1. Create `tests/__init__.py` (empty).
2. Create `tests/test_validator.py` with:
   - `fixture` `conn` — in-memory DB with one FIR row (narrative fixed for tests).
   - `test_quote_not_in_narrative` — passes extraction with a quote that doesn't appear.
   - `test_unknown_crime_type` — crime_type = "arson" (not in taxonomy).
   - `test_bad_phone` — phone = "12345" (too short, wrong prefix).
   - `test_stoplist_phone` — a 10-digit phone that normalises to a stoplist entry (note:
     stoplist numbers are 3–4 digits, so actual 10-digit mobiles can't hit the stoplist;
     test this by mocking `_STOPLIST` or using a number that the validator treats as a match
     after canonicalisation — just test that the validator rejects it correctly).
   - `test_unknown_accused` — person with name "Unknown" in people list.
   - `test_valid_extraction` — a complete, correct extraction dict; assert ok=True.

**Relevant Context**
- The validator's `source` param controls quote-check skipping.
- For the stoplist phone test: since canonical mobiles are 10 digits starting 6-9 and stoplist
  entries are ≤4 digits, an actual match is impossible unless the validator checks the raw
  string. The test should verify the check function handles this correctly — pass a phone
  that is in `_STOPLIST` by patching, or document in the test that no real 10-digit mobile
  matches the stoplist and the test demonstrates the guard is in place.

**Status** `[ ] pending`

---

## Dependency order

```
Sub-task 1  engine/config.py           (no deps)
Sub-task 2  engine/baseline.py         (no deps beyond stdlib + collections)
Sub-task 3  engine/mo.py               (imports ingest.rule_extract regexes)
Sub-task 4  engine/link.py             (imports engine.config)
Sub-task 5  engine/cluster.py          (imports engine.config)
Sub-task 6  engine/analytics.py        (no engine deps)
Sub-task 7  engine/validator.py        (imports store.db.get_fir, reads data/*.json)
Sub-task 8  store additions            (schema + db.py helpers)
Sub-task 9  engine/run.py              (imports engine.mo/link/cluster/analytics + store.db)
Sub-task 10 engine/seed_baseline.py    (imports ingest.rule_extract, engine.baseline/validator, store.db)
Sub-task 11 pipeline.py slim-down      (imports all engine.* + store.db)
Sub-task 12 tests/test_validator.py    (imports engine.validator, store.db)
```

## Acceptance check

After sub-task 11, run:
```
py pipeline.py data results.json
```
Must print exactly:
```
clusters=10 offenders=17 links={'total': 65, 'strong': 39, 'review': 24, 'weak': 2}
crime_type=0.995 auto={'precision': 1.0, 'recall': 0.384, 'f1': 0.555} with_sugg=...
suggestions 10/17  none_in_clusters=0  gang firs linked 44/74
```
If any number differs, diagnose before marking sub-task 11 done.
