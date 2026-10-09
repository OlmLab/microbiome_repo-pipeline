"""Registry tier (S0 track): vocabularies load, matchers honour word boundaries and negatives, the deterministic classifier
behaves on 30 synthetic + 200 real universe rows, the LLM parser/validator handles synthetic responses, registry_studies
conforms to audit/registry_schema.json and scope derivation follows config/scope.yaml."""
import json
import os
import sys

import pandas as pd
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
os.environ.setdefault("CATALOG_CONFIG_DIR", os.path.join(ROOT, "config"))

from catalog.registry import vocab as V  # noqa: E402
from catalog.registry import classify_llm as L  # noqa: E402
from catalog.registry import build_registry as B  # noqa: E402
from catalog.registry.classify_deterministic import classify_study, classify_frame, split_multi  # noqa: E402
from catalog.triage.curation_kernel import validate_evidence  # noqa: E402

FIXTURE = os.path.join(ROOT, "tests", "data", "registry_fixture_universe.parquet")
INFANT_UNIVERSE = os.environ.get("CATALOG_INFANT_UNIVERSE", os.path.join(ROOT, "data", "inputs", "data_package", "universe_studies_all.parquet"))
SPEC_BODY_SITES = ["gut_stool", "oral", "skin", "nasal_nasopharyngeal", "respiratory_lower", "vaginal_urogenital", "milk", "blood_tissue",
                   "eye_ear", "other_site", "unknown_site", "multi_site"]
SPEC_LIFE_STAGES = ["neonate", "infant", "child", "adolescent", "adult", "elderly", "unknown_age", "mixed_ages"]
SPEC_ASSAY = ["shotgun_dna", "amplicon_misfiled", "amplicon", "rna", "isolate_genome", "assembly_only", "other", "mixed", "unknown"]
SPEC_FLAGS = ["pregnant", "mother_infant_pair", "twins", "disease_cohort", "healthy_volunteers", "hospitalised", "antibiotic_trial", "other"]
SPEC_SCOPES = ["human_all", "infant_gut", "gut_child", "gut_adult", "oral", "skin", "vaginal_urogenital", "respiratory", "milk", "blood_tissue",
               "other_site", "unknown_site"]


# --------------------------------------------------------------------------- vocabularies
def test_vocabularies_match_frozen_spec():
    assert V.codes("body_sites") == SPEC_BODY_SITES
    assert V.codes("life_stages") == SPEC_LIFE_STAGES
    assert V.codes("assay") == SPEC_ASSAY
    assert V.codes("population_flags") == SPEC_FLAGS
    assert V.scope_ids() == SPEC_SCOPES
    scope = V.load_scope()
    inf = next(s for s in scope["scopes"] if s["id"] == "infant_gut")
    assert inf["curated"] is True and sum(s["curated"] for s in scope["scopes"]) == 1


def test_uberon_lookup():
    assert V.uberon_primary_id("gut_stool") == "UBERON:0001988"
    assert {u["id"] for u in V.uberon_lookup("oral")} == {"UBERON:0000167", "UBERON:0001836", "UBERON:0016482"}
    assert V.uberon_lookup("unknown_site") == []
    with pytest.raises(V.VocabError):
        V.uberon_lookup("nope")


def test_age_bins_cover_infant_window():
    assert V.age_days_to_life_stage(0) == "neonate" and V.age_days_to_life_stage(28) == "neonate"
    assert V.age_days_to_life_stage(29) == "infant" and V.age_days_to_life_stage(1100) == "infant"
    assert V.age_days_to_life_stage(1101) == "child" and V.age_days_to_life_stage(30 * 365.25) == "adult"
    assert V.age_days_to_life_stage(70 * 365.25) == "elderly" and V.age_days_to_life_stage(None) is None


# --------------------------------------------------------------------------- matchers / traps
@pytest.mark.parametrize("text,code,expected", [
    ("Gut microbiome of the Gutierrez cohort", "gut_stool", ["Gut"]),           # 'gut' whole word; Gutierrez is not gut
    ("ulcerative colitis in kidney transplant applications", "gut_stool", []),  # rat / kid / cat not hosts; colitis not colon
    ("bacterial colonization of colonic mucosa", "gut_stool", ["colonic"]),     # colonization is not colon
    ("atherosclerotic plaque microbiome", "oral", []),                           # negative phrase cancels plaque
    ("plasmid-borne resistance in plasma", "blood_tissue", ["plasma"]),         # plasmid is not plasma
    ("Bal Harbour sewage", "respiratory_lower", []),                             # acronym BAL is case-sensitive
    ("BAL fluid from ventilated patients", "respiratory_lower", ["BAL"]),
    ("UBERON:feces | Not applicable", "gut_stool", ["feces"]),
    ("cow milk microbiota", "milk", []),                                          # non-human milk cancelled
    ("breast milk of lactating mothers", "milk", ["breast milk", "milk"]),
])
def test_body_site_matcher(text, code, expected):
    got = V.matcher("body_sites").match(text).get(code, [])
    assert got == expected


@pytest.mark.parametrize("text,code,expected", [
    ("Salmonella enterica serovar Infantis genomes", "infant", []),              # Infantis is a bacterium
    ("Bifidobacterium longum subsp. infantis isolates", "infant", []),
    ("stool of healthy infants at 6 months old", "infant", ["infants", "months old"]),
    ("infant macaques fed formula", "infant", []),                                # animal infant naming
    ("premature ovarian insufficiency cohort", "neonate", []),                    # premature FP list
    ("preterm neonates in the NICU", "neonate", ["preterm", "neonates", "NICU"]),
    ("kids with ADHD", "child", []),                                              # 'kid' not a child term (and not a host)
    ("adult mice fed high-fat diet", "adult", []),
])
def test_life_stage_matcher(text, code, expected):
    got = V.matcher("life_stages").match(text).get(code, [])
    assert sorted(got, key=str.lower) == sorted(expected, key=str.lower)


def test_word_boundary_traps_never_match_hosts():
    """The curation-skill trio: rat / kid / cat inside longer words must not fire anywhere in the vocabularies."""
    text = "ulcerative kidney applications concatenation education"
    for name in ("body_sites", "life_stages", "population_flags"):
        assert V.matcher(name).match(text) == {}, name


def test_split_multi_formats():
    assert split_multi('{"WGS": 12, "OTHER": 3}') == ["WGS", "OTHER"]
    assert split_multi("stool | missing;feces | not collected") == ["stool", "feces"]
    assert split_multi(None) == [] and split_multi(float("nan")) == []


# --------------------------------------------------------------------------- deterministic classifier: synthetic
def _rec(**kw):
    base = {"study_accession": "PRJTEST", "library_strategies": '{"WGS": 10}', "library_sources": "METAGENOMIC"}
    base.update(kw)
    return base


SYNTHETIC = [
    # (record, expectations)
    (_rec(study_title="Gut microbiome of preterm infants in the NICU", host_tax_ids="9606", isolation_sources="stool"),
     dict(host_human="yes", assay="shotgun_dna", body_site_primary="gut_stool", life_stage_primary="neonate", needs_llm=False)),
    (_rec(study_title="Piglet gut metagenome after weaning", host_tax_ids="9823", host_scientific_names="Sus scrofa"),
     dict(host_human="no", body_sites=[], life_stages=[], needs_llm=False)),
    (_rec(study_title="Salmonella Infantis outbreak isolates", library_sources="GENOMIC", scientific_names="Salmonella enterica", serovars="Infantis"),
     dict(assay="isolate_genome")),
    (_rec(study_title="Mock community benchmark", description="ZymoBIOMICS mock community sequenced on NovaSeq", scientific_names="synthetic metagenome"),
     dict(host_human="no")),
    (_rec(study_title="Human metagenome", scientific_names="human metagenome", host_tax_ids="9606"),
     dict(host_human="yes", body_site_primary="unknown_site", life_stage_primary="unknown_age", needs_llm=True)),
    (_rec(study_title="Saliva microbiome of adults with periodontitis", host_tax_ids="9606", isolation_sources="saliva"),
     dict(body_site_primary="oral", life_stage_primary="adult", population_flags=["disease_cohort"])),
    (_rec(study_title="Vaginal microbiome in pregnant women", host_tax_ids="9606"),
     dict(body_site_primary="vaginal_urogenital", life_stage_primary="adult", population_flags=["pregnant"])),
    (_rec(study_title="Mother-infant pairs: breast milk and infant stool", host_tax_ids="9606", isolation_sources="breast milk | stool"),
     dict(body_sites=["gut_stool", "milk"], population_flags=["mother_infant_pair"])),
    (_rec(study_title="Skin microbiome of the forearm in healthy volunteers", host_tax_ids="9606"),
     dict(body_site_primary="skin", population_flags=["healthy_volunteers"])),
    (_rec(study_title="Sputum metagenomics in cystic fibrosis patients", host_tax_ids="9606"),
     dict(body_site_primary="respiratory_lower")),
    (_rec(study_title="Nasopharyngeal microbiome of children with pneumonia", host_tax_ids="9606"),
     dict(body_site_primary="nasal_nasopharyngeal", life_stage_primary="child")),
    (_rec(study_title="Blood microbiome in sepsis", host_tax_ids="9606"), dict(body_site_primary="blood_tissue")),
    (_rec(study_title="Gut microbiome of centenarians", host_tax_ids="9606", isolation_sources="feces"),
     dict(life_stage_primary="elderly", body_site_primary="gut_stool")),
    (_rec(study_title="Human gut metagenome 16S", host_tax_ids="9606", target_genes="16S rRNA"), dict(assay="amplicon_misfiled")),
    (_rec(study_title="Gut metatranscriptome", host_tax_ids="9606", library_strategies='{"RNA-Seq": 5}', library_sources="METATRANSCRIPTOMIC"),
     dict(assay="rna")),
    (_rec(study_title="Amplicon survey", host_tax_ids="9606", library_strategies='{"AMPLICON": 5}'), dict(assay="amplicon")),
    (_rec(study_title="Human gut deep sequencing", host_tax_ids="9606", library_strategies='{"OTHER": 66}', base_count_median="7808523023.0",
          read_count_median="25856036.5", library_layouts="PAIRED", instrument_models="Illumina NovaSeq 6000"),
     dict(assay="shotgun_dna", needs_llm=True)),
    (_rec(study_title="Human gut shallow OTHER", host_tax_ids="9606", library_strategies='{"OTHER": 6}', base_count_median="4e7",
          read_count_median="86000", library_layouts="PAIRED", instrument_models="Illumina MiSeq"), dict(assay="other")),
    (_rec(study_title="Mixed design", host_tax_ids="9606", library_strategies='{"WGS": 50, "AMPLICON": 50}'), dict(assay="mixed")),
    (_rec(study_title="Human gut metagenome misfiled GENOMIC", host_tax_ids="9606", library_sources="GENOMIC", scientific_names="human gut metagenome"),
     dict(assay="shotgun_dna", needs_llm=True)),
    (_rec(study_title="Gut microbiome study", infant_verdict="exclude", infant_reason_code="host_nonhuman"),
     dict(host_human="no", classification_stage="deterministic_prior")),
    (_rec(study_title="Gut microbiome study", infant_verdict="exclude", infant_reason_code="age_adult_only", isolation_sources="stool"),
     dict(host_human="yes", life_stage_primary="adult", body_site_primary="gut_stool")),
    (_rec(study_title="Cohort", infant_verdict="exclude", infant_reason_code="age_child_over_36m", infant_body_site_call="gut"),
     dict(life_stage_primary="child", body_site_primary="gut_stool")),
    (_rec(study_title="Cohort", infant_verdict="exclude", infant_reason_code="age_maternal_only", infant_body_site_call="stool"),
     dict(life_stage_primary="adult", population_flags=["pregnant"])),
    (_rec(study_title="Cohort", infant_verdict="exclude", infant_reason_code="site_excluded", infant_body_site_call="oral"),
     dict(body_site_primary="oral")),
    (_rec(study_title="Cohort", infant_verdict="include"), dict(life_stage_primary="infant", body_site_primary="gut_stool", host_human="yes")),
    (_rec(study_title="Gut microbiota of mice and humans", description="humanized mice"),
     dict(host_human="unknown", needs_llm=True)),
    (_rec(study_title="Adenoid samples", scientific_names="human gut metagenome", host_tax_ids="9606", isolation_sources="adenoid tissue"),
     dict(body_site_primary="blood_tissue")),  # taxon is not body site: sample field wins over the gut taxon prior
    (_rec(study_title="Human gut metagenome", scientific_names="human gut metagenome"),
     dict(host_human="yes", body_site_primary="gut_stool", needs_llm=True)),  # taxon prior only → weak, review
    (_rec(study_title="Stool of infants aged 3 months", host_tax_ids="9606", ages="3 months | 6 months | 45"),
     dict(life_stage_primary="infant")),  # unitless 45 ignored
]


@pytest.mark.parametrize("rec,exp", SYNTHETIC, ids=[r["study_title"][:40] for r, _ in SYNTHETIC])
def test_synthetic_classification(rec, exp):
    r = classify_study(rec)
    for k, v in exp.items():
        if isinstance(v, list):
            assert sorted(r[k]) == sorted(v) if k != "population_flags" else set(v) <= set(r[k]), (k, r[k])
        else:
            assert r[k] == v, (k, r[k], r["needs_llm_reasons"])
    for key in ("host_evidence", "assay_evidence", "body_site_evidence", "life_stage_evidence", "population_evidence"):
        if r[key]:
            ok, msg = validate_evidence(r[key])
            assert ok, (key, msg)
            assert all(len(e["quote"].split()) <= 12 for e in r[key])


def test_synthetic_count():
    assert len(SYNTHETIC) >= 30


# --------------------------------------------------------------------------- real universe rows (fixture)
@pytest.fixture(scope="module")
def fixture_universe():
    assert os.path.exists(FIXTURE), "tests/data/registry_fixture_universe.parquet missing (make registry-fixture)"
    return pd.read_parquet(FIXTURE)


@pytest.fixture(scope="module")
def infant_universe(fixture_universe):
    if os.path.exists(INFANT_UNIVERSE):
        return pd.read_parquet(INFANT_UNIVERSE)
    # offline fallback: verdict columns embedded in the fixture's provenance side file
    side = FIXTURE.replace(".parquet", "_verdicts.parquet")
    assert os.path.exists(side), "no infant universe available"
    return pd.read_parquet(side)


def test_fixture_shape(fixture_universe):
    assert len(fixture_universe) == 200 and fixture_universe.study_accession.is_unique


def test_real_rows_host_and_infant(fixture_universe, infant_universe):
    inf = infant_universe[infant_universe.release_retired.isna()] if "release_retired" in infant_universe.columns else infant_universe
    reg = B.assemble(fixture_universe, inf, None, "R2026.4", "1.6.0")
    chk = reg.merge(inf[["study_accession", "triage_verdict", "reason_code"]], on="study_accession")
    # a curator audit / owner decision (config/registry_overrides.yaml) supersedes the infant triage host verdict (R2026.20:
    # e.g. PRJEB50505, every BioSample "human gut metagenome", had been triaged host_nonhuman)
    audited = chk.classification_stage.isin(["curator_audit", "owner_decision"])
    nonhuman = chk[chk.reason_code.isin(["host_nonhuman", "host_environmental", "host_synthetic"]) & ~audited]
    assert len(nonhuman) >= 60 and (nonhuman.host_human == "no").all()
    assert (nonhuman.body_sites == "").all() and (nonhuman.life_stages == "").all()
    inc = chk[chk.triage_verdict == "include"]
    assert len(inc) >= 30
    assert (inc.host_human == "yes").all()
    assert (inc.life_stage_primary == "infant").all() and inc.life_stages.str.contains("infant").all()
    assert (inc.body_site_primary == "gut_stool").all() and inc.body_sites.str.contains("gut_stool").all()
    assert (inc.in_infant_catalog == "include").all() and inc.scope_memberships.str.contains("infant_gut").all()
    adults = chk[chk.reason_code == "age_adult_only"]
    assert (adults.life_stage_primary == "adult").all()
    assert set(chk.in_infant_catalog) <= {"include", "exclude", "uncertain", "not_screened"}
    # deterministic: a second assembly is identical
    reg2 = B.assemble(fixture_universe, inf, None, "R2026.4", "1.6.0")
    pd.testing.assert_frame_equal(reg, reg2)


def test_schema_conformance(fixture_universe, infant_universe):
    reg = B.assemble(fixture_universe, infant_universe, None, "R2026.4", "1.6.0")
    B.validate_registry(reg)  # raises on any violation
    assert list(reg.columns) == B.study_columns()
    assert list(reg.columns[-3:]) == ["release_added", "release_retired", "package_added"]
    assert (reg.release_added == "R2026.4").all() and reg.release_retired.isna().all()


# --------------------------------------------------------------------------- scope derivation
@pytest.mark.parametrize("host,sites,stages,assay,inf,expected", [
    ("yes", ["gut_stool"], ["infant"], "shotgun_dna", "include", ["human_all", "infant_gut"]),
    ("yes", ["gut_stool"], ["adult", "elderly"], "shotgun_dna", "exclude", ["human_all", "gut_adult"]),
    ("yes", ["gut_stool"], ["child"], "shotgun_dna", "exclude", ["human_all", "gut_child"]),
    ("yes", ["oral", "skin"], ["adult"], "shotgun_dna", "not_screened", ["human_all", "oral", "skin"]),
    ("yes", ["nasal_nasopharyngeal"], ["child"], "shotgun_dna", "exclude", ["human_all", "respiratory"]),
    ("yes", ["unknown_site"], ["unknown_age"], "shotgun_dna", "exclude", ["human_all", "unknown_site"]),
    ("yes", ["eye_ear"], ["adult"], "shotgun_dna", "exclude", ["human_all", "other_site"]),
    ("no", [], [], "shotgun_dna", "exclude", []),
    ("yes", ["gut_stool"], ["adult"], "amplicon", "exclude", ["gut_adult"]),          # not shotgun → not in human_all
    ("mixed", ["gut_stool"], ["adult"], "mixed", "exclude", ["human_all", "gut_adult"]),
])
def test_scope_derivation(host, sites, stages, assay, inf, expected):
    assert B.derive_scope_memberships(host, sites, stages, assay, inf) == expected


# --------------------------------------------------------------------------- LLM stage: parser / validator / agreement
def _good(acc="PRJNA1", **kw):
    o = {"study_accession": acc, "host_human": "yes", "assay": "shotgun_dna", "body_sites": ["gut_stool"], "body_site_primary": "gut_stool",
         "life_stages": ["adult"], "life_stage_primary": "adult", "population_flags": ["disease_cohort"], "health_context": "IBD", "confidence": 0.9,
         "evidence": {"host_human": [{"source": "run.host_tax_id", "quote": "9606"}], "assay": [{"source": "run.library_strategy", "quote": "WGS"}],
                      "body_site": [{"source": "sample.attr.isolation_source", "quote": "stool"}],
                      "life_stage": [{"source": "study.title", "quote": "adults with IBD"}],
                      "population_flags": {"disease_cohort": [{"source": "study.title", "quote": "IBD"}]}}}
    o.update(kw)
    return o


def test_parse_every_id_returned_and_missing_sentinel():
    res = {"tool_use": {"input": {"studies": [_good("PRJNA1"), _good("PRJNA9")]}}, "usage": {"input_tokens": 100, "output_tokens": 50}}
    rows, st = L.parse_and_validate(res, ["PRJNA1", "PRJNA2"], "sonnet-test")
    assert [r["study_accession"] for r in rows] == ["PRJNA1", "PRJNA2"]
    assert rows[0]["outcome"] == "predicted" and rows[1]["outcome"] == "sentinel_no_evidence"
    assert st["missing"] == 1 and st["extra"] == 1 and st["tokens"] == 150


def test_parse_unparsable():
    rows, st = L.parse_and_validate({"text": "sorry"}, ["A", "B"], "m")
    assert st["unparsable"] == 1 and all(r["outcome"] == "sentinel_no_evidence" for r in rows)


def test_validator_long_quote_downgrades_that_decision_only():
    o = _good(evidence={**_good()["evidence"], "life_stage": [{"source": "study.title", "quote": " ".join(["w"] * 15)}]})
    row, probs = L.validate_study(o, "m")
    assert row["life_stage_primary"] == "unknown_age" and row["body_site_primary"] == "gut_stool"
    assert any("life_stage" in p for p in probs) and row["outcome"] == "predicted"


def test_validator_bad_vocab_rejects():
    row, probs = L.validate_study(_good(assay="metaG"), "m")
    assert row["outcome"] == "validator_rejected" and row["assay"] == "unknown"
    row, probs = L.validate_study(_good(body_sites=["tummy"], body_site_primary="tummy"), "m")
    assert row["outcome"] == "validator_rejected"


def test_validator_unlabelled_source_rejected():
    o = _good(evidence={**_good()["evidence"], "host_human": [{"source": "general knowledge", "quote": "humans"}]})
    row, probs = L.validate_study(o, "m")
    assert row["host_human"] == "unknown" and any("host_human" in p for p in probs)


def test_validator_animal_clears_site_and_stage():
    o = _good(host_human="no", evidence={**_good()["evidence"], "host_human": [{"source": "run.host_scientific_name", "quote": "Sus scrofa"}]})
    row, _ = L.validate_study(o, "m")
    assert row["body_sites"] == [] and row["life_stage_primary"] is None


def test_text_json_fallback_and_text_result():
    txt = json.dumps({"studies": [_good("X")]})
    assert L.tool_input({"text": "here: " + txt})["studies"][0]["study_accession"] == "X"
    assert L.tool_input(txt)["studies"]


def test_merge_replicates_agreement_and_disagreement():
    r1, _ = L.validate_study(_good("A"), "m")
    r2, _ = L.validate_study(_good("A", confidence=0.7), "m")
    d1, _ = L.validate_study(_good("B"), "m")
    d2, _ = L.validate_study(_good("B", life_stage_primary="child", life_stages=["child"]), "m")
    merged, queue = L.merge_replicates([r1, d1], [r2, d2], "sonnet-test")
    assert queue == ["B"]
    a = next(r for r in merged if r["study_accession"] == "A")
    assert a["classification_stage"] == "sonnet_x2" and a["confidence"] == 0.8
    b = next(r for r in merged if r["study_accession"] == "B")
    assert b["classification_stage"] == "pending" and "disagreement" in b["note"]


def test_batches_and_request_shape():
    recs = [{"study_accession": f"PRJ{i}", "study_title": "t", "library_strategies": '{"WGS": 1}'} for i in range(14)]
    batches = L.build_batches(recs, {"PRJ0": {"host_human": "yes", "needs_llm_reasons": ["site_unknown"]}}, 6)
    assert [len(b["ids"]) for b in batches] == [6, 6, 2]
    assert batches[0]["records"][0]["deterministic_hint"]["host_human"] == "yes"
    req = L.make_request(batches[0], "model-x")
    assert req["model"] == "model-x" and req["tool_choice"]["name"] == "registry_classification"
    assert "PRJ0, PRJ1" in req["prompt"] and "Infantis" in req["system"]
    schema = req["tools"][0]["input_schema"]
    assert schema["properties"]["studies"]["items"]["properties"]["assay"]["enum"] == SPEC_ASSAY


def test_to_registry_row_columns():
    r, _ = L.validate_study(_good("A"), "m")
    r["classification_stage"] = "sonnet_x2"
    row = L.to_registry_row(r)
    assert row["body_sites"] == "gut_stool" and json.loads(row["host_evidence"])[0]["source"] == "run.host_tax_id"


def test_models_resolve_through_roles(monkeypatch):
    from catalog import models
    monkeypatch.setenv("CATALOG_MODEL_RUBRIC", "sonnet-env")
    monkeypatch.setenv("CATALOG_MODEL_ADJUDICATE", "opus-env")
    models._HOST = None
    assert L.resolve_models(None) == {"replicate": "sonnet-env", "adjudicate": "opus-env"}


def test_cost_log():
    log = L.CostLog()
    log.add("sonnet_rep1", "m", 6, 6000)
    log.add("sonnet_rep1", "m", 6, 6600)
    s = log.summary()
    assert s["stages"]["sonnet_rep1"]["tokens_per_study"] == 1050.0 and s["total_tokens"] == 12600


def test_conservative_merge_keeps_agreed_fields_and_flags_pending():
    from catalog.registry.classify_llm import conservative_merge, sentinel, to_registry_row
    base = dict(study_accession="PRJX1", host_human="yes", assay="shotgun_dna", body_sites=["gut_stool"], body_site_primary="gut_stool",
                life_stages=["adult"], life_stage_primary="adult", population_flags=["hospitalised"], health_context="IBD", confidence=0.8,
                evidence={"host_human": [{"field": "study_title", "quote": "human gut"}], "assay": [], "body_site": [], "life_stage": [], "population_flags": {}},
                model="m", outcome="predicted", note=None)
    r2 = dict(base, body_sites=["gut_stool", "oral"], life_stage_primary="child", life_stages=["child"], population_flags=[], confidence=0.6)
    m = conservative_merge(base, r2, "m", note="adjudication unavailable")
    assert m["classification_stage"] == "pending" and m["outcome"] == "replicates_unadjudicated"
    assert m["host_human"] == "yes" and m["body_site_primary"] == "gut_stool" and m["life_stage_primary"] == "unknown_age"
    assert m["body_sites"] == ["gut_stool", "oral"] and m["life_stages"] == ["adult", "child"] and m["population_flags"] == []
    assert m["confidence"] == 0.3 and "adjudication unavailable" in m["note"]
    row = to_registry_row(m)
    assert row["body_sites"] == "gut_stool;oral" and row["classification_stage"] == "pending"
    # one failed replicate → the valid one, capped confidence
    s = sentinel("PRJX1", "m", "llm_error")
    m2 = conservative_merge(base, s, "m")
    assert m2["host_human"] == "yes" and m2["confidence"] == 0.5 and m2["classification_stage"] == "pending"
    # both failed → sentinel, pending
    m3 = conservative_merge(s, dict(s), "m")
    assert m3["outcome"] == "sentinel_no_evidence" and m3["classification_stage"] == "pending"


def test_carry_infant_universe_appends_missing_included_study():
    from catalog.registry.build_registry import carry_infant_universe, CARRY_FOUND_BY
    uni = pd.DataFrame({"study_accession": ["PRJA1"], "study_title": ["x"], "n_runs": [3], "candidate_class": ["nosignal_new"],
                        "found_by": ["S1"], "in_infant_catalog": ["not_screened"], "description_short": [""]})
    inf = pd.DataFrame({"study_accession": ["PRJA1", "PRJB2", "PRJC3"], "triage_verdict": ["exclude", "include", "include"],
                        "study_title": ["x", "carried", "retired"], "n_runs": [3, 7, 1], "reason_code": ["r", "r", "r"],
                        "release_retired": [None, None, "R2026.2"]})
    out = carry_infant_universe(uni, inf)
    assert list(out.study_accession) == ["PRJA1", "PRJB2"]
    row = out.iloc[1]
    assert row.found_by == CARRY_FOUND_BY and row.candidate_class == "prior_human" and row.in_infant_catalog == "include"
    assert row.study_title == "carried" and row.n_runs == 7 and row.description_short == ""
    assert carry_infant_universe(out, inf).shape == out.shape  # idempotent


def test_curated_precedence_overrides_archive_only_values_for_included_studies():
    from catalog.registry.build_registry import apply_curated_precedence, derive_scope_memberships
    df = pd.DataFrame({"in_infant_catalog": ["include", "include", "exclude"], "host_human": ["yes", "unknown", "no"],
                       "assay": ["other", "shotgun_dna", "other"], "body_sites": ["unknown_site", "gut_stool;oral", ""],
                       "body_site_primary": ["unknown_site", "oral", None], "life_stages": ["unknown_age", "adult", ""],
                       "life_stage_primary": ["unknown_age", "adult", None]})
    out = apply_curated_precedence(df)
    assert list(out.assay) == ["shotgun_dna", "shotgun_dna", "other"] and list(out.host_human) == ["yes", "yes", "no"]
    assert out.body_sites[0] == "gut_stool" and out.body_site_primary[0] == "gut_stool" and out.life_stage_primary[0] == "infant"
    assert out.body_sites[1] == "gut_stool;oral" and out.body_site_primary[1] == "oral" and out.life_stages[1] == "adult;infant"
    assert out.attrs["curated_precedence_applied"] == 2 and out.body_sites[2] == ""
    assert "human_all" in derive_scope_memberships("yes", ["gut_stool"], ["infant"], "shotgun_dna", "include")


def test_carry_release_columns_keeps_added_and_retires_vanished():
    from catalog.registry.build_registry import carry_release_columns
    prev = pd.DataFrame({"study_accession": ["A", "B", "C"], "v": [1, 2, 3], "release_added": ["R2026.4", "R2026.4", "R2026.3"],
                         "release_retired": [None, None, "R2026.4"], "package_added": ["1.6.0", "1.6.0", "1.5.0"]})
    cur = pd.DataFrame({"study_accession": ["A", "D"], "v": [10, 40], "release_added": ["R2026.5", "R2026.5"], "release_retired": [None, None], "package_added": ["1.7.0", "1.7.0"]})
    out = carry_release_columns(cur, prev, ["study_accession"], "R2026.5").set_index("study_accession")
    assert out.loc["A", "release_added"] == "R2026.4" and out.loc["A", "package_added"] == "1.6.0" and out.loc["A", "v"] == 10
    assert out.loc["D", "release_added"] == "R2026.5"
    assert out.loc["B", "release_retired"] == "R2026.5" and out.loc["B", "v"] == 2      # vanished → retired this release
    assert out.loc["C", "release_retired"] == "R2026.4"                                  # earlier retirement carried verbatim
    assert len(out) == 4


def test_build_biosamples_picks_best_code_and_rolls_up():
    from catalog.registry.build_biosamples import build_biosamples, rollup_studies
    att = pd.DataFrame([
        ("S1", "P1", "host_body_site", "stool", "body_site", False), ("S1", "P1", "isolation_source", "feces", "body_site", False),
        ("S1", "P1", "host_age", "34", "age", False), ("S1", "P1", "sex", "F", "sex", False), ("S1", "P1", "geo_loc_name", "USA: Ohio", "country", False),
        ("S1", "P1", "collection_date", "2019-05-02", "collection_date", False), ("S1", "P1", "host_disease", "IBD", "disease", False),
        ("S2", "P1", "host_body_site", "stool", "body_site", False), ("S2", "P1", "host_age", "missing", "age", True),
        ("S3", "P1", "host_body_site", "stool", "body_site", False), ("S3", "P1", "host_age", "2 months", "age", False),
        ("S4", "P2", "env_medium", "soil", "body_site", False)],
        columns=["sample_acc", "study_accession", "attr_key_norm", "attr_value", "field", "is_placeholder"])
    att["acc_resolved"], att["attr_key"], att["source"], att["attr_units"] = att.sample_acc, att.attr_key_norm, "ena_xml", None
    norm = pd.DataFrame([
        ("body_site", "host_body_site", "stool", "gut_stool", None, None, None, None, 0.99), ("body_site", "isolation_source", "feces", "gut_stool", None, None, None, None, 0.9),
        ("age", "host_age", "34", None, "adult", 34 * 365.25, None, None, 0.9), ("sex", "sex", "F", None, None, None, "female", None, 0.95),
        ("country", "geo_loc_name", "USA: Ohio", None, None, None, None, "US", 0.99), ("age", "host_age", "2 months", None, "infant", 61.0, None, None, 0.95),
        ("body_site", "env_medium", "soil", "unknown_site", None, None, None, None, 0.9)],
        columns=["field", "attr_key_norm", "attr_value", "body_site_code", "life_stage", "age_days", "sex", "country_iso2", "confidence"])
    norm["null_output"] = False
    bios = build_biosamples(att, norm).set_index("sample_accession")
    assert bios.loc["S1", "body_site_code"] == "gut_stool" and bios.loc["S1", "body_site_raw_key"] == "host_body_site"
    assert bios.loc["S1", "life_stage"] == "adult" and abs(bios.loc["S1", "age_days"] - 34 * 365.25) < 1e-6 and bios.loc["S1", "sex"] == "female"
    assert bios.loc["S1", "country_iso2"] == "US" and bios.loc["S1", "collection_year"] == 2019 and bios.loc["S1", "disease_raw"] == "IBD"
    assert pd.isna(bios.loc["S2", "life_stage"]) and bios.loc["S3", "life_stage"] == "infant"   # placeholder age → no attribute; 2 months → infant
    assert bios.loc["S4", "body_site_code"] == "unknown_site"                                      # attribute existed, no site
    studies = pd.DataFrame({"study_accession": ["P1", "P2"], "body_sites": ["unknown_site", "unknown_site"], "body_site_primary": ["unknown_site", "unknown_site"],
                            "body_site_evidence": ["[]", "[]"], "life_stages": ["unknown_age", ""], "life_stage_primary": ["unknown_age", None], "life_stage_evidence": ["[]", "[]"]})
    st, stats = rollup_studies(studies, bios.reset_index())
    p1 = st.set_index("study_accession").loc["P1"]
    assert p1.n_biosamples_harvested == 3 and p1.n_biosamples_with_site == 3 and p1.n_biosamples_with_age == 2 and p1.n_biosamples_with_sex == 1
    assert p1.body_site_primary == "gut_stool" and p1.body_sites == "gut_stool" and json.loads(p1.body_site_evidence)[0]["source"] == "sample.attr.host_body_site"
    assert json.loads(p1.sample_life_stages) == {"adult": 1, "infant": 1} and p1.life_stage_primary == "unknown_age"   # no 60 % majority
    assert stats["site_primary_refined"] == 1 and stats["sites_added"] == 1
    p2 = st.set_index("study_accession").loc["P2"]
    assert p2.n_biosamples_harvested == 1 and p2.body_site_primary == "unknown_site"   # < 3 samples: no refinement
