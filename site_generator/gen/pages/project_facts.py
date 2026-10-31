"""Project-page blocks: the methods strip, the small body pictogram with its site list, the living-conditions table and
the per-project facts they are drawn from (paper reading + per-sample values). A block renders nothing when the project
has no facts for it.
"""
from __future__ import annotations

import html

import pandas as pd

E = html.escape

# marker positions on a 64 x 112 pictogram; the label is shown in the list beside the figure
SPOTS = {
    "eye_ear": (26.5, 10, "Eye / ear"), "nasal_nasopharyngeal": (32, 12.5, "Nose / nasopharynx"),
    "oral": (32, 16.5, "Mouth / throat"), "respiratory_lower": (32, 32, "Airway / lung"),
    "milk": (26, 37, "Breast milk"), "blood_tissue": (13.8, 42, "Blood"), "gut_stool": (32, 48, "Gut / stool"),
    "vaginal_urogenital": (32, 60, "Urogenital"), "skin": (50.2, 54, "Skin"),
}
ORDER = list(SPOTS)
OTHER_SITES = {"other_site": "Other site", "multi_site": "Several sites", "unknown_site": "Site not stated"}


def body_figure(site_counts: dict, total: int) -> str:
    """site_counts: body_site_code -> number of samples."""
    pres = [c for c in ORDER if site_counts.get(c)]
    if not pres and not any(site_counts.get(c) for c in OTHER_SITES):
        return ""
    fig = ['<svg viewBox="0 0 64 112" class="bs-fig" role="img" aria-label="Body sites sampled in this project">'
           '<g fill="#E4E1D8">',
           '<circle cx="32" cy="11" r="8.5"/><rect x="19" y="22.5" width="26" height="38" rx="7"/>',
           '<rect x="10" y="23.5" width="7.6" height="34" rx="3.8"/><rect x="46.4" y="23.5" width="7.6" height="34" rx="3.8"/>',
           '<rect x="20" y="58" width="11.4" height="50" rx="5"/><rect x="32.6" y="58" width="11.4" height="50" rx="5"/></g>']
    for c in pres:
        x, y, _ = SPOTS[c]
        fig.append(f'<circle cx="{x}" cy="{y}" r="3.4" fill="#A88B4A" stroke="#fff" stroke-width="1.1"/>')
    fig.append("</svg>")
    items = []
    for c in pres:
        n = site_counts[c]
        items.append(f'<li><svg class="bs-dot" viewBox="0 0 8 8" width="8" height="8"><circle cx="4" cy="4" r="3.5" '
                     f'fill="#A88B4A"/></svg><span class="bs-name">{E(SPOTS[c][2])}</span>'
                     f'<span class="bs-det">{n:,} sample{"s" if n != 1 else ""}'
                     f'{f" · {100 * n / total:.0f}&#160;%" if total else ""}</span></li>')
    for c, lab in OTHER_SITES.items():
        n = site_counts.get(c)
        if n:
            items.append(f'<li><svg class="bs-dot" viewBox="0 0 8 8" width="8" height="8"><circle cx="4" cy="4" r="3.5" '
                         f'fill="#C9C4B4"/></svg><span class="bs-name">{E(lab)}</span>'
                         f'<span class="bs-det">{n:,} sample{"s" if n != 1 else ""}{f" · {100 * n / total:.0f}&#160;%" if total else ""}</span></li>')
    return f'<div class="bs">{"".join(fig)}<ul class="bs-list">{"".join(items)}</ul></div>'


RECRUIT = {"community": "community / population sample", "outpatient_clinic": "outpatient clinic",
           "hospital_inpatient": "hospital inpatients", "icu": "intensive care", "birth_cohort": "birth cohort",
           "school_or_daycare": "school / daycare", "occupational": "workplace", "volunteers": "volunteers",
           "blood_donors": "blood donors", "not_stated": "not stated"}
SETTLEMENT = {"urban": "urban", "peri_urban": "peri-urban", "rural": "rural", "mixed": "urban and rural",
              "not_stated": "not stated"}
NA_LABEL = {"dna": "DNA", "rna": "RNA", "both": "DNA and RNA", "conflict": "DNA or RNA unresolved", "unclear": "not recorded"}


def _present(v):
    try:
        return v is not None and not pd.isna(v) and str(v) != ""
    except (TypeError, ValueError):
        return v is not None


def facts_of(row) -> dict:
    """The methods strip: three core fields plus chips for everything else the paper states."""
    g = lambda k: row.get(k) if _present(row.get(k)) else None
    f = {"platform": g("platform") or g("platform_paper"), "extraction_kit": g("extraction_kit"), "library_kit": g("library_kit")}
    layout = g("library_layouts") or g("library_layout")
    if layout:
        f["seq_note"] = str(layout).replace(";", ", ")
    extras = []
    na = g("nucleic_acid")
    if na in ("rna", "both"):
        extras.append(("RNA data set" if na == "rna" else "DNA and RNA data sets", False, None))
    if g("rna_methods"):
        extras.append(("RNA methods stated in the paper", False, "rRNA depletion / DNase / strandedness"))
    for code in str(g("processing") or "").split(";"):
        if code and code != "none":
            extras.append((code.replace("_", " "), code in ("wga_mda", "virome_vlp"),
                           "sample processing before sequencing, from the project classification"))
    acc = g("metadata_access_paper")
    if acc and acc not in ("not_stated", "default", "public"):
        extras.append((f"metadata access: {str(acc).replace('_', ' ')}", True,
                       "how the participant metadata can be obtained, as stated by the paper"))
    if g("nucleic_acid_conflict"):
        extras.append(("archive and paper disagree on DNA / RNA", True,
                       "the project is listed under neither DNA nor RNA until the conflict is resolved"))
    f["extras"] = extras
    return f


def living_of(row):
    """Rows of the living-conditions table; [] when the paper reading gave none of them."""
    g = lambda k: row.get(k) if _present(row.get(k)) else None
    rows = []
    r = g("recruitment")
    rows.append(("Recruitment setting", RECRUIT.get(r, r) if r and r != "not_stated" else None, "cohort", "paper, methods"))
    st = g("settlement")
    rows.append(("Settlement type", SETTLEMENT.get(st, st) if st and st != "not_stated" else None, "place", "paper, methods"))
    np_ = g("n_places")
    try:
        np_ = int(float(np_)) if np_ is not None else 0
    except (TypeError, ValueError):
        np_ = 0
    rows.append(("Places named below country", f"{np_} place{'s' if np_ != 1 else ''}" if np_ > 0 else None, "place", "paper, methods"))
    ls = g("lifestyle_paper")
    rows.append(("Lifestyle", str(ls).replace("_", " ") if ls and ls not in ("unknown", "not_stated") else None,
                 "cohort", "paper, checked against the lifestyle vocabulary"))
    nx = g("n_exposure_items")
    try:
        nx = int(float(nx)) if nx is not None else 0
    except (TypeError, ValueError):
        nx = 0
    rows.append(("Routine faecal–oral exposure items", f"{nx} recorded" if nx > 0 else None, "cohort", "paper, with a quote per item"))
    return rows if any(v for _, v, _, _ in rows) else []


def methods_strip(f: dict) -> str:
    """Three core fields — extraction, library prep, sequencing — with everything else as chips."""
    if not f:
        return ""
    core = [("DNA / RNA extraction", f.get("extraction_kit"), f.get("extraction_note"), f.get("extraction_quote")),
            ("Library preparation", f.get("library_kit"), f.get("library_note"), f.get("library_quote")),
            ("Sequencing", f.get("platform"), f.get("seq_note"), f.get("platform_quote"))]
    if not any(v for _, v, _, _ in core):
        return ""
    cells = "".join(
        f'<div><div class="k">{E(k)}</div><div class="v{"" if v else " nr"}"'
        f'{f" title={chr(34)}{E(str(q))}{chr(34)}" if q else ""}>{E(str(v)) if v else "not reported"}</div>'
        f'<div class="s">{E(str(s)) if s else ""}</div></div>' for k, v, s, q in core)
    chips = "".join(f'<span class="x{" warn" if w else ""}" title="{E(str(t2 or ""))}">{E(str(t))}</span>'
                    for t, w, t2 in f.get("extras") or [])
    return f'<div class="mstrip">{cells}</div>{f"<div class=mextra>{chips}</div>" if chips else ""}'


def living_table(rows) -> str:
    """rows: (field, value_html, scale, source) — scale says what the value describes."""
    if not rows:
        return ""
    body = "".join(
        f'<tr><td>{E(str(fl))}</td><td>{v if v else "<span class=pend>not stated by this project</span>"}</td>'
        f'<td><span class="scale {sc}">{E(str(sc))}</span></td><td class="muted">{E(str(sr or ""))}</td></tr>'
        for fl, v, sc, sr in rows)
    return ('<table class="livtbl"><tr><th>Field</th><th>Value</th><th>Scale</th><th>From</th></tr>' + body +
            '</table><p class="small muted" style="margin:.35rem 0 0">Scale says what the value describes: this '
            'person, a group or arm, the whole cohort, or the sampling place.</p>')


CSS = """
.mstrip{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));border:1px solid #e3e3e3;border-radius:10px;margin:.55rem 0 .3rem;background:#fff}
.mstrip>div{padding:.5rem .8rem;border-left:1px solid #eee}.mstrip>div:first-child{border-left:0}
.mstrip .k{font-size:.66rem;text-transform:uppercase;letter-spacing:.07em;color:#5f6365;font-weight:600}
.mstrip .v{font-size:.92rem;font-weight:600;margin-top:.1rem}.mstrip .v.nr{color:#9a9a9a;font-weight:400;font-style:italic}
.mstrip .s{font-size:.76rem;color:#5f6365;margin-top:.1rem}
.mextra{font-size:.78rem;color:#444;margin:.1rem 0 .3rem}
.mextra .x{display:inline-block;padding:.05rem .5rem;margin:.1rem .25rem .1rem 0;border-radius:999px;background:#efe4c4;border:1px solid #d9c896;color:#4a3c14}
.mextra .x.warn{background:#fff6e5;border-color:#e0b25b;color:#7a4b00}
.v2-glance{display:grid;grid-template-columns:230px minmax(0,1fr);gap:1.6rem;align-items:start}
.v2-glance .dists{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:.6rem 1.6rem}
.bs{display:flex;gap:.8rem;align-items:flex-start}.bs-fig{width:46px;height:80px;flex:none}
.bs-list{list-style:none;margin:0;padding:.1rem 0 0;font-size:.8rem;line-height:1.3}
.bs-list li{margin:0 0 .45rem;display:grid;grid-template-columns:.7rem 1fr;column-gap:.3rem}
.bs-dot{margin-top:.3rem}.bs-name{font-weight:600}.bs-det{grid-column:2;color:#5f6365;font-size:.76rem}
.livtbl{width:100%;border-collapse:collapse;font-size:.82rem}
.livtbl td,.livtbl th{padding:.28rem .4rem;border-bottom:1px solid #f0f0ee;text-align:left;vertical-align:top}
.livtbl th{font-size:.66rem;text-transform:uppercase;letter-spacing:.06em;color:#5f6365}
.scale{font-size:.68rem;padding:.02rem .42rem;border-radius:999px;border:1px solid #ccc;white-space:nowrap}
.scale.person{border-color:#1a7f37;color:#1a7f37}.scale.group{border-color:#2f4b7c;color:#2f4b7c}
.scale.cohort{border-color:#7a5c00;color:#7a5c00}.scale.place{border-color:#555;color:#555}
.pend{color:#9a9a9a;font-style:italic}
.nab{display:inline-block;font-size:.7rem;padding:.04rem .5rem;border-radius:999px;border:1px solid #bbb;margin-left:.35rem;vertical-align:middle}
.nab.dna{border-color:#2f6b3f;color:#2f6b3f;background:#f2f8f3}.nab.rna{border-color:#8a4b8a;color:#8a4b8a;background:#faf3fa}
.nab.conflict{border-color:#b3261e;color:#b3261e;background:#fdf3f2}
"""
