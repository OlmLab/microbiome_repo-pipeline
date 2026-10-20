#!/usr/bin/env python
"""Projects whose metadata sit behind an access application (R2026.26, owner request 2026-10-20).

Reads the NCBI BioProject record of every PRJNA catalog project (E-utilities efetch, 100 per request) and records the dbGaP
study accessions (phs######) it names. Output: config/metadata_access.csv (study_accession, access_system, access_study,
apply_url, evidence). build_gut_scope turns this into gut_studies.metadata_access (controlled | partly_controlled) using the
project's public core-field coverage.
usage: python scripts/harvest_metadata_access.py --studies build/package/gut_studies.parquet --out config/metadata_access.csv
"""
import argparse, re, time
import pandas as pd, requests

ap = argparse.ArgumentParser(); ap.add_argument("--studies", required=True); ap.add_argument("--out", required=True)
a = ap.parse_args()
ids = [s[5:] for s in pd.read_parquet(a.studies, columns=["study_accession"]).study_accession if s.startswith("PRJNA")]
rows = []
for i in range(0, len(ids), 100):
    for _ in range(3):
        try:
            x = requests.post("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi", data=dict(db="bioproject", id=",".join(ids[i:i + 100])), timeout=120).text
            break
        except Exception:
            time.sleep(3)
    for doc in re.split(r"<DocumentSummary[ >]", x)[1:]:
        acc = re.search(r'accession="(PRJNA\d+)"', doc)
        ph = sorted(set(re.findall(r"phs\d{6}", doc)))
        if acc and ph:
            rows.append(dict(study_accession=acc.group(1), access_system="dbGaP", access_study=";".join(ph),
                             apply_url=f"https://www.ncbi.nlm.nih.gov/projects/gap/cgi-bin/study.cgi?study_id={ph[0]}",
                             evidence="NCBI BioProject record names dbGaP study " + ", ".join(ph)))
    time.sleep(0.35)
pd.DataFrame(rows, columns=["study_accession", "access_system", "access_study", "apply_url", "evidence"]).to_csv(a.out, index=False)
print(len(rows), "projects")
