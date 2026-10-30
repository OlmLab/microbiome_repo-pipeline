"""blind_audit_scope.py — independent re-classification of a random sample of projects at a different model tier
(R2026.30 release check).

The stage-1 verdict came from two readings at the utility tier, with an Opus tie-break where they disagreed. This
audit takes a stratified random sample of projects, sends each ONE request at the `audit` tier with exactly the same
evidence and the same scope rules, and compares the verdict. The auditor never sees the stage-1 answer, so agreement
is a genuine reliability estimate rather than a confirmation.

Outputs AUDIT_DIR/audit.parquet (one row per project: stage-1 verdict, audit verdict, agreement) and prints the
agreement rate per verdict class plus every disagreement, which is the list a human should look at.

usage (Claude Science kernel, `host` defined):
    runpy.run_path("scripts/blind_audit_scope.py", init_globals={"host": host,
        "ARGS": [CLASSIFY.parquet, DOSSIERS, AUDIT_DIR, N, BUDGET]}, run_name="__main__")
"""
import json, os, sys

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "src"))
import pilot_passes as PPS
from catalog.llm_backend import LLM, BudgetExceeded
from catalog import models as M

CACHE = os.path.expanduser(os.environ.get("CATALOG_CACHE_DIR", "~/catalog/cache"))


def main(classify, dossiers, audit_dir, n, budget, projects=None, seed=20261029):
    if globals().get("host") is not None:
        M.set_host(globals()["host"])
    os.makedirs(audit_dir, exist_ok=True)
    cls = pd.read_parquet(classify)
    cls = cls[cls["in_scope"].notna()]
    n = int(n)
    # stratified by verdict, proportional, at least 10 of each class present
    if projects:
        want = {l.strip() for l in open(projects) if l.strip()}
        smp = cls[cls["study_accession"].isin(want)]
        smp = smp[[os.path.exists(os.path.join(dossiers, f"{p}.json")) for p in smp["study_accession"]]]
        return _run(cls, smp, dossiers, audit_dir, budget)
    take = []
    for v, g in cls.groupby("in_scope"):
        k = max(10, int(round(n * len(g) / len(cls)))) if len(g) >= 10 else len(g)
        take.append(g.sample(min(k, len(g)), random_state=seed))
    smp = pd.concat(take).drop_duplicates("study_accession")
    smp = smp[[os.path.exists(os.path.join(dossiers, f"{p}.json")) for p in smp["study_accession"]]]
    return _run(cls, smp, dossiers, audit_dir, budget)


def _run(cls, smp, dossiers, audit_dir, budget):
    llm = LLM("audit", cache_dir=f"{CACHE}/llm/stage8_audit", ledger=f"{audit_dir}/ledger.csv",
              budget_usd=float(budget), pass_name="stage8_audit", max_concurrency=6)
    reqs, keys = [], []
    for p in smp["study_accession"]:
        d = json.load(open(os.path.join(dossiers, f"{p}.json")))
        titles = "\n".join(f"- [{x.get('methods')}] {x.get('title')} ({x.get('year')})" for x in d["papers"][:8])
        reqs.append({"system": "You classify sequencing projects for a human microbiome catalog. " + PPS.SCOPE_RULES +
                               "\n(independent audit reading)",
                     "prompt": PPS.dossier_text(d) + "\n\nCandidate papers (titles only):\n" + titles +
                               "\n\nExample samples:\n" + PPS.example_samples_text(d),
                     "tools": [PPS.CLASSIFY_TOOL], "tool_choice": {"type": "tool", "name": "classify"},
                     "max_tokens": 2000})
        keys.append(p)
    try:
        res = llm(reqs)
    except BudgetExceeded as e:
        print(json.dumps({"error": "budget", "detail": str(e)}))
        return

    rows = []
    for p, r in zip(keys, res):
        v = r.get("tool_use") if r.get("status") in ("ok", "cached") else None
        if isinstance(v, str):
            try:
                v = json.loads(v)
            except ValueError:
                v = None
        s1 = cls[cls["study_accession"] == p].iloc[0]
        rows.append({"study_accession": p, "stage1_in_scope": s1["in_scope"], "stage1_status": s1["status"],
                     "stage1_out_reason": s1.get("out_reason"), "stage1_nucleic_acid": s1.get("nucleic_acid"),
                     "audit_status": r.get("status"),
                     "audit_in_scope": (v or {}).get("in_scope"), "audit_out_reason": (v or {}).get("out_reason"),
                     "audit_nucleic_acid": (v or {}).get("nucleic_acid"),
                     "audit_confidence": (v or {}).get("confidence"), "audit_evidence": (v or {}).get("evidence")})
    a = pd.DataFrame(rows)
    a.to_parquet(f"{audit_dir}/audit.parquet", index=False)
    ok = a[a["audit_in_scope"].notna()].copy()
    ok["agree"] = ok["stage1_in_scope"] == ok["audit_in_scope"]
    # the decision that matters is in-scope vs out-of-scope, not yes vs partly
    ok["agree_binary"] = ok["stage1_in_scope"].isin(["yes", "partly"]) == ok["audit_in_scope"].isin(["yes", "partly"])
    print(json.dumps({
        "sampled": int(len(a)), "answered": int(len(ok)), "refused_or_error": int(len(a) - len(ok)),
        "agreement_exact": round(float(ok["agree"].mean()), 3),
        "agreement_in_vs_out": round(float(ok["agree_binary"].mean()), 3),
        "by_stage1_verdict": {k: round(float(v), 3) for k, v in ok.groupby("stage1_in_scope")["agree_binary"].mean().items()},
        "disagreements": ok.loc[~ok["agree_binary"], ["study_accession", "stage1_in_scope", "audit_in_scope",
                                                     "audit_out_reason"]].to_dict("records")[:40],
        "llm": llm.summary(),
    }))


if __name__ == "__main__":
    main(*(globals().get("ARGS") or sys.argv[1:7]))
