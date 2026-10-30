"""Count how often faecal-oral exposure concepts appear in the papers we already hold (PMC OA cache), by where they appear:
methods/participant sections, tables in the paper, and column headers of supplementary tables (per-sample candidates).
Deterministic keyword scan, no model calls. Output: one row per (pmcid, concept, place) hit with a short snippet.

usage: python scripts/scan_exposure_concepts.py CACHE_DIR PMCID_LIST OUT.csv
"""
import csv, glob, io, os, re, sys, zipfile
from multiprocessing import Pool
import xml.etree.ElementTree as ET

CONCEPTS = {
    "sanitation": r"latrine|toilet|sanitation|open defecation|flush toilet|sewer|septic tank|pit latrine",
    "drinking_water": r"drinking[- ]water|water source|source of (?:drinking )?water|tap water|well water|borehole|piped water|bottled water|boil(?:ed|ing) water|water treatment|surface water|river water|municipal water",
    "handwashing": r"hand[- ]?washing|hand hygiene|wash(?:ing)? hands|\bsoap\b",
    "floor_housing": r"floor (?:material|type)|(?:earth|dirt|mud|dung|cement|concrete|tiled?) floor|housing (?:material|type|condition)|roof material|wall material",
    "crowding": r"crowding|persons? per room|people per room|household size|number of (?:household members|people in the household|persons in the household)|rooms in the house",
    "siblings": r"siblings?|older (?:brothers?|sisters?)|birth order|number of children in the household",
    "daycare": r"day[- ]?care|childcare|child care|nursery school|kindergarten|crèche|creche",
    "livestock": r"livestock|cattle|poultry|chickens?|goats?|\bsheep\b|\bpigs?\b|domestic animals?|farm animals?|animal (?:contact|ownership|husbandry)|animals? in the (?:house|household|compound)",
    "pets": r"\bpets?\b|pet ownership|dog ownership|cat ownership|household (?:dogs?|cats?)|own(?:ed|s)? a (?:dog|cat)",
    "farm_living": r"farm(?:ing)? (?:families|households|children|environment|life|living)|lives? on a farm|living on a farm|farmers?\b|agricultur",
    "urban_rural": r"\brural\b|\burban\b|peri-?urban|\bvillages?\b|\bslums?\b|informal settlement",
    "deworming": r"deworm|albendazole|mebendazole|anthelmint|praziquantel|ivermectin",
    "parasite_testing": r"helminth|ascaris|trichuris|hookworm|schistosom|giardia|cryptosporid|entamoeba|kato-katz|stool microscopy|ova and parasites|taqman array|enteric pathogen",
    "diarrhoea": r"diarrh(?:o)?ea",
    "growth_eed": r"stunt(?:ing|ed)|length-for-age|height-for-age|\bLAZ\b|\bHAZ\b|environmental enteric|enteropathy|myeloperoxidase|lactulose",
    "socioeconomic": r"wealth index|socioeconomic|household income|maternal education|asset index|poverty",
    "wash_intervention": r"\bWASH\b|water, sanitation,? and hygiene",
    "travel": r"travel(?:led|ed)? (?:abroad|to|outside)|international travel|travel history",
    "breastfeeding": r"breast[- ]?fe(?:d|eding)|exclusive(?:ly)? breast",
}
RX = {k: re.compile(v, re.I) for k, v in CONCEPTS.items()}
METHODS_TITLE = re.compile(r"method|material|participant|subject|population|cohort|study design|recruit|enrol|sample collection|setting", re.I)


def text_of(el):
    return " ".join("".join(el.itertext()).split())


def hits(txt, place, pmcid, out):
    for k, rx in RX.items():
        m = rx.search(txt)
        if m:
            a = max(0, m.start() - 70)
            out.append((pmcid, k, place, txt[a:m.end() + 70].replace("\n", " ")))


def supp_headers(path):
    """first rows of tabular supplementary files (xlsx/csv/tsv/txt), as one string; '' when unreadable."""
    ext = path.lower().rsplit(".", 1)[-1]
    try:
        if ext in ("csv", "tsv", "txt"):
            with open(path, "r", errors="ignore") as f:
                return " | ".join(f.readline()[:4000] for _ in range(3))
        if ext == "xlsx":
            import openpyxl
            wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
            parts = []
            for ws in wb.worksheets[:6]:
                for i, row in enumerate(ws.iter_rows(max_row=4, values_only=True)):
                    parts.append(" | ".join(str(c) for c in row[:200] if c is not None))
            wb.close()
            return " || ".join(parts)
    except Exception:
        return ""
    return ""


def scan(pmcid):
    out, cache = [], CACHE
    folders = sorted(glob.glob(os.path.join(cache, pmcid + ".*")))
    xmlp = None
    for d in folders:
        xs = [x for x in glob.glob(os.path.join(d, "*.xml")) + glob.glob(os.path.join(d, "*.nxml"))]
        if xs:
            xmlp = xs[0]; folder = d; break
    if not xmlp:
        alt = os.path.join(cache, f"NCBI_{pmcid}.xml")
        if os.path.exists(alt):
            xmlp, folder = alt, None
        else:
            return [(pmcid, "_no_xml", "", "")]
    try:
        root = ET.parse(xmlp).getroot()
    except Exception:
        return [(pmcid, "_bad_xml", "", "")]
    body = root.find(".//body")
    if body is not None:
        for sec in body.iter("sec"):
            t = sec.find("title")
            if t is not None and METHODS_TITLE.search(text_of(t)):
                hits(text_of(sec), "methods", pmcid, out)
        hits(text_of(body), "anywhere", pmcid, out)
    for tw in root.iter("table-wrap"):
        hits(text_of(tw), "paper_table", pmcid, out)
    if folder:
        for f in glob.glob(os.path.join(folder, "*")):
            h = supp_headers(f)
            if h:
                hits(h, "supp_table_header", pmcid, out)
    out.append((pmcid, "_scanned", "", ""))
    # one row per (pmcid, concept, place)
    seen, uniq = set(), []
    for r in out:
        if (r[1], r[2]) not in seen:
            seen.add((r[1], r[2])); uniq.append(r)
    return uniq


if __name__ == "__main__":
    CACHE, lst, outp = sys.argv[1:4]
    ids = [x.strip() for x in open(lst) if x.strip()]
    with Pool(8) as p, open(outp, "w", newline="") as fo:
        w = csv.writer(fo); w.writerow(["pmcid", "concept", "place", "snippet"])
        for i, rows in enumerate(p.imap_unordered(scan, ids, chunksize=8)):
            w.writerows(rows)
            if i % 1000 == 0:
                print(i, flush=True)
else:
    CACHE = os.environ.get("PMC_CACHE", "")
