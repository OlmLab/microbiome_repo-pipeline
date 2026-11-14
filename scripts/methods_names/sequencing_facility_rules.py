#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Controlled-name mapping for the `sequencing facility` methods field
(Microbiome Repo deep pass stage B, release R2026.40).

Input : sequencing_facility_strings.csv  (string_id, value_raw, n_projects)
Output: sequencing_facility_map.csv      (one row per input string)
        sequencing_facility_vocab.csv    (one row per controlled name)

Method: an ordered list of regular-expression rules over a normalised copy of
value_raw (case-folded, dashes/quotes ASCII-ised).  The first matching rule
wins, so specific rules precede general ones.  Each rule carries the controlled
name, the facility type, and a country specification:

    "XX"  fixed ISO 3166-1 alpha-2 code (organisation has one site, or the
          site is implied by the name itself)
    "*"   infer the country from place names in the string; blank when the
          string names no place (organisation has sites in several countries)
    "*XX" infer, falling back to XX

Strings naming two facilities are listed in MULTI and get the two controlled
names joined with ' | '.
"""

import csv
import os
import re
import unicodedata
from collections import OrderedDict

HERE = os.path.dirname(os.path.abspath(__file__))
IN_CSV = os.path.join(HERE, "sequencing_facility_strings.csv")
MAP_CSV = os.path.join(HERE, "sequencing_facility_map.csv")
VOCAB_CSV = os.path.join(HERE, "sequencing_facility_vocab.csv")

CO = "company"
UC = "university_core_facility"
RI = "research_institute_or_genome_centre"
HO = "hospital_or_clinical_lab"
NC = "consortium_or_national_centre"
AL = "authors_own_lab"
OT = "other"


def norm(s):
    s = unicodedata.normalize("NFC", s)
    for a, b in (("\u2010", "-"), ("\u2011", "-"), ("\u2012", "-"),
                 ("\u2013", "-"), ("\u2014", "-"), ("\u2019", "'"),
                 ("\u2018", "'"), ("\u201c", '"'), ("\u201d", '"'),
                 ("\u00ae", ""), ("\u2122", "")):
        s = s.replace(a, b)
    return re.sub(r"\s+", " ", s).strip().lower()


# --------------------------------------------------------------------------
# country inference: ordered, first match wins
# --------------------------------------------------------------------------
COUNTRY_TOKENS = [
    (r"hong kong|hksar|hk s\.a\.r", "HK"),
    (r"\btaiwan\b|taoyuan", "TW"),
    (r"\bchina\b|beijing|shanghai|shenzhen|tianjin|hangzhou|guangzhou|nanjing|"
     r"wuhan|qingdao|chengdu|changsha|zhengzhou|fuzhou|suzhou|xi'an|guangdong|"
     r"jiangsu", "CN"),
    (r"\bkorea\b|seoul", "KR"),
    (r"\bjapan\b|kusatsu|tokyo|nagoya", "JP"),
    (r"singapore|\bpte\b", "SG"),
    (r"\bindia\b|bangalore|hyderabad|new delhi|bhopal", "IN"),
    (r"vietnam|hanoi", "VN"),
    (r"australia|sydney|adelaide|brisbane|melbourne|\bunsw\b|geelong", "AU"),
    (r"new zealand|otago|dunedin", "NZ"),
    (r"canada|montreal|montr\u00e9al|vancouver|halifax|calgary|toronto|"
     r"quebec|qu\u00e9bec|nova scotia|\bab\b", "CA"),
    (r"mexico|m\u00e9xico", "MX"),
    (r"brazil|brasil|campinas|guarapuava", "BR"),
    (r"colombia|medellin|medell\u00edn", "CO"),
    (r"\bchile\b", "CL"),
    (r"\buk\b|u\.k\.|united kingdom|england|london|essex|nottingham|liverpool|"
     r"birmingham|oxford|norwich|edinburgh", "GB"),
    (r"ireland|moorepark|\bcork\b|dublin", "IE"),
    (r"germany|munich|konstanz|constance|t\u00fcbingen|tubingen|stuttgart|"
     r"braunschweig|\bkiel\b|berlin|heidelberg|giessen|hannover|ebersberg|"
     r"\bjena\b|\bgmbh\b", "DE"),
    (r"switzerland|\bbern\b|balgach|lausanne|zurich|z\u00fcrich|geneva", "CH"),
    (r"austria|vienna", "AT"),
    (r"netherlands|leiden|utrecht|amsterdam|rotterdam|\bb\.v\.|\bbv\b", "NL"),
    (r"belgium|leuven", "BE"),
    (r"luxembourg", "LU"),
    (r"france|paris|\blyon\b|montpellier|jouy-en-josas|lille", "FR"),
    (r"italy|italia|trento|\btn\b|padua|padova|udine|salerno|naples|portici|"
     r"florence|verona|ferrara|milan|bologna|\brome\b|\bs\.r\.l|\bsrl\b", "IT"),
    (r"spain|valencia|barcelona|madrid", "ES"),
    (r"sweden|stockholm|uppsala|gothenburg|\blund\b", "SE"),
    (r"norway|\boslo\b|troms|trondheim", "NO"),
    (r"denmark|copenhagen", "DK"),
    (r"finland|helsinki|turku", "FI"),
    (r"estonia|tartu", "EE"),
    (r"latvia|\briga\b", "LV"),
    (r"south africa|bloemfontein", "ZA"),
    (r"nigeria", "NG"),
    (r"senegal|dakar", "SN"),
    (r"bangladesh|dhaka", "BD"),
    (r"saudi|jeddah", "SA"),
    (r"kuwait", "KW"),
    (r"qatar|doha", "QA"),
    (r"\busa\b|u\.s\.a|united states|\bus\b|americas|california|sacramento|"
     r"chula vista|san diego|san francisco|irvine|fremont|minneapolis|"
     r"germantown|bethlehem|huntingdon|norcross|phoenix|nashville|rockville|"
     r"\bca\b|\bmd\b|\bmn\b|\bpa\b|\bga\b|\baz\b|\btx\b|\bil\b|\bny\b", "US"),
]


def infer_country(text):
    for pat, code in COUNTRY_TOKENS:
        if re.search(pat, text):
            return code
    return ""


# --------------------------------------------------------------------------
# strings naming two facilities
# --------------------------------------------------------------------------
MULTI = {
    "bgi pathogenesis pharmaceutical technology, bgi-shenzhen and hugobiotech "
    "co., ltd., beijing, china": ("BGI | Hugobiotech", "CN", CO,
                                  "string names two companies"),
}

# --------------------------------------------------------------------------
# ordered rules: (pattern, controlled name, type, country spec, note)
# --------------------------------------------------------------------------
RULES = [
    # ---- precedence guards -------------------------------------------------
    (r"meiji holdings", "Meiji Holdings", CO, "JP", ""),
    (r"cosmos wisdom", "Cosmos Wisdom Biotechnology", CO, "CN", ""),
    (r"corebiome", "CoreBiome", CO, "US",
     "acquired by OraSure; business now run as Diversigen"),
    (r"diversigen", "Diversigen", CO, "US", ""),
    (r"mgi tech", "MGI Tech", CO, "*",
     "sequencing-instrument arm of the BGI group"),
    (r"psomagen", "Psomagen", CO, "US", "US subsidiary of Macrogen"),
    (r"\bmicrobes ?ng\b", "MicrobesNG", CO, "GB",
     "spin-out hosted at the University of Birmingham"),
    (r"duchossois", "University of Chicago Duchossois Family Institute", UC,
     "US", ""),

    # ---- Novogene and its misspellings ------------------------------------
    (r"novogene|novo gene|novagene|novogen\b|novozyme|\bnovo inc", "Novogene",
     CO, "*", ""),

    # ---- BGI group ---------------------------------------------------------
    (r"\bbgi\b|beijing genomics institute|beijing genome institute|"
     r"beijing genomic institute|huada", "BGI", CO, "*", ""),

    # ---- other Chinese sequencing companies -------------------------------
    (r"majorbio|meiji biomedical|shanghai meiji", "Majorbio", CO, "CN", ""),
    (r"\boe biotech", "OE Biotech", CO, "CN", ""),
    (r"personal ?bio|personal biotechnology", "Personalbio", CO, "CN", ""),
    (r"genoxor", "Genoxor", CO, "CN", ""),
    (r"quantihealth", "Beijing QuantiHealth Technology", CO, "CN", ""),
    (r"ann?oroad", "Annoroad Gene Technology", CO, "CN", ""),
    (r"berry genomics", "Berry Genomics", CO, "CN", ""),
    (r"biomarker technologies", "Biomarker Technologies", CO, "*", ""),
    (r"biotecan", "Biotecan", CO, "CN", ""),
    (r"wekemo", "Wekemo Tech Group", CO, "CN", ""),
    (r"lc.?bio|lianchuan", "LC-Bio Technology", CO, "CN",
     "trading name of Hangzhou Lianchuan Biotechnology"),
    (r"metware", "Metware Biotechnology", CO, "CN", ""),
    (r"mygenostics", "MyGenostics", CO, "CN", ""),
    (r"genskey", "Genskey", CO, "CN", ""),
    (r"hugobiotech", "Hugobiotech", CO, "CN", ""),
    (r"dinfectome", "Dinfectome", CO, "CN", ""),
    (r"vision medical", "Vision Medicals", CO, "CN", ""),
    (r"willingmed", "WillingMed Technology", CO, "CN", ""),
    (r"shaan probiomicros", "Shaan Probiomicros", CO, "CN", ""),
    (r"biozeron", "Shanghai Biozeron Biotechnology", CO, "CN", ""),
    (r"shanghai biotech", "Shanghai Biotechnology Corporation", CO, "CN", ""),
    (r"g&c biotechnology", "Shanghai G&C Biotechnology", CO, "CN", ""),
    (r"genergy", "Shanghai Genergy Biotechnology", CO, "CN", ""),
    (r"megagene", "Guangdong MegaGene Technology", CO, "CN", ""),
    (r"kingcreate", "Guangzhou KingCreate Biotechnology", CO, "CN", ""),
    (r"sagene", "Guangzhou Sagene Biotech", CO, "CN", ""),
    (r"matridx", "Matridx Biotechnology", CO, "CN", ""),
    (r"jieyi", "Hangzhou Jieyi Biotechnology", CO, "CN", ""),
    (r"life baseline", "Chengdu Life Baseline Technology", CO, "CN", ""),
    (r"microread", "Microread Genetics", CO, "CN", ""),
    (r"capitalbio", "CapitalBio Medical Laboratory", CO, "CN", ""),
    (r"kingmed", "KingMed Diagnostics", HO, "CN", ""),
    (r"dian diagnostics", "Dian Diagnostics", HO, "CN", ""),
    (r"gold key medical", "Gold Key Medical Laboratory", CO, "", ""),
    (r"practice medicine", "Practice Medicine Company", CO, "CN", ""),
    (r"groken", "Groken Bioscience", CO, "HK", ""),
    (r"microbecode", "MicrobeCode Biotech", CO, "", ""),
    (r"agene genomics", "Agene Genomics Laboratory", CO, "CN", ""),
    (r"genebay", "GeneBay", CO, "", ""),
    (r"bioengineering lab", "Bioengineering Lab", CO, "JP", ""),
    (r"health genetech", "Health GeneTech", CO, "TW", ""),

    # ---- Chinese / Hong Kong institutes and hospitals ----------------------
    (r"institute of microbiology, chinese academy",
     "Chinese Academy of Sciences Institute of Microbiology", RI, "CN", ""),
    (r"beijing institutes of life science",
     "Chinese Academy of Sciences Beijing Institutes of Life Science", RI,
     "CN", ""),
    (r"henan gene hospital|zhengzhou university",
     "Zhengzhou University First Affiliated Hospital Henan Gene Hospital", HO,
     "CN", ""),
    (r"shanghai tenth people",
     "Shanghai Tenth People's Hospital Department of Clinical Laboratory", HO,
     "CN", ""),
    (r"shanghai public health clinical center",
     "Fudan University Shanghai Public Health Clinical Center", HO, "CN", ""),
    (r"chinese university of hong kong",
     "Chinese University of Hong Kong Centre for Gut Microbiota Research", UC,
     "HK", ""),
    (r"microbiota i-cent", "Microbiota I-Center (MagIC)", RI, "HK", ""),
    (r"panoromic",
     "University of Hong Kong Centre for PanorOmic Sciences Genomics Core", UC,
     "HK", "formerly the Centre for Genomic Sciences"),
    (r"centre for genomic sciences",
     "University of Hong Kong Centre for PanorOmic Sciences Genomics Core", UC,
     "HK", "paper names the Centre for Genomic Sciences, absorbed into CPOS"),

    # ---- Korea / Japan -----------------------------------------------------
    (r"cj bioscience", "CJ Bioscience", CO, "KR", "formerly ChunLab"),
    (r"chunlab", "ChunLab", CO, "KR", "renamed CJ Bioscience"),
    (r"kyungpook|knu ngs",
     "Kyungpook National University NGS Core Facility", UC, "KR", ""),
    (r"macrogen", "Macrogen", CO, "*", ""),
    (r"takara bio", "Takara Bio", CO, "*JP", ""),
    (r"taniguchi", "Taniguchi Dental Clinic Oral Microbiome Center", HO, "JP",
     ""),
    (r"research institute of environmental medicine",
     "Nagoya University Research Institute of Environmental Medicine", RI,
     "JP", ""),

    # ---- India / South and South-East Asia --------------------------------
    (r"iiser",
     "Indian Institute of Science Education and Research Bhopal "
     "Next-Generation Sequencing Facility", UC, "IN", ""),
    (r"\bigib\b|institute of genomics and integrative biology",
     "CSIR Institute of Genomics and Integrative Biology", RI, "IN", ""),
    (r"genotypic technology", "Genotypic Technology", CO, "IN", ""),
    (r"biokart", "Biokart India", CO, "IN", ""),
    (r"leucine rich", "Leucine Rich Bio", CO, "IN", ""),
    (r"medgenome", "MedGenome", CO, "IN", ""),
    (r"nucleome", "Nucleome Informatics", CO, "IN", ""),
    (r"gentis", "GENTIS", CO, "VN", ""),
    (r"nanyang technological", "Nanyang Technological University", UC, "SG",
     "sequencing unit not named in the paper"),
    (r"genome institute of singapore", "Genome Institute of Singapore", RI,
     "SG", ""),

    # ---- US companies ------------------------------------------------------
    (r"cosmosid", "CosmosID", CO, "US", ""),
    (r"microbiome ?insights", "Microbiome Insights", CO, "CA", ""),
    (r"seqcenter|microbial genome sequencing center", "SeqCenter", CO, "US",
     "formerly the Microbial Genome Sequencing Center (MiGS)"),
    (r"abbott laboratories", "Abbott Laboratories", CO, "US", ""),
    (r"admera", "Admera Health", CO, "US", ""),
    (r"aperiomics", "Aperiomics", CO, "US", ""),
    (r"arbor ?biosciences", "Arbor Biosciences", CO, "US", ""),
    (r"ezbiome", "EzBiome", CO, "US", ""),
    (r"fulgent", "Fulgent Genetics", CO, "US", ""),
    (r"jumpcode", "Jumpcode Genomics", CO, "US", ""),
    (r"\bkarius\b", "Karius", CO, "US", ""),
    (r"\bmogene\b", "MOgene", CO, "US", ""),
    (r"mr dna|molecular research lp", "Molecular Research LP (MR DNA)", CO,
     "US", ""),
    (r"microgen ?dx", "MicroGenDX", CO, "US", ""),
    (r"new england biolabs", "New England Biolabs", CO, "US", ""),
    (r"omega bioservices", "Omega Bioservices", CO, "US", ""),
    (r"phase genomics", "Phase Genomics", CO, "US", ""),
    (r"plasmidsaurus", "Plasmidsaurus", CO, "US", ""),
    (r"seqmatic", "SeqMatic", CO, "US", ""),
    (r"viome", "Viome Life Sciences", CO, "US", ""),
    (r"\bzymo", "Zymo Research", CO, "*US", ""),
    (r"wright labs", "Wright Labs", CO, "US", ""),
    (r"contamination source identification",
     "Contamination Source Identification (CSI)", CO, "US", ""),
    (r"prebiomics", "Prebiomics", CO, "IT", ""),
    (r"azenta", "Azenta Life Sciences", CO, "*",
     "parent company of GENEWIZ since 2022"),
    (r"genewiz", "GENEWIZ", CO, "*", "now part of Azenta Life Sciences"),

    # ---- European companies ------------------------------------------------
    (r"fasteris", "Fasteris", CO, "CH", ""),
    (r"\bgatc\b", "GATC Biotech", CO, "DE",
     "acquired by Eurofins Genomics in 2017"),
    (r"eurofins", "Eurofins Genomics", CO, "*", ""),
    (r"cegat", "CeGaT", CO, "DE", ""),
    (r"noscendo", "Noscendo", CO, "DE", ""),
    (r"lgc genomics", "LGC Genomics", CO, "*", ""),
    (r"vertis", "Vertis Biotechnologie", CO, "DE", ""),
    (r"microsynth", "Microsynth", CO, "CH", ""),
    (r"baseclear", "BaseClear", CO, "NL", ""),
    (r"genomescan", "GenomeScan", CO, "NL", ""),
    (r"clinical microbiomics|\bcmbio\b", "Clinical Microbiomics", CO, "DK",
     ""),
    (r"genoscreen", "GenoScreen", CO, "FR", ""),
    (r"source bioscience", "Source BioScience", CO, "GB", ""),
    (r"\bqiagen\b", "QIAGEN Genomic Services", CO, "*", ""),
    (r"iga technology", "IGA Technology Services", CO, "IT", ""),
    (r"bmr genomics", "BMR Genomics", CO, "IT", ""),
    (r"genprobio", "GenProbio", CO, "IT", ""),
    (r"\bg4l\b", "Genomix4Life", CO, "IT", "paper writes 'G4L Company'"),
    (r"adm-biopolis|adm biopolis", "ADM Biopolis", CO, "ES", ""),
    (r"life sequencing|lifesequencing", "Lifesequencing", CO, "ES", ""),
    (r"the bioarte|bioarte", "The BioArte", CO, "MT", ""),
    (r"\bmicroba\b", "Microba Life Sciences", CO, "AU", ""),

    # ---- genome centres and research institutes ---------------------------
    (r"broad", "Broad Institute", RI, "US", ""),
    (r"sanger", "Wellcome Sanger Institute", RI, "GB",
     "formerly the Wellcome Trust Sanger Institute"),
    (r"craig venter", "J. Craig Venter Institute", RI, "US", ""),
    (r"chan zuckerberg|cz biohub", "Chan Zuckerberg Biohub San Francisco", RI,
     "US", ""),
    (r"new york genome cent", "New York Genome Center", RI, "US", ""),
    (r"scripps", "The Scripps Research Institute", RI, "US", ""),
    (r"\btgen\b|translational genomics research institute",
     "Translational Genomics Research Institute (TGen)", RI, "US", ""),
    (r"jackson laboratory",
     "Jackson Laboratory for Genomic Medicine Genome Technologies", RI, "US",
     ""),
    (r"fred hutchinson",
     "Fred Hutchinson Cancer Center Genomics Shared Resource", RI, "US",
     "renamed Fred Hutchinson Cancer Center in 2022"),
    (r"oklahoma medical research",
     "Oklahoma Medical Research Foundation Next-Generation Sequencing Core",
     RI, "US", ""),
    (r"argonne",
     "Argonne National Laboratory Environmental Sample Preparation and "
     "Sequencing Facility", NC, "US", ""),
    (r"nih intramural|national intramural sequencing",
     "NIH Intramural Sequencing Center", NC, "US", ""),
    (r"national cancer institute|nci lici",
     "National Cancer Institute Microbiome and Genetics Core", NC, "US", ""),
    (r"\bcdc\b", "Centers for Disease Control and Prevention Genomics Core",
     NC, "US", ""),
    (r"european molecular biology laboratory|embl",
     "European Molecular Biology Laboratory Genomics Core Facility", RI, "DE",
     ""),
    (r"mpi-eva|max planck institute for evolutionary",
     "Max Planck Institute for Evolutionary Anthropology", RI, "DE", ""),
    (r"max planck institute for biology",
     "Max Planck Institute for Biology T\u00fcbingen", RI, "DE", ""),
    (r"max planck institute for the science of human history",
     "Max Planck Institute for the Science of Human History", RI, "DE", ""),
    (r"helmholtz", "Helmholtz Centre for Infection Research", RI, "DE", ""),
    (r"fraunhofer",
     "Fraunhofer Institute for Interfacial Engineering and Biotechnology", RI,
     "DE", ""),
    (r"eurac", "Eurac Research Institute for Mummy Studies", RI, "IT", ""),
    (r"\biigm\b", "Italian Institute for Genomic Medicine", RI, "IT", ""),
    (r"estonian biocentre", "Estonian Biocentre Core Laboratory", RI, "EE",
     ""),
    (r"michael smith genome sciences",
     "BC Cancer Michael Smith Genome Sciences Centre", RI, "CA", ""),
    (r"\bqib\b|quadram", "Quadram Institute Bioscience Sequencing Service", RI,
     "GB", ""),
    (r"geelong centre", "Geelong Centre for Emerging Infectious Diseases", RI,
     "AU", ""),
    (r"\bkaimrc\b", "King Abdullah International Medical Research Center", RI,
     "SA", ""),
    (r"oswaldo cruz|fiocruz",
     "Oswaldo Cruz Foundation High-Throughput Sequencing Platform", RI, "BR",
     ""),
    (r"\bipec\b", "Institute for Genomic Research (IPEC), Guarapuava", RI,
     "BR", ""),
    (r"mim_c|microbial interactions and microbiome center",
     "Microbial Interactions and Microbiome Center (mim_c)", RI, "", ""),
    (r"metag[e\u00e9]nopolis", "MetaGenoPolis (INRAE)", RI, "FR", ""),

    # ---- national / consortium centres ------------------------------------
    (r"norwegian sequencing", "Norwegian Sequencing Centre", NC, "NO", ""),
    (r"danish national high", "Danish National High-Throughput DNA "
     "Sequencing Centre", NC, "DK", ""),
    (r"scilifelab|science for life laboratory", "Science for Life Laboratory",
     NC, "SE", ""),
    (r"statens serum", "Statens Serum Institut", NC, "DK", ""),
    (r"finnish functional genomics",
     "Finnish Functional Genomics Centre", NC, "FI", ""),
    (r"teagasc", "Teagasc Sequencing Facility", NC, "IE", ""),
    (r"national microbiology laboratory", "National Microbiology Laboratory",
     NC, "CA", ""),
    (r"\bphe\b|public health england",
     "Public Health England Genomics Services Development Unit", NC, "GB", ""),
    (r"central public health laboratory",
     "Central Public Health Laboratory Genomics Core Laboratory", NC, "", ""),
    (r"\bcnm genomics",
     "Centro Nacional de Microbiolog\u00eda Genomics Core Facility", NC, "ES",
     "read as the Spanish Centro Nacional de Microbiolog\u00eda (ISCIII)"),
    (r"national center for genome analysis",
     "Centro Nacional de An\u00e1lisis Gen\u00f3mico (CNAG)", NC, "ES", ""),
    (r"fisabio|csisp",
     "FISABIO Sequencing and Bioinformatics Service", NC, "ES", ""),
    (r"inmegen|national institute for genomic medicine|"
     r"nacional institute of genomic medicine",
     "Instituto Nacional de Medicina Gen\u00f3mica (INMEGEN)", NC, "MX", ""),
    (r"technological support to genomic sciences",
     "National Laboratory for Technological Support to Genomic Sciences "
     "(UNAM)", NC, "MX", ""),
    (r"bcsir|bangladesh council",
     "Bangladesh Council of Scientific and Industrial Research Genomic "
     "Research Laboratory", NC, "BD", ""),
    (r"cidm-ph",
     "Centre for Infectious Diseases and Microbiology Public Health", HO,
     "AU", ""),

    # ---- US university cores ----------------------------------------------
    (r"minnesota genomics", "University of Minnesota Genomics Center", UC,
     "US", ""),
    (r"maryland",
     "University of Maryland School of Medicine Institute for Genome "
     "Sciences", UC, "US", ""),
    (r"alkek|center for metagenomics and microbiome research",
     "Baylor College of Medicine Alkek Center for Metagenomics and Microbiome "
     "Research", UC, "US", ""),
    (r"human genome sequencing center",
     "Baylor College of Medicine Human Genome Sequencing Center", UC, "US",
     ""),
    (r"baylor", "Baylor College of Medicine", UC, "US",
     "paper does not name which Baylor centre"),
    (r"\bigm\b|institute for genomic medicine",
     "University of California San Diego IGM Genomics Center", UC, "US", ""),
    (r"center for advanced technology|\bcat\b",
     "University of California San Francisco Center for Advanced Technology",
     UC, "US", ""),
    (r"university of california, san francisco|university of california san "
     r"francisco|\bucsf\b", "University of California San Francisco", UC,
     "US", "paper does not name the core facility"),
    (r"uc davis|university of california davis|university of california at "
     r"davis",
     "University of California Davis Genome Center DNA Technologies and "
     "Expression Analysis Core", UC, "US", ""),
    (r"coates|\bqb3\b|california institute for quantitative biosciences",
     "University of California Berkeley QB3 Vincent J. Coates Genomics "
     "Sequencing Laboratory", UC, "US", ""),
    (r"george washington|gw genomics",
     "George Washington University Genomics Core", UC, "US", ""),
    (r"gtac|genome technology access center|mcdonnell",
     "Washington University in St. Louis McDonnell Genome Institute Genome "
     "Technology Access Center", UC, "US", ""),
    (r"edison family|center for genome sciences",
     "Washington University in St. Louis Edison Family Center for Genome "
     "Sciences and Systems Biology", UC, "US", ""),
    (r"washington university", "Washington University in St. Louis", UC, "US",
     "paper does not name the core facility"),
    (r"university of illinois at chicago|\buic\b|university of illinois, "
     r"chicago", "University of Illinois Chicago Genome Research Core", UC,
     "US", ""),
    (r"carver|keck center|university of illinois|urbana",
     "University of Illinois at Urbana-Champaign Roy J. Carver Biotechnology "
     "Center", UC, "US", ""),
    (r"weill cornell.*(epigenomics)",
     "Weill Cornell Medicine Epigenomics Core", UC, "US", ""),
    (r"weill cornell", "Weill Cornell Medicine Microbiome Core Laboratory",
     UC, "US", ""),
    (r"cornell biotechnology",
     "Cornell University Biotechnology Resource Center Genomics Facility", UC,
     "US", ""),
    (r"langone|new york university genome technology",
     "NYU Langone Health Genome Technology Center", UC, "US", ""),
    (r"center for genomics and systems biology",
     "New York University Center for Genomics and Systems Biology Genomics "
     "Core Facility", UC, "US", ""),
    (r"indiana university center for medical genomics|center for medical "
     r"genomics|indiana university medical genomics",
     "Indiana University School of Medicine Center for Medical Genomics", UC,
     "US", ""),
    (r"microbiology and immunology at iu",
     "Indiana University Department of Microbiology and Immunology", AL, "US",
     ""),
    (r"north carolina.*microbiome",
     "University of North Carolina at Chapel Hill Microbiome Core Facility",
     UC, "US", ""),
    (r"north carolina|unc-chapel hill",
     "University of North Carolina at Chapel Hill High-Throughput Sequencing "
     "Facility", UC, "US", ""),
    (r"yale cent", "Yale Center for Genome Analysis", UC, "US", ""),
    (r"biodesign",
     "Arizona State University Biodesign Institute Genomics Core", UC, "US",
     ""),
    (r"advanced genomics core|university of michigan",
     "University of Michigan Advanced Genomics Core", UC, "US", ""),
    (r"boston university", "Boston University School of Medicine", UC, "US",
     "paper does not name the core facility"),
    (r"duke", "Duke University Sequencing and Genomic Technologies Shared "
     "Resource", UC, "US", ""),
    (r"texas at austin",
     "University of Texas at Austin Genomic Sequencing and Analysis Facility",
     UC, "US", ""),
    (r"texas at dallas", "University of Texas at Dallas Genome Center", UC,
     "US", ""),
    (r"texas southwestern",
     "University of Texas Southwestern Medical Center Genomics Core Facility",
     UC, "US", ""),
    (r"\bmdacc\b|md anderson",
     "MD Anderson Cancer Center Microbiome Core Facility", UC, "US", ""),
    (r"pittsburgh health sciences",
     "University of Pittsburgh Health Sciences Sequencing Core", UC, "US",
     ""),
    (r"pittsburgh", "University of Pittsburgh Genomics Research Core", UC,
     "US", ""),
    (r"ohio state", "Ohio State University Genomics Shared Resource", UC,
     "US", ""),
    (r"rush", "Rush University Genomics and Microbiome Core Facility", UC,
     "US", ""),
    (r"molecular evolution core|georgia institute of technology|georgia tech",
     "Georgia Institute of Technology Molecular Evolution Core Facility", UC,
     "US", ""),
    (r"harvard", "Harvard Medical School Biopolymers Facility", UC, "US", ""),
    (r"hubbard cent|new hampshire",
     "University of New Hampshire Hubbard Center for Genome Studies", UC,
     "US", ""),
    (r"ibest|university of idaho",
     "University of Idaho IBEST Genomics Resources Core", UC, "US", ""),
    (r"sulzberger|columbia genome",
     "Columbia University JP Sulzberger Genome Center", UC, "US", ""),
    (r"johns hopkins",
     "Johns Hopkins Genomics Genetic Resources Core Facility", UC, "US", ""),
    (r"michigan state|\brtsf\b",
     "Michigan State University Research Technology Support Facility Genomics "
     "Core", UC, "US", ""),
    (r"mount sinai",
     "Icahn School of Medicine at Mount Sinai Microbiome Translational "
     "Center", UC, "US", ""),
    (r"interdisciplinary center for biotechnology research|\bicbr\b",
     "University of Florida Interdisciplinary Center for Biotechnology "
     "Research NextGen DNA Sequencing Core", UC, "US", ""),
    (r"nuseq|northwestern",
     "Northwestern University NUSeq Core Facility", UC, "US", ""),
    (r"onco-genomics|university of miami",
     "University of Miami Onco-Genomics Shared Resource", UC, "US", ""),
    (r"stanford", "Stanford University Genomics Service Center", UC, "US",
     ""),
    (r"umass chan", "UMass Chan Medical School Center for Microbiome "
     "Research", UC, "US", ""),
    (r"panda core|university of arizona",
     "University of Arizona PANDA Core for Genomics and Microbiome Research",
     UC, "US", ""),
    (r"cincinnati",
     "University of Cincinnati Genomics, Epigenomics and Sequencing Core", UC,
     "US", ""),
    (r"university of houston",
     "University of Houston Sequencing and Gene Editing Core", UC, "US", ""),
    (r"university of oregon", "University of Oregon", UC, "US",
     "paper does not name the core facility"),
    (r"northwest genomics",
     "University of Washington Northwest Genomics Center", UC, "US", ""),
    (r"wisconsin",
     "University of Wisconsin-Madison Biotechnology Center", UC, "US", ""),
    (r"\bvcu\b|virginia commonwealth",
     "Virginia Commonwealth University Nucleic Acids Research Facility", UC,
     "US", ""),
    (r"vantage|vanderbilt",
     "Vanderbilt University Medical Center VANTAGE", UC, "US", ""),
    (r"colorado", "University of Colorado Genomics Shared Resource", UC, "US",
     ""),
    (r"global bioscience genomics lab",
     "Global Bioscience Genomics Lab, Mason", OT, "US",
     "host institution not identified from the paper"),

    # ---- US hospitals and clinical labs ------------------------------------
    (r"children's hospital of philadelphia|chop|penn ?chop",
     "Children's Hospital of Philadelphia Microbiome Center", HO, "US", ""),
    (r"clinical genome sequencing laboratory",
     "Mayo Clinic Clinical Genome Sequencing Laboratory", HO, "US", ""),
    (r"mayo clinic", "Mayo Clinic Medical Genome Facility", HO, "US", ""),
    (r"\bmskcc\b|sloan kettering",
     "Memorial Sloan Kettering Cancer Center Molecular Microbiology Core", HO,
     "US", ""),
    (r"st\.? jude", "St. Jude Children's Research Hospital", HO, "US", ""),

    # ---- Canada ------------------------------------------------------------
    (r"integrated microbiome res",
     "Dalhousie University Integrated Microbiome Resource", UC, "CA", ""),
    (r"g\u00e9nome qu\u00e9bec|genome quebec|genome qu\u00e9bec|"
     r"g\u00e9nome-qu\u00e9bec", "G\u00e9nome Qu\u00e9bec", NC, "CA", ""),
    (r"health genomics and informatics",
     "University of Calgary Centre for Health Genomics and Informatics", UC,
     "CA", ""),
    (r"applied genomics",
     "The Centre for Applied Genomics, Hospital for Sick Children", HO, "CA",
     ""),
    (r"princess margaret", "Princess Margaret Genomics Centre", HO, "CA", ""),
    (r"mcmaster", "McMaster University Genome Facility", UC, "CA", ""),

    # ---- UK / Ireland ------------------------------------------------------
    (r"edinburgh genomics", "Edinburgh Genomics", UC, "GB", ""),
    (r"oxford genomics|oxford centre for genomics", "Oxford Genomics Centre",
     UC, "GB", ""),
    (r"liverpool", "University of Liverpool Centre for Genomic Research", UC,
     "GB", ""),
    (r"genomics birmingham", "University of Birmingham Genomics Birmingham",
     UC, "GB", ""),
    (r"university of cambridge",
     "University of Cambridge Department of Biochemistry DNA Sequencing "
     "Facility", UC, "GB", ""),
    (r"warwick", "University of Warwick Medical School", UC, "GB", ""),

    # ---- continental Europe cores -----------------------------------------
    (r"\blcsb\b|luxembourg",
     "Luxembourg Centre for Systems Biomedicine Sequencing Platform", UC,
     "LU", ""),
    (r"nucleomics", "VIB Nucleomics Core", UC, "BE", ""),
    (r"viral metagenomics",
     "KU Leuven Laboratory of Viral Metagenomics", AL, "BE", ""),
    (r"utrecht", "Utrecht Sequencing Facility", UC, "NL", ""),
    (r"erasmus", "Erasmus MC", HO, "NL", ""),
    (r"competence centre for genomic analysis|\bccga\b",
     "Kiel University Competence Centre for Genomic Analysis", UC, "DE", ""),
    (r"justus liebig|m3u",
     "Justus Liebig University Giessen Institute of Medical Microbiology", UC,
     "DE", ""),
    (r"hannover medical school|\bmhh\b", "Hannover Medical School", HO, "DE",
     ""),
    (r"inselspital|university of bern",
     "University of Bern Next Generation Sequencing Platform", UC, "CH", ""),
    (r"\bchuv\b",
     "Lausanne University Hospital (CHUV) Genomics and Metagenomics "
     "Laboratory", HO, "CH", ""),
    (r"joint microbiome facility",
     "Joint Microbiome Facility, University of Vienna and Medical University "
     "of Vienna", UC, "AT", ""),
    (r"chu-lyon|chu de lyon", "CHU de Lyon", HO, "FR", ""),
    (r"\bmgx\b", "Montpellier GenomiX (MGX)", UC, "FR", ""),
    (r"gothenburg|genecore su",
     "University of Gothenburg Genomics Core Facility", UC, "SE", ""),
    (r"lund university", "Lund University Center for Translational Genomics",
     UC, "SE", ""),
    (r"\bntnu\b|norwegian university of science",
     "Norwegian University of Science and Technology Genomics Core Facility",
     UC, "NO", ""),
    (r"troms", "UiT The Arctic University of Norway Genomics Support Centre "
     "Troms\u00f8", UC, "NO", ""),
    (r"\bfimm\b|molecular medicine finland",
     "Institute for Molecular Medicine Finland (FIMM)", UC, "FI", ""),
    (r"biomedicum",
     "University of Helsinki Biomedicum Functional Genomics Unit", UC, "FI",
     ""),
    (r"institute of biotechnology",
     "University of Helsinki Institute of Biotechnology Sequencing Service",
     UC, "FI", ""),
    (r"university of tartu",
     "University of Tartu Institute of Genomics Core Facility", UC, "EE", ""),
    (r"university of verona",
     "University of Verona Technological Platform Centre", UC, "IT", ""),
    (r"ferrara", "University of Ferrara NGS Service", UC, "IT", ""),
    (r"naples federico",
     "University of Naples Federico II Department of Agricultural Sciences",
     AL, "IT", ""),
    (r"university of florence", "University of Florence Department of "
     "Biology", AL, "IT", ""),
    (r"trento|cibio|integrative biology",
     "University of Trento Next Generation Sequencing Facility", UC, "IT",
     ""),
    (r"sgb-uab",
     "Universitat Aut\u00f2noma de Barcelona Servei de Gen\u00f2mica i "
     "Bioinform\u00e0tica", UC, "ES", ""),
    (r"parque cient\u00edfico de madrid|parque cientifico de madrid",
     "Parque Cient\u00edfico de Madrid", RI, "ES", ""),

    # ---- Australia / New Zealand ------------------------------------------
    (r"australian genom", "Australian Genome Research Facility", CO, "AU", ""),
    (r"ramac+i+otti", "Ramaciotti Centre for Genomics, UNSW Sydney", UC, "AU",
     ""),
    (r"centre for microbiome research",
     "Centre for Microbiome Research, Queensland University of Technology",
     UC, "AU", ""),
    (r"diamantina", "University of Queensland Diamantina Institute", UC, "AU",
     ""),
    (r"otago", "University of Otago Genomics Facility", UC, "NZ", ""),
    (r"\bcsu ngs\b", "CSU Next-Generation Sequencing Facility", OT, "",
     "'CSU' not disambiguated in the paper"),

    # ---- Africa / Middle East / Latin America ------------------------------
    (r"free state",
     "University of the Free State Next Generation Sequencing Unit", UC, "ZA",
     ""),
    (r"redeemer", "Redeemer's University", UC, "NG", ""),
    (r"king abdulaziz|kfmrc",
     "King Abdulaziz University King Fahd Medical Research Center Special "
     "Infectious Agents Unit", UC, "SA", ""),
    (r"kuwait university",
     "Kuwait University Health Sciences Centre OMICS Research Unit", UC, "KW",
     ""),
    (r"sidra", "Sidra Medicine", HO, "QA", ""),
    (r"parasitologie",
     "Laboratoire de Parasitologie et Mycologie, Dakar", AL, "SN", ""),
    (r"lactad",
     "University of Campinas Central Laboratory for High Performance "
     "Technologies (LaCTAD)", UC, "BR", ""),
    (r"lbmcf|computational biology of fungi",
     "Laboratory of Molecular and Computational Biology of Fungi", AL, "BR",
     ""),
    (r"nacional university|universidad nacional de colombia",
     "Universidad Nacional de Colombia One Health Genomic Laboratory", UC,
     "CO", ""),
    (r"genoma mayor|universidad mayor",
     "Universidad Mayor Genoma Mayor", UC, "CL", ""),
]

# per-string notes (keyed on the normalised raw string)
EXTRA_NOTES = {
    "beijing novozyme biotech": "read as a mis-spelling of Novogene",
    "novozymes bioinformatics co, ltd":
        "read as Novogene Bioinformatics, the former company name",
    "novo inc., beijing, china": "read as Novogene",
    "novagene": "spelling variant of Novogene",
    "novagene (beijing)": "spelling variant of Novogene",
    "novagene co, beijing, china": "spelling variant of Novogene",
    "novagene, inc.": "spelling variant of Novogene",
    "novogen, beijing, china": "spelling variant of Novogene",
    "novo gene": "spelling variant of Novogene",
    "novo gene, china": "spelling variant of Novogene",
    "huada laboratories": "Huada is the Chinese name of BGI",
    "huada laboratories (shenzhen, china)": "Huada is the Chinese name of BGI",
    "bgi-huada genomics institute": "Huada is the Chinese name of BGI",
    "center for accurate diagnosis of infectious diseases established by "
    "usec-bgi": "joint USTC-BGI diagnostic centre",
    "center for accurate diagnosis of infectious diseases established by "
    "ustc-bgi": "joint USTC-BGI centre; sequencing attributed to BGI",
    "corebiome (now diversigen)": "paper notes CoreBiome is now Diversigen",
    "microbial genome sequencing center, now seqcenter":
        "paper notes MiGS is now SeqCenter",
    "cmbio (copenhagen, denmark)": "Cmbio is Clinical Microbiomics' short name",
    "shanghai meiji biomedical technology co., ltd.":
        "Shanghai Meiji is the Chinese name of Majorbio",
    "genomic resource center at the university of maryland school of medicine":
        "Genomic Resource Center sits within the Institute for Genome Sciences",
    "maryland genomics at umsom":
        "Maryland Genomics is the service name of the Institute for Genome "
        "Sciences",
    "uc davis, ca": "paper names only UC Davis",
    "university of california, san francisco":
        "paper does not name the core facility",
    "university of california san francisco (ucsf)":
        "paper does not name the core facility",
    "center for health genomics and informatics (university of calgary, "
    "british columbia)": "paper mis-states the province; Calgary is in Alberta",
    "next-generation sequencing platform of the inselspital, bern, switzerland":
        "Inselspital is the Bern university hospital of the same platform",
    "sequencing and analytical center of the pennchop microbiome program":
        "PennCHOP Microbiome Program, based at CHOP",
    "eurofins scientific (ebersberg, germany)":
        "paper names the parent, Eurofins Scientific",
    "collaborative sequencing center (tgen; phoenix, az, usa)":
        "TGen Collaborative Sequencing Center",
    "fred hutchinson cancer research center":
        "paper does not name the core; renamed Fred Hutchinson Cancer Center",
    "argonne national laboratory": "paper does not name the sequencing facility",
    "baylor college of medicine": "paper does not name which Baylor centre",
    "baylor college of medicine sequencing facility":
        "paper does not name which Baylor centre",
    "washington university school of medicine":
        "paper does not name the core facility",
    "high throughput sequencing core, georgia institute of technology":
        "Georgia Tech high-throughput sequencing is run by the Molecular "
        "Evolution Core",
    "georgia institute of technology (georgia tech) sequencing core facility":
        "Georgia Tech core; read as the Molecular Evolution Core",
    "sequencing core facility at johns hopkins":
        "paper does not name the Johns Hopkins core",
    "university of bern": "paper names only the university",
    "university of bern, switzerland": "paper names only the university",
    "university of trento, trento, italy": "paper names only the university",
    "cibio department (trento, italy)": "CIBIO department of Trento",
    "centre for integrative biology, trento, italy": "CIBIO department of Trento",
    "nacional university, medellin, colombia":
        "read as Universidad Nacional de Colombia, Medell\u00edn",
    "g4l company, salerno, italy": "G4L is Genomix4Life",
    "usec from inmegen": "sequencing unit (USec) of INMEGEN",
    "broad clinical laboratory":
        "Broad Clinical Labs, the Broad Institute's CLIA laboratory",
}


def apply_rules(raw):
    t = norm(raw)
    if t in MULTI:
        name, country, ftype, note = MULTI[t]
        return name, country, ftype, note
    for pat, name, ftype, spec, note in RULES:
        if re.search(pat, t):
            if spec.startswith("*"):
                country = infer_country(t) or spec[1:]
            else:
                country = spec
            note = EXTRA_NOTES.get(t, note)
            return name, country, ftype, note
    return None


def main():
    rows = []
    with open(IN_CSV, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            rows.append(r)

    out = []
    unmatched = []
    for r in rows:
        hit = apply_rules(r["value_raw"])
        if hit is None:
            unmatched.append(r["value_raw"])
            name, country, ftype, note = "unclear", "", OT, \
                "no facility identified in the string"
        else:
            name, country, ftype, note = hit
        assert len(note) <= 120, note
        out.append({
            "string_id": r["string_id"],
            "value_raw": r["value_raw"],
            "facility_norm": name,
            "facility_country": country,
            "facility_type": ftype,
            "mapping_note": note,
        })

    if unmatched:
        print("UNMATCHED (%d):" % len(unmatched))
        for u in unmatched:
            print("   ", u)

    # ---- checks -----------------------------------------------------------
    ids = [r["string_id"] for r in out]
    assert len(ids) == len(set(ids)) == len(rows), "string_id not unique"

    # no two controlled names differing only by case/punctuation
    key = {}
    for r in out:
        for nm in r["facility_norm"].split(" | "):
            k = re.sub(r"[^a-z0-9]", "", nm.lower())
            key.setdefault(k, set()).add(nm)
    dupes = {k: v for k, v in key.items() if len(v) > 1}
    assert not dupes, dupes

    # one type per controlled name
    types = {}
    for r in out:
        if " | " in r["facility_norm"]:
            continue
        types.setdefault(r["facility_norm"], set()).add(r["facility_type"])
    bad = {k: v for k, v in types.items() if len(v) > 1}
    assert not bad, bad

    with open(MAP_CSV, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["string_id", "value_raw",
                                           "facility_norm", "facility_country",
                                           "facility_type", "mapping_note"])
        w.writeheader()
        w.writerows(out)

    # ---- vocabulary -------------------------------------------------------
    np = {r["string_id"]: int(r["n_projects"]) for r in rows}
    vocab = OrderedDict()
    for r in out:
        for nm in r["facility_norm"].split(" | "):
            v = vocab.setdefault(nm, {"facility_norm": nm,
                                      "countries": set(),
                                      "facility_type": r["facility_type"],
                                      "n_strings": 0, "n_projects": 0})
            v["countries"].add(r["facility_country"])
            v["n_strings"] += 1
            v["n_projects"] += np[r["string_id"]]

    vrows = []
    for v in vocab.values():
        cs = v["countries"]
        vrows.append({
            "facility_norm": v["facility_norm"],
            # a code only when every string mapped to the name names that
            # country; blank for organisations whose strings disagree or are
            # silent about the site
            "facility_country": list(cs)[0] if len(cs) == 1 else "",
            "facility_type": v["facility_type"],
            "n_strings": v["n_strings"],
            "n_projects": v["n_projects"],
        })
    vrows.sort(key=lambda x: (-x["n_projects"], -x["n_strings"],
                              x["facility_norm"]))

    with open(VOCAB_CSV, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["facility_norm", "facility_country",
                                           "facility_type", "n_strings",
                                           "n_projects"])
        w.writeheader()
        w.writerows(vrows)

    print("strings=%d names=%d unclear=%d multiple=%d" % (
        len(out), len(vocab),
        sum(1 for r in out if r["facility_norm"] == "unclear"),
        sum(1 for r in out if " | " in r["facility_norm"])))
    for v in vrows[:30]:
        print("%5d %3d  %s" % (v["n_projects"], v["n_strings"],
                               v["facility_norm"]))


if __name__ == "__main__":
    main()
