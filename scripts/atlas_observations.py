#!/usr/bin/env python
"""Recompute the Atlas observation cards (atlas/observations.html) from the released tables.

Inputs (all shipped with the data release / the Sandpiper leaf outputs):

* ``--genus``   catalog_sandpiper_sample_genus.parquet   (sample_key, genus, lineage, relabund)
* ``--species`` catalog_sandpiper_sample_species.parquet (sample_key, species, relabund) - released at relabund >= 1e-3
* ``--summary`` catalog_sandpiper_sample_summary.parquet (sample_key, study_accession, body_site_class, qc_*, age_category,
                richness_genus, shannon_genus, root_coverage_sum)
* ``--pca``     catalog_sandpiper_pca_scores.parquet (sample_key, pc1..pc5) - optional, used by the variance card
* ``--wide``    catalog_sample_metadata_wide.parquet (country, health_condition, delivery_mode, feeding_mode, lifestyle,
                collection_year, location_site, ...)
* ``--hdi``     the official UNDP HDI table (iso2, hdi_2023); default = the copy shipped with the site generator

Output (``--out``, normally the precomputed atlas payload directory): ``obs/<card>.png`` + ``obs/<card>.csv`` per card
and ``observations.json`` (``meta`` + ``cards``), which ``site_generator/gen/pages/atlas.py`` renders.

Every card is study-aware: pooled numbers are always accompanied by the number of studies, and two-group contrasts
report (a) the fraction of studies (>= MIN_STUDY_N samples in both groups) whose within-study effect has the sign of the
median and (b) the median of the per-study effects.  Cards that cannot be computed from the supplied tables (missing
columns, too few studies) are skipped and listed in ``observations.json -> meta.skipped`` so a partial input (or the
synthetic test fixture) still yields a valid payload.

The script never invents values: every number on a card is computed here from the input tables and written to the
card's CSV.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import scipy.sparse as sps
from scipy import stats

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt  # noqa: E402

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / 'site_generator' / 'gen'))
from pages import atlas  # noqa: E402  (analysis_samples, hdi_band, HDI_SOURCE, load_hdi, PRESENCE)

PSEUDO = 1e-5                      # log10(relabund + PSEUDO)
MIN_STUDY_N = 20                   # samples per group for a study to enter a within-study contrast
MIN_COUNTRY_N = atlas.MIN_COUNTRY_SAMPLES
AGE_ORDER = ['neonate', 'infant', 'child', 'adolescent', 'adult', 'elderly', 'unknown']
ADULT_LIKE = ['child', 'adolescent', 'adult', 'elderly']
EARLY = ['neonate', 'infant']
NON_INDUSTRIAL = ['hunter_gatherer', 'pastoralist', 'traditional_agriculturalist', 'rural_non_industrialized']
HOSPITAL_RE = re.compile(r'hospital|clinic|medical cent|health cent|infirmary|nicu|intensive care|ward\b')
COMMUNITY_RE = re.compile(r'community|village|household|town\b|settlement')
DPI = 110
BAND_COL = {'low': '#b2182b', 'middle': '#f4a582', 'high': '#2166ac', None: '#999999'}
GENUS_COL = {'Bifidobacterium': '#7b3294', 'Bacteroides': '#1b7837', 'Phocaeicola': '#5aae61', 'Prevotella': '#d6604d',
             'Escherichia': '#e08214', 'Faecalibacterium': '#4393c3', 'Enterococcus': '#8c510a', 'Klebsiella': '#bf812d',
             'Akkermansia': '#01665e', 'Fusobacterium': '#762a83'}
LOG = []


def log(msg):
    LOG.append(msg); print(f'[atlas_observations] {msg}', file=sys.stderr, flush=True)


def style():
    plt.rcParams.update({'font.size': 8, 'axes.titlesize': 8, 'axes.labelsize': 8, 'legend.fontsize': 7, 'xtick.labelsize': 6.5, 'ytick.labelsize': 6.5,
                         'axes.spines.top': False, 'axes.spines.right': False, 'axes.titlelocation': 'left', 'legend.frameon': False,
                         'figure.dpi': DPI, 'savefig.dpi': DPI, 'savefig.bbox': 'tight', 'savefig.pad_inches': 0.04, 'axes.grid': False})


def fmt_n(n):
    return f'{int(n):,}'


def pretty(g):
    return g[3:] if isinstance(g, str) and g[1:3] == '__' else g


# ----------------------------------------------------------------------------------------------------------------- data
class Data:
    """Analysis sample table S (one row per sample) + sparse sample x genus relabund matrix M."""

    def __init__(self, genus_long, species_long, summary, wide, pca=None, hdi=None):
        self.hdi = hdi if hdi is not None else atlas.HDI_2023
        S = atlas.analysis_samples(summary)
        keep = [c for c in ['country', 'health_condition', 'delivery_mode', 'feeding_mode', 'lifestyle', 'collection_year', 'location_site',
                            'detailed_location', 'subject_id'] if c in wide.columns]
        S = S.merge(wide[['sample_key'] + keep].drop_duplicates('sample_key'), on='sample_key', how='left').reset_index(drop=True)
        for c in ['age_category', 'country', 'health_condition', 'delivery_mode', 'feeding_mode', 'lifestyle', 'location_site', 'richness_genus',
                  'shannon_genus', 'root_coverage_sum', 'collection_year']:
            if c not in S.columns:
                S[c] = np.nan
        for c in ['age_category', 'country', 'health_condition', 'delivery_mode', 'feeding_mode', 'lifestyle', 'location_site', 'study_accession']:
            S[c] = S[c].astype(object).where(S[c].notna(), np.nan)   # plain object dtype: Arrow-backed strings give NA-valued masks
        S['age_category'] = S.age_category.fillna('unknown').astype(str)
        S['hdi'] = S.country.map(self.hdi).where(S.country.notna())   # never let a NaN country pick up a NaN dict key
        S['band'] = S.hdi.map(atlas.hdi_band)
        self.S = S
        self.N = len(S)
        key2idx = pd.Series(np.arange(self.N), index=S.sample_key.values)
        g = genus_long[genus_long.sample_key.isin(set(S.sample_key)) & (genus_long.genus != 'unassigned_at_genus')]
        self.genera = pd.Index(sorted(g.genus.unique()))
        rows = key2idx.reindex(g.sample_key.values).values.astype(np.int64)
        cols = self.genera.get_indexer(g.genus.values)
        self.M = sps.csr_matrix((g.relabund.values.astype(np.float32), (rows, cols)), shape=(self.N, len(self.genera)))
        self.lineage = g.drop_duplicates('genus').set_index('genus').lineage.reindex(self.genera)
        self.prev = np.asarray((self.M >= atlas.PRESENCE).sum(axis=0)).ravel() / self.N
        # richness / top genus straight from the matrix when the summary lacks them
        if S.richness_genus.isna().all():
            S['richness_genus'] = np.asarray((self.M > 0).sum(axis=1)).ravel()
        top = np.asarray(self.M.argmax(axis=1)).ravel()
        S['top_genus_atlas'] = np.where(np.asarray(self.M.max(axis=1).todense()).ravel() > 0, self.genera.values[top], 'none')
        self.species_long = species_long
        self.pca = pca
        self._cache = {}

    def genus(self, name):
        """Dense relabund vector of one genus (g__ prefix optional); zeros if absent from the table."""
        name = name if name.startswith('g__') else 'g__' + name
        if name not in self._cache:
            j = self.genera.get_loc(name) if name in self.genera else None
            self._cache[name] = np.zeros(self.N, dtype=np.float32) if j is None else self.M[:, j].toarray().ravel()
        return self._cache[name]

    def genus_group(self, prefix):
        """Sum of all GTDB genera whose name starts with prefix (e.g. 'g__Fusobacterium' -> Fusobacterium, Fusobacterium_A, ...)."""
        js = [i for i, n in enumerate(self.genera) if n == prefix or n.startswith(prefix + '_')]
        return np.zeros(self.N, dtype=np.float32) if not js else np.asarray(self.M[:, js].sum(axis=1)).ravel()

    def family(self, fam):
        js = [i for i, l in enumerate(self.lineage.values) if isinstance(l, str) and f'; {fam};' in l + ';']
        return np.zeros(self.N, dtype=np.float32) if not js else np.asarray(self.M[:, js].sum(axis=1)).ravel()

    def lg(self, x):
        return np.log10(np.asarray(x, dtype=np.float64) + PSEUDO)


# ----------------------------------------------------------------------------------------------------------- statistics
def within_study(S, mask_a, mask_b, values, min_n=MIN_STUDY_N, label_a='a', label_b='b'):
    """Per-study effect = mean(values | b) - mean(values | a) for studies with >= min_n samples in both groups.

    Returns (summary dict, per-study DataFrame).  frac_agree = share of studies whose effect has the sign of the median.
    """
    v = np.asarray(values, dtype=np.float64)
    df = pd.DataFrame(dict(study=S.study_accession.values, grp=np.where(mask_a, 'a', np.where(mask_b, 'b', None)), v=v))
    df = df[df.grp.notna()]
    if df.empty:
        return dict(n_studies=0, median_effect=None, frac_agree=None, pooled_effect=None, n_a=int(mask_a.sum()), n_b=int(mask_b.sum())), pd.DataFrame()
    t = df.groupby(['study', 'grp']).v.agg(['mean', 'size']).unstack()
    ok = t[('size', 'a')].fillna(0).ge(min_n) & t[('size', 'b')].fillna(0).ge(min_n)
    per = pd.DataFrame(dict(study=t.index[ok], n_a=t.loc[ok, ('size', 'a')].astype(int).values, n_b=t.loc[ok, ('size', 'b')].astype(int).values,
                            mean_a=t.loc[ok, ('mean', 'a')].values, mean_b=t.loc[ok, ('mean', 'b')].values))
    per['effect'] = per.mean_b - per.mean_a
    pooled = float(v[mask_b].mean() - v[mask_a].mean()) if mask_a.any() and mask_b.any() else None
    if len(per):
        med = float(per.effect.median()); agree = float((np.sign(per.effect) == np.sign(med)).mean()) if med != 0 else 0.5
    else:
        med = agree = None
    return dict(n_studies=int(len(per)), median_effect=med, frac_agree=agree, pooled_effect=pooled, n_a=int(mask_a.sum()), n_b=int(mask_b.sum()),
                label_a=label_a, label_b=label_b), per


def study_level(S, mask_lo, mask_hi, values, min_n=MIN_STUDY_N):
    """Unit = study: mean of values over each study's samples in the group; Mann-Whitney over studies (two-sided)."""
    v = np.asarray(values, dtype=np.float64)
    out = {}
    for k, m in (('lo', mask_lo), ('hi', mask_hi)):
        d = pd.DataFrame(dict(study=S.study_accession.values[m], v=v[m])).groupby('study').v.agg(['mean', 'size'])
        out[k] = d[d['size'] >= min_n]['mean']
    a, b = out['lo'], out['hi']
    if len(a) < 2 or len(b) < 2:
        return dict(n_lo=int(len(a)), n_hi=int(len(b)), effect=None, p=None, frac_lo_higher=None), a, b
    p = float(stats.mannwhitneyu(a, b, alternative='two-sided').pvalue)
    u = stats.mannwhitneyu(a, b, alternative='two-sided').statistic
    return dict(n_lo=int(len(a)), n_hi=int(len(b)), effect=float(a.median() - b.median()), p=p, frac_lo_higher=float(u / (len(a) * len(b)))), a, b


def bh(p):
    p = np.asarray(p, dtype=float); n = len(p); o = np.argsort(p); r = np.empty(n); r[o] = np.arange(1, n + 1)
    q = np.minimum.accumulate((p[o] * n / np.arange(1, n + 1))[::-1])[::-1]; out = np.empty(n); out[o] = np.minimum(q, 1); return out


def consistency_sentence(name, c):
    if not c['n_studies']:
        return f'{name}: no study with >= {MIN_STUDY_N} samples in both groups'
    return (f"{name}: {c['n_studies']} studies, {c['frac_agree'] * 100:.0f}% agree, median Δlog10={c['median_effect']:+.2f} "
            f"(pooled {c['pooled_effect']:+.2f})")


def effect_dot_plot(ax, pers, names, title, xlabel):
    """Per-study effects as faint dots, the median as a black square (one row per feature)."""
    for i, (n, per) in enumerate(zip(names, pers)):
        col = GENUS_COL.get(n, '#555555')
        if len(per):
            ax.scatter(per.effect, np.full(len(per), i) + np.random.default_rng(i).uniform(-0.18, 0.18, len(per)), s=9, color=col, alpha=0.45, lw=0)
            ax.scatter([per.effect.median()], [i], marker='s', s=34, color='black', zorder=3)
    ax.axvline(0, color='#888888', lw=0.7)
    ax.set_yticks(range(len(names))); ax.set_yticklabels([n.replace('_', ' ') for n in names], style='italic'); ax.invert_yaxis()
    ax.set_xlabel(xlabel); ax.set_title(title); ax.margins(0.05)


# ------------------------------------------------------------------------------------------------------------- cards
class Cards:
    def __init__(self, D: Data, out_dir: Path, seed=42):
        self.D = D; self.S = D.S; self.out = Path(out_dir); self.obs = self.out / 'obs'; self.obs.mkdir(parents=True, exist_ok=True)
        self.seed = seed; self.cards = []; self.skipped = []

    def save(self, fig, cid, table: pd.DataFrame):
        fig.savefig(self.obs / f'{cid}.png'); plt.close(fig); table.to_csv(self.obs / f'{cid}.csv', index=False)

    def card(self, cid, title, n_samples, n_studies, description, definition, confounder, numbers, tag=None):
        c = dict(id=cid, title=title, figure=f'{cid}.png', csv=f'{cid}.csv', description=description, definition=definition, confounder=confounder,
                 n_samples=int(n_samples), n_studies=int(n_studies), numbers=numbers, tag=tag)
        self.cards.append(c); log(f'card {cid}: n={n_samples} studies={n_studies}'); return c

    def run(self, only=None):
        fns = [self.a_age_trajectories, self.b_pb_ratio_hdi, self.b_vanish_blossum_volcano, self.c_lifestyle_industrialization,
               self.c_athletes_vs_healthy_adults, self.c_collection_year_drift, self.d_disease_within_study, self.e_richness_shannon,
               self.f_map_prevotella, self.f_map_bifidobacterium, self.g_depth_vs_richness, self.h_variance_explained, self.i_dominant_genus_by_age,
               self.i_delivery_mode_infants, self.i_akkermansia, self.i_feeding_mode_infants, self.i_species_prevalence_by_age, self.i_recruitment_site]
        for fn in fns:
            if only and fn.__name__ not in only:
                continue
            t0 = time.time()
            try:
                r = fn()
                if r is None:
                    self.skipped.append(dict(card=fn.__name__, reason='insufficient data')); log(f'skip {fn.__name__}: insufficient data')
            except Exception as e:  # a card must never break the payload
                self.skipped.append(dict(card=fn.__name__, reason=f'{type(e).__name__}: {e}')); log(f'skip {fn.__name__}: {type(e).__name__}: {e}')
                plt.close('all')
            log(f'{fn.__name__} {time.time() - t0:.1f}s')
        return self.cards

    # --- a -----------------------------------------------------------------------------------------------------------
    def a_age_trajectories(self):
        S, D = self.S, self.D
        genera = ['Bifidobacterium', 'Bacteroides', 'Prevotella', 'Escherichia', 'Faecalibacterium']
        m = S.age_category.isin(AGE_ORDER[:-1]).values
        if m.sum() < 50:
            return None
        cats = [a for a in AGE_ORDER[:-1] if (S.age_category.values[m] == a).sum() > 0]
        rows, pers, cons = [], [], {}
        inf, adu = (S.age_category.eq('infant') & m).values, (S.age_category.eq('adult') & m).values
        for g in genera:
            x = D.genus(g)
            for a in cats:
                ma = (S.age_category.values == a) & m
                st = pd.DataFrame(dict(study=S.study_accession.values[ma], x=x[ma])).groupby('study').x.agg(['mean', 'size'])
                st = st[st['size'] >= MIN_STUDY_N]
                rows.append(dict(genus=g, age_category=a, n_samples=int(ma.sum()), n_studies=int(S.study_accession.values[ma].astype(str).size and len(set(S.study_accession.values[ma]))),
                                 mean_relabund=float(x[ma].mean()), prevalence=float((x[ma] >= atlas.PRESENCE).mean()),
                                 median_of_study_means=float(st['mean'].median()) if len(st) else None, n_studies_ge20=int(len(st))))
            c, per = within_study(S, inf, adu, D.lg(x), label_a='infant', label_b='adult'); cons[g] = c; pers.append(per)
        tab = pd.DataFrame(rows)
        fig, axes = plt.subplots(1, 2, figsize=(8.6, 3.6), gridspec_kw=dict(width_ratios=[1.5, 1]))
        ax = axes[0]
        for g in genera:
            t = tab[tab.genus == g]; col = GENUS_COL[g]
            ax.plot(range(len(cats)), t.mean_relabund.values, '-o', color=col, ms=3.5, lw=1.4, label=g)
            ax.scatter(range(len(cats)), t.median_of_study_means.values, marker='s', s=22, color='black', zorder=4)
        ax.set_yscale('symlog', linthresh=1e-3); ax.set_ylim(0, 1); ax.set_yticks([0, 1e-3, 1e-2, 1e-1, 1]); ax.set_yticklabels(['0', '0.1 %', '1 %', '10 %', '100 %'])
        ax.set_xticks(range(len(cats))); ax.set_xticklabels(cats); ax.set_ylabel('mean relative abundance'); ax.set_xlim(-0.3, len(cats) - 0.7); ax.legend(loc='lower right', prop={'style': 'italic'}, ncol=2)
        ax.set_title('Bifidobacterium and Escherichia fall, Faecalibacterium and Prevotella rise across life stages')
        ax.text(0.0, -0.2, 'lines: pooled mean; black squares: median of per-study means (studies ≥ 20 samples)', transform=ax.transAxes, fontsize=6, color='#555555')
        effect_dot_plot(axes[1], pers, genera, 'Within-study change infant → adult', 'Δ mean log10(relabund + 1e-5), adult − infant')
        fig.tight_layout()
        self.save(fig, 'a_age_trajectories', tab)
        n_st = S.study_accession[m].nunique()
        sent = '; '.join(consistency_sentence(g, cons[g]) for g in genera)
        bact = cons['Bacteroides']
        desc = ('Bifidobacterium dominates neonates and infants and declines through childhood to adulthood; Faecalibacterium and Prevotella rise from '
                'near-absence in neonates to several percent in children and adults; Escherichia is a neonatal genus that becomes rare later. ')
        if bact['n_studies'] and abs(bact['median_effect']) < 0.2 and bact['pooled_effect'] and abs(bact['pooled_effect']) > 0.5:
            desc += (f"Bacteroides is the exception: the pooled rise from infants to adults ({bact['pooled_effect']:+.2f} log10) is not reproduced within studies "
                     f"(median within-study change {bact['median_effect']:+.2f}), so it is a between-study, not a within-study, pattern. ")
        desc += 'Faint dots in the right panel are individual studies.'
        return self.card('a_age_trajectories', 'Genus trajectories across life stages', m.sum(), n_st, desc,
                         f'Samples with a known age category (n={fmt_n(m.sum())}, {fmt_n(n_st)} studies). Mean relative abundance per category (symlog axis); black squares = '
                         f'median of per-study means over studies with >= {MIN_STUDY_N} samples in that category. Per-study consistency, infant vs adult, studies with >= {MIN_STUDY_N} '
                         f'samples in both (effect = Δ mean log10(relabund + 1e-5)): {sent}.',
                         f"Age categories are study-level attributes for most samples (routes R3/R4), so category and study are strongly confounded; only "
                         f"{cons['Bifidobacterium']['n_studies']} studies contain both infants and adults with >= {MIN_STUDY_N} samples each. Repeated sampling of the same infants inflates n.",
                         dict(consistency=cons))

    # --- b -----------------------------------------------------------------------------------------------------------
    def _adult_hdi_mask(self):
        S = self.S
        return (S.age_category.isin(ADULT_LIKE) & S.hdi.notna()).values

    def _single_country_studies(self, m):
        """Studies with >= MIN_STUDY_N samples in mask m of which >= 80 % come from one country -> Series study -> country."""
        S = self.S[m]
        t = S.groupby(['study_accession', 'country']).size().reset_index(name='n')
        tot = t.groupby('study_accession').n.sum(); top = t.sort_values('n', ascending=False).drop_duplicates('study_accession').set_index('study_accession')
        ok = (tot >= MIN_STUDY_N) & (top.n.reindex(tot.index) / tot >= 0.8)
        return top.loc[ok[ok].index, 'country']

    def b_pb_ratio_hdi(self):
        S, D = self.S, self.D
        m = self._adult_hdi_mask()
        if m.sum() < 100:
            return None
        pb = np.log10(D.genus('Prevotella') + PSEUDO) - np.log10(D.genus('Bacteroides') + D.genus('Phocaeicola') + PSEUDO)
        df = pd.DataFrame(dict(study=S.study_accession.values[m], country=S.country.values[m], hdi=S.hdi.values[m], pb=pb[m]))
        cn = df.groupby('country').agg(n_samples=('pb', 'size'), n_studies=('study', 'nunique'), hdi=('hdi', 'first'), median_pb=('pb', 'median'), mean_pb=('pb', 'mean'))
        stm = df.groupby(['country', 'study']).pb.agg(['median', 'size']).reset_index()
        cn['median_of_study_medians'] = stm[stm['size'] >= MIN_STUDY_N].groupby('country')['median'].median()
        cn['n_studies_ge20'] = stm[stm['size'] >= MIN_STUDY_N].groupby('country').size()
        cn = cn[cn.n_samples >= MIN_COUNTRY_N].reset_index()
        cn['band'] = cn.hdi.map(atlas.hdi_band)
        if len(cn) < 5:
            return None
        rc = stats.spearmanr(cn.hdi, cn.median_pb)
        sc = self._single_country_studies(m)
        st = df[df.study.isin(sc.index)].groupby('study').pb.median().to_frame('median_pb'); st['country'] = sc.reindex(st.index).values; st['hdi'] = st.country.map(D.hdi)
        rs = stats.spearmanr(st.hdi, st.median_pb) if len(st) >= 5 else None
        fig, ax = plt.subplots(figsize=(7.2, 4))
        ax.scatter(st.hdi, st.median_pb, s=8, color='#bbbbbb', lw=0, label='one study (median PB)')
        for b in ['low', 'middle', 'high']:
            t = cn[cn.band == b]
            ax.scatter(t.hdi, t.median_pb, s=np.clip(t.n_samples / 40, 12, 160), color=BAND_COL[b], alpha=0.8, edgecolor='white', lw=0.5, label=f'country, HDI {b}')
            ax.scatter(t.hdi, t.median_of_study_medians, marker='s', s=14, color='black', zorder=4)
        ext = pd.concat([cn.nsmallest(3, 'median_pb'), cn.nlargest(3, 'median_pb')])
        for _, r in ext.iterrows():
            ax.annotate(r.country, (r.hdi, r.median_pb), xytext=(3, 3), textcoords='offset points', fontsize=6)
        ax.axhline(0, color='#888888', lw=0.6, ls=':')
        ax.set_xlabel('Human Development Index 2023 (UNDP HDR 2025)'); ax.set_ylabel('median log10 Prevotella / (Bacteroides + Phocaeicola)')
        ax.set_title(f'Prevotella gives way to Bacteroides as HDI rises (countries ρ = {rc.statistic:.2f}; studies ρ = {rs.statistic:.2f})' if rs else 'Prevotella/Bacteroides ratio vs HDI')
        ax.legend(loc='upper right', markerscale=0.8); ax.margins(0.05); ax.text(0.01, 0.02, 'black squares: median of per-study medians; circle area ∝ n samples', transform=ax.transAxes, fontsize=6, color='#555555')
        self.save(fig, 'b_pb_ratio_hdi', cn)
        n_st = int(df.study.nunique())
        return self.card('b_pb_ratio_hdi', 'Prevotella/Bacteroides ratio follows the industrialisation gradient', m.sum(), n_st,
                         'Countries with a low Human Development Index carry Prevotella-dominated gut communities, and the Prevotella/Bacteroides balance tips towards '
                         'Bacteroides (and its GTDB split-off Phocaeicola) as HDI increases. ' +
                         ('The relation holds when each study is reduced to a single median, so it is not an artefact of a handful of large cohorts.' if rs and rs.pvalue < 1e-3 and rs.statistic < 0 else
                          'At the study level the relation is weaker; treat the country-level correlation with care.'),
                         f'Samples aged child to elderly with a country that has an HDI value (n={fmt_n(m.sum())}, {fmt_n(n_st)} studies); PB = log10((Prevotella + 1e-5)/(Bacteroides + Phocaeicola + 1e-5)) per sample; '
                         f'countries with >= {MIN_COUNTRY_N} such samples plotted (n={len(cn)}); black squares = median over per-study medians (studies with >= {MIN_STUDY_N} samples in the country). '
                         f'Country-level Spearman ρ(HDI, median PB) = {rc.statistic:.2f}, p = {rc.pvalue:.1e}. Study-level check: {len(st)} studies with >= {MIN_STUDY_N} samples and >= 80 % from one country, '
                         + (f'Spearman ρ(HDI, study median PB) = {rs.statistic:.2f}, p = {rs.pvalue:.1e}.' if rs else 'too few for a correlation.') + f' HDI: {atlas.HDI_SOURCE}.',
                         'Country and study are almost perfectly confounded (most studies sample one country) and low-HDI countries are represented by few studies, often with a specific '
                         'recruitment focus (malnutrition, infection). Infants and neonates are excluded because their Bifidobacterium-dominated communities would swamp the contrast.',
                         dict(spearman_country=float(rc.statistic), p_country=float(rc.pvalue), spearman_study=float(rs.statistic) if rs else None, p_study=float(rs.pvalue) if rs else None,
                              n_countries=int(len(cn)), n_studies_single_country=int(len(st))))

    def b_vanish_blossum_volcano(self):
        S, D = self.S, self.D
        m = self._adult_hdi_mask()
        sc = self._single_country_studies(m)
        if len(sc) < 6:
            return None
        band = sc.map(D.hdi).map(atlas.hdi_band)
        low, high = band.index[band == 'low'], band.index[band == 'high']
        if len(low) < 3 or len(high) < 3:
            return None
        gi = np.where(np.asarray((D.M[m] >= atlas.PRESENCE).mean(axis=0)).ravel() >= 0.05)[0]
        if len(gi) == 0:
            return None
        X = np.log10(D.M[m][:, gi].toarray().astype(np.float64) + PSEUDO)
        st = pd.DataFrame(X, columns=D.genera[gi]).groupby(S.study_accession.values[m]).mean()
        A, B = st.reindex(low).dropna(), st.reindex(high).dropna()
        rows = []
        for g in st.columns:
            u = stats.mannwhitneyu(A[g], B[g], alternative='two-sided')
            rows.append(dict(genus=g, n_low=len(A), n_high=len(B), median_low=float(A[g].median()), median_high=float(B[g].median()), effect=float(A[g].median() - B[g].median()),
                             p=float(u.pvalue), frac_low_studies_higher=float(u.statistic / (len(A) * len(B))), prevalence=float(D.prev[D.genera.get_loc(g)])))
        tab = pd.DataFrame(rows); tab['q'] = bh(tab.p); tab['class'] = np.where((tab.q < 0.05) & (tab.effect > 0), 'VANISH-like (low-HDI higher)', np.where((tab.q < 0.05) & (tab.effect < 0), 'BloSSUM-like (high-HDI higher)', 'n.s.'))
        tab = tab.sort_values('p')
        fig, ax = plt.subplots(figsize=(7.2, 4.4))
        col = tab['class'].map({'VANISH-like (low-HDI higher)': BAND_COL['low'], 'BloSSUM-like (high-HDI higher)': BAND_COL['high'], 'n.s.': '#bbbbbb'})
        ax.scatter(tab.effect, -np.log10(tab.q.clip(1e-300)), s=np.clip(tab.prevalence * 60, 6, 60), color=col, alpha=0.8, lw=0)
        lab = pd.concat([tab[tab['class'].str.startswith('VANISH')].nlargest(5, 'effect'), tab[tab['class'].str.startswith('BloSSUM')].nsmallest(5, 'effect')])
        for k, (_, r) in enumerate(lab.iterrows()):
            ax.annotate(pretty(r.genus), (r.effect, -np.log10(max(r.q, 1e-300))), xytext=(4, 3 if k % 2 == 0 else -8), textcoords='offset points', fontsize=6, style='italic')
        ax.axhline(-np.log10(0.05), color='#888888', lw=0.6, ls=':'); ax.axvline(0, color='#888888', lw=0.6)
        ax.set_xlabel('difference of band medians, mean log10(relabund + 1e-5): low-HDI studies − high-HDI studies'); ax.set_ylabel('−log10 q (BH)')
        nv, nb = int((tab['class'].str.startswith('VANISH')).sum()), int((tab['class'].str.startswith('BloSSUM')).sum())
        ax.set_title(f'{nv} genera higher in low-HDI studies (red), {nb} higher in high-HDI studies (blue); unit = study'); ax.margins(0.06)
        ax.text(0.01, 0.02, f'{len(A)} low-HDI vs {len(B)} high-HDI studies; dot area ∝ prevalence; labels = 5 largest effects per side', transform=ax.transAxes, fontsize=6, color='#555555', va='bottom')
        self.save(fig, 'b_vanish_blossum_volcano', tab)
        n_samp = int(S.study_accession.isin(sc.index).values[m].sum() if False else (S.study_accession.isin(sc.index) & m).sum())
        top_v = [pretty(g) for g in tab[tab['class'].str.startswith('VANISH')].genus.head(6)]; top_b = [pretty(g) for g in tab[tab['class'].str.startswith('BloSSUM')].genus.head(7)]
        prev_row = tab[tab.genus == 'g__Prevotella']
        return self.card('b_vanish_blossum_volcano', 'Genera that vanish or bloom with industrialisation (study-level volcano)', n_samp, len(sc),
                         f"Treating each study as one observation, genera that are more abundant in studies from low-HDI countries (VANISH-like: {', '.join(top_v)}) separate from genera "
                         f"that are more abundant in high-HDI studies (BloSSUM-like: {', '.join(top_b)}). Names follow GTDB R232" +
                         (f"; a random low-HDI study exceeds a random high-HDI study for Prevotella {prev_row.frac_low_studies_higher.iloc[0] * 100:.0f} % of the time." if len(prev_row) else '.'),
                         f'Unit = study: {len(sc)} studies with >= {MIN_STUDY_N} child-to-elderly samples of which >= 80 % come from one country; low band HDI < 0.70 (n={len(A)} studies), high band HDI >= 0.80 (n={len(B)}); '
                         f'middle-band studies excluded. Per study: mean of log10(relabund + 1e-5) over its samples (absent = 1e-5). Genera with prevalence >= 5 % in the child-to-elderly set (n={len(tab)}). '
                         f'Effect = difference of band medians; p from two-sided Mann–Whitney over studies; q = Benjamini–Hochberg (q < 0.05 coloured). Column frac_low_studies_higher gives the probability that a '
                         f'random low-HDI study exceeds a random high-HDI study. HDI: {atlas.HDI_SOURCE}.',
                         f'Only {len(A)} low-HDI studies enter, several from the same research groups and with specific cohorts (children with malnutrition, rural cohorts recruited for that reason); '
                         'study-level testing removes pseudo-replication but not recruitment bias.',
                         dict(n_genera=int(len(tab)), n_vanish=nv, n_blossum=nb, n_low=int(len(A)), n_high=int(len(B))))

    # --- c (new in R2026.13) ------------------------------------------------------------------------------------------
    def _two_group_genus_card(self, cid, title, mask_a, mask_b, label_a, label_b, n_top=12, min_within=MIN_STUDY_N, extra_def='', confounder='', tag=None, intro=''):
        """Generic lifestyle contrast: study-level test over studies with >= MIN_STUDY_N samples in a group; within-study check where a study has both groups."""
        S, D = self.S, self.D
        if mask_a.sum() < 20 or mask_b.sum() < 20:
            return None
        m = mask_a | mask_b
        gi = np.where(np.asarray((D.M[m] >= atlas.PRESENCE).mean(axis=0)).ravel() >= 0.10)[0]
        if len(gi) == 0:
            return None
        X = np.log10(D.M[m][:, gi].toarray().astype(np.float64) + PSEUDO)
        grp = np.where(mask_a[m], 'a', 'b'); studies = S.study_accession.values[m]
        st = pd.DataFrame(X, columns=D.genera[gi]).assign(_study=studies, _grp=grp).groupby(['_study', '_grp']).agg(['mean', 'size'])
        rows = []
        both = None
        for g in D.genera[gi]:
            t = st[g].unstack()
            a = t['mean']['a'][t['size']['a'] >= MIN_STUDY_N].dropna() if 'a' in t['mean'] else pd.Series(dtype=float)
            b = t['mean']['b'][t['size']['b'] >= MIN_STUDY_N].dropna() if 'b' in t['mean'] else pd.Series(dtype=float)
            r = dict(genus=g, prevalence=float((D.M[m][:, D.genera.get_loc(g)] >= atlas.PRESENCE).mean()),
                     pooled_effect=float(X[grp == 'b', list(D.genera[gi]).index(g)].mean() - X[grp == 'a', list(D.genera[gi]).index(g)].mean()),
                     n_studies_a=int(len(a)), n_studies_b=int(len(b)), median_a=float(a.median()) if len(a) else None, median_b=float(b.median()) if len(b) else None)
            if len(a) >= 2 and len(b) >= 2:
                u = stats.mannwhitneyu(a, b, alternative='two-sided'); r.update(study_effect=float(b.median() - a.median()), p_study=float(u.pvalue), frac_b_studies_higher=float(1 - u.statistic / (len(a) * len(b))))
            else:
                r.update(study_effect=None, p_study=None, frac_b_studies_higher=None)
            if 'a' in t['size'] and 'b' in t['size']:
                ok = (t['size']['a'] >= min_within) & (t['size']['b'] >= min_within)
                w = (t['mean']['b'] - t['mean']['a'])[ok]
                r.update(n_studies_within=int(len(w)), within_median_effect=float(w.median()) if len(w) else None, within_frac_agree=float((np.sign(w) == np.sign(w.median())).mean()) if len(w) and w.median() != 0 else None)
                both = int(ok.sum())
            else:
                r.update(n_studies_within=0, within_median_effect=None, within_frac_agree=None)
            rows.append(r)
        tab = pd.DataFrame(rows)
        mode = 'study' if tab.p_study.notna().any() else 'pooled'
        if mode == 'study':
            tab['q_study'] = bh(tab.p_study.fillna(1)); tab['rank_effect'] = tab.study_effect
        else:
            tab['q_study'] = np.nan; tab['rank_effect'] = tab.pooled_effect
        tab = tab.sort_values('rank_effect'); top = pd.concat([tab.head(n_top // 2), tab.tail(n_top // 2)])
        fig, ax = plt.subplots(figsize=(7.4, 4.2))
        y = np.arange(len(top))
        ax.barh(y, top.rank_effect, color=np.where(top.rank_effect > 0, '#2166ac', '#b2182b'), alpha=0.85, height=0.62)
        if mode == 'study':
            for yi, (_, r) in zip(y, top.iterrows()):
                if r.q_study < 0.05:
                    ax.text(r.rank_effect + (0.03 if r.rank_effect > 0 else -0.03), yi, '*', ha='left' if r.rank_effect > 0 else 'right', va='center', fontsize=8)
            if both:
                ax.scatter(top.within_median_effect, y, marker='D', s=18, color='black', zorder=4, label=f'within-study median (n={both} studies with both groups)')
                ax.legend(loc='upper left')
        ax.set_yticks(y); ax.set_yticklabels([pretty(g) for g in top.genus], style='italic'); ax.axvline(0, color='#888888', lw=0.7)
        ax.set_xlabel(f'{"difference of study-group medians" if mode == "study" else "pooled difference"}, mean log10(relabund + 1e-5): {label_b} − {label_a}')
        na, nb = int(mask_a.sum()), int(mask_b.sum()); sa, sb = int(S.study_accession[mask_a].nunique()), int(S.study_accession[mask_b].nunique())
        ax.set_title(f'{title}: {n_top // 2} genera each way ({label_a}: {fmt_n(na)} samples / {sa} studies; {label_b}: {fmt_n(nb)} / {sb})'); ax.margins(0.08)
        if mode == 'study':
            ax.text(0.99, 0.02, '* q < 0.05 (BH, Mann–Whitney over studies)', transform=ax.transAxes, fontsize=6, color='#555555', ha='right', va='bottom')
        fig.tight_layout(); self.save(fig, cid, tab.drop(columns=['rank_effect']))
        hi = [pretty(g) for g in tab.tail(4).genus[::-1]]; lo = [pretty(g) for g in tab.head(4).genus]
        nsig = int((tab.q_study < 0.05).sum()) if mode == 'study' else None
        desc = (intro + f' Higher in {label_b}: {", ".join(hi)}; higher in {label_a}: {", ".join(lo)} (ranked by {"the study-level" if mode == "study" else "the pooled"} effect). ' +
                (f'{nsig} of {len(tab)} genera reach q < 0.05 at the study level. ' if nsig is not None else 'Too few studies per group for a study-level test; effects are pooled and unadjusted. ') +
                (f'{both} stud{"y" if both == 1 else "ies"} contain{"s" if both == 1 else ""} both groups with >= {min_within} samples; the black diamonds show the within-study median effect for the plotted genera.' if both else
                 f'No study contains both groups with >= {min_within} samples each, so the contrast is entirely between studies (caveat: study, country and protocol differ between the groups).'))
        return self.card(cid, title, na + nb, int(S.study_accession[m].nunique()), desc,
                         f'{label_a}: {fmt_n(na)} samples, {sa} studies; {label_b}: {fmt_n(nb)} samples, {sb} studies. {extra_def} Genera with prevalence >= 10 % in the union set (n={len(tab)}). '
                         f'Study level: mean log10(relabund + 1e-5) per study-group with >= {MIN_STUDY_N} samples; effect = difference of group medians over studies; p two-sided Mann–Whitney, q Benjamini–Hochberg. '
                         f'Within-study: studies with >= {min_within} samples in both groups, effect = mean({label_b}) − mean({label_a}), median across studies and share agreeing with the sign of the median. '
                         f'Pooled effect (unadjusted) in the CSV.', confounder, dict(mode=mode, n_genera=int(len(tab)), n_sig=nsig, n_studies_both=both,
                                                                                   top_higher_b=hi, top_higher_a=lo), tag=tag)

    def c_lifestyle_industrialization(self):
        S = self.S
        if S.lifestyle.isna().all():
            return None
        a = S.lifestyle.isin(NON_INDUSTRIAL).values; b = S.lifestyle.eq('urban_industrialized').values
        return self._two_group_genus_card('c_lifestyle_industrialization', 'Lifestyle: non-industrialised vs urban-industrialised', a, b, 'non-industrialised', 'urban-industrialised', min_within=15,
                                          extra_def='non-industrialised = lifestyle in {hunter_gatherer, pastoralist, traditional_agriculturalist, rural_non_industrialized}; urban-industrialised = lifestyle urban_industrialized (curated field, all ages).',
                                          confounder='lifestyle is recorded for a small minority of samples (mostly R1 BioSample attributes of studies designed around this contrast), so the groups come from few studies, '
                                                     'different countries, ages and sequencing depths; the within-study check rests on the one or two studies that sampled both groups.',
                                          intro='Samples whose curated lifestyle marks a non-industrialised way of life are compared with samples labelled urban-industrialised.')

    def c_athletes_vs_healthy_adults(self):
        S = self.S
        if S.lifestyle.isna().all():
            return None
        b = S.lifestyle.eq('athlete').values
        a = (S.health_condition.eq('healthy_control') & S.age_category.eq('adult') & ~S.lifestyle.eq('athlete').fillna(False)).values
        return self._two_group_genus_card('c_athletes_vs_healthy_adults', 'Lifestyle: athletes vs healthy adults', a, b, 'healthy adults', 'athletes', min_within=MIN_STUDY_N,
                                          extra_def='athletes = lifestyle athlete (any age; all but a handful are adults); healthy adults = health_condition healthy_control, age_category adult, lifestyle not athlete.',
                                          confounder='no study in the catalog contains both athletes and non-athlete healthy adults with >= 20 samples each, so this is an across-study contrast: athlete cohorts differ '
                                                     'in country, diet, sequencing depth and recruitment from the healthy-control arms of case-control studies; several athlete studies also carry a health_condition '
                                                     'label of intervention_cohort or none.',
                                          intro='Athlete cohorts (curated lifestyle = athlete) are compared with the healthy adult controls of other studies.')

    def c_collection_year_drift(self):
        S, D = self.S, self.D
        cy = pd.to_numeric(S.collection_year, errors='coerce')
        m = (cy >= 2005) & (cy <= 2025)
        if m.sum() < 100:
            return None
        prev = D.genus('Prevotella'); rich = S.richness_genus.values.astype(float)
        df = pd.DataFrame(dict(study=S.study_accession.values[m], year=cy[m].astype(int).values, rich=rich[m], prev=(prev[m] >= atlas.PRESENCE).astype(float), depth=S.root_coverage_sum.values[m].astype(float),
                               age=S.age_category.values[m]))
        yrs = df.groupby(['study', 'year']).size().reset_index(name='n'); yrs = yrs[yrs.n >= MIN_STUDY_N]
        long_st = yrs.groupby('study').size(); long_st = long_st[long_st >= 3].index
        if len(long_st) < 3:
            return None
        rows = []
        for s, d in df[df.study.isin(long_st)].groupby('study'):
            yy = yrs[yrs.study == s].year.values; d = d[d.year.isin(yy)]
            per = d.groupby('year').agg(n=('rich', 'size'), rich=('rich', 'median'), prev=('prev', 'mean'), depth=('depth', 'median'))
            r_r = stats.spearmanr(per.index, per.rich).statistic; r_p = stats.spearmanr(per.index, per.prev).statistic; r_d = stats.spearmanr(per.index, per.depth).statistic
            rows.append(dict(study=s, n_samples=int(len(d)), n_years=int(len(per)), year_min=int(per.index.min()), year_max=int(per.index.max()), rho_year_richness=float(r_r), rho_year_prevotella_prevalence=float(r_p),
                             rho_year_depth=float(r_d), richness_first=float(per.rich.iloc[0]), richness_last=float(per.rich.iloc[-1]), prevotella_first=float(per.prev.iloc[0]), prevotella_last=float(per.prev.iloc[-1]),
                             main_age=str(d.age.value_counts().index[0])))
        tab = pd.DataFrame(rows)
        # across-study, per calendar year: median of per-study medians (studies >= MIN_STUDY_N samples in the year)
        py = df.groupby(['study', 'year']).agg(n=('rich', 'size'), rich=('rich', 'median'), prev=('prev', 'mean')).reset_index(); py = py[py.n >= MIN_STUDY_N]
        year_tab = py.groupby('year').agg(n_studies=('study', 'nunique'), median_richness_of_study_medians=('rich', 'median'), median_prevotella_prevalence_of_studies=('prev', 'median'))
        year_tab = year_tab[year_tab.n_studies >= 3]
        fig, axes = plt.subplots(1, 3, figsize=(9.6, 3.4))
        ax = axes[0]
        for _, r in tab.iterrows():
            ax.plot([r.year_min, r.year_max], [r.richness_first, r.richness_last], color='#1f77b4' if r.rho_year_richness > 0 else '#d62728', alpha=0.5, lw=0.9)
        ax.set_xlabel('collection year'); ax.set_ylabel('median genus richness (first → last year)'); ax.set_title(f'{len(tab)} long-running studies: first vs last year')
        ax.text(0.02, 0.97, 'blue: richness rises within the study; red: falls', transform=ax.transAxes, fontsize=6, va='top', color='#555555')
        ax = axes[1]
        for i, (col, lab) in enumerate([('rho_year_richness', 'richness'), ('rho_year_prevotella_prevalence', 'Prevotella prevalence'), ('rho_year_depth', 'depth (root coverage)')]):
            ax.scatter(tab[col], np.full(len(tab), i) + np.random.default_rng(i).uniform(-0.2, 0.2, len(tab)), s=10, alpha=0.5, color='#555555', lw=0)
            ax.scatter([tab[col].median()], [i], marker='s', s=34, color='black', zorder=3)
        ax.set_yticks(range(3)); ax.set_yticklabels(['richness', 'Prevotella prevalence', 'depth (root coverage)']); ax.invert_yaxis(); ax.axvline(0, color='#888888', lw=0.7)
        ax.set_xlabel('within-study Spearman ρ (year, yearly median)'); ax.set_title('Within-study drift: dots = studies, square = median'); ax.set_xlim(-1.1, 1.1)
        ax = axes[2]
        ax.plot(year_tab.index, year_tab.median_richness_of_study_medians, '-o', ms=3, color='#1f77b4'); ax.set_ylabel('median of study-median richness', color='#1f77b4')
        ax2 = ax.twinx(); ax2.plot(year_tab.index, year_tab.median_prevotella_prevalence_of_studies, '-s', ms=3, color='#d6604d'); ax2.set_ylabel('median study Prevotella prevalence', color='#d6604d'); ax2.spines['top'].set_visible(False)
        ax.set_xlabel('collection year'); ax.set_title('Across studies, per calendar year (≥ 3 studies)')
        fig.tight_layout(); self.save(fig, 'c_collection_year_drift', tab)
        year_tab.reset_index().to_csv(self.obs / 'c_collection_year_drift_by_year.csv', index=False)
        n_samp = int(tab.n_samples.sum()); med_r, med_p, med_d = tab.rho_year_richness.median(), tab.rho_year_prevotella_prevalence.median(), tab.rho_year_depth.median()
        fpos_r = float((tab.rho_year_richness > 0).mean()); fpos_p = float((tab.rho_year_prevotella_prevalence > 0).mean())
        desc = (f'Within the {len(tab)} studies that collected samples over three or more calendar years, richness rises with collection year in {fpos_r * 100:.0f} % of studies '
                f'(median within-study Spearman ρ = {med_r:+.2f}) and Prevotella prevalence in {fpos_p * 100:.0f} % (median ρ = {med_p:+.2f}); the depth proxy drifts with a median ρ of {med_d:+.2f}, '
                'so part of any richness drift is technical. ' +
                ('Neither direction is consistent enough to call a systematic temporal drift of composition within studies.' if abs(med_r) < 0.3 and abs(med_p) < 0.3 else 'The drift is consistent enough to deserve a depth-matched follow-up.') +
                f' Across studies, the right panel shows the median of per-study medians per calendar year (all samples with a collection year: {fmt_n(m.sum())}, {fmt_n(df.study.nunique())} studies; years with >= 3 studies).')
        return self.card('c_collection_year_drift', 'Does composition drift with collection year?', n_samp, len(tab), desc,
                         f'collection_year from the curated collection_date (2005–2025; n={fmt_n(m.sum())} samples with a year, {fmt_n(df.study.nunique())} studies). Long-running study = >= 3 calendar years with >= {MIN_STUDY_N} samples each '
                         f'(n={len(tab)} studies, {fmt_n(n_samp)} samples). Per study and year: median genus richness, Prevotella prevalence (relabund >= 1e-4), median root_coverage_sum; within-study Spearman ρ between year and the yearly value. '
                         f'Across studies: per calendar year the median over studies (>= {MIN_STUDY_N} samples in that year) of the study median richness / Prevotella prevalence. Companion table c_collection_year_drift_by_year.csv.',
                         'Collection year is confounded with sequencing platform, read length and depth (later samples are deeper), with the age of longitudinal cohorts (infant cohorts age with calendar time) and with '
                         'which studies were active in a given year; the across-study curve mixes different study populations per year and must not be read as a population trend.',
                         dict(n_long_studies=int(len(tab)), median_rho_richness=float(med_r), frac_positive_richness=fpos_r, median_rho_prevotella=float(med_p), frac_positive_prevotella=fpos_p, median_rho_depth=float(med_d)))

    # --- d -----------------------------------------------------------------------------------------------------------
    def d_disease_within_study(self):
        S, D = self.S, self.D
        if S.health_condition.isna().all():
            return None
        healthy = S.health_condition.eq('healthy_control').values
        contrasts = {'IBD': S.health_condition.isin(['crohns_disease', 'ulcerative_colitis', 'ibd_unspecified']).values, 'Colorectal cancer': S.health_condition.eq('colorectal_cancer').values,
                     'Type 2 diabetes': S.health_condition.eq('type2_diabetes').values}
        feats = {'Faecalibacterium': D.lg(D.genus('Faecalibacterium')), 'Escherichia': D.lg(D.genus('Escherichia')), 'Fusobacterium (all GTDB genera)': D.lg(D.genus_group('g__Fusobacterium')),
                 'Bacteroides': D.lg(D.genus('Bacteroides')), 'Akkermansia': D.lg(D.genus('Akkermansia')), 'genus richness': S.richness_genus.values.astype(float)}
        rows, pers, used = [], [], np.zeros(D.N, bool)
        for cname, dis in contrasts.items():
            for fname, v in feats.items():
                c, per = within_study(S, healthy, dis, v, label_a='healthy_control', label_b=cname)
                if c['n_studies']:
                    used |= (healthy | dis) & S.study_accession.isin(per.study).values
                    rows.append(dict(contrast=cname, feature=fname, **{k: c[k] for k in ['n_studies', 'median_effect', 'frac_agree', 'pooled_effect', 'n_a', 'n_b']})); pers.append((cname, fname, per))
        if not rows:
            return None
        tab = pd.DataFrame(rows)
        cs = [c for c in contrasts if (tab.contrast == c).any()]
        fig, axes = plt.subplots(2, len(cs), figsize=(3.2 * len(cs) + 1, 4.2), squeeze=False, gridspec_kw=dict(height_ratios=[5, 1.6]))
        fn = [f for f in feats if f != 'genus richness']
        for j, cname in enumerate(cs):
            get = lambda f: next((p for c_, f_, p in pers if c_ == cname and f_ == f), pd.DataFrame(columns=['effect']))
            nst = int(tab[tab.contrast == cname].n_studies.max())
            effect_dot_plot(axes[0, j], [get(f) for f in fn], fn, f'{cname} vs healthy ({nst} studies)', 'Δ disease − healthy, mean log10(relabund + 1e-5)')
            axes[0, j].set_yticklabels([f.split(' (')[0] for f in fn], style='italic')
            effect_dot_plot(axes[1, j], [get('genus richness')], ['genus richness'], '', 'Δ disease − healthy, genus richness (genera)')
            axes[1, j].set_yticklabels(['richness'], style='normal')
            if j:
                axes[0, j].set_yticklabels([]); axes[1, j].set_yticklabels([])
        fig.tight_layout(); self.save(fig, 'd_disease_within_study', tab)
        summ = '; '.join(f"{r.contrast}, {r.feature}: {r.n_studies} studies, median Δ={r.median_effect:+.2f}, {r.frac_agree * 100:.0f}% agree" for r in tab.itertuples())
        ibd = tab[tab.contrast == 'IBD'].set_index('feature'); crc = tab[tab.contrast == 'Colorectal cancer'].set_index('feature')
        desc = 'Comparing patients with healthy controls inside the same study removes the between-study differences that dominate pooled contrasts. '
        if 'Faecalibacterium' in ibd.index:
            desc += (f"Inflammatory bowel disease: Faecalibacterium median Δ {ibd.loc['Faecalibacterium', 'median_effect']:+.2f} ({ibd.loc['Faecalibacterium', 'frac_agree'] * 100:.0f} % of {int(ibd.loc['Faecalibacterium', 'n_studies'])} studies agree), "
                     f"genus richness Δ {ibd.loc['genus richness', 'median_effect']:+.1f} genera, Escherichia Δ {ibd.loc['Escherichia', 'median_effect']:+.2f} ({ibd.loc['Escherichia', 'frac_agree'] * 100:.0f} % agree). ")
        if 'Fusobacterium (all GTDB genera)' in crc.index:
            r = crc.loc['Fusobacterium (all GTDB genera)']; desc += f"Colorectal cancer: Fusobacterium higher in {r.frac_agree * 100:.0f} % of {int(r.n_studies)} studies (median Δ {r.median_effect:+.2f}). "
        t2 = tab[tab.contrast == 'Type 2 diabetes']
        if len(t2):
            desc += f"Type 2 diabetes rests on {int(t2.n_studies.max())} stud{'y' if t2.n_studies.max() == 1 else 'ies'} and its shifts are small (largest |median Δ| = {t2.median_effect.abs().max():.2f})."
        return self.card('d_disease_within_study', 'Disease signatures that reproduce within studies', used.sum(), S.study_accession[used].nunique(), desc,
                         f'Samples with health_condition = healthy_control vs (IBD = crohns_disease + ulcerative_colitis + ibd_unspecified | colorectal_cancer | type2_diabetes), all ages. Unit = study with >= {MIN_STUDY_N} samples '
                         f'in both groups. Per-study effect = mean(disease) − mean(healthy) of log10(relabund + 1e-5), or of genus richness. Summary = median of per-study effects; "agree" = fraction of studies whose effect '
                         f'has the sign of that median. {summ}.',
                         'health_condition is a study-level label for most samples (R3/R4), so within a study controls may come from a different recruitment stream; treatments (antibiotics, chemotherapy, metformin) '
                         'are not adjusted; several IBD cohorts sample the same patients longitudinally.', dict(summary=tab.to_dict('records')))

    # --- e -----------------------------------------------------------------------------------------------------------
    def e_richness_shannon(self):
        S = self.S
        rows = []
        facets = [('age_category', S.age_category.isin(AGE_ORDER[:-1]).values, S.age_category.values, AGE_ORDER[:-1])]
        mh = self._adult_hdi_mask()
        if mh.sum() >= 30:
            facets.append(('hdi_band', mh, S.band.values, ['low', 'middle', 'high']))
        if S.health_condition.notna().any():
            topc = S.health_condition.value_counts().index[:12].tolist(); facets.append(('health_condition', S.health_condition.isin(topc).values, S.health_condition.values, topc))
        have_sh = S.shannon_genus.notna().any()
        for facet, m, lab, order in facets:
            for metric, v in [('richness', S.richness_genus.values.astype(float))] + ([('shannon', S.shannon_genus.values.astype(float))] if have_sh else []):
                d = pd.DataFrame(dict(g=lab[m], v=v[m], study=S.study_accession.values[m])).dropna()
                for g in order:
                    x = d[d.g == g]
                    if not len(x):
                        continue
                    st = x.groupby('study').v.agg(['median', 'size']); st = st[st['size'] >= MIN_STUDY_N]
                    rows.append(dict(facet=facet, metric=metric, group=g, n_samples=int(len(x)), n_studies=int(x.study.nunique()), median=float(x.v.median()), q1=float(x.v.quantile(0.25)), q3=float(x.v.quantile(0.75)),
                                     median_of_study_medians=float(st['median'].median()) if len(st) else None, n_studies_ge20=int(len(st))))
        tab = pd.DataFrame(rows)
        if tab.empty:
            return None
        rt = tab[tab.metric == 'richness']; fac = [f for f in ['age_category', 'hdi_band', 'health_condition'] if (rt.facet == f).any()]
        fig, axes = plt.subplots(1, len(fac), figsize=(2.2 + 2.6 * len(fac) + (2 if 'health_condition' in fac else 0), 3.8), squeeze=False, gridspec_kw=dict(width_ratios=[1 if f != 'health_condition' else 2.2 for f in fac])); axes = axes[0]
        for ax, f in zip(axes, fac):
            t = rt[rt.facet == f]; x = np.arange(len(t))
            cols = [BAND_COL.get(g, '#4393c3') if f == 'hdi_band' else '#4393c3' for g in t.group]
            ax.bar(x, t['median'], color=cols, alpha=0.75, width=0.65); ax.errorbar(x, t['median'], yerr=[t['median'] - t.q1, t.q3 - t['median']], fmt='none', ecolor='#333333', lw=0.8, capsize=2)
            ax.scatter(x, t.median_of_study_medians, marker='s', s=22, color='black', zorder=4)
            ax.set_xticks(x); ax.set_xticklabels([g.replace('_', ' ') for g in t.group], rotation=45 if f == 'health_condition' else 0, ha='right' if f == 'health_condition' else 'center')
            ax.set_title({'age_category': 'Life stage', 'hdi_band': 'HDI band (child–elderly)', 'health_condition': 'Health condition (12 most common)'}[f]); ax.margins(0.04)
            for xi, r in zip(x, t.itertuples()):
                ax.text(xi, 0.5, f'{r.n_studies}', ha='center', fontsize=5.5, color='white' if r.median > 4 else 'black')
        axes[0].set_ylabel('genus richness (median, IQR); black squares = median of study medians'); fig.tight_layout()
        self.save(fig, 'e_richness_shannon', tab)
        ra = rt[rt.facet == 'age_category'].set_index('group'); desc = ''
        if {'neonate', 'adult'} <= set(ra.index):
            desc += f"Genus richness rises from a median of {ra.loc['neonate', 'median']:.0f} genera in neonates to {ra.loc['adult', 'median']:.0f} in adults" + (f" and {ra.loc['elderly', 'median']:.0f} in the elderly. " if 'elderly' in ra.index else '. ')
        rb = rt[rt.facet == 'hdi_band'].set_index('group')
        if {'low', 'high'} <= set(rb.index):
            lo, hi = rb.loc['low'], rb.loc['high']
            desc += (f"Children and adults from low-HDI countries {'do not show more' if lo['median'] <= hi['median'] else 'show more'} genera than those from high-HDI countries in this collection "
                     f"(median {lo['median']:.0f} vs {hi['median']:.0f}" + (f"; per-study medians {lo.median_of_study_medians:.0f} vs {hi.median_of_study_medians:.0f}" if pd.notna(lo.median_of_study_medians) and pd.notna(hi.median_of_study_medians) else '') + "), a result that most likely reflects the shallower sequencing of many low-HDI cohorts "
                     f"rather than biology (see the depth card). ")
        rh = rt[rt.facet == 'health_condition'].sort_values('median')
        if len(rh):
            desc += f"Across health conditions the lowest median richness is in {rh.group.iloc[0].replace('_', ' ')} ({rh['median'].iloc[0]:.0f}) and the highest in {rh.group.iloc[-1].replace('_', ' ')} ({rh['median'].iloc[-1]:.0f}). Numbers inside the bars = n studies."
        return self.card('e_richness_shannon', 'Richness and Shannon diversity by life stage, HDI band and health condition', self.D.N, S.study_accession.nunique(), desc,
                         f'Richness = number of genera with relabund > 0 in the Sandpiper profile; Shannon on genus proportions (from the per-sample summary table; CSV only). Panels: life stage (n={fmt_n(ra.n_samples.sum()) if len(ra) else 0}); '
                         f'HDI band for child-to-elderly samples with a country HDI (n={fmt_n(rb.n_samples.sum()) if len(rb) else 0}; {atlas.HDI_SOURCE}); health condition for the 12 most common curated conditions '
                         f'(n={fmt_n(rh.n_samples.sum()) if len(rh) else 0}). Bars = pooled median with IQR; black squares = median of per-study medians (studies >= {MIN_STUDY_N} samples). Per-group n, n studies and per-study medians are in the CSV.',
                         'Richness depends strongly on sequencing depth (see the depth card) and depth differs systematically between studies, countries and eras; age, country and condition are all study-level '
                         'attributes for most samples. No depth rarefaction was applied, so the HDI-band panel in particular should not be read as a biological difference.', dict(groups=tab.to_dict('records')))

    # --- f -----------------------------------------------------------------------------------------------------------
    def _country_prevalence(self, x, m):
        S = self.S
        d = pd.DataFrame(dict(country=S.country.values[m], study=S.study_accession.values[m], p=(x[m] >= atlas.PRESENCE).astype(float), ra=x[m])).dropna(subset=['country'])
        cn = d.groupby('country').agg(n_samples=('p', 'size'), n_studies=('study', 'nunique'), prevalence=('p', 'mean'), mean_relabund=('ra', 'mean'))
        st = d.groupby(['country', 'study']).p.agg(['mean', 'size']).reset_index(); st = st[st['size'] >= MIN_STUDY_N]
        cn['median_of_study_prevalences'] = st.groupby('country')['mean'].median(); cn['n_studies_ge20'] = st.groupby('country').size().reindex(cn.index).fillna(0).astype(int)
        cn['hdi'] = cn.index.map(self.D.hdi); cn['band'] = cn.hdi.map(atlas.hdi_band)
        return cn[cn.n_samples >= MIN_COUNTRY_N].reset_index().sort_values('prevalence', ascending=False)

    def _prevalence_dot_chart(self, cn, genus, cid, title):
        fig, ax = plt.subplots(figsize=(6.4, max(2.4, 0.135 * len(cn) + 1.0)))
        y = np.arange(len(cn))
        ax.hlines(y, 0, cn.prevalence, color='#dddddd', lw=1)
        ax.scatter(cn.prevalence, y, s=np.clip(cn.n_samples / 60, 8, 80), color=[BAND_COL.get(b if isinstance(b, str) else None) for b in cn.band], zorder=3, edgecolor='white', lw=0.4)
        ax.scatter(cn.median_of_study_prevalences, y, marker='|', s=40, color='black', zorder=4)
        ax.set_yticks(y); ax.set_yticklabels(cn.country, fontsize=5.5); ax.invert_yaxis(); ax.set_xlim(-0.02, 1.05); ax.set_xlabel(f'share of child-to-elderly samples with {genus} ≥ 0.01 %')
        ax.set_title(title); ax.margins(y=0.01)
        for b, lab in [('low', 'HDI low (< 0.70)'), ('middle', 'HDI middle'), ('high', 'HDI high (≥ 0.80)'), (None, 'no UNDP value')]:
            ax.scatter([], [], color=BAND_COL[b], label=lab, s=20)
        ax.scatter([], [], marker='|', color='black', label='median of per-study prevalences', s=40); ax.legend(loc='lower right', fontsize=6)
        fig.tight_layout(); self.save(fig, cid, cn)

    def f_map_prevotella(self):
        S, D = self.S, self.D
        m = S.age_category.isin(ADULT_LIKE).values
        cn = self._country_prevalence(D.genus('Prevotella'), m)
        if len(cn) < 3:
            return None
        self._prevalence_dot_chart(cn, 'Prevotella', 'f_map_prevotella', f'Prevotella prevalence by country ({len(cn)} countries ≥ {MIN_COUNTRY_N} child-to-elderly samples)')
        hi, lo = cn.head(3), cn.tail(3)[::-1]
        return self.card('f_map_prevotella', 'World map: Prevotella prevalence', m.sum() - S.country[m].isna().sum(), S.study_accession[m & S.country.notna().values].nunique(),
                         'Prevotella is carried by nearly every child and adult sampled in low-HDI African, South-Asian and Latin-American countries but by well under half in most of Europe, North America and East Asia '
                         '(interactive map: Atlas → taxon map). ' + 'Highest: ' + ', '.join(f'{r.country} {r.prevalence * 100:.0f}%' for r in hi.itertuples()) + '; lowest: ' + ', '.join(f'{r.country} {r.prevalence * 100:.0f}%' for r in lo.itertuples()) + '.',
                         f'Prevalence = share of child-to-elderly samples (age_category child, adolescent, adult or elderly) with genus relative abundance >= 1e-4 (0.01 %); countries with >= {MIN_COUNTRY_N} such samples '
                         f'(n={len(cn)}). Dot colour = HDI band ({atlas.HDI_SOURCE}); black tick = median of per-study prevalences (studies with >= {MIN_STUDY_N} samples in the country). The CSV adds mean relative abundance and n studies.',
                         'Country-level prevalence is a mixture of a few studies per country, each with its own recruitment (many low-HDI cohorts are disease or malnutrition studies); the 1e-4 threshold detects low-level '
                         f'presence and is sensitive to sequencing depth; country is missing for {S.country.isna().mean() * 100:.0f} % of samples.',
                         dict(highest=[[r.country, float(r.prevalence)] for r in hi.itertuples()], lowest=[[r.country, float(r.prevalence)] for r in lo.itertuples()], n_countries=int(len(cn))))

    def f_map_bifidobacterium(self):
        S, D = self.S, self.D
        x = D.genus('Bifidobacterium'); m = S.age_category.isin(ADULT_LIKE).values
        cn = self._country_prevalence(x, m)
        if len(cn) < 3:
            return None
        mi = S.age_category.isin(EARLY).values
        inf = self._country_prevalence(x, mi) if mi.sum() else pd.DataFrame(columns=['country', 'prevalence', 'n_samples'])
        cn = cn.merge(inf[['country', 'prevalence', 'n_samples']].rename(columns={'prevalence': 'prevalence_infant', 'n_samples': 'n_samples_infant'}), on='country', how='left')
        self._prevalence_dot_chart(cn, 'Bifidobacterium', 'f_map_bifidobacterium', f'Bifidobacterium prevalence by country ({len(cn)} countries ≥ {MIN_COUNTRY_N} child-to-elderly samples)')
        hi, lo = cn.head(3), cn.tail(3)[::-1]
        early_prev = float((x[mi] >= atlas.PRESENCE).mean()) if mi.sum() else float('nan')
        return self.card('f_map_bifidobacterium', 'World map: Bifidobacterium prevalence', m.sum() - S.country[m].isna().sum(), S.study_accession[m & S.country.notna().values].nunique(),
                         f'In neonates and infants Bifidobacterium is present in {early_prev * 100:.0f}% of samples overall, so the chart shows children and adults, where prevalence differs between countries. '
                         'Highest: ' + ', '.join(f'{r.country} {r.prevalence * 100:.0f}%' for r in hi.itertuples()) + '; lowest: ' + ', '.join(f'{r.country} {r.prevalence * 100:.0f}%' for r in lo.itertuples()) + '. The CSV also carries the infant values per country.',
                         f'Prevalence = share of child-to-elderly samples with genus relative abundance >= 1e-4 (0.01 %); countries with >= {MIN_COUNTRY_N} such samples (n={len(cn)}). Dot colour = HDI band ({atlas.HDI_SOURCE}); '
                         f'black tick = median of per-study prevalences (studies with >= {MIN_STUDY_N} samples in the country).',
                         'Country-level prevalence is a mixture of a few studies per country, each with its own recruitment; the 1e-4 threshold is sensitive to sequencing depth; '
                         f'country is missing for {S.country.isna().mean() * 100:.0f} % of samples.',
                         dict(highest=[[r.country, float(r.prevalence)] for r in hi.itertuples()], lowest=[[r.country, float(r.prevalence)] for r in lo.itertuples()], n_countries=int(len(cn)), early_life_prevalence=early_prev))

    # --- g -----------------------------------------------------------------------------------------------------------
    def g_depth_vs_richness(self):
        S = self.S
        d = pd.DataFrame(dict(study=S.study_accession.values, depth=S.root_coverage_sum.values.astype(float), rich=S.richness_genus.values.astype(float))).dropna()
        d = d[d.depth > 0]
        if len(d) < 30:
            return None
        r_all = stats.spearmanr(d.depth, d.rich).statistic
        rows = []
        for s, x in d.groupby('study'):
            if len(x) >= MIN_STUDY_N and x.depth.nunique() > 2:
                rows.append(dict(study=s, n=len(x), rho=float(stats.spearmanr(x.depth, x.rich).statistic), median_depth=float(x.depth.median()), median_richness=float(x.rich.median())))
        st = pd.DataFrame(rows)
        lb = np.log10(d.depth); bins = np.arange(np.floor(lb.min() * 4) / 4, lb.max() + 0.25, 0.25); mid = (bins[:-1] + bins[1:]) / 2
        med = d.groupby(pd.cut(lb, bins), observed=False).rich.median()
        fig, axes = plt.subplots(1, 2, figsize=(8.4, 3.6), gridspec_kw=dict(width_ratios=[1.5, 1]))
        ax = axes[0]; hb = ax.hexbin(lb, d.rich, gridsize=45, bins='log', cmap='Greys', mincnt=1, linewidths=0.1); ax.plot(mid, med.values, color='#d6604d', lw=1.8, label='median richness per 0.25-log10 depth bin')
        ax.set_xlabel('log10 root coverage (depth proxy)'); ax.set_ylabel('genus richness'); ax.set_title(f'Richness rises with depth (Spearman ρ = {r_all:.2f}, n = {fmt_n(len(d))})'); ax.legend(loc='upper left')
        fig.colorbar(hb, ax=ax, label='samples (log)', shrink=0.8)
        ax = axes[1]
        if len(st):
            ax.hist(st.rho, bins=np.linspace(-1, 1, 33), color='#4393c3'); ax.axvline(st.rho.median(), color='black', lw=1); ax.axvline(0, color='#888888', lw=0.6, ls=':')
            ax.set_xlabel('within-study Spearman ρ (depth, richness)'); ax.set_ylabel('studies'); ax.set_title(f'Within {len(st)} studies: median ρ = {st.rho.median():.2f}')
        fig.tight_layout(); self.save(fig, 'g_depth_vs_richness', st if len(st) else pd.DataFrame(dict(bin_mid=mid, median_richness=med.values)))
        pd.DataFrame(dict(log10_depth_bin_mid=mid, median_richness=med.values)).to_csv(self.obs / 'g_depth_vs_richness_bins.csv', index=False)
        med_rho = float(st.rho.median()) if len(st) else None; fpos = float((st.rho > 0).mean()) if len(st) else None
        return self.card('g_depth_vs_richness', 'Caution: richness tracks sequencing depth', len(d), d.study.nunique(),
                         'The number of genera detected in a sample rises steadily with the amount of sequence profiled' + (f', and this holds inside individual studies too (median within-study Spearman ρ = {med_rho:.2f}; positive in {fpos * 100:.0f} % of studies)' if med_rho is not None else '') +
                         '. Any richness difference between groups that also differ in depth (countries, eras, sample types) is therefore partly or wholly technical. The atlas does not rarefy, so read richness cards with this curve in mind.',
                         f'All {fmt_n(len(d))} analysis samples; depth proxy = Sandpiper root_coverage_sum (marker-gene coverage summed over the profile; roughly proportional to bases sequenced); richness = genera with relabund > 0. '
                         f'Hexbin on log10 depth; line = median richness in 0.25-log10 depth bins (companion CSV g_depth_vs_richness_bins.csv). Within-study ρ computed for studies with >= {MIN_STUDY_N} samples (n={len(st)}); the card CSV lists them.',
                         'Root coverage also depends on read length and library type; samples flagged low-depth (root coverage < 2) were already excluded; only Illumina runs >= 100 Mbp are profiled by Sandpiper.',
                         dict(spearman_all=float(r_all), median_within_study_rho=med_rho, n_studies=int(len(st)), frac_positive=fpos))

    # --- h -----------------------------------------------------------------------------------------------------------
    def h_variance_explained(self, n_sub=5000, n_perm=20):
        S, D = self.S, self.D
        m = (S.country.notna() & S.age_category.ne('unknown')).values
        idx = np.where(m)[0]
        if len(idx) < 200:
            return None
        rng = np.random.default_rng(self.seed); sub = np.sort(rng.choice(idx, size=min(n_sub, len(idx)), replace=False))
        gi = np.where(D.prev >= 0.10)[0]
        if len(gi) < 5:
            return None
        X = D.M[sub][:, gi].toarray().astype(np.float64)
        clr = np.log(X + PSEUDO); clr -= clr.mean(axis=1, keepdims=True)
        from scipy.spatial.distance import pdist, squareform
        Da = squareform(pdist(clr, 'euclidean')) ** 2
        Xn = X / np.clip(X.sum(axis=1, keepdims=True), 1e-12, None); Xn[X.sum(axis=1) == 0] = 0
        Db = squareform(pdist(Xn, 'braycurtis')); Db[np.isnan(Db)] = 1.0; Db = Db ** 2
        dists = {'aitchison': Da, 'braycurtis': Db}
        if D.pca is not None and {'pc1', 'pc2', 'pc3', 'pc4', 'pc5'} <= set(D.pca.columns):
            P = D.pca.set_index('sample_key').reindex(S.sample_key.values[sub])[['pc1', 'pc2', 'pc3', 'pc4', 'pc5']].values.astype(float)
            ok = ~np.isnan(P).any(axis=1)
            if ok.mean() > 0.9:
                P[~ok] = np.nanmean(P, axis=0); dists['pca5'] = squareform(pdist(P, 'euclidean')) ** 2

        def r2(D2, labels):
            lab = pd.factorize(pd.Series(labels).fillna('__na__'))[0]; n = len(lab); sst = D2.sum() / (2 * n); ssw = 0.0
            for k in np.unique(lab):
                ii = np.where(lab == k)[0]
                if len(ii) > 1:
                    ssw += D2[np.ix_(ii, ii)].sum() / (2 * len(ii))
            return 1 - ssw / sst

        factors = {'study_accession': S.study_accession.values[sub], 'country': S.country.values[sub], 'age_category': S.age_category.values[sub]}
        if S.health_condition.notna().any():
            factors['health_condition'] = S.health_condition.values[sub]
        if S.band.notna().any():
            factors['hdi_band'] = S.band.values[sub]
        rows = []
        for f, lab in factors.items():
            r = dict(factor=f, n_levels=int(pd.Series(lab).nunique(dropna=True)))
            for dn, D2 in dists.items():
                r[f'r2_{dn}'] = float(r2(D2, lab))
            r['r2_aitchison_permuted_mean'] = float(np.mean([r2(Da, rng.permutation(lab)) for _ in range(n_perm)]))
            rows.append(r)
        tab = pd.DataFrame(rows).sort_values('r2_aitchison', ascending=False)
        fig, ax = plt.subplots(figsize=(6.4, 3.2)); y = np.arange(len(tab))
        ax.barh(y - 0.18, tab.r2_aitchison, height=0.34, color='#4393c3', label='Aitchison (CLR Euclidean)'); ax.barh(y + 0.18, tab.r2_braycurtis, height=0.34, color='#92c5de', label='Bray–Curtis')
        ax.scatter(tab.r2_aitchison_permuted_mean, y - 0.18, marker='|', s=60, color='black', zorder=4, label='label-permuted baseline (Aitchison)')
        ax.set_yticks(y); ax.set_yticklabels([f"{f.replace('_', ' ')} ({n} levels)" for f, n in zip(tab.factor, tab.n_levels)]); ax.invert_yaxis(); ax.set_xlabel('R² (PERMANOVA numerator, one factor at a time)')
        ax.set_title(f'Study explains {tab.r2_aitchison.iloc[0] * 100:.0f} % of Aitchison variance on {fmt_n(len(sub))} random samples'); ax.legend(loc='lower right'); ax.margins(0.05)
        fig.tight_layout(); self.save(fig, 'h_variance_explained', tab)
        t = tab.set_index('factor')
        desc = (f"On a random subsample of {fmt_n(len(sub))} samples, the study a sample came from explains {t.loc['study_accession', 'r2_aitchison'] * 100:.0f}% of the Aitchison-distance variance "
                f"(Bray–Curtis: {t.loc['study_accession', 'r2_braycurtis'] * 100:.0f}%), versus {t.loc['country', 'r2_aitchison'] * 100:.0f}% for country, {t.loc['age_category', 'r2_aitchison'] * 100:.0f}% for life stage" +
                (f" and {t.loc['health_condition', 'r2_aitchison'] * 100:.0f}% for health condition" if 'health_condition' in t.index else '') +
                (f"; HDI band explains {t.loc['hdi_band', 'r2_aitchison'] * 100:.0f}%" if 'hdi_band' in t.index else '') +
                ". Study has many more levels than the other factors and so absorbs part of their signal, but the label-permuted baselines (black ticks) show how much R² the number of levels alone produces; "
                "study still stands far above its baseline. This is why every observation on this page reports a within-study check.")
        return self.card('h_variance_explained', 'How much of the composition does "study" explain?', len(sub), int(pd.Series(factors['study_accession']).nunique()), desc,
                         f'Subsample: {fmt_n(len(sub))} samples drawn without replacement (seed {self.seed}) from samples with a known country and life stage; matrix = {len(gi)} genera with prevalence >= 10 %, CLR after adding 1e-5, '
                         f'Euclidean (Aitchison) distance; Bray–Curtis on renormalised relabund (samples with none of the kept genera set to distance 1)' + ('; also the Euclidean distance on the 5 released PCA scores (column r2_pca5)' if 'pca5' in dists else '') +
                         f'. R² is the PERMANOVA numerator, one factor at a time (marginal, unadjusted). Permuted baseline = mean R² over {n_perm} label shuffles. HDI band from {atlas.HDI_SOURCE}.',
                         'Marginal R² values are not additive and the factors are nested (study within country, mostly one life stage per study); a nested model would attribute shared variance differently. '
                         'Subsample results vary by about ±0.01 between seeds (not shown).', dict(table=tab.to_dict('records')))

    # --- i -----------------------------------------------------------------------------------------------------------
    def i_dominant_genus_by_age(self):
        S = self.S
        top = pd.Series(S.top_genus_atlas.values).map(pretty)
        cats = [a for a in AGE_ORDER if (S.age_category == a).any()]
        common = top.value_counts().index[:8].tolist()
        t2 = top.where(top.isin(common), 'other')
        ct = pd.crosstab(S.age_category.values, t2.values).reindex(index=cats, columns=common + ['other'], fill_value=0)
        sh = ct.div(ct.sum(axis=1), axis=0)
        tab = sh.reset_index().rename(columns={'index': 'age_category'}); tab.insert(1, 'n_samples', ct.sum(axis=1).values); tab.insert(2, 'n_studies', [S.study_accession[S.age_category == a].nunique() for a in cats])
        fig, ax = plt.subplots(figsize=(7.2, 3.6)); bottom = np.zeros(len(cats)); pal = plt.get_cmap('tab10')
        for j, g in enumerate(common + ['other']):
            ax.bar(range(len(cats)), sh[g].values, bottom=bottom, color=GENUS_COL.get(g, pal(j)) if g != 'other' else '#cccccc', width=0.7, label=g); bottom += sh[g].values
        ax.set_xticks(range(len(cats))); ax.set_xticklabels([f'{a}\n{fmt_n(n)}' for a, n in zip(cats, ct.sum(axis=1))]); ax.set_ylabel('share of samples'); ax.set_ylim(0, 1)
        ax.set_title('Which genus is most abundant in a sample, by life stage (n samples under each bar)'); ax.legend(loc='center left', bbox_to_anchor=(1.01, 0.5), prop={'style': 'italic'}); fig.tight_layout()
        self.save(fig, 'i_dominant_genus_by_age', tab)
        shares = {g: {a: round(float(sh.loc[a, g]), 3) for a in cats} for g in common}
        desc = 'For each sample the single most abundant genus was taken from the Sandpiper profile. '
        if 'Bifidobacterium' in sh.columns and {'neonate', 'infant', 'adult'} <= set(cats):
            desc += f"Bifidobacterium is the top genus in {sh.loc['neonate', 'Bifidobacterium'] * 100:.0f}% of neonates and {sh.loc['infant', 'Bifidobacterium'] * 100:.0f}% of infants but only {sh.loc['adult', 'Bifidobacterium'] * 100:.0f}% of adults"
            desc += f"; Escherichia is the top genus in {sh.loc['neonate', 'Escherichia'] * 100:.0f}% of neonatal samples. " if 'Escherichia' in sh.columns else '. '
        if 'unknown' in cats and 'Bifidobacterium' in sh.columns:
            desc += f"The {fmt_n(ct.loc['unknown'].sum())} samples without an age category have a dominant-genus mix that sits between children and adults (Bifidobacterium-dominated in {sh.loc['unknown', 'Bifidobacterium'] * 100:.0f}%), i.e. they are mostly but not exclusively adult."
        return self.card('i_dominant_genus_by_age', 'Exploratory: which genus dominates a sample, by life stage', self.D.N, S.study_accession.nunique(), desc,
                         f'All analysis samples (n={fmt_n(self.D.N)}); top genus = genus with the largest relative abundance in the genus table; the eight most common top genera are shown, the rest grouped as other.',
                         'Dominance is a single-feature summary and is sensitive to the GTDB genus splits (Phocaeicola vs Bacteroides); the unknown-age group is defined by missing metadata, not by biology.',
                         dict(shares=shares), tag='exploratory')

    def _infant_two_group(self, cid, title, col, a_val, b_val, genera, label_a, label_b, definition_note, confounder):
        S, D = self.S, self.D
        if col not in S.columns or S[col].isna().all():
            return None
        early = S.age_category.isin(EARLY).values
        ma, mb = (early & S[col].eq(a_val).values), (early & S[col].eq(b_val).values)
        if ma.sum() < MIN_STUDY_N or mb.sum() < MIN_STUDY_N:
            return None
        rows, pers = [], []
        for g in genera:
            c, per = within_study(S, ma, mb, D.lg(D.genus(g)), label_a=label_a, label_b=label_b); rows.append(dict(genus=g, **{k: c[k] for k in ['n_studies', 'median_effect', 'frac_agree', 'pooled_effect', 'n_a', 'n_b']})); pers.append(per)
        tab = pd.DataFrame(rows)
        if not tab.n_studies.max():
            return None
        fig, ax = plt.subplots(figsize=(6.4, 3.4)); effect_dot_plot(ax, pers, genera, f'{label_b} − {label_a}, within {int(tab.n_studies.max())} infant studies', 'Δ mean log10(relabund + 1e-5)'); fig.tight_layout()
        self.save(fig, cid, tab)
        n = int((ma | mb).sum()); nst = int(S.study_accession[ma | mb].nunique())
        sent = '; '.join(f"{r.genus}: {r.n_studies} studies, median Δ={r.median_effect:+.2f}, {r.frac_agree * 100:.0f}% agree" for r in tab.itertuples() if r.n_studies)
        return tab, n, nst, sent, definition_note, confounder

    def i_delivery_mode_infants(self):
        genera = ['Bifidobacterium', 'Bacteroides', 'Phocaeicola', 'Escherichia', 'Enterococcus', 'Klebsiella']
        r = self._infant_two_group('i_delivery_mode_infants', 'Exploratory: birth mode and the infant gut, within studies', 'delivery_mode', 'vaginal', 'c_section', genera, 'vaginal', 'C-section',
                                   'Neonate and infant samples with a curated delivery_mode', 'Infant age at sampling differs between studies and the C-section effect fades with age; antibiotic exposure at birth, feeding and NICU status are not adjusted; '
                                   'delivery mode is a sample-level attribute here (R1/R2) but comes from a minority of infant studies.')
        if r is None:
            return None
        tab, n, nst, sent, dn, conf = r; t = tab.set_index('genus')
        desc = ('Among infant studies that recorded both vaginal and Caesarean births, the well-known Caesarean signature ' +
                ('reproduces study by study: ' if all(t.loc[g, 'frac_agree'] and t.loc[g, 'frac_agree'] >= 0.75 for g in ['Bacteroides', 'Phocaeicola', 'Enterococcus', 'Klebsiella'] if t.loc[g, 'n_studies']) else 'is only partly reproduced: ') +
                f"Bacteroides and Phocaeicola are depleted after C-section (median Δ {t.loc['Bacteroides', 'median_effect']:+.2f} / {t.loc['Phocaeicola', 'median_effect']:+.2f}) while Enterococcus and Klebsiella are enriched "
                f"({t.loc['Enterococcus', 'median_effect']:+.2f} / {t.loc['Klebsiella', 'median_effect']:+.2f}). Bifidobacterium shows a median within-study difference of {t.loc['Bifidobacterium', 'median_effect']:+.2f} "
                f"({t.loc['Bifidobacterium', 'frac_agree'] * 100:.0f}% of studies agree) against a pooled contrast of {t.loc['Bifidobacterium', 'pooled_effect']:+.2f}" +
                (' — a textbook case of a between-study artefact. ' if abs(t.loc['Bifidobacterium', 'median_effect']) < 0.2 and abs(t.loc['Bifidobacterium', 'pooled_effect']) > 0.5 else '. ') + 'Each dot is one study.')
        return self.card('i_delivery_mode_infants', 'Exploratory: birth mode and the infant gut, within studies', n, nst, desc,
                         f'{dn} (n={fmt_n(n)}, {nst} studies); unit = study with >= {MIN_STUDY_N} samples in both birth modes; effect = mean log10(relabund + 1e-5) in C-section minus vaginal; summary = median across studies; '
                         f'agree = share of studies with the sign of the median. {sent}.', conf, dict(summary=tab.to_dict('records')), tag='exploratory')

    def i_feeding_mode_infants(self):
        genera = ['Bifidobacterium', 'Bacteroides', 'Escherichia', 'Klebsiella', 'Enterococcus']
        r = self._infant_two_group('i_feeding_mode_infants', 'Exploratory: feeding mode and the infant gut, within studies', 'feeding_mode', 'exclusive_breast', 'formula', genera, 'exclusive breast', 'formula',
                                   'Neonate and infant samples with feeding_mode exclusive_breast or formula', 'Feeding mode changes with age and is recorded at different ages across studies; mixed feeding excluded; delivery mode and antibiotics not adjusted; very few studies qualify.')
        if r is None:
            return None
        tab, n, nst, sent, dn, conf = r; t = tab.set_index('genus'); k = int(tab.n_studies.max())
        desc = (f"Only {k} infant stud{'y' if k == 1 else 'ies'} record exclusive breastfeeding and formula feeding for at least {MIN_STUDY_N} infants each, so the evidence base is thin. " +
                '. '.join(f"{g}: median Δ {t.loc[g, 'median_effect']:+.2f} with {t.loc[g, 'frac_agree'] * 100:.0f}% of {int(t.loc[g, 'n_studies'])} studies agreeing" for g in genera if t.loc[g, 'n_studies']) +
                f". Bifidobacterium is {'lower' if t.loc['Bifidobacterium', 'median_effect'] < 0 else 'not lower'} in formula-fed infants in the median study.")
        return self.card('i_feeding_mode_infants', 'Exploratory: feeding mode and the infant gut, within studies', n, nst, desc,
                         f'{dn} (n={fmt_n(n)}, {nst} studies); unit = study with >= {MIN_STUDY_N} samples in both groups; effect = mean log10(relabund + 1e-5) in formula minus exclusive-breast; summary = median across studies. {sent}.',
                         conf, dict(summary=tab.to_dict('records')), tag='exploratory')

    def i_akkermansia(self):
        S, D = self.S, self.D
        x = D.genus('Akkermansia'); p = (x >= atlas.PRESENCE).astype(float)
        rows, dots = [], []
        ma = S.age_category.isin(AGE_ORDER[:-1]).values
        for a in AGE_ORDER[:-1]:
            m = ma & (S.age_category.values == a)
            if m.sum():
                rows.append(dict(facet='life stage', group=a, n_samples=int(m.sum()), n_studies=int(S.study_accession[m].nunique()), prevalence=float(p[m].mean()), mean_relabund=float(x[m].mean())))
        mh = self._adult_hdi_mask()
        for b in ['low', 'middle', 'high']:
            m = mh & (S.band.values == b)
            if m.sum():
                rows.append(dict(facet='HDI band', group=b, n_samples=int(m.sum()), n_studies=int(S.study_accession[m].nunique()), prevalence=float(p[m].mean()), mean_relabund=float(x[m].mean())))
                st = pd.DataFrame(dict(study=S.study_accession.values[m], p=p[m])).groupby('study').p.agg(['mean', 'size']); st = st[st['size'] >= MIN_STUDY_N]
                dots.append((b, st['mean'].values))
        tab = pd.DataFrame(rows)
        if tab.empty:
            return None
        fig, axes = plt.subplots(1, 2, figsize=(7.6, 3.2), gridspec_kw=dict(width_ratios=[1.6, 1]))
        t = tab[tab.facet == 'life stage']; axes[0].bar(range(len(t)), t.prevalence, color=GENUS_COL['Akkermansia'], alpha=0.8); axes[0].set_xticks(range(len(t))); axes[0].set_xticklabels(t.group); axes[0].set_ylabel('prevalence (relabund ≥ 1e-4)')
        axes[0].set_title('Akkermansia prevalence rises with life stage'); axes[0].margins(0.04)
        t = tab[tab.facet == 'HDI band']; axes[1].bar(range(len(t)), t.prevalence, color=[BAND_COL.get(b if isinstance(b, str) else None) for b in t.group], alpha=0.8); axes[1].set_xticks(range(len(t))); axes[1].set_xticklabels([f'HDI {b}' for b in t.group])
        for i, (b, v) in enumerate(dots):
            axes[1].scatter(np.full(len(v), i) + np.random.default_rng(i).uniform(-0.25, 0.25, len(v)), v, s=6, color='black', alpha=0.4, lw=0)
        axes[1].set_title('Child–elderly, by HDI band (dots = studies)'); axes[1].margins(0.04); fig.tight_layout()
        self.save(fig, 'i_akkermansia', tab)
        ti = tab.set_index(['facet', 'group']); nd = int(sum(len(v) for _, v in dots))
        desc = 'Akkermansia (the mucin degrader A. muciniphila) is detected in '
        if ('life stage', 'neonate') in ti.index and ('life stage', 'adult') in ti.index:
            desc += f"{ti.loc[('life stage', 'neonate'), 'prevalence'] * 100:.0f}% of neonates and {ti.loc[('life stage', 'adult'), 'prevalence'] * 100:.0f}% of adults. "
        if ('HDI band', 'low') in ti.index and ('HDI band', 'high') in ti.index:
            desc += (f"Among children and adults its prevalence is {ti.loc[('HDI band', 'low'), 'prevalence'] * 100:.0f}% in low-HDI countries versus {ti.loc[('HDI band', 'high'), 'prevalence'] * 100:.0f}% in high-HDI countries, "
                     'consistent with its BloSSUM classification in the volcano card; the per-study dots show the spread behind the bars.')
        return self.card('i_akkermansia', 'Exploratory: Akkermansia prevalence by life stage and HDI band', D.N, S.study_accession.nunique(), desc,
                         f'Prevalence at relabund >= 1e-4; life-stage panel over samples with a known age category (n={fmt_n(ma.sum())}); HDI panel over child-to-elderly samples with a country HDI (n={fmt_n(mh.sum())}; {atlas.HDI_SOURCE}); '
                         f'dots = per-study prevalence for studies with >= {MIN_STUDY_N} samples in the band (n={nd}).',
                         'Prevalence at a 1e-4 threshold depends on depth, and low-HDI cohorts tend to be shallower; the HDI panel is not depth-matched.', dict(table=tab.to_dict('records')), tag='exploratory')

    def i_species_prevalence_by_age(self):
        S, D = self.S, self.D
        sp = D.species_long
        if sp is None or not len(sp):
            return None
        key2idx = pd.Series(np.arange(D.N), index=S.sample_key.values)
        sp = sp[sp.sample_key.isin(set(S.sample_key)) & (sp.species != 'unassigned_at_species') & (sp.relabund >= atlas.SPECIES_PRESENCE)]
        top = sp.species.value_counts().index[:12].tolist()
        sp = sp[sp.species.isin(top)]
        cats = [a for a in AGE_ORDER if (S.age_category == a).any()]
        denom = S.age_category.value_counts()
        age_of = S.age_category.values[key2idx.reindex(sp.sample_key.values).values]
        ct = pd.crosstab(sp.species.values, age_of).reindex(index=top, columns=cats, fill_value=0)
        prev = ct.div(denom.reindex(cats), axis=1)
        tab = prev.reset_index().rename(columns={'index': 'species'}); tab['n_all'] = ct.sum(axis=1).values; tab.insert(1, 'prevalence_all', ct.sum(axis=1).values / D.N)
        fig, ax = plt.subplots(figsize=(7.2, 4.2))
        im = ax.imshow(prev.values, cmap='viridis', vmin=0, vmax=max(0.05, prev.values.max()), aspect='auto')
        ax.set_xticks(range(len(cats))); ax.set_xticklabels([f'{c}\n{fmt_n(denom[c])}' for c in cats]); ax.set_yticks(range(len(top))); ax.set_yticklabels([pretty(s) for s in top], style='italic')
        for i in range(len(top)):
            for j in range(len(cats)):
                v = prev.values[i, j]; ax.text(j, i, f'{v * 100:.0f}', ha='center', va='center', fontsize=6, color='white' if v < 0.55 * prev.values.max() else 'black')
        fig.colorbar(im, ax=ax, label='prevalence (relabund ≥ 0.1 %)', shrink=0.8); ax.set_title('The 12 most prevalent species are adult-type organisms (% of samples; n under each column)'); fig.tight_layout()
        self.save(fig, 'i_species_prevalence_by_age', tab)
        table = {c: {pretty(s): round(float(prev.loc[s, c]), 3) for s in top} for c in cats}
        peak = prev.idxmax(axis=1); infant_peak = [pretty(s) for s in top if peak[s] in EARLY]
        desc = (f'At species level (GTDB R232 names; species table thresholded at 0.1 % relative abundance) the twelve most prevalent species in the whole collection are ' +
                (f'adult-type organisms except {", ".join(infant_peak)}, which peak{"s" if len(infant_peak) == 1 else ""} in early life' if infant_peak else 'all adult-type organisms') +
                (f"; the others are found in {prev.loc[[s for s in top if pretty(s) not in infant_peak], 'neonate'].min() * 100:.0f}–{prev.loc[[s for s in top if pretty(s) not in infant_peak], 'neonate'].max() * 100:.0f} % of neonates and climb through infancy and childhood. " if 'neonate' in cats else '. ') +
                ('The unknown-age column again resembles adults. ' if 'unknown' in cats else '') + 'Suffixed names such as Blautia_A are GTDB placeholder genera.')
        return self.card('i_species_prevalence_by_age', 'Exploratory: the most prevalent species across life stages', D.N, S.study_accession.nunique(), desc,
                         f'Species prevalence = share of samples with relabund >= 1e-3 (the species table is released at this threshold); denominators are all analysis samples per life stage (n = ' +
                         ', '.join(f'{c} {fmt_n(denom[c])}' for c in cats) + ').',
                         'The 0.1 % species threshold is ten times the genus presence threshold, so species prevalence is depth-sensitive and lower than genus prevalence; life stage is a study-level attribute for most samples.',
                         dict(table=table), tag='exploratory')

    def i_recruitment_site(self):
        """Exploratory: hospital/clinic-recruited vs community-recruited adults (location_site text), unit = study."""
        S, D = self.S, self.D
        if S.location_site.isna().all():
            return None
        ls = S.location_site.fillna('').astype(str).str.lower()
        hosp = ls.str.contains(HOSPITAL_RE); comm = ls.str.contains(COMMUNITY_RE)
        adult = S.age_category.isin(ADULT_LIKE).values
        a = (comm & ~hosp).values & adult; b = (hosp & ~comm).values & adult
        rich = S.richness_genus.values.astype(float); ent = D.lg(D.family('f__Enterobacteriaceae'))
        res = {}
        for name, v in [('genus richness', rich), ('Enterobacteriaceae (log10 relabund)', ent)]:
            r, sa, sb = study_level(S, a, b, v); res[name] = (r, sa, sb)
        r0 = res['genus richness'][0]
        if r0['n_lo'] < 3 or r0['n_hi'] < 3:
            return None
        rows = [dict(feature=k, n_studies_community=r['n_lo'], n_studies_hospital=r['n_hi'], median_community=float(sa.median()), median_hospital=float(sb.median()), effect_hospital_minus_community=float(sb.median() - sa.median()),
                     p_mannwhitney_studies=r['p'], n_samples_community=int(a.sum()), n_samples_hospital=int(b.sum())) for k, (r, sa, sb) in res.items()]
        tab = pd.DataFrame(rows)
        fig, axes = plt.subplots(1, 2, figsize=(7.2, 3.2))
        for ax, (k, (r, sa, sb)) in zip(axes, res.items()):
            for i, (lab, v) in enumerate([('community', sa), ('hospital / clinic', sb)]):
                ax.scatter(np.full(len(v), i) + np.random.default_rng(i).uniform(-0.15, 0.15, len(v)), v, s=12, alpha=0.6, color='#555555', lw=0); ax.hlines(v.median(), i - 0.25, i + 0.25, color='black', lw=1.5)
            ax.set_xticks([0, 1]); ax.set_xticklabels([f'community\n({r["n_lo"]} studies)', f'hospital / clinic\n({r["n_hi"]} studies)']); ax.set_title(f'{k}: p = {r["p"]:.2g} (studies)'); ax.set_ylabel('study mean'); ax.margins(0.1)
        fig.tight_layout(); self.save(fig, 'i_recruitment_site', tab)
        e = tab.set_index('feature')
        desc = (f"Adults whose location_site names a hospital or clinic ({fmt_n(b.sum())} samples, {r0['n_hi']} studies with >= {MIN_STUDY_N}) versus adults whose location_site names a community, village, household or town "
                f"({fmt_n(a.sum())} samples, {r0['n_lo']} studies): at the study level, genus richness differs by {e.loc['genus richness', 'effect_hospital_minus_community']:+.1f} genera (hospital − community, p = {r0['p']:.2g}) "
                f"and Enterobacteriaceae by {e.loc['Enterobacteriaceae (log10 relabund)', 'effect_hospital_minus_community']:+.2f} log10 (p = {res['Enterobacteriaceae (log10 relabund)'][0]['p']:.2g}). "
                f"With only {r0['n_lo']} community-recruited studies the comparison has almost no power; recruitment site is a proxy that mixes health status, country and protocol, so this card documents the contrast rather than interpreting it.")
        return self.card('i_recruitment_site', 'Exploratory: hospital- vs community-recruited adults (location_site)', int(a.sum() + b.sum()), int(S.study_accession[a | b].nunique()), desc,
                         f'Adults (child–elderly) with a curated location_site; hospital/clinic = site text matches /{HOSPITAL_RE.pattern}/ and not the community pattern; community = matches /{COMMUNITY_RE.pattern}/ and not the hospital pattern. '
                         f'Unit = study with >= {MIN_STUDY_N} samples in the group: study mean of genus richness and of log10(Enterobacteriaceae relabund + 1e-5) (family sum over GTDB genera); effect = difference of group medians over studies; '
                         'p two-sided Mann–Whitney over studies.',
                         'location_site is recorded for a minority of samples (mostly R1 attributes of clinical cohorts); hospital-recruited samples are dominated by patients (the disease card handles health condition '
                         'within studies), community samples by a handful of population cohorts from other countries; no study contains both groups, so the contrast is entirely between studies.',
                         dict(table=tab.to_dict('records')), tag='exploratory')


# -------------------------------------------------------------------------------------------------------------- driver
def run(genus_long, species_long, summary, wide, pca, hdi, out_dir, seed=42, only=None, generated_from=''):
    style(); warnings.filterwarnings('ignore', category=RuntimeWarning)
    t0 = time.time()
    D = Data(genus_long, species_long, summary, wide, pca, hdi)
    log(f'analysis samples: {D.N}, studies: {D.S.study_accession.nunique()}, genera: {len(D.genera)} ({time.time() - t0:.0f}s)')
    C = Cards(D, out_dir, seed=seed); C.run(only=only)
    meta = dict(n_samples=int(D.N), n_studies=int(D.S.study_accession.nunique()), presence_threshold=atlas.PRESENCE, species_threshold=atlas.SPECIES_PRESENCE, sample_filter=atlas.SAMPLE_FILTER,
                hdi_source=atlas.HDI_SOURCE, hdi_countries_with_value=int(D.S.country.dropna().map(D.hdi).notna().groupby(D.S.country.dropna()).first().sum()), min_study_n=MIN_STUDY_N, pseudo_count=PSEUDO,
                generated_from=generated_from, generated_by='scripts/atlas_observations.py', n_cards=len(C.cards), skipped=C.skipped, seed=seed)
    (Path(out_dir) / 'observations.json').write_text(json.dumps(dict(meta=meta, cards=C.cards), separators=(',', ':'), ensure_ascii=False, default=_json_default))
    log(f'{len(C.cards)} cards, {len(C.skipped)} skipped, {time.time() - t0:.0f}s')
    return meta, C.cards


def _json_default(o):
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return None if np.isnan(o) else float(o)
    if isinstance(o, np.ndarray):
        return o.tolist()
    if isinstance(o, float) and np.isnan(o):
        return None
    raise TypeError(str(type(o)))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--genus', required=True); ap.add_argument('--species', required=True); ap.add_argument('--summary', required=True); ap.add_argument('--wide', required=True)
    ap.add_argument('--pca', default=None); ap.add_argument('--hdi', default=None, help='UNDP HDI csv (iso2, hdi_2023); default = site_generator/gen/data/undp_hdr2025_hdi_2023.csv')
    ap.add_argument('--out', required=True, help='atlas payload directory (writes obs/ and observations.json)'); ap.add_argument('--seed', type=int, default=42)
    ap.add_argument('--only', nargs='*', default=None, help='card method names to run (debugging)'); ap.add_argument('--generated-from', default='')
    a = ap.parse_args(argv)
    hdi = atlas.load_hdi(a.hdi) if a.hdi else None
    genus = pd.read_parquet(a.genus, columns=['sample_key', 'genus', 'lineage', 'relabund'])
    species = pd.read_parquet(a.species, columns=['sample_key', 'species', 'relabund'])
    summary = pd.read_parquet(a.summary); wide = pd.read_parquet(a.wide); pca = pd.read_parquet(a.pca) if a.pca else None
    run(genus, species, summary, wide, pca, hdi, a.out, seed=a.seed, only=a.only, generated_from=a.generated_from or f'{Path(a.wide).name} + {Path(a.genus).name}')


if __name__ == '__main__':
    main()
