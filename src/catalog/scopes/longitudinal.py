"""longitudinal.py — repeated sampling and relatedness, derived deterministically from the gut wide table (R2026.23).

Owner request 2026-10-10: mark projects whose samples are longitudinal, with timing metadata (how often, how long), and show
relatedness / transmission structure (mothers and infants, fathers, siblings, twins, partners, households, villages, daycares ...).

Per sample (added to gut_sample_metadata_wide):
  subject_n_samples       gut samples (body_site_class primary / unknown) of the same person in the project; null without a real
                          subject_id (generated one-sample-per-subject ids count as 1)
  days_since_first_sample days from the person's first gut sample, on the person's time axis (null when no axis)
  time_axis               age (age_at_collection_days) > date (full collection_date) > timepoint (parsed day / week / month / year
                          labels, baseline / birth = 0); the first axis that gives >= 2 distinct values for the person
  person_key              the person used for relatedness: subject_id, else family_id + role for parents / partners, else sample_key

Per project (gut_studies): n_subjects, n_subjects_repeated, n_samples_repeated, samples_per_subject_median / _max,
followup_days_median / _max, interval_days_median, time_axis, longitudinal_status (longitudinal | repeated_unresolved |
cross_sectional | unknown), plus relatedness columns n_families, n_related_people, n_mother_child_families, n_twin_sets,
n_shared_settings, family_motifs (json {motif: n}), shared_setting_types (';'), relatedness_designs (';').
Tables: catalog_relatedness.parquet (one row per family or shared-setting unit with >= 2 people), catalog_relations.parquet
(explicit pairwise relations from the annotation shards, e.g. twin zygosity or FMT donor -> recipient).
"""
from __future__ import annotations

import json
import re

import numpy as np
import pandas as pd

GUT = ("primary", "unknown")
UNIT = {"h": 1 / 24, "d": 1.0, "w": 7.0, "m": 30.4375, "y": 365.25}
TP_RE = [
    (re.compile(r"^(\d+(?:\.\d+)?)\s*[-_ ]?\s*(?:h|hr|hrs|hours?)$", re.I), "h"),
    (re.compile(r"(?:^|[^a-z])(?:day|days|d)\s*[-_ ]?\s*(\d+(?:\.\d+)?)(?![\d.])", re.I), "d"),
    (re.compile(r"(\d+(?:\.\d+)?)\s*[-_ ]?\s*(?:days?|d)(?:$|[^a-z])", re.I), "d"),
    (re.compile(r"(?:^|[^a-z])(?:week|weeks|wk|wks|w)\s*[-_ ]?\s*(\d+(?:\.\d+)?)(?![\d.])", re.I), "w"),
    (re.compile(r"(\d+(?:\.\d+)?)\s*[-_ ]?\s*(?:weeks?|wks?|w)(?:$|[^a-z])", re.I), "w"),
    (re.compile(r"(?:^|[^a-z])(?:month|months|mo|m)\s*[-_ ]?\s*(\d+(?:\.\d+)?)(?![\d.])", re.I), "m"),
    (re.compile(r"(\d+(?:\.\d+)?)\s*[-_ ]?\s*(?:months?|mos?|m)(?:$|[^a-z])", re.I), "m"),
    (re.compile(r"(?:^|[^a-z])(?:year|years|yr|y)\s*[-_ ]?\s*(\d+(?:\.\d+)?)(?![\d.])", re.I), "y"),
    (re.compile(r"(\d+(?:\.\d+)?)\s*[-_ ]?\s*(?:years?|yrs?|y)(?:$|[^a-z])", re.I), "y"),
]
ZERO_RE = re.compile(r"^\s*(baseline|birth|at birth|delivery|meconium|day of birth|t0|v0|visit 0)\s*$", re.I)
# relatedness_designs vocabulary: study-design terms; shared-setting types map onto them (R2026.23)
DESIGN_ALIAS = {"household": "households", "village_or_community": "community", "ethnic_group_or_tribe": "tribe", "daycare_or_school": "daycare_school",
                "sports_team": "team", "military_unit": "military", "family": "families"}
PARENT_ROLES = {"mother", "father", "parent", "partner", "grandparent"}
CHILD_ROLES = {"child", "twin", "sibling"}


def parse_timepoint_days(label) -> float | None:
    if label is None or (isinstance(label, float) and np.isnan(label)):
        return None
    s = str(label).strip()
    if not s:
        return None
    if ZERO_RE.match(s):
        return 0.0
    hits = []
    for rx, u in TP_RE:
        m = rx.search(s)
        if m:
            hits.append(float(m.group(1)) * UNIT[u])
    return hits[0] if len(set(round(h, 3) for h in hits)) == 1 else None


def _month_date(v):
    """YYYY-MM or YYYY-MM-DD -> the first of that month (month precision; R2026.25)."""
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return None
    m = re.match(r"(\d{4})-(\d{2})(?:-\d{2})?$", str(v))
    if not m or not (1 <= int(m.group(2)) <= 12):
        return None
    return pd.Timestamp(int(m.group(1)), int(m.group(2)), 1)


def _full_date(v):
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return None
    s = str(v)
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        try:
            return pd.Timestamp(s)
        except Exception:
            return None
    return None


SCHED_UNIT = {"hour": 1 / 24, "day": 1.0, "week": 7.0, "month": 30.4375, "year": 365.25}
SCHED_REJECT = re.compile(r"not stated|not given|unknown|unclear|coded|suffix|ordinal|index|visit code|sequence number", re.I)
BARE_NUM = re.compile(r"^-?\d+(?:\.\d+)?$")


def schedule_unit(schedule, labels) -> str | None:
    """R2026.25: unit for bare-number timepoint labels (archive 'tp' = -10, 1, 3, 14 …) taken from the project's stated
    sampling schedule. Accepted only when the schedule names exactly one time unit, does not call the labels codes /
    ordinals, and at least 60 % (and >= 2) of the distinct label values appear as numbers in the schedule text."""
    if not isinstance(schedule, str) or not schedule.strip() or SCHED_REJECT.search(schedule):
        return None
    s = schedule.lower()
    units = {u for u in SCHED_UNIT if re.search(rf"\b{u}s?\b", s)}
    if len(units) != 1:
        return None
    nums = {float(n) for n in re.findall(r"-?\d+(?:\.\d+)?", s)}
    lab = {float(l) for l in labels if BARE_NUM.match(str(l).strip())}
    shared = lab & nums
    if len(lab) >= 2 and len(shared) >= 2 and len(shared) / len(lab) >= 0.6:
        return units.pop()
    return None


def _real_subject(w: pd.DataFrame, generated_keys: set) -> pd.Series:
    sub = w.subject_id.astype("string")
    return sub.where(sub.notna() & (sub != "") & ~w.sample_key.isin(generated_keys))


def sample_longitudinal(w: pd.DataFrame, generated_keys: set, schedules: dict | None = None, mapped_offsets: pd.Series | None = None) -> pd.DataFrame:
    """Adds subject_n_samples, days_since_first_sample, time_axis, person_key to the wide table (a copy).
    schedules: study_accession -> stated sampling schedule, used to give bare-number timepoint labels a unit.
    mapped_offsets: sample_key -> days from the visit-code timing pass (R2026.25; each code mapped to a stated schedule offset)."""
    w = w.copy()
    gut = w.body_site_class.isin(GUT)
    sub = _real_subject(w, generated_keys)
    w["subject_n_samples"] = pd.array([pd.NA] * len(w), dtype="Int64")
    w["days_since_first_sample"] = np.nan
    w["time_axis"] = None
    key = w.study_accession.astype(str) + "\x1f" + sub.fillna("").astype(str)
    has = gut & sub.notna()
    cnt = key[has].map(key[has].value_counts())
    w.loc[has, "subject_n_samples"] = cnt.astype("Int64")
    w.loc[gut & w.sample_key.isin(generated_keys), "subject_n_samples"] = 1
    multi = has & (w.subject_n_samples.fillna(0) >= 2)
    age = pd.to_numeric(w.get("age_at_collection_days"), errors="coerce")
    dts = w.get("collection_date").map(_full_date) if "collection_date" in w else pd.Series(None, index=w.index)
    tps = w.get("timepoint_label").map(parse_timepoint_days) if "timepoint_label" in w else pd.Series(None, index=w.index)
    # per subject (vectorised, R2026.23): first axis whose values are complete and vary within the subject — age, then full
    # collection date, then a parsed timepoint label
    km = key[multi]
    days = pd.Series(np.nan, index=km.index)
    axis = pd.Series(None, index=km.index, dtype=object)
    dd = pd.to_datetime(pd.Series(dts, index=w.index).loc[km.index], errors="coerce")
    cands = [("age", age.loc[km.index].astype(float)),
             ("date", (dd - pd.Timestamp("1900-01-01")).dt.days.astype(float)),
             ("timepoint", pd.to_numeric(pd.Series(tps, index=w.index).loc[km.index], errors="coerce").astype(float))]
    if schedules and "timepoint_label" in w:
        tl = w.timepoint_label.loc[km.index].astype("string")
        acc_k = w.study_accession.loc[km.index].astype(str)
        su = pd.Series(np.nan, index=km.index)
        for acc_, idx in acc_k.groupby(acc_k).groups.items():
            labs = tl.loc[idx].dropna().astype(str).str.strip()
            if not len(labs) or labs.map(lambda v: bool(BARE_NUM.match(v))).mean() < 0.9:
                continue
            u = schedule_unit(schedules.get(acc_), labs.unique())
            if u:
                su.loc[idx] = pd.to_numeric(labs.reindex(idx), errors="coerce") * SCHED_UNIT[u]
        cands.append(("timepoint_stated_unit", su.astype(float)))
    if mapped_offsets is not None and len(mapped_offsets):
        mo = pd.to_numeric(w.sample_key.loc[km.index].map(mapped_offsets), errors="coerce")
        cands.append(("timepoint_mapped", mo.astype(float)))
    if "collection_date" in w:
        mm = w.collection_date.loc[km.index].map(_month_date)
        cands.append(("date_month", (pd.to_datetime(mm, errors="coerce") - pd.Timestamp("1900-01-01")).dt.days.astype(float)))
    for name, v in cands:
        g = v.groupby(km)
        ok = v.notna().groupby(km).transform("all") & (g.transform("nunique") >= 2) & axis.isna()
        days[ok] = (v[ok] - g.transform("min")[ok]).round(1)
        axis[ok] = name
    w.loc[km.index, "days_since_first_sample"] = days.values
    w.loc[km.index, "time_axis"] = axis.values
    fam = w.get("family_id").astype("string") if "family_id" in w else pd.Series(pd.NA, index=w.index, dtype="string")
    role = w.get("family_role").astype("string") if "family_role" in w else pd.Series(pd.NA, index=w.index, dtype="string")
    pk = sub.copy()
    by_role = fam.notna() & role.isin(sorted(PARENT_ROLES)) & pk.isna()
    pk[by_role] = "fam:" + fam[by_role] + ":" + role[by_role]
    pk = pk.fillna("sample:" + w.sample_key.astype(str))
    w["person_key"] = pk.astype(str)
    return w


def study_longitudinal(w: pd.DataFrame, generated_keys: set, design: pd.DataFrame | None = None) -> pd.DataFrame:
    gut = w[w.body_site_class.isin(GUT)]
    sub = _real_subject(gut, generated_keys)
    g = gut.assign(_sub=sub)
    rows = []
    for acc, x in g.groupby("study_accession"):
        xs = x[x._sub.notna()]
        n_sub = int(xs._sub.nunique()) + int(x.sample_key.isin(generated_keys).sum())
        per = xs.groupby("_sub").size()
        rep = per[per >= 2]
        spans, gaps, axes = [], [], []
        for s_, y in xs[xs._sub.isin(rep.index)].groupby("_sub"):
            t = pd.to_numeric(y.days_since_first_sample, errors="coerce").dropna().sort_values()
            if len(t) >= 2:
                spans.append(float(t.max() - t.min()))
                gaps += [float(v) for v in np.diff(t.values) if v > 0]
                axes.append(y.time_axis.dropna().iloc[0] if y.time_axis.notna().any() else None)
        cov_sub = (x._sub.notna() | x.sample_key.isin(generated_keys)).mean()
        rows.append(dict(study_accession=acc, n_subjects=n_sub, n_subjects_repeated=int(len(rep)), n_samples_repeated=int(rep.sum()),
                         samples_per_subject_median=float(rep.median()) if len(rep) else None, samples_per_subject_max=int(rep.max()) if len(rep) else None,
                         followup_days_median=float(np.median(spans)) if spans else None, followup_days_max=float(max(spans)) if spans else None,
                         interval_days_median=float(np.median(gaps)) if gaps else None,
                         time_axis=(pd.Series([a for a in axes if a]).value_counts().index[0] if any(axes) else None), _cov_sub=float(cov_sub)))
    out = pd.DataFrame(rows).set_index("study_accession")
    d = design.set_index("study_accession") if design is not None and len(design) else pd.DataFrame()
    def status(r):
        des = str(d.longitudinal_design.get(r.name, "")) if "longitudinal_design" in d.columns else ""
        if r.n_subjects_repeated >= 2 or (r.n_subjects_repeated >= 1 and des == "yes"):
            return "longitudinal"
        if des == "yes":
            return "repeated_unresolved"
        if r._cov_sub >= 0.9 and r.n_subjects_repeated == 0:
            return "cross_sectional"
        if des == "no":
            return "cross_sectional"
        return "unknown"
    out["longitudinal_status"] = out.apply(status, axis=1)
    return out.drop(columns=["_cov_sub"])


def _motif(roles: list) -> str:
    c = pd.Series(roles).value_counts()
    order = ["mother", "father", "parent", "partner", "grandparent", "child", "twin", "sibling", "other_relative", "household_member", "unknown"]
    parts = []
    for r in order + sorted(set(c.index) - set(order)):
        if r in c.index:
            parts.append(r if c[r] == 1 else f"{r}×{int(c[r])}")
    return " + ".join(parts)


def relatedness(w: pd.DataFrame, design: pd.DataFrame | None = None, relations: pd.DataFrame | None = None):
    """Returns (groups table, per-study summary)."""
    have_f = "family_id" in w.columns
    have_s = "shared_setting_id" in w.columns
    groups = []
    if have_f:
        f = w[w.family_id.notna() & (w.family_id.astype(str) != "")]
        for (acc, fid), x in f.groupby(["study_accession", "family_id"]):
            persons = x.drop_duplicates("person_key")
            if len(persons) < 2:
                continue
            roles = persons.family_role.fillna("unknown").astype(str).tolist() if "family_role" in x else ["unknown"] * len(persons)
            groups.append(dict(study_accession=acc, group_kind="family", group_type="family", group_id=str(fid), n_people=int(len(persons)), n_samples=int(len(x)),
                               roles=json.dumps(pd.Series(roles).value_counts().to_dict()), motif=_motif(roles),
                               people=";".join(sorted(persons.person_key.astype(str)))[:2000], sample_keys=";".join(sorted(x.sample_key.astype(str)))[:4000]))
    if have_s:
        s_ = w[w.shared_setting_id.notna() & (w.shared_setting_id.astype(str) != "")]
        for (acc, typ, sid), x in s_.groupby(["study_accession", "shared_setting_type", "shared_setting_id"]):
            persons = x.drop_duplicates("person_key")
            if len(persons) < 2:
                continue
            groups.append(dict(study_accession=acc, group_kind="shared_setting", group_type=str(typ), group_id=str(sid), n_people=int(len(persons)), n_samples=int(len(x)),
                               roles=None, motif=None, people=";".join(sorted(persons.person_key.astype(str)))[:2000], sample_keys=";".join(sorted(x.sample_key.astype(str)))[:4000]))
    G = pd.DataFrame(groups, columns=["study_accession", "group_kind", "group_type", "group_id", "n_people", "n_samples", "roles", "motif", "people", "sample_keys"])
    rows = []
    studies = set(G.study_accession)
    d = design.set_index("study_accession") if design is not None and len(design) else pd.DataFrame()
    if len(d):
        studies |= set(d.index[d.get("relatedness_design", pd.Series("", index=d.index)).fillna("none").astype(str).str.strip().ne("none") & d.get("relatedness_design", pd.Series("", index=d.index)).notna()])
    R = relations if relations is not None else pd.DataFrame(columns=["study_accession", "relation"])
    studies |= set(R.study_accession) if len(R) else set()
    for acc in sorted(studies):
        g = G[G.study_accession == acc]
        fam = g[g.group_kind == "family"]
        sett = g[g.group_kind == "shared_setting"]
        mc = int(fam.motif.fillna("").str.contains(r"(?:mother|father|parent)").__and__(fam.motif.fillna("").str.contains(r"(?:child|twin)")).sum()) if len(fam) else 0
        tw = int(fam.motif.fillna("").str.contains("twin").sum()) if len(fam) else 0
        rr = R[R.study_accession == acc] if len(R) else R
        tw += int(rr.relation.astype(str).str.startswith("twin").sum()) if len(rr) else 0
        des = str(d.relatedness_design.get(acc, "") or "") if "relatedness_design" in d.columns else ""
        kinds = set(x for x in des.split(";") if x and x != "none")
        if mc:
            kinds.add("mother_infant" if fam.motif.str.contains("mother").any() else "parent_child")
        if tw:
            kinds.add("twins")
        kinds |= set(sett.group_type)
        if len(rr):
            kinds |= {"fmt_donor_recipient"} if rr.relation.eq("donor_of").any() else set()
        kinds = {DESIGN_ALIAS.get(k.strip(), k.strip()) for k in kinds if k.strip()}
        rows.append(dict(study_accession=acc, n_families=int(len(fam)), n_related_people=int(fam.n_people.sum()) if len(fam) else 0, n_mother_child_families=mc, n_twin_sets=tw,
                         n_shared_settings=int(len(sett)), family_motifs=json.dumps(fam.motif.value_counts().to_dict()) if len(fam) else None,
                         shared_setting_types=";".join(sorted(set(sett.group_type))) or None, relatedness_designs=";".join(sorted(kinds)) or None,
                         relatedness_summary=(str(d.relatedness_summary.get(acc)) if "relatedness_summary" in d.columns and pd.notna(d.relatedness_summary.get(acc)) else None)))
    S = pd.DataFrame(rows, columns=["study_accession", "n_families", "n_related_people", "n_mother_child_families", "n_twin_sets", "n_shared_settings", "family_motifs",
                                    "shared_setting_types", "relatedness_designs", "relatedness_summary"]).set_index("study_accession")
    return G, S
