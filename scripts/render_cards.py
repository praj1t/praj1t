#!/usr/bin/env python3
"""Render the animated SVG cards for the GitHub profile README.

    python scripts/render_cards.py            # animated cards -> cards/, README block refreshed
    STATIC=1 python scripts/render_cards.py   # final frame only -> preview/ (cards + index.html)

Every card is an SVG that GitHub shows through an <img> tag, so only features that
work in that context are used: an inline <style> block (CSS keyframes), SMIL
<animate>/<animateTransform>, and fonts embedded as base64 WOFF data URIs. No scripts,
no external requests, no foreignObject. Each embedded font is cut down with pyftsubset to
the glyphs that card actually draws.

Animations are staggered with a global timeline so the page plays top to bottom, and all
of them are switched off under `prefers-reduced-motion: reduce`.

Input : data/github.json (refreshed daily by the workflow), assets/portrait.png (cut-out photo)
Output: cards/*.svg and the block between the cards markers in README.md
"""
from __future__ import annotations

import base64
import colorsys
import datetime as dt
import hashlib
import html
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from fontTools.ttLib import TTFont

# ======================================================================================
# CONFIG - everything you are likely to edit lives in this block
# ======================================================================================
USERNAME = os.environ.get("PROFILE_USER", "praj1t")

NAME = "Prajit Balan"  # drawn on two lines: first word, then the rest
NAME_TRACKING = -1.5   # letter-spacing of the name, px
NAME_LINE_HEIGHT = 1.0  # distance between the two lines of the name, in ems
LIKE_TO_DO_LABEL = "WHAT I LIKE TO DO"
LIKE_TO_DO = (
    "I like building cool stuff, trying out new technologies, "
    "and seeing how far I can take an idea."
)

# Hand-set numbers (GitHub can't know these).
HACKATHONS_ATTENDED = 2
HACKATHONS_WON = 2

LINKEDIN_URL = "https://www.linkedin.com/in/prajitbalan"
X_URL = "https://x.com/prajitbalan"
WEBSITE_TEXT = "yourwebsite.com"  # placeholder shown under "Website"
WEBSITE_URL = None                # set to "https://..." when the site is live; the tile links to it then

# Palette: white, black and gold. Light cards use LIGHT, the project cards use DARK.
GOLD = "#B8902F"  # marks and rules on white
LIGHT = {
    "bg1": "#FFFFFF", "bg2": "#F4F2EC", "border": "#E3DED1",
    "text": "#0D0D0D", "text_2": "#3B3934", "muted": "#6B675F", "dim": "#8F8A7F",
    "line": "#DDD8CB", "deep": "#E9E5DA", "gold": GOLD, "gold_text": "#8A6A14", "chip": "#FFFFFF",
}
DARK = {
    "bg1": "#1B1B1B", "bg2": "#0B0B0B", "border": "#2C2C2C",
    "text": "#FFFFFF", "text_2": "#D6D2C8", "muted": "#A39E92", "dim": "#7D786E",
    "line": "#303030", "deep": "#222222", "gold": "#D8B24A", "gold_text": "#E2C468", "chip": "#141414",
}
# Contribution graph ramp, empty -> busiest (one hue, light to deep, like your reference).
GOLD_RAMP = ["#EAE6DB", "#F1E2B0", "#E3C66E", "#C99C2C", "#94690B"]

# Fonts: source WOFFs live in fonts/ (SIL OFL). key -> (file, css family, weight, style)
FONTS = {
    "display": ("unbounded-latin-800-normal.woff", "PX Display", 800, "normal"),
    "sans": ("inter-latin-400-normal.woff", "PX Sans", 400, "normal"),
    "sansb": ("inter-latin-600-normal.woff", "PX Sans Semibold", 600, "normal"),
    "mono": ("jetbrains-mono-latin-400-normal.woff", "PX Mono", 400, "normal"),
    "monob": ("jetbrains-mono-latin-700-normal.woff", "PX Mono Bold", 700, "normal"),
}
FALLBACKS = {
    "display": "Unbounded,'Arial Black','Helvetica Neue',Arial,sans-serif",
    "sans": "Inter,'Helvetica Neue',Arial,sans-serif",
    "sansb": "Inter,'Helvetica Neue',Arial,sans-serif",
    "mono": "'JetBrains Mono',ui-monospace,Menlo,Consolas,monospace",
    "monob": "'JetBrains Mono',ui-monospace,Menlo,Consolas,monospace",
}

# The projects row: a heading, then one card per project, all drawn the same way.
PROJECTS_HEADING = ("What I'm ", "coding")  # the second part is set in gold
# name : title on the card            desc: one or two sentences (falls back to the repo's description)
# url  : where a click on the card goes
# aliases: other spellings of the repo name, used to pick up its language and stars from data/github.json
# footer : optional; overrides the bottom line (by default: language, stars, and where the link goes)
FEATURED = [
    {"name": "NeuroSight", "aliases": ["neurosight", "neuro-sight"], "url": "https://writeontime.ink",
     "desc": "A research prototype that brings records, timelines, assessments and trends into one "
             "workspace you can ask questions of."},
    {"name": "NavIQ", "aliases": ["naviq", "nav-iq"], "url": "https://github.com/praj1t/NavIQ",
     "desc": "Research Indian mutual funds, compare historical performance, and explore financial data "
             "to make more informed investment decisions."},
    {"name": "Callender", "aliases": [], "url": "https://github.com/praj1t/Callender",
     "desc": "An AI voice agent that handles inbound customer calls, checks availability, and manages "
             "bookings, rescheduling, and cancellations through Google Calendar."},
]

# Portrait. The cut-out is turned into ASCII art: lit skin in gold, hair and features in black.
PORTRAIT_FILE = "portrait.png"
PORTRAIT_ASCII_SIZE = 4.0    # font size in px: smaller = finer
PORTRAIT_GOLD = "#A07A30"    # the tone of the lit skin: a muted bronze, not yellow
PORTRAIT_FEATURE_CUT = 0.24  # 0..1: skin darker than this is drawn black (raise for heavier features)
PORTRAIT_SHARPEN = 120       # unsharp-mask strength, percent
HERO_MIN_H = 400

# Layout. Cards are drawn 840 wide: about the width of a profile README at 100%.
W = 840
TILE_W = 273  # 32.5% of W, so a tile row is drawn at 1:1 when it is shown at 32.5%
PAD = 36
MARGIN_Y = 6  # transparent breathing room above and below every card
RADIUS = 28
TILE_GAP = 8  # px of space between the three tiles
TILE_VALUE_SIZE, TILE_TRACKING = 30, -0.6
HEADING_SIZE = 24  # card titles

# Global timeline (seconds): when each card starts. Cards load together, so a card's
# own animations are offset by this to make the page play top to bottom.
T_HERO = 0.0
T_TILES = [0.55, 0.7, 0.85]
T_STATS = 1.15
T_REPO0, T_REPO_STEP = 1.65, 0.22

# Looping effects. Every card repeats on the same clock, each a little later than the one
# above it, so the shine travels down the page again every LOOP seconds.
LOOP = 8.0
L_NAME, L_PORTRAIT = 1.6, 2.4
L_TILES = [3.2, 3.4, 3.6]
L_STATS = 4.2
L_PROJECTS, L_PROJECT_STEP = 4.8, 0.2
L_GRAPH = 5.8
SHINE = "#E9C96B"  # the glint that crosses the name

# ======================================================================================
ROOT = Path(__file__).resolve().parent.parent
FONT_DIR = ROOT / "fonts"
DATA_FILE = ROOT / "data" / "github.json"
STATIC = os.environ.get("STATIC", "").strip().lower() not in ("", "0", "false", "no")
OUT_DIR = ROOT / ("preview" if STATIC else "cards")

README_START = "<!-- cards:start -->"
README_END = "<!-- cards:end -->"


# --------------------------------------------------------------------------- helpers
def esc(s: str) -> str:
    return html.escape(str(s), quote=True)


def hex_rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def mix(a: str, b: str, t: float) -> str:
    ra, rb = hex_rgb(a), hex_rgb(b)
    return "#%02X%02X%02X" % tuple(round(x + (y - x) * t) for x, y in zip(ra, rb))


def fmt(n: float) -> str:
    """Compact SVG number."""
    s = f"{n:.2f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def fmt_int(n: int) -> str:
    return f"{n:,}"


def fmt_date(iso: str, with_year: bool = True) -> str:
    d = dt.date.fromisoformat(iso[:10])
    return f"{d.strftime('%b')} {d.day}" + (f", {d.year}" if with_year else "")


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())



# ------------------------------------------------------------------ font measuring + subsetting
class FontBook:
    """Measures text with the real glyph advances and subsets fonts per card."""

    def __init__(self) -> None:
        self.fonts: dict[str, TTFont] = {}
        self.cmaps: dict[str, dict] = {}
        self.cache: dict[str, str] = {}
        self.tmp = Path(tempfile.mkdtemp(prefix="cards-fonts-"))
        self.cli = shutil.which("pyftsubset")

    def _font(self, key: str) -> TTFont:
        if key not in self.fonts:
            f = TTFont(str(FONT_DIR / FONTS[key][0]))
            self.fonts[key] = f
            cmap = dict(f.getBestCmap())
            # Lining figures: some display faces default to old-style numerals that dip below the
            # line. Where the font has an "lnum" feature, measure (and draw) with the lining ones.
            if "GSUB" in f:
                gsub = f["GSUB"].table
                swap = {}
                for rec in gsub.FeatureList.FeatureRecord:
                    if rec.FeatureTag == "lnum":
                        for li in rec.Feature.LookupListIndex:
                            for st in gsub.LookupList.Lookup[li].SubTable:
                                st = getattr(st, "ExtSubTable", st)
                                swap.update(getattr(st, "mapping", {}))
                cmap = {cp: swap.get(g, g) for cp, g in cmap.items()}
            self.cmaps[key] = cmap
        return self.fonts[key]

    def width(self, key: str, s: str, size: float, ls: float = 0.0) -> float:
        f = self._font(key)
        cmap, hmtx, upm = self.cmaps[key], f["hmtx"], f["head"].unitsPerEm
        total = 0.0
        for ch in s:
            g = cmap.get(ord(ch))
            total += (hmtx[g][0] if g else hmtx[".notdef"][0]) * size / upm
        return total + ls * len(s)

    def missing(self, key: str, s: str) -> set[str]:
        self._font(key)
        return {ch for ch in s if ord(ch) not in self.cmaps[key] and not ch.isspace()}

    def wrap(self, key: str, s: str, size: float, maxw: float, maxlines: int, ls: float = 0.0) -> list[str]:
        words, lines, cur = [], [], ""
        for w in s.split():
            while self.width(key, w, size, ls) > maxw and len(w) > 1:  # unbreakable run: hard-split it
                cut = len(w) - 1
                while cut > 1 and self.width(key, w[:cut], size, ls) > maxw:
                    cut -= 1
                words.append(w[:cut])
                w = w[cut:]
            words.append(w)
        for w in words:
            trial = (cur + " " + w).strip()
            if self.width(key, trial, size, ls) <= maxw or not cur:
                cur = trial
            else:
                lines.append(cur)
                cur = w
        if cur:
            lines.append(cur)
        if len(lines) > maxlines:
            lines = lines[:maxlines]
            last = lines[-1]
            while last and self.width(key, last + "…", size, ls) > maxw:
                last = last[:-1].rstrip()
            lines[-1] = last + "…"
        return lines

    def subset_b64(self, key: str, chars: str) -> str:
        chars = "".join(sorted(set(chars) | {" "}))
        ck = key + hashlib.md5(chars.encode()).hexdigest()
        if ck in self.cache:
            return self.cache[ck]
        src = FONT_DIR / FONTS[key][0]
        out = self.tmp / f"{ck}.woff"
        text_file = self.tmp / f"{ck}.txt"
        text_file.write_text(chars, encoding="utf-8")
        cmd = (
            [self.cli] if self.cli else [sys.executable, "-m", "fontTools.subset"]
        ) + [
            str(src),
            f"--text-file={text_file}",
            "--flavor=woff",
            "--layout-features=kern,lnum",
            "--no-hinting",
            "--name-IDs=",
            f"--output-file={out}",
        ]
        subprocess.run(cmd, check=True, capture_output=True)
        b64 = base64.b64encode(out.read_bytes()).decode()
        self.cache[ck] = b64
        return b64


FB = FontBook()



# ------------------------------------------------------------------------------- card model
class Card:
    """One SVG. Collects elements, the glyphs each font must carry, and CSS."""

    def __init__(self, name: str, w: int, h: int, title: str, desc: str, t0: float = 0.0, dark: bool = False,
                 inset_x: float = 0.0, radius: float = RADIUS) -> None:
        self.name, self.w, self.h, self.title, self.desc, self.t0 = name, w, h, title, desc, t0
        self.ix, self.radius = inset_x, radius  # inset_x: transparent margin left and right of the card
        self.P = DARK if dark else LIGHT
        self.defs: list[str] = []
        self.body: list[str] = []
        self.glyphs: dict[str, set[str]] = {}
        self.uses_motion = False

    # --- animation: returns attribute text for the element, or "" in static mode
    def A(self, kind: str, delay: float = 0.0, extra_class: str = "") -> str:
        cls = extra_class
        if STATIC:
            return f' class="{cls}"' if cls else ""
        self.uses_motion = True
        cls = (cls + " " + kind).strip()
        return f' class="{cls}" style="animation-delay:{fmt(self.t0 + delay)}s"'

    def add(self, s: str) -> None:
        self.body.append(s)

    def motion(self, s: str) -> None:
        """Decorative SMIL. Wrapped so prefers-reduced-motion can hide it; dropped in STATIC."""
        if not STATIC:
            self.uses_motion = True
            self.body.append(f'<g class="motion">{s}</g>')

    def use(self, font: str, s: str) -> None:
        self.glyphs.setdefault(font, set()).update(s)

    def text(self, x, y, s, font, size, fill, anchor="start", ls=0.0, anim=None, opacity=None, raw=False) -> None:
        self.use(font, s)
        attrs = f'x="{fmt(x)}" y="{fmt(y)}" font-size="{fmt(size)}" fill="{fill}"'
        if anchor != "start":
            attrs += f' text-anchor="{anchor}"'
        if opacity is not None:
            attrs += f' fill-opacity="{opacity}"'
        style = f"letter-spacing:{fmt(ls)}px" if ls else ""
        a = self.A(*anim, extra_class=f"f-{font}") if anim else f' class="f-{font}"'
        if style:
            if 'style="' in a:
                a = a.replace('style="', f'style="{style};')
            else:
                a += f' style="{style}"'
        self.add(f"<text {attrs}{a}>{s if raw else esc(s)}</text>")

    # --- finishing
    def css(self) -> str:
        faces = []
        for font, chars in sorted(self.glyphs.items()):
            miss = FB.missing(font, "".join(chars))
            if miss:
                print(f"  warning[{self.name}]: font '{font}' lacks {sorted(miss)}", file=sys.stderr)
            fam, weight, style = FONTS[font][1], FONTS[font][2], FONTS[font][3]
            b64 = FB.subset_b64(font, "".join(chars))
            faces.append(
                f'@font-face{{font-family:"{fam}";font-weight:{weight};font-style:{style};'
                f'src:url(data:font/woff;base64,{b64}) format("woff")}}'
            )
        rules = [
            f".f-{k}{{font-family:\"{FONTS[k][1]}\",{FALLBACKS[k]};font-weight:{FONTS[k][2]};font-style:{FONTS[k][3]}}}"
            for k in sorted(self.glyphs)
        ]
        rules.append("text{font-kerning:normal;font-variant-numeric:lining-nums}.pre{white-space:pre;text-rendering:geometricPrecision}")
        if self.uses_motion and not STATIC:
            ease = "cubic-bezier(.2,.7,.2,1)"
            rules += [
                f".rise{{animation:rise .75s {ease} both}}",
                f".fade{{animation:fade .8s ease-out both}}",
                f".pop{{transform-box:fill-box;transform-origin:50% 50%;animation:pop .5s {ease} both}}",
                f".grow{{transform-box:fill-box;transform-origin:50% 100%;animation:grow .85s {ease} both}}",
                f".draw{{stroke-dasharray:1;animation:draw 1.1s ease-in-out both}}",
                f".wave{{transform-box:fill-box;transform-origin:50% 50%;animation:wave .45s {ease} both}}",
                ".blink{animation:blink 1.15s steps(1) infinite}",
                "@keyframes rise{from{opacity:0;transform:translateY(12px)}}",
                "@keyframes fade{from{opacity:0}}",
                "@keyframes pop{from{opacity:0;transform:scale(.82)}}",
                "@keyframes grow{from{transform:scaleY(0)}}",
                "@keyframes draw{from{stroke-dashoffset:1}to{stroke-dashoffset:0}}",
                "@keyframes wave{from{opacity:0;transform:scale(.35)}}",
                "@keyframes blink{50%{opacity:0}}",
                "@media (prefers-reduced-motion:reduce){"
                "*{animation:none!important;transition:none!important}.motion{display:none}}",
            ]
        return "<style>" + "".join(faces) + "".join(rules) + "</style>"

    def svg(self) -> str:
        w, h = self.w, self.h + 2 * MARGIN_Y
        P = self.P
        ix, rad = self.ix, self.radius
        shell = (
            f'<rect x="{fmt(ix + .5)}" y=".5" width="{fmt(self.w - 1 - 2 * ix)}" height="{self.h - 1}" rx="{fmt(rad)}" '
            f'fill="url(#bg)" stroke="{P["border"]}"/>'
        )
        bg = (
            '<linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">'
            f'<stop offset="0" stop-color="{P["bg1"]}"/><stop offset="1" stop-color="{P["bg2"]}"/>'
            "</linearGradient>"
        )
        clip = (f'<clipPath id="card"><rect x="{fmt(ix)}" width="{fmt(self.w - 2 * ix)}" height="{self.h}" '
                f'rx="{fmt(rad)}"/></clipPath>')
        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}" '
            f'role="img" aria-labelledby="t d">'
            f"<title id=\"t\">{esc(self.title)}</title><desc id=\"d\">{esc(self.desc)}</desc>"
            f"{self.css()}<defs>{bg}{clip}{''.join(self.defs)}</defs>"
            f'<g transform="translate(0 {MARGIN_Y})">{shell}<g clip-path="url(#card)">{"".join(self.body)}</g></g>'
            "</svg>\n"
        )



# --------------------------------------------------------------------------------- data
def load_data() -> dict:
    if not DATA_FILE.exists():
        sys.exit(f"{DATA_FILE} is missing. Run scripts/fetch_data.py (or add --sample) first.")
    data = json.loads(DATA_FILE.read_text())
    if data.get("sample"):
        print("note: data/github.json is SAMPLE data. Numbers are placeholders until the workflow runs.")
    return data


def contribution_stats(days: list[dict]) -> dict:
    days = sorted(days, key=lambda d: d["date"])
    counts = [d["count"] for d in days]
    # current streak: today may still be empty, so it only breaks the streak once it's over
    i = len(days) - 1
    if i >= 0 and counts[i] == 0:
        i -= 1
    cur = 0
    while i >= 0 and counts[i] > 0:
        cur += 1
        i -= 1
    longest = run = 0
    for c in counts:
        run = run + 1 if c > 0 else 0
        longest = max(longest, run)
    year = days[-365:]  # GitHub's "last year"; the calendar can run a few days longer
    busiest = max(year, key=lambda d: (d["count"], d["date"]))
    active = sum(1 for d in year if d["count"] > 0)
    # twelve calendar months ending with the current one
    last = dt.date.fromisoformat(days[-1]["date"])
    keys = []
    y, m = last.year, last.month
    for _ in range(12):
        keys.append((y, m))
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    keys.reverse()
    per = {k: 0 for k in keys}
    for d in days:
        dd = dt.date.fromisoformat(d["date"])
        if (dd.year, dd.month) in per:
            per[(dd.year, dd.month)] += d["count"]
    out = {
        "current": cur,
        "longest": longest,
        "active": active,
        "span": len(year),
        "year_total": sum(d["count"] for d in year),
        "months_total": 0,
        "busiest": busiest,
        "months": [(dt.date(y, m, 1).strftime("%b"), v) for (y, m), v in per.items()],
        "total": sum(counts),
    }
    out["months_total"] = sum(v for _, v in out["months"])
    return out


def nice_ceiling(v: int) -> tuple[int, int]:
    """Round the axis top up to a tidy number; returns (top, step) with 2 intervals."""
    if v <= 4:
        return 4, 2
    mag = 10 ** math.floor(math.log10(v))
    for m in (1, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10):
        top = m * mag
        if top >= v and (top / 2) == int(top / 2):
            return int(top), int(top / 2)
    return int(math.ceil(v / 2) * 2), int(math.ceil(v / 2))



# ---------------------------------------------------------------------------------- motion
def pulse(card: Card, cx: float, cy: float, r0: float, r1: float, color: str, dur: float = 2.4, begin: float = 0) -> None:
    card.motion(
        f'<circle cx="{fmt(cx)}" cy="{fmt(cy)}" r="{fmt(r0)}" fill="none" stroke="{color}" stroke-width="1.5">'
        f'<animate attributeName="r" values="{fmt(r0)};{fmt(r1)}" dur="{dur}s" begin="{begin}s" repeatCount="indefinite"/>'
        f'<animate attributeName="opacity" values=".7;0" dur="{dur}s" begin="{begin}s" repeatCount="indefinite"/>'
        "</circle>"
    )


def sweep(card: Card, x0: float, x1: float, y0: float, y1: float, begin: float,
          color: str = "#FFFFFF", opacity: float = 0.12, band: float = 80, clip: bool = False) -> None:
    """A soft diagonal band of light that crosses the box x0..x1 once per LOOP, then waits off to the side."""
    if STATIC:
        return
    n = len(card.defs)
    card.defs.append(
        f'<linearGradient id="sw{n}"><stop offset="0" stop-color="{color}" stop-opacity="0"/>'
        f'<stop offset=".5" stop-color="{color}" stop-opacity="{opacity}"/>'
        f'<stop offset="1" stop-color="{color}" stop-opacity="0"/></linearGradient>'
    )
    h = y1 - y0
    lean = h * 0.4
    start, end = x0 - band - lean - 4, x1 + 4
    shape = (
        f'<path d="M0 {fmt(y1)}L{fmt(lean)} {fmt(y0)}H{fmt(lean + band)}L{fmt(band)} {fmt(y1)}Z" fill="url(#sw{n})" '
        f'transform="translate({fmt(start)} 0)">'
        f'<animateTransform attributeName="transform" type="translate" values="{fmt(start)} 0;{fmt(end)} 0;{fmt(end)} 0" '
        f'keyTimes="0;.17;1" dur="{fmt(LOOP)}s" begin="{fmt(begin)}s" repeatCount="indefinite"/></path>'
    )
    if clip:
        card.defs.append(f'<clipPath id="sc{n}"><rect x="{fmt(x0)}" y="{fmt(y0)}" width="{fmt(x1 - x0)}" height="{fmt(h)}"/></clipPath>')
        shape = f'<g clip-path="url(#sc{n})">{shape}</g>'
    card.motion(shape)


def shine_text(card: Card, lines: list[tuple[float, float, str]], font: str, size: float, ls: float,
               x0: float, x1: float, begin: float, color: str = SHINE) -> None:
    """A glint that swipes across text already drawn at (x, y, string): a copy of the text
    filled with a moving slanted gradient, so only the letters catch the light."""
    if STATIC:
        return
    n = len(card.defs)
    # the band is slanted (it leans 38 down per 110 across), so its rest position has to clear
    # the lowest line of text as well as the right-hand edge
    start, end = x0 - 150, x1 + 30 + max(y for _, y, _ in lines) * 38 / 110
    card.defs.append(
        f'<linearGradient id="sh{n}" gradientUnits="userSpaceOnUse" x1="0" y1="0" x2="110" y2="38" '
        f'gradientTransform="translate({fmt(start)} 0)">'
        f'<stop offset="0" stop-color="{color}" stop-opacity="0"/><stop offset=".5" stop-color="{color}"/>'
        f'<stop offset="1" stop-color="{color}" stop-opacity="0"/>'
        f'<animateTransform attributeName="gradientTransform" type="translate" values="{fmt(start)} 0;{fmt(end)} 0;{fmt(end)} 0" '
        f'keyTimes="0;.2;1" dur="{fmt(LOOP)}s" begin="{fmt(begin)}s" repeatCount="indefinite"/></linearGradient>'
    )
    style = f' style="letter-spacing:{fmt(ls)}px"' if ls else ""
    card.use(font, "".join(t for _, _, t in lines))
    card.motion("".join(
        f'<text x="{fmt(x)}" y="{fmt(y)}" font-size="{fmt(size)}" fill="url(#sh{n})" class="f-{font}"{style}>{esc(t)}</text>'
        for x, y, t in lines
    ))


# ------------------------------------------------------------------------- ASCII portrait
ASCII_RAMP = " .:-=+*#%@"  # light -> dense


def portrait_rows(region_w: float, region_h: float, fs: float):
    """Turn assets/portrait.png into ASCII rows: [(row, first_col, [[is_gold, text], ...]), ...].

    Returns None when there is no portrait. The face is toned on its own scale so it uses the
    whole character ramp: lit skin is drawn in gold, shadows and features (eyes, brows, beard)
    in black, so the face reads clearly at small sizes.
    """
    path = ROOT / "assets" / PORTRAIT_FILE
    if not path.exists():
        return None
    try:
        from PIL import Image, ImageFilter
    except ImportError:
        print("  warning: Pillow missing, skipping portrait", file=sys.stderr)
        return None
    im = Image.open(path).convert("RGBA")
    im = im.crop(im.getchannel("A").point(lambda a: 255 if a > 20 else 0).getbbox())
    cell_w, cell_h = fs * 0.6, fs  # JetBrains Mono advance is 0.6em
    scale = min(region_w / im.width, region_h / im.height)
    cols, rows = int(im.width * scale / cell_w), int(im.height * scale / cell_h)
    # sharpen at 3x the grid, then average down: edges of the eye, nose and mouth survive
    big = im.resize((cols * 3, rows * 3), Image.LANCZOS)
    sharp = big.convert("RGB").filter(ImageFilter.UnsharpMask(radius=3, percent=PORTRAIT_SHARPEN, threshold=2))
    px = sharp.resize((cols, rows), Image.BOX).load()
    al = big.getchannel("A").resize((cols, rows), Image.BOX).load()

    def is_skin(R, G, B):
        h_, s_, v_ = colorsys.rgb_to_hsv(R / 255, G / 255, B / 255)
        return (h_ < 0.12 or h_ > 0.96) and 0.18 < s_ < 0.85 and v_ > 0.26

    lum = [[(0.299 * px[c, r][0] + 0.587 * px[c, r][1] + 0.114 * px[c, r][2]) / 255 for c in range(cols)] for r in range(rows)]
    skin = [[al[c, r] >= 80 and is_skin(*px[c, r]) for c in range(cols)] for r in range(rows)]
    # the face region: skin cells plus the dark features enclosed by them (grown by a few cells)
    grow = max(2, cols // 40)
    face = [[False] * cols for _ in range(rows)]
    for r in range(rows):
        for c in range(cols):
            if skin[r][c]:
                for rr in range(max(0, r - grow), min(rows, r + grow + 1)):
                    for cc in range(max(0, c - grow), min(cols, c + grow + 1)):
                        face[rr][cc] = True
    def spread(values, lo_p, hi_p):
        v = sorted(values) or [0.0, 1.0]
        return v[int((len(v) - 1) * lo_p)], v[int((len(v) - 1) * hi_p)]

    s_lo, s_hi = spread((lum[r][c] for r in range(rows) for c in range(cols) if skin[r][c]), 0.04, 0.985)
    # hair and shirt get their own scale too, so curls keep some texture instead of going flat
    h_lo, h_hi = spread((lum[r][c] for r in range(rows) for c in range(cols) if al[c, r] >= 200 and not face[r][c]), 0.05, 0.97)

    out = []
    n = len(ASCII_RAMP) - 1
    for r in range(rows):
        row = []
        for c in range(cols):
            A = al[c, r]
            if A < 80:
                row.append((False, " "))
                continue
            edge = min(1.0, (A / 255) ** 1.4)
            if face[r][c]:
                t = min(1.0, max(0.0, (lum[r][c] - s_lo) / max(1e-6, s_hi - s_lo)))  # 0 shadow .. 1 highlight
                if t < PORTRAIT_FEATURE_CUT:  # eyes, brows, beard, deep shadow: black ink
                    dark = 0.5 + 0.5 * (1 - t / PORTRAIT_FEATURE_CUT)
                    gold = False
                else:  # lit skin: gold, never fainter than a mid-weight glyph
                    u = (t - PORTRAIT_FEATURE_CUT) / (1 - PORTRAIT_FEATURE_CUT)
                    dark = 0.8 - 0.5 * (u ** 0.8)
                    gold = True
            else:  # hair and shirt: black, lighter glyphs on the highlights
                t = min(1.0, max(0.0, (lum[r][c] - h_lo) / max(1e-6, h_hi - h_lo)))
                dark, gold = 1.0 - 0.55 * t, False
            dark *= edge
            row.append((gold, ASCII_RAMP[1 + min(n - 1, int(dark * n + 0.5))]))
        inked = [i for i, (_, ch) in enumerate(row) if ch != " "]
        if not inked:
            continue
        runs: list[list] = []
        for gold, ch in row[inked[0]: inked[-1] + 1]:
            if runs and runs[-1][0] == gold:
                runs[-1][1] += ch
            else:
                runs.append([gold, ch])
        out.append((r, inked[0], runs))
    return out, cols, rows, cell_w, cell_h


# ================================================================================ CARDS
def fit(font: str, s: str, size: float, maxw: float, ls: float = 0.0, floor: float = 18) -> float:
    """Largest size <= `size` at which `s` fits in `maxw`."""
    while FB.width(font, s, size, ls) > maxw and size > floor:
        size -= 1
    return size


def card_hero(t0: float) -> Card:
    P = LIGHT
    parts = NAME.split()
    lines = [parts[0], " ".join(parts[1:])] if len(parts) > 1 else [NAME]
    left_w = 400
    size = min(fit("display", ln, 108, left_w, NAME_TRACKING, floor=56) for ln in lines)
    line_h = size * NAME_LINE_HEIGHT
    tag_lines = FB.wrap("sans", LIKE_TO_DO, 19, left_w, 3)
    # stack: name lines, gap, label, tagline; centred vertically in the card
    name_h = size * 0.74 + line_h * (len(lines) - 1)
    block = name_h + 62 + 32 + 28 * (len(tag_lines) - 1)
    h = round(max(HERO_MIN_H, block + 112))
    top = (h - block) / 2
    base1 = top + size * 0.74
    label_y = base1 + line_h * (len(lines) - 1) + 62
    tag_y0 = label_y + 32
    c = Card("hero", W, h, NAME, f"{NAME}. {LIKE_TO_DO}", t0)

    for k, ln in enumerate(lines):
        c.text(PAD - 4, base1 + line_h * k, ln, "display", size, P["text"], ls=NAME_TRACKING, anim=("rise", 0.15 + 0.12 * k))
    shine_text(c, [(PAD - 4, base1 + line_h * k, ln) for k, ln in enumerate(lines)], "display", size, NAME_TRACKING,
               PAD, PAD + left_w + 20, L_NAME)

    c.add(
        f'<path d="M{PAD} {fmt(label_y - 4)}h20" stroke="{P["gold"]}" stroke-width="2.5" stroke-linecap="round" '
        f'fill="none" pathLength="1"{c.A("draw", 0.6)}/>'
    )
    c.text(PAD + 30, label_y, LIKE_TO_DO_LABEL, "mono", 11.5, P["gold_text"], ls=2, anim=("rise", 0.6))
    for i, line in enumerate(tag_lines):
        c.text(PAD, tag_y0 + 28 * i, line, "sans", 19, P["text_2"], anim=("rise", 0.75 + 0.1 * i))

    # ASCII portrait, revealed top to bottom
    rx0, ry0 = 452, 22
    art = portrait_rows(W - 28 - rx0, h - 2 * ry0, PORTRAIT_ASCII_SIZE)
    if art:
        rows_, cols, nrows, cw, ch = art
        x_off = rx0 + (W - 28 - rx0 - cols * cw) / 2
        y_off = ry0 + (h - 2 * ry0 - nrows * ch) / 2
        c.use("monob", ASCII_RAMP)
        for r, first_col, runs in rows_:
            spans = "".join(
                f'<tspan fill="{PORTRAIT_GOLD if gold else P["text"]}">{text}</tspan>' for gold, text in runs
            )
            c.add(
                f'<text x="{fmt(x_off + first_col * cw)}" y="{fmt(y_off + (r + 1) * ch - ch * 0.2)}" '
                f'font-size="{fmt(PORTRAIT_ASCII_SIZE)}" xml:space="preserve" '
                # pin each row's width: browsers round glyph advances at tiny sizes, which would shear the picture
                f'textLength="{fmt(sum(len(t) for _, t in runs) * cw)}" lengthAdjust="spacing"'
                f'{c.A("fade", 0.4 + r * 0.011, extra_class="f-monob pre")}>{spans}</text>'
            )
        sweep(c, x_off - 6, x_off + cols * cw + 6, y_off, y_off + nrows * ch, L_PORTRAIT,
              color=P["bg1"], opacity=0.6, band=90, clip=True)
    return c


def card_tile(name: str, spec: dict, size: float, t0: float, loop_at: float) -> Card:
    """A black social tile: the name set large, the handle under it. Inset so the three don't touch."""
    h, ix = 132, TILE_GAP / 2
    c = Card(name, TILE_W, h, spec["value"], spec["alt"], t0, dark=True, inset_x=ix, radius=22)
    P = c.P
    x = ix + 24
    c.add(
        f'<path d="M{fmt(x)} 34h22" stroke="{P["gold"]}" stroke-width="2.5" stroke-linecap="round" fill="none" '
        f'pathLength="1"{c.A("draw", 0.15)}/>'
    )
    c.text(x - 1, 82, spec["value"], "display", size, P["text"], ls=TILE_TRACKING, anim=("rise", 0.2))
    c.text(x, 108, spec["caption"], "mono", 12, P["muted"], anim=("rise", 0.32))
    sweep(c, ix, TILE_W - ix, 0, h, loop_at, opacity=0.13, band=70)
    return c


def card_heading(plain: str, gold: str, t0: float) -> Card:
    """The title of the projects row: white on black, last word in gold."""
    c = Card("projects-heading", W, 80, plain + gold, plain + gold, t0, dark=True)
    P = c.P
    size = fit("display", plain + gold, 27, W - 2 * PAD, -0.8, floor=18)
    c.text(PAD - 1, 50, plain, "display", size, P["text"], ls=-0.8, anim=("rise", 0.0))
    c.text(PAD - 1 + FB.width("display", plain, size, -0.8), 50, gold, "display", size, P["gold"], ls=-0.8, anim=("rise", 0.08))
    return c


PROJECT_TEXT = ("sans", 13.5, 21)  # font, size, line height of the description
PROJECT_TEXT_W = TILE_W - TILE_GAP - 48


def project_footer(spec: dict, repo: dict | None) -> str:
    """Bottom line of a project card: language, stars if there are any, and where the click goes."""
    if spec.get("footer"):
        return spec["footer"]
    host = re.sub(r"^https?://(www\.)?", "", spec.get("url") or "").split("/")[0]
    parts = []
    if repo and repo.get("language"):
        parts.append(repo["language"])
    if repo and repo.get("stars"):
        parts.append(f"★ {fmt_int(repo['stars'])}")
    if host == "github.com":
        parts.append("GitHub")
    elif host:
        parts += [host, "live"]
    return " · ".join(parts)


def card_project(i: int, spec: dict, repo: dict | None, rows: int, t0: float, loop_at: float) -> Card:
    """One project: dot and name, description, footer. `rows` is the tallest description in the row,
    so the three cards come out the same height."""
    font, fs, lh = PROJECT_TEXT
    ix = TILE_GAP / 2
    name = spec["name"]
    desc = spec.get("desc") or (repo or {}).get("description") or ""
    if not desc:
        print(f"  warning: no description for {name!r}; add one to FEATURED in render_cards.py", file=sys.stderr)
    lines = FB.wrap(font, desc, fs, PROJECT_TEXT_W, 7) if desc else []
    y_name, y_desc = 50, 84
    h = round(y_desc + lh * (max(rows, 1) - 1) + 58)
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    c = Card(f"project-{slug}", TILE_W, h, name, f"{name}: {desc}" if desc else name, t0, inset_x=ix, radius=22)
    P = c.P
    x = ix + 24
    c.add(f'<rect x="{fmt(x)}" y="{y_name - 12}" width="11" height="11" rx="3" fill="{P["gold"]}"{c.A("pop", 0.1)}/>')
    size = fit("display", name, 16.5, PROJECT_TEXT_W - 22, -0.4, floor=11)
    c.text(x + 21, y_name, name, "display", size, P["text"], ls=-0.4, anim=("rise", 0.12))
    for k, line in enumerate(lines):
        c.text(x, y_desc + lh * k, line, font, fs, P["text_2"], anim=("rise", 0.22 + 0.05 * k))
    footer = project_footer(spec, repo)
    if footer:
        c.text(x, h - 26, footer, "mono", 11.5, P["muted"], anim=("fade", 0.6))
    sweep(c, ix, TILE_W - ix, 0, h, loop_at, color=P["gold"], opacity=0.16, band=70)
    return c


def card_stats(stats: dict, t0: float) -> Card:
    P = LIGHT
    months = stats["months"]
    vmax = max(v for _, v in months) or 1
    top, step = nice_ceiling(vmax)
    h = 408
    cur_s, longest, active, span = stats["current"], stats["longest"], stats["active"], stats["span"]
    bd = stats["busiest"]
    pct_won = round(100 * HACKATHONS_WON / HACKATHONS_ATTENDED) if HACKATHONS_ATTENDED else 0
    plural = lambda n, w: f"{n} {w}" + ("" if n == 1 else "s")
    desc = (
        f"Contributions per month over the last 12 months, {fmt_int(stats['months_total'])} in total. "
        f"Longest streak {plural(longest, 'day')}, current streak {plural(cur_s, 'day')}. "
        f"Active on {active} of {span} days. Busiest day {fmt_date(bd['date'])} with {bd['count']}. "
        f"Hackathons: won {HACKATHONS_WON} of {HACKATHONS_ATTENDED}. Monthly: " + ", ".join(f"{m} {v}" for m, v in months)
    )
    c = Card("stats", W, h, "Contributions by month", desc, t0)

    c.text(PAD - 1, 60, "Contributions by month", "display", HEADING_SIZE, P["text"], ls=-0.8, anim=("rise", 0.0))
    c.text(PAD, 87, f"{fmt_int(stats['months_total'])} in the last 12 months", "sans", 13.5, P["muted"], anim=("rise", 0.08))

    # ---- four figures, same weight; gold marks the one that isn't from GitHub
    colw = (W - 2 * PAD) / 4
    ly, num = 136, 44
    figures = [
        ("LONGEST STREAK", str(longest), "day" if longest == 1 else "days", f"current: {plural(cur_s, 'day')}", P["text"]),
        ("ACTIVE DAYS", fmt_int(active), f"of {span}", f"{plural(stats['year_total'], 'contribution')}", P["text"]),
        ("BUSIEST DAY", fmt_date(bd["date"], with_year=False), "", plural(bd["count"], "contribution"), P["text"]),
        ("HACKATHONS WON", f"{HACKATHONS_WON}/{HACKATHONS_ATTENDED}", "", f"{pct_won}% win rate", P["gold_text"]),
    ]
    unit_ws = [FB.width("sansb", f[2], 14.5) + 7 if f[2] else 0 for f in figures]
    size = min(fit("display", f[1], num, colw - 30 - uw, -1.5, floor=24) for f, uw in zip(figures, unit_ws))
    for k, (label, value, unit, caption, ink) in enumerate(figures):
        x = PAD + colw * k + (0 if k == 0 else 4)
        if k:
            c.add(f'<path d="M{fmt(x - 20)} {ly - 12}V{ly + 88}" stroke="{P["line"]}" stroke-width="1"{c.A("fade", 0.2)}/>')
        d = 0.15 + 0.07 * k
        c.text(x, ly, label, "mono", 11, P["muted"], ls=1.8, anim=("rise", d))
        c.text(x - 1, ly + 56, value, "display", size, ink, ls=-1.5, anim=("rise", d + 0.1))
        if unit:
            c.text(x + FB.width("display", value, size, -1.5) + 6, ly + 55, unit, "sansb", 14.5, P["text_2"], anim=("rise", d + 0.15))
        if ink != P["text"]:
            shine_text(c, [(x - 1, ly + 56, value)], "display", size, -1.5, x, x + colw, L_STATS, color="#F6E3A1")
        c.text(x, ly + 84, caption, "mono", 12, P["dim"], anim=("rise", d + 0.22))

    # ---- monthly bars: one series, one colour; the peak month is inked black
    x0, x1 = 84, W - PAD
    ytop, ybase = 276, 358
    slot = (x1 - x0) / 12
    bw = min(24, slot * 0.46)
    for k in range(3):
        v = step * k
        y = ybase - (ybase - ytop) * v / top
        c.add(f'<path d="M{x0 - 8} {fmt(y)}H{x1}" stroke="{P["line"]}" stroke-width="1"{c.A("fade", 0.6)}/>')
        c.text(x0 - 18, y + 4, fmt_int(v), "mono", 11.5, P["dim"], anchor="end", anim=("fade", 0.65))
    peak_i = max(range(12), key=lambda i: (months[i][1], i))
    labelled = {peak_i, 11}
    bar = mix(P["bg1"], P["gold"], 0.78)
    for i, (m, v) in enumerate(months):
        cx_ = x0 + slot * (i + 0.5)
        bx = cx_ - bw / 2
        bh = max(2, (ybase - ytop) * v / top)
        r = min(4, bh / 2)
        yb = ybase - bh
        d = (f"M{fmt(bx)} {ybase}V{fmt(yb + r)}Q{fmt(bx)} {fmt(yb)} {fmt(bx + r)} {fmt(yb)}"
             f"H{fmt(bx + bw - r)}Q{fmt(bx + bw)} {fmt(yb)} {fmt(bx + bw)} {fmt(yb + r)}V{ybase}Z")
        fill = P["line"] if v == 0 else P["text"] if i == peak_i else bar
        c.add(f'<path d="{d}" fill="{fill}"{c.A("grow", 0.65 + 0.055 * i)}/>')
        if i in labelled and v > 0:
            c.text(cx_, yb - 8, fmt_int(v), "display", 14, P["text"], anchor="middle", anim=("fade", 1.3 + 0.055 * i))
        c.text(cx_, ybase + 26, m, "mono", 12, P["muted"] if i == peak_i else P["dim"], anchor="middle", anim=("fade", 0.7 + 0.04 * i))
    sweep(c, x0, x1, ytop - 4, ybase, L_STATS + 0.5, color=P["bg1"], opacity=0.75, band=70, clip=True)
    return c


def card_graph(stats: dict, days: list[dict], t0: float) -> Card:
    days = sorted(days, key=lambda d: d["date"])
    first = dt.date.fromisoformat(days[0]["date"])
    lead = (first.weekday() + 1) % 7  # Sunday = row 0, like GitHub
    cells = [((n + lead) // 7, (n + lead) % 7, d["count"], d["date"]) for n, d in enumerate(days)]
    ncols = cells[-1][0] + 1
    nz = sorted(c_[2] for c_ in cells if c_[2] > 0)

    def level(v: int) -> int:
        if v <= 0 or not nz:
            return 0
        q = lambda p: nz[min(len(nz) - 1, int(p * len(nz)))]
        return 1 + (v > q(0.25)) + (v > q(0.5)) + (v > q(0.75))

    P = LIGHT
    gx0 = PAD + 30
    pitch = (W - PAD - gx0) / ncols
    size = pitch - 3.2
    gy0 = 150
    h = round(gy0 + 7 * pitch + 62)
    desc = f"Contribution graph: {fmt_int(stats['total'])} contributions in the last year, drawn in gold."
    c = Card("graph", W, h, "Every day of the last year", desc, t0)

    c.text(PAD - 1, 60, "Every day of the last year", "display", HEADING_SIZE, P["text"], ls=-0.8, anim=("rise", 0.0))
    c.text(W - PAD, 60, "REFRESHED DAILY", "mono", 11.5, P["muted"], anchor="end", ls=2, anim=("rise", 0.1))
    c.text(PAD, 87, f"{fmt_int(stats['total'])} contributions in the last year", "sans", 13.5, P["muted"], anim=("rise", 0.08))

    last_x, seen = -99.0, None
    for col, row, _, iso in cells:
        d = dt.date.fromisoformat(iso)
        if row == 0 and (d.year, d.month) != seen:
            seen = (d.year, d.month)
            x = gx0 + col * pitch
            if x - last_x >= 38 and col < ncols - 2:
                c.text(x, gy0 - 14, d.strftime("%b"), "mono", 11.5, P["dim"], anim=("fade", 0.3))
                last_x = x
    for r, lab in ((1, "Mon"), (3, "Wed"), (5, "Fri")):
        c.text(PAD, gy0 + r * pitch + size - 1.5, lab, "mono", 11, P["dim"], anim=("fade", 0.3))

    for col, row, v, iso in cells:
        x, y = gx0 + col * pitch, gy0 + row * pitch
        c.add(
            f'<rect x="{fmt(x)}" y="{fmt(y)}" width="{fmt(size)}" height="{fmt(size)}" rx="2.8" '
            f'fill="{GOLD_RAMP[level(v)]}"{c.A("wave", 0.35 + col * 0.0135 + row * 0.022)}/>'
        )
    sweep(c, gx0 - 2, W - PAD + 2, gy0 - 2, gy0 + 7 * pitch, L_GRAPH, color=P["bg1"], opacity=0.8, band=120, clip=True)
    tc = cells[-1]
    pulse(c, gx0 + tc[0] * pitch + size / 2, gy0 + tc[1] * pitch + size / 2, size / 2, size * 1.5, GOLD_RAMP[4], dur=2.2, begin=1.8)

    ly = gy0 + 7 * pitch + 24
    lx = W - PAD
    c.text(lx, ly + 10, "more", "mono", 11.5, P["dim"], anchor="end", anim=("fade", 1.0))
    lx -= FB.width("mono", "more", 11.5) + 8
    for k in range(4, -1, -1):
        lx -= size
        c.add(f'<rect x="{fmt(lx)}" y="{fmt(ly)}" width="{fmt(size)}" height="{fmt(size)}" rx="2.8" fill="{GOLD_RAMP[k]}"{c.A("fade", 1.0)}/>')
        lx -= 4
    c.text(lx - 4, ly + 10, "less", "mono", 11.5, P["dim"], anchor="end", anim=("fade", 1.0))
    return c



# ================================================================================ OUTPUT
def write_readme(entries: list[tuple[str, str, str | None, str, float]]) -> None:
    """entries: (file, alt, href, width, ...) -> block between the markers in README.md."""
    rows: list[list[tuple]] = []
    for e in entries:
        if e[3] == "32.5%" and rows and rows[-1][0][3] == "32.5%" and len(rows[-1]) < 3:
            rows[-1].append(e)
        else:
            rows.append([e])
    lines = ['<div align="center">', ""]
    for row in rows:
        parts = []
        for file, alt, href, width, _ in row:
            img = f'<img src="cards/{file}" width="{width}" alt="{esc(alt)}">'
            parts.append(f'<a href="{esc(href)}">{img}</a>' if href else img)
        lines.append("\n".join(parts))
        lines.append("<br>")
    lines[-1] = ""
    lines.append("</div>")
    block = f"{README_START}\n" + "\n".join(lines) + f"\n{README_END}"
    readme = ROOT / "README.md"
    if readme.exists() and README_START in readme.read_text():
        txt = readme.read_text()
        new = re.sub(re.escape(README_START) + r".*?" + re.escape(README_END), lambda _: block, txt, flags=re.S)
    else:
        new = block + "\n"
    if not readme.exists() or readme.read_text() != new:
        readme.write_text(new)


def write_preview(entries: list) -> None:
    body = []
    row: list[str] = []
    for file, alt, href, width, _ in entries:
        tag = f'<img src="cards/{file}" width="{width}" alt="{esc(alt)}">'
        if width == "32.5%":
            row.append(tag)
            if len(row) == 3:
                body.append("<div>" + " ".join(row) + "</div>")
                row = []
        else:
            body.append(f"<div>{tag}</div>")
    page = (
        '<!doctype html><meta charset="utf-8"><title>Profile preview (STATIC)</title>'
        "<style>body{margin:0;background:#0d1117;display:flex;justify-content:center}"
        "main{width:min(840px,100vw);padding:24px 0;text-align:center;font-size:0}"
        "img{vertical-align:middle;max-width:100%}</style>"
        "<main>" + "".join(body) + "</main>"
    )
    (OUT_DIR / "index.html").write_text(page)



def resolve_featured(data: dict) -> list[tuple[dict, dict | None]]:
    index = {norm(r["name"]): r for r in data["repos"]}
    resolved = []
    for spec in FEATURED:
        keys = [norm(spec["name"])] + [norm(a) for a in spec.get("aliases", [])]
        resolved.append((spec, next((index[k] for k in keys if k in index), None)))
    return resolved


def main() -> None:
    data = load_data()
    days = data["contributions"]["days"]
    stats = contribution_stats(days)

    tiles = [
        {"value": "LinkedIn", "caption": "in/prajitbalan", "href": LINKEDIN_URL, "alt": f"{NAME} on LinkedIn"},
        {"value": "X", "caption": "@prajitbalan", "href": X_URL, "alt": f"{NAME} on X"},
        {"value": "Website", "caption": WEBSITE_TEXT, "href": WEBSITE_URL,
         "alt": f"{NAME}'s website" + ("" if WEBSITE_URL else " (coming soon)")},
    ]

    cards: list[tuple[Card, str | None, str, str]] = []  # card, href, width, alt
    cards.append((card_hero(T_HERO), None, "100%", f"{NAME}. {LIKE_TO_DO}"))
    tile_size = min(fit("display", t["value"], TILE_VALUE_SIZE, TILE_W - TILE_GAP - 48, TILE_TRACKING, floor=18) for t in tiles)
    for i, spec in enumerate(tiles):
        cards.append((card_tile(f"tile-{i + 1}", spec, tile_size, T_TILES[i], L_TILES[i]), spec["href"], "32.5%", spec["alt"]))
    cards.append((card_stats(stats, T_STATS), None, "100%",
                  f"Contributions per month for the last year. Longest streak {stats['longest']} days, "
                  f"active on {stats['active']} days. Won {HACKATHONS_WON} of {HACKATHONS_ATTENDED} hackathons."))

    # Projects: a heading, then one card each, all the same height, each linking to its url.
    featured = resolve_featured(data)
    font, fs, _ = PROJECT_TEXT
    texts = [spec.get("desc") or (repo or {}).get("description") or "" for spec, repo in featured]
    rows = max([len(FB.wrap(font, t, fs, PROJECT_TEXT_W, 7)) for t in texts if t] or [1])
    t = T_REPO0
    cards.append((card_heading(*PROJECTS_HEADING, t), None, "100%", "".join(PROJECTS_HEADING)))
    t += 0.15
    for i, ((spec, repo), text) in enumerate(zip(featured, texts)):
        href = spec.get("url") or (repo or {}).get("url")
        card = card_project(i, spec, repo, rows, t, L_PROJECTS + L_PROJECT_STEP * i)
        cards.append((card, href, "32.5%", f"{spec['name']}: {text}".strip(": ")))
        t += T_REPO_STEP
    cards.append((card_graph(stats, days, t + 0.1), None, "100%", f"Contribution graph, {stats['total']} contributions in the last year."))

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    if not STATIC:
        for old in OUT_DIR.glob("*.svg"):
            old.unlink()
    elif (OUT_DIR / "cards").exists():
        shutil.rmtree(OUT_DIR / "cards")
    card_dir = OUT_DIR / "cards" if STATIC else OUT_DIR
    card_dir.mkdir(parents=True, exist_ok=True)

    entries = []
    for card, href, width, alt in cards:
        svg = card.svg()
        # The file name carries a hash of the card. GitHub's image CDN keeps serving the old picture for
        # a URL for several minutes, so a changed card gets a new name and shows up at once.
        fname = f"{card.name}.svg" if STATIC else f"{card.name}-{hashlib.md5(svg.encode()).hexdigest()[:8]}.svg"
        (card_dir / fname).write_text(svg, encoding="utf-8")
        entries.append((fname, alt, href, width, card.t0))
        print(f"  {fname:<28} {len(svg) / 1024:6.1f} KB")

    if STATIC:
        write_preview(entries)
        print(f"STATIC=1: final frames written to {OUT_DIR.relative_to(ROOT)}/ (open {OUT_DIR.name}/index.html)")
    else:
        write_readme(entries)
        print(f"wrote {len(entries)} cards to {OUT_DIR.relative_to(ROOT)}/ and refreshed README.md")
    shutil.rmtree(FB.tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
