#!/usr/bin/env python
"""build_ext_delta_r38.py — add the values of the November 2026 re-match of prior curated resources (R2026.38).

The external resources were ingested on 2026-09-28 (R2026.11) by joining them to the catalog of that day, which held gut samples
only. On 2026-11-11 each resource was fetched again at the same version and joined to every current catalog sample (all body
sites); the joined values are in <rematch>/<id>/<id>_values.parquet (sample_key, study_accession, field_name, field_value,
value_normalized, resource_id, source_locator, mapped) with a report next to them. For the samples and fields both joins have, the
values are identical, so the September shards stay as they are and this script writes only what is new:

    data/inputs/gut/r2/gut_r2_determinations_shard_ext_<id>_r38.parquet

one row per sample x field that the re-match has, the September shard of the resource does not, and whose value has a code in the
catalog's vocabulary (`mapped`). Rows keep the conventions of the September ingest: route R2, evidence_source external.<id>,
evidence_locator = link to the source record, determined_by and confidence of the resource's September shard. A raw condition
string (health_condition_detail) is written only next to a coded health_condition of the same resource and sample, and a field the
September ingest did not take from a resource is not taken now.

usage: python scripts/build_ext_delta_r38.py [--rematch ~/catalog/cache/ext_rematch_r37] [--package build/package]
"""
import argparse
import glob
import json
import os

import pandas as pd

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
R2 = os.path.join(ROOT, "data", "inputs", "gut", "r2")
RESOURCES = ["cmd", "gmrepo", "meta2db", "hmgdb", "microbiomedb", "hmp_ihmp", "ahmp", "mbodymap", "microbeatlas"]
FIELDS = ["age_at_collection_days", "sex", "bmi", "country", "health_condition", "health_condition_detail", "antibiotic_exposure"]
BAD = {"", "unknown", "nan", "None", "none"}


class Gate:
    """Which re-matched values may be ingested, judged against the previous package and the R2026.38 corrections."""

    def __init__(self, package: str, corrections: str):
        d = pd.read_parquet(os.path.join(package, "catalog_sample_determinations.parquet"),
                            columns=["sample_key", "field_name", "study_accession", "value_normalized", "route", "determined_by", "parse_note", "release_retired"])
        d = d[d.release_retired.isna() & d.field_name.isin(FIELDS)]
        stmt = d.parse_note.astype(str).str.contains("expanded from study_all")
        self.prev = {(s, f): (v, st, ro, by, acc) for s, f, v, st, ro, by, acc in
                     zip(d.sample_key, d.field_name, d.value_normalized.astype(str), stmt, d.route, d.determined_by.astype(str), d.study_accession)}
        c = pd.read_parquet(corrections).fillna("")
        per = c[c.sample_key != ""]
        self.recoded = {(s, f): v for s, f, v in zip(per.sample_key, per.field_name, per.new_value) if v}
        self.retired_cells = set(zip(per[per.action == "retire"].sample_key, per[per.action == "retire"].field_name))
        self.project_rules = {}
        for r in c[c.sample_key == ""].itertuples(index=False):
            self.project_rules.setdefault((r.study_accession, r.field_name), []).append((r.old_value, r.old_route, r.old_determined_by))

    @staticmethod
    def agree(field: str, a, b) -> bool:
        a, b = str(a).strip().lower(), str(b).strip().lower()
        if a == b:
            return True
        if field in ("age_at_collection_days", "bmi"):
            try:
                return abs(float(a) - float(b)) <= (1.0 if field == "age_at_collection_days" else 0.1)
            except ValueError:
                return False
        return False

    def reasons(self, new: pd.DataFrame) -> pd.Series:
        out = []
        for s, f, v in zip(new.sample_key, new.field_name, new.value_normalized.astype(str)):
            if f == "health_condition_detail":
                out.append("")
                continue
            if (s, f) in self.recoded:
                out.append("" if self.agree(f, self.recoded[(s, f)], v) else "differs from a value checked for R2026.38")
                continue
            p = self.prev.get((s, f))
            if p is None or p[1] or (s, f) in self.retired_cells:
                out.append("")
                continue
            if "plausibility_correction" in p[3]:
                # the cell was set by a checked correction that replaced the archive value after source resolution; a resource row
                # here would be compared with the replaced archive value and withhold the cell (PRJEB37731, 20 samples)
                out.append("the cell was set by a checked correction")
                continue
            withdrawn = any((ov in ("", p[0])) and (ro in ("", p[2])) and (by == "" or p[3].startswith(by)) for ov, ro, by in self.project_rules.get((p[4], f), []))
            out.append("" if withdrawn or self.agree(f, p[0], v) else "differs from the current per-sample value")
        return pd.Series(out, index=new.index)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rematch", default=os.path.expanduser("~/catalog/cache/ext_rematch_r37"))
    ap.add_argument("--package", default=os.path.join(ROOT, "data", "inputs", "data_package"), help="the previous (R2026.37) package, unpacked")
    ap.add_argument("--corrections", default=os.path.join(ROOT, "data", "inputs", "gut", "dq_corrections_r38.parquet"))
    ap.add_argument("--release-id", default="R2026.38")
    ap.add_argument("--package-version", default="1.31.0")
    a = ap.parse_args()
    w = pd.read_parquet(os.path.join(a.package, "catalog_sample_metadata_wide.parquet"), columns=["sample_key", "study_accession", "release_retired"])
    cur = dict(zip(w.loc[w.release_retired.isna(), "sample_key"], w.loc[w.release_retired.isna(), "study_accession"]))
    gate = Gate(a.package, a.corrections)
    stats, dropped = {}, []
    for r in RESOURCES:
        old = pd.read_parquet(os.path.join(R2, f"gut_r2_determinations_shard_ext_{r}.parquet"))
        v = pd.read_parquet(os.path.join(a.rematch, r, f"{r}_values.parquet"))
        v = v[v.sample_key.isin(cur) & v.field_name.isin(FIELDS)].copy()
        v["study_accession"] = v.sample_key.map(cur)                                   # the catalog's project for the sample
        # a resource can hold several records for one sample (MicrobiomeDB): keep the value when the records agree, drop it when not
        v = v.sort_values("mapped", ascending=False)
        nuniq = v[v.mapped.astype(bool)].groupby(["sample_key", "field_name"]).value_normalized.nunique()
        amb = set(nuniq[nuniq > 1].index)
        n_dup, n_amb = int(v.duplicated(["sample_key", "field_name"]).sum()), len(amb)
        v = v[[k not in amb for k in zip(v.sample_key, v.field_name)]].drop_duplicates(["sample_key", "field_name"])
        detail = v.field_name.eq("health_condition_detail")
        coded = v[v.mapped.astype(bool) & ~detail & v.value_normalized.notna() & ~v.value_normalized.astype(str).isin(BAD)]
        has_code = set(coded.loc[coded.field_name.eq("health_condition"), "sample_key"])
        det = v[detail & v.sample_key.isin(has_code) & v.field_value.notna()].assign(value_normalized=lambda x: x.field_value.astype(str))
        new = pd.concat([coded, det], ignore_index=True)
        old_keys = set(zip(old.sample_key, old.field_name))
        new = new[[k not in old_keys for k in zip(new.sample_key, new.field_name)]]
        # the September ingest decided, per resource, which fields to take; fields it did not take are not added now
        skipped = new[~new.field_name.isin(set(old.field_name))].field_name.value_counts().to_dict()
        new = new[new.field_name.isin(set(old.field_name))]
        # R2026.38 gate: a re-matched value fills an empty cell, supports an equal value, or replaces a cohort-wide statement
        # (per-sample evidence outranks it, as in September). It is NOT ingested where it differs from a current per-sample value:
        # that would either replace a value nobody has checked against the resource, or withhold both (first build: Hong Kong
        # samples lost their country because two resources code Hong Kong as China).
        reason = gate.reasons(new)
        bad_hc = set(new.loc[reason.ne("").values & new.field_name.eq("health_condition").values, "sample_key"])
        reason[(new.field_name.eq("health_condition_detail") & new.sample_key.isin(bad_hc)).values] = "its health_condition value was not ingested"
        d = new[reason.ne("").values].assign(resource_id=r, reason=reason[reason.ne("")].values)
        dropped.append(d[["resource_id", "sample_key", "study_accession", "field_name", "field_value", "value_normalized", "reason"]])
        new = new[reason.eq("").values]
        conf = old.groupby("field_name").confidence.agg(lambda s: float(s.mode().iloc[0])).to_dict()
        base_conf = float(old.confidence.mode().iloc[0])
        by = str(old.determined_by.mode().iloc[0])
        out = pd.DataFrame({
            "sample_key": new.sample_key.values, "field_name": new.field_name.values, "study_accession": new.study_accession.values,
            "field_value": new.field_value.astype(str).values, "value_normalized": new.value_normalized.astype(str).values,
            "confidence": [conf.get(f, base_conf) for f in new.field_name], "evidence_source": f"external.{r}",
            "evidence_locator": new.source_locator.astype(str).values, "evidence_quote": new.field_value.astype(str).str.slice(0, 200).values,
            "evidence_limited_to_abstract": 0, "determined_by": by, "route": "R2", "scope": "sample",
            "parse_note": "re-match of the resource to every current catalog sample, 2026-11-11 (same resource version as the 2026-09-28 ingest)",
            "group_audit": None, "src_track": str(old.src_track.mode().iloc[0]), "release_added": a.release_id, "release_retired": None,
            "package_added": a.package_version})
        out = out.reindex(columns=list(old.columns))
        for c in old.columns:                                                          # same dtypes as the September shard
            try:
                out[c] = out[c].astype(old[c].dtype)
            except (TypeError, ValueError):
                pass
        p = os.path.join(R2, f"gut_r2_determinations_shard_ext_{r}_r38.parquet")
        out.to_parquet(p, index=False)
        stats[r] = dict(rows=int(len(out)), samples=int(out.sample_key.nunique()), projects=int(out.study_accession.nunique()),
                        by_field=out.field_name.value_counts().to_dict(), fields_not_taken_in_september=skipped, duplicate_records_collapsed=n_dup,
                        sample_fields_dropped_records_disagree=n_amb, determined_by=by,
                        not_ingested=d.reason.value_counts().to_dict(), not_ingested_by_field=d.field_name.value_counts().to_dict())
    nd = pd.concat(dropped, ignore_index=True)
    nd["current_value"] = [gate.prev.get((s_, f_), (None,))[0] for s_, f_ in zip(nd.sample_key, nd.field_name)]
    nd.to_parquet(os.path.join(os.path.dirname(R2), "ext_delta_r38_not_ingested.parquet"), index=False)   # outside r2/: the build globs r2/*.parquet
    json.dump(stats, open(os.path.join(R2, "ext_delta_r38_summary.json"), "w"), indent=1)
    print(json.dumps(stats, indent=1))


if __name__ == "__main__":
    main()
