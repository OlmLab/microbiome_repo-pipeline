"""Score the setting / exposure block of the R2026.30 pilot (two readings of the generating paper per project) without gold labels:
  grounding   every quote and place name must occur in the text the model was given (normalised substring match)
  agreement   reading 1 vs reading 2 per field
  recall      keyword hits (scan_exposure_concepts.CONCEPTS) in the same text that a small model labels PERSON/COHORT,
              versus concepts either reading reported
  precision   a small model checks each reported item (concept, scale, summary, quote) against its quote
Writes OUT/setting_items.csv, OUT/setting_projects.csv, OUT/SETTING_SCORE.md. Needs `host` for the two small-model checks.
usage: runpy with ARGS = [PILOT_DIR, DOSSIER_DIR, OUT]
"""
import glob, json, os, re, sys, unicodedata
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pilot_passes as PPS
import scan_exposure_concepts as SC

MAP = {"sanitation": "sanitation", "drinking_water": "drinking_water", "handwashing": "handwashing", "floor_housing": "floor_or_housing",
       "crowding": "household_size_or_crowding", "siblings": "siblings", "daycare": "daycare", "livestock": "livestock_or_animals", "pets": "pets",
       "farm_living": "farm_living", "deworming": "deworming", "parasite_testing": "parasite_or_pathogen_testing", "diarrhoea": "diarrhoea",
       "socioeconomic": "socioeconomic", "wash_intervention": "wash_intervention", "travel": "travel", "breastfeeding": "breastfeeding"}


def norm(t):
    t = unicodedata.normalize("NFKC", t or "").lower()
    t = re.sub(r"[‐-―−]", "-", t)
    return re.sub(r"[^a-z0-9%]+", " ", t).strip()


def grounded(q, text_n):
    qn = norm(q)
    if not qn:
        return None
    if qn in text_n:
        return True
    w = qn.split()                      # tolerate an ellipsis or small edit: 8-word windows
    if len(w) >= 10:
        hits = sum(" ".join(w[i:i + 8]) in text_n for i in range(0, len(w) - 7, 4))
        return hits >= max(1, (len(w) - 7) // 8)
    return False


def jacc(a, b):
    a, b = set(a or []), set(b or [])
    return 1.0 if not a and not b else len(a & b) / len(a | b)


JUDGE_SYS = ("You check one extracted item from a microbiome paper. Answer one word. YES if the quote shows that the paper records this "
             "concept for its human participants at the stated scale (per_person = a value per participant; per_group = summaries by group; "
             "whole_cohort = applies to all participants, e.g. an inclusion/exclusion criterion or setting). NO if the quote is about "
             "laboratory procedures, reagents, animals in experiments, other studies, or does not support the concept or scale.")
LABEL_SYS = ("You label short text snippets from microbiome papers. Answer with one word: PERSON if the snippet reports this concept for "
             "individual participants or households (a variable, a per-person or per-group value, a table column); COHORT if it states "
             "something that applies to the whole study population or setting (inclusion/exclusion criterion, study site description); "
             "OTHER if it is unrelated to the participants (lab reagent, method, animal model, background or discussion of other studies).")


def word(x):
    t = (x.get("text") or "").strip().split() if isinstance(x, dict) else []
    return t[0].upper().strip(".,") if t else "ERR"


def main(pilot_dir, dossier_dir, out):
    os.makedirs(out, exist_ok=True)
    files = sorted(glob.glob(f"{pilot_dir}/out4_*/*.json"))
    files = [f for f in files if not os.path.basename(f).startswith("stats_")]
    items, projs, miss_q = [], [], []
    for f in files:
        p = os.path.basename(f)[:-5]
        r = json.load(open(f)); d = json.load(open(f"{dossier_dir}/{p}.json"))
        a, b = r.get("fulltext") or {}, r.get("fulltext2") or {}
        pid = a.get("paper_id") or b.get("paper_id")
        pm = next((x.get("pmcid") for x in d["papers"] if x.get("paper_id") == pid), None)
        text = PPS.paper_sections(pm) if pm else None
        row = {"project": p, "pmcid": pm, "read1": "setting" in a, "read2": "setting" in b}
        if not text or not row["read1"] or not row["read2"]:
            row["status"] = a.get("status") or b.get("status") or ("no_text" if not text else "missing_setting")
            projs.append(row); continue
        tn = norm(text)
        def _obj(x):
            if not isinstance(x, str):
                return x
            try:
                return json.loads(x)
            except ValueError:
                return None                      # malformed JSON string: counted as setting_not_object
        s1, s2 = (PPS.coerce_setting(x) for x in (_obj(a["setting"]), _obj(b["setting"])))
        if not isinstance(s1, dict) or not isinstance(s2, dict):
            row["status"] = "setting_not_object"; projs.append(row); continue
        row["setting_was_string"] = isinstance(a["setting"], str) or isinstance(b["setting"], str)
        row.update(status="ok",
                   recruit_eq=set(s1.get("recruitment_setting") or []) == set(s2.get("recruitment_setting") or []),
                   settle_eq=set(s1.get("settlement_type") or []) == set(s2.get("settlement_type") or []),
                   lifestyle_eq=s1.get("lifestyle") == s2.get("lifestyle"),
                   places_j=jacc({norm(x.get("name")) for x in s1.get("places") or []}, {norm(x.get("name")) for x in s2.get("places") or []}),
                   concepts_j=jacc({x.get("concept") for x in s1.get("exposure_variables") or []}, {x.get("concept") for x in s2.get("exposure_variables") or []}),
                   recruit1=";".join(s1.get("recruitment_setting") or []), settle1=";".join(s1.get("settlement_type") or []),
                   lifestyle1=s1.get("lifestyle"), lifestyle2=s2.get("lifestyle"),
                   places1="; ".join(f"{x.get('name')} [{x.get('level')}, {x.get('is_residence')}]" for x in s1.get("places") or []))
        sc = {}
        for rep, s in ((1, s1), (2, s2)):
            for x in s.get("places") or []:
                items.append({"project": p, "reading": rep, "kind": "place", "concept": x.get("level"), "scale": x.get("is_residence"),
                              "summary": x.get("name"), "quote": x.get("name"), "grounded": grounded(x.get("name"), tn)})
            for x in s.get("exposure_variables") or []:
                items.append({"project": p, "reading": rep, "kind": "exposure", "concept": x.get("concept"), "scale": x.get("scale"),
                              "summary": x.get("summary"), "quote": x.get("quote"), "grounded": grounded(x.get("quote"), tn)})
                sc.setdefault(x.get("concept"), set()).add((rep, x.get("scale")))
        row["scale_agree"] = sum(1 for c, v in sc.items() if {r for r, _ in v} == {1, 2} and len({s for _, s in v}) == 1)
        row["both_concepts"] = sum(1 for c, v in sc.items() if {r for r, _ in v} == {1, 2})
        reported = set(sc)
        for k, rx in SC.RX.items():          # recall candidates: keyword hit in the same text
            if k not in MAP:
                continue
            m = rx.search(text)
            if m:
                snip = text[max(0, m.start() - 160): m.end() + 160]
                miss_q.append({"project": p, "concept": MAP[k], "snippet": snip, "reported": MAP[k] in reported})
        projs.append(row)
    I, P, Q = pd.DataFrame(items), pd.DataFrame(projs), pd.DataFrame(miss_q)
    # small-model checks
    ex = I[I.kind == "exposure"].reset_index(drop=True)
    jr = [{"prompt": f"Concept: {r.concept}\nScale: {r.scale}\nSummary: {r.summary}\nQuote: {r.quote}", "system": JUDGE_SYS, "max_tokens": 4} for r in ex.itertuples()]
    lr = [{"prompt": f"Concept: {r.concept}\nSnippet: {r.snippet}", "system": LABEL_SYS, "max_tokens": 4} for r in Q.itertuples()]
    res = []
    for i in range(0, len(jr + lr), 400):
        res += host.llm((jr + lr)[i:i + 400], max_concurrency=16)
    ex["judge"] = [word(x) for x in res[:len(jr)]]
    Q["label"] = [word(x) for x in res[len(jr):]]
    I = I.merge(ex[["project", "reading", "concept", "quote", "judge"]], on=["project", "reading", "concept", "quote"], how="left")
    I.to_csv(f"{out}/setting_items.csv", index=False); P.to_csv(f"{out}/setting_projects.csv", index=False); Q.to_csv(f"{out}/setting_recall_hits.csv", index=False)
    ok = P[P.status == "ok"]
    true_hits = Q[Q.label.isin(["PERSON", "COHORT"])]
    rec = true_hits.reported.mean() if len(true_hits) else float("nan")
    exI = I[I.kind == "exposure"]; plI = I[I.kind == "place"]
    lines = [f"# Setting / exposure block — pilot scoring ({len(P)} projects, {len(ok)} with both readings of a generating paper)", "",
             "| Measure | Value |", "|---|---|",
             f"| recruitment_setting identical in both readings | {ok.recruit_eq.mean():.0%} |",
             f"| settlement_type identical | {ok.settle_eq.mean():.0%} |",
             f"| lifestyle code identical | {ok.lifestyle_eq.mean():.0%} |",
             f"| places: mean overlap (Jaccard) of names | {ok.places_j.mean():.2f} |",
             f"| exposure concepts: mean overlap (Jaccard) | {ok.concepts_j.mean():.2f} |",
             f"| concepts found by both readings with the same scale | {int(ok.scale_agree.sum())} of {int(ok.both_concepts.sum())} |",
             f"| place names found verbatim in the text | {plI.grounded.mean():.0%} ({len(plI)}) |",
             f"| exposure quotes found verbatim in the text | {exI.grounded.mean():.0%} ({len(exI)}) |",
             f"| exposure items the checker accepts (YES) | {(exI.judge == 'YES').mean():.0%} |",
             f"| recall: keyword hits labelled PERSON/COHORT that a reading reported | {rec:.0%} ({int(true_hits.reported.sum())} of {len(true_hits)}) |",
             "", "Projects with any exposure item: " + str(int((exI.groupby('project').size() > 0).sum())),
             "", "Exposure items by concept (reading 1):", ""]
    c1 = exI[exI.reading == 1].groupby("concept").agg(n=("project", "size"), projects=("project", "nunique"), grounded=("grounded", "mean"),
                                                     judge_yes=("judge", lambda x: (x == "YES").mean())).sort_values("n", ascending=False)
    lines.append(c1.to_markdown(floatfmt=".2f"))
    lines += ["", "Missed concepts (keyword hit labelled PERSON/COHORT, not reported by either reading):", ""]
    mm = true_hits[~true_hits.reported].groupby("concept").size().sort_values(ascending=False)
    lines.append(mm.to_markdown() if len(mm) else "none")
    lines += ["", "Recruitment setting, reading 1: " + json.dumps(ok.recruit1.str.split(";").explode().value_counts().to_dict()),
              "Settlement type, reading 1: " + json.dumps(ok.settle1.str.split(";").explode().value_counts().to_dict()),
              "Lifestyle, reading 1: " + json.dumps(ok.lifestyle1.value_counts().to_dict()),
              "Projects without both readings: " + json.dumps(P[P.status != "ok"].status.value_counts().to_dict())]
    open(f"{out}/SETTING_SCORE.md", "w").write("\n".join(lines))
    print("\n".join(lines[:16]))


if __name__ == "__main__":
    main(*(globals().get("ARGS") or sys.argv[1:4]))
