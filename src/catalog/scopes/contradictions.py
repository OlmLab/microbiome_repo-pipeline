"""Self-contradictory metadata (R2026.25, owner request).

A value is *contradicted* when two sample-level sources give incompatible values for the same sample and field, or when
the samples of one person disagree on a person-constant field (sex). Contradicted values are WITHHELD: the sample gets no
value for that field (and no cohort-wide statement may fill it), and both sides are kept, with their evidence, in
gut_metadata_contradictions.parquet. Project pages show a red "!" badge with a summary.

Incompatible means (formatting / refinement differences are not contradictions):
  sex, country            any different value
  antibiotic_exposure     yes vs no
  age_at_collection_days  differ by > max(30 d, 20 %)
  bmi                     differ by > 2 kg/m2
  health_condition        healthy_control vs a specific disease code (codes that are compatible with "healthy" in
                          study designs — obesity, overweight, preterm_nicu — and the generic other_* / unknown codes
                          are not counted); disease-vs-disease differences are usually refinements or comorbidity and
                          are left to the resolver.
"""
from __future__ import annotations

import pandas as pd

CONTRA_FIELDS = ("sex", "country", "antibiotic_exposure", "age_at_collection_days", "bmi", "health_condition")
HEALTHY_COMPATIBLE = {"obesity", "overweight", "preterm_nicu", "unknown", "healthy_control"}
COLS = ["sample_key", "study_accession", "field_name", "kind", "value_a", "route_a", "source_a", "quote_a",
        "value_b", "route_b", "source_b", "quote_b", "handling", "note"]


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def incompatible(field: str, a, b) -> bool:
    a, b = str(a).strip().lower(), str(b).strip().lower()
    if a == b or a in ("", "nan", "none") or b in ("", "nan", "none"):
        return False
    if field in ("sex", "country"):
        return True
    if field == "antibiotic_exposure":
        return {a, b} == {"yes", "no"}
    if field in ("age_at_collection_days", "bmi"):
        x, y = _num(a), _num(b)
        if x is None or y is None:
            return False
        return abs(x - y) > 2 if field == "bmi" else abs(x - y) > max(30.0, 0.2 * max(abs(x), abs(y)))
    if field == "health_condition":
        if "healthy_control" not in (a, b):
            return False
        other = b if a == "healthy_control" else a
        return not (other in HEALTHY_COMPATIBLE or other.startswith("other"))
    return False


def find_contradictions(winners: pd.DataFrame, losers: pd.DataFrame, extra: pd.DataFrame | None = None,
                        generated_subject_rules: tuple = ()) -> pd.DataFrame:
    """winners: one current sample-level row per (sample, field) (resolve() output); losers: resolve() conflicts;
    extra: further sample-level rows that disagreed with a published value but were not ingested (deep-pass DISAGREES rows).
    Returns one row per contradicted (sample, field)."""
    w = winners[winners.field_name.isin(CONTRA_FIELDS)]
    cand = [losers[losers.field_name.isin(CONTRA_FIELDS)]]
    if extra is not None and len(extra):
        cand.append(extra[extra.field_name.isin(CONTRA_FIELDS)])
    L = pd.concat(cand, ignore_index=True) if cand else pd.DataFrame(columns=w.columns)
    L = L[L.sample_key.isin(set(w.sample_key))]
    m = L.merge(w[["sample_key", "field_name", "value_normalized", "route", "evidence_source", "evidence_quote"]]
                .rename(columns={"value_normalized": "value_a", "route": "route_a", "evidence_source": "source_a", "evidence_quote": "quote_a"}),
                on=["sample_key", "field_name"], how="inner")
    m = m[[incompatible(f, a, b) for f, a, b in zip(m.field_name, m.value_a, m.value_normalized)]]
    m = m.sort_values(["sample_key", "field_name", "confidence"], ascending=[True, True, False]).drop_duplicates(["sample_key", "field_name"])
    src = pd.DataFrame(dict(sample_key=m.sample_key, study_accession=m.study_accession, field_name=m.field_name, kind="sources_disagree",
                            value_a=m.value_a, route_a=m.route_a, source_a=m.source_a, quote_a=m.quote_a,
                            value_b=m.value_normalized, route_b=m.route, source_b=m.evidence_source, quote_b=m.evidence_quote,
                            handling="withheld: neither value published", note=None))
    # within-person: one real (not rule-generated) subject id whose samples carry more than one sex
    sub = winners[(winners.field_name == "subject_id") & ~winners.determined_by.astype(str).isin(generated_subject_rules)][["sample_key", "study_accession", "value_normalized"]]
    sx = w[w.field_name == "sex"][["sample_key", "value_normalized", "route", "evidence_source", "evidence_quote"]]
    ps = sub.rename(columns={"value_normalized": "subject"}).merge(sx, on="sample_key")
    ps["v"] = ps.value_normalized.astype(str).str.lower()
    bad = ps.groupby(["study_accession", "subject"]).v.transform("nunique") > 1
    pp = ps[bad]
    sid = sub.set_index("sample_key").value_normalized
    rows = []
    for (acc, s_), g in pp.groupby(["study_accession", "subject"]):
        vals = g.v.value_counts()
        note = f"subject {s_}: " + ", ".join(f"{k} in {int(n)} sample{'s' if n != 1 else ''}" for k, n in vals.items())
        # a split shared by many samples means the id is not one person (group / household / arm code): withhold the id, keep sex;
        # an odd sample or two in one person's series means the sex values are unreliable: withhold sex
        not_person = len(g) > 20 or vals.iloc[1:].sum() / len(g) > 0.25
        both = len(g) <= 3   # too few samples to tell whether the id or the sex is wrong: withhold both
        for r in g.itertuples(index=False):
            if both:
                rows.append(dict(sample_key=r.sample_key, study_accession=acc, field_name="sex", kind="person_inconsistent", value_a=r.value_normalized, route_a=r.route,
                                 source_a=r.evidence_source, quote_a=r.evidence_quote, value_b=None, route_b=None, source_b=None, quote_b=None,
                                 handling="withheld: neither value published", note=note))
            if not_person or both:
                rows.append(dict(sample_key=r.sample_key, study_accession=acc, field_name="subject_id", kind="id_not_one_person", value_a=sid.get(r.sample_key), route_a=None,
                                 source_a=None, quote_a=None, value_b=r.value_normalized, route_b=r.route, source_b=r.evidence_source, quote_b=r.evidence_quote,
                                 handling="subject id withheld (samples of both sexes share it)" + ("" if both else "; sex kept"), note=note))
            if not (not_person or both):
                rows.append(dict(sample_key=r.sample_key, study_accession=acc, field_name="sex", kind="person_inconsistent", value_a=r.value_normalized, route_a=r.route,
                                 source_a=r.evidence_source, quote_a=r.evidence_quote, value_b=None, route_b=None, source_b=None, quote_b=None,
                                 handling="withheld: neither value published", note=note))
    per = pd.DataFrame(rows, columns=COLS)
    out = pd.concat([src.reindex(columns=COLS), per], ignore_index=True)
    out = out.drop_duplicates(["sample_key", "field_name", "kind"])
    return out.reset_index(drop=True)


EXT_NAMES = {"cmd": "curatedMetagenomicData", "gmrepo": "GMrepo", "meta2db": "Meta2DB", "microbiomedb": "MicrobiomeDB", "hmdb": "HumanMetagenomeDB", "mbodymap": "mBodyMap"}
SOURCE_LABEL = [("external.", None), ("sample.attr", "sequence archive"), ("biosample", "sequence archive"), ("paper.supp", "supplementary table"), ("paper.body", "paper table"),
                ("paper.fulltext", "paper text"), ("paper.abstract", "abstract"), ("sample.name", "sample names"), ("ext.", "external resource"),
                ("study.", "project description")]


def source_label(s) -> str:
    s = str(s or "")
    for k, v in SOURCE_LABEL:
        if s.startswith(k):
            if v is None:
                n = s[len(k):].split(".")[0].split("[")[0]
                return EXT_NAMES.get(n.lower(), n)
            return v
    return s.split("[")[0] or "unknown source"


def study_summary(c: pd.DataFrame) -> pd.DataFrame:
    """Per project: n_contradicted_samples, contradiction_summary (short human-readable text)."""
    rows = []
    for acc, g in c.groupby("study_accession"):
        parts = []
        for (f, k), h in g.groupby(["field_name", "kind"]):
            n = h.sample_key.nunique()
            f_ = f.replace("_at_collection_days", "").replace("_", " ")
            ns = f"{n} sample{'s' if n != 1 else ''}"
            if k == "person_inconsistent":
                parts.append(f"sex differs between samples of the same subject id ({h.note.str.split(':').str[0].nunique()} subjects, {ns}) — sex withheld")
            elif k == "id_not_one_person":
                parts.append(f"subject ids shared by samples of both sexes ({h.note.str.split(':').str[0].nunique()} ids, {ns}) — ids withheld")
            else:
                la, lb = h.source_a.map(source_label), h.source_b.map(source_label)
                pair = pd.Series([("two " + x + "s" if x == y else f"{x} and {y}") for x, y in zip(la, lb)]).value_counts().index[0]
                ex = h.iloc[0]
                parts.append(f"{f_}: {pair} disagree for {ns} (e.g. {ex.value_a} vs {ex.value_b}) — withheld")
        rows.append(dict(study_accession=acc, n_contradicted_samples=int(g.sample_key.nunique()), contradiction_summary="; ".join(parts)))
    return pd.DataFrame(rows, columns=["study_accession", "n_contradicted_samples", "contradiction_summary"])
