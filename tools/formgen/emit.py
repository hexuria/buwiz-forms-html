#!/usr/bin/env python3
"""Emit a self-contained HTML form from the IR + box model + font plan.

This is the generator half of the pipeline. Everything it writes is derived
from numbers the PDF itself carries, so nothing here is traced, eyeballed or
tuned: `extract.py` says where the ink is, `lattice.py` says what the ink
*means*, `fonts.py` says which face reproduces the advances, and this module
turns those three into markup. `verify.py` then prints the markup back to PDF,
re-extracts it with the same extractor and diffs IR against IR. No raster is
produced or consulted at any point.

Two rule backends are implemented, because the choice is not obvious and the
project's architecture depends on measuring it rather than assuming it:

  --rule-backend svg  one inline <svg> per page, every rule a <rect> in page
                      units. Measured: SVG rects round-trip through Chromium
                      print-to-PDF at zero delta on every edge.
  --rule-backend css  every rule an absolutely positioned div painted with
                      background-color (never `border`, whose shorthand is
                      snapped and collapsed separately). Measured: Chromium
                      snaps CSS box geometry to the 0.75pt device grid when
                      printing, so 0.24 / 0.48 / 1.44pt all collapse onto
                      {0.75, 1.5} and positions drift up to ~0.27pt.

Apart from the rule layer the two documents are byte-for-byte the same idea:
same page boxes, same text layer, same field layer, same growable template.
That is deliberate -- it is what makes the round-trip a controlled comparison
of the rule layer alone.

One sheet, two documents
------------------------

Given a `guides.py` plan this module emits either half of the sheet:

  --document form   everything the plan does *not* claim. Page boxes, page count
                    and @page are unchanged, so a page whose lower 70% became
                    empty keeps its full height. That is the point: the form's
                    geometry has to stay bit-identical, and the freed space is
                    what a growable band expands into.
  --document guide  only what the plan claims, plus any standalone guide PDF.

A straddler is claimed by nobody and therefore stays on the form. Losing a rule
off the form is a geometry regression; a duplicated rule on the guide is
cosmetic. With no `--guide-plan` the form document is byte-identical to what
this module emitted before the split existed, which `--self-test` asserts.

The guide does not need parity, and one of its pages is actively wrong without
reflowing: 1603Q's guideline block is two columns of 6pt prose, and placing
those as positioned runs is what makes them overlap. `--guide-layout reflow`
(the default for the guide) groups the runs into reading order and emits
flowing HTML, which fixes the overlap by construction rather than by nudging
coordinates. `--guide-layout absolute` keeps the positioned form.

A guide is a document people print, so the reflowed guide carries an `@page` of
its own: the form's paper, with a reading margin instead of the form's
`margin:0`. A standalone guide PDF is reflowed into the same document from its
own extraction (`--guide-source`) rather than embedded with `<object>`, because
an embedded PDF is a second document with its own pagination and does not print
with the one around it. The pinned PDF stays beside the HTML and is linked.

Usage:
    python3 tools/formgen/emit.py \
        --ir build/ir/2551q-2018.ir.json \
        --layout build/layout/2551q-2018.layout.json \
        --font-plan build/fonts/2551q-2018.fontplan.json \
        --rule-backend svg \
        --out build/html/2551q-2018.svg.html

    python3 tools/formgen/emit.py --ir ... --layout ... --font-plan ... \
        --guide-plan build/guides/2551q-2018.guide.json \
        --document guide --out build/html/2551q-2018.guide.html
"""

from __future__ import annotations

import argparse
import filecmp
import json
import math
import operator
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.parse
from typing import Any, Iterable, Iterator, Sequence

_HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
import guides  # noqa: E402 - a sibling stage; used for its text-table column grid

SCHEMA_VERSION = 1

_ROOT = pathlib.Path(__file__).resolve().parents[2]

# Arial Narrow is not a separately drawn design: it is Arial rendered through a
# constant horizontal scale. Measured across 70 glyphs the scale is 0.820047
# with a maximum deviation of 0.000691 (0.0039pt at the 9.48pt this form uses),
# which is below the extractor's own 2dp quantisation. Rendering those runs as
# Arimo under scaleX() is therefore the same operation the PDF performs with
# its Tz operator, and it is exact in a way that reaching for a *different*
# condensed design is not: Roboto Condensed is off by up to 1.358pt (0.14em) on
# the same runs. If fonts.py starts emitting `horizontal_scale` this constant
# is not consulted -- the plan wins.
ARIAL_NARROW_HORIZONTAL_SCALE = 0.820047

# Mirrors fonts.py: tracking below both of these is float noise in the source
# and emitting it would be a claim the measurement does not support.
LETTER_SPACING_EPSILON_PT = 0.01
LETTER_SPACING_ACCUMULATED_PT = 0.05

# Row separators sit centred on a growable's row_y; a rule is treated as a
# boundary when its centre is within this of one. Half the thickest observed
# rule (1.44pt) is the loosest this may ever be without a 1.44pt row-local rule
# hugging a boundary being misread as the boundary itself.
BAND_EPSILON_PT = 0.6

# z-order. The rule layer is at the bottom by construction; within it, every
# painted rect is emitted in the source's own content-stream order. Painting a
# decorative rule black is a documented past failure of this project, so the
# grey travels with the rect and is never normalised.
Z_RULES = 1
Z_TEXT = 5
Z_CELLS = 6

# One CSS pixel in points. Chromium's print pipeline treats this as the device
# grid: CSS box geometry and text baselines are floored onto it, which is the
# single fact that both rule backends and the text layer have to survive.
DEVICE_PX_PT = 0.75


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------


def fmt(value: float) -> str:
    """Format one pt scalar for CSS/SVG: fixed precision, no locale, no '-0'.

    Determinism is the property that makes 'convert the other 34 forms' a
    matter of running the script, so no value reaches the output through
    `repr()`, whose shortest-round-trip form differs between float paths that
    are numerically identical.
    """
    text = f"{float(value):.4f}".rstrip("0").rstrip(".")
    return "0" if text in ("", "-", "-0") else text


def parse_pt(value: str | float | None, default: float = 0.0) -> float:
    """Read a pt scalar back out of the font plan's CSS strings."""
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return float(value)
    match = re.fullmatch(r"\s*(-?\d*\.?\d+)\s*pt\s*", str(value))
    return float(match.group(1)) if match else default


def esc_text(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def esc_attr(text: str) -> str:
    return esc_text(text).replace('"', "&quot;")


def paint_color(rgb: Sequence[float] | None, gray: float | None) -> str:
    """Exact source colour as #rrggbb.

    The IR keeps the literal fill value, which is the only thing that
    distinguishes a black rule from grey decoration; both are reproduced, and
    neither is rounded toward the other.
    """
    if rgb:
        channels = [float(c) for c in rgb[:3]]
    elif gray is not None:
        channels = [float(gray)] * 3
    else:
        channels = [0.0, 0.0, 0.0]
    return "#" + "".join(f"{max(0, min(255, round(c * 255))):02x}" for c in channels)


def text_color(value: Any) -> str:
    """PyMuPDF span colour (packed sRGB int) as #rrggbb."""
    try:
        return f"#{int(value) & 0xFFFFFF:06x}"
    except (TypeError, ValueError):
        return "#000000"


def run_id(page_index: int, run_index: int) -> str:
    """The id lattice.py already uses to point at a text run."""
    return f"p{page_index}t{run_index}"


def style_attr(pairs: Iterable[tuple[str, str | None]]) -> str:
    """Join declarations into a style attribute value.

    The result is escaped by the caller through `esc_attr`, which matters more
    than it looks: a CSS font-family stack and font-variation-settings both
    contain double quotes, and emitting them raw would close the attribute
    mid-value and silently drop every declaration after the family name.
    """
    return ";".join(f"{name}:{value}" for name, value in pairs if value is not None)


# ---------------------------------------------------------------------------
# Rects
# ---------------------------------------------------------------------------


def paint_key(box: dict[str, Any], tie: str) -> tuple[int, int, float, float, str]:
    """Sort key that reproduces the source's paint order, total and stable.

    `paint_seq` is the index of the op that first painted the box and
    `paint_seq_max` the last op a merged bar absorbed, so a bar the generator
    drew as fifteen short rects sorts where it started and still breaks ties
    against a bar that started at the same op. Position and id follow, because
    two boxes from one op must not depend on dict order to decide which is
    emitted first -- determinism is the property this pipeline protects above
    any individual form's score.
    """
    return (int(box["paint_seq"]), int(box["paint_seq_max"]),
            float(box["y0"]), float(box["x0"]), tie)


class Rect:
    """One painted rectangle in page points, with the id of its source rule."""

    __slots__ = ("x", "y", "w", "h", "fill", "source_id", "role")

    def __init__(self, x: float, y: float, w: float, h: float, fill: str,
                 source_id: str | None = None, role: str = "structural") -> None:
        self.x, self.y, self.w, self.h = x, y, w, h
        self.fill = fill
        self.source_id = source_id
        self.role = role

    @classmethod
    def from_box(cls, box: dict[str, Any], source_id: str | None = None) -> "Rect":
        return cls(box["x0"], box["y0"], box["x1"] - box["x0"], box["y1"] - box["y0"],
                   paint_color(box.get("rgb"), box.get("gray")), source_id,
                   str(box.get("role", "structural")))

    def shifted(self, dy: float) -> "Rect":
        return Rect(self.x, self.y + dy, self.w, self.h, self.fill, self.source_id,
                    self.role)

    def to_json(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "x": round(self.x, 4), "y": round(self.y, 4),
            "w": round(self.w, 4), "h": round(self.h, 4), "fill": self.fill,
        }
        if self.source_id:
            payload["id"] = self.source_id
        return payload


# A placement matrix is applied only when it reproduces the box the extractor
# also recorded. The two are independent views of the same placement -- the
# matrix comes from get_image_info(), the box from get_image_rects() -- so a
# disagreement means one of them is wrong about this instance, and the box is
# the one the round-trip differ compares. Half the extractor's own 2dp
# quantisation is the widest disagreement that is pure rounding.
PLACEMENT_MATRIX_EPSILON_PT = 0.005


def matrix_box(matrix: Sequence[float]) -> tuple[float, float, float, float]:
    """The axis-aligned box the unit square lands in under `matrix`."""
    a, b, c, d, e, f = (float(v) for v in matrix)
    xs = (e, a + e, c + e, a + c + e)
    ys = (f, b + f, d + f, b + d + f)
    return min(xs), min(ys), max(xs), max(ys)


def placement_matrix(image: dict[str, Any]) -> list[float] | None:
    """The image's 6-element PDF matrix, or None to fall back to its box.

    A box cannot express a flip, and four forms place artwork with a negative
    `d`: 1600-PT's masthead and 2550M's, 2551M's and 2553's seal all rendered
    upside-down with their rim lettering reading bottom-to-top, because the IR's
    x0..y1 is all the emitter used. 0605 places its seal with a small non-zero
    `b`, which a box cannot express either.

    The matrix maps the image's own unit square -- (0,0) at its top-left, y
    downwards -- into page points, which is the same convention an SVG <image>
    at (0,0) sized 1x1 uses, so carrying it needs no conversion and no
    special-casing of the flip. `transform` is None wherever get_image_info()
    and get_image_rects() could not be reconciled; that is extract.py's honest
    answer and this returns None in turn rather than inventing an identity.
    """
    matrix = image.get("transform")
    if not matrix or len(matrix) != 6:
        return None
    x0, y0, x1, y1 = matrix_box(matrix)
    if max(abs(x0 - float(image["x0"])), abs(y0 - float(image["y0"])),
           abs(x1 - float(image["x1"])), abs(y1 - float(image["y1"]))
           ) > PLACEMENT_MATRIX_EPSILON_PT:
        return None
    return [float(v) for v in matrix]


def path_data(path: dict[str, Any]) -> str:
    """One non-rectilinear path as SVG path data, in page points.

    The IR's ops are the PDF operators, and SVG's commands are the same
    operators under different names, so this is a rename and not a conversion:
    `l` is `L`, `c` is `C`, a `re` subpath is its four corners, and `closed`
    (which extract.py *measures* rather than trusting the flag) is `Z`. No
    coordinate is recomputed, which is what keeps a triangle's apex on the
    source's own point.
    """
    parts: list[str] = []
    for sub in path["subpaths"]:
        ops = sub["ops"]
        rect_only = all(op["op"] == "re" for op in ops)
        if not rect_only:
            start = sub["start"]
            parts.append(f"M{fmt(start[0])} {fmt(start[1])}")
        for op in ops:
            points = [float(v) for v in op["points"]]
            if op["op"] == "l":
                for index in range(0, len(points), 2):
                    parts.append(f"L{fmt(points[index])} {fmt(points[index + 1])}")
            elif op["op"] == "c":
                for index in range(0, len(points), 6):
                    parts.append("C" + " ".join(fmt(v) for v in points[index:index + 6]))
            elif op["op"] == "re":
                # A rect is always its own closed subpath in the IR; it is
                # written out in full here so that a subpath is never left
                # relying on a `start` that belongs to a different operator.
                x0, y0, x1, y1 = points
                parts.append(f"M{fmt(x0)} {fmt(y0)}L{fmt(x1)} {fmt(y0)}"
                             f"L{fmt(x1)} {fmt(y1)}L{fmt(x0)} {fmt(y1)}Z")
            else:  # extract.py raises on anything else, so this cannot be reached
                raise SystemExit(f"unknown path op {op['op']!r} in {path['id']}")
        if sub["closed"] and not rect_only:
            parts.append("Z")
    return "".join(parts)


def path_paints(path: dict[str, Any]) -> tuple[str | None, str | None]:
    """(fill, stroke) as #rrggbb, either being None when the path has none.

    Both are carried because a path may do both and the two differ: 2551M's
    pre-printed decimal points are filled *and* stroked, so collapsing them to
    one ink would lose the fact that the mark is 0.72pt wider than its fill.
    """
    has_fill = path.get("fill") is not None or path.get("fill_gray") is not None
    has_stroke = (float(path.get("stroke_width_pt") or 0.0) > 0.0
                  and (path.get("stroke") is not None
                       or path.get("stroke_gray") is not None))
    return (paint_color(path.get("fill"), path.get("fill_gray")) if has_fill else None,
            paint_color(path.get("stroke"), path.get("stroke_gray")) if has_stroke else None)


def path_svg(path: dict[str, Any]) -> str:
    """The <path> element itself, shared by both backends.

    `fill-rule` is only stated where the source stated it, so a path with the
    nonzero default keeps the SVG default and the two documents stay
    comparable.
    """
    fill, stroke = path_paints(path)
    attributes = [f'd="{path_data(path)}"',
                  f'fill="{fill}"' if fill else 'fill="none"']
    if fill and path.get("even_odd"):
        attributes.append('fill-rule="evenodd"')
    if stroke:
        attributes.append(f'stroke="{stroke}"')
        attributes.append(f'stroke-width="{fmt(path["stroke_width_pt"])}"')
    attributes.append(f'data-path-id="{esc_attr(str(path["id"]))}"')
    return f'<path {" ".join(attributes)}/>'


class RuleBackend:
    """Paints rects, paths and placed artwork. The only thing that differs per backend.

    Images and paths ride the backend too. Both are geometry in a box exactly as
    a rule is, so routing them through the same code is what makes the
    round-trip a comparison of *every* painted mark on the page rather than of
    the rules alone.
    """

    name = ""

    def open_page(self, page: dict[str, Any]) -> str:
        raise NotImplementedError

    def close_page(self) -> str:
        raise NotImplementedError

    def rects(self, rects: Sequence[Rect], layer: str) -> str:
        raise NotImplementedError

    def band_container(self, band_id: str, rects: Sequence[Rect]) -> str:
        raise NotImplementedError

    def image(self, image: dict[str, Any], href: str, present: bool) -> str:
        raise NotImplementedError

    def path(self, path: dict[str, Any], page: dict[str, Any]) -> str:
        raise NotImplementedError


class SvgBackend(RuleBackend):
    """One <svg> per page whose user units are page points, 1:1.

    The viewBox is the MediaBox and the element is sized in pt, so a rect's
    x/y/width/height are the PDF's own numbers with no unit conversion left to
    the browser. preserveAspectRatio="none" removes even the fitting step,
    which is already an identity here but need not stay one for a form whose
    page box is not this one.
    """

    name = "svg"

    def open_page(self, page: dict[str, Any]) -> str:
        return (f'<svg class="rl" xmlns="http://www.w3.org/2000/svg" '
                f'viewBox="0 0 {fmt(page["width_pt"])} {fmt(page["height_pt"])}" '
                f'preserveAspectRatio="none" '
                f'style="width:{fmt(page["width_pt"])}pt;'
                f'height:{fmt(page["height_pt"])}pt;z-index:{Z_RULES}">')

    def close_page(self) -> str:
        return "</svg>"

    def rects(self, rects: Sequence[Rect], layer: str) -> str:
        if not rects:
            return ""
        parts = [f'<g class="layer-{layer}">']
        parts.extend(self._rect(rect) for rect in rects)
        parts.append("</g>")
        return "".join(parts)

    @staticmethod
    def _rect(rect: Rect) -> str:
        """One rect, anti-aliased.

        shape-rendering="crispEdges" was here to keep rules sharp, and measuring
        it refuted that. Disabling anti-aliasing makes coverage all-or-nothing
        against the pixel centre, so a rule thinner than one device pixel does
        not get sharper -- it disappears. At device_scale_factor 1 that erased
        every 0.24pt comb divider and every 0.48pt box outline on 2552 page 1,
        which is exactly the structure a comb field consists of.

        Against the official raster, per device-pixel row over the whole page,
        mean |tone delta| with crispEdges vs without: 5.013 -> 1.327 at dsf 1,
        2.789 -> 0.959 at dsf 2, 1.793 -> 0.806 at dsf 4 (2552 p1; 2551Q and
        2316 agree, 2.709 -> 0.889 and 2.462 -> 0.840 at dsf 2). Restricting the
        removal to sub-pixel decorative rects, which is the narrower fix,
        recovers almost none of that: 2.789 -> 2.708 on the same page, and
        nothing at all on 2316, whose structure is stroked black.

        Anti-aliasing is also what the official raster does, so a sub-pixel rule
        lands as the mid-grey tone the source produces rather than as ink or
        nothing. Print is unaffected either way: printing to PDF keeps the rects
        as vectors, and the IR round-trip is unchanged by this attribute.
        """
        rid = f' data-rule-id="{esc_attr(rect.source_id)}"' if rect.source_id else ""
        return (f'<rect x="{fmt(rect.x)}" y="{fmt(rect.y)}" width="{fmt(rect.w)}" '
                f'height="{fmt(rect.h)}" fill="{rect.fill}"{rid}/>')

    def band_container(self, band_id: str, rects: Sequence[Rect]) -> str:
        body = "".join(self._rect(rect) for rect in rects)
        return f'<g class="layer-band" id="band-rules-{band_id}">{body}</g>'

    def image(self, image: dict[str, Any], href: str, present: bool) -> str:
        matrix = placement_matrix(image)
        if matrix is None:
            geometry = (f'x="{fmt(image["x0"])}" y="{fmt(image["y0"])}" '
                        f'width="{fmt(image["x1"] - image["x0"])}" '
                        f'height="{fmt(image["y1"] - image["y0"])}"')
        else:
            # The unit square is the image's own space with (0,0) at its
            # top-left, which is exactly what the matrix is defined against, so
            # the placement is the source's matrix and nothing else.
            geometry = ('x="0" y="0" width="1" height="1" transform="matrix('
                        + ",".join(fmt(v) for v in matrix) + ')"')
        tag = (f'<image href="{esc_attr(href)}" {geometry} preserveAspectRatio="none" '
               if present else
               f'<rect fill="none" {geometry} data-missing-src="{esc_attr(href)}" ')
        return f'{tag}data-sha256="{esc_attr(image["sha256"])}"/>'

    def path(self, path: dict[str, Any], page: dict[str, Any]) -> str:
        return path_svg(path)


class CssBackend(RuleBackend):
    """Every rule an absolutely positioned div painted with background-color.

    `border` is deliberately not used: the shorthand is resolved against the
    used border-width, which Chromium snaps independently of the box position,
    so a 0.24pt border and a 0.24pt-tall background box do not even fail the
    same way. Painting the box makes the failure mode a single one, which is
    what makes the backend comparison mean something.
    """

    name = "css"

    def open_page(self, page: dict[str, Any]) -> str:
        return f'<div class="rl" style="z-index:{Z_RULES}">'

    def close_page(self) -> str:
        return "</div>"

    def rects(self, rects: Sequence[Rect], layer: str) -> str:
        if not rects:
            return ""
        parts = [f'<div class="layer-{layer}">']
        parts.extend(self._rect(rect) for rect in rects)
        parts.append("</div>")
        return "".join(parts)

    @staticmethod
    def _rect(rect: Rect) -> str:
        rid = f' data-rule-id="{esc_attr(rect.source_id)}"' if rect.source_id else ""
        style = style_attr((
            ("left", f"{fmt(rect.x)}pt"), ("top", f"{fmt(rect.y)}pt"),
            ("width", f"{fmt(rect.w)}pt"), ("height", f"{fmt(rect.h)}pt"),
            ("background-color", rect.fill),
        ))
        return f'<div class="r" style="{esc_attr(style)}"{rid}></div>'

    def band_container(self, band_id: str, rects: Sequence[Rect]) -> str:
        body = "".join(self._rect(rect) for rect in rects)
        return f'<div class="layer-band" id="band-rules-{band_id}">{body}</div>'

    def image(self, image: dict[str, Any], href: str, present: bool) -> str:
        matrix = placement_matrix(image)
        if matrix is None:
            style = style_attr((
                ("left", f"{fmt(image['x0'])}pt"), ("top", f"{fmt(image['y0'])}pt"),
                ("width", f"{fmt(image['x1'] - image['x0'])}pt"),
                ("height", f"{fmt(image['y1'] - image['y0'])}pt"),
            ))
        else:
            # A 1pt box scaled by the matrix: scale is unit-agnostic, so a and d
            # land the same lengths the SVG backend does. Only the translation
            # differs -- CSS `matrix()` takes it in px, never in the element's
            # own unit -- so e and f are divided by the device pixel, which is
            # exact in binary and therefore costs no precision.
            style = style_attr((
                ("left", "0"), ("top", "0"), ("width", "1pt"), ("height", "1pt"),
                ("transform-origin", "0 0"),
                ("transform", "matrix("
                 + ",".join(fmt(v) for v in matrix[:4])
                 + f",{fmt(matrix[4] / DEVICE_PX_PT)},{fmt(matrix[5] / DEVICE_PX_PT)})"),
            ))
        common = (f'class="img" data-sha256="{esc_attr(image["sha256"])}" '
                  f'style="{esc_attr(style)}"')
        if present:
            return f'<img src="{esc_attr(href)}" alt="" {common}>'
        return f'<div data-missing-src="{esc_attr(href)}" {common}></div>'

    def path(self, path: dict[str, Any], page: dict[str, Any]) -> str:
        """A path cannot be a CSS box, so it is an SVG of its own.

        No CSS property draws a Bezier, and 257 of the corpus's 944 paths are
        filled curves. The element is page-sized with the page's own viewBox, so
        the geometry inside it is identical to the SVG backend's and the two
        backends still differ in exactly one thing: how *rules* are painted.
        Document order is preserved, so the source's paint order survives.
        """
        style = style_attr((
            ("position", "absolute"), ("left", "0"), ("top", "0"),
            ("width", f"{fmt(page['width_pt'])}pt"),
            ("height", f"{fmt(page['height_pt'])}pt"),
        ))
        return (f'<svg class="pl" xmlns="http://www.w3.org/2000/svg" '
                f'viewBox="0 0 {fmt(page["width_pt"])} {fmt(page["height_pt"])}" '
                f'preserveAspectRatio="none" style="{esc_attr(style)}">'
                f'{path_svg(path)}</svg>')


BACKENDS = {"svg": SvgBackend, "css": CssBackend}


# ---------------------------------------------------------------------------
# Typography
# ---------------------------------------------------------------------------


class RunStyle:
    """Everything needed to place one text run, resolved from the font plan."""

    __slots__ = ("css", "scale_x", "baseline_offset_pt", "top_pt", "translate_y_pt",
                 "font_family", "font_file", "css_style", "font_face_weight",
                 "unresolved", "ink")

    def __init__(self, css: dict[str, Any], scale_x: float | None,
                 baseline_offset_pt: float, top_pt: float, translate_y_pt: float,
                 font_family: str | None, font_file: str | None, css_style: str,
                 unresolved: bool, font_face_weight: str = "100 900",
                 ink: Ink | None = None) -> None:
        self.css = css
        # What this run actually puts on the page: its ink and the anchor that
        # ink sits on. A run with no padding is its own ink, so this is the
        # identity for all but the padded ones. None means "not measured", and
        # then the run's whole string is emitted at its first character's origin.
        self.ink = ink
        self.scale_x = scale_x
        self.baseline_offset_pt = baseline_offset_pt
        self.top_pt = top_pt
        self.translate_y_pt = translate_y_pt
        self.font_family = font_family
        self.font_file = font_file
        self.css_style = css_style
        # The `@font-face` weight *descriptor* for this run's file, straight from
        # the plan. Defaulted to the variable range so a plan written before
        # fonts.py emitted `font_face` still produces what it produced before.
        self.font_face_weight = font_face_weight
        self.unresolved = unresolved

    def text_of(self, run: dict[str, Any]) -> str:
        return self.ink.text if self.ink is not None else run["text"]

    def origin_of(self, run: dict[str, Any]) -> float:
        return (self.ink.origin_x if self.ink is not None
                else float(run["origin_x"] or 0.0))


def _donor_face(faces: Sequence[dict[str, Any]], face: dict[str, Any]) -> dict[str, Any] | None:
    """The metric-compatible face that can carry a non-compatible one under scaleX.

    Chosen by CSS weight and style rather than by name, so the correction is a
    property of the plan and not a hardcoded 'Arial Narrow -> Arimo' table.
    """
    candidates = [f for f in faces
                  if f.get("status") == "resolved" and f.get("metric_compatible")
                  and f.get("css_style") == face.get("css_style")
                  and f.get("css_weight") == face.get("css_weight")
                  and f.get("css_family")]
    return sorted(candidates, key=lambda f: f["face_key"])[0] if candidates else None


def _horizontal_scale(entry: dict[str, Any], face: dict[str, Any]) -> float | None:
    """Scale the plan itself declares, if fonts.py has started emitting one."""
    for source in (entry, face):
        value = source.get("horizontal_scale")
        if value is not None:
            return float(value)
    return None


def _source_tracking(run: dict[str, Any], first: int, last: int,
                     scale: float) -> float | None:
    """The source's own per-gap tracking across glyphs [first, last], un-scaled.

    Derived from the IR alone, which is what makes it deterministic and free of
    any platform font: `char_origin_offsets_pt` gives each glyph's painted
    origin and `char_widths_pt` the advance the face itself would have made, so
    their difference over a gap is the tracking the generator added there.

    A scale multiplies the whole inline box, tracking included, exactly as the
    PDF's Tz operator does: if the unscaled face advances `natural` and we add
    `sp` per gap, the painted advance is scale*(natural + sp*(n-1)). The Narrow
    face *is* the donor face scaled, so `natural` is the PDF's own advances
    divided by the scale and the scale cancels to a single division here.
    """
    gaps = last - first
    if gaps <= 0 or scale <= 0:
        return None
    offsets = run["char_origin_offsets_pt"]
    widths = run["char_widths_pt"]
    if last >= len(offsets) or last >= len(widths):
        return None
    painted = float(offsets[last]) - float(offsets[first])
    natural = sum(float(w) for w in widths[first:last])
    spacing = (painted - natural) / (scale * gaps)
    if (abs(spacing) < LETTER_SPACING_EPSILON_PT
            and abs(spacing) * gaps < LETTER_SPACING_ACCUMULATED_PT):
        return None
    return round(spacing, 4)


def _rescaled_letter_spacing(run: dict[str, Any], scale: float) -> float | None:
    """Tracking for a run whose face is reached through scaleX(scale)."""
    return _source_tracking(run, 0, len(run["text"]) - 1, scale)


class Ink:
    """The part of a run that puts ink on paper, and where its left edge sits.

    A run's padding spaces are position, not content. The BIR generator writes a
    checkbox label as `' 2nd '` and puts 6.86pt of the 9.36pt leading advance in
    a TJ offset, so reproducing the string with the substitute face's own space
    advance -- plus whatever share of the run's tracking the space happens to get
    -- lands the ink somewhere else entirely: measured, 'Calendar' on 2550-DS,
    2550Q and 1707A comes out 4.9-6.0pt to the left, inside the checkbox beside
    it, and 2550Q's 'Quarter' 0.71pt to the right, on top of its neighbour.

    So a padded run is emitted as its ink, anchored on the first *visible*
    glyph's own origin, which is also the point verify.py measures. Nothing is
    lost: a space paints nothing, and the advance it was carrying is expressed
    by the anchor instead of by a font's space width.

    Trimming is restricted to a run whose ink is a single word, and the
    restriction is what keeps it honest rather than convenient. The font plan's
    `letter-spacing` is a claim about the string the plan measured, so emitting a
    different string means re-deriving it, and re-deriving it over a range that
    still contains word gaps would smear those gaps across the letters -- the
    same defect this is undoing. 4390 of the corpus's 10732 padded runs are
    single-word; the rest keep their padding and today's rendering exactly.
    """

    __slots__ = ("text", "origin_x", "letter_spacing_pt", "trimmed")

    def __init__(self, text: str, origin_x: float,
                 letter_spacing_pt: float | None, trimmed: bool) -> None:
        self.text = text
        self.origin_x = origin_x
        self.letter_spacing_pt = letter_spacing_pt
        self.trimmed = trimmed


def run_ink(run: dict[str, Any], scale: float | None) -> Ink:
    """What to emit for one run: its ink, its anchor, and its own tracking."""
    text = run["text"]
    origin = float(run["origin_x"] or 0.0)
    visible = [index for index, char in enumerate(text) if not char.isspace()]
    if not visible:  # extract.py drops whitespace-only runs; this cannot happen
        return Ink(text, origin, None, False)
    first, last = visible[0], visible[-1]
    if (first, last) == (0, len(text) - 1):
        return Ink(text, origin, None, False)
    if any(char.isspace() for char in text[first:last + 1]):
        return Ink(text, origin, None, False)
    offsets = run["char_origin_offsets_pt"]
    if first >= len(offsets):
        return Ink(text, origin, None, False)
    return Ink(text[first:last + 1], origin + float(offsets[first]),
               _source_tracking(run, first, last, scale if scale else 1.0), True)


def _round_half_up(value: float) -> float:
    """Blink rounds font metrics with roundf, i.e. half away from zero."""
    return math.floor(value + 0.5) if value >= 0 else -math.floor(-value + 0.5)


def _baseline_offset_px(css: dict[str, Any], face: dict[str, Any], run: dict[str, Any],
                        warnings: list[str]) -> float:
    """Where Blink puts the baseline inside the run's line box, in device px.

    Measured, not guessed. A probe that printed the same string at 30 tops in
    0.05pt steps came back with exactly three baselines, all on the 0.75pt
    device grid: Blink floors the block's top to an integer device pixel and
    floors the baseline again, and it rounds the face's ascent and descent to
    integer pixels before computing half-leading. Reproducing that arithmetic
    is what lets `translate_y_pt` below cancel it exactly.

    The ascent and descent are the *emitted* face's hhea values -- the ones
    Chromium will actually use -- not the PDF's, which belong to a face we are
    not shipping.
    """
    size = parse_pt(css.get("font-size"), float(run["size_pt"]))
    metrics = face.get("vertical_metrics") or {}
    ascender = metrics.get("css_hhea_ascender")
    descender = metrics.get("css_hhea_descender")
    if ascender is None or descender is None:
        ascender = float(run["ascender"])
        descender = float(run["descender"])
        warnings.append(
            f"face {face.get('face_key')!r} carries no shipped-face vertical metrics; "
            f"baselines fall back to the PDF's own ascender/descender, which belong "
            f"to a face we do not ship")
    size_px = size / DEVICE_PX_PT
    ascent_px = _round_half_up(float(ascender) * size_px)
    descent_px = _round_half_up(-float(descender) * size_px)
    content_px = ascent_px + descent_px
    line_px = parse_pt(css.get("line-height"), content_px * DEVICE_PX_PT) / DEVICE_PX_PT
    return (line_px - content_px) / 2.0 + ascent_px


def _vertical_placement(baseline_y: float, offset_px: float) -> tuple[float, float, float]:
    """Split a baseline into a snapped `top` and an exact translateY residual.

    The probe above proves two things at once: `top` alone cannot express a
    sub-0.75pt baseline, and a `transform` can -- 30 translateY steps of 0.05pt
    produced 30 distinct baselines, off-lattice, tracking the request exactly.

    So the box is placed on the device grid, where Blink's flooring is a
    no-op and therefore harmless, and the remaining fraction is carried by a
    transform, which is applied after layout and is not snapped. Nothing here
    is a fudge factor: `residual` is whatever the reproduced Blink arithmetic
    left over, and it is zero when the baseline already lands on the grid.
    """
    top_px = math.floor((baseline_y - offset_px * DEVICE_PX_PT) / DEVICE_PX_PT)
    painted_px = top_px + math.floor(offset_px)
    return (top_px * DEVICE_PX_PT,
            baseline_y - painted_px * DEVICE_PX_PT,
            offset_px * DEVICE_PX_PT)


def resolve_run_styles(ir: dict[str, Any], plan: dict[str, Any],
                       warnings: list[str]) -> dict[tuple[int, int], RunStyle]:
    """Join the font plan onto the IR runs and apply the Narrow correction.

    The plan as generated maps Arial Narrow onto Roboto Condensed and says so
    honestly (`metric_compatible: false`). Roboto Condensed is an independently
    drawn design, so its glyph origins are wrong inside the run even when
    letter-spacing restores the total width. Where the plan does not already
    carry a `horizontal_scale`, this retargets such runs onto the plan's own
    metric-compatible face under a horizontal scale, which is the operation the
    PDF is performing.
    """
    faces = {f["face_key"]: f for f in plan["faces"]}
    face_list = list(plan["faces"])
    runs_by_key = {(int(e["page"]), int(e["run_index"])): e for e in plan["runs"]}

    styles: dict[tuple[int, int], RunStyle] = {}
    corrected: set[str] = set()

    for page in ir["pages"]:
        for index, run in enumerate(page["text_runs"]):
            key = (int(page["index"]), index)
            entry = runs_by_key.get(key)
            if entry is None or not entry.get("css"):
                warnings.append(
                    f"page {page['index']} run {index}: the font plan resolves no face "
                    f"for {run['text'][:24]!r}; it is emitted with the generic stack so "
                    f"the run is still present, and it will fail the round-trip")
                css = {
                    "font-family": "sans-serif",
                    "font-size": f"{fmt(run['size_pt'])}pt",
                    "font-weight": 700 if run["bold"] else 400,
                    "font-style": "italic" if run["italic"] else "normal",
                    "line-height": f"{fmt(run['line_height_pt'])}pt",
                }
                offset_px = _baseline_offset_px(css, {}, run, [])
                top_pt, translate_y, offset_pt = _vertical_placement(
                    float(run["baseline_y"]), offset_px)
                styles[key] = RunStyle(css, None, offset_pt, top_pt, translate_y,
                                       None, None, css["font-style"], True,
                                       ink=run_ink(run, None))
                continue

            face = faces[entry["face_key"]]
            css = dict(entry["css"])
            scale = _horizontal_scale(entry, face)
            emitted_face = face

            if scale is None and not face.get("metric_compatible", False):
                donor = _donor_face(face_list, face)
                if donor is not None:
                    scale = ARIAL_NARROW_HORIZONTAL_SCALE
                    emitted_face = donor
                    css["font-family"] = donor["css_family_stack"]
                    spacing = _rescaled_letter_spacing(run, scale)
                    css["letter-spacing"] = (f"{fmt(spacing)}pt"
                                             if spacing is not None else None)
                    if face["face_key"] not in corrected:
                        corrected.add(face["face_key"])
                        warnings.append(
                            f"face {face['face_key']} is served by "
                            f"{donor['css_family']} under scaleX("
                            f"{ARIAL_NARROW_HORIZONTAL_SCALE}) instead of the plan's "
                            f"{face.get('css_family')}: the plan itself reports the "
                            f"latter is not metric-compatible, and this family is the "
                            f"donor family at a constant horizontal scale. Emit "
                            f"`horizontal_scale` from fonts.py to make this the plan's "
                            f"own decision.")
                else:
                    warnings.append(
                        f"face {face['face_key']} is not metric-compatible and the plan "
                        f"offers no compatible donor at the same weight/style; its runs "
                        f"keep the plan's substitute and its glyph origins are wrong")

            offset_px = _baseline_offset_px(css, emitted_face, run, warnings)
            top_pt, translate_y, offset_pt = _vertical_placement(
                float(run["baseline_y"]), offset_px)
            styles[key] = RunStyle(
                css=css,
                scale_x=scale,
                baseline_offset_pt=offset_pt,
                top_pt=top_pt,
                translate_y_pt=translate_y,
                font_family=emitted_face.get("css_family"),
                font_file=emitted_face.get("font_file"),
                css_style=str(emitted_face.get("css_style") or "normal"),
                unresolved=False,
                font_face_weight=str(
                    (emitted_face.get("font_face") or {}).get("weight") or "100 900"),
                ink=run_ink(run, scale),
            )
    return styles


# Emitted in this order, so a diff of two generated documents is readable.
# `transform`, `transform-origin` and `display` are managed here rather than
# copied: the first two are derived from the plan's horizontal_scale together
# with the baseline this module computes, and `display` is blockified by CSS on
# an absolutely positioned box anyway. Everything else the plan carries is
# passed through, including keys added after this was written.
FONT_CSS_ORDER = ("font-family", "font-size", "font-weight", "font-style",
                  "letter-spacing", "line-height", "font-kerning",
                  "font-variant-ligatures", "font-feature-settings",
                  "font-variation-settings")
FONT_CSS_MANAGED = frozenset({"transform", "transform-origin", "display"})


def font_declarations(run: dict[str, Any], style: RunStyle) -> list[tuple[str, str | None]]:
    """The plan's CSS for one run, verbatim, in a fixed order.

    With one substitution, and only where the emitted string is not the string
    the plan measured: the plan's `letter-spacing` is
    (measured_advance - natural_advance) / gaps over that whole string, so it is
    not this string's tracking once the padding spaces are gone. Ink re-derives
    it over the glyphs actually emitted; see Ink for why that is restricted to a
    run whose ink is one word.
    """
    css = style.css
    trimmed = style.ink.trimmed if style.ink is not None else False
    pairs: list[tuple[str, str | None]] = []
    for name in FONT_CSS_ORDER:
        value = css.get(name)
        if name == "letter-spacing" and trimmed:
            spacing = style.ink.letter_spacing_pt
            value = None if spacing is None else f"{fmt(spacing)}pt"
        pairs.append((name, None if value is None else str(value)))
    for name in sorted(set(css) - set(FONT_CSS_ORDER) - FONT_CSS_MANAGED):
        value = css[name]
        pairs.append((name, None if value is None else str(value)))
    pairs.append(("color", text_color(run.get("color"))))
    return pairs


def is_scaled(style: RunStyle) -> bool:
    return style.scale_x is not None and abs(style.scale_x - 1.0) > 1e-9


def transform_declarations(style: RunStyle, origin_x: float) -> list[tuple[str, str | None]]:
    """The run's transform: sub-device-pixel placement, then scaleX.

    Measured: an untransformed box keeps sub-pixel *horizontal* text placement
    (245 of 254 glyph origins land off the device grid) while its baseline is
    floored onto that grid, and a translate fixes the baseline without
    disturbing x. A scaleX behaves differently -- it snaps the box's x as well,
    which showed up as 0.02-0.37pt origin errors on exactly the ten scaled
    runs. So a scaled run gives up `left` entirely and carries both axes in the
    transform, where the box origin is (0, grid) and nothing is left to snap.

    scaleX is written last, which in CSS matrix order means it applies to the
    element first, about the run's left baseline -- the point the PDF's Tz
    operator scales about. The identity scale and a zero residual are both
    omitted, so a run already on the grid keeps a transform-free box.
    """
    operations = []
    if is_scaled(style):
        operations.append(f"translate({fmt(origin_x)}pt,{fmt(style.translate_y_pt)}pt)")
        operations.append(f"scaleX({style.scale_x})")
    elif abs(style.translate_y_pt) > 1e-9:
        operations.append(f"translateY({fmt(style.translate_y_pt)}pt)")
    if not operations:
        return []
    return [("transform", " ".join(operations)),
            ("transform-origin", f"0 {fmt(style.baseline_offset_pt)}pt")]


def text_markup(run: dict[str, Any], identifier: str, style: RunStyle,
                extra: Sequence[tuple[str, str]] = ()) -> str:
    """One absolutely positioned run, anchored on its own glyph origin.

    The anchor is the first *visible* glyph's origin_x and the run's baseline_y,
    never the bbox: a bbox is ink extent and therefore depends on which glyphs
    happen to be in the string. How those two numbers reach the page --
    `left`/`top` or a transform -- is decided by `transform_declarations`,
    because Chromium snaps the two axes differently. See Ink for why the padding
    spaces are not part of what is emitted.
    """
    origin = style.origin_of(run)
    pairs: list[tuple[str, str | None]] = [
        ("left", "0" if is_scaled(style) else f"{fmt(origin)}pt"),
        ("top", f"{fmt(style.top_pt)}pt"),
    ]
    pairs.extend(font_declarations(run, style))
    pairs.extend(transform_declarations(style, origin))
    attrs = "".join(f' {name}="{esc_attr(value)}"' for name, value in extra)
    unresolved = ' data-unresolved="true"' if style.unresolved else ""
    return (f'<div class="t" id="{identifier}" style="{esc_attr(style_attr(pairs))}"'
            f'{attrs}{unresolved}>{esc_text(style.text_of(run))}</div>')


def text_json(run: dict[str, Any], identifier: str, style: RunStyle,
              row_index: int, column_role: str | None) -> dict[str, Any]:
    """The same run as data, for the growable template's JS renderer."""
    scale = (style.scale_x if style.scale_x is not None
             and abs(style.scale_x - 1.0) > 1e-9 else None)
    return {
        "id": identifier,
        "row": row_index,
        "role": column_role,
        "text": style.text_of(run),
        "x": round(style.origin_of(run), 4),
        "baseline_y": round(float(run["baseline_y"]), 4),
        "style": style_attr(font_declarations(run, style)),
        # The renderer re-derives `top` and the translateY residual from these
        # two, because a row shifted to an overflow position lands on a
        # different point of the device grid and its residual is not the
        # template row's.
        "baseline_offset_pt": round(style.baseline_offset_pt, 4),
        "scale_x": scale,
    }


# ---------------------------------------------------------------------------
# Fields: the surface a taxpayer types on
# ---------------------------------------------------------------------------
#
# A cell of kind "field" is a box the official sheet left blank. Until this
# layer existed the generated document reproduced the blank exactly and gave
# nobody anywhere to type: 2551Q carried 147 cells marked `data-cell-kind=
# "field"` and not one input. The field layer adds the typing surface without
# adding a single unit of ink, which is what makes it safe to bolt onto a
# document whose whole value is that it round-trips.
#
# Three properties are load-bearing:
#
#   * An empty field prints as nothing. Every affordance is a `:hover`/`:focus`
#     rule under `@media screen`, and print has neither state, so the printed
#     sheet is the same PDF it was before this layer existed even if a
#     stylesheet transform drops the media guard -- which is a thing that has
#     actually happened to this repo's packaged bundles.
#   * A field never states its geometry twice. A comb slot is already a
#     positioned box, so its input is `inset:0` inside it; only a plain field
#     states an inset, and it states offsets rather than a width, so the same
#     markup fits a band row whose cell is a different size.
#   * The typography is the font plan's, not the browser's. A blank has no text
#     of its own to measure, so the face is derived from the sheet's own body
#     text (see FieldFace) and served by the same @font-face, at a size fitted
#     to the box the form actually drew.


# Below this a fitted field would be typographically useless, and a box that
# small is more likely a mis-detected cell than a place to write. It is
# reported, never silently corrected: the box is the source's.
FIELD_MIN_SIZE_PT = 4.0

# ...but "the box is the source's" is only true of a box we did not shrink
# ourselves, and until this split existed the report could not tell the two
# apart. A field is undersized for exactly one of two reasons:
#
#   * the CELL the source drew is itself too short to carry a legible size --
#     nothing downstream can fix that, and reporting it is all that is honest;
#   * the cell is tall enough and the writing box we DERIVED from it is not --
#     a legible box existed and we discarded it.
#
# Only the second is our defect, and it is the one that was invisible, because
# both populations arrived as one undifferentiated count. Measured over the 53
# built bundles at the time of writing: 243 undersized fields of 10,481, of
# which 15 are the source's and 228 are ours.
#
# The classification is deliberately not a ratio: it asks whether the cell
# COULD have carried FIELD_MIN_SIZE_PT, through the same fit `field_box`
# performs, so the audit cannot disagree with the fit it is auditing.


# A writing box that leaves its own cell is clipped away by `.f{overflow:hidden}`
# and by `.f .s`, so the part outside is a typing surface nobody can see, reach
# or print. That is never legitimate: 225 comb bands across 21 bundles are in
# this state today, the worst of them 350.16pt tall and starting 165.84pt ABOVE
# a 16.80pt cell. The epsilon is the layout's own coordinate noise, not a
# tolerance to hide inside.
FIELD_CONTAINMENT_EPSILON_PT = 0.01

# A comb's writing box is the divider band lattice.py measured, and the official
# artwork says that band is a guide mark UNDER the writing box rather than the
# box itself: in 2550M's item-4 TIN row the cell walls span the full 15.60pt of
# the row (x 65.64, 99.48, 104.28, 137.40, 141.96, 175.08, 179.88, 212.76, every
# one from y 118.80 to 134.40) while the digit separators are 3.12pt ticks along
# its bottom edge at y 131.28-134.40. Reading the tick as the height of the
# writing surface collapses the typing box to a fifth of the row.
#
# A minority share of the cell is reported, not failed: whether a comb band is a
# sub-band of a taller cell or the whole of it is lattice.py's measurement to
# make, and emit.py policing it with a threshold of its own would be the wrong
# file correcting the wrong producer. What emit.py owns is saying out loud how
# much of the cell the surface it emitted actually covers. Measured today: 4,474
# of 4,522 comb cells are under half, 2551Q's 105 of 105 at 0.34-0.42.
COMB_BAND_COLLAPSE_RATIO = 0.5

# Used only when the plan carries no shipped-face vertical metrics, which is
# also warned about. 1.2em is the browser's own `normal` line box, so a field
# fitted through it is no worse placed than an unstyled one.
FALLBACK_LINE_SPAN_EM = 1.2


class FieldFace:
    """The face a taxpayer's own entries are typed in, derived from the sheet.

    A blank has no text of its own, so its typography has to come from
    somewhere else, and the only defensible source is the form's own printed
    body text: whatever face and size the sheet sets most of its characters in
    is what a filled entry has to match, or a filled form stops matching its
    own blanks. It is chosen by glyph count over the *font plan*, so an entry
    is served by the identical @font-face, kerning, ligature and variation
    settings as the pre-printed runs beside it -- not by a look-alike, and not
    by the platform UI font an unstyled <input> would use.

    Candidates are restricted to resolved, metric-compatible faces at unit
    horizontal scale: a face reached through scaleX() carries its advances
    through a transform that would have to be re-derived per box, and a
    non-metric-compatible face has wrong glyph origins by the plan's own
    admission. Upright regular wins over bold and italic where both exist,
    because the runs a BIR sheet sets bold are its headings, not its data.
    """

    __slots__ = ("css", "size_pt", "letter_spacing_pt", "line_span_em", "face_key")

    def __init__(self, css: dict[str, Any], size_pt: float, letter_spacing_pt: float,
                 line_span_em: float, face_key: str) -> None:
        self.css = css
        self.size_pt = size_pt
        self.letter_spacing_pt = letter_spacing_pt
        self.line_span_em = line_span_em
        self.face_key = face_key


def resolve_field_face(plan: dict[str, Any], warnings: list[str]) -> FieldFace | None:
    """The modal body face of the font plan, by glyph count. Deterministic."""
    faces = {f["face_key"]: f for f in plan["faces"]}
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for entry in plan["runs"]:
        face = faces.get(entry.get("face_key"))
        css = entry.get("css") or {}
        if face is None or face.get("status") != "resolved":
            continue
        if not face.get("metric_compatible") or not css.get("font-family"):
            continue
        scale = entry.get("horizontal_scale")
        if scale is not None and abs(float(scale) - 1.0) > 1e-9:
            continue
        groups.setdefault((str(entry["face_key"]), str(css.get("font-size"))),
                          []).append(entry)

    if not groups:
        warnings.append(
            "the font plan resolves no metric-compatible face at unit scale, so a field "
            "input would be typed in the browser's UI font; no typing surface is emitted "
            "rather than one whose advances are nobody's")
        return None

    def rank(item: tuple[tuple[str, str], list[dict[str, Any]]]) -> tuple[int, int]:
        face = faces[item[0][0]]
        upright = (int(face.get("css_weight") or 400) == 400
                   and str(face.get("css_style") or "normal") == "normal")
        return (1 if upright else 0, sum(int(e.get("chars") or 0) for e in item[1]))

    (face_key, _size), entries = max(sorted(groups.items()), key=rank)
    donor = max(sorted(entries, key=lambda e: (int(e["page"]), int(e["run_index"]))),
                key=lambda e: int(e.get("chars") or 0))
    face = faces[face_key]

    metrics = face.get("vertical_metrics") or {}
    ascender = metrics.get("css_hhea_ascender")
    descender = metrics.get("css_hhea_descender")
    if ascender is None or descender is None:
        warnings.append(
            f"face {face_key!r} carries no shipped-face vertical metrics; field sizes are "
            f"fitted through the browser's {fmt(FALLBACK_LINE_SPAN_EM)}em default line box "
            f"instead of the face's own")
        span = FALLBACK_LINE_SPAN_EM
    else:
        span = float(ascender) - float(descender)

    dropped = {"font-size", "line-height", "letter-spacing", "color"}
    css = {name: value for name, value in (donor.get("css") or {}).items()
           if name not in FONT_CSS_MANAGED and name not in dropped and value is not None}
    # The typed value is the taxpayer's own ink and is always black; the donor
    # run's colour belongs to the printed label it was measured from.
    css["color"] = "#000000"
    return FieldFace(css, parse_pt((donor.get("css") or {}).get("font-size")),
                     parse_pt((donor.get("css") or {}).get("letter-spacing")),
                     span, face_key)


def _border_thickness(cell: dict[str, Any], side: str) -> float:
    entry = (cell.get("border") or {}).get(side) or {}
    return float(entry.get("thickness_pt") or 0.0)


def _floor2(value: float) -> float:
    """A fitted size, rounded down to the extractor's own 2dp quantisation.

    Down, never to nearest: a size rounded up is a size that no longer fits the
    box the form drew, and fitting it is the entire exercise.
    """
    return math.floor(value * 100.0) / 100.0


class FieldBox:
    """One field's writing box and the text metrics fitted into it.

    The writing box is what the source drew, not the cell rect -- and for both
    kinds it is now the same measurement: the cell inset by the thickness of the
    rule bounding each side. A comb's used to be the extent of its own dividers
    (7.44pt inside an 18.90pt cell on 2551Q), which mistook a guide tick for a
    wall; `lattice.comb_on_writing_surface` carries the artwork that settles it
    and applies the identical inset there, publishing it as `writing_y0` /
    `writing_height_pt` beside the divider band, so a comb arrives already
    inset -- see `comb_writing_rect`, which is the one reader of it -- and only
    the plain-field branch below computes one.
    A cell rect runs along its rules' *centres*
    (rule v173 spans x 136.10-136.58 and the cell edge is 136.34), so half a
    thickness already clears the ink; using the whole one clears it and leaves
    the same distance again as clearance. It is the cell's own measurement
    rather than a constant, so a 1.44pt underline and a 0.24pt comb divider do
    not get the same margin.

    `regions` is the box after the strokes the source prints INSIDE it have
    been taken out: one inset per writing region, left to right, and `None`
    when the cell is the single region it looks like. A plain field is one
    region in 9,932 of this corpus's 9,971 cells and several in 39 of them,
    which is why `inset_trbl` stays what it always was -- the first region's,
    identical to the whole box wherever there is only one -- and every reader
    that does not know about regions keeps working on the boxes that have one.
    The metrics are shared across a cell's regions and that is geometry, not
    convenience: the strokes that divide a box are vertical, so every region
    has the same height and fits the same face at the same size.
    """

    __slots__ = ("kind", "inset_trbl", "size_pt", "line_height_pt",
                 "letter_spacing_pt", "capacity", "regions")

    def __init__(self, kind: str, inset_trbl: tuple[float, float, float, float] | None,
                 size_pt: float, line_height_pt: float,
                 letter_spacing_pt: float | None, capacity: int | None,
                 regions: tuple[tuple[float, float, float, float], ...] | None
                 = None) -> None:
        self.kind = kind
        self.inset_trbl = inset_trbl
        self.size_pt = size_pt
        self.line_height_pt = line_height_pt
        self.letter_spacing_pt = letter_spacing_pt
        self.capacity = capacity
        self.regions = regions

    @property
    def region_insets(self) -> tuple[tuple[float, float, float, float] | None, ...]:
        """One inset per writing region; a single-region box yields its own."""
        return self.regions if self.regions is not None else (self.inset_trbl,)

    @property
    def metrics_key(self) -> tuple[float, float, float | None]:
        return (self.size_pt, self.line_height_pt, self.letter_spacing_pt)


def comb_writing_rect(cell: dict[str, Any],
                      comb: dict[str, Any]) -> tuple[float, float]:
    """One comb's WRITING rectangle: absolute top, and height. Never the band.

    `lattice.comb_on_writing_surface` publishes two vertical extents for one
    comb because they answer two different questions, and every defect this
    function exists to prevent came from one caller reading the other one:

      * `y0` / `y1` / `height_pt` are the **source divider band** -- the extent
        of the tick marks themselves. They are the contract the comb referee's
        `classify_band` seeds its source topology from and the reviewed 2551Q
        control was signed against, so nothing emitted may restate them. This
        function never returns them when a writing rectangle exists.
      * `writing_y0` / `writing_y1` / `writing_height_pt` are the **writing
        box** -- the owning cell's printed walls inset by that cell's own
        border thicknesses, the identical inset `field_box` gives a plain text
        field.

    A tick is a guide mark *under* the box, not the box. On 2550M's item-4 TIN
    row the cell walls span the full 15.60pt of the row while the digit
    separators are 3.12pt stubs along its bottom edge; laying the slot div and
    its input on the band hands the taxpayer a 3.12pt typing surface fitted at
    a 2.81pt face, which is F186. Everything the emitter LAYS OUT for a comb --
    the slot rectangle, the band-template JSON the runtime re-lays cloned rows
    from, and the face `field_box` fits -- therefore comes from here, so those
    three can never disagree with each other again.

    The INPUT inside the slot is one step removed from this on purpose (F227):
    where the sheet has already printed into the slot's own shared top --
    1604CF's "8 Telephone No." over p1c16, 2316's "(MM/DD/YYYY)" hint over
    p1c38/p1c39 -- `field_box` insets the input off THIS rectangle by
    `comb_writing_top_clear_of_printed_ink`, never the other way around. The
    rectangle this function returns stays the referee's contract; only the
    typing surface nested inside it may be smaller.

    The fallback is the band, and it is for a layout that predates
    `comb_on_writing_surface` (and for the emitter's own synthetic fixtures),
    not a preference: measured over `build/layout`, all 4,561 combs in this
    corpus publish all three writing keys, `writing_y1 - writing_y0` equals
    `writing_height_pt` on every one of them, and every writing rectangle lies
    inside its own cell.
    """
    if ("writing_y0" in comb and "writing_y1" in comb
            and "writing_height_pt" in comb):
        return float(comb["writing_y0"]), float(comb["writing_height_pt"])
    return (float(comb.get("y0", cell["y0"])),
            float(comb.get("height_pt", float(cell["y1"]) - float(cell["y0"]))))


def comb_slot_edges(comb: dict[str, Any]) -> list[float]:
    """One comb's slot boundaries AS LAID OUT: N+1 absolute x, outer inset.

    The horizontal twin of `comb_writing_rect`, and it exists for the identical
    reason. `slot_x` runs rail CENTRE to rail centre -- that is how every
    boundary in the layout is positioned -- so a compartment laid straight onto
    it is laid across half of each outer rail's own printed ink. On 2551M the
    wall left of item 28C is painted x 238.92-239.64 and the caption's `C` inks
    to 239.5176, tucked under the rule; a box starting at the rail's centre of
    239.28 starts on top of the label with no blank paper between them, which is
    F199's seven money boxes and F208's mechanism.

    So the OUTER two edges come from `lattice.comb_writing_edges`, published as
    `writing_x0`/`writing_x1`, and every INTERNAL edge stays exactly its
    measured `divider_x`: a divider is one stroke shared by the compartments
    either side of it, and both are drawn against its centre. Everything the
    emitter lays out horizontally for a comb -- the slot rectangle, the input
    inside it, and the band-template JSON the runtime re-lays cloned rows from
    -- comes from here, so those three can never disagree.

    The fallback is `slot_x` unchanged, for a layout that predates the writing
    edges and for the emitter's own synthetic fixtures. It is not a preference:
    measured over `build/layout`, all 4,554 combs in this corpus publish both
    keys.
    """
    slot_x = [float(value) for value in comb["slot_x"]]
    if "writing_x0" not in comb or "writing_x1" not in comb:
        return slot_x
    return [float(comb["writing_x0"]), *slot_x[1:-1],
            float(comb["writing_x1"])]


# How many times the trim below may re-measure one box. Trimming one side can
# put a glyph that was inside the box outside it, which is a fact about the
# glyph and not an iteration artefact, so the loop re-measures rather than
# guessing. It is bounded because a bug must stop rather than hang: the corpus
# needs at most 2 passes on any of the 66 boxes that meet printed ink at all,
# and a box that has not settled in 8 is left as measured so far, never
# silently trusted.
INK_TRIM_MAX_PASSES = 8


def writing_box_clear_of_printed_ink(
        box: tuple[float, float, float, float],
        ink: "PrePrintedInk | None",
        ) -> tuple[float, float, float, float]:
    """One writing box, moved off the printed ink the sheet lays into it.

    The box a taxpayer types in is the paper the source left BLANK inside the
    cell, not the cell inset by its own rules. Those differ wherever the sheet
    sets a caption tight against the box it labels -- 1604CF prints "8
    Telephone No." so that the descender of its `p` crosses the box's top rule
    and hangs 0.67pt inside it, and 2551M prints the item code `14E` so that
    its first digit overhangs the box to its left by 0.72pt. Inset by rules
    alone, the emitted input covers that ink and a typed character lands on
    top of it.

    **The trim is on the side the ink comes FROM.** A glyph whose own centre
    lies above the box top is a line printed above this blank, so the blank
    starts under it; the same statement mirrored gives left and right. A glyph
    whose centre lies INSIDE the box implies no side at all -- the sheet has
    printed into the middle of the blank, which is a different defect
    (`field_verdict`'s pre-printed rule owns it) and one no trim can answer --
    so this returns the box unchanged rather than guessing which half to keep.

    **Three sides, not four, and the missing one is the whole reason this
    trims against a run's line box at all.** What the IR states about a printed
    run is its baseline, its face's ascent and its face's descent; per GLYPH it
    states only the advance box. Those bound the run's ink to very different
    accuracies, and a trim is only worth making against a tight bound:

      * *Downward* the line box is tight. Every character either carries a
        descender, which reaches the line box's own floor, or stops within a
        small overshoot of the baseline -- a fraction of a point at these
        sizes. So ink arriving from ABOVE is trimmed to that floor.
      * *Horizontally* the advance box is tight enough to act on. Side
        bearings are not derivable from the IR, and measured across the eleven
        faces this corpus sets they are hundredths of an em -- the 0.50 to
        0.72pt overhangs this trims are the artwork, not the bearing.
      * *Upward* the line box is NOT tight, and this is the one place it is
        loose by an order of magnitude. Its ceiling is the ASCENT line: where
        the face could put ink, not where this run does. A `.` stops about
        0.1em above the baseline and the ascent line is 0.9em above it. On
        2550M the leader of "Debit Memo................" runs between two rows
        of boxes and its ascent line reaches 2.61pt into the row above, with
        3.77pt of blank paper between that line and the dots themselves --
        confirmed against the source raster, where the dots sit wholly in the
        gutter. Trimming to it would take 2.61pt off a 10.32pt writing box and
        drop its fitted face from 8.25pt to 6.96pt for a collision that is not
        on the paper. Shrinking real output to satisfy a bound we know to be
        loose is the same error as widening a tolerance, pointed the other
        way, so ink arriving from BELOW is not trimmed. `audit.py` reads the
        same ascent line and reports those boxes; that report is the honest
        residue of a bound neither file can tighten, and the answer to it is
        to say so, not to move the box.

    The box is trimmed to the ink's own edge and no further. The audit scores
    an overlap at OVERLAP_EPS_PT = 0.05, so an input butted against the glyph
    beside it is correct and needs no clearance invented for it.
    """
    x0, y0, x1, y1 = box
    if ink is None:
        return box
    for _ in range(INK_TRIM_MAX_PASSES):
        if x1 <= x0 or y1 <= y0:
            break
        top = left = right = None
        for gx0, gy0, gx1, gy1 in ink.intrusions(x0, y0, x1, y1):
            centre_y = (gy0 + gy1) / 2.0
            centre_x = (gx0 + gx1) / 2.0
            if centre_y <= y0:
                top = gy1 if top is None else max(top, gy1)
            elif centre_y >= y1:
                continue    # the loose bound; see the docstring
            elif centre_x <= x0:
                left = gx1 if left is None else max(left, gx1)
            elif centre_x >= x1:
                right = gx0 if right is None else min(right, gx0)
        moved = (x0, y0, x1, y1)
        if top is not None:
            y0 = max(y0, top)
        if left is not None:
            x0 = max(x0, left)
        if right is not None:
            x1 = min(x1, right)
        if (x0, y0, x1, y1) == moved:
            break
    return (x0, y0, x1, y1)


def writing_regions(box: tuple[float, float, float, float],
                    partitions: Sequence[dict[str, Any]],
                    ) -> list[tuple[float, float, float, float]]:
    """One writing box, cut at the compartment dividers printed inside it.

    A cell is one box to the grid; the paper can say otherwise. 1604CF page 2
    rules "ADDRESS OF PAYEES" off "* STATUS" with a column border the whole
    table long, 2551M page 2 does the same between "Period Covered" and "Name
    of Withholding Agent", 2316 halves its "From (MM/DD)" boxes with a bottom
    guide tick and 2550M does the same to its TIN groups and its date box.
    Every one of those is one cell whose single wide input lies straight across
    a rule the source prints -- 39 inputs on 5 forms, and the entire population
    of `audit.check_inputs_span_no_printed_divider`.

    Two tests, and the corpus separates on both rather than being tuned to
    either. `lattice.printed_partitions` has already said which marks the
    composited page still shows; these say which of them divide THIS box:

      * **Wholly inside, horizontally.** A stroke whose ink crosses the box's
        edge is the box's own wall. No tolerance and no constant: the wall of a
        cell is drawn on the lattice line, so its ink straddles the edge, while
        a divider's lies between the edges.
      * **Deeper into the box than its own width.** A stroke that reaches in by
        less than the ink drawing it has not entered the box -- it is the next
        row's tick overshooting its baseline, which BIR's artwork does by 0.12
        to 0.42pt in 54 places on 19 forms. This is the ink-versus-paper test
        `band_ink` and `bottom_guide_tick_baseline` already use, asked of a
        stroke and a box instead of two strokes. Measured over the whole
        corpus, every grazing overshoot is at 0.25 to 0.87 of its own width and
        every real divider at 3.83 and up; nothing lies between.

    Regions are returned left to right, and a divider that would leave a region
    of no width simply does not make one -- two dividers can be one composite
    boundary, and a box with nothing between them is not a box.
    """
    x0, y0, x1, y1 = box
    cuts: list[tuple[float, float]] = []
    for span in partitions:
        ix0, iy0 = float(span["x0"]), float(span["y0"])
        ix1, iy1 = float(span["x1"]), float(span["y1"])
        if ix0 < x0 or ix1 > x1:
            continue
        if min(y1, iy1) - max(y0, iy0) <= ix1 - ix0:
            continue
        cuts.append((ix0, ix1))
    if not cuts:
        return [box]
    cuts.sort()
    regions: list[tuple[float, float, float, float]] = []
    left = x0
    for cut_x0, cut_x1 in cuts:
        if cut_x0 > left:
            regions.append((left, y0, cut_x0, y1))
        left = max(left, cut_x1)
    if x1 > left:
        regions.append((left, y0, x1, y1))
    return regions or [box]


def comb_writing_top_clear_of_printed_ink(
        comb: dict[str, Any], write_top: float, height: float,
        ink: "PrePrintedInk | None") -> float:
    """How far a comb's shared writing top must drop to clear printed ink above it.

    F227: three combs in this corpus sit directly under a caption whose own
    descender hangs into the row -- 1604CF's "8 Telephone No." over its phone
    comb (p1c16) and 2316's "(MM/DD/YYYY)" hint over its two date combs (p1c38,
    p1c39). `field_box`'s plain-field branch already answers this with
    `writing_box_clear_of_printed_ink`; the comb branch never called it at all,
    which is why these three were never offered the evidence the plain-field
    branch has used for every other cell since `ruled_blank_field_box`.

    `comb_writing_rect`'s own docstring says a comb's slot RECTANGLE is exempt
    and stays exempt -- it is cross-checked against the source's own painted
    walls by `comb_referee.writing_band_corroboration`, and moving it would
    break that correspondence for every comb in the corpus, not just these
    three. So this answers a narrower question than the plain-field trim does:
    not where the slot sits, but how much of its own shared top the sheet has
    already inked. `field_box` turns the answer into an `inset` on the INPUT
    inside each slot -- never on the slot itself -- which is exactly the shape
    `audit._comb_input_insets`'s own docstring anticipates: "an input inset
    inside its slot is exactly the shape a producer-side fix takes."

    **Only the top.** The same trim, run over the whole 53-form corpus, also
    reports a non-zero LEFT clearance on 7 compartments across three other
    forms (0605 p1c3, 2551M p1c74/79/86, 2553 p1c79/84/91 -- a neighbouring
    caption's last glyph grazing the comb's own left rail on its advance box).
    A comb's height is already shared across every slot in the row -- one face
    size fitted once, per the `min` in the size-fit comment below -- so a
    shared TOP clearance is the same kind of quantity the row already shares.
    Width is not: each compartment owns its own slot, and applying a shared
    LEFT/RIGHT inset across the whole row would shrink typing area in
    compartments the ink never reaches. None of that population is in F227 and
    none of it fails `inputs_over_printed_text` today, so only the vertical
    component is read here; the horizontal intrusions this trim would also
    report are deliberately left to `intrusions` (the same lookup, unused for
    combs) rather than acted on per-slot without evidence that any of them are
    real.
    """
    slot_x = comb_slot_edges(comb)
    box = (float(slot_x[0]), write_top, float(slot_x[-1]), write_top + height)
    _tx0, ty0, _tx1, _ty1 = writing_box_clear_of_printed_ink(box, ink)
    return ty0 - write_top


def field_box(cell: dict[str, Any], face: FieldFace,
              ink: "PrePrintedInk | None" = None) -> FieldBox | None:
    """Fit the body face into one field's writing box. None if it cannot fit.

    `ink` is the page's pre-printed glyphs. Passing None means that evidence
    was not measured and the box is the cell inset by its own rules, which is
    what every synthetic fixture wants and what no real emission may rely on --
    `FieldPlan` already reports an unmeasured IR as a warning of its own.
    """
    comb = cell.get("comb")
    regions: tuple[tuple[float, float, float, float], ...] | None = None
    if comb:
        kind, capacity = "comb", int(comb["cells"])
        write_top, height = comb_writing_rect(cell, comb)
        top_clear = comb_writing_top_clear_of_printed_ink(comb, write_top, height, ink)
        inset: tuple[float, float, float, float] | None = (
            (top_clear, 0.0, 0.0, 0.0) if top_clear > 0.0 else None)
        height -= top_clear
    else:
        kind, capacity = "text", None
        top = _border_thickness(cell, "top")
        right = _border_thickness(cell, "right")
        bottom = _border_thickness(cell, "bottom")
        left = _border_thickness(cell, "left")
        # A cell no bigger than its own borders on an axis gives up the
        # clearance on that axis rather than the box: clearance is the optional
        # part. 2551Q has eight 0.72pt-wide slivers classified as fields with a
        # 1.44pt rule down one side, and inset by it they would be inputs of
        # negative width.
        if top + bottom >= cell["y1"] - cell["y0"]:
            top = bottom = 0.0
        if left + right >= cell["x1"] - cell["x0"]:
            left = right = 0.0
        # The rules give the box; the sheet's own ink takes back whatever it
        # has already printed inside it. A comb is exempt and stays exempt: its
        # slot rectangle is bound to `writing_y0`/`writing_y1` and to `slot_x`,
        # which `comb_referee.writing_band_corroboration` re-derives from the
        # source's own painted walls, so a comb compartment that meets printed
        # ink is a fact to report and not a rectangle this may move.
        cx0, cy0 = float(cell["x0"]) + left, float(cell["y0"]) + top
        cx1, cy1 = float(cell["x1"]) - right, float(cell["y1"]) - bottom
        tx0, ty0, tx1, ty1 = writing_box_clear_of_printed_ink(
            (cx0, cy0, cx1, cy1), ink)
        top += ty0 - cy0
        bottom += cy1 - ty1
        left += tx0 - cx0
        right += cx1 - tx1
        inset = (top, right, bottom, left) if any((top, right, bottom, left)) else None
        height = (cell["y1"] - cell["y0"]) - top - bottom
        # Width is checked only on this branch, and only because the trim above
        # can take it away: a rule inset can never exceed the cell (the guard
        # above sees to that) whereas ink printed across a whole blank can.
        if (cell["x1"] - cell["x0"]) - left - right <= 0.0:
            return None
        # ... and last, the strokes the source rules ACROSS the box. Last
        # because each earlier step moves the box, and a divider divides the
        # box a taxpayer is actually given.
        cut = writing_regions(
            (float(cell["x0"]) + left, float(cell["y0"]) + top,
             float(cell["x1"]) - right, float(cell["y1"]) - bottom),
            cell.get("printed_partitions") or ())
        if len(cut) > 1:
            regions = tuple(
                (top, float(cell["x1"]) - rx1, bottom, rx0 - float(cell["x0"]))
                for rx0, _ry0, rx1, _ry1 in cut)
    if height <= 0.0 or face.line_span_em <= 0.0:
        return None

    # One fit for both kinds, and the `min` is the whole reason a comb cannot
    # outgrow the sheet it sits on. The fitted size is the smaller of what the
    # box allows and what the sheet prints its own body text at, so a comb whose
    # writing box is the full height of a 15.60pt row is set at the modal body
    # size (8.52pt on 2551Q) and not at the 13.96pt that height alone would
    # permit -- a taxpayer's TIN must not print larger than the label beside it.
    # Measured over the corpus with every comb band taken at its cell's full
    # height, all 4,522 land on the cap, so this is the binding constraint on
    # every comb once the band is derived correctly, not a rare guard.
    #
    # The other axis needs no cap, and that is measured rather than assumed: at
    # the capped size a digit's advance (0.556em in Arimo, 0.5em in Tinos)
    # exceeds the narrowest slot of 0 of those 4,522 combs, and of 1 even at a
    # pessimistic 0.6em. A width cap here would be speculative machinery earning
    # nothing, so there is none.
    size = min(face.size_pt, _floor2(height / face.line_span_em))
    if size <= 0.0:
        return None
    spacing = None
    if face.size_pt > 0 and abs(face.letter_spacing_pt) > 0:
        # Tracking is a per-em property of the run it was measured from, so it
        # travels to a refitted size in proportion rather than as absolute pt.
        scaled = round(face.letter_spacing_pt * size / face.size_pt, 4)
        if abs(scaled) >= LETTER_SPACING_EPSILON_PT:
            spacing = scaled
    return FieldBox(kind, inset, size, round(height, 4), spacing, capacity,
                    regions)


# extract.py's rule origin, restated here because emit.py reads the IR as JSON
# and carries no import of the module that produces it. Must read exactly
# `extract.RULE_ORIGIN_TEXT_UNDERSCORE`.
RULE_ORIGIN_TEXT_UNDERSCORE = "text-underscore"


def ruled_blank_field_box(cell: dict[str, Any], rules: Sequence[dict[str, Any]],
                          face: FieldFace, ink: "PrePrintedInk | None",
                          ) -> FieldBox | None:
    """The writing surface a label cell's own underscore-drawn rule(s) earn it.

    F148/F149: item 9 on 1701 page 4 prints "Other Tax Credits/Payments
    (specify)" followed by a ruled blank on the SAME line, and the caption and
    the blank are one `label` cell -- `field_verdict` refuses every `label`,
    so the taxpayer had no way to state what the credit was. **A ruled blank
    is written ON its line, not below it**: the strip lattice.py cuts beneath
    the rule is a 3.22pt sliver, too thin to hold a glyph, and correctly
    classified `blank` -- the writing space is the caption cell's OWN paper,
    to the right of the caption, sitting on the bar.

    One box per rule, in reading order (top to bottom, then left to right),
    because a caption can carry more than one blank on its own line -- 1706
    page 2 sets "____ % X ____ = ____" as three, and 1600-WP's masthead sets
    "Page ____ of ____" as two. `emit.RuledBlankWriting` has already resolved
    which rules belong to this cell before this is called; a rule claimed by
    two `label` cells at once (2550Q page 2's fraction bar under "Total
    Sales", underscored rather than drawn) is refused THERE, not here.

    Geometry, not a re-derivation of `field_box`'s: the box sits ABOVE its
    rule, seated on it -- `y1` is the rule's own top edge, so the box never
    overlaps the ink a taxpayer's own line is ruled with, and `x0`/`x1` are
    the rule's own extent, because that is exactly the span the sheet left
    blank (the caption's glyphs end before the underscores begin; there is no
    printed ink to trim horizontally). Height is capped at ONE line -- the
    modal body face's own natural line box -- never the cell's remaining
    height, which is most of a wrapped caption's own second line on some
    forms. `writing_box_clear_of_printed_ink` still runs per box, because nothing
    here has looked at whether some OTHER run (a caption two lines up) hangs
    into it, and that function already answers exactly that question.

    A cell's regions share one metrics class (`FieldBox.metrics_key`), so
    every surviving box is reclamped to the SHORTEST one after its own trim:
    two blanks on one line where only one meets printed ink still render at
    one size, one line, like every other multi-region field in this corpus.
    """
    if face.line_span_em <= 0.0:
        return None
    ordered = sorted(rules, key=lambda r: (float(r["y0"]), float(r["x0"])))
    one_line = face.size_pt * face.line_span_em
    cy0 = float(cell["y0"])

    boxes: list[tuple[float, float, float, float]] = []
    for rule in ordered:
        headroom = float(rule["y0"]) - cy0
        if headroom <= 0.0:
            continue
        height = min(headroom, one_line)
        rx0, rx1, ry1 = float(rule["x0"]), float(rule["x1"]), float(rule["y0"])
        ry0 = ry1 - height
        tx0, ty0, tx1, ty1 = writing_box_clear_of_printed_ink((rx0, ry0, rx1, ry1), ink)
        if tx1 - tx0 <= 0.0 or ty1 - ty0 <= 0.0:
            continue
        # A glyph printed ON the line at its left end is the line's own
        # printed PREFIX, and the writable surface starts after it: 2551M
        # sets its item number "26" 1.68pt into its "Title/Position of
        # Signatory" line (the only such site in the corpus -- every other
        # claimed rule prints its item number BEFORE the line begins).
        # `writing_box_clear_of_printed_ink` deliberately refuses to trim a
        # glyph whose centre is inside the box, because in general "which
        # half to keep" is a guess; on a WRITING LINE it is not -- reading
        # order settles it. Anchored means the glyph starts within its OWN
        # ink height of the current left edge, a self-referential bound
        # with no tuned constant: an item number is at most one glyph tall,
        # so a glyph one glyph-height into the line is still the prefix,
        # while text printed mid-line (a filled-in blank) stays where it is
        # for `inputs_over_printed_text` to refuse -- pre-printed ink deep
        # in a line is a defect to report, never one to type over.
        if ink is not None:
            while True:
                anchored = [
                    (gx0, gy0, gx1, gy1)
                    for gx0, gy0, gx1, gy1 in ink.intrusions(
                        tx0, ty0, tx1, ty1)
                    if gx1 > tx0 and gx0 - tx0 < (gy1 - gy0)
                ]
                if not anchored:
                    break
                tx0 = max(gx1 for _gx0, _gy0, gx1, _gy1 in anchored)
                if tx1 - tx0 <= 0.0:
                    break
        if tx1 - tx0 <= 0.0:
            continue
        boxes.append((tx0, ty0, tx1, ty1))
    if not boxes:
        return None

    shared_height = min(y1 - y0 for _x0, y0, _x1, y1 in boxes)
    size = min(face.size_pt, _floor2(shared_height / face.line_span_em))
    if size <= 0.0:
        return None
    spacing = None
    if face.size_pt > 0 and abs(face.letter_spacing_pt) > 0:
        scaled = round(face.letter_spacing_pt * size / face.size_pt, 4)
        if abs(scaled) >= LETTER_SPACING_EPSILON_PT:
            spacing = scaled

    cx0, cx1, cy1 = float(cell["x0"]), float(cell["x1"]), float(cell["y1"])
    # Every region's top is reclamped to its own bottom minus the ONE shared
    # height, not to whatever its own (possibly less-trimmed) box left it at
    # -- see the docstring's last paragraph.
    insets = tuple(
        ((y1 - shared_height) - cy0, cx1 - x1, cy1 - y1, x0 - cx0)
        for x0, _y0, x1, y1 in boxes
    )
    if len(insets) == 1:
        return FieldBox("text", insets[0], size, round(shared_height, 4),
                        spacing, None, None)
    return FieldBox("text", insets[0], size, round(shared_height, 4),
                    spacing, None, insets)


class RuledBlankWriting:
    """Which `label` cells a ruled blank earns an input, per page (F148/F149).

    Two filters, both load-bearing. A rule must be `RULE_ORIGIN_TEXT_UNDERSCORE`
    -- extract.py's own statement that it measured the bar off underscore
    glyphs rather than a path operator (`extract.ruled_blank_bars`) -- and it
    must be `role == "structural"` (`gray == 0.0`): 58 of this corpus's 118
    underscore-drawn bars are `knockout` (`gray == 1.0`, white-on-colour
    lettering inside a legend/swatch, 1600-PT/1600-VT/1606/1706), and every
    one of them sits over paper the lattice never even cut a cell for. Tone
    is the same discriminator CLAUDE.md already requires for every other rule
    on this sheet: width says how thick a stroke is, only grey says whether a
    taxpayer is meant to see it.

    A rule claimed by more than one `label` cell is refused rather than
    guessed at. 2550Q page 2 sets "VAT Exempt Sale" over "Total Sales" as a
    fraction, and the bar between them -- drawn with underscore glyphs, not a
    path -- straddles the boundary between the two caption cells exactly the
    way F148's own writing line straddles `label`/`blank`. There the second
    cell is empty; here it holds "Total Input Tax attributable to Exempt
    Sale", so admitting it would print a live `<input>` over that caption's
    own printed text -- the one population `inputs_over_printed_text` exists
    to catch. Ownership by exactly one `label` cell is what tells a writing
    line from a fraction bar the sheet happened to draw the same way.
    """

    __slots__ = ("_claims",)

    def __init__(self, rules: Sequence[dict[str, Any]],
                 cells: Sequence[dict[str, Any]]) -> None:
        label_cells = [c for c in cells if c["kind"] == "label"]
        claims: dict[str, list[dict[str, Any]]] = {}
        for rule in rules:
            if (rule.get("origin") != RULE_ORIGIN_TEXT_UNDERSCORE
                    or rule.get("role") != "structural"):
                continue
            rx0, ry0 = float(rule["x0"]), float(rule["y0"])
            rx1, ry1 = float(rule["x1"]), float(rule["y1"])
            owners = [c for c in label_cells
                     if float(c["x0"]) <= rx0 and rx1 <= float(c["x1"])
                     and ry0 < float(c["y1"]) and ry1 > float(c["y0"])]
            if len(owners) != 1:
                continue
            claims.setdefault(owners[0]["id"], []).append(rule)
        self._claims = claims

    def for_cell(self, cell_id: str) -> Sequence[dict[str, Any]]:
        return self._claims.get(cell_id, ())


# The size band a checkbox square's own KNOCKOUT interior falls in --
# `checkbox_square_boxes`' box detector -- measured across the corpus's real
# population rather than guessed: every one of the 22 real squares measures
# 9.12-12.0pt on both axes, comfortably inside a 4-20pt band that is wide
# enough to admit them and narrow enough to exclude every wide reference-table
# knockout F206 catalogued separately (the narrowest of those is 56pt).
CHECKBOX_SQUARE_MIN_PT = 4.0
CHECKBOX_SQUARE_MAX_PT = 20.0
# The float-fuzz margin ADDED to a candidate rule's own half-thickness, never
# a substitute for it -- see `checkbox_square_boxes` for why half-thickness is
# the exact, measured tolerance and not a tuned guess.
CHECKBOX_SQUARE_EDGE_SLACK_PT = 0.02


def checkbox_square_boxes(rules: Sequence[dict[str, Any]],
                          fills: Sequence[dict[str, Any]],
                          ) -> list[dict[str, Any]]:
    """Every closed decorative-rule box whose interior is a knockout square.

    F210, CLAUDE.md's own mechanism, restated once and read from here on: the
    square is drawn by four DECORATIVE rules (`role == "decorative"`, the tone
    band strictly between structural black and knockout white) closing a box,
    and the source paints the interior back to paper with a KNOCKOUT fill
    (`role == "knockout"`) -- the sheet's own "write here". `lattice.tone_role`
    calls that frame decorative and `lattice.build_page` builds the x/y
    lattice from structural rules only, so this area never becomes a lattice
    boundary; the caption glyphs beside it ("Taxpayer", "Yes", ...) then
    swallow the whole region into one `label` cell, and `field_verdict`
    refuses every `label` before this ever runs. See its `checkbox-square`
    branch, the one reader of this function's output.

    The tolerance matching a candidate rule to the fill's own edge is NOT a
    constant: it is that rule's OWN half-thickness (plus the fixed float-fuzz
    margin above), because the knockout is painted to the frame's INNER edge
    while the "centre" read off each rule here is the stroke's geometric
    centre -- exactly half its own thickness from that inner edge. Measured
    over the corpus's 22 real squares, the observed deviation is exactly
    0.24pt on every 0.48pt-thick frame and exactly 0.36pt on every 0.72pt-thick
    one -- precisely half their own stroke, with no spread -- so this is the
    tolerance the paper itself states, not one tuned to make a count match.

    A closed box needs FOUR rules and they need not be four DISTINCT ones: two
    checkboxes stacked in one frame (1701 Schedule 1's Taxpayer/Spouse pair)
    share their left and right verticals, drawn as one continuous stroke
    spanning both boxes. So only the horizontal top/bottom edges are matched
    on LENGTH as well as position -- a rule whose own x-extent does not match
    the fill's is some other band merely passing close by, exactly the trap a
    page-wide 0.8509 row separator sets 1.5pt from a real box edge on
    1700-2018 -- while the verticals are matched on COVERAGE alone, never
    length, or the shared-wall case above would be refused.

    Corpus-wide this selects exactly 22 squares on exactly 3 forms
    (1700-2018: 14, 1701-2018: 6, 1701a-2018: 2), independently corroborated
    by the tone-aware `?debug=fields` overlay's `vacant` census computed in a
    browser from the rendered SVG -- a completely different code path
    reporting the identical 6/14/2.
    """
    h_dec = [r for r in rules
            if r.get("axis") == "h" and r.get("role") == "decorative"]
    v_dec = [r for r in rules
            if r.get("axis") == "v" and r.get("role") == "decorative"]
    squares: list[dict[str, Any]] = []
    for fill in fills:
        if fill.get("role") != "knockout":
            continue
        fx0, fy0 = float(fill["x0"]), float(fill["y0"])
        fx1, fy1 = float(fill["x1"]), float(fill["y1"])
        width, height = fx1 - fx0, fy1 - fy0
        if not (CHECKBOX_SQUARE_MIN_PT <= width <= CHECKBOX_SQUARE_MAX_PT
                and CHECKBOX_SQUARE_MIN_PT <= height <= CHECKBOX_SQUARE_MAX_PT):
            continue

        def find_h(y_target: float) -> dict[str, Any] | None:
            for rule in h_dec:
                tol = float(rule["thickness_pt"]) / 2.0 + CHECKBOX_SQUARE_EDGE_SLACK_PT
                centre = (float(rule["y0"]) + float(rule["y1"])) / 2.0
                if (abs(centre - y_target) <= tol
                        and abs(float(rule["x0"]) - fx0) <= tol
                        and abs(float(rule["x1"]) - fx1) <= tol):
                    return rule
            return None

        def find_v(x_target: float) -> dict[str, Any] | None:
            for rule in v_dec:
                tol = float(rule["thickness_pt"]) / 2.0 + CHECKBOX_SQUARE_EDGE_SLACK_PT
                centre = (float(rule["x0"]) + float(rule["x1"])) / 2.0
                if (abs(centre - x_target) <= tol
                        and float(rule["y0"]) <= fy0 + tol
                        and float(rule["y1"]) >= fy1 - tol):
                    return rule
            return None

        top, bottom = find_h(fy0), find_h(fy1)
        left, right = find_v(fx0), find_v(fx1)
        if top is None or bottom is None or left is None or right is None:
            continue
        squares.append({
            "x0": fx0, "y0": fy0, "x1": fx1, "y1": fy1,
            "top_pt": float(top["thickness_pt"]),
            "bottom_pt": float(bottom["thickness_pt"]),
            "left_pt": float(left["thickness_pt"]),
            "right_pt": float(right["thickness_pt"]),
        })
    return squares


class CheckboxSquareWriting:
    """Which `label` cells a checkbox square earns an input, per page (F210).

    Mirrors `RuledBlankWriting`'s shape exactly, for the identical reason: a
    `label` cell may hold more than one square (1701 Schedule 1's row holds
    both the Taxpayer and the Spouse box; item 8 holds the Yes and the No
    box), each earning its own region, and a square whose interior is not
    contained in exactly one `label` cell is refused rather than guessed at.
    Measured over the corpus this refusal never fires -- all 22 squares land
    inside exactly one `label` cell each -- but it is kept because nothing
    here may assume the shape of a form it has not read.
    """

    __slots__ = ("_claims",)

    def __init__(self, rules: Sequence[dict[str, Any]],
                 fills: Sequence[dict[str, Any]],
                 cells: Sequence[dict[str, Any]]) -> None:
        label_cells = [c for c in cells if c["kind"] == "label"]
        claims: dict[str, list[dict[str, Any]]] = {}
        for square in checkbox_square_boxes(rules, fills):
            sx0, sy0 = square["x0"], square["y0"]
            sx1, sy1 = square["x1"], square["y1"]
            owners = [c for c in label_cells
                     if float(c["x0"]) <= sx0 and sx1 <= float(c["x1"])
                     and float(c["y0"]) <= sy0 and sy1 <= float(c["y1"])]
            if len(owners) != 1:
                continue
            claims.setdefault(owners[0]["id"], []).append(square)
        self._claims = claims

    def for_cell(self, cell_id: str) -> Sequence[dict[str, Any]]:
        return self._claims.get(cell_id, ())


def checkbox_square_field_box(cell: dict[str, Any],
                              squares: Sequence[dict[str, Any]],
                              face: FieldFace, ink: "PrePrintedInk | None",
                              ) -> FieldBox | None:
    """The single-character writing surface a checkbox square earns (F210).

    One region per square, in reading order (top to bottom, then left to
    right) -- a `label` cell can hold more than one, the same shape
    `ruled_blank_field_box` answers for a caption's own writing line.

    The box is the square's own interior: the knockout fill's rectangle,
    inset by the FRAME rule's own thickness on each side -- the identical
    clearance `field_box` gives an ordinary cell (see its docstring for why
    the full thickness, not half, is the deliberate margin). This is a
    mark-with-an-X box, so one character is the whole capacity. The input
    stays `type="text"` -- the official client never ships a checkbox
    widget -- and `field_input_markup` stamps `maxlength="1"` because
    `input_is_single_character` sees a square in this size band.

    A cell's regions share one metrics class, so -- exactly as
    `ruled_blank_field_box` reclamps two blanks on one caption line to the
    shorter one -- every surviving region here is reclamped to the SHORTEST
    one after its own trim. It is reclamped CENTRED rather than seated on an
    edge: a checkbox square has no baseline to write a line of text on, so
    there is no "seat on the rule" reason `ruled_blank_field_box` has to
    anchor at the bottom, and a mark meant to land inside a small square is
    better centred in it than pinned to one side.
    """
    if face.line_span_em <= 0.0:
        return None
    ordered = sorted(squares, key=lambda s: (s["y0"], s["x0"]))

    boxes: list[tuple[float, float, float, float]] = []
    for square in ordered:
        x0 = square["x0"] + square["left_pt"]
        y0 = square["y0"] + square["top_pt"]
        x1 = square["x1"] - square["right_pt"]
        y1 = square["y1"] - square["bottom_pt"]
        tx0, ty0, tx1, ty1 = writing_box_clear_of_printed_ink((x0, y0, x1, y1), ink)
        if tx1 - tx0 <= 0.0 or ty1 - ty0 <= 0.0:
            continue
        boxes.append((tx0, ty0, tx1, ty1))
    if not boxes:
        return None

    shared_height = min(y1 - y0 for _x0, y0, _x1, y1 in boxes)
    size = min(face.size_pt, _floor2(shared_height / face.line_span_em))
    if size <= 0.0:
        return None
    spacing = None
    if face.size_pt > 0 and abs(face.letter_spacing_pt) > 0:
        scaled = round(face.letter_spacing_pt * size / face.size_pt, 4)
        if abs(scaled) >= LETTER_SPACING_EPSILON_PT:
            spacing = scaled

    cx0, cx1 = float(cell["x0"]), float(cell["x1"])
    cy0, cy1 = float(cell["y0"]), float(cell["y1"])
    insets = tuple(
        (((y0 + y1 - shared_height) / 2.0) - cy0, cx1 - x1,
         cy1 - ((y0 + y1 + shared_height) / 2.0), x0 - cx0)
        for x0, y0, x1, y1 in boxes
    )
    if len(insets) == 1:
        return FieldBox("text", insets[0], size, round(shared_height, 4),
                        spacing, None, None)
    return FieldBox("text", insets[0], size, round(shared_height, 4),
                    spacing, None, insets)


# The evidence the sheet prints when it reserves a bordered box for the
# TAXPAYER's own signature (F211), mirroring BUREAU_RESERVED_PREFIXES
# exactly: a caption STARTS with its subject, so this is a prefix test, not a
# substring one -- 0619E/0619F's sworn-declaration block prints "For
# Individual:" over one box and "For Non-Individual:" over its neighbour, one
# caption run each, and every other form carrying the identical block sets
# the identical two captions. Measured corpus-wide (build/ir + build/layout,
# 53 bundles, 2026-08-11): 54 caption runs on 27 forms, exactly two per form.
SIGNATURE_BOX_CAPTION_PREFIXES = ("for individual", "for non-individual")

# The bordered-box population this rule is even asked about, all four
# measured rather than guessed. `border_count` >= 3 is the same floor
# `lattice.classify_cell` already uses to call an empty box a writing
# surface; the two size floors are generous enough to admit the smallest
# real signature box in the corpus (0619F `p1c101`, 273.58 x 31.56pt) while
# excluding the sea of small printed-reference cells that also carry a
# partial frame. Over the full corpus this selects 126 cells -- NOT one
# population: 54 are the taxpayer's own signature boxes above (the one this
# class claims), 71 are Bureau-only stamp/validation boxes a taxpayer must
# never be able to type in (already recognised by `BureauReservation`'s own
# captions, and never claimed here because `_signature_box_caption` and
# `BUREAU_RESERVED_PREFIXES` share no word), and 1 (`1600wp-2010` `p1c93`) is
# neither -- it is the plain column-number header "(8)" of a table ("(4)",
# "(5)", "(6)", "(7)" sit beside it, each too narrow to clear
# SIGNATURE_BOX_MIN_WIDTH_PT), wide only because it is the table's last
# column and runs to the page's right margin. It is left untouched, by
# construction: its caption matches no prefix here, so this class never
# claims it and it keeps its `label` verdict.
SIGNATURE_BOX_MIN_BORDERS = 3
SIGNATURE_BOX_MIN_HEIGHT_PT = 20.0
SIGNATURE_BOX_MIN_WIDTH_PT = 100.0
# How much of the box's OWN height the caption must be confined to, measured
# from the top. Every one of the 54 real signature captions sits within the
# top 21.6% of its box (0619F's, the tallest fraction, at 31.56pt); the
# fraction is left generous rather than tuned tight to that number, because
# the population this rule must never touch (the 71 Bureau boxes) is kept out
# by caption TEXT, not by this band -- see `BUREAU_RESERVED_PREFIXES` and the
# routing through `BureauReservation` at `field_verdict`'s call site.
SIGNATURE_BOX_CAPTION_BAND = 0.4


def _signature_box_caption(text: str) -> bool:
    """Whether this run is a caption dedicating the box it sits in to the
    taxpayer's own signature (F211)."""
    normalised = " ".join(text.split()).lower()
    return any(normalised.startswith(prefix)
               for prefix in SIGNATURE_BOX_CAPTION_PREFIXES)


class SignatureBoxWriting:
    """Which `label` cells the sheet's own top-left caption dedicates to the
    TAXPAYER's own signature (F211).

    Each box's only printed ink is one caption run at its top-left corner --
    "For Individual: " or "For Non-Individual: " -- which sets `is_empty =
    False`, so `lattice.classify_cell` returns `label` and `field_verdict`
    refuses it before this ever runs: one caption run costs a writing surface
    as large as 302 x 43pt. `0620-2019` `p1c87` is the control -- the
    identical signature box with no caption printed inside it, `is_empty =
    True`, classified `field`, shipping a working input today -- so the
    defect is exactly this: the geometry is a writing surface either way, and
    only the caption's PRESENCE, not its content, decided whether the sheet
    was allowed to keep its own writing space.

    Ownership is per cell, not per run, because every real signature box in
    this corpus carries exactly one caption run and the population this rule
    must never admit -- the Bureau's own reserved boxes -- is excluded by
    `_signature_box_caption` refusing their words, not by any cardinality
    test here.

    `field_verdict` still routes every cell this class claims through
    `BureauReservation` before promoting it (see that call site) rather than
    trusting this class's own caption test as the only gate -- the ordering
    trap F211 itself named: `BureauReservation` already exists and already
    recognises "Machine Validation...", "Stamp of Receiving Office/AAB..."
    and "Stamp of Authorized Agent Bank...", but a `label` cell used to be
    refused before that check was ever consulted, so a promotion rule that
    bypassed it instead of routing through it would risk exactly the 71
    Bureau boxes this rule is not allowed to touch.
    """

    __slots__ = ("_claims",)

    def __init__(self, cells: Sequence[dict[str, Any]], page_index: int,
                 runs: Sequence[dict[str, Any]]) -> None:
        claims: dict[str, list[dict[str, Any]]] = {}
        for cell, cell_runs, caption in signature_box_candidates(
                cells, page_index, runs):
            if _signature_box_caption(caption):
                claims[cell["id"]] = cell_runs
        self._claims = claims

    def for_cell(self, cell_id: str) -> Sequence[dict[str, Any]]:
        return self._claims.get(cell_id, ())

    def cell_ids(self) -> frozenset[str]:
        """Every cell this class claims, for `SignatureLineBinding`'s own
        candidate test -- a promoted signature box is a valid binding target
        for the "Signature over Printed Name..." caption below it exactly
        the way a pre-existing `field` cell is."""
        return frozenset(self._claims)


def signature_box_candidates(
        cells: Sequence[dict[str, Any]], page_index: int,
        runs: Sequence[dict[str, Any]],
        ) -> Iterator[tuple[dict[str, Any], list[dict[str, Any]], str]]:
    """Every bordered box whose printed ink is confined to a top-left caption.

    The geometric half of F211's rule, split out from `SignatureBoxWriting`
    so `signature_box_corpus_assertions` can ask the identical question of
    the OTHER caption vocabulary a candidate might carry -- a Bureau
    reservation instead of a taxpayer signature -- without maintaining a
    second copy of the five thresholds. Every yielded cell, its own claimed
    text runs and their joined caption text; the caller decides what the
    caption means. Corpus-wide (build/ir + build/layout, 53 bundles,
    2026-08-11) this selects exactly 126 cells: 54 caption "For Individual:"
    / "For Non-Individual:" (`SignatureBoxWriting`'s own claim, see it), 71
    caption one of `BUREAU_RESERVED_PREFIXES` / `BUREAU_RESERVED_SUBSTRINGS`,
    and 1 (`1600wp-2010` `p1c93`) caption neither -- the plain column-number
    header "(8)" of a table, wide only because it is the table's last column
    and runs to the page's right margin; it is claimed by nothing on either
    side and keeps its `label` verdict.
    """
    runs_by_id = {run_id(page_index, i): run for i, run in enumerate(runs)}
    for cell in cells:
        if cell["kind"] != "label":
            continue
        if cell["border_count"] < SIGNATURE_BOX_MIN_BORDERS:
            continue
        width = float(cell["x1"]) - float(cell["x0"])
        height = float(cell["y1"]) - float(cell["y0"])
        if (height < SIGNATURE_BOX_MIN_HEIGHT_PT
                or width < SIGNATURE_BOX_MIN_WIDTH_PT):
            continue
        run_ids = cell.get("text_run_ids") or ()
        cell_runs = [runs_by_id[rid] for rid in run_ids if rid in runs_by_id]
        if not cell_runs:
            continue
        top_limit = float(cell["y0"]) + SIGNATURE_BOX_CAPTION_BAND * height
        if max(float(r["y1"]) for r in cell_runs) > top_limit:
            continue
        yield cell, cell_runs, " ".join(r["text"] for r in cell_runs)


def signature_box_field_box(cell: dict[str, Any], captions: Sequence[dict[str, Any]],
                            face: FieldFace, ink: "PrePrintedInk | None",
                            ) -> FieldBox | None:
    """The writing surface a signature box's own top-left caption leaves it (F211).

    Geometry, not a re-derivation of `field_box`'s: the box is the cell inset
    by its own border thickness on every side, exactly as an ordinary field
    gets, and then its TOP is raised again to clear the caption -- to the
    caption's own lowest line-box edge, the same evidence
    `writing_box_clear_of_printed_ink` reads for ink hanging in from a line
    above. That function is not the mechanism here and is called afterward
    only as the same defensive belt `ruled_blank_field_box` and
    `checkbox_square_field_box` both keep: `SignatureBoxWriting` has already
    proven every OTHER run in this cell sits in the top
    `SIGNATURE_BOX_CAPTION_BAND` of its height, so nothing besides the
    caption itself should ever intrude on the remainder, and this call
    catches it if that measurement is ever wrong on a form not yet in this
    corpus.

    A cell this class is asked about carries exactly one caption cluster, so
    -- unlike `ruled_blank_field_box`'s and `checkbox_square_field_box`'s
    several regions on one line -- there is one box, seated on the FULL
    remaining width: the caption is confined to the box's own top band by
    construction, so trimming the whole strip below it can never cut into the
    caption's own horizontal extent, and it is what a taxpayer signing "over"
    that caption's line actually gets, corner to corner.
    """
    if not captions:
        return None
    top = _border_thickness(cell, "top")
    right = _border_thickness(cell, "right")
    bottom = _border_thickness(cell, "bottom")
    left = _border_thickness(cell, "left")
    cx0, cy0 = float(cell["x0"]) + left, float(cell["y0"]) + top
    cx1, cy1 = float(cell["x1"]) - right, float(cell["y1"]) - bottom
    caption_bottom = max(float(r["y1"]) for r in captions)
    cy0 = max(cy0, caption_bottom)
    tx0, ty0, tx1, ty1 = writing_box_clear_of_printed_ink((cx0, cy0, cx1, cy1), ink)
    if tx1 - tx0 <= 0.0 or ty1 - ty0 <= 0.0 or face.line_span_em <= 0.0:
        return None
    height = ty1 - ty0
    size = min(face.size_pt, _floor2(height / face.line_span_em))
    if size <= 0.0:
        return None
    spacing = None
    if face.size_pt > 0 and abs(face.letter_spacing_pt) > 0:
        scaled = round(face.letter_spacing_pt * size / face.size_pt, 4)
        if abs(scaled) >= LETTER_SPACING_EPSILON_PT:
            spacing = scaled
    inset = (ty0 - float(cell["y0"]), float(cell["x1"]) - tx1,
             float(cell["y1"]) - ty1, tx0 - float(cell["x0"]))
    return FieldBox("text", inset, size, round(height, 4), spacing, None, None)


def _signature_line_caption(text: str) -> bool:
    """Whether this run names both a signature and a printed name -- BIR's
    own words for "this is where you sign" (F212), in either order the
    corpus sets them: "Signature over/and Printed Name of ..." on the
    majority of this corpus's forms, "Printed Name and Signature of ..." on
    five more (1700, 1701, 1701A, 1701MS, 2550-DS) plus 2200S's two officer
    lines and 1701Q's own "Signature and Printed Name of Taxpayer...".
    Neither word alone would do: "printed name" alone also names 1701A item
    19's caption strip (F207), and "signature" alone would catch prose that
    merely mentions one nearby. Together they select 87 runs corpus-wide,
    of which one -- 2316 `p1t218`, a duplicate "Employee Signature over
    Printed Name" -- is not assigned to any lattice cell at all and so cannot
    bind anything.
    """
    normalised = " ".join(text.split()).lower()
    return "signature" in normalised and "printed name" in normalised


def _signatory_detail_caption(text: str) -> bool:
    """Whether this run is one of BIR's signatory-detail captions -- the
    words the corpus sets under the OTHER ruled lines of a jurat strip
    (user decision, 2026-08-16: the Title/Position line of 0605's item 22A
    strip "needs to have its own input field", generalised to the caption
    family, never to one form). Three phrasings, measured corpus-wide
    (build/ir, 53 bundles): "Title/Position of Signatory" on 8 runs (0605,
    1600WP, 1604CF x2, 2550M x2, 2551M, 2553), "Title of Signatory" on 8
    (the 1702 family, whose strips are field cells that already carry
    inputs, so no binding arises there), "TIN of Signatory" on 5 (1600WP,
    1604CF x2, 2550M x2).

    A FULL match on the normalised run, never containment: 1604-E and
    1604-F page 2 set the words "title of signatory and" INSIDE an
    instruction paragraph, and a containment test would dedicate whatever
    rule that paragraph happens to sit over. Normalisation collapses
    1604CF's double-spaced "TIN of  Signatory" and strips 2553's leading
    spaces; nothing else in the corpus comes close.
    """
    normalised = " ".join(text.split()).lower()
    return normalised in {
        "title/position of signatory",
        "title of signatory",
        "tin of signatory",
    }


# Float fuzz only, not a tuned tolerance: a caption's owning cell and the box
# above it share the identical lattice wall coordinate, so the measured gap
# over all 75 real bindings in this corpus is exactly 0.0pt -- see
# `SignatureLineBinding`'s own docstring.
SIGNATURE_LINE_ADJACENCY_EPSILON_PT = 0.01


class SignatureLineBinding:
    """Which boxes a "Signature (over|and) Printed Name..." caption printed
    directly BELOW them dedicates to a bottom-seated, centred writing line
    (F212) -- the `BureauReservation` precedent, reversed: there a caption
    printed ABOVE a blank reserves it for the Bureau; here a caption printed
    BELOW a box says what belongs on the line at its bottom. Both readings
    come from the identical fact about how BIR sets type: a caption is
    printed immediately against the blank it describes.

    **The binding is geometric and exact, not fuzzy.** For each caption run,
    the compartment directly ABOVE its own owning cell -- `y1` equal to the
    owning cell's `y0`, and the run's own x-centre inside the candidate's
    `x0..x1` -- is the box it governs, the mirror image of how
    `BureauReservation` reads the compartment a caption is printed inside.
    Measured over the 75 real bindings this corpus admits, the y-gap is
    0.0pt on every one (the two cells share one lattice wall) and the
    x-centre clears its candidate's nearer edge by 116.7pt at the closest, so
    `SIGNATURE_LINE_ADJACENCY_EPSILON_PT` is float fuzz, not a constant tuned
    to make a count match.

    **The candidate must already be a writing surface, or a box
    `SignatureBoxWriting` has already claimed -- never any adjacent label.**
    Geometry alone is not enough: 2316's "Present Employer/... Signature over
    Printed Name" caption sits directly below its own "Date Signed" sub-row
    (kind `label`, a different field entirely) with the REAL signature box
    two rows further up, separated by that sub-row and a 1.32pt blank
    divider -- an unconstrained adjacency test binds the wrong cell instead
    of refusing outright. Restricting the candidate's kind removes that false
    positive and every other still-open case in the corpus at once: 0605,
    1604CF, 2550M, 2551M and 2553 each set their signature LINE as a vector
    rule drawn inside the SAME cell as its own caption, not as a separate box
    above it -- a different shape, refused rather than guessed at, exactly
    `RuledBlankWriting`'s and `CheckboxSquareWriting`'s own precedent for
    ownership that does not resolve to exactly one claimant. Multiple
    geometric candidates are refused the same way; none occur in this
    corpus.

    Measured corpus-wide (build/ir + build/layout, 53 bundles, 2026-08-11):
    75 boxes across 43 forms are bound. 54 are the signature boxes this same
    session's `SignatureBoxWriting` creates -- every one of them also carries
    this caption on the label cell below it -- and 21 are pre-existing
    `field` cells, `1701-2018` `p1c125` among them: the strip the user typed
    their name into and confirmed correct. 11 of the 86 reachable caption
    runs are refused, all for the reason above.
    """

    __slots__ = ("_claims",)

    def __init__(self, cells: Sequence[dict[str, Any]], page_index: int,
                 runs: Sequence[dict[str, Any]],
                 signature_box_cell_ids: frozenset[str]) -> None:
        runs_by_id = {run_id(page_index, i): run for i, run in enumerate(runs)}
        claims: dict[str, list[dict[str, Any]]] = {}
        for cell in cells:
            for rid in cell.get("text_run_ids") or ():
                run = runs_by_id.get(rid)
                if run is None or not _signature_line_caption(run["text"]):
                    continue
                x_centre = (float(run["x0"]) + float(run["x1"])) / 2.0
                candidates = [
                    other for other in cells
                    if other["id"] != cell["id"]
                    and abs(float(other["y1"]) - float(cell["y0"]))
                        <= SIGNATURE_LINE_ADJACENCY_EPSILON_PT
                    and float(other["x0"]) <= x_centre <= float(other["x1"])
                    and (other["kind"] == "field"
                         or other["id"] in signature_box_cell_ids)
                ]
                if len(candidates) != 1:
                    continue
                claims.setdefault(candidates[0]["id"], []).append(run)
        self._claims = claims

    def for_cell(self, cell_id: str) -> Sequence[dict[str, Any]]:
        return self._claims.get(cell_id, ())


def seat_signature_line(box: FieldBox | None, one_line_pt: float) -> FieldBox | None:
    """Re-seat a signature strip's writing box at the CELL's own bottom edge,
    exactly one line tall (F212).

    "Signature over Printed Name" puts the writing ON the line directly above
    the printed name, at the bottom of the box the sheet rules for it -- not
    floating at the box's vertical centre, which is what an ordinary field's
    `line-height` equal to its FULL box height produces (`1701-2018`
    `p1c125-i` measured at font-size 9pt / line-height 33.24pt in a 34.20pt
    strip, so the caret sits at mid-height). The fix is arithmetic on the
    box `field_box` (or `signature_box_field_box`) already computed, not a
    new box: only the TOP inset grows, by the full difference between the
    box's own height and one natural line at the fitted face, so the BOTTOM
    inset -- already the cell's own bottom border clearance -- is untouched
    and the strip stays seated exactly where it always was, just shorter.

    A box already one line tall or shorter is returned unchanged: there is
    nothing to seat further down, and growing its inset would push text
    outside the box the sheet actually rules. A comb, or a box the sheet has
    already cut into several regions with its own dividers, is returned
    unchanged too -- fail closed, matching `ruled_blank_field_box`'s and
    `checkbox_square_field_box`'s own refusal to guess ownership it cannot
    resolve to exactly one claim; measured over this corpus's 75 bound boxes,
    every one is a single-region plain field, so this guard is stated, not
    exercised, but nothing here may assume the shape of a form it has not
    read.

    Horizontal centring is a SEPARATE declaration, applied at markup time by
    `field_input_markup` to every input this box belongs to (see
    `FieldPlan.centered`) -- this function only moves the box vertically.
    """
    if (box is None or box.kind != "text" or box.regions is not None
            or box.inset_trbl is None or box.line_height_pt <= one_line_pt):
        return box
    top, right, bottom, left = box.inset_trbl
    new_top = round(top + (box.line_height_pt - one_line_pt), 4)
    return FieldBox(box.kind, (new_top, right, bottom, left), box.size_pt,
                    round(one_line_pt, 4), box.letter_spacing_pt, box.capacity, None)


class SignatureRuleWriting:
    """Which `label` cells own a vector-drawn line at their own bottom wall,
    directly above a "Signature over Printed Name" caption printed in the
    cell below (F221, case 1) -- `RuledBlankWriting`'s own relation, reused
    rather than reinvented, with two things swapped for it: the rule there is
    UNDERSCORE-drawn text wholly inside the caption's own cell; here it is a
    VECTOR rule straddling the wall this cell shares with the next one down,
    and the caption that names it is not in this cell at all -- it is in the
    cell below, `SignatureLineBinding`'s own "caption below, candidate above"
    reading, because a `label` cell (unlike a `field` cell or a
    `SignatureBoxWriting` claim) is not a candidate `SignatureLineBinding`
    will ever bind to.

    0605-1999, 1604cf-2008, 2550m-2007, 2551m-2002 and 2553-1999 each set
    their jurat declaration ("I declare, under the penalties of perjury...")
    and one or two item numbers in ONE `label` cell, with a vector rule ruled
    across the bottom of that SAME cell for each signatory -- the line a
    taxpayer signs on -- and set "Signature over Printed Name of..." (or
    2550M's "(Signature Over Printed Name)") as a caption in the NEXT cell
    down, against the identical wall the rule is drawn on.
    `SignatureLineBinding`'s own box-above test requires the candidate
    directly above the caption to already be `kind == "field"`; here it is
    `label`, because it also carries the jurat paragraph and the item
    number, so that test never fires and the rule is never associated with
    anything a taxpayer can type into.

    Ownership is a STRADDLE test, not `RuledBlankWriting`'s containment: the
    rule's own thickness is centred exactly on the two cells' shared wall (a
    printed rule doubling as the lattice boundary that separates them), so
    `rule["y0"] <= cell["y1"] <= rule["y1"]` is what "this rule is drawn at
    THIS cell's own bottom" means here -- measured at 0.0pt slack on every
    one of the 9 rules this selects. A rule at a cell's own TOP never
    satisfies this test for the cell whose CAPTION lives below it, which is
    what keeps a header rule (2550M's `h100`, 452.64-575.28pt, sitting
    exactly over the x-range where item 28's caption centres) from being
    claimed: its centre sits at the cell's own TOP wall, not its bottom.

    A rule is claimed only when exactly one caption in a cell directly below
    (sharing this cell's own bottom wall, `SignatureLineBinding`'s own
    `SIGNATURE_LINE_ADJACENCY_EPSILON_PT`) names it -- its x-centre inside
    the rule's own x-extent. A claiming caption is either a signature
    caption (`_signature_line_caption`) or a signatory-detail caption
    (`_signatory_detail_caption`: "Title/Position of Signatory", "Title of
    Signatory", "TIN of Signatory") -- the second family added by the
    user's 2026-08-16 decision that the other ruled lines of a jurat strip
    earn a typing surface exactly the way the signature line always has.
    0605 rules three lines at this cell's own bottom: two signature
    captions and one title caption, all three claimed; 2551M and 2553 each
    rule two, the second under its own "Title/Position of Signatory".
    `RuledBlankWriting`'s own precedent for ownership that does not resolve
    to exactly one claimant still holds: refused, not guessed at.

    Where the box is drawn: reused whole, not re-derived --
    `ruled_blank_field_box`'s "the box sits ABOVE its rule, seated on it" is
    the identical geometry a signature line needs, one line tall, x-extent
    the rule's own extent, trimmed by whatever ink hangs into it from above
    (the jurat paragraph, on every one of these forms, sits well clear of
    it). `field_verdict` calls that function directly with this class's own
    claims; there is no second field-box function to keep in step with it.

    Measured over this corpus (build/ir + build/layout, 53 bundles): 8
    signature rules across the 5 forms named above, plus a 9th this class
    reaches without being told to look for it -- `2316-2021`'s own item 55
    ("I declare... qualified under substituted filing...") sets its rule
    and its caption's owning cell directly adjacent, the identical shape,
    so it is claimed the same way, not special-cased for the form. The
    signatory-detail family adds 12 more, every one at an exact 0.0pt
    shared wall, no new ambiguity anywhere: 0605 1 (the title line the
    user asked after), 1600WP 2, 1604CF 3, 2550M 4, 2551M 1, 2553 1 --
    23 claimed rules in all, with the two F226 gap sites below.

    **F226: the caption need not share the rule-owner's own wall exactly --
    it may sit across a small vertical GAP, provided nothing is printed in
    it.** Two more 2316 sites are this shape: item 53 ("Date Signed") owns
    its rule at its own bottom wall, y=770.10, but the caption naming it
    ("Present Employer/Authorized Agent Signature over Printed Name") is not
    in the cell sharing that wall -- it is one cell further down, across a
    1.32pt blank sliver with no caption of its own; item 54's own rule
    (y=801.30) is separated from its caption the same way, across a 0.54pt
    span the lattice made no cell for AT ALL (an ungridded hole, not merely
    an empty cell). Both gaps are real: `lattice.classify_cell` genuinely cut
    a sliver cell (or nothing) rather than fusing the wall, exactly the
    fusion `is_one_boundary` was measured NOT to perform for these two pairs
    (F222/W7's own corpus-wide fusion audit).

    The gap is bridged only when it is **smaller than the form's own
    `glyph_height_pt`** (`lattice.min_fillable_line_metrics`'s own sliver-rule
    metric -- no new constant) **and carries no printed ink anywhere across
    the rule's own x-extent.** This is deliberately geometric -- the
    candidate caption's own cell top minus the rule-owner's own cell bottom,
    never a cell-hop count -- so the 0.54pt ungridded hole needs no special
    handling: there is no cell to hop across, only a vertical distance to
    measure. It also refuses the one wrong-binding shape this exact form
    offers to THIS class's own search direction (a rule-owning cell looking
    DOWN for its caption, never `SignatureLineBinding`'s own caption-looking-
    UP test, which W4b already fixed separately by restricting its own
    candidates to `kind == "field"`): `p1c322`'s own rule h178 (the jurat-
    paragraph/field-divider rule, straddling `p1c322`'s own bottom wall,
    y=748.62) is 22.8pt above the nearest x-matching caption, `p1c327`'s own
    "Present Employer/Authorized Agent Signature over Printed Name" --
    correctly refused, measured directly against this corpus: 22.8pt is
    more than 4x this form's own `glyph_height_pt` (4.65pt), AND the gap
    itself carries real printed ink ("Date Signed"/"53", `p1c324`'s own
    text) across the rule's own x-extent. Either test alone refuses it, and
    both hold together, because a row that holds text is at least one glyph
    tall by definition and can never fit under `glyph_height_pt`. The two
    real 2316 gaps (1.32pt, 0.54pt) both clear glyph-free.

    A third 2316 site, item 56's own caption ("Employee Signature over
    Printed Name", `p1t218`), stays open regardless: its run is not assigned
    to ANY lattice cell on the page (confirmed directly, unmoved by this
    extension), so there is no "candidate cell's own top" for this
    geometric test -- or any other cell-to-cell adjacency test -- to read at
    all. `_signature_line_caption`'s own docstring already names it as
    unreachable this way; closing it needs a `lattice.py` change to how a
    run is assigned to a cell, out of this class's own scope.

    Measured corpus-wide (build/ir + build/layout, 53 bundles, F226): the
    extension binds exactly these two additional rules and nothing else --
    no form outside 2316-2021 carries a `label` cell whose owned rule has an
    ink-free, sub-`glyph_height_pt` gap to exactly one signature caption
    below it that the old exact-wall test did not already claim.
    """

    __slots__ = ("_claims", "_gap_bound", "_gap_refused")

    def __init__(self, cells: Sequence[dict[str, Any]], page_index: int,
                 rules: Sequence[dict[str, Any]],
                 runs: Sequence[dict[str, Any]],
                 metrics: dict[str, float] | None = None) -> None:
        runs_by_id = {run_id(page_index, i): run for i, run in enumerate(runs)}
        ink = PrePrintedInk(runs)
        glyph_height = (float(metrics["glyph_height_pt"])
                        if metrics is not None else None)
        label_cells = [c for c in cells if c["kind"] == "label"]
        claims: dict[str, list[dict[str, Any]]] = {}
        # F226's own two corpus-wide witnesses: `_gap_bound` counts a rule
        # actually claimed across a genuine gap (the sliver-gap population
        # this extension exists for); `_gap_refused` counts a REAL candidate
        # caption -- one that matches this rule's own x-range -- found near
        # a rule-owning cell across a genuine gap and correctly declined,
        # either too tall or carrying real ink (h178's own 22.8pt/inked
        # case, and every other near-miss this corpus carries). Both are
        # asserted `> 0` corpus-wide so neither side of the guard is
        # vacuous.
        gap_bound = 0
        gap_refused = 0
        for cell in label_cells:
            owned = [
                r for r in rules
                if r.get("origin") != RULE_ORIGIN_TEXT_UNDERSCORE
                and r.get("role") == "structural"
                and float(cell["x0"]) <= float(r["x0"])
                and float(r["x1"]) <= float(cell["x1"])
                and float(r["y0"]) <= float(cell["y1"]) <= float(r["y1"])
            ]
            if not owned:
                continue
            cell_bottom = float(cell["y1"])
            matched = []
            for rule in owned:
                rx0, rx1 = float(rule["x0"]), float(rule["x1"])
                hits: list[tuple[dict[str, Any], float]] = []
                for other in cells:
                    if other["id"] == cell["id"]:
                        continue
                    # ALL matching captions this candidate carries, not just
                    # the first -- two captions in the SAME cell must still
                    # count as two hits (the `ambiguous` fixture's own
                    # shape), exactly as the exact-wall test always has.
                    captions = [
                        run for rid in (other.get("text_run_ids") or ())
                        for run in (runs_by_id.get(rid),)
                        if (run is not None
                            and (_signature_line_caption(run["text"])
                                 or _signatory_detail_caption(run["text"]))
                            and rx0 <= (float(run["x0"]) + float(run["x1"])) / 2.0 <= rx1)
                    ]
                    if not captions:
                        continue
                    gap = float(other["y0"]) - cell_bottom
                    if gap < -SIGNATURE_LINE_ADJACENCY_EPSILON_PT:
                        continue
                    if gap > SIGNATURE_LINE_ADJACENCY_EPSILON_PT:
                        # A genuine gap, not the two cells' shared wall: only
                        # bridge it when it is smaller than a glyph and the
                        # rule's own x-extent carries no printed ink across
                        # it -- see the class docstring's own h178 refusal.
                        if glyph_height is None or gap >= glyph_height:
                            gap_refused += len(captions)
                            continue
                        if _gap_has_ink(ink, rx0, cell_bottom, rx1,
                                        cell_bottom + gap):
                            gap_refused += len(captions)
                            continue
                    hits.extend((caption, gap) for caption in captions)
                if len(hits) == 1:
                    matched.append(rule)
                    if hits[0][1] > SIGNATURE_LINE_ADJACENCY_EPSILON_PT:
                        gap_bound += 1
            if matched:
                claims[cell["id"]] = matched
        self._claims = claims
        self._gap_bound = gap_bound
        self._gap_refused = gap_refused

    def for_cell(self, cell_id: str) -> Sequence[dict[str, Any]]:
        return self._claims.get(cell_id, ())

    def gap_bound_count(self) -> int:
        """How many rules this instance claimed across a genuine (non-wall)
        gap -- the sliver-gap population's own positive witness."""
        return self._gap_bound

    def gap_refused_count(self) -> int:
        """How many real candidate captions, matching a rule's own x-range
        across a genuine gap, were declined -- too tall, inked, or both --
        the guard's own negative witness."""
        return self._gap_refused


# Restated from `lattice.min_fillable_line_metrics` -- emit.py reads the IR as
# JSON and carries no import of the module that computes it (the same reason
# `RULE_ORIGIN_TEXT_UNDERSCORE` above is a restated literal, not an import).
# Must stay the identical computation: `glyph_height_pt` is the smallest body
# run's own cap-height in points, `line_width_pt` is two em squares of that
# run's own size, both derived only from runs of two or more non-whitespace
# glyphs. `knockout_specify_corpus_assertions` cross-checks this against
# `lattice.min_fillable_line_metrics` directly on every self-test run, so a
# drift between the two trips immediately rather than silently.
def _min_fillable_line_metrics(ir: dict[str, Any]) -> dict[str, float] | None:
    fonts = ir.get("fonts") or {}
    cap_ratio_by_font: dict[str, float] = {}
    for key, descriptor in fonts.items():
        if not isinstance(descriptor, dict):
            continue
        base = str(descriptor.get("basefont") or key)
        stripped = base.split("+", 1)[-1]
        capheight = descriptor.get("capheight")
        if (isinstance(capheight, (int, float)) and capheight > 0
                and stripped not in cap_ratio_by_font):
            cap_ratio_by_font[stripped] = float(capheight) / 1000.0

    glyph_height: float | None = None
    line_width: float | None = None
    for page in ir.get("pages", ()):
        for run in page["text_runs"]:
            if sum(1 for ch in run["text"] if not ch.isspace()) < 2:
                continue
            size = float(run["size_pt"])
            ratio = cap_ratio_by_font.get(str(run["font"]), float(run["ascender"]))
            height = size * ratio
            if glyph_height is None or height < glyph_height:
                glyph_height = height
            if line_width is None or 2.0 * size < line_width:
                line_width = 2.0 * size
    if glyph_height is None or line_width is None:
        return None
    return {"glyph_height_pt": glyph_height, "line_width_pt": line_width}


# F206's marker for the "part caption, part writing surface" family (F148,
# F149, F151's own kin): the widest ink-free band whose topmost paint is a
# KNOCKOUT over a decorative tint, at >= 1x the form's own two-glyph line
# width. `lattice.classify_cell` never segments this: the caption glyphs set
# `is_empty = False`, so the whole cell -- caption AND the blank paper beside
# it -- is one refused `label`, exactly the shape `RuledBlankWriting` and
# `CheckboxSquareWriting` already fix for a printed underscore and a knockout
# square. This is the same family's third member, and the writing surface a
# KNOCKOUT band earns is a sub-region of the cell, never a reclassification of
# it (F206's own conclusion) -- `knockout_specify_field_box` seats the region
# exactly where `checkbox_square_field_box`/`ruled_blank_field_box` seat theirs.
#
# Two refinements measurement forced beyond F206's own prose, both load-bearing
# against its own named residue -- 8 cells across 3 forms (0605-1999
# p2c58/p2c121, 2553-1999 p1c37/p1c42/p1c47/p1c52, 1600wp-2010 p1c30/p1c33),
# every one an ATC-code or rate cell on a reference table, none a writing
# surface:
#
#   * A fill only counts as "this cell's own tint" (or "this cell's own
#     knockout") if its overlap with the cell clears a REAL size on both
#     axes, not a hairline. A neighbouring row's background fill grazes a
#     cell's own top edge by 0.12-0.33pt at 0605 p2c58 and 1600wp p1c30/p1c33
#     -- registration overlap, not this cell's own paint -- and admitting it
#     lets the widest band search treat the WHOLE cell as fair game.
#     `KNOCKOUT_FILL_MEANINGFUL_OVERLAP_PT` is `extract.MAX_RULE_THICKNESS_PT`
#     (1.5pt) restated: the identical floor extraction already uses to tell a
#     fill from a rule, applied here to tell a fill that belongs to this cell
#     from one that merely touches it.
#   * A candidate cell's own assigned text runs block by their WHOLE line box
#     (`x0..x1`, `y0..y1`), not per glyph. 2553's ATC-code captions ("OT  0
#     1   0") are printed with wide inter-character gaps to align with a
#     reference grid; a per-glyph ink test leaves those gaps "free" and the
#     widest-rectangle search reports the full cell width. The whole run's own
#     territory is not writable paper merely because a letterform does not
#     ink every point of it.
#
# Against the 9 named cells this measures the strict separation the residue
# is defined by: 1801-2018 p2c261 ("Others (specify)") clears
# `KNOCKOUT_FILL_MEANINGFUL_OVERLAP_PT` on a genuine tint-white-tint tiled row
# and reports a 294.65pt band (32x its own line width); every one of the 8
# residue cells reports either no band at all or one under 1x. See
# `knockout_specify_corpus_assertions` for the standing, corpus-wide version
# of this measurement, and this package's report for the full selected-cell
# list.
KNOCKOUT_FILL_MEANINGFUL_OVERLAP_PT = 1.5   # extract.MAX_RULE_THICKNESS_PT, restated

# The caption vocabulary this class promotes: BIR's own invitation to write
# free text, not a fixed reference code. "Others (specify)", "(please
# specify)", "If yes, specify" -- every spelling in the corpus carries this
# one word. It is what separates 1801 p2c261 from 2553's "OT  0   1   0" and
# 1604CF's table headers, all of which ALSO carry a wide knockout-over-tint
# band (measured: 1604CF alone contributes 15 such bands) but state a fixed
# code or an empty column, never an invitation. Substring, case-insensitive,
# because the corpus sets it embedded ("please specify", "Specify Foreign
# Tax Number") as often as standalone.
KNOCKOUT_SPECIFY_CAPTION_MARKER = "specify"


def _knockout_specify_caption(text: str) -> bool:
    return KNOCKOUT_SPECIFY_CAPTION_MARKER in text.lower()


def knockout_specify_band(cell: dict[str, Any], area_fills: Sequence[dict[str, Any]],
                          runs_by_id: dict[str, dict[str, Any]],
                          metrics: dict[str, float],
                          ) -> tuple[float, float, float, float] | None:
    """This cell's own widest ink-free knockout-over-tint band, or None.

    Every point of the cell is resolved to its topmost fill (or to this
    cell's own printed ink, which is always topmost); a point counts as a
    band candidate only when that topmost fill is `role == "knockout"` --
    never bare paper, which is `dropping the "over a tint" requirement`
    F206 itself measured destroys the marker (123 cells, mostly white-on-
    white reference tables). `has_knockout`/`has_decorative` gate the whole
    cell first, both past `KNOCKOUT_FILL_MEANINGFUL_OVERLAP_PT` on both axes,
    so a cell with no tint anywhere in its own paper is never searched at all.

    The widest RECTANGLE, not the bounding box of a merely-connected region:
    a caption's own ink can sit in the middle of a wide knockout-over-tint
    cell with clear paper on both sides, and a flood fill's bounding box
    would then claim the full cell width even though no single straight band
    of that width is ink-free (measured on 2553's "OT  0   1   0" cells
    before this was fixed: a reported 4x-line-width band that was actually a
    ring around the caption, not a band). Classic largest-rectangle-in-
    histogram, one row of atoms at a time, heights accumulated in actual
    points because atom rows are not uniform height.
    """
    x0, y0 = float(cell["x0"]), float(cell["y0"])
    x1, y1 = float(cell["x1"]), float(cell["y1"])
    if x1 <= x0 or y1 <= y0:
        return None

    layers: list[tuple[float, float, float, float, int, str]] = []
    has_knockout = has_decorative = False
    for fill in area_fills:
        cx0 = max(float(fill["x0"]), x0)
        cx1 = min(float(fill["x1"]), x1)
        if cx1 <= cx0:
            continue
        cy0 = max(float(fill["y0"]), y0)
        cy1 = min(float(fill["y1"]), y1)
        if cy1 <= cy0:
            continue
        role = fill.get("role")
        meaningful = ((cx1 - cx0) > KNOCKOUT_FILL_MEANINGFUL_OVERLAP_PT
                      and (cy1 - cy0) > KNOCKOUT_FILL_MEANINGFUL_OVERLAP_PT)
        if role == "knockout" and meaningful:
            has_knockout = True
        elif role == "decorative" and meaningful:
            has_decorative = True
        ordinal = int(fill.get("paint_seq_max", fill.get("paint_seq", -1)))
        layers.append((cx0, cy0, cx1, cy1, ordinal, role))
    if not (has_knockout and has_decorative):
        return None

    # This cell's own printed ink -- the caption -- is always topmost, and
    # blocks by its WHOLE line box (see the module comment above for why not
    # per glyph).
    ink_ordinal = max((layer[4] for layer in layers), default=0) + 1
    for run_id in cell.get("text_run_ids") or ():
        run = runs_by_id.get(run_id)
        if run is None:
            continue
        cx0 = max(float(run["x0"]), x0)
        cx1 = min(float(run["x1"]), x1)
        if cx1 <= cx0:
            continue
        cy0 = max(float(run["y0"]), y0)
        cy1 = min(float(run["y1"]), y1)
        if cy1 <= cy0:
            continue
        layers.append((cx0, cy0, cx1, cy1, ink_ordinal, "ink"))

    xs = sorted({x0, x1} | {edge for layer in layers for edge in (layer[0], layer[2])})
    ys = sorted({y0, y1} | {edge for layer in layers for edge in (layer[1], layer[3])})
    columns, rows = len(xs) - 1, len(ys) - 1

    candidate = [[False] * columns for _ in range(rows)]
    for j in range(rows):
        my = (ys[j] + ys[j + 1]) / 2.0
        for i in range(columns):
            mx = (xs[i] + xs[i + 1]) / 2.0
            covering = [(ordinal, role) for cx0, cy0, cx1, cy1, ordinal, role in layers
                       if cx0 < mx < cx1 and cy0 < my < cy1]
            if not covering:
                continue
            covering.sort()
            _top_ordinal, top_role = covering[-1]
            if top_role == "knockout":
                candidate[j][i] = True

    min_height = float(metrics["glyph_height_pt"])
    min_width = float(metrics["line_width_pt"])
    best_width = 0.0
    best_rect: tuple[float, float, float, float] | None = None
    heights = [0.0] * columns
    for j in range(rows):
        row_height = ys[j + 1] - ys[j]
        for i in range(columns):
            heights[i] = heights[i] + row_height if candidate[j][i] else 0.0
        stack: list[tuple[int, float]] = []
        for i in range(columns + 1):
            height = heights[i] if i < columns else 0.0
            start = i
            while stack and stack[-1][1] > height:
                popped_start, popped_height = stack.pop()
                if popped_height >= min_height:
                    width = xs[i] - xs[popped_start]
                    if width > best_width:
                        best_width = width
                        best_rect = (xs[popped_start], ys[j + 1] - popped_height,
                                    xs[i], ys[j + 1])
                start = popped_start
            stack.append((start, height))
    if best_rect is None or best_width < min_width:
        return None
    return best_rect


class KnockoutSpecifyWriting:
    """Which `label` cells a knockout-over-tint band, beside a "(specify)"
    caption, earns an input (F206).

    Mirrors `RuledBlankWriting`'s and `CheckboxSquareWriting`'s own shape: a
    label cell whose printed caption invites free text and whose own paper
    carries the band `knockout_specify_band` measures earns a sub-region, not
    a reclassification of the whole cell. Ownership is per cell -- a cell has
    at most one caption cluster carrying "specify" in this corpus, so there
    is no multi-claimant case to refuse, unlike a ruled blank's several
    underscores or a checkbox pair's two squares.
    """

    __slots__ = ("_claims",)

    def __init__(self, cells: Sequence[dict[str, Any]], page_index: int,
                 runs: Sequence[dict[str, Any]],
                 area_fills: Sequence[dict[str, Any]],
                 metrics: dict[str, float] | None) -> None:
        claims: dict[str, tuple[float, float, float, float]] = {}
        if metrics is None:
            self._claims = claims
            return
        runs_by_id = {run_id(page_index, i): run for i, run in enumerate(runs)}
        for cell in cells:
            if cell["kind"] != "label" or cell.get("comb"):
                continue
            caption = " ".join(
                runs_by_id[rid]["text"] for rid in cell.get("text_run_ids") or ()
                if rid in runs_by_id)
            if not _knockout_specify_caption(caption):
                continue
            band = knockout_specify_band(cell, area_fills, runs_by_id, metrics)
            if band is not None:
                claims[cell["id"]] = band
        self._claims = claims

    def for_cell(self, cell_id: str) -> tuple[float, float, float, float] | None:
        return self._claims.get(cell_id)


def knockout_specify_field_box(cell: dict[str, Any],
                               band: tuple[float, float, float, float],
                               face: FieldFace, ink: "PrePrintedInk | None",
                               ) -> FieldBox | None:
    """The writing surface a knockout-over-tint band earns (F206).

    Geometry, not a re-derivation of `field_box`'s: the box is the band's own
    rectangle -- the knockout paper `knockout_specify_band` already measured,
    clipped to nothing further, because that band IS the sheet's own blank
    paper and the whole reason it was found. `writing_box_clear_of_printed_ink`
    still runs, the same defensive belt `ruled_blank_field_box` and
    `checkbox_square_field_box` both keep: nothing here has looked at whether
    some OTHER run on the page (not this cell's own caption, already excluded
    by construction) hangs into the band, and that function already answers
    exactly that question.
    """
    if face.line_span_em <= 0.0:
        return None
    bx0, by0, bx1, by1 = band
    tx0, ty0, tx1, ty1 = writing_box_clear_of_printed_ink((bx0, by0, bx1, by1), ink)
    if tx1 - tx0 <= 0.0 or ty1 - ty0 <= 0.0:
        return None
    height = ty1 - ty0
    size = min(face.size_pt, _floor2(height / face.line_span_em))
    if size <= 0.0:
        return None
    spacing = None
    if face.size_pt > 0 and abs(face.letter_spacing_pt) > 0:
        scaled = round(face.letter_spacing_pt * size / face.size_pt, 4)
        if abs(scaled) >= LETTER_SPACING_EPSILON_PT:
            spacing = scaled
    inset = (ty0 - float(cell["y0"]), float(cell["x1"]) - tx1,
             float(cell["y1"]) - ty1, tx0 - float(cell["x0"]))
    return FieldBox("text", inset, size, round(height, 4), spacing, None, None)


# F151's Schedule D half, and P2's own measured generalisation of it
# corpus-wide: a bare row number -- "1 ", "2 ", "12", nothing else -- printed
# where BIR always prints one, at the head of a row that also carries a
# fillable field. `lattice.classify_cell` swallows the numeral AND the blank
# paper beside it into one `label` cell exactly the way it swallows a ruled
# blank's line, a checkbox's square or a "(specify)" knockout band into
# theirs; a taxpayer can fill the row's amount but never say what the row
# IS. Pure digits, no punctuation: "12" is a row number, "12." or "(12)" is
# not ONLY a numeral and is left alone -- the vocabulary this class promotes
# is as narrow as `KNOCKOUT_SPECIFY_CAPTION_MARKER`'s, just numeric instead
# of lexical.
ROW_NUMBER_TEXT_RE = re.compile(r"^\d{1,3}$")


def row_number_band(cell: dict[str, Any], runs_by_id: dict[str, dict[str, Any]],
                    metrics: dict[str, float], ink: "PrePrintedInk | None",
                    ) -> tuple[float, float, float, float] | None:
    """The paper beside a bare row number, or None if it is not viable.

    Geometry only -- no field, no comb: this is a candidacy TEST, asked of
    one `label` cell's own ink and its own remaining paper, exactly the
    question `lattice.min_fillable_line_metrics`'s sliver rule already asks
    of an EMPTY bordered cell, asked here of a non-empty one instead.

    The numeral's own ink edge is read the same way `PrePrintedInk` reads
    every other glyph in this module -- `_glyph_spans`' per-character widths,
    not the run's advance box, so the trailing space `text_run_ids` always
    carries after a row number ("1 ") is never counted as the numeral's own
    mark. The candidate band starts there and runs to the cell's own right
    wall: that is the paper the numeral's row has left blank, precisely the
    paper `KnockoutSpecifyWriting`'s "(specify)" band and `RuledBlankWriting`'s
    underscore line are the SAME shape of evidence for.

    Two measured bounds, both restated from `metrics`
    (`lattice.min_fillable_line_metrics`) rather than a new constant, per
    CLAUDE.md and per P2's own measurement (2026-08-10): the trailing blank
    must clear the form's own `line_width_pt` at 1.0x -- P2's corpus census
    is 188 cells below 0.5x (BIR's own narrow item-number boxes, "12" inside
    a box barely wider than two digits), 52 more between 0.5x and 1.0x, and
    56 at or past 1.0x, 45 of those past 2.0x. The 1.0x line is the one that
    keeps the narrow item-number population out while admitting Schedule D's
    452.7pt row (blank >= 2x on this metric) -- and the cell must be tall
    enough to hold one line of the form's own smallest body text
    (`glyph_height_pt`), the same floor the sliver rule already applies to an
    EMPTY strip, asked here of this one instead.

    **The band must carry no OTHER printed ink, corpus-wide, not just this
    cell's own.** `writing_box_clear_of_printed_ink` trims ink hanging in
    from OUTSIDE a box; it deliberately leaves alone a glyph whose own
    centre lies INSIDE the box, on the reasoning that the sheet printed
    something in the middle of the blank -- a different defect
    `field_verdict`'s `PREPRINTED_COVERAGE` rule owns for a plain `field`
    cell, and one this promotion never reached before F151 measured it:
    0605-1999 assigns "  For the           Calendar           Fiscal" (a
    checkbox caption, one wide run with big gaps for the boxes) to a cell
    well to this one's right, but that run's own glyphs -- "For the" --
    physically overlap p1c81's rectangle, the same "the row_number's box
    centre is not where its ink is" shape CLAUDE.md and `assign_points`
    already document for `printed_box_peers_all_fillable`. `intrusions`
    checked here, not `coverage`: this band claims to be BLANK, not merely
    under some threshold of ink, so ANY glyph reaching into it -- from any
    run, owned by this cell or not -- refuses the candidacy. Corpus-wide
    this refuses exactly the one cell it exists for and moves nothing else;
    `inputs_over_printed_text` stays at its own pre-existing 2 forms/5.
    """
    rids = cell.get("text_run_ids") or ()
    runs = [runs_by_id[rid] for rid in rids if rid in runs_by_id]
    text = "".join(run["text"] for run in runs).strip()
    if not ROW_NUMBER_TEXT_RE.match(text):
        return None
    spans = [span for run in runs for span in _glyph_spans(run)]
    if not spans:
        return None
    x0, y0 = float(cell["x0"]), float(cell["y0"])
    x1, y1 = float(cell["x1"]), float(cell["y1"])
    ink_x1 = max(span[2] for span in spans)
    if ink_x1 >= x1:
        return None
    if (x1 - ink_x1) < float(metrics["line_width_pt"]):
        return None
    if (y1 - y0) < float(metrics["glyph_height_pt"]):
        return None
    if ink is not None and ink.intrusions(ink_x1, y0, x1, y1):
        return None
    return (ink_x1, y0, x1, y1)


ATC_CONSTANT_RE = re.compile(r"^[A-Z]{2} ?[0-9]{3}$")


class PrintedDecoration:
    """Cells the sheet DECORATES rather than offers for writing (F235/F237).

    Three relations, every one census-first, every census recorded in the
    findings ledger before a clause was written, and the user approved the
    populations on the official sheets (2026-08-15 decisions page):

    * **comb-separator-fill** -- a cell carrying a DEDICATED non-white fill
      (the fill's own rectangle is the cell's, within 1pt) that sits BETWEEN
      two character combs in its own row. 6 cells corpus-wide, all TIN group
      separators: 2553-1999 p1c19/21/23 (peach rgb 1.0,0.8,0.6) and
      1604cf-2008 p1c8/10/12 (grey 0.8902). Neither colour nor width is the
      signal -- 1604CF's are as wide as real character boxes -- the signal is
      decoration drawn FOR a cell between combs. Refuted on the way here:
      width-alone (misses 1604CF), has-fill (strips 2,061 legitimate money
      boxes on tint), dedicated-fill-alone (1,619 white writing knockouts),
      dedicated-non-white-alone (2200AN's two writable schedule cells).
    * **printed-constant** -- a cell INTERSECTED by a printed ATC-format
      constant. Exactly 1 corpus-wide: 1800-2018 p1c186, the sliver the row
      mosaic cut from the bottom of the sheet's ONE undivided 'DN 010' box.
      The three legitimate ATC write-ins (1702MX/Q/RT second row) sit 3.5pt
      BELOW their example constants with no overlap and are untouched.
      Refuted on the way here: crossed-by-any-run (10 hits, mostly 2550M
      payment boxes under caption leader dots -- real fields).
    * **sub-glyph-height** -- a cell shorter than the smallest glyph the
      document itself prints. Exactly 1 corpus-wide: 2551q-2018 p1c168,
      0.88pt tall, an input nobody could see or use, surfaced by this
      census rather than by a user.

    The flip budget is EXACTLY 8 inputs corpus-wide and is asserted by the
    corpus census below; a ninth flip is a regression, not a bonus.
    """

    def __init__(self, cells: "Sequence[dict[str, Any]]",
                 fills: "Sequence[dict[str, Any]]",
                 runs: "Sequence[dict[str, Any]] | None",
                 min_glyph_height_pt: float | None):
        self._reasons: dict[str, str] = {}
        rows: dict[tuple[float, float], list[dict[str, Any]]] = {}
        for cell in cells:
            key = (round(float(cell["y0"]), 1), round(float(cell["y1"]), 1))
            rows.setdefault(key, []).append(cell)
        constants = [
            run for run in (runs or ())
            if ATC_CONSTANT_RE.fullmatch(str(run.get("text", "")).strip())
        ]
        for row in rows.values():
            row.sort(key=lambda item: float(item["x0"]))
            for index, cell in enumerate(row):
                if isinstance(cell.get("comb"), dict):
                    continue
                x0, y0 = float(cell["x0"]), float(cell["y0"])
                x1, y1 = float(cell["x1"]), float(cell["y1"])
                cell_id = str(cell["id"])
                if (min_glyph_height_pt is not None
                        and y1 - y0 < min_glyph_height_pt):
                    self._reasons[cell_id] = "sub-glyph-height"
                    continue
                if any(min(x1, float(r["x1"])) - max(x0, float(r["x0"])) > 1.0
                       and min(y1, float(r["y1"])) - max(y0, float(r["y0"]))
                       > 0.75
                       for r in constants):
                    self._reasons[cell_id] = "printed-constant"
                    continue
                dedicated = any(
                    abs(float(f["x0"]) - x0) <= 1.0
                    and abs(float(f["x1"]) - x1) <= 1.0
                    and abs(float(f["y0"]) - y0) <= 1.0
                    and abs(float(f["y1"]) - y1) <= 1.0
                    and not (
                        (f.get("gray") is not None and float(f["gray"]) >= 0.99)
                        or (f.get("rgb")
                            and all(float(v) >= 0.99 for v in f["rgb"])))
                    for f in fills)
                if not dedicated:
                    continue
                left = row[index - 1] if index > 0 else None
                right = row[index + 1] if index + 1 < len(row) else None
                if (left is not None and isinstance(left.get("comb"), dict)
                        and right is not None
                        and isinstance(right.get("comb"), dict)):
                    self._reasons[cell_id] = "comb-separator-fill"

    def reason(self, cell_id: str) -> str | None:
        return self._reasons.get(str(cell_id))


class RowNumberWriting:
    """Which `label` cells a bare row number earns an input beside (F151).

    Mirrors `RuledBlankWriting`'s, `CheckboxSquareWriting`'s and
    `KnockoutSpecifyWriting`'s own shape: a label cell whose printed content
    is a row number sharing its row with a fillable field, and whose own
    paper carries the band `row_number_band` measures, earns a sub-region,
    not a reclassification of the whole cell. Ownership is per cell -- a
    cell holds at most one numeral run in this corpus, so there is no
    multi-claimant case to refuse, the same as `KnockoutSpecifyWriting`'s own
    "specify" caption and unlike a ruled blank's several underscores or a
    checkbox pair's two squares.

    "Sharing a row with a fillable field" is asked of the LATTICE's own
    `kind == "field"`, not of `field_verdict`'s eventual answer for that
    sibling -- the same layer every sibling promotion in this family reads
    its own evidence from, and the reason a narrow item-number column stays
    excluded even where its immediate neighbour is itself later refused (a
    shaded or pre-printed field): the numeral is still sitting at the head of
    a row the sheet built to be filled in, which is the fact this class
    tests for.

    Builds its OWN `PrePrintedInk` off the page's full `runs` -- a second
    index alongside `FieldPlan`'s own, not a saving of one -- because
    `row_number_band` needs the corpus-wide "does ANY glyph reach into this
    band" question `intrusions` answers, not the per-cell question
    `assign_points` already resolved when it gave this cell exactly its own
    `text_run_ids`.
    """

    __slots__ = ("_claims",)

    def __init__(self, cells: Sequence[dict[str, Any]], page_index: int,
                 runs: Sequence[dict[str, Any]],
                 metrics: dict[str, float] | None) -> None:
        claims: dict[str, tuple[float, float, float, float]] = {}
        if metrics is None:
            self._claims = claims
            return
        runs_by_id = {run_id(page_index, i): run for i, run in enumerate(runs)}
        ink = PrePrintedInk(runs)
        cells_by_row: dict[Any, list[dict[str, Any]]] = {}
        for cell in cells:
            cells_by_row.setdefault(cell.get("row"), []).append(cell)
        for cell in cells:
            if cell["kind"] != "label" or cell.get("comb"):
                continue
            siblings = cells_by_row.get(cell.get("row"), ())
            if not any(sibling["kind"] == "field" for sibling in siblings
                       if sibling["id"] != cell["id"]):
                continue
            band = row_number_band(cell, runs_by_id, metrics, ink)
            if band is not None:
                claims[cell["id"]] = band
        self._claims = claims

    def for_cell(self, cell_id: str) -> tuple[float, float, float, float] | None:
        return self._claims.get(cell_id)


def row_number_field_box(cell: dict[str, Any],
                         band: tuple[float, float, float, float],
                         face: FieldFace, ink: "PrePrintedInk | None",
                         ) -> FieldBox | None:
    """The writing surface a bare row number earns beside it (F151).

    Geometry, not a re-derivation of `field_box`'s, and identical in shape to
    `knockout_specify_field_box`: the box is the band's own rectangle --
    `row_number_band` has already measured the paper to the right of the
    numeral's own ink, clipped to nothing further, because that IS the
    sheet's own blank paper and the whole reason it was found.
    `writing_box_clear_of_printed_ink` still runs, the same defensive belt
    every sibling in this family keeps: nothing here has looked at whether
    some OTHER run on the page hangs into the band, and that function already
    answers exactly that question.
    """
    if face.line_span_em <= 0.0:
        return None
    bx0, by0, bx1, by1 = band
    tx0, ty0, tx1, ty1 = writing_box_clear_of_printed_ink((bx0, by0, bx1, by1), ink)
    if tx1 - tx0 <= 0.0 or ty1 - ty0 <= 0.0:
        return None
    height = ty1 - ty0
    size = min(face.size_pt, _floor2(height / face.line_span_em))
    if size <= 0.0:
        return None
    spacing = None
    if face.size_pt > 0 and abs(face.letter_spacing_pt) > 0:
        scaled = round(face.letter_spacing_pt * size / face.size_pt, 4)
        if abs(scaled) >= LETTER_SPACING_EPSILON_PT:
            spacing = scaled
    inset = (ty0 - float(cell["y0"]), float(cell["x1"]) - tx1,
             float(cell["y1"]) - ty1, tx0 - float(cell["x0"]))
    return FieldBox("text", inset, size, round(height, 4), spacing, None, None)


# A cell whose own width is mostly pre-printed glyph ink is not a blank the
# taxpayer can write in, whatever the box detector made of it: it is a table
# cell whose text lattice.py assigned to a neighbour because the run crosses
# cell boundaries. A majority is the test, and the corpus separates on it rather
# than being tuned to it. Measured over all 5423 non-comb field cells
# (build/layout + build/ir, 53 bundles, 2026-08-09), 31 are above it:
#
#     0.537   2   1701A item 19's caption strip, p1c227 and p2c208 (F207)
#     0.614   1   1601EQ's "1/2 of 1% WI156" ATC row
#     0.758-  28  every statutory bracket of the graduated-rate table on
#     0.842       1701, 1701A, 1701Q and 1701MS page 2
#
# and the next cell down is at 0.126 (1604CF p1c20). Nothing lands in between,
# so the threshold sits in an empty gap four times its own width; 5355 of the
# remaining 5392 are at 0.0 exactly. The two F207 cells are the closest any
# cell in the corpus comes to it and they clear it by 0.037 -- see
# `PrePrintedInk._spans_over` for what they are and why the measure, not the
# threshold, is what had to change to see them.
PREPRINTED_COVERAGE = 0.5

# One indexed run: its line box, its glyph x-extents, the band its outlines ink
# (None where the source does not state them), the baseline they are hung off
# (None where the run states none), and one measured outline box per glyph in
# `spans` (None per glyph where that character's own outline is unstated). The
# band and baseline are what `_spans_over` asks "printed in" of; the line box
# is `intrusions`' own fallback, and the per-glyph boxes are what it asks first
# -- see `_glyph_ink_box`.
_InkEntry = tuple[float, float, list[tuple[str, float, float]],
                  tuple[float, float] | None, float | None,
                  list[tuple[float, float, float, float] | None]]


class PrePrintedInk:
    """Where one page's pre-printed glyphs put ink, asked per cell.

    Ink, not run boxes. A run's bbox spans its whole line -- 'Amended Return?
    Yes        No' is one run 132pt wide -- so measuring coverage from bboxes
    reports every checkbox on the corpus as full and would delete the typing
    surface from all of them. What occupies a box is the *non-space* glyphs,
    and the IR states each one's origin and width, so this is measured rather
    than inferred.

    A run counts against a cell when the ink it lays is mostly on that cell's
    paper, or when the line that ink is seated on is in the cell. See
    `_spans_over` for why the question needs both halves and why neither is
    asked of the run's box.
    """

    __slots__ = ("_buckets",)

    BUCKET_PT = 16.0

    def __init__(self, runs: Sequence[dict[str, Any]]) -> None:
        self._buckets: dict[int, list[_InkEntry]] = {}
        for run in runs:
            spans = _glyph_spans(run)
            if not spans:
                continue
            y0, y1 = float(run["y0"]), float(run["y1"])
            baseline = run.get("baseline_y")
            glyph_boxes = [_glyph_ink_box(run, char, origin)
                           for char, origin, _x1 in spans]
            entry = (y0, y1, spans, _ink_band(run),
                     None if baseline is None else float(baseline), glyph_boxes)
            for bucket in range(int(y0 // self.BUCKET_PT), int(y1 // self.BUCKET_PT) + 1):
                self._buckets.setdefault(bucket, []).append(entry)

    def _entries_over(self, y0: float, y1: float) -> Iterator["_InkEntry"]:
        """Every run whose own line box reaches into the band `y0..y1`.

        No "printed in" test at all, which is what separates this from
        `_spans_over`: the two occupancy questions ask which text BELONGS to a
        box, and a caption belongs to the row above even when its descenders
        hang into the row below. `intrusions` asks the opposite question --
        where does the sheet put ink, whoever it belongs to -- and a rule that
        first discarded the caption above could never see the ink it hangs
        into the blank underneath it, which is the whole of the defect.

        Sorted, so a caller that trims a box against these gets the same box
        whatever order the buckets were filled in.
        """
        seen: set[int] = set()
        out: list[_InkEntry] = []
        for bucket in range(int(y0 // self.BUCKET_PT), int(y1 // self.BUCKET_PT) + 1):
            for entry in self._buckets.get(bucket, ()):
                if id(entry) in seen:
                    continue
                seen.add(id(entry))
                run_y0, run_y1 = entry[0], entry[1]
                if run_y1 <= run_y0 or run_y1 <= y0 or run_y0 >= y1:
                    continue
                out.append(entry)
        # Every entry carries at least one glyph -- `__init__` drops a run with
        # none -- so the first glyph is always a usable tie-break.
        out.sort(key=lambda entry: (entry[0], entry[1], entry[2][0]))
        return iter(out)

    def _spans_over(self, y0: float, y1: float
                    ) -> Iterator[list[tuple[str, float, float]]]:
        """The glyphs of every run that is printed IN the band `y0..y1`.

        "Printed in" is the whole of this method, and it lives here so that the
        two questions asked of this index, occupancy of a cell and what a comb
        slot carries, cannot drift apart on which runs they consider.

        **Occupancy is not ownership, and that is why there are two clauses.**
        `lattice.assign_points` gives a run to exactly one cell, because a run
        has one owner. This asks something else -- is this cell's paper already
        marked -- and one line of type can mark two rows: it fills the row its
        ink mostly lies in and it is SEATED on the row its baseline is in. A
        single test can only ever name one of those two rows, and both of them
        are cells a taxpayer must not be handed a typing surface on.

          * **Mostly on this paper.** At least half the run's INK band inside,
            which is what distinguishes "this text is printed in this box" from
            "the line above dips 0.4pt into it": 1604C, 2316 and 2200S all have
            field cells that a neighbouring line grazes. The band is the run's
            outlines (`_ink_band`), not its box, because a run's box is its
            face's LINE box -- ascent line to descent line -- and charges every
            glyph with a descender depth its characters may not have.
          * **Seated on this paper.** The run's baseline inside the band. Type
            is placed by its origin and every outline in `glyph_ink_em` is a
            box hung off that origin, so the baseline is where the ink sits,
            not a proxy for it; a line whose seat is on this cell is printed on
            this cell however far its ascenders reach into the row above.

        The second clause is the whole of F207 and neither half of it can
        answer alone. 1701A item 19 sets the second line of both its captions
        across the strip below the boxes -- "of deduction" and "[available if
        gross sales/receipts ... (P3M)]" -- so that each line's ascenders cross
        the lattice boundary. The first is 45% inside by its measured ink (47%
        by its box); the second is set in Arial Narrow Italic, a face whose
        outlines MuPDF's name cleaner cannot resolve, so it has no measured
        band at all and falls back to its line box, 39% inside. Both are wholly
        seated in the strip, and the unmeasured one is 267.16 of the 313.49pt
        of ink that refuses a 584.16pt cell -- so the fallback is not a corner
        case here, it is the majority of the evidence. With only the first
        clause the strip reads as blank paper and takes a 584pt input laid over
        two printed lines.

        Measured over the whole corpus (build/layout + build/ir, 53 bundles,
        2026-08-09), asking both clauses instead of half the run's box height
        moves 11 cells across `PREPRINTED_COVERAGE` and no cell the other way.
        Nine are `blank`, `label` or `shaded` cells, whose kind already refuses
        an input before this evidence is read; the two that change what is
        emitted are 1701A p1c227 and p2c208.
        """
        for run_y0, run_y1, spans, band, baseline, _glyph_boxes in self._entries_over(y0, y1):
            top, bottom = band if band is not None else (run_y0, run_y1)
            if bottom > top and min(y1, bottom) - max(y0, top) >= 0.5 * (bottom - top):
                yield spans
            elif baseline is not None and y0 <= baseline <= y1:
                yield spans

    def intrusions(self, x0: float, y0: float, x1: float, y1: float
                   ) -> list[tuple[float, float, float, float]]:
        """Every printed glyph box that reaches into the rectangle x0..x1/y0..y1.

        One box per non-space glyph, and per glyph rather than per run is the
        whole of F227: where the source states that character's own outline
        (`_glyph_ink_box`), THAT is the box on all four edges; only where it
        does not is a glyph still charged its run's line box vertically and its
        own advance span horizontally, exactly as every glyph in every run was
        before this.

        The run's line box was never a LAYOUT margin deliberately held wider
        than the ink, as it read before this: it is the face's ascent and
        descent lines, restated identically for every character the run sets
        regardless of what that character's own outline reaches, so a run
        mixing a descender with plain letters charges the plain letters a depth
        they do not ink (1604CF's "Zip Code" -- see `_glyph_ink_box`) and, less
        obviously, can charge the descender LESS than it truly inks when the
        face's own declared descender metric is shallower than that specific
        glyph's outline (2316's " Employer's Name " 'p'/'y' at -0.218em against
        a declared -0.21em). Both directions are simply gone once the box asked
        of is the glyph's own.
        """
        out: list[tuple[float, float, float, float]] = []
        for run_y0, run_y1, spans, _band, _baseline, glyph_boxes in self._entries_over(y0, y1):
            for (_char, span_x0, span_x1), precise in zip(spans, glyph_boxes):
                if span_x1 <= x0 or span_x0 >= x1:
                    continue
                out.append(precise if precise is not None
                           else (span_x0, run_y0, span_x1, run_y1))
        return out

    def coverage(self, cell: dict[str, Any]) -> float:
        """Fraction of the cell's width covered by pre-printed glyph ink."""
        width = float(cell["x1"]) - float(cell["x0"])
        if width <= 0:
            return 0.0
        x0, x1 = float(cell["x0"]), float(cell["x1"])
        inside: list[tuple[float, float]] = []
        for spans in self._spans_over(float(cell["y0"]), float(cell["y1"])):
            for _char, span_x0, span_x1 in spans:
                low, high = max(x0, span_x0), min(x1, span_x1)
                if high > low:
                    inside.append((low, high))
        if not inside:
            return 0.0
        inside.sort()
        covered = 0.0
        low, high = inside[0]
        for span_x0, span_x1 in inside[1:]:
            if span_x0 > high:
                covered += high - low
                low, high = span_x0, span_x1
            else:
                high = max(high, span_x1)
        covered += high - low
        return covered / width

    def slot_constant(self, x0: float, y0: float, x1: float, y1: float) -> str | None:
        """The pre-printed glyph this comb slot is ALREADY OCCUPIED BY, or None.

        `coverage` cannot answer this. A comb slot is one character wide, so a
        single printed glyph covers most of it whatever that glyph is; how much
        ink is in the slot cannot tell a compartment the source filled in from
        a blank compartment beside one it filled in.

        The name is kept because three resolved findings quote it verbatim, but
        the question it asks is occupancy, not authorship -- see the third
        paragraph. Two conditions, each a population the corpus separates on
        (build/layout + build/ir, 53 bundles, 4,561 comb cells, 39,444
        compartments, 2026-08-08 -- 407 compartments carry ink at all, 373 of
        those carry exactly one glyph, and 366 of THOSE are contained and
        admitted here):

          * **Exactly one glyph.** A value is typeset AT the comb's pitch, one
            character per compartment, because it is printed to look like a
            filled-in box: `I I 0 1 1`, `X C 0 1 0`, `2 0`, `0 0 0 0 0`, and
            the money bullet in its own compartment. A caption the lattice
            swallowed into the same cell is typeset at label scale and lands 9
            to 654 glyphs in ONE slot -- `7A ZIP Code`, `12 Contact Number`,
            2200A's whole signature line. All 34 such slots are that, every one
            of them 9 glyphs or more; all 373 single-glyph slots are not.
            Nothing in the corpus is in between, and the two populations answer
            to different defects: a swallowed caption is a segmentation fault
            (G05/G12) and deleting its slot's input would hide it, not fix it.
          * **Wholly inside the slot.** A neighbour's caption can clip one
            glyph into the first compartment: 2551M's and 2553's `28C`/`29B`
            item numbers overhang by 4.53pt, and 0605 p1c3's date hint clips
            its closing bracket in by 1.98pt. The margin (glyph to slot wall)
            is negative for exactly those 7 and >= +0.24pt for all 366 admitted
            compartments; the corpus separates by 2.22pt with nothing in
            between, so the test is containment itself and carries no
            tolerance.

        There was a third condition -- the glyph must be ALPHANUMERIC -- on the
        reasoning that `.` `-` `%` and the money bullet `●` are drawn INSIDE a
        field to shape what is typed into it rather than to state a value, and
        that refusing their compartments would re-create C4 (a money comb with
        no way to type an amount at all). Measured at compartment resolution
        over the whole corpus, that reasoning is wrong on both halves:

          * **Character class is not the question.** The compartment is one
            character wide and the source has already put a character in it.
            Whatever that character means, the compartment is SPENT: an input
            there is a typing surface no taxpayer can use, laid directly over
            printed ink. Those 92 money bullets are `inputs_over_printed_text`'s
            largest offender population -- 89 of its 147 -- and no vertical
            inset can clear them, because the bullet sits inside the divider
            band. The digits of an amount go into the compartments either side
            of the point, which is precisely why refusing the point's own
            compartment leaves the comb usable.
          * **The C4 evidence does not say what it was read as saying.** C4 is
            a comb with ZERO inputs; this verdict is per compartment, and what
            protects C4 is that only an OCCUPIED compartment is refused.
            Dropping the condition refuses 94 compartments that were live: 92
            money bullets, each ONE compartment of a 14-, 29- or
            33-compartment comb, every one of them the third from the right
            with the two centavos compartments to its right (2000-DST 16,
            2200A 20, 2200C 20, 2200P 20, 2200S 16); and the 2 that complete
            the printed rate `0 %` on 1800 p1c68 and 2550-DS p1c79, 2-
            compartment combs the source fills entirely and which are not money
            boxes. Re-measured against the C4 list itself: 2000-DST's page-1
            money grid keeps 13 of 14 compartments on every money row, 2200A
            and 2200P Part III likewise, and 1801 item 24, 2316 items 23-24 and
            1702EX item 18 carry no bullet compartment at all and do not move.
            Not one digit compartment anywhere in the corpus loses its input,
            and no comb that had a typeable compartment is left without one
            except the two printed rates.
          * The other 7 non-alphanumeric compartments were **already refused**
            before this change, by `comb_slot_verdicts`' shading branch: the
            grey group separators printing `-` (1800 p1c15, 1801 p1c14, p1c15,
            p1c31 twice, p1c32) or `.` (1801 p1c57). All that moves for them is
            which evidence the report names -- their emitted bytes are
            identical, because a slot div deliberately carries no
            `data-preprinted` attribute. What is left to the shading branch is
            then exactly the 9 swallowed-caption compartments, which is the
            population that branch was reasoned about.

        Where it stops, stated rather than papered over. A caption printed
        beside the comb whose LAST glyph lands wholly inside the first
        compartment, alone, would be read as that compartment's own ink and
        cost the taxpayer a box. Neither condition above rejects that shape and
        the alphanumeric one never did either: it caught only the half of it
        that ends in punctuation, and a caption ending in a letter passed it
        unchanged. The corpus has neither half -- all 366 admitted compartments
        take their glyph from a run lying wholly within its own comb's
        compartment span -- and the two overhang shapes that DO occur are what
        the two conditions above are for. A format hint typeset at comb pitch
        and centred in the writing band -- a literal `M M / Y Y` inside the
        boxes -- would likewise read as occupancy; the corpus has no such hint,
        its only date hints (`( MM / YYYY )` on 0605 p1c3, `( MM / DD / YYYY )`)
        being printed OUTSIDE the comb and merely clipping a bracket 1.98pt
        into the first slot, which containment rejects and which is now the
        tightest rejection in the corpus. Adding a size, colour or run-extent
        test against a population that does not exist would be machinery
        earning nothing.
        """
        found: tuple[str, float, float] | None = None
        for spans in self._spans_over(y0, y1):
            for char, span_x0, span_x1 in spans:
                if min(x1, span_x1) <= max(x0, span_x0):
                    continue
                if found is not None:
                    return None
                found = (char, span_x0, span_x1)
        if found is None:
            return None
        char, span_x0, span_x1 = found
        if span_x0 < x0 or span_x1 > x1:
            return None
        return char

    def slot_caption(self, x0: float, y0: float, x1: float, y1: float
                     ) -> str | None:
        """A whole printed run this compartment CONTAINS, or None.

        `slot_constant` answers the compartment that is one character wide,
        which is every compartment a comb is drawn to receive one digit in.
        This answers the one that is not. On 2200-A, -C and -P the source
        deletes the wall between the "Particulars" column and the first five
        boxes of the money grid on the "27 Tax Debit Memo" row and greys that
        space out, so the lattice reads the row as ONE 29-compartment comb
        whose first compartment is 173.66pt wide -- twelve times each of the
        other twenty-eight -- and carries the item's own printed caption. An
        input there is a typing surface laid over "27 Tax Debit Memo".

        The test is CONTAINMENT of the run, which is the principle
        `slot_constant`'s second condition already uses one glyph at a time and
        for the same reason: a caption that belongs to the box next door
        reaches across the wall, and a value the source set INSIDE this box
        does not. It carries no tolerance and no threshold, and the corpus
        separates on it rather than being tuned to it -- of the 239
        compartments that contain a whole run, 236 are already refused an input
        and the 3 that are not are exactly those three item-27 captions. Ink
        COVERAGE would not have settled it: those three measure 0.4295 of their
        compartment's width against the 0.5 `field_verdict` needs for a cell,
        and the next live compartment down is at 0.0533. A second, lower
        threshold for compartments would be a constant chosen to fit three
        cells.

        What this deliberately does NOT catch is the glyph a neighbour clips
        across the wall: 2551M's and 2553's `28C`/`29B`/`30C` item numbers and
        0605's `( MM / YYYY )` date hint reach 0.24 to 1.98pt into the first
        compartment of the comb beside them, and every one of those
        compartments is a digit box a taxpayer must be able to use. None of
        those runs is contained, and this returns None for all seven.
        """
        for spans in self._spans_over(y0, y1):
            if not spans:
                continue
            if min(span[1] for span in spans) < x0:
                continue
            if max(span[2] for span in spans) > x1:
                continue
            return "".join(span[0] for span in spans)
        return None


def _gap_has_ink(ink: "PrePrintedInk", x0: float, y0: float,
                 x1: float, y1: float) -> bool:
    """Whether the strip x0..x1/y0..y1 carries any pre-printed glyph ink.

    `PrePrintedInk.intrusions` is a coarse pre-filter -- it selects by a
    run's whole LINE box, not by where the returned glyph's own precise box
    actually sits, so every caller re-checks the returned boxes against its
    own rectangle (`writing_box_clear_of_printed_ink` does, by centre, one
    direction at a time). This is the plain, symmetric version: true only
    when a glyph's own box genuinely overlaps this rectangle on both axes,
    used by
    `SignatureRuleWriting` to ask whether a vertical gap between a
    rule-owning cell and a caption cell below it is genuinely ink-free paper
    (F226) rather than the run whose LINE merely reaches into the query
    band from outside it.
    """
    for gx0, gy0, gx1, gy1 in ink.intrusions(x0, y0, x1, y1):
        if gy1 > y0 and gy0 < y1 and gx1 > x0 and gx0 < x1:
            return True
    return False


def _glyph_ink_box(run: dict[str, Any], char: str, origin_x: float
                   ) -> tuple[float, float, float, float] | None:
    """One glyph's own outline box in absolute page coordinates, or None.

    F227's mechanism: `intrusions` used to hand `writing_box_clear_of_printed_ink`
    one box per glyph shaped `(span_x0, run_y0, span_x1, run_y1)` -- the whole
    RUN's line box, restated for every character in it. That is a run-level
    question wearing a per-glyph shape, and it fails in both directions on the
    same corpus: 2316's " Employer's Name " sets 'p' and 'y' whose measured ink
    (glyph_ink_em -0.218em) reaches 0.055pt deeper than the face's own declared
    descender (-0.21em) states the run's line box to, so the run box under-
    trims p1c62 and p1c83 by exactly that; 1604CF's "Zip Code" sets 'e' -- no
    descender at all -- whose line box is inflated to the RUN's deepest
    character ('p', in the same word), over-reporting an intrusion 1.45pt
    deeper than 'e' itself ever inks. Restated per glyph rather than per run,
    both directions disappear: 'p'/'y' report their own true depth and 'e'
    reports its own true shallowness.

    This is `audit.published_glyph_ink` plus `audit.glyph_boxes`'s box
    assembly, restated here because emit.py reads the IR as JSON and carries no
    import of the module that checks it -- the same relationship
    `RULE_ORIGIN_TEXT_UNDERSCORE` above already has to `extract.py`. Where the
    source states the glyph's outline, that outline IS the box on every edge;
    None is the fail-closed answer for `intrusions` to fall back to the run's
    own line box on, exactly as it always has for the 21.6% of this corpus's
    glyphs (76,991 of 356,092) that carry no measured outline at all.
    """
    table = run.get("glyph_ink_em")
    if not isinstance(table, dict):
        return None
    box = table.get(char)
    if not isinstance(box, (list, tuple)) or len(box) != 4:
        return None
    if any(isinstance(value, bool) or not isinstance(value, (int, float))
           or not math.isfinite(value) for value in box):
        return None
    if box[2] <= box[0] or box[3] <= box[1]:
        return None
    if run.get("rotated") or not run.get("size_pt") or run.get("baseline_y") is None:
        return None
    size = float(run["size_pt"])
    baseline = float(run["baseline_y"])
    gx0, gy0, gx1, gy1 = (float(value) for value in box)
    # Font space counts y up from the baseline; the page counts it down.
    return (origin_x + size * gx0, baseline - size * gy1,
            origin_x + size * gx1, baseline - size * gy0)


def _ink_band(run: dict[str, Any]) -> tuple[float, float] | None:
    """The band this run's glyph outlines actually ink, or None when unknown.

    `extract.run_glyph_ink` publishes `glyph_ink_em`, the outline box of every
    character a run sets, in em units hung off the run's own baseline and
    origin; `audit.glyph_boxes` reads the same table for the same reason. This
    is that evidence collapsed to one vertical extent, because the question
    asked of it here is a per-run one.

    None is the fail-closed answer and every caller must fall back to the run's
    recorded line box on it. It is returned whenever the table cannot describe
    this run's ink: no table at all (a face MuPDF's own name cleaner does not
    resolve -- Arial Narrow, Ebrima, Nirmala UI), a rotated run whose baseline
    is not horizontal, a run with no baseline or size to hang the box off, a
    malformed box, or a table that is missing even one of the characters the
    run sets. That last one is deliberate: a band derived from part of a run is
    not that run's band, and 457 of this corpus's 19,287 runs are that shape
    (an en-dash in a `Part I – ...` heading, a fragment whose sibling carries
    the letters). 15,995 runs publish a complete table and 2,835 publish none.
    """
    table = run.get("glyph_ink_em")
    if not isinstance(table, dict) or not table:
        return None
    if run.get("rotated") or not run.get("size_pt"):
        return None
    baseline = run.get("baseline_y")
    if baseline is None:
        return None
    baseline, size = float(baseline), float(run["size_pt"])
    top: float | None = None
    bottom: float | None = None
    for char in run.get("text") or "":
        if not char.strip():
            continue
        box = table.get(char)
        if not isinstance(box, (list, tuple)) or len(box) != 4:
            return None
        if any(isinstance(value, bool) or not isinstance(value, (int, float))
               or not math.isfinite(value) for value in box):
            return None
        if box[3] <= box[1]:
            return None
        # Font space counts y up from the baseline; the page counts it down.
        glyph_top = baseline - size * float(box[3])
        glyph_bottom = baseline - size * float(box[1])
        top = glyph_top if top is None else min(top, glyph_top)
        bottom = glyph_bottom if bottom is None else max(bottom, glyph_bottom)
    if top is None or bottom is None or bottom <= top:
        return None
    return top, bottom


def _glyph_spans(run: dict[str, Any]) -> list[tuple[str, float, float]]:
    """Each non-space glyph and its x extent, from the IR's per-glyph metrics."""
    origin = run.get("origin_x")
    if origin is None:
        return []
    offsets = run.get("char_origin_offsets_pt") or []
    widths = run.get("char_widths_pt") or []
    spans: list[tuple[str, float, float]] = []
    for index, char in enumerate(run["text"]):
        if char.isspace() or index >= len(offsets) or index >= len(widths):
            continue
        start = float(origin) + float(offsets[index])
        spans.append((char, start, start + float(widths[index])))
    return spans


# A cell the source shaded is a cell the source said not to write in. BIR's
# "no rate applies here" grey is a literal operand in the content stream --
# extract.classify_tone stamps every fill structural/decorative/knockout from
# it -- so this is read, never inferred from a raster.
#
# The threshold separates the corpus rather than being tuned to it. Measured
# over every non-comb `field` cell whose topmost covering fill is decorative
# (build/layout + build/ir, 53 bundles, 2026-08-06), the grey values are:
#
#     0.5020   3     2551M's shaded Part II rows
#     0.5882   1     1604CF page 3
#     0.6510  27     1601-FQ / 1602Q schedule shading
#     0.7489 149     the largest population, 2200-series schedules
#     0.7529  41     0605 and the TIN-group gaps
#     0.8509 126     the band grey CLAUDE.md names
#     ----------  the gap the threshold sits in  ----------
#     0.8902   3     1604CF p1c8/c10/c12 -- REAL fields, beside item "7" and
#                    "(Last Name, First Name, Middle Name)"
#     0.9489   2     2200AN p2c247/c255 -- REAL, one labelled "(To Schedule 1C)"
#
# Nothing lands between 0.8509 and 0.8902. `role == "decorative"` alone spans
# the gap and would delete those 5 real fields; 0.87 catches 347 and spares
# them. A knockout (>= 0.98) covering the cell is white paper painted back over
# a band and leaves the cell fillable -- 2381 cells sit that way -- and a
# chromatic fill (no grey at all: 2553 p1c16/c18/c20) is not this evidence and
# is left alone.
DECORATIVE_GRAY_MAX = 0.87
# Area, not width. A band that runs the length of a row covers a narrow cell
# entirely, and a cell straddling the edge of a band is not one the source
# shaded -- it is one the lattice cut across a boundary, which is a different
# defect and must not be silently answered here.
DECORATIVE_COVERAGE = 0.7


class DecorativeShading:
    """What tone the paper under a cell is, asked per cell.

    Topmost, because paint order decides what the taxpayer sees: BIR draws a
    grey band across a whole row and then knocks white boxes back out of it for
    the blanks, so the fill with the highest `paint_seq` is the one whose colour
    the paper actually is. Reading the band alone would report every knocked-out
    blank on the row as shaded and delete the row's real fields.
    """

    __slots__ = ("_buckets",)

    BUCKET_PT = 32.0

    def __init__(self, fills: Sequence[dict[str, Any]]) -> None:
        self._buckets: dict[int, list[tuple[int, dict[str, Any]]]] = {}
        for index, fill in enumerate(fills):
            entry = (index, fill)
            y0, y1 = float(fill["y0"]), float(fill["y1"])
            for bucket in range(int(y0 // self.BUCKET_PT), int(y1 // self.BUCKET_PT) + 1):
                self._buckets.setdefault(bucket, []).append(entry)

    def covering(self, cell: dict[str, Any]) -> dict[str, Any] | None:
        """The topmost fill covering most of this cell, or None."""
        x0, y0 = float(cell["x0"]), float(cell["y0"])
        x1, y1 = float(cell["x1"]), float(cell["y1"])
        area = (x1 - x0) * (y1 - y0)
        if area <= 0.0:
            return None
        best: dict[str, Any] | None = None
        # The IR's own index breaks a paint_seq tie, so the answer is a pure
        # function of the file and cannot depend on bucket iteration order.
        best_key: tuple[int, int] = (-1, -1)
        seen: set[int] = set()
        for bucket in range(int(y0 // self.BUCKET_PT), int(y1 // self.BUCKET_PT) + 1):
            for index, fill in self._buckets.get(bucket, ()):
                if index in seen:
                    continue
                seen.add(index)
                width = min(x1, float(fill["x1"])) - max(x0, float(fill["x0"]))
                height = min(y1, float(fill["y1"])) - max(y0, float(fill["y0"]))
                if width <= 0.0 or height <= 0.0:
                    continue
                if width * height < DECORATIVE_COVERAGE * area:
                    continue
                key = (int(fill["paint_seq"]), index)
                if key > best_key:
                    best, best_key = fill, key
        return best

    def blocks(self, cell: dict[str, Any]) -> bool:
        """Whether the source shaded this cell to say it is not a blank."""
        fill = self.covering(cell)
        if fill is None or fill.get("role") != "decorative":
            return False
        gray = fill.get("gray")
        return gray is not None and float(gray) <= DECORATIVE_GRAY_MAX


# The captions with which BIR reserves a box for its own officers, normalised
# to single-space lowercase. These are the sheet's own words, not ours: the
# corpus states a reservation in exactly four voices, and the DLN/PSIC/PSOC
# line at the top of every legacy sheet is already inert for a different
# reason -- it has no ruled box at all, so the lattice never makes a cell
# there. What that protection cannot cover is a reserved blank that IS boxed,
# which is F147's defect: 0605's "BCS No./Item No. (To be filled up by the
# BIR)" satisfies the box detector exactly as a taxpayer blank does, because
# nothing at the box-model stage reads the caption the source set against it.
#
# Substrings, because 0605 embeds the phrase at the end of a longer caption
# run; and "to be filled up the bir" is not a typo of ours -- it is the
# official sheet's own missing "by", printed at the top of 0605 page 1, and a
# match list that silently corrected it would not match the paper.
BUREAU_RESERVED_SUBSTRINGS = (
    "to be filled up by the bir",
    "to be filled up the bir",
    "for bir use only",
)
# Prefixes, not substrings: guide prose discusses these boxes mid-sentence
# ("The machine validation shall reflect the date of payment...") and a
# substring match would make a paragraph a reservation. A caption STARTS with
# its subject; prose does not.
BUREAU_RESERVED_PREFIXES = (
    "machine validation",
    "stamp of receiving",
    "stamp of authorized",
)


def _bureau_caption(text: str) -> bool:
    """Whether this run is a caption reserving a box for the Bureau."""
    normalised = " ".join(text.split()).lower()
    return (any(phrase in normalised for phrase in BUREAU_RESERVED_SUBSTRINGS)
            or any(normalised.startswith(prefix)
                   for prefix in BUREAU_RESERVED_PREFIXES))


class BureauReservation:
    """Which blanks the sheet's own captions reserve for the Bureau, per page.

    The evidence is the SOURCE's, twice over: the caption text comes from the
    IR's runs, and the box it governs is bound structurally rather than by
    proximity -- a caption governs the ruled compartment it is printed in, and
    nothing outside it. Both halves are needed. A keyword rule alone has
    already burned this corpus once ("Add: Penalties"), and pure geometry
    cannot tell 0605's Bureau-reserved BCS blank from the taxpayer's Return
    Period boxes beside it: both are white knockouts on the same grey band,
    under captions on the same caption row, drawn by the same operators. The
    caption is the only thing on the paper that distinguishes them, so the
    caption is the evidence -- anchored to the walls the source drew.

    Two bindings, each measured against the corpus (build/ir + build/layout +
    the shipped documents, 53 bundles, 2026-08-07; 96 caption runs match, and
    the two bindings together claim 7 inputs, every one verified against the
    official raster by a human in F147's fix):

      * **The caption is printed inside the blank** -- at least half of the
        caption's own height lies in the rect's y-band and its x-centre is
        inside the rect. This is the bottom-of-sheet shape: 2200-A/C/P draw
        one wide band split by a dashed rule into "Machine Validation/..."
        and "Stamp of Receiving Office/AAB...", each box carrying its caption
        at its top edge. (The lattice reads that band as a 2-slot comb, which
        is why the slot resolution exists.)
      * **The caption is printed directly above the blank, in the same ruled
        compartment.** Adjacency is bounded by the caption's own height -- a
        caption is set against the blank it governs, one line of separation
        at most, and 0605's measures 4.4pt against a 9.0pt line. The
        compartment is the nearest pair of v-walls flanking the CAPTION that
        span from the caption down through the rect, and the rect must lie
        between them; a h-wall spanning the compartment between caption and
        rect breaks the binding, because a ruled separator means the caption
        governs a section header's row, not this blank. Both constraints are
        load-bearing: without the walls, the "For BIR Use Only" corner stack
        on 2200S would claim the whole header band below it, and without the
        separator test, 1701MS page 2's "PART VI ... (For BIR Use Only)"
        header would claim the ruled money row beneath -- a real reservation
        on the paper, but one a header-scope rule must claim over different
        evidence, reviewed on its own.

    Walls are the page's rules plus its thin structural fills, merged along
    their line: BIR draws its outer frames as filled rectangles and breaks
    inner rules wherever text sits on them, so an unmerged segment list would
    find no wall where the paper plainly draws one.
    """

    __slots__ = ("_captions", "_h_walls", "_v_walls")

    WALL_FILL_MAX_PT = 3.0
    WALL_LINE_EPSILON_PT = 0.6
    WALL_SPAN_GAP_PT = 1.0

    def __init__(self, runs: Sequence[dict[str, Any]],
                 rules: Sequence[dict[str, Any]],
                 fills: Sequence[dict[str, Any]]) -> None:
        self._captions = [
            (float(run["x0"]), float(run["y0"]),
             float(run["x1"]), float(run["y1"]))
            for run in runs if _bureau_caption(run["text"])]
        self._h_walls: list[tuple[float, float, float]] = []
        self._v_walls: list[tuple[float, float, float]] = []
        if not self._captions:
            return
        segments: dict[str, list[tuple[float, float, float]]] = {"h": [], "v": []}
        for rule in rules:
            if rule["axis"] == "h":
                segments["h"].append(((float(rule["y0"]) + float(rule["y1"])) / 2.0,
                                      float(rule["x0"]), float(rule["x1"])))
            else:
                segments["v"].append(((float(rule["x0"]) + float(rule["x1"])) / 2.0,
                                      float(rule["y0"]), float(rule["y1"])))
        for fill in fills:
            if fill.get("role") != "structural":
                continue
            width = float(fill["x1"]) - float(fill["x0"])
            height = float(fill["y1"]) - float(fill["y0"])
            if height <= self.WALL_FILL_MAX_PT < width:
                segments["h"].append(((float(fill["y0"]) + float(fill["y1"])) / 2.0,
                                      float(fill["x0"]), float(fill["x1"])))
            elif width <= self.WALL_FILL_MAX_PT < height:
                segments["v"].append(((float(fill["x0"]) + float(fill["x1"])) / 2.0,
                                      float(fill["y0"]), float(fill["y1"])))
        for axis, walls in (("h", self._h_walls), ("v", self._v_walls)):
            lines: list[tuple[float, list[list[float]]]] = []
            for mid, lo, hi in sorted(segments[axis]):
                if lines and abs(lines[-1][0] - mid) <= self.WALL_LINE_EPSILON_PT:
                    lines[-1][1].append([lo, hi])
                else:
                    lines.append((mid, [[lo, hi]]))
            for mid, spans in lines:
                spans.sort()
                merged = [spans[0]]
                for lo, hi in spans[1:]:
                    if lo <= merged[-1][1] + self.WALL_SPAN_GAP_PT:
                        merged[-1][1] = max(merged[-1][1], hi)
                    else:
                        merged.append([lo, hi])
                walls.extend((mid, lo, hi) for lo, hi in merged)

    def blocks(self, x0: float, y0: float, x1: float, y1: float) -> bool:
        """Whether a Bureau caption reserves this rect, by either binding."""
        for rx0, ry0, rx1, ry1 in self._captions:
            height = ry1 - ry0
            if height <= 0.0:
                continue
            centre = (rx0 + rx1) / 2.0
            if (min(y1, ry1) - max(y0, ry0) >= 0.5 * height
                    and x0 <= centre <= x1):
                return True
            if not (-0.5 <= y0 - ry1 <= height):
                continue
            lo, hi = ry0 + 1.0, y1 - 1.0
            left = max((w for w in self._v_walls
                        if w[0] <= rx0 + 0.5 and w[1] <= lo and w[2] >= hi),
                       key=lambda w: w[0], default=None)
            right = min((w for w in self._v_walls
                         if w[0] >= rx1 - 0.5 and w[1] <= lo and w[2] >= hi),
                        key=lambda w: w[0], default=None)
            if left is None or right is None:
                continue
            if x0 < left[0] - 1.0 or x1 > right[0] + 1.0:
                continue
            if any(ry1 - 0.5 < w[0] < y0 + 0.5
                   and w[1] <= left[0] + 1.5 and w[2] >= right[0] - 1.5
                   for w in self._h_walls):
                continue
            return True
        return False


def field_verdict(cell: dict[str, Any], ink: PrePrintedInk | None,
                  shading: DecorativeShading | None,
                  reservation: "BureauReservation | None",
                  ruled_blanks: "RuledBlankWriting | None" = None,
                  checkbox_squares: "CheckboxSquareWriting | None" = None,
                  signature_boxes: "SignatureBoxWriting | None" = None,
                  knockout_specify: "KnockoutSpecifyWriting | None" = None,
                  row_numbers: "RowNumberWriting | None" = None,
                  signature_rules: "SignatureRuleWriting | None" = None,
                  decoration: "PrintedDecoration | None" = None,
                  ) -> tuple[bool, str]:
    """Whether a taxpayer can type in this cell, and why.

    Ten rules, in this order, and the order is the point:

      * **A `label` cell whose paper carries its own underscore-drawn writing
        line is a field there, whatever else refuses it -- unless the sheet's
        own Bureau caption also claims it.** F148/F149: a ruled blank is
        written ON its line, so the writing space is the CAPTION cell's own
        paper, not the sliver `lattice.classify_cell` cuts beneath the rule.
        `RuledBlankWriting` has already resolved ownership -- one `label`
        cell, one or more of its own rules -- so this asks it rather than
        re-deriving the geometry. See `RuledBlankWriting` and
        `ruled_blank_field_box`. **Routed through `BureauReservation`**
        (F218), the identical guard the signature-box rule below already
        took and this one had not: zero of the corpus's 58 claims are also a
        Bureau claim, checked rather than assumed. This branch is also the
        one place `shading` is deliberately NOT consulted -- see its own
        comment at the call site for the measured 41/7 split that makes an
        underscore rule on tint a writing line, not shaded decoration.
      * **A `label` cell whose own bottom wall carries a vector-drawn
        signature line, with a "Signature over Printed Name" caption in the
        cell directly below naming it, is a field there too, unless the
        sheet's own Bureau caption also claims it.** F221 case 1:
        `RuledBlankWriting`'s own relation, for a VECTOR rule straddling the
        wall this cell shares with its own caption below rather than an
        underscore rule drawn wholly inside the caption's own cell.
        `SignatureRuleWriting` has already resolved ownership; see it and
        `ruled_blank_field_box`, which this branch reuses whole -- there is
        no second field-box function for a vector-drawn signature line.
        **Routed through `BureauReservation` and `shading`**, both: none of
        this corpus's 9 claims sit on a Bureau caption or a decorative tint,
        checked rather than assumed, the same discipline the row-number rule
        below was shipped without once (F151/W2) and had to add after the
        fact.
      * **A `label` cell whose paper carries its own checkbox square is a
        field there too, unless the sheet's own Bureau caption also claims
        it.** F210: the square is a closed box of decorative rules the
        lattice never turns into a boundary, painted back to paper with a
        knockout fill -- the source's own "write here" -- so the caption
        glyphs beside it ("Taxpayer", "Yes", ...) swallow the whole area
        into one `label` cell that `lattice.classify_cell` never segments.
        `CheckboxSquareWriting` has already resolved ownership; see it and
        `checkbox_square_field_box`. **Routed through `BureauReservation`**
        (F218) for the identical reason: zero of the corpus's 22 claims are
        also a Bureau claim today, checked rather than assumed.
      * **A `label` cell whose only ink is a top-left caption dedicating it to
        the taxpayer's OWN signature is a field there too, unless the sheet's
        own Bureau caption also claims it.** F211: one caption run ("For
        Individual: ") sets `is_empty = False` and costs a 302 x 43pt writing
        surface the identical box ships today with no caption inside it
        (`0620-2019` `p1c87`, the control). `SignatureBoxWriting` has already
        resolved which `label` cells this is; see it and
        `signature_box_field_box`. **This is routed through
        `BureauReservation` here, explicitly, rather than skipped past it**:
        a promoted `label` cell used to be refused at the `kind != "field"`
        rule below before the Bureau check further down was ever reached, so
        simply moving the refusal earlier without re-consulting
        `BureauReservation` would risk handing a taxpayer one of the 71
        Bureau-only stamp/validation boxes this rule's own population also
        contains. Measured over this corpus, zero of `SignatureBoxWriting`'s
        54 claims are also `BureauReservation` claims -- the two caption
        vocabularies share no word -- but the check runs regardless, not on
        that measurement's faith.
      * **A `label` cell whose own paper carries a knockout-over-tint band
        beside a "(specify)" caption is a field there too.** F206: the band
        is a sub-region of the cell, the same "caption swallows the whole
        `label`" shape as the two rules above, and `KnockoutSpecifyWriting`
        has already resolved which cells qualify and measured the band; see
        it and `knockout_specify_field_box`. Ordered after the ruled-blank
        and checkbox-square rules so a cell either of those already claims is
        never re-claimed here, and before the signature-box rule's own
        vocabulary is irrelevant to this one: the two caption vocabularies
        ("specify" vs "for individual"/"for non-individual") share no word,
        measured the same way the signature-box/Bureau split is.
      * **A `label` cell holding ONLY a bare row number, sharing its row with
        a fillable field, is a field beside the numeral too.** F151 (P2's
        row-number rule): the row's own item number -- "1 ", "12", nothing
        else -- is printed the way BIR always prints one, and the caption
        cell's remaining paper is the row's description, swallowed the same
        way a ruled blank's line, a checkbox's square or a "(specify)" band
        is. `RowNumberWriting` has already resolved which cells qualify and
        measured the band, bounded by the form's own `line_width_pt` and
        `glyph_height_pt` (`lattice.min_fillable_line_metrics`, the sliver
        rule's own metric -- no new constant); see it and
        `row_number_field_box`. Ordered last of the four `label`-promotion
        rules because its vocabulary -- pure digits, nothing else in the
        cell's own text -- cannot overlap any of the other three's (an
        underscore rule, a checkbox square, a "specify"/"for individual"
        caption all require ink this rule's own text test refuses).
      * **A comb-bearing cell is a field whatever text it also holds.** A comb
        *is* the field -- N boxes drawn with tick marks -- and the pre-printed
        "." or "%" inside it is decoration within that field, not a label. Only
        `kind == "field"` used to reach an input, so 2000-DST's whole page-1
        money grid (`kind="mixed", comb=yes`), 2200A's Part III and IV, 2200P's
        Part III, 1801 item 24, 2316 items 23-24 and 1702EX item 18 had no way
        to type an amount at all, headline payable included. 195 cells across
        37 forms.

        This is a verdict about the CELL and it stops there. Which of that
        cell's compartments a taxpayer may type in is a separate question with
        a separate answer per compartment -- see `comb_slot_verdicts`. Reading
        this rule as "and therefore every slot is editable" is what put a live
        text box on the statutory ATC codes `II 011` and `XC 010`, on the
        century `2 0`, and on the TIN branch code `0 0 0 0 0` across 24 forms.
      * **A blank the source printed over is not a blank.** Independently of
        whether guides.py relocated the table it belongs to, a cell whose
        geometry is filled with pre-printed text gets no input: on 1700 page 2 a
        taxpayer could type over the statutory bracket "Not over P 250,000".
        This is the belt to that fix's braces and it protects the forms whose
        reference tables are never relocated.
      * **A blank the source shaded is not a blank either.** The rule above
        protects statutory TEXT and was applied to the wrong evidence for a
        whole class of cells: an empty three-bordered region on a grey band
        satisfies the box detector exactly as a white one does, because
        `lattice.classify_cell` is never told what colour the paper is. On
        2200T page 2 that let a taxpayer focus a cell the official form shades
        precisely to say NO RATE APPLIES and type 999,999.00 into it. The tone
        is in the IR -- `extract.classify_tone` computed it from the content
        stream operand -- and was simply discarded at the box-model stage.
      * **A blank the sheet's own caption reserves for the Bureau is not the
        taxpayer's.** F147: 0605's "BCS No./Item No. (To be filled up by the
        BIR)" blank is a white knockout under a caption, structurally
        identical to the taxpayer's Return Period boxes on the same row --
        the caption is the only distinguishing ink on the paper, so it is the
        evidence, bound to the ruled compartment it is printed in. See
        `BureauReservation` for the two bindings and their measured corpus.

    This function is the independent belt to `lattice.classify_cell`'s brace,
    so it asks the IR itself rather than trusting the cell kind the lattice
    assigned. Both must be wrong for a shaded cell to become typeable.

    `shading` is separate from `ink` deliberately: they answer different
    questions ("is there statutory text here" vs "is this paper a writing
    surface") off different parts of the IR, and a cell can fail either alone.
    Passing None for either means that evidence was not measured -- the caller
    says so in its report rather than the check silently passing.
    """
    if (cell["kind"] == "label" and ruled_blanks is not None
            and ruled_blanks.for_cell(cell["id"])):
        # F218: routed through `BureauReservation`, the same guard the
        # signature-box branch below already takes deliberately. Zero of the
        # corpus's 58 ruled-blank claims fall inside a Bureau-reserved
        # rectangle today, but the guard has to hold by construction, not by
        # that measurement's own faith holding forever.
        #
        # Deliberately does NOT consult `shading`, unlike the ordinary field
        # path below -- this is the one place the two are meant to disagree.
        # An underscore-drawn writing line is the taxpayer's OWN writing
        # surface even printed on a tinted band, because BIR paints the tint
        # first and the rule second: an explicit line on tint means "write
        # here", not "no rate applies". Measured (F218): of 48 underscore
        # rules sitting on tint, 41 sit inside a white knockout the sheet
        # paints to clear a writing space, and the 7 without one are genuine
        # write-on lines -- 1801 item 21's "...to be paid on or before ____"
        # (a date blank with a visible white box) and 2200A/2200P's "Others
        # (specify)" ATC lines. This override is CORRECT and must stay; see
        # `ruled_blank_corpus_assertions`'s own shaded-claim check, which
        # proves it on the corpus every run rather than leaving it to this
        # comment's word.
        if reservation is not None and reservation.blocks(
                float(cell["x0"]), float(cell["y0"]),
                float(cell["x1"]), float(cell["y1"])):
            return False, "bureau"
        return True, "ruled-blank"
    if decoration is not None:
        decoration_reason = decoration.reason(cell["id"])
        if decoration_reason is not None:
            # F235/F237, FIRST: these are refutations by the sheet's own
            # geometry (its fill, its printed constant, its own smallest
            # glyph), not competing claims -- no later branch may overrule.
            return False, decoration_reason
    if (cell["kind"] == "label" and signature_rules is not None
            and signature_rules.for_cell(cell["id"])):
        # F221 case 1: routed through BOTH `BureauReservation` and `shading`
        # -- unlike the ruled-blank branch above, which deliberately skips
        # `shading` for a measured reason stated there. This population has
        # no equivalent measurement excusing it, so it takes the ordinary
        # double guard `row_number`'s own W2 package had to add after
        # shipping without it (F151): zero of this corpus's 9 claims sit on
        # a Bureau caption or a decorative tint, checked rather than assumed.
        #
        # Asked over the CLAIMED RULES' own x-span, never the owning cell's
        # full width. 0605's own jurat cell rules item 22A/22B's two
        # signature lines AND prints "Stamp of Receiving Office and Date of
        # Receipt" 300pt further right in the SAME oversized `label` cell --
        # a caption for a DIFFERENT, already-reserved compartment on the
        # page, not for either signature line. Asking `reservation`/`shading`
        # of the whole cell (`ruled_blanks`'/`checkbox_squares`'/
        # `signature_boxes`' own shape, correct for their own single-purpose
        # cells) would refuse both real signature lines over a caption
        # neither one sits under; measured directly, this is not
        # hypothetical -- 0605-1999 and 1604cf-2008 both do this, and a
        # whole-cell test refused their real claims before this was caught.
        claimed = signature_rules.for_cell(cell["id"])
        rx0 = min(float(r["x0"]) for r in claimed)
        rx1 = max(float(r["x1"]) for r in claimed)
        if reservation is not None and reservation.blocks(
                rx0, float(cell["y0"]), rx1, float(cell["y1"])):
            return False, "bureau"
        if shading is not None and shading.blocks(
                {"x0": rx0, "y0": cell["y0"], "x1": rx1, "y1": cell["y1"]}):
            return False, "shading"
        return True, "signature-rule"
    if (cell["kind"] == "label" and checkbox_squares is not None
            and checkbox_squares.for_cell(cell["id"])):
        # F218: the identical guard, the identical reasoning -- zero of the
        # corpus's 22 checkbox-square claims fall inside a Bureau-reserved
        # rectangle today, asserted rather than assumed to stay that way.
        if reservation is not None and reservation.blocks(
                float(cell["x0"]), float(cell["y0"]),
                float(cell["x1"]), float(cell["y1"])):
            return False, "bureau"
        return True, "checkbox-square"
    if (cell["kind"] == "label" and signature_boxes is not None
            and signature_boxes.for_cell(cell["id"])):
        if reservation is not None and reservation.blocks(
                float(cell["x0"]), float(cell["y0"]),
                float(cell["x1"]), float(cell["y1"])):
            return False, "bureau"
        return True, "signature-box"
    if (cell["kind"] == "label" and knockout_specify is not None
            and knockout_specify.for_cell(cell["id"]) is not None):
        return True, "knockout-specify"
    if (cell["kind"] == "label" and row_numbers is not None
            and row_numbers.for_cell(cell["id"]) is not None):
        # A row number on SHADED paper is the sheet's own printed index, not a
        # writing surface -- the same thing the ordinary field path refuses a
        # few lines below ("a blank the source shaded is not a blank either"),
        # asked here because this branch returns before reaching it.
        #
        # Measured over the 49 cells this rule first promoted: 31 sit on >=70%
        # tint and 18 on white paper. The 31 are Seq. No. / item-index columns
        # -- 1621-2019 p2's "Seq. No. (A)" is the clean case, a grey band whose
        # 1..5 are printed by the Bureau and rendered as such. The 18 include
        # F151's four Schedule D Description cells (1701-2018-conso p2c132,
        # p2c136, p2c140, p2c144), which the official sheet leaves white and
        # writable. Nothing here keys on a form code; the discriminator is the
        # source's own tint, read through the existing gate.
        if shading is not None and shading.blocks(cell):
            return False, "shading"
        return True, "row-number"
    if cell.get("comb"):
        # Left as it stands, on measurement rather than on principle. Exactly
        # one comb-bearing cell in the corpus is >= 70% covered by decorative
        # shading at <= 0.87 (1801 p1c13, 226pt wide on the 0.8509 Part I
        # band), and its comb holds real TIN digit boxes: the band is the row,
        # the comb is the field. The 25 narrow slivers in the census -- the
        # grey gaps BETWEEN TIN digit groups, e.g. 2550M p1c10/c12/c14, 4.8pt
        # strips each carrying its own 0.7529 patch -- are plain `field` cells
        # and are caught below. Blanket-blocking a shaded comb would remove
        # real digit boxes and buy nothing. The evidence that DOES survive at
        # comb resolution is applied per slot instead, in `comb_slot_verdicts`.
        return True, "comb"
    if cell["kind"] != "field":
        return False, cell["kind"]
    if ink is not None and ink.coverage(cell) > PREPRINTED_COVERAGE:
        return False, "pre-printed"
    if shading is not None and shading.blocks(cell):
        return False, "shading"
    if reservation is not None and reservation.blocks(
            float(cell["x0"]), float(cell["y0"]),
            float(cell["x1"]), float(cell["y1"])):
        return False, "bureau"
    return True, "field"


def comb_slot_verdicts(cell: dict[str, Any], ink: PrePrintedInk | None,
                       shading: DecorativeShading | None,
                       reservation: "BureauReservation | None") -> dict[int, str]:
    """Which of a comb's compartments the source already filled in, and why.

    `field_verdict` settles the cell; a comb cell is N compartments and they do
    not share an answer. On 1600-PT the year comb prints the century `2 0` in
    its first two boxes and leaves the last two blank for the taxpayer -- one
    cell, two verdicts. Answering per cell can only be wrong in one of the two
    directions: editable everywhere (what shipped: 2,187 inputs over 180 cells
    the lattice had already marked `mixed`) or editable nowhere (which would
    delete the year).

    Three kinds of evidence, the same three `field_verdict` uses, re-asked of
    the slot's own rectangle rather than the cell's (the third, a Bureau
    caption printed in the slot, is documented at its branch below):

      * **Glyph ink the source put in the compartment** -- `slot_constant`,
        which is where the occupied-versus-free discrimination lives. 366
        compartments corpus-wide.
      * **Decorative shading under the slot** -- the identical
        `DecorativeShading.blocks` test at slot resolution. It resolves the 9
        compartments the ink rule declines because they hold a whole swallowed
        label rather than one glyph: the caption compartment of a cell whose
        lattice segmentation ate a label (1801 p1c13/c31/c33/c112, 1800 p1c26,
        2200S p1c29, 2552 p1c28, 1604F p1c25/c36). Not one is a digit box --
        they are 56 to 366pt wide. The 7 grey group-separator compartments of a
        TIN comb, which print `-` (1800 p1c15, 1801 p1c14/c15/c31 twice/c32) or
        `.` (1801 p1c57) and are exactly the "narrow grey slivers between TIN
        digit groups" the cell-level rule catches when the lattice happens to
        cut them as their own cells, used to be resolved here too; the ink rule
        now answers for them first, since the source has printed a glyph into
        each. The verdict for those 7 changes from `shading` to `pre-printed`
        and their emitted bytes do not change at all.

    Per slot and never per group, which is a decision the corpus forces. "Any
    constant in this comb blocks the comb" would delete 1600-PT's year entry
    (constant leading, blanks trailing); "everything left of the constant goes
    too" would delete the first nine digits of every 12-slot TIN comb
    (constant trailing, blanks leading: 1702EX p1c75, 1702Q p2c47). The two
    shapes are mirror images and no rule over the group distinguishes them, so
    the group is not the unit -- the compartment is. The one case this leaves
    open is recorded rather than guessed at: 1800 p2c63 prints `2 5 0 0 0 0`
    into the last six of ten compartments and leaves four leading ones blank,
    the way a right-aligned number does; those four stay editable, and typing
    in them produces a figure the form's own printed digits contradict. That is
    a segmentation question (the printed amount is one value, not a field),
    not a can-a-taxpayer-overtype-a-constant one.
    """
    comb = cell.get("comb")
    if not comb:
        return {}
    slot_x = [float(value) for value in comb["slot_x"]]
    # The verdicts are questions about the COMPARTMENT -- did the source print
    # a constant into this box, shade this box, caption this box for the
    # Bureau -- so they are asked of the compartment's writing rectangle,
    # never of the divider tick band. Asking them of the band regressed G11 on
    # the r21 integration: 1702EX's branch-code `00000` sits mid-compartment,
    # stopped being "wholly inside" the 3.84pt band, and 145 refused constants
    # corpus-wide came back as live inputs over statutory ink.
    #
    # The invariant is CONTAINMENT, not sameness: the rectangle asked about
    # must never be smaller than the rectangle the input occupies, or there is
    # ink under a live box that no verdict was asked about. Vertically the two
    # are identical (`comb_writing_rect`, the same call `comb_slots_markup`
    # makes). Horizontally the question is asked of the PRINTED compartment,
    # rail centre to rail centre, while the input is laid on the writing edges
    # inside it (`comb_slot_edges`) -- a superset, so a constant the sheet
    # tucked under its own wall still spends its compartment.
    top, height = comb_writing_rect(cell, comb)
    y0, y1 = top, top + height
    verdicts: dict[int, str] = {}
    for index in range(len(slot_x) - 1):
        x0, x1 = slot_x[index], slot_x[index + 1]
        if x1 <= x0:
            continue
        # Two shapes of "the source already wrote in this box", asked in the
        # order the corpus makes them: one glyph typeset at the comb's own
        # pitch, and -- for a compartment the lattice cut far wider than a
        # character because the sheet erased its wall -- a whole printed run
        # set inside it. Both are containment tests and neither excuses a
        # neighbour's caption clipping across the wall.
        if ink is not None and (
                ink.slot_constant(x0, y0, x1, y1) is not None
                or ink.slot_caption(x0, y0, x1, y1) is not None):
            verdicts[index] = "pre-printed"
        elif shading is not None and shading.blocks(
                {"x0": x0, "y0": y0, "x1": x1, "y1": y1}):
            verdicts[index] = "shading"
        elif reservation is not None and reservation.blocks(x0, y0, x1, y1):
            # The bottom-of-sheet shape: 2200-A/C/P's "Machine Validation" /
            # "Stamp of Receiving Office" band reads to the lattice as one
            # 2-slot comb, so the Bureau's two boxes surface here, each
            # carrying its own caption. Slot resolution for the same reason
            # ink and shading get it -- the compartments do not share answers.
            verdicts[index] = "bureau"
    return verdicts


class FieldPlan:
    """Every field on the sheet, and the CSS classes their metrics collapse to.

    The metrics are shared: the 65 comb cells on 2551Q page 1 sit in identically
    sized sub-bands, so writing `font-size`/`line-height`/`letter-spacing` onto
    each of their 488 inputs would repeat one declaration 488 times. They are
    collected first, sorted, then named `fh<n>`, which keeps the class index a
    pure function of the layout and therefore deterministic.
    """

    __slots__ = ("face", "boxes", "classes", "small", "blocked",
                 "undersized_source", "undersized_derived", "uncontained",
                 "collapsed", "comb_count", "blocked_slots", "slot_count",
                 "centered")

    def __init__(self, layout: dict[str, Any], face: FieldFace | None,
                 warnings: list[str], ir: dict[str, Any] | None = None) -> None:
        self.face = face
        self.boxes: dict[str, FieldBox] = {}
        self.classes: dict[tuple[float, float, float | None], str] = {}
        self.small: list[str] = []
        # The two populations `small` used to conflate, and the two comb-band
        # faults nothing reported at all. Each cell id appears in at most one of
        # `uncontained` and `collapsed`: a band that has left its cell is
        # already the worse finding and its share of the cell is meaningless.
        self.undersized_source: list[str] = []
        self.undersized_derived: list[str] = []
        self.uncontained: list[str] = []
        self.collapsed: list[str] = []
        self.comb_count = 0
        self.slot_count = 0
        # cell id -> why it has no typing surface, for the markup and the report.
        self.blocked: dict[str, str] = {}
        # cell id -> {slot index: why that ONE compartment has none}. Separate
        # from `blocked` because it is a different claim: `blocked` says the
        # cell is not a blank at all, this says the source already wrote in
        # some of the boxes it drew. A cell appears in at most one of them --
        # a comb cell is always fillable at cell resolution.
        self.blocked_slots: dict[str, dict[int, str]] = {}
        # cell id -> the plain field's own input(s) carry an inline
        # `text-align:center` (F212's signature-strip target set; see
        # `SignatureLineBinding`). Never a comb slot -- those are centred by
        # the stylesheet's `.fc` rule already, and this set is only ever
        # consulted for `slot_index is None` in `field_input_markup`.
        self.centered: set[str] = set()
        if face is None:
            return
        # One natural line at the fitted face, the target height
        # `seat_signature_line` re-seats a signature strip's own box to.
        one_line_pt = face.size_pt * face.line_span_em
        # The whole-IR metric `KnockoutSpecifyWriting` measures a band's
        # width and height against; None when the IR states no body run at
        # all (an empty/synthetic fixture), in which case that class claims
        # nothing rather than guessing a threshold.
        fillable_metrics = _min_fillable_line_metrics(ir) if ir is not None else None
        # Keyed by page index rather than zipped, so a layout page and an IR page
        # cannot be paired by position if either list is ever ordered otherwise.
        ink_by_page = {int(page["index"]): PrePrintedInk(page["text_runs"])
                       for page in (ir or {}).get("pages", ())}
        shading_by_page = {int(page["index"]): DecorativeShading(page["area_fills"])
                           for page in (ir or {}).get("pages", ())}
        reservation_by_page = {
            int(page["index"]): BureauReservation(
                page["text_runs"], page["rules"], page["area_fills"])
            for page in (ir or {}).get("pages", ())}
        # Rules only, keyed the same way: RuledBlankWriting and
        # CheckboxSquareWriting need the layout's OWN cells (to know which
        # are `label`), so each is built per page below rather than
        # alongside the three evidence indices above.
        rules_by_page = {int(page["index"]): page["rules"]
                         for page in (ir or {}).get("pages", ())}
        fills_by_page = {int(page["index"]): page["area_fills"]
                         for page in (ir or {}).get("pages", ())}
        # Runs only, keyed the same way again: SignatureBoxWriting and
        # SignatureLineBinding both read the page's OWN text runs by id
        # (`run_id`), the same evidence PrePrintedInk is built from above,
        # not derived from it.
        runs_by_page = {int(page["index"]): page["text_runs"]
                        for page in (ir or {}).get("pages", ())}
        for page in layout["pages"]:
            page_index = int(page["index"])
            ink = ink_by_page.get(page_index)
            shading = shading_by_page.get(page_index)
            reservation = reservation_by_page.get(page_index)
            rules = rules_by_page.get(page_index)
            fills = fills_by_page.get(page_index)
            runs = runs_by_page.get(page_index)
            ruled_blanks = (RuledBlankWriting(rules, page["cells"])
                            if rules is not None else None)
            checkbox_squares = (
                CheckboxSquareWriting(rules, fills or (), page["cells"])
                if rules is not None else None)
            signature_boxes = (
                SignatureBoxWriting(page["cells"], page_index, runs)
                if runs is not None else None)
            signature_lines = (
                SignatureLineBinding(
                    page["cells"], page_index, runs,
                    signature_boxes.cell_ids() if signature_boxes is not None
                    else frozenset())
                if runs is not None else None)
            knockout_specify = (
                KnockoutSpecifyWriting(
                    page["cells"], page_index, runs, fills or (), fillable_metrics)
                if runs is not None else None)
            row_numbers = (
                RowNumberWriting(page["cells"], page_index, runs, fillable_metrics)
                if runs is not None else None)
            signature_rules = (
                SignatureRuleWriting(page["cells"], page_index, rules, runs,
                                     fillable_metrics)
                if rules is not None and runs is not None else None)
            decoration = PrintedDecoration(
                page["cells"], fills or (), runs,
                (fillable_metrics or {}).get("glyph_height_pt"))
            for cell in page["cells"]:
                fillable, reason = field_verdict(cell, ink, shading, reservation,
                                                 ruled_blanks, checkbox_squares,
                                                 signature_boxes, knockout_specify,
                                                 row_numbers, signature_rules,
                                                 decoration)
                if not fillable:
                    if reason in ("pre-printed", "shading", "bureau"):
                        self.blocked[cell["id"]] = reason
                    continue
                if reason == "ruled-blank":
                    box = ruled_blank_field_box(
                        cell, ruled_blanks.for_cell(cell["id"]), face, ink)
                elif reason == "signature-rule":
                    box = ruled_blank_field_box(
                        cell, signature_rules.for_cell(cell["id"]), face, ink)
                elif reason == "checkbox-square":
                    box = checkbox_square_field_box(
                        cell, checkbox_squares.for_cell(cell["id"]), face, ink)
                elif reason == "signature-box":
                    box = signature_box_field_box(
                        cell, signature_boxes.for_cell(cell["id"]), face, ink)
                elif reason == "knockout-specify":
                    box = knockout_specify_field_box(
                        cell, knockout_specify.for_cell(cell["id"]), face, ink)
                elif reason == "row-number":
                    box = row_number_field_box(
                        cell, row_numbers.for_cell(cell["id"]), face, ink)
                else:
                    box = field_box(cell, face, ink)
                if box is None:
                    warnings.append(f"cell {cell['id']}: the field's writing box has no "
                                    f"height, so it gets no typing surface")
                    continue
                if (reason == "signature-rule"
                        or (signature_lines is not None
                            and signature_lines.for_cell(cell["id"]))):
                    # F221 case 1's own box already sits ON its rule, one
                    # line tall (`ruled_blank_field_box`'s own geometry), so
                    # `seat_signature_line` is a no-op for it every time --
                    # its own guard returns a box already at or under one
                    # line unchanged. It is still called, not skipped,
                    # because it is the SAME re-seat `SignatureLineBinding`'s
                    # own claims take, and a future form whose vector rule
                    # leaves more than one line of headroom above it must not
                    # silently start floating this box at that box's own
                    # centre instead of on the line.
                    box = seat_signature_line(box, one_line_pt)
                    self.centered.add(cell["id"])
                self.boxes[cell["id"]] = box
                self._audit_surface(cell, box, face)
                comb = cell.get("comb")
                if comb:
                    self.slot_count += max(len(comb["slot_x"]) - 1, 0)
                    filled = comb_slot_verdicts(cell, ink, shading, reservation)
                    if filled:
                        self.blocked_slots[cell["id"]] = filled
        # Sorted through an explicit key: the tracking is None for a field whose
        # scaled tracking rounds to nothing, and a bare tuple sort would compare
        # None against a float the day two boxes share a size and differ there.
        distinct = sorted({b.metrics_key for b in self.boxes.values()},
                          key=lambda k: (k[0], k[1], k[2] is not None, k[2] or 0.0))
        for index, key in enumerate(distinct):
            self.classes[key] = f"fh{index}"
        self._report(warnings)
        # Reported as two populations, each naming what it excluded: they are
        # blocked on different evidence, and a count alone would leave a reader
        # unable to tell a statutory bracket from a shaded spacer or to check
        # either. An exclusion has to publish what it excluded.
        printed = sorted(i for i, why in self.blocked.items() if why == "pre-printed")
        shaded = sorted(i for i, why in self.blocked.items() if why == "shading")
        reserved = sorted(i for i, why in self.blocked.items() if why == "bureau")
        if reserved:
            warnings.append(
                f"{len(reserved)} cell(s) the box detector called blank are reserved "
                f"for the Bureau by the sheet's own caption and get no typing surface "
                f"({self._sample(reserved)}); a taxpayer must not be able to fill a "
                f"box the form says the BIR fills")
        if printed:
            warnings.append(
                f"{len(printed)} cell(s) the box detector called blank are filled with "
                f"pre-printed text and get no typing surface ({self._sample(printed)}); "
                f"a taxpayer must not be able to type over a statutory rate")
        if shaded:
            warnings.append(
                f"{len(shaded)} cell(s) the box detector called blank sit on decorative "
                f"shading at grey <= {fmt(DECORATIVE_GRAY_MAX)} and get no typing surface "
                f"({self._sample(shaded)}); the source shaded them to say no entry "
                f"applies there")
        # The same obligation at slot resolution, and named the same two ways:
        # a compartment refused for glyph ink is a value the form STATES and a
        # reader must be able to check that claim against the sheet, while one
        # refused for tone is a separator or a swallowed caption. Reporting a
        # single total would put an ATC code and a TIN hyphen in one bucket.
        printed_slots = sorted(
            f"{cell_id}[{index}]" for cell_id, slots in self.blocked_slots.items()
            for index, why in slots.items() if why == "pre-printed")
        shaded_slots = sorted(
            f"{cell_id}[{index}]" for cell_id, slots in self.blocked_slots.items()
            for index, why in slots.items() if why == "shading")
        reserved_slots = sorted(
            f"{cell_id}[{index}]" for cell_id, slots in self.blocked_slots.items()
            for index, why in slots.items() if why == "bureau")
        if reserved_slots:
            warnings.append(
                f"{len(reserved_slots)} of {self.slot_count} comb slot(s) are boxes "
                f"the sheet's own caption reserves for the Bureau and get no input "
                f"({self._sample(reserved_slots)}); they are the Machine Validation "
                f"and Stamp of Receiving Office boxes the lattice reads as a comb")
        if printed_slots:
            warnings.append(
                f"{len(printed_slots)} of {self.slot_count} comb slot(s) already carry "
                f"a constant the form prints and get no input "
                f"({self._sample(printed_slots)}); a taxpayer must not be able to "
                f"overtype a statutory code, a century or a fixed branch code")
        if shaded_slots:
            warnings.append(
                f"{len(shaded_slots)} of {self.slot_count} comb slot(s) sit on "
                f"decorative shading at grey <= {fmt(DECORATIVE_GRAY_MAX)} and get no "
                f"input ({self._sample(shaded_slots)}); they are the grey gaps between "
                f"digit groups and the caption end of a cell the lattice cut too wide")
        if ir is None:
            warnings.append(
                "no IR was given to the field plan, so pre-printed occupancy and paper "
                "tone are both unmeasured; a cell filled with statutory text or shaded "
                "out by the source may be editable")

    def _audit_surface(self, cell: dict[str, Any], box: FieldBox,
                       face: FieldFace) -> None:
        """Classify one emitted writing box against the cell it came from."""
        cell_height = float(cell["y1"]) - float(cell["y0"])
        if box.size_pt < FIELD_MIN_SIZE_PT:
            self.small.append(cell["id"])
            # The same fit `field_box` performs, applied to the whole cell: if
            # the cell could have carried a legible size then the box that
            # cannot is one we derived, not one the source drew.
            capable = (face.line_span_em > 0.0
                       and _floor2(cell_height / face.line_span_em) >= FIELD_MIN_SIZE_PT)
            target = self.undersized_derived if capable else self.undersized_source
            target.append(cell["id"])
        comb = cell.get("comb")
        if not comb:
            return
        self.comb_count += 1
        # The box this classifies is the one the emitter actually lays out, so
        # it is measured on `comb_writing_rect`'s rectangle. Measured on the
        # divider band instead, 4,474 of 4,522 combs report `collapsed` and 225
        # report `uncontained` -- a report about a rectangle nothing is drawn
        # on, which is how the 3.12pt slot survived a whole round.
        write_top, height = comb_writing_rect(cell, comb)
        top = write_top - float(cell["y0"])
        if (top < -FIELD_CONTAINMENT_EPSILON_PT
                or top + height > cell_height + FIELD_CONTAINMENT_EPSILON_PT):
            self.uncontained.append(cell["id"])
        elif cell_height > 0.0 and height < COMB_BAND_COLLAPSE_RATIO * cell_height:
            self.collapsed.append(cell["id"])

    @staticmethod
    def _sample(ids: Sequence[str], limit: int = 6) -> str:
        return ", ".join(ids[:limit]) + ("..." if len(ids) > limit else "")

    def _report(self, warnings: list[str]) -> None:
        """State every writing-box fault, by population, against a denominator.

        Four populations, because one count could not be acted on. The previous
        report said "N field(s) fit the body face at under 4pt; the box is the
        source's own" about every undersized field at once, which was true of 15
        of them and false of the other 228, and said nothing whatever about a
        comb band that had left its cell -- 225 of those, unreported, including
        one 350.16pt band inside a 16.80pt cell.

        Each line names its denominator. A report that says "228 fields" leaves
        a reader guessing whether that is most of the sheet or a rounding error;
        one that says "228 of 10,481" does not, and a count that moves between
        two runs is legible without going and measuring the corpus.

        Note where this arrives: `main` prints these to stderr, and batch.py's
        `run_stage` discards a subprocess's stderr entirely unless it exits
        non-zero, so on a corpus run nobody sees a single one of them. That is
        the real reason the old warning was invisible -- not that it fired often,
        but that it fired into a stream that is thrown away. The channels that do
        survive are `--self-test`, which gate.py runs and cannot discard, and the
        `?debug=fields` overlay, which puts the same four populations in front of
        a human in colour. Both are wired to these lists.
        """
        total = len(self.boxes)
        if self.undersized_derived:
            warnings.append(
                f"{len(self.undersized_derived)} of {total} field(s) fit the body face "
                f"at under {fmt(FIELD_MIN_SIZE_PT)}pt inside a cell that is tall enough "
                f"to carry a legible one, so the writing box was lost in derivation and "
                f"is ours to fix, not the source's ({self._sample(self.undersized_derived)})")
        if self.undersized_source:
            warnings.append(
                f"{len(self.undersized_source)} of {total} field(s) fit the body face at "
                f"under {fmt(FIELD_MIN_SIZE_PT)}pt because the cell the source drew is "
                f"itself that short; the box is the source's own and is emitted as "
                f"measured ({self._sample(self.undersized_source)})")
        if self.uncontained:
            warnings.append(
                f"{len(self.uncontained)} of {self.comb_count} comb writing box(es) are "
                f"not inside the cell that owns them, so `.f{{overflow:hidden}}` clips "
                f"away a typing surface that cannot be seen, reached or printed "
                f"({self._sample(self.uncontained)})")
        if self.collapsed:
            warnings.append(
                f"{len(self.collapsed)} of {self.comb_count} comb writing box(es) cover "
                f"under {fmt(COMB_BAND_COLLAPSE_RATIO * 100)}% of the height of their own "
                f"cell, so the typing surface is the divider band rather than the box the "
                f"cell walls draw ({self._sample(self.collapsed)})")

    def __bool__(self) -> bool:
        return bool(self.boxes)

    def of(self, cell_id: str) -> FieldBox | None:
        return self.boxes.get(cell_id)

    def slot_blocked(self, cell_id: str, index: int) -> str | None:
        """Why this one comb compartment carries no input, or None."""
        return self.blocked_slots.get(cell_id, {}).get(index)

    def class_of(self, box: FieldBox) -> str:
        return self.classes[box.metrics_key]


def field_region_suffix(box: FieldBox, region_index: int) -> str:
    """The id suffix for one plain field's writing region.

    A field with one region keeps `-i`, which is every field this corpus had
    before the source's own dividers were read and is what stage 3 will map. A
    field the sheet rules into several regions numbers them, because they are
    several places to write and one name cannot address two of them.
    """
    return "-i" if box.regions is None else f"-i{region_index}"


# Hair-tick charbox compartments (P1). 2550M "No. of sheets" regions measure
# 24.6pt; zip/RDO regions 9.72-16.68pt. A table-column split (ADDRESS vs
# STATUS) is tens to hundreds of points. The gap is empty; 28pt sits in it.
CHAR_REGION_MAX_PT = 28.0
# Square-ish X-boxes the sheet says to mark with an X (P1b). Reuses the F210
# knockout band (4-20pt) plus an aspect cut so a 5x19 sliver is not an X-box.
XBOX_ASPECT_LO = 0.70
XBOX_ASPECT_HI = 1.45


def region_inner_size(cell: dict[str, Any],
                      inset: tuple[float, float, float, float] | None
                      ) -> tuple[float, float]:
    """Writing width/height of one region, from the cell and that region's inset."""
    width = float(cell["x1"]) - float(cell["x0"])
    height = float(cell["y1"]) - float(cell["y0"])
    if inset is None:
        return width, height
    top, right, bottom, left = inset
    return width - left - right, height - top - bottom


def input_is_single_character(cell: dict[str, Any], box: FieldBox,
                              region_index: int) -> bool:
    """Whether this writing region is one character on the printed sheet.

    Comb slots never consult this (`slot_index is not None` already stamps
    maxlength=1). Two populations remain, both `type="text"`:

      * P1b / F210: the region itself is a checkbox square (lattice cell or
        knockout interior inside a label).
      * P1: every region of the cell is a digit-sized hair-tick compartment.
        A wide+narrow split (ADDRESS vs STATUS) fails the all-regions test
        and is left unbounded.
    """
    sizes = [region_inner_size(cell, inset) for inset in box.region_insets]
    width, height = sizes[region_index]
    if (CHECKBOX_SQUARE_MIN_PT <= width <= CHECKBOX_SQUARE_MAX_PT
            and CHECKBOX_SQUARE_MIN_PT <= height <= CHECKBOX_SQUARE_MAX_PT
            and width > 0.0
            and XBOX_ASPECT_LO <= height / width <= XBOX_ASPECT_HI):
        return True
    if (len(sizes) > 1
            and all(0.0 < inner_w <= CHAR_REGION_MAX_PT for inner_w, _h in sizes)):
        return True
    return False


def field_input_markup(cell_id: str, box: FieldBox, fields: FieldPlan,
                       slot_index: int | None, live: bool,
                       region_index: int = 0,
                       cell: dict[str, Any] | None = None) -> str:
    """One <input>. Its geometry is its parent's unless it is a plain field.

    A template's inputs carry no `id` and no `name`: template content is the
    blueprint for rows that do not exist yet, and a name is an identity. The
    band renderer stamps both from the row's own cell id as it clones.
    """
    classes = ["fi", fields.class_of(box)]
    attrs: list[str] = []
    declarations: list[str] = []
    # F227: a comb slot's input can carry an inset too, off printed ink the
    # sheet has already put in the row's shared top
    # (`comb_writing_top_clear_of_printed_ink`) -- never off the slot div
    # itself, which stays the referee's rectangle. `region_index` is always 0
    # for a comb slot (its caller never varies it) and `region_insets` is
    # always `(inset_trbl,)` for one -- combs carry no `regions` -- so this is
    # the same lookup a plain field's own region already used, unified rather
    # than duplicated.
    region = box.region_insets[region_index]
    if region:
        declarations.append(
            "inset:" + " ".join(f"{fmt(v)}pt" for v in region))
    if slot_index is None:
        # F212: a signature strip's own writing line is centred over the
        # printed name below it -- an inline declaration, never a new class
        # or a new rule on `.fi`, because the referee's stylesheet allowlist
        # would reject either while its inline-style scan (unlike its
        # attribute-KEY sets) does not ban `text-align`. Comb slots never
        # reach here (`slot_index is None` guards this branch); they are
        # already centred by the stylesheet's own `.fc` rule.
        if cell_id in fields.centered:
            declarations.append("text-align:center")
    else:
        classes.append("fc")
    if declarations:
        attrs.append(f'style="{esc_attr(";".join(declarations))}"')
    identity: list[str] = []
    if live:
        suffix = (field_region_suffix(box, region_index) if slot_index is None
                  else f"-s{slot_index}")
        identity = [f'id="{esc_attr(cell_id + suffix)}"', f'name="{esc_attr(cell_id)}"']
    if slot_index is not None:
        identity.append(f'data-slot-index="{slot_index}"')
        identity.append('maxlength="1"')
    elif (cell is not None
          and input_is_single_character(cell, box, region_index)):
        identity.append('maxlength="1"')
    return ('<input type="text" class="' + " ".join(classes) + '" '
            + " ".join(identity + attrs + ['autocomplete="off"', 'spellcheck="false"'])
            + ">")


def field_json(box: FieldBox, fields: FieldPlan, cell_id: str,
               cell: dict[str, Any] | None = None) -> dict[str, Any]:
    """The field as data, so the band renderer can build one from scratch."""
    ones = (
        [input_is_single_character(cell, box, index)
         for index in range(len(box.region_insets))]
        if cell is not None else [])
    return {
        "kind": box.kind,
        "class": fields.class_of(box),
        "size_pt": box.size_pt,
        "line_height_pt": box.line_height_pt,
        "letter_spacing_pt": box.letter_spacing_pt,
        "capacity": box.capacity,
        "inset_trbl": ([round(v, 4) for v in box.inset_trbl] if box.inset_trbl else None),
        # One entry per writing region, so a band row cloned at run time gets
        # the same regions the pre-rendered rows have. Emitted only where the
        # source divides the cell: a one-region field's JSON is unchanged, and
        # `inset_trbl` remains the answer for every reader that has one.
        **({"region_insets": [
            None if inset is None else [round(v, 4) for v in inset]
            for inset in box.region_insets]} if box.regions is not None else {}),
        # F219: mirrors `field_input_markup`'s own inline `text-align:center`
        # (`if cell_id in fields.centered`) into the band blob, so a row
        # cloned at run time from THIS json is not left-aligned when its
        # pre-rendered sibling is centred. Sparse, like `region_insets` above:
        # present only where the field IS centred, so an uncentred field's
        # JSON is unchanged and every other reader keeps its current answer.
        **({"centered": True} if cell_id in fields.centered else {}),
        # P1/P1b: same sparsity. A cloned row must stamp maxlength=1 on the
        # same regions the pre-rendered inputs already carry, and nowhere else.
        **({"maxlength_one": True} if ones and all(ones) else {}),
        **({"region_maxlength_one": ones}
           if ones and any(ones) and not all(ones) else {}),
    }


FIELD_CSS_ORDER = ("font-family", "font-weight", "font-style", "font-kerning",
                   "font-variant-ligatures", "font-feature-settings",
                   "font-variation-settings", "color")


def field_css(fields: FieldPlan) -> str:
    """The field layer's stylesheet: one face, N fitted metrics, no printed ink.

    The screen affordances are `:hover`/`:focus` only. Print has neither state,
    so an empty field prints as nothing *by construction*, not merely because
    the `@media print` reset below says so.
    """
    if not fields or fields.face is None:
        return ""
    css = fields.face.css
    declarations = [(name, css[name]) for name in FIELD_CSS_ORDER if name in css]
    declarations.extend((name, css[name])
                        for name in sorted(set(css) - set(FIELD_CSS_ORDER)))
    lines = [
        ".fi{position:absolute;inset:0;appearance:none;-webkit-appearance:none;"
        "border:0;margin:0;padding:0;background:transparent;box-shadow:none;outline:0;"
        "border-radius:0;caret-color:#000000;text-rendering:geometricPrecision;"
        + style_attr(declarations) + "}",
        # A character must not show outside the box it was typed in. It is not
        # hypothetical: 1600-VT classifies a 1.04pt-wide sliver as a field, and
        # the digit typed into it printed 5pt wide across the first slot of the
        # comb beside it. The clip is stated on the field box rather than on the
        # input, because the box is what the source drew. Note what it does and
        # does not do: clipping is not deletion, so the round-trip still finds
        # the glyph at its full extent and fill_check.py still attributes it to
        # the field it was typed into. What this fixes is the sheet.
        ".f,.f .s{overflow:hidden}",
        ".fc{text-align:center}",
    ]
    for key, name in sorted(fields.classes.items(), key=lambda kv: kv[1]):
        size, line_height, spacing = key
        pairs = [("font-size", f"{fmt(size)}pt"),
                 ("line-height", f"{fmt(line_height)}pt"),
                 ("letter-spacing", None if spacing is None else f"{fmt(spacing)}pt")]
        lines.append(f".{name}{{{style_attr(pairs)}}}")
    lines.append("@media screen{.fi:hover{background:rgba(21,101,192,.07)}"
                 ".fi:focus{background:rgba(255,213,0,.35)}}")
    # The caret is the one piece of input chrome that is not a background, a
    # border or an outline, and Chromium prints it: a sheet printed with the
    # cursor still in a field came back with a 0.75x6.75pt black bar that the
    # round-trip reported as an extra structural rule, because that is exactly
    # what an extra 1px vertical bar is. Measured, `caret-color` does *not*
    # suppress it in Chromium's print path -- FIELD_JS dropping the focus on
    # `beforeprint` is what does. This declaration is the second line of
    # defence and the correct thing to say, not the fix.
    lines.append("@media print{.fi{border:0;outline:0;background:none;box-shadow:none;"
                 "-webkit-appearance:none;appearance:none;caret-color:transparent}}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Cells and combs
# ---------------------------------------------------------------------------


def cell_markup(cell: dict[str, Any], fields: FieldPlan | None = None,
                id_attribute: str = "id") -> str:
    """One addressable box. Fields carry their comb slots; nothing paints ink.

    The rule layer already holds every border and every comb divider, so a cell
    that also drew them would double the ink and make the round-trip diff
    report phantom extra rules. What a cell contributes is identity and exact
    geometry: `id`, `data-cell-kind`, and for a comb the measured slot
    positions, which are never index*pitch -- the pitch is not uniform and the
    deviations reach 0.12pt.

    Inside a <template> the identity moves to `data-cell-id`: template content
    is a blueprint for rows that do not exist yet, and stamping the row it was
    cut from with a live `id` would hand two elements the same identity the
    moment the renderer clones it.

    A field cell also carries its typing surface. The cell *is* the field --
    including a comb, which is one field drawn with N tick marks and not N
    fields -- so the field's name, kind and comb capacity are attributes of the
    cell, and the inputs under it are how that one field is typed into.
    """
    width = cell["x1"] - cell["x0"]
    height = cell["y1"] - cell["y0"]
    style = style_attr((
        ("left", f"{fmt(cell['x0'])}pt"), ("top", f"{fmt(cell['y0'])}pt"),
        ("width", f"{fmt(width)}pt"), ("height", f"{fmt(height)}pt"),
    ))
    kind = cell["kind"]
    box = fields.of(cell["id"]) if fields is not None else None
    if box is not None and box.kind == "comb" and not cell.get("comb"):
        # A guide cut clipped this cell and its comb band went to the other
        # piece. The field is that piece's; this one is a box, not a blank.
        box = None
    # `.f` is the field box -- the thing that clips a typed character to the box
    # the source drew -- so it follows the typing surface and not the box
    # detector's `kind`. A comb-bearing `mixed` cell is a field; a `field` cell
    # filled with pre-printed text is not.
    classes = "c f" if box is not None else "c"
    attrs = [f'{id_attribute}="{esc_attr(cell["id"])}"', f'class="{classes}"',
             f'data-cell-kind="{esc_attr(kind)}"',
             f'data-row="{cell["row"]}"', f'data-col="{cell["col"]}"']
    if not cell.get("rectangular", True):
        attrs.append('data-rectangular="false"')
    # One attribute, because it answers one question -- "the source printed
    # over this blank, so it is not one" -- and its value names WHICH evidence
    # said so. `"true"` is kept for glyph ink rather than renamed: three
    # resolved findings quote it verbatim, and a shaded cell is as literally
    # pre-printed as a bracket of statutory text is. A Bureau-reserved cell
    # says `"bureau"`: the comb referee pins this element's KEY set, not the
    # value, and a reader must be able to tell a blank the BIR reserved from
    # one the sheet printed over.
    blocked = fields.blocked.get(cell["id"]) if fields is not None else None
    if blocked:
        attrs.append('data-preprinted="shading"' if blocked == "shading"
                     else ('data-preprinted="bureau"' if blocked == "bureau"
                           else 'data-preprinted="true"'))
    live = id_attribute == "id"
    if box is not None:
        attrs.append(f'data-field-kind="{esc_attr(box.kind)}"')
        if live:
            attrs.append(f'data-field-name="{esc_attr(cell["id"])}"')
        if box.capacity is not None:
            attrs.append(f'data-comb-capacity="{box.capacity}"')

    comb = cell.get("comb")
    body = ""
    if comb:
        attrs.append(f'data-comb-slots="{comb["cells"]}"')
        attrs.append(f'data-comb-pitch="{fmt(comb["pitch_pt"])}"')
        body = comb_slots_markup(cell, comb, box, fields, live)
    elif box is not None:
        # One input per writing region. A cell the source rules across is
        # several places to write, and one box laid over the rule is the defect
        # `writing_regions` exists to answer; 9,932 of 9,971 cells have exactly
        # one region and emit exactly the input they always did.
        body = "".join(
            field_input_markup(cell["id"], box, fields, None, live, index, cell)
            for index in range(len(box.region_insets)))
    return f'<div {" ".join(attrs)} style="{esc_attr(style)}">{body}</div>'


def comb_slots_markup(cell: dict[str, Any], comb: dict[str, Any],
                      box: "FieldBox | None" = None,
                      fields: "FieldPlan | None" = None, live: bool = True) -> str:
    """N slots inside ONE field, from the comb's own measured slot_x.

    Each slot holds one input, and that is the whole of "the glyphs land in the
    slots": the input *is* the slot box, so a centred character is centred on a
    measured slot centre by layout rather than by an advance calculation over a
    pitch that is not uniform (2551Q's pitch deviates by up to 0.12pt).

    Each slot the SOURCE already filled holds none. The slot div is emitted
    either way, because the printed compartment exists either way and every
    consumer that counts compartments -- `comb_slots_match_printed`, the comb
    referee's structural pass -- is counting the artwork, not the affordance.
    What changes is only whether there is something to type into, which is the
    same signal `money_boxes_have_inputs` already reads ("<input" in the slot).

    No attribute says so, deliberately. The obvious move is a `data-preprinted`
    on the slot to match the one `cell_markup` puts on a blocked cell, and it
    would break two downstream readers that pin this element's exact attribute
    set: `audit.SLOT_RE` matches `class`, `data-slot`, `style` in that order and
    nothing else, and `comb_referee._emitter_attributes_valid` asserts the key
    set is exactly `{"class", "data-slot", "style"}`. The exclusion is published
    through `FieldPlan`'s warnings, per cell and per slot index, instead.
    """
    # The slot rectangle is the compartment a taxpayer types into, so on BOTH
    # axes it is the comb's WRITING rectangle: never its divider band
    # vertically (on the band, 2550M's item-4 TIN slots were 3.12pt tall inside
    # a 15.60pt row) and never its rail centres horizontally (on the centres,
    # the outer compartments are laid across half of each printed wall).
    # `comb_writing_rect` and `comb_slot_edges` document which is which and why.
    slot_x = comb_slot_edges(comb)
    write_top, height = comb_writing_rect(cell, comb)
    top = write_top - cell["y0"]
    parts = []
    for index in range(len(slot_x) - 1):
        left = float(slot_x[index]) - cell["x0"]
        width = float(slot_x[index + 1]) - float(slot_x[index])
        style = style_attr((
            ("left", f"{fmt(left)}pt"), ("top", f"{fmt(top)}pt"),
            ("width", f"{fmt(width)}pt"), ("height", f"{fmt(height)}pt"),
        ))
        # `live` is the guard, not an optimisation. A band template is cut from
        # row 0 and carries row 0's cell id, so consulting the verdict there
        # would take a constant printed in the first row of a schedule and
        # stamp it onto every row the renderer clones -- rows whose paper is
        # blank. What the source printed is a fact about the row it printed it
        # in; the pre-rendered rows are real cells and get their own verdicts a
        # few lines up in `emit_page`. No cell in the corpus is affected either
        # way (measured: 0 of the 82 cells with a filled compartment lie inside
        # a growable band), so this is a guard rather than a behaviour.
        inner = ("" if box is None or fields is None
                 or (live and fields.slot_blocked(cell["id"], index) is not None)
                 else field_input_markup(cell["id"], box, fields, index, live))
        parts.append(f'<div class="s" data-slot="{index}" '
                     f'style="{esc_attr(style)}">{inner}</div>')
    return "".join(parts)


def cell_json(cell: dict[str, Any], fields: "FieldPlan | None" = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": cell["id"],
        "kind": cell["kind"],
        "row": cell["row"], "col": cell["col"],
        "x": round(cell["x0"], 4), "y": round(cell["y0"], 4),
        "w": round(cell["x1"] - cell["x0"], 4), "h": round(cell["y1"] - cell["y0"], 4),
    }
    comb = cell.get("comb")
    if comb:
        # `slot_x`/`y`/`h` are what the runtime re-lays a CLONED band row's
        # slots from, so all three have to be the same rectangles
        # `comb_slots_markup` gave the pre-rendered rows -- the writing
        # rectangle on both axes. A clone laid out on the divider band would be
        # a row whose boxes are 3pt tall beside identical printed rows whose
        # boxes are not, and a clone laid out on the rail centres would be a row
        # whose first and last boxes sit on the printed wall beside identical
        # printed rows whose boxes do not.
        write_top, height = comb_writing_rect(cell, comb)
        payload["comb"] = {
            "cells": comb["cells"],
            "pitch_pt": comb["pitch_pt"],
            "slot_x": [round(value - cell["x0"], 4)
                       for value in comb_slot_edges(comb)],
            "y": round(write_top - cell["y0"], 4),
            "h": round(height, 4),
        }
    box = fields.of(cell["id"]) if fields is not None else None
    if box is not None:
        payload["field"] = field_json(box, fields, cell["id"], cell)
    return payload


# ---------------------------------------------------------------------------
# Growable bands
# ---------------------------------------------------------------------------


class BandPlan:
    """A growable band decomposed into what repeats and what stretches.

    A band is not six copies of a picture. Three different things live in it
    and they grow differently:

      row-local   rules, cells and text wholly inside one row slab -> repeat
      boundary    the separator centred on each row_y -> one per boundary,
                  so N rows need N+1 of them
      spanning    a vertical running the full height of the band -> stretches

    Rows inside the official capacity are emitted from their *measured*
    geometry rather than from a template offset, because the pitch is not
    constant: row 6 is 18.27pt where the others are 18.24pt, and the comb
    sub-bands drift independently of the row rules. Rendering row i from
    row_y[i] is exact; rendering it from y0 + i*pitch is not.
    """

    __slots__ = ("band", "page_index", "rules_by_row", "boundary_rules", "spanning_rules",
                 "cells_by_row", "texts_by_row", "rule_ids", "cell_ids", "run_ids",
                 "template_row", "template_boundary", "ordinal_column")

    def __init__(self, band: dict[str, Any], page_index: int) -> None:
        self.band = band
        self.page_index = page_index
        self.rules_by_row: dict[int, list[Rect]] = {}
        self.boundary_rules: dict[int, list[Rect]] = {}
        self.spanning_rules: list[tuple[Rect, float, float]] = []
        self.cells_by_row: dict[int, list[dict[str, Any]]] = {}
        self.texts_by_row: dict[int, list[tuple[str, dict[str, Any], Any]]] = {}
        self.rule_ids: set[str] = set()
        self.cell_ids: set[str] = set()
        self.run_ids: set[str] = set()
        self.template_row: int = 0
        self.template_boundary: int = 0
        self.ordinal_column: tuple[float, float] | None = None

    @property
    def row_y(self) -> list[float]:
        return [float(v) for v in self.band["row_y"]]

    @property
    def capacity(self) -> int:
        return int(self.band["capacity"])


def _row_of(y_top: float, y_bottom: float, row_y: Sequence[float]) -> int | None:
    """Index of the row slab that wholly contains [y_top, y_bottom], if any."""
    for index in range(len(row_y) - 1):
        if (y_top >= row_y[index] - BAND_EPSILON_PT
                and y_bottom <= row_y[index + 1] + BAND_EPSILON_PT):
            return index
    return None


def _boundary_of(centre: float, row_y: Sequence[float]) -> int | None:
    for index, value in enumerate(row_y):
        if abs(centre - value) <= BAND_EPSILON_PT:
            return index
    return None


def _run_index_of(identifier: str) -> int:
    """Recover the IR run index from a `p<page>t<index>` id."""
    return int(identifier.rsplit("t", 1)[-1])


def _run_order(entry: tuple[str, dict[str, Any], Any]) -> int:
    """Sort band text by IR run index, not by id string ('t9' < 't21')."""
    return _run_index_of(entry[0])


def _modal_index(groups: dict[int, list[Rect]], limit: int) -> int:
    """The lowest index whose relative rule signature is the most common one.

    Rows beyond the official capacity have no measured geometry, so they are
    stamped from a template. Taking the modal row rather than row 0 matters:
    the first and last rows of a band are frequently irregular (a column rule
    that starts above the band, a heavier closing rule), and stamping an
    irregular row would make every overflow row wrong in the same way.
    """
    signatures: dict[tuple, list[int]] = {}
    for index in range(limit):
        rects = groups.get(index, [])
        signature = tuple(sorted((round(r.x, 3), round(r.w, 3), round(r.h, 3), r.fill)
                                 for r in rects))
        signatures.setdefault(signature, []).append(index)
    if not signatures:
        return 0
    best = max(sorted(signatures.items(), key=lambda kv: kv[1][0]), key=lambda kv: len(kv[1]))
    return best[1][0]


def build_band_plan(band: dict[str, Any], page_ir: dict[str, Any],
                    cells_by_id: dict[str, dict[str, Any]]) -> BandPlan:
    plan = BandPlan(band, int(page_ir["index"]))
    row_y = plan.row_y
    top, bottom = row_y[0], row_y[-1]
    x_low = float(band["x0"]) - 2.0
    x_high = float(band["x1"]) + 2.0

    for rule in page_ir["rules"]:
        if rule["x0"] < x_low or rule["x1"] > x_high:
            continue
        rect = Rect.from_box(rule, rule["id"])
        if rule["axis"] == "h":
            centre = (rule["y0"] + rule["y1"]) / 2.0
            boundary = _boundary_of(centre, row_y)
            if boundary is not None:
                plan.boundary_rules.setdefault(boundary, []).append(rect)
                plan.rule_ids.add(rule["id"])
                continue
            row = _row_of(rule["y0"], rule["y1"], row_y)
            if row is not None:
                plan.rules_by_row.setdefault(row, []).append(rect)
                plan.rule_ids.add(rule["id"])
            continue
        # Vertical: full-height verticals stretch, row-local verticals repeat,
        # and anything that crosses only *some* rows is left in the static
        # layer -- it is not part of the repeating unit and inventing a growth
        # rule for it would be a guess.
        if rule["y0"] <= top + BAND_EPSILON_PT and rule["y1"] >= bottom - BAND_EPSILON_PT:
            plan.spanning_rules.append((rect, rule["y0"] - top, rule["y1"] - bottom))
            plan.rule_ids.add(rule["id"])
            continue
        row = _row_of(rule["y0"], rule["y1"], row_y)
        if row is not None:
            plan.rules_by_row.setdefault(row, []).append(rect)
            plan.rule_ids.add(rule["id"])

    rows_seen = sorted({cells_by_id[cid]["row"] for cid in band["cell_ids"]})
    row_number = {value: index for index, value in enumerate(rows_seen)}
    for cid in band["cell_ids"]:
        cell = cells_by_id[cid]
        index = row_number[cell["row"]]
        plan.cells_by_row.setdefault(index, []).append(cell)
        plan.cell_ids.add(cid)
        for rid in cell["text_run_ids"]:
            plan.run_ids.add(rid)
            plan.texts_by_row.setdefault(index, []).append((rid, cell, None))

    plan.template_row = _modal_index(plan.rules_by_row, plan.capacity)
    plan.template_boundary = _modal_index(plan.boundary_rules, plan.capacity + 1)

    roles = band.get("column_roles") or []
    columns = band.get("column_x") or []
    for index, role in enumerate(roles):
        if role == "enumerated" and index + 1 < len(columns):
            plan.ordinal_column = (float(columns[index]), float(columns[index + 1]))
            break
    return plan


def band_rects(plan: BandPlan, rows: int) -> list[Rect]:
    """Every rule of the band for `rows` rendered rows, in paint order."""
    row_y = plan.row_y
    capacity = plan.capacity
    pitch = float(plan.band["row_pitch_pt"])

    def y_at(index: int) -> float:
        if index < len(row_y):
            return row_y[index]
        return row_y[-1] + (index - (len(row_y) - 1)) * pitch

    out: list[Rect] = []
    for index in range(rows):
        if index < capacity:
            out.extend(plan.rules_by_row.get(index, []))
        else:
            base = plan.rules_by_row.get(plan.template_row, [])
            delta = y_at(index) - row_y[plan.template_row]
            out.extend(rect.shifted(delta) for rect in base)
    for index in range(rows + 1):
        if index < len(row_y):
            out.extend(plan.boundary_rules.get(index, []))
        else:
            base = plan.boundary_rules.get(plan.template_boundary, [])
            delta = y_at(index) - row_y[plan.template_boundary]
            out.extend(rect.shifted(delta) for rect in base)
    for rect, d_top, d_bottom in plan.spanning_rules:
        y0 = row_y[0] + d_top
        y1 = y_at(rows) + d_bottom
        out.append(Rect(rect.x, y0, rect.w, y1 - y0, rect.fill, rect.source_id))
    return out


def band_json(plan: BandPlan, rendered_rows: int, styles: dict[tuple[int, int], RunStyle],
              runs_by_id: dict[str, dict[str, Any]],
              fields: FieldPlan | None = None) -> dict[str, Any]:
    """The band as data: what the JS renderer needs to reproduce any row count.

    Rows within capacity ship their measured geometry so a re-render at the
    official row count is exact by construction rather than by arithmetic.
    """
    row_y = plan.row_y
    rows = []
    for index in range(plan.capacity):
        texts = []
        for rid, cell, _ in sorted(plan.texts_by_row.get(index, []), key=_run_order):
            run = runs_by_id[rid]
            key = (plan.page_index, _run_index_of(rid))
            role = None
            if plan.ordinal_column and plan.ordinal_column[0] <= cell["x0"] < plan.ordinal_column[1]:
                role = "enumerated"
            texts.append(text_json(run, rid, styles[key], index, role))
        rows.append({
            "index": index,
            "y": round(row_y[index], 4),
            "rules": [r.to_json() for r in plan.rules_by_row.get(index, [])],
            "cells": [cell_json(c, fields)
                      for c in sorted(plan.cells_by_row.get(index, []),
                                      key=lambda c: (c["x0"], c["id"]))],
            "texts": texts,
        })
    return {
        "id": plan.band["id"],
        "page": plan.page_index,
        "kind": plan.band["kind"],
        "capacity": plan.capacity,
        "rendered_rows": rendered_rows,
        "row_pitch_pt": plan.band["row_pitch_pt"],
        "row_y": [round(v, 4) for v in row_y],
        "template_row": plan.template_row,
        "template_boundary": plan.template_boundary,
        "ordinal_column": ([round(v, 4) for v in plan.ordinal_column]
                           if plan.ordinal_column else None),
        "boundaries": [[r.to_json() for r in plan.boundary_rules.get(i, [])]
                       for i in range(plan.capacity + 1)],
        "spanning": [{"rect": rect.to_json(), "d_top": round(d_top, 4),
                      "d_bottom": round(d_bottom, 4)}
                     for rect, d_top, d_bottom in plan.spanning_rules],
        "rows": rows,
    }


def band_template_markup(plan: BandPlan, blob_index: int,
                         fields: FieldPlan | None = None) -> str:
    """One row of markup, emitted once, cloned by the renderer.

    The template is what makes this a generated form rather than a traced
    picture: the sheet's official row count is data, not structure, so the same
    document renders 3 rows or 6 and the borders grow with them.

    Its cell geometry is stated relative to the row's own top edge, because the
    template describes the *shape* of a row. Where a row actually sits, and the
    small per-row deviations from a nominal pitch, come from the measured row
    data the renderer overlays onto the clone.
    """
    cells = sorted(plan.cells_by_row.get(plan.template_row, []),
                   key=lambda c: (c["x0"], c["id"]))
    row_top = plan.row_y[plan.template_row]
    parts = []
    for cell in cells:
        relative = dict(cell)
        relative["y0"] = cell["y0"] - row_top
        relative["y1"] = cell["y1"] - row_top
        comb = cell.get("comb")
        if comb:
            relative["comb"] = dict(comb)
            # Every absolute y this comb publishes, moved together. The writing
            # rectangle is the one `comb_writing_rect` lays the slots out from,
            # so leaving it absolute here while `y0` moves would put a template
            # row's compartments a page-height away from the row.
            for key in ("y0", "y1", "writing_y0", "writing_y1"):
                if key in comb:
                    relative["comb"][key] = float(comb[key]) - row_top
        parts.append(cell_markup(relative, fields, id_attribute="data-cell-id"))
    return (f'<template id="band-template-{esc_attr(plan.band["id"])}" '
            f'data-band="{esc_attr(plan.band["id"])}" '
            f'data-band-index="{blob_index}" '
            f'data-capacity="{plan.capacity}" '
            f'data-row-pitch="{fmt(plan.band["row_pitch_pt"])}" '
            f'data-row-y="{esc_attr(",".join(fmt(v) for v in plan.row_y))}" '
            f'data-template-row="{plan.template_row}">{"".join(parts)}</template>')


# ---------------------------------------------------------------------------
# Assets
# ---------------------------------------------------------------------------


def image_markup(image: dict[str, Any], backend: RuleBackend, options: "Options",
                 warnings: list[str]) -> str:
    """The official raster at its exact rect, addressed by content hash.

    A missing asset is a warning and an empty rect of the exact size, never a
    hard failure and never a substitute drawing: emitting visible placeholder
    ink would put ink in the round-trip that the official form does not have,
    turning a missing file into a fake geometry failure.
    """
    name = f"{image['sha256']}.{image.get('ext') or 'png'}"
    assets_dir = options.assets_dir
    href = f"{assets_dir.rstrip('/')}/{name}" if assets_dir else name
    present = (options.out_dir is None
               or (options.out_dir / assets_dir / name).is_file())
    if not present:
        warnings.append(
            f"missing asset {href}: emitting a transparent placeholder rect at the exact "
            f"rect ({fmt(image['x0'])},{fmt(image['y0'])}) "
            f"{fmt(image['x1'] - image['x0'])}x{fmt(image['y1'] - image['y0'])}pt; the "
            f"round-trip will report this image as missing, which is the truth")
    return backend.image(image, href, present)


# ---------------------------------------------------------------------------
# Splitting the sheet into a form document and a guide document
# ---------------------------------------------------------------------------


class PageSplit:
    """Which of one page's elements the document being emitted may carry.

    guides.py claims an element for the guide only when it lies *wholly* below
    the cut. What crosses the cut is not awarded to either side any more: it is
    **clipped**, and the plan carries both pieces. Awarding straddlers to the
    form was chosen so a cut could never lose a rule and had the opposite
    failure -- 1600-PT kept two 461pt verticals with nothing between them, an
    empty three-sided box down two thirds of the page. So a straddler is carried
    by both documents, each drawing its own piece, and `clipped` is what
    substitutes the piece for the whole element.

    Paths are split here rather than in the plan. guides.py predates the IR's
    `paths` and claims no path ids, so the same rule it applies to every other
    element -- wholly below the cut belongs to the guide -- is applied to paths
    directly. It is not a cosmetic detail: 532 of 0605's 584 paths are on the
    page whose whole content is guide material, and drawing them on the form
    would put 532 marks on a sheet that has nothing else left on it.
    """

    __slots__ = ("guide_side", "rule_ids", "cell_ids", "run_indices",
                 "fill_indices", "image_indices", "path_ids", "pieces")

    def __init__(self, guide_side: bool, rule_ids: frozenset[str] = frozenset(),
                 cell_ids: frozenset[str] = frozenset(),
                 run_indices: frozenset[int] = frozenset(),
                 fill_indices: frozenset[int] = frozenset(),
                 image_indices: frozenset[int] = frozenset(),
                 path_ids: frozenset[str] = frozenset(),
                 pieces: dict[tuple[str, str], dict[str, Any]] | None = None) -> None:
        self.guide_side = guide_side
        self.rule_ids = rule_ids
        self.cell_ids = cell_ids
        self.run_indices = run_indices
        self.fill_indices = fill_indices
        self.image_indices = image_indices
        self.path_ids = path_ids
        # (kind, ref) -> this side's clipped geometry, for straddlers only.
        self.pieces = dict(pieces or {})

    def _keep(self, ref: Any, claimed: frozenset) -> bool:
        return (ref in claimed) if self.guide_side else (ref not in claimed)

    def keep_rule(self, rule_id: str) -> bool:
        return ("rule", rule_id) in self.pieces or self._keep(rule_id, self.rule_ids)

    def keep_cell(self, cell_id: str) -> bool:
        return ("cell", cell_id) in self.pieces or self._keep(cell_id, self.cell_ids)

    def keep_run(self, run_index: int) -> bool:
        return self._keep(run_index, self.run_indices)

    def keep_fill(self, index: int) -> bool:
        return (("area_fill", f"#{index}") in self.pieces
                or self._keep(index, self.fill_indices))

    def keep_image(self, index: int) -> bool:
        return (("image", f"#{index}") in self.pieces
                or self._keep(index, self.image_indices))

    def keep_path(self, path_id: str) -> bool:
        return self._keep(path_id, self.path_ids)

    def clipped(self, item: dict[str, Any], kind: str, ref: str) -> dict[str, Any]:
        """`item` with this side's box, when the cut passes through it.

        Only the box is replaced. Tone, role and paint order are properties of
        the ink and not of where it was cut, and a clipped rule is still the
        same rule -- guides.py recomputes `length_pt` per piece for exactly that
        reason, so that the round-trip differ is given the geometry the document
        actually draws.
        """
        piece = self.pieces.get((kind, ref))
        if piece is None:
            return item
        merged = dict(item)
        merged.update({key: piece[key] for key in ("x0", "y0", "x1", "y1")
                       if key in piece})
        if kind == "cell":
            # A band that fell on the other side of the cut is not this piece's
            # to render, and a cut may not pass through one (guides.py refuses
            # such a cut), so the count is 0 or all of them.
            if "combs" in piece and not piece["combs"]:
                merged.pop("comb", None)
            if "text_run_ids" in piece:
                merged["text_run_ids"] = list(piece["text_run_ids"])
        return merged

    def without_band(self, rule_ids: Iterable[str], cell_ids: Iterable[str],
                     run_indices: Iterable[int]) -> "PageSplit":
        """Unclaim everything a growable band owns: a band is always the form's.

        A band regenerates its own rules, cells and text from one blueprint, so
        it cannot hand half of itself to another document without the renderer
        stamping rows that are missing their geometry. Awarding the whole band
        to the form is the same trade the straddler rule makes -- a duplicated
        line on the guide is cosmetic, a band with a hole in it is not. In the
        current corpus no band overlaps a guide region at all, so this is a
        guard rather than a behaviour; --self-test asserts that stays true.
        """
        return PageSplit(self.guide_side,
                         self.rule_ids - frozenset(rule_ids),
                         self.cell_ids - frozenset(cell_ids),
                         self.run_indices - frozenset(run_indices),
                         self.fill_indices, self.image_indices, self.path_ids,
                         self.pieces)


WHOLE_PAGE = PageSplit(guide_side=False)


class DocumentSplit:
    """The guide plan, joined onto one form's IR and read from one side.

    Constructed once per document. With no plan the form side keeps everything
    and the guide side is empty, which is what makes `--document form` without
    `--guide-plan` byte-identical to what this module emitted before splitting
    existed.
    """

    __slots__ = ("document", "plan", "_pages")

    def __init__(self, plan: dict[str, Any] | None, ir: dict[str, Any],
                 document: str) -> None:
        if document not in ("form", "guide"):
            raise SystemExit(f"unknown document {document!r}")
        self.document = document
        self.plan = plan
        self._pages: dict[int, PageSplit] = {}
        if plan is None:
            if document == "guide":
                raise SystemExit("--document guide needs --guide-plan")
            return

        form = plan.get("form") or {}
        if (form.get("code"), form.get("revision")) != (ir["form"]["code"],
                                                        ir["form"]["revision"]):
            raise SystemExit(
                f"guide plan is for {form.get('code')}-{form.get('revision')} "
                f"but the IR is {ir['form']['code']}-{ir['form']['revision']}")

        by_index = {int(page["index"]): page for page in ir["pages"]}
        side = "guide" if document == "guide" else "form"
        for entry in plan.get("inline", []):
            index = int(entry["page"])
            page = by_index.get(index)
            if page is None:
                raise SystemExit(f"guide plan claims page {index}, which the IR has not")
            claimed = PageSplit(
                guide_side=(document == "guide"),
                rule_ids=frozenset(entry["rule_ids"]),
                cell_ids=frozenset(entry["cell_ids"]),
                run_indices=frozenset(int(i) for i in entry["text_run_indices"]),
                fill_indices=frozenset(int(i) for i in entry["area_fill_indices"]),
                image_indices=frozenset(int(i) for i in entry["image_indices"]),
                path_ids=_claimed_paths(page, float(entry["cut_y_pt"])),
                pieces={(s["kind"], s["ref"]): s[side]
                        for s in entry.get("straddlers", ())
                        if s.get("disposition") == "clipped" and s.get(side)},
            )
            _validate_claims(claimed, page, index)
            self._pages[index] = claimed

    def page(self, page_index: int) -> PageSplit:
        default = PageSplit(guide_side=(self.document == "guide"))
        return self._pages.get(int(page_index), default)

    @property
    def guide_pages(self) -> list[int]:
        return sorted(self._pages)

    @property
    def has_guide(self) -> bool:
        return bool(self._pages) or bool(self.standalone_pdfs)

    @property
    def guide_side(self) -> bool:
        return self.document == "guide"

    @property
    def standalone_pdfs(self) -> list[str]:
        return list((self.plan or {}).get("standalone_pdfs") or [])


def _claimed_paths(page: dict[str, Any], cut_y: float) -> frozenset[str]:
    """The path ids that lie wholly below the cut, i.e. the guide's.

    The same test guides.py applies to rules, fills and images. A path that
    *crosses* the cut would need clipping, which is a geometry operation on
    Bezier segments rather than on a box; none occurs in this corpus, and one
    that did would stay on the form -- losing a mark off the form is worse than
    duplicating it -- and say so through `--self-test`.
    """
    return frozenset(str(path["id"]) for path in page.get("paths", ())
                     if float(path["y0"]) >= cut_y)


def straddling_paths(page: dict[str, Any], cut_y: float) -> list[str]:
    return [str(path["id"]) for path in page.get("paths", ())
            if float(path["y0"]) < cut_y < float(path["y1"])]


def _validate_claims(split: PageSplit, page: dict[str, Any], index: int) -> None:
    """A claim on something the page does not have is a stale plan, not a split."""
    known_rules = {rule["id"] for rule in page["rules"]}
    for label, claimed, universe in (
            ("rule", split.rule_ids, known_rules),
            ("text run", split.run_indices, set(range(len(page["text_runs"])))),
            ("area fill", split.fill_indices, set(range(len(page["area_fills"])))),
            ("image", split.image_indices, set(range(len(page["images"]))))):
        unknown = sorted(claimed - universe, key=str)[:3]
        if unknown:
            raise SystemExit(
                f"guide plan claims {label}(s) {unknown} on page {index}, which the "
                f"IR does not have; the plan was built from a different extraction")


# ---------------------------------------------------------------------------
# Guide reflow
# ---------------------------------------------------------------------------
#
# The guide document does not need parity and cannot usefully have it: 1603Q's
# guideline block is two columns of 6pt prose, and reproducing it as absolutely
# positioned runs is what makes those columns overlap on screen. Reflowing is
# the fix that cannot regress -- the runs stop carrying coordinates at all, so
# there is nothing left to overlap.
#
# Everything below is a heuristic, and is labelled as one in the output
# (`data-flow` on each section). None of it is allowed anywhere near the form
# document.

# x is binned at 1pt to find gutters. Finer resolution buys nothing: the
# narrowest real gutter in the corpus is 4pt (2200C) and the coarsest text is
# 6pt, so a bin below a point only splits glyph-level noise.
GUTTER_BIN_PT = 1.0

# A bin is "empty" when this few runs cover it. It is a fraction of the page's
# own peak coverage rather than an absolute count, because a 40-run guide
# region (2000-OT) and a 213-run one (2551M) cannot share a constant. Measured:
# real gutters sit at 4-8% of peak, prose interiors at 70-100%.
GUTTER_COVERAGE_FRACTION = 0.12
MIN_GUTTER_PT = 4.0

# A column narrower than this is not a column; it is a stray run sitting in a
# gutter (2200C, 2551M) or one cell of a table (1600-PT, 2550M). It is
# dissolved into whichever neighbour it is separated from by the *narrower*
# gutter, which is the neighbour it was most likely part of.
MIN_COLUMN_FRACTION = 0.15

# Two runs are on one line when their baselines are within this. The tightest
# real leading in a guide region is 5.16pt (1600-VT) and the closest pair of
# *different* lines that must not merge is 0.48pt apart in different columns
# (2550M) -- but those are separated by column first, so within a column the
# margin is comfortable.
LINE_BASELINE_TOLERANCE_PT = 2.0

# A run overlapping more than one column by more than this spans them.
COLUMN_OVERLAP_PT = 2.0

# Prose vs table, measured as the median fraction of a column's width that a
# line actually puts ink on -- summed run widths, not the line's extent, so a
# rate column and an ATC column sharing a baseline are not mistaken for one
# full-width line. Across the 17 guide regions this separates cleanly: the two
# ATC tables score 0.36-0.55 per column, the thirteen prose regions 0.61-0.95.
# The threshold sits in that gap; if a future form lands between 0.55 and 0.61
# the classification is a coin toss and the region should be looked at.
PROSE_INK_FILL = 0.60

# Paragraph breaks. A gap this much larger than the column's own median line
# gap is a new block; a previous line ending this far short of the column's
# right edge is a finished paragraph.
PARAGRAPH_GAP_FACTOR = 1.6
SHORT_LINE_FRACTION = 0.15

# A wholly bold line narrower than this fraction of its column is a heading.
HEADING_WIDTH_FRACTION = 0.6

# Runs are glyph runs, not words: they are concatenated unless the source left
# a gap wider than this fraction of the font size, which is a space.
WORD_GAP_FRACTION = 0.15

LIST_MARKER = re.compile(r"^\s*(?:\(?\d{1,2}[.)]|\(?[A-Za-z][.)]|[•▪·*‐-]\s)\s*")


def _coverage_gutters(runs: Sequence[dict[str, Any]], x0: float, x1: float
                      ) -> list[tuple[float, float]]:
    """x intervals across which almost nothing is printed."""
    bins = max(1, int((x1 - x0) / GUTTER_BIN_PT) + 1)
    coverage = [0] * bins
    for run in runs:
        low = max(0, int((float(run["x0"]) - x0) / GUTTER_BIN_PT))
        high = min(bins - 1, int((float(run["x1"]) - x0) / GUTTER_BIN_PT))
        for index in range(low, high + 1):
            coverage[index] += 1
    threshold = max(1.0, GUTTER_COVERAGE_FRACTION * max(coverage))

    gutters: list[tuple[float, float]] = []
    index = 0
    while index < bins:
        if coverage[index] > threshold:
            index += 1
            continue
        end = index
        while end < bins and coverage[end] <= threshold:
            end += 1
        low, high = x0 + index * GUTTER_BIN_PT, x0 + end * GUTTER_BIN_PT
        if high - low >= MIN_GUTTER_PT and index > 0 and end < bins:
            gutters.append((low, high))
        index = end
    return gutters


def _dissolve_narrow(columns: list[tuple[float, float]],
                     gutters: list[tuple[float, float]],
                     width: float) -> list[tuple[float, float]]:
    """Merge away columns too narrow to be one, narrowest gutter first."""
    columns = list(columns)
    gutters = list(gutters)
    while len(columns) > 1:
        widths = [high - low for low, high in columns]
        index = min(range(len(columns)), key=widths.__getitem__)
        if widths[index] >= MIN_COLUMN_FRACTION * width:
            break
        left = gutters[index - 1][1] - gutters[index - 1][0] if index > 0 else None
        right = gutters[index][1] - gutters[index][0] if index < len(gutters) else None
        drop = index if left is None or (right is not None and right < left) else index - 1
        columns[drop:drop + 2] = [(columns[drop][0], columns[drop + 1][1])]
        gutters.pop(drop)
    return columns


def _column_bands(runs: Sequence[dict[str, Any]]
                  ) -> tuple[list[tuple[float, float]], list[tuple[float, float]]]:
    """(grid, flow) column bands: the raw gutter grid, and the dissolved one.

    Both are needed. The grid is the table geometry a tabular region is laid out
    on; the flow columns are the reading columns of a prose region, which is the
    grid with its slivers merged away.
    """
    x0 = min(float(run["x0"]) for run in runs)
    x1 = max(float(run["x1"]) for run in runs)
    gutters = _coverage_gutters(runs, x0, x1)
    grid: list[tuple[float, float]] = []
    cursor = x0
    for low, high in gutters:
        grid.append((cursor, low))
        cursor = high
    grid.append((cursor, x1))
    return grid, _dissolve_narrow(grid, gutters, x1 - x0)


def _is_set_fragment(run: dict[str, Any], other: dict[str, Any]) -> bool:
    """Whether these two runs are one printed line, proven by ink and not by a
    tolerance.

    A raised or dropped fragment -- a superscript ordinal, an exponent, a
    footnote mark -- is set on its OWN baseline, so the baseline window cannot
    see that it belongs to the text it is set against. 1702Q page 3 writes
    `(4th)` as `...the fourth (4`, a 4.56pt `th) ` 2.04pt higher, and
    `taxable year (whether `; at 0.04pt outside a 2.00pt window the fragment
    became a line of its own and the sentence was published as
    `...is imposed upon th)` and `...the fourth (4 taxable year` (finding F060).

    The proof used instead adds no constant of its own. One run's ink band lies
    strictly INSIDE the other's -- which two runs of the SAME size on different
    baselines can never do, so this can never merge two ordinary lines -- and
    the two ABUT across the page: the distance between their nearer edges is no
    more than `_line_pieces` already reads as a word space, in either direction,
    because adjacent glyph runs' advance boxes overlap by hundredths of a point.
    Measured over the corpus that separation is not close: every one of the 32
    fragments the baseline window strands abuts its text within [-0.12, +0.31]pt,
    while 2553's `BIR Form No.` -- a genuinely different line that merely sits
    inside the tall title's box -- overlaps it by 4.60pt and is refused.
    """
    tall, small = (other, run) if (
        float(other["y1"]) - float(other["y0"])
        > float(run["y1"]) - float(run["y0"])) else (run, other)
    if not (float(tall["y0"]) <= float(small["y0"])
            and float(small["y1"]) <= float(tall["y1"])
            and float(tall["y1"]) - float(tall["y0"])
            > float(small["y1"]) - float(small["y0"])):
        return False
    gap = max(float(run["x0"]), float(other["x0"])) - min(
        float(run["x1"]), float(other["x1"]))
    return abs(gap) <= WORD_GAP_FRACTION * min(float(run["size_pt"]),
                                               float(other["size_pt"]))


def _group_lines(runs: Sequence[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Runs grouped into lines by baseline, each line ordered left to right.

    The anchor is the line's first baseline rather than a running mean, so a
    column of tightly leaded rows cannot drift a line's tolerance downward until
    it swallows the next one.

    A run the window would strand -- set INSIDE the ink of another
    (`_is_set_fragment`) and further from every such owner's baseline than the
    window reaches -- never anchors a line and is not grouped by its own
    baseline at all. It is set aside, the rest is grouped exactly as before, and
    each stranded fragment then joins the line holding the run it is set
    against. Only a stranded one is diverted, so this adds to the grouping and
    changes nothing the window already got right.

    The attachment is a second pass rather than a branch inside the walk because
    the walk runs in reading order ACROSS columns: 1702Q's `th` is followed by
    the next column's run on the body baseline, which would open the line before
    the body run that owns the fragment ever arrives.

    A fragment whose owners are not all on one body line keeps its own line,
    which is what this function did for every run before: no guess replaces a
    measurement that came out ambiguous.
    """
    owners: dict[int, list[dict[str, Any]]] = {}
    body: list[dict[str, Any]] = []
    for run in runs:
        set_against = [other for other in runs
                       if other is not run and _is_set_fragment(run, other)
                       and float(other["y1"]) - float(other["y0"])
                       > float(run["y1"]) - float(run["y0"])]
        if set_against and all(
                abs(float(other["baseline_y"]) - float(run["baseline_y"]))
                > LINE_BASELINE_TOLERANCE_PT for other in set_against):
            owners[id(run)] = set_against
        else:
            body.append(run)

    lines: list[list[dict[str, Any]]] = []
    anchor = None
    for run in sorted(body, key=lambda r: (float(r["baseline_y"]), float(r["origin_x"]))):
        baseline = float(run["baseline_y"])
        if anchor is None or baseline - anchor > LINE_BASELINE_TOLERANCE_PT:
            anchor = baseline
            lines.append([])
        lines[-1].append(run)

    of_body = {id(member): index for index, line in enumerate(lines)
               for member in line}
    for run in runs:
        set_against = owners.get(id(run))
        if set_against is None:
            continue
        found = [of_body[id(other)] for other in set_against
                 if id(other) in of_body]
        if len(found) == len(set_against) and len(set(found)) == 1:
            lines[found[0]].append(run)
        else:
            lines.append([run])
    lines.sort(key=lambda line: min(float(r["baseline_y"]) for r in line))
    return [sorted(line, key=lambda r: float(r["origin_x"])) for line in lines]


def _line_baseline(line: Sequence[dict[str, Any]]) -> float:
    return min(float(run["baseline_y"]) for run in line)


def _line_ink(line: Sequence[dict[str, Any]]) -> float:
    return sum(float(run["x1"]) - float(run["x0"]) for run in line)


def _median(values: Sequence[float]) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2.0


def _column_of(run: dict[str, Any], columns: Sequence[tuple[float, float]]) -> int | None:
    """The single column this run lives in, or None if it spans several."""
    hits = [index for index, (low, high) in enumerate(columns)
            if min(float(run["x1"]), high) - max(float(run["x0"]), low) > COLUMN_OVERLAP_PT]
    if len(hits) == 1:
        return hits[0]
    if hits:
        return None
    centre = (float(run["x0"]) + float(run["x1"])) / 2.0
    return min(range(len(columns)),
               key=lambda i: abs(centre - (columns[i][0] + columns[i][1]) / 2.0))


def _is_prose(runs: Sequence[dict[str, Any]],
              columns: Sequence[tuple[float, float]]) -> bool:
    if len(columns) < 2:
        return False
    for index, (low, high) in enumerate(columns):
        own = [run for run in runs if _column_of(run, columns) == index]
        lines = _group_lines(own)
        if not lines:
            return False
        if _median([_line_ink(line) / (high - low) for line in lines]) < PROSE_INK_FILL:
            return False
    return True


def _line_pieces(line: Sequence[dict[str, Any]]) -> list[tuple[str, Any]]:
    """One line's runs as (text, colour) pieces, spaced where the source was.

    The colour travels with the text because the source's colour is content:
    1600-PT and 1600-VT each carry 25 runs at 0xFFFFFF -- BIR reviewer initials,
    invisible on the official's white paper -- inside the ATC table the guide
    relocates. The form document has emitted every run's own colour all along;
    the reflowed guide dropped it and published those initials in black, where
    they read as ATC data. So the colour is carried here too, and white runs are
    *not* filtered out: they are in the document, and a form may legitimately
    set white text over a dark band.
    """
    pieces: list[tuple[str, Any]] = []
    previous: dict[str, Any] | None = None
    for run in line:
        if previous is not None:
            gap = float(run["x0"]) - float(previous["x1"])
            if gap > WORD_GAP_FRACTION * float(run["size_pt"]):
                pieces.append((" ", previous.get("color")))
        pieces.append((run["text"], run.get("color")))
        previous = run
    return pieces


def _line_text(line: Sequence[dict[str, Any]]) -> str:
    """One line's runs concatenated, with a space wherever the source left one."""
    return "".join(text for text, _colour in _line_pieces(line))


def _render_pieces(pieces: Sequence[tuple[str, Any]]) -> str:
    """Escaped markup for one block: whitespace collapsed, colour preserved.

    Whitespace is collapsed exactly as `" ".join(text.split())` collapses it --
    this is a reading document and the source's line breaks are not its own --
    and a colour is stated only where it is not the black the stylesheet already
    sets, so a block whose every run is black emits the bytes it emitted before
    colour was carried at all.
    """
    chars: list[tuple[str, Any]] = []
    space = False
    for text, colour in pieces:
        for char in text:
            if char.isspace():
                space = True
                continue
            if space and chars:
                # The space keeps the colour of the text *before* it, so a black
                # word followed by a white one does not extend the white span
                # backwards over the gap.
                chars.append((" ", chars[-1][1]))
            space = False
            chars.append((char, colour))
    out: list[str] = []
    index = 0
    while index < len(chars):
        colour = chars[index][1]
        stop = index
        while stop < len(chars) and chars[stop][1] == colour:
            stop += 1
        body = esc_text("".join(char for char, _colour in chars[index:stop]))
        css = text_color(colour)
        out.append(body if css == "#000000"
                   else f'<span style="color:{css}">{body}</span>')
        index = stop
    return "".join(out)


def _is_heading(line: Sequence[dict[str, Any]], column_width: float) -> bool:
    return (all(run.get("bold") for run in line)
            and _line_ink(line) < HEADING_WIDTH_FRACTION * column_width
            and bool(_line_text(line).strip()))


def _blocks_of_lines(lines: Sequence[Sequence[dict[str, Any]]],
                     column_width: float, heading_tag: str) -> list[tuple[str, str]]:
    """One column's lines as (tag, markup) blocks: headings and paragraphs."""
    baselines = [_line_baseline(line) for line in lines]
    gaps = [b - a for a, b in zip(baselines, baselines[1:])]
    typical = _median(gaps) or 1.0
    right_edge = max((max(float(r["x1"]) for r in line) for line in lines), default=0.0)

    out: list[tuple[str, str]] = []
    buffer: list[tuple[str, Any]] = []

    def flush() -> None:
        if buffer:
            out.append(("p", _render_pieces(buffer)))
            buffer.clear()

    previous: Sequence[dict[str, Any]] | None = None
    for index, line in enumerate(lines):
        text = _line_text(line)
        if not text.strip():
            continue
        if _is_heading(line, column_width):
            flush()
            out.append((heading_tag, _render_pieces(_line_pieces(line))))
            previous = line
            continue
        if previous is not None:
            gap = baselines[index] - baselines[index - 1]
            ended_short = (right_edge - max(float(r["x1"]) for r in previous)
                           > SHORT_LINE_FRACTION * column_width)
            if (gap > PARAGRAPH_GAP_FACTOR * typical
                    or ended_short
                    or _is_heading(previous, column_width)
                    or LIST_MARKER.match(text)):
                flush()
        if buffer:
            buffer.append((" ", None))
        buffer.extend(_line_pieces(line))
        previous = line
    flush()
    return out


def _prose_markup(runs: Sequence[dict[str, Any]],
                  columns: Sequence[tuple[float, float]]) -> str:
    """Multi-column prose in reading order: down a column, then the next.

    A run overlapping several columns is a full-width line and splits the page
    into blocks, so a heading that spans the columns is emitted where it belongs
    rather than being dragged into whichever column it happens to start in.
    Anything sharing a baseline with such a run joins it, which is what keeps
    1603Q's "[January 2018 (ENCS)]" attached to its title instead of opening the
    right-hand column.
    """
    spanning = [run for run in runs if _column_of(run, columns) is None]
    spanning_baselines = sorted({float(run["baseline_y"]) for run in spanning})

    def spans(run: dict[str, Any]) -> bool:
        baseline = float(run["baseline_y"])
        return any(abs(baseline - value) <= LINE_BASELINE_TOLERANCE_PT
                   for value in spanning_baselines)

    spanning_lines = _group_lines([run for run in runs if spans(run)])
    column_runs: dict[int, list[dict[str, Any]]] = {i: [] for i in range(len(columns))}
    for run in runs:
        if spans(run):
            continue
        column_runs[_column_of(run, columns)].append(run)

    cuts = [_line_baseline(line) for line in spanning_lines]
    width = max(high - low for low, high in columns)

    def block_of(baseline: float) -> int:
        return sum(1 for cut in cuts if baseline > cut + LINE_BASELINE_TOLERANCE_PT)

    per_block: dict[int, dict[int, list[list[dict[str, Any]]]]] = {}
    for index, runs_here in column_runs.items():
        for line in _group_lines(runs_here):
            block = block_of(_line_baseline(line))
            per_block.setdefault(block, {}).setdefault(index, []).append(line)

    parts: list[str] = []

    def emit_block(block: int) -> None:
        for index in range(len(columns)):
            lines = per_block.get(block, {}).get(index)
            if not lines:
                continue
            column_width = columns[index][1] - columns[index][0]
            parts.append(f'<div class="gl-col" data-column="{index}">')
            for tag, markup in _blocks_of_lines(lines, column_width, "h3"):
                parts.append(f"<{tag}>{markup}</{tag}>")
            parts.append("</div>")

    emit_block(0)
    for index, line in enumerate(spanning_lines):
        for tag, markup in _blocks_of_lines([line], width, "h2"):
            parts.append(f"<{tag}>{markup}</{tag}>")
        emit_block(index + 1)
    return "".join(parts)


def _table_markup(runs: Sequence[dict[str, Any]],
                  grid: Sequence[tuple[float, float]]) -> str:
    """A tabular guide region as a real table: one row per baseline, cells on the grid.

    Reading order for a table is row-major, and column-major flow would list
    every ATC code and then every description. The grid is the *undissolved*
    gutter geometry, which is exactly the table's own column structure --
    2550M's nine bands are its three repeated (index, industry, ATC) triples.
    """
    lines = _group_lines(runs)
    if len(grid) < 2:
        parts = ['<div class="gl-col" data-column="0">']
        for tag, markup in _blocks_of_lines(lines, grid[0][1] - grid[0][0] if grid else 1.0,
                                            "h3"):
            parts.append(f"<{tag}>{markup}</{tag}>")
        parts.append("</div>")
        return "".join(parts)

    rows: list[str] = []
    for position, line in enumerate(lines):
        cells: dict[int, tuple[int, list[dict[str, Any]]]] = {}
        for run in line:
            hits = [i for i, (low, high) in enumerate(grid)
                    if min(float(run["x1"]), high) - max(float(run["x0"]), low)
                    > COLUMN_OVERLAP_PT]
            if not hits:
                hits = [_column_of(run, grid) or 0]
            start = hits[0]
            span, existing = cells.get(start, (1, []))
            cells[start] = (max(span, len(hits)), existing + [run])
        tag = "th" if all(run.get("bold") for run in line) and position == 0 else "td"
        body: list[str] = []
        index = 0
        while index < len(grid):
            if index not in cells:
                body.append(f"<{tag}></{tag}>")
                index += 1
                continue
            span, group = cells[index]
            # A colspan may not reach a column that owns a cell of its own.
            # `span` is how many columns the widest run in this cell overlaps,
            # and a run can overlap a column that a *later* run on the same
            # line starts in -- a long description whose last word crosses into
            # the rate column, say. Unclamped, the walk below then steps from
            # this cell straight past that start index and never emits it, so
            # its runs leave the document altogether. That is a text loss, and
            # emit.py's own "carries every run of its own extraction" check is
            # the thing that says so. Narrowing the span is the only correct
            # repair: the printed columns are the printed columns, and two
            # cells cannot occupy one of them.
            span = min(span, min((start for start in cells if start > index),
                                 default=len(grid)) - index)
            attribute = f' colspan="{span}"' if span > 1 else ""
            markup = _render_pieces(
                _line_pieces(sorted(group, key=lambda r: float(r["origin_x"]))))
            body.append(f"<{tag}{attribute}>{markup}</{tag}>")
            index += span
        rows.append(f"<tr>{''.join(body)}</tr>")
    return f'<table class="gl-table">{"".join(rows)}</table>'


def _lattice_rows(cells: Sequence[dict[str, Any]]
                  ) -> list[tuple[float, list[dict[str, Any]]]]:
    """The band's cells grouped into lattice rows, ordered down the page."""
    by_row: dict[Any, list[dict[str, Any]]] = {}
    for cell in cells:
        by_row.setdefault(cell["row"], []).append(cell)
    rows = [(min(float(c["y0"]) for c in group),
             sorted(group, key=lambda c: (float(c["x0"]), c["id"])))
            for group in by_row.values()]
    rows.sort(key=lambda item: (item[0], item[1][0]["id"]))
    return rows


def _column_grid(cells: Sequence[dict[str, Any]]) -> list[float]:
    """The band's column edges: every distinct cell edge, left to right."""
    edges = sorted({round(float(c["x0"]), 2) for c in cells}
                   | {round(float(c["x1"]), 2) for c in cells})
    return edges


def _cells_of_run(run: dict[str, Any], cells: Sequence[dict[str, Any]]
                  ) -> list[dict[str, Any]]:
    """The cells of one lattice row that this run puts ink in, left to right.

    A run frequently spans several cells of its row, because the source draws a
    statutory bracket as one string across the whole row: 1700's
    " Over P 250,000 but not over P 400,000     20% of the excess over P 250,000 "
    is a single run 280pt wide crossing both columns of TABLE 1. It is neither
    split (there is nothing to split it on but a guess) nor dropped into the
    column it happens to cover most, which left the row's description cell empty
    and its rate cell holding both -- the exact pattern C9 is about. It is
    reported as spanning, and the row emits it as one cell with a colspan.
    """
    centre = (float(run["y0"]) + float(run["y1"])) / 2.0
    row = [cell for cell in cells if float(cell["y0"]) <= centre <= float(cell["y1"])]
    hit = [cell for cell in row
           if min(float(run["x1"]), float(cell["x1"]))
           - max(float(run["x0"]), float(cell["x0"])) > COLUMN_OVERLAP_PT]
    if not hit and row:
        best = max(row, key=lambda cell: (
            min(float(run["x1"]), float(cell["x1"]))
            - max(float(run["x0"]), float(cell["x0"])), -float(cell["x0"])))
        overlap = (min(float(run["x1"]), float(best["x1"]))
                   - max(float(run["x0"]), float(best["x0"])))
        hit = [best] if overlap > 0 else []
    return sorted(hit, key=lambda cell: float(cell["x0"]))


def _lattice_table_markup(runs: Sequence[dict[str, Any]],
                          cells: Sequence[dict[str, Any]]) -> str:
    """A relocated table rebuilt on the lattice's own rows and columns.

    This is what keeps a rate attached to its nature of payment. The reflow used
    to lay out one row per printed *line*, so a two-line ATC description stranded
    its rate and code on a row whose description was empty: 1600-PT's guide read
    "Franchise Tax on radio & TV broadcasting companies whose annual gross
    receipts do not exceed P10M &" with no rate, and then a row containing only
    "3% | WB 050". A reader can attach that rate to the wrong nature of payment,
    which is the one defect in the 51-form review that is a correctness hazard
    rather than an appearance one.

    lattice.py already knows the row structure -- the table's own ruled grid --
    so a row here is a lattice row and a cell holds every line of text that falls
    inside it, however many lines that is.

    Returns "" -- meaning "the caller should lay this section out from its lines"
    -- unless the lattice describes *all* of the band's ink. That is an all-or-
    nothing condition rather than a threshold, and the corpus separates on it
    with nothing in between: 12 of the 14 ruled bands put every claimed run in a
    cell, and the two that do not (0605 p2 and 2550M p3, both 0.67) would come
    out as a structured table with a third of its content stranded in full-width
    rows between the rows it belongs to, which is less readable than the uniform
    line layout, not more.
    """
    rows = _lattice_rows(cells)
    edges = _column_grid(cells)
    if len(rows) < 2 or len(edges) < 3:
        return ""
    if any(not _cells_of_run(run, cells) for run in runs):
        return ""

    # The grid is built from the cells' own edges, so a cell's span is a pair of
    # edge *indices* and needs no interval test. Asking which interval an edge
    # falls into asks a question whose answer is on a boundary, and that is how
    # the description column came to swallow the rate column beside it.
    def edge_index(value: float) -> int:
        return min(range(len(edges)), key=lambda i: (abs(edges[i] - value), i))

    # Anchor cell id -> its runs, and how far right the widest of them reaches.
    contents: dict[str, list[dict[str, Any]]] = {}
    reach: dict[str, float] = {}
    for run in runs:
        hit = _cells_of_run(run, cells)
        anchor = hit[0]["id"]
        contents.setdefault(anchor, []).append(run)
        reach[anchor] = max(reach.get(anchor, 0.0), float(hit[-1]["x1"]))

    columns = len(edges) - 1
    blocks: list[tuple[float, str]] = []
    for position, (row_y, row_cells) in enumerate(rows):
        body: list[str] = []
        cursor = 0
        heading = True
        for cell in row_cells:
            start = edge_index(float(cell["x0"]))
            if start < cursor:  # a spanning run already covered this column
                continue
            group = sorted(contents.get(cell["id"], ()),
                           key=lambda r: (float(r["baseline_y"]), float(r["origin_x"])))
            stop = max(start + 1,
                       edge_index(max(float(cell["x1"]),
                                      reach.get(cell["id"], 0.0))))
            while cursor < start:
                body.append("<td></td>")
                cursor += 1
            pieces: list[tuple[str, Any]] = []
            for line in _group_lines(group):
                if pieces:
                    pieces.append((" ", None))
                pieces.extend(_line_pieces(line))
            span = stop - cursor
            attribute = f' colspan="{span}"' if span > 1 else ""
            body.append(f"<td{attribute}>{_render_pieces(pieces)}</td>")
            heading = heading and bool(group) and all(r.get("bold") for r in group)
            cursor = stop
        while cursor < columns:
            body.append("<td></td>")
            cursor += 1
        tag = "th" if heading and position == 0 else "td"
        if tag == "th":
            body = [piece.replace("<td", "<th").replace("</td>", "</th>")
                    for piece in body]
        blocks.append((row_y, f"<tr>{''.join(body)}</tr>"))
    blocks.sort(key=lambda item: item[0])
    return f'<table class="gl-table">{"".join(body for _y, body in blocks)}</table>'


# Two cell edges this close are the same edge. It is the extractor's own 2dp
# quantisation, not a tolerance: an edge is stated to a hundredth of a point and
# nothing between two hundredths exists to be confused.
EDGE_EPSILON_PT = 0.01


def _ruled_bands(cells: Sequence[dict[str, Any]]) -> list[tuple[float, float]]:
    """The y intervals the lattice found ruled structure in, merged."""
    spans = sorted((float(c["y0"]), float(c["y1"])) for c in cells)
    merged: list[list[float]] = []
    for y0, y1 in spans:
        if merged and y0 <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], y1)
        else:
            merged.append([y0, y1])
    return [(y0, y1) for y0, y1 in merged]


def _region_sections(runs: Sequence[dict[str, Any]],
                     cells: Sequence[dict[str, Any]]
                     ) -> list[tuple[str, list[dict[str, Any]], list[dict[str, Any]]]]:
    """Split a guide region into ruled and unruled stretches, in page order.

    One region is regularly two documents. 2551M page 2 carries an unruled ATC
    rate table and then the "Guidelines and Instructions" prose, and measuring
    columns and ink-fill over the pair classified the whole thing as prose --
    which is how its rate table came to be published as running text with the
    column relationship destroyed. Splitting at the lattice's own boundary lets
    each stretch be classified on its own evidence.

    The split is on where the lattice found ruled cells, so it costs no
    threshold: a stretch either has a ruled grid in it or it has not.
    """
    bands = _ruled_bands(cells)
    if not bands or not runs:
        return [("unruled", list(runs), [])]

    def band_of(run: dict[str, Any]) -> int | None:
        centre = (float(run["y0"]) + float(run["y1"])) / 2.0
        for index, (y0, y1) in enumerate(bands):
            if y0 <= centre <= y1:
                return index
        return None

    keyed = sorted(((float(r["y0"]), float(r["x0"]), band_of(r), r) for r in runs),
                   key=lambda item: (item[0], item[1]))
    sections: list[tuple[str, list[dict[str, Any]], list[dict[str, Any]]]] = []
    current: int | None = -1
    for _y0, _x0, band, run in keyed:
        if band != current or not sections:
            current = band
            if band is None:
                sections.append(("unruled", [], []))
            else:
                y0, y1 = bands[band]
                inside = [c for c in cells
                          if float(c["y0"]) >= y0 - EDGE_EPSILON_PT
                          and float(c["y1"]) <= y1 + EDGE_EPSILON_PT]
                sections.append(("ruled", [], inside))
        sections[-1][1].append(run)
    return sections


def reflow_page(page_ir: dict[str, Any], split: PageSplit,
                page_layout: dict[str, Any] | None = None) -> str:
    """One page's guide region as flowing HTML.

    With a layout the region is split at the lattice's ruled bands and each
    stretch is laid out as what it is; without one (a standalone guide PDF has
    no lattice) the whole region is classified as before.
    """
    runs = [run for index, run in enumerate(page_ir["text_runs"])
            if index in split.run_indices]
    if not runs:
        return ""
    cells = [cell for cell in (page_layout or {}).get("cells", ())
             if cell["id"] in split.cell_ids]
    parts: list[str] = []
    flows: list[str] = []
    for kind, section_runs, section_cells in _region_sections(runs, cells):
        if not section_runs:
            continue
        markup = (_lattice_table_markup(section_runs, section_cells)
                  if kind == "ruled" else "")
        if markup:
            flows.append("lattice")
            parts.append(markup)
            continue
        # The column *grid* of an unruled reference table comes from where the
        # source starts its cells, not from a coverage histogram. On 2551M p2
        # the real gutter between the left description and the left rate sits
        # at 4-5 runs against a peak of 18, so `_coverage_gutters` called it
        # occupied, emitted 4 columns where the sheet prints 6, and merged two
        # independent source rows that share a scanline -- binding PT 060 to
        # the right half's 5% against an official 2%. `guides.table_columns`
        # asks the unambiguous question instead ("where does a cell start"),
        # and guides.py's self-test pins its answer on both real unruled pages.
        #
        # `flow` -- the dissolved reading columns -- is unchanged and still
        # comes from `_column_bands`, so the prose path and `_is_prose`'s own
        # classification move for no region. The ruled-lattice path above never
        # reaches here at all.
        grid, flow = guides.table_columns(section_runs), _column_bands(section_runs)[1]
        prose = _is_prose(section_runs, flow)
        flows.append("prose" if prose else "table")
        parts.append(_prose_markup(section_runs, flow) if prose
                     else _table_markup(section_runs, grid))
    return (f'<section class="gl-page" data-page="{page_ir["index"]}" '
            f'data-flow="{",".join(flows)}" '
            f'data-sections="{len(parts)}">{"".join(parts)}</section>')


# ---------------------------------------------------------------------------
# Document
# ---------------------------------------------------------------------------


BASE_CSS = """*{margin:0;padding:0;box-sizing:border-box}
html,body{background:#fff;-webkit-print-color-adjust:exact;print-color-adjust:exact}
.page{position:relative;overflow:hidden;background:#fff;break-after:page;page-break-after:always}
.page:last-of-type{break-after:auto;page-break-after:auto}
.rl{position:absolute;left:0;top:0}
.r{position:absolute}
.t{position:absolute;white-space:pre;text-rendering:geometricPrecision;z-index:%(z_text)d}
.c{position:absolute;z-index:%(z_cells)d}
.s{position:absolute}
.img{position:absolute;z-index:%(z_cells)d;display:block}
.band{position:absolute;left:0;top:0;width:100%%;height:100%%}
""" % {"z_text": Z_TEXT, "z_cells": Z_CELLS}


# Emitted only when there is a sibling document to point at, and never part of
# BASE_CSS, so a form with no guide keeps the stylesheet it had.
#
# `position:absolute` is the load-bearing part. `.page` is `position:relative`
# and therefore in normal flow, so a link in flow ahead of it would push every
# page down and move every rule and glyph on the sheet. Out of flow it cannot,
# and `@media print` removes it from the printed document entirely, which is
# the document verify.py measures.
DOC_LINK_CSS = (".doc-link{position:absolute;left:0;top:0;z-index:9;"
                "font:12px/1.5 system-ui,-apple-system,sans-serif;padding:2px 8px;"
                "background:#fff;color:#0645ad;text-decoration:underline}\n"
                "@media print{.doc-link{display:none}}")


# The reflowed guide is a reading document, so it is typeset for reading rather
# than for the source's metrics. No @font-face is emitted for it on purpose:
# shipping the measured WOFF2 with text that has deliberately been re-broken
# would imply an advance-level fidelity this document does not have and is not
# trying to have.
#
# The @media print block is where the guide stops being a web page. Its rules
# are pagination, not decoration: a heading must not be the last thing on a
# sheet, a table row must not be sliced in half, and the navigation links back
# to the form are furniture that means nothing on paper.
GUIDE_CSS = """body{background:#fff;color:#111}
.gl{max-width:46em;margin:0 auto;padding:3em 1.5em 4em;
font:16px/1.6 Georgia,"Times New Roman",serif}
.gl h1{font-size:1.6em;margin:0 0 .2em}
.gl .gl-sub{color:#555;font-size:.9em;margin:0 0 2em}
.gl h2{font-size:1.25em;margin:2em 0 .5em;line-height:1.3}
.gl h3{font-size:1.05em;margin:1.5em 0 .4em}
.gl p{margin:0 0 .8em;text-align:justify;hyphens:auto}
.gl .gl-page{margin:0 0 2.5em}
.gl .gl-table{border-collapse:collapse;width:100%;font-size:.85em;margin:1em 0}
.gl .gl-table td,.gl .gl-table th{border:1px solid #bbb;padding:.25em .5em;
text-align:left;vertical-align:top}
.gl .gl-table th{background:#f0f0f0}
@media print{
.gl{max-width:none;padding:0;font-size:11pt}
.gl h1,.gl h2,.gl h3{break-after:avoid;page-break-after:avoid;
break-inside:avoid;page-break-inside:avoid}
.gl p{orphans:2;widows:2}
.gl li,.gl .gl-sub,.gl .gl-download,.gl .gl-table tr{
break-inside:avoid;page-break-inside:avoid}
.gl .gl-table thead{display:table-header-group}
.gl .gl-page{margin:0 0 1.5em}
}"""


# Shared by both guide layouts: the absolute one needs it too, and it is the
# only styling that document has beyond the form's own scaffolding.
#
# `.gl-pdf object` is the fallback path only -- a guide PDF that came with an
# extraction is reflowed into this document instead, because a plugin viewport
# is not printable content. Where the fallback is taken the print rules make it
# say so rather than printing a blank frame.
GUIDE_PDF_CSS = """.gl-pdf{margin:2.5em auto;max-width:46em;
font:16px/1.6 Georgia,"Times New Roman",serif}
.gl-pdf object{display:block;width:100%;height:90vh;border:1px solid #bbb}
.gl .gl-download{color:#555;font-size:.9em;margin:0 0 1.5em}
@media print{
.gl-pdf{margin:0;max-width:none}
.gl-page+.gl-pdf,.gl-pdf+.gl-pdf{break-before:page;page-break-before:always}
.gl-pdf object{display:none}
}"""


# The form prints with `margin:0` because every coordinate on it is measured
# from the MediaBox and a margin would move all of them. The reflowed guide has
# no such coordinates -- it is prose -- and prose set to the paper's edge runs
# into the unprintable border that consumer printers reserve. 36pt (half an
# inch) clears that border on every common printer, so it is the widest measure
# that is safe to print unscaled anywhere.
GUIDE_PAGE_MARGIN_PT = 36.0


def guide_page_css(ir: dict[str, Any]) -> str:
    """@page for the reflowed guide: the form's paper, with a reading margin.

    Sized from the *form's* paper block rather than from the guide's own
    content, because a guide is printed to be read alongside its form and a
    Legal form whose instructions come out on Letter is two stacks of paper
    instead of one. There is deliberately no per-page rule here as there is for
    the form: the reflowed guide has no page boxes of its own, so it flows
    across as many sheets as the prose needs and every one of them is the same
    size.
    """
    paper = ir["paper"]
    return (f'@page{{size:{fmt(paper["width_pt"])}pt {fmt(paper["height_pt"])}pt;'
            f'margin:{fmt(GUIDE_PAGE_MARGIN_PT)}pt}}')


BAND_JS = r"""(function(){
"use strict";
var SVG_NS="http://www.w3.org/2000/svg";
var backend=document.documentElement.getAttribute("data-rule-backend");
var node=document.getElementById("formgen-bands");
var bands=node?JSON.parse(node.textContent):[];
var byId={};
bands.forEach(function(b){byId[b.id]=b;});

function rowY(band,i){
  var ys=band.row_y;
  if(i<ys.length){return ys[i];}
  return ys[ys.length-1]+(i-(ys.length-1))*band.row_pitch_pt;
}
function shift(rect,dy){
  return {x:rect.x,y:rect.y+dy,w:rect.w,h:rect.h,fill:rect.fill,id:rect.id};
}
function paint(target,rect){
  var el;
  if(backend==="svg"){
    el=document.createElementNS(SVG_NS,"rect");
    el.setAttribute("x",rect.x);el.setAttribute("y",rect.y);
    el.setAttribute("width",rect.w);el.setAttribute("height",rect.h);
    el.setAttribute("fill",rect.fill);
    /* no shape-rendering: a re-rendered row must paint exactly as the
       pre-rendered one does, and _rect() explains why that is anti-aliased */
    if(rect.id){el.setAttribute("data-rule-id",rect.id);}
  }else{
    el=document.createElement("div");
    el.className="r";
    el.style.cssText="left:"+rect.x+"pt;top:"+rect.y+"pt;width:"+rect.w+
      "pt;height:"+rect.h+"pt;background-color:"+rect.fill;
    if(rect.id){el.setAttribute("data-rule-id",rect.id);}
  }
  target.appendChild(el);
}
/* Rules for `rows` rendered rows. Rows inside capacity use their measured
   geometry; only overflow rows are stamped from the template, because the
   measured pitch is not constant (row 6 is 18.27pt, the rest 18.24pt). */
function bandRects(band,rows){
  var out=[],i,j,base,delta;
  for(i=0;i<rows;i++){
    if(i<band.capacity){
      base=band.rows[i].rules;
      for(j=0;j<base.length;j++){out.push(base[j]);}
    }else{
      base=band.rows[band.template_row].rules;
      delta=rowY(band,i)-band.row_y[band.template_row];
      for(j=0;j<base.length;j++){out.push(shift(base[j],delta));}
    }
  }
  for(i=0;i<=rows;i++){
    if(i<band.row_y.length){
      base=band.boundaries[i]||[];
      for(j=0;j<base.length;j++){out.push(base[j]);}
    }else{
      base=band.boundaries[band.template_boundary]||[];
      delta=rowY(band,i)-band.row_y[band.template_boundary];
      for(j=0;j<base.length;j++){out.push(shift(base[j],delta));}
    }
  }
  for(i=0;i<band.spanning.length;i++){
    var s=band.spanning[i];
    var y0=band.row_y[0]+s.d_top;
    var y1=rowY(band,rows)+s.d_bottom;
    out.push({x:s.rect.x,y:y0,w:s.rect.w,h:y1-y0,fill:s.rect.fill,id:s.rect.id});
  }
  return out;
}
function rowGeometry(band,i){
  if(i<band.capacity){return band.rows[i];}
  var template=band.rows[band.template_row];
  var delta=rowY(band,i)-band.row_y[band.template_row];
  return {index:i,y:rowY(band,i),
    cells:template.cells.map(function(c){
      var copy=JSON.parse(JSON.stringify(c));
      copy.id=band.id+"-r"+i+"-c"+c.col;
      copy.y=c.y+delta;
      return copy;}),
    texts:template.texts.map(function(t,j){
      var copy=JSON.parse(JSON.stringify(t));
      copy.id=band.id+"-r"+i+"-t"+j;
      copy.baseline_y=t.baseline_y+delta;
      /* the enumerated column carries the row's own ordinal, not the
         template row's */
      if(t.role==="enumerated"){copy.text=String(i+1);}
      return copy;})};
}
/* One row's cells: clone the <template> for the row's shape, then overlay the
   measured geometry for this particular row. The template says which cells a
   row has, which are fields and how many comb slots each carries; the blob
   says where this row's edges actually are, which is not the template's
   position plus i*pitch. */
function rowCells(band,row){
  var tpl=document.getElementById("band-template-"+band.id);
  var nodes=null;
  if(tpl&&tpl.content){
    nodes=tpl.content.cloneNode(true).querySelectorAll("[data-cell-kind]");
  }
  if(!nodes||nodes.length!==row.cells.length){
    return row.cells.map(function(cell){return cellElement(cell);});
  }
  var out=[],i;
  for(i=0;i<row.cells.length;i++){out.push(applyCell(nodes[i],row.cells[i]));}
  return out;
}
/* A row added at run time has to be as fillable as a pre-rendered one, so the
   field layer is rebuilt with the row's own measurements rather than inherited
   from the template: a band row's comb sub-band is measured per row and the
   template's fitted size is not automatically this row's. */
function fieldRegions(field){
  /* One inset per writing region. `region_insets` is present only where the
     source rules the cell into several regions; everywhere else the single
     `inset_trbl` is the whole answer and stays it. Mirrors
     FieldBox.region_insets. */
  return field.region_insets?field.region_insets:[field.inset_trbl];
}
function fieldMaxlengthOne(cell,regionIndex){
  /* Mirrors field_input_markup's P1/P1b maxlength=1 stamp into a row cloned
     at run time. Sparse keys: maxlength_one means every region, region_
     maxlength_one is the mixed case, absent means unbounded. */
  var field=cell.field;
  if(!field){return false;}
  if(field.maxlength_one){return true;}
  if(field.region_maxlength_one){
    return !!field.region_maxlength_one[regionIndex||0];
  }
  return false;
}
function fieldMetrics(el,field,regionIndex,slotIndex){
  el.style.fontSize=field.size_pt+"pt";
  el.style.lineHeight=field.line_height_pt+"pt";
  if(field.letter_spacing_pt!==null&&field.letter_spacing_pt!==undefined){
    el.style.letterSpacing=field.letter_spacing_pt+"pt";
  }else{
    el.style.letterSpacing="";
  }
  var inset=fieldRegions(field)[regionIndex||0];
  if(inset){
    el.style.inset=inset[0]+"pt "+inset[1]+"pt "+inset[2]+"pt "+inset[3]+"pt";
  }
  /* F219: mirrors field_input_markup's own inline text-align:center for a
     plain field's writing line (F212's signature-strip target set,
     `fields.centered`) into a row cloned at run time -- a comb slot never
     reaches here with field.centered true (SignatureLineBinding never
     targets a comb), but the slotIndex==null guard matches field_input_
     markup's own condition exactly rather than relying on that being so. */
  el.style.textAlign=(slotIndex==null&&field.centered)?"center":"";
}
function fieldName(cell,slotIndex,regionIndex){
  if(slotIndex!==null){return cell.id+"-s"+slotIndex;}
  return cell.id+(cell.field.region_insets?"-i"+(regionIndex||0):"-i");
}
function fieldInput(cell,slotIndex,regionIndex){
  var el=document.createElement("input");
  el.type="text";
  el.className="fi "+cell.field["class"]+(slotIndex===null?"":" fc");
  el.id=fieldName(cell,slotIndex,regionIndex);
  el.name=cell.id;
  el.setAttribute("autocomplete","off");
  el.setAttribute("spellcheck","false");
  if(slotIndex!==null){
    el.setAttribute("data-slot-index",slotIndex);
    el.setAttribute("maxlength","1");
  }else if(fieldMaxlengthOne(cell,regionIndex)){
    el.setAttribute("maxlength","1");
  }
  fieldMetrics(el,cell.field,regionIndex,slotIndex);
  return el;
}
/* The clone carries the template's inputs, which are deliberately anonymous:
   an identity in a <template> would be a second element with the row's name. */
function applyFields(el,cell){
  if(!cell.field){return;}
  var inputs=el.querySelectorAll("input.fi"),i,slot,region=0;
  for(i=0;i<inputs.length;i++){
    slot=inputs[i].getAttribute("data-slot-index");
    /* A plain field's inputs are its writing regions, in document order; a
       comb's are its slots and carry their own index. Counting the plain ones
       here is what keeps a cloned row's regions from all collapsing onto the
       first region's inset and its id. */
    inputs[i].id=fieldName(cell,slot===null?null:slot,region);
    inputs[i].name=cell.id;
    inputs[i].value="";
    fieldMetrics(inputs[i],cell.field,region,slot);
    if(slot===null){region++;}
  }
}
function applyCell(el,cell){
  el.removeAttribute("data-cell-id");
  el.id=cell.id;
  el.setAttribute("data-row",cell.row);
  el.setAttribute("data-col",cell.col);
  if(cell.field){el.setAttribute("data-field-name",cell.id);}
  el.style.cssText="left:"+cell.x+"pt;top:"+cell.y+"pt;width:"+cell.w+
    "pt;height:"+cell.h+"pt";
  var slots=el.querySelectorAll(".s");
  if(cell.comb&&slots.length===cell.comb.slot_x.length-1){
    for(var i=0;i<slots.length;i++){
      slots[i].style.cssText="left:"+cell.comb.slot_x[i]+"pt;top:"+cell.comb.y+
        "pt;width:"+(cell.comb.slot_x[i+1]-cell.comb.slot_x[i])+
        "pt;height:"+cell.comb.h+"pt";
    }
  }else if(cell.comb){
    /* the row's comb has a different slot count than the template's: rebuild
       rather than reposition, and never invent slots the layout did not
       measure */
    return cellElement(cell);
  }
  applyFields(el,cell);
  return el;
}
function cellElement(cell){
  var el=document.createElement("div");
  el.id=cell.id;
  /* `.f` follows the typing surface, not the box detector's kind: a comb cell
     that also holds a pre-printed decimal point is a field. Mirrors
     cell_markup(). */
  el.className=cell.field?"c f":"c";
  el.setAttribute("data-cell-kind",cell.kind);
  el.setAttribute("data-row",cell.row);
  el.setAttribute("data-col",cell.col);
  el.style.cssText="left:"+cell.x+"pt;top:"+cell.y+"pt;width:"+cell.w+
    "pt;height:"+cell.h+"pt";
  if(cell.field){
    el.setAttribute("data-field-kind",cell.field.kind);
    el.setAttribute("data-field-name",cell.id);
    if(cell.field.capacity!==null&&cell.field.capacity!==undefined){
      el.setAttribute("data-comb-capacity",cell.field.capacity);
    }
  }
  if(cell.comb){
    el.setAttribute("data-comb-slots",cell.comb.cells);
    el.setAttribute("data-comb-pitch",cell.comb.pitch_pt);
    for(var i=0;i<cell.comb.slot_x.length-1;i++){
      var slot=document.createElement("div");
      slot.className="s";
      slot.setAttribute("data-slot",i);
      slot.style.cssText="left:"+cell.comb.slot_x[i]+"pt;top:"+cell.comb.y+
        "pt;width:"+(cell.comb.slot_x[i+1]-cell.comb.slot_x[i])+
        "pt;height:"+cell.comb.h+"pt";
      if(cell.field){slot.appendChild(fieldInput(cell,i,0));}
      el.appendChild(slot);
    }
  }else if(cell.field){
    var regions=fieldRegions(cell.field),r;
    for(r=0;r<regions.length;r++){el.appendChild(fieldInput(cell,null,r));}
  }
  return el;
}
/* Blink floors a block's top to the device grid and floors the baseline
   inside it, so `top` alone cannot express a sub-0.75pt baseline. Place the
   box on the grid, where that flooring is a no-op, and carry the remainder in
   a transform, which layout does not snap. Mirrors _vertical_placement(). */
var DEVICE_PX_PT=0.75;
function placeBaseline(baselineY,offsetPt){
  var offsetPx=offsetPt/DEVICE_PX_PT;
  var topPx=Math.floor((baselineY-offsetPt)/DEVICE_PX_PT);
  var paintedPx=topPx+Math.floor(offsetPx);
  return {top:topPx*DEVICE_PX_PT,ty:baselineY-paintedPx*DEVICE_PX_PT};
}
function textElement(text){
  var el=document.createElement("div");
  var place=placeBaseline(text.baseline_y,text.baseline_offset_pt);
  var scaled=text.scale_x!==null&&text.scale_x!==undefined;
  var ops=[];
  el.className="t";
  el.id=text.id;
  /* a scaled box snaps x as well, so it carries both axes in the transform */
  el.style.cssText=text.style+";left:"+(scaled?"0":text.x+"pt")+
    ";top:"+place.top+"pt";
  if(scaled){
    ops.push("translate("+text.x+"pt,"+place.ty+"pt)");
    ops.push("scaleX("+text.scale_x+")");
  }else if(Math.abs(place.ty)>1e-9){
    ops.push("translateY("+place.ty+"pt)");
  }
  if(ops.length){
    el.style.transform=ops.join(" ");
    el.style.transformOrigin="0 "+text.baseline_offset_pt+"pt";
  }
  el.textContent=text.text;
  return el;
}
/* Render `rows` rows of `bandId`. Rows beyond the sheet's official capacity
   are not silently overrun: the sheet holds `capacity`, the remainder belongs
   on a continuation page, so the overflow is reported rather than drawn. */
function setBandRows(bandId,rows,options){
  var band=byId[bandId];
  if(!band){throw new Error("no such band: "+bandId);}
  options=options||{};
  var drawn=options.allowOverflow?rows:Math.min(rows,band.capacity);
  var overflow=Math.max(0,rows-drawn);
  var rules=document.getElementById("band-rules-"+bandId);
  var content=document.getElementById("band-content-"+bandId);
  while(rules.firstChild){rules.removeChild(rules.firstChild);}
  while(content.firstChild){content.removeChild(content.firstChild);}
  bandRects(band,drawn).forEach(function(rect){paint(rules,rect);});
  for(var i=0;i<drawn;i++){
    var row=rowGeometry(band,i);
    rowCells(band,row).forEach(function(el){content.appendChild(el);});
    row.texts.forEach(function(text){content.appendChild(textElement(text));});
  }
  content.setAttribute("data-rendered-rows",drawn);
  content.setAttribute("data-overflow-rows",overflow);
  band.rendered_rows=drawn;
  return {rendered:drawn,overflow:overflow,capacity:band.capacity};
}
window.formgen={bands:bands,setBandRows:setBandRows,bandRects:bandRects,rowY:rowY};
})();"""


# A comb is one field drawn with N tick marks, so it has to *behave* as one:
# typing runs through it, backspace runs back through it, and a pasted TIN
# lands in it whole. Every listener is delegated from the document rather than
# bound per input, which is what makes a band row added by setBandRows fillable
# with no re-binding step -- and there are 488 comb inputs on 2551Q page 1
# alone, so 488 listener registrations is not a neutral alternative.
FIELD_JS = r"""(function(){
"use strict";
function isSlot(el){
  return !!(el&&el.classList&&el.classList.contains("fi")&&
            el.hasAttribute("data-slot-index"));
}
function slotsOf(el){
  var cell=el.closest("[data-cell-kind]");
  return cell?cell.querySelectorAll("input.fi[data-slot-index]"):null;
}
/* Position in the list of TYPEABLE slots, which is not `data-slot-index`. A
   comb compartment the source already filled -- the century "2 0", a TIN's
   printed branch code, the grey gap between two digit groups -- emits its slot
   div with no input, so the two numberings diverge the moment any comb has one.
   Reading the attribute and indexing the NodeList with it would then step off
   the end (a 4-slot year comb whose first two are printed leaves 2 inputs, and
   slot 2 would look for list[3]) and typing would stop advancing at the first
   printed box. The list itself is the sequence a taxpayer moves through. */
function positionOf(list,el){
  for(var i=0;i<list.length;i++){if(list[i]===el){return i;}}
  return -1;
}
function move(el,delta,select){
  var list=slotsOf(el);
  if(!list){return null;}
  var at=positionOf(list,el);
  if(at<0){return null;}
  var index=at+delta;
  if(index<0||index>=list.length){return null;}
  list[index].focus();
  if(select&&list[index].select){list[index].select();}
  return list[index];
}
/* Selecting on focus is what lets a filled slot be typed over: maxlength=1
   rejects a second character outright, so without it a comb can be corrected
   only by deleting first. */
document.addEventListener("focusin",function(ev){
  if(isSlot(ev.target)&&ev.target.select){ev.target.select();}
});
document.addEventListener("input",function(ev){
  if(!isSlot(ev.target)){return;}
  if(ev.target.value.length>0){move(ev.target,1,true);}
});
document.addEventListener("keydown",function(ev){
  var el=ev.target;
  if(!isSlot(el)){return;}
  if(ev.key==="Backspace"&&el.value===""){
    var previous=move(el,-1,false);
    if(previous){previous.value="";ev.preventDefault();}
  }else if(ev.key==="ArrowLeft"&&el.selectionStart===0){
    if(move(el,-1,true)){ev.preventDefault();}
  }else if(ev.key==="ArrowRight"&&el.selectionEnd===el.value.length){
    if(move(el,1,true)){ev.preventDefault();}
  }
});
/* maxlength=1 truncates a paste to its first character, which for a TIN is
   the difference between one keystroke and nine. Spread it instead. */
document.addEventListener("paste",function(ev){
  var el=ev.target;
  if(!isSlot(el)){return;}
  var clipboard=ev.clipboardData||window.clipboardData;
  var list=slotsOf(el);
  if(!clipboard||!list){return;}
  var text=(clipboard.getData("text")||"").replace(/\s+/g,"");
  if(!text){return;}
  ev.preventDefault();
  var index=positionOf(list,el),written=0,last=el;
  if(index<0){return;}
  while(index<list.length&&written<text.length){
    list[index].value=text.charAt(written);
    last=list[index];
    index++;written++;
  }
  last.focus();
  if(last.select){last.select();}
});
/* A caret is browser chrome, and Chromium paints it into the printed page:
   printing with the cursor still in a comb produced a 0.75x6.75pt black bar
   which the round-trip read as an extra structural rule -- correctly, because
   on paper that is what it is. `caret-color:transparent` does not suppress it
   in the print path, so the focus itself is dropped for the duration. */
window.addEventListener("beforeprint",function(){
  var el=document.activeElement;
  if(el&&el.classList&&el.classList.contains("fi")){el.blur();}
});
window.formgenFields={slotsOf:slotsOf};
})();"""


# ---------------------------------------------------------------------------
# The field debug overlay
# ---------------------------------------------------------------------------
#
# WHY THIS EXISTS, AND WHY ITS FIRST VERSION WAS WORTHLESS
#
# The affordance a taxpayer needs is `:focus`, and it shows exactly one field at
# a time. That is right for filling a form in and useless for reviewing one:
# 2551Q carries 782 inputs, so seeing them all means 782 tab presses, and a
# defect nobody looked at is a defect that ships.
#
# The first version of this overlay coloured each input by occlusion, ancestor
# clipping and fitted font size. Every one of those is computed FROM THE INPUT'S
# OWN RECT -- the rect the field layer emitted. It reported "233 as drawn, 0
# problems" on a page the user could see was wrong, and it was not lying: each
# input really was exactly where the field layer said it should be. The field
# layer was what was wrong.
#
#   AN OVERLAY THAT DERIVES ITS EXPECTATION FROM THE THING IT IS CHECKING IS
#   DECORATION, NOT A CHECK. A checker that shares its subject's source of
#   truth cannot fail.
#
# This version compares the field layer against a DIFFERENT PRODUCER on the
# same page. `emit_page()` builds two layers from two inputs that never meet:
#
#     <svg class="rl">    from page_ir["rules"] / ["area_fills"] -- the PDF's
#                         own painting operators, via extract.py
#     <div class="layer-cells">   from page_layout["cells"] + FieldPlan -- what
#                         lattice.py decided those operators MEAN
#
# The rule layer is the printed sheet. The field layer is our reading of it. The
# overlay asks the only question that can catch a misreading: does each writing
# box fill the printed box it is supposed to fill, and does every printed box
# have a writing box in it? A wrong cell classification, a comb that stops
# short, an input laid across three printed columns, a box a taxpayer cannot
# type in -- all of those are silent to the field layer and loud here.
#
# It is not a perfect independence. Both layers descend from one extraction, so
# an error in extract.py could move both together. It is the strongest
# independence available inside the document, and it is the one that separates
# "we drew the box here" from "the box is here".
#
# WHAT A PRINTED BOX IS, DERIVED RATHER THAN GUESSED
#
# The rule layer is not a list of boxes; it is a list of painted rectangles,
# most of them one stroke of one wall. A box has to be reconstructed, and every
# earlier attempt in this project to reconstruct one by proximity ("the nearest
# printed rect by centre distance") produced numbers that were wrong. So:
#
#   * A rect bounds a region by its INNER face. The white space to the right of
#     a rule starts at the rule's x1, not at its centre and not at its x0. That
#     removes the "is a rule thin or thick" question entirely -- no thickness
#     threshold is needed anywhere in this file, and none is used.
#   * A rect only bounds what it is painted OVER differently. A white knockout
#     inside a grey band is a visible box; the same white rect on white paper is
#     nothing. `visibleRects()` compares each rect's fill with the fill of the
#     innermost EARLIER rect that wholly contains it (paper white if there is
#     none). Containment, not a point probe: an earlier draft sampled the fill
#     under each rect's centre, and a vertical rule crossing a horizontal rule
#     has another black rect under its centre, so it declared 2550M page 2's
#     column dividers invisible and reported a 253pt-wide box where the printed
#     box is 87pt. That was an instrument error, found by rendering the page.
#   * A wall must SPAN the box it walls. `faceCoverage()` measures how much of
#     each side is actually inked; a side under 90% is a tick, not a wall, and
#     the search bans it and widens. This is what makes a comb slot resolve to
#     the comb CELL its guides subdivide, instead of to a slot with two open
#     sides. Comb guides are deliberately short on BIR forms.
#   * Inputs that land in the same printed box are judged TOGETHER, as their
#     union. Forty comb slots tiling one printed cell fill it; each slot alone
#     covers 2.5% of it. Asking the question per input would report thirty-nine
#     false defects per comb, which is how instruments in this project have
#     inflated counts 68-fold before.
#
# TOLERANCE, FROM THE DATA
#
# Two numbers, and neither is taste:
#
#   * `RULE_POSITION_TOLERANCE_PT` (0.25pt) is the pipeline's own position
#     tolerance, the same one verify.py holds the round-trip to.
#   * The stroke's own width. "The box" is ambiguous to within the ink that
#     draws it -- inside face, centre, outside face are three defensible
#     answers differing by the stroke width -- so a writing box may sit anywhere
#     within the wall without being wrong. That is expressed geometrically, not
#     as a constant: the box has an INNER rectangle (inner faces) and an OUTER
#     rectangle (outer faces), and an input is at home anywhere between them,
#     plus 0.25pt.
#
# Measured over 2550M and 2200T (487 printed boxes, 1169 inputs): the emitted
# insets cluster at 0.22-0.35pt and 0.70-0.74pt, always about half the wall they
# sit inside, and every one of the 487 is inside the tolerance with margin --
# the tolerance is not absorbing a population, it is absorbing the ink. The
# defects that survive it are not marginal either: overflow beyond the outer
# face is either 0.0pt (469 boxes) or 0.46pt and up, with six inputs 170pt
# outside their box. There is no smooth tail for a threshold to sit in the
# middle of, which is the property a threshold needs.
#
# THE STATES
#
#   fits      green, hairline dash -- the union of the box's inputs covers the
#                                     printed box, and stays inside its walls
#   small     orange               -- part of the printed box more than a wall's
#                                     width inside the ink is unreachable; the
#                                     % of box area unreached is reported
#   over      red                  -- an input crosses a printed wall
#   unboxed   magenta              -- no closed printed rectangle around it at
#                                     all: the field layer invented this box
#   vacant    blue                 -- a closed printed rectangle, big enough to
#                                     write in, carrying no input and no printed
#                                     label. This is the user's "no yellow box
#                                     here", and the previous overlay could not
#                                     express it: it iterated over inputs, so a
#                                     missing input was a thing it never visited
#
# Health is a hairline dash and every defect is solid and filled, so a healthy
# sheet reads as a wireframe and a fault reads as a blot. Every flagged printed
# box is also outlined in the overlay layer, which is what makes a zero-size
# input visible: the input paints nothing, but the box we compared it against
# is drawn.
#
# WHY IT IS SAFE TO SHIP INSIDE THE DOCUMENT (four barriers, any one enough)
#
#   * The rules are a JS string. The emitted stylesheet is untouched, so a
#     printed or packaged page contains no debug selector to lose a guard over,
#     and the single `<style>` the document ships is byte-for-byte the one it
#     shipped before this existed.
#   * The script's first statement that touches anything is behind an exact
#     `debug=fields` token in `location.search`. Without it the function returns
#     having read one string off `window.location` and created nothing: no
#     element, no attribute, no listener. A page opened without the token is the
#     page that would have rendered if this script were deleted. That is the
#     byte-identical-render proof, and `field_debug_assertions()` re-proves it
#     from the source text on every build.
#   * The rules it does inject are wrapped in `@media screen`, and a second
#     `@media print` block neutralises every one of them with `!important`.
#   * `beforeprint` removes the injected stylesheet AND every element the
#     overlay created, and `afterprint` puts them back, so even a browser that
#     ignored both media guards has nothing left to apply.
#
# The order matters: the query string is first because it is the only one that
# survives a stylesheet transform dropping the media guards, which has happened
# to this repo's packaged bundles before.
#
# Two further containment rules the overlay obeys, both asserted:
#
#   * It never creates a `div`. `.page:last-of-type{break-after:auto}` is the
#     one structural selector in BASE_CSS, and appending a div to `<body>`
#     would make the LAST PAGE stop matching it and add a page break after the
#     sheet. Overlay chrome is `<aside>` and `<i>`, which cannot be a `.page`.
#   * It reads no geometry from the field layer's own containers. The selectors
#     `.layer-cells`, `.c` and `.s` do not appear in the script at all -- if
#     they did, the expectation could drift back towards the subject, which is
#     the exact failure this rewrite exists to end.

# The pipeline's position tolerance, from GOAL.md's constraint list ("exact
# tolerances (position 0.25pt, ...)"). Stated here rather than inlined so the
# overlay and the round-trip cannot be relaxed independently of each other.
RULE_POSITION_TOLERANCE_PT = 0.25

# A side of a candidate box must be inked over at least this much of its length
# to count as a wall rather than a guide tick. Measured on 2550M and 2200T,
# sides are either fully inked (>= 0.99) or a comb guide at <= 0.46; nothing
# lands between, so the cut has no population sitting on it.
RULE_WALL_COVERAGE = 0.9

# TONE, THE DEFECT THIS SPLIT CLOSES (F213)
#
# Every rect the overlay reads was, until this constant existed, a wall
# candidate purely because its fill differed from what was under it --
# `visibleRects()` cannot and must not stop meaning that, because that
# question ("did this rect paint something new") is also how the overlay
# decides what tone sits under a candidate box. What is missing is a SECOND
# question: paint something new is not the same as BOUND something. A page-
# spanning row-band tint fragment differs from paper (so it is visible ink)
# and can still be running straight through the middle of a checkbox, not
# forming its edge -- 1701-2018 p2's Compensation-Earner checkbox has exactly
# this shape: `h27`, a 0.8509-gray tint fragment 15.24pt wide, crosses it, and
# the old code closed a false box on it and reported the correctly-placed
# input `p2c21-i` as "crosses a printed wall" (F213).
#
# Corpus-wide, rule tone is quantised to exactly eight values (measured over
# all 53 `build/ir/*.ir.json`, PLAN.md T3+T4): 0.0 structural; 0.251, 0.502,
# 0.651 decorative and WALL -- mid-grey checkbox outlines, 100% and 94% short
# box-edge runs respectively, neither with a single run >=100pt; 0.7489,
# 0.7529, 0.8509 decorative and TINT -- 42-45% of their own ink is a run
# >=100pt, i.e. dominated by page-spanning bands; 1.0 knockout (white). The
# ONLY empty interval in that list is 0.651 -> 0.7489, corroborated
# independently by the ink-morphology split above (short edges below it,
# spanning bands at or above it), so `RULE_WALL_TINT_SPLIT_GRAY` sits inside
# it. Placing the split anywhere at or below 0.651 would erase the checkbox
# outlines the overlay is supposed to keep finding -- including the four
# Schedule-1 squares F210 anchors, whose blue "vacant" marks must survive this
# change -- which is the mistake an earlier draft of this plan made and
# measurement refuted.
#
# Knockout (white, gray 1.0) is deliberately NOT folded into "tint": it was
# never a page-spanning band to begin with (it is the source painting paper
# back over a shaded box so a taxpayer can write there), no proven case shows
# it acting as a phantom wall, and the corpus's own quantisation makes the
# distinction free -- gray 1.0 is the sentinel above every real tint value
# (the highest, 0.8509, is nowhere near it), so `gray < 1` in the overlay's
# `isTintTone()` costs no second threshold. Knockout keeps acting exactly as
# every other visible rect always did: eligible to bound a box wherever its
# own edges happen to line up with one, decided by geometry, same as before
# this constant existed.
RULE_WALL_TINT_SPLIT_GRAY = 0.70

FIELD_DEBUG_SCREEN_CSS = (
    '[data-fg-field]{outline-offset:0!important}'
    '[data-fg-field="fits"]{outline:1px dashed rgba(46,125,50,.85)!important}'
    '[data-fg-field="small"]{outline:1.5px solid #ef6c00!important;'
    'background:rgba(239,108,0,.20)!important}'
    '[data-fg-field="over"]{outline:1.5px solid #d32f2f!important;'
    'background:rgba(211,47,47,.25)!important}'
    '[data-fg-field="unboxed"]{outline:1.5px solid #c2185b!important;'
    'background:rgba(194,24,91,.25)!important}'
    # The printed box every flagged group was measured against, drawn where the
    # RULE layer says it is. A zero-size input paints no outline of its own, so
    # without this the one fault a reviewer most needs to find is the only one
    # that is invisible.
    '[data-fg-layer]{position:absolute;left:0;top:0;right:0;bottom:0;'
    'pointer-events:none;z-index:2147483646}'
    '[data-fg-box]{position:absolute;display:block}'
    '[data-fg-box="small"]{outline:1.5px solid #ef6c00;'
    'background:repeating-linear-gradient(45deg,rgba(239,108,0,.28) 0 3px,'
    'rgba(239,108,0,0) 3px 7px)}'
    '[data-fg-box="over"]{outline:1.5px solid #d32f2f}'
    '[data-fg-box="vacant"]{outline:1.5px solid #1565c0;'
    'background:rgba(21,101,192,.30)}'
    '[data-fg-legend] b{display:block;font-weight:400}'
    '[data-fg-legend]{position:absolute;right:4pt;top:4pt;z-index:2147483647;'
    'background:#fff;color:#111;border:1px solid #111;padding:6px 8px;'
    'font:11px/1.5 system-ui,-apple-system,sans-serif;white-space:nowrap;'
    'box-shadow:0 1px 4px rgba(0,0,0,.35)}'
    '[data-fg-swatch]{display:inline-block;width:9px;height:9px;'
    'margin-right:6px;vertical-align:-1px;border:1px solid #111}'
    '[data-fg-swatch="fits"]{background:#2e7d32}'
    '[data-fg-swatch="small"]{background:#ef6c00}'
    '[data-fg-swatch="over"]{background:#d32f2f}'
    '[data-fg-swatch="unboxed"]{background:#c2185b}'
    '[data-fg-swatch="vacant"]{background:#1565c0}'
)
FIELD_DEBUG_PRINT_CSS = (
    '[data-fg-field]{outline:0!important;background:none!important}'
    '[data-fg-layer]{display:none!important}'
    '[data-fg-legend]{display:none!important}'
)
FIELD_DEBUG_CSS = ("@media screen{" + FIELD_DEBUG_SCREEN_CSS + "}"
                   "@media print{" + FIELD_DEBUG_PRINT_CSS + "}")

FIELD_DEBUG_JS = r"""(function(){
"use strict";
/* An exact token, not a substring: `?nodebug=fields` and `?debug=fieldsets`
   are not requests for this. */
var TOKEN="debug=fields";
function requested(){
  var query=String(window.location.search||"");
  if(query.charAt(0)==="?"){query=query.slice(1);}
  var parts=query.split("&");
  for(var i=0;i<parts.length;i++){
    if(parts[i]===TOKEN){return true;}
  }
  return false;
}

/* ---- pipeline constants: pure, no DOM ------------------------------------
   Interpolated from the module constants so the overlay and the pipeline
   cannot drift apart or be relaxed independently. */
var TOL=__RULE_POSITION_TOLERANCE_PT__;
var WALL=__RULE_WALL_COVERAGE__;
var MIN_BOX=__FIELD_MIN_SIZE_PT__;
/* The wall/tint split (F213). See RULE_WALL_TINT_SPLIT_GRAY's own comment in
   emit.py for the measured tone table and why 0.70 is the only interval on
   the sheet with no rule tone sitting in it. */
var TINT_SPLIT_GRAY=__RULE_WALL_TINT_SPLIT_GRAY__;
var STRUCTURAL_MAX_GRAY=0.15;
/* Sub-point arithmetic noise on coordinates that arrive as pt from a viewport
   measured in px. Never a tolerance: tolerances are TOL and the wall width. */
var EPS=0.02;
/* How far outside a wall a vacant-box probe is planted. Half a point is under
   every printed gap on the corpus and over every rounding artefact. */
var PROBE=0.5;
/* A box has four sides, so four bans settle it; the cap is a termination
   guarantee, not a tuning knob. */
var ROUNDS=8;
var PAPER="rgb(255, 255, 255)";
var ORDER=["fits","small","over","unboxed","vacant"];

/* ---- the printed sheet, read from the rule layer only -------------------
   Read-only DOM access below (getBoundingClientRect, getComputedStyle,
   querySelectorAll): every one of these is what window.formgenFieldCensus
   needs to compute its census, and none of it can change what the page
   renders, so it is reachable below WITHOUT the ?debug=fields token -- see
   that assignment's own comment, right before the gate, for the boundary
   this crosses and why it is safe. */

/* Every painted rectangle on one page, in page points, in PAINT ORDER.
   Rects only: an <image> or a filled <path> is ink, but its bounding box is
   not a wall, and treating it as one would invent walls where the artwork
   merely has extent. Both rule backends are read the same way, through the
   viewport, so neither the svg backend's user units nor the css backend's
   snapped divs are trusted to be what their markup says. */
function ruleRects(page,frame,ptPerPx){
  var out=[],nodes=page.querySelectorAll(".rl rect, .rl .r"),i,el,r,style;
  for(i=0;i<nodes.length;i++){
    el=nodes[i];
    r=el.getBoundingClientRect();
    if(r.width<=0||r.height<=0){continue;}
    style=window.getComputedStyle(el);
    out.push({n:out.length,
              x:(r.left-frame.left)*ptPerPx,y:(r.top-frame.top)*ptPerPx,
              x1:(r.right-frame.left)*ptPerPx,y1:(r.bottom-frame.top)*ptPerPx,
              fill:el.tagName.toLowerCase()==="rect"?style.fill:style.backgroundColor});
  }
  return out;
}
function ptRect(el,frame,ptPerPx){
  var r=el.getBoundingClientRect();
  return {id:el.id,x:(r.left-frame.left)*ptPerPx,y:(r.top-frame.top)*ptPerPx,
          x1:(r.right-frame.left)*ptPerPx,y1:(r.bottom-frame.top)*ptPerPx};
}
/* ---- tone: separates paint that EXISTS from paint that BOUNDS a box ----- */
/* PURE-GEOMETRY-BEGIN -- nothing between this line and its matching END
   touches an element, the document or the window: every function here reads
   only the plain {n,x,y,x1,y1,fill} objects ruleRects() already built. That
   is what lets emit.py's self-test extract this exact span, verbatim, and run
   it under node on a synthetic case -- proving RULE_WALL_TINT_SPLIT_GRAY is
   load-bearing in the shipped bytes rather than merely present in them. */
function toneGray(fill){
  var m=/rgba?\(\s*([\d.]+)[,\s]+([\d.]+)[,\s]+([\d.]+)/.exec(fill||"");
  if(!m){return 0;}
  return ((parseFloat(m[1])+parseFloat(m[2])+parseFloat(m[3]))/3)/255;
}
/* Tint: a page-spanning shading band, corpus-wide grays 0.7489/0.7529/0.8509
   (F213's h27 among them). The upper bound is not a second threshold -- it is
   exactly 1, the knockout sentinel, so white paint keeps bounding a box
   exactly as it always did (RULE_WALL_TINT_SPLIT_GRAY's own comment says why
   that is deliberate). Everything at or below the split, including 0.251's
   checkbox outlines (F210), stays a wall candidate, unaffected. */
function isTintTone(fill){
  var gray=toneGray(fill);
  return gray>TINT_SPLIT_GRAY&&gray<1;
}
/* The vacant probe's own, BROADER test. Strokes and interiors are different
   questions: a 0.651 STROKE is a checkbox outline (a wall -- 94% of 0.651
   rules are short box edges, so it sits below TINT_SPLIT_GRAY on purpose),
   but a 0.651 FILL covering a box's interior is a shading pad (0619-E's
   centavo-separator compartments are exactly this). At a box's CENTRE only a
   slab can be present -- any wall-tone stroke through the centre would have
   closed a smaller box at itself -- so grey paint at the centre means
   decoration regardless of which side of the stroke split its tone falls on.
   The band is (STRUCTURAL_MAX_GRAY, 1): neither printed black ink nor
   knockout white nor bare paper. STRUCTURAL_MAX_GRAY mirrors the pipeline's
   own structural cutoff (extract.classify_tone / lattice.tone_role), not a
   new number. */
function isDecorPaint(fill){
  var gray=toneGray(fill);
  return gray>STRUCTURAL_MAX_GRAY&&gray<1;
}
/* The colour actually showing at one point, searched back to front in PAINT
   ORDER -- the same "innermost rect that matters here" question
   visibleRects() asks about a whole rect, asked here about a point instead.
   Used by the `vacant` probe to read what tone sits under a candidate box's
   centre without ever consulting the field layer. */
function paintAt(px,py,rects){
  var i,r;
  for(i=rects.length-1;i>=0;i--){
    r=rects[i];
    if(r.x-EPS<=px&&px<=r.x1+EPS&&r.y-EPS<=py&&py<=r.y1+EPS){return r.fill;}
  }
  return PAPER;
}
/* A rect delimits a region only where it changes the colour under it. The
   comparison is against the innermost EARLIER rect that WHOLLY CONTAINS this
   one, paper white if there is none.

   Containment and not a point probe, and this is the instrument bug that made
   the first draft of this function report a 253pt box where the printed box is
   87pt: a vertical divider's centre lands on the horizontal rule it crosses,
   which is also black, so a centre probe called every crossing divider
   invisible. Containment cannot be fooled that way -- a crossing rect does not
   contain the rect it crosses.

   The residual error is one-directional and stated: a rect only PARTLY over a
   different colour is kept. That over-reports walls, which can only shrink a
   box, so it can turn a `fits` into an `over` and never the reverse. It cannot
   hide a defect. */
function visibleRects(rects){
  var out=[],i,j,r,q,under;
  for(i=0;i<rects.length;i++){
    r=rects[i];under=PAPER;
    for(j=i-1;j>=0;j--){
      q=rects[j];
      if(q.x<=r.x+EPS&&q.y<=r.y+EPS&&q.x1>=r.x1-EPS&&q.y1>=r.y1-EPS){
        under=q.fill;break;
      }
    }
    if(r.fill!==under){out.push(r);}
  }
  return out;
}
/* What fraction of [lo,hi] the union of these intervals inks. */
function coverage(spans,lo,hi){
  if(hi<=lo){return 1;}
  spans.sort(function(a,b){return a[0]-b[0];});
  var total=0,cursor=lo,i,a,b;
  for(i=0;i<spans.length;i++){
    a=Math.max(spans[i][0],lo);b=Math.min(spans[i][1],hi);
    if(b<=cursor){continue;}
    total+=b-Math.max(a,cursor);cursor=Math.max(cursor,b);
  }
  return total/(hi-lo);
}
/* F220: whether a vertical (L/R) member's ink actually spans the box it is
   being asked to bound, along Y -- as opposed to merely grazing it at one
   end while running on past the other, which is the shape of a rule that
   belongs to a taller structure and happens to graze this box, not one
   drawn for it. 1604cf-2008 p1c33's column-header row is the measured case:
   its six interior verticals stop 9.6pt short of the cell's own bottom
   rule, so a taxpayer-facing box never closes there -- but the DATA
   table's column dividers immediately below start at that exact same y
   (271, a coincidence of where BIR's own artwork happens to break the
   stroke) and run on for another 116pt through the real rows beneath. A
   probe landing in the 8pt gap between the header's own closing rule and
   the first real row divider closes a box using those dividers as its L/R
   walls: `Math.abs(r.x1-L)<=TOL` is satisfied (their inner face is exactly
   L), coverage is 100% (they run the box's whole height and far past it),
   and nothing before this caught it. 2550m-2007 p2's column-header block is
   the same shape from the other side: `boxAt` finds walls purely from ink
   with no notion of "this region is unsegmented", so 40+59 phantom marks
   over the two forms alone (measured; 99 of the corpus's 108).

   Y only, never X: a comb's shared top/bottom rail is ONE rule under every
   compartment it writes, so the first and last compartment's own T/B member
   is, BY DESIGN, flush with that one compartment's own edge on one side (its
   own end of the comb) while running the width of every other compartment
   on the other -- 2551Q p1c9-s0's own top rail runs 0.48pt short of flush on
   its own left and 57pt past flush on its right, which is exactly this
   asymmetric shape and is not a defect. Applying this check to T/B rejected
   every edge compartment of every comb in the corpus (measured: unboxed rose
   from 240 to 29,450). A vertical divider carries no such shared-rail
   population -- the corpus's dividers are drawn per column, not per comb --
   so the asymmetry the L/R check catches is never this one.
   A member fully capable of covering [lo,hi] on its own -- reaching to
   within TOL of BOTH ends -- is genuine in exactly two shapes: flush at BOTH
   ends (a standalone box's own frame, drawn for exactly that box: a
   checkbox square, a signature box) or flush at NEITHER (an interior
   divider running through a row it does not begin or end, which is every
   real multi-row divider in the corpus, verified against 1604cf-2008's own
   real first and last remittance rows -- both overshoot their own row by
   several points on the far side, never landing flush). Flush at EXACTLY
   one end and running CLEARLY past the other is neither: it is a rule whose
   far end belongs to some OTHER structure and whose near end merely happens
   to coincide with this box's edge, which is precisely 1604cf-2008's and
   2550m-2007's shape above.

   "Clearly" has to be scaled to the box, not a fixed point value, or this
   over-fires: comb dividers routinely overshoot their own writing band by a
   few tenths of a point on one side only (a drawn tick's own serif, not a
   second structure) -- measured directly, 2551Q p1c9-s0's own left divider
   overshoots its box's top by 0.48pt and is flush at the bottom, and a fixed
   TOL=0.25pt margin called that "asymmetric" and cost 9,421 real inputs
   their box corpus-wide before this was caught. The corpus's two genuine
   phantom shapes overshoot by 30 to 116pt against an 7.68-8.64pt box -- 4 to
   15 times the box's own span -- while every regressed comb tick overshoot
   measured was under 3% of its own box's span. The margin is the box's own
   span: an overshoot smaller than the box itself is the box's own stroke
   detail, and only an overshoot AT LEAST AS LARGE AS the box itself is big
   enough to be another row or column's worth of a different structure.
   A member that does not reach both ends at all is a partial contributor to
   `coverage()` and is untouched -- this guards only the single-member,
   full-coverage case the corpus evidence measures.

   Applied only in `strict` mode (`allBoxes`' own candidate search, never the
   per-input lookup `pageCensus` makes for a REAL input's own centre). That
   split is not a shortcut, it is load-bearing: any box a real input resolves
   to is re-added to the candidate set from that non-strict lookup regardless
   of what `allBoxes` found (see `pageCensus`, "if(!boxes[key])"), so this
   function can only ever remove candidates that no input occupies -- the
   vacant population is the only population `strict` can change, by
   construction, not by care taken while tuning the margin above. */
function crossesCleanly(a0,a1,lo,hi){
  if(a0>lo+TOL||a1<hi-TOL){return true;}
  var margin=Math.max(TOL,hi-lo);
  var flushLo=(lo-a0)<=margin,flushHi=(a1-hi)<=margin;
  return flushLo===flushHi;
}
/* The four walls of a candidate box: which rects form each one, how much of
   the side they ink, and where the wall's OUTER face is. The outer face is the
   far side of the ink, and it is the whole tolerance story: "the box" is
   ambiguous to within the stroke that draws it, so an input is at home
   anywhere between the inner rectangle and this one. `strict` is F220's own
   flag -- see `crossesCleanly`'s docstring for why it may only ever narrow
   the vacant population, never an input's own box. */
function wallsOf(L,T,R,B,vis,banned,strict){
  var walls={L:null,T:null,R:null,B:null},side,i,r,spans,members,outer,lo,hi;
  var sides=["L","T","R","B"];
  for(var s=0;s<4;s++){
    side=sides[s];spans=[];members=[];outer=null;
    lo=(side==="L"||side==="R")?T:L;
    hi=(side==="L"||side==="R")?B:R;
    for(i=0;i<vis.length;i++){
      r=vis[i];
      if(banned[r.n]){continue;}
      if(side==="L"&&Math.abs(r.x1-L)<=TOL&&r.y<B&&r.y1>T){
        if(strict&&!crossesCleanly(r.y,r.y1,lo,hi)){continue;}
        spans.push([r.y,r.y1]);members.push(r.n);
        outer=outer===null?r.x:Math.min(outer,r.x);
      }else if(side==="R"&&Math.abs(r.x-R)<=TOL&&r.y<B&&r.y1>T){
        if(strict&&!crossesCleanly(r.y,r.y1,lo,hi)){continue;}
        spans.push([r.y,r.y1]);members.push(r.n);
        outer=outer===null?r.x1:Math.max(outer,r.x1);
      }else if(side==="T"&&Math.abs(r.y1-T)<=TOL&&r.x<R&&r.x1>L){
        spans.push([r.x,r.x1]);members.push(r.n);
        outer=outer===null?r.y:Math.min(outer,r.y);
      }else if(side==="B"&&Math.abs(r.y-B)<=TOL&&r.x<R&&r.x1>L){
        spans.push([r.x,r.x1]);members.push(r.n);
        outer=outer===null?r.y1:Math.max(outer,r.y1);
      }
    }
    walls[side]={cover:coverage(spans,lo,hi),members:members,
                 outer:outer===null?(side==="L"?L:side==="R"?R:side==="T"?T:B):outer};
  }
  return walls;
}
/* The smallest CLOSED printed rectangle around a point, or null.

   Closed is the whole of it. A side inked over less than WALL of its length is
   a guide tick, not a wall; it is banned and the search widens, which is what
   resolves a comb slot to the comb cell its guides subdivide rather than to a
   slot with two open sides. Without that step 2550M reported nine "no printed
   box" fields that are sitting in perfectly good printed boxes.

   `strict`, when true, additionally refuses a wall member that does not
   `crossesCleanly` (F220) -- passed true only from `allBoxes`' own candidate
   search, never from a lookup keyed to a real input's own centre. Default
   false/undefined preserves this function's exact prior behaviour for every
   other caller. */
function boxAt(cx,cy,vis,strict){
  var banned={},round,i,r,L,T,R,B,walls,worst,worstSide,sides=["L","T","R","B"],s;
  for(round=0;round<ROUNDS;round++){
    L=null;T=null;R=null;B=null;
    for(i=0;i<vis.length;i++){
      r=vis[i];
      if(banned[r.n]){continue;}
      /* Inner faces only, so no stroke-thickness question is ever asked. */
      if(r.y-EPS<=cy&&cy<=r.y1+EPS){
        if(r.x1<=cx+EPS&&(L===null||r.x1>L)){L=r.x1;}
        if(r.x>=cx-EPS&&(R===null||r.x<R)){R=r.x;}
      }
      if(r.x-EPS<=cx&&cx<=r.x1+EPS){
        if(r.y1<=cy+EPS&&(T===null||r.y1>T)){T=r.y1;}
        if(r.y>=cy-EPS&&(B===null||r.y<B)){B=r.y;}
      }
    }
    if(L===null||T===null||R===null||B===null||R-L<=EPS||B-T<=EPS){return null;}
    walls=wallsOf(L,T,R,B,vis,banned,strict);
    worst=2;worstSide=null;
    for(s=0;s<4;s++){
      if(walls[sides[s]].cover<worst){worst=walls[sides[s]].cover;worstSide=sides[s];}
    }
    if(worst>=WALL){return {L:L,T:T,R:R,B:B,walls:walls};}
    for(i=0;i<walls[worstSide].members.length;i++){
      banned[walls[worstSide].members[i]]=true;
    }
  }
  return null;
}
/* Exact area of a union of axis-aligned rectangles, by coordinate sweep. Forty
   comb slots tiling one printed cell fill it; forty times the area of one slot
   would double-count every shared edge and is not the same question. */
function unionArea(list){
  var xs=[],i,j,x0,x1,spans,cursor,covered,total=0;
  for(i=0;i<list.length;i++){xs.push(list[i][0]);xs.push(list[i][2]);}
  xs.sort(function(a,b){return a-b;});
  for(i=0;i<xs.length-1;i++){
    x0=xs[i];x1=xs[i+1];
    if(x1-x0<=0){continue;}
    spans=[];
    for(j=0;j<list.length;j++){
      if(list[j][0]<=x0&&list[j][2]>=x1){spans.push([list[j][1],list[j][3]]);}
    }
    spans.sort(function(a,b){return a[0]-b[0];});
    cursor=-1e9;covered=0;
    for(j=0;j<spans.length;j++){
      if(spans[j][1]<=cursor){continue;}
      covered+=spans[j][1]-Math.max(spans[j][0],cursor);
      cursor=Math.max(cursor,spans[j][1]);
    }
    total+=covered*(x1-x0);
  }
  return total;
}
function boxKey(box){
  return [box.L,box.T,box.R,box.B].map(function(v){return v.toFixed(2);}).join("|");
}
/* Every closed printed rectangle on the page, found by planting a probe just
   outside each crossing of a horizontal and a vertical rect. A box's corner IS
   such a crossing, so no closed box on the sheet is unreachable this way, and
   the probe count is proportional to the number of boxes rather than to the
   square of the number of rects. Probes are snapped to a half-point grid so
   two rules that cross twice do not pay twice.

   `boxAt` is called `strict` here (F220): this is the candidate set the
   vacant census is built from, and nowhere else -- `pageCensus`'s own
   per-input lookup calls `boxAt` non-strict, and re-adds any box a real
   input resolves to regardless of what this function found (see
   `crossesCleanly`'s docstring for why that ordering makes the stricter
   search safe to apply here alone). */
function allBoxes(vis){
  var horizontal=[],vertical=[],i,j,h,v,seen={},boxes={},px,py,key,box,probes=[];
  for(i=0;i<vis.length;i++){
    (vis[i].x1-vis[i].x>=vis[i].y1-vis[i].y?horizontal:vertical).push(vis[i]);
  }
  for(i=0;i<horizontal.length;i++){
    h=horizontal[i];
    for(j=0;j<vertical.length;j++){
      v=vertical[j];
      if(v.x1<h.x-PROBE||v.x>h.x1+PROBE||v.y1<h.y-PROBE||v.y>h.y1+PROBE){continue;}
      var xs=[v.x-PROBE,v.x1+PROBE],ys=[h.y-PROBE,h.y1+PROBE],a,b;
      for(a=0;a<2;a++){for(b=0;b<2;b++){
        px=Math.round(xs[a]*2)/2;py=Math.round(ys[b]*2)/2;
        key=px+","+py;
        if(seen[key]){continue;}
        seen[key]=true;probes.push([px,py]);
      }}
    }
  }
  for(i=0;i<probes.length;i++){
    box=boxAt(probes[i][0],probes[i][1],vis,true);
    if(box===null){continue;}
    key=boxKey(box);
    if(!boxes[key]){boxes[key]=box;}
  }
  return boxes;
}
/* PURE-GEOMETRY-END */

/* ---- one page --------------------------------------------------------- */

/* The census, and ONLY the census: no element created, no attribute set, no
   listener registered. This is what makes it safe to expose unconditionally,
   below, before the token is even checked. */
function pageCensus(page){
  var frame=page.getBoundingClientRect();
  var widthPt=parseFloat(page.style.width);
  if(!(widthPt>0)||!(frame.width>0)){return null;}
  var ptPerPx=widthPt/frame.width;
  /* `rawRects`, not `vis`, is what the vacant probe below reads. `vis` marks
     a rect invisible when it does not change the colour under it, and that
     is a PER-RECT judgement against the nearest WHOLLY CONTAINING earlier
     rect -- exactly right for wall-finding (an edge nobody could perceive
     cannot be a wall), and exactly wrong for a point probe. 1701-2018 p2's
     own Schedule-1 squares (F210) proved it: the white knockout that paints
     "write here" over the grey band is the SAME colour as paper, and no
     single earlier rect wholly contains its 31.44x10.8pt extent (only a
     narrower tint fragment overlaps part of it), so visibleRects() drops it
     -- correctly, nothing there needs to be perceived as an edge -- but
     paintAt() then found the tint UNDER it instead of the knockout ON TOP
     of it, and reported a real vacant box as tinted decoration. paintAt()
     already does its own point-in-paint-order search, so it needs the full
     paint stack, not the pre-filtered one. */
  var rawRects=ruleRects(page,frame,ptPerPx);
  var vis=visibleRects(rawRects);
  /* Tint rects stop being wall candidates here (F213): they still PAINT --
     the probe below still reads them, in `rawRects` -- but they can no
     longer BOUND a box. */
  var wallVis=vis.filter(function(r){return !isTintTone(r.fill);});
  var boxes=allBoxes(wallVis);
  var inputs=[],nodes=page.querySelectorAll("input.fi"),i,key,box;
  for(i=0;i<nodes.length;i++){inputs.push(ptRect(nodes[i],frame,ptPerPx));}
  var texts=[],tnodes=page.querySelectorAll(".t");
  for(i=0;i<tnodes.length;i++){texts.push(ptRect(tnodes[i],frame,ptPerPx));}

  /* Each input is judged in the smallest closed printed box its CENTRE lies
     in. The centre and not a corner: an input that overflows its box has
     corners in the neighbouring boxes, and asking which box a corner is in
     would answer with the box next door. */
  var groups={},unboxed=[];
  for(i=0;i<inputs.length;i++){
    box=boxAt((inputs[i].x+inputs[i].x1)/2,(inputs[i].y+inputs[i].y1)/2,wallVis);
    if(box===null){unboxed.push(inputs[i]);continue;}
    key=boxKey(box);
    if(!boxes[key]){boxes[key]=box;}
    if(!groups[key]){groups[key]=[];}
    groups[key].push(inputs[i]);
  }

  var counts={},marks=[],inputStates=[],state;
  for(i=0;i<ORDER.length;i++){counts[ORDER[i]]=0;}
  counts.unboxed=unboxed.length;
  for(i=0;i<unboxed.length;i++){
    inputStates.push({id:unboxed[i].id,state:"unboxed",unreached:null});
  }
  for(key in groups){
    if(!Object.prototype.hasOwnProperty.call(groups,key)){continue;}
    box=boxes[key];
    var members=groups[key],w=box.walls,rects=[],over=0,m;
    for(m=0;m<members.length;m++){
      rects.push([members[m].x,members[m].y,members[m].x1,members[m].y1]);
      /* Beyond the OUTER face of the wall, plus the pipeline's own position
         tolerance. Inside the ink is not outside the box. */
      over=Math.max(over,w.L.outer-members[m].x-TOL,members[m].x1-w.R.outer-TOL,
                    w.T.outer-members[m].y-TOL,members[m].y1-w.B.outer-TOL);
    }
    /* The mirror image on the inside: erode the printed box to the far side of
       its own ink plus TOL, and ask whether the inputs reach all of what is
       left. `2*L - outer` is the far side of the left wall measured from the
       inner face the box is defined by; the direction is easy to invert, and
       inverting it made every field on both test forms report as too small,
       because the "eroded" box then included the walls themselves. A fixed AREA fraction would have been the wrong shape -- the inset
       is a fixed distance, so it costs a short box a far larger share of its
       area than a tall one, and a fraction would flag every small box on the
       sheet. */
    var eL=2*box.L-w.L.outer+TOL,eT=2*box.T-w.T.outer+TOL;
    var eR=2*box.R-w.R.outer-TOL,eB=2*box.B-w.B.outer-TOL;
    var shortfall=0,clipped=[],c;
    if(eR>eL&&eB>eT){
      for(m=0;m<rects.length;m++){
        c=[Math.max(rects[m][0],eL),Math.max(rects[m][1],eT),
           Math.min(rects[m][2],eR),Math.min(rects[m][3],eB)];
        if(c[2]>c[0]&&c[3]>c[1]){clipped.push(c);}
      }
      shortfall=1-unionArea(clipped)/((eR-eL)*(eB-eT));
    }
    /* Reported against the printed box itself, not against the eroded one: a
       reviewer asked "how much of this box can nobody type in" means the box
       they can see. */
    var unreached=1-unionArea(rects)/((box.R-box.L)*(box.B-box.T));
    state=over>0?"over":(shortfall>EPS?"small":"fits");
    counts[state]+=members.length;
    for(m=0;m<members.length;m++){
      inputStates.push({id:members[m].id,state:state,
                        unreached:state==="small"?unreached:null});
    }
    if(state!=="fits"){
      marks.push({state:state,L:box.L,T:box.T,R:box.R,B:box.B,unreached:unreached});
    }
  }

  /* The state the previous overlay could not express, because it iterated over
     inputs and a missing input is not something an input iteration visits. A
     printed box counts as vacant only when it holds no input AND no printed
     text -- a box with a label in it is a label, not a field somebody forgot --
     and only when it is big enough to write a character in, and only when its
     own interior does not resolve to GREY paint (F213: a box sitting on shaded
     decoration is not a forgotten field, and a box FILLED with a shading pad
     -- 0619-E's centavo separators -- is the pad, not a field; see
     isDecorPaint for why the interior band is wider than the stroke split.
     Knockout white and bare paper both still pass, which is what keeps
     F210's Schedule-1 squares blue). Boxes
     that contain another found box are containers, not cells, and are not
     reported: the sheet's outer frame encloses everything and is nobody's
     missing field. */
  var keys=Object.keys(boxes),k,other,vacant=0;
  for(i=0;i<keys.length;i++){
    box=boxes[keys[i]];
    if(groups[keys[i]]){continue;}
    if(box.R-box.L<MIN_BOX||box.B-box.T<MIN_BOX){continue;}
    var occupied=false;
    for(k=0;k<inputs.length;k++){
      if(inputs[k].x1>box.L+EPS&&inputs[k].x<box.R-EPS&&
         inputs[k].y1>box.T+EPS&&inputs[k].y<box.B-EPS){occupied=true;break;}
    }
    if(occupied){continue;}
    for(k=0;k<texts.length;k++){
      if(texts[k].x1>box.L+TOL&&texts[k].x<box.R-TOL&&
         texts[k].y1>box.T+TOL&&texts[k].y<box.B-TOL){occupied=true;break;}
    }
    if(occupied){continue;}
    for(k=0;k<keys.length;k++){
      if(k===i){continue;}
      other=boxes[keys[k]];
      if(other.L>=box.L-EPS&&other.T>=box.T-EPS&&
         other.R<=box.R+EPS&&other.B<=box.B+EPS){occupied=true;break;}
    }
    if(occupied){continue;}
    if(isDecorPaint(paintAt((box.L+box.R)/2,(box.T+box.B)/2,rawRects))){continue;}
    vacant++;
    marks.push({state:"vacant",L:box.L,T:box.T,R:box.R,B:box.B,unreached:1});
  }
  counts.vacant=vacant;
  return {page:page.id,counts:counts,inputs:inputStates,marks:marks,boxes:keys.length};
}
/* Every page's census, as one plain array of plain objects -- no DOM element
   anywhere in the return value, which is what makes it safe to hand back
   across Playwright's evaluate() boundary. This is the ONLY place the
   box/verdict logic runs: the visual overlay below calls this exact function
   too, so there is one source of truth for "what state is this input in" and
   "which printed boxes are vacant", never two. */
function census(){
  var pages=document.querySelectorAll(".page"),out=[],i,result;
  for(i=0;i<pages.length;i++){
    result=pageCensus(pages[i]);
    if(result!==null){out.push(result);}
  }
  return out;
}
/* Exposed BEFORE the token gate, deliberately: tab_check.py calls this on
   every generated form, most of which are never loaded with ?debug=fields at
   all. Everything it reaches (census -> pageCensus -> ruleRects/ptRect/the
   pure geometry above) only READS the document -- getBoundingClientRect,
   getComputedStyle, querySelectorAll -- so calling it changes nothing about
   what the page renders, which is the actual guarantee the gate below exists
   to protect. The gate itself still stands: nothing that can create an
   element, set an attribute or register a listener is reachable without the
   token, and the self-test asserts precisely that split rather than banning
   "the document" outright, which would ban this deliberate exception too. */
window.formgenFieldCensus=census;
/* Nothing above this line can MUTATE the document or register a listener, and
   nothing below it runs without the token. This is the byte-identical-render
   proof, narrowed to what it actually needs to prove: a page loaded without
   ?debug=fields, and never handed to window.formgenFieldCensus(), creates no
   element, sets no attribute and registers no listener, so its rendered
   layout is the layout it had before this script existed. */
if(!requested()){return;}
var CSS=__FIELD_DEBUG_CSS__;
/* fits/small/over/unboxed each count INPUTS (an input judged individually, or
   as part of a group sharing one printed box); vacant counts BOXES (a printed
   box with no input at all has no input to count). The label states which,
   because the two numbers answer different questions and the legend used to
   read as though they were the same kind of thing. */
var LABELS={fits:"input(s) fill their printed box",
            small:"input(s) smaller than their printed box",
            over:"input(s) cross a printed wall",
            unboxed:"input(s) with no printed box at all",
            vacant:"printed box(es) with no input, after tone filtering"};

/* ---- painting ----------------------------------------------------------- */

/* `aside` and `i`, never `div`: `.page:last-of-type{break-after:auto}` is the
   one structural selector in the emitted stylesheet, and a div appended to the
   sheet would take that match off the last page and add a page break after it.
   An element that cannot be a `.page` cannot do that. */
function layerOf(page){
  var layer=page.querySelector("[data-fg-layer]");
  if(!layer){
    layer=document.createElement("aside");
    layer.setAttribute("data-fg-layer","");
    page.appendChild(layer);
  }
  while(layer.firstChild){layer.removeChild(layer.firstChild);}
  return layer;
}
function paintPage(page,result){
  var layer=layerOf(page),i,mark,node,el;
  for(i=0;i<result.inputs.length;i++){
    el=document.getElementById(result.inputs[i].id);
    if(el){el.setAttribute("data-fg-field",result.inputs[i].state);}
  }
  for(i=0;i<result.marks.length;i++){
    mark=result.marks[i];
    node=document.createElement("i");
    node.setAttribute("data-fg-box",mark.state);
    node.setAttribute("data-fg-unreached",Math.round(mark.unreached*100)+"%");
    node.title=mark.state+": "+Math.round(mark.unreached*100)+"% of this printed "
      +"box is unreached";
    node.style.left=mark.L+"pt";node.style.top=mark.T+"pt";
    node.style.width=(mark.R-mark.L)+"pt";node.style.height=(mark.B-mark.T)+"pt";
    layer.appendChild(node);
  }
  /* One legend per page, inside the page, so a reviewer photographing a single
     page gets that page's numbers with it. A viewport-fixed legend showed the
     document's totals over whichever page happened to be scrolled to. */
  var legend=document.createElement("aside");
  legend.setAttribute("data-fg-legend","");
  legend.addEventListener("click",function(){legend.style.display="none";});
  /* <b>, not <div>, for the same reason the containers are <aside>: no
     element this overlay creates may be a div, so no div can ever end up as
     the last one in the sheet and take `.page:last-of-type` off the last
     page. */
  var head=document.createElement("b");
  head.textContent="?debug=fields — "+result.inputs.length+" input(s), "
    +result.boxes+" printed box(es)";
  legend.appendChild(head);
  for(i=0;i<ORDER.length;i++){
    var line=document.createElement("b"),swatch=document.createElement("span");
    swatch.setAttribute("data-fg-swatch",ORDER[i]);
    line.appendChild(swatch);
    line.appendChild(document.createTextNode(result.counts[ORDER[i]]+"  "
                                             +LABELS[ORDER[i]]));
    legend.appendChild(line);
  }
  var orangeNote=document.createElement("b");
  orangeNote.textContent="orange on a TIN comb is pre-printed constants plus "
    +"the comb's own outer-edge insets -- expected, not a defect.";
  legend.appendChild(orangeNote);
  var blueNote=document.createElement("b");
  blueNote.textContent="blue is a candidate missing input, after excluding "
    +"grey tint bands from what counts as a printed wall.";
  legend.appendChild(blueNote);
  layer.appendChild(legend);
}
function run(){
  var results=census(),totals={},report=[],i,s,j,k,result,page;
  for(i=0;i<ORDER.length;i++){totals[ORDER[i]]=0;}
  for(i=0;i<results.length;i++){
    result=results[i];
    page=document.getElementById(result.page);
    if(!page){continue;}
    paintPage(page,result);
    for(s=0;s<ORDER.length;s++){totals[ORDER[s]]+=result.counts[ORDER[s]];}
    for(j=0;j<result.inputs.length;j++){
      report.push({page:result.page,input:result.inputs[j].id,
                   state:result.inputs[j].state,unreached:result.inputs[j].unreached});
    }
    for(k=0;k<result.marks.length;k++){
      if(result.marks[k].state!=="vacant"){continue;}
      report.push({page:result.page,input:null,state:"vacant",unreached:1,
                   at:[result.marks[k].L.toFixed(1),result.marks[k].T.toFixed(1),
                       (result.marks[k].R-result.marks[k].L).toFixed(1),
                       (result.marks[k].B-result.marks[k].T).toFixed(1)].join(" ")});
    }
  }
  window.formgenFieldDebug.report=report;
  return totals;
}
function inject(){
  if(document.getElementById("formgen-field-debug")){return;}
  var style=document.createElement("style");
  style.id="formgen-field-debug";
  style.textContent=CSS;
  document.head.appendChild(style);
}
/* The fourth barrier, and it removes the marks as well as the rules: the
   overlay now creates ELEMENTS, and a media query that a transformed
   stylesheet dropped would leave those elements painting on paper. Nothing the
   overlay made survives into the print. */
function drop(){
  var style=document.getElementById("formgen-field-debug"),i,nodes;
  if(style&&style.parentNode){style.parentNode.removeChild(style);}
  nodes=document.querySelectorAll("[data-fg-layer]");
  for(i=0;i<nodes.length;i++){nodes[i].parentNode.removeChild(nodes[i]);}
  nodes=document.querySelectorAll("[data-fg-field]");
  for(i=0;i<nodes.length;i++){nodes[i].removeAttribute("data-fg-field");}
}
window.addEventListener("beforeprint",drop);
window.addEventListener("afterprint",function(){inject();run();});
function start(){inject();run();}
if(document.readyState==="complete"){start();}
else{window.addEventListener("load",start);}
window.formgenFieldDebug={refresh:run,boxAt:boxAt,report:[]};
})();""".replace("__FIELD_DEBUG_CSS__", json.dumps(FIELD_DEBUG_CSS)) \
        .replace("__RULE_POSITION_TOLERANCE_PT__", fmt(RULE_POSITION_TOLERANCE_PT)) \
        .replace("__RULE_WALL_COVERAGE__", fmt(RULE_WALL_COVERAGE)) \
        .replace("__FIELD_MIN_SIZE_PT__", fmt(FIELD_MIN_SIZE_PT)) \
        .replace("__RULE_WALL_TINT_SPLIT_GRAY__", fmt(RULE_WALL_TINT_SPLIT_GRAY))


# ---------------------------------------------------------------------------
# The tab-walk debug viewer (T3)
# ---------------------------------------------------------------------------
#
# `?debug=tab` renders a T2 run's own artifact -- forms/review/<slug>/
# tab.json -- back onto the live document: green/red boxes and the tab
# sequence number, from the JSON's own recorded pt geometry. It reads NOTHING
# from the field layer (no `.c`, `.s`, `.layer-cells`, no `closest(`), the
# same independence rule FIELD_DEBUG_JS follows and for the same reason: an
# overlay that derives its expectation from the thing it is checking cannot
# fail. Here the "different producer" is tab_check.py's own recorded walk,
# not the rule layer, but the discipline is identical.
#
# It ships behind its own token (`debug=tab`, not `debug=fields`) so the two
# overlays cannot be requested by accident together or interfere with each
# other's DOM, and it follows the same four barriers: gated before any DOM
# access; CSS scoped to @media screen with an @media print neutraliser;
# print removes and reloads its own marks; and it creates no `div`.
#
# `tab.json` is fetched by a RELATIVE path worked out from the document's own
# location, because the served root moves: `forms/<slug>/index.html` needs
# `../review/<slug>/tab.json`, `forms/extra/<slug>/index.html` needs
# `../../review/<slug>/tab.json`, and both are on one server root only after
# `review-serve` was re-rooted at `forms/` (T3). Two failure modes are
# distinguished and each gets its own actionable hint, never a silent blank
# page: `file://` (fetch cannot read local JSON in Chromium) says to run
# `just review-serve`; a fetch that resolves but 404s says to run
# `just tab-check <slug>` first.

TAB_DEBUG_SCREEN_CSS = (
    '[data-tabdbg-layer]{position:absolute;left:0;top:0;right:0;bottom:0;'
    'pointer-events:none;z-index:2147483645}'
    '[data-tabdbg-mark]{position:absolute;box-sizing:border-box;display:block}'
    '[data-tabdbg-mark="green"]{outline:1.5px solid #0a8a3e;'
    'background:rgba(10,138,62,.18)}'
    '[data-tabdbg-mark="red"]{outline:1.5px solid #d32f2f;'
    'background:rgba(211,47,47,.22)}'
    '[data-tabdbg-mark] b{position:absolute;left:0;top:-7pt;color:#fff;'
    'font:bold 6.5pt/1.4 Arial,Helvetica,sans-serif;padding:0.5pt 2pt;'
    'border-radius:1.5pt;white-space:nowrap}'
    '[data-tabdbg-mark="green"] b{background:#0a8a3e}'
    '[data-tabdbg-mark="red"] b{background:#d32f2f}'
    '[data-tabdbg-hint]{position:fixed;left:8px;top:8px;z-index:2147483647;'
    'background:#fff3cd;color:#664d03;border:1px solid #664d03;'
    'padding:8px 10px;font:12px/1.4 system-ui,-apple-system,sans-serif;'
    'max-width:60ch;box-shadow:0 1px 4px rgba(0,0,0,.35)}'
)
TAB_DEBUG_PRINT_CSS = (
    '[data-tabdbg-layer]{display:none!important}'
    '[data-tabdbg-hint]{display:none!important}'
)
TAB_DEBUG_CSS = ("@media screen{" + TAB_DEBUG_SCREEN_CSS + "}"
                 "@media print{" + TAB_DEBUG_PRINT_CSS + "}")

TAB_DEBUG_JS = r"""(function(){
"use strict";
/* An exact token, not a substring, and a different one from ?debug=fields so
   the two overlays are never both requested by one query string. */
var TOKEN="debug=tab";
function requested(){
  var query=String(window.location.search||"");
  if(query.charAt(0)==="?"){query=query.slice(1);}
  var parts=query.split("&");
  for(var i=0;i<parts.length;i++){
    if(parts[i]===TOKEN){return true;}
  }
  return false;
}
/* Nothing above this line has touched the document, and nothing below it runs
   without the token -- the same byte-identical-render proof FIELD_DEBUG_JS
   makes, re-proved here for this script by tab_debug_assertions(). */
if(!requested()){return;}
var CSS=__TAB_DEBUG_CSS__;

/* TAB-JSON-TARGET-BEGIN -- pure, no DOM but for the one location.pathname
   read: emit.py's self-test extracts this exact span and runs it under node
   against synthetic pathnames, proving the ../ vs ../../ split against both
   shapes forms/ actually contains rather than merely describing it.
   The slug and the tab.json path are read from the document's OWN location,
   never hard-coded and never read from any element on the page: `forms/
   <slug>/index.html` needs one `../`, `forms/extra/<slug>/index.html` needs
   two, and the only reliable signal for which is the path itself -- the
   directory literally named "extra" immediately above the slug. */
function tabJsonTarget(){
  var raw=String(window.location.pathname||"").split("/");
  var segments=[];
  for(var i=0;i<raw.length;i++){if(raw[i].length){segments.push(raw[i]);}}
  if(segments.length&&/\.html?$/i.test(segments[segments.length-1])){
    segments.pop();
  }
  var slug=segments.length?segments[segments.length-1]:"";
  var extra=segments.length>=2&&segments[segments.length-2]==="extra";
  return {slug:slug,path:(extra?"../../review/":"../review/")+slug+"/tab.json"};
}
/* TAB-JSON-TARGET-END */
function layerOf(page){
  var layer=page.querySelector("[data-tabdbg-layer]");
  if(!layer){
    layer=document.createElement("aside");
    layer.setAttribute("data-tabdbg-layer","");
    page.appendChild(layer);
  }
  return layer;
}
function hint(message){
  var box=document.createElement("aside");
  box.setAttribute("data-tabdbg-hint","");
  box.textContent=message;
  box.addEventListener("click",function(){box.style.display="none";});
  document.body.appendChild(box);
}
/* Draws exactly what tab.json recorded: no field-layer read, no re-derived
   geometry, no re-judged verdict. `inputs[i].verdict` is one of "green",
   "red-skipped" or "red-order" (tab_check.py's own three states); anything
   other than "green" paints red here, because a reviewer scanning for
   trouble does not need the two red reasons told apart by colour. */
function paint(report){
  var byPage={},i,inp;
  for(i=0;i<report.inputs.length;i++){
    inp=report.inputs[i];
    (byPage[inp.page]=byPage[inp.page]||[]).push(inp);
  }
  var pages=document.querySelectorAll(".page"),p,m,pageIndex,marks,node,chip;
  for(p=0;p<pages.length;p++){
    m=/^page-(\d+)$/.exec(pages[p].id||"");
    if(!m){continue;}
    pageIndex=parseInt(m[1],10);
    marks=byPage[pageIndex]||[];
    var layer=layerOf(pages[p]);
    while(layer.firstChild){layer.removeChild(layer.firstChild);}
    for(i=0;i<marks.length;i++){
      inp=marks[i];
      node=document.createElement("i");
      node.setAttribute("data-tabdbg-mark",inp.verdict==="green"?"green":"red");
      node.style.left=inp.x_pt+"pt";node.style.top=inp.y_pt+"pt";
      node.style.width=inp.w_pt+"pt";node.style.height=inp.h_pt+"pt";
      node.title=inp.id+": "+inp.verdict
        +(inp.reached_at?(" (reached #"+inp.reached_at+")"):" (never reached)");
      chip=document.createElement("b");
      chip.textContent=inp.reached_at?String(inp.reached_at):"×";
      node.appendChild(chip);
      layer.appendChild(node);
    }
  }
}
function drop(){
  var nodes=document.querySelectorAll("[data-tabdbg-layer]"),i;
  for(i=0;i<nodes.length;i++){nodes[i].parentNode.removeChild(nodes[i]);}
}
function start(){
  var style=document.createElement("style");
  style.id="formgen-tab-debug";
  style.textContent=CSS;
  document.head.appendChild(style);
  if(window.location.protocol==="file:"){
    hint("?debug=tab needs a server -- fetch() cannot read tab.json over "
      +"file://. Run: just review-serve, then reload this page from "
      +"http://127.0.0.1:4190/.");
    return;
  }
  var target=tabJsonTarget();
  fetch(target.path).then(function(resp){
    if(!resp.ok){
      hint("no tab.json for \""+target.slug+"\" (fetch " + resp.status
        +"). Run: just tab-check "+target.slug+", then reload.");
      return null;
    }
    return resp.json();
  }).then(function(report){
    if(report){paint(report);}
  }).catch(function(err){
    hint("fetch of "+target.path+" failed ("+err+"). Run: just review-serve, "
      +"then just tab-check "+target.slug+".");
  });
}
window.addEventListener("beforeprint",drop);
window.addEventListener("afterprint",start);
if(document.readyState==="complete"){start();}
else{window.addEventListener("load",start);}
})();""".replace("__TAB_DEBUG_CSS__", json.dumps(TAB_DEBUG_CSS))



class Options:
    __slots__ = ("rule_backend", "fonts_dir", "assets_dir", "out_dir", "band_rows",
                 "title", "guide_plan", "document", "guide_layout", "guide_href",
                 "form_href", "guide_pdf_dir", "guide_sources")

    def __init__(self, rule_backend: str, fonts_dir: str, assets_dir: str,
                 out_dir: pathlib.Path | None, band_rows: int | None,
                 title: str | None, guide_plan: dict[str, Any] | None = None,
                 document: str = "form", guide_layout: str = "reflow",
                 guide_href: str = "guide.html", form_href: str = "index.html",
                 guide_pdf_dir: str = "guides",
                 guide_sources: dict[str, dict[str, Any]] | None = None) -> None:
        self.rule_backend = rule_backend
        self.fonts_dir = fonts_dir
        self.assets_dir = assets_dir
        self.out_dir = out_dir
        self.band_rows = band_rows
        self.title = title
        self.guide_plan = guide_plan
        self.document = document
        self.guide_layout = guide_layout
        self.guide_href = guide_href
        self.form_href = form_href
        self.guide_pdf_dir = guide_pdf_dir
        # Keyed by PDF file name, which is what the guide plan lists. Only the
        # guide document reads it; the form side has no standalone PDFs.
        self.guide_sources = dict(guide_sources or {})


def _font_face_key(family: str, css_style: str, font_weight: str,
                   font_file: str) -> tuple[str, str, str, str]:
    return (family, css_style, font_weight, pathlib.PurePosixPath(font_file).name)


def _field_font_face(plan: dict[str, Any], fields: FieldPlan) -> dict[str, Any] | None:
    """Return the shipped face used by editable fields, if one exists.

    FieldFace deliberately keeps only the key and the CSS declarations needed
    by the input. The plan remains the authority for the file and @font-face
    descriptor, so a field cannot silently fall back to a platform face merely
    because no printed run on this document happened to use the modal face.
    """
    if not fields or fields.face is None:
        return None
    for face in plan.get("faces", ()):
        if face.get("face_key") == fields.face.face_key:
            if (face.get("status") == "resolved"
                    and face.get("css_family") and face.get("font_file")):
                return face
            return None
    return None


def _font_href(font_file: str, options: Options) -> str:
    name = pathlib.PurePosixPath(font_file).name
    return f"{options.fonts_dir.rstrip('/')}/{name}" if options.fonts_dir else name


def font_preload_hrefs(field_face: dict[str, Any] | None,
                       visible_font_files: set[str], options: Options) -> tuple[str, ...]:
    """Preload a field-only face so the isolated audit observes its request.

    An empty input does not make Chromium request its font during the print
    render. If that face is not also used by visible printed text, the CSS would
    otherwise retain a dependency that the request-closure proof correctly
    rejects. Preload only that missing file; visible text already loads the
    other case, and a second request would be noise rather than evidence.
    """
    if field_face is None or not field_face.get("font_file"):
        return ()
    name = pathlib.PurePosixPath(str(field_face["font_file"])).name
    if name in visible_font_files:
        return ()
    return (_font_href(str(field_face["font_file"]), options),)


def font_face_css(styles: dict[tuple[int, int], RunStyle], options: Options,
                  warnings: list[str],
                  extra_faces: Sequence[dict[str, Any]] = ()) -> str:
    """@font-face for exactly the faces the emitted runs actually reference.

    Keyed by weight as well as by family and style, and the weight *descriptor*
    comes from the plan rather than being a constant. A variable file legitimately
    covers `100 900` on its own; a static family keeps each weight in a separate
    file, and declaring one of those over the whole range makes Chromium serve
    that single file for every weight and synthesise the rest -- emboldened
    outlines with advances no measurement in the plan covers.
    """
    used: dict[tuple[str, str, str, str], str] = {}
    for style in styles.values():
        if style.font_family and style.font_file:
            key = _font_face_key(style.font_family, style.css_style,
                                 style.font_face_weight, style.font_file)
            used.setdefault(key, style.font_file)
    for face in extra_faces:
        family = str(face.get("css_family") or "")
        font_file = str(face.get("font_file") or "")
        if not family or not font_file:
            continue
        css_style = str(face.get("css_style") or "normal")
        font_weight = str((face.get("font_face") or {}).get("weight") or "100 900")
        used.setdefault(_font_face_key(family, css_style, font_weight, font_file),
                        font_file)
    blocks = []
    for (family, css_style, font_weight, _name), font_file in sorted(used.items()):
        name = pathlib.PurePosixPath(font_file).name
        href = _font_href(font_file, options)
        if options.out_dir is not None and not (options.out_dir / options.fonts_dir / name).is_file():
            warnings.append(
                f"missing font file {href}: the document references it but it is not "
                f"beside the output, so Chromium will fall back to a platform face and "
                f"every advance in the plan becomes a claim about the wrong font")
        blocks.append(
            f'@font-face{{font-family:"{family}";font-style:{css_style};'
            f'font-weight:{font_weight};font-display:block;'
            f'src:url("{href}") format("woff2")}}')
    return "\n".join(blocks)


def page_css(ir: dict[str, Any]) -> str:
    """@page from the PDF's own MediaBox, per page when they differ.

    Never Letter, never A4, never a constant: 2551Q is 612x936pt, 0619E is
    612x792 and others are 612x1008. A single hardcoded size would move every
    coordinate on 34 of the 35 forms.
    """
    paper = ir["paper"]
    lines = [f'@page{{size:{fmt(paper["width_pt"])}pt {fmt(paper["height_pt"])}pt;margin:0}}']
    if not paper.get("uniform", True):
        for page in ir["pages"]:
            lines.append(f'@page page-{page["index"]}{{size:{fmt(page["width_pt"])}pt '
                         f'{fmt(page["height_pt"])}pt;margin:0}}')
            lines.append(f'.page-{page["index"]}{{page:page-{page["index"]}}}')
    return "\n".join(lines)


def cells_in_tab_order(cells: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """The order `tab_check.py` grades: lattice `data-row`, then left edge.

    Focus order is DOM order (`input.fi` has no `tabindex`). Lattice cell
    ids are materialisation order, which is not that key — a later-id comb
    on an earlier row (1601EQ `p1c9` at row 2, left 522pt, after `p1c10`–
    `p1c17` on row 3) is the Stage 1 corpus's 21/53 tab-order fail. Sorting
    each `layer-cells` run here, without moving growable-band splices, is
    what makes the walk match the grade.
    """
    return sorted(cells, key=lambda cell: (int(cell["row"]), float(cell["x0"]),
                                           str(cell["id"])))


def emit_page(page_ir: dict[str, Any], page_layout: dict[str, Any],
              styles: dict[tuple[int, int], RunStyle], backend: RuleBackend,
              options: Options, band_blobs: list[dict[str, Any]],
              warnings: list[str], split: PageSplit = WHOLE_PAGE,
              fields: FieldPlan | None = None,
              used_style_keys: set[tuple[int, int]] | None = None) -> str:
    index = int(page_ir["index"])
    cells_by_id = {c["id"]: c for c in page_layout["cells"]}
    runs = page_ir["text_runs"]
    runs_by_id = {run_id(index, i): run for i, run in enumerate(runs)}

    plans = [build_band_plan(band, page_ir, cells_by_id) for band in page_layout["growable"]]
    band_rule_ids = {rid for plan in plans for rid in plan.rule_ids}
    band_cell_ids = {cid for plan in plans for cid in plan.cell_ids}
    band_run_ids = {rid for plan in plans for rid in plan.run_ids}

    # A band is indivisible and always the form's, so its members leave the
    # guide's claim before anything is filtered, and the guide document drops
    # the bands themselves.
    split = split.without_band(band_rule_ids, band_cell_ids,
                               {_run_index_of(rid) for rid in band_run_ids})
    if split.guide_side:
        plans = []

    # -- rule layer, bottom: the source's own content-stream order ------------
    # Every rule, area fill and image carries the index of the op that painted
    # it, so the layer is emitted in that order. The previous fills ->
    # decorative -> structural bucket order was a guess about z-order, and it
    # was wrong wherever the source paints a *lighter* rect after a darker one:
    # 2552 draws the white knockout inside each checkbox at op 4774 and the
    # grey row separator crossing it at op 172, so bucketing put a light-grey
    # line through every checkbox on the sheet.
    painted: list[tuple[tuple[int, int, float, float, str], str, Any]] = []
    for fill_index, fill in enumerate(page_ir["area_fills"]):
        if split.keep_fill(fill_index):
            painted.append((paint_key(fill, ""), "rect",
                            Rect.from_box(split.clipped(fill, "area_fill",
                                                        f"#{fill_index}"))))
    for rule in page_ir["rules"]:
        if rule["id"] not in band_rule_ids and split.keep_rule(rule["id"]):
            clipped = split.clipped(rule, "rule", rule["id"])
            painted.append((paint_key(rule, rule["id"]), "rect",
                            Rect.from_box(clipped, rule["id"])))
    for image_index, image in enumerate(page_ir["images"]):
        if split.keep_image(image_index):
            painted.append((paint_key(image, image["sha256"]), "image", image))
    # Paths are the third kind of ink, and they paint in the same one order the
    # rules and fills do: 0605 draws its "write here" markers and its
    # pre-printed decimal points as filled paths interleaved with the boxes they
    # sit in, so bucketing them into a layer of their own would be the same
    # z-order guess the fills -> greys -> black bucketing was.
    for path in page_ir.get("paths", ()):
        if split.keep_path(path["id"]):
            painted.append((paint_key(path, str(path["id"])), "path", path))
    painted.sort(key=operator.itemgetter(0))

    parts = [f'<div class="page page-{index}" id="page-{index}" '
             f'style="width:{fmt(page_ir["width_pt"])}pt;'
             f'height:{fmt(page_ir["height_pt"])}pt">']

    parts.append(backend.open_page(page_ir))
    # Consecutive rects of the same role share one group, so the markup still
    # reads as labelled layers without the grouping dictating the paint order.
    run: list[Rect] = []
    run_role = ""
    for _key, kind, payload in painted:
        if kind == "rect" and (not run or payload.role == run_role):
            run_role = payload.role
            run.append(payload)
            continue
        parts.append(backend.rects(run, run_role))
        run = []
        if kind == "image":
            parts.append(image_markup(payload, backend, options, warnings))
        elif kind == "path":
            parts.append(backend.path(payload, page_ir))
        else:
            run_role = payload.role
            run = [payload]
    parts.append(backend.rects(run, run_role))
    for plan in plans:
        rows = plan.capacity if options.band_rows is None else min(options.band_rows,
                                                                   plan.capacity)
        # The rects are the container's direct children, matching what the JS
        # renderer produces, so a re-render is a like-for-like replacement.
        parts.append(backend.band_container(plan.band["id"], band_rects(plan, rows)))
    parts.append(backend.close_page())

    # -- text layer ----------------------------------------------------------
    parts.append('<div class="layer-text">')
    for run_index, run in enumerate(runs):
        rid = run_id(index, run_index)
        if rid in band_run_ids or not split.keep_run(run_index):
            continue
        if used_style_keys is not None:
            used_style_keys.add((index, run_index))
        parts.append(text_markup(run, rid, styles[(index, run_index)]))
    parts.append("</div>")

    # -- field layer, split around each band's reading-order position --------
    # A growable band is not appended after every static cell: it is spliced
    # into the tab order at its own (y0, x0), the SAME key lattice.py sorts
    # `page_layout["cells"]` by (`boxes.sort(key=lambda b: (yl.positions[...],
    # xl.positions[...]))`), so a repeat-row table lands exactly where its
    # first row would have sorted instead of at the page's tab-order end
    # (F209: a 490pt backward jump on 1600-pt-2018 p1). The cells layer is
    # therefore emitted as `len(band_order) + 1` separate, flat
    # `<div class="layer-cells">` sibling runs -- never nested -- with one
    # band's `<template>` + rendered `<div class="band">` filling each gap.
    # Multiple bands on one page each land at their own position because
    # `band_order` is sorted by that same key and the cells are partitioned
    # by walking it once. Inside each run, cells are then ordered by
    # `cells_in_tab_order` so DOM order matches tab_check's (data-row, left)
    # grade; lattice ids are not that key.
    band_order = sorted(
        plans, key=lambda plan: (float(plan.band["y0"]), float(plan.band["x0"])))
    segments: list[list[dict[str, Any]]] = [[]]
    band_cursor = 0
    for cell in page_layout["cells"]:
        if cell["id"] in band_cell_ids or not split.keep_cell(cell["id"]):
            continue
        cell_key = (cell["y0"], cell["x0"])
        while (band_cursor < len(band_order)
               and cell_key >= (float(band_order[band_cursor].band["y0"]),
                                float(band_order[band_cursor].band["x0"]))):
            segments.append([])
            band_cursor += 1
        segments[-1].append(cell)
    while band_cursor < len(band_order):
        segments.append([])
        band_cursor += 1

    def emit_cells_layer(cells: list[dict[str, Any]]) -> None:
        parts.append('<div class="layer-cells">')
        for cell in cells_in_tab_order(cells):
            parts.append(cell_markup(split.clipped(cell, "cell", cell["id"]), fields))
        parts.append("</div>")

    emit_cells_layer(segments[0])
    for seg_index, plan in enumerate(band_order):
        rows = plan.capacity if options.band_rows is None else min(options.band_rows,
                                                                   plan.capacity)
        parts.append(band_template_markup(plan, len(band_blobs), fields))
        parts.append(f'<div class="band" id="band-content-{esc_attr(plan.band["id"])}" '
                     f'data-band="{esc_attr(plan.band["id"])}" '
                     f'data-rendered-rows="{rows}" data-overflow-rows="0" '
                     f'data-capacity="{plan.capacity}" '
                     f'data-row-pitch="{fmt(plan.band["row_pitch_pt"])}">')
        for row in range(rows):
            for cell in sorted(plan.cells_by_row.get(row, []), key=lambda c: (c["x0"], c["id"])):
                parts.append(cell_markup(cell, fields))
            for rid, _cell, _ in sorted(plan.texts_by_row.get(row, []), key=_run_order):
                key = (index, _run_index_of(rid))
                if used_style_keys is not None:
                    used_style_keys.add(key)
                parts.append(text_markup(runs_by_id[rid], rid, styles[key],
                                         extra=(("data-band-row", str(row)),)))
        parts.append("</div>")
        band_blobs.append(band_json(plan, rows, styles, runs_by_id, fields))
        emit_cells_layer(segments[seg_index + 1])

    parts.append("</div>")
    return "".join(parts)


def _form_side_inventory(page_ir: dict[str, Any], page_layout: dict[str, Any],
                         split: PageSplit) -> dict[str, int]:
    """How much of one page the form document still carries, per kind.

    Counted from the same predicates emit_page() uses, so "nothing is left"
    means the emitter would draw nothing rather than that a flag says so.
    """
    return {
        "rules": sum(1 for r in page_ir["rules"] if split.keep_rule(r["id"])),
        "area_fills": sum(1 for i in range(len(page_ir["area_fills"]))
                          if split.keep_fill(i)),
        "images": sum(1 for i in range(len(page_ir["images"])) if split.keep_image(i)),
        "paths": sum(1 for p in page_ir.get("paths", ())
                     if split.keep_path(str(p["id"]))),
        "text_runs": sum(1 for i in range(len(page_ir["text_runs"]))
                         if split.keep_run(i)),
        "cells": sum(1 for c in page_layout["cells"] if split.keep_cell(c["id"])),
        "growable": len(page_layout["growable"]),
    }


def doc_link_markup(href: str, label: str) -> str:
    return f'<a class="doc-link" href="{esc_attr(href)}">{esc_text(label)}</a>'


def whole_page_split(page: dict[str, Any]) -> PageSplit:
    """A page of a standalone guide PDF: all of it is guide material.

    guides.py computes which runs of a *form* sheet lie below the cut. A guide
    PDF has no form on it to cut away from, so the claim is the whole page, and
    the same reflow that fixed 1603Q's overlapping columns applies unchanged.
    """
    return PageSplit(guide_side=True,
                     run_indices=frozenset(range(len(page["text_runs"]))))


def guide_source_key(source_ir: dict[str, Any]) -> str:
    """The PDF file name a guide-source IR was extracted from.

    extract.py records `external:<name>` for any PDF outside the repo, which
    every source PDF is. The name is what binds an extraction back to the entry
    in the guide plan; nothing here matches on order, so passing the sources in
    a different order cannot silently pair the wrong text with the wrong link.
    """
    file_name = str(source_ir.get("source", {}).get("file", ""))
    return pathlib.PurePosixPath(file_name.split(":", 1)[-1]).name


def standalone_pdf_markup(split: DocumentSplit, options: Options,
                          warnings: list[str]) -> str:
    """The guide PDFs batch.py skips, reflowed after the inline guide pages.

    These used to be embedded with `<object>`. An embedded PDF is a second
    document with its own pagination: printing the page around it prints the
    plugin's viewport, not the four pages of instructions inside it, so the
    twelve bundles whose whole guide was a linked PDF printed as one near-blank
    sheet. Running the guide PDF through the same extractor and the same reflow
    the inline guides use makes it text in *this* document, which is the only
    form of it that prints, and makes all 29 guides print the same way.

    The conversion is a reading copy, not a replacement: the pinned PDF stays
    beside the HTML and is linked, so the exact artefact is always one click
    away and the printed sheet names it. A PDF with no extraction falls back to
    the embed, with a warning -- degraded rather than dropped.
    """
    if not split.standalone_pdfs:
        return ""
    directory = options.guide_pdf_dir.rstrip("/")
    parts: list[str] = []
    for source in split.standalone_pdfs:
        name = pathlib.PurePosixPath(source).name
        href = f"{directory}/{name}" if directory else name
        # BIR ships these with spaces in the file name ("1701Q Guide Jan 2018.pdf"),
        # which is a valid path and not a valid URL.
        url = urllib.parse.quote(href)
        if options.out_dir is not None and not (options.out_dir / href).is_file():
            warnings.append(
                f"missing guide PDF {href}: the guide document links it but it is not "
                f"beside the output, so the link 404s")
        source_ir = options.guide_sources.get(name)
        converted = source_ir is not None
        parts.append(f'<section class="gl-pdf" data-source="{esc_attr(name)}" '
                     f'data-converted="{"true" if converted else "false"}">')
        parts.append(f"<h2>{esc_text(name)}</h2>")
        parts.append(f'<p class="gl-download">Source PDF: '
                     f'<a href="{esc_attr(url)}">{esc_text(name)}</a></p>')
        if converted:
            for page in source_ir["pages"]:
                parts.append(reflow_page(page, whole_page_split(page)))
        else:
            warnings.append(
                f"no --guide-source for {name}: it is embedded as a linked PDF, which "
                f"does not print with this document")
            parts.append(f'<object data="{esc_attr(url)}" type="application/pdf" '
                         f'data-source="{esc_attr(name)}">'
                         f'<a href="{esc_attr(url)}">{esc_text(name)}</a></object>')
        parts.append("</section>")
    return "".join(parts)


def _head(ir: dict[str, Any], title: str, styles: str, backend_name: str | None,
          document: str, font_preloads: Sequence[str] = ()) -> list[str]:
    form = ir["form"]
    attributes = ['lang="en"', f'data-form="{esc_attr(form["code"])}"',
                  f'data-revision="{esc_attr(form["revision"])}"']
    if backend_name is not None:
        attributes.append(f'data-rule-backend="{backend_name}"')
    attributes.append(f'data-source-sha256="{esc_attr(ir["source"]["sha256"])}"')
    attributes.append(f'data-schema-version="{SCHEMA_VERSION}"')
    # Only the guide announces itself. The form document's <html> tag is left
    # exactly as it was so that a form with no guide is byte-identical to what
    # this module emitted before the split existed.
    if document != "form":
        attributes.append(f'data-document="{esc_attr(document)}"')
    preloads = [
        f'<link rel="preload" href="{esc_attr(href)}" as="font" '
        'type="font/woff2" crossorigin>'
        for href in sorted(set(font_preloads))
    ]
    return [
        "<!doctype html>",
        f"<html {' '.join(attributes)}>",
        "<head>",
        '<meta charset="utf-8">',
        f"<title>{esc_text(title)}</title>",
        "<!-- Generated by tools/formgen/emit.py from the pinned PDF's own content "
        "stream. Do not hand-edit: regenerate. -->",
        *preloads,
        "<style>",
        styles,
        "</style>",
        "</head>",
        "<body>",
    ]


def _validate_guide_sources(split: DocumentSplit, ir: dict[str, Any],
                            options: Options) -> None:
    """A guide-source IR must belong to this form and to a PDF the plan lists.

    Both directions are fatal. An extraction of the wrong PDF would print
    another form's instructions under this form's heading, and a source that
    matches no entry in the plan means the driver and the plan disagree about
    what this bundle's guide is made of. Either way the document would be
    quietly wrong, which is the one outcome this pipeline exists to make
    impossible.
    """
    if not options.guide_sources:
        return
    listed = {pathlib.PurePosixPath(path).name for path in split.standalone_pdfs}
    unknown = sorted(set(options.guide_sources) - listed)
    if unknown:
        raise SystemExit(
            f"--guide-source names {unknown}, which the guide plan does not list as a "
            f"standalone PDF of {ir['form']['code']}-{ir['form']['revision']}")
    for name, source_ir in sorted(options.guide_sources.items()):
        source_form = source_ir.get("form", {})
        if (source_form.get("code"), source_form.get("revision")) != (
                ir["form"]["code"], ir["form"]["revision"]):
            raise SystemExit(
                f"guide source {name} was extracted as "
                f"{source_form.get('code')}-{source_form.get('revision')}, not as "
                f"{ir['form']['code']}-{ir['form']['revision']}")


def build_reflow_guide(ir: dict[str, Any], layout: dict[str, Any],
                       split: DocumentSplit, options: Options,
                       warnings: list[str]) -> tuple[str, list[str]]:
    """The guide as a readable document: no coordinates, therefore no overlap."""
    form = ir["form"]
    title = options.title or (f"BIR Form {form['code']} ({form['revision']}) "
                              f"-- Guidelines and Instructions")
    by_index = {int(page["index"]): page for page in ir["pages"]}
    # The lattice is what tells a relocated *table* from relocated prose, so the
    # guide reads the same layout the form does.
    layout_by_index = {int(page["index"]): page for page in layout["pages"]}
    body = [f'<div class="gl">',
            doc_link_markup(options.form_href, "← Back to the form"),
            f"<h1>{esc_text(title)}</h1>",
            f'<p class="gl-sub">Reference material lifted off the sheet by '
            f'tools/formgen/guides.py. Re-typeset for reading: the text is the '
            f"source's, the line breaks are not.</p>"]
    for index in split.guide_pages:
        page = by_index[index]
        images = [i for i in range(len(page["images"]))
                  if i in split.page(index).image_indices]
        if images:
            warnings.append(
                f"page {index}: the guide region claims {len(images)} image(s); the "
                f"reflowed guide drops them. Use --guide-layout absolute to keep them.")
        body.append(reflow_page(page, split.page(index), layout_by_index.get(index)))
    body.append(standalone_pdf_markup(split, options, warnings))
    body.append("</div>")

    # guide_page_css leads for the same reason page_css does in the form: the
    # sheet the document is printed on is the first thing about it.
    head = _head(ir, title,
                 "\n".join([guide_page_css(ir), BASE_CSS.rstrip(), DOC_LINK_CSS,
                            GUIDE_CSS, GUIDE_PDF_CSS]),
                 None, "guide")
    return "\n".join(head) + "\n" + "".join(body) + "\n</body>\n</html>\n", warnings


def build_document(ir: dict[str, Any], layout: dict[str, Any], plan: dict[str, Any],
                   options: Options) -> tuple[str, list[str]]:
    warnings: list[str] = []
    if ir["source"]["sha256"] != layout["source"]["sha256"]:
        raise SystemExit("layout was built from a different PDF than the IR")
    if ir["source"]["sha256"] != plan["source"]["ir_sha256_of_pdf"]:
        raise SystemExit("font plan was built from a different PDF than the IR")

    split = DocumentSplit(options.guide_plan, ir, options.document)
    _validate_guide_sources(split, ir, options)
    if options.document == "guide" and options.guide_layout == "reflow":
        return build_reflow_guide(ir, layout, split, options, warnings)

    backend = BACKENDS[options.rule_backend]()
    styles = resolve_run_styles(ir, plan, warnings)
    # Built from the whole layout, never from the half being emitted, so a
    # cell's typing surface is the same markup whichever document carries it --
    # which is what lets the split assertions compare the two halves against the
    # undivided document byte for byte.
    fields = FieldPlan(layout, resolve_field_face(plan, warnings), warnings, ir)

    # The form keeps every page at its full height even where the guide took the
    # lower 70% of one: the page box, the page count and @page are the form's
    # geometry, and the freed space is what a growable band expands into. The
    # guide document carries only the pages it actually has content on.
    #
    # A page the guide took *entirely* is the one exception, and it is not the
    # same case: keeping it prints a blank sheet stapled into the middle of the
    # form (2200P p3, 2550M p3 and p4, 0605 p2, 1702Q p3, 2553 p2 all did). There
    # is no geometry left on it to preserve and no band that could expand into
    # it, so the page leaves the form document. The reference the round-trip is
    # scored against has to lose it too, or a correctly dropped sheet reads as a
    # page-count mismatch; the emptiness is asserted here rather than trusted so
    # that a page with anything left on it stays, whatever the plan says.
    wanted = (set(split.guide_pages) if options.document == "guide"
              else {int(page["index"]) for page in ir["pages"]})
    if options.document == "form":
        for page_ir, page_layout in zip(ir["pages"], layout["pages"]):
            index = int(page_ir["index"])
            remains = _form_side_inventory(page_ir, page_layout, split.page(index))
            if index in split.guide_pages and not any(remains.values()):
                wanted.discard(index)
                warnings.append(
                    f"page {index}: every element is guide material, so the form "
                    f"document drops the page rather than printing a blank sheet. "
                    f"Score the form against a reference with this page removed.")

    band_blobs: list[dict[str, Any]] = []
    used_style_keys: set[tuple[int, int]] = set()
    pages = [emit_page(page_ir, page_layout, styles, backend, options, band_blobs,
                       warnings, split.page(int(page_ir["index"])), fields,
                       used_style_keys)
             for page_ir, page_layout in zip(ir["pages"], layout["pages"])
             if int(page_ir["index"]) in wanted]

    form = ir["form"]
    if options.document == "guide":
        title = options.title or (f"BIR Form {form['code']} ({form['revision']}) "
                                  f"-- Guidelines and Instructions")
        link = doc_link_markup(options.form_href, "← Back to the form")
    else:
        title = options.title or f"BIR Form {form['code']} ({form['revision']})"
        link = (doc_link_markup(options.guide_href, "Guidelines and Instructions →")
                if split.has_guide else "")

    field_face = _field_font_face(plan, fields)
    visible_font_files = {
        pathlib.PurePosixPath(styles[key].font_file).name
        for key in used_style_keys
        if styles[key].font_file
    }
    field_preloads = font_preload_hrefs(
        field_face, visible_font_files, options)
    styles_css = [
        page_css(ir),
        font_face_css(
            {key: styles[key] for key in sorted(used_style_keys)},
            options, warnings,
            extra_faces=([field_face] if field_face is not None else [])),
        BASE_CSS.rstrip(),
    ]
    field_style = field_css(fields)
    if field_style:
        styles_css.append(field_style)
    if link:
        styles_css.append(DOC_LINK_CSS)
    if split.guide_side and split.standalone_pdfs:
        styles_css.append(GUIDE_PDF_CSS)
    head = _head(ir, title, "\n".join(styles_css), backend.name,
                 options.document if options.document != "form" else "form",
                 field_preloads)
    if link:
        head.append(link)
    tail = [
        '<script type="application/json" id="formgen-bands">',
        json.dumps(band_blobs, ensure_ascii=False, separators=(",", ":")),
        "</script>",
        "<script>",
        BAND_JS,
        "</script>",
        "<script>",
        FIELD_JS,
        "</script>",
        # Last, and inert until asked for: see FIELD_DEBUG_JS for the four
        # barriers that keep it out of the printed and packaged page.
        "<script>",
        FIELD_DEBUG_JS,
        "</script>",
        # Also inert until asked for, behind its own token so the two
        # overlays cannot collide: see TAB_DEBUG_JS's own comment.
        "<script>",
        TAB_DEBUG_JS,
        "</script>",
        "</body>",
        "</html>",
        "",
    ]
    body = "\n".join(pages)
    trailer = standalone_pdf_markup(split, options, warnings) if split.guide_side else ""
    if trailer:
        body = f"{body}\n{trailer}" if body else trailer
    return "\n".join(head) + "\n" + body + "\n" + "\n".join(tail), warnings


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------


DEFAULT_IR = _ROOT / "build/ir/2551q-2018.ir.json"
DEFAULT_LAYOUT = _ROOT / "build/layout/2551q-2018.layout.json"
DEFAULT_PLAN = _ROOT / "build/fonts/2551q-2018.fontplan.json"
DEFAULT_GUIDE_PLAN = _ROOT / "build/guides/2551q-2018.guide.json"

# The slot element as the self-test reads it back. Deliberately the same shape
# as `audit.SLOT_RE` -- class, data-slot, style, in that order and nothing else
# -- so that adding an attribute here fails the emitter's own test rather than
# the audit's, 60 minutes later.
SELF_TEST_SLOT_RE = re.compile(
    r'<div class="s" data-slot="\d+" style="[^"]*">(.*?)</div>', re.S)


def _check(ok: bool, label: str, detail: str, failures: list[str]) -> None:
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}: {detail}", file=sys.stderr)
    if not ok:
        failures.append(label)


PAGE_SPLIT_RE = re.compile(r'<div class="page page-(\d+)"')
RECT_RE = re.compile(r'<rect [^>]*?/>|<div class="r" style="[^"]*"[^>]*></div>')
RUN_RE = re.compile(r'<div class="t" id="p\d+t\d+"[^>]*>')
CELL_RE = re.compile(r'<div id="([^"]+)" class="c[^"]*"')


def _pages_of(html: str) -> dict[int, str]:
    """The emitted document sliced per page, for per-page inventory checks.

    The document's tail is cut off first. Without that the last page's slice
    swallows the band blob and the scripts, and two documents with a different
    *page count* then compare unequal on a page they both emitted identically --
    which is exactly the shape of a form that dropped a wholly relocated page.
    """
    body = html.split('<script type="application/json"', 1)[0]
    out: dict[int, str] = {}
    chunks = PAGE_SPLIT_RE.split(body)
    for index in range(1, len(chunks) - 1, 2):
        out[int(chunks[index])] = chunks[index + 1]
    return out


INPUT_RE = re.compile(r"<input\b[^>]*>")
INPUT_NAME_RE = re.compile(r'<input\b[^>]*\bname="([^"]+)"')
INPUT_ID_RE = re.compile(r'<input\b[^>]*\bid="([^"]+)"')


def constructed_assertions(ir: dict[str, Any], layout: dict[str, Any],
                           plan: dict[str, Any], guide_plan: dict[str, Any] | None,
                           failures: list[str]) -> None:
    """The cases 2551Q does not contain, built rather than skipped.

    A check that cannot be evaluated is a failure and not a pass -- that is the
    failure mode this project has already been burned by, an audit that scored
    137 real defects as clean because it only compared what it knew to compare.
    2551Q has no non-rectilinear path, no flipped image, no cell filled with
    statutory text, no page the guide took whole and no straddler, so every one
    of those is staged on its own data here. Nothing is keyed on a form code:
    what is asserted is the *behaviour*, on input shaped like the corpus's.
    """
    # -- paths: geometry, both paints, the fill rule, and the paint order ------
    triangle = {
        "id": "path0", "x0": 32.88, "y0": 27.6, "x1": 36.96, "y1": 31.44,
        "fill": [0.0, 0.0, 0.0], "fill_gray": 0.0, "stroke": None,
        "stroke_gray": None, "stroke_width_pt": 0.0, "even_odd": False,
        "role": "structural", "paint_seq": 0, "paint_seq_max": 0,
        "subpaths": [{"start": [32.88, 27.6], "closed": True, "ops": [
            {"op": "l", "points": [36.96, 29.52]},
            {"op": "l", "points": [32.88, 31.44]},
            {"op": "l", "points": [32.88, 27.6]}]}],
    }
    dot = dict(triangle, id="path1", even_odd=True, stroke=[0.2, 0.2, 0.2],
               stroke_gray=0.2, stroke_width_pt=0.24, paint_seq=1, paint_seq_max=2,
               subpaths=[{"start": [10.0, 20.0], "closed": False, "ops": [
                             {"op": "c", "points": [10.9, 20.0, 11.7, 20.7, 11.7, 21.5]}]},
                         {"start": [1.0, 2.0], "closed": True, "ops": [
                             {"op": "re", "points": [1.0, 2.0, 3.0, 4.5]}]}])
    _check(path_data(triangle) == "M32.88 27.6L36.96 29.52L32.88 31.44L32.88 27.6Z",
           "a filled triangle is one closed path on the source's own points",
           path_data(triangle), failures)
    _check(path_data(dot) == ("M10 20C10.9 20 11.7 20.7 11.7 21.5"
                              "M1 2L3 2L3 4.5L1 4.5Z"),
           "a curve keeps its control points and a `re` subpath its four corners",
           path_data(dot), failures)
    _check(path_paints(triangle) == ("#000000", None)
           and path_paints(dot) == ("#000000", "#333333"),
           "a path carries its fill and its outline separately",
           f"{path_paints(triangle)} / {path_paints(dot)}", failures)
    _check('fill-rule="evenodd"' in path_svg(dot)
           and 'fill-rule' not in path_svg(triangle)
           and 'stroke-width="0.24"' in path_svg(dot),
           "even-odd and the stroke width are stated only where the source states them",
           path_svg(dot), failures)

    staged_ir = json.loads(json.dumps(ir))
    page = staged_ir["pages"][0]
    page["paths"] = [triangle, dot]
    # A rule painted after the paths must still be emitted after them.
    late = max(int(r["paint_seq"]) for r in page["rules"])
    triangle["paint_seq"] = triangle["paint_seq_max"] = late + 1
    for backend_name in sorted(BACKENDS):
        html, _ = build_document(staged_ir, layout, plan,
                                 Options(backend_name, "fonts", "assets", None, None, None))
        drawn = re.findall(r'data-(?:rule|path)-id="([^"]+)"', html)
        _check(drawn.count("path0") == 1 and drawn.count("path1") == 1,
               f"{backend_name} every path is emitted exactly once",
               f"{drawn.count('path0')}/{drawn.count('path1')}", failures)
        _check("path1" in drawn and drawn.index("path1") < drawn.index("path0"),
               f"{backend_name} paths paint in the source's content-stream order",
               f"path1 at {drawn.index('path1')}, path0 at {drawn.index('path0')}",
               failures)

    # -- image placement: the matrix is carried, and only when it fits the box --
    flipped = {"sha256": "f" * 64, "ext": "png", "x0": 30.48, "y0": 37.92,
               "x1": 71.28, "y1": 74.64, "paint_seq": 0, "paint_seq_max": 0,
               "transform": [40.8, 0.0, -0.0, -36.72, 30.48, 74.64]}
    _check(placement_matrix(flipped) == [40.8, 0.0, -0.0, -36.72, 30.48, 74.64],
           "a vertical flip is carried as the source's own matrix",
           str(placement_matrix(flipped)), failures)
    _check(placement_matrix(dict(flipped, transform=None)) is None
           and placement_matrix(dict(flipped, x1=99.0)) is None,
           "a missing or box-contradicting matrix falls back to the box",
           "None for both", failures)
    for backend_name, needle in (("svg", 'transform="matrix(40.8,0,0,-36.72,30.48,74.64)"'),
                                 ("css", "matrix(40.8,0,0,-36.72,40.64,99.52)")):
        staged = json.loads(json.dumps(ir))
        staged["pages"][0]["images"] = [flipped]
        html, _ = build_document(staged, layout, plan,
                                 Options(backend_name, "fonts", "assets", None, None, None))
        _check(needle in html, f"{backend_name} emits the flip",
               needle, failures)

    # -- C6 part 2: pre-printed text over a blank makes it uneditable ----------
    staged_ir = json.loads(json.dumps(ir))
    staged_layout = json.loads(json.dumps(layout))
    victim = next((c for p in staged_layout["pages"] for c in p["cells"]
                   if c["kind"] == "field" and not c.get("comb")
                   and c["x1"] - c["x0"] > 20.0), None)
    if victim is None:
        _check(False, "C6 part 2 has a plain field cell to cover", "none found", failures)
    else:
        page_index = int(victim["id"][1:].split("c")[0])
        page = next(p for p in staged_ir["pages"] if int(p["index"]) == page_index)
        width = victim["x1"] - victim["x0"]
        text = "Not over P 250,000"
        each = width / len(text)
        page["text_runs"].append({
            "text": text, "font": "Arial", "family": "Arial", "size_pt": 7.0,
            "bold": False, "italic": False, "serif": False, "monospace": False,
            "superscript": False, "flags": 0, "color": 0,
            "x0": victim["x0"], "y0": victim["y0"], "x1": victim["x1"],
            "y1": victim["y1"], "baseline_y": victim["y1"], "origin_x": victim["x0"],
            "ascender": 0.9, "descender": -0.2, "line_height_pt": 8.0,
            "measured_advance_pt": width,
            "char_origin_offsets_pt": [each * i for i in range(len(text))],
            "char_advances_pt": [each] * len(text),
            "char_widths_pt": [each] * len(text),
            "direction": [1.0, 0.0], "rotated": False, "unmapped_glyphs": [],
        })
        ink = PrePrintedInk(page["text_runs"])
        _check(not field_verdict(victim, ink, None, None)[0]
               and field_verdict(dict(victim, comb={"cells": 3}), ink, None, None)[0],
               "pre-printed text blocks a plain field but never a comb",
               f"coverage {ink.coverage(victim):.2f}", failures)
        html, _ = build_document(staged_ir, staged_layout, plan,
                                 Options("svg", "fonts", "assets", None, None, None))
        cell = re.search(rf'<div id="{victim["id"]}"[^>]*>(<input[^>]*>)?', html)
        _check(cell is not None and cell.group(1) is None
               and 'data-preprinted="true"' in cell.group(0),
               "a cell filled with pre-printed text is emitted without an input",
               cell.group(0)[:80] if cell else "cell absent", failures)

    # -- C6 part 2b: the strip a caption's second line is SEATED on (F207) -----
    # 1701A item 19, at its measured geometry. The captions' second lines are
    # set across the 6.89pt strip below the boxes so that their ascenders cross
    # the lattice boundary: neither line's box is half inside the strip (39%
    # and 47%), so a box-overlap test reads the strip as blank paper and lays a
    # 584pt input over two printed lines. Each mutation below moves exactly one
    # thing and must trip exactly its own clause.
    # Arial's own outlines, as `extract.run_glyph_ink` publishes them: these are
    # the boxes this corpus's IR states for these characters, not a table
    # written here (2,086 to 4,612 runs each, one value apiece).
    ARIAL_INK = {"a": [0.042, -0.023, 0.535, 0.539],
                 "c": [0.031, -0.023, 0.477, 0.539],
                 "d": [0.026, -0.023, 0.495, 0.729],
                 "e": [0.04, -0.023, 0.513, 0.539],
                 "f": [0.018, 0.0, 0.258, 0.732],
                 "i": [0.066, 0.0, 0.15, 0.729],
                 "n": [0.07, 0.0, 0.487, 0.539],
                 "o": [0.036, -0.023, 0.51, 0.539],
                 "r": [0.069, 0.0, 0.321, 0.539],
                 "s": [0.034, -0.023, 0.459, 0.539],
                 "t": [0.014, -0.023, 0.254, 0.668],
                 "u": [0.065, -0.023, 0.482, 0.524]}

    def _seated_run(text: str, x0: float, baseline: float, *,
                    size: float = 9.0, table: dict[str, Any] | None = ARIAL_INK,
                    ascent: float = 0.905, descent: float = 0.21,
                    **extra: Any) -> dict[str, Any]:
        """One measured Arial run, seated on `baseline` and starting at `x0`."""
        each = size * 0.556
        run: dict[str, Any] = {
            "text": text, "font": "Arial", "family": "Arial", "size_pt": size,
            "bold": False, "italic": False, "serif": False, "monospace": False,
            "superscript": False, "flags": 0, "color": 0,
            "x0": x0, "y0": baseline - ascent * size,
            "x1": x0 + each * len(text), "y1": baseline + descent * size,
            "baseline_y": baseline, "origin_x": x0,
            "ascender": ascent, "descender": -descent,
            "line_height_pt": (ascent + descent) * size,
            "measured_advance_pt": each * len(text),
            "char_origin_offsets_pt": [each * i for i in range(len(text))],
            "char_advances_pt": [each] * len(text),
            "char_widths_pt": [each] * len(text),
            "direction": [1.0, 0.0], "rotated": False, "unmapped_glyphs": [],
            "glyph_ink_em": dict(table) if table else {},
        }
        run.update(extra)
        return run

    def _box_fraction(run: dict[str, Any], cell: dict[str, Any]) -> float:
        """How much of the run's LINE box is inside the cell -- the old test."""
        height = float(run["y1"]) - float(run["y0"])
        inside = (min(float(cell["y1"]), float(run["y1"]))
                  - max(float(cell["y0"]), float(run["y0"])))
        return inside / height if height > 0 else 0.0

    strip = {"id": "p1c227", "x0": 14.88, "y0": 400.86, "x1": 599.04,
             "y1": 407.75, "kind": "field", "border": {}, "border_count": 3}
    # 47% of its box and 45% of its ink band are inside the strip; all of its
    # seat is. The second, in a face whose outlines the source does not state,
    # is the 267.16pt of the 313.49 that carries the verdict and it falls back
    # to its line box -- 39% inside, so only its seat can see it.
    seated = [_seated_run("of deduction", 101.18, 403.73),
              _seated_run("[available if gross sales/receipts and other non-"
                          "operating income do not exceed Three million pesos"
                          " (P3M)]", 289.37, 402.65, size=7.56, table=None,
                          ascent=0.936, descent=0.208,
                          font="Arial Narrow,Italic", family="Arial Narrow",
                          italic=True)]
    seated_ink = PrePrintedInk(seated)
    _check(seated_ink.coverage(strip) > PREPRINTED_COVERAGE
           and field_verdict(strip, seated_ink, None, None)[1] == "pre-printed",
           "a caption's second line refuses the strip it is seated on",
           f"coverage {seated_ink.coverage(strip):.3f} from two runs "
           f"{min(_box_fraction(r, strip) for r in seated):.0%} and "
           f"{max(_box_fraction(r, strip) for r in seated):.0%} inside by box",
           failures)
    # The bound on the seat clause, set as tight as the geometry allows rather
    # than comfortably: the same two lines raised 3.00pt, so they are seated
    # 0.13 and 1.21pt ABOVE the strip while both still hang into it -- 1.76pt
    # and 0.36pt of line box, and 0.08pt of measured ink. That is the graze
    # 1604C, 2316 and 2200S are made of. A seat clause that admitted any run
    # reaching the band would take this strip, and this strip is blank paper.
    lifted = [_seated_run(r["text"], r["x0"], r["baseline_y"] - 3.0,
                          size=r["size_pt"], table=r["glyph_ink_em"] or None,
                          ascent=r["ascender"], descent=-r["descender"],
                          font=r["font"], family=r["family"],
                          italic=r["italic"])
              for r in seated]
    _check(all(run["y1"] > strip["y0"] for run in lifted)
           and all(run["baseline_y"] < strip["y0"] for run in lifted)
           and PrePrintedInk(lifted).coverage(strip) == 0.0
           and field_verdict(strip, PrePrintedInk(lifted), None, None)[0],
           "lines seated just above the strip graze it and do not take it",
           f"coverage {PrePrintedInk(lifted).coverage(strip):.3f} from two "
           f"runs reaching {min(r['y1'] for r in lifted) - strip['y0']:.2f} "
           f"and {max(r['y1'] for r in lifted) - strip['y0']:.2f}pt into it",
           failures)
    # The other clause, alone, and in the direction only IT can answer: a line
    # seated below the strip, of characters that carry no descender, whose LINE
    # box is half inside because that box is drawn to its face's descent line
    # and its ink is nowhere near it. This is the 0.39-1.5pt of blank paper
    # audit.py measures between such a caption and the box under it; charging
    # the cell for it would refuse a taxpayer a real writing surface.
    narrow = {"id": "p1c227b", "x0": 14.88, "y0": 400.86, "x1": 120.0,
              "y1": 407.75, "kind": "field", "border": {}, "border_count": 3}
    below = _seated_run("successor courses", 20.0, 410.5)
    _check(_box_fraction(below, narrow) >= 0.5
           and PrePrintedInk([below]).coverage(narrow) == 0.0
           and field_verdict(narrow, PrePrintedInk([below]), None, None)[0],
           "a line box half inside is not ink half inside, and only ink counts",
           f"box {_box_fraction(below, narrow):.0%} inside, ink "
           f"{(407.75 - (410.5 - 9.0 * 0.539)) / (9.0 * 0.562):.0%}", failures)
    # ... and the same run with no stated outline falls back to that line box
    # and does refuse the cell, which is the fail-closed side: a band nobody
    # measured is never invented, and the looser bound is the one that stands.
    unmeasured = dict(below, glyph_ink_em={})
    _check(_ink_band(unmeasured) is None
           and PrePrintedInk([unmeasured]).coverage(narrow) > PREPRINTED_COVERAGE,
           "with no stated outline the same run falls back to its line box",
           f"coverage {PrePrintedInk([unmeasured]).coverage(narrow):.3f}",
           failures)
    # `_ink_band` itself, and each of its refusals mutated in isolation.
    band = _ink_band(below)
    _check(band is not None and abs(band[0] - (410.5 - 9.0 * 0.539)) < 1e-9
           and abs(band[1] - (410.5 + 9.0 * 0.023)) < 1e-9,
           "the band is the tallest and deepest outline the run sets",
           f"{band[0]:.3f}..{band[1]:.3f} against a line box "
           f"{below['y0']:.3f}..{below['y1']:.3f}", failures)
    partial = dict(ARIAL_INK)
    partial.pop("c")
    _check(_ink_band(dict(below, rotated=True)) is None
           and _ink_band(dict(below, baseline_y=None)) is None
           and _ink_band(dict(below, size_pt=0.0)) is None
           and _ink_band(dict(below, glyph_ink_em=partial)) is None
           and _ink_band(dict(below, glyph_ink_em=dict(
               ARIAL_INK, c=[0.031, 0.7, 0.477, 0.539]))) is None,
           "a rotated, unseated, sizeless, partial or degenerate table is refused",
           "None for all five", failures)
    # The reshaped index must not have moved the OTHER question this class is
    # asked, except in the one way F227 changes it: `intrusions` now reads
    # each glyph's OWN outline where the source states one, instead of
    # restating the run's shared ascent-to-descent extent for every character
    # in it. seated[0] ("of deduction") states every character it sets, in
    # ARIAL_INK; seated[1] (Arial Narrow Italic) states none and must still
    # fall back to the run's line box exactly as before -- the population
    # `_glyph_ink_box` itself falls back for is unchanged.
    first_run, second_run = seated
    first_spans = _glyph_spans(first_run)
    first_x1 = max(gx1 for _char, _gx0, gx1 in first_spans)
    first_boxes = seated_ink.intrusions(
        first_run["x0"] - 1.0, 400.86, first_x1 + 1.0, 407.75)
    second_spans = _glyph_spans(second_run)
    second_x1 = max(gx1 for _char, _gx0, gx1 in second_spans)
    second_boxes = seated_ink.intrusions(
        second_run["x0"] - 1.0, 400.86, second_x1 + 1.0, 407.75)
    expected_first = {_glyph_ink_box(first_run, char, origin)
                      for char, origin, _x1 in first_spans}
    _check(len(first_boxes) == len(first_spans)
           and set(first_boxes) == expected_first
           and None not in expected_first
           and len({box[1:4:2] for box in first_boxes}) > 1
           and len(second_boxes) == len(second_spans)
           and all(box[1] == second_run["y0"] and box[3] == second_run["y1"]
                   for box in second_boxes),
           "intrusions reads each glyph's own outline where the source "
           "states one (F227), and still falls back to the run's line box "
           "where it does not",
           f"{len({box[1:4:2] for box in first_boxes})} distinct y-extent(s) "
           f"across {len(first_boxes)} measured glyph(s) vs 1 across "
           f"{len(second_boxes)} unmeasured glyph(s)",
           failures)

    # -- C6 part 3: decorative shading over a blank makes it uneditable --------
    # The 2200T page-2 hazard, staged: a cell the official form shades to say NO
    # RATE APPLIES accepted 999,999.00 because nothing in the box model ever
    # asked what colour its paper was.
    shade_ir = json.loads(json.dumps(ir))
    shade_layout = json.loads(json.dumps(layout))
    shade_victim = next((c for p in shade_layout["pages"] for c in p["cells"]
                         if c["kind"] == "field" and not c.get("comb")
                         and c["x1"] - c["x0"] > 20.0
                         and c["y1"] - c["y0"] > 4.0), None)
    if shade_victim is None:
        _check(False, "C6 part 3 has a plain field cell to shade", "none found", failures)
    else:
        page_index = int(shade_victim["id"][1:].split("c")[0])
        page = next(p for p in shade_ir["pages"] if int(p["index"]) == page_index)
        top = max((int(f["paint_seq"]) for f in page["area_fills"]), default=0)
        x0, y0 = float(shade_victim["x0"]), float(shade_victim["y0"])
        x1, y1 = float(shade_victim["x1"]), float(shade_victim["y1"])

        def _fill(gray: float, role: str, seq: int, *, shrink: float = 0.0
                  ) -> dict[str, Any]:
            return {"x0": x0, "y0": y0, "x1": x1 - shrink * (x1 - x0), "y1": y1,
                    "gray": gray, "rgb": [gray, gray, gray], "role": role,
                    "paint_seq": seq, "paint_seq_max": seq}

        band = _fill(0.8509, "decorative", top + 1)
        _check(DecorativeShading([band]).blocks(shade_victim)
               and field_verdict(shade_victim, None,
                                 DecorativeShading([band]), None)[1] == "shading"
               and field_verdict(dict(shade_victim, comb={"cells": 3}), None,
                                 DecorativeShading([band]), None)[0],
               "decorative shading blocks a plain field but never a comb",
               f"grey 0.8509 over {shade_victim['id']}", failures)
        # The five real fields the threshold exists to spare: 1604CF's three
        # 0.8902 cells and 2200AN's two 0.9489 ones. Asserted as tone, not as
        # form code -- a rule that named the forms would not be a rule.
        _check(not DecorativeShading([_fill(0.8902, "decorative", top + 1)])
               .blocks(shade_victim)
               and not DecorativeShading([_fill(0.9489, "decorative", top + 1)])
               .blocks(shade_victim),
               "near-white decoration above the threshold leaves a real field typeable",
               f"{fmt(DECORATIVE_GRAY_MAX)} is the bound", failures)
        _check(not DecorativeShading([band, _fill(1.0, "knockout", top + 2)])
               .blocks(shade_victim)
               and DecorativeShading([_fill(1.0, "knockout", top + 1), band])
               .blocks(shade_victim),
               "the topmost fill decides: a knockout over a band restores the blank",
               "and a band over a knockout takes it away", failures)
        _check(not DecorativeShading([_fill(0.8509, "decorative", top + 1,
                                            shrink=0.45)]).blocks(shade_victim),
               "a band covering under 70% of the cell is not this cell's paper",
               f"{fmt(DECORATIVE_COVERAGE * 100)}% is the bound", failures)

        page["area_fills"].append(band)
        page["area_fills"].sort(key=lambda f: (f["y0"], f["x0"]))
        html, _ = build_document(shade_ir, shade_layout, plan,
                                 Options("svg", "fonts", "assets", None, None, None))
        cell = re.search(rf'<div id="{shade_victim["id"]}"[^>]*>(<input[^>]*>)?', html)
        _check(cell is not None and cell.group(1) is None
               and 'data-preprinted="shading"' in cell.group(0),
               "a cell on decorative shading is emitted without an input",
               cell.group(0)[:80] if cell else "cell absent", failures)

    # -- C6 part 4: a comb slot the SOURCE already filled in --------------------
    # G11, the one place in the corpus where a producer bug puts a live text box
    # on a statutory constant: 180 of 180 `mixed` cells were emitted with a full
    # set of editable slots because `field_verdict`'s first rule returns before
    # any ink is consulted. The assertions below are the four populations that
    # decide the rule -- a constant, a whole printed group, a money comb's
    # decimal bullet, and a rate the sheet prints across a whole comb -- plus
    # the two false positives that would have cost real typing surface.
    slot_victim = next(
        (cell for page in layout["pages"] for cell in page["cells"]
         if cell.get("comb") and len(cell["comb"]["slot_x"]) >= 6
         and not any(cell["x0"] >= band["x0"] - 1.0 and cell["x1"] <= band["x1"] + 1.0
                     and cell["y0"] >= band["y0"] - 1.0 and cell["y1"] <= band["y1"] + 1.0
                     for band in (page.get("growable") or ()))), None)
    if slot_victim is None:
        _check(False, "C6 part 4 has a comb cell of 5+ slots to fill in",
               "none found", failures)
    else:
        slot_x = [float(v) for v in slot_victim["comb"]["slot_x"]]
        # The probes below must land inside the rectangle the verdicts are
        # asked of, which is the compartment's WRITING rectangle when the
        # layout publishes one and the divider band otherwise -- the same
        # preference `comb_slot_verdicts` itself takes, for the reason
        # documented there.
        if ("writing_y0" in slot_victim["comb"]
                and "writing_y1" in slot_victim["comb"]):
            band_y0 = float(slot_victim["comb"]["writing_y0"])
            band_y1 = float(slot_victim["comb"]["writing_y1"])
        else:
            band_y0 = float(slot_victim["comb"].get("y0", slot_victim["y0"]))
            band_y1 = band_y0 + float(slot_victim["comb"].get(
                "height_pt", float(slot_victim["y1"]) - float(slot_victim["y0"])))
        glyph_y0 = band_y0 + 0.25 * (band_y1 - band_y0)
        glyph_y1 = band_y1 - 0.25 * (band_y1 - band_y0)

        def _printed(text: str, left: float, width: float, *,
                     y0: float = glyph_y0, y1: float = glyph_y1) -> dict[str, Any]:
            """A run of `text` laid from `left`, each glyph `width` wide."""
            return {
                "text": text, "font": "Arial,Bold", "family": "Arial",
                "size_pt": 11.04, "bold": True, "italic": False, "serif": False,
                "monospace": False, "superscript": False, "flags": 16, "color": 0,
                "x0": left, "y0": y0, "x1": left + width * len(text), "y1": y1,
                "baseline_y": y1, "origin_x": left, "ascender": 0.905,
                "descender": -0.21, "line_height_pt": y1 - y0,
                "measured_advance_pt": width * len(text),
                "char_origin_offsets_pt": [width * i for i in range(len(text))],
                "char_advances_pt": [width] * len(text),
                "char_widths_pt": [width] * len(text),
                "direction": [1.0, 0.0], "rotated": False, "unmapped_glyphs": [],
            }

        def _centred(char: str, index: int, share: float = 0.45) -> dict[str, Any]:
            """`char` printed in the middle of slot `index`, at comb pitch."""
            width = (slot_x[index + 1] - slot_x[index]) * share
            return _printed(char, slot_x[index] + (slot_x[index + 1]
                                                   - slot_x[index] - width) / 2.0, width)

        def _verdicts(runs: Sequence[dict[str, Any]],
                      fills: Sequence[dict[str, Any]] = ()) -> dict[int, str]:
            return comb_slot_verdicts(slot_victim, PrePrintedInk(runs),
                                      DecorativeShading(fills), None)

        # 1. A constant: the century BIR prints into the first two boxes of a
        #    year comb (1600-PT/VT p1c5, 1604C/1604E p1c4). Per slot, so the
        #    two the taxpayer must still fill stay fillable -- a whole-cell rule
        #    here would delete the year.
        _check(_verdicts([_centred("2", 0), _centred("0", 1)]) ==
               {0: "pre-printed", 1: "pre-printed"},
               "a constant printed at comb pitch takes only its own slots",
               "the century leaves the year's boxes typeable", failures)
        # 2. A whole printed group: the TIN branch code, 5 adjacent slots of
        #    zeros on 1701 x5 pages, 1701A x2, 1702-EX/MX/RT/Q x11, 1800 x3.
        #    Recognised without a group rule, because each zero answers for its
        #    own compartment.
        _check(_verdicts([_centred("0", i) for i in range(5)]) ==
               {i: "pre-printed" for i in range(5)},
               "every slot of a printed branch code is refused",
               "5 of 5, from 5 independent per-slot verdicts", failures)
        # 3. The money decimal bullet in its OWN compartment: 2000-DST,
        #    2200A/C/P/S print it into the third compartment from the right of
        #    a 14-, 29- or 33-compartment money comb, with the two centavos
        #    boxes to its right. 92 compartments corpus-wide, and 89 of
        #    `inputs_over_printed_text`'s 147 offenders. It takes its own
        #    compartment and nothing else, which is what keeps C4 fixed: the
        #    digits of the amount go either side of the point, so 2000-DST's
        #    page-1 money grid, 2200A/2200P Part III, 1801 item 24, 2316 items
        #    23-24 and 1702EX item 18 all stay typeable.
        n_slots = len(slot_x) - 1
        point = n_slots - 3
        bullet = _verdicts([_centred("●", point)])
        _check(bullet == {point: "pre-printed"}
               and point - 1 not in bullet
               and point + 1 not in bullet and point + 2 not in bullet,
               "the money bullet is refused in its own compartment and no other",
               f"1 of {n_slots} compartments refused; the two centavos boxes to "
               f"its right and every digit box to its left stay typeable",
               failures)
        # 4. A statutory rate set across a whole comb: 1800 p1c68 and 2550-DS
        #    p1c79 print "0 %" into a 2-compartment comb. Both compartments are
        #    spent, so that comb ends with no input at all -- correct, and not
        #    C4: C4 is a money box a taxpayer must fill, and a printed rate is
        #    not one. These are the only two combs in the corpus the source
        #    fills entirely.
        _check(_verdicts([_centred("0", 0), _centred("%", 1)])
               == {0: "pre-printed", 1: "pre-printed"},
               "a rate the form prints spends both compartments it is set in",
               "'0 %' is a value the sheet states, not a box to type in", failures)
        # 5. A caption the lattice swallowed into the comb cell: 9+ glyphs in
        #    ONE slot, at label scale rather than comb pitch. It is a
        #    segmentation defect (G05/G12) AND the compartment carrying it is
        #    not a typing surface, which are not alternatives -- refusing the
        #    input does not hide the defect, because the comb still publishes a
        #    compartment twelve times the width of its neighbours and
        #    `audit.check_comb_slots_match_printed` still reports the cell.
        #    Leaving it live is what put an editable box over "27 Tax Debit
        #    Memo" on 2200-A, -C and -P. This is the position the corpus was
        #    already taking, on different evidence for the same fact: the 9
        #    swallowed-caption compartments the shading branch answers for
        #    (1801 p1c13/c31/c33/c112, 1800 p1c26, 2200S p1c29, 2552 p1c28,
        #    1604F p1c25/c36) are refused because the sheet greys them, and the
        #    only ones left live were the three whose grey the source paints as
        #    a run of abutting rectangles rather than one. Measured: 5
        #    compartments corpus-wide contain a whole multi-glyph run, 2 of
        #    them already refused for tone and unchanged in their emitted
        #    bytes, and 3 of them these captions.
        _check(_verdicts([_printed(
            "7A ZIP Code", slot_x[0] + 0.5,
            (slot_x[1] - slot_x[0] - 1.0) / 11.0)]) == {0: "pre-printed"},
               "a label the sheet sets INSIDE one compartment spends it",
               "'7A ZIP Code' is printed there; a taxpayer cannot type over it",
               failures)
        # ... and the mutation that proves the clause doing the work is
        #    containment and not size: the identical caption moved so that it
        #    starts before the compartment's wall belongs to the box next door
        #    and leaves every compartment typeable.
        _check(_verdicts([_printed(
            "7A ZIP Code", slot_x[0] - 4.0,
            (slot_x[1] - slot_x[0] - 1.0) / 11.0)]) == {},
               "the same label reaching in from outside spends nothing",
               "a caption that crosses the wall belongs to the box beside it",
               failures)
        # 6. A neighbour's item number clipping into the first box: 2551M's and
        #    2553's "28C"/"29B" overhang by 4.53pt and 0605 p1c3's date hint
        #    clips its bracket in by 1.98pt, while every occupied compartment
        #    in the corpus clears its walls by at least 0.24pt.
        overhang = (slot_x[1] - slot_x[0]) * 0.45
        _check(_verdicts([_printed("8", slot_x[0] - overhang / 2.0, overhang)]) == {}
               and _verdicts([_centred("8", 0)]) == {0: "pre-printed"},
               "a glyph must clear the slot's own walls to be that slot's value",
               "containment carries no tolerance; the corpus separates by 2.22pt",
               failures)
        # 7. Tone at slot resolution: the caption end of a cell the lattice cut
        #    too wide (1801 p1c13/c31/c33/c112, 1800 p1c26, 2200S p1c29, 2552
        #    p1c28, 1604F p1c25/c36). Same test as the cell-level rule, same
        #    threshold. The grey TIN group separators this branch used to answer
        #    for are now taken by the ink rule, which sees the `-` printed in
        #    them; tone still has to work on its own, so it is asserted on a
        #    compartment carrying no ink at all.
        def _slot_fill(index: int, gray: float, role: str, seq: int) -> dict[str, Any]:
            return {"x0": slot_x[index], "y0": band_y0,
                    "x1": slot_x[index + 1], "y1": band_y1,
                    "gray": gray, "rgb": [gray, gray, gray], "role": role,
                    "paint_seq": seq, "paint_seq_max": seq}

        grey = _slot_fill(3, 0.8509, "decorative", 1)
        _check(_verdicts((), [grey]) == {3: "shading"}
               and _verdicts((), [grey, _slot_fill(3, 1.0, "knockout", 2)]) == {},
               "a shaded compartment is refused and a knockout over it is not",
               "the topmost fill decides, exactly as it does for a cell", failures)

        # 8. End to end: the slot div is still emitted, and it is the absent
        #    <input> that says so -- no new attribute, because audit.SLOT_RE and
        #    the comb referee both pin this element's exact attribute set.
        slot_ir = json.loads(json.dumps(ir))
        page_index = int(slot_victim["id"][1:].split("c")[0])
        page = next(p for p in slot_ir["pages"] if int(p["index"]) == page_index)
        page["text_runs"].append(_centred("0", 0))
        page["text_runs"].sort(key=lambda r: (r["y0"], r["x0"]))
        html, slot_warnings = build_document(
            slot_ir, layout, plan, Options("svg", "fonts", "assets", None, None, None))
        cell = re.search(rf'<div id="{slot_victim["id"]}"[^>]*>(.*?)</div></div>',
                         html, re.S)
        slots = SELF_TEST_SLOT_RE.findall(cell.group(1)) if cell else []
        _check(len(slots) >= 2 and "<input" not in slots[0] and "<input" in slots[1],
               "the filled compartment emits its slot div with no input in it",
               f"slot 0 {'empty' if slots and '<input' not in slots[0] else 'editable'}",
               failures)
        _check(any("carry a constant the form prints" in w for w in slot_warnings),
               "the exclusion is published, per cell and per slot index",
               next((w for w in slot_warnings if "constant the form prints" in w),
                    "no such warning")[:90], failures)
        # 9. The navigation the gap breaks. `data-slot-index` is the compartment's
        #    number and stays that; the NodeList is the sequence a taxpayer moves
        #    through, and indexing one with the other stops advancing at the first
        #    printed box.
        _check("positionOf(list,el)" in FIELD_JS
               and 'getAttribute("data-slot-index"),10)+delta' not in FIELD_JS,
               "comb navigation steps through the typeable slots, not slot numbers",
               "positionOf, not the attribute", failures)

    # -- C6 part 5: a box the sheet reserves for the Bureau ---------------------
    # F147: 0605's "BCS No./Item No. (To be filled up by the BIR)" blank was a
    # 253x17.5pt free-text taxpayer input. The geometry below is that sheet's,
    # transcribed: the caption sits on the grey band, its compartment's left
    # wall at 287.9 and right wall at the outer frame, the white knockout
    # 4.4pt below the caption, and the taxpayer's own Return Period box on the
    # same row but outside the caption's walls. The knockout's top border
    # (321.2..576.5) does not span the compartment (287.9..579.8), which is
    # exactly why caption and blank are one compartment on the real sheet.
    bcs_caption = {"text": " BCS No./Item No. (To be filled up by the  BIR)",
                   "x0": 290.0, "y0": 172.5, "x1": 456.6, "y1": 181.5}
    bcs_walls = [
        {"axis": "v", "x0": 287.6, "y0": 172.8, "x1": 288.1, "y1": 208.7},
        {"axis": "v", "x0": 579.6, "y0": 168.0, "x1": 580.1, "y1": 208.7},
        {"axis": "h", "x0": 321.2, "y0": 185.7, "x1": 576.5, "y1": 186.1},
        {"axis": "h", "x0": 321.2, "y0": 204.6, "x1": 576.5, "y1": 205.0},
    ]
    bcs_res = BureauReservation([bcs_caption], bcs_walls, [])
    bcs_cell = {"kind": "field", "x0": 321.6, "y0": 185.9, "x1": 576.1, "y1": 204.8}
    peer_cell = {"kind": "field", "x0": 40.6, "y0": 185.9, "x1": 165.7, "y1": 205.6}
    _check(field_verdict(bcs_cell, None, None, bcs_res) == (False, "bureau")
           and field_verdict(peer_cell, None, None, bcs_res)[0],
           "a Bureau-captioned blank is refused; the taxpayer box beside it is not",
           "0605's BCS box against its Return Period neighbour", failures)
    separated = BureauReservation(
        [bcs_caption],
        bcs_walls + [{"axis": "h", "x0": 286.0, "y0": 183.3, "x1": 581.0, "y1": 183.7}],
        [])
    _check(field_verdict(bcs_cell, None, None, separated)[0],
           "a ruled separator under the caption breaks the binding",
           "a section header governs its section, not the row beneath", failures)
    _check(field_verdict(dict(bcs_cell, y0=191.0), None, None, bcs_res)[0],
           "adjacency is bounded by the caption's own height",
           "9.5pt of separation against a 9.0pt line is not adjacency", failures)
    _check(_bureau_caption("(To be filled up the BIR)")
           and not _bureau_caption(
               "The machine validation shall reflect the date of payment")
           and not _bureau_caption("with an “X”. Three (3) copies must be "
                                   "filed: two (2) copies for BIR and one copy "
                                   "for the taxpayer."),
           "the sheet's own typo is a caption; prose about the boxes is not",
           "prefix match for prose-prone phrases, substring for parentheticals",
           failures)
    # The bottom-of-sheet shape, transcribed from 2200-A: one band the lattice
    # reads as a 2-slot comb, the left compartment captioned "Machine
    # Validation/...", the right "Stamp of Receiving Office/AAB...". Per slot,
    # because the compartments are separate Bureau boxes; and the cell itself
    # stays a field, because a comb cell always is one at cell resolution.
    validation_caption = {"text": "Machine  Validation/Revenue Official Receipt "
                                  "Details (if not filed with an Authorized "
                                  "Agent Bank) ",
                          "x0": 21.7, "y0": 847.4, "x1": 306.0, "y1": 856.3}
    stamp_caption = {"text": "Stamp of Receiving Office/AAB and Date of Receipt ",
                     "x0": 402.1, "y0": 847.4, "x1": 587.9, "y1": 856.3}
    band_cell = {"kind": "mixed", "x0": 16.3, "y0": 847.1, "x1": 595.3, "y1": 897.7,
                 "comb": {"cells": 2, "slot_x": [16.3, 392.7, 595.3],
                          "y0": 847.6, "height_pt": 49.7}}
    both = BureauReservation([validation_caption, stamp_caption], [], [])
    _check(comb_slot_verdicts(band_cell, None, None, both)
           == {0: "bureau", 1: "bureau"}
           and field_verdict(band_cell, None, None, both)[0],
           "each Bureau box of the bottom band refuses its own compartment",
           "per slot, and the comb cell itself stays a field", failures)
    _check(comb_slot_verdicts(band_cell, None, None,
                              BureauReservation([validation_caption], [], []))
           == {0: "bureau"},
           "a compartment without a Bureau caption keeps its input",
           "slot 1 stays when only the left box is captioned", failures)

    # End to end: a Bureau caption printed inside a blank leaves the cell in
    # the document, marked, with no input -- and the exclusion is published.
    bureau_ir = json.loads(json.dumps(ir))
    bureau_layout = json.loads(json.dumps(layout))
    bureau_victim = next((c for p in bureau_layout["pages"] for c in p["cells"]
                          if c["kind"] == "field" and not c.get("comb")
                          and c["x1"] - c["x0"] > 40.0
                          and c["y1"] - c["y0"] > 8.0), None)
    if bureau_victim is None:
        _check(False, "C6 part 5 has a plain field cell to reserve",
               "none found", failures)
    else:
        page_index = int(bureau_victim["id"][1:].split("c")[0])
        page = next(p for p in bureau_ir["pages"] if int(p["index"]) == page_index)
        text = "For BIR Use Only"
        # 30% of the cell's width: enough ink to be the caption, well under
        # the 50% at which the pre-printed rule would answer first.
        width = 0.3 * (bureau_victim["x1"] - bureau_victim["x0"])
        left = bureau_victim["x0"] + 0.35 * (bureau_victim["x1"] - bureau_victim["x0"])
        mid_y = (bureau_victim["y0"] + bureau_victim["y1"]) / 2.0
        each = width / len(text)
        page["text_runs"].append({
            "text": text, "font": "Arial", "family": "Arial", "size_pt": 6.5,
            "bold": False, "italic": False, "serif": False, "monospace": False,
            "superscript": False, "flags": 0, "color": 0,
            "x0": left, "y0": mid_y - 3.5, "x1": left + width, "y1": mid_y + 3.5,
            "baseline_y": mid_y + 3.5, "origin_x": left,
            "ascender": 0.9, "descender": -0.2, "line_height_pt": 7.0,
            "measured_advance_pt": width,
            "char_origin_offsets_pt": [each * i for i in range(len(text))],
            "char_advances_pt": [each] * len(text),
            "char_widths_pt": [each] * len(text),
            "direction": [1.0, 0.0], "rotated": False, "unmapped_glyphs": [],
        })
        page["text_runs"].sort(key=lambda r: (r["y0"], r["x0"]))
        html, bureau_warnings = build_document(
            bureau_ir, bureau_layout, plan,
            Options("svg", "fonts", "assets", None, None, None))
        cell = re.search(rf'<div id="{bureau_victim["id"]}"[^>]*>(<input[^>]*>)?', html)
        _check(cell is not None and cell.group(1) is None
               and 'data-preprinted="bureau"' in cell.group(0),
               "a cell the Bureau reserves is emitted without an input",
               cell.group(0)[:80] if cell else "cell absent", failures)
        _check(any("reserved for the Bureau" in w for w in bureau_warnings),
               "the Bureau exclusion is published, per cell",
               next((w for w in bureau_warnings if "reserved for the Bureau" in w),
                    "no such warning")[:90], failures)

    # -- Ink: a padded single-word run is emitted as its ink, at its own origin -
    padded = {"text": " No ", "origin_x": 231.17,
              "char_origin_offsets_pt": [0.0, 9.36, 15.84, 20.88],
              "char_widths_pt": [2.5, 6.5, 5.0, 2.5], "measured_advance_pt": 23.38}
    ink = run_ink(padded, None)
    _check(ink.trimmed and ink.text == "No" and abs(ink.origin_x - 240.53) < 1e-9,
           "a padded run is anchored on its first visible glyph",
           f"{ink.text!r} at {fmt(ink.origin_x)}", failures)
    # The source put 6.86pt of extra advance in the leading space and none
    # between N and o; the plan's uniform model gave that gap 2.2914pt of it,
    # which is what made the round-trip read back ' N o  ' and stop matching.
    _check(abs(ink.letter_spacing_pt or 0.0) <= 0.05,
           "the padding's advance does not become tracking between the letters",
           f"{ink.letter_spacing_pt}pt per gap", failures)
    multi = dict(padded, text=" No Yes ")
    _check(not run_ink(multi, None).trimmed,
           "a run whose ink contains a word gap keeps its padding",
           run_ink(multi, None).text, failures)

    # -- C8: the reflowed guide carries a run's colour ------------------------
    white = {"text": "MMC", "color": 16777215, "x0": 0.0, "x1": 10.0,
             "size_pt": 6.0, "baseline_y": 10.0}
    black = dict(white, text="ATC", color=0, x0=-20.0, x1=-10.0)
    _check(_render_pieces(_line_pieces([black, white]))
           == 'ATC <span style="color:#ffffff">MMC</span>',
           "a white run stays white in the guide and a black one carries no span",
           _render_pieces(_line_pieces([black, white])), failures)

    if guide_plan is None:
        return

    # -- S3: a page whose every element is guide material leaves the form ------
    staged_plan = json.loads(json.dumps(guide_plan))
    staged_layout = json.loads(json.dumps(layout))
    last = ir["pages"][-1]
    # A page whose every element is guide material has no growable band left on
    # it either -- all six such pages in the corpus have none -- and a band is
    # content, so the emitter must keep a page that still has one.
    staged_layout["pages"][-1]["growable"] = []
    staged_plan["inline"] = [{
        "page": int(last["index"]), "cut_y_pt": 0.0, "whole_page": True,
        "rule_ids": [r["id"] for r in last["rules"]],
        "cell_ids": [c["id"] for c in layout["pages"][-1]["cells"]],
        "text_run_indices": list(range(len(last["text_runs"]))),
        "area_fill_indices": list(range(len(last["area_fills"]))),
        "image_indices": list(range(len(last["images"]))),
        "straddlers": [],
    }]
    html, warnings = build_document(ir, staged_layout, plan, Options(
        "svg", "fonts", "assets", None, None, None, staged_plan, "form"))
    _check(f'id="page-{last["index"]}"' not in html
           and any("blank sheet" in w for w in warnings),
           "a wholly relocated page is dropped from the form, not printed blank",
           f"{len(_pages_of(html))} pages emitted", failures)

    # The control: emptiness is asserted from the emitter's own predicates, not
    # taken from the plan's flag, so one element left behind keeps the page.
    if last["rules"]:
        held = json.loads(json.dumps(staged_plan))
        held["inline"][0]["rule_ids"] = held["inline"][0]["rule_ids"][1:]
        kept, _ = build_document(ir, staged_layout, plan, Options(
            "svg", "fonts", "assets", None, None, None, held, "form"))
        _check(f'id="page-{last["index"]}"' in kept,
               "a page with one element left on it is not dropped",
               f"{len(_pages_of(kept))} pages emitted", failures)
    if layout["pages"][-1]["growable"]:
        kept, _ = build_document(ir, layout, plan, Options(
            "svg", "fonts", "assets", None, None, None, staged_plan, "form"))
        _check(f'id="page-{last["index"]}"' in kept,
               "a page that still carries a growable band is not dropped",
               f"{len(_pages_of(kept))} pages emitted", failures)

    # -- straddlers: the form draws its own clipped piece --------------------
    first_page = ir["pages"][0]
    rule = next((r for r in first_page["rules"] if r["y1"] - r["y0"] > 20.0), None)
    if rule is None:
        _check(False, "a straddling rule can be staged", "no tall rule", failures)
        return
    cut = (float(rule["y0"]) + float(rule["y1"])) / 2.0
    staged_plan = json.loads(json.dumps(guide_plan))
    staged_plan["inline"] = [{
        "page": int(first_page["index"]), "cut_y_pt": cut, "whole_page": False,
        "rule_ids": [], "cell_ids": [], "text_run_indices": [],
        "area_fill_indices": [], "image_indices": [],
        "straddlers": [{
            "kind": "rule", "ref": rule["id"], "x0": rule["x0"], "y0": rule["y0"],
            "x1": rule["x1"], "y1": rule["y1"], "detail": "", "disposition": "clipped",
            "form": {"x0": rule["x0"], "y0": rule["y0"], "x1": rule["x1"], "y1": cut},
            "guide": {"x0": rule["x0"], "y0": cut, "x1": rule["x1"], "y1": rule["y1"]},
        }],
    }]
    form_html, _ = build_document(ir, layout, plan, Options(
        "svg", "fonts", "assets", None, None, None, staged_plan, "form"))
    guide_html, _ = build_document(ir, layout, plan, Options(
        "svg", "fonts", "assets", None, None, None, staged_plan, "guide", "absolute"))
    form_rect = re.search(rf'<rect x="[^"]*" y="([^"]*)" width="[^"]*" '
                          rf'height="([^"]*)"[^>]*data-rule-id="{rule["id"]}"', form_html)
    guide_rect = re.search(rf'<rect x="[^"]*" y="([^"]*)" width="[^"]*" '
                           rf'height="([^"]*)"[^>]*data-rule-id="{rule["id"]}"', guide_html)
    _check(form_rect is not None and guide_rect is not None,
           "a clipped straddler is drawn by both documents",
           f"form {bool(form_rect)}, guide {bool(guide_rect)}", failures)
    if form_rect and guide_rect:
        above = float(form_rect.group(1)), float(form_rect.group(2))
        below = float(guide_rect.group(1)), float(guide_rect.group(2))
        _check(abs(above[0] - float(rule["y0"])) < 0.005
               and abs(above[0] + above[1] - cut) < 0.005
               and abs(below[0] - cut) < 0.005
               and abs(below[0] + below[1] - float(rule["y1"])) < 0.005,
               "the two pieces meet at the cut and reconstruct the rule",
               f"{above} + {below} for {rule['y0']}..{rule['y1']} cut {fmt(cut)}",
               failures)


def field_assertions(ir: dict[str, Any], layout: dict[str, Any], plan: dict[str, Any],
                     html: str, rendered: str, failures: list[str]) -> None:
    """Every blank the source drew is typeable, exactly once, and prints clean.

    Counted against the *layout*, not against the markup, so an input the
    generator forgot fails as loudly as one it emitted twice. That matters more
    here than anywhere else in this module: a field that silently does not exist
    looks exactly like a form that prints correctly, which is precisely the
    state this layer was written to end.

    "Every blank" is `field_verdict`'s answer, not `kind == "field"`. The two
    disagree in both directions and each disagreement was a live defect: a
    comb-bearing `mixed` cell is a field the old test did not expect (2000-DST's
    entire money grid), and a `field` cell filled with statutory text is not one
    (1700 page 2's tax brackets), nor is one the source shaded out (2200T page
    2's "no rate applies" rows).

    F217: this is an INDEPENDENT re-derivation of `FieldPlan`'s own fillable
    set -- `expected` above comes from `fields.of(...)`, `fillable` below is
    built by calling `field_verdict` directly, a second time, over evidence
    assembled here rather than trusted from `FieldPlan`'s own construction.
    That independence is void wherever the two constructions disagree on
    which optional plans they pass: `ruled_blanks` and `signature_boxes` were
    wired into both; `checkbox_squares`, `knockout_specify` and `row_numbers`
    were only ever wired into `FieldPlan`'s own production call, never here,
    so all three of `field_verdict`'s corresponding branches were
    unreachable from this self-check. Latent for `checkbox_squares` only
    because the pinned self-test form (2551Q) carries zero checkbox squares;
    latent for `knockout_specify` and `row_numbers` by pure coincidence --
    2551Q's one knockout-specify claim (`p1c187`) also carries an underscore
    rule, so `ruled_blanks` claims it first and the missing branch was never
    exercised even though it would have disagreed. All three are wired in
    now, the same shape `ruled_blanks`/`signature_boxes` already had.
    `signature_rules` (F221) is wired in from its own first commit, not left
    to repeat the same gap a second time.
    """
    fields = FieldPlan(layout, resolve_field_face(plan, []), [], ir)
    expected: dict[str, int] = {}
    for page in layout["pages"]:
        for cell in page["cells"]:
            box = fields.of(cell["id"])
            if box is not None:
                # A comb is its compartments; a plain field is its writing
                # REGIONS, which is one everywhere the sheet does not rule
                # across the cell and several where it does. Still an exact
                # count per cell, and still the emitter's own answer measured
                # against the markup it wrote.
                expected[cell["id"]] = (
                    box.capacity if box.capacity is not None
                    else len(box.region_insets))

    ink = {int(p["index"]): PrePrintedInk(p["text_runs"]) for p in ir["pages"]}
    shading = {int(p["index"]): DecorativeShading(p["area_fills"]) for p in ir["pages"]}
    reservation = {int(p["index"]): BureauReservation(
        p["text_runs"], p["rules"], p["area_fills"]) for p in ir["pages"]}
    rules_by_page = {int(p["index"]): p["rules"] for p in ir["pages"]}
    fills_by_page = {int(p["index"]): p["area_fills"] for p in ir["pages"]}
    runs_by_page = {int(p["index"]): p["text_runs"] for p in ir["pages"]}
    fillable_metrics = _min_fillable_line_metrics(ir)
    ruled_blanks = {int(p["index"]): RuledBlankWriting(
        rules_by_page.get(int(p["index"]), ()), p["cells"])
        for p in layout["pages"]}
    checkbox_squares = {int(p["index"]): CheckboxSquareWriting(
        rules_by_page.get(int(p["index"]), ()), fills_by_page.get(int(p["index"]), ()),
        p["cells"]) for p in layout["pages"]}
    signature_boxes = {int(p["index"]): SignatureBoxWriting(
        p["cells"], int(p["index"]), runs_by_page.get(int(p["index"]), ()))
        for p in layout["pages"]}
    knockout_specify = {int(p["index"]): KnockoutSpecifyWriting(
        p["cells"], int(p["index"]), runs_by_page.get(int(p["index"]), ()),
        fills_by_page.get(int(p["index"]), ()), fillable_metrics)
        for p in layout["pages"]}
    row_numbers = {int(p["index"]): RowNumberWriting(
        p["cells"], int(p["index"]), runs_by_page.get(int(p["index"]), ()), fillable_metrics)
        for p in layout["pages"]}
    signature_rules = {int(p["index"]): SignatureRuleWriting(
        p["cells"], int(p["index"]), rules_by_page.get(int(p["index"]), ()),
        runs_by_page.get(int(p["index"]), ()), fillable_metrics)
        for p in layout["pages"]}
    decoration = {int(p["index"]): PrintedDecoration(
        p["cells"], fills_by_page.get(int(p["index"]), ()),
        runs_by_page.get(int(p["index"]), ()),
        (fillable_metrics or {}).get("glyph_height_pt"))
        for p in layout["pages"]}
    fillable = [c["id"] for p in layout["pages"] for c in p["cells"]
                if field_verdict(c, ink.get(int(p["index"])),
                                 shading.get(int(p["index"])),
                                 reservation.get(int(p["index"])),
                                 ruled_blanks.get(int(p["index"])),
                                 checkbox_squares=checkbox_squares.get(int(p["index"])),
                                 signature_boxes=signature_boxes.get(int(p["index"])),
                                 knockout_specify=knockout_specify.get(int(p["index"])),
                                 row_numbers=row_numbers.get(int(p["index"])),
                                 signature_rules=signature_rules.get(int(p["index"])),
                                 decoration=decoration.get(int(p["index"])))[0]]
    _check(len(expected) == len(fillable) and set(expected) == set(fillable),
           "every fillable cell has a typing surface",
           f"{len(expected)} of {len(fillable)} fillable cells", failures)

    combs = {c["id"] for p in layout["pages"] for c in p["cells"] if c.get("comb")}
    _check(combs <= set(expected),
           "every comb is a field whatever text it also holds",
           f"{len(combs)} comb cells, {len(combs - set(expected))} without a surface",
           failures)

    names = INPUT_NAME_RE.findall(rendered)
    counts: dict[str, int] = {}
    for name in names:
        counts[name] = counts.get(name, 0) + 1
    _check(counts == expected,
           "one input per comb slot, one per plain field, and none anywhere else",
           f"{len(names)} inputs over {len(counts)} names; "
           f"{sum(expected.values())} expected over {len(expected)}", failures)

    identifiers = INPUT_ID_RE.findall(rendered)
    duplicates = sorted({i for i in identifiers if identifiers.count(i) > 1})
    _check(len(identifiers) == len(names) and not duplicates,
           "every input carries a unique id",
           f"{len(identifiers)} ids, {len(duplicates)} duplicated", failures)

    # A <template>'s inputs are a blueprint: an id or a name in there is a
    # second element answering to a row's identity.
    inert = "".join(re.findall(r"<template\b.*?</template>", html, flags=re.S))
    _check(not INPUT_ID_RE.search(inert) and not INPUT_NAME_RE.search(inert),
           "template inputs are anonymous",
           f"{len(INPUT_RE.findall(inert))} template input(s)", failures)

    used = {c for tag in INPUT_RE.findall(html)
            for c in re.findall(r'class="([^"]*)"', tag)[0].split()}
    declared = {"fi", "fc"} | set(fields.classes.values())
    _check(used <= declared and all(f".{name}{{" in html for name in used - {"fi", "fc"}),
           "every input's metrics class is declared",
           f"{len(used)} used, {len(declared)} declared", failures)

    # The affordances are :hover/:focus under @media screen, so an empty field
    # cannot print ink even if the guard is lost; the print reset is the second
    # line of defence, not the first.
    screen = re.search(r"@media screen\{(.*?)\}\n", html + "\n", flags=re.S)
    _check(screen is not None and ":hover" in screen.group(1)
           and ":focus" in screen.group(1),
           "field affordances are screen-only states",
           "hover/focus under @media screen" if screen else "no @media screen block",
           failures)
    _check("@media print{.fi{" in html,
           "the print stylesheet strips input chrome",
           "@media print resets border/outline/background", failures)

    field_surface_assertions(layout, plan, failures)
    comb_writing_rectangle_assertions(plan, failures)
    writing_box_assertions(plan, failures)
    knockout_specify_writing_assertions(plan, failures)
    signature_rule_writing_assertions(plan, failures)
    field_debug_assertions(html, failures)
    tab_debug_assertions(html, failures)


def ruled_blank_corpus_assertions(failures: list[str]) -> None:
    """Every underscore-drawn writing line earns its label cell an input --
    corpus-wide, not just on the one pinned form the rest of this self-test
    exercises.

    F148/F149 were found by the user tabbing through 1701 page 4 by hand.
    That is the class this check ends: it re-derives, independently of
    whatever `batch.py` last emitted, which rules `RuledBlankWriting` would
    admit on EVERY form this checkout has extracted, and fails loudly if any
    of them lacks a typing surface. A form added to `build/ir` after this
    check was written is covered the moment it is extracted, by construction
    -- nothing here names a slug.

    Runs over `build/ir` + `build/layout` + `build/fonts`, which is why it
    lives beside emit.py's other self-test assertions rather than in
    `audit.py` (locked) or `comb_referee.py` (an independent referee scoped
    to comb geometry, not to this mechanism): those three directories are
    exactly what `FieldPlan` and `RuledBlankWriting` already need, this
    module already knows how to read them, and `python3 tools/formgen/
    emit.py --self-test` is a check an operator runs directly and one of the
    ten modules `gate.py`'s own `SELF_TEST_MODULES` runs on every gate round
    -- the same enforcement point as every other assertion in this file.
    """
    ir_dir = _ROOT / "build/ir"
    layout_dir = _ROOT / "build/layout"
    plan_dir = _ROOT / "build/fonts"
    ir_paths = sorted(ir_dir.glob("*.ir.json"))
    if not ir_paths:
        _check(False, "every underscore-drawn writing line has an input, corpus-wide",
               f"no build/ir corpus at {ir_dir}", failures)
        return

    forms_checked = 0
    rules_checked = 0
    unfilled: list[str] = []
    shaded_claims = 0
    shaded_unfilled: list[str] = []
    for ir_path in ir_paths:
        slug = ir_path.name[: -len(".ir.json")]
        layout_path = layout_dir / f"{slug}.layout.json"
        plan_path = plan_dir / f"{slug}.fontplan.json"
        if not layout_path.is_file() or not plan_path.is_file():
            unfilled.append(f"{slug}: no layout/font plan to check against")
            continue
        ir = json.loads(ir_path.read_text(encoding="utf-8"))
        layout = json.loads(layout_path.read_text(encoding="utf-8"))
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        face = resolve_field_face(plan, [])
        if face is None:
            continue
        fields = FieldPlan(layout, face, [], ir)
        forms_checked += 1
        rules_by_page = {int(p["index"]): p["rules"] for p in ir["pages"]}
        fills_by_page = {int(p["index"]): p["area_fills"] for p in ir["pages"]}
        for page in layout["pages"]:
            page_index = int(page["index"])
            ruled_blanks = RuledBlankWriting(
                rules_by_page.get(page_index, ()), page["cells"])
            shading = DecorativeShading(fills_by_page.get(page_index, []))
            for cell in page["cells"]:
                claimed = ruled_blanks.for_cell(cell["id"])
                if not claimed:
                    continue
                rules_checked += len(claimed)
                has_input = fields.of(cell["id"]) is not None
                if not has_input:
                    unfilled.append(
                        f"{slug} {cell['id']}: {len(claimed)} underscore-drawn "
                        f"rule(s) claimed, no typing surface")
                # F218: `field_verdict`'s ruled-blank branch deliberately does
                # NOT consult `shading` -- an underscore-drawn writing line
                # beats decorative tint, the one place the two are meant to
                # disagree (see the branch's own comment for the 41/7 measured
                # split). Asserted in THIS direction, the mirror of
                # `row_number_corpus_assertions`' own shaded check: a claim
                # on shaded paper must STILL have a typing surface, so the
                # override cannot rot into a silent loss the day someone
                # "fixes" it back to consulting shading.
                if shading.blocks(cell):
                    shaded_claims += 1
                    if not has_input:
                        shaded_unfilled.append(
                            f"{slug} {cell['id']}: shaded ruled-blank claim "
                            f"lost its typing surface")

    # rules_checked > 0 is load-bearing, not decoration: a discovery mechanism
    # that silently claimed nothing would leave `unfilled` empty too, and
    # "not unfilled" alone would pass having verified nothing at all.
    _check(forms_checked > 0 and rules_checked > 0 and not unfilled,
           "every underscore-drawn writing line has an input, corpus-wide",
           f"{forms_checked} form(s), {rules_checked} rule(s) claimed, "
           f"{len(unfilled)} without a typing surface"
           + (f" ({'; '.join(unfilled[:6])}{'...' if len(unfilled) > 6 else ''})"
              if unfilled else ""),
           failures)
    # shaded_claims > 0 is load-bearing for the identical reason: without a
    # tinted claim in the corpus this proves nothing about the override.
    _check(shaded_claims > 0 and not shaded_unfilled,
           "a ruled blank on SHADED paper still has an input -- an explicit "
           "writing line beats decorative tint, corpus-wide",
           f"{shaded_claims} shaded ruled-blank claim(s), {len(shaded_unfilled)} "
           f"lost their typing surface to shading"
           + (f" ({'; '.join(shaded_unfilled[:6])}"
              f"{'...' if len(shaded_unfilled) > 6 else ''})" if shaded_unfilled else ""),
           failures)


def checkbox_square_corpus_assertions(failures: list[str]) -> None:
    """Every checkbox square earns its label cell an input -- corpus-wide,
    not just on the one pinned form the rest of this self-test exercises.

    F210 was found by the user tabbing through 1701 page 2 by hand and
    confirmed real by the tone-aware `?debug=fields` overlay. That is the
    class this check ends, the same shape `ruled_blank_corpus_assertions`
    already gives F148/F149: it re-derives, independently of whatever
    `batch.py` last emitted, which squares `CheckboxSquareWriting` would
    admit on EVERY form this checkout has extracted, and fails loudly if any
    of them lacks a typing surface. A form added to `build/ir` after this
    check was written is covered the moment it is extracted, by construction
    -- nothing here names a slug.

    Lives beside `ruled_blank_corpus_assertions` for the identical reason:
    `build/ir` + `build/layout` + `build/fonts` are exactly what `FieldPlan`
    and `CheckboxSquareWriting` already need, this module already knows how
    to read them, and `python3 tools/formgen/emit.py --self-test` is a check
    an operator runs directly and one of the ten modules `gate.py`'s own
    `SELF_TEST_MODULES` runs on every gate round.
    """
    ir_dir = _ROOT / "build/ir"
    layout_dir = _ROOT / "build/layout"
    plan_dir = _ROOT / "build/fonts"
    ir_paths = sorted(ir_dir.glob("*.ir.json"))
    if not ir_paths:
        _check(False, "every checkbox square has an input, corpus-wide",
               f"no build/ir corpus at {ir_dir}", failures)
        return

    forms_checked = 0
    squares_checked = 0
    unfilled: list[str] = []
    for ir_path in ir_paths:
        slug = ir_path.name[: -len(".ir.json")]
        layout_path = layout_dir / f"{slug}.layout.json"
        plan_path = plan_dir / f"{slug}.fontplan.json"
        if not layout_path.is_file() or not plan_path.is_file():
            unfilled.append(f"{slug}: no layout/font plan to check against")
            continue
        ir = json.loads(ir_path.read_text(encoding="utf-8"))
        layout = json.loads(layout_path.read_text(encoding="utf-8"))
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        face = resolve_field_face(plan, [])
        if face is None:
            continue
        fields = FieldPlan(layout, face, [], ir)
        forms_checked += 1
        rules_by_page = {int(p["index"]): p["rules"] for p in ir["pages"]}
        fills_by_page = {int(p["index"]): p["area_fills"] for p in ir["pages"]}
        for page in layout["pages"]:
            checkbox_squares = CheckboxSquareWriting(
                rules_by_page.get(int(page["index"]), ()),
                fills_by_page.get(int(page["index"]), ()),
                page["cells"])
            for cell in page["cells"]:
                claimed = checkbox_squares.for_cell(cell["id"])
                if not claimed:
                    continue
                squares_checked += len(claimed)
                if fields.of(cell["id"]) is None:
                    unfilled.append(
                        f"{slug} {cell['id']}: {len(claimed)} checkbox "
                        f"square(s) claimed, no typing surface")

    # squares_checked > 0 is load-bearing, not decoration: a discovery
    # mechanism that silently claimed nothing would leave `unfilled` empty
    # too, and "not unfilled" alone would pass having verified nothing at all.
    _check(forms_checked > 0 and squares_checked > 0 and not unfilled,
           "every checkbox square has an input, corpus-wide",
           f"{forms_checked} form(s), {squares_checked} square(s) claimed, "
           f"{len(unfilled)} without a typing surface"
           + (f" ({'; '.join(unfilled[:6])}{'...' if len(unfilled) > 6 else ''})"
              if unfilled else ""),
           failures)


def signature_box_corpus_assertions(failures: list[str]) -> None:
    """Every recognised signature box has an input, and no Bureau-reserved
    box in the identical population ever does -- corpus-wide, not just on
    the one pinned form the rest of this self-test exercises (F211).

    Re-derives, independently of whatever `batch.py` last emitted, both
    halves of F211's own claim against EVERY form this checkout has
    extracted -- the same shape `ruled_blank_corpus_assertions`'s and
    `checkbox_square_corpus_assertions`'s own checks already give F148/F149
    and F210, and for the identical reason: a form added to `build/ir` after
    this check was written is covered the moment it is extracted, by
    construction, nothing here names a slug.

    The two halves share one geometric population,
    `signature_box_candidates`' own 126 cells, split by caption:

      * every candidate `SignatureBoxWriting` claims (its caption matches
        `_signature_box_caption`) must have a typing surface;
      * every candidate whose caption instead matches `_bureau_caption`
        (`BUREAU_RESERVED_PREFIXES` / `BUREAU_RESERVED_SUBSTRINGS`) must NOT
        -- this is CLAUDE.md's own measurement, "no Bureau-reserved box has
        one", proved on the corpus each run rather than assumed from the two
        caption vocabularies sharing no word.

    Both counts asserted `> 0` is load-bearing, not decoration, in both
    directions -- following `ruled_blank_corpus_assertions`'s own comment on
    the identical guard: a discovery mechanism that silently claimed nothing
    would leave the corresponding failure list empty too, and "not unfilled"
    (or "not leaked") alone would pass having verified nothing at all.
    """
    ir_dir = _ROOT / "build/ir"
    layout_dir = _ROOT / "build/layout"
    plan_dir = _ROOT / "build/fonts"
    ir_paths = sorted(ir_dir.glob("*.ir.json"))
    if not ir_paths:
        _check(False, "every signature box has an input, corpus-wide",
               f"no build/ir corpus at {ir_dir}", failures)
        _check(False, "no Bureau-reserved box in the signature-box population has an input",
               f"no build/ir corpus at {ir_dir}", failures)
        return

    forms_checked = 0
    signature_claimed = 0
    bureau_candidates = 0
    unfilled: list[str] = []
    leaked: list[str] = []
    for ir_path in ir_paths:
        slug = ir_path.name[: -len(".ir.json")]
        layout_path = layout_dir / f"{slug}.layout.json"
        plan_path = plan_dir / f"{slug}.fontplan.json"
        if not layout_path.is_file() or not plan_path.is_file():
            unfilled.append(f"{slug}: no layout/font plan to check against")
            continue
        ir = json.loads(ir_path.read_text(encoding="utf-8"))
        layout = json.loads(layout_path.read_text(encoding="utf-8"))
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        face = resolve_field_face(plan, [])
        if face is None:
            continue
        fields = FieldPlan(layout, face, [], ir)
        forms_checked += 1
        runs_by_page = {int(p["index"]): p["text_runs"] for p in ir["pages"]}
        for page in layout["pages"]:
            page_index = int(page["index"])
            runs = runs_by_page.get(page_index, ())
            for cell, _cell_runs, caption in signature_box_candidates(
                    page["cells"], page_index, runs):
                if _signature_box_caption(caption):
                    signature_claimed += 1
                    if fields.of(cell["id"]) is None:
                        unfilled.append(
                            f"{slug} {cell['id']}: signature box claimed, "
                            f"no typing surface")
                elif _bureau_caption(caption):
                    bureau_candidates += 1
                    if fields.of(cell["id"]) is not None:
                        leaked.append(
                            f"{slug} {cell['id']}: Bureau-captioned box has "
                            f"a typing surface")

    _check(forms_checked > 0 and signature_claimed > 0 and not unfilled,
           "every recognised signature box has an input, corpus-wide",
           f"{forms_checked} form(s), {signature_claimed} signature box(es) "
           f"claimed, {len(unfilled)} without a typing surface"
           + (f" ({'; '.join(unfilled[:6])}{'...' if len(unfilled) > 6 else ''})"
              if unfilled else ""),
           failures)
    _check(bureau_candidates > 0 and not leaked,
           "no Bureau-reserved box in the signature-box population has an input",
           f"{bureau_candidates} Bureau-captioned candidate(s), {len(leaked)} "
           f"leaked a typing surface"
           + (f" ({'; '.join(leaked[:6])}{'...' if len(leaked) > 6 else ''})"
              if leaked else ""),
           failures)


def signature_line_corpus_assertions(failures: list[str]) -> None:
    """Every signature-line binding seats its writing box at the cell's own
    bottom, exactly one line tall, and centres its input -- corpus-wide, not
    just on the one pinned form the rest of this self-test exercises (F212).

    Lives beside `signature_box_corpus_assertions` for the identical reason:
    `SignatureLineBinding` and `FieldPlan` already need `build/ir` +
    `build/layout` + `build/fonts`, this module already knows how to read
    them, and re-deriving the claim set independently of whatever
    `batch.py` last emitted is what makes this a corpus check rather than a
    one-form spot check.
    """
    ir_dir = _ROOT / "build/ir"
    layout_dir = _ROOT / "build/layout"
    plan_dir = _ROOT / "build/fonts"
    ir_paths = sorted(ir_dir.glob("*.ir.json"))
    if not ir_paths:
        _check(False, "every signature line is bottom-seated and centred, corpus-wide",
               f"no build/ir corpus at {ir_dir}", failures)
        return

    forms_checked = 0
    lines_checked = 0
    wrong: list[str] = []
    for ir_path in ir_paths:
        slug = ir_path.name[: -len(".ir.json")]
        layout_path = layout_dir / f"{slug}.layout.json"
        plan_path = plan_dir / f"{slug}.fontplan.json"
        if not layout_path.is_file() or not plan_path.is_file():
            wrong.append(f"{slug}: no layout/font plan to check against")
            continue
        ir = json.loads(ir_path.read_text(encoding="utf-8"))
        layout = json.loads(layout_path.read_text(encoding="utf-8"))
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        face = resolve_field_face(plan, [])
        if face is None:
            continue
        fields = FieldPlan(layout, face, [], ir)
        forms_checked += 1
        one_line_pt = face.size_pt * face.line_span_em
        runs_by_page = {int(p["index"]): p["text_runs"] for p in ir["pages"]}
        for page in layout["pages"]:
            page_index = int(page["index"])
            runs = runs_by_page.get(page_index, ())
            signature_boxes = SignatureBoxWriting(page["cells"], page_index, runs)
            signature_lines = SignatureLineBinding(
                page["cells"], page_index, runs, signature_boxes.cell_ids())
            for cell in page["cells"]:
                if not signature_lines.for_cell(cell["id"]):
                    continue
                lines_checked += 1
                box = fields.of(cell["id"])
                if box is None:
                    wrong.append(f"{slug} {cell['id']}: bound, no typing surface")
                    continue
                if cell["id"] not in fields.centered:
                    wrong.append(f"{slug} {cell['id']}: bound, not centred")
                    continue
                # Compared rounded-to-rounded: `box.line_height_pt` is always
                # `round(x, 4)` of something (`seat_signature_line` rounds
                # its own output; an unmodified box's own producer already
                # rounds `height` the same way), so comparing it against the
                # UNROUNDED `one_line_pt` can be off by up to half of 1e-4 --
                # far more than a float-fuzz epsilon -- and reports a box
                # this same module just fitted exactly to one line as
                # "more than one line tall".
                if box.line_height_pt > round(one_line_pt, 4) + 1e-6:
                    wrong.append(
                        f"{slug} {cell['id']}: box is {fmt(box.line_height_pt)}pt "
                        f"tall, more than one line ({fmt(one_line_pt)}pt)")

    _check(forms_checked > 0 and lines_checked > 0 and not wrong,
           "every signature line is bottom-seated, one line tall and centred, "
           "corpus-wide",
           f"{forms_checked} form(s), {lines_checked} signature line(s) bound, "
           f"{len(wrong)} wrong"
           + (f" ({'; '.join(wrong[:6])}{'...' if len(wrong) > 6 else ''})"
              if wrong else ""),
           failures)


def knockout_specify_corpus_assertions(failures: list[str]) -> None:
    """Every knockout-over-tint band beside a "(specify)" caption has an
    input, corpus-wide -- the same shape `ruled_blank_corpus_assertions`,
    `checkbox_square_corpus_assertions` and `signature_box_corpus_assertions`
    already give F148/F149, F210 and F211 (F206): it re-derives, independently
    of whatever `batch.py` last emitted, which cells `KnockoutSpecifyWriting`
    would admit on EVERY form this checkout has extracted, and fails loudly if
    any of them lacks a typing surface. A form added to `build/ir` after this
    check was written is covered the moment it is extracted, by construction
    -- nothing here names a slug.

    Also cross-checks `_min_fillable_line_metrics` -- the restated computation
    this module needs because it carries no import of `lattice.py` -- against
    `lattice.min_fillable_line_metrics` directly, on every form this corpus
    holds, so a drift between the two trips HERE rather than silently sizing
    a band against the wrong threshold. `lattice` is imported locally, only
    inside this one check, so it stays no part of the load-bearing pipeline.
    """
    ir_dir = _ROOT / "build/ir"
    layout_dir = _ROOT / "build/layout"
    plan_dir = _ROOT / "build/fonts"
    ir_paths = sorted(ir_dir.glob("*.ir.json"))
    if not ir_paths:
        _check(False, "every knockout-specify band has an input, corpus-wide",
               f"no build/ir corpus at {ir_dir}", failures)
        return

    sys.path.insert(0, str(_ROOT / "tools/formgen"))
    import lattice  # local import: see the docstring above for why

    forms_checked = 0
    bands_checked = 0
    unfilled: list[str] = []
    metric_drift: list[str] = []
    for ir_path in ir_paths:
        slug = ir_path.name[: -len(".ir.json")]
        layout_path = layout_dir / f"{slug}.layout.json"
        plan_path = plan_dir / f"{slug}.fontplan.json"
        if not layout_path.is_file() or not plan_path.is_file():
            unfilled.append(f"{slug}: no layout/font plan to check against")
            continue
        ir = json.loads(ir_path.read_text(encoding="utf-8"))
        layout = json.loads(layout_path.read_text(encoding="utf-8"))
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        face = resolve_field_face(plan, [])
        if face is None:
            continue

        restated = _min_fillable_line_metrics(ir)
        canonical = lattice.min_fillable_line_metrics(ir)
        if restated != canonical:
            metric_drift.append(f"{slug}: restated {restated} != lattice {canonical}")

        fields = FieldPlan(layout, face, [], ir)
        forms_checked += 1
        runs_by_page = {int(p["index"]): p["text_runs"] for p in ir["pages"]}
        fills_by_page = {int(p["index"]): p["area_fills"] for p in ir["pages"]}
        for page in layout["pages"]:
            page_index = int(page["index"])
            runs = runs_by_page.get(page_index, ())
            fills = fills_by_page.get(page_index, ())
            knockout_specify = KnockoutSpecifyWriting(
                page["cells"], page_index, runs, fills, restated)
            for cell in page["cells"]:
                band = knockout_specify.for_cell(cell["id"])
                if band is None:
                    continue
                bands_checked += 1
                if fields.of(cell["id"]) is None:
                    unfilled.append(
                        f"{slug} {cell['id']}: knockout-specify band claimed, "
                        f"no typing surface")

    _check(not metric_drift,
           "the restated line-fit metric matches lattice.min_fillable_line_metrics, "
           "corpus-wide",
           f"{len(metric_drift)} form(s) drifted"
           + (f" ({'; '.join(metric_drift[:6])}{'...' if len(metric_drift) > 6 else ''})"
              if metric_drift else ""),
           failures)
    # bands_checked > 0 is load-bearing, not decoration, following every
    # sibling corpus assertion's own comment on the identical guard: a
    # discovery mechanism that silently claimed nothing would leave
    # `unfilled` empty too, and "not unfilled" alone would pass having
    # verified nothing at all.
    _check(forms_checked > 0 and bands_checked > 0 and not unfilled,
           "every knockout-specify band has an input, corpus-wide",
           f"{forms_checked} form(s), {bands_checked} band(s) claimed, "
           f"{len(unfilled)} without a typing surface"
           + (f" ({'; '.join(unfilled[:6])}{'...' if len(unfilled) > 6 else ''})"
              if unfilled else ""),
           failures)


def row_number_corpus_assertions(failures: list[str]) -> None:
    """Every bare row number beside a fillable field has an input beside it,
    corpus-wide -- the same shape `ruled_blank_corpus_assertions`,
    `checkbox_square_corpus_assertions`, `signature_box_corpus_assertions` and
    `knockout_specify_corpus_assertions` already give F148/F149, F210,
    F211/F212 and F206 (F151, P2's row-number rule): it re-derives,
    independently of whatever `batch.py` last emitted, which cells
    `RowNumberWriting` would admit on EVERY form this checkout has extracted,
    and fails loudly if any of them lacks a typing surface. A form added to
    `build/ir` after this check was written is covered the moment it is
    extracted, by construction -- nothing here names a slug.

    Lives beside `knockout_specify_corpus_assertions` for the identical
    reason: `build/ir` + `build/layout` + `build/fonts` are exactly what
    `FieldPlan` and `RowNumberWriting` already need, this module already
    knows how to read them, and `python3 tools/formgen/emit.py --self-test`
    is a check an operator runs directly and one of the ten modules
    `gate.py`'s own `SELF_TEST_MODULES` runs on every gate round.
    """
    ir_dir = _ROOT / "build/ir"
    layout_dir = _ROOT / "build/layout"
    plan_dir = _ROOT / "build/fonts"
    ir_paths = sorted(ir_dir.glob("*.ir.json"))
    if not ir_paths:
        _check(False, "every bare row number has an input beside it, corpus-wide",
               f"no build/ir corpus at {ir_dir}", failures)
        return

    forms_checked = 0
    claims_checked = 0
    shaded_claims = 0
    unfilled: list[str] = []
    leaked: list[str] = []
    for ir_path in ir_paths:
        slug = ir_path.name[: -len(".ir.json")]
        layout_path = layout_dir / f"{slug}.layout.json"
        plan_path = plan_dir / f"{slug}.fontplan.json"
        if not layout_path.is_file() or not plan_path.is_file():
            unfilled.append(f"{slug}: no layout/font plan to check against")
            continue
        ir = json.loads(ir_path.read_text(encoding="utf-8"))
        layout = json.loads(layout_path.read_text(encoding="utf-8"))
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        face = resolve_field_face(plan, [])
        if face is None:
            continue
        fields = FieldPlan(layout, face, [], ir)
        forms_checked += 1
        metrics = _min_fillable_line_metrics(ir)
        runs_by_page = {int(p["index"]): p["text_runs"] for p in ir["pages"]}
        fills_by_page = {int(p["index"]): p.get("area_fills") or []
                         for p in ir["pages"]}
        for page in layout["pages"]:
            page_index = int(page["index"])
            runs = runs_by_page.get(page_index, ())
            row_numbers = RowNumberWriting(page["cells"], page_index, runs, metrics)
            shading = DecorativeShading(fills_by_page.get(page_index, []))
            for cell in page["cells"]:
                band = row_numbers.for_cell(cell["id"])
                if band is None:
                    continue
                claims_checked += 1
                if shading.blocks(cell):
                    # A row number on shaded paper is the sheet's printed
                    # index. Asserted in the OTHER direction so the exclusion
                    # cannot rot into a silent skip: it must have no input.
                    shaded_claims += 1
                    if fields.of(cell["id"]) is not None:
                        leaked.append(
                            f"{slug} {cell['id']}: row number on shaded paper "
                            f"has a typing surface")
                    continue
                if fields.of(cell["id"]) is None:
                    unfilled.append(
                        f"{slug} {cell['id']}: row-number band claimed, "
                        f"no typing surface")

    # claims_checked > 0 is load-bearing, not decoration, following every
    # sibling corpus assertion's own comment on the identical guard: a
    # discovery mechanism that silently claimed nothing would leave
    # `unfilled` empty too, and "not unfilled" alone would pass having
    # verified nothing at all.
    _check(forms_checked > 0 and claims_checked > 0 and not unfilled,
           "every bare row number on UNSHADED paper has an input beside it, "
           "corpus-wide",
           f"{forms_checked} form(s), {claims_checked} row-number cell(s) "
           f"claimed, {len(unfilled)} without a typing surface"
           + (f" ({'; '.join(unfilled[:6])}{'...' if len(unfilled) > 6 else ''})"
              if unfilled else ""),
           failures)
    _check(shaded_claims > 0 and not leaked,
           "no row number on SHADED paper has an input -- the sheet's own "
           "printed index is not a writing surface",
           f"{shaded_claims} shaded row-number cell(s), {len(leaked)} leaked "
           f"a typing surface"
           + (f" ({'; '.join(leaked[:6])}{'...' if len(leaked) > 6 else ''})"
              if leaked else ""),
           failures)


def signature_rule_corpus_assertions(failures: list[str]) -> None:
    """Every vector-drawn signature line a `label` cell owns at its own
    bottom wall, with a matching "Signature over Printed Name" caption in
    the cell below, has an input -- corpus-wide (F221 case 1), the same
    shape `ruled_blank_corpus_assertions`, `checkbox_square_corpus_
    assertions`, `signature_box_corpus_assertions`, `signature_line_corpus_
    assertions` and `row_number_corpus_assertions` already give their own
    findings: it re-derives, independently of whatever `batch.py` last
    emitted, which cells `SignatureRuleWriting` would admit on EVERY form
    this checkout has extracted, and fails loudly if any of them lacks a
    typing surface. A form added to `build/ir` after this check was written
    is covered the moment it is extracted, by construction -- nothing here
    names a slug.

    The corpus carries no Bureau-reserved or shaded case in this population
    to assert the second direction against (measured: 0 of the 9 real claims
    are either), so that half is proved on synthetic geometry instead --
    `signature_rule_writing_assertions`, `knockout_specify_writing_
    assertions`' own precedent for a gate the real corpus never exercises.

    F226: also re-derives, over the SAME corpus, the sliver-gap population
    the class's own gap extension adds -- `gap_bound_count()` sums to the
    number of rules claimed across a genuine (non-wall) gap, corpus-wide
    (2316-2021's own item 53/54, and nothing else: no other form carries a
    `label` cell whose owned rule has an ink-free, sub-`glyph_height_pt` gap
    to exactly one caption below it), and `gap_refused_count()` sums the
    real candidate captions this same search found and correctly declined
    -- too tall, inked, or both, `p1c322`'s own h178/`p1c327` case among
    them. Both are asserted `> 0`: a positive-only proof would not show the
    guard ever says no.
    """
    ir_dir = _ROOT / "build/ir"
    layout_dir = _ROOT / "build/layout"
    plan_dir = _ROOT / "build/fonts"
    ir_paths = sorted(ir_dir.glob("*.ir.json"))
    if not ir_paths:
        _check(False, "every owned signature line has an input, corpus-wide",
               f"no build/ir corpus at {ir_dir}", failures)
        return

    forms_checked = 0
    rules_checked = 0
    gap_bound_total = 0
    gap_refused_total = 0
    unfilled: list[str] = []
    for ir_path in ir_paths:
        slug = ir_path.name[: -len(".ir.json")]
        layout_path = layout_dir / f"{slug}.layout.json"
        plan_path = plan_dir / f"{slug}.fontplan.json"
        if not layout_path.is_file() or not plan_path.is_file():
            unfilled.append(f"{slug}: no layout/font plan to check against")
            continue
        ir = json.loads(ir_path.read_text(encoding="utf-8"))
        layout = json.loads(layout_path.read_text(encoding="utf-8"))
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        face = resolve_field_face(plan, [])
        if face is None:
            continue
        fields = FieldPlan(layout, face, [], ir)
        forms_checked += 1
        rules_by_page = {int(p["index"]): p["rules"] for p in ir["pages"]}
        runs_by_page = {int(p["index"]): p["text_runs"] for p in ir["pages"]}
        fillable_metrics = _min_fillable_line_metrics(ir)
        for page in layout["pages"]:
            page_index = int(page["index"])
            signature_rules = SignatureRuleWriting(
                page["cells"], page_index,
                rules_by_page.get(page_index, ()), runs_by_page.get(page_index, ()),
                fillable_metrics)
            gap_bound_total += signature_rules.gap_bound_count()
            gap_refused_total += signature_rules.gap_refused_count()
            for cell in page["cells"]:
                claimed = signature_rules.for_cell(cell["id"])
                if not claimed:
                    continue
                rules_checked += len(claimed)
                if fields.of(cell["id"]) is None:
                    unfilled.append(
                        f"{slug} {cell['id']}: {len(claimed)} owned signature "
                        f"line(s) claimed, no typing surface")

    # rules_checked > 0 is load-bearing, not decoration: a discovery mechanism
    # that silently claimed nothing would leave `unfilled` empty too, and
    # "not unfilled" alone would pass having verified nothing at all.
    _check(forms_checked > 0 and rules_checked > 0 and not unfilled,
           "every owned signature line has an input, corpus-wide",
           f"{forms_checked} form(s), {rules_checked} rule(s) claimed, "
           f"{len(unfilled)} without a typing surface"
           + (f" ({'; '.join(unfilled[:6])}{'...' if len(unfilled) > 6 else ''})"
              if unfilled else ""),
           failures)
    # F226: the sliver-gap extension's own two witnesses, both directions --
    # something is actually bound across a gap, and something real is
    # actually refused across a gap. Neither alone would prove the guard is
    # not vacuous.
    _check(gap_bound_total > 0,
           "the sliver-gap extension claims at least one rule across a "
           "genuine (non-wall) gap, corpus-wide",
           f"{gap_bound_total} rule(s) gap-bound", failures)
    _check(gap_refused_total > 0,
           "the sliver-gap extension also refuses at least one real "
           "candidate caption across a genuine gap, corpus-wide",
           f"{gap_refused_total} candidate(s) refused", failures)


def _synthetic_comb(cells: int, x0: float, pitch: float,
                    y0: float, height: float,
                    writing: tuple[float, float] | None = None) -> dict[str, Any]:
    """A comb subject. `writing` is (top, height) for the writing rectangle.

    Omitted, the subject carries the divider band alone -- a layout written
    before `lattice.comb_on_writing_surface` published a writing rectangle, and
    the fallback branch of `comb_writing_rect`.
    """
    comb = {"cells": cells, "pitch_pt": pitch, "y0": y0, "height_pt": height,
            "y1": y0 + height,
            "slot_x": [x0 + index * pitch for index in range(cells + 1)]}
    if writing is not None:
        comb["writing_y0"] = writing[0]
        comb["writing_y1"] = writing[0] + writing[1]
        comb["writing_height_pt"] = writing[1]
    return comb


def _synthetic_cell(cell_id: str, y0: float, y1: float,
                    comb: dict[str, Any] | None = None) -> dict[str, Any]:
    cell: dict[str, Any] = {"id": cell_id, "kind": "field", "row": 0, "col": 0,
                            "x0": 0.0, "x1": 120.0, "y0": y0, "y1": y1}
    if comb is not None:
        cell["comb"] = comb
    return cell


_SELF_TEST_STYLE_RE = re.compile(
    r"top:(-?[\d.]+)pt;width:(-?[\d.]+)pt;height:(-?[\d.]+)pt")
_SELF_TEST_INSET_RE = re.compile(
    r'id="([^"]+)"[^>]*style="inset:([^"]*)"')


def _synthetic_run(text: str, x0: float, baseline: float, size: float,
                   ascender: float = 0.9, descender: float = -0.21
                   ) -> dict[str, Any]:
    """One printed run at a fixed pitch, with the per-glyph metrics the IR has."""
    advance = size * 0.5
    return {
        "text": text, "font": "Arial", "family": "Arial", "size_pt": size,
        "baseline_y": baseline, "origin_x": x0,
        "x0": x0, "x1": x0 + advance * len(text),
        "y0": baseline - ascender * size, "y1": baseline - descender * size,
        "ascender": ascender, "descender": descender,
        "char_origin_offsets_pt": [advance * i for i in range(len(text))],
        "char_widths_pt": [advance] * len(text),
        "char_advances_pt": [advance] * len(text),
        "rotated": False,
    }


def writing_box_assertions(plan: dict[str, Any], failures: list[str]) -> None:
    """The writing box is the paper the source left blank, and it can be several.

    Two relations, each proved by mutating ONE thing about a fixture the
    corpus' own artwork is drawn from:

      * `writing_box_clear_of_printed_ink` -- the box begins where the sheet's
        ink ends, on the side the ink comes from. 1604CF's "Telephone No."
        hangs a descender 0.67pt into the box below it and 2551M prints `14E`
        so its first digit overhangs the box to its left by 0.72pt. Five
        mutations put the same glyph on each of the four sides and in the
        middle. Three trim; the middle one implies no side; and the one BELOW
        the box does not trim either, because the bound the line box gives on a
        run's upward ink is the ascent line and that is loose by most of an em
        -- see the function's own docstring for the measurement and for what
        acting on it costs 2550M.
      * `writing_regions` -- a cell the source rules across is several places
        to write. Three mutations: a stroke that divides, the same stroke
        reaching in by less than its own width (a neighbour's tick), and the
        same stroke moved onto the box's edge (its own wall).

    A comb is exempt from both, and that is asserted rather than assumed: its
    slot rectangle is `comb_referee`'s contract with the source's painted
    walls, so ink inside a compartment is a fact to report and never a
    rectangle this file may move.
    """
    face = resolve_field_face(plan, [])
    if face is None:
        _check(False, "the font plan resolves a body face to fit fields to",
               "no metric-compatible face", failures)
        return

    # A 20pt-tall cell spanning x 0..120, and one 8pt glyph run placed against
    # each of its four sides in turn. `p` descends; the run's line box is what
    # a layout must keep clear either way.
    cell = _synthetic_cell("p0c0", 40.0, 60.0)
    above = _synthetic_run("pp", 50.0, 40.6, 8.0)      # line box 33.4..42.28
    below = _synthetic_run("pp", 50.0, 66.0, 8.0)      # line box 58.8..67.68
    # One glyph each, straddling a side wall with its own centre outside it --
    # 2551M's `14E` and 2553's `28C` overhang exactly this way.
    at_left = _synthetic_run("p", -3.0, 50.0, 8.0)     # advance box -3..1
    at_right = _synthetic_run("p", 119.0, 50.0, 8.0)   # advance box 119..123
    middle = _synthetic_run("pp", 56.0, 50.0, 8.0)     # wholly inside

    def inset_of(runs: Sequence[dict[str, Any]]
                 ) -> tuple[float, float, float, float] | None:
        box = field_box(cell, face, PrePrintedInk(runs))
        return None if box is None else box.inset_trbl

    plain = inset_of(())
    _check(plain is None,
           "a cell the source prints nothing into keeps the box its rules give it",
           f"inset {plain}", failures)
    for label, runs, expected in (
            ("top", [above], (2.28, 0.0, 0.0, 0.0)),
            ("left", [at_left], (0.0, 0.0, 0.0, 1.0)),
            ("right", [at_right], (0.0, 1.0, 0.0, 0.0)),
    ):
        measured = inset_of(runs)
        _check(measured is not None
               and tuple(round(value, 4) for value in measured) == expected,
               f"ink printed at the {label} moves the {label} of the writing box "
               f"and no other side",
               f"{measured} != {expected}", failures)
    # The fourth side, and it does NOT move. `below`'s line box reaches 1.2pt
    # into the bottom of the box, but that edge of a run's line box is its
    # ASCENT line -- where the face could put ink, not where this run does. The
    # identical run's DESCENT edge, which the trim above does act on, is tight
    # to a fraction of a point. Acting on the loose one costs 2550M 2.61pt of a
    # 10.32pt box and a fitted face, for 3.77pt of blank paper.
    from_below = inset_of([below])
    _check(from_below is None,
           "ink printed BELOW the box does not move it: the line box bounds a "
           "run's ink downward and not upward",
           f"inset {from_below} for a line box 1.2pt inside the floor", failures)
    enclosed = inset_of([middle])
    _check(enclosed is None,
           "ink the sheet prints in the MIDDLE of a blank implies no side, so "
           "the box is left where the rules put it and reported elsewhere",
           f"inset {enclosed}", failures)

    # The comb SLOT stays the referee's contract -- `comb_writing_rect` takes
    # no `ink` argument at all, so its own rectangle cannot move regardless.
    # F227: the INPUT nested inside it can, off ink in the row's shared top,
    # exactly as a plain field's box does. `above`'s line box hangs 1.78pt
    # into this comb's writing top (40.5); `at_left` grazes its left rail but
    # must NOT move anything, because a comb's width belongs to one
    # compartment each while its height is already shared across the whole
    # row (see `comb_writing_top_clear_of_printed_ink`'s own docstring).
    comb = _synthetic_comb(4, 0.0, 30.0, 56.0, 4.0, (40.5, 19.0))
    comb_cell = _synthetic_cell("p0c1", 40.0, 60.0, comb)
    with_ink = field_box(comb_cell, face, PrePrintedInk([above, at_left]))
    without = field_box(comb_cell, face, None)
    left_only = field_box(comb_cell, face, PrePrintedInk([at_left]))
    _check(with_ink is not None and without is not None and left_only is not None
           and without.inset_trbl is None
           and with_ink.inset_trbl is not None
           and tuple(round(v, 4) for v in with_ink.inset_trbl) == (1.78, 0.0, 0.0, 0.0)
           and round(with_ink.line_height_pt, 4) == 17.22
           and round(without.line_height_pt, 4) == 19.0
           and left_only.inset_trbl is None
           and with_ink.regions is None,
           "printed ink above a comb insets the INPUT off its shared top "
           "(F227) and a graze on its left rail insets nothing",
           f"with_ink {with_ink.inset_trbl if with_ink else None} "
           f"({with_ink.line_height_pt if with_ink else None}pt) vs without "
           f"{without.inset_trbl if without else None} "
           f"({without.line_height_pt if without else None}pt) vs left_only "
           f"{left_only.inset_trbl if left_only else None}", failures)

    # `writing_regions`: one 0.5pt stroke down the middle of the box 0..120 /
    # 40..60, mutated one property at a time.
    box = (0.0, 40.0, 120.0, 60.0)
    divides = {"x0": 59.75, "y0": 58.0, "x1": 60.25, "y1": 64.0}
    grazes = {**divides, "y0": 59.6}
    wall = {"x0": 119.75, "y0": 40.0, "x1": 120.25, "y1": 60.0}
    _check(writing_regions(box, [divides])
           == [(0.0, 40.0, 59.75, 60.0), (60.25, 40.0, 120.0, 60.0)],
           "a stroke printed across the box divides it, at its own ink",
           f"{writing_regions(box, [divides])}", failures)
    _check(writing_regions(box, [grazes]) == [box],
           "the same stroke reaching in by less than its own width is the next "
           "row's tick and divides nothing",
           f"{writing_regions(box, [grazes])} for a 0.5pt stroke 0.4pt in",
           failures)
    _check(writing_regions(box, [wall]) == [box],
           "the same stroke drawn on the box's edge is the box's own wall",
           f"{writing_regions(box, [wall])}", failures)

    # ... and what the emitter does with them: one input per region, each with
    # its own inset and its own name, and the band template's JSON carrying the
    # same list so a row cloned at run time is not laid out on region 0.
    divided_cell = {**_synthetic_cell("p0c2", 40.0, 60.0),
                    "printed_partitions": [divides]}
    divided_plan = FieldPlan(
        {"pages": [{"index": 0, "cells": [divided_cell]}]}, face, [])
    divided_box = divided_plan.of("p0c2")
    markup = cell_markup(divided_cell, divided_plan)
    named = _SELF_TEST_INSET_RE.findall(markup)
    _check(divided_box is not None and divided_box.regions is not None
           and len(divided_box.regions) == 2
           and [name for name, _inset in named] == ["p0c2-i0", "p0c2-i1"]
           and [inset for _name, inset in named]
           == ["0pt 60.25pt 0pt 0pt", "0pt 0pt 0pt 60.25pt"],
           "a divided cell emits one named input per region, on the region",
           f"{named}", failures)
    _check(field_json(divided_box, divided_plan, "p0c2")["region_insets"]
           == [[0.0, 60.25, 0.0, 0.0], [0.0, 0.0, 0.0, 60.25]],
           "the band template carries every region, so a cloned row has them all",
           f"{field_json(divided_box, divided_plan, 'p0c2').get('region_insets')}",
           failures)
    single_plan = FieldPlan(
        {"pages": [{"index": 0, "cells": [_synthetic_cell("p0c3", 40.0, 60.0)]}]},
        face, [])
    single_box = single_plan.of("p0c3")
    _check(single_box is not None and single_box.regions is None
           and "region_insets" not in field_json(single_box, single_plan, "p0c3")
           and 'id="p0c3-i"' in cell_markup(
               _synthetic_cell("p0c3", 40.0, 60.0), single_plan),
           "an undivided cell keeps the one input and the one name it had",
           f"regions {single_box.regions if single_box else 'none'}", failures)

    # F219: `field_json`'s `centered` key must exist exactly where
    # `field_input_markup`'s own inline `text-align:center` does (`fields.
    # centered`, F212's own set), and be silently absent everywhere else --
    # proven directly against `FieldPlan.centered` rather than routed through
    # `SignatureLineBinding`'s caption detection, which is a separate,
    # already-proven mechanism (`signature_line_corpus_assertions`).
    _check("centered" not in field_json(single_box, single_plan, "p0c3"),
           "an uncentred field's band JSON carries no centered key at all",
           f"{field_json(single_box, single_plan, 'p0c3')}", failures)
    single_plan.centered.add("p0c3")
    _check(field_json(single_box, single_plan, "p0c3").get("centered") is True,
           "a centred field's band JSON carries centered:true, mirroring its "
           "pre-rendered inline text-align:center",
           f"{field_json(single_box, single_plan, 'p0c3')}", failures)
    _check("field.centered" in BAND_JS and "textAlign" in BAND_JS,
           "the band runtime mirrors a centred field's alignment into a row "
           "cloned at run time, not just the pre-rendered instance",
           "BAND_JS's fieldMetrics reads field.centered and sets textAlign",
           failures)

    # P1/P1b: maxlength=1 on hair-tick compartments and X-squares, never on a
    # wide column split, never as type=checkbox.
    _check('maxlength="' not in markup,
           "a wide column-split (ADDRESS vs STATUS shape) stays unbounded",
           markup, failures)
    tick = {"x0": 24.9, "y0": 10.0, "x1": 25.5, "y1": 16.5}
    charbox = {**_synthetic_cell("p0c5", 0.0, 16.0),
               "x1": 50.4, "printed_partitions": [tick]}
    char_plan = FieldPlan(
        {"pages": [{"index": 0, "cells": [charbox]}]}, face, [])
    char_box = char_plan.of("p0c5")
    char_markup = cell_markup(charbox, char_plan)
    char_json = field_json(char_box, char_plan, "p0c5", charbox) if char_box else {}
    _check(char_box is not None and char_box.regions is not None
           and len(char_box.regions) == 2
           and char_markup.count('maxlength="1"') == 2
           and char_json.get("maxlength_one") is True,
           "a hair-tick charbox stamps maxlength=1 on every compartment (P1)",
           f"regions {None if char_box is None else char_box.regions} "
           f"markup={char_markup} json={char_json}", failures)
    xbox = _synthetic_cell("p0c6", 0.0, 12.0)
    xbox["x1"] = 12.0
    xbox_plan = FieldPlan(
        {"pages": [{"index": 0, "cells": [xbox]}]}, face, [])
    xbox_box = xbox_plan.of("p0c6")
    xbox_markup = cell_markup(xbox, xbox_plan)
    xbox_json = field_json(xbox_box, xbox_plan, "p0c6", xbox) if xbox_box else {}
    _check(xbox_box is not None
           and xbox_markup.count('maxlength="1"') == 1
           and 'type="text"' in xbox_markup
           and "checkbox" not in xbox_markup
           and xbox_json.get("maxlength_one") is True,
           "an X-square stays type=text with maxlength=1 (P1b)",
           f"markup={xbox_markup} json={xbox_json}", failures)
    wide = _synthetic_cell("p0c7", 40.0, 60.0)
    wide_plan = FieldPlan(
        {"pages": [{"index": 0, "cells": [wide]}]}, face, [])
    wide_box = wide_plan.of("p0c7")
    wide_markup = cell_markup(wide, wide_plan)
    _check(wide_box is not None
           and 'maxlength="' not in wide_markup
           and "maxlength_one" not in field_json(wide_box, wide_plan, "p0c7", wide),
           "a wide undivided text field stays unbounded",
           wide_markup, failures)
    _check("fieldMaxlengthOne" in BAND_JS,
           "the band runtime stamps maxlength=1 on cloned single-character "
           "regions, not just the pre-rendered instance",
           "BAND_JS defines fieldMaxlengthOne", failures)


def _knockout_specify_fill(role: str, gray: float | None,
                           x0: float, y0: float, x1: float, y1: float,
                           seq: int) -> dict[str, Any]:
    return {"role": role, "gray": gray, "x0": x0, "y0": y0, "x1": x1, "y1": y1,
            "paint_seq": seq, "paint_seq_max": seq}


def _knockout_specify_run(text: str, x0: float, x1: float,
                          y0: float = 0.0, y1: float = 10.0) -> dict[str, Any]:
    return {"text": text, "x0": x0, "x1": x1, "y0": y0, "y1": y1}


def knockout_specify_writing_assertions(plan: dict[str, Any],
                                        failures: list[str]) -> None:
    """F206: a "(specify)" caption's own knockout-over-tint band earns an
    input, and the marker's own named residue does not.

    Four mutations of one 200 x 10pt `label` cell, each isolating the ONE
    property that separates `1801-2018` `p2c261` ("Others (specify)") from
    F206's own named residue -- 8 ATC-code/rate cells across 3 forms
    (0605-1999 p2c58/p2c121, 2553-1999 p1c37/p1c42/p1c47/p1c52, 1600wp-2010
    p1c30/p1c33), none a writing surface:

      * the base case -- a caption at the left, a decorative tint under it, a
        knockout to its right -- gets the knockout's own rectangle;
      * the identical geometry with "specify" dropped from the caption gets
        nothing, proving the caption gate `KnockoutSpecifyWriting` applies
        (this is what excludes 2553's "OT  0   1   0" and every other
        ATC-code caption in the corpus, none of which says "specify");
      * a decorative fill overlapping the cell by 0.2pt -- a neighbouring
        row's background grazing this cell's own edge, the exact shape at
        0605 p2c58 and 1600wp p1c30/p1c33 -- gets nothing, proving
        `KNOCKOUT_FILL_MEANINGFUL_OVERLAP_PT` is load-bearing and not a
        hairline the corpus never reaches;
      * a caption spanning all but 2pt of the cell on each side (less than
        `metrics["line_width_pt"]`) gets nothing even though every fill is a
        clean, meaningful knockout over a clean, meaningful tint, proving the
        whole-run-bbox ink block -- the shape at every one of 2553's four
        residue cells, whose "OT  0   1   0" caption's own bounding box
        reaches to within a few points of the cell's own walls.
    """
    face = resolve_field_face(plan, [])
    if face is None:
        _check(False, "the font plan resolves a body face to fit fields to",
               "no metric-compatible face", failures)
        return
    metrics = {"glyph_height_pt": 4.0, "line_width_pt": 12.0}
    page_index = 0

    def claim(cell_id: str, caption: str, caption_x1: float,
             decorative: tuple[float, float, float, float],
             knockout: tuple[float, float, float, float],
             ) -> tuple[float, float, float, float] | None:
        cell = {"id": cell_id, "kind": "label", "row": 0, "col": 0,
                "x0": 0.0, "y0": 0.0, "x1": 200.0, "y1": 10.0,
                "text_run_ids": [run_id(page_index, 0)]}
        runs = [_knockout_specify_run(caption, 2.0, caption_x1)]
        fills = [
            _knockout_specify_fill("decorative", 0.75, *decorative, 1),
            _knockout_specify_fill("knockout", 1.0, *knockout, 2),
        ]
        writing = KnockoutSpecifyWriting([cell], page_index, runs, fills, metrics)
        return writing.for_cell(cell_id)

    base = claim("p0k0", "Others (specify)", 48.0,
                (0.0, 0.0, 60.0, 10.0), (60.0, 0.0, 200.0, 10.0))
    _check(base == (60.0, 0.0, 200.0, 10.0),
           "a knockout beside a decorative tint, past a \"(specify)\" caption, "
           "earns its own rectangle",
           f"{base}", failures)

    no_specify = claim("p0k1", "Others", 48.0,
                       (0.0, 0.0, 60.0, 10.0), (60.0, 0.0, 200.0, 10.0))
    _check(no_specify is None,
           "the identical band with \"specify\" dropped from the caption is "
           "refused -- the caption gate, not the geometry, is what F206's "
           "ATC-code residue fails first",
           f"{no_specify}", failures)

    sliver_overlap = claim("p0k2", "Others (specify)", 48.0,
                           (0.0, -9.8, 200.0, 0.2), (0.0, 0.0, 200.0, 10.0))
    _check(sliver_overlap is None,
           "a decorative fill grazing this cell's own edge by 0.2pt -- a "
           "neighbouring row's background, not this cell's own tint -- is "
           "refused",
           f"{sliver_overlap}", failures)

    dense_caption = claim(
        "p0k3", "Others (specify) 0 1 0 1 0 1 0 1 0 1 0 1 0 1 0 1 0", 198.0,
        (0.0, 0.0, 200.0, 10.0), (0.0, 0.0, 200.0, 10.0))
    _check(dense_caption is None,
           "a caption reaching to within 2pt of the cell's own walls leaves "
           "no band wide enough to clear the form's own line width, even "
           "over a clean knockout-over-tint cell",
           f"{dense_caption}", failures)

    # End to end: the claimed band becomes an input, seated on its own
    # rectangle, through the same dispatch `field_verdict` and `FieldPlan`
    # use on the real corpus.
    box = knockout_specify_field_box(
        {"id": "p0k0", "x0": 0.0, "y0": 0.0, "x1": 200.0, "y1": 10.0},
        base, face, None)
    _check(box is not None and box.inset_trbl == (0.0, 0.0, 0.0, 60.0),
           "the field box is seated exactly on the claimed band",
           f"{box.inset_trbl if box else None}", failures)
    verdict = field_verdict(
        {"id": "p0k0", "kind": "label", "x0": 0.0, "y0": 0.0,
         "x1": 200.0, "y1": 10.0},
        None, None, None, knockout_specify=KnockoutSpecifyWriting(
            [{"id": "p0k0", "kind": "label", "x0": 0.0, "y0": 0.0,
              "x1": 200.0, "y1": 10.0,
              "text_run_ids": [run_id(page_index, 0)]}],
            page_index, [_knockout_specify_run("Others (specify)", 2.0, 48.0)],
            [_knockout_specify_fill("decorative", 0.75, 0.0, 0.0, 60.0, 10.0, 1),
             _knockout_specify_fill("knockout", 1.0, 60.0, 0.0, 200.0, 10.0, 2)],
            metrics))
    _check(verdict == (True, "knockout-specify"),
           "field_verdict routes a claimed cell through the knockout-specify "
           "reason",
           f"{verdict}", failures)


def signature_rule_writing_assertions(plan: dict[str, Any],
                                      failures: list[str]) -> None:
    """F221 case 1: a label cell's OWN vector-drawn rule earns it a writing
    surface exactly when a "Signature over Printed Name" caption sits in the
    cell directly below it, naming that rule -- and not otherwise. Paired
    positive/negative fixture assertions, each isolating the one property
    that separates 2550M's own item 27/28 (and eight more sites like them)
    from the corpus's much larger population of vector rules a `label` cell
    happens to own, plus the Bureau and shading gate the real corpus carries
    no case to exercise this against (`signature_rule_corpus_assertions`'s
    own note).

    F226: a second population of paired assertions for the sliver-gap
    extension -- a caption cell that does NOT share the rule-owner's own
    wall, across a genuine vertical gap. `far_caption` above already proves
    the `metrics=None` fallback (no line-fit metric to bound the gap by, so
    even a 1.0pt gap is refused, the pre-F226 behaviour exactly); these add
    the `metrics`-driven cases: a small, ink-free gap is bridged; the
    identical gap with the form's own glyph tall enough to reach across it
    is refused (`p1c322`'s own h178 shape, in miniature); and a gap wide
    enough on its own, whatever ink it carries, is refused too.
    """
    page_index = 0
    OWNER = {"id": "p0g0", "kind": "label", "row": 0, "col": 0,
            "x0": 0.0, "y0": 0.0, "x1": 300.0, "y1": 40.0, "text_run_ids": []}
    CAPTION_CELL = {"id": "p0g1", "kind": "label", "row": 1, "col": 0,
                    "x0": 0.0, "y0": 40.0, "x1": 300.0, "y1": 60.0,
                    "text_run_ids": [run_id(page_index, 0)]}

    def owned_rule(y0: float = 39.6, y1: float = 40.4, x0: float = 50.0,
                   x1: float = 250.0, origin: str = "vector") -> dict[str, Any]:
        return {"id": "hTEST", "axis": "h", "x0": x0, "x1": x1, "y0": y0, "y1": y1,
                "thickness_pt": round(y1 - y0, 4), "gray": 0.0,
                "role": "structural", "origin": origin}

    def caption_run(text: str = "Signature over Printed Name of Taxpayer",
                    x0: float = 100.0, x1: float = 200.0,
                    y0: float = 42.0, y1: float = 50.0) -> dict[str, Any]:
        return {"text": text, "x0": x0, "x1": x1, "y0": y0, "y1": y1}

    def claim(cells: Sequence[dict[str, Any]], rules: Sequence[dict[str, Any]],
             runs: Sequence[dict[str, Any]],
             metrics: dict[str, float] | None = None) -> Sequence[dict[str, Any]]:
        return SignatureRuleWriting(cells, page_index, rules, runs,
                                    metrics).for_cell("p0g0")

    base = claim([OWNER, CAPTION_CELL], [owned_rule()], [caption_run()])
    _check(list(base) == [owned_rule()],
           "a label cell's own vector rule at its own bottom wall, named by "
           "a signature caption directly below it, is claimed",
           f"{base}", failures)

    title_caption = claim([OWNER, CAPTION_CELL], [owned_rule()],
                          [caption_run("Title/Position of Signatory")])
    _check(list(title_caption) == [owned_rule()],
           "the identical rule under a signatory-detail caption (0605's own "
           "\"Title/Position of Signatory\" line) is claimed too -- the "
           "user's 2026-08-16 decision",
           f"{title_caption}", failures)

    prose = claim([OWNER, CAPTION_CELL], [owned_rule()],
                  [caption_run(
                      "provide the necessary details (e.g. title of "
                      "signatory and TIN)")])
    _check(not prose,
           "an instruction paragraph CONTAINING \"title of signatory\" "
           "(1604-E/F page 2's own text) is refused -- the detail test is "
           "a full match, never containment",
           f"{prose}", failures)

    both_cell = {**CAPTION_CELL,
                 "text_run_ids": [run_id(page_index, 0),
                                  run_id(page_index, 1)]}
    two_captions = claim([OWNER, both_cell], [owned_rule()],
                         [caption_run(),
                          caption_run("Title/Position of Signatory")])
    _check(not two_captions,
           "one rule named by BOTH a signature and a detail caption is "
           "still ambiguous: refused, not guessed at",
           f"{two_captions}", failures)

    underscore = claim([OWNER, CAPTION_CELL], [owned_rule(origin="text-underscore")],
                       [caption_run()])
    _check(not underscore,
           "an UNDERSCORE-drawn rule in the identical geometry is refused -- "
           "that population is RuledBlankWriting's, not this class's",
           f"{underscore}", failures)

    top_rule = claim([OWNER, CAPTION_CELL], [owned_rule(y0=-0.4, y1=0.4)],
                     [caption_run()])
    _check(not top_rule,
           "a rule straddling this cell's own TOP wall instead of its "
           "bottom -- 2550M's own header rule h100's shape -- is refused",
           f"{top_rule}", failures)

    far_caption = claim(
        [OWNER, {**CAPTION_CELL, "y0": 41.0}], [owned_rule()], [caption_run()])
    _check(not far_caption,
           "a caption cell that does not share this cell's own bottom wall "
           "(a 1.0pt gap, not the 0.0pt every real binding measures) is "
           "refused",
           f"{far_caption}", failures)

    ambiguous = claim(
        [OWNER, {**CAPTION_CELL,
                 "text_run_ids": [run_id(page_index, 0), run_id(page_index, 1)]}],
        [owned_rule()],
        [caption_run(), caption_run("Signature over Printed Name of Spouse",
                                    x0=60.0, x1=150.0)])
    _check(not ambiguous,
           "a rule two captions both name is refused rather than guessed "
           "at -- RuledBlankWriting's own precedent for ownership that does "
           "not resolve to exactly one claimant",
           f"{ambiguous}", failures)

    # F226: the sliver-gap extension, only reachable with a `metrics` line-fit
    # metric. `GAP_CELL` sits 3.0pt below the rule-owner's own bottom wall
    # (40.0), under the fixture's own 5.0pt `glyph_height_pt` -- 2316's own
    # 1.32pt and 0.54pt gaps, both under its own 4.65pt metric, at this
    # fixture's own scale.
    GAP_METRICS = {"glyph_height_pt": 5.0, "line_width_pt": 12.0}
    GAP_CELL = {"id": "p0g2", "kind": "label", "row": 1, "col": 0,
               "x0": 0.0, "y0": 43.0, "x1": 300.0, "y1": 60.0,
               "text_run_ids": [run_id(page_index, 0)]}
    gap_caption = caption_run(y0=44.0, y1=52.0)

    gap_clean = claim([OWNER, GAP_CELL], [owned_rule()], [gap_caption], GAP_METRICS)
    _check(list(gap_clean) == [owned_rule()],
           "a caption cell 3.0pt below the rule-owner's own bottom wall, "
           "under the form's own glyph_height_pt and carrying no ink in "
           "the gap, is bridged -- 2316's own item 53/54 shape",
           f"{gap_clean}", failures)

    # The identical 3.0pt gap, but a run genuinely printed IN it (any text,
    # not a signature caption -- p1c322's own "Date Signed" shape) refuses
    # the bridge even though the gap itself clears the height bound.
    gap_ink = _synthetic_run("Date Signed", 100.0, 41.0, 1.0,
                             ascender=0.5, descender=-0.5)  # line box 40.5..41.5
    gap_inked = claim([OWNER, GAP_CELL], [owned_rule()],
                      [gap_caption, gap_ink], GAP_METRICS)
    _check(not gap_inked,
           "the identical 3.0pt gap is refused once real ink is printed in "
           "it, even though the gap alone clears glyph_height_pt",
           f"{gap_inked}", failures)

    # A gap at or past glyph_height_pt is refused on its own, whatever ink
    # it does or does not carry -- p1c322's own h178/p1c327 shape (22.8pt,
    # more than 4x this form's own glyph_height_pt) in miniature.
    GAP_TOO_TALL = {**GAP_CELL, "id": "p0g3", "y0": 46.0}  # 6.0pt gap
    gap_too_tall = claim([OWNER, GAP_TOO_TALL], [owned_rule()],
                         [caption_run(y0=47.0, y1=55.0)], GAP_METRICS)
    _check(not gap_too_tall,
           "a 6.0pt gap -- past the fixture's own 5.0pt glyph_height_pt -- "
           "is refused even though it carries no ink",
           f"{gap_too_tall}", failures)

    # `metrics=None` (no line-fit metric at all -- an empty/synthetic IR)
    # falls back to the pre-F226 behaviour: even the smallest genuine gap is
    # refused, exactly `far_caption` above with no metric to bound it by.
    gap_no_metrics = claim([OWNER, GAP_CELL], [owned_rule()], [gap_caption])
    _check(not gap_no_metrics,
           "the identical 3.0pt gap is refused with no glyph_height_pt to "
           "measure it against at all",
           f"{gap_no_metrics}", failures)

    # The extension's own two corpus witnesses, exercised directly: the
    # clean gap is claimed AND counted as gap-bound; the inked and the too-
    # tall gaps are each counted as one real candidate refused.
    clean_stats = SignatureRuleWriting(
        [OWNER, GAP_CELL], page_index, [owned_rule()], [gap_caption], GAP_METRICS)
    _check(clean_stats.gap_bound_count() == 1 and clean_stats.gap_refused_count() == 0,
           "gap_bound_count/gap_refused_count read the clean gap correctly",
           f"bound={clean_stats.gap_bound_count()} "
           f"refused={clean_stats.gap_refused_count()}", failures)

    inked_stats = SignatureRuleWriting(
        [OWNER, GAP_CELL], page_index, [owned_rule()],
        [gap_caption, gap_ink], GAP_METRICS)
    _check(inked_stats.gap_bound_count() == 0 and inked_stats.gap_refused_count() == 1,
           "gap_bound_count/gap_refused_count read the inked gap correctly",
           f"bound={inked_stats.gap_bound_count()} "
           f"refused={inked_stats.gap_refused_count()}", failures)

    tall_stats = SignatureRuleWriting(
        [OWNER, GAP_TOO_TALL], page_index, [owned_rule()],
        [caption_run(y0=47.0, y1=55.0)], GAP_METRICS)
    _check(tall_stats.gap_bound_count() == 0 and tall_stats.gap_refused_count() == 1,
           "gap_bound_count/gap_refused_count read the too-tall gap "
           "correctly",
           f"bound={tall_stats.gap_bound_count()} "
           f"refused={tall_stats.gap_refused_count()}", failures)

    # End to end: the gap-bound claim becomes an input the same way the
    # exact-wall claim does -- `ruled_blank_field_box` is reused whole, no
    # second geometry function for this shape.
    gap_verdict = field_verdict(OWNER, None, None, None,
                                signature_rules=clean_stats)
    _check(gap_verdict == (True, "signature-rule"),
           "field_verdict routes a gap-bound claim through the signature-"
           "rule reason exactly like an exact-wall claim",
           f"{gap_verdict}", failures)

    # End to end: the claimed rule becomes an input, seated on its own
    # geometry, through the same dispatch `field_verdict` and `FieldPlan`
    # use on the real corpus -- and the Bureau/shading gate this population
    # has no real corpus case to exercise refuses it exactly like every
    # sibling mechanism's own does.
    signature_rules = SignatureRuleWriting(
        [OWNER, CAPTION_CELL], page_index, [owned_rule()], [caption_run()])
    verdict = field_verdict(OWNER, None, None, None,
                            signature_rules=signature_rules)
    _check(verdict == (True, "signature-rule"),
           "field_verdict routes a claimed cell through the signature-rule "
           "reason",
           f"{verdict}", failures)

    bureau = BureauReservation(
        [{"text": "Stamp of Receiving Office", "x0": 100.0, "y0": 2.0,
          "x1": 150.0, "y1": 10.0}], [], [])
    bureau_verdict = field_verdict(OWNER, None, None, bureau,
                                   signature_rules=signature_rules)
    _check(bureau_verdict == (False, "bureau"),
           "a Bureau caption over the identical claimed cell refuses it, "
           "not typed over by a taxpayer's own signature line",
           f"{bureau_verdict}", failures)

    # 0605-1999's and 1604cf-2008's own measured shape: the SAME oversized
    # `label` cell rules a taxpayer signature line AND prints an unrelated
    # "Stamp of Receiving Office" caption for a DIFFERENT, already-reserved
    # compartment 300pt further along the same cell. The guard is asked over
    # the CLAIMED RULE's own x-span, not the whole cell, so a caption outside
    # that span must not refuse a real claim.
    far_bureau = BureauReservation(
        [{"text": "Stamp of Receiving Office", "x0": 260.0, "y0": 2.0,
          "x1": 295.0, "y1": 10.0}], [], [])
    far_bureau_verdict = field_verdict(OWNER, None, None, far_bureau,
                                       signature_rules=signature_rules)
    _check(far_bureau_verdict == (True, "signature-rule"),
           "a Bureau caption printed elsewhere in the SAME oversized label "
           "cell, outside the claimed rule's own x-span, does not refuse a "
           "real signature-line claim -- 0605-1999 p1c176's and "
           "1604cf-2008 p1c316's own measured shape",
           f"{far_bureau_verdict}", failures)

    shading = DecorativeShading(
        [{"x0": 0.0, "y0": 0.0, "x1": 300.0, "y1": 40.0, "role": "decorative",
          "gray": 0.65, "paint_seq": 1}])
    shading_verdict = field_verdict(OWNER, None, shading, None,
                                    signature_rules=signature_rules)
    _check(shading_verdict == (False, "shading"),
           "a decorative tint under the identical claimed cell refuses it -- "
           "unlike RuledBlankWriting's own deliberate override, this "
           "population has no measurement excusing it from the shading gate",
           f"{shading_verdict}", failures)

    face = resolve_field_face(plan, [])
    if face is None:
        _check(False, "the font plan resolves a body face to fit fields to",
               "no metric-compatible face", failures)
        return
    box = ruled_blank_field_box(OWNER, signature_rules.for_cell("p0g0"), face, None)
    _check(box is not None and box.inset_trbl is not None
           and abs(box.inset_trbl[2] - (40.0 - 39.6)) < 1e-6,
           "the field box is seated exactly on the claimed rule's own top "
           "edge, not floating at the cell's own centre",
           f"{box.inset_trbl if box else None}", failures)


def comb_writing_rectangle_assertions(plan: dict[str, Any],
                                      failures: list[str]) -> None:
    """F186: every rectangle the emitter LAYS OUT for a comb is the writing box.

    One comb carries two vertical extents and they mean different things --
    `comb_writing_rect`'s docstring says which is which. This proves the split
    holds in both directions at once, because getting either half wrong has
    already shipped:

      * Read the divider band for layout and 2550M's item-4 TIN compartments
        are 3.12pt tall inside a 15.60pt row, fitted at a 2.81pt face. That is
        r22's state and this finding.
      * Restate the writing box INTO `y0`/`y1` and the comb referee's
        `classify_band` seeds its source topology from a rectangle the source
        never drew: 4,417 of 4,522 combs went source-unevaluable and the
        reviewed 2551Q control failed (G18).

    So the pin is not "the numbers are 14.64" -- it is that the four emitted
    rectangles all equal the WRITING rectangle, that none of them equals the
    band, and that the subject the emitter was handed still carries the band
    unmodified for the referee to read after emission.

    The geometry is 2550M's item-4 TIN row, transcribed: a 15.60pt row whose
    digit separators are 3.12pt stubs along its bottom edge.
    """
    face = resolve_field_face(plan, [])
    if face is None:
        _check(False, "the font plan resolves a body face to fit comb slots to",
               "no metric-compatible face", failures)
        return
    row_y0, row_y1 = 100.0, 115.6
    band_y0, band_h = 112.48, 3.12
    write_y0, write_h = 100.48, 14.64
    comb = _synthetic_comb(4, 0.0, 30.0, band_y0, band_h, (write_y0, write_h))
    cell = _synthetic_cell("p0c0", row_y0, row_y1, comb)
    layout = {"pages": [{"index": 0, "cells": [cell]}]}
    fields = FieldPlan(layout, face, [])
    box = fields.of("p0c0")

    _check(comb_writing_rect(cell, comb) == (write_y0, write_h),
           "a comb's layout rectangle is its writing box, not its divider band",
           f"writing ({fmt(write_y0)}, {fmt(write_h)}) chosen over band "
           f"({fmt(band_y0)}, {fmt(band_h)})", failures)

    # The slot div, and therefore its input: `.s` is absolutely positioned and
    # `.fi` is `inset:0` inside it, so the rectangle asserted here is the
    # rectangle a taxpayer's character is typed into.
    markup = comb_slots_markup(cell, comb, box, fields, True)
    rects = _SELF_TEST_STYLE_RE.findall(markup)
    tops = {value[0] for value in rects}
    heights = {value[2] for value in rects}
    _check(len(rects) == 4 and tops == {fmt(write_y0 - row_y0)}
           and heights == {fmt(write_h)}
           and markup.count("<input") == 4,
           "every comb slot div, and the input inside it, is the writing box",
           f"4 slots at top {sorted(tops)} height {sorted(heights)}; the band "
           f"would give top {fmt(band_y0 - row_y0)} height {fmt(band_h)}",
           failures)

    # The band template's clones are re-laid out from this JSON, so a clone
    # whose compartments disagree with the pre-rendered rows beside it is the
    # same defect one indirection away.
    payload = cell_json(cell, fields)["comb"]
    _check(payload["y"] == round(write_y0 - row_y0, 4)
           and payload["h"] == round(write_h, 4),
           "the band-data JSON a cloned row is re-laid out from says the same",
           f"y={payload['y']} h={payload['h']}", failures)

    # The fitted face. `min` still caps it at the sheet's own body size, so the
    # assertion is that the BOX grew, which is what the cap is applied to.
    band_only = field_box(_synthetic_cell(
        "p0c1", row_y0, row_y1,
        _synthetic_comb(4, 0.0, 30.0, band_y0, band_h)), face)
    _check(box is not None and band_only is not None
           and box.line_height_pt == round(write_h, 4)
           and band_only.line_height_pt == round(band_h, 4)
           and box.size_pt > band_only.size_pt,
           "the face is fitted to the writing box, so a 3.12pt band is not a field",
           f"{fmt(box.size_pt) if box else 'none'}pt in {fmt(write_h)}pt against "
           f"{fmt(band_only.size_pt) if band_only else 'none'}pt in {fmt(band_h)}pt",
           failures)

    # The other direction. The subject the emitter was handed is the referee's
    # input too, and emission must leave its contract untouched.
    _check(comb["y0"] == band_y0 and comb["height_pt"] == band_h
           and comb["y1"] == band_y0 + band_h
           and fmt(band_y0 - row_y0) not in tops
           and fmt(band_h) not in heights,
           "the divider band survives emission unmodified and is laid out on by nothing",
           "y0/y1/height_pt are the referee's contract and stay the source's",
           failures)

    # A layout that predates the writing rectangle still emits, on the band.
    legacy_comb = _synthetic_comb(4, 0.0, 30.0, band_y0, band_h)
    legacy_cell = _synthetic_cell("p0c0", row_y0, row_y1, legacy_comb)
    legacy_fields = FieldPlan({"pages": [{"index": 0, "cells": [legacy_cell]}]},
                              face, [])
    legacy = _SELF_TEST_STYLE_RE.findall(comb_slots_markup(
        legacy_cell, legacy_comb, legacy_fields.of("p0c0"), legacy_fields, True))
    _check(comb_writing_rect(legacy_cell, legacy_comb) == (band_y0, band_h)
           and {value[0] for value in legacy} == {fmt(band_y0 - row_y0)}
           and {value[2] for value in legacy} == {fmt(band_h)},
           "a subject with no writing rectangle falls back to its band, and says so",
           "the fallback is for a layout that predates it, not a preference",
           failures)

    # A comb's horizontal extent is its own `slot_x`, and that is no longer the
    # cell's: `lattice.comb_rails` measures the printed rails, and a rectangle
    # that also rules a caption or a TIN dash box hands the comb only the part
    # it owns. The emitter lays out on those rails and never widens them back
    # to the cell, because widening them is exactly how a typeable box lands
    # over the caption the sheet printed.
    railed_comb = {**_synthetic_comb(3, 0.0, 30.0, band_y0, band_h,
                                     (write_y0, write_h)),
                   "slot_x": [29.9, 59.9, 89.9, 119.9]}
    railed_cell = _synthetic_cell("p0c0", row_y0, row_y1, railed_comb)
    railed_fields = FieldPlan(
        {"pages": [{"index": 0, "cells": [railed_cell]}]}, face, [])
    railed_markup = comb_slots_markup(
        railed_cell, railed_comb, railed_fields.of("p0c0"),
        railed_fields, True)
    railed_lefts = re.findall(r"left:(-?[\d.]+)pt", railed_markup)
    railed_widths = [value[1] for value in
                     _SELF_TEST_STYLE_RE.findall(railed_markup)]
    _check(railed_lefts == [fmt(29.9), fmt(59.9), fmt(89.9)]
           and railed_widths == [fmt(30.0), fmt(30.0), fmt(30.0)]
           and railed_markup.count("<input") == 3,
           "a comb is laid out on its measured rails, not on its cell's edges",
           f"lefts {railed_lefts} widths {railed_widths} in a cell 0..120",
           failures)
    _check(cell_json(railed_cell, railed_fields)["comb"]["slot_x"]
           == [29.9, 59.9, 89.9, 119.9],
           "the band template re-lays cloned rows from the same rails",
           "the runtime must not rebuild a clone on the cell's edges",
           failures)

    # ...and a rail is a painted STROKE, so the outer compartments are laid on
    # its ink edges and not down the middle of it. F208: `slot_x` runs rail
    # centre to rail centre, so slot 0 used to start half a wall inside the
    # printed wall -- on 2551M, 0.36pt under the `C` of the item code beside it.
    # Only the two outer edges move; every internal divider stays exactly where
    # it was measured, because both compartments either side of it are drawn
    # against that one stroke's centre.
    written_comb = {**railed_comb, "writing_x0": 30.26, "writing_x1": 119.54}
    written_cell = _synthetic_cell("p0c0", row_y0, row_y1, written_comb)
    written_fields = FieldPlan(
        {"pages": [{"index": 0, "cells": [written_cell]}]}, face, [])
    written_markup = comb_slots_markup(
        written_cell, written_comb, written_fields.of("p0c0"),
        written_fields, True)
    written_lefts = re.findall(r"left:(-?[\d.]+)pt", written_markup)
    written_widths = [value[1] for value in
                      _SELF_TEST_STYLE_RE.findall(written_markup)]
    _check(comb_slot_edges(written_comb) == [30.26, 59.9, 89.9, 119.54]
           and written_lefts == [fmt(30.26), fmt(59.9), fmt(89.9)]
           and written_widths == [fmt(29.64), fmt(30.0), fmt(29.64)]
           and written_comb["slot_x"] == [29.9, 59.9, 89.9, 119.9],
           "the outer compartments are laid on the rails' ink, not their centres",
           f"lefts {written_lefts} widths {written_widths} from writing edges "
           "30.26..119.54 on rails 29.9..119.9",
           failures)
    _check(cell_json(written_cell, written_fields)["comb"]["slot_x"]
           == [30.26, 59.9, 89.9, 119.54],
           "the band template re-lays cloned rows on the same writing edges",
           "a clone rebuilt on the rail centres would sit on the printed wall "
           "beside identical pre-rendered rows that do not",
           failures)
    # The seam. G11's 287 corpus refusals and the 145 statutory constants that
    # nearly shipped as live inputs depend on the verdicts being asked of the same
    # rectangle the input occupies: a constant printed in the writing box but
    # clear of the 3.12pt band is invisible to a band-scoped question.
    glyph_y0 = write_y0 + 2.0
    glyph_y1 = glyph_y0 + 8.0

    def _glyph(text: str, x0: float, x1: float) -> dict[str, Any]:
        return {
            "text": text, "font": "Arial", "family": "Arial", "size_pt": 8.0,
            "bold": False, "italic": False, "serif": False,
            "monospace": False, "superscript": False, "flags": 0, "color": 0,
            "x0": x0, "y0": glyph_y0, "x1": x1, "y1": glyph_y1,
            "baseline_y": glyph_y1, "origin_x": x0, "ascender": 0.905,
            "descender": -0.21, "line_height_pt": glyph_y1 - glyph_y0,
            "measured_advance_pt": x1 - x0,
            "char_origin_offsets_pt": [0.0],
            "char_advances_pt": [x1 - x0], "char_widths_pt": [x1 - x0],
            "direction": [1.0, 0.0], "rotated": False, "unmapped_glyphs": [],
        }

    constant = _glyph("0", 8.0, 22.0)
    _check(comb_slot_verdicts(cell, PrePrintedInk([constant]), None, None)
           == {0: "pre-printed"}
           and comb_slot_verdicts(legacy_cell, PrePrintedInk([constant]),
                                  None, None) == {},
           "the pre-printed verdict is asked of the rectangle the input occupies",
           f"a constant at y {fmt(glyph_y0)}..{fmt(glyph_y1)} is inside the writing "
           f"box and clear of the band; only the writing-box question sees it",
           failures)
    # The horizontal half of the same seam, and it runs the OTHER way. The
    # verdict rectangle may never be SMALLER than the box the input occupies or
    # the sheet's own ink under that box goes unasked about, so the verdicts
    # stay on the printed compartment -- rail centre to rail centre -- which
    # contains the inset writing box by construction. A constant the sheet tucks
    # under its own wall therefore still spends its compartment.
    tucked = _glyph("2", 30.0, 30.2)
    _check(comb_slot_verdicts(written_cell, PrePrintedInk([tucked]),
                              None, None) == {0: "pre-printed"},
           "a constant tucked under the printed wall still spends its slot",
           "the verdicts are asked of the printed compartment, never of the "
           "inset writing box, so an input is never offered over ink",
           failures)


def field_surface_assertions(layout: dict[str, Any], plan: dict[str, Any],
                             failures: list[str]) -> None:
    """The fitted size is capped, and every writing-box fault is classified.

    Two things are proved here, and the second is why the first was worth
    proving. The size a field is fitted at is the smaller of what its box allows
    and what the sheet prints its own body text at, so a comb whose writing box
    grows to the full height of its row is still set at the modal body size --
    a taxpayer's TIN must not print larger than the caption beside it. And every
    field whose writing box is unusable lands in exactly one named population,
    so the report can be acted on: 243 undersized fields split 15 the source's
    and 228 ours is a work list, while "243 fields are small" is not.

    The populations are exercised on constructed cells rather than found ones.
    A corpus that happens to contain no uncontained comb today would silently
    stop testing the classifier that finds them, and the whole point of this
    layer is that a fault nobody can see is a fault that ships.
    """
    face = resolve_field_face(plan, [])
    if face is None:
        _check(False, "the font plan resolves a body face to fit fields to",
               "no metric-compatible face", failures)
        return

    # A comb band at the full height of a realistic 15.60pt row: the row allows
    # a far larger size than the sheet's own body text, and the cap is what
    # decides. Asserted against a plain field of identical height, because the
    # two must be capped by the same rule and once were not obviously so.
    row = 15.6
    allowed = _floor2(row / face.line_span_em)
    comb_box = field_box(_synthetic_cell("synthetic-comb", 0.0, row,
                                         _synthetic_comb(9, 0.0, 11.04, 0.0, row)), face)
    plain_box = field_box(_synthetic_cell("synthetic-field", 0.0, row), face)
    _check(comb_box is not None and plain_box is not None
           and comb_box.size_pt == face.size_pt
           and plain_box.size_pt == face.size_pt
           and allowed > face.size_pt,
           "a full-height comb is capped at the sheet's own body size",
           f"{fmt(row)}pt box allows {fmt(allowed)}pt, both kinds fit "
           f"{fmt(comb_box.size_pt) if comb_box else 'none'}pt against a body face of "
           f"{fmt(face.size_pt)}pt", failures)

    # One cell per population, in this order, and nothing may land in two.
    short = 2.0
    tall = 15.6
    stub = {"pages": [{"index": 0, "cells": [
        _synthetic_cell("p0c0", 0.0, short),
        _synthetic_cell("p0c1", 0.0, tall,
                        _synthetic_comb(9, 0.0, 11.04, tall - short, short)),
        _synthetic_cell("p0c2", 0.0, 10.0,
                        _synthetic_comb(9, 0.0, 11.04, -5.0, 20.0)),
        _synthetic_cell("p0c3", 0.0, tall,
                        _synthetic_comb(9, 0.0, 11.04, 0.0, tall)),
    ]}]}
    stub_warnings: list[str] = []
    stub_plan = FieldPlan(stub, face, stub_warnings)
    classified = {
        "undersized_source": stub_plan.undersized_source,
        "undersized_derived": stub_plan.undersized_derived,
        "uncontained": stub_plan.uncontained,
        "collapsed": stub_plan.collapsed,
    }
    _check(classified == {"undersized_source": ["p0c0"],
                          "undersized_derived": ["p0c1"],
                          "uncontained": ["p0c2"],
                          "collapsed": ["p0c1"]},
           "every writing-box fault lands in its own population",
           "; ".join(f"{name}={value}" for name, value in sorted(classified.items())),
           failures)
    _check(stub_plan.small == ["p0c0", "p0c1"]
           and len(stub_plan.undersized_source) + len(stub_plan.undersized_derived)
           == len(stub_plan.small),
           "the undersized populations partition the undersized fields",
           f"{len(stub_plan.small)} undersized = "
           f"{len(stub_plan.undersized_source)} the source's + "
           f"{len(stub_plan.undersized_derived)} ours", failures)
    # A count with no denominator cannot be read, and a fault with no count
    # cannot be tracked. Both were true of the report this replaced.
    reported = "\n".join(stub_warnings)
    _check(all(phrase in reported for phrase in
               ("1 of 4 field(s)", "1 of 3 comb writing box(es)",
                "lost in derivation", "the cell the source drew is",
                "not inside the cell that owns them", "cover under 50%")),
           "each population is reported with its own count and denominator",
           f"{len(stub_warnings)} warning(s)", failures)

    # The same four populations over the form actually under test. The first two
    # are invariants -- a legible box was available and something threw it away,
    # or a typing surface sits outside the box that clips it, and neither is ever
    # the source's doing. The third is reported, not asserted: how much of a cell
    # a comb band covers is lattice.py's measurement, and emit.py failing on a
    # threshold of its own would be the wrong file correcting the wrong producer.
    live = FieldPlan(layout, face, [])
    _check(not live.undersized_derived,
           "no field on this form fits under the minimum inside a cell that could "
           "have carried a legible size",
           f"{len(live.undersized_derived)} of {len(live.boxes)}: "
           f"{FieldPlan._sample(live.undersized_derived) or 'none'}", failures)
    _check(not live.uncontained,
           "every comb writing box is inside the cell that owns it",
           f"{len(live.uncontained)} of {live.comb_count}: "
           f"{FieldPlan._sample(live.uncontained) or 'none'}", failures)
    _check(True, "comb writing boxes covering under half their cell (reported)",
           f"{len(live.collapsed)} of {live.comb_count}", failures)


FIELD_DEBUG_PURE_BEGIN = "/* PURE-GEOMETRY-BEGIN"
FIELD_DEBUG_PURE_END = "/* PURE-GEOMETRY-END */"


def _field_debug_pure_geometry_source() -> str:
    """The exact, unmodified span of FIELD_DEBUG_JS the self-test executes.

    Sliced by the two marker comments the script itself carries, so this is
    never a re-implementation of the tone/box-finding logic: it is the
    shipped bytes, verbatim, between two exact anchors.
    """
    start = FIELD_DEBUG_JS.index(FIELD_DEBUG_PURE_BEGIN)
    end = FIELD_DEBUG_JS.index(FIELD_DEBUG_PURE_END) + len(FIELD_DEBUG_PURE_END)
    return FIELD_DEBUG_JS[start:end]


def _field_debug_constant_lines() -> str:
    """The exact `var NAME=...;` lines the pure block's free variables need,
    extracted from the shipped script rather than reconstructed from the
    Python constants, so a drift between the two would show up here too."""
    lines = []
    for name in ("TOL", "WALL", "MIN_BOX", "TINT_SPLIT_GRAY",
                 "STRUCTURAL_MAX_GRAY", "EPS", "PROBE",
                 "ROUNDS", "PAPER"):
        match = re.search(rf"var {name}=[^;]+;", FIELD_DEBUG_JS)
        if match is None:
            raise SystemExit(f"field_debug self-test: no `var {name}=...;` "
                             f"found in FIELD_DEBUG_JS")
        lines.append(match.group(0))
    return "\n".join(lines)


# The proven shape (F213, 1701-2018 p2 item 3 "Filer's Spouse Type"): a real
# black wall around a narrow field, and a short grey tint fragment crossing
# it -- 15.24pt wide on the source (h27, gray 0.8509), here 20pt, wider than
# the 11pt-wide field it crosses but a fraction of the field's own 51pt
# height. That is exactly the shape that let the fragment satisfy
# `wallsOf`'s 90% coverage test against the NARROW field while being, on the
# row it actually belongs to, the page-spanning band it really is. Probing at
# (5,35) -- inside the true box, below the tint line -- correct code must
# find the wall's real top (T=0); code with the split mutated to swallow the
# fragment into the wall bucket must find the tint's own y1 (T=25) instead:
# the fragment closing a false, too-short box, which is F213 itself,
# reproduced in miniature and mutated back into existence on purpose.
_FIELD_DEBUG_TONE_FIXTURE_RECTS = [
    {"n": 0, "x": -1, "y": 0, "x1": 0, "y1": 50, "fill": "rgb(0, 0, 0)"},
    {"n": 1, "x": 10, "y": 0, "x1": 11, "y1": 50, "fill": "rgb(0, 0, 0)"},
    {"n": 2, "x": -1, "y": -1, "x1": 11, "y1": 0, "fill": "rgb(0, 0, 0)"},
    {"n": 3, "x": -1, "y": 50, "x1": 11, "y1": 51, "fill": "rgb(0, 0, 0)"},
    {"n": 4, "x": -5, "y": 24.5, "x1": 15, "y1": 25, "fill": "rgb(217, 217, 217)"},
]


def _field_debug_tone_probe_script(split_override: float | None) -> str:
    """The extracted pure geometry, plus the synthetic case above, run under
    node.

    `split_override`, when given, is a scratch-copy MUTATION: the shipped
    `var TINT_SPLIT_GRAY=...;` line is replaced with a different value in a
    COPY of the constant lines before the script runs, never edited in place.
    That is what proves the split is load-bearing rather than merely
    present: a check that cannot be made to fail on a mutated input is not
    evidence (house style -- see this module's other FIELD_DEBUG assertions
    and extract.py's SELF_TEST_MUTATIONS for the same pattern).
    """
    constants = _field_debug_constant_lines()
    if split_override is not None:
        mutated_line = f"var TINT_SPLIT_GRAY={fmt(split_override)};"
        constants, count = re.subn(r"var TINT_SPLIT_GRAY=[^;]+;",
                                   mutated_line, constants)
        if count != 1 or mutated_line not in constants:
            raise SystemExit("field_debug self-test: mutation did not take")
    pure = _field_debug_pure_geometry_source()
    rects = json.dumps(_FIELD_DEBUG_TONE_FIXTURE_RECTS)
    return (
        f"{constants}\n{pure}\n"
        f"var rects={rects};\n"
        "var vis=visibleRects(rects);\n"
        "var wallVis=vis.filter(function(r){return !isTintTone(r.fill);});\n"
        "var box=boxAt(5,35,wallVis);\n"
        "process.stdout.write(JSON.stringify(box));\n"
    )


def _run_node(script: str) -> Any:
    node = shutil.which("node")
    if node is None:
        raise SystemExit("field_debug self-test: node is not on PATH, and a "
                         "check that cannot be evaluated is a failure")
    done = subprocess.run([node, "-e", script], capture_output=True, text=True)
    if done.returncode != 0:
        raise SystemExit(f"field_debug self-test: node failed: {done.stderr}")
    return json.loads(done.stdout)


def field_debug_tone_mutation_assertions(failures: list[str]) -> None:
    """Run the SHIPPED tone-classification bytes under node, twice.

    The real split must exclude 1701-2018 p2's proven tint fragment (h27's
    shape) from wall candidacy, recovering the field's true top wall. A
    scratch-copy mutation that raises the split just above the tint band's
    own grey value must reproduce the original defect on the identical
    synthetic input -- proving RULE_WALL_TINT_SPLIT_GRAY is load-bearing in
    the bytes that ship, not merely present in them.
    """
    real = _run_node(_field_debug_tone_probe_script(None))
    _check(real is not None and real.get("T") == 0,
           "the real tone split recovers the field's true wall past a tint "
           "fragment (the proven 1701-2018 p2 item-3 shape, synthetic)",
           f"{real}", failures)
    mutated = _run_node(_field_debug_tone_probe_script(0.9))
    _check(mutated is not None and mutated.get("T") == 25,
           "a scratch-copy mutation that swallows the tint band into the "
           "wall bucket reproduces the original defect on the identical "
           "input -- the check can fail, so it is evidence",
           f"{mutated}", failures)
    _check(real != mutated,
           "the split is load-bearing: the real and mutated verdicts differ "
           "on the same synthetic case",
           f"real={real} mutated={mutated}", failures)


# The proven shape (F210, 1701-2018 p2's Schedule-1 squares): a wide tint band
# painted FIRST, and a taller white knockout painted AFTER it that does NOT
# fit wholly inside the tint's extent (it sticks out top and bottom) -- the
# exact geometry that makes visibleRects() call the knockout invisible (no
# EARLIER rect wholly contains it, so it is compared to PAPER, and paper is
# white too) even though it is the real, topmost paint at the probe point.
# Found live on 1701-2018 p2 while proving this package's own acceptance
# criteria: the first cut of paintAt() read `vis` (visibleRects' output) and
# reported these real vacant boxes as tinted decoration, which would have
# broken F210's own "stays blue" requirement silently.
_FIELD_DEBUG_PAINT_FIXTURE_RECTS = [
    {"n": 0, "x": -10, "y": 24, "x1": 30, "y1": 36, "fill": "rgb(217, 217, 217)"},
    {"n": 1, "x": 0, "y": 20, "x1": 10, "y1": 40, "fill": "rgb(255, 255, 255)"},
]


def _field_debug_paint_probe_script(source: str) -> str:
    """paintAt(5, 30, <source>) over the F210-shaped fixture above, `source`
    being the literal JS expression naming which array to search."""
    constants = _field_debug_constant_lines()
    pure = _field_debug_pure_geometry_source()
    rects = json.dumps(_FIELD_DEBUG_PAINT_FIXTURE_RECTS)
    return (
        f"{constants}\n{pure}\n"
        f"var rawRects={rects};\n"
        "var vis=visibleRects(rawRects);\n"
        f"process.stdout.write(JSON.stringify(paintAt(5,30,{source})));\n"
    )


def field_debug_paint_source_assertions(failures: list[str]) -> None:
    """The vacant probe must read raw paint, not visibleRects' filtered view.

    Both calls below run the identical, real, shipped `paintAt()` -- no
    mutation is needed because the bug is in which ARGUMENT is passed, and
    both arguments are real. `rawRects` recovers the true topmost paint
    (white, F210's knockout); `vis` reproduces the defect this package found
    and fixed while proving its own acceptance criteria (grey, the tint
    underneath) because visibleRects() dropped a same-as-paper rect nothing
    wholly contains.
    """
    correct = _run_node(_field_debug_paint_probe_script("rawRects"))
    _check(correct == "rgb(255, 255, 255)",
           "paintAt() over the RAW paint stack finds the true topmost paint "
           "(the F210 knockout, white) past a same-as-paper rect "
           "visibleRects() would have dropped",
           f"{correct}", failures)
    wrong_argument = _run_node(_field_debug_paint_probe_script("vis"))
    _check(wrong_argument == "rgb(217, 217, 217)",
           "the SAME paintAt(), given visibleRects' filtered view instead, "
           "reproduces the defect on the identical point -- proving the "
           "choice of argument is load-bearing, not cosmetic",
           f"{wrong_argument}", failures)
    _check(correct != wrong_argument,
           "rawRects and vis disagree on the same probe point: the "
           "distinction this package's fix depends on is real",
           f"rawRects={correct} vis={wrong_argument}", failures)


def _field_debug_decor_probe_script() -> str:
    """Run the SHIPPED isTintTone/isDecorPaint on the three interior paints
    the vacant probe actually meets: a 0.651 shading pad (0619-E's
    centavo-separator compartments, rgb(166,166,166)), F210's knockout white,
    and a 0.8509 tint band (rgb(217,217,217))."""
    constants = _field_debug_constant_lines()
    pure = _field_debug_pure_geometry_source()
    return (
        f"{constants}\n{pure}\n"
        'var pad="rgb(166, 166, 166)";\n'
        'var knockout="rgb(255, 255, 255)";\n'
        'var tint="rgb(217, 217, 217)";\n'
        "process.stdout.write(JSON.stringify({"
        "padTint:isTintTone(pad),padDecor:isDecorPaint(pad),"
        "koDecor:isDecorPaint(knockout),tintDecor:isDecorPaint(tint)"
        "}));\n"
    )


def field_debug_interior_decor_assertions(failures: list[str]) -> None:
    """A 0.651 STROKE is a wall; a 0.651 FILL under a box centre is a pad.

    The stroke split (isTintTone, 0.70) and the interior test (isDecorPaint,
    the pipeline's structural cutoff 0.15) must therefore DISAGREE about
    0.651 -- that disagreement is the whole reason two functions exist. If a
    refactor ever collapses them, 0619-E's twelve centavo-separator pads come
    back as phantom vacant boxes, or F210's checkbox outlines stop bounding
    boxes; either way this trips first. Run on the shipped bytes under node,
    not described."""
    verdict = _run_node(_field_debug_decor_probe_script())
    parsed = json.loads(verdict) if isinstance(verdict, str) else verdict
    _check(parsed == {"padTint": False, "padDecor": True,
                      "koDecor": False, "tintDecor": True},
           "a 0.651 pad is below the stroke split yet inside the interior "
           "decoration band, knockout white is in neither, tint is in both",
           f"{parsed}", failures)


def field_debug_assertions(html: str, failures: list[str]) -> None:
    """The overlay cannot reach paper, and cannot move anything without asking.

    Four barriers, asserted one at a time, because "it is behind a flag" is the
    kind of claim that is true until a refactor and false silently afterwards.
    Beyond the barriers: the tone split (F213), proven by RUNNING the shipped
    bytes under node rather than by describing them, and the census callable
    (T3+T4) tab_check.py depends on.
    """
    styles = re.findall(r"<style>(.*?)</style>", html, flags=re.S)
    _check(len(styles) == 1, "the document still ships exactly one stylesheet",
           f"{len(styles)} <style> element(s)", failures)
    _check(all("data-fg-" not in sheet for sheet in styles),
           "no debug rule is in the emitted stylesheet",
           "the overlay's CSS exists only as a runtime string", failures)
    body = html.split('<script type="application/json"', 1)[0]
    _check("data-fg-" not in body,
           "no debug attribute is in the emitted markup",
           "every mark is set at runtime", failures)
    _check(html.count(FIELD_DEBUG_JS) == 1 and 'debug=fields' in html,
           "the overlay ships once, behind its query string",
           "?debug=fields", failures)

    # Barrier 1: nothing before the gate can change what the page renders.
    # window.formgenFieldCensus is a deliberate, documented exception (T3+T4,
    # F213's fix and tab_check.py's blue census): it is reachable WITHOUT the
    # token because tab_check.py calls it on forms nobody ever loads with
    # ?debug=fields, and everything it reaches -- census -> pageCensus ->
    # ruleRects/ptRect/the pure geometry -- only READS the document
    # (getBoundingClientRect, getComputedStyle, querySelectorAll), which
    # cannot itself change what renders. The invariant this barrier actually
    # protects is narrower than "no document access before the gate": it is
    # "no MUTATION and no LISTENER before the gate", checked by name so the
    # sanctioned exception is not banned along with everything else
    # "document." would have caught.
    guard = FIELD_DEBUG_JS.find("if(!requested()){return;}")
    before_gate = FIELD_DEBUG_JS[:guard] if guard > 0 else ""
    after_gate = FIELD_DEBUG_JS[guard:] if guard > 0 else FIELD_DEBUG_JS
    _check(guard > 0 and "window.formgenFieldCensus=census;" in before_gate,
           "the census callable is assigned before the gate, unconditionally",
           f"gate at {guard}", failures)
    mutators = ("createElement(", "appendChild(", "removeChild(",
               "setAttribute(", "removeAttribute(", "addEventListener(",
               "innerHTML", "outerHTML", "insertAdjacentHTML(",
               "insertAdjacentElement(", "insertAdjacentText(",
               "document.write(", "insertBefore(", "replaceChild(",
               "replaceChildren(")
    found_before = sorted(name for name in mutators if name in before_gate)
    _check(guard > 0 and not found_before,
           "nothing before the gate can mutate the document or register a "
           "listener",
           f"gate at {guard}, found before it: {found_before or 'none'}",
           failures)
    _check(all(name in after_gate for name in
               ("createElement(", "appendChild(", "setAttribute(",
                "addEventListener(")),
           "the overlay's own painting -- creation, attributes, listeners -- "
           "still happens, gated, after it",
           "present after the gate", failures)

    # Barrier 2/3: every injected rule is inside @media screen or @media print,
    # proved by walking the braces rather than by matching a substring.
    depth, cursor, outside = 0, 0, []
    for index, char in enumerate(FIELD_DEBUG_CSS):
        if char == "{":
            if depth == 0:
                outside.append(FIELD_DEBUG_CSS[cursor:index])
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                cursor = index + 1
    outside.append(FIELD_DEBUG_CSS[cursor:])
    _check(depth == 0 and [chunk.strip() for chunk in outside]
           == ["@media screen", "@media print", ""],
           "every debug rule is inside a media block",
           " | ".join(chunk.strip() for chunk in outside), failures)
    declarations = re.findall(r"([a-z-]+)\s*:([^;}]*)", FIELD_DEBUG_PRINT_CSS)
    _check(bool(declarations) and all(
               name in ("outline", "background", "display")
               and "!important" in value for name, value in declarations),
           "the print block only neutralises, with !important",
           "; ".join(f"{name}:{value.strip()}" for name, value in declarations),
           failures)

    # Barrier 4: the stylesheet AND every element the overlay made leave the
    # document for the duration of a print. The elements matter now in a way
    # they did not when the overlay only set attributes on inputs that were
    # already there: a mark is a real <i> painting a real outline, and a
    # stylesheet transform that dropped @media print would leave it on paper.
    _check('window.addEventListener("beforeprint",drop)' in FIELD_DEBUG_JS
           and 'window.addEventListener("afterprint",function(){inject();run();})'
           in FIELD_DEBUG_JS,
           "the overlay leaves the document for the print itself",
           "beforeprint drops it, afterprint rebuilds it", failures)
    _check('nodes=document.querySelectorAll("[data-fg-layer]");' in FIELD_DEBUG_JS
           and 'nodes=document.querySelectorAll("[data-fg-field]");' in FIELD_DEBUG_JS,
           "the print drop removes the marks, not only the stylesheet",
           "layers detached and field marks cleared", failures)

    # The overlay may never relax a threshold the pipeline holds elsewhere.
    for name, value in (("TOL", RULE_POSITION_TOLERANCE_PT),
                        ("WALL", RULE_WALL_COVERAGE),
                        ("MIN_BOX", FIELD_MIN_SIZE_PT),
                        ("TINT_SPLIT_GRAY", RULE_WALL_TINT_SPLIT_GRAY)):
        _check(f"var {name}={fmt(value)};" in FIELD_DEBUG_JS,
               f"the overlay's {name} is the module's own constant",
               f"{fmt(value)}", failures)

    # The point of the rewrite, asserted rather than described. The expectation
    # comes from the rule layer -- a different producer, fed by the PDF's own
    # painting operators -- and the field layer's own containers are not
    # readable from the script at all. An overlay that derives its expectation
    # from the thing it is checking is decoration, not a check, and the way
    # that failure comes back is by someone reaching for `.c` because it is
    # convenient.
    _check('querySelectorAll(".rl rect, .rl .r")' in FIELD_DEBUG_JS,
           "the overlay's expectation is read from the rule layer",
           "page_ir rules and area fills, not page_layout cells", failures)
    for selector in ('".layer-cells"', '".c"', '".s"', "'.c'", "closest("):
        _check(selector not in FIELD_DEBUG_JS,
               f"the overlay never reads the field layer's {selector} container",
               "no path from the subject to the expectation", failures)

    # F213: tint stops being a wall candidate, but still paints. The logic
    # itself is proven by EXECUTING it below (field_debug_tone_mutation_
    # assertions); this is only the wiring -- that the right array is passed
    # to the right function.
    _check("wallVis=vis.filter(function(r){return !isTintTone(r.fill);});"
           in FIELD_DEBUG_JS,
           "tint rects are filtered out of wall candidacy before allBoxes/"
           "boxAt ever see them",
           "wallVis excludes isTintTone", failures)
    _check("allBoxes(wallVis)" in FIELD_DEBUG_JS
           and "boxAt((inputs[i].x+inputs[i].x1)/2,(inputs[i].y+inputs[i].y1)"
               "/2,wallVis)" in FIELD_DEBUG_JS,
           "box-finding for both the printed-box census and the input-to-box "
           "match uses the tint-filtered set",
           "allBoxes(wallVis), boxAt(..., wallVis)", failures)
    _check("isDecorPaint(paintAt((box.L+box.R)/2,(box.T+box.B)/2,rawRects))"
           in FIELD_DEBUG_JS,
           "the vacant probe reads the RAW, unfiltered paint stack, not "
           "visibleRects' per-rect judgement (F210's own knockout squares "
           "are the proof this must NOT be `vis`: a same-as-paper knockout "
           "painted over a tint band is invisible to visibleRects but is "
           "still the real, topmost paint at that point)",
           "paintAt(..., rawRects), not vis or wallVis", failures)
    _check(FIELD_DEBUG_PURE_BEGIN in FIELD_DEBUG_JS
           and FIELD_DEBUG_PURE_END in FIELD_DEBUG_JS
           and (FIELD_DEBUG_JS.index(FIELD_DEBUG_PURE_BEGIN)
                < FIELD_DEBUG_JS.index(FIELD_DEBUG_PURE_END)),
           "the tone/box-finding functions are marked as the pure span the "
           "self-test extracts and executes",
           "PURE-GEOMETRY-BEGIN...END present and ordered", failures)
    field_debug_tone_mutation_assertions(failures)
    field_debug_paint_source_assertions(failures)
    field_debug_interior_decor_assertions(failures)

    # T4-addendum: one source of truth for the census, callable by
    # tab_check.py on a page that never carries ?debug=fields.
    _check("function census(){" in FIELD_DEBUG_JS
           and "window.formgenFieldCensus=census;" in FIELD_DEBUG_JS,
           "window.formgenFieldCensus is exposed and backed by the real "
           "census function",
           "census() assigned to window.formgenFieldCensus", failures)
    _check("results=census()" in FIELD_DEBUG_JS
           and "paintPage(page,result);" in FIELD_DEBUG_JS,
           "the visual overlay itself calls census() -- one computation, "
           "never two",
           "run() calls census(), then paints its result", failures)
    _check("return {id:el.id," in FIELD_DEBUG_JS,
           "ptRect carries an id string, not a DOM element reference, so "
           "census() results can cross Playwright's evaluate() boundary",
           "ptRect returns {id,...}", failures)
    _check("el=document.getElementById(result.inputs[i].id);" in FIELD_DEBUG_JS,
           "painting re-resolves elements by id from the census result, "
           "rather than the census itself carrying element references",
           "paintPage looks inputs up by id", failures)

    # Legend honesty (F213's counting-units fix and the two reviewer notes).
    _check("input(s) fill their printed box" in FIELD_DEBUG_JS
           and "printed box(es) with no input, after tone filtering"
               in FIELD_DEBUG_JS,
           "the legend states which UNIT each count is over (input vs box)",
           "LABELS text names its own unit", failures)
    _check("orange on a TIN comb is pre-printed constants" in FIELD_DEBUG_JS
           and "blue is a candidate missing input, after excluding"
               in FIELD_DEBUG_JS,
           "the legend explains the two marks reviewers ask about most: "
           "orange on TIN combs, and what blue means after tone filtering",
           "two explanatory legend lines present", failures)

    # The erosion moves INWARD, and this is a regression guard for a bug that
    # was written here: with the sign inverted the "eroded" box included its own
    # walls, and every field on both test forms reported as too small -- 1069
    # false positives, the exact failure mode this project keeps producing when
    # an instrument is trusted before it is looked at.
    _check("var eL=2*box.L-w.L.outer+TOL,eT=2*box.T-w.T.outer+TOL;" in FIELD_DEBUG_JS
           and "var eR=2*box.R-w.R.outer-TOL,eB=2*box.B-w.B.outer-TOL;" in FIELD_DEBUG_JS,
           "the shortfall test erodes the printed box inward",
           "left/top gain the wall, right/bottom lose it", failures)

    # `.page:last-of-type{break-after:auto}` is the one structural selector in
    # BASE_CSS. A <div> appended anywhere in the sheet takes that match off the
    # last page and adds a page break after it, which is a layout change made
    # by a debug tool -- exactly what the query-string gate exists to prevent.
    _check('createElement("div")' not in FIELD_DEBUG_JS,
           "the overlay creates no div, so .page:last-of-type still matches",
           "chrome is <aside> and <i>", failures)


TAB_DEBUG_PATH_BEGIN = "/* TAB-JSON-TARGET-BEGIN"
TAB_DEBUG_PATH_END = "/* TAB-JSON-TARGET-END */"


def _tab_debug_path_source() -> str:
    start = TAB_DEBUG_JS.index(TAB_DEBUG_PATH_BEGIN)
    end = TAB_DEBUG_JS.index(TAB_DEBUG_PATH_END) + len(TAB_DEBUG_PATH_END)
    return TAB_DEBUG_JS[start:end]


def _tab_debug_path_for(pathname: str) -> dict[str, str]:
    """Run the SHIPPED tabJsonTarget() under node against a synthetic
    location.pathname, proving the ../ vs ../../ split by execution rather
    than by describing it."""
    script = (
        "var window={location:{pathname:" + json.dumps(pathname) + "}};\n"
        + _tab_debug_path_source()
        + "\nprocess.stdout.write(JSON.stringify(tabJsonTarget()));\n"
    )
    return _run_node(script)


def tab_debug_assertions(html: str, failures: list[str]) -> None:
    """The tab-walk viewer (T3): same independence rule, same four barriers,
    a different token, proven the same way FIELD_DEBUG_JS is proven.
    """
    styles = re.findall(r"<style>(.*?)</style>", html, flags=re.S)
    _check(all("data-tabdbg-" not in sheet for sheet in styles),
           "no tab-debug rule is in the emitted stylesheet",
           "the viewer's CSS exists only as a runtime string", failures)
    body = html.split('<script type="application/json"', 1)[0]
    _check("data-tabdbg-" not in body,
           "no tab-debug attribute is in the emitted markup",
           "every mark is set at runtime", failures)
    _check(html.count(TAB_DEBUG_JS) == 1 and "debug=tab" in html,
           "the tab-walk viewer ships once, behind its own query string",
           "?debug=tab", failures)
    _check('var TOKEN="debug=fields";' in FIELD_DEBUG_JS
           and 'var TOKEN="debug=tab";' in TAB_DEBUG_JS,
           "the two overlays gate on their own distinct TOKEN, so neither "
           "can be triggered by the other's query string",
           'FIELD_DEBUG_JS TOKEN="debug=fields", TAB_DEBUG_JS TOKEN="debug=tab"',
           failures)

    guard = TAB_DEBUG_JS.find("if(!requested()){return;}")
    first_touch = min((index for index in
                       (TAB_DEBUG_JS.find("document."),
                        TAB_DEBUG_JS.find("addEventListener"))
                       if index >= 0), default=-1)
    _check(guard > 0 and first_touch > guard,
           "the query-string gate precedes any access to the document",
           f"gate at {guard}, first document access at {first_touch}", failures)

    depth, cursor, outside = 0, 0, []
    for index, char in enumerate(TAB_DEBUG_CSS):
        if char == "{":
            if depth == 0:
                outside.append(TAB_DEBUG_CSS[cursor:index])
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                cursor = index + 1
    outside.append(TAB_DEBUG_CSS[cursor:])
    _check(depth == 0 and [chunk.strip() for chunk in outside]
           == ["@media screen", "@media print", ""],
           "every tab-debug rule is inside a media block",
           " | ".join(chunk.strip() for chunk in outside), failures)
    declarations = re.findall(r"([a-z-]+)\s*:([^;}]*)", TAB_DEBUG_PRINT_CSS)
    _check(bool(declarations) and all("!important" in value
                                      for _name, value in declarations),
           "the print block only neutralises, with !important",
           "; ".join(f"{name}:{value.strip()}" for name, value in declarations),
           failures)
    _check('window.addEventListener("beforeprint",drop)' in TAB_DEBUG_JS
           and 'window.addEventListener("afterprint",start)' in TAB_DEBUG_JS,
           "the viewer leaves the document for the print itself",
           "beforeprint drops its marks, afterprint redraws them", failures)
    _check('createElement("div")' not in TAB_DEBUG_JS,
           "the viewer creates no div, so .page:last-of-type still matches",
           "chrome is <aside>, <i> and <b>", failures)

    # No path from the subject (the field layer) to the expectation (the
    # tab-walk artifact): the same independence rule FIELD_DEBUG_JS follows.
    _check("fetch(" in TAB_DEBUG_JS,
           "the viewer's expectation is fetched from tab_check.py's own "
           "artifact, a different producer entirely",
           "forms/review/<slug>/tab.json, not page_layout cells", failures)
    for selector in ('".layer-cells"', '".c"', '".s"', "'.c'", "closest("):
        _check(selector not in TAB_DEBUG_JS,
               f"the viewer never reads the field layer's {selector} container",
               "no path from the subject to the expectation", failures)

    # The slug/relative-path logic, run for real under node against both
    # shapes forms/ actually contains.
    plain = _tab_debug_path_for("/1701-2018/index.html")
    _check(plain == {"slug": "1701-2018", "path": "../review/1701-2018/tab.json"},
           "forms/<slug>/index.html resolves one level up to review/<slug>",
           f"{plain}", failures)
    plain_dir = _tab_debug_path_for("/1701-2018/")
    _check(plain_dir == plain,
           "a directory URL with no index.html resolves the same as the file",
           f"{plain_dir}", failures)
    extra = _tab_debug_path_for("/extra/1800-2018/index.html")
    _check(extra == {"slug": "1800-2018", "path": "../../review/1800-2018/tab.json"},
           "forms/extra/<slug>/index.html resolves two levels up",
           f"{extra}", failures)
    nested_extra = _tab_debug_path_for("/forms/extra/1800-2018/index.html")
    _check(nested_extra == extra,
           "the split depends on the path shape, not on what is mounted "
           "above forms/",
           f"{nested_extra}", failures)

    # The two actionable hints the spec asks for, by exact substring: a
    # file:// load and a fetch 404 must never present the same blank page.
    _check("fetch() cannot read tab.json over " in TAB_DEBUG_JS
           and "just review-serve" in TAB_DEBUG_JS,
           "file:// gets a hint naming the fix (no server, so fetch fails)",
           "hint text present", failures)
    _check("just tab-check " in TAB_DEBUG_JS,
           "a fetch that resolves but 404s gets a hint naming the fix "
           "(tab_check.py has not run for this slug)",
           "hint text present", failures)


def split_assertions(ir: dict[str, Any], layout: dict[str, Any], plan: dict[str, Any],
                     guide_plan: dict[str, Any], failures: list[str]) -> None:
    """The split must be free: same geometry, redistributed, nothing lost.

    Every assertion here compares the *markup* of the two halves against the
    markup of the undivided document, not just counts. A rect that moved by a
    hundredth of a point would still count, and counting is what this pipeline
    has to be unable to be fooled by.
    """
    for backend_name in sorted(BACKENDS):
        base_options = Options(backend_name, "fonts", "assets", None, None, None)
        whole, _ = build_document(ir, layout, plan, base_options)
        form_html, _ = build_document(ir, layout, plan, Options(
            backend_name, "fonts", "assets", None, None, None, guide_plan, "form"))
        guide_html, _ = build_document(ir, layout, plan, Options(
            backend_name, "fonts", "assets", None, None, None, guide_plan, "guide",
            "absolute"))

        whole_pages = _pages_of(whole)
        form_pages = _pages_of(form_html)
        guide_pages = _pages_of(guide_html)
        claimed = {int(e["page"]): e for e in guide_plan["inline"]}

        # Every page except one the guide took whole: that page has no geometry
        # left to preserve and printed as a blank sheet stapled into the form.
        relocated = {int(e["page"]) for e in guide_plan["inline"] if e.get("whole_page")}
        expected_pages = sorted(set(whole_pages) - relocated)
        _check(sorted(form_pages) == expected_pages,
               f"{backend_name} form keeps every page it did not wholly relocate",
               f"{sorted(form_pages)} == {expected_pages} "
               f"({len(relocated)} relocated whole)", failures)
        _check(sorted(guide_pages) == sorted(claimed),
               f"{backend_name} guide carries only pages with a guide region",
               f"{sorted(guide_pages)} == {sorted(claimed)}", failures)

        for index, body in whole_pages.items():
            entry = claimed.get(index)
            if index not in form_pages and index not in guide_pages:
                _check(bool(entry and entry.get("whole_page")),
                       f"{backend_name} p{index} is emitted by one document or claimed whole",
                       f"whole_page={bool(entry and entry.get('whole_page'))}", failures)
                continue
            band_rules = set(re.findall(r'id="band-rules-([^"]+)"', body))

            # A clipped straddler is on both sides, as two pieces neither of
            # which is the whole element, so it is counted out of the identity
            # comparison and checked on its own below. 2551Q has none, so the
            # numbers here are unchanged for the form --self-test runs on.
            clipped = [s for s in (entry or {}).get("straddlers", ())
                       if s.get("disposition") == "clipped"]
            extra_rects = sum(1 for s in clipped
                              if s["kind"] in ("rule", "area_fill", "image"))
            clipped_refs = {s["ref"] for s in clipped}
            for label, pattern in (("rect", RECT_RE), ("text run", RUN_RE)):
                allowance = extra_rects if label == "rect" else 0
                everything = pattern.findall(body)
                mine = pattern.findall(form_pages[index])
                theirs = pattern.findall(guide_pages.get(index, ""))
                _check(len(mine) + len(theirs) == len(everything) + allowance,
                       f"{backend_name} p{index} {label}s sum to the whole",
                       f"{len(mine)} form + {len(theirs)} guide == {len(everything)}"
                       f"{f' + {allowance} clipped' if allowance else ''}",
                       failures)
                # Order is preserved on both sides, so a positional comparison
                # is enough to prove nothing was re-laid-out.
                # Identity is asserted over the marks that carry an id, which is
                # every rule and every text run. An area fill and an image carry
                # no id, so a clipped one of those cannot be told apart from its
                # own whole; on a page that has one, those are held to the count
                # above and to the reconstruction guides.py proves.
                anonymous = any(s["kind"] in ("area_fill", "image") for s in clipped)
                def identified(items: Sequence[str]) -> list[str]:
                    return sorted(item for item in items
                                  if not any(f'"{ref}"' in item for ref in clipped_refs)
                                  and (not anonymous or "data-rule-id=" in item
                                       or label != "rect"))
                _check(identified(mine + theirs) == identified(everything),
                       f"{backend_name} p{index} {label}s are byte-identical after the split",
                       f"{len(everything)} compared, {len(clipped_refs)} clipped"
                       f"{', anonymous fills held to the count' if anonymous else ''}",
                       failures)

            if entry is None:
                _check(form_pages[index] == body,
                       f"{backend_name} p{index} is untouched (no guide region)",
                       f"{len(body)} bytes", failures)
                continue

            # Every rule the guide claims must be gone from the form and present
            # on the guide -- unless a band owns it, in which case the band, and
            # therefore the form, keeps it whole.
            form_rules = set(re.findall(r'data-rule-id="([^"]+)"', form_pages[index]))
            guide_rules = set(re.findall(r'data-rule-id="([^"]+)"',
                                         guide_pages.get(index, "")))
            all_rules = {r["id"] for r in ir["pages"][index - 1]["rules"]
                         if r["role"] == "structural"}
            _check(all_rules <= (form_rules | guide_rules),
                   f"{backend_name} p{index} no structural rule is lost by the split",
                   f"{len(all_rules - (form_rules | guide_rules))} lost", failures)
            shared = (form_rules & guide_rules) - clipped_refs
            _check(not shared,
                   f"{backend_name} p{index} no rule is emitted twice",
                   f"{len(shared)} shared beyond the {len(clipped_refs)} clipped",
                   failures)
            _check(set(entry["rule_ids"]) & form_rules == set(),
                   f"{backend_name} p{index} the form drops exactly the claimed rules",
                   f"{len(set(entry['rule_ids']) & form_rules)} claimed rules kept",
                   failures)

            # Straddlers are clipped, so both documents draw the piece on their
            # own side of the cut and neither may lose the element.
            straddling_rules = [s["ref"] for s in entry["straddlers"] if s["kind"] == "rule"]
            _check(all(ref in form_rules and ref in guide_rules
                       for ref in straddling_rules),
                   f"{backend_name} p{index} both documents draw every straddling rule",
                   f"{len(straddling_rules)} straddler(s)", failures)

            _check(not (set(entry["rule_ids"]) & band_rules),
                   f"{backend_name} p{index} no growable band overlaps the guide region",
                   f"{len(band_rules)} band container(s)", failures)

        # Cells are addressed by id, so they are compared as sets.
        for index, entry in claimed.items():
            form_cells = set(CELL_RE.findall(form_pages[index]))
            guide_cells = set(CELL_RE.findall(guide_pages.get(index, "")))
            whole_cells = set(CELL_RE.findall(whole_pages[index]))
            clipped_cells = {s["ref"] for s in entry.get("straddlers", ())
                             if s["kind"] == "cell" and s.get("disposition") == "clipped"}
            _check(form_cells | guide_cells == whole_cells
                   and not ((form_cells & guide_cells) - clipped_cells),
                   f"{backend_name} p{index} cells partition exactly",
                   f"{len(form_cells)} + {len(guide_cells)} == {len(whole_cells)}, "
                   f"{len(clipped_cells)} clipped", failures)

    # The reflowed guide is a different document, so it is checked for what it
    # promises: every claimed run present, exactly once, and no coordinates.
    reflow, _ = build_document(ir, layout, plan, Options(
        "svg", "fonts", "assets", None, None, None, guide_plan, "guide", "reflow"))
    # Nothing in the reflowed body carries a coordinate, which is why it cannot
    # overlap. (`.doc-link` is absolute on purpose and is not body content.)
    body = reflow.split("</head>", 1)[-1]
    _check('class="page' not in body and 'class="t"' not in body
           and "pt;top:" not in body,
           "reflowed guide places no content absolutely",
           f"{len(reflow)} bytes", failures)
    for entry in guide_plan["inline"]:
        page = ir["pages"][int(entry["page"]) - 1]
        text = re.sub(r"<[^>]+>", " ", reflow)
        # Compared against the source text, so undo esc_text: an ampersand in an
        # ATC row would otherwise read as a dropped run (2550M has five).
        for entity, literal in (("&lt;", "<"), ("&gt;", ">"), ("&amp;", "&")):
            text = text.replace(entity, literal)
        dense = "".join(text.split())
        missing = [index for index in entry["text_run_indices"]
                   if "".join(page["text_runs"][index]["text"].split()) not in dense]
        _check(not missing,
               f"reflowed guide p{entry['page']} carries every claimed run",
               f"{len(entry['text_run_indices'])} runs, {len(missing)} missing", failures)

    print_assertions(ir, layout, plan, guide_plan, reflow, failures)


def _dense_text(html: str) -> str:
    """The document's visible text, whitespace-free, with esc_text undone."""
    text = re.sub(r"<[^>]+>", " ", html)
    for entity, literal in (("&lt;", "<"), ("&gt;", ">"), ("&amp;", "&")):
        text = text.replace(entity, literal)
    return "".join(text.split())


def print_assertions(ir: dict[str, Any], layout: dict[str, Any], plan: dict[str, Any],
                     guide_plan: dict[str, Any], reflow: str,
                     failures: list[str]) -> None:
    """A guide is a document people print, so assert it is printable.

    Without an `@page` a guide prints at the browser's default paper, which is
    Letter wherever the user happens to be and is not the paper its form prints
    on. Without the print block the navigation links print as furniture and
    headings strand at the foot of a sheet. Both are invisible on screen, which
    is exactly why they need an assertion rather than an inspection.
    """
    paper = ir["paper"]
    want_page = (f'@page{{size:{fmt(paper["width_pt"])}pt {fmt(paper["height_pt"])}pt;'
                 f'margin:{fmt(GUIDE_PAGE_MARGIN_PT)}pt}}')
    _check(want_page in reflow, "reflowed guide sets @page from the form's paper",
           want_page, failures)
    _check(f'@page{{size:{fmt(paper["width_pt"])}pt {fmt(paper["height_pt"])}pt;'
           f'margin:0}}' not in reflow,
           "the guide does not inherit the form's zero-margin @page",
           f"{fmt(GUIDE_PAGE_MARGIN_PT)}pt margin", failures)
    _check("@media print{.doc-link{display:none}}" in reflow,
           "the guide hides its cross-document link in print",
           "DOC_LINK_CSS present", failures)
    for declaration in ("break-after:avoid", "orphans:2", "break-inside:avoid"):
        _check(declaration in reflow.split("@media print{", 1)[-1],
               f"the guide's print block declares {declaration}",
               "pagination rule present", failures)

    # A standalone guide PDF, exercised on this form's own IR. 2551Q ships no
    # separate guide, so the case is built rather than found: what is being
    # asserted is that a source IR reaches the document as reflowed text and
    # that the pinned PDF is still linked beside it, and any extraction proves
    # that as well as the real one would.
    name = guide_source_key(ir)
    _check(name.lower().endswith(".pdf"), "guide_source_key reads the source file name",
           name, failures)
    staged = dict(guide_plan, standalone_pdfs=[f"/somewhere/{name}"], standalone_pdf=name)
    converted, _ = build_document(ir, layout, plan, Options(
        "svg", "fonts", "assets", None, None, None, staged, "guide", "reflow",
        guide_sources={name: ir}))
    dense = _dense_text(converted)
    runs = [run["text"] for page in ir["pages"] for run in page["text_runs"]]
    missing = [text for text in runs if "".join(text.split()) not in dense]
    _check(not missing, "a converted guide PDF carries every run of its own extraction",
           f"{len(runs)} runs, {len(missing)} missing", failures)
    _check('data-converted="true"' in converted and "<object" not in converted,
           "a converted guide PDF is reflowed text, not an embedded viewer",
           f"{len(converted)} bytes", failures)

    # The colspan clamp, asserted at the function rather than only through the
    # whole-document run census above. A description whose last word crosses
    # into the next printed column overlaps two columns and would claim a
    # colspan of 2; the rate that starts in that second column owns a cell
    # there. Unclamped, the row walk steps from the first cell past the second
    # cell's index and that rate leaves the document -- silently, and with the
    # row still looking well formed. This is the narrowest statement of the
    # loss, and it is a loss of exactly the kind of token this table exists to
    # publish.
    # The ink band is stated, not omitted: every run in an extraction carries
    # one, `_group_lines` reads it to tell a set fragment from a line, and a
    # fixture that leaves it out would be asserting the clamp on a shape the
    # producer never sees. Both runs are one size, so neither can contain the
    # other and the grouping under test is untouched.
    def _cell_run(text: str, x0: float, x1: float) -> dict[str, Any]:
        return {"text": text, "x0": x0, "x1": x1, "origin_x": x0,
                "y0": 100.0 - 0.905 * 8.0, "y1": 100.0 + 0.21 * 8.0,
                "baseline_y": 100.0, "size_pt": 8.0}

    crossing = _table_markup(
        [_cell_run("description", 0.0, 150.0), _cell_run("2%", 110.0, 190.0)],
        [(0.0, 100.0), (100.0, 200.0), (200.0, 300.0)])
    _check("description" in crossing and "2%" in crossing
           and 'colspan="2"' not in crossing,
           "a run crossing into an occupied column does not swallow its cell",
           crossing, failures)

    # A run set inside another's ink is one printed line with it, whatever the
    # baseline window says (`_is_set_fragment`, finding F060). Both directions
    # are asserted with the corpus's own measurements, because a rule that only
    # ever joins is not a rule -- the second case is 2553 page 1, where a 9.33pt
    # label genuinely IS inside the 20.85pt title's box and is a different line.
    def _raised(text: str, x0: float, x1: float, y0: float, y1: float,
                baseline: float, size: float) -> dict[str, Any]:
        return {"text": text, "x0": x0, "x1": x1, "origin_x": x0,
                "y0": y0, "y1": y1, "baseline_y": baseline, "size_pt": size}

    # 1702Q p3: `...the fourth (4`, a raised `th) `, `taxable year (whether `.
    ordinal = [_raised("any domestic corporation and resident foreign "
                       "corporation beginning the fourth (4",
                       320.09, 526.59, 449.92, 457.89, 456.43, 6.96),
               _raised("taxable year (whether ",
                       532.42, 589.23, 449.92, 457.89, 456.43, 6.96),
               _raised("th) ", 526.90, 532.25, 450.12, 455.35, 454.39, 4.56)]
    grouped = _group_lines(ordinal)
    _check(len(grouped) == 1
           and [run["text"][-4:] for run in grouped[0]]
           == ["h (4", "th) ", "her "],
           "a superscript 2.04pt outside the baseline window joins its own line, "
           "in reading order",
           [[run["text"][-4:] for run in line] for line in grouped], failures)

    # 2553 p1: the title's box contains the label beside it, and they overlap by
    # 4.60pt rather than abutting. Two lines, exactly as the window says.
    titles = [_raised("Return of Percentage Tax",
                      235.92, 470.92, 51.69, 76.00, 71.52, 20.85),
              _raised("             BIR Form No.",
                      466.32, 555.11, 52.19, 63.24, 61.20, 9.33)]
    _check(len(_group_lines(titles)) == 2,
           "a shorter line merely sitting inside a tall title's box is not a "
           "fragment of it",
           [[run["text"] for run in line] for line in _group_lines(titles)],
           failures)

    # Two ordinary lines of ONE size, at the tightest real leading in any guide
    # region (5.16pt, 1600-VT), abutting where the columns meet. Their ink bands
    # overlap by 2.81pt, so an overlap test would fuse a column's last word to
    # the next line's first; containment cannot, because two runs of the same
    # size can never contain each other. That is the whole reason the test is
    # containment and not overlap.
    leaded = [_raised("Total amount ", 100.0, 200.0, 449.92, 457.89, 456.43, 6.96),
              _raised("due here", 200.0, 260.0, 455.08, 463.05, 461.59, 6.96)]
    _check(len(_group_lines(leaded)) == 2,
           "two same-size lines at the corpus's tightest leading stay two lines",
           [[run["text"] for run in line] for line in _group_lines(leaded)],
           failures)
    url = esc_attr(urllib.parse.quote(f"guides/{name}"))
    _check(f'<p class="gl-download">Source PDF: <a href="{url}">' in converted,
           "a converted guide PDF is still linked as the pinned artefact",
           url, failures)

    # The fallback has to stay honest: no extraction means the embed, and the
    # warning has to say that the embed does not print.
    embedded, warnings = build_document(ir, layout, plan, Options(
        "svg", "fonts", "assets", None, None, None, staged, "guide", "reflow"))
    _check('data-converted="false"' in embedded and "<object" in embedded,
           "a guide PDF with no extraction falls back to the embed",
           f"{len(embedded)} bytes", failures)
    _check(any("does not print" in warning for warning in warnings),
           "the fallback warns that an embedded PDF does not print",
           f"{len(warnings)} warning(s)", failures)

    # Wrong file, wrong form: both must fail rather than produce a document.
    for label, sources in (("a PDF the plan does not list", {"not-in-the-plan.pdf": ir}),
                           ("an extraction of another form",
                            {name: dict(ir, form={"code": "9999", "revision": "1999"})})):
        try:
            build_document(ir, layout, plan, Options(
                "svg", "fonts", "assets", None, None, None, staged, "guide", "reflow",
                guide_sources=sources))
        except SystemExit:
            _check(True, f"guide source is rejected: {label}", "SystemExit", failures)
        else:
            _check(False, f"guide source is rejected: {label}", "accepted", failures)


def self_test(ir_path: pathlib.Path, layout_path: pathlib.Path,
              plan_path: pathlib.Path,
              guide_plan_path: pathlib.Path | None = None) -> int:
    """Assert the emitted document carries the source's own inventory, exactly.

    Every assertion is a count or a set equality against the IR/layout, so it
    fails on omission as loudly as on duplication. A generator that silently
    drops a rule or paints one twice is the failure mode this pipeline exists
    to make impossible.
    """
    for path in (ir_path, layout_path, plan_path):
        if not path.is_file():
            print(f"self-test input missing: {path}", file=sys.stderr)
            return 2
    ir = json.loads(ir_path.read_text(encoding="utf-8"))
    layout = json.loads(layout_path.read_text(encoding="utf-8"))
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    failures: list[str] = []

    for backend_name in sorted(BACKENDS):
        print(f"backend {backend_name}", file=sys.stderr)
        options = Options(backend_name, "fonts", "assets", None, None, None)
        html, warnings = build_document(ir, layout, plan, options)

        pages = re.findall(r'<div class="page page-(\d+)"', html)
        _check(len(pages) == len(ir["pages"]), "page count",
               f"{len(pages)} == {len(ir['pages'])}", failures)

        for page in ir["pages"]:
            want = (f'style="width:{fmt(page["width_pt"])}pt;'
                    f'height:{fmt(page["height_pt"])}pt"')
            _check(want in html, f"page {page['index']} box",
                   f"{fmt(page['width_pt'])}x{fmt(page['height_pt'])}pt", failures)
        _check(f'@page{{size:{fmt(ir["paper"]["width_pt"])}pt '
               f'{fmt(ir["paper"]["height_pt"])}pt;margin:0}}' in html,
               "@page from MediaBox",
               f"{fmt(ir['paper']['width_pt'])}x{fmt(ir['paper']['height_pt'])}pt", failures)

        emitted_runs = re.findall(r'<div class="t" id="(p\d+t\d+)"', html)
        expected_runs = [run_id(p["index"], i)
                         for p in ir["pages"] for i in range(len(p["text_runs"]))]
        duplicates = sorted({r for r in emitted_runs if emitted_runs.count(r) > 1})
        _check(sorted(emitted_runs) == sorted(expected_runs) and not duplicates,
               "every text run exactly once",
               f"{len(emitted_runs)} emitted / {len(expected_runs)} in IR, "
               f"{len(duplicates)} duplicated", failures)

        painted = re.findall(r'data-rule-id="([^"]+)"', html)
        expected_rules = {f'{p["index"]}:{r["id"]}'
                          for p in ir["pages"] for r in p["rules"]
                          if r["role"] == "structural"}
        # Rule ids are unique per page only, so scope the comparison per page.
        per_page: list[str] = []
        for chunk in html.split('<div class="page page-')[1:]:
            number = re.match(r"(\d+)", chunk)
            for rid in re.findall(r'data-rule-id="([^"]+)"', chunk):
                per_page.append(f"{number.group(1)}:{rid}")
        missing = expected_rules - set(per_page)
        _check(not missing, "every structural rule in the rule layer",
               f"{len(expected_rules)} expected, {len(missing)} missing, "
               f"{len(painted)} rects carry an id", failures)

        # <template> content is inert: it is the blueprint for rows that do not
        # exist yet, so it must not be counted as rendered geometry.
        rendered = re.sub(r"<template\b.*?</template>", "", html, flags=re.S)
        slots = len(re.findall(r'data-slot="', rendered))
        expected_slots = sum(p["stats"]["comb_slots"] for p in layout["pages"])
        _check(slots == expected_slots, "comb slots outside the template",
               f"{slots} == {expected_slots}", failures)
        comb_cells = {c["id"]: c["comb"]["cells"]
                      for p in layout["pages"] for c in p["cells"] if c.get("comb")}
        bad = []
        for cid, count in sorted(comb_cells.items()):
            match = re.search(rf'(?<![-\w])id="{cid}"[^>]*data-comb-slots="(\d+)"', rendered)
            if match is None or int(match.group(1)) != count:
                bad.append(cid)
        _check(not bad, "comb slot counts match the layout",
               f"{len(comb_cells)} comb cells, {len(bad)} wrong", failures)

        field_assertions(ir, layout, plan, html, rendered, failures)

        bands = [(p["index"], g) for p in layout["pages"] for g in p["growable"]]
        for page_index, band in bands:
            template = re.search(
                rf'<template id="band-template-{band["id"]}"[^>]*'
                rf'data-capacity="(\d+)"[^>]*data-row-pitch="([^"]+)"', html)
            _check(template is not None and int(template.group(1)) == band["capacity"],
                   f"growable {band['id']} is a template",
                   f"capacity {template.group(1) if template else 'absent'} == "
                   f"{band['capacity']}", failures)
            _check(f'data-row-y="{",".join(fmt(v) for v in band["row_y"])}"' in html,
                   f"growable {band['id']} carries measured row_y",
                   f"{len(band['row_y'])} values", failures)
        _check(bool(bands), "at least one growable band", f"{len(bands)} found", failures)

        # The band must be laid out by indexing row_y, never by y0 + i*pitch.
        # 2551Q's last row is 18.27pt where the rest are 18.24, so the two
        # models disagree by 0.03pt at the closing rule -- small enough that
        # only an explicit assertion catches the wrong one.
        for page_ir, page_layout in zip(ir["pages"], layout["pages"]):
            cells_by_id = {c["id"]: c for c in page_layout["cells"]}
            for band in page_layout["growable"]:
                band_plan = build_band_plan(band, page_ir, cells_by_id)
                row_y = band_plan.row_y
                pitch = float(band["row_pitch_pt"])
                nominal = [row_y[0] + i * pitch for i in range(len(row_y))]
                drift = max(abs(a - b) for a, b in zip(row_y, nominal))
                rects = band_rects(band_plan, band_plan.capacity)
                _check({r.source_id for r in rects if r.source_id} == band_plan.rule_ids,
                       f"growable {band['id']} regenerates its own rules",
                       f"{len(rects)} rects, {len(band_plan.rule_ids)} source rules", failures)
                closing = [r for r in rects
                           if abs(r.y + r.h / 2.0 - row_y[-1]) <= BAND_EPSILON_PT]
                indexed = closing and all(
                    abs(r.y + r.h / 2.0 - row_y[-1]) < abs(r.y + r.h / 2.0 - nominal[-1])
                    for r in closing) if drift > 0 else bool(closing)
                _check(bool(indexed),
                       f"growable {band['id']} closes on row_y[-1], not y0+n*pitch",
                       f"{len(closing)} closing rules, pitch drift {fmt(drift)}pt", failures)
                _check(all(f"{fmt(r.y)}" in html for r in closing),
                       f"growable {band['id']} pre-render carries the measured close",
                       ", ".join(fmt(r.y) for r in closing) or "none", failures)

        for warning in warnings:
            print(f"  warn: {warning}", file=sys.stderr)

    with tempfile.TemporaryDirectory() as tmp:
        directory = pathlib.Path(tmp)
        for backend_name in sorted(BACKENDS):
            options = Options(backend_name, "fonts", "assets", None, None, None)
            first = directory / f"{backend_name}.1.html"
            second = directory / f"{backend_name}.2.html"
            first.write_text(build_document(ir, layout, plan, options)[0], encoding="utf-8")
            second.write_text(build_document(ir, layout, plan, options)[0], encoding="utf-8")
            _check(filecmp.cmp(first, second, shallow=False),
                   f"{backend_name} output is byte-identical across runs",
                   f"{first.stat().st_size} bytes", failures)

    if guide_plan_path is not None and guide_plan_path.is_file():
        print("form/guide split", file=sys.stderr)
        guide_plan = json.loads(guide_plan_path.read_text(encoding="utf-8"))
        # The split must cost nothing when there is nothing to split off.
        options = Options("svg", "fonts", "assets", None, None, None)
        plain, _ = build_document(ir, layout, plan, options)
        empty_plan = dict(guide_plan, inline=[], standalone_pdfs=[], standalone_pdf=None)
        with_empty, _ = build_document(ir, layout, plan, Options(
            "svg", "fonts", "assets", None, None, None, empty_plan, "form"))
        _check(plain == with_empty, "a guide plan that claims nothing changes nothing",
               f"{len(plain)} bytes", failures)
        split_assertions(ir, layout, plan, guide_plan, failures)
        for layout_name in ("absolute", "reflow"):
            variant = Options("svg", "fonts", "assets", None, None, None, guide_plan,
                              "guide", layout_name)
            first, _ = build_document(ir, layout, plan, variant)
            second, _ = build_document(ir, layout, plan, variant)
            _check(first == second,
                   f"{layout_name} guide output is byte-identical across runs",
                   f"{len(first)} chars", failures)
        print("constructed cases", file=sys.stderr)
        constructed_assertions(ir, layout, plan, guide_plan, failures)
    else:
        print(f"form/guide split: skipped, no guide plan at {guide_plan_path}",
              file=sys.stderr)
        print("constructed cases", file=sys.stderr)
        constructed_assertions(ir, layout, plan, None, failures)

    print("ruled-blank corpus check", file=sys.stderr)
    ruled_blank_corpus_assertions(failures)

    print("checkbox-square corpus check", file=sys.stderr)
    checkbox_square_corpus_assertions(failures)

    print("signature-box corpus check", file=sys.stderr)
    signature_box_corpus_assertions(failures)

    print("signature-line corpus check", file=sys.stderr)
    signature_line_corpus_assertions(failures)

    print("knockout-specify corpus check", file=sys.stderr)
    knockout_specify_corpus_assertions(failures)

    print("row-number corpus check", file=sys.stderr)
    row_number_corpus_assertions(failures)

    print("signature-rule corpus check", file=sys.stderr)
    signature_rule_corpus_assertions(failures)

    print(f"\n{'FAILED: ' + ', '.join(failures) if failures else 'all assertions passed'}",
          file=sys.stderr)
    return 1 if failures else 0


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--ir", type=pathlib.Path, default=DEFAULT_IR)
    parser.add_argument("--layout", type=pathlib.Path, default=DEFAULT_LAYOUT)
    parser.add_argument("--font-plan", type=pathlib.Path, default=DEFAULT_PLAN)
    parser.add_argument("--rule-backend", choices=sorted(BACKENDS), default="svg",
                        help="How rules are painted. Default svg (measured zero delta).")
    parser.add_argument("--fonts-dir", default="fonts",
                        help="Where the bundled WOFF2 live, relative to the HTML.")
    parser.add_argument("--assets-dir", default="assets",
                        help="Where the sha256-named images live, relative to the HTML.")
    parser.add_argument("--band-rows", type=int, default=None,
                        help="Rows to pre-render per growable band (default: capacity).")
    parser.add_argument("--guide-plan", type=pathlib.Path, default=None,
                        help="guides.py plan; splits the sheet into form and guide.")
    parser.add_argument("--document", choices=("form", "guide"), default="form",
                        help="Which half to emit. form keeps its full page boxes.")
    parser.add_argument("--guide-layout", choices=("absolute", "reflow"), default="reflow",
                        help="--document guide only: positioned runs, or reading order.")
    parser.add_argument("--guide-href", default="guide.html",
                        help="Where the form's link to the guide points.")
    parser.add_argument("--form-href", default="index.html",
                        help="Where the guide's link back to the form points.")
    parser.add_argument("--guide-pdf-dir", default="guides",
                        help="Where standalone guide PDFs live, relative to the HTML.")
    parser.add_argument("--guide-source", type=pathlib.Path, action="append", default=None,
                        metavar="IR", help="extract.py IR of a standalone guide PDF. Its "
                                           "text is reflowed into the guide document "
                                           "instead of the PDF being embedded, which is "
                                           "what makes it print. Repeatable.")
    parser.add_argument("--title", default=None)
    parser.add_argument("--out", type=pathlib.Path, default=None,
                        help="Write the HTML here (default: stdout).")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)

    if args.self_test:
        return self_test(args.ir, args.layout, args.font_plan, args.guide_plan
                         or DEFAULT_GUIDE_PLAN)

    for path in (args.ir, args.layout, args.font_plan):
        if not path.is_file():
            print(f"no such input: {path}", file=sys.stderr)
            return 2
    if args.guide_plan is not None and not args.guide_plan.is_file():
        print(f"no such guide plan: {args.guide_plan}", file=sys.stderr)
        return 2
    if args.document == "guide" and args.guide_plan is None:
        print("--document guide needs --guide-plan", file=sys.stderr)
        return 2

    for path in args.guide_source or ():
        if not path.is_file():
            print(f"no such guide source IR: {path}", file=sys.stderr)
            return 2

    ir = json.loads(args.ir.read_text(encoding="utf-8"))
    layout = json.loads(args.layout.read_text(encoding="utf-8"))
    plan = json.loads(args.font_plan.read_text(encoding="utf-8"))
    guide_plan = (json.loads(args.guide_plan.read_text(encoding="utf-8"))
                  if args.guide_plan else None)
    guide_sources: dict[str, dict[str, Any]] = {}
    for path in args.guide_source or ():
        source_ir = json.loads(path.read_text(encoding="utf-8"))
        guide_sources[guide_source_key(source_ir)] = source_ir

    out_dir = args.out.resolve().parent if args.out else None
    options = Options(args.rule_backend, args.fonts_dir, args.assets_dir,
                      out_dir, args.band_rows, args.title, guide_plan,
                      args.document, args.guide_layout, args.guide_href,
                      args.form_href, args.guide_pdf_dir, guide_sources)
    html, warnings = build_document(ir, layout, plan, options)

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(html, encoding="utf-8")
        print(f"wrote {args.out} ({len(html)} bytes, {args.document} document, "
              f"{args.rule_backend} rule backend)", file=sys.stderr)
    else:
        sys.stdout.write(html)

    for warning in warnings:
        print(f"warn: {warning}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
