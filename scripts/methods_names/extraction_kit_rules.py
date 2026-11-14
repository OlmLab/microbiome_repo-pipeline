#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Controlled names for the DNA/RNA extraction kit / method field (deep pass stage B).

Reads  extraction_kit_strings.csv   (string_id, value_raw, n_projects)
Writes extraction_kit_map.csv       (string_id, value_raw, extraction_kit_norm,
                                     maker, n_named, mapping_note)
       extraction_kit_vocab.csv     (extraction_kit_norm, maker, n_strings, n_projects)

Controlled name = '<Maker> <product name as the maker writes it>', no catalogue
numbers, no city/country, no trademark signs.  Non-kit methods get a method name.
Strings naming two or more kits list them all, separated by ' | '.
Strings that name no kit or method get 'unclear'.

Product-name form decides the maker for the MO BIO / Qiagen succession, e.g.
'PowerSoil DNA Isolation Kit' -> MO BIO even when the paper cites Qiagen as the
supplier, 'DNeasy PowerSoil Kit' -> Qiagen even when the paper cites MO BIO.
"""

import csv
import os
import re
import unicodedata
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
IN_CSV = os.path.join(HERE, "extraction_kit_strings.csv")
MAP_CSV = os.path.join(HERE, "extraction_kit_map.csv")
VOCAB_CSV = os.path.join(HERE, "extraction_kit_vocab.csv")

UNCLEAR = "unclear"

# --------------------------------------------------------------------------- #
# normalisation
# --------------------------------------------------------------------------- #

TM_WORDS = ("zymobiomics|hostzero|masterpure|purelink|magmax|mirvana|powermag|"
            "fastdna|quick dna|allprep|ribopure|instagene|recoverall|ezna|"
            "invitrogen|maxwell|dneasy|rneasy|qiaamp|nucleospin|mag bind|"
            "molpure|magicpure|pathoxtract|hiyield|azul|cretmag|ngsmaster|"
            "fastpure|zr|tianamp|indispin|nuclisens|easymag|spineasy|exgene|"
            "psp|echolution|danagene|magabind|omnigene")


def norm(s):
    """Lower-case, sign-free, dash-free matching form of a raw string."""
    t = unicodedata.normalize("NFKC", s)
    for ch in "\u00ae\u2122\u00a9\u2120":
        t = t.replace(ch, " ")
    for ch in "\u2010\u2011\u2012\u2013\u2014\u2015\u2212":
        t = t.replace(ch, "-")
    t = t.replace("\u2019", "'").replace("\u2018", "'")
    t = t.lower()
    # E.Z.N.A. -> ezna  (before the dot/dash squashing)
    t = re.sub(r"e\s*\.?\s*z\s*\.?\s*n\s*\.?\s*a\s*\.?", "ezna", t)
    t = t.replace("-", " ")
    t = re.sub(r"\b(%s)tm\b" % TM_WORDS, r"\1", t)
    t = re.sub(r"[,;:()\[\]#]", " ", t)
    # spelling / brand variants
    reps = [
        ("pure link", "purelink"), ("all prep", "allprep"),
        ("quiagen", "qiagen"), ("qiagene", "qiagen"), ("qiagen's", "qiagen"),
        ("qia amp", "qiaamp"), ("qiamp", "qiaamp"), ("qiaamp", "qiaamp"),
        ("qiaaamp", "qiaamp"),
        ("dnaeasy", "dneasy"), ("dna easy", "dneasy"),
        ("rnaeasy", "rneasy"), ("rna easy", "rneasy"),
        ("dneasypowersoil", "dneasy powersoil"),
        ("nucleospinsoil", "nucleospin soil"),
        ("magnapure", "magna pure"), ("magna pure", "magna pure"),
        ("power soil", "powersoil"), ("power  soil", "powersoil"),
        ("power fecal", "powerfecal"), ("power faecal", "powerfecal"),
        ("powerfaecal", "powerfecal"), ("faecal", "fecal"), ("faeces", "feces"),
        ("power lyzer", "powerlyzer"), ("powerlyser", "powerlyzer"),
        ("power biofilm", "powerbiofilm"), ("power microbiome", "powermicrobiome"),
        ("power mag", "powermag"), ("power max", "powermax"),
        ("mo bio", "mobio"), ("mobio", "mobio"), ("mo  bio", "mobio"),
        ("minikit", "mini kit"), ("mini kits", "mini kit"),
        ("mini prep", "miniprep"), ("micro prep", "microprep"),
        ("mini  prep", "miniprep"),
        ("zymo research", "zymo"), ("zymoresearch", "zymo"),
        ("mp biomedicals", "mpbio"), ("mp bio", "mpbio"), ("mpbiomedicals", "mpbio"),
        ("macherey nagel", "macherey nagel"), ("machery nagel", "macherey nagel"),
        ("omega bio tek", "omega"), ("omega biotek", "omega"),
        ("omega bio tech", "omega"), ("omega biotech", "omega"),
        ("perkin elmer", "perkinelmer"),
        ("biomerieux", "biomerieux"),
    ]
    for a, b in reps:
        t = t.replace(a, b)
    t = re.sub(r"\s+", " ", t).strip()
    return t


# --------------------------------------------------------------------------- #
# makers
# --------------------------------------------------------------------------- #

MAKER_PREFIXES = [
    "Nanjing Practice Medicine Diagnostics", "Thermo Fisher Scientific",
    "Precision System Science", "Indical Bioscience", "AMS Biotechnology",
    "Jumpcode Genomics", "Beckman Coulter", "TransGen Biotech",
    "Lybe Scientific", "Macherey-Nagel", "Hangzhou Guhe", "Sigma-Aldrich",
    "Zymo Research", "Omega Bio-tek", "MP Biomedicals", "Norgen Biotek",
    "DNA Genotek", "CapitalBio", "Fuzhou OJX", "Longseegen", "bioMérieux",
    "PerkinElmer", "DAAN Gene", "IngeniGen", "Takara Bio", "Bio-Rad",
    "Invitrogen", "WillingMed", "Danagene", "Takegene", "ALFA-SEQ",
    "GeneAll", "Favorgen", "Zinexts", "NorDiag", "Bioline", "AccuGen",
    "Magigene", "Genskey", "MatriDx", "CretMag", "Durviz", "Canvax",
    "TANBead", "Epicentre", "Lucigen", "Citomed", "BioEcho", "Kurabo",
    "Syntol", "Stratec", "Molzym", "Promega", "Tiangen", "BioTeke",
    "Vazyme", "Yeasen", "iNtRON", "Jinshi", "Abbott", "Lexogen", "Dxome",
    "CWBIO", "Qiagen", "MO BIO", "Roche", "Magen", "Zymo", "Omega",
    "LGC", "MGI",
]


def maker_of(name):
    if name == UNCLEAR:
        return ""
    for p in MAKER_PREFIXES:
        if name == p or name.startswith(p + " "):
            return p
    return ""


# --------------------------------------------------------------------------- #
# strings naming two or more kits (keyed by string_id)
# --------------------------------------------------------------------------- #

MULTI = {
    112: (["MP Biomedicals FastDNA SPIN Kit",
           "Promega Maxwell RSC PureFood GMO and Authentication Kit"], ""),
    223: (["Qiagen AllPrep DNA/RNA Micro Kit",
           "Qiagen QIAamp DNA FFPE Tissue Kit",
           "Qiagen DNeasy PowerSoil Kit"], ""),
    237: (["Qiagen AllPrep PowerFecal DNA/RNA 96 Kit",
           "Promega Maxwell HT 96 gDNA Blood Isolation System"], ""),
    238: (["Qiagen AllPrep PowerFecal DNA/RNA Kit",
           "Qiagen DNeasy PowerSoil Pro Kit"], ""),
    243: (["Qiagen AllPrep PowerViral DNA/RNA Kit",
           "Invitrogen PureLink Microbiome DNA Purification Kit",
           "Qiagen QIAamp DNA Mini Kit",
           "Qiagen QIAamp Fast DNA Stool Mini Kit",
           "Zymo Research ZymoBIOMICS DNA Miniprep Kit"], ""),
    267: (["Promega DNA IQ System", "Qiagen QIAamp DNA Stool Mini Kit"], ""),
    301: (["Qiagen DNeasy Blood & Tissue Kit",
           "Thermo Fisher Scientific TRIzol LS"], ""),
    306: (["Qiagen DNeasy PowerBiofilm Kit", "Qiagen DNeasy Blood & Tissue Kit"],
          "PowerBiofilm for skin/oral swabs, Blood & Tissue for faeces"),
    309: (["Qiagen DNeasy PowerSoil Kit", "Qiagen QIAamp UCP Pathogen Mini Kit"], ""),
    344: (["Qiagen DNeasy PowerSoil Kit", "Qiagen QIAamp DNA Microbiome Kit"], ""),
    394: (["Qiagen EZ1 Virus Mini Kit v2.0",
           "Promega Maxwell RSC ccfDNA Plasma Kit"], ""),
    404: (["GeneAll Exgene Clinic SV Mini Kit",
           "Qiagen QIAamp Fast DNA Stool Mini Kit"], ""),
    511: (["Thermo Fisher Scientific MICROBEnrich Kit",
           "Thermo Fisher Scientific MICROBExpress Kit"],
          "mRNA enrichment kits, not extraction kits"),
    517: (["MO BIO PowerSoil DNA Isolation Kit",
           "MO BIO PowerMax Soil DNA Isolation Kit"], ""),
    537: (["Omega Bio-tek Mag-Bind Universal Metagenomics Kit",
           "Qiagen DNeasy PowerSoil Kit"], ""),
    538: (["Omega Bio-tek Mag-Bind Universal Metagenomics Kit",
           "Qiagen DNeasy PowerSoil Kit"], ""),
    543: (["Qiagen MagAttract PowerMicrobiome DNA/RNA Kit",
           "Qiagen AllPrep PowerFecal Pro DNA/RNA Kit",
           "Thermo Fisher Scientific MagMAX-96 Viral RNA Isolation Kit"], ""),
    550: (["Qiagen MagAttract PowerSoil DNA Kit",
           "Thermo Fisher Scientific MagMAX Microbiome Ultra Nucleic Acid "
           "Isolation Kit"], ""),
    604: (["Tiangen Magnetic Soil and Stool DNA Kit",
           "Omega Bio-tek E.Z.N.A. Soil DNA Kit"],
          "paper attributes the Magnetic Soil and Stool Kit to Qiagen; it is a "
          "Tiangen product"),
    721: (["Macherey-Nagel NucleoSpin Virus Kit",
           "Zymo Research Quick-DNA/RNA Miniprep Plus Kit"], ""),
    722: (["Macherey-Nagel NucleoSpin cfDNA XS Kit",
           "Qiagen QIAamp UCP Pathogen Mini Kit"], ""),
    736: (["bioMérieux eMAG", "Roche MagNA Pure 24",
           "Qiagen QIAamp Viral RNA Mini Kit"], ""),
    754: (["Stratec PSP Spin Stool DNA Kit",
           "Stratec PSP Spin Stool DNA Plus Kit"], ""),
    846: (["MO BIO PowerSoil DNA Isolation Kit",
           "MO BIO PowerSoil-htp 96 Well DNA Isolation Kit"], ""),
    876: (["Invitrogen PureLink Viral RNA/DNA Mini Kit",
           "Qiagen QIAamp Viral RNA Mini Kit"], ""),
    916: (["Qiagen QIAsymphony", "bioMérieux NucliSENS easyMAG"], ""),
    924: (["Qiagen QIAamp Circulating Nucleic Acid Kit",
           "Qiagen DNeasy Blood & Tissue Kit"], ""),
    925: (["Qiagen QIAamp DNA Blood Mini Kit",
           "Qiagen QIAamp PowerFecal Pro DNA Kit"], ""),
    939: (["Qiagen QIAamp DNA Microbiome Kit",
           "Omega Bio-tek E.Z.N.A. MicroElute Genomic DNA Kit"], ""),
    947: (["Qiagen QIAamp DNA Mini Kit",
           "Qiagen QIAamp UCP Pathogen Mini Kit"], ""),
    950: (["Qiagen QIAamp DNA Mini Kit",
           "Qiagen QIAsymphony DSP Virus/Pathogen Kit",
           "bioMérieux NucliSENS easyMAG"], ""),
    953: (["Qiagen QIAamp DNA Mini Kit",
           "Qiagen QIAamp DNA Blood Mini Kit"], ""),
    954: (["Qiagen QIAamp DNA Mini Kit",
           "Qiagen QIAamp DNA Blood Mini Kit"], "cites the kit handbook"),
    957: (["Qiagen QIAamp DNA Mini Kit",
           "Qiagen QIAamp PowerFecal Pro DNA Kit",
           "Promega Maxwell RSC Cultured Cells DNA Kit",
           "Promega Maxwell RSC Buccal Swab DNA Kit"], ""),
    1001: (["Qiagen QIAamp Fast DNA Stool Mini Kit",
            "Qiagen QIAamp DNA Mini Kit"], ""),
    1002: (["Qiagen QIAamp Fast DNA Stool Mini Kit",
            "Zymo Research ZR Fecal DNA MiniPrep Kit"],
           "second kit named only as 'Zymo Research Fecal DNA kit'"),
    1025: (["Qiagen QIAamp PowerFecal DNA Kit",
            "Qiagen QIAamp DNA Mini Kit"], ""),
    1034: (["Qiagen QIAamp PowerFecal Pro DNA Kit", "IHMS protocol Q"], ""),
    1061: (["Qiagen QIAamp cador Pathogen Mini Kit",
            "MO BIO PowerFecal DNA Isolation Kit"], ""),
    1078: (["Qiagen QIAamp DNA Microbiome Kit",
            "Qiagen QIAamp PowerFecal Pro DNA Kit"], ""),
    1104: (["Qiagen QIAamp DNA Mini Kit",
            "Qiagen QIAamp DNA Stool Mini Kit",
            "iNtRON i-genomic Urine DNA Extraction Mini Kit",
            "Zymo Research ZymoBIOMICS DNA Miniprep Kit"], ""),
    1116: (["Qiagen QIAamp DNA Microbiome Kit",
            "Zymo Research DNA Clean & Concentrator"], ""),
    1160: (["Qiagen BioRobot M48", "Roche MagNA Pure"], ""),
    1204: (["MO BIO PowerSoil DNA Isolation Kit",
            "Qiagen QIAamp Fast DNA Stool Mini Kit"],
           "second kit used with MO BIO bead shearing"),
    1257: (["Qiagen RNeasy Plus Universal Kit", "Qiagen RNeasy Plus Mini Kit",
            "Norgen Biotek Total RNA Purification Kit"], ""),
    1322: (["Thermo Fisher Scientific TRIzol LS",
            "Zymo Research RNA Clean & Concentrator"], ""),
    1343: (["Molzym Ultra-Deep Microbiome Prep", "Molzym Micro-Dx Kit",
            "Qiagen QIAamp DNA Microbiome Kit"], ""),
    1364: (["Zymo Research DNA Clean & Concentrator",
            "Zymo Research RNA Clean & Concentrator"],
           "Clean & Concentrator-5 (D4014 / R1014)"),
    1422: (["Zymo Research ZymoBIOMICS DNA Miniprep Kit",
            "Qiagen DNeasy PowerSoil Kit"], ""),
    1435: (["TransGen Biotech EasyPure Viral DNA/RNA Kit",
            "Zymo Research DNA Clean & Concentrator"], ""),
}

# --------------------------------------------------------------------------- #
# single-string overrides (keyed by string_id) for cases the rules below
# cannot reach from the wording alone
# --------------------------------------------------------------------------- #

SPECIAL = {
    43: ("PerkinElmer chemagic DNA Blood Kit-96", "run on a chemagic MSM I"),
    181: ("Qiagen EZ1 DSP Virus Kit", "run on an EZ1 Advanced XL"),
    217: ("Molzym SelectNA Blood Pathogen Kit", "with Add-on 10 complement"),
    251: ("PerkinElmer chemagic DNA Saliva Kit",
          "cat CMG-1035, run on a chemagic MSM I"),
    275: ("Qiagen QIAamp UCP Pathogen Mini Kit",
          "named only by catalogue numbers 50214 and 19092"),
    363: ("Tiangen TIANamp Magnetic DNA Kit", "catalogue number DP710-T2A only"),
    393: ("Qiagen EZ1 DSP Kit", "virus/tissue variant not stated"),
    458: ("Thermo Fisher Scientific GeneJET Genomic DNA Purification Kit",
          "Gram-positive bacteria protocol"),
    496: ("IHMS protocol Q", "IHMS SOP 07 V2"),
    510: ("MGP SOP 002 protocol", ""),
    570: ("Thermo Fisher Scientific MagMAX CORE Nucleic Acid Purification Kit",
          "lysis and elution modules on a KingFisher Flex"),
    571: ("Thermo Fisher Scientific MagMAX Multi-Sample Kit",
          "run on a MagMAX Express-96"),
    629: ("Thermo Fisher Scientific MagMAX CORE Nucleic Acid Purification Kit",
          "named only as 'Max Core protocol'"),
    645: ("Promega Maxwell", "named only as 'Maxwell culture DNA extraction kit'"),
    646: ("Promega Maxwell Blood DNA Kit", "Maxwell platform generation not stated"),
    686: ("Molzym MolYsis", "human DNA removal kit D-300-050"),
    748: ("DNA Genotek prepIT.L2P", "Oragene collection kit"),
    902: ("Qiagen QIAamp UCP Pathogen Mini Kit", "with Pathogen Lysis Tubes L"),
    918: ("Qiagen QIAsymphony DSP DNA Mini Kit", ""),
    966: ("Qiagen QIAamp DNA Stool Mini Kit",
          "also unspecified Tiangen kits, product not named"),
    928: ("Qiagen QIAamp DNA Stool Mini Kit",
          "source writes 'QIAamp DNA Extraction Power stool kit'"),
    1140: ("Qiagen QIAsymphony CellFree 500 Kit", ""),
    1196: ("Qiagen EZ1 DNA Tissue Kit",
           "with PowerBead tubes and buffer C1 on an EZ1 Advanced XL"),
    1202: ("Qiagen EZ1 DNA Tissue Kit",
           "with PowerBead tubes and buffer C1 on an EZ1 Advanced XL"),
    1214: ("Qiagen RNeasy Kit", "after a QIAshredder column"),
    1319: ("in-house protocol", "total nucleic acid + proteinase K, Abbott Molecular"),
    1445: ("in-house protocol", "laboratory-defined assay, Abbott Molecular"),
}


# --------------------------------------------------------------------------- #
# ordered rules
# --------------------------------------------------------------------------- #

def classify(t):
    """Return (controlled_name, mapping_note) for one normalised string."""

    # ---------------- methods and protocols ------------------------------- #
    if "ihms" in t or "international human microbiome standards" in t or \
            "international human microbiota standards" in t:
        if "protocol h" in t:
            return "IHMS protocol H", ""
        return "IHMS protocol Q", ""
    if "metahit" in t:
        return "MetaHIT protocol", ""
    if "netovir" in t:
        return "NetoVIR protocol", ""
    if "ctab" in t or "cetyltrimethylammonium" in t or \
            "hexadecyltrimethylammonium" in t:
        if "sodium dodecyl" in t or "sds" in t:
            return "CTAB/SDS extraction", ""
        note = "described as a kit" if "kit" in t else ""
        if "modified" in t:
            note = "modified CTAB"
        return "CTAB extraction", note
    if "phenol" in t and "chloroform" in t:
        if "bead" in t:
            return "bead beating with phenol-chloroform", ""
        return "phenol-chloroform extraction", ""
    if "phenol" in t and "trichloromethane" in t:
        return "phenol-chloroform extraction", "written phenol/trichloromethane"
    if "chloroform based" in t:
        return "chloroform-based extraction", "protocol of Tang et al. 2008"
    if "repeated bead beating" in t or "repeated bead-beating" in t or \
            "repeated beat beating" in t:
        if "column purification" in t or "rbb+c" in t or "rbb c" in t:
            return "repeated bead beating plus column purification", ""
        return "repeated bead beating", ""
    if "bead shaking" in t:
        return "bead beating", ""
    if "fastprep" in t and "fp120" in t:
        return "bead beating", "FastPrep FP120 homogeniser only"
    if "sodium dodecyl" in t:
        return "SDS extraction", ""
    if "lithium acetate" in t:
        return "lithium acetate extraction", ""
    if "silica spin column protocol" in t:
        return "silica spin-column extraction", "protocol of Dabney et al. 2013"
    if "in house silica" in t:
        return "in-house protocol", "silica-based in-house method"
    if t.strip() == "enzymatic method":
        return "enzymatic lysis", ""

    # ---------------- Zymo Research --------------------------------------- #
    if "zymobiomics" in t or "zymobiomic" in t:
        if "96" in t and "magbead" in t.replace(" ", ""):
            return "Zymo Research ZymoBIOMICS 96 MagBead DNA Kit", ""
        if "magbead" in t.replace(" ", "") and ("dna/rna" in t or "rna" in t
                                                and "dna" in t):
            return "Zymo Research ZymoBIOMICS MagBead DNA/RNA Kit", ""
        if "magbead" in t.replace(" ", ""):
            return ("Zymo Research ZymoBIOMICS MagBead DNA Kit",
                    "96-well format not stated")
        if "96" in t:
            return "Zymo Research ZymoBIOMICS 96 DNA Kit", ""
        if "dna/rna" in t and "microbiome" in t:
            return "Zymo Research ZymoBIOMICS DNA/RNA Microbiome Mini Kit", ""
        if "dna/rna" in t:
            note = "" if "miniprep" in t else "written 'DNA/RNA Mini Kit'"
            return "Zymo Research ZymoBIOMICS DNA/RNA Miniprep Kit", note
        if "rna miniprep" in t:
            return "Zymo Research ZymoBIOMICS RNA Miniprep Kit", ""
        if "microprep" in t or "micro kit" in t:
            return "Zymo Research ZymoBIOMICS DNA Microprep Kit", ""
        return "Zymo Research ZymoBIOMICS DNA Miniprep Kit", ""
    if "hostzero" in t:
        return "Zymo Research HostZERO Microbial DNA Kit", ""
    if "direct zol" in t:
        if "dna/rna" in t:
            return "Zymo Research Direct-zol DNA/RNA Miniprep Kit", ""
        if "microprep" in t:
            return "Zymo Research Direct-zol RNA MicroPrep Kit", ""
        return "Zymo Research Direct-zol RNA MiniPrep Kit", ""
    if "zr duet" in t:
        return "Zymo Research ZR-Duet DNA/RNA MiniPrep Kit", ""
    if "quick dna" in t or "quick rna" in t or "quick  dna" in t:
        if "pathogen" in t and "magbead" in t.replace(" ", ""):
            return "Zymo Research Quick-DNA/RNA Pathogen MagBead Kit", ""
        if "pathogen" in t:
            return "Zymo Research Quick-DNA/RNA Pathogen MiniPrep Kit", ""
        if "hmw" in t:
            return "Zymo Research Quick-DNA HMW MagBead Kit", ""
        if "magbead" in t.replace(" ", ""):
            if "plus" in t:
                return "Zymo Research Quick-DNA MagBead Plus Kit", ""
            return "Zymo Research Quick-DNA/RNA MagBead Kit", ""
        if "fecal" in t or "soil" in t:
            if "quick rna" in t or ("rna" in t and "quick dna" not in t):
                return "Zymo Research Quick-RNA Fecal/Soil Microbe Microprep Kit", ""
            if "microprep" in t:
                return "Zymo Research Quick-DNA Fecal/Soil Microbe Microprep Kit", ""
            note = "" if "miniprep" in t else "Miniprep/Microprep format not stated"
            return "Zymo Research Quick-DNA Fecal/Soil Microbe Miniprep Kit", note
        if "fungal" in t or "bacterial" in t:
            if "microprep" in t:
                return "Zymo Research Quick-DNA Fungal/Bacterial Microprep Kit", ""
            return "Zymo Research Quick-DNA Fungal/Bacterial Miniprep Kit", ""
        if "viral" in t and "dna/rna" in t:
            return "Zymo Research Quick-DNA/RNA Viral Kit", ""
        if "viral" in t:
            return "Zymo Research Quick-DNA Viral Kit", ""
        if "dna/rna" in t and "miniprep plus" in t:
            return "Zymo Research Quick-DNA/RNA Miniprep Plus Kit", ""
        if "microprep" in t:
            return "Zymo Research Quick-DNA Microprep Kit", ""
        if "d6010" in t:
            return ("Zymo Research Quick-DNA Fecal/Soil Microbe Miniprep Kit",
                    "catalogue number D6010")
        return "Zymo Research Quick-DNA Miniprep Kit", ""
    if "fecal/soil microbe" in t or "fecal/soil" in t:
        return ("Zymo Research Quick-DNA Fecal/Soil Microbe Miniprep Kit",
                "Miniprep/Microprep format not stated")
    if re.search(r"\bzr\b", t) or ("zymo" in t and "fecal" in t):
        if "soil/fecal" in t and "rna" in t:
            return "Zymo Research ZR Soil/Fecal RNA MicroPrep Kit", ""
        if "fungal" in t or "bacterial" in t:
            return "Zymo Research ZR Fungal/Bacterial DNA MicroPrep Kit", ""
        if "rna microprep" in t or ("rna" in t and "microprep" in t
                                    and "dna" not in t):
            return "Zymo Research ZR Fecal RNA MicroPrep Kit", ""
        if "microprep" in t:
            return "Zymo Research ZR Fecal DNA MicroPrep Kit", ""
        return "Zymo Research ZR Fecal DNA MiniPrep Kit", ""
    if "clean" in t and "concentrator" in t and "zymo" in t:
        note = "purification kit, not an extraction kit"
        if "genomic" in t:
            return "Zymo Research Genomic DNA Clean & Concentrator", note
        if re.search(r"\brna\b", t) and "dna" not in t:
            return "Zymo Research RNA Clean & Concentrator", note
        return "Zymo Research DNA Clean & Concentrator", note
    if "zymo" in t and "pathogen" in t and "magbead" in t.replace(" ", ""):
        return "Zymo Research Quick-DNA/RNA Pathogen MagBead Kit", ""
    if "zymo" in t and "bead beating kit" in t:
        return UNCLEAR, "Zymo bead-beating kit, product not named"
    if "zymo" in t and "dna/rna" in t and "microbiome" in t:
        return "Zymo Research ZymoBIOMICS DNA/RNA Miniprep Kit", ""
    if "zymo" in t and "miniprep" in t:
        return UNCLEAR, "Zymo miniprep kit, product line not identifiable"

    # ---------------- Qiagen: PowerSoil / PowerFecal / Power* -------------- #
    if "cretmag" in t:
        return "CretMag Power Soil DNA Kit", ""
    if "powersoil" in t and "rneasy" in t:
        return "Qiagen RNeasy PowerSoil Total RNA Kit", ""
    if "powersoil" in t:
        is_pro = "pro" in re.sub(r"\bprotocol\b", "", t)
        ht = bool(re.search(r"\b(htp|ht|96)\b", t)) or "qiacube ht" in t or \
            "high throughput" in t or "extraction plates" in t
        if is_pro and ht:
            return "Qiagen DNeasy 96 PowerSoil Pro QIAcube HT Kit", ""
        if is_pro:
            note = ""
            if "qiaamp" in t:
                note = "written QIAamp PowerSoil Pro"
            elif "mobio" in t:
                note = "paper cites MO BIO; PowerSoil Pro is a Qiagen product"
            return "Qiagen DNeasy PowerSoil Pro Kit", note
        if "powerlyzer" in t:
            if "dneasy" in t:
                return "Qiagen DNeasy PowerLyzer PowerSoil Kit", ""
            return ("MO BIO PowerLyzer PowerSoil DNA Isolation Kit",
                    "paper cites Qiagen as supplier" if "qiagen" in t else "")
        if "powermag" in t:
            return "MO BIO PowerMag Soil DNA Isolation Kit", ""
        if "magattract" in t:
            if "kf" in t.split():
                return "Qiagen MagAttract PowerSoil DNA KF Kit", ""
            if re.search(r"\bep\b", t):
                return "Qiagen MagAttract PowerSoil DNA EP Kit", ""
            return "Qiagen MagAttract PowerSoil DNA Kit", ""
        if ht:
            if "dneasy" in t:
                return "Qiagen DNeasy PowerSoil HTP 96 Kit", ""
            return ("MO BIO PowerSoil-htp 96 Well DNA Isolation Kit",
                    "paper cites Qiagen as supplier" if "qiagen" in t else "")
        if "qiaamp" in t:
            return "Qiagen DNeasy PowerSoil Kit", "written QIAamp PowerSoil"
        if "dneasy" in t:
            note = "'extraction plates' (96-well)" if "extraction plates" in t else ""
            return "Qiagen DNeasy PowerSoil Kit", note
        if "powermax" in t:
            return "MO BIO PowerMax Soil DNA Isolation Kit", ""
        return ("MO BIO PowerSoil DNA Isolation Kit",
                "paper cites Qiagen as supplier" if "qiagen" in t else "")
    if "powerfecal" in t:
        pro = bool(re.search(r"\bpro\b", t)) or "prodna" in t
        if "allprep" in t or "dna/rna" in t:
            if "96" in t:
                return "Qiagen AllPrep PowerFecal DNA/RNA 96 Kit", ""
            if pro:
                return "Qiagen AllPrep PowerFecal Pro DNA/RNA Kit", ""
            return "Qiagen AllPrep PowerFecal DNA/RNA Kit", ""
        if "96" in t and "qiacube" in t:
            return "Qiagen QIAamp 96 PowerFecal QIAcube HT Kit", ""
        if pro:
            note = ""
            if "fna" in t:
                note = "'FNA' in source, read as DNA"
            elif "dneasy" in t:
                note = "written DNeasy PowerFecal Pro"
            elif "qiasymphony" in t:
                note = "run on a QIAsymphony"
            return "Qiagen QIAamp PowerFecal Pro DNA Kit", note
        if "51804" in t:
            return ("Qiagen QIAamp PowerFecal Pro DNA Kit",
                    "catalogue number 51804")
        if "mobio" in t:
            return "MO BIO PowerFecal DNA Isolation Kit", ""
        if "isolation" in t:
            return ("MO BIO PowerFecal DNA Isolation Kit",
                    "paper cites Qiagen as supplier" if "qiagen" in t else "")
        note = "'Pro' not stated" if t.strip() in (
            "powerfecal qiagen qiagen uk", "qiagen powerfecal kit") else ""
        return "Qiagen QIAamp PowerFecal DNA Kit", note
    if "fecal power amp" in t:
        return "Qiagen QIAamp PowerFecal DNA Kit", "garbled source string"
    if "powerbiofilm" in t:
        return "Qiagen DNeasy PowerBiofilm Kit", ""
    if "powermicrobiome" in t:
        if "magattract" in t:
            if re.search(r"\bep\b", t):
                return "Qiagen MagAttract PowerMicrobiome DNA/RNA EP Kit", ""
            if re.search(r"\bkf\b", t):
                return "Qiagen MagAttract PowerMicrobiome DNA KF Kit", ""
            return "Qiagen MagAttract PowerMicrobiome DNA/RNA Kit", ""
        if "rneasy" in t:
            return "Qiagen RNeasy PowerMicrobiome Kit", ""
        return "MO BIO PowerMicrobiome RNA Isolation Kit", ""
    if "powerviral" in t:
        return "Qiagen AllPrep PowerViral DNA/RNA Kit", ""
    if "powermax" in t:
        return "MO BIO PowerMax Soil DNA Isolation Kit", ""
    if "powerfood" in t:
        return "MO BIO PowerFood Microbial DNA Isolation Kit", ""
    if "powermag" in t:
        return "MO BIO PowerMag Soil DNA Isolation Kit", ""
    if "powerlyzer" in t:
        return "Qiagen DNeasy PowerLyzer PowerSoil Kit", \
            "PowerSoil not stated" if "powersoil" not in t else ""

    # ---------------- Qiagen: DNeasy ------------------------------------- #
    if "dneasy" in t:
        if "ultraclean" in t:
            if "96" in t:
                return "Qiagen DNeasy UltraClean 96 Microbial Kit", ""
            return "Qiagen DNeasy UltraClean Microbial Kit", ""
        if "plant" in t:
            return "Qiagen DNeasy Plant Mini Kit", ""
        if "blood" in t or "tissue" in t:
            return "Qiagen DNeasy Blood & Tissue Kit", ""
        if "96" in t:
            return "Qiagen DNeasy Kit", "'DNeasy 96 protocol', product not named"
        return "Qiagen DNeasy Kit", "product line only"
    if "ultraclean" in t and "microbial" in t:
        return "MO BIO UltraClean Microbial DNA Isolation Kit", ""

    # ---------------- Qiagen: QIAamp ------------------------------------- #
    if "qiaamp" in t or "qiagen" in t or "qiasymphony" in t or "qiacube" in t \
            or "ez1" in t or "allprep" in t or "rneasy" in t or "qiaquick" in t \
            or "mirneasy" in t or "repli g" in t or "gentra" in t \
            or "puregene" in t or "dsp virus" in t or "dsp dna" in t \
            or "minelute" in t or "minielute" in t or re.search(r"\bucp\b", t) \
            or "cador" in t or "biostic" in t or "bacteremia" in t \
            or "magattract" in t or "genomic tip" in t or "generead" in t \
            or "qiaseq" in t or "maxiprep" in t:
        if "qiaseq" in t:
            return ("Qiagen QIAseq FX DNA Library Kit",
                    "library preparation kit, not an extraction kit")
        if "biostic" in t or "bacteremia" in t:
            if "mobio" in t:
                return "MO BIO BiOstic Bacteremia DNA Isolation Kit", ""
            if "isolation" in t:
                return ("MO BIO BiOstic Bacteremia DNA Isolation Kit",
                        "paper cites Qiagen as supplier" if "qiagen" in t else "")
            return "Qiagen QIAamp BiOstic Bacteremia DNA Kit", ""
        if "cador" in t:
            return "Qiagen QIAamp cador Pathogen Mini Kit", ""
        if "ucp" in t or "upc pathogen" in t:
            if "mini" in t:
                return "Qiagen QIAamp UCP Pathogen Mini Kit", ""
            return "Qiagen QIAamp UCP Pathogen DNA Kit", ""
        if "minelute" in t or "mini elute" in t or "minielute" in t:
            if "ccfdna" in t:
                return "Qiagen QIAamp MinElute ccfDNA Kit", ""
            if "pcr purification" in t:
                return ("Qiagen MinElute PCR Purification Kit",
                        "purification kit, not an extraction kit")
            return "Qiagen QIAamp MinElute Virus Spin Kit", ""
        if "ultrasens" in t or "ultra sens" in t:
            return "Qiagen QIAamp UltraSens Virus Kit", ""
        if "circulating nucleic acid" in t:
            return "Qiagen QIAamp Circulating Nucleic Acid Kit", ""
        if "qiasymphony" in t or "qiasymphany" in t:
            if "circulating" in t:
                return "Qiagen QIAsymphony DSP Circulating DNA Kit", ""
            if "virus/bacteria" in t:
                return "Qiagen QIAsymphony Virus/Bacteria Mini Kit", ""
            if "virus/pathogen" in t or "virus pathogen" in t:
                if "midi" in t:
                    return "Qiagen QIAsymphony DSP Virus/Pathogen Midi Kit", ""
                if "mini" in t:
                    return "Qiagen QIAsymphony DSP Virus/Pathogen Mini Kit", ""
                return "Qiagen QIAsymphony DSP Virus/Pathogen Kit", ""
            if "dna midi" in t:
                return "Qiagen QIAsymphony DSP DNA Midi Kit", ""
            if "dna mini" in t:
                return "Qiagen QIAsymphony DSP DNA Mini Kit", ""
            return "Qiagen QIAsymphony", "instrument only"
        if "dsp virus" in t:
            if "midi" in t:
                return "Qiagen QIAsymphony DSP Virus/Pathogen Midi Kit", ""
            return "Qiagen EZ1 DSP Virus Kit", ""
        if "ez1" in t:
            if "virus" in t or "viral" in t:
                if "v2" in t or "version 2" in t:
                    return "Qiagen EZ1 Virus Mini Kit v2.0", ""
                return "Qiagen EZ1 Virus Mini Kit", "version not stated"
            if "tissue" in t:
                return "Qiagen EZ1 DNA Tissue Kit", ""
            if "blood" in t:
                return "Qiagen EZ1 DNA Blood 200 \u00b5L Kit", ""
            return "Qiagen EZ1 DNA Kit", "product line only"
        if "allprep" in t:
            if "96" in t:
                return "Qiagen AllPrep PowerFecal DNA/RNA 96 Kit", ""
            if "mirna" in t:
                return "Qiagen AllPrep DNA/RNA/miRNA Universal Kit", ""
            if "protein" in t:
                return "Qiagen AllPrep DNA/RNA/Protein Mini Kit", ""
            if "micro" in t:
                return "Qiagen AllPrep DNA/RNA Micro Kit", ""
            if "mini" in t or "dna/rna" in t or "rna/dna" in t:
                return "Qiagen AllPrep DNA/RNA Mini Kit", ""
            return "Qiagen AllPrep Kit", "product line only"
        if "magattract" in t:
            if "microbial" in t:
                return "Qiagen MagAttract Microbial DNA Kit", ""
            return "Qiagen MagAttract DNA Kit", "product line only"
        if "mirneasy" in t:
            if "serum" in t or "plasma" in t:
                return "Qiagen miRNeasy Serum/Plasma Kit", ""
            return "Qiagen miRNeasy Kit", "product line only"
        if "rneasy" in t:
            if "powersoil" in t:
                return "Qiagen RNeasy PowerSoil Total RNA Kit", ""
            if "lipid" in t:
                return "Qiagen RNeasy Lipid Tissue Mini Kit", ""
            if "plus universal" in t:
                if "midi" in t:
                    return "Qiagen RNeasy Plus Universal Midi Kit", ""
                if "mini" in t:
                    return "Qiagen RNeasy Plus Universal Mini Kit", ""
                return "Qiagen RNeasy Plus Universal Kit", ""
            if "plus mini" in t:
                return "Qiagen RNeasy Plus Mini Kit", ""
            if "micro" in t:
                return "Qiagen RNeasy Micro Kit", ""
            if "midi" in t:
                return "Qiagen RNeasy Midi Kit", ""
            if "mini" in t:
                return "Qiagen RNeasy Mini Kit", ""
            return "Qiagen RNeasy Kit", "product line only"
        if "qiaquick" in t or "pcr clean up" in t or "pcr purification" in t:
            note = "purification kit, not an extraction kit"
            if "96" in t:
                return "Qiagen QIAquick 96 PCR Purification Kit", note
            return "Qiagen QIAquick PCR Purification Kit", note
        if "repli g" in t or "repli-g" in t:
            return ("Qiagen REPLI-g Mini Kit",
                    "whole-genome amplification kit, not an extraction kit")
        if "gentra" in t or "puregene" in t:
            if "buccal" in t:
                return "Qiagen Gentra Puregene Buccal Cell Kit", ""
            if "yeast" in t or "bacteria" in t:
                return "Qiagen Gentra Puregene Yeast/Bacteria Kit", ""
            return "Qiagen Gentra Puregene Tissue Kit", ""
        if "genomic tip" in t or "genomic-tip" in t:
            return "Qiagen Genomic-tip 20/G", ""
        if "generead" in t:
            return "Qiagen GeneRead DNA FFPE Kit", ""
        if "ffpe" in t:
            return "Qiagen QIAamp DNA FFPE Tissue Kit", ""
        if "investigator" in t:
            return "Qiagen QIAamp DNA Investigator Kit", ""
        if "microbiome" in t:
            return "Qiagen QIAamp DNA Microbiome Kit", ""
        if "viral rna" in t or "rna mini kit" in t and "qiaamp" in t:
            return "Qiagen QIAamp Viral RNA Mini Kit", ""
        if "mini rna" in t:
            return "Qiagen QIAamp Mini RNA Kit", ""
        if "96" in t and "qiacube" in t:
            if "virus" in t:
                return "Qiagen QIAamp 96 Virus QIAcube HT Kit", ""
            return "Qiagen QIAamp 96 DNA QIAcube HT Kit", ""
        if "stool" in t or "fecal" in t:
            if "fast" in t:
                return "Qiagen QIAamp Fast DNA Stool Mini Kit", ""
            return "Qiagen QIAamp DNA Stool Mini Kit", ""
        if "blood" in t and "tissue" in t:
            return "Qiagen DNeasy Blood & Tissue Kit", ""
        if "blood" in t:
            return "Qiagen QIAamp DNA Blood Mini Kit", ""
        if "micro kit" in t or "dna micro" in t:
            return "Qiagen QIAamp DNA Micro Kit", ""
        if "qiacube" in t and "dna mini" in t:
            return "Qiagen QIAamp DNA Mini QIAcube Kit", ""
        if "fast dna mini" in t:
            return "Qiagen QIAamp Fast DNA Mini Kit", ""
        if "mini" in t and "qiaamp" in t:
            return "Qiagen QIAamp DNA Mini Kit", ""
        if "dna mini" in t:
            return "Qiagen QIAamp DNA Mini Kit", ""
        if "qiacube" in t:
            return "Qiagen QIAamp DNA Mini Kit", "run on a QIAcube"
        if "qiaamp" in t:
            return "Qiagen QIAamp DNA Kit", "product line only"
        if "maxiprep" in t:
            return UNCLEAR, "Qiagen 'maxiprep kit', product not named"
        if "pb buffer" in t:
            return UNCLEAR, "Qiagen buffers only, no kit named"
        if "biorobot" in t:
            return "Qiagen BioRobot M48", ""
        if "microbial dna" in t or "dna extraction kit" in t or t.strip() in (
                "qiagen kit",):
            return UNCLEAR, "Qiagen named, product not named"
        return UNCLEAR, "Qiagen named, product not named"

    # ---------------- MO BIO ---------------------------------------------- #
    if "mobio" in t:
        if "microbiome" in t:
            return UNCLEAR, "MO BIO 'microbiome kit', product not named"
        return UNCLEAR, "MO BIO named, product not named"

    # ---------------- MP Biomedicals -------------------------------------- #
    if "fastdna" in t or "fast dna spin" in t or "mpbio" in t or "gnome" in t \
            or "g'nome" in t or "spineasy" in t:
        if "magbead" in t.replace(" ", ""):
            if "soil" in t:
                return "MP Biomedicals MagBeads FastDNA Kit for Soil", ""
            return "MP Biomedicals MagBeads FastDNA Kit", ""
        if "gnome" in t or "g'nome" in t:
            return "MP Biomedicals GNOME DNA Isolation Kit", ""
        if "spineasy" in t:
            if "pro" in t:
                return "MP Biomedicals SPINeasy DNA Pro Kit for Soil", ""
            return "MP Biomedicals SPINeasy DNA Kit for Soil", ""
        if "soil" in t:
            return "MP Biomedicals FastDNA SPIN Kit for Soil", ""
        if "feces" in t or "fecal" in t:
            return "MP Biomedicals FastDNA SPIN Kit for Feces", ""
        return "MP Biomedicals FastDNA SPIN Kit", ""

    # ---------------- Omega Bio-tek --------------------------------------- #
    if "ezna" in t or re.search(r"\bomega\b", t) or "mag bind" in t \
            or "magbind" in t:
        if "mag bind" in t or "magbind" in t:
            if "metagenomics" in t:
                return "Omega Bio-tek Mag-Bind Universal Metagenomics Kit", ""
            if "universal pathogen" in t:
                return "Omega Bio-tek Mag-Bind Universal Pathogen 96 Kit", ""
            if "viral" in t:
                return "Omega Bio-tek Mag-Bind Viral DNA/RNA Kit", ""
            if "cfdna" in t:
                return "Omega Bio-tek Mag-Bind cfDNA Kit", ""
            if "stool" in t or t.strip().startswith("pf "):
                return "Omega Bio-tek PF Mag-Bind Stool DNA Kit", ""
            return "Omega Bio-tek Mag-Bind Soil DNA Kit", ""
        if t.strip().startswith("pf "):
            return "Omega Bio-tek PF Mag-Bind Stool DNA Kit", ""
        if "microelute" in t:
            return "Omega Bio-tek E.Z.N.A. MicroElute Genomic DNA Kit", ""
        if "viral" in t:
            return "Omega Bio-tek E.Z.N.A. Viral DNA Kit", ""
        if "soil" in t:
            return "Omega Bio-tek E.Z.N.A. Soil DNA Kit" if "ezna" in t \
                else "Omega Bio-tek Mag-Bind Soil DNA Kit", ""
        if "tissue" in t:
            return "Omega Bio-tek E.Z.N.A. Tissue DNA Kit", ""
        if "stool" in t or "fecal" in t:
            note = "written 'fecal DNA Kit'" if "fecal" in t else ""
            return "Omega Bio-tek E.Z.N.A. Stool DNA Kit", note
        return UNCLEAR, "Omega Bio-tek named, product not named"

    # ---------------- Thermo Fisher / Invitrogen / Applied Biosystems ----- #
    if "magmax" in t:
        if "microbiome ultra" in t:
            if re.search(r"\bii\b", t):
                return ("Thermo Fisher Scientific MagMAX Microbiome Ultra II "
                        "Nucleic Acid Isolation Kit", "")
            return ("Thermo Fisher Scientific MagMAX Microbiome Ultra Nucleic "
                    "Acid Isolation Kit", "")
        if "core" in t:
            return ("Thermo Fisher Scientific MagMAX CORE Nucleic Acid "
                    "Purification Kit", "")
        if "viral/pathogen" in t or "mvp" in t:
            if re.search(r"\bii\b", t):
                return ("Thermo Fisher Scientific MagMAX Viral/Pathogen II "
                        "Nucleic Acid Isolation Kit", "")
            return ("Thermo Fisher Scientific MagMAX Viral/Pathogen Nucleic "
                    "Acid Isolation Kit", "")
        if "96" in t and "viral rna" in t:
            return "Thermo Fisher Scientific MagMAX-96 Viral RNA Isolation Kit", ""
        if "viral rna" in t:
            return "Thermo Fisher Scientific MagMAX Viral RNA Isolation Kit", ""
        if "total nucleic acid" in t or "total kit" in t:
            return ("Thermo Fisher Scientific MagMAX Total Nucleic Acid "
                    "Isolation Kit", "")
        return "Thermo Fisher Scientific MagMAX Kit", "product line only"
    if "kingfisher" in t:
        return "Thermo Fisher Scientific KingFisher Flex", "instrument only"
    if "purelink" in t:
        if "microbiome" in t:
            return "Invitrogen PureLink Microbiome DNA Purification Kit", ""
        if "viral" in t or "rna/dna" in t:
            return "Invitrogen PureLink Viral RNA/DNA Mini Kit", ""
        if "mini" in t:
            return "Invitrogen PureLink Genomic DNA Mini Kit", ""
        return "Invitrogen PureLink Genomic DNA Kit", "format not stated"
    if "recoverall" in t:
        if "multi sample" in t or "workflow" in t:
            return ("Thermo Fisher Scientific RecoverAll Multi-Sample RNA/DNA "
                    "Workflow", "")
        return ("Thermo Fisher Scientific RecoverAll Total Nucleic Acid "
                "Isolation Kit", "")
    if "mirvana" in t:
        if "paris" in t:
            return "Thermo Fisher Scientific mirVana PARIS Kit", ""
        return "Thermo Fisher Scientific mirVana miRNA Isolation Kit", ""
    if "ribopure" in t:
        return "Thermo Fisher Scientific RiboPure Bacteria Kit", ""
    if "genejet" in t:
        return ("Thermo Fisher Scientific GeneJET Genomic DNA Purification Kit", "")
    if "trizol" in t:
        if re.search(r"\bls\b", t):
            return "Thermo Fisher Scientific TRIzol LS", ""
        return "Thermo Fisher Scientific TRIzol", ""
    if "labserv" in t:
        return "Thermo Fisher Scientific LabServ Prefill Viral Total NA Kit", ""
    if "quant" in t and "dsdna" in t:
        return ("Thermo Fisher Scientific Quant-iT dsDNA Assay Kit",
                "quantification assay, not an extraction kit")
    if "qubit" in t:
        return ("Invitrogen Qubit dsDNA BR Assay Kit",
                "quantification assay, not an extraction kit")
    if "crisprclean" in t:
        return ("Jumpcode Genomics CRISPRclean Plus Stranded Total RNA Prep Kit",
                "library preparation kit, not an extraction kit")
    if "nugen" in t or "ovation" in t:
        return ("Tecan NuGEN Ovation v2 Kit",
                "amplification/library kit, not an extraction kit")
    if "genomiphi" in t:
        return ("Cytiva GenomiPhi DNA Amplification Kit",
                "amplification kit, not an extraction kit")
    if "truseq" in t:
        return UNCLEAR, "library preparation guide, no extraction kit named"
    if "qiaseq" in t:
        return ("Qiagen QIAseq FX DNA Library Kit",
                "library preparation kit, not an extraction kit")

    # ---------------- Promega --------------------------------------------- #
    if "maxwell" in t or "wizard" in t or "magazorb" in t or "dna iq" in t:
        if "purefood" in t:
            if "pathogen" in t:
                return "Promega Maxwell RSC PureFood Pathogen Kit", ""
            return "Promega Maxwell RSC PureFood GMO and Authentication Kit", ""
        if "wizard" in t:
            if "hmw" in t:
                return "Promega Wizard HMW DNA Extraction Kit", ""
            if "sv 96" in t:
                return "Promega Wizard SV 96 Genomic DNA Purification Kit", ""
            return "Promega Wizard Genomic DNA Purification Kit", ""
        if "magazorb" in t:
            return "Promega MagaZorb DNA Mini-Prep Kit", ""
        if "dna iq" in t:
            return "Promega DNA IQ System", ""
        if "ht 96" in t or "ht96" in t:
            return "Promega Maxwell HT 96 gDNA Blood Isolation System", ""
        if "csc" in t:
            return "Promega Maxwell CSC HT Viral TNA Kit", ""
        if "ccfdna" in t:
            return "Promega Maxwell RSC ccfDNA Plasma Kit", ""
        if "fecal" in t or "microbiome" in t:
            return "Promega Maxwell RSC Fecal Microbiome DNA Kit", ""
        if "16" in t:
            if "lev blood" in t:
                return "Promega Maxwell 16 LEV Blood DNA Kit", ""
            if "ffs" in t:
                return "Promega Maxwell 16 FFS Nucleic Acid Extraction Kit", ""
            if "tissue lev total rna" in t or ("tissue" in t and "rna" in t):
                return "Promega Maxwell 16 Tissue LEV Total RNA Purification Kit", ""
            if "tissue" in t:
                return "Promega Maxwell 16 Tissue DNA Kit", ""
            if "viral" in t:
                return ("Promega Maxwell 16 Viral Total Nucleic Acid "
                        "Purification Kit", "")
            return "Promega Maxwell 16", "instrument only"
        if "viral total nucleic acid" in t or ("viral" in t and "rsc" in t):
            return ("Promega Maxwell RSC Viral Total Nucleic Acid Purification "
                    "Kit", "")
        if "tissue" in t and "rsc" in t:
            return "Promega Maxwell RSC Tissue DNA Kit", ""
        if "blood" in t:
            return "Promega Maxwell RSC Blood DNA Kit", ""
        if "rsc" in t:
            return "Promega Maxwell RSC", "instrument only"
        return "Promega Maxwell", "instrument only"
    if "total nucleic acid purification kit" in t and "promega" in t:
        return "Promega Total Nucleic Acid Purification Kit", ""

    # ---------------- Roche ------------------------------------------------ #
    if "magna pure" in t or "high pure" in t or "cobas" in t or "septifast" in t:
        if "septifast" in t:
            return "Roche SeptiFast Lysis Kit", ""
        if "cobas" in t:
            if "ampliprep" in t:
                return "Roche COBAS AmpliPrep", ""
            return "Roche cobas 4800", "instrument only"
        if "high pure" in t:
            if "pcr template" in t:
                return "Roche High Pure PCR Template Preparation Kit", ""
            if "large volume" in t:
                return "Roche High Pure Viral Nucleic Acid Large Volume Kit", ""
            if "viral rna" in t:
                return "Roche High Pure Viral RNA Kit", ""
            return "Roche High Pure Viral Nucleic Acid Kit", ""
        if "kit iii" in t or "isolation kit iii" in t:
            return "Roche MagNA Pure LC DNA Isolation Kit III (Bacteria, Fungi)", ""
        if "total nucleic acid" in t and "lc" in t:
            return "Roche MagNA Pure LC Total Nucleic Acid Isolation Kit", ""
        if "large volume" in t:
            return "Roche MagNA Pure 96 DNA and Viral NA Large Volume Kit", ""
        if "small volume" in t:
            return "Roche MagNA Pure 96 DNA and Viral NA Small Volume Kit", ""
        if "24" in t:
            if "total na isolation" in t:
                return "Roche MagNA Pure 24 Total NA Isolation Kit", ""
            return "Roche MagNA Pure 24", "Fast Pathogen 200 protocol" \
                if "pathogen 200" in t else "instrument only"
        if "96" in t:
            return "Roche MagNA Pure 96", "instrument only"
        if re.search(r"\blc\b", t):
            return "Roche MagNA Pure LC", "instrument only"
        return "Roche MagNA Pure", "instrument only"
    if "dna and viral na" in t:
        if "large volume" in t:
            return "Roche MagNA Pure 96 DNA and Viral NA Large Volume Kit", ""
        return ("Roche DNA and Viral NA Small Volume Kit",
                "MagNA Pure system not stated")
    if "assembly tubes" in t:
        return UNCLEAR, "Roche silica columns only, no kit named"

    # ---------------- Macherey-Nagel -------------------------------------- #
    if "nucleospin" in t or "nucleomag" in t or "macherey nagel" in t:
        if "nucleomag" in t:
            return "Macherey-Nagel NucleoMag DNA Microbiome Kit", ""
        if "96" in t and "soil" in t:
            return "Macherey-Nagel NucleoSpin 96 Soil Kit", ""
        if "96" in t and "stool" in t:
            return "Macherey-Nagel NucleoSpin 96 Stool Kit", ""
        if "soil" in t:
            return "Macherey-Nagel NucleoSpin Soil Kit", ""
        if "stool" in t:
            return "Macherey-Nagel NucleoSpin DNA Stool Kit", ""
        if "microbial" in t:
            return "Macherey-Nagel NucleoSpin Microbial DNA Kit", ""
        if "cfdna" in t:
            note = "supplied by Takara Bio" if "takara" in t else ""
            if "xs" in t:
                return "Macherey-Nagel NucleoSpin cfDNA XS Kit", note
            return "Macherey-Nagel NucleoSpin cfDNA Kit", note
        if "plasma xs" in t:
            return "Macherey-Nagel NucleoSpin Plasma XS Kit", ""
        if "virus" in t:
            return "Macherey-Nagel NucleoSpin Virus Kit", ""
        if "rna plus" in t:
            return ("Macherey-Nagel NucleoSpin RNA Plus Kit",
                    "supplied by Takara Bio")
        if "gdna clean" in t or "clean up" in t:
            return ("Macherey-Nagel NucleoSpin gDNA Clean-up Kit",
                    "purification kit, not an extraction kit")
        if "tissue" in t:
            return "Macherey-Nagel NucleoSpin Tissue Kit", ""
        return UNCLEAR, "Macherey-Nagel named, product not named"

    # ---------------- Tiangen ---------------------------------------------- #
    if "tianamp" in t or "tiangen" in t or "tianmicrobe" in t or \
            "magnetic soil and stool" in t or "dp316" in t or "dp712" in t or \
            "dp328" in t:
        if "tianmicrobe" in t or "ng550" in t:
            return "Tiangen TIANMicrobe Magnetic Patho-DNA/RNA Kit", ""
        if "dp316" in t or "micro dna" in t:
            return "Tiangen TIANamp Micro DNA Kit", ""
        if "magnetic soil and stool" in t or "dp712" in t or "dp210831" in t:
            note = "catalogue number only" if "magnetic soil and stool" not in t \
                else ""
            return "Tiangen Magnetic Soil and Stool DNA Kit", note
        if "stool" in t and ("dp328" in t or "tianamp" in t):
            return "Tiangen TIANamp Stool DNA Kit", ""
        if "dp328" in t:
            return "Tiangen TIANamp Stool DNA Kit", ""
        if "magnetic" in t and "serum" in t:
            if "maxi" in t:
                return "Tiangen Magnetic Serum/Plasma DNA Maxi Kit", ""
            return "Tiangen Magnetic Serum/Plasma DNA Kit", ""
        if "magnetic" in t and "universal" in t:
            return "Tiangen Magnetic Universal Genomic DNA Kit", ""
        if "magnetic" in t and "viral" in t:
            return "Tiangen Magnetic Viral DNA/RNA Kit", ""
        if "magnetic bead genomic" in t or "dop336" in t:
            return "Tiangen Magnetic Bead Genomic DNA Kit", ""
        if "magnetic dna kit" in t or ("tianamp" in t and "magnetic" in t):
            return "Tiangen TIANamp Magnetic DNA Kit", ""
        if "bacteria dna" in t:
            return "Tiangen TIANamp Bacteria DNA Kit", ""
        if "maxi" in t:
            return "Tiangen TIANamp Maxi DNA Kit", ""
        if "swab" in t:
            return "Tiangen TIANamp Swab DNA Kit", ""
        if "virus dna/rna" in t or "viral genome" in t:
            return "Tiangen TIANamp Virus DNA/RNA Kit", ""
        if "stool" in t:
            return "Tiangen TIANamp Stool DNA Kit", ""
        if "amp genomic" in t or "dp336" in t:
            return "Tiangen TIANamp Genomic DNA Kit", ""
        return UNCLEAR, "Tiangen named, product not named"

    # ---------------- Magen ------------------------------------------------ #
    if "hipure" in t or "magpure" in t or "mappure" in t or "magen" in t:
        if "hipure" in t:
            if "bacterial" in t:
                return "Magen HiPure Bacterial DNA Kit", ""
            if "fecal" in t:
                return "Magen HiPure Fecal DNA Mini Kit", ""
            if "microbiome" in t:
                return "Magen HiPure Microbiome DNA Kit", ""
            if "maxi" in t:
                return "Magen HiPure HP DNA Maxi Kit", ""
            if "mini" in t:
                return "Magen HiPure Stool DNA Mini Kit", ""
            return "Magen HiPure Stool DNA Kit", ""
        if "pathogen" in t:
            return "Magen MagPure Pathogen DNA/RNA Extraction Kit", ""
        if "circulating" in t:
            return "Magen MagPure Circulating DNA Mini Precast Kit", ""
        if "soil" in t:
            if re.search(r"\blq\b", t):
                return "Magen MagPure Soil DNA LQ Kit", ""
            return "Magen MagPure Soil DNA KF Kit", ""
        if re.search(r"\bkf\b", t):
            return "Magen MagPure Stool DNA KF Kit B", ""
        return "Magen MagPure Stool DNA LQ Kit", \
            "'MapPure' in source" if "mappure" in t else ""

    # ---------------- other named products --------------------------------- #
    if "psp" in t and "stool" in t:
        note = "supplied by Thermo Fisher" if "thermofisher" in t else ""
        if "plus" in t:
            return "Stratec PSP Spin Stool DNA Plus Kit", note
        return "Stratec PSP Spin Stool DNA Kit", note
    if "invimag" in t:
        return "Stratec InviMag Stool DNA Kit", ""
    if "masterpure" in t or "master pure" in t:
        if "yeast" in t:
            note = "yeast cell lysis buffer only" if "lysis buffer" in t else ""
            return "Epicentre MasterPure Yeast DNA Purification Kit", note
        if "gram positive" in t:
            return "Epicentre MasterPure Gram Positive DNA Purification Kit", ""
        if "rna purification" in t and "dna" not in t:
            return "Epicentre MasterPure RNA Purification Kit", ""
        if "complete" in t:
            return ("Epicentre MasterPure Complete DNA and RNA Purification "
                    "Kit", "")
        return "Epicentre MasterPure Kit", "product line only"
    if "genelute" in t:
        return "Sigma-Aldrich GenElute Bacterial Genomic DNA Kit", ""
    if "extract n amp" in t:
        return "Sigma-Aldrich Extract-N-Amp Kit", ""
    if "tri ls" in t:
        return "Sigma-Aldrich TRI Reagent LS", ""
    if "gram positive dna purification" in t and "lucigen" in t:
        return "Lucigen Gram-Positive DNA Purification Kit", ""
    if "norgen" in t or "phage dna" in t:
        if "phage" in t:
            return "Norgen Biotek Phage DNA Isolation Kit", ""
        if "all in one" in t:
            return "Norgen Biotek All-in-One Purification Kit", ""
        if "total rna" in t:
            return "Norgen Biotek Total RNA Purification Kit", ""
        if "urine" in t:
            return ("Norgen Biotek Urine DNA Isolation Kit for Exfoliated Cells "
                    "and Bacteria", "")
        if "collection and preservation" in t:
            return ("Norgen Biotek Stool Nucleic Acid Collection and "
                    "Preservation System", "collection device, not an extraction kit")
        return "Norgen Biotek Stool DNA Isolation Kit", ""
    if "molzym" in t or "molysis" in t or "ultra deep microbiome" in t or \
            "micro dx" in t:
        if "molysis" in t:
            return "Molzym MolYsis Complete5", ""
        if "micro dx" in t:
            return "Molzym Micro-Dx Kit", ""
        if "selectna" in t or "blood pathogen" in t:
            return "Molzym SelectNA Blood Pathogen Kit", ""
        return "Molzym Ultra-Deep Microbiome Prep", ""
    if "nuclisens" in t or "easymag" in t or "emag" in t:
        if "emag" in t and "easymag" not in t:
            return "bioMérieux eMAG", ""
        return "bioMérieux NucliSENS easyMAG", "instrument only"
    if "chemagic" in t:
        if "blood" in t:
            return "PerkinElmer chemagic DNA Blood Kit-96", ""
        if "stool" in t:
            return "PerkinElmer chemagic Stool gDNA Kit", ""
        if "viral" in t:
            return "PerkinElmer chemagic Viral NA/gDNA Kit", ""
        if "360" in t:
            return "PerkinElmer chemagic 360", "instrument only"
        return "PerkinElmer chemagic MSM I", "instrument only"
    if "maglead" in t:
        return "Precision System Science MagLEAD", "instrument only"
    if "magpurix" in t:
        return "Zinexts MagPurix Bacterial DNA Extraction Kit", ""
    if "indispin" in t:
        return "Indical Bioscience IndiSpin Pathogen Kit", ""
    if "agowa" in t or ("mag mini dna isolation" in t and "lgc" in t):
        return "LGC AGOWA mag Mini DNA Isolation Kit", ""
    if "quickgene" in t:
        return "Kurabo QuickGene DNA Tissue Kit S", ""
    if "dnadvance" in t:
        return "Beckman Coulter DNAdvance Kit", ""
    if "genfind" in t:
        return "Beckman Coulter GenFind V3", ""
    if "rnadvance" in t or "agencourt" in t:
        return "Beckman Coulter Agencourt RNAdvance Tissue Kit", ""
    if "instagene" in t:
        return "Bio-Rad InstaGene Matrix", ""
    if "split rna" in t:
        return "Lexogen SPLIT RNA Extraction Kit", ""
    if "rna bee" in t:
        return "AMS Biotechnology RNA-Bee", ""
    if "minibest" in t:
        return "Takara Bio MiniBEST Viral RNA/DNA Extraction Kit", ""
    if "exgene" in t:
        if "clinic" in t:
            return "GeneAll Exgene Clinic SV Mini Kit", ""
        return "GeneAll Exgene Cell SV Kit", ""
    if "easyspin" in t:
        return "Citomed Easyspin Genomic DNA Tissue Kit", ""
    if "echolution" in t:
        return "BioEcho EchoLUTION Tissue DNA Micro Kit", ""
    if "naxtra" in t:
        return "Lybe Scientific Naxtra Blood Total Nucleic Acid Kit", ""
    if "tanbead" in t:
        return "TANBead OptiPure Viral Auto Plate Kit", ""
    if "mgieasy" in t:
        return "MGI MGIEasy Magnetic Beads Virus DNA/RNA Extraction Kit", ""
    if "magicpure" in t:
        return "TransGen Biotech MagicPure Stool and Soil Genomic DNA Kit", ""
    if "easypure" in t:
        if "viral" in t:
            return "TransGen Biotech EasyPure Viral DNA/RNA Kit", ""
        return "TransGen Biotech EasyPure RNA Kit", ""
    if "fastpure" in t:
        if "host removal" in t:
            return "Vazyme FastPure Host Removal and Microbiome DNA Isolation Kit", ""
        note = "magnetic-bead format" if "magnetic" in t else ""
        return "Vazyme FastPure Stool DNA Isolation Kit", note
    if "molpure" in t:
        return "Yeasen MolPure Stool DNA Kit", ""
    if "stoolgen" in t or "stool genomic dna" in t or "cw2092" in t or \
            "cw20925" in t:
        note = "written 'StoolGen'" if "stoolgen" in t else ""
        return "CWBIO Stool Genomic DNA Kit", note
    if "bioteke" in t or "au46111" in t:
        return "BioTeke Fecal Genome DNA Extraction Kit", ""
    if "pathoxtract" in t:
        if "plasma" in t:
            return "WillingMed PathoXtract Plasma Nucleic Acid Kit", ""
        if "hemocyte" in t:
            return ("WillingMed PathoXtract HemoCyte Whole Blood Pathogen "
                    "Enrichment Extraction Kit", "")
        if "cell free" in t or "wyxm03010s" in t or "mcfdna" in t:
            return "WillingMed PathoXtract Cell-Free Nucleic Acid Kit", ""
        if "universal" in t or "wyxm03202s" in t:
            return ("WillingMed PathoXtract Universal Pathogen Enrichment "
                    "Extraction Kit", "")
        if "basic" in t or "wyxm03211s" in t:
            return "WillingMed PathoXtract Basic Pathogen Nucleic Acid Kit", ""
        return "WillingMed PathoXtract", "product line only"
    if "matridx" in t or "mar002" in t or "md013" in t:
        if "cfdna" in t or "md005" in t:
            return "MatriDx cfDNA Extraction Kit", ""
        if "library preparation" in t or "ngs" in t:
            return UNCLEAR, "MatriDx library preparation system, not an extraction kit"
        return "MatriDx Nucleic Acid Extraction Kit", ""
    if "ngsmaster" in t:
        return UNCLEAR, "automated library preparation system, not an extraction kit"
    if "genskey" in t:
        if "micro" in t or "1901" in t:
            return "Genskey Micro DNA Kit", ""
        return "Genskey nucleic acid extraction kit", "product not named"
    if "magigene" in t:
        return "Magigene DNA Isolation Kit", ""
    if "capitalbio" in t or "mapmi" in t:
        return ("CapitalBio MAPMI Sample Preparation and Nucleic Acid "
                "Extraction Kit", "")
    if "guhe" in t:
        return "Hangzhou Guhe Nucleic Acid Extraction Kit", ""
    if "daan gene" in t:
        return "DAAN Gene Nucleic Acid Extraction or Purification Reagents", ""
    if "ingenigen" in t:
        return "IngeniGen Total Nucleic Acids Extraction Kit", \
            "written 'DNA extraction kit'" if "total nucleic" not in t else ""
    if "dxome" in t:
        return "Dxome Magnetic Circulating DNA Maxi Reagent Kit", ""
    if "syntol" in t or "sorb gmo" in t:
        return "Syntol Sorb-GMO-A Kit", ""
    if "durviz" in t or "real microbiome fecal" in t:
        return "Durviz Real Microbiome Fecal DNA Kit", ""
    if "danagene" in t:
        if "swab" in t:
            return "Danagene Microbiome Swab DNA Kit", ""
        return "Danagene Microbiome Fecal DNA Kit", ""
    if "canvax" in t:
        return ("Canvax Stool Sample Collection & Stabilization Kit",
                "collection kit, not an extraction kit")
    if "bioline" in t or "isolate fecal" in t:
        return "Bioline Isolate Fecal DNA Kit", ""
    if "nordiag" in t or "arrow stool" in t:
        return "NorDiag Arrow Stool DNA Extraction Kit", ""
    if "favorprep" in t:
        return "Favorgen FavorPrep Stool DNA Isolation Kit", ""
    if "longseegen" in t:
        return "Longseegen Mini Stool DNA Isolation Kit", ""
    if "jinshi" in t:
        return "Jinshi MicroDNA Kit", ""
    if "takegene" in t:
        return "Takegene Oral Genomic DNA Extraction Kit", ""
    if "omnigene" in t or "om 501" in t:
        return ("DNA Genotek OMNIgene ORAL OM-501",
                "collection device, not an extraction kit")
    if "humoral microbial" in t or "pmd101" in t:
        return ("Nanjing Practice Medicine Diagnostics Humoral Microbial DNA "
                "Kit", "")
    if "ojx" in t:
        return "Fuzhou OJX magnetic bead pathogen DNA extraction kit", ""
    if "alfa seq" in t:
        return "ALFA-SEQ Advanced Stool DNA Kit", ""
    if "azul" in t:
        return "AZUL Stool DNA Extraction Kit", ""
    if "accugen" in t:
        return "AccuGen Nucleic Acid Extraction Kit", ""
    if "cretmag" in t:
        return "CretMag Power Soil DNA Kit", ""
    if "hiyield" in t:
        return "HiYield Genomic DNA Mini Kit", ""
    if "idtect" in t:
        return "iDTECT Dx Blood", ""
    if "gtx stool" in t:
        return "GTX Stool Kit", "maker not stated"
    if "i genomic" in t and "urine" in t:
        return "iNtRON i-genomic Urine DNA Extraction Mini Kit", ""
    if "sample preparation systemdna" in t or "sample preparation system dna" in t:
        return "Abbott Sample Preparation System DNA", ""

    # ---------------- nothing identifiable --------------------------------- #
    return UNCLEAR, "no kit or method named"


# --------------------------------------------------------------------------- #
# driver
# --------------------------------------------------------------------------- #

def main():
    with open(IN_CSV, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))

    out = []
    for r in rows:
        sid = int(r["string_id"])
        raw = r["value_raw"]
        t = norm(raw)
        if sid in MULTI:
            names, note = MULTI[sid]
            name = " | ".join(names)
            n_named = len(names)
        elif sid in SPECIAL:
            name, note = SPECIAL[sid]
            n_named = 1
        else:
            name, note = classify(t)
            n_named = 0 if name == UNCLEAR else 1
        maker = " | ".join(dict.fromkeys(
            [m for m in (maker_of(p) for p in name.split(" | ")) if m]))
        note = (note or "")[:120]
        out.append({
            "string_id": r["string_id"],
            "value_raw": raw,
            "extraction_kit_norm": name,
            "maker": maker,
            "n_named": n_named,
            "mapping_note": note,
            "_n_projects": int(r["n_projects"]),
        })

    with open(MAP_CSV, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["string_id", "value_raw", "extraction_kit_norm", "maker",
                    "n_named", "mapping_note"])
        for o in out:
            w.writerow([o["string_id"], o["value_raw"], o["extraction_kit_norm"],
                        o["maker"], o["n_named"], o["mapping_note"]])

    agg = defaultdict(lambda: {"maker": "", "n_strings": 0, "n_projects": 0})
    for o in out:
        k = o["extraction_kit_norm"]
        agg[k]["maker"] = o["maker"]
        agg[k]["n_strings"] += 1
        agg[k]["n_projects"] += o["_n_projects"]
    vocab = sorted(agg.items(), key=lambda kv: (-kv[1]["n_projects"], kv[0]))
    with open(VOCAB_CSV, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["extraction_kit_norm", "maker", "n_strings", "n_projects"])
        for k, v in vocab:
            w.writerow([k, v["maker"], v["n_strings"], v["n_projects"]])

    return out, vocab


if __name__ == "__main__":
    main()
