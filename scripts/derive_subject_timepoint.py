"""Per-sample subject_id and timepoint_label from archive attributes (route R1), for the R2026.30 scope.

Deterministic; no model calls. Follows the SUBJECT RULE of docs/DEEP_ANNOTATION_BRIEF.md: a subject id READ from a
source is kept as it stands; no subject id is generated here. Only an allow-listed attribute key counts, so a value
like `patient_died` or `donor_storage_hours` can never become a subject id. Values that are placeholders, or that are
identical for every sample of a project (one shared label is not an identifier), are dropped.

usage: python scripts/derive_subject_timepoint.py ATTRS.parquet OUT.parquet
"""
import json, re, sys

import pandas as pd

SUBJECT_KEYS = ["host_subject_id", "subject_id", "submitted_subject_id", "gap_subject_id", "subject",
                "participant_id", "subjectid", "individual_humanid", "individual_human_id", "patient_id",
                "patientnumber", "patient", "patient_number", "donor_id", "donor", "individual", "individual_id",
                "volunteer_id", "host_individual", "host_id", "person_id", "anonymized_name", "study_subject_id",
                "subject_code", "participant", "patient_code", "host_subject", "sample_subject_id"]
TIMEPOINT_KEYS = ["timepoint", "time_point", "visit", "visit_number", "study_day", "collection_day", "day",
                  "week", "time", "sampling_timepoint", "time_point_days", "visit_name", "sampling_time",
                  "timepoint_label", "study_week", "month", "sampling_visit", "period", "phase"]
PLACEHOLDER = re.compile(r"^(na|n/?a|none|null|not applicable|not provided|not collected|missing|unknown|unspecified"
                         r"|not available|nan|-|\.)$", re.I)


def pick(df, keys, label):
    """One value per sample: the first allow-listed key that has a usable value, in list order."""
    rank = {k: i for i, k in enumerate(keys)}
    d = df[df["attr_key_norm"].isin(rank)].copy()
    d["v"] = d["attr_value"].astype(str).str.strip()
    d = d[(d["v"] != "") & ~d["v"].str.match(PLACEHOLDER) & (d["v"].str.len() <= 64)]
    d["r"] = d["attr_key_norm"].map(rank)
    d = d.sort_values(["sample_key", "r"]).drop_duplicates("sample_key")
    # a label shared by every sample of a project is not an identifier
    if label == "subject_id":
        n = d.groupby("study_accession")["v"].agg(["nunique", "size"])
        bad = set(n[(n["nunique"] == 1) & (n["size"] > 1)].index)
        d = d[~d["study_accession"].isin(bad)]
    return d.rename(columns={"v": label, "attr_key_norm": f"{label}_key"})[
        ["sample_key", "study_accession", label, f"{label}_key"]]


def main(attrs, out):
    at = pd.read_parquet(attrs, columns=["sample_key", "study_accession", "attr_key_norm", "attr_value"])
    sub = pick(at, SUBJECT_KEYS, "subject_id")
    tp = pick(at, TIMEPOINT_KEYS, "timepoint_label")
    m = sub.merge(tp.drop(columns=["study_accession"]), on="sample_key", how="outer")
    m["study_accession"] = m["study_accession"].fillna(
        at.drop_duplicates("sample_key").set_index("sample_key")["study_accession"])
    m.to_parquet(out, index=False)
    per = sub.groupby("study_accession")["subject_id"].agg(n_subjects="nunique", n_samples="size")
    print(json.dumps({
        "samples_with_subject_id": int(sub["sample_key"].nunique()),
        "projects_with_subject_id": int(sub["study_accession"].nunique()),
        "samples_with_timepoint": int(tp["sample_key"].nunique()),
        "projects_with_timepoint": int(tp["study_accession"].nunique()),
        "projects_repeated_sampling": int((per["n_samples"] > per["n_subjects"]).sum()),
        "subject_keys_used": sub["subject_id_key"].value_counts().head(10).to_dict(),
    }))


if __name__ == "__main__":
    main(*sys.argv[1:3])
