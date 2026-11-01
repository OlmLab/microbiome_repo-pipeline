"""Project-page 'Samples at a glance' charts (R2026.32 redesign, owner review 2026-10-31).

year_chart   — collection year as labelled columns: every column carries its year and its sample count when there is room,
               otherwise the years are labelled at a regular interval; empty years keep a faint baseline mark.
depth_chart  — sequencing depth per sample on a LINEAR axis: this project's samples only, in 8–12 bins of a round width up
               to the 95th percentile, one overflow column for anything deeper, a labelled median line, and the catalog-wide
               comparison as a sentence instead of an overlaid histogram.
Both return '' when there is nothing to draw, and plain text for a single value.
"""
from __future__ import annotations

import bisect
import html
import math

E = html.escape


def _compact(n: int) -> str:
    n = int(n)
    if n >= 10000:
        return f"{n / 1000:.0f}k"
    if n >= 1000:
        return f"{n / 1000:.1f}k".replace(".0k", "k")
    return f"{n}"


def _num(v: float) -> str:
    """Tick / value label with at most 2 significant digits after the leading ones (0.25, 1.5, 12, 300)."""
    if v == 0:
        return "0"
    if v >= 100:
        return f"{v:,.0f}"
    if v >= 10:
        return f"{v:.0f}" if abs(v - round(v)) < 1e-9 else f"{v:.1f}"
    s = f"{v:.2g}" if v < 1 else f"{v:.2f}".rstrip("0").rstrip(".")
    return s


def year_chart(years, n_total: int, width: int = 300, height: int = 54) -> str:
    """years: [(year, n), ...] over a contiguous range (zeros included). Returns SVG + caption HTML."""
    years = [(int(y), int(n)) for y, n in years]
    dated = sum(n for _, n in years)
    if not dated:
        return ""
    nz = [(y, n) for y, n in years if n]
    if len(nz) == 1:
        y, n = nz[0]
        return (f'<p class="glance-one">All {n:,} dated sample{"s" if n != 1 else ""} were collected in <b>{y}</b>.</p>'
                + _dated_note(dated, n_total))
    nb = len(years)
    top, bottom = 14, 15
    bw = width / nb
    gap = min(3.0, bw * 0.18)
    m = max(n for _, n in years)
    show_counts = bw >= 22
    # year labels: every column when they fit, else every k-th year (k in 2, 5, 10, 20) aligned to round years
    k = next((k for k in (1, 2, 5, 10, 20, 50) if bw * k >= 34), 50)
    short = False
    out = [f'<svg class="gchart years" viewBox="0 0 {width} {height + top + bottom}" width="{width}" height="{height + top + bottom}" role="img" '
           f'aria-label="Samples per collection year, {years[0][0]} to {years[-1][0]}">']
    for i, (y, n) in enumerate(years):
        x = i * bw + gap / 2
        w = max(1.0, bw - gap)
        if n:
            h = max(2.0, height * n / m)
            out.append(f'<rect class="gc-bar" fill="#CFB87C" x="{x:.1f}" y="{top + height - h:.1f}" width="{w:.1f}" height="{h:.1f}"><title>{y}: {n:,} sample{"s" if n != 1 else ""}</title></rect>')
            if show_counts or n == m:
                out.append(f'<text class="gc-n halo" font-size="9.5" fill="#5F6365" stroke="#FFFFFF" stroke-width="3" stroke-linejoin="round" aria-hidden="true" x="{x + w / 2:.1f}" y="{top + height - h - 3:.1f}" text-anchor="middle">{_compact(n)}</text>')
                out.append(f'<text class="gc-n" font-size="9.5" fill="#5F6365" x="{x + w / 2:.1f}" y="{top + height - h - 3:.1f}" text-anchor="middle">{_compact(n)}</text>')
        else:
            out.append(f'<rect class="gc-zero" fill="#D6D6D6" x="{x:.1f}" y="{top + height - 1:.1f}" width="{w:.1f}" height="1"><title>{y}: no samples</title></rect>')
        if y % k == 0 or k == 1:
            out.append(f'<text class="gc-x" font-size="10" fill="#5F6365" x="{x + w / 2:.1f}" y="{top + height + 12:.1f}" text-anchor="middle">{("’" + str(y)[2:]) if short else y}</text>')
    out.append(f'<line class="gc-base" stroke="#BDBDBD" stroke-width="1" x1="0" x2="{width}" y1="{top + height + 0.5}" y2="{top + height + 0.5}"/></svg>')
    return "".join(out) + _dated_note(dated, n_total)


def _dated_note(dated: int, n_total: int) -> str:
    if not n_total or dated >= n_total:
        return ""
    return f'<div class="gc-cap">{dated:,} of {n_total:,} samples carry a collection date.</div>'


def _nice_step(raw: float) -> float:
    if raw <= 0:
        return 1.0
    e = math.floor(math.log10(raw))
    for mlt in (1, 2, 2.5, 5, 10):
        if mlt * 10 ** e >= raw:
            return mlt * 10 ** e
    return 10 ** (e + 1)


def depth_chart(gbp, repo_sorted=None, width: int = 300, height: int = 54) -> str:
    """gbp: per-sample depth in Gbp (> 0) of this project; repo_sorted: all catalog samples' depth, sorted (Gbp)."""
    vals = sorted(float(v) for v in gbp if v and v > 0)
    if not vals:
        return ""
    n = len(vals)
    med = vals[n // 2] if n % 2 else (vals[n // 2 - 1] + vals[n // 2]) / 2
    q98 = vals[min(n - 1, int(math.ceil(0.95 * n)) - 1)]   # axis cap: 95th percentile; deeper samples go to the last column
    unit, f = ("Mbp", 1000.0) if q98 < 0.1 else ("Gbp", 1.0)
    cmp_txt = ""
    if repo_sorted is not None and len(repo_sorted):
        rs = repo_sorted
        cm = rs[len(rs) // 2]
        pct = 100.0 * bisect.bisect_left(rs, med) / len(rs)
        cu, cf = ("Mbp", 1000.0) if cm < 0.1 else ("Gbp", 1.0)
        cmp_txt = f" · catalog median {_num(cm * cf)} {cu} ({pct:.0f} % of all catalog samples are shallower than this project's median)"
    med_txt = f"{_num(med * f)} {unit}"
    if n <= 5:   # too few samples for a histogram: list them
        cap = cmp_txt.lstrip(" ·")
        cap = cap[:1].upper() + cap[1:]
        if n == 1:
            return f'<p class="glance-one">One sample, sequenced to <b>{med_txt}</b>.</p><div class="gc-cap">{cap}</div>'
        lst = ", ".join(_num(x * f) for x in vals)
        return (f'<p class="glance-one">{n} samples, sequenced to <b>{lst} {unit}</b> (median {med_txt}).</p>'
                f'<div class="gc-cap">{cap}</div>')
    v = [x * f for x in vals]
    hi = q98 * f
    if hi <= 0:
        hi = v[-1]
    step = _nice_step(hi / 10)
    upper = step * math.ceil(hi / step - 1e-9)
    if upper <= 0:
        upper = step
    nb = max(1, int(round(upper / step)))
    counts = [0] * nb
    over = 0
    for x in v:
        if x > upper:
            over += 1
        else:
            counts[min(nb - 1, int(x / step))] += 1
    top, bottom = 27, 15
    ow = 0 if not over else max(18.0, width / (nb + 1))
    pw = width - ow - (6 if over else 0)
    bw = pw / nb
    gap = min(2.0, bw * 0.15)
    m = max(counts + [over])
    sx = lambda x: x / upper * pw
    out = [f'<svg class="gchart depth" viewBox="0 0 {width} {height + top + bottom}" width="{width}" height="{height + top + bottom}" role="img" '
           f'aria-label="Sequencing depth per sample, linear scale in {unit}">']
    for i, c in enumerate(counts):
        if not c:
            continue
        h = max(2.0, height * c / m)
        x = i * bw + gap / 2
        lo, up = i * step, (i + 1) * step
        out.append(f'<rect class="gc-bar" fill="#CFB87C" x="{x:.1f}" y="{top + height - h:.1f}" width="{max(1.0, bw - gap):.1f}" height="{h:.1f}"><title>{_num(lo)}–{_num(up)} {unit}: '
                   f'{c:,} sample{"s" if c != 1 else ""}</title></rect>')
        if c == m or bw >= 22:
            out.append(f'<text class="gc-n halo" font-size="9.5" fill="#5F6365" stroke="#FFFFFF" stroke-width="3" stroke-linejoin="round" aria-hidden="true" x="{x + (bw - gap) / 2:.1f}" y="{top + height - h - 3:.1f}" text-anchor="middle">{_compact(c)}</text>')
            out.append(f'<text class="gc-n" font-size="9.5" fill="#5F6365" x="{x + (bw - gap) / 2:.1f}" y="{top + height - h - 3:.1f}" text-anchor="middle">{_compact(c)}</text>')
    if over:
        h = max(2.0, height * over / m)
        x = width - ow
        out.append(f'<rect class="gc-bar gc-over" fill="#E6DCC0" x="{x:.1f}" y="{top + height - h:.1f}" width="{ow - 1:.1f}" height="{h:.1f}"><title>deeper than {_num(upper)} {unit}: '
                   f'{over:,} sample{"s" if over != 1 else ""} (up to {_num(v[-1])} {unit})</title></rect>')
        out.append(f'<text class="gc-n halo" font-size="9.5" fill="#5F6365" stroke="#FFFFFF" stroke-width="3" stroke-linejoin="round" aria-hidden="true" x="{x + (ow - 1) / 2:.1f}" y="{top + height - h - 3:.1f}" text-anchor="middle">{_compact(over)}</text>')
        out.append(f'<text class="gc-n" font-size="9.5" fill="#5F6365" x="{x + (ow - 1) / 2:.1f}" y="{top + height - h - 3:.1f}" text-anchor="middle">{_compact(over)}</text>')
        out.append(f'<text class="gc-x" font-size="10" fill="#5F6365" x="{x + (ow - 1) / 2:.1f}" y="{top + height + 12:.1f}" text-anchor="middle">&gt;</text>')
    # x ticks: 0, every te-th bin edge, the upper edge (with the unit)
    te = next((k for k in (1, 2, 3, 4, 5, 6) if nb / k <= 4), 6)   # 3-5 labelled ticks on the bin grid
    ticks = [j * step for j in range(0, nb + 1, te)]
    if abs(ticks[-1] - upper) > 1e-9:
        if sx(upper) - sx(ticks[-1]) < 30:
            ticks = ticks[:-1]
        ticks.append(upper)
    for t in ticks:
        anchor = "start" if t == 0 else ("end" if t == upper else "middle")
        out.append(f'<text class="gc-x" font-size="10" fill="#5F6365" x="{sx(t):.1f}" y="{top + height + 12:.1f}" text-anchor="{anchor}">{_num(t)}</text>')
        out.append(f'<line class="gc-tick" stroke="#9A9A9A" stroke-width="1" x1="{sx(t):.1f}" x2="{sx(t):.1f}" y1="{top + height}" y2="{top + height + 3}"/>')
    mx = sx(min(med * f, upper))
    anchor = "start" if mx < 40 else ("end" if mx > pw - 40 else "middle")
    out.append(f'<line class="gc-med" stroke="#333333" stroke-width="1.4" stroke-dasharray="3 2" x1="{mx:.1f}" x2="{mx:.1f}" y1="13" y2="{top + height}"><title>median {med_txt} per sample</title></line>')
    out.append(f'<text class="gc-m halo" font-size="10" font-weight="600" fill="#333333" stroke="#FFFFFF" stroke-width="3" stroke-linejoin="round" aria-hidden="true" x="{mx + (3 if anchor == "start" else -3 if anchor == "end" else 0):.1f}" y="10" text-anchor="{anchor}">median {med_txt}</text>')
    out.append(f'<text class="gc-m" font-size="10" font-weight="600" fill="#333333" x="{mx + (3 if anchor == "start" else -3 if anchor == "end" else 0):.1f}" y="10" text-anchor="{anchor}">median {med_txt}</text>')
    out.append(f'<line class="gc-base" stroke="#BDBDBD" stroke-width="1" x1="0" x2="{width}" y1="{top + height + 0.5}" y2="{top + height + 0.5}"/></svg>')
    over_txt = f" · last column: {over:,} sample{'s' if over != 1 else ''} deeper than {_num(upper)} {unit}" if over else ""
    return "".join(out) + f'<div class="gc-cap">{n:,} samples · linear axis in {unit}{over_txt}{cmp_txt}</div>'


CSS = """
/* R2026.32 glance charts: labelled columns, linear depth axis. Class names are gc-* only: a bare .bar rule elsewhere in
   site.css sets width/height, which browsers apply to SVG <rect> elements (SVG 2 geometry properties). */
.sparks{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,320px));gap:1.2rem 2.4rem;margin-top:.8rem}
svg.gchart{display:block;max-width:100%;height:auto;overflow:visible}
svg.gchart rect.gc-bar{fill:var(--cu-gold,#CFB87C)}svg.gchart rect.gc-bar:hover{fill:#7A6A3C}
svg.gchart rect.gc-bar.gc-over{fill:#e6dcc0}svg.gchart rect.gc-zero{fill:#d6d6d6}
svg.gchart line.gc-base{stroke:#bdbdbd;stroke-width:1}svg.gchart line.gc-tick{stroke:#9a9a9a;stroke-width:1}
svg.gchart line.gc-med{stroke:#333;stroke-width:1.4;stroke-dasharray:3 2}
svg.gchart text{font-family:inherit;fill:#4a4a4a}svg.gchart .gc-x{font-size:10px;fill:#5f6365}
svg.gchart .gc-n{font-size:9.5px;fill:#5f6365;font-variant-numeric:tabular-nums}svg.gchart .gc-m{font-size:10px;font-weight:600;fill:#333}
svg.gchart .halo{fill:#fff;stroke:#fff}
.gc-cap{font-size:.74rem;color:#5f6365;margin-top:.15rem;max-width:320px;line-height:1.35}
.glance-one{font-size:.82rem;margin:.1rem 0 .1rem}
"""
