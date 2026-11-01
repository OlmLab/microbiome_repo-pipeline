#!/usr/bin/env python
"""design_statement_check.py — an Opus 5 check of every design_statement written by scripts/design_passes.py (R2026.33).

The production audit found an unsupported detail in 7 of 30 statements, most often a sample count given as a participant
count, or a group / comparison that exists only in sample-attribute values. Each statement is checked against the same text
the readings saw (plus the number of specimens in the project) by the `adjudicate` role, which returns whether every fact is
supported and, if not, a revised statement that keeps only supported facts.
Merge (deterministic): supported -> keep; not supported -> the revised statement when it is at least 15 characters, else no
statement. Writes design/design_statement_check_shard_S{k}.parquet; --apply folds the results into
design/design_reading_all.parquet (design_statement replaced, design_statement_original and statement_check kept; backup in
design/backup_pre_statement_check/). Prints counts only.

usage (kernel with `host`, via runpy with init_globals={"host": host}):
  python scripts/design_statement_check.py --shard K --nshards N --budget USD
  python scripts/design_statement_check.py --apply
"""
import argparse
import glob
import json
import os
import shutil
import sys

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, HERE)
import design_passes as DP  # noqa: E402  (texts exactly as the readings saw them)
from catalog import models as M  # noqa: E402
from catalog.llm_backend import LLM, BudgetExceeded  # noqa: E402

M.set_host(globals().get("host"))
CP = DP.CP
GUT = os.path.join(ROOT, "data", "inputs", "gut")
NOTHINK = {"type": "disabled"}

SYSTEM = """You check one sentence that describes the STUDY DESIGN of a sequencing project, against the project's text (archive
record and, when available, the paper or its abstract) and the number of specimens deposited. The sentence will be shown to
catalog users as a summary of the design, so every fact in it must be stated in the text.
Unsupported facts include: a number of participants that the text does not state (a number of samples or specimens is never a
number of participants); a group, arm, control group or comparison that the text does not name (sample-attribute values or
sample names alone are not enough); a body site, country, age group, schedule or count not in the text; anything taken from
the results rather than the design.
Return supported = yes when every fact is stated in the text. Otherwise supported = no, list the unsupported parts, and give
revised_statement: the same sentence with the unsupported parts removed or corrected from the text (at most 40 words, plain
English), or an empty string when nothing reliable is left."""

TOOL = DP.PP.tool("check", {
    "supported": {"type": "string", "enum": ["yes", "no"]},
    "unsupported_parts": {"type": "array", "items": {"type": "string"}},
    "revised_statement": {"type": "string"}}, ["supported", "unsupported_parts", "revised_statement"])


def run(shard, nshards, budget, host=None):
    if host is not None:
        M.set_host(host)
    R = pd.read_parquet(os.path.join(GUT, "design", "design_reading_all.parquet"))
    R = R[R.design_statement.notna() & (R.design_statement.astype(str).str.strip() != "")]
    ids = sorted(R.study_accession)
    mine = [p for i, p in enumerate(ids) if i % nshards == shard]
    R = R.set_index("study_accession")
    st = pd.read_parquet(os.path.join(DP.PKG, "catalog_studies.parquet"))
    st = st[st.release_retired.isna()].set_index("study_accession") if "release_retired" in st.columns else st.set_index("study_accession")
    papers = pd.read_parquet(os.path.join(ROOT, "data", "inputs", "catalog", "study_papers.parquet"))
    papers = papers[papers.relation == "own_data"]
    w = pd.read_parquet(os.path.join(DP.PKG, "catalog_sample_metadata_wide.parquet"), columns=["study_accession", "sample_type", "release_retired"],
                        filters=[("study_accession", "in", mine)])
    w = w[w.release_retired.isna() & (w.sample_type.fillna("specimen") == "specimen")]
    nspec = w.groupby("study_accession").size().to_dict()
    llm = LLM("adjudicate", cache_dir=os.path.expanduser("~/catalog/cache/llm/design_statement_r2026_33"),
              ledger=os.path.join(GUT, "design", "logs", f"ledger_statement_S{shard}.csv"), budget_usd=budget,
              pass_name="design_statement_r2026_33", max_concurrency=4)
    reqs, keys = [], []
    for acc in mine:
        if acc not in st.index:
            continue
        tx = CP.project_texts(acc, CP.load_dossier(acc), st.loc[acc].to_dict(), papers)
        prompt = (tx["prompt"][:24000] + f"\n\nSPECIMENS DEPOSITED IN THIS PROJECT: {int(nspec.get(acc, 0))}\n\nSTATEMENT TO CHECK:\n"
                  + str(R.loc[acc, "design_statement"]))
        reqs.append({"system": SYSTEM, "prompt": prompt, "tools": [TOOL], "tool_choice": {"type": "tool", "name": "check"},
                     "max_tokens": 800, "thinking": NOTHINK})
        keys.append(acc)
    status = {}
    try:
        res = llm(reqs)
    except BudgetExceeded as e:
        res = []
        status["budget"] = str(e)[:200]
    rows = []
    for acc, r in zip(keys, res):
        status[r.get("status")] = status.get(r.get("status"), 0) + 1
        tu = r.get("tool_use") if r.get("status") in ("ok", "cached") else None
        if not isinstance(tu, dict) or tu.get("supported") not in ("yes", "no"):
            rows.append(dict(study_accession=acc, statement_check="missing", revised_statement=None, n_unsupported=None))
            continue
        rev = str(tu.get("revised_statement") or "").strip()
        rows.append(dict(study_accession=acc, statement_check="supported" if tu["supported"] == "yes" else ("revised" if len(rev) >= 15 else "dropped"),
                         revised_statement=rev if tu["supported"] == "no" and len(rev) >= 15 else None,
                         n_unsupported=len(tu.get("unsupported_parts") or [])))
    df = pd.DataFrame(rows, columns=["study_accession", "statement_check", "revised_statement", "n_unsupported"])
    df.to_parquet(os.path.join(GUT, "design", f"design_statement_check_shard_S{shard}.parquet"), index=False)
    summ = dict(shard=shard, statements=len(keys), llm_status=status, result=df.statement_check.value_counts().to_dict(), cost=llm.summary())
    print(json.dumps(summ, default=str))
    return summ


def apply():
    fs = sorted(glob.glob(os.path.join(GUT, "design", "design_statement_check_shard_S*.parquet")))
    C = pd.concat([pd.read_parquet(f) for f in fs], ignore_index=True).drop_duplicates("study_accession", keep="last").set_index("study_accession")
    p = os.path.join(GUT, "design", "design_reading_all.parquet")
    bk = os.path.join(GUT, "design", "backup_pre_statement_check")
    os.makedirs(bk, exist_ok=True)
    if not os.path.exists(os.path.join(bk, os.path.basename(p))):
        shutil.copy2(p, os.path.join(bk, os.path.basename(p)))
    R = pd.read_parquet(os.path.join(bk, os.path.basename(p)))   # always apply to the unchecked version
    R["design_statement_original"] = R.design_statement
    R["statement_check"] = R.study_accession.map(C.statement_check)
    rev = R.study_accession.map(C.revised_statement)
    m_rev = R.statement_check == "revised"
    R.loc[m_rev, "design_statement"] = rev[m_rev]
    R.loc[R.statement_check == "dropped", "design_statement"] = None
    R.to_parquet(p, index=False)
    print(json.dumps({"shards": len(fs), "checked": int(R.statement_check.notna().sum()), "result": R.statement_check.value_counts().to_dict(),
                      "statements_now": int(R.design_statement.notna().sum())}))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--shard", type=int, default=0)
    ap.add_argument("--nshards", type=int, default=1)
    ap.add_argument("--budget", type=float, default=5.0)
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args(argv)
    if a.apply:
        return apply()
    return run(a.shard, a.nshards, a.budget, host=globals().get("host"))


if __name__ == "__main__":
    main()
