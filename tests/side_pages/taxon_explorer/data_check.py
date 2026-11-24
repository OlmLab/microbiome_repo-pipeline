#!/usr/bin/env python
"""data_check.py — checks the taxon explorer payload against the sources it was built from.

  (ii) round 2: for the 721 taxa that had their own page, the detected counts by body site must equal those in
       track_b/out721/taxa/<slug>.json (the same population and the same group keys).
 (iii) tree: for 200 sampled species tips the distance to the domain root must equal the distance in the UNPRUNED
       GTDB tree within 1e-6; every genus node must be an ancestor of all of its catalog species; the number of
       tips must equal species + genus-only taxa; no branch length may be negative.
   (i) cells: expected numbers for 300 taxa sampled with a fixed seed plus the 20 genera carried by the most
       samples are recomputed from the profile tables with DuckDB and written to --expected; reader_check.mjs
       compares them with what the JavaScript reader returns from the payload.

usage: python data_check.py --payload track_b/explorer_out --package ... --sandpiper-dir ... --gtdb-dir ... \
         --out721 track_b/out721/taxa --expected tests/expected_cells.json [--memory-limit 2GB]
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
import random
import struct
import sys

import duckdb

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from taxon_explorer import (QC_EXCLUDE, UNASSIGNED, parse_newick, slugify)   # noqa: E402

SEED = 20261115
N_SAMPLE = 300
N_TOP = 20
N_NON_GUT = 100
N_TIPS_CHECKED = 200


def read_container(path, magic):
    b = open(path, "rb").read()
    assert b[:4].decode() == magic, "%s is not a %s file" % (path, magic)
    hlen = struct.unpack("<I", b[6:10])[0]
    header = json.loads(b[10:10 + hlen].decode())
    base = 10 + hlen
    sec = {k: b[base + v[0]:base + v[0] + v[1]] for k, v in header["sections"].items()}
    return header, sec


def uvarint(b, i):
    v, s = 0, 0
    while True:
        x = b[i]
        i += 1
        v |= (x & 0x7F) << s
        s += 7
        if not x & 0x80:
            return v, i


def decode_stats(header, sec):
    """-> {taxon id: {dim: {group index: (detected, mean, n_projects)}}}"""
    idx = struct.unpack("<%dI" % (len(sec["index"]) // 4), sec["index"])
    cells, dims, scale = sec["cells"], header["dims"], header["ra_scale"]
    out = {}
    for t in range(header["n_taxa"]):
        i, rec = idx[t], {}
        for dim in dims:
            k, i = uvarint(cells, i)
            g, prev = [], 0
            for _ in range(k):
                d, i = uvarint(cells, i)
                prev += d
                g.append(prev)
            det = []
            for _ in range(k):
                d, i = uvarint(cells, i)
                det.append(d)
            mean = []
            for _ in range(k):
                mean.append(10 ** (-struct.unpack_from("<H", cells, i)[0] / scale))
                i += 2
            npj = []
            for _ in range(k):
                d, i = uvarint(cells, i)
                npj.append(d)
            rec[dim] = {g[j]: (det[j], mean[j], npj[j]) for j in range(k)}
        assert i == idx[t + 1], "record %d does not end where the index says" % t
        out[t] = rec
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--payload", required=True)
    ap.add_argument("--package", required=True)
    ap.add_argument("--sandpiper-dir", required=True)
    ap.add_argument("--gtdb-dir", required=True)
    ap.add_argument("--out721", required=True)
    ap.add_argument("--expected", required=True)
    ap.add_argument("--memory-limit", default="2GB")
    ap.add_argument("--threads", type=int, default=4)
    a = ap.parse_args()
    th, ts = read_container(os.path.join(a.payload, "tree.bin"), "SPXT")
    sh, ss = read_container(os.path.join(a.payload, "stats.bin"), "SPXS")
    nn, ntx = th["n_nodes"], th["n_taxa"]
    parent = struct.unpack("<%di" % nn, ts["parent"])
    length = struct.unpack("<%dI" % nn, ts["len"])
    taxnode = struct.unpack("<%dI" % ntx, ts["taxNode"])
    taxflags = ts["taxFlags"]
    tname_off = struct.unpack("<%dI" % (ntx + 1), ts["taxNameOff"])
    tname = [ts["taxNameBlob"][tname_off[i]:tname_off[i + 1]].decode() for i in range(ntx)]
    tid = {t: i for i, t in enumerate(tname)}
    scale = th["len_scale"]
    site_keys = th["site_keys"]
    lead = [None if b == 255 else site_keys[b] for b in ts["taxSite"]]   # the taxon's leading body site
    fail, checks = [], {}

    def ok(name, cond, detail=""):
        checks[name] = checks.get(name, [0, 0])
        checks[name][0 if cond else 1] += 1
        if not cond and len(fail) < 20:
            fail.append("%s: %s" % (name, detail))

    # ---- (iii) tree ----------------------------------------------------------------------------------------
    kids = [0] * nn
    for i in range(1, nn):
        kids[parent[i]] += 1
    tips = [i for i in range(nn) if kids[i] == 0]
    n_genus_only = sum(1 for f in taxflags if f & 2)
    ok("tips = species + genus-only", len(tips) == th["n_species"] + n_genus_only,
       "%d vs %d + %d" % (len(tips), th["n_species"], n_genus_only))
    ok("no negative branch length", all(x >= 0 for x in length))
    depth = [0.0] * nn
    for i in range(1, nn):
        depth[i] = depth[parent[i]] + length[i] / scale
    # distance to the domain root in the UNPRUNED trees, by representative accession
    rng = random.Random(SEED)
    tip_all = {}
    for dom, fn in (("Bacteria", "bac120_r232.tree"), ("Archaea", "ar53_r232.tree")):
        p0, l0, lb0 = parse_newick(os.path.join(a.gtdb_dir, fn))
        d0, k0 = [0.0] * len(p0), [0] * len(p0)
        for i in range(1, len(p0)):
            d0[i] = d0[p0[i]] + l0[i]
            k0[p0[i]] += 1
        n_dom = 0
        for i in range(len(p0)):
            if k0[i] == 0:
                tip_all[lb0[i]] = d0[i]
                n_dom += 1
        ok("unpruned tree read (%s)" % dom, n_dom > 0, "%d tips" % n_dom)
    # the GTDB taxonomy of every tip representative (the same rule the build used: first accession in sort order)
    con = duckdb.connect()
    con.execute("SET memory_limit='%s'" % a.memory_limit)
    con.execute("SET threads=%d" % a.threads)
    gtax = {}
    bx = con.execute("SELECT accession, gtdb_taxonomy FROM read_parquet('%s/r232_names.parquet')" % a.gtdb_dir).df()
    for acc, tx in zip(bx.accession, bx.gtdb_taxonomy):
        if acc in tip_all:
            gtax[acc] = tx
    del bx
    with gzip.open(os.path.join(a.gtdb_dir, "ar53_taxonomy_r232.tsv.gz"), "rt") as fh:
        for line in fh:
            acc, tx = line.rstrip("\n").split("\t")
            if acc in tip_all:
                gtax[acc] = tx
    acc_of_sp = {}
    for acc in sorted(gtax):
        sp = gtax[acc].split(";")[-1]
        if sp not in acc_of_sp:
            acc_of_sp[sp] = acc
    species_ids = [i for i in range(ntx) if i >= th["n_genera"]]
    for i in rng.sample(species_ids, N_TIPS_CHECKED):
        acc = acc_of_sp.get(tname[i])
        if acc is None:
            ok("tip distance", False, "%s has no representative" % tname[i])
            continue
        ok("tip distance", abs(depth[taxnode[i]] - tip_all[acc]) < 1e-6,
           "%s: %.9f vs %.9f" % (tname[i], depth[taxnode[i]], tip_all[acc]))
    # every genus node is an ancestor of all of its catalog species
    mx = list(range(nn))
    for i in range(nn - 1, 0, -1):
        if mx[i] > mx[parent[i]]:
            mx[parent[i]] = mx[i]
    sp_by_genus = {}
    for i in species_ids:
        acc = acc_of_sp.get(tname[i])
        if acc:
            sp_by_genus.setdefault(gtax[acc].split(";")[-2], []).append(i)
    for g, ids in sorted(sp_by_genus.items()):
        gi = tid.get(g)
        if gi is None:
            continue
        gnode = taxnode[gi]
        bad = [i for i in ids if not (gnode <= taxnode[i] <= mx[gnode])]
        ok("genus node is an ancestor of its species", not bad,
           "%s misses %d of %d species" % (g, len(bad), len(ids)))

    # ---- (ii) round 2 --------------------------------------------------------------------------------------
    stats = decode_stats(sh, ss)
    site_idx = {g["key"]: i for i, g in enumerate(sh["groups"]["site"])}
    n721, n_gut_lead = 0, 0
    for f in sorted(os.listdir(a.out721)):
        if not f.endswith(".json"):
            continue
        J = json.load(open(os.path.join(a.out721, f)))
        i = tid.get(J["taxon"])
        if i is None:
            ok("round-2 taxon present", False, J["taxon"])
            continue
        n721 += 1
        cols = J["cols"]
        kcol, dcol = cols.index("key"), cols.index("det")
        for row in J["site"]:
            want, got = int(row[dcol]), stats[i]["site"].get(site_idx[row[kcol]], (0, 0, 0))[0]
            ok("round-2 body-site detected counts", want == got,
               "%s / %s: %d vs %d" % (J["taxon"], row[kcol], want, got))
        # round 2 computed age and lifestyle among gut_stool samples for every taxon it carried; for the taxa
        # whose leading site is gut_stool the explorer's scope is the same, so the counts must still agree
        if lead[i] == "gut_stool":
            n_gut_lead += 1
            for dim in ("age", "life"):
                gidx_ = {g["key"]: k for k, g in enumerate(sh["groups"][dim])}
                for row in J[dim]:
                    want, got = int(row[dcol]), stats[i][dim].get(gidx_[row[kcol]], (0, 0, 0))[0]
                    ok("round-2 %s detected counts (gut-leading taxa)" % dim, want == got,
                       "%s / %s: %d vs %d" % (J["taxon"], row[kcol], want, got))
    print("round-2 pages compared: %d (%d of them gut-leading)" % (n721, n_gut_lead))

    # ---- (i) expectations for the reader -------------------------------------------------------------------
    top = sorted(range(th["n_genera"]), key=lambda i: (-struct.unpack("<%dI" % ntx, ts["taxSamples"])[i], tname[i]))[:N_TOP]
    non_gut = [i for i in range(ntx) if lead[i] not in (None, "gut_stool")]
    pick = sorted(set(random.Random(SEED).sample(range(ntx), N_SAMPLE)) | set(top) |
                  set(random.Random(SEED + 1).sample(non_gut, N_NON_GUT)))
    names = {"genus": [tname[i] for i in pick if i < th["n_genera"]],
             "species": [tname[i] for i in pick if i >= th["n_genera"]]}
    print("taxa checked: %d (%d with a leading site other than gut_stool, %d with none)" %
          (len(pick), sum(1 for i in pick if lead[i] not in (None, "gut_stool")),
           sum(1 for i in pick if lead[i] is None)))
    qc = " OR ".join("s.%s" % c for c in QC_EXCLUDE)
    con.execute(f"""CREATE TABLE uni AS
        SELECT w.sample_key, w.study_accession, coalesce(w.body_site_code, 'no_site_attribute') AS site,
               coalesce(w.age_category, 'unknown') AS age, coalesce(w.lifestyle, 'not_recorded') AS life, w.country
        FROM read_parquet('{a.package}/catalog_sample_metadata_wide.parquet') w
        JOIN read_parquet('{a.sandpiper_dir}/gut_sandpiper_sample_summary.parquet') s USING (sample_key)
        WHERE w.release_retired IS NULL AND w.sample_type = 'specimen' AND NOT ({qc})""")
    WHERE = {"site": "", "age": "", "life": "", "country": "AND u.country IS NOT NULL"}
    KEY = {"site": "u.site", "age": "u.age", "life": "u.life", "country": "u.country"}
    # the body-site dimension is over every sample; the other three are over the taxon's leading site only, with
    # that site's denominators (header.denoms[dim][site][group index])
    gn_site = {g["key"]: g["n"] for g in sh["groups"]["site"]}
    gidx_of = {d: {g["key"]: k for k, g in enumerate(sh["groups"][d])} for d in sh["dims"]}
    expected, presence = {}, {}
    for lvl, col, fname in (("genus", "genus", "gut_sandpiper_sample_genus.parquet"),
                            ("species", "species", "gut_sandpiper_sample_species.parquet")):
        if not names[lvl]:
            continue
        lst = ",".join("'%s'" % t for t in names[lvl])
        # round 4: the carriers themselves, for tests/presence_check.mjs — how many, and the md5 of their
        # sorted accessions joined by newlines
        for t, n, h in con.execute(f"""
                SELECT p.{col}, count(*), md5(string_agg(u.sample_key, chr(10) ORDER BY u.sample_key))
                FROM read_parquet('{a.sandpiper_dir}/{fname}') p JOIN uni u USING (sample_key)
                WHERE p.relabund > 0 AND p.{col} IN ({lst}) AND p.{col} NOT IN {UNASSIGNED}
                GROUP BY 1""").fetchall():
            presence[str(tid[t])] = [int(n), h]
        for dim in sh["dims"]:
            d = con.execute(f"""
                SELECT p.{col} AS taxon, u.site AS site, {KEY[dim]} AS grp, count(*) AS det,
                       sum(p.relabund) AS sra, count(DISTINCT u.study_accession) AS np
                FROM read_parquet('{a.sandpiper_dir}/{fname}') p JOIN uni u USING (sample_key)
                WHERE p.relabund > 0 AND p.{col} IN ({lst}) AND p.{col} NOT IN {UNASSIGNED} {WHERE[dim]}
                GROUP BY 1, 2, 3 ORDER BY 1, 2, 3""").df()
            for t, s, g, det, sra, np_ in zip(d.taxon, d.site, d.grp, d.det, d.sra, d.np):
                i = tid[t]
                if dim == "site":
                    n = gn_site[str(s)]
                elif lead[i] != s:
                    continue
                else:
                    n = sh["denoms"][dim][s][gidx_of[dim][str(g)]][0]
                e = expected.setdefault(str(i), {})
                e.setdefault(dim, {})[str(g)] = [int(det), float(sra) / n, int(np_)]
    out = dict(seed=SEED, n_taxa_checked=len(pick), ra_scale=sh["ra_scale"],
               taxa={str(i): tname[i] for i in pick}, cells=expected,
               presence={str(i): presence.get(str(i), [0, "d41d8cd98f00b204e9800998ecf8427e"]) for i in pick},
               lead={str(i): lead[i] for i in pick},
               slugs={str(i): slugify(tname[i]) for i in pick})
    os.makedirs(os.path.dirname(os.path.abspath(a.expected)), exist_ok=True)
    json.dump(out, open(a.expected, "w"), separators=(",", ":"), sort_keys=True)
    print("expected cells for %d taxa written to %s" % (len(pick), a.expected))

    # ---- report --------------------------------------------------------------------------------------------
    npass = sum(v[0] for v in checks.values())
    nfail = sum(v[1] for v in checks.values())
    for k in sorted(checks):
        print("  %-45s pass %7d  fail %d" % (k, checks[k][0], checks[k][1]))
    for f in fail:
        print("  !! " + f)
    print("data_check: %d pass, %d fail" % (npass, nfail))
    sys.exit(1 if nfail else 0)


if __name__ == "__main__":
    main()
