#!/usr/bin/env python3
"""Controlled-name mapping for the deep-pass B1 'library preparation kit / method' field.

Reads  library_kit_strings.csv   (string_id, value_raw, n_projects)
Writes library_kit_map.csv       (one row per input string)
       library_kit_vocab.csv     (one row per controlled name)

Method: the raw string is normalised (case, trademark signs, dash/quote variants,
whitespace) and then matched against ordered rule lists.  First match wins.

  MULTI  - strings that name two or more library methods; the controlled names of
           the components are joined with ' | ' and n_named is set accordingly.
  RULES  - ordered single-kit rules, grouped by maker/product family.
           Within a family the most specific product is tested first.
  Anything left unmatched falls through to 'unclear'.

Controlled name = '<Maker> <product name as the maker writes it>', no catalogue
numbers.  For Oxford Nanopore and PacBio the kit code is part of the name.
Strings that name only a maker or a product family get a '(kit not specified)'
name; strings that name no library method at all get 'unclear'.

NAME2MAKER below is the controlled vocabulary: every name produced by a rule must
be a key of it (asserted at the end of the run).
"""

import csv
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
IN_PATH = os.path.join(HERE, "library_kit_strings.csv")
MAP_PATH = os.path.join(HERE, "library_kit_map.csv")
VOCAB_PATH = os.path.join(HERE, "library_kit_vocab.csv")

UNCLEAR = "unclear"

# --------------------------------------------------------------------------
# controlled vocabulary: controlled name -> maker
# --------------------------------------------------------------------------
NAME2MAKER = {
    # ---- Illumina -------------------------------------------------------
    "Illumina Nextera XT DNA Library Preparation Kit": "Illumina",
    "Illumina Nextera DNA Flex Library Prep Kit": "Illumina",
    "Illumina Nextera DNA Library Preparation Kit": "Illumina",
    "Illumina Nextera Mate Pair Sample Preparation Kit": "Illumina",
    "Illumina Nextera Rapid Capture Enrichment Kit": "Illumina",
    "Illumina Nextera (kit not specified)": "Illumina",
    "Illumina DNA Prep": "Illumina",
    "Illumina DNA Prep with Enrichment": "Illumina",
    "Illumina DNA PCR-Free Prep": "Illumina",
    "Illumina TruSeq DNA PCR-Free Library Prep Kit": "Illumina",
    "Illumina TruSeq Nano DNA Library Prep Kit": "Illumina",
    "Illumina TruSeq DNA Sample Preparation Kit": "Illumina",
    "Illumina TruSeq DNA (kit not specified)": "Illumina",
    "Illumina TruSeq (kit not specified)": "Illumina",
    "Illumina TruSeq Stranded Total RNA Library Prep Kit": "Illumina",
    "Illumina TruSeq RNA Library Prep Kit v2": "Illumina",
    "Illumina TruSeq Small RNA Library Prep Kit": "Illumina",
    "Illumina TruSeq Custom Amplicon Low Input Kit": "Illumina",
    "Illumina Stranded Total RNA Prep Ligation with Ribo-Zero Plus": "Illumina",
    "Illumina ScriptSeq v2 RNA-Seq Library Preparation Kit": "Illumina",
    "Illumina ScriptSeq Complete Kit": "Illumina",
    "Illumina COVIDSeq Test": "Illumina",
    "Illumina Respiratory Pathogen ID/AMR Enrichment Kit": "Illumina",
    "Illumina Viral Surveillance Panel": "Illumina",
    "Illumina NeoPrep Library Prep System": "Illumina",
    "Illumina Genomic DNA Sample Prep Kit": "Illumina",
    "Illumina Paired-End DNA Sample Prep Kit": "Illumina",
    "Illumina 16S Metagenomic Sequencing Library Preparation": "Illumina",
    "Illumina (kit not specified)": "Illumina",
    # ---- NEB ------------------------------------------------------------
    "NEB NEBNext Ultra DNA Library Prep Kit for Illumina": "NEB",
    "NEB NEBNext Ultra II DNA Library Prep Kit for Illumina": "NEB",
    "NEB NEBNext Ultra II FS DNA Library Prep Kit for Illumina": "NEB",
    "NEB NEBNext Ultra II RNA Library Prep Kit for Illumina": "NEB",
    "NEB NEBNext Ultra II Directional RNA Library Prep Kit for Illumina": "NEB",
    "NEB NEBNext Ultra Directional RNA Library Prep Kit for Illumina": "NEB",
    "NEB NEBNext Ultra RNA Library Prep Kit for Illumina": "NEB",
    "NEB NEBNext Ultra II (kit not specified)": "NEB",
    "NEB NEBNext Ultra (kit not specified)": "NEB",
    "NEB NEBNext DNA Library Prep Master Mix Set for Illumina": "NEB",
    "NEB NEBNext DNA Library Prep Master Mix Set for 454": "NEB",
    "NEB NEBNext Fast DNA Fragmentation & Library Prep Set for Ion Torrent": "NEB",
    "NEB NEBNext Multiplex Small RNA Library Prep Set for Illumina": "NEB",
    "NEB NEBNext (kit not specified)": "NEB",
    "NEB (kit not specified)": "NEB",
    # ---- Roche / KAPA ---------------------------------------------------
    "Roche KAPA HyperPlus Kit": "Roche",
    "Roche KAPA HyperPrep Kit": "Roche",
    "Roche KAPA RNA HyperPrep Kit": "Roche",
    "Roche KAPA Stranded mRNA-Seq Kit": "Roche",
    "Roche KAPA HTP Library Preparation Kit": "Roche",
    "Roche KAPA LTP Library Preparation Kit": "Roche",
    "Roche KAPA Library Preparation Kit": "Roche",
    "Roche KAPA (kit not specified)": "Roche",
    "Roche NimbleGen SeqCap EZ Library": "Roche",
    # ---- MGI / BGI ------------------------------------------------------
    "MGI MGIEasy Universal DNA Library Prep Set": "MGI",
    "MGI MGIEasy FS DNA Library Prep Set": "MGI",
    "MGI MGIEasy Fast FS DNA Library Prep Set": "MGI",
    "MGI MGIEasy Cell-free DNA Library Prep Set": "MGI",
    "MGI MGIEasy PCR-Free DNA Library Prep Set": "MGI",
    "MGI MGIEasy RNA Library Prep Set": "MGI",
    "MGI MGIEasy (kit not specified)": "MGI",
    "MGI stLFR Library Prep Kit": "MGI",
    "MGI One-Step DNB Kit": "MGI",
    "BGI Optimal DNA Library Prep Kit": "BGI",
    "BGI PMseq High-throughput Detection Kit for Infectious Pathogens": "BGI",
    # ---- Oxford Nanopore ------------------------------------------------
    "Oxford Nanopore Ligation Sequencing Kit SQK-LSK108": "Oxford Nanopore",
    "Oxford Nanopore Ligation Sequencing Kit SQK-LSK109": "Oxford Nanopore",
    "Oxford Nanopore Ligation Sequencing Kit SQK-LSK110": "Oxford Nanopore",
    "Oxford Nanopore Ligation Sequencing Kit SQK-LSK114": "Oxford Nanopore",
    "Oxford Nanopore Ligation Sequencing Kit (kit code not specified)": "Oxford Nanopore",
    "Oxford Nanopore Native Barcoding Kit SQK-NBD112.24": "Oxford Nanopore",
    "Oxford Nanopore Native Barcoding Kit SQK-NBD114.24": "Oxford Nanopore",
    "Oxford Nanopore Native Barcoding Kit SQK-NBD114.96": "Oxford Nanopore",
    "Oxford Nanopore Native Barcoding Kit (kit code not specified)": "Oxford Nanopore",
    "Oxford Nanopore Rapid Barcoding Kit SQK-RBK004": "Oxford Nanopore",
    "Oxford Nanopore Rapid Barcoding Kit SQK-RBK110.96": "Oxford Nanopore",
    "Oxford Nanopore Rapid Barcoding Kit SQK-RBK114.24": "Oxford Nanopore",
    "Oxford Nanopore Rapid Barcoding Kit (kit code not specified)": "Oxford Nanopore",
    "Oxford Nanopore Rapid PCR Barcoding Kit SQK-RPB004": "Oxford Nanopore",
    "Oxford Nanopore Rapid PCR Barcoding Kit SQK-RPB114.24": "Oxford Nanopore",
    "Oxford Nanopore Rapid PCR Barcoding Kit (kit code not specified)": "Oxford Nanopore",
    "Oxford Nanopore PCR Barcoding Kit SQK-PBK004": "Oxford Nanopore",
    "Oxford Nanopore PCR Sequencing Kit SQK-PSK004": "Oxford Nanopore",
    "Oxford Nanopore Direct RNA Sequencing Kit SQK-RNA001": "Oxford Nanopore",
    "Oxford Nanopore Rapid Sequencing Kit (kit code not specified)": "Oxford Nanopore",
    "Oxford Nanopore (kit not specified)": "Oxford Nanopore",
    # ---- PacBio ---------------------------------------------------------
    "PacBio SMRTbell Express Template Prep Kit 2.0": "PacBio",
    "PacBio SMRTbell Template Prep Kit 1.0": "PacBio",
    # ---- Thermo Fisher --------------------------------------------------
    "Thermo Fisher Ion Plus Fragment Library Kit": "Thermo Fisher",
    "Thermo Fisher Ion Xpress Plus Fragment Library Kit": "Thermo Fisher",
    "Thermo Fisher Ion Xpress Fragment Library Kit": "Thermo Fisher",
    "Thermo Fisher 5500 SOLiD Fragment Library Core Kit": "Thermo Fisher",
    "Thermo Fisher Invitrogen Collibri PS DNA Library Prep Kit for Illumina": "Thermo Fisher",
    "Thermo Fisher (kit not specified)": "Thermo Fisher",
    # ---- Qiagen ---------------------------------------------------------
    "Qiagen QIAseq FX DNA Library Kit": "Qiagen",
    "Qiagen QIAseq FX Single Cell RNA Library Kit": "Qiagen",
    "Qiagen QIAseq Ultralow Input Library Kit": "Qiagen",
    "Qiagen GeneRead DNA Library I Core Kit": "Qiagen",
    # ---- Vazyme ---------------------------------------------------------
    "Vazyme TruePrep DNA Library Prep Kit for Illumina": "Vazyme",
    "Vazyme TruePrep DNA Library Prep Kit V2 for Illumina": "Vazyme",
    "Vazyme TruePrep Flexible DNA Library Prep Kit": "Vazyme",
    "Vazyme VAHTS Universal Plus DNA Library Prep Kit for Illumina": "Vazyme",
    "Vazyme VAHTS Universal Plus DNA Library Prep Kit for Illumina V2": "Vazyme",
    "Vazyme VAHTS Universal Plus DNA Library Prep Kit for MGI": "Vazyme",
    "Vazyme VAHTS Universal DNA Library Prep Kit for Illumina": "Vazyme",
    "Vazyme VAHTS Universal V6 RNA-seq Library Prep Kit for Illumina": "Vazyme",
    # ---- Yeasen ---------------------------------------------------------
    "Yeasen Hieff NGS OnePot II DNA Library Prep Kit for MGI": "Yeasen",
    "Yeasen Hieff NGS OnePot II DNA Library Prep Kit for Illumina": "Yeasen",
    "Yeasen Hieff NGS OnePot Pro DNA Library Prep Kit for Illumina": "Yeasen",
    "Yeasen Hieff NGS Fast Tagment DNA Library Prep Kit for Illumina": "Yeasen",
    # ---- Swift / IDT ----------------------------------------------------
    "Swift Biosciences Accel-NGS 1S Plus DNA Library Kit": "Swift Biosciences",
    "Swift Biosciences Accel-NGS 2S Plus DNA Library Kit": "Swift Biosciences",
    "Swift Biosciences Accel-NGS 2S PCR-Free DNA Library Kit": "Swift Biosciences",
    "Swift Biosciences Accel-NGS 2S Turbo DNA Library Kit": "Swift Biosciences",
    "Swift Biosciences Accel-NGS 2S (kit not specified)": "Swift Biosciences",
    "IDT xGen DNA Library Preparation Kit": "IDT",
    "IDT Lotus DNA Library Prep Kit": "IDT",
    "IDT (kit not specified)": "IDT",
    # ---- Agilent --------------------------------------------------------
    "Agilent SureSelect XT Target Enrichment System": "Agilent",
    "Agilent SureSelect XT HS Target Enrichment System": "Agilent",
    "Agilent SureSelect XT HS2 DNA Reagent Kit": "Agilent",
    "Agilent SureSelect XT Low Input Target Enrichment System": "Agilent",
    "Agilent SureSelect XT2 Target Enrichment System": "Agilent",
    # ---- NuGEN / Tecan --------------------------------------------------
    "NuGEN Ovation Ultralow System V2": "NuGEN",
    "NuGEN Ovation Ultralow Library System": "NuGEN",
    "NuGEN Ovation Ultralow DR Multiplex System": "NuGEN",
    "NuGEN Ovation RNA-Seq System V2": "NuGEN",
    "NuGEN Ovation SoLo RNA-Seq System": "NuGEN",
    "NuGEN Trio RNA-Seq Library Preparation Kit": "NuGEN",
    "NuGEN Celero DNA-Seq Kit": "NuGEN",
    "NuGEN Allegro Targeted Genotyping V2": "NuGEN",
    "Tecan Revelo RNA-Seq Library Prep Kit": "Tecan",
    "Tecan Revelo (kit not specified)": "Tecan",
    "Tecan MagicPrep NGS": "Tecan",
    # ---- Takara ---------------------------------------------------------
    "Takara Bio ThruPLEX DNA-Seq Kit": "Takara Bio",
    "Takara Bio SMART-Seq Stranded Kit": "Takara Bio",
    "Takara Bio SMARTer Stranded RNA-Seq Kit": "Takara Bio",
    "Takara Bio SMARTer Stranded Total RNA-Seq Kit v2 - Pico Input Mammalian": "Takara Bio",
    "Takara Bio PrepX DNA Library Kit": "Takara Bio",
    "Takara Bio PrepX RNA-Seq for Illumina Library Preparation Kit": "Takara Bio",
    "Takara Bio PicoPLEX DNA-seq Kit": "Takara Bio",
    # ---- Bioo Scientific / NEXTFLEX -------------------------------------
    "Bioo Scientific NEXTFLEX Rapid DNA-Seq Kit": "Bioo Scientific",
    "Bioo Scientific NEXTFLEX Rapid XP DNA-Seq Kit": "Bioo Scientific",
    "Bioo Scientific NEXTFLEX Cell Free DNA-Seq Kit V2": "Bioo Scientific",
    "Bioo Scientific NEXTFLEX DNA Sequencing Kit": "Bioo Scientific",
    "Bioo Scientific NEXTFLEX (kit not specified)": "Bioo Scientific",
    # ---- other suppliers -------------------------------------------------
    "Twist Bioscience Library Preparation EF Kit": "Twist Bioscience",
    "Twist Bioscience Library Preparation EF Kit 1.0": "Twist Bioscience",
    "Twist Bioscience Total Nucleic Acids Library Preparation EF Kit 2.0": "Twist Bioscience",
    "Twist Bioscience Comprehensive Viral Research Panel": "Twist Bioscience",
    "Twist Bioscience (kit not specified)": "Twist Bioscience",
    "seqWell plexWell 384 Library Preparation Kit": "seqWell",
    "seqWell plexWell LP384 Library Preparation Kit": "seqWell",
    "Watchmaker Genomics DNA Library Prep Kit": "Watchmaker Genomics",
    "Watchmaker Genomics (kit not specified)": "Watchmaker Genomics",
    "Claret Bioscience SRSLY PicoPlus DNA NGS Library Preparation Kit": "Claret Bioscience",
    "10x Genomics Chromium Genome Reagent Kit v1": "10x Genomics",
    "10x Genomics Chromium (kit not specified)": "10x Genomics",
    "Ultima Genomics PCR-Free WGS Library Preparation Protocol": "Ultima Genomics",
    "Beckman Coulter SPRIworks Fragment Library System": "Beckman Coulter",
    "Phase Genomics ProxiMeta Hi-C Kit": "Phase Genomics",
    "Jumpcode Genomics CRISPRclean Plus Stranded Total RNA Prep with rRNA Depletion": "Jumpcode Genomics",
    "ABclonal Rapid Plus DNA Lib Prep Kit for Illumina": "ABclonal",
    "ABclonal Rapid Plus DNA Lib Prep Kit for Illumina V2": "ABclonal",
    "MatriDx NGSmaster Automatic Library Preparation System": "MatriDx",
    "MatriDx Cell-free DNA Library Preparation Kit": "MatriDx",
    "MatriDx Total DNA Library Preparation Kit": "MatriDx",
    "MatriDx (kit not specified)": "MatriDx",
    "KingCreate Pathogenic Metagenomic DNA Detection Kit": "KingCreate",
    "KingCreate Library Construction Kit": "KingCreate",
    "Genskey NGS Library Construction Kit": "Genskey",
    "Novogene NGS DNA Library Prep Set": "Novogene",
    "Novogene Universal Plus DNA Library Prep Kit": "Novogene",
    "Westburg NGS DNA Library Prep Kit": "Westburg",
    "Sagene Biotechnology DNA Library Kit": "Sagene Biotechnology",
    "ExCell Bio TrueLib DNA Library Rapid Prep Kit for Illumina": "ExCell Bio",
    "IngeniGen DNA Library Prep Kit": "IngeniGen",
    "IngeniGen XMKbio DNA-seq Library Prep Kit": "IngeniGen",
    "Celemics EP Kit": "Celemics",
    "Willingmed PathoXtract": "Willingmed",
    "Tiangen (kit not specified)": "Tiangen",
    "Fuzhou OJX Biotechnology (kit not specified)": "Fuzhou OJX Biotechnology",
    "iGenomX Riptide High Throughput Rapid DNA Library Prep Kit": "iGenomX",
    "OncoScreen Plus 520-gene panel": "unknown",
    "AccuNext Stranded Single Cell and Low Input RNA-seq Library Prep Kit for Illumina": "unknown",
    # ---- in-house / published methods -----------------------------------
    "Hackflex": "in-house",
    "in-house Tn5 tagmentation": "in-house",
    "in-house amplicon library preparation": "in-house",
    "in-house Illumina library preparation": "in-house",
    # ---- nothing named ---------------------------------------------------
    UNCLEAR: "",
}

# --------------------------------------------------------------------------
# normalisation
# --------------------------------------------------------------------------
DASHES = "\u2010\u2011\u2012\u2013\u2014\u2015\u2212"


def normalise(s):
    for ch in DASHES:
        s = s.replace(ch, "-")
    s = s.replace("\u2019", "'").replace("\u2018", "'")
    s = s.replace("\u201c", '"').replace("\u201d", '"')
    for ch in ("\u00ae", "\u2122", "\u00a9", "\u00a0"):
        s = s.replace(ch, " " if ch == "\u00a0" else "")
    s = s.lower()
    s = re.sub(r"\s+", " ", s).strip()
    return s


# handy fragments
XT = r"(?:next\s*-?\s*era\s*-?\s*xt|nextra\s*-?\s*xt|nextera\s*xt)"
TS = r"tru\s*e?\s*-?\s*seq"
ULTRA2 = r"ultra\s*(?:tm)?\s*(?:ii|ll|2)\b"
ULTRA = r"ultra(?!low)\s*(?:tm)?"
NEBTAG = r"nebnext|neb\s*next|new england|\bneb\b"

# --------------------------------------------------------------------------
# MULTI: strings naming two or more library methods
# (pattern, joined controlled name, n_named, note)
# --------------------------------------------------------------------------
MULTI = [
    (r"nextera xt and illumina truseq dna pcr-free",
     "Illumina Nextera XT DNA Library Preparation Kit | Illumina TruSeq DNA PCR-Free Library Prep Kit | Roche KAPA HyperPrep Kit", 3, ""),
    (r"kapa hyperplus; nextera dna flex",
     "Roche KAPA HyperPlus Kit | Illumina Nextera DNA Flex Library Prep Kit | Qiagen QIAseq FX DNA Library Kit", 3, ""),
    (r"hyperprep kit.*illumina dna prep|hyper prep kits.*illumina dna prep",
     "Roche KAPA HyperPrep Kit | Illumina DNA Prep", 2, ""),
    (r"kapa hyper prep kit.*trueprep dna library prep kit v2",
     "Roche KAPA HyperPrep Kit | Vazyme TruePrep DNA Library Prep Kit V2 for Illumina", 2, ""),
    (r"truseq dna sample prep kit.*kapa library preparation kit",
     "Illumina TruSeq DNA Sample Preparation Kit | Roche KAPA Library Preparation Kit", 2, ""),
    (r"smarter stranded total rna-seq kit v2.*kapa hyperprep",
     "Takara Bio SMARTer Stranded Total RNA-Seq Kit v2 - Pico Input Mammalian | Roche KAPA HyperPrep Kit", 2, "NEBNext oligos also named"),
    (r"smarter.*trio rna-seq|trio rna-seq.*smarter",
     "Takara Bio SMARTer Stranded Total RNA-Seq Kit v2 - Pico Input Mammalian | NuGEN Trio RNA-Seq Library Preparation Kit", 2, ""),
    (r"truseq stranded ?total ?rna.*trio rna-seq",
     "Illumina TruSeq Stranded Total RNA Library Prep Kit | NuGEN Trio RNA-Seq Library Preparation Kit", 2, ""),
    (r"truseq stranded total rna / ovation solo",
     "Illumina TruSeq Stranded Total RNA Library Prep Kit | NuGEN Ovation SoLo RNA-Seq System", 2, ""),
    (r"nextera.*xt.*trio rna-seq",
     "Illumina Nextera XT DNA Library Preparation Kit | NuGEN Trio RNA-Seq Library Preparation Kit", 2, ""),
    (r"truseq total rna preparation.*nextera xt",
     "Illumina TruSeq Stranded Total RNA Library Prep Kit | Illumina Nextera XT DNA Library Preparation Kit", 2, "paper writes 'TruSeq total RNA preparation'"),
    (r"trueseq nano dna library prep kit and swift turbo 2s",
     "Illumina TruSeq Nano DNA Library Prep Kit | Swift Biosciences Accel-NGS 2S Turbo DNA Library Kit", 2, ""),
    (r"td503\).*vahts universal v6",
     "Vazyme TruePrep DNA Library Prep Kit V2 for Illumina | Vazyme VAHTS Universal V6 RNA-seq Library Prep Kit for Illumina", 2, ""),
    (r"ultra ii rna.*ultra ii fs dna",
     "NEB NEBNext Ultra II RNA Library Prep Kit for Illumina | NEB NEBNext Ultra II FS DNA Library Prep Kit for Illumina", 2, ""),
    (r"ultra ii rna and dna library",
     "NEB NEBNext Ultra II RNA Library Prep Kit for Illumina | NEB NEBNext Ultra II DNA Library Prep Kit for Illumina", 2, ""),
    (r"ultra dna library prep kit or the nebnext ultra ii dna",
     "NEB NEBNext Ultra DNA Library Prep Kit for Illumina | NEB NEBNext Ultra II DNA Library Prep Kit for Illumina", 2, ""),
    (r"nebnext ultra or nebnext ultra ii fs",
     "NEB NEBNext Ultra (kit not specified) | NEB NEBNext Ultra II FS DNA Library Prep Kit for Illumina", 2, ""),
    (r"ultra dna library prep kit for illumina / nebnext ultra ii dna",
     "NEB NEBNext Ultra DNA Library Prep Kit for Illumina | NEB NEBNext Ultra II DNA Library Prep Kit for Illumina", 2, ""),
    (r"ultra ?tm dna library prep kit \(neb, us\) \(batch 1\)",
     "NEB NEBNext Ultra DNA Library Prep Kit for Illumina | Bioo Scientific NEXTFLEX Rapid DNA-Seq Kit", 2, "different kit per batch"),
    (r"illumina dna prep kit and the illumina stranded total rna",
     "Illumina DNA Prep | Illumina Stranded Total RNA Prep Ligation with Ribo-Zero Plus", 2, ""),
    (r"truseq stranded total rna prep.*illumina stranded total library prep",
     "Illumina TruSeq Stranded Total RNA Library Prep Kit | Illumina Stranded Total RNA Prep Ligation with Ribo-Zero Plus", 2, ""),
    (r"covidseq.*nextera xt",
     "Illumina COVIDSeq Test | Illumina Nextera XT DNA Library Preparation Kit", 2, "different kit per sample set"),
    (r"nextera flex or igenomx riptide",
     "Illumina Nextera DNA Flex Library Prep Kit | iGenomX Riptide High Throughput Rapid DNA Library Prep Kit", 2, ""),
    (r"nextera flex or nextera xt",
     "Illumina Nextera DNA Flex Library Prep Kit | Illumina Nextera XT DNA Library Preparation Kit", 2, ""),
    (r"nextera xt dna library prep kit \(illumina\) and nextera flex",
     "Illumina Nextera XT DNA Library Preparation Kit | Illumina Nextera DNA Flex Library Prep Kit", 2, ""),
    (r"nextera dna sample preparation kit and nextera xt",
     "Illumina Nextera DNA Library Preparation Kit | Illumina Nextera XT DNA Library Preparation Kit", 2, ""),
    (r"nextera xt protocol and nextera dna sample preparation",
     "Illumina Nextera XT DNA Library Preparation Kit | Illumina Nextera DNA Library Preparation Kit", 2, ""),
    (r"nextera xt library preparation kit and the nextera mate pair",
     "Illumina Nextera XT DNA Library Preparation Kit | Illumina Nextera Mate Pair Sample Preparation Kit", 2, ""),
    (r"nexteraxt dna library preparation kit / native barcoding",
     "Illumina Nextera XT DNA Library Preparation Kit | Oxford Nanopore Native Barcoding Kit (kit code not specified)", 2, "V14 barcoding chemistry; kit code not given"),
    (r"sureselectxt / sureselectxt-hs2",
     "Agilent SureSelect XT Target Enrichment System | Agilent SureSelect XT HS2 DNA Reagent Kit", 2, ""),
    (r"cell-?free dna library preparation kit.*total dna library preparation kit",
     "MatriDx Cell-free DNA Library Preparation Kit | MatriDx Total DNA Library Preparation Kit", 2, "different kit per sample type"),
    (r"rapid barcoding sequencing or nanopore genomic dna ligation",
     "Oxford Nanopore Rapid Barcoding Kit (kit code not specified) | Oxford Nanopore Ligation Sequencing Kit (kit code not specified)", 2, ""),
    (r"sqk-lsk109 or sqk-lsk110",
     "Oxford Nanopore Ligation Sequencing Kit SQK-LSK109 | Oxford Nanopore Ligation Sequencing Kit SQK-LSK110", 2, ""),
    (r"sqk-nbd114-24 and sqk-nbd114-96",
     "Oxford Nanopore Native Barcoding Kit SQK-NBD114.24 | Oxford Nanopore Native Barcoding Kit SQK-NBD114.96", 2, ""),
    (r"sqk-lsk109 for r9, and sqk-lsk114",
     "Oxford Nanopore Ligation Sequencing Kit SQK-LSK109 | Oxford Nanopore Ligation Sequencing Kit SQK-LSK114", 2, "native barcoding expansions also named"),
    (r"sqk-rpb004 / sqk-rbk114",
     "Oxford Nanopore Rapid PCR Barcoding Kit SQK-RPB004 | Oxford Nanopore Rapid Barcoding Kit SQK-RBK114.24", 2, ""),
    (r"watchmaker biosciences and twist bioscience",
     "Watchmaker Genomics (kit not specified) | Twist Bioscience (kit not specified)", 2, "makers only; no product named"),
]

# --------------------------------------------------------------------------
# RULES: ordered single-method rules (pattern, controlled name, note)
# --------------------------------------------------------------------------
RULES = [
    # ---- in-house / published methods (before the kit families they cite) --
    (r"hackflex", "Hackflex", ""),
    (r"tn5", "in-house Tn5 tagmentation", ""),
    (r"custom primer set targeting", "in-house amplicon library preparation", ""),
    (r"kircher|half-udg treated|p5/p7 barcoded adapters",
     "in-house Illumina library preparation", "published in-house protocol"),
    # ---- Oxford Nanopore -------------------------------------------------
    (r"sqk-?lsk\s*-?114", "Oxford Nanopore Ligation Sequencing Kit SQK-LSK114", ""),
    (r"sqk-?lsk\s*-?110", "Oxford Nanopore Ligation Sequencing Kit SQK-LSK110", ""),
    (r"sqk-?lsk\s*-?109|lsk-109", "Oxford Nanopore Ligation Sequencing Kit SQK-LSK109", ""),
    (r"sqk-?lsk\s*-?108", "Oxford Nanopore Ligation Sequencing Kit SQK-LSK108", ""),
    (r"sqk-?nbd\s*-?114[.-]?96", "Oxford Nanopore Native Barcoding Kit SQK-NBD114.96", ""),
    (r"sqk-?nbd\s*-?114", "Oxford Nanopore Native Barcoding Kit SQK-NBD114.24", ""),
    (r"sqk-?nbd\s*-?112", "Oxford Nanopore Native Barcoding Kit SQK-NBD112.24", ""),
    (r"sqk-?rbk\s*-?114|sqk-?rbk114", "Oxford Nanopore Rapid Barcoding Kit SQK-RBK114.24", ""),
    (r"sqk-?rbk\s*-?110", "Oxford Nanopore Rapid Barcoding Kit SQK-RBK110.96", ""),
    (r"sqk-?r[bk]{2}004", "Oxford Nanopore Rapid Barcoding Kit SQK-RBK004", ""),
    (r"sqk-?(?:rpb|prb)\s*-?114", "Oxford Nanopore Rapid PCR Barcoding Kit SQK-RPB114.24", ""),
    (r"sqk-?rpb\s*-?004", "Oxford Nanopore Rapid PCR Barcoding Kit SQK-RPB004", ""),
    (r"sqk-?pbk\s*-?004", "Oxford Nanopore PCR Barcoding Kit SQK-PBK004", ""),
    (r"sqk-?psk\s*-?004", "Oxford Nanopore PCR Sequencing Kit SQK-PSK004", ""),
    (r"sqk-?rna001", "Oxford Nanopore Direct RNA Sequencing Kit SQK-RNA001", ""),
    (r"ligation sequencing|genomic dna by ligation|ligation library preparation|dna ligation kit",
     "Oxford Nanopore Ligation Sequencing Kit (kit code not specified)", ""),
    ((r"native barcoding", r"kit 24 v14"), "Oxford Nanopore Native Barcoding Kit SQK-NBD114.24",
     "V14 24-plex native barcoding kit; code not given in string"),
    (r"native barcoding", "Oxford Nanopore Native Barcoding Kit (kit code not specified)", ""),
    (r"rapid pcr barcoding", "Oxford Nanopore Rapid PCR Barcoding Kit (kit code not specified)", ""),
    (r"rapid barcoding", "Oxford Nanopore Rapid Barcoding Kit (kit code not specified)", ""),
    (r"rapid sequencing kit", "Oxford Nanopore Rapid Sequencing Kit (kit code not specified)", ""),
    (r"nanopore", "Oxford Nanopore (kit not specified)", ""),
    # ---- PacBio ----------------------------------------------------------
    (r"smrtbell express template prep kit 2", "PacBio SMRTbell Express Template Prep Kit 2.0", ""),
    (r"smrtbell template prep kit 1", "PacBio SMRTbell Template Prep Kit 1.0", ""),
    # ---- sequencing / clustering reagents, not library prep --------------
    (r"^5500 solid.*fragment library core kit, solid fragment library barcoding",
     "Thermo Fisher 5500 SOLiD Fragment Library Core Kit", "SOLiD sequencing and barcoding kits also named"),
    (r"^kapa library preparation kit or the truseq sbs$", "Roche KAPA Library Preparation Kit",
     "TruSeq SBS (a sequencing kit) also named"),
    (r"miseq reagent kit|novaseq|dnbseq-g400|mgiseq-200|pe cluster kit|truseq sbs|^600-cycle|single-end sequencing kit|paired-end kit \(illumina\)",
     UNCLEAR, "names only a sequencing/clustering reagent kit"),
    # ---- Jumpcode (contains 'stranded total rna prep') -------------------
    (r"crisprclean", "Jumpcode Genomics CRISPRclean Plus Stranded Total RNA Prep with rRNA Depletion", ""),
    # ---- named Illumina products ----------------------------------------
    (r"covidseq", "Illumina COVIDSeq Test", ""),
    (r"respiratory pathogen id", "Illumina Respiratory Pathogen ID/AMR Enrichment Kit", "enrichment kit; base library kit not named"),
    (r"viral surveillance panel", "Illumina Viral Surveillance Panel", "enrichment panel; base library kit not named"),
    (r"neoprep", "Illumina NeoPrep Library Prep System", ""),
    (r"scriptseq ?(?:tm)? ?complete", "Illumina ScriptSeq Complete Kit", ""),
    (r"scriptseq", "Illumina ScriptSeq v2 RNA-Seq Library Preparation Kit", ""),
    (r"16s metagenomic sequencing library preparation|standard protocol from illumina \(16s",
     "Illumina 16S Metagenomic Sequencing Library Preparation", "16S amplicon protocol"),
    (r"genomic dna sample prep kit", "Illumina Genomic DNA Sample Prep Kit", ""),
    (r"^illumina paired-end library preparation kit$", "Illumina Paired-End DNA Sample Prep Kit", ""),
    (r"nextera.*rapid capture", "Illumina Nextera Rapid Capture Enrichment Kit", ""),
    # ---- Illumina DNA Prep family ---------------------------------------
    (r"dna prep with enrichment|prep with enrichment tagmentation",
     "Illumina DNA Prep with Enrichment", ""),
    ((r"^(?!.*tru\s*e?-?seq)", r"dna pcr-?free (?:prep|library prep)"), "Illumina DNA PCR-Free Prep", ""),
    (r"^illumina nextera flex \(now \"dna prep\"\) kit$",
     "Illumina Nextera DNA Flex Library Prep Kit", "paper's name is Nextera Flex"),
    (r"\bdna prep\b|dna-prep|dna prep-", "Illumina DNA Prep", ""),
    (r"^illumina dna seq protocol \(formerly, nextera flex\)$", "Illumina DNA Prep", ""),
    (r"illumina dna lp tagmentation|illumina preparation kit \(m\) tagmentation",
     "Illumina DNA Prep", "paper writes a short form of the product name"),
    (r"nextera kit \(illumina, catalogue no\. 20060060\)", "Illumina DNA Prep", "catalogue number identifies Illumina DNA Prep"),
    # ---- Illumina TruSeq family ------------------------------------------
    ((TS, r"nano"), "Illumina TruSeq Nano DNA Library Prep Kit", ""),
    (r"^illumina's dna nano library preparation protocol$", "Illumina TruSeq Nano DNA Library Prep Kit", "paper writes 'DNA Nano'"),
    (TS + r".*pcr[ -]?free|pcr[ -]?free.*" + TS, "Illumina TruSeq DNA PCR-Free Library Prep Kit", ""),
    (TS + r".*small rna", "Illumina TruSeq Small RNA Library Prep Kit", ""),
    (TS + r".*custom amplicon", "Illumina TruSeq Custom Amplicon Low Input Kit", ""),
    ((TS, r"total ?rna|totalrna|total stranded rna"), "Illumina TruSeq Stranded Total RNA Library Prep Kit", ""),
    ((TS, r"stranded", r"ribo-?zero"), "Illumina TruSeq Stranded Total RNA Library Prep Kit",
     "paper writes 'TruSeq stranded libraries ... RiboZero'"),
    (TS + r".*rna (?:sample|library) prep|" + TS + r".*rna sample preparation",
     "Illumina TruSeq RNA Library Prep Kit v2", ""),
    (TS + r".*dna.*(?:sample prep|dnaseq sample|sample preparation)|" + TS + r" dna-seq dna sample",
     "Illumina TruSeq DNA Sample Preparation Kit", ""),
    ((TS, r"\bdna\b", r"\b(?:ht|lt)\b"), "Illumina TruSeq DNA Sample Preparation Kit",
     "HT/LT format of the TruSeq DNA Sample Prep kit"),
    (TS + r".*dna library preparation kit v2", "Illumina TruSeq DNA Sample Preparation Kit", "v2 of the TruSeq DNA Sample Prep kit"),
    (TS + r".*\bdna\b", "Illumina TruSeq DNA (kit not specified)", ""),
    (TS, "Illumina TruSeq (kit not specified)", ""),
    # ---- Illumina Stranded RNA Prep --------------------------------------
    (r"stranded total rna prep|stranded total library prep|illumina stranded total rna",
     "Illumina Stranded Total RNA Prep Ligation with Ribo-Zero Plus", ""),
    # ---- Nextera Flex / DNA Prep predecessors ----------------------------
    (r"nextera xt flex|nextera xt and flex", "Illumina Nextera (kit not specified)",
     "string conflates Nextera XT and Nextera Flex"),
    (r"nextera ?(?:dna )?flex|nexteraflex|nexter flex|nextra dna flex|dna flex|^illumina dna \(flex\)$|^illumina flex$",
     "Illumina Nextera DNA Flex Library Prep Kit", ""),
    # ---- Nextera XT -------------------------------------------------------
    (XT, "Illumina Nextera XT DNA Library Preparation Kit", ""),
    (r"illumina library \(illumina, fc-131-1096\)", "Illumina Nextera XT DNA Library Preparation Kit",
     "catalogue number FC-131-1096 identifies Nextera XT"),
    # ---- Nextera DNA / Nextera family -------------------------------------
    (r"nextera library prep kit \(illumina, catalog number fc-121",
     "Illumina Nextera DNA Library Preparation Kit", "catalogue number identifies Nextera DNA"),
    ((r"nextera", r"\bxt\b"), "Illumina Nextera XT DNA Library Preparation Kit", ""),
    (r"nextera[ -]?dna|nextera-dna-sample", "Illumina Nextera DNA Library Preparation Kit", ""),
    (r"nextera", "Illumina Nextera (kit not specified)", ""),
    # ---- NEB (order-independent: maker tag may follow the product name) ---
    ((NEBTAG, r"multiplex small rna"), "NEB NEBNext Multiplex Small RNA Library Prep Set for Illumina", ""),
    ((NEBTAG, r"fast dna fragmentation"), "NEB NEBNext Fast DNA Fragmentation & Library Prep Set for Ion Torrent", ""),
    ((NEBTAG, r"master set for 454|library preparation kit for 454|e6070"),
     "NEB NEBNext DNA Library Prep Master Mix Set for 454", ""),
    ((NEBTAG, r"master (?:mix )?set|sample prep master mix"),
     "NEB NEBNext DNA Library Prep Master Mix Set for Illumina", ""),
    (r"fs dna module", "NEB NEBNext Ultra II FS DNA Library Prep Kit for Illumina",
     "paper names the NEBNext Ultra II FS DNA Module"),
    ((NEBTAG, ULTRA, r"\bfs\b"), "NEB NEBNext Ultra II FS DNA Library Prep Kit for Illumina", ""),
    ((NEBTAG, ULTRA2, r"directional rna"), "NEB NEBNext Ultra II Directional RNA Library Prep Kit for Illumina", ""),
    (r"directional rna.*e7760|e7550", "NEB NEBNext Ultra II Directional RNA Library Prep Kit for Illumina", ""),
    ((NEBTAG, ULTRA2, r"\brna\b"), "NEB NEBNext Ultra II RNA Library Prep Kit for Illumina", ""),
    ((NEBTAG, ULTRA2, r"dna|e7645"), "NEB NEBNext Ultra II DNA Library Prep Kit for Illumina", ""),
    (r"e7645", "NEB NEBNext Ultra II DNA Library Prep Kit for Illumina", ""),
    (r"spri hd", "NEB NEBNext Ultra II (kit not specified)", "SPRI HD kit also named"),
    ((NEBTAG, ULTRA2), "NEB NEBNext Ultra II (kit not specified)", ""),
    ((NEBTAG, ULTRA, r"directional rna"), "NEB NEBNext Ultra Directional RNA Library Prep Kit for Illumina", ""),
    ((NEBTAG, ULTRA, r"\brna\b"), "NEB NEBNext Ultra RNA Library Prep Kit for Illumina", ""),
    ((NEBTAG, ULTRA, r"dna|e7370"), "NEB NEBNext Ultra DNA Library Prep Kit for Illumina", ""),
    ((NEBTAG, ULTRA), "NEB NEBNext Ultra (kit not specified)", ""),
    (r"end repair module", "NEB NEBNext (kit not specified)", "individual NEBNext enzyme modules"),
    (r"nebnext|neb\s*next", "NEB NEBNext (kit not specified)", ""),
    (r"new england|\bneb\b", "NEB (kit not specified)", ""),
    # ---- Roche / KAPA (order-independent) ---------------------------------
    ((r"kapa", r"polymerase"), UNCLEAR, "names only a PCR polymerase kit"),
    (r"rna hyperprep", "Roche KAPA RNA HyperPrep Kit", ""),
    ((r"kapa", r"mrna-seq"), "Roche KAPA Stranded mRNA-Seq Kit", "catalogue number KK8421"),
    ((r"kapa", r"hyper ?stranded"), "Roche KAPA (kit not specified)",
     "paper writes 'Hyper Stranded'; product not identifiable"),
    (r"hyper\s*-?\s*plus", "Roche KAPA HyperPlus Kit", ""),
    (r"hyper\s*-?\s*prep|dna hyperprep|kapa hyper kit|kapa hyper library|hyper library construction|kapa hyper\b",
     "Roche KAPA HyperPrep Kit", ""),
    ((r"kapa", r"\bhtp\b|high[- ]throughput"), "Roche KAPA HTP Library Preparation Kit", ""),
    ((r"kapa", r"\bltp\b|low throughput|low-input"), "Roche KAPA LTP Library Preparation Kit", ""),
    (r"kapa library preparation kit|library preparation kit from kapa|kapa library prep",
     "Roche KAPA Library Preparation Kit", ""),
    (r"\bkapa\b", "Roche KAPA (kit not specified)", ""),
    (r"seqcap ez", "Roche NimbleGen SeqCap EZ Library", ""),
    # ---- Vazyme (before MGI: 'VAHTS ... for MGI') -------------------------
    (r"vahts universal plus.*for mgi|vahts universal plus dna library prep kit \(nd[m]?617",
     "Vazyme VAHTS Universal Plus DNA Library Prep Kit for MGI", ""),
    (r"vahts universal plus.*v2", "Vazyme VAHTS Universal Plus DNA Library Prep Kit for Illumina V2", ""),
    (r"vahts universal plus", "Vazyme VAHTS Universal Plus DNA Library Prep Kit for Illumina", ""),
    (r"vahts universal v6", "Vazyme VAHTS Universal V6 RNA-seq Library Prep Kit for Illumina", ""),
    (r"vahts universal", "Vazyme VAHTS Universal DNA Library Prep Kit for Illumina", ""),
    (r"trueprep.*(?:v2|td50[23])|truprep.*v2", "Vazyme TruePrep DNA Library Prep Kit V2 for Illumina", ""),
    (r"trueprep flexible", "Vazyme TruePrep Flexible DNA Library Prep Kit", ""),
    (r"trueprep|truprep", "Vazyme TruePrep DNA Library Prep Kit for Illumina", ""),
    # ---- Yeasen ------------------------------------------------------------
    (r"onepot ii.*for mgi", "Yeasen Hieff NGS OnePot II DNA Library Prep Kit for MGI", ""),
    (r"onepot ii", "Yeasen Hieff NGS OnePot II DNA Library Prep Kit for Illumina", ""),
    (r"onepot pro", "Yeasen Hieff NGS OnePot Pro DNA Library Prep Kit for Illumina", ""),
    (r"fast tagment", "Yeasen Hieff NGS Fast Tagment DNA Library Prep Kit for Illumina", ""),
    # ---- MGI / BGI ---------------------------------------------------------
    (r"mgi.*purification", UNCLEAR, "names only a DNA purification kit"),
    (r"stlfr", "MGI stLFR Library Prep Kit", ""),
    (r"one-?step dnb", "MGI One-Step DNB Kit", "DNB preparation kit"),
    (r"mgieasy universal", "MGI MGIEasy Universal DNA Library Prep Set", ""),
    (r"fast ?fs", "MGI MGIEasy Fast FS DNA Library Prep Set", ""),
    (r"mgieasy fs|mgi fs dna|mgi fs library|mgi'?s fs library", "MGI MGIEasy FS DNA Library Prep Set", ""),
    (r"mgieasy cell-?free", "MGI MGIEasy Cell-free DNA Library Prep Set", ""),
    (r"mgieasy pcr-?free", "MGI MGIEasy PCR-Free DNA Library Prep Set", ""),
    (r"mgieasy rna", "MGI MGIEasy RNA Library Prep Set", ""),
    (r"mgieasy", "MGI MGIEasy (kit not specified)", ""),
    (r"^library prep kit for mgi high-throughput sequencing platform$", UNCLEAR, "no maker or product named"),
    (r"bgi optimal", "BGI Optimal DNA Library Prep Kit", ""),
    (r"pmseq", "BGI PMseq High-throughput Detection Kit for Infectious Pathogens", ""),
    # ---- Qiagen -------------------------------------------------------------
    (r"qiaquick|qiagen microbial dna extraction", UNCLEAR, "names only an extraction/purification kit"),
    (r"fast ?select", UNCLEAR, "names only an rRNA depletion kit"),
    (r"repli-?g", UNCLEAR, "names only a whole-transcriptome amplification kit"),
    (r"qiaseq.*single cell rna|qiaseq fx single cell", "Qiagen QIAseq FX Single Cell RNA Library Kit", ""),
    (r"qiaseq ultralow|qiaseq\u2122? ultralow|ultralow input library kit", "Qiagen QIAseq Ultralow Input Library Kit", ""),
    (r"qiaseq fx|qiaseq dna library preparation protocol|qiagen fx dna library|qiaseq.*fx",
     "Qiagen QIAseq FX DNA Library Kit", ""),
    (r"gene ?read dna library", "Qiagen GeneRead DNA Library I Core Kit", ""),
    # ---- Thermo Fisher -------------------------------------------------------
    (r"ion xpress plus fragment|ion xpress.*plus fragment", "Thermo Fisher Ion Xpress Plus Fragment Library Kit", ""),
    (r"ion xpress fragment", "Thermo Fisher Ion Xpress Fragment Library Kit", ""),
    (r"ion ?plus fragment", "Thermo Fisher Ion Plus Fragment Library Kit", ""),
    (r"solid.*fragment", "Thermo Fisher 5500 SOLiD Fragment Library Core Kit", ""),
    (r"colibri|collibri", "Thermo Fisher Invitrogen Collibri PS DNA Library Prep Kit for Illumina", ""),
    (r"superscript", UNCLEAR, "names only a cDNA synthesis kit"),
    (r"^library prep kit \(thermo fisher\)$", "Thermo Fisher (kit not specified)", ""),
    # ---- Swift / IDT ----------------------------------------------------------
    (r"accel-?\s?ngs 1s", "Swift Biosciences Accel-NGS 1S Plus DNA Library Kit", ""),
    (r"accel-?\s?ngs 2s pcr-?free", "Swift Biosciences Accel-NGS 2S PCR-Free DNA Library Kit", ""),
    (r"accel-?\s?ngs 2s plus", "Swift Biosciences Accel-NGS 2S Plus DNA Library Kit", ""),
    (r"swift 2s? ?turbo|swift 2s turbo|2s\u2122? turbo", "Swift Biosciences Accel-NGS 2S Turbo DNA Library Kit", ""),
    (r"accel", "Swift Biosciences Accel-NGS 2S (kit not specified)", ""),
    (r"xgen dna library", "IDT xGen DNA Library Preparation Kit", ""),
    (r"lotus dna library", "IDT Lotus DNA Library Prep Kit", ""),
    (r"^idt, singapore$", "IDT (kit not specified)", "maker only; no product named"),
    # ---- Agilent ---------------------------------------------------------------
    (r"sureselect ?xt ?hs2|sureselect xths v2|sureselectxt-hs2", "Agilent SureSelect XT HS2 DNA Reagent Kit", ""),
    (r"sureselect ?xt ?hs", "Agilent SureSelect XT HS Target Enrichment System", ""),
    (r"sureselect ?xt ?low input|sureselect low input", "Agilent SureSelect XT Low Input Target Enrichment System", ""),
    (r"sureselect ?xt2", "Agilent SureSelect XT2 Target Enrichment System", ""),
    (r"sureselect", "Agilent SureSelect XT Target Enrichment System", ""),
    # ---- NuGEN / Tecan ----------------------------------------------------------
    (r"ovation ultralow dr|ultralow dr multiplex", "NuGEN Ovation Ultralow DR Multiplex System", ""),
    (r"ovation ultralow.*v2|ultralow system v2|ovation ultralow v2", "NuGEN Ovation Ultralow System V2", ""),
    (r"ovation ultralow|ultralow protocol", "NuGEN Ovation Ultralow Library System", "system version not stated"),
    (r"ovation rna-?seq", "NuGEN Ovation RNA-Seq System V2", ""),
    (r"ovation solo", "NuGEN Ovation SoLo RNA-Seq System", ""),
    (r"trio rna-?seq", "NuGEN Trio RNA-Seq Library Preparation Kit", ""),
    (r"celero", "NuGEN Celero DNA-Seq Kit", ""),
    (r"allegro targeted genotyping", "NuGEN Allegro Targeted Genotyping V2", ""),
    (r"revelo rna-?seq", "Tecan Revelo RNA-Seq Library Prep Kit", ""),
    (r"revelo", "Tecan Revelo (kit not specified)", ""),
    (r"magicprep", "Tecan MagicPrep NGS", ""),
    # ---- Takara ------------------------------------------------------------------
    (r"thruplex", "Takara Bio ThruPLEX DNA-Seq Kit", ""),
    (r"smart-?seq stranded", "Takara Bio SMART-Seq Stranded Kit", ""),
    (r"smarter stranded total rna", "Takara Bio SMARTer Stranded Total RNA-Seq Kit v2 - Pico Input Mammalian", ""),
    (r"smarter stranded rna", "Takara Bio SMARTer Stranded RNA-Seq Kit", ""),
    (r"prepx rna", "Takara Bio PrepX RNA-Seq for Illumina Library Preparation Kit", ""),
    (r"prepx", "Takara Bio PrepX DNA Library Kit", ""),
    (r"picoplex", "Takara Bio PicoPLEX DNA-seq Kit", ""),
    # ---- NEXTFLEX ------------------------------------------------------------------
    (("nextflex", "rapid xp"), "Bioo Scientific NEXTFLEX Rapid XP DNA-Seq Kit", ""),
    (("nextflex", "cell-?free"), "Bioo Scientific NEXTFLEX Cell Free DNA-Seq Kit V2", ""),
    (("nextfle", "rapid dna"), "Bioo Scientific NEXTFLEX Rapid DNA-Seq Kit", ""),
    (("nextflex", "dna sequencing kit"), "Bioo Scientific NEXTFLEX DNA Sequencing Kit", ""),
    (r"nextfle", "Bioo Scientific NEXTFLEX (kit not specified)", ""),
    # ---- other suppliers -------------------------------------------------------------
    (r"comprehensive viral research panel", "Twist Bioscience Comprehensive Viral Research Panel",
     "enrichment panel; base library kit not named"),
    (r"total nucleic acids library preparation ef", "Twist Bioscience Total Nucleic Acids Library Preparation EF Kit 2.0", ""),
    (r"library preparation enzymatic fragmentation kit 1\.0", "Twist Bioscience Library Preparation EF Kit 1.0", ""),
    (r"twist ef library prep", "Twist Bioscience Library Preparation EF Kit", ""),
    (r"twist", "Twist Bioscience (kit not specified)", ""),
    (r"plexwell lp384", "seqWell plexWell LP384 Library Preparation Kit", ""),
    (r"plexwell", "seqWell plexWell 384 Library Preparation Kit", ""),
    (r"watchmaker", "Watchmaker Genomics DNA Library Prep Kit", ""),
    (r"srsly", "Claret Bioscience SRSLY PicoPlus DNA NGS Library Preparation Kit", ""),
    (r"chromium genome v1", "10x Genomics Chromium Genome Reagent Kit v1", ""),
    (r"chromium|10x genomics", "10x Genomics Chromium (kit not specified)", ""),
    (r"ultima genomics", "Ultima Genomics PCR-Free WGS Library Preparation Protocol", ""),
    (r"spriworks", "Beckman Coulter SPRIworks Fragment Library System", ""),
    (r"proximeta", "Phase Genomics ProxiMeta Hi-C Kit", ""),
    (r"rapid plus dna lib prep kit for illumina v2|rapid plus dna lib prep kit for illumina \(rk20255",
     "ABclonal Rapid Plus DNA Lib Prep Kit for Illumina V2", "maker credited differently across papers"),
    (r"rapid plus dna lib", "ABclonal Rapid Plus DNA Lib Prep Kit for Illumina", "maker credited differently across papers"),
    (r"ngsmaster|ngs automatic library preparation", "MatriDx NGSmaster Automatic Library Preparation System", ""),
    (r"cfdna library preparation kit \(matridx|cell-?free dna library preparation kit \(cat\. md007",
     "MatriDx Cell-free DNA Library Preparation Kit", ""),
    (r"total dna(?:/rna)? library preparation kit", "MatriDx Total DNA Library Preparation Kit", ""),
    (r"matridx", "MatriDx (kit not specified)", ""),
    (r"ks619|pathogenic metagenomic dna detection", "KingCreate Pathogenic Metagenomic DNA Detection Kit", ""),
    (r"kingcreate", "KingCreate Library Construction Kit", ""),
    (r"genskey", "Genskey NGS Library Construction Kit", ""),
    (r"universal plus dna library prep kit \(novogene", "Novogene Universal Plus DNA Library Prep Kit", ""),
    (r"novogene", "Novogene NGS DNA Library Prep Set", ""),
    (r"westburg", "Westburg NGS DNA Library Prep Kit", ""),
    (r"sagene", "Sagene Biotechnology DNA Library Kit", ""),
    (r"truelib", "ExCell Bio TrueLib DNA Library Rapid Prep Kit for Illumina", ""),
    (r"xmkbio", "IngeniGen XMKbio DNA-seq Library Prep Kit", ""),
    (r"ingenigen", "IngeniGen DNA Library Prep Kit", ""),
    (r"celemics", "Celemics EP Kit", ""),
    (r"pathoxtract", "Willingmed PathoXtract", ""),
    (r"tiangen", "Tiangen (kit not specified)", "maker only; no product named"),
    (r"fuzhou ojx", "Fuzhou OJX Biotechnology (kit not specified)", "maker only; no product named"),
    (r"riptide", "iGenomX Riptide High Throughput Rapid DNA Library Prep Kit", ""),
    (r"oncoscreen", "OncoScreen Plus 520-gene panel", "enrichment panel; maker not named in string"),
    # ---- not a library method --------------------------------------------------------
    (r"genomiphi|\bwta\b|wta2|transplex|whole transcriptome amplification",
     UNCLEAR, "names only a whole-genome/transcriptome amplification kit"),
    (r"ribo-?zero", UNCLEAR, "names only an rRNA depletion kit"),
    (r"pcr-?free (?:method|technology|genomic dna library)", UNCLEAR, "PCR-free stated; no kit named"),
    # ---- maker-only fallbacks --------------------------------------------------------
    (r"accunext", "AccuNext Stranded Single Cell and Low Input RNA-seq Library Prep Kit for Illumina",
     "maker not named in string"),
    (r"^dna tagmentation kit \(illumina, ca, usa\)$", "Illumina (kit not specified)",
     "tagmentation stated; Illumina product not identifiable"),
    (r"^illumina mrna-seq prep kit$", "Illumina (kit not specified)",
     "RNA kit; Illumina product not identifiable"),
    (r"illumina", "Illumina (kit not specified)", ""),
    (r"\bmgi\b", "MGI MGIEasy (kit not specified)", ""),
]

def _compile(pat):
    """A rule pattern is a string, or a tuple of strings that must all match
    (order-independent AND)."""
    if isinstance(pat, tuple):
        return tuple(re.compile(x) for x in pat)
    return (re.compile(pat),)


RULES_C = [(_compile(p), n, note) for p, n, note in RULES]
MULTI_C = [(re.compile(p), n, k, note) for p, n, k, note in MULTI]

INDEX_RE = re.compile(r"index|indices|indexes|adapter|adaptor|oligos")
MOD_RE = re.compile(r"modified|miniaturi[sz]|1:10|1/2 volume|low-volume|\u00bc")
PCRFREE_RE = re.compile(r"pcr[ -]?free|without amplification|without pcr")


def classify(raw):
    """Return (controlled_name, maker, n_named, note)."""
    s = normalise(raw)
    for rx, name, k, note in MULTI_C:
        if rx.search(s):
            makers = []
            for part in name.split(" | "):
                m = NAME2MAKER[part]
                if m and m not in makers:
                    makers.append(m)
            return name, " | ".join(makers), k, note
    for rxs, name, note in RULES_C:
        if all(rx.search(s) for rx in rxs):
            if not note and name != UNCLEAR:
                if INDEX_RE.search(s) and not name.startswith("Oxford Nanopore"):
                    note = "also names an index/adapter kit"
                elif MOD_RE.search(s):
                    note = "modified or reduced-volume protocol"
                elif PCRFREE_RE.search(s):
                    note = "PCR-free variant of the kit"
            return name, NAME2MAKER[name], 1, note
    return UNCLEAR, "", 1, "no library preparation method named"


def main():
    with open(IN_PATH, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))

    out = []
    for r in rows:
        name, maker, k, note = classify(r["value_raw"])
        for part in name.split(" | "):
            assert part in NAME2MAKER, "name not in vocabulary: %r" % part
        assert len(note) <= 120, note
        out.append({
            "string_id": r["string_id"],
            "value_raw": r["value_raw"],
            "library_kit_norm": name,
            "maker": maker,
            "n_named": k,
            "mapping_note": note,
        })

    with open(MAP_PATH, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["string_id", "value_raw", "library_kit_norm",
                                           "maker", "n_named", "mapping_note"])
        w.writeheader()
        w.writerows(out)

    # vocabulary: one row per individual controlled name; a string naming two
    # methods counts towards each of them.
    agg = {}
    for r, o in zip(rows, out):
        for part in o["library_kit_norm"].split(" | "):
            a = agg.setdefault(part, {"n_strings": 0, "n_projects": 0})
            a["n_strings"] += 1
            a["n_projects"] += int(r["n_projects"])
    vocab = sorted(agg.items(), key=lambda kv: (-kv[1]["n_projects"], -kv[1]["n_strings"], kv[0]))
    with open(VOCAB_PATH, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["library_kit_norm", "maker", "n_strings", "n_projects"])
        for name, a in vocab:
            w.writerow([name, NAME2MAKER[name], a["n_strings"], a["n_projects"]])

    sys.stderr.write("strings=%d controlled_names=%d unclear=%d multiple=%d\n" % (
        len(out), len(agg), sum(1 for o in out if o["library_kit_norm"] == UNCLEAR),
        sum(1 for o in out if o["n_named"] > 1)))


if __name__ == "__main__":
    main()
