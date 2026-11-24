#!/usr/bin/env python
"""svg_png.py — rasterise the SVGs the headless test wrote, so the drawing can be looked at.

The replication page's own marks carry their colours as attributes; only the shared bar chart (sp_core.js)
takes its colours from static/side_pages.css, which CairoSVG does not load, so those are written in as attributes.
This is a check of the drawing, not a screenshot of the page.

usage: python tests/svg_png.py tests/out <png dir>
"""
import sys
from pathlib import Path

import cairosvg

# presentation attributes for the shared bar chart's classes (CairoSVG does not apply class rules reliably)
INLINE = {
    'class="sp-bar-bg"': 'class="sp-bar-bg" fill="#f2f2ef"',
    'class="sp-bar-label"': 'class="sp-bar-label" fill="#333333" font-size="11"',
    'class="sp-bar-val"': 'class="sp-bar-val" fill="#555555" font-size="10.5"',
    'class="sp-axis-title"': 'class="sp-axis-title" fill="#6a6a6a" font-size="10.5"',
}
STYLE = """<style type="text/css"><![CDATA[ text { font-family: 'Helvetica', 'DejaVu Sans', sans-serif; } ]]></style>"""


def main():
    src, dst = Path(sys.argv[1]), Path(sys.argv[2])
    dst.mkdir(parents=True, exist_ok=True)
    for f in sorted(src.glob("*.svg")):
        s = f.read_text()
        for k, v in INLINE.items():
            s = s.replace(k, v)
        i = s.index(">") + 1
        cairosvg.svg2png(bytestring=(s[:i] + STYLE + s[i:]).encode(), write_to=str(dst / (f.stem + ".png")), scale=2,
                         background_color="white")
        print(f.stem + ".png")


if __name__ == "__main__":
    main()
