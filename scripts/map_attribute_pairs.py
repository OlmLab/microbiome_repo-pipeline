"""map_attribute_pairs.py — map distinct BioSample (field, key, value) pairs to catalog codes (R2026.30 stage 5).

Each request carries up to 40 distinct pairs of one field and returns, per pair: body_site_code (body_sites.yaml), age_days and
life_stage (life_stages.yaml), sex, country_iso2, condition_code (health_conditions.yaml), confidence, null_output.
Only the field's own columns are kept. The output has the columns of data/inputs/registry/registry_pairs_normalised.parquet
plus condition_code, so build_biosamples can use it directly. Values are never invented: null_output=true when the value
does not state the field (e.g. 'missing', a sample code). Prints counts only.

usage (Claude Science kernel, `host` defined):
    runpy.run_path("scripts/map_attribute_pairs.py", init_globals={"host": host, "ARGS": [PAIRS_SHARD.parquet, OUT.parquet, LEDGER_DIR, BUDGET]}, run_name="__main__")
"""
import collections, csv, json, os, sys
import pandas as pd
import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "src"))
from catalog.llm_backend import LLM, BudgetExceeded
from catalog import models as M

CACHE = os.path.expanduser(os.environ.get("CATALOG_CACHE_DIR", "~/catalog/cache"))
VOC = os.path.join(HERE, "..", "config", "vocab")
codes = lambda f: list((yaml.safe_load(open(os.path.join(VOC, f + ".yaml"))).get("codes") or {}).keys())
SITES, STAGES, CONDS = codes("body_sites"), codes("life_stages"), codes("health_conditions")
N = {"type": ["string", "null"]}
TOOL = {"name": "map_pairs", "description": "normalised codes for each pair",
        "input_schema": {"type": "object", "properties": {"pairs": {"type": "array", "items": {"type": "object", "properties": {
            "pair_id": {"type": "integer"},
            "body_site_code": {"type": ["string", "null"], "enum": SITES + [None]},
            "age_days": {"type": ["number", "null"]},
            "life_stage": {"type": ["string", "null"], "enum": STAGES + [None]},
            "sex": {"type": ["string", "null"], "enum": ["female", "male", "mixed", None]},
            "country_iso2": N,
            "condition_code": {"type": ["string", "null"], "enum": CONDS + [None]},
            "confidence": {"type": "number"},
            "null_output": {"type": "boolean"}}, "required": ["pair_id", "confidence", "null_output"]}}}, "required": ["pairs"]}}
SYSTEM = {
    "body_site": "Map each sample-attribute value to the human body site it was taken from, using one code: " + ", ".join(SITES)
                 + ". gut_stool = faeces, rectal swab, intestinal content or mucosa; oral = saliva, plaque, tongue, throat/oropharynx; "
                   "respiratory_lower = sputum, BAL, endotracheal aspirate, lung tissue; nasal_nasopharyngeal = nose, nasopharynx; "
                   "vaginal_urogenital = vagina, cervix, vulva, penis, urine, semen, urethra, placenta, amniotic fluid; blood_tissue = blood, "
                   "plasma, serum, CSF, bone marrow and every solid organ tissue or biopsy, including kidney, bladder, prostate, breast, "
                   "liver, brain and tumours of those organs; skin = skin surface swabs and skin biopsies; other_site = a stated site that "
                   "fits none of these (e.g. wound fluid, abscess pus, joint fluid); multi_site = several sites in one value. "
                   "null_output=true when the value names no body site (a sample code, 'missing', a lab or environment).",
    "age": "Convert each age value to age_days (years x 365.25, months x 30.44, weeks x 7). If only a category is stated, leave "
           "age_days null and give life_stage (" + ", ".join(STAGES) + "). A range gives its midpoint and life_stage only if the whole "
           "range falls in one stage. Use the key name for the unit when the value is a bare number (e.g. key age_months). "
           "null_output=true for missing values or numbers whose unit cannot be determined.",
    "sex": "Give sex: female, male, or mixed (pooled sample). null_output=true when not stated.",
    "country": "Give the ISO 3166-1 alpha-2 code of the country in the value (e.g. 'USA: Boston' -> US, 'Uganda: Torroro' -> UG). "
               "null_output=true when no country is identifiable.",
    "disease": "Map each value to the participant's health condition using one code: " + ", ".join(CONDS)
               + ". healthy_control only when the value says healthy/control/none. gi_infection_or_diarrhoea for enteric infection or "
                 "diarrhoea; other_infection for other infections; sepsis_or_infection for sepsis or bloodstream infection. A study "
                 "group label that is not a condition (e.g. 'group A', 'baseline') is null_output=true.",
}
KEEP = {"body_site": ["body_site_code"], "age": ["age_days", "life_stage"], "sex": ["sex"], "country": ["country_iso2"],
        "disease": ["condition_code"]}


def main(shard, out, ledger_dir, budget):
    if globals().get("host") is not None:
        M.set_host(globals()["host"])
    os.makedirs(ledger_dir, exist_ok=True)
    df = pd.read_parquet(shard).reset_index(drop=True)
    df["pair_id"] = range(len(df))
    llm = LLM("screen", cache_dir=f"{CACHE}/llm/stage5_pairs", ledger=f"{ledger_dir}/ledger.csv", budget_usd=float(budget),
              pass_name="stage5_pairs", max_concurrency=8)
    reqs, keys = [], []
    for fld, g in df.groupby("field"):
        if fld not in SYSTEM:
            continue
        for i in range(0, len(g), 40):
            ch = g.iloc[i:i + 40]
            lines = "\n".join(f"{r.pair_id}\tkey={r.attr_key_norm}\tvalue={str(r.attr_value)[:200]}" for r in ch.itertuples())
            reqs.append({"system": SYSTEM[fld] + " Return one entry per pair_id.", "prompt": f"Field: {fld}\npair_id\tkey\tvalue\n{lines}",
                         "tools": [TOOL], "tool_choice": {"type": "tool", "name": "map_pairs"}, "max_tokens": 6000})
            keys.append((fld, list(ch.pair_id)))
    stats = {}
    try:
        res = llm(reqs)
    except BudgetExceeded as e:
        res = []; stats["budget"] = str(e)
    rows, st = [], collections.Counter()
    for (fld, ids), r in zip(keys, res):
        st[r["status"]] += 1
        got = {}
        tu = r.get("tool_use") if r["status"] in ("ok", "cached") else None
        if isinstance(tu, str):
            try:
                tu = json.loads(tu)
            except ValueError:
                tu = None
        for x in (tu or {}).get("pairs", []) if isinstance(tu, dict) else []:
            if isinstance(x, dict) and x.get("pair_id") in ids:
                got[x["pair_id"]] = x
        for pid in ids:
            x = got.get(pid)
            base = df.loc[pid, ["field", "attr_key_norm", "attr_value"]].to_dict()
            if x is None:
                rows.append(base | {"status": "unmapped", "null_output": True, "confidence": 0.0}); continue
            row = base | {"status": "ok", "confidence": float(x.get("confidence") or 0), "null_output": bool(x.get("null_output"))}
            for c in KEEP[fld]:
                v = x.get(c)
                if c == "age_days":
                    try:
                        v = float(v) if v is not None and not isinstance(v, (dict, list, bool)) else None
                    except (TypeError, ValueError):
                        v = None
                elif v is not None and not isinstance(v, str):
                    v = None
                row[c] = v
            rows.append(row)
    outdf = pd.DataFrame(rows)
    for c in ["body_site_code", "life_stage", "age_days", "sex", "country_iso2", "condition_code"]:
        if c not in outdf:
            outdf[c] = None
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    outdf.to_parquet(out, index=False)
    led = list(csv.DictReader(open(f"{ledger_dir}/ledger.csv"))) if os.path.exists(f"{ledger_dir}/ledger.csv") else []
    print(json.dumps({"pairs": len(df), "mapped": int((outdf.status == "ok").sum()), "null_output": int(outdf.null_output.sum()),
                      "requests": dict(st), "usd": round(sum(float(x["usd"]) for x in led), 3),
                      "models": dict(collections.Counter(x["model"] for x in led)), **stats}))


if __name__ == "__main__":
    main(*(globals().get("ARGS") or sys.argv[1:5]))
