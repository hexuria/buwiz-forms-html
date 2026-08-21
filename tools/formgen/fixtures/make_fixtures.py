#!/usr/bin/env python3
"""Build the synthetic PDF corpus extract.py's --self-test measures in CI.

`extract.py --self-test` is the strongest check this pipeline has, and none of
it could run anywhere but this laptop: every assertion is pinned against seven
official BIR PDFs that are deliberately untracked (`*.pdf` is gitignored, they
are official documents, and they are pinned by sha256 precisely so a swapped
file fails loudly). A CI job that skipped it would print a green tick having
evaluated nothing -- the exact failure this project spent seventy commits
removing from its own gate.

So this builds a second corpus that *is* trackable. Each file here is the
smallest PDF that still exercises one property the real corpus taught us about,
and the docstring on each builder names the real form it stands in for. The
fixtures do not replace the real pins: a fixture can only ever encode what this
module already believes, whereas the seven official files are evidence. Both pin
tables live in extract.py and both are still runnable; `--self-test` alone reads
the real one.

Determinism is not incidental. The pins in extract.py are sha256 over these
exact bytes, so a rebuild that differed by one byte would fail extraction rather
than silently score a different corpus. MuPDF stamps no creation date once the
metadata is cleared and `no_new_id` suppresses the /ID array, so two runs of
this script produce byte-identical files. The one thing that does move them is
the MuPDF version, whose banner MuPDF writes into its own header comment; that
is why --verify exists and why it prints the version it measured.

Usage:
    python3 tools/formgen/fixtures/make_fixtures.py            # write the corpus
    python3 tools/formgen/fixtures/make_fixtures.py --verify   # rebuild + compare
    python3 tools/formgen/fixtures/make_fixtures.py --pins     # the pin table
"""

from __future__ import annotations

import argparse
import hashlib
import pathlib
import sys
from typing import Any, Callable, Sequence

try:
    import fitz  # PyMuPDF
except ImportError:  # pragma: no cover - environment guard
    sys.exit("PyMuPDF is required: pip install pymupdf")

FIXTURE_ROOT = pathlib.Path(__file__).resolve().parent

# Folio, the paper 2551Q is printed on: neither A4 nor Letter, so a fixture that
# accidentally took a default would be visible in the paper assertion.
PAGE_WIDTH_PT = 612.0
PAGE_HEIGHT_PT = 936.0

# The four rule thicknesses the BIR generator actually emits. 0.24 is also the
# thickness extract.py invents for a zero-width `l` op, which is why the paths
# fixture below must carry no rule at all.
THICKNESSES_PT = (0.24, 0.48, 0.96, 1.44)

# The two decorative greys the corpus uses, and the knockout white. CLAUDE.md
# records that painting a decorative grey black is a shipped past failure, so
# tone classification has to be exercised on real values, not on placeholders.
GREY_LIGHT = 0.8509
GREY_MID = 0.6509
WHITE = 1.0
BLACK = 0.0

# F210's checkbox square: a closed box of four DECORATIVE rules (frame) around
# a KNOCKOUT interior (the source's own "write here"), reproduced at the
# corpus's own scale. The frame is painted GREY_MID, the SAME live constant
# every other decorative rule in this fixture uses (not a snapshot), so
# `mutate_tone`'s corpus-wide "paint every decorative rule black" reaches it
# too, exactly as it reaches the rest of the corpus's decorative ink.
CHECKBOX_SQUARE_THICKNESS_PT = THICKNESSES_PT[1]

# How far prove_fixtures_fail.py's mutate_checkbox_square slides the knockout
# fill off the frame's own centreline. Zero in every built fixture; a
# dedicated constant rather than an inline literal so the mutation can reach
# it without editing the drawing call.
CHECKBOX_SQUARE_KNOCKOUT_DRIFT_PT = 0.0

# F211/F212's two captions, reproduced from 2551Q page 1 verbatim (the
# in-box one) and abbreviated (the one below, which only needs the two words
# `emit._signature_line_caption` tests for). Both set at the corpus's own
# modal body size.
SIGNATURE_BOX_CAPTION = "For Individual: "
SIGNATURE_LINE_CAPTION = "Signature over Printed Name of Taxpayer"
SIGNATURE_BOX_FONT_SIZE_PT = 9.0

# How far prove_fixtures_fail.py's mutate_signature_box/mutate_signature_line
# slide each caption's own baseline, in points, positive downward. Zero in
# every built fixture; dedicated constants rather than inline literals so
# each mutation can reach one caption without touching the drawing call or
# the other caption.
SIGNATURE_BOX_CAPTION_DRIFT_PT = 0.0
SIGNATURE_LINE_CAPTION_DRIFT_PT = 0.0

# F151's row-number rule (P2): a bare row number -- "1", nothing else -- in a
# bordered `label` cell sharing its row with a bordered `field` cell, with
# paper beside it wide enough to be a description surface too. The bound is
# the form's own `line_width_pt` (`lattice.min_fillable_line_metrics`, two em
# squares of the smallest two-glyph body run) at 1.0x -- no new constant --
# and in this fixture that body run is `SIGNATURE_BOX_CAPTION` /
# `SIGNATURE_LINE_CAPTION` at `SIGNATURE_BOX_FONT_SIZE_PT` (9pt), the only
# other text this file's IR states, giving `line_width_pt` = 18pt. The
# numeral is one glyph, so it never enters that measurement itself (the
# metric demands two or more non-whitespace glyphs) -- it only ever sits
# beside the paper the metric bounds.
#
# `ROW_NUMBER_LABEL_WIDTH_PT` is the label cell's own width, wide enough at
# 60pt that the paper trailing the numeral clears 18pt several times over.
# prove_fixtures_fail.py's mutate_row_number shrinks it below the bound
# without moving the numeral's own ink, any rule, or the field cell's own
# width, so that mutation trips the row-number rule alone.
ROW_NUMBER_LABEL_WIDTH_PT = 60.0
ROW_NUMBER_FIELD_WIDTH_PT = 190.0
ROW_NUMBER_ROW_HEIGHT_PT = 24.0
ROW_NUMBER_TEXT = "1"
ROW_NUMBER_FONT_SIZE_PT = SIGNATURE_BOX_FONT_SIZE_PT

# A stroked separator that leans less than its own stroke width. 2316 draws
# twelve of these; an exact-alignment test demoted every one of them out of
# `rules`, taking real box sides out of lattice.py's reach. 0.17pt of lean
# across 14.5pt of run, against a 0.44pt stroke.
LEAN_COUNT = 12
LEAN_RUN_PT = 14.5
LEAN_OFFSET_PT = 0.17
LEAN_STROKE_PT = 0.44

# The unmappable glyph. A Type3 font whose one glyph has a non-standard name and
# no ToUnicode CMap: get_texttrace() answers U+FFFD honestly, while rawdict
# hands back the raw code byte -- 0xA7, which reads as a section sign and looks
# exactly like content. That is 2550M and 2553's defect, reproduced.
UNMAPPED_CODE = 0xA7
UNMAPPED_GLYPH_NAME = "gexotic"

# F064's comb-band-reunification shape (W3, `lattice._reunify_comb_band`): a
# bordered row whose comb only partly reaches the row's own top rule -- the
# comb's own left rail is drawn from mid-row down, exactly as 1707-2021's own
# v255/v256 continue an existing border rather than hanging free -- so the
# row's own DSU component is genuinely non-rectangular only when something
# ELSE inside the row also fails to reach a wall (1707-2021's own item 8/9
# checkboxes). `COMB_BAND_REUNIFICATION_NOTCH_SIZE_PT` is that something: a
# small bordered notch box, left of the comb, entirely inside the row. At
# 0.0 (every built fixture) nothing is drawn there, the row's own component
# stays rectangular, `source_owned_comb_frame` owns it directly, and the
# comb resolves through the ordinary path with no `retained_unresolved`
# subject at all. `prove_fixtures_fail.py`'s `mutate_comb_band_reunification`
# sets it to 12.0, drawing the notch: the row's own component becomes
# non-rectangular, `no-rectangular-owner` appears (F064's own ledger state),
# and `lattice._reunify_comb_band` correctly DECLINES to absorb it -- the
# notch's own two internal walls match none of the comb's own divider
# positions, which is exactly the "never swallow paper this mechanism does
# not understand" refusal the mechanism exists to make.
COMB_BAND_REUNIFICATION_ROW_WIDTH_PT = 300.0
COMB_BAND_REUNIFICATION_ROW_HEIGHT_PT = 40.0
# Rescaled 2026-08-16 for DECISION A: the fixture's compartments were 60pt
# out of drawing convenience, which the compartment rule now refuses as
# not-character-boxes (the corpus's real bound is 24.5pt). The rail inset
# and slot count move together so every compartment lands ~20-23pt --
# rule-legal -- while every property the fixture exists for (outer paper
# within one pitch of the rails, the notch mutation inside one slot) keeps
# its own relation to the pitch.
COMB_BAND_REUNIFICATION_RAIL_INSET_PT = 20.0
COMB_BAND_REUNIFICATION_RAIL_TOP_INSET_PT = 15.0
COMB_BAND_REUNIFICATION_SLOTS = 12
COMB_BAND_REUNIFICATION_BAND_TOP_INSET_PT = 25.0
COMB_BAND_REUNIFICATION_NOTCH_SIZE_PT = 0.0

# F221 case 1's shape (`emit.SignatureRuleWriting`): a bordered `label` cell
# (the jurat declaration's own item number, nothing else) rules a VECTOR
# signature line across its own bottom wall, and a SEPARATE `label` cell
# directly below carries the "Signature over Printed Name..." caption that
# names it -- 0605-1999, 1604cf-2008, 2550m-2007, 2551m-2002 and 2553-1999's
# own item pairs, reproduced at the corpus's own scale. The signature bar
# spans the row's own full available width (between its left/right borders)
# rather than a narrower slice the way 2550M's own two-per-row rules do,
# because a narrower one would not, by itself, give the row's shared wall
# enough coverage to become a lattice boundary in a single-column fixture --
# on the real forms that coverage comes from OTHER columns in the same page-
# wide row, which a minimal fixture has no reason to reproduce. The straddle
# ownership test (`rule["y0"] <= cell["y1"] <= rule["y1"]`) and the caption
# match are exercised exactly the same either way.
#
# `SIGNATURE_RULE_CAPTION_TEXT` is its own constant, not a reuse of
# `SIGNATURE_LINE_CAPTION` above, so `prove_fixtures_fail.py`'s
# `mutate_signature_rule` can change it without touching the unrelated
# F212 fixture `signature_box()` builds. Mutated to 0605-1999's own real
# residue -- "Title/Position of Signatory", the identical geometry beside a
# caption that names no signature -- so the mutation is drawn from a real,
# already-measured refusal rather than invented.
SIGNATURE_RULE_ROW_WIDTH_PT = 260.0
SIGNATURE_RULE_ROW_HEIGHT_PT = 30.0
SIGNATURE_RULE_CAPTION_ROW_HEIGHT_PT = 20.0
SIGNATURE_RULE_ITEM_TEXT = "27"
SIGNATURE_RULE_CAPTION_TEXT = "Signature over Printed Name of Taxpayer"

# F226's own sliver-gap shape (`emit.SignatureRuleWriting`'s caption search,
# extended): the caption naming a rule-owning `label` cell's own vector line
# is not in the cell sharing its bottom wall -- it is one further row down,
# across a genuinely blank sliver cell 2316-2021's own item 53 measures at
# 1.32pt (`p1c324` -> `p1c326`'s own blank sliver -> `p1c327`'s caption).
# `SIGNATURE_RULE_GAP_SLIVER_HEIGHT_PT` is the sliver's own height, well
# under `rules.pdf`'s own measured `glyph_height_pt`: `prove_fixtures_fail.py`'s
# `mutate_signature_rule_gap` raises it past that metric (a real wall move,
# `COMB_BAND_REUNIFICATION_NOTCH_SIZE_PT`'s own precedent for mutating a
# fixture's geometry rather than a caption's own text) so the gap this class
# bridges becomes too tall to bridge, the fail-closed half of F226's own
# height-AND-ink guard.
SIGNATURE_RULE_GAP_ROW_WIDTH_PT = 260.0
SIGNATURE_RULE_GAP_ROW_HEIGHT_PT = 30.0
SIGNATURE_RULE_GAP_SLIVER_HEIGHT_PT = 3.0
SIGNATURE_RULE_GAP_CAPTION_ROW_HEIGHT_PT = 20.0
SIGNATURE_RULE_GAP_ITEM_TEXT = "53"
SIGNATURE_RULE_GAP_CAPTION_TEXT = (
    "Present Employer/Authorized Agent Signature over Printed Name")

# F227's shape (`emit.comb_writing_top_clear_of_printed_ink`): a caption's
# own descender genuinely hangs into a COMB's shared writing top -- 1604CF's
# "8 Telephone No." over its phone comb, reproduced at the corpus's own
# scale and with the corpus's own words. The comb's divider ticks are drawn
# confined to a band `INK_TRIM_COMB_BAND_TOP_INSET_PT` below the row's own
# top wall, never spanning the row's full height: a divider that reaches the
# top wall reads to the grid-building pass as a genuine COLUMN boundary and
# splits the row into `INK_TRIM_COMB_SLOTS` separate FIELD cells instead of
# one comb, measured directly against this fixture -- the mirror, on the
# divider side, of the same fact `comb_writing_rect`'s own docstring states
# on the writing-rectangle side ("A tick is a guide mark *under* the box,
# not the box").
#
# `INK_TRIM_CAPTION_GAP_PT` is the caption's own baseline, measured UP from
# the row's own top wall, at 0.0 drift (every built fixture): 1.0pt puts the
# 'p' of "Telephone" 0.24pt inside the comb's own writing top
# (`emit.comb_writing_top_clear_of_printed_ink`, measured directly against
# this fixture), the same order of magnitude 1604CF's own real defect
# measures (0.71pt) and comfortably clear of float noise.
# `INK_TRIM_CAPTION_DRIFT_PT` is its own constant, zero in every built
# fixture like every other DRIFT constant here: `prove_fixtures_fail.py`'s
# `mutate_ink_trim_comb` raises it enough to lift the caption's baseline
# clear of the comb's own writing top entirely, without moving the row's
# own walls, dividers or slot count.
INK_TRIM_COMB_ROW_WIDTH_PT = 200.0
INK_TRIM_COMB_ROW_HEIGHT_PT = 24.0
# 9 slots, not 7, since DECISION A: 200/7 = 28.6pt compartments were wider
# than the corpus's own 24.5pt character-box bound and the rule now refuses
# the band whole; 200/9 = 22.2pt is inside every real comb's range.
INK_TRIM_COMB_SLOTS = 9
INK_TRIM_COMB_BAND_TOP_INSET_PT = 14.0
INK_TRIM_CAPTION_TEXT = "Telephone No."
INK_TRIM_CAPTION_FONT_SIZE_PT = 9.0
INK_TRIM_CAPTION_GAP_PT = 1.0
INK_TRIM_CAPTION_DRIFT_PT = 0.0


def gray(value: float) -> tuple[float, float, float]:
    """A neutral RGB triple. to_gray() collapses it back to this exact value."""
    return (value, value, value)


# ---------------------------------------------------------------------------
# Deterministic output
# ---------------------------------------------------------------------------


def serialize(doc: fitz.Document) -> bytes:
    """Turn one built document into the exact bytes a fixture file holds.

    Clearing the metadata removes the creation and modification dates MuPDF
    would otherwise stamp, and `no_new_id` suppresses the /ID array, which is
    seeded from the clock. `garbage=4` also makes object numbering a function of
    the content rather than of the order the builder happened to allocate xrefs
    in, so an edit to one builder cannot renumber another fixture's objects.

    This exists as one function because it used to exist as two. `save()` wrote
    the files and `verify()` re-implemented the identical call, so `--verify`
    compared the committed bytes against bytes produced by a code path those
    bytes had not come from. Changing `no_new_id=True` to `False` in the writer
    alone left `--verify` reporting "identical and pinned" for all six fixtures
    while every real run emitted bytes that matched no pin. A checker that does
    not share the writer's code path is not checking the writer.
    """
    doc.set_metadata({})
    doc.del_xml_metadata()
    return doc.tobytes(garbage=4, deflate=True, no_new_id=True,
                       preserve_metadata=0)


def save(doc: fitz.Document, path: pathlib.Path) -> bytes:
    """Write one fixture to disk, and return exactly what was written."""
    payload = serialize(doc)
    path.write_bytes(payload)
    return payload


def new_document(pages: int = 1) -> tuple[fitz.Document, list[fitz.Page]]:
    """A blank fixture of `pages` folio sheets.

    The page handles are re-fetched after the last new_page(), because
    new_page() resets every outstanding page reference and drawing on a stale
    one raises rather than painting.
    """
    doc = fitz.open()
    for _ in range(pages):
        doc.new_page(width=PAGE_WIDTH_PT, height=PAGE_HEIGHT_PT)
    return doc, [doc[index] for index in range(pages)]


# ---------------------------------------------------------------------------
# Drawing helpers
# ---------------------------------------------------------------------------


def bar(page: fitz.Page, x0: float, y0: float, x1: float, y1: float,
        tone: float) -> None:
    """One filled axis-aligned bar, which is how the BIR generator draws ink."""
    page.draw_rect(fitz.Rect(x0, y0, x1, y1), color=None, fill=gray(tone),
                   width=0)


def split_run(start: float, end: float, pieces: int) -> list[tuple[float, float]]:
    """Cut a run into abutting pieces, the way a long border is really drawn.

    The pieces meet exactly, so extract.py's interval union has to join them on
    a zero gap rather than on its epsilon. Real joints are patched by corner
    squares, which `merged_box` adds on top.
    """
    step = (end - start) / pieces
    return [(round(start + i * step, 2), round(start + (i + 1) * step, 2))
            for i in range(pieces)]


def merged_box(page: fitz.Page, rect: fitz.Rect, thickness: float,
               tone: float = BLACK, pieces: int = 4) -> None:
    """A box border drawn as many short bars plus a square at each corner.

    This is how the BIR generator emits a long rule, and it is the reason
    extract_segments unions intervals at all: each edge arrives as several
    filled rects, and each corner square is thin on *both* axes so it is offered
    to the horizontal and the vertical grouping alike. The square must vanish
    into the runs it patches -- it is a duplicate contributor, not a rule.
    """
    for x0, x1 in split_run(rect.x0, rect.x1, pieces):
        bar(page, x0, rect.y0, x1, rect.y0 + thickness, tone)
        bar(page, x0, rect.y1 - thickness, x1, rect.y1, tone)
    for y0, y1 in split_run(rect.y0, rect.y1, pieces):
        bar(page, rect.x0, y0, rect.x0 + thickness, y1, tone)
        bar(page, rect.x1 - thickness, y0, rect.x1, y1, tone)
    for x in (rect.x0, rect.x1 - thickness):
        for y in (rect.y0, rect.y1 - thickness):
            bar(page, x, y, x + thickness, y + thickness, tone)


def comb_band(page: fitz.Page, rect: fitz.Rect, slots: int,
              group_after: int) -> None:
    """An enclosing box with equally spaced dividers and one heavier separator.

    A comb is where a wrong slot count puts a typed digit on top of a divider,
    so the fixture carries the two thicknesses a real comb mixes: hairline
    dividers between slots and a heavier bar between groups.
    """
    merged_box(page, rect, THICKNESSES_PT[1], BLACK, pieces=2)
    step = (rect.x1 - rect.x0) / slots
    for index in range(1, slots):
        x = round(rect.x0 + index * step, 2)
        thickness = THICKNESSES_PT[2] if index == group_after else THICKNESSES_PT[0]
        bar(page, x, rect.y0, x + thickness, rect.y1, BLACK)


def checkbox_square(page: fitz.Page, x0: float, y0: float, x1: float,
                    y1: float) -> None:
    """F210's checkbox square: four DECORATIVE rules around a KNOCKOUT fill.

    `x0,y0,x1,y1` is the KNOCKOUT interior -- the box a taxpayer marks -- not
    the frame. The frame is drawn OUTWARD from it, exactly the relationship
    the real corpus's 22 squares measure: each rule's own centre sits ON the
    knockout's edge (so the knockout is the frame's centreline rectangle,
    never independently placed), and each side's LENGTH runs half the frame's
    own thickness past the interior's corner so the two perpendicular bars
    close the corner rather than leaving a gap. `checkbox_square_boxes` in
    emit.py matches a candidate rule to a knockout fill on precisely this
    tolerance -- see its docstring.

    The knockout is offset by `CHECKBOX_SQUARE_KNOCKOUT_DRIFT_PT`, zero in
    every built fixture: prove_fixtures_fail.py's mutate_checkbox_square sets
    it non-zero to slide the fill off the frame's centreline without
    touching any rule's tone, so that mutation trips `checkbox-square` alone
    -- `check_tone`'s corpus-wide decorative census, which owns
    `mutate_tone`, never sees a changed gray value.
    """
    thickness = CHECKBOX_SQUARE_THICKNESS_PT
    half = thickness / 2.0
    tone = GREY_MID
    drift = CHECKBOX_SQUARE_KNOCKOUT_DRIFT_PT
    bar(page, x0 - half, y0 - half, x1 + half, y0 + half, tone)   # top
    bar(page, x0 - half, y1 - half, x1 + half, y1 + half, tone)   # bottom
    bar(page, x0 - half, y0 - half, x0 + half, y1 + half, tone)   # left
    bar(page, x1 - half, y0 - half, x1 + half, y1 + half, tone)   # right
    bar(page, x0 + drift, y0, x1 + drift, y1, WHITE)              # knockout


def signature_box(page: fitz.Page, x0: float, y0: float, x1: float,
                  y1: float) -> None:
    """F211/F212's signature box: a bordered box, a top-left caption confined
    to its own top band, and a SEPARATE caption directly below it naming
    "Signature over Printed Name...".

    `x0,y0,x1,y1` is the box itself, framed by `merged_box`. Its BOTTOM
    border is the one wall two things share, exactly the shape the real
    corpus's 54 signature boxes measure: 2551Q page 1's "For Individual:"
    box and the "Signature over Printed Name of Taxpayer..." caption below
    it are drawn against the identical rule, one cell's bottom and the
    next one's top. `emit.SignatureBoxWriting` reads the top-left caption
    (confined to the box's own top `emit.SIGNATURE_BOX_CAPTION_BAND`);
    `emit.SignatureLineBinding` reads the caption below it (bound to the
    box the same wall it sits under also bounds) -- the `BureauReservation`
    precedent, reversed.

    The in-box caption is set 12pt below the box's own top edge -- inside
    the pinned 40% band for any box taller than 30pt, this one included --
    and the caption below is set 12pt under the box's own bottom edge, both
    at the corpus's own modal 9pt body size.

    Each caption's own baseline carries its own drift
    (`SIGNATURE_BOX_CAPTION_DRIFT_PT` / `SIGNATURE_LINE_CAPTION_DRIFT_PT`),
    zero in every built fixture: prove_fixtures_fail.py's mutate_signature_box
    and mutate_signature_line set one non-zero at a time to slide a single
    caption without touching the box, any rule's tone, or the other caption
    -- so each mutation trips its own check alone.
    """
    merged_box(page, fitz.Rect(x0, y0, x1, y1), THICKNESSES_PT[1], BLACK)
    page.insert_text(
        fitz.Point(x0 + 6, y0 + 12 + SIGNATURE_BOX_CAPTION_DRIFT_PT),
        SIGNATURE_BOX_CAPTION,
        fontname="helv", fontsize=SIGNATURE_BOX_FONT_SIZE_PT)
    page.insert_text(
        fitz.Point(x0 + 6, y1 + 12 + SIGNATURE_LINE_CAPTION_DRIFT_PT),
        SIGNATURE_LINE_CAPTION,
        fontname="helv", fontsize=SIGNATURE_BOX_FONT_SIZE_PT)


def row_number_row(page: fitz.Page, x0: float, y0: float) -> None:
    """F151's row-number shape (P2): a bordered row split into a bare-numeral
    label cell and a blank field cell beside it.

    `x0,y0` anchors the row's own top-left corner. The row is one bordered
    box (`merged_box`, the same shape every other bordered region in this
    fixture uses) split by ONE vertical rule into two cells: a label cell
    `ROW_NUMBER_LABEL_WIDTH_PT` wide, holding only `ROW_NUMBER_TEXT`, and a
    field cell `ROW_NUMBER_FIELD_WIDTH_PT` wide, empty. lattice.py cuts
    those at the divider, the way it cuts any two-column table row; the
    numeral sits at the label cell's own left edge, matching the real
    corpus's own row-number columns (`p2c132`... on 1701-2018-conso).

    Row height is fixed at `ROW_HEIGHT_PT`, comfortably taller than one line
    of the fixture's own 9pt body face, so neither cell is a sliver.
    """
    x1 = x0 + ROW_NUMBER_LABEL_WIDTH_PT + ROW_NUMBER_FIELD_WIDTH_PT
    y1 = y0 + ROW_NUMBER_ROW_HEIGHT_PT
    merged_box(page, fitz.Rect(x0, y0, x1, y1), THICKNESSES_PT[1], BLACK,
              pieces=2)
    divider = x0 + ROW_NUMBER_LABEL_WIDTH_PT
    bar(page, divider, y0, divider + THICKNESSES_PT[1], y1, BLACK)
    page.insert_text(fitz.Point(x0 + 6, y0 + 14), ROW_NUMBER_TEXT,
                     fontname="helv", fontsize=ROW_NUMBER_FONT_SIZE_PT)


def comb_band_reunification_row(page: fitz.Page, x0: float, y0: float) -> None:
    """F064's shape (W3): a comb band whose row is genuinely non-rectangular
    only when something ELSE in the row also fails to reach a wall.

    `x0,y0` anchors a bordered row. The comb's own left rail is drawn only
    from `COMB_BAND_REUNIFICATION_RAIL_TOP_INSET_PT` down -- like
    1707-2021's own v255/v256, which continue an EXISTING border rather
    than hang free, so `split_verticals` classifies it as a border (a
    rail), never a hanging comb divider. With
    `COMB_BAND_REUNIFICATION_NOTCH_SIZE_PT` at 0 (every built fixture)
    nothing else is drawn left of the rail: the row's own component stays
    rectangular, `source_owned_comb_frame` owns it directly, and the comb
    resolves through the ordinary path. `prove_fixtures_fail.py`'s
    `mutate_comb_band_reunification` draws a small bordered notch there
    instead, entirely inside the row -- the row's own component becomes
    non-rectangular (a hole neither the notch's own borders nor the row's
    outer ones reach), F064's own `no-rectangular-owner` ledger state
    appears, and `lattice._reunify_comb_band` must correctly decline it:
    the notch's own two internal walls match none of the comb's own
    divider positions.
    """
    x1 = x0 + COMB_BAND_REUNIFICATION_ROW_WIDTH_PT
    y1 = y0 + COMB_BAND_REUNIFICATION_ROW_HEIGHT_PT
    thickness = THICKNESSES_PT[1]
    merged_box(page, fitz.Rect(x0, y0, x1, y1), thickness, BLACK, pieces=2)

    if COMB_BAND_REUNIFICATION_NOTCH_SIZE_PT > 0.0:
        notch = COMB_BAND_REUNIFICATION_NOTCH_SIZE_PT
        merged_box(
            page,
            fitz.Rect(x0 + 10.0, y0 + 4.0, x0 + 10.0 + notch,
                     y0 + 4.0 + notch * 0.6),
            thickness, BLACK, pieces=1)

    comb_x0 = x0 + COMB_BAND_REUNIFICATION_RAIL_INSET_PT
    step = (x1 - comb_x0) / COMB_BAND_REUNIFICATION_SLOTS
    band_y0 = y0 + COMB_BAND_REUNIFICATION_BAND_TOP_INSET_PT
    for index in range(1, COMB_BAND_REUNIFICATION_SLOTS):
        divider_x = comb_x0 + index * step
        bar(page, divider_x, band_y0, divider_x + THICKNESSES_PT[0], y1,
            BLACK)

    rail_y0 = y0 + COMB_BAND_REUNIFICATION_RAIL_TOP_INSET_PT
    bar(page, comb_x0, rail_y0, comb_x0 + thickness, y1, BLACK)


def signature_rule_row(page: fitz.Page, x0: float, y0: float) -> None:
    """F221 case 1's shape (`emit.SignatureRuleWriting`): a `label` cell's
    own vector-drawn signature line, straddling the wall it shares with a
    caption cell below.

    `x0,y0` anchors the upper cell's own top-left corner. Two abutting
    bordered cells, sharing one frame and one internal wall (drawn as plain
    bars, not `merged_box`'s four-sided box, because the wall a real form
    draws there is not the CELL's own reported border either --
    2550M's `p1c181` measures `border_count: 2`, not 4): the upper cell
    carries `SIGNATURE_RULE_ITEM_TEXT`, the item number that is its own
    only OTHER ink (what makes it `label`, not `blank`); the lower cell
    carries `SIGNATURE_RULE_CAPTION_TEXT`. Between them, straddling the
    wall, is the vector signature line itself -- the one piece of ink
    `emit.SignatureRuleWriting`'s own ownership test
    (`rule["y0"] <= cell["y1"] <= rule["y1"]`) reads, drawn the same way
    every rule in this fixture is (a filled bar, `bar()`), never as
    underscore glyphs (`RuledBlankWriting`'s own population, a different
    shape this class explicitly excludes).
    """
    x1 = x0 + SIGNATURE_RULE_ROW_WIDTH_PT
    wall_y = y0 + SIGNATURE_RULE_ROW_HEIGHT_PT
    caption_y1 = wall_y + SIGNATURE_RULE_CAPTION_ROW_HEIGHT_PT
    thickness = THICKNESSES_PT[1]
    half = thickness / 2.0
    bar(page, x0, y0, x0 + thickness, caption_y1, BLACK)
    bar(page, x1 - thickness, y0, x1, caption_y1, BLACK)
    bar(page, x0, y0, x1, y0 + thickness, BLACK)
    bar(page, x0, caption_y1 - thickness, x1, caption_y1, BLACK)
    page.insert_text(fitz.Point(x0 + 6, y0 + 14), SIGNATURE_RULE_ITEM_TEXT,
                     fontname="helv", fontsize=SIGNATURE_BOX_FONT_SIZE_PT)
    bar(page, x0 + thickness, wall_y - half, x1 - thickness, wall_y + half,
        BLACK)
    page.insert_text(fitz.Point(x0 + 10, wall_y + 12),
                     SIGNATURE_RULE_CAPTION_TEXT,
                     fontname="helv", fontsize=SIGNATURE_BOX_FONT_SIZE_PT)


def signature_rule_gap_row(page: fitz.Page, x0: float, y0: float) -> None:
    """F226's shape (`emit.SignatureRuleWriting`'s sliver-gap extension): a
    `label` cell's own vector-drawn signature line at its own bottom wall,
    with the caption naming it not directly adjacent -- one further row
    down, across a genuinely blank sliver cell no caption occupies.

    `x0,y0` anchors the upper (rule-owning) cell's own top-left corner.
    THREE stacked bordered cells sharing one frame: the upper cell carries
    `SIGNATURE_RULE_GAP_ITEM_TEXT`, the middle sliver carries no ink at all
    (`lattice.classify_cell`'s own `kind == "blank"`, exactly `p1c326`'s own
    measured fact), and the lower cell carries
    `SIGNATURE_RULE_GAP_CAPTION_TEXT`. The vector signature line straddles
    the wall between the upper cell and the sliver -- the rule-owner's own
    bottom wall, `emit.SignatureRuleWriting`'s own ownership test
    (`rule["y0"] <= cell["y1"] <= rule["y1"]`) -- and a second, ordinary
    wall (never a signature line, just a ROW boundary) separates the sliver
    from the caption cell below it, reproducing 2316-2021's own item 53
    (`p1c324`'s own rule h180 -> `p1c326`'s own 1.32pt blank sliver ->
    `p1c327`'s own caption) at this fixture's own scale.
    """
    x1 = x0 + SIGNATURE_RULE_GAP_ROW_WIDTH_PT
    wall_y = y0 + SIGNATURE_RULE_GAP_ROW_HEIGHT_PT
    sliver_y1 = wall_y + SIGNATURE_RULE_GAP_SLIVER_HEIGHT_PT
    caption_y1 = sliver_y1 + SIGNATURE_RULE_GAP_CAPTION_ROW_HEIGHT_PT
    thickness = THICKNESSES_PT[1]
    half = thickness / 2.0
    bar(page, x0, y0, x0 + thickness, caption_y1, BLACK)
    bar(page, x1 - thickness, y0, x1, caption_y1, BLACK)
    bar(page, x0, y0, x1, y0 + thickness, BLACK)
    # The sliver's own bottom wall -- an ordinary row boundary, not a
    # signature line -- separates the blank sliver from the caption cell.
    bar(page, x0, sliver_y1 - thickness, x1, sliver_y1, BLACK)
    bar(page, x0, caption_y1 - thickness, x1, caption_y1, BLACK)
    page.insert_text(fitz.Point(x0 + 6, y0 + 14), SIGNATURE_RULE_GAP_ITEM_TEXT,
                     fontname="helv", fontsize=SIGNATURE_BOX_FONT_SIZE_PT)
    bar(page, x0 + thickness, wall_y - half, x1 - thickness, wall_y + half,
        BLACK)
    page.insert_text(fitz.Point(x0 + 10, sliver_y1 + 12),
                     SIGNATURE_RULE_GAP_CAPTION_TEXT,
                     fontname="helv", fontsize=SIGNATURE_BOX_FONT_SIZE_PT)


def ink_trim_comb_row(page: fitz.Page, x0: float, y0: float) -> None:
    """F227's shape (`emit.comb_writing_top_clear_of_printed_ink`): a
    caption's own descender genuinely hangs into a comb's own shared
    writing top, and the comb -- unlike a plain field -- was never offered
    that evidence at all before this session.

    `x0,y0` anchors the comb's own top-left corner. One bordered box
    (`merged_box`, the row's own four walls), its divider ticks confined to
    a band `INK_TRIM_COMB_BAND_TOP_INSET_PT` below the row's own top wall
    rather than spanning the row's full height -- see the module comment
    above `INK_TRIM_COMB_ROW_WIDTH_PT` for why that confinement is what
    lets the row resolve as one comb instead of `INK_TRIM_COMB_SLOTS`
    separate field cells. `INK_TRIM_CAPTION_TEXT` is set above the row's
    own top wall, `INK_TRIM_CAPTION_GAP_PT` plus `INK_TRIM_CAPTION_DRIFT_PT`
    above it, so its own 'p' reaches -- or, once `prove_fixtures_fail.py`'s
    `mutate_ink_trim_comb` raises the drift, does not reach -- past the
    comb's own writing top the identical way 1604CF's "8 Telephone No."
    does over its real phone comb.
    """
    x1 = x0 + INK_TRIM_COMB_ROW_WIDTH_PT
    y1 = y0 + INK_TRIM_COMB_ROW_HEIGHT_PT
    thickness = THICKNESSES_PT[1]
    merged_box(page, fitz.Rect(x0, y0, x1, y1), thickness, BLACK, pieces=2)
    step = (x1 - x0) / INK_TRIM_COMB_SLOTS
    band_y0 = y0 + INK_TRIM_COMB_BAND_TOP_INSET_PT
    for index in range(1, INK_TRIM_COMB_SLOTS):
        divider_x = x0 + index * step
        bar(page, divider_x, band_y0, divider_x + THICKNESSES_PT[0], y1,
            BLACK)
    baseline = y0 - INK_TRIM_CAPTION_GAP_PT - INK_TRIM_CAPTION_DRIFT_PT
    page.insert_text(fitz.Point(x0 + 4, baseline), INK_TRIM_CAPTION_TEXT,
                     fontname="helv", fontsize=INK_TRIM_CAPTION_FONT_SIZE_PT)


def checkerboard(width: int, height: int, rgb: tuple[int, int, int],
                 alpha: Callable[[int, int], int]) -> fitz.Pixmap:
    """A tiny RGBA image. `alpha` decides the soft mask fitz will write."""
    samples = bytearray(width * height * 4)
    for y in range(height):
        for x in range(width):
            index = (y * width + x) * 4
            samples[index:index + 3] = bytes(rgb)
            samples[index + 3] = alpha(x, y)
    return fitz.Pixmap(fitz.csRGB, width, height, bytes(samples), True)


# ---------------------------------------------------------------------------
# The fixtures
# ---------------------------------------------------------------------------


def build_rules() -> fitz.Document:
    """Structure: merged runs, every thickness, both greys, a knockout, a comb.

    Stands in for 2551Q, which is the paper, determinism and comb reference and
    is almost entirely filled bars. The white-filled, black-stroked box is the
    fill+stroke case: one drawing, two paint ops, and the reconciliation in
    paint_order() has to give the interior a lower ordinal than its own border
    or the interior erases it.
    """
    doc, pages = new_document(2)
    first, second = pages

    merged_box(first, fitz.Rect(48, 60, 564, 180), THICKNESSES_PT[1], BLACK)
    merged_box(first, fitz.Rect(48, 200, 564, 260), THICKNESSES_PT[3], BLACK,
               pieces=6)
    merged_box(first, fitz.Rect(48, 280, 300, 330), THICKNESSES_PT[2], BLACK,
               pieces=3)
    # A hairline run, and the thinnest ink the corpus carries.
    for x0, x1 in split_run(48, 564, 8):
        bar(first, x0, 350, x1, 350 + THICKNESSES_PT[0], BLACK)

    # Decorative tone: near-invisible on paper, and never to be painted black.
    bar(first, 48, 380, 564, 380 + THICKNESSES_PT[1], GREY_LIGHT)
    bar(first, 48, 396, 564, 396 + THICKNESSES_PT[2], GREY_MID)
    # A tint band, which is an area fill rather than a rule: thick on both axes.
    first.draw_rect(fitz.Rect(48, 420, 564, 452), color=None,
                    fill=gray(GREY_LIGHT), width=0)

    # One drawing, two paint ops: white paper knocked out of the band above,
    # outlined in black. `fs` in get_drawings(), two entries in get_bboxlog().
    first.draw_rect(fitz.Rect(64, 428, 200, 446), color=gray(BLACK),
                    fill=gray(WHITE), width=THICKNESSES_PT[1])

    # F210's checkbox square: a closed box of four DECORATIVE rules around a
    # KNOCKOUT interior, drawn as four separate filled bars plus a fill --
    # never a single stroked rect -- because that is how the BIR generator
    # draws it (see checkbox_square()'s own docstring). Placed clear of the
    # tint band above (which ends at y452) and the comb below (which starts
    # at y480).
    checkbox_square(first, 500, 460, 511, 471)

    comb_band(first, fitz.Rect(48, 480, 480, 504), slots=12, group_after=4)

    # F211/F212's signature box: a bordered box with a top-left caption
    # (see signature_box()'s own docstring) and a separate caption below it
    # on the box's own bottom rule. Placed clear of the comb above (which
    # ends at y504).
    signature_box(first, 48, 560, 300, 600)

    # Page 2 exists so the paper assertion has more than one page to measure,
    # and carries drawings of its own so nothing on it is vacuously clean.
    merged_box(second, fitz.Rect(48, 60, 564, 140), THICKNESSES_PT[1], BLACK)
    bar(second, 48, 170, 564, 170 + THICKNESSES_PT[0], GREY_MID)
    second.draw_rect(fitz.Rect(48, 200, 300, 240), color=None,
                     fill=gray(GREY_LIGHT), width=0)

    # F151's row-number shape (P2): placed clear of the grey band above
    # (which ends at y240).
    row_number_row(second, 48, 280)

    # F064's comb-band-reunification shape (W3): placed clear of the
    # row-number row above (which ends at y304).
    comb_band_reunification_row(second, 48, 340)

    # F221 case 1's own vector-drawn signature line (`emit.
    # SignatureRuleWriting`): placed clear of the comb-band-reunification
    # row above (which ends at y380).
    signature_rule_row(second, 48, 400)

    # F227's own shape (`emit.comb_writing_top_clear_of_printed_ink`):
    # placed clear of the signature-rule row above (which ends at y450).
    ink_trim_comb_row(second, 48, 480)

    # F226's own sliver-gap shape (`emit.SignatureRuleWriting`'s extended
    # caption search): placed clear of the ink-trim comb above (which ends
    # at y504).
    signature_rule_gap_row(second, 48, 560)
    return doc


def right_triangle(page: fitz.Page, x: float, y: float, size: float,
                   shape: Any = None) -> None:
    """A solid right-pointing "write here" marker, filled and closed.

    0605 draws thirty of these. Forced through the rule classifier each one
    became three axis-aligned hairlines with its fill discarded, so a solid
    black arrow printed as a light grey open "F".
    """
    points = [fitz.Point(x, y), fitz.Point(x + size, y + size / 2),
              fitz.Point(x, y + size), fitz.Point(x, y)]
    if shape is None:
        page.draw_polyline(points, color=None, fill=gray(BLACK), width=0)
    else:
        shape.draw_polyline(points)


def build_paths() -> fitz.Document:
    """Non-rectilinear ink: filled triangles and filled Bezier marks.

    Stands in for 0605. The decimal points are four `c` ops each, and were
    dropped outright because only `re` and `l` ops ever reached either
    classifier. This fixture deliberately contains no filled rect at all, so
    the assertion that it carries no rule at the invented 0.24pt default is a
    real statement about the classifier rather than about the drawing.
    """
    doc, (page,) = new_document()
    for row in range(6):
        for column in range(5):
            right_triangle(page, 60 + column * 90, 80 + row * 40, 6)
    # Three markers share a path with a rect, which is why the triangle test is
    # on the subpath and not on the whole path's op census.
    for index in range(3):
        shape = page.new_shape()
        shape.draw_rect(fitz.Rect(60 + index * 90, 340, 100 + index * 90, 352))
        right_triangle(page, 60 + index * 90, 356, 6, shape=shape)
        shape.finish(color=None, fill=gray(BLACK), width=0)
        shape.commit()
    for index in range(10):
        page.draw_circle(fitz.Point(80 + index * 48, 420), 0.85,
                         color=None, fill=gray(BLACK), width=0)
    return doc


def build_masks() -> fitz.Document:
    """Two soft-masked placements: one partly transparent, one fully opaque.

    Stands in for 1604E, whose masked base streams are /Matte padding -- flat
    black that the mask removes -- so painting the base puts a black block over
    a printed label. The opaque one is 2316's case and is the converse the
    assertion needed: when a declared mask hides nothing, compositing it is a
    no-op and the painted digest legitimately equals the base image's.
    """
    doc, (page,) = new_document()
    partial = checkerboard(12, 8, (0, 0, 0),
                           lambda x, y: 255 if (x + y) % 2 == 0 else 0)
    opaque = checkerboard(12, 8, (217, 217, 217), lambda x, y: 255)
    page.insert_image(fitz.Rect(48, 60, 168, 140), pixmap=partial)
    page.insert_image(fitz.Rect(200, 60, 320, 140), pixmap=opaque)
    merged_box(page, fitz.Rect(48, 180, 320, 220), THICKNESSES_PT[1], BLACK)
    return doc


def flip_placement(doc: fitz.Document, page: fitz.Page,
                   box: fitz.Rect) -> None:
    """Rewrite the page's image placement as a vertical flip: negative `d`.

    Written by hand because insert_image() offers only whole-quadrant
    rotations, and rotate=180 negates `a` as well -- a different transform,
    which would let a consumer that handles only 180deg rotation pass.

    insert_image() appends its placement as a content stream of its own, so the
    one holding the `Do` can be replaced without touching the ink around it.
    Which stream that is gets located rather than assumed: rewriting the wrong
    one would erase a drawing instead of flipping the image.

    Factored out so prove_fixtures_fail.py can disable exactly this and nothing
    else, without restating the builder around it.
    """
    name = page.get_images(full=True)[0][7]
    placements = [xref for xref in page.get_contents()
                  if f"/{name} Do".encode("latin-1") in doc.xref_stream(xref)]
    if len(placements) != 1:
        raise SystemExit(f"expected one stream placing /{name}, "
                         f"found {len(placements)}")
    top = PAGE_HEIGHT_PT - box.y0
    doc.update_stream(
        placements[0],
        f"q {box.width} 0 0 -{box.height} {box.x0} {top} cm /{name} Do Q\n"
        .encode("latin-1"))


def build_flip() -> fitz.Document:
    """One placement whose matrix has a negative `d`: a vertical flip.

    Stands in for 2550M's seal, which printed upside-down with its rim lettering
    reading bottom-to-top because the IR carried only a bounding box, and a box
    cannot express a flip.
    """
    doc, (page,) = new_document()
    image = checkerboard(12, 8, (0, 0, 0),
                         lambda x, y: 255 if y < 4 else 96)
    box = fitz.Rect(48, 60, 168, 140)
    page.insert_image(box, pixmap=image)
    merged_box(page, fitz.Rect(48, 180, 320, 220), THICKNESSES_PT[1], BLACK)
    flip_placement(doc, page, box)
    return doc


def insert_unmappable_glyph(doc: fitz.Document, page: fitz.Page) -> None:
    """Draw one glyph the file states no Unicode meaning for.

    A Type3 font whose encoding difference names a glyph nothing knows, with no
    ToUnicode CMap. MuPDF's two text views then disagree: get_texttrace()
    reports U+FFFD with the glyph id, and get_text("rawdict") hands back the
    code byte 0xA7, which renders as a section sign and is indistinguishable
    from content. That is 2550M and 2553's defect, reproduced from scratch.

    Factored out so prove_fixtures_fail.py can disable exactly this and nothing
    else, without restating the builder around it.
    """
    proc = doc.get_new_xref()
    doc.update_object(proc, "<<>>")
    doc.update_stream(proc, b"600 0 0 0 600 600 d1\n60 60 480 480 re f\n")
    font = doc.get_new_xref()
    doc.update_object(font, f"""<</Type/Font/Subtype/Type3
/FontBBox[0 0 600 600]/FontMatrix[0.001 0 0 0.001 0 0]
/CharProcs<</{UNMAPPED_GLYPH_NAME} {proc} 0 R>>
/Encoding<</Type/Encoding/Differences[{UNMAPPED_CODE}/{UNMAPPED_GLYPH_NAME}]>>
/FirstChar {UNMAPPED_CODE}/LastChar {UNMAPPED_CODE}/Widths[600]
/Resources<<>>>>""")
    resources = doc.xref_get_key(page.xref, "Resources")
    doc.xref_set_key(int(resources[1].split()[0]), "Font/T3", f"{font} 0 R")

    stream = doc.get_new_xref()
    doc.update_object(stream, "<<>>")
    doc.update_stream(
        stream,
        b"BT /T3 9 Tf 48 %d Td <%02X> Tj ET\n"
        % (int(PAGE_HEIGHT_PT) - 120, UNMAPPED_CODE))
    existing = doc.xref_get_key(page.xref, "Contents")[1].strip("[] ")
    doc.xref_set_key(page.xref, "Contents", f"[{existing} {stream} 0 R]")


def build_glyphs() -> fitz.Document:
    """A glyph with no Unicode meaning, beside a question mark that has one.

    Stands in for 2550M and 2553. The literal "?" is the other half of the same
    assertion: both characters are what a mis-read symbolic glyph looks like
    when it lands as something readable, so extract.py corroborates every one of
    them against the source's own glyph log -- and a corroboration check needs a
    case that legitimately passes as well as one that must not.
    """
    doc, (page,) = new_document()
    page.insert_text(fitz.Point(48, 80), "WHAT IS THE RATE?", fontname="helv",
                     fontsize=9)
    page.insert_text(fitz.Point(48, 100), "AMOUNT OF PAYMENT", fontname="helv",
                     fontsize=9)
    merged_box(page, fitz.Rect(48, 140, 320, 180), THICKNESSES_PT[1], BLACK)
    insert_unmappable_glyph(doc, page)
    return doc


def build_lean() -> fitz.Document:
    """Twelve stroked separators that lean less than their own stroke width.

    Stands in for 2316. Each covers the same ink a bar would, and each has to
    stay in `rules` where lattice.py can find a box side. This fixture carries
    no curve and no steep segment, so the assertion that it gained no
    non-rectilinear path at all is meaningful: anything in `paths` here is a
    separator that got demoted.
    """
    doc, (page,) = new_document()
    merged_box(page, fitz.Rect(48, 60, 564, 260), THICKNESSES_PT[1], BLACK)
    for index in range(LEAN_COUNT):
        x = 80.0 + index * 40.0
        y = 80.0 + (index % 3) * 50.0
        page.draw_line(fitz.Point(x, y),
                       fitz.Point(x + LEAN_OFFSET_PT, y + LEAN_RUN_PT),
                       color=gray(BLACK), width=LEAN_STROKE_PT)
    return doc


# name -> (builder, what it is the only fixture to exercise)
BUILDERS: tuple[tuple[str, Callable[[], fitz.Document], str], ...] = (
    ("rules.pdf", build_rules,
     "paper, determinism, merged runs, four thicknesses, greys, knockout, "
     "fill+stroke, comb"),
    ("paths.pdf", build_paths, "filled triangles and Bezier marks"),
    ("masks.pdf", build_masks, "partial and fully opaque soft masks"),
    ("flip.pdf", build_flip, "a placement matrix with a negative d"),
    ("glyphs.pdf", build_glyphs, "an unmappable glyph and a real question mark"),
    ("lean.pdf", build_lean, "twelve bar-like leaning separators"),
)


def build_all(out_dir: pathlib.Path) -> dict[str, bytes]:
    out_dir.mkdir(parents=True, exist_ok=True)
    written: dict[str, bytes] = {}
    for name, builder, _why in BUILDERS:
        doc = builder()
        written[name] = save(doc, out_dir / name)
        doc.close()
    return written


def report(written: dict[str, bytes], stream: Any) -> None:
    total = sum(len(payload) for payload in written.values())
    for name, payload in written.items():
        print(f"  {name:<12} {len(payload):>7} bytes  "
              f"{hashlib.sha256(payload).hexdigest()}", file=stream)
    print(f"  {'total':<12} {total:>7} bytes over {len(written)} files",
          file=stream)


def extract_pins() -> dict[str, str]:
    """extract.py's fixture pin table, keyed by file name.

    Read back rather than duplicated here. Two spellings of one digest drift,
    and the drift would surface as an unexplained hash mismatch at extraction
    time instead of as the stale pin table it is.
    """
    sys.path.insert(0, str(FIXTURE_ROOT.parent))
    import extract  # noqa: E402 - the path has to be set up first

    return {relative: digest
            for relative, _revision, digest in extract.FIXTURE_FIXTURES.values()}


def verify(out_dir: pathlib.Path) -> int:
    """Rebuild every fixture in memory and compare it to the tracked bytes.

    A difference is a real finding either way round: either a builder changed
    and the tracked corpus is stale, or this MuPDF writes different bytes than
    the one that produced the tracked corpus. Both invalidate the sha256 pins in
    extract.py, so neither may be reported as a pass.

    The tracked bytes are also checked against those pins, which closes the
    loop: builder, file on disk and pin either all agree or this says which one
    does not.
    """
    pinned = extract_pins()
    failures: list[str] = []
    for name in sorted(set(pinned) - {name for name, _b, _w in BUILDERS}):
        failures.append(f"{name}: pinned in extract.py but no builder makes it")
    for name, builder, _why in BUILDERS:
        path = out_dir / name
        doc = builder()
        # serialize(), not a copy of it: this is the whole point of --verify.
        rebuilt = serialize(doc)
        doc.close()
        if not path.is_file():
            failures.append(f"{name}: not present under {out_dir}")
            continue
        tracked = path.read_bytes()
        digest = hashlib.sha256(tracked).hexdigest()
        if tracked != rebuilt:
            failures.append(
                f"{name}: tracked {len(tracked)} bytes "
                f"sha256 {digest[:16]} != "
                f"rebuilt {len(rebuilt)} bytes "
                f"sha256 {hashlib.sha256(rebuilt).hexdigest()[:16]}")
        elif pinned.get(name) != digest:
            failures.append(
                f"{name}: extract.py pins {(pinned.get(name) or 'nothing')[:16]}, "
                f"the tracked file hashes to {digest[:16]}")
        else:
            print(f"  {name:<12} identical and pinned ({len(tracked)} bytes)",
                  file=sys.stderr)
    print(f"built with PyMuPDF {fitz.VersionBind} / MuPDF {fitz.VersionFitz}",
          file=sys.stderr)
    for message in failures:
        print(f"    FAIL {message}", file=sys.stderr)
    print(f"verify: {'PASS' if not failures else f'{len(failures)} FAILURE(S)'}",
          file=sys.stderr)
    return 1 if failures else 0


def pins(out_dir: pathlib.Path) -> int:
    """Print the pin table extract.py's fixture profile holds, ready to paste."""
    for name, _builder, why in BUILDERS:
        path = out_dir / name
        if not path.is_file():
            print(f"# {name}: absent", file=sys.stdout)
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        code = f'FIXTURE-{name.removesuffix(".pdf").upper()}'
        print(f'    # {why}\n    "{code}": (\n        "{name}", "0001",\n'
              f'        "{digest}"),')
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out-dir", type=pathlib.Path, default=FIXTURE_ROOT,
                        help="Where the fixture PDFs are written or verified.")
    parser.add_argument("--verify", action="store_true",
                        help="Rebuild and compare against the tracked bytes "
                             "instead of overwriting them.")
    parser.add_argument("--pins", action="store_true",
                        help="Print the sha256 pin table for extract.py.")
    args = parser.parse_args(argv)

    if args.verify:
        return verify(args.out_dir)
    if args.pins:
        return pins(args.out_dir)
    written = build_all(args.out_dir)
    report(written, sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
