#!/usr/bin/env python3
"""Decide which parts of a BIR sheet are reference material rather than form.

A BIR sheet carries two documents printed on the same paper. One is the form:
the boxes a taxpayer fills in, whose geometry is the whole point of this
pipeline. The other is reference material -- ATC code tables, statutory rate
tables, "Guidelines and Instructions", treaty-code lists -- which nobody fills
in and which the BIR does not require on the filed sheet.

Splitting them is worth doing twice over:

  * It frees space. The guide region occupies a mean 60% of the page it sits on,
    and that is exactly the room a growable band needs before it has to spill
    onto a continuation page.
  * It separates content whose parity does not matter from content whose parity
    is the entire objective. A mis-set line in an ATC table costs nothing; a
    mis-set line in a money comb is the bug we are here to prevent.

How the cut is found
--------------------

Three detectors *propose* a cut on a page. One admissibility test judges all
three, and the topmost surviving proposal wins, because a higher cut relocates
strictly more reference material and the test is what guarantees it relocates
nothing fillable.

  1. `marker` -- one of three STRICT lexical markers. Strictness is load-bearing:
     a loose /penalt/ pattern was tried and is wrong, because it matches
     "Add: Penalties", a *form* line on 1601C, 1603Q, 1600-PT and 2551Q.
  2. `reference-band` -- purely structural, no lexicon at all. Grown upward from
     the foot of the page one lattice row at a time for as long as each row is
     pre-printed table material. This is what finds the statutory tax-bracket
     tables on 1700/1701/1701A/1701Q/1701MS, the DST rate table on 2000-DST, the
     treaty-code lists on 1601-FQ and 1602Q, and the ATC table under Part VI of
     2552 -- none of which carries a usable marker. Keywords were refused here
     on purpose; the structural signal is the one that has never lied.
  3. `unfillable-page` -- a page with nothing fillable on it anywhere is
     reference material entire, and cuts at 0.0. Two shapes reach it: a page with
     no cells at all (2550M p4, 2553 p2, 2200P p3 -- full pages of guidelines,
     two of which head themselves "GUIDELINES AND INSTRUCTIONS" with no trailing
     "for BIR Form No. ...", so no marker fires), and a page whose every row the
     walk below cleared (0605 p2, 1702Q p3, 2550M p3 -- pages that are nothing
     but an ATC table).

The admissibility test (`cut_objections`)
-----------------------------------------

Over-detection is the dangerous failure: relocating a fillable box silently
removes it from the form, while missing a table only leaves reference material
on the sheet, where emit.py's own "no input over pre-printed text" rule still
protects the taxpayer. So the test is asymmetric on purpose, and every cut --
lexical or structural -- must clear all of it:

  * `admissible_cut_limit()` walks up from the foot of the page and refuses to
    pass a row that holds a comb, or a row whose entry box continues a column of
    entry boxes from the row above (the cut would sever a fillable column). No
    cut may go above where that walk stopped. This one test is what rejects the
    ATC column *headers* on the fillable faces of 1700, 1701, 1701Q and 1702EX,
    and it is what stops the 1600-PT band at its ATC title instead of eating the
    Schedule 1 total row one line higher.
  * every relocated empty box must be explained. An empty enclosed cell is
    either pre-printed table whitespace (some cell in its own lattice column
    carries pre-printed text: a blank ATC slot in a code table), or a frame
    gutter (tall, narrow, spanning three or more rows: the strip between the two
    tax tables on 1700), or it is a form control and the cut is refused. That is
    what keeps the two "8% in lieu of Graduated Rates" checkboxes on 1701A p2
    and the signature/accreditation boxes on 1604C/E/F p1 out of the guide.
  * no text run may straddle the cut: a run of glyphs cannot be rendered twice.

Counting was tried and refused. An allowance of "<= max(4, 5% of field cells)"
passes 5 relocated boxes out of 86 as easily as it passes 5 spurious slivers,
and on 1600-PT it accepted a cut through the Schedule 1 total row. Classify
every box and explain each one, or refuse the cut.

Membership and clipping
-----------------------

Membership is geometric: an element belongs to the guide if it lies wholly below
the cut. An element crossing the cut used to be awarded to the form outright, on
the grounds that a cut must never *lose* a rule. That has the opposite failure
and it is a blocker on 2000-OT: the relocated table's grey title band and its
full-height outer verticals stayed behind, drawing an empty three-sided box down
two thirds of the page with nothing inside it.

So straddlers are **clipped**, not awarded. The portion above the cut stays with
the form, the portion below goes to the guide, and both portions are emitted, so
each document gets a complete and self-consistent set. Nothing is lost and no
judgement about intent is needed. A text run is the one thing that cannot be
clipped, so a cut that splits one is inadmissible instead.

Standalone guides
-----------------

Some forms ship their guide as a separate PDF instead of, or as well as, an
inline region. batch.py deliberately skips those files -- converting an
instruction sheet into a fillable-looking bundle is worse than skipping it --
but they *are* the guide for their parent form, so this module enumerates them
and maps each back to a form code and revision using the same folder-name logic
batch.py's classify() applies. batch.py is not modified; the mapping is
exported from here.

Nothing here rasterises anything. Every decision is a coordinate or a string.

Usage:
    python3 tools/formgen/guides.py --ir build/ir/1603q-2018.ir.json \\
        --layout build/layout/1603q-2018.layout.json \\
        --out build/guides/1603q-2018.guide.json --summary
    python3 tools/formgen/guides.py --corpus
    python3 tools/formgen/guides.py --self-test
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import pathlib
import re
import sys
from typing import Any, Iterable, Sequence

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parent.parent

# Strict, and deliberately so. Each pattern was checked against the whole corpus
# for false positives on form content before it was admitted.
#   - the en dash in the "table" pattern is what BIR actually prints; the hyphen
#     and em dash are there because three sheets differ.
#   - "^\s*table \d+" is anchored: "see Table 2 below" is prose inside a form.
#   - the trailing "for" stays. Four sheets head their guidelines without it, and
#     dropping it would buy nothing while costing the guarantee that the pattern
#     cannot fire mid-form: 0605 p2, 2550M p4 and 2553 p2 are whole unfillable
#     pages and cut at 0.0, above where the heading sits, and 2551M p2 is already
#     cut 162pt higher by the alphanumeric-tax-code marker.
MARKERS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("guidelines-and-instructions", re.compile(r"guidelines?\s+and\s+instructions?\s+for", re.I)),
    ("table-n", re.compile(r"^\s*table\s+\d+\s*[-–—]", re.I)),
    ("alphanumeric-tax-code", re.compile(r"alphanumeric tax code", re.I)),
)

BAND_PATTERN = "reference-band"
UNFILLABLE_PATTERN = "unfillable-page"

# A marker hit is one string. Before a cut is taken on that evidence alone the
# region below it has to be prose: fewer runs than this and the "marker" is a
# stray heading with nothing under it.
MIN_GUIDE_TEXT_RUNS = 25

# The structural band arrives with far more evidence than a string -- every row
# in it has been shown to be pre-printed table material -- so it does not need
# the prose floor, which would reject the 22-run tax-bracket tables on 1700 and
# 1701Q. It needs enough of a table to be a table.
MIN_BAND_TEXT_RUNS = 12
MIN_BAND_ROWS = 3
MIN_BAND_CELLS = 4

# An unfillable page still has to have something on it to be worth moving, and
# the prose floor is the right bar for that.
MIN_UNFILLABLE_TEXT_RUNS = MIN_GUIDE_TEXT_RUNS

# Rounded cut values are compared against unrounded tops; 0.005 is half the last
# reported digit, so the comparison agrees with the report by construction.
EPS_PT = 0.005

# Lattice coordinates land on the same edge to well under a tenth of a point;
# 0.6 is loose enough to absorb that and far tighter than any real gap.
ALIGN_TOL_PT = 0.6

# Two cells are "loosely in one column" when they overlap by this much of the
# narrower one. Used only to decide whether a cut would sever a column of entry
# boxes, where being generous means stopping earlier, which is the safe side.
COLUMN_OVERLAP_FRACTION = 0.6

# A vacant cell this many lattice rows tall, taller than it is wide, is a frame
# gutter rather than a row's entry box. The lattice may represent one source
# gutter as either one tall cell or several vertically adjacent cells; the
# latter count together only when the source geometry has uninterrupted side
# rails and no horizontal stroke between them.
GUTTER_MIN_ROW_SPAN = 3

# Pre-printed text has to actually sit inside a box before it explains it away;
# a point of shared edge is not text in a cell.
TEXT_IN_CELL_MIN_PT = 1.0

# batch.py skips these; for this module they are the target, not the noise.
GUIDE_NAME_MARKERS = ("guide", "guidelines", "instruction", "annex")

# Owned here and imported by batch.py, which already depends on this module.
# It used to be duplicated in both files with a self-test asserting the copies
# agreed; the test did its job and caught the drift, but a constant that can
# drift at all is the defect. One definition cannot disagree with itself.
REVISION_OVERRIDES = {
    "0605": "1999",     # printed in the sheet body; matches the repo's existing pin
    # Neither folder ("2550M", "2551M") nor file name ("bir2550m.pdf",
    # "2551m.pdf") carries a year, so discovery fell back to "undated". Both
    # sheets do stamp the revision, in the masthead where every other BIR form
    # stamps it -- the year simply never reached classify(), which only reads
    # names. The PDF metadata is *not* the evidence for either (2550M's says
    # created 2007-04-15 by "Acrobat PDFWriter 5.0", 2551M's 2003-02-14 by
    # "Acrobat PDFWriter 4.05"): that dates the file, not the form.
    "2550M": "2007",    # p1 masthead "February 2007 (ENCS)" at (468.96, 69.39) pt,
                        # repeated in the p2/p3 running head:
                        # "BIR  Form 2550M  -  February 2007 (ENCS)      Page 2"
    "2551M": "2002",    # p1 masthead "April  2002  (ENCS)" at (476.4, 99.36) pt
}

# 2 adds the three-detector record (`detector`, `walk_limit_pt`, `vacancies`,
# `whole_page`, `rejected`) and replaces wholesale straddler ownership with
# per-side clipped geometry on every straddler.
SCHEMA_VERSION = 2
DEFAULT_SOURCE_ROOT = pathlib.Path("~/Downloads/forms").expanduser()


# --------------------------------------------------------------------------
# geometry helpers
# --------------------------------------------------------------------------

def box(item: dict[str, Any]) -> dict[str, float]:
    return {"x0": item["x0"], "y0": item["y0"], "x1": item["x1"], "y1": item["y1"]}


def loosely_one_column(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """Do these two boxes share a column, generously?"""
    overlap = min(a["x1"], b["x1"]) - max(a["x0"], b["x0"])
    narrower = min(a["x1"] - a["x0"], b["x1"] - b["x0"])
    return narrower > 0.0 and overlap >= COLUMN_OVERLAP_FRACTION * narrower


def exactly_one_column(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """Are these two boxes the same lattice column?

    Exact, not generous. A table column's cells share both x edges to well
    inside a tenth of a point; a wide declaration paragraph merely *contains*
    the signature box under it, and must not be allowed to explain it away.
    """
    return (abs(a["x0"] - b["x0"]) <= ALIGN_TOL_PT
            and abs(a["x1"] - b["x1"]) <= ALIGN_TOL_PT)


def contains(outer: dict[str, Any], inner: dict[str, Any]) -> bool:
    return (outer["x0"] <= inner["x0"] + ALIGN_TOL_PT
            and outer["x1"] >= inner["x1"] - ALIGN_TOL_PT
            and outer["y0"] <= inner["y0"] + ALIGN_TOL_PT
            and outer["y1"] >= inner["y1"] - ALIGN_TOL_PT)


def text_sits_in(cell: dict[str, Any], text_runs: Sequence[dict[str, Any]]) -> bool:
    """Does any pre-printed run land inside this cell's box?

    Cell membership in lattice.py gives a run to the single smallest cell
    containing its centre, so a run spanning a divider leaves the cell it
    overlaps looking empty. On 1700 p2 that is why four cells of the tax-bracket
    table report as fillable: ' Over P 250,000 but not over P 400,000  20%' is
    one run crossing the column rule. Overlap, not ownership, is the question
    being asked here.
    """
    for run in text_runs:
        if (min(cell["x1"], run["x1"]) - max(cell["x0"], run["x0"]) > TEXT_IN_CELL_MIN_PT
                and min(cell["y1"], run["y1"]) - max(cell["y0"], run["y0"]) > TEXT_IN_CELL_MIN_PT):
            return True
    return False


def comb_bands(cell: dict[str, Any]) -> list[dict[str, Any]]:
    """Every comb band in a cell. `combs` is present only when there is more
    than one, and `comb` is then the widest of them, so the union is what must be
    checked -- not either key alone."""
    bands = list(cell.get("combs") or ())
    if not bands and "comb" in cell:
        bands = [cell["comb"]]
    return bands


def has_border(cell: dict[str, Any], side: str) -> bool:
    """Whether source rule geometry supplies this side of a lattice cell."""
    return (cell.get("border") or {}).get(side) is not None


def is_gutter(cell: dict[str, Any],
              band: Sequence[dict[str, Any]] = ()) -> bool:
    """Is this narrow vacancy a source-visible frame gutter?

    A gutter normally arrives as one tall cell. A later lattice refinement can
    split that same source geometry at text-row y coordinates even though no
    horizontal rule exists there. Reconstruct only that provable shape: exact
    column alignment, visible rails on both sides, no text or comb, and an open
    seam between every adjacent fragment. Separately boxed entry cells retain
    their horizontal strokes and therefore never join this chain.

    `band` is deliberately the candidate guide region rather than the page.
    The walk is bidirectional inside that region because lattice refinement can
    make the candidate cell a middle fragment. Geometry above the proposed cut
    is absent from `band` and cannot help explain a vacancy below it.
    """
    height = cell["y1"] - cell["y0"]
    width = cell["x1"] - cell["x0"]
    if not band:
        return cell["row_span"] >= GUTTER_MIN_ROW_SPAN and height > width
    if (not has_border(cell, "left") or not has_border(cell, "right")
            or cell["text_run_ids"] or comb_bands(cell)):
        return False

    def is_fragment(other: dict[str, Any]) -> bool:
        return (exactly_one_column(cell, other)
                and has_border(other, "left")
                and has_border(other, "right")
                and not other.get("text_run_ids")
                and not comb_bands(other))

    fragments = {other["id"]: other for other in [*band, cell]
                 if is_fragment(other)}
    reached: dict[str, dict[str, Any]] = {}
    pending = [cell]
    while pending:
        current = pending.pop()
        if current["id"] in reached:
            continue
        reached[current["id"]] = current
        above = [other for other in fragments.values()
                 if other["id"] != current["id"]
                 and abs(other["y1"] - current["y0"]) <= ALIGN_TOL_PT
                 and not has_border(other, "bottom")
                 and not has_border(current, "top")]
        below = [other for other in fragments.values()
                 if other["id"] != current["id"]
                 and abs(other["y0"] - current["y1"]) <= ALIGN_TOL_PT
                 and not has_border(current, "bottom")
                 and not has_border(other, "top")]
        # A source gutter is one unbranched channel. More than one neighbour on
        # either edge is ambiguous even if the prefix already spans enough rows.
        if len(above) > 1 or len(below) > 1:
            return False
        pending.extend([*above, *below])

    top = min(part["y0"] for part in reached.values())
    bottom = max(part["y1"] for part in reached.values())
    row_span = sum(part["row_span"] for part in reached.values())
    return row_span >= GUTTER_MIN_ROW_SPAN and (bottom - top) > width


def is_control(cell: dict[str, Any], cells: Sequence[dict[str, Any]]) -> bool:
    """An empty box nested inside a cell that carries text is a tick box.

    1701A p2 prints "8% in lieu of Graduated Rates under Section 24(A) and 40%
    OSD" as one bordered line with two 13 x 11.5pt boxes inside it. They are the
    only fillable thing between the form and the tax table, they are what a
    taxpayer ticks, and no table-whitespace rule may be allowed to swallow them.
    """
    return any(other["id"] != cell["id"] and other["text_run_ids"]
               and contains(other, cell) for other in cells)


# --------------------------------------------------------------------------
# the box census: which empty cells are fillable
# --------------------------------------------------------------------------

@dataclasses.dataclass(frozen=True)
class Vacancy:
    """An empty enclosed cell, and what it turned out to be."""
    cell_id: str
    verdict: str             # whitespace | gutter | control | unexplained

    @property
    def fillable(self) -> bool:
        return self.verdict in ("control", "unexplained")


def empty_boxes(ir_page: dict[str, Any],
                layout_page: dict[str, Any]) -> list[dict[str, Any]]:
    """Cells that hold no pre-printed ink and are enclosed enough to write in."""
    runs = ir_page["text_runs"]
    return [c for c in layout_page["cells"]
            if c["kind"] == "field" and not text_sits_in(c, runs)]


def classify_vacancy(cell: dict[str, Any], band: Sequence[dict[str, Any]],
                     cells: Sequence[dict[str, Any]]) -> Vacancy:
    """Decide what one empty box inside a candidate guide region really is.

    `band` is every cell the cut would relocate; a column is judged from the
    band alone, never from the whole page. Judging page-wide would let a money
    column's own header ("Amount of Tax Withheld (6)") vouch for the money boxes
    beneath it, which is precisely backwards.
    """
    if is_gutter(cell, band):
        return Vacancy(cell["id"], "gutter")
    if is_control(cell, cells):
        return Vacancy(cell["id"], "control")
    for other in band:
        if other["id"] != cell["id"] and other["text_run_ids"] and exactly_one_column(other, cell):
            return Vacancy(cell["id"], "whitespace")
    return Vacancy(cell["id"], "unexplained")


# --------------------------------------------------------------------------
# how high a cut may go
# --------------------------------------------------------------------------

def row_cells(cells: Sequence[dict[str, Any]], y_lattice: Sequence[float],
              index: int) -> list[dict[str, Any]]:
    """Cells that start in lattice row `index`."""
    top, bottom = y_lattice[index], y_lattice[index + 1]
    return [c for c in cells if top - EPS_PT <= c["y0"] < bottom - EPS_PT]


def cells_ending_at(cells: Sequence[dict[str, Any]], y: float) -> list[dict[str, Any]]:
    return [c for c in cells if abs(c["y1"] - y) <= ALIGN_TOL_PT]


def severed_columns(line_y: float, cells: Sequence[dict[str, Any]],
                    row: Sequence[dict[str, Any]],
                    box_ids: frozenset[str]) -> list[tuple[str, str]]:
    """Entry boxes this line would cut off from the column they belong to.

    A pre-printed table can perfectly well have two blank cells stacked in one
    column -- 1601-FQ's ATC table leaves the WC column empty for two rows
    running -- so a stack of blanks alone proves nothing. What proves it is the
    *company* the lower blank keeps: a row with more pre-printed cells than
    empty ones is a table row, and a row where the empty boxes are the majority
    is somebody's entry row. Only the latter can sever a column.
    """
    boxes = [c for c in row if c["id"] in box_ids and not is_gutter(c)]
    printed = [c for c in row if c["text_run_ids"]]
    if len(printed) > len(boxes):
        return []
    above = [c for c in cells_ending_at(cells, line_y)
             if c["id"] in box_ids and not is_gutter(c)]
    return [(b["id"], a["id"]) for b in boxes for a in above if loosely_one_column(a, b)]


def admissible_cut_limit(ir_page: dict[str, Any],
                         layout_page: dict[str, Any]) -> tuple[float, str]:
    """The topmost y a cut may take on this page, and why it stops there.

    Walks up from the foot of the page one lattice row at a time. A comb is a
    fillable field by definition, and a severed entry column is a form table, so
    either one ends the walk. Reaching the top of the lattice means 0.0: no cell
    can begin above the first lattice line, so nothing fillable lives up there.
    """
    cells = layout_page["cells"]
    y_lattice = layout_page["y_lattice"]
    if not cells or len(y_lattice) < 2:
        return 0.0, "no cells on the page"
    box_ids = frozenset(c["id"] for c in empty_boxes(ir_page, layout_page))

    for index in range(len(y_lattice) - 2, -1, -1):
        line_y = y_lattice[index]
        row = row_cells(cells, y_lattice, index)
        combs = [c["id"] for c in row if "comb" in c]
        if combs:
            return line_y, f"comb {combs[0]} in the next row down"
        severed = severed_columns(line_y, cells, row, box_ids)
        if severed:
            return line_y, f"cut would sever fillable column {severed[0][1]} -> {severed[0][0]}"
    return 0.0, "no fillable row above the foot of the page"


# --------------------------------------------------------------------------
# candidates and the one test that judges them
# --------------------------------------------------------------------------

@dataclasses.dataclass(frozen=True)
class Candidate:
    """A proposed cut, before the admissibility test runs."""
    cut_y: float
    marker: str
    pattern: str
    detector: str
    min_text_runs: int


@dataclasses.dataclass(frozen=True)
class Straddler:
    """An element crossing the cut, clipped so both documents are complete.

    `form` and `guide` are the clipped geometry for each side. Both are present
    for everything except a text run, which cannot be split and is therefore
    never allowed to straddle an accepted cut.
    """
    kind: str                # rule | area_fill | image | text_run | cell
    ref: str                 # rule/cell id, or "#<index>" for the index-keyed lists
    x0: float
    y0: float
    x1: float
    y1: float
    detail: str              # why it is interesting: cell kind, fill tone, ...
    disposition: str         # clipped | form
    form: dict[str, Any] | None
    guide: dict[str, Any] | None

    def as_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "ref": self.ref, "x0": self.x0, "y0": self.y0,
                "x1": self.x1, "y1": self.y1, "detail": self.detail,
                "disposition": self.disposition, "form": self.form, "guide": self.guide}


def marker_hit(text: str) -> tuple[str, str] | None:
    """Return (matched substring, pattern name) for the first marker that fires."""
    for name, pattern in MARKERS:
        match = pattern.search(text)
        if match:
            return match.group(0), name
    return None


def marker_candidates(text_runs: Sequence[dict[str, Any]]) -> list[Candidate]:
    """Every lexical hit on the page, topmost first.

    Keyed on the run's top edge, not its baseline: the cut has to fall above the
    heading's own glyphs or the heading stays behind on the form.
    """
    found: dict[float, Candidate] = {}
    for run in text_runs:
        hit = marker_hit(run["text"])
        if hit is None:
            continue
        cut_y = run["y0"]
        # Two markers can fire on one line ("Table 1 - Alphanumeric Tax Code").
        # First wins, so the reported marker is stable under pattern reordering
        # only if MARKERS is; that ordering is fixed above.
        if cut_y not in found:
            found[cut_y] = Candidate(cut_y=cut_y, marker=run["text"].strip(),
                                     pattern=hit[1], detector="marker",
                                     min_text_runs=MIN_GUIDE_TEXT_RUNS)
    return [found[y] for y in sorted(found)]


def unfillable_page_candidate(ir_page: dict[str, Any], layout_page: dict[str, Any],
                              limit_y: float) -> Candidate | None:
    """A page with nothing fillable on it anywhere is reference material entire.

    Two shapes reach this. 2550M page 4 and 2553 page 2 are full pages of
    guidelines whose heading stops at "GUIDELINES AND INSTRUCTIONS", with no
    "for BIR Form No. ..." for the lexical marker to match; rather than loosen
    the marker -- which would then also fire mid-form -- this asks the structural
    question, and a page with no cells has nothing anyone could fill in. 0605
    page 2, 1702Q page 3 and 2550M page 3 are the other shape: pages that are
    nothing but an ATC table, where the row walk found no fillable row above the
    foot of the page. Both mean the same thing, so both cut at 0.0 and the page
    leaves whole, running head and outer frame included -- which is also how the
    form stops being left with a page of clipped frame slivers on it.

    The cut still has to clear cut_objections(): an isolated tick box or entry
    box anywhere on the page is enough to refuse it, because the row walk only
    ever stops for combs and for severed columns.
    """
    if limit_y > EPS_PT:
        return None
    if len(ir_page["text_runs"]) < MIN_UNFILLABLE_TEXT_RUNS:
        return None
    first = min(ir_page["text_runs"], key=lambda r: (r["y0"], r["x0"]))
    return Candidate(cut_y=0.0, marker=first["text"].strip(), pattern=UNFILLABLE_PATTERN,
                     detector=UNFILLABLE_PATTERN, min_text_runs=MIN_UNFILLABLE_TEXT_RUNS)


def band_candidate(ir_page: dict[str, Any], layout_page: dict[str, Any],
                   limit_y: float) -> Candidate | None:
    """The topmost lattice line that puts a whole pre-printed table below it.

    Grows the band upward from the foot of the page, keeping the highest line
    that still passes every objection. No keywords: a rate table is recognised
    by being a contiguous run of rows whose cells hold pre-printed text, hold no
    comb, and hold no unexplained empty box.
    """
    cells = layout_page["cells"]
    y_lattice = layout_page["y_lattice"]
    if not cells or len(y_lattice) < 2:
        return None

    best: Candidate | None = None
    rows = 0
    for index in range(len(y_lattice) - 2, -1, -1):
        line_y = y_lattice[index]
        if line_y < limit_y - EPS_PT:
            break
        rows += 1
        band = [c for c in cells if c["y0"] >= line_y - EPS_PT]
        if rows < MIN_BAND_ROWS or len(band) < MIN_BAND_CELLS:
            continue
        candidate = Candidate(cut_y=line_y, marker="", pattern=BAND_PATTERN,
                              detector=BAND_PATTERN, min_text_runs=MIN_BAND_TEXT_RUNS)
        if cut_objections(candidate, ir_page, layout_page, limit_y):
            continue
        best = candidate
    if best is None:
        return None
    below = [r for r in ir_page["text_runs"] if r["y0"] >= best.cut_y - EPS_PT]
    first = min(below, key=lambda r: (r["y0"], r["x0"]))
    return dataclasses.replace(best, marker=first["text"].strip())


def cut_objections(candidate: Candidate, ir_page: dict[str, Any],
                   layout_page: dict[str, Any], limit_y: float) -> list[str]:
    """Every reason this cut may not be taken. Empty means take it.

    One test for all three detectors. A lexical hit is one string of evidence
    and gets no more latitude than a structural band for it.
    """
    cut_y = candidate.cut_y
    problems: list[str] = []

    if cut_y < limit_y - EPS_PT:
        problems.append(f"cut {cut_y:.2f} is above the admissible limit {limit_y:.2f}")

    runs_below = sum(1 for r in ir_page["text_runs"] if r["y0"] >= cut_y - EPS_PT)
    if runs_below < candidate.min_text_runs:
        problems.append(f"only {runs_below} text run(s) below the cut, "
                        f"{candidate.min_text_runs} needed")

    split = [i for i, r in enumerate(ir_page["text_runs"]) if r["y0"] < cut_y < r["y1"]]
    if split:
        problems.append(f"text run #{split[0]} straddles the cut and cannot be clipped")

    # A comb *band* split down the middle is a mangled field; a comb *cell* whose
    # box merely crosses the cut is not. 2551M p2c0 is a one-bordered 267pt walk
    # artefact holding the whole schedule and a spurious 2-slot comb 14pt from
    # the page top: refusing the cut for that would leave 853pt of ATC table on
    # the form to protect a comb the cut never touches.
    for cell in layout_page["cells"]:
        for comb in comb_bands(cell):
            if comb["y0"] < cut_y < comb["y1"]:
                problems.append(f"comb band in cell {cell['id']} straddles the cut")
                break

    band = [c for c in layout_page["cells"] if c["y0"] >= cut_y - EPS_PT]
    band_ids = frozenset(c["id"] for c in band)
    for cell in empty_boxes(ir_page, layout_page):
        crosses = cell["y0"] < cut_y < cell["y1"]
        if cell["id"] not in band_ids and not crosses:
            continue
        verdict = classify_vacancy(cell, band, layout_page["cells"])
        if verdict.fillable:
            where = "split" if crosses else "relocate"
            problems.append(f"would {where} fillable box {cell['id']} ({verdict.verdict})")
    return problems


# --------------------------------------------------------------------------
# clipping
# --------------------------------------------------------------------------

def clip_rule(rule: dict[str, Any], cut_y: float) -> tuple[dict[str, Any], dict[str, Any]]:
    """Split a straddling rule at the cut, recomputing its length.

    `length_pt` is a derived field and the round-trip differ compares it. A
    clipped rule that keeps the original length is exactly the false mismatch
    this pipeline must not manufacture, so it is recomputed per side, on the
    rule's own axis.
    """
    def piece(y0: float, y1: float) -> dict[str, Any]:
        span = (rule["x1"] - rule["x0"]) if rule["axis"] == "h" else (y1 - y0)
        return {"x0": rule["x0"], "y0": round(y0, 2), "x1": rule["x1"], "y1": round(y1, 2),
                "thickness_pt": rule["thickness_pt"], "length_pt": round(span, 2)}
    return piece(rule["y0"], cut_y), piece(cut_y, rule["y1"])


def clip_area(item: dict[str, Any], cut_y: float) -> tuple[dict[str, Any], dict[str, Any]]:
    """Split a straddling fill or image box at the cut.

    An image is clipped, never resampled: the payload and `pixel_sha256` stay
    exactly as extracted and each side renders the same picture through a clip
    rectangle. Cropping the pixels would move the hash and break the artwork
    proof for a reason that has nothing to do with artwork.
    """
    above = dict(box(item)); above["y1"] = round(cut_y, 2)
    below = dict(box(item)); below["y0"] = round(cut_y, 2)
    return above, below


def clip_cell(cell: dict[str, Any], cut_y: float,
              run_top: dict[str, float]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Split a straddling cell, sending each of its runs to the side it sits on.

    The cut edge is not printed ink, so `border_at_cut` is False on both pieces:
    emit.py must not paint a border there or the split shows up as a rule the
    official does not have.

    `combs` names the comb bands each piece keeps. A cut may not pass through a
    band (cut_objections refuses that), so every band lands wholly on one side
    and the piece that has it is the piece that renders the field.
    """
    above_runs = [t for t in cell["text_run_ids"] if run_top.get(t, 0.0) < cut_y]
    below_runs = [t for t in cell["text_run_ids"] if run_top.get(t, 0.0) >= cut_y]
    bands = comb_bands(cell)
    above = dict(box(cell))
    above.update({"y1": round(cut_y, 2), "text_run_ids": above_runs,
                  "border_at_cut": False, "cut_edge": "bottom",
                  "combs": sum(1 for b in bands if b["y1"] <= cut_y + EPS_PT)})
    below = dict(box(cell))
    below.update({"y0": round(cut_y, 2), "text_run_ids": below_runs,
                  "border_at_cut": False, "cut_edge": "top",
                  "combs": sum(1 for b in bands if b["y0"] >= cut_y - EPS_PT)})
    return above, below


def straddling(items: Iterable[dict[str, Any]], cut_y: float) -> list[dict[str, Any]]:
    return [item for item in items if item["y0"] < cut_y < item["y1"]]


def collect_straddlers(cut_y: float, ir_page: dict[str, Any],
                       layout_page: dict[str, Any]) -> list[Straddler]:
    """Every element the cut passes through, with its clipped geometry."""
    out: list[Straddler] = []

    for rule in straddling(ir_page["rules"], cut_y):
        form, guide = clip_rule(rule, cut_y)
        out.append(Straddler("rule", rule["id"], rule["x0"], rule["y0"], rule["x1"], rule["y1"],
                             f"{rule['axis']} {rule['thickness_pt']}pt gray={rule['gray']}",
                             "clipped", form, guide))
    for index, fill in enumerate(ir_page["area_fills"]):
        if fill["y0"] < cut_y < fill["y1"]:
            form, guide = clip_area(fill, cut_y)
            out.append(Straddler("area_fill", f"#{index}", fill["x0"], fill["y0"],
                                 fill["x1"], fill["y1"], f"gray={fill['gray']}",
                                 "clipped", form, guide))
    for index, image in enumerate(ir_page["images"]):
        if image["y0"] < cut_y < image["y1"]:
            form, guide = clip_area(image, cut_y)
            out.append(Straddler("image", f"#{index}", image["x0"], image["y0"],
                                 image["x1"], image["y1"], image["sha256"][:12],
                                 "clipped", form, guide))
    # A run of glyphs cannot be split, so an accepted cut never crosses one --
    # cut_objections() rejects the cut instead. The branch stays because a
    # rejected candidate is still reported, and reporting it needs the shape.
    for index, run in enumerate(ir_page["text_runs"]):
        if run["y0"] < cut_y < run["y1"]:
            out.append(Straddler("text_run", f"#{index}", run["x0"], run["y0"],
                                 run["x1"], run["y1"], repr(run["text"][:40]),
                                 "form", box(run), None))

    run_top = {f"p{ir_page['index']}t{i}": r["y0"]
               for i, r in enumerate(ir_page["text_runs"])}
    for cell in straddling(layout_page["cells"], cut_y):
        form, guide = clip_cell(cell, cut_y, run_top)
        detail = f"kind={cell['kind']}"
        if guide["text_run_ids"]:
            detail += (f" {len(guide['text_run_ids'])}/{len(cell['text_run_ids'])} "
                       f"run(s) go with the guide")
        out.append(Straddler("cell", cell["id"], cell["x0"], cell["y0"],
                             cell["x1"], cell["y1"], detail, "clipped", form, guide))
    return out


# --------------------------------------------------------------------------
# detection
# --------------------------------------------------------------------------

def below(items: Iterable[dict[str, Any]], cut_y: float) -> list[dict[str, Any]]:
    """Elements lying wholly below the cut. Touching the cut counts as below."""
    return [item for item in items if item["y0"] >= cut_y]


def page_candidates(ir_page: dict[str, Any], layout_page: dict[str, Any],
                    limit_y: float) -> list[Candidate]:
    """Every proposal on this page, in a fixed order so ties resolve the same way."""
    proposals: list[Candidate] = []
    unfillable = unfillable_page_candidate(ir_page, layout_page, limit_y)
    if unfillable is not None:
        proposals.append(unfillable)
    proposals.extend(marker_candidates(ir_page["text_runs"]))
    band = band_candidate(ir_page, layout_page, limit_y)
    if band is not None:
        proposals.append(band)
    return proposals


def detect_page(ir_page: dict[str, Any],
                layout_page: dict[str, Any]) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """Find the guide region on one page. Returns (entry or None, rejections).

    The topmost admissible proposal wins: a higher cut relocates strictly more
    reference material, and cut_objections() is what makes "higher" safe.
    """
    text_runs = ir_page["text_runs"]
    limit_y, limit_reason = admissible_cut_limit(ir_page, layout_page)

    accepted: list[Candidate] = []
    rejected: list[dict[str, Any]] = []
    for candidate in page_candidates(ir_page, layout_page, limit_y):
        problems = cut_objections(candidate, ir_page, layout_page, limit_y)
        if problems:
            rejected.append({"page": ir_page["index"], "cut_y_pt": round(candidate.cut_y, 2),
                             "detector": candidate.detector, "marker": candidate.marker,
                             "objections": problems})
        else:
            accepted.append(candidate)
    if not accepted:
        return None, rejected

    chosen = min(accepted, key=lambda c: c.cut_y)
    cut_y = chosen.cut_y
    height = ir_page["height_pt"]
    reclaimed = height - cut_y

    guide_runs = [i for i, r in enumerate(text_runs) if r["y0"] >= cut_y]
    guide_cells = [c["id"] for c in layout_page["cells"] if c["y0"] >= cut_y]
    guide_rules = [r["id"] for r in ir_page["rules"] if r["y0"] >= cut_y]
    guide_fills = [i for i, f in enumerate(ir_page["area_fills"]) if f["y0"] >= cut_y]
    guide_images = [i for i, m in enumerate(ir_page["images"]) if m["y0"] >= cut_y]
    # Paths arrived with IR schema 2, after this module was written, so they were
    # silently never claimed. 0605 page 2 relocated every rule, run and fill and
    # still kept 532 stroked dashes, which left the page looking non-empty to
    # everything reading the plan while emit.py correctly printed nothing. Same
    # geometric rule as every other bucket: top edge at or below the cut.
    guide_paths = [i for i, p in enumerate(ir_page.get("paths") or ())
                   if p["y0"] >= cut_y]
    straddlers = collect_straddlers(cut_y, ir_page, layout_page)

    field_cells = [c for c in layout_page["cells"] if c["kind"] == "field"]
    band = [c for c in layout_page["cells"] if c["y0"] >= cut_y - EPS_PT]
    band_ids = frozenset(c["id"] for c in band)
    vacancies = [classify_vacancy(c, band, layout_page["cells"])
                 for c in empty_boxes(ir_page, layout_page) if c["id"] in band_ids]

    # Nothing of the page is left on the form: emit.py has an empty page, not a
    # page with a stub on it, and should drop it rather than print blank paper.
    whole_page = (len(guide_runs) == len(text_runs)
                  and len(guide_cells) == len(layout_page["cells"])
                  and not [s for s in straddlers if s.kind in ("rule", "area_fill", "image")])

    return {
        "page": ir_page["index"],
        "cut_y_pt": round(cut_y, 2),
        "reclaimed_pt": round(reclaimed, 2),
        "reclaimed_pct": round(reclaimed / height * 100),
        "text_run_indices": guide_runs,
        "cell_ids": guide_cells,
        "rule_ids": guide_rules,
        "area_fill_indices": guide_fills,
        "path_indices": guide_paths,
        "image_indices": guide_images,
        "detector": chosen.detector,
        "marker": chosen.marker,
        "marker_pattern": chosen.pattern,
        "whole_page": whole_page,
        "walk_limit_pt": round(limit_y, 2),
        "walk_limit_reason": limit_reason,
        "text_runs_below": len([r for r in text_runs if r["y0"] >= cut_y]),
        "field_cells_below": len(below(field_cells, cut_y)),
        "field_cells_on_page": len(field_cells),
        "vacancies": {
            "whitespace": sum(1 for v in vacancies if v.verdict == "whitespace"),
            "gutter": sum(1 for v in vacancies if v.verdict == "gutter"),
        },
        "straddlers": [s.as_dict() for s in straddlers],
    }, rejected


# --------------------------------------------------------------------------
# standalone guide PDFs
# --------------------------------------------------------------------------

def is_guide_name(pdf: pathlib.Path) -> bool:
    name = pdf.name.lower()
    return any(marker in name for marker in GUIDE_NAME_MARKERS)


def parent_form_identity(pdf: pathlib.Path) -> tuple[str, str] | None:
    """Map any source PDF to (code, revision) from its folder and file name.

    This is batch.classify()'s identity logic with the non-form skip removed --
    a guide's *folder* is its parent form's folder, so the same derivation names
    the form the guide belongs to. self_test() asserts the two stay in step.
    """
    match = re.match(r"^(?P<code>\d{4}[A-Za-z-]*?)(?:v(?P<rev>\d{4}[A-Za-z]?))?$", pdf.parent.name)
    if not match:
        return None
    code = match.group("code").upper().rstrip("-")
    revision = (match.group("rev") or "").upper()
    if not revision:
        revision = REVISION_OVERRIDES.get(code, "")
    if not revision:
        year = re.search(r"(?:19|20)\d{2}", pdf.name)
        revision = year.group(0) if year else "undated"
    return code, revision


def form_key(code: str, revision: str) -> str:
    return f"{code}-{revision}".upper()


def standalone_guide_pdfs(source_root: pathlib.Path = DEFAULT_SOURCE_ROOT
                          ) -> dict[str, list[pathlib.Path]]:
    """Every PDF batch.py skips as a non-form, keyed by its parent form.

    A folder can hold more than one (2200AN ships an Annex A alongside its
    guidelines), so the value is a sorted list, not a single path. Sorted by
    path throughout, because the caller pins on this and determinism is the
    property being protected.
    """
    mapping: dict[str, list[pathlib.Path]] = {}
    if not source_root.is_dir():
        return mapping
    for pdf in sorted(source_root.rglob("*.pdf")):
        if not is_guide_name(pdf):
            continue
        identity = parent_form_identity(pdf)
        if identity is None:
            continue
        mapping.setdefault(form_key(*identity), []).append(pdf.resolve())
    return {key: sorted(paths) for key, paths in sorted(mapping.items())}


# --------------------------------------------------------------------------
# plan
# --------------------------------------------------------------------------

def build_plan(ir: dict[str, Any], layout: dict[str, Any],
               standalone: dict[str, list[pathlib.Path]] | None = None) -> dict[str, Any]:
    if ir["form"] != layout["form"]:
        raise ValueError(f"IR is {ir['form']} but layout is {layout['form']}")
    if len(ir["pages"]) != len(layout["pages"]):
        raise ValueError("IR and layout disagree on page count")

    inline: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for ir_page, layout_page in zip(ir["pages"], layout["pages"]):
        entry, refused = detect_page(ir_page, layout_page)
        rejected.extend(refused)
        if entry is not None:
            inline.append(entry)

    key = form_key(ir["form"]["code"], ir["form"]["revision"])
    guides = (standalone or {}).get(key, [])
    pcts = [e["reclaimed_pct"] for e in inline]

    return {
        "schema_version": SCHEMA_VERSION,
        "form": dict(ir["form"]),
        "inline": inline,
        # Why a page was *not* cut, on every page where something was proposed.
        # The audit passed 137 real defects once; a detector that cannot explain
        # its refusals is how that happens.
        "rejected": rejected,
        "standalone_pdf": str(guides[0]) if guides else None,
        "standalone_pdfs": [str(p) for p in guides],
        "stats": {
            "pages": len(ir["pages"]),
            "pages_with_guide": len(inline),
            "whole_pages": sum(1 for e in inline if e["whole_page"]),
            "reclaimed_pct_mean": round(sum(pcts) / len(pcts)) if pcts else 0,
            "reclaimed_pct_min": min(pcts) if pcts else 0,
            "reclaimed_pct_max": max(pcts) if pcts else 0,
            "guide_text_runs": sum(len(e["text_run_indices"]) for e in inline),
            "guide_cells": sum(len(e["cell_ids"]) for e in inline),
            "guide_rules": sum(len(e["rule_ids"]) for e in inline),
            "guide_area_fills": sum(len(e["area_fill_indices"]) for e in inline),
            "guide_paths": sum(len(e.get("path_indices") or ()) for e in inline),
            "guide_images": sum(len(e["image_indices"]) for e in inline),
            "straddlers": sum(len(e["straddlers"]) for e in inline),
            "clipped": sum(1 for e in inline for s in e["straddlers"]
                           if s["disposition"] == "clipped"),
            "detectors": {name: sum(1 for e in inline if e["detector"] == name)
                          for name in ("marker", BAND_PATTERN, UNFILLABLE_PATTERN)},
        },
    }


def check_partition(plan: dict[str, Any], ir: dict[str, Any],
                    layout: dict[str, Any]) -> list[str]:
    """Prove the split is exhaustive and disjoint on every page it touches.

    emit.py will ask "does this element go in the form or the guide?" exactly
    once per element. If the answer is ever "both" or "neither", the sheet
    silently loses or duplicates geometry, so the invariant is checked here
    rather than trusted. A clipped straddler is the one element that is legally
    in both, and the proof for it is that its two pieces meet at the cut and
    span exactly the original: no ink is lost and none is drawn twice.
    """
    problems: list[str] = []
    by_page = {e["page"]: e for e in plan["inline"]}
    for ir_page, layout_page in zip(ir["pages"], layout["pages"]):
        entry = by_page.get(ir_page["index"])
        if entry is None:
            continue
        cut_y = entry["cut_y_pt"]
        buckets = (
            ("text_run", [r["y0"] for r in ir_page["text_runs"]], entry["text_run_indices"],
             list(range(len(ir_page["text_runs"])))),
            ("area_fill", [f["y0"] for f in ir_page["area_fills"]], entry["area_fill_indices"],
             list(range(len(ir_page["area_fills"])))),
            ("path", [p["y0"] for p in (ir_page.get("paths") or ())],
             entry.get("path_indices") or [],
             list(range(len(ir_page.get("paths") or ())))),
            ("image", [m["y0"] for m in ir_page["images"]], entry["image_indices"],
             list(range(len(ir_page["images"])))),
            ("rule", [r["y0"] for r in ir_page["rules"]], entry["rule_ids"],
             [r["id"] for r in ir_page["rules"]]),
            ("cell", [c["y0"] for c in layout_page["cells"]], entry["cell_ids"],
             [c["id"] for c in layout_page["cells"]]),
        )
        for kind, tops, guide_side, all_refs in buckets:
            guide_set = set(guide_side)
            if len(guide_set) != len(guide_side):
                problems.append(f"page {ir_page['index']} {kind}: duplicate entries")
            unknown = guide_set - set(all_refs)
            if unknown:
                problems.append(f"page {ir_page['index']} {kind}: unknown refs {sorted(unknown)[:3]}")
            for ref, top in zip(all_refs, tops):
                # cut_y_pt is rounded for the report; compare with the same slack.
                should_be_guide = top >= cut_y - EPS_PT
                if should_be_guide != (ref in guide_set):
                    problems.append(
                        f"page {ir_page['index']} {kind} {ref}: top {top} vs cut {cut_y} "
                        f"but {'in' if ref in guide_set else 'not in'} the guide")
        problems.extend(check_clips(entry, ir_page, layout_page))
    return problems


def check_clips(entry: dict[str, Any], ir_page: dict[str, Any],
                layout_page: dict[str, Any]) -> list[str]:
    """Each clipped straddler's two pieces must reconstruct the original exactly."""
    problems: list[str] = []
    cut_y = entry["cut_y_pt"]
    page = ir_page["index"]
    cells = {c["id"]: c for c in layout_page["cells"]}
    rules = {r["id"]: r for r in ir_page["rules"]}

    for straddler in entry["straddlers"]:
        ref, kind = straddler["ref"], straddler["kind"]
        if straddler["disposition"] != "clipped":
            if kind != "text_run":
                problems.append(f"page {page} {kind} {ref}: not clipped and not a text run")
            continue
        form, guide = straddler["form"], straddler["guide"]
        if form is None or guide is None:
            problems.append(f"page {page} {kind} {ref}: clipped but a side is missing")
            continue
        if abs(form["y1"] - cut_y) > EPS_PT or abs(guide["y0"] - cut_y) > EPS_PT:
            problems.append(f"page {page} {kind} {ref}: pieces do not meet at the cut")
        if abs(form["y0"] - straddler["y0"]) > EPS_PT or abs(guide["y1"] - straddler["y1"]) > EPS_PT:
            problems.append(f"page {page} {kind} {ref}: pieces do not span the original")
        if form["y1"] <= form["y0"] or guide["y1"] <= guide["y0"]:
            problems.append(f"page {page} {kind} {ref}: a piece has no height")
        if kind == "rule":
            axis = rules[ref]["axis"]
            for side, piece in (("form", form), ("guide", guide)):
                span = (piece["x1"] - piece["x0"]) if axis == "h" else (piece["y1"] - piece["y0"])
                if abs(piece["length_pt"] - round(span, 2)) > EPS_PT:
                    problems.append(f"page {page} rule {ref}: {side} length_pt "
                                    f"{piece['length_pt']} != {round(span, 2)}")
        if kind == "cell":
            owned = sorted(cells[ref]["text_run_ids"])
            split = sorted([*form["text_run_ids"], *guide["text_run_ids"]])
            if owned != split:
                problems.append(f"page {page} cell {ref}: runs {split} != owned {owned}")
    return problems


# --------------------------------------------------------------------------
# column structure of a relocated text-only table
# --------------------------------------------------------------------------
#
# A relocated reference table is frequently drawn with no rules at all. 2551M
# page 2's ATC schedule has exactly one horizontal rule anywhere in its 170pt
# band, and that rule is the *foot* of the table, so lattice.py can offer only a
# single 568 x 185pt `label` cell for the whole thing and the ruled-grid reflow
# has nothing to rebuild from. The column structure of such a table exists only
# in where the source set its glyphs.
#
# Reading it in scanline order and concatenating is what shipped a wrong
# statutory rate. The sheet prints SIX columns -- (ATC, Percentage Tax On, Tax
# Rate) twice, side by side -- so one scanline carries two independent table
# rows. Row y 202.02 is `PT 060 | Franchises on electric utilities, gas and
# water utility | 2%` on the left and the middle line of PT 111's description
# with its `5%` on the right. Concatenated, PT 060 came out carrying 5%; the
# official rate is 2%.
#
# So the association has to come from x, and it must not come from a coverage
# histogram either. The histogram that was doing this job asks "how many runs
# cover this 1pt bin", and on 2551M the real gutter between the left
# description and the left rate sits at 4-5 runs against a peak of 18 -- it is
# not empty, because descriptions in that column are long and two page titles
# cross the sheet. Every one of the four missing boundaries was a bin the
# histogram called occupied.
#
# What is unambiguous is where a *cell* starts. A table column is a stack of
# cells that begin at the same x, and the gap that separates one cell from the
# next on a line is nothing like the gap inside one. Measured over all 178
# intra-line run gaps in the corpus's four text-table regions (0605 p2, 2000-OT
# p2, 2550M p3, 2551M p2): every gap inside a cell is <= 8.2pt and every gap
# between cells is >= 15.4pt, with nothing at all in between.
#
# Nothing here rasterises and nothing here reads a form code.

# The midpoint of that measured empty band. A run further than this from the
# ink to its left starts a new cell; anything closer continues the current one,
# which is what keeps "1)" attached to the description it hangs off and "PT",
# " " and "060" -- three glyph runs -- as one ATC code.
CELL_GAP_MIN_PT = 12.0

# Cell starts in one column agree to well under a point (2551M's ATC column
# starts at 24.00 and 24.24; its right description column at 323.04 and 337.20
# are different columns 14pt apart). This absorbs the former without reaching
# the latter.
COLUMN_START_TOL_PT = 1.5

# One line cannot establish a column. Two can: a column is a repetition, and a
# lone start is a title, a centred caption or a stray. This is what keeps
# 2551M's "BIR Form No. 2551M Percentage Tax Return" (one line, x 222.24) from
# cutting the left description column in half.
MIN_COLUMN_SUPPORT = 2

# Distinct starts closer than this are one column: a paragraph indent, a
# hanging list marker, or a second setting of the same column. Measured on the
# same four regions, the widest false separation is 14.28pt (0605's continuation
# indent at 451.90 under its description column at 437.62) and the narrowest
# true one is 27.12pt (0605's second tax-type code column at 224.81 against its
# description at 251.93); 2551M's narrowest true separation is 28.32pt. This
# sits in that band.
MIN_COLUMN_SEPARATION_PT = 20.0

# Runs are on one line when their baselines agree to this. emit.py's reflow
# groups lines with the same constant and self_test() asserts the two agree,
# because a column structure measured on one line grouping and applied to
# another is not a column structure.
LINE_BASELINE_TOLERANCE_PT = 2.0


def text_lines(runs: Sequence[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Runs grouped into lines by baseline, each line ordered left to right.

    The anchor is the line's first baseline rather than a running mean, so a
    tightly leaded column cannot drift a line's tolerance downward until it
    swallows the next one.
    """
    lines: list[list[dict[str, Any]]] = []
    anchor: float | None = None
    for run in sorted(runs, key=lambda r: (float(r["baseline_y"]), float(r["origin_x"]))):
        baseline = float(run["baseline_y"])
        if anchor is None or baseline - anchor > LINE_BASELINE_TOLERANCE_PT:
            anchor = baseline
            lines.append([])
        lines[-1].append(run)
    return [sorted(line, key=lambda r: float(r["origin_x"])) for line in lines]


def cell_starts(runs: Sequence[dict[str, Any]]) -> list[float]:
    """Every x at which a line begins a new cell, across the whole region.

    The first run on a line always starts one. After that a run starts a cell
    when the nearest ink to its left on the same line ends at least
    CELL_GAP_MIN_PT away. `reach` is the rightmost x seen so far rather than the
    previous run's own right edge, because a source may set a short run inside
    the horizontal extent of a longer one it already emitted.
    """
    starts: list[float] = []
    for line in text_lines(runs):
        reach: float | None = None
        for run in line:
            if reach is None or float(run["x0"]) - reach >= CELL_GAP_MIN_PT:
                starts.append(float(run["x0"]))
            reach = float(run["x1"]) if reach is None else max(reach, float(run["x1"]))
    return sorted(starts)


def table_columns(runs: Sequence[dict[str, Any]]) -> list[tuple[float, float]]:
    """The x intervals of a text-only table's columns, left to right.

    Returned in the shape emit.py's table reflow already consumes for its
    column grid, so it is a replacement for that grid and for nothing else: the
    prose path and the ruled-lattice path are untouched by it.

    A region with no repeated cell start is not a table and comes back as one
    column spanning it, which is what 2000-OT p2 and 2550M p3 are and what they
    already emitted.
    """
    if not runs:
        return []
    x0 = min(float(run["x0"]) for run in runs)
    x1 = max(float(run["x1"]) for run in runs)

    clusters: list[list[float]] = []
    for start in cell_starts(runs):
        if clusters and start - clusters[-1][0] <= COLUMN_START_TOL_PT:
            clusters[-1][1] += 1
        else:
            clusters.append([start, 1])

    merged: list[list[float]] = []
    for start, support in clusters:
        if support < MIN_COLUMN_SUPPORT:
            continue
        if merged and start - merged[-1][1] <= MIN_COLUMN_SEPARATION_PT:
            merged[-1][1] = start
        else:
            merged.append([start, start])

    starts = [group[0] for group in merged] or [x0]
    starts[0] = min(starts[0], x0)
    edges = [*starts, max(x1, starts[-1])]
    return [(edges[i], edges[i + 1]) for i in range(len(edges) - 1)]


def column_of(run: dict[str, Any], columns: Sequence[tuple[float, float]]) -> int:
    """The column a run's own left edge sits in. Diagnostic, not emission.

    emit.py decides a run's cell from overlap, so that a run genuinely spanning
    columns gets a colspan rather than being dropped into one of them. This
    answers the narrower question the self-test needs -- "which column did the
    source start this run in" -- and is the question that binds a rate to a code.
    """
    for index in range(len(columns) - 1, -1, -1):
        if float(run["x0"]) >= columns[index][0] - COLUMN_START_TOL_PT:
            return index
    return 0


# --------------------------------------------------------------------------
# corpus sweep
# --------------------------------------------------------------------------

def load_pair(ir_dir: pathlib.Path, layout_dir: pathlib.Path,
              slug: str) -> tuple[dict[str, Any], dict[str, Any]]:
    ir = json.loads((ir_dir / f"{slug}.ir.json").read_text(encoding="utf-8"))
    layout = json.loads((layout_dir / f"{slug}.layout.json").read_text(encoding="utf-8"))
    return ir, layout


def sweep(ir_dir: pathlib.Path, layout_dir: pathlib.Path,
          source_root: pathlib.Path) -> list[tuple[str, dict[str, Any]]]:
    standalone = standalone_guide_pdfs(source_root)
    out: list[tuple[str, dict[str, Any]]] = []
    for path in sorted(ir_dir.glob("*.ir.json")):
        slug = path.name[: -len(".ir.json")]
        if not (layout_dir / f"{slug}.layout.json").is_file():
            continue
        ir, layout = load_pair(ir_dir, layout_dir, slug)
        out.append((slug, build_plan(ir, layout, standalone)))
    return out


def print_corpus_table(rows: Sequence[tuple[str, dict[str, Any]]],
                       standalone: dict[str, list[pathlib.Path]],
                       stream: Any = sys.stdout) -> None:
    def w(line: str = "") -> None:
        print(line, file=stream)

    w(f"{'form':<26} {'pg':>3} {'guide page':>10} {'cut pt':>8} {'reclaim':>8} "
      f"{'%':>4} {'runs':>5} {'fld':>4} {'std':>4}  {'detector':<15} marker")
    w("-" * 132)
    with_inline = with_standalone = neither = 0
    pcts: list[int] = []
    for slug, plan in rows:
        has_std = bool(plan["standalone_pdf"])
        if not plan["inline"]:
            if has_std:
                with_standalone += 1
            else:
                neither += 1
            w(f"{slug:<26} {plan['stats']['pages']:>3} {'-':>10} {'-':>8} {'-':>8} "
              f"{'-':>4} {'-':>5} {'-':>4} {'yes' if has_std else '-':>4}")
            continue
        with_inline += 1
        if has_std:
            with_standalone += 1
        for n, entry in enumerate(plan["inline"]):
            pcts.append(entry["reclaimed_pct"])
            whole = " [whole page]" if entry["whole_page"] else ""
            w(f"{slug if n == 0 else '':<26} {plan['stats']['pages'] if n == 0 else '':>3} "
              f"{entry['page']:>10} {entry['cut_y_pt']:>8.2f} {entry['reclaimed_pt']:>8.2f} "
              f"{entry['reclaimed_pct']:>4} {entry['text_runs_below']:>5} "
              f"{entry['field_cells_below']:>4} "
              f"{('yes' if has_std else '-') if n == 0 else '':>4}  {entry['detector']:<15} "
              f"{entry['marker'][:40]}{whole}")
    w("-" * 132)
    pages = sum(p["stats"]["pages"] for _, p in rows)
    w(f"{len(rows)} forms, {pages} pages: {len(pcts)} guide pages on {with_inline} forms; "
      f"{with_standalone} forms have a standalone guide PDF; {neither} have neither.")
    if pcts:
        w(f"reclaimed: mean {round(sum(pcts) / len(pcts))}%  min {min(pcts)}%  max {max(pcts)}%"
          f"  total {round(sum(e['reclaimed_pt'] for _, p in rows for e in p['inline']))}pt")
    by_detector: dict[str, int] = {}
    for _, plan in rows:
        for entry in plan["inline"]:
            by_detector[entry["detector"]] = by_detector.get(entry["detector"], 0) + 1
    w(f"detectors: {by_detector}")

    w()
    w("straddling elements (clipped at the cut; the form keeps the part above it):")
    total = 0
    for slug, plan in rows:
        for entry in plan["inline"]:
            for s in entry["straddlers"]:
                total += 1
                clip = (f"form y {s['form']['y0']:.2f}->{s['form']['y1']:.2f} | "
                        f"guide y {s['guide']['y0']:.2f}->{s['guide']['y1']:.2f}"
                        if s["disposition"] == "clipped" else "KEPT WHOLE BY THE FORM")
                w(f"  {slug:<24} p{entry['page']} {s['kind']:<9} {s['ref']:<7} "
                  f"y {s['y0']:.2f}->{s['y1']:.2f}  {clip}  {s['detail']}")
    w(f"  {total} straddler(s)")

    w()
    w("proposals refused (why a page was not cut, or not cut higher):")
    for slug, plan in rows:
        for refusal in plan["rejected"]:
            w(f"  {slug:<24} p{refusal['page']} {refusal['detector']:<15} "
              f"cut {refusal['cut_y_pt']:>7.2f}  {refusal['objections'][0]}")

    w()
    w("standalone guide PDFs:")
    built = {form_key(p["form"]["code"], p["form"]["revision"]) for _, p in rows}
    for key, paths in standalone.items():
        for path in paths:
            mark = "" if key in built else "   (no built form for this key)"
            w(f"  {key:<16} {path}{mark}")


# --------------------------------------------------------------------------
# self-test
# --------------------------------------------------------------------------

def self_test(ir_dir: pathlib.Path, layout_dir: pathlib.Path,
              source_root: pathlib.Path) -> int:
    """Assert against the real corpus, not a synthetic fixture."""
    failures: list[str] = []

    def check(condition: bool, message: str) -> None:
        if not condition:
            failures.append(message)

    # The loose pattern that was rejected must stay rejected: these four strings
    # are form content and no marker may fire on them.
    for text in ("Add: Penalties", "Penalties", "Surcharge, Interest and Compromise Penalty",
                 "Total Penalties", "see Table 2 below", "Table of contents"):
        check(marker_hit(text) is None, f"a marker fired on form content {text!r}")
    for text in ("Guidelines and Instructions for BIR Form No. 1603Q",
                 "Table 1 – Alphanumeric Tax Code (ATC)",
                 "ALPHANUMERIC TAX CODES (ATC)"):
        check(marker_hit(text) is not None, f"no marker fired on guide heading {text!r}")

    # A source gutter remains a gutter when extra lattice y coordinates split it
    # into fragments without adding ink. This is deliberately a geometry test:
    # a real horizontal stroke makes the same stack a sequence of entry boxes.
    rail = {"thickness_pt": 1.44, "gray": 0.0}
    gutter_top = {
        "id": "gutter-top", "x0": 10.0, "y0": 10.0, "x1": 20.0, "y1": 28.0,
        "row_span": 2, "text_run_ids": [],
        "border": {"top": rail, "bottom": None, "left": rail, "right": rail},
    }
    gutter_tail = {
        "id": "gutter-tail", "x0": 10.0, "y0": 28.0, "x1": 20.0, "y1": 36.0,
        "row_span": 1, "text_run_ids": [],
        "border": {"top": None, "bottom": None, "left": rail, "right": rail},
    }
    check(is_gutter(gutter_top, [gutter_top, gutter_tail]),
          "an open, fragmented source gutter was treated as a fillable box")
    check(is_gutter(gutter_tail, [gutter_top, gutter_tail]),
          "a middle gutter fragment could not find its source geometry above it")
    competing_tails = [
        {
            "id": f"competing-tail-{suffix}",
            "x0": 10.0, "y0": 36.0, "x1": 20.0, "y1": 44.0,
            "row_span": 1, "text_run_ids": [],
            "border": {"top": None, "bottom": None, "left": rail, "right": rail},
        }
        for suffix in ("a", "b")
    ]
    check(not is_gutter(gutter_top, [gutter_top, gutter_tail, *competing_tails]),
          "a qualifying gutter prefix ignored competing source continuations")
    boxed_tail = dict(gutter_tail)
    boxed_tail.update({
        "id": "boxed-tail",
        "border": {"top": rail, "bottom": rail, "left": rail, "right": rail},
    })
    check(not is_gutter(gutter_top, [gutter_top, boxed_tail]),
          "a horizontal source stroke was ignored when joining gutter fragments")

    # Corpus census. These are pins, not thresholds: they exist so a change that
    # silently adds or drops a form has to be declared here. 51 -> 53 bundles and
    # 110 -> 116 pages is the deliberate arrival of 1604-CF and 2200-AN, the two
    # forms on BIR's official list that had no local source PDF until they were
    # fetched from the BIR CDN. Never move these to make a run pass; move them
    # only when the corpus was meant to change, and say which forms in the commit.
    rows = sweep(ir_dir, layout_dir, source_root)
    check(len(rows) == 53, f"expected 53 forms in the corpus, got {len(rows)}")
    plans = dict(rows)

    pages = sum(p["stats"]["pages"] for _, p in rows)
    check(pages == 116, f"expected 116 pages in the corpus, got {pages}")

    guide_pages = [(slug, e) for slug, p in rows for e in p["inline"]]
    forms_with = [slug for slug, p in rows if p["inline"]]
    # 29 -> 30 pages and 28 -> 29 forms is one page arriving: 1601-EQ p2, which
    # is pinned in `expected` below with the reason it was previously refused.
    # No page left, and no page that already had a cut changed where it cuts.
    check(len(guide_pages) == 30, f"expected 30 guide pages, got {len(guide_pages)}")
    check(len(forms_with) == 29, f"expected 29 forms with a guide, got {len(forms_with)}")

    pcts = [e["reclaimed_pct"] for _, e in guide_pages]
    # 60 -> 62 is arithmetic, not drift: the 29th guide page (2200-AN, which
    # reclaims 100% of its page) pulls a 28-page mean of 60 up by two points.
    # 62 -> 63 is the same arithmetic again for the 30th (1601-EQ p2, 86%).
    # min and max are unmoved, which is the check that the distribution's shape
    # did not change -- only its membership.
    check(round(sum(pcts) / len(pcts)) == 63, f"expected mean reclaim 63%, got {pcts}")
    check(min(pcts) == 9, f"expected min reclaim 9%, got {min(pcts)}")
    check(max(pcts) == 100, f"expected max reclaim 100%, got {max(pcts)}")

    # Cut, field cells below, runs below, reclaimed %, detector. Every detector,
    # every C6 blocker page, and the cuts the validated prototype measured to the
    # hundredth. `field_cells_below` counts cells lattice.py called `field`,
    # including the ones a pre-printed run crosses; the corpus-wide assertion
    # further down is what proves none of them is actually fillable.
    #
    # Four of those counts dropped when lattice.py learned to read the tone of
    # the paper: a cell whose topmost covering fill is decorative shading at
    # gray <= 0.87 is no longer `field` but a new kind, `shaded`. Nothing left a
    # page and no cut moved -- each of these cuts is at the same y, relocating
    # the same runs, at the same percentage, found by the same detector. What
    # moved is only what the census calls the grey blocks BIR prints inside a
    # reference table to mean "no code applies in this column": 1700 p2 9 -> 1
    # (8 cells, gray 0.8509), 1701MS p2 6 -> 5 (1, 0.8509), 2000-DST p2 7 -> 2
    # (5, 0.651 and 0.8509), 2550M p3 6 -> 1 (5, 0.7529). Every dropped cell was
    # checked one at a time: each is `shaded` in the new layout, each has a
    # decorative covering fill beneath it, and no cell anywhere in the corpus
    # became `field` that was not one before. 1601-FQ p2 (24 -> 0) and 1602Q p3
    # (4 -> 0) moved for the same reason and are not pinned here.
    expected = {
        ("1603q-2018", 2): (284.54, 0, 171, 70, "marker"),
        # 126 -> 121 on 2026-08-10 (P3): this page's ATC table sets its blanks
        # as WHITE underscores, and extract.py now publishes a run of them as
        # the bar it draws instead of as text. Twenty groups on the page; the
        # fifteen that share a run with a code ('MMC ______') keep that code as
        # a run, and the five that are a run of their own ('______') leave the
        # text entirely. Cut, field cells below, reclaim and detector are all
        # unchanged: nothing moved but which layer the underscores are on.
        ("1600-pt-2018", 2): (283.91, 4, 121, 70, BAND_PATTERN),
        ("2551q-2018", 2): (292.43, 2, 93, 69, BAND_PATTERN),
        # 2551M keeps its cut only because the comb test asks about comb *bands*:
        # p2c0's box crosses the cut, its 2-slot comb sits 100pt above it.
        ("2551m-2002", 2): (154.32, 0, 213, 85, "marker"),
        # The tax-bracket tables C6 called a blocker: a taxpayer could type over
        # "Not over P 250,000". Partial cuts -- the fillable face above them is
        # untouched, which is what the seven-page guard below proves.
        ("1700-2018", 2): (837.56, 1, 22, 11, BAND_PATTERN),
        ("1701ms-2024", 2): (838.43, 5, 22, 10, BAND_PATTERN),
        ("1701q-2018", 2): (769.46, 9, 22, 18, BAND_PATTERN),
        ("1701a-2018", 2): (854.84, 9, 22, 9, BAND_PATTERN),
        # 2 -> 0 on 2026-08-09 (r35): p2c230 and p2c232 (both y 751.5-769.7,
        # below this page's 538.03 cut) are shaded paper the seam rule now
        # reaches -- the source paints their band in two strips with a seam too
        # narrow to write in, so they were fields carrying an input over "do
        # not write here". They are the only two fields this page had below the
        # cut. Nothing about the cut itself moved.
        ("2000-dst-2018", 2): (538.03, 0, 137, 43, BAND_PATTERN),
        ("2552-2018", 2): (801.52, 2, 23, 14, BAND_PATTERN),
        # 1601-EQ p2 held the "no cut at all" line until lattice.py started
        # reading paper tone, and it was holding it for a defect. The page is a
        # masthead, a TIN comb, a Withholding Agent's Name comb, and then
        # nothing but the "Schedule of Alphanumeric Tax Codes (ATC)" -- the
        # reference material this module exists to move, on the same page design
        # 1602Q p3 has, which has always cut at 128.42 for 86%. What refused it
        # was admissible_cut_limit stopping at 831.84 on "cut would sever
        # fillable column p2c222 -> p2c226": two grey blocks (0.7489/0.8509) in
        # the bottom-right corner of the ATC table, where the right-hand column
        # runs out of codes before the left one does. They are decoration, they
        # were never an entry column, and now that they are `shaded` the walk
        # stops where it should -- at the TIN comb, y 110.06 -- so the band cut
        # lands at 129.62, the lattice line under the comb row. Both combs stay
        # on the form, and the corpus-wide comb assertion below is the proof.
        # 26 -> 25 on 2026-08-09 (r35): p2c49 (y 242.5-259.6, below this page's
        # 129.62 cut) is shaded paper the seam rule now reaches. Same cause as
        # 2000-DST p2 above; one cell, and the cut is unchanged.
        ("1601eq-2019", 2): (129.62, 25, 339, 86, BAND_PATTERN),
        # Whole pages: 2550M p3 is nothing but its ATC table, so the marker cut at
        # 72.52 that left the running head behind is superseded by cutting at 0.
        #
        # 2550M p3 4 -> 6 and 0605 p2 6 -> 8 is lattice.wall_boundaries -- "let a
        # painted wall bound a cell, not only a rule". Both pages end their ATC
        # table at a black painted rectangle rather than a rule (2550M p3 at
        # x 590.04-591.96, 1.92pt; 0605 p2 at x 21.24-23.04, 1.8pt), both are over
        # MAX_RULE_THICKNESS_PT so neither was ever a column, and the outermost
        # table column therefore stopped short of it. Reaching the wall closes that
        # column, and the two cells it gains on each page are the blanks a group
        # heading leaves there: 2550M's ATC column beside "9. Real Estate, Renting
        # & Business Activity" and "12. Others:", 0605's code column beside "Excise
        # Tax on Goods" and "Alcohol Products". Every one is empty, carries no run,
        # classifies `whitespace` against the printed code in its own column, and is
        # claimed by a cut at 0 -- so the count grew where the census counts, inside
        # the reclaimed region, while the corpus-wide non-fillable assertion below
        # is unmoved. The cut, runs, reclaim and detector are all unchanged.
        #
        # 6 -> 1 is the paper-tone change described at the head of this table, and
        # it half-supersedes the paragraph above: of the two blanks a group heading
        # leaves in 2550M's ATC column, the one beside "12. Others:" is one of the
        # five cells here that BIR fills with its 0.7529 grey, so it is `shaded`
        # now and no longer counted. The one beside "9. Real Estate, Renting &
        # Business Activity" is grey on paper too but stays `field`: it is a
        # two-row-tall cell and the source paints that block as two 7.8pt strips,
        # neither of which covers 70% of it on its own. 0605 p2 keeps all 8 -- its
        # blanks are white. Cut, runs, reclaim and detector are again unchanged.
        #
        # 1 -> 0 (2026-08-07, r14) is the rest of that same sentence arriving.
        # `lattice.on_shaded_paper` no longer asks a single fill's own rectangle;
        # `covering_shading_band` clips every overlapping fill to the cell,
        # resolves each atom to the TOPMOST fill, then measures the largest
        # CONNECTED region of one exact tone. The two 7.8pt 0.7529 strips over
        # "9. Real Estate, Renting & Business Activity" touch, so they are one
        # band and do reach 70% -- the cell is `shaded`, which is what the
        # paragraph above says the paper actually is. Neither threshold moved
        # (SHADED_PAPER_MAX_GRAY 0.87, SHADED_PAPER_MIN_COVERAGE 0.70). Cut,
        # runs, reclaim and detector are again unchanged, and 0605 p2 still
        # keeps all 8.
        ("2550m-2007", 3): (0.0, 0, 125, 100, UNFILLABLE_PATTERN),
        ("2550m-2007", 4): (0.0, 0, 143, 100, UNFILLABLE_PATTERN),
        ("2553-1999", 2): (0.0, 0, 94, 100, UNFILLABLE_PATTERN),
        ("0605-1999", 2): (0.0, 8, 281, 100, UNFILLABLE_PATTERN),
    }
    for (slug, page), (cut, fields, runs, pct, detector) in expected.items():
        entry = next((e for e in plans[slug]["inline"] if e["page"] == page), None)
        if entry is None:
            failures.append(f"{slug} p{page}: no guide detected")
            continue
        check(entry["cut_y_pt"] == cut, f"{slug} p{page}: cut {entry['cut_y_pt']} != {cut}")
        check(entry["field_cells_below"] == fields,
              f"{slug} p{page}: {entry['field_cells_below']} fields below, expected {fields}")
        check(entry["text_runs_below"] == runs,
              f"{slug} p{page}: {entry['text_runs_below']} runs below, expected {runs}")
        check(entry["reclaimed_pct"] == pct,
              f"{slug} p{page}: reclaimed {entry['reclaimed_pct']}%, expected {pct}%")
        check(entry["detector"] == detector,
              f"{slug} p{page}: detector {entry['detector']}, expected {detector}")

    # The six pages a cut must never eat. Three take no cut at all. Three --
    # 1700 p2, 1701MS p2, 2552 p2 -- end in a statutory rate or ATC table and
    # take a small partial cut, leaving the fillable face above it untouched;
    # C6 lists two of them as blockers for exactly that table. So the assertion
    # is the property that matters rather than "no cut": nothing fillable moves.
    #
    # 1601-EQ p2 was a fourth "no cut" page and is one no longer. It is pinned
    # in `expected` above instead, on all five of its values rather than on the
    # single fact that it was refused, because the thing that refused it was two
    # shaded ATC-table blocks read as an entry column and not any form content.
    for slug, page in (("1701-2018", 1), ("1701q-2018", 1), ("1702ex-2018", 1)):
        entry = next((e for e in plans[slug]["inline"] if e["page"] == page), None)
        check(entry is None, f"{slug} p{page}: cut a guide out of fillable form")
    for slug, page in (("1700-2018", 2), ("1701ms-2024", 2), ("2552-2018", 2)):
        entry = next((e for e in plans[slug]["inline"] if e["page"] == page), None)
        check(entry is not None and entry["reclaimed_pct"] <= 15,
              f"{slug} p{page}: expected a small partial cut, got {entry}")

    # Corpus-wide, and the reason the seven-page list is no longer the guard:
    # no cut anywhere may relocate a comb, a fillable box or a growable band.
    for slug, plan in rows:
        ir, layout = load_pair(ir_dir, layout_dir, slug)
        by_page = {e["page"]: e for e in plan["inline"]}
        for ir_page, layout_page in zip(ir["pages"], layout["pages"]):
            entry = by_page.get(ir_page["index"])
            if entry is None:
                continue
            cut_y = entry["cut_y_pt"]
            claimed = set(entry["cell_ids"])
            # Comb *bands*, not comb cells: 2551M p2c0 is a one-bordered walk
            # artefact whose box crosses the cut while its comb band sits 100pt
            # above it, and the band is what must stay whole and stay on the form.
            combs = [f"{c['id']} y{b['y0']}-{b['y1']}" for c in layout_page["cells"]
                     for b in comb_bands(c)
                     if b["y0"] >= cut_y - EPS_PT or b["y0"] < cut_y < b["y1"]]
            check(not combs, f"{slug} p{ir_page['index']}: cut relocates or splits "
                             f"comb band(s) {combs[:3]}")
            band = [c for c in layout_page["cells"] if c["y0"] >= cut_y - EPS_PT]
            for cell in empty_boxes(ir_page, layout_page):
                if cell["id"] not in claimed:
                    continue
                verdict = classify_vacancy(cell, band, layout_page["cells"])
                check(not verdict.fillable,
                      f"{slug} p{ir_page['index']}: relocates fillable box "
                      f"{cell['id']} ({verdict.verdict})")
            # A growable band is a repeating *fillable* table by construction, so
            # it must stay wholly above the cut -- not straddle it, not follow it.
            for growable in layout_page.get("growable") or ():
                check(growable["y1"] <= cut_y + EPS_PT,
                      f"{slug} p{ir_page['index']}: growable {growable['id']} "
                      f"y{growable['y0']}-{growable['y1']} is not above the cut {cut_y}")

    # Exhaustive and disjoint, on every form, not just the interesting ones,
    # and every clipped straddler reconstructs the element it came from.
    for slug, plan in rows:
        ir, layout = load_pair(ir_dir, layout_dir, slug)
        for problem in check_partition(plan, ir, layout):
            failures.append(f"{slug}: {problem}")

    # No text run may straddle: a split glyph run cannot be rendered twice.
    for slug, entry in guide_pages:
        split_runs = [s for s in entry["straddlers"] if s["kind"] == "text_run"]
        check(not split_runs, f"{slug} p{entry['page']}: text run straddles the cut {split_runs}")

    # ------------------------------------------------------------------
    # Column structure of a text-only table.
    # ------------------------------------------------------------------
    # The defect this exists to prevent, stated as geometry rather than as a
    # form: two independent table rows printed side by side on one scanline,
    # each with its own rate. Read in scanline order they concatenate and the
    # left row's code takes the right row's rate. The first assertion is the
    # one that shipped wrong: `2%` must be the cell that follows PT 060.
    def run(text: str, x0: float, x1: float, baseline: float) -> dict[str, Any]:
        return {"text": text, "x0": x0, "x1": x1, "origin_x": x0,
                "baseline_y": baseline, "y0": baseline - 6.0, "y1": baseline}

    # Six scanlines, each carrying two independent source rows. Two shapes of
    # right-hand row occur and both have to be here: one whose description
    # follows a code on the same line, and one that is a continuation with no
    # code beside it -- it is the second that establishes the description
    # column, and the pair that makes the two settings one column rather than
    # two. Scanline index 2 is the PT 060 row: a left rate and a right rate on
    # one line, with a continuation between them.
    scanlines = [
        # code   description       rate            code2         description2   rate2
        (24.0, 49.7, 66.5, 234.7, 251.5, 261.6, None, None, 323.0, 404.4, None, None),
        (24.0, 49.7, 66.5, 196.3, 251.5, 261.6, 294.7, 317.3, 337.2, 529.7, None, None),
        (24.0, 49.7, 66.5, 226.3, 251.5, 261.6, None, None, 337.2, 450.2, 549.1, 559.2),
        (24.0, 49.7, 66.5, 241.4, 251.5, 261.6, 294.7, 317.3, 337.2, 525.6, None, None),
        (None, None, 66.5, 241.4, 251.5, 261.6, None, None, 323.0, 447.6, None, None),
        (24.0, 49.7, 66.5, 204.0, 251.5, 261.6, 294.7, 317.3, 323.0, 399.1, 549.1, 559.2),
    ]
    labels = ("code", "desc", "rate", "code2", "desc2", "rate2")
    table = []
    for row, spans in enumerate(scanlines):
        baseline = 200.0 + 8.64 * row
        for index, label in enumerate(labels):
            x0, x1 = spans[2 * index], spans[2 * index + 1]
            if x0 is None:
                continue
            text = "2%" if (row == 2 and label == "rate") else label
            table.append(run(text, x0, x1, baseline))
    columns = table_columns(table)
    check(len(columns) == 6,
          f"a six-column source table came back as {len(columns)} column(s): "
          f"{[(round(a, 2), round(b, 2)) for a, b in columns]}")
    third = [r for r in table if abs(float(r["baseline_y"]) - (200.0 + 8.64 * 2)) < 0.5]
    placed = {column_of(r, columns): r["text"] for r in third}
    check(placed.get(0) == "code" and placed.get(1) == "desc" and placed.get(2) == "2%",
          f"a rate bound to the wrong column on a two-row scanline: {placed}")
    check(placed.get(5) == "rate2",
          f"the right half's rate did not stay in the right half: {placed}")

    # The two measured bands the constants sit in, asserted as behaviour rather
    # than as arithmetic on the constants themselves.
    pair = [run("a", 10.0, 20.0, 100.0), run("b", 28.2, 40.0, 100.0)]
    check(cell_starts(pair) == [10.0], "an 8.2pt gap inside a cell started a new one")
    pair = [run("a", 10.0, 20.0, 100.0), run("b", 35.4, 50.0, 100.0)]
    check(cell_starts(pair) == [10.0, 35.4], "a 15.4pt gap between cells did not start one")

    # And against the real pages, on the two bands the corpus actually draws
    # without rules. The y windows are facts about the source sheets, not
    # tuning: 2551M page 2's ATC schedule runs from the cut at 154.32 down to
    # the "BIR Form No. 2551M" title at 307.2, and 0605 page 2's tax-type table
    # and two-column guidelines begin below its ATC table's last row at 274.5.
    bands = {
        ("2551m-2002", 2, 154.32, 305.0): [
            (24.0, 66.48), (66.48, 251.52), (251.52, 294.72),
            (294.72, 323.04), (323.04, 534.96), (534.96, 582.96)],
        ("0605-1999", 2, 280.0, 1e6): [
            (37.44, 94.8), (94.8, 224.81), (224.81, 251.93), (251.93, 317.83),
            (317.83, 388.15), (388.15, 437.62), (437.62, 576.94)],
    }
    for (slug, page, low, high), want in bands.items():
        ir, _layout = load_pair(ir_dir, layout_dir, slug)
        entry = next((e for e in plans[slug]["inline"] if e["page"] == page), None)
        if entry is None:
            failures.append(f"{slug} p{page}: no guide region to read columns from")
            continue
        ir_page = next(p for p in ir["pages"] if p["index"] == page)
        claimed = set(entry["text_run_indices"])
        band_runs = [r for i, r in enumerate(ir_page["text_runs"])
                     if i in claimed and low - EPS_PT <= r["y0"] < high]
        got = [(round(a, 2), round(b, 2)) for a, b in table_columns(band_runs)]
        check(got == want, f"{slug} p{page}: columns {got} != {want}")

    failures.extend(emit_agreement_failures())

    # Determinism: the plan is a pure function of its inputs.
    ir, layout = load_pair(ir_dir, layout_dir, "1603q-2018")
    standalone = standalone_guide_pdfs(source_root)
    first = json.dumps(build_plan(ir, layout, standalone), sort_keys=True)
    second = json.dumps(build_plan(ir, layout, standalone), sort_keys=True)
    check(first == second, "build_plan is not deterministic")

    # The standalone mapping, and the claim that it uses batch.py's logic.
    check(len(standalone) >= 1 or not source_root.is_dir(),
          f"no standalone guide PDFs found under {source_root}")
    if source_root.is_dir():
        for key in ("1601EQ-2019", "1701Q-2018", "2550Q-2024", "2552-2018", "1600WP-2010"):
            check(key in standalone, f"standalone guide for {key} not found")
        check(len(standalone.get("2200AN-2018", [])) == 2,
              "2200AN should map to two non-form PDFs (guidelines + Annex A)")
        failures.extend(batch_agreement_failures(source_root))

    for message in failures:
        print(f"FAIL {message}", file=sys.stderr)
    print(f"guides self-test: {len(failures)} failure(s)", file=sys.stderr)
    return 1 if failures else 0


def emit_agreement_failures() -> list[str]:
    """The line grouping this module measures columns on must be emit.py's.

    `table_columns` reads a column structure off the lines it groups; emit.py's
    reflow then lays runs out on lines it groups itself. If the two tolerances
    ever differ, the structure is measured on one partition of the runs and
    applied to another, which is a defect of exactly the shape this module was
    written to remove. One constant, asserted rather than assumed -- the same
    trade REVISION_OVERRIDES makes against batch.py.
    """
    sys.path.insert(0, str(HERE))
    try:
        import emit  # noqa: PLC0415 - optional, and only for this cross-check
    except Exception as error:  # noqa: BLE001 - a broken emit.py is not our failure
        return [f"(skipped emit.py cross-check: {error})"]
    finally:
        sys.path.pop(0)

    theirs = getattr(emit, "LINE_BASELINE_TOLERANCE_PT", None)
    if theirs != LINE_BASELINE_TOLERANCE_PT:
        return [f"LINE_BASELINE_TOLERANCE_PT drifted from emit.py: "
                f"{theirs} vs {LINE_BASELINE_TOLERANCE_PT}"]
    return []


def batch_agreement_failures(source_root: pathlib.Path) -> list[str]:
    """parent_form_identity() must agree with batch.classify() where both speak.

    The two derivations are written out separately -- batch.py is owned
    elsewhere and importing its classify() would not work here anyway, since it
    returns None for exactly the files this module is about. Asserting the
    agreement is what keeps them from drifting apart silently.
    """
    sys.path.insert(0, str(HERE))
    try:
        import batch  # noqa: PLC0415 - optional, and only for this cross-check
    except Exception as error:  # noqa: BLE001 - a broken batch.py is not our failure
        return [f"(skipped batch.py cross-check: {error})"]
    finally:
        sys.path.pop(0)

    out: list[str] = []
    if getattr(batch, "REVISION_OVERRIDES", None) != REVISION_OVERRIDES:
        out.append(f"REVISION_OVERRIDES drifted from batch.py: {batch.REVISION_OVERRIDES}")
    if tuple(getattr(batch, "NON_FORM_MARKERS", ())) != GUIDE_NAME_MARKERS:
        out.append(f"NON_FORM_MARKERS drifted from batch.py: {batch.NON_FORM_MARKERS}")
    for pdf in sorted(source_root.rglob("*.pdf")):
        source = batch.classify(pdf)
        if source is None:
            continue
        mine = parent_form_identity(pdf)
        if mine != (source.code, source.revision):
            out.append(f"identity drift on {pdf.name}: {mine} vs batch {(source.code, source.revision)}")
    return out


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def print_summary(slug: str, plan: dict[str, Any], stream: Any = sys.stderr) -> None:
    stats = plan["stats"]
    print(f"{slug}: {stats['pages']} page(s), {stats['pages_with_guide']} with a guide region",
          file=stream)
    for entry in plan["inline"]:
        print(f"  page {entry['page']}: cut y={entry['cut_y_pt']}pt  "
              f"reclaims {entry['reclaimed_pt']}pt ({entry['reclaimed_pct']}%)  "
              f"{entry['text_runs_below']} runs / {entry['field_cells_below']} field cells below"
              f"{'  [whole page]' if entry['whole_page'] else ''}", file=stream)
        print(f"           detector [{entry['detector']}] {entry['marker']!r}", file=stream)
        print(f"           admissible above y={entry['walk_limit_pt']} "
              f"({entry['walk_limit_reason']}); relocated empty boxes: "
              f"{entry['vacancies']['whitespace']} table whitespace, "
              f"{entry['vacancies']['gutter']} frame gutter", file=stream)
        print(f"           guide takes {len(entry['rule_ids'])} rules, "
              f"{len(entry['area_fill_indices'])} fills, {len(entry['image_indices'])} images, "
              f"{len(entry['cell_ids'])} cells, {len(entry['text_run_indices'])} runs",
              file=stream)
        for s in entry["straddlers"]:
            if s["disposition"] == "clipped":
                print(f"           CLIPPED {s['kind']} {s['ref']} "
                      f"y {s['y0']:.2f}->{s['y1']:.2f} ({s['detail']}) -- form keeps "
                      f"{s['form']['y0']:.2f}->{s['form']['y1']:.2f}, guide takes "
                      f"{s['guide']['y0']:.2f}->{s['guide']['y1']:.2f}", file=stream)
            else:
                print(f"           STRADDLES {s['kind']} {s['ref']} "
                      f"y {s['y0']:.2f}->{s['y1']:.2f} ({s['detail']}) -- kept by the form",
                      file=stream)
    for refusal in plan["rejected"]:
        print(f"  page {refusal['page']}: refused {refusal['detector']} cut at "
              f"{refusal['cut_y_pt']}pt -- {refusal['objections'][0]}", file=stream)
    print(f"  standalone guide PDF: {plan['standalone_pdf'] or 'none'}", file=stream)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--ir", type=pathlib.Path, help="one form's .ir.json")
    parser.add_argument("--layout", type=pathlib.Path, help="the matching .layout.json")
    parser.add_argument("--out", type=pathlib.Path, help="write the guide plan here")
    parser.add_argument("--summary", action="store_true")
    parser.add_argument("--corpus", action="store_true",
                        help="sweep every build/ir/*.ir.json and print the detection table")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--ir-dir", type=pathlib.Path, default=pathlib.Path("build/ir"))
    parser.add_argument("--layout-dir", type=pathlib.Path, default=pathlib.Path("build/layout"))
    parser.add_argument("--out-dir", type=pathlib.Path,
                        help="--corpus: write one guide plan per form here")
    parser.add_argument("--source-root", type=pathlib.Path, default=DEFAULT_SOURCE_ROOT)
    args = parser.parse_args()

    if args.self_test:
        return self_test(args.ir_dir, args.layout_dir, args.source_root)

    if args.corpus:
        rows = sweep(args.ir_dir, args.layout_dir, args.source_root)
        print_corpus_table(rows, standalone_guide_pdfs(args.source_root))
        if args.out_dir:
            args.out_dir.mkdir(parents=True, exist_ok=True)
            for slug, plan in rows:
                (args.out_dir / f"{slug}.guide.json").write_text(
                    json.dumps(plan, indent=2) + "\n", encoding="utf-8")
        return 0

    if not (args.ir and args.layout):
        parser.error("--ir and --layout are required unless --corpus or --self-test")

    ir = json.loads(args.ir.read_text(encoding="utf-8"))
    layout = json.loads(args.layout.read_text(encoding="utf-8"))
    plan = build_plan(ir, layout, standalone_guide_pdfs(args.source_root))

    problems = check_partition(plan, ir, layout)
    if problems:
        for problem in problems:
            print(f"PARTITION {problem}", file=sys.stderr)
        return 1

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")
    if args.summary or not args.out:
        print_summary(args.ir.name[: -len(".ir.json")], plan)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
