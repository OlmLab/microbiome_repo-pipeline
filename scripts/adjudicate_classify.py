"""Tie-break (adjudication) of stage-1 classifications where the two readings disagree, or only one reading exists.
Opus-class 'adjudicate' role, one request per project, same evidence as the readings plus both readings' answers.
Writes OUT/<accession>.json {"adjudicated": {...classify fields...}} and OUT/ledger.csv; prints counts only.

usage (in a Claude Science python kernel, `host` defined):
    runpy.run_path("scripts/adjudicate_classify.py", init_globals={"host": host, "ARGS": [IDS.txt, MERGED.parquet, DOSSIERS, OUT, BUDGET]}, run_name="__main__")
"""
import collections, csv, json, os, sys
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
import pilot_passes as PPS
from catalog.llm_backend import LLM, BudgetExceeded
from catalog import models as M

CACHE = os.path.expanduser(os.environ.get("CATALOG_CACHE_DIR", "~/catalog/cache"))


def main(ids_path, merged, dossiers, out, budget):
    os.makedirs(out, exist_ok=True)
    if globals().get("host") is not None:
        M.set_host(globals()["host"])
    ids = [x.strip() for x in open(ids_path) if x.strip()]
    mg = pd.read_parquet(merged).set_index("study_accession")
    llm = LLM("adjudicate", cache_dir=f"{CACHE}/llm/stage1_adjudicate", ledger=f"{out}/ledger.csv", budget_usd=float(budget),
              pass_name="stage1_adjudicate", max_concurrency=8)
    reqs, keys = [], []
    for p in ids:
        d = json.load(open(f"{dossiers}/{p}.json"))
        readings_dir = os.environ.get("CLASSIFY_READINGS_DIR") or f"{os.path.dirname(merged)}/out"
        r0 = json.load(open(f"{readings_dir}/{mg.loc[p, 'batch']}/{p}.json"))
        prior = []
        for k in ("classify_1", "classify_2"):
            v = r0.get(k)
            if isinstance(v, dict) and "status" not in v:
                prior.append(f"{k}: in_scope={v.get('in_scope')} out_reason={v.get('out_reason')} nucleic_acid={v.get('nucleic_acid')} "
                             f"body_sites={v.get('body_sites')} evidence: {v.get('evidence')}")
            else:
                prior.append(f"{k}: no answer")
        titles = "\n".join(f"- [{x.get('methods')}] {x.get('title')} ({x.get('year')})" for x in d["papers"][:8])
        reqs.append({"system": "You settle a disagreement between two classifications of a sequencing project for a human microbiome catalog. "
                               + PPS.SCOPE_RULES + " Weigh the evidence yourself; do not average the two answers. Give the final classification by calling the classify tool exactly once.",
                     "prompt": PPS.dossier_text(d) + "\n\nCandidate papers (titles only):\n" + titles + "\n\nExample samples:\n"
                               + PPS.example_samples_text(d) + "\n\nThe two earlier readings:\n" + "\n".join(prior),
                     "tools": [PPS.CLASSIFY_TOOL], "tool_choice": {"type": "auto"}, "max_tokens": 8000})
        keys.append(p)
    stats = {}
    try:
        res = llm(reqs)
    except BudgetExceeded as e:
        res = []; stats["budget"] = str(e)
    st = collections.Counter(); errs = collections.Counter()
    for p, r in zip(keys, res):
        ok = r["status"] in ("ok", "cached") and r.get("tool_use")
        v = r["tool_use"] if ok else {"status": r.get("status_original") or r["status"], "error": (r.get("error") or "")[:300]}
        if ok:
            v, _ = PPS.validate(v, {"body_sites": PPS.SITES, "processing": PPS.PROC, "sample_types_present": PPS.STYPES,
                                    "study_shape": PPS.SHAPES, "out_reason": PPS.OUT_REASONS + ["none"]})
        json.dump({"adjudicated": v, "model": r.get("model")}, open(f"{out}/{p}.json", "w"), indent=1, default=str)
        st[r["status"]] += 1
        if r["status"] == "error":
            errs[(r.get("error") or "")[:160]] += 1
    led = list(csv.DictReader(open(f"{out}/ledger.csv"))) if os.path.exists(f"{out}/ledger.csv") else []
    print(json.dumps({"projects": len(keys), "status": dict(st), "usd": round(sum(float(x["usd"]) for x in led), 3),
                      "models": dict(collections.Counter(x["model"] for x in led)), "errors": dict(errs.most_common(2)), **stats}))


if __name__ == "__main__":
    main(*(globals().get("ARGS") or sys.argv[1:6]))
