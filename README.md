# FIR Intel: cross-district FIR intelligence tool



Architecture (v3): PASTE-ARCHITECTURE-PAGE-LINK-HERE



## Folders

\- data/            firs.json and reference files (taxonomy, gazetteer, sections, stoplist)

\- data/answer\_key/ ground\_truth.csv, gang\_profiles.json (read only by eval/)

\- ingest/          loading, normalization, rule-based identifier extraction

\- store/           SQLite case store

\- engine/          validator, linking, clustering, suggestions, analytics

\- mcp\_server/      FIR Intelligence MCP server used by Bob

\- prompts/         extraction and narration prompts

\- api/, dashboard/ FastAPI backend and dashboard

\- eval/            evaluation harness (the only code that reads the answer key)



## Run the prototype

py pipeline.py data results.json



## MCP server

The MCP server exposes the FIR intelligence tools to Bob.
Register it in Bob's MCP settings using the values below.

| Setting | Value |
|---|---|
| **Command** | Absolute path to `.venv\Scripts\python.exe` in the project root, e.g. `C:\Users\you\fir-intel\.venv\Scripts\python.exe` |
| **Arguments** | `mcp_server/server.py` |
| **Working directory** | Project root (the directory containing `pipeline.py`) |

The database path defaults to `<project root>/fir_intel.db`.
Override with the `FIR_INTEL_DB` environment variable if needed.

**Before starting the server**, run the pipeline at least once so the
database is populated:

```
py pipeline.py data results.json
```

### Available tools

| Tool | Type | Description |
|---|---|---|
| `get_reference` | read | Taxonomy, MO schema, extraction JSON schema and the quote rule |
| `get_pending_firs` | read | FIRs awaiting extraction (with narrative and rule identifiers) |
| `get_fir` | read | Full FIR, latest extraction, cluster membership, linked FIRs |
| `search_firs` | read | Filter by district, station, crime type, date range or keyword |
| `list_clusters` | read | All clusters with members and suggested attachments |
| `explain_link` | read | Score, tier and evidence for a FIR pair |
| `list_offenders` | read | Offender signals ranked by FIR count |
| `station_fact_sheet` | read | Per-station analytics (series, spikes, mix, loss, demographics) |
| `submit_extraction` | write | Validate and save Bob's extraction; audited |
| `run_linking` | write | Re-run the intelligence engine with latest extractions |
| `submit_summary` | write | Save a Bob-authored station narrative (numbers validated) |
| `review_suggestion` | write | Record confirmed/rejected decision on a cluster suggestion |



Project rules for Bob are in .bob/rules/project\_rules.md.



