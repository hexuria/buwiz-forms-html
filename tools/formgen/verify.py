#!/usr/bin/env python3
"""Round-trip verifier: print our HTML to PDF, re-extract, and diff IR against IR.

This replaces pixel diffing outright. The old gate rasterised the official PDF
and compared it to a Chromium screenshot; because none of the source PDFs embed
their primary faces, Poppler substituted glyphs and glyph-outline *shape* became
the majority of the residual -- a quantity no layout change can move. Here the
comparison never leaves vector space: both sides go through extract.py, so what
is compared is border geometry, font identity, size, origin, baseline and
advance. Those are what layout *is*. Outlines are never consulted, so an
unembedded source face costs us nothing.

Two shapes of use:

    # our HTML against the official IR (the real gate)
    python3 tools/formgen/verify.py --reference build/ir/2551q-2018.ir.json \
        --html build/html/2551q-2018.html

    # two PDFs against each other (smoke test, needs no HTML)
    python3 tools/formgen/verify.py --reference official.pdf --candidate other.pdf

    # prove the differ can actually fail
    python3 tools/formgen/verify.py --self-test

Tolerances are strict by construction: 0.25pt position, 0.05pt thickness, 0.10pt
advance, 0.01pt size. They are CLI-overridable because a *deliberate*, argued
loosening is sometimes the honest call -- but widening one to turn a red run
green is the exact failure this pipeline exists to escape. Don't.

Text is compared by *visible glyphs*, not by run boundaries. Chromium's PDF
writer chunks spans as it likes -- it hands back "Use Only   Item:   " for two
separate elements, and "1", " 6", " 5" for one -- so positions are taken from
the first and last non-space glyph origins, and a stretch of text is allowed to
be spelled by several consecutive runs on either side. What is never relaxed is
what the glyphs must spell: the concatenation has to reproduce the other side's
visible characters exactly, in order, so a truncated, altered or absent string
still reports missing. Static text correctness is the one thing standing between
this pipeline and a wrong tax rate on a printed form.

Those two ends are not the whole run, so *every* glyph between them is located
too (`Interior`). A layer that reproduces a run's total advance by spreading it
evenly -- which is exactly fonts.py's one-letter-spacing-per-run model -- passes
origin, end and advance while putting every interior glyph somewhere else; the
corpus round-trip carries such runs at up to 7.44pt. Whitespace is still not
compared at a seam, but *ink position* is compared everywhere.

Determinism: the report is a pure function of the two IRs. The candidate PDF
itself is not byte-reproducible (Chromium stamps a creation date), which is why
the diff ignores the IR's `source` and `generator` blocks entirely.

Measured on 2551Q, and load-bearing for emit.py: Chromium snaps CSS *box*
geometry to the 1px = 0.75pt device grid when printing, so a `background`/
`border` rule asked for at y=111.50pt thickness 0.48pt comes back at y=111.75pt
thickness 0.75pt -- 0.25pt and 0.27pt out, both over tolerance, on a rule that
was specified exactly. The same rectangles drawn as `<svg><rect>` round-trip to
the last hundredth of a point. Rules therefore have to be painted through SVG;
that is not a tolerance problem and must not be answered with a wider tolerance.
"""

from __future__ import annotations

import argparse
import bisect
import copy
import json
import math
import pathlib
import shutil
import subprocess
import sys
from dataclasses import dataclass
from typing import Any, Iterable, Sequence

_HERE = pathlib.Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import extract  # noqa: E402  - sibling module, imported not shelled out

REPORT_VERSION = 1

# Strict defaults. See the module docstring before touching any of these.
DEFAULT_POSITION_TOL_PT = 0.25
DEFAULT_THICKNESS_TOL_PT = 0.05
DEFAULT_ADVANCE_TOL_PT = 0.10
DEFAULT_SIZE_TOL_PT = 0.01

PT_PER_INCH = 72.0

# Only these roles are gated by default. Knockouts and decorative greys are
# extracted and reported but a missing white-on-white bar is not a layout
# defect; CLAUDE.md's grey-vs-black lesson is the whole reason role exists.
DEFAULT_ROLES = ("structural",)

DEFAULT_SELF_TEST_PDF = pathlib.Path(
    "/Users/uriah/Downloads/forms/2551Qv2018/2551Q Jan 2018 ENCS final rev 3_copy.pdf")

CHROMIUM_CANDIDATES = (
    "chromium",
    "chromium-browser",
    "chrome",
    "google-chrome",
    "google-chrome-stable",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing",
)


@dataclass(frozen=True)
class Tolerances:
    """Every numeric slack in the comparison, in points."""

    position: float = DEFAULT_POSITION_TOL_PT
    thickness: float = DEFAULT_THICKNESS_TOL_PT
    advance: float = DEFAULT_ADVANCE_TOL_PT
    size: float = DEFAULT_SIZE_TOL_PT

    def to_ir(self) -> dict[str, float]:
        return {
            "position_pt": self.position,
            "thickness_pt": self.thickness,
            "advance_pt": self.advance,
            "size_pt": self.size,
        }


def r4(value: float | None) -> float | None:
    """Round a delta for reporting. Keeps the JSON stable across platforms."""
    return None if value is None else round(float(value) + 0.0, 4)


# ---------------------------------------------------------------------------
# HTML -> PDF
# ---------------------------------------------------------------------------


def _inches(points: float) -> str:
    """Chromium's print API takes px/in/cm/mm and rejects 'pt' outright."""
    return f"{points / PT_PER_INCH:.6f}in"


def _playwright_importable() -> bool:
    try:
        import playwright.sync_api  # noqa: F401
    except Exception:  # noqa: BLE001 - any import failure means "not usable"
        return False
    return True


def _find_chromium(explicit: str | None) -> str | None:
    if explicit:
        found = shutil.which(explicit) or (explicit if pathlib.Path(explicit).is_file() else None)
        if not found:
            raise FileNotFoundError(f"chromium binary not found: {explicit}")
        return found
    for candidate in CHROMIUM_CANDIDATES:
        found = shutil.which(candidate) or (candidate if pathlib.Path(candidate).is_file() else None)
        if found:
            return found
    return None


def _print_with_playwright(html_path: pathlib.Path, out_pdf: pathlib.Path,
                           width_pt: float, height_pt: float) -> None:
    from playwright.sync_api import sync_playwright

    zero = {"top": "0", "bottom": "0", "left": "0", "right": "0"}
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            page = browser.new_page()
            page.goto(html_path.resolve().as_uri(), wait_until="load")
            # Print media so @media print rules apply, exactly as the user sees.
            page.emulate_media(media="print")
            page.pdf(
                path=str(out_pdf),
                width=_inches(width_pt),
                height=_inches(height_pt),
                margin=zero,
                print_background=True,
                # The IR's paper block is authoritative; a stray @page size in
                # the HTML must not silently resize the candidate.
                prefer_css_page_size=False,
                scale=1.0,
            )
        finally:
            browser.close()


def _print_with_chromium_cli(binary: str, html_path: pathlib.Path, out_pdf: pathlib.Path,
                             width_pt: float, height_pt: float, timeout_s: int) -> None:
    """--print-to-pdf has no page-size flag, so the size has to arrive via CSS.

    The shim is written *next to* the source HTML so every relative asset
    reference still resolves, and is removed again on the way out.
    """
    shim = html_path.with_name(f"{html_path.stem}.formgen-print.html")
    style = (f'\n<style>@page {{ size: {width_pt}pt {height_pt}pt; margin: 0; }}'
             f' html, body {{ margin: 0; }}</style>\n')
    shim.write_text(html_path.read_text(encoding="utf-8") + style, encoding="utf-8")
    try:
        result = subprocess.run(
            [
                binary,
                "--headless=new",
                "--disable-gpu",
                "--no-pdf-header-footer",
                "--run-all-compositor-stages-before-draw",
                "--virtual-time-budget=10000",
                f"--print-to-pdf={out_pdf}",
                shim.resolve().as_uri(),
            ],
            capture_output=True, text=True, timeout=timeout_s, check=False)
        if not out_pdf.is_file():
            raise RuntimeError(
                f"chromium produced no PDF (exit {result.returncode})\n{result.stderr.strip()}")
    finally:
        shim.unlink(missing_ok=True)


def html_to_pdf(html_path: pathlib.Path, out_pdf: pathlib.Path,
                width_pt: float, height_pt: float,
                chromium_binary: str | None = None, timeout_s: int = 120) -> pathlib.Path:
    """Render `html_path` to `out_pdf` at exactly width_pt x height_pt.

    Zero margins, backgrounds on, scale 1. The page size comes from the IR's
    paper block and nowhere else -- assuming A4 or Letter would silently shift
    every coordinate in the diff by the paper difference.
    """
    if not html_path.is_file():
        raise FileNotFoundError(f"no such HTML: {html_path}")
    out_pdf.parent.mkdir(parents=True, exist_ok=True)
    out_pdf.unlink(missing_ok=True)

    if chromium_binary is None and _playwright_importable():
        _print_with_playwright(html_path, out_pdf, width_pt, height_pt)
        return out_pdf

    binary = _find_chromium(chromium_binary)
    if binary is None:
        raise RuntimeError(
            "no way to render HTML to PDF. Either install the Playwright Python "
            "package with a Chromium build (`pip install playwright && "
            "playwright install chromium`), or provide a Chromium/Chrome binary "
            "on PATH or via --chromium (tried: " + ", ".join(CHROMIUM_CANDIDATES) + ")")
    _print_with_chromium_cli(binary, html_path, out_pdf, width_pt, height_pt, timeout_s)
    return out_pdf


# ---------------------------------------------------------------------------
# Matching primitives
# ---------------------------------------------------------------------------


def greedy_assign(pairs: Sequence[tuple[float, int, int]], ref_count: int,
                  cand_count: int) -> tuple[list[tuple[int, int, float]], list[int], list[int]]:
    """Assign reference index -> candidate index, cheapest feasible pair first.

    Deterministic: `pairs` is sorted by (cost, ref index, candidate index), so
    ties always resolve the same way regardless of enumeration order.
    """
    matched: list[tuple[int, int, float]] = []
    used_ref: set[int] = set()
    used_cand: set[int] = set()
    for cost, i, j in sorted(pairs):
        if i in used_ref or j in used_cand:
            continue
        used_ref.add(i)
        used_cand.add(j)
        matched.append((i, j, cost))
    matched.sort()
    missing = [i for i in range(ref_count) if i not in used_ref]
    extra = [j for j in range(cand_count) if j not in used_cand]
    return matched, missing, extra


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------


def rule_geometry(rule: dict[str, Any]) -> tuple[float, float, float, float]:
    """(cross-axis centre, span start, span end, thickness).

    The centre, not an edge, is the position: that keeps a stroke that is merely
    too thick from also reading as displaced. CLAUDE.md records three separate
    occasions where weight was misreported as displacement.
    """
    x0, y0, x1, y1 = rule["x0"], rule["y0"], rule["x1"], rule["y1"]
    if rule["axis"] == "h":
        return ((y0 + y1) / 2.0, x0, x1, y1 - y0)
    return ((x0 + x1) / 2.0, y0, y1, x1 - x0)


def _rule_cost(a: tuple[float, float, float, float],
               b: tuple[float, float, float, float]) -> float:
    return max(abs(a[0] - b[0]), abs(a[1] - b[1]), abs(a[2] - b[2]))


def _rule_brief(rule: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": rule.get("id"),
        "axis": rule["axis"],
        "x0": rule["x0"], "y0": rule["y0"], "x1": rule["x1"], "y1": rule["y1"],
        "thickness_pt": rule["thickness_pt"],
        "gray": rule.get("gray"),
        "role": rule.get("role"),
    }


def diff_rules(ref_rules: Sequence[dict[str, Any]], cand_rules: Sequence[dict[str, Any]],
               tol: Tolerances) -> dict[str, Any]:
    ref_geo = [rule_geometry(r) for r in ref_rules]
    cand_geo = [rule_geometry(r) for r in cand_rules]

    pairs: list[tuple[float, int, int]] = []
    for axis in ("h", "v"):
        cand_idx = sorted((j for j, r in enumerate(cand_rules) if r["axis"] == axis),
                          key=lambda j: cand_geo[j][0])
        positions = [cand_geo[j][0] for j in cand_idx]
        for i, rule in enumerate(ref_rules):
            if rule["axis"] != axis:
                continue
            lo = bisect.bisect_left(positions, ref_geo[i][0] - tol.position)
            hi = bisect.bisect_right(positions, ref_geo[i][0] + tol.position)
            for j in cand_idx[lo:hi]:
                cost = _rule_cost(ref_geo[i], cand_geo[j])
                if cost <= tol.position:
                    pairs.append((cost, i, j))

    matched, missing, extra = greedy_assign(pairs, len(ref_rules), len(cand_rules))

    max_pos = 0.0
    max_thick = 0.0
    thickness_violations: list[dict[str, Any]] = []
    for i, j, cost in matched:
        d_thick = abs(ref_geo[i][3] - cand_geo[j][3])
        max_pos = max(max_pos, cost)
        max_thick = max(max_thick, d_thick)
        if d_thick > tol.thickness:
            thickness_violations.append({
                "reference": _rule_brief(ref_rules[i]),
                "candidate": _rule_brief(cand_rules[j]),
                "thickness_delta_pt": r4(d_thick),
                "position_delta_pt": r4(cost),
            })

    # A rule reported "missing" because its span is 3pt short is useless without
    # saying so, hence the nearest-unmatched diagnostic. It never affects the
    # verdict -- it only makes the verdict actionable.
    unmatched_cand = set(extra)
    missing_out: list[dict[str, Any]] = []
    for i in missing:
        nearest: dict[str, Any] | None = None
        best = math.inf
        for j in unmatched_cand:
            if cand_rules[j]["axis"] != ref_rules[i]["axis"]:
                continue
            cost = _rule_cost(ref_geo[i], cand_geo[j])
            if cost < best:
                best = cost
                nearest = {
                    "candidate": _rule_brief(cand_rules[j]),
                    "position_delta_pt": r4(cost),
                    "start_delta_pt": r4(cand_geo[j][1] - ref_geo[i][1]),
                    "end_delta_pt": r4(cand_geo[j][2] - ref_geo[i][2]),
                    "thickness_delta_pt": r4(cand_geo[j][3] - ref_geo[i][3]),
                }
        missing_out.append({"reference": _rule_brief(ref_rules[i]), "nearest_candidate": nearest})

    thickness_violations.sort(key=lambda v: (-(v["thickness_delta_pt"] or 0.0),
                                             v["reference"]["y0"], v["reference"]["x0"]))
    return {
        "reference_count": len(ref_rules),
        "candidate_count": len(cand_rules),
        "matched": len(matched),
        "missing": missing_out,
        "extra": [_rule_brief(cand_rules[j]) for j in extra],
        "thickness_violations": thickness_violations,
        "max_position_delta_pt": r4(max_pos),
        "max_thickness_delta_pt": r4(max_thick),
        "ok": not missing_out and not extra and not thickness_violations,
    }


# ---------------------------------------------------------------------------
# Text
# ---------------------------------------------------------------------------

_STYLE_SUFFIXES = ("bolditalic", "boldoblique", "bold", "italic", "oblique", "regular")

# Run boundaries are not a property of the page. Printing two absolutely
# positioned elements that contain no whitespace at all, then re-extracting,
# gives all three of these purely as a function of the gap between them:
#
#     gap 0.5pt -> one run, "32TOTAL"     (merged, no separator)
#     gap 1.5pt -> one run, "32 TOTAL"    (merged, separator invented)
#     gap 2.5pt -> two runs, "32", "TOTAL"
#
# So the reference and the candidate can disagree about run boundaries and about
# whether a space exists at one, while painting identical ink. Reporting that as
# a missing string buries real defects under measurement noise.
#
# It is not licence to ignore whitespace. Only the *seam* between consecutive
# runs is treated as unknown. Within a part, spacing is still compared exactly;
# across a block, the visible characters must be reproduced exactly and in
# order, with nothing left over; and every part on the finer-grained side gets
# its own position and advance checked. A string that was truncated, altered,
# reordered or dropped still fails, which is the entire point. This caps how
# many runs one accepted block may span.
MAX_MERGE_PARTS = 8


def normalise_text(text: str) -> str:
    """Collapse whitespace and strip. HTML emits NBSP where the PDF used spaces.

    This settles *identity* only -- whether two stretches of page carry the same
    string. Position and extent never come from it: they come from the first and
    last non-space glyph origins, so a run whose padding spaces went missing
    still reports the truth about where its ink starts and stops.

    str.split() and str.isspace() agree on every code point, NBSP included,
    which is what lets _visible_indices trim with the same notion of whitespace
    that this collapses with.
    """
    return " ".join(text.split())


def _visible_indices(text: str) -> list[int]:
    """Character indices of the glyphs that put ink on the page."""
    return [i for i, ch in enumerate(text) if not ch.isspace()]


def _dense(text: str) -> str:
    """Every visible character, with all whitespace removed.

    Used only to decide whether a *block* of runs spells the same thing as
    another block. Seam spaces are invented and destroyed by the extractor (see
    MAX_MERGE_PARTS above), so they cannot take part in that decision -- but
    spacing inside a part is still compared with normalise_text once the block
    has been segmented, so nothing here loosens what a single run must say.
    """
    return "".join(text.split())


def _origin_offsets(run: dict[str, Any]) -> list[float]:
    """Each glyph's origin as an offset from its run's origin.

    extract.py records these exactly. An IR extracted before that field existed
    falls back to summing char_advances_pt, which is right only to the
    accumulated quantisation of the prefix -- exact at index 0, so every
    unmerged run is unaffected, but drifting up to 0.86pt deep inside a long
    run. Re-extract rather than lean on the fallback.
    """
    exact = run.get("char_origin_offsets_pt")
    if exact:
        return [float(v) for v in exact]
    offsets = [0.0]
    for advance in list(run.get("char_advances_pt") or [])[:-1]:
        offsets.append(offsets[-1] + float(advance))
    return offsets


def normalise_family(name: str) -> str:
    """Fold PostScript naming noise so 'ArialMT' == 'Arial'.

    Only naming conventions are folded, never distinct families: 'Arial Narrow'
    stays distinct from 'Arial', which is the difference that actually moves a
    line of text.
    """
    if "+" in name[:8]:
        name = name.split("+", 1)[1]
    folded = "".join(ch for ch in name.lower() if ch.isalnum())
    for suffix in ("psmt", "mt", "ps"):
        if folded.endswith(suffix) and len(folded) > len(suffix):
            folded = folded[: -len(suffix)]
            break
    changed = True
    while changed:
        changed = False
        for token in _STYLE_SUFFIXES:
            if folded.endswith(token) and len(folded) > len(token):
                folded = folded[: -len(token)]
                changed = True
                break
    return folded


def _style_hints(font_name: str) -> tuple[bool, bool]:
    folded = font_name.lower()
    bold = any(t in folded for t in ("bold", "black", "heavy"))
    italic = any(t in folded for t in ("italic", "oblique"))
    return bold, italic


def run_style(run: dict[str, Any]) -> tuple[str, bool, bool]:
    hint_bold, hint_italic = _style_hints(str(run.get("font", "")))
    return (
        normalise_family(str(run.get("family") or run.get("font") or "")),
        bool(run.get("bold")) or hint_bold,
        bool(run.get("italic")) or hint_italic,
    )


@dataclass(frozen=True)
class Placement:
    """Where one stretch of visible glyphs actually sits on the page.

    `origin_x` is the origin of the first NON-SPACE glyph and `end_x` the right
    edge of the last, so a run that swallowed a neighbour's trailing space and
    one that did not are described identically. That is the opposite of ignoring
    whitespace: padding spaces put no ink on paper, and where the *next* string
    starts is checked by that string's own placement, not by this one's padding.
    """

    text: str
    run: dict[str, Any]
    origin_x: float
    end_x: float
    baseline_y: float

    @property
    def advance_pt(self) -> float:
        return self.end_x - self.origin_x


class Strip:
    """Consecutive runs from one side, flattened to their visible glyphs.

    Merging and splitting are one problem seen from two directions, so both
    sides are held in the same shape and compared glyph-for-glyph. Whichever
    side the rasteriser chunked differently, the glyph sequence is identical
    whenever the normalised strings are, because collapsing whitespace cannot
    change which non-space characters appear or in what order.
    """

    __slots__ = ("runs", "glyphs", "offsets", "widths")

    def __init__(self, runs: Sequence[dict[str, Any]]) -> None:
        self.runs = list(runs)
        self.offsets: list[list[float]] = []
        self.widths: list[list[float]] = []
        for run in self.runs:
            count = len(run["text"])
            # A short or absent array degrades to a zero offset rather than an
            # exception: a malformed IR should surface as a position delta.
            self.offsets.append((_origin_offsets(run) + [0.0] * count)[:count])
            self.widths.append(
                ([float(w) for w in (run.get("char_widths_pt") or [])] + [0.0] * count)[:count])
        self.glyphs = [(index, char) for index, run in enumerate(self.runs)
                       for char in _visible_indices(run["text"])]

    def placement(self, first: int, last: int) -> Placement:
        """Placement of this strip's visible glyphs [first, last], inclusive."""
        run0, char0 = self.glyphs[first]
        run1, char1 = self.glyphs[last]
        pieces = []
        for index in range(run0, run1 + 1):
            text = self.runs[index]["text"]
            start = char0 if index == run0 else 0
            stop = char1 + 1 if index == run1 else len(text)
            pieces.append(text[start:stop])
        return Placement(
            text="".join(pieces),
            run=self.runs[run0],
            origin_x=float(self.runs[run0]["origin_x"] or 0.0) + self.offsets[run0][char0],
            end_x=(float(self.runs[run1]["origin_x"] or 0.0)
                   + self.offsets[run1][char1] + self.widths[run1][char1]),
            baseline_y=float(self.runs[run0]["baseline_y"] or 0.0),
        )

    def glyph_range(self, index: int) -> tuple[int, int] | None:
        """The [first, last] glyph positions contributed by runs[index]."""
        positions = [k for k, (run, _) in enumerate(self.glyphs) if run == index]
        return (positions[0], positions[-1]) if positions else None

    def glyph_x(self, index: int) -> float:
        """Absolute page x of one visible glyph's origin."""
        run, char = self.glyphs[index]
        return float(self.runs[run]["origin_x"] or 0.0) + self.offsets[run][char]

    def glyph_char(self, index: int) -> str:
        """The character one visible glyph carries, for naming it in a report."""
        run, char = self.glyphs[index]
        return self.runs[run]["text"][char]


@dataclass(frozen=True)
class Interior:
    """The worst-placed glyph strictly inside a segment, and where it sits.

    A segment's outer measurements -- origin_x, end_x, advance -- pin only its
    two ends. Everything between them was unchecked, and that is not a
    hypothetical gap: fonts.py derives one uniform `letter-spacing` per run as
    (measured_advance - natural_advance) / gaps, whose whole justification is
    that "every glyph origin still lands where the PDF put it, which is what
    verify.py compares". That claim is only true when the source's own tracking
    is uniform. Where the PDF concentrated its extra advance in one gap -- a
    wide word space, a single TJ offset -- the uniform model reproduces the
    outer extent exactly while moving every interior glyph, so the run passed
    both ends and the advance while its ink sat elsewhere. Measured on the
    corpus round-trip that reaches 7.44pt, thirty times the position tolerance.

    Glyph 0 is excluded because it *is* origin_x and already has its own reason;
    everything after it is what nothing else looks at.

    This is the one comparison that leans hard on `char_origin_offsets_pt`. An
    IR old enough to lack it falls back to summing quantised advances, which
    drifts up to 0.86pt deep inside a long run -- so on such an IR this reports
    the fallback's accumulation rather than a defect. Re-extract; do not widen
    the tolerance.
    """

    delta_pt: float
    index: int          # position within the segment, 0 = the segment's origin
    char: str

    @classmethod
    def between(cls, reference: Strip, candidate: Strip, first: int, stop: int) -> "Interior":
        worst = cls(0.0, 0, "")
        for position in range(first + 1, stop):
            delta = abs(reference.glyph_x(position) - candidate.glyph_x(position))
            if delta > worst.delta_pt:
                worst = cls(delta, position - first, reference.glyph_char(position))
        return worst


@dataclass(frozen=True)
class Pairing:
    """One stretch of reference glyphs against the candidate glyphs carrying it.

    Usually a whole run on both sides. Where the extractor chunked one side more
    finely it is a segment of a run, so the comparison always happens at the
    finer of the two resolutions.
    """

    ref_index: int
    reference: Placement
    candidate: Placement
    parts: int          # runs spanned on the side the extractor chunked differently
    interior: Interior  # worst glyph between the segment's two measured ends


def _anchor(run: dict[str, Any]) -> tuple[float, float]:
    """Where a run's ink starts: first non-space glyph origin, and baseline."""
    visible = _visible_indices(run["text"])
    offsets = _origin_offsets(run)
    index = visible[0] if visible else 0
    return (float(run.get("origin_x") or run["x0"]) + (offsets[index]
                                                       if index < len(offsets) else 0.0),
            float(run.get("baseline_y") or run["y1"]))


def _run_brief(run: dict[str, Any]) -> dict[str, Any]:
    return {
        "text": run["text"],
        "font": run.get("font"),
        "family": run.get("family"),
        "size_pt": run.get("size_pt"),
        "bold": bool(run.get("bold")),
        "italic": bool(run.get("italic")),
        "origin_x": run.get("origin_x"),
        "baseline_y": run.get("baseline_y"),
        "measured_advance_pt": run.get("measured_advance_pt"),
    }


def _placement_brief(placement: Placement) -> dict[str, Any]:
    run = placement.run
    return {
        "text": placement.text,
        "font": run.get("font"),
        "family": run.get("family"),
        "size_pt": run.get("size_pt"),
        "bold": bool(run.get("bold")),
        "italic": bool(run.get("italic")),
        "origin_x": r4(placement.origin_x),
        "baseline_y": r4(placement.baseline_y),
        "measured_advance_pt": r4(placement.advance_pt),
    }


def _pair_block(ref_runs: Sequence[dict[str, Any]], cand_runs: Sequence[dict[str, Any]],
                ref_block: Sequence[int], cand_block: Sequence[int]) -> list[Pairing] | None:
    """Line two blocks up glyph-for-glyph and cut them at every part boundary.

    The segmentation is the union of both sides' boundaries, so whichever side
    the extractor chunked more finely is the one that sets the resolution: three
    candidate runs carrying one reference run get three position and advance
    checks, not one check of their outer extent. Every segment therefore lies
    inside exactly one run on each side, which is what makes the internal-spacing
    comparison below meaningful -- the only whitespace it cannot see is the
    whitespace at a seam, which is the only whitespace the extractor invents.

    Returns None if the two sides do not line up, or if any segment's internal
    spacing differs. The caller then keeps reporting both sides: a block this
    cannot explain must never be silently dropped.
    """
    ref_strip = Strip([ref_runs[i] for i in ref_block])
    cand_strip = Strip([cand_runs[j] for j in cand_block])
    total = len(ref_strip.glyphs)
    if not total or total != len(cand_strip.glyphs):
        return None

    cuts = {0, total}
    owner: dict[int, int] = {}
    for local, ref_index in enumerate(ref_block):
        bounds = ref_strip.glyph_range(local)
        if bounds is None:
            return None
        cuts.add(bounds[0])
        for position in range(bounds[0], bounds[1] + 1):
            owner[position] = ref_index
    for local in range(len(cand_block)):
        bounds = cand_strip.glyph_range(local)
        if bounds is None:
            return None
        cuts.add(bounds[0])

    pairings: list[Pairing] = []
    parts = max(len(ref_block), len(cand_block))
    edges = sorted(cuts)
    for first, stop in zip(edges, edges[1:]):
        reference = ref_strip.placement(first, stop - 1)
        candidate = cand_strip.placement(first, stop - 1)
        if normalise_text(reference.text) != normalise_text(candidate.text):
            return None
        pairings.append(Pairing(ref_index=owner[first], reference=reference,
                                candidate=candidate, parts=parts,
                                interior=Interior.between(ref_strip, cand_strip,
                                                          first, stop)))
    return pairings


def _grow_block(runs: Sequence[dict[str, Any]], free: set[int], start: int,
                target: str) -> list[int] | None:
    """Smallest run of >=2 consecutive free runs from `start` that spells `target`.

    `target` and the growing concatenation are both whitespace-free, because the
    seam is exactly where the extractor's invented spaces live. Growth stops the
    moment the concatenation stops being a prefix of the target, which is sound
    because appending text can only lengthen the result, never shorten it.
    """
    dense = ""
    block: list[int] = []
    for index in range(start, min(start + MAX_MERGE_PARTS, len(runs))):
        if index not in free:
            break
        block.append(index)
        dense += _dense(runs[index]["text"])
        if len(block) >= 2 and dense == target:
            return block
        if not target.startswith(dense):
            break
    return None


def _reconcile_chunking(ref_runs: Sequence[dict[str, Any]], ref_dense: Sequence[str],
                        cand_runs: Sequence[dict[str, Any]], cand_dense: Sequence[str],
                        free_ref: set[int], free_cand: set[int]
                        ) -> list[tuple[list[int], list[int]]]:
    """Pair leftovers where one side chunked a stretch of text differently.

    Two shapes are attempted, both anchored in page order: several consecutive
    reference runs carried by one candidate run, and one reference run carried
    by several consecutive candidate runs. A block must be consecutive, made
    entirely of still-unmatched runs, and spell the other side's string exactly.
    N-to-M is deliberately not attempted -- leaving it reported is the safe
    direction to be wrong in.

    Mutates `free_ref` / `free_cand`, which are the caller's missing and extra
    sets, so anything this cannot explain stays reported.
    """
    blocks: list[tuple[list[int], list[int]]] = []

    for j in sorted(free_cand):
        for start in sorted(free_ref):
            block = _grow_block(ref_runs, free_ref, start, cand_dense[j])
            if block is not None:
                blocks.append((block, [j]))
                free_ref.difference_update(block)
                free_cand.discard(j)
                break

    for i in sorted(free_ref):
        for start in sorted(free_cand):
            block = _grow_block(cand_runs, free_cand, start, ref_dense[i])
            if block is not None:
                blocks.append(([i], block))
                free_cand.difference_update(block)
                free_ref.discard(i)
                break

    return blocks


def diff_text(ref_runs: Sequence[dict[str, Any]], cand_runs: Sequence[dict[str, Any]],
              tol: Tolerances) -> dict[str, Any]:
    ref_keys = [normalise_text(run["text"]) for run in ref_runs]
    cand_keys = [normalise_text(run["text"]) for run in cand_runs]

    groups: dict[str, tuple[list[int], list[int]]] = {}
    for i, key in enumerate(ref_keys):
        groups.setdefault(key, ([], []))[0].append(i)
    for j, key in enumerate(cand_keys):
        groups.setdefault(key, ([], []))[1].append(j)

    # Identical strings repeat (2551Q page 1 carries the decimal point 16 times),
    # so within a text group runs are paired by nearest anchor, not by order.
    pairs: list[tuple[float, int, int]] = []
    for ref_idx, cand_idx in groups.values():
        for i in ref_idx:
            ax, ay = _anchor(ref_runs[i])
            for j in cand_idx:
                bx, by = _anchor(cand_runs[j])
                pairs.append((math.hypot(ax - bx, ay - by), i, j))

    matched, missing, extra = greedy_assign(pairs, len(ref_runs), len(cand_runs))
    free_ref, free_cand = set(missing), set(extra)

    blocks = [([i], [j]) for i, j, _cost in matched]
    blocks.extend(_reconcile_chunking(ref_runs, [_dense(t) for t in ref_keys],
                                      cand_runs, [_dense(t) for t in cand_keys],
                                      free_ref, free_cand))

    pairings: list[Pairing] = []
    reconciled = 0
    for ref_block, cand_block in blocks:
        resolved = _pair_block(ref_runs, cand_runs, ref_block, cand_block)
        if resolved is None:
            # Reconciliation proposed it; the glyph-level check refused it. Both
            # sides go back to being reported, which is the only safe outcome.
            free_ref.update(ref_block)
            free_cand.update(cand_block)
            continue
        reconciled += max(len(ref_block), len(cand_block)) > 1
        pairings.extend(resolved)
    pairings.sort(key=lambda p: p.ref_index)

    mismatched: list[dict[str, Any]] = []
    max_origin = 0.0
    max_baseline = 0.0
    max_advance = 0.0
    max_size = 0.0
    max_interior = 0.0
    for pairing in pairings:
        ref, cand = pairing.reference, pairing.candidate
        ref_family, ref_bold, ref_italic = run_style(ref.run)
        cand_family, cand_bold, cand_italic = run_style(cand.run)

        d_origin = abs(ref.origin_x - cand.origin_x)
        d_baseline = abs(ref.baseline_y - cand.baseline_y)
        d_advance = abs(ref.advance_pt - cand.advance_pt)
        d_size = abs(float(ref.run["size_pt"] or 0.0) - float(cand.run["size_pt"] or 0.0))
        max_origin = max(max_origin, d_origin)
        max_baseline = max(max_baseline, d_baseline)
        max_advance = max(max_advance, d_advance)
        max_size = max(max_size, d_size)
        max_interior = max(max_interior, pairing.interior.delta_pt)

        reasons: list[str] = []
        if ref_family != cand_family:
            reasons.append(f"family {ref_family!r} != {cand_family!r}")
        if ref_bold != cand_bold:
            reasons.append(f"bold {ref_bold} != {cand_bold}")
        if ref_italic != cand_italic:
            reasons.append(f"italic {ref_italic} != {cand_italic}")
        if d_size > tol.size:
            reasons.append(f"size Δ{d_size:.4f}pt")
        if d_origin > tol.position:
            reasons.append(f"origin_x Δ{d_origin:.4f}pt")
        if d_baseline > tol.position:
            reasons.append(f"baseline_y Δ{d_baseline:.4f}pt")
        if d_advance > tol.advance:
            reasons.append(f"advance Δ{d_advance:.4f}pt")
        if pairing.interior.delta_pt > tol.position:
            reasons.append(f"glyph {pairing.interior.index} "
                           f"{pairing.interior.char!r} origin "
                           f"Δ{pairing.interior.delta_pt:.4f}pt")
        if reasons:
            entry = {
                "reference": _placement_brief(ref),
                "candidate": _placement_brief(cand),
                "reasons": reasons,
                "origin_x_delta_pt": r4(d_origin),
                "baseline_y_delta_pt": r4(d_baseline),
                "size_delta_pt": r4(d_size),
                "advance_delta_pt": r4(d_advance),
                "interior_glyph_delta_pt": r4(pairing.interior.delta_pt),
                "interior_glyph_index": pairing.interior.index,
            }
            if pairing.parts > 1:
                # Say so: a reader comparing this against the raw IR would
                # otherwise not find a run with these bounds on either side.
                entry["rasteriser_chunked_parts"] = pairing.parts
            mismatched.append(entry)

    mismatched.sort(key=lambda m: (m["reference"]["baseline_y"] or 0.0,
                                   m["reference"]["origin_x"] or 0.0))
    return {
        "reference_count": len(ref_runs),
        "candidate_count": len(cand_runs),
        "matched": len({pairing.ref_index for pairing in pairings}),
        "reconciled_chunkings": reconciled,
        "missing": [_run_brief(ref_runs[i]) for i in sorted(free_ref)],
        "extra": [_run_brief(cand_runs[j]) for j in sorted(free_cand)],
        "mismatched": mismatched,
        "max_origin_x_delta_pt": r4(max_origin),
        "max_baseline_y_delta_pt": r4(max_baseline),
        "max_advance_delta_pt": r4(max_advance),
        "max_size_delta_pt": r4(max_size),
        # Reported next to the others because it is the only one that describes
        # the inside of a run. A page can carry a perfect max_origin_x and a
        # multi-point max_interior_glyph: same ink volume, wrong ink positions.
        "max_interior_glyph_delta_pt": r4(max_interior),
        "ok": not free_ref and not free_cand and not mismatched,
    }


# ---------------------------------------------------------------------------
# Images
# ---------------------------------------------------------------------------


def _image_brief(image: dict[str, Any]) -> dict[str, Any]:
    return {
        "sha256": image["sha256"],
        "name": image.get("name"),
        "x0": image["x0"], "y0": image["y0"], "x1": image["x1"], "y1": image["y1"],
        "bytes": image.get("bytes"),
    }


def diff_images(ref_images: Sequence[dict[str, Any]], cand_images: Sequence[dict[str, Any]],
                tol: Tolerances) -> dict[str, Any]:
    def identity(image: dict[str, Any]) -> str:
        """What makes two images "the same picture".

        The decoded-pixel hash, when both sides carry one. Chromium re-encodes
        every image it prints, so the compressed-stream hash always differs even
        when the samples are byte-identical -- comparing streams reported nine
        forms as missing artwork that was present and correct. Falls back to the
        stream hash so an undecodable XObject still matches itself rather than
        silently matching everything.
        """
        return image.get("pixel_sha256") or image["sha256"]

    groups: dict[str, tuple[list[int], list[int]]] = {}
    for i, image in enumerate(ref_images):
        groups.setdefault(identity(image), ([], []))[0].append(i)
    for j, image in enumerate(cand_images):
        groups.setdefault(identity(image), ([], []))[1].append(j)

    pairs: list[tuple[float, int, int]] = []
    for ref_idx, cand_idx in groups.values():
        for i in ref_idx:
            for j in cand_idx:
                cost = math.hypot(ref_images[i]["x0"] - cand_images[j]["x0"],
                                  ref_images[i]["y0"] - cand_images[j]["y0"])
                pairs.append((cost, i, j))

    matched, missing, extra = greedy_assign(pairs, len(ref_images), len(cand_images))

    placement: list[dict[str, Any]] = []
    max_delta = 0.0
    for i, j, _cost in matched:
        ref, cand = ref_images[i], cand_images[j]
        deltas = {k: cand[k] - ref[k] for k in ("x0", "y0", "x1", "y1")}
        worst = max(abs(v) for v in deltas.values())
        max_delta = max(max_delta, worst)
        if worst > tol.position:
            placement.append({
                "reference": _image_brief(ref),
                "candidate": _image_brief(cand),
                "deltas_pt": {k: r4(v) for k, v in deltas.items()},
                "max_delta_pt": r4(worst),
            })

    return {
        "reference_count": len(ref_images),
        "candidate_count": len(cand_images),
        "matched": len(matched),
        "missing": [_image_brief(ref_images[i]) for i in missing],
        "extra": [_image_brief(cand_images[j]) for j in extra],
        "placement_violations": placement,
        "max_placement_delta_pt": r4(max_delta),
        "ok": not missing and not extra and not placement,
    }


# ---------------------------------------------------------------------------
# Whole-document diff
# ---------------------------------------------------------------------------


def _paper_diff(reference: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    """Paper is exact-or-nothing: a resize shifts every coordinate downstream."""
    problems: list[str] = []
    ref_paper, cand_paper = reference["paper"], candidate["paper"]
    ref_pages, cand_pages = len(reference["pages"]), len(candidate["pages"])
    if ref_pages != cand_pages:
        problems.append(f"page count {ref_pages} != {cand_pages}")
    for key in ("width_pt", "height_pt"):
        if ref_paper[key] != cand_paper[key]:
            problems.append(f"paper {key} {ref_paper[key]} != {cand_paper[key]}")
    for index in range(min(ref_pages, cand_pages)):
        ref_page, cand_page = reference["pages"][index], candidate["pages"][index]
        for key in ("width_pt", "height_pt", "rotation"):
            if ref_page[key] != cand_page[key]:
                problems.append(
                    f"page {index + 1} {key} {ref_page[key]} != {cand_page[key]}")
    return {
        "reference": {"width_pt": ref_paper["width_pt"], "height_pt": ref_paper["height_pt"],
                      "page_count": ref_pages},
        "candidate": {"width_pt": cand_paper["width_pt"], "height_pt": cand_paper["height_pt"],
                      "page_count": cand_pages},
        "problems": problems,
        "ok": not problems,
    }


def _select_rules(page: dict[str, Any], roles: Sequence[str]) -> list[dict[str, Any]]:
    if "all" in roles:
        return list(page["rules"])
    allowed = set(roles)
    return [r for r in page["rules"] if r.get("role") in allowed]


def _font_inventory(ir: dict[str, Any]) -> list[str]:
    families = {normalise_family(str(f.get("family") or name))
                for name, f in ir.get("fonts", {}).items()}
    return sorted(families)


def diff_ir(reference_ir: dict[str, Any], candidate_ir: dict[str, Any],
            tolerances: Tolerances | None = None,
            roles: Sequence[str] = DEFAULT_ROLES) -> dict[str, Any]:
    """Diff two extract.py IRs. Pure function; no I/O, no clocks, no randomness."""
    tol = tolerances or Tolerances()
    paper = _paper_diff(reference_ir, candidate_ir)

    report: dict[str, Any] = {
        "report_version": REPORT_VERSION,
        "form": reference_ir.get("form"),
        "tolerances": tol.to_ir(),
        "gated_roles": list(roles),
        "paper": paper,
        "fonts": {
            "reference_families": _font_inventory(reference_ir),
            "candidate_families": _font_inventory(candidate_ir),
        },
        "pages": [],
        "totals": {},
        "ok": False,
    }
    report["fonts"]["missing_families"] = sorted(
        set(report["fonts"]["reference_families"]) - set(report["fonts"]["candidate_families"]))

    if not paper["ok"]:
        # Hard failure: comparing geometry across different paper would produce a
        # long list of plausible-looking deltas that all have one cause.
        report["hard_failure"] = "paper mismatch"
        report["totals"] = {"rules_missing": 0, "rules_extra": 0, "rules_thickness_violations": 0,
                            "text_missing": 0, "text_extra": 0, "text_mismatched": 0,
                            "images_missing": 0, "images_extra": 0,
                            "images_placement_violations": 0}
        return report

    totals = {k: 0 for k in ("rules_missing", "rules_extra", "rules_thickness_violations",
                             "text_missing", "text_extra", "text_mismatched",
                             "images_missing", "images_extra", "images_placement_violations")}
    for ref_page, cand_page in zip(reference_ir["pages"], candidate_ir["pages"]):
        rules = diff_rules(_select_rules(ref_page, roles), _select_rules(cand_page, roles), tol)
        text = diff_text(ref_page["text_runs"], cand_page["text_runs"], tol)
        images = diff_images(ref_page["images"], cand_page["images"], tol)
        totals["rules_missing"] += len(rules["missing"])
        totals["rules_extra"] += len(rules["extra"])
        totals["rules_thickness_violations"] += len(rules["thickness_violations"])
        totals["text_missing"] += len(text["missing"])
        totals["text_extra"] += len(text["extra"])
        totals["text_mismatched"] += len(text["mismatched"])
        totals["images_missing"] += len(images["missing"])
        totals["images_extra"] += len(images["extra"])
        totals["images_placement_violations"] += len(images["placement_violations"])
        report["pages"].append({
            "index": ref_page["index"],
            "rules": rules,
            "text": text,
            "images": images,
            "ok": rules["ok"] and text["ok"] and images["ok"],
        })

    report["totals"] = totals
    report["ok"] = paper["ok"] and all(p["ok"] for p in report["pages"])
    return report


# ---------------------------------------------------------------------------
# Human-readable report
# ---------------------------------------------------------------------------


def _bullets(items: Iterable[str], limit: int, stream: Any) -> None:
    items = list(items)
    for line in items[:limit]:
        print(f"      {line}", file=stream)
    if len(items) > limit:
        print(f"      … and {len(items) - limit} more", file=stream)


def print_report(report: dict[str, Any], stream: Any = sys.stdout, limit: int = 12) -> None:
    form = report.get("form") or {}
    print(f"form {form.get('code', '?')} rev {form.get('revision', '?')}   "
          f"tolerances pos {report['tolerances']['position_pt']}pt / "
          f"thk {report['tolerances']['thickness_pt']}pt / "
          f"adv {report['tolerances']['advance_pt']}pt / "
          f"size {report['tolerances']['size_pt']}pt", file=stream)

    paper = report["paper"]
    verdict = "OK" if paper["ok"] else "HARD FAILURE"
    print(f"paper  {paper['reference']['width_pt']}x{paper['reference']['height_pt']}pt "
          f"{paper['reference']['page_count']}pp vs "
          f"{paper['candidate']['width_pt']}x{paper['candidate']['height_pt']}pt "
          f"{paper['candidate']['page_count']}pp   {verdict}", file=stream)
    if not paper["ok"]:
        _bullets(paper["problems"], limit, stream)
        print("VERDICT: FAIL (paper)", file=stream)
        return

    missing_families = report["fonts"]["missing_families"]
    if missing_families:
        print(f"fonts  families absent from candidate: {', '.join(missing_families)}", file=stream)

    for page in report["pages"]:
        rules, text, images = page["rules"], page["text"], page["images"]
        print(f"page {page['index']}  gated rules {rules['matched']}/{rules['reference_count']} "
              f"matched  missing {len(rules['missing'])}  extra {len(rules['extra'])}  "
              f"maxΔpos {rules['max_position_delta_pt']}pt  "
              f"maxΔthk {rules['max_thickness_delta_pt']}pt", file=stream)
        _bullets(
            (f"missing {m['reference']['axis']} rule "
             f"({m['reference']['x0']},{m['reference']['y0']})-"
             f"({m['reference']['x1']},{m['reference']['y1']}) "
             f"thk {m['reference']['thickness_pt']}"
             + (f"  nearest Δpos {m['nearest_candidate']['position_delta_pt']} "
                f"Δstart {m['nearest_candidate']['start_delta_pt']} "
                f"Δend {m['nearest_candidate']['end_delta_pt']}"
                if m["nearest_candidate"] else "  (no unmatched candidate on this axis)")
             for m in rules["missing"]), limit, stream)
        _bullets((f"extra {e['axis']} rule ({e['x0']},{e['y0']})-({e['x1']},{e['y1']}) "
                  f"thk {e['thickness_pt']} role {e['role']}" for e in rules["extra"]),
                 limit, stream)
        _bullets((f"thickness {v['reference']['axis']} rule "
                  f"({v['reference']['x0']},{v['reference']['y0']}) "
                  f"Δ{v['thickness_delta_pt']}pt "
                  f"({v['reference']['thickness_pt']} -> {v['candidate']['thickness_pt']})"
                  for v in rules["thickness_violations"]), limit, stream)

        print(f"         text {text['matched']}/{text['reference_count']} matched  "
              f"missing {len(text['missing'])}  extra {len(text['extra'])}  "
              f"mismatched {len(text['mismatched'])}  "
              f"rechunked {text.get('reconciled_chunkings', 0)}  "
              f"maxΔorigin {text['max_origin_x_delta_pt']}pt  "
              f"maxΔbaseline {text['max_baseline_y_delta_pt']}pt  "
              f"maxΔadv {text['max_advance_delta_pt']}pt  "
              f"maxΔglyph {text.get('max_interior_glyph_delta_pt')}pt", file=stream)
        _bullets((f"missing text {m['text']!r} @ ({m['origin_x']},{m['baseline_y']}) "
                  f"{m['family']} {m['size_pt']}pt" for m in text["missing"]), limit, stream)
        _bullets((f"extra text {e['text']!r} @ ({e['origin_x']},{e['baseline_y']}) "
                  f"{e['family']} {e['size_pt']}pt" for e in text["extra"]), limit, stream)
        _bullets((f"text {m['reference']['text']!r} @ "
                  f"({m['reference']['origin_x']},{m['reference']['baseline_y']}): "
                  + "; ".join(m["reasons"]) for m in text["mismatched"]), limit, stream)

        print(f"         images {images['matched']}/{images['reference_count']} matched  "
              f"missing {len(images['missing'])}  extra {len(images['extra'])}  "
              f"maxΔplacement {images['max_placement_delta_pt']}pt", file=stream)
        _bullets((f"missing image {m['sha256'][:12]}… @ ({m['x0']},{m['y0']})"
                  for m in images["missing"]), limit, stream)
        _bullets((f"extra image {e['sha256'][:12]}… @ ({e['x0']},{e['y0']})"
                  for e in images["extra"]), limit, stream)
        _bullets((f"image {v['reference']['sha256'][:12]}… placement Δ{v['max_delta_pt']}pt"
                  for v in images["placement_violations"]), limit, stream)

    totals = report["totals"]
    print("totals " + "  ".join(f"{k} {v}" for k, v in sorted(totals.items())), file=stream)
    print(f"VERDICT: {'PASS' if report['ok'] else 'FAIL'}", file=stream)


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def load_ir(path: pathlib.Path, form_code: str, revision: str) -> dict[str, Any]:
    """Accept either an extracted IR JSON or a PDF (extracted on the spot)."""
    if path.suffix.lower() == ".pdf":
        return extract.extract(path, form_code, revision, None)
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------


def _first_structural_index(page: dict[str, Any], axis: str) -> int:
    for i, rule in enumerate(page["rules"]):
        if rule["role"] == "structural" and rule["axis"] == axis:
            return i
    raise AssertionError(f"no structural {axis} rule on page {page['index']}")


def _fake_run(text: str, origin_x: float, baseline_y: float, pitch: float = 4.0,
              size: float = 8.0) -> dict[str, Any]:
    """A text run with self-consistent glyph metrics, for the text-differ cases.

    Every glyph is `pitch` wide, so each expected delta below is arithmetic a
    reader can do in their head rather than a number copied out of a run.
    """
    offsets = [round(i * pitch, 2) for i in range(len(text))]
    return {
        "text": text, "font": "ArialMT", "family": "Arial", "size_pt": size,
        "bold": False, "italic": False,
        "x0": origin_x, "y0": baseline_y - size,
        "x1": origin_x + pitch * len(text), "y1": baseline_y,
        "origin_x": origin_x, "baseline_y": baseline_y,
        "measured_advance_pt": round(pitch * len(text), 2),
        "char_origin_offsets_pt": offsets,
        "char_advances_pt": [pitch] * len(text),
        "char_widths_pt": [pitch] * len(text),
    }


def _chunk_as_one(*runs: dict[str, Any], separator: str = "") -> dict[str, Any]:
    """Re-emit several runs as the single span the extractor would hand back.

    Only the chunking changes: every glyph keeps the absolute x it had, so a
    part that was displaced before the merge is still displaced after it. That
    is what makes the merged cases below able to fail.

    `separator` reproduces the space the extractor invents at a seam. It is
    given exactly the gap it sits in, so it moves no glyph -- which is precisely
    why it must not decide whether the strings match.
    """
    first = runs[0]
    pieces: list[str] = []
    offsets: list[float] = []
    widths: list[float] = []
    for run in runs:
        base = float(run["origin_x"]) - float(first["origin_x"])
        if separator and offsets:
            seam = offsets[-1] + widths[-1]
            pieces.append(separator)
            offsets.append(round(seam, 2))
            widths.append(round(base - seam, 2))
        pieces.append(run["text"])
        offsets.extend(round(base + off, 2) for off in run["char_origin_offsets_pt"])
        widths.extend(float(w) for w in run["char_widths_pt"])
    merged = dict(first)
    merged["text"] = "".join(pieces)
    merged["char_origin_offsets_pt"] = offsets
    merged["char_widths_pt"] = widths
    merged["char_advances_pt"] = [round(offsets[i + 1] - offsets[i], 2)
                                  for i in range(len(offsets) - 1)] + [widths[-1]]
    merged["measured_advance_pt"] = round(offsets[-1] + widths[-1], 2)
    merged["x1"] = float(first["origin_x"]) + merged["measured_advance_pt"]
    return merged


def text_differ_cases(check: Any) -> None:
    """Prove the whitespace handling matches merges without matching mistakes.

    Half of these assert a *catch*. A differ that pairs a merged run with its
    reference is only worth having if it still refuses to pair a truncated one,
    so the negative cases here are the load-bearing ones.
    """
    tol = Tolerances()

    # "Use Only   " ends at 10 + 11*4 = 54, where "Item:   " begins.
    use_only = _fake_run("Use Only   ", 10.0, 50.0)
    item = _fake_run("Item:   ", 54.0, 50.0)

    absorbed = diff_text([use_only, item], [_chunk_as_one(use_only, item)], tol)
    check("a run that absorbed a neighbour's trailing space is matched",
          absorbed["ok"] and absorbed["matched"] == 2
          and absorbed["reconciled_chunkings"] == 1,
          f"missing {len(absorbed['missing'])} extra {len(absorbed['extra'])} "
          f"mismatched {len(absorbed['mismatched'])}")

    # The second part's first glyph must still be located at 54, not at the
    # merged span's origin of 10 -- so moving it 1pt has to be caught.
    moved = diff_text([use_only, item],
                      [_chunk_as_one(use_only, _fake_run("Item:   ", 55.0, 50.0))], tol)
    check("a displaced part inside a merged run is caught at its own origin",
          len(moved["mismatched"]) == 1
          and abs((moved["mismatched"][0]["origin_x_delta_pt"] or 0.0) - 1.0) < 1e-6
          and any("origin_x" in r for r in moved["mismatched"][0]["reasons"]),
          f"{len(moved['mismatched'])} mismatched")

    # Advance is measured over the same substring on both sides: widening only
    # the first part must show up on the first part alone.
    widened = diff_text(
        [use_only, item],
        [_chunk_as_one(_fake_run("Use Only   ", 10.0, 50.0, pitch=4.06), item)], tol)
    check("advance is measured on the merged part, not the whole span",
          len(widened["mismatched"]) == 1
          and widened["mismatched"][0]["reference"]["text"] == "Use Only"
          and abs((widened["mismatched"][0]["advance_delta_pt"] or 0.0) - 0.48) < 5e-3,
          f"{[m['reasons'] for m in widened['mismatched']]}")

    # A seam space the extractor invented sits in the gap and moves nothing, so
    # it must not decide whether the two sides carry the same string. "32" ends
    # at 19.56 + 2*5.02 = 29.6 and "TOTAL" starts at 32.16, a 2.56pt gap -- the
    # exact geometry that produced a synthetic space on 1601-FQ.
    number = _fake_run("32", 19.56, 140.0, pitch=5.02)
    caption = _fake_run("TOTAL AMOUNT STILL DUE", 32.16, 140.0)
    seam = diff_text([number, caption],
                     [_chunk_as_one(number, caption, separator=" ")], tol)
    check("a space invented at a seam does not make the string missing",
          seam["ok"] and seam["matched"] == 2 and seam["reconciled_chunkings"] == 1,
          f"missing {len(seam['missing'])} extra {len(seam['extra'])} "
          f"mismatched {len(seam['mismatched'])}")

    # Seam-blindness stops at the seam: spacing *inside* a part still counts.
    inner = diff_text(
        [_fake_run("Fine ", 10.0, 150.0), _fake_run("P= 10,000", 30.0, 150.0)],
        [_chunk_as_one(_fake_run("Fine ", 10.0, 150.0),
                       _fake_run("P=10,000", 30.0, 150.0))], tol)
    check("spacing lost inside a part is still missing",
          not inner["ok"] and len(inner["missing"]) == 2 and len(inner["extra"]) == 1,
          f"missing {[m['text'] for m in inner['missing']]}")

    # The other direction: one reference run spelled by three candidate runs.
    split = diff_text(
        [_fake_run("1 6 5 ", 20.0, 60.0)],
        [_fake_run("1", 20.0, 60.0), _fake_run(" 6", 24.0, 60.0),
         _fake_run(" 5", 32.0, 60.0)], tol)
    check("a reference run split across candidate runs is matched",
          split["ok"] and split["matched"] == 1 and split["reconciled_chunkings"] == 1,
          f"missing {len(split['missing'])} extra {len(split['extra'])} "
          f"mismatched {len(split['mismatched'])}")

    # ...and the split side sets the resolution, so a middle piece that drifted
    # cannot hide inside the outer extent of the run that carries it.
    drifted = diff_text(
        [_fake_run("1 6 5 ", 20.0, 60.0)],
        [_fake_run("1", 20.0, 60.0), _fake_run(" 6", 25.0, 60.0),
         _fake_run(" 5", 32.0, 60.0)], tol)
    check("a drifted middle piece of a split run is caught",
          not drifted["ok"] and any("origin_x" in reason
                                    for m in drifted["mismatched"] for reason in m["reasons"]),
          f"{[m['reasons'] for m in drifted['mismatched']]}")

    # Leading spaces: the anchor is the first *visible* glyph, so padding that
    # renders 1pt wider per space displaces the ink even though the run origin
    # is untouched. The old run-origin comparison could not see this.
    padded = _fake_run("   10D", 20.0, 70.0)
    wide_pad = dict(padded)
    wide_pad["char_origin_offsets_pt"] = [0.0, 5.0, 10.0, 15.0, 19.0, 23.0]
    wide_pad["char_advances_pt"] = [5.0, 5.0, 5.0, 4.0, 4.0, 4.0]
    padding = diff_text([padded], [wide_pad], tol)
    check("wider leading padding is caught as a visible-origin shift",
          len(padding["mismatched"]) == 1
          and abs((padding["mismatched"][0]["origin_x_delta_pt"] or 0.0) - 3.0) < 1e-6,
          f"{[m['reasons'] for m in padding['mismatched']]}")

    # Redistributed tracking: the run keeps its origin, its end and therefore its
    # advance, and moves every glyph in between. This is what one uniform
    # letter-spacing does to a source that put all its extra advance in one gap,
    # and until Interior existed nothing in this file looked at it. "2nd" is
    # 5pt per glyph over a 20pt advance: the source spends the 5pt of tracking
    # entirely in the first gap, the even model spends 2.5pt in each, so glyph 1
    # lands 2.5pt right of where the PDF put it while both ends are exact.
    def _tracked(offsets: list[float]) -> dict[str, Any]:
        run = _fake_run("2nd", 30.0, 160.0, pitch=5.0)
        run["char_origin_offsets_pt"] = offsets
        run["char_advances_pt"] = [round(offsets[i + 1] - offsets[i], 2)
                                   for i in range(len(offsets) - 1)] + [5.0]
        run["measured_advance_pt"] = round(offsets[-1] + 5.0, 2)
        run["x1"] = 30.0 + run["measured_advance_pt"]
        return run

    retracked = diff_text([_tracked([0.0, 10.0, 15.0])],
                          [_tracked([0.0, 7.5, 15.0])], tol)
    worst = retracked["mismatched"][0] if retracked["mismatched"] else {}
    check("evenly redistributed tracking is caught on the interior glyph",
          not retracked["ok"] and len(retracked["mismatched"]) == 1
          and abs((worst.get("origin_x_delta_pt") or 0.0)) < 1e-6
          and abs((worst.get("advance_delta_pt") or 0.0)) < 1e-6
          and abs((worst.get("interior_glyph_delta_pt") or 0.0) - 2.5) < 1e-6
          and worst.get("interior_glyph_index") == 1
          and any("glyph 1" in reason for reason in worst.get("reasons", [])),
          f"{retracked['mismatched']}")

    check("an interior glyph delta is reported even when nothing is mismatched",
          "max_interior_glyph_delta_pt" in retracked
          and abs((retracked["max_interior_glyph_delta_pt"] or 0.0) - 2.5) < 1e-6,
          f"{retracked.get('max_interior_glyph_delta_pt')}")

    # The corpus's three unmatched runs, to the hundredth of a point: 2550Q's
    # ' 2nd ' against what our own print round-trips to. The source spends 6pt
    # of tracking in the gap after the leading space; the even model spends
    # ~1.5pt in each of four gaps, which both moves every glyph AND opens gaps
    # wide enough that the extractor invents a space between them. The invented
    # spaces must not reconcile it away, and -- the load-bearing half -- the
    # geometry must condemn it on its own, with the invented spaces removed.
    source_2nd = _fake_run(" 2nd ", 481.18, 116.8, size=9.0)
    source_2nd["char_origin_offsets_pt"] = [0.0, 8.59, 13.59, 18.59, 23.62]
    source_2nd["char_advances_pt"] = [8.59, 5.0, 5.0, 5.03, 2.5]
    source_2nd["char_widths_pt"] = [2.5, 5.0, 5.0, 5.0, 2.5]
    source_2nd["measured_advance_pt"] = 26.12

    printed_2nd = _fake_run(" 2 n d  ", 481.17, 116.8, size=9.0)
    printed_2nd["char_origin_offsets_pt"] = [0.0, 4.03, 9.03, 10.56, 15.56,
                                             17.09, 22.09, 23.62]
    printed_2nd["char_advances_pt"] = [4.03, 5.0, 1.53, 5.0, 1.53, 5.0, 1.53, 2.49]
    printed_2nd["char_widths_pt"] = [2.49, 5.0, 1.53, 5.0, 1.53, 5.0, 1.53, 2.49]
    printed_2nd["measured_advance_pt"] = 26.11

    redistributed = diff_text([source_2nd], [printed_2nd], tol)
    check("a short padded run whose glyphs were redistributed stays reported",
          not redistributed["ok"] and len(redistributed["missing"]) == 1
          and len(redistributed["extra"]) == 1
          and redistributed["reconciled_chunkings"] == 0,
          f"missing {[m['text'] for m in redistributed['missing']]} "
          f"extra {[e['text'] for e in redistributed['extra']]}")

    without_invented = dict(printed_2nd, text=" 2nd ",
                            char_origin_offsets_pt=[0.0, 4.03, 10.56, 17.09, 23.62],
                            char_advances_pt=[4.03, 6.53, 6.53, 6.53, 2.49],
                            char_widths_pt=[2.49, 5.0, 5.0, 5.0, 2.49])
    geometry_only = diff_text([source_2nd], [without_invented], tol)
    check("the same redistribution is condemned by geometry alone",
          not geometry_only["ok"] and len(geometry_only["mismatched"]) == 1
          and abs((geometry_only["mismatched"][0]["origin_x_delta_pt"] or 0.0)
                  - 4.57) < 5e-3,
          f"{[m['reasons'] for m in geometry_only['mismatched']]}")

    # The guard on all of the above: whatever the differ learns to forgive about
    # short padded strings, one that is simply NOT on the candidate page must
    # still be missing -- even with a sibling of the same shape sitting beside
    # it to be mistaken for it. ' No ' is 1800's real string; it fails this
    # pipeline's whole purpose if it can be answered by ' 2nd '.
    no_run = _fake_run(" No ", 231.17, 371.81, size=9.0)
    dropped = diff_text([no_run, source_2nd], [source_2nd], tol)
    check("a short padded string absent from the candidate is still missing",
          not dropped["ok"] and [m["text"] for m in dropped["missing"]] == [" No "]
          and not dropped["extra"],
          f"missing {[m['text'] for m in dropped['missing']]}")

    # --- and now the cases that must NOT be explained away -------------------

    truncated = diff_text(
        [_fake_run("quarter ", 20.0, 80.0),
         _fake_run("Any person required under the NIRC", 52.0, 80.0)],
        [_chunk_as_one(_fake_run("quarter ", 20.0, 80.0),
                       _fake_run("Any person required", 52.0, 80.0))], tol)
    check("a merged run that truncated its text is still missing",
          not truncated["ok"] and len(truncated["missing"]) == 2
          and len(truncated["extra"]) == 1 and truncated["reconciled_chunkings"] == 0,
          f"missing {len(truncated['missing'])} extra {len(truncated['extra'])}")

    altered = diff_text(
        [_fake_run("23", 19.0, 90.0), _fake_run("TOTAL AMOUNT STILL DUE", 27.0, 90.0)],
        [_chunk_as_one(_fake_run("23", 19.0, 90.0),
                       _fake_run("TOTAL AMOUNT STILL DUF", 27.0, 90.0))], tol)
    check("a merged run whose visible text differs is still missing",
          not altered["ok"] and len(altered["missing"]) == 2 and len(altered["extra"]) == 1,
          f"missing {len(altered['missing'])} extra {len(altered['extra'])}")

    absent = diff_text([_fake_run("Alpha", 10.0, 100.0), _fake_run("Beta", 40.0, 100.0)],
                       [_fake_run("Alpha", 10.0, 100.0)], tol)
    check("a run absent from the candidate is still missing",
          not absent["ok"] and [m["text"] for m in absent["missing"]] == ["Beta"],
          f"missing {[m['text'] for m in absent['missing']]}")

    # A rate that reads 12% instead of 2% is the failure this whole file exists
    # to prevent, and it differs from its reference by one glyph inside a merge.
    rate = diff_text(
        [_fake_run("Tax Rate ", 10.0, 110.0), _fake_run("2%", 46.0, 110.0)],
        [_chunk_as_one(_fake_run("Tax Rate ", 10.0, 110.0),
                       _fake_run("12%", 46.0, 110.0))], tol)
    check("a changed tax rate inside a merged run is still missing",
          not rate["ok"] and len(rate["missing"]) == 2,
          f"missing {[m['text'] for m in rate['missing']]}")

    # Reordering is not chunking: the same strings in the wrong order must not
    # be reconciled into a match.
    reordered = diff_text(
        [_fake_run("Alpha ", 10.0, 120.0), _fake_run("Beta", 34.0, 120.0)],
        [_chunk_as_one(_fake_run("Beta ", 10.0, 120.0), _fake_run("Alpha", 30.0, 120.0))], tol)
    check("reordered strings are not reconciled as a merge",
          not reordered["ok"] and len(reordered["missing"]) == 2,
          f"missing {[m['text'] for m in reordered['missing']]}")

    repeated = diff_text([_fake_run("Alpha", 10.0, 130.0)] * 2,
                        [_fake_run("Alpha", 10.0, 130.0)], tol)
    check("a duplicated reference run is not satisfied twice by one candidate",
          not repeated["ok"] and len(repeated["missing"]) == 1)

    first = json.dumps(diff_text([use_only, item],
                                 [_chunk_as_one(use_only, item)], tol), sort_keys=True)
    second = json.dumps(diff_text([use_only, item],
                                  [_chunk_as_one(use_only, item)], tol), sort_keys=True)
    check("merged-run report is deterministic", first == second)


def self_test(pdf_path: pathlib.Path, stream: Any = sys.stderr) -> int:
    """Prove the differ is both silent on identity and loud on perturbation.

    A differ that cannot fail certifies nothing, so every check below that
    asserts a *catch* is as load-bearing as the clean-diff check.
    """
    failures: list[str] = []

    def check(name: str, condition: bool, detail: str = "") -> None:
        print(f"  {'PASS' if condition else 'FAIL'}  {name}"
              + (f"  {detail}" if detail else ""), file=stream)
        if not condition:
            failures.append(name)

    # These need no PDF, so they run even when the pinned source is absent.
    print("text differ", file=stream)
    text_differ_cases(check)

    if not pdf_path.is_file():
        print(f"self-test needs the pinned source PDF for the rest: {pdf_path}\n"
              f"pass --pdf if it lives elsewhere", file=stream)
        return 1 if failures else 2

    print(f"self-test on {pdf_path.name}", file=stream)
    reference = extract.extract(pdf_path, "2551Q", "2018", None)
    candidate = extract.extract(pdf_path, "2551Q", "2018", None)

    check("extract.py is deterministic (byte-identical IR on re-extraction)",
          json.dumps(reference, sort_keys=False) == json.dumps(candidate, sort_keys=False))

    tol = Tolerances()
    clean = diff_ir(reference, candidate, tol)
    totals = clean["totals"]
    check("self-diff passes", clean["ok"])
    check("self-diff has no missing/extra/mismatched", sum(totals.values()) == 0, str(totals))
    zero_deltas = all(
        page["rules"]["max_position_delta_pt"] == 0.0
        and page["rules"]["max_thickness_delta_pt"] == 0.0
        and page["text"]["max_origin_x_delta_pt"] == 0.0
        and page["text"]["max_baseline_y_delta_pt"] == 0.0
        and page["text"]["max_advance_delta_pt"] == 0.0
        and page["text"]["max_size_delta_pt"] == 0.0
        and page["text"]["max_interior_glyph_delta_pt"] == 0.0
        and page["images"]["max_placement_delta_pt"] == 0.0
        for page in clean["pages"])
    check("self-diff deltas are exactly zero", zero_deltas)
    check("self-diff actually compared something",
          clean["pages"] and clean["pages"][0]["rules"]["matched"] > 100
          and clean["pages"][0]["text"]["matched"] > 100,
          f"page 1: {clean['pages'][0]['rules']['matched']} rules, "
          f"{clean['pages'][0]['text']['matched']} runs")

    # Perturbation 1: displace one horizontal structural rule by 0.5pt (2x tol).
    shifted = copy.deepcopy(candidate)
    page1 = shifted["pages"][0]
    idx = _first_structural_index(page1, "h")
    moved = page1["rules"][idx]
    moved["y0"] = round(moved["y0"] + 0.5, 2)
    moved["y1"] = round(moved["y1"] + 0.5, 2)

    # Perturbation 2: fatten one vertical structural rule about its own centre,
    # which leaves position clean and isolates the weight-vs-displacement axis.
    vidx = _first_structural_index(page1, "v")
    fattened = page1["rules"][vidx]
    fattened["x0"] = round(fattened["x0"] - 0.1, 2)
    fattened["x1"] = round(fattened["x1"] + 0.1, 2)
    fattened["thickness_pt"] = round(fattened["x1"] - fattened["x0"], 2)

    # Perturbation 3: change one text run's size.
    resized = page1["text_runs"][0]
    original_size = resized["size_pt"]
    resized["size_pt"] = round(original_size + 1.0, 3)

    caught = diff_ir(reference, shifted, tol)
    check("perturbed diff fails", not caught["ok"])
    page = caught["pages"][0]
    check("displaced rule is caught (missing + extra)",
          len(page["rules"]["missing"]) >= 1 and len(page["rules"]["extra"]) >= 1,
          f"missing {len(page['rules']['missing'])}, extra {len(page['rules']['extra'])}")
    check("displaced rule reports its 0.5pt offset via nearest-candidate",
          any(m["nearest_candidate"] is not None
              and abs((m["nearest_candidate"]["position_delta_pt"] or 0.0) - 0.5) < 0.011
              for m in page["rules"]["missing"]))
    check("thickened rule is caught as a thickness violation, not a displacement",
          any(abs((v["thickness_delta_pt"] or 0.0) - 0.2) < 0.011
              for v in page["rules"]["thickness_violations"]),
          f"{len(page['rules']['thickness_violations'])} thickness violations")
    check("resized run is caught with the right delta",
          any(abs((m["size_delta_pt"] or 0.0) - 1.0) < 1e-6
              and any("size" in reason for reason in m["reasons"])
              for m in page["text"]["mismatched"]),
          f"{len(page['text']['mismatched'])} mismatched runs")

    # A sub-tolerance nudge must stay quiet, or the tolerances mean nothing.
    nudged = copy.deepcopy(candidate)
    nudge_page = nudged["pages"][0]
    nidx = _first_structural_index(nudge_page, "h")
    nudge = nudge_page["rules"][nidx]
    nudge["y0"] = round(nudge["y0"] + 0.1, 2)
    nudge["y1"] = round(nudge["y1"] + 0.1, 2)
    quiet = diff_ir(reference, nudged, tol)
    check("a 0.1pt nudge stays within the 0.25pt tolerance", quiet["ok"])

    # Paper is a hard failure with no page diffing behind it.
    truncated = copy.deepcopy(candidate)
    truncated["pages"] = truncated["pages"][:1]
    paper_fail = diff_ir(reference, truncated, tol)
    check("page-count change is an immediate hard failure",
          not paper_fail["ok"] and paper_fail.get("hard_failure") == "paper mismatch"
          and not paper_fail["pages"])

    resized_paper = copy.deepcopy(candidate)
    resized_paper["paper"]["width_pt"] = 595.0
    resized_paper["pages"][0]["width_pt"] = 595.0
    check("paper resize is an immediate hard failure",
          not diff_ir(reference, resized_paper, tol)["ok"])

    # Report shape must be JSON-clean and stable, since CI consumes it.
    first = json.dumps(diff_ir(reference, shifted, tol), sort_keys=True)
    second = json.dumps(diff_ir(reference, shifted, tol), sort_keys=True)
    check("report is deterministic and JSON-serialisable", first == second)

    print(f"self-test: {len(failures)} failure(s)", file=stream)
    return 1 if failures else 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--reference", type=pathlib.Path,
                        help="Official IR JSON, or a PDF to extract on the spot.")
    parser.add_argument("--candidate", type=pathlib.Path,
                        help="Candidate IR JSON or PDF. Mutually exclusive with --html.")
    parser.add_argument("--html", type=pathlib.Path,
                        help="Generated HTML; printed to PDF and extracted.")
    parser.add_argument("--candidate-pdf", type=pathlib.Path, default=None,
                        help="Where --html is printed (default: alongside the HTML).")
    parser.add_argument("--chromium", default=None,
                        help="Chromium/Chrome binary; forces the CLI path over Playwright.")
    parser.add_argument("--form-code", default=None)
    parser.add_argument("--revision", default=None)
    parser.add_argument("--roles", default=",".join(DEFAULT_ROLES),
                        help="Rule roles to gate: comma list, or 'all'.")
    parser.add_argument("--tol-position", type=float, default=DEFAULT_POSITION_TOL_PT)
    parser.add_argument("--tol-thickness", type=float, default=DEFAULT_THICKNESS_TOL_PT)
    parser.add_argument("--tol-advance", type=float, default=DEFAULT_ADVANCE_TOL_PT)
    parser.add_argument("--tol-size", type=float, default=DEFAULT_SIZE_TOL_PT)
    parser.add_argument("--json", nargs="?", const="-", default=None, metavar="PATH",
                        help="Machine-readable report; bare flag writes to stdout.")
    parser.add_argument("--limit", type=int, default=12,
                        help="Findings printed per category before eliding.")
    parser.add_argument("--self-test", action="store_true",
                        help="Diff the pinned 2551Q against itself, then prove the differ fails.")
    parser.add_argument("--pdf", type=pathlib.Path, default=DEFAULT_SELF_TEST_PDF,
                        help="Source PDF for --self-test.")
    args = parser.parse_args(argv)

    if args.self_test:
        return self_test(args.pdf)

    if not args.reference:
        parser.error("--reference is required (or use --self-test)")
    if bool(args.candidate) == bool(args.html):
        parser.error("give exactly one of --candidate or --html")
    if not args.reference.is_file():
        print(f"no such reference: {args.reference}", file=sys.stderr)
        return 2

    tol = Tolerances(position=args.tol_position, thickness=args.tol_thickness,
                     advance=args.tol_advance, size=args.tol_size)
    roles = tuple(part.strip() for part in args.roles.split(",") if part.strip())

    reference_ir = load_ir(args.reference, args.form_code or "UNKNOWN", args.revision or "UNKNOWN")
    form = reference_ir.get("form", {})
    form_code = args.form_code or form.get("code", "UNKNOWN")
    revision = args.revision or form.get("revision", "UNKNOWN")

    if args.html:
        if not args.html.is_file():
            print(f"no such HTML: {args.html}", file=sys.stderr)
            return 2
        out_pdf = args.candidate_pdf or args.html.with_suffix(".candidate.pdf")
        try:
            html_to_pdf(args.html, out_pdf, reference_ir["paper"]["width_pt"],
                        reference_ir["paper"]["height_pt"], chromium_binary=args.chromium)
        except (RuntimeError, FileNotFoundError) as exc:
            print(str(exc), file=sys.stderr)
            return 2
        candidate_ir = extract.extract(out_pdf, form_code, revision, None)
    else:
        if not args.candidate.is_file():
            print(f"no such candidate: {args.candidate}", file=sys.stderr)
            return 2
        candidate_ir = load_ir(args.candidate, form_code, revision)

    report = diff_ir(reference_ir, candidate_ir, tol, roles)
    payload = json.dumps(report, indent=2, ensure_ascii=False) + "\n"

    if args.json == "-":
        sys.stdout.write(payload)
        print_report(report, stream=sys.stderr, limit=args.limit)
    else:
        if args.json:
            path = pathlib.Path(args.json)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(payload, encoding="utf-8")
        print_report(report, stream=sys.stdout, limit=args.limit)

    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
