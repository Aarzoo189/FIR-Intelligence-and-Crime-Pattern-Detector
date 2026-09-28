# 🚀 FIR Intelligence & Crime Pattern Detector

> Bob-powered detection of repeat offenders hidden across district FIRs.

## 👥 Team

| Field | Value |
| --- | --- |
| Team Name | Cyberpuff Girls |
| Track | AI |
| Team Lead | Kritika Bhardwaj — kritikabharadwaj2310@gmail.com |
| Members | Aarzoo Jain, Ananya Bhardwaj |

## 🎯 Problem Statement

UP Police's CCTNS holds over 3 crore digitized FIRs, but no language-analysis layer reads them, so pattern analysis is done by hand, one station at a time. Serial offenders such as the Jamtara gangs evade detection for years because a case in one district is never connected to a case in another. Crime analysts and investigating officers have the data, but no way to see the links hidden inside free-text FIR narratives.

## 💡 Solution

We built a tool where IBM Bob reads each FIR, classifies the crime and extracts the accused, victim profile and modus operandi, while Python code validates Bob's work and links FIRs that share hard evidence: phone numbers, UPI IDs, vehicles or named accused. Linked FIRs form gang clusters with a flagged repeat-offender list and station-level crime trends. Bob reaches the data only through our own MCP server, which checks every submission, and uncertain links go to an investigator to confirm or reject.

**Design in one line:** Bob reads. Code checks and connects. A person decides.

## ✨ Key Features

- **Feature 1: Bob-powered FIR extraction.** Bob classifies each FIR into one of 7 crime types and extracts roles and MO slots from English and Hinglish narratives, quoting the exact words that support every field.
- **Feature 2: Evidence-based gang detection.** FIR pairs are scored from shared evidence (noisy-OR over weighted clues) and joined into clusters. Every link shows why it exists, e.g. "0.97: same phone 9123465423, same accused Naveen Verma".
- **Feature 3: Repeat-offender signatures.** Phones, UPI IDs, vehicles and persons seen in 2 or more FIRs of a gang, ranked by FIR count, districts spanned, recency and total loss.
- **Feature 4: Station crime trends.** Monthly counts per station with Poisson-tested spike detection, crime mix and victim profiles, turned into summaries Bob writes from computed figures only.
- **Feature 5: FIR Intelligence MCP server with guardrails.** A controlled door for Bob: read tools are open, write tools are validated, approved and audit-logged, and no tool can reach the answer key.

## 🛠️ Tech Stack

| Category | Technologies |
| --- | --- |
| Languages | Python, HTML/JavaScript |
| Frameworks | FastAPI, Model Context Protocol (MCP) Python SDK, networkx, scikit-learn, SciPy, rapidfuzz |
| IBM Technologies | IBM Bob (development and runtime extraction via MCP); IBM Granite on watsonx.ai planned for scaled deployment |
| Databases | SQLite (PostgreSQL planned for deployment) |
| Other | Git, [Docker if used] |

## 📁 Repository Structure

```
├── src/                      # All source code
│   ├── ingest/               # Loader, normalizer, rule extractors (phones, UPI IDs, vehicles)
│   ├── store/                # SQLite schema and database access
│   ├── engine/               # Validator, entity resolution, linker, clustering, analytics
│   ├── mcp_server/           # FIR Intelligence MCP server (read and write tools, audit log)
│   ├── prompts/              # Versioned Bob prompts (extraction, station summary)
│   ├── api/                  # FastAPI backend for the dashboard
│   ├── dashboard/            # Investigator dashboard
│   ├── eval/                 # Evaluation harness (only code that reads the answer key)
│   └── data/                 # firs.json, reference files, answer_key/
├── docs/                     # Written documentation
│   ├── problem-statement.md
│   ├── solution-overview.md
│   ├── architecture.md
│   └── setup-guide.md
├── demo/                     # Demo artifacts
│   ├── screenshots/          # App screenshots
│   └── demo-video-link.txt   # Link to demo video
├── presentation/             # Slide deck
└── submission.yaml           # Structured submission metadata
```

## ⚡ How to Run

Copy these exact steps from your docs/setup-guide.md.

```bash
# 1. Clone the repo
git clone https://github.com/[your-repo].git
cd [your-repo]

# 2. Install dependencies
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# 3. Configure environment
cp .env.example .env
# Edit .env with your values (database path; no API key is needed for the demo)

# 4. Run the project
python -m src.ingest.loader src/data/firs.json    # load and clean the 200 FIRs
python -m src.mcp_server.server                   # start the MCP server, then connect it in Bob
                                                  # and ask Bob to process pending FIRs
python -m src.eval.harness                        # score the run against the answer key
uvicorn src.api.main:app --reload                 # dashboard at http://localhost:8000
```

> [Check these commands against the final code before submission.]

## 🖥️ Demo

| Artifact | Link |
| --- | --- |
| 📹 Demo Video | See demo/demo-video-link.txt |
| 🌐 Live Demo | See demo/live-demo-url.txt |
| 🖼️ Screenshots | See demo/screenshots/ |
| 📊 Presentation | See presentation/slides.pdf |

**Results on our 200-FIR synthetic dataset** (5 UP districts, 20 police stations, 10 planted gangs, 126 unrelated FIRs including decoys), from our prototype run with rules standing in for Bob's extraction:

| Measure | Result |
| --- | --- |
| Planted gangs recovered as their own cluster | 10 of 10 |
| Unrelated FIRs wrongly put into a gang | 0 of 126 |
| Precision of automatic links | 100% |
| Gang FIRs linked automatically | 44 of 74 |
| Review suggestions that were correct | 10 of 17 |
| Crime type correct | 199 of 200 |

[Update this table with the numbers from your Bob extraction run.]

## ⚠️ Known Limitations

- **Synthetic data only.** Tested on 200 synthetic FIRs; real FIRs are messier (Devanagari script, spelling errors, scanned documents) and would need OCR and a larger station list.
- **Recall is limited by design.** 30 of 74 gang FIRs share no phone, UPI ID, vehicle or named accused with their gang. We send those to human review instead of guessing, because matching on MO wording alone gave only 24% precision against the decoys.
- **Prototype rules were tuned on the same data.** Crime-type rules were written with this dataset in view, so we report scores on a fresh test batch as well: [add fresh-batch results].
- **Bob processes FIRs in batches through the IDE.** This suits a demo, not 3 crore FIRs; scaled extraction would run the same prompt and validator behind a model API such as IBM Granite on watsonx.
- **No production authentication.** The dashboard runs locally for one team; role-based logins per district are future work.

## 🏅 What We're Most Proud Of

**Links that can be trusted and explained.** Our first attempt linked FIRs by similar crime wording and reached only 24% precision, because the dataset's decoy FIRs copy gang wording on purpose. We redesigned the system so only hard evidence creates links, and wording only adds support. The result: all 10 gangs found, 0 of 126 unrelated FIRs pulled in, and every link shows the exact evidence behind it.

We are equally proud of the guardrails around Bob. Every fact Bob extracts must quote words that really exist in the FIR, crime types come from a fixed list, station summaries are rejected if they contain a number the code did not compute, and every write is approved and audit-logged. Bob's mistakes cannot reach the database.