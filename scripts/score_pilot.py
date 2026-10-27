#!/usr/bin/env python
"""score_pilot.py — score the pilot passes against the hand-labelled gold set; report cost, refusals and agreement.

usage: python scripts/score_pilot.py --gold DIR/labels --pilot OUT_A OUT_B ... --out report.md [--csv per_field.csv]
Per field: n (gold has a value), coverage (pilot gave a value), accuracy (pilot value matches gold, among both given),
false values (pilot gave a value where gold has none). Matching: exact for categories; set equality and overlap for lists;
numbers within 10 % (ages converted to days); free text by normalised containment / token overlap >= 0.5.
"""
import argparse, csv, glob, json, os, re
from collections import defaultdict


def norm(s):
    return re.sub(r"[^a-z0-9 ]", " ", str(s).lower()).split()


def txt_match(a, b):
    if a is None or b is None:
        return None
    A, B = set(norm(a)), set(norm(b))
    if not A or not B:
        return None
    if " ".join(norm(a)) in " ".join(norm(b)) or " ".join(norm(b)) in " ".join(norm(a)):
        return True
    return len(A & B) / min(len(A), len(B)) >= 0.5


UNIT = {"day": 1, "days": 1, "d": 1, "week": 7, "weeks": 7, "wk": 7, "month": 30.4375, "months": 30.4375, "mo": 30.4375,
        "year": 365.25, "years": 365.25, "y": 365.25, "yr": 365.25}


def age_days(v, unit):
    if v is None:
        return None
    m = re.search(r"\d+(\.\d+)?", str(v))
    if not m:
        return None
    u = str(unit or "").lower().strip()
    u = next((k for k in UNIT if u.startswith(k)), None) or next((k for k in UNIT if re.search(rf"\b{k}\b", str(v).lower())), "years")
    return float(m.group()) * UNIT[u]


def is_null(v):
    return v is None or (isinstance(v, str) and v.strip().lower() in ("", "null", "none", "not_reported", "not reported", "unknown", "na", "n/a", "missing", "not applicable", "unclear")) \
        or (isinstance(v, list) and not v)


class Tally:
    def __init__(self):
        self.d = defaultdict(lambda: {"n_gold": 0, "covered": 0, "correct": 0, "compared": 0, "false_value": 0, "n_pred": 0, "examples": []})

    def add(self, field, gold, pred, ok, acc=None):
        t = self.d[field]
        g, p = not is_null(gold), not is_null(pred)
        t["n_gold"] += g; t["n_pred"] += p
        if g and p:
            t["covered"] += 1
            if ok is not None:
                t["compared"] += 1; t["correct"] += bool(ok)
                if not ok and len(t["examples"]) < 6:
                    t["examples"].append(f"{acc}: gold={str(gold)[:60]} | pilot={str(pred)[:60]}")
        elif p and not g:
            t["false_value"] += 1
            if len(t["examples"]) < 6:
                t["examples"].append(f"{acc}: gold=∅ | pilot={str(pred)[:60]}")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--gold", required=True); ap.add_argument("--pilot", nargs="+", required=True)
    ap.add_argument("--out", required=True); ap.add_argument("--csv")
    a = ap.parse_args(argv)
    P = {}
    for d in a.pilot:
        for f in glob.glob(os.path.join(d, "PRJ*.json")) + glob.glob(os.path.join(d, "SRP*.json")):
            P[os.path.basename(f)[:-5]] = json.load(open(f))
    G = {json.load(open(f))["study_accession"]: json.load(open(f)) for f in glob.glob(os.path.join(a.gold, "*.json"))}
    T = Tally()
    status = defaultdict(lambda: defaultdict(int))
    agree = defaultdict(lambda: [0, 0])
    for acc, p in P.items():
        for k in ("classify_1", "classify_2", "link_audit", "fulltext", "samples"):
            v = p.get(k)
            status[k]["missing" if v is None else (v.get("status") or "ok") if isinstance(v, dict) else "ok"] += 1
        c1, c2 = p.get("classify_1") or {}, p.get("classify_2") or {}
        for f in ("in_scope", "nucleic_acid", "study_shape"):
            if c1.get(f) and c2.get(f):
                agree[f][0] += c1[f] == c2[f]; agree[f][1] += 1
        if c1.get("body_sites") and c2.get("body_sites"):
            agree["body_sites"][0] += set(c1["body_sites"]) == set(c2["body_sites"]); agree["body_sites"][1] += 1
        g = G.get(acc)
        if not g:
            continue
        c = c1 if c1.get("in_scope") else c2
        T.add("in_scope (yes/partly/no)", g.get("in_scope"), c.get("in_scope"), c.get("in_scope") == g.get("in_scope"), acc)
        gi, pi = g.get("in_scope") in ("yes", "partly"), c.get("in_scope") in ("yes", "partly")
        T.add("in_scope (in vs out)", g.get("in_scope"), c.get("in_scope"), gi == pi, acc)
        if g.get("in_scope") == "no":
            T.add("out_reason", g.get("out_reason"), c.get("out_reason"), c.get("out_reason") == g.get("out_reason"), acc)
        T.add("nucleic_acid (classify)", g.get("nucleic_acid"), c.get("nucleic_acid"), c.get("nucleic_acid") == g.get("nucleic_acid"), acc)
        ftn = (p.get("fulltext") or {}).get("nucleic_acids_sequenced")
        comb = c.get("nucleic_acid")
        if ftn in ("dna", "rna", "both") and comb in ("dna", "rna", "both") and ftn != comb:
            comb = "both" if {ftn, comb} == {"dna", "rna"} or "both" in (ftn, comb) else comb
        T.add("nucleic_acid (classify + paper)", g.get("nucleic_acid"), comb, comb == g.get("nucleic_acid"), acc)
        gs, ps = set(g.get("body_sites") or []), set(c.get("body_sites") or [])
        T.add("body_sites (exact set)", g.get("body_sites"), c.get("body_sites"), gs == ps, acc)
        T.add("body_sites (overlap)", g.get("body_sites"), c.get("body_sites"), bool(gs & ps), acc)
        T.add("study_shape", g.get("study_shape"), c.get("study_shape"), c.get("study_shape") == g.get("study_shape"), acc)
        gp = set(x for x in (g.get("processing") or []) if x != "none"); pp = set(x for x in (c.get("processing") or []) if x != "none")
        T.add("processing (set, 'none' = empty)", sorted(gp) or "none", sorted(pp) or "none", gp == pp, acc)
        # link audit
        la = {x.get("paper_id"): x.get("relation") for x in ((p.get("link_audit") or {}).get("papers") or [])}
        for gpap in g.get("papers") or []:
            pid = str(gpap.get("paper_id"))
            if pid in la:
                T.add("paper relation", gpap.get("relation"), la[pid], la[pid] == gpap.get("relation"), acc)
                T.add("paper is generating (yes/no)", gpap.get("relation"), la[pid], (la[pid] == "generated") == (gpap.get("relation") == "generated"), acc)
        # full text
        ft = p.get("fulltext") or {}
        if "status" not in ft or ft.get("status") is None:
            mp = {"application_required": "controlled", "partly_application": "partly", "on_request": "open", "open": "open", "controlled": "controlled", "partly": "partly"}.get(ft.get("metadata_access"))
            T.add("metadata_access", g.get("metadata_access"), mp, mp == g.get("metadata_access"), acc)
            gsq, psq = g.get("sequencing") or {}, ft.get("sequencing") or {}
            for f in ("platform", "read_layout", "extraction_kit", "library_kit"):
                T.add(f"seq.{f}", gsq.get(f), psq.get(f), txt_match(gsq.get(f), psq.get(f)), acc)
            hd = psq.get("host_depletion_wetlab", psq.get("host_depletion_method"))
            T.add("seq.host_depletion (wet lab)", gsq.get("host_depletion_method"), hd, txt_match(gsq.get("host_depletion_method"), hd), acc)
            gr, pr = g.get("rna_methods") or {}, ft.get("rna_methods") or {}
            for f in ("rrna_depletion", "extraction_kit", "library_kit", "dnase_treatment"):
                T.add(f"rna.{f}", gr.get(f), pr.get(f), txt_match(gr.get(f), pr.get(f)), acc)
            T.add("rna.target", gr.get("target"), pr.get("target"), None if is_null(gr.get("target")) or is_null(pr.get("target")) else gr.get("target") == pr.get("target"), acc)
        # samples
        ps_ = {str(x.get("sample")): x for x in ((p.get("samples") or {}).get("samples") or [])}
        for gsmp in g.get("samples") or []:
            q = ps_.get(str(gsmp.get("sample")))
            if q is None:
                continue
            for f in ("sex", "country"):
                gv, pv = gsmp.get(f), q.get(f)
                T.add(f"sample.{f}", gv, pv, txt_match(gv, pv), acc)
            for f in ("body_site", "sample_type"):
                gv, pv = gsmp.get(f), q.get(f)
                T.add(f"sample.{f}", gv, pv, None if is_null(gv) or is_null(pv) else str(gv).strip() == str(pv).strip(), acc)
            gd, pd_ = age_days(gsmp.get("age"), gsmp.get("age_unit")), age_days(q.get("age"), q.get("age_unit"))
            T.add("sample.age", gsmp.get("age"), q.get("age"), None if gd is None or pd_ is None else abs(gd - pd_) <= max(15, 0.1 * gd), acc)
            T.add("sample.subject_id", gsmp.get("subject_id"), q.get("subject_id"), None if is_null(gsmp.get("subject_id")) or is_null(q.get("subject_id")) else str(gsmp["subject_id"]).strip() == str(q["subject_id"]).strip(), acc)
            T.add("sample.health_condition", gsmp.get("health_condition"), q.get("health_condition"), txt_match(gsmp.get("health_condition"), q.get("health_condition")), acc)
            T.add("sample.timepoint", gsmp.get("timepoint"), q.get("timepoint"), txt_match(gsmp.get("timepoint"), q.get("timepoint")), acc)
    # cost
    cost = defaultdict(float); calls = defaultdict(int); refused = defaultdict(int)
    for d in a.pilot:
        f = os.path.join(d, "ledger.csv")
        if os.path.exists(f):
            for r in csv.DictReader(open(f)):
                cost[r["pass"]] += float(r["usd"] or 0); calls[r["pass"]] += 1; refused[r["pass"]] += r["status"] == "refused"
    L = ["# Pilot score (R2026.30 large update)", "", f"Projects run: {len(P)}; with gold labels: {len(set(P) & set(G))}", "",
         "## Cost, calls and refusals (estimated at list price)", "", "| Pass | Calls | Refused | $ |", "|---|---:|---:|---:|"]
    for k in sorted(cost):
        L.append(f"| {k} | {calls[k]} | {refused[k]} | {cost[k]:.2f} |")
    L.append(f"| **total** | {sum(calls.values())} | {sum(refused.values())} | **{sum(cost.values()):.2f}** |")
    L += ["", "## Result status per pass (all pilot projects)", ""]
    for k, v in status.items():
        L.append(f"- {k}: " + ", ".join(f"{s} {n}" for s, n in sorted(v.items(), key=lambda x: -x[1])))
    L += ["", "## Agreement of the two classification readings (all pilot projects)", ""]
    for f, (x, n) in agree.items():
        L.append(f"- {f}: {x}/{n} ({100 * x / max(1, n):.0f} %)")
    L += ["", "## Accuracy against the gold labels", "", "| Field | Gold has value | Pilot covered | Accuracy | False values |", "|---|---:|---:|---:|---:|"]
    rows = []
    for f, t in T.d.items():
        accu = f"{t['correct']}/{t['compared']} ({100 * t['correct'] / t['compared']:.0f} %)" if t["compared"] else "–"
        L.append(f"| {f} | {t['n_gold']} | {t['covered']} | {accu} | {t['false_value']} |")
        rows.append({"field": f, **{k: v for k, v in t.items() if k != "examples"}, "examples": " || ".join(t["examples"])})
    L += ["", "## Disagreements (examples)", ""]
    for f, t in T.d.items():
        if t["examples"]:
            L.append(f"**{f}**"); L += [f"- {e}" for e in t["examples"]]
    open(a.out, "w").write("\n".join(L) + "\n")
    if a.csv:
        with open(a.csv, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
    print(f"scored {len(set(P) & set(G))} gold projects; total ${sum(cost.values()):.2f}; refused {sum(refused.values())}")


if __name__ == "__main__":
    main()
