"""harvest_biosamples.py — sample attributes for a list of samples (free, network, resumable).

ENA browser XML first (fast, batched); samples ENA does not return are fetched from NCBI BioSample (esearch+efetch by
accession). Writes one parquet shard per chunk to OUT_DIR so a stopped run resumes where it left off; combine with
pandas.read_parquet(OUT_DIR). Columns match new_biosample_attributes.parquet:
sample_key, attr_key, attr_key_norm, attr_value, attr_units, source, study_accession.

usage: python scripts/harvest_biosamples.py --samples samples.csv --out DIR [--chunk 200] [--workers 4]
       samples.csv: columns sample_accession,study_accession
"""
import argparse, glob, os, re, time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
import pandas as pd
import requests

ENA = "https://www.ebi.ac.uk/ena/browser/api/xml/"
EUT = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"
norm = lambda k: re.sub(r"[^a-z0-9]+", "_", str(k).lower()).strip("_")
S = requests.Session()


def _rows(sample, pairs, source, study):
    return [{"sample_key": sample, "attr_key": k, "attr_key_norm": norm(k), "attr_value": v, "attr_units": u,
             "source": source, "study_accession": study} for k, v, u in pairs if v not in (None, "")]


def ena_batch(accs, study_of):
    out, got = [], set()
    try:
        r = S.get(ENA + ",".join(accs), timeout=120)
        if r.status_code != 200 or not r.text.lstrip().startswith("<"):
            return out, got
        root = ET.fromstring(r.text)
    except Exception:
        return out, got
    for s in root.iter("SAMPLE"):
        acc = s.get("accession") or ""
        ext = {x.get("namespace"): x.text for x in s.iter("EXTERNAL_ID")}
        key = next((v for k, v in ext.items() if v and v.startswith(("SAMN", "SAMD", "SAME"))), acc)
        pairs = []
        for a in s.iter("SAMPLE_ATTRIBUTE"):
            pairs.append((a.findtext("TAG"), a.findtext("VALUE"), a.findtext("UNITS")))
        nm = s.find("SAMPLE_NAME")
        if nm is not None:
            for t in ("TAXON_ID", "SCIENTIFIC_NAME", "COMMON_NAME"):
                pairs.append((f"SAMPLE_NAME.{t}", nm.findtext(t), None))
        for t in ("TITLE",):
            pairs.append((t, s.findtext(t), None))
        st = study_of.get(key) or study_of.get(acc)
        out += _rows(key, pairs, "ena_xml", st)
        got.add(key); got.add(acc)
    return out, got


def ncbi_batch(accs, study_of):
    out = []
    try:
        ids = S.get(EUT + "esearch.fcgi", params={"db": "biosample", "term": " OR ".join(f"{a}[accn]" for a in accs),
                                                  "retmax": len(accs) * 2, "retmode": "json"}, timeout=60).json()["esearchresult"]["idlist"]
        if not ids:
            return out
        time.sleep(0.4)
        x = S.get(EUT + "efetch.fcgi", params={"db": "biosample", "id": ",".join(ids), "retmode": "xml"}, timeout=120).text
        if not x.lstrip().startswith("<"):
            return out
        for b in ET.fromstring(x).findall("BioSample"):
            acc = b.get("accession")
            pairs = [(a.get("attribute_name") or a.get("harmonized_name"), a.text, None) for a in b.iter("Attribute")]
            t = b.find(".//Title")
            if t is not None:
                pairs.append(("TITLE", t.text, None))
            out += _rows(acc, pairs, "ncbi_biosample", study_of.get(acc))
    except Exception:
        return out
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--samples", required=True); ap.add_argument("--out", required=True)
    ap.add_argument("--chunk", type=int, default=200); ap.add_argument("--workers", type=int, default=4)
    a = ap.parse_args(argv)
    os.makedirs(a.out, exist_ok=True)
    df = pd.read_csv(a.samples)
    study_of = dict(zip(df.sample_accession, df.study_accession))
    todo = sorted(set(df.sample_accession))
    done = set()
    for f in glob.glob(f"{a.out}/*.parquet"):
        try:
            done |= set(pd.read_parquet(f, columns=["sample_key"]).sample_key)
        except Exception:
            pass
    todo = [s for s in todo if s not in done]
    print(f"{len(df.sample_accession.unique())} samples, {len(done)} already harvested, {len(todo)} to do", flush=True)
    chunks = [todo[i:i + a.chunk] for i in range(0, len(todo), a.chunk)]

    def one(i_c):
        i, c = i_c
        rows, got = ena_batch(c, study_of)
        missing = [s for s in c if s not in got]
        if missing:
            for j in range(0, len(missing), 100):
                rows += ncbi_batch(missing[j:j + 100], study_of)
                time.sleep(0.4)
        if rows:
            pd.DataFrame(rows).to_parquet(f"{a.out}/shard_{i:05d}.parquet", index=False)
        return len(rows), len({r['sample_key'] for r in rows})

    n_rows = n_s = 0
    with ThreadPoolExecutor(a.workers) as ex:
        for k, (nr, ns) in enumerate(ex.map(one, list(enumerate(chunks)))):
            n_rows += nr; n_s += ns
            if k % 50 == 0:
                print(f"chunk {k}/{len(chunks)} rows={n_rows} samples={n_s}", flush=True)
    print(f"done: {n_rows} rows for {n_s} samples", flush=True)


if __name__ == "__main__":
    main()
