#!/usr/bin/env python3
"""Independent vector referee for printed comb compartments.

The two existing compartment measurements share too much context to decide
which one is wrong:

* lattice.py classifies extracted rules and builds the emitted slot geometry;
* audit.py asks MuPDF for drawing objects inside a lattice-owned cell.

This tool is deliberately a third implementation.  It invokes Poppler's
``pdftocairo -svg`` as a separate process, parses only the painted vector
geometry in that SVG with the Python standard library, and never imports
audit.py, extract.py, lattice.py, or PyMuPDF.

The layout supplies a *subject* (page and cell rectangle) and the already
recognised divider anchors.  It does not supply the answer.  Between adjacent
recognised anchors, or immediately beyond the anchored run, the referee admits
a missing boundary only when:

1. the source-space gap is an integral number of the measured base pitch;
2. Poppler paints every missing pitch position in one common source band; and
3. every already-recognised anchor is also painted in that band.

One fail-closed exception can prove that a lattice anchor is absent, but it is
restricted to an already-active unresolved ledger subject and can never
discover a new comb.  One partial-anchor source topology must occupy the
entire open band; every observed divider must map one-to-one to a declared
anchor; and every missing anchor must have an exact raw target-tone rail that
one supported, unclipped, non-target final owner exhaustively erases across
every open slab.  Clipped paint, unsupported geometry, mixed topmost owners,
or surviving target-tone ink closes the exception.  Subject ownership comes
only from the active ledger identity--this certificate does not claim an
independent source enclosure--and retained subjects remain ineligible.

An outward boundary must continue the measured source pitch, or be the sole
boundary that symmetrically divides the remaining edge interval.  Cell-edge
ink is never counted as an interior divider.  These constraints make the check
useful for both disputed heavy group separators and truncated first/last ticks
without turning unrelated verticals in a broad mixed cell into character
boxes.  Every other partial pattern, unsupported vector geometry, clipped
candidate, missing provenance, or competing source band is UNEVALUABLE --
never a pass.

A subject the lattice RETAINS -- published, emitting nothing, blocking the
gate -- is withdrawn from that adjudication, so the topology it leaves behind
must not be one the producer certified for itself; see THE RETAINED-TOPOLOGY
INVARIANT below.  The single exception is a suppression reason whose factual
claim about the paper this referee can re-derive from the same Poppler output,
and it is then re-derived rather than believed.

Raster output is not produced and cannot affect a verdict.

Examples:

    python3 tools/formgen/comb_referee.py --self-test
    python3 tools/formgen/comb_referee.py --only 1707 \
        --out build/comb-referee-1707.json
    python3 tools/formgen/comb_referee.py \
        --out build/comb-referee.json
"""

from __future__ import annotations

import argparse
import ast
import dataclasses
import hashlib
import html.parser
import importlib.machinery
import json
import math
import mimetypes
import os
import pathlib
import platform
import posixpath
import re
import signal
import shutil
import subprocess
import sys
import sysconfig
import tempfile
import urllib.parse
import xml.etree.ElementTree as ET
from collections.abc import Iterable, Sequence
from decimal import Decimal, InvalidOperation
from typing import Any


HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parent.parent


def _load_review_registry():
    """Load the reviewed-ledger registries by explicit pinned path.

    The gate invokes this referee with `-I` (isolated mode), which strips the
    script directory from sys.path, so a bare `import review_registry` works
    at a shell and dies inside the gate's child -- gate r69 failed exactly
    so.  Loading by file location is isolation-proof and says precisely which
    bytes are trusted: the module beside this file, nothing importable from
    anywhere else.
    """
    import importlib.util
    path = HERE / "review_registry.py"
    spec = importlib.util.spec_from_file_location("review_registry", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


review_registry = _load_review_registry()

REPORT_VERSION = 2
EXPECTED_FORMS = 53
# 4540 -> 4521 (r14, 1e4da29) -> 4538 (2026-08-07, this change). r14's move was
# WRONG, and the retraction is here rather than in a note because the number is
# the evidence. This pin is the LEDGER SUBJECT denominator: `validate_comb_ledger`
# compares it to `len(published_subjects)` and the form report compares it to
# `len(cells)`, and BOTH enumerate every subject the ledger publishes -- active
# and `retained_unresolved` alike. A comb that stops being a writing surface does
# not leave the ledger; that is the ledger's entire purpose ("a retained subject
# remains published even though no active cell is allowed to emit it"). r14
# measured comb CELLS, found 4,521, and subtracted the difference from a
# denominator that does not count cells, so 12 forms failed the referee outright
# and the corpus report has been partial at 40/53 ever since.
#
# Re-measured 2026-08-07 by running 21e0630^'s lattice.py over the unchanged
# build/ir for all 53 forms and diffing the ledgers against HEAD's: they are
# IDENTICAL, form for form -- 4,538 subjects, 4,521 active comb cells, 17
# retained. 21e0630's shaded-paper fix moved neither census. What was genuinely
# stale at r14 was exactly TWO subjects, both 1700-2018 (143 -> 141), and they
# were already stale before 21e0630. The other 17 "missing" subjects are the 17
# retained ones, which never moved at all.
#
# 4538 -> 4543 (2026-08-07, r20), re-measured over the regenerated layouts by
# counting every published subject -- active and retained alike -- exactly as
# `validate_comb_ledger` does. Six slugs move and no others: 0605 21 -> 19,
# 1600WP 16 -> 17, 1604CF 12 -> 15, 2550M 23 -> 21, 2551M 15 -> 18, 2553 16 ->
# 18. All six are extract.py's line-cap model reaching a rail it used to stop
# short of, which is a divider appearing or a band closing, and each moves the
# retained half of the census below with it.
#
# 4543 -> 4583 (2026-08-07, r21), re-measured the same way over the regenerated
# layouts. Nine slugs move and no others: 0605 19 -> 27, 1600WP 17 -> 23,
# 1701MS 136 -> 140, 2200A 42 -> 43, 2316 28 -> 31, 2550-DS 77 -> 79, 2550Q
# 144 -> 150, 2551M 18 -> 25, 2553 18 -> 21. All nine are `lattice.py`'s
# bottom-guide-tick recognition (see LATTICE_PRODUCER_SHA256 below): a comb
# guide the artwork deliberately stops a hair above its baseline is now a
# divider instead of an unsupported border, so groups that had lost their
# compartments (2316's TINs, 0605's return-period boxes, 1600WP's item 5)
# become combs again.
EXPECTED_COMBS = 4587
# The comparison vocabulary, in ONE place. It was duplicated -- inline in
# the per-form counts and again in the corpus aggregate -- and the second
# copy silently dropped `excepted`, so a corpus total under-reported an
# entire comparison kind while every per-form count was right. A vocabulary
# that exists twice is a vocabulary that will disagree with itself.
COMPARISON_NAMES = (
    "agree", "excepted", "repair-lattice", "repair-audit",
    "stale-generation", "stop", "unevaluable",
)
LATTICE_PRODUCER_FILE = "tools/formgen/lattice.py"
# Re-pinned 2026-08-07 (r14): `topmost_covering_fill` became
# `covering_shading_band`, so `on_shaded_paper` asks whether ONE connected,
# same-tone band of decorative fill still reaches SHADED_PAPER_MIN_COVERAGE of
# the cell after occlusion is resolved, instead of asking a single fill's own
# rectangle. Neither SHADED_PAPER_MAX_GRAY (0.87) nor
# SHADED_PAPER_MIN_COVERAGE (0.70) moved.
#
# Re-pinned 2026-08-07 (r20) for G03/G10: two producer bugs behind
# `printed_box_peers_all_fillable`. (1) `GroupGeometry.span` filtered coverage
# by distance to the cluster's mean centre and so could exclude a rule that is
# itself a member of that cluster -- `line_thickness_gray` already exempts a
# cluster's own rules and `span` now does the same, so a drawn wall counts as
# coverage of the line it defines. (2) `assign_points` placed a text run by its
# bounding-box centre, which is the run's ADVANCE; `glyph_ink_spans` reads the
# per-character origins the IR already carries, so a run whose home cell holds
# none of its ink moves to the cell holding the most of it, and a caption's
# inter-word gap no longer makes the checkbox drawn in it "printed text".
# CLUSTER_TOL_PT (0.3) did not move and no classification threshold moved.
# (3) `resolve_retained_partition_overlaps`: a suppressed subject's
# `mapped_partition_cell_ids` is a partition, and nothing enforced that. Once
# 2550M's `p1c7` lost its rectangular owner too, it and the row band `p1c6` that
# contains it both claimed the same three cells, and
# `validate_comb_owner_registry` correctly invalidated the whole form -- taking
# all 17 of its comb subjects to `source-topology-unevaluable`. The contested
# cell now goes to the smallest claiming area. Corpus-wide this contests 3 cells
# on one page of one form and empties no mapping.
#
# Re-pinned 2026-08-07 (r21) for G02d/G02e/G02g and G18. (1)
# `bottom_guide_tick_baseline`: a vertical supported at NEITHER end can still
# be a bottom guide tick -- official artwork stops a comb guide a hair above
# the baseline it visibly sits on (2316's first TIN group 0.25pt, 1600WP item
# 5 0.345pt), and `supported_at` filed those as borders, so the group lost its
# compartments and a TIN reached the taxpayer as one unbounded input (F111,
# F178, F181). The recognition is a PATTERN, not a widened tolerance -- a
# blanket y-tolerance was tried and refuted (it flipped 45 real combs): the
# tick must hang from nothing, land within its own stroke width of a baseline,
# sit between two full-height top-supported walls on that baseline, and be
# corroborated by same-loop siblings or uniform wall-to-wall pitch. No
# tolerance constant moved. (2) `comb_on_writing_surface` no longer restates
# the writing box into the contract's `y0`/`y1` -- those keys stay the SOURCE
# DIVIDER BAND this referee's `classify_band` seeds from and the reviewed
# 2551Q control was signed against; the writing box is published BESIDE the
# band as `writing_y0`/`writing_y1`/`writing_height_pt`. The restatement had
# made 4,417 of 4,522 active combs source-unevaluable and failed the reviewed
# control (G18).
#
# Re-pinned 2026-08-08 (r24) for G19/F184. (1)
# `erased_witness_rail_residue`: `definitely_erased` demands one known-later
# nonstructural layer covering the witness's COMPLETE bbox, but 2550M paints
# its Schedule date-box knockout only down to the middle of the row's bottom
# rule, so a sliver of the stale tick survives -- wholly inside the
# final-visible horizontal rail, where there is no paper the sliver could
# print on (the same no-paper contract `horizontal_rail_across` documents).
# The erasure certificate now accepts that one shape, publishes every excused
# interval as `rail_covered_residue_y`, and an un-covered un-railed portion
# still fails closed -- both directions mutation-proven in the self-test.
# (2) A competing endpoint topology proven ONLY inside a full-width
# horizontal rail exists on no paper, so it no longer de-certifies the
# paper-bearing topology; it stays published in the evidence with its new
# `paper_coverage_pt`, and a competitor with any paper coverage still forces
# `competing-endpoint-topologies` exactly as before. No tolerance constant
# moved and no threshold moved.
#
# Re-pinned 2026-08-08 (r27) for G05/G12: `printed_caption_refutes_comb`. A
# comb compartment is a CHARACTER cell, and `classify_cell` reached its "mixed"
# verdict from `has_comb` alone -- so a single stray vertical inside a printed
# caption block made the whole caption a typing surface (1606 page 2's entire
# statutory rate table, 566 x 106pt of Exempt / 1.5% / 3.0% / 5.0% / 6.0%, and
# one masthead on each of four excise forms). The cell's own printed text is
# now asked what it put IN the compartments: decoration in a character cell is
# at most one glyph per cell, because the cell is one character wide. The
# corpus separates the two populations with a gap and no tuned constant -- of
# 4,561 comb cells, 4,524 have at least one empty compartment, 26 carry exactly
# one glyph in every compartment (`I I 0 1 1`, `X C 0 1 0`, `0 %` -- the
# decoration the "mixed" verdict is FOR), and 11 carry 29 or more; nothing lies
# between 1 and 29. The test is EVERY compartment and never SOME, which is what
# keeps 2200A `p1c111`'s 29-box money comb (one swallowed caption in the first
# compartment, 28 empty ones after it) with its money boxes.
#
# What this referee adjudicates moves in the direction it is BUILT for, not
# around it: a refuted subject does not leave the ledger. It loses its comb,
# `cell_id` goes None, `emission` goes `suppressed`, `blocks_gate` stays True,
# `requires_independent_evidence` stays True, and `retired_proven_false` is
# among its permitted transitions for whoever reviews it. Nothing in lattice.py
# retires it -- a producer does not certify its own promotion -- so
# EXPECTED_COMBS and EXPECTED_COMBS_BY_SLUG, which count active + retained, do
# not move; a refuted band that no subject published raises rather than
# shipping; and a refuted subject that is not an identity mapping onto its own
# rectangle raises too. No tolerance constant moved and no threshold moved.
# Re-pinned 2026-08-08 (r29) for the comb RAIL derivation, which is a change
# to a derivation this referee does adjudicate, so it is stated in full.
# `comb_bands` no longer takes a comb's outer `slot_x` from the lattice cell
# box. It measures them: each side is the ink of the owner's own edge WHERE IT
# CROSSES THIS BAND (a lattice line sits at the mean centre of every collinear
# bar on the page, and the bar ruling one comb can be a third of a point from
# that mean -- 1800 fuses 584.26/584.50/584.74 into 584.56 over a comb the
# 584.26 bar rules), moved inward to the innermost boundary that crosses the
# owner's whole paper when the comb's tick run does not reach it (1801 rules a
# TIN dash box, a "Contact Number" caption and 366pt of item-24 caption inside
# the same rectangles as its digit boxes, and slot 0 was a typeable box over
# printed text). Two source facts decide it and no constant does: whether a
# boundary's ink crosses the paper the owner encloses, joined where the paper
# between fragments is thinner than the ink either side of it and never across
# a corridor a later layer painted out; and where that ink's own centre is.
#
# A rail moves the comb's edge INWARD off the fused mean and only inward: the
# rectangle is the paper the comb is emitted on, and `emitted_comb_evidence`
# in audit.py requires every emitted slot to stay inside it. Where the drawn
# bar's own centre falls outside the rectangle, the rectangle wins and the
# comb keeps the old edge -- which leaves SIX combs (1701MS p1c166/p1c173/
# p1c179/p1c187, 2550M p1c203, 2550Q p1c6) whose printed rail is 0.26-0.31pt
# beyond their owner's fused edge still reported by the audit. That residual
# is in the lattice POSITION, not in the comb, and it is not touched here.
#
# The census does NOT move: 4583 subjects, 33 retained, 4550 active comb cells,
# form for form. What moves is 773 combs' outer coordinates and 9 combs' slot
# counts, and the audit -- which reads the compartment count and the rail
# positions from the pinned PDF's own paint stream, never from the layout --
# agrees on all 4,531 compartment counts and on 4,422 of the 4,428 rail pairs
# it can evaluate, where before it named 33 of these combs as defects.
# `EXPECTED_HTML_STRUCTURE_SHA256` moves with the emitted slot rectangles,
# per form, below.
# Re-pinned 2026-08-09 (r33): lattice.py gained `partition_ink` /
# `printed_partitions`, which publish per non-comb cell the structural ink the
# source draws INSIDE it that no later opaque layer provably covers. A cell the
# sheet rules across is several writing regions, not one -- 1604CF p2 rules
# "ADDRESS OF PAYEES" off "* STATUS" the whole table long, and 2316/2550M/1800
# print bottom guide ticks inside date and TIN boxes. This is a derivation the
# referee adjudicates, so it is pinned here.
# Re-pinned 2026-08-09 (r35): `covering_shading_band` joins two strips of
# one tone across a seam of BARE paper too narrow to write a character in,
# bounded by the form's own measured `glyph_height_pt`. The source butts its
# shading strips together and leaves a seam -- 0619F p1c8 is 82% grey in two
# strips 0.48pt apart and scored 0.495, the upper strip alone -- so 328 cells
# the sheet shades to say NO ENTRY HERE were classified `field` and carried an
# input. A knockout still separates however thin it is: the bridge asks for
# the ABSENCE of paint, and a knockout is paint.
# Re-pinned 2026-08-10 (r37, P1) for F097: `bridge_knockout_bites` rejoins a
# lattice line's spans across a same-axis knockout strictly interior to one
# collinear rail -- 2200C p1's Date item had a 1.56pt white bite mid-height on
# both side rails, so the cell walk read the bite as a real doorway and MM/
# YYYY dissolved into blank slivers with no input at all (only DD was
# typeable). Bridged 8 gaps corpus-wide, all v-axis: 7 on 2200C p1, 1 on
# 2000-DST p1; 0 h-axis; every other form 0. Bounded by the form's own
# `min_fillable_line_metrics(ir)["glyph_height_pt"]`, same-axis and collinear
# only, and STRICT edge-to-edge coverage at epsilon 1e-6 (never
# CLUSTER_TOL_PT) -- three measured negative cases stay unbridged by
# construction: 2200A p1 x0=580.66 (a PERPENDICULAR knockout marking a
# deliberate comb-divider doorway), 1800-2018 p1 y=805.3 and 1604E-2018 p1
# y=383.6 (a knockout that only ABUTS a gap edge rather than covering it).
# EXPECTED_COMBS and EXPECTED_COMBS_BY_SLUG do NOT move: the two forms'
# subject totals are unchanged (2200C 40, 2000-DST 131), because the restored
# walls surface their divider ticks as ordinary `printed_partitions` on plain
# `field` cells, not as new registered comb ledger subjects -- 2200C p1c111/
# p1c112 land 2 and 4 `<input>`s (printed 2/4 = latticed = emitted) with no
# comb subject at all. One EXISTING subject's resolution moves: 2000-DST's
# p1c4 (`p1@164.30,109.94,248.69,131.06`, a legacy 6-slot single-character
# comb whose divider positions -- including x=192.38 -- were already read off
# the bottom guide ticks independently of this wall) had its cell split by
# the now-continuous rail, so its old bbox no longer names one rectangular
# cell; it moves `active_resolved` -> `retained_unresolved`,
# `emission-suppressed-no-rectangular-owner` / `painted-edge-partition`,
# `blocks_gate: true`, `requires_independent_evidence: true`. See
# EXPECTED_RETAINED_SUBJECTS_BY_SLUG below, which is the pin this moves.
# Moved 2026-08-10 (r41) by F201/P1b: `bridge_knockout_bites` now runs on the
# LEGACY lattice as well as the current one, on exactly the same evidence and the
# same 8 bites. A rail the source always drew, interrupted only by a white bite
# strictly inside it, is one wall in both readings -- so the healed boxes register
# comb subjects through the legacy discovery flow instead of emitting free-text
# regions. 2200C item 1 is now 8 character compartments (MM 2, DD 2, YYYY 4) and
# 2000-DST's gate-blocking retained subject p1@164.30,109.94,248.69,131.06 splits
# into two active_resolved subjects and RETIRES. Zero cells move corpus-wide.
# Moved 2026-08-11 (r43) by F208: a comb now has a HORIZONTAL writing surface.
# `comb_rails` reports each outer rail's own painted ink beside its position
# (`left_rail_ink`/`right_rail_ink`, measured from exactly the bars that
# established the position), and `comb_on_writing_surface` insets `slot_x`'s
# outer values to those ink edges as `writing_x0`/`writing_x1`/
# `writing_width_pt`, with `writing_x_rails` naming what derived each side.
# This is the exact twin of the vertical `writing_y0`/`writing_y1` and exists
# for the identical reason: `slot_x` runs rail CENTRE to rail centre, so every
# comb's outer compartments were laid across half of each printed wall's ink.
# `slot_x`, `divider_x`, the compartment counts and the pitch are UNTOUCHED, so
# no ledger topology digest moves and the census does not move (4,587 subjects,
# 33 retained, 4,554 comb cells, form for form). 4,546 of the 4,554 combs get a
# measured ink inset on both rails, 8 fail closed to `slot_x` on one or both
# sides and are counted by reason in `writing_x_rails`; 0 surrender to a
# degenerate width, and the narrowest resulting outer compartment corpus-wide is
# 6.12pt (1604CF p1c30). `EXPECTED_HTML_STRUCTURE_SHA256` moves on all 53, below.
#
# Re-pinned 2026-08-12 (W3, F064): `lattice.py` gains `_reunify_comb_band`,
# `comb_band_reunification_owner` and `_rail_local_span`. When the general
# cell walk cannot find one rectangular owner for a legacy comb subject, the
# new mechanism absorbs or trims exactly the current cells that subject's own
# rails and rows bound into one new cell -- never inventing a lattice
# position, never touching a cell whose own DSU component was already fully
# occupied (so a `source_owned_comb_frame` certificate or refusal is never
# second-guessed), never claiming a wall that is not one of the comb's own
# dividers, printed ink, or a cell an already-resolved comb owns. Reworking
# the general lattice walk instead (bounding a line's reach by the ink that
# induces it) was measured and refused: even at its most permissive tested
# bound it still fragments 1707-2021's own item 8A, still regresses an
# unrelated field on the same page, and moves 166-751 cells across 13-24 of
# the 53 forms depending on the bound -- not reviewable. Three documents
# move (see `EXPECTED_HTML_STRUCTURE_SHA256` and
# `EXPECTED_RETAINED_SUBJECTS_BY_SLUG`, both below); no lattice constant,
# tolerance or comb field moves, and `lattice.py --self-test` (2551Q) passes
# unchanged (489/264 slots).
#
# Re-pinned again the same day: the new cell was appended to `cells` --
# reading order (F209's own key, `(y0, x0)`) -- so it tabbed dead last on
# the page instead of where it prints. `tab_check.py` caught it directly
# (1707-2021 and 1707a-2021 both `red-order=25`, one press per slot); the
# new cell is now inserted at its own sorted position instead. Both forms
# re-pin below with a second byte change; 2551m-2002's cell already landed
# in its correct sorted position and its own bytes are unchanged.
# Re-pinned 2026-08-14 (C1a). Two changes, one relation: border records now
# carry their per-segment geometry (span extent, cross-axis ink band,
# thickness, tone), and a comb's writing surface insets by the weight of the
# NEAREST segment to each edge over the comb's OWN span -- the referee's own
# qualifying rule -- with equal separations resolving to the heavier segment
# and span overlap demanded beyond the coincidence tolerance. Measured on the
# regenerated corpus: exactly SIX writing bands move -- 2316 p1c74/76/78/80
# (-0.39pt: the row above's 0.84pt rule, fused into the boundary line 0.63pt
# short of this cell, no longer donates its weight) and 1604CF p1c16/p1c21
# (-0.24pt: the 0.96pt segment lies outside the comb span; the span-local
# wall is 0.72). A segment qualifies only where it spans one of the comb's
# own COMPARTMENT MIDPOINTS -- the same rays the referee measures on -- which
# also converges 1701-MS p1c117/120/123/126/129/132/135 (-0.30: the 0.5pt
# caption stretch spans no compartment midpoint; the 0.2pt wall over the
# compartments decides), 1706 p1c122 and 1800 p1c144/147/152/155/165 (-0.24:
# knockout-halved walls) -- NINETEEN bands across five forms in all, every
# one from the failing population, every delta equal to the referee's own
# midpoint measurement, and 2316 p1c40 (-0.39 bottom: the row below's 0.84
# rule, wholly outside the cell band, is no candidate under the referee's
# own overlap rule, now mirrored -- its old inset left 0.23pt of the true
# 0.45 wall's ink inside the writing band). TWENTY bands, five forms. A
# span-overlap tolerance plus referee span-end rays was tried first and
# REVERTED: the extra rays crossed shared-boundary junctions, refused 249
# cells, and moved the reviewed 2551Q control digest.
# Baseline parity was proven before any of it: stock code on this worktree's
# environment reproduces all 53 shipped pins byte-for-byte. 4,557 comb
# subjects unchanged; all 53 forms hold every audit assertion.
# Re-pinned again 2026-08-15 (C3-A step 1): lattice gained
# `apply_reviewed_transitions` -- the producer half of review_registry's
# doctrine -- proven a byte-identical no-op on the shipped EMPTY registry
# (fresh build_layout vs disk on 2551M/1604CF/2200A), with every fail-closed
# guard fixture-covered and neuter-proven. No layout byte moves until the
# user signs entries.
# Re-pinned 2026-08-15: lattice now recounts each page's comb-subject stats
# inside the reviewed-decision passes. The stats were computed while the page
# was built -- before any decision could apply -- so every page carrying one
# published a summary of the ledger as it stood a moment earlier, and this
# referee refused 27 of 53 forms on "ledger stat ... is N, expected N+1".
# Re-pinned 2026-08-16 (DECISION A): lattice gained the compartment rule --
# COMB_COMPARTMENT_MAX_PT = 24.5, census-derived, user-approved (Sitting 2).
# Three placements: the CURRENT band builder refuses a band with no run of
# character boxes; the same refusal at the subject layer routes a
# rule-refused LEGACY comb to retained (reason
# `emission-suppressed-compartment-rule`) instead of publishing it into a
# cell by continuity; and the legacy pass itself is deliberately untouched
# (28 of the 30 retained/composite subjects fail the rule -- that population
# IS the legacy detector's false-positive class, already adjudicated by
# reviewed transitions that refusing there would erase). Corpus effect:
# 0605's suppressed inference is never inferred; 2551M p2c13 and 1604CF
# p2c73 lose their 2-slot phantom combs and emit plain region-cut inputs;
# 1604F p1c25 carries its runs as published evidence, geometry unchanged.
# Re-pinned same-day: the routing narrowed (a rule-refused legacy comb is
# retained only when nothing CURRENT can own the cell -- 1600WP p1c74's
# regression) and the reunification/ink-trim fixture geometries moved under
# the bound. Same DECISION A lineage as the entry above.
# Re-pinned same-day again: the compartment-rule retention moved from the
# subject loop to a dedicated sweep AFTER the caption refutation, in its
# exact shape -- the loop-order version stole eleven caption-block
# subjects on eight forms and mismatched their reviewed certificates.
# Re-pinned 2026-08-18 (gol/tin-stage3): lattice admits thick horizontal
# table rails to the y-lattice and skips painted walls that would fuse
# into an existing y-line. No comb-referee constant other than this
# producer pin moves with it.
LATTICE_PRODUCER_SHA256 = (
    "f525554aeb81795c98314d87808160ac060e095441b1a7311b0cce6d278d060c"
)
AUDIT_PRODUCER_FILE = "tools/formgen/audit.py"
# Re-pinned 2026-08-07 (r18) for G10: audit.py gained two FIELD-LAYER
# assertions, `inputs_span_no_printed_divider` and
# `printed_box_peers_all_fillable`, taking ASSERTION_KEYS from 8 to 10. Neither
# reads `b.layout`, `b.plan`, emit.py's markers or the IR -- their whole
# expectation comes from the pinned PDF's own composited paint stream
# (`ordered_vector_paints`) and its own text operators (`drawn_glyph_boxes`) --
# so no derivation this referee adjudicates changed. No comb constant moved and
# no existing assertion's code path was touched.
#
# Re-pinned 2026-08-07 (r23): `money_boxes_have_inputs` and
# `printed_box_peers_all_fillable` now read the reservation the SHEET's own
# caption places on a blank -- "(To be filled up by the BIR)", "Machine
# Validation" -- derived in `source_bureau_reservations` from the pinned PDF's
# own text operators and from nothing else. Not from emit.py's
# `BureauReservation`, not from the IR, not from the layout: the two answer
# the same question about the paper through different producers, which is what
# lets this one still catch an emitter that reserves a box the sheet does not.
# Corpus-wide the exclusion claims exactly ONE box (0605 `p1c17`, finding
# F147's blocker), and every caller publishes the count as
# `boxes_bureau_reserved`, so it can never be silent. `ASSERTION_KEYS` is
# unchanged at 10, no comb constant moved, and `comb_slots_match_printed`'s
# code path -- the only derivation this referee adjudicates -- is untouched.
#
# Re-pinned 2026-08-08 (r27), two changes, neither of them in a derivation this
# referee adjudicates:
#
# (1) `glyph_boxes` scores an emitted input against the glyph's INK band rather
# than the font's LINE box. extract.py records a run's y-extent as MuPDF's span
# bbox, which is `baseline - ascender*size` to `baseline - descender*size`
# (verified on all 19,333 runs of this corpus), so every glyph in a run was
# charged with the full descender depth of its face whether or not the
# character has a descender -- and an input under a caption set in capitals was
# reported as sitting on printed text with blank paper between them. The lower
# edge alone moves, per GLYPH and never per run, and only for characters whose
# depth was MEASURED: `BASELINE_SEATED_INK` is an evidence list, an unmeasured
# character keeps the full line box, and so do symbol-encoded faces, rotated
# runs, and a run missing the metrics. `GLYPH_BASELINE_OVERSHOOT_EM` (0.0308,
# the deepest baseline-seated character over eleven measured faces) ENLARGES
# the band, so an error in it errs towards reporting a collision. Six mutations
# that weaken the band are each caught by a named fixture in the self-test.
# This feeds `inputs_over_printed_text` only. `comb_slots_match_printed` -- the
# one derivation this referee adjudicates -- takes its printed count from
# `printed_compartments`, i.e. from the source's own paints and text operators
# via `drawn_glyph_boxes`, and never from `glyph_boxes`; it is untouched.
#
# (2) The retained comb-subject registry admits a THIRD reason tuple,
# `emission-suppressed-caption-block-not-character-cells`, through exactly the
# identity branch the no-band tuple goes through. It is added because the shape
# now EXISTS (see LATTICE_PRODUCER_SHA256 above) and because an unrecognised
# retained record fails the whole form's registry rather than its own. Nothing
# is weakened: the record must still be suppressed, blocking, comb-less and an
# identity mapping onto its own still-present cell, and both directions are
# asserted in the self-test. `ASSERTION_KEYS` is unchanged at 10 and no comb
# constant moved.
#
# Re-pinned 2026-08-08 (r28), and unlike every re-pin above this one DOES land
# inside `comb_slots_match_printed`, the single derivation this referee
# adjudicates. Stated plainly rather than buried: `SourceSlotOracle` decides
# whether a compartment the emitter left without an input was already spent by
# the sheet, and both halves of that question moved.
#
#   * The rectangle the GLYPH question is asked of is now the compartment's
#     printed ROW -- the source's own dividers for walls, the cell's own top
#     and bottom for the other two edges -- instead of the writing rectangle
#     `emit.comb_writing_rect` chose. The old rectangle made the answer a
#     function of OUR typography: 85 of 92 identical money bullets read as
#     blank paper because the bullet's descent falls 0.03-0.41pt below the
#     writing floor. The row cannot reach outside the cell, and the self-test
#     drives a glyph above and below the cell as a neighbour's ink.
#   * The character-class test is gone. A compartment is one character wide,
#     the source has already put a character in it, and an input laid there is
#     a typing surface no taxpayer can use whatever the character means. What
#     protects C4 -- a money comb with no way to enter an amount -- is that
#     only an OCCUPIED compartment is ever excused, one compartment at a time;
#     audit.py's self-test drives an emitter that empties a whole comb still
#     failing on the compartments either side of the printed mark. The kind is
#     still published per compartment (`printed-constant` vs `printed-mark`),
#     so a report can still tell a statutory value from a separator.
#
# This referee does NOT re-derive that excuse. It re-derives the printed and
# layout topologies and then checks that audit.py's published relations agree
# with its own published `emission_state`. So this re-pin carries NO verdict on
# the oracle: the oracle is bound by audit.py's own mutation-proven self-test
# cases and by the corpus measurement recorded in STATUS.md r28.
# `ASSERTION_KEYS` is unchanged at 10, no comb constant moved, no tolerance
# moved, and the assertion's published SHAPE -- which
# `_normalise_outer_comb_assertion` is contract-bound to -- is unchanged.
# Re-pinned 2026-08-08 (r31): the source-topology reader gained six
# content-stream relations so `comb_slots_match_printed` can DECIDE cases it
# previously called unevaluable -- a junction block belongs to its rule chain,
# a break narrower than the stroke it interrupts is not a break, a painted
# knockout is a break at any size, a wall carries into the rule above where a
# divider does not, walls cut a frame into cells, and a touching junction is
# sampled on the rule's side. Checker-only: lattice.py and emit.py are
# untouched and regeneration is byte-identical, so no shipped geometry moved.
# `ASSERTION_KEYS` is unchanged at 10 and no tolerance moved -- POSITION_TOL_PT
# is still 0.25. Unevaluable fell 182 -> 19 and decided-and-failing rose 3 ->
# 33: the assertion now names producer defects it used to be unable to see.
#
# Re-pinned 2026-08-10 (F196/F199 blocker work): audit.py now publishes an
# APPLICATION RUNTIME CLOSURE -- the fitz/pymupdf package trees, every loaded
# application module bound to a file inside them, and the bundled native
# libraries `libmupdf.dylib`/`libmupdfcpp.so` that PyMuPDF loads through the
# dynamic linker and that no import-based inventory ever saw -- and derives
# its manifest's `attestation_complete`/`enforceable`/`complete` from a
# measured relation over that closure instead of from a constant False. This
# referee rehashes every one of those members from the installed package
# before accepting any of it; see `verify_published_closure`.
# Re-pinned 2026-08-10 (r42): audit.glyph_boxes now scores against the GLYPH'S
# OWN OUTLINE -- `glyph_ink_em`, published per run by extract.run_glyph_ink from
# the face MuPDF actually drew with -- on all four edges where it is derivable,
# keeping the advance box where it is not. 78.4% of the corpus's glyphs, with
# every one of the 76,991 fallbacks counted by reason. audit.py's own comment
# said a glyph's horizontal ink extent 'is not derivable here'; that was true of
# audit, and stopped being true of the pipeline when P3 built the outline reader
# for ruled blanks. Also fixes a latent defect in that reader: a face loaded from
# a buffer answers EVERY glyph query with its own font box (9,217 glyphs on 48 of
# 53 forms), which published would claim ink across blank paper.
# Re-pinned 2026-08-10 (r44) for F205: `_source_u_frame` now partitions a
# printed frame at a WALL the sheet erased just above the band. Where ink
# presence above the cut decides nothing -- an erased wall looks exactly like
# 2200-A's knocked-out DIVIDER -- the sheets separate the two on HOW THE
# STROKES ARE DRAWN, on two axes and never one: a wall is strictly thicker
# than the thinnest stroke standing between the same two rails AND stands
# strictly higher than every one of them. The comparison is refused outright
# unless the dividers themselves stand above the band, because with every
# divider flush to the band 'higher than every divider' collapses into 'above
# the band at all', which is the reading 2200-A refutes.
# Re-pinned 2026-08-11 (r43) for F208, and this one DOES land inside
# `comb_slots_match_printed`, the single derivation this referee adjudicates.
# Stated plainly rather than buried: the TARGET of the two outer-edge relations
# moved from the source rail's CENTRE to that rail's own INK EDGE, and the
# layout side of them moved from `slot_x`'s outer values to the layout's
# published `writing_x0`/`writing_x1`. Both are the same relation restated on
# quantities that mean the same thing -- an inner edge against an inner edge --
# and the ink edges were already published, unused, in `source_frame_geometry`.
# No tolerance moved: `POSITION_TOL_PT` is still 0.25 and
# `EMITTED_GEOMETRY_EPS_PT` still 0.0002. The internal-divider comparison, the
# printed compartment count and every other failure kind are untouched. A layout
# that publishes no writing edges is an OFFENDER, by name, never an excused
# comparison. Second change, same increment: `input_boxes` now reads a comb
# input's OWN declared inset instead of scoring its slot div, so a producer-side
# move inside a slot is visible to the judge in both directions; where the
# attribution is ambiguous it keeps the larger slot rectangle, which can only
# report more overlap and never less.
# Re-pinned 2026-08-12 (W5): `printed_compartments` gained three narrowly
# scoped extensions to what it can MEASURE, none touching this file's own
# proven rules. (1) A chromatic fill is refused only when its own `re`/`qu`
# items are not exactly rectilinear -- `_perceptual_luminance` (BT.601,
# published coefficients, not invented) supplies the missing scalar tone for
# any colour, and the exact regions are reconstructed and composited exactly
# as a grey fill's would be, scoped to one comb's own evaluation via a local
# `dataclasses.replace(page, ...)`, never touching the shared bundle-wide
# `page.paints`/`page.unsupported`. (2) A non-rectilinear (bezier/diagonal)
# stroke's own extrema-derived bounding rect -- already computed by pymupdf,
# unchanged -- decides whether it can BE a divider, mirroring the existing
# position-aware `text_hits` deferral; one that never straddles a divider the
# band's own rectilinear ink establishes no longer blocks. (3) When a
# candidate band's full painted extent (F064's own shared-multi-row-rule
# reading) yields no strict-majority topology at all, the SAME unmodified
# `_band_topologies` majority rule is retried against that band clipped to
# the claimed owner's own rectangle -- one further, narrower measurement of
# the same source ink, not a different rule. A fourth and fifth candidate
# extension (a `_dominant_certified_topology`-style containment fallback for
# competing topologies, and a multi-wall generalisation of
# `_frame_cut_at_source_walls`) were implemented, then refused and reverted:
# both passed their own target offenders but broke this file's own proven
# mutation-tested guards (a synthetic short, richer slab must stay competing;
# a spuriously reclassified wall must not let a claim silently resolve), so
# neither ships. `comb_slots_match_printed` moves from 10 forms/19 offenders
# to 9 forms/13 (1604-CF p1c13, 2550-M p1c13, 2553 p1c18/p1c20/p1c22/p1c24
# decided-agree; every other prior offender is either untouched or, for
# 1604-CF p2c73 and 2551-M p2c13, now diagnosed as the SAME U-frame-ownership
# problem the refused fifth extension would have addressed, not resolved).
# No comb constant or tolerance moved; every other assertion, corpus-wide, is
# unmoved (verified by a full 53-form `audit.py --assertions-only` re-run,
# form by form, assertion by assertion, against the pre-change baseline).
# Re-pinned 2026-08-13 (W8): `audit.py` gained `REVIEWED_COMB_TOPOLOGY`, a
# human-reviewed-topology registry consulted ONLY where `printed_compartments`
# has already raised `source-topology-unevaluable` for the exact subject, and
# never for a subject the audit can decide on its own evidence (a registry
# entry for a decidable subject is itself an ERROR, asserted by a new
# self-test check). SHIPPED EMPTY: with no entries, every lookup returns
# "no entry" and every branch that reads `printed`/`layout_relation` afterward
# is byte-for-byte the pre-change code path. `comb_slots_match_printed` stays
# **9 forms/13 offenders, unmoved**; `inputs_over_printed_text` **0/0,
# unmoved**; every other assertion, corpus-wide, unmoved (diffed form by form,
# assertion by assertion, against the pre-change tree); comb censuses
# **4,587/4,557/30, unmoved**; input count **45,548, unmoved**; tab-walk
# **53/53 green, unmoved**; blue census **5, unmoved**. No comb constant,
# tolerance, or existing assertion weakened.
# Re-pinned 2026-08-13 (Z1): `REVIEWED_COMB_TOPOLOGY` is populated with TEN
# reviewed comb-topology facts, each carrying its own source-PDF sha256, page,
# cell bbox, reviewer, date and the review panel it was read from. Three of the
# thirteen the user confirmed were WITHDRAWN before commit rather than pinned --
# 2200a p1c111, 2200c p1c107, 2200p p1c110 -- because measuring them showed the
# confirmation answered a different question than the registry asks: their slot
# 0 is 173.66pt wide against a 14.52pt pitch and holds the row's printed
# caption, and the sheet prints ticks inside that caption region at the same
# pitch, so the compartment count is genuinely open there (filed as F229).
# Measured on a clean 53-form audit: `comb_slots_match_printed` 9 forms/13
# offenders -> 3 forms/3, `decided_by_review` 10, and the three that remain are
# exactly the withdrawn trio, which also carry `invalid-emission` independently
# of topology. No producer bytes moved; `EXPECTED_HTML_STRUCTURE_SHA256` is
# unchanged.
# Re-pinned 2026-08-13 (Z2, F229). `comb_rails` gains a SECOND inward trim,
# `outer_paper_unguided`, for the case the wall trim cannot reach: where the
# sheet closes a caption off with nothing at all, the rail fell back to the
# lattice cell's nominal edge and published the caption as a compartment. The
# rail now moves to the outermost guide TICK when the paper between them holds
# more than two of the comb's own compartments AND carries none of its guide
# ink. Measured on the regenerated tree: exactly FOUR combs move -- 2200A
# p1c111, 2200C p1c107, 2200P p1c110 (29 -> 28, retiring the 173.66pt "box"
# over "27 Tax Debit Memo") and 1801 p1c13 (4 -> 3, the 183.05pt box over
# "5 Taxpayer Identification Number (TIN)"). Comb subjects 4,557 unchanged;
# compartments 39,475 -> 39,471; the <input> census is UNCHANGED, because
# slot 0 carried no input in any of the four. Exactly 4 emitted documents
# change, and their four structure pins move below.
#
# audit.py moves too, and it is DATA not logic: REVIEWED_COMB_TOPOLOGY gains
# the three 2200A/C/P entries at 28 compartments and 1801 p1c13 goes 4 -> 3,
# all four re-reviewed against the sheet's own ink and confirmed by the owner.
# Re-pinned 2026-08-15: the user-approved review bundle admitted the
# reviewed composite arrival to the judge (16 failing assertions -> 0, none
# newly broken). The referee's own validation of the same certificate is
# unchanged and independent -- it re-derives it against the review registry
# and against its own Poppler corroboration, so this pin records WHICH judge
# bytes were bound, never a delegation of judgement to them.
# Audit + 4 structure pins re-pinned 2026-08-15 (user-approved F235/F237
# package): PrintedDecoration in emit + the decoration rider in the judge.
# Flip census: EXACTLY 8 inputs corpus-wide (45,708 -> 45,700), the approved
# cells and nothing else.
# Re-pinned 2026-08-16 (DECISION A rider): audit.py's retained-reason
# vocabulary gained its fourth identity tuple,
# `emission-suppressed-compartment-rule` -- the user-approved judge-side
# acknowledgement of the compartment rule's two retained->composite
# subjects (2551M p2c13, 1604CF p2c73). Nothing else in the judge moved.
AUDIT_PRODUCER_SHA256 = (
    "4d5070c101101184bdd958c6eb68c58effdcd197a0fb61c5244ce4e2ee08497a"
)
AUDIT_DEPENDENCY_SHA256 = {
    # Re-pinned 2026-08-07 (r20): extract.py now models PDF 32000-1 8.4.3.3
    # line caps. A round (1) or projecting (2) cap inks half a stroke width past
    # the declared endpoint of an OPEN subpath, so the IR was publishing 340 of
    # this corpus's 569 open strokes short of their own ink. Caps are applied to
    # the two ends of a reconstructed subpath only -- never to `re`/`qu`, never
    # to a polyline that returns to its start, never to an interior join -- and
    # a new written-here probe page (`CAP_PROBE_STREAM`) asserts all thirteen
    # cases with a mutation that restores the old behaviour.
    #
    # Re-pinned 2026-08-10 (r37, P3): extract.py now publishes a run of three
    # or more underscore glyphs as the RULE it typographically is -- a ruled
    # blank is the sheet drawing a writing line with a text operator -- instead
    # of as a text run (review finding F200). 119 groups on 23 forms; 118
    # published, at the glyphs' OWN ink band, measured from the outline of the
    # face MuPDF drew them with and cross-checked against MuPDF's independent
    # bound for the same text op; 1 refused and left as text (1707-2021 p1,
    # whose face is unembedded Arial Narrow, which is not a name MuPDF's own
    # cleaner resolves, so nothing here can state that glyph's outline).
    # 22 documents move; 31 are byte-identical. The bars are offered into the
    # same interval union the drawn bars use, so an abutting rule of the same
    # tone would merge rather than print twice -- none does in this corpus.
    # No comb constant moves and no tolerance moves; three new self-test checks
    # (`ruled-blank-split`, `ruled-blank-floor`, `ruled-blank-fail-closed`)
    # take extract.py from 14 checks to 17 and 14+24 probes to 17+24.
    #
    # Re-pinned 2026-08-10 (r38, P4) for review findings F070 and F102: a
    # rawdict span is now cut at every baseline change (`baseline_groups`),
    # because a run in this IR states ONE baseline and MuPDF's line builder
    # does not guarantee one -- it groups glyphs that are merely close enough,
    # and the first glyph's baseline was then published for all of them.
    # 24 of the corpus's 19,333 ink-bearing spans carry two baselines, across
    # 11 forms. In 23 the odd baseline holds only a positioning space, which
    # now leaves the IR exactly as a whitespace-only span already did; in one
    # (1706 p2 `1 A`) both stretches ink and become two runs. Each published
    # run then sits on its OWN baseline: 1707-A p1's `Calendar` moves 3.72pt
    # down onto the `1 For` / `Fiscal` it is printed with, and 2200-P p2's
    # ` Total Tax-` 4.80pt down and clear of the column rule at x=508.30 that
    # used to bisect its `T`.
    # 11 documents move and 42 are byte-identical -- exactly the 11 the census
    # names, listed at EXPECTED_HTML_STRUCTURE_SHA256 below. The `<input>`
    # count does NOT move (45,494), EXPECTED_COMBS / EXPECTED_COMBS_BY_SLUG /
    # EXPECTED_RETAINED_SUBJECTS_BY_SLUG do NOT move (4,583 subjects, 34
    # retained, every per-slug count), no tolerance and no assertion moves,
    # and every assertion offender list is identical cell for cell. One new
    # self-test check (`baseline-split`), with its own written-here probe page
    # and a mutation that re-reads that page with the split disabled, takes
    # extract.py from 17 checks to 18 and 17+24 probes to 18+24.
    #
    # Re-pinned 2026-08-11 (T5c, F148/F149): every rule now carries an
    # explicit `origin` (`RULE_ORIGIN_VECTOR` or `RULE_ORIGIN_TEXT_UNDERSCORE`)
    # so a `label` cell's writing-line rule can be told apart from an ordinary
    # printed one downstream. `merge_intervals` takes a 4th element per
    # interval (the origin) and returns a 6-tuple, not a 5-tuple; a merged
    # segment's own origin is `RULE_ORIGIN_TEXT_UNDERSCORE` only when EVERY
    # contributor is -- a vector fragment abutting an underscore bar on the
    # same band is one stroke on paper and is reported as vector-origin,
    # never guessed to be a writing line because part of it is. One new
    # written-here probe page (`rule_origin_probe_ir`) and one new self-test
    # check (`rule-origin`), with its own source-level mutation in
    # `fixtures/prove_fixtures_fail.py`, take extract.py from 20 checks to 21
    # and 20+24 probes to 21+24. `SCHEMA_VERSION` does NOT move (a key ADDED
    # to a structure, per its own comment) and no existing rule field, comb
    # census or tolerance moves.
    #
    # Re-pinned 2026-08-11 (T5a, F210): a new self-test check
    # (`checkbox-square`) pins the geometric fact `emit.checkbox_square_boxes`
    # depends on -- a KNOCKOUT fill sitting on its own frame rules' centreline,
    # within their own half-thickness -- deliberately leaving role/tone to
    # `check_tone`'s existing corpus-wide scan, so the two checks cannot stand
    # in for each other. `SELF_TEST_FIXTURES` gains a seventh real PDF, "1701"
    # (already used elsewhere in this pipeline; no new untracked dependency),
    # naming 1701 page 2's Part V Schedule-1 "Taxpayer" square as
    # `SELF_TEST_CHECKBOX_SQUARE`; the synthetic corpus gains a matching shape
    # in `fixtures/rules.pdf` (`make_fixtures.checkbox_square`, re-pinned in
    # `FIXTURE_FIXTURES`) with its own source-level mutation in
    # `fixtures/prove_fixtures_fail.py`. extract.py goes from 21 checks to 22
    # and 21+24 probes to 22+24, over 7 pinned PDFs instead of 6. No existing
    # rule field, comb census or tolerance moves.
    #
    # Re-pinned 2026-08-11 (T5b+T5d, F211/F212), for the identical reason as
    # T5a's own entry above: two new self-test checks (`signature-box`,
    # `signature-line`) pin the geometric facts `emit.SignatureBoxWriting`
    # and `emit.SignatureLineBinding` depend on -- a caption confined to a
    # box's own top 40%, and a caption sitting below the wall the box above
    # it also bounds -- against 2551Q page 1's "For Individual:" box and its
    # own caption below it (no new untracked PDF; already `SELF_TEST_
    # FIXTURES["2551Q"]`). `fixtures/rules.pdf` gains a matching shape
    # (`make_fixtures.signature_box`, re-pinned in `FIXTURE_FIXTURES`) with
    # its own two source-level mutations in `fixtures/prove_fixtures_fail.py`.
    # extract.py goes from 22 checks to 24 and 22+24 probes to 24+24, still
    # over 7 pinned PDFs. No existing rule field, comb census or tolerance
    # moves; `build/ir/*.ir.json` is byte-identical to a re-extraction under
    # this pin (verified directly on 2551Q, this pin's own subject).
    #
    # Re-pinned 2026-08-12 (W2, F151), and for a different reason than every
    # entry above: extract.py's own CHECKS do not move -- row-number (F151's
    # Schedule D half, P2's measured rule) is entirely a `lattice.py`/
    # `emit.py` decision with no new extract-level primitive, so `extract.
    # SELF_TEST_CHECKS` stays at 24 checks and `prove_fixtures_fail.py`'s own
    # CASES/CONTRACT_ONLY accounting is untouched (see `prove_row_number`,
    # deliberately run outside it). What moved this file's own bytes is
    # `FIXTURE_FIXTURES["FIXTURE-RULES"]`'s sha256: `fixtures/rules.pdf`
    # gained a new shape (`make_fixtures.row_number_row`, a bordered row
    # split into a bare-numeral label cell and a blank field cell) so the
    # row-number rule has a source-level mutation to fail, the same "mutate
    # the source PDF, rebuild, observe" method every other case in this
    # corpus uses. No rule field, comb census, extract.py check count or
    # tolerance moves; `build/ir/*.ir.json` for the seven REAL pinned PDFs is
    # untouched (only the synthetic FIXTURE-RULES pin moved).
    #
    # Re-pinned 2026-08-12 (W3, F064), for the identical reason as the
    # row-number entry above: comb-band-reunification is entirely a
    # `lattice.py` decision with no new extract-level primitive, so
    # `extract.SELF_TEST_CHECKS` stays at 24 checks and
    # `prove_fixtures_fail.py`'s own CASES/CONTRACT_ONLY accounting is
    # untouched (see `prove_comb_band_reunification`, deliberately run
    # outside it). What moved this file's own bytes is
    # `FIXTURE_FIXTURES["FIXTURE-RULES"]`'s sha256:
    # `fixtures/rules.pdf` gained a new shape
    # (`make_fixtures.comb_band_reunification_row`, a bordered row whose
    # comb's own rail is drawn only from mid-row down) so the mechanism has
    # a source-level mutation to fail. No rule field, comb census,
    # extract.py check count or tolerance moves; `build/ir/*.ir.json` for
    # the seven real pinned PDFs is untouched (only the synthetic
    # FIXTURE-RULES pin moved).
    #
    # Re-pinned 2026-08-12 (W4b, F221 case 1), for the identical reason as
    # the two entries above: `emit.SignatureRuleWriting` is entirely a
    # `lattice.py`/`emit.py` decision with no new extract-level primitive,
    # so `extract.SELF_TEST_CHECKS` stays at 24 checks and
    # `prove_fixtures_fail.py`'s own CASES/CONTRACT_ONLY accounting is
    # untouched (see `prove_signature_rule`, deliberately run outside it).
    # What moved this file's own bytes is BOTH `FIXTURE_FIXTURES
    # ["FIXTURE-RULES"]`'s sha256 (`fixtures/rules.pdf` gained a new shape,
    # `make_fixtures.signature_rule_row`, a bordered `label` cell ruling a
    # vector signature line across its own bottom wall above a caption cell
    # naming it) and this comment recording that cause -- both part of the
    # whole-file bytes the sha256 below is taken over. No rule field, comb
    # census, extract.py check count or tolerance moves; `build/ir/
    # *.ir.json` for the seven real pinned PDFs is untouched (only the
    # synthetic FIXTURE-RULES pin moved).
    # Re-pinned 2026-08-13 (W6, F227), for the identical reason as the three
    # entries above: `emit.comb_writing_top_clear_of_printed_ink` and
    # `emit.PrePrintedInk.intrusions`'s own per-glyph precision are entirely
    # a `lattice.py`/`emit.py` decision with no new extract-level primitive,
    # so `extract.SELF_TEST_CHECKS` stays at 24 checks and
    # `prove_fixtures_fail.py`'s own CASES/CONTRACT_ONLY accounting is
    # untouched (see `prove_ink_trim_comb`, deliberately run outside it).
    # What moved this file's own bytes is BOTH `FIXTURE_FIXTURES
    # ["FIXTURE-RULES"]`'s sha256 (`fixtures/rules.pdf` gained a new shape,
    # `make_fixtures.ink_trim_comb_row`, a caption's own descender genuinely
    # hanging into a comb's shared writing top) and this comment recording
    # that cause -- both part of the whole-file bytes the sha256 below is
    # taken over. No rule field, comb census, extract.py check count or
    # tolerance moves; `build/ir/*.ir.json` for the seven real pinned PDFs
    # is untouched (only the synthetic FIXTURE-RULES pin moved).
    #
    # Re-pinned 2026-08-13 (W9, F226): `FIXTURE_FIXTURES["FIXTURE-RULES"]`'s
    # sha256 moved again -- `fixtures/rules.pdf` gained one more new shape,
    # `make_fixtures.signature_rule_gap_row` (a rule-owning `label` cell's
    # caption sitting one row down, across a genuinely blank sliver cell,
    # `emit.SignatureRuleWriting`'s own new sliver-gap extension) -- and
    # this comment recording that cause is again part of the whole-file
    # bytes below. No rule field, comb census, extract.py check count or
    # tolerance moves; `build/ir/*.ir.json` for the seven real pinned PDFs
    # is untouched.
    #
    # Re-pinned 2026-08-13 (Z3, F065): two independent fixes to
    # `substitutable_faces`/`glyph_ink_box`. (1) A face embedded under a
    # PDF-spec subset-tagged `/BaseFont` (`SUBSET_TAG_RE`, six uppercase
    # letters then '+') is now ALSO registered under its tag-stripped name,
    # additive, never displacing an exact-key hit -- MuPDF's own rawdict
    # strips that tag from `span["font"]` before this module ever sees it,
    # so a face registered only under its exact `/BaseFont` was invisible to
    # every span asking for it the stripped way. Corpus-wide this resolves
    # 61,781 of the corpus's 62,010 "no face is resolvable for this font"
    # glyphs (229 unembedded Tahoma remain, this corpus's one genuinely
    # unresolvable face). (2) New function `embedded_glyph_outline`
    # hand-parses one glyph's own outline from an embedded TrueType
    # program's `head`/`loca`/`glyf`/`hmtx` bytes (mirroring `fonts.py`'s
    # own WOFF2 table-directory reading), because every embedded,
    # buffer-loaded face answers `glyph_bbox` with its own whole font box,
    # never a real per-glyph outline -- fed into `ruled_blank_bars` ONLY,
    # cross-checked against the file's own stated advance. Together these
    # close F065: 1707-2021's item 9 ruled blank -- the corpus's one ruled-
    # blank refusal -- now publishes; `ruled_blank_groups`/`published`/
    # `refused` moves 119/118/1 -> 119/119/0 corpus-wide. Two new self-test
    # checks (`ruled-blank-embedded-subset`, a new written-here probe page
    # proving both the golden path and a genuinely unparsable embedded
    # program) take extract.py from 24 checks to 25 and 24+24 probes to
    # 25+24; `PAINT_SPAN_CONTRACT_CASES` (24 cases, unmoved) is pulled out
    # to a module constant so that count stays independent of
    # `len(SELF_TEST_CHECKS)`, an incidental coupling the 25th check would
    # otherwise have broken. No comb constant, tolerance, or existing check
    # count moves. `fixtures/prove_fixtures_fail.py` gains two source-level
    # mutations against the new check (21 total, up from 19).
    # Re-pinned 2026-08-17: extract's FIXTURE-RULES sha moved because the
    # fixture combs were rescaled under DECISION A's 24.5pt bound and the
    # tracked rules.pdf was regenerated (CI's byte-verify caught the gap).
    # No extract check, count or tolerance moved.
    "tools/formgen/extract.py": (
        "4c72c5f9787a1ee693ed7b967e47b58de0cd8ecc21684164cefcb6105583ba1a"
    ),
    "tools/formgen/verify.py": (
        "8dbeb222c9f04c8c71cf6ccf58acb519631e8e94966128fcdca9a56d097bad44"
    ),
}
AUDIT_INPUT_ROLES = frozenset({
    "ir", "layout", "html", "guide", "guide_html", "source_pdf",
})
AUDIT_ROUNDTRIP_LAUNCH_ARGS = [
    "--disable-background-networking",
    "--disable-component-update",
    "--disable-default-apps",
    "--disable-sync",
    "--metrics-recording-only",
    "--no-first-run",
]
AUDIT_ROUNDTRIP_SCOPE = (
    "playwright-package-tree-and-explicit-chromium-executable"
)
AUDIT_CANDIDATE_MATERIALIZATION = (
    "private-0700-o_excl-o_nofollow-fsynced-unlinked-read-fd"
)
AUDIT_PDF_NORMALIZATION_REPLACEMENT = "D:19700101000000+00'00'"
POPPLER_IDENTITY_TIMEOUT_SECONDS = 10.0
POPPLER_PAGE_TIMEOUT_SECONDS = 60.0
SUBPROCESS_CLEANUP_POLICY = "kill-isolated-process-group"
AUDIT_POSITION_FIELDS = {
    "emission_layout_position": (
        "emission-layout-position-mismatch", False),
    "emission_layout_outer_position": (
        "emission-layout-outer-position-mismatch", True),
    "emission_source_position": (
        "emission-source-position-mismatch", False),
    "emission_source_outer_position": (
        "emission-source-outer-position-mismatch", True),
    "layout_source_outer_position": (
        "layout-source-outer-position-mismatch", True),
}
AUDIT_FAILURE_KINDS = frozenset({
    "source-topology-unevaluable",
    "layout-printed-mismatch",
    "duplicate-layout-subject",
    "emission-container-page-mismatch",
    "emission-container-geometry-mismatch",
    "emission-layout-position-mismatch",
    "emission-layout-outer-position-mismatch",
    "emission-source-position-mismatch",
    "emission-source-outer-position-mismatch",
    "layout-source-outer-position-mismatch",
    "invalid-emission",
    "emission-layout-mismatch",
    "emission-printed-mismatch",
    "unexpected-emitted-comb",
    "emitted-cell-binding-invalid",
    "duplicate-emitted-cell-id",
    "missing-layout-cell-owner",
    "duplicate-layout-cell-owner",
    "emitted-cell-page-mismatch",
    "emitted-cell-geometry-mismatch",
    "unowned-live-comb-markup",
    "comb-inventory-mismatch",
    "comb-owner-registry-invalid",
})
AUDIT_OWNER_CERTIFICATE_CRITERION = (
    "exact-reviewed-layout-comb-subject-owner-v1"
)
ACTIVE_PARTIAL_ANCHOR_CRITERION = (
    "active-full-band-partial-anchor-source-topology-v1"
)
AUDIT_OWNER_CERTIFICATE_VALID_KEYS = frozenset({
    "criterion", "valid", "layout_sha256", "page", "cell_id",
    "legacy_cell_id", "subject_key", "legacy_bbox",
    "bbox_number_format", "state", "supplies_topology",
})
AUDIT_OWNER_CERTIFICATE_INVALID_KEYS = frozenset({
    "criterion", "valid", "reason", "supplies_topology",
})
LATTICE_GENERATOR_KEYS = frozenset({
    "producer",
    "schema_version",
    "consumes_ir_schema_version",
    "cluster_tolerance_pt",
    "pitch_tolerance_pt",
})
LATTICE_GENERATOR_CONTRACT = {
    "producer": LATTICE_PRODUCER_FILE,
    "schema_version": 1,
    "consumes_ir_schema_version": 2,
    "cluster_tolerance_pt": 0.3,
    "pitch_tolerance_pt": 0.3,
}
COMB_SUBJECT_STATES = frozenset({
    "active_resolved",
    "active_unresolved",
    "active_composite",
    "retained_unresolved",
})
COMB_INFERENCE_STATE = "suppressed_unreviewed_inference"

# THE RETAINED-TOPOLOGY INVARIANT, stated once because two different failures
# hide behind the same word "retained".
#
#   A subject the producer has WITHDRAWN FROM ADJUDICATION may not leave behind
#   a topology that same producer has certified.
#
# A retained subject emits nothing, so no emitted-slot assertion can reach it;
# `comparison` returns `unevaluable` for it, so the four-way agreement is never
# taken; and `transition_decision` says only that an explicit ledger transition
# is required.  The one thing it does leave in the ledger is `legacy_comb` --
# and that is precisely the shape a later transition would be promoted from.
# Letting a producer publish `resolution.status == "resolved"` there is letting
# it bank a self-certified shape for a promotion nobody adjudicated, which is
# the exact mechanism GOAL.md's decision 1 forbids.
#
# Until r27 every retained subject published a LEGACY-CONTINUITY band, which
# `lattice.legacy_comb_bands` marks unresolved by construction, so "a retained
# legacy_comb is unresolved" was true of every shape that existed and the guard
# never had to distinguish anything.  r27 created a second shape: a cell whose
# own printed text refutes the claim that its compartments are character cells
# keeps the comb the lattice actually measured on it, and that comb may well be
# RESOLVED -- we know exactly what the source drew; we are declining to emit it
# as a typing surface.  The topology status answers "could the source shape be
# determined?"; the suppression reason answers "may it be emitted?".  They are
# different questions and the guard was reading one for the other.
#
# The producer is NOT taken at its word about which of the two it is in.  A
# retained subject may publish a resolved topology only when its reason tuple
# is one this referee can RE-DERIVE FROM POPPLER (below), and the re-derivation
# then runs against the pinned PDF's own vector output.  An unrecognised reason
# with a resolved topology still fails, exactly as before.
CHARACTER_CELL_MAX_PRINTED_GLYPHS = 1
SOURCE_CAPTION_BLOCK_CRITERION = (
    "source-printed-caption-block-not-character-cells-v1"
)
# The corpus's structural-tone bound: rule tones at or below this are drawn
# structure (the quantised corpus tones are 0.0, 0.251, ...; 0.15 separates
# black structure from every decorative grey), the same boundary audit.py's
# tone_role uses.  Used only by the null-border absence check: a claim of "no
# wall" is refuted by structural ink at the edge, never by decoration.
STRUCTURAL_TONE_MAX = 0.15
SOURCE_PARTITION_EDGE_CRITERION = (
    "source-partition-edge-in-final-picture-v1"
)
SOURCE_CROSSING_RULE_CRITERION = (
    "source-crossing-rule-not-comb-scoped-v1"
)
# Half the corpus hairline (0.72pt), the thickest stroke a partition boundary
# is drawn with here.  A tone sample taken to certify what stands BESIDE an
# edge must clear the edge's own ink; this is that physical clearance, not a
# tolerance anyone tunes.
PARTITION_EDGE_PROBE_OFFSET_PT = 0.36
# Retained suppression reason tuples this referee can corroborate from the
# source, and the criterion each is corroborated under.  A tuple is admitted
# here only when the referee can answer the reason's OWN factual claim out of
# Poppler's output; a reason that merely asserts something about the producer's
# internal state can never appear in this table.
RETAINED_SUPPRESSION_SOURCE_CRITERIA = {
    ("emission-suppressed-caption-block-not-character-cells",):
        SOURCE_CAPTION_BLOCK_CRITERION,
    # R2a (2026-08-14).  Two more reason tuples whose factual claims Poppler
    # can answer.  Their corroborations return a VERDICT CERTIFICATE rather
    # than raising: the obligation is that the question was asked of the
    # paper, and "the paper does not show it" is an answer -- the subject then
    # simply stays unevaluable and cannot be retired on source evidence.
    # Measured before wiring (the probes are the R2a record): of the 18
    # partition subjects, 17 carry a full-span edge complete in the final
    # picture and ONE does not -- 1800-2018 p1c4's only full-span edge has a
    # 42.55pt stretch that is neither painted nor a tone boundary -- and the
    # sole crossing-rule subject (1600WP p1c36) corroborates.
    ("emission-suppressed-no-rectangular-owner", "painted-edge-partition"):
        SOURCE_PARTITION_EDGE_CRITERION,
    ("emission-suppressed-no-final-visible-band",):
        SOURCE_CROSSING_RULE_CRITERION,
    # DECISION A (2026-08-16): a legacy comb the compartment rule refuses
    # whole (no run of character boxes survives) is retained rather than
    # published. Its factual claim is the same one the crossing-rule
    # corroboration already re-derives against Poppler: the "dividers" are
    # rules that extend far beyond the comb band -- table structure, not
    # comb ticks. Both members of this population (2551M p2c13's column
    # rule spanning y 46.56-140.16 across a 11.76pt row, 1604CF p2c73's
    # grid rules spanning 350.88pt across a 16.8pt row) are exactly that
    # shape. No new probe is added; a criterion whose corroboration cannot
    # run still fails closed below.
    ("emission-suppressed-compartment-rule",):
        SOURCE_CROSSING_RULE_CRITERION,
}
# Poppler emits every character as a `use` of a `#glyph-*` path, and `parse_svg`
# records each one as an unsupported region carrying its transformed bound.
# These two reasons are the ones that carry a MEASURED glyph box; the other
# glyph reasons are whole-page regions meaning "text exists that this parser
# could not place", and they are deliberately not counted.
MEASURED_GLYPH_REASON_PREFIXES = (
    "glyph use may occlude geometry: ",
    "stroked glyph use may occlude geometry: ",
)
# Corpus identity pins, not geometry exceptions: every slug follows the same
# parser and decision rules. A substituted/missing form must not pass merely
# because the replacement keeps the two aggregate counts unchanged.
EXPECTED_COMBS_BY_SLUG = {
    "0605-1999": 27,
    "0619e-2018": 60,
    "0619f-2018": 64,
    "0620-2019": 60,
    "1600-pt-2018": 95,
    "1600-vt-2018": 95,
    "1600wp-2010": 23,
    "1601-fq-2020": 106,
    "1601c-2018": 132,
    "1601eq-2019": 99,
    "1602q-2019": 175,
    "1603q-2018": 78,
    "1604c-2018": 19,
    "1604cf-2008": 15,
    "1604e-2018": 15,
    "1604f-2018": 16,
    "1606-2018": 76,
    "1621-2019": 69,
    "1700-2018": 141,
    "1701-2018": 283,
    "1701-2018-attachment": 123,
    "1701-2018-conso": 40,
    "1701a-2018": 134,
    "1701ms-2024": 140,
    "1701q-2018": 128,
    "1702ex-2018": 149,
    "1702mx-2018c": 117,
    "1702mx-2018c-attachment": 108,
    "1702q-2018": 106,
    "1702rt-2018c": 205,
    "1706-2018": 81,
    "1707-2021": 113,
    "1707a-2021": 97,
    "1709-2020": 19,
    "1800-2018": 108,
    "1801-2018": 102,
    "2000-dst-2018": 132,
    "2000-ot-2018": 75,
    "2200a-2020": 43,
    "2200an-2018": 87,
    "2200c-2018": 43,
    "2200m-2018": 86,
    "2200p-2020": 42,
    "2200s-2018": 66,
    "2200t-2022": 90,
    "2316-2021": 31,
    "2550-ds-2025": 79,
    "2550m-2007": 21,
    "2550q-2024": 150,
    "2551m-2002": 25,
    "2551q-2018": 105,
    "2552-2018": 73,
    "2553-1999": 21,
}
if (len(EXPECTED_COMBS_BY_SLUG) != EXPECTED_FORMS
        or sum(EXPECTED_COMBS_BY_SLUG.values()) != EXPECTED_COMBS):
    raise RuntimeError("comb referee corpus pins are internally inconsistent")

# The second half of the census, pinned separately so that the two quantities
# r14 conflated can never be added or subtracted from each other again. The
# ledger denominator above is active + retained; this is the retained half, per
# slug, and the difference is the ACTIVE comb-cell count. A retained subject is
# a comb the lattice can no longer own a rectangle for: it blocks the gate, it
# emits nothing, and it stays in the ledger as continuity evidence. Slugs absent
# from this table are pinned at zero -- retention appearing on a new form is a
# census move that must be declared here, exactly like a comb count moving.
# Measured 2026-08-07 over build/layout, and identical under 21e0630^'s lattice.
# Re-measured 2026-08-07 (r20) over the regenerated layouts: 17 -> 21 retained,
# on the same six slugs whose subject totals moved. The cause is extract.py's
# line-cap model (see AUDIT_DEPENDENCY_SHA256): a round-capped tick that reaches
# its rail is a divider, so bands that were single unbroken rows become combs,
# and a legacy comb subject that can no longer be given one rectangle is
# retained rather than dropped. 0605 3 -> 1 and 1604CF 2 -> 1 move the other way
# for the same reason -- their retained subjects found rectangular owners once
# the ticks reached.
# Re-measured 2026-08-07 (r21): 21 -> 22, one slug only -- 2551M 3 -> 4. The
# bottom-guide-tick recognition gives 2551M seven more subjects (18 -> 25) and
# one of them, like the four already retained there, cannot be given a single
# rectangular owner; it is retained rather than dropped, exactly as the r20
# note above describes for the line-cap model.
# Re-measured 2026-08-08 (r27): 22 -> 33, on exactly seven slugs, and every
# one of the eleven new subjects carries the single reason code
# `emission-suppressed-caption-block-not-character-cells` -- 1606 +1, 2200A +2,
# 2200AN +1, 2200C +1, 2200P +2, 2200S +1, 2200T +2. This is the caption-block
# refutation described at LATTICE_PRODUCER_SHA256: a cell whose every comb
# compartment carries running prose is not a character cell, so the comb comes
# off the cell and the subject moves to the retained half rather than leaving
# the ledger. EXPECTED_COMBS does not move and no per-slug comb count moves,
# because active + retained is what that denominator counts -- which is the
# whole point of splitting the two quantities here.
#
# 2200C's entry goes 1 -> 3 and keeps its original subject: one
# `emission-suppressed-no-rectangular-owner` / `painted-edge-partition` from
# r20, plus the two new ones.
# Re-measured 2026-08-10 (r37, P1) for F097: 2000-DST enters this table for
# the first time, 0 (absent) -> 1. `bridge_knockout_bites` restores the
# vertical rail at x=192.38, so the legacy 6-slot comb subject
# `p1@164.30,109.94,248.69,131.06` (`p1c4`) can no longer be given one
# rectangular cell -- its cell splits into `p1c111`/`p1c112` -- and it moves
# `active_resolved` -> `retained_unresolved`,
# `emission-suppressed-no-rectangular-owner` / `painted-edge-partition`,
# exactly the reason codes 2200C's own r20 retained subject already carries.
# EXPECTED_RETAINED_SUBJECTS (derived below) moves 33 -> 34 with it.
# EXPECTED_COMBS_BY_SLUG["2000-dst-2018"] does NOT move (131, unchanged): the
# subject stays in the ledger, only its resolution state does.
#
# Re-measured 2026-08-12 (W3, F064): `lattice._reunify_comb_band` gives a
# comb band its own rectangle -- absorbing or trimming exactly the current
# cells its own rails and rows bound -- when the general cell walk could not
# find one rectangular owner for it but doing so claims no paper the comb's
# own dividers do not own. 1707-2021's item 8A ("If yes, please specify"),
# F064's own named defect, is entirely resolved: its ONE
# `emission-suppressed-no-rectangular-owner` subject (a 25-slot comb whose
# band the cell walk split at a false row cut a checkbox's own bottom edge
# induced, then fragmented further at a page-wide x-coincidence between its
# own dividers and unrelated ink elsewhere) moves `retained_unresolved` ->
# `active_resolved` and leaves this table (1 -> 0, absent). Generalising the
# SAME evidence, never special-cased to this form, resolves two more
# subjects the general mechanism reaches by the identical proof: 1707a-2021's
# own matching item-8A shape (2 -> 1) and one of 2551m-2002's four subjects,
# a 4-slot comb sharing the same false-cut shape (4 -> 3). The other 30
# retained subjects in this table are untouched -- each one either already
# has an exact current-cell owner (nothing to reunify) or the candidate
# rectangle would cross a wall not among the comb's own dividers, absorb a
# cell a `source_owned_comb_frame` certificate or an already-resolved comb
# already owns, or swallow printed ink -- and reunification correctly
# declines all of them. EXPECTED_RETAINED_SUBJECTS moves 33 -> 30.
EXPECTED_RETAINED_SUBJECTS_BY_SLUG = {
    "0605-1999": 1,
    "1600wp-2010": 2,
    "1604cf-2008": 2,
    "1604f-2018": 1,
    "1606-2018": 1,
    "1707a-2021": 1,
    "1800-2018": 1,
    "2000-ot-2018": 2,
    "2200a-2020": 2,
    "2200an-2018": 1,
    "2200c-2018": 3,
    "2200p-2020": 2,
    "2200s-2018": 1,
    "2200t-2022": 2,
    "2550m-2007": 4,
    "2551m-2002": 4,
    "2553-1999": 2,
}
EXPECTED_RETAINED_SUBJECTS = sum(EXPECTED_RETAINED_SUBJECTS_BY_SLUG.values())
# The number r14 mistook for the ledger denominator. It is derived here, never
# written as a literal, so the two can only ever disagree by a declared change.
EXPECTED_ACTIVE_COMBS = EXPECTED_COMBS - EXPECTED_RETAINED_SUBJECTS
if (set(EXPECTED_RETAINED_SUBJECTS_BY_SLUG) - set(EXPECTED_COMBS_BY_SLUG)
        or EXPECTED_ACTIVE_COMBS != sum(
            EXPECTED_COMBS_BY_SLUG[slug]
            - EXPECTED_RETAINED_SUBJECTS_BY_SLUG.get(slug, 0)
            for slug in EXPECTED_COMBS_BY_SLUG)
        or any(count <= 0
               for count in EXPECTED_RETAINED_SUBJECTS_BY_SLUG.values())
        or any(EXPECTED_RETAINED_SUBJECTS_BY_SLUG[slug]
               > EXPECTED_COMBS_BY_SLUG[slug]
               for slug in EXPECTED_RETAINED_SUBJECTS_BY_SLUG)):
    raise RuntimeError("comb referee retained-subject pins are inconsistent")

# Reviewed from report payload
# 15b6454ef9c156435fc33d47b177ff4b2db379207fa694bbcdb87200bb341ca4.
# The digest binds all 105 ordered tuples of cell identity, subject identity,
# source verdict, compartment count, divider positions, and position verdict.
REVIEWED_2551Q_REFEREE_TUPLES_SHA256 = (
    "f6fa281a670156784c723911329669849cf433c3f082c3d108a89980f1290414"
)
REVIEWED_2551Q_EXPLICIT_COMPARTMENTS = {
    "p2c5": 14,
    "p2c80": 12,
}

# This is verify.py's fixed position tolerance.  It is copied as a bound, not
# exposed as a CLI knob: changing it here would make the referee a third
# independently tunable answer rather than an adjudicator.
POSITION_TOL_PT = 0.25

# lattice.py publishes every layout coordinate through `lattice.q`, which rounds
# to `lattice.QUANT` = 2 decimal places.  A source measurement is compared with a
# published one at exactly that precision: anything finer would only measure
# Poppler's own coordinate quantum, and anything coarser would let two distinct
# printed weights read as one -- this corpus draws walls at 0.44pt AND 0.45pt,
# and at 0.75pt AND 0.76pt, so even POSITION_TOL_PT would conflate them.
LAYOUT_QUANT_PLACES = 2

_NUMBER = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"
_TRANSFORM_RE = re.compile(r"([A-Za-z]+)\s*\(([^)]*)\)")
_PATH_TOKEN_RE = re.compile(rf"[A-Za-z]|{_NUMBER}")
_CELL_RE = re.compile(r"^p\d+c\d+$")
_CELL_PAGE_RE = re.compile(r"^p(\d+)c\d+$")
_CELL_SLOT_RE = re.compile(r"^(p\d+c\d+)-s(\d+)$")
_PAGE_RE = re.compile(r"^page-(\d+)$")
_SUBJECT_KEY_RE = re.compile(
    rf"^p(\d+)@({_NUMBER}),({_NUMBER}),({_NUMBER}),({_NUMBER})$")
# emit.py serialises point geometry to four decimal places.  Two independently
# rounded endpoints can differ by at most two ten-thousandths of a point.
HTML_GEOMETRY_EPSILON_PT = 0.0002
# The five published position relations do NOT share one tolerance, and pinning
# them all to HTML_GEOMETRY_EPSILON_PT was a category error that made every
# offender record unparseable.  `emission_layout*` compares two of OUR OWN
# four-decimal serialisations, so it is exact to HTML_GEOMETRY_EPSILON_PT.  The
# three relations whose name carries `source` cross into raw source geometry,
# whose floats are not that serialisation; audit.py binds exactly those three
# to POSITION_TOL_PT and says so at its own declaration ("it applies only to
# comparisons that cross representations into raw source geometry ... every
# same-representation emitted/layout comparison keeps EMITTED_GEOMETRY_EPS_PT"),
# and this file already carries POSITION_TOL_PT under the same name for its own
# Poppler-space work.  Each relation is still pinned to exactly one fixed
# constant -- neither is a knob -- and swapping them in either direction is
# still rejected.
AUDIT_POSITION_TOLERANCE_PT = {
    "emission_layout_position": HTML_GEOMETRY_EPSILON_PT,
    "emission_layout_outer_position": HTML_GEOMETRY_EPSILON_PT,
    "emission_source_position": POSITION_TOL_PT,
    "emission_source_outer_position": POSITION_TOL_PT,
    "layout_source_outer_position": POSITION_TOL_PT,
}
if set(AUDIT_POSITION_TOLERANCE_PT) != set(AUDIT_POSITION_FIELDS):
    raise RuntimeError(
        "every audit position relation needs exactly one pinned tolerance")
SVG_INLINE_STYLE_PROPERTIES = frozenset({
    "clip-path",
    "display",
    "fill",
    "fill-opacity",
    "fill-rule",
    "filter",
    "marker-end",
    "marker-mid",
    "marker-start",
    "mask",
    "opacity",
    "paint-order",
    "stroke",
    "stroke-dasharray",
    "stroke-dashoffset",
    "stroke-linecap",
    "stroke-linejoin",
    "stroke-miterlimit",
    "stroke-opacity",
    "stroke-width",
    "transform",
    "vector-effect",
    "visibility",
})
UNSUPPORTED_SVG_PRESENTATION_ATTRIBUTES = frozenset({
    "backdrop-filter",
    "isolation",
    "mix-blend-mode",
    "transform-box",
    "transform-origin",
})
HTML_VOID_ELEMENTS = frozenset({
    "area",
    "base",
    "br",
    "col",
    "embed",
    "hr",
    "img",
    "input",
    "link",
    "meta",
    "param",
    "source",
    "track",
    "wbr",
})
HTML_RENDER_AFFECTING_INLINE_PROPERTIES = frozenset({
    "all",
    "animation",
    "animation-name",
    "backdrop-filter",
    "clip",
    "clip-path",
    "contain",
    "content-visibility",
    "display",
    "filter",
    "isolation",
    "mix-blend-mode",
    "opacity",
    "perspective",
    "rotate",
    "scale",
    "transform",
    "translate",
    "visibility",
    "zoom",
})
# These are the only structural declarations emitted by emit.py's document
# stylesheet.  Point geometry itself remains bound independently to the layout
# artifact below.  Rejecting any other structural selector/property prevents a
# stylesheet from moving or hiding a comb while its inline numbers still look
# canonical.
HTML_STYLESHEET_STRUCTURAL_DECLARATIONS = frozenset({
    (".band", "height", "100%"),
    (".band", "left", "0"),
    (".band", "position", "absolute"),
    (".band", "top", "0"),
    (".band", "width", "100%"),
    (".c", "position", "absolute"),
    (".c", "z-index", "6"),
    (".doc-link", "display", "none"),
    (".doc-link", "left", "0"),
    (".doc-link", "position", "absolute"),
    (".doc-link", "top", "0"),
    (".doc-link", "z-index", "9"),
    (".f,.f .s", "overflow", "hidden"),
    (".fi", "inset", "0"),
    (".fi", "position", "absolute"),
    (".img", "display", "block"),
    (".img", "position", "absolute"),
    (".img", "z-index", "6"),
    (".page", "overflow", "hidden"),
    (".page", "position", "relative"),
    (".r", "position", "absolute"),
    (".rl", "left", "0"),
    (".rl", "position", "absolute"),
    (".rl", "top", "0"),
    (".s", "position", "absolute"),
    (".t", "position", "absolute"),
    (".t", "z-index", "5"),
})
HTML_STYLESHEET_STRUCTURAL_PROPERTIES = frozenset({
    "all",
    "animation",
    "animation-name",
    "backdrop-filter",
    "bottom",
    "clip",
    "clip-path",
    "contain",
    "content-visibility",
    "display",
    "filter",
    "height",
    "inset",
    "isolation",
    "left",
    "mix-blend-mode",
    "opacity",
    "overflow",
    "perspective",
    "position",
    "right",
    "rotate",
    "scale",
    "top",
    "transform",
    "translate",
    "visibility",
    "width",
    "z-index",
    "zoom",
})
HTML_REQUIRED_STYLESHEET_DECLARATIONS = frozenset({
    (".page", "overflow", "hidden"),
    (".page", "position", "relative"),
    (".c", "position", "absolute"),
    (".s", "position", "absolute"),
})
HTML_STYLESHEET_FIXED_VALUES: dict[tuple[str, str], frozenset[str]] = {
    ("*", "box-sizing"): frozenset({"border-box"}),
    ("*", "margin"): frozenset({"0"}),
    ("*", "padding"): frozenset({"0"}),
    ("html,body", "-webkit-print-color-adjust"): frozenset({"exact"}),
    ("html,body", "background"): frozenset({"#fff"}),
    ("html,body", "print-color-adjust"): frozenset({"exact"}),
    (".page", "background"): frozenset({"#fff"}),
    (".page", "break-after"): frozenset({"page"}),
    (".page", "overflow"): frozenset({"hidden"}),
    (".page", "page-break-after"): frozenset({"always"}),
    (".page", "position"): frozenset({"relative"}),
    (".page:last-of-type", "break-after"): frozenset({"auto"}),
    (".page:last-of-type", "page-break-after"): frozenset({"auto"}),
    (".rl", "left"): frozenset({"0"}),
    (".rl", "position"): frozenset({"absolute"}),
    (".rl", "top"): frozenset({"0"}),
    (".r", "position"): frozenset({"absolute"}),
    (".t", "position"): frozenset({"absolute"}),
    (".t", "text-rendering"): frozenset({"geometricprecision"}),
    (".t", "white-space"): frozenset({"pre"}),
    (".t", "z-index"): frozenset({"5"}),
    (".c", "position"): frozenset({"absolute"}),
    (".c", "z-index"): frozenset({"6"}),
    (".s", "position"): frozenset({"absolute"}),
    (".img", "display"): frozenset({"block"}),
    (".img", "position"): frozenset({"absolute"}),
    (".img", "z-index"): frozenset({"6"}),
    (".band", "height"): frozenset({"100%"}),
    (".band", "left"): frozenset({"0"}),
    (".band", "position"): frozenset({"absolute"}),
    (".band", "top"): frozenset({"0"}),
    (".band", "width"): frozenset({"100%"}),
    (".f,.f .s", "overflow"): frozenset({"hidden"}),
    (".fc", "text-align"): frozenset({"center"}),
    (".fi", "-webkit-appearance"): frozenset({"none"}),
    (".fi", "appearance"): frozenset({"none"}),
    (".fi", "background"): frozenset({"none", "transparent"}),
    (".fi", "border"): frozenset({"0"}),
    (".fi", "border-radius"): frozenset({"0"}),
    (".fi", "box-shadow"): frozenset({"none"}),
    (".fi", "caret-color"): frozenset({"#000000", "transparent"}),
    (".fi", "color"): frozenset({"#000000"}),
    (".fi", "font-family"): frozenset({
        '"ebirforms arimo", arimo, arial, helvetica, sans-serif',
        '"ebirforms tinos", tinos, "times new roman", times, serif',
    }),
    (".fi", "font-feature-settings"): frozenset({"normal"}),
    (".fi", "font-kerning"): frozenset({"none"}),
    (".fi", "font-style"): frozenset({"normal"}),
    (".fi", "font-variant-ligatures"): frozenset({"none"}),
    (".fi", "font-variation-settings"): frozenset({'"wght" 400'}),
    (".fi", "font-weight"): frozenset({"400"}),
    (".fi", "inset"): frozenset({"0"}),
    (".fi", "margin"): frozenset({"0"}),
    (".fi", "outline"): frozenset({"0"}),
    (".fi", "padding"): frozenset({"0"}),
    (".fi", "position"): frozenset({"absolute"}),
    (".fi", "text-rendering"): frozenset({"geometricprecision"}),
    (".fi:focus", "background"): frozenset({"rgba(255,213,0,.35)"}),
    (".fi:hover", "background"): frozenset({"rgba(21,101,192,.07)"}),
    (".doc-link", "background"): frozenset({"#fff"}),
    (".doc-link", "color"): frozenset({"#0645ad"}),
    (".doc-link", "display"): frozenset({"none"}),
    (".doc-link", "font"): frozenset({
        "12px/1.5 system-ui,-apple-system,sans-serif",
    }),
    (".doc-link", "left"): frozenset({"0"}),
    (".doc-link", "padding"): frozenset({"2px 8px"}),
    (".doc-link", "position"): frozenset({"absolute"}),
    (".doc-link", "text-decoration"): frozenset({"underline"}),
    (".doc-link", "top"): frozenset({"0"}),
    (".doc-link", "z-index"): frozenset({"9"}),
    ("@font-face", "font-display"): frozenset({"block"}),
    ("@font-face", "font-family"): frozenset({
        '"ebirforms arimo"', '"ebirforms tinos"',
    }),
    ("@font-face", "font-style"): frozenset({"italic", "normal"}),
    ("@font-face", "font-weight"): frozenset({"100 900", "400", "700"}),
    ("@font-face", "src"): frozenset({
        'url("fonts/arimo-latin-wght-italic.woff2") format("woff2")',
        'url("fonts/arimo-latin-wght-normal.woff2") format("woff2")',
        'url("fonts/tinos-latin-400-normal.woff2") format("woff2")',
        'url("fonts/tinos-latin-700-normal.woff2") format("woff2")',
    }),
    ("@page", "margin"): frozenset({"0"}),
}
# In document order: the band data runtime, the field runtime, the field
# debug overlay, and (new, T3+T4) the tab-walk debug viewer. All 53 bundles
# emit this exact tuple -- measured, not assumed.
#
# The FIRST hash has never moved. It is the band data runtime, and its being
# byte-identical across every re-pin is the standing evidence that none of the
# field-layer work has reached into page scaffolding.
#
# The SECOND moved at r14 only. It has not moved since.
#
# The THIRD (field debug overlay) moved again here, T3+T4 (2026-08-11), and
# for two measured reasons, not one:
#   * F213's tone split: a rect is a wall candidate only when its own gray is
#     at or below RULE_WALL_TINT_SPLIT_GRAY (0.70) or is the knockout
#     sentinel (1.0) -- see that constant's own comment in emit.py for the
#     eight-value tone census this is measured against. `over` (red) fell to
#     0 or dropped on every one of the 53 forms measured before/after (the
#     phantom "crosses a printed wall" reports a tint band invented); `vacant`
#     (blue) fell on every form too, and never rose on any -- the phantom
#     boxes a tint fragment used to close on its own are gone, while the real
#     ones (F210's Schedule-1 squares, four blue boxes, unchanged shape,
#     survive by construction: 0.251 sits below the split).
#   * The vacant probe's own paint source: `paintAt()` now reads the RAW,
#     unfiltered rect list, not `visibleRects()`'s output. Found live on
#     1701-2018 p2 while proving the above: a same-as-paper white knockout
#     (F210's own "write here" paint) that nothing wholly contains is
#     invisible to `visibleRects()` (compared against paper, and paper is
#     white too), so the probe was finding the tint UNDER it instead of the
#     knockout ON TOP of it -- which would have reported F210's own real
#     defect as decoration. `field_debug_paint_source_assertions()` proves
#     this by running the SAME shipped `paintAt()` against both arguments.
#   * field runtime (e2b0b779 -> 1ed88b99, r14). A comb compartment the
#     source already filled in now emits its slot div with no <input> in it,
#     so the NodeList a taxpayer tabs through is shorter than the
#     compartment count. `move` and the paste handler used to index that
#     list with the `data-slot-index` ATTRIBUTE, which would have stopped
#     advancing at the first printed box; they now find the element's
#     position in the list with `positionOf`. `data-slot-index` still means
#     the compartment's number.
#   * field debug overlay (877c6c01 -> 96754a6a, r14). F172/G15: the
#     corrected overlay had existed in emit.py and had simply never been
#     regenerated into the corpus. `printed box with no input` now appears in
#     53 of 53 bundles and `no usable box` in none.
#
# The FOURTH is new: TAB_DEBUG_JS, the `?debug=tab` viewer (T3). It ships
# behind its own `debug=tab` token, distinct from `debug=fields`, so the two
# cannot be triggered by each other's query string, and follows the same four
# barriers FIELD_DEBUG_JS does (tab_debug_assertions() proves them the same
# way).
#
# CORRECTED 2026-08-11, same session, and the correction is the point of the
# pin: EXPECTED_HTML_STRUCTURE_SHA256 locks the bytes of
# `build/html/<slug>.html` -- this module's own `--html-dir` default -- NOT
# `forms/<slug>/index.html`. The two differ by design (the shipped bundle
# links form.css; the build document inlines it, ~3.6 KB more on 0605-1999),
# so a pin re-derived from the wrong tree matches nothing. Operator did
# exactly that while reviewing T3+T4, and every one of the 53 forms came back
# `emitted HTML bytes changed from the reviewed pin` -- 0 forms measured, the
# referee refusing to adjudicate anything. That is the pin working: it fails
# closed and totally, not partially, and an unevaluable referee is a gate
# FAILURE here rather than a pass. Re-derived from build/html: 53 forms
# measured, 0 errors, 4,587 combs found, 4,508 agree, ZERO disagreements,
# 4,554 active subjects, 33 retained. If you ever re-pin these, read them out
# of build/html or the referee will reject the whole corpus.
# Re-pinned 2026-08-11 (T3+T4). Read only by the referee, which runs last.
#
# Re-pinned 2026-08-11 (T5a, F210): `emit.py`'s `field_verdict` gains a
# `checkbox-square` branch (`CheckboxSquareWriting` / `checkbox_square_
# field_box`) that gives a `label` cell an input for every checkbox square
# printed inside it -- a closed box of four DECORATIVE rules whose interior
# carries a KNOCKOUT fill, 4-20pt on both axes, the source's own "write here"
# that the lattice never turns into a cell boundary. Corpus-wide this selects
# exactly 22 squares on exactly 3 forms -- 1700-2018 (14), 1701-2018 (6, the
# four Schedule-1 Taxpayer/Spouse boxes plus the two item-8 Foreign-Tax-Credit
# Yes/No boxes), 1701a-2018 (2) -- independently corroborated by the
# tone-aware `?debug=fields` overlay's `vacant` census (6/14/2, computed in a
# browser from the rendered SVG, a completely different code path). 11 label
# cells gain an input, 22 `<input>` elements are added (every claimed cell
# holds exactly two squares). No comb constant moves and no existing tolerance
# moves; `inputs_over_printed_text` stays 3 forms/6, `comb_slots_match_printed`
# stays 10/19, comb censuses stay 4,587/33/4,554 unmoved. New standing
# corpus-wide self-test `emit.checkbox_square_corpus_assertions` (run by
# `emit.py --self-test`) re-derives the claim set against every `build/ir`
# this checkout has and fails if any claimed cell lacks a typing surface.
# `EXPECTED_HTML_STRUCTURE_SHA256` re-pinned for the 3 moved slugs.
# `AUDIT_DEPENDENCY_SHA256["tools/formgen/extract.py"]` (this file's own
# earlier pin) is ALSO re-pinned this session, but for an unrelated reason:
# extract.py's own self-test corpus gained a `checkbox-square` check, not an
# extraction behaviour change -- every `build/ir/*.ir.json` byte-matches a
# re-extraction of the same source PDF. `HTML_RUNTIME_SCRIPT_SHA256` does NOT
# move (no runtime script text changed, only pre-rendered cell markup on
# these 3 documents).
#
# The THIRD moved once more in the same session, for the vacant probe's
# INTERIOR test only (operator review of T3+T4). `isTintTone` answers "can
# this STROKE bound a box?" and keeps its 0.70 split, because 0.651 strokes
# are checkbox outlines (94% of 0.651 rules are short box edges) and must go
# on bounding boxes. A separate `isDecorPaint` answers "is this box's
# INTERIOR a shading pad?" over the wider band (STRUCTURAL_MAX_GRAY, 1) --
# 0.15 being the pipeline's own structural cutoff, not a new number. At a box
# CENTRE only a slab can be present (a wall-tone stroke through the centre
# would have closed a smaller box at itself), so grey there means decoration
# whichever side of the stroke split its tone falls on. Measured: 0619-E's
# twelve centavo-separator compartments -- each a gray 0.651 pad filling its
# own box -- stop being reported as forgotten fields (12 -> 0), while F210's
# Schedule-1 knockout squares stay blue. Corpus `vacant` 130 across 12 forms.
# `field_debug_interior_decor_assertions()` runs the SHIPPED isTintTone and
# isDecorPaint under node and requires them to DISAGREE about 0.651: if a
# refactor ever collapses the two questions into one, that trips first.
# The other three digests did not move; only FIELD_DEBUG_JS changed.
#
# Re-pinned 2026-08-12 (W4 package, F219 and F220), TWO digests moved. Emitted
# script order is fixed (BAND_JS, then a second runtime script, then
# FIELD_DEBUG_JS, then TAB_DEBUG_JS), so positions 0 and 2 below are BAND_JS
# and FIELD_DEBUG_JS; position 1 and 3 did NOT move.
#
# Position 0 (BAND_JS), F219: `SignatureLineBinding`'s inline
# `text-align:center` was applied at static pre-render time
# (`field_input_markup`) but never mirrored into the growable-band runtime
# blob, so a signature line inside a band would have rendered centred in the
# pre-rendered row and left-aligned in every runtime-cloned one.
# `field_json` gains a sparse `centered` key (present only where `fields.
# centered` already holds the cell id, mirroring `region_insets`'s own
# sparse shape) and BAND_JS's `fieldMetrics` gains a `slotIndex` parameter it
# uses to set `el.style.textAlign` -- centred when `field.centered` is true
# and `slotIndex` is null (a plain field's own writing line, never a comb
# slot, matching `field_input_markup`'s identical guard), cleared otherwise.
# Zero live cases (measured: the band-data JSON blob for every one of the 53
# forms is byte-identical before and after -- no growable band in this
# corpus currently combines with a centred field), so this is a structural
# fix with no visible effect on the shipped corpus today, proven by a
# synthetic assertion (`field_json`'s own `centered`-key presence/absence,
# and a source-text check that BAND_JS actually reads it) rather than by a
# corpus measurement that currently has nothing to find.
#
# Position 2 (FIELD_DEBUG_JS), F220: `boxAt`'s wall discovery closed a box on
# a vertical member whose ink merely GRAZED the box's own T/B at one end
# while running well past the other -- the shape of a rule belonging to a
# taller structure that happens to touch this box, not one drawn for it (see
# `crossesCleanly`'s own docstring in FIELD_DEBUG_JS for the full mechanism
# and the two measured cases, 1604cf-2008 p1c33 and 2550m-2007 p2's
# column-header block). `crossesCleanly` is new, applied only from
# `allBoxes`' own `boxAt(...,true)` call -- never from the per-input lookup
# `pageCensus` makes for a real input's own centre -- so it can only narrow
# the VACANT population, by construction (see the same docstring for why).
# `wallsOf`/`boxAt` gained a `strict` parameter threading `crossesCleanly`
# through the L/R branches only (never T/B: a comb's shared rail is, by
# design, flush with its own edge compartment on one side and not the other,
# and applying this there rejected every edge compartment in the corpus --
# measured, reverted).
#
# What moved: exactly these two digests (0 and 2 of 4) -- confirmed by
# re-deriving all four from `build/html/*.html` for all 53 forms and diffing
# against the prior pin position-by-position: only [0] and [2] disagreed,
# each identically on every form (one hash value per position across all 53
# -- neither runtime script carries per-form data). What did NOT move: every
# non-script byte. Diffing all 53 `build/html/<slug>.html` files against
# their own pre-change bytes with every `<script ...>...</script>` span
# stripped from both sides first (attributed tags too, so a leak into the
# band-data JSON blob would also show) found ZERO forms with any residual
# difference: 53/53 identical outside the script tags, and the band-data
# JSON blob specifically is byte-identical on all 53 (F219's own zero-live-
# case measurement, restated). `emit.py --self-test`'s field-decision counts
# (`inputs_over_printed_text` 2/5, `comb_slots_match_printed` 10/19) are
# unmoved, and the `vacant` census F220's overlay computes fell from 110 to 7
# corpus-wide with fits/small/over/unboxed totals UNCHANGED to the input
# (43,731/604/27/240 before and after) -- proof the fix touches only phantom
# discovery, never a real input's own box. `EXPECTED_HTML_STRUCTURE_SHA256`
# moves on all 53 for the identical reason (the script bytes are counted in
# the structure hash); every entry below is re-derived from the same
# `build/html` regeneration this comment describes.
HTML_RUNTIME_SCRIPT_SHA256 = (
    "56c58ef299d8bb7340f18021fd8af0af67e657becd4c9dd61b3da6421504d1bf",
    "1ed88b99506a819cacf86e3020a2c73c6bac3e12c3d739327b385145d2d13147",
    "4b02351ea0c06079df64225ffd99bc39f5388aff018b1e49928106c0a5b7886a",
    "47a80c75dbd18ba85ef12623bd01867201962d6646ba267fe8efc9d988d396e0",
)
HTML_ROOT_ATTRIBUTES = frozenset({
    "data-form",
    "data-revision",
    "data-rule-backend",
    "data-schema-version",
    "data-source-sha256",
    "lang",
})
HTML_COMB_ATTRIBUTES = frozenset({
    "class",
    "data-cell-kind",
    "data-col",
    "data-comb-capacity",
    "data-comb-pitch",
    "data-comb-slots",
    "data-field-kind",
    "data-field-name",
    "data-row",
    "id",
    "style",
})
HTML_BAND_ATTRIBUTES = frozenset({
    "class",
    "data-band",
    "data-capacity",
    "data-overflow-rows",
    "data-rendered-rows",
    "data-row-pitch",
    "id",
})
HTML_INPUT_ATTRIBUTES = frozenset({
    "autocomplete",
    "class",
    "data-slot-index",
    "id",
    "maxlength",
    "name",
    "spellcheck",
    "type",
})
def slot_input_style_ok(style: str | None) -> bool:
    """The one style a comb-slot input may declare (W6/F227): a top-only
    writing-trim inset, positive, in points, with the other three components
    exactly 0pt. Anything else -- any other property, any other inset shape --
    is outside the emitter grammar, so the style attribute cannot become a
    general styling channel for slot inputs."""
    if style is None:
        return True
    return re.fullmatch(r"inset:\d+(?:\.\d+)?pt 0pt 0pt 0pt", style) is not None


HTML_LINK_ATTRIBUTES = frozenset({
    "as",
    "crossorigin",
    "href",
    "rel",
    "type",
})
HTML_FONT_PRELOAD_HREFS = frozenset({
    "fonts/arimo-latin-wght-italic.woff2",
    "fonts/arimo-latin-wght-normal.woff2",
    "fonts/tinos-latin-400-normal.woff2",
    "fonts/tinos-latin-700-normal.woff2",
})
HTML_ALLOWED_TAGS = frozenset({
    "a",
    "body",
    "div",
    "g",
    "head",
    "html",
    "image",
    "input",
    "link",
    "meta",
    "path",
    "rect",
    "script",
    "style",
    "svg",
    "template",
    "title",
})
# All 53 refreshed 2026-08-07 (r14). They had been stale on all 53 since the
# GOAL.md §Blocked entry, so the referee had been UNEVALUABLE -- a red verdict,
# not a pass -- for every run since. What was reviewed before refreshing them is
# recorded here, because "the producer just wrote these bytes" is not a review:
#
#   Every one of the 53 emitted documents was diffed against its HEAD (21e0630)
#   committed self at the level of tags, ids and attributes. Across the whole
#   corpus the tag inventory changes in exactly ONE direction -- 332 <input>
#   elements deleted, zero elements of any kind added -- and the attribute-level
#   changes are 6,384 input[class] renumberings (the field-metric CSS buckets
#   shift when the field population does), and 46 divs whose data-cell-kind goes
#   `field` -> `shaded`. Nothing else moved in any document.
#
#   Both directions were then checked against the official PDFs by eye, not only
#   by count: 281 of the 332 are comb compartments the source itself printed a
#   constant into (`II 011`, `XC 010`, the century `2 0`, the TIN branch code
#   `0 0 0 0 0`) and 51 are cells sitting on official grey "no entry applies"
#   shading -- 2200T page 2's Part V header band was rasterised from the pinned
#   PDF to confirm the band really is grey and the P2.50 row below it really is
#   white. Rasters in the session scratchpad under preprinted/.
#
# 25 of the 53 refreshed 2026-08-07 (r20) -- the other 28 documents are
# byte-identical and their pins were not touched.
#
# This pin hashes `build/html/<slug>.html`, the EMITTED document, not
# `forms/<slug>/index.html`, the bundled one. r20's refresh was first computed
# off the bundle and it cost a full 60-minute gate: 25 forms reported "emitted
# HTML bytes changed from the reviewed pin" and the referee fell to 27 of 53.
# Exactly the same 25 slugs differ at both levels -- verified by diffing today's
# `build/html` digests against the r19 pins in git -- so the review below, taken
# on the bundled documents, covers the same population; only the artifact being
# hashed was wrong.
#
# Reviewed as follows, and the review is again the point rather than the
# regeneration:
#
#   Tag inventory across the 25 changed documents moves in ONE direction:
#   +60 <input>, +29 <div>, +3 <rect>, and nothing of any kind deleted (2551M's
#   -1 div is a comb container replaced by its slots). Visible text is
#   token-for-token identical in every document -- the only text-length changes,
#   2550M +3,767 and 1604CF -1, are entirely inside the embedded band-data
#   <script>, which encodes the comb bands and therefore has to grow when a form
#   gains combs.
#
#   The 60 inputs are the two producer fixes, at coordinates: 14 checkboxes and
#   comb groups a printed-box assertion named (0619-E and 0620 item 3 Amended
#   YES, 2550Q item 3 2nd quarter, 1701 ATC II016, 2200M item 12, 2200S x3, and
#   the rest), and 46 compartments on combs the source draws with round-capped
#   ticks (2550M's year comb, 2553, 1604CF, 2551M).
#
#   The 3 new rects were checked against the sheet rather than counted: all
#   three are on 2550M page 2 at x 574.92, y 568.32/656.16/670.32 -- the LAST
#   THREE segments of the right-hand column rule, whose mirror image at x 452.28
#   (v285, v286, v287) was already painted at HEAD and still is. The source
#   strokes them with a round cap that stopped 0.36pt short of its rails. Every
#   other rule in the corpus moved by exactly half its stroke width at a capped
#   end and by nothing at a butt-capped one, which is `check_stroke_caps`'s
#   probe restated over real artwork.
#
# This pin still hashes every emitted byte, so it still invalidates on every
# legitimate producer change; the design tension recorded in GOAL.md §Blocked is
# unresolved and this refresh does not resolve it.
#
# Refreshed 2026-08-07 (r21), all 53, from build/html/<slug>.html (the emitted
# document, per the r20 lesson above). Reviewed as follows before re-pinning:
#
#   Every one of the 53 documents changed, because the comb contract's
#   y0/y1 keys reverted to the SOURCE DIVIDER BAND (see the note at
#   LATTICE_PRODUCER_SHA256): every comb slot div's top/height moved back to
#   the tick band, corpus-wide. That is the deliberate cost of restoring this
#   referee's reviewed 2551Q control and the classify_band seeding; the
#   writing surface is published beside the band as writing_* and the emitter
#   does not read it yet -- reported loudly in STATUS.md as the r21 cost.
#
#   Tag inventory across all 53: +122 <input>, +162 <div>, nothing else moved
#   and nothing deleted, with visible text token-for-token identical in every
#   document. The gains are the bottom-guide-tick recognition returning
#   compartments to groups that had lost them (2316's TINs, 0605's return
#   period, 1600WP item 5, 2551M, 2553, 2550Q, 1701MS, 2200A). The only lost
#   inputs are SIX comb compartments on 2200A/C/P's bottom band -- the
#   "Machine Validation" / "Stamp of Receiving Office" boxes, refused by
#   emit.BureauReservation because the sheet's own caption reserves them for
#   the Bureau. Not one previously-refused compartment regained an input:
#   G11's constants stay refused, because comb_slot_verdicts asks its
#   questions of the compartment's writing rectangle (writing_y0/writing_y1),
#   not of the divider band the y0/y1 keys now measure -- the seam fix
#   documented at that function.
#
# Refreshed 2026-08-07 (r23), all 53, from build/html/<slug>.html. This is r21's
# declared cost being PAID, not a new change: emit.py now lays every rectangle
# it DRAWS for a comb out on the WRITING box (`emit.comb_writing_rect`) rather
# than on the divider band, so the typing surface on 2550M's item-4 TIN row is
# 14.16pt inside a 15.60pt row again instead of the 3.12pt stub r21 shipped
# (finding F186). The band is untouched and is still the source contract
# `classify_band` seeds from and the reviewed 2551Q control was signed against
# -- emit.py restates it nowhere, which its own self-test now drives in both
# directions.
#
#   Reviewed before re-pinning, and the review is unusually strong: the tag
#   inventory delta across all 53 documents is ZERO for every tag name --
#   239,562 elements before and 239,562 after, nothing added, nothing deleted
#   -- and visible text is token-for-token identical in every one. The entire
#   change is attribute values on `<div class="s">`: 2316's slot 0 goes
#   `top:8.71pt;height:6.05pt` (the tick band) to `top:0.45pt;height:13.92pt`
#   (the writing box), 2550M's `top:9.24pt;height:4.32pt` to
#   `top:0.72pt;height:11.76pt`. Every slot div moved and only slot divs moved.
#
#   HTML_RUNTIME_SCRIPT_SHA256 was re-derived and did NOT move: all three
#   pinned runtime scripts are byte-identical, which is the standing evidence
#   that a layout change did not reach the page runtime.
#
# Refreshed 2026-08-08 (r27): TEN of the 53, from build/html/<slug>.html. The
# other 43 documents are byte-identical and their pins were not touched --
# which is itself the first review finding, because two producer changes that
# could each have moved every document moved ten.
#
#   Tag inventory across the ten moves in ONE direction: -110 <input>, -22
#   <div>, nothing of any kind added, and visible text is token-for-token
#   identical in every one of the ten (312/313/238/399/330/234/366/279/446/173
#   text runs, unchanged and in the same order). Every embedded <script> body
#   is byte-identical, so HTML_RUNTIME_SCRIPT_SHA256 does not move.
#
#   The 22 divs are 11 refuted caption blocks x 2 compartments, and the 110
#   inputs are three named populations, checked against the sheets rather than
#   counted:
#
#   * 92 money decimal-bullet compartments (2000-DST 16, 2200A 20, 2200C 20,
#     2200P 20, 2200S 16), each ONE compartment of a 14-, 29- or
#     33-compartment money comb, each the third from the right with the two
#     centavos boxes to its right. The source prints the bullet INTO that
#     compartment, so it was a typing surface laid on printed ink.
#   * 16 on the 8 refuted caption blocks that had inputs -- 1606 p2's whole
#     Schedule 4 rate table, and the mastheads of 2200A/2200AN/2200C/2200P/
#     2200S/2200T(x2).
#   * 2 completing the printed rate `0 %` on 1800 p1c68 and 2550-DS p1c79,
#     2-compartment combs the sheet fills entirely.
#
#   NOT A SHRUNK WRITING SURFACE, which is the regression this pin exists to
#   catch (r22 lowered the same assertion by cutting every comb to a 3.12pt
#   stub). All 7,405 slot rectangles that survive in the ten documents were
#   compared attribute-string to attribute-string against their r26 selves and
#   ZERO moved; the only rectangles that disappear are the 22 belonging to the
#   11 refuted blocks. Per-document minimum slot height is unchanged in all
#   ten (16.32 / 14.88 / 14.52 / 12.96 / 12.96 / 14.52 / 12.96 / 12.96 / 12.84
#   / 15.09pt); only the MAXIMUM falls, from 103.83pt (1606's rate table) and
#   47-55pt (the mastheads) to a normal compartment. 2550M and 2316, the two
#   forms the writing-box check is watched on, are byte-identical documents.
#
# Re-pinned 2026-08-08 (r29) for the comb RAIL derivation described at
# LATTICE_PRODUCER_SHA256. 43 of the 53 documents move and 10 are
# byte-identical. What moves in them is comb slot RECTANGLES and nothing else:
#
#   * 764 combs' slot runs shift horizontally, by 0.02 to 0.47pt, onto the bar
#     the source rules the band with instead of the mean of every collinear
#     bar on that lattice line. No slot count and no slot height changes in any
#     of them.
#   * 9 combs on 5 documents lose their leading or trailing slot -- 1604F
#     p1c36, 1800 p1c15 and p1c26, 1801 p1c31/p1c32/p1c33/p1c112, 2200S p1c29,
#     2552 p1c28 -- because the region they covered is a printed caption or a
#     TIN dash box that the same rectangle also rules, and the comb's own rail
#     starts after it. The widest was 365.95pt over "24 TOTAL AMOUNT PAYABLE
#     (For full payment Sum of Items 20 and 23D)". None of the nine carried an
#     <input>: emit.py's pre-printed-ink rule had already refused them one,
#     which is why `inputs_over_printed_text` neither improves nor regresses
#     here. What goes is the compartment the sheet does not print, and with it
#     the cell's `data-comb-capacity`; the caption still prints.
#
#   NOT A SHRUNK WRITING SURFACE, the regression the pin above exists to catch:
#   every surviving slot's `top` and `height` is unchanged, and no minimum slot
#   height moves on any document. The independent evidence is audit.py's, which
#   reads compartment counts and rail positions from the pinned PDF's own paint
#   stream: of the 33 combs it named as defects before, 27 are gone and the six
#   named at LATTICE_PRODUCER_SHA256 remain, and it agrees on all 4,531
#   compartment counts it can evaluate.
# Re-pinned 2026-08-10 (r37, P1) for F097, two slugs only: `2200c-2018`
# (MM gains a 2-`<input>` field p1c111, YYYY a 4-`<input>` field p1c112,
# `comb_slots_match_printed` unchanged at 12/25 -- both offenders on these two
# slugs are pre-existing, byte-identical cells at unrelated y-bands) and
# `2000-dst-2018` (p1c4's 6-slot comb splits into p1c111/p1c112, still 2+4
# `<input>`s). No other slug's emitted HTML moved.
#
# Re-pinned 2026-08-10 (r37, P3) for the ruled-blank reclassification described
# at AUDIT_DEPENDENCY_SHA256. 22 documents move and 31 are byte-identical, and
# the 22 are exactly the forms carrying a published group. What moves in them:
#
#   * 118 underscore groups leave the text layer and arrive in the rule layer,
#     each as one h rule at its glyphs' own ink band (0.3 to 0.6pt thick, tone
#     from the run's own fill -- 60 black, 58 white on the four ATC pages that
#     set their blanks in white). The drawn line on paper is the same line;
#     only which layer draws it changed.
#   * The runs those groups were in split, so the text-run census falls
#     19,333 -> 19,286 (-47): a run that was ONLY a blank leaves entirely, a
#     run that shared its line with one keeps the rest, and a run holding two
#     blanks becomes two fragments (1600WP and 1706 each net +1 for that
#     reason). Run ids renumber on all 22, and with them the cell ids of the
#     47 cells a new lattice line divides.
#   * ONE new `<input>` corpus-wide, on 1706-2018 p2c183: 45,493 -> 45,494.
#     The other 117 blanks yield no writing surface -- see the P3 report; a
#     ruled blank is written ON TOP of its line, so the strip the split hands
#     back below the line is a 0.9-15.5pt sliver (33 `blank`, 7 `shaded`,
#     4 `label`, 3 `field`) and 71 bars sit where no cell wall reaches them.
#   * EXPECTED_COMBS and EXPECTED_COMBS_BY_SLUG do NOT move (4583, and every
#     per-slug count unchanged): no bar lands on a comb band.
#
# Re-pinned 2026-08-10 (r38, P4) for the baseline split described at
# AUDIT_DEPENDENCY_SHA256. ELEVEN documents move and 42 are byte-identical, and
# the 11 are exactly the forms carrying a two-baseline span: 1701-2018-
# attachment, 1701a-2018, 1701q-2018, 1702ex-2018, 1706-2018, 1707a-2021,
# 1800-2018, 2200p-2020, 2200t-2022, 2550-ds-2025, 2550q-2024. What moves in
# them is the text, box and placement of 25 runs, and nothing else:
#
#   * 24 two-baseline spans yield 25 ink-bearing runs. Each loses the leading
#     or trailing positioning space the file set on the other baseline, so its
#     text, `x0`/`x1` and advance census move on all 25.
#   * 21 of the 25 land on a different baseline -- the four that do not are the
#     ones whose odd baseline held a TRAILING space, so their first glyph was
#     already the ink. Eleven move by 2.00pt or more: 2200-P p2 ` Total Tax-`
#     +4.80, 1707-A p1 ` Calendar ` +3.72, 2550-Q p1's `Calendar`/`Fiscal`/
#     `2nd`/`3rd` and 2550-DS p1's `  2 ` +3.10, 2550-DS `Calendar`/`2nd`
#     +3.09, 1800 p1 ` No ` +2.28, 2550-DS `3rd` +2.00. The other ten move
#     0.24 to 1.20pt, two of them upward (1702-EX p2 ` % ` -1.20, 1706 p2 and
#     1800 p2 -0.24).
#   * 1 span becomes two runs (1706 p2 `1 A`, ink on two baselines 0.36pt
#     apart), so that form's text-run census is +1 and its run ids renumber.
#   * No `<input>` is added or removed on any of the 11 (45,494 corpus-wide,
#     unchanged), no comb subject moves state, and no slot rectangle moves:
#     the change is confined to the text layer.
#
# Re-pinned 2026-08-11 (r43) for F208, ALL 53, and this is the widest pin move
# in this file's history, so what was reviewed matters more than the count.
# Every document moves because almost every comb in the corpus has an outer
# compartment, and every outer compartment's own edge moves inward off the rail
# CENTRE onto that rail's painted INK EDGE. Reviewed structurally, on the
# emitted bytes, before re-pinning:
#
#   * TAG INVENTORY IS IDENTICAL IN ALL 53. Not one element of any kind is
#     added or removed anywhere -- `<input>` stays at 45,494 corpus-wide, and
#     every `<div>`, `<rect>`, `<path>`, `<template>` and `<script>` count is
#     unchanged. Compare r27's -110 `<input>`/-22 `<div>`: this change adds and
#     deletes nothing.
#   * VISIBLE TEXT IS TOKEN-FOR-TOKEN IDENTICAL IN ALL 53.
#   * The `<input>` ATTRIBUTE MULTISET IS IDENTICAL IN ALL 53, which is the
#     evidence that no fitted face moved: a comb's size is fitted to its writing
#     HEIGHT and capped at the sheet's body size, never to its slot width, so
#     narrowing two compartments renumbers no field-metric CSS class.
#   * The RUNTIME `<script>` IS BYTE-IDENTICAL IN ALL 53, so
#     `HTML_RUNTIME_SCRIPT_SHA256` does NOT move. The only script bytes that
#     move are the 15 `formgen-bands` JSON blobs, which carry the band
#     template's own `slot_x` and therefore have to state the same writing
#     edges the pre-rendered rows are laid on -- a clone rebuilt on the rail
#     centres would sit on the printed wall beside identical printed rows that
#     do not.
#   * SLOT COUNT MOVES ON NO DOCUMENT. 9,307 of the corpus's 40,185 slot
#     `style` strings change, and every one of them is a first or last
#     compartment of its comb; every internal divider is byte-identical.
#     2551M p1c74 is the shape: slot 0 `left:0pt;width:11.04pt` ->
#     `left:0.36pt;width:10.68pt` (half of the 0.72pt wall the sheet paints at
#     x 238.92-239.64, under which it prints the `C` of `28C`), slot 1
#     `width:10.44pt` -> `width:9.96pt`, the divider between them unmoved at
#     11.04pt, and the cell's own rect, capacity, pitch, ids and classes
#     untouched.
#   * EXPECTED_COMBS, EXPECTED_COMBS_BY_SLUG and
#     EXPECTED_RETAINED_SUBJECTS_BY_SLUG do NOT move (4,587 subjects, 33
#     retained, 4,554 comb cells, form for form): `slot_x`, `divider_x` and
#     every compartment count are untouched, so no ledger topology digest
#     moves either.
#
# Re-pinned 2026-08-11 (F207) for ONE slug, `1701a-2018`. The other 52
# documents are byte-identical, which is the first thing reviewed: the change
# is to `emit.PrePrintedInk`'s "printed in" test, which every form's every cell
# is asked, and it moves two cells on one form.
#
#   * TWO `<input>` LEAVE THE CORPUS, 45,335 -> 45,333, both on 1701A: p1c227
#     and p2c208, the full-width strips item 19 sets the second line of each of
#     its two captions across ("of deduction", "[available if gross
#     sales/receipts ... (P3M)]"). Each strip's cell goes `class="c f"` +
#     `data-field-kind="text"` + `data-field-name` to `class="c"` +
#     `data-preprinted="true"`, which are the first two cells in any FORM
#     document to carry that attribute -- the 29 statutory-bracket cells the
#     same rule refuses are all relocated into their guide and emitted in
#     neither document.
#   * NOTHING ELSE MOVES IN ANY DOCUMENT. Cell count 393 -> 393 on 1701A and
#     unchanged elsewhere; no other cell's class, attribute string or input
#     count changes anywhere in the corpus; visible text is token-for-token
#     identical; every `<template>` and `<script>` is byte-identical, so
#     `HTML_RUNTIME_SCRIPT_SHA256` does NOT move.
#   * 1701A's `form.css` loses `.fh0` (0.61pt) and `.fh1` (1.06pt) and the
#     remaining classes renumber. Those two metrics existed only for these two
#     inputs: `writing_box_clear_of_printed_ink` had already trimmed each box
#     down to the sliver under the caption's own line box, so what is removed
#     is a 584pt-wide typing surface 0.61pt tall laid across printed text --
#     not a writing surface anyone could use, and the reason `check_inputs_
#     over_printed_text` never saw it (F207 is `audit_blind`).
#   * COMB CENSUSES DO NOT MOVE: 4,587 subjects, 33 retained, 4,554 comb
#     cells, form for form, and every compartment verdict is identical -- 369
#     compartments refused before and after, the same 369. The "printed in"
#     test is shared with `slot_constant`/`slot_caption` on purpose, so this
#     was measured per compartment over all 4,554 comb cells rather than
#     assumed.
# Re-pinned 2026-08-11 (F209) for the growable-band reading-order fix.
# `emit.emit_page` used to write the WHOLE `<div class="layer-cells">`
# static-cell layer, close it, and only THEN append every band's
# `<template>` + `<div class="band" id="band-content-...">` -- so a band
# whose rows print mid-page was always last in DOM/tab order regardless of
# where the sheet puts them (worst measured: 1600-pt-2018 p1, a tab from
# p1c195 at top 829.0pt to p1c50 at top 338.9pt, a 490pt jump backward,
# with p1c50 a member of band p1g0). `emit_page` now splits
# `page_layout["cells"]` around each band at the band's own (y0, x0) -- the
# SAME key lattice.py sorts cell arrays by -- so a band lands exactly where
# its first row would have sorted; a page with N bands emits N+1
# `<div class="layer-cells">` sibling runs instead of one, never nested,
# each a flat sibling run of `<div>` cells exactly as before.
#
# 22 of 53 documents move -- exactly the forms carrying a growable band, by
# build/layout/*.layout.json's own inventory -- and 31 are byte-identical.
# Reviewed structurally, on the emitted bytes, before re-pinning:
#
#   * TAG INVENTORY MOVES BY EXACTLY ONE KIND, ONE DIRECTION: `<div>` +42
#     corpus-wide and nothing else -- not `<input>` (45,333, unchanged), not
#     `<template>`, `<script>`, `<svg>`, `<rect>`, `<path>`, `<image>`, nor
#     `<div class="t">` (text runs). 42 is exactly the corpus's own
#     growable-band count summed across all 53 layouts: every new `<div>` is
#     one `<div class="layer-cells">` re-opening, one per band, whether or
#     not that band's own trailing segment turns out empty.
#   * CELL MARKUP IS BYTE-IDENTICAL: concatenating every page's
#     `<div class="layer-cells">` segments back into one string (stripping
#     only the wrapper `<div class="layer-cells">`/`</div>` pairs
#     themselves) reproduces the pre-move single-segment string exactly, for
#     all 22 documents, every page -- no cell id, class, attribute, style or
#     input changed anywhere, only where the wrapper divs fall.
#   * EVERY BAND'S OWN CHUNK IS BYTE-IDENTICAL: each
#     `<template id="band-template-*">...</template><div class="band"
#     id="band-content-*">...</div>` pair, matched by band id, compares
#     byte-for-byte equal between the pre-move (page-end) and post-move
#     (in-position) documents -- only its position on the page changed.
#   * READING ORDER, MEASURED ACROSS ALL 53: every `<input>` mapped to its
#     cell's `(y0, x0)` and clustered into rows (half the page's own
#     growable-band `row_pitch_pt` where a band is present, 1.0pt elsewhere)
#     shows 3,363 same-page row inversions across 20 of the 53 documents
#     before this change (worst: 1600-pt-2018 p1, 175) and ZERO across all
#     53 after.
#   * `HTML_RUNTIME_SCRIPT_SHA256` does NOT move: `BAND_JS` is untouched, and
#     `setBandRows`/`rowCells`/`applyFields` look a band up by
#     `#band-content-<id>`/`#band-rules-<id>`/`#band-template-<id>` and
#     operate on cloned per-cell subtrees, never on page-wide DOM position,
#     so where a band sits on the page was already irrelevant to the runtime.
#   * COMB CENSUSES AND `inputs_over_printed_text`/`comb_slots_match_printed`
#     DO NOT MOVE on the regenerated corpus: 4,587 subjects, 33 retained,
#     4,554 comb cells; `inputs_over_printed_text` still 3 forms / 6
#     offenders (1604cf-2008 x1, 1701ms-2024 x1, 2316-2021 x4);
#     `comb_slots_match_printed` still 10 forms / 19 offenders. The SlotParser
#     nesting contract (`_push_render_element`) needed NO change: it already
#     binds `cells`/`text-layer`/`band` to `parent_roles == ["html", "body",
#     "page"]` with no cardinality check, so more than one
#     `<div class="layer-cells">` per page was already grammatical --
#     verified by feeding it a synthetic two-segment page before relying on
#     it.
# Re-pinned 2026-08-11 (T3+T4, F213). All 53 move, and only for the reason
# below: the inline runtime script is IN every document, so growing
# FIELD_DEBUG_JS (the tone split, F213) and adding a fourth script,
# TAB_DEBUG_JS (`?debug=tab`, T3), changes every document's bytes by the same
# fixed amount regardless of what the rest of the page contains.
#
#   * SCRIPT ONLY, MEASURED: `html_bytes` in every one of the 53
#     `provenance.json` files grew by EXACTLY 13,346 -- 1701-2018 (1,668,376
#     -> 1,681,722), 0605-1999 (214,922 -> 228,268), 2551q-2018 (543,764 ->
#     557,110), 2553-1999 (249,003 -> 262,349), every other form the same
#     delta -- and that number is independently reproduced by summing the two
#     causes: `len(FIELD_DEBUG_JS.encode())` grew by 7,254 bytes (the tone
#     split, `pageCensus`/`census`, the paint-source fix, the legend text),
#     and the added `"<script>\n"+TAB_DEBUG_JS+"\n</script>\n"` is 6,092
#     bytes; 7,254 + 6,092 = 13,346, exactly. A constant per-document delta
#     is only possible if nothing else in the document changed size, which a
#     variable-length change to cell markup, inputs or geometry could not do.
#   * `HTML_RUNTIME_SCRIPT_SHA256` moves (see that pin's own comment): the
#     band data runtime (first hash) and field runtime (second hash) do NOT
#     move -- untouched by this package -- only the field debug overlay
#     (third) and the new tab-walk viewer (fourth, new).
#   * COMB CENSUSES, `inputs_over_printed_text`, `comb_slots_match_printed`
#     and the corpus's own input count DO NOT MOVE: measured via
#     `audit.py --assertions-only` against the regenerated corpus --
#     4,587 subjects / 33 retained / 4,554 comb cells, `inputs_over_printed_
#     text` 3 forms / 6, `comb_slots_match_printed` 10 forms / 19, 45,333
#     inputs -- all unchanged from the T1/T2 baseline this package started
#     from. Cell markup, field markup and band markup are produced by code
#     this package never touches (`emit_page`'s `pages`/`cells` construction,
#     `cell_markup`, `field_json`, `band_rects` -- reviewed by inspection: the
#     only lines this package added to `emit_page` append two more entries,
#     unconditionally, to the tail list that already carried
#     `FIELD_DEBUG_JS`), and the corpus-wide invariants above are the
#     measured proof that nothing downstream of them moved either.
# Re-pinned 2026-08-11 (T5c, F148/F149). 19 of 53 move; the other 34 are
# byte-identical. Cause: extract.py now stamps every rule with `origin`
# (`RULE_ORIGIN_VECTOR` or `RULE_ORIGIN_TEXT_UNDERSCORE`, the bar a run of
# underscore glyphs draws -- see `ruled_blank_bars`), and a `label` cell whose
# paper carries a structural, singly-owned underscore-drawn rule now earns ONE
# `<input>` per rule, seated on the rule's own line (`RuledBlankWriting`,
# `ruled_blank_field_box` in emit.py). Moved: 1600wp-2010, 1601c-2018,
# 1603q-2018, 1700-2018, 1701-2018, 1701a-2018, 1701ms-2024, 1701q-2018,
# 1702mx-2018c, 1706-2018, 1801-2018, 2200a-2020, 2200an-2018, 2200m-2018,
# 2200p-2020, 2200t-2022, 2550-ds-2025, 2550q-2024, 2551q-2018 -- exactly the
# 19 forms whose corpus carries a `role == "structural"` underscore-drawn rule
# owned by exactly one `label` cell; every other underscore-drawn rule in the
# corpus is either `role == "knockout"` (58, white-on-colour lettering inside
# a legend/swatch on 1600-PT/1600-VT/1606/1706, never inside any cell the
# lattice cut) or claimed by two `label` cells at once (2550Q p2's fraction
# bar under "Total Sales", refused rather than guessed at -- see
# `RuledBlankWriting`). 54 cells gain an input; 58 `<input>` elements are
# added, because three cells carry more than one blank on their own line
# (1600wp-2010 "Page ___ of ___", 1706-2018 "___ % X ___ = ___" and
# "____ = ____"). `HTML_RUNTIME_SCRIPT_SHA256` does NOT move: no runtime
# script text changed, only the pre-rendered cell markup these 19 documents
# carry.
#
# Re-pinned 2026-08-11 (T5b+T5d, F211/F212). 43 of 53 move; the other 10 are
# byte-identical (0605-1999, 1600wp-2010, 1604cf-2008, 1701-2018-attachment,
# 1701-2018-conso, 1702mx-2018c-attachment, 2316-2021, 2550m-2007,
# 2551m-2002, 2553-1999 -- every one of them either carries none of either
# target caption or binds it to nothing this session's rules will claim; see
# `SignatureBoxWriting` and `SignatureLineBinding` in emit.py for why each is
# refused rather than guessed at). Two causes, landed together because they
# touch the same cells:
#
#   * F211: `emit.py`'s `field_verdict` gains a `signature-box` branch
#     (`SignatureBoxWriting` / `signature_box_field_box`) that gives a
#     `label` cell an input when its only printed ink is a top-left caption
#     dedicating the box to the taxpayer's own signature ("For Individual:"
#     / "For Non-Individual:") -- routed through the existing
#     `BureauReservation` check rather than around it, so none of the 71
#     Bureau-captioned boxes in the identical bordered-box population gain
#     one. 54 `label` cells across 27 forms gain a NEW `<input>`.
#   * F212: `emit.py`'s `FieldPlan` re-seats every box a "Signature (over|
#     and) Printed Name..." caption binds (`SignatureLineBinding`,
#     `seat_signature_line`) to a single-line strip at the CELL's own
#     bottom, centred with an inline `text-align:center` on the plain-field
#     input -- no new CSS class, no new declaration on an existing selector.
#     75 boxes across 43 forms are bound: the 54 F211 creates (every one
#     also carries this caption below it) plus 21 pre-existing `field`
#     cells, `1701-2018` `p1c125` among them (unchanged in kind -- it
#     already worked -- only its own `<input>`'s inline `style` moves).
#
# So 54 NEW `<input>` elements are added and a further 21 PRE-EXISTING ones
# have their inline `style` attribute changed; no cell's `id`, `class` or any
# other attribute moves, and no comb is touched. `inputs_over_printed_text`
# stays 3 forms/6, `comb_slots_match_printed` stays 10/19, comb censuses stay
# 4,587/33/4,554 unmoved. New standing corpus-wide self-tests
# `emit.signature_box_corpus_assertions` and
# `emit.signature_line_corpus_assertions` (run by `emit.py --self-test`)
# re-derive both claim sets against every `build/ir` this checkout has.
# `HTML_RUNTIME_SCRIPT_SHA256` does NOT move: no runtime script text changed,
# only the pre-rendered cell/field markup on these 43 documents.
#
# Re-pinned 2026-08-12 (W1, F206). `emit.py`'s `field_verdict` gains a
# `knockout-specify` branch (`KnockoutSpecifyWriting` / `knockout_specify_
# field_box`) that gives a `label` cell an input when its own paper carries a
# knockout-over-tint band -- the widest ink-free rectangle whose topmost
# paint is a knockout fill directly over, or immediately beside within the
# same row, a decorative tint, at or past the form's own two-glyph line
# width -- beside a caption containing "specify". Corpus-wide this claims 23
# cells across 11 forms; 22 already carried an input from an earlier rule
# (`RuledBlankWriting`, checked first) and are untouched byte-for-byte.
# **Only 1 slug moves: `1801-2018`.** `p1c197` ("Others (specify)", item 24)
# gains one NEW `<input>`, seated on the knockout's own rectangle
# (516.82-584.38pt, the same row's decorative tint at 411.91-471.7pt beside
# it); no other cell on any of the 53 documents changes. The other 8 cells
# F206 measured as this marker's own residue -- 0605-1999 p2c58/p2c121,
# 2553-1999 p1c37/p1c42/p1c47/p1c52, 1600wp-2010 p1c30/p1c33, every one an
# ATC-code or rate cell on a reference table -- are proven unchanged: none
# carries "specify" in its caption, so `KnockoutSpecifyWriting` never
# constructs a band for them regardless of geometry. `HTML_RUNTIME_SCRIPT_
# SHA256` does NOT move: no runtime script text changed, only `1801-2018`'s
# own pre-rendered cell markup.
# Re-pinned 2026-08-12 (W2, F151): `emit.py`'s `field_verdict` gains a
# `row-number` branch (`RowNumberWriting` / `row_number_field_box`) that gives
# a `label` cell an input beside its own bare row number -- "1", nothing else
# -- for every row where that cell shares its row with a `field` cell and its
# own trailing blank clears the form's own `line_width_pt`
# (`lattice.min_fillable_line_metrics`) at 1.0x, no new constant, the same
# bound the sliver rule already spends. P2's measured corpus census was 296
# candidates -> 56 across 23 forms on the r38 tree; re-measured on this tree,
# after ~35 intervening commits (F148/F149 ruled-blank, F210 checkbox-square,
# F211/F212 signature-box, F206 knockout-specify, and several lattice-level
# ink/wall/pre-printed-text fixes, each capable of reclassifying a `label`
# cell), the rule's own claim set is 61 across 13 forms (`emit.
# row_number_corpus_assertions`, run by `emit.py --self-test`); 12 of those
# 61 (2200m-2018's 4, 1702mx-2018c's p2c278/p2c280 + p4c196/198/200/202/204/
# 206) are ALREADY fillable via `RuledBlankWriting`, which landed after P2's
# own measurement, so the NET NEW gain this package ships is 49 cells across
# 11 forms -- listed below, each also gaining an EXPECTED_HTML_STRUCTURE_
# SHA256 pin. The four Schedule D anchors this closes F151 for -- 1701-2018-
# conso p2c132/p2c136/p2c140/p2c144 -- are among them, verified typeable in a
# real Chromium page (goto/Tab/type/read-back). Excludes, by construction and
# verified as a count: all 228 of the corpus's narrow (13-16pt) item-number
# boxes sharing a row with a field cell (BIR's own "12" inside a box barely
# wider than two digits; P2 measured 188 on the r38 tree, the same drift as
# above), and 1701-2018-conso's Schedule C p2c97/p2c103/p2c109 (pre-printed
# category names, not bare numerals, F151's own refuted half). One real
# defect this measurement FOUND and fixed before shipping: `row_number_band`
# originally trimmed only the CANDIDATE cell's own leading ink, the same way
# `writing_box_clear_of_printed_ink` trims ink hanging in from outside a box
# -- but 0605-1999 p1c81 sits under a wide "Calendar ... Fiscal" checkbox
# caption assigned to a NEIGHBOURING cell whose own glyphs physically
# overlap p1c81's rectangle (the `assign_points`/`printed_box_peers_all_
# fillable` shape CLAUDE.md already documents, pointed a new way), so the
# unguarded band claimed a "blank" that was not blank and this rule's own
# published input landed on top of printed ink. `row_number_band` now
# refuses any candidate whose band carries ANY intrusion from ANY run's own
# ink, corpus-wide, not just this cell's assigned one -- `inputs_over_
# printed_text` stays at its own pre-existing 2 forms/5 (1604cf-2008,
# 2316-2021), `comb_slots_match_printed` stays 10/19, and comb censuses stay
# 4,587 subjects/33 retained/4,554 comb cells (row-number never touches a
# comb cell by construction). Input count rises 45,468 -> 45,520 (+52; 49 net
# -new cells, +3 more because three of them sit in a growable band and are
# mirrored into that band's own <template> blueprint). New standing
# corpus-wide self-test `emit.row_number_corpus_assertions` (run by
# `emit.py --self-test`) re-derives the claim set against every `build/ir`
# this checkout has and fails if any claimed cell lacks a typing surface.
# Re-pinned 2026-08-12 (W2 operator correction). The row-number rule promoted
# 49 label cells; 31 of them sit on >=70% decorative tint and are the sheet's
# OWN printed row index, not a writing surface -- 1621-2019 p2's "Seq. No. (A)"
# column is the clean case, a grey band whose 1..5 the Bureau prints. Those 31
# now take the same refusal the ordinary field path already applies ("a blank
# the source shaded is not a blank either"), which this branch returned before
# ever reaching. 18 cells keep their inputs, including all four of F151's
# Schedule D Description cells, which the official sheet leaves white. Inputs
# 45,520 -> 45,487 (-33: the 31 cells plus 2 growable-band template mirrors).
# Nothing keys on a form code; the discriminator is the source's own tint read
# through the existing gate. `row_number_corpus_assertions` now asserts BOTH
# directions -- unshaded claims must have an input, shaded claims must not --
# so the exclusion cannot rot into a silent skip.
#
# Re-pinned 2026-08-12 (W3, F064): `lattice._reunify_comb_band` gives a comb
# band its own rectangle when the general cell walk found none, claiming only
# paper the comb's own dividers own. Three documents move: 1707-2021's item
# 8A comb (F064's own named defect, 25 slots), 1707a-2021's matching shape
# (same 25 slots) and one of 2551m-2002's four retained subjects (4 slots).
# The other 50 documents are byte-identical. Input count 45,487 -> 45,541
# (+54: 25+25+4). Comb censuses: EXPECTED_COMBS/EXPECTED_COMBS_BY_SLUG do NOT
# move (4,587; no ledger entry is added or removed, three existing ones
# change state and subject_key); EXPECTED_RETAINED_SUBJECTS_BY_SLUG moves
# 33 -> 30 (1707-2021 leaves the table, 1707a-2021 2 -> 1, 2551m-2002 4 -> 3),
# measured directly: `comb_subjects_active` 4,554 -> 4,557,
# `comb_subjects_retained_unresolved` 33 -> 30. `inputs_over_printed_text`
# and `comb_slots_match_printed` are unmoved (neither touches a cell either
# assertion's own offender list names).
EXPECTED_HTML_STRUCTURE_SHA256 = {
    # Re-pinned 2026-08-12 (W4b, F221 case 1): `emit.SignatureRuleWriting`
    # gives a `label` cell an input when it owns a vector-drawn signature
    # line at its own bottom wall, named by a "Signature over Printed
    # Name..." caption in the cell directly below. Six documents move:
    # 0605-1999, 1604cf-2008, 2550m-2007, 2551m-2002, 2553-1999 (F221's own
    # named case 1, 8 rules across 8 cells) and 2316-2021 (its own item 55,
    # reached by the identical shape without being told to look for it, 1
    # rule). The other 47 are byte-identical. Comb censuses (EXPECTED_COMBS,
    # EXPECTED_COMBS_BY_SLUG, EXPECTED_RETAINED_SUBJECTS_BY_SLUG) do not
    # move -- this mechanism never touches a comb cell, verified directly.
    # Re-pinned 2026-08-13 (W6, F227): `emit.field_box`'s comb branch now
    # calls `comb_writing_top_clear_of_printed_ink`, the identical ink trim a
    # plain field's own writing box already had, and `intrusions` now reads
    # each glyph's OWN measured outline (`glyph_ink_em`) where the source
    # states one instead of restating a run's shared line box for every
    # character in it. Twelve documents move: 1604cf-2008 and 2316-2021 (the
    # five named F227 offenders, now clear), 1701ms-2024 (one further comb
    # the same reach finds, not previously named), and 1600wp-2010, 1604f-2018,
    # 1706-2018, 1800-2018, 2200an-2018, 2200p-2020, 2200t-2022, 2550m-2007,
    # 2551m-2002 (plain-field writing boxes that were over-trimmed against a
    # run's shared line box and now measure against their own glyphs' real
    # ink instead, recovering real typing area). The other 41 are
    # byte-identical. Comb censuses (EXPECTED_COMBS, EXPECTED_COMBS_BY_SLUG,
    # EXPECTED_RETAINED_SUBJECTS_BY_SLUG) do not move -- no slot rectangle,
    # divider or slot count changes on any comb; only the INPUT nested inside
    # a slot or a plain field's own box gains or loses an inset.
    # Re-pinned 2026-08-13 (W9, F226): `emit.SignatureRuleWriting`'s caption
    # search now bridges a small vertical gap between a rule-owning `label`
    # cell and its caption -- smaller than the form's own `glyph_height_pt`
    # and carrying no printed ink across the rule's own x-extent -- not just
    # the exact shared wall F221/W4b already claimed. One document moves:
    # 2316-2021 (item 53's rule h180, bridged across a 1.32pt blank sliver
    # with no caption of its own, and item 54's rule h183, bridged across a
    # 0.54pt span the lattice made no cell for at all). The other 52 are
    # byte-identical. Comb censuses (EXPECTED_COMBS, EXPECTED_COMBS_BY_SLUG,
    # EXPECTED_RETAINED_SUBJECTS_BY_SLUG) do not move -- this mechanism
    # never touches a comb cell, verified directly.
    # Re-pinned 2026-08-13 (Z3, F065): F065's own stated cause was wrong, and
    # it was fixable. 1707-2021's item 9 ruled blank is drawn in an EMBEDDED,
    # subset-tagged TrueType face (`ABCDEE+Arial Narrow`), not an unembedded
    # unresolvable one; `substitutable_faces` registered it under its exact
    # `/BaseFont` while every span asked for it by its tag-stripped name
    # (MuPDF's own rawdict strips the PDF spec's subset prefix,
    # `extract.SUBSET_TAG_RE`), so the key never matched. Fixed by
    # registering the stripped name too (additive; an exact-key hit is never
    # displaced), plus a second, independent fix: every embedded, buffer-
    # loaded face answers `glyph_bbox` with its own whole font box, never a
    # real per-glyph outline (`glyph_ink_box`'s own documented barrier,
    # confirmed corpus-wide by this package), so the underscore's own
    # outline is instead hand-parsed from the embedded program's own
    # `head`/`loca`/`glyf`/`hmtx` bytes (`extract.embedded_glyph_outline`),
    # scoped to the ruled-blank path only, cross-checked against the file's
    # own stated advance. `ruled_blank_groups`/`published`/`refused` moves
    # 119/118/1 -> 119/119/0 -- every ruled blank in either corpus now
    # publishes. One document moves: 1707-2021 (build/html/1707-2021.html;
    # the tracked bundle is forms/extra/1707-2021, in_corpus=false). The
    # other 52 are byte-identical. Comb censuses (EXPECTED_COMBS,
    # EXPECTED_COMBS_BY_SLUG, EXPECTED_RETAINED_SUBJECTS_BY_SLUG) do not
    # move -- this mechanism never touches a comb cell, verified directly.
    # Input count 45,548 -> 45,549 (+1). `inputs_over_printed_text` stays 0
    # forms/0 offenders. Browser-verified: cell p1c214, Tab press 64, typed
    # and read back verbatim.
    "0605-1999": "8a72b9c32f2a2dc5001f713f967605ba7c914b4f19a9e72dd7c60ed1c5bab4e0",
    "0619e-2018": "f2575f26a27d2e58a9ecbba461c67bdb7b293e270274d589da1bfd433e4ace51",
    "0619f-2018": "4d0b8447d14f56c47becc9aff4806562a25bd2012b10383b36f7caae5de89519",
    "0620-2019": "92697df81ac7b1b2a3dddb8e25143b6bd9f70f9077cbe4e654d6b4bf3d546127",
    "1600-pt-2018": "8f5056587fb0175179364ba1ce84a0d14a4eda4b2369de5d3d3e719a72de95b7",
    "1600-vt-2018": "f69ebf582b846345a32e34d91d7e7b0f6ff589d13dbc4a51e6094ccdc3fad08d",
    "1600wp-2010": "ab1e006ade827451181a305fc064f95515dda96fcc4d07a50ee804eb3326e54c",
    "1601-fq-2020": "4a4cc47645c07ab8a4430d2e8ab1212cbbe02b1d25f73c0ec2c379908b6866d0",
    "1601c-2018": "ce9361ba0f720c6f25e95de9f26eb315c2837730baef86c975f22f310be7ab9e",
    "1601eq-2019": "b9ebe5e60b4b0583755c3320d4197c3bea6b1f2d9179e75433ac04249191294f",
    "1602q-2019": "68ff4f254ca15d3124646099b21d6c3088f8cee8ec4f08a694a50ea9515b4fd1",
    "1603q-2018": "c0447994e444036275ff6297e69d5378e412242be86be673888040d25aa7f6f7",
    "1604c-2018": "1115dd91d6f2a985749a487aedf7c142bc07a0d825c2585b5952610d14f53563",
    "1604cf-2008": "9719777bc0cacb3be6f39333fff4ecbe01422f0c907485db6d0db262f293a965",
    "1604e-2018": "64e2ea8da9a38f21df98cdbadc861206b7fd8baeedd8387c5dc98f771f5f821d",
    "1604f-2018": "60ae661e938892a4186f25f7febb3eb846882a878d42717efa3d25ddad876864",
    "1606-2018": "4e7d51d0bde8223137156140882cf9c3015afbe2f00cd31b5f7f004c45d92099",
    "1621-2019": "36bf4f8cc2a094eb505623a49bbe29dba4b2709364ea224f076200bbb2ef84d3",
    "1700-2018": "a44b168534770580efb653f3c3fe9f20e35481deac45e0356943a1f468051500",
    "1701-2018-attachment": "a3827477a27630219b0775b256184102dcc7fe53824354e51aeb97ab39c8803f",
    "1701-2018-conso": "c3702a3e7dd82f2fb8e6a99bc4a27482b9b239ed8b43fd93e4fcbaebea83084a",
    "1701-2018": "dd4d02bcb9378e9358530370605da09da5e0d5b6e76b919738e18e187ce8059c",
    "1701a-2018": "8c0cfa010fe6f871855b36c913497f449afdfecc2407b0f4727891bef7a272ad",
    "1701ms-2024": "adb7a7d24eb443c33b7ae72679be5a298f63bd79a916f15f3018c9e99e6b829d",
    "1701q-2018": "37b927b03a97a9364a9ff5f043a720125d44b22326eb2d23815da1cc0264c645",
    "1702ex-2018": "e0fd2ae0f9ce22a0079c5a861bb8deff665fc6e9d9e815dc54e770e46650cff0",
    "1702mx-2018c-attachment": "3a85eb3f58daa74a9aa68e12e67c322dd56fef63e917a63a58a7f4f1053568f1",
    "1702mx-2018c": "24fd0667582662a624ff0d030dbe460b148cea2ec50c95f78c0dc08070f17996",
    "1702q-2018": "50d00a5a72c815302db5658b6f1e9f17993d083495e715c1c3ab1f0eec28450c",
    "1702rt-2018c": "a3db97261ed811e522beaa515470b048a9232559e5c60a84eeaf9f2eae761ee7",
    "1706-2018": "17280da2c6b7467d303a2dcec514cb3f28873862bd6a0859891301213aff5ef4",
    "1707-2021": "b6f4ef2d3c0a918826d1f3ce00df1067e41a65c373650d6e04dfd6a5c4aceae7",
    "1707a-2021": "5dda59a6001f9ddb3fa373af6eb1894a29c8271b7bd9e377ea4938d5f05a158d",
    "1709-2020": "0dfb8ad6079e8739eee88f31710d61c7afccd7f011acfc1cd30b9f0618bc48bf",
    "1800-2018": "a3efcf1b2f0738afd9fd103b269bccff01f5ed02187a8ee18495a5cc4c2fb21d",
    "1801-2018": "392bd8cca242071af0ef4df9c8b8210228b528e46e4b6ab58487b813a26a1cf9",
    "2000-dst-2018": "0a013b34da94170de5f892d86a941db6efec0050654194bd42df4a708d29477b",
    "2000-ot-2018": "af310b4678190881c92d2df96a2511cef5f17cb2b9eddad382e461305ee62162",
    "2200a-2020": "edf9ab2debf1836b91d41aaf45f866d09e87f7c3fa2230bdcf53f5529a864226",
    "2200an-2018": "b99630b147eb9b2e7e727d96fd48c1909661fca045e7df26967f9b2c67b2f878",
    "2200c-2018": "4be680a112b2474a05fda74b31003275d0f8d16a85d14433ed633bc31c458e72",
    "2200m-2018": "efefd832ce29747d50b2f9cdc55637e339b77dbae6ffb322523b204dbd9b446b",
    "2200p-2020": "6c558749f0cae83b82bb9484831582807c721dfb507f4e743ba08c2f896b444c",
    "2200s-2018": "5e7f5686e19aa361614f6e9b0b20f783e8e17433d1e825473ae24e0b1e7451fc",
    "2200t-2022": "ad66c8a6e4d3195a6a3b4ba181c730f81cad5f3f16978ccdc514fa84ff2dd521",
    "2316-2021": "42e9979b5032e1eb77de4ae7290b68cd6fabfdb2168b86d93f4261356741d674",
    "2550-ds-2025": "ca244dfa8be687e2d11c1951f4aed6d37f908d2c2b476c9488af3025def19074",
    "2550m-2007": "135edefefe6fd7950e0077bf7af7cfc405bfd2701926bf7ff18c2f8f5fd73632",
    "2550q-2024": "12b3ba5c14757efb3bf38befd17457f723423574fce98163eb6e7a1cce3c24fa",
    "2551m-2002": "7eaff42dc757675f661be5609d64b9926232315b8f8a006405d9df8bb9ad818b",
    "2551q-2018": "10389fc047d04e83a6b3175f0c37ca4b7ad99015d17f873b88bf71254d669ba3",
    "2552-2018": "589c11d4c819e77f2f21ecd63093e476ec616cca67f40eb75fcdba4f90eade5b",
    "2553-1999": "e7156fc6438f75e4a218ba2275e28d5cd3e63448f6d43e4a2b6015ab1e6d7997",
}
if set(EXPECTED_HTML_STRUCTURE_SHA256) != set(EXPECTED_COMBS_BY_SLUG):
    raise RuntimeError("HTML structural pins disagree with the referee corpus")


class RefereeError(RuntimeError):
    """A form or corpus cannot be measured with complete provenance."""


@dataclasses.dataclass(frozen=True)
class Matrix:
    """SVG affine matrix: x'=a*x+c*y+e, y'=b*x+d*y+f."""

    a: float = 1.0
    b: float = 0.0
    c: float = 0.0
    d: float = 1.0
    e: float = 0.0
    f: float = 0.0

    def then(self, child: "Matrix") -> "Matrix":
        """Return this matrix applied after ``child`` (self * child)."""
        return Matrix(
            self.a * child.a + self.c * child.b,
            self.b * child.a + self.d * child.b,
            self.a * child.c + self.c * child.d,
            self.b * child.c + self.d * child.d,
            self.a * child.e + self.c * child.f + self.e,
            self.b * child.e + self.d * child.f + self.f,
        )

    def point(self, x: float, y: float) -> tuple[float, float]:
        return (self.a * x + self.c * y + self.e,
                self.b * x + self.d * y + self.f)

    def stroke_scale(self) -> float:
        """Largest singular value of the affine linear part.

        A determinant/geometric-mean scale is not conservative under
        anisotropic transforms: ``scale(10, 1)`` would report only sqrt(10)
        even though a stroked curve can expand tenfold in x.  The largest
        singular value bounds the transformed stroke in every direction.
        """
        trace = self.a * self.a + self.b * self.b + self.c * self.c + self.d * self.d
        determinant = self.a * self.d - self.b * self.c
        discriminant = max(0.0, trace * trace - 4 * determinant * determinant)
        return math.sqrt(max(0.0, (trace + math.sqrt(discriminant)) / 2))

    def is_similarity(self) -> bool:
        """Whether the linear part preserves angles up to a uniform scale."""
        first = self.a * self.a + self.b * self.b
        second = self.c * self.c + self.d * self.d
        dot = self.a * self.c + self.b * self.d
        scale = max(1.0, first, second)
        return (abs(first - second) <= 1e-10 * scale
                and abs(dot) <= 1e-10 * scale)


@dataclasses.dataclass(frozen=True)
class Paint:
    x0: float
    y0: float
    x1: float
    y1: float
    tone: float
    order: int
    kind: str
    element: str
    clipped: bool = False

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        return self.y1 - self.y0

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2

    def covers(self, x: float, y: float) -> bool:
        return self.x0 <= x <= self.x1 and self.y0 <= y <= self.y1


@dataclasses.dataclass(frozen=True)
class UnsupportedRegion:
    x0: float
    y0: float
    x1: float
    y1: float
    reason: str
    element: str
    tone: float | None = None
    order: int = -1
    clipped: bool = True


@dataclasses.dataclass
class SvgPage:
    width: float
    height: float
    paints: list[Paint]
    unsupported: list[UnsupportedRegion]
    sha256: str


def sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def emitted_structure_sha256(payload: bytes) -> str:
    """Hash every emitted byte; any change requires an explicit pin review."""
    return sha256_bytes(payload)


def canonical_digest(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def referee_tuple_digest(cells: Sequence[dict[str, Any]]) -> str:
    tuples = [
        [
            cell["cell"],
            cell["subject_key"],
            cell["referee"]["status"],
            cell["referee"].get("compartments"),
            cell["referee"].get("source_divider_x"),
            cell["referee"].get("positions_match"),
        ]
        for cell in sorted(cells, key=lambda item: item["subject_key"])
    ]
    return canonical_digest(tuples)


def validate_2551q_referee_golden(cells: Sequence[dict[str, Any]]) -> None:
    if len(cells) != EXPECTED_COMBS_BY_SLUG["2551q-2018"]:
        raise RefereeError("2551Q reviewed referee tuple count changed")
    by_cell = {cell["cell"]: cell for cell in cells}
    if len(by_cell) != len(cells):
        raise RefereeError("2551Q reviewed referee tuple identities changed")
    for cell_id, expected in REVIEWED_2551Q_EXPLICIT_COMPARTMENTS.items():
        cell = by_cell.get(cell_id)
        if (cell is None
                or cell["referee"].get("status") != "measured"
                or cell["referee"].get("compartments") != expected):
            raise RefereeError(
                f"2551Q reviewed control changed: {cell_id} != {expected}")
    actual = referee_tuple_digest(cells)
    if actual != REVIEWED_2551Q_REFEREE_TUPLES_SHA256:
        raise RefereeError(
            "2551Q reviewed 105-tuple referee digest changed: " + actual)


def attach_report_digest(report: dict[str, Any]) -> None:
    if "payload_sha256" in report:
        raise RefereeError("report already carries a payload digest")
    report["self_digest"] = {
        "algorithm": "sha256",
        "canonicalization": "json-sort-keys-compact-utf8",
        "excluded_field": "payload_sha256",
    }
    report["payload_sha256"] = canonical_digest(report)


def report_digest_valid(report: dict[str, Any]) -> bool:
    payload_sha256 = report.get("payload_sha256")
    if not isinstance(payload_sha256, str):
        return False
    without_digest = {
        key: value for key, value in report.items()
        if key != "payload_sha256"
    }
    return payload_sha256 == canonical_digest(without_digest)


def report_bytes(report: dict[str, Any]) -> bytes:
    if not report_digest_valid(report):
        raise RefereeError("report self-digest is missing or stale")
    return (json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False)
            + "\n").encode("utf-8")


def parse_transform(text: str | None) -> Matrix:
    if not text:
        return Matrix()
    result = Matrix()
    consumed = ""
    for match in _TRANSFORM_RE.finditer(text):
        consumed += match.group(0)
        name = match.group(1)
        arguments = match.group(2)
        if re.sub(r"[\s,]+", "", re.sub(_NUMBER, "", arguments)):
            raise RefereeError(
                f"unsupported SVG transform units: {match.group(0)}")
        values = [float(v) for v in re.findall(_NUMBER, arguments)]
        if name == "matrix" and len(values) == 6:
            op = Matrix(*values)
        elif name == "translate" and len(values) in (1, 2):
            op = Matrix(e=values[0], f=values[1] if len(values) == 2 else 0.0)
        elif name == "scale" and len(values) in (1, 2):
            op = Matrix(a=values[0], d=values[1] if len(values) == 2 else values[0])
        elif name == "rotate" and len(values) in (1, 3):
            radians = math.radians(values[0])
            rotation = Matrix(a=math.cos(radians), b=math.sin(radians),
                              c=-math.sin(radians), d=math.cos(radians))
            if len(values) == 3:
                cx, cy = values[1:]
                op = (Matrix(e=cx, f=cy).then(rotation)
                      .then(Matrix(e=-cx, f=-cy)))
            else:
                op = rotation
        else:
            raise RefereeError(f"unsupported SVG transform: {match.group(0)}")
        result = result.then(op)
    if re.sub(r"[\s,]+", "", text).lower() != re.sub(
            r"[\s,]+", "", consumed).lower():
        raise RefereeError(f"unparsed SVG transform: {text}")
    return result


def inline_style(element: ET.Element) -> dict[str, str]:
    raw_style = element.get("style", "")
    if ("/*" in raw_style or "*/" in raw_style
            or re.search(r"!\s*important\b", raw_style,
                         flags=re.IGNORECASE)):
        raise RefereeError(
            "SVG inline CSS comments and !important are unsupported")
    result: dict[str, str] = {}
    for part in raw_style.split(";"):
        part = part.strip()
        if not part:
            continue
        if ":" not in part:
            raise RefereeError(f"malformed SVG inline style: {raw_style}")
        key, value = part.split(":", 1)
        key = key.strip().lower()
        if (not key or key in result
                or key not in SVG_INLINE_STYLE_PROPERTIES):
            raise RefereeError(
                f"unsupported or duplicate SVG CSS property: {key}")
        result[key] = value.strip()
    return result


def parse_style(element: ET.Element, inherited: dict[str, str]) -> dict[str, str]:
    style = dict(inherited)
    inline = inline_style(element)
    for key in ("fill", "fill-opacity", "fill-rule",
                "stroke", "stroke-opacity",
                "stroke-width", "stroke-dasharray", "stroke-dashoffset",
                "stroke-linecap", "stroke-linejoin", "stroke-miterlimit",
                "vector-effect", "paint-order",
                "marker-start", "marker-mid", "marker-end",
                "display", "visibility"):
        if key in element.attrib:
            style[key] = element.attrib[key]
        if key in inline:
            style[key] = inline[key]
    local_opacity = clamp_opacity(inline.get(
        "opacity", element.get("opacity", "1")))
    style["_cumulative-opacity"] = str(
        float(inherited.get("_cumulative-opacity", "1"))
        * local_opacity
    )
    return style


def svg_keyword(style: dict[str, str], key: str, default: str,
                allowed: Sequence[str]) -> str:
    value = style.get(key, default).strip().lower()
    if value not in allowed:
        raise RefereeError(f"unsupported SVG {key} value: {value}")
    return value


def reject_unsupported_svg_presentation(element: ET.Element) -> None:
    unsupported = sorted(
        set(element.attrib) & UNSUPPORTED_SVG_PRESENTATION_ATTRIBUTES)
    if unsupported:
        raise RefereeError(
            "unsupported SVG presentation attribute(s): "
            + ", ".join(unsupported))


def clamp_opacity(value: str | float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        raise RefereeError(f"invalid SVG opacity: {value}")
    if not math.isfinite(parsed):
        raise RefereeError(f"non-finite SVG opacity: {value}")
    return max(0.0, min(1.0, parsed))


def colour_tone(value: str | None) -> float | None:
    if value is None or value.strip().lower() in ("none", "transparent"):
        return None
    text = value.strip().lower()
    if text in ("black",):
        return 0.0
    if text in ("white",):
        return 1.0
    if text.startswith("#"):
        raw = text[1:]
        if len(raw) == 3:
            raw = "".join(ch * 2 for ch in raw)
        if len(raw) != 6:
            raise RefereeError(f"unsupported SVG colour: {value}")
        channels = [int(raw[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    else:
        match = re.fullmatch(r"rgb\((.*)\)", text)
        if not match:
            raise RefereeError(f"unsupported SVG colour: {value}")
        parts = [p.strip() for p in match.group(1).split(",")]
        if len(parts) != 3:
            raise RefereeError(f"unsupported SVG colour: {value}")
        channels = []
        for part in parts:
            if part.endswith("%"):
                channels.append(float(part[:-1]) / 100)
            else:
                channels.append(float(part) / 255)
    return max(0.0, min(1.0, 0.2126 * channels[0]
                        + 0.7152 * channels[1] + 0.0722 * channels[2]))


def effective_tone(style: dict[str, str], key: str) -> float | None:
    tone = colour_tone(style.get(key))
    if tone is None:
        return None
    opacity = clamp_opacity(style.get("_cumulative-opacity", "1"))
    opacity *= clamp_opacity(style.get(f"{key}-opacity", "1"))
    if opacity <= 1e-12:
        return None
    # Composite over white paper; this preserves decorative greys rather than
    # treating every non-white paint as black.
    return 1 - opacity * (1 - tone)


def effective_opacity(style: dict[str, str], key: str) -> float:
    return (clamp_opacity(style.get("_cumulative-opacity", "1"))
            * clamp_opacity(style.get(f"{key}-opacity", "1")))


def arc_bound_points(start: tuple[float, float], rx: float, ry: float,
                     rotation: float, large_arc: float, sweep: float,
                     end: tuple[float, float]) -> list[tuple[float, float]]:
    """Return a conservative local-space box for one SVG elliptical arc.

    The complete ellipse is deliberately bounded, not only the selected arc.
    That may make a nearby comb unevaluable, but it can never hide painted
    geometry.  The SVG endpoint-to-centre conversion below follows the W3C
    implementation notes and includes the mandatory radii expansion.
    """
    rx, ry = abs(rx), abs(ry)
    if rx <= 1e-12 or ry <= 1e-12 or start == end:
        return [start, end]
    phi = math.radians(rotation % 360.0)
    cos_phi, sin_phi = math.cos(phi), math.sin(phi)
    dx = (start[0] - end[0]) / 2
    dy = (start[1] - end[1]) / 2
    x_prime = cos_phi * dx + sin_phi * dy
    y_prime = -sin_phi * dx + cos_phi * dy
    scale = (x_prime * x_prime) / (rx * rx) + (
        y_prime * y_prime) / (ry * ry)
    if scale > 1:
        factor = math.sqrt(scale)
        rx *= factor
        ry *= factor
    numerator = max(
        0.0,
        rx * rx * ry * ry
        - rx * rx * y_prime * y_prime
        - ry * ry * x_prime * x_prime,
    )
    denominator = (
        rx * rx * y_prime * y_prime
        + ry * ry * x_prime * x_prime
    )
    coefficient = 0.0 if denominator <= 1e-24 else math.sqrt(
        numerator / denominator)
    if bool(round(large_arc)) == bool(round(sweep)):
        coefficient = -coefficient
    cx_prime = coefficient * rx * y_prime / ry
    cy_prime = -coefficient * ry * x_prime / rx
    cx = (
        cos_phi * cx_prime - sin_phi * cy_prime
        + (start[0] + end[0]) / 2
    )
    cy = (
        sin_phi * cx_prime + cos_phi * cy_prime
        + (start[1] + end[1]) / 2
    )
    x_radius = math.sqrt((rx * cos_phi) ** 2 + (ry * sin_phi) ** 2)
    y_radius = math.sqrt((rx * sin_phi) ** 2 + (ry * cos_phi) ** 2)
    return [
        start,
        end,
        (cx - x_radius, cy - y_radius),
        (cx - x_radius, cy + y_radius),
        (cx + x_radius, cy - y_radius),
        (cx + x_radius, cy + y_radius),
    ]


def path_subpaths(
        data: str
) -> tuple[
    list[tuple[list[tuple[float, float]], bool]],
    list[list[tuple[float, float]]],
    bool,
]:
    """Parse SVG paths without letting one curve poison an entire page.

    Linear subpaths are returned for exact rectangle/line handling.  A subpath
    containing a Bezier or arc is returned as a conservative point cloud: an
    affine transform of that cloud still bounds the painted curve because
    Beziers stay inside their control hull and arcs use the full ellipse box.
    ``malformed`` is separate because an unparsed command has no honest local
    bound and must remain page-wide UNEVALUABLE.
    """
    tokens = _PATH_TOKEN_RE.findall(data)
    subpaths: list[tuple[list[tuple[float, float]], bool]] = []
    unsupported_subpaths: list[list[tuple[float, float]]] = []
    current: list[tuple[float, float]] = []
    unsupported_points: list[tuple[float, float]] = []
    command: str | None = None
    cursor = (0.0, 0.0)
    start = (0.0, 0.0)
    previous_op: str | None = None
    cubic_control: tuple[float, float] | None = None
    quadratic_control: tuple[float, float] | None = None
    i = 0
    malformed = False

    def number(index: int) -> float:
        if index >= len(tokens) or tokens[index].isalpha():
            raise ValueError("missing path coordinate")
        return float(tokens[index])

    def point(x: float, y: float, relative: bool,
              base: tuple[float, float]) -> tuple[float, float]:
        return (x + base[0], y + base[1]) if relative else (x, y)

    def flush(closed: bool) -> None:
        nonlocal current, unsupported_points
        if current:
            if unsupported_points:
                unsupported_subpaths.append([
                    *current,
                    *unsupported_points,
                ])
            else:
                subpaths.append((current, closed))
        current = []
        unsupported_points = []

    try:
        while i < len(tokens):
            token = tokens[i]
            if token.isalpha():
                command = token
                i += 1
            if command is None:
                raise ValueError("path starts without command")
            relative = command.islower()
            op = command.upper()
            if op == "Z":
                flush(True)
                cursor = start
                command = None
                previous_op = "Z"
                cubic_control = None
                quadratic_control = None
                continue
            if op in ("M", "L"):
                x, y = number(i), number(i + 1)
                i += 2
                x, y = point(x, y, relative, cursor)
                if op == "M":
                    flush(False)
                    current = [(x, y)]
                    start = (x, y)
                    command = "l" if relative else "L"
                else:
                    current.append((x, y))
                    if unsupported_points:
                        unsupported_points.append((x, y))
                cursor = (x, y)
            elif op == "H":
                x = number(i)
                i += 1
                if relative:
                    x += cursor[0]
                cursor = (x, cursor[1])
                current.append(cursor)
                if unsupported_points:
                    unsupported_points.append(cursor)
            elif op == "V":
                y = number(i)
                i += 1
                if relative:
                    y += cursor[1]
                cursor = (cursor[0], y)
                current.append(cursor)
                if unsupported_points:
                    unsupported_points.append(cursor)
            elif op == "C":
                base = cursor
                control1 = point(number(i), number(i + 1), relative, base)
                control2 = point(number(i + 2), number(i + 3), relative, base)
                end = point(number(i + 4), number(i + 5), relative, base)
                i += 6
                unsupported_points.extend((cursor, control1, control2, end))
                current.append(end)
                cursor = end
                cubic_control = control2
                quadratic_control = None
            elif op == "S":
                base = cursor
                reflected = (
                    (2 * cursor[0] - cubic_control[0],
                     2 * cursor[1] - cubic_control[1])
                    if previous_op in ("C", "S") and cubic_control is not None
                    else cursor
                )
                control2 = point(number(i), number(i + 1), relative, base)
                end = point(number(i + 2), number(i + 3), relative, base)
                i += 4
                unsupported_points.extend(
                    (cursor, reflected, control2, end))
                current.append(end)
                cursor = end
                cubic_control = control2
                quadratic_control = None
            elif op == "Q":
                base = cursor
                control = point(number(i), number(i + 1), relative, base)
                end = point(number(i + 2), number(i + 3), relative, base)
                i += 4
                unsupported_points.extend((cursor, control, end))
                current.append(end)
                cursor = end
                quadratic_control = control
                cubic_control = None
            elif op == "T":
                base = cursor
                control = (
                    (2 * cursor[0] - quadratic_control[0],
                     2 * cursor[1] - quadratic_control[1])
                    if previous_op in ("Q", "T")
                    and quadratic_control is not None
                    else cursor
                )
                end = point(number(i), number(i + 1), relative, base)
                i += 2
                unsupported_points.extend((cursor, control, end))
                current.append(end)
                cursor = end
                quadratic_control = control
                cubic_control = None
            elif op == "A":
                rx, ry = number(i), number(i + 1)
                rotation = number(i + 2)
                large_arc, sweep = number(i + 3), number(i + 4)
                if large_arc not in (0.0, 1.0) or sweep not in (0.0, 1.0):
                    raise ValueError("invalid SVG arc flag")
                end = point(number(i + 5), number(i + 6), relative, cursor)
                i += 7
                unsupported_points.extend(arc_bound_points(
                    cursor, rx, ry, rotation, large_arc, sweep, end))
                current.append(end)
                cursor = end
                cubic_control = None
                quadratic_control = None
            else:
                malformed = True
                break
            if op not in ("C", "S"):
                cubic_control = None
            if op not in ("Q", "T"):
                quadratic_control = None
            previous_op = op
    except (ValueError, IndexError):
        malformed = True

    flush(False)
    return subpaths, unsupported_subpaths, malformed


def bbox(points: Sequence[tuple[float, float]]) -> tuple[float, float, float, float]:
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    return min(xs), min(ys), max(xs), max(ys)


def transformed_bbox(points: Sequence[tuple[float, float]],
                     transform: Matrix) -> tuple[float, float, float, float]:
    return bbox([transform.point(x, y) for x, y in points])


def transformed_ellipse_bbox(cx: float, cy: float, rx: float, ry: float,
                             transform: Matrix
                             ) -> tuple[float, float, float, float]:
    centre_x, centre_y = transform.point(cx, cy)
    radius_x = math.sqrt(
        (transform.a * rx) ** 2 + (transform.c * ry) ** 2)
    radius_y = math.sqrt(
        (transform.b * rx) ** 2 + (transform.d * ry) ** 2)
    return (
        centre_x - radius_x, centre_y - radius_y,
        centre_x + radius_x, centre_y + radius_y,
    )


def is_axis_aligned_rectangle(points: Sequence[tuple[float, float]]) -> bool:
    """Whether a closed point sequence is exactly an axis-aligned rectangle."""
    cleaned: list[tuple[float, float]] = []
    for point in points:
        if not cleaned or (abs(point[0] - cleaned[-1][0]) > 1e-6
                           or abs(point[1] - cleaned[-1][1]) > 1e-6):
            cleaned.append(point)
    if len(cleaned) > 1 and (abs(cleaned[0][0] - cleaned[-1][0]) <= 1e-6
                             and abs(cleaned[0][1] - cleaned[-1][1]) <= 1e-6):
        cleaned.pop()
    if len(cleaned) < 4:
        return False
    x0, y0, x1, y1 = bbox(cleaned)
    if x1 - x0 <= 1e-6 or y1 - y0 <= 1e-6:
        return False
    corners = {
        (round(x0, 6), round(y0, 6)),
        (round(x0, 6), round(y1, 6)),
        (round(x1, 6), round(y0, 6)),
        (round(x1, 6), round(y1, 6)),
    }
    if {(round(x, 6), round(y, 6)) for x, y in cleaned} != corners:
        return False
    return all(
        abs(b[0] - a[0]) <= 1e-6 or abs(b[1] - a[1]) <= 1e-6
        for a, b in zip(cleaned, [*cleaned[1:], cleaned[0]])
    )


def attr_float(element: ET.Element, name: str, default: float = 0.0) -> float:
    raw = element.get(name)
    if raw is None:
        return default
    match = re.fullmatch(rf"\s*({_NUMBER})(?:px)?\s*", raw)
    if not match:
        raise RefereeError(f"non-numeric SVG {name}: {raw}")
    value = float(match.group(1))
    if not math.isfinite(value):
        raise RefereeError(f"non-finite SVG {name}: {raw}")
    return value


def parse_svg(path: pathlib.Path) -> SvgPage:
    root = ET.parse(path).getroot()
    view_box = [float(v) for v in re.findall(_NUMBER, root.get("viewBox", ""))]
    if len(view_box) != 4 or view_box[0] != 0 or view_box[1] != 0:
        raise RefereeError(f"unsupported SVG viewBox: {root.get('viewBox')}")
    width, height = view_box[2], view_box[3]
    paints: list[Paint] = []
    unsupported: list[UnsupportedRegion] = []
    order = 0
    definitions = {
        element.get("id"): element for element in root.iter()
        if element.get("id")
    }
    xlink_href = "{http://www.w3.org/1999/xlink}href"

    def next_order() -> int:
        nonlocal order
        value = order
        order += 1
        return value

    def add_rect(box_value: tuple[float, float, float, float], tone: float,
                 kind: str, element_id: str, clipped: bool,
                 paint_order: int | None = None) -> None:
        x0, y0, x1, y1 = box_value
        if x1 <= x0 or y1 <= y0:
            return
        if paint_order is None:
            paint_order = next_order()
        paints.append(Paint(round(x0, 6), round(y0, 6), round(x1, 6),
                            round(y1, 6), round(tone, 8), paint_order, kind,
                            element_id, clipped))

    def add_unsupported(
        box_value: tuple[float, float, float, float],
        reason: str,
        element_id: str,
        tone: float | None = None,
        clipped: bool = True,
        paint_order: int | None = None,
    ) -> None:
        x0, y0, x1, y1 = box_value
        if x1 <= x0 or y1 <= y0:
            return
        if paint_order is None:
            paint_order = next_order()
        unsupported.append(UnsupportedRegion(
            round(x0, 6), round(y0, 6), round(x1, 6), round(y1, 6),
            reason, element_id,
            None if tone is None else round(tone, 8),
            paint_order, clipped,
        ))

    def stroke_metrics(style: dict[str, str], matrix: Matrix
                       ) -> tuple[float, float, bool, str]:
        """Return transformed width, conservative join pad, ambiguity, cap."""
        vector_effect = style.get("vector-effect", "none").strip().lower()
        if vector_effect in ("", "none"):
            scale = matrix.stroke_scale()
            vector_ambiguous = False
            transform_ambiguous = not matrix.is_similarity()
        elif vector_effect == "non-scaling-stroke":
            scale = 1.0
            vector_ambiguous = False
            transform_ambiguous = False
        else:
            # Keep a conservative extent while refusing to treat the stroke as
            # an exact divider.
            scale = max(1.0, matrix.stroke_scale())
            vector_ambiguous = True
            transform_ambiguous = True
        width_value = float(style.get("stroke-width", "1")) * scale
        dash = style.get("stroke-dasharray", "none").strip().lower()
        dashed = dash not in ("", "none")
        linecap = style.get("stroke-linecap", "butt").strip().lower()
        linejoin = style.get("stroke-linejoin", "miter").strip().lower()
        semantics_ambiguous = (
            vector_ambiguous
            or transform_ambiguous
            or dashed
            or linecap not in ("butt", "round", "square")
            or linejoin not in ("miter", "round", "bevel")
        )
        try:
            miter_limit = float(style.get("stroke-miterlimit", "4"))
        except ValueError:
            miter_limit = 4.0
            semantics_ambiguous = True
        if not math.isfinite(miter_limit) or miter_limit < 1:
            miter_limit = 4.0
            semantics_ambiguous = True
        join_pad = width_value / 2
        if linejoin == "miter":
            join_pad *= miter_limit
        return width_value, join_pad, semantics_ambiguous, linecap

    def nondefault_paint_order(style: dict[str, str]) -> bool:
        """Whether SVG paint ordering is not the default fill-then-stroke.

        The referee records fills and strokes as separate ordered rectangles.
        Treating an explicit reordering as if it were the default can invert
        final ownership at a divider.  No corpus result may depend on that
        approximation, so an explicit ordering remains locally ambiguous.
        """
        return style.get("paint-order", "normal").strip().lower() not in (
            "", "normal")

    def add_glyph_reference(
        referenced: ET.Element,
        instance_matrix: Matrix,
        instance_style: dict[str, str],
        instance_clipped: bool,
        element_id: str,
        href: str,
    ) -> None:
        """Record glyph paint as possible occlusion, never as a divider."""

        def visit(node: ET.Element, parent_matrix: Matrix,
                  inherited_style: dict[str, str],
                  inherited_clipped: bool) -> None:
            node_tag = node.tag.rsplit("}", 1)[-1]
            reject_unsupported_svg_presentation(node)
            node_inline = inline_style(node)
            transform_text = node_inline.get(
                "transform", node.get("transform"))
            node_matrix = parent_matrix.then(parse_transform(
                None if transform_text in (None, "", "none")
                else transform_text))
            node_style = parse_style(node, inherited_style)
            display = svg_keyword(
                node_style, "display", "inline", ("inline", "none"))
            visibility = svg_keyword(
                node_style, "visibility", "visible",
                ("visible", "hidden", "collapse"))
            if display == "none":
                return
            node_effects = {
                key: node_inline.get(key, node.get(key))
                for key in ("clip-path", "mask", "filter")
            }
            node_clipped = inherited_clipped or any(
                value not in (None, "", "none")
                for value in node_effects.values())
            if visibility in ("hidden", "collapse"):
                # Visibility is inherited, but a descendant can restore it.
                # Display:none, handled above, prunes the whole subtree.
                for child in node:
                    visit(child, node_matrix, node_style, node_clipped)
                return
            if node_tag in ("g", "symbol"):
                for child in node:
                    visit(child, node_matrix, node_style, node_clipped)
                return
            if node_tag != "path":
                add_unsupported(
                    (0.0, 0.0, width, height),
                    f"unsupported glyph use target: {href}", element_id)
                return
            linear, curved, malformed = path_subpaths(node.get("d", ""))
            points = [
                point for subpath, _closed in linear for point in subpath
            ]
            points.extend(point for subpath in curved for point in subpath)
            glyph_fill = effective_tone(node_style, "fill")
            glyph_stroke = effective_tone(node_style, "stroke")
            if malformed:
                add_unsupported(
                    (0.0, 0.0, width, height),
                    f"malformed glyph use: {href}", element_id,
                    glyph_stroke if glyph_stroke is not None else glyph_fill)
                return
            if not points or (glyph_fill is None and glyph_stroke is None):
                return
            transformed = [
                node_matrix.point(x, y) for x, y in points
            ]
            glyph_box = bbox(transformed)
            if glyph_fill is not None:
                add_unsupported(
                    glyph_box,
                    f"glyph use may occlude geometry: {href}",
                    element_id, glyph_fill,
                    node_clipped
                    or effective_opacity(node_style, "fill") < 1.0 - 1e-8
                    or nondefault_paint_order(node_style))
            if glyph_stroke is not None:
                _width, glyph_pad, glyph_ambiguous, _cap = stroke_metrics(
                    node_style, node_matrix)
                add_unsupported(
                    (glyph_box[0] - glyph_pad,
                     glyph_box[1] - glyph_pad,
                     glyph_box[2] + glyph_pad,
                     glyph_box[3] + glyph_pad),
                    f"stroked glyph use may occlude geometry: {href}",
                    element_id, glyph_stroke,
                    node_clipped or glyph_ambiguous
                    or nondefault_paint_order(node_style))

        visit(referenced, instance_matrix, instance_style, instance_clipped)

    def walk(element: ET.Element, parent_matrix: Matrix,
             inherited: dict[str, str], in_defs: bool = False,
             clipped: bool = False) -> None:
        tag = element.tag.rsplit("}", 1)[-1]
        if tag == "defs":
            return
        reject_unsupported_svg_presentation(element)
        local_inline = inline_style(element)
        transform_text = local_inline.get(
            "transform", element.get("transform"))
        local = parse_transform(
            None if transform_text in (None, "", "none") else transform_text)
        matrix = parent_matrix.then(local)
        style = parse_style(element, inherited)
        local_effects = {
            key: local_inline.get(key, element.get(key))
            for key in ("clip-path", "mask", "filter")
        }
        clipped_here = clipped or any(
            value not in (None, "", "none") for value in local_effects.values())
        display = svg_keyword(
            style, "display", "inline", ("inline", "none"))
        visibility = svg_keyword(
            style, "visibility", "visible",
            ("visible", "hidden", "collapse"))
        if display == "none":
            return
        if visibility in ("hidden", "collapse"):
            # ``visibility`` is inherited but, unlike ``display:none``, a
            # descendant may explicitly restore ``visibility:visible``.
            for child in element:
                walk(child, matrix, style, in_defs, clipped_here)
            return
        element_id = element.get("id") or f"{tag}-{len(paints) + len(unsupported)}"
        marker_values = [
            style.get(name, "none").strip().lower()
            for name in ("marker-start", "marker-mid", "marker-end")
        ]
        if any(value not in ("", "none") for value in marker_values):
            add_unsupported(
                (0.0, 0.0, width, height),
                "SVG marker paint is not resolved", element_id)
            return
        if tag == "switch":
            add_unsupported(
                (0.0, 0.0, width, height),
                "SVG switch conditional selection is not resolved",
                element_id)
            return
        if local_effects["filter"] not in (None, "", "none"):
            add_unsupported(
                (0.0, 0.0, width, height),
                "SVG filter has unbounded paint effects", element_id)
        if tag == "svg" and element is not root:
            nested_width = attr_float(element, "width")
            nested_height = attr_float(element, "height")
            nested_x = attr_float(element, "x")
            nested_y = attr_float(element, "y")
            if nested_width > 0 and nested_height > 0:
                add_unsupported(
                    transformed_bbox(
                        [(nested_x, nested_y),
                         (nested_x + nested_width, nested_y),
                         (nested_x + nested_width, nested_y + nested_height),
                         (nested_x, nested_y + nested_height)],
                        matrix,
                    ),
                    "nested SVG viewport", element_id)
            else:
                add_unsupported(
                    (0.0, 0.0, width, height),
                    "unbounded nested SVG viewport", element_id)
            return

        if tag == "path":
            subpaths, curved, malformed = path_subpaths(element.get("d", ""))
            fill = effective_tone(style, "fill")
            stroke = effective_tone(style, "stroke")
            fill_rule = style.get("fill-rule", "nonzero").strip().lower()
            fill_subpaths = [
                (points, closed)
                for points, closed in subpaths
                if len(points) >= 3
                and bbox(points)[2] - bbox(points)[0] > 1e-9
                and bbox(points)[3] - bbox(points)[1] > 1e-9
            ]
            rectangular_boxes = [
                bbox(points)
                for points, closed in fill_subpaths
                if closed and is_axis_aligned_rectangle(points)
            ]
            rectangles_overlap = any(
                min(first[2], second[2]) - max(first[0], second[0]) > 1e-8
                and min(first[3], second[3]) - max(first[1], second[1]) > 1e-8
                for index, first in enumerate(rectangular_boxes)
                for second in rectangular_boxes[index + 1:]
            )
            compound_fill_ambiguous = (
                fill_rule not in ("", "nonzero", "evenodd")
                or (
                    len(fill_subpaths) > 1
                    and (
                        len(rectangular_boxes) != len(fill_subpaths)
                        or rectangles_overlap
                    )
                )
            )
            if fill is not None and compound_fill_ambiguous:
                fill_points = [
                    point for points, _closed in subpaths for point in points
                ]
                fill_points.extend(
                    point for points in curved for point in points)
                if fill_points:
                    add_unsupported(
                        transformed_bbox(fill_points, matrix),
                        ("non-default or compound SVG fill topology"),
                        element_id, fill, clipped_here)
                fill = None
            fill_ambiguous = (
                clipped_here
                or effective_opacity(style, "fill") < 1.0 - 1e-8
                or nondefault_paint_order(style))
            stroke_width, join_pad, stroke_semantics_ambiguous, linecap = (
                stroke_metrics(style, matrix)
            )
            stroke_ambiguous = (
                clipped_here
                or effective_opacity(style, "stroke") < 1.0 - 1e-8
                or stroke_semantics_ambiguous
                or nondefault_paint_order(style)
            )
            for points, closed in subpaths:
                if len(points) < 2:
                    continue
                transformed = [matrix.point(x, y) for x, y in points]
                if fill is not None:
                    if closed and is_axis_aligned_rectangle(transformed):
                        add_rect(bbox(transformed), fill, "fill",
                                 element_id, fill_ambiguous)
                    elif closed or len(transformed) >= 3:
                        x0, y0, x1, y1 = bbox(transformed)
                        add_unsupported(
                            (x0, y0, x1, y1),
                            ("non-rectangular closed SVG fill" if closed else
                             "implicitly closed SVG fill"),
                            element_id, fill, fill_ambiguous)
                if stroke is not None:
                    half = stroke_width / 2
                    cap_pad = half if linecap in ("round", "square") else 0.0
                    pairs = list(zip(transformed, transformed[1:]))
                    if closed:
                        pairs.append((transformed[-1], transformed[0]))
                    for (x0, y0), (x1, y1) in pairs:
                        if abs(x1 - x0) <= 1e-6 and abs(y1 - y0) > 0:
                            add_rect((min(x0, x1) - half,
                                      min(y0, y1) - cap_pad,
                                      max(x0, x1) + half,
                                      max(y0, y1) + cap_pad),
                                     stroke, "stroke", element_id,
                                     stroke_ambiguous)
                        elif (abs(y1 - y0) > abs(x1 - x0)
                              and abs(x1 - x0) / 2 <= POSITION_TOL_PT):
                            centre_x = (x0 + x1) / 2
                            drift_pad = abs(x1 - x0) / 2
                            add_rect(
                                (centre_x - half - drift_pad,
                                 min(y0, y1) - cap_pad,
                                 centre_x + half + drift_pad,
                                 max(y0, y1) + cap_pad),
                                stroke, "near-vertical-stroke", element_id,
                                stroke_ambiguous)
                        elif (abs(y1 - y0) <= 1e-6
                              and abs(x1 - x0) > 0):
                            add_rect((min(x0, x1) - cap_pad,
                                      min(y0, y1) - half,
                                      max(x0, x1) + cap_pad,
                                      max(y0, y1) + half),
                                     stroke, "stroke", element_id,
                                     stroke_ambiguous)
                        elif abs(x1 - x0) > 0 or abs(y1 - y0) > 0:
                            add_unsupported(
                                (min(x0, x1) - join_pad,
                                 min(y0, y1) - join_pad,
                                 max(x0, x1) + join_pad,
                                 max(y0, y1) + join_pad),
                                "diagonal SVG path stroke", element_id,
                                stroke, stroke_ambiguous)
            if curved and (fill is not None or stroke is not None):
                pad = join_pad if stroke is not None else 0.0
                curve_tone = stroke if stroke is not None else fill
                for points in curved:
                    transformed = [matrix.point(x, y) for x, y in points]
                    x0, y0, x1, y1 = bbox(transformed)
                    add_unsupported(
                        (x0 - pad, y0 - pad, x1 + pad, y1 + pad),
                        "curved SVG path", element_id, curve_tone,
                        clipped_here or stroke_semantics_ambiguous)
            if malformed and (fill is not None or stroke is not None):
                add_unsupported(
                    (0.0, 0.0, width, height),
                    "malformed or unknown SVG path command", element_id,
                    stroke if stroke is not None else fill)
        elif tag == "rect":
            x, y = attr_float(element, "x"), attr_float(element, "y")
            w, h = attr_float(element, "width"), attr_float(element, "height")
            if w < 0 or h < 0:
                add_unsupported(
                    (0.0, 0.0, width, height),
                    "negative SVG rect extent", element_id)
                return
            if w == 0 or h == 0:
                return
            points = [matrix.point(x, y), matrix.point(x + w, y),
                      matrix.point(x + w, y + h), matrix.point(x, y + h)]
            fill = effective_tone(style, "fill")
            stroke = effective_tone(style, "stroke")
            stroke_width, join_pad, stroke_semantics_ambiguous, _linecap = (
                stroke_metrics(style, matrix)
            )
            fill_ambiguous = (
                clipped_here
                or effective_opacity(style, "fill") < 1.0 - 1e-8
                or nondefault_paint_order(style))
            stroke_ambiguous = (
                clipped_here
                or effective_opacity(style, "stroke") < 1.0 - 1e-8
                or stroke_semantics_ambiguous
                or nondefault_paint_order(style)
            )
            rounded = attr_float(element, "rx") > 0 or attr_float(element, "ry") > 0
            if rounded and (fill is not None or stroke is not None):
                box_value = bbox(points)
                add_unsupported(
                    (box_value[0] - (join_pad if stroke is not None else 0.0),
                     box_value[1] - (join_pad if stroke is not None else 0.0),
                     box_value[2] + (join_pad if stroke is not None else 0.0),
                     box_value[3] + (join_pad if stroke is not None else 0.0)),
                    "rounded SVG rect", element_id,
                    stroke if stroke is not None else fill,
                    clipped_here or stroke_semantics_ambiguous)
                fill = None
                stroke = None
            elif not is_axis_aligned_rectangle(points):
                x0, y0, x1, y1 = bbox(points)
                add_unsupported(
                    (x0 - (join_pad if stroke is not None else 0.0),
                     y0 - (join_pad if stroke is not None else 0.0),
                     x1 + (join_pad if stroke is not None else 0.0),
                     y1 + (join_pad if stroke is not None else 0.0)),
                    "transformed SVG rect is not axis-aligned", element_id,
                    stroke if stroke is not None else fill,
                    clipped_here or stroke_semantics_ambiguous)
                fill = None
                stroke = None
            if fill is not None:
                add_rect(bbox(points), fill, "fill", element_id, fill_ambiguous)
            if stroke is not None:
                half = stroke_width / 2
                box_value = bbox(points)
                add_rect((box_value[0] - half, box_value[1],
                          box_value[0] + half, box_value[3]),
                         stroke, "stroke", element_id, stroke_ambiguous)
                add_rect((box_value[2] - half, box_value[1],
                          box_value[2] + half, box_value[3]),
                         stroke, "stroke", element_id, stroke_ambiguous)
                add_rect((box_value[0], box_value[1] - half,
                          box_value[2], box_value[1] + half),
                         stroke, "stroke", element_id, stroke_ambiguous)
                add_rect((box_value[0], box_value[3] - half,
                          box_value[2], box_value[3] + half),
                         stroke, "stroke", element_id, stroke_ambiguous)
        elif tag == "line":
            p0 = matrix.point(attr_float(element, "x1"), attr_float(element, "y1"))
            p1 = matrix.point(attr_float(element, "x2"), attr_float(element, "y2"))
            stroke = effective_tone(style, "stroke")
            stroke_width, join_pad, stroke_semantics_ambiguous, linecap = (
                stroke_metrics(style, matrix)
            )
            stroke_ambiguous = (
                clipped_here
                or effective_opacity(style, "stroke") < 1.0 - 1e-8
                or stroke_semantics_ambiguous
                or nondefault_paint_order(style)
            )
            half = stroke_width / 2
            cap_pad = half if linecap in ("round", "square") else 0.0
            if stroke is not None and abs(p1[0] - p0[0]) <= 1e-6:
                add_rect((min(p0[0], p1[0]) - half,
                          min(p0[1], p1[1]) - cap_pad,
                          max(p0[0], p1[0]) + half,
                          max(p0[1], p1[1]) + cap_pad),
                         stroke, "stroke", element_id, stroke_ambiguous)
            elif (stroke is not None
                  and abs(p1[1] - p0[1]) > abs(p1[0] - p0[0])
                  and abs(p1[0] - p0[0]) / 2 <= POSITION_TOL_PT):
                centre_x = (p0[0] + p1[0]) / 2
                drift_pad = abs(p1[0] - p0[0]) / 2
                add_rect(
                    (centre_x - half - drift_pad,
                     min(p0[1], p1[1]) - cap_pad,
                     centre_x + half + drift_pad,
                     max(p0[1], p1[1]) + cap_pad),
                    stroke, "near-vertical-line", element_id,
                    stroke_ambiguous)
            elif stroke is not None and abs(p1[1] - p0[1]) <= 1e-6:
                add_rect((min(p0[0], p1[0]) - cap_pad,
                          min(p0[1], p1[1]) - half,
                          max(p0[0], p1[0]) + cap_pad,
                          max(p0[1], p1[1]) + half),
                         stroke, "stroke", element_id, stroke_ambiguous)
            elif stroke is not None:
                add_unsupported(
                    (min(p0[0], p1[0]) - join_pad,
                     min(p0[1], p1[1]) - join_pad,
                     max(p0[0], p1[0]) + join_pad,
                     max(p0[1], p1[1]) + join_pad),
                    "diagonal SVG line", element_id, stroke,
                    stroke_ambiguous)
        elif tag == "image":
            x, y = attr_float(element, "x"), attr_float(element, "y")
            w, h = attr_float(element, "width"), attr_float(element, "height")
            if w < 0 or h < 0:
                add_unsupported(
                    (0.0, 0.0, width, height),
                    "negative SVG image extent", element_id)
                return
            if w > 0 and h > 0:
                x0, y0, x1, y1 = transformed_bbox(
                    [(x, y), (x + w, y), (x + w, y + h), (x, y + h)],
                    matrix)
                add_unsupported(
                    (x0, y0, x1, y1), "embedded raster image", element_id)
        elif tag == "use":
            href = element.get(xlink_href) or element.get("href") or ""
            referenced = definitions.get(href.removeprefix("#"))
            if referenced is None:
                add_unsupported(
                    (0.0, 0.0, width, height),
                    f"unresolved SVG use reference: {href}", element_id)
            elif href.startswith("#glyph-"):
                referenced_tag = referenced.tag.rsplit("}", 1)[-1]
                if (referenced_tag == "symbol"
                        and (referenced.get("viewBox") is not None
                             or element.get("width") is not None
                             or element.get("height") is not None)):
                    add_unsupported(
                        (0.0, 0.0, width, height),
                        f"glyph symbol viewport is not resolved: {href}",
                        element_id)
                else:
                    glyph_matrix = matrix.then(Matrix(
                        e=attr_float(element, "x"),
                        f=attr_float(element, "y")))
                    add_glyph_reference(
                        referenced, glyph_matrix, style, clipped_here,
                        element_id, href)
            elif referenced.tag.rsplit("}", 1)[-1] == "image":
                x = attr_float(referenced, "x") + attr_float(element, "x")
                y = attr_float(referenced, "y") + attr_float(element, "y")
                w = attr_float(referenced, "width")
                h = attr_float(referenced, "height")
                ref_matrix = matrix.then(parse_transform(
                    referenced.get("transform")))
                x0, y0, x1, y1 = transformed_bbox(
                    [(x, y), (x + w, y), (x + w, y + h), (x, y + h)],
                    ref_matrix)
                add_unsupported(
                    (x0, y0, x1, y1),
                    f"embedded raster use: {href}", element_id)
            else:
                add_unsupported(
                    (0.0, 0.0, width, height),
                    f"unsupported SVG use reference: {href}", element_id)
        elif tag in ("circle", "ellipse", "polygon", "polyline"):
            points: list[tuple[float, float]] = []
            shape_box: tuple[float, float, float, float] | None = None
            if tag == "circle":
                cx, cy = attr_float(element, "cx"), attr_float(element, "cy")
                rx = ry = attr_float(element, "r")
                if rx < 0:
                    add_unsupported(
                        (0.0, 0.0, width, height),
                        "negative SVG circle radius", element_id)
                    return
                shape_box = transformed_ellipse_bbox(
                    cx, cy, rx, ry, matrix)
            elif tag == "ellipse":
                cx, cy = attr_float(element, "cx"), attr_float(element, "cy")
                rx, ry = attr_float(element, "rx"), attr_float(element, "ry")
                if rx < 0 or ry < 0:
                    add_unsupported(
                        (0.0, 0.0, width, height),
                        "negative SVG ellipse radius", element_id)
                    return
                shape_box = transformed_ellipse_bbox(
                    cx, cy, rx, ry, matrix)
            else:
                values = [float(value) for value in re.findall(
                    _NUMBER, element.get("points", ""))]
                points = list(zip(values[0::2], values[1::2]))
                if points:
                    shape_box = transformed_bbox(points, matrix)
            shape_fill = effective_tone(style, "fill")
            shape_stroke = effective_tone(style, "stroke")
            if (shape_box is not None
                    and (shape_fill is not None or shape_stroke is not None)):
                x0, y0, x1, y1 = shape_box
                _width, shape_pad, shape_ambiguous, _cap = stroke_metrics(
                    style, matrix)
                pad = shape_pad if shape_stroke is not None else 0.0
                add_unsupported(
                    (x0 - pad, y0 - pad, x1 + pad, y1 + pad),
                    f"unsupported SVG {tag}", element_id,
                    shape_stroke if shape_stroke is not None else shape_fill,
                    clipped_here or shape_ambiguous)
        elif tag not in (
                "svg", "g", "a", "switch", "metadata", "title", "desc",
                "symbol", "clipPath", "mask"):
            add_unsupported(
                (0.0, 0.0, width, height),
                f"unsupported SVG element: {tag}", element_id)

        # Glyph uses are text, not vector compartment geometry. Other uses and
        # images were recorded above as unsupported visible regions.
        if tag not in ("use", "image", "symbol", "clipPath", "mask"):
            for child in element:
                walk(child, matrix, style, in_defs, clipped_here)

    for stylesheet in (
            element for element in root.iter()
            if element.tag.rsplit("}", 1)[-1] == "style"):
        add_unsupported(
            (0.0, 0.0, width, height),
            "embedded SVG stylesheet is not resolved",
            stylesheet.get("id") or "style",
        )

    walk(root, Matrix(), {"fill": "black", "stroke": "none",
                          "fill-opacity": "1", "stroke-opacity": "1",
                          "opacity": "1"})
    return SvgPage(width, height, paints, unsupported, sha256_file(path))


def source_pdf(layout: dict[str, Any], source_root: pathlib.Path) -> pathlib.Path:
    source = layout.get("source") or {}
    filename = str(source.get("file", "")).split(":", 1)[-1]
    expected = source.get("sha256")
    if not filename or not expected:
        raise RefereeError("layout has no pinned source filename/hash")
    matches = []
    for candidate in sorted(source_root.rglob(filename)):
        if candidate.is_file() and sha256_file(candidate) == expected:
            matches.append(candidate)
    if not matches:
        raise RefereeError(f"source PDF not found with pinned hash: {filename}")
    # Byte-identical duplicate inputs are semantically the same source.  The
    # lexicographic path is deterministic and every match is reported.
    return matches[0]


def run_bounded_subprocess(
        command: Sequence[str],
        *,
        timeout_seconds: float,
        label: str,
        ) -> subprocess.CompletedProcess[str]:
    """Run one oracle process in an isolated group with a fixed hard limit."""
    if (not math.isfinite(timeout_seconds) or timeout_seconds <= 0
            or not command or not all(
                isinstance(item, str) and item for item in command)):
        raise RefereeError(f"{label} has an invalid subprocess contract")
    process = subprocess.Popen(
        list(command),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=(os.name == "posix"),
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        if os.name == "posix":
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        else:
            process.kill()
        try:
            process.communicate(timeout=5.0)
        except subprocess.TimeoutExpired:
            process.kill()
            try:
                process.communicate(timeout=5.0)
            except subprocess.TimeoutExpired as error:
                raise RefereeError(
                    f"{label} could not be reaped after "
                    f"{SUBPROCESS_CLEANUP_POLICY}") from error
        raise RefereeError(
            f"{label} exceeded its fixed {timeout_seconds:g}-second "
            f"deadline; cleanup={SUBPROCESS_CLEANUP_POLICY}")
    return subprocess.CompletedProcess(
        args=list(command),
        returncode=process.returncode,
        stdout=stdout,
        stderr=stderr,
    )


def poppler_identity() -> dict[str, Any]:
    binary = shutil.which("pdftocairo")
    if binary is None:
        raise RefereeError("pdftocairo is not installed")
    proc = run_bounded_subprocess(
        [binary, "-v"],
        timeout_seconds=POPPLER_IDENTITY_TIMEOUT_SECONDS,
        label="pdftocairo identity",
    )
    version = (proc.stdout + proc.stderr).strip().splitlines()
    if proc.returncode != 0 or not version:
        raise RefereeError("pdftocairo -v failed")
    return {
        "version": version[0],
        "binary_path": str(pathlib.Path(binary).resolve()),
        "binary_sha256": sha256_file(pathlib.Path(binary)),
        "identity_timeout_seconds": POPPLER_IDENTITY_TIMEOUT_SECONDS,
        "page_timeout_seconds": POPPLER_PAGE_TIMEOUT_SECONDS,
        "subprocess_cleanup_policy": SUBPROCESS_CLEANUP_POLICY,
    }


def render_svg_page(binary: str, pdf: pathlib.Path, page_number: int,
                    directory: pathlib.Path) -> pathlib.Path:
    output = directory / f"page-{page_number}.svg"
    proc = run_bounded_subprocess(
        [binary, "-svg", "-f", str(page_number), "-l", str(page_number),
         str(pdf), str(output)],
        timeout_seconds=POPPLER_PAGE_TIMEOUT_SECONDS,
        label=f"pdftocairo page {page_number}",
    )
    if proc.returncode != 0 or not output.is_file():
        detail = (proc.stdout + proc.stderr).strip()
        raise RefereeError(f"pdftocairo page {page_number} failed: {detail}")
    return output


class SlotParser(html.parser.HTMLParser):
    def __init__(self, require_runtime_contract: bool = True) -> None:
        super().__init__(convert_charrefs=True)
        self.require_runtime_contract = require_runtime_contract
        self.document_contract_checked = False
        self.template_depth = 0
        self.doctype_count = 0
        self.doctype_valid = True
        self.element_stack: list[tuple[str, bool, str]] = []
        self.div_stack: list[
            tuple[str | None, int | None, int | None]
        ] = []
        self.physical_slots: dict[str, list[int]] = {}
        self.editable_slots: dict[str, list[int]] = {}
        self.slot_geometry: dict[str, list[dict[str, float | int]]] = {}
        self.comb_geometry: dict[str, tuple[float, float]] = {}
        self.comb_position: dict[str, tuple[float, float]] = {}
        self.comb_page: dict[str, int] = {}
        self.declared_slots: dict[str, tuple[int, int]] = {}
        self.invalid_bindings: list[str] = []
        self.comb_containers: set[str] = set()
        self.root: dict[str, str | None] | None = None
        self.pages: list[int] = []
        self.page_geometry: list[tuple[int, float, float]] = []
        self.style_depth = 0
        self.style_count = 0
        self.style_parts: list[str] = []
        self.stylesheet_structural_declarations: set[
            tuple[str, str, str]
        ] = set()
        self.stylesheet_page_sizes: list[tuple[float, float]] = []
        # A form whose source mixes page sizes (1604-CF) is emitted with one
        # named `@page page-N` per page plus a `.page-N{page:page-N}` binding.
        # They are recorded separately so the contract can bind each named rule
        # to that page's own emitted geometry instead of collapsing them.
        self.stylesheet_named_page_sizes: dict[int, tuple[float, float]] = {}
        self.stylesheet_named_page_selectors: set[int] = set()
        self.script_depth = 0
        self.script_attrs: tuple[tuple[str, str | None], ...] | None = None
        self.script_parts: list[str] = []
        self.runtime_script_hashes: list[str] = []
        self.band_data_scripts = 0

    def handle_starttag(self, tag: str,
                        attrs: list[tuple[str, str | None]]) -> None:
        self._validate_global_tag(tag, attrs)
        values = dict(attrs)
        if tag == "template":
            self.template_depth += 1
            return
        if self.template_depth:
            return
        render_safe = self._push_render_element(tag, attrs)
        if tag == "style":
            self.style_count += 1
            if self.style_depth:
                self.invalid_bindings.append("HTML has a nested style element")
            if values:
                self.invalid_bindings.append(
                    "HTML style element has unsupported attributes")
            self.style_depth += 1
            self.style_parts = []
        if tag == "script":
            if self.script_depth:
                self.invalid_bindings.append("HTML has a nested script element")
            self.script_depth += 1
            self.script_attrs = tuple(sorted(attrs))
            self.script_parts = []
        if tag == "meta" and values != {"charset": "utf-8"}:
            self.invalid_bindings.append(
                "HTML has unsupported meta directives")
        if tag in ("base", "embed", "iframe", "link", "noscript", "object"):
            if tag == "link":
                parent = self.element_stack[-1][0] if self.element_stack else None
                if parent != "head":
                    self.invalid_bindings.append(
                        "HTML font preload is outside the document head")
            elif tag in ("base", "embed", "iframe", "noscript", "object"):
                self.invalid_bindings.append(
                    f"HTML has unsupported rendering element: {tag}")
        if tag == "html":
            if self.root is not None:
                raise RefereeError("HTML has more than one root element")
            self.root = values
            return
        if tag == "div":
            parent_cell, _parent_slot, parent_page = (
                self.div_stack[-1]
                if self.div_stack else (None, None, None)
            )
            identifier = values.get("id") or ""
            cell = parent_cell
            slot_index: int | None = None
            page_index = parent_page
            page_match = _PAGE_RE.fullmatch(identifier)
            if page_match and "page" in (values.get("class") or "").split():
                page_index = int(page_match.group(1))
                self.pages.append(page_index)
                style = values.get("style") or ""
                geometry = self._geometry_style(
                    style, ("width", "height"))
                if geometry is None:
                    raise RefereeError(
                        f"HTML page {page_index} has non-canonical geometry")
                self.page_geometry.append((
                    page_index, geometry["width"], geometry["height"],
                ))
            if (_CELL_RE.fullmatch(identifier)
                    and values.get("data-field-kind") == "comb"):
                cell = identifier
                if not render_safe:
                    self.invalid_bindings.append(
                        f"comb is outside rendered layout: {identifier}")
                if identifier in self.comb_containers:
                    self.invalid_bindings.append(
                        f"duplicate comb container: {identifier}")
                self.comb_containers.add(identifier)
                if values.get("data-field-name") != identifier:
                    self.invalid_bindings.append(
                        f"comb field binding disagrees: {identifier}")
                style = values.get("style") or ""
                geometry = self._geometry_style(
                    style, ("left", "top", "width", "height"))
                if geometry is None:
                    self.invalid_bindings.append(
                        f"comb geometry is non-canonical: {identifier}")
                else:
                    left, top = geometry["left"], geometry["top"]
                    width, height = geometry["width"], geometry["height"]
                    self.comb_geometry[identifier] = (width, height)
                    self.comb_position[identifier] = (left, top)
                cell_page_match = _CELL_PAGE_RE.fullmatch(identifier)
                expected_page = (
                    int(cell_page_match.group(1))
                    if cell_page_match is not None else None
                )
                if page_index is None or expected_page != page_index:
                    self.invalid_bindings.append(
                        f"comb page binding disagrees: {identifier}")
                else:
                    self.comb_page[identifier] = page_index
                try:
                    declared_capacity = int(
                        values.get("data-comb-capacity") or "")
                    declared_count = int(values.get("data-comb-slots") or "")
                except ValueError:
                    declared_capacity = declared_count = -1
                self.declared_slots[identifier] = (
                    declared_capacity, declared_count)
            slot = values.get("data-slot")
            if cell and slot is not None and "s" in (
                    values.get("class") or "").split():
                if not render_safe:
                    self.invalid_bindings.append(
                        f"slot is outside rendered layout: {cell}-s{slot}")
                try:
                    slot_index = int(slot)
                except ValueError:
                    slot_index = -1
                self.physical_slots.setdefault(cell, []).append(slot_index)
                style = values.get("style") or ""
                geometry = self._geometry_style(
                    style, ("left", "top", "width", "height"))
                if geometry is None:
                    self.invalid_bindings.append(
                        f"slot geometry is non-canonical: {cell}-s{slot_index}")
                else:
                    self.slot_geometry.setdefault(cell, []).append({
                        "index": slot_index,
                        **geometry,
                    })
            self.div_stack.append((cell, slot_index, page_index))
            return
        if tag != "input":
            return
        slot = values.get("data-slot-index")
        identifier = values.get("id") or ""
        match = _CELL_SLOT_RE.fullmatch(identifier)
        if slot is None or match is None:
            return
        if not render_safe:
            self.invalid_bindings.append(
                f"editable input is outside rendered layout: {identifier}")
        try:
            index = int(slot)
        except ValueError:
            index = -1
        if index != int(match.group(2)):
            index = -1
        parent_cell, parent_slot, _parent_page = (
            self.div_stack[-1]
            if self.div_stack else (None, None, None))
        if parent_cell != match.group(1) or parent_slot != index:
            self.invalid_bindings.append(
                f"editable input is outside its physical slot: {identifier}")
            index = -1
        self.editable_slots.setdefault(match.group(1), []).append(index)

    def handle_decl(self, decl: str) -> None:
        self.doctype_count += 1
        if decl.strip().lower() != "doctype html":
            self.doctype_valid = False

    def handle_endtag(self, tag: str) -> None:
        if self.template_depth:
            if tag == "template":
                self.template_depth -= 1
            return
        if tag == "style":
            if not self.style_depth:
                raise RefereeError("HTML has an unmatched closing style")
            self.style_depth -= 1
            self._validate_stylesheet("".join(self.style_parts))
            self.style_parts = []
        if tag == "script":
            if not self.script_depth:
                raise RefereeError("HTML has an unmatched closing script")
            self.script_depth -= 1
            self._validate_script(
                self.script_attrs, "".join(self.script_parts))
            self.script_attrs = None
            self.script_parts = []
        if tag == "div":
            if not self.div_stack:
                raise RefereeError("HTML has an unmatched closing div")
            self.div_stack.pop()
        if tag not in HTML_VOID_ELEMENTS:
            if not self.element_stack or self.element_stack[-1][0] != tag:
                raise RefereeError(
                    f"HTML has an unmatched closing element: {tag}")
            self.element_stack.pop()

    def handle_data(self, data: str) -> None:
        if self.style_depth and not self.template_depth:
            self.style_parts.append(data)
        if self.script_depth and not self.template_depth:
            self.script_parts.append(data)

    def _validate_global_tag(
            self, tag: str,
            attrs: Sequence[tuple[str, str | None]]) -> None:
        names = [name for name, _value in attrs]
        if len(names) != len(set(names)):
            self.invalid_bindings.append(
                f"HTML {tag} element has duplicate attributes")
        if any(name.startswith("on") for name in names):
            self.invalid_bindings.append(
                f"HTML {tag} element has executable event attributes")
        if tag not in HTML_ALLOWED_TAGS:
            self.invalid_bindings.append(
                f"HTML has an unsupported emitter element: {tag}")
            return
        values = dict(attrs)
        keys = set(values)
        valid = True
        if tag in ("body", "head", "style", "title"):
            valid = not values
        elif tag == "html":
            valid = keys <= HTML_ROOT_ATTRIBUTES
        elif tag == "meta":
            valid = values == {"charset": "utf-8"}
        elif tag == "link":
            valid = (
                set(values) == HTML_LINK_ATTRIBUTES
                and values.get("rel") == "preload"
                and values.get("as") == "font"
                and values.get("type") == "font/woff2"
                and values.get("crossorigin") is None
                and values.get("href") in HTML_FONT_PRELOAD_HREFS
            )
        elif tag == "a":
            valid = (
                keys == {"class", "href"}
                and values.get("class") == "doc-link"
            )
        elif tag == "script":
            valid = (
                not values
                or values == {
                    "id": "formgen-bands",
                    "type": "application/json",
                }
            )
        elif tag == "template":
            valid = keys == {
                "data-band", "data-band-index", "data-capacity",
                "data-row-pitch", "data-row-y", "data-template-row", "id",
            }
        elif tag == "svg":
            valid = keys == {
                "class", "preserveaspectratio", "style", "viewbox", "xmlns",
            }
        elif tag == "g":
            valid = keys in ({"class"}, {"class", "id"})
        elif tag == "rect":
            valid = keys in (
                {"fill", "height", "width", "x", "y"},
                {"data-rule-id", "fill", "height", "width", "x", "y"},
            )
        elif tag == "path":
            valid = keys in (
                {"d", "data-path-id", "fill"},
                {"d", "data-path-id", "fill", "fill-rule"},
                {"d", "data-path-id", "fill", "stroke", "stroke-width"},
                {
                    "d", "data-path-id", "fill", "fill-rule",
                    "stroke", "stroke-width",
                },
            )
        elif tag == "image":
            valid = keys == {
                "data-sha256", "height", "href", "preserveaspectratio",
                "transform", "width", "x", "y",
            }
        elif tag == "input":
            # DECLARED SCHEMA CHANGE (W6/F227, 2026-08-13): a comb-slot input
            # may carry a `style` whose SOLE declaration is the writing-top
            # trim -- `inset:<T>pt 0pt 0pt 0pt` -- because the existing
            # printed-ink trim now reaches combs and expresses its result on
            # the nested input, never on the slot div (whose geometry this
            # referee pins and compares). The value shape is enforced at the
            # slot-input check below; here only the key set widens, and only
            # by `style`.
            valid = keys in (
                HTML_INPUT_ATTRIBUTES,
                HTML_INPUT_ATTRIBUTES | {"style"},
                HTML_INPUT_ATTRIBUTES - {"id", "name"},
                (HTML_INPUT_ATTRIBUTES | {"style"}) - {"id", "name"},
                {
                    "autocomplete", "class", "id", "name", "spellcheck",
                    "style", "type",
                },
                {
                    "autocomplete", "class", "spellcheck", "style", "type",
                },
            )
        if not valid:
            self.invalid_bindings.append(
                f"HTML {tag} element is outside the emitter grammar")

    @staticmethod
    def _element_role(tag: str, values: dict[str, str | None]) -> str:
        classes = set((values.get("class") or "").split())
        if tag in ("html", "body"):
            return tag
        if tag == "div" and "page" in classes:
            return "page"
        if tag == "div" and classes == {"layer-cells"}:
            return "cells"
        if tag == "div" and classes == {"layer-text"}:
            return "text-layer"
        if tag == "div" and classes == {"band"}:
            return "band"
        if tag == "div" and values.get("data-field-kind") == "comb":
            return "comb"
        if tag == "div" and classes == {"s"} and "data-slot" in values:
            return "slot"
        if tag == "div" and classes == {"t"}:
            return "text"
        if tag == "div" and classes in ({"c"}, {"c", "f"}):
            return "cell"
        return "other"

    @staticmethod
    def _emitter_attributes_valid(
            role: str, values: dict[str, str | None]) -> bool:
        keys = set(values)
        if role == "html":
            return keys <= HTML_ROOT_ATTRIBUTES
        if role == "body":
            return not values
        if role == "page":
            identifier = values.get("id") or ""
            match = _PAGE_RE.fullmatch(identifier)
            return (
                keys == {"class", "id", "style"}
                and match is not None
                and set((values.get("class") or "").split())
                == {"page", f"page-{match.group(1)}"}
            )
        if role == "cells":
            return values == {"class": "layer-cells"}
        if role == "text-layer":
            return values == {"class": "layer-text"}
        if role == "band":
            return (
                keys == HTML_BAND_ATTRIBUTES
                and values.get("class") == "band"
            )
        if role == "comb":
            return (
                keys in (
                    HTML_COMB_ATTRIBUTES,
                    HTML_COMB_ATTRIBUTES | {"data-rectangular"},
                )
                and values.get("class") == "c f"
                and values.get("data-field-kind") == "comb"
            )
        if role == "slot":
            return (
                keys == {"class", "data-slot", "style"}
                and values.get("class") == "s"
            )
        if role == "text":
            return (
                values.get("class") == "t"
                and keys in (
                    {"class", "id", "style"},
                    {"class", "data-unresolved", "id", "style"},
                    {"class", "data-band-row", "id", "style"},
                    {
                        "class", "data-band-row", "data-unresolved",
                        "id", "style",
                    },
                )
            )
        if role == "cell":
            base = {
                "class", "data-cell-kind", "data-col", "data-row",
                "id", "style",
            }
            field = base | {"data-field-kind", "data-field-name"}
            return keys in (
                base,
                base | {"data-preprinted"},
                base | {"data-rectangular"},
                field,
                field | {"data-rectangular"},
            )
        return True

    def _push_render_element(
            self, tag: str, attrs: Sequence[tuple[str, str | None]]) -> bool:
        parent_safe = self.element_stack[-1][1] if self.element_stack else True
        values = dict(attrs)
        role = self._element_role(tag, values)
        render_safe = parent_safe
        if tag == "div" and role == "other":
            self.invalid_bindings.append(
                "HTML has an unsupported div outside the emitter grammar")
            render_safe = False
        if not self._emitter_attributes_valid(role, values):
            render_safe = False
        parent_roles = [item[2] for item in self.element_stack]
        if role == "comb" and parent_roles not in (
                ["html", "body", "page", "cells"],
                ["html", "body", "page", "band"]):
            render_safe = False
        if role in ("cells", "text-layer", "band") and parent_roles != [
                "html", "body", "page"]:
            render_safe = False
        if role == "page" and parent_roles != ["html", "body"]:
            render_safe = False
        if role == "text" and (
                not parent_roles
                or parent_roles[-1] not in ("text-layer", "band")):
            render_safe = False
        if role == "cell" and (
                not parent_roles
                or parent_roles[-1] not in ("cells", "band")):
            render_safe = False
        if role == "slot" and (
                not parent_roles or parent_roles[-1] != "comb"):
            render_safe = False
        if tag == "input" and parent_roles and parent_roles[-1] == "slot":
            slot_keys = set(values)
            slot_style = values.get("style")
            # The one style a comb-slot input may declare is the writing-top
            # trim W6/F227 introduced: a top-only inset, positive, in points,
            # with the other three components exactly 0pt. Anything else --
            # any other property, any other inset shape -- stays outside the
            # grammar, so this cannot become a general styling channel.
            style_ok = slot_input_style_ok(slot_style)
            if (slot_keys not in (HTML_INPUT_ATTRIBUTES,
                                  HTML_INPUT_ATTRIBUTES | {"style"})
                    or not style_ok
                    or values.get("type") != "text"
                    or values.get("maxlength") != "1"
                    or values.get("autocomplete") != "off"
                    or values.get("spellcheck") != "false"
                    or re.fullmatch(
                        r"fi fh\d+ fc", values.get("class") or "") is None):
                render_safe = False
        if "hidden" in values:
            render_safe = False
        if tag in ("details", "dialog") and "open" not in values:
            render_safe = False
        raw_style = values.get("style") or ""
        if ("/*" in raw_style or "*/" in raw_style
                or re.search(r"!\s*important\b", raw_style,
                             flags=re.IGNORECASE)):
            render_safe = False
        declarations: set[str] = set()
        for raw in raw_style.split(";"):
            raw = raw.strip()
            if not raw:
                continue
            if ":" not in raw:
                render_safe = False
                continue
            key = raw.split(":", 1)[0].strip().lower()
            if not key or key in declarations:
                render_safe = False
                continue
            declarations.add(key)
            if key in HTML_RENDER_AFFECTING_INLINE_PROPERTIES:
                render_safe = False
        if tag not in HTML_VOID_ELEMENTS:
            self.element_stack.append((tag, render_safe, role))
        return render_safe

    @staticmethod
    def _css_leaf_blocks(css: str) -> list[tuple[str, str]]:
        """Return qualified leaf rules while respecting strings and nesting."""
        if "/*" in css or "*/" in css:
            raise RefereeError("HTML stylesheet comments are unsupported")

        def matching_brace(start: int, end: int) -> int:
            depth = 1
            quote: str | None = None
            escaped = False
            index = start + 1
            while index < end:
                char = css[index]
                if quote is not None:
                    if escaped:
                        escaped = False
                    elif char == "\\":
                        escaped = True
                    elif char == quote:
                        quote = None
                elif char in ("'", '"'):
                    quote = char
                elif char == "{":
                    depth += 1
                elif char == "}":
                    depth -= 1
                    if depth == 0:
                        return index
                index += 1
            raise RefereeError("HTML stylesheet has unbalanced braces")

        def parse_range(start: int, end: int) -> list[tuple[str, str]]:
            blocks: list[tuple[str, str]] = []
            cursor = start
            while cursor < end:
                open_brace = css.find("{", cursor, end)
                if open_brace < 0:
                    if "}" in css[cursor:end]:
                        raise RefereeError(
                            "HTML stylesheet has an unmatched closing brace")
                    break
                header = css[cursor:open_brace].strip()
                if ";" in header:
                    header = header.rsplit(";", 1)[-1].strip()
                close_brace = matching_brace(open_brace, end)
                body = css[open_brace + 1:close_brace]
                nested = parse_range(open_brace + 1, close_brace)
                if nested:
                    blocks.extend(nested)
                else:
                    if not header:
                        raise RefereeError(
                            "HTML stylesheet has a rule without a selector")
                    blocks.append((header, body))
                cursor = close_brace + 1
            return blocks

        return parse_range(0, len(css))

    def _validate_stylesheet(self, css: str) -> None:
        if re.search(r"!\s*important\b", css, flags=re.IGNORECASE):
            self.invalid_bindings.append(
                "HTML stylesheet uses unsupported !important")
            return
        if re.search(
                r"@(charset|container|import|layer|namespace|scope|supports)"
                r"\b",
                css,
                flags=re.IGNORECASE):
            self.invalid_bindings.append(
                "HTML stylesheet uses unsupported conditional or statement "
                "at-rules")
            return
        media_conditions = re.findall(
            r"@media\s+([^{}]+)\{", css, flags=re.IGNORECASE)
        if any(condition.strip().lower() not in ("print", "screen")
               for condition in media_conditions):
            self.invalid_bindings.append(
                "HTML stylesheet uses an unsupported media condition")
            return
        for selector, body in self._css_leaf_blocks(css):
            normalized_selector = re.sub(r"\s+", " ", selector.strip())
            if "#" in normalized_selector or "[" in normalized_selector:
                self.invalid_bindings.append(
                    "HTML stylesheet uses an unsupported structural selector")
                continue
            declarations: set[str] = set()
            for raw in body.split(";"):
                raw = raw.strip()
                if not raw:
                    continue
                if ":" not in raw:
                    self.invalid_bindings.append(
                        "HTML stylesheet has a malformed declaration")
                    continue
                key, value = raw.split(":", 1)
                key = key.strip().lower()
                value = re.sub(r"\s+", " ", value.strip().lower())
                if not key or key in declarations:
                    self.invalid_bindings.append(
                        "HTML stylesheet has a duplicate declaration")
                    continue
                declarations.add(key)
                if not self._stylesheet_declaration_allowed(
                        normalized_selector, key, value):
                    self.invalid_bindings.append(
                        "HTML stylesheet is outside the emitter grammar: "
                        + f"{normalized_selector} {{{key}:{value}}}")
                    continue
                declaration = (normalized_selector, key, value)
                if normalized_selector == "@page" and key == "size":
                    size = re.fullmatch(
                        rf"({_NUMBER})pt ({_NUMBER})pt", value)
                    assert size is not None
                    self.stylesheet_page_sizes.append((
                        float(size.group(1)), float(size.group(2))))
                named_page = re.fullmatch(
                    r"@page page-(\d+)", normalized_selector)
                if named_page is not None and key == "size":
                    size = re.fullmatch(
                        rf"({_NUMBER})pt ({_NUMBER})pt", value)
                    assert size is not None
                    index = int(named_page.group(1))
                    if index in self.stylesheet_named_page_sizes:
                        self.invalid_bindings.append(
                            "HTML stylesheet declares a named @page twice")
                    self.stylesheet_named_page_sizes[index] = (
                        float(size.group(1)), float(size.group(2)))
                page_binding = re.fullmatch(
                    r"\.page-(\d+)", normalized_selector)
                if page_binding is not None and key == "page":
                    self.stylesheet_named_page_selectors.add(
                        int(page_binding.group(1)))
                if declaration in HTML_STYLESHEET_STRUCTURAL_DECLARATIONS:
                    self.stylesheet_structural_declarations.add(declaration)

    @staticmethod
    def _stylesheet_declaration_allowed(
            selector: str, key: str, value: str) -> bool:
        fixed = HTML_STYLESHEET_FIXED_VALUES.get((selector, key))
        if fixed is not None:
            return value in fixed
        if re.fullmatch(r"\.fh\d+", selector):
            if key not in ("font-size", "letter-spacing", "line-height"):
                return False
            match = re.fullmatch(rf"({_NUMBER})pt", value)
            if match is None:
                return False
            number = float(match.group(1))
            return math.isfinite(number) and (
                key == "letter-spacing" or number > 0)
        if selector == ".fi" and key == "word-spacing":
            match = re.fullmatch(rf"({_NUMBER})pt", value)
            return match is not None and math.isfinite(float(match.group(1)))
        if selector == "@page" and key == "size":
            values = re.fullmatch(
                rf"({_NUMBER})pt ({_NUMBER})pt", value)
            return (
                values is not None
                and float(values.group(1)) > 0
                and float(values.group(2)) > 0
            )
        named_page = re.fullmatch(r"@page page-(\d+)", selector)
        if named_page is not None:
            if key == "margin":
                return value == "0"
            if key != "size":
                return False
            values = re.fullmatch(
                rf"({_NUMBER})pt ({_NUMBER})pt", value)
            return (
                values is not None
                and float(values.group(1)) > 0
                and float(values.group(2)) > 0
            )
        page_binding = re.fullmatch(r"\.page-(\d+)", selector)
        if page_binding is not None:
            return key == "page" and value == f"page-{page_binding.group(1)}"
        return False

    def _validate_script(
            self,
            attrs: tuple[tuple[str, str | None], ...] | None,
            body: str,
            ) -> None:
        if attrs == (
                ("id", "formgen-bands"),
                ("type", "application/json"),
                ):
            self.band_data_scripts += 1
            try:
                bands = json.loads(body)
            except json.JSONDecodeError:
                self.invalid_bindings.append(
                    "HTML band data script is malformed")
                return
            if not isinstance(bands, list):
                self.invalid_bindings.append(
                    "HTML band data script is not a list")
            return
        if attrs:
            self.invalid_bindings.append(
                "HTML has an unsupported executable script")
            return
        self.runtime_script_hashes.append(
            sha256_bytes(body.encode("utf-8")))

    @staticmethod
    def _geometry_style(
            style: str, required: Sequence[str]
            ) -> dict[str, float] | None:
        """Parse the complete inline geometry grammar, rejecting overrides."""
        declarations: dict[str, str] = {}
        for raw in style.split(";"):
            raw = raw.strip()
            if not raw:
                continue
            if ":" not in raw:
                return None
            key, value = raw.split(":", 1)
            key = key.strip().lower()
            if not key or key in declarations:
                return None
            declarations[key] = value.strip()
        if set(declarations) != set(required):
            return None
        result: dict[str, float] = {}
        for name in required:
            match = re.fullmatch(rf"\s*({_NUMBER})pt\s*",
                                 declarations[name])
            if match is None:
                return None
            value = float(match.group(1))
            if not math.isfinite(value):
                return None
            result[name] = value
        return result


def emitted_slots(path: pathlib.Path) -> dict[str, dict[str, Any]]:
    parser = SlotParser()
    parser.feed(path.read_text(encoding="utf-8"))
    parser.close()
    if (parser.template_depth or parser.div_stack or parser.element_stack
            or parser.style_depth or parser.script_depth):
        raise RefereeError("HTML ended with unclosed structural elements")
    return slot_records(parser)


def slot_records(
        parser: SlotParser,
        expected: dict[str, dict[str, Any]] | None = None,
        ) -> dict[str, dict[str, Any]]:
    result = {}
    if parser.require_runtime_contract and not parser.document_contract_checked:
        if parser.doctype_count != 1 or not parser.doctype_valid:
            parser.invalid_bindings.append(
                "HTML is not bound to one standards-mode doctype")
        if parser.style_count != 1:
            parser.invalid_bindings.append(
                f"HTML has {parser.style_count} document stylesheets, expected 1")
        if parser.band_data_scripts != 1:
            parser.invalid_bindings.append(
                "HTML has no unique formgen band-data script")
        if tuple(parser.runtime_script_hashes) != HTML_RUNTIME_SCRIPT_SHA256:
            parser.invalid_bindings.append(
                "HTML runtime scripts disagree with the reviewed emitter")
        geometry_by_index = {
            index: (width, height)
            for index, width, height in parser.page_geometry
        }
        page_sizes = set(geometry_by_index.values())
        if (len(parser.stylesheet_page_sizes) != 1
                or not geometry_by_index
                or parser.stylesheet_page_sizes[0] != geometry_by_index[
                    min(geometry_by_index)]):
            parser.invalid_bindings.append(
                "HTML @page size disagrees with emitted page geometry")
        # A single-size document must NOT carry named page rules, and a
        # mixed-size one must carry exactly one per emitted page, each bound to
        # that page's own geometry and to its own `.page-N{page:page-N}`. The
        # old contract demanded `len(page_sizes) == 1` outright, which made
        # 1604-CF's four correct named rules read as thirteen grammar
        # violations -- and `slot_records` folds `parser.invalid_bindings` into
        # every cell's `valid`, so all ten of its combs were published as
        # emission disagreements they are not.
        named_sizes = parser.stylesheet_named_page_sizes
        named_selectors = parser.stylesheet_named_page_selectors
        if len(page_sizes) == 1:
            if named_sizes or named_selectors:
                parser.invalid_bindings.append(
                    "HTML declares named page rules for uniform paper")
        elif (named_selectors != set(named_sizes)
              or not set(geometry_by_index) <= set(named_sizes)
              or any(named_sizes[index] != geometry_by_index[index]
                     for index in geometry_by_index)
              or any(index in parser.pages
                     for index in set(named_sizes) - set(geometry_by_index))):
            # Every page this document renders must carry a named rule bound to
            # its own geometry. A named rule may survive for a page the guide
            # reclaimed (1604-CF's page 4 prints from guide.html), but only if
            # nothing in this document is bound to it -- an unreferenced rule
            # cannot move a page that is here, and a referenced one is checked.
            parser.invalid_bindings.append(
                "HTML named @page rules disagree with emitted page geometry")
        parser.document_contract_checked = True
    missing_stylesheet_contract = (
        HTML_REQUIRED_STYLESHEET_DECLARATIONS
        - parser.stylesheet_structural_declarations
    )
    if missing_stylesheet_contract:
        parser.invalid_bindings.append(
            "HTML stylesheet is missing required structural declarations: "
            + ", ".join(
                f"{selector} {{{key}:{value}}}"
                for selector, key, value in sorted(missing_stylesheet_contract)
            ))
    page_bounds = {
        index: (width, height)
        for index, width, height in parser.page_geometry
    }
    for cell in sorted(parser.comb_containers):
        physical = parser.physical_slots.get(cell, [])
        ordered = sorted(physical)
        editable = sorted(parser.editable_slots.get(cell, ()))
        physical_set = set(physical)
        geometry = sorted(
            parser.slot_geometry.get(cell, ()),
            key=lambda item: int(item["index"]),
        )
        container = parser.comb_geometry.get(cell)
        container_position = parser.comb_position.get(cell)
        page_index = parser.comb_page.get(cell)
        page = page_bounds.get(page_index) if page_index is not None else None
        container_on_page = (
            container is not None
            and container_position is not None
            and page is not None
            and float(container_position[0]) >= -HTML_GEOMETRY_EPSILON_PT
            and float(container_position[1]) >= -HTML_GEOMETRY_EPSILON_PT
            and float(container_position[0]) + float(container[0])
            <= float(page[0]) + HTML_GEOMETRY_EPSILON_PT
            and float(container_position[1]) + float(container[1])
            <= float(page[1]) + HTML_GEOMETRY_EPSILON_PT
        )
        declared_capacity, declared_count = parser.declared_slots.get(
            cell, (-1, -1))
        expected_cell = expected.get(cell) if expected is not None else None
        expected_geometry = (
            expected_cell.get("slots")
            if isinstance(expected_cell, dict) else None
        )
        layout_binding_valid = (
            isinstance(expected_cell, dict)
            and page_index == expected_cell.get("page_index")
            and container_position is not None
            and container is not None
            and all(
                abs(actual - target) <= HTML_GEOMETRY_EPSILON_PT
                for actual, target in zip(
                    (*container_position, *container),
                    (
                        float(expected_cell["left"]),
                        float(expected_cell["top"]),
                        float(expected_cell["width"]),
                        float(expected_cell["height"]),
                    ),
                )
            )
            and isinstance(expected_geometry, list)
            and len(geometry) == len(expected_geometry)
            and all(
                int(actual["index"]) == int(target["index"])
                and all(
                    abs(float(actual[name]) - float(target[name]))
                    <= HTML_GEOMETRY_EPSILON_PT
                    for name in ("left", "top", "width", "height")
                )
                for actual, target in zip(geometry, expected_geometry)
            )
        )
        geometry_valid = (
            container is not None
            and container_on_page
            and float(container[0]) > 0
            and float(container[1]) > 0
            and bool(geometry)
            and len(geometry) == len(physical)
            and [int(item["index"]) for item in geometry] == ordered
            and all(
                float(item["width"]) > 0
                and float(item["height"]) > 0
                and float(item["left"]) >= -HTML_GEOMETRY_EPSILON_PT
                and float(item["left"]) + float(item["width"])
                <= float(container[0]) + HTML_GEOMETRY_EPSILON_PT
                and max(0.0, float(item["top"]))
                < min(float(container[1]),
                      float(item["top"]) + float(item["height"]))
                for item in geometry
            )
        )
        if geometry_valid and geometry:
            # A comb runs between its own printed RAILS, and those need not be
            # the container's edges: the container is the lattice cell, whose x
            # is the mean centre of every collinear bar on that line, and the
            # cell may also rule a caption or a dash box beside the comb, which
            # the comb does not own. So the run is no longer required to START
            # at 0 and END at the container's width. It is still required to
            # lie inside the container (above, unchanged) and to be contiguous
            # (below, unchanged), which is what keeps the compartments one
            # partition of one field rather than a scattering of boxes.
            geometry_valid = (
                all(
                    abs(
                        float(right["left"])
                        - (float(left["left"]) + float(left["width"]))
                    ) <= HTML_GEOMETRY_EPSILON_PT
                    and abs(
                        max(0.0, float(right["top"]))
                        - max(0.0, float(left["top"]))
                    )
                    <= HTML_GEOMETRY_EPSILON_PT
                    and abs(
                        min(float(container[1]),
                            float(right["top"]) + float(right["height"]))
                        - min(float(container[1]),
                              float(left["top"]) + float(left["height"]))
                    )
                    <= HTML_GEOMETRY_EPSILON_PT
                    for left, right in zip(geometry, geometry[1:])
                )
            )
        result[cell] = {
            "count": len(physical),
            "indexes": ordered,
            "editable_indexes": editable,
            "declared_capacity": declared_capacity,
            "declared_count": declared_count,
            "page_index": page_index,
            "container_position": (
                list(container_position)
                if container_position is not None else None
            ),
            "container_geometry": (
                list(container) if container is not None else None
            ),
            "layout_binding_valid": layout_binding_valid,
            "expected_geometry": expected_cell,
            "slot_geometry": geometry,
            "valid": (
                len(physical) == len(set(physical))
                and -1 not in physical
                and ordered == list(range(len(physical)))
                and declared_capacity == len(physical)
                and declared_count == len(physical)
                and geometry_valid
                and layout_binding_valid
                and len(editable) == len(set(editable))
                and -1 not in editable
                and all(index in physical_set for index in editable)
                and not parser.invalid_bindings
            ),
        }
    return result


def relocated_cells(data: dict[str, Any]) -> set[str]:
    cells: set[str] = set()
    for region in data.get("inline") or ():
        cells.update(region.get("cell_ids") or ())
    return cells


def emitted_geometry_contract(
        layout: dict[str, Any], guide: dict[str, Any],
        pages: dict[int, SvgPage],
        ) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    """Build exact main-form comb geometry, including guide cut straddlers.

    The VERTICAL extent of a slot is the comb's WRITING SURFACE
    (`writing_y0`/`writing_y1`), never its `y0`/`y1`.  Those two keys are the
    band the source's divider TICKS span -- typically a ~2.9pt stub at the foot
    of a ~17pt cell -- and `lattice.comb_on_writing_surface` says so at its own
    declaration.  Expecting the tick band made `layout_binding_valid` false on
    every emitting comb in the corpus, which forced all 4,550 of them to
    `stale-generation` and published 4,583 "mismatches" that did not exist.

    The OUTER HORIZONTAL extent is the same distinction on the other axis, and
    the same trap.  `slot_x` runs rail CENTRE to rail centre; the compartments
    are laid on `writing_x0`/`writing_x1`, those rails' own ink edges, because
    a rail is a painted stroke and a box starting at its centre starts inside
    the printed rule.  Every INTERNAL edge is `slot_x`'s, untouched: a divider
    is one stroke shared by the compartments either side of it.

    Alongside the contract this returns, per cell, the source's own verdict on
    that writing band: the vertical number is otherwise one the lattice asserts
    freely, whereas the horizontal ones are bound to Poppler's
    `source_divider_x` (internally) and to `audit.source_frame_geometry`'s
    independently measured rail ink (at the outer edges).
    """
    relocated = relocated_cells(guide)
    clipped_form_boxes: dict[str, dict[str, Any]] = {}
    for region in guide.get("inline") or ():
        for straddler in region.get("straddlers") or ():
            if (straddler.get("kind") != "cell"
                    or straddler.get("disposition") != "clipped"):
                continue
            cell_id = str(straddler.get("ref") or "")
            form_box = straddler.get("form")
            if not _CELL_RE.fullmatch(cell_id) or not isinstance(form_box, dict):
                raise RefereeError("guide has an invalid clipped cell straddler")
            if cell_id in clipped_form_boxes:
                raise RefereeError(
                    f"guide clips one cell more than once: {cell_id}")
            clipped_form_boxes[cell_id] = form_box

    result: dict[str, dict[str, Any]] = {}
    corroborations: dict[str, dict[str, Any]] = {}
    for page in layout.get("pages") or ():
        page_index = int(page["index"])
        for cell in page.get("cells") or ():
            comb = cell.get("comb")
            cell_id = str(cell.get("id") or "")
            if not comb or cell_id in relocated:
                continue
            full_box = {
                name: float(cell[name])
                for name in ("x0", "y0", "x1", "y1")
            }
            box = clipped_form_boxes.get(cell_id, full_box)
            if cell_id in clipped_form_boxes:
                straddler = next(
                    item
                    for region in guide.get("inline") or ()
                    for item in region.get("straddlers") or ()
                    if item.get("kind") == "cell"
                    and item.get("ref") == cell_id
                    and item.get("disposition") == "clipped"
                )
                if any(
                    abs(float(straddler[name]) - full_box[name]) > 1e-8
                    for name in ("x0", "y0", "x1", "y1")
                ):
                    raise RefereeError(
                        f"guide/layout clipped cell provenance disagrees: {cell_id}")
            try:
                box_values = {
                    name: float(box[name])
                    for name in ("x0", "y0", "x1", "y1")
                }
                slot_x = [float(value) for value in comb["slot_x"]]
                writing_y0 = finite_number(
                    comb["writing_y0"], f"{cell_id} comb writing_y0")
                writing_y1 = finite_number(
                    comb["writing_y1"], f"{cell_id} comb writing_y1")
                writing_x0 = finite_number(
                    comb["writing_x0"], f"{cell_id} comb writing_x0")
                writing_x1 = finite_number(
                    comb["writing_x1"], f"{cell_id} comb writing_x1")
                count = int(comb["cells"])
            except (KeyError, TypeError, ValueError):
                raise RefereeError(
                    f"layout comb geometry is incomplete: {cell_id}")
            # The compartment boundaries as LAID OUT: the writing edges outside,
            # the measured dividers inside. Derived here from the layout's own
            # published keys, independently of the producer that wrote them and
            # of `gate._emission_geometry_from_layout`, which must arrive at the
            # identical vector by its own route.
            laid_x = [writing_x0, *slot_x[1:-1], writing_x1]
            if (len(slot_x) != count + 1
                    or any(right <= left
                           for left, right in zip(slot_x, slot_x[1:]))
                    or any(right <= left
                           for left, right in zip(laid_x, laid_x[1:]))
                    or writing_x0 < slot_x[0] - HTML_GEOMETRY_EPSILON_PT
                    or writing_x1 > slot_x[-1] + HTML_GEOMETRY_EPSILON_PT
                    or box_values["x1"] <= box_values["x0"]
                    or box_values["y1"] <= box_values["y0"]
                    or writing_y1 <= writing_y0
                    or writing_y0 < full_box["y0"] - HTML_GEOMETRY_EPSILON_PT
                    or writing_y1 > full_box["y1"] + HTML_GEOMETRY_EPSILON_PT):
                raise RefereeError(
                    f"layout comb geometry is invalid: {cell_id}")
            source_page = pages.get(page_index)
            if source_page is None:
                raise RefereeError(
                    f"no source page raster to corroborate: {cell_id}")
            corroborations[cell_id] = writing_band_corroboration(
                full_box, cell.get("border"), comb, source_page)
            result[cell_id] = {
                "page_index": page_index,
                "left": box_values["x0"],
                "top": box_values["y0"],
                "width": box_values["x1"] - box_values["x0"],
                "height": box_values["y1"] - box_values["y0"],
                "slots": [
                    {
                        "index": index,
                        "left": left - box_values["x0"],
                        "top": writing_y0 - box_values["y0"],
                        "width": right - left,
                        "height": writing_y1 - writing_y0,
                    }
                    for index, (left, right)
                    in enumerate(zip(laid_x, laid_x[1:]))
                ],
            }
    return result, corroborations


def exact_nonnegative_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise RefereeError(f"{label} is not a non-negative integer")
    return value


def finite_number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RefereeError(f"{label} is not numeric")
    result = float(value)
    if not math.isfinite(result):
        raise RefereeError(f"{label} is not finite")
    return result


def string_list(value: Any, label: str, *, nonempty: bool = False
                ) -> list[str]:
    if (not isinstance(value, list)
            or any(not isinstance(item, str) or not item for item in value)
            or len(value) != len(set(value))
            or (nonempty and not value)):
        raise RefereeError(f"{label} is not a unique string list")
    return value


def same_numbers(left: Sequence[Any], right: Sequence[Any]) -> bool:
    """Exact serialized-number equality, allowing only float representation."""
    return (
        len(left) == len(right)
        and all(abs(float(a) - float(b)) <= 1e-9
                for a, b in zip(left, right))
    )


def decimal_identity(value: Any, label: str) -> str:
    """Return audit.py's exact, non-exponent Decimal identity."""
    if isinstance(value, bool):
        raise RefereeError(f"{label} is not a decimal number")
    try:
        if isinstance(value, Decimal):
            number = value
        elif isinstance(value, int):
            number = Decimal(value)
        elif isinstance(value, str):
            number = Decimal(value)
        else:
            raise RefereeError(f"{label} is not an exact decimal value")
    except InvalidOperation as error:
        raise RefereeError(f"{label} is not a decimal number") from error
    if not number.is_finite():
        raise RefereeError(f"{label} is not a finite decimal number")
    rendered = format(number, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return "0" if rendered in {"", "-0"} else rendered


def canonical_decimal_string(value: Any, label: str) -> Decimal:
    if not isinstance(value, str) or not value:
        raise RefereeError(f"{label} is not a decimal string")
    try:
        number = Decimal(value)
    except InvalidOperation as error:
        raise RefereeError(f"{label} is not a decimal string") from error
    if not number.is_finite() or decimal_identity(number, label) != value:
        raise RefereeError(f"{label} is not a canonical decimal string")
    return number


def audit_owner_binding(
        layout_payload: bytes,
        ledger: dict[str, Any],
        ) -> dict[str, Any]:
    """Build exact expected owner certificates from retained layout bytes."""
    try:
        retained = json.loads(
            layout_payload.decode("utf-8"), parse_float=Decimal)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RefereeError(
            "retained layout bytes are not exact UTF-8 JSON") from error
    pages = retained.get("pages") if isinstance(retained, dict) else None
    if not isinstance(pages, list) or not pages:
        raise RefereeError("retained layout has no exact page inventory")
    active_subjects: dict[str, dict[str, Any]] = {}
    for subject in ledger.get("subjects") or ():
        if subject.get("state") not in {
                "active_resolved", "active_unresolved"}:
            continue
        cell_id = subject.get("cell_id")
        if not isinstance(cell_id, str) or cell_id in active_subjects:
            raise RefereeError(
                "active ledger owner identities are not unique")
        active_subjects[cell_id] = subject

    layout_sha256 = sha256_bytes(layout_payload)
    certificates: dict[str, dict[str, Any]] = {}
    for expected_page, page in enumerate(pages, 1):
        if (not isinstance(page, dict)
                or page.get("index") != expected_page
                or not isinstance(page.get("cells"), list)):
            raise RefereeError(
                "retained layout pages are not exhaustive and ordered")
        for cell in page["cells"]:
            if not isinstance(cell, dict):
                raise RefereeError("retained layout contains a malformed cell")
            cell_id = cell.get("id")
            subject = active_subjects.get(cell_id)
            if subject is None:
                continue
            page_match = (
                _CELL_PAGE_RE.fullmatch(cell_id)
                if isinstance(cell_id, str) else None
            )
            if page_match is None or int(page_match.group(1)) != expected_page:
                raise RefereeError(
                    "retained layout owner cell does not identify its page")
            subject_key = cell.get("subject_key")
            if (subject.get("page") != expected_page
                    or subject.get("cell_id") != cell_id
                    or subject.get("legacy_cell_id") != cell_id
                    or subject.get("subject_key") != subject_key):
                raise RefereeError(
                    f"retained layout owner disagrees with ledger: {cell_id}")
            bbox_values = [
                cell.get(name) for name in ("x0", "y0", "x1", "y1")
            ]
            bbox = [
                decimal_identity(
                    value, f"retained layout owner {cell_id} bbox")
                for value in bbox_values
            ]
            bbox_numbers = [Decimal(value) for value in bbox]
            if (bbox_numbers[2] <= bbox_numbers[0]
                    or bbox_numbers[3] <= bbox_numbers[1]):
                raise RefereeError(
                    f"retained layout owner has non-positive bbox: {cell_id}")
            subject_match = (
                _SUBJECT_KEY_RE.fullmatch(subject_key)
                if isinstance(subject_key, str) else None
            )
            if subject_match is None or int(subject_match.group(1)) != expected_page:
                raise RefereeError(
                    f"retained layout owner has invalid subject_key: {cell_id}")
            encoded_bbox = [
                Decimal(subject_match.group(index)) for index in range(2, 6)
            ]
            if encoded_bbox != bbox_numbers:
                raise RefereeError(
                    f"retained layout owner subject_key/bbox differ: {cell_id}")
            if cell_id in certificates:
                raise RefereeError(
                    f"retained layout duplicates active owner: {cell_id}")
            certificates[cell_id] = {
                "criterion": AUDIT_OWNER_CERTIFICATE_CRITERION,
                "valid": True,
                "layout_sha256": layout_sha256,
                "page": expected_page,
                "cell_id": cell_id,
                "legacy_cell_id": cell_id,
                "subject_key": subject_key,
                "legacy_bbox": bbox,
                "bbox_number_format": "canonical-decimal-string-v1",
                "state": subject["state"],
                "supplies_topology": False,
            }
    if set(certificates) != set(active_subjects):
        raise RefereeError(
            "retained layout and active ledger owner inventories differ")
    return {
        "layout_sha256": layout_sha256,
        "cells": certificates,
    }


def validate_audit_owner_certificate(
        value: Any,
        expected: dict[str, Any] | None,
        ) -> dict[str, Any]:
    """Validate one identity-only audit certificate, never source topology."""
    if not isinstance(value, dict):
        raise RefereeError("audit offender owner certificate is missing")
    if value.get("criterion") != AUDIT_OWNER_CERTIFICATE_CRITERION:
        raise RefereeError("audit offender owner criterion is invalid")
    if value.get("valid") is True:
        if (set(value) != AUDIT_OWNER_CERTIFICATE_VALID_KEYS
                or value.get("supplies_topology") is not False):
            raise RefereeError(
                "audit offender valid owner certificate schema is false")
        layout_sha = value.get("layout_sha256")
        if (not isinstance(layout_sha, str)
                or re.fullmatch(r"[0-9a-f]{64}", layout_sha) is None):
            raise RefereeError(
                "audit offender owner layout SHA-256 is invalid")
        page = exact_nonnegative_int(
            value.get("page"), "audit offender owner page")
        if page == 0:
            raise RefereeError("audit offender owner page is not one-based")
        cell_id = value.get("cell_id")
        legacy_cell_id = value.get("legacy_cell_id")
        cell_match = (
            _CELL_PAGE_RE.fullmatch(cell_id)
            if isinstance(cell_id, str) else None
        )
        if (cell_match is None or int(cell_match.group(1)) != page
                or legacy_cell_id != cell_id):
            raise RefereeError(
                "audit offender owner cell identity is invalid")
        subject_key = value.get("subject_key")
        subject_match = (
            _SUBJECT_KEY_RE.fullmatch(subject_key)
            if isinstance(subject_key, str) else None
        )
        raw_bbox = value.get("legacy_bbox")
        if (subject_match is None or int(subject_match.group(1)) != page
                or not isinstance(raw_bbox, list) or len(raw_bbox) != 4):
            raise RefereeError(
                "audit offender owner subject/bbox identity is invalid")
        bbox = [
            canonical_decimal_string(
                item, "audit offender owner legacy_bbox")
            for item in raw_bbox
        ]
        encoded_bbox = [
            Decimal(subject_match.group(index)) for index in range(2, 6)
        ]
        if (encoded_bbox != bbox or bbox[2] <= bbox[0]
                or bbox[3] <= bbox[1]):
            raise RefereeError(
                "audit offender owner subject_key/bbox relation is false")
        if (value.get("bbox_number_format")
                != "canonical-decimal-string-v1"):
            raise RefereeError(
                "audit offender owner bbox number format is invalid")
        if value.get("state") not in {
                "active_resolved", "active_unresolved"}:
            raise RefereeError("audit offender owner state is invalid")
        if expected is not None and value != expected:
            raise RefereeError(
                "audit offender owner certificate is not layout-bound")
        return value
    if (set(value) != AUDIT_OWNER_CERTIFICATE_INVALID_KEYS
            or value.get("valid") is not False
            or value.get("supplies_topology") is not False
            or not isinstance(value.get("reason"), str)
            or not value["reason"]):
        raise RefereeError(
            "audit offender invalid owner certificate schema is false")
    return value


def validate_subject_identity(
        subject_key: Any,
        legacy_cell_id: Any,
        page_index: int,
        bbox_value: Any,
        label: str,
        ) -> tuple[str, str, list[float]]:
    if not isinstance(subject_key, str):
        raise RefereeError(f"{label} has no string subject_key")
    match = _SUBJECT_KEY_RE.fullmatch(subject_key)
    if match is None or int(match.group(1)) != page_index:
        raise RefereeError(f"{label} has an invalid subject_key")
    if not isinstance(legacy_cell_id, str):
        raise RefereeError(f"{label} has no string legacy_cell_id")
    cell_match = _CELL_PAGE_RE.fullmatch(legacy_cell_id)
    if cell_match is None or int(cell_match.group(1)) != page_index:
        raise RefereeError(f"{label} has an invalid legacy_cell_id")
    if not isinstance(bbox_value, list) or len(bbox_value) != 4:
        raise RefereeError(f"{label} has no four-number legacy_bbox")
    bbox_values = [
        finite_number(value, f"{label} legacy_bbox")
        for value in bbox_value
    ]
    if (bbox_values[2] <= bbox_values[0]
            or bbox_values[3] <= bbox_values[1]):
        raise RefereeError(f"{label} has a non-positive legacy_bbox")
    encoded_bbox = [float(match.group(index)) for index in range(2, 6)]
    if not same_numbers(encoded_bbox, bbox_values):
        raise RefereeError(
            f"{label} subject_key disagrees with legacy_bbox")
    return subject_key, legacy_cell_id, bbox_values


def validate_comb_topology(
        comb: Any,
        bbox_value: Sequence[Any],
        label: str,
        ) -> dict[str, Any]:
    if not isinstance(comb, dict):
        raise RefereeError(f"{label} has no comb topology")
    cells = exact_nonnegative_int(comb.get("cells"), f"{label} cells")
    divider_count = exact_nonnegative_int(
        comb.get("divider_count"), f"{label} divider_count")
    if cells < 1 or divider_count != cells - 1:
        raise RefereeError(f"{label} cells/divider_count topology disagrees")
    raw_dividers = comb.get("divider_x")
    raw_slots = comb.get("slot_x")
    if not isinstance(raw_dividers, list) or not isinstance(raw_slots, list):
        raise RefereeError(f"{label} has no divider_x/slot_x topology")
    dividers = [
        finite_number(value, f"{label} divider_x")
        for value in raw_dividers
    ]
    slots = [
        finite_number(value, f"{label} slot_x")
        for value in raw_slots
    ]
    if len(dividers) != divider_count or len(slots) != cells + 1:
        raise RefereeError(f"{label} divider_x/slot_x inventory disagrees")
    if any(right <= left for left, right in zip(slots, slots[1:])):
        raise RefereeError(f"{label} slot_x is not strictly increasing")
    if not same_numbers(slots[1:-1], dividers):
        raise RefereeError(f"{label} divider_x disagrees with slot_x")
    bbox_numbers = [
        finite_number(value, f"{label} bbox") for value in bbox_value
    ]
    # A comb's outer slot boundaries are its PRINTED RAILS, and those are not
    # the subject's rectangle. The rectangle's x is a fused lattice position --
    # the mean centre of every collinear bar on that line -- while the rail is
    # the bar that crosses THIS band; and a rectangle may rule more than the
    # comb, a caption or a dash box beside it, in which case the comb starts
    # where its own rail is drawn.
    #
    # What must still hold is that every COMPARTMENT belongs to this subject,
    # and that is exactly the statement that its centre lies inside the
    # rectangle. A compartment centred outside it is the subject next door's,
    # however the rails bounding it were derived, and a rail sitting a fraction
    # of a point beyond a fused edge takes nothing from anybody. Over this
    # corpus all 39,573 compartments satisfy it.
    if len(bbox_numbers) != 4 or not all(
            bbox_numbers[0] < (left + right) / 2.0 < bbox_numbers[2]
            for left, right in zip(slots, slots[1:])):
        raise RefereeError(f"{label} slot_x disagrees with subject bbox")
    y0 = finite_number(comb.get("y0"), f"{label} y0")
    y1 = finite_number(comb.get("y1"), f"{label} y1")
    if y1 <= y0:
        raise RefereeError(f"{label} has a non-positive comb band")
    pitch = finite_number(comb.get("pitch_pt"), f"{label} pitch_pt")
    if pitch <= 0:
        raise RefereeError(f"{label} has no positive pitch")
    resolution = comb.get("resolution")
    if not isinstance(resolution, dict):
        raise RefereeError(f"{label} has no resolution record")
    resolution_status = resolution.get("status")
    if resolution_status not in ("resolved", "unresolved"):
        raise RefereeError(f"{label} has an unknown resolution status")
    reason_codes = string_list(
        resolution.get("reason_codes"), f"{label} resolution reason_codes")
    if bool(reason_codes) != (resolution_status == "unresolved"):
        raise RefereeError(f"{label} resolution reasons/status disagree")
    topology = {
        "cells": cells,
        "divider_x": dividers,
        "slot_x": slots,
        "y0": y0,
        "y1": y1,
        "resolution_status": resolution_status,
        "reason_codes": reason_codes,
    }
    topology["sha256"] = canonical_digest(topology)
    return topology


def bind_lattice_generator(
        layout: dict[str, Any],
        lattice_producer_bytes: bytes,
        ) -> dict[str, Any]:
    actual_sha = sha256_bytes(lattice_producer_bytes)
    if actual_sha != LATTICE_PRODUCER_SHA256:
        raise RefereeError(
            "lattice producer bytes disagree with the committed pin")
    generator = layout.get("generator")
    if (not isinstance(generator, dict)
            or set(generator) != LATTICE_GENERATOR_KEYS
            or generator != LATTICE_GENERATOR_CONTRACT):
        raise RefereeError(
            "layout lattice generator contract is missing or stale")
    return {
        "file": LATTICE_PRODUCER_FILE,
        "bytes": len(lattice_producer_bytes),
        "sha256": actual_sha,
        "expected_sha256": LATTICE_PRODUCER_SHA256,
        "layout_generator": dict(generator),
    }


def validate_comb_ledger(
        slug: str,
        layout: dict[str, Any],
        lattice_producer_bytes: bytes,
        ) -> dict[str, Any]:
    """Bind the immutable 4,442-subject denominator to active layout cells.

    The legacy ledger is identity and continuity evidence.  It never promotes
    an unresolved current comb, and a retained subject remains published even
    though no active cell is allowed to emit it.
    """
    expected_total = EXPECTED_COMBS_BY_SLUG.get(slug)
    if expected_total is None:
        raise RefereeError(f"{slug}: form is not in the pinned referee corpus")
    lattice = bind_lattice_generator(layout, lattice_producer_bytes)
    pages = layout.get("pages")
    if not isinstance(pages, list) or not pages:
        raise RefereeError(f"{slug}: layout has no page inventory")

    published_subjects: list[dict[str, Any]] = []
    published_inferences: list[dict[str, Any]] = []
    active_cell_ids: set[str] = set()
    retained_legacy_ids: set[str] = set()
    inference_cell_ids: set[str] = set()
    global_subject_keys: set[str] = set()
    global_legacy_ids: set[str] = set()

    for expected_page, page in enumerate(pages, 1):
        if not isinstance(page, dict) or page.get("index") != expected_page:
            raise RefereeError(
                f"{slug}: ledger pages are not exhaustive and ordered")
        page_index = expected_page
        raw_cells = page.get("cells")
        if not isinstance(raw_cells, list):
            raise RefereeError(f"{slug} page {page_index}: cells is not a list")
        cells_by_id: dict[str, dict[str, Any]] = {}
        cells_by_subject: dict[str, dict[str, Any]] = {}
        for raw_cell in raw_cells:
            if not isinstance(raw_cell, dict):
                raise RefereeError(
                    f"{slug} page {page_index}: malformed layout cell")
            cell_id = raw_cell.get("id")
            subject_key = raw_cell.get("subject_key")
            if not isinstance(cell_id, str) or not _CELL_RE.fullmatch(cell_id):
                raise RefereeError(
                    f"{slug} page {page_index}: layout cell has invalid id")
            cell_match = _CELL_PAGE_RE.fullmatch(cell_id)
            if cell_match is None or int(cell_match.group(1)) != page_index:
                raise RefereeError(
                    f"{slug} page {page_index}: layout cell id is on another page")
            bbox_value = [
                raw_cell.get(name) for name in ("x0", "y0", "x1", "y1")
            ]
            validate_subject_identity(
                subject_key, cell_id, page_index, bbox_value,
                f"{slug} page {page_index} layout cell {cell_id}")
            if cell_id in cells_by_id or subject_key in cells_by_subject:
                raise RefereeError(
                    f"{slug} page {page_index}: duplicate layout cell identity")
            cells_by_id[cell_id] = raw_cell
            cells_by_subject[str(subject_key)] = raw_cell

        if "comb_subjects" not in page:
            raise RefereeError(
                f"{slug} page {page_index}: comb subject ledger is missing")
        subjects = page["comb_subjects"]
        if not isinstance(subjects, list):
            raise RefereeError(
                f"{slug} page {page_index}: comb subject ledger is not a list")
        if "comb_inferences" not in page:
            raise RefereeError(
                f"{slug} page {page_index}: comb inference ledger is missing")
        inferences = page["comb_inferences"]
        if not isinstance(inferences, list):
            raise RefereeError(
                f"{slug} page {page_index}: comb inference ledger is not a list")

        page_subject_keys: set[str] = set()
        page_legacy_ids: set[str] = set()
        page_active_ids: set[str] = set()
        page_active_order: list[str] = []
        for index, subject in enumerate(subjects):
            label = f"{slug} page {page_index} subject {index}"
            if not isinstance(subject, dict):
                raise RefereeError(f"{label} is not an object")
            subject_key, legacy_cell_id, legacy_bbox = (
                validate_subject_identity(
                    subject.get("subject_key"),
                    subject.get("legacy_cell_id"),
                    page_index,
                    subject.get("legacy_bbox"),
                    label,
                )
            )
            if (subject_key in page_subject_keys
                    or legacy_cell_id in page_legacy_ids
                    or subject_key in global_subject_keys
                    or legacy_cell_id in global_legacy_ids):
                raise RefereeError(
                    f"{label} duplicates a subject_key or legacy_cell_id")
            page_subject_keys.add(subject_key)
            page_legacy_ids.add(legacy_cell_id)
            global_subject_keys.add(subject_key)
            global_legacy_ids.add(legacy_cell_id)
            state = subject.get("state")
            if state not in COMB_SUBJECT_STATES:
                raise RefereeError(
                    f"{label} has unknown or retired state: {state}")
            reason_codes = string_list(
                subject.get("reason_codes"), f"{label} reason_codes",
                nonempty=state != "active_resolved")
            blocks_gate = subject.get("blocks_gate")
            # active_composite carries blocks_gate False: its certificate is
            # validated below against the review registry byte-for-byte, and
            # its MEASUREMENT (the R2a source corroboration) is what the
            # comparison scores -- a composite the paper refutes still fails
            # the gate through its own comparison row, never silently.
            if (not isinstance(blocks_gate, bool)
                    or blocks_gate != (state not in {
                        "active_resolved", "active_composite"})):
                raise RefereeError(
                    f"{label} state/blocks_gate contract disagrees")

            if state in {"active_resolved", "active_unresolved"}:
                cell_id = subject.get("cell_id")
                mapped_ids = subject.get("mapped_partition_cell_ids")
                if (not isinstance(cell_id, str)
                        or mapped_ids != [cell_id]
                        or cell_id in page_active_ids):
                    raise RefereeError(
                        f"{label} has no unique one-to-one active cell mapping")
                cell = cells_by_id.get(cell_id)
                if cell is None or cell.get("subject_key") != subject_key:
                    raise RefereeError(
                        f"{label} active cell subject_key/cell_id disagrees")
                cell_bbox = [
                    cell.get(name) for name in ("x0", "y0", "x1", "y1")
                ]
                if not same_numbers(legacy_bbox, cell_bbox):
                    raise RefereeError(
                        f"{label} active cell geometry changed subject identity")
                topology = validate_comb_topology(
                    cell.get("comb"), cell_bbox, f"{label} active cell")
                subject_cells = exact_nonnegative_int(
                    subject.get("cells"), f"{label} cells")
                if subject_cells != topology["cells"]:
                    raise RefereeError(
                        f"{label} ledger/cell comb counts disagree")
                certificate = subject.get("resolution_certificate")
                registry_key = (slug, page_index, str(cell_id))
                entry = review_registry.REVIEWED_LEDGER_RESOLUTIONS.get(
                    registry_key)
                if certificate is not None:
                    # The adjudicator half of the resolution path.  The
                    # certificate must match the registry byte-for-byte AND
                    # the cell's own resolution record must carry the same
                    # one -- the producer transitions two records and a forger
                    # who moved only the subject is caught here.  Whether the
                    # PAPER agrees is decided later, in `comparison`, which
                    # re-derives the four-way and refuses to let a review
                    # stand against it.
                    source_sha = str(
                        (layout.get("source") or {}).get("sha256") or "")
                    cell_certificate = (
                        ((cell.get("comb") or {}).get("resolution") or {})
                        .get("review_certificate"))
                    if (state != "active_resolved"
                            or not isinstance(certificate, dict)
                            or set(certificate) != {
                                "criterion", "registry_key", "four_way",
                                "resolved_reason_codes", "reviewer", "date"}
                            or certificate["criterion"]
                            != review_registry.RESOLUTION_CRITERION
                            or certificate["registry_key"]
                            != [slug, page_index, str(cell_id)]
                            or entry is None
                            or entry["subject_key"] != subject_key
                            or entry["source_sha256"] != source_sha
                            or certificate["four_way"] != {
                                name: int(entry["four_way"][name])
                                for name in ("lattice", "audit", "emitted",
                                             "referee")}
                            or certificate["reviewer"] != entry["reviewer"]
                            or certificate["date"] != entry["date"]
                            or cell_certificate != certificate
                            or not string_list(
                                certificate["resolved_reason_codes"],
                                f"{label} resolved_reason_codes",
                                nonempty=True)):
                        raise RefereeError(
                            f"{label} resolution certificate does not match "
                            "the review registry")
                elif entry is not None:
                    raise RefereeError(
                        f"{label} has a reviewed resolution the producer did "
                        "not apply")
                expected_resolution = (
                    "resolved" if state == "active_resolved" else "unresolved")
                if (topology["resolution_status"] != expected_resolution
                        or reason_codes != topology["reason_codes"]):
                    raise RefereeError(
                        f"{label} ledger/cell resolution evidence disagrees")
                transition = subject.get("boundary_topology_transition")
                transition_fields_present = any(
                    key in subject for key in (
                        "old_divider_x", "new_divider_x",
                        "boundary_topology_transition",
                    )
                )
                cell_transition = (
                    (cell.get("comb") or {}).get("resolution") or {}
                ).get("boundary_topology_transition")
                if transition_fields_present or cell_transition is not None:
                    if (not isinstance(transition, dict)
                            or set(transition) != {
                                "old_divider_x", "new_divider_x",
                                "comparison_tolerance_pt",
                                "independently_certified",
                            }
                            or transition != cell_transition
                            or subject.get("old_divider_x")
                            != transition.get("old_divider_x")
                            or subject.get("new_divider_x")
                            != transition.get("new_divider_x")
                            or transition.get("independently_certified") is not False
                            or transition.get("comparison_tolerance_pt")
                            != LATTICE_GENERATOR_CONTRACT[
                                "cluster_tolerance_pt"]
                            or not same_numbers(
                                transition.get("new_divider_x") or (),
                                topology["divider_x"])
                            or len(transition.get("old_divider_x") or ())
                            != topology["cells"] - 1
                            or state != "active_unresolved"
                            or "same-count-boundary-topology-change"
                            not in reason_codes):
                        raise RefereeError(
                            f"{label} boundary topology transition is invalid")
                page_active_ids.add(cell_id)
                page_active_order.append(cell_id)
                active_cell_ids.add(cell_id)
                published_subjects.append({
                    "resolution_certificate": certificate,
                    "page": page_index,
                    "subject_key": subject_key,
                    "legacy_cell_id": legacy_cell_id,
                    "cell_id": cell_id,
                    "state": state,
                    "blocks_gate": blocks_gate,
                    "reason_codes": reason_codes,
                    "legacy_bbox": legacy_bbox,
                    "source_cell": cell,
                    "topology": topology,
                    "ledger": subject,
                    "source_suppression_criterion": None,
                })
                continue

            if (subject.get("cell_id") is not None
                    or subject.get("emission") != "suppressed"
                    or subject.get("requires_independent_evidence") is not True
                    or subject.get("permitted_transitions") != [
                        "active_composite", "retired_proven_false",
                    ]):
                raise RefereeError(
                    f"{label} retained suppression evidence is incomplete")
            legacy_topology = validate_comb_topology(
                subject.get("legacy_comb"), legacy_bbox,
                f"{label} retained legacy_comb")
            # See THE RETAINED-TOPOLOGY INVARIANT above.  `reason_codes` is
            # already proven to be a unique, non-empty list of non-empty
            # strings for every non-`active_resolved` subject, so an absent or
            # malformed reason has failed before this point; what is decided
            # here is whether the reason it published is one whose factual
            # claim this referee can re-derive from the source.  Nothing is
            # granted by this lookup: a hit only defers the decision to
            # `retained_suppression_corroboration`, which runs against Poppler
            # and which `form_report` proves it ran.
            suppression_criterion = RETAINED_SUPPRESSION_SOURCE_CRITERIA.get(
                tuple(reason_codes))
            if (legacy_topology["resolution_status"] != "unresolved"
                    and suppression_criterion is None):
                raise RefereeError(
                    f"{label} retained legacy_comb is not unresolved")
            mapped_ids = string_list(
                subject.get("mapped_partition_cell_ids"),
                f"{label} mapped_partition_cell_ids")
            mapped_keys = string_list(
                subject.get("mapped_partition_subject_keys"),
                f"{label} mapped_partition_subject_keys")
            if len(mapped_ids) != len(mapped_keys):
                raise RefereeError(
                    f"{label} retained partition mappings disagree")
            for mapped_id, mapped_key in zip(mapped_ids, mapped_keys):
                mapped_cell = cells_by_id.get(mapped_id)
                if (mapped_cell is None
                        or mapped_cell.get("subject_key") != mapped_key):
                    raise RefereeError(
                        f"{label} retained partition mapping is stale")
            if (subject_key in cells_by_subject
                    and "comb" in cells_by_subject[subject_key]):
                raise RefereeError(
                    f"{label} retained subject still has an active comb")
            certificate = subject.get("transition_certificate")
            if state == "active_composite":
                # The adjudicator half of review_registry's doctrine: the
                # producer's certificate is re-validated here against the
                # registry byte-for-byte, and the criterion it names must be
                # the criterion the subject's own reason tuple tables --
                # review cannot substitute a different factual claim than the
                # paper's.  Every mismatch is an ERROR, not a downgrade.
                registry_key = (slug, page_index, legacy_cell_id)
                entry = review_registry.REVIEWED_LEDGER_TRANSITIONS.get(
                    registry_key)
                source_sha = str(
                    (layout.get("source") or {}).get("sha256") or "")
                if (not isinstance(certificate, dict)
                        or set(certificate) != {
                            "criterion", "registry_key", "transition",
                            "suppression_criterion", "reviewer", "date"}
                        or certificate["criterion"]
                        != review_registry.TRANSITION_CRITERION
                        or certificate["registry_key"]
                        != [slug, page_index, legacy_cell_id]
                        or entry is None
                        or entry["subject_key"] != subject_key
                        or entry["source_sha256"] != source_sha
                        or entry["transition"] != "active_composite"
                        or certificate["transition"] != entry["transition"]
                        or certificate["suppression_criterion"]
                        != entry["suppression_criterion"]
                        or certificate["reviewer"] != entry["reviewer"]
                        or certificate["date"] != entry["date"]):
                    raise RefereeError(
                        f"{label} composite transition certificate does not "
                        "match the review registry")
                if (suppression_criterion is None
                        or certificate["suppression_criterion"]
                        != suppression_criterion):
                    raise RefereeError(
                        f"{label} composite certificate names a criterion "
                        "the subject's reason codes do not table")
            elif certificate is not None:
                raise RefereeError(
                    f"{label} carries a transition certificate without the "
                    "transitioned state")
            elif (slug, page_index, legacy_cell_id
                    ) in review_registry.REVIEWED_LEDGER_TRANSITIONS:
                raise RefereeError(
                    f"{label} has a reviewed transition the producer did "
                    "not apply")
            retained_legacy_ids.add(legacy_cell_id)
            published_subjects.append({
                "page": page_index,
                "subject_key": subject_key,
                "legacy_cell_id": legacy_cell_id,
                "cell_id": None,
                "state": state,
                "blocks_gate": blocks_gate,
                "transition_certificate": certificate,
                "reason_codes": reason_codes,
                "legacy_bbox": legacy_bbox,
                "source_cell": {
                    "id": legacy_cell_id,
                    "subject_key": subject_key,
                    "x0": legacy_bbox[0],
                    "y0": legacy_bbox[1],
                    "x1": legacy_bbox[2],
                    "y1": legacy_bbox[3],
                    "comb": subject["legacy_comb"],
                },
                "topology": legacy_topology,
                "ledger": subject,
                "source_suppression_criterion": suppression_criterion,
            })

        comb_cells = [
            cell for cell in raw_cells if isinstance(cell.get("comb"), dict)
        ]
        comb_cell_ids = {str(cell["id"]) for cell in comb_cells}
        if page_active_ids != comb_cell_ids:
            missing = sorted(comb_cell_ids - page_active_ids)
            extra = sorted(page_active_ids - comb_cell_ids)
            raise RefereeError(
                f"{slug} page {page_index}: active ledger/cell reverse mapping "
                "disagrees"
                + (f"; missing ledger: {', '.join(missing[:8])}"
                   if missing else "")
                + (f"; unknown active: {', '.join(extra[:8])}"
                   if extra else ""))
        # Canonical order is the LAYOUT CELL STREAM, never the numeric cell id:
        # `lattice.py` hands a cell its LEGACY CONTINUITY id when the rectangle
        # matches a legacy box and otherwise draws a fresh id from the end of
        # the legacy range, so a repaired partition can seat a high-numbered
        # cell mid-page (2550M's restored p1c193 sits above p1c103-105).  Cell
        # ids identify continuity, not position.  Prove -- do not assume --
        # that the subject ledger walks the stream in stream order, so that
        # every consumer of this report can treat ledger order and document
        # order as the same order.
        comb_cell_order = [str(cell["id"]) for cell in comb_cells]
        if page_active_order != comb_cell_order:
            divergence = next(
                position for position, (ledger_id, stream_id)
                in enumerate(zip(page_active_order, comb_cell_order))
                if ledger_id != stream_id)
            raise RefereeError(
                f"{slug} page {page_index}: active subject ledger order "
                "disagrees with the layout cell stream at index "
                f"{divergence}: ledger {page_active_order[divergence]} vs "
                f"stream {comb_cell_order[divergence]}")

        page_inference_keys: set[str] = set()
        page_inference_ids: set[str] = set()
        for index, inference in enumerate(inferences):
            label = f"{slug} page {page_index} inference {index}"
            if not isinstance(inference, dict):
                raise RefereeError(f"{label} is not an object")
            state = inference.get("state")
            if state != COMB_INFERENCE_STATE:
                raise RefereeError(
                    f"{label} has unknown or unsuppressed state: {state}")
            subject_key = inference.get("subject_key")
            cell_id = inference.get("cell_id")
            bbox_value = inference.get("bbox")
            if not isinstance(subject_key, str) or not isinstance(cell_id, str):
                raise RefereeError(f"{label} has no subject_key/cell_id")
            match = _SUBJECT_KEY_RE.fullmatch(subject_key)
            cell_match = _CELL_PAGE_RE.fullmatch(cell_id)
            if (match is None or int(match.group(1)) != page_index
                    or cell_match is None
                    or int(cell_match.group(1)) != page_index
                    or not isinstance(bbox_value, list)
                    or len(bbox_value) != 4):
                raise RefereeError(f"{label} identity is invalid")
            bbox_numbers = [
                finite_number(value, f"{label} bbox") for value in bbox_value
            ]
            if not same_numbers(
                    [float(match.group(item)) for item in range(2, 6)],
                    bbox_numbers):
                raise RefereeError(
                    f"{label} subject_key disagrees with bbox")
            if (subject_key in page_inference_keys
                    or cell_id in page_inference_ids
                    or subject_key in global_subject_keys
                    or cell_id in page_active_ids):
                raise RefereeError(
                    f"{label} duplicates a ledger subject or inference")
            cell = cells_by_id.get(cell_id)
            if (cell is None or cell.get("subject_key") != subject_key
                    or "comb" in cell
                    or not same_numbers(
                        bbox_numbers,
                        [cell.get(name)
                         for name in ("x0", "y0", "x1", "y1")])):
                raise RefereeError(
                    f"{label} does not map to one suppressed layout cell")
            if (inference.get("blocks_gate") is not True
                    or inference.get("requires_independent_evidence") is not True
                    or inference.get("permitted_transitions")
                    != ["active_reviewed"]):
                raise RefereeError(
                    f"{label} is not explicit and blocking")
            reason_codes = string_list(
                inference.get("reason_codes"), f"{label} reason_codes",
                nonempty=True)
            topology = validate_comb_topology(
                inference.get("inferred_comb"), bbox_numbers,
                f"{label} inferred_comb")
            page_inference_keys.add(subject_key)
            page_inference_ids.add(cell_id)
            inference_cell_ids.add(cell_id)
            published_inferences.append({
                "page": page_index,
                "subject_key": subject_key,
                "cell_id": cell_id,
                "state": state,
                "blocks_gate": True,
                "reason_codes": reason_codes,
                "bbox": bbox_numbers,
                "topology": topology,
                "ledger": inference,
            })

        stats = page.get("stats")
        if not isinstance(stats, dict):
            raise RefereeError(
                f"{slug} page {page_index}: layout stats are missing")
        active_resolved = sum(
            subject.get("state") == "active_resolved" for subject in subjects)
        active_unresolved = sum(
            subject.get("state") == "active_unresolved" for subject in subjects)
        retained = sum(
            subject.get("state") == "retained_unresolved"
            for subject in subjects)
        subject_blockers = sum(
            subject.get("blocks_gate") is True for subject in subjects)
        inference_blockers = sum(
            inference.get("blocks_gate") is True for inference in inferences)
        page_composite = sum(
            subject.get("state") == "active_composite"
            for subject in subjects)
        expected_stats = {
            "comb_cells": len(comb_cells),
            "comb_subjects": len(subjects),
            "comb_subjects_active": (
                active_resolved + active_unresolved + page_composite),
            "comb_subjects_active_resolved": active_resolved,
            "comb_subjects_active_unresolved": active_unresolved,
            "comb_subjects_retained_unresolved": retained,
            "comb_subjects_retired": 0,
            "comb_subjects_blocking": subject_blockers,
            "comb_inferences_suppressed": len(inferences),
            "comb_inferences_blocking": inference_blockers,
            "comb_evidence_blocking": (
                subject_blockers + inference_blockers),
            "comb_slots": sum(
                exact_nonnegative_int(
                    cell["comb"].get("cells"),
                    f"{slug} page {page_index} comb cells")
                for cell in comb_cells
            ),
        }
        for key, expected_value in expected_stats.items():
            if stats.get(key) != expected_value:
                raise RefereeError(
                    f"{slug} page {page_index}: ledger stat {key} "
                    f"is {stats.get(key)!r}, expected {expected_value}")

    if len(published_subjects) != expected_total:
        raise RefereeError(
            f"{slug}: subject ledger has {len(published_subjects)} subjects, "
            f"expected pinned {expected_total}")
    if len(global_subject_keys) != expected_total:
        raise RefereeError(f"{slug}: subject ledger identities are not unique")
    active_resolved = sum(
        subject["state"] == "active_resolved"
        for subject in published_subjects)
    active_composite = sum(
        subject["state"] == "active_composite"
        for subject in published_subjects)
    active_unresolved = sum(
        subject["state"] == "active_unresolved"
        for subject in published_subjects)
    retained = sum(
        subject["state"] == "retained_unresolved"
        for subject in published_subjects)
    expected_retained = EXPECTED_RETAINED_SUBJECTS_BY_SLUG.get(slug, 0)
    # The pin counts SUPPRESSED subjects -- subjects with no active cell of
    # their own -- and a reviewed composite transition changes a subject's
    # state, never its suppressed emission or its ledger membership (Path A:
    # nothing leaves the books).  The census therefore counts retained AND
    # composite together against the same unmoved pin, and the composite
    # slice is bounded by the registry itself: every composite subject was
    # already proven above to match a registry entry byte-for-byte.
    if retained + active_composite != expected_retained:
        raise RefereeError(
            f"{slug}: subject ledger retains "
            f"{retained + active_composite} suppressed subjects "
            f"({retained} retained, {active_composite} composite), "
            f"expected pinned {expected_retained}")
    expected_active = expected_total - expected_retained
    if active_resolved + active_unresolved != expected_active:
        raise RefereeError(
            f"{slug}: subject ledger has "
            f"{active_resolved + active_unresolved} active combs, "
            f"expected pinned {expected_active}")
    blockers = sum(
        subject["blocks_gate"] for subject in published_subjects
    ) + len(published_inferences)
    # The work order for `form_report`, computed here so that the obligation is
    # created by the same pass that admits the reason.  A corroboration this
    # ledger demanded and nobody performed is an ERROR, never a pass.
    suppression_obligations = {
        subject["legacy_cell_id"]: subject["source_suppression_criterion"]
        for subject in published_subjects
        if subject["source_suppression_criterion"] is not None
    }
    return {
        "lattice": lattice,
        "subjects": published_subjects,
        "inferences": published_inferences,
        "active_cell_ids": active_cell_ids,
        "retained_legacy_ids": retained_legacy_ids,
        "inference_cell_ids": inference_cell_ids,
        "suppression_obligations": suppression_obligations,
        "counts": {
            "subjects": len(published_subjects),
            "active": active_resolved + active_unresolved + active_composite,
            "active_resolved": active_resolved,
            "active_unresolved": active_unresolved,
            "active_composite": active_composite,
            "retained_unresolved": retained,
            "inferences_suppressed": len(published_inferences),
            "blocking": blockers,
        },
    }


def validate_emission_inventory(
        ledger: dict[str, Any],
        slots: dict[str, dict[str, Any]],
        ) -> dict[str, Any]:
    """Bind every emitted comb to exactly one active ledger subject."""
    active_ids = set(ledger["active_cell_ids"])
    retained_ids = set(ledger["retained_legacy_ids"])
    inference_ids = set(ledger["inference_cell_ids"])
    emitted_ids = set(slots)
    missing = sorted(active_ids - emitted_ids)
    unexpected = sorted(emitted_ids - active_ids)
    retained_emitted = sorted(emitted_ids & retained_ids)
    inference_emitted = sorted(emitted_ids & inference_ids)
    invalid = sorted(
        cell_id for cell_id in active_ids & emitted_ids
        if not bool(slots[cell_id].get("valid"))
    )
    errors: list[str] = []
    if missing:
        errors.append(f"{len(missing)} active ledger subjects are not emitted")
    if unexpected:
        errors.append(f"{len(unexpected)} emitted combs have no active subject")
    if retained_emitted:
        errors.append(
            f"{len(retained_emitted)} retained subjects are still emitted")
    if inference_emitted:
        errors.append(
            f"{len(inference_emitted)} suppressed inferences are still emitted")
    if invalid:
        errors.append(f"{len(invalid)} active emissions are invalid")
    return {
        "complete": not errors,
        "reason": "complete" if not errors else "; ".join(errors),
        "expected_active_cell_ids": sorted(active_ids),
        "emitted_cell_ids": sorted(emitted_ids),
        "missing_active_cell_ids": missing,
        "unexpected_emitted_cell_ids": unexpected,
        "retained_emitted_cell_ids": retained_emitted,
        "inference_emitted_cell_ids": inference_emitted,
        "invalid_active_cell_ids": invalid,
    }


def composited_segments(y: float,
                        all_paints: Sequence[Paint]
                        ) -> list[dict[str, Any]]:
    """Return exact final-tone x components on one open horizontal slab.

    Looking only at paint drawn *after* a candidate is order-dependent: a thin
    black line over a broad earlier black fill and the same line below a broad
    later black fill have identical final pixels.  Partitioning at every paint
    edge and selecting the last owner makes those two cases identical and
    prevents a same-tone background from masquerading as a narrow divider.
    """
    active = [
        paint for paint in all_paints
        if paint.y0 <= y <= paint.y1 and paint.x1 - paint.x0 > 1e-9
    ]
    endpoints = sorted({
        coordinate for paint in active for coordinate in (paint.x0, paint.x1)
    })
    atomic: list[dict[str, Any]] = []
    for left, right in zip(endpoints, endpoints[1:]):
        if right - left <= 1e-9:
            continue
        midpoint = (left + right) / 2
        owners = [
            paint for paint in active
            if paint.x0 < midpoint < paint.x1
        ]
        if not owners:
            continue
        owner = max(owners, key=lambda paint: paint.order)
        atomic.append({
            "x0": left,
            "x1": right,
            "tone": owner.tone,
            "clipped": owner.clipped,
            "elements": {owner.element},
            "orders": {owner.order},
        })
    merged: list[dict[str, Any]] = []
    for segment in atomic:
        if (merged
                and abs(float(merged[-1]["x1"])
                        - float(segment["x0"])) <= 1e-6
                and abs(float(merged[-1]["tone"])
                        - float(segment["tone"])) <= 1e-8):
            merged[-1]["x1"] = segment["x1"]
            merged[-1]["clipped"] = (
                bool(merged[-1]["clipped"]) or bool(segment["clipped"]))
            merged[-1]["elements"].update(segment["elements"])
            merged[-1]["orders"].update(segment["orders"])
        else:
            merged.append({
                **segment,
                "elements": set(segment["elements"]),
                "orders": set(segment["orders"]),
            })
    return merged


def merge_centres(paints: Sequence[Paint], y: float,
                  all_paints: Sequence[Paint],
                  max_width: float) -> list[dict[str, Any]]:
    if not paints:
        return []
    tones = {round(paint.tone, 8) for paint in paints}
    if len(tones) != 1:
        raise RefereeError("divider candidates do not have one bound tone")
    target_tone = next(iter(tones))
    active_candidates = [
        paint for paint in paints if paint.y0 <= y <= paint.y1
    ]
    active_candidate_orders = {paint.order for paint in active_candidates}
    components = composited_segments(y, all_paints)
    ambiguous_occlusion = any(
        bool(component["clipped"])
        and any(
            float(component["x1"]) > paint.x0
            and float(component["x0"]) < paint.x1
            for paint in active_candidates
        )
        for component in components
    )
    groups = [
        component for component in components
        if abs(float(component["tone"]) - target_tone) <= 1e-8
        and float(component["x1"]) - float(component["x0"])
        <= max_width + 1e-6
        and bool(component["orders"])
        and set(component["orders"]).issubset(active_candidate_orders)
    ]
    return [{
        "x": round((float(group["x0"]) + float(group["x1"])) / 2, 6),
        "x0": round(float(group["x0"]), 6),
        "x1": round(float(group["x1"]), 6),
        "tone": round(float(group["tone"]), 8),
        "elements": sorted(group["elements"]),
        "clipped": bool(group["clipped"]) or ambiguous_occlusion,
    } for group in groups]


def near(value: float, target: float) -> bool:
    return abs(value - target) <= POSITION_TOL_PT


def layout_quantized(value: float) -> float:
    """Round to the precision every published layout coordinate carries."""
    return round(float(value) + 0.0, LAYOUT_QUANT_PLACES)


def final_paint_owner(paints: Sequence[Paint], x: float, y: float
                      ) -> Paint | None:
    """The paint that finally owns one point, by SVG document order."""
    active = [paint for paint in paints if paint.covers(x, y)]
    return max(
        active,
        key=lambda paint: (paint.order, paint.element, paint.kind),
        default=None,
    )


def visible_vertical_runs(
        paints: Sequence[Paint], x: float, y0: float, y1: float,
        ) -> list[tuple[float, float, Paint]]:
    """Each paint's FINALLY VISIBLE vertical extent on the ray at ``x``.

    This is the vertical twin of `composited_segments`: partition at every
    paint edge, take the last owner of each slab, and merge only slabs the
    SAME paint owns.  Merging by tone instead would join two stacked rules --
    1701's row boundaries really are two abutting 0.48pt fills, one per row --
    into a single 0.96pt wall and mis-measure both cells that share it.
    """
    local = [
        paint for paint in paints
        if paint.x0 <= x <= paint.x1 and paint.y1 > y0 and paint.y0 < y1
    ]
    edges = {y0, y1}
    for paint in local:
        for value in (paint.y0, paint.y1):
            if y0 < value < y1:
                edges.add(value)
    runs: list[tuple[float, float, Paint]] = []
    ordered = sorted(edges)
    for top, bottom in zip(ordered, ordered[1:]):
        if bottom - top <= 1e-9:
            continue
        owner = final_paint_owner(local, x, (top + bottom) / 2)
        if owner is None:
            continue
        if (runs and runs[-1][2] is owner
                and abs(runs[-1][1] - top) <= 1e-9):
            runs[-1] = (runs[-1][0], bottom, owner)
        else:
            runs.append((top, bottom, owner))
    return runs


def closes_subject_box(paints: Sequence[Paint], x: float,
                       y0: float, y1: float, tone: float) -> bool:
    """Does the source's own ink at `x` close this rectangle top to bottom?

    This is the referee's WALL test, and it is what separates a comb's outer
    RAIL from its compartment dividers.  A rail closes the box: its ink runs
    from the rule that shuts the rectangle at the top to the rule that shuts
    it at the bottom.  A divider tick hangs inside that box and stops on
    paper.  Both are the same tone, the same width and the same shape, so
    neither the tone filter nor the width filter above can tell them apart;
    only the ray can.

    Everything here is Poppler's.  The ray is walked with
    `visible_vertical_runs`, so a mark a later fill covers is already gone,
    and the rectangle comes from the subject the ledger names -- never from
    the producer's own answer about where the comb's edges are.

    Two joins, and they are the only tolerances:

      * a break narrower than the STROKE either side of it is not a break.
        Poppler splits one printed rule into a fill per crossing vertical and
        leaves one-quantum seams between them, and official sheets break a
        wall where the band's own rule crosses it (1800 leaves 0.24pt of
        paper between a 0.48 and a 0.24pt stroke) or stop it a hair short of
        the rule above (0.12pt under a 1.44pt rule).  The bound is the
        stroke's own thickness, `min(width, height)`, so it is always a
        fraction of a point and can never bridge a compartment's worth of
        paper.
      * a break the source PAINTED OVER is never joined, however narrow. An
        erasure across the junction is the sheet saying the stroke stops
        there, and it is exactly what separates two forms that draw the same
        shape: 2200-A paints out the junction between its YYYY column and the
        row above, 1800 leaves paper beside its knockout and the column
        carries through.

    The search window is the rectangle plus its own height either side -- the
    same neighbourhood `source_wall_thickness` measures a wall in -- because
    the rules that close a box are drawn outside it.
    """
    height = y1 - y0
    segments: list[tuple[float, float, bool, float]] = []
    cursor = y0 - height
    for top, bottom, owner in visible_vertical_runs(
            paints, x, y0 - height, y1 + height):
        if top > cursor + 1e-9:
            segments.append((cursor, top, False, 0.0))
        segments.append((
            top, bottom,
            abs(owner.tone - tone) <= 1e-8 and not owner.clipped,
            min(owner.width, owner.height),
        ))
        cursor = bottom
    if cursor < y1 + height - 1e-9:
        segments.append((cursor, y1 + height, False, 0.0))

    chains: list[tuple[float, float]] = []
    chain: tuple[float, float, float] | None = None
    paper_gap: tuple[float, float] | None = None
    for top, bottom, on_tone, weight in segments:
        if on_tone:
            if chain is not None and paper_gap is not None:
                gap, before = paper_gap
                if gap <= max(before, weight) + 1e-9:
                    chain = (chain[0], bottom, weight)
                    paper_gap = None
                    continue
                chains.append((chain[0], chain[1]))
                chain = None
            paper_gap = None
            chain = ((top, bottom, weight) if chain is None
                     else (chain[0], bottom, weight))
            continue
        if chain is None:
            continue
        if weight == 0.0:
            # Unpainted paper: a candidate break, decided when ink resumes.
            paper_gap = (bottom - top, chain[2])
            continue
        chains.append((chain[0], chain[1]))
        chain = None
        paper_gap = None
    if chain is not None:
        chains.append((chain[0], chain[1]))
    return any(
        top <= y0 + POSITION_TOL_PT and bottom >= y1 - POSITION_TOL_PT
        for top, bottom in chains
    )


def source_wall_thickness(
        paints: Sequence[Paint], box: dict[str, float], tone: float,
        edge: str, probes: Sequence[float],
        ) -> tuple[float | None, str | None]:
    """Measure, from Poppler alone, the wall the source paints at one cell edge.

    The measurement is taken on a vertical ray through the middle of EVERY
    compartment, because that is the paper the writing surface claims: a wall
    that is present over some compartments and absent or lighter over others
    does not establish one inset, and this returns no measurement rather than
    an average.  Rays, not a full-width span test, because Poppler splits a
    single printed rule into one fill per crossing vertical and leaves
    one-quantum seams between them (1701 p1c37's top rule is 44 fills with
    0.0039pt gaps); a span test reads those seams as paper.
    """
    y0, y1 = box["y0"], box["y1"]
    height = y1 - y0
    window_y0, window_y1 = y0 - height, y1 + height
    anchor = y0 if edge == "top" else y1
    centre = (y0 + y1) / 2

    def separation(run: tuple[float, float]) -> float:
        if run[0] <= anchor <= run[1]:
            return 0.0
        return min(abs(run[0] - anchor), abs(run[1] - anchor))

    thicknesses: set[float] = set()
    for x in probes:
        candidates: list[tuple[float, float]] = []
        for top, bottom, owner in visible_vertical_runs(
                paints, x, window_y0, window_y1):
            if owner.clipped or abs(owner.tone - tone) > 1e-8:
                continue
            if not (bottom > y0 - POSITION_TOL_PT
                    and top < y1 + POSITION_TOL_PT):
                continue
            midpoint = (top + bottom) / 2
            if edge == "top":
                if midpoint >= centre:
                    continue
            elif midpoint <= centre:
                continue
            candidates.append((top, bottom))
        if not candidates:
            return None, (
                f"the source paints no {edge} wall of the declared tone over "
                "every compartment")
        nearest = min(candidates, key=separation)
        thickness = layout_quantized(nearest[1] - nearest[0])
        if any(
            abs(separation(run) - separation(nearest)) <= 1e-9
            and layout_quantized(run[1] - run[0]) != thickness
            for run in candidates
        ):
            return None, (
                f"two source {edge} walls of different weight are equally "
                "near the cell edge")
        if (nearest[0] <= window_y0 + 1e-9
                or nearest[1] >= window_y1 - 1e-9):
            return None, (
                f"the source {edge} wall is not bounded inside the cell's own "
                "neighbourhood")
        thicknesses.add(thickness)
    if not thicknesses:
        return None, (
            f"the source paints no {edge} wall of the declared tone over "
            "every compartment")
    # The inset the writing surface stands under is the MAXIMUM weight over
    # the span: a border drawn 0.5pt over the caption stretch and 0.2pt over
    # the compartments claims 0.5 wherever both reach into the span, and the
    # span-end probe rays below are what let this census see a heavier
    # segment that only nicks the span's first sliver (1701-MS, 1800, 1706).
    return max(thicknesses), None


def writing_band_corroboration(
        box: dict[str, float], border: Any, comb: dict[str, Any],
        page: SvgPage,
        ) -> dict[str, Any]:
    """Re-derive the comb's VERTICAL writing surface from the source, compare.

    `slot_x` is corroborated horizontally against Poppler's own
    `source_divider_x`; without this the VERTICAL extent of every emitted slot
    would be a number the lattice asserts and nobody checks.  The relation the
    lattice publishes is `emit.field_box`'s: the writing surface is the cell's
    printed rectangle inset by its own horizontal wall weights.  So the referee
    measures those two weights itself, from the pinned PDF's vector output,
    insets the same rectangle, and demands the published band back to the
    precision the layout is written at.

    The horizontal writing edges (`writing_x0`/`writing_x1`) are deliberately
    NOT re-derived here, and the asymmetry is a decision rather than an
    omission.  This function exists because the vertical inset is a RELATION
    between two numbers the lattice asserts -- the cell box and its border
    weight -- so nothing outside the producer could confirm it.  The horizontal
    edges are not a relation: they are the rails' own painted ink, and
    `audit.py` already measures that ink independently, from the pinned PDF's
    content stream through its own parser rather than through Poppler, publishes
    it per comb as `source_frame_geometry.left_rail.ink_x1` /
    `right_rail.ink_x0`, and binds the layout to it at POSITION_TOL_PT in
    `layout_source_outer_position`.  A second exact re-derivation here would
    duplicate an existing independent check and would compare absolute
    COORDINATES between two rasterisers at 1e-9, where the vertical compares a
    THICKNESS and is robust to a sub-point offset between them.

    The declared border TONE selects which ink to measure; every number that
    reaches the verdict is Poppler's.  Selecting by tone is not a courtesy to
    the producer: many official "rules" are near-invisible grey decoration, and
    an ink-agnostic search would measure a grey band where the contract claims
    a black wall (2550M's rows sit inside a 0.7529 band) and silently confirm
    an inset derived from something else.
    """
    slot_x = [float(value) for value in comb["slot_x"]]
    probes = [
        (left + right) / 2 for left, right in zip(slot_x, slot_x[1:])
    ]
    # The COMPARTMENT MIDPOINTS are the relation, on both sides: the producer
    # counts a border segment only where it spans one of these same rays, so
    # the two measurers qualify identical ink by construction.  Span-end rays
    # were tried and REVERTED: at shared boundaries they crossed junction
    # corners the old probes never touched, refused 249 cells corpus-wide,
    # and moved the human-reviewed 2551Q control tuples -- the fail-closed
    # digest caught it.  A heavier stretch that spans no compartment midpoint
    # bounds no compartment's writing surface.
    walls: dict[str, float] = {}
    for edge in ("top", "bottom"):
        record = border.get(edge) if isinstance(border, dict) else None
        if record is None:
            # A null border is a CLAIM -- "no wall here" -- and the sheet is
            # asked to confirm it: no structural-tone run may stand within
            # the coincidence tolerance of this edge at any probe.  Absence
            # verified is an inset of zero (1707 item 9's 25-compartment
            # combs and 2551-M p1c103 are exactly this shape); ink found is a
            # refusal, exactly as a wrong thickness is.
            edge_y = box["y0"] if edge == "top" else box["y1"]
            intruder = None
            for x in probes:
                for top, bottom, owner in visible_vertical_runs(
                        page.paints, x, edge_y - 2 * POSITION_TOL_PT,
                        edge_y + 2 * POSITION_TOL_PT):
                    if owner.clipped or owner.tone > STRUCTURAL_TONE_MAX:
                        continue
                    if (top <= edge_y + POSITION_TOL_PT
                            and bottom >= edge_y - POSITION_TOL_PT):
                        intruder = (x, top, bottom, owner.tone)
                        break
                if intruder is not None:
                    break
            if intruder is not None:
                return {
                    "status": "uncorroborated",
                    "reason": (
                        f"the layout declares no {edge} border but the "
                        "source paints structural ink at the edge"),
                }
            walls[edge] = 0.0
            continue
        tone = record.get("gray")
        if (isinstance(tone, bool) or not isinstance(tone, (int, float))
                or not math.isfinite(float(tone))):
            return {
                "status": "uncorroborated",
                "reason": (
                    f"the layout declares no {edge} border tone for the "
                    "source measurement to select"),
            }
        thickness, reason = source_wall_thickness(
            page.paints, box, float(tone), edge, probes)
        if thickness is None:
            assert reason is not None
            return {"status": "uncorroborated", "reason": reason}
        walls[edge] = thickness
    source_y0 = layout_quantized(box["y0"] + walls["top"])
    source_y1 = layout_quantized(box["y1"] - walls["bottom"])
    evidence = {
        "source_top_wall_pt": walls["top"],
        "source_bottom_wall_pt": walls["bottom"],
        "source_writing_y0": source_y0,
        "source_writing_y1": source_y1,
        "layout_writing_y0": float(comb["writing_y0"]),
        "layout_writing_y1": float(comb["writing_y1"]),
    }
    if source_y1 - source_y0 <= 0:
        return {
            "status": "uncorroborated",
            "reason": "the source walls leave the cell no writing surface",
            **evidence,
        }
    # One layout quantum (0.01pt) of tolerance, because the two rasterisers
    # can disagree on a thickness at the last written digit: 0605 p1c36's
    # bottom wall is 0.76pt in the content stream and 0.75pt in Poppler's
    # vectors.  Half-ulp disagreement between two honestly-quantized numbers
    # is not a defect; anything more is.
    if (abs(source_y0 - evidence["layout_writing_y0"]) > 0.01 + 1e-9
            or abs(source_y1 - evidence["layout_writing_y1"]) > 0.01 + 1e-9):
        return {
            "status": "uncorroborated",
            "reason": (
                "the source walls inset this cell to "
                f"{source_y0:g}..{source_y1:g}, not the published writing "
                f"band {evidence['layout_writing_y0']:g}.."
                f"{evidence['layout_writing_y1']:g}"),
            **evidence,
        }
    return {
        "status": "corroborated",
        "reason": (
            "the source insets the cell by its own painted walls to the "
            "published writing band"),
        **evidence,
    }


def classify_band(
        cell: dict[str, Any],
        page: SvgPage,
        *,
        ledger_state: str | None = None,
        _evaluation_window: tuple[float, float] | None = None,
        ) -> dict[str, Any]:
    comb = cell["comb"]
    anchors = [float(value) for value in comb.get("divider_x") or ()]
    if not anchors:
        return {"status": "unevaluable",
                "reason": "no recognised divider anchors; one compartment is unproven"}
    pitch = float(comb.get("pitch_pt") or 0)
    if pitch <= 0:
        return {"status": "unevaluable", "reason": "no positive measured pitch"}
    try:
        divider_tone = float(comb["divider_gray"])
    except (KeyError, TypeError, ValueError):
        return {"status": "unevaluable",
                "reason": "comb has no numeric divider tone contract"}
    x0, x1 = float(cell["x0"]), float(cell["x1"])
    contract_y0, contract_y1 = float(comb["y0"]), float(comb["y1"])
    if contract_y1 <= contract_y0:
        return {"status": "unevaluable",
                "reason": "comb has no positive source band"}

    # The lattice contract can include the outer half of its closing
    # horizontals.  Normalize to the open compartment band: a vertical ending
    # at the near edge of a baseline is topologically coextensive with one
    # painted through that baseline.  This uses vector geometry and the
    # contract's own anchored run, not a form-specific height tolerance.
    anchor_left = min(anchors) if len(anchors) > 1 else x0
    anchor_right = max(anchors) if len(anchors) > 1 else x1
    contract_height = contract_y1 - contract_y0
    def finally_spans(paint: Paint, y: float,
                      left: float, right: float) -> bool:
        return any(
            abs(float(component["tone"]) - divider_tone) <= 1e-8
            and not bool(component["clipped"])
            and float(component["x0"]) <= left + POSITION_TOL_PT
            and float(component["x1"]) >= right - POSITION_TOL_PT
            for component in composited_segments(y, page.paints)
        )

    closing_rules = [
        paint for paint in page.paints
        if not paint.clipped
        and abs(paint.tone - divider_tone) <= 1e-8
        and paint.width > paint.height
        and paint.height <= min(contract_height / 2, pitch / 2)
        and paint.x0 <= anchor_left + POSITION_TOL_PT
        and paint.x1 >= anchor_right - POSITION_TOL_PT
        and finally_spans(paint, paint.y0 + paint.height / 2,
                          anchor_left, anchor_right)
    ]
    top_edges = [
        paint.y1 for paint in closing_rules
        if paint.y0 <= contract_y0 + POSITION_TOL_PT
        and paint.y1 > contract_y0
    ]
    bottom_edges = [
        paint.y0 for paint in closing_rules
        if paint.y0 < contract_y1
        and paint.y1 >= contract_y1 - POSITION_TOL_PT
    ]
    seed_y0 = max([contract_y0, *top_edges])
    seed_y1 = min([contract_y1, *bottom_edges])
    if seed_y1 - seed_y0 <= POSITION_TOL_PT:
        return {
            "status": "unevaluable",
            "reason": "closing rules leave no measurable open compartment band",
            "contract_y0": round(contract_y0, 6),
            "contract_y1": round(contract_y1, 6),
            "open_y0": round(seed_y0, 6),
            "open_y1": round(seed_y1, 6),
        }
    cell_y0, cell_y1 = float(cell["y0"]), float(cell["y1"])
    band_attached_above = (
        seed_y0 < cell_y0
        and seed_y1 <= cell_y1
        and seed_y1 >= cell_y0 - POSITION_TOL_PT
    )
    band_attached_below = (
        seed_y0 >= cell_y0
        and seed_y1 > cell_y1
        and seed_y0 <= cell_y1 + POSITION_TOL_PT
    )
    attached_external_band = band_attached_above or band_attached_below
    evaluation_y0, evaluation_y1 = (
        _evaluation_window
        if _evaluation_window is not None
        else (cell_y0, cell_y1)
    )
    # Frame ownership and unsupported-gap exclusion must be certified in the
    # same vertical window that supplies the divider topology.  On the first
    # pass this is exactly the original cell rectangle.  On an attached-band
    # retry it prevents an unrelated frame inside the cell from proving an
    # empty multi-pitch gap in the external source band.
    proof_y0, proof_y1 = evaluation_y0, evaluation_y1

    wall_verdicts: dict[float, bool] = {}

    def is_rail(value: float) -> bool:
        """Whether the source closes the subject's box at this boundary."""
        key = round(float(value), 6)
        verdict = wall_verdicts.get(key)
        if verdict is None:
            verdict = closes_subject_box(
                page.paints, float(value), cell_y0, cell_y1, divider_tone)
            wall_verdicts[key] = verdict
        return verdict

    def outer_region_prose(lo: float, hi: float) -> tuple[int, int]:
        """Glyphs and divider-tone structure inside one claimed outer region.

        Both counts come from this referee's own parse of Poppler's vectors,
        never from the lattice: glyph bounds via `measured_glyph_boxes`
        (containment-conservative -- an error UNDERcounts, which keeps the
        rectangle edge and today's behaviour), structure from every
        divider-tone vertical whose centre lies strictly inside the region.
        The structure pool is deliberately wider than `candidates`: a paint
        too thick to be a divider candidate is still structure this clause
        must not silently pave over.

        The vertical window is the CELL's, not the comb contract's, because
        the claim under refutation is the compartment the cell edge would
        own, and that claim spans the cell.  The contract band can be just
        the divider-tick row (2200-A item 27's is 4.92pt tall) while the
        caption prose stands in the writing area above it; asking only the
        tick row finds nothing and the phantom compartment survives.  The
        9,068-side census that grounds the `> 1` boundary was measured on
        this same cell window.
        """
        glyphs = 0
        for region in measured_glyph_boxes(
                page, [lo, cell_y0, hi, cell_y1]):
            centre = (region.x0 + region.x1) / 2.0
            if lo + POSITION_TOL_PT < centre < hi - POSITION_TOL_PT:
                glyphs += 1
        # An endpoint grazing the window is coincidence, not presence: the
        # row above 2200-A item 27 ends its rule 0.24pt inside this cell's
        # top, and counting that quarter-point tail as "structure standing in
        # the region" would turn a caption refutation into a conflict.  The
        # bound is the file's one coincidence tolerance, not a new constant.
        structure = sum(
            1 for item in page.paints
            if not item.clipped
            and abs(item.tone - divider_tone) <= 1e-8
            and item.height > item.width
            and min(item.y1, cell_y1) - max(item.y0, cell_y0)
            > POSITION_TOL_PT
            and lo + POSITION_TOL_PT
            < (item.x0 + item.x1) / 2.0
            < hi - POSITION_TOL_PT
        )
        return glyphs, structure

    def rail_bounded(values: Sequence[float]
                     ) -> tuple[float, float, list[float]]:
        """Split one slab's measured boundaries into rails and dividers.

        A comb ends where the source closes its box, not where the ledger's
        rectangle ends: one rectangle can rule a caption, a TIN dash box or a
        neighbouring field beside the comb, and counting `len(dividers) + 1`
        across the whole rectangle then invents a compartment the sheet does
        not print.  A wall OUTSIDE the tick run is that comb's rail; a wall
        between two ticks stays a divider, because a box closed on both sides
        of a compartment is still one of this comb's compartments (1801's TIN
        rules its dash boxes with exactly such walls).  With no wall outside
        the run -- and with no ticks at all, a row of full-height boxes -- the
        rectangle's own edges stand HERE; whether an edge-railed outer region
        is really a compartment is then asked of the CHOSEN topology by
        `refuted_outer_rails`, never of a partial slab, because a sub-slab's
        shorter tick run manufactures an outer region the cell does not
        claim (eleven 1701-family TIN rows regressed to "ambiguous topology"
        when this question was asked per slab).
        """
        ticks = [value for value in values if not is_rail(value)]
        left, right = x0, x1
        if ticks:
            walls_left = [value for value in values
                          if value < ticks[0] and is_rail(value)]
            walls_right = [value for value in values
                           if value > ticks[-1] and is_rail(value)]
            if walls_left:
                left = max(walls_left)
            if walls_right:
                right = min(walls_right)
        return left, right, [
            value for value in values if left < value < right
        ]

    def refuted_outer_rails(band: dict[str, Any]) -> dict[str, Any] | None:
        """Ask the sheet whether an edge-railed outer region is a compartment.

        Applied to the CHOSEN band only, after topology selection -- the
        claim under refutation is the compartment count this cell will
        publish, and the census grounding the `> 1` boundary was measured on
        chosen topologies.  A partial slab's shorter tick run manufactures an
        outer region the cell never claims, and asking there poisoned eleven
        1701-family TIN rows.

        Where a rail sits on the rectangle's edge with a tick run inside it,
        the region between them is a claimed compartment with no closing
        evidence.  A compartment is one character wide, so at most one
        pre-printed glyph fits it; MORE than one is running text -- the same
        physical statement the caption-block corroboration makes, re-derived
        from Poppler glyph bounds, a stack lattice.py never reads.  Census
        over all 9,068 edge-railed sides of the 53-form corpus: 8,948 hold no
        glyph, 90 hold exactly one, 30 hold more, and nothing lies between 1
        and 14 glyphs, so `> 1` separates two real populations rather than
        tuning a constant.  A refuted region moves the rail to the outermost
        tick and the compartment count follows; prose standing together with
        divider-tone structure is a conflict this clause must not resolve
        either way, and the whole cell fails closed (None).

        Rows of full-height boxes never reach the question -- their
        boundaries are walls, so `ticks` is empty and the edges stand: a
        walled table compartment legitimately carries text (1604CF `p2c73`,
        reviewed and pinned at 2 compartments, holds 29 glyphs in its right
        box).  Retained subjects never reach it either: the caption-block
        corroboration COUNTS their prose per compartment as the evidence of
        correct suppression, so trimming it away would destroy the census
        that corroboration runs on.
        """
        values = [float(value) for value in band["source_divider_x"]]
        rails = [float(value) for value in band["source_rail_x"]]
        ticks = [value for value in values if not is_rail(value)]
        derivation: dict[str, Any] = {}
        for side, index, edge in (("left", 0, x0), ("right", 1, x1)):
            rail = rails[index]
            if abs(rail - edge) > 1e-6:
                derivation[side] = {
                    "basis": "wall-outside-run",
                    "wall_x": round(rail, 6),
                }
                continue
            derivation[side] = {"basis": "owner-edge"}
            if not ticks or ledger_state not in (
                    None, "active_resolved", "active_unresolved"):
                continue
            lo, hi = ((edge, ticks[0]) if side == "left"
                      else (ticks[-1], edge))
            if hi - lo <= POSITION_TOL_PT:
                continue
            glyphs, structure = outer_region_prose(lo, hi)
            if glyphs > 1 and structure:
                return None
            if glyphs > 1:
                rails[index] = ticks[0] if side == "left" else ticks[-1]
                derivation[side] = {
                    "basis": "prose-refuted-outer-region",
                    "from_x": round(edge, 6),
                    "span_pt": round(hi - lo, 6),
                    "glyphs": glyphs,
                }
        enclosed = [value for value in values
                    if rails[0] < value < rails[1]]
        if len(enclosed) + 1 < 2:
            # A refutation that leaves no comb band is a caption block
            # wrongly active -- the retained ledger's question, not a rail's.
            return None
        return {
            **band,
            "source_rail_x": [round(rails[0], 6), round(rails[1], 6)],
            "compartments": len(enclosed) + 1,
            "rail_derivation": derivation,
        }

    max_width = pitch / 2
    candidates = [
        paint for paint in page.paints
        if abs(paint.tone - divider_tone) <= 1e-8
        and paint.width <= max_width
        and paint.height > paint.width
        and paint.x1 > x0 and paint.x0 < x1
        and paint.y1 > evaluation_y0 and paint.y0 < evaluation_y1
    ]
    # Glyphs are never divider candidates, so they matter only when they can
    # occlude an eligible interior vertical.  A glyph whose conservative bound
    # merely touches the cell's own side cannot change compartment topology.
    # Apply the same paper-width test used below for outward source candidates
    # before allowing a glyph bound to make the subject unevaluable.
    interior_candidates = [
        paint for paint in candidates
        if paint.x0 > x0 + POSITION_TOL_PT
        and paint.x1 < x1 - POSITION_TOL_PT
        and paint.x0 - x0 > paint.width
        and x1 - paint.x1 > paint.width
    ]

    # A non-uniform empty gap is safe only when the source independently proves
    # that the whole subject is one physical rectangle.  The certificate is
    # deliberately stronger than "there are lines near four sides": one
    # Poppler element must finally own all four complete target-tone edges.
    # This distinguishes a genuinely irregular enclosed comb from two comb runs
    # that lattice.py accidentally joined across a label or gutter.
    def final_owner(x: float, y: float) -> Paint | None:
        return final_paint_owner(page.paints, x, y)

    def final_target_spans_horizontal(y: float) -> bool:
        endpoints = {x0, x1}
        for paint in page.paints:
            if paint.y0 <= y <= paint.y1 and paint.x1 > x0 and paint.x0 < x1:
                endpoints.update((max(x0, paint.x0), min(x1, paint.x1)))
        ordered = sorted(endpoints)
        for left, right in zip(ordered, ordered[1:]):
            if right - left <= 1e-9:
                continue
            owner = final_owner((left + right) / 2, y)
            if (owner is None or owner.clipped
                    or abs(owner.tone - divider_tone) > 1e-8):
                if ((left <= x0 + 1e-9 or right >= x1 - 1e-9)
                        and right - left <= POSITION_TOL_PT):
                    continue
                return False
        return True

    def final_target_spans_vertical(x: float) -> bool:
        endpoints = {proof_y0, proof_y1}
        for paint in page.paints:
            if (paint.x0 <= x <= paint.x1
                    and paint.y1 > proof_y0 and paint.y0 < proof_y1):
                endpoints.update((
                    max(proof_y0, paint.y0), min(proof_y1, paint.y1)))
        ordered = sorted(endpoints)
        for top, bottom in zip(ordered, ordered[1:]):
            if bottom - top <= 1e-9:
                continue
            owner = final_owner(x, (top + bottom) / 2)
            if (owner is None or owner.clipped
                    or abs(owner.tone - divider_tone) > 1e-8):
                if ((top <= proof_y0 + 1e-9
                     or bottom >= proof_y1 - 1e-9)
                        and bottom - top <= POSITION_TOL_PT):
                    continue
                return False
        return True

    subject_frame_elements_cache: list[str] | None = None

    def single_source_frame_elements() -> list[str]:
        nonlocal subject_frame_elements_cache
        if subject_frame_elements_cache is not None:
            return subject_frame_elements_cache
        paints_by_element: dict[str, list[Paint]] = {}
        for paint in page.paints:
            if (not paint.clipped
                    and abs(paint.tone - divider_tone) <= 1e-8):
                paints_by_element.setdefault(paint.element, []).append(paint)
        subject_frame_elements: list[str] = []
        for element, element_paints in sorted(paints_by_element.items()):
            top_lines = [
                paint for paint in element_paints
                if paint.width > paint.height
                and paint.x0 <= x0 + POSITION_TOL_PT
                and paint.x1 >= x1 - POSITION_TOL_PT
                and paint.y0 <= proof_y0 + POSITION_TOL_PT
                and paint.y1 >= proof_y0 - POSITION_TOL_PT
            ]
            bottom_lines = [
                paint for paint in element_paints
                if paint.width > paint.height
                and paint.x0 <= x0 + POSITION_TOL_PT
                and paint.x1 >= x1 - POSITION_TOL_PT
                and paint.y0 <= proof_y1 + POSITION_TOL_PT
                and paint.y1 >= proof_y1 - POSITION_TOL_PT
            ]
            left_lines = [
                paint for paint in element_paints
                if paint.height > paint.width
                and paint.y0 <= proof_y0 + POSITION_TOL_PT
                and paint.y1 >= proof_y1 - POSITION_TOL_PT
                and paint.x0 <= x0 + POSITION_TOL_PT
                and paint.x1 >= x0 - POSITION_TOL_PT
            ]
            right_lines = [
                paint for paint in element_paints
                if paint.height > paint.width
                and paint.y0 <= proof_y0 + POSITION_TOL_PT
                and paint.y1 >= proof_y1 - POSITION_TOL_PT
                and paint.x0 <= x1 + POSITION_TOL_PT
                and paint.x1 >= x1 - POSITION_TOL_PT
            ]
            if (
                any(final_target_spans_horizontal(
                    (paint.y0 + paint.y1) / 2)
                    for paint in top_lines)
                and any(final_target_spans_horizontal(
                    (paint.y0 + paint.y1) / 2)
                    for paint in bottom_lines)
                and any(final_target_spans_vertical(
                    (paint.x0 + paint.x1) / 2)
                    for paint in left_lines)
                and any(final_target_spans_vertical(
                    (paint.x0 + paint.x1) / 2)
                    for paint in right_lines)
            ):
                subject_frame_elements.append(element)
        subject_frame_elements_cache = subject_frame_elements
        return subject_frame_elements_cache
    ambiguous_target_paints = [
        paint for paint in page.paints
        if paint.clipped
        and abs(paint.tone - divider_tone) <= 1e-8
        and paint.height > paint.width
        and paint.x1 > x0 and paint.x0 < x1
        and paint.y1 > seed_y0 and paint.y0 < seed_y1
    ]
    if ambiguous_target_paints:
        return {
            "status": "unevaluable",
            "reason": "ambiguous target-tone SVG paint intersects the comb band",
            "paints": [dataclasses.asdict(paint)
                       for paint in ambiguous_target_paints],
        }

    def unsupported_affects_comb(region: UnsupportedRegion) -> bool:
        if not (
            region.x1 > x0 and region.x0 < x1
            and region.y1 > seed_y0 and region.y0 < seed_y1
            and region.y1 - region.y0 > 1e-6
            and min(region.y1, seed_y1) - max(region.y0, seed_y0)
            > POSITION_TOL_PT
        ):
            return False
        # Poppler normally emits text as glyph ``use`` nodes, but a few
        # official forms carry outlined characters as broad curved paths and
        # small arrowheads as simple closed fills.  A bound that cannot itself
        # be a tall, narrow compartment boundary can affect topology only by
        # covering or joining an eligible source divider.  Divider-like bounds,
        # clipped simple fills, and structurally complex fills stay unsupported.
        vertical_overlap = (
            min(region.y1, seed_y1) - max(region.y0, seed_y0)
        )
        region_can_be_divider = (
            region.reason in (
                "curved SVG path",
                "non-rectangular closed SVG fill",
            )
            and region.x1 - region.x0 <= max_width
            and region.y1 - region.y0 > region.x1 - region.x0
            and vertical_overlap > (seed_y1 - seed_y0) / 2
        )
        occlusion_only = (
            "glyph use" in region.reason
            or (
                region.reason == "curved SVG path"
                and not region_can_be_divider
            )
            or (
                region.reason == "non-rectangular closed SVG fill"
                and not region.clipped
                and not region_can_be_divider
            )
        )
        if not occlusion_only:
            return True
        # Glyphs are explicitly excluded as divider candidates.  Their only
        # topology effect is possible occlusion of an earlier raw divider.
        # Same-tone glyph paint preserves that ink; a differently toned glyph
        # matters only where its conservative bound crosses an earlier
        # candidate rectangle.
        if (region.tone is not None
                and abs(region.tone - divider_tone) <= 1e-8):
            return any(
                region.x1 > paint.x0 and region.x0 < paint.x1
                and region.y1 > max(seed_y0, paint.y0)
                and region.y0 < min(seed_y1, paint.y1)
                for paint in interior_candidates
            )
        return any(
            (region.order < 0 or region.order > paint.order)
            and region.x1 > paint.x0 and region.x0 < paint.x1
            and region.y1 > max(seed_y0, paint.y0)
            and region.y0 < min(seed_y1, paint.y1)
            for paint in interior_candidates
        )

    intersecting_unsupported = [
        region for region in page.unsupported
        if unsupported_affects_comb(region)
    ]
    if intersecting_unsupported:
        return {
            "status": "unevaluable",
            "reason": "unsupported SVG geometry intersects the comb band",
            "unsupported": [dataclasses.asdict(region)
                            for region in intersecting_unsupported],
        }

    endpoints = {seed_y0, seed_y1}
    for paint in page.paints:
        if not (paint.x1 > x0 and paint.x0 < x1):
            continue
        a = max(evaluation_y0, paint.y0)
        b = min(evaluation_y1, paint.y1)
        if b > a and b > seed_y0 and a < seed_y1:
            endpoints.update((a, b))
    ordered_y = sorted(endpoints)
    bands: list[dict[str, Any]] = []
    ignored_slabs: list[dict[str, Any]] = []
    for a, b in zip(ordered_y, ordered_y[1:]):
        # A thinner y-slab is only coordinate noise at a shared endpoint: it
        # cannot establish a geometrically distinct band under the repository's
        # fixed 0.25pt position tolerance.
        if b - a <= POSITION_TOL_PT or b <= seed_y0 or a >= seed_y1:
            if b > seed_y0 and a < seed_y1:
                ignored_slabs.append({
                    "y0": round(max(a, seed_y0), 6),
                    "y1": round(min(b, seed_y1), 6),
                    "reason": "slab is no wider than the fixed position bound",
                })
            continue
        mid = (a + b) / 2
        groups = merge_centres(candidates, mid, page.paints, max_width)
        if not groups:
            ignored_slabs.append({
                "y0": round(a, 6), "y1": round(b, 6),
                "reason": "no candidate divider ink",
            })
            continue

        # A thick page/frame edge is sometimes a stack of two target-tone
        # bars.  The inner bar can coincide with a stale lattice anchor, but
        # paper narrower than the combined ink weights is not a writable
        # compartment.  Frame evidence comes from all final components, not
        # the width-filtered divider candidates: a broad outer bar is precisely
        # the case that needs to disqualify its narrow neighbour.  Apply this
        # before both complete and partial anchor matching.
        frame_groups = [
            component for component in composited_segments(mid, page.paints)
            if abs(float(component["tone"]) - divider_tone) <= 1e-8
            and ((float(component["x0"]) <= x0 + POSITION_TOL_PT
                  and float(component["x1"]) >= x0 - POSITION_TOL_PT)
                 or (float(component["x0"]) <= x1 + POSITION_TOL_PT
                     and float(component["x1"])
                     >= x1 - POSITION_TOL_PT))
        ]

        def distinct_from_frames(group: dict[str, Any]) -> bool:
            for frame in frame_groups:
                paper = (max(float(group["x0"]), float(frame["x0"]))
                         - min(float(group["x1"]), float(frame["x1"])))
                weights = ((float(group["x1"]) - float(group["x0"]))
                           + (float(frame["x1"]) - float(frame["x0"])))
                if paper <= weights:
                    return False
            return True

        matchable_groups = [
            group for group in groups
            if distinct_from_frames(group)
        ]

        # Match recognised anchors to independently painted source boundaries.
        # A referee must not silently move an anchor to the nearest plausible
        # line: source and lattice positions agree inside the repository's
        # fixed 0.25pt bound or they do not agree.
        available = list(range(len(matchable_groups)))
        anchor_matches: list[dict[str, float]] = []
        for anchor in anchors:
            choices = sorted(
                ((abs(matchable_groups[index]["x"] - anchor), index)
                 for index in available
                 if abs(matchable_groups[index]["x"] - anchor)
                 <= POSITION_TOL_PT),
                key=lambda item: (
                    item[0], matchable_groups[item[1]]["x"]),
            )
            if not choices:
                anchor_matches = []
                break
            distance, index = choices[0]
            available.remove(index)
            anchor_matches.append({
                "layout_x": round(anchor, 6),
                "source_x": matchable_groups[index]["x"],
                "delta_pt": round(
                    matchable_groups[index]["x"] - anchor, 6),
                "group_index": index,
            })
        if len(anchor_matches) != len(anchors):
            interior_groups = [
                group for group in groups
                if not (
                    (group["x0"] <= x0 + POSITION_TOL_PT
                     and group["x1"] >= x0 - POSITION_TOL_PT)
                    or (group["x0"] <= x1 + POSITION_TOL_PT
                        and group["x1"] >= x1 - POSITION_TOL_PT)
                )
                and distinct_from_frames(group)
            ]
            record = {
                "y0": round(a, 6), "y1": round(b, 6),
                "source_divider_x": [
                    round(float(group["x"]), 6) for group in groups
                ],
            }
            available_anchors = list(anchors)
            partial_matches: list[dict[str, float]] = []
            for group in interior_groups:
                choices = sorted(
                    (abs(float(group["x"]) - anchor), index)
                    for index, anchor in enumerate(available_anchors)
                    if near(float(group["x"]), anchor)
                )
                if len(choices) != 1:
                    partial_matches = []
                    break
                _distance, index = choices[0]
                anchor = available_anchors.pop(index)
                partial_matches.append({
                    "layout_x": round(anchor, 6),
                    "source_x": round(float(group["x"]), 6),
                    "delta_pt": round(float(group["x"]) - anchor, 6),
                })
            if (interior_groups
                    and len(partial_matches) == len(interior_groups)
                    and any(group["clipped"] for group in interior_groups)):
                bands.append({
                    "status": "unevaluable",
                    "reason": (
                        "a partial source topology has unresolved clipping"
                    ),
                    **record,
                })
            elif (interior_groups
                  and len(partial_matches) == len(interior_groups)):
                partial_x = sorted(
                    round(float(group["x"]), 6)
                    for group in interior_groups)
                missing_anchor_x = sorted(
                    round(float(anchor), 6)
                    for anchor in available_anchors)
                partial_left, partial_right, partial_enclosed = (
                    rail_bounded(partial_x))
                bands.append({
                    "status": "measured",
                    "y0": round(a, 6), "y1": round(b, 6),
                    "source_divider_x": partial_x,
                    "source_rail_x": [
                        round(partial_left, 6), round(partial_right, 6)],
                    "extra_divider_x": [],
                    "compartments": len(partial_enclosed) + 1,
                    "anchor_matches": partial_matches,
                    "missing_anchor_x": missing_anchor_x,
                    "anchors_complete": False,
                    "positions_match": False,
                    "components": interior_groups,
                })
            elif interior_groups:
                bands.append({
                    "status": "unevaluable",
                    "reason": (
                        "unrecognised candidate ink exists while an anchor is missing"
                    ),
                    **record,
                })
            else:
                ignored_slabs.append({
                    "reason": (
                        "only cell-edge frames remain when an anchor is absent"
                    ),
                    **record,
                })
            continue
        matched_groups = [matchable_groups[int(match["group_index"])]
                          for match in anchor_matches]
        if any(group["clipped"] for group in matched_groups):
            bands.append({
                "status": "unevaluable",
                "reason": "a recognised divider is under an unresolved SVG clip",
                "y0": a, "y1": b,
            })
            continue

        eligible_groups = [
            group for group in groups
            if distinct_from_frames(group)
        ]

        extras: list[dict[str, Any]] = []
        partial: list[dict[str, Any]] = []
        subject_gap_proofs: list[dict[str, Any]] = []
        unproven_subject_gaps: list[dict[str, Any]] = []
        source_anchors = [float(match["source_x"]) for match in anchor_matches]
        for gap_index, (left, right) in enumerate(
                zip(source_anchors, source_anchors[1:])):
            gap = right - left
            multiple = int(round(gap / pitch))
            integral_residual = abs(gap - multiple * pitch)
            between = sorted(
                (group for group in eligible_groups
                 if left + POSITION_TOL_PT < float(group["x"])
                 < right - POSITION_TOL_PT),
                key=lambda group: float(group["x"]),
            )
            if between:
                source_steps = [
                    b - a for a, b in zip(
                        [left, *(float(group["x"]) for group in between)],
                        [*(float(group["x"]) for group in between), right],
                    )
                ]
                # The source itself can prove a regular subdivision even when
                # lattice.py chose the wrong modal pitch.  This is what happens
                # when a heavy group separator is 0.24pt off the nominal tick
                # position: both neighbouring source compartments still agree
                # with each other inside the fixed position tolerance.
                if (len(between) == max(0, multiple - 1)
                        and integral_residual <= POSITION_TOL_PT
                        and max(source_steps) - min(source_steps)
                        <= POSITION_TOL_PT
                        and all(abs(step - pitch) <= POSITION_TOL_PT
                                for step in source_steps)):
                    extras.extend(between)
                    continue
            if multiple <= 1:
                if between:
                    partial.append({
                        "left": round(left, 6), "right": round(right, 6),
                        "reason": (
                            "unexplained target-tone ink exists inside "
                            "a one-pitch anchor gap"
                        ),
                        "pitch_pt": round(pitch, 6),
                        "found_x": [item["x"] for item in between],
                    })
                continue
            # An empty gap wider than one measured pitch needs the stronger,
            # independently verified single-frame certificate above and must
            # contain no unsupported fixed ink.  Even an integral multi-pitch
            # void could be two separate comb runs joined by a bad lattice
            # subject.
            if not between:
                # Occlusion is asked of THIS SLAB, not of the whole cell.  The
                # claim under proof is "no divider crosses this slab in this
                # gap", and a divider that crosses the slab is visible in the
                # slab unless something covers it THERE.  Ink elsewhere in the
                # cell cannot hide it here, and citing that ink refused
                # 1604F p1c25 on glyphs 2.16pt above the slab they were
                # charged against.  The protection is unweakened where it
                # bears: ink actually overlapping the slab still refuses, and
                # a divider hidden under such ink still leaves that slab's
                # topology disagreeing with its neighbours', which the
                # majority rule catches.  The single-frame certificate stays
                # cell-scoped -- a frame is a property of the subject.
                gap_unsupported = [
                    region for region in page.unsupported
                    if region.x1 > left and region.x0 < right
                    and region.y1 > a and region.y0 < b
                    and min(region.x1, right) - max(region.x0, left)
                    > POSITION_TOL_PT
                    and min(region.y1, b) - max(region.y0, a)
                    > POSITION_TOL_PT
                ]
                subject_frame_elements = single_source_frame_elements()
                if subject_frame_elements and not gap_unsupported:
                    subject_gap_proofs.append({
                        "left": round(left, 6),
                        "right": round(right, 6),
                        "gap_pt": round(gap, 6),
                        "pitch_pt": round(pitch, 6),
                        "integral_residual_pt": round(
                            integral_residual, 6),
                        "single_frame_elements": subject_frame_elements,
                        "unsupported_regions": [],
                    })
                    continue
                unproven_subject_gaps.append({
                    "left": round(left, 6), "right": round(right, 6),
                    "reason": (
                        "multi-pitch empty gap lacks a clean single-frame proof"
                    ),
                    "pitch_pt": round(pitch, 6),
                    "gap_pt": round(gap, 6),
                    "integral_residual_pt": round(integral_residual, 6),
                    "single_frame_elements": subject_frame_elements,
                    "unsupported_regions": [
                        dataclasses.asdict(region)
                        for region in gap_unsupported
                    ],
                    "found_x": [],
                })
                continue
            if integral_residual > POSITION_TOL_PT:
                partial.append({
                    "left": round(left, 6), "right": round(right, 6),
                    "reason": "anchor gap is not an integral pitch multiple",
                    "pitch_pt": round(pitch, 6),
                    "residual_pt": round(integral_residual, 6),
                    "found_x": [item["x"] for item in between],
                })
                continue
            expected = [
                (left + index * pitch,
                 right - (multiple - index) * pitch)
                for index in range(1, multiple)
            ]
            found: list[dict[str, Any]] = []
            for from_left, from_right in expected:
                hit = next((
                    group for group in eligible_groups
                    if group not in found
                    and (near(group["x"], from_left)
                         or near(group["x"], from_right))
                ), None)
                if hit is not None:
                    found.append(hit)
            if found and len(found) != len(expected):
                partial.append({
                    "left": round(left, 6), "right": round(right, 6),
                    "expected_x": [
                        [round(from_left, 6), round(from_right, 6)]
                        for from_left, from_right in expected
                    ],
                    "found_x": [item["x"] for item in found],
                })
            elif found:
                extras.extend(found)
        if partial:
            bands.append({
                "status": "unevaluable",
                "reason": "only part of an integral-pitch gap is painted",
                "y0": a, "y1": b, "partial": partial,
            })
            continue
        unique_extras = {round(item["x"], 6): item for item in extras}
        if any(item["clipped"] for item in unique_extras.values()):
            bands.append({
                "status": "unevaluable",
                "reason": "a candidate divider is under an unresolved SVG clip",
                "y0": a, "y1": b,
            })
            continue
        source_x = sorted({round(anchor, 6) for anchor in source_anchors}
                          | set(unique_extras))

        # A source run can extend beyond the anchors when lattice.py missed its
        # first or last divider.  Continue only through the nearest same-band
        # interior boundary.  The run's measured gaps (or the fixed lattice
        # pitch when it agrees) must predict that boundary.  One special case
        # is still topological rather than heuristic: a lone first/last
        # boundary can divide the remaining interval to the cell edge into two
        # equal compartments.  That edge-bisection proof is allowed once per
        # side and cannot bootstrap a walk through unrelated ink.
        #
        # Source ink touching the cell edge is its frame, not another divider.
        # Failing to exclude it is catastrophic: every ordinary N-slot comb
        # becomes N+2 merely because its box has two sides.
        interior_groups = [
            group for group in eligible_groups
            if group["x0"] > x0 + POSITION_TOL_PT
            and group["x1"] < x1 - POSITION_TOL_PT
            and float(group["x0"]) - x0
            > float(group["x1"]) - float(group["x0"])
            and x1 - float(group["x1"])
            > float(group["x1"]) - float(group["x0"])
        ]

        def extend(direction: int) -> tuple[bool, str | None]:
            nonlocal source_x
            extensions = 0
            while source_x:
                edge = source_x[0] if direction < 0 else source_x[-1]
                possible = [
                    group for group in interior_groups
                    if not any(near(group["x"], value) for value in source_x)
                    and ((group["x"] < edge - POSITION_TOL_PT)
                         if direction < 0 else
                         (group["x"] > edge + POSITION_TOL_PT))
                ]
                if not possible:
                    return True, None
                candidate = (max(possible, key=lambda item: item["x"])
                             if direction < 0
                             else min(possible, key=lambda item: item["x"]))
                gap = abs(float(candidate["x"]) - edge)
                adjacent = [
                    right - left for left, right in zip(source_x, source_x[1:])
                    if POSITION_TOL_PT < right - left <= 1.5 * pitch
                ]
                pitch_match = any(
                    abs(gap - model) <= POSITION_TOL_PT
                    for model in (pitch, *adjacent)
                )
                paper_edge = x0 if direction < 0 else x1
                edge_bisection = (
                    extensions == 0
                    and abs(abs(float(candidate["x"]) - paper_edge) - gap)
                    <= POSITION_TOL_PT
                )
                if not pitch_match and not edge_bisection:
                    return False, (
                        "off-pitch source ink blocks outward continuation"
                    )
                if candidate["clipped"]:
                    return False, "an outward source divider has unresolved clipping"
                value = round(float(candidate["x"]), 6)
                source_x.append(value)
                source_x.sort()
                unique_extras[value] = candidate
                extensions += 1
            return True, None

        left_ok, left_reason = extend(-1)
        right_ok, right_reason = extend(1)
        if not left_ok or not right_ok:
            bands.append({
                "status": "unevaluable",
                "reason": left_reason or right_reason,
                "y0": a, "y1": b,
            })
            continue
        invalid_source_gaps = []
        extra_values = set(unique_extras)
        source_models = [pitch, *[
            right - left for left, right in zip(source_anchors,
                                                source_anchors[1:])
            if right - left > POSITION_TOL_PT
        ]]
        for left, right in zip(source_x, source_x[1:]):
            if left not in extra_values and right not in extra_values:
                continue
            gap = right - left
            model_results = []
            for model in source_models:
                multiple = max(1, int(round(gap / model)))
                model_results.append((
                    abs(gap - multiple * model), model, multiple))
            residual, model, multiple = min(model_results)
            if residual > POSITION_TOL_PT:
                invalid_source_gaps.append({
                    "left": round(left, 6),
                    "right": round(right, 6),
                    "gap_pt": round(gap, 6),
                    "nearest_model_pt": round(model, 6),
                    "nearest_pitch_multiple": multiple,
                    "residual_pt": round(residual, 6),
                })
        if invalid_source_gaps:
            bands.append({
                "status": "unevaluable",
                "reason": "final source gaps are not integral pitch multiples",
                "y0": round(a, 6), "y1": round(b, 6),
                "pitch_pt": round(pitch, 6),
                "invalid_gaps": invalid_source_gaps,
            })
            continue
        rail_left, rail_right, enclosed_x = rail_bounded(source_x)
        bands.append({
            "status": "measured",
            "y0": round(a, 6), "y1": round(b, 6),
            "source_divider_x": source_x,
            "source_rail_x": [round(rail_left, 6), round(rail_right, 6)],
            "extra_divider_x": sorted(unique_extras),
            "compartments": len(enclosed_x) + 1,
            "anchor_matches": [
                {key: value for key, value in match.items()
                 if key != "group_index"}
                for match in anchor_matches
            ],
            "positions_match": all(
                abs(float(match["delta_pt"])) <= POSITION_TOL_PT
                for match in anchor_matches
            ),
            "anchors_complete": True,
            "subject_gap_proofs": subject_gap_proofs,
            "unproven_subject_gaps": unproven_subject_gaps,
            "components": [group for group in groups
                           if any(near(group["x"], x) for x in source_x)],
        })

    measured = [band for band in bands if band["status"] == "measured"]
    ambiguous = [band for band in bands if band["status"] != "measured"]
    seed_span = seed_y1 - seed_y0
    measured_span = sum(
        float(band["y1"]) - float(band["y0"]) for band in measured)

    def topology(band: dict[str, Any]) -> tuple[float, ...]:
        return tuple(round(float(value), 6)
                     for value in band["source_divider_x"])

    measured_topologies = {topology(band) for band in measured}
    topology_coverage = {
        candidate: sum(
            float(band["y1"]) - float(band["y0"])
            for band in measured if topology(band) == candidate
        )
        for candidate in measured_topologies
    }

    def topology_key(candidate: tuple[float, ...]) -> str:
        return ",".join(str(value) for value in candidate)

    coverage_evidence = {
        "contract_y0": round(contract_y0, 6),
        "contract_y1": round(contract_y1, 6),
        "open_y0": round(seed_y0, 6),
        "open_y1": round(seed_y1, 6),
        "contract_span_pt": round(contract_y1 - contract_y0, 6),
        "seed_span_pt": round(seed_span, 6),
        "measured_span_pt": round(measured_span, 6),
        "unmeasured_span_pt": round(max(0.0, seed_span - measured_span), 6),
        "topology_coverage_pt": {
            topology_key(candidate): round(topology_coverage[candidate], 6)
            for candidate in sorted(measured_topologies)
        },
        "ignored_slabs": ignored_slabs,
    }
    if ambiguous:
        return {
            "status": "unevaluable",
            "reason": "one or more source slabs have ambiguous topology",
            **coverage_evidence,
            "bands": bands,
        }
    if not measured:
        reason = (bands[0]["reason"] if bands else
                  "no common Poppler band contains every recognised divider")
        result = {
            "status": "unevaluable", "reason": reason,
            **coverage_evidence, "bands": bands,
        }
        # Preserve the original cell-clipped referee as the first and
        # authoritative attempt.  Only its exact empty-band verdict may retry
        # against a complete source band attached across/outside one cell edge.
        # Detached and two-edge-enveloping bands never retry, and every other
        # fail-closed verdict remains untouched.
        if (_evaluation_window is None
                and not bands
                and reason == (
                    "no common Poppler band contains every recognised divider")
                and attached_external_band):
            return classify_band(
                cell, page, ledger_state=ledger_state,
                _evaluation_window=(seed_y0, seed_y1))
        return result
    if seed_span <= 0 or measured_span <= seed_span / 2:
        return {
            "status": "unevaluable",
            "reason": (
                "source topology does not occupy a strict majority "
                "of the full comb band"
            ),
            **coverage_evidence,
            "bands": measured,
        }
    topologies = measured_topologies
    topology_reason = "one source topology contains every recognised anchor"
    superset_relations: list[dict[str, Any]] = []
    if len(topologies) == 1:
        chosen_topology = next(iter(topologies))
    else:
        # A thick group separator can be slightly shorter than the hairline
        # seeds beside it.  The y partition then has a narrow seed-only cap and
        # a much taller slab with the complete compartment topology.  That is
        # not competing evidence: the longer separator still visibly divides
        # the comb.  Admit the richer topology only when it contains every
        # divider in every other slab (within the fixed position bound) and
        # occupies a strict majority of the measured vertical band.  A short
        # midpoint or two genuinely competing slabs remains UNEVALUABLE.
        def contains(superset: tuple[float, ...],
                     subset: tuple[float, ...]) -> bool:
            available = list(superset)
            for value in subset:
                choices = sorted(
                    (abs(candidate - value), index)
                    for index, candidate in enumerate(available)
                    if near(candidate, value)
                )
                if not choices:
                    return False
                _distance, index = choices[0]
                available.pop(index)
            return True

        for candidate in sorted(topologies):
            for other in sorted(topologies):
                if candidate == other:
                    continue
                superset_relations.append({
                    "candidate": list(candidate),
                    "other": list(other),
                    "contains": contains(candidate, other),
                    "proper": (
                        len(candidate) > len(other)
                        and contains(candidate, other)
                    ),
                })
        dominant = [
            candidate for candidate in topologies
            if all(
                other == candidate
                or (len(candidate) > len(other)
                    and contains(candidate, other))
                for other in topologies
            )
            and topology_coverage[candidate] > seed_span / 2
        ]
        if len(dominant) != 1:
            return {
                "status": "unevaluable",
                "reason": "source slabs have different divider topology",
                **coverage_evidence,
                "topology_superset_relations": superset_relations,
                "bands": measured,
            }
        chosen_topology = dominant[0]
        topology_reason = (
            "one richer source topology contains every other slab and "
            "occupies a strict majority of the comb band"
        )
    chosen = max(
        (band for band in measured if topology(band) == chosen_topology),
        key=lambda band: (
            float(band["y1"]) - float(band["y0"]),
            -float(band["y0"]),
            -float(band["y1"]),
            tuple(float(value) for value in band["source_divider_x"]),
        ),
    )
    if not bool(chosen.get("anchors_complete")):
        # An already-active comb can use source absence as evidence against a
        # stale lattice anchor, but only when the source proof is exhaustive.
        # This path must never discover a new comb: retained subjects and raw
        # table/label cells remain ineligible, and every observed divider must
        # still map one-to-one to a declared anchor.  At every remaining anchor
        # Poppler must expose the raw rail and one supported non-target owner
        # must finally erase it across the whole open band.  That proves the
        # smaller final topology without assuming the lattice count is correct.
        partial_bands = [
            band for band in measured
            if not bool(band.get("anchors_complete"))
        ]
        missing_sets = {
            tuple(float(value) for value in band.get("missing_anchor_x", ()))
            for band in partial_bands
        }
        observed_anchor_sets = {
            tuple(sorted(
                float(match["layout_x"])
                for match in band.get("anchor_matches", ())))
            for band in partial_bands
        }
        partial_components_valid = all(
            band.get("source_divider_x")
            and len(band.get("anchor_matches", ()))
            == len(band.get("source_divider_x", ()))
            and len({
                float(match["layout_x"])
                for match in band.get("anchor_matches", ())
            }) == len(band.get("anchor_matches", ()))
            and all(
                not bool(component.get("clipped"))
                for component in band.get("components", ())
            )
            for band in partial_bands
        )
        full_partial_coverage = (
            ledger_state == "active_unresolved"
            and len(topologies) == 1
            and len(partial_bands) == len(measured)
            and bool(partial_bands)
            and not ignored_slabs
            and abs(measured_span - seed_span) <= 1e-6
            and abs(topology_coverage[chosen_topology] - seed_span) <= 1e-6
            and len(missing_sets) == 1
            and len(observed_anchor_sets) == 1
            and partial_components_valid
        )
        missing_anchor_proofs: list[dict[str, Any]] = []
        anchor_corridor_clipped_paints: list[Paint] = []
        anchor_corridor_unsupported_regions: list[UnsupportedRegion] = []
        if full_partial_coverage:
            missing_anchor_x = sorted(next(iter(missing_sets)))
            observed_anchor_x = sorted(next(iter(observed_anchor_sets)))
            if not missing_anchor_x:
                full_partial_coverage = False
            declared_anchor_x = sorted({
                *observed_anchor_x,
                *missing_anchor_x,
            })
            anchor_corridor_clipped_paints = [
                paint for paint in page.paints
                if paint.clipped
                and any(
                    paint.x1 > anchor - POSITION_TOL_PT
                    and paint.x0 < anchor + POSITION_TOL_PT
                    for anchor in declared_anchor_x
                )
                and min(paint.y1, seed_y1) - max(paint.y0, seed_y0)
                > 1e-9
            ]
            anchor_corridor_unsupported_regions = [
                region for region in page.unsupported
                if any(
                    region.x1 > anchor - POSITION_TOL_PT
                    and region.x0 < anchor + POSITION_TOL_PT
                    for anchor in declared_anchor_x
                )
                and min(region.y1, seed_y1) - max(region.y0, seed_y0)
                > 1e-9
            ]
            if (anchor_corridor_clipped_paints
                    or anchor_corridor_unsupported_regions):
                full_partial_coverage = False
            for anchor in missing_anchor_x:
                corridor_x0 = anchor - POSITION_TOL_PT
                corridor_x1 = anchor + POSITION_TOL_PT
                raw_anchor_rails = [
                    paint for paint in page.paints
                    if abs(paint.tone - divider_tone) <= 1e-8
                    and paint.width <= max_width
                    and paint.height > paint.width
                    and near(paint.cx, anchor)
                    and sum(
                        near(paint.cx, missing)
                        for missing in declared_anchor_x
                    ) == 1
                    and paint.x1 > corridor_x0 and paint.x0 < corridor_x1
                    and min(paint.y1, seed_y1) - max(paint.y0, seed_y0)
                    > POSITION_TOL_PT
                ]
                proof_x0 = min(
                    [corridor_x0,
                     *(paint.x0 for paint in raw_anchor_rails)])
                proof_x1 = max(
                    [corridor_x1,
                     *(paint.x1 for paint in raw_anchor_rails)])
                clipped_paints = [
                    paint for paint in page.paints
                    if paint.clipped
                    and paint.x1 > proof_x0 and paint.x0 < proof_x1
                    and min(paint.y1, seed_y1) - max(paint.y0, seed_y0)
                    > 1e-9
                ]
                unsupported_regions = [
                    region for region in page.unsupported
                    if region.x1 > proof_x0 and region.x0 < proof_x1
                    and min(region.y1, seed_y1) - max(region.y0, seed_y0)
                    > 1e-9
                ]
                final_target_segments: list[dict[str, float]] = []
                erasure_slabs: list[dict[str, Any]] = []
                erasure_roles: set[tuple[str, int, str, float]] = set()
                proof_top_role_ambiguities: list[dict[str, Any]] = []
                raw_rail_identity_valid = (
                    len(raw_anchor_rails) == 1
                    and raw_anchor_rails[0].y0 <= seed_y0 + 1e-6
                    and raw_anchor_rails[0].y1 >= seed_y1 - 1e-6
                )
                erasure_valid = raw_rail_identity_valid
                for band in measured:
                    mid = (float(band["y0"]) + float(band["y1"])) / 2
                    final_segments = composited_segments(mid, page.paints)
                    for segment in final_segments:
                        if (abs(float(segment["tone"]) - divider_tone)
                                <= 1e-8
                                and float(segment["x1"]) > proof_x0
                                and float(segment["x0"]) < proof_x1):
                            final_target_segments.append({
                                "y": round(mid, 6),
                                "x0": round(float(segment["x0"]), 6),
                                "x1": round(float(segment["x1"]), 6),
                            })
                    proof_active_paints = [
                        paint for paint in page.paints
                        if paint.y0 <= mid <= paint.y1
                        and paint.x1 > proof_x0 and paint.x0 < proof_x1
                    ]
                    proof_endpoints = {proof_x0, proof_x1}
                    for paint in proof_active_paints:
                        proof_endpoints.update((
                            max(proof_x0, paint.x0),
                            min(proof_x1, paint.x1),
                        ))
                    ordered_proof_x = sorted(proof_endpoints)
                    for left, right in zip(
                            ordered_proof_x, ordered_proof_x[1:]):
                        if right - left <= 1e-9:
                            continue
                        sample_x = (left + right) / 2
                        owners = [
                            paint for paint in proof_active_paints
                            if paint.x0 < sample_x < paint.x1
                        ]
                        if not owners:
                            continue
                        max_order = max(paint.order for paint in owners)
                        top_roles = sorted({
                            (
                                paint.element,
                                paint.order,
                                paint.kind,
                                round(paint.tone, 8),
                                paint.clipped,
                            )
                            for paint in owners
                            if paint.order == max_order
                        })
                        if len(top_roles) > 1:
                            erasure_valid = False
                            proof_top_role_ambiguities.append({
                                "y": round(mid, 6),
                                "x0": round(left, 6),
                                "x1": round(right, 6),
                                "roles": [
                                    {
                                        "element": role[0],
                                        "order": role[1],
                                        "kind": role[2],
                                        "tone": role[3],
                                        "clipped": role[4],
                                    }
                                    for role in top_roles
                                ],
                            })
                    active_rails = [
                        paint for paint in raw_anchor_rails
                        if paint.y0 <= mid <= paint.y1
                    ]
                    raw_intervals = sorted(
                        (paint.x0, paint.x1)
                        for paint in active_rails
                        if paint.x1 - paint.x0 > 1e-9
                    )
                    merged_raw: list[list[float]] = []
                    for left, right in raw_intervals:
                        if (merged_raw
                                and left <= merged_raw[-1][1] + 1e-6):
                            merged_raw[-1][1] = max(
                                merged_raw[-1][1], right)
                        else:
                            merged_raw.append([left, right])
                    slab_evidence: dict[str, Any] = {
                        "y0": round(float(band["y0"]), 6),
                        "y1": round(float(band["y1"]), 6),
                        "sample_y": round(mid, 6),
                        "raw_rail_elements": sorted({
                            paint.element for paint in active_rails
                        }),
                        "raw_intervals": [
                            [round(left, 6), round(right, 6)]
                            for left, right in merged_raw
                        ],
                        "final_owner_segments": [],
                        "ambiguous_top_roles": [],
                    }
                    slab_roles: set[tuple[str, int, str, float]] = set()
                    if len(merged_raw) != 1:
                        erasure_valid = False
                    for raw_left, raw_right in merged_raw:
                        endpoints = {raw_left, raw_right}
                        active_paints = [
                            paint for paint in page.paints
                            if paint.y0 <= mid <= paint.y1
                            and paint.x1 > raw_left and paint.x0 < raw_right
                        ]
                        for paint in active_paints:
                            endpoints.update((
                                max(raw_left, paint.x0),
                                min(raw_right, paint.x1),
                            ))
                        ordered_x = sorted(endpoints)
                        for left, right in zip(ordered_x, ordered_x[1:]):
                            if right - left <= 1e-9:
                                continue
                            sample_x = (left + right) / 2
                            owners = [
                                paint for paint in active_paints
                                if paint.x0 < sample_x < paint.x1
                            ]
                            if not owners:
                                erasure_valid = False
                                continue
                            max_order = max(paint.order for paint in owners)
                            top_owners = [
                                paint for paint in owners
                                if paint.order == max_order
                            ]
                            top_roles = sorted({
                                (
                                    paint.element,
                                    paint.order,
                                    paint.kind,
                                    round(paint.tone, 8),
                                    paint.clipped,
                                )
                                for paint in top_owners
                            })
                            if len(top_roles) != 1:
                                erasure_valid = False
                                slab_evidence["ambiguous_top_roles"].append([
                                    {
                                        "element": role[0],
                                        "order": role[1],
                                        "kind": role[2],
                                        "tone": role[3],
                                        "clipped": role[4],
                                    }
                                    for role in top_roles
                                ])
                                continue
                            owner = top_owners[0]
                            role = (
                                owner.element,
                                owner.order,
                                owner.kind,
                                round(owner.tone, 8),
                            )
                            slab_roles.add(role)
                            erasure_roles.add(role)
                            slab_evidence["final_owner_segments"].append({
                                "x0": round(left, 6),
                                "x1": round(right, 6),
                                "element": owner.element,
                                "order": owner.order,
                                "kind": owner.kind,
                                "tone": round(owner.tone, 8),
                                "clipped": owner.clipped,
                            })
                            if (owner.clipped
                                    or abs(owner.tone - divider_tone)
                                    <= 1e-8):
                                erasure_valid = False
                    if len(slab_roles) != 1:
                        erasure_valid = False
                    erasure_slabs.append(slab_evidence)
                if len(erasure_roles) != 1:
                    erasure_valid = False
                proof = {
                    "layout_x": round(anchor, 6),
                    "corridor_x0": round(corridor_x0, 6),
                    "corridor_x1": round(corridor_x1, 6),
                    "proof_x0": round(proof_x0, 6),
                    "proof_x1": round(proof_x1, 6),
                    "open_y0": round(seed_y0, 6),
                    "open_y1": round(seed_y1, 6),
                    "raw_anchor_rails": [
                        {
                            "element": paint.element,
                            "order": paint.order,
                            "kind": paint.kind,
                            "x0": round(paint.x0, 6),
                            "x1": round(paint.x1, 6),
                            "center_x": round(paint.cx, 6),
                            "delta_pt": round(paint.cx - anchor, 6),
                            "y0": round(paint.y0, 6),
                            "y1": round(paint.y1, 6),
                            "tone": round(paint.tone, 8),
                            "clipped": paint.clipped,
                        }
                        for paint in sorted(
                            raw_anchor_rails,
                            key=lambda item: (
                                item.order, item.element,
                                item.x0, item.y0, item.x1, item.y1),
                        )
                    ],
                    "raw_rail_identity_valid": raw_rail_identity_valid,
                    "proof_top_role_ambiguities": (
                        proof_top_role_ambiguities),
                    "erasure_slabs": erasure_slabs,
                    "erasure_owner_roles": [
                        {
                            "element": role[0],
                            "order": role[1],
                            "kind": role[2],
                            "tone": role[3],
                        }
                        for role in sorted(erasure_roles)
                    ],
                    "clipped_paint_elements": sorted({
                        paint.element for paint in clipped_paints
                    }),
                    "final_target_tone_segments": final_target_segments,
                    "unsupported_region_elements": sorted({
                        region.element for region in unsupported_regions
                    }),
                }
                missing_anchor_proofs.append(proof)
                if (not erasure_valid
                        or clipped_paints or unsupported_regions
                        or final_target_segments):
                    full_partial_coverage = False
            if full_partial_coverage:
                certificate = {
                    "criterion": ACTIVE_PARTIAL_ANCHOR_CRITERION,
                    "valid": True,
                    "ledger_state": ledger_state,
                    "subject_ownership_basis": (
                        "active_unresolved lattice ledger"
                    ),
                    "independent_source_enclosure_proven": False,
                    "divider_count_basis": (
                        "final-composited Poppler vector topology"
                    ),
                    "missing_anchor_basis": (
                        "raw target-tone rail exhaustively replaced by one "
                        "supported unclipped non-target final owner"
                    ),
                    "anchor_corridor_clipped_paint_elements": sorted({
                        paint.element
                        for paint in anchor_corridor_clipped_paints
                    }),
                    "anchor_corridor_unsupported_region_elements": sorted({
                        region.element
                        for region in anchor_corridor_unsupported_regions
                    }),
                    "open_y0": round(seed_y0, 6),
                    "open_y1": round(seed_y1, 6),
                    "coverage_pt": round(measured_span, 6),
                    "source_divider_x": list(chosen_topology),
                    "observed_anchor_x": observed_anchor_x,
                    "missing_anchor_x": missing_anchor_x,
                    "missing_anchor_proofs": missing_anchor_proofs,
                }
                finalized_partial = refuted_outer_rails(chosen)
                if finalized_partial is None:
                    return {
                        "status": "unevaluable",
                        "reason": (
                            "an outer-region prose refutation cannot "
                            "stand: prose and divider-tone structure "
                            "conflict, or no comb band would remain"),
                        **coverage_evidence,
                        "chosen_topology": list(chosen_topology),
                        "topology_superset_relations": superset_relations,
                        "bands": measured,
                    }
                return {
                    "status": "measured",
                    "reason": (
                        "ledger-owned active subject has full-band Poppler "
                        "proof of erased lattice anchors"
                    ),
                    **{key: value for key, value in finalized_partial.items()
                       if key != "status"},
                    **coverage_evidence,
                    "chosen_topology": list(chosen_topology),
                    "topology_superset_relations": superset_relations,
                    "active_partial_anchor_certificate": certificate,
                }
        return {
            "status": "unevaluable",
            "reason": "dominant source topology omits recognised anchors",
            **coverage_evidence,
            "chosen_topology": list(chosen_topology),
            "topology_superset_relations": superset_relations,
            "bands": measured,
        }
    if chosen.get("unproven_subject_gaps"):
        return {
            "status": "unevaluable",
            "reason": (
                "chosen source topology lacks a clean single-frame subject proof"
            ),
            **coverage_evidence,
            "chosen_topology": list(chosen_topology),
            "topology_superset_relations": superset_relations,
            "bands": measured,
        }
    finalized = refuted_outer_rails(chosen)
    if finalized is None:
        return {
            "status": "unevaluable",
            "reason": (
                "an outer-region prose refutation cannot stand: prose and "
                "divider-tone structure conflict, or no comb band would "
                "remain"),
            **coverage_evidence,
            "chosen_topology": list(chosen_topology),
            "topology_superset_relations": superset_relations,
            "bands": measured,
        }
    return {
        "status": "measured",
        "reason": topology_reason,
        **{key: value for key, value in finalized.items()
           if key != "status"},
        **coverage_evidence,
        "chosen_topology": list(chosen_topology),
        "topology_superset_relations": superset_relations,
    }


def measured_glyph_boxes(page: SvgPage,
                         bbox_value: Sequence[float],
                         ) -> list[UnsupportedRegion]:
    """Poppler's own glyph bounds that certainly print inside one rectangle.

    Two deliberate narrowings, both of which can only make a glyph count
    SMALLER, so an error in either errs towards refusing a producer's claim
    rather than granting it:

    * a `clipped` region is under an unresolved clip/mask/filter, a non-unit
      opacity, or a non-default paint order, so this parser cannot say it
      prints at all; and
    * a bound that leaves the rectangle is not unambiguously this rectangle's
      text.  Poppler pads a STROKED glyph's bound by its join, so the test is
      containment of the padded bound, never of a nominal glyph box.
    """
    x0, y0, x1, y1 = (float(value) for value in bbox_value)
    return [
        region for region in page.unsupported
        if region.reason.startswith(MEASURED_GLYPH_REASON_PREFIXES)
        and not region.clipped
        and region.x0 >= x0 and region.x1 <= x1
        and region.y0 >= y0 and region.y1 <= y1
    ]


def composited_vertical_segments(x: float,
                                 all_paints: Sequence[Paint]
                                 ) -> list[dict[str, Any]]:
    """Exact final-tone y components on one open vertical slab.

    The vertical mirror of `composited_segments`, with the same last-owner
    rule: partition at every paint edge and let the highest paint order own
    each atomic piece, so occluded ink never masquerades as final.
    """
    active = [
        paint for paint in all_paints
        if paint.x0 <= x <= paint.x1 and paint.y1 - paint.y0 > 1e-9
    ]
    endpoints = sorted({
        coordinate for paint in active for coordinate in (paint.y0, paint.y1)
    })
    atomic: list[dict[str, Any]] = []
    for low, high in zip(endpoints, endpoints[1:]):
        if high - low <= 1e-9:
            continue
        midpoint = (low + high) / 2
        owners = [
            paint for paint in active
            if paint.y0 < midpoint < paint.y1
        ]
        if not owners:
            continue
        owner = max(owners, key=lambda paint: paint.order)
        atomic.append({"y0": low, "y1": high, "tone": owner.tone})
    return atomic


def _final_line_profile(page: SvgPage, axis: str, coord: float,
                        span_a: float, span_b: float,
                        ) -> list[tuple[float, float, float | None]]:
    """Atomic (a, b, tone) pieces of one line's final composite; None = paper."""
    if axis == "h":
        segments = [
            (seg["x0"], seg["x1"], seg["tone"])
            for seg in composited_segments(coord, page.paints)
        ]
    else:
        segments = [
            (seg["y0"], seg["y1"], seg["tone"])
            for seg in composited_vertical_segments(coord, page.paints)
        ]
    profile: list[tuple[float, float, float | None]] = []
    cursor = span_a
    for low, high, tone in sorted(segments):
        low, high = max(low, span_a), min(high, span_b)
        if high - low <= 1e-9:
            continue
        if low > cursor:
            profile.append((cursor, low, None))
        profile.append((low, high, round(float(tone), 6)))
        cursor = high
    if cursor < span_b:
        profile.append((cursor, span_b, None))
    return profile


def _edge_incomplete_gap(page: SvgPage, axis: str, coord: float,
                         span_a: float, span_b: float) -> float:
    """Longest stretch of one line that is neither painted nor a boundary.

    A partition edge exists in the FINAL PICTURE wherever the line itself
    carries non-white ink, or the final tones on its two sides differ -- an
    edge drawn as a knockout against ink is still an edge (2550M p1c7's
    0.72pt sliver boundary is exactly that).  What remains is void: final
    paper on the line and the same final tone on both sides.  Returns the
    longest void run; 0.0 is a complete edge.
    """
    offset = PARTITION_EDGE_PROBE_OFFSET_PT
    profiles = [
        _final_line_profile(page, axis, coord, span_a, span_b),
        _final_line_profile(page, axis, coord - offset, span_a, span_b),
        _final_line_profile(page, axis, coord + offset, span_a, span_b),
    ]
    breakpoints = sorted({
        value
        for profile in profiles
        for a, b, _tone in profile
        for value in (a, b)
    } | {span_a, span_b})

    def tone_at(profile: list[tuple[float, float, float | None]],
                point: float) -> float | None:
        for a, b, tone in profile:
            if a <= point <= b:
                return tone
        return None

    worst = 0.0
    run = 0.0
    for low, high in zip(breakpoints, breakpoints[1:]):
        if high - low <= 1e-9:
            continue
        midpoint = (low + high) / 2
        on_line = tone_at(profiles[0], midpoint)
        painted = on_line is not None and on_line < 1.0 - 1e-8
        differs = (tone_at(profiles[1], midpoint)
                   != tone_at(profiles[2], midpoint))
        if painted or differs:
            run = 0.0
        else:
            run += high - low
            worst = max(worst, run)
    return worst


def partition_edge_corroboration(
        subject: dict[str, Any],
        page: SvgPage,
        label: str,
        ) -> dict[str, Any]:
    """Does the final picture draw an edge splitting the legacy rectangle?

    `emission-suppressed-no-rectangular-owner` / `painted-edge-partition`
    claims the legacy comb's rectangle is no longer one writing surface.  The
    minimal factual content Poppler can check: at least ONE internal edge of
    the published mapped partition spans the rectangle fully and is complete
    in the final picture -- painted, or a tone boundary, at every point
    (longest void <= POSITION_TOL_PT).  One complete spanning edge proves the
    rectangle is split; the full decomposition is the producer's ledger
    arithmetic, checked elsewhere.

    Returns a verdict certificate and NEVER raises on a negative: "the paper
    does not show it" leaves the subject unevaluable and unretirable, which
    is exactly what it deserves.  Measured 2026-08-14 over all 18 subjects
    carrying this reason: 17 corroborate; 1800-2018 p1c4's only full-span
    edge (h at y=100.24) has a 42.55pt void and stays refused.
    """
    ledger = subject["ledger"]
    bbox = [float(value) for value in ledger["legacy_bbox"]]
    keys = ledger.get("mapped_partition_subject_keys") or []
    segments: dict[tuple[str, float], list[tuple[float, float]]] = {}
    for key in keys:
        _page_part, rest = str(key).split("@", 1)
        x0, y0, x1, y1 = (float(value) for value in rest.split(","))
        for axis, coord, a, b, on_border in (
                ("v", x0, y0, y1, abs(x0 - bbox[0]) <= 1e-6),
                ("v", x1, y0, y1, abs(x1 - bbox[2]) <= 1e-6),
                ("h", y0, x0, x1, abs(y0 - bbox[1]) <= 1e-6),
                ("h", y1, x0, x1, abs(y1 - bbox[3]) <= 1e-6)):
            if not on_border:
                segments.setdefault(
                    (axis, round(coord, 3)), []).append((a, b))
    checked = 0
    certifying = None
    worst_full_span_gap = None
    for (axis, coord), spans in sorted(segments.items()):
        spans.sort()
        merged = [list(spans[0])]
        for a, b in spans[1:]:
            if a <= merged[-1][1] + 1e-6:
                merged[-1][1] = max(merged[-1][1], b)
            else:
                merged.append([a, b])
        for a, b in merged:
            lo_lim, hi_lim = ((bbox[0], bbox[2]) if axis == "h"
                              else (bbox[1], bbox[3]))
            if a > lo_lim + POSITION_TOL_PT or b < hi_lim - POSITION_TOL_PT:
                continue
            checked += 1
            gap = _edge_incomplete_gap(page, axis, coord, a, b)
            if worst_full_span_gap is None or gap < worst_full_span_gap:
                worst_full_span_gap = gap
            if gap <= POSITION_TOL_PT and certifying is None:
                certifying = {
                    "axis": axis, "coord": round(coord, 6),
                    "span_a": round(a, 6), "span_b": round(b, 6),
                    "void_pt": round(gap, 6),
                }
    return {
        "criterion": SOURCE_PARTITION_EDGE_CRITERION,
        "corroborated": certifying is not None,
        "full_span_edges_checked": checked,
        "certifying_edge": certifying,
        "least_void_pt": (round(worst_full_span_gap, 6)
                          if worst_full_span_gap is not None else None),
    }


def crossing_rule_corroboration(
        subject: dict[str, Any],
        page: SvgPage,
        label: str,
        ) -> dict[str, Any]:
    """Is every legacy divider a rule that CROSSES the subject, comb-scoped to
    nothing?

    `emission-suppressed-no-final-visible-band` claims no comb band supports
    the legacy subject on its owner.  The checkable content: each legacy
    divider's final ink runs continuously across the subject's whole height
    AND continues beyond both edges -- a crossing table rule, not a divider
    hanging inside a band.  A comb-scoped divider would end at or inside the
    subject; a stroke that crosses and keeps going belongs to the page's
    table structure, which is exactly why no band exists here.

    Verdict certificate; a negative never raises.  Measured 2026-08-14 on the
    sole subject carrying this reason (1600WP p1c36): its one divider at
    x=53.52 spans y 257.45..346.37 finally painted -- the subject is
    293.84..311.12, so the stroke overruns it by more than 17pt on each side.
    """
    ledger = subject["ledger"]
    bbox = [float(value) for value in ledger["legacy_bbox"]]
    comb = ledger.get("legacy_comb") or {}
    dividers = [float(value) for value in comb.get("divider_x") or ()]
    evidence = []
    corroborated = bool(dividers)
    for divider_x in dividers:
        # The compositor's pieces are ATOMIC -- cut at every paint edge -- so
        # a continuous rule arrives as many abutting pieces.  Coalesce runs
        # first (a sub-tolerance seam is endpoint coincidence, the file's one
        # coincidence rule), then ask whether one run covers and overruns.
        pieces = sorted(
            (piece for piece in composited_vertical_segments(
                divider_x, page.paints)
             if piece["tone"] < 1.0 - 1e-8),
            key=lambda piece: piece["y0"])
        runs: list[list[float]] = []
        for piece in pieces:
            if runs and piece["y0"] <= runs[-1][1] + POSITION_TOL_PT:
                runs[-1][1] = max(runs[-1][1], piece["y1"])
            else:
                runs.append([piece["y0"], piece["y1"]])
        covering = None
        for run in runs:
            if (run[0] <= bbox[1] + POSITION_TOL_PT
                    and run[1] >= bbox[3] - POSITION_TOL_PT):
                covering = {"y0": run[0], "y1": run[1]}
                break
        crosses = bool(
            covering is not None
            and covering["y0"] < bbox[1] - POSITION_TOL_PT
            and covering["y1"] > bbox[3] + POSITION_TOL_PT)
        corroborated = corroborated and crosses
        evidence.append({
            "x": round(divider_x, 6),
            "crosses": crosses,
            "final_y0": (round(covering["y0"], 6)
                         if covering is not None else None),
            "final_y1": (round(covering["y1"], 6)
                         if covering is not None else None),
        })
    return {
        "criterion": SOURCE_CROSSING_RULE_CRITERION,
        "corroborated": corroborated,
        "dividers": evidence,
    }


def retained_suppression_corroboration(
        subject: dict[str, Any],
        band: dict[str, Any],
        page: SvgPage,
        label: str,
        ) -> dict[str, Any]:
    """Re-derive a retained subject's suppression reason from the source.

    This is the price of THE RETAINED-TOPOLOGY INVARIANT's one exception.  A
    subject withdrawn from adjudication may keep a RESOLVED topology only when
    its published reason makes a claim about the paper that this referee can
    check against the paper -- and the check is made here, from `pdftocairo`'s
    vector output for the pinned PDF, never from the reason code itself.  The
    reason code selects which question to ask; it is not an answer to it.

    `emission-suppressed-caption-block-not-character-cells` claims: the source
    printed running prose in EVERY compartment, so those compartments are not
    character cells.  Three things are demanded of the source, in order:

    1. Poppler must be able to measure the band at all, and
    2. must draw the published compartment walls where the ledger says they
       are -- the walls the glyphs are then attributed to are POPPLER's
       `source_divider_x`, not the ledger's `divider_x`; and
    3. every compartment those walls cut must hold more than one glyph.

    (3) is the producer's own stated test (`lattice.printed_caption_refutes_
    comb`) re-derived through a different PDF stack: the lattice reaches it
    from MuPDF text runs assigned to the cell, this reaches it from Poppler
    glyph `use` bounds contained in the rectangle.  A comb compartment is one
    character wide, so one glyph is all the pre-printed decoration that can
    fit -- the `%`, the money point, the TIN dash -- and `> 1` is therefore a
    statement about character cells rather than a tuned constant.  Measured
    over all 4,583 published subjects of the 53-form corpus, the minimum
    per-compartment count is 0 for 4,535 and 1 for 27; all 21 subjects above 1
    are already RETAINED ones, and the 11 that publish this reason score 28 to
    87.  No ACTIVE comb in the corpus could carry this claim, which is what
    gives the refusal its force: the census is not a formality that every comb
    would pass.

    The returned census is evidence for the caller and for the self-test; it is
    NOT written into the report, because `gate.CELL_KEYS` and
    `gate.MEASURED_REFEREE_KEYS` fix the published per-subject schema exactly
    and this file does not own that schema.  Nothing is hidden by that: the
    check cannot be skipped (`assert_suppression_corroborations_exhaustive`),
    and every refusal names the compartment and the whole count vector.
    """
    criterion = subject.get("source_suppression_criterion")
    if criterion == SOURCE_PARTITION_EDGE_CRITERION:
        return partition_edge_corroboration(subject, page, label)
    if criterion == SOURCE_CROSSING_RULE_CRITERION:
        return crossing_rule_corroboration(subject, page, label)
    if criterion != SOURCE_CAPTION_BLOCK_CRITERION:
        # Unreachable through `validate_comb_ledger`, which only ever stores a
        # value from RETAINED_SUPPRESSION_SOURCE_CRITERIA.  A new tuple added
        # to that table without a re-derivation to go with it must fail here
        # rather than be waved through by the branch below.
        raise RefereeError(
            f"{label} suppression criterion has no source re-derivation: "
            f"{criterion!r}")
    if band.get("status") != "measured":
        raise RefereeError(
            f"{label} suppression is uncorroborated: the source band is not "
            f"measurable ({band.get('reason', 'no reason')})")
    topology = subject["topology"]
    source_x = [
        finite_number(value, f"{label} source_divider_x")
        for value in band.get("source_divider_x") or ()
    ]
    # The compartments the glyphs are attributed to run between the source's
    # own RAILS, not between the subject rectangle's edges: `classify_band`
    # measures where the box closes, and a rectangle that also rules a caption
    # would otherwise hand this census a compartment the sheet never printed.
    rails = [
        finite_number(value, f"{label} source_rail_x")
        for value in band.get("source_rail_x") or ()
    ]
    if len(rails) != 2 or rails[1] <= rails[0]:
        raise RefereeError(
            f"{label} suppression is uncorroborated: the source publishes no "
            "rail pair for the retained band")
    enclosed = [value for value in source_x if rails[0] < value < rails[1]]
    if (band.get("compartments") != topology["cells"]
            or len(enclosed) != topology["cells"] - 1
            or not bool(band.get("positions_match"))):
        raise RefereeError(
            f"{label} suppression is uncorroborated: the source draws "
            f"{band.get('compartments')} compartments where the retained "
            f"topology publishes {topology['cells']}")
    walls = [rails[0], *enclosed, rails[1]]
    if any(right <= left for left, right in zip(walls, walls[1:])):
        raise RefereeError(
            f"{label} suppression is uncorroborated: the source walls are "
            "not strictly increasing across the subject rectangle")
    counts = [0] * (len(walls) - 1)
    for region in measured_glyph_boxes(page, subject["legacy_bbox"]):
        centre = (region.x0 + region.x1) / 2.0
        for index in range(len(counts)):
            if walls[index] <= centre < walls[index + 1]:
                counts[index] += 1
                break
    weakest = min(counts)
    if weakest <= CHARACTER_CELL_MAX_PRINTED_GLYPHS:
        raise RefereeError(
            f"{label} suppression is uncorroborated: the source prints "
            f"{weakest} glyph(s) in compartment {counts.index(weakest) + 1} "
            f"of {len(counts)}, so the source does not agree that these "
            f"compartments are a caption block; per-compartment counts "
            f"{counts}")
    return {
        "criterion": criterion,
        "corroborated": True,
        "source_divider_x": source_x,
        "compartment_glyph_counts": counts,
        "compartment_glyph_walls": walls,
        "min_compartment_glyphs": weakest,
        "threshold": CHARACTER_CELL_MAX_PRINTED_GLYPHS,
    }


def assert_suppression_corroborations_exhaustive(
        slug: str,
        obligations: dict[str, str],
        corroborations: dict[str, str],
        ) -> None:
    """A corroboration that did not run is an error, never a pass.

    `validate_comb_ledger` records the debt at the moment it admits the
    reason; this is where the debt is proven settled.  Comparing the two
    inventories -- not just their sizes -- also catches a corroboration
    performed under a different criterion from the one that was admitted.
    """
    if corroborations == obligations:
        return
    missing = sorted(set(obligations) - set(corroborations))
    unexpected = sorted(set(corroborations) - set(obligations))
    mismatched = sorted(
        cell_id for cell_id in set(obligations) & set(corroborations)
        if obligations[cell_id] != corroborations[cell_id])
    raise RefereeError(
        f"{slug}: retained suppression corroboration is not exhaustive"
        + (f"; not re-derived: {', '.join(missing)}" if missing else "")
        + (f"; not owed: {', '.join(unexpected)}" if unexpected else "")
        + (f"; wrong criterion: {', '.join(mismatched)}" if mismatched else ""))


def _audit_optional_int(value: Any, label: str) -> int | None:
    if value is None:
        return None
    return exact_nonnegative_int(value, label)


def _audit_number_list(value: Any, label: str) -> list[float] | None:
    if value is None:
        return None
    if not isinstance(value, list):
        raise RefereeError(f"{label} is not a numeric list")
    return [
        finite_number(item, f"{label}[{index}]")
        for index, item in enumerate(value)
    ]


def validate_audit_position_evidence(
        name: str,
        value: Any,
        *,
        outer: bool,
        ) -> bool:
    """Validate one independently published fixed-tolerance relation."""
    if not isinstance(value, dict):
        raise RefereeError(f"audit offender {name} is not an object")
    axis = "outer" if outer else "internal"
    actual_key = f"actual_{axis}_edges_x"
    expected_key = f"expected_{axis}_edges_x"
    required = {
        "comparable", "tolerance_pt", actual_key, expected_key,
        "count_matches", "deltas_pt", "matches",
    }
    allowed = required | {"unavailable_reason"}
    if not required <= set(value) or set(value) - allowed:
        raise RefereeError(
            f"audit offender {name} has an unsupported evidence schema")
    comparable = value["comparable"]
    if not isinstance(comparable, bool):
        raise RefereeError(f"audit offender {name}.comparable is not boolean")
    expected_tolerance = AUDIT_POSITION_TOLERANCE_PT.get(name)
    if expected_tolerance is None:
        raise RefereeError(
            f"audit offender {name} has no pinned position tolerance")
    tolerance = finite_number(
        value["tolerance_pt"], f"audit offender {name}.tolerance_pt")
    if abs(tolerance - expected_tolerance) > 1e-12:
        raise RefereeError(
            f"audit offender {name} changes the fixed position tolerance")
    actual = _audit_number_list(
        value[actual_key], f"audit offender {name}.{actual_key}")
    expected = _audit_number_list(
        value[expected_key], f"audit offender {name}.{expected_key}")
    if not comparable:
        if (value["count_matches"] is not None
                or value["deltas_pt"] is not None
                or value["matches"] is not None
                or not isinstance(value.get("unavailable_reason"), str)
                or not value["unavailable_reason"]):
            raise RefereeError(
                f"audit offender {name} has malformed unavailable evidence")
        return False
    if not isinstance(value["count_matches"], bool):
        raise RefereeError(
            f"audit offender {name}.count_matches is not boolean")
    count_matches = actual is not None and expected is not None and (
        len(actual) == len(expected))
    if value["count_matches"] is not count_matches:
        raise RefereeError(
            f"audit offender {name} has a false edge-count relation")
    deltas = _audit_number_list(
        value["deltas_pt"], f"audit offender {name}.deltas_pt")
    expected_deltas = (
        [round(left - right, 6) for left, right in zip(actual, expected)]
        if count_matches and actual is not None and expected is not None
        else None
    )
    if ((deltas is None) != (expected_deltas is None)
            or (deltas is not None and expected_deltas is not None
                and not same_numbers(deltas, expected_deltas))):
        raise RefereeError(
            f"audit offender {name} has false edge deltas")
    matches = bool(
        count_matches
        and all(abs(delta) <= tolerance for delta in expected_deltas or ())
    )
    if not isinstance(value["matches"], bool) or value["matches"] is not matches:
        raise RefereeError(
            f"audit offender {name} has a false position verdict")
    return not matches


def validate_audit_container_binding(value: Any) -> dict[str, bool]:
    if not isinstance(value, dict) or set(value) != {
        "expected_page", "emitted_id_page", "emitted_dom_page",
        "page_matches", "expected_rect", "actual_rect", "rect_deltas_pt",
        "rect_matches", "tolerance_pt",
    }:
        raise RefereeError(
            "audit offender has malformed emission container evidence")
    expected_page = exact_nonnegative_int(
        value["expected_page"], "audit offender expected page")
    if expected_page == 0:
        raise RefereeError("audit offender expected page is not one-based")
    emitted_id_page = _audit_optional_int(
        value["emitted_id_page"], "audit offender emitted id page")
    emitted_dom_page = _audit_optional_int(
        value["emitted_dom_page"], "audit offender emitted DOM page")
    expected_rect = _audit_number_list(
        value["expected_rect"], "audit offender expected rect")
    actual_rect = _audit_number_list(
        value["actual_rect"], "audit offender actual rect")
    if expected_rect is None or len(expected_rect) != 4:
        raise RefereeError("audit offender expected rect is not four numbers")
    if actual_rect is not None and len(actual_rect) != 4:
        raise RefereeError("audit offender actual rect is not four numbers")
    page_matches = (
        emitted_id_page == expected_page
        and emitted_dom_page == expected_page
    )
    if (not isinstance(value["page_matches"], bool)
            or value["page_matches"] is not page_matches):
        raise RefereeError("audit offender has a false container-page relation")
    deltas = _audit_number_list(
        value["rect_deltas_pt"], "audit offender rect deltas")
    expected_deltas = (
        [left - right for left, right in zip(actual_rect, expected_rect)]
        if actual_rect is not None else None
    )
    if ((deltas is None) != (expected_deltas is None)
            or (deltas is not None and expected_deltas is not None
                and not same_numbers(deltas, expected_deltas))):
        raise RefereeError("audit offender has false container deltas")
    tolerance = finite_number(
        value["tolerance_pt"], "audit offender container tolerance")
    if abs(tolerance - HTML_GEOMETRY_EPSILON_PT) > 1e-12:
        raise RefereeError(
            "audit offender changes the fixed container tolerance")
    rect_matches = bool(
        expected_deltas is not None
        and all(abs(delta) <= tolerance for delta in expected_deltas)
    )
    if (not isinstance(value["rect_matches"], bool)
            or value["rect_matches"] is not rect_matches):
        raise RefereeError("audit offender has a false container-rect relation")
    return {
        "page_mismatch": not page_matches,
        "rect_mismatch": not rect_matches,
    }


def audit_offender_dimensions(
        item: Any,
        expected_owner: dict[str, Any] | None = None,
        ) -> dict[str, Any]:
    """Re-derive every published offender relation from its raw evidence."""
    if not isinstance(item, dict):
        raise RefereeError("audit offender is not an object")
    required = {
        "cell", "page", "slots", "latticed", "printed",
        "printed_divider_x", "emission_state", "physical_slots",
        "declared_slots", "emitted_occurrences", "layout_relation",
        "emission_relation", "failure_kinds", "why",
    }
    allowed = required | {
        "slot_indexes", "input_slot_indexes", "slot_geometry",
        "emission_container_binding",
        "emission_layout_position", "emission_layout_outer_position",
        "emission_source_position", "source_frame_geometry",
        "emission_source_outer_position", "layout_source_outer_position",
        "source_topology_evidence", "effective_emission_state",
        "source_owner_certificate",
        "emitted_cell_binding_evidence", "raw_dom_evidence",
    }
    if not required <= set(item) or set(item) - allowed:
        raise RefereeError("audit offender has an unsupported schema")
    cell_id = item["cell"]
    if not isinstance(cell_id, str) or not cell_id:
        raise RefereeError("audit offender cell identity is missing")
    if (not _CELL_RE.fullmatch(cell_id)
            and not (cell_id.startswith("<") and cell_id.endswith(">"))):
        # A malformed live marker may carry its literal noncanonical id, but
        # only the raw-DOM relation is allowed to publish it.
        if item.get("failure_kinds") != ["unowned-live-comb-markup"]:
            raise RefereeError("audit offender cell identity is not canonical")
    page = _audit_optional_int(item["page"], f"audit offender {cell_id} page")
    if page == 0:
        raise RefereeError(f"audit offender {cell_id} page is not one-based")
    slots = _audit_optional_int(
        item["slots"], f"audit offender {cell_id} slots")
    latticed = _audit_optional_int(
        item["latticed"], f"audit offender {cell_id} latticed")
    printed = _audit_optional_int(
        item["printed"], f"audit offender {cell_id} printed")
    physical = _audit_optional_int(
        item["physical_slots"], f"audit offender {cell_id} physical slots")
    _audit_optional_int(
        item["declared_slots"], f"audit offender {cell_id} declared slots")
    occurrences = exact_nonnegative_int(
        item["emitted_occurrences"],
        f"audit offender {cell_id} emitted occurrences")
    if slots is not None and physical is not None and slots != physical:
        raise RefereeError(
            f"audit offender {cell_id} slots disagree with physical slots")
    divider_x = _audit_number_list(
        item["printed_divider_x"],
        f"audit offender {cell_id} printed dividers")
    if divider_x is None:
        raise RefereeError(
            f"audit offender {cell_id} printed dividers are missing")
    if printed is None:
        if divider_x:
            raise RefereeError(
                f"audit offender {cell_id} has dividers without a result")
    elif len(divider_x) != max(0, printed - 1):
        raise RefereeError(
            f"audit offender {cell_id} printed topology is inconsistent")
    failure_kinds = string_list(
        item["failure_kinds"],
        f"audit offender {cell_id} failure kinds",
        nonempty=True,
    )
    unknown_kinds = set(failure_kinds) - AUDIT_FAILURE_KINDS
    if unknown_kinds:
        raise RefereeError(
            f"audit offender {cell_id} has unsupported failure kinds: "
            + ", ".join(sorted(unknown_kinds)))
    if not isinstance(item["why"], str) or not item["why"]:
        raise RefereeError(f"audit offender {cell_id} has no explanation")
    if not isinstance(item["emission_state"], str) or not item["emission_state"]:
        raise RefereeError(f"audit offender {cell_id} has no emission state")

    layout_relation = item["layout_relation"]
    if layout_relation == "match":
        if printed is None or latticed is None or printed != latticed:
            raise RefereeError(
                f"audit offender {cell_id} has a false layout match")
        expected_layout_kind = None
    elif layout_relation == "mismatch":
        if printed is None or latticed is None or printed == latticed:
            raise RefereeError(
                f"audit offender {cell_id} has a false layout mismatch")
        expected_layout_kind = "layout-printed-mismatch"
    elif layout_relation == "unevaluable":
        if printed is not None:
            raise RefereeError(
                f"audit offender {cell_id} hides a measured source topology")
        expected_layout_kind = "source-topology-unevaluable"
    elif layout_relation == "duplicate-subject":
        expected_layout_kind = "duplicate-layout-subject"
    elif layout_relation == "registry-invalid":
        expected_layout_kind = None
    elif layout_relation in {
            "not-owned", "cell-binding-invalid", "inventory-invalid"}:
        expected_layout_kind = None
    else:
        raise RefereeError(
            f"audit offender {cell_id} has unsupported layout relation")
    for kind in {
            "layout-printed-mismatch", "source-topology-unevaluable",
            "duplicate-layout-subject"}:
        if ((kind in failure_kinds)
                != (kind == expected_layout_kind)):
            raise RefereeError(
                f"audit offender {cell_id} has a false {kind} relation")

    normal_subject = layout_relation in {"match", "mismatch", "unevaluable"}
    owner_certificate: dict[str, Any] | None = None
    if normal_subject or layout_relation == "duplicate-subject":
        owner_certificate = validate_audit_owner_certificate(
            item.get("source_owner_certificate"), expected_owner)
        if layout_relation == "duplicate-subject":
            if owner_certificate.get("valid") is not False:
                raise RefereeError(
                    f"audit duplicate subject {cell_id} has a valid owner "
                    "certificate")
            if "source_topology_evidence" in item:
                raise RefereeError(
                    f"audit duplicate subject {cell_id} invents source "
                    "topology evidence")
        if owner_certificate.get("valid") is False:
            if (printed is not None
                    or layout_relation not in {
                        "unevaluable", "duplicate-subject"}
                    or item.get("source_frame_geometry") is not None):
                raise RefereeError(
                    f"audit offender {cell_id} lets an invalid owner "
                    "certificate supply source topology")
            if normal_subject:
                topology = item.get("source_topology_evidence")
                if (not isinstance(topology, dict)
                        or set(topology) != {
                            "criterion", "owner_certificate"}
                        or topology.get("criterion")
                        != AUDIT_OWNER_CERTIFICATE_CRITERION
                        or topology.get("owner_certificate")
                        != owner_certificate):
                    raise RefereeError(
                        f"audit offender {cell_id} has malformed invalid-owner "
                        "topology evidence")
    elif layout_relation == "registry-invalid":
        owner_certificate = validate_audit_owner_certificate(
            item.get("source_owner_certificate"), None)
        if (owner_certificate.get("valid") is not False
                or cell_id != "<comb-owner-registry>"
                or page is not None
                or any(value is not None for value in (
                    slots, latticed, printed, physical,
                    item["declared_slots"]))
                or divider_x != []
                or occurrences != 0
                or item["emission_state"] != "not-evaluated"
                or item.get("effective_emission_state") != "not-evaluated"
                or item.get("emission_relation") != "not-evaluated"
                or failure_kinds != ["comb-owner-registry-invalid"]
                or "source_topology_evidence" in item
                or "source_frame_geometry" in item):
            raise RefereeError(
                "audit comb owner-registry offender is malformed")
    elif "source_owner_certificate" in item:
        raise RefereeError(
            f"non-owned audit offender invents owner certificate: {cell_id}")

    topology_evidence = item.get("source_topology_evidence")
    if topology_evidence is not None:
        if not isinstance(topology_evidence, dict):
            raise RefereeError(
                f"audit offender {cell_id} source topology evidence is malformed")
        nested_owner = topology_evidence.get("owner_certificate")
        if nested_owner is not None and nested_owner != owner_certificate:
            raise RefereeError(
                f"audit offender {cell_id} topology owner certificate differs")

    position_mismatch = False
    for field, (kind, outer) in AUDIT_POSITION_FIELDS.items():
        present = field in item
        if normal_subject and not present:
            raise RefereeError(
                f"audit offender {cell_id} omits {field}")
        mismatch = (
            validate_audit_position_evidence(
                field, item[field], outer=outer)
            if present else False
        )
        if (kind in failure_kinds) != mismatch:
            raise RefereeError(
                f"audit offender {cell_id} has a false {kind} relation")
        position_mismatch = position_mismatch or mismatch
    if normal_subject:
        layout_internal = item["emission_layout_position"]
        source_internal = item["emission_source_position"]
        layout_outer = item["emission_layout_outer_position"]
        source_outer = item["emission_source_outer_position"]
        layout_source_outer = item["layout_source_outer_position"]
        internal_actual = _audit_number_list(
            layout_internal["actual_internal_edges_x"],
            f"audit offender {cell_id} layout actual edges")
        source_actual = _audit_number_list(
            source_internal["actual_internal_edges_x"],
            f"audit offender {cell_id} source actual edges")
        if (internal_actual is not None and source_actual is not None
                and not same_numbers(internal_actual, source_actual)):
            raise RefereeError(
                f"audit offender {cell_id} publishes two emitted edge vectors")
        source_expected = _audit_number_list(
            source_internal["expected_internal_edges_x"],
            f"audit offender {cell_id} source expected edges")
        if (printed is None) != (source_expected is None):
            raise RefereeError(
                f"audit offender {cell_id} source divider availability is false")
        if (printed is not None and source_expected is not None
                and not same_numbers(source_expected, divider_x)):
            raise RefereeError(
                f"audit offender {cell_id} source divider evidence disagrees")
        emitted_outer_a = _audit_number_list(
            layout_outer["actual_outer_edges_x"],
            f"audit offender {cell_id} layout actual outer edges")
        emitted_outer_b = _audit_number_list(
            source_outer["actual_outer_edges_x"],
            f"audit offender {cell_id} source actual outer edges")
        if (emitted_outer_a is not None and emitted_outer_b is not None
                and not same_numbers(emitted_outer_a, emitted_outer_b)):
            raise RefereeError(
                f"audit offender {cell_id} publishes two emitted outer vectors")
        layout_expected_outer = _audit_number_list(
            layout_outer["expected_outer_edges_x"],
            f"audit offender {cell_id} layout expected outer edges")
        layout_source_actual = _audit_number_list(
            layout_source_outer["actual_outer_edges_x"],
            f"audit offender {cell_id} layout/source actual outer edges")
        if (layout_expected_outer is not None
                and layout_source_actual is not None
                and not same_numbers(
                    layout_expected_outer, layout_source_actual)):
            raise RefereeError(
                f"audit offender {cell_id} publishes two layout outer vectors")
        source_expected_outer = _audit_number_list(
            source_outer["expected_outer_edges_x"],
            f"audit offender {cell_id} source expected outer edges")
        layout_source_expected = _audit_number_list(
            layout_source_outer["expected_outer_edges_x"],
            f"audit offender {cell_id} layout/source expected outer edges")
        if (source_expected_outer is not None
                and layout_source_expected is not None
                and not same_numbers(
                    source_expected_outer, layout_source_expected)):
            raise RefereeError(
                f"audit offender {cell_id} publishes two source outer vectors")
        frame = item.get("source_frame_geometry")
        if printed is None:
            if frame is not None:
                raise RefereeError(
                    f"audit offender {cell_id} has a frame without topology")
        elif frame is not None:
            if not isinstance(frame, dict):
                raise RefereeError(
                    f"audit offender {cell_id} source frame is malformed")
            try:
                # The rails' INNER INK EDGES, which is what an outer
                # compartment is bounded by. A rail is a painted stroke and its
                # `center_x` runs down the middle of it, so a comparison
                # against the centre asks whether the taxpayer's box starts
                # half a wall inside the printed wall (F208). This referee
                # re-derives the audit's own expectation, so it has to name the
                # same quantity the audit named or every framed offender would
                # be rejected as disagreeing with itself.
                frame_edges = [
                    finite_number(
                        frame["left_rail"]["ink_x1"],
                        f"audit offender {cell_id} left source rail"),
                    finite_number(
                        frame["right_rail"]["ink_x0"],
                        f"audit offender {cell_id} right source rail"),
                ]
            except (KeyError, TypeError):
                raise RefereeError(
                    f"audit offender {cell_id} source frame is malformed")
            if (source_expected_outer is None
                    or layout_source_expected is None
                    or not same_numbers(frame_edges, source_expected_outer)
                    or not same_numbers(frame_edges, layout_source_expected)):
                raise RefereeError(
                    f"audit offender {cell_id} source rails disagree")
        elif (source_expected_outer is not None
              or layout_source_expected is not None
              or owner_certificate is None
              or owner_certificate.get("valid") is not True):
            raise RefereeError(
                f"audit offender {cell_id} has an uncertified unframed "
                "source topology")

    container_mismatch = False
    if normal_subject:
        if "emission_container_binding" not in item:
            raise RefereeError(
                f"audit offender {cell_id} omits container binding evidence")
        container = validate_audit_container_binding(
            item["emission_container_binding"])
        expected_page_kind = bool(
            occurrences == 1 and container["page_mismatch"])
        expected_rect_kind = bool(
            occurrences == 1 and container["rect_mismatch"])
        if (("emission-container-page-mismatch" in failure_kinds)
                != expected_page_kind):
            raise RefereeError(
                f"audit offender {cell_id} has a false container-page failure")
        if (("emission-container-geometry-mismatch" in failure_kinds)
                != expected_rect_kind):
            raise RefereeError(
                f"audit offender {cell_id} has a false container-rect failure")
        container_mismatch = expected_page_kind or expected_rect_kind
    elif any(
            kind in failure_kinds for kind in {
                "emission-container-page-mismatch",
                "emission-container-geometry-mismatch",
            }):
        raise RefereeError(
            f"audit offender {cell_id} has unbound container failures")

    binding_invalid = container_mismatch or position_mismatch
    physical_emission_valid = item["emission_state"] == "physical-slots"
    if normal_subject:
        if (("invalid-emission" in failure_kinds)
                != (not physical_emission_valid)):
            raise RefereeError(
                f"audit offender {cell_id} has a false invalid-emission flag")
        layout_slot_mismatch = (
            slots is not None and latticed is not None and slots != latticed)
        printed_slot_mismatch = (
            slots is not None and printed is not None and slots != printed)
        if (("emission-layout-mismatch" in failure_kinds)
                != (physical_emission_valid and layout_slot_mismatch)):
            raise RefereeError(
                f"audit offender {cell_id} has a false layout-slot relation")
        if (("emission-printed-mismatch" in failure_kinds)
                != (physical_emission_valid and printed_slot_mismatch)):
            raise RefereeError(
                f"audit offender {cell_id} has a false printed-slot relation")
        if not physical_emission_valid or binding_invalid:
            expected_emission_relation = "invalid"
        else:
            mismatched = [
                name for name, mismatch in (
                    ("layout", layout_slot_mismatch),
                    ("printed", printed_slot_mismatch),
                ) if mismatch
            ]
            expected_emission_relation = (
                "mismatch-" + "-and-".join(mismatched)
                if mismatched else "match"
            )
        if item["emission_relation"] != expected_emission_relation:
            raise RefereeError(
                f"audit offender {cell_id} has a false emission relation")
        if "effective_emission_state" in item:
            expected_state = (
                "container-binding-invalid"
                if container_mismatch else
                "slot-position-invalid"
                if position_mismatch else
                item["emission_state"]
            )
            if item["effective_emission_state"] != expected_state:
                raise RefereeError(
                    f"audit offender {cell_id} has a false effective state")
    elif layout_relation == "duplicate-subject":
        if item["emission_relation"] not in {"invalid", "unbound"}:
            raise RefereeError(
                f"audit offender {cell_id} has false duplicate binding")
    elif layout_relation == "not-owned":
        expected_kind = (
            "unowned-live-comb-markup"
            if "unowned-live-comb-markup" in failure_kinds
            else "unexpected-emitted-comb"
        )
        expected_relation = (
            "invalid" if expected_kind == "unowned-live-comb-markup"
            else "unexpected"
        )
        if (item["emission_relation"] != expected_relation
                or expected_kind not in failure_kinds):
            raise RefereeError(
                f"audit offender {cell_id} has false unowned-emission evidence")
    elif layout_relation == "cell-binding-invalid":
        if (item["emission_relation"] != "invalid"
                or "emitted-cell-binding-invalid" not in failure_kinds):
            raise RefereeError(
                f"audit offender {cell_id} has false cell-binding evidence")
    elif layout_relation == "registry-invalid":
        if (item["emission_relation"] != "not-evaluated"
                or failure_kinds != ["comb-owner-registry-invalid"]):
            raise RefereeError(
                "audit comb owner-registry relation is false")
    elif (item["emission_relation"] != "inventory-invalid"
          or "comb-inventory-mismatch" not in failure_kinds):
        raise RefereeError(
            f"audit offender {cell_id} has false inventory evidence")

    inventory_binding = bool(set(failure_kinds) & {
        "duplicate-layout-subject", "unexpected-emitted-comb",
        "emitted-cell-binding-invalid", "duplicate-emitted-cell-id",
        "missing-layout-cell-owner", "duplicate-layout-cell-owner",
        "emitted-cell-page-mismatch", "emitted-cell-geometry-mismatch",
        "unowned-live-comb-markup", "comb-inventory-mismatch",
        "comb-owner-registry-invalid",
        "emission-container-page-mismatch",
        "emission-container-geometry-mismatch",
    })
    dimensions = {
        "layout_mismatch": layout_relation == "mismatch",
        "source_unevaluable": layout_relation in {
            "unevaluable", "duplicate-subject", "inventory-invalid"},
        "emission_invalid": bool(
            not physical_emission_valid or binding_invalid),
        "emission_behind": bool(
            layout_relation == "duplicate-subject"
            or not physical_emission_valid
            or binding_invalid
            or (slots is not None and latticed is not None
                and slots != latticed)
            or "unexpected-emitted-comb" in failure_kinds
            or "unowned-live-comb-markup" in failure_kinds),
        "position_mismatch": position_mismatch,
        "inventory_binding": inventory_binding,
    }
    # Pseudo binding/inventory records do not contribute to audit.py's
    # emission-invalid summary unless they are raw live comb markup.
    if not normal_subject:
        dimensions["emission_invalid"] = bool(
            ("unowned-live-comb-markup" in failure_kinds)
            or ("unexpected-emitted-comb" in failure_kinds
                and not physical_emission_valid)
            or (layout_relation == "duplicate-subject"
                and not physical_emission_valid)
        )
        dimensions["emission_behind"] = bool(
            "unexpected-emitted-comb" in failure_kinds
            or "unowned-live-comb-markup" in failure_kinds
            or layout_relation == "duplicate-subject"
        )
    if not any(dimensions.values()):
        raise RefereeError(
            f"audit offender {cell_id} is unsupported by any failure relation")
    return {
        "cell": cell_id,
        "page": page,
        "slots": slots,
        "latticed": latticed,
        "printed": printed,
        "emitted_occurrences": occurrences,
        "layout_relation": layout_relation,
        "emission_state": item["emission_state"],
        "failure_kinds": failure_kinds,
        "source_owner_certificate": owner_certificate,
        "dimensions": dimensions,
    }


def audit_evidence(
        audit_record: dict[str, Any] | None,
        owner_binding: dict[str, Any] | None = None,
        ) -> dict[str, Any]:
    """Validate exhaustive audit publication without conflating dimensions."""
    if not audit_record:
        return {
            "assertion_valid": False,
            "complete": False,
            "reason": "no audit record",
            "errors": ["no audit record"],
            "offenders": {},
        }
    assertions = audit_record.get("assertions")
    assertion = (
        assertions.get("comb_slots_match_printed")
        if isinstance(assertions, dict) else None
    )
    if not isinstance(assertion, dict):
        return {
            "assertion_valid": False,
            "complete": False,
            "reason": "comb audit assertion is missing",
            "errors": ["comb audit assertion is missing"],
            "offenders": {},
        }
    raw_offenders = assertion.get("offenders")
    if not isinstance(raw_offenders, list):
        return {
            "assertion_valid": False,
            "complete": False,
            "reason": "audit offenders is not a list",
            "errors": ["audit offenders is not a list"],
            "offenders": {},
        }
    errors: list[str] = []
    owner_cells: dict[str, dict[str, Any]] | None = None
    if owner_binding is not None:
        if (not isinstance(owner_binding, dict)
                or set(owner_binding) != {"layout_sha256", "cells"}
                or not isinstance(owner_binding.get("layout_sha256"), str)
                or not isinstance(owner_binding.get("cells"), dict)):
            errors.append("audit owner binding context is malformed")
            owner_cells = {}
        else:
            owner_cells = owner_binding["cells"]
            for cell_id, certificate in owner_cells.items():
                try:
                    if (not isinstance(cell_id, str)
                            or certificate.get("cell_id") != cell_id
                            or certificate.get("layout_sha256")
                            != owner_binding["layout_sha256"]):
                        raise RefereeError(
                            "owner binding identity/layout SHA is false")
                    validate_audit_owner_certificate(
                        certificate, certificate)
                except (AttributeError, RefereeError) as error:
                    errors.append(
                        f"audit owner binding {cell_id!r}: {error}")
    dimensions_by_cell: dict[str, dict[str, Any]] = {}
    valid_items: list[dict[str, Any]] = []
    for index, item in enumerate(raw_offenders):
        try:
            raw_cell = item.get("cell") if isinstance(item, dict) else None
            expected_owner = (
                owner_cells.get(raw_cell)
                if owner_cells is not None and isinstance(raw_cell, str)
                else None
            )
            dimensions = audit_offender_dimensions(item, expected_owner)
        except RefereeError as error:
            errors.append(f"offender[{index}]: {error}")
            continue
        cell_id = dimensions["cell"]
        if cell_id in dimensions_by_cell:
            errors.append(f"duplicate offender cell: {cell_id}")
            continue
        dimensions_by_cell[cell_id] = dimensions
        valid_items.append(item)
    offenders = {
        item["cell"]: item for item in valid_items
    }

    try:
        expected_ids = string_list(
            assertion["expected_comb_ids"], "audit expected comb ids")
        checked_ids = string_list(
            assertion["checked_comb_ids"], "audit checked comb ids")
        emitted_ids = string_list(
            assertion["emitted_comb_ids"], "audit emitted comb ids")
        unexpected_ids = string_list(
            assertion["unexpected_emitted_comb_ids"],
            "audit unexpected emitted comb ids")
        duplicate_layout_ids = string_list(
            assertion["duplicate_layout_comb_ids"],
            "audit duplicate layout comb ids")
        duplicate_emitted_ids = string_list(
            assertion["duplicate_emitted_cell_ids"],
            "audit duplicate emitted cell ids")
        counts = {
            key: exact_nonnegative_int(
                assertion[key], f"audit {key.replace('_', ' ')}")
            for key in (
                "combs_expected", "combs_checked", "raw_live_comb_issues",
                "emitted_cell_binding_issues", "layout_mismatches",
                "layout_unevaluable", "emission_behind_layout",
                "owner_certificates_valid", "owner_certificates_invalid",
                "source_u_frame_evaluable",
                "source_certified_unframed_evaluable", "emission_invalid",
                # Z1's declared schema change. This belongs in the SAME
                # extraction as its siblings: read through `assertion[key]` so
                # an audit that omits it raises KeyError and fails closed. It
                # was first mirrored with `counts.get(..., 0)`, which silently
                # published "no reviewed subjects" for every form and made the
                # gate's three-way partition false on all 7 forms that carry
                # one -- a default that answers a question the producer never
                # actually answered.
                "decided_by_review",
            )
        }
        reviewed_subjects = assertion["decided_by_review_subjects"]
        if not isinstance(reviewed_subjects, list):
            raise RefereeError(
                "audit decided by review subjects is not a list")
    except (KeyError, RefereeError) as error:
        errors.append(str(error))
        expected_ids = []
        checked_ids = []
        emitted_ids = []
        unexpected_ids = []
        duplicate_layout_ids = []
        duplicate_emitted_ids = []
        # None, never [] -- an empty list is a claim ("this form has no
        # reviewed subjects"), and a failed parse is entitled to make none.
        # The gate refuses a non-list here as malformed source accounting.
        reviewed_subjects = None
        counts = {
            key: -1 for key in (
                "combs_expected", "combs_checked", "raw_live_comb_issues",
                "emitted_cell_binding_issues", "layout_mismatches",
                "layout_unevaluable", "emission_behind_layout",
                "owner_certificates_valid", "owner_certificates_invalid",
                "source_u_frame_evaluable",
                "source_certified_unframed_evaluable", "emission_invalid",
                "decided_by_review",
            )
        }
    if checked_ids != expected_ids:
        errors.append("audit checked IDs are not the exhaustive expected order")
    if counts["combs_expected"] != len(expected_ids):
        errors.append("audit expected count disagrees with expected IDs")
    if counts["combs_checked"] != len(checked_ids):
        errors.append("audit checked count disagrees with checked IDs")
    if owner_cells is not None and expected_ids != list(owner_cells):
        errors.append(
            "audit expected IDs differ from exact retained owner order")
    if emitted_ids != sorted(emitted_ids):
        errors.append("audit emitted IDs are not canonical sorted inventory")
    if unexpected_ids != sorted(set(emitted_ids) - set(expected_ids)):
        errors.append("audit unexpected emitted inventory is false")
    if duplicate_layout_ids != sorted(duplicate_layout_ids):
        errors.append("audit duplicate-layout IDs are not sorted")
    if duplicate_emitted_ids != sorted(duplicate_emitted_ids):
        errors.append("audit duplicate-emitted IDs are not sorted")

    holds = assertion.get("holds")
    inventory_complete = assertion.get("inventory_complete")
    if not isinstance(holds, bool):
        errors.append("audit holds flag is not boolean")
        holds = False
    if not isinstance(inventory_complete, bool):
        errors.append("audit inventory_complete flag is not boolean")
        inventory_complete = False
    count = assertion.get("offender_count", 0 if holds else None)
    published = assertion.get("offenders_published", 0 if holds else None)
    omitted = assertion.get("offenders_omitted", 0 if holds else None)
    complete_flag = assertion.get("offenders_complete", True if holds else None)
    try:
        count = exact_nonnegative_int(count, "audit offender count")
        published = exact_nonnegative_int(
            published, "audit published offender count")
        omitted = exact_nonnegative_int(
            omitted, "audit omitted offender count")
    except RefereeError as error:
        errors.append(str(error))
        count = published = omitted = -1
    if not isinstance(complete_flag, bool):
        errors.append("audit offenders_complete flag is not boolean")
        complete_flag = False
    if published != len(raw_offenders):
        errors.append("audit published count disagrees with offender list")
    if count != published + omitted:
        errors.append("audit published and omitted counts do not sum")
    if omitted != 0 or not complete_flag:
        errors.append("audit offender publication is not exhaustive")

    expected_set = set(expected_ids)
    offender_ids = set(offenders)
    for cell_id in expected_ids:
        if cell_id not in emitted_ids:
            item = offenders.get(cell_id)
            if (item is None
                    or item.get("emission_state") != "missing-emitted-cell"
                    or not set(item.get("failure_kinds") or ()) & {
                        "invalid-emission", "duplicate-layout-subject"}):
                errors.append(
                    f"audit omits missing-emission offender: {cell_id}")
    for cell_id in unexpected_ids:
        item = offenders.get(cell_id)
        if item is None or "unexpected-emitted-comb" not in (
                item.get("failure_kinds") or ()):
            errors.append(
                f"audit omits unexpected-emission offender: {cell_id}")
    derived_duplicate_layout = sorted(
        cell_id for cell_id, detail in dimensions_by_cell.items()
        if detail["layout_relation"] == "duplicate-subject")
    if duplicate_layout_ids != derived_duplicate_layout:
        errors.append("audit duplicate-layout inventory lacks exact offenders")
    raw_issue_ids = {
        cell_id for cell_id, detail in dimensions_by_cell.items()
        if "unowned-live-comb-markup" in detail["failure_kinds"]
    }
    if counts["raw_live_comb_issues"] != len(raw_issue_ids):
        errors.append("audit raw-live-comb count disagrees with offenders")
    inventory_offenders = {
        cell_id for cell_id, detail in dimensions_by_cell.items()
        if "comb-inventory-mismatch" in detail["failure_kinds"]
    }
    owner_registry_offenders = {
        cell_id for cell_id, detail in dimensions_by_cell.items()
        if "comb-owner-registry-invalid" in detail["failure_kinds"]
    }
    binding_issue_ids = set(duplicate_emitted_ids) | set(unexpected_ids)
    for cell_id, detail in dimensions_by_cell.items():
        if set(detail["failure_kinds"]) & {
                "emission-container-page-mismatch",
                "emission-container-geometry-mismatch",
                "emitted-cell-binding-invalid",
                "duplicate-emitted-cell-id",
                "missing-layout-cell-owner",
                "duplicate-layout-cell-owner",
                "emitted-cell-page-mismatch",
                "emitted-cell-geometry-mismatch",
            }:
            binding_issue_ids.add(cell_id)
    if counts["emitted_cell_binding_issues"] != len(binding_issue_ids):
        errors.append("audit cell-binding count disagrees with offenders")
    relevant_duplicate_emitted = (
        set(duplicate_emitted_ids) & (set(expected_ids) | set(emitted_ids)))
    derived_inventory_complete = not (
        unexpected_ids
        or duplicate_layout_ids
        or relevant_duplicate_emitted
        or raw_issue_ids
        or binding_issue_ids
        or inventory_offenders
        or owner_registry_offenders
    )
    if inventory_complete is not derived_inventory_complete:
        errors.append("audit inventory_complete relation is false")

    derived_counts = {
        "layout_mismatches": sum(
            detail["dimensions"]["layout_mismatch"]
            for detail in dimensions_by_cell.values()),
        "layout_unevaluable": sum(
            detail["dimensions"]["source_unevaluable"]
            for detail in dimensions_by_cell.values()),
        "emission_behind_layout": sum(
            detail["dimensions"]["emission_behind"]
            for detail in dimensions_by_cell.values()),
        "emission_invalid": sum(
            detail["dimensions"]["emission_invalid"]
            for detail in dimensions_by_cell.values()),
    }
    for key, derived in derived_counts.items():
        if counts[key] != derived:
            errors.append(
                f"audit {key} count {counts[key]} disagrees with "
                f"{derived} independent offender relations")

    if (counts["owner_certificates_valid"]
            + counts["owner_certificates_invalid"]
            != counts["combs_checked"]):
        errors.append(
            "audit owner certificate counts do not partition checked cells")
    checked_certificates = {
        cell_id: detail["source_owner_certificate"]
        for cell_id, detail in dimensions_by_cell.items()
        if cell_id in expected_set
        and isinstance(detail.get("source_owner_certificate"), dict)
    }
    published_invalid_certificates = sum(
        certificate.get("valid") is False
        for certificate in checked_certificates.values()
    )
    published_valid_certificates = sum(
        certificate.get("valid") is True
        for certificate in checked_certificates.values()
    )
    if (counts["owner_certificates_invalid"]
            != published_invalid_certificates):
        errors.append(
            "audit invalid owner certificate count disagrees with offenders")
    if (published_valid_certificates
            > counts["owner_certificates_valid"]):
        errors.append(
            "audit published valid owner certificates exceed their count")
    if set(checked_certificates) == expected_set and (
            counts["owner_certificates_valid"]
            != published_valid_certificates):
        errors.append(
            "audit complete owner certificate publication disagrees with count")
    if owner_registry_offenders and (
            published_invalid_certificates != counts["combs_checked"]
            or counts["owner_certificates_invalid"]
            != counts["combs_checked"]
            or counts["owner_certificates_valid"] != 0
            or set(checked_certificates) != expected_set):
        errors.append(
            "audit global owner-registry failure does not invalidate every "
            "checked certificate")

    checked_source_unevaluable = {
        cell_id for cell_id, detail in dimensions_by_cell.items()
        if cell_id in expected_set
        and detail["dimensions"]["source_unevaluable"]
    }
    source_evaluable = (
        counts["combs_checked"] - len(checked_source_unevaluable))
    # Z1's declared schema change, adjudicated INDEPENDENTLY here -- this is
    # the referee's own partition, not a mirror of the gate's. A reviewed
    # decision is a third source-evaluability class: the audit returns such a
    # cell to held(), so it stops being published as an offender and is not in
    # checked_source_unevaluable, yet its count came from review rather than
    # from a U-frame or an unframed certificate, so neither source term covers
    # it. Disjointness is asserted, not assumed: a cell that is both reviewed
    # and still published source-unevaluable means the registry failed to
    # clear it.
    reviewed_cells = {
        subject.get("cell") for subject in (reviewed_subjects or ())
        if isinstance(subject, dict) and isinstance(subject.get("cell"), str)
    }
    if len(reviewed_cells) != counts["decided_by_review"]:
        errors.append(
            "audit reviewed-topology subjects disagree with their count")
    if reviewed_cells - expected_set:
        errors.append(
            "audit reviewed-topology subject is not a checked cell")
    if reviewed_cells & checked_source_unevaluable:
        errors.append(
            "audit counts a reviewed cell as source unevaluable")
    if (counts["source_u_frame_evaluable"]
            + counts["source_certified_unframed_evaluable"]
            + len(reviewed_cells)
            != source_evaluable):
        errors.append(
            "audit source frame/unframed/reviewed counts do not partition "
            "evaluable checked cells")
    published_u_frame = 0
    published_certified_unframed = 0
    for cell_id, detail in dimensions_by_cell.items():
        if cell_id not in expected_set or detail["printed"] is None:
            continue
        certificate = detail.get("source_owner_certificate")
        if (not isinstance(certificate, dict)
                or certificate.get("valid") is not True):
            errors.append(
                f"audit measured source lacks valid owner certificate: {cell_id}")
            continue
        if offenders[cell_id].get("source_frame_geometry") is None:
            published_certified_unframed += 1
        else:
            published_u_frame += 1
    if published_u_frame > counts["source_u_frame_evaluable"]:
        errors.append(
            "audit published U-frame source results exceed their count")
    if (published_certified_unframed
            > counts["source_certified_unframed_evaluable"]):
        errors.append(
            "audit published certified-unframed results exceed their count")
    unsupported_canonical = sorted(
        cell_id for cell_id in offender_ids - expected_set - set(unexpected_ids)
        if _CELL_RE.fullmatch(cell_id)
        and "emitted-cell-binding-invalid"
        not in (offenders[cell_id].get("failure_kinds") or ())
        and "unowned-live-comb-markup"
        not in (offenders[cell_id].get("failure_kinds") or ())
    )
    if unsupported_canonical:
        errors.append(
            "audit publishes canonical offenders outside its inventories: "
            + ", ".join(unsupported_canonical[:8]))
    expected_holds = bool(
        count == 0
        and inventory_complete
        and all(value == 0 for value in derived_counts.values())
    )
    if holds is not expected_holds:
        errors.append("audit holds flag disagrees with independent relations")
    if audit_record.get("comb_slots_match_printed") is not holds:
        errors.append("audit top-level comb verdict disagrees with assertion")
    reason_value = assertion.get("reason")
    if not isinstance(reason_value, str) or (not holds and not reason_value):
        errors.append("audit assertion reason is malformed")

    assertion_valid = not errors
    return {
        "assertion_valid": assertion_valid,
        # Manifest/attestation binding is applied later; this field is kept
        # fail-closed until then.
        "complete": False,
        "reason": (
            "assertion publication verified; attestation not yet bound"
            if assertion_valid else "; ".join(errors)
        ),
        "errors": errors,
        "offender_count": count,
        "offenders_published": published,
        "offenders_omitted": omitted,
        "combs_expected": counts["combs_expected"],
        "combs_checked": counts["combs_checked"],
        "expected_comb_ids": expected_ids,
        "checked_comb_ids": checked_ids,
        "emitted_comb_ids": emitted_ids,
        "unexpected_emitted_comb_ids": unexpected_ids,
        "duplicate_layout_comb_ids": duplicate_layout_ids,
        "duplicate_emitted_cell_ids": duplicate_emitted_ids,
        "raw_live_comb_issues": counts["raw_live_comb_issues"],
        "emitted_cell_binding_issues": (
            counts["emitted_cell_binding_issues"]),
        "inventory_complete": inventory_complete,
        "layout_mismatches": counts["layout_mismatches"],
        "layout_unevaluable": counts["layout_unevaluable"],
        "owner_certificates_valid": counts["owner_certificates_valid"],
        "owner_certificates_invalid": counts["owner_certificates_invalid"],
        "source_u_frame_evaluable": counts["source_u_frame_evaluable"],
        "source_certified_unframed_evaluable": (
            counts["source_certified_unframed_evaluable"]),
        "emission_behind_layout": counts["emission_behind_layout"],
        "emission_invalid": counts["emission_invalid"],
        # Z1's declared schema change: mirrored from the audit's own
        # publication so the gate can compare the two key for key. PROVENANCE
        # only -- the referee still adjudicates a reviewed subject exactly as
        # a measured one, and never treats "decided by review" as agreement.
        "decided_by_review": counts["decided_by_review"],
        "decided_by_review_subjects": (
            list(reviewed_subjects) if reviewed_subjects is not None
            else None),
        "offender_dimensions": dimensions_by_cell,
        "offenders": offenders,
        "holds": holds,
    }


class AuditRenderDependencyScanner(html.parser.HTMLParser):
    """Independent local-resource inventory for the frozen audit manifest."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.references: list[tuple[str, str]] = []
        self.errors: list[str] = []
        self.style_depth = 0

    def _add(self, value: str | None, kind: str) -> None:
        if value is not None and value.strip():
            self.references.append((value.strip(), kind))

    def _srcset(self, value: str | None, kind: str) -> None:
        if value:
            for candidate in value.split(","):
                self._add(candidate.strip().split()[0], kind)

    def handle_starttag(
            self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key.lower(): value for key, value in attrs}
        lowered = tag.lower()
        if lowered == "style":
            self.style_depth += 1
        self.references.extend(
            (url, "inline-style")
            for url in _audit_css_urls(values.get("style") or "")
        )
        if lowered == "link":
            rel = {
                item.lower() for item in (values.get("rel") or "").split()}
            if rel & {
                    "stylesheet", "preload", "modulepreload",
                    "icon", "manifest"}:
                self._add(values.get("href"), "link")
        elif lowered in {"img", "source"}:
            self._add(values.get("src"), lowered)
            self._srcset(values.get("srcset"), f"{lowered}-srcset")
        elif lowered in {
                "video", "audio", "track", "embed", "iframe"}:
            self._add(values.get("src"), lowered)
            if lowered == "video":
                self._add(values.get("poster"), "video-poster")
        elif lowered == "object":
            self._add(values.get("data"), "object")
        elif lowered == "input" and (
                values.get("type") or "").lower() == "image":
            self._add(values.get("src"), "input-image")
        elif lowered == "image":
            self._add(
                values.get("href") or values.get("xlink:href"), "svg-image")

    def handle_startendtag(
            self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag.lower() == "style" and self.style_depth:
            self.style_depth -= 1

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "style" and self.style_depth:
            self.style_depth -= 1

    def handle_data(self, data: str) -> None:
        if self.style_depth:
            self.references.extend(
                (url, "style-block") for url in _audit_css_urls(data))


_AUDIT_CSS_URL_RE = re.compile(
    r"""url\(\s*(?P<quote>["']?)(?P<url>.*?)(?P=quote)\s*\)""",
    re.IGNORECASE,
)
_AUDIT_CSS_IMPORT_RE = re.compile(
    r"""@import\s+(?:url\(\s*)?(?P<quote>["'])(?P<url>.*?)(?P=quote)""",
    re.IGNORECASE,
)


def _audit_css_urls(css: str) -> list[str]:
    return [
        *(match.group("url") for match in _AUDIT_CSS_IMPORT_RE.finditer(css)),
        *(match.group("url") for match in _AUDIT_CSS_URL_RE.finditer(css)),
    ]


def _audit_logical_resource(reference: str, base: str) -> str | None:
    parsed = urllib.parse.urlsplit(reference.strip())
    if parsed.scheme.lower() == "data":
        return None
    if (parsed.scheme or parsed.netloc or reference.startswith("//")
            or parsed.path.startswith("/") or parsed.query):
        raise RefereeError(
            f"external, absolute, or query-bearing render resource: {reference}")
    if not parsed.path:
        return None
    decoded = urllib.parse.unquote(parsed.path)
    if ("\\" in decoded
            or any(ord(character) < 32 or ord(character) == 127
                   for character in decoded)):
        raise RefereeError(f"invalid render resource path: {reference}")
    logical = posixpath.normpath(
        posixpath.join(posixpath.dirname(base), decoded))
    if (logical in {"", ".", ".."} or logical.startswith("../")
            or pathlib.PurePosixPath(logical).is_absolute()):
        raise RefereeError(f"render resource escapes snapshot: {reference}")
    return logical


def audit_render_dependencies(
        html_payload: bytes,
        entrypoint: str,
        html_dir: pathlib.Path,
        ) -> tuple[list[dict[str, Any]], list[str]]:
    try:
        text = html_payload.decode("utf-8")
    except UnicodeDecodeError as error:
        return [], [f"HTML is not UTF-8: {error}"]
    scanner = AuditRenderDependencyScanner()
    scanner.feed(text)
    scanner.close()
    errors = list(scanner.errors)
    pending = [
        (reference, entrypoint, kind)
        for reference, kind in scanner.references
    ]
    root = html_dir.resolve()
    metadata: dict[str, dict[str, Any]] = {}
    payloads: dict[str, bytes] = {}
    visited_css: set[str] = set()
    while pending:
        reference, referrer, kind = pending.pop(0)
        try:
            logical = _audit_logical_resource(reference, referrer)
        except RefereeError as error:
            errors.append(f"{referrer}: {error}")
            continue
        if logical is None:
            continue
        item = metadata.setdefault(logical, {
            "path": logical,
            "mime_type": None,
            "present": False,
            "bytes": None,
            "sha256": None,
            "kinds": set(),
            "referrers": set(),
        })
        item["kinds"].add(kind)
        item["referrers"].add(referrer)
        if logical in payloads:
            continue
        candidate = root.joinpath(*pathlib.PurePosixPath(logical).parts)
        try:
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(root)
            if resolved != candidate or not resolved.is_file():
                raise RefereeError("symlinked or non-file dependency")
            payload = resolved.read_bytes()
        except (OSError, ValueError, RefereeError) as error:
            errors.append(
                f"{referrer}: unresolved render dependency "
                f"{reference!r} ({error})")
            continue
        payloads[logical] = payload
        mime_type = mimetypes.guess_type(logical)[0]
        if mime_type is None:
            errors.append(f"{logical}: unknown render dependency MIME type")
            continue
        item.update({
            "mime_type": mime_type,
            "present": True,
            "bytes": len(payload),
            "sha256": sha256_bytes(payload),
        })
        if logical.lower().endswith(".css") and logical not in visited_css:
            visited_css.add(logical)
            try:
                css = payload.decode("utf-8")
            except UnicodeDecodeError as error:
                errors.append(f"{logical}: CSS is not UTF-8 ({error})")
                continue
            pending.extend(
                (nested, logical, "css")
                for nested in _audit_css_urls(css)
            )
    entries = [
        {
            **{
                key: value for key, value in item.items()
                if key not in {"kinds", "referrers"}
            },
            "kinds": sorted(item["kinds"]),
            "referrers": sorted(item["referrers"]),
        }
        for _logical, item in sorted(metadata.items())
    ]
    return entries, sorted(set(errors))


APPLICATION_PACKAGE_NAMES = ("fitz", "pymupdf")
ROUNDTRIP_PACKAGE_NAME = "playwright"
NATIVE_LIBRARY_SUFFIXES = (".dylib", ".so", ".dll", ".pyd")
APPLICATION_CLOSURE_SCOPE = (
    "interpreter-binaries-and-application-package-trees-v1")
TREE_CLOSURE_ALGORITHM = "sha256(canonical-json(path,type,bytes,digest))"
APPLICATION_CLOSURE_KEYS = {
    "scope", "algorithm", "bytecode_caches_excluded", "exclusion_reason",
    "packages", "modules", "native_libraries", "unbound_modules",
    "validated_before_after", "complete",
}
TREE_MANIFEST_KEYS = {
    "logical_root", "algorithm", "files", "symlinks", "bytes", "tree_sha256",
}
# What this referee still does not rehash, republished on every binding so a
# green report never reads as a claim about the whole host.  The audit's own
# `declared_out_of_scope` says the same thing from the producer's side; both
# are printed, neither is derived from the other.
REFEREE_HOST_SCOPE_BOUNDARIES = (
    "the Python standard library and the interpreter's own dynamic libraries "
    "are outside the application closure this referee rehashes",
    "operating-system shared libraries, font services and other host "
    "services loaded by Python, PyMuPDF and Chromium are host trusted "
    "computing base and are rehashed by nobody",
    "bytecode caches inside the application package trees are excluded from "
    "the closure on both sides, matching the gate's approved-dependency "
    "materialisation, which also runs the audit with bytecode writing "
    "disabled and a redirected cache prefix",
)


def _is_bytecode_cache(logical: str) -> bool:
    return (
        "__pycache__" in pathlib.PurePosixPath(logical).parts
        or logical.endswith(".pyc")
    )


def approved_package_roots() -> tuple[pathlib.Path, ...]:
    """Package roots derived from this interpreter alone.

    Deliberately independent of the audit: nothing here reads a path the
    audit published, an environment variable, or `sys.path`.  The referee runs
    under `-I -S`, so the installed package directories are not even importable
    from it; they are computed from the interpreter's own configuration, which
    is what makes the rehash below a second opinion instead of an echo.
    """
    candidates: list[str | None] = [
        sysconfig.get_path("purelib"),
        sysconfig.get_path("platlib"),
    ]
    schemes = set(sysconfig.get_scheme_names())
    for scheme in ("osx_framework_user", "posix_user", "nt_user"):
        if scheme not in schemes:
            continue
        candidates.extend((
            sysconfig.get_path("purelib", scheme=scheme),
            sysconfig.get_path("platlib", scheme=scheme),
        ))
    roots: list[pathlib.Path] = []
    for value in candidates:
        if not value:
            continue
        path = pathlib.Path(value).resolve()
        if path.is_dir() and path not in roots:
            roots.append(path)
    return tuple(roots)


def resolve_package_root(name: str) -> pathlib.Path:
    spec = importlib.machinery.PathFinder.find_spec(
        name, [str(root) for root in approved_package_roots()])
    locations = list(getattr(spec, "submodule_search_locations", None) or ())
    if spec is None or len(locations) != 1:
        raise RefereeError(
            f"runtime package has no single independently resolved root: "
            f"{name}")
    return pathlib.Path(locations[0]).resolve(strict=True)


def snapshot_tree(root: pathlib.Path) -> list[list[Any]]:
    """Every file and symlink under ``root``, hashed, canonically ordered."""
    entries: list[list[Any]] = []
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        logical = path.relative_to(root).as_posix()
        if _is_bytecode_cache(logical):
            continue
        if path.is_symlink():
            target = os.readlink(path)
            try:
                path.resolve(strict=True).relative_to(root)
            except (FileNotFoundError, ValueError) as exc:
                raise RefereeError(
                    f"runtime closure symlink escapes root: {logical}"
                ) from exc
            entries.append([logical, "symlink", None, target])
        elif path.is_file():
            entries.append([
                logical, "file", path.stat().st_size, sha256_file(path)])
    return entries


def tree_manifest(logical_root: str, entries: Sequence[Any]) -> dict[str, Any]:
    canonical = json.dumps(
        entries, separators=(",", ":"), ensure_ascii=True)
    return {
        "logical_root": logical_root,
        "algorithm": TREE_CLOSURE_ALGORITHM,
        "files": sum(1 for item in entries if item[1] == "file"),
        "symlinks": sum(1 for item in entries if item[1] == "symlink"),
        "bytes": sum(int(item[2] or 0) for item in entries if item[1] == "file"),
        "tree_sha256": sha256_bytes(canonical.encode("ascii")),
    }


@dataclasses.dataclass(frozen=True)
class IndependentClosure:
    """This referee's own answer to "what is in the runtime closure"."""

    manifests: tuple[dict[str, Any], ...]
    files: dict[str, tuple[int, str]]
    native_libraries: tuple[dict[str, Any], ...]
    executable: tuple[int, str]
    runtime_library: tuple[int, str] | None
    roots: tuple[pathlib.Path, ...]
    error: str | None = None


def _derive_independent_closure(
        names: Sequence[str]) -> IndependentClosure:
    manifests: list[dict[str, Any]] = []
    files: dict[str, tuple[int, str]] = {}
    roots: list[pathlib.Path] = []
    for name in names:
        root = resolve_package_root(name)
        entries = snapshot_tree(root)
        manifests.append(tree_manifest(name, entries))
        roots.append(root)
        for logical, kind, size, digest in entries:
            if kind == "file":
                files[f"{name}/{logical}"] = (int(size), digest)
    native = tuple(
        {"file": logical, "bytes": size, "sha256": digest}
        for logical, (size, digest) in sorted(files.items())
        if logical.endswith(NATIVE_LIBRARY_SUFFIXES)
    )
    python_path = pathlib.Path(sys.executable).resolve()
    library = sysconfig.get_config_var("LDLIBRARY")
    library_dir = sysconfig.get_config_var("LIBDIR")
    runtime_library: tuple[int, str] | None = None
    if library and library_dir:
        candidate = pathlib.Path(library_dir) / str(library)
        if candidate.is_file():
            resolved = candidate.resolve()
            runtime_library = (
                resolved.stat().st_size, sha256_file(resolved))
    return IndependentClosure(
        manifests=tuple(manifests),
        files=files,
        native_libraries=native,
        executable=(python_path.stat().st_size, sha256_file(python_path)),
        runtime_library=runtime_library,
        roots=tuple(roots),
    )


def _failed_closure(error: str) -> IndependentClosure:
    return IndependentClosure(
        manifests=(), files={}, native_libraries=(),
        executable=(0, ""), runtime_library=None, roots=(), error=error)


_APPLICATION_CLOSURE: IndependentClosure | None = None
_ROUNDTRIP_CLOSURE: IndependentClosure | None = None


def independent_application_closure() -> IndependentClosure:
    """Rehash the fitz/pymupdf trees once per run, from the installed package.

    A failure to derive it is recorded and returned rather than raised: a
    closure that cannot be checked must make every binding that depends on it
    fail, not make one form explode while the rest look green.
    """
    global _APPLICATION_CLOSURE
    if _APPLICATION_CLOSURE is None:
        try:
            _APPLICATION_CLOSURE = _derive_independent_closure(
                APPLICATION_PACKAGE_NAMES)
        except (OSError, RefereeError, ValueError) as error:
            _APPLICATION_CLOSURE = _failed_closure(
                f"{type(error).__name__}: {error}")
    return _APPLICATION_CLOSURE


def independent_roundtrip_closure() -> IndependentClosure:
    global _ROUNDTRIP_CLOSURE
    if _ROUNDTRIP_CLOSURE is None:
        try:
            _ROUNDTRIP_CLOSURE = _derive_independent_closure(
                (ROUNDTRIP_PACKAGE_NAME,))
        except (OSError, RefereeError, ValueError) as error:
            _ROUNDTRIP_CLOSURE = _failed_closure(
                f"{type(error).__name__}: {error}")
    return _ROUNDTRIP_CLOSURE


def revalidate_independent_closures() -> list[str]:
    """Re-derive both closures and report any byte that moved during the run."""
    errors: list[str] = []
    for label, cached, names in (
        ("application", _APPLICATION_CLOSURE, APPLICATION_PACKAGE_NAMES),
        ("roundtrip", _ROUNDTRIP_CLOSURE, (ROUNDTRIP_PACKAGE_NAME,)),
    ):
        if cached is None:
            continue
        try:
            fresh = _derive_independent_closure(names)
        except (OSError, RefereeError, ValueError) as error:
            errors.append(
                f"{label} runtime closure could not be re-derived: "
                f"{type(error).__name__}: {error}")
            continue
        if cached.error is not None:
            errors.append(
                f"{label} runtime closure was never derived: {cached.error}")
            continue
        if (fresh.manifests != cached.manifests
                or fresh.files != cached.files
                or fresh.executable != cached.executable
                or fresh.runtime_library != cached.runtime_library):
            errors.append(
                f"{label} runtime closure changed during the referee run")
    return errors


def verify_published_closure(
        published: Any,
        members: Sequence[tuple[str, int, str]],
        ) -> tuple[list[str], bool]:
    """Rehash every named module and native dependency, not just Python.

    `members` is the audit's loaded-application inventory, already parsed.
    Everything the audit named is resolved AGAIN from the installed package
    and compared byte for byte, and the package trees themselves are re-walked
    so that a member the audit failed to name still has to be accounted for by
    a tree digest.  Nothing here believes a path, a count or a digest the audit
    published.
    """
    errors: list[str] = []
    closure = independent_application_closure()
    if closure.error is not None:
        return (
            [f"referee could not derive the application closure: "
             f"{closure.error}"],
            False,
        )
    if not isinstance(published, dict) or set(
            published) != APPLICATION_CLOSURE_KEYS:
        return ["audit application closure schema is unsupported"], False
    if (published["scope"] != APPLICATION_CLOSURE_SCOPE
            or published["algorithm"] != TREE_CLOSURE_ALGORITHM
            or published["bytecode_caches_excluded"] is not True
            or not isinstance(published["exclusion_reason"], str)
            or not published["exclusion_reason"]
            or published["validated_before_after"] is not True):
        errors.append("audit application closure declaration is malformed")
    if published["packages"] != [
            dict(manifest) for manifest in closure.manifests]:
        errors.append(
            "audit application package trees disagree with the referee's "
            "own rehash")
    if published["native_libraries"] != [
            dict(item) for item in closure.native_libraries]:
        errors.append(
            "audit native library inventory disagrees with the referee's "
            "own rehash")
    modules = published["modules"]
    module_names: list[str] = []
    if not isinstance(modules, list):
        errors.append("audit application module inventory is not a list")
    else:
        for index, item in enumerate(modules):
            if (not isinstance(item, dict)
                    or set(item) != {"module", "file", "bytes", "sha256"}
                    or not isinstance(item.get("module"), str)
                    or not item["module"]
                    or not isinstance(item.get("file"), str)):
                errors.append(
                    f"audit application module[{index}] is malformed")
                continue
            module_names.append(item["module"])
            observed = closure.files.get(item["file"])
            if observed is None:
                errors.append(
                    f"audit application module is not in the referee's "
                    f"rehashed tree: {item['file']}")
                continue
            if (item["bytes"], item["sha256"]) != observed:
                errors.append(
                    f"audit application module bytes disagree with the "
                    f"referee's rehash: {item['file']}")
        published_files = [
            item["file"] for item in modules
            if isinstance(item, dict) and isinstance(item.get("file"), str)
        ]
        if published_files != sorted(published_files):
            errors.append("audit application modules are not canonical ordered")
    unbound = published["unbound_modules"]
    if (not isinstance(unbound, list)
            or not all(isinstance(item, str) and item for item in unbound)
            or unbound != sorted(unbound)):
        errors.append("audit unbound-module inventory is malformed")
        unbound = []
    # Every loaded application module must be accounted for exactly once,
    # either inside a rehashed package tree or on the published unbound list.
    # This is the relation that stops an inconvenient module being dropped.
    member_modules = {
        logical[len("module/"):]: (size, digest)
        for logical, size, digest in members
        if logical.startswith("module/")
    }
    accounted = set(module_names) | {
        item[len("module/"):] for item in unbound
        if item.startswith("module/")
    }
    if accounted != set(member_modules) or len(module_names) != len(
            set(module_names)):
        errors.append(
            "audit application closure does not account for exactly the "
            "loaded application modules")
    if isinstance(modules, list):
        for item in modules:
            if not isinstance(item, dict) or not isinstance(
                    item.get("module"), str):
                continue
            member = member_modules.get(item["module"])
            if member is not None and member != (
                    item.get("bytes"), item.get("sha256")):
                errors.append(
                    f"audit module inventory and loaded-file inventory "
                    f"disagree: {item['module']}")
    if published["complete"] is not (not unbound):
        errors.append("audit application closure completeness relation is false")
    # Interpreter binaries: named or absent, but never taken on trust.
    executable = [item for item in members if item[0] == "python/executable"]
    if len(executable) != 1 or executable[0][1:] != closure.executable:
        errors.append("audit runtime Python executable bytes are stale")
    library = [item for item in members if item[0] == "python/runtime-library"]
    if closure.runtime_library is None:
        if library:
            errors.append(
                "audit names a Python runtime library the referee cannot "
                "resolve")
    elif len(library) != 1 or library[0][1:] != closure.runtime_library:
        errors.append("audit runtime Python library bytes are stale")
    unknown = sorted(
        item[0] for item in members
        if item[0] not in {"python/executable", "python/runtime-library"}
        and not item[0].startswith("module/")
    )
    if unknown:
        errors.append(
            "audit runtime names members outside the attested closure: "
            + ", ".join(unknown[:8]))
    attested = not errors and published["complete"] is True
    return errors, attested


def verify_published_roundtrip_closure(
        dependency_closure: Any, chromium: Any) -> tuple[list[str], bool]:
    """Rehash the Playwright package tree and the exact Chromium binary."""
    errors: list[str] = []
    closure = independent_roundtrip_closure()
    if closure.error is not None:
        return (
            [f"referee could not derive the Playwright closure: "
             f"{closure.error}"],
            False,
        )
    expected = dict(closure.manifests[0]) if closure.manifests else {}
    if dependency_closure != expected:
        errors.append(
            "audit Playwright dependency closure disagrees with the "
            "referee's own rehash")
    if not isinstance(chromium, dict) or not isinstance(
            chromium.get("file"), str):
        errors.append("audit Chromium identity cannot be rehashed")
        return errors, False
    observed = closure.files.get(chromium["file"])
    if observed is None:
        errors.append(
            "audit Chromium executable is not inside the referee's rehashed "
            "Playwright tree")
    elif (chromium.get("bytes"), chromium.get("sha256")) != observed:
        errors.append(
            "audit Chromium executable bytes disagree with the referee's "
            "rehash")
    return errors, not errors


def validate_audit_runtime(runtime: Any) -> tuple[list[str], list[str], bool]:
    """Schema errors, independent-rehash errors, and the attestation verdict."""
    errors: list[str] = []
    if not isinstance(runtime, dict) or set(runtime) != {
        "python", "pymupdf", "loaded_application_files", "application_closure",
        "stdlib_and_system_shared_libraries_bound",
        "scope_complete", "incomplete_reason",
    }:
        return ["audit base runtime manifest schema is unsupported"], [], False
    python = runtime["python"]
    if not isinstance(python, dict) or set(python) != {
            "implementation", "version", "cache_tag"}:
        errors.append("audit Python runtime identity is malformed")
    elif (python["implementation"] != platform.python_implementation()
          or python["version"] != platform.python_version()
          or python["cache_tag"] != sys.implementation.cache_tag):
        errors.append("audit Python runtime differs from referee runtime")
    pymupdf = runtime["pymupdf"]
    if (not isinstance(pymupdf, dict)
            or set(pymupdf) != {"package_version", "version_bind"}
            or not all(isinstance(value, str) and value
                       for value in pymupdf.values())
            or pymupdf["package_version"] != pymupdf["version_bind"]):
        errors.append("audit PyMuPDF identity is malformed")
    loaded = runtime["loaded_application_files"]
    parsed: list[tuple[str, int, str]] = []
    inventory_valid = True
    if not isinstance(loaded, dict) or set(loaded) != {
            "algorithm", "files", "bytes", "tree_sha256", "members",
            "validated_before_after"}:
        errors.append("audit loaded-application manifest schema is malformed")
        inventory_valid = False
    else:
        members = loaded["members"]
        if not isinstance(members, list):
            errors.append("audit loaded-application members are not a list")
        else:
            for index, member in enumerate(members):
                if (not isinstance(member, dict)
                        or set(member) != {"file", "bytes", "sha256"}
                        or not isinstance(member.get("file"), str)
                        or not member["file"]):
                    errors.append(
                        f"audit runtime member[{index}] is malformed")
                    continue
                try:
                    size = exact_nonnegative_int(
                        member["bytes"], f"audit runtime member[{index}] bytes")
                except RefereeError as error:
                    errors.append(str(error))
                    continue
                digest = member["sha256"]
                if (not isinstance(digest, str)
                        or re.fullmatch(r"[0-9a-f]{64}", digest) is None):
                    errors.append(
                        f"audit runtime member[{index}] hash is malformed")
                    continue
                parsed.append((member["file"], size, digest))
        if len({item[0] for item in parsed}) != len(parsed):
            errors.append("audit runtime members contain duplicate identities")
            inventory_valid = False
        if parsed != sorted(parsed, key=lambda item: item[0]):
            errors.append("audit runtime members are not canonical ordered")
        canonical = json.dumps(parsed, separators=(",", ":"))
        if loaded.get("algorithm") != (
                "sha256(canonical-json(logical-file,bytes,sha256))"):
            errors.append("audit runtime digest algorithm is unsupported")
        if loaded.get("files") != len(parsed):
            errors.append("audit runtime member count is false")
        if loaded.get("bytes") != sum(item[1] for item in parsed):
            errors.append("audit runtime byte total is false")
        if loaded.get("tree_sha256") != sha256_bytes(
                canonical.encode("ascii")):
            errors.append("audit runtime tree digest is false")
        if loaded.get("validated_before_after") is not True:
            errors.append("audit runtime was not validated before and after")
    if runtime["stdlib_and_system_shared_libraries_bound"] is not False:
        errors.append("audit base runtime overclaims system-library binding")
    if runtime["scope_complete"] is not False:
        errors.append("audit base runtime overclaims complete scope")
    if (not isinstance(runtime["incomplete_reason"], str)
            or not runtime["incomplete_reason"]):
        errors.append("audit base runtime lacks its incomplete-scope reason")
    # The independent rehash. Every named module and native dependency is
    # resolved again from the installed package and hashed here; the Python
    # executable is one member of that inventory rather than the only one it
    # was.  A malformed inventory cannot be rehashed at all, and that is a
    # failure to attest, never an attestation.
    if not inventory_valid:
        return (
            errors,
            ["audit runtime inventory could not be independently rehashed"],
            False,
        )
    closure_errors, attested = verify_published_closure(
        runtime["application_closure"], parsed)
    return errors, closure_errors, bool(attested and not errors)


def validate_audit_roundtrip(
        audit_record: dict[str, Any],
        entrypoint: str,
        dependency_paths: Sequence[str],
        ) -> tuple[bool | None, list[str], bool]:
    """Roundtrip scope, errors, and whether the referee rehashed its closure."""
    errors: list[str] = []
    if audit_record.get("roundtrip") == "skipped":
        if any(key in audit_record for key in (
                "roundtrip_runtime", "render_requests", "candidate_pdf")):
            errors.append("skipped audit carries partial roundtrip evidence")
        return None, errors, False
    runtime = audit_record.get("roundtrip_runtime")
    requests = audit_record.get("render_requests")
    candidate = audit_record.get("candidate_pdf")
    if not all(isinstance(value, dict)
               for value in (runtime, requests, candidate)):
        return None, ["audit roundtrip evidence is missing or partial"], False
    required_runtime = {
        "mode", "playwright_package_version", "dependency_closure",
        "chromium", "same_resolution_session_used_for_render",
        "dependency_closure_validated_before_after",
        "system_shared_libraries_bound", "native_host_environment_bound",
        "scope", "scope_complete", "incomplete_reason",
        "live_browser_version", "explicit_executable_path_used",
        "launch_args", "service_workers", "browser_context_offline",
        "websocket_policy", "request_policy",
        "playwright_operation_timeout_ms", "hard_deadline_seconds",
        "hard_deadline_enforced_by", "deadline_cleanup_policy",
    }
    if set(runtime) != required_runtime:
        errors.append("audit roundtrip runtime schema is unsupported")
    else:
        deadline_value = runtime["hard_deadline_seconds"]
        deadline = (
            float(deadline_value)
            if (not isinstance(deadline_value, bool)
                and isinstance(deadline_value, (int, float))
                and math.isfinite(float(deadline_value)))
            else None
        )
        live_browser_version = runtime["live_browser_version"]
        if (not isinstance(runtime["playwright_package_version"], str)
                or not runtime["playwright_package_version"]
                or not isinstance(live_browser_version, str)
                or not live_browser_version
                or runtime["mode"] != "playwright-exact-executable"
                or runtime["same_resolution_session_used_for_render"] is not True
                or runtime[
                    "dependency_closure_validated_before_after"] is not True
                or runtime["explicit_executable_path_used"] is not True
                or runtime["browser_context_offline"] is not True
                or runtime["service_workers"] != "block"
                or runtime["websocket_policy"]
                != "record-and-leave-unconnected"
                or runtime["request_policy"] != "formgen-snapshot-only-v1"
                or deadline != 60.0
                or not isinstance(
                    runtime["playwright_operation_timeout_ms"], int)
                or isinstance(
                    runtime["playwright_operation_timeout_ms"], bool)
                or runtime["playwright_operation_timeout_ms"] != 120000
                or runtime["hard_deadline_enforced_by"]
                != "isolated-render-worker-process-v1"
                or runtime["deadline_cleanup_policy"]
                != "kill-worker-and-chromium-process-group"):
            errors.append("audit roundtrip execution binding is malformed")
        if (runtime["system_shared_libraries_bound"] is not False
                or runtime["native_host_environment_bound"] is not False
                or runtime["scope"] != AUDIT_ROUNDTRIP_SCOPE
                or runtime["scope_complete"] is not False
                or not isinstance(runtime["incomplete_reason"], str)
                or not runtime["incomplete_reason"]):
            errors.append("audit roundtrip runtime overclaims its scope")
        if runtime["launch_args"] != AUDIT_ROUNDTRIP_LAUNCH_ARGS:
            errors.append("audit roundtrip launch arguments are not exact")
        closure = runtime["dependency_closure"]
        if (not isinstance(closure, dict)
                or set(closure) != {
                    "logical_root", "algorithm", "files", "symlinks",
                    "bytes", "tree_sha256"}
                or closure.get("logical_root") != "playwright"
                or closure.get("algorithm") != (
                    "sha256(canonical-json(path,type,bytes,digest))")
                or not all(
                    isinstance(closure.get(key), int)
                    and not isinstance(closure.get(key), bool)
                    and closure[key] >= 0
                    for key in ("files", "symlinks", "bytes"))
                or not isinstance(closure.get("tree_sha256"), str)
                or re.fullmatch(
                    r"[0-9a-f]{64}", closure["tree_sha256"]) is None):
            errors.append("audit roundtrip dependency closure is malformed")
        chromium = runtime["chromium"]
        chromium_file = (
            chromium.get("file") if isinstance(chromium, dict) else None)
        chromium_file_canonical = bool(
            isinstance(chromium_file, str)
            and chromium_file.startswith("playwright/")
            and posixpath.normpath(chromium_file) == chromium_file
            and ".." not in pathlib.PurePosixPath(chromium_file).parts
        )
        if (not isinstance(chromium, dict)
                or set(chromium) != {
                    "file", "bytes", "sha256", "version_output"}
                or not chromium_file_canonical
                or not isinstance(chromium.get("bytes"), int)
                or isinstance(chromium.get("bytes"), bool)
                or chromium["bytes"] <= 0
                or not isinstance(chromium.get("sha256"), str)
                or re.fullmatch(
                    r"[0-9a-f]{64}", chromium["sha256"]) is None
                or not isinstance(chromium.get("version_output"), str)
                or not chromium["version_output"]
                or not isinstance(live_browser_version, str)
                or live_browser_version
                not in chromium["version_output"]):
            errors.append("audit roundtrip Chromium identity is malformed")
    if set(requests) != {
            "policy", "synthetic_origin", "fulfilled", "fulfilled_requests",
            "blocked", "blocked_requests", "blocked_websockets",
            "all_requests_from_retained_closure"}:
        errors.append("audit roundtrip request manifest is unsupported")
    else:
        fulfilled = requests["fulfilled"]
        blocked = requests["blocked"]
        websockets = requests["blocked_websockets"]
        retained_paths_valid = bool(
            isinstance(entrypoint, str)
            and entrypoint
            and isinstance(dependency_paths, Sequence)
            and all(isinstance(item, str) and item
                    for item in dependency_paths)
            and list(dependency_paths) == sorted(dependency_paths)
            and len(dependency_paths) == len(set(dependency_paths))
            and entrypoint not in dependency_paths
        )
        retained_paths = (
            {entrypoint, *dependency_paths} if retained_paths_valid else set())
        fulfilled_valid = bool(
            isinstance(fulfilled, list)
            and fulfilled
            and all(isinstance(item, str) and item for item in fulfilled)
        )
        fulfilled_exact = bool(
            fulfilled_valid
            and entrypoint in fulfilled
            and fulfilled == sorted(fulfilled)
            and len(fulfilled) == len(set(fulfilled))
            and set(fulfilled) <= retained_paths
        )
        fulfilled_count_exact = bool(
            isinstance(requests["fulfilled_requests"], int)
            and not isinstance(requests["fulfilled_requests"], bool)
            and requests["fulfilled_requests"] == (
                len(fulfilled) if fulfilled_valid else -1)
        )
        blocked_http_empty = bool(
            isinstance(blocked, list)
            and blocked == []
            and isinstance(requests["blocked_requests"], int)
            and not isinstance(requests["blocked_requests"], bool)
            and requests["blocked_requests"] == 0
        )
        blocked_websockets_empty = bool(
            isinstance(websockets, list) and websockets == [])
        derived_retained_closure = bool(
            fulfilled_exact
            and fulfilled_count_exact
            and blocked_http_empty
            and blocked_websockets_empty
        )
        if (requests["policy"] != "formgen-snapshot-only-v1"
                or requests["synthetic_origin"] != "https://formgen.invalid"
                or not retained_paths_valid
                or not derived_retained_closure
                or requests["all_requests_from_retained_closure"]
                is not derived_retained_closure):
            errors.append("audit roundtrip request closure is false")
    required_candidate = {
        "bytes", "sha256", "retained_exact_bytes",
        "chromium_returned_in_memory", "normalization", "materialization",
        "expected_sha256_passed_to_extractor",
        "validated_before_after_extraction", "candidate_ir_sha256",
        "candidate_ir_digest_scope",
    }
    if set(candidate) != required_candidate:
        errors.append("audit candidate PDF manifest is unsupported")
    else:
        if (not isinstance(candidate["bytes"], int)
                or isinstance(candidate["bytes"], bool)
                or candidate["bytes"] <= 0
                or not isinstance(candidate["sha256"], str)
                or re.fullmatch(r"[0-9a-f]{64}", candidate["sha256"]) is None
                or not isinstance(candidate["candidate_ir_sha256"], str)
                or re.fullmatch(
                    r"[0-9a-f]{64}", candidate["candidate_ir_sha256"]) is None
                or candidate["retained_exact_bytes"] is not True
                or candidate["chromium_returned_in_memory"] is not True
                or candidate[
                    "expected_sha256_passed_to_extractor"] is not True
                or candidate[
                    "validated_before_after_extraction"] is not True
                or candidate["materialization"]
                != AUDIT_CANDIDATE_MATERIALIZATION
                or candidate["candidate_ir_digest_scope"]
                != "source-and-generator-removed"):
            errors.append("audit candidate PDF provenance is malformed")
        normalization = candidate["normalization"]
        if (not isinstance(normalization, dict)
                or set(normalization) != {
                    "algorithm", "fields_normalized", "replacement",
                    "xref_offsets_preserved"}
                or normalization["algorithm"]
                != "fixed-width-creation-modification-date-v1"
                or not isinstance(normalization["fields_normalized"], int)
                or isinstance(normalization["fields_normalized"], bool)
                or normalization["fields_normalized"] != 2
                or normalization["replacement"]
                != AUDIT_PDF_NORMALIZATION_REPLACEMENT
                or normalization["xref_offsets_preserved"] is not True):
            errors.append("audit candidate PDF normalization is malformed")
    if (audit_record.get("measured") is not True
            or audit_record.get("hard_failure") is not None
            or audit_record.get("error") is not None
            or audit_record.get("status") != "ok"
            or "roundtrip_liveness" in audit_record):
        errors.append("audit roundtrip success state is malformed")
    # The second half of the independent rehash: the Playwright package tree
    # and the exact Chromium binary that printed the candidate, resolved from
    # the installed package by this process and hashed again.
    closure_errors, attested = verify_published_roundtrip_closure(
        runtime.get("dependency_closure"), runtime.get("chromium"))
    errors.extend(closure_errors)
    return False, errors, bool(attested and not errors)


def bind_audit_manifest(
        audit_record: dict[str, Any] | None,
        expected: dict[str, tuple[pathlib.Path, bool, bytes | None]],
        *,
        source_path: pathlib.Path,
        source_identity: str,
        source_root: pathlib.Path,
        source_payload: bytes,
        expected_source_sha256: str,
        html_dir: pathlib.Path,
        producer_sources: dict[str, bytes],
        ) -> dict[str, Any]:
    """Verify exact bytes while preserving intentional attestation blockers."""
    errors: list[str] = []
    blockers: list[str] = []
    if not audit_record:
        return {
            "binding_valid": False,
            "complete": False,
            "reason": "no audit record",
            "errors": ["no audit record"],
            "blockers": [],
        }
    manifest = audit_record.get("input_manifest")
    if not isinstance(manifest, dict) or set(manifest) != {
            "schema", "algorithm", "producer", "runtime",
            "inputs_complete", "attestation_complete", "enforceable",
            "complete", "missing_required", "inputs", "render"}:
        return {
            "binding_valid": False,
            "complete": False,
            "reason": "audit input manifest schema is missing or unsupported",
            "errors": [
                "audit input manifest schema is missing or unsupported"],
            "blockers": [],
        }
    if (manifest["schema"] != "formgen-audit-input-manifest-v1"
            or manifest["algorithm"] != "sha256"):
        errors.append("audit input manifest schema/algorithm is unsupported")

    expected_producer_keys = {
        "file", "bytes", "sha256", "dependencies",
        "dependency_execution_bound", "audit_execution_bound",
        "assertion_producer_bound", "roundtrip_runtime_bound_in_record",
        "standalone_attestation_complete", "incomplete_reason",
    }
    producer = manifest["producer"]
    if not isinstance(producer, dict) or set(producer) != expected_producer_keys:
        errors.append("audit producer manifest schema is unsupported")
    else:
        audit_payload = producer_sources.get(AUDIT_PRODUCER_FILE)
        if (audit_payload is None
                or sha256_bytes(audit_payload) != AUDIT_PRODUCER_SHA256
                or producer["file"] != AUDIT_PRODUCER_FILE
                or producer["bytes"] != len(audit_payload)
                or producer["sha256"] != AUDIT_PRODUCER_SHA256):
            errors.append("audit producer bytes differ from the frozen pin")
        dependencies = producer["dependencies"]
        if not isinstance(dependencies, list):
            errors.append("audit dependency manifest is not a list")
        else:
            expected_dependency_files = list(AUDIT_DEPENDENCY_SHA256)
            if [
                    item.get("file") if isinstance(item, dict) else None
                    for item in dependencies
            ] != expected_dependency_files:
                errors.append("audit dependency order or identity is false")
            for index, logical in enumerate(expected_dependency_files):
                if index >= len(dependencies):
                    break
                item = dependencies[index]
                payload = producer_sources.get(logical)
                if (not isinstance(item, dict)
                        or set(item) != {
                            "file", "bytes", "sha256", "loaded_origin",
                            "executed_from_snapshotted_source"}
                        or payload is None
                        or sha256_bytes(payload)
                        != AUDIT_DEPENDENCY_SHA256[logical]
                        or item.get("file") != logical
                        or item.get("loaded_origin") != logical
                        or item.get("bytes") != len(payload)
                        or item.get("sha256")
                        != AUDIT_DEPENDENCY_SHA256[logical]
                        or item.get(
                            "executed_from_snapshotted_source") is not True):
                    errors.append(
                        f"audit dependency bytes/binding are false: {logical}")
        expected_flags = {
            "dependency_execution_bound": True,
            "audit_execution_bound": False,
            "assertion_producer_bound": False,
            "roundtrip_runtime_bound_in_record": False,
            "standalone_attestation_complete": False,
        }
        for key, expected_value in expected_flags.items():
            if producer.get(key) is not expected_value:
                errors.append(f"audit producer flag is false: {key}")
        if (not isinstance(producer.get("incomplete_reason"), str)
                or not producer["incomplete_reason"]):
            errors.append("audit producer lacks its incomplete-scope reason")

    runtime_errors, closure_errors, base_attested = validate_audit_runtime(
        manifest["runtime"])
    errors.extend(runtime_errors)
    errors.extend(closure_errors)
    inputs = manifest["inputs"]
    if not isinstance(inputs, dict) or set(inputs) != AUDIT_INPUT_ROLES:
        errors.append("audit input manifest roles disagree")
        inputs = {}
    if set(expected) != AUDIT_INPUT_ROLES - {"source_pdf"}:
        errors.append("referee audit input specification is incomplete")
    missing: list[str] = []
    for role in sorted(AUDIT_INPUT_ROLES - {"source_pdf"}):
        spec = expected.get(role)
        entry = inputs.get(role)
        if spec is None or not isinstance(entry, dict):
            errors.append(f"audit input entry is missing: {role}")
            continue
        path, required, payload = spec
        present = payload is not None
        expected_entry = {
            "file": path.name,
            "required": required,
            "present": present,
            "bytes": len(payload) if payload is not None else None,
            "sha256": (
                sha256_bytes(payload) if payload is not None else None),
        }
        if entry != expected_entry:
            errors.append(f"audit input bytes/metadata disagree: {role}")
        if required and not present:
            missing.append(role)
    source_entry = inputs.get("source_pdf")
    try:
        logical_source_path = source_path.relative_to(
            source_root.expanduser()).as_posix()
    except ValueError:
        logical_source_path = source_path.name
    expected_source_entry = {
        "file": source_identity.split(":", 1)[-1],
        "logical_identity": source_identity,
        "path": logical_source_path,
        "required": True,
        "present": True,
        "bytes": len(source_payload),
        "sha256": sha256_bytes(source_payload),
        "expected_sha256": expected_source_sha256,
    }
    if source_entry != expected_source_entry:
        errors.append("audit source PDF bytes/identity disagree")
    if manifest["missing_required"] != missing:
        errors.append("audit missing-required inventory is false")
    inputs_complete = not missing
    if manifest["inputs_complete"] is not inputs_complete:
        errors.append("audit inputs_complete relation is false")

    render = manifest["render"]
    html_spec = expected.get("html")
    html_payload = html_spec[2] if html_spec is not None else None
    expected_entrypoint = html_spec[0].name if html_spec is not None else None
    independent_dependencies: list[dict[str, Any]] = []
    render_errors: list[str] = []
    if isinstance(html_payload, bytes) and expected_entrypoint is not None:
        independent_dependencies, render_errors = audit_render_dependencies(
            html_payload, expected_entrypoint, html_dir)
    else:
        render_errors.append("HTML snapshot is absent")
    if not isinstance(render, dict) or set(render) != {
            "entrypoint", "dependencies", "errors", "complete",
            "network_policy"}:
        errors.append("audit render manifest schema is unsupported")
        dependency_paths: list[str] = []
    else:
        dependencies = render["dependencies"]
        dependency_paths = (
            [item.get("path") for item in dependencies]
            if isinstance(dependencies, list)
            and all(isinstance(item, dict) for item in dependencies)
            else []
        )
        if render["entrypoint"] != expected_entrypoint:
            errors.append("audit render entrypoint is false")
        if dependencies != independent_dependencies:
            errors.append("audit render dependency closure/bytes disagree")
        if render["errors"] != render_errors:
            errors.append("audit render error inventory disagrees")
        if render["complete"] is not (not render_errors):
            errors.append("audit render complete relation is false")
        if render["network_policy"] != (
                "deny-except-retained-relative-resources-and-inline-data"):
            errors.append("audit render network policy is unsupported")

    provenance = audit_record.get("provenance_validation")
    if provenance != {
            "validated_before": True,
            "validated_after": True,
            "error": None}:
        errors.append("audit provenance was not validated before and after")
    roundtrip_scope, roundtrip_errors, roundtrip_attested = (
        validate_audit_roundtrip(
            audit_record, expected_entrypoint or "", dependency_paths))
    errors.extend(roundtrip_errors)
    # What the referee itself proved, and therefore what the audit is allowed
    # to claim. A claim the referee did not verify is an overclaim and stays
    # an error; a claim the audit withholds while the referee did verify it is
    # equally a disagreement, because the manifest must state the truth rather
    # than a safe-looking approximation of it.
    verified_closure = bool(base_attested)
    verified_claim = bool(inputs_complete and verified_closure)
    verified_attestation_complete = bool(
        verified_claim
        and (roundtrip_scope is None or roundtrip_attested))
    attestation = audit_record.get("attestation")
    if not isinstance(attestation, dict) or set(attestation) != {
            "inputs_complete", "producer_execution_bound",
            "base_runtime_scope_complete", "roundtrip_runtime_scope_complete",
            "application_closure_complete", "validated_before_after",
            "complete", "enforceable", "incomplete_reasons",
            "declared_out_of_scope", "future_gate_required"}:
        errors.append("audit top-level attestation schema is unsupported")
        attestation = {}
    else:
        expected_attestation = {
            "inputs_complete": inputs_complete,
            "producer_execution_bound": False,
            "base_runtime_scope_complete": False,
            "roundtrip_runtime_scope_complete": roundtrip_scope,
            "application_closure_complete": verified_closure,
            "validated_before_after": True,
            "complete": verified_attestation_complete,
            "enforceable": verified_attestation_complete,
        }
        for key, expected_value in expected_attestation.items():
            if attestation.get(key) is not expected_value:
                errors.append(f"audit attestation relation is false: {key}")
        if (not isinstance(attestation["incomplete_reasons"], list)
                or not all(isinstance(item, str) and item
                           for item in attestation["incomplete_reasons"])
                or bool(attestation["incomplete_reasons"])
                is bool(attestation.get("complete"))
                or not isinstance(attestation["declared_out_of_scope"], list)
                or not attestation["declared_out_of_scope"]
                or not all(isinstance(item, str) and item
                           for item in attestation["declared_out_of_scope"])
                or not isinstance(attestation["future_gate_required"], str)
                or not attestation["future_gate_required"]):
            errors.append("audit attestation blocker explanation is malformed")
    if manifest["attestation_complete"] is not verified_claim:
        errors.append(
            "audit manifest overclaims producer attestation"
            if manifest["attestation_complete"]
            else "audit manifest attestation disagrees with the referee's "
                 "independent verification")
    if manifest["enforceable"] is not verified_claim:
        errors.append(
            "audit manifest overclaims enforceability"
            if manifest["enforceable"]
            else "audit manifest enforceability disagrees with the referee's "
                 "independent verification")
    if manifest["complete"] is not verified_claim:
        errors.append(
            "audit manifest overclaims completeness"
            if manifest["complete"]
            else "audit manifest completeness disagrees with the referee's "
                 "independent verification")
    if manifest["attestation_complete"] is False:
        blockers.append("audit producer/runtime attestation is incomplete")
    if manifest["enforceable"] is False:
        blockers.append("audit evidence is not yet enforceable")
    if manifest["complete"] is False:
        blockers.append("audit input manifest is intentionally non-gating")
    if not base_attested:
        blockers.append(
            "audit PyMuPDF/application runtime closure is not independently "
            "rehashed: the referee could not confirm every named module and "
            "native dependency from the installed package")
    if roundtrip_scope is None:
        blockers.append(
            "audit published no round trip, so its Playwright/Chromium "
            "closure could not be independently rehashed")
    elif not roundtrip_attested:
        blockers.append(
            "audit Playwright/Chromium closure is not independently rehashed "
            "by the standalone referee")
    binding_valid = not errors
    complete = bool(
        binding_valid
        and manifest["complete"] is True
        and manifest["enforceable"] is True
        and attestation.get("complete") is True
        and attestation.get("enforceable") is True
        and base_attested
        and roundtrip_attested
    )
    reason_parts = [
        *(f"invalid: {error}" for error in errors),
        *(f"blocked: {blocker}" for blocker in blockers),
    ]
    return {
        "binding_valid": binding_valid,
        "manifest_inputs_complete": inputs_complete,
        "attestation_complete": bool(
            manifest["attestation_complete"]
            and attestation.get("complete")),
        "enforceable": bool(
            manifest["enforceable"] and attestation.get("enforceable")),
        "complete": complete,
        "reason": "; ".join(reason_parts) if reason_parts else "complete",
        "errors": errors,
        "blockers": blockers,
        "host_scope_boundaries": [
            *REFEREE_HOST_SCOPE_BOUNDARIES,
            *(attestation.get("declared_out_of_scope") or ()),
        ],
        "producer_sha256": producer.get("sha256") if isinstance(
            producer, dict) else None,
        "runtime_tree_sha256": (
            ((manifest.get("runtime") or {}).get(
                "loaded_application_files") or {}).get("tree_sha256")
        ),
        "runtime_manifest_self_consistent": not runtime_errors,
        "base_runtime_closure_independently_attested": base_attested,
        "roundtrip_runtime_closure_independently_attested": roundtrip_attested,
        "render_dependency_count": len(independent_dependencies),
        "render_dependencies": independent_dependencies,
        "roundtrip_present": roundtrip_scope is not None,
    }


def bind_audit_assertion(
        audit: dict[str, Any],
        ledger: dict[str, Any],
        slots: dict[str, dict[str, Any]],
        emission_inventory: dict[str, Any],
        ) -> dict[str, Any]:
    """Bind legacy-cell audit identities to the canonical subject ledger."""
    errors: list[str] = []
    # Ledger order, which `validate_comb_ledger` proves is the layout cell-stream
    # order -- the same canonical order the published `cells` list carries.
    active_order = [
        subject["cell_id"] for subject in ledger["subjects"]
        if subject["state"] in {"active_resolved", "active_unresolved"}
    ]
    active_ids = set(active_order)

    def bound_ids(key: str, label: str) -> list[str] | None:
        try:
            return string_list(audit.get(key), label)
        except RefereeError as error:
            errors.append(str(error))
            return None

    expected_ids = bound_ids(
        "expected_comb_ids", "audit expected comb IDs")
    checked_ids = bound_ids(
        "checked_comb_ids", "audit checked comb IDs")
    if (expected_ids is not None and checked_ids is not None
            and checked_ids != expected_ids):
        errors.append("audit checked IDs differ from expected IDs")
    for label, published_ids in (
            ("expected", expected_ids), ("checked", checked_ids)):
        if published_ids is None:
            continue
        published = set(published_ids)
        missing = sorted(active_ids - published)
        extra = sorted(published - active_ids)
        if missing:
            errors.append(
                f"audit {label} IDs omit active ledger IDs: "
                + ", ".join(missing[:8]))
        if extra:
            errors.append(
                f"audit {label} IDs add non-active ledger IDs: "
                + ", ".join(extra[:8]))
    emitted_ids = sorted(slots)
    if audit.get("emitted_comb_ids") != emitted_ids:
        errors.append("audit emitted inventory differs from parsed HTML")
    if audit.get("unexpected_emitted_comb_ids") != (
            emission_inventory["unexpected_emitted_cell_ids"]):
        errors.append(
            "audit unexpected-emission inventory differs from ledger binding")

    ledger_aliases = {
        subject["legacy_cell_id"]: subject for subject in ledger["subjects"]
    }
    inference_aliases = {
        inference["cell_id"]: inference for inference in ledger["inferences"]
    }

    def validated_noncomb_binding_offender(offender: Any) -> bool:
        if not isinstance(offender, dict):
            return False
        failure_kinds = offender.get("failure_kinds") or ()
        relation = offender.get("layout_relation")
        emission_relation = offender.get("emission_relation")
        return bool(
            emission_relation == "invalid"
            and (
                (relation == "cell-binding-invalid"
                 and "emitted-cell-binding-invalid" in failure_kinds)
                or (relation == "not-owned"
                    and "unowned-live-comb-markup" in failure_kinds)
            )
        )

    unknown = sorted(
        cell_id for cell_id, offender in audit.get("offenders", {}).items()
        if _CELL_RE.fullmatch(cell_id)
        and cell_id not in ledger_aliases
        and cell_id not in inference_aliases
        and not validated_noncomb_binding_offender(offender)
    )
    if unknown:
        errors.append(
            "audit offenders do not map to ledger legacy identities: "
            + ", ".join(unknown[:8]))
    for missing_id in emission_inventory["missing_active_cell_ids"]:
        subject = next(
            item for item in ledger["subjects"]
            if item["cell_id"] == missing_id)
        offender = audit.get("offenders", {}).get(
            subject["legacy_cell_id"])
        if (offender is None
                or offender.get("emission_state") != "missing-emitted-cell"):
            errors.append(
                "audit omits current missing active emission: "
                + subject["subject_key"])
    for unexpected_id in emission_inventory["unexpected_emitted_cell_ids"]:
        offender = audit.get("offenders", {}).get(unexpected_id)
        if (offender is None
                or "unexpected-emitted-comb"
                not in (offender.get("failure_kinds") or ())):
            errors.append(
                f"audit omits current unexpected emission: {unexpected_id}")
    return {
        "binding_valid": not errors,
        "reason": "complete" if not errors else "; ".join(errors),
        "errors": errors,
        "active_subject_ids": active_order,
        "emitted_ids": emitted_ids,
        "legacy_alias_count": len(ledger_aliases),
    }


def page_signature(value: dict[str, Any]) -> list[tuple[int, float, float]]:
    pages = value.get("pages")
    if not isinstance(pages, list):
        raise RefereeError("artifact pages is not a list")
    signature = [
        (int(page["index"]), float(page["width_pt"]), float(page["height_pt"]))
        for page in pages
    ]
    if [index for index, _, _ in signature] != list(
            range(1, len(signature) + 1)):
        raise RefereeError("artifact pages are not exhaustive and ordered")
    return signature


def bind_artifacts(slug: str, layout: dict[str, Any], ir: dict[str, Any],
                   guide: dict[str, Any], parser: SlotParser) -> None:
    for name, value in (("layout", layout), ("IR", ir), ("guide", guide)):
        if not isinstance(value, dict):
            raise RefereeError(f"{slug}: {name} artifact is not an object")
    for key in ("form", "source", "paper"):
        if ir.get(key) != layout.get(key):
            raise RefereeError(f"{slug}: IR/layout {key} provenance disagrees")
    if guide.get("form") != layout.get("form"):
        raise RefereeError(f"{slug}: guide/layout form provenance disagrees")
    layout_pages = page_signature(layout)
    if page_signature(ir) != layout_pages:
        raise RefereeError(f"{slug}: IR/layout page geometry disagrees")
    source = layout.get("source") or {}
    if int(source.get("page_count", -1)) != len(layout_pages):
        raise RefereeError(f"{slug}: pinned source page count disagrees")
    # The paper contract is bound PER PAGE, not collapsed to one size. Demanding
    # `uniform is True` refused 1604-CF outright, and its page 3 really IS
    # landscape in the pinned source (`pdfinfo`: 612x1008, 612x1008, 1008x612,
    # 612x1008). A form the referee cannot evaluate is a red verdict, so an
    # unsupported-but-correct source was scoring the same as a broken one, and
    # the per-page SVG geometry is checked against `page["width_pt"]` anyway --
    # uniformity was never what made the measurement sound. Every page is still
    # bound to the declared inventory, the inventory must be exhaustive and
    # canonically ordered, and `uniform` must be the true derived claim. That is
    # strictly MORE than the old check asked of the 52 uniform forms: a false
    # `distinct_sizes` used to pass and now does not.
    paper = layout.get("paper") or {}
    if set(paper) != {"uniform", "width_pt", "height_pt", "distinct_sizes"}:
        raise RefereeError(f"{slug}: paper contract schema is unsupported")
    declared_sizes = paper.get("distinct_sizes")
    if (not isinstance(declared_sizes, list) or not declared_sizes
            or declared_sizes != sorted(declared_sizes)
            or len(declared_sizes) != len(set(declared_sizes))):
        raise RefereeError(f"{slug}: paper size inventory is not canonical")
    observed_sizes = sorted({
        f"{width}x{height}" for _, width, height in layout_pages})
    if declared_sizes != observed_sizes:
        raise RefereeError(
            f"{slug}: paper size inventory does not enumerate the pages")
    if paper.get("uniform") is not (len(observed_sizes) == 1):
        raise RefereeError(f"{slug}: paper uniformity claim is false")
    first_width, first_height = layout_pages[0][1], layout_pages[0][2]
    if (abs(float(paper.get("width_pt", -1)) - first_width) > 1e-8
            or abs(float(paper.get("height_pt", -1)) - first_height) > 1e-8):
        raise RefereeError(f"{slug}: layout pages disagree with paper contract")
    form = layout.get("form") or {}
    root = parser.root
    if root is None:
        raise RefereeError(f"{slug}: HTML root metadata is missing")
    expected_root = {
        "data-form": str(form.get("code", "")),
        "data-revision": str(form.get("revision", "")),
        "data-source-sha256": str(source.get("sha256", "")),
        "data-schema-version": str(layout.get("schema_version", "")),
    }
    for key, expected in expected_root.items():
        if not expected or root.get(key) != expected:
            raise RefereeError(
                f"{slug}: HTML {key} disagrees with layout provenance")
    layout_page_indexes = {index for index, _, _ in layout_pages}
    whole_guide_pages: set[int] = set()
    inline = guide.get("inline") or []
    if not isinstance(inline, list):
        raise RefereeError(f"{slug}: guide inline inventory is not a list")
    for region in inline:
        try:
            page = int(region["page"])
            cut = float(region["cut_y_pt"])
            reclaimed = float(region["reclaimed_pct"])
        except (KeyError, TypeError, ValueError):
            raise RefereeError(f"{slug}: guide region provenance is incomplete")
        if page not in layout_page_indexes:
            raise RefereeError(f"{slug}: guide references an unknown page")
        if abs(cut) <= 1e-8 and abs(reclaimed - 100.0) <= 1e-8:
            whole_guide_pages.add(page)
    stats = guide.get("stats") or {}
    if int(stats.get("pages", -1)) != len(layout_pages):
        raise RefereeError(f"{slug}: guide/layout page counts disagree")
    expected_pages = [
        index for index, _, _ in layout_pages if index not in whole_guide_pages
    ]
    if parser.pages != expected_pages:
        raise RefereeError(f"{slug}: HTML/layout page inventory disagrees")
    expected_geometry = [
        (index, width, height)
        for index, width, height in layout_pages
        if index not in whole_guide_pages
    ]
    if parser.page_geometry != expected_geometry:
        raise RefereeError(f"{slug}: HTML/layout page geometry disagrees")


def bind_tracked_provenance(slug: str, layout: dict[str, Any]
                            ) -> tuple[pathlib.Path, bytes]:
    matches = sorted((REPO / "forms").glob(f"**/{slug}/provenance.json"))
    if len(matches) != 1:
        raise RefereeError(
            f"{slug}: expected one tracked provenance record, got {len(matches)}")
    path = matches[0]
    payload = path.read_bytes()
    provenance = json.loads(payload)
    form_sources = [
        source for source in provenance.get("sources") or ()
        if source.get("role") == "form"
    ]
    if len(form_sources) != 1:
        raise RefereeError(
            f"{slug}: tracked provenance has no unique form source")
    pinned = form_sources[0]
    layout_source = layout.get("source") or {}
    form = layout.get("form") or {}
    if (provenance.get("slug") != slug
            or str(provenance.get("revision")) != str(form.get("revision"))
            or pinned.get("sha256") != layout_source.get("sha256")
            or pinned.get("file")
            != str(layout_source.get("file", "")).split(":", 1)[-1]):
        raise RefereeError(
            f"{slug}: layout source disagrees with tracked provenance")
    return path, payload


def audit_relation_for_subject(
        subject: dict[str, Any],
        audit_complete: bool,
        audit_offender: dict[str, Any] | None,
        ) -> tuple[int | None, str]:
    """Publish audit topology only where the exhaustive audit proves it."""
    if audit_offender is not None:
        return audit_offender.get("printed"), "published-offender"
    if audit_complete and subject["state"] in {
            "active_resolved", "active_unresolved"}:
        return int(subject["topology"]["cells"]), "complete-non-offender"
    if audit_complete:
        return None, "complete-blocked-subject"
    return None, "unknown-truncated"


def composite_comparison(cell: dict[str, Any]) -> tuple[str, str]:
    """Score a reviewed composite on the only claim it makes.

    Its claim is not a compartment count -- it has no comb -- but that the
    source suppresses the legacy comb for the tabled reason the review
    confirmed.  Review cannot overrule the paper: a corroboration that comes
    back FALSE is a `stop`, exactly as a four-way disagreement is, and the
    registry entry behind it is then wrong rather than stronger.
    """
    referee = cell["referee"]
    if cell.get("emitted") is not None:
        return (
            "stop",
            "a composite subject emitted physical slots of its own",
        )
    if referee.get("status") != "composite":
        return (
            "unevaluable",
            "composite subject carries no corroboration measurement",
        )
    if not referee.get("corroborated"):
        return (
            "stop",
            "the source refutes the reviewed composite's suppression claim",
        )
    return (
        "agree",
        "the source corroborates the reviewed composite's suppression claim",
    )


def reviewed_exception_status(cell: dict[str, Any], slug: str,
                              source_sha: str, status: str, reason: str
                              ) -> tuple[str, str]:
    """Apply a reviewed exception, and ONLY to the verdict it names.

    The third review path (S2). The other two say "the reviewer confirms
    what the paper shows"; this one says "the reviewer accepts that the
    paper CANNOT show it". It is deliberately the narrowest of the three:

      * it applies only to an `unevaluable` comparison -- an exception can
        never turn a `stop` (an active disagreement) into a pass;
      * the entry records the EXACT refusal string it excuses, and this
        honours it only while the live refusal still equals it. A fix, a
        re-pin or a re-read that changes the refusal makes the exception
        STALE, which is an error, not a silent pass;
      * the result is published as its own comparison kind, `excepted`,
        never folded into `agree`, so the report always states how many
        verdicts were excused and the pass bar has to name them.
    """
    if status != "unevaluable":
        return status, reason
    key = (slug, int(cell["page"]),
           str(cell.get("cell_id") or cell["legacy_cell_id"]))
    entry = review_registry.REVIEWED_UNEVALUABLE_EXCEPTIONS.get(key)
    if entry is None:
        return status, reason
    if entry["source_sha256"] != source_sha:
        return status, reason
    if entry["subject_key"] != cell["subject_key"]:
        raise RefereeError(
            f"{key}: reviewed exception subject_key does not bind this "
            "subject")
    if entry["reason"] != reason:
        raise RefereeError(
            f"{key}: reviewed exception is stale -- it excuses "
            f"{entry['reason']!r} but the source now says {reason!r}")
    return "excepted", f"reviewed exception: {reason}"


def comparison(cell: dict[str, Any], audit_complete: bool) -> tuple[str, str]:
    ledger_state = cell.get("ledger_state")
    if ledger_state == "active_composite":
        return composite_comparison(cell)
    if ledger_state not in {"active_resolved", "active_unresolved"}:
        return (
            "unevaluable",
            "ledger subject has no active topology for adjudication",
        )
    lattice = cell["latticed"]
    emitted = cell["emitted"]
    referee = cell["referee"]
    certificate = cell.get("resolution_certificate")
    if emitted != lattice or not cell["emitted_indexes_valid"]:
        return "stale-generation", "emitted physical slots disagree with lattice"
    if not audit_complete:
        return "unevaluable", "audit evidence is incomplete"
    # Complete audit evidence can still leave ONE subject without a topology:
    # the audit publishes it as an offender whose printed compartment count it
    # could not measure. Calling that "audit evidence is incomplete" was
    # harmless while the evidence was never complete, and is a false statement
    # now that it is.
    if cell["audit_printed"] is None:
        return (
            "unevaluable",
            "audit published this subject as an offender with no printed "
            "topology",
        )
    if referee.get("status") != "measured":
        return "unevaluable", f"referee: {referee.get('reason', 'no reason')}"
    if not bool(referee.get("positions_match")):
        return "stop", "referee positions disagree with lattice anchors"
    source = int(referee["compartments"])
    audit = int(cell["audit_printed"])
    if certificate is not None:
        # REVIEW CANNOT OVERRULE THE PAPER.  A reviewed resolution was signed
        # on four counts recorded in the registry; this run re-derives all
        # four from the source, the audit, the layout and the emission, and
        # every one must still equal what was signed.  A corpus that has
        # moved under a signed decision is a `stop`, never a quiet pass --
        # the decision has to be re-reviewed against the new evidence.
        signed = certificate["four_way"]
        if (signed["lattice"] != lattice or signed["audit"] != audit
                or signed["emitted"] != emitted
                or signed["referee"] != source):
            return (
                "stop",
                "the evidence has moved since this resolution was reviewed",
            )
    if source == lattice == audit:
        return "agree", "referee, lattice, audit, and emitted agree"
    if source == audit and source != lattice:
        return "repair-lattice", "referee and audit agree against lattice"
    if source == lattice and source != audit:
        return "repair-audit", "referee and lattice agree against audit"
    if lattice == audit and source != lattice:
        return "stop", "lattice and audit agree against the independent referee"
    return "stop", "referee, lattice, and audit all differ"


def transition_decision(
        cell: dict[str, Any], comparison_status: str) -> tuple[str, str]:
    """Report review eligibility without mutating the blocking ledger."""
    ledger_state = cell.get("ledger_state")
    if comparison_status == "excepted":
        # A reviewed exception IS the adjudication: the user accepted that
        # the paper cannot decide this subject, so no transition is pending
        # -- there is nothing left for a future review to wait on unless
        # the exception itself is removed, at which point the subject
        # returns to its blocking shape and this function's other branches
        # apply again.
        return (
            "none",
            "reviewed exception holds; the paper cannot adjudicate a "
            "transition",
        )
    if ledger_state == "active_resolved":
        return "none", "active ledger subject is already resolved"
    if ledger_state == "active_composite":
        # The transition this state names has already been made, under a
        # reviewed certificate this run re-validated.  There is nothing left
        # to become eligible for.
        return "none", "reviewed composite transition is already applied"
    if ledger_state == "active_unresolved":
        if comparison_status == "agree":
            return (
                "eligible-for-reviewed-resolution",
                "four-way evidence agrees; explicit review is still required",
            )
        return (
            "blocked",
            "active unresolved ledger subject remains blocking while "
            f"comparison status is {comparison_status}",
        )
    if ledger_state == "retained_unresolved":
        return (
            "explicit-transition-required",
            "retained unresolved subject has no active topology; an explicit "
            "ledger transition is required",
        )
    return (
        "blocked",
        "unknown ledger state cannot be transitioned",
    )


def changed_snapshot_inputs(form: dict[str, Any],
                            args: argparse.Namespace) -> list[str]:
    slug = form["slug"]
    artifacts = form["artifacts"]
    checks: list[tuple[str, pathlib.Path, str | None]] = [
        ("layout", args.layout_dir / f"{slug}.layout.json",
         artifacts["layout_sha256"]),
        ("ir", args.ir_dir / f"{slug}.ir.json", artifacts["ir_sha256"]),
        ("html", args.html_dir / f"{slug}.html", artifacts["html_sha256"]),
        ("guide", args.guide_dir / f"{slug}.guide.json",
         artifacts["guide_sha256"]),
        ("guide_html", args.html_dir / f"{slug}.guide.html",
         artifacts["guide_html_sha256"]),
        ("tracked_provenance", REPO / artifacts["tracked_provenance_file"],
         artifacts["tracked_provenance_sha256"]),
        ("source", args.source_root / form["source"]["file"],
         form["source"]["sha256"]),
    ]
    changed: list[str] = []
    for role, path, expected in checks:
        if expected is None:
            if path.exists():
                changed.append(role)
            continue
        try:
            actual = sha256_file(path)
        except OSError:
            changed.append(role)
            continue
        if actual != expected:
            changed.append(role)
    manifest_binding = (
        form.get("audit_evidence", {}).get("manifest_binding", {}))
    render_dependencies = manifest_binding.get("render_dependencies", [])
    if isinstance(render_dependencies, list):
        for entry in render_dependencies:
            if not isinstance(entry, dict):
                changed.append("audit_render_dependency_manifest")
                continue
            logical = entry.get("path")
            expected_sha = entry.get("sha256")
            if not isinstance(logical, str) or not isinstance(expected_sha, str):
                changed.append("audit_render_dependency_manifest")
                continue
            path = args.html_dir.joinpath(
                *pathlib.PurePosixPath(logical).parts)
            try:
                actual = sha256_file(path)
            except OSError:
                changed.append(f"audit_render_dependency:{logical}")
                continue
            if actual != expected_sha:
                changed.append(f"audit_render_dependency:{logical}")
    return changed


def form_report(layout_path: pathlib.Path, args: argparse.Namespace,
                audit_by_slug: dict[str, dict[str, Any]],
                poppler: dict[str, Any]) -> dict[str, Any]:
    slug = layout_path.name.removesuffix(".layout.json")
    html_path = args.html_dir / f"{slug}.html"
    ir_path = args.ir_dir / f"{slug}.ir.json"
    guide_path = args.guide_dir / f"{slug}.guide.json"
    guide_html_path = args.html_dir / f"{slug}.guide.html"
    snapshots: dict[str, bytes | None] = {}
    for role, path, required in (
        ("layout", layout_path, True),
        ("ir", ir_path, True),
        ("html", html_path, True),
        ("guide", guide_path, True),
        ("guide_html", guide_html_path, False),
    ):
        try:
            snapshots[role] = path.read_bytes()
        except FileNotFoundError:
            if required:
                raise RefereeError(
                    f"{slug}: missing artifact: {path.relative_to(REPO)}")
            snapshots[role] = None
    layout_bytes = snapshots["layout"]
    ir_bytes = snapshots["ir"]
    html_bytes = snapshots["html"]
    guide_bytes = snapshots["guide"]
    assert (layout_bytes is not None and ir_bytes is not None
            and html_bytes is not None and guide_bytes is not None)
    layout = json.loads(layout_bytes)
    ir = json.loads(ir_bytes)
    guide = json.loads(guide_bytes)
    expected_combs = EXPECTED_COMBS_BY_SLUG.get(slug)
    if expected_combs is None:
        raise RefereeError(f"{slug}: form is not in the pinned referee corpus")
    ledger = validate_comb_ledger(
        slug, layout, args.lattice_producer_bytes)
    html_structure_sha256 = emitted_structure_sha256(html_bytes)
    if html_structure_sha256 != EXPECTED_HTML_STRUCTURE_SHA256.get(slug):
        raise RefereeError(
            f"{slug}: emitted HTML bytes changed from the reviewed pin")
    html_parser = SlotParser()
    html_parser.feed(html_bytes.decode("utf-8"))
    html_parser.close()
    if (html_parser.template_depth or html_parser.div_stack
            or html_parser.element_stack or html_parser.style_depth
            or html_parser.script_depth):
        raise RefereeError(
            f"{slug}: HTML has unclosed structural elements")
    bind_artifacts(slug, layout, ir, guide, html_parser)
    provenance_path, provenance_bytes = bind_tracked_provenance(slug, layout)
    pdf = source_pdf(layout, args.source_root)
    expected_sha = layout["source"]["sha256"]
    pdf_bytes = pdf.read_bytes()
    actual_sha = sha256_bytes(pdf_bytes)
    if actual_sha != expected_sha:
        raise RefereeError(f"{slug}: source hash changed")
    source_contract = layout.get("source")
    if (not isinstance(source_contract, dict)
            or set(source_contract) != {
                "file", "sha256", "bytes", "page_count",
            }
            or source_contract.get("bytes") != len(pdf_bytes)
            or source_contract.get("page_count") != len(layout["pages"])):
        raise RefereeError(
            f"{slug}: source PDF provenance contract is incomplete")

    # Every page is rasterised once, up front, because the emission contract
    # now has to be corroborated against the same source the subjects are
    # measured on.  Rendering twice would double the corpus cost and could
    # answer two questions from two different rasters.
    page_meta: list[dict[str, Any]] = []
    svg_pages: dict[int, SvgPage] = {}
    with tempfile.TemporaryDirectory(prefix=f"comb-referee-{slug}-") as temp:
        directory = pathlib.Path(temp)
        pdf_snapshot = directory / "source.pdf"
        pdf_snapshot.write_bytes(pdf_bytes)
        for page in sorted(layout["pages"], key=lambda item: int(item["index"])):
            page_index = int(page["index"])
            svg_path = render_svg_page(
                poppler["binary_path"], pdf_snapshot, page_index, directory)
            svg = parse_svg(svg_path)
            if (abs(svg.width - float(page["width_pt"])) > POSITION_TOL_PT
                    or abs(svg.height - float(page["height_pt"]))
                    > POSITION_TOL_PT):
                raise RefereeError(
                    f"{slug} page {page_index}: SVG/page dimensions disagree")
            page_meta.append({
                "page": page_index,
                "svg_sha256": svg.sha256,
                "vector_paints": len(svg.paints),
                "unsupported_regions": len(svg.unsupported),
            })
            svg_pages[page_index] = svg

    emission_contract, band_corroborations = emitted_geometry_contract(
        layout, guide, svg_pages)
    if set(emission_contract) != set(ledger["active_cell_ids"]):
        raise RefereeError(
            f"{slug}: guide/layout emission contract does not exactly bind "
            "the active subject ledger")
    slots = slot_records(html_parser, emission_contract)
    emission_inventory = validate_emission_inventory(ledger, slots)
    audit_record = audit_by_slug.get(slug)
    owner_binding = audit_owner_binding(layout_bytes, ledger)
    audit = audit_evidence(audit_record, owner_binding)
    manifest_binding = bind_audit_manifest(audit_record, {
        "ir": (ir_path, True, ir_bytes),
        "layout": (layout_path, True, layout_bytes),
        "html": (html_path, True, html_bytes),
        "guide": (guide_path, True, guide_bytes),
        "guide_html": (
            guide_html_path, False, snapshots["guide_html"]),
    },
        source_path=pdf,
        source_identity=str(source_contract["file"]),
        source_root=args.source_root,
        source_payload=pdf_bytes,
        expected_source_sha256=actual_sha,
        html_dir=args.html_dir,
        producer_sources={
            AUDIT_PRODUCER_FILE: args.audit_producer_bytes,
            **args.audit_dependency_bytes,
        },
    )
    assertion_binding = bind_audit_assertion(
        audit, ledger, slots, emission_inventory)
    audit["input_manifest_verified"] = manifest_binding["binding_valid"]
    audit["input_manifest_reason"] = manifest_binding["reason"]
    audit["manifest_binding"] = manifest_binding
    audit["ledger_binding"] = assertion_binding
    audit["evidence_published"] = bool(audit.get("assertion_valid"))
    audit["byte_and_relation_binding_valid"] = bool(
        audit.get("assertion_valid")
        and manifest_binding["binding_valid"]
        and assertion_binding["binding_valid"]
    )
    audit["runtime_closure_independently_attested"] = bool(
        manifest_binding["base_runtime_closure_independently_attested"]
        and manifest_binding[
            "roundtrip_runtime_closure_independently_attested"])
    audit["integrity_valid"] = bool(
        audit["byte_and_relation_binding_valid"]
        and manifest_binding[
            "base_runtime_closure_independently_attested"]
        and manifest_binding[
            "roundtrip_runtime_closure_independently_attested"]
    )
    audit["complete"] = bool(
        audit["integrity_valid"]
        and audit["byte_and_relation_binding_valid"]
        and manifest_binding["complete"])
    audit_reasons = [
        value for value in (
            None if audit.get("assertion_valid") else audit.get("reason"),
            None if assertion_binding["binding_valid"]
            else assertion_binding["reason"],
            None if manifest_binding["complete"]
            else manifest_binding["reason"],
        ) if value
    ]
    audit["reason"] = (
        "complete" if audit["complete"] else "; ".join(audit_reasons))
    cells: list[dict[str, Any]] = []
    # Every retained suppression the ledger admitted on a re-derivable reason
    # is discharged here, against Poppler, and the two inventories are compared
    # afterwards.  A corroboration that did not run cannot be mistaken for one
    # that ran and passed.
    suppression_corroborations: dict[str, str] = {}
    subjects_by_page: dict[int, list[dict[str, Any]]] = {}
    for subject in ledger["subjects"]:
        subjects_by_page.setdefault(int(subject["page"]), []).append(subject)

    for page in sorted(layout["pages"], key=lambda item: int(item["index"])):
        page_index = int(page["index"])
        svg = svg_pages[page_index]
        for subject in subjects_by_page.get(page_index, ()):
            source_cell = subject["source_cell"]
            result = classify_band(
                source_cell, svg, ledger_state=subject["state"])
            corroboration = None
            if subject["source_suppression_criterion"] is not None:
                corroboration = retained_suppression_corroboration(
                    subject, result, svg,
                    f"{slug} page {page_index} "
                    f"{subject['legacy_cell_id']}")
                suppression_corroborations[subject["legacy_cell_id"]] = (
                    corroboration["criterion"])
            if subject["state"] == "active_composite":
                # A composite subject has NO comb of its own -- that is what
                # the review certified -- so measuring its legacy band would
                # score a claim nobody makes.  Its measurement is the source
                # corroboration of the suppression itself, which is the only
                # thing about it the paper can answer.  The partition cells it
                # maps to carry their own comparison rows, so their
                # correctness is scored there and never double-counted here.
                label = (f"{slug} page {page_index} "
                         f"{subject['legacy_cell_id']}")
                if corroboration is None:
                    raise RefereeError(
                        f"{label} composite subject has no tabled suppression "
                        "criterion to corroborate")
                if "corroborated" in corroboration:
                    corroborated = bool(corroboration["corroborated"])
                elif (corroboration["criterion"]
                        == SOURCE_CAPTION_BLOCK_CRITERION):
                    # That criterion RAISES on every failure, so a returned
                    # census is its affirmative verdict.
                    corroborated = True
                else:
                    raise RefereeError(
                        f"{label} composite corroboration published no "
                        "verdict")
                result = {
                    "status": "composite",
                    "criterion": corroboration["criterion"],
                    "corroborated": corroborated,
                    "reason": (
                        "the source corroborates the reviewed composite's "
                        "suppression claim"
                        if corroborated else
                        "the source REFUTES the reviewed composite's "
                        "suppression claim"),
                }
            report_cell_id = (
                subject["cell_id"] or subject["legacy_cell_id"])
            # An emitted subject whose writing band the source refuses to
            # confirm cannot be adjudicated from that source: the vertical
            # geometry every one of its slots is bound to is unproven.  That
            # is published as the referee's own UNEVALUABLE verdict -- counted
            # in `combs_source_unevaluable` and in the unevaluable comparison
            # bucket -- and never silently dropped.
            band = band_corroborations.get(report_cell_id)
            if band is not None and band["status"] != "corroborated":
                result = {
                    "status": "unevaluable",
                    "reason": (
                        "the source does not corroborate the comb writing "
                        f"band: {band['reason']}"),
                    "writing_band_corroboration": band,
                }
            emitted = slots.get(report_cell_id)
            audit_offender = audit["offenders"].get(
                subject["legacy_cell_id"])
            audit_printed, audit_relation = audit_relation_for_subject(
                subject, bool(audit["complete"]), audit_offender)
            cells.append({
                "cell": report_cell_id,
                "subject_key": subject["subject_key"],
                "legacy_cell_id": subject["legacy_cell_id"],
                "cell_id": subject["cell_id"],
                "ledger_state": subject["state"],
                "ledger_blocks_gate": subject["blocks_gate"],
                "ledger_reason_codes": subject["reason_codes"],
                "ledger_topology_sha256": subject["topology"]["sha256"],
                "ledger_evidence": subject["ledger"],
                "page": page_index,
                "bbox": list(subject["legacy_bbox"]),
                "latticed": int(subject["topology"]["cells"]),
                "lattice_divider_x": subject["topology"]["divider_x"],
                "emitted": emitted["count"] if emitted else None,
                "emitted_indexes_valid": bool(emitted and emitted["valid"]),
                "emitted_evidence": emitted,
                "audit_printed": audit_printed,
                "audit_relation": audit_relation,
                "resolution_certificate": subject.get(
                    "resolution_certificate"),
                # Published for the SAME reason as its sibling above, and it
                # was omitted here while the sibling was not: gate's
                # corpus-coverage guard reads certificates off these cells,
                # so 29 correctly-applied transitions read as "applied
                # nowhere". The certificate lived on the ledger subject the
                # whole time -- what was missing was the report saying so.
                "transition_certificate": subject.get(
                    "transition_certificate"),
                # None on every cell until the exception pass below stamps
                # the applied entry's identity -- published unconditionally
                # so the report schema is one shape, the certificate
                # pattern exactly.
                "exception_registry_key": None,
                "referee": result,
            })

    cell_ids = {cell["cell"] for cell in cells}
    if len(cell_ids) != len(cells) or len(cells) != expected_combs:
        raise RefereeError(
            f"{slug}: published subject identities are not exhaustive")
    assert_suppression_corroborations_exhaustive(
        slug, ledger["suppression_obligations"], suppression_corroborations)
    if slug == "2551q-2018":
        validate_2551q_referee_golden(cells)
    source_sha_for_exceptions = str(
        (layout.get("source") or {}).get("sha256") or "")
    for cell in cells:
        status, reason = comparison(cell, bool(audit.get("complete")))
        status, reason = reviewed_exception_status(
            cell, slug, source_sha_for_exceptions, status, reason)
        cell["comparison_status"] = status
        cell["comparison_reason"] = reason
        if status == "excepted":
            # The applied entry's identity, published the same way the two
            # certificates publish theirs: so the gate's corpus-coverage
            # guard can count every reviewed exception applied exactly
            # once, off the report's own claims. The gate's mirror still
            # re-derives the entry per cell -- this key is the census, not
            # the proof.
            cell["exception_registry_key"] = [
                slug, int(cell["page"]),
                str(cell.get("cell_id") or cell["legacy_cell_id"])]
        transition_status, transition_reason = transition_decision(
            cell, status)
        cell["transition_status"] = transition_status
        cell["transition_reason"] = transition_reason
        cell["four_way"] = {
            "referee": (
                int(cell["referee"]["compartments"])
                if cell["referee"].get("status") == "measured" else None
            ),
            "lattice": cell["latticed"],
            "audit": cell["audit_printed"],
            "emitted": cell["emitted"],
        }

    source_measured = [
        cell for cell in cells if cell["referee"]["status"] == "measured"]
    # A composite IS measured -- on its corroboration, not on a band -- so
    # it belongs in neither bucket: not "measured" (no band measurement) and
    # never "source_unevaluable" (the source answered its question). The
    # gate partitions cells the same three ways and cross-checks this count.
    source_unevaluable = [
        cell for cell in cells
        if cell["referee"]["status"] not in ("measured", "composite")]
    unevaluable = [
        cell for cell in cells
        if cell["comparison_status"] == "unevaluable"]
    excepted = [
        cell for cell in cells
        if cell["comparison_status"] == "excepted"]
    layout_mismatches = [
        cell for cell in source_measured
        if int(cell["referee"]["compartments"]) != int(cell["latticed"])
    ]
    position_mismatches = [
        cell for cell in source_measured
        if not bool(cell["referee"].get("positions_match"))
    ]
    emission_mismatches = [
        cell for cell in cells
        # A suppressed subject (retained or reviewed composite) emits
        # nothing BY DESIGN; the emission inventory accounts for it, and
        # the gate derives this count with the same exclusion.
        if cell["ledger_state"] not in (
            "retained_unresolved", "active_composite")
        and (cell["emitted"] != cell["latticed"]
             or not cell["emitted_indexes_valid"])
    ]
    comparison_counts = {
        name: sum(cell["comparison_status"] == name for cell in cells)
        for name in COMPARISON_NAMES
    }
    status = "ok"
    reasons: list[str] = []
    # A blocking subject whose cell's comparison is `excepted` is EXCUSED:
    # a reviewed entry names its exact live refusal, this run re-verified
    # the match (a drifted refusal raises before reaching here), and the
    # blocker is counted out loud below rather than hidden. Only blockers
    # WITHOUT an applied exception keep the form unevaluable -- excusal is
    # per-subject and bound to the registry, never a form-level waiver.
    excepted_ids = {
        str(cell.get("legacy_cell_id") or cell.get("cell_id"))
        for cell in cells
        if cell.get("comparison_status") == "excepted"
    }
    blocking_excused = sum(
        1 for subject in ledger["subjects"]
        if subject.get("blocks_gate")
        and str(subject.get("legacy_cell_id") or subject.get("cell_id"))
        in excepted_ids
    )
    blocking_unexcused = ledger["counts"]["blocking"] - blocking_excused
    if blocking_unexcused:
        status = "unevaluable"
        reasons.append(
            f"{blocking_unexcused} lattice-ledger blockers")
    elif blocking_excused:
        reasons.append(
            f"{blocking_excused} blocker(s) excused by reviewed exception")
    if not emission_inventory["complete"]:
        status = "unevaluable"
        reasons.append(
            f"emission inventory incomplete: {emission_inventory['reason']}")
    if not audit.get("complete"):
        status = "unevaluable"
        reasons.append(f"audit evidence incomplete: {audit.get('reason')}")
    if comparison_counts["unevaluable"]:
        status = "unevaluable"
        reasons.append(f"{comparison_counts['unevaluable']} combs unevaluable")
    if status != "unevaluable" and any(
            comparison_counts[name] for name in (
                "repair-lattice", "repair-audit", "stale-generation", "stop")):
        status = "disagreement"
        reasons.append("one or more four-way comparisons disagree")

    return {
        "slug": slug,
        "status": status,
        "reason": ", ".join(reasons) if reasons else "all combs measured",
        "source": {
            "file": str(pdf.relative_to(args.source_root)),
            "sha256": actual_sha,
            "bytes": len(pdf_bytes),
            "page_count": len(layout["pages"]),
            "layout_pin": dict(source_contract),
        },
        "artifacts": {
            "ir_sha256": sha256_bytes(ir_bytes),
            "layout_sha256": sha256_bytes(layout_bytes),
            "html_sha256": sha256_bytes(html_bytes),
            "html_structure_sha256": html_structure_sha256,
            "guide_sha256": sha256_bytes(guide_bytes),
            "guide_html_sha256": (
                sha256_bytes(snapshots["guide_html"])
                if snapshots["guide_html"] is not None else None
            ),
            "tracked_provenance_file": str(provenance_path.relative_to(REPO)),
            "tracked_provenance_sha256": sha256_bytes(provenance_bytes),
        },
        "lattice_evidence": ledger["lattice"],
        "poppler": poppler,
        "pages": page_meta,
        "audit_evidence": {
            key: value for key, value in audit.items() if key != "offenders"
        },
        "emission_inventory": emission_inventory,
        "emission_binding_errors": html_parser.invalid_bindings,
        "counts": {
            "combs": len(cells),
            "subjects": ledger["counts"]["subjects"],
            "subjects_active": ledger["counts"]["active"],
            "subjects_active_resolved": ledger["counts"]["active_resolved"],
            "subjects_active_unresolved": ledger["counts"]["active_unresolved"],
            "subjects_retained_unresolved": (
                ledger["counts"]["retained_unresolved"]),
            "inferences_suppressed": (
                ledger["counts"]["inferences_suppressed"]),
            "ledger_blocking_excused": blocking_excused,
            "ledger_blocking": ledger["counts"]["blocking"],
            "measured": len(source_measured),
            "composite": sum(
                1 for cell in cells
                if cell["referee"]["status"] == "composite"),
            "source_unevaluable": len(source_unevaluable),
            "unevaluable": len(unevaluable),
            "referee_layout_mismatches": len(layout_mismatches),
            "referee_layout_position_mismatches": len(position_mismatches),
            "emission_layout_mismatches": len(emission_mismatches),
            "comparisons": comparison_counts,
        },
        "inferences": [
            {
                "page": inference["page"],
                "subject_key": inference["subject_key"],
                "cell_id": inference["cell_id"],
                "state": inference["state"],
                "blocks_gate": inference["blocks_gate"],
                "reason_codes": inference["reason_codes"],
                "bbox": inference["bbox"],
                "topology_sha256": inference["topology"]["sha256"],
                "ledger_evidence": inference["ledger"],
                "emitted_evidence": slots.get(inference["cell_id"]),
            }
            for inference in ledger["inferences"]
        ],
        # Published in LAYOUT CELL-STREAM order: pages ascending; within a page
        # the active subjects in the order the layout's own cell stream lists
        # them, which `validate_comb_ledger` PROVES equals the subject ledger
        # order rather than assuming it; then the retained subjects, which have
        # no cell in the current stream and therefore no document position, in
        # ledger order after every streamed cell on their page.
        #
        # This list used to be re-sorted by numeric cell id, and that was the
        # odd one out.  A cell id is a CONTINUITY identifier: lattice.py keeps
        # a cell's legacy id while its subject_key still matches a legacy box
        # and otherwise draws a fresh id from the end of the legacy range, so a
        # repaired partition seats a high-numbered owner mid-page (2550M's
        # restored p1c193 sits above p1c103-105).  Numeric order reads
        # discovery history, not geometry, and was right only by luck.  Every
        # other producer already declares the stream canonical -- lattice.py
        # binds the owner registry to "the exact order of the current layout
        # cell stream" and audit.py publishes offenders in page/cell document
        # order.
        #
        # This referee is the ADJUDICATOR: its derivation is the proven one,
        # which is exactly why its ordering must not be the one that disagrees.
        # The gate aligned its projection to the numeric key published here and
        # so canonicalised discovery order for every downstream consumer; the
        # ordering had to be corrected at the source, not compensated for
        # downstream.  A stable sort is used deliberately: it moves nothing
        # except the retained subjects, so the proven stream order survives.
        "cells": sorted(
            cells,
            key=lambda cell: (
                int(cell["page"]),
                cell["ledger_state"] == "retained_unresolved",
            ),
        ),
    }


def source_literal_duplicate_keys(name: str) -> list[str]:
    """Keys written more than once in one of this file's own dict LITERALS.

    F231: `EXPECTED_HTML_STRUCTURE_SHA256` had accumulated five full blocks of
    all 53 slugs, holding DIFFERENT shas. Python keeps the last, so four blocks
    were dead weight that read exactly like a live pin -- and a scripted re-pin
    duly updated a dead one and left the live pin stale.

    Nothing caught it, and the reason is the failure mode this repo keeps
    hitting: a checker that shares an assumption with its subject. Any check
    that reads the RESOLVED dict -- including the obvious "are the pins
    current?" check -- also keeps the last value, so it agrees with the runtime
    and the dead blocks stay invisible. This reads the SOURCE LITERAL with
    `ast` instead, which is the only place the duplication exists.
    """
    tree = ast.parse(pathlib.Path(__file__).read_text(encoding="utf-8"))
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if getattr(node.targets[0], "id", "") != name:
            continue
        if not isinstance(node.value, ast.Dict):
            continue
        seen: set[Any] = set()
        duplicates: list[str] = []
        for key in node.value.keys:
            value = getattr(key, "value", None)
            if value in seen:
                duplicates.append(f"{value} (line {key.lineno})")
            seen.add(value)
        return duplicates
    raise RefereeError(f"no dict literal named {name} in this file")


def self_test() -> int:
    # The registries are user data, not fixture data: the C4b sitting filled
    # them with real reviewed decisions, and the synthetic ledgers below reuse
    # real slugs (1604e-2018 is the fixture slug), so an entry for a real cell
    # would hit the fail-closed "reviewed resolution the producer did not
    # apply" guard inside a fixture that never claimed to have applied it.
    # Empty both for the duration of the fixtures -- the guard itself is
    # exercised deliberately by the composite fixtures via with_registry --
    # and restore whatever was registered before returning.
    _saved_resolutions = dict(review_registry.REVIEWED_LEDGER_RESOLUTIONS)
    _saved_transitions = dict(review_registry.REVIEWED_LEDGER_TRANSITIONS)
    review_registry.REVIEWED_LEDGER_RESOLUTIONS.clear()
    review_registry.REVIEWED_LEDGER_TRANSITIONS.clear()
    try:
        return _self_test_body(_saved_resolutions, _saved_transitions)
    finally:
        review_registry.REVIEWED_LEDGER_RESOLUTIONS.clear()
        review_registry.REVIEWED_LEDGER_RESOLUTIONS.update(_saved_resolutions)
        review_registry.REVIEWED_LEDGER_TRANSITIONS.clear()
        review_registry.REVIEWED_LEDGER_TRANSITIONS.update(_saved_transitions)


def _self_test_body(_saved_resolutions, _saved_transitions) -> int:
    # F231, proven able to fail by the mutation below: a pin dict whose source
    # literal repeats a key is a silent liar, because only the last one is
    # live. Asked of the SOURCE, never of the resolved dict.
    for _pin_dict in ("EXPECTED_HTML_STRUCTURE_SHA256", "AUDIT_DEPENDENCY_SHA256"):
        _duplicates = source_literal_duplicate_keys(_pin_dict)
        assert not _duplicates, (
            f"{_pin_dict} repeats keys, so only the last is live: "
            f"{_duplicates[:5]}")

    # The W6/F227 slot-input style grammar, proven able to FAIL. The accept
    # path is exercised for real by every corpus run (1604CF, 1701MS and 2316
    # ship top-inset slot inputs); these probes prove the REJECT paths exist,
    # so the grammar cannot silently widen into a styling channel.
    assert slot_input_style_ok(None)
    assert slot_input_style_ok("inset:0.18pt 0pt 0pt 0pt")
    assert not slot_input_style_ok("inset:0.18pt 0pt 1pt 0pt"), (
        "a non-top-only inset must be outside the grammar")
    assert not slot_input_style_ok("color:red"), (
        "a non-inset property must be outside the grammar")
    assert not slot_input_style_ok("inset:0.18pt 0pt 0pt 0pt;color:red"), (
        "a second declaration must be outside the grammar")

    assert parse_transform("matrix(1,0,0,-1,0,100)").point(3, 20) == (3, 80)
    translated = parse_transform("translate(10 20) scale(2)")
    assert translated.point(1, 1) == (12, 22)
    assert Matrix(a=10, d=1).stroke_scale() == 10
    try:
        parse_transform("rotate(0.5turn)")
    except RefereeError:
        pass
    else:
        raise AssertionError("CSS angle units were interpreted as SVG degrees")

    bounded = run_bounded_subprocess(
        [sys.executable, "-c", "print('bounded-self-test')"],
        timeout_seconds=2.0,
        label="bounded self-test",
    )
    assert bounded.returncode == 0
    assert bounded.stdout.strip() == "bounded-self-test"
    try:
        run_bounded_subprocess(
            [
                sys.executable,
                "-c",
                (
                    "import subprocess,sys,time;"
                    "subprocess.Popen([sys.executable,'-c',"
                    "\"import time;time.sleep(10)\"]);"
                    "time.sleep(10)"
                ),
            ],
            timeout_seconds=0.1,
            label="bounded timeout self-test",
        )
    except RefereeError as error:
        assert "fixed 0.1-second deadline" in str(error)
        assert SUBPROCESS_CLEANUP_POLICY in str(error)
    else:
        raise AssertionError("bounded subprocess timeout was not enforced")

    subpaths, unsupported, malformed = path_subpaths(
        "M 1 2 L 3 2 L 3 9 L 1 9 Z")
    assert not unsupported and not malformed
    assert len(subpaths) == 1 and subpaths[0][1]
    _subpaths, unsupported, malformed = path_subpaths(
        "M 0 0 C 1 2 3 4 5 6")
    assert unsupported and not malformed
    assert bbox(unsupported[0]) == (0.0, 0.0, 5.0, 6.0)
    _subpaths, unsupported, malformed = path_subpaths(
        "M 10 10 c 1 2 3 4 5 6 s 2 3 4 5")
    assert unsupported and not malformed
    assert bbox(unsupported[0]) == (10.0, 10.0, 19.0, 21.0)
    _subpaths, unsupported, malformed = path_subpaths(
        "M 10 10 A 5 3 0 0 1 20 10")
    assert unsupported and not malformed
    arc_box = bbox(unsupported[0])
    assert arc_box[0] <= 10 and arc_box[2] >= 20
    assert is_axis_aligned_rectangle([(1, 2), (3, 2), (3, 9), (1, 9)])
    assert not is_axis_aligned_rectangle([(1, 2), (3, 2), (2, 9)])

    with tempfile.TemporaryDirectory(prefix="comb-referee-self-test-") as temp:
        svg_path = pathlib.Path(temp) / "synthetic.svg"
        svg_path.write_text(
            '<svg xmlns="http://www.w3.org/2000/svg" '
            'viewBox="0 0 100 100">'
            '<defs><path id="glyph-white" '
            'd="M 0 0 L 3 0 L 3 3 L 0 3 Z"/>'
            '<symbol id="glyph-symbol" viewBox="0 0 10 10">'
            '<path d="M0 0L10 0L10 10L0 10Z"/></symbol></defs>'
            '<path id="triangle" d="M 10 10 L 20 10 L 15 30 Z" fill="#000"/>'
            '<path id="nonpainting" d="M 20 40 L 30 40 L 25 50 Z" '
            'fill="none" stroke="none"/>'
            '<g clip-path="url(#clip)"><rect id="clipped" x="30" y="10" '
            'width="1" height="20" fill="#000"/></g>'
            '<rect id="style-clipped" x="35" y="10" width="1" height="20" '
            'fill="#000" style="clip-path:url(#clip)"/>'
            '<line id="diagonal" x1="40" y1="10" x2="50" y2="30" '
            'stroke="#000" stroke-width="1"/>'
            '<line id="near-diagonal" x1="60" y1="10" x2="60.2" y2="30" '
            'stroke="#000" stroke-width="1"/>'
            '<line id="outside-position-bound" '
            'x1="65" y1="10" x2="65.6" y2="30" '
            'stroke="#000" stroke-width="1"/>'
            '<line id="dashed" x1="55" y1="10" x2="55" y2="30" '
            'stroke="#000" style="stroke-dasharray:1 1"/>'
            '<rect id="translucent" x="70" y="10" width="1" height="20" '
            'fill="#000" opacity="0.5"/>'
            '<rect id="clamped-opacity" x="72" y="10" width="1" height="20" '
            'fill="#000" opacity="2"/>'
            '<rect id="reordered" x="75" y="10" width="1" height="20" '
            'fill="#fff" stroke="#000" '
            'style="paint-order:stroke fill"/>'
            '<path id="curve" d="M 80 80 C 82 82 84 84 86 86" '
            'fill="none" stroke="#000"/>'
            '<path id="anisotropic-curve" transform="scale(10 1)" '
            'd="M 0 60 C 0.5 61 1.5 61 2 60" '
            'fill="none" stroke="#000"/>'
            '<rect id="glyph-base" x="90" y="10" width="1" height="20" '
            'fill="#000"/>'
            '<use id="glyph-knockout" href="#glyph-white" x="89" y="12" '
            'fill="#fff"/>'
            '<line id="anisotropic-line" transform="scale(1 10)" '
            'x1="50" y1="1" x2="50" y2="3" '
            'stroke="#000" stroke-width="0.2"/>'
            '<path id="evenodd" fill="#000" fill-rule="evenodd" '
            'd="M9.9 10L10.1 10L10.1 30L9.9 30Z'
            'M9.9 10L10.1 10L10.1 30L9.9 30Z"/>'
            '<g visibility="HIDDEN"><rect id="visible-child" '
            'visibility="VISIBLE" x="45" y="10" width="1" height="20"/></g>'
            '<g id="glyph-visible-child"><g visibility="hidden">'
            '<path visibility="visible" fill="#fff" '
            'd="M0 0L3 0L3 3L0 3Z"/></g></g>'
            '<use id="glyph-visible-child-use" href="#glyph-visible-child" '
            'x="20" y="20" fill="#fff"/>'
            '<line id="marked" x1="5" y1="5" x2="6" y2="6" '
            'stroke="#000" marker-start="url(#m)"/>'
            '<switch id="conditional"><rect systemLanguage="zz" '
            'x="5" y="10" width="1" height="20"/></switch>'
            '<use id="glyph-symbol-use" href="#glyph-symbol" '
            'x="10" y="2" width="20" height="6" fill="#fff"/>'
            '<rect id="negative-rect" x="20" y="20" '
            'width="-1" height="2" fill="#000"/>'
            '</svg>',
            encoding="utf-8",
        )
        parsed_svg = parse_svg(svg_path)
        assert any(region.reason == "non-rectangular closed SVG fill"
                   for region in parsed_svg.unsupported)
        assert any(region.reason == "diagonal SVG line"
                   for region in parsed_svg.unsupported)
        assert any(paint.element == "near-diagonal"
                   and paint.kind == "near-vertical-line"
                   for paint in parsed_svg.paints)
        assert any(region.element == "outside-position-bound"
                   for region in parsed_svg.unsupported)
        assert any(paint.element == "clipped" and paint.clipped
                   for paint in parsed_svg.paints)
        assert any(paint.element == "style-clipped" and paint.clipped
                   for paint in parsed_svg.paints)
        assert any(paint.element == "dashed" and paint.clipped
                   for paint in parsed_svg.paints)
        assert any(paint.element == "translucent" and paint.clipped
                   for paint in parsed_svg.paints)
        assert any(paint.element == "clamped-opacity" and paint.tone == 0.0
                   for paint in parsed_svg.paints)
        assert any(paint.element == "reordered" and paint.clipped
                   for paint in parsed_svg.paints)
        assert any(paint.element == "anisotropic-line" and paint.clipped
                   for paint in parsed_svg.paints)
        assert any(paint.element == "visible-child"
                   for paint in parsed_svg.paints)
        assert any(region.element == "evenodd"
                   and "compound SVG fill" in region.reason
                   for region in parsed_svg.unsupported)
        assert any(region.element == "marked"
                   and region.reason == "SVG marker paint is not resolved"
                   for region in parsed_svg.unsupported)
        assert any(region.element == "conditional"
                   and "switch conditional" in region.reason
                   for region in parsed_svg.unsupported)
        assert any(region.element == "glyph-symbol-use"
                   and "glyph symbol viewport" in region.reason
                   for region in parsed_svg.unsupported)
        assert any(region.element == "negative-rect"
                   and region.reason == "negative SVG rect extent"
                   for region in parsed_svg.unsupported)
        assert not any(region.element == "nonpainting"
                       for region in parsed_svg.unsupported)
        curve = next(region for region in parsed_svg.unsupported
                     if region.element == "curve")
        assert curve.x0 > 70 and curve.y0 > 70
        anisotropic = next(
            region for region in parsed_svg.unsupported
            if region.element == "anisotropic-curve")
        assert anisotropic.x0 <= -19 and anisotropic.x1 >= 39
        knockout = next(
            region for region in parsed_svg.unsupported
            if region.element == "glyph-knockout")
        assert knockout.tone == 1.0 and knockout.order > 0
        assert any(
            region.element == "glyph-visible-child-use"
            and region.tone == 1.0
            for region in parsed_svg.unsupported
        )

        styled_path = pathlib.Path(temp) / "styled.svg"
        styled_path.write_text(
            '<svg xmlns="http://www.w3.org/2000/svg" '
            'viewBox="0 0 100 100"><defs><style>'
            '.divider{fill:#fff}</style></defs>'
            '<rect class="divider" x="10" y="10" '
            'width="1" height="20"/></svg>',
            encoding="utf-8",
        )
        styled_svg = parse_svg(styled_path)
        assert any(
            region.reason == "embedded SVG stylesheet is not resolved"
            and region.x0 == 0 and region.y0 == 0
            and region.x1 == 100 and region.y1 == 100
            for region in styled_svg.unsupported
        )
        invalid_svg_fragments = {
            "important":
                '<rect style="visibility:hidden!important" '
                'x="1" y="1" width="1" height="1"/>',
            "comment":
                '<rect style="visibility:/*x*/hidden" '
                'x="1" y="1" width="1" height="1"/>',
            "inline-blend":
                '<rect style="mix-blend-mode:screen" '
                'x="1" y="1" width="1" height="1"/>',
            "attribute-blend":
                '<rect mix-blend-mode="screen" '
                'x="1" y="1" width="1" height="1"/>',
        }
        for name, fragment in invalid_svg_fragments.items():
            invalid_svg_path = pathlib.Path(temp) / f"invalid-{name}.svg"
            invalid_svg_path.write_text(
                '<svg xmlns="http://www.w3.org/2000/svg" '
                f'viewBox="0 0 10 10">{fragment}</svg>',
                encoding="utf-8",
            )
            try:
                parse_svg(invalid_svg_path)
            except RefereeError:
                pass
            else:
                raise AssertionError(
                    f"unsupported SVG CSS was accepted: {name}")

    def paint(x: float, a: float = 2, b: float = 8, order: int = 0,
              tone: float = 0.0) -> Paint:
        return Paint(x - 0.1, a, x + 0.1, b, tone, order,
                     "test", f"x{x}-o{order}")

    def source_frame() -> list[Paint]:
        return [
            Paint(-0.1, -0.1, 40.1, 0.1, 0.0, 10,
                  "stroke", "single-frame"),
            Paint(-0.1, 9.9, 40.1, 10.1, 0.0, 11,
                  "stroke", "single-frame"),
            Paint(-0.1, -0.1, 0.1, 10.1, 0.0, 12,
                  "stroke", "single-frame"),
            Paint(39.9, -0.1, 40.1, 10.1, 0.0, 13,
                  "stroke", "single-frame"),
        ]

    cell = {
        "id": "p1c0", "x0": 0.0, "y0": 0.0, "x1": 40.0, "y1": 10.0,
        "comb": {"cells": 3, "divider_x": [10.0, 30.0],
                 "pitch_pt": 10.0, "divider_gray": 0.0,
                 "y0": 2.0, "y1": 8.0},
    }
    page = SvgPage(100, 100, [paint(10), paint(20), paint(30)], [], "x")
    result = classify_band(cell, page)
    assert result["status"] == "measured", result
    assert result["compartments"] == 4, result
    assert result["extra_divider_x"] == [20.0], result
    assert result["source_rail_x"] == [0.0, 40.0], result

    # ---- the comb's own RAILS, measured from Poppler ------------------------
    #
    # Every boundary above hangs inside the box, so the rectangle's edges are
    # the rails and the count is unchanged.  Close the box at one of them and
    # the comb starts there instead: the rectangle also rules something else,
    # and the compartment beyond the rail is not this comb's.
    rail_cell = {
        **cell,
        "comb": {**cell["comb"], "cells": 3, "divider_x": [20.0, 30.0]},
    }
    closing_wall = Paint(9.9, 0.1, 10.1, 9.9, 0.0, 20, "stroke", "wall-10")
    railed = classify_band(rail_cell, SvgPage(
        100, 100,
        [*source_frame(), closing_wall, paint(20), paint(30)], [], "x"))
    assert railed["status"] == "measured", railed
    assert railed["source_divider_x"] == [10.0, 20.0, 30.0], railed
    assert railed["source_rail_x"] == [10.0, 40.0], railed
    assert railed["compartments"] == 3, railed

    # The same shape with the junction PAINTED OUT is the sheet saying the
    # stroke stops there.  It divides nothing, so it stays a divider and the
    # comb keeps the rectangle's edges.
    erased_junction = Paint(9.8, 0.05, 10.2, 0.45, 1.0, 21, "fill", "knockout")
    unrailed = classify_band(rail_cell, SvgPage(
        100, 100,
        [*source_frame(), closing_wall, erased_junction,
         paint(20), paint(30)], [], "x"))
    assert unrailed["status"] == "measured", unrailed
    assert unrailed["source_rail_x"] == [0.0, 40.0], unrailed
    assert unrailed["compartments"] == 4, unrailed

    # A wall BETWEEN two ticks is a compartment boundary the source closed on
    # both sides -- 1801 rules its TIN dash boxes exactly so -- and a comb
    # drawn entirely from such walls keeps every one of them.
    interior_wall_cell = {
        **cell,
        "comb": {**cell["comb"], "cells": 4, "divider_x": [10.0, 20.0, 30.0]},
    }
    interior_walled = classify_band(interior_wall_cell, SvgPage(
        100, 100,
        [*source_frame(), paint(10),
         Paint(19.9, 0.1, 20.1, 9.9, 0.0, 20, "stroke", "wall-20"),
         paint(30)], [], "x"))
    assert interior_walled["status"] == "measured", interior_walled
    assert interior_walled["source_rail_x"] == [0.0, 40.0], interior_walled
    assert interior_walled["compartments"] == 4, interior_walled

    # ---- prose-refuted outer regions (R1, F232) -----------------------------
    #
    # A tick-drawn comb with no wall outside the run used to keep the
    # rectangle's edges unconditionally, counting the region between the edge
    # and the outermost tick as a compartment even where it holds the row's
    # printed caption.  More than one glyph in that region is running text,
    # and running text is not a one-character writing compartment; the rail
    # moves to the outermost tick and says so.  Exactly one glyph is the
    # pre-printed decoration a compartment may carry, and the edge stands.
    def glyph(x: float, order: int) -> UnsupportedRegion:
        return UnsupportedRegion(
            x - 1.0, 4.0, x + 1.0, 6.0,
            "glyph use may occlude geometry: fixture",
            f"glyph-{x}-{order}", tone=0.0, order=order, clipped=False)

    prose_cell = {
        "id": "p1c1", "x0": 0.0, "y0": 0.0, "x1": 40.0, "y1": 10.0,
        "comb": {"cells": 2, "divider_x": [20.0, 30.0],
                 "pitch_pt": 10.0, "divider_gray": 0.0,
                 "y0": 2.0, "y1": 8.0},
    }
    prose_refuted = classify_band(prose_cell, SvgPage(
        100, 100, [paint(20), paint(30)],
        [glyph(6.0, 30), glyph(11.0, 31)], "x"))
    assert prose_refuted["status"] == "measured", prose_refuted
    assert prose_refuted["source_rail_x"] == [20.0, 40.0], prose_refuted
    assert prose_refuted["compartments"] == 2, prose_refuted
    assert prose_refuted["rail_derivation"]["left"] == {
        "basis": "prose-refuted-outer-region",
        "from_x": 0.0, "span_pt": 20.0, "glyphs": 2,
    }, prose_refuted["rail_derivation"]
    assert prose_refuted["rail_derivation"]["right"] == {
        "basis": "owner-edge"}, prose_refuted["rail_derivation"]

    one_glyph = classify_band(prose_cell, SvgPage(
        100, 100, [paint(20), paint(30)], [glyph(6.0, 30)], "x"))
    assert one_glyph["status"] == "measured", one_glyph
    assert one_glyph["source_rail_x"] == [0.0, 40.0], one_glyph
    assert one_glyph["compartments"] == 3, one_glyph
    assert one_glyph["rail_derivation"]["left"] == {
        "basis": "owner-edge"}, one_glyph["rail_derivation"]

    # Prose AND divider-tone structure together is a conflict the clause must
    # not resolve either way: the paint at x=10 is too thick to be a divider
    # candidate (width > pitch/2), so it matched nothing, yet it is structure
    # standing in the region the glyphs would refute.  Fail closed.
    conflicted = classify_band(prose_cell, SvgPage(
        100, 100,
        [paint(20), paint(30),
         Paint(7.25, 2.0, 12.75, 8.0, 0.0, 5, "fill", "thick-structure")],
        [glyph(6.0, 30), glyph(15.0, 31)], "x"))
    assert conflicted["status"] == "unevaluable", conflicted
    assert "prose refutation cannot stand" in str(
        conflicted.get("reason")), conflicted

    # A refutation that leaves no comb band is a caption block wrongly
    # active: refuting BOTH sides of a single-tick run would publish a
    # one-compartment "comb", which is no comb at all.  Fail closed instead.
    lone_tick_cell = {
        "id": "p1c3", "x0": 0.0, "y0": 0.0, "x1": 40.0, "y1": 10.0,
        "comb": {"cells": 2, "divider_x": [20.0],
                 "pitch_pt": 20.0, "divider_gray": 0.0,
                 "y0": 2.0, "y1": 8.0},
    }
    annihilated = classify_band(lone_tick_cell, SvgPage(
        100, 100, [paint(20)],
        [glyph(6.0, 30), glyph(11.0, 31),
         glyph(26.0, 32), glyph(31.0, 33)], "x"))
    assert annihilated["status"] == "unevaluable", annihilated
    assert "prose refutation cannot stand" in str(
        annihilated.get("reason")), annihilated

    # A wall outside the run answers the question before prose is asked: the
    # region beyond it belongs to whatever the wall closes off, however much
    # text it carries.
    walled_prose = classify_band(prose_cell, SvgPage(
        100, 100,
        [*source_frame(),
         Paint(19.9, 0.1, 20.1, 9.9, 0.0, 20, "stroke", "wall-20"),
         paint(30)],
        [glyph(4.0, 30), glyph(8.0, 31), glyph(12.0, 32)], "x"))
    assert walled_prose["status"] == "measured", walled_prose
    assert walled_prose["source_rail_x"] == [20.0, 40.0], walled_prose
    assert walled_prose["rail_derivation"]["left"] == {
        "basis": "wall-outside-run", "wall_x": 20.0,
    }, walled_prose["rail_derivation"]

    # A row of full-height boxes has no tick run to sit outside of, so the
    # edges stand and no prose question is asked: a walled table compartment
    # legitimately carries text (1604CF p2c73, reviewed at 2 compartments,
    # holds 29 glyphs in its right box).
    boxes_cell = {
        "id": "p1c2", "x0": 0.0, "y0": 0.0, "x1": 40.0, "y1": 10.0,
        "comb": {"cells": 2, "divider_x": [20.0],
                 "pitch_pt": 20.0, "divider_gray": 0.0,
                 "y0": 2.0, "y1": 8.0},
    }
    walled_boxes = classify_band(boxes_cell, SvgPage(
        100, 100,
        [*source_frame(),
         Paint(19.9, 0.1, 20.1, 9.9, 0.0, 20, "stroke", "wall-20")],
        [glyph(4.0, 30), glyph(8.0, 31), glyph(12.0, 32),
         glyph(24.0, 33), glyph(28.0, 34)], "x"))
    assert walled_boxes["status"] == "measured", walled_boxes
    assert walled_boxes["source_rail_x"] == [0.0, 40.0], walled_boxes
    assert walled_boxes["compartments"] == 2, walled_boxes
    assert walled_boxes["rail_derivation"] == {
        "left": {"basis": "owner-edge"},
        "right": {"basis": "owner-edge"},
    }, walled_boxes["rail_derivation"]

    # `closes_subject_box` itself, on the two joins that are its only
    # tolerances and on the case they must not reach.
    frame_paints = source_frame()
    assert closes_subject_box(
        [*frame_paints, closing_wall], 10.0, 0.0, 10.0, 0.0)
    assert not closes_subject_box(
        [*frame_paints, paint(20)], 20.0, 0.0, 10.0, 0.0)
    hairline_break = [
        Paint(9.9, 0.1, 10.1, 5.0, 0.0, 20, "stroke", "wall-upper"),
        Paint(9.9, 5.15, 10.1, 9.9, 0.0, 21, "stroke", "wall-lower"),
    ]
    assert closes_subject_box(
        [*frame_paints, *hairline_break], 10.0, 0.0, 10.0, 0.0)
    paper_break = [
        Paint(9.9, 0.1, 10.1, 5.0, 0.0, 20, "stroke", "wall-upper"),
        Paint(9.9, 5.4, 10.1, 9.9, 0.0, 21, "stroke", "wall-lower"),
    ]
    assert not closes_subject_box(
        [*frame_paints, *paper_break], 10.0, 0.0, 10.0, 0.0)
    assert not closes_subject_box(
        [*frame_paints, closing_wall, erased_junction],
        10.0, 0.0, 10.0, 0.0)
    # A rail is a fact about the SUBJECT's box: the same ink closes nothing
    # for a rectangle it does not reach the bottom of.
    assert not closes_subject_box(
        [*frame_paints, closing_wall], 10.0, 0.0, 20.0, 0.0)

    # A source guide band may live immediately outside one cell edge because
    # the shared horizontal owns the partition.  The whole attached band is
    # evidence; clipping it to the cell would discard it as <=0.25pt noise.
    attached_above = {
        **cell,
        "y0": 20.0,
        "y1": 30.0,
        "comb": {**cell["comb"], "y0": 13.76, "y1": 19.76},
    }
    attached_above_paints = [
        paint(10, 13.76, 19.76),
        paint(20, 13.76, 19.76),
        paint(30, 13.76, 19.76),
    ]
    result = classify_band(attached_above, SvgPage(
        100, 100, attached_above_paints, [], "x"))
    assert result["status"] == "measured", result
    assert result["compartments"] == 4, result

    # The attached-band retry must not borrow a different rectangle in the
    # original cell to justify an empty multi-pitch gap in the external band.
    # Only the two anchors are painted in that band; the missing midpoint is
    # safe solely when the same evaluation window has its own single-frame
    # subject proof.
    unrelated_cell_frame = [
        Paint(-0.1, 19.9, 40.1, 20.1, 0.0, 10,
              "stroke", "unrelated-cell-frame"),
        Paint(-0.1, 29.9, 40.1, 30.1, 0.0, 11,
              "stroke", "unrelated-cell-frame"),
        Paint(-0.1, 19.9, 0.1, 30.1, 0.0, 12,
              "stroke", "unrelated-cell-frame"),
        Paint(39.9, 19.9, 40.1, 30.1, 0.0, 13,
              "stroke", "unrelated-cell-frame"),
    ]
    attached_gap_wrong_frame = classify_band(attached_above, SvgPage(
        100, 100,
        [paint(10, 13.76, 19.76), paint(30, 13.76, 19.76),
         *unrelated_cell_frame],
        [], "x"))
    assert attached_gap_wrong_frame["status"] == "unevaluable", (
        attached_gap_wrong_frame)
    assert attached_gap_wrong_frame["reason"] == (
        "chosen source topology lacks a clean single-frame subject proof"
    ), attached_gap_wrong_frame
    assert any(
        band.get("unproven_subject_gaps")
        for band in attached_gap_wrong_frame["bands"]
    ), attached_gap_wrong_frame

    attached_below = {
        **cell,
        "comb": {**cell["comb"], "y0": 10.24, "y1": 16.24},
    }
    result = classify_band(attached_below, SvgPage(
        100, 100,
        [paint(10, 10.24, 16.24),
         paint(20, 10.24, 16.24),
         paint(30, 10.24, 16.24)],
        [], "x"))
    assert result["status"] == "measured", result
    assert result["compartments"] == 4, result

    detached = {
        **cell,
        "comb": {**cell["comb"], "y0": 10.26, "y1": 16.26},
    }
    result = classify_band(detached, SvgPage(
        100, 100,
        [paint(10, 10.26, 16.26),
         paint(20, 10.26, 16.26),
         paint(30, 10.26, 16.26)],
        [], "x"))
    assert result["status"] == "unevaluable", result
    assert result["reason"] == (
        "no common Poppler band contains every recognised divider"), result
    detached_no_retry = classify_band(
        detached,
        SvgPage(
            100, 100,
            [paint(10, 10.26, 16.26),
             paint(20, 10.26, 16.26),
             paint(30, 10.26, 16.26)],
            [], "x"),
        _evaluation_window=(0.0, 10.0),
    )
    assert result == detached_no_retry

    enveloping = {
        **cell,
        "comb": {**cell["comb"], "y0": -1.0, "y1": 11.0},
    }
    result = classify_band(enveloping, SvgPage(
        100, 100,
        [paint(10, -1, 11), paint(20, -1, 11), paint(30, -1, 11)],
        [], "x"))
    assert result["status"] == "measured", result
    assert result["compartments"] == 4, result

    # An attached band whose original cell-clipped verdict is already
    # ambiguous must not invoke the fallback.
    crossing = {
        **cell,
        "y0": 20.0,
        "y1": 30.0,
        "comb": {**cell["comb"], "y0": 19.76, "y1": 25.76},
    }
    crossing_ambiguous_page = SvgPage(
        100, 100,
        [paint(10, 19.76, 25.76),
         paint(20, 19.76, 22.0),
         paint(30, 19.76, 25.76)],
        [], "x")
    crossing_ambiguous = classify_band(crossing, crossing_ambiguous_page)
    crossing_ambiguous_no_retry = classify_band(
        crossing, crossing_ambiguous_page,
        _evaluation_window=(20.0, 30.0))
    assert crossing_ambiguous["status"] == "unevaluable", crossing_ambiguous
    assert crossing_ambiguous == crossing_ambiguous_no_retry

    crossing_minority_page = SvgPage(
        100, 100,
        [paint(10, 20.0, 22.0), paint(30, 20.0, 22.0)], [], "x")
    crossing_minority = classify_band(crossing, crossing_minority_page)
    crossing_minority_no_retry = classify_band(
        crossing, crossing_minority_page,
        _evaluation_window=(20.0, 30.0))
    assert crossing_minority["status"] == "unevaluable", crossing_minority
    assert "strict majority" in crossing_minority["reason"], crossing_minority
    assert crossing_minority == crossing_minority_no_retry

    off_band_decoy = classify_band(attached_above, SvgPage(
        100, 100,
        [*attached_above_paints, paint(15, 22, 28)], [], "x"))
    assert off_band_decoy["status"] == "measured", off_band_decoy
    assert off_band_decoy["compartments"] == 4, off_band_decoy
    assert 15.0 not in off_band_decoy["source_divider_x"], off_band_decoy

    attached_partial = classify_band(attached_above, SvgPage(
        100, 100,
        [paint(10, 13.76, 19.76),
         paint(20, 13.76, 16.66),
         paint(30, 13.76, 19.76)],
        [], "x"))
    assert attached_partial["status"] == "unevaluable", attached_partial

    attached_clipped = classify_band(attached_above, SvgPage(
        100, 100,
        [*attached_above_paints,
         Paint(24.9, 13.76, 25.1, 19.76, 0.0, 5,
               "test", "attached-clipped", True)],
        [], "x"))
    assert attached_clipped["status"] == "unevaluable", attached_clipped

    attached_outward = {
        **attached_above,
        "comb": {
            **attached_above["comb"],
            "divider_x": [20.0, 30.0],
        },
    }
    attached_off_pitch = classify_band(attached_outward, SvgPage(
        100, 100,
        [paint(5, 13.76, 19.76), paint(10, 13.76, 19.76),
         paint(20, 13.76, 19.76), paint(30, 13.76, 19.76)],
        [], "x"))
    assert attached_off_pitch["status"] == "unevaluable", attached_off_pitch

    attached_unsupported = classify_band(attached_above, SvgPage(
        100, 100, attached_above_paints,
        [UnsupportedRegion(
            5, 13.76, 35, 19.76,
            "unsupported attached overlay", "attached-overlay")],
        "x"))
    assert attached_unsupported["status"] == "unevaluable", attached_unsupported

    attached_non_majority = classify_band(attached_above, SvgPage(
        100, 100,
        [paint(30, 13.76, 14.76), paint(20, 13.76, 14.36)],
        [], "x"))
    assert attached_non_majority["status"] == "unevaluable", (
        attached_non_majority)

    def parsed_subject(anchor: float) -> dict[str, Any]:
        return {
            "id": "p1c9",
            "x0": anchor - 1.5, "y0": 9.0,
            "x1": anchor + 1.5, "y1": 31.0,
            "comb": {
                "cells": 2, "divider_x": [anchor],
                "pitch_pt": 3.0, "divider_gray": 0.0,
                "y0": 10.0, "y1": 30.0,
            },
        }

    for anchor in (30.5, 35.5, 55.0, 90.5):
        result = classify_band(parsed_subject(anchor), parsed_svg)
        assert result["status"] == "unevaluable", (anchor, result)

    # Unexplained full-height ink inside a one-pitch gap is ambiguous; it
    # cannot be silently ignored to preserve the lattice answer.
    normal = {
        **cell,
        "comb": {"cells": 3, "divider_x": [10.0, 20.0],
                 "pitch_pt": 10.0, "divider_gray": 0.0,
                 "y0": 2.0, "y1": 8.0},
    }
    result = classify_band(normal, SvgPage(
        100, 100, [paint(10), paint(15), paint(20)], [], "x"))
    assert result["status"] == "unevaluable", result

    # A final-tone component must still descend from eligible vertical ink.
    # A short square inside the width bound is not a divider candidate.
    square = classify_band(cell, SvgPage(
        100, 100, [
            paint(10), paint(30),
            Paint(18, 2, 22, 6, 0.0, 3, "fill", "square"),
            *source_frame(),
        ], [], "x"))
    assert square["status"] == "measured", square
    assert square["compartments"] == 3 and not square["extra_divider_x"], square

    erased_under_square = classify_band(cell, SvgPage(
        100, 100, [
            paint(10, order=0), paint(20, order=1), paint(30, order=2),
            Paint(19, 2, 21, 8, 1.0, 3, "fill", "white-erasure"),
            Paint(18, 2, 22, 6, 0.0, 4, "fill", "square"),
            *source_frame(),
        ], [], "x"))
    assert erased_under_square["status"] == "measured", erased_under_square
    assert (erased_under_square["compartments"] == 3
            and not erased_under_square["extra_divider_x"]), erased_under_square

    # Source steps may establish their own regular subdivision while each
    # endpoint remains inside the fixed position bound.
    irregular = {
        **cell,
        "comb": {**cell["comb"], "divider_x": [10.0, 30.2]},
    }
    result = classify_band(irregular, SvgPage(
        100, 100, [paint(10), paint(20.1), paint(30.2)], [], "x"))
    assert result["status"] == "measured" and result["compartments"] == 4, result
    assert result["extra_divider_x"] == [20.1], result

    fragmented = classify_band(cell, SvgPage(
        100, 100, [
            paint(10), paint(30),
            paint(20, a=2, b=5), paint(20, a=5, b=8),
        ], [], "x"))
    assert fragmented["status"] == "measured", fragmented
    assert fragmented["compartments"] == 4, fragmented

    nested_majority = classify_band(cell, SvgPage(
        100, 100, [
            paint(10, a=2, b=7.5), paint(20, a=2, b=7.5),
            paint(30, a=2, b=7.5),
            paint(10.2, a=7.5, b=8), paint(30.2, a=7.5, b=8),
        ], [], "x"))
    assert nested_majority["status"] == "measured", nested_majority
    assert nested_majority["compartments"] == 4, nested_majority
    assert nested_majority["seed_span_pt"] == 6.0, nested_majority
    assert nested_majority["measured_span_pt"] == 6.0, nested_majority
    assert nested_majority["chosen_topology"] == [10.0, 20.0, 30.0]
    assert nested_majority["topology_superset_relations"], nested_majority

    # A tiny richer slab cannot win by excluding the anchorless remainder from
    # its denominator.
    mostly_anchorless = classify_band(cell, SvgPage(
        100, 100, [
            paint(30, a=2, b=3),
            paint(20, a=2, b=2.6),
        ], [], "x"))
    assert mostly_anchorless["status"] == "unevaluable", mostly_anchorless

    # Rounding 25/10 to two intervals must not invent a regular subdivision.
    non_integral = {
        **cell,
        "comb": {**cell["comb"], "divider_x": [10.0, 35.0]},
    }
    result = classify_band(non_integral, SvgPage(
        100, 100, [paint(10), paint(20), paint(35)], [], "x"))
    assert result["status"] == "unevaluable", result

    # A fully observed source comb may have deliberately non-uniform
    # compartments.  Pitch is an inference aid for extra painted boundaries,
    # not permission to invent a boundary where Poppler paints none.
    non_uniform = {
        **cell,
        "comb": {**cell["comb"],
                 "cells": 4,
                 "divider_x": [7.5, 19.25, 30.25],
                 "pitch_pt": 7.5},
    }
    result = classify_band(non_uniform, SvgPage(
        100, 100,
        [paint(7.5), paint(19.25), paint(30.25), *source_frame()],
        [], "x"))
    assert result["status"] == "measured", result
    assert result["compartments"] == 4 and not result["extra_divider_x"], result
    assert result["subject_gap_proofs"], result

    # Two independent combs separated by a static-label-sized void are not one
    # non-uniform comb merely because no divider is painted in the void.
    conflated = {
        **cell,
        "x1": 100.0,
        "comb": {**cell["comb"],
                 "cells": 3,
                 "divider_x": [10.0, 80.0],
                 "pitch_pt": 10.0},
    }
    result = classify_band(conflated, SvgPage(
        100, 100, [paint(10), paint(80)], [], "x"))
    assert result["status"] == "unevaluable", result

    short_extra = classify_band(cell, SvgPage(
        100, 100, [
            paint(10), paint(30), paint(20, a=2, b=5),
        ], [], "x"))
    assert short_extra["status"] == "unevaluable", short_extra

    # A white or differently toned band is not a structural closing rule and
    # cannot crop away the part of a comb that contradicts the anchors.
    white_crop = classify_band(cell, SvgPage(
        100, 100, [
            Paint(0, 2, 40, 4.9, 1.0, 0, "fill", "white-band"),
            paint(10, order=1), paint(30, order=2),
            paint(20, a=2, b=4.8, order=3),
        ], [], "x"))
    assert white_crop["status"] == "unevaluable", white_crop
    assert white_crop["open_y0"] == 2.0, white_crop

    # The cell's own sides are not two more compartments.
    framed = classify_band(normal, SvgPage(
        100, 100, [paint(0), paint(10), paint(20), paint(40)], [], "x"))
    assert framed["compartments"] == 3 and not framed["extra_divider_x"], framed

    # A source-backed run may extend beyond the recognised anchors.
    outward = {
        **cell,
        "comb": {"cells": 3, "divider_x": [20.0, 30.0],
                 "pitch_pt": 10.0, "divider_gray": 0.0,
                 "y0": 2.0, "y1": 8.0},
    }
    result = classify_band(outward, SvgPage(
        100, 100, [paint(0), paint(10), paint(20), paint(30), paint(40)], [], "x"))
    assert result["compartments"] == 4, result
    assert result["extra_divider_x"] == [10.0], result

    # Edge bisection cannot overrule final gaps that disagree with the measured
    # pitch.
    edge_split = {
        **cell,
        "comb": {"cells": 3, "divider_x": [20.0, 31.0],
                 "pitch_pt": 9.0, "divider_gray": 0.0,
                 "y0": 2.0, "y1": 8.0},
    }
    result = classify_band(edge_split, SvgPage(
        100, 100, [paint(0), paint(10), paint(20), paint(31), paint(40)], [], "x"))
    assert result["status"] == "unevaluable", result

    # A nearer off-pitch stroke cannot be skipped to reach a farther convenient
    # continuation.
    outward_blocked = {
        **cell,
        "comb": {**cell["comb"], "divider_x": [20.0, 30.0]},
    }
    result = classify_band(outward_blocked, SvgPage(
        100, 100, [paint(5), paint(10), paint(20), paint(30)], [], "x"))
    assert result["status"] == "unevaluable", result

    # An off-pitch vertical in a broad mixed interval makes ownership
    # ambiguous; it cannot be silently skipped.
    broad = {
        **cell,
        "comb": {"cells": 2, "divider_x": [30.0],
                 "pitch_pt": 10.0, "divider_gray": 0.0,
                 "y0": 2.0, "y1": 8.0},
        "x1": 100.0,
    }
    result = classify_band(broad, SvgPage(
        100, 100, [paint(0), paint(30), paint(55), paint(100)], [], "x"))
    assert result["status"] == "unevaluable", result

    # A pitch-aligned grey decoration is not a black compartment boundary.
    grey = classify_band(outward, SvgPage(
        100, 100, [paint(0), paint(10, tone=0.5),
                   paint(20), paint(30), paint(40)], [], "x"))
    assert grey["compartments"] == 3 and not grey["extra_divider_x"], grey

    # Two thick bars with too little paper between them are one frame edge.
    composite = {
        **cell,
        "x1": 42.0,
        "comb": {"cells": 4, "divider_x": [10.0, 20.0, 30.0],
                 "pitch_pt": 10.0, "divider_gray": 0.0,
                 "y0": 2.0, "y1": 8.0},
    }
    result = classify_band(composite, SvgPage(
        100, 100, [
            paint(0), paint(10), paint(20), paint(30),
            Paint(39.0, 2, 41.0, 8, 0.0, 0, "test", "inner-frame"),
            Paint(40.5, 2, 43.5, 8, 0.0, 0, "test", "outer-frame"),
        ], [], "x"))
    assert result["compartments"] == 4 and not result["extra_divider_x"], result

    # A declared anchor does not make the inner bar of a composite frame into
    # a writable divider; anchor matching itself uses frame-distinct groups.
    composite_anchor = {
        **cell,
        "comb": {
            "cells": 2,
            "divider_x": [1.5],
            "pitch_pt": 3.0,
            "divider_gray": 0.0,
            "y0": 2.0,
            "y1": 8.0,
        },
    }
    composite_anchor_result = classify_band(
        composite_anchor,
        SvgPage(100, 100, [
            *source_frame(),
            Paint(1.0, 2, 2.0, 8, 0.0, 14,
                  "stroke", "declared-inner-frame-bar"),
        ], [], "x"),
    )
    assert composite_anchor_result["status"] == "unevaluable", (
        composite_anchor_result)

    broad_frame_result = classify_band(
        {
            **cell,
            "comb": {
                "cells": 2,
                "divider_x": [2.5],
                "pitch_pt": 3.0,
                "divider_gray": 0.0,
                "y0": 2.0,
                "y1": 8.0,
            },
        },
        SvgPage(100, 100, [
            Paint(-1.0, 2, 1.0, 8, 0.0, 0,
                  "stroke", "broad-outer-frame"),
            Paint(2.0, 2, 3.0, 8, 0.0, 1,
                  "stroke", "narrow-inner-frame"),
        ], [], "x"),
    )
    assert broad_frame_result["status"] == "unevaluable", broad_frame_result

    shifted = classify_band(normal, SvgPage(
        100, 100, [paint(7), paint(17)], [], "x"))
    assert shifted["status"] == "unevaluable", shifted

    # Missing layout anchors leave a smaller source topology ambiguous.
    stale = {
        **cell,
        "comb": {"cells": 3, "divider_x": [20.0, 23.0],
                 "pitch_pt": 3.0, "divider_gray": 0.0,
                 "y0": 2.0, "y1": 8.0},
    }
    erased_stale_page = SvgPage(100, 100, [
        paint(20, order=0),
        paint(23, order=1),
        Paint(22.7, 2, 23.3, 8, 1.0, 2,
              "fill", "supported-white-erasure"),
    ], [], "x")
    result = classify_band(stale, erased_stale_page)
    assert result["status"] == "unevaluable", result

    # A missing lattice anchor is independently measurable only for an
    # already-active unresolved subject whose one observed topology occupies
    # the complete open band.  The source count comes from observed final ink;
    # the retained path remains closed so an ordinary table rail cannot become
    # a newly discovered comb.
    active_partial = classify_band(
        stale, erased_stale_page, ledger_state="active_unresolved")
    assert active_partial["status"] == "measured", active_partial
    assert active_partial["compartments"] == 2, active_partial
    assert active_partial["anchors_complete"] is False, active_partial
    assert active_partial["positions_match"] is False, active_partial
    assert active_partial["missing_anchor_x"] == [23.0], active_partial
    partial_certificate = active_partial.get(
        "active_partial_anchor_certificate")
    assert isinstance(partial_certificate, dict), active_partial
    assert partial_certificate == {
        "criterion": ACTIVE_PARTIAL_ANCHOR_CRITERION,
        "valid": True,
        "ledger_state": "active_unresolved",
        "subject_ownership_basis": "active_unresolved lattice ledger",
        "independent_source_enclosure_proven": False,
        "divider_count_basis": "final-composited Poppler vector topology",
        "missing_anchor_basis": (
            "raw target-tone rail exhaustively replaced by one supported "
            "unclipped non-target final owner"
        ),
        "anchor_corridor_clipped_paint_elements": [],
        "anchor_corridor_unsupported_region_elements": [],
        "open_y0": 2.0,
        "open_y1": 8.0,
        "coverage_pt": 6.0,
        "source_divider_x": [20.0],
        "observed_anchor_x": [20.0],
        "missing_anchor_x": [23.0],
        "missing_anchor_proofs": [{
            "layout_x": 23.0,
            "corridor_x0": 22.75,
            "corridor_x1": 23.25,
            "proof_x0": 22.75,
            "proof_x1": 23.25,
            "open_y0": 2.0,
            "open_y1": 8.0,
            "raw_anchor_rails": [{
                "element": "x23-o1",
                "order": 1,
                "kind": "test",
                "x0": 22.9,
                "x1": 23.1,
                "center_x": 23.0,
                "delta_pt": 0.0,
                "y0": 2,
                "y1": 8,
                "tone": 0.0,
                "clipped": False,
            }],
            "raw_rail_identity_valid": True,
            "proof_top_role_ambiguities": [],
            "erasure_slabs": [{
                "y0": 2.0,
                "y1": 8.0,
                "sample_y": 5.0,
                "raw_rail_elements": ["x23-o1"],
                "raw_intervals": [[22.9, 23.1]],
                "final_owner_segments": [{
                    "x0": 22.9,
                    "x1": 23.1,
                    "element": "supported-white-erasure",
                    "order": 2,
                    "kind": "fill",
                    "tone": 1.0,
                    "clipped": False,
                }],
                "ambiguous_top_roles": [],
            }],
            "erasure_owner_roles": [{
                "element": "supported-white-erasure",
                "order": 2,
                "kind": "fill",
                "tone": 1.0,
            }],
            "clipped_paint_elements": [],
            "final_target_tone_segments": [],
            "unsupported_region_elements": [],
        }],
    }, partial_certificate
    for ineligible_state in (None, "active_resolved", "retained_unresolved"):
        ineligible = classify_band(
            stale, erased_stale_page, ledger_state=ineligible_state)
        assert ineligible["status"] == "unevaluable", (
            ineligible_state, ineligible)

    # Ledger ownership is necessary but not sufficient: a lone active rail
    # with no erased source rail at the declared missing anchor stays closed.
    lone_active_rail = classify_band(
        stale,
        SvgPage(100, 100, [paint(20)], [], "x"),
        ledger_state="active_unresolved",
    )
    assert lone_active_rail["status"] == "unevaluable", lone_active_rail

    # The partial path applies the same paper-versus-ink proximity test as the
    # complete path.  An inner bar separated from a frame edge by less paper
    # than their combined weights is not a writable compartment boundary.
    composite_partial = {
        **cell,
        "comb": {
            "cells": 3,
            "divider_x": [1.5, 4.5],
            "pitch_pt": 3.0,
            "divider_gray": 0.0,
            "y0": 2.0,
            "y1": 8.0,
        },
    }
    composite_partial_result = classify_band(
        composite_partial,
        SvgPage(100, 100, [
            *source_frame(),
            Paint(1.0, 2, 2.0, 8, 0.0, 14,
                  "stroke", "inner-frame-bar"),
        ], [], "x"),
        ledger_state="active_unresolved",
    )
    assert composite_partial_result["status"] == "unevaluable", (
        composite_partial_result)

    # Every certificate condition is fail closed: incomplete band coverage,
    # competing topology, an inexact or incomplete raw rail, incomplete or
    # mixed final erasure, clipping, unsupported/glyph/raster geometry, or
    # surviving target-tone ink prevents the active-only exception.
    partial_coverage = classify_band(
        stale,
        SvgPage(100, 100, [
            paint(20, a=2, b=7, order=0),
            paint(23, order=1),
            Paint(22.7, 2, 23.3, 8, 1.0, 2,
                  "fill", "supported-white-erasure"),
        ], [], "x"),
        ledger_state="active_unresolved",
    )
    assert partial_coverage["status"] == "unevaluable", partial_coverage

    competing_partial = {
        **stale,
        "comb": {**stale["comb"],
                 "cells": 4, "divider_x": [20.0, 23.0, 26.0]},
    }
    competing_topologies = classify_band(
        competing_partial,
        SvgPage(100, 100, [
            paint(20, order=0),
            paint(26, a=4, b=8, order=1),
            paint(23, order=2),
            Paint(22.7, 2, 23.3, 8, 1.0, 3,
                  "fill", "supported-white-erasure"),
        ], [], "x"),
        ledger_state="active_unresolved",
    )
    assert competing_topologies["status"] == "unevaluable", (
        competing_topologies)

    clipped_observed_anchor = classify_band(
        stale,
        SvgPage(100, 100, [
            Paint(19.9, 2, 20.1, 8, 0.0, 2,
                  "test", "observed-anchor-clip", True),
            paint(23, order=3),
            Paint(22.7, 2, 23.3, 8, 1.0, 4,
                  "fill", "supported-white-erasure"),
        ], [], "x"),
        ledger_state="active_unresolved",
    )
    assert clipped_observed_anchor["status"] == "unevaluable", (
        clipped_observed_anchor)

    incomplete_raw_rail = classify_band(
        stale,
        SvgPage(100, 100, [
            paint(20, order=0),
            paint(23, a=2, b=7, order=1),
            Paint(22.7, 2, 23.3, 8, 1.0, 2,
                  "fill", "supported-white-erasure"),
        ], [], "x"),
        ledger_state="active_unresolved",
    )
    assert incomplete_raw_rail["status"] == "unevaluable", (
        incomplete_raw_rail)

    shifted_raw_fragments = classify_band(
        stale,
        SvgPage(100, 100, [
            paint(20, order=0),
            paint(22.9, a=2, b=5, order=1),
            paint(23.1, a=5, b=8, order=2),
            Paint(22.6, 2, 23.4, 8, 1.0, 3,
                  "fill", "supported-white-erasure"),
        ], [], "x"),
        ledger_state="active_unresolved",
    )
    assert shifted_raw_fragments["status"] == "unevaluable", (
        shifted_raw_fragments)

    inexact_raw_rail = classify_band(
        stale,
        SvgPage(100, 100, [
            paint(20, order=0),
            paint(23.3, order=1),
            Paint(22.7, 2, 23.5, 8, 1.0, 2,
                  "fill", "supported-white-erasure"),
        ], [], "x"),
        ledger_state="active_unresolved",
    )
    assert inexact_raw_rail["status"] == "unevaluable", inexact_raw_rail

    overlapping_missing_anchors = {
        **stale,
        "comb": {
            **stale["comb"],
            "cells": 4,
            "divider_x": [20.0, 23.0, 23.3],
            "pitch_pt": 0.3,
        },
    }
    shared_raw_rail = classify_band(
        overlapping_missing_anchors,
        SvgPage(100, 100, [
            Paint(19.95, 2, 20.05, 8, 0.0, 0,
                  "stroke", "observed-rail"),
            Paint(23.1, 2, 23.2, 8, 0.0, 1,
                  "stroke", "ambiguous-raw-rail"),
            Paint(22.8, 2, 23.5, 8, 1.0, 2,
                  "fill", "supported-white-erasure"),
        ], [], "x"),
        ledger_state="active_unresolved",
    )
    assert shared_raw_rail["status"] == "unevaluable", shared_raw_rail

    close_observed_and_missing = {
        **stale,
        "comb": {
            **stale["comb"],
            "cells": 3,
            "divider_x": [20.0, 20.3],
            "pitch_pt": 0.3,
        },
    }
    raw_near_observed_anchor = classify_band(
        close_observed_and_missing,
        SvgPage(100, 100, [
            Paint(19.95, 2, 20.05, 8, 0.0, 0,
                  "stroke", "observed-rail"),
            Paint(20.1, 2, 20.2, 8, 0.0, 1,
                  "stroke", "ambiguous-raw-rail"),
            Paint(20.075, 2, 20.225, 8, 1.0, 2,
                  "fill", "supported-white-erasure"),
        ], [], "x"),
        ledger_state="active_unresolved",
    )
    assert raw_near_observed_anchor["status"] == "unevaluable", (
        raw_near_observed_anchor)

    incomplete_erasure = classify_band(
        stale,
        SvgPage(100, 100, [
            paint(20, order=0),
            paint(23, order=1),
            Paint(22.7, 2, 23.3, 7, 1.0, 2,
                  "fill", "incomplete-white-erasure"),
        ], [], "x"),
        ledger_state="active_unresolved",
    )
    assert incomplete_erasure["status"] == "unevaluable", (
        incomplete_erasure)

    mixed_erasure = classify_band(
        stale,
        SvgPage(100, 100, [
            paint(20, order=0),
            paint(23, order=1),
            Paint(22.7, 2, 23.0, 8, 1.0, 2,
                  "fill", "left-white-erasure"),
            Paint(23.0, 2, 23.3, 8, 1.0, 3,
                  "fill", "right-white-erasure"),
        ], [], "x"),
        ledger_state="active_unresolved",
    )
    assert mixed_erasure["status"] == "unevaluable", mixed_erasure

    broad_raw_rail = {
        **stale,
        "comb": {**stale["comb"], "pitch_pt": 4.0},
    }
    split_wide_erasure = classify_band(
        broad_raw_rail,
        SvgPage(100, 100, [
            paint(20, order=0),
            Paint(22.0, 2, 24.0, 8, 0.0, 1,
                  "stroke", "wide-raw-anchor-rail"),
            Paint(21.9, 2, 22.75, 8, 1.0, 2,
                  "fill", "wide-erasure-left"),
            Paint(22.75, 2, 23.25, 8, 1.0, 3,
                  "fill", "wide-erasure-core"),
            Paint(23.25, 2, 24.1, 8, 1.0, 4,
                  "fill", "wide-erasure-right"),
        ], [], "x"),
        ledger_state="active_unresolved",
    )
    assert split_wide_erasure["status"] == "unevaluable", (
        split_wide_erasure)

    ambiguous_erasure = classify_band(
        stale,
        SvgPage(100, 100, [
            paint(20, order=0),
            paint(23, order=1),
            Paint(22.7, 2, 23.3, 8, 1.0, 2,
                  "fill", "white-erasure-a"),
            Paint(22.7, 2, 23.3, 8, 1.0, 2,
                  "fill", "white-erasure-b"),
        ], [], "x"),
        ledger_state="active_unresolved",
    )
    assert ambiguous_erasure["status"] == "unevaluable", (
        ambiguous_erasure)

    outside_raw_tie = classify_band(
        stale,
        SvgPage(100, 100, [
            *erased_stale_page.paints,
            Paint(23.15, 2, 23.2, 8, 0.5, 3,
                  "fill", "outside-raw-owner-a"),
            Paint(23.15, 2, 23.2, 8, 0.75, 3,
                  "fill", "outside-raw-owner-b"),
        ], [], "x"),
        ledger_state="active_unresolved",
    )
    assert outside_raw_tie["status"] == "unevaluable", outside_raw_tie

    clipped_missing_anchor = classify_band(
        stale,
        SvgPage(100, 100, [
            paint(20, order=0), paint(23, order=1),
            Paint(22.7, 2, 23.3, 8, 1.0, 2,
                  "fill", "missing-anchor-clip", True),
        ], [], "x"),
        ledger_state="active_unresolved",
    )
    assert clipped_missing_anchor["status"] == "unevaluable", (
        clipped_missing_anchor)

    for unsupported_reason, unsupported_element in (
        ("glyph use may occlude geometry: #glyph-missing",
         "missing-anchor-glyph"),
        ("embedded raster image intersects source geometry",
         "missing-anchor-raster"),
        ("unsupported source overlay", "missing-anchor-unsupported"),
    ):
        unsupported_missing_anchor = classify_band(
            stale,
            SvgPage(
                100, 100, list(erased_stale_page.paints),
                [UnsupportedRegion(
                    22.9, 2, 23.1, 8,
                    unsupported_reason, unsupported_element,
                    1.0, 3, False)],
                "x",
            ),
            ledger_state="active_unresolved",
        )
        assert unsupported_missing_anchor["status"] == "unevaluable", (
            unsupported_reason, unsupported_missing_anchor)

    thin_unsupported = classify_band(
        stale,
        SvgPage(
            100, 100, list(erased_stale_page.paints),
            [UnsupportedRegion(
                22.9, 5.0, 23.1, 5.1,
                "thin unsupported source overlay",
                "thin-missing-anchor-unsupported",
                1.0, 3, False)],
            "x",
        ),
        ledger_state="active_unresolved",
    )
    assert thin_unsupported["status"] == "unevaluable", thin_unsupported

    thin_observed_unsupported = classify_band(
        stale,
        SvgPage(
            100, 100, list(erased_stale_page.paints),
            [UnsupportedRegion(
                19.9, 5.0, 20.1, 5.1,
                "thin raster over observed divider",
                "thin-observed-anchor-raster",
                1.0, 3, False)],
            "x",
        ),
        ledger_state="active_unresolved",
    )
    assert thin_observed_unsupported["status"] == "unevaluable", (
        thin_observed_unsupported)

    broad_target_at_missing_anchor = classify_band(
        stale,
        SvgPage(100, 100, [
            *erased_stale_page.paints,
            Paint(21, 2, 25, 8, 0.0, 3,
                  "fill", "broad-missing-anchor-ink"),
        ], [], "x"),
        ledger_state="active_unresolved",
    )
    assert broad_target_at_missing_anchor["status"] == "unevaluable", (
        broad_target_at_missing_anchor)

    unexplained_missing_anchor = classify_band(
        stale,
        SvgPage(100, 100, [
            *erased_stale_page.paints,
            paint(21.5, order=3),
        ], [], "x"),
        ledger_state="active_unresolved",
    )
    assert unexplained_missing_anchor["status"] == "unevaluable", (
        unexplained_missing_anchor)

    complete_active = classify_band(
        stale,
        SvgPage(100, 100, [
            paint(20), paint(23),
        ], [], "x"),
        ledger_state="active_unresolved",
    )
    assert complete_active["status"] == "measured", complete_active
    assert "active_partial_anchor_certificate" not in complete_active, (
        complete_active)

    # A short midpoint that does not prove the lattice anchor is unevaluable.
    short_midpoint = {
        **cell,
        "comb": {"cells": 2, "divider_x": [23.0],
                 "pitch_pt": 10.0, "divider_gray": 0.0,
                 "y0": 2.0, "y1": 8.0},
    }
    result = classify_band(short_midpoint, SvgPage(
        100, 100, [paint(0), paint(20, b=6), paint(40)], [], "x"))
    assert result["status"] == "unevaluable", result

    # A partially painted three-pitch gap is ambiguous, never rounded down.
    partial = {
        **cell,
        "comb": {"cells": 3, "divider_x": [10.0, 40.0],
                 "pitch_pt": 10.0, "divider_gray": 0.0,
                 "y0": 2.0, "y1": 8.0},
        "x1": 50.0,
    }
    result = classify_band(partial, SvgPage(
        100, 100, [paint(10), paint(20), paint(40)], [], "x"))
    assert result["status"] == "unevaluable", result

    # One complete slab cannot overrule a partial subdivision in another.
    mixed_partial = classify_band(partial, SvgPage(
        100, 100, [
            paint(10), paint(20), paint(40),
            paint(30, a=2, b=5),
        ], [], "x"))
    assert mixed_partial["status"] == "unevaluable", mixed_partial

    # A clipped anchor subset is ambiguous even when another slab has a
    # complete topology.
    clipped_subset = classify_band(cell, SvgPage(
        100, 100, [
            paint(10, a=2, b=6, order=0),
            paint(30, a=2, b=6, order=1),
            Paint(9.9, 6, 10.1, 8, 0.0, 2,
                  "test", "clipped-anchor", True),
        ], [], "x"))
    assert clipped_subset["status"] == "unevaluable", clipped_subset

    wide_unsupported = UnsupportedRegion(
        5, 2, 35, 8, "unsupported wide overlay", "overlay")
    result = classify_band(cell, SvgPage(
        100, 100, [paint(10), paint(20), paint(30)],
        [wide_unsupported], "x"))
    assert result["status"] == "unevaluable", result

    # A later white rectangle erases a divider.
    erased = SvgPage(100, 100, [
        paint(10, order=0), paint(20, order=1), paint(30, order=2),
        Paint(19, 0, 21, 10, 1.0, 3, "fill", "white"),
        *source_frame(),
    ], [], "x")
    result = classify_band(cell, erased)
    assert result["status"] == "measured" and result["compartments"] == 3, result

    # A clipped knockout cannot prove that the underlying divider disappeared.
    clipped_erasure = SvgPage(100, 100, [
        paint(10, order=0), paint(20, order=1), paint(30, order=2),
        Paint(19, 0, 21, 10, 1.0, 3, "fill", "clipped-white", True),
    ], [], "x")
    result = classify_band(cell, clipped_erasure)
    assert result["status"] == "unevaluable", result

    # Any later opaque paint owns the final pixel; a grey overpaint hides black.
    grey_overpaint = SvgPage(100, 100, [
        paint(10, order=0), paint(20, order=1), paint(30, order=2),
        Paint(19, 0, 21, 10, 0.5, 3, "fill", "grey"),
        *source_frame(),
    ], [], "x")
    result = classify_band(cell, grey_overpaint)
    assert result["status"] == "measured" and result["compartments"] == 3, result

    # A broad same-tone fill removes the distinct narrow boundary even though
    # the final pixels remain black.
    black_overpaint = SvgPage(100, 100, [
        paint(10, order=0), paint(20, order=1), paint(30, order=2),
        Paint(15, 0, 25, 10, 0.0, 3, "fill", "broad-black"),
        *source_frame(),
    ], [], "x")
    result = classify_band(cell, black_overpaint)
    assert result["status"] == "measured" and result["compartments"] == 3, result

    # Final topology is independent of whether a same-tone background was
    # painted before or after the narrow mark.
    black_underpaint = SvgPage(100, 100, [
        Paint(15, 0, 25, 10, 0.0, 0, "fill", "broad-black"),
        paint(10, order=1), paint(20, order=2), paint(30, order=3),
        *source_frame(),
    ], [], "x")
    result = classify_band(cell, black_underpaint)
    assert result["status"] == "measured" and result["compartments"] == 3, result

    same_tone_glyph = UnsupportedRegion(
        15, 2, 25, 8, "glyph use may occlude geometry: #glyph-x",
        "glyph", 0.0, 4, False)
    result = classify_band(cell, SvgPage(
        100, 100, [paint(10), paint(20), paint(30)],
        [same_tone_glyph], "x"))
    assert result["status"] == "unevaluable", result

    # A conservative glyph bound touching only the cell-side frame cannot
    # occlude an eligible interior divider.  It must not make an otherwise
    # exhaustive source topology unevaluable.
    edge_glyph = UnsupportedRegion(
        -1, 2, 0.2, 8, "glyph use may occlude geometry: #glyph-edge",
        "glyph", 0.0, 4, False)
    result = classify_band(cell, SvgPage(
        100, 100, [paint(0), paint(10), paint(20), paint(30)],
        [edge_glyph], "x"))
    assert result["status"] == "measured", result
    assert result["compartments"] == 4, result

    # Some official fixed text is encoded as broad curved outlines instead of
    # glyph-use nodes.  A broad curve wholly inside a compartment cannot be a
    # straight divider and cannot occlude one.  A narrow curve, or a broad
    # curve crossing an actual divider, remains unevaluable.
    broad_curve_inside = UnsupportedRegion(
        12, 2, 18, 8, "curved SVG path", "fixed-outline", 0.0, 4, False)
    result = classify_band(cell, SvgPage(
        100, 100, [paint(10), paint(20), paint(30)],
        [broad_curve_inside], "x"))
    assert result["status"] == "measured", result
    narrow_curve = UnsupportedRegion(
        19.8, 2, 20.2, 8, "curved SVG path",
        "narrow-curve", 0.0, 4, False)
    assert classify_band(cell, SvgPage(
        100, 100, [paint(10), paint(20), paint(30)],
        [narrow_curve], "x"))["status"] == "unevaluable"
    short_narrow_curve = UnsupportedRegion(
        15, 7.6, 15.4, 8, "curved SVG path",
        "short-narrow-curve", 0.0, 4, False)
    result = classify_band(cell, SvgPage(
        100, 100, [paint(10), paint(20), paint(30)],
        [short_narrow_curve], "x"))
    assert result["status"] == "measured", result
    broad_curve_crossing = UnsupportedRegion(
        15, 2, 25, 8, "curved SVG path",
        "crossing-outline", 0.0, 4, False)
    assert classify_band(cell, SvgPage(
        100, 100, [paint(10), paint(20), paint(30)],
        [broad_curve_crossing], "x"))["status"] == "unevaluable"

    # Poppler emits small arrowheads as simple closed path fills.  Preserve the
    # unsupported provenance, but let a squat unclipped bound that cannot be a
    # divider proceed through the same conservative occlusion check as an
    # outlined glyph.  Crossing a real divider, looking divider-like, or being
    # clipped remains unevaluable.
    squat_fill_inside = UnsupportedRegion(
        12, 3, 18, 5, "non-rectangular closed SVG fill",
        "arrow-inside", 0.0, 4, False)
    result = classify_band(cell, SvgPage(
        100, 100, [paint(10), paint(20), paint(30)],
        [squat_fill_inside], "x"))
    assert result["status"] == "measured", result

    squat_fill_crossing = UnsupportedRegion(
        18, 3, 22, 5, "non-rectangular closed SVG fill",
        "arrow-crossing", 0.0, 4, False)
    assert classify_band(cell, SvgPage(
        100, 100, [paint(10), paint(20), paint(30)],
        [squat_fill_crossing], "x"))["status"] == "unevaluable"

    divider_like_fill = UnsupportedRegion(
        14.9, 2, 15.1, 8, "non-rectangular closed SVG fill",
        "divider-like-fill", 0.0, 4, False)
    assert classify_band(cell, SvgPage(
        100, 100, [paint(10), paint(20), paint(30)],
        [divider_like_fill], "x"))["status"] == "unevaluable"

    clipped_squat_fill = dataclasses.replace(
        squat_fill_inside, element="clipped-arrow", clipped=True)
    assert classify_band(cell, SvgPage(
        100, 100, [paint(10), paint(20), paint(30)],
        [clipped_squat_fill], "x"))["status"] == "unevaluable"

    embedded_raster = UnsupportedRegion(
        12, 2, 28, 8, "embedded raster use: #source-test",
        "embedded-raster", None, 4, True)
    assert classify_band(cell, SvgPage(
        100, 100, [paint(10), paint(20), paint(30)],
        [embedded_raster], "x"))["status"] == "unevaluable"

    no_anchor = {**cell, "comb": {**cell["comb"], "cells": 1, "divider_x": []}}
    assert classify_band(no_anchor, page)["status"] == "unevaluable"

    # ---- emitted_geometry_contract binds the WRITING band, and the source
    # ---- has to confirm it.  Every fixture below is layout-SHAPED and reaches
    # ---- the contract through the layout's own field names: the defect this
    # ---- covers survived for the whole corpus precisely because the older
    # ---- coverage hand-authored the expectation dict and so never named
    # ---- `comb["writing_y0"]` at all.
    def band_wall(y0: float, y1: float, order: int, element: str,
                  x0: float = 0.0, x1: float = 40.0,
                  tone: float = 0.0, clipped: bool = False) -> Paint:
        return Paint(x0, y0, x1, y1, tone, order, "fill", element, clipped)

    def band_cell(**comb_overrides: Any) -> dict[str, Any]:
        comb = {
            "cells": 2, "slot_x": [0.0, 20.0, 40.0], "divider_x": [20.0],
            "pitch_pt": 20.0, "divider_gray": 0.0,
            # The tick band: a short stub at the FOOT of the cell, which is
            # what the stale contract used and what a typed character must
            # never be placed in.
            "y0": 8.0, "y1": 10.0,
            "writing_y0": 1.0, "writing_y1": 9.0,
            # The horizontal twin: `slot_x`'s outer values are the rails'
            # CENTRES, and the writing edges are those 1.2pt rails' ink.
            "writing_x0": 0.6, "writing_x1": 39.4,
        }
        comb.update(comb_overrides)
        for key in [name for name, value in comb.items() if value is None]:
            del comb[key]
        return {
            "id": "p1c1", "x0": 0.0, "y0": 0.0, "x1": 40.0, "y1": 10.0,
            "border": {
                "top": {"thickness_pt": 1.0, "gray": 0.0},
                "bottom": {"thickness_pt": 1.0, "gray": 0.0},
            },
            "comb": comb,
        }

    def band_layout(cell_record: dict[str, Any]) -> dict[str, Any]:
        return {"pages": [{"index": 1, "cells": [cell_record]}]}

    band_top_wall = band_wall(0.0, 1.0, 0, "top-wall")
    band_bottom_wall = band_wall(9.0, 10.0, 1, "bottom-wall")

    def band_page(*paints: Paint) -> SvgPage:
        return SvgPage(100, 100, list(paints), [], "band")

    band_source = band_page(band_top_wall, band_bottom_wall)
    band_contract, band_corroboration = emitted_geometry_contract(
        band_layout(band_cell()), {}, {1: band_source})
    assert band_contract["p1c1"]["slots"] == [
        {"index": 0, "left": 0.6, "top": 1.0, "width": 19.4, "height": 8.0},
        {"index": 1, "left": 20.0, "top": 1.0, "width": 19.4, "height": 8.0},
    ], band_contract
    assert band_corroboration["p1c1"]["status"] == "corroborated"
    assert band_corroboration["p1c1"]["source_top_wall_pt"] == 1.0
    assert band_corroboration["p1c1"]["source_bottom_wall_pt"] == 1.0

    def band_verdict(cell_record: dict[str, Any],
                     source: SvgPage = band_source) -> dict[str, Any]:
        _contract, corroborations = emitted_geometry_contract(
            band_layout(cell_record), {}, {1: source})
        return corroborations["p1c1"]

    def band_refuses(cell_record: dict[str, Any], fragment: str,
                     source: SvgPage = band_source) -> None:
        verdict = band_verdict(cell_record, source)
        assert verdict["status"] == "uncorroborated", verdict
        assert fragment in verdict["reason"], verdict["reason"]

    def band_raises(cell_record: dict[str, Any], fragment: str) -> None:
        try:
            emitted_geometry_contract(
                band_layout(cell_record), {}, {1: band_source})
        except RefereeError as error:
            assert fragment in str(error), str(error)
        else:
            raise AssertionError(f"contract accepted a layout it must reject: "
                                 f"{fragment}")

    # Mutation: the tick band substituted for the writing band.  The contract
    # follows what the layout publishes, so only the source can catch this.
    band_refuses(
        band_cell(writing_y0=8.0, writing_y1=10.0),
        "the source walls inset this cell to 1..9")
    # Mutation: the writing band is missing.  A missing field is an ERROR.
    band_raises(band_cell(writing_y0=None), "layout comb geometry is incomplete")
    band_raises(band_cell(writing_y1=None), "layout comb geometry is incomplete")
    band_raises(band_cell(writing_y0=float("nan")), "is not finite")
    band_raises(band_cell(writing_y0="1.0"), "is not numeric")
    # Mutation: the writing band leaves the cell box.
    band_raises(band_cell(writing_y1=10.5), "layout comb geometry is invalid")
    band_raises(band_cell(writing_y0=-0.5), "layout comb geometry is invalid")
    # Mutation: the writing band is degenerate.
    band_raises(band_cell(writing_y0=5.0, writing_y1=5.0),
                "layout comb geometry is invalid")
    band_raises(band_cell(writing_y0=9.0, writing_y1=1.0),
                "layout comb geometry is invalid")
    # The horizontal writing edges, on exactly the same terms.  A contract that
    # laid the outer compartments on `slot_x` would put them across half of
    # each printed rail, so a missing edge is an ERROR and never a fallback to
    # the rail's centre.
    band_raises(band_cell(writing_x0=None),
                "layout comb geometry is incomplete")
    band_raises(band_cell(writing_x1=None),
                "layout comb geometry is incomplete")
    band_raises(band_cell(writing_x0=float("nan")), "is not finite")
    band_raises(band_cell(writing_x1="39.4"), "is not numeric")
    # Mutation: a writing edge OUTSIDE the rail it is meant to be the ink of.
    # Inward off the centre is the only direction a rail's ink can move it.
    band_raises(band_cell(writing_x0=-0.5), "layout comb geometry is invalid")
    band_raises(band_cell(writing_x1=40.5), "layout comb geometry is invalid")
    # Mutation: the inset swallows the outer compartment.
    band_raises(band_cell(writing_x0=21.0), "layout comb geometry is invalid")
    band_raises(band_cell(writing_x1=19.0), "layout comb geometry is invalid")
    # Mutation: the inset matches no painted rule -- the wall is absent, is a
    # different weight, or is painted in a tone the contract does not claim.
    band_refuses(band_cell(), "paints no top wall", band_page(band_bottom_wall))
    band_refuses(
        band_cell(), "paints no bottom wall", band_page(band_top_wall))
    band_refuses(
        band_cell(), "the source walls inset this cell to 2..9",
        band_page(band_wall(0.0, 2.0, 0, "heavy-top"), band_bottom_wall))
    band_refuses(
        band_cell(), "paints no top wall",
        band_page(band_wall(0.0, 1.0, 0, "grey-top", tone=0.85099792),
                  band_bottom_wall))
    band_refuses(
        band_cell(), "paints no top wall",
        band_page(band_wall(0.0, 1.0, 0, "clipped-top", clipped=True),
                  band_bottom_wall))
    # Mutation: the wall is not one weight over every compartment.  An average
    # is not a measurement.
    # C1 v2: a wall drawn 1.0pt over the left compartment and 0.5pt over the
    # right no longer refuses -- the writing surface stands under the HEAVIER
    # claim, so the census takes the maximum, and the corroboration holds
    # exactly when the layout inset by that same maximum (1702-MX's four
    # cells are the corpus population).  An inset taken from the lighter
    # segment is still refused: the relation is the max, not "any weight the
    # wall somewhere has".
    mixed_weight_source = band_page(
        band_wall(0.0, 1.0, 0, "left-top", x1=20.0),
        band_wall(0.0, 0.5, 1, "right-top", x0=20.0),
        band_bottom_wall)
    mixed_ok = band_verdict(band_cell(), mixed_weight_source)
    assert mixed_ok["status"] == "corroborated", mixed_ok
    assert mixed_ok["source_top_wall_pt"] == 1.0, mixed_ok
    thin_inset = band_cell()
    thin_inset["border"]["top"]["thickness_pt"] = 0.5
    thin_inset["comb"]["writing_y0"] = 0.5
    band_refuses(
        thin_inset, "the source walls inset this cell",
        mixed_weight_source)
    # Mutation: a later opaque paint eats half the wall.  The visible extent is
    # the measurement, so the shortened wall must not confirm the band.
    band_refuses(
        band_cell(), "the source walls inset this cell to 0.5..9",
        band_page(band_top_wall, band_bottom_wall,
                  band_wall(0.0, 0.5, 2, "knockout", tone=1.0)))
    # Mutation: two walls of different weight sit equally near the cell edge.
    # Two walls of DIFFERENT weight equally near the edge stay refused: which
    # of them bounds the box is genuinely ambiguous at that ray, and a census
    # that picked either would corroborate an inset the paper does not
    # establish.  (A tie-break to the heavier run was tried in C1 and
    # REVERTED: at shared-boundary junctions it refused 249 cells and moved
    # the reviewed 2551Q control digest.)
    band_refuses(
        band_cell(), "equally near the cell edge",
        band_page(band_wall(-1.5, 0.0, 0, "outer-top"), band_top_wall,
                  band_bottom_wall))
    # Mutation: the wall runs past the neighbourhood the cell can vouch for.
    band_refuses(
        band_cell(), "not bounded inside the cell's own neighbourhood",
        band_page(band_wall(-12.0, 1.0, 0, "unbounded-top"),
                  band_bottom_wall))
    # Mutation: the walls swallow the writing surface entirely.
    band_refuses(
        band_cell(writing_y0=5.0, writing_y1=5.01),
        "leave the cell no writing surface",
        band_page(band_wall(0.0, 5.0, 0, "fat-top"),
                  band_wall(5.0, 10.0, 1, "fat-bottom")))
    # Mutation: the border tone the measurement selects by is missing.
    toneless = band_cell()
    toneless["border"]["top"] = {"thickness_pt": 1.0}
    band_refuses(toneless, "declares no top border tone")
    # A source page that was never rastered is an error, not an empty pass.
    try:
        emitted_geometry_contract(band_layout(band_cell()), {}, {})
    except RefereeError as error:
        assert "no source page raster to corroborate" in str(error)
    else:
        raise AssertionError("the contract corroborated against no source")

    minimal_style = (
        "<style>"
        ".page{position:relative;overflow:hidden}"
        ".c{position:absolute}"
        ".s{position:absolute}"
        "</style>"
    )
    valid_font_preload = (
        '<link rel="preload" href="fonts/tinos-latin-400-normal.woff2" '
        'as="font" type="font/woff2" crossorigin>'
    )
    valid_preload_parser = SlotParser(require_runtime_contract=False)
    valid_preload_parser.feed(
        "<html><head>" + valid_font_preload
        + "</head><body></body></html>"
    )
    valid_preload_parser.close()
    assert not valid_preload_parser.invalid_bindings
    for hostile_preload in (
            valid_font_preload.replace(
                "fonts/tinos-latin-400-normal.woff2",
                "https://example.test/font.woff2"),
            valid_font_preload.replace(
                'rel="preload"', 'rel="stylesheet"'),
            valid_font_preload.replace(
                'type="font/woff2"', 'type="text/css"'),
            valid_font_preload.replace(
                " crossorigin>", ' crossorigin="anonymous">'),
            valid_font_preload.replace(
                "fonts/tinos-latin-400-normal.woff2",
                "fonts/../assets/foreign.woff2"),
            ):
        hostile_parser = SlotParser(require_runtime_contract=False)
        hostile_parser.feed(
            "<html><head>" + hostile_preload
            + "</head><body></body></html>"
        )
        hostile_parser.close()
        assert hostile_parser.invalid_bindings, hostile_preload
    body_preload_parser = SlotParser(require_runtime_contract=False)
    body_preload_parser.feed(
        "<html><head></head><body>" + valid_font_preload
        + "</body></html>"
    )
    body_preload_parser.close()
    assert any(
        "outside the document head" in error
        for error in body_preload_parser.invalid_bindings
    )
    parser = SlotParser(require_runtime_contract=False)
    parser.feed(
        '<html data-form="X">' + minimal_style
        + '<body><div class="page page-1" id="page-1" '
        'style="width:100pt;height:100pt">'
        '<div class="layer-cells">'
        '<div id="p1c1" data-field-kind="comb" data-field-name="p1c1" '
        'class="c f" '
        'data-comb-capacity="2" data-comb-slots="2" '
        'data-comb-pitch="10" data-cell-kind="field" '
        'data-row="0" data-col="0" '
        'style="left:0pt;top:0pt;width:20pt;height:10pt">'
        '<div class="s" data-slot="0" '
        'style="left:0pt;top:0pt;width:10pt;height:10pt">'
        '<input type="text" class="fi fh0 fc" id="p1c1-s0" '
        'name="p1c1" data-slot-index="0" maxlength="1" '
        'autocomplete="off" spellcheck="false"></div>'
        '<div class="s" data-slot="1" '
        'style="left:10pt;top:0pt;width:10pt;height:10pt">'
        '</div></div></div></div>'
        '<template id="band-template-0" data-band="b0" '
        'data-band-index="0" data-capacity="1" data-row-pitch="10" '
        'data-row-y="0" data-template-row="0">'
        '<div class="s" data-slot="2">'
        '<input type="text" class="fi fh0 fc" data-slot-index="2" '
        'maxlength="1" autocomplete="off" spellcheck="false">'
        '</div></template>'
        '</body></html>'
    )
    assert parser.physical_slots == {"p1c1": [0, 1]}
    assert parser.editable_slots == {"p1c1": [0]}
    assert parser.comb_containers == {"p1c1"}
    assert parser.root == {"data-form": "X"}
    assert parser.pages == [1]
    assert parser.page_geometry == [(1, 100.0, 100.0)]
    parser_expected = {"p1c1": {
        "page_index": 1,
        "left": 0.0, "top": 0.0, "width": 20.0, "height": 10.0,
        "slots": [
            {"index": 0, "left": 0.0, "top": 0.0,
             "width": 10.0, "height": 10.0},
            {"index": 1, "left": 10.0, "top": 0.0,
             "width": 10.0, "height": 10.0},
        ],
    }}
    assert slot_records(parser, parser_expected)["p1c1"]["valid"]
    moved_expected = {
        "p1c1": {**parser_expected["p1c1"], "left": 50.0, "top": 50.0},
    }
    assert not slot_records(parser, moved_expected)["p1c1"]["valid"]

    invalid_slots = SlotParser(require_runtime_contract=False)
    invalid_slots.feed(
        '<html>' + minimal_style + '<body>'
        '<div class="page page-1" id="page-1" '
        'style="width:100pt;height:100pt">'
        '<div class="layer-cells">'
        '<div id="p1c1" data-field-kind="comb" data-field-name="p1c1" '
        'class="c f" data-cell-kind="field" data-row="0" data-col="0" '
        'data-comb-pitch="10" '
        'data-comb-capacity="3" data-comb-slots="3" '
        'style="left:0pt;top:0pt;width:20pt;height:10pt">'
        '<div class="s" data-slot="0" '
        'style="left:0pt;top:0pt;width:10pt;height:10pt"></div>'
        '<div class="s" data-slot="1" '
        'style="left:10pt;top:0pt;width:0pt;height:10pt"></div>'
        '<div class="s" data-slot="2" '
        'style="left:9pt;top:0pt;width:11pt;height:10pt"></div>'
        '</div></div></div></body></html>'
    )
    assert not slot_records(invalid_slots)["p1c1"]["valid"]

    def emitted_slot_fixture(slot_markup: str) -> SlotParser:
        fixture_parser = SlotParser(require_runtime_contract=False)
        fixture_parser.feed(
            '<html>' + minimal_style + '<body>'
            '<div class="page page-1" id="page-1" '
            'style="width:100pt;height:100pt">'
            '<div class="layer-cells">'
            '<div id="p1c1" data-field-kind="comb" '
            'data-field-name="p1c1" class="c f" '
            'data-cell-kind="field" data-row="0" data-col="0" '
            'data-comb-pitch="10" data-comb-capacity="2" '
            'data-comb-slots="2" '
            'style="left:0pt;top:0pt;width:20pt;height:10pt">'
            + slot_markup
            + '</div></div></div></body></html>'
        )
        fixture_parser.close()
        return fixture_parser

    missing_emitted_slot = emitted_slot_fixture(
        '<div class="s" data-slot="0" '
        'style="left:0pt;top:0pt;width:10pt;height:10pt">'
        '<input type="text" class="fi fh0 fc" id="p1c1-s0" '
        'name="p1c1" data-slot-index="0" maxlength="1" '
        'autocomplete="off" spellcheck="false"></div>'
    )
    assert not slot_records(
        missing_emitted_slot, parser_expected)["p1c1"]["valid"]
    duplicate_emitted_slot = emitted_slot_fixture(
        '<div class="s" data-slot="0" '
        'style="left:0pt;top:0pt;width:10pt;height:10pt">'
        '<input type="text" class="fi fh0 fc" id="p1c1-s0" '
        'name="p1c1" data-slot-index="0" maxlength="1" '
        'autocomplete="off" spellcheck="false"></div>'
        '<div class="s" data-slot="0" '
        'style="left:10pt;top:0pt;width:10pt;height:10pt">'
        '<input type="text" class="fi fh0 fc" id="p1c1-s0" '
        'name="p1c1" data-slot-index="0" maxlength="1" '
        'autocomplete="off" spellcheck="false"></div>'
    )
    assert not slot_records(
        duplicate_emitted_slot, parser_expected)["p1c1"]["valid"]

    invalid_page_binding = SlotParser(require_runtime_contract=False)
    invalid_page_binding.feed(
        '<html>' + minimal_style + '<body>'
        '<div class="page page-1" id="page-1" '
        'style="width:100pt;height:100pt">'
        '<div class="layer-cells">'
        '<div id="p1c1" data-field-kind="comb" data-field-name="p1c1" '
        'class="c f" data-cell-kind="field" data-row="0" data-col="0" '
        'data-comb-pitch="10" '
        'data-comb-capacity="1" data-comb-slots="1" '
        'style="left:999pt;top:0pt;width:10pt;height:10pt">'
        '<div class="s" data-slot="0" '
        'style="left:0pt;top:0pt;width:10pt;height:10pt"></div></div>'
        '<div id="p2c1" data-field-kind="comb" data-field-name="p2c1" '
        'class="c f" data-cell-kind="field" data-row="0" data-col="0" '
        'data-comb-pitch="10" '
        'data-comb-capacity="1" data-comb-slots="1" '
        'style="left:0pt;top:0pt;width:10pt;height:10pt">'
        '<div class="s" data-slot="0" '
        'style="left:0pt;top:0pt;width:10pt;height:10pt"></div></div>'
        '</div></div></body></html>'
    )
    invalid_records = slot_records(invalid_page_binding)
    assert not invalid_records["p1c1"]["valid"]
    assert not invalid_records["p2c1"]["valid"]
    assert any("comb page binding disagrees: p2c1" == error
               for error in invalid_page_binding.invalid_bindings)

    invalid_style = SlotParser(require_runtime_contract=False)
    invalid_style.feed(
        '<html>' + minimal_style + '<body>'
        '<div class="page page-1" id="page-1" '
        'style="width:100pt;height:100pt">'
        '<div class="layer-cells">'
        '<div id="p1c1" data-field-kind="comb" data-field-name="p1c1" '
        'class="c f" data-cell-kind="field" data-row="0" data-col="0" '
        'data-comb-pitch="10" '
        'data-comb-capacity="1" data-comb-slots="1" '
        'style="left:0pt;top:0pt;width:10pt;height:10pt;display:none">'
        '<div class="s" data-slot="0" '
        'style="left:0pt;top:0pt;width:10pt;height:10pt"></div>'
        '</div></div></div></body></html>'
    )
    assert any("comb geometry is non-canonical: p1c1" == error
               for error in invalid_style.invalid_bindings)

    def hidden_layout_record(
            wrapper_open: str = "", wrapper_close: str = "",
            comb_attribute: str = "", extra_style: str = "",
            sibling_html: str = "",
            ) -> tuple[dict[str, Any], SlotParser]:
        hidden_parser = SlotParser(require_runtime_contract=False)
        hidden_parser.feed(
            '<html>' + minimal_style + extra_style
            + '<body><div class="page page-1" id="page-1" '
            'style="width:100pt;height:100pt">'
            + sibling_html
            + '<div class="layer-cells">'
            + wrapper_open
            + '<div class="c" id="p1c1" data-field-kind="comb" '
            'data-field-name="p1c1" data-comb-capacity="1" '
            'data-comb-slots="1" data-comb-pitch="10" '
            'data-cell-kind="field" data-row="0" data-col="0" '
            'style="left:0pt;top:0pt;width:10pt;height:10pt"'
            + comb_attribute + '>'
            '<div class="s" data-slot="0" '
            'style="left:0pt;top:0pt;width:10pt;height:10pt">'
            '<input type="text" class="fi fh0 fc" id="p1c1-s0" '
            'name="p1c1" data-slot-index="0" maxlength="1" '
            'autocomplete="off" spellcheck="false"></div></div>'
            + wrapper_close + '</div></div></body></html>'
        )
        hidden_parser.close()
        return slot_records(hidden_parser)["p1c1"], hidden_parser

    hidden_comb, _hidden_comb_parser = hidden_layout_record(
        comb_attribute=" hidden")
    assert not hidden_comb["valid"]
    hidden_ancestor, _hidden_ancestor_parser = hidden_layout_record(
        wrapper_open="<section hidden>", wrapper_close="</section>")
    assert not hidden_ancestor["valid"]
    inline_hidden, _inline_hidden_parser = hidden_layout_record(
        wrapper_open='<section style="display:none">',
        wrapper_close="</section>",
    )
    assert not inline_hidden["valid"]
    closed_details, _closed_details_parser = hidden_layout_record(
        wrapper_open="<details>", wrapper_close="</details>")
    assert not closed_details["valid"]
    styled_hidden, styled_hidden_parser = hidden_layout_record(
        extra_style="<style>#p1c1{display:none!important}</style>")
    assert not styled_hidden["valid"]
    assert any("!important" in error
               for error in styled_hidden_parser.invalid_bindings)
    for property_name, property_value in (
            ("margin-left", "20pt"),
            ("background", "white"),
            ("mask", "linear-gradient(transparent,transparent)"),
            ):
        styled, _styled_parser = hidden_layout_record(
            extra_style=(
                f"<style>.c{{{property_name}:{property_value}}}</style>"
            ))
        assert not styled["valid"], property_name
    shifted_ancestor, _shifted_ancestor_parser = hidden_layout_record(
        wrapper_open=(
            '<section style="position:relative;left:20pt">'),
        wrapper_close="</section>",
    )
    assert not shifted_ancestor["valid"]
    popover_comb, _popover_parser = hidden_layout_record(
        comb_attribute=" popover")
    assert not popover_comb["valid"]
    noscript_comb, _noscript_parser = hidden_layout_record(
        wrapper_open="<noscript>", wrapper_close="</noscript>")
    assert not noscript_comb["valid"]
    duplicate_attribute, _duplicate_parser = hidden_layout_record(
        comb_attribute=' style="display:none"')
    assert not duplicate_attribute["valid"]
    imported_css, _import_parser = hidden_layout_record(
        extra_style=(
            '<style>@import url("data:text/css,.c%7Bdisplay%3Anone%7D");'
            "</style>"))
    assert not imported_css["valid"]
    conditional_css, _conditional_parser = hidden_layout_record(
        extra_style="<style>@media not all{.c{position:absolute}}</style>")
    assert not conditional_css["valid"]
    supported_css, _supported_parser = hidden_layout_record(
        extra_style=(
            "<style>@supports (unknown: value)"
            "{.c{position:absolute}}</style>"))
    assert not supported_css["valid"]
    overlay, _overlay_parser = hidden_layout_record(
        sibling_html=(
            '<div style="position:fixed;inset:0;background:#fff;'
            'z-index:9999"></div>'))
    assert not overlay["valid"]
    event_image, _event_image_parser = hidden_layout_record(
        sibling_html=(
            '<img src="invalid://x" '
            'onerror="document.body.hidden=true">'))
    assert not event_image["valid"]
    plaintext_parser = SlotParser(require_runtime_contract=False)
    plaintext_parser.feed("<html><body><plaintext>x</plaintext></body></html>")
    assert any("unsupported emitter element: plaintext" in error
               for error in plaintext_parser.invalid_bindings)
    page_size_parser = SlotParser()
    page_size_parser.doctype_count = 1
    page_size_parser.style_count = 1
    page_size_parser.band_data_scripts = 1
    page_size_parser.runtime_script_hashes = list(HTML_RUNTIME_SCRIPT_SHA256)
    page_size_parser.page_geometry = [(1, 612.0, 936.0)]
    page_size_parser.stylesheet_page_sizes = [(1.0, 1.0)]
    slot_records(page_size_parser)
    assert any("@page size disagrees" in error
               for error in page_size_parser.invalid_bindings)
    def named_page_parser(
            geometry: list[tuple[int, float, float]],
            named: dict[int, tuple[float, float]],
            selectors: set[int] | None = None,
            pages: list[int] | None = None,
            unnamed: tuple[float, float] = (612.0, 1008.0),
            ) -> SlotParser:
        value = SlotParser()
        value.doctype_count = 1
        value.style_count = 1
        value.band_data_scripts = 1
        value.runtime_script_hashes = list(HTML_RUNTIME_SCRIPT_SHA256)
        value.page_geometry = list(geometry)
        value.pages = (
            [index for index, _w, _h in geometry] if pages is None
            else list(pages))
        value.stylesheet_page_sizes = [unnamed]
        value.stylesheet_named_page_sizes = dict(named)
        value.stylesheet_named_page_selectors = (
            set(named) if selectors is None else set(selectors))
        slot_records(value)
        return value

    # 1604-CF's real shape: three emitted pages, one of them landscape, plus a
    # surviving rule for the page the guide reclaimed.
    mixed_geometry = [(1, 612.0, 1008.0), (2, 612.0, 1008.0),
                      (3, 1008.0, 612.0)]
    mixed_named = {1: (612.0, 1008.0), 2: (612.0, 1008.0),
                   3: (1008.0, 612.0), 4: (612.0, 1008.0)}
    def page_rule_errors(value: SlotParser) -> list[str]:
        return [
            error for error in value.invalid_bindings
            if "@page" in error or "named page rules" in error
        ]

    assert not page_rule_errors(
        named_page_parser(mixed_geometry, mixed_named))
    for label, geometry, named, selectors, pages in (
        ("wrong-named-size", mixed_geometry,
         {**mixed_named, 3: (612.0, 1008.0)}, None, None),
        ("missing-named-rule", mixed_geometry,
         {k: v for k, v in mixed_named.items() if k != 2}, None, None),
        ("selector-without-rule", mixed_geometry, mixed_named,
         {1, 2, 3, 4, 5}, None),
        ("rule-without-selector", mixed_geometry, mixed_named,
         {1, 2, 3}, None),
        ("referenced-extra-page", mixed_geometry, mixed_named, None,
         [1, 2, 3, 4]),
        ("named-rules-on-uniform-paper",
         [(1, 612.0, 1008.0), (2, 612.0, 1008.0)],
         {1: (612.0, 1008.0), 2: (612.0, 1008.0)}, None, None),
    ):
        broken_pages = named_page_parser(
            geometry, named, selectors, pages)
        assert page_rule_errors(broken_pages), label

    no_doctype_parser = SlotParser()
    no_doctype_parser.style_count = 1
    no_doctype_parser.band_data_scripts = 1
    no_doctype_parser.runtime_script_hashes = list(
        HTML_RUNTIME_SCRIPT_SHA256)
    no_doctype_parser.page_geometry = [(1, 612.0, 936.0)]
    no_doctype_parser.stylesheet_page_sizes = [(612.0, 936.0)]
    slot_records(no_doctype_parser)
    assert any("standards-mode doctype" in error
               for error in no_doctype_parser.invalid_bindings)
    structure = b"<!doctype html><html><body><div></div></body></html>"
    assert emitted_structure_sha256(structure) != emitted_structure_sha256(
        structure.replace(b"<div>", b"<svg></svg><div>"))
    assert emitted_structure_sha256(
        structure.replace(b"<div>", b"<input id=x><div>")
    ) != emitted_structure_sha256(
        structure.replace(b"<div>", b"<input id=y><div>")
    )
    bogus_runtime = SlotParser()
    bogus_runtime._validate_script((), "document.body.hidden=true")
    slot_records(bogus_runtime)
    assert any("runtime scripts disagree" in error
               for error in bogus_runtime.invalid_bindings)

    def synthetic_ledger_comb(
            x0: float, x1: float, status: str = "resolved",
            reason_codes: list[str] | None = None,
            ) -> dict[str, Any]:
        midpoint = (x0 + x1) / 2
        reasons = list(reason_codes or ())
        return {
            "cells": 2,
            "divider_count": 1,
            "pitch_pt": midpoint - x0,
            "pitch_min_pt": midpoint - x0,
            "pitch_max_pt": x1 - midpoint,
            "slot_x": [x0, midpoint, x1],
            "divider_x": [midpoint],
            "divider_thickness_pt": 0.2,
            "divider_thicknesses_pt": [0.2],
            "divider_gray": 0.0,
            "divider_paint_seq": [1],
            "divider_paint_ranges": [[1, 1]],
            "y0": 1.0,
            "y1": 9.0,
            "height_pt": 8.0,
            "resolution": {
                "status": status,
                "method": "self-test",
                "reason_codes": reasons,
            },
        }

    def refresh_ledger_stats(layout_value: dict[str, Any]) -> None:
        page_value = layout_value["pages"][0]
        subjects_value = page_value["comb_subjects"]
        inferences_value = page_value["comb_inferences"]
        active_resolved_value = sum(
            item["state"] == "active_resolved" for item in subjects_value)
        active_unresolved_value = sum(
            item["state"] == "active_unresolved" for item in subjects_value)
        retained_value = sum(
            item["state"] == "retained_unresolved" for item in subjects_value)
        subject_blockers_value = sum(
            item.get("blocks_gate") is True for item in subjects_value)
        inference_blockers_value = sum(
            item.get("blocks_gate") is True for item in inferences_value)
        comb_cells_value = [
            item for item in page_value["cells"] if "comb" in item
        ]
        page_value["stats"] = {
            "comb_cells": len(comb_cells_value),
            "comb_subjects": len(subjects_value),
            "comb_subjects_active": (
                active_resolved_value + active_unresolved_value),
            "comb_subjects_active_resolved": active_resolved_value,
            "comb_subjects_active_unresolved": active_unresolved_value,
            "comb_subjects_retained_unresolved": retained_value,
            "comb_subjects_retired": 0,
            "comb_subjects_blocking": subject_blockers_value,
            "comb_inferences_suppressed": len(inferences_value),
            "comb_inferences_blocking": inference_blockers_value,
            "comb_evidence_blocking": (
                subject_blockers_value + inference_blockers_value),
            "comb_slots": sum(
                int(item["comb"]["cells"]) for item in comb_cells_value),
        }

    # The all-active ledger fixtures below are pinned to a slug whose retained
    # count is zero, so "how many subjects does this form publish" and "how many
    # of them are suppressed" stay separable in the self-test too. The retained
    # census gets its own fixture further down, on a slug that really has one.
    ledger_fixture_slug = "1702mx-2018c-attachment"
    assert EXPECTED_RETAINED_SUBJECTS_BY_SLUG.get(ledger_fixture_slug, 0) == 0

    def synthetic_ledger_layout() -> dict[str, Any]:
        cells_value: list[dict[str, Any]] = []
        subjects_value: list[dict[str, Any]] = []
        for index in range(EXPECTED_COMBS_BY_SLUG[ledger_fixture_slug]):
            x0 = float(index * 3)
            x1 = x0 + 2.0
            bbox_value = [x0, 0.0, x1, 10.0]
            subject_key = (
                f"p1@{x0:.2f},0.00,{x1:.2f},10.00")
            cell_id = f"p1c{index}"
            comb_value = synthetic_ledger_comb(x0, x1)
            cells_value.append({
                "id": cell_id,
                "subject_key": subject_key,
                "x0": x0,
                "y0": 0.0,
                "x1": x1,
                "y1": 10.0,
                "comb": comb_value,
            })
            subjects_value.append({
                "subject_key": subject_key,
                "legacy_cell_id": cell_id,
                "legacy_bbox": bbox_value,
                "cell_id": cell_id,
                "mapped_partition_cell_ids": [cell_id],
                "state": "active_resolved",
                "reason_codes": [],
                "cells": 2,
                "blocks_gate": False,
            })
        value = {
            "generator": dict(LATTICE_GENERATOR_CONTRACT),
            "pages": [{
                "index": 1,
                "cells": cells_value,
                "comb_subjects": subjects_value,
                "comb_inferences": [],
            }],
        }
        refresh_ledger_stats(value)
        return value

    def clone(value: Any) -> Any:
        return json.loads(json.dumps(value))

    lattice_producer_bytes = (HERE / "lattice.py").read_bytes()
    ledger_fixture = synthetic_ledger_layout()
    ledger_result = validate_comb_ledger(
        ledger_fixture_slug, ledger_fixture, lattice_producer_bytes)
    # Derived from the corpus pin, never restated as a literal. The fixture is
    # built by looping `range(EXPECTED_COMBS_BY_SLUG[slug])` above, so a
    # literal here is a second copy of one number in one file -- the shape that
    # made 4442 and 4540 disagree, and that made this very assertion the last
    # thing standing between r14 and a 60-minute gate run on stale constants.
    fixture_subjects = EXPECTED_COMBS_BY_SLUG[ledger_fixture_slug]
    assert ledger_result["counts"] == {
        "subjects": fixture_subjects,
        "active": fixture_subjects,
        "active_resolved": fixture_subjects,
        "active_unresolved": 0,
        "active_composite": 0,
        "retained_unresolved": 0,
        "inferences_suppressed": 0,
        "blocking": 0,
    }

    for name, mutate in (
        (
            "missing-ledger",
            lambda value: value["pages"][0].pop("comb_subjects"),
        ),
        (
            "missing-inference-ledger",
            lambda value: value["pages"][0].pop("comb_inferences"),
        ),
        (
            "empty-ledger",
            lambda value: value["pages"][0].__setitem__(
                "comb_subjects", []),
        ),
        (
            "duplicate-subject-key",
            lambda value: value["pages"][0]["comb_subjects"][1].__setitem__(
                "subject_key",
                value["pages"][0]["comb_subjects"][0]["subject_key"]),
        ),
        (
            "duplicate-legacy-id",
            lambda value: value["pages"][0]["comb_subjects"][1].__setitem__(
                "legacy_cell_id",
                value["pages"][0]["comb_subjects"][0]["legacy_cell_id"]),
        ),
        (
            "active-cell-mismatch",
            lambda value: (
                value["pages"][0]["comb_subjects"][0].__setitem__(
                    "cell_id", "p1c999"),
                value["pages"][0]["comb_subjects"][0].__setitem__(
                    "mapped_partition_cell_ids", ["p1c999"]),
            ),
        ),
        (
            "retired-state",
            lambda value: value["pages"][0]["comb_subjects"][0].__setitem__(
                "state", "retired_proven_false"),
        ),
        (
            "unknown-state",
            lambda value: value["pages"][0]["comb_subjects"][0].__setitem__(
                "state", "mystery"),
        ),
    ):
        broken_ledger = clone(ledger_fixture)
        mutate(broken_ledger)
        try:
            validate_comb_ledger(
                ledger_fixture_slug, broken_ledger,
                lattice_producer_bytes)
        except RefereeError:
            pass
        else:
            raise AssertionError(f"invalid comb ledger passed: {name}")
    try:
        validate_comb_ledger(
            ledger_fixture_slug, ledger_fixture, b"stale lattice")
    except RefereeError:
        pass
    else:
        raise AssertionError("stale lattice producer bytes were accepted")

    reverse_mismatch = clone(ledger_fixture)
    reverse_mismatch["pages"][0]["cells"].append({
        "id": "p1c999",
        "subject_key": "p1@90.00,0.00,92.00,10.00",
        "x0": 90.0, "y0": 0.0, "x1": 92.0, "y1": 10.0,
        "comb": synthetic_ledger_comb(90.0, 92.0),
    })
    refresh_ledger_stats(reverse_mismatch)
    try:
        validate_comb_ledger(
            ledger_fixture_slug, reverse_mismatch, lattice_producer_bytes)
    except RefereeError:
        pass
    else:
        raise AssertionError("unledgered active comb passed reverse mapping")

    unresolved_ledger = clone(ledger_fixture)
    unresolved_subject = unresolved_ledger["pages"][0]["comb_subjects"][0]
    unresolved_cell = unresolved_ledger["pages"][0]["cells"][0]
    unresolved_subject.update({
        "state": "active_unresolved",
        "reason_codes": ["self-test-unresolved"],
        "blocks_gate": True,
    })
    unresolved_cell["comb"]["resolution"].update({
        "status": "unresolved",
        "reason_codes": ["self-test-unresolved"],
    })
    refresh_ledger_stats(unresolved_ledger)
    unresolved_result = validate_comb_ledger(
        ledger_fixture_slug, unresolved_ledger, lattice_producer_bytes)
    assert unresolved_result["counts"]["active_unresolved"] == 1
    assert unresolved_result["counts"]["blocking"] == 1

    inference_ledger = clone(ledger_fixture)
    # Identity AND geometry sit clear of the synthetic 0..N run for every
    # slug total (the run's cells occupy x = 3*index): at 15 subjects the
    # old x 63..65 was free paper, at 108 it was synthetic cell 21's exact
    # bbox and collided on subject_key, which is this fixture's own
    # duplicate-identity guard firing on the fixture itself.
    inferred_cell = {
        "id": "p1c9021",
        "subject_key": "p1@9021.00,0.00,9023.00,10.00",
        "x0": 9021.0, "y0": 0.0, "x1": 9023.0, "y1": 10.0,
    }
    inference_ledger["pages"][0]["cells"].append(inferred_cell)
    inference_ledger["pages"][0]["comb_inferences"].append({
        "subject_key": inferred_cell["subject_key"],
        "cell_id": inferred_cell["id"],
        "bbox": [9021.0, 0.0, 9023.0, 10.0],
        "state": COMB_INFERENCE_STATE,
        "reason_codes": ["no-legacy-subject"],
        "inferred_comb": synthetic_ledger_comb(
            9021.0, 9023.0, "unresolved", ["no-legacy-subject"]),
        "requires_independent_evidence": True,
        "permitted_transitions": ["active_reviewed"],
        "blocks_gate": True,
    })
    refresh_ledger_stats(inference_ledger)
    inference_result = validate_comb_ledger(
        ledger_fixture_slug, inference_ledger, lattice_producer_bytes)
    assert inference_result["counts"]["inferences_suppressed"] == 1
    assert inference_result["counts"]["blocking"] == 1

    retained_ledger = clone(ledger_fixture)
    retained_subject = retained_ledger["pages"][0]["comb_subjects"][0]
    retained_cell = retained_ledger["pages"][0]["cells"][0]
    retained_comb = retained_cell.pop("comb")
    retained_comb["resolution"].update({
        "status": "unresolved",
        "reason_codes": ["legacy-continuity-only"],
    })
    retained_subject.clear()
    retained_subject.update({
        "subject_key": retained_cell["subject_key"],
        "legacy_cell_id": retained_cell["id"],
        "legacy_bbox": [
            retained_cell["x0"], retained_cell["y0"],
            retained_cell["x1"], retained_cell["y1"],
        ],
        "cell_id": None,
        "mapped_partition_cell_ids": [retained_cell["id"]],
        "mapped_partition_subject_keys": [retained_cell["subject_key"]],
        "state": "retained_unresolved",
        "emission": "suppressed",
        # A reason tuple deliberately OUTSIDE the criteria table -- case (a)
        # is about untabled reasons carrying no obligation, and the tuple it
        # first used (no-final-visible-band) became tabled in R2a.
        "reason_codes": [
            "emission-suppressed-unproved-multi-row-divider-corridor"],
        "legacy_comb": retained_comb,
        "requires_independent_evidence": True,
        "permitted_transitions": [
            "active_composite", "retired_proven_false",
        ],
        "blocks_gate": True,
    })
    refresh_ledger_stats(retained_ledger)
    # A ledger carrying one suppressed subject is only valid on a slug whose
    # retained census is one. Both slugs publish the same number of subjects, so
    # the same fixture shape exercises retained-zero and retained-one, and the
    # assertion below fails loudly if a future census move breaks that pairing.
    #
    # It did exactly that at r20, which is the check working: 2551M went 15 -> 18
    # subjects and 1 -> 3 retained, so it is no longer paired with 1604-E and no
    # longer a retained-ONE slug. 1604-CF replaced it then, and moved again
    # on 2026-08-16 when DECISION A's transition took it 1 -> 2 retained
    # (p2c73 joined the suppressed census), breaking the (15, 15) pairing
    # with 1604-E outright -- no 15-subject retained-ONE slug exists on
    # today's census. The pairing moves to (108, 108): 1702-MX's attachment
    # (108 subjects, retained zero) carries the ledger fixture and 1800
    # (108 subjects, exactly one retained -- p1c4, the reviewed exception's
    # own subject) carries the retained census. 2551M stays the
    # retained-MANY negative control, now at four. Neither the fixture nor
    # the rule was weakened; only the slugs that satisfy them were
    # re-measured, which is this assertion doing its job a third time.
    retained_fixture_slug = "1800-2018"
    assert (EXPECTED_COMBS_BY_SLUG[retained_fixture_slug]
            == EXPECTED_COMBS_BY_SLUG[ledger_fixture_slug])
    assert EXPECTED_RETAINED_SUBJECTS_BY_SLUG[retained_fixture_slug] == 1
    assert EXPECTED_RETAINED_SUBJECTS_BY_SLUG["2551m-2002"] > 1
    for wrong_slug in (ledger_fixture_slug, "2551m-2002"):
        try:
            validate_comb_ledger(
                wrong_slug, retained_ledger, lattice_producer_bytes)
        except RefereeError:
            pass
        else:
            raise AssertionError(
                f"retained census was not enforced for {wrong_slug}")
    # And the inverse, which is the exact shape of the r14 census fault: a
    # ledger whose total is right but which publishes no suppressed subject at
    # all must not validate against a slug that is pinned to retain one.
    try:
        validate_comb_ledger(
            retained_fixture_slug, ledger_fixture, lattice_producer_bytes)
    except RefereeError:
        pass
    else:
        raise AssertionError(
            "a ledger missing its pinned retained subject was accepted")
    retained_result = validate_comb_ledger(
        retained_fixture_slug, retained_ledger, lattice_producer_bytes)
    assert retained_result["counts"]["retained_unresolved"] == 1

    # ---- C3-A step 2: the composite transition, adjudicator half ----------
    #
    # A user-signed registry entry lets the producer publish
    # `active_composite`; this referee re-validates the certificate against
    # the registry byte-for-byte and refuses every forgery.  The fixture
    # subject uses a TABLED reason tuple (the R2a partition-edge criterion),
    # because a composite of an untabled claim has no source corroboration
    # to measure and must refuse.
    composite_ledger = clone(retained_ledger)
    composite_ledger["source"] = {"sha256": "ab" * 32}
    composite_subject = composite_ledger["pages"][0]["comb_subjects"][0]
    composite_subject["reason_codes"] = [
        "emission-suppressed-no-rectangular-owner",
        "painted-edge-partition",
    ]
    composite_subject["state"] = "active_composite"
    composite_subject["blocks_gate"] = False
    # The page stats transition exactly as lattice.py's own would: composite
    # counts in `comb_subjects_active` (state startswith "active_"), leaves
    # the retained count, and stops blocking.
    composite_stats = composite_ledger["pages"][0]["stats"]
    composite_stats["comb_subjects_active"] += 1
    composite_stats["comb_subjects_retained_unresolved"] -= 1
    composite_stats["comb_subjects_blocking"] -= 1
    composite_stats["comb_evidence_blocking"] -= 1
    composite_key = (
        retained_fixture_slug, 1, composite_subject["legacy_cell_id"])
    composite_entry = {
        "subject_key": composite_subject["subject_key"],
        "source_sha256": "ab" * 32,
        "transition": "active_composite",
        "suppression_criterion": SOURCE_PARTITION_EDGE_CRITERION,
        "reviewer": "self-test", "date": "2026-08-15",
        "citation": "self-test",
    }
    composite_subject["transition_certificate"] = {
        "criterion": review_registry.TRANSITION_CRITERION,
        "registry_key": list(composite_key),
        "transition": "active_composite",
        "suppression_criterion": SOURCE_PARTITION_EDGE_CRITERION,
        "reviewer": "self-test", "date": "2026-08-15",
    }

    def with_registry(entries, work):
        saved = dict(review_registry.REVIEWED_LEDGER_TRANSITIONS)
        review_registry.REVIEWED_LEDGER_TRANSITIONS.clear()
        review_registry.REVIEWED_LEDGER_TRANSITIONS.update(entries)
        try:
            return work()
        finally:
            review_registry.REVIEWED_LEDGER_TRANSITIONS.clear()
            review_registry.REVIEWED_LEDGER_TRANSITIONS.update(saved)

    composite_result = with_registry(
        {composite_key: composite_entry},
        lambda: validate_comb_ledger(
            retained_fixture_slug, composite_ledger, lattice_producer_bytes))
    assert composite_result["counts"]["active_composite"] == 1
    assert composite_result["counts"]["retained_unresolved"] == 0
    assert composite_result["counts"]["blocking"] == 0
    published_composite = [
        subject for subject in composite_result["subjects"]
        if subject["state"] == "active_composite"]
    assert len(published_composite) == 1
    assert (published_composite[0]["transition_certificate"]
            == composite_subject["transition_certificate"])
    assert published_composite[0]["blocks_gate"] is False
    assert (published_composite[0]["source_suppression_criterion"]
            == SOURCE_PARTITION_EDGE_CRITERION)

    def composite_refused(name, entries=None, mutate=None):
        broken_ledger = clone(composite_ledger)
        if mutate is not None:
            mutate(broken_ledger)
        try:
            with_registry(
                {composite_key: composite_entry}
                if entries is None else entries,
                lambda: validate_comb_ledger(
                    retained_fixture_slug, broken_ledger,
                    lattice_producer_bytes))
        except RefereeError:
            return
        raise AssertionError(f"composite forgery was accepted: {name}")

    composite_refused("no registry entry at all", entries={})
    composite_refused(
        "registry names a different reviewer",
        entries={composite_key: {**composite_entry, "reviewer": "someone"}})
    composite_refused(
        "registry reviewed different source bytes",
        entries={composite_key: {
            **composite_entry, "source_sha256": "cd" * 32}})

    def strip_certificate(value):
        value["pages"][0]["comb_subjects"][0].pop("transition_certificate")
        value["pages"][0]["comb_subjects"][0]["state"] = "retained_unresolved"
        value["pages"][0]["comb_subjects"][0]["blocks_gate"] = True
    composite_refused(
        "reviewed transition the producer did not apply",
        mutate=strip_certificate)

    def untabled_reasons(value):
        value["pages"][0]["comb_subjects"][0]["reason_codes"] = [
            "emission-suppressed-unproved-multi-row-divider-corridor"]
    composite_refused(
        "certificate names a criterion the reasons do not table",
        mutate=untabled_reasons)

    def composite_blocks(value):
        value["pages"][0]["comb_subjects"][0]["blocks_gate"] = True
    composite_refused(
        "a composite subject claiming to block the gate",
        mutate=composite_blocks)

    def certificate_without_state(value):
        value["pages"][0]["comb_subjects"][0]["state"] = "retained_unresolved"
        value["pages"][0]["comb_subjects"][0]["blocks_gate"] = True
    composite_refused(
        "certificate carried by a retained state",
        mutate=certificate_without_state)

    # The composite's own comparison: it is scored on the suppression
    # corroboration, never on a compartment count it does not have.
    def composite_cell(**overrides):
        value = {
            "ledger_state": "active_composite",
            "latticed": 4,
            "emitted": None,
            "audit_printed": None,
            "emitted_indexes_valid": False,
            "referee": {
                "status": "composite",
                "criterion": SOURCE_PARTITION_EDGE_CRITERION,
                "corroborated": True,
            },
        }
        value.update(overrides)
        return value

    assert comparison(composite_cell(), True)[0] == "agree"
    # Audit completeness is irrelevant to a subject with no printed topology:
    # the corroboration is the whole measurement.
    assert comparison(composite_cell(), False)[0] == "agree"
    # Review cannot overrule the paper.
    refuted_composite = comparison(composite_cell(referee={
        "status": "composite",
        "criterion": SOURCE_PARTITION_EDGE_CRITERION,
        "corroborated": False}), True)
    assert refuted_composite[0] == "stop", refuted_composite
    assert "refutes" in refuted_composite[1]
    # A composite that emitted slots of its own contradicts its own claim.
    assert comparison(composite_cell(emitted=4), True)[0] == "stop"
    # An unmeasured composite is unevaluable, never a pass.
    assert comparison(composite_cell(referee={"status": "unevaluable"}),
                      True)[0] == "unevaluable"
    assert transition_decision(composite_cell(), "agree")[0] == "none"

    # ---- S2: the reviewed exception, the narrowest of the three paths ----
    exc_cell = {"page": 1, "cell_id": "p1c9", "legacy_cell_id": "p1c9",
                "subject_key": "p1@9,9"}
    exc_key = ("fixture-1999", 1, "p1c9")
    exc_entry = {
        "subject_key": "p1@9,9", "source_sha256": "ab" * 32,
        "reason": "referee: chosen source topology lacks a proof",
        "evidence": "self-test", "reviewer": "self-test",
        "date": "2026-08-16", "citation": "self-test"}

    def with_exceptions(entries, work):
        saved = dict(review_registry.REVIEWED_UNEVALUABLE_EXCEPTIONS)
        review_registry.REVIEWED_UNEVALUABLE_EXCEPTIONS.clear()
        review_registry.REVIEWED_UNEVALUABLE_EXCEPTIONS.update(entries)
        try:
            return work()
        finally:
            review_registry.REVIEWED_UNEVALUABLE_EXCEPTIONS.clear()
            review_registry.REVIEWED_UNEVALUABLE_EXCEPTIONS.update(saved)

    excused = with_exceptions({exc_key: exc_entry}, lambda:
        reviewed_exception_status(exc_cell, "fixture-1999", "ab" * 32,
                                  "unevaluable", exc_entry["reason"]))
    assert excused[0] == "excepted", excused
    assert "reviewed exception" in excused[1]
    # An exception NEVER converts an active disagreement.
    stopped = with_exceptions({exc_key: exc_entry}, lambda:
        reviewed_exception_status(exc_cell, "fixture-1999", "ab" * 32,
                                  "stop", exc_entry["reason"]))
    assert stopped[0] == "stop", stopped
    # Different document sharing the slug: not ours, left alone.
    sibling = with_exceptions({exc_key: exc_entry}, lambda:
        reviewed_exception_status(exc_cell, "fixture-1999", "cd" * 32,
                                  "unevaluable", exc_entry["reason"]))
    assert sibling[0] == "unevaluable", sibling
    # STALE: the refusal moved, so the exception no longer describes reality.
    for bad_reason, needle in (
            ("referee: something else entirely", "stale"),
    ):
        try:
            with_exceptions({exc_key: exc_entry}, lambda:
                reviewed_exception_status(exc_cell, "fixture-1999", "ab" * 32,
                                          "unevaluable", bad_reason))
        except RefereeError as error:
            assert needle in str(error), error
        else:
            raise AssertionError("a stale exception was honoured")
    # Wrong subject bound to the key.
    try:
        with_exceptions({exc_key: {**exc_entry, "subject_key": "p1@0,0"}},
                        lambda: reviewed_exception_status(
                            exc_cell, "fixture-1999", "ab" * 32,
                            "unevaluable", exc_entry["reason"]))
    except RefereeError:
        pass
    else:
        raise AssertionError("an exception bound to another subject passed")

    # ---- C4a: a reviewed resolution is scored against THIS run's evidence --
    def resolved_cell(**overrides):
        value = {
            "ledger_state": "active_resolved",
            "latticed": 4, "emitted": 4, "audit_printed": 4,
            "emitted_indexes_valid": True,
            "referee": {"status": "measured", "compartments": 4,
                        "positions_match": True},
            "resolution_certificate": {
                "criterion": "reviewed-ledger-resolution-v1",
                "registry_key": ["0605-1999", 1, "p1c66"],
                "four_way": {"lattice": 4, "audit": 4,
                             "emitted": 4, "referee": 4},
                "resolved_reason_codes": ["competing-endpoint-topologies"],
                "reviewer": "self-test", "date": "2026-08-15",
            },
        }
        value.update(overrides)
        return value

    assert comparison(resolved_cell(), True)[0] == "agree"
    # Every one of the four signed counts is re-derived; if the corpus has
    # moved under a signed decision it STOPS rather than passing quietly.
    # (Moving `latticed` ALONE is not this case: emitted-vs-lattice disagreement
    # is the more fundamental `stale-generation` fault and is caught earlier,
    # which is the correct ordering -- a stale generation is not a stale
    # review.  Lattice drift reaches this check when the emission moved with
    # it, as a regenerated corpus does.)
    drifted_cases = {
        "audit alone": {"audit_printed": 5},
        "regenerated lattice and emission": {"latticed": 5, "emitted": 5},
        "the source itself": {"referee": {
            "status": "measured", "compartments": 5,
            "positions_match": True}},
        "the whole corpus": {
            "latticed": 5, "emitted": 5, "audit_printed": 5,
            "referee": {"status": "measured", "compartments": 5,
                        "positions_match": True}},
    }
    for name, overrides in drifted_cases.items():
        drifted = comparison(resolved_cell(**overrides), True)
        assert drifted[0] == "stop", (name, drifted)
        assert "moved since this resolution was reviewed" in drifted[1], name
    stale_generation = comparison(resolved_cell(latticed=5), True)
    assert stale_generation[0] == "stale-generation", stale_generation
    # A subject with no certificate is untouched by any of this.
    assert comparison(resolved_cell(resolution_certificate=None),
                      True)[0] == "agree"

    retained_emission = {
        subject["cell_id"]: {"valid": True}
        for subject in retained_result["subjects"]
        if subject["cell_id"] is not None
    }
    retained_emission[retained_cell["id"]] = {"valid": True}
    retained_inventory = validate_emission_inventory(
        retained_result, retained_emission)
    assert not retained_inventory["complete"]
    assert retained_inventory["retained_emitted_cell_ids"] == [
        retained_cell["id"]]

    # ------------------------------------------------------------------
    # THE RETAINED-TOPOLOGY INVARIANT and its one, source-checked exception.
    #
    # Four populations, and the whole point is that they are four and not two:
    #   (a) retained, topology UNRESOLVED  -> accepted, as it always was, and
    #       no corroboration is owed because nothing was certified;
    #   (b) retained, topology RESOLVED, reason re-derivable -> accepted ONLY
    #       after Poppler is asked the reason's own question;
    #   (c) retained, topology RESOLVED, reason absent/unknown -> refused;
    #   (d) (b) with a source that CONTRADICTS the reason -> refused.
    # ------------------------------------------------------------------
    # The reason tuple is read out of the registry rather than restated, so a
    # second entry added to that table without a fixture of its own trips this
    # pairing instead of quietly riding on the caption block's evidence.
    # R2a widened the table to three entries; DECISION A (2026-08-16) to
    # four. Each pairing is pinned here so a fifth cannot ride on the
    # existing fixtures.
    assert len(RETAINED_SUPPRESSION_SOURCE_CRITERIA) == 4
    caption_reason_codes = [
        "emission-suppressed-caption-block-not-character-cells"]
    assert (RETAINED_SUPPRESSION_SOURCE_CRITERIA[tuple(caption_reason_codes)]
            == SOURCE_CAPTION_BLOCK_CRITERION)
    assert (RETAINED_SUPPRESSION_SOURCE_CRITERIA[(
        "emission-suppressed-no-rectangular-owner",
        "painted-edge-partition")] == SOURCE_PARTITION_EDGE_CRITERION)
    assert (RETAINED_SUPPRESSION_SOURCE_CRITERIA[(
        "emission-suppressed-no-final-visible-band",)]
        == SOURCE_CROSSING_RULE_CRITERION)
    assert (RETAINED_SUPPRESSION_SOURCE_CRITERIA[(
        "emission-suppressed-compartment-rule",)]
        == SOURCE_CROSSING_RULE_CRITERION)

    # (a) An unresolved retained topology stays accepted, and stays free of any
    # corroboration obligation: there is no certified shape to corroborate.
    assert retained_result["subjects"][0]["state"] == "retained_unresolved"
    assert (retained_result["subjects"][0]["source_suppression_criterion"]
            is None)
    assert retained_result["suppression_obligations"] == {}
    assert all(
        subject["source_suppression_criterion"] is None
        for subject in retained_result["subjects"])

    def retained_variant(status: str, reason_codes: list[str]
                         ) -> dict[str, Any]:
        variant = clone(retained_ledger)
        subject_value = variant["pages"][0]["comb_subjects"][0]
        subject_value["reason_codes"] = list(reason_codes)
        subject_value["legacy_comb"]["resolution"].update({
            "status": status,
            # `validate_comb_topology` binds reasons to status, so an
            # unresolved variant has to carry evidence and a resolved one
            # must not.  The fixture obeys the schema it is testing.
            "reason_codes": (
                [] if status == "resolved" else ["legacy-continuity-only"]),
        })
        refresh_ledger_stats(variant)
        return variant

    # (b) A resolved topology under a re-derivable reason is admitted by the
    # ledger pass -- and the ledger records the debt rather than settling it.
    caption_ledger = retained_variant("resolved", caption_reason_codes)
    caption_result = validate_comb_ledger(
        retained_fixture_slug, caption_ledger, lattice_producer_bytes)
    caption_subject = caption_result["subjects"][0]
    assert caption_subject["state"] == "retained_unresolved"
    assert caption_subject["topology"]["resolution_status"] == "resolved"
    assert (caption_subject["source_suppression_criterion"]
            == SOURCE_CAPTION_BLOCK_CRITERION)
    assert caption_result["suppression_obligations"] == {
        retained_cell["id"]: SOURCE_CAPTION_BLOCK_CRITERION}
    # An UNRESOLVED topology under the same reason owes the same debt: the
    # corroboration is attached to the CLAIM, not to the topology status, so a
    # producer cannot dodge it by marking its own measurement unresolved.
    unresolved_caption = validate_comb_ledger(
        retained_fixture_slug,
        retained_variant("unresolved", caption_reason_codes),
        lattice_producer_bytes)
    assert unresolved_caption["suppression_obligations"] == {
        retained_cell["id"]: SOURCE_CAPTION_BLOCK_CRITERION}
    # DECISION A's tuple carries the same debt through both topology
    # statuses, and its criterion is the crossing-rule re-derivation -- the
    # subject's "dividers" must prove to be rules that outrun the comb band.
    for rule_status in ("resolved", "unresolved"):
        rule_refused = validate_comb_ledger(
            retained_fixture_slug,
            retained_variant(
                rule_status, ["emission-suppressed-compartment-rule"]),
            lattice_producer_bytes)
        rule_subject = rule_refused["subjects"][0]
        assert rule_subject["state"] == "retained_unresolved"
        assert (rule_subject["source_suppression_criterion"]
                == SOURCE_CROSSING_RULE_CRITERION)
        assert rule_refused["suppression_obligations"] == {
            retained_cell["id"]: SOURCE_CROSSING_RULE_CRITERION}

    # (c) A resolved topology under a reason this referee cannot re-derive is
    # the original guard, and it still closes.  Both shapes are covered: a
    # reason that is real but carries no source claim, and an invented one.
    for unrecognised in (
            # The first two moved INTO the table in R2a; the shapes stay
            # covered by reasons that remain outside it.
            ["emission-suppressed-unproved-multi-row-divider-corridor"],
            ["emission-suppressed-no-rectangular-owner"],
            ["emission-suppressed-because-the-producer-says-so"],
            [*caption_reason_codes, "and-one-more-reason"],
    ):
        assert tuple(unrecognised) not in RETAINED_SUPPRESSION_SOURCE_CRITERIA
        try:
            validate_comb_ledger(
                retained_fixture_slug,
                retained_variant("resolved", unrecognised),
                lattice_producer_bytes)
        except RefereeError:
            pass
        else:
            raise AssertionError(
                "a retained subject certified its own resolved topology "
                f"under {unrecognised}")

    # (d) The corroboration itself, against Poppler's vector output.  The
    # walls are the ones POPPLER draws; the census is Poppler's glyph bounds.
    def caption_glyph(x0: float, x1: float, ident: str,
                      clipped: bool = False,
                      y0: float = 3.0, y1: float = 4.0,
                      stroked: bool = False) -> UnsupportedRegion:
        prefix = MEASURED_GLYPH_REASON_PREFIXES[1 if stroked else 0]
        return UnsupportedRegion(
            x0, y0, x1, y1, f"{prefix}#glyph-{ident}", ident, 0.0, 40, clipped)

    caption_comb_contract = {
        "cells": 2, "divider_count": 1, "pitch_pt": 20.0,
        "pitch_min_pt": 20.0, "pitch_max_pt": 20.0,
        "slot_x": [0.0, 20.0, 40.0], "divider_x": [20.0],
        "divider_thickness_pt": 0.2, "divider_thicknesses_pt": [0.2],
        "divider_gray": 0.0, "divider_paint_seq": [1],
        "divider_paint_ranges": [[1, 1]],
        "y0": 2.0, "y1": 8.0, "height_pt": 6.0,
        "resolution": {
            "status": "resolved", "method": "self-test", "reason_codes": []},
    }
    caption_bbox = [0.0, 0.0, 40.0, 10.0]
    caption_source_cell = {
        "id": "p1c0", "subject_key": "p1@0.00,0.00,40.00,10.00",
        "x0": 0.0, "y0": 0.0, "x1": 40.0, "y1": 10.0,
        "comb": caption_comb_contract,
    }
    caption_published = {
        "legacy_bbox": caption_bbox,
        "topology": validate_comb_topology(
            caption_comb_contract, caption_bbox, "caption self-test"),
        "source_suppression_criterion": SOURCE_CAPTION_BLOCK_CRITERION,
    }

    # A comb's outer slot boundaries are its printed rails, so they are no
    # longer required to equal the subject's rectangle -- but the comb must
    # still be that subject's. It may sit inside the rectangle (the rectangle
    # rules a caption or a dash box beside the comb), and it may overhang it by
    # less than one of its own compartments (the rectangle's x is a fused mean
    # of every collinear bar on the line, and the rail is one of them). It may
    # not overhang by a whole compartment, and it may not miss the rectangle.
    for railed in (
            {"slot_x": [8.0, 20.0, 40.0], "divider_x": [20.0]},
            {"slot_x": [0.0, 20.0, 32.0], "divider_x": [20.0]},
            {"slot_x": [-0.4, 20.0, 40.4], "divider_x": [20.0]},
            {"slot_x": [8.0, 20.0, 32.0], "divider_x": [20.0]},
    ):
        validate_comb_topology(
            {**caption_comb_contract, **railed},
            caption_bbox, "railed self-test")
    for stolen in (
            {"slot_x": [-20.0, 0.0, 20.0], "divider_x": [0.0]},
            {"slot_x": [20.0, 40.0, 60.0], "divider_x": [40.0]},
            {"slot_x": [40.0, 60.0, 80.0], "divider_x": [60.0]},
            {"slot_x": [-40.0, -20.0, 0.0], "divider_x": [-20.0]},
    ):
        try:
            validate_comb_topology(
                {**caption_comb_contract, **stolen},
                caption_bbox, "stolen self-test")
        except RefereeError:
            continue
        raise AssertionError(
            f"a comb with a compartment centred outside its subject passed: "
            f"{stolen}")

    def caption_page(glyph_regions: Sequence[UnsupportedRegion]) -> SvgPage:
        return SvgPage(100, 100, [paint(20), *source_frame()],
                       list(glyph_regions), "caption")

    def caption_band(page_value: SvgPage) -> dict[str, Any]:
        return classify_band(
            caption_source_cell, page_value,
            ledger_state="retained_unresolved")

    prose = [caption_glyph(3, 4, "a"), caption_glyph(6, 7, "b"),
             caption_glyph(23, 24, "c"), caption_glyph(26, 27, "d")]
    prose_page = caption_page(prose)
    prose_band = caption_band(prose_page)
    assert prose_band["status"] == "measured", prose_band
    assert prose_band["compartments"] == 2, prose_band
    corroborated = retained_suppression_corroboration(
        caption_published, prose_band, prose_page, "caption self-test")
    assert corroborated["criterion"] == SOURCE_CAPTION_BLOCK_CRITERION
    assert corroborated["compartment_glyph_counts"] == [2, 2], corroborated
    # The walls the census used are Poppler's, not the ledger's.
    assert corroborated["source_divider_x"] == prose_band["source_divider_x"]

    def caption_refused(name: str, subject_value: dict[str, Any],
                        page_value: SvgPage,
                        band_value: dict[str, Any] | None = None) -> None:
        try:
            retained_suppression_corroboration(
                subject_value,
                caption_band(page_value) if band_value is None else band_value,
                page_value, "caption self-test")
        except RefereeError:
            return
        raise AssertionError(
            f"an uncorroborated caption suppression was accepted: {name}")

    # A compartment the source left empty is a character cell, not prose.
    caption_refused("empty compartment", caption_published, caption_page(
        [caption_glyph(3, 4, "a"), caption_glyph(6, 7, "b")]))
    # And so is one carrying a single glyph -- the `%`, the money point, the
    # TIN dash.  This is the boundary the threshold names, tested at it.
    caption_refused("one decoration glyph", caption_published, caption_page(
        [caption_glyph(3, 4, "a"), caption_glyph(6, 7, "b"),
         caption_glyph(23, 24, "c")]))
    assert CHARACTER_CELL_MAX_PRINTED_GLYPHS == 1
    two_per_compartment = retained_suppression_corroboration(
        caption_published,
        caption_band(caption_page(prose)), caption_page(prose),
        "caption self-test")
    assert min(two_per_compartment["compartment_glyph_counts"]) == (
        CHARACTER_CELL_MAX_PRINTED_GLYPHS + 1)
    # Ink this parser cannot say is printed, or cannot say belongs to this
    # rectangle, is not evidence that the rectangle is a caption.
    caption_refused("clipped glyph", caption_published, caption_page(
        [*prose[:3], caption_glyph(26, 27, "d", clipped=True)]))
    caption_refused("glyph outside the rectangle", caption_published,
                    caption_page([*prose[:3],
                                  caption_glyph(26, 27, "d", y0=9.5, y1=10.5)]))
    caption_refused("glyph left of the rectangle", caption_published,
                    caption_page([*prose[:3],
                                  caption_glyph(-1.0, 0.5, "d")]))
    # A stroked glyph bound counts exactly like a filled one: both are text.
    stroked_prose = [*prose[:3], caption_glyph(26, 27, "d", stroked=True)]
    assert retained_suppression_corroboration(
        caption_published, caption_band(caption_page(stroked_prose)),
        caption_page(stroked_prose), "caption self-test",
    )["compartment_glyph_counts"] == [2, 2]
    # A region that merely mentions a glyph but carries no measured bound --
    # `parse_svg` publishes those over the WHOLE PAGE, meaning "text exists
    # here that this parser could not place" -- is never counted into a
    # compartment.  Asserted on the census directly, because such a region
    # also makes the band itself unmeasurable, which is the fail-closed
    # outcome the refusal below records.
    unplaced_glyph = UnsupportedRegion(
        0.0, 0.0, 100.0, 100.0,
        "unsupported glyph use target: #glyph-d", "d", 0.0, 40, False)
    assert measured_glyph_boxes(
        caption_page([unplaced_glyph]), caption_bbox) == []
    caption_refused("unplaced glyph region", caption_published, caption_page(
        [*prose[:3], unplaced_glyph]))

    # The band half of the corroboration: the source must actually draw the
    # compartments the retained topology claims, or there is nothing to census.
    caption_refused(
        "unmeasurable band", caption_published, prose_page,
        {"status": "unevaluable", "reason": "self-test"})
    # And the same refusal when the unevaluable verdict arrives carrying a
    # full measured payload.  `status` is the verdict; the topology fields
    # beside it are working notes, and a band the referee could not measure
    # corroborates nothing however complete those notes look.
    caption_refused(
        "unevaluable band with a measured payload", caption_published,
        prose_page,
        {**prose_band, "status": "unevaluable",
         "reason": "self-test residue"})
    caption_refused(
        "compartment count disagreement", caption_published, prose_page,
        {**prose_band, "compartments": 3,
         "source_divider_x": [13.0, 26.0]})
    caption_refused(
        "anchors do not sit where the ledger says", caption_published,
        prose_page, {**prose_band, "positions_match": False})
    # A criterion with no re-derivation behind it fails closed rather than
    # falling through to an accept.
    caption_refused(
        "criterion with no re-derivation",
        {**caption_published,
         "source_suppression_criterion": "some-future-criterion-v1"},
        prose_page)

    # (e) The two R2a criteria, both verdicts each.  These return verdict
    # certificates and never raise on a negative: "the paper does not show
    # it" leaves the subject unevaluable and unretirable, which is the
    # fail-closed answer at the right level (1800-2018 p1c4 is the measured
    # negative in the corpus -- its only full-span edge has a 42.55pt void).
    def partition_subject(keys: list[str]) -> dict[str, Any]:
        return {
            "source_suppression_criterion": SOURCE_PARTITION_EDGE_CRITERION,
            "ledger": {
                "legacy_bbox": [0.0, 0.0, 40.0, 20.0],
                "mapped_partition_subject_keys": keys,
            },
        }

    split_keys = ["p1@0.0,0.0,40.0,10.0", "p1@0.0,10.0,40.0,20.0"]
    painted_split = retained_suppression_corroboration(
        partition_subject(split_keys),
        {"status": "measured"},
        SvgPage(100, 100, [
            Paint(-0.1, 9.9, 40.1, 10.1, 0.0, 5, "stroke", "split-rule"),
        ], [], "x"),
        "partition self-test")
    assert painted_split["criterion"] == SOURCE_PARTITION_EDGE_CRITERION
    assert painted_split["corroborated"] is True, painted_split
    assert painted_split["certifying_edge"]["axis"] == "h", painted_split

    # An edge drawn in PAPER: a knockout band below the line against ink
    # above it.  Nothing is painted ON the line, but the final tones differ
    # across it -- an edge in the final picture.
    knockout_split = retained_suppression_corroboration(
        partition_subject(split_keys),
        {"status": "measured"},
        SvgPage(100, 100, [
            Paint(-0.1, -0.1, 40.1, 10.0, 0.5, 5, "fill", "tint-above"),
            Paint(-0.1, 10.0, 40.1, 20.1, 1.0, 6, "fill", "knockout-below"),
        ], [], "x"),
        "partition self-test")
    assert knockout_split["corroborated"] is True, knockout_split

    # A void: paper on the line and the same paper on both sides.  Refused,
    # and refused as a VERDICT -- no exception.
    void_split = retained_suppression_corroboration(
        partition_subject(split_keys),
        {"status": "measured"},
        SvgPage(100, 100, [], [], "x"),
        "partition self-test")
    assert void_split["corroborated"] is False, void_split
    assert void_split["full_span_edges_checked"] == 1, void_split

    # A painted edge that does NOT span the rectangle certifies nothing: a
    # partial interior stroke splits a corner, not the subject.
    tee_keys = ["p1@0.0,0.0,20.0,10.0", "p1@20.0,0.0,40.0,10.0",
                "p1@0.0,10.0,40.0,20.0"]
    partial_only = retained_suppression_corroboration(
        partition_subject(tee_keys),
        {"status": "measured"},
        SvgPage(100, 100, [
            Paint(19.9, -0.1, 20.1, 10.0, 0.0, 5, "stroke", "half-rule"),
        ], [], "x"),
        "partition self-test")
    assert partial_only["corroborated"] is False, partial_only

    def crossing_subject() -> dict[str, Any]:
        return {
            "source_suppression_criterion": SOURCE_CROSSING_RULE_CRITERION,
            "ledger": {
                "legacy_bbox": [0.0, 10.0, 40.0, 20.0],
                "legacy_comb": {"divider_x": [15.0]},
            },
        }

    crossing = retained_suppression_corroboration(
        crossing_subject(),
        {"status": "measured"},
        SvgPage(100, 100, [
            Paint(14.9, 0.0, 15.1, 30.0, 0.0, 5, "stroke", "table-rule"),
        ], [], "x"),
        "crossing self-test")
    assert crossing["criterion"] == SOURCE_CROSSING_RULE_CRITERION
    assert crossing["corroborated"] is True, crossing

    # A divider that ends INSIDE the subject is comb-scoped ink, and the
    # crossing claim is refused as a verdict.
    hanging = retained_suppression_corroboration(
        crossing_subject(),
        {"status": "measured"},
        SvgPage(100, 100, [
            Paint(14.9, 12.0, 15.1, 18.0, 0.0, 5, "stroke", "hanging-tick"),
        ], [], "x"),
        "crossing self-test")
    assert hanging["corroborated"] is False, hanging

    # And the accounting that makes the corroboration unskippable: an admitted
    # reason whose re-derivation never ran is an error, not a pass.
    owed = {"p1c0": SOURCE_CAPTION_BLOCK_CRITERION}
    assert_suppression_corroborations_exhaustive("self-test", {}, {})
    assert_suppression_corroborations_exhaustive("self-test", owed, dict(owed))
    for name, obligations_value, corroborations_value in (
            ("skipped", owed, {}),
            ("invented", {}, owed),
            ("substituted criterion", owed, {"p1c0": "other-criterion-v1"}),
            ("substituted subject", owed,
             {"p1c9": SOURCE_CAPTION_BLOCK_CRITERION}),
    ):
        try:
            assert_suppression_corroborations_exhaustive(
                "self-test", obligations_value, corroborations_value)
        except RefereeError:
            pass
        else:
            raise AssertionError(
                f"suppression corroboration accounting was not enforced: "
                f"{name}")

    def unavailable_position(field: str) -> dict[str, Any]:
        # The fixture derives BOTH the axis and the tolerance from the field,
        # never from a literal.  Publishing HTML_GEOMETRY_EPSILON_PT on all
        # five here is what let the tolerance category error survive: no
        # producer has ever emitted that record.
        axis = "outer" if AUDIT_POSITION_FIELDS[field][1] else "internal"
        return {
            "comparable": False,
            "tolerance_pt": AUDIT_POSITION_TOLERANCE_PT[field],
            f"actual_{axis}_edges_x": [1.0, 2.0],
            f"expected_{axis}_edges_x": None,
            "count_matches": None,
            "deltas_pt": None,
            "matches": None,
            "unavailable_reason": "self-test source topology is unavailable",
        }

    self_audit_layout_sha = "a" * 64

    def self_owner_certificate(cell_id: str) -> dict[str, Any]:
        return {
            "criterion": AUDIT_OWNER_CERTIFICATE_CRITERION,
            "valid": True,
            "layout_sha256": self_audit_layout_sha,
            "page": 1,
            "cell_id": cell_id,
            "legacy_cell_id": cell_id,
            "subject_key": "p1@0,0,2,1",
            "legacy_bbox": ["0", "0", "2", "1"],
            "bbox_number_format": "canonical-decimal-string-v1",
            "state": "active_resolved",
            "supplies_topology": False,
        }

    def self_owner_binding(cell_ids: Sequence[str]) -> dict[str, Any]:
        return {
            "layout_sha256": self_audit_layout_sha,
            "cells": {
                cell_id: self_owner_certificate(cell_id)
                for cell_id in cell_ids
            },
        }

    def self_invalid_owner(reason: str = "self-test invalid owner"
                           ) -> dict[str, Any]:
        return {
            "criterion": AUDIT_OWNER_CERTIFICATE_CRITERION,
            "valid": False,
            "reason": reason,
            "supplies_topology": False,
        }

    def source_unevaluable_offender(cell_id: str) -> dict[str, Any]:
        item: dict[str, Any] = {
            "cell": cell_id,
            "page": 1,
            "slots": 2,
            "latticed": 2,
            "printed": None,
            "printed_divider_x": [],
            "emission_state": "physical-slots",
            "physical_slots": 2,
            "declared_slots": 2,
            "emitted_occurrences": 1,
            "slot_indexes": [0, 1],
            "input_slot_indexes": [[0], [1]],
            "slot_geometry": [],
            "emission_container_binding": {
                "expected_page": 1,
                "emitted_id_page": 1,
                "emitted_dom_page": 1,
                "page_matches": True,
                "expected_rect": [0.0, 0.0, 2.0, 1.0],
                "actual_rect": [0.0, 0.0, 2.0, 1.0],
                "rect_deltas_pt": [0.0, 0.0, 0.0, 0.0],
                "rect_matches": True,
                "tolerance_pt": HTML_GEOMETRY_EPSILON_PT,
            },
            "source_owner_certificate": self_owner_certificate(cell_id),
            "layout_relation": "unevaluable",
            "emission_relation": "match",
            "failure_kinds": ["source-topology-unevaluable"],
            "why": "self-test source topology is unavailable",
        }
        for field in AUDIT_POSITION_FIELDS:
            item[field] = unavailable_position(field)
        item["effective_emission_state"] = "physical-slots"
        return item

    def layout_mismatch_offender(cell_id: str) -> dict[str, Any]:
        item = source_unevaluable_offender(cell_id)
        item.update({
            "printed": 1,
            "layout_relation": "mismatch",
            "emission_relation": "mismatch-printed",
            "failure_kinds": [
                "layout-printed-mismatch", "emission-printed-mismatch"],
            "why": (
                "self-test layout has two slots but source prints one "
                "compartment"),
            # An outer compartment is bounded by the rail's INNER INK EDGE, so
            # that is the number this referee re-derives the audit's own
            # expectation from. The centres are stated beside them and are
            # deliberately different values, so a referee reading the centre
            # cannot pass by coincidence.
            "source_frame_geometry": {
                "left_rail": {
                    "center_x": -0.12, "ink_x0": -0.24, "ink_x1": 0.0},
                "right_rail": {
                    "center_x": 2.12, "ink_x0": 2.0, "ink_x1": 2.24},
            },
        })
        item["emission_layout_position"] = {
            "comparable": True,
            "tolerance_pt": (
                AUDIT_POSITION_TOLERANCE_PT["emission_layout_position"]),
            "actual_internal_edges_x": [1.0],
            "expected_internal_edges_x": [1.0],
            "count_matches": True,
            "deltas_pt": [0.0],
            "matches": True,
        }
        item["emission_layout_outer_position"] = {
            "comparable": True,
            "tolerance_pt": (
                AUDIT_POSITION_TOLERANCE_PT["emission_layout_outer_position"]),
            "actual_outer_edges_x": [0.0, 2.0],
            "expected_outer_edges_x": [0.0, 2.0],
            "count_matches": True,
            "deltas_pt": [0.0, 0.0],
            "matches": True,
        }
        item["emission_source_position"] = {
            "comparable": False,
            "tolerance_pt": (
                AUDIT_POSITION_TOLERANCE_PT["emission_source_position"]),
            "actual_internal_edges_x": [1.0],
            "expected_internal_edges_x": [],
            "count_matches": None,
            "deltas_pt": None,
            "matches": None,
            "unavailable_reason": "emitted/source slot counts differ",
        }
        item["emission_source_outer_position"] = {
            "comparable": False,
            "tolerance_pt": (
                AUDIT_POSITION_TOLERANCE_PT["emission_source_outer_position"]),
            "actual_outer_edges_x": [0.0, 2.0],
            "expected_outer_edges_x": [0.0, 2.0],
            "count_matches": None,
            "deltas_pt": None,
            "matches": None,
            "unavailable_reason": "emitted/source slot counts differ",
        }
        item["layout_source_outer_position"] = {
            "comparable": False,
            "tolerance_pt": (
                AUDIT_POSITION_TOLERANCE_PT["layout_source_outer_position"]),
            "actual_outer_edges_x": [0.0, 2.0],
            "expected_outer_edges_x": [0.0, 2.0],
            "count_matches": None,
            "deltas_pt": None,
            "matches": None,
            "unavailable_reason": "layout/source slot counts differ",
        }
        return item

    def noncomb_binding_offender(
            cell_id: str, failure_kind: str) -> dict[str, Any]:
        if failure_kind == "emitted-cell-binding-invalid":
            layout_relation = "cell-binding-invalid"
            emission_state = "cell-binding-invalid"
        elif failure_kind == "unowned-live-comb-markup":
            layout_relation = "not-owned"
            emission_state = "raw-live-comb-markup"
        else:
            raise AssertionError("unknown self-test non-comb failure kind")
        return {
            "cell": cell_id,
            "page": 1,
            "slots": None,
            "latticed": None,
            "printed": None,
            "printed_divider_x": [],
            "emission_state": emission_state,
            "physical_slots": None,
            "declared_slots": None,
            "emitted_occurrences": 1,
            "layout_relation": layout_relation,
            "emission_relation": "invalid",
            "failure_kinds": [failure_kind],
            "why": "self-test canonical-looking non-comb binding offender",
        }

    def comb_assertion(
            offenders: list[dict[str, Any]],
            *,
            expected_ids: list[str],
            emitted_ids: list[str] | None = None,
            ) -> dict[str, Any]:
        emitted = list(expected_ids if emitted_ids is None else emitted_ids)
        mismatch_count = sum(
            item.get("layout_relation") == "mismatch"
            for item in offenders)
        unevaluable_count = sum(
            item.get("layout_relation") in {
                "unevaluable", "duplicate-subject", "inventory-invalid"}
            for item in offenders)
        behind_count = sum(
            audit_offender_dimensions(item)[
                "dimensions"]["emission_behind"]
            for item in offenders)
        invalid_count = sum(
            audit_offender_dimensions(item)[
                "dimensions"]["emission_invalid"]
            for item in offenders)
        unexpected = sorted(set(emitted) - set(expected_ids))
        assertion = {
            "holds": not offenders,
            "reason": (
                "" if not offenders
                else f"{len(offenders)} self-test offender(s)"),
            "offenders": offenders,
            "combs_expected": len(expected_ids),
            "combs_checked": len(expected_ids),
            "expected_comb_ids": expected_ids,
            "checked_comb_ids": list(expected_ids),
            "emitted_comb_ids": sorted(emitted),
            "unexpected_emitted_comb_ids": unexpected,
            "duplicate_layout_comb_ids": [],
            "duplicate_emitted_cell_ids": [],
            "raw_live_comb_issues": 0,
            "emitted_cell_binding_issues": len(unexpected),
            "inventory_complete": not unexpected,
            "layout_mismatches": mismatch_count,
            "layout_unevaluable": unevaluable_count,
            "owner_certificates_valid": len(expected_ids),
            "owner_certificates_invalid": 0,
            "source_u_frame_evaluable": sum(
                item.get("cell") in expected_ids
                and item.get("printed") is not None
                and item.get("source_frame_geometry") is not None
                for item in offenders
            ),
            "source_certified_unframed_evaluable": 0,
            "emission_behind_layout": behind_count,
            "emission_invalid": invalid_count,
            # Z1's declared schema change, in the fixture too: this assertion
            # is now incomplete without it, and the referee is required to say
            # so rather than default it to zero.
            "decided_by_review": 0,
            "decided_by_review_subjects": [],
        }
        invalid_owner_ids = {
            item.get("cell") for item in offenders
            if item.get("cell") in expected_ids
            and isinstance(item.get("source_owner_certificate"), dict)
            and item["source_owner_certificate"].get("valid") is False
        }
        assertion["owner_certificates_invalid"] = len(invalid_owner_ids)
        assertion["owner_certificates_valid"] = (
            len(expected_ids) - len(invalid_owner_ids))
        checked_source_unevaluable = {
            item.get("cell") for item in offenders
            if item.get("cell") in expected_ids
            and item.get("layout_relation") in {
                "unevaluable", "duplicate-subject"}
        }
        assertion["source_certified_unframed_evaluable"] = (
            len(expected_ids)
            - len(checked_source_unevaluable)
            - assertion["source_u_frame_evaluable"]
        )
        if any(
                item.get("layout_relation") in {
                    "duplicate-subject", "inventory-invalid",
                    "registry-invalid",
                }
                for item in offenders):
            assertion["inventory_complete"] = False
        if offenders:
            assertion.update({
                "offender_count": len(offenders),
                "offenders_published": len(offenders),
                "offenders_omitted": 0,
                "offenders_complete": True,
            })
        return assertion

    held_assertion = comb_assertion([], expected_ids=["p1c1"])
    audit_pass = audit_evidence({
        "comb_slots_match_printed": True,
        "assertions": {"comb_slots_match_printed": held_assertion},
    }, self_owner_binding(["p1c1"]))
    assert audit_pass["assertion_valid"]
    assert not audit_pass["complete"] and audit_pass["offender_count"] == 0
    one_offender = source_unevaluable_offender("p1c1")
    broken_assertion = comb_assertion(
        [one_offender], expected_ids=["p1c1"])
    audit_broken = audit_evidence({
        "comb_slots_match_printed": False,
        "assertions": {"comb_slots_match_printed": broken_assertion},
    }, self_owner_binding(["p1c1"]))
    assert audit_broken["assertion_valid"]
    assert audit_broken["layout_unevaluable"] == 1
    independent_relations = comb_assertion(
        [one_offender, layout_mismatch_offender("p1c2")],
        expected_ids=["p1c1", "p1c2"],
    )
    independent_audit = audit_evidence({
        "comb_slots_match_printed": False,
        "assertions": {"comb_slots_match_printed": independent_relations},
    }, self_owner_binding(["p1c1", "p1c2"]))
    assert independent_audit["assertion_valid"]
    assert independent_audit["offender_count"] == 2
    assert independent_audit["layout_mismatches"] == 1
    assert independent_audit["layout_unevaluable"] == 1

    # Z1: the referee's own three-way source partition. The partition was
    # previously uncovered here, so a reviewed decision could have been added
    # to it without anything proving the arithmetic still refuses a forgery.
    # The accept path proves the third class is genuinely admitted; each
    # forgery is then refused separately.
    def reviewed_subject(cell: str = "p1c1") -> dict[str, Any]:
        return {
            "cell": cell, "printed": 3, "latticed": 3,
            "reviewed_comb_topology": {
                "criterion": "reviewed-comb-topology-v1", "valid": True,
                "compartments": 3, "source_sha256": "0" * 64,
                "reviewer": "self-test", "citation": "self-test",
            },
        }

    def reviewed_errors(mutate=None, offenders=()) -> list[str]:
        assertion = comb_assertion(
            list(offenders), expected_ids=["p1c1", "p1c2"])
        assertion["decided_by_review"] = 1
        assertion["decided_by_review_subjects"] = [reviewed_subject()]
        # A reviewed cell is carried by the reviewed term, so it has to come
        # OUT of the unframed term or the partition counts it twice.
        if not offenders:
            assertion["source_certified_unframed_evaluable"] -= 1
        if mutate is not None:
            mutate(assertion)
        evidence = audit_evidence({
            "comb_slots_match_printed": not offenders,
            "assertions": {"comb_slots_match_printed": assertion},
        }, self_owner_binding(["p1c1", "p1c2"]))
        return [error for error in evidence["errors"]
                if "partition" in error or "reviewed" in error]

    assert not reviewed_errors(), (
        "a correctly partitioned reviewed subject must be accepted: "
        f"{reviewed_errors()}")
    reviewed_forgeries: list[tuple[str, Any, str, tuple[Any, ...]]] = [
        (
            "u-frame inflated to absorb the reviewed cell",
            lambda value: value.update({
                "source_u_frame_evaluable":
                    value["source_u_frame_evaluable"] + 1}),
            "source frame/unframed/reviewed counts do not partition",
            (),
        ),
        (
            "reviewed count disagrees with its subject list",
            lambda value: value.update({"decided_by_review": 2}),
            "reviewed-topology subjects disagree with their count",
            (),
        ),
        (
            "reviewed subject names a cell this form never checked",
            lambda value: value["decided_by_review_subjects"][0].update({
                "cell": "p1c9"}),
            "reviewed-topology subject is not a checked cell",
            (),
        ),
        (
            "reviewed cell is still published source-unevaluable",
            None,
            "counts a reviewed cell as source unevaluable",
            (source_unevaluable_offender("p1c1"),),
        ),
    ]
    for label, mutate, needle, offenders in reviewed_forgeries:
        found = reviewed_errors(mutate, offenders)
        assert any(needle in error for error in found), (
            f"{label} must be refused; got {found}")

    invalid_geometry = layout_mismatch_offender("p1c1")
    invalid_geometry.update({
        "emission_state": "invalid-slot-geometry",
        "effective_emission_state": "invalid-slot-geometry",
        "emission_relation": "invalid",
        "failure_kinds": [
            "layout-printed-mismatch", "invalid-emission"],
        "why": (
            "self-test source disagrees while physical emission geometry "
            "is independently invalid"),
    })
    invalid_geometry_assertion = comb_assertion(
        [invalid_geometry], expected_ids=["p1c1"])
    invalid_geometry_audit = audit_evidence({
        "comb_slots_match_printed": False,
        "assertions": {
            "comb_slots_match_printed": invalid_geometry_assertion},
    }, self_owner_binding(["p1c1"]))
    assert invalid_geometry_audit["assertion_valid"], invalid_geometry_audit
    false_invalid_count_relation = clone(invalid_geometry_assertion)
    false_invalid_count_relation["offenders"][0][
        "failure_kinds"].append("emission-printed-mismatch")
    assert not audit_evidence({
        "comb_slots_match_printed": False,
        "assertions": {
            "comb_slots_match_printed": false_invalid_count_relation},
    }, self_owner_binding(["p1c1"]))["assertion_valid"]

    # Valid owner certificates are exact identity-only records.  They bind to
    # the retained layout SHA, page/cell/subject/state and canonical Decimal
    # bbox.  No individual field may drift while the topology relation passes.
    for label, mutate in (
        ("layout-sha", lambda cert: cert.__setitem__(
            "layout_sha256", "b" * 64)),
        ("subject", lambda cert: cert.__setitem__(
            "subject_key", "p1@0,0,3,1")),
        ("noncanonical-bbox", lambda cert: cert.__setitem__(
            "legacy_bbox", ["0", "0", "2.0", "1"])),
        ("state", lambda cert: cert.__setitem__(
            "state", "active_unresolved")),
        ("topology-claim", lambda cert: cert.__setitem__(
            "supplies_topology", True)),
        ("extra-key", lambda cert: cert.__setitem__("extra", False)),
    ):
        mutated_assertion = clone(broken_assertion)
        mutate(mutated_assertion["offenders"][0][
            "source_owner_certificate"])
        mutated_audit = audit_evidence({
            "comb_slots_match_printed": False,
            "assertions": {
                "comb_slots_match_printed": mutated_assertion},
        }, self_owner_binding(["p1c1"]))
        assert not mutated_audit["assertion_valid"], (
            label, mutated_audit)

    nested_owner_assertion = clone(broken_assertion)
    nested_owner_assertion["offenders"][0]["source_topology_evidence"] = {
        "criterion": "unanimous-source-derived-topology-required",
        "owner_certificate": clone(
            nested_owner_assertion["offenders"][0][
                "source_owner_certificate"]),
    }
    nested_owner_audit = audit_evidence({
        "comb_slots_match_printed": False,
        "assertions": {
            "comb_slots_match_printed": nested_owner_assertion},
    }, self_owner_binding(["p1c1"]))
    assert nested_owner_audit["assertion_valid"], nested_owner_audit
    unequal_nested_assertion = clone(nested_owner_assertion)
    unequal_nested_assertion["offenders"][0][
        "source_topology_evidence"]["owner_certificate"][
            "layout_sha256"] = "b" * 64
    unequal_nested_audit = audit_evidence({
        "comb_slots_match_printed": False,
        "assertions": {
            "comb_slots_match_printed": unequal_nested_assertion},
    }, self_owner_binding(["p1c1"]))
    assert not unequal_nested_audit["assertion_valid"], unequal_nested_audit

    invalid_owner_offender = source_unevaluable_offender("p1c1")
    invalid_owner_offender["source_owner_certificate"] = self_invalid_owner()
    invalid_owner_offender["source_topology_evidence"] = {
        "criterion": AUDIT_OWNER_CERTIFICATE_CRITERION,
        "owner_certificate": clone(
            invalid_owner_offender["source_owner_certificate"]),
    }
    invalid_owner_assertion = comb_assertion(
        [invalid_owner_offender], expected_ids=["p1c1"])
    invalid_owner_audit = audit_evidence({
        "comb_slots_match_printed": False,
        "assertions": {
            "comb_slots_match_printed": invalid_owner_assertion},
    }, self_owner_binding(["p1c1"]))
    assert invalid_owner_audit["assertion_valid"], invalid_owner_audit
    assert invalid_owner_audit["owner_certificates_valid"] == 0
    assert invalid_owner_audit["owner_certificates_invalid"] == 1
    assert invalid_owner_audit["source_u_frame_evaluable"] == 0
    assert invalid_owner_audit[
        "source_certified_unframed_evaluable"] == 0
    invalid_owner_extra_topology = clone(invalid_owner_assertion)
    invalid_owner_extra_topology["offenders"][0][
        "source_topology_evidence"]["divider_x"] = [1.0]
    assert not audit_evidence({
        "comb_slots_match_printed": False,
        "assertions": {
            "comb_slots_match_printed": invalid_owner_extra_topology},
    }, self_owner_binding(["p1c1"]))["assertion_valid"]

    invalid_owner_with_topology = layout_mismatch_offender("p1c1")
    invalid_owner_with_topology["source_owner_certificate"] = (
        self_invalid_owner())
    invalid_owner_with_topology["source_topology_evidence"] = {
        "criterion": AUDIT_OWNER_CERTIFICATE_CRITERION,
        "owner_certificate": clone(
            invalid_owner_with_topology["source_owner_certificate"]),
    }
    try:
        audit_offender_dimensions(
            invalid_owner_with_topology,
            self_owner_certificate("p1c1"))
    except RefereeError:
        pass
    else:
        raise AssertionError(
            "invalid owner certificate supplied a measured topology")

    duplicate_subject_offender = {
        "cell": "p1c1",
        "page": 1,
        "slots": 2,
        "latticed": None,
        "printed": None,
        "printed_divider_x": [],
        "emission_state": "physical-slots",
        "physical_slots": 2,
        "declared_slots": 2,
        "emitted_occurrences": 1,
        "source_owner_certificate": self_invalid_owner(
            "self-test duplicate layout owner"),
        "layout_relation": "duplicate-subject",
        "emission_relation": "unbound",
        "failure_kinds": ["duplicate-layout-subject"],
        "why": "self-test layout has two subjects with this id",
    }
    duplicate_subject_assertion = comb_assertion(
        [duplicate_subject_offender], expected_ids=["p1c1"])
    duplicate_subject_assertion["duplicate_layout_comb_ids"] = ["p1c1"]
    duplicate_subject_audit = audit_evidence({
        "comb_slots_match_printed": False,
        "assertions": {
            "comb_slots_match_printed": duplicate_subject_assertion},
    }, self_owner_binding(["p1c1"]))
    assert duplicate_subject_audit["assertion_valid"], (
        duplicate_subject_audit)
    assert duplicate_subject_audit["owner_certificates_invalid"] == 1
    assert duplicate_subject_audit["owner_certificates_valid"] == 0
    for invented_topology in (
        {
            "criterion": "invented-duplicate-subject-topology",
            "divider_x": [1.0],
        },
        {
            "printed_compartments": 2,
            "owner_certificate": clone(
                duplicate_subject_offender[
                    "source_owner_certificate"]),
        },
    ):
        invented_duplicate = clone(duplicate_subject_assertion)
        invented_duplicate["offenders"][0][
            "source_topology_evidence"] = invented_topology
        invented_duplicate_audit = audit_evidence({
            "comb_slots_match_printed": False,
            "assertions": {
                "comb_slots_match_printed": invented_duplicate},
        }, self_owner_binding(["p1c1"]))
        assert not invented_duplicate_audit["assertion_valid"], (
            invented_duplicate_audit)

    registry_offender = {
        "cell": "<comb-owner-registry>",
        "page": None,
        "slots": None,
        "latticed": None,
        "printed": None,
        "printed_divider_x": [],
        "emission_state": "not-evaluated",
        "effective_emission_state": "not-evaluated",
        "physical_slots": None,
        "declared_slots": None,
        "emitted_occurrences": 0,
        "source_owner_certificate": self_invalid_owner(
            "self-test global owner registry failure"),
        "layout_relation": "registry-invalid",
        "emission_relation": "not-evaluated",
        "failure_kinds": ["comb-owner-registry-invalid"],
        "why": "self-test global owner registry failure",
    }
    registry_assertion = comb_assertion(
        [registry_offender, invalid_owner_offender],
        expected_ids=["p1c1"],
    )
    registry_audit = audit_evidence({
        "comb_slots_match_printed": False,
        "assertions": {"comb_slots_match_printed": registry_assertion},
    }, self_owner_binding(["p1c1"]))
    assert registry_audit["assertion_valid"], registry_audit
    assert registry_audit["owner_certificates_invalid"] == 1
    assert registry_audit["owner_certificates_valid"] == 0
    assert registry_audit["combs_checked"] == 1
    assert registry_audit["offender_dimensions"][
        "<comb-owner-registry>"]["source_owner_certificate"][
            "valid"] is False

    audit_truncated_record = {
        "comb_slots_match_printed": False,
        "assertions": {"comb_slots_match_printed": clone(broken_assertion)},
    }
    audit_truncated_record["assertions"][
        "comb_slots_match_printed"]["offender_count"] = 2
    audit_truncated_record["assertions"][
        "comb_slots_match_printed"]["offenders_omitted"] = 1
    audit_truncated_record["assertions"][
        "comb_slots_match_printed"]["offenders_complete"] = False
    assert not audit_evidence(audit_truncated_record)["assertion_valid"]

    duplicate_offender_record = {
        "comb_slots_match_printed": False,
        "assertions": {"comb_slots_match_printed": comb_assertion(
            [one_offender, clone(one_offender)],
            expected_ids=["p1c1"],
        )},
    }
    assert not audit_evidence(
        duplicate_offender_record)["assertion_valid"]

    malformed_relation = clone(one_offender)
    malformed_relation["failure_kinds"] = ["layout-printed-mismatch"]
    malformed_relation["layout_relation"] = "mismatch"
    malformed_assertion = clone(broken_assertion)
    malformed_assertion["offenders"] = [malformed_relation]
    assert not audit_evidence({
        "comb_slots_match_printed": False,
        "assertions": {"comb_slots_match_printed": malformed_assertion},
    })["assertion_valid"]

    bogus_failure = clone(one_offender)
    bogus_failure["failure_kinds"].append("invented-self-test-failure")
    bogus_assertion = clone(broken_assertion)
    bogus_assertion["offenders"] = [bogus_failure]
    assert not audit_evidence({
        "comb_slots_match_printed": False,
        "assertions": {"comb_slots_match_printed": bogus_assertion},
    })["assertion_valid"]

    false_position = clone(one_offender)
    false_position["emission_layout_position"] = {
        "comparable": True,
        "tolerance_pt": HTML_GEOMETRY_EPSILON_PT,
        "actual_internal_edges_x": [1.0],
        "expected_internal_edges_x": [2.0],
        "count_matches": True,
        "deltas_pt": [-1.0],
        "matches": False,
    }
    false_position_assertion = clone(broken_assertion)
    false_position_assertion["offenders"] = [false_position]
    assert not audit_evidence({
        "comb_slots_match_printed": False,
        "assertions": {
            "comb_slots_match_printed": false_position_assertion},
    })["assertion_valid"]

    # Each of the five position relations is pinned to its OWN constant, and
    # the referee must reject a swap in either direction: an audit that widened
    # a same-representation emitted/layout comparison to the source tolerance
    # would be hiding real emission drift, and one that narrowed a source
    # comparison to the emitted epsilon would be inventing mismatches. The
    # positive case is covered by the corpus itself -- every published offender
    # carries POSITION_TOL_PT on the three `source` relations.
    for swap_field, pinned in AUDIT_POSITION_TOLERANCE_PT.items():
        other = (
            POSITION_TOL_PT if pinned == HTML_GEOMETRY_EPSILON_PT
            else HTML_GEOMETRY_EPSILON_PT)
        assert other != pinned
        swapped = clone(one_offender)
        swapped[swap_field]["tolerance_pt"] = other
        swapped_assertion = clone(broken_assertion)
        swapped_assertion["offenders"] = [swapped]
        swapped_result = audit_evidence({
            "comb_slots_match_printed": False,
            "assertions": {"comb_slots_match_printed": swapped_assertion},
        }, self_owner_binding(["p1c1"]))
        assert not swapped_result["assertion_valid"], swap_field
        assert any(
            "changes the fixed position tolerance" in message
            for message in swapped_result["errors"]), swap_field

    missing_without_offender = comb_assertion(
        [], expected_ids=["p1c1"], emitted_ids=[])
    assert not audit_evidence({
        "comb_slots_match_printed": True,
        "assertions": {
            "comb_slots_match_printed": missing_without_offender},
    })["assertion_valid"]

    active_order = [
        subject["cell_id"] for subject in ledger_result["subjects"]]
    active_slots = {
        cell_id: {"valid": True} for cell_id in active_order}
    active_inventory = validate_emission_inventory(
        ledger_result, active_slots)
    bound_assertion = {
        "expected_comb_ids": active_order,
        "checked_comb_ids": active_order,
        "emitted_comb_ids": sorted(active_slots),
        "unexpected_emitted_comb_ids": [],
        "offenders": {},
    }
    assert bind_audit_assertion(
        bound_assertion, ledger_result, active_slots,
        active_inventory)["binding_valid"]
    permuted_assertion = clone(bound_assertion)
    permuted_assertion["expected_comb_ids"] = list(reversed(active_order))
    permuted_assertion["checked_comb_ids"] = list(reversed(active_order))
    assert bind_audit_assertion(
        permuted_assertion, ledger_result, active_slots,
        active_inventory)["binding_valid"]
    duplicate_assertion = clone(bound_assertion)
    duplicate_assertion["expected_comb_ids"].append(active_order[0])
    duplicate_assertion["checked_comb_ids"].append(active_order[0])
    assert not bind_audit_assertion(
        duplicate_assertion, ledger_result, active_slots,
        active_inventory)["binding_valid"]
    missing_assertion = clone(bound_assertion)
    missing_assertion["expected_comb_ids"] = active_order[:-1]
    missing_assertion["checked_comb_ids"] = active_order[:-1]
    assert not bind_audit_assertion(
        missing_assertion, ledger_result, active_slots,
        active_inventory)["binding_valid"]
    extra_assertion = clone(bound_assertion)
    extra_assertion["expected_comb_ids"].append("p1c999")
    extra_assertion["checked_comb_ids"].append("p1c999")
    assert not bind_audit_assertion(
        extra_assertion, ledger_result, active_slots,
        active_inventory)["binding_valid"]
    checked_mismatch = clone(bound_assertion)
    checked_mismatch["checked_comb_ids"] = list(reversed(active_order))
    assert not bind_audit_assertion(
        checked_mismatch, ledger_result, active_slots,
        active_inventory)["binding_valid"]

    noncomb_offenders = [
        noncomb_binding_offender(
            "p1c998", "emitted-cell-binding-invalid"),
        noncomb_binding_offender(
            "p1c999", "unowned-live-comb-markup"),
    ]
    noncomb_assertion = comb_assertion(
        noncomb_offenders, expected_ids=active_order)
    noncomb_assertion["raw_live_comb_issues"] = 1
    noncomb_assertion["emitted_cell_binding_issues"] = 1
    noncomb_assertion["inventory_complete"] = False
    noncomb_audit = audit_evidence({
        "comb_slots_match_printed": False,
        "assertions": {"comb_slots_match_printed": noncomb_assertion},
    })
    assert noncomb_audit["assertion_valid"], noncomb_audit["errors"]
    assert bind_audit_assertion(
        noncomb_audit, ledger_result, active_slots,
        active_inventory)["binding_valid"]

    mixed_binding = source_unevaluable_offender("p1c996")
    mixed_binding["failure_kinds"].append(
        "emitted-cell-binding-invalid")
    mixed_unowned = source_unevaluable_offender("p1c997")
    mixed_unowned["failure_kinds"].append(
        "unowned-live-comb-markup")
    mixed_assertion = comb_assertion(
        [mixed_binding, mixed_unowned], expected_ids=active_order)
    mixed_assertion["raw_live_comb_issues"] = 1
    mixed_assertion["emitted_cell_binding_issues"] = 1
    mixed_assertion["inventory_complete"] = False
    mixed_audit = audit_evidence({
        "comb_slots_match_printed": False,
        "assertions": {"comb_slots_match_printed": mixed_assertion},
    })
    assert mixed_audit["assertion_valid"], mixed_audit["errors"]
    mixed_binding_result = bind_audit_assertion(
        mixed_audit, ledger_result, active_slots, active_inventory)
    assert not mixed_binding_result["binding_valid"]
    assert all(
        cell_id in mixed_binding_result["reason"]
        for cell_id in ("p1c996", "p1c997"))

    unknown_assertion = comb_assertion(
        [source_unevaluable_offender("p1c995")],
        expected_ids=active_order)
    unknown_audit = audit_evidence({
        "comb_slots_match_printed": False,
        "assertions": {"comb_slots_match_printed": unknown_assertion},
    })
    assert not unknown_audit["assertion_valid"]
    assert not bind_audit_assertion(
        unknown_audit, ledger_result, active_slots,
        active_inventory)["binding_valid"]

    # As with the base runtime, the Playwright fixture is the referee's own
    # rehash of the installed package: the happy path must be the truth, or
    # every mutation below it would pass for the wrong reason.
    roundtrip_closure = independent_roundtrip_closure()
    assert roundtrip_closure.error is None, roundtrip_closure.error
    roundtrip_chromium_file, roundtrip_chromium = next(
        (logical, value)
        for logical, value in sorted(roundtrip_closure.files.items())
        if value[0] > 0
    )
    roundtrip_fixture = {
        "status": "ok",
        "measured": True,
        "hard_failure": None,
        "error": None,
        "roundtrip_runtime": {
            "mode": "playwright-exact-executable",
            "playwright_package_version": "self-test",
            "dependency_closure": dict(roundtrip_closure.manifests[0]),
            "chromium": {
                "file": roundtrip_chromium_file,
                "bytes": roundtrip_chromium[0],
                "sha256": roundtrip_chromium[1],
                "version_output": "Chrome self-test",
            },
            "same_resolution_session_used_for_render": True,
            "dependency_closure_validated_before_after": True,
            "system_shared_libraries_bound": False,
            "native_host_environment_bound": False,
            "scope": AUDIT_ROUNDTRIP_SCOPE,
            "scope_complete": False,
            "incomplete_reason": "self-test native scope is incomplete",
            "live_browser_version": "self-test",
            "explicit_executable_path_used": True,
            "launch_args": list(AUDIT_ROUNDTRIP_LAUNCH_ARGS),
            "service_workers": "block",
            "browser_context_offline": True,
            "websocket_policy": "record-and-leave-unconnected",
            "request_policy": "formgen-snapshot-only-v1",
            "playwright_operation_timeout_ms": 120000,
            "hard_deadline_seconds": 60.0,
            "hard_deadline_enforced_by": (
                "isolated-render-worker-process-v1"),
            "deadline_cleanup_policy": (
                "kill-worker-and-chromium-process-group"),
        },
        "render_requests": {
            "policy": "formgen-snapshot-only-v1",
            "synthetic_origin": "https://formgen.invalid",
            "fulfilled": ["asset.png", "x.html"],
            "fulfilled_requests": 2,
            "blocked": [],
            "blocked_requests": 0,
            "blocked_websockets": [],
            "all_requests_from_retained_closure": True,
        },
        "candidate_pdf": {
            "bytes": 1,
            "sha256": "3" * 64,
            "retained_exact_bytes": True,
            "chromium_returned_in_memory": True,
            "normalization": {
                "algorithm": "fixed-width-creation-modification-date-v1",
                "fields_normalized": 2,
                "replacement": AUDIT_PDF_NORMALIZATION_REPLACEMENT,
                "xref_offsets_preserved": True,
            },
            "materialization": AUDIT_CANDIDATE_MATERIALIZATION,
            "expected_sha256_passed_to_extractor": True,
            "validated_before_after_extraction": True,
            "candidate_ir_sha256": "4" * 64,
            "candidate_ir_digest_scope": "source-and-generator-removed",
        },
    }
    roundtrip_scope, roundtrip_errors, roundtrip_attested = (
        validate_audit_roundtrip(roundtrip_fixture, "x.html", ["asset.png"]))
    assert roundtrip_scope is False and not roundtrip_errors, roundtrip_errors
    assert roundtrip_attested is True

    tampered_playwright_tree = clone(roundtrip_fixture)
    tampered_playwright_tree["roundtrip_runtime"]["dependency_closure"][
        "tree_sha256"] = "9" * 64
    tampered_tree_result = validate_audit_roundtrip(
        tampered_playwright_tree, "x.html", ["asset.png"])
    assert tampered_tree_result[1] and tampered_tree_result[2] is False

    tampered_chromium = clone(roundtrip_fixture)
    tampered_chromium["roundtrip_runtime"]["chromium"]["sha256"] = "8" * 64
    tampered_chromium_result = validate_audit_roundtrip(
        tampered_chromium, "x.html", ["asset.png"])
    assert tampered_chromium_result[1] and tampered_chromium_result[2] is False

    invented_chromium = clone(roundtrip_fixture)
    invented_chromium["roundtrip_runtime"]["chromium"]["file"] = (
        "playwright/not-in-the-installed-tree")
    invented_chromium_result = validate_audit_roundtrip(
        invented_chromium, "x.html", ["asset.png"])
    assert invented_chromium_result[1] and invented_chromium_result[2] is False

    blocked_http_roundtrip = clone(roundtrip_fixture)
    blocked_http_roundtrip["render_requests"].update({
        "blocked": [{
            "url": "https://outside.invalid/data",
            "reason": "absent from retained closure",
        }],
        "blocked_requests": 1,
        # The producer aggregate is deliberately left forged true.
        "all_requests_from_retained_closure": True,
    })
    assert validate_audit_roundtrip(
        blocked_http_roundtrip, "x.html", ["asset.png"])[1]

    bad_request_count_roundtrip = clone(roundtrip_fixture)
    bad_request_count_roundtrip[
        "render_requests"]["fulfilled_requests"] = 3
    assert validate_audit_roundtrip(
        bad_request_count_roundtrip, "x.html", ["asset.png"])[1]

    bad_blocked_count_roundtrip = clone(roundtrip_fixture)
    bad_blocked_count_roundtrip[
        "render_requests"]["blocked_requests"] = 1
    assert validate_audit_roundtrip(
        bad_blocked_count_roundtrip, "x.html", ["asset.png"])[1]

    blocked_websocket_roundtrip = clone(roundtrip_fixture)
    blocked_websocket_roundtrip["render_requests"].update({
        "blocked_websockets": ["wss://outside.invalid/socket"],
        "all_requests_from_retained_closure": True,
    })
    assert validate_audit_roundtrip(
        blocked_websocket_roundtrip, "x.html", ["asset.png"])[1]

    unknown_request_roundtrip = clone(roundtrip_fixture)
    unknown_request_roundtrip["render_requests"].update({
        "fulfilled": ["asset.png", "unknown.png", "x.html"],
        "fulfilled_requests": 3,
        "all_requests_from_retained_closure": True,
    })
    assert validate_audit_roundtrip(
        unknown_request_roundtrip, "x.html", ["asset.png"])[1]

    boolean_count_roundtrip = clone(roundtrip_fixture)
    boolean_count_roundtrip[
        "render_requests"]["fulfilled_requests"] = True
    assert validate_audit_roundtrip(
        boolean_count_roundtrip, "x.html", ["asset.png"])[1]

    malformed_request_list_roundtrip = clone(roundtrip_fixture)
    malformed_request_list_roundtrip[
        "render_requests"]["fulfilled"] = ["asset.png", 7, "x.html"]
    assert validate_audit_roundtrip(
        malformed_request_list_roundtrip, "x.html", ["asset.png"])[1]

    reordered_launch_args_roundtrip = clone(roundtrip_fixture)
    reordered_launch_args_roundtrip[
        "roundtrip_runtime"]["launch_args"].reverse()
    assert validate_audit_roundtrip(
        reordered_launch_args_roundtrip, "x.html", ["asset.png"])[1]

    wrong_scope_roundtrip = clone(roundtrip_fixture)
    wrong_scope_roundtrip[
        "roundtrip_runtime"]["scope"] = "playwright-only"
    assert validate_audit_roundtrip(
        wrong_scope_roundtrip, "x.html", ["asset.png"])[1]

    wrong_materialization_roundtrip = clone(roundtrip_fixture)
    wrong_materialization_roundtrip[
        "candidate_pdf"]["materialization"] = "ordinary-temp-file"
    assert validate_audit_roundtrip(
        wrong_materialization_roundtrip, "x.html", ["asset.png"])[1]

    wrong_normalization_roundtrip = clone(roundtrip_fixture)
    wrong_normalization_roundtrip["candidate_pdf"][
        "normalization"]["replacement"] = "D:20000101000000+00'00'"
    assert validate_audit_roundtrip(
        wrong_normalization_roundtrip, "x.html", ["asset.png"])[1]

    with tempfile.TemporaryDirectory(prefix="comb-referee-audit-bind-") as temp:
        root = pathlib.Path(temp)
        html_dir = root / "html"
        source_root = root / "source"
        html_dir.mkdir()
        source_root.mkdir()
        payloads = {
            "ir": b'{"self_test":"ir"}',
            "layout": b'{"self_test":"layout"}',
            "html": b"<!doctype html><html></html>",
            "guide": b'{"self_test":"guide"}',
            "guide_html": None,
        }
        paths = {
            "ir": root / "x.ir.json",
            "layout": root / "x.layout.json",
            "html": html_dir / "x.html",
            "guide": root / "x.guide.json",
            "guide_html": html_dir / "x.guide.html",
        }
        for role, payload in payloads.items():
            if payload is not None:
                paths[role].write_bytes(payload)
        source_payload = b"%PDF-self-test"
        source_path = source_root / "test.pdf"
        source_path.write_bytes(source_payload)
        expected = {
            role: (paths[role], role != "guide_html", payload)
            for role, payload in payloads.items()
        }
        audit_producer_bytes = (HERE / "audit.py").read_bytes()
        self_test_audit_sha = sha256_bytes(audit_producer_bytes)
        dependency_sources = {
            logical: (REPO / logical).read_bytes()
            for logical in AUDIT_DEPENDENCY_SHA256
        }
        producer_sources = {
            AUDIT_PRODUCER_FILE: audit_producer_bytes,
            **dependency_sources,
        }
        # The runtime fixture is the referee's OWN independent derivation, not
        # a hand-written stand-in: a fixture the referee did not derive could
        # only ever prove that it accepts something, never that it accepts the
        # truth and rejects everything else. Each mutation below moves exactly
        # one member of it.
        application_closure = independent_application_closure()
        assert application_closure.error is None, application_closure.error
        closure_modules = [
            {
                "module": name,
                "file": f"{name}/__init__.py",
                "bytes": application_closure.files[f"{name}/__init__.py"][0],
                "sha256": application_closure.files[f"{name}/__init__.py"][1],
            }
            for name in APPLICATION_PACKAGE_NAMES
        ]
        runtime_members = [
            (f"module/{item['module']}", item["bytes"], item["sha256"])
            for item in closure_modules
        ]
        runtime_members.append(
            ("python/executable", *application_closure.executable))
        if application_closure.runtime_library is not None:
            runtime_members.append(
                ("python/runtime-library",
                 *application_closure.runtime_library))
        runtime_members.sort(key=lambda item: item[0])
        runtime_canonical = json.dumps(
            runtime_members, separators=(",", ":"))
        published_application_closure = {
            "scope": APPLICATION_CLOSURE_SCOPE,
            "algorithm": TREE_CLOSURE_ALGORITHM,
            "bytecode_caches_excluded": True,
            "exclusion_reason": "self-test mirrors the published exclusion",
            "packages": [
                dict(item) for item in application_closure.manifests],
            "modules": sorted(
                closure_modules, key=lambda item: item["file"]),
            "native_libraries": [
                dict(item) for item in application_closure.native_libraries],
            "unbound_modules": [],
            "validated_before_after": True,
            "complete": True,
        }
        runtime = {
            "python": {
                "implementation": platform.python_implementation(),
                "version": platform.python_version(),
                "cache_tag": sys.implementation.cache_tag,
            },
            "pymupdf": {
                "package_version": "self-test",
                "version_bind": "self-test",
            },
            "loaded_application_files": {
                "algorithm": (
                    "sha256(canonical-json(logical-file,bytes,sha256))"),
                "files": len(runtime_members),
                "bytes": sum(item[1] for item in runtime_members),
                "tree_sha256": sha256_bytes(
                    runtime_canonical.encode("ascii")),
                "members": [
                    {"file": item[0], "bytes": item[1], "sha256": item[2]}
                    for item in runtime_members
                ],
                "validated_before_after": True,
            },
            "application_closure": published_application_closure,
            "stdlib_and_system_shared_libraries_bound": False,
            "scope_complete": False,
            "incomplete_reason": "self-test intentionally incomplete scope",
        }
        producer = {
            "file": AUDIT_PRODUCER_FILE,
            "bytes": len(audit_producer_bytes),
            "sha256": self_test_audit_sha,
            "dependencies": [
                {
                    "file": logical,
                    "bytes": len(dependency_sources[logical]),
                    "sha256": expected_sha,
                    "loaded_origin": logical,
                    "executed_from_snapshotted_source": True,
                }
                for logical, expected_sha
                in AUDIT_DEPENDENCY_SHA256.items()
            ],
            "dependency_execution_bound": True,
            "audit_execution_bound": False,
            "assertion_producer_bound": False,
            "roundtrip_runtime_bound_in_record": False,
            "standalone_attestation_complete": False,
            "incomplete_reason": "self-test bootstrap is intentionally open",
        }
        input_entries = {
            role: {
                "file": paths[role].name,
                "required": role != "guide_html",
                "present": payload is not None,
                "bytes": len(payload) if payload is not None else None,
                "sha256": (
                    sha256_bytes(payload) if payload is not None else None),
            }
            for role, payload in payloads.items()
        }
        input_entries["source_pdf"] = {
            "file": "test.pdf",
            "logical_identity": "external:test.pdf",
            "path": "test.pdf",
            "required": True,
            "present": True,
            "bytes": len(source_payload),
            "sha256": sha256_bytes(source_payload),
            "expected_sha256": sha256_bytes(source_payload),
        }
        audit_record = {
            "roundtrip": "skipped",
            "provenance_validation": {
                "validated_before": True,
                "validated_after": True,
                "error": None,
            },
            "attestation": {
                "inputs_complete": True,
                "producer_execution_bound": False,
                "base_runtime_scope_complete": False,
                "roundtrip_runtime_scope_complete": None,
                "application_closure_complete": True,
                "validated_before_after": True,
                "complete": True,
                "enforceable": True,
                "incomplete_reasons": [],
                "declared_out_of_scope": [
                    "self-test producer/runtime scope is intentionally open"],
                "future_gate_required": "self-test trusted gate",
            },
            "input_manifest": {
                "schema": "formgen-audit-input-manifest-v1",
                "algorithm": "sha256",
                "producer": producer,
                "runtime": runtime,
                "inputs_complete": True,
                "attestation_complete": True,
                "enforceable": True,
                "complete": True,
                "missing_required": [],
                "inputs": input_entries,
                "render": {
                    "entrypoint": "x.html",
                    "dependencies": [],
                    "errors": [],
                    "complete": True,
                    "network_policy": (
                        "deny-except-retained-relative-resources-and-inline-data"),
                },
            },
        }
        assert self_test_audit_sha == AUDIT_PRODUCER_SHA256
        binding = bind_audit_manifest(
            audit_record,
            expected,
            source_path=source_path,
            source_identity="external:test.pdf",
            source_root=source_root,
            source_payload=source_payload,
            expected_source_sha256=sha256_bytes(source_payload),
            html_dir=html_dir,
            producer_sources=producer_sources,
        )
        assert binding["binding_valid"] and not binding["complete"], binding
        assert binding["blockers"]
        stale_expected = {
            **expected,
            "ir": (paths["ir"], True, b"changed"),
        }
        assert not bind_audit_manifest(
            audit_record,
            stale_expected,
            source_path=source_path,
            source_identity="external:test.pdf",
            source_root=source_root,
            source_payload=source_payload,
            expected_source_sha256=sha256_bytes(source_payload),
            html_dir=html_dir,
            producer_sources=producer_sources,
        )["binding_valid"]
        stale_manifest = clone(audit_record)
        stale_manifest["input_manifest"]["producer"]["sha256"] = "0" * 64
        assert not bind_audit_manifest(
            stale_manifest,
            expected,
            source_path=source_path,
            source_identity="external:test.pdf",
            source_root=source_root,
            source_payload=source_payload,
            expected_source_sha256=sha256_bytes(source_payload),
            html_dir=html_dir,
            producer_sources=producer_sources,
        )["binding_valid"]
        stale_render = clone(audit_record)
        stale_render["input_manifest"]["render"]["dependencies"] = [{
            "path": "invented-self-test.bin",
            "mime_type": "application/octet-stream",
            "present": True,
            "bytes": 1,
            "sha256": "0" * 64,
            "kinds": ["img"],
            "referrers": ["x.html"],
        }]
        assert not bind_audit_manifest(
            stale_render,
            expected,
            source_path=source_path,
            source_identity="external:test.pdf",
            source_root=source_root,
            source_payload=source_payload,
            expected_source_sha256=sha256_bytes(source_payload),
            html_dir=html_dir,
            producer_sources=producer_sources,
        )["binding_valid"]
        def rebind(record: dict[str, Any]) -> dict[str, Any]:
            return bind_audit_manifest(
                record,
                expected,
                source_path=source_path,
                source_identity="external:test.pdf",
                source_root=source_root,
                source_payload=source_payload,
                expected_source_sha256=sha256_bytes(source_payload),
                html_dir=html_dir,
                producer_sources=producer_sources,
            )

        # The independent rehash has to be able to reject every part of what
        # it now attests, or attesting it would be theatre. One mutation per
        # member of the closure, and the claim must collapse with it.
        assert binding["base_runtime_closure_independently_attested"] is True
        assert binding["roundtrip_runtime_closure_independently_attested"] \
            is False
        assert binding["host_scope_boundaries"]

        tampered_tree = clone(audit_record)
        tampered_tree["input_manifest"]["runtime"]["application_closure"][
            "packages"][0]["tree_sha256"] = "b" * 64
        tampered_tree_binding = rebind(tampered_tree)
        assert not tampered_tree_binding["binding_valid"]
        assert not tampered_tree_binding[
            "base_runtime_closure_independently_attested"]

        tampered_native = clone(audit_record)
        assert tampered_native["input_manifest"]["runtime"][
            "application_closure"]["native_libraries"]
        tampered_native["input_manifest"]["runtime"]["application_closure"][
            "native_libraries"][0]["sha256"] = "c" * 64
        assert not rebind(tampered_native)["binding_valid"]

        dropped_native = clone(audit_record)
        dropped_native["input_manifest"]["runtime"]["application_closure"][
            "native_libraries"].pop()
        assert not rebind(dropped_native)["binding_valid"]

        tampered_module = clone(audit_record)
        tampered_module["input_manifest"]["runtime"]["application_closure"][
            "modules"][0]["sha256"] = "d" * 64
        assert not rebind(tampered_module)["binding_valid"]

        invented_module = clone(audit_record)
        invented_module["input_manifest"]["runtime"]["application_closure"][
            "modules"][0]["file"] = "pymupdf/not-in-the-installed-tree.py"
        assert not rebind(invented_module)["binding_valid"]

        unaccounted_module = clone(audit_record)
        unaccounted_module["input_manifest"]["runtime"][
            "application_closure"]["modules"].pop()
        assert not rebind(unaccounted_module)["binding_valid"]

        false_completeness = clone(audit_record)
        false_completeness["input_manifest"]["runtime"][
            "application_closure"]["unbound_modules"] = ["module/elsewhere"]
        assert not rebind(false_completeness)["binding_valid"]

        stale_library = clone(audit_record)
        stale_library["input_manifest"]["runtime"][
            "loaded_application_files"]["members"].append({
                "file": "python/runtime-library"
                        if application_closure.runtime_library is None
                        else "python/unexpected-member",
                "bytes": 1,
                "sha256": "e" * 64,
            })
        assert not rebind(stale_library)["binding_valid"]

        # Claiming the attestation the referee did not verify, and withholding
        # the one it did: both are disagreements with the independent answer.
        overclaimed = clone(audit_record)
        overclaimed["input_manifest"]["runtime"]["application_closure"][
            "packages"][0]["files"] += 1
        overclaimed_binding = rebind(overclaimed)
        assert not overclaimed_binding["binding_valid"]
        assert any("overclaims producer attestation" in error
                   for error in overclaimed_binding["errors"]), (
            overclaimed_binding["errors"])

        underclaimed = clone(audit_record)
        underclaimed["input_manifest"]["complete"] = False
        underclaimed_binding = rebind(underclaimed)
        assert not underclaimed_binding["binding_valid"]
        assert any("disagrees with the referee's independent verification"
                   in error for error in underclaimed_binding["errors"]), (
            underclaimed_binding["errors"])

    assert audit_relation_for_subject(
        ledger_result["subjects"][0], True, None
    ) == (2, "complete-non-offender")
    assert audit_relation_for_subject(
        unresolved_result["subjects"][0], True, None
    ) == (2, "complete-non-offender")
    assert audit_relation_for_subject(
        retained_result["subjects"][0], True, None
    ) == (None, "complete-blocked-subject")

    unresolved_compared = {
        "ledger_state": "active_unresolved",
        "ledger_blocks_gate": True,
        "latticed": 3,
        "emitted": 3,
        "emitted_indexes_valid": True,
        "audit_printed": 3,
        "referee": {
            "status": "measured",
            "compartments": 3,
            "positions_match": True,
        },
    }
    comparison_cases = [
        ("agree", True, {}, {}),
        ("repair-lattice", True, {"audit_printed": 4},
         {"compartments": 4}),
        ("repair-audit", True, {"audit_printed": 4},
         {"compartments": 3}),
        ("stop", True, {"audit_printed": 3}, {"compartments": 5}),
        ("stale-generation", True, {"emitted": 2}, {}),
        ("unevaluable", False, {}, {}),
        ("unevaluable", True, {"audit_printed": None}, {}),
    ]
    for expected_status, audit_complete, updates, referee_updates in (
            comparison_cases):
        compared = clone(unresolved_compared)
        compared.update(updates)
        compared["referee"].update(referee_updates)
        before = clone(compared)
        status, _reason = comparison(compared, audit_complete)
        assert status == expected_status, (expected_status, status)
        transition_status, _transition_reason = transition_decision(
            compared, status)
        assert transition_status == (
            "eligible-for-reviewed-resolution"
            if status == "agree" else "blocked"
        )
        assert compared == before
        assert compared["ledger_state"] == "active_unresolved"
        assert compared["ledger_blocks_gate"] is True

    resolved_compared = clone(unresolved_compared)
    resolved_compared.update({
        "ledger_state": "active_resolved",
        "ledger_blocks_gate": False,
    })
    resolved_status, _ = comparison(resolved_compared, True)
    assert resolved_status == "agree"
    assert transition_decision(
        resolved_compared, resolved_status)[0] == "none"

    retained_compared = clone(unresolved_compared)
    retained_compared["ledger_state"] = "retained_unresolved"
    retained_status, _ = comparison(retained_compared, True)
    assert retained_status == "unevaluable"
    assert transition_decision(
        retained_compared, retained_status)[0] == (
            "explicit-transition-required")

    artifact = {
        "schema_version": 1,
        "form": {"code": "X", "revision": "1"},
        "source": {"file": "external:x.pdf", "sha256": "abc",
                   "page_count": 1},
        "paper": {
            "uniform": True, "width_pt": 100.0, "height_pt": 100.0,
            "distinct_sizes": ["100.0x100.0"],
        },
        "pages": [{"index": 1, "width_pt": 100.0, "height_pt": 100.0}],
    }
    mixed_artifact = {
        **artifact,
        "paper": {
            "uniform": False, "width_pt": 100.0, "height_pt": 200.0,
            "distinct_sizes": ["100.0x200.0", "200.0x100.0"],
        },
        "source": {**artifact["source"], "page_count": 2},
        "pages": [
            {"index": 1, "width_pt": 100.0, "height_pt": 200.0},
            {"index": 2, "width_pt": 200.0, "height_pt": 100.0},
        ],
    }
    ir = {**artifact, "schema_version": 2}
    guide = {"form": artifact["form"], "inline": [], "stats": {"pages": 1}}
    parser.root = {
        "data-form": "X", "data-revision": "1",
        "data-source-sha256": "abc", "data-schema-version": "1",
    }
    bind_artifacts("x-1", artifact, ir, guide, parser)
    # A genuinely mixed-orientation source (1604-CF) is EVALUABLE, and every
    # falsifiable part of its paper contract is still bound.
    mixed_guide = {**guide, "stats": {"pages": 2}}
    mixed_parser = SlotParser()
    mixed_parser.root = dict(parser.root)
    mixed_parser.pages = [1, 2]
    mixed_parser.page_geometry = [
        (1, 100.0, 200.0), (2, 200.0, 100.0)]
    bind_artifacts(
        "x-2", mixed_artifact, {**mixed_artifact, "schema_version": 2},
        mixed_guide, mixed_parser)
    for label, broken_paper in (
        ("false-uniform", {
            "uniform": True, "width_pt": 100.0, "height_pt": 200.0,
            "distinct_sizes": ["100.0x200.0", "200.0x100.0"]}),
        ("false-uniform-single", {
            "uniform": False, "width_pt": 100.0, "height_pt": 100.0,
            "distinct_sizes": ["100.0x100.0"]}),
        ("short-inventory", {
            "uniform": False, "width_pt": 100.0, "height_pt": 200.0,
            "distinct_sizes": ["100.0x200.0"]}),
        ("unsorted-inventory", {
            "uniform": False, "width_pt": 100.0, "height_pt": 200.0,
            "distinct_sizes": ["200.0x100.0", "100.0x200.0"]}),
        ("undeclared-size", {
            "uniform": False, "width_pt": 100.0, "height_pt": 200.0,
            "distinct_sizes": ["100.0x200.0", "300.0x400.0"]}),
        ("wrong-first-page", {
            "uniform": False, "width_pt": 200.0, "height_pt": 100.0,
            "distinct_sizes": ["100.0x200.0", "200.0x100.0"]}),
        ("missing-inventory", {
            "uniform": True, "width_pt": 100.0, "height_pt": 100.0}),
    ):
        base = (
            artifact if label == "false-uniform-single" else mixed_artifact)
        broken = {**base, "paper": broken_paper}
        try:
            bind_artifacts(
                "x-3", broken, {**broken, "schema_version": 2},
                mixed_guide if base is mixed_artifact else guide,
                mixed_parser if base is mixed_artifact else parser)
        except RefereeError:
            pass
        else:
            raise AssertionError(f"false paper contract accepted: {label}")
    bad_ir = {**ir, "source": {**ir["source"], "sha256": "changed"}}
    try:
        bind_artifacts("x-1", artifact, bad_ir, guide, parser)
    except RefereeError:
        pass
    else:
        raise AssertionError("mismatched IR provenance was accepted")

    first = canonical_digest({"b": 2, "a": [1, 2]})
    second = canonical_digest({"a": [1, 2], "b": 2})
    assert first == second
    digest_report = {"schema_version": REPORT_VERSION, "forms": [], "status": "ok"}
    attach_report_digest(digest_report)
    assert report_digest_valid(digest_report)
    assert report_bytes(digest_report) == report_bytes(clone(digest_report))
    changed_digest_report = clone(digest_report)
    changed_digest_report["status"] = "unevaluable"
    assert not report_digest_valid(changed_digest_report)
    selected = select_layouts(
        [pathlib.Path("0605-1999.layout.json"),
         pathlib.Path("1701-2018.layout.json")],
        ["0605"],
    )
    assert [path.name for path in selected] == ["0605-1999.layout.json"]
    try:
        select_layouts(selected, ["0605", "0605-1999"])
    except RefereeError:
        pass
    else:
        raise AssertionError("overlapping --only selectors were accepted")
    assert corpus_coverage_ok(
        {"0605-1999"}, [{}], EXPECTED_COMBS_BY_SLUG["0605-1999"], [])
    assert not corpus_coverage_ok(
        {"0605-1999"}, [{}], EXPECTED_COMBS_BY_SLUG["0605-1999"] - 1, [])
    standalone_attestation = referee_attestation()
    assert standalone_attestation["scope_complete"] is False
    assert standalone_attestation["complete"] is False
    assert standalone_attestation["enforceable"] is False
    assert standalone_attestation["incomplete_reasons"]
    assert standalone_attestation[
        "poppler_invocations_have_hard_deadlines"] is True

    print("comb_referee self-test: pass")
    return 0


def select_layouts(layouts: Sequence[pathlib.Path],
                   selectors: Sequence[str]) -> list[pathlib.Path]:
    """Resolve --only selectors without silently dropping or double-matching."""
    if not selectors:
        return sorted(layouts)
    normalized = [value.lower() for value in selectors]
    if len(normalized) != len(set(normalized)):
        raise RefereeError("--only contains a duplicate selector")
    selected: dict[str, pathlib.Path] = {}
    claimed_by: dict[str, str] = {}
    for selector in normalized:
        matches = [
            path for path in layouts
            if (path.name.removesuffix(".layout.json").lower() == selector
                or path.name.split("-", 1)[0].lower() == selector)
        ]
        if not matches:
            raise RefereeError(f"--only selector matched no layout: {selector}")
        for path in matches:
            slug = path.name.removesuffix(".layout.json")
            if slug in selected:
                raise RefereeError(
                    "--only selectors overlap for "
                    f"{slug}: {claimed_by[slug]}, {selector}")
            selected[slug] = path
            claimed_by[slug] = selector
    return [selected[slug] for slug in sorted(selected)]


def corpus_coverage_ok(selected_slugs: set[str],
                       forms: Sequence[dict[str, Any]],
                       combs: int,
                       errors: Sequence[dict[str, str]]) -> bool:
    if errors or selected_slugs - set(EXPECTED_COMBS_BY_SLUG):
        return False
    expected = sum(EXPECTED_COMBS_BY_SLUG[slug] for slug in selected_slugs)
    return len(forms) == len(selected_slugs) and combs == expected


def referee_attestation() -> dict[str, Any]:
    """State the exact boundary this standalone process does not attest."""
    return {
        "schema": "comb-referee-runtime-attestation-v1",
        "producer_and_declared_dependency_bytes_bound": True,
        "published_form_input_bytes_bound_before_after": True,
        "python_executable_fingerprinted": True,
        "python_executable_validated_before_after": False,
        "poppler_executable_bound_before_after": True,
        "poppler_invocations_have_hard_deadlines": True,
        "poppler_timeout_cleanup_policy": SUBPROCESS_CLEANUP_POLICY,
        "clean_source_revision_bound": False,
        "python_stdlib_closure_bound": False,
        "python_dynamic_libraries_bound": False,
        "poppler_dynamic_libraries_bound": False,
        "operating_system_and_host_services_bound": False,
        "scope_complete": False,
        "complete": False,
        "enforceable": False,
        "incomplete_reasons": [
            (
                "the standalone referee hashes its source and declared local "
                "dependencies but is not bound to a reviewed clean source "
                "revision"
            ),
            (
                "the Python standard library, Python dynamic libraries, "
                "Poppler dynamic libraries, and operating-system services "
                "are outside the independently rehashed application closure"
            ),
            (
                "the Python executable is fingerprinted for reporting but "
                "is not independently snapshotted and revalidated before "
                "and after the run"
            ),
        ],
        "future_gate_required": (
            "trusted clean-source and host/runtime closure binding"),
    }


def main(argv: Sequence[str] | None = None) -> int:
    # R2b: the reviewed-ledger registries are consulted by the producer and
    # validated here; a malformed registry refuses EVERY run before any form
    # is read.  Empty registries are valid and change nothing.
    registry_defects = review_registry.registry_errors()
    if registry_defects:
        raise RefereeError(
            "reviewed-ledger registry is malformed: "
            + "; ".join(registry_defects[:3]))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=pathlib.Path,
                        default=pathlib.Path.home() / "Downloads/forms")
    parser.add_argument("--layout-dir", type=pathlib.Path,
                        default=REPO / "build/layout")
    parser.add_argument("--ir-dir", type=pathlib.Path, default=REPO / "build/ir")
    parser.add_argument("--html-dir", type=pathlib.Path, default=REPO / "build/html")
    parser.add_argument("--guide-dir", type=pathlib.Path,
                        default=REPO / "build/guides")
    parser.add_argument("--audit", type=pathlib.Path,
                        default=REPO / "build/audit.json")
    parser.add_argument("--out", type=pathlib.Path,
                        default=REPO / "build/comb-referee.json")
    parser.add_argument("--only", action="append", default=None,
                        help="Restrict to a code or slug (repeatable).")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.self_test:
        return self_test()
    try:
        producer_bytes = pathlib.Path(__file__).resolve().read_bytes()
        lattice_producer_bytes = (HERE / "lattice.py").read_bytes()
        if sha256_bytes(lattice_producer_bytes) != LATTICE_PRODUCER_SHA256:
            raise RefereeError(
                "lattice producer changed from committed SHA "
                + LATTICE_PRODUCER_SHA256)
        audit_producer_bytes = (HERE / "audit.py").read_bytes()
        if sha256_bytes(audit_producer_bytes) != AUDIT_PRODUCER_SHA256:
            raise RefereeError(
                "audit producer changed from committed SHA "
                + AUDIT_PRODUCER_SHA256)
        audit_dependency_bytes = {
            logical: (REPO / logical).read_bytes()
            for logical in AUDIT_DEPENDENCY_SHA256
        }
        for logical, expected_sha in AUDIT_DEPENDENCY_SHA256.items():
            if sha256_bytes(audit_dependency_bytes[logical]) != expected_sha:
                raise RefereeError(
                    f"audit dependency changed from committed SHA: {logical}")
        audit_bytes = args.audit.read_bytes()
        args.lattice_producer_bytes = lattice_producer_bytes
        args.audit_producer_bytes = audit_producer_bytes
        args.audit_dependency_bytes = audit_dependency_bytes
        poppler = poppler_identity()
        audit_data = json.loads(audit_bytes)
        if not isinstance(audit_data, list):
            raise RefereeError("audit report is not a list")
        audit_by_slug = {record["slug"]: record for record in audit_data}
        if len(audit_by_slug) != len(audit_data):
            raise RefereeError("audit report contains duplicate form slugs")
        wanted = [value.lower() for value in args.only or ()]
        layouts = select_layouts(
            sorted(args.layout_dir.glob("*.layout.json")), wanted)
        if not layouts:
            raise RefereeError("no matching layout files")
        selected_slugs = {
            path.name.removesuffix(".layout.json") for path in layouts
        }
        if not wanted and selected_slugs != set(EXPECTED_COMBS_BY_SLUG):
            missing = sorted(set(EXPECTED_COMBS_BY_SLUG) - selected_slugs)
            extra = sorted(selected_slugs - set(EXPECTED_COMBS_BY_SLUG))
            raise RefereeError(
                "layout corpus identity disagrees"
                + (f"; missing: {', '.join(missing)}" if missing else "")
                + (f"; extra: {', '.join(extra)}" if extra else ""))
        unexpected_selected = sorted(
            selected_slugs - set(EXPECTED_COMBS_BY_SLUG))
        if unexpected_selected:
            raise RefereeError(
                "selected layouts are outside the pinned corpus: "
                + ", ".join(unexpected_selected))
        missing_audit = sorted(selected_slugs - set(audit_by_slug))
        if missing_audit:
            raise RefereeError(
                f"audit report is missing forms: {', '.join(missing_audit)}")
        if not wanted:
            extra_audit = sorted(set(audit_by_slug) - selected_slugs)
            if extra_audit:
                raise RefereeError(
                    f"audit report has unexpected forms: {', '.join(extra_audit)}")

        forms: list[dict[str, Any]] = []
        errors: list[dict[str, str]] = []
        for layout_path in layouts:
            try:
                forms.append(form_report(layout_path, args, audit_by_slug, poppler))
            except Exception as error:  # publish every failed form, fail closed
                errors.append({
                    "slug": layout_path.name.removesuffix(".layout.json"),
                    "error": f"{type(error).__name__}: {error}",
                })

        if args.audit.read_bytes() != audit_bytes:
            errors.append({
                "slug": "<corpus>",
                "error": "RefereeError: audit report changed during referee run",
            })
        if (pathlib.Path(__file__).resolve().read_bytes() != producer_bytes
                or (HERE / "audit.py").read_bytes() != audit_producer_bytes
                or (HERE / "lattice.py").read_bytes()
                != lattice_producer_bytes
                or any(
                    (REPO / logical).read_bytes() != payload
                    for logical, payload in audit_dependency_bytes.items())):
            errors.append({
                "slug": "<corpus>",
                "error": "RefereeError: producer code changed during referee run",
            })
        try:
            poppler_changed = (
                sha256_file(pathlib.Path(poppler["binary_path"]))
                != poppler["binary_sha256"]
            )
        except OSError:
            poppler_changed = True
        if poppler_changed:
            errors.append({
                "slug": "<corpus>",
                "error": "RefereeError: Poppler binary changed during referee run",
            })
        for closure_error in revalidate_independent_closures():
            errors.append({
                "slug": "<corpus>",
                "error": f"RefereeError: {closure_error}",
            })
        for form in forms:
            changed = changed_snapshot_inputs(form, args)
            if changed:
                errors.append({
                    "slug": form["slug"],
                    "error": (
                        "RefereeError: inputs changed during referee run: "
                        + ", ".join(changed)
                    ),
                })

        combs = sum(form["counts"]["combs"] for form in forms)
        measured = sum(form["counts"]["measured"] for form in forms)
        unevaluable = sum(form["counts"]["unevaluable"] for form in forms)
        source_unevaluable = sum(
            form["counts"]["source_unevaluable"] for form in forms)
        active = sum(
            form["counts"]["subjects_active"] for form in forms)
        active_resolved = sum(
            form["counts"]["subjects_active_resolved"] for form in forms)
        active_unresolved = sum(
            form["counts"]["subjects_active_unresolved"] for form in forms)
        retained_unresolved = sum(
            form["counts"]["subjects_retained_unresolved"] for form in forms)
        inferences_suppressed = sum(
            form["counts"]["inferences_suppressed"] for form in forms)
        ledger_blocking = sum(
            form["counts"]["ledger_blocking"] for form in forms)
        ledger_blocking_excused = sum(
            form["counts"].get("ledger_blocking_excused", 0)
            for form in forms)
        mismatches = sum(form["counts"]["referee_layout_mismatches"]
                         for form in forms)
        position_mismatches = sum(
            form["counts"]["referee_layout_position_mismatches"]
            for form in forms
        )
        comparison_totals = {
            name: sum(form["counts"]["comparisons"][name] for form in forms)
            for name in COMPARISON_NAMES
        }
        expected_comb_total = sum(
            EXPECTED_COMBS_BY_SLUG[slug] for slug in selected_slugs)
        coverage_ok = corpus_coverage_ok(
            selected_slugs, forms, combs, errors)
        status_reasons: list[str] = []
        if (not coverage_ok
                or any(form["status"] == "unevaluable" for form in forms)):
            corpus_status = "unevaluable"
            status_reasons.append(
                "corpus coverage or one or more forms are unevaluable")
        elif any(form["status"] == "disagreement" for form in forms):
            corpus_status = "disagreement"
            status_reasons.append(
                "one or more four-way form comparisons disagree")
        else:
            corpus_status = "ok"
        runtime_attestation = referee_attestation()
        if not runtime_attestation["complete"]:
            corpus_status = "unevaluable"
            status_reasons.append(
                "standalone referee runtime/application attestation "
                "is incomplete and non-enforceable")
        python_binary = pathlib.Path(sys.executable).resolve()
        report: dict[str, Any] = {
            "schema_version": REPORT_VERSION,
            "producer": "tools/formgen/comb_referee.py",
            "producer_sha256": sha256_bytes(producer_bytes),
            "python_version": sys.version.split()[0],
            "provenance": {
                "producer": {
                    "file": "tools/formgen/comb_referee.py",
                    "bytes": len(producer_bytes),
                    "sha256": sha256_bytes(producer_bytes),
                },
                "dependencies": {
                    "audit": {
                        "file": AUDIT_PRODUCER_FILE,
                        "bytes": len(audit_producer_bytes),
                        "sha256": sha256_bytes(audit_producer_bytes),
                        "expected_sha256": AUDIT_PRODUCER_SHA256,
                        "dependencies": [
                            {
                                "file": logical,
                                "bytes": len(audit_dependency_bytes[logical]),
                                "sha256": sha256_bytes(
                                    audit_dependency_bytes[logical]),
                                "expected_sha256": expected_sha,
                            }
                            for logical, expected_sha
                            in AUDIT_DEPENDENCY_SHA256.items()
                        ],
                    },
                    "lattice": {
                        "file": LATTICE_PRODUCER_FILE,
                        "bytes": len(lattice_producer_bytes),
                        "sha256": sha256_bytes(lattice_producer_bytes),
                        "expected_sha256": LATTICE_PRODUCER_SHA256,
                    },
                },
                "runtime": {
                    "python_implementation": sys.implementation.name,
                    "python_version": sys.version.split()[0],
                    "python_executable": str(python_binary),
                    "python_executable_sha256": sha256_file(python_binary),
                    "poppler": poppler,
                },
            },
            "status": corpus_status,
            "status_reasons": status_reasons,
            "attestation": runtime_attestation,
            "poppler": poppler,
            "inputs": {
                "audit_sha256": sha256_bytes(audit_bytes),
                "audit_bytes": len(audit_bytes),
                "layout_count": len(layouts),
            },
            "totals": {
                "forms_expected": len(layouts) if args.only else EXPECTED_FORMS,
                "forms_measured": len(forms),
                "forms_error": len(errors),
                "combs_expected": expected_comb_total,
                "combs_found": combs,
                "combs_measured": measured,
                "combs_composite": sum(
                    form["counts"]["composite"] for form in forms),
                "combs_unevaluable": unevaluable,
                "combs_source_unevaluable": source_unevaluable,
                "subjects_active": active,
                "subjects_active_resolved": active_resolved,
                "subjects_active_unresolved": active_unresolved,
                "subjects_retained_unresolved": retained_unresolved,
                "inferences_suppressed": inferences_suppressed,
                "ledger_blocking": ledger_blocking,
                "ledger_blocking_excused": ledger_blocking_excused,
                "referee_layout_mismatches": mismatches,
                "referee_layout_position_mismatches": position_mismatches,
                "comparisons": comparison_totals,
                "forms_ok": sum(form["status"] == "ok" for form in forms),
                "forms_disagreement": sum(
                    form["status"] == "disagreement" for form in forms),
                "forms_unevaluable": sum(
                    form["status"] == "unevaluable" for form in forms),
                "audit_evidence_complete_forms": sum(
                    bool(form["audit_evidence"]["complete"]) for form in forms),
                "referee_attestation_complete": (
                    runtime_attestation["complete"]),
                "referee_enforceable": runtime_attestation["enforceable"],
            },
            "errors": errors,
            "forms": sorted(forms, key=lambda item: item["slug"]),
        }
        attach_report_digest(report)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_bytes(report_bytes(report))
        print(json.dumps({
            "status": report["status"],
            **report["totals"],
            "out": str(args.out),
            "payload_sha256": report["payload_sha256"],
        }, sort_keys=True))
        if report["status"] == "ok":
            return 0
        return 2 if report["status"] == "unevaluable" else 1
    except (OSError, ValueError, KeyError, RefereeError, json.JSONDecodeError) as error:
        print(f"comb_referee: UNEVALUABLE: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
