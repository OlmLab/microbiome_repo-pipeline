"""Study-design block for project pages (R2026.32 / site 1.27.1, owner request: "understand the overall design of each study and
present that to the end user").

Everything here is assembled from catalog facts, no new model reading:
  design type   study_shape (read from the paper) > study_shape_classification (admission classification) > inferred from the
                sample variables (arms, case/control labels, repeated sampling)
  groups        the per-sample intervention arm when arms are resolved, otherwise the per-sample health_condition
  people        subject_id; sampling = samples per person; sites = body_site_code
  checks        the paper / classification against what the sample variables show
The diagram is a group x body-site matrix (people per cell, or samples when people cannot be told apart). Each element names the
field and the archive attribute or text it came from. SVG classes are ds-* only (a bare .bar rule in site.css once resized chart
rectangles in browsers)."""
import html
import math

import pandas as pd

E = html.escape

SHAPE_LABEL = {
    'case_control': 'Case–control study',
    'cross_sectional_groups': 'Cross-sectional comparison of groups',
    'cohort_longitudinal': 'Longitudinal cohort',
    'case_series': 'Single-group study',
    'population_survey': 'Population survey',
    'rct': 'Randomised controlled trial',
    'crossover': 'Crossover trial',
    'single_arm_intervention': 'Single-arm intervention (before / after)',
    'non_randomised_intervention': 'Non-randomised intervention study',
    'method_or_benchmark': 'Methods or benchmark study',
}
SHAPE_HELP = {
    'case_control': 'people with a condition compared with people without it',
    'cross_sectional_groups': 'groups sampled once and compared',
    'cohort_longitudinal': 'the same people followed and sampled over time',
    'case_series': 'one group of people, no comparison group',
    'population_survey': 'a population sampled broadly, not selected by condition',
    'rct': 'people randomly assigned to arms',
    'crossover': 'each person receives the treatments in turn',
    'single_arm_intervention': 'one group sampled before and after an intervention',
    'non_randomised_intervention': 'arms assigned without randomisation',
    'method_or_benchmark': 'samples used to test a laboratory or computational method',
}
REPEATED_SHAPES = {'cohort_longitudinal', 'rct', 'crossover', 'single_arm_intervention', 'non_randomised_intervention'}
GROUP_SHAPES = {'case_control', 'cross_sectional_groups'}
TRIAL_DESIGNS = {'randomized_controlled_trial', 'non_randomized_controlled', 'crossover', 'single_arm_before_after'}
STAGE_NOUN = {'neonate': ('neonate', 'neonates'), 'infant': ('infant', 'infants'), 'child': ('child', 'children'),
              'adolescent': ('adolescent', 'adolescents'), 'adult': ('adult', 'adults'), 'elderly': ('older adult', 'older adults')}
NON_DISEASE = {'healthy_control', 'unknown', 'intervention_cohort'}

CSS = """
/* R2026.32 study-design block (site 1.27.1) */
.ds-card{border:1.5px solid var(--cu-gold,#CFB87C);border-radius:12px;padding:.9rem 1.1rem 1rem;background:#fffdf7;margin-top:.4rem}
.ds-head{display:flex;align-items:baseline;gap:.6rem;flex-wrap:wrap}
.ds-type{font-size:1.22rem;font-weight:700;color:#222}.ds-src{font-size:.72rem;color:#7a6a3a;border:1px solid #e0d5b2;border-radius:999px;padding:0 .45rem;background:#fff}
.ds-help{font-size:.8rem;color:#5f6365}
.ds-sent{font-size:.95rem;line-height:1.5;margin:.35rem 0 .2rem;color:#222}
.ds-stmt{font-size:.95rem;line-height:1.5;margin:.45rem 0 .1rem;color:#222}.ds-grp{font-size:.88rem;margin:.15rem 0 .1rem;color:#333}.ds-inlab{font-size:.72rem;text-transform:uppercase;letter-spacing:.05em;color:#7a6a3a;font-weight:600}
.ds-grid{display:grid;grid-template-columns:minmax(0,1.2fr) minmax(0,1fr);gap:1.1rem 1.6rem;margin-top:.6rem;align-items:start}
@media (max-width:900px){.ds-grid{grid-template-columns:1fr}}
.ds-fig svg{display:block;max-width:100%;height:auto}
.ds-fig .ds-cap{font-size:.74rem;color:#5f6365;margin-top:.2rem;max-width:560px}
svg.ds-mat text{font-family:inherit}svg.ds-mat .ds-rl{font-size:11px;fill:#333}svg.ds-mat .ds-cl{font-size:10.5px;fill:#333}
svg.ds-mat .ds-ax{font-size:9.5px;fill:#7a6a3a;font-family:ui-monospace,Menlo,monospace}svg.ds-mat .ds-num{font-size:10px;fill:#333;font-variant-numeric:tabular-nums}
svg.ds-mat .ds-tot{font-size:10px;fill:#5f6365;font-variant-numeric:tabular-nums}
svg.ds-mat circle.ds-dot{fill:#CFB87C;fill-opacity:.8;stroke:#A88B4A;stroke-width:.8}svg.ds-mat circle.ds-dot:hover{fill:#A88B4A}
svg.ds-mat line.ds-grid-l{stroke:#eee6cf;stroke-width:1}
table.ds-el{width:100%;border-collapse:collapse;font-size:.8rem}
table.ds-el th{font-size:.66rem;text-transform:uppercase;letter-spacing:.06em;color:#5f6365;text-align:left;padding:.15rem .3rem;border-bottom:1px solid #e6e1d0;font-weight:600}
table.ds-el td{padding:.25rem .3rem;border-bottom:1px solid #f0ece0;vertical-align:top}
.ds-var{font-family:ui-monospace,Menlo,monospace;font-size:.76rem;background:#f4efdf;border:1px solid #e0d5b2;border-radius:4px;padding:0 .28rem;cursor:help;white-space:nowrap}
.ds-from{color:#444}.ds-none{color:#a0522d;font-style:italic}
.ds-cov{display:inline-block;width:2.8rem;height:6px;background:#eee;border-radius:3px;vertical-align:middle;margin-right:.3rem;position:relative;overflow:hidden}
.ds-cov i{position:absolute;left:0;top:0;bottom:0;background:#A88B4A}
ul.ds-checks{margin:.7rem 0 0;padding:0;list-style:none;font-size:.82rem}
ul.ds-checks li{display:flex;gap:.45rem;margin:.25rem 0;line-height:1.38}
.ds-ck{flex:none;width:1.05rem;height:1.05rem;border-radius:50%;font-size:.68rem;font-weight:700;display:inline-flex;align-items:center;justify-content:center;color:#fff;margin-top:.08rem}
.ds-ck.ok{background:#3f7d4e}.ds-ck.warn{background:#c88a12}.ds-ck.info{background:#8a8f93}
.ds-checks-h{font-size:.66rem;text-transform:uppercase;letter-spacing:.06em;color:#5f6365;margin-top:.3rem;font-weight:600}
details.ds-more{margin-top:.7rem}details.ds-more>summary{font-size:.66rem;text-transform:uppercase;letter-spacing:.06em;color:#5f6365;font-weight:600;cursor:pointer}
details.ds-more table.ds-el{margin-top:.3rem}
"""


def _null(v):
    return v is None or (isinstance(v, float) and math.isnan(v)) or (isinstance(v, str) and v.strip().lower() in ('', 'nan', 'none'))


def _fmt(n):
    return f'{int(n):,}'


def _days(d):
    d = float(d)
    if d < 60:
        return f'{d:.0f} day' + ('' if round(d) == 1 else 's')
    if d < 730:
        return f'{d / 30.44:.0f} months'
    return f'{d / 365.25:.1f} years'


def source_phrase(src):
    """Plain-language name of an evidence_source value."""
    s = str(src or '')
    for p in ('sample.attr.', 'biosample.attribute:', 'biosample.attributes.'):
        if s.startswith(p):
            return f"archive attribute ‘{s[len(p):]}’"
    if s.startswith('sample.name_rule:'):
        return f"a naming rule on the sample {s.split(':', 1)[1].replace('_', ' ').lower()}"
    if s.startswith('design.'):
        k, _, where = s[len('design.'):].partition(':')
        what = {'one_sample_per_subject': 'one sample per person', 'single_subject': 'a single person'}.get(k, k.replace('_', ' '))
        return f"generated: the {where or 'source'} says {what}"
    table = {'biosample_attr': 'an archive attribute', 'sample.title': 'the sample title', 'paper.fulltext.methods': "the paper's methods",
             'paper.fulltext.abstract': "the paper's abstract", 'paper.abstract': "the paper's abstract", 'study.description': 'the archive project description',
             'ena.study_description': 'the archive project description', 'paper.supp.table': 'a supplementary table of the paper',
             'external.cmd': 'curatedMetagenomicData', 'external.gmrepo': 'GMrepo', 'paper.fulltext': 'the paper'}
    for k, v in table.items():
        if s == k or s.startswith(k + '.'):
            return v
    if s.startswith('paper.'):
        return 'the paper'
    if s.startswith('external.'):
        return s.split('.', 1)[1]
    return s.replace('_', ' ') or 'not recorded'


def _sources(dg, field, k=2):
    if dg is None or not len(dg) or 'field_name' not in dg.columns:
        return [], ''
    x = dg.loc[dg.field_name == field, 'evidence_source'].astype(str)
    if not len(x):
        return [], ''
    vc = x.map(source_phrase).value_counts()
    tot = int(vc.sum())
    top = [(p, int(n)) for p, n in vc.head(k).items()]
    tip = '; '.join(f'{p}: {n:,} value{"s" if n != 1 else ""}' for p, n in vc.head(6).items())
    if len(vc) > 6:
        tip += f'; {len(vc) - 6} other sources'
    return [(p, n, n / tot) for p, n in top], tip


def _cov(n, d):
    w = 0 if not d else 100 * n / d
    return f'<span class="ds-cov"><i style="width:{w:.0f}%"></i></span>{_fmt(n)} / {_fmt(d)}'


def _shape(s):
    """(code or None, label, source tag, help, alternatives)"""
    sh = s.get('study_shape')
    if not _null(sh) and sh != 'unclear':
        return sh, SHAPE_LABEL.get(sh, sh.replace('_', ' ')), 'read from the paper', SHAPE_HELP.get(sh, ''), []
    dr = s.get('design_shape_reading')
    mode = {'fulltext': 'read from the paper (design reading)', 'abstract': 'read from the abstract (design reading)',
            'archive': 'read from the archive description (design reading)'}.get(s.get('design_text_mode'), 'design reading')
    if not _null(dr) and dr != 'unclear' and not str(dr).startswith('suggested:'):
        return dr, SHAPE_LABEL.get(dr, dr.replace('_', ' ')), mode, SHAPE_HELP.get(dr, ''), []
    sc = s.get('study_shape_classification')
    if _null(sc) or sc == 'unclear':
        sc = dr   # the design reading's two readings disagreed: show both
    if not _null(sc) and sc != 'unclear':
        if sc.startswith('suggested:'):
            a = [c for c in sc.split(':', 1)[1].split('|') if c]
            lab = ' or '.join(SHAPE_LABEL.get(c, c.replace('_', ' ')).lower() for c in a)
            return a[0], lab[:1].upper() + lab[1:], 'two readings of the source differed', SHAPE_HELP.get(a[0], ''), a
        return sc, SHAPE_LABEL.get(sc, sc.replace('_', ' ')), 'classified from the paper or abstract', SHAPE_HELP.get(sc, ''), []
    return None, None, None, None, []


def _groups(g, ivarms, hc_labels, iv_labels):
    """(field, [(code, label, mask)]) for the primary grouping variable of the specimens in g."""
    if ivarms and ivarms.get('resolved') and 'intervention' in g.columns:
        a = g.intervention.astype('string')
        vc = a.value_counts()
        out = []
        for v in vc.index:
            lab = ' + '.join(iv_labels.get(c, c.replace('_', ' ')) for c in str(v).split(';') if c)
            out.append((str(v), lab, (a == v).fillna(False).to_numpy(dtype=bool)))
        return 'intervention', out
    if 'comparison_group' in g.columns:
        cg = g.comparison_group.astype('string')
        vc = cg.dropna().value_counts()
        if len(vc) >= 2 and cg.notna().mean() >= 0.5:
            return 'comparison_group', [(str(v), str(v), (cg == v).fillna(False).to_numpy(dtype=bool)) for v in vc.index]
    if 'health_condition' in g.columns:
        h = g.health_condition.astype('string')
        vc = h[h.notna() & (h != 'unknown')].value_counts()
        thr = max(3, 0.01 * len(g))
        keep = [c for c, n in vc.items() if n >= thr]
        if len(keep) >= 2:
            return 'health_condition', [(c, hc_labels.get(c) or c.replace('_', ' '), (h == c).fillna(False).to_numpy(dtype=bool)) for c in keep]
        if len(keep) == 1:
            return 'health_condition_single', [(keep[0], hc_labels.get(keep[0]) or keep[0].replace('_', ' '), (h == keep[0]).fillna(False).to_numpy(dtype=bool))]
    return None, []


def _people(sub, mask):
    x = sub[mask].dropna()
    return int(x.nunique())


def short_label(lab):
    """'Neurological / psychiatric (PD, MS, …)' -> 'Neurological / psychiatric'"""
    import re
    s = re.sub(r'\s*\([^)]*\)', '', str(lab)).strip()
    return s or str(lab)


def _wrap2(lab, width=14):
    w = str(lab).split(' ')
    a, b = '', ''
    for x in w:
        if not b and len((a + ' ' + x).strip()) <= width:
            a = (a + ' ' + x).strip()
        else:
            b = (b + ' ' + x).strip()
    if len(b) > width + 3:
        b = b[:width + 1] + '…'
    return a, b


def matrix_svg(rows, cols, cell, totals, unit, row_field, col_field):
    """rows/cols: [(code,label)], cell: {(r,c): (value, people, samples)}, totals: {r: value}. Presentation attributes are inline
    (fill, font-size) so every renderer draws the same; CSS only adds hover."""
    rl = [short_label(l) for _, l in rows]
    rl = [x if len(x) <= 34 else x[:33] + '…' for x in rl]
    lw = int(min(240, max(90, 6.4 * max(len(x) for x in rl) + 16)))
    cw_, top, rh = 92, 64, 30
    W = lw + cw_ * len(cols) + 78
    H = top + rh * len(rows) + 10
    vmax = max([v for v, _, _ in cell.values()] + [1])
    R = lambda v: 2.5 + 11 * math.sqrt(v / vmax) if v else 0
    o = [f'<svg class="ds-mat" viewBox="0 0 {W} {H}" width="{W}" role="img" aria-label="{E(unit)} per group and body site">']
    o.append(f'<text x="{lw - 8}" y="12" text-anchor="end" font-size="9.5" fill="#7a6a3a" font-family="ui-monospace,Menlo,monospace">rows: {E(row_field)}</text>')
    o.append(f'<text x="{lw + 4}" y="12" font-size="9.5" fill="#7a6a3a" font-family="ui-monospace,Menlo,monospace">columns: {E(col_field)}</text>')
    for i, (r, _) in enumerate(rows):
        if i % 2 == 0:
            o.append(f'<rect x="0" y="{top + rh * i}" width="{W}" height="{rh}" fill="#f7f2e3" fill-opacity=".55"/>')
    for j, (c, lab) in enumerate(cols):
        x = lw + cw_ * j + cw_ / 2
        a, b = _wrap2(short_label(lab))
        o.append(f'<text x="{x:.0f}" y="{top - (22 if b else 9)}" text-anchor="middle" font-size="10.5" fill="#333"><title>{E(lab)}</title>{E(a)}</text>')
        if b:
            o.append(f'<text x="{x:.0f}" y="{top - 9}" text-anchor="middle" font-size="10.5" fill="#333">{E(b)}</text>')
    xt = lw + cw_ * len(cols) + 40
    o.append(f'<text x="{xt}" y="{top - 9}" text-anchor="middle" font-size="10.5" fill="#333" font-weight="600">{E(unit)}</text>')
    for i, (r, lab) in enumerate(rows):
        y = top + rh * i + rh / 2
        o.append(f'<text x="{lw - 8}" y="{y + 4:.0f}" text-anchor="end" font-size="11" fill="#333"><title>{E(lab)}</title>{E(rl[i])}</text>')
        for j, (c, clab) in enumerate(cols):
            v, p, n = cell.get((r, c), (0, 0, 0))
            x = lw + cw_ * j + cw_ / 2
            if not v and n:
                o.append(f'<circle cx="{x - 14:.0f}" cy="{y:.0f}" r="5" fill="none" stroke="#A88B4A" stroke-width=".9" stroke-dasharray="2 2"><title>{E(short_label(lab))} × {E(short_label(clab))}: {n:,} samples, no person identifier</title></circle>')
                o.append(f'<text x="{x - 6:.0f}" y="{y + 3.5:.0f}" font-size="9.5" fill="#7a6a3a">{n:,} s.</text>')
                continue
            if not v:
                o.append(f'<text x="{x:.0f}" y="{y + 4:.0f}" text-anchor="middle" font-size="10" fill="#bbb">·</text>')
                continue
            rr = R(v)
            tip = f'{short_label(lab)} × {short_label(clab)}: ' + (f'{p:,} people, ' if p else '') + f'{n:,} samples'
            o.append(f'<circle class="ds-dot" cx="{x - 14:.0f}" cy="{y:.0f}" r="{rr:.1f}" fill="#CFB87C" fill-opacity=".85" stroke="#A88B4A" stroke-width=".8"><title>{E(tip)}</title></circle>')
            o.append(f'<text x="{x - 14 + max(rr, 4) + 3:.0f}" y="{y + 3.5:.0f}" font-size="10" fill="#333">{v:,}</text>')
        o.append(f'<text x="{xt}" y="{y + 4:.0f}" text-anchor="middle" font-size="10.5" fill="#333" font-weight="600">{totals.get(r, 0):,}</text>')
    o.append('</svg>')
    return ''.join(o)


def build(s, g, dg, ivarms, design, hc_labels, site_labels, iv_labels):
    """HTML of the study-design card, or '' when the project has no specimens."""
    n_all = len(g)
    if not n_all:
        return ''
    spec = g[g.sample_type.fillna('specimen') == 'specimen'] if 'sample_type' in g.columns else g
    n_ctl = n_all - len(spec)
    n = len(spec)
    if not n:
        return ''
    sub = spec.subject_id.astype('string') if 'subject_id' in spec.columns else pd.Series([pd.NA] * n, index=spec.index, dtype='string')
    n_sub_s = int(sub.notna().sum())
    people_ok = n_sub_s >= 0.5 * n
    n_people = int(sub.dropna().nunique())
    per = sub.dropna().value_counts()
    n_rep = int((per >= 2).sum())
    # body sites
    bs = spec.body_site_code.astype('string') if 'body_site_code' in spec.columns else pd.Series([pd.NA] * n, index=spec.index, dtype='string')
    bs = bs.where(bs != 'unknown_site')
    site_vc = bs.dropna().value_counts()
    slab = lambda c: (site_labels.get(c) if isinstance(site_labels.get(c), str) else None) or c.replace('_', ' ')
    sites = [(c, slab(c)) for c, k in site_vc.items() if k >= max(2, 0.005 * n)][:6] or [(c, slab(c)) for c in site_vc.index[:1]]
    multi_site_people = 0
    if people_ok and len(site_vc) >= 2:
        k = pd.DataFrame({'p': sub, 'b': bs}).dropna().groupby('p').b.nunique()
        multi_site_people = int((k >= 2).sum())
    # life stage
    stage = None
    if 'age_category' in spec.columns:
        a = spec.age_category.astype('string')
        a = a[a.notna() & (a != 'unknown')].value_counts()
        if len(a) and a.iloc[0] >= 0.8 * a.sum():
            stage = a.index[0]
    # groups
    gfield, groups = _groups(spec, ivarms, hc_labels, iv_labels)
    multi_group = gfield in ('intervention', 'health_condition', 'comparison_group') and len(groups) >= 2
    hc_codes = set(spec.health_condition.dropna().astype(str)) if 'health_condition' in spec.columns else set()
    hcn = spec.health_condition.astype('string').value_counts() if 'health_condition' in spec.columns else pd.Series(dtype=int)
    has_cc = int(hcn.get('healthy_control', 0)) >= 3 and int(hcn[[c for c in hcn.index if c not in NON_DISEASE]].sum()) >= 3
    # design type
    code, label, src, help_, alts = _shape(s)
    inferred = code is None
    lstat = s.get('longitudinal_status')
    if inferred:
        src = 'inferred from the sample variables'
        if gfield == 'intervention':
            code = 'rct' if len(groups) >= 2 else 'single_arm_intervention'
            label = 'Intervention study' if len(groups) >= 2 else SHAPE_LABEL['single_arm_intervention']
        elif lstat == 'longitudinal' or (people_ok and n_people and n_rep >= max(3, 0.3 * n_people) and multi_site_people < 0.5 * n_rep):
            code = 'cohort_longitudinal'
            label = ('Longitudinal comparison of cases and healthy controls' if has_cc else
                     'Longitudinal comparison of groups' if multi_group else 'Longitudinal cohort')
        elif has_cc:
            code, label = 'case_control', 'Comparison of cases and healthy controls'
        elif multi_group:
            code, label = 'cross_sectional_groups', 'Comparison of groups'
        elif people_ok and n_rep == 0:
            code, label = 'population_survey', 'Cross-sectional sample set (one sample per person)'
        else:
            code, label = None, 'Design not stated'
        help_ = 'no design statement was read from a paper; this is what the sample variables show' if code else \
            'no design statement was read from a paper, and the sample variables do not show groups, arms or repeated sampling'
    # ---------------------------------------------------------------- sentence
    noun = STAGE_NOUN.get(stage, ('person', 'people'))
    bits = []
    if people_ok and n_people:
        who = f'<b>{_fmt(n_people)}</b> {noun[0] if n_people == 1 else noun[1]}' + (f' (person known for {100 * n_sub_s / n:.0f} % of samples)' if n_sub_s < 0.9 * n else '')
    else:
        who = f'<b>{_fmt(n)}</b> samples' + (f' from {noun[1]}' if stage else '') + ' (which samples share a person is not recorded)'
    if multi_group:
        unitp = people_ok
        parts = []
        for c_, lab, m in groups[:5]:
            v = _people(sub, m) if unitp else int(m.sum())
            parts.append(f'{E(short_label(lab))} {_fmt(v)}')
        rest = len(groups) - 5
        gtxt = '; '.join(parts) + (f'; {rest} more' if rest > 0 else '')
        kind = 'arms' if gfield == 'intervention' else 'groups'
        bits.append(f'{who} in {len(groups)} {kind} ({gtxt})')
    elif gfield == 'health_condition_single' and groups and groups[0][2].sum() >= 0.8 * n:
        bits.append(f'{who}, all healthy' if groups[0][0] == 'healthy_control' else f'{who}, all with {E(short_label(groups[0][1]).lower())}')
    else:
        bits.append(who)
    if people_ok and n_people:
        if n_rep == 0:
            bits.append('one sample per person')
        else:
            k_med = float(per[per >= 2].median())
            t = f'<b>{_fmt(n_rep)}</b> sampled more than once (median {k_med:.0f}, up to {int(per.max())} samples per person)'
            if design and design.get('fum'):
                t += f', followed for a median of {_days(design["fum"])}'
            if n_rep < 0.2 * n_people:
                t = f'mostly one sample per person; {t}'
            bits.append(t)
    if sites:
        if len(sites) == 1:
            bits.append('sampled at: ' + E(short_label(sites[0][1]).lower()))
        else:
            st = ', '.join(E(short_label(l).lower()) for _, l in sites[:4]) + (f' and {len(sites) - 4} more sites' if len(sites) > 4 else '')
            st = f'{len(site_vc)} body sites ({st})'
            if multi_site_people:
                st += f'; <b>{_fmt(multi_site_people)}</b> people sampled at more than one site'
            bits.append(st)
    if design and design.get('n_fam'):
        bits.append(f'<b>{_fmt(design["n_fam"])}</b> families' + (f' ({_fmt(design["n_mc"])} with a parent and a child)' if design.get('n_mc') else ''))
    if n_ctl:
        bits.append(f'plus {_fmt(n_ctl)} control sample{"s" if n_ctl != 1 else ""}')
    sentence = '; '.join(bits) + '.'
    sentence = sentence[0].upper() + sentence[1:] if sentence[:1].islower() else sentence
    # ---------------------------------------------------------------- diagram
    fig = ''
    if multi_group or len(sites) >= 2:
        rows = [(c_, lab) for c_, lab, _ in groups[:6]] if multi_group else [('all', 'all samples')]
        masks = {c_: m for c_, _, m in groups[:6]} if multi_group else {'all': pd.Series(True, index=spec.index).to_numpy()}
        cols = sites if sites else [('any', 'any site')]
        unit = 'people' if people_ok else 'samples'
        cell, tot = {}, {}
        for r, _ in rows:
            m = masks[r]
            tot[r] = _people(sub, m) if people_ok else int(m.sum())
            for c_, _ in cols:
                mm = m & (bs == c_).fillna(False).to_numpy(dtype=bool) if c_ != 'any' else m
                p = _people(sub, mm)
                ns = int(mm.sum())
                cell[(r, c_)] = (p if people_ok else ns, p, ns)
        rf = {'intervention': 'intervention (arm)', 'health_condition': 'health_condition', 'comparison_group': 'comparison_group'}.get(gfield, 'all samples')
        fig = matrix_svg(rows, cols, cell, tot, unit, rf, 'body_site_code')
        cap = (f'Each circle: {unit} in one group at one body site (area ∝ count; hover for people and samples)'
               + ('; dashed circles: samples without a person identifier (“s.” = samples)' if people_ok and any(p == 0 and ns > 0 for _, p, ns in cell.values()) else '') + '. '
               f'Rows come from <span class="mono">{E(rf)}</span>, columns from <span class="mono">body_site_code</span>'
               + (f'; {len(groups) - 6} smaller groups not drawn' if multi_group and len(groups) > 6 else '') + '.')
        fig = f'<div class="ds-fig">{fig}<div class="ds-cap">{cap}</div></div>'
    # ---------------------------------------------------------------- elements table
    els = []

    def row(element, field, srcs, tip, n_has, note=None):
        if not n_has:
            srcs = []
            note = None
        elif not srcs and not note:
            note = 'the archive sample record (standard field)'
        if srcs:
            fr = '; '.join(f'{E(p)}' + (f' ({100 * f:.0f} %)' if len(srcs) > 1 else '') for p, _, f in srcs)
        else:
            fr = E(note) if note else '<span class="ds-none">not recorded</span>'
        els.append(f'<tr><td>{E(element)}</td><td><span class="ds-var" title="{E(tip or field)}">{E(field)}</span></td>'
                   f'<td class="ds-from">{fr}</td><td style="white-space:nowrap">{_cov(n_has, n)}</td></tr>')

    srcs, tip = _sources(dg, 'subject_id')
    row('Person', 'subject_id', srcs, tip, n_sub_s, None if n_sub_s else 'no variable names the person')
    if 'comparison_group' in spec.columns and spec.comparison_group.notna().any():
        srcs, tip = _sources(dg, 'comparison_group')
        row("Study's comparison group", 'comparison_group', srcs, tip, int(spec.comparison_group.notna().sum()))
    if gfield == 'intervention':
        srcs, tip = _sources(dg, 'intervention')
        row('Arm', 'intervention', srcs, tip, int(spec.intervention.notna().sum()))
    hcs_n = int((spec.health_condition.notna() & (spec.health_condition != 'unknown')).sum()) if 'health_condition' in spec.columns else 0
    srcs, tip = _sources(dg, 'health_condition')
    row('Group / condition', 'health_condition', srcs, tip, hcs_n)
    bas = spec.body_site_basis.astype('string') if 'body_site_basis' in spec.columns else None
    if bas is not None and bas.notna().any():
        bvc = bas.value_counts()
        tot_b = int(bvc.sum())
        bvc = bvc[bvc.index.isin(['sample_attribute', 'study_single_site'])]
        tot_b = int(bvc.sum()) or 1
        bsrc = [({'sample_attribute': "each sample's own record", 'study_single_site': "the project's single stated site"}[k], int(v), v / tot_b) for k, v in bvc.head(2).items()]
    else:
        bsrc = []
    row('Body site', 'body_site_code', bsrc, 'body_site_code: from each sample record, or the project-level site when the project has a single one', int(bs.notna().sum()))
    for f, el in (('age_at_collection_days', 'Age at sampling'), ('timepoint_label', 'Time point'), ('collection_date', 'Collection date')):
        if f in spec.columns and spec[f].notna().any():
            srcs, tip = _sources(dg, f)
            row(el, f, srcs, tip, int(spec[f].notna().sum()))
    if 'family_id' in spec.columns and spec.family_id.notna().any():
        srcs, tip = _sources(dg, 'family_id')
        row('Family', 'family_id', srcs, tip, int(spec.family_id.notna().sum()))
    table = ('<table class="ds-el"><tr><th>Design element</th><th>Field</th><th>Comes from</th><th>Samples</th></tr>' + ''.join(els) + '</table>')
    # ---------------------------------------------------------------- checks
    ck = []
    npart = s.get('n_participants')
    if _null(npart):
        npart = s.get('design_n_participants_stated')
    if not _null(npart):
        npart = int(round(float(npart)))
        if npart == 1:
            ck.append(('ok' if (not people_ok or n_people <= 1) else 'warn',
                       'The paper describes a single participant; every sample comes from that person.' if (not people_ok or n_people <= 1) else
                       f'The paper describes a single participant, but the samples name {_fmt(n_people)} different people.'))
        elif not people_ok:
            ck.append(('warn', f'The paper states {_fmt(npart)} participants, but no variable says which samples came from the same person, so the samples cannot be grouped by participant.'))
        elif n_people == npart:
            ck.append(('ok', f'Participants: paper {_fmt(npart)} · distinct people in the samples {_fmt(n_people)}.'))
        else:
            d = n_people - npart
            why = ('fewer people were sequenced than enrolled, or the person identifier is missing for part of the samples' if d < 0 else
                   'the identifier may name something finer than a person (a sample or a visit), or the deposit includes people outside the paper')
            ck.append(('warn', f'Participants: paper {_fmt(npart)} · distinct people in the samples {_fmt(n_people)} ({"+" if d > 0 else ""}{d:,}); {why}.'))
    shape_codes = set(alts) | ({code} if code else set())
    if not inferred and (shape_codes & REPEATED_SHAPES or s.get('longitudinal_design') == 'yes'):
        if not people_ok:
            ck.append(('warn', 'The source describes repeated sampling, but which samples belong to the same person is not recorded, so the time course cannot be drawn.'))
        elif n_rep == 0:
            ck.append(('warn', 'The source describes repeated sampling, but no person has more than one sample in the deposited records.'))
        else:
            ck.append(('ok', f'Repeated sampling: {_fmt(n_rep)} people have two or more samples.'))
    if not inferred and shape_codes & GROUP_SHAPES:
        if 'case_control' in shape_codes and has_cc:
            ck.append(('ok', 'Cases and healthy controls are told apart by each sample’s health_condition.'))
        elif not multi_group:
            alt = []
            if 'case_control' not in shape_codes:
                for f_, nm_ in (('country', 'country'), ('age_category', 'age_category'), ('lifestyle', 'lifestyle'), ('diet', 'diet'), ('location_region', 'location_region')):
                    if f_ in spec.columns:
                        v_ = spec[f_].astype('string')
                        v_ = v_[v_.notna() & (v_ != 'unknown')].value_counts()
                        if (v_ >= 3).sum() >= 2:
                            alt.append(f'{nm_} ({int((v_ >= 3).sum())} values)')
            if alt:
                ck.append(('info', 'The source compares groups; health_condition does not separate them, but the samples differ by ' + ', '.join(alt[:3]) + ', which may define them.'))
            else:
                ck.append(('warn', 'The source compares groups, but no sample variable says which group each sample belongs to' + (f' (every sample with a value reads “{E(short_label(groups[0][1]))}”)' if groups else '') + '.'))
        else:
            ck.append(('ok', f'Groups are told apart by each sample’s {"intervention arm" if gfield == "intervention" else gfield} ({len(groups)} groups).'))
    if not inferred and code == 'case_series' and has_cc:
        ck.append(('warn', f'Classified as a single-group study, but {_fmt(int(hcn.get("healthy_control", 0)))} samples are labelled healthy controls.'))
    dz = s.get('intervention_design')
    if not _null(dz) and dz in TRIAL_DESIGNS:
        if ivarms and ivarms.get('resolved'):
            ck.append(('ok', f'Intervention arms: every sample is assigned ({100 * ivarms.get("cov", 0):.0f} % carry an arm value).'))
        else:
            c = 100 * (ivarms or {}).get('cov', 0)
            ck.append(('warn', f'The study gave an intervention ({str(dz).replace("_", " ")}), but only {c:.0f} % of samples say which arm they belong to.'))
    if bas is not None and bas.notna().any() and (bas == 'study_single_site').mean() > 0.5:
        ck.append(('info', 'Body site is the project’s single stated site; the samples themselves do not record it.'))
    if design and design.get('rel') and design.get('rel_res') is False:
        ck.append(('warn', 'The source describes related or co-located people, but which samples belong to which family or setting is not recorded.'))
    if n_ctl:
        ck.append(('ok', f'{_fmt(n_ctl)} control sample{"s are" if n_ctl != 1 else " is"} marked and left out of the counts above.'))
    checks = ''
    if ck:
        sym = {'ok': '✓', 'warn': '!', 'info': 'i'}
        checks = ('<div class="ds-checks-h">Paper and data checks</div><ul class="ds-checks">' +
                  ''.join(f'<li><span class="ds-ck {k}">{sym[k]}</span><span>{t}</span></li>' for k, t in ck) + '</ul>')
    stmt = s.get('design_statement')
    chk = s.get('design_statement_check')
    chk_txt = {'supported': '; an Opus 5 check found every fact in the text', 'revised': '; an Opus 5 check removed details the text does not state'}.get(chk, '')
    stmt_html = (f'<p class="ds-stmt">{E(str(stmt))} <span class="mlabel" title="Written by a language model from the {E(str(s.get("design_text_mode") or "source"))} text; '
                 f'two independent readings agreed on the design type{chk_txt}">machine-written{", checked" if chk_txt else ""}</span></p>') if not _null(stmt) else ''
    head = (f'<div class="ds-head"><span class="ds-type">{E(label)}</span>' + (f'<span class="ds-src" title="Where the design type comes from">{E(src)}</span>' if src else '') + '</div>'
            + (f'<div class="ds-help">{E(help_)}</div>' if help_ else ''))
    if fig and checks:
        top_ = f'<div class="ds-grid">{fig}<div>{checks}</div></div>'
    else:
        top_ = fig or checks
    more = (f'<details class="ds-more" open><summary>Which variables the design is drawn from</summary>{table}</details>')
    link = '<p class="small muted" style="margin:.5rem 0 0"><a href="../about/value_sources.html#design-block">How this block is assembled</a></p>'
    grp_html = ''
    dgj = s.get('design_groups')
    if not _null(dgj) and not _null(stmt):
        try:
            import json as _json
            dgl = [x for x in _json.loads(dgj) if isinstance(x, dict) and x.get('label')][:8]
        except ValueError:
            dgl = []
        if len(dgl) >= 2:
            grp_html = ('<p class="ds-grp"><span class="ds-inlab">Groups the source compares:</span> ' +
                        ' · '.join(E(str(x['label'])[:60]) + (f' <span class="muted">(n = {int(x["n_people_stated"]):,})</span>' if isinstance(x.get('n_people_stated'), int) else '') for x in dgl) + '</p>')
    stmt_html += grp_html
    data_lab = '<span class="ds-inlab">In the data:</span> ' if stmt_html else ''
    return f'<div class="ds-card">{head}{stmt_html}<p class="ds-sent">{data_lab}{sentence}</p>{top_}{more}{link}</div>'


def letters(s, g, ivarms=None):
    """Extra project-table markers: C (cases and healthy controls) and S (same people at several body sites).
    Value 2 = resolved in the sample variables, 1 = described by the source but not resolved."""
    out = {}
    spec = g[g.sample_type.fillna('specimen') == 'specimen'] if 'sample_type' in g.columns else g
    if not len(spec):
        return out
    hcn = spec.health_condition.astype('string').value_counts() if 'health_condition' in spec.columns else pd.Series(dtype=int)
    has_cc = int(hcn.get('healthy_control', 0)) >= 3 and int(hcn[[c for c in hcn.index if c not in NON_DISEASE]].sum()) >= 3
    sh = {s.get('study_shape'), *(str(s.get('study_shape_classification') or '').replace('suggested:', '').split('|'))}
    if has_cc:
        out['cc'] = 2
    elif 'case_control' in sh:
        out['cc'] = 1
    if 'body_site_code' in spec.columns:
        bs = spec.body_site_code.astype('string')
        bs = bs.where(bs != 'unknown_site')
        vc = bs.dropna().value_counts()
        if (vc >= 3).sum() >= 2:
            sub = spec.subject_id.astype('string') if 'subject_id' in spec.columns else None
            k = pd.DataFrame({'p': sub, 'b': bs}).dropna().groupby('p').b.nunique() if sub is not None else pd.Series(dtype=int)
            m = int((k >= 2).sum())
            if m >= 3:
                out['ms'] = 2
                out['msn'] = m
            elif sub is None or sub.notna().mean() < 0.5:
                out['ms'] = 1
    return out
