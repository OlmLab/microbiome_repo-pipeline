"""Author attribution rules (R2026.25 fix after an author's report): generated-only credit, 'unsure' kept apart,
owner overrides, ORCID merges applied row-wise only."""
import sys
from pathlib import Path
import pandas as pd
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import build_site as B


def _pkg(tmp_path):
    base = dict(position=1, is_first=False, is_last=False)
    ra = pd.DataFrame([
        dict(study_accession='S1', pmid='1', author_display='Morgan XC', author_key='morgan xc', paper_relation='own_data', orcid='0000-0002-6264-6961', **base),
        dict(study_accession='S2', pmid='2', author_display='Morgan X', author_key='morgan x', paper_relation='own_data', orcid='0000-0002-6264-6961', **base),
        dict(study_accession='S3', pmid='3', author_display='Morgan X', author_key='morgan x', paper_relation='own_data', orcid=None, **base),
        dict(study_accession='S4', pmid='4', author_display='Zhou J', author_key='zhou j', paper_relation='unsure', orcid=None, **base),
    ])
    ia = pd.DataFrame([
        dict(study_accession='S5', pmid='5', author_display='Morgan XC', author_key='morgan xc', paper_relation='reused_public_data', link_method='llm_sonnet', **base),
        dict(study_accession='S6', pmid='6', author_display='Olm MR', author_key='olm mr', paper_relation=None, link_method='bioproject_publication_epmc', **base),
        dict(study_accession='S7', pmid='7', author_display='Olm MR', author_key='olm mr', paper_relation='cited_only', link_method='llm_sonnet', **base),
    ])
    ra.to_parquet(tmp_path / 'registry_authors.parquet'); ia.to_parquet(tmp_path / 'authors.parquet')
    ov = tmp_path / 'ov.csv'
    ov.write_text('# c\naction,study_accession,author_key,author_display,pmid,is_first,is_last,note\n'
                  'remove_author,S3,morgan x,,,,,x\nadd_author,S8,morgan xc,Morgan XC,8,1,0,x\n')
    return ov


def test_author_rules(tmp_path):
    ov = _pkg(tmp_path)
    gen, poss = B.load_site_authors(tmp_path, {f'S{i}' for i in range(1, 9)}, ov)
    pairs = set(zip(gen.study_accession, gen.author_key))
    assert pairs == {('S1', 'morgan xc'), ('S2', 'morgan xc'), ('S6', 'olm mr'), ('S8', 'morgan xc')}
    assert set(zip(poss.study_accession, poss.author_key)) == {('S4', 'zhou j')}
    assert set(gen.author_display) == {'Morgan XC', 'Olm MR'}
