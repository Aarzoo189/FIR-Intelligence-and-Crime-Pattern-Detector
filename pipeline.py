"""FIR Intelligence prototype pipeline.

Reads firs.json, runs the pipeline stages, and writes results.json.
The answer key (ground_truth.csv, gang_profiles.json) is read only by evaluate().

Usage: py pipeline.py <dataset_dir> <out.json>
"""
import collections
import csv
import itertools
import json
import os
import sys
from datetime import datetime

from engine.config import STRONG, REVIEW, ENGINE_VERSION
from engine.run import run_engine
from engine.seed_baseline import seed_baseline
from ingest.loader import load
from store.db import init_db, upsert_fir

DATA = sys.argv[1] if len(sys.argv) > 1 else "."
OUT  = sys.argv[2] if len(sys.argv) > 2 else "results.json"


# ---------- Evaluation (the only code that reads the answer key) ----------

def evaluate(firs, clusters, offenders, path):
    key = os.path.join(path, "answer_key")
    if not os.path.isdir(key):
        key = path
    gt = {r["fir_id"]: r for r in csv.DictReader(open(os.path.join(key, "ground_truth.csv"), encoding="utf-8"))}
    gangs = json.load(open(os.path.join(key, "gang_profiles.json"), encoding="utf-8"))
    ids = [f["id"] for f in firs]
    ct_ok = sum(f["crime_type"] == gt[f["id"]]["crime_type"] for f in firs)
    lang = collections.defaultdict(lambda: [0, 0])
    for f in firs:
        lang[f["language"]][0] += f["crime_type"] == gt[f["id"]]["crime_type"]; lang[f["language"]][1] += 1

    def pairwise(label):
        tp = fp = fn = 0
        for a, b in itertools.combinations(ids, 2):
            t = gt[a]["group_id"] != "NONE" and gt[a]["group_id"] == gt[b]["group_id"]
            p = a in label and b in label and label[a] == label[b]
            tp += t and p; fp += (not t) and p; fn += t and not p
        pr = tp / (tp + fp) if tp + fp else 0; rc = tp / (tp + fn) if tp + fn else 0
        return {"precision": round(pr, 3), "recall": round(rc, 3), "f1": round(2 * pr * rc / (pr + rc), 3) if pr + rc else 0}

    auto = {n: c["label"] for c in clusters for n in c["members"]}
    withs = dict(auto); withs.update({s["fir"]: c["label"] for c in clusters for s in c["suggested"]})
    sug_ok = sum(1 for c in clusters for s in c["suggested"]
                 if gt[s["fir"]]["group_id"] == collections.Counter(gt[n]["group_id"] for n in c["members"]).most_common(1)[0][0])
    sug_total = sum(len(c["suggested"]) for c in clusters)
    per_gang = []
    for g in gangs:
        mem = {i for i in ids if gt[i]["group_id"] == g["group_id"]}
        best = max(clusters, key=lambda c: len(mem & c["members"]), default=None)
        ov = len(mem & best["members"]) if best else 0
        sugg = {s["fir"] for s in best["suggested"]} if best else set()
        want = g["phones"] + [u.lower() for u in g["upis"]] + [m["name"] for m in g["members"]] + ([g["vehicle_last4"]] if g.get("vehicle_last4") else [])
        found = [w for w in want if any(w.lower() in o["value"].lower() for o in offenders if set(o["firs"]) & mem)]
        per_gang.append({"group_id": g["group_id"], "crime": g["crime"], "districts": g["districts"], "size": len(mem),
                         "cluster": best["label"] if ov else None, "linked": ov,
                         "linked_plus_suggested": ov + len(mem & sugg),
                         "impure": len(best["members"] - mem) if ov else 0,
                         "signals_found": len(found), "signals_total": len(want), "missing_signals": [w for w in want if w not in found]})
    return {"crime_type_accuracy": round(ct_ok / len(firs), 3), "crime_type_correct": ct_ok, "n": len(firs),
            "by_language": {k: round(v[0] / v[1], 3) for k, v in lang.items()},
            "misclassified": [{"fir": f["id"], "true": gt[f["id"]]["crime_type"], "pred": f["crime_type"]} for f in firs if f["crime_type"] != gt[f["id"]]["crime_type"]],
            "auto": pairwise(auto), "with_suggestions": pairwise(withs),
            "suggestions_correct": sug_ok, "suggestions_total": sug_total,
            "none_in_clusters": sum(1 for n in auto if gt[n]["group_id"] == "NONE"),
            "gang_firs_total": sum(1 for i in ids if gt[i]["group_id"] != "NONE"),
            "gang_firs_linked": sum(1 for n in auto if gt[n]["group_id"] != "NONE"),
            "per_gang": per_gang}


# ---------- Main ----------

def main():
    # Stage 1-2: load and validate firs.json
    firs, rejects, raw_valid = load(DATA)

    # Persist raw source records to SQLite
    db_path = os.path.join(os.path.dirname(os.path.abspath(OUT)), "fir_intel.db")
    conn = init_db(db_path)
    for raw in raw_valid:
        upsert_fir(conn, raw)

    # Stage 3-4: rule extraction + classification (baseline seed)
    # seed_baseline attaches f["x"] and f["crime_type"] to in-memory FIRs for
    # any that don't already have an accepted extraction. On re-runs it skips
    # those, so we must attach from the store for the ones it skipped.
    passed, failed = seed_baseline(conn, firs)

    # Ensure every fir has f["x"] and f["crime_type"] before running the engine.
    # seed_baseline skips FIRs already in the store, so we fill gaps from there.
    from store.db import latest_extractions as _latest_ext
    from ingest.rule_extract import rule_extract as _rule_extract
    from engine.baseline import classify as _classify
    ext_map = _latest_ext(conn)
    for f in firs:
        if "crime_type" not in f:
            ext = ext_map.get(f["id"])
            if ext:
                ids = (ext.get("identifiers_json") or {})
                f["x"] = {
                    "phones": ids.get("phones") or [],
                    "upis": ids.get("upis") or [],
                    "vehicle_regs": ids.get("vehicle_regs") or [],
                    "vehicle_last4": ids.get("vehicle_last4") or [],
                    "vehicle_desc": ids.get("vehicle_desc"),
                    "amounts": [],
                    "evidence": [],
                }
                f["crime_type"] = ext.get("crime_type") or "other"
            else:
                f["x"] = _rule_extract(f)
                f["crime_type"], f["ct_conf"], _ = _classify(f)

    # Stage 5+: run the intelligence engine
    engine_out = run_engine(conn, firs)
    conn.close()

    # Unpack engine output
    clusters  = engine_out["clusters"]
    offenders = engine_out["offenders"]
    raw_links = engine_out["raw_links"]
    months    = engine_out["months"]
    stations  = engine_out["stations"]
    hubs      = engine_out["hubs"]

    # Evaluate (reads answer key — only place allowed to do so)
    ev = evaluate(firs, clusters, offenders, DATA)

    # Build results.json in the same format as before
    idx = {f["id"]: f for f in firs}
    where = {n: c["label"] for c in clusters for n in c["members"]}

    cl_out = []
    for c in clusters:
        m = sorted(c["members"], key=lambda n: idx[n]["occurred"])
        cl_out.append({
            "label": c["label"],
            "crime_type": c["crime_type"],
            "size": len(m),
            "districts": sorted({idx[n]["district"] for n in m}),
            "stations": sorted({idx[n]["station"] for n in m}),
            "first": idx[m[0]]["occurred"][:10],
            "last": idx[m[-1]]["occurred"][:10],
            "strength": round(sum(e["score"] for e in c["edges"]) / len(c["edges"]), 2),
            "loss_inr": sum(idx[n]["property"].get("value_inr") or 0 for n in m),
            "firs": m,
            "suggested": c["suggested"],
            "edges": [{"a": e["a"], "b": e["b"], "score": e["score"], "evidence": e["evidence"]} for e in c["edges"]],
            "victims": [
                {"fir": n, "name": idx[n]["victim"]["name"], "age": idx[n]["victim"]["age"],
                 "gender": idx[n]["victim"]["gender"], "occupation": idx[n]["victim"]["occupation"],
                 "district": idx[n]["district"], "station": idx[n]["station"],
                 "date": idx[n]["occurred"][:10], "loss": idx[n]["property"].get("value_inr")}
                for n in m
            ],
        })

    fir_out = [
        {"id": f["id"], "district": f["district"], "station": f["station"],
         "date": f["occurred"][:10], "crime_type": f["crime_type"],
         "language": f["language"], "accused": f["accused"], "victim": f["victim"],
         "loss": f["property"].get("value_inr"), "narrative": f["narrative"],
         "evidence": f["x"]["evidence"], "cluster": where.get(f["id"])}
        for f in firs
    ]

    W = {
        "phone": 0.90, "upi": 0.90, "vehicle_reg": 0.85, "person_strong": 0.70,
        "vehicle_last4_desc": 0.55, "person_name": 0.30, "vehicle_last4": 0.25, "mo_support": 0.30,
    }

    res = {
        "generated": datetime.now().isoformat(timespec="minutes"),
        "n_firs": len(firs),
        "rejects": rejects,
        "months": months,
        "stations": stations,
        "clusters": cl_out,
        "offenders": offenders,
        "links": engine_out["links"],
        "hubs": [list(h) for h in hubs],
        "weights": W,
        "crime_counts": dict(collections.Counter(f["crime_type"] for f in firs).most_common()),
        "firs": fir_out,
        "eval": ev,
    }

    json.dump(res, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"clusters={len(cl_out)} offenders={len(offenders)} links={res['links']}")
    print(f"crime_type={ev['crime_type_accuracy']} auto={ev['auto']} with_sugg={ev['with_suggestions']}")
    print(f"suggestions {ev['suggestions_correct']}/{ev['suggestions_total']}  none_in_clusters={ev['none_in_clusters']}  gang firs linked {ev['gang_firs_linked']}/{ev['gang_firs_total']}")
    for g in ev["per_gang"]:
        print(" ", {k: g[k] for k in ("group_id", "size", "cluster", "linked", "linked_plus_suggested", "impure", "signals_found", "signals_total")})


if __name__ == "__main__":
    main()
