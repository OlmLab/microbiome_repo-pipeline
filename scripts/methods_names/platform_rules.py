#!/usr/bin/env python3
"""platform_rules.py -- controlled names for the sequencing-instrument field.

Deep pass stage B, Microbiome Repo catalog (release R2026.40).

Reads   platform_strings.csv   (string_id, value_raw, n_projects)
Writes  platform_map.csv       (string_id, value_raw, platform_norm,
                                platform_family, n_named, mapping_note)
        platform_vocab.csv     (platform_norm, platform_family, n_strings, n_projects)

Method: the raw string is normalised (trademark signs, dashes, thousands
separators, vendor spellings), a few shorthand enumerations are expanded,
then an ordered list of regular expressions is applied.  The first (most
specific) rule to claim a span of characters wins, so "HiSeq X Ten" is read
before "HiSeq X" before "HiSeq".  Family-level tokens ("Illumina HiSeq",
"Illumina") are dropped when a specific model of the same family was also
named.  Surviving hits are joined with ' | ' in the order written.
A hand table (OVERRIDES) carries the strings that rules cannot settle.

Read lengths, flow cells, chemistries, reagent kits and software are not
part of an instrument name and never create a controlled name.
"""

import csv
import os
import re
from collections import defaultdict

NORM_DIR = os.path.dirname(os.path.abspath(__file__))
IN_CSV = os.path.join(NORM_DIR, "platform_strings.csv")
MAP_CSV = os.path.join(NORM_DIR, "platform_map.csv")
VOC_CSV = os.path.join(NORM_DIR, "platform_vocab.csv")

ILL = "Illumina"
MGI = "MGI/BGI"
ONT = "Oxford Nanopore"
PB = "PacBio"
ION = "Ion Torrent"
R454 = "454"
SOL = "SOLiD"
ELE = "Element"
ULT = "Ultima"
OTH = "other"

UNCLEAR = "unclear"

# ---------------------------------------------------------------- normalise


def normalise(s):
    """Spelling-level clean-up; does not change which instruments are named."""
    t = s
    t = t.replace("\u2122", " ").replace("\u00ae", " ").replace("\u00a9", " ")
    for d in "\u2010\u2011\u2012\u2013\u2014\u2212":
        t = t.replace(d, "-")
    t = t.replace("\u2019", "'").replace("\u2018", "'")
    # "NovaSeqTM 6000" -> "NovaSeq 6000"
    t = re.sub(r"(?<=[a-zA-Z])TM(?=[\s\d\u00d7])", " ", t)
    # thousands separators: "HiSeq 2,500" -> "HiSeq 2500"
    t = re.sub(r"(\d),(\d{3})\b", r"\1\2", t)
    # vendor spellings
    t = re.sub(r"Nov[ao][\s\-]*[Ss][Ee][Qq]", "NovaSeq", t, flags=re.I)
    t = re.sub(r"Navo[\s\-]*[Ss]eq", "NovaSeq", t, flags=re.I)
    t = re.sub(r"\bNova(?![a-zA-Z])", "NovaSeq", t)
    t = re.sub(r"Hi[\s\-]*Seq", "HiSeq", t, flags=re.I)
    t = re.sub(r"Next[\s\-]*Seq", "NextSeq", t, flags=re.I)
    t = re.sub(r"Mini[\s\-]*Seq", "MiniSeq", t, flags=re.I)
    t = re.sub(r"\bMi[\s\-]*Seq", "MiSeq", t, flags=re.I)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def expand(t):
    """Expand shorthand enumerations into explicit instrument names."""
    # "NextSeq 500/550" inside a kit name is a kit, not two instruments
    t = re.sub(
        r"NextSeq\s*500\s*/\s*550(?=.{0,30}(?:kit|Kit|output|Output|OUTPUT))",
        "NextSeq",
        t,
    )
    t = re.sub(r"NextSeq\s*500\s*/\s*550", "NextSeq 500 and NextSeq 550", t)
    t = re.sub(r"NextSeq\s*1000\s*/\s*2000", "NextSeq 1000 and NextSeq 2000", t)
    t = re.sub(r"NovaSeq\s*6000\s*/\s*X\s*\+", "NovaSeq 6000 and NovaSeq X Plus", t)
    t = re.sub(r"MGI\s*200\s*/\s*2000", "MGISEQ-200 and MGISEQ-2000", t)
    # "HiSeq 2500, 4000", "HiSeq 2000 or 2500", "HiSeq2000/4000"
    prev = None
    while prev != t:
        prev = t
        t = re.sub(
            r"HiSeq\s*(\d{4})\s*(?:,\s*|\s+and\s+|\s+or\s+|\s*/\s*)(\d{4})\b",
            r"HiSeq \1 and HiSeq \2",
            t,
        )
    return t


# -------------------------------------------------------------------- rules
# (regex, controlled name, family, sub-family key, level, note)
# level: ''  specific model
#        'sub'     family-level token, dropped if a specific model of the
#                  same sub-family is also named
#        'vendor'  maker-level token, dropped if any instrument of that
#                  maker is also named

RULES_RAW = [
    # ---- Illumina HiSeq X ------------------------------------------------
    (r"HiSeq[\s\-]*X[\s\-]*(?:Ten|TEN|ten|10)\b", "HiSeq X Ten", ILL, "hiseq", "", ""),
    (r"HiSeq[\s\-]*X[\s\-]*(?:Five|five|5)\b", "HiSeq X Five", ILL, "hiseq", "", ""),
    (r"Illumina[\s\-]*X[\s\-]*(?:Ten|ten|TEN|10)\b", "HiSeq X Ten", ILL, "hiseq", "", ""),
    (r"\bX[\s\-]*(?:Ten|ten|TEN)\b", "HiSeq X Ten", ILL, "hiseq", "", ""),
    (r"HiSeq[\s\-]*X\b", "Illumina HiSeq X", ILL, "hiseq", "", ""),
    # ---- Illumina HiSeq numbered ----------------------------------------
    (r"HiSeq[\s\.\-]*1000(?!\d)", "Illumina HiSeq 1000", ILL, "hiseq", "", ""),
    (r"HiSeq[\s\.\-]*1500(?!\d)", "Illumina HiSeq 1500", ILL, "hiseq", "", ""),
    (r"HiSeq[\s\.\-]*2000(?!\d)", "Illumina HiSeq 2000", ILL, "hiseq", "", ""),
    (r"HiSeq[\s\.\-]*2500(?!\d)", "Illumina HiSeq 2500", ILL, "hiseq", "", ""),
    (r"HiSeq[\s\.\-]*3000(?!\d)", "Illumina HiSeq 3000", ILL, "hiseq", "", ""),
    (r"HiSeq[\s\.\-]*4000(?!\d)", "Illumina HiSeq 4000", ILL, "hiseq", "", ""),
    (r"HiSeq[\s\.\-]*4K\b", "Illumina HiSeq 4000", ILL, "hiseq", "",
     "'HiSeq 4K' read as HiSeq 4000"),
    (r"HiSeq[\s\.\-]*400\b(?!\d)", "Illumina HiSeq 4000", ILL, "hiseq", "",
     "'HiSeq 400' read as HiSeq 4000"),
    (r"HiSeq[\s\.\-]*6000(?!\d)", "Illumina HiSeq (model not specified)", ILL, "hiseq",
     "sub", "no HiSeq 6000 exists; may be NovaSeq 6000"),
    (r"HiScan[\s\-]*SQ", "Illumina HiScanSQ", ILL, "hiscan", "", ""),
    (r"HiSeq", "Illumina HiSeq (model not specified)", ILL, "hiseq", "sub", ""),
    # ---- Illumina NovaSeq -------------------------------------------------
    (r"NovaSeq[\s\-]*X[\s\-]*(?:Plus|PLUS|plus|\+)", "Illumina NovaSeq X Plus", ILL,
     "novaseq", "", ""),
    (r"NovaSeq[\s\.\-]*6000\s*(?:DX|Dx|dx)\b", "Illumina NovaSeq 6000Dx", ILL,
     "novaseq", "", ""),
    (r"NovaSeq[\s\.\-]*6000(?!\d)", "Illumina NovaSeq 6000", ILL, "novaseq", "", ""),
    (r"NovaSeq[\s\.\-]*600\b(?!\d)", "Illumina NovaSeq 6000", ILL, "novaseq", "",
     "'NovaSeq 600' read as NovaSeq 6000"),
    (r"NovaSeq[\s\-]*X\b(?!\s*\d)", "Illumina NovaSeq X", ILL, "novaseq", "", ""),
    (r"NovaSeq", "Illumina NovaSeq (model not specified)", ILL, "novaseq", "sub", ""),
    # ---- Illumina NextSeq -------------------------------------------------
    (r"NextSeq[\s\.\-]*(?:550\s*(?:Dx|DX|dx)|Dx\s*550|DX\s*550)",
     "NextSeq 550Dx", ILL, "nextseq", "", ""),
    (r"NextSeq[\s\.\-]*CN\s*500", "NextSeq CN500", ILL, "nextseq", "", ""),
    (r"NextSeq[\s\.\-]*CN\s*550", "NextSeq CN550", ILL, "nextseq", "", ""),
    (r"NextSeq[\s\.\-]*500(?!\d)", "NextSeq 500", ILL, "nextseq", "", ""),
    (r"NextSeq[\s\.\-]*550(?!\d)", "NextSeq 550", ILL, "nextseq", "", ""),
    (r"NextSeq[\s\.\-]*1000(?!\d)", "NextSeq 1000", ILL, "nextseq", "", ""),
    (r"NextSeq[\s\.\-]*2000(?!\d)", "NextSeq 2000", ILL, "nextseq", "", ""),
    (r"NextSeq[\s\.\-]*2K\b", "NextSeq 2000", ILL, "nextseq", "",
     "'NextSeq 2K' read as NextSeq 2000"),
    (r"NextSeq", "Illumina NextSeq (model not specified)", ILL, "nextseq", "sub", ""),
    # ---- other Illumina ---------------------------------------------------
    (r"MiSeq\s*(?:DX|Dx)\b", "Illumina MiSeq Dx", ILL, "miseq", "", ""),
    (r"MiSeq", "Illumina MiSeq", ILL, "miseq", "", ""),
    (r"MiniSeq", "Illumina MiniSeq", ILL, "miniseq", "", ""),
    (r"iSeq[\s\-]*100", "Illumina iSeq 100", ILL, "iseq", "", ""),
    (r"(?:Genome\s*Analy[sz]\w*|GA)[\s\-]*IIx", "Illumina Genome Analyzer IIx", ILL,
     "ga", "", ""),
    (r"Genome\s*Analy[sz]\w*[\s\-]*II\b", "Illumina Genome Analyzer II", ILL, "ga",
     "", ""),
    (r"Genome\s*Analy[sz]\w*", "Illumina Genome Analyzer", ILL, "ga", "", ""),
    # ---- MGI / BGI --------------------------------------------------------
    (r"DNBSEQ[\s\-]*G400\s*T10", "DNBSEQ-G400", MGI, "mgi", "",
     "written 'DNBSEQ-G400T10'"),
    (r"DNBSEQ[\s\-]*G400\s*RS", "DNBSEQ-G400", MGI, "mgi", "",
     "written 'DNBSEQ-G400RS'"),
    (r"DNBSEQ[\s\-]*G400", "DNBSEQ-G400", MGI, "mgi", "", ""),
    (r"DNBSEQ[\s\-]*G50(?!\d)", "DNBSEQ-G50", MGI, "mgi", "", ""),
    (r"DNBSEQ[\s\-]*T7\s*RS", "DNBSEQ-T7", MGI, "mgi", "", "written 'DNBSEQ-T7RS'"),
    (r"DNBSEQ[\s\-]*T7", "DNBSEQ-T7", MGI, "mgi", "", ""),
    (r"MG[IE]SEQ[\s\-]*T7", "DNBSEQ-T7", MGI, "mgi", "",
     "written 'MGISEQ-T7' (renamed DNBSEQ-T7)"),
    (r"MGISEQ[\s\-]*2000\s*RS", "MGISEQ-2000RS", MGI, "mgi", "", ""),
    (r"MGISEQ[\s\-]*2000", "MGISEQ-2000", MGI, "mgi", "", ""),
    (r"BGISEQ[\s\-]*2000", "MGISEQ-2000", MGI, "mgi", "",
     "written 'BGISEQ-2000'; no such model"),
    (r"MGISEQ[\s\-]*200\s*RS", "MGISEQ-200", MGI, "mgi", "", "written 'MGISEQ-200RS'"),
    (r"MGISEQ[\s\-]*200(?!\d)", "MGISEQ-200", MGI, "mgi", "", ""),
    (r"MGISEQ\s*2000", "MGISEQ-2000", MGI, "mgi", "", ""),
    (r"MGI[\s\-]*200(?!\d)", "MGISEQ-200", MGI, "mgi", "", ""),
    (r"mgiseo[\s\-]*200", "MGISEQ-200", MGI, "mgi", "", "spelling 'mgiseo-200'"),
    (r"BGI[\s\-]*(?:SEQ|EQ|Seq)?[\s\-]*500\s*RS", "BGISEQ-500", MGI, "mgi", "",
     "written 'BGISEQ-500RS'"),
    (r"BGI[\s\-]*(?:SEQ|EQ|Seq)?[\s\-]*500", "BGISEQ-500", MGI, "mgi", "", ""),
    (r"BGISEQ[\s\-]*100", "BGISEQ-100", MGI, "mgi", "", ""),
    (r"BGISEQ[\s\-]*200(?!\d)", "BGISEQ-200", MGI, "mgi", "", ""),
    (r"BGISEQ[\s\-]*50(?!\d)", "BGISEQ-50", MGI, "mgi", "", ""),
    (r"DNBSEQ|DNBseq|BGISEQ|BGISeq|MGISEQ|MGIseq|BGI\s*platform|MGI\s*platform|"
     r"MGI\s*DNBSEQ|DNA\s*nanoball",
     "MGI/BGI DNBSEQ (model not specified)", MGI, "mgi", "vendor", ""),
    (r"\bMGI\b|\bBGI\b", "MGI/BGI DNBSEQ (model not specified)", MGI, "mgi",
     "vendor", ""),
    # ---- Oxford Nanopore --------------------------------------------------
    (r"MinION|Min\s*ION", "MinION", ONT, "ont", "", ""),
    (r"GridION", "GridION", ONT, "ont", "", ""),
    (r"Promethion|PromethION", "PromethION", ONT, "ont", "", ""),
    (r"Oxford\s*Nanopore|\bONT\b|[Nn]anopore", "Oxford Nanopore (model not specified)",
     ONT, "ont", "vendor", ""),
    # ---- PacBio -----------------------------------------------------------
    (r"Sequel\s*IIe", "Sequel IIe", PB, "pacbio", "", ""),
    (r"Sequel\s*II\b", "Sequel II", PB, "pacbio", "", ""),
    (r"Sequel", "Sequel", PB, "pacbio", "", ""),
    (r"Revio", "Revio", PB, "pacbio", "", ""),
    (r"\bRS\s*II\b", "PacBio RS II", PB, "pacbio", "", ""),
    (r"\bOnso\b", "Onso", PB, "pacbio", "", ""),
    (r"PacBio|Pacific\s*Biosciences", "PacBio (model not specified)", PB, "pacbio",
     "vendor", ""),
    # ---- Ion Torrent ------------------------------------------------------
    (r"Ion[\s\-]*(?:Torrent[\s\-]*)?Gene\s*Studio[\s\-]*S5\s*(?:XL|xl)",
     "Ion Torrent S5 XL", ION, "ion", "", "written 'GeneStudio S5XL'"),
    (r"Ion[\s\-]*(?:Torrent[\s\-]*)?Gene\s*Studio", "Ion GeneStudio S5", ION, "ion",
     "", ""),
    (r"Ion[\s\-]*(?:Torrent[\s\-]*)?S5\s*(?:XL|xl)", "Ion Torrent S5 XL", ION, "ion",
     "", ""),
    (r"Ion[\s\-]*(?:Torrent[\s\-]*)?S5\b", "Ion Torrent S5", ION, "ion", "", ""),
    (r"[Ii]on[\s\-]*[Pp]roton", "Ion Torrent Proton", ION, "ion", "", ""),
    (r"\bPGM\b|Personal\s*Genome\s*Machine", "Ion Torrent PGM", ION, "ion", "", ""),
    (r"Ion[\s\-]*Torrent", "Ion Torrent (model not specified)", ION, "ion", "vendor",
     ""),
    # ---- Element / Ultima -------------------------------------------------
    (r"AVITI\s*24", "AVITI 24", ELE, "element", "", ""),
    (r"AVITI", "Element AVITI", ELE, "element", "", ""),
    (r"UG\s*100", "UG 100", ULT, "ultima", "", ""),
    # ---- Illumina, maker only --------------------------------------------
    (r"[Ii]ll?umina|Illuemina", "Illumina (model not specified)", ILL, "illumina",
     "vendor", ""),
]

RULES = [
    (re.compile(rx), canon, fam, sub, lvl, note)
    for rx, canon, fam, sub, lvl, note in RULES_RAW
]

CANON_FAMILY = {}
for _rx, _c, _f, _s, _l, _n in RULES_RAW:
    CANON_FAMILY.setdefault(_c, _f)

# ------------------------------------------------------------- hand table
# value_raw -> (platform_norm, mapping_note).  Strings the ordered rules
# cannot settle: 454/SOLiD wording, assay platforms that name no sequencer,
# and a handful of misspelt or impossible model numbers.
OVERRIDES = {
    "454": ("454 (model not specified)", ""),
    "454 FLX pyrosequencer": ("454 GS FLX", ""),
    "454 Life Sciences Genome Sequencer FLX with GS FLX Titanium series reagents":
        ("454 GS FLX Titanium", ""),
    "454 Life Sciences genome sequencer FLX instrument, titanium (Ti) series reagents":
        ("454 GS FLX Titanium", ""),
    "454 pyrosequencing Genome Sequencer FLX titanium plus":
        ("454 GS FLX+", "'FLX titanium plus' read as GS FLX+"),
    "454 GS FLX Titanium and FLX+ (Roche), Ion PGM and Ion Proton (Life Technologies), and MiSeq (Illumina)":
        ("454 GS FLX Titanium | 454 GS FLX+ | Ion Torrent PGM | Ion Torrent Proton | Illumina MiSeq",
         ""),
    "GS FLX (454 Life Sciences)": ("454 GS FLX", ""),
    "GS-FLX Titanium chemistry system": ("454 GS FLX Titanium", ""),
    "Roche 454 GS FLX": ("454 GS FLX", ""),
    "Roche GS FLX sequencer and Titanium chemistry": ("454 GS FLX Titanium", ""),
    "Roche 454 GS FLX Titanium or Illumina GAIIX":
        ("454 GS FLX Titanium | Illumina Genome Analyzer IIx", ""),
    "Roche\u00ae 454 titanium chemistry":
        ("454 GS FLX Titanium", "titanium chemistry read as GS FLX Titanium"),
    "pyrosequencing": (UNCLEAR, "no instrument named"),
    "SOLiD": ("SOLiD (model not specified)", ""),
    "SOLiD 5500xl": ("AB 5500xl Genetic Analyzer", "written 'SOLiD 5500xl'"),
    "SOLiD 5500xl Wildfire":
        ("AB 5500xl Genetic Analyzer", "written 'SOLiD 5500xl Wildfire'"),
    "SOLiD 5500 W": ("AB 5500 Genetic Analyzer", "written 'SOLiD 5500 W' (Wildfire)"),
    "SOLiD 4, SOLiD 5500 and Ion Torrent":
        ("AB SOLiD 4 System | AB 5500 Genetic Analyzer | Ion Torrent (model not specified)",
         ""),
    "MGISP-960 (MGI, Shenzhen, China)":
        (UNCLEAR, "MGISP-960 is a library-prep robot, not a sequencer"),
    "Dif seq platform": (UNCLEAR, "Dif-seq is an assay platform; no sequencer named"),
    "Dif seq platform (Dinfectome Medical Technology Inc, Nanjing, China)":
        (UNCLEAR, "Dif-seq is an assay platform; no sequencer named"),
    "iDTECT\u00ae Dx Blood v1\u00ae platform (PathoQuest, Paris, France)":
        (UNCLEAR, "iDTECT Dx Blood is an assay, not a sequencer"),
    "CBTSeq2 Pro platform (CapitalBio Technology, Chengdu, China)":
        ("CBTSeq2 Pro", "CapitalBio instrument; not in archive vocabulary"),
    "Thermo Fisher Scientific (DA8600), Illumina NextSeq 550, MGI 200/2000, and Q-mNGS\u21223.0":
        ("DA8600 | NextSeq 550 | MGISEQ-200 | MGISEQ-2000",
         "Q-mNGS 3.0 is an assay, not an instrument"),
    "Beijing Genomics Institute sequencer-100": ("BGISEQ-100", ""),
    "Element Biosciences AVITI24": ("AVITI 24", ""),
    "AVITI LT Instrument (Element Biosciences)":
        ("Element AVITI", "written 'AVITI LT'"),
    "Ion Torrent GeneStudio S5XL (Thermo Fisher Scientific, Waltham, MA, USA)":
        ("Ion Torrent S5 XL", "written 'GeneStudio S5XL'"),
    "Illumina 2500": ("Illumina HiSeq 2500", "'Illumina 2500' read as HiSeq 2500"),
    "Illumina 550 platform": ("NextSeq 550", "'Illumina 550' read as NextSeq 550"),
    "Illumina NovaSeq 2000":
        ("Illumina NovaSeq (model not specified)",
         "no NovaSeq 2000 exists; may be NextSeq 2000"),
    "Illumina HiSeq 1T":
        ("Illumina HiSeq (model not specified)", "'1T' is not a model"),
    "NextSeq 2500 platform (Illumina)":
        ("Illumina NextSeq (model not specified)", "no NextSeq 2500 exists"),
    "Illumina NovaSeq XP":
        ("Illumina NovaSeq (model not specified)",
         "'XP' is the XP loading workflow, not a model"),
    "Novaseq S6000": ("Illumina NovaSeq 6000", "written 'Novaseq S6000'"),
    "NovaSeq S4 / NovaSeq X":
        ("Illumina NovaSeq 6000 | Illumina NovaSeq X",
         "S4 flow cell read as NovaSeq 6000"),
    "BGI MGISEQ2000 and Illumina MiSeq 3000 sequencers":
        ("MGISEQ-2000 | Illumina MiSeq", "'MiSeq 3000' written; no such model"),
    "Illumina MiSeq 500 platform (2 \u00d7 150bp)":
        ("Illumina MiSeq", "'MiSeq 500' is the 500-cycle kit"),
    "GridION using FLO-MIN106D (MinION)":
        ("GridION", "'MinION' here names the flow cell type"),
    "FLO-MIN114 flowcell (R10.4.1 chemistry)":
        ("Oxford Nanopore (model not specified)",
         "FLO-MIN114 is an ONT flow cell; device not named"),
    "R9 flow cells":
        ("Oxford Nanopore (model not specified)", "flow cell only; device not named"),
    "R9.4.1 flow cell connected to MInIT device, MinKNOW":
        ("Oxford Nanopore (model not specified)",
         "MinIT is a compute unit; sequencer not named"),
    "MiSeq Reporter v2.3 (Illumina)":
        ("Illumina MiSeq", "MiSeq Reporter is software; instrument implied"),
    "Illumina NovaSeq 6000 and BGI BGI500":
        ("Illumina NovaSeq 6000 | BGISEQ-500", "written 'BGI500'"),
}

EXTRA_FAMILY = {
    "454 (model not specified)": R454,
    "454 GS FLX": R454,
    "454 GS FLX Titanium": R454,
    "454 GS FLX+": R454,
    "SOLiD (model not specified)": SOL,
    "AB 5500xl Genetic Analyzer": SOL,
    "AB 5500 Genetic Analyzer": SOL,
    "AB SOLiD 4 System": SOL,
    "CBTSeq2 Pro": OTH,
    "DA8600": OTH,
    UNCLEAR: OTH,
}
CANON_FAMILY.update(EXTRA_FAMILY)


# ------------------------------------------------------------------ mapping


def find_instruments(text):
    """Return [(canon, family, note)] in the order the instruments are written."""
    taken = []
    hits = []
    for rx, canon, fam, sub, lvl, note in RULES:
        for m in rx.finditer(text):
            s, e = m.span()
            if any(not (e <= a or s >= b) for a, b in taken):
                continue
            taken.append((s, e))
            hits.append({"pos": s, "canon": canon, "fam": fam, "sub": sub,
                         "lvl": lvl, "note": note})
    hits.sort(key=lambda h: h["pos"])

    specific_subs = {h["sub"] for h in hits if h["lvl"] == ""}
    fams_specific = {h["fam"] for h in hits if h["lvl"] != "vendor"}
    kept = []
    for h in hits:
        if h["lvl"] == "sub" and h["sub"] in specific_subs:
            continue
        if h["lvl"] == "vendor" and h["fam"] in fams_specific:
            continue
        kept.append(h)

    out = []
    seen = set()
    for h in kept:
        if h["canon"] in seen:
            continue
        seen.add(h["canon"])
        out.append((h["canon"], h["fam"], h["note"]))
    return out


def family_of(names):
    fams = {CANON_FAMILY.get(n, OTH) for n in names}
    if len(fams) == 1:
        return fams.pop()
    return OTH


def map_string(value_raw):
    if value_raw in OVERRIDES:
        norm, note = OVERRIDES[value_raw]
        names = norm.split(" | ")
        if norm == UNCLEAR:
            return UNCLEAR, OTH, 0, note
        return norm, family_of(names), len(names), note

    text = expand(normalise(value_raw))
    hits = find_instruments(text)
    if not hits:
        return UNCLEAR, OTH, 0, "no instrument named"
    names = [h[0] for h in hits]
    notes = []
    for _c, _f, n in hits:
        if n and n not in notes:
            notes.append(n)
    note = "; ".join(notes)[:120]
    return " | ".join(names), family_of(names), len(names), note


# -------------------------------------------------------------------- main


def main():
    with open(IN_CSV, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))

    out_rows = []
    for r in rows:
        norm, fam, n_named, note = map_string(r["value_raw"])
        out_rows.append({
            "string_id": r["string_id"],
            "value_raw": r["value_raw"],
            "platform_norm": norm,
            "platform_family": fam,
            "n_named": n_named,
            "mapping_note": note,
        })

    with open(MAP_CSV, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["string_id", "value_raw", "platform_norm",
                                           "platform_family", "n_named",
                                           "mapping_note"])
        w.writeheader()
        w.writerows(out_rows)

    agg = defaultdict(lambda: {"n_strings": 0, "n_projects": 0, "family": OTH})
    for src, out in zip(rows, out_rows):
        a = agg[out["platform_norm"]]
        a["n_strings"] += 1
        a["n_projects"] += int(src["n_projects"])
        a["family"] = out["platform_family"]
    voc = sorted(agg.items(), key=lambda kv: (-kv[1]["n_projects"], kv[0]))

    with open(VOC_CSV, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["platform_norm", "platform_family", "n_strings", "n_projects"])
        for name, a in voc:
            w.writerow([name, a["family"], a["n_strings"], a["n_projects"]])

    return out_rows, voc


if __name__ == "__main__":
    _rows, _voc = main()
    print(len(_rows), "strings ->", len(_voc), "controlled names")
