"""classify_supp_columns.py — decide which catalog field each supplementary-table column carries (R2026.30 stage 7).

One request per batch of column names, each with a few example values. The answer is a pack field or `none`; a column
the model cannot place is dropped rather than guessed. Columns classified as a vocabulary field are then passed to
scripts/map_attribute_pairs.py, which maps their VALUES with the same prompts and the same cache as the archive
attributes, so the two sources are normalised identically.

usage (Claude Science kernel, `host` defined):
    runpy.run_path("scripts/classify_supp_columns.py", init_globals={"host": host,
        "ARGS": [COLUMNS.parquet, OUT.parquet, LEDGER_DIR, BUDGET]}, run_name="__main__")

COLUMNS.parquet: attr_key_norm, n_samples, n_projects, examples (string, ' | '-joined)
"""
import json, os, sys

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "src"))
from catalog.llm_backend import LLM, BudgetExceeded
from catalog import models as M

CACHE = os.path.expanduser(os.environ.get("CATALOG_CACHE_DIR", "~/catalog/cache"))
FIELDS = ["body_site", "age", "sex", "country", "disease", "subject_id", "timepoint_label", "study_arm",
          "bmi", "diet", "smoking_status", "antibiotic_exposure", "intervention", "collection_date",
          "location_locality", "ethnicity_or_population", "none"]
TOOL = {"name": "classify_columns", "description": "the catalog field each column carries",
        "input_schema": {"type": "object", "properties": {"columns": {"type": "array", "items": {"type": "object",
            "properties": {
                "col_id": {"type": "integer"},
                "field": {"type": "string", "enum": FIELDS},
                "unit": {"type": ["string", "null"], "enum": ["years", "months", "weeks", "days", None]},
                "confidence": {"type": "number"}},
            "required": ["col_id", "field", "confidence"]}}}, "required": ["columns"]}}
SYSTEM = (
    "Each item is one column of a published supplementary table that has been joined to sequencing samples. Say which "
    "catalog field the column carries, from: " + ", ".join(FIELDS) + ".\n"
    "Rules: `age` only for the participant's age at sampling (give the unit the numbers are in; for gestational age or "
    "age of a non-participant use none). `sex` for sex or gender of the participant. `disease` for a diagnosis, health "
    "condition or case/control status. `study_arm` for a treatment arm, group or cohort label that is not itself a "
    "diagnosis. `subject_id` for a person identifier, `timepoint_label` for a visit, day or time-point label. "
    "`body_site` for the specimen site. `ethnicity_or_population` for ethnicity, race or population labels. "
    "Everything else — sequencing statistics, assay readouts, sample or file identifiers, taxonomic abundances, "
    "clinical measurements the catalog does not hold — is `none`. When a column name is ambiguous and the example "
    "values do not settle it, answer none; a wrong field is worse than a missing one.")


def main(cols_path, out, ledger_dir, budget, batch=50):
    if globals().get("host") is not None:
        M.set_host(globals()["host"])
    os.makedirs(ledger_dir, exist_ok=True)
    df = pd.read_parquet(cols_path).reset_index(drop=True)
    df["col_id"] = range(len(df))
    llm = LLM("screen", cache_dir=f"{CACHE}/llm/stage7_columns", ledger=f"{ledger_dir}/ledger.csv",
              budget_usd=float(budget), pass_name="stage7_columns", max_concurrency=8)
    reqs = []
    for i in range(0, len(df), int(batch)):
        b = df.iloc[i:i + int(batch)]
        items = [{"col_id": int(r.col_id), "column_name": r.attr_key_norm, "examples": str(r.examples)[:300]}
                 for r in b.itertuples()]
        reqs.append({"system": SYSTEM, "prompt": json.dumps(items), "tools": [TOOL],
                     "tool_choice": {"type": "tool", "name": "classify_columns"}, "max_tokens": 4000})
    stats = {"requests": len(reqs), "ok": 0, "bad": 0}
    try:
        answers = llm(reqs)
    except BudgetExceeded as e:
        print(json.dumps({"error": "budget", "detail": str(e)})); return
    rows = []
    for ans in answers:
        tu = ans.get("tool_use") if ans.get("status") in ("ok", "cached") else None
        if isinstance(tu, str):
            try:
                tu = json.loads(tu)
            except ValueError:
                tu = None
        got = (tu or {}).get("columns") if isinstance(tu, dict) else None
        if not got:
            stats["bad"] += 1
            continue
        stats["ok"] += 1
        rows += [x for x in got if isinstance(x, dict)]
    r = pd.DataFrame(rows).drop_duplicates("col_id")
    r = df.merge(r, on="col_id", how="left")
    r.to_parquet(out, index=False)
    stats["columns"] = int(len(r))
    stats["fields"] = r["field"].value_counts().to_dict()
    stats["llm"] = llm.summary()
    print(json.dumps(stats))


if __name__ == "__main__":
    main(*(globals().get("ARGS") or sys.argv[1:5]))
