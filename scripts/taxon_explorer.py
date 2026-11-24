#!/usr/bin/env python
"""taxon_explorer.py — payload for Atlas > Taxon explorer (ONE interactive page for every taxon).

Covers every genus and every species that occurs in the Sandpiper profiles of the frozen package (package 1.31.0:
8,391 genera and 24,322 species; the rows unassigned_at_genus / unassigned_at_species are not taxa). The page loads
these files and draws everything in the browser:

  tree.bin    the GTDB R232 bacterial and archaeal reference trees pruned to the catalog's taxa, branch lengths
              kept, the two domains joined under one root (join edges of length 0 — see FORMAT.md). Carries per node
              what the page needs to draw, label, collapse and search WITHOUT the statistics file.
  stats.bin   for every taxon, per group of the four dimensions (body site, age group, country, lifestyle): the
              number of samples with relabund > 0, the mean relative abundance over all samples of the group, and
              the number of projects with a detection. Group denominators, labels, order, ISO codes, map ids and the
              display thresholds are stored once in the header, not per taxon.
  projects.bin  OPTIONAL (--skip-projects leaves it out): per taxon the up to 5 projects with the highest mean
              relative abundance, as indices into one shared project table.
  lit/<slug>.json  the literature summaries of --literature, copied unchanged and fetched on demand.

Population and group definitions are the ones of for_editor/taxon_pages/taxon_pages.py (round 2): QC-clean specimen
samples with a Sandpiper profile (380,430 samples in 2,902 projects in package 1.31.0), the same body-site / age /
country / lifestyle keys, labels, order and display thresholds MIN_CELL_SAMPLES / MIN_CELL_PROJECTS.

SCOPE (round 3). The body-site dimension is over every sample. Age, country and lifestyle are computed for each
taxon WITHIN THE SAMPLES OF ITS LEADING BODY SITE, so a vaginal taxon's age chart is about vaginal samples and a
gut taxon's map is not diluted by countries that contributed oral or skin samples. The leading site is the
anatomical body-site group (main = true) that passes the display thresholds and has the highest share of its
samples carrying the taxon; ties go to the group with more samples carrying it, then to body-site order. A taxon
with no such group has no leading site, no age/country/lifestyle cells, and carries flag 8. Denominators for the
three scoped dimensions are therefore a (body site x group) table, stored once in the stats header. The optional
most-abundant-project list is scoped the same way, which is what round 2 did for its taxa. Nothing is computed or
written by health condition.

usage: python taxon_explorer.py --package build/package --sandpiper-dir .../sandpiper_profiles \
         --countries data/inputs/gut/atlas/countries.json --basemap .../countries-110m.json \
         --gtdb-dir work/gtdb --literature .../taxon_pages/literature \
         --vanish-results .../track_a/v2/out/results.csv --out out/explorer [--memory-limit 2GB] [--parquet]
Out of core: DuckDB with --memory-limit and --threads spilling to <out>/_work; no profile table is ever read into
pandas. Deterministic: same inputs in, same bytes out (no wall clock in any file; every sort has a tie-break).
"""
from __future__ import annotations

import argparse
import gzip
import json
import math
import os
import re
import resource
import shutil
import struct
import time

import duckdb
import pandas as pd

# ---------------------------------------------------------------- group definitions (identical to taxon_pages.py)
SITE_LABEL = {"gut_stool": "Gut (stool)", "oral": "Oral", "skin": "Skin", "vaginal_urogenital": "Vaginal / urogenital",
              "nasal_nasopharyngeal": "Nasal / nasopharyngeal", "respiratory_lower": "Lower respiratory",
              "blood_tissue": "Blood / tissue", "eye_ear": "Eye / ear", "milk": "Milk", "other_site": "Other site",
              "multi_site": "Several sites", "unknown_site": "Site not recorded",
              "no_site_attribute": "No body-site attribute"}
SITE_ORDER = list(SITE_LABEL)
ANATOMICAL = ["gut_stool", "oral", "skin", "vaginal_urogenital", "nasal_nasopharyngeal", "respiratory_lower",
              "blood_tissue", "eye_ear", "milk"]
AGE_LABEL = {"neonate": "Neonate (under 1 month)", "infant": "Infant (1 month to 3 years)",
             "child": "Child (3 to 12 years)", "adolescent": "Adolescent (12 to 18 years)",
             "adult": "Adult (18 to 65 years)", "elderly": "Older adult (65 years and over)",
             "unknown": "Age not recorded"}
AGE_ORDER = list(AGE_LABEL)
LIFE_LABEL = {"hunter_gatherer": "Hunter-gatherer", "pastoralist": "Pastoralist",
              "traditional_agriculturalist": "Traditional agriculturalist",
              "indigenous_community": "Indigenous community", "rural_non_industrialized": "Rural, non-industrialized",
              "urban_industrialized": "Urban, industrialized", "institutionalized": "Institutionalized",
              "military": "Military", "athlete": "Athlete", "vegetarian_or_vegan": "Vegetarian or vegan",
              "extreme_environment": "Extreme environment", "spaceflight_or_analog": "Spaceflight or analogue",
              "other_lifestyle": "Other lifestyle", "not_recorded": "Lifestyle not recorded"}
LIFE_ORDER = list(LIFE_LABEL)
QC_EXCLUDE = ["qc_low_depth", "qc_non_metagenome", "qc_synthetic", "qc_rna", "qc_predicted_ecological",
              "qc_no_genus_assigned", "qc_low_complexity"]
UNASSIGNED = ("unassigned_at_genus", "unassigned_at_species")
MIN_CELL_SAMPLES = 30      # a group is shown only with at least this many samples ...
MIN_CELL_PROJECTS = 2      # ... and at least this many projects
MIN_PROJ_SAMPLES = 10      # a project counts towards the most-abundant list with at least this many samples
N_TOP_PROJECTS = 5
EXTRA_COUNTRIES = {"PG": ("598", "Papua New Guinea")}

# ------------------------------------------------------------------------------------------------- payload coding
FORMAT_VERSION = 1
RANKS = ["root", "domain", "phylum", "class", "order", "family", "genus", "species", "clade", "unplaced"]
RANK_OF_PREFIX = {"d": 1, "p": 2, "c": 3, "o": 4, "f": 5, "g": 6, "s": 7}
LEN_SCALE = 10 ** 7        # branch lengths are stored as round(length * LEN_SCALE) in a uint32
RA_SCALE = 5000            # mean relative abundance -> code = round(-log10(mean) * RA_SCALE), uint16
DIMS = ["site", "age", "country", "life"]
CLASSES = ["", "VANISH", "BloSSUM", "StIL"]   # StIL = "StIL candidate" (METHOD_v2 section D, round 3)


def slugify(taxon: str) -> str:
    """GTDB name -> file-safe slug, exactly as in taxon_pages.py (the literature files use it)."""
    bare = taxon.split("__", 1)[1] if "__" in taxon else taxon
    return re.sub(r"[^A-Za-z0-9_]", "_", bare.replace(" ", "_"))


def uvarint(v: int) -> bytes:
    """LEB128 unsigned varint."""
    out = bytearray()
    while True:
        b = v & 0x7F
        v >>= 7
        out.append(b | (0x80 if v else 0))
        if not v:
            return bytes(out)


def ra_code(mean: float) -> int:
    """Mean relative abundance (a fraction of 1) -> uint16 code. Reconstruction: 10 ** (-code / RA_SCALE)."""
    if mean <= 0:
        return 65535
    c = int(round(-math.log10(mean) * RA_SCALE))
    return 0 if c < 0 else (65535 if c > 65535 else c)


def container(header: dict, sections: list) -> bytes:
    """magic (4 bytes) | format version uint16 | header length uint32 | header JSON | sections, in order.

    Sections are padded to a multiple of 8 bytes and the first one starts at a multiple of 8, so a reader can take
    typed-array views straight onto the buffer. header['sections'] maps name -> [offset, length], the offset being
    relative to the first section.
    """
    off, index, blobs = 0, {}, []
    for name, blob in sections:
        index[name] = [off, len(blob)]
        pad = (-len(blob)) % 8
        blobs.append(blob + b"\0" * pad)
        off += len(blob) + pad
    header = dict(header)
    header["sections"] = index
    hj = json.dumps(header, separators=(",", ":"), sort_keys=True).encode()
    hj += b" " * ((-(10 + len(hj))) % 8)
    out = bytearray(header["magic"].encode() + struct.pack("<HI", FORMAT_VERSION, len(hj)) + hj)
    for blob in blobs:
        out += blob
    return bytes(out)


def blob_of(strings) -> tuple:
    """(uint32 offsets of length n+1, utf-8 blob) for a list of strings."""
    offs, buf, o = [], bytearray(), 0
    for s in strings:
        offs.append(o)
        b = s.encode()
        buf += b
        o += len(b)
    offs.append(o)
    return struct.pack("<%dI" % len(offs), *offs), bytes(buf)


# ---------------------------------------------------------------------------------------------------- newick
def parse_newick(path: str):
    """Minimal Newick reader for the GTDB reference trees.

    Returns (parent, length, label) as lists in creation order, which for Newick is preorder, so parent[i] < i.
    A GTDB internal token looks like '100.0:g__Bacteroides:0.0123' — support value, optional ';'-joined rank names,
    then the branch length; a tip token is 'RS_GCF_000000005.1:0.0123'.
    """
    s = open(path).read().strip()
    parent, length, label = [], [], []
    stack, i, n = [], 0, len(s)

    def new(p):
        parent.append(p)
        length.append(0.0)
        label.append("")
        return len(parent) - 1

    def read_token(i):
        """A token is an optional quoted label followed by anything up to the next ( ) , or ;.

        GTDB quotes internal labels and puts several rank names in one of them:
        )'100:f__Aestuariirhabdaceae; g__Aestuariirhabdus':0.03128
        """
        j = i
        if j < n and s[j] == "'":
            j += 1
            while j < n:
                if s[j] == "'":
                    if j + 1 < n and s[j + 1] == "'":
                        j += 2
                        continue
                    j += 1
                    break
                j += 1
        while j < n and s[j] not in "(),;":
            j += 1
        return s[i:j].strip(), j

    def set_token(k, tok):
        lab, ln = tok, 0.0
        if ":" in tok:
            head, tail = tok.rsplit(":", 1)
            try:
                ln = float(tail)
                lab = head
            except ValueError:
                pass
        lab = lab.strip()
        if len(lab) > 1 and lab[0] == "'" and lab[-1] == "'":
            lab = lab[1:-1].replace("''", "'")
        label[k], length[k] = lab, ln

    while i < n:
        ch = s[i]
        if ch == "(":
            k = new(stack[-1] if stack else -1)
            stack.append(k)
            i += 1
        elif ch == ",":
            i += 1
        elif ch == ")":
            k = stack.pop()
            tok, i = read_token(i + 1)
            set_token(k, tok)
        elif ch == ";":
            break
        else:
            tok, i = read_token(i)
            k = new(stack[-1] if stack else -1)
            set_token(k, tok)
    return parent, length, label


def rank_names(lab: str):
    """'100.0:p__Foo;c__Bar' -> [('p', 'p__Foo'), ('c', 'c__Bar')]; '86.0' -> []."""
    out = []
    for part in str(lab).split(":"):
        for tok in part.split(";"):
            tok = tok.strip()
            if len(tok) > 3 and tok[1:3] == "__" and tok[0] in RANK_OF_PREFIX:
                out.append((tok[0], tok))
    return out


def prune(path: str, keep_tips: set):
    """Prune one GTDB tree to the tips in keep_tips.

    Keeps every kept tip, every node with two or more kept-tip-bearing children, the root, and every node that
    carries a GTDB rank name (so the page can label and collapse by rank) — keeping extra nodes changes no distance.
    A dropped unary node has its branch length summed into the surviving child.
    Returns (parent, length, label, is_tip) in preorder of the pruned tree.
    """
    par, ln, lab = parse_newick(path)
    nn = len(par)
    kids = [0] * nn
    for i in range(1, nn):
        kids[par[i]] += 1
    cnt = [0] * nn
    for i in range(nn - 1, -1, -1):
        if kids[i] == 0 and lab[i] in keep_tips:
            cnt[i] = 1
        if par[i] >= 0:
            cnt[par[i]] += cnt[i]
    nkept_kids = [0] * nn
    for i in range(nn - 1, 0, -1):
        if cnt[i] > 0:
            nkept_kids[par[i]] += 1
    retain = [cnt[i] > 0 and (par[i] < 0 or kids[i] == 0 or nkept_kids[i] >= 2 or bool(rank_names(lab[i])))
              for i in range(nn)]
    nra, dist = [-1] * nn, [0.0] * nn
    for i in range(nn):
        p = par[i]
        if p < 0:
            nra[i], dist[i] = -1, 0.0
        elif retain[p]:
            nra[i], dist[i] = p, ln[i]
        else:
            nra[i], dist[i] = nra[p], ln[i] + dist[p]
    sel = [i for i in range(nn) if retain[i]]
    newid = {o: k for k, o in enumerate(sel)}
    return ([(-1 if nra[o] < 0 else newid[nra[o]]) for o in sel], [dist[o] for o in sel],
            [lab[o] for o in sel], [kids[o] == 0 for o in sel])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--package", required=True)
    ap.add_argument("--sandpiper-dir", required=True)
    ap.add_argument("--countries", required=True)
    ap.add_argument("--basemap", default=None, help="vendored countries-110m.json; only checked against the "
                    "country ids written (the browser draws the map)")
    ap.add_argument("--gtdb-dir", required=True, help="bac120_r232.tree, ar53_r232.tree, r232_names.parquet, "
                    "ar53_taxonomy_r232.tsv.gz")
    ap.add_argument("--literature", default=None, help="directory of <slug>.json literature summaries (optional)")
    ap.add_argument("--vanish-results", default=None, help="track_a results.csv (contrast C1, stratum adult)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--memory-limit", default="2GB")
    ap.add_argument("--threads", type=int, default=4)
    ap.add_argument("--parquet", action="store_true", help="also write stats_long.parquet (the same cells as one "
                    "long table, for the downloads); off by default")
    ap.add_argument("--skip-projects", action="store_true", help="do not write projects.bin")
    ap.add_argument("--skip-presence", action="store_true",
                    help="do not write presence/ (which sample carries which taxon; about 24 MB)")
    a = ap.parse_args()
    t0 = time.time()
    SP, GT = a.sandpiper_dir, a.gtdb_dir
    os.makedirs(os.path.join(a.out, "_work"), exist_ok=True)

    def say(msg):
        print("[%5.0fs] %s" % (time.time() - t0, msg), flush=True)

    # ---- universe ------------------------------------------------------------------------------------------
    con = duckdb.connect()
    con.execute("SET memory_limit='%s'" % a.memory_limit)
    con.execute("SET threads=%d" % a.threads)
    con.execute("SET temp_directory='%s'" % os.path.join(a.out, "_work"))
    con.execute("SET preserve_insertion_order=false")
    qc = " OR ".join("s.%s" % c for c in QC_EXCLUDE)
    con.execute(f"""CREATE TABLE uni AS
        SELECT w.sample_key, w.study_accession,
               coalesce(w.body_site_code, 'no_site_attribute') AS site,
               coalesce(w.age_category, 'unknown') AS age,
               coalesce(w.lifestyle, 'not_recorded') AS life, w.country
        FROM read_parquet('{a.package}/catalog_sample_metadata_wide.parquet') w
        JOIN read_parquet('{SP}/gut_sandpiper_sample_summary.parquet') s USING (sample_key)
        WHERE w.release_retired IS NULL AND w.sample_type = 'specimen' AND NOT ({qc})""")
    n_samples, n_projects = con.execute("SELECT count(*), count(DISTINCT study_accession) FROM uni").fetchone()
    say("universe: %d QC-clean specimen samples in %d projects" % (n_samples, n_projects))

    # ---- taxa and ids --------------------------------------------------------------------------------------
    genera = sorted(x for x in con.execute(
        f"SELECT DISTINCT genus FROM read_parquet('{SP}/gut_sandpiper_sample_genus.parquet')").df().genus
        if x not in UNASSIGNED)
    species = sorted(x for x in con.execute(
        f"SELECT DISTINCT species FROM read_parquet('{SP}/gut_sandpiper_sample_species.parquet')").df().species
        if x not in UNASSIGNED)
    taxa = genera + species                      # taxon id = index into this list: genera first, each block sorted
    tid = {t: i for i, t in enumerate(taxa)}
    n_gen, n_spe = len(genera), len(species)
    say("taxa: %d genera + %d species = %d" % (n_gen, n_spe, len(taxa)))

    # ---- group tables (denominators once, not per taxon) ---------------------------------------------------
    def group_table(sql, order, label, extra=None):
        d = con.execute(sql).df()
        rows = {str(r.key): (int(r.n), int(r.np)) for r in d.itertuples()}
        if order:
            keys = [k for k in order if k in rows] + sorted(k for k in rows if k not in order)
        else:
            keys = sorted(rows, key=lambda k: (-rows[k][0], k))
        out = []
        for k in keys:
            n, np_ = rows[k]
            g = dict(key=k, label=label(k), n=n, proj=np_,
                     shown=bool(n >= MIN_CELL_SAMPLES and np_ >= MIN_CELL_PROJECTS))
            if extra:
                g.update(extra(k))
            out.append(g)
        return out

    cj = json.load(open(a.countries))
    have_iso = {c["iso2"] for c in cj}
    cj += [dict(iso2=k, num=v[0], name=v[1]) for k, v in EXTRA_COUNTRIES.items() if k not in have_iso]
    shape_ids = None
    if a.basemap:
        topo = json.load(open(a.basemap))
        shape_ids = {str(g.get("id")) for g in topo["objects"]["countries"]["geometries"]}
    num_of = {c["iso2"]: (str(c["num"]).lstrip("0") if shape_ids is None or str(c["num"]).lstrip("0") in shape_ids
                          else str(c["num"]).zfill(3)) for c in cj if c.get("num")}
    name_of = {c["iso2"]: c.get("name", c["iso2"]) for c in cj}

    # The body-site dimension is over every QC-clean sample. Age, country and lifestyle are computed WITHIN one
    # body site — the taxon's leading site — so their denominators are a (site x group) table, stored once.
    groups = {
        "site": group_table("SELECT site AS key, count(*) n, count(DISTINCT study_accession) np FROM uni GROUP BY 1",
                            SITE_ORDER, lambda k: SITE_LABEL.get(k, k), lambda k: dict(main=bool(k in ANATOMICAL))),
    }
    site_keys = [g["key"] for g in groups["site"]]
    DIM_COL = {"age": "age", "country": "country", "life": "life"}
    DIM_FILTER = {"age": "", "country": " AND country IS NOT NULL", "life": ""}
    DIM_ORDER = {"age": AGE_ORDER, "life": LIFE_ORDER, "country": None}
    DIM_LABEL = {"age": lambda k: AGE_LABEL.get(k, k), "life": lambda k: LIFE_LABEL.get(k, k),
                 "country": lambda k: name_of.get(k, k)}
    DIM_EXTRA = {"age": lambda k: dict(main=bool(k != "unknown")),
                 "life": lambda k: dict(main=bool(k != "not_recorded")),
                 "country": lambda k: dict(iso2=k, map=num_of.get(k), main=True)}
    denoms = {}
    for dim in ("age", "country", "life"):
        d = con.execute("SELECT site, %s AS key, count(*) n, count(DISTINCT study_accession) np FROM uni "
                        "WHERE true%s GROUP BY 1, 2" % (DIM_COL[dim], DIM_FILTER[dim])).df()
        tot = {}
        for k, n in zip(d.key, d.n):
            tot[str(k)] = tot.get(str(k), 0) + int(n)
        order = DIM_ORDER[dim]
        keys = ([k for k in order if k in tot] + sorted(k for k in tot if k not in order)) if order else \
            sorted(tot, key=lambda k: (-tot[k], k))
        gi = {k: i for i, k in enumerate(keys)}
        groups[dim] = [dict(key=k, label=DIM_LABEL[dim](k), n_all_sites=tot[k], **DIM_EXTRA[dim](k)) for k in keys]
        tbl = {s: [[0, 0] for _ in keys] for s in site_keys}
        for s, k, n, np_ in zip(d.site, d.key, d.n, d.np):
            tbl[s][gi[str(k)]] = [int(n), int(np_)]
        denoms[dim] = tbl
    miss_shape = [g["key"] for g in groups["country"] if shape_ids is not None and g.get("map") not in shape_ids]
    if miss_shape:
        print("[warn] %d country codes have no shape in the basemap: %s" % (len(miss_shape), sorted(miss_shape)[:8]),
              flush=True)
    gidx = {d: {g["key"]: i for i, g in enumerate(groups[d])} for d in DIMS}
    for d in DIMS:
        assert len(groups[d]) <= 256, "%s has more than 256 groups; the group index is one byte" % d
    say("groups: " + ", ".join("%s %d" % (d, len(groups[d])) for d in DIMS))

    # ---- the cube ------------------------------------------------------------------------------------------
    SRC = {"genus": ("genus", "gut_sandpiper_sample_genus.parquet"),
           "species": ("species", "gut_sandpiper_sample_species.parquet")}
    WHERE = {"site": "", "age": "", "life": "", "country": "AND u.country IS NOT NULL"}
    KEY = {"site": "u.site", "age": "u.age", "life": "u.life", "country": "u.country"}
    cells = {i: {d: [] for d in DIMS} for i in range(len(taxa))}
    cube = {}
    for lvl in ("genus", "species"):
        col, fname = SRC[lvl]
        cube[(lvl, "site")] = con.execute(f"""
            SELECT p.{col} AS taxon, u.site AS grp, count(*) AS det, sum(p.relabund) AS sra,
                   count(DISTINCT u.study_accession) AS np
            FROM read_parquet('{SP}/{fname}') p JOIN uni u USING (sample_key)
            WHERE p.relabund > 0 AND p.{col} NOT IN {UNASSIGNED}
            GROUP BY 1, 2 ORDER BY 1, 2""").df()
        say("cube %s/site: %d cells" % (lvl, len(cube[(lvl, "site")])))
    gn_site = [g["n"] for g in groups["site"]]
    for (lvl, dim), d in cube.items():
        for t, g, det, sra, np_ in zip(d.taxon, d.grp, d.det, d.sra, d.np):
            j = gidx["site"][str(g)]
            cells[tid[t]]["site"].append((j, int(det), float(sra) / gn_site[j], int(np_)))

    # Leading body site: among the nine anatomical groups that pass the display thresholds, the one with the
    # highest share of its samples carrying the taxon; ties by the number of samples carrying it, then by the
    # order of SITE_ORDER. A taxon detected in no such group has no leading site and no age/country/life cells.
    site_main = [g["key"] in ANATOMICAL and g["shown"] for g in groups["site"]]
    tax_samples, tax_site = [0] * len(taxa), [255] * len(taxa)
    for i in range(len(taxa)):
        best = None
        for j, det, mean, np_ in cells[i]["site"]:
            tax_samples[i] += det
            if site_main[j]:
                key = (det / gn_site[j], det, -j)
                if best is None or key > best:
                    best, tax_site[i] = key, j
        cells[i]["site"].sort()
    lead_key = [None if tax_site[i] == 255 else site_keys[tax_site[i]] for i in range(len(taxa))]
    by_lead = {}
    for i in range(len(taxa)):
        by_lead[lead_key[i]] = by_lead.get(lead_key[i], 0) + 1
    say("leading site: " + ", ".join("%s %d" % (k, v) for k, v in sorted(by_lead.items(), key=lambda kv: -kv[1])))

    # age, country and lifestyle, each taxon within the samples of its own leading body site
    for lvl in ("genus", "species"):
        col, fname = SRC[lvl]
        for dim in ("age", "country", "life"):
            d = con.execute(f"""
                SELECT p.{col} AS taxon, u.site AS site, {KEY[dim]} AS grp, count(*) AS det,
                       sum(p.relabund) AS sra, count(DISTINCT u.study_accession) AS np
                FROM read_parquet('{SP}/{fname}') p JOIN uni u USING (sample_key)
                WHERE p.relabund > 0 AND p.{col} NOT IN {UNASSIGNED} {WHERE[dim]}
                GROUP BY 1, 2, 3 ORDER BY 1, 2, 3""").df()
            kept = 0
            for t, s, g, det, sra, np_ in zip(d.taxon, d.site, d.grp, d.det, d.sra, d.np):
                i = tid[t]
                if lead_key[i] != s:
                    continue
                j = gidx[dim][str(g)]
                n = denoms[dim][s][j][0]
                cells[i][dim].append((j, int(det), float(sra) / n, int(np_)))
                kept += 1
            say("cube %s/%s: %d cells in the leading site (of %d)" % (lvl, dim, kept, len(d)))
    for i in cells:
        for d in DIMS:
            cells[i][d].sort()
    say("cells: %d non-zero" % sum(len(cells[i][d]) for i in cells for d in DIMS))

    # ---- literature flags and VANISH/BloSSUM classes --------------------------------------------------------
    ok_lit, lit_name = set(), {}
    if a.literature:
        for i, t in enumerate(taxa):
            p = os.path.join(a.literature, "%s.json" % slugify(t))
            if not os.path.exists(p):
                continue
            try:
                L = json.load(open(p))
            except Exception:
                continue
            if str(L.get("status")) != "ok":
                continue
            ok_lit.add(i)
            nm = re.sub(r"\s+", " ", re.sub(r"<[^>]*>", "", str(L.get("literature_name") or ""))).strip()
            if nm and nm != t.split("__", 1)[-1]:
                lit_name[i] = nm
    cls_of, cls_named = {}, {}
    if a.vanish_results:
        rv = pd.read_csv(a.vanish_results)
        rv = rv[(rv.contrast == "C1") & (rv.stratum == "adult")]
        for lvl, tx, cl in zip(rv.level, rv.taxon, rv["class"]):
            if cl not in CLASSES[1:]:
                continue
            if tx in tid:
                cls_of[tid[tx]] = CLASSES.index(cl)
            else:
                cls_named[tx] = CLASSES.index(cl)
    say("literature: %d taxa (%d with another literature name); classes: %d taxa, %d other clades" %
        (len(ok_lit), len(lit_name), len(cls_of), len(cls_named)))

    # ---- GTDB trees ----------------------------------------------------------------------------------------
    trees = [("Bacteria", os.path.join(GT, "bac120_r232.tree")), ("Archaea", os.path.join(GT, "ar53_r232.tree"))]
    tip_accs = {}
    for dom, path in trees:
        tip_accs[dom] = set(re.findall(r"[(,]([A-Za-z0-9_.]+):", open(path).read()))
    all_tips = tip_accs["Bacteria"] | tip_accs["Archaea"]
    tax_of_acc = {}
    bx = con.execute(f"SELECT accession, gtdb_taxonomy FROM read_parquet('{GT}/r232_names.parquet')").df()
    for acc, tx in zip(bx.accession, bx.gtdb_taxonomy):
        if acc in all_tips:
            tax_of_acc[acc] = tx
    del bx
    with gzip.open(os.path.join(GT, "ar53_taxonomy_r232.tsv.gz"), "rt") as fh:
        for line in fh:
            acc, tx = line.rstrip("\n").split("\t")
            if acc in all_tips:
                tax_of_acc[acc] = tx
    sp_acc, gen_sp, gen_lin = {}, {}, {}
    for dom, _ in trees:
        for acc in sorted(tip_accs[dom]):
            tx = tax_of_acc.get(acc)
            if not tx:
                continue
            parts = tx.split(";")
            sp, gn = parts[-1], parts[-2]
            gen_lin.setdefault(gn, parts[:-2])   # GTDB lineage of the genus, domain to family
            if sp not in sp_acc:                # one representative genome per GTDB species; first in sorted order
                sp_acc[sp] = (acc, dom)
                gen_sp.setdefault(gn, []).append(sp)
    # tips to keep: the representative of every catalog species, plus one representative per genus that has no
    # catalog species (deterministic: the alphabetically first GTDB species of that genus)
    keep = {"Bacteria": set(), "Archaea": set()}
    tip_taxa, genus_only, unplaced = {}, set(), set()
    for t in species:
        if t in sp_acc:
            acc, dom = sp_acc[t]
            keep[dom].add(acc)
            tip_taxa.setdefault((dom, acc), []).append(tid[t])
        else:
            unplaced.add(tid[t])
    gen_with_sp = {tax_of_acc[sp_acc[t][0]].split(";")[-2] for t in species if t in sp_acc}
    for t in genera:
        if t in gen_with_sp:
            continue                            # placed below, as the MRCA of its catalog species
        sps = sorted(gen_sp.get(t, []))
        if not sps:
            unplaced.add(tid[t])
            continue
        acc, dom = sp_acc[sps[0]]
        keep[dom].add(acc)
        tip_taxa.setdefault((dom, acc), []).append(tid[t])
        genus_only.add(tid[t])
    say("tips to keep: %d bacteria, %d archaea; %d genus-only tips; %d unplaced so far" %
        (len(keep["Bacteria"]), len(keep["Archaea"]), len(genus_only), len(unplaced)))

    # prune each domain, then join under one root with join edges of length 0 (not a measured distance)
    P, LN, LB, TIP = [-1], [0.0], ["root"], [False]
    acc_node = {}
    for dom, path in trees:
        p, l, lb, tp = prune(path, keep[dom])
        off = len(P)
        for k in range(len(p)):
            P.append(0 if p[k] < 0 else p[k] + off)
            LN.append(0.0 if p[k] < 0 else l[k])
            LB.append("d__%s" % dom if p[k] < 0 else lb[k])
            TIP.append(tp[k])
            if tp[k]:
                acc_node[(dom, lb[k])] = off + k
        say("pruned %s: %d nodes, %d tips" % (dom, len(p), sum(tp)))

    tax_node = [-1] * len(taxa)
    for (dom, acc), ids in sorted(tip_taxa.items()):
        for i in ids:
            tax_node[i] = acc_node[(dom, acc)]

    def recompute_ranges():
        n = len(P)
        mx, nt = list(range(n)), [0] * n
        for i in range(n - 1, 0, -1):
            if TIP[i]:
                nt[i] = 1
            if mx[i] > mx[P[i]]:
                mx[P[i]] = mx[i]
            nt[P[i]] += nt[i]
        return mx, nt

    mx, ntips = recompute_ranges()
    # a genus with catalog species sits on the most recent common ancestor of those species in the pruned tree
    sp_nodes_of_genus = {}
    for t in species:
        if t in sp_acc:
            sp_nodes_of_genus.setdefault(tax_of_acc[sp_acc[t][0]].split(";")[-2], []).append(tax_node[tid[t]])
    for t in genera:
        i = tid[t]
        if tax_node[i] >= 0 or i in unplaced:
            continue
        nodes = sp_nodes_of_genus.get(t)
        if not nodes:
            unplaced.add(i)
            continue
        lo, hi, anc = min(nodes), max(nodes), min(nodes)
        while not (anc <= lo and mx[anc] >= hi):
            anc = P[anc]
        tax_node[i] = anc
    # unplaced taxa: one group under the root, each a zero-length tip, so the page can still reach them
    if unplaced:
        up_root = len(P)
        P.append(0), LN.append(0.0), LB.append("Unplaced"), TIP.append(False)
        for i in sorted(unplaced):
            tax_node[i] = len(P)
            P.append(up_root), LN.append(0.0), LB.append(taxa[i]), TIP.append(True)
        mx, ntips = recompute_ranges()
    nn = len(P)
    assert all(x >= 0 for x in tax_node), "a taxon has no node"

    # node names and ranks: the GTDB clade label where the reference tree carries one, else the taxon at the node
    taxa_at = {}
    for i, nd in enumerate(tax_node):
        taxa_at.setdefault(nd, []).append(i)
    node_name, node_rank, node_cls = [""] * nn, [8] * nn, [0] * nn
    for k in range(nn):
        rn = rank_names(LB[k])
        if rn:
            node_name[k] = ";".join(nm for _, nm in rn)
            node_rank[k] = min(RANK_OF_PREFIX[p] for p, _ in rn)
            for _, nm in rn:
                if nm in cls_named:
                    node_cls[k] = cls_named[nm]
        elif LB[k] == "root":
            node_name[k], node_rank[k] = "Life", 0
        elif LB[k] == "Unplaced":
            node_name[k], node_rank[k] = "Unplaced", 9
    for k, ids in taxa_at.items():
        # a node that carries a taxon and no GTDB clade label of its own is left nameless here: the reader takes
        # its name from the taxon sitting on it (which saves ~0.7 MB of duplicated species names)
        if node_rank[k] == 8:
            node_rank[k] = 7 if max(ids) >= n_gen else 6
    say("tree: %d nodes, %d tips, %d internal" % (nn, sum(TIP), nn - sum(TIP)))

    # ---- GTDB taxonomy table (round 4) ----------------------------------------------------------------------
    # The pruned tree loses a clade label when the labelled node is pruned away (111 taxa in R2026.38 had an
    # incomplete lineage when it was read off the tree). The taxonomy outline and the breadcrumbs therefore
    # take the lineage from GTDB's own taxonomy strings: `higher` lists every domain..family name that has a
    # catalog genus, each with the index of its parent, and `genus_parent` gives the family of every genus.
    hi_ix, higher, genus_parent = {}, [], []
    for t in genera:
        par = -1
        for depth, nm in enumerate(gen_lin.get(t, [])):
            key = ";".join(gen_lin[t][:depth + 1])
            if key not in hi_ix:
                hi_ix[key] = len(higher)
                higher.append([nm[0], nm[3:], par])
            par = hi_ix[key]
        genus_parent.append(par)
    # samples carrying each name above genus = samples in which at least one of its genera is detected (the
    # profiles are read at genus resolution; a sample that has the clade only in an unassigned row is not
    # counted). The taxonomy list sorts on it.
    anc = []
    for gi, t in enumerate(genera):
        p = genus_parent[gi]
        while p >= 0:
            anc.append((t, p))
            p = higher[p][2]
    con.register("ganc_df", pd.DataFrame(anc, columns=["genus", "h"]))
    hn = dict(con.execute(f"""
        SELECT a.h, count(DISTINCT p.sample_key)
        FROM read_parquet('{SP}/gut_sandpiper_sample_genus.parquet') p JOIN uni u USING (sample_key)
        JOIN ganc_df a ON a.genus = p.genus
        WHERE p.relabund > 0 GROUP BY 1""").fetchall())
    con.unregister("ganc_df")
    for k in range(len(higher)):
        higher[k].append(int(hn.get(k, 0)))
    say("taxonomy table: %d names above genus, %d genera without a GTDB lineage" %
        (len(higher), sum(1 for x in genus_parent if x < 0)))

    # ---- tree.bin ------------------------------------------------------------------------------------------
    nm_off, nm_blob = blob_of(node_name)
    tx_off, tx_blob = blob_of(taxa)
    lit_ids = sorted(lit_name)
    ln_off, ln_blob = blob_of([lit_name[i] for i in lit_ids])
    flags = [(1 if i in ok_lit else 0) | (2 if i in genus_only else 0) | (4 if i in unplaced else 0) |
             (8 if tax_site[i] == 255 else 0) for i in range(len(taxa))]
    tree_hdr = dict(
        magic="SPXT", kind="taxon_explorer_tree", release="GTDB R232", n_nodes=nn, n_tips=int(sum(TIP)),
        n_internal=int(nn - sum(TIP)), n_taxa=len(taxa), n_genera=n_gen, n_species=n_spe,
        n_genus_only=len(genus_only), n_unplaced=len(unplaced), n_literature=len(ok_lit),
        ranks=RANKS, classes=CLASSES, len_scale=LEN_SCALE, site_keys=[g["key"] for g in groups["site"]],
        site_labels=[g["label"] for g in groups["site"]],
        domain_join="the bacterial and the archaeal reference tree are attached to one root with edges of length 0, "
                    "so every tip keeps its distance to its own domain root; the distance between a bacterial and "
                    "an archaeal tip is the sum of those two distances and is NOT a measured distance",
        taxon_ids="0..%d genera (sorted by GTDB name), %d..%d species (sorted by GTDB name)" %
                  (n_gen - 1, n_gen, len(taxa) - 1),
        flags={"1": "has literature summary", "2": "genus-only tip", "4": "unplaced",
               "8": "no leading body site: the taxon has no age, country or lifestyle cells"},
        n_no_leading_site=sum(1 for i in range(len(taxa)) if tax_site[i] == 255),
        taxonomy=dict(higher=higher, genus_parent=genus_parent,
                      note="GTDB R232 taxonomy: higher[k] = [rank letter, name, index of its parent or -1, "
                           "samples with at least one of its genera detected]; "
                           "genus_parent[genus id] = index into higher of its family, -1 if unknown"),
        leading_site="taxSite is the index into site_keys of the anatomical body-site group (main = true) that "
                     "passes the display thresholds and has the highest share of its samples carrying the taxon; "
                     "ties by the number of samples carrying it, then by body-site order. 255 = none. The age, "
                     "country and lifestyle cells of stats.bin are computed within that site's samples")
    tree_bin = container(tree_hdr, [
        ("parent", struct.pack("<%di" % nn, *P)),
        ("len", struct.pack("<%dI" % nn, *[int(round(x * LEN_SCALE)) for x in LN])),
        ("rank", bytes(bytearray(node_rank))),
        ("ntips", struct.pack("<%dI" % nn, *ntips)),
        ("nodeCls", bytes(bytearray(node_cls))),
        ("nameOff", nm_off), ("nameBlob", nm_blob),
        ("taxNode", struct.pack("<%dI" % len(taxa), *tax_node)),
        ("taxSamples", struct.pack("<%dI" % len(taxa), *tax_samples)),
        ("taxSite", bytes(bytearray(tax_site))),
        ("taxCls", bytes(bytearray([cls_of.get(i, 0) for i in range(len(taxa))]))),
        ("taxFlags", bytes(bytearray(flags))),
        ("taxNameOff", tx_off), ("taxNameBlob", tx_blob),
        ("litIds", struct.pack("<%dI" % len(lit_ids), *lit_ids)),
        ("litNameOff", ln_off), ("litNameBlob", ln_blob)])
    open(os.path.join(a.out, "tree.bin"), "wb").write(tree_bin)
    say("tree.bin: %d bytes" % len(tree_bin))

    # ---- stats.bin -----------------------------------------------------------------------------------------
    body, index = bytearray(), [0]
    for i in range(len(taxa)):
        for d in DIMS:
            cc = cells[i][d]
            body += uvarint(len(cc))
            prev = 0
            for j, det, mean, np_ in cc:
                body += uvarint(j - prev)
                prev = j
            for j, det, mean, np_ in cc:
                body += uvarint(det)
            for j, det, mean, np_ in cc:
                body += struct.pack("<H", ra_code(mean))
            for j, det, mean, np_ in cc:
                body += uvarint(np_)
        index.append(len(body))
    stats_hdr = dict(
        magic="SPXS", kind="taxon_explorer_stats", n_taxa=len(taxa), dims=DIMS, groups=groups, ra_scale=RA_SCALE,
        ra_note="mean relative abundance = 10 ** (-code / %d), a fraction of 1" % RA_SCALE,
        min_cell_samples=MIN_CELL_SAMPLES, min_cell_projects=MIN_CELL_PROJECTS,
        n_samples=int(n_samples), n_projects=int(n_projects), site_keys=site_keys,
        denoms=denoms, denom_note="denoms[dim][site key][group index] = [samples, projects] for that body site; "
                                  "a group is shown when samples >= min_cell_samples and projects >= "
                                  "min_cell_projects; the body-site dimension keeps its own n/proj/shown in groups",
        scope={"site": "every QC-clean specimen sample",
               "age": "the samples of the taxon's leading body site",
               "life": "the samples of the taxon's leading body site",
               "country": "the samples of the taxon's leading body site that have a country"},
        leading_site="the anatomical body-site group (main = true) that passes the display thresholds and has the "
                     "highest share of its samples carrying the taxon; ties by the number of samples carrying it, "
                     "then by body-site order. Stored per taxon in tree.bin as taxSite (255 = none; taxFlags bit 8)",
        taxa_by_leading_site={(k or "none"): v for k, v in sorted(by_lead.items(), key=lambda kv: -kv[1])},
        cell="per dimension, in the order of 'dims': varint k, then k varint group-index deltas (ascending), k "
             "varint detected counts, k uint16 abundance codes, k varint project counts; cells with no detection "
             "are not stored")
    stats_bin = container(stats_hdr, [("index", struct.pack("<%dI" % len(index), *index)), ("cells", bytes(body))])
    open(os.path.join(a.out, "stats.bin"), "wb").write(stats_bin)
    say("stats.bin: %d bytes" % len(stats_bin))

    # ---- projects.bin (optional) ---------------------------------------------------------------------------
    if not a.skip_projects:
        parts = []
        for lvl in ("genus", "species"):
            col, fname = SRC[lvl]
            parts.append(con.execute(f"""
                WITH det AS (SELECT p.{col} AS taxon, u.site AS site, u.study_accession AS acc, count(*) AS d,
                                    sum(p.relabund) AS sra
                             FROM read_parquet('{SP}/{fname}') p JOIN uni u USING (sample_key)
                             WHERE p.relabund > 0 AND p.{col} NOT IN {UNASSIGNED} GROUP BY 1, 2, 3),
                     pr AS (SELECT site, study_accession AS acc, count(*) AS n FROM uni GROUP BY 1, 2
                            HAVING count(*) >= {MIN_PROJ_SAMPLES})
                SELECT taxon, site, acc, d, sra / n AS mean FROM det JOIN pr USING (site, acc)
                QUALIFY row_number() OVER (PARTITION BY taxon, site ORDER BY sra / n DESC, acc) <= {N_TOP_PROJECTS}
                ORDER BY taxon, site, mean DESC, acc""").df())
        tp = pd.concat(parts, ignore_index=True)
        st = pd.read_parquet("%s/catalog_studies.parquet" % a.package)
        if "release_retired" in st.columns:
            st = st[st.release_retired.isna()]
        tcol = "study_title" if "study_title" in st.columns else ("title" if "title" in st.columns else None)
        titles = dict(zip(st.study_accession, st[tcol])) if tcol else {}
        tp = tp[[lead_key[tid[t]] == s for t, s in zip(tp.taxon, tp.site)]]
        pn = {(s, acc): int(n) for s, acc, n in
              con.execute("SELECT site, study_accession, count(*) FROM uni GROUP BY 1, 2").fetchall()}
        accs = sorted(set(tp.acc))
        pidx = {acc: i for i, acc in enumerate(accs)}
        rec = {i: [] for i in range(len(taxa))}
        used = set()
        for t, s, acc, d, mean in zip(tp.taxon, tp.site, tp.acc, tp.d, tp["mean"]):
            rec[tid[t]].append((pidx[acc], int(d), float(mean)))
            used.add((gidx["site"][s], pidx[acc]))
        pbody, pindex = bytearray(), [0]
        for i in range(len(taxa)):
            pbody += uvarint(len(rec[i]))
            for p, d, m in rec[i]:
                pbody += uvarint(p) + uvarint(d) + struct.pack("<H", ra_code(m))
            pindex.append(len(pbody))
        ac_off, ac_blob = blob_of(accs)
        ti_off, ti_blob = blob_of([str(titles.get(x, "") or "") for x in accs])
        trip = []
        for si, pi in sorted(used):
            trip += [si, pi, pn.get((site_keys[si], accs[pi]), 0)]
        proj_hdr = dict(magic="SPXP", kind="taxon_explorer_projects", n_taxa=len(taxa), n_projects=len(accs),
                        n_top=N_TOP_PROJECTS, min_project_samples=MIN_PROJ_SAMPLES, ra_scale=RA_SCALE,
                        n_denom=len(used), site_keys=site_keys,
                        scope="the samples of the taxon's leading body site in that project — the round-2 rule, "
                              "which scoped the most-abundant list to the taxon's own sites",
                        denom="projDenom holds uint32 triples (site index, project index, samples of that project "
                              "at that site); a taxon's denominators are the triples of its leading site",
                        cell="per taxon: varint k, then k x (varint project index, varint detected, uint16 "
                             "abundance code), ordered by mean relative abundance, descending")
        proj_bin = container(proj_hdr, [
            ("index", struct.pack("<%dI" % len(pindex), *pindex)), ("cells", bytes(pbody)),
            ("projDenom", struct.pack("<%dI" % len(trip), *trip)),
            ("accOff", ac_off), ("accBlob", ac_blob), ("titleOff", ti_off), ("titleBlob", ti_blob)])
        open(os.path.join(a.out, "projects.bin"), "wb").write(proj_bin)
        say("projects.bin: %d bytes (%d projects referenced)" % (len(proj_bin), len(accs)))

    # ---- presence (round 4): which sample carries which taxon ---------------------------------------------
    # The taxon view lists the samples of a group that carry the taxon. Samples are numbered in the order
    # body site, project, accession, so that carriers form runs; a taxon's block is raw DEFLATE of either its
    # sorted sample indices as varint gaps or a bitmap over all samples, whichever is smaller. Blocks are
    # packed into shards of about 256 KB, so the page fetches one small file per taxon asked for.
    n_presence_files, n_presence_bytes = 0, 0
    if not a.skip_presence:
        import zlib
        import numpy as np

        def deflate(b):
            c = zlib.compressobj(9, zlib.DEFLATED, -15)
            return c.compress(b) + c.flush()

        def varints(vals):
            out = bytearray()
            for v in vals:
                while v >= 128:
                    out.append((v & 127) | 128)
                    v >>= 7
                out.append(v)
            return bytes(out)

        pdir = os.path.join(a.out, "presence")
        os.makedirs(pdir, exist_ok=True)           # nothing is deleted: pages/taxa.py copies only the shards
        #                                            that index.bin names, so a leftover shard never ships
        sm = con.execute("SELECT sample_key, study_accession, site, age, life, country FROM uni "
                         "ORDER BY site, study_accession, sample_key").df()
        ns = len(sm)
        assert ns == n_samples
        pkeys = {d: [g["key"] for g in groups[d]] for d in ("site", "age", "life", "country")}
        assert len(pkeys["country"]) < 255 and all(len(pkeys[d]) < 255 for d in pkeys)
        pix = {d: {k: i for i, k in enumerate(pkeys[d])} for d in pkeys}
        accs_p = sorted(set(sm.study_accession))
        assert len(accs_p) < 65536
        aix = {x: i for i, x in enumerate(accs_p)}
        s_site = bytes(bytearray(pix["site"][str(x)] for x in sm.site))
        s_age = bytes(bytearray(pix["age"][str(x)] for x in sm.age))
        s_life = bytes(bytearray(pix["life"][str(x)] for x in sm.life))
        s_country = bytes(bytearray(255 if x is None or x != x else pix["country"][str(x)] for x in sm.country))
        s_study = struct.pack("<%dH" % ns, *[aix[x] for x in sm.study_accession])
        s_keys = "\n".join(sm.sample_key.astype(str)).encode()
        con.register("samp_df", pd.DataFrame({"sample_key": sm.sample_key, "idx": np.arange(ns, dtype=np.int32)}))
        con.register("taxid_df", pd.DataFrame({"taxon": taxa, "tid": np.arange(len(taxa), dtype=np.int32)}))
        t_shard, t_off, t_len = [0] * len(taxa), [0] * len(taxa), [0] * len(taxa)
        t_enc, t_n = [0] * len(taxa), [0] * len(taxa)
        shard, shard_sizes, n_pairs, n_bitmap = bytearray(), [], 0, 0
        SHARD_BYTES = 262144

        def flush():
            if shard:
                open(os.path.join(pdir, "p%03d.bin" % len(shard_sizes)), "wb").write(bytes(shard))
                shard_sizes.append(len(shard))
                del shard[:]

        for lvl in ("genus", "species"):
            col, fname = SRC[lvl]
            d = con.execute(f"""
                SELECT t.tid AS t, s.idx AS i
                FROM read_parquet('{SP}/{fname}') p JOIN samp_df s USING (sample_key)
                JOIN taxid_df t ON t.taxon = p.{col}
                WHERE p.relabund > 0 AND p.{col} NOT IN {UNASSIGNED}
                ORDER BY 1, 2""").fetchnumpy()
            tt, ii = np.asarray(d["t"]), np.asarray(d["i"]).astype(np.int64)
            cut = np.flatnonzero(np.diff(tt)) + 1
            starts = np.concatenate([[0], cut]) if len(tt) else np.array([], dtype=np.int64)
            ends = np.concatenate([cut, [len(tt)]]) if len(tt) else np.array([], dtype=np.int64)
            for s0, e0 in zip(starts, ends):
                i_tax, x = int(tt[s0]), ii[s0:e0]
                blk, enc = deflate(varints(np.diff(x, prepend=-1).tolist())), 1
                if len(x) > ns // 64:              # a bitmap can only win for a widely carried taxon
                    bm = np.zeros(ns, dtype=np.uint8)
                    bm[x] = 1
                    alt = deflate(np.packbits(bm).tobytes())
                    if len(alt) < len(blk):
                        blk, enc = alt, 2
                        n_bitmap += 1
                if len(shard) and len(shard) + len(blk) > SHARD_BYTES:
                    flush()
                t_shard[i_tax], t_off[i_tax], t_len[i_tax] = len(shard_sizes), len(shard), len(blk)
                t_enc[i_tax], t_n[i_tax] = enc, len(x)
                shard += blk
                n_pairs += len(x)
            say("presence %s: %d taxa with carriers" % (lvl, len(starts)))
        flush()
        con.unregister("samp_df")
        con.unregister("taxid_df")
        bad = [i for i in range(len(taxa)) if t_n[i] != tax_samples[i]]
        assert not bad, "presence and the body-site cells disagree for %d taxa, e.g. %s" % (len(bad), taxa[bad[0]])
        zsec = dict(sampSiteZ=s_site, sampAgeZ=s_age, sampLifeZ=s_life, sampCountryZ=s_country,
                    sampStudyZ=s_study, keysZ=s_keys)
        ac_off2, ac_blob2 = blob_of(accs_p)
        pres_hdr = dict(
            magic="SPXS", kind="taxon_explorer_presence", n_samples=ns, n_taxa=len(taxa), n_pairs=int(n_pairs),
            n_shards=len(shard_sizes), shard_bytes=shard_sizes, n_bitmap=n_bitmap,
            site_keys=pkeys["site"], age_keys=pkeys["age"], life_keys=pkeys["life"], country_keys=pkeys["country"],
            raw={k: len(v) for k, v in zsec.items()},
            order="samples are numbered by body site, project accession, sample accession",
            encoding="taxEnc 0 = no carrier, 1 = raw DEFLATE of the varint gaps between sorted sample indices "
                     "(first gap = index + 1), 2 = raw DEFLATE of a bitmap over all samples, most significant "
                     "bit first. A block is bytes taxOff..taxOff+taxLen of shard taxShard (pNNN.bin). Sections "
                     "ending in Z are raw DEFLATE; header.raw gives their decoded length. sampCountry 255 = none; "
                     "sampStudy is uint16 little-endian, an index into the project accessions")
        pres_bin = container(pres_hdr, [
            ("taxShard", struct.pack("<%dH" % len(taxa), *t_shard)), ("taxOff", struct.pack("<%dI" % len(taxa), *t_off)),
            ("taxLen", struct.pack("<%dI" % len(taxa), *t_len)), ("taxEnc", bytes(bytearray(t_enc))),
            ("taxN", struct.pack("<%dI" % len(taxa), *t_n))] +
            [(k, deflate(v)) for k, v in zsec.items()] + [("accOff", ac_off2), ("accBlob", ac_blob2)])
        open(os.path.join(pdir, "index.bin"), "wb").write(pres_bin)
        n_presence_files = len(shard_sizes) + 1
        n_presence_bytes = sum(shard_sizes) + len(pres_bin)
        say("presence/: %d pairs, %d shards, %d bytes in all (index.bin %d bytes; %d taxa as bitmaps)" %
            (n_pairs, len(shard_sizes), n_presence_bytes, len(pres_bin), n_bitmap))

    # ---- literature, copied unchanged ----------------------------------------------------------------------
    if a.literature:
        dst = os.path.join(a.out, "lit")
        os.makedirs(dst, exist_ok=True)
        n_copied = 0
        for f in sorted(os.listdir(a.literature)):
            if f.endswith(".json"):
                shutil.copyfile(os.path.join(a.literature, f), os.path.join(dst, f))
                n_copied += 1
        say("lit/: %d files copied unchanged" % n_copied)

    # ---- optional long table -------------------------------------------------------------------------------
    if a.parquet:
        rows = []                       # built from the cells that were written, so the scope is the same
        for i in range(len(taxa)):
            for dim in DIMS:
                for j, det, mean, np_ in cells[i][dim]:
                    n = groups["site"][j]["n"] if dim == "site" else denoms[dim][lead_key[i]][j][0]
                    rows.append((taxa[i], "genus" if i < n_gen else "species", dim,
                                 "all_sites" if dim == "site" else lead_key[i], groups[dim][j]["key"], n,
                                 det, det / n, mean, np_))
        long = pd.DataFrame(rows, columns=["taxon", "level", "dimension", "scope_site", "group", "n_group",
                                           "n_detected", "prevalence", "mean_relabund", "n_projects_detected"]
                            ).sort_values(["level", "dimension", "taxon", "group"]).reset_index(drop=True)
        long.round({"prevalence": 6, "mean_relabund": 10}).to_parquet(
            os.path.join(a.out, "stats_long.parquet"), index=False, compression="zstd")
        say("stats_long.parquet: %d rows, %d bytes" %
            (len(long), os.path.getsize(os.path.join(a.out, "stats_long.parquet"))))

    # ---- manifest ------------------------------------------------------------------------------------------
    vj = json.load(open("%s/VERSION.json" % a.package))
    files = {}
    for root, _, fs in os.walk(a.out):
        if "_work" in root:
            continue
        for f in sorted(fs):
            if f != "manifest.json":
                p = os.path.join(root, f)
                files[os.path.relpath(p, a.out)] = os.path.getsize(p)
    man = dict(package_version=vj.get("package_version"), release_id=vj.get("release_id"), gtdb="R232",
               n_taxa=len(taxa), n_genera=n_gen, n_species=n_spe, n_nodes=nn, n_tips=int(sum(TIP)),
               n_internal=int(nn - sum(TIP)), n_genus_only=len(genus_only), n_unplaced=len(unplaced),
               n_literature=len(ok_lit), n_samples=int(n_samples), n_projects=int(n_projects),
               n_cells=int(sum(len(cells[i][d]) for i in cells for d in DIMS)),
               n_no_leading_site=int(by_lead.get(None, 0)),
               taxa_by_leading_site={(k or "none"): v for k, v in sorted(by_lead.items(), key=lambda kv: -kv[1])},
               bytes={k: v for k, v in sorted(files.items())
                      if not k.startswith("lit/") and not k.startswith("presence/")},
               presence_files=n_presence_files, presence_bytes=n_presence_bytes,
               lit_files=sum(1 for k in files if k.startswith("lit/")),
               lit_bytes=sum(v for k, v in files.items() if k.startswith("lit/")))
    json.dump(man, open(os.path.join(a.out, "manifest.json"), "w"), separators=(",", ":"), sort_keys=True, indent=1)
    shutil.rmtree(os.path.join(a.out, "_work"), ignore_errors=True)
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1024.0 ** 3)
    say("done in %.0f s, peak RSS %.2f GB" % (time.time() - t0, peak))
    print(json.dumps(dict(seconds=round(time.time() - t0, 1), peak_rss_gb=round(peak, 2),
                          **{k: v for k, v in man.items() if k != "bytes"})))


if __name__ == "__main__":
    main()
