#!/usr/bin/env python3
"""Round-trip every generated form, score it, and assert what scoring cannot see.

Two independent halves, deliberately:

**The round trip** prints each generated bundle to PDF with Chromium, re-extracts
it with the same extractor, and diffs against the source IR. Scoring is blunt --
rules and text runs recovered, as percentages -- and answers "did the browser
reproduce the geometry we told it to".

**The assertions** answer the question the round trip cannot ask. A round trip
compares our output against our own input, so anything wrong in the input is
reproduced faithfully and scores 100%. That is how this audit reported
`rules 100% on 51/51` while 137 real defects were present: a black rectangle over
a header, a seal printed upside-down, statutory tax brackets a taxpayer could
type over, money grids with no input fields at all. Every one of those survives a
perfect round trip.

The eight assertions in GOAL.md close that gap. Each publishes a boolean per form
under its own key so `gate.py` can demand it, plus a detail record naming the
offenders -- an assertion that fails without naming what failed is not
actionable. **True means the assertion holds.** Anything that cannot be evaluated
is `False` with a reason, never `True` and never absent: `gate.py` counts absence
as unevaluable and fails, which is the whole point.

The assertions read the source PDF's own drawing and text operators wherever the
question is "what does the official form actually print". That keeps them
independent of the IR schema and of the module whose output they are checking --
`comb_slots_match_printed` in particular must not be scored by the code that
produced the number under test.

Nothing here rasterises. Every measurement is a coordinate or a codepoint.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import bisect
import collections
import contextlib
import copy
import dataclasses
import functools
import hashlib
import importlib.machinery
import importlib.metadata
import importlib.util
import json
import math
import mimetypes
import os
import pathlib
import platform
import posixpath
import re
import signal
import stat
import statistics
import subprocess
import sys
import sysconfig
import tempfile
import traceback
import types
import urllib.parse
from decimal import Decimal
from html.parser import HTMLParser
from typing import Any, Iterable, Sequence

HERE = pathlib.Path(__file__).resolve().parent


@dataclasses.dataclass(frozen=True)
class _TrustedSource:
    name: str
    path: pathlib.Path
    payload: bytes
    sha256: str
    module: types.ModuleType


def _stable_read(path: pathlib.Path) -> bytes:
    """Read one path-bound regular file and reject mutation during the read."""
    flags = os.O_RDONLY
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise RuntimeError(
            f"trusted source could not be opened without following a "
            f"symlink: {path}") from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise RuntimeError(
                f"trusted source is not one regular file: {path}")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1 << 20)
            if not chunk:
                break
            chunks.append(chunk)
        payload = b"".join(chunks)
        after = os.fstat(descriptor)
        try:
            path_after = os.stat(path, follow_symlinks=False)
        except OSError as exc:
            raise RuntimeError(
                f"trusted source path changed while read: {path}") from exc
        stable_fields = (
            "st_dev", "st_ino", "st_mode", "st_size",
            "st_mtime_ns", "st_ctime_ns",
        )
        if (any(getattr(before, field) != getattr(after, field)
                for field in stable_fields)
                or not stat.S_ISREG(path_after.st_mode)
                or (path_after.st_dev, path_after.st_ino)
                != (after.st_dev, after.st_ino)
                or len(payload) != after.st_size):
            raise RuntimeError(
                f"trusted source changed while read: {path}")
        return payload
    finally:
        os.close(descriptor)


@contextlib.contextmanager
def _standard_importers_only() -> Iterable[None]:
    """Exclude caller-installed import hooks while trusted modules execute."""
    original = sys.meta_path[:]
    sys.meta_path[:] = [
        importlib.machinery.BuiltinImporter,
        importlib.machinery.FrozenImporter,
        importlib.machinery.PathFinder,
    ]
    try:
        yield
    finally:
        sys.meta_path[:] = original


def _execute_source_module(
        name: str,
        path: pathlib.Path,
        payload: bytes,
        bindings: dict[str, types.ModuleType],
        ) -> types.ModuleType:
    """Compile exact source bytes, bypassing bytecode and import hooks."""
    module = types.ModuleType(name)
    module.__file__ = str(path)
    module.__package__ = ""
    module.__loader__ = None
    module.__spec__ = importlib.util.spec_from_loader(
        name, loader=None, origin=str(path))
    source_sha = hashlib.sha256(payload).hexdigest()
    module.__dict__["__formgen_source_sha256__"] = source_sha
    code = compile(payload, str(path), "exec", dont_inherit=True)
    prior = {key: sys.modules.get(key) for key in (name, *bindings)}
    old_dont_write = sys.dont_write_bytecode
    try:
        sys.dont_write_bytecode = True
        sys.modules.update(bindings)
        sys.modules[name] = module
        with _standard_importers_only():
            exec(code, module.__dict__)
    finally:
        sys.dont_write_bytecode = old_dont_write
        for key, previous in prior.items():
            if previous is None:
                sys.modules.pop(key, None)
            else:
                sys.modules[key] = previous
    return module


def _load_trusted_formgen_modules(
        extract_path: pathlib.Path,
        verify_path: pathlib.Path,
        ) -> tuple[_TrustedSource, _TrustedSource]:
    """Load sibling producers from exact bytes, independent of sys.modules."""
    extract_payload = _stable_read(extract_path)
    verify_payload = _stable_read(verify_path)
    trusted_extract = _execute_source_module(
        "extract", extract_path, extract_payload, {})
    trusted_verify = _execute_source_module(
        "verify", verify_path, verify_payload,
        {"extract": trusted_extract},
    )
    if trusted_verify.__dict__.get("extract") is not trusted_extract:
        raise RuntimeError("verify did not bind the snapshotted extract module")
    return (
        _TrustedSource(
            "extract", extract_path.resolve(), extract_payload,
            hashlib.sha256(extract_payload).hexdigest(), trusted_extract),
        _TrustedSource(
            "verify", verify_path.resolve(), verify_payload,
            hashlib.sha256(verify_payload).hexdigest(), trusted_verify),
    )


_TRUSTED_EXTRACT, _TRUSTED_VERIFY = _load_trusted_formgen_modules(
    HERE / "extract.py", HERE / "verify.py")
extract = _TRUSTED_EXTRACT.module
verify = _TRUSTED_VERIFY.module
_AUDIT_SOURCE_PATH = pathlib.Path(__file__).resolve()
_AUDIT_SOURCE_PAYLOAD = _stable_read(_AUDIT_SOURCE_PATH)
# Keep the exact modules installed for any delayed sibling import, and make a
# later substitution visible to the before/after validator below.
sys.modules["extract"] = extract
sys.modules["verify"] = verify

# --------------------------------------------------------------------------
# tolerances
#
# None of these widen a verify.py tolerance; they are the thresholds the
# assertions need and each is derived from one that already exists.
# --------------------------------------------------------------------------

# Two rectangles "overlap" only if they share area in both axes. Sharing an edge
# is not an overlap: an input butted against the glyph beside it is correct.
OVERLAP_EPS_PT = 0.05

# A rule sits below the guide cut only if it clears it by more than the position
# tolerance. Exactly at the cut is exactly right.
CUT_EPS_PT = 0.25

# Shear small enough to be invisible is not a transform. 0.01pt is a 25th of the
# 0.25pt position tolerance, measured over the image's unit square, so the worst
# displacement it admits is 0.01pt. One corpus placement (0605 xref 13) carries
# b = 5.3e-05 and c = 1.7e-06 -- arithmetic noise in the producer, not a skew.
TRANSFORM_EPS = 0.01

# Comb geometry, for the printed-compartment oracle.
#   MERGE: two verticals closer than this cannot be two character compartments.
#          The tightest comb pitch in the corpus is 10.32pt, so this is a sixth
#          of the smallest real gap. It exists because the generator draws one
#          divider as two collinear pieces 0.6pt apart when the piece below the
#          band is thicker than the tick above it.
#   EDGE:  a vertical this close to the cell's own side is that side's border.
#   MINLEN: shorter ink than this is a decimal point or a dot leader, not a
#          divider. The shortest real tick measured is 2.88pt.
#   EDGE:  a divider is interior only if its *ink* clears the cell's own sides
#          by this much. Two facts set it. The cell box is not the frame's
#          centreline -- on 1701 the 1.44pt page frame's outer edge lands 0.15pt
#          inside the cell's x1, so a centre-based or hairline margin counts the
#          frame as a slot boundary and invents 46 merges across 1701 and 1701A.
#          And the 1st-percentile comb pitch in this corpus is 10.24pt, so no
#          real compartment boundary is ever within 2pt of its own cell's side.
#          The measured mismatch count is flat from 2.0 to 3.0pt, which is what
#          being past the artefact and short of real data looks like. (A handful
#          of degenerate combs report a sub-1pt pitch; those are broken combs,
#          and this margin can hide a boundary inside one.)
COMB_MERGE_PT = 1.5
COMB_EDGE_PT = 2.0
COMB_FALLBACK_HALFWIDTH_PT = 0.6   # for an `l` op that declares no stroke width
COMB_MINLEN_PT = 0.8
COMB_YSLACK_PT = 0.5
COMB_MAX_WIDTH_PT = 2.5   # 1.44pt group separators are in; column borders are not
# The reviewed comb-subject ledger may certify only that one exact layout cell
# owns one legacy subject rectangle.  Both active states are reviewed ownership
# decisions; their resolved/unresolved distinction is topology evidence and is
# deliberately not consumed by this oracle.
COMB_OWNER_REVIEWED_STATES = frozenset({
    "active_resolved",
    "active_unresolved",
})
# REVIEW BUNDLE (user-approved 2026-08-15). This judge already NAMES
# `active_composite` -- it is half of RETAINED_COMB_TRANSITIONS below, the
# set of destinations a retained subject is permitted to reach. What it
# lacked was any way to accept a subject that had ARRIVED there, so the
# first reviewed transition invalidated its form's whole comb-owner
# registry and every comb on that form failed inventory binding. Measured
# before the change: 16 forms carry a composite, the same 16 failed, and
# the two sets were identical.
#
# A composite is a SUPPRESSED subject, exactly like a retained one -- no
# active cell of its own, its legacy comb kept, its partition mapped. The
# single difference is that a reviewer has certified the transition, so it
# no longer blocks. It is admitted here on that certificate and on nothing
# else: `validate_comb_owner_registry` demands the certificate's shape, and
# comb_referee.py independently re-derives it against the review registry
# and against its own source corroboration. The producer cannot mint one.
COMB_SUPPRESSED_STATES = frozenset({
    "retained_unresolved",
    "active_composite",
})
COMPOSITE_TRANSITION_CERTIFICATE_KEYS = frozenset({
    "criterion", "registry_key", "transition",
    "suppression_criterion", "reviewer", "date",
})
RETAINED_COMB_SUBJECT_KEYS = frozenset({
    "subject_key",
    "legacy_cell_id",
    "legacy_bbox",
    "cell_id",
    "mapped_partition_cell_ids",
    "mapped_partition_subject_keys",
    "state",
    "emission",
    "reason_codes",
    "legacy_comb",
    "requires_independent_evidence",
    "permitted_transitions",
    "blocks_gate",
})
RETAINED_COMB_SUBJECT_OPTIONAL_KEYS = frozenset({
    "erased_edge_replacement_candidates",
})
RETAINED_COMB_TRANSITIONS = (
    "active_composite",
    "retired_proven_false",
)
RETAINED_PARTITION_REASON_CODES = (
    "emission-suppressed-no-rectangular-owner",
    "painted-edge-partition",
)
RETAINED_NO_BAND_REASON_CODES = (
    "emission-suppressed-no-final-visible-band",
)
# The third retained shape, and it is the SAME shape as the one above: one
# legacy subject, its own rectangle, an identity mapping onto itself, comb
# removed, emission suppressed, gate blocked. It is published by lattice.py
# when a cell's own printed text refutes the claim that its compartments are
# character cells (`lattice.REFUTED_CAPTION_BLOCK_REASON_CODE`, kept spelled
# out here rather than imported, because this file must not take a producer's
# word for the vocabulary it adjudicates).
#
# This tuple is added because the shape it names EXISTS -- 1606 p2's statutory
# rate table and the excise mastheads -- and because a retained subject the
# registry does not recognise fails the WHOLE form's registry
# (`comb-owner-registry-invalid`), not just its own record. Nothing here
# weakens the record: it is validated through exactly the identity branch
# RETAINED_NO_BAND_REASON_CODES goes through, so a refuted subject that is not
# an identity mapping onto its own still-present layout cell is rejected the
# same way, and `retained_unresolved subject still owns an active comb` above
# still fails it if the comb was left on the cell.
RETAINED_REFUTED_CAPTION_REASON_CODES = (
    "emission-suppressed-caption-block-not-character-cells",
)
# The fourth retained shape -- REVIEW BUNDLE RIDER, Sitting 2 DECISION A
# (2026-08-16), user-approved. Same identity shape again: one legacy
# subject, its own rectangle, comb removed, emission suppressed. It is
# published by lattice.py when the compartment rule refuses the legacy
# comb whole (no run of character-box-width compartments survives; the
# corpus census behind the 24.5pt bound lives at
# `lattice.COMB_COMPARTMENT_MAX_PT`, spelled out here rather than imported
# for the same reason as every tuple above) AND nothing current can own
# the cell -- 2551M p2c13's column-rule "comb" of 70.80/156.72pt and
# 1604CF p2c73's 68.64/26.64pt grid cells, both of which the user
# adjudicated by name. Validated through the identity branch like its two
# siblings; a subject carrying this reason while its cell still owns an
# active comb, or while its certificate names a different criterion than
# its reason codes table, still fails exactly as before. The referee
# corroborates the claim against Poppler under
# `source-crossing-rule-not-comb-scoped-v1` -- the "dividers" must outrun
# the comb band -- so nothing is taken on the producer's word.
RETAINED_COMPARTMENT_RULE_REASON_CODES = (
    "emission-suppressed-compartment-rule",
)
# The retained reason tuples that map a subject onto its own layout cell,
# one-to-one, rather than onto a partition of other cells.
RETAINED_IDENTITY_REASON_CODES = frozenset({
    RETAINED_NO_BAND_REASON_CODES,
    RETAINED_REFUTED_CAPTION_REASON_CODES,
    RETAINED_COMPARTMENT_RULE_REASON_CODES,
})
# emit.py serialises point geometry to four decimals. Two rounded endpoints can
# differ by at most two ten-thousandths of a point.
EMITTED_GEOMETRY_EPS_PT = 0.0002
# PyMuPDF source coordinates carry float noise at roughly the same scale. A
# smaller paper seam is not promoted into a visible source corridor.
SOURCE_COORD_EPS_PT = 0.0002
# This is verify.py's fixed position tolerance.  It is copied as a bound, not
# exposed as a CLI knob: changing it here would make this assertion an
# independently tunable answer rather than one aligned with the comb
# referee's adjudicated derivation (comb_referee.py carries the same bound
# under the same name).  It applies only to comparisons that cross
# representations into raw source geometry, whose floats are not the
# four-decimal emitted serialisation; every same-representation
# emitted/layout comparison keeps EMITTED_GEOMETRY_EPS_PT.
POSITION_TOL_PT = 0.25
# emit.py's field_verdict threshold, mirrored (emit.py PREPRINTED_COVERAGE): a
# cell whose own width is mostly pre-printed glyph ink is not a blank the
# taxpayer can write in, and the emitter refuses it an input.  The audit's
# money-box predicate must apply the same measured-ink rule -- half of the
# run's own height inside the cell, coverage as a fraction of the cell's
# width -- or it demands inputs exactly where the emitter is right to refuse
# them (1601EQ's ATC rows, 2200P's "XP010 Lubricating Oils").  Copied as a
# bound: tuning it here would split the two producers' definitions of
# "pre-printed".
PREPRINTED_COVERAGE = 0.5

# What the SOURCE itself already put in a comb compartment, and therefore which
# compartments cannot be blanks. Re-derived here from the source PDF's own text
# and paint operators; see `SourceSlotOracle`. The three bounds are copied, not
# invented -- lattice.py carries the tone pair as SHADED_PAPER_MAX_GRAY /
# SHADED_PAPER_MIN_COVERAGE and comb_referee.py repeats both -- because a
# compartment must not be "occupied" to one producer and "blank" to another.
# The lower tone bound is extract.classify_tone's structural/decorative
# boundary: a black rule that happens to cover a compartment is ink the sheet
# draws, never paper the sheet shaded, and must not excuse a missing input.
SOURCE_SHADING_MAX_TONE = 0.87
SOURCE_SHADING_MIN_TONE = 0.15
SOURCE_SHADING_MIN_COVERAGE = 0.70

# --------------------------------------------------------------------------
# The field-layer bounds (assertions 9 and 10).
#
# Both assertions exist because the audit was structurally blind to the FIELD
# layer: 171 of 172 ledger findings carry `audit_blind: true`, and 137 of the
# 138 defects a 51-form visual sweep found sat on pages this audit scored
# 100% rules / 100% text / 0 missing / 0 extra.  The reason is stated once,
# here, because it decides every bound below: the two assertions that come
# closest to the field layer take their CANDIDATE POPULATION from the producer
# that made the mistake.  `check_money_boxes_have_inputs` enumerates from
# `b.layout_cells` and accepts only `kind == "field"`, so a printed box the
# lattice mis-read as a label never enters the population -- and a `field` cell
# with zero inputs occurs 0 times in 9,971, which is the mechanism, not a
# clean bill of health.  `check_comb_slots_match_printed` opens with
# `if b.layout is None: return broken(...)` and takes its inventory from the
# layout's comb subjects, so a printed comb the lattice never recognised is not
# in it.  Neither assertion below reads `b.layout`, `b.plan`, emit.py's
# markers, or the IR: their whole expectation comes from the pinned PDF's own
# composited paint stream (`ordered_vector_paints`) and its own text operators
# (`drawn_glyph_boxes`), scored against the emitted DOM's `input_boxes`.
#
# A source vertical counts as a printed compartment divider when it is dark,
# thin, materially taller than it is wide, and still visible after the page has
# composited.  The visibility clause is not decoration: 2550M draws a comb tick
# and then paints a white 44 x 13pt rectangle over it, and 2553, 2551M and 0605
# do the same.  Dropping the clause inflates the offender count from 79 to 111
# with 32 dividers that are not on the printed page at all.  The clause was
# checked against the rasteriser rather than trusted: over 38,650 candidates on
# 53 page-1s, the compositor's visible/not-visible verdict agrees with the
# rendered raster 38,650 times and disagrees 0 times.
# REVIEW BUNDLE RIDER (user-approved 2026-08-15, decisions page): three
# relations under which an enclosed empty field cell is the sheet's own
# DECORATION rather than a writing box, so no input is demanded of it. Each
# population was censused corpus-wide, refuting weaker rules on the way (the
# refutations live in findings F235/F237), and the user approved every cell
# on the official sheets: the 6 TIN group separators (2553's peach three,
# 1604CF's grey three), the 1800 ATC sliver the row mosaic cut from the ONE
# undivided 'DN 010' box, and 2551Q's 0.88pt-tall invisible strip. Exactly 8
# cells corpus-wide; a ninth match is a regression to investigate, which is
# why every exclusion is published in the assertion's own counts.
ATC_CONSTANT_RE = re.compile(r"^[A-Z]{2} ?[0-9]{3}$")


def printed_decoration_reason(cell_id: str,
                              layout_cells_by_row: dict,
                              layout_cell: dict,
                              fills, runs,
                              min_glyph_height_pt) -> str | None:
    x0, y0 = float(layout_cell["x0"]), float(layout_cell["y0"])
    x1, y1 = float(layout_cell["x1"]), float(layout_cell["y1"])
    if (min_glyph_height_pt is not None
            and y1 - y0 < float(min_glyph_height_pt)):
        return "sub-glyph-height"
    for run in runs or ():
        if not ATC_CONSTANT_RE.fullmatch(str(run.get("text", "")).strip()):
            continue
        if (min(x1, float(run["x1"])) - max(x0, float(run["x0"])) > 1.0
                and min(y1, float(run["y1"])) - max(y0, float(run["y0"]))
                > 0.75):
            return "printed-constant"
    dedicated = any(
        abs(float(f["x0"]) - x0) <= 1.0 and abs(float(f["x1"]) - x1) <= 1.0
        and abs(float(f["y0"]) - y0) <= 1.0 and abs(float(f["y1"]) - y1) <= 1.0
        and not ((f.get("gray") is not None and float(f["gray"]) >= 0.99)
                 or (f.get("rgb")
                     and all(float(v) >= 0.99 for v in f["rgb"])))
        for f in fills or ())
    if not dedicated:
        return None
    row = layout_cells_by_row.get(
        (round(y0, 1), round(y1, 1)), [])
    index = next((i for i, c in enumerate(row)
                  if str(c["id"]) == str(cell_id)), None)
    if index is None:
        return None
    left = row[index - 1] if index > 0 else None
    right = row[index + 1] if index + 1 < len(row) else None
    if (left is not None and isinstance(left.get("comb"), dict)
            and right is not None and isinstance(right.get("comb"), dict)):
        return "comb-separator-fill"
    return None


DIVIDER_MAX_TONE = 0.5
DIVIDER_MAX_WIDTH_PT = 1.6
DIVIDER_MIN_HEIGHT_PT = 2.0
# Anisotropy, not height alone: a 1.5 x 2.2pt speck is not a divider.
DIVIDER_MIN_ANISOTROPY_PT = 2.0
# How far inside an input's own edges a divider must lie before it is a
# divider the input SPANS rather than the input's own wall.  A box drawn
# edge-to-edge over its printed frame must never be reported against itself.
DIVIDER_INTERIOR_PT = 0.5
DIVIDER_MIN_Y_OVERLAP_PT = 1.0

# The source-derived printed-box inventory (assertion 10).  A box is four
# covered sides around white paper: grid lines are clustered from thin dark
# paint, a side counts as drawn when no gap along it exceeds
# PRINTED_BOX_SIDE_MAX_GAP_PT, and the box survives only if its inset interior
# holds no source glyph and is overwhelmingly paper.  The glyph and paper
# clauses are what keep a caption cell and an official grey "no entry applies"
# band out of a population that says "a taxpayer should be able to type here".
PRINTED_RULE_MAX_TONE = 0.5
PRINTED_RULE_MAX_THICKNESS_PT = 2.5
PRINTED_RULE_MIN_LENGTH_PT = 2.0
PRINTED_RULE_CLUSTER_PT = 1.0
PRINTED_BOX_SIDE_MAX_GAP_PT = 1.2
PRINTED_BOX_MIN_SIDE_PT = 5.0
PRINTED_BOX_MAX_SIDE_PT = 400.0
PRINTED_BOX_INSET_PT = 1.0
PRINTED_BOX_PAPER_MIN_TONE = 0.95
PRINTED_BOX_PAPER_MIN_FRACTION = 0.95
PRINTED_BOX_SAMPLE_PITCH_PT = 1.5
PRINTED_BOX_SAMPLE_MAX = 8
# When does an emitted input "fill" a printed box?  Measured against the
# SMALLER of the two areas, so a comb's individual slot counts (the slot lies
# wholly inside the box) and a single wide input spanning several boxes counts
# for each of them (the box lies wholly inside the input) -- a taxpayer can
# type in both.  Per-input width fractions do not work: 2316's RDO comb fills
# its box with three 12.6pt slots inside a 38pt box and would read as empty.
PRINTED_BOX_FILL_MIN_FRACTION = 0.5
# Bucket edge for the point-tone index.  Purely a lookup granularity; it can
# never change a composited answer, only how many paints are considered.
TONE_BUCKET_PT = 24.0

# Default offender preview limit. Assertions that need exhaustive evidence may
# opt out explicitly; the comb assertion does because its full disagreement set
# is the evidence the referee needs.
MAX_OFFENDERS = 12

# The rate/reference tables. `reflow_rate_without_description` is about a
# relocated *table*, and these are the marker patterns guides.py assigns to one.
RATE_TABLE_MARKERS = frozenset({"table-n", "alphanumeric-tax-code"})

ASSERTION_KEYS = (
    "inputs_over_printed_text",
    "comb_slots_match_printed",
    "money_boxes_have_inputs",
    "rules_below_guide_cut",
    "run_colour_matches_ir",
    "reflow_rate_without_description",
    "image_transform_applied",
    "no_invented_codepoints",
    "inputs_span_no_printed_divider",
    "printed_box_peers_all_fillable",
)

INPUT_MANIFEST_SCHEMA = "formgen-audit-input-manifest-v1"
REQUIRED_INPUT_ROLES = ("ir", "layout", "html", "guide", "source_pdf")

Rect = tuple[float, float, float, float]


# --------------------------------------------------------------------------
# geometry
# --------------------------------------------------------------------------


def overlaps(a: Rect, b: Rect, eps: float = OVERLAP_EPS_PT) -> bool:
    return (min(a[2], b[2]) - max(a[0], b[0]) > eps
            and min(a[3], b[3]) - max(a[1], b[1]) > eps)


class InkIndex:
    """Printed glyph boxes on one page, bucketed by y so lookups stay cheap.

    A linear scan is 17s over the corpus for one assertion; two assertions need
    it, and an audit nobody runs protects nothing.
    """

    BUCKET_PT = 8.0

    def __init__(self, boxes: Iterable[tuple[Rect, Any]]) -> None:
        self.buckets: dict[int, list[tuple[Rect, Any]]] = collections.defaultdict(list)
        for box, tag in boxes:
            lo = int(math.floor(box[1] / self.BUCKET_PT))
            hi = int(math.floor(box[3] / self.BUCKET_PT))
            for key in range(lo, hi + 1):
                self.buckets[key].append((box, tag))

    def hits(self, rect: Rect) -> list[tuple[Rect, Any]]:
        lo = int(math.floor(rect[1] / self.BUCKET_PT))
        hi = int(math.floor(rect[3] / self.BUCKET_PT))
        out = []
        for key in range(lo, hi + 1):
            for box, tag in self.buckets.get(key, ()):
                if overlaps(rect, box):
                    out.append((box, tag))
        return out

    def any_hit(self, rect: Rect) -> tuple[Rect, Any] | None:
        for hit in self.hits(rect):
            return hit
        return None


# --------------------------------------------------------------------------
# The ink band of a glyph -- and why the box extract.py records is not it.
#
# THE MEASURED ANSWER, where the source states one.
#
# `glyph_ink_em` is the outline box of the face that drew the glyph, published
# per run by extract.py's `run_glyph_ink` and resolved by the machinery
# `ruled_blank_bars` already used: the glyph id `get_texttrace()` says was
# drawn, the faces `pdf_clean_font_name` says the page could be drawing, the
# advance the file itself states, and MuPDF's own bound on where that text op
# inks. It is not a convention, a table written here, or a font installed on
# this machine. Where it is present this file uses it for all four edges, and
# both over-reaches below are then simply gone: the box is the ink.
#
# It is present for 78.4% of this corpus's glyphs -- 279,101 of 356,092. The
# other 76,991 keep the advance box described below, which is what they always
# had -- extract.py counts every one of them by reason in `glyph_ink_refusals`,
# and the largest reason by far is a face MuPDF's own name cleaner does not
# resolve (Arial Narrow, Ebrima, Nirmala UI: 62,010 glyphs on 50 of the 53
# forms). A band that cannot be measured is never guessed.
#
# THE FALLBACK, for the 19%.
#
# extract.py stores a run's y-extent as MuPDF's span bbox, and that bbox is the
# font's LINE box rather than its ink. Measured over all 19,333 runs of this
# corpus: `y0 == baseline_y - ascender * size_pt` and
# `y1 == baseline_y - descender * size_pt` (19,105 runs exact on the lower
# edge, every other run inside 0.01pt bar 123 runs of one face whose reported
# descender rounds 0.22pt short). Scoring an emitted input against that box
# charges EVERY glyph in a run with the full descender depth of its face,
# whether or not the character has a descender -- so an input placed just
# under a caption set entirely in capitals is reported as sitting on printed
# text when the paper between them is blank. 15 of the 147 offenders this
# assertion published were exactly that, and every one of them clears by a
# margin re-measured against the real face outlines: worst case -0.39pt, most
# of them -0.7pt to -1.5pt of blank paper between the ink and the input.
#
# What is removed here is therefore blank paper, not a collision. Note what is
# NOT removed: a caption whose word carries a descender still collides, because
# the descender really does hang into the box. Eleven cells that a per-run
# reading of this same defect had written off as false positives keep failing
# for exactly that reason (1604CF p1c16/20/31/32 'p','g'; 2316 p1c62/83 'y';
# 1600WP p1c63/64 and 2316 p1c38/39 '(' and ')'; 1701MS p1c287 'g'), with real
# ink overlaps of +0.20pt to +0.90pt. The rule is per GLYPH, never per run.
#
# Those overlaps are this path's numbers, and the measured path revises three of
# them DOWNWARD and one class of them upward. A face's declared descender is not
# a bound on its glyphs' ink: Helvetica's 'p' and 'y' reach 0.2172 em where the
# span reports -0.210, and Arial-ItalicMT's 'g' likewise, so on 2316 p1c62/p1c83
# and 1701MS p1c287 -- three inputs the emitter had placed flush against the
# line box, at exactly 0.0000pt of overlap -- the outline is 0.0548pt and
# 0.0660pt inside the box, and those cells fail on the measured path having held
# on this one. Nothing about them changed; the line box was 0.05pt short of the
# ink it was standing in for.
#
# On this path the upper edge stays where extract.py put it (the ascent line):
# no character reaches above it, so keeping it can only over-report. It cannot
# be tightened by a constant the way the lower edge can, and for the same
# reason the horizontal edges cannot: cap height, x-height and side bearings
# are per-character and per-face quantities, while the descent this removes is
# a single figure the whole face shares.
#
# The horizontal over-reach is the same shape. `char_widths_pt[i]` is
# `bbox[2] - bbox[0]` of MuPDF's per-character box, and that box is the ADVANCE
# box, side bearings included: over the 437,615 characters of this corpus it
# equals `char_advances_pt[i]` to within the IR's own 0.005pt quantisation, and
# `page.get_texttrace()` -- the file's own operator stream, read independently
# by `drawn_glyph_boxes` -- reports the same x-extent to the millipoint.
# Neither view records a side bearing, and it cannot be bounded by a constant
# either: measured across the eleven real faces these PDFs name, the smallest
# left bearing of a character this corpus sets is -0.0015em (Arial 'A'), so the
# only sound uniform bound is zero and removes nothing. That is why the
# horizontal edges could not be tightened FROM THE IR AS IT WAS, and why the
# answer had to come from the face's own outline instead of from this file.
#
# GLYPH_BASELINE_OVERSHOOT_EM is that overshoot, measured rather than chosen.
# Over Arial, Arial Bold, Arial Italic, Arial Bold Italic, Arial Narrow, Arial
# Narrow Bold, Times New Roman, Times New Roman Bold, Times New Roman Italic,
# Tahoma and Tahoma Bold, the deepest any character in BASELINE_SEATED_INK
# reaches below its baseline is 0.0308em ('%', Arial Bold Italic); the round
# letters that motivate the allowance at all sit at 0.0117-0.0171em. It
# ENLARGES the ink band, so an error in it errs towards reporting a collision.
# Every character in DESCENDING_INK measures 0.1582em ('/', Tahoma) or deeper
# -- a fivefold gap on either side of the split, which is why no per-face table
# is needed and why widening the constant several times over would not move a
# single character across.
GLYPH_BASELINE_OVERSHOOT_EM = 0.0308

# Characters whose ink descends. `J` and `Q` are in it although the eleven
# faces measured keep `J` on the baseline: both descend in faces this corpus
# does set but that were not measurable here (Calibri, Berlin Sans FB Demi),
# and the failure direction of a wrong guess is what decides the membership --
# calling a descender baseline-seated hides ink, the reverse only keeps a
# report nobody needed.
DESCENDING_INK = frozenset("(),/;@JQ[]_gjpqy")
# `f` is the one character in this corpus whose descender is a property of the
# face rather than the character: it measures 0.2158em in Times New Roman
# Italic and exactly 0.0000em in Arial, Arial Bold, Arial Italic, Arial Narrow,
# Times New Roman and Tahoma. Splitting it out is what lets 2316 p1c37 stop
# reporting the `f` of "Date of Birth" -- set in upright Arial, 0.38pt of blank
# paper -- while an italic face keeps the full line box, which is wider than
# the italic descender actually needs and so cannot hide one.
ITALIC_ONLY_DESCENDING_INK = frozenset("f")
# Characters measured to stop at the baseline (within the overshoot above).
# This is an evidence list, not a character class: it holds exactly the
# characters this corpus prints whose depth was measured, so any character a
# new form introduces falls through to the full line box until it is measured
# too. U+F0A7 and U+FFFD are absent on purpose -- one is a Wingdings private
# use code and the other is a glyph whose encoding failed, and neither names a
# shape whose ink can be looked up.
BASELINE_SEATED_INK = frozenset(
    "0123456789"
    "ABCDEFGHIKLMNOPRSTUVWXYZ"
    "abcdefhiklmnorstuvwxz"
    "\"%&'*+-.:=?"
    "½–’“”•…●"
)
# A symbol-encoded face draws something other than the character its code
# names, so a character-indexed ink table cannot be applied to it. Named
# independently of fonts.py: this file must not take a producer's word for
# which faces those are.
SYMBOL_ENCODED_INK_FAMILIES = frozenset({
    "Symbol", "Wingdings", "Wingdings 2", "Wingdings 3", "Webdings",
    "ZapfDingbats", "Marlett",
})


def published_glyph_ink(run: dict, char: str
                        ) -> tuple[float, float, float, float] | None:
    """The em outline box the IR states for this character, or None.

    None is the fail-closed answer and the caller must fall back to the advance
    box on it. The shape is validated rather than trusted: a producer that
    published a degenerate or non-finite box would otherwise hand `overlaps` a
    rectangle it silently reads as "no collision", which is the one direction a
    checker must never fail in. A rotated run is refused here as well as in
    extract.py -- the box is in font space, and the matrix that would place it
    on the page is not in the IR.
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
    if run.get("rotated") or not run.get("size_pt"):
        return None
    if run.get("baseline_y") is None:
        return None
    return (float(box[0]), float(box[1]), float(box[2]), float(box[3]))


def glyph_ink_bottom(run: dict, char: str) -> float | None:
    """Lowest y this character's ink can reach, or None when that is unknown.

    The FALLBACK path's answer, for a glyph whose outline the source did not
    state. None is the fail-closed answer and every caller must fall back to
    the run's own recorded line box on it. It is returned whenever the
    reasoning above does not apply: a run without the metrics the derivation
    needs, a rotated run whose baseline is not horizontal, a symbol-encoded
    face, a character that has not been measured, or a character that descends.
    """
    if char in DESCENDING_INK or char not in BASELINE_SEATED_INK:
        return None
    if char in ITALIC_ONLY_DESCENDING_INK and run.get("italic"):
        return None
    if run.get("rotated"):
        return None
    if run.get("family") in SYMBOL_ENCODED_INK_FAMILIES:
        return None
    baseline = run.get("baseline_y")
    size = run.get("size_pt")
    if baseline is None or not size:
        return None
    return float(baseline) + GLYPH_BASELINE_OVERSHOOT_EM * float(size)


def glyph_boxes(run: dict) -> list[Rect]:
    """One box per *inked* glyph in a text run.

    The run bbox is the wrong unit for "does an input sit on pre-printed text".
    A label like `'Yes            No'` is one run whose bbox spans the checkbox
    drawn in the gap between the two words; scored by bbox it reports a collision
    that is not there, and 269 of the 362 bbox collisions in this corpus are that
    artefact.

    Where the source states the glyph's outline, that outline IS the box, on all
    four edges: the em box hung off the baseline and origin the run states, at
    the size it states. Where it does not, the glyph keeps the advance box --
    its own advance horizontally, and `glyph_ink_bottom` vertically -- which is
    wider than the ink on both axes and so can only over-report. The two are
    never mixed within one glyph: a measured glyph is measured on every edge,
    and a fallback glyph falls back on every edge.
    """
    out: list[Rect] = []
    offsets = run.get("char_origin_offsets_pt") or ()
    widths = run.get("char_widths_pt") or ()
    if len(offsets) != len(run["text"]) or len(widths) != len(run["text"]):
        # Without per-glyph metrics the run bbox is all there is; say so by
        # returning it, so the assertion errs towards reporting a collision.
        return [(run["x0"], run["y0"], run["x1"], run["y1"])]
    origin = run.get("origin_x", run["x0"])
    top = run["y0"]
    line_bottom = run["y1"]
    for char, offset, width in zip(run["text"], offsets, widths):
        if not char.strip():
            continue
        x = origin + offset
        ink = published_glyph_ink(run, char)
        if ink is not None:
            size = float(run["size_pt"])
            baseline = float(run["baseline_y"])
            # Font space counts y up from the baseline; the page counts it down.
            out.append((x + size * ink[0], baseline - size * ink[3],
                        x + size * ink[2], baseline - size * ink[1]))
            continue
        ink_bottom = glyph_ink_bottom(run, char)
        # A band that does not reach below the ascent line is not a measurement
        # of anything; fall back rather than publish a degenerate box, which
        # `overlaps` would silently read as "no collision".
        bottom = (line_bottom if ink_bottom is None or ink_bottom <= top
                  else min(line_bottom, ink_bottom))
        out.append((x, top, x + width, bottom))
    return out


# --------------------------------------------------------------------------
# emitted-document parsing
#
# The markup, not the layout engine. emit.py writes every position it means as
# an inline `pt` value in the same coordinate space as the IR, so parsing is
# exact, deterministic and about 400x faster than driving a browser. Playwright
# would only add its own layout opinion between us and the numbers we wrote.
# --------------------------------------------------------------------------

CELL_RE = re.compile(r'<div id="(p(\d+)c\d+)" class="([^"]*)"([^>]*)>')
# The last cell on a page is followed by the page's closing tags, an inert band
# template, or the band script, so cell content has to stop there too. Template
# inputs are blueprints, not live inputs belonging to the preceding cell.
CELL_BOUNDARY_RE = re.compile(
    r'<div id="p\d+c\d+" class="|<div class="page |<template|<script')
STYLE_BOX_RE = re.compile(
    r'style="left:([-\d.]+)pt;top:([-\d.]+)pt;width:([-\d.]+)pt;height:([-\d.]+)pt"')
SLOT_RE = re.compile(
    r'<div class="s" data-slot="(\d+)" '
    r'style="left:([-\d.]+)pt;top:([-\d.]+)pt;width:([-\d.]+)pt;height:([-\d.]+)pt"\s*>'
    r'(.*?)</div>', re.S)
INPUT_RE = re.compile(r'<input\b([^>]*)>')
INSET_RE = re.compile(
    r'inset:([-\d.]+)pt ([-\d.]+)pt ([-\d.]+)pt ([-\d.]+)pt')
RUN_RE = re.compile(r'<div class="t" id="p(\d+)t(\d+)" style="([^"]*)"')
PAGE_SPLIT_RE = re.compile(r'<div class="page page-(\d+)"')
SVG_RECT_RE = re.compile(r'<rect\b([^>]*)/>')
SVG_IMAGE_RE = re.compile(r'<image\b([^>]*)/>')
ATTR_RE = re.compile(r'([-a-zA-Z0-9:]+)="([^"]*)"')
COLOR_RE = re.compile(r'(?:^|;)color:#([0-9a-fA-F]{6})')
SECTION_RE = re.compile(r'<section class="gl-page"([^>]*)>(.*?)</section>', re.S)
ROW_RE = re.compile(r'<tr>(.*?)</tr>', re.S)
TD_RE = re.compile(r'<td[^>]*>(.*?)</td>', re.S)
TAG_RE = re.compile(r'<[^>]*>')
GL_TABLE_RE = re.compile(r'<table class="gl-table"[^>]*>(.*?)</table>', re.S)
# The reflow hazard is content-shaped, not column-shaped: a bare rate value
# ("3%", "0.5 %") or a bare alphanumeric tax code ("WB 050", "PT 060") with
# nothing descriptive beside it on the row.  A continuation row of a
# multi-line description has descriptive text and passes.
RATE_VALUE_RE = re.compile(r'\d+(?:\.\d+)?\s*%')
ATC_CODE_VALUE_RE = re.compile(r'[A-Z]{1,3}\s?\d{2,4}')


@dataclasses.dataclass
class Cell:
    id: str
    page: int
    classes: str
    attrs: str
    rect: Rect
    inner: str
    dom_page: int | None = None
    dom_record: _DomCellRecord | None = None

    @property
    def comb_slots_attr(self) -> int | None:
        got = re.search(r'data-comb-slots="(\d+)"', self.attrs)
        return int(got.group(1)) if got else None


CANONICAL_CELL_ID_RE = re.compile(r"p(\d+)c\d+\Z")
DOM_PAGE_CLASS_RE = re.compile(r"(?:^|\s)page-(\d+)(?:\s|$)")


@dataclasses.dataclass
class _DomInputRecord:
    element_index: int
    data_slot_index: int | None
    owning_slot: tuple[int, int] | None
    inset: tuple[float, float, float, float] | None
    input_type: str
    editable: bool


@dataclasses.dataclass
class _DomSlotRecord:
    element_index: int
    index: int | None
    geometry: dict[str, float] | None
    input_indexes: list[int | None] = dataclasses.field(default_factory=list)


@dataclasses.dataclass
class _DomCellRecord:
    element_index: int
    cell_id: str | None
    dom_page: int | None
    live: bool
    comb_marked: bool
    slots: list[_DomSlotRecord] = dataclasses.field(default_factory=list)
    inputs: list[_DomInputRecord] = dataclasses.field(default_factory=list)
    unowned_slot_inputs: list[dict[str, Any]] = dataclasses.field(
        default_factory=list)

    @property
    def slot_count(self) -> int:
        return len(self.slots)


class _EmittedDomScanner(HTMLParser):
    """Track live comb ownership using real nesting, including page owners."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.current_page: int | None = None
        self.template_depth = 0
        self.guide_depth = 0
        self.section_stack: list[bool] = []
        self.div_stack: list[
            tuple[int | None, int | None, tuple[int, int] | None]
        ] = []
        self.cell_stack: list[int] = []
        self.slot_stack: list[tuple[int, int]] = []
        self.records: list[_DomCellRecord] = []
        self.orphan_slots: list[dict[str, Any]] = []
        self.element_index = 0

    @property
    def live(self) -> bool:
        return self.template_depth == 0 and self.guide_depth == 0

    def handle_starttag(
            self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        values = {name.lower(): (value or "") for name, value in attrs}
        classes = set(values.get("class", "").split())
        if tag == "template":
            self.template_depth += 1
            return
        if tag == "section":
            is_guide = "gl-page" in classes
            self.section_stack.append(is_guide)
            self.guide_depth += int(is_guide)
            return
        if tag == "input":
            if not self.live:
                return
            marked_slot_input = "data-slot-index" in values
            owners = [
                index for index in self.cell_stack
                if self.records[index].live
            ]
            slots = [
                slot for slot in self.slot_stack
                if slot[0] in owners
            ]
            raw_index = values.get("data-slot-index")
            try:
                input_index = (
                    int(raw_index) if raw_index not in (None, "") else None)
            except ValueError:
                input_index = None
            input_type = values.get("type", "text").lower() or "text"
            style = values.get("style", "").lower().replace(" ", "")
            editable = (
                "disabled" not in values
                and "readonly" not in values
                and "hidden" not in values
                and values.get("aria-hidden", "").lower() != "true"
                and input_type not in {
                    "hidden", "button", "submit", "reset", "image"}
                and "display:none" not in style
                and "visibility:hidden" not in style
            )
            inset_match = INSET_RE.search(values.get("style", ""))
            inset = (
                tuple(float(inset_match.group(index))
                      for index in (1, 2, 3, 4))
                if inset_match is not None else None
            )
            owning_slot = slots[0] if len(slots) == 1 else None
            if len(owners) == 1:
                self.records[owners[0]].inputs.append(_DomInputRecord(
                    element_index=self.element_index,
                    data_slot_index=input_index,
                    owning_slot=owning_slot,
                    inset=inset,
                    input_type=input_type,
                    editable=editable,
                ))
            if len(owners) == 1 and len(slots) == 1 and editable:
                owner_index, slot_index = slots[0]
                self.records[owner_index].slots[
                    slot_index].input_indexes.append(input_index)
            elif marked_slot_input or (len(slots) == 1 and not editable):
                issue = {
                    "element_index": self.element_index,
                    "dom_page": self.current_page,
                    "owner_ids": [
                        self.records[index].cell_id for index in owners
                    ],
                    "data_slot_index": raw_index,
                    "reason": (
                        "comb input is not one live editable input enclosed "
                        "by exactly one live physical slot in exactly one "
                        "live cell"),
                }
                if len(owners) == 1:
                    self.records[owners[0]].unowned_slot_inputs.append(issue)
                else:
                    self.orphan_slots.append(issue)
            self.element_index += 1
            return
        if tag != "div":
            return

        prior_page = self.current_page
        page_match = DOM_PAGE_CLASS_RE.search(values.get("class", ""))
        if page_match:
            self.current_page = int(page_match.group(1))

        cell_id = values.get("id") or None
        canonical = (
            CANONICAL_CELL_ID_RE.fullmatch(cell_id)
            if cell_id is not None else None
        )
        comb_marker = (
            values.get("data-field-kind", "").lower() == "comb"
            or "data-comb-slots" in values
            or "data-comb-capacity" in values
        )
        is_slot = "s" in classes and "data-slot" in values
        is_cell = (
            "c" in classes
            or canonical is not None
            or comb_marker
        ) and not is_slot
        pushed_cell: int | None = None
        pushed_slot: tuple[int, int] | None = None
        if is_cell:
            pushed_cell = len(self.records)
            self.records.append(_DomCellRecord(
                element_index=self.element_index,
                cell_id=cell_id,
                dom_page=self.current_page,
                live=self.live,
                comb_marked=comb_marker,
            ))
            self.cell_stack.append(pushed_cell)
        if is_slot and self.live:
            owners = [
                index for index in self.cell_stack
                if self.records[index].live
            ]
            raw_slot_index = values.get("data-slot")
            try:
                slot_index = (
                    int(raw_slot_index)
                    if raw_slot_index not in (None, "") else None)
            except ValueError:
                slot_index = None
            box = STYLE_BOX_RE.search(
                f'style="{values.get("style", "")}"')
            geometry = None
            if box is not None:
                left, top, width, height = (
                    float(box.group(index)) for index in (1, 2, 3, 4))
                geometry = {
                    "index": slot_index,
                    "left": left,
                    "top": top,
                    "width": width,
                    "height": height,
                }
            if len(owners) == 1:
                owner = self.records[owners[0]]
                owner.slots.append(_DomSlotRecord(
                    element_index=self.element_index,
                    index=slot_index,
                    geometry=geometry,
                ))
                owner.comb_marked = True
                pushed_slot = (owners[0], len(owner.slots) - 1)
                self.slot_stack.append(pushed_slot)
            else:
                self.orphan_slots.append({
                    "element_index": self.element_index,
                    "dom_page": self.current_page,
                    "data_slot": raw_slot_index,
                    "owner_ids": [
                        self.records[index].cell_id for index in owners
                    ],
                    "reason": (
                        "live physical slot is not enclosed by exactly one "
                        "live cell container"),
                })
        self.div_stack.append((prior_page, pushed_cell, pushed_slot))
        self.element_index += 1

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "template":
            self.template_depth = max(0, self.template_depth - 1)
            return
        if tag == "section":
            if self.section_stack:
                self.guide_depth -= int(self.section_stack.pop())
            return
        if tag != "div" or not self.div_stack:
            return
        prior_page, pushed_cell, pushed_slot = self.div_stack.pop()
        if pushed_slot is not None:
            if self.slot_stack and self.slot_stack[-1] == pushed_slot:
                self.slot_stack.pop()
            elif pushed_slot in self.slot_stack:
                self.slot_stack.remove(pushed_slot)
        if pushed_cell is not None:
            if self.cell_stack and self.cell_stack[-1] == pushed_cell:
                self.cell_stack.pop()
            elif pushed_cell in self.cell_stack:
                self.cell_stack.remove(pushed_cell)
        self.current_page = prior_page

    def handle_startendtag(
            self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)


@functools.lru_cache(maxsize=2)
def _scan_emitted_dom(
        html: str,
        ) -> tuple[tuple[_DomCellRecord, ...], tuple[dict[str, Any], ...]]:
    scanner = _EmittedDomScanner()
    scanner.feed(html)
    scanner.close()
    return tuple(scanner.records), tuple(scanner.orphan_slots)


def parse_cells(html: str) -> list[Cell]:
    """Every field/label cell div with its box, in document order."""
    dom_records, _orphan_slots = _scan_emitted_dom(html)
    by_id: dict[str, collections.deque[_DomCellRecord]] = (
        collections.defaultdict(collections.deque))
    for record in dom_records:
        if record.cell_id is not None:
            by_id[record.cell_id].append(record)
    starts = list(CELL_RE.finditer(html))
    cells: list[Cell] = []
    for index, match in enumerate(starts):
        dom_record = (
            by_id[match.group(1)].popleft()
            if by_id[match.group(1)] else None
        )
        if dom_record is not None and not dom_record.live:
            continue
        limit = starts[index + 1].start() if index + 1 < len(starts) else len(html)
        stop = CELL_BOUNDARY_RE.search(html, match.end(), limit)
        inner = html[match.end():stop.start() if stop else limit]
        box = STYLE_BOX_RE.search(match.group(4))
        if not box:
            continue
        left, top, width, height = (float(box.group(i)) for i in (1, 2, 3, 4))
        cells.append(Cell(id=match.group(1), page=int(match.group(2)),
                          classes=match.group(3), attrs=match.group(4),
                          rect=(left, top, left + width, top + height),
                          inner=inner,
                          dom_page=(
                              dom_record.dom_page
                              if dom_record is not None else None
                          ),
                          dom_record=dom_record))
    return cells


def live_comb_inventory_issues(
        html: str, parsed_cells: Sequence[Cell],
        ) -> list[dict[str, Any]]:
    """Every live comb marker/slot must belong to one parsed canonical cell."""
    records, orphan_slots = _scan_emitted_dom(html)
    parsed_remaining = collections.Counter(cell.id for cell in parsed_cells)
    issues: list[dict[str, Any]] = [dict(issue) for issue in orphan_slots]
    for record in records:
        for input_issue in record.unowned_slot_inputs:
            issues.append({
                **input_issue,
                "cell_id": record.cell_id,
                "slot_count": record.slot_count,
            })
        if not record.live or not (record.comb_marked or record.slot_count):
            continue
        canonical = (
            CANONICAL_CELL_ID_RE.fullmatch(record.cell_id)
            if record.cell_id is not None else None
        )
        if canonical is None:
            issues.append({
                "element_index": record.element_index,
                "cell_id": record.cell_id,
                "dom_page": record.dom_page,
                "slot_count": record.slot_count,
                "reason": (
                    "live comb container has no canonical pNcN cell id"),
            })
            continue
        if parsed_remaining[record.cell_id] <= 0:
            issues.append({
                "element_index": record.element_index,
                "cell_id": record.cell_id,
                "dom_page": record.dom_page,
                "slot_count": record.slot_count,
                "reason": (
                    "live canonical comb container was not parsed as a cell"),
            })
            continue
        parsed_remaining[record.cell_id] -= 1
    return sorted(
        issues,
        key=lambda issue: (
            int(issue.get("element_index", -1)),
            str(issue.get("cell_id") or ""),
            str(issue.get("reason") or ""),
        ),
    )


def emitted_cell_binding_issues(b: Any) -> list[dict[str, Any]]:
    """Bind every live canonical cell to one layout owner and actual DOM page.

    A cell the guide plan records as a clipped straddler is deliberately
    emitted at its clipped extent, so its expected rectangle is the
    straddler record's ``form`` rect, not the unclipped layout rect.  The
    substitution demands the exact ``disposition == "clipped"`` record and
    is published as ``clipped_by_guide_cut`` -- any other geometry drift on
    such a cell still fails at the same tolerance.
    """
    if getattr(b, "layout", None) is None:
        return []
    relocated = set(getattr(b, "relocated_cells", set()))
    clipped_cell_rects: dict[str, Rect] = {}
    for region in getattr(b, "regions", None) or ():
        for straddler in region.get("straddlers") or ():
            if (not isinstance(straddler, dict)
                    or straddler.get("kind") != "cell"
                    or straddler.get("disposition") != "clipped"):
                continue
            ref = straddler.get("ref")
            form_rect = straddler.get("form")
            if not isinstance(ref, str) or not isinstance(form_rect, dict):
                continue
            try:
                clipped_cell_rects[ref] = (
                    float(form_rect["x0"]), float(form_rect["y0"]),
                    float(form_rect["x1"]), float(form_rect["y1"]),
                )
            except (KeyError, TypeError, ValueError):
                continue
    layout_subjects: dict[
        str, list[tuple[int, dict[str, Any]]]
    ] = collections.defaultdict(list)
    for page_index, page in sorted(getattr(b, "layout_pages", {}).items()):
        for layout_cell in page.get("cells", ()):
            cell_id = layout_cell.get("id")
            if isinstance(cell_id, str) and cell_id not in relocated:
                layout_subjects[cell_id].append((page_index, layout_cell))

    emitted: dict[str, list[Cell]] = collections.defaultdict(list)
    emitted_order: list[str] = []
    for cell in getattr(b, "cells", ()):
        if cell.id not in emitted:
            emitted_order.append(cell.id)
        emitted[cell.id].append(cell)

    issues: list[dict[str, Any]] = []
    has_real_html = isinstance(getattr(b, "form_html", None), str)
    for cell_id in emitted_order:
        cells = emitted[cell_id]
        subjects = layout_subjects.get(cell_id, ())
        kinds: list[str] = []
        reasons: list[str] = []
        if len(cells) != 1:
            kinds.append("duplicate-emitted-cell-id")
            reasons.append(
                f"emitted document contains {len(cells)} live cells with id "
                f"{cell_id}; exactly one is required")
        if len(subjects) != 1:
            kinds.append(
                "missing-layout-cell-owner"
                if not subjects else "duplicate-layout-cell-owner")
            reasons.append(
                f"layout contains {len(subjects)} non-relocated owners for "
                f"{cell_id}; exactly one is required")
        if len(cells) == 1 and len(subjects) == 1:
            emitted_cell = cells[0]
            page_index, layout_cell = subjects[0]
            dom_page = emitted_cell.dom_page
            if dom_page is None and not has_real_html:
                dom_page = emitted_cell.page
            if (emitted_cell.page != page_index
                    or dom_page != page_index):
                kinds.append("emitted-cell-page-mismatch")
                reasons.append(
                    "cell id page, enclosing DOM page, and layout page differ")
            layout_rect = (
                float(layout_cell["x0"]), float(layout_cell["y0"]),
                float(layout_cell["x1"]), float(layout_cell["y1"]),
            )
            clipped_rect = clipped_cell_rects.get(cell_id)
            clipped_by_guide_cut = clipped_rect is not None
            expected_rect = (
                clipped_rect if clipped_rect is not None else layout_rect)
            deltas = [
                actual - expected
                for actual, expected in zip(emitted_cell.rect, expected_rect)
            ]
            if any(abs(delta) > EMITTED_GEOMETRY_EPS_PT
                   for delta in deltas):
                kinds.append("emitted-cell-geometry-mismatch")
                reasons.append(
                    "emitted cell rectangle differs from its guide-cut "
                    "clipped extent"
                    if clipped_by_guide_cut else
                    "emitted cell rectangle differs from its layout owner")
            evidence = {
                "cell": cell_id,
                "emitted_occurrences": 1,
                "layout_occurrences": 1,
                "emitted_id_page": emitted_cell.page,
                "emitted_dom_page": dom_page,
                "layout_page": page_index,
                "actual_rect": list(emitted_cell.rect),
                "expected_rect": list(expected_rect),
                "clipped_by_guide_cut": clipped_by_guide_cut,
                **({
                    "unclipped_layout_rect": list(layout_rect),
                } if clipped_by_guide_cut else {}),
                "rect_deltas_pt": [
                    round(delta, 6) for delta in deltas],
                "tolerance_pt": EMITTED_GEOMETRY_EPS_PT,
            }
        else:
            evidence = {
                "cell": cell_id,
                "emitted_occurrences": len(cells),
                "layout_occurrences": len(subjects),
            }
        if kinds:
            issues.append({
                **evidence,
                "failure_kinds": kinds,
                "why": "; ".join(reasons),
            })
    return issues


def slot_boxes(cell: Cell) -> list[tuple[int, Rect, bool]]:
    """Comb slots as (index, absolute box, whether it holds an input)."""
    left, top, _, _ = cell.rect
    if cell.dom_record is not None:
        live_out: list[tuple[int, Rect, bool]] = []
        for slot in cell.dom_record.slots:
            geometry = slot.geometry
            if slot.index is None or geometry is None:
                continue
            x = float(geometry["left"])
            y = float(geometry["top"])
            width = float(geometry["width"])
            height = float(geometry["height"])
            live_out.append((
                slot.index,
                (left + x, top + y, left + x + width, top + y + height),
                bool(slot.input_indexes),
            ))
        return live_out
    out = []
    for match in SLOT_RE.finditer(cell.inner):
        x, y, w, h = (float(match.group(i)) for i in (2, 3, 4, 5))
        out.append((int(match.group(1)), (left + x, top + y, left + x + w, top + y + h),
                    "<input" in match.group(6)))
    return out


def emitted_comb_evidence(cells: Sequence[Cell],
                          source: SourceSlotOracle | None = None,
                          ) -> dict[str, Any]:
    """Physical emitted-comb state without falling back to layout metadata.

    `source` answers the one question this evidence cannot answer from the
    emitted document: whether a compartment carrying no input is a compartment
    the SOURCE already filled in. Every compartment of an editable comb must
    offer a typing surface EXCEPT the ones the official sheet printed a
    constant into or shaded, and those are read from the source PDF's own
    operators (`SourceSlotOracle`) rather than from anything the emitter says
    about itself. Passing None -- an ambiguous owner, a comb with no layout
    subject at all -- means that evidence was not established, and then no
    compartment is excused.
    """
    occurrences = len(cells)
    if occurrences == 0:
        return {
            "valid": False,
            "state": "missing-emitted-cell",
            "slots": None,
            "physical_slots": None,
            "declared_slots": None,
            "occurrences": occurrences,
            "reason": "emitted document has no matching cell",
        }
    if occurrences != 1:
        return {
            "valid": False,
            "state": "duplicate-emitted-cell",
            "slots": None,
            "physical_slots": None,
            "declared_slots": None,
            "occurrences": occurrences,
            "reason": (
                f"emitted document contains {occurrences} cells with this id; "
                "exactly one is required"),
        }

    cell = cells[0]
    matches = list(SLOT_RE.finditer(cell.inner))
    dom_slots = (
        list(cell.dom_record.slots)
        if cell.dom_record is not None else None
    )
    indexes: list[int | None] = (
        [slot.index for slot in dom_slots]
        if dom_slots is not None
        else [int(match.group(1)) for match in matches]
    )
    physical = len(indexes)
    declared = cell.comb_slots_attr
    if physical == 0:
        if declared is None:
            state = "missing-comb-markup"
            reason = "emitted cell has no comb slot markup"
            slots: int | None = None
        else:
            state = "zero-physical-slots"
            reason = (
                f"emitted comb declares {declared} slot(s) but renders zero "
                "physical slots")
            slots = 0
        return {
            "valid": False,
            "state": state,
            "slots": slots,
            "physical_slots": physical,
            "declared_slots": declared,
            "occurrences": occurrences,
            "reason": reason,
        }
    if declared is None:
        return {
            "valid": False,
            "state": "missing-declared-slot-count",
            "slots": physical,
            "physical_slots": physical,
            "declared_slots": None,
            "occurrences": occurrences,
            "slot_indexes": indexes,
            "reason": (
                "emitted physical comb slots have no data-comb-slots "
                "declaration"),
        }
    if len(set(indexes)) != physical:
        return {
            "valid": False,
            "state": "duplicate-slot-index",
            "slots": physical,
            "physical_slots": physical,
            "declared_slots": declared,
            "occurrences": occurrences,
            "slot_indexes": indexes,
            "reason": (
                "emitted comb repeats a physical data-slot index "
                f"({indexes})"),
        }
    expected_indexes = list(range(physical))
    if indexes != expected_indexes:
        return {
            "valid": False,
            "state": "invalid-slot-index-sequence",
            "slots": physical,
            "physical_slots": physical,
            "declared_slots": declared,
            "occurrences": occurrences,
            "slot_indexes": indexes,
            "reason": (
                "emitted comb data-slot indexes must be exactly ordered "
                f"0..{physical - 1}; got {indexes}"),
        }
    input_indexes: list[list[int | None]] = (
        [list(slot.input_indexes) for slot in dom_slots]
        if dom_slots is not None else []
    )
    bad_input_indexes: list[dict[str, Any]] = []
    if dom_slots is None:
        for slot_index, match in zip(indexes, matches):
            within_slot: list[int | None] = []
            for input_match in INPUT_RE.finditer(match.group(6)):
                index_match = re.search(
                    r'(?:^|\s)data-slot-index="(\d+)"(?:\s|$)',
                    input_match.group(1),
                )
                input_index = int(index_match.group(1)) if index_match else None
                within_slot.append(input_index)
            input_indexes.append(within_slot)
    # The compartment a taxpayer would type into, so the source is asked about
    # that exact rectangle. An excuse cannot carry a comb whose boxes are not
    # where the sheet prints them: whenever the emitted partition is not the
    # source's own the comb is an offender anyway, on
    # `emission-printed-mismatch` for a different compartment count, on
    # `emission-source-position-mismatch` for edges past the referee's
    # position bound, and on `source-topology-unevaluable` where the source
    # partition could not be derived at all. The excuse only ever decides
    # whether `invalid-emission` joins those, never whether the comb passes.
    #
    # Beside it, the same compartment over its printed ROW: the walls stay the
    # source's dividers and the top and bottom become the cell's, which the
    # sheet drew, instead of the writing rectangle's, which emit chose. That is
    # the rectangle the glyph question is asked of; `SourceSlotOracle` states
    # why, and measures what it costs (nothing) and what it buys (85 identical
    # money bullets that answered differently from their 7 twins).
    slot_rects = {index: box for index, box, _live in slot_boxes(cell)}
    slot_rows = {
        index: (box[0], cell.rect[1], box[2], cell.rect[3])
        for index, box in slot_rects.items()
    }
    source_filled: dict[int, dict[str, Any]] = {}
    for slot_index, within_slot in zip(indexes, input_indexes):
        for input_index in within_slot:
            if input_index != slot_index:
                bad_input_indexes.append({
                    "slot": slot_index,
                    "input_slot_index": input_index,
                })
        if len(within_slot) > 1:
            bad_input_indexes.append({
                "slot": slot_index,
                "input_slot_index": within_slot,
                "reason": "multiple editable inputs in one physical slot",
            })
        if "f" in cell.classes.split() and not within_slot:
            occupancy = (
                source.occupancy(
                    slot_rects.get(slot_index), slot_rows.get(slot_index))
                if source is not None else None
            )
            if occupancy is not None:
                source_filled[slot_index] = occupancy
                continue
            if source is not None and source.available:
                why_not = (
                    "the source prints no glyph or shading in that "
                    "compartment")
            else:
                unavailable = (
                    source.unavailable_reason if source is not None else None)
                why_not = (
                    "source compartment occupancy is unevaluable: "
                    + (unavailable or "no source evidence was supplied"))
            bad_input_indexes.append({
                "slot": slot_index,
                "input_slot_index": None,
                "reason": (
                    "editable comb slot has no live input element and "
                    + why_not),
            })
    nested_input_count = sum(len(items) for items in input_indexes)
    if dom_slots is None:
        marked_input_count = sum(
            "data-slot-index" in input_match.group(1)
            for input_match in INPUT_RE.finditer(cell.inner)
        )
        if marked_input_count != nested_input_count:
            bad_input_indexes.append({
                "reason": (
                    "one or more slot-indexed inputs are outside a physical "
                    "slot"),
                "nested": nested_input_count,
                "marked": marked_input_count,
            })
    elif cell.dom_record is not None and cell.dom_record.unowned_slot_inputs:
        bad_input_indexes.extend(cell.dom_record.unowned_slot_inputs)
    if bad_input_indexes:
        return {
            "valid": False,
            "state": "slot-input-index-mismatch",
            "slots": physical,
            "physical_slots": physical,
            "declared_slots": declared,
            "occurrences": occurrences,
            "slot_indexes": indexes,
            "input_slot_indexes": input_indexes,
            "source_filled_slots": source_filled,
            "reason": (
                "one or more comb inputs do not identify their owning slot: "
                f"{bad_input_indexes}"),
        }

    try:
        geometry = (
            [dict(slot.geometry) if slot.geometry is not None else {}
             for slot in dom_slots]
            if dom_slots is not None
            else [
                {
                    "index": int(match.group(1)),
                    "left": float(match.group(2)),
                    "top": float(match.group(3)),
                    "width": float(match.group(4)),
                    "height": float(match.group(5)),
                }
                for match in matches
            ]
        )
    except (TypeError, ValueError):
        geometry = []
    cell_width = cell.rect[2] - cell.rect[0]
    cell_height = cell.rect[3] - cell.rect[1]
    finite_container = (
        all(math.isfinite(value) for value in cell.rect)
        and cell_width > 0
        and cell_height > 0
    )
    geometry_valid = (
        finite_container
        and len(geometry) == physical
        and all(
            all(name in item for name in ("left", "top", "width", "height"))
            and all(math.isfinite(float(item[name]))
                for name in ("left", "top", "width", "height"))
            and float(item["width"]) > 0
            and float(item["height"]) > 0
            and float(item["left"]) >= -EMITTED_GEOMETRY_EPS_PT
            and float(item["left"]) + float(item["width"])
            <= cell_width + EMITTED_GEOMETRY_EPS_PT
            and max(0.0, float(item["top"]))
            < min(cell_height,
                  float(item["top"]) + float(item["height"]))
            for item in geometry
        )
    )
    if geometry_valid:
        geometry_valid = all(
                abs(
                    float(right["left"])
                    - (float(left["left"]) + float(left["width"]))
                ) <= EMITTED_GEOMETRY_EPS_PT
                and abs(
                    max(0.0, float(right["top"]))
                    - max(0.0, float(left["top"]))
                ) <= EMITTED_GEOMETRY_EPS_PT
                and abs(
                    min(cell_height,
                        float(right["top"]) + float(right["height"]))
                    - min(cell_height,
                          float(left["top"]) + float(left["height"]))
                ) <= EMITTED_GEOMETRY_EPS_PT
                for left, right in zip(geometry, geometry[1:])
            )
    if not geometry_valid:
        return {
            "valid": False,
            "state": "invalid-slot-geometry",
            "slots": physical,
            "physical_slots": physical,
            "declared_slots": declared,
            "occurrences": occurrences,
            "slot_indexes": indexes,
            "input_slot_indexes": input_indexes,
            "slot_geometry": geometry,
            "source_filled_slots": source_filled,
            "reason": (
                "emitted slots must be finite positive, vertically present "
                "after clipping, and form one ordered contiguous x partition "
                "within their comb container"),
        }
    if declared is not None and declared != physical:
        return {
            "valid": False,
            "state": "declared-physical-slot-mismatch",
            "slots": physical,
            "physical_slots": physical,
            "declared_slots": declared,
            "occurrences": occurrences,
            "slot_indexes": indexes,
            "input_slot_indexes": input_indexes,
            "slot_geometry": geometry,
            "source_filled_slots": source_filled,
            "reason": (
                f"emitted comb declares {declared} slot(s) but renders "
                f"{physical} physical slots"),
        }
    return {
        "valid": True,
        "state": "physical-slots",
        "slots": physical,
        "physical_slots": physical,
        "declared_slots": declared,
        "occurrences": occurrences,
        "slot_indexes": indexes,
        "input_slot_indexes": input_indexes,
        "slot_geometry": geometry,
        "source_filled_slots": source_filled,
        "reason": "",
    }


def _position_evidence(
        actual: Sequence[float] | None,
        expected: Sequence[float] | None,
        *,
        comparable: bool,
        unavailable_reason: str | None = None,
        tolerance_pt: float = EMITTED_GEOMETRY_EPS_PT,
        ) -> dict[str, Any]:
    """Publish one fixed-tolerance physical-edge comparison."""
    actual_values = (
        [float(value) for value in actual] if actual is not None else None)
    expected_values = (
        [float(value) for value in expected] if expected is not None else None)
    evidence: dict[str, Any] = {
        "comparable": comparable,
        "tolerance_pt": tolerance_pt,
        "actual_internal_edges_x": (
            [round(value, 6) for value in actual_values]
            if actual_values is not None else None),
        "expected_internal_edges_x": (
            [round(value, 6) for value in expected_values]
            if expected_values is not None else None),
    }
    if not comparable:
        evidence.update({
            "count_matches": None,
            "deltas_pt": None,
            "matches": None,
            "unavailable_reason": unavailable_reason,
        })
        return evidence
    if actual_values is None or expected_values is None:
        evidence.update({
            "count_matches": False,
            "deltas_pt": None,
            "matches": False,
            "unavailable_reason": (
                unavailable_reason
                or "required physical-edge geometry is absent"),
        })
        return evidence
    count_matches = len(actual_values) == len(expected_values)
    deltas = (
        [actual_value - expected_value
         for actual_value, expected_value
         in zip(actual_values, expected_values)]
        if count_matches else None
    )
    evidence.update({
        "count_matches": count_matches,
        "deltas_pt": (
            [round(delta, 6) for delta in deltas]
            if deltas is not None else None),
        "matches": (
            count_matches
            and all(abs(delta) <= tolerance_pt
                    for delta in deltas or ())
        ),
    })
    return evidence


def _emitted_slot_edges(
        cell: Cell | None, emission: dict[str, Any],
        ) -> list[float] | None:
    """Absolute x positions of every physical slot boundary, including rails."""
    geometry = emission.get("slot_geometry")
    if cell is None or not isinstance(geometry, list) or not geometry:
        return None
    try:
        return [
            cell.rect[0] + float(geometry[0]["left"]),
            *(
                cell.rect[0] + float(slot["left"]) + float(slot["width"])
                for slot in geometry
            ),
        ]
    except (KeyError, TypeError, ValueError):
        return None


def _emitted_internal_edges(
        cell: Cell | None, emission: dict[str, Any],
        ) -> list[float] | None:
    edges = _emitted_slot_edges(cell, emission)
    return edges[1:-1] if edges is not None else None


def _outer_position_evidence(
        actual: Sequence[float] | None,
        expected: Sequence[float] | None,
        *,
        comparable: bool,
        unavailable_reason: str | None = None,
        tolerance_pt: float = EMITTED_GEOMETRY_EPS_PT,
        ) -> dict[str, Any]:
    evidence = _position_evidence(
        actual,
        expected,
        comparable=comparable,
        unavailable_reason=unavailable_reason,
        tolerance_pt=tolerance_pt,
    )
    evidence["actual_outer_edges_x"] = evidence.pop(
        "actual_internal_edges_x")
    evidence["expected_outer_edges_x"] = evidence.pop(
        "expected_internal_edges_x")
    return evidence


def _comb_input_insets(
        cell: Cell) -> dict[int, tuple[float, float, float, float] | None]:
    """Per slot number, the inset the input inside it declares for ITSELF.

    A comb input carries `inset:0` today, so the slot div and the box a
    taxpayer types in are the same rectangle -- but nothing enforced that, and
    the difference is not cosmetic: an input inset inside its slot is exactly
    the shape a producer-side fix takes, and a judge that scores the SLOT can
    neither see such a fix nor catch a producer that widened the input back out
    over printed ink.

    `None` means "read this slot's own rectangle", and it is returned for every
    case this cannot resolve unambiguously -- no inset declared, or more than
    one editable input attributed to one slot number. That is the fail-closed
    direction on purpose: the slot is the larger rectangle, so an unresolvable
    inset can only make an overlap MORE visible, never less.
    """
    insets: dict[int, list[tuple[float, float, float, float] | None]] = {}
    if cell.dom_record is not None:
        positions = {
            position: slot.index
            for position, slot in enumerate(cell.dom_record.slots)
        }
        for input_record in cell.dom_record.inputs:
            if input_record.owning_slot is None or not input_record.editable:
                continue
            number = positions.get(input_record.owning_slot[1])
            if number is None:
                continue
            insets.setdefault(number, []).append(input_record.inset)
    else:
        for match in SLOT_RE.finditer(cell.inner):
            number = int(match.group(1))
            for input_match in INPUT_RE.finditer(match.group(6)):
                found = INSET_RE.search(input_match.group(1))
                insets.setdefault(number, []).append(
                    tuple(float(found.group(index)) for index in (1, 2, 3, 4))
                    if found is not None else None)
    return {
        number: values[0] if len(values) == 1 else None
        for number, values in insets.items()
    }


def input_boxes(cell: Cell) -> list[Rect]:
    """Every editable box this cell renders, in page coordinates.

    Comb inputs live inside their slot div, inset by whatever they declare for
    themselves; a plain text input fills the cell minus its declared inset.
    Both are absolute numbers in the markup, so no layout pass is needed to know
    where a taxpayer can type. A comb slot is clipped to its parent cell because
    `.f` uses `overflow:hidden`; geometry outside that box cannot paint typed
    text over the page.
    """
    left, top, right, bottom = cell.rect
    out: list[Rect] = []
    slot_insets = _comb_input_insets(cell)
    for number, box, has_input in slot_boxes(cell):
        if has_input:
            inset = slot_insets.get(number)
            if inset is not None:
                inset_top, inset_right, inset_bottom, inset_left = inset
                box = (box[0] + inset_left, box[1] + inset_top,
                       box[2] - inset_right, box[3] - inset_bottom)
            clipped = (max(left, box[0]), max(top, box[1]),
                       min(right, box[2]), min(bottom, box[3]))
            if clipped[2] > clipped[0] and clipped[3] > clipped[1]:
                out.append(clipped)
    if cell.dom_record is not None:
        for input_record in cell.dom_record.inputs:
            if input_record.owning_slot is not None or not input_record.editable:
                continue
            if input_record.inset is not None:
                t, r, b, l = input_record.inset
                out.append((left + l, top + t, right - r, bottom - b))
            else:
                out.append(cell.rect)
        return out
    for match in INPUT_RE.finditer(cell.inner):
        attrs = match.group(1)
        if "data-slot-index" in attrs:
            continue    # already counted via its slot
        inset = INSET_RE.search(attrs)
        if inset:
            t, r, b, l = (float(inset.group(i)) for i in (1, 2, 3, 4))
            out.append((left + l, top + t, right - r, bottom - b))
        else:
            out.append(cell.rect)
    return out


def parse_run_styles(html: str) -> dict[tuple[int, int], str]:
    return {(int(m.group(1)), int(m.group(2))): m.group(3) for m in RUN_RE.finditer(html)}


def page_chunks(html: str) -> dict[int, str]:
    """The markup of each `.page` div, keyed by its 1-based page number."""
    parts = PAGE_SPLIT_RE.split(html)
    return {int(parts[i]): parts[i + 1] for i in range(1, len(parts), 2)}


def attrs_of(text: str) -> dict[str, str]:
    return {k: v for k, v in ATTR_RE.findall(text)}


# --------------------------------------------------------------------------
# source-PDF oracles
#
# Everything below reads the pinned official PDF, so it answers "what does the
# form print" rather than "what did we decide the form prints".
# --------------------------------------------------------------------------


@functools.lru_cache(maxsize=1)
def source_index(root: str) -> dict[str, tuple[pathlib.Path, ...]]:
    base = pathlib.Path(root).expanduser()
    index: dict[str, list[pathlib.Path]] = collections.defaultdict(list)
    if base.is_dir():
        for pdf in sorted(base.rglob("*.pdf")):
            index[pdf.name].append(pdf)
    return {name: tuple(paths) for name, paths in index.items()}


def resolve_source_payload(
        ir: dict, root: str,
        ) -> tuple[pathlib.Path, bytes] | None:
    """Read the pinned PDF once and return its confirmed immutable payload.

    The IR records only a basename (`external:bir2550m.pdf`) and the corpus has
    duplicate folders offering the same name, so the recorded sha256 is what
    decides. A near-miss is not accepted: an assertion scored against the wrong
    revision is worse than one that reports it could not be scored. The caller
    retains these exact bytes; no later assertion reopens the mutable path.
    """
    source = ir.get("source") or {}
    name = str(source.get("file", "")).split(":", 1)[-1]
    wanted = source.get("sha256")
    for candidate in source_index(root).get(name, ()):
        try:
            payload = _stable_read(candidate)
        except RuntimeError:
            continue
        if hashlib.sha256(payload).hexdigest() == wanted:
            return candidate, payload
    return None


@dataclasses.dataclass(frozen=True)
class VectorPaint:
    """One axis-aligned region belonging to one source painting operation.

    Several regions may share an operation.  They are composited once, not once
    per region: an even-odd compound fill can cancel overlapping rectangles,
    and a translucent compound fill applies its opacity once over their union.
    """

    x0: float
    y0: float
    x1: float
    y1: float
    tone: float
    opacity: float
    order: int
    kind: str
    operation: int = -1
    fill_rule: str = "union"
    winding: int = 1

    def covers(self, x: float, y: float) -> bool:
        return self.x0 <= x <= self.x1 and self.y0 <= y <= self.y1


@dataclasses.dataclass(frozen=True)
class UnsupportedVectorPaint:
    """A source paint whose final topology cannot be represented exactly.

    `exact_regions` is populated only for a "chromatic vector fill" whose OWN
    geometry (its `re`/`qu` items) parses exactly rectilinear -- the same
    parse the grey-fill path already runs, just gated on colour rather than
    shape. When present, `tone` is that fill's measured luminance and
    `fill_rule` is its own even-odd/nonzero rule, letting a caller that has
    independently decided a chromatic colour is not disqualifying reconstruct
    the exact regions this paint would have contributed had it been grey.
    Never populated for any other reason: a genuinely non-rectilinear or
    unbounded fill has no exact regions to offer.
    """

    rect: Rect
    order: int
    reason: str
    tone: float | None = None
    opacity: float | None = None
    trace_rects: tuple[Rect, ...] = ()
    exact_regions: tuple[tuple[Rect, int], ...] = ()
    fill_rule: str = "union"


@dataclasses.dataclass(frozen=True)
class VectorPage:
    paints: tuple[VectorPaint, ...]
    unsupported: tuple[UnsupportedVectorPaint, ...]


@dataclasses.dataclass(frozen=True)
class _SourceBaselineSpan:
    """One untrimmed, final-visible horizontal source-paint lineage."""

    y: float
    y0: float
    y1: float
    left: float
    right: float
    operations: tuple[tuple[int, int], ...]
    segments: tuple[tuple[float, float, float, float], ...] = ()


class CombTopologyError(ValueError):
    """Fail-closed topology error carrying machine-checkable source evidence."""

    def __init__(self, message: str, evidence: dict[str, Any]) -> None:
        super().__init__(message)
        self.evidence = evidence


COMB_SUBJECT_KEY_RE = re.compile(
    r"p(?P<page>\d+)@"
    r"(?P<x0>-?(?:\d+(?:\.\d*)?|\.\d+)),"
    r"(?P<y0>-?(?:\d+(?:\.\d*)?|\.\d+)),"
    r"(?P<x1>-?(?:\d+(?:\.\d*)?|\.\d+)),"
    r"(?P<y1>-?(?:\d+(?:\.\d*)?|\.\d+))\Z"
)


def _canonical_decimal(value: Any) -> Decimal | None:
    """Return one exact finite JSON-number identity without float coercion."""
    if isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        return value if value.is_finite() else None
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            return None
        # `str` is Python's shortest round-tripping representation of the
        # parsed float.  Comparing it with the Decimal parsed from retained
        # bytes fails closed when json.loads already rounded a longer token.
        return Decimal(str(value))
    return None


def _decimal_identity(value: Decimal) -> str:
    """Stable, exact, non-exponent evidence for one Decimal identity."""
    rendered = format(value, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    if rendered in {"", "-0"}:
        return "0"
    return rendered


def _exact_json_equal(left: Any, right: Any) -> bool:
    """Recursively compare retained JSON without lossy numeric conversion."""
    left_number = _canonical_decimal(left)
    right_number = _canonical_decimal(right)
    if left_number is not None or right_number is not None:
        return (left_number is not None
                and right_number is not None
                and left_number == right_number)
    if isinstance(left, dict) or isinstance(right, dict):
        return (
            isinstance(left, dict)
            and isinstance(right, dict)
            and set(left) == set(right)
            and all(_exact_json_equal(left[key], right[key]) for key in left)
        )
    if isinstance(left, (list, tuple)) or isinstance(right, (list, tuple)):
        return (
            isinstance(left, (list, tuple))
            and isinstance(right, (list, tuple))
            and len(left) == len(right)
            and all(_exact_json_equal(a, b) for a, b in zip(left, right))
        )
    return type(left) is type(right) and left == right


@dataclasses.dataclass(frozen=True)
class CombOwnerCertificate:
    """Hash-bound reviewed identity for one comb owner, never its topology."""

    page: int
    cell_id: str
    legacy_cell_id: str
    subject_key: str
    bbox: tuple[Decimal, Decimal, Decimal, Decimal]
    state: str
    layout_sha256: str

    def evidence(self) -> dict[str, Any]:
        return {
            "criterion": "exact-reviewed-layout-comb-subject-owner-v1",
            "valid": True,
            "layout_sha256": self.layout_sha256,
            "page": self.page,
            "cell_id": self.cell_id,
            "legacy_cell_id": self.legacy_cell_id,
            "subject_key": self.subject_key,
            "legacy_bbox": [
                _decimal_identity(value) for value in self.bbox
            ],
            "bbox_number_format": "canonical-decimal-string-v1",
            "state": self.state,
            "supplies_topology": False,
        }

    def matches(self, page_index: int, cell: dict[str, Any]) -> bool:
        try:
            cell_id = cell["id"]
            raw_bbox = tuple(
                cell[key] for key in ("x0", "y0", "x1", "y1"))
        except KeyError:
            return False
        canonical_id = (
            CANONICAL_CELL_ID_RE.fullmatch(cell_id)
            if isinstance(cell_id, str) else None
        )
        return (
            page_index == self.page
            and canonical_id is not None
            and int(canonical_id.group(1)) == page_index
            and cell_id == self.cell_id
            and cell.get("subject_key") == self.subject_key
            and _exact_number_vector(raw_bbox, self.bbox)
        )


@dataclasses.dataclass(frozen=True)
class CombOwnerRegistry:
    """Exact-byte layout binding and its identity-only owner certificates."""

    certificates: dict[tuple[int, str], CombOwnerCertificate]
    errors: dict[tuple[int, str], str]
    binding_error: str | None = None

    def resolve(
            self, page_index: int, cell: dict[str, Any],
            ) -> tuple[CombOwnerCertificate | None, str | None]:
        if self.binding_error is not None:
            return None, self.binding_error
        if isinstance(page_index, bool) or not isinstance(page_index, int):
            return None, "comb owner page index is not an integer"
        cell_id = cell.get("id")
        if not isinstance(cell_id, str):
            return None, "comb owner cell has no string id"
        key = (page_index, cell_id)
        certificate = self.certificates.get(key)
        if certificate is None:
            return None, self.errors.get(
                key,
                "no exact unique reviewed comb_subject owns this layout cell",
            )
        if not certificate.matches(page_index, cell):
            return None, (
                "reviewed comb_subject certificate is stale for the active "
                "layout cell identity or bbox"
            )
        return certificate, None


def _exact_number_vector(left: Any, right: Any) -> bool:
    """Numeric JSON equality with no geometry tolerance of any kind."""
    if (not isinstance(left, (list, tuple))
            or not isinstance(right, (list, tuple))
            or len(left) != len(right)):
        return False
    pairs = [
        (_canonical_decimal(a), _canonical_decimal(b))
        for a, b in zip(left, right)
    ]
    return all(a is not None and b is not None and a == b
               for a, b in pairs)


def reviewed_comb_owner_registry(bundle: Any) -> CombOwnerRegistry:
    """Validate the hash-bound layout ledger without reading comb topology.

    The exact retained layout bytes are the authority.  The parsed layout used
    elsewhere in the assertion must still equal those bytes, and the digest
    must be the digest recorded by the input snapshot.  Only identity, state,
    and rectangle fields are inspected below: `cells`, `comb`, `divider_x`,
    `slot_x`, band y, and grey are intentionally outside this certificate.
    """
    payload = getattr(bundle, "layout_payload", None)
    expected_sha = getattr(bundle, "layout_sha256", None)
    parsed_layout = getattr(bundle, "layout", None)
    if not isinstance(payload, bytes) or not isinstance(expected_sha, str):
        return CombOwnerRegistry(
            {}, {},
            "layout comb_subject ownership is not bound to retained bytes",
        )
    actual_sha = hashlib.sha256(payload).hexdigest()
    if expected_sha != actual_sha:
        return CombOwnerRegistry(
            {}, {},
            "retained layout bytes do not match their recorded SHA-256",
        )
    try:
        retained_layout = json.loads(
            payload.decode("utf-8"), parse_float=Decimal)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return CombOwnerRegistry(
            {}, {}, "retained layout bytes are not valid UTF-8 JSON")
    if not _exact_json_equal(retained_layout, parsed_layout):
        return CombOwnerRegistry(
            {}, {},
            "parsed layout is stale relative to its retained hash-bound bytes",
        )
    pages = retained_layout.get("pages") if isinstance(retained_layout, dict) else None
    if not isinstance(pages, list) or not pages:
        return CombOwnerRegistry(
            {}, {}, "hash-bound layout has no exhaustive page list")

    layout_cells: dict[tuple[int, str], dict[str, Any]] = {}
    layout_cells_by_subject: dict[tuple[int, str], dict[str, Any]] = {}
    layout_cell_order: dict[tuple[int, str], int] = {}
    comb_cells: set[tuple[int, str]] = set()
    active_subjects: dict[tuple[int, str], dict[str, Any]] = {}
    cell_ids: set[str] = set()
    cell_subject_keys: set[str] = set()
    subject_cell_ids: set[str] = set()
    subject_keys: set[str] = set()
    legacy_cell_ids: set[str] = set()
    retained_partition_cells: set[tuple[int, str]] = set()
    retained_partition_subjects: set[tuple[int, str]] = set()

    def fail(reason: str) -> CombOwnerRegistry:
        return CombOwnerRegistry({}, {}, reason)

    def identity_bbox(
            subject_key: Any,
            page_index: int,
            bbox: Any,
            label: str,
            ) -> tuple[
                Decimal, Decimal, Decimal, Decimal
            ] | CombOwnerRegistry:
        if not _exact_number_vector(bbox, bbox) or len(bbox) != 4:
            return fail(f"{label} has no exact four-number bbox")
        canonical = tuple(_canonical_decimal(value) for value in bbox)
        if any(value is None for value in canonical):
            return fail(f"{label} has no exact four-number bbox")
        values = canonical
        if values[2] <= values[0] or values[3] <= values[1]:
            return fail(f"{label} bbox has no positive area")
        match = (
            COMB_SUBJECT_KEY_RE.fullmatch(subject_key)
            if isinstance(subject_key, str) else None
        )
        if match is None:
            return fail(f"{label} has a malformed subject_key")
        encoded = [
            Decimal(match.group(name))
            for name in ("x0", "y0", "x1", "y1")
        ]
        if (int(match.group("page")) != page_index
                or not _exact_number_vector(encoded, bbox)):
            return fail(f"{label} subject_key does not encode its exact bbox")
        return values  # type: ignore[return-value]

    # First bind the complete ordered page/cell registry. Subject mappings are
    # validated only after every reverse cell identity is available.
    for expected_page_index, page in enumerate(pages, 1):
        page_value = page.get("index") if isinstance(page, dict) else None
        if (not isinstance(page, dict)
                or isinstance(page_value, bool)
                or not isinstance(page_value, int)
                or page_value != expected_page_index):
            return fail(
                "hash-bound layout pages are not exhaustive and ordered "
                "from index 1")
        page_index = page_value
        raw_cells = page.get("cells")
        raw_subjects = page.get("comb_subjects")
        if not isinstance(raw_cells, list):
            return fail(f"layout page {page_index} has no cell list")
        if not isinstance(raw_subjects, list):
            return fail(
                f"layout page {page_index} has no reviewed comb_subject ledger")
        for cell_order, cell in enumerate(raw_cells):
            if not isinstance(cell, dict) or not isinstance(cell.get("id"), str):
                return fail(
                    f"layout page {page_index} contains a malformed cell")
            cell_id = cell["id"]
            canonical_id = CANONICAL_CELL_ID_RE.fullmatch(cell_id)
            if canonical_id is None or int(canonical_id.group(1)) != page_index:
                return fail(
                    f"layout cell {cell_id} does not identify page {page_index}")
            key = (page_index, cell_id)
            subject_key = cell.get("subject_key")
            cell_bbox = [
                cell.get(name) for name in ("x0", "y0", "x1", "y1")
            ]
            bbox_or_error = identity_bbox(
                subject_key, page_index, cell_bbox,
                f"layout cell {cell_id}")
            if isinstance(bbox_or_error, CombOwnerRegistry):
                return bbox_or_error
            if (key in layout_cells or cell_id in cell_ids
                    or subject_key in cell_subject_keys):
                return fail(
                    "hash-bound layout contains duplicate cell identity")
            layout_cells[key] = cell
            layout_cells_by_subject[(page_index, subject_key)] = cell
            layout_cell_order[key] = cell_order
            cell_ids.add(cell_id)
            cell_subject_keys.add(subject_key)
            comb_value = cell.get("comb")
            if comb_value is not None:
                if not isinstance(comb_value, dict):
                    return fail(
                        f"layout cell {cell_id} has a malformed comb marker")
                comb_cells.add(key)

    # Then validate every subject, including retained/suppressed records. One
    # malformed non-active record invalidates the complete registry; otherwise
    # a corrupt ledger tail could still certify earlier active cells.
    for page in pages:
        page_index = page["index"]
        raw_subjects = page["comb_subjects"]
        for subject in raw_subjects:
            if not isinstance(subject, dict):
                return fail(
                    f"layout page {page_index} contains a malformed comb_subject")
            state = subject.get("state")
            if state not in (*COMB_OWNER_REVIEWED_STATES,
                             *COMB_SUPPRESSED_STATES):
                return fail(
                    f"layout page {page_index} comb_subject has unknown state")
            cell_id = subject.get("cell_id")
            subject_key = subject.get("subject_key")
            legacy_cell_id = subject.get("legacy_cell_id")
            legacy_bbox = subject.get("legacy_bbox")
            bbox_or_error = identity_bbox(
                subject_key, page_index, legacy_bbox,
                f"layout page {page_index} comb_subject")
            if isinstance(bbox_or_error, CombOwnerRegistry):
                return bbox_or_error
            if not isinstance(legacy_cell_id, str):
                return fail(
                    f"layout page {page_index} comb_subject has no legacy id")
            legacy_canonical = CANONICAL_CELL_ID_RE.fullmatch(legacy_cell_id)
            if (legacy_canonical is None
                    or int(legacy_canonical.group(1)) != page_index):
                return fail(
                    f"comb_subject legacy id does not identify page {page_index}")
            if (subject_key in subject_keys
                    or legacy_cell_id in legacy_cell_ids):
                return fail(
                    "hash-bound layout contains duplicate comb_subject identity")
            subject_keys.add(subject_key)
            legacy_cell_ids.add(legacy_cell_id)

            if state in COMB_SUPPRESSED_STATES:
                composite = state == "active_composite"
                subject_key_set = set(subject)
                allowed = set(RETAINED_COMB_SUBJECT_KEYS)
                optional = set(RETAINED_COMB_SUBJECT_OPTIONAL_KEYS)
                if composite:
                    # The certificate is REQUIRED, not optional: it is the
                    # only thing separating a composite from a retained
                    # subject that simply stopped blocking, and that
                    # difference must never be assertable by omission.
                    allowed = allowed | {"transition_certificate"}
                if (not allowed <= subject_key_set
                        or subject_key_set - allowed - optional):
                    return fail(
                        f"{state} comb_subject schema is malformed")
                if composite:
                    certificate = subject.get("transition_certificate")
                    if (not isinstance(certificate, dict)
                            or set(certificate)
                            != COMPOSITE_TRANSITION_CERTIFICATE_KEYS
                            or certificate.get("transition")
                            != "active_composite"
                            or not isinstance(certificate.get("reviewer"), str)
                            or not certificate.get("reviewer")
                            or not isinstance(certificate.get("date"), str)
                            or not certificate.get("date")
                            or not isinstance(
                                certificate.get("suppression_criterion"), str)
                            or not certificate.get("suppression_criterion")):
                        return fail(
                            "active_composite transition certificate is "
                            "malformed")
                if (cell_id is not None
                        or subject.get("emission") != "suppressed"
                        or subject.get("requires_independent_evidence") is not True
                        or subject.get("blocks_gate") is not (not composite)
                        or tuple(subject.get("permitted_transitions") or ())
                        != RETAINED_COMB_TRANSITIONS
                        or not isinstance(subject.get("legacy_comb"), dict)):
                    return fail(
                        f"{state} suppression/blocking/transition "
                        "evidence is incomplete")
                reason_codes_value = subject.get("reason_codes")
                if (not isinstance(reason_codes_value, list)
                        or tuple(reason_codes_value) not in {
                            RETAINED_PARTITION_REASON_CODES,
                            *RETAINED_IDENTITY_REASON_CODES,
                        }):
                    return fail(
                        f"{state} suppression reason evidence is "
                        "malformed")
                mapped_ids = subject.get("mapped_partition_cell_ids")
                mapped_keys = subject.get("mapped_partition_subject_keys")
                replacements = subject.get(
                    "erased_edge_replacement_candidates")
                if (not isinstance(mapped_ids, list)
                        or not isinstance(mapped_keys, list)
                        or len(mapped_ids) != len(mapped_keys)
                        or (not mapped_ids and not replacements)
                        or any(not isinstance(value, str)
                               for value in (*mapped_ids, *mapped_keys))
                        or len(mapped_ids) != len(set(mapped_ids))
                        or len(mapped_keys) != len(set(mapped_keys))):
                    return fail(
                        "retained_unresolved partition mapping is malformed")
                mapped_orders: list[int] = []
                for mapped_id, mapped_subject_key in zip(
                        mapped_ids, mapped_keys):
                    mapped_id_match = CANONICAL_CELL_ID_RE.fullmatch(mapped_id)
                    mapped_cell = layout_cells.get((page_index, mapped_id))
                    reverse_cell = layout_cells_by_subject.get(
                        (page_index, mapped_subject_key))
                    if (mapped_id_match is None
                            or int(mapped_id_match.group(1)) != page_index
                            or mapped_cell is None
                            or reverse_cell is not mapped_cell
                            or mapped_cell.get("subject_key")
                            != mapped_subject_key):
                        return fail(
                            "retained_unresolved partition mapping target or "
                            "reverse subject_key mapping is stale")
                    mapped_cell_key = (page_index, mapped_id)
                    mapped_subject_identity = (
                        page_index, mapped_subject_key)
                    if (mapped_cell_key in retained_partition_cells
                            or mapped_subject_identity
                            in retained_partition_subjects):
                        return fail(
                            "retained_unresolved partition mapping target is "
                            "owned more than once")
                    retained_partition_cells.add(mapped_cell_key)
                    retained_partition_subjects.add(mapped_subject_identity)
                    mapped_orders.append(layout_cell_order[mapped_cell_key])
                if mapped_orders != sorted(mapped_orders):
                    return fail(
                        "retained_unresolved partition mapping is not in "
                        "layout cell order")
                retained_cell = layout_cells_by_subject.get(
                    (page_index, subject_key))
                if (retained_cell is not None
                        and retained_cell.get("comb") is not None):
                    return fail(
                        "retained_unresolved subject still owns an active comb")
                if (tuple(reason_codes_value)
                        in RETAINED_IDENTITY_REASON_CODES):
                    if (mapped_ids != [legacy_cell_id]
                            or mapped_keys != [subject_key]
                            or retained_cell is None
                            or not _exact_number_vector(
                                legacy_bbox,
                                [retained_cell.get(name) for name in (
                                    "x0", "y0", "x1", "y1")])):
                        return fail(
                            "retained_unresolved identity mapping is stale")
                elif retained_cell is not None:
                    return fail(
                        "retained_unresolved partition subject still has a "
                        "layout owner")
                if replacements is not None:
                    if (not isinstance(replacements, list)
                            or not replacements
                            or any(not isinstance(item, dict)
                                   for item in replacements)):
                        return fail(
                            "retained_unresolved replacement identity evidence "
                            "is malformed")
                    for replacement in replacements:
                        candidate_id = replacement.get("cell_id")
                        candidate_key = replacement.get("new_subject_key")
                        candidate_bbox = replacement.get("new_bbox")
                        candidate_cell = (
                            layout_cells.get((page_index, candidate_id))
                            if isinstance(candidate_id, str) else None
                        )
                        if (not isinstance(candidate_key, str)
                                or replacement.get("old_subject_key")
                                != subject_key
                                or not _exact_number_vector(
                                    replacement.get("old_bbox"), legacy_bbox)
                                or replacement.get("blocks_gate") is not True
                                or not isinstance(
                                    replacement.get("activation_blockers"), list)
                                or not replacement.get("activation_blockers")
                                or any(not isinstance(value, str) for value in
                                       replacement["activation_blockers"])
                                or candidate_cell is None
                                or candidate_cell.get("subject_key")
                                != candidate_key
                                or not _exact_number_vector(
                                    candidate_bbox,
                                    [candidate_cell.get(name) for name in (
                                        "x0", "y0", "x1", "y1")])):
                            return fail(
                                "retained_unresolved replacement identity or "
                                "blocking evidence is stale")
                continue
            if not isinstance(cell_id, str):
                return fail("active comb_subject has no string cell_id")
            canonical_id = CANONICAL_CELL_ID_RE.fullmatch(cell_id)
            if canonical_id is None or int(canonical_id.group(1)) != page_index:
                return fail(
                    f"active comb_subject {cell_id} does not identify its page")
            key = (page_index, cell_id)
            if key in active_subjects or cell_id in subject_cell_ids:
                return fail(
                    "active comb_subject cell mapping is not unique")
            if subject.get("mapped_partition_cell_ids") != [cell_id]:
                return fail(
                    "active comb_subject is not a one-to-one cell mapping")
            reason_codes = subject.get("reason_codes")
            if (not isinstance(reason_codes, list)
                    or any(not isinstance(reason, str)
                           for reason in reason_codes)
                    or len(reason_codes) != len(set(reason_codes))
                    or (state == "active_resolved"
                        and (reason_codes or subject.get("blocks_gate") is not False))
                    or (state == "active_unresolved"
                        and (not reason_codes
                             or subject.get("blocks_gate") is not True))):
                return fail(
                    "active comb_subject review/blocking evidence is malformed")
            active_subjects[key] = subject
            subject_cell_ids.add(cell_id)

    orphan_active = sorted(set(active_subjects) - set(layout_cells))
    if orphan_active:
        page_index, cell_id = orphan_active[0]
        return fail(
            f"active comb_subject {cell_id} on page {page_index} is orphaned")
    active_noncomb = sorted(set(active_subjects) - comb_cells)
    if active_noncomb:
        page_index, cell_id = active_noncomb[0]
        return fail(
            f"active comb_subject {cell_id} on page {page_index} owns no comb cell")
    missing_active = sorted(comb_cells - set(active_subjects))
    if missing_active:
        page_index, cell_id = missing_active[0]
        return fail(
            f"comb cell {cell_id} on page {page_index} has no reviewed active "
            "comb_subject")

    certificates: dict[tuple[int, str], CombOwnerCertificate] = {}
    for key in sorted(comb_cells):
        page_index, cell_id = key
        cell = layout_cells[key]
        subject = active_subjects[key]
        state = subject.get("state")
        subject_key = subject.get("subject_key")
        cell_subject_key = cell.get("subject_key")
        legacy_cell_id = subject.get("legacy_cell_id")
        legacy_bbox = subject.get("legacy_bbox")
        cell_bbox = [cell.get(name) for name in ("x0", "y0", "x1", "y1")]
        if (state not in COMB_OWNER_REVIEWED_STATES
                or subject_key != cell_subject_key
                or legacy_cell_id != cell_id
                or not _exact_number_vector(legacy_bbox, cell_bbox)):
            return fail(
                f"active comb_subject {cell_id} identity/bbox is stale")
        bbox_values = tuple(_canonical_decimal(value) for value in legacy_bbox)
        if any(value is None for value in bbox_values):
            return fail(f"active comb_subject {cell_id} bbox is not exact")
        certificates[key] = CombOwnerCertificate(
            page=page_index,
            cell_id=cell_id,
            legacy_cell_id=legacy_cell_id,
            subject_key=subject_key,
            bbox=bbox_values,  # type: ignore[arg-type]
            state=state,
            layout_sha256=actual_sha,
        )
    return CombOwnerRegistry(certificates, {})


# --------------------------------------------------------------------------
# reviewed comb topology (W8) -- human review for subjects the source
# genuinely cannot settle, consulted ONLY where `printed_compartments` has
# already raised on its own evidence
# --------------------------------------------------------------------------


COMB_TOPOLOGY_REVIEW_FIELDS = (
    "compartments", "source_sha256", "page", "cell_id", "bbox",
    "reviewer", "date", "citation",
)


@dataclasses.dataclass(frozen=True)
class ReviewedCombTopology:
    """One human-reviewed printed-compartment count for one subject.

    Never a substitute for measurement, and never consulted for a subject
    `printed_compartments` can decide on its own: `resolve_reviewed_comb_topology`
    reads this only after that function has already raised for this EXACT
    (slug, page, cell_id).  It supplies a compartment COUNT only -- never
    divider positions, which stay unevaluable exactly as they do today.
    """

    compartments: int
    source_sha256: str
    page: int
    cell_id: str
    bbox: tuple[float, float, float, float]
    reviewer: str
    date: str
    citation: str

    def evidence(self) -> dict[str, Any]:
        return {
            "criterion": "reviewed-comb-topology-v1",
            "valid": True,
            "compartments": self.compartments,
            "source_sha256": self.source_sha256,
            "page": self.page,
            "cell_id": self.cell_id,
            "bbox": list(self.bbox),
            "reviewer": self.reviewer,
            "date": self.date,
            "citation": self.citation,
        }


# Keyed by (slug, page, cell_id).  Consulted only for a subject the audit has
# already decided it cannot evaluate from vector data alone -- never for a
# subject it can decide (see the sibling guard at the call site).  Follows the
# exact discipline `scripts/audit_html_form_migration.py` already uses for its
# trusted-producer registries: "a producer is registered only after the user
# reviews it".  SHIPPED EMPTY.  An entry lands in its own reviewed,
# evidence-carrying commit once the user has confirmed the fact -- never here
# -- and an empty registry must change nothing this file already reports.
REVIEWED_COMB_TOPOLOGY: dict[tuple[str, int, str], dict[str, Any]] = {
    ("1604cf-2008", 2, "p2c73"): {
        "compartments": 2,
        "source_sha256": "877fbeee071752b2d9af72924647196e6dafa71a2412e74bc9f17897767cc2e7",
        "page": 2,
        "cell_id": "p2c73",
        "bbox": (174.48, 220.32, 269.76, 237.12),
        "reviewer": "uriah (repository owner)",
        "date": "2026-08-13",
        "citation": (
            "Reviewed against the official sheet on the 13-cell topology "
            "review sheet, panel 01: the pinned source PDF rendered at "
            "6x beside our own render of the same region, the cell outlined "
            "in red on both. Confirmed 2026-08-13."),
    },
    ("1604f-2018", 1, "p1c25"): {
        "compartments": 36,
        "source_sha256": "fc34de40dc7e6bc5f7a8cbc3feb5b170cca4bce4f0abd5b7b0dece4e9dd75c4d",
        "page": 1,
        "cell_id": "p1c25",
        "bbox": (14.64, 223.58, 591.46, 241.82),
        "reviewer": "uriah (repository owner)",
        "date": "2026-08-13",
        "citation": (
            "Reviewed against the official sheet on the 13-cell topology "
            "review sheet, panel 02: the pinned source PDF rendered at "
            "6x beside our own render of the same region, the cell outlined "
            "in red on both. Confirmed 2026-08-13."),
    },
    # RE-REVIEWED 2026-08-13 (evening): 4 -> 3. The panel-03 review asked
    # "does our render match the official sheet?" and the answer was correctly
    # yes -- our render drew a box over the caption and the sheet has a caption
    # there. It never asked how many WRITING boxes the sheet prints, which is
    # what this registry stores, so the pinned 4 counted the caption region as
    # a compartment. Re-reviewed against the ink and re-confirmed by the owner
    # from annotated 300-dpi crops. Same defect as the 2200A/C/P trio below,
    # which was withdrawn for this reason before it was ever pinned.
    ("1801-2018", 1, "p1c13"): {
        "compartments": 3,
        "source_sha256": "ec49207aab9b035d1913d41091b677d9df690e01b391ed2c2f4c34cf43a524c6",
        "page": 1,
        "cell_id": "p1c13",
        "bbox": (21.6, 136.7, 247.97, 155.54),
        "reviewer": "uriah (repository owner)",
        "date": "2026-08-13",
        "citation": (
            "Re-reviewed against the official sheet's own ink 2026-08-13 and "
            "confirmed by the owner from annotated 300-dpi crops of the "
            "pinned PDF (sha256 ec49207a..., verified against this IR). The "
            "row prints 14 black guide marks in this band and EVERY one is "
            "right of x=204.41; left of it the sheet prints no guide ink at "
            "all, only the page border at x=20.88 which runs the full 600pt "
            "height of the page. The unguided paper 21.6->204.65 is 183.05pt "
            "against the comb's own 14.16pt pitch (12.9 compartments) and "
            "holds the printed caption runs '5', 'Taxpayer Identification "
            "Number' and '(TIN)' at x 27.00-182.67. Three writing "
            "compartments; the caption region is printed matter. The tick run "
            "continues past this cell to x=446.23, so the rest of the TIN "
            "lives in neighbouring cells and no digit capacity is lost. "
            "Supersedes the panel-03 reading of 4."),
    },
    # The 2200A/C/P trio (F229). Withdrawn from the 13-panel review because the
    # panel could not settle the count, then re-reviewed against the ink and
    # confirmed by the owner 2026-08-13. All three forms print this row to the
    # same coordinates. The four gray-1.0 marks inside the caption are NOT
    # erased boxes: they are painted at paint_seq 5629-5638, BEFORE the black
    # guides at 5727+, with no rule beneath them -- checked directly. They
    # erase nothing; the bottom guide row simply never extends there.
    ("2200a-2020", 1, "p1c111"): {
        "compartments": 28,
        "source_sha256": "c294bd45da56aa641f40ed5ed22b6c7c782860e84c2da6431c3340bd73194879",
        "page": 1,
        "cell_id": "p1c111",
        "bbox": (16.32, 807.7, 595.32, 821.64),
        "reviewer": "uriah (repository owner)",
        "date": "2026-08-13",
        "citation": (
            "Re-reviewed against the official sheet's own ink 2026-08-13 and "
            "confirmed by the owner from annotated 300-dpi crops of the "
            "pinned PDF (sha256 c294bd45..., verified against this IR). The "
            "band carries 32 black guide marks and every one is right of "
            "x=189.74. The unguided paper 16.32->189.98 is 173.66pt against "
            "the comb's own 14.52pt pitch (12.0 compartments) and holds the "
            "printed caption runs '27' and 'Tax Debit Memo' at x "
            "28.92-103.82. 28 writing compartments; the caption region is "
            "printed matter, not a box."),
    },
    ("2200c-2018", 1, "p1c107"): {
        "compartments": 28,
        "source_sha256": "7b60d517ac6f3697e351aa89c124423d03dd7cac0961c4319b6507dd0ae64ce2",
        "page": 1,
        "cell_id": "p1c107",
        "bbox": (16.32, 805.54, 595.32, 821.88),
        "reviewer": "uriah (repository owner)",
        "date": "2026-08-13",
        "citation": (
            "Re-reviewed against the official sheet's own ink 2026-08-13 and "
            "confirmed by the owner. Same row and same coordinates as "
            "2200A p1c111: the four gray-1.0 caption marks sit at x 131.90, "
            "146.42, 160.82 and 175.34, the first black guide is at x=189.74, "
            "and no black guide ink exists left of it. 28 writing "
            "compartments."),
    },
    ("2200p-2020", 1, "p1c110"): {
        "compartments": 28,
        "source_sha256": "7bf29a28a93f45ae7af9ba344d4755540abd324137831d594bc623b4a0c06d2c",
        "page": 1,
        "cell_id": "p1c110",
        "bbox": (16.32, 807.94, 595.32, 821.88),
        "reviewer": "uriah (repository owner)",
        "date": "2026-08-13",
        "citation": (
            "Re-reviewed against the official sheet's own ink 2026-08-13 and "
            "confirmed by the owner. Same row and same coordinates as "
            "2200A p1c111: the four gray-1.0 caption marks sit at x 131.90, "
            "146.42, 160.82 and 175.34, the first black guide is at x=189.74, "
            "and no black guide ink exists left of it. 28 writing "
            "compartments."),
    },
    ("1801-2018", 1, "p1c31"): {
        "compartments": 11,
        "source_sha256": "ec49207aab9b035d1913d41091b677d9df690e01b391ed2c2f4c34cf43a524c6",
        "page": 1,
        "cell_id": "p1c31",
        "bbox": (21.6, 259.58, 291.65, 277.85),
        "reviewer": "uriah (repository owner)",
        "date": "2026-08-13",
        "citation": (
            "Reviewed against the official sheet on the 13-cell topology "
            "review sheet, panel 04: the pinned source PDF rendered at "
            "6x beside our own render of the same region, the cell outlined "
            "in red on both. Confirmed 2026-08-13."),
    },
    ("2000-dst-2018", 1, "p1c109"): {
        "compartments": 14,
        "source_sha256": "b18ce9d2380d216814f4410c2e132eebb02b93754f7ca0167311f368e100e79f",
        "page": 1,
        "cell_id": "p1c109",
        "bbox": (389.05, 804.58, 585.79, 823.32),
        "reviewer": "uriah (repository owner)",
        "date": "2026-08-13",
        "citation": (
            "Reviewed against the official sheet on the 13-cell topology "
            "review sheet, panel 05: the pinned source PDF rendered at "
            "6x beside our own render of the same region, the cell outlined "
            "in red on both. Confirmed 2026-08-13."),
    },
    ("2200a-2020", 1, "p1c62"): {
        "compartments": 14,
        "source_sha256": "c294bd45da56aa641f40ed5ed22b6c7c782860e84c2da6431c3340bd73194879",
        "page": 1,
        "cell_id": "p1c62",
        "bbox": (392.74, 432.89, 595.32, 448.02),
        "reviewer": "uriah (repository owner)",
        "date": "2026-08-13",
        "citation": (
            "Reviewed against the official sheet on the 13-cell topology "
            "review sheet, panel 06: the pinned source PDF rendered at "
            "6x beside our own render of the same region, the cell outlined "
            "in red on both. Confirmed 2026-08-13."),
    },
    ("2200a-2020", 1, "p1c86"): {
        "compartments": 14,
        "source_sha256": "c294bd45da56aa641f40ed5ed22b6c7c782860e84c2da6431c3340bd73194879",
        "page": 1,
        "cell_id": "p1c86",
        "bbox": (392.74, 614.71, 595.32, 629.97),
        "reviewer": "uriah (repository owner)",
        "date": "2026-08-13",
        "citation": (
            "Reviewed against the official sheet on the 13-cell topology "
            "review sheet, panel 07: the pinned source PDF rendered at "
            "6x beside our own render of the same region, the cell outlined "
            "in red on both. Confirmed 2026-08-13."),
    },
    ("2551m-2002", 1, "p1c82"): {
        "compartments": 4,
        "source_sha256": "f678be684558b8fb15a026b70a7c473f904fd07d49df64e0345fe1c0f81de71e",
        "page": 1,
        "cell_id": "p1c82",
        "bbox": (286.08, 785.07, 326.16, 804.45),
        "reviewer": "uriah (repository owner)",
        "date": "2026-08-13",
        "citation": (
            "Reviewed against the official sheet on the 13-cell topology "
            "review sheet, panel 11: the pinned source PDF rendered at "
            "6x beside our own render of the same region, the cell outlined "
            "in red on both. Confirmed 2026-08-13."),
    },
    ("2551m-2002", 2, "p2c13"): {
        "compartments": 2,
        "source_sha256": "f678be684558b8fb15a026b70a7c473f904fd07d49df64e0345fe1c0f81de71e",
        "page": 2,
        "cell_id": "p2c13",
        "bbox": (22.56, 92.64, 250.08, 104.4),
        "reviewer": "uriah (repository owner)",
        "date": "2026-08-13",
        "citation": (
            "Reviewed against the official sheet on the 13-cell topology "
            "review sheet, panel 12: the pinned source PDF rendered at "
            "6x beside our own render of the same region, the cell outlined "
            "in red on both. Confirmed 2026-08-13."),
    },
    ("2553-1999", 1, "p1c87"): {
        "compartments": 4,
        "source_sha256": "e52f96fe48aba2890078f889930744a4e13a4defe1284aa9c5292e2c702a20e5",
        "page": 1,
        "cell_id": "p1c87",
        "bbox": (284.16, 797.79, 324.24, 817.17),
        "reviewer": "uriah (repository owner)",
        "date": "2026-08-13",
        "citation": (
            "Reviewed against the official sheet on the 13-cell topology "
            "review sheet, panel 13: the pinned source PDF rendered at "
            "6x beside our own render of the same region, the cell outlined "
            "in red on both. Confirmed 2026-08-13."),
    },
}

_COMB_TOPOLOGY_SHA256_RE = re.compile(r"[0-9a-f]{64}")


def resolve_reviewed_comb_topology(
        slug: Any, page_index: int, cell: dict[str, Any], source_sha256: Any,
        ) -> tuple[ReviewedCombTopology | None, str | None]:
    """Consult REVIEWED_COMB_TOPOLOGY for one already-unevaluable subject.

    Returns (entry, None) when a valid reviewed fact applies. Returns
    (None, None) when no entry exists for this exact (slug, page, cell_id) --
    the subject stays `source-topology-unevaluable`, exactly as it does with
    an empty registry. Returns (None, reason) when an entry exists but fails
    a guard: the caller MUST publish this as an ERROR, never silently treat
    it as "no entry".
    """
    cell_id = cell.get("id")
    if not isinstance(slug, str) or not isinstance(cell_id, str):
        return None, None
    raw = REVIEWED_COMB_TOPOLOGY.get((slug, page_index, cell_id))
    if raw is None:
        return None, None
    if not isinstance(raw, dict):
        return None, (
            f"reviewed comb topology entry for {cell_id} on page "
            f"{page_index} is not a record")
    missing = sorted(
        field for field in COMB_TOPOLOGY_REVIEW_FIELDS if field not in raw)
    if missing:
        return None, (
            f"reviewed comb topology entry for {cell_id} on page "
            f"{page_index} is missing required field(s): "
            f"{', '.join(missing)}")
    compartments = raw["compartments"]
    entry_sha256 = raw["source_sha256"]
    entry_page = raw["page"]
    entry_cell_id = raw["cell_id"]
    bbox = raw["bbox"]
    reviewer = raw["reviewer"]
    date = raw["date"]
    citation = raw["citation"]
    if (isinstance(compartments, bool) or not isinstance(compartments, int)
            or compartments <= 0):
        return None, (
            "reviewed comb topology entry compartments must be a positive "
            f"integer for {cell_id} on page {page_index}")
    if (not isinstance(entry_sha256, str)
            or _COMB_TOPOLOGY_SHA256_RE.fullmatch(entry_sha256) is None):
        return None, (
            "reviewed comb topology entry source_sha256 must be a "
            f"64-character lowercase hex digest for {cell_id} on page "
            f"{page_index}")
    if isinstance(entry_page, bool) or entry_page != page_index:
        return None, (
            "reviewed comb topology entry page does not match its own "
            f"registry key for {cell_id} on page {page_index}")
    if entry_cell_id != cell_id:
        return None, (
            "reviewed comb topology entry cell_id does not match its own "
            f"registry key for {cell_id} on page {page_index}")
    if (not isinstance(bbox, (list, tuple)) or len(bbox) != 4
            or any(isinstance(value, bool)
                   or not isinstance(value, (int, float))
                   or not math.isfinite(value) for value in bbox)
            or not (bbox[2] > bbox[0] and bbox[3] > bbox[1])):
        return None, (
            "reviewed comb topology entry bbox must be four finite numbers "
            f"with positive area for {cell_id} on page {page_index}")
    try:
        cell_bbox = tuple(
            float(cell[name]) for name in ("x0", "y0", "x1", "y1"))
    except (KeyError, TypeError, ValueError):
        cell_bbox = None
    if cell_bbox is None or any(
            abs(float(entry_value) - live_value) > 1e-6
            for entry_value, live_value in zip(bbox, cell_bbox)):
        return None, (
            "reviewed comb topology entry bbox does not match the active "
            f"layout cell geometry for {cell_id} on page {page_index}")
    if (not isinstance(reviewer, str) or not reviewer.strip()
            or not isinstance(date, str) or not date.strip()
            or not isinstance(citation, str) or not citation.strip()):
        return None, (
            "reviewed comb topology entry reviewer/date/citation must be "
            f"non-empty text for {cell_id} on page {page_index}")
    if not isinstance(source_sha256, str) or entry_sha256 != source_sha256:
        return None, (
            "reviewed comb topology entry source_sha256 does not match the "
            f"current IR's source.sha256 for {cell_id} on page {page_index}")
    return ReviewedCombTopology(
        compartments=compartments,
        source_sha256=entry_sha256,
        page=entry_page,
        cell_id=entry_cell_id,
        bbox=tuple(float(value) for value in bbox),
        reviewer=reviewer,
        date=date,
        citation=citation,
    ), None


def _axis_aligned_quad_box(quad: Any) -> Rect | None:
    points = (quad.ul, quad.ur, quad.ll, quad.lr)
    xs = [float(point.x) for point in points]
    ys = [float(point.y) for point in points]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    corners = {
        (round(x0, 6), round(y0, 6)),
        (round(x0, 6), round(y1, 6)),
        (round(x1, 6), round(y0, 6)),
        (round(x1, 6), round(y1, 6)),
    }
    if {(round(x, 6), round(y, 6)) for x, y in zip(xs, ys)} != corners:
        return None
    return x0, y0, x1, y1


def _rectilinear_fill_regions(
        drawing: dict[str, Any],
        ) -> tuple[list[tuple[Rect, int]], str] | None:
    """One drawing's exact axis-aligned fill regions, or None if it has none.

    Shared by the grey-fill path (which paints these regions immediately) and
    a chromatic fill (W5 mechanism 1): a chromatic fill is refused only when
    its OWN `re`/`qu` items are not exactly rectilinear, never merely for its
    colour -- colour and geometry are independent questions, and this answers
    only the geometry one.
    """
    fill_supported = True
    fill_regions: list[tuple[Rect, int]] = []
    parts = drawing.get("items") or ()
    for item in parts:
        if item[0] == "re":
            rect = item[1]
            winding = int(item[2]) if len(item) > 2 else 1
            if winding == 0:
                fill_supported = False
            else:
                fill_regions.append((_rect_tuple(rect), winding))
        elif item[0] == "qu":
            box = _axis_aligned_quad_box(item[1])
            if box is None or len(parts) != 1:
                fill_supported = False
            else:
                fill_regions.append((box, 1))
        else:
            fill_supported = False
    if not fill_supported or not fill_regions:
        return None
    fill_rule = "evenodd" if bool(drawing.get("even_odd")) else "nonzero"
    return fill_regions, fill_rule


def _perceptual_luminance(color: Sequence[float]) -> float:
    """ITU-R BT.601 luma: the scalar brightness of a genuinely chromatic fill.

    `extract.to_gray` refuses a colour whose channel spread exceeds 1e-3
    because it is not this codebase's near-neutral BIR grey; that refusal is
    correct for the structural/decorative/knockout tone bands, which assume
    near-neutral ink. It is not a reason to treat a chromatic colour as
    unmeasurable -- every colour has a standard scalar luma, computed here
    with the same published coefficients used throughout image processing to
    collapse RGB to grayscale, not a constant invented for this file. The
    downstream tone comparisons (final-tone equality, the existing cutoffs)
    apply to the result exactly as they do to any other tone.
    """
    r, g, b = (float(channel) for channel in color[:3])
    return max(0.0, min(1.0, 0.299 * r + 0.587 * g + 0.114 * b))


def _rect_tuple(rect: Any) -> Rect:
    return tuple(float(value) for value in (rect.x0, rect.y0, rect.x1, rect.y1))


def _rect_intersection(left: Rect, right: Rect) -> Rect | None:
    rect = (max(left[0], right[0]), max(left[1], right[1]),
            min(left[2], right[2]), min(left[3], right[3]))
    return rect if rect[2] > rect[0] and rect[3] > rect[1] else None


def _rects_intersect(left: Rect, right: Rect) -> bool:
    return _rect_intersection(left, right) is not None


@dataclasses.dataclass(frozen=True)
class _DrawingContext:
    clip: Rect | None
    fully_clipped: bool
    unsupported: tuple[tuple[str, Rect], ...]


def _simple_clip_rect(item: dict[str, Any]) -> Rect | None:
    """Return an exact rectangle only when a clip really is one rectangle."""
    parts = item.get("items") or ()
    if len(parts) != 1:
        return None
    part = parts[0]
    if part[0] == "re":
        return _rect_tuple(part[1])
    if part[0] == "qu":
        return _axis_aligned_quad_box(part[1])
    return None


def _drawing_contexts(
        drawings: Sequence[dict[str, Any]],
        bboxlog: Sequence[tuple[str, Any]],
        ) -> dict[int, _DrawingContext]:
    """Resolve PyMuPDF's extended clip/group nesting for every path.

    Extended drawings are a depth-first stream.  A clip or group at level N
    owns following paths at deeper levels until another item at level N (or
    above) replaces it.  Simple rectangular scissors are applied exactly.
    Compound / curved clips and non-normal transparency groups are retained as
    unsupported evidence for every path they can affect.
    """
    stack: list[tuple[int, str, Rect | None, Rect | None, str | None]] = []
    contexts: dict[int, _DrawingContext] = {}

    for item in drawings:
        try:
            level = int(item.get("level", 0))
        except (TypeError, ValueError) as exc:
            raise ValueError("source drawing has a non-integral nesting level") from exc
        while stack and stack[-1][0] >= level:
            stack.pop()

        kind = str(item.get("type") or "")
        if kind == "clip":
            scissor_value = item.get("scissor")
            if scissor_value is None:
                raise ValueError("source clip has no bounded scissor")
            scissor = _rect_tuple(scissor_value)
            exact = _simple_clip_rect(item)
            reason = None if exact is not None else (
                "compound or non-rectilinear source clip")
            stack.append((level, kind, exact, scissor, reason))
            continue
        if kind == "group":
            rect_value = item.get("rect")
            rect = _rect_tuple(rect_value) if rect_value is not None else None
            opacity = float(item.get("opacity")
                            if item.get("opacity") is not None else 1.0)
            blend = str(item.get("blendmode") or "Normal")
            knockout = bool(item.get("knockout"))
            reason = None
            if opacity != 1.0 or blend != "Normal" or knockout:
                reason = "non-normal source transparency group"
            stack.append((level, kind, rect, rect, reason))
            continue

        if kind not in {"f", "s", "fs"}:
            raise ValueError(f"unsupported extended source drawing type {kind!r}")
        seqno = item.get("seqno")
        if not isinstance(seqno, int) or seqno < 0:
            raise ValueError("source drawing has no valid content-stream ordinal")
        if seqno in contexts:
            raise ValueError(f"duplicate source drawing ordinal {seqno}")

        drawing_rect_value = item.get("rect")
        if drawing_rect_value is None:
            raise ValueError("source drawing has no bounded rectangle")
        drawing_rect = _rect_tuple(drawing_rect_value)
        half = (
            float(item.get("width") or 2 * COMB_FALLBACK_HALFWIDTH_PT) / 2
            if kind in {"s", "fs"} else 0.0
        )
        painted_rect = (
            drawing_rect[0] - half,
            drawing_rect[1] - half,
            drawing_rect[2] + half,
            drawing_rect[3] + half,
        )
        bbox_ordinals = (seqno, seqno + 1) if kind == "fs" else (seqno,)
        for ordinal in bbox_ordinals:
            if 0 <= ordinal < len(bboxlog):
                bbox_rect = tuple(float(value) for value in bboxlog[ordinal][1])
                if bbox_rect[2] > bbox_rect[0] and bbox_rect[3] > bbox_rect[1]:
                    painted_rect = (
                        min(painted_rect[0], bbox_rect[0]),
                        min(painted_rect[1], bbox_rect[1]),
                        max(painted_rect[2], bbox_rect[2]),
                        max(painted_rect[3], bbox_rect[3]),
                    )
        clip: Rect | None = None
        unsupported: list[tuple[str, Rect]] = []
        fully_clipped = False
        for _container_level, container_kind, exact, scissor, reason in stack:
            if container_kind == "clip":
                if scissor is None:
                    raise ValueError("source clip lost its bounded scissor")
                affected = _rect_intersection(painted_rect, scissor)
                if affected is None:
                    fully_clipped = True
                    break
                if exact is None:
                    unsupported.append((
                        reason or "unsupported source clip",
                        affected,
                    ))
                    continue
                clip = exact if clip is None else _rect_intersection(clip, exact)
                if clip is None or _rect_intersection(painted_rect, clip) is None:
                    fully_clipped = True
                    break
            elif reason is not None:
                # The extended stream's nesting level, not positive-area
                # rectangle overlap, establishes group ownership. Line-path
                # rectangles can be zero-width/height, and opacity or blend is
                # applied to the nested paint regardless.
                affected = (
                    _rect_intersection(painted_rect, exact)
                    if exact is not None else None
                )
                unsupported.append((reason, affected or painted_rect))
        contexts[seqno] = _DrawingContext(
            clip=clip,
            fully_clipped=fully_clipped,
            unsupported=tuple(sorted(set(unsupported))),
        )

    return contexts


def ordered_vector_paints(page: Any) -> VectorPage:
    """The page's final-paint inputs in exact PDF content-stream order.

    `extended=True` is mandatory: default drawings retain paths that a nested
    clip removes completely.  The drawing `seqno` is the full bbox-log ordinal,
    so later text and images stay ordered relative to vector paint instead of
    disappearing from the compositor.
    """
    try:
        drawings = list(page.get_drawings(extended=True))
        bboxlog = list(page.get_bboxlog())
    except Exception as exc:
        raise ValueError(f"source paint stream is unevaluable: {exc}") from exc
    contexts = _drawing_contexts(drawings, bboxlog)
    try:
        texttrace = list(page.get_texttrace())
    except (AttributeError, RuntimeError, TypeError, ValueError):
        texttrace = []
    text_by_order: dict[
        int, list[tuple[Rect, float | None, float, float | None]]
    ] = (
        collections.defaultdict(list))
    for span in texttrace:
        seqno = span.get("seqno")
        if not isinstance(seqno, int) or seqno < 0:
            continue
        tone = extract.to_gray(span.get("color"))
        opacity = float(span.get("opacity")
                        if span.get("opacity") is not None else 1.0)
        linewidth_value = span.get("linewidth")
        linewidth = (float(linewidth_value)
                     if linewidth_value is not None else None)
        chars = span.get("chars") or ()
        for char in chars:
            if len(char) < 4:
                continue
            rect = tuple(float(value) for value in char[3])
            if rect[2] > rect[0] and rect[3] > rect[1]:
                text_by_order[seqno].append(
                    (rect, tone, opacity, linewidth))

    paints: list[VectorPaint] = []
    unsupported: list[UnsupportedVectorPaint] = []

    def add_rect(rect: Rect, tone: float, opacity: float, ordinal: int,
                 kind: str, operation: int, fill_rule: str = "union",
                 winding: int = 1, clip: Rect | None = None) -> None:
        x0, y0, x1, y1 = (float(value) for value in rect)
        if clip is not None:
            clipped = _rect_intersection((x0, y0, x1, y1), clip)
            if clipped is None:
                return
            x0, y0, x1, y1 = clipped
        if x1 <= x0 or y1 <= y0 or opacity <= 0:
            return
        if not all(math.isfinite(value)
                   for value in (x0, y0, x1, y1, tone, opacity)):
            raise ValueError("source vector paint has a non-finite value")
        if not 0 <= opacity <= 1:
            raise ValueError("source vector paint opacity is outside 0..1")
        paints.append(VectorPaint(
            x0, y0, x1, y1, float(tone), opacity, ordinal, kind,
            operation, fill_rule, winding))

    def add_unsupported_rect(rect: Rect, ordinal: int, reason: str,
                             pad: float = 0.0,
                             clip: Rect | None = None,
                             tone: float | None = None,
                             opacity: float | None = None,
                             exact_regions: tuple[tuple[Rect, int], ...] = (),
                             fill_rule: str = "union") -> None:
        padded = (rect[0] - pad, rect[1] - pad,
                  rect[2] + pad, rect[3] + pad)
        if clip is not None:
            clipped = _rect_intersection(padded, clip)
            if clipped is None:
                return
            padded = clipped
        if padded[2] <= padded[0] or padded[3] <= padded[1]:
            return
        if not all(math.isfinite(value) for value in padded):
            raise ValueError(f"{reason} has a non-finite source rectangle")
        unsupported.append(UnsupportedVectorPaint(
            padded, ordinal, reason, tone, opacity, (),
            exact_regions, fill_rule))

    def add_unsupported(drawing: dict[str, Any], ordinal: int,
                        reason: str, pad: float = 0.0,
                        clip: Rect | None = None,
                        tone: float | None = None,
                        opacity: float | None = None,
                        exact_regions: tuple[tuple[Rect, int], ...] = (),
                        fill_rule: str = "union") -> None:
        rect_value = drawing.get("rect")
        if rect_value is None:
            raise ValueError(f"{reason} has no bounded source rectangle")
        add_unsupported_rect(_rect_tuple(rect_value), ordinal, reason, pad,
                             clip, tone, opacity, exact_regions, fill_rule)

    def expect_bbox_kind(ordinal: int, wanted: str) -> None:
        if not 0 <= ordinal < len(bboxlog):
            raise ValueError(
                f"source drawing ordinal {ordinal} is outside the bbox log")
        if str(bboxlog[ordinal][0]) != wanted:
            raise ValueError(
                f"source drawing ordinal {ordinal} is {bboxlog[ordinal][0]!r}, "
                f"expected {wanted!r}")

    for drawing in drawings:
        drawing_type = str(drawing.get("type") or "")
        if drawing_type in {"clip", "group"}:
            continue
        seqno = int(drawing["seqno"])
        context = contexts[seqno]
        if context.fully_clipped:
            continue

        fill_order = -1
        stroke_order = -1
        if drawing_type == "f":
            fill_order = seqno
            expect_bbox_kind(fill_order, "fill-path")
        elif drawing_type == "s":
            stroke_order = seqno
            expect_bbox_kind(stroke_order, "stroke-path")
        elif drawing_type == "fs":
            fill_order = seqno
            stroke_order = seqno + 1
            expect_bbox_kind(fill_order, "fill-path")
            expect_bbox_kind(stroke_order, "stroke-path")
        else:
            raise ValueError(
                f"unsupported extended source drawing type {drawing_type!r}")

        if context.unsupported:
            for reason, rect in context.unsupported:
                add_unsupported_rect(
                    rect, seqno, reason, clip=context.clip)
            continue

        fill_colour = drawing.get("fill")
        stroke_colour = drawing.get("color")
        fill_tone = extract.to_gray(fill_colour)
        stroke_tone = extract.to_gray(stroke_colour)
        fill_opacity = float(drawing.get("fill_opacity")
                             if drawing.get("fill_opacity") is not None else 1.0)
        stroke_opacity = float(drawing.get("stroke_opacity")
                               if drawing.get("stroke_opacity") is not None else 1.0)
        stroke_width = float(drawing.get("width") or
                             2 * COMB_FALLBACK_HALFWIDTH_PT)
        half = stroke_width / 2

        if fill_colour is not None and fill_tone is None:
            # A chromatic colour is refused only for having no exact
            # rectilinear geometry to offer, never for being chromatic on its
            # own: `_perceptual_luminance` gives every colour a measurable
            # scalar tone, and a caller that independently decides this fill
            # cannot hide a comb divider (F225/W5 mechanism 1) reconstructs
            # these exact regions rather than approximating a shape.
            parsed = _rectilinear_fill_regions(drawing)
            if parsed is None:
                add_unsupported(
                    drawing, fill_order, "chromatic vector fill",
                    clip=context.clip)
            else:
                regions, fill_rule = parsed
                add_unsupported(
                    drawing, fill_order, "chromatic vector fill",
                    clip=context.clip,
                    tone=round(_perceptual_luminance(fill_colour), 4),
                    opacity=fill_opacity,
                    exact_regions=tuple(regions), fill_rule=fill_rule)
        elif fill_order >= 0 and fill_tone is not None:
            parsed = _rectilinear_fill_regions(drawing)
            if parsed is None:
                add_unsupported(drawing, fill_order,
                                "non-rectilinear or unbounded vector fill",
                                clip=context.clip)
            else:
                fill_regions, fill_rule = parsed
                for rect, winding in fill_regions:
                    add_rect(
                        rect, fill_tone, fill_opacity, fill_order,
                        "fill-region", fill_order, fill_rule, winding,
                        context.clip)

        if stroke_colour is not None and stroke_tone is None:
            add_unsupported(
                drawing, stroke_order, "chromatic vector stroke", half,
                context.clip)
        elif stroke_order >= 0 and stroke_tone is not None:
            stroke_supported = True
            stroke_regions: list[Rect] = []
            dashes = str(drawing.get("dashes") or "[] 0").strip()
            if dashes not in ("", "[] 0"):
                stroke_supported = False
            for item in drawing["items"]:
                op = item[0]
                if op == "re":
                    rect = item[1]
                    stroke_regions.extend((
                        (rect.x0 - half, rect.y0 - half,
                         rect.x1 + half, rect.y0 + half),
                        (rect.x0 - half, rect.y1 - half,
                         rect.x1 + half, rect.y1 + half),
                        (rect.x0 - half, rect.y0 + half,
                         rect.x0 + half, rect.y1 - half),
                        (rect.x1 - half, rect.y0 + half,
                         rect.x1 + half, rect.y1 - half),
                    ))
                elif op == "l":
                    p0, p1 = item[1], item[2]
                    dx, dy = abs(float(p1.x) - float(p0.x)), abs(float(p1.y) - float(p0.y))
                    if dx <= verify.DEFAULT_POSITION_TOL_PT:
                        stroke_regions.append((
                            min(p0.x, p1.x) - half,
                            min(p0.y, p1.y) - half,
                            max(p0.x, p1.x) + half,
                            max(p0.y, p1.y) + half,
                        ))
                    elif dy <= verify.DEFAULT_POSITION_TOL_PT:
                        stroke_regions.append((
                            min(p0.x, p1.x) - half,
                            min(p0.y, p1.y) - half,
                            max(p0.x, p1.x) + half,
                            max(p0.y, p1.y) + half,
                        ))
                    else:
                        stroke_supported = False
                elif op == "qu":
                    box = _axis_aligned_quad_box(item[1])
                    if box is None:
                        stroke_supported = False
                    else:
                        x0, y0, x1, y1 = box
                        stroke_regions.extend((
                            (x0 - half, y0 - half, x1 + half, y0 + half),
                            (x0 - half, y1 - half, x1 + half, y1 + half),
                            (x0 - half, y0 + half, x0 + half, y1 - half),
                            (x1 - half, y0 + half, x1 + half, y1 - half),
                        ))
                else:
                    stroke_supported = False
            if not stroke_supported:
                add_unsupported(drawing, stroke_order,
                                "non-rectilinear vector stroke", half,
                                context.clip)
            else:
                for rect in stroke_regions:
                    add_rect(
                        rect, stroke_tone, stroke_opacity, stroke_order,
                        "stroke-region", stroke_order, "union", 1,
                        context.clip)

    for ordinal, (kind, rect_value) in enumerate(bboxlog):
        if kind not in {"fill-image", "fill-text", "stroke-text"}:
            continue
        rect = tuple(float(value) for value in rect_value)
        if rect[2] <= rect[0] or rect[3] <= rect[1]:
            continue
        if kind in {"fill-text", "stroke-text"}:
            traced = text_by_order.get(ordinal) or ()
            tones = {tone for _char_rect, tone, _opacity, _width in traced}
            opacities = {
                opacity for _char_rect, _tone, opacity, _width in traced
            }
            tone = next(iter(tones)) if len(tones) == 1 else None
            opacity = (next(iter(opacities))
                       if len(opacities) == 1 else None)
            trace_rects = tuple(
                char_rect for char_rect, _tone, _opacity, _width in traced)
            if kind == "stroke-text":
                widths = {
                    width for _rect, _tone, _opacity, width in traced
                    if width is not None
                }
                half = (max(widths) / 2 if widths
                        else COMB_FALLBACK_HALFWIDTH_PT)
                rect = (rect[0] - half, rect[1] - half,
                        rect[2] + half, rect[3] + half)
                trace_rects = tuple(
                    (char_rect[0] - half, char_rect[1] - half,
                     char_rect[2] + half, char_rect[3] + half)
                    for char_rect in trace_rects
                )
            unsupported.append(UnsupportedVectorPaint(
                rect, ordinal, f"unmodeled source {kind} paint",
                tone, opacity, trace_rects))
            continue
        unsupported.append(UnsupportedVectorPaint(
            rect, ordinal, f"unmodeled source {kind} paint"))

    paints.sort(key=lambda paint: (
        paint.order, paint.operation, paint.kind,
        paint.x0, paint.y0, paint.x1, paint.y1,
        paint.fill_rule, paint.winding))
    unsupported.sort(key=lambda paint: (paint.order, paint.reason, paint.rect))
    return VectorPage(tuple(paints), tuple(unsupported))


def _same_topology(left: Sequence[float], right: Sequence[float]) -> bool:
    return (len(left) == len(right)
            and all(abs(a - b) <= COMB_MERGE_PT
                    for a, b in zip(left, right)))


def _topology_subset(left: Sequence[float], right: Sequence[float]) -> bool:
    """Strict subset under a sorted, monotone, one-to-one divider match."""
    if len(left) >= len(right):
        return False
    candidates = sorted(float(value) for value in right)
    cursor = 0
    for value in sorted(float(value) for value in left):
        while (cursor < len(candidates)
                and candidates[cursor] < value - COMB_MERGE_PT):
            cursor += 1
        if (cursor >= len(candidates)
                or candidates[cursor] > value + COMB_MERGE_PT):
            return False
        cursor += 1
    return True


def _merge_intervals(
        intervals: Sequence[tuple[float, float]], gap: float,
        ) -> list[tuple[float, float]]:
    merged: list[tuple[float, float]] = []
    for left, right in sorted(intervals):
        if merged and left <= merged[-1][1] + gap:
            merged[-1] = (merged[-1][0], max(merged[-1][1], right))
        else:
            merged.append((left, right))
    return merged


def _operation_covers(regions: Sequence[VectorPaint],
                      x: float, y: float) -> bool:
    hits = [region for region in regions if region.covers(x, y)]
    if not hits:
        return False
    rules = {region.fill_rule for region in regions}
    if len(rules) != 1:
        raise ValueError("one source paint operation has conflicting fill rules")
    rule = next(iter(rules))
    if rule == "union":
        return True
    if rule == "evenodd":
        return len(hits) % 2 == 1
    if rule == "nonzero":
        return sum(region.winding for region in hits) != 0
    raise ValueError(f"unknown source fill rule {rule!r}")


def _final_tone_and_owner(
        active: Sequence[VectorPaint], x: float, y: float,
        ) -> tuple[float, tuple[VectorPaint, ...]]:
    operations: dict[tuple[int, int], list[VectorPaint]] = (
        collections.defaultdict(list))
    for region in active:
        operations[(region.order, region.operation)].append(region)

    tone = 1.0
    owner: tuple[VectorPaint, ...] = ()
    for key in sorted(operations):
        regions = operations[key]
        if not _operation_covers(regions, x, y):
            continue
        tones = {region.tone for region in regions}
        opacities = {region.opacity for region in regions}
        if len(tones) != 1 or len(opacities) != 1:
            raise ValueError(
                "one source paint operation has conflicting tone or opacity")
        opacity = next(iter(opacities))
        tone = opacity * next(iter(tones)) + (1 - opacity) * tone
        owner = tuple(region for region in regions if region.covers(x, y))
    return tone, owner


def _final_tone(active: Sequence[VectorPaint], x: float, y: float) -> float:
    return _final_tone_and_owner(active, x, y)[0]


def _is_comb_vertical(paint: VectorPaint) -> bool:
    """A source stroke with material, not epsilon-only, vertical anisotropy."""
    width = paint.x1 - paint.x0
    height = paint.y1 - paint.y0
    return (
        width <= COMB_MAX_WIDTH_PT
        and height >= COMB_MINLEN_PT
        and height - width >= COMB_MINLEN_PT
    )


def _source_band_candidates(
        page: VectorPage, cell: Rect
        ) -> tuple[list[tuple[float, float]], int | None]:
    """Bands proposed only by narrow source paint near the cell."""
    x0, y0, x1, y1 = cell
    seeds: list[tuple[float, float, float, int]] = []
    for paint in page.paints:
        width = paint.x1 - paint.x0
        height = paint.y1 - paint.y0
        centre = (paint.x0 + paint.x1) / 2
        if (_is_comb_vertical(paint)
                and centre > x0 + COMB_EDGE_PT
                and centre < x1 - COMB_EDGE_PT
                and paint.y1 >= y0
                and paint.y0 <= y1):
            seeds.append((centre, paint.y0, paint.y1, paint.order))

    bands = {(a, b) for _x, a, b, _order in seeds
             if b - a >= COMB_MINLEN_PT}
    by_x = sorted(seeds)
    clusters: list[list[tuple[float, float, float, int]]] = []
    for seed in by_x:
        if clusters and seed[0] - clusters[-1][-1][0] <= COMB_MERGE_PT:
            clusters[-1].append(seed)
        else:
            clusters.append([seed])
    for cluster in clusters:
        intervals = sorted((a, b) for _x, a, b, _order in cluster)
        if not intervals:
            continue
        start, end = intervals[0]
        for a, b in intervals[1:]:
            if a <= end + COMB_YSLACK_PT:
                end = max(end, b)
            else:
                if end - start >= COMB_MINLEN_PT:
                    bands.add((start, end))
                start, end = a, b
        if end - start >= COMB_MINLEN_PT:
            bands.add((start, end))
    first_order = min((order for _x, _a, _b, order in seeds), default=None)
    return sorted(bands), first_order


def _band_topologies(page: VectorPage, x0: float, x1: float,
                     y0: float, y1: float
                     ) -> list[tuple[float, tuple[float, ...]]]:
    paints = [
        paint for paint in page.paints
        if paint.x1 > x0 and paint.x0 < x1
        and paint.y1 > y0 and paint.y0 < y1
    ]
    endpoints = {y0, y1}
    for paint in paints:
        endpoints.update((max(y0, paint.y0), min(y1, paint.y1)))
    ordered_y = sorted(endpoints)
    slabs: list[
        tuple[float, float, dict[float, tuple[tuple[float, float], ...]]]
    ] = []

    for a, b in zip(ordered_y, ordered_y[1:]):
        span = b - a
        if span <= verify.DEFAULT_POSITION_TOL_PT:
            continue
        mid_y = (a + b) / 2
        active = [paint for paint in paints if paint.y0 <= mid_y <= paint.y1]
        x_edges = {x0, x1}
        for paint in active:
            x_edges.update((max(x0, paint.x0), min(x1, paint.x1)))
        ordered_x = sorted(x_edges)
        intervals: list[tuple[float, float, float]] = []
        for left, right in zip(ordered_x, ordered_x[1:]):
            if right <= left:
                continue
            tone = round(_final_tone(active, (left + right) / 2, mid_y), 4)
            if intervals and intervals[-1][2] == tone:
                intervals[-1] = (intervals[-1][0], right, tone)
            else:
                intervals.append((left, right, tone))

        # A divider is a narrow final-paint contrast corridor, not necessarily
        # non-white ink. A white knockout through a grey band is just as visible
        # as a black rule through white paper. Keeping tones separate makes a
        # mixed-tone topology fail closed below rather than silently dropping
        # one of its visible boundaries.
        by_tone: dict[float, list[tuple[float, float]]] = (
            collections.defaultdict(list))
        for index, (left, right, tone) in enumerate(intervals):
            if (index == 0 or index == len(intervals) - 1
                    or right - left > COMB_MAX_WIDTH_PT
                    or left <= x0 + COMB_EDGE_PT
                    or right >= x1 - COMB_EDGE_PT):
                continue
            left_tone = intervals[index - 1][2]
            right_tone = intervals[index + 1][2]
            if tone == left_tone or tone == right_tone:
                continue
            centre = (left + right) / 2
            final_tone, owners = _final_tone_and_owner(
                active, centre, mid_y)
            if (round(final_tone, 4) != tone
                    or not any(
                        owner.covers(centre, mid_y)
                        and _is_comb_vertical(owner)
                        for owner in owners
                    )):
                # A final-tone seam with no narrow vertical source operation
                # is paper between paints, not a divider. This applies before
                # every topology verdict, including the single-choice path.
                continue
            by_tone[tone].append((
                round(centre, 6),
                right - left,
            ))
        slabs.append((
            a,
            b,
            {tone: tuple(components)
             for tone, components in sorted(by_tone.items())},
        ))

    out: list[tuple[float, tuple[float, ...]]] = []
    full_span = y1 - y0
    tones = sorted({
        tone for _a, _b, slab_tones in slabs for tone in slab_tones
    })
    for tone in tones:
        observations = [
            (centre, width, b - a)
            for a, b, slab_tones in slabs
            for centre, width in slab_tones.get(tone, ())
        ]
        centre_clusters: list[list[tuple[float, float, float]]] = []
        for observation in sorted(observations):
            if (centre_clusters
                    and observation[0] - centre_clusters[-1][-1][0]
                    <= COMB_MERGE_PT):
                centre_clusters[-1].append(observation)
            else:
                centre_clusters.append([observation])

        stable: list[float] = []
        for cluster in centre_clusters:
            weight = sum(span for _centre, _width, span in cluster)
            anchor = (
                sum(centre * span for centre, _width, span in cluster) / weight
                if weight else cluster[0][0]
            )
            longest = current = 0.0
            run_width = max_run_width = 0.0
            previous_end: float | None = None
            for a, b, slab_tones in slabs:
                matches = [
                    width for centre, width in slab_tones.get(tone, ())
                    if abs(centre - anchor) <= COMB_MERGE_PT
                ]
                if (matches
                        and (previous_end is None
                             or a - previous_end
                             <= verify.DEFAULT_POSITION_TOL_PT)):
                    current += b - a
                    run_width = max(run_width, max(matches))
                elif matches:
                    current = b - a
                    run_width = max(matches)
                else:
                    current = 0.0
                    run_width = 0.0
                if current > longest:
                    longest = current
                    max_run_width = run_width
                previous_end = b
            # Strict-majority *continuous* evidence prevents disconnected dots
            # from summing into a divider. Requiring its run to be taller than
            # its widest final component rejects a square even when the square
            # happens to occupy most of a short proposed band.
            if (longest > full_span / 2
                    and longest >= COMB_MINLEN_PT
                    and longest - max_run_width >= COMB_MINLEN_PT):
                stable.append(round(anchor, 6))

        if not stable:
            continue

        longest_common = current_common = 0.0
        previous_end = None
        for a, b, slab_tones in slabs:
            components = slab_tones.get(tone, ())
            all_present = all(
                any(abs(centre - anchor) <= COMB_MERGE_PT
                    for centre, _width in components)
                for anchor in stable
            )
            if (all_present
                    and (previous_end is None
                         or a - previous_end
                         <= verify.DEFAULT_POSITION_TOL_PT)):
                current_common += b - a
            elif all_present:
                current_common = b - a
            else:
                current_common = 0.0
            longest_common = max(longest_common, current_common)
            previous_end = b
        if (longest_common > full_span / 2
                and longest_common >= COMB_MINLEN_PT):
            out.append((tone, tuple(stable)))
    return out


def _source_paint_evidence(paint: VectorPaint) -> dict[str, Any]:
    """Serialize one final-paint owner without losing source lineage."""
    width = paint.x1 - paint.x0
    height = paint.y1 - paint.y0
    if width > height:
        orientation = "horizontal"
    elif height > width:
        orientation = "vertical"
    else:
        orientation = "square"
    return {
        "order": paint.order,
        "operation": paint.operation,
        "kind": paint.kind,
        "tone": round(paint.tone, 6),
        "opacity": round(paint.opacity, 6),
        "orientation": orientation,
        "rect": [
            round(paint.x0, 6),
            round(paint.y0, 6),
            round(paint.x1, 6),
            round(paint.y1, 6),
        ],
        "width_pt": round(width, 6),
        "height_pt": round(height, 6),
    }


def _vertical_lineage_diagnostics(
        page: VectorPage, x0: float, x1: float, y0: float, y1: float,
        ) -> list[dict[str, Any]]:
    """Explain why raw same-x source strokes lack one continuous final run.

    This is evidence only: it never promotes a topology. Each slab is owned
    only when the final source operation at that x is itself a narrow vertical.
    Later orthogonal paint therefore appears as an explicit interruption rather
    than being silently stitched into a divider.
    """
    candidates = [
        paint for paint in page.paints
        if paint.x1 > x0 and paint.x0 < x1
        and paint.y1 > y0 and paint.y0 < y1
        and _is_comb_vertical(paint)
        and (paint.x0 + paint.x1) / 2 > x0 + COMB_EDGE_PT
        and (paint.x0 + paint.x1) / 2 < x1 - COMB_EDGE_PT
    ]
    clusters: list[list[VectorPaint]] = []
    for paint in sorted(
            candidates,
            key=lambda item: (
                (item.x0 + item.x1) / 2,
                item.y0, item.y1, item.order, item.operation,
            )):
        centre = (paint.x0 + paint.x1) / 2
        prior_centre = (
            (clusters[-1][-1].x0 + clusters[-1][-1].x1) / 2
            if clusters else None
        )
        if (prior_centre is not None
                and centre - prior_centre <= COMB_MERGE_PT):
            clusters[-1].append(paint)
        else:
            clusters.append([paint])

    relevant = [
        paint for paint in page.paints
        if paint.x1 > x0 and paint.x0 < x1
        and paint.y1 > y0 and paint.y0 < y1
    ]
    endpoints = {y0, y1}
    for paint in relevant:
        endpoints.update((max(y0, paint.y0), min(y1, paint.y1)))
    ordered_y = sorted(endpoints)
    slabs = [
        (a, b) for a, b in zip(ordered_y, ordered_y[1:])
        if b - a > SOURCE_COORD_EPS_PT
    ]
    full_span = y1 - y0
    diagnostics: list[dict[str, Any]] = []

    for cluster in clusters:
        anchor_paint = min(
            cluster,
            key=lambda paint: (
                -max(0.0, min(y1, paint.y1) - max(y0, paint.y0)),
                (paint.x0 + paint.x1) / 2,
                paint.order,
                paint.operation,
            ),
        )
        anchor = (anchor_paint.x0 + anchor_paint.x1) / 2
        cluster_left = min(paint.x0 for paint in cluster)
        cluster_right = max(paint.x1 for paint in cluster)
        slab_evidence: list[dict[str, Any]] = []
        for a, b in slabs:
            mid_y = (a + b) / 2
            active = [
                paint for paint in relevant
                if paint.y0 <= mid_y <= paint.y1
            ]
            active_members = [
                paint for paint in cluster
                if paint.y0 <= mid_y <= paint.y1
            ]
            sample_xs = sorted({
                (paint.x0 + paint.x1) / 2 for paint in active_members
            } or {anchor}, key=lambda value: (abs(value - anchor), value))
            x_edges = {x0, x1}
            for paint in active:
                x_edges.update((max(x0, paint.x0), min(x1, paint.x1)))
            intervals: list[tuple[float, float, float]] = []
            for left, right in zip(sorted(x_edges), sorted(x_edges)[1:]):
                if right <= left:
                    continue
                tone = round(
                    _final_tone(active, (left + right) / 2, mid_y), 4)
                if intervals and intervals[-1][2] == tone:
                    intervals[-1] = (intervals[-1][0], right, tone)
                else:
                    intervals.append((left, right, tone))

            samples: list[dict[str, Any]] = []
            for sample_x in sample_xs:
                final_tone, owners = _final_tone_and_owner(
                    active, sample_x, mid_y)
                owned = any(
                    owner.covers(sample_x, mid_y)
                    and _is_comb_vertical(owner)
                    and abs(
                        (owner.x0 + owner.x1) / 2 - anchor
                    ) <= COMB_MERGE_PT
                    for owner in owners
                )
                containing = [
                    (index, interval)
                    for index, interval in enumerate(intervals)
                    if (interval[0] - SOURCE_COORD_EPS_PT <= sample_x
                        <= interval[1] + SOURCE_COORD_EPS_PT)
                ]
                if containing:
                    interval_index, corridor = min(
                        containing,
                        key=lambda item: (
                            abs((item[1][0] + item[1][1]) / 2 - sample_x),
                            item[1][1] - item[1][0],
                            item[0],
                        ),
                    )
                    left, right, corridor_tone = corridor
                    left_tone = (
                        intervals[interval_index - 1][2]
                        if interval_index > 0 else None
                    )
                    right_tone = (
                        intervals[interval_index + 1][2]
                        if interval_index + 1 < len(intervals) else None
                    )
                    visible = (
                        owned
                        and right - left <= COMB_MAX_WIDTH_PT
                        and left > x0 + COMB_EDGE_PT
                        and right < x1 - COMB_EDGE_PT
                        and left_tone is not None
                        and right_tone is not None
                        and corridor_tone != left_tone
                        and corridor_tone != right_tone
                    )
                    corridor_evidence: dict[str, Any] | None = {
                        "x0": round(left, 6),
                        "x1": round(right, 6),
                        "width_pt": round(right - left, 6),
                        "tone": corridor_tone,
                        "left_tone": left_tone,
                        "right_tone": right_tone,
                    }
                else:
                    visible = False
                    corridor_evidence = None
                samples.append({
                    "visible": visible,
                    "owned": owned,
                    "sample_x": sample_x,
                    "final_tone": final_tone,
                    "owners": owners,
                    "corridor": corridor_evidence,
                })
            selected = next(
                (sample for sample in samples if sample["visible"]),
                next(
                    (sample for sample in samples if sample["owned"]),
                    samples[0],
                ),
            )
            owned = bool(selected["owned"])
            visible = bool(selected["visible"])
            sample_x = float(selected["sample_x"])
            final_tone = float(selected["final_tone"])
            owners = selected["owners"]

            surrounding_samples = []
            for side, probe_x in (
                    ("left", max(
                        x0 + SOURCE_COORD_EPS_PT,
                        cluster_left - 2 * SOURCE_COORD_EPS_PT,
                    )),
                    ("right", min(
                        x1 - SOURCE_COORD_EPS_PT,
                        cluster_right + 2 * SOURCE_COORD_EPS_PT,
                    ))):
                probe_tone, probe_owners = _final_tone_and_owner(
                    active, probe_x, mid_y)
                surrounding_samples.append({
                    "side": side,
                    "x": round(probe_x, 6),
                    "final_tone": round(probe_tone, 6),
                    "last_owners": [
                        _source_paint_evidence(owner)
                        for owner in sorted(
                            probe_owners,
                            key=lambda item: (
                                item.order, item.operation, item.kind,
                                item.x0, item.y0, item.x1, item.y1,
                            ),
                        )
                    ],
                })
            orthogonal_owners: list[dict[str, Any]] = []
            seen_orthogonal: set[
                tuple[int, int, float, float, float, float]
            ] = set()
            for sample in surrounding_samples:
                if (round(float(sample["final_tone"]), 4)
                        != round(final_tone, 4)):
                    continue
                for owner_evidence in sample["last_owners"]:
                    if owner_evidence["orientation"] != "horizontal":
                        continue
                    rect = owner_evidence["rect"]
                    owner_key = (
                        int(owner_evidence["order"]),
                        int(owner_evidence["operation"]),
                        float(rect[0]), float(rect[1]),
                        float(rect[2]), float(rect[3]),
                    )
                    if owner_key not in seen_orthogonal:
                        seen_orthogonal.add(owner_key)
                        orthogonal_owners.append(owner_evidence)
            if visible:
                interruption_cause = None
            elif not owned:
                interruption_cause = "final-owner-not-narrow-vertical"
            else:
                interruption_cause = "no-narrow-final-tone-contrast-corridor"
            slab_evidence.append({
                "y0": round(a, 6),
                "y1": round(b, 6),
                "span_pt": round(b - a, 6),
                "source_present": bool(active_members),
                "owned_by_narrow_vertical": owned,
                "visible_narrow_corridor": visible,
                "interruption_cause": interruption_cause,
                "sample_x": round(sample_x, 6),
                "final_tone": round(final_tone, 6),
                "corridor": selected["corridor"],
                "last_owners": [
                    _source_paint_evidence(owner)
                    for owner in sorted(
                        owners,
                        key=lambda item: (
                            item.order, item.operation, item.kind,
                            item.x0, item.y0, item.x1, item.y1,
                        ),
                    )
                ],
                "surrounding_samples": surrounding_samples,
                "orthogonal_same_tone_owners": orthogonal_owners,
            })

        continuous_runs: list[list[float]] = []
        interruptions: list[list[float]] = []
        for slab in slab_evidence:
            target = (
                continuous_runs
                if slab["visible_narrow_corridor"] else interruptions
            )
            a = float(slab["y0"])
            b = float(slab["y1"])
            if (target
                    and abs(target[-1][1] - a) <= SOURCE_COORD_EPS_PT):
                target[-1][1] = b
            else:
                target.append([a, b])
        if not continuous_runs:
            continue
        covered = sum(b - a for a, b in continuous_runs)
        longest = max(b - a for a, b in continuous_runs)
        max_source_width = max(
            paint.x1 - paint.x0 for paint in cluster)
        diagnostics.append({
            "x": round(anchor, 6),
            "band_y0": round(y0, 6),
            "band_y1": round(y1, 6),
            "band_span_pt": round(full_span, 6),
            "source_segments": [
                _source_paint_evidence(paint)
                for paint in sorted(
                    cluster,
                    key=lambda item: (
                        item.y0, item.y1, item.order, item.operation,
                        item.x0, item.x1,
                    ),
                )
            ],
            "continuous_runs": continuous_runs,
            "interruptions": interruptions,
            "interruption_segments": [
                slab for slab in slab_evidence
                if not slab["visible_narrow_corridor"]
            ],
            "covered_pt": round(covered, 6),
            "longest_run_pt": round(longest, 6),
            "strict_majority": (
                longest > full_span / 2
                and longest >= COMB_MINLEN_PT
                and longest > max_source_width
            ),
        })
    return sorted(diagnostics, key=lambda item: item["x"])


def _stable_source_verticals(
        page: VectorPage, x0: float, x1: float, y0: float, y1: float,
        tone: float,
        ) -> list[float]:
    """Continuous final-tone verticals with an owning source operation."""
    candidates = [
        paint for paint in page.paints
        if paint.x1 > x0 and paint.x0 < x1
        and paint.y1 > y0 and paint.y0 < y1
        and _is_comb_vertical(paint)
    ]
    clusters: list[list[VectorPaint]] = []
    for paint in sorted(
            candidates,
            key=lambda item: ((item.x0 + item.x1) / 2, item.y0, item.y1)):
        centre = (paint.x0 + paint.x1) / 2
        if (clusters
                and centre - (
                    clusters[-1][-1].x0 + clusters[-1][-1].x1
                ) / 2 <= COMB_MERGE_PT):
            clusters[-1].append(paint)
        else:
            clusters.append([paint])

    relevant = [
        paint for paint in page.paints
        if paint.x1 > x0 and paint.x0 < x1
        and paint.y1 > y0 and paint.y0 < y1
    ]
    endpoints = {y0, y1}
    for paint in relevant:
        endpoints.update((max(y0, paint.y0), min(y1, paint.y1)))
    slabs = [
        (a, b) for a, b in zip(sorted(endpoints), sorted(endpoints)[1:])
        if b - a > SOURCE_COORD_EPS_PT
    ]
    full_span = y1 - y0
    wanted_tone = round(tone, 4)
    stable: list[float] = []
    for cluster in clusters:
        weights = [max(0.0, min(y1, paint.y1) - max(y0, paint.y0))
                   for paint in cluster]
        weight = sum(weights)
        anchor = (
            sum(((paint.x0 + paint.x1) / 2) * span
                for paint, span in zip(cluster, weights)) / weight
            if weight else (cluster[0].x0 + cluster[0].x1) / 2
        )
        longest = current = 0.0
        for a, b in slabs:
            mid_y = (a + b) / 2
            active = [
                paint for paint in relevant
                if paint.y0 <= mid_y <= paint.y1
            ]
            owned = False
            for member in active:
                width = member.x1 - member.x0
                height = member.y1 - member.y0
                centre = (member.x0 + member.x1) / 2
                if (not _is_comb_vertical(member)
                        or abs(centre - anchor) > COMB_MERGE_PT):
                    continue
                final_tone, owners = _final_tone_and_owner(
                    active, centre, mid_y)
                if (round(final_tone, 4) == wanted_tone
                        and any(
                            owner.covers(centre, mid_y)
                            and _is_comb_vertical(owner)
                            for owner in owners
                        )):
                    owned = True
                    break
            if owned:
                current += b - a
                longest = max(longest, current)
            else:
                current = 0.0
        if (longest >= full_span - COMB_YSLACK_PT
                and longest >= COMB_MINLEN_PT):
            stable.append(round(anchor, 6))
    return stable


def _source_vertical_ink_geometry(
        page: VectorPage,
        centre_x: float,
        band_y0: float,
        band_y1: float,
        tone: float,
        ) -> dict[str, Any] | None:
    """Raw painted x extent supporting one stable vertical lineage."""
    wanted_tone = round(tone, 4)
    members = [
        paint for paint in page.paints
        if _is_comb_vertical(paint)
        and round(paint.tone, 4) == wanted_tone
        and paint.y1 > band_y0
        and paint.y0 < band_y1
        and abs((paint.x0 + paint.x1) / 2.0 - centre_x)
        <= COMB_MERGE_PT
    ]
    if not members:
        return None
    return {
        "center_x": float(centre_x),
        "ink_x0": min(paint.x0 for paint in members),
        "ink_x1": max(paint.x1 for paint in members),
        "ink_y0": min(paint.y0 for paint in members),
        "ink_y1": max(paint.y1 for paint in members),
        "members": tuple(members),
        "paint_rects": [
            [paint.x0, paint.y0, paint.x1, paint.y1]
            for paint in sorted(
                members,
                key=lambda item: (
                    item.x0, item.y0, item.x1, item.y1,
                    item.order, item.operation),
            )
        ],
    }


def _baseline_segments(
        baseline: _SourceBaselineSpan,
        ) -> tuple[tuple[float, float, float, float], ...]:
    if baseline.segments:
        return baseline.segments
    return ((baseline.left, baseline.right, baseline.y0, baseline.y1),)


def _baseline_contact_segments(
        baseline: _SourceBaselineSpan,
        contact_x: float,
        ) -> tuple[_SourceBaselineSpan, ...]:
    """Retain the actual baseline segment levels touching one endpoint."""
    return tuple(
        _SourceBaselineSpan(
            y=(segment_y0 + segment_y1) / 2.0,
            y0=segment_y0,
            y1=segment_y1,
            left=segment_left,
            right=segment_right,
            operations=baseline.operations,
            segments=(
                (segment_left, segment_right, segment_y0, segment_y1),
            ),
        )
        for (
            segment_left, segment_right, segment_y0, segment_y1,
        ) in _baseline_segments(baseline)
        if contact_x >= segment_left - SOURCE_COORD_EPS_PT
        and contact_x <= segment_right + SOURCE_COORD_EPS_PT
    )


def _junction_sample_y(
        member_y0: float, member_y1: float,
        base_y0: float, base_y1: float) -> float:
    """Where to measure the ink at one vertical/baseline junction.

    Inside the overlap when the two rectangles overlap. When they only touch,
    their intersection is empty and its midpoint lies inside neither: 2200-A's
    comb rails end at y=432.6499939 where the baseline starts at
    y=432.6500244, three hundredths of a thousandth of a point apart, and
    sampling between them found no ink at all -- no comb on that sheet had a
    frame. The caller has already refused any gap wider than source-coordinate
    noise, so the touch is a junction; measure it on the rule's own side of
    the touch, where a knockout that erases the junction erases it too.
    """
    contact_y0 = max(member_y0, base_y0)
    contact_y1 = min(member_y1, base_y1)
    if contact_y1 >= contact_y0:
        return (contact_y0 + contact_y1) / 2.0
    return (base_y0 + base_y1) / 2.0


def _vertical_baseline_contact_intervals(
        page: VectorPage,
        tone: float,
        rail: dict[str, Any] | None,
        baseline: _SourceBaselineSpan,
        ) -> list[tuple[float, float]]:
    """Final-visible x intervals where this vertical physically meets a base.

    The aggregate ``ink_x0..ink_x1`` envelope is deliberately not evidence:
    two split strokes can straddle a baseline endpoint while leaving paper at
    the claimed contact coordinate. Each interval below is cut from an actual
    narrow source rectangle, at an exact y-overlap/touch with an actual
    baseline segment, and remains finally owned by one of those two lineages.
    """
    if rail is None:
        return []
    members = tuple(rail.get("members", ()))
    allowed_operations = {
        (paint.order, paint.operation) for paint in members
    } | set(baseline.operations)
    wanted_tone = round(tone, 4)
    intervals: list[tuple[float, float]] = []
    for member in members:
        for base_left, base_right, base_y0, base_y1 in _baseline_segments(
                baseline):
            if (member.y0 > base_y1 + SOURCE_COORD_EPS_PT
                    or member.y1 < base_y0 - SOURCE_COORD_EPS_PT
                    or member.x0 > base_right + SOURCE_COORD_EPS_PT
                    or member.x1 < base_left - SOURCE_COORD_EPS_PT):
                continue
            left = max(member.x0, base_left)
            right = min(member.x1, base_right)
            if right < left - SOURCE_COORD_EPS_PT:
                continue
            sample_y = _junction_sample_y(
                member.y0, member.y1, base_y0, base_y1)
            active = [
                paint for paint in page.paints
                if (paint.y0 <= sample_y + SOURCE_COORD_EPS_PT
                    and paint.y1 >= sample_y - SOURCE_COORD_EPS_PT
                    and paint.x1 >= left - SOURCE_COORD_EPS_PT
                    and paint.x0 <= right + SOURCE_COORD_EPS_PT)
            ]
            x_edges = {left, right}
            for paint in active:
                clipped_left = max(left, paint.x0)
                clipped_right = min(right, paint.x1)
                if clipped_right >= clipped_left:
                    x_edges.update((clipped_left, clipped_right))
            ordered_x = sorted(x_edges)
            if len(ordered_x) == 1:
                ordered_x.append(ordered_x[0])
            for slab_left, slab_right in zip(
                    ordered_x, ordered_x[1:]):
                sample_x = (slab_left + slab_right) / 2.0
                final_tone, owners = _final_tone_and_owner(
                    active, sample_x, sample_y)
                if (round(final_tone, 4) != wanted_tone
                        or not any(
                            (owner.order, owner.operation)
                            in allowed_operations
                            for owner in owners
                        )):
                    continue
                intervals.append((slab_left, slab_right))
    return _merge_intervals(intervals, SOURCE_COORD_EPS_PT)


def _baseline_coordinate_contacts_vertical(
        page: VectorPage,
        tone: float,
        contact_x: float,
        rail: dict[str, Any] | None,
        baseline: _SourceBaselineSpan,
        ) -> bool:
    """Require exact 2D contact with one painted vertical interval."""
    return any(
        contact_x >= left - SOURCE_COORD_EPS_PT
        and contact_x <= right + SOURCE_COORD_EPS_PT
        for left, right in _vertical_baseline_contact_intervals(
            page, tone, rail, baseline)
    )


def _connected_vertical_baseline_contact(
        page: VectorPage,
        tone: float,
        rail: dict[str, Any] | None,
        band_y0: float,
        span_y1: float,
        contact_x: float,
        baseline: _SourceBaselineSpan,
        ) -> bool:
    """Bind exact baseline contact to one uninterrupted visible rail path.

    ``_stable_source_verticals`` deliberately tolerates up to
    ``COMB_YSLACK_PT`` of missing span.  That tolerance cannot prove a U-frame
    side rail: a long stroke and a separate same-x contact fragment could
    otherwise straddle paper and jointly satisfy the stable/contact checks.
    Track final-tone ink backed by the effective compound paint operation of an
    actual vertical member slab by slab.  One run may start within the existing
    leading ``COMB_YSLACK_PT`` allowance; after it starts, only x-overlapping
    continuation can reach the exact baseline coordinate.  A later same-tone
    fill may own the final pixel while preserving a genuinely painted vertical
    operation; it cannot replace a canceled member or missing ink with an
    unrelated broad repaint.
    """
    if (rail is None
            or span_y1 <= band_y0 + SOURCE_COORD_EPS_PT):
        return False
    members = tuple(rail.get("members", ()))
    if not members:
        return False
    operation_regions: dict[
        tuple[int, int], list[VectorPaint]
    ] = collections.defaultdict(list)
    for paint in page.paints:
        operation_regions[(paint.order, paint.operation)].append(paint)

    span_left = min(paint.x0 for paint in members)
    span_right = max(paint.x1 for paint in members)
    relevant = [
        paint for paint in page.paints
        if paint.x1 >= span_left - SOURCE_COORD_EPS_PT
        and paint.x0 <= span_right + SOURCE_COORD_EPS_PT
        and paint.y1 > band_y0
        and paint.y0 < span_y1
    ]
    endpoints = {band_y0, span_y1}
    for paint in relevant:
        endpoints.update((
            max(band_y0, paint.y0),
            min(span_y1, paint.y1),
        ))
    slabs = [
        (a, b)
        for a, b in zip(sorted(endpoints), sorted(endpoints)[1:])
        if b - a > SOURCE_COORD_EPS_PT
    ]
    if not slabs:
        return False

    wanted_tone = round(tone, 4)
    start_deadline = band_y0 + COMB_YSLACK_PT
    reachable: list[tuple[float, float]] | None = None
    prior_y = band_y0
    for a, b in slabs:
        if a > prior_y + SOURCE_COORD_EPS_PT:
            if a > start_deadline + SOURCE_COORD_EPS_PT:
                return False
            reachable = None
        sample_y = (a + b) / 2.0
        active = [
            paint for paint in relevant
            if paint.y0 <= sample_y <= paint.y1
        ]
        active_members = [
            paint for paint in members
            if paint.y0 <= sample_y <= paint.y1
        ]

        x_edges = {span_left, span_right}
        for paint in active:
            clipped_left = max(span_left, paint.x0)
            clipped_right = min(span_right, paint.x1)
            if clipped_right >= clipped_left:
                x_edges.update((clipped_left, clipped_right))
        visible: list[tuple[float, float]] = []
        ordered_x = sorted(x_edges)
        if len(ordered_x) == 1:
            ordered_x.append(ordered_x[0])
        for left, right in zip(ordered_x, ordered_x[1:]):
            sample_x = (left + right) / 2.0
            final_tone = _final_tone(active, sample_x, sample_y)
            if (round(final_tone, 4) == wanted_tone
                    and any(
                        member.covers(sample_x, sample_y)
                        and _operation_covers(
                            operation_regions[
                                (member.order, member.operation)
                            ],
                            sample_x,
                            sample_y,
                        )
                        for member in active_members
                    )):
                visible.append((left, right))
        visible = _merge_intervals(visible, SOURCE_COORD_EPS_PT)
        if reachable is not None:
            connected = [
                interval for interval in visible
                if any(
                    interval[0] <= prior[1] + SOURCE_COORD_EPS_PT
                    and interval[1] >= prior[0] - SOURCE_COORD_EPS_PT
                    for prior in reachable
                )
            ]
            if connected:
                reachable = connected
            elif visible and a <= start_deadline + SOURCE_COORD_EPS_PT:
                reachable = visible
            elif b <= start_deadline + SOURCE_COORD_EPS_PT:
                reachable = None
            else:
                return False
        elif visible:
            if a > start_deadline + SOURCE_COORD_EPS_PT:
                return False
            reachable = visible
        elif b > start_deadline + SOURCE_COORD_EPS_PT:
            return False
        prior_y = b

    if (prior_y < span_y1 - SOURCE_COORD_EPS_PT
            or reachable is None
            or not any(
                contact_x >= left - SOURCE_COORD_EPS_PT
                and contact_x <= right + SOURCE_COORD_EPS_PT
                for left, right in reachable
            )):
        return False
    return _baseline_coordinate_contacts_vertical(
        page, tone, contact_x, rail, baseline)


def _vertical_has_connected_baseline_contact(
        page: VectorPage,
        tone: float,
        rail: dict[str, Any] | None,
        band_y0: float,
        contact_x: float,
        baseline: _SourceBaselineSpan,
        ) -> bool:
    """Require one actual segment-level contact on the connected rail path.

    A segmented junction may legitimately span more than one touching
    baseline segment or level.  Each is evaluated independently; an aggregate
    rail envelope or aggregate baseline component is never itself a witness.
    No spanning segment or no independently connected segment fails closed.
    """
    contacts = _baseline_contact_segments(baseline, contact_x)
    return bool(contacts) and any(
        _connected_vertical_baseline_contact(
            page,
            tone,
            rail,
            band_y0,
            contact.y0,
            contact_x,
            contact,
        )
        for contact in contacts
    )


def _published_vertical_geometry(
        page: VectorPage,
        tone: float,
        rail: dict[str, Any],
        baseline: _SourceBaselineSpan,
        ) -> dict[str, Any]:
    members = sorted(
        rail.get("members", ()),
        key=lambda item: (
            item.x0, item.y0, item.x1, item.y1,
            item.order, item.operation, item.kind,
        ),
    )
    return {
        "center_x": round(float(rail["center_x"]), 6),
        "ink_x0": round(float(rail["ink_x0"]), 6),
        "ink_x1": round(float(rail["ink_x1"]), 6),
        "ink_y0": round(float(rail["ink_y0"]), 6),
        "ink_y1": round(float(rail["ink_y1"]), 6),
        "paint_rects": [
            [
                round(paint.x0, 6), round(paint.y0, 6),
                round(paint.x1, 6), round(paint.y1, 6),
            ]
            for paint in members
        ],
        "paint_operations": [
            [paint.order, paint.operation] for paint in members
        ],
        "contact_intervals_x": [
            [round(left, 6), round(right, 6)]
            for left, right in _vertical_baseline_contact_intervals(
                page, tone, rail, baseline)
        ],
    }


def _adds_no_ink_outside_rule(
        owner: VectorPaint, rule: VectorPaint) -> bool:
    """A same-tone paint confined to one rule's own thickness buries nothing.

    Official rule chains are emitted segment by segment, with a square junction
    block wherever a divider crosses the rule (1707's date box, 2200A's comb
    baseline: 0.48pt wide by 0.48pt tall).  Such a block is neither wider than
    tall nor tall enough to read as a divider, so neither of the other two
    ownership clauses recognises it -- and because consecutive segments of one
    chain overlap by source-coordinate noise (0.004pt on 1707), one junction
    block was enough to discard the 13.3pt segment beside it and cut the chain
    in half.

    Burial is what this ownership test exists to catch: a later broad fill that
    swallows a narrow rule leaves nothing a reader could call a rule.  A paint
    that adds no ink above or below the rule's own thickness cannot do that --
    the visible top and bottom edge of the ink stay exactly where the rule drew
    them -- so it is the same rule, however far it runs sideways.
    """
    return (round(owner.tone, 4) == round(rule.tone, 4)
            and owner.y0 >= rule.y0 - SOURCE_COORD_EPS_PT
            and owner.y1 <= rule.y1 + SOURCE_COORD_EPS_PT)


def _is_rule_shaped(paint: VectorPaint) -> bool:
    """Paint that lies ALONG a rule rather than across it.

    Materially taller than wide is a stroke crossing the rule; anything else
    lies along it, the square junction block where a divider meets it included.
    """
    return (paint.y1 - paint.y0) - (paint.x1 - paint.x0) <= SOURCE_COORD_EPS_PT


def _baseline_spans(
        page: VectorPage, band_y1: float, tone: float,
        ) -> list[_SourceBaselineSpan]:
    """Untrimmed final-visible source baselines near one band bottom.

    A claimed layout box is deliberately absent from this function. Clipping a
    raw baseline to that box turns any two internal dividers into counterfeit
    frame endpoints, making a shrunk layout self-validating. Each returned run
    therefore retains its source-operation lineage and its real merged source
    endpoints.

    The shape test rejects paint that is MATERIALLY taller than wide. A rule
    chain's junction blocks are square to the last bit -- 2000-DST paints the
    same 0.48pt block at x=206.33 with width 0.4799957 and at x=262.37 with
    width 0.4799805, one either side of its 0.4799805 height -- so a strict
    `width <= height` decided whether a 39-divider baseline chain survived on
    1.5e-5pt of float noise. Which side of a float the two identical blocks
    land on is not a fact about the paper.
    """
    wanted_tone = round(tone, 4)
    raw: list[VectorPaint] = []
    for paint in page.paints:
        if (not _is_rule_shaped(paint)
                or paint.y1 - paint.y0 > COMB_MAX_WIDTH_PT
                or round(paint.tone, 4) != wanted_tone
                or band_y1 < paint.y0 - COMB_YSLACK_PT
                or band_y1 > paint.y1 + COMB_YSLACK_PT):
            continue
        raw.append(paint)

    spans: list[_SourceBaselineSpan] = []
    for paint in sorted(
            raw,
            key=lambda item: (
                (item.y0 + item.y1) / 2.0,
                item.x0, item.x1, item.order, item.operation,
            )):
        sample_y = (paint.y0 + paint.y1) / 2.0
        raw_left, raw_right = paint.x0, paint.x1
        active = [
            candidate for candidate in page.paints
            if candidate.y0 <= sample_y <= candidate.y1
            and candidate.x1 > raw_left and candidate.x0 < raw_right
        ]
        x_edges = {raw_left, raw_right}
        for candidate in active:
            x_edges.update((
                max(raw_left, candidate.x0),
                min(raw_right, candidate.x1),
            ))
        visible: list[tuple[float, float]] = []
        ordered_x = sorted(x_edges)
        lineage_operation = (paint.order, paint.operation)
        for left, right in zip(ordered_x, ordered_x[1:]):
            final_tone, owners = _final_tone_and_owner(
                active, (left + right) / 2, sample_y)
            owned_by_baseline_or_connector = any(
                (
                    (owner.order, owner.operation) == lineage_operation
                    and owner.x1 - owner.x0 > owner.y1 - owner.y0
                )
                or _is_comb_vertical(owner)
                or _adds_no_ink_outside_rule(owner, paint)
                for owner in owners
            )
            if (right > left
                    and round(final_tone, 4) == wanted_tone
                    and owned_by_baseline_or_connector):
                visible.append((left, right))
        visible = _merge_intervals(visible, SOURCE_COORD_EPS_PT)
        if not any(
                left <= raw_left + SOURCE_COORD_EPS_PT
                and right >= raw_right - SOURCE_COORD_EPS_PT
                for left, right in visible):
            continue
        spans.append(_SourceBaselineSpan(
            y=sample_y,
            y0=paint.y0,
            y1=paint.y1,
            left=raw_left,
            right=raw_right,
            operations=(lineage_operation,),
            segments=((raw_left, raw_right, paint.y0, paint.y1),),
        ))
    return spans


def _components_cut_at_source_walls(
        page: VectorPage,
        tone: float,
        band_y0: float,
        band_y1: float,
        components: Sequence[_SourceBaselineSpan],
        ) -> list[_SourceBaselineSpan]:
    """Each baseline chain, plus the cells the source's own walls cut it into.

    A chain is a run of ink, not a box. On 2200-A the comb's baseline is the
    bottom rule of a full-width table row and runs from x=15.6 to x=596.04,
    so the run's endpoints are page furniture and the rails that bound the
    comb -- x=392.71 and x=595.32 -- are interior to it. Requiring the outer
    rails to stand on the chain's own ends therefore rejected a comb the sheet
    prints perfectly clearly.

    Cutting at walls fixes that without loosening the endpoint requirement:
    each cut is a source-painted box edge (``_carries_band_into_rule_above``),
    the cell's ends are that edge, and a rail must still stand exactly on the
    end of the piece it bounds. The uncut chain is retained beside the pieces,
    so a comb whose baseline really does end at its rails is unaffected.
    """
    expanded: list[_SourceBaselineSpan] = []
    for component in components:
        expanded.append(component)
        cuts = _source_wall_partition(
            page, tone, band_y0, band_y1,
            component.left, component.right, component)
        if len(cuts) < 3:
            continue
        for cut_left, cut_right in zip(cuts, cuts[1:]):
            if cut_right - cut_left <= 2 * COMB_MERGE_PT:
                continue
            segments = tuple(sorted({
                (max(left, cut_left), min(right, cut_right), y0, y1)
                for left, right, y0, y1 in _baseline_segments(component)
                if right > cut_left and left < cut_right
            }))
            if not segments:
                continue
            expanded.append(_SourceBaselineSpan(
                y=component.y,
                y0=min(segment[2] for segment in segments),
                y1=max(segment[3] for segment in segments),
                left=cut_left,
                right=cut_right,
                operations=component.operations,
                segments=segments,
            ))
    return expanded


def _rule_ink_meets(
        left_x0: float, left_x1: float, left_y0: float, left_y1: float,
        right_x0: float, right_x1: float, right_y0: float, right_y1: float,
        ) -> bool:
    """Do two collinear rule segments' ink meet, at the ink's own scale?

    Overlap and exact touch always meet. Beyond that the only admitted gap is
    one strictly narrower than the thinner of the two strokes: a break that
    cannot be as wide as the rule is thick cannot be seen as a break in it.
    """
    gap = max(right_x0 - left_x1, left_x0 - right_x1, 0.0)
    if gap <= SOURCE_COORD_EPS_PT:
        return True
    return gap < min(left_y1 - left_y0, right_y1 - right_y0)


def _segmented_u_frame_candidates(
        page: VectorPage,
        baselines: Sequence[_SourceBaselineSpan],
        band_y0: float,
        band_y1: float,
        tone: float,
        topology: Sequence[float],
        ) -> list[
            tuple[
                float, float, tuple[float, ...], _SourceBaselineSpan,
                tuple[float, ...], tuple[tuple[int, int], ...],
            ]
        ]:
    """Build maximal pitch-coherent frames over explicitly painted segments.

    Some official comb baselines are emitted as one horizontal operation per
    compartment. Their raw endpoints are genuine, but the full frame is the
    maximal source-owned chain, not whichever subset a claimed bbox clips out.
    A large non-comb table cell sharing that y is separated by its incompatible
    pitch, while group-separator variation remains inside a 30% source-derived
    pitch envelope.

    Segments join where their ink meets, and the bound on "meets" is the ink's
    own thickness rather than a fixed slack. Two official sheets leave a gap in
    an otherwise exact chain -- 2000-DST at x=206.324/206.330 and x=473.134/
    473.140, 2200-A at x=464.014/464.020 -- 0.006pt of paper interrupting a
    0.48pt rule, one twentieth of a pixel at 600dpi. A break narrower than the
    stroke it interrupts cannot print as a break, so it is not one; a gap as
    wide as the rule is thick still separates two rules, which is what keeps a
    missing junction block from being bridged into existence.
    """
    remaining = list(sorted(
        baselines,
        key=lambda item: (
            item.left, item.right, item.y0, item.y1, item.operations,
        ),
    ))
    connected_groups: list[list[_SourceBaselineSpan]] = []
    while remaining:
        group = [remaining.pop(0)]
        changed = True
        while changed:
            changed = False
            retained: list[_SourceBaselineSpan] = []
            for candidate in remaining:
                connected = any(
                    any(
                        (
                            _rule_ink_meets(
                                candidate_left, candidate_right,
                                candidate_y0, candidate_y1,
                                member_left, member_right,
                                member_y0, member_y1,
                            )
                            and candidate_y0
                            <= member_y1 + SOURCE_COORD_EPS_PT
                            and candidate_y1
                            >= member_y0 - SOURCE_COORD_EPS_PT
                        )
                        for (
                            candidate_left, candidate_right,
                            candidate_y0, candidate_y1,
                        ) in _baseline_segments(candidate)
                        for (
                            member_left, member_right,
                            member_y0, member_y1,
                        ) in _baseline_segments(member)
                    )
                    for member in group
                )
                if connected:
                    group.append(candidate)
                    changed = True
                else:
                    retained.append(candidate)
            remaining = retained
        connected_groups.append(group)

    components: list[_SourceBaselineSpan] = []
    for group in connected_groups:
        segments = tuple(sorted({
            segment
            for item in group
            for segment in _baseline_segments(item)
        }))
        components.append(_SourceBaselineSpan(
            y=sum(item.y for item in group) / len(group),
            y0=min(segment[2] for segment in segments),
            y1=max(segment[3] for segment in segments),
            left=min(segment[0] for segment in segments),
            right=max(segment[1] for segment in segments),
            operations=tuple(sorted({
                operation
                for item in group
                for operation in item.operations
            })),
            segments=segments,
        ))

    candidates = []
    for component in _components_cut_at_source_walls(
            page, tone, band_y0, band_y1, components):
        if component.y0 <= band_y0 + SOURCE_COORD_EPS_PT:
            continue
        verticals = _stable_source_verticals(
            page,
            component.left - COMB_MAX_WIDTH_PT,
            component.right + COMB_MAX_WIDTH_PT,
            band_y0,
            component.y0,
            tone,
        )
        verticals = sorted(
            value for value in verticals
            if (value >= component.left - COMB_MAX_WIDTH_PT
                and value <= component.right + COMB_MAX_WIDTH_PT)
        )
        matched_indexes = [
            index for index, value in enumerate(verticals)
            if any(
                abs(value - divider) <= COMB_MERGE_PT
                for divider in topology
            )
        ]
        if not matched_indexes:
            continue

        if len(matched_indexes) == 1:
            index = matched_indexes[0]
            if index == 0 or index + 1 >= len(verticals):
                continue
            left_gap = verticals[index] - verticals[index - 1]
            right_gap = verticals[index + 1] - verticals[index]
            pitch = (left_gap + right_gap) / 2
            tolerance = max(COMB_MERGE_PT, 0.3 * pitch)
            if abs(left_gap - right_gap) > tolerance:
                continue
            start, end = index - 1, index + 1
        else:
            differences = sorted(
                verticals[right] - verticals[left]
                for left, right in zip(
                    matched_indexes, matched_indexes[1:])
            )
            pitch = differences[len(differences) // 2]
            tolerance = max(COMB_MERGE_PT, 0.3 * pitch)
            runs: list[list[int]] = [[matched_indexes[0]]]
            for index in matched_indexes[1:]:
                gap = verticals[index] - verticals[runs[-1][-1]]
                if abs(gap - pitch) <= tolerance:
                    runs[-1].append(index)
                else:
                    runs.append([index])
            longest = max(
                runs,
                key=lambda run: (
                    len(run), verticals[run[-1]] - verticals[run[0]],
                    -verticals[run[0]],
                ),
            )
            start, end = longest[0], longest[-1]

        while start > 0:
            gap = verticals[start] - verticals[start - 1]
            if abs(gap - pitch) > tolerance:
                break
            start -= 1
        while end + 1 < len(verticals):
            gap = verticals[end + 1] - verticals[end]
            if abs(gap - pitch) > tolerance:
                break
            end += 1
        run = verticals[start:end + 1]
        if len(run) < 3:
            continue
        left, right = run[0], run[-1]
        run_geometry = {
            source_x: _source_vertical_ink_geometry(
                page, source_x, band_y0, band_y1, tone)
            for source_x in run
        }
        contact_coordinates = (
            [(left, component.left)]
            + [(source_x, source_x) for source_x in run[1:-1]]
            + [(right, component.right)]
        )
        if any(
                not _vertical_has_connected_baseline_contact(
                    page,
                    tone,
                    run_geometry[source_x],
                    band_y0,
                    contact_x,
                    component,
                )
                for source_x, contact_x in contact_coordinates
                ):
            continue
        interior = tuple(
            divider for divider in topology
            if (divider > left + COMB_MERGE_PT
                and divider < right - COMB_MERGE_PT
                and any(
                    abs(divider - source_x) <= COMB_MERGE_PT
                    for source_x in run[1:-1]
                ))
        )
        if not interior:
            continue
        external = tuple(
            divider for divider in topology
            if (divider < left - COMB_MERGE_PT
                or divider > right + COMB_MERGE_PT)
        )
        candidates.append((
            left,
            right,
            interior,
            component,
            external,
            component.operations,
        ))
    return candidates


def _erasure_ends_run(owners: Sequence[VectorPaint]) -> bool:
    """Is the break in this stroke PAINTED, rather than merely unreached?

    The two look identical from the stroke's end and are opposite facts.
    2200-A knocks the rule above its comb out with a white rectangle 0.48pt
    tall across a 0.48pt stroke: that is the sheet saying the stroke stops
    there, at any size. 1700's walls simply miss each other by 0.006pt with
    nothing drawn in between: that is two operations meeting, and the paper
    in the gap was never claimed by anybody.
    """
    return bool(owners)


def _stroke_break_ends_run(
        gap: float, reachable: Sequence[tuple[float, float]]) -> bool:
    """Is a break ALONG a stroke long enough to read as a break in it?

    ``_rule_ink_meets`` turned through a right angle. The stroke's own width
    is the scale: 1700 draws each date-box wall as two operations that miss
    each other by 0.006pt across a 0.24pt stroke, and no printer resolves
    that. A break as long as the stroke is wide still ends the run, which is
    what stops a divider from reaching over the paper above it and claiming
    the rule that closes some other box.
    """
    return gap > min(right - left for left, right in reachable)


def _carries_band_into_rule_above(
        page: VectorPage, tone: float, rail: dict[str, Any] | None,
        band_y0: float) -> bool:
    """Does this vertical carry the comb band up into a rule above it?

    This is the source's own difference between a compartment divider and a
    wall, and it is visible in the content stream without asking anybody what
    the box is supposed to be. A divider hangs from the baseline and stops
    inside the field: above the band there is paper (1600WP's date ticks stop
    at the box's white fill), or a knockout (2200-A erases the rule above its
    comb), or nothing at all. A wall carries on until it joins the rule that
    closes the box above -- and joining a rule is exactly what shows up here,
    because at that level the connected same-tone ink stops being a stroke and
    starts running sideways.

    The walk is slab by slab upward from the band, keeping only ink connected
    to what the slab below reached, so an unrelated rule crossing this x at
    some higher level cannot be claimed by a divider that never reaches it.
    "Runs sideways" is measured against ``COMB_MAX_WIDTH_PT``, already this
    file's bound on how wide a comb stroke may be: a 1.44pt thousands
    separator stays a stroke, a rule does not.

    A wall is often two operations meeting at the band's own top edge, and
    official sheets miss that meeting by a hair -- 1700's date-box walls stop
    0.006pt above the band their own ticks start at. The gap bound is the same
    one ``_rule_ink_meets`` applies along a rule, turned through a right
    angle: a break shorter than the stroke is wide cannot print as a break.
    Anything longer ends the walk, which is what keeps a divider from
    claiming the rule that happens to run above the field it stops inside.
    """
    if rail is None:
        return False
    centre_x = float(rail["center_x"])
    seed = (float(rail["ink_x0"]), float(rail["ink_x1"]))
    wanted_tone = round(tone, 4)
    window_x0 = centre_x - COMB_MAX_WIDTH_PT
    window_x1 = centre_x + COMB_MAX_WIDTH_PT
    relevant = [
        paint for paint in page.paints
        if paint.x1 >= window_x0
        and paint.x0 <= window_x1
        and paint.y0 < band_y0 - SOURCE_COORD_EPS_PT
    ]
    if not relevant:
        return False
    edges = {band_y0}
    for paint in relevant:
        edges.add(paint.y0)
        edges.add(min(paint.y1, band_y0))
    slabs = [
        (a, b)
        for a, b in zip(sorted(edges), sorted(edges)[1:])
        if b - a > SOURCE_COORD_EPS_PT and b <= band_y0 + SOURCE_COORD_EPS_PT
    ]
    reachable: list[tuple[float, float]] = [seed]
    last_ink_y = band_y0
    for a, b in sorted(slabs, reverse=True):
        sample_y = (a + b) / 2.0
        active = [
            paint for paint in relevant
            if paint.y0 <= sample_y <= paint.y1
        ]
        x_edges = {window_x0, window_x1}
        for paint in active:
            clipped_left = max(window_x0, paint.x0)
            clipped_right = min(window_x1, paint.x1)
            if clipped_right >= clipped_left:
                x_edges.update((clipped_left, clipped_right))
        visible: list[tuple[float, float]] = []
        ordered_x = sorted(x_edges)
        for left, right in zip(ordered_x, ordered_x[1:]):
            if right <= left:
                continue
            final_tone = _final_tone(active, (left + right) / 2.0, sample_y)
            if round(final_tone, 4) == wanted_tone:
                visible.append((left, right))
        visible = _merge_intervals(visible, SOURCE_COORD_EPS_PT)
        connected = [
            interval for interval in visible
            if any(
                interval[0] <= prior[1] + SOURCE_COORD_EPS_PT
                and interval[1] >= prior[0] - SOURCE_COORD_EPS_PT
                for prior in reachable
            )
        ]
        if not connected:
            if _erasure_ends_run(
                    _final_tone_and_owner(active, centre_x, sample_y)[1]):
                return False
            continue
        if _stroke_break_ends_run(last_ink_y - b, reachable):
            return False
        if any(right - left > COMB_MAX_WIDTH_PT for left, right in connected):
            return True
        reachable = connected
        last_ink_y = a
    return False


def _stroke_column_break_is_erased(
        page: VectorPage,
        ink_x0: float, ink_x1: float,
        gap_y0: float, gap_y1: float,
        ) -> bool:
    """Is this break in a stroke column an erasure OF THAT STROKE?

    ``_erasure_ends_run`` asks whether a break was painted; this asks whose
    break it is, and the sheet answers in the erasure's own width. 2000-DST
    stops its middle wall 1.56pt above the comb band with a white rectangle at
    x 192.14..192.62 -- exactly the 0.48pt stroke it covers and nothing else --
    and 2200-C does the same at x 30.60..31.08 over its own 0.48pt stroke. An
    erasure cut to one stroke's width is that stroke's own edit, so the ink
    above it belongs to the same column. Paint that reaches beyond the stroke
    is somebody else's: a grey band, a knocked-out row, a white field fill, all
    of which cross this x on their way somewhere else and none of which say
    anything about what stands above. Requiring full opacity keeps a
    see-through overlay from bridging a column it cannot actually replace.

    Bounding the bridge by the stroke's own width is also what keeps the reach
    below finite without a magic window: to walk further up the page the sheet
    has to have deliberately erased every point of the way at this exact width.
    """
    covered = _merge_intervals([
        (max(gap_y0, paint.y0), min(gap_y1, paint.y1))
        for paint in page.paints
        if paint.opacity == 1.0
        and paint.x0 >= ink_x0 - SOURCE_COORD_EPS_PT
        and paint.x1 <= ink_x1 + SOURCE_COORD_EPS_PT
        and paint.y1 > gap_y0
        and paint.y0 < gap_y1
    ], SOURCE_COORD_EPS_PT)
    return any(
        low <= gap_y0 + SOURCE_COORD_EPS_PT
        and high >= gap_y1 - SOURCE_COORD_EPS_PT
        for low, high in covered
    )


def _source_stroke_column_reach(
        page: VectorPage,
        tone: float,
        rail: dict[str, Any] | None,
        band_y0: float,
        ) -> float:
    """How far above the band this vertical's OWN source strokes stand.

    The extent half of the wall relation, and the only place here that reads a
    source stroke the final composite has covered. It is deliberately narrow:
    a stroke belongs to this column only if it carries the same tone, is a
    comb vertical rather than a rule chain's junction block, and is painted
    within this stroke's own ink width -- 2000-DST's 0.48pt wall is three
    operations all at x 192.14..192.62, while the 0.48pt-square block that
    joins the rule above it to its neighbours is not a vertical at all. Breaks
    are crossed only through ``_stroke_column_break_is_erased``.

    Returned as the topmost y the column reaches, so smaller is higher; the
    band top itself when nothing stands above it.
    """
    if rail is None:
        return band_y0
    ink_x0 = float(rail["ink_x0"])
    ink_x1 = float(rail["ink_x1"])
    wanted_tone = round(tone, 4)
    column = _merge_intervals([
        (paint.y0, paint.y1)
        for paint in page.paints
        if _is_comb_vertical(paint)
        and round(paint.tone, 4) == wanted_tone
        and paint.x0 >= ink_x0 - SOURCE_COORD_EPS_PT
        and paint.x1 <= ink_x1 + SOURCE_COORD_EPS_PT
    ], SOURCE_COORD_EPS_PT)
    reach = band_y0
    while True:
        above = [
            interval for interval in column
            if interval[0] < reach - SOURCE_COORD_EPS_PT
        ]
        if not above:
            return reach
        low, high = max(above, key=lambda interval: (interval[1], interval[0]))
        if (high < reach - SOURCE_COORD_EPS_PT
                and not _stroke_column_break_is_erased(
                    page, ink_x0, ink_x1, high, reach)):
            return reach
        reach = low


def _stroke_stands_at_border_weight(
        page: VectorPage,
        tone: float,
        rail: dict[str, Any] | None,
        band_y0: float,
        divider_weight: float,
        divider_reach: float,
        ) -> bool:
    """Is this interior vertical drawn as a box side rather than a divider?

    ``_carries_band_into_rule_above`` reads the wall's own connection to the
    rule that closes the box, which is the strongest witness there is and stays
    first. It cannot answer when the sheet erases that connection: 2000-DST and
    2200-C both stop their walls a point and a half short of the comb band, so
    the wall is final-visible for the whole band and cut off just above it,
    exactly as 2200-A's knocked-out DIVIDER is. Ink presence above the cut
    therefore decides nothing, and the sheets separate the two cases on how the
    strokes are DRAWN instead -- both axes, never one:

    | sheet, band                 | dividers stand      | the odd stroke      |
    | --------------------------- | ------------------- | ------------------- |
    | 2000-DST p1, y122.18-129.02 | 4 x 0.24pt, 1.08pt  | 0.48pt, 12.00pt     |
    |                             | above the band      | above the band      |
    | 2200-C p1, y126.50-132.14   | 1 x 0.24pt, 1.08pt  | 0.48pt, 10.80pt     |
    |                             | above the band      | above the band      |
    | 2200-A p1, erased divider   | 0.24pt              | 0.24pt (same)       |
    | 2551-M p1, y800.28-803.88   | 0.72pt              | 0.72pt (same)       |
    | 2200-C p1, y403.49-409.61   | 0.24pt, NOT above   | 0.48pt, 9.60pt      |
    |   (a money row)             | the band at all     | above the band      |

    Twice the weight and ten times the reach on the two sheets that partition;
    no weight separation at all on the two whose odd stroke is a divider. So a
    wall is a stroke strictly THICKER than the thinnest strokes of its own span
    AND standing strictly HIGHER than every one of them. Either test alone is a
    known misreading: weight alone promotes a money comb's thousands separator,
    which is thicker than its digit dividers and hangs from the same baseline to
    the same height, into a box wall; reach alone promotes 2200-A's divider,
    which is cut off from the rule above by a knockout as thick as itself.

    The last row is why the dividers, and not the band, must supply the scale.
    2200-C's money rows carry real column walls -- 0.48pt, 9.6pt above the band,
    bounding the sheet's grey N/A blocks -- beside digit dividers that stop dead
    at the band's own top edge. With no divider standing above the band the
    sheet has said nothing about how high a divider may stand, "higher than
    every divider" collapses into "above the band at all", and that is the
    reading 2200-A refutes. So the comparison is refused outright unless the
    dividers themselves demonstrate the height a divider reaches. A span whose
    verticals are all one weight, or whose dividers all stop at the band, yields
    no wall here: the frame stays whole and an owner inside it stays
    unevaluable, which is the fail-closed direction.
    """
    if rail is None or divider_reach >= band_y0 - SOURCE_COORD_EPS_PT:
        return False
    thickness = float(rail["ink_x1"]) - float(rail["ink_x0"])
    if thickness <= divider_weight + SOURCE_COORD_EPS_PT:
        return False
    return (_source_stroke_column_reach(page, tone, rail, band_y0)
            < divider_reach - SOURCE_COORD_EPS_PT)


def _source_wall_partition(
        page: VectorPage,
        tone: float,
        band_y0: float,
        band_y1: float,
        left: float,
        right: float,
        baseline: _SourceBaselineSpan,
        ) -> list[float]:
    """The source's own walls inside one U-frame, left rail to right rail.

    A U-frame proves a rail pair and a baseline. It does not prove that the
    span between them is ONE box: 1600WP's date field is a single stroked
    rectangle carrying three sub-boxes (MM, DD, YYYY) on full-height interior
    verticals, and 1707 draws the same shape as one row rule with the box
    walls standing on it. Each interior wall is source paint that reaches both
    the baseline and the rule above (``_carries_band_into_rule_above``), or is
    drawn to border weight and stands above every divider of the same span
    (``_stroke_stands_at_border_weight``) where the sheet has erased that
    reach. Either way the partition is read off the page, never off a claimed
    rectangle.
    """
    interior: list[tuple[float, dict[str, Any] | None]] = []
    for source_x in _stable_source_verticals(
            page,
            left - COMB_MAX_WIDTH_PT,
            right + COMB_MAX_WIDTH_PT,
            band_y0,
            baseline.y0,
            tone):
        if (source_x <= left + COMB_MERGE_PT
                or source_x >= right - COMB_MERGE_PT):
            continue
        geometry = _source_vertical_ink_geometry(
            page, source_x, band_y0, band_y1, tone)
        if not _vertical_has_connected_baseline_contact(
                page, tone, geometry, band_y0, source_x, baseline):
            continue
        interior.append((source_x, geometry))

    # The divider population of THIS span, measured rather than assumed: the
    # thinnest strokes standing between these two rails, and the highest any
    # of them reaches. Both bounds come from the same source operators the
    # candidate walls are measured against.
    weights = [
        float(geometry["ink_x1"]) - float(geometry["ink_x0"])
        for _source_x, geometry in interior
        if geometry is not None
    ]
    divider_weight = min(weights) if weights else 0.0
    divider_reach = min(
        (
            _source_stroke_column_reach(page, tone, geometry, band_y0)
            for _source_x, geometry in interior
            if geometry is not None
            and float(geometry["ink_x1"]) - float(geometry["ink_x0"])
            <= divider_weight + SOURCE_COORD_EPS_PT
        ),
        default=band_y0,
    )

    walls = sorted(
        source_x for source_x, geometry in interior
        if _carries_band_into_rule_above(page, tone, geometry, band_y0)
        or _stroke_stands_at_border_weight(
            page, tone, geometry, band_y0, divider_weight, divider_reach)
    )
    return [left, *walls, right]


def _local_baseline_spans(
        page: VectorPage, x0: float, x1: float, band_y1: float,
        tone: float,
        ) -> list[tuple[float, float, float]]:
    """Local segmented-baseline evidence used only after source-first guards."""
    wanted_tone = round(tone, 4)
    raw: list[tuple[float, float, float]] = []
    for paint in page.paints:
        width = paint.x1 - paint.x0
        height = paint.y1 - paint.y0
        centre_y = (paint.y0 + paint.y1) / 2
        if (width <= height
                or height > COMB_MAX_WIDTH_PT
                or round(paint.tone, 4) != wanted_tone
                or band_y1 < paint.y0 - COMB_YSLACK_PT
                or band_y1 > paint.y1 + COMB_YSLACK_PT
                or paint.x1 <= x0 or paint.x0 >= x1):
            continue
        raw.append((
            centre_y,
            max(x0, paint.x0),
            min(x1, paint.x1),
        ))

    y_groups: list[list[tuple[float, float, float]]] = []
    for item in sorted(raw):
        if (y_groups
                and item[0] - y_groups[-1][-1][0] <= COMB_YSLACK_PT):
            y_groups[-1].append(item)
        else:
            y_groups.append([item])
    spans: list[tuple[float, float, float]] = []
    for group in y_groups:
        sample_y = sum(item[0] for item in group) / len(group)
        raw_intervals = _merge_intervals([
            (left, right) for _centre_y, left, right in group
        ], COMB_MERGE_PT)
        active = [
            paint for paint in page.paints
            if paint.y0 <= sample_y <= paint.y1
            and paint.x1 > x0 and paint.x0 < x1
        ]
        x_edges = {x0, x1}
        for paint in active:
            x_edges.update((max(x0, paint.x0), min(x1, paint.x1)))
        visible: list[tuple[float, float]] = []
        ordered_x = sorted(x_edges)
        for left, right in zip(ordered_x, ordered_x[1:]):
            if (right > left
                    and round(_final_tone(
                        active, (left + right) / 2, sample_y), 4)
                    == wanted_tone):
                visible.append((left, right))
        visible = _merge_intervals(
            visible, verify.DEFAULT_POSITION_TOL_PT)
        group_spans: list[tuple[float, float]] = []
        for raw_left, raw_right in raw_intervals:
            for visible_left, visible_right in visible:
                left = max(raw_left, visible_left)
                right = min(raw_right, visible_right)
                if right > left:
                    group_spans.append((left, right))
        spans.extend(
            (sample_y, left, right)
            for left, right in _merge_intervals(
                group_spans, verify.DEFAULT_POSITION_TOL_PT)
        )
    return spans


def _frame_cut_at_source_walls(
        page: VectorPage,
        tone: float,
        band_y0: float,
        band_y1: float,
        x0: float,
        x1: float,
        topology: Sequence[float],
        candidate: tuple[
            float, float, tuple[float, ...], _SourceBaselineSpan,
            tuple[float, ...], tuple[tuple[int, int], ...],
        ],
        ) -> tuple[
            float, float, tuple[float, ...], _SourceBaselineSpan,
            tuple[float, ...], tuple[tuple[int, int], ...],
        ]:
    """Reduce one U-frame to the source cell of it the owner claims.

    Maximality is what stops a shrunk rectangle from nominating two of its own
    dividers as counterfeit rails, and it stays: this cuts ONLY at walls the
    source itself paints, so every endpoint offered here was drawn as a box
    edge on the sheet. What it removes is the assumption that one rail pair
    bounds one comb. Official sheets draw a row of boxes as a single rule with
    walls standing on it, and the maximal frame then spans the whole row --
    1707's MM|DD|YYYY, 1600WP's three date boxes inside one stroked rectangle.
    Reporting those as "the owner cropped a wider frame" said nothing about
    the owner and hid the compartment count behind an unevaluable verdict.

    An owner that does not coincide with one source cell keeps its original
    frame, so cropping a genuine comb and absorbing a neighbouring one are
    still reported exactly as before.
    """
    left, right, interior, baseline, _external, lineage = candidate
    cuts = _source_wall_partition(
        page, tone, band_y0, band_y1, left, right, baseline)
    if len(cuts) < 3:
        return candidate
    claimed = [
        (cut_left, cut_right)
        for cut_left, cut_right in zip(cuts, cuts[1:])
        if abs(cut_left - x0) <= COMB_MERGE_PT
        and abs(cut_right - x1) <= COMB_MERGE_PT
    ]
    if len(claimed) != 1:
        return candidate
    cut_left, cut_right = claimed[0]
    cut_interior = tuple(
        divider for divider in interior
        if divider > cut_left + COMB_MERGE_PT
        and divider < cut_right - COMB_MERGE_PT
    )
    if not cut_interior:
        return candidate
    cut_external = tuple(
        divider for divider in topology
        if (divider < cut_left - COMB_MERGE_PT
            or divider > cut_right + COMB_MERGE_PT)
    )
    return (
        cut_left, cut_right, cut_interior, baseline, cut_external, lineage)


def _source_u_frame(
        page: VectorPage, x0: float, x1: float,
        band_y0: float, band_y1: float, tone: float,
        topology: Sequence[float],
        ) -> tuple[tuple[float, ...], dict[str, Any]] | None:
    """Resolve one maximal source U-frame before trusting the claimed bbox."""
    if not topology:
        return None
    baselines = _baseline_spans(page, band_y1, tone)
    candidates: list[
        tuple[
            float, float, tuple[float, ...], _SourceBaselineSpan,
            tuple[float, ...], tuple[tuple[int, int], ...],
        ]
    ] = []
    for baseline in baselines:
        if baseline.right <= x0 or baseline.left >= x1:
            continue
        if baseline.y0 <= band_y0 + SOURCE_COORD_EPS_PT:
            continue
        verticals = _stable_source_verticals(
            page,
            baseline.left - COMB_MAX_WIDTH_PT,
            baseline.right + COMB_MAX_WIDTH_PT,
            band_y0,
            baseline.y0,
            tone,
        )
        vertical_geometry = {
            value: _source_vertical_ink_geometry(
                page, value, band_y0, band_y1, tone)
            for value in verticals
        }
        left_matches = sorted(
            (value for value in verticals
             if _vertical_has_connected_baseline_contact(
                 page, tone, vertical_geometry[value],
                 band_y0, baseline.left, baseline)),
            key=lambda value: (abs(value - baseline.left), value),
        )
        right_matches = sorted(
            (value for value in verticals
             if _vertical_has_connected_baseline_contact(
                 page, tone, vertical_geometry[value],
                 band_y0, baseline.right, baseline)),
            key=lambda value: (abs(value - baseline.right), value),
        )
        if not left_matches or not right_matches:
            continue
        left, right = left_matches[0], right_matches[0]
        if right - left <= 2 * COMB_MERGE_PT:
            continue
        interior = tuple(
            divider for divider in topology
            if divider > left + COMB_MERGE_PT
            and divider < right - COMB_MERGE_PT
            and any(
                abs(divider - source_x) <= COMB_MERGE_PT
                and _vertical_has_connected_baseline_contact(
                    page, tone, vertical_geometry[source_x],
                    band_y0, source_x, baseline)
                for source_x in verticals
            )
        )
        if not interior:
            continue
        external = tuple(
            divider for divider in topology
            if (divider < left - COMB_MERGE_PT
                or divider > right + COMB_MERGE_PT)
        )
        candidates.append((
            left, right, interior, baseline,
            external, baseline.operations,
        ))
    candidates.extend(_segmented_u_frame_candidates(
        page,
        baselines,
        band_y0,
        band_y1,
        tone,
        topology,
    ))
    if not candidates:
        return None

    candidates = [
        _frame_cut_at_source_walls(
            page, tone, band_y0, band_y1, x0, x1, topology, candidate)
        for candidate in candidates
    ]

    widest = max(
        right - left
        for left, right, _interior, _baseline, _external, _lineage
        in candidates
    )
    maximal = [
        candidate for candidate in candidates
        if abs((candidate[1] - candidate[0]) - widest)
        <= verify.DEFAULT_POSITION_TOL_PT
    ]
    interiors: list[tuple[float, ...]] = []
    for _left, _right, interior, _baseline, _external, _lineage in maximal:
        if not any(_same_topology(interior, seen) for seen in interiors):
            interiors.append(interior)
    if len(interiors) != 1:
        raise ValueError(
            "maximal same-tone source U-frames yield different interiors")
    closest = min(
        maximal,
        key=lambda item: (
            abs(item[3].y - band_y1),
            item[3].y, item[0], item[1]),
    )
    left, right, _interior, baseline, external, lineage = closest
    left_rail_geometry = _source_vertical_ink_geometry(
        page, left, band_y0, band_y1, tone)
    right_rail_geometry = _source_vertical_ink_geometry(
        page, right, band_y0, band_y1, tone)
    if left_rail_geometry is None or right_rail_geometry is None:
        return None
    rail_geometry = {
        "left": _published_vertical_geometry(
            page, tone, left_rail_geometry, baseline),
        "right": _published_vertical_geometry(
            page, tone, right_rail_geometry, baseline),
    }
    baseline_segments = [
        {
            "x0": round(segment[0], 6),
            "x1": round(segment[1], 6),
            "y0": round(segment[2], 6),
            "y1": round(segment[3], 6),
        }
        for segment in _baseline_segments(baseline)
    ]
    frame_evidence = {
        "left_rail": round(left, 6),
        "right_rail": round(right, 6),
        "rail_geometry": rail_geometry,
        "baseline_y": round(baseline.y, 6),
        "baseline_y0": round(baseline.y0, 6),
        "baseline_y1": round(baseline.y1, 6),
        "baseline_segments": baseline_segments,
        "baseline_operations": [
            list(operation) for operation in lineage
        ],
    }
    cropped_sides = []
    if left < x0 - COMB_MERGE_PT:
        cropped_sides.append("left")
    if right > x1 + COMB_MERGE_PT:
        cropped_sides.append("right")
    if cropped_sides:
        raise CombTopologyError(
            "claimed comb owner crops a wider source U-frame",
            {
                "criterion": "maximal-source-u-frame-owner",
                "owner_rect": [
                    round(x0, 6), round(band_y0, 6),
                    round(x1, 6), round(band_y1, 6),
                ],
                "frame": frame_evidence,
                "cropped_sides": cropped_sides,
            },
        )
    if external:
        raise CombTopologyError(
            "claimed comb owner absorbs unframed source corridors outside "
            "its complete source U-frame",
            {
                "criterion": "complete-source-u-frame-bounds",
                "owner_rect": [
                    round(x0, 6), round(band_y0, 6),
                    round(x1, 6), round(band_y1, 6),
                ],
                "frame": frame_evidence,
                "unframed_corridors": [
                    round(value, 6) for value in external
                ],
            },
        )
    return interiors[0], {
        "tone": round(tone, 4),
        "left_rail": rail_geometry["left"],
        "right_rail": rail_geometry["right"],
        "band_y0": round(band_y0, 6),
        "baseline_y": round(baseline.y, 6),
        "baseline_y0": round(baseline.y0, 6),
        "baseline_y1": round(baseline.y1, 6),
        "baseline_segments": baseline_segments,
        "baseline_operations": [
            list(operation) for operation in lineage
        ],
    }


def _union_span(intervals: Sequence[tuple[float, float]]) -> float:
    """Total length of a union of possibly-overlapping y intervals."""
    total = 0.0
    covered_to: float | None = None
    for y0, y1 in sorted(intervals):
        if covered_to is None or y0 > covered_to:
            total += max(0.0, y1 - y0)
            covered_to = max(covered_to or y1, y1)
        elif y1 > covered_to:
            total += y1 - covered_to
            covered_to = y1
    return total


def _dominant_certified_topology(
        results: Sequence[tuple[
            float, float, float, tuple[float, ...], dict[str, Any] | None]],
        topology_groups: Sequence[tuple[float, ...]],
        ) -> tuple[tuple[float, ...] | None, list[dict[str, Any]]]:
    """The comb referee's proven slab-disambiguation rule, mirrored.

    A thick group separator can be slightly shorter than the hairline seeds
    beside it.  The y partition then has a narrow seed-only cap and a much
    taller slab with the complete compartment topology.  That is not
    competing evidence: the longer separator still visibly divides the comb.
    Admit the richer topology only when it contains every divider of every
    other slab (within the referee's fixed position bound) and its slabs
    occupy a strict majority of the measured vertical band.  A short
    midpoint or two genuinely competing slabs yields no dominant topology.
    (comb_referee.py proves and applies this same rule; POSITION_TOL_PT is
    the same copied bound in both files.)
    """

    def contains(superset: tuple[float, ...],
                 subset: tuple[float, ...]) -> bool:
        available = [float(value) for value in superset]
        for value in subset:
            choices = sorted(
                (abs(candidate - float(value)), index)
                for index, candidate in enumerate(available)
                if abs(candidate - float(value)) <= POSITION_TOL_PT
            )
            if not choices:
                return False
            _distance, index = choices[0]
            available.pop(index)
        return True

    group_intervals: list[list[tuple[float, float]]] = [
        [] for _group in topology_groups]
    all_intervals: list[tuple[float, float]] = []
    for band_y0, band_y1, _tone, topology, _frame in results:
        all_intervals.append((band_y0, band_y1))
        for index, group in enumerate(topology_groups):
            if _same_topology(topology, group):
                group_intervals[index].append((band_y0, band_y1))
                break
    total_span = _union_span(all_intervals)
    coverage = [_union_span(intervals) for intervals in group_intervals]
    relations: list[dict[str, Any]] = []
    for index, candidate in enumerate(topology_groups):
        for other_index, other in enumerate(topology_groups):
            if index == other_index:
                continue
            relations.append({
                "candidate": [round(value, 6) for value in candidate],
                "other": [round(value, 6) for value in other],
                "contains": contains(candidate, other),
                "proper": (
                    len(candidate) > len(other)
                    and contains(candidate, other)
                ),
                "candidate_coverage_pt": round(coverage[index], 6),
                "measured_band_span_pt": round(total_span, 6),
            })
    dominant = [
        index for index, candidate in enumerate(topology_groups)
        if all(
            other_index == index
            or (len(candidate) > len(topology_groups[other_index])
                and contains(candidate, topology_groups[other_index]))
            for other_index in range(len(topology_groups))
        )
        and coverage[index] * 2 > total_span
    ]
    if len(dominant) != 1:
        return None, relations
    return topology_groups[dominant[0]], relations


def printed_compartments(
        page: VectorPage,
        cell: dict[str, Any],
        *,
        include_frame: bool = False,
        owner_certificate: CombOwnerCertificate | None = None,
        ) -> tuple[int, list[float]] | tuple[
            int, list[float], dict[str, Any] | None]:
    """Count the source's final visible divider topology inside one comb.

    The lattice supplies only an exact, reviewed owner identity and rectangle.
    Candidate vertical bands, tones, and every divider come from raw source
    paint within (or crossing) that cell.  No member of the lattice's `comb`
    object or subject topology is read.  A complete source U-frame owns its
    interior directly.  Without one, the reviewed certificate may establish
    only *whose* rectangle this is, and only one unanimous source-derived
    topology -- or the comb referee's proven dominant, a richer topology
    containing every other slab that occupies a strict majority of the
    measured band -- can be used.  Genuinely competing topology stays
    unevaluable.
    """
    try:
        x0, y0 = float(cell["x0"]), float(cell["y0"])
        x1, y1 = float(cell["x1"]), float(cell["y1"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("comb owner geometry is incomplete") from exc
    if not all(math.isfinite(value) for value in (x0, y0, x1, y1)):
        raise ValueError("comb owner geometry is non-finite")
    if x1 <= x0 or y1 <= y0:
        raise ValueError("comb owner has no positive area")
    if (owner_certificate is not None
            and not owner_certificate.matches(
                int(owner_certificate.page), cell)):
        raise ValueError(
            "comb owner certificate does not bind this exact cell identity")

    owner = (x0, y0, x1, y1)
    # W5 mechanism 1: a chromatic fill the extractor could parse exactly
    # rectilinear (`exact_regions` populated -- see `_rectilinear_fill_regions`)
    # is promoted from refused paint to ordinary paint for this comb's OWN
    # evaluation only. This never touches `page.paints`/`page.unsupported`
    # themselves -- `page` is rebound to a new `VectorPage` local to this
    # call, so `inputs_over_printed_text` and every other reader of the
    # shared bundle-wide vector page is unaffected. Scoped to paint
    # intersecting the owner rect: paint that never comes near this comb
    # cannot change what it prints.
    chromatic_promotions = [
        unsupported for unsupported in page.unsupported
        if unsupported.reason == "chromatic vector fill"
        and unsupported.exact_regions
        and unsupported.tone is not None
        and _rects_intersect(owner, unsupported.rect)
    ]
    if chromatic_promotions:
        promoted_paints = tuple(
            VectorPaint(
                *region_rect, float(item.tone),
                item.opacity if item.opacity is not None else 1.0,
                item.order, "fill-region", item.order, item.fill_rule,
                winding,
            )
            for item in chromatic_promotions
            for region_rect, winding in item.exact_regions
        )
        promoted = set(chromatic_promotions)
        page = dataclasses.replace(
            page,
            paints=tuple(sorted(
                (*page.paints, *promoted_paints),
                key=lambda paint: (
                    paint.order, paint.operation, paint.kind,
                    paint.x0, paint.y0, paint.x1, paint.y1,
                    paint.fill_rule, paint.winding),
            )),
            unsupported=tuple(
                item for item in page.unsupported
                if item not in promoted
            ),
        )
    bands, first_source_order = _source_band_candidates(page, owner)
    if not bands:
        relevant = sorted({
            unsupported.reason for unsupported in page.unsupported
            if _rects_intersect(owner, unsupported.rect)
        })
        suffix = f": {', '.join(relevant)}" if relevant else ""
        raise ValueError(f"no plausible source-derived comb band{suffix}")

    results: list[
        tuple[
            float, float, float, tuple[float, ...],
            dict[str, Any] | None,
        ]
    ] = []
    text_reasons = {
        "unmodeled source fill-text paint",
        "unmodeled source stroke-text paint",
    }
    image_reason = "unmodeled source fill-image paint"
    stroke_reason = "non-rectilinear vector stroke"
    deferred_reasons = {*text_reasons, image_reason, stroke_reason}
    image_hits = sorted(
        (
            unsupported for unsupported in page.unsupported
            if unsupported.reason == image_reason
            and any(_rects_intersect(
                (x0, band_y0, x1, band_y1), unsupported.rect)
                for band_y0, band_y1 in bands)
        ),
        key=lambda item: (item.order, item.rect),
    )
    if image_hits:
        raise CombTopologyError(
            "unmodeled source fill-image paint intersects a plausible "
            "source-derived comb band",
            {
                "criterion": "source-comb-band-image-free-required",
                "owner_rect": [
                    round(x0, 6), round(y0, 6),
                    round(x1, 6), round(y1, 6),
                ],
                "candidate_bands": [
                    [round(band_y0, 6), round(band_y1, 6)]
                    for band_y0, band_y1 in bands
                ],
                "image_paint": [
                    {
                        "order": hit.order,
                        "rect": [round(value, 6) for value in hit.rect],
                    }
                    for hit in image_hits
                ],
                **({
                    "owner_certificate": owner_certificate.evidence(),
                } if owner_certificate is not None else {}),
            },
        )
    blocked: set[str] = set()

    def process_band(band_y0: float, band_y1: float) -> None:
        subject = (x0, band_y0, x1, band_y1)
        nonforeign_hits = [
            unsupported for unsupported in page.unsupported
            if _rects_intersect(subject, unsupported.rect)
            and unsupported.reason not in deferred_reasons
        ]
        if nonforeign_hits:
            blocked.update(hit.reason for hit in nonforeign_hits)
            return
        for tone, topology in _band_topologies(
                page, x0, x1, band_y0, band_y1):
            # W5 mechanism 2: a non-rectilinear (bezier/diagonal) stroke's own
            # bounding geometry -- pymupdf's extrema-derived `rect`, already
            # exact and already computed at extraction time -- is what decides
            # whether it can BE one of this band's dividers, exactly as an
            # unmodeled text paint's rect already decides for `text_hits`
            # below. One that never straddles a divider this band's own
            # rectilinear ink establishes is refuted as a divider candidate,
            # not left unevaluable; one that does still blocks exactly as
            # before -- this never admits a curve as ink, it only ever
            # excuses one that cannot be a divider from blocking the ones
            # that are.
            stroke_hits = [
                unsupported for unsupported in page.unsupported
                if unsupported.reason == stroke_reason
                and _rects_intersect(subject, unsupported.rect)
                and any(unsupported.rect[0] <= divider_x <= unsupported.rect[2]
                        for divider_x in topology)
            ]
            if stroke_hits:
                blocked.update(hit.reason for hit in stroke_hits)
                continue
            text_hits = [
                unsupported for unsupported in page.unsupported
                if unsupported.reason in text_reasons
                and first_source_order is not None
                and unsupported.order > first_source_order
                and _rects_intersect(subject, unsupported.rect)
                and any(unsupported.rect[0] <= divider_x <= unsupported.rect[2]
                        for divider_x in topology)
                and not (
                    unsupported.reason in {
                        "unmodeled source fill-text paint",
                        "unmodeled source stroke-text paint",
                    }
                    and unsupported.tone is not None
                    and unsupported.opacity == 1.0
                    and bool(unsupported.trace_rects)
                    and round(unsupported.tone, 4) == tone
                    and all(
                        trace_rect[2] < divider_x - COMB_MERGE_PT
                        or trace_rect[0] > divider_x + COMB_MERGE_PT
                        for trace_rect in unsupported.trace_rects
                        for divider_x in topology
                    )
                )
            ]
            if text_hits:
                blocked.update(hit.reason for hit in text_hits)
                continue
            frame = _source_u_frame(
                page, x0, x1, band_y0, band_y1, tone, topology)
            if frame is None:
                normalized = topology
                frame_key = None
            else:
                normalized, frame_key = frame
            results.append((
                band_y0, band_y1, tone, normalized, frame_key))

    for band_y0, band_y1 in bands:
        process_band(band_y0, band_y1)

    # W5 mechanism 4: F064 lets a candidate band own the full painted extent
    # of a vertical mark so a genuinely shared multi-row divider is read as
    # one continuous topology. The strict-majority test inside
    # `_band_topologies` must then clear that FULL extent, which is right
    # when the extent really is one row's own comb and wrong when the
    # claimed owner is only a fraction of a taller seed -- a short comb row
    # whose own divider ink sits inside a much taller shared column rule.
    # Retried only when the unclipped pass decided nothing at all and hit no
    # blocking paint either: a band clipped to the claimed owner's own
    # rectangle is measured by the exact same strict-majority rule in
    # `_band_topologies`, unmodified, just windowed to the row actually being
    # asked about instead of every row the same physical stroke also serves.
    if not blocked and not results:
        clipped_bands = sorted({
            (max(band_y0, y0), min(band_y1, y1))
            for band_y0, band_y1 in bands
            if max(band_y0, y0) < min(band_y1, y1) - COMB_MINLEN_PT
        } - set(bands))
        for band_y0, band_y1 in clipped_bands:
            process_band(band_y0, band_y1)

    if blocked:
        raise ValueError(
            "unsupported source paint intersects a plausible source-derived "
            f"comb band: {', '.join(sorted(blocked))}")
    if not results:
        raise CombTopologyError(
            "plausible source-derived bands have no strict-majority topology",
            {
                "criterion": "continuous-final-source-owner-strict-majority",
                "bands": [
                    {
                        "y0": round(band_y0, 6),
                        "y1": round(band_y1, 6),
                        "vertical_lineages": _vertical_lineage_diagnostics(
                            page, x0, x1, band_y0, band_y1),
                    }
                    for band_y0, band_y1 in bands
                ],
            },
        )

    topology_groups: list[tuple[float, ...]] = []
    for _band_y0, _band_y1, _tone, topology, _frame in sorted(
            results, key=lambda item: item[:4]):
        if not any(_same_topology(topology, seen) for seen in topology_groups):
            topology_groups.append(topology)

    # A U-frame is two continuous source-owned rails plus a same-tone source
    # baseline. It proves ownership without preferring whichever band happens
    # to have more dividers. Near-identical seed bands can rediscover the same
    # frame, so collapse only those; two distinct complete frames are ambiguous.
    frame_groups: list[
        tuple[
            dict[str, Any],
            tuple[float, ...],
        ]
    ] = []
    for _a, _b, _tone, topology, frame_key in results:
        if frame_key is None:
            continue
        matched_index = next((
            index for index, item in enumerate(frame_groups)
            if (abs(item[0]["tone"] - frame_key["tone"])
                <= SOURCE_COORD_EPS_PT
                and abs(
                    item[0]["left_rail"]["center_x"]
                    - frame_key["left_rail"]["center_x"]
                ) <= COMB_MERGE_PT
                and abs(
                    item[0]["right_rail"]["center_x"]
                    - frame_key["right_rail"]["center_x"]
                ) <= COMB_MERGE_PT
                and abs(item[0]["baseline_y"] - frame_key["baseline_y"])
                <= verify.DEFAULT_POSITION_TOL_PT)
        ), None)
        if matched_index is None:
            frame_groups.append((frame_key, topology))
            continue
        matched_key, matched_topology = frame_groups[matched_index]
        if _same_topology(matched_topology, topology):
            continue
        if _topology_subset(matched_topology, topology):
            # One seed band can see only the dividers that continue through the
            # whole composite cell. The same physical rails/baseline own every
            # source corridor that meets them, so retain the exhaustive
            # superset rather than treating omission as a second frame.
            frame_groups[matched_index] = (matched_key, topology)
        elif not _topology_subset(topology, matched_topology):
            raise ValueError(
                "one source U-frame yields incomparable interior topologies")

    if len(frame_groups) > 1:
        counts = sorted({len(topology) + 1 for topology in topology_groups})
        framed_counts = sorted({
            len(topology) + 1 for _frame, topology in frame_groups
        })
        raise ValueError(
            "multiple complete source U-frames compete "
            f"(compartment counts {counts}; U-frames {framed_counts})")
    # Certified ownership admits the comb referee's proven disambiguation:
    # one richer topology that contains every other slab within the fixed
    # position bound and occupies a strict majority of the measured band.
    # Everything short of that stays competing, i.e. unevaluable.
    dominant_topology: tuple[float, ...] | None = None
    superset_relations: list[dict[str, Any]] = []
    if owner_certificate is not None and len(topology_groups) > 1:
        dominant_topology, superset_relations = _dominant_certified_topology(
            results, topology_groups)
    if len(frame_groups) != 1:
        counts = sorted({len(topology) + 1 for topology in topology_groups})
        if owner_certificate is not None:
            chosen_unframed = (
                topology_groups[0] if len(topology_groups) == 1
                else dominant_topology
            )
            if chosen_unframed is not None:
                result = (len(chosen_unframed) + 1,
                          [float(value) for value in chosen_unframed])
                if not include_frame:
                    return result
                return (*result, None)
        criterion = (
            "unanimous-source-derived-topology-required"
            if owner_certificate is not None
            else "independent-complete-source-u-frame-required"
        )
        reason = (
            "reviewed comb owner has competing source-derived band/tone "
            "topologies"
            if owner_certificate is not None
            else "plausible source-derived band/tone choices disagree without "
                 "one complete source U-frame owner"
        )
        raise CombTopologyError(
            f"{reason} (compartment counts {counts}; U-frames [])",
            {
                "criterion": criterion,
                "owner_rect": [
                    round(x0, 6), round(y0, 6),
                    round(x1, 6), round(y1, 6),
                ],
                "unframed_compartment_counts": counts,
                **({
                    "topology_superset_relations": superset_relations,
                } if superset_relations else {}),
                **({
                    "owner_certificate": owner_certificate.evidence(),
                } if owner_certificate is not None else {}),
            },
        )
    frame_key, chosen = frame_groups[0]
    if (dominant_topology is not None
            and not _same_topology(chosen, dominant_topology)):
        # Dominance required containing every other slab, the framed one
        # included: the U-frame proves the rails while the richer
        # strict-majority slab supplies the interior dividers.  This is the
        # referee's thick-group-separator case, where the complete topology
        # lives in a slab the frame's seed band does not reach.
        chosen = dominant_topology
    result = (len(chosen) + 1, [float(value) for value in chosen])
    if not include_frame:
        return result
    return (*result, copy.deepcopy(frame_key))


def drawn_codepoints(page) -> dict[tuple[float, float], set[int]]:
    """Codepoint(s) the page draws at each glyph origin.

    `get_texttrace()` reports what the font's encoding actually yields, U+FFFD
    included; `get_text("rawdict")` -- which extract.py reads -- substitutes a
    plausible character when the encoding fails. Comparing the two at the origin
    is how an invented character is caught, since the origin is the one thing
    both views agree on.
    """
    seen: dict[tuple[float, float], set[int]] = {}
    for span in page.get_texttrace():
        for char in span["chars"]:
            key = (round(char[2][0], 2), round(char[2][1], 2))
            seen.setdefault(key, set()).add(char[0])
    return seen


@dataclasses.dataclass(frozen=True)
class SourceGlyph:
    """One glyph the source page's own text operators draw, and where."""

    text: str
    x0: float
    y0: float
    x1: float
    y1: float


def drawn_glyph_boxes(page) -> tuple[SourceGlyph, ...]:
    """Every visible glyph the page draws, with the box the file gives it.

    The same `get_texttrace()` view `drawn_codepoints` reads, for the same
    reason: it reports what the font's encoding actually yields rather than
    rawdict's plausible substitute, and it is the source file's own operator
    stream rather than any IR derived from it. A glyph whose encoding failed
    arrives as U+FFFD and is simply not alphanumeric, so it can never be read
    as a printed constant.

    Whitespace and degenerate boxes are dropped: a space occupies a
    compartment the way an empty compartment does, which is to say not at all.
    """
    glyphs: list[SourceGlyph] = []
    for span in page.get_texttrace():
        for char in span.get("chars") or ():
            if len(char) < 4:
                continue
            text = chr(char[0]) if char[0] else ""
            if not text.strip():
                continue
            box = tuple(float(value) for value in char[3])
            if not all(math.isfinite(value) for value in box):
                continue
            if box[2] <= box[0] or box[3] <= box[1]:
                continue
            glyphs.append(SourceGlyph(text, *box))
    return tuple(glyphs)


# The sheet's own words for "this blank is not yours". Quoted from the paper,
# including 0605's missing "by" -- a match list that silently corrected the
# Bureau's typo would not match the Bureau's form.
#
# Written WITHOUT spaces because they are matched against a text operator
# stream, not against prose: `drawn_glyph_boxes` drops whitespace glyphs
# (a space occupies a compartment the way an empty compartment does), so the
# only faithful comparison is between the visible characters on both sides.
#
# ANYWHERE for the parenthetical, which 0605 sets at the end of a longer
# caption line; LINE_START for the two bottom-of-sheet band headings, because
# guide prose discusses those boxes mid-sentence ("The machine validation
# shall reflect the date of payment") and an anywhere rule would read a
# paragraph as a reservation. A caption STARTS with its subject; prose does
# not.
BUREAU_RESERVING_ANYWHERE = (
    "tobefilledupbythebir",
    "tobefilledupthebir",
    "forbiruseonly",
)
BUREAU_RESERVING_LINE_START = (
    "machinevalidation",
    "stampofreceiving",
    "stampofauthorized",
)
# A caption is set against the blank it governs. 0605's clears its BCS box by
# 5.3pt against its own 8.1pt line, so the bound is the caption's OWN height:
# one line of separation at most, measured rather than chosen.
BUREAU_CAPTION_LINE_TOLERANCE_PT = 1.0


def source_bureau_reservations(
        glyphs: Sequence[SourceGlyph]) -> tuple[Rect, ...]:
    """Every caption the SOURCE PAGE prints reserving a blank for the Bureau.

    Assembled from the pinned PDF's own text operators (`drawn_glyph_boxes`)
    and from nothing else. `emit.py` answers the same question from
    `extract.py`'s IR runs and binds it through the page's walls; this reads
    the file directly and binds it geometrically, so the two share the words
    the Bureau printed and share no producer, no code path and no
    intermediate. That is the point: an emitter that reserved a box the sheet
    does not reserve still has to answer to this, because this never asks the
    emitter anything.

    Glyphs are grouped into printed lines by their own box band and ordered by
    x, which is the only structure a texttrace gives. **The rectangle reported
    is the matching phrase's own glyphs, never the line's** -- 0605 sets
    "Return Period (MM/DD/YYYY)" and "BCS No./Item No. (To be filled up by the
    BIR)" on ONE baseline, and a line-wide rectangle would hand the taxpayer's
    Return Period boxes the Bureau's excuse.
    """
    lines: dict[tuple[float, float], list[SourceGlyph]] = (
        collections.defaultdict(list))
    for glyph in glyphs:
        lines[(round(glyph.y0, 1), round(glyph.y1, 1))].append(glyph)
    captions: list[Rect] = []
    for key in sorted(lines):
        ordered = sorted(lines[key], key=lambda glyph: glyph.x0)
        text = "".join(glyph.text for glyph in ordered).lower()
        spans: list[tuple[int, int]] = []
        for phrase in BUREAU_RESERVING_ANYWHERE:
            start = text.find(phrase)
            while start >= 0:
                spans.append((start, start + len(phrase)))
                start = text.find(phrase, start + 1)
        for phrase in BUREAU_RESERVING_LINE_START:
            if text.startswith(phrase):
                spans.append((0, len(phrase)))
        for start, stop in spans:
            matched = ordered[start:stop]
            if not matched:
                continue
            captions.append((
                min(glyph.x0 for glyph in matched),
                min(glyph.y0 for glyph in matched),
                max(glyph.x1 for glyph in matched),
                max(glyph.y1 for glyph in matched),
            ))
    return tuple(captions)


def bureau_reserved_box(box: Rect, captions: Sequence[Rect]) -> bool:
    """Whether a printed blank carries a Bureau reservation on the paper.

    Two placements, both of which the corpus prints:

      * the caption is set INSIDE the blank -- 2200-A/C/P's bottom band draws
        one wide rectangle split into "Machine Validation" and "Stamp of
        Receiving Office", each carrying its heading at its own top edge; and
      * the caption is set DIRECTLY ABOVE the blank, within the caption's own
        height, horizontally overlapping it -- 0605's
        "BCS No./Item No. (To be filled up by the BIR)".

    This can only ever REMOVE an offender, so the count it removes is
    published by every caller. It is deliberately silent about a caption that
    is merely near: a reservation the paper does not place on the box is a
    guess, and a guess here would hide exactly the class of defect these
    assertions exist to find.
    """
    for caption in captions:
        if (caption[0] >= box[0] - OVERLAP_EPS_PT
                and caption[2] <= box[2] + OVERLAP_EPS_PT
                and caption[1] >= box[1] - OVERLAP_EPS_PT
                and caption[3] <= box[3] + OVERLAP_EPS_PT):
            return True
        overlap = min(caption[2], box[2]) - max(caption[0], box[0])
        if overlap <= 0:
            continue
        gap = box[1] - caption[3]
        height = caption[3] - caption[1]
        if -OVERLAP_EPS_PT <= gap <= height + BUREAU_CAPTION_LINE_TOLERANCE_PT:
            return True
    return False


class PointToneIndex:
    """Composited final tone at an arbitrary point of one source page.

    A bucketed view over `VectorPage.paints` so a point query does not rescan
    the page.  The bucket is a lookup device only: `_final_tone` is handed
    every paint whose rectangle contains the point, which is the same set it
    would receive from a full scan, so the answer it returns is identical --
    `_stable_source_verticals` already narrows its `active` list the same way.

    `None` means the page cannot be composited at that point (one operation
    with conflicting tone or opacity).  Every caller treats `None` as
    unevaluable and publishes it; none treats it as paper.
    """

    __slots__ = ("_buckets",)

    def __init__(self, paints: Sequence[VectorPaint]) -> None:
        buckets: dict[tuple[int, int], list[VectorPaint]] = (
            collections.defaultdict(list))
        for paint in paints:
            for bx in range(int(paint.x0 // TONE_BUCKET_PT),
                            int(paint.x1 // TONE_BUCKET_PT) + 1):
                for by in range(int(paint.y0 // TONE_BUCKET_PT),
                                int(paint.y1 // TONE_BUCKET_PT) + 1):
                    buckets[(bx, by)].append(paint)
        self._buckets = dict(buckets)

    def tone(self, x: float, y: float) -> float | None:
        candidates = self._buckets.get(
            (int(x // TONE_BUCKET_PT), int(y // TONE_BUCKET_PT)))
        if not candidates:
            return 1.0
        active = [paint for paint in candidates
                  if paint.x0 <= x <= paint.x1 and paint.y0 <= y <= paint.y1]
        if not active:
            return 1.0
        try:
            return _final_tone(active, x, y)
        except ValueError:
            return None


def source_printed_dividers(
        page: VectorPage, tones: PointToneIndex,
        ) -> tuple[tuple[float, VectorPaint], ...]:
    """Every compartment divider the SOURCE page still shows, by x centre.

    Dark, thin, materially taller than wide, and -- the clause that matters --
    still visible once the page has composited.  See the DIVIDER_* block for
    why the visibility clause is load bearing and how it was checked against
    the rasteriser rather than assumed.
    """
    out: list[tuple[float, VectorPaint]] = []
    for paint in page.paints:
        if paint.tone > DIVIDER_MAX_TONE:
            continue
        width = paint.x1 - paint.x0
        height = paint.y1 - paint.y0
        if width > DIVIDER_MAX_WIDTH_PT or height < DIVIDER_MIN_HEIGHT_PT:
            continue
        if height - width < DIVIDER_MIN_ANISOTROPY_PT:
            continue
        centre_x = (paint.x0 + paint.x1) / 2.0
        tone = tones.tone(centre_x, (paint.y0 + paint.y1) / 2.0)
        if tone is None or tone > DIVIDER_MAX_TONE:
            continue
        out.append((centre_x, paint))
    out.sort(key=lambda item: (item[0], item[1].y0, item[1].y1, item[1].order))
    return tuple(out)


def _covered_without_gap(spans: Sequence[tuple[float, float]],
                         low: float, high: float,
                         gap: float = PRINTED_BOX_SIDE_MAX_GAP_PT) -> bool:
    """Is [low, high] drawn by `spans`, leaving no gap wider than `gap`?"""
    cursor = low
    for span_low, span_high in spans:
        if span_high <= cursor:
            continue
        if span_low > high:
            break
        if span_low > cursor + gap:
            return False
        cursor = max(cursor, span_high)
        if cursor >= high:
            return True
    return cursor >= high - gap


def _source_grid_lines(
        page: VectorPage,
        ) -> tuple[tuple[tuple[float, list[tuple[float, float]]], ...],
                   tuple[tuple[float, list[tuple[float, float]]], ...]]:
    """Clustered horizontal and vertical source rule lines, with their extents.

    A "line" is every thin dark paint sharing a coordinate to within
    PRINTED_RULE_CLUSTER_PT, and its extent is the union of those paints'
    spans.  Rules are drawn in pieces -- a table's top edge is one operation
    per column on most of these sheets -- so the union, not any single paint,
    is what says whether a side exists.
    """
    horizontal: list[tuple[float, float, float]] = []
    vertical: list[tuple[float, float, float]] = []
    for paint in page.paints:
        if paint.tone > PRINTED_RULE_MAX_TONE:
            continue
        width = paint.x1 - paint.x0
        height = paint.y1 - paint.y0
        if (height <= PRINTED_RULE_MAX_THICKNESS_PT
                and width >= PRINTED_RULE_MIN_LENGTH_PT and width > height):
            horizontal.append(((paint.y0 + paint.y1) / 2.0, paint.x0, paint.x1))
        elif (width <= PRINTED_RULE_MAX_THICKNESS_PT
                and height >= PRINTED_RULE_MIN_LENGTH_PT and height > width):
            vertical.append(((paint.x0 + paint.x1) / 2.0, paint.y0, paint.y1))

    def cluster(items: list[tuple[float, float, float]]
                ) -> tuple[tuple[float, list[tuple[float, float]]], ...]:
        groups: list[list[Any]] = []
        for coord, low, high in sorted(items):
            if groups and coord - groups[-1][0] <= PRINTED_RULE_CLUSTER_PT:
                groups[-1][0] = coord
                groups[-1][1].append((low, high))
            else:
                groups.append([coord, [(low, high)]])
        return tuple((round(coord, 4), _merge_intervals(spans, 0.0))
                     for coord, spans in groups)

    return cluster(horizontal), cluster(vertical)


def source_printed_boxes(
        page: VectorPage, glyphs: Sequence[SourceGlyph],
        tones: PointToneIndex,
        ) -> tuple[tuple[Rect, ...], int]:
    """Blank enclosed boxes the SOURCE page draws, and how many were unevaluable.

    Minimal by construction: for each top-left grid intersection the first
    right edge and then the first bottom edge that close a fully drawn
    rectangle win, so a box is never reported nested inside a larger one that
    shares its corner.  Survivors must be blank -- no source glyph inside the
    1pt inset interior, and at least PRINTED_BOX_PAPER_MIN_FRACTION of the
    sampled interior at paper tone.  Both clauses are about the claim the
    inventory makes: these are boxes a taxpayer is meant to write in, not
    captions and not the official grey bands that say no entry applies.

    The second return value counts boxes whose interior could not be
    composited.  They are excluded from the inventory and published, never
    silently treated as paper.
    """
    horizontal, vertical = _source_grid_lines(page)
    boxes: list[Rect] = []
    unevaluable = 0
    for i in range(len(vertical) - 1):
        x0, left = vertical[i]
        for j in range(len(horizontal) - 1):
            y0, top = horizontal[j]
            closed: tuple[float, float] | None = None
            for k in range(i + 1, len(vertical)):
                x1, right = vertical[k]
                if x1 - x0 < PRINTED_BOX_MIN_SIDE_PT:
                    continue
                if x1 - x0 > PRINTED_BOX_MAX_SIDE_PT:
                    break
                if not _covered_without_gap(
                        right, y0, y0 + PRINTED_BOX_MIN_SIDE_PT):
                    continue
                for m in range(j + 1, len(horizontal)):
                    y1, bottom = horizontal[m]
                    if y1 - y0 < PRINTED_BOX_MIN_SIDE_PT:
                        continue
                    if y1 - y0 > PRINTED_BOX_MAX_SIDE_PT:
                        break
                    if (_covered_without_gap(top, x0, x1)
                            and _covered_without_gap(bottom, x0, x1)
                            and _covered_without_gap(left, y0, y1)
                            and _covered_without_gap(right, y0, y1)):
                        closed = (x1, y1)
                        break
                if closed is not None:
                    break
            if closed is None:
                continue
            x1, y1 = closed
            ix0 = x0 + PRINTED_BOX_INSET_PT
            iy0 = y0 + PRINTED_BOX_INSET_PT
            ix1 = x1 - PRINTED_BOX_INSET_PT
            iy1 = y1 - PRINTED_BOX_INSET_PT
            if ix1 <= ix0 or iy1 <= iy0:
                continue
            if any(glyph.x1 > ix0 and glyph.x0 < ix1
                   and glyph.y1 > iy0 and glyph.y0 < iy1 for glyph in glyphs):
                continue
            columns = min(PRINTED_BOX_SAMPLE_MAX, max(
                2, int((ix1 - ix0) // PRINTED_BOX_SAMPLE_PITCH_PT) + 1))
            rows = min(PRINTED_BOX_SAMPLE_MAX, max(
                2, int((iy1 - iy0) // PRINTED_BOX_SAMPLE_PITCH_PT) + 1))
            sampled = paper = 0
            broke = False
            for column in range(columns):
                for row in range(rows):
                    tone = tones.tone(
                        ix0 + (ix1 - ix0) * (column + 0.5) / columns,
                        iy0 + (iy1 - iy0) * (row + 0.5) / rows)
                    if tone is None:
                        broke = True
                        break
                    sampled += 1
                    if tone >= PRINTED_BOX_PAPER_MIN_TONE:
                        paper += 1
                if broke:
                    break
            if broke:
                unevaluable += 1
                continue
            if not sampled or paper / sampled < PRINTED_BOX_PAPER_MIN_FRACTION:
                continue
            boxes.append((x0, y0, x1, y1))
    boxes.sort()
    return tuple(boxes), unevaluable


@dataclasses.dataclass(frozen=True)
class SourceSlotOracle:
    """Which comb compartments the SOURCE has already filled in, and how.

    A comb compartment the official sheet printed a value into is not a blank,
    and emitting a text box over it lets a taxpayer overtype a statutory
    constant -- the ATC codes `II 011` and `XC 010`, the century `2 0`, the TIN
    branch code `0 0 0 0 0`. The emitter now refuses those compartments an
    input, which is a change to what "a complete comb emission" means, and this
    is the assertion's independent re-derivation of that same fact. It reads
    the source PDF's own text and paint operators -- never emit.py's decision,
    never a marker emit publishes, and never the absence of the input, all
    three of which would make the check a mirror of its subject.

    Two ways the source occupies a compartment, answered in this order because
    that is the order they are painted in:

      * **A printed glyph.** Exactly one glyph overlaps the compartment's own
        printed ROW, and it lies wholly inside that rectangle. One glyph,
        because a value is typeset AT the comb's pitch, one character per box,
        to look like a filled-in form; a caption the lattice swallowed into the
        same cell lands 9 to 87 glyphs in a single compartment (measured: 1801
        p1c110 carries 87). Wholly inside, because a neighbouring caption can
        clip one glyph across a compartment wall.
      * **Decorative shading.** The topmost source fill covering the
        compartment is grey at the copied `SOURCE_SHADING_*` bounds. This is
        the same statement made with tone instead of glyphs -- BIR shades a
        box to say NO ENTRY APPLIES -- and it is what accounts for the
        swallowed captions. Topmost, because the sheet paints a grey band
        across a row and then knocks white boxes back out of it for the blanks;
        reading the band alone would excuse every real field on that row.

    **The compartment is asked about over its printed row, not over the
    emitted writing rectangle.** The compartment's left and right walls are the
    source's own dividers -- an emitted partition that is not the source's is
    an offender anyway, on `emission-printed-mismatch`,
    `emission-source-position-mismatch` or `source-topology-unevaluable`, so an
    excuse can never carry a comb whose boxes are not where the sheet prints
    them. Its top and bottom are nothing the source drew: they are the writing
    rectangle emit chose. Containing the glyph's font box in THAT made the
    answer a function of the emitter's typography, and the corpus shows the
    cost exactly: of 92 identical money bullets, 7 were called occupied and 85
    blank paper, the only difference being that the bullet's descent line falls
    0.03-0.41pt below the writing rectangle's floor. The row -- the compartment
    stretched to the top and bottom of the cell whose walls the sheet drew --
    is a rectangle the source is responsible for, and it keeps every rejection
    the old test made: over the whole corpus's 375 inputless compartments, the
    row rectangle admits every compartment the writing rectangle admitted and
    loses none.

    **Character class is not the question, and the C4 reasoning that said it
    was does not survive its own measurement.** The old rule demanded an
    ALPHANUMERIC glyph, on the reasoning that `.` `,` `-` `%` and the money
    bullet are drawn INSIDE a field to shape what is typed there rather than to
    state a value, and that excusing them would put back C4 -- a money comb
    with no way to enter an amount at all. A compartment is one character wide
    and the source has already put a character in it; whatever that character
    means, the compartment is SPENT, and an input there is a typing surface
    laid on printed ink that no taxpayer can use. What actually protects C4 is
    that only an OCCUPIED compartment is ever excused, which is a per-
    compartment fact the source answers: measured over this corpus, the
    non-alphanumeric population is 92 money bullets, each ONE compartment of a
    14-, 29- or 33-compartment comb, every one of them the third from the right
    with the two centavos compartments to its right and centred in its own
    compartment to within 0.2pt (2000-DST 16, 2200A 20, 2200C 20, 2200P 20,
    2200S 16); the 2 that complete the printed rate `0 %` on 1800 p1c68 and
    2550-DS p1c79, 2-compartment combs the source fills entirely and which are
    not money boxes; and 7 grey TIN group separators printing `-` or `.` that
    the shading branch below already excused. No digit compartment anywhere in
    the corpus loses its input. The kind is still published per compartment --
    `printed-constant` for an alphanumeric glyph, `printed-mark` for one that
    is not -- so a report can still tell a statutory value from a separator.

    `available` is False when either evidence is missing (no glyph operators
    for the page, no modelled source paint, a rotated page whose text operators
    are not in the paint's coordinate space). An unavailable oracle excuses
    nothing: a compartment with no input and no readable source evidence stays
    an offender, because "we could not look" is not "the source filled it in".
    """

    glyphs: tuple[SourceGlyph, ...] | None
    paints: tuple[VectorPaint, ...] | None
    unavailable_reason: str | None = None

    @property
    def available(self) -> bool:
        return (self.glyphs is not None and self.paints is not None
                and self.unavailable_reason is None)

    def occupancy(self, box: Rect | None,
                  row: Rect | None = None) -> dict[str, Any] | None:
        """What the source put in this compartment, or None for nothing.

        `box` is the compartment a taxpayer would type into, and it is what the
        shading question is asked of -- tone is a fact about the paper directly
        under the box. `row` is that same compartment stretched to its printed
        row, and it is what the glyph question is asked of, for the reason the
        class docstring gives. A caller that cannot supply the row supplies no
        glyph evidence and gets none: the oracle fails closed rather than
        falling back to a rectangle whose vertical edges the emitter chose.
        """
        if not self.available or box is None:
            return None
        x0, y0, x1, y1 = (float(value) for value in box)
        if not all(math.isfinite(value) for value in (x0, y0, x1, y1)):
            return None
        if x1 <= x0 or y1 <= y0:
            return None
        glyph = self._printed_glyph(row)
        if glyph is not None:
            return {
                "kind": ("printed-constant" if glyph.isalnum()
                         else "printed-mark"),
                "text": glyph,
            }
        tone = self._covering_shading(x0, y0, x1, y1)
        if tone is not None:
            return {"kind": "decorative-shading", "tone": tone}
        return None

    def _printed_glyph(self, row: Rect | None) -> str | None:
        if row is None:
            return None
        x0, y0, x1, y1 = (float(value) for value in row)
        if not all(math.isfinite(value) for value in (x0, y0, x1, y1)):
            return None
        if x1 <= x0 or y1 <= y0:
            return None
        found: SourceGlyph | None = None
        for glyph in self.glyphs or ():
            if min(x1, glyph.x1) <= max(x0, glyph.x0):
                continue
            if min(y1, glyph.y1) <= max(y0, glyph.y0):
                continue
            if found is not None:
                return None
            found = glyph
        if found is None:
            return None
        if found.x0 < x0 or found.x1 > x1 or found.y0 < y0 or found.y1 > y1:
            return None
        return found.text

    def _covering_shading(self, x0: float, y0: float,
                          x1: float, y1: float) -> float | None:
        area = (x1 - x0) * (y1 - y0)
        needed = area * SOURCE_SHADING_MIN_COVERAGE
        best: VectorPaint | None = None
        best_key: tuple[int, int] = (-1, -1)
        for index, paint in enumerate(self.paints or ()):
            width = min(x1, paint.x1) - max(x0, paint.x0)
            height = min(y1, paint.y1) - max(y0, paint.y0)
            if width <= 0.0 or height <= 0.0 or width * height < needed:
                continue
            # The source's own paint order decides what the paper is; the
            # enumeration index only breaks a tie between two paints of one
            # operation, so the answer stays a pure function of the file.
            key = (paint.order, index)
            if key > best_key:
                best, best_key = paint, key
        if best is None or best.kind != "fill-region" or best.opacity != 1.0:
            return None
        if not SOURCE_SHADING_MIN_TONE < best.tone <= SOURCE_SHADING_MAX_TONE:
            return None
        return best.tone


def transform_signature(matrix: Sequence[float]) -> tuple[int, int, bool]:
    """Orientation of a placement matrix: (x sign, y sign, sheared).

    Magnitude is already checked by the image's bbox, so what has to survive to
    the SVG is *orientation*. PyMuPDF reports the placement in the same
    y-downward space the page and our SVG use, so an upright image has d > 0 and
    2550M's flipped seal has d < 0; an SVG that reproduces it must therefore
    carry a negative y scale. Comparing signs rather than the six numbers keeps
    the assertion from dictating how emit.py decomposes the matrix.
    """
    a, b, c, d = (float(v) for v in matrix[:4])
    return (-1 if a < 0 else 1, -1 if d < 0 else 1,
            abs(b) > TRANSFORM_EPS or abs(c) > TRANSFORM_EPS)


SVG_TRANSFORM_RE = re.compile(r'(matrix|scale|translate|rotate)\(([^)]*)\)')


def svg_signature(transform: str | None) -> tuple[int, int, bool]:
    """The same orientation signature, read off an SVG transform list."""
    if not transform:
        return (1, 1, False)
    a, b, c, d = 1.0, 0.0, 0.0, 1.0
    for name, body in SVG_TRANSFORM_RE.findall(transform):
        nums = [float(v) for v in re.split(r'[\s,]+', body.strip()) if v]
        if name == "matrix" and len(nums) == 6:
            na, nb, nc, nd = nums[:4]
        elif name == "scale":
            na, nb, nc, nd = nums[0], 0.0, 0.0, (nums[1] if len(nums) > 1 else nums[0])
        elif name == "rotate" and nums:
            rad = math.radians(nums[0])
            na, nb, nc, nd = math.cos(rad), math.sin(rad), -math.sin(rad), math.cos(rad)
        else:
            continue    # translate has no linear part
        a, b, c, d = (a * na + c * nb, b * na + d * nb,
                      a * nc + c * nd, b * nc + d * nd)
    return transform_signature((a, b, c, d))


# --------------------------------------------------------------------------
# the bundle every assertion reads
# --------------------------------------------------------------------------


CSS_URL_RE = re.compile(
    r"""url\(\s*(?P<quote>["']?)(?P<url>.*?)\1\s*\)""",
    re.IGNORECASE | re.DOTALL,
)
CSS_IMPORT_RE = re.compile(
    r"""@import\s+(?:url\(\s*)?(?P<quote>["']?)
        (?P<url>[^"'\s;)]+)\1\s*\)?""",
    re.IGNORECASE | re.VERBOSE,
)


class _RenderDependencyScanner(HTMLParser):
    """Collect only resource URLs a browser can fetch while rendering."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.references: list[tuple[str, str]] = []
        self.errors: list[str] = []
        self._style_depth = 0

    def _add(self, value: str | None, kind: str) -> None:
        if value is not None and value.strip():
            self.references.append((value.strip(), kind))

    def _add_srcset(self, value: str | None, kind: str) -> None:
        if value is None:
            return
        if "data:" in value.lower():
            self.errors.append(
                "data URLs in srcset are unsupported by the closure parser")
            return
        for candidate in value.split(","):
            url = candidate.strip().split(None, 1)[0]
            if url and not url.lower().startswith("data:"):
                self._add(url, kind)

    def handle_starttag(
            self, tag: str,
            attrs: list[tuple[str, str | None]],
            ) -> None:
        values = {name.lower(): value for name, value in attrs}
        tag = tag.lower()
        if tag == "base" and values.get("href"):
            self.errors.append(
                "base href is forbidden in an isolated render snapshot")
        if (tag == "meta"
                and (values.get("http-equiv") or "").lower() == "refresh"):
            self.errors.append(
                "meta refresh is forbidden in an isolated render snapshot")
        if values.get("style"):
            self.references.extend(
                (url, "inline-style")
                for url in _css_resource_urls(values["style"] or "")
            )
        if tag == "style":
            self._style_depth += 1
        if tag == "script":
            self._add(values.get("src"), "script")
        elif tag == "link":
            rel = {
                item.lower()
                for item in (values.get("rel") or "").split()
            }
            if rel & {
                    "stylesheet", "preload", "modulepreload",
                    "icon", "manifest"}:
                self._add(values.get("href"), "link")
        elif tag in {"img", "source"}:
            self._add(values.get("src"), tag)
            self._add_srcset(values.get("srcset"), f"{tag}-srcset")
        elif tag in {"video", "audio", "track", "embed", "iframe"}:
            self._add(values.get("src"), tag)
            if tag == "video":
                self._add(values.get("poster"), "video-poster")
        elif tag == "object":
            self._add(values.get("data"), "object")
        elif tag == "input" and (values.get("type") or "").lower() == "image":
            self._add(values.get("src"), "input-image")
        elif tag == "image":
            self._add(
                values.get("href") or values.get("xlink:href"),
                "svg-image",
            )

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "style" and self._style_depth:
            self._style_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._style_depth:
            self.references.extend(
                (url, "style-block") for url in _css_resource_urls(data))


def _css_resource_urls(css: str) -> list[str]:
    imports = [match.group("url") for match in CSS_IMPORT_RE.finditer(css)]
    urls = [match.group("url") for match in CSS_URL_RE.finditer(css)]
    return imports + urls


def _logical_resource_path(reference: str, base: str) -> str | None:
    """Map one relative browser URL to a canonical isolated-tree path."""
    parsed = urllib.parse.urlsplit(reference.strip())
    scheme = parsed.scheme.lower()
    if scheme == "data":
        return None
    if (scheme or parsed.netloc or reference.startswith("//")
            or parsed.path.startswith("/")):
        raise ValueError(f"external or absolute render dependency: {reference}")
    if parsed.query:
        raise ValueError(
            f"query-bearing render dependency is ambiguous: {reference}")
    if not parsed.path:
        return None
    decoded = urllib.parse.unquote(parsed.path)
    if ("\\" in decoded
            or any(ord(character) < 32 or ord(character) == 127
                   for character in decoded)):
        raise ValueError(f"invalid render dependency path: {reference}")
    joined = posixpath.normpath(
        posixpath.join(posixpath.dirname(base), decoded))
    if (joined in {"", ".", ".."}
            or joined.startswith("../")
            or posixpath.isabs(joined)):
        raise ValueError(
            f"render dependency escapes its snapshot root: {reference}")
    return joined


def discover_render_dependencies(
        html_payload: bytes,
        html_filename: str,
        html_dir: pathlib.Path,
        ) -> tuple[
            dict[str, bytes],
            list[dict[str, Any]],
            list[str],
        ]:
    """Snapshot the recursive local dependency closure of one HTML document."""
    try:
        html_text = html_payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        return {}, [], [f"HTML is not UTF-8: {exc}"]
    scanner = _RenderDependencyScanner()
    scanner.feed(html_text)
    errors = list(scanner.errors)
    root = html_dir.resolve()
    pending = [
        (reference, html_filename, kind)
        for reference, kind in scanner.references
    ]
    payloads: dict[str, bytes] = {}
    metadata: dict[str, dict[str, Any]] = {}
    visited_css: set[str] = set()
    while pending:
        reference, referrer, kind = pending.pop(0)
        try:
            logical = _logical_resource_path(reference, referrer)
        except ValueError as exc:
            errors.append(f"{referrer}: {exc}")
            continue
        if logical is None:
            continue
        item = metadata.setdefault(logical, {
            "path": logical,
            "kinds": set(),
            "referrers": set(),
            "mime_type": None,
            "present": False,
            "bytes": None,
            "sha256": None,
        })
        item["kinds"].add(kind)
        item["referrers"].add(referrer)
        if logical in payloads:
            continue
        candidate = root / pathlib.PurePosixPath(logical)
        try:
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(root)
            if candidate.is_symlink() or resolved != candidate:
                raise ValueError("symlinked dependency path is forbidden")
            payload = _stable_read(resolved)
        except (FileNotFoundError, RuntimeError, ValueError) as exc:
            errors.append(
                f"{referrer}: unresolved render dependency "
                f"{reference!r} ({exc})")
            continue
        payloads[logical] = payload
        mime_type = mimetypes.guess_type(logical)[0]
        if mime_type is None:
            errors.append(
                f"{logical}: render dependency has unknown MIME type")
            continue
        item.update({
            "mime_type": mime_type,
            "present": True,
            "bytes": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
        })
        is_css = (
            logical.lower().endswith(".css")
            or kind in {"inline-style", "style-block", "link"}
            and logical.lower().split("?", 1)[0].endswith(".css")
        )
        if is_css and logical not in visited_css:
            visited_css.add(logical)
            try:
                css = payload.decode("utf-8")
            except UnicodeDecodeError as exc:
                errors.append(f"{logical}: CSS is not UTF-8 ({exc})")
                continue
            pending.extend(
                (nested, logical, "css")
                for nested in _css_resource_urls(css)
            )
    entries = [
        {
            **{key: value for key, value in item.items()
               if key not in {"kinds", "referrers"}},
            "kinds": sorted(item["kinds"]),
            "referrers": sorted(item["referrers"]),
        }
        for _logical, item in sorted(metadata.items())
    ]
    return payloads, entries, sorted(set(errors))


@dataclasses.dataclass(frozen=True)
class InputSnapshot:
    manifest: dict[str, Any]
    contents: dict[str, bytes | None]
    missing_required: tuple[str, ...]
    render_assets: dict[str, bytes] = dataclasses.field(default_factory=dict)
    render_entrypoint: str | None = None


def file_fingerprint(path: pathlib.Path, logical_file: str) -> dict[str, Any]:
    payload = _stable_read(path)
    return bytes_fingerprint(payload, logical_file)


def bytes_fingerprint(payload: bytes, logical_file: str) -> dict[str, Any]:
    return {
        "file": logical_file,
        "bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }


def validate_trusted_producer_sources() -> None:
    """Fail if loaded local code or its source path changes during one run."""
    expected = (
        (_AUDIT_SOURCE_PATH, _AUDIT_SOURCE_PAYLOAD, None, "audit"),
        (_TRUSTED_EXTRACT.path, _TRUSTED_EXTRACT.payload,
         _TRUSTED_EXTRACT, "extract"),
        (_TRUSTED_VERIFY.path, _TRUSTED_VERIFY.payload,
         _TRUSTED_VERIFY, "verify"),
    )
    for path, payload, loaded, name in expected:
        if _stable_read(path) != payload:
            raise RuntimeError(
                f"trusted producer source changed after snapshot: {name}")
        if (loaded is not None
                and loaded.module.__dict__.get(
                    "__formgen_source_sha256__") != loaded.sha256):
            raise RuntimeError(f"loaded producer source marker changed: {name}")
    if verify.__dict__.get("extract") is not extract:
        raise RuntimeError(
            "loaded verify module no longer references trusted extract")
    if (sys.modules.get("extract") is not extract
            or sys.modules.get("verify") is not verify):
        raise RuntimeError("trusted producer module binding was substituted")


@functools.lru_cache(maxsize=1)
def _producer_fingerprint_snapshot() -> dict[str, Any]:
    """Loaded dependency bytes plus an honest standalone-attestation scope."""
    validate_trusted_producer_sources()
    files = [
        bytes_fingerprint(
            _AUDIT_SOURCE_PAYLOAD, "tools/formgen/audit.py"),
        bytes_fingerprint(
            _TRUSTED_EXTRACT.payload, "tools/formgen/extract.py"),
        bytes_fingerprint(
            _TRUSTED_VERIFY.payload, "tools/formgen/verify.py"),
    ]
    files[1]["loaded_origin"] = "tools/formgen/extract.py"
    files[1]["executed_from_snapshotted_source"] = True
    files[2]["loaded_origin"] = "tools/formgen/verify.py"
    files[2]["executed_from_snapshotted_source"] = True
    return {
        **files[0],
        "dependencies": files[1:],
        "dependency_execution_bound": True,
        "audit_execution_bound": False,
        "assertion_producer_bound": False,
        "roundtrip_runtime_bound_in_record": False,
        "standalone_attestation_complete": False,
        "incomplete_reason": (
            "audit.py self-execution predates its in-process source snapshot; "
            "clean-bootstrap or clean-commit gate binding is required"
        ),
    }


def producer_fingerprint() -> dict[str, Any]:
    validate_trusted_producer_sources()
    return copy.deepcopy(_producer_fingerprint_snapshot())


def _stable_file_sha256(path: pathlib.Path) -> tuple[int, str]:
    flags = os.O_RDONLY
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise RuntimeError(
            "runtime closure member could not be opened without following "
            f"a symlink: {path}") from exc
    digest = hashlib.sha256()
    size = 0
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise RuntimeError(
                f"runtime closure member is not regular: {path}")
        while True:
            chunk = os.read(descriptor, 1 << 20)
            if not chunk:
                break
            size += len(chunk)
            digest.update(chunk)
        after = os.fstat(descriptor)
        try:
            path_after = os.stat(path, follow_symlinks=False)
        except OSError as exc:
            raise RuntimeError(
                f"runtime closure path changed while read: {path}") from exc
        stable_fields = (
            "st_dev", "st_ino", "st_mode", "st_size",
            "st_mtime_ns", "st_ctime_ns",
        )
        if (any(getattr(before, field) != getattr(after, field)
                for field in stable_fields)
                or not stat.S_ISREG(path_after.st_mode)
                or (path_after.st_dev, path_after.st_ino)
                != (after.st_dev, after.st_ino)
                or size != after.st_size):
            raise RuntimeError(
                f"runtime closure member changed while read: {path}")
        return size, digest.hexdigest()
    finally:
        os.close(descriptor)


@dataclasses.dataclass(frozen=True)
class _TreeClosure:
    root: pathlib.Path
    entries: tuple[tuple[str, str, int | None, str], ...]

    def manifest(self, logical_root: str) -> dict[str, Any]:
        canonical = json.dumps(
            self.entries, separators=(",", ":"), ensure_ascii=True)
        return {
            "logical_root": logical_root,
            "algorithm": TREE_CLOSURE_ALGORITHM,
            "files": sum(1 for item in self.entries if item[1] == "file"),
            "symlinks": sum(
                1 for item in self.entries if item[1] == "symlink"),
            "bytes": sum(
                int(item[2] or 0)
                for item in self.entries if item[1] == "file"),
            "tree_sha256": hashlib.sha256(
                canonical.encode("ascii")).hexdigest(),
        }


TREE_CLOSURE_ALGORITHM = "sha256(canonical-json(path,type,bytes,digest))"
# Bytecode caches are excluded from every runtime tree closure, and the
# exclusion is published rather than assumed.  The gate materialises each
# approved runtime dependency into a private byte-verified view whose
# inventory deliberately omits `__pycache__` and `*.pyc`
# (gate.ISOLATED_PYTHON_BOOTSTRAP's `tree_records`), and it runs this audit
# with `-B`, `PYTHONDONTWRITEBYTECODE=1` and a redirected `pycache_prefix`,
# so inside that view no in-tree cache exists and none could be loaded if it
# did.  Hashing the same set on both sides is what lets an independent
# verifier re-derive this closure from the installed package rather than from
# the gate's temporary copy of it, which is the whole point of publishing it.
BYTECODE_CACHE_EXCLUSION_REASON = (
    "__pycache__ directories and .pyc files are excluded so the closure is "
    "re-derivable from the installed package; the gate's approved-dependency "
    "view omits them and its audit runs with bytecode writing disabled and a "
    "redirected pycache prefix, so no in-tree cache is loadable"
)


def _is_bytecode_cache(logical: str) -> bool:
    return (
        "__pycache__" in pathlib.PurePosixPath(logical).parts
        or logical.endswith(".pyc")
    )


def _snapshot_tree(root: pathlib.Path) -> _TreeClosure:
    root = root.resolve(strict=True)
    entries: list[tuple[str, str, int | None, str]] = []
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        logical = path.relative_to(root).as_posix()
        if _is_bytecode_cache(logical):
            continue
        if path.is_symlink():
            target = os.readlink(path)
            try:
                path.resolve(strict=True).relative_to(root)
            except (FileNotFoundError, ValueError) as exc:
                raise RuntimeError(
                    f"runtime closure symlink escapes root: "
                    f"{logical} -> {target}") from exc
            entries.append((logical, "symlink", None, target))
        elif path.is_file():
            size, digest = _stable_file_sha256(path)
            entries.append((logical, "file", size, digest))
    return _TreeClosure(root=root, entries=tuple(entries))


def _validate_tree_closure(
        closure: _TreeClosure,
        phase: str,
        ) -> None:
    observed = _snapshot_tree(closure.root)
    if observed.entries != closure.entries:
        raise RuntimeError(f"runtime dependency closure changed {phase}")


@dataclasses.dataclass(frozen=True)
class _BoundPlaywrightRuntime:
    playwright: Any
    chromium_path: pathlib.Path
    provenance: dict[str, Any]
    closure: _TreeClosure


_BOUND_PLAYWRIGHT_MODULE_IDENTITIES: dict[str, int] | None = None


def _loaded_playwright_modules() -> dict[str, types.ModuleType]:
    return {
        name: module
        for name, module in sys.modules.items()
        if (name == "playwright" or name.startswith("playwright."))
        and isinstance(module, types.ModuleType)
    }


def _validate_playwright_module_bindings(
        loaded: dict[str, types.ModuleType],
        expected: dict[str, int] | None,
        ) -> None:
    if expected is None:
        if loaded:
            raise RuntimeError(
                "Playwright was imported before its dependency closure "
                "was bound")
        return
    if set(loaded) != set(expected):
        raise RuntimeError(
            "bound Playwright module set changed between uses")
    for name, identity in expected.items():
        if id(loaded[name]) != identity:
            raise RuntimeError(
                f"bound Playwright module was substituted: {name}")


def _playwright_package_root() -> pathlib.Path:
    spec = importlib.machinery.PathFinder.find_spec("playwright", sys.path)
    if spec is None or spec.origin is None:
        raise FileNotFoundError(
            "Playwright is required for a provenance-bound round trip")
    return pathlib.Path(spec.origin).resolve(strict=True).parent


@contextlib.contextmanager
def _bound_playwright_runtime() -> Iterable[_BoundPlaywrightRuntime]:
    """Resolve, bind, use and revalidate one Playwright/Chromium closure."""
    global _BOUND_PLAYWRIGHT_MODULE_IDENTITIES
    package_root = _playwright_package_root()
    closure = _snapshot_tree(package_root)
    preloaded = _loaded_playwright_modules()
    _validate_playwright_module_bindings(
        preloaded, _BOUND_PLAYWRIGHT_MODULE_IDENTITIES)
    old_dont_write = sys.dont_write_bytecode
    try:
        sys.dont_write_bytecode = True
        with _standard_importers_only():
            import playwright
            from playwright.sync_api import sync_playwright
            origin = pathlib.Path(playwright.__file__).resolve(strict=True)
            try:
                origin.relative_to(package_root)
            except ValueError as exc:
                raise RuntimeError(
                    "loaded Playwright module is outside its bound closure"
                ) from exc
            _validate_tree_closure(closure, "before Playwright use")
            with sync_playwright() as pw:
                chromium_path = pathlib.Path(
                    pw.chromium.executable_path).resolve(strict=True)
                try:
                    chromium_logical = chromium_path.relative_to(
                        package_root).as_posix()
                except ValueError as exc:
                    raise RuntimeError(
                        "resolved Chromium executable is outside Playwright "
                        "closure") from exc
                version_result = subprocess.run(
                    [str(chromium_path), "--version"],
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                if version_result.returncode != 0:
                    raise RuntimeError(
                        "could not identify bound Chromium runtime: "
                        + version_result.stderr.strip())
                chromium = file_fingerprint(
                    chromium_path, f"playwright/{chromium_logical}")
                chromium["version_output"] = version_result.stdout.strip()
                provenance = {
                    "mode": "playwright-exact-executable",
                    "playwright_package_version": importlib.metadata.version(
                        "playwright"),
                    "dependency_closure": closure.manifest("playwright"),
                    "chromium": chromium,
                    "same_resolution_session_used_for_render": True,
                    "dependency_closure_validated_before_after": True,
                    "system_shared_libraries_bound": False,
                    "native_host_environment_bound": False,
                    "scope": (
                        "playwright-package-tree-and-explicit-chromium-"
                        "executable"),
                    "scope_complete": False,
                    "incomplete_reason": (
                        "operating-system shared libraries, font services, "
                        "and other native rendering resources loaded by "
                        "Python and Chromium are outside the application-file "
                        "closure"
                    ),
                }
                runtime = _BoundPlaywrightRuntime(
                    playwright=pw,
                    chromium_path=chromium_path,
                    provenance=provenance,
                    closure=closure,
                )
                try:
                    yield runtime
                finally:
                    _validate_tree_closure(
                        closure, "after Playwright use")
                    loaded = _loaded_playwright_modules()
                    for name, module in loaded.items():
                        origin_value = getattr(module, "__file__", None)
                        if origin_value is None:
                            raise RuntimeError(
                                "loaded Playwright module has no bound "
                                f"origin: {name}")
                        module_origin = pathlib.Path(
                            origin_value).resolve(strict=True)
                        try:
                            module_origin.relative_to(package_root)
                        except ValueError as exc:
                            raise RuntimeError(
                                "loaded Playwright module escaped its "
                                f"closure: {name}") from exc
                    identities = {
                        name: id(module)
                        for name, module in loaded.items()
                    }
                    if _BOUND_PLAYWRIGHT_MODULE_IDENTITIES is None:
                        _BOUND_PLAYWRIGHT_MODULE_IDENTITIES = identities
                    else:
                        for name, identity in (
                                _BOUND_PLAYWRIGHT_MODULE_IDENTITIES.items()):
                            if identities.get(name) != identity:
                                raise RuntimeError(
                                    "bound Playwright module identity "
                                    f"changed: {name}")
                        _BOUND_PLAYWRIGHT_MODULE_IDENTITIES.update(identities)
    finally:
        sys.dont_write_bytecode = old_dont_write


def roundtrip_runtime_provenance() -> dict[str, Any]:
    """Inspect the exact closure a real round trip will resolve and reuse."""
    with _bound_playwright_runtime() as runtime:
        return copy.deepcopy(runtime.provenance)


APPLICATION_PACKAGE_NAMES = ("fitz", "pymupdf")
NATIVE_LIBRARY_SUFFIXES = (".dylib", ".so", ".dll", ".pyd")
APPLICATION_CLOSURE_SCOPE = (
    "interpreter-binaries-and-application-package-trees-v1")


def _resolve_package_root(
        name: str, search_path: Sequence[str]) -> pathlib.Path:
    """The single directory ``name`` would be imported from, or an error.

    Resolution runs over `sys.path`, so the closure covers the tree this
    process ACTUALLY imports from -- under the gate that is its private
    byte-verified dependency view, not the installed package.  comb_referee.py
    resolves the installed package instead, from the interpreter's own
    configuration, and the two answers have to agree byte for byte.
    """
    spec = importlib.machinery.PathFinder.find_spec(name, list(search_path))
    locations = list(getattr(spec, "submodule_search_locations", None) or ())
    if spec is None or len(locations) != 1:
        raise RuntimeError(
            f"application package has no single resolved root: {name}")
    return pathlib.Path(locations[0]).resolve(strict=True)


@functools.lru_cache(maxsize=1)
def _application_closure_snapshot(
        ) -> tuple[tuple[str, _TreeClosure], ...]:
    """Snapshot every file in the package trees this process imports from.

    Enumerating the tree rather than the loaded module list is what makes the
    closure complete AND independently re-derivable: a verifier cannot know
    which submodules this process happened to import, but it can resolve the
    same package root and hash the same files.  The bundled native libraries
    PyMuPDF loads through the dynamic linker rather than through an import --
    `libmupdf.dylib` and `libmupdfcpp.so` -- live in that tree and are bound
    here for the first time; the loaded-module inventory never saw them.
    """
    return tuple(
        (name, _snapshot_tree(_resolve_package_root(name, sys.path)))
        for name in APPLICATION_PACKAGE_NAMES
    )


def validate_application_closure() -> None:
    for name, closure in _application_closure_snapshot():
        if _resolve_package_root(name, sys.path) != closure.root:
            raise RuntimeError(
                f"application package root was substituted: {name}")
        _validate_tree_closure(closure, f"in the {name} application closure")


def _application_closure_manifest() -> dict[str, Any]:
    """Publish the application closure, and whether it is complete.

    `complete` is a measured relation, not a declaration: every loaded
    application module must fall inside a package root that was resolved from
    the interpreter's own configuration.  A module imported from anywhere else
    -- an injected path entry, a zip importer, an editable checkout -- leaves
    the tree unable to account for it, and says so instead of claiming a
    completeness it cannot support.
    """
    closures = _application_closure_snapshot()
    modules: list[dict[str, Any]] = []
    unbound: list[str] = []
    for logical, path, size, digest in _base_runtime_snapshot():
        if not logical.startswith("module/"):
            continue
        for name, closure in closures:
            try:
                relative = path.relative_to(closure.root).as_posix()
            except ValueError:
                continue
            modules.append({
                "module": logical[len("module/"):],
                "file": f"{name}/{relative}",
                "bytes": size,
                "sha256": digest,
            })
            break
        else:
            unbound.append(logical)
    native = [
        {"file": f"{name}/{logical}", "bytes": size, "sha256": digest}
        for name, closure in closures
        for logical, kind, size, digest in closure.entries
        if kind == "file" and logical.endswith(NATIVE_LIBRARY_SUFFIXES)
    ]
    return {
        "scope": APPLICATION_CLOSURE_SCOPE,
        "algorithm": TREE_CLOSURE_ALGORITHM,
        "bytecode_caches_excluded": True,
        "exclusion_reason": BYTECODE_CACHE_EXCLUSION_REASON,
        "packages": [closure.manifest(name) for name, closure in closures],
        "modules": sorted(modules, key=lambda item: item["file"]),
        "native_libraries": sorted(native, key=lambda item: item["file"]),
        "unbound_modules": sorted(unbound),
        "validated_before_after": True,
        "complete": not unbound,
    }


def _runtime_bound_paths() -> dict[str, pathlib.Path]:
    paths = {"python/executable": pathlib.Path(sys.executable).resolve()}
    library = sysconfig.get_config_var("LDLIBRARY")
    library_dir = sysconfig.get_config_var("LIBDIR")
    if library and library_dir:
        candidate = pathlib.Path(library_dir) / str(library)
        if candidate.is_file():
            paths["python/runtime-library"] = candidate.resolve()
    for name, module in sorted(sys.modules.items()):
        if not (name == "fitz" or name.startswith("pymupdf")):
            continue
        origin = getattr(module, "__file__", None)
        if origin and pathlib.Path(origin).is_file():
            paths[f"module/{name}"] = pathlib.Path(origin).resolve()
    return paths


@functools.lru_cache(maxsize=1)
def _base_runtime_snapshot(
        ) -> tuple[tuple[str, pathlib.Path, int, str], ...]:
    records = []
    for logical, path in sorted(_runtime_bound_paths().items()):
        size, digest = _stable_file_sha256(path)
        records.append((logical, path, size, digest))
    return tuple(records)


def validate_base_runtime() -> None:
    snapshot = _base_runtime_snapshot()
    expected_paths = {
        logical: path for logical, path, _size, _sha in snapshot}
    if _runtime_bound_paths() != expected_paths:
        raise RuntimeError(
            "bound Python/PyMuPDF loaded-module closure changed")
    for logical, path, expected_size, expected_sha in snapshot:
        size, digest = _stable_file_sha256(path)
        if size != expected_size or digest != expected_sha:
            raise RuntimeError(
                f"bound Python/PyMuPDF runtime changed: {logical}")
    validate_application_closure()


@functools.lru_cache(maxsize=1)
def _runtime_provenance_snapshot() -> dict[str, Any]:
    """Interpreter/PyMuPDF application files, with scope stated honestly."""
    import fitz
    records = _base_runtime_snapshot()
    canonical = json.dumps(
        [(logical, size, digest)
         for logical, _path, size, digest in records],
        separators=(",", ":"),
    )
    return {
        "python": {
            "implementation": platform.python_implementation(),
            "version": platform.python_version(),
            "cache_tag": sys.implementation.cache_tag,
        },
        "pymupdf": {
            "package_version": str(getattr(fitz, "__version__", "")),
            "version_bind": str(getattr(fitz, "VersionBind", "")),
        },
        "loaded_application_files": {
            "algorithm": (
                "sha256(canonical-json(logical-file,bytes,sha256))"),
            "files": len(records),
            "bytes": sum(item[2] for item in records),
            "tree_sha256": hashlib.sha256(
                canonical.encode("ascii")).hexdigest(),
            "members": [
                {
                    "file": logical,
                    "bytes": size,
                    "sha256": digest,
                }
                for logical, _path, size, digest in records
            ],
            "validated_before_after": True,
        },
        "application_closure": _application_closure_manifest(),
        "stdlib_and_system_shared_libraries_bound": False,
        "scope_complete": False,
        "incomplete_reason": (
            "the application closure binds the interpreter executable, "
            "runtime library, every loaded PyMuPDF application module and "
            "every file in the fitz/pymupdf package trees including their "
            "bundled native libraries; the Python standard library and the "
            "operating system's own shared libraries stay in the host trusted "
            "computing base and are not bound by any scope here"
        ),
    }


def runtime_provenance() -> dict[str, Any]:
    validate_base_runtime()
    return copy.deepcopy(_runtime_provenance_snapshot())


def snapshot_inputs(slug: str, ir_dir: pathlib.Path, html_dir: pathlib.Path,
                    layout_dir: pathlib.Path,
                    guide_dir: pathlib.Path | None,
                    source_root: str) -> InputSnapshot:
    """Read and hash the exact bytes one form's audit will consume.

    Paths in the manifest are logical filenames rather than absolute paths, so
    the same inputs publish byte-identical evidence in another checkout.
    `guide_html` is optional because only forms with relocated guide content
    emit one; the guide plan itself is required for every form. The official
    PDF is resolved from the snapshotted IR, read once, and retained as bytes so
    a path mutation cannot change what later assertions evaluate.
    """
    validate_trusted_producer_sources()
    validate_base_runtime()
    specs = (
        ("ir", ir_dir / f"{slug}.ir.json", True),
        ("layout", layout_dir / f"{slug}.layout.json", True),
        ("html", html_dir / f"{slug}.html", True),
        ("guide", guide_dir / f"{slug}.guide.json" if guide_dir else None, True),
        ("guide_html", html_dir / f"{slug}.guide.html", False),
    )
    entries: dict[str, dict[str, Any]] = {}
    contents: dict[str, bytes | None] = {}
    missing: list[str] = []
    for role, path, required in specs:
        filename = path.name if path is not None else (
            f"{slug}.guide.json" if role == "guide" else role)
        if path is None or not path.is_file():
            entries[role] = {
                "file": filename,
                "required": required,
                "present": False,
                "bytes": None,
                "sha256": None,
            }
            contents[role] = None
            if required:
                missing.append(role)
            continue
        payload = _stable_read(path)
        entries[role] = {
            "file": filename,
            "required": required,
            "present": True,
            "bytes": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
        }
        contents[role] = payload

    source_identity = ""
    source_name = f"{slug}.source.pdf"
    expected_source_sha: str | None = None
    source_resolution: tuple[pathlib.Path, bytes] | None = None
    ir_payload = contents.get("ir")
    if ir_payload is not None:
        try:
            snapshotted_ir = json.loads(ir_payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            snapshotted_ir = None
        if isinstance(snapshotted_ir, dict):
            source = snapshotted_ir.get("source") or {}
            source_identity = str(source.get("file", ""))
            source_name = source_identity.split(":", 1)[-1] or source_name
            wanted = source.get("sha256")
            expected_source_sha = str(wanted) if wanted is not None else None
            source_resolution = resolve_source_payload(
                snapshotted_ir, source_root)

    if source_resolution is None:
        entries["source_pdf"] = {
            "file": source_name,
            "logical_identity": source_identity,
            "path": None,
            "required": True,
            "present": False,
            "bytes": None,
            "sha256": None,
            "expected_sha256": expected_source_sha,
        }
        contents["source_pdf"] = None
        missing.append("source_pdf")
    else:
        source_path, source_payload = source_resolution
        source_base = pathlib.Path(source_root).expanduser()
        try:
            logical_path = source_path.relative_to(source_base).as_posix()
        except ValueError:
            logical_path = source_path.name
        source_sha = hashlib.sha256(source_payload).hexdigest()
        entries["source_pdf"] = {
            "file": source_name,
            "logical_identity": source_identity,
            "path": logical_path,
            "required": True,
            "present": True,
            "bytes": len(source_payload),
            "sha256": source_sha,
            "expected_sha256": expected_source_sha,
        }
        contents["source_pdf"] = source_payload
    render_entrypoint = entries["html"]["file"]
    render_assets: dict[str, bytes] = {}
    render_dependencies: list[dict[str, Any]] = []
    render_errors: list[str] = []
    if contents.get("html") is not None:
        render_assets, render_dependencies, render_errors = (
            discover_render_dependencies(
                contents["html"] or b"",
                render_entrypoint,
                html_dir,
            )
        )
    if render_errors:
        missing.append("render_dependencies")
    producer = producer_fingerprint()
    runtime = runtime_provenance()
    # What this manifest claims, and what it deliberately does not.  The claim
    # is about the PUBLISHED evidence: every input and every member of the
    # application runtime closure is named with a logical identity, a byte
    # count and a digest that an independent verifier can resolve and rehash
    # from scratch.  `producer["standalone_attestation_complete"]` stays False
    # forever and is not part of this relation -- standalone self-attestation
    # is what this audit cannot do, which is precisely why the closure is
    # published for somebody else to check.  comb_referee.py rehashes every
    # member and rejects this claim outright when its own derivation
    # disagrees, so claiming it here without earning it fails the referee.
    closure_complete = bool(runtime["application_closure"]["complete"])
    manifest = {
        "schema": INPUT_MANIFEST_SCHEMA,
        "algorithm": "sha256",
        "producer": producer,
        "runtime": runtime,
        "inputs_complete": not missing,
        "attestation_complete": bool(not missing and closure_complete),
        "enforceable": bool(not missing and closure_complete),
        "complete": bool(not missing and closure_complete),
        "missing_required": missing,
        "inputs": entries,
        "render": {
            "entrypoint": render_entrypoint,
            "dependencies": render_dependencies,
            "errors": render_errors,
            "complete": not render_errors,
            "network_policy": (
                "deny-except-retained-relative-resources-and-inline-data"),
        },
    }
    return InputSnapshot(manifest=manifest, contents=contents,
                         missing_required=tuple(missing),
                         render_assets=render_assets,
                         render_entrypoint=render_entrypoint)


def empty_input_manifest() -> dict[str, Any]:
    """Fail-closed placeholder retained even if input snapshotting raises."""
    return {
        "schema": INPUT_MANIFEST_SCHEMA,
        "algorithm": "sha256",
        "producer": producer_fingerprint(),
        "runtime": runtime_provenance(),
        "inputs_complete": False,
        "attestation_complete": False,
        "enforceable": False,
        "complete": False,
        "missing_required": list(REQUIRED_INPUT_ROLES),
        "inputs": {},
        "render": {
            "entrypoint": None,
            "dependencies": [],
            "errors": ["input snapshot did not complete"],
            "complete": False,
            "network_policy": (
                "deny-except-retained-relative-resources-and-inline-data"),
        },
    }


@dataclasses.dataclass
class Bundle:
    slug: str
    ir: dict
    layout: dict | None
    plan: dict | None
    form_html: str | None
    guide_html: str | None
    pdf: bytes | pathlib.Path | None
    form_html_bytes: bytes | None = None
    render_assets: dict[str, bytes] = dataclasses.field(default_factory=dict)
    render_entrypoint: str | None = None
    layout_payload: bytes | None = None
    layout_sha256: str | None = None

    @functools.cached_property
    def pages(self) -> dict[int, dict]:
        return {p["index"]: p for p in self.ir["pages"]}

    @functools.cached_property
    def layout_pages(self) -> dict[int, dict]:
        return {p["index"]: p for p in (self.layout or {}).get("pages", ())}

    @functools.cached_property
    def cells(self) -> list[Cell]:
        return parse_cells(self.form_html) if self.form_html else []

    @functools.cached_property
    def layout_cells(self) -> dict[str, dict]:
        return {c["id"]: c for p in self.layout_pages.values() for c in p["cells"]}

    @functools.cached_property
    def regions(self) -> list[dict]:
        return list((self.plan or {}).get("inline") or ())

    @functools.cached_property
    def relocated_cells(self) -> set[str]:
        out: set[str] = set()
        for region in self.regions:
            out.update(region.get("cell_ids") or ())
        return out

    @functools.cached_property
    def relocated_runs(self) -> set[tuple[int, int]]:
        out: set[tuple[int, int]] = set()
        for region in self.regions:
            for index in region.get("text_run_indices") or ():
                out.add((region["page"], index))
        return out

    @functools.cached_property
    def emitted_runs(self) -> dict[tuple[int, int], str]:
        return parse_run_styles(self.form_html) if self.form_html else {}

    @functools.cached_property
    def ink(self) -> dict[int, InkIndex]:
        """Printed glyph ink, per page, for the runs the form document emits."""
        boxes: dict[int, list[tuple[Rect, Any]]] = collections.defaultdict(list)
        for (page, index) in self.emitted_runs:
            run = self.pages.get(page, {}).get("text_runs", [])[index:index + 1]
            if not run:
                continue
            for box in glyph_boxes(run[0]):
                boxes[page].append((box, index))
        return {page: InkIndex(items) for page, items in boxes.items()}

    @functools.cached_property
    def doc(self):
        if self.pdf is None:
            return None
        import fitz  # local import: only the PDF oracles need it
        if isinstance(self.pdf, bytes):
            return fitz.open(stream=self.pdf, filetype="pdf")
        return fitz.open(self.pdf)

    @functools.cached_property
    def vector_pages(self) -> dict[int, VectorPage]:
        if self.doc is None:
            return {}
        return {index: ordered_vector_paints(self.doc[index - 1])
                for index in self.pages}

    @functools.cached_property
    def source_glyphs(self) -> dict[int, tuple[SourceGlyph, ...]]:
        """Glyphs the source pages draw, in the same space as their paint.

        A page is ABSENT from this map rather than empty when its text
        operators cannot be compared with `vector_pages` -- today that is a
        page the file rotates, whose text trace is reported in the unrotated
        space. Absent means unevaluable, and every reader here fails closed on
        it; there is no such page in this corpus (134 of 134 are unrotated) and
        the day one appears it must not be answered with a guess.
        """
        if self.doc is None:
            return {}
        out: dict[int, tuple[SourceGlyph, ...]] = {}
        for index in self.pages:
            page = self.doc[index - 1]
            if int(getattr(page, "rotation", 0) or 0):
                continue
            out[index] = drawn_glyph_boxes(page)
        return out

    def run_text(self, page: int, index: int) -> str:
        runs = self.pages.get(page, {}).get("text_runs", [])
        return runs[index]["text"] if 0 <= index < len(runs) else ""

    def close(self) -> None:
        if "doc" in self.__dict__ and self.__dict__["doc"] is not None:
            self.__dict__["doc"].close()


def held(**detail: Any) -> dict[str, Any]:
    return {"holds": True, "reason": "", "offenders": [], **detail}


def broken(reason: str, offenders: Sequence[Any] = (),
           *, offender_limit: int | None = MAX_OFFENDERS,
           **detail: Any) -> dict[str, Any]:
    all_offenders = list(offenders)
    published = (all_offenders if offender_limit is None
                 else all_offenders[:offender_limit])
    return {"holds": False, "reason": reason,
            "offender_count": len(all_offenders),
            "offenders_published": len(published),
            "offenders_omitted": len(all_offenders) - len(published),
            "offenders_complete": len(published) == len(all_offenders),
            "offenders": published, **detail}


# --------------------------------------------------------------------------
# assertion 1 -- no input over pre-printed text
# --------------------------------------------------------------------------


def check_inputs_over_printed_text(b: Bundle) -> dict[str, Any]:
    """C6's belt: a taxpayer must not be able to type over the printed form.

    The defect this catches is a statutory rate table emitted as fields -- on
    1700 page 2 the DOM really does offer an editable box over
    "Not over P 250,000". Scored against glyph ink, so the decorative `.` inside
    a money comb is the only kind of hit that is arguable, and it is reported
    rather than excused: a slot that already holds ink is a slot nothing should
    be typed into.
    """
    if b.form_html is None:
        return broken("no emitted form document to check")
    binding_issues = emitted_cell_binding_issues(b)
    if binding_issues:
        return broken(
            f"{len(binding_issues)} emitted cell binding issue(s)",
            binding_issues,
            offender_limit=None,
            cells_checked=len(b.cells),
            emitted_cell_binding_issues=len(binding_issues),
        )
    offenders = []
    for cell in b.cells:
        index = b.ink.get(cell.page)
        if index is None:
            continue
        for box in input_boxes(cell):
            hit = index.any_hit(box)
            if hit is None:
                continue
            offenders.append({
                "cell": cell.id,
                "page": cell.page,
                "run": hit[1],
                "text": b.run_text(cell.page, hit[1])[:60],
            })
            break
    if offenders:
        return broken(f"{len(offenders)} input(s) sit on printed glyph ink", offenders,
                      cells_checked=len(b.cells))
    return held(
        cells_checked=len(b.cells), emitted_cell_binding_issues=0)


# --------------------------------------------------------------------------
# assertion 2 -- comb slots equal printed compartments
# --------------------------------------------------------------------------


def check_comb_slots_match_printed(b: Bundle) -> dict[str, Any]:
    """C5: a slot count short of the printed one centres a digit on a bar.

    The printed count comes from `printed_compartments`, i.e. from the source
    PDF's drawing operators, because two existing oracles disagree about this
    and one of them is lattice.py's own comb code.

    The verdict is scored against the *emitted* slot count, because that is the
    comb a taxpayer types into. The lattice's own slot count is recorded beside
    it: the two differ whenever the emitted document predates a lattice change,
    and telling that apart from a real merge is the difference between a
    regeneration and a fix. Layout and emission relations are published
    independently; a malformed or duplicate emitted comb remains an offender
    even when the source and lattice counts agree.

    A compartment the source itself filled in -- a printed statutory constant,
    the money bullet or a printed `%` in a compartment of its own, or shading
    that says no entry applies -- is emitted without an input on purpose, and
    this assertion reads that fact from the same place it reads the printed
    topology: the source PDF's own operators, through `SourceSlotOracle`. It is deliberately NOT read from emit.py's verdict, nor
    from any marker emit could publish, nor inferred from the missing input
    itself; a check that takes the emitter's word for why the emitter did
    something is a mirror, not a check. Everything else about a compartment
    with no input is unchanged: with no source evidence, or with source
    evidence that the compartment is blank paper, it is still an offender.

    W8: when `printed_compartments` itself raises -- the source genuinely
    cannot settle the topology from vector data alone -- `REVIEWED_COMB_TOPOLOGY`
    is consulted for this exact subject before the verdict is published. A
    reviewed fact decides only a compartment COUNT, never divider positions,
    and a subject it resolves is published with `layout_relation
    == "decided-by-review"`, never silently indistinguishable from one this
    function measured itself. See `resolve_reviewed_comb_topology`.
    """
    if b.layout is None:
        return broken("no layout to read comb geometry from")
    if b.doc is None:
        return broken("source PDF not resolved; printed compartments unknown")
    owner_registry = reviewed_comb_owner_registry(b)
    form_html = getattr(b, "form_html", None)
    # One oracle per page, built once and only from the source file: the
    # glyphs its text operators draw and the paint `printed_compartments`
    # already reads. A bundle that publishes no glyph operators at all, or a
    # page missing from either view, yields an oracle that excuses nothing.
    source_glyph_pages = getattr(b, "source_glyphs", None)
    source_oracles: dict[int, SourceSlotOracle] = {}

    def source_oracle(page_index: int) -> SourceSlotOracle:
        oracle = source_oracles.get(page_index)
        if oracle is not None:
            return oracle
        glyphs = (
            source_glyph_pages.get(page_index)
            if isinstance(source_glyph_pages, dict) else None
        )
        vector = b.vector_pages.get(page_index)
        reason: str | None = None
        if not isinstance(source_glyph_pages, dict):
            reason = "bundle publishes no source glyph operators"
        elif glyphs is None:
            reason = (
                f"page {page_index} has no source glyph operators in the "
                "source paint's coordinate space")
        elif vector is None:
            reason = f"page {page_index} has no source vector paint"
        oracle = SourceSlotOracle(
            glyphs=glyphs if reason is None else None,
            paints=vector.paints if reason is None and vector is not None
            else None,
            unavailable_reason=reason,
        )
        source_oracles[page_index] = oracle
        return oracle

    emitted_by_id: dict[str, list[Cell]] = collections.defaultdict(list)
    for emitted_cell in b.cells:
        emitted_by_id[emitted_cell.id].append(emitted_cell)
    raw_inventory_issues = (
        live_comb_inventory_issues(form_html, b.cells)
        if isinstance(form_html, str) else []
    )
    all_cell_binding_issues = emitted_cell_binding_issues(b)
    emitted_comb_ids = sorted(
        cell_id for cell_id, cells in emitted_by_id.items()
        if any(
            cell.comb_slots_attr is not None
            or SLOT_RE.search(cell.inner) is not None
            or 'data-field-kind="comb"' in cell.attrs
            or "data-comb-capacity=" in cell.attrs
            for cell in cells
        )
    )
    duplicate_emitted_ids = sorted(
        cell_id for cell_id, cells in emitted_by_id.items()
        if len(cells) != 1
    )

    layout_subjects: dict[
        str, list[tuple[int, dict[str, Any]]]
    ] = collections.defaultdict(list)
    all_layout_comb_count = 0
    reported_comb_count = 0
    reported_stats_present = False
    for page_index, page in sorted(b.layout_pages.items()):
        stats = page.get("stats")
        if isinstance(stats, dict) and "comb_cells" in stats:
            reported_stats_present = True
            try:
                reported_comb_count += int(stats["comb_cells"])
            except (TypeError, ValueError):
                reported_comb_count = -1
        for cell in page["cells"]:
            comb = cell.get("comb")
            if not comb:
                continue
            all_layout_comb_count += 1
            if cell["id"] in b.relocated_cells:
                continue
            layout_subjects[cell["id"]].append((page_index, cell))

    # Preserve page/cell document order so exhaustive offender publication is
    # inspectable in the same order as the owning layout.
    expected_ids = list(layout_subjects)
    duplicate_layout_ids = [
        cell_id for cell_id, subjects in layout_subjects.items()
        if len(subjects) != 1
    ]
    # Relocated cells belong to the non-interactive guide document. A live comb
    # with that id in the form document is stale duplicate markup, not an
    # allowed relocation.
    allowed_emitted_ids = set(expected_ids)
    unexpected_emitted_ids = sorted(
        set(emitted_comb_ids) - allowed_emitted_ids
    )
    covered_comb_ids = set(expected_ids) | set(emitted_comb_ids)
    uncovered_cell_binding_issues = [
        issue for issue in all_cell_binding_issues
        if issue["cell"] not in covered_comb_ids
    ]

    offenders: list[dict[str, Any]] = []
    checked_ids: list[str] = []
    layout_mismatches = 0
    layout_unevaluable = 0
    stale_emission = 0
    emission_invalid = 0
    owner_certificates_valid = 0
    owner_certificates_invalid = 0
    source_u_frame_evaluable = 0
    source_certified_unframed_evaluable = 0
    decided_by_review = 0
    decided_by_review_subjects: list[dict[str, Any]] = []
    source_sha256 = (
        (getattr(b, "ir", None) or {}).get("source") or {}
    ).get("sha256")
    bundle_slug = getattr(b, "slug", None)
    if owner_registry.binding_error is not None:
        # Registry integrity is assertion-wide. It must fail even when every
        # active comb is relocated, or when a malformed retained-only ledger
        # has no active comb cell to enter the per-cell loop below.
        offenders.append({
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
            "source_owner_certificate": {
                "criterion": "exact-reviewed-layout-comb-subject-owner-v1",
                "valid": False,
                "reason": owner_registry.binding_error,
                "supplies_topology": False,
            },
            "layout_relation": "registry-invalid",
            "emission_relation": "not-evaluated",
            "failure_kinds": ["comb-owner-registry-invalid"],
            "why": (
                "reviewed comb owner registry is globally invalid: "
                f"{owner_registry.binding_error}"
            ),
        })
    for cell_id in expected_ids:
        subjects = layout_subjects[cell_id]
        checked_ids.append(cell_id)
        # A comb with two layout owners has no settled page, so it gets no
        # source oracle and no compartment of it is excused. It is already an
        # offender on `duplicate-layout-subject`; the point is that ambiguous
        # ownership must not become a way to excuse a missing input.
        emission = emitted_comb_evidence(
            emitted_by_id.get(cell_id, ()),
            source_oracle(subjects[0][0]) if len(subjects) == 1 else None,
        )
        slots = emission["slots"]
        base_stale_emission = (
            not emission["valid"]
            or len(subjects) != 1
            or (len(subjects) == 1
                and slots != subjects[0][1]["comb"]["cells"])
        )
        if len(subjects) != 1:
            stale_emission += int(base_stale_emission)
            emission_invalid += int(not emission["valid"])
            page_index, cell = subjects[0]
            owner_certificates_invalid += 1
            owner_certificate_reason = (
                owner_registry.binding_error
                or f"layout contains {len(subjects)} non-unique comb owners "
                   f"for {cell_id}"
            )
            offenders.append({
                "cell": cell_id,
                "page": page_index,
                "slots": slots,
                "latticed": None,
                "printed": None,
                "printed_divider_x": [],
                "emission_state": emission["state"],
                "physical_slots": emission["physical_slots"],
                "declared_slots": emission["declared_slots"],
                "emitted_occurrences": emission["occurrences"],
                "source_owner_certificate": {
                    "criterion": (
                        "exact-reviewed-layout-comb-subject-owner-v1"),
                    "valid": False,
                    "reason": owner_certificate_reason,
                    "supplies_topology": False,
                },
                "layout_relation": "duplicate-subject",
                "emission_relation": (
                    "invalid" if not emission["valid"] else "unbound"),
                "failure_kinds": ["duplicate-layout-subject"],
                "why": (
                    f"layout contains {len(subjects)} comb subjects with this "
                    "id; exactly one is required"),
            })
            layout_unevaluable += 1
            continue

        page_index, cell = subjects[0]
        emitted_cells = emitted_by_id.get(cell_id, ())
        emitted_cell = emitted_cells[0] if len(emitted_cells) == 1 else None
        emitted_dom_page = (
            emitted_cell.dom_page
            if emitted_cell is not None else None
        )
        # Direct unit fixtures predate DOM parsing. Production cells parsed
        # from real HTML must carry the actual enclosing `.page page-N`.
        if (emitted_cell is not None and emitted_dom_page is None
                and not isinstance(form_html, str)):
            emitted_dom_page = emitted_cell.page
        expected_rect = (
            float(cell["x0"]), float(cell["y0"]),
            float(cell["x1"]), float(cell["y1"]),
        )
        actual_rect = emitted_cell.rect if emitted_cell is not None else None
        rect_deltas = (
            [actual - expected
             for actual, expected in zip(actual_rect, expected_rect)]
            if actual_rect is not None else None
        )
        page_binding_matches = (
            emitted_cell is not None
            and emitted_cell.page == page_index
            and emitted_dom_page == page_index
        )
        rect_binding_matches = (
            rect_deltas is not None
            and all(abs(delta) <= EMITTED_GEOMETRY_EPS_PT
                    for delta in rect_deltas)
        )
        container_binding = {
            "expected_page": page_index,
            "emitted_id_page": (
                emitted_cell.page if emitted_cell is not None else None),
            "emitted_dom_page": emitted_dom_page,
            "page_matches": page_binding_matches,
            "expected_rect": list(expected_rect),
            "actual_rect": (
                list(actual_rect) if actual_rect is not None else None),
            "rect_deltas_pt": rect_deltas,
            "rect_matches": rect_binding_matches,
            "tolerance_pt": EMITTED_GEOMETRY_EPS_PT,
        }

        actual_slot_edges = _emitted_slot_edges(emitted_cell, emission)
        actual_internal_edges = (
            actual_slot_edges[1:-1]
            if actual_slot_edges is not None else None)
        layout_slot_x = cell["comb"].get("slot_x")
        layout_internal_edges: list[float] | None = None
        layout_position_reason: str | None = None
        if (isinstance(layout_slot_x, list)
                and len(layout_slot_x) == int(cell["comb"]["cells"]) + 1):
            try:
                layout_internal_edges = [
                    float(value) for value in layout_slot_x][1:-1]
            except (TypeError, ValueError):
                layout_position_reason = (
                    "layout comb slot_x contains a non-numeric coordinate")
        else:
            layout_position_reason = (
                "layout comb lacks the complete cells-plus-one slot_x vector")
        # The OUTER edges are a different published quantity from `slot_x`'s
        # own outer values, and the difference is half a printed wall.  `slot_x`
        # runs rail CENTRE to rail centre; `writing_x0`/`writing_x1` are those
        # rails' ink edges, which is where the sheet leaves paper to write on
        # and therefore what the emitted outer compartments are laid on.  A
        # layout that publishes no writing edges is not excused here -- the
        # comparison stays comparable and fails with this reason, because a
        # comb whose typing surface cannot be located is not a comb that passes.
        layout_outer_edges: list[float] | None = None
        layout_outer_reason: str | None = None
        try:
            layout_outer_edges = [
                float(cell["comb"]["writing_x0"]),
                float(cell["comb"]["writing_x1"]),
            ]
        except (KeyError, TypeError, ValueError):
            layout_outer_reason = (
                "layout comb publishes no numeric writing_x0/writing_x1 "
                "horizontal writing surface")
        layout_position = _position_evidence(
            actual_internal_edges,
            layout_internal_edges,
            comparable=bool(
                emission["valid"] and slots == cell["comb"]["cells"]),
            unavailable_reason=(
                layout_position_reason
                or ("emitted/layout slot counts differ"
                    if slots != cell["comb"]["cells"]
                    else "emitted slot geometry is invalid")),
        )
        layout_outer_position = _outer_position_evidence(
            (
                [actual_slot_edges[0], actual_slot_edges[-1]]
                if actual_slot_edges is not None else None
            ),
            layout_outer_edges,
            comparable=bool(
                emission["valid"] and slots == cell["comb"]["cells"]),
            unavailable_reason=(
                layout_outer_reason
                or ("emitted/layout slot counts differ"
                    if slots != cell["comb"]["cells"]
                    else "emitted slot geometry is invalid")),
        )
        vector_page = b.vector_pages.get(page_index)
        latticed = cell["comb"]["cells"]
        owner_certificate, owner_certificate_error = owner_registry.resolve(
            page_index, cell)
        if owner_certificate is None:
            owner_certificates_invalid += 1
            owner_certificate_evidence = {
                "criterion": "exact-reviewed-layout-comb-subject-owner-v1",
                "valid": False,
                "reason": owner_certificate_error,
                "supplies_topology": False,
            }
        else:
            owner_certificates_valid += 1
            owner_certificate_evidence = owner_certificate.evidence()
        evidence = {
            "cell": cell_id,
            "page": page_index,
            "slots": slots,
            "latticed": latticed,
            "emission_state": emission["state"],
            "physical_slots": emission["physical_slots"],
            "declared_slots": emission["declared_slots"],
            "emitted_occurrences": emission["occurrences"],
            "slot_indexes": emission.get("slot_indexes"),
            "input_slot_indexes": emission.get("input_slot_indexes"),
            "slot_geometry": emission.get("slot_geometry"),
            "emission_container_binding": container_binding,
            "emission_layout_position": layout_position,
            "emission_layout_outer_position": layout_outer_position,
            "source_owner_certificate": owner_certificate_evidence,
        }
        failure_kinds: list[str] = []
        reasons: list[str] = []
        source_frame: dict[str, Any] | None = None
        printed_positions_available = True
        if owner_certificate is None:
            printed = None
            xs = []
            layout_relation = "unevaluable"
            failure_kinds.append("source-topology-unevaluable")
            reasons.append(
                "invalid reviewed source owner certificate: "
                f"{owner_certificate_error}")
            evidence["source_topology_evidence"] = {
                "criterion": "exact-reviewed-layout-comb-subject-owner-v1",
                "owner_certificate": owner_certificate_evidence,
            }
            layout_unevaluable += 1
        elif vector_page is None:
            printed = None
            xs: list[float] = []
            layout_relation = "unevaluable"
            failure_kinds.append("source-topology-unevaluable")
            reasons.append(f"page {page_index} has no source vector paint")
            layout_unevaluable += 1
        else:
            try:
                printed, xs, source_frame = printed_compartments(
                    vector_page,
                    cell,
                    include_frame=True,
                    owner_certificate=owner_certificate,
                )
            except ValueError as exc:
                printed = None
                xs = []
                layout_relation = "unevaluable"
                failure_kinds.append("source-topology-unevaluable")
                reasons.append(f"unevaluable final-paint topology: {exc}")
                if isinstance(exc, CombTopologyError):
                    evidence["source_topology_evidence"] = exc.evidence
                layout_unevaluable += 1
                # W8: the source cannot settle this from vector data alone --
                # consult the reviewed-topology registry for this EXACT
                # subject. An empty registry (the ship state) always returns
                # (None, None) here and this whole branch is a no-op.
                reviewed_topology, reviewed_topology_error = (
                    resolve_reviewed_comb_topology(
                        bundle_slug, page_index, cell, source_sha256))
                if reviewed_topology_error is not None:
                    failure_kinds.append("reviewed-comb-topology-invalid")
                    reasons.append(reviewed_topology_error)
                    evidence["reviewed_comb_topology"] = {
                        "criterion": "reviewed-comb-topology-v1",
                        "valid": False,
                        "reason": reviewed_topology_error,
                    }
                elif reviewed_topology is not None:
                    layout_unevaluable -= 1
                    decided_by_review += 1
                    printed = reviewed_topology.compartments
                    printed_positions_available = False
                    layout_relation = "decided-by-review"
                    failure_kinds = [
                        kind for kind in failure_kinds
                        if kind != "source-topology-unevaluable"
                    ]
                    reasons = [
                        reason for reason in reasons
                        if not reason.startswith(
                            "unevaluable final-paint topology")
                    ]
                    evidence["reviewed_comb_topology"] = (
                        reviewed_topology.evidence())
                    if latticed != printed:
                        layout_mismatches += 1
                        failure_kinds.append("layout-printed-mismatch")
                        reasons.append(
                            f"layout has {latticed} slots but the reviewed "
                            f"source topology prints {printed} compartments")
                    decided_by_review_subjects.append({
                        "cell": cell_id,
                        "page": page_index,
                        "latticed": latticed,
                        "printed": printed,
                        "reviewed_comb_topology":
                            reviewed_topology.evidence(),
                    })
            else:
                # W8's load-bearing guard: a reviewed fact must never be
                # entered for a subject the audit can already decide for
                # itself. A stray entry here is an integrity ERROR, not a
                # silently-ignored fact.
                if (bundle_slug, page_index, cell_id) in REVIEWED_COMB_TOPOLOGY:
                    failure_kinds.append("reviewed-comb-topology-invalid")
                    reasons.append(
                        "reviewed comb topology registry has an entry for "
                        f"{cell_id} on page {page_index}, but the source "
                        "topology is independently decidable; a reviewed "
                        "fact must never be entered for a subject the audit "
                        "can already evaluate")
                    evidence["reviewed_comb_topology"] = {
                        "criterion": "reviewed-comb-topology-v1",
                        "valid": False,
                        "reason": reasons[-1],
                    }
                if source_frame is None:
                    source_certified_unframed_evaluable += 1
                else:
                    source_u_frame_evaluable += 1
                if latticed == printed:
                    layout_relation = "match"
                else:
                    layout_relation = "mismatch"
                    layout_mismatches += 1
                    failure_kinds.append("layout-printed-mismatch")
                    reasons.append(
                        f"layout has {latticed} slots but source prints "
                        f"{printed} compartments")

        source_position = _position_evidence(
            actual_internal_edges,
            xs if printed is not None and printed_positions_available
            else None,
            comparable=bool(
                emission["valid"]
                and printed is not None
                and printed_positions_available
                and slots == printed
            ),
            unavailable_reason=(
                "source topology is unevaluable"
                if printed is None else
                "source topology was decided by the reviewed-comb-topology "
                "registry, which supplies only a compartment count, not "
                "divider positions"
                if not printed_positions_available else
                "emitted/source slot counts differ"
                if slots != printed else
                "emitted slot geometry is invalid"
            ),
            tolerance_pt=POSITION_TOL_PT,
        )
        evidence["emission_source_position"] = source_position
        # The rails' INK edges, not their centres.  A rail is a painted stroke
        # and its centre runs down the middle of that stroke, so an outer
        # compartment placed on the centre is placed across half the printed
        # rule -- with the label the sheet tucks under that rule beneath it
        # (F208).  The two quantities differ by 0.36-0.48pt here, and against
        # the centre 38.7% of this corpus's 8,306 flush outer edges exceed
        # POSITION_TOL_PT, so comparing a writing edge to a centre is comparing
        # unlike things rather than measuring a defect.  Nothing else moves:
        # the tolerance, the internal-divider comparison and the compartment
        # count are untouched, and `ink_x0`/`ink_x1` are already published here
        # from the source's own paint stream.
        source_outer_edges = (
            [
                float(source_frame["left_rail"]["ink_x1"]),
                float(source_frame["right_rail"]["ink_x0"]),
            ]
            if source_frame is not None else None
        )
        emission_source_outer_position = _outer_position_evidence(
            (
                [actual_slot_edges[0], actual_slot_edges[-1]]
                if actual_slot_edges is not None else None
            ),
            source_outer_edges,
            comparable=bool(
                emission["valid"]
                and printed is not None
                and slots == printed
                and source_frame is not None
            ),
            unavailable_reason=(
                "source U-frame geometry is unevaluable"
                if source_frame is None else
                "emitted/source slot counts differ"
                if slots != printed else
                "emitted slot geometry is invalid"
            ),
            tolerance_pt=POSITION_TOL_PT,
        )
        layout_source_outer_position = _outer_position_evidence(
            layout_outer_edges,
            source_outer_edges,
            comparable=bool(
                printed is not None
                and latticed == printed
                and source_frame is not None
            ),
            unavailable_reason=(
                "source U-frame geometry is unevaluable"
                if source_frame is None else
                "layout/source slot counts differ"
                if latticed != printed else
                layout_outer_reason
            ),
            tolerance_pt=POSITION_TOL_PT,
        )
        evidence.update({
            "source_frame_geometry": source_frame,
            "emission_source_outer_position": (
                emission_source_outer_position),
            "layout_source_outer_position": layout_source_outer_position,
        })

        binding_invalid = False
        if emitted_cell is not None and not page_binding_matches:
            binding_invalid = True
            failure_kinds.append("emission-container-page-mismatch")
            reasons.append(
                "emitted cell id page, enclosing DOM page, and layout page "
                f"must all be {page_index}; got id page "
                f"{emitted_cell.page} and DOM page {emitted_dom_page}")
        if emitted_cell is not None and not rect_binding_matches:
            binding_invalid = True
            failure_kinds.append("emission-container-geometry-mismatch")
            reasons.append(
                "emitted comb container does not occupy its layout cell "
                f"within {EMITTED_GEOMETRY_EPS_PT}pt")
        if (layout_position["comparable"]
                and not layout_position["matches"]):
            binding_invalid = True
            failure_kinds.append("emission-layout-position-mismatch")
            reasons.append(
                "emitted internal slot edges do not match layout comb.slot_x "
                f"within {EMITTED_GEOMETRY_EPS_PT}pt")
        if (layout_outer_position["comparable"]
                and not layout_outer_position["matches"]):
            binding_invalid = True
            failure_kinds.append("emission-layout-outer-position-mismatch")
            reasons.append(
                "emitted physical outer slot edges do not match layout "
                "comb.writing_x0/writing_x1 within "
                f"{EMITTED_GEOMETRY_EPS_PT}pt")
        if (source_position["comparable"]
                and not source_position["matches"]):
            binding_invalid = True
            failure_kinds.append("emission-source-position-mismatch")
            reasons.append(
                "emitted internal slot edges do not match independently "
                "measured source dividers within "
                f"{POSITION_TOL_PT}pt")
        if (emission_source_outer_position["comparable"]
                and not emission_source_outer_position["matches"]):
            binding_invalid = True
            failure_kinds.append("emission-source-outer-position-mismatch")
            reasons.append(
                "emitted physical outer slot edges do not match the source "
                f"U-frame rails' own ink edges within {POSITION_TOL_PT}pt")
        if (layout_source_outer_position["comparable"]
                and not layout_source_outer_position["matches"]):
            binding_invalid = True
            failure_kinds.append("layout-source-outer-position-mismatch")
            reasons.append(
                "layout comb writing edges do not match the source U-frame "
                f"rails' own ink edges within {POSITION_TOL_PT}pt")
        evidence["effective_emission_state"] = (
            "container-binding-invalid"
            if any(kind.startswith("emission-container-")
                   for kind in failure_kinds)
            else "slot-position-invalid"
            if any(kind in {
                "emission-layout-position-mismatch",
                "emission-layout-outer-position-mismatch",
                "emission-source-position-mismatch",
                "emission-source-outer-position-mismatch",
                "layout-source-outer-position-mismatch",
            } for kind in failure_kinds)
            else emission["state"]
        )

        stale_emission += int(base_stale_emission or binding_invalid)
        emission_invalid += int(not emission["valid"] or binding_invalid)
        if not emission["valid"]:
            emission_relation = "invalid"
            failure_kinds.append("invalid-emission")
            reasons.append(emission["reason"])
        else:
            emission_relations = []
            if slots != latticed:
                emission_relations.append("layout")
                failure_kinds.append("emission-layout-mismatch")
                reasons.append(
                    f"emission has {slots} slots but layout has {latticed}")
            if printed is not None and slots != printed:
                emission_relations.append("printed")
                failure_kinds.append("emission-printed-mismatch")
                reasons.append(
                    f"emission has {slots} slots but source prints {printed}")
            emission_relation = (
                "mismatch-" + "-and-".join(emission_relations)
                if emission_relations else "match"
            )
            if binding_invalid:
                emission_relation = "invalid"
        if failure_kinds:
            offenders.append({
                **evidence,
                "printed": printed,
                "printed_divider_x": [
                    round(value, 6) for value in xs],
                "layout_relation": layout_relation,
                "emission_relation": emission_relation,
                "failure_kinds": failure_kinds,
                "why": "; ".join(reasons),
            })

    for cell_id in unexpected_emitted_ids:
        # No non-relocated layout subject owns this comb, so nothing certifies
        # which source rectangle its compartments are. It gets no oracle.
        emission = emitted_comb_evidence(emitted_by_id[cell_id])
        stale_emission += 1
        emission_invalid += int(not emission["valid"])
        cells = emitted_by_id[cell_id]
        offenders.append({
            "cell": cell_id,
            "page": cells[0].page,
            "slots": emission["slots"],
            "latticed": None,
            "printed": None,
            "printed_divider_x": [],
            "emission_state": emission["state"],
            "physical_slots": emission["physical_slots"],
            "declared_slots": emission["declared_slots"],
            "emitted_occurrences": emission["occurrences"],
            "slot_indexes": emission.get("slot_indexes"),
            "input_slot_indexes": emission.get("input_slot_indexes"),
            "slot_geometry": emission.get("slot_geometry"),
            "layout_relation": "not-owned",
            "emission_relation": "unexpected",
            "failure_kinds": ["unexpected-emitted-comb"],
            "why": (
                "emitted comb is not owned by a non-relocated layout subject"),
        })

    for issue in uncovered_cell_binding_issues:
        offenders.append({
            "cell": issue["cell"],
            "page": issue.get("emitted_dom_page"),
            "slots": None,
            "latticed": None,
            "printed": None,
            "printed_divider_x": [],
            "emission_state": "emitted-cell-binding-invalid",
            "effective_emission_state": "emitted-cell-binding-invalid",
            "physical_slots": None,
            "declared_slots": None,
            "emitted_occurrences": issue["emitted_occurrences"],
            "layout_relation": "cell-binding-invalid",
            "emission_relation": "invalid",
            "failure_kinds": [
                "emitted-cell-binding-invalid",
                *issue["failure_kinds"],
            ],
            "emitted_cell_binding_evidence": issue,
            "why": issue["why"],
        })

    for index, issue in enumerate(raw_inventory_issues, 1):
        stale_emission += 1
        emission_invalid += 1
        offenders.append({
            "cell": (
                issue.get("cell_id")
                or f"<live-comb-{index}>"),
            "page": issue.get("dom_page"),
            "slots": issue.get("slot_count"),
            "latticed": None,
            "printed": None,
            "printed_divider_x": [],
            "emission_state": "unowned-live-comb-markup",
            "effective_emission_state": "unowned-live-comb-markup",
            "physical_slots": issue.get("slot_count"),
            "declared_slots": None,
            "emitted_occurrences": 1,
            "layout_relation": "not-owned",
            "emission_relation": "invalid",
            "failure_kinds": ["unowned-live-comb-markup"],
            "raw_dom_evidence": issue,
            "why": issue["reason"],
        })

    inventory_failures: list[str] = []
    if (reported_stats_present
            and reported_comb_count != all_layout_comb_count):
        inventory_failures.append(
            "layout stats report "
            f"{reported_comb_count} combs but page cells contain "
            f"{all_layout_comb_count}")
    if (not expected_ids and not emitted_comb_ids
            and reported_stats_present and reported_comb_count > 0):
        inventory_failures.append(
            "comb inventory is empty despite a positive layout stats signal")
    if inventory_failures:
        offenders.append({
            "cell": "<comb-inventory>",
            "page": None,
            "slots": None,
            "latticed": None,
            "printed": None,
            "printed_divider_x": [],
            "emission_state": "inventory-invalid",
            "physical_slots": None,
            "declared_slots": None,
            "emitted_occurrences": 0,
            "layout_relation": "inventory-invalid",
            "emission_relation": "inventory-invalid",
            "failure_kinds": ["comb-inventory-mismatch"],
            "why": "; ".join(inventory_failures),
        })
        layout_unevaluable += 1

    inventory_complete = not (
        unexpected_emitted_ids
        or duplicate_layout_ids
        or any(
            cell_id in layout_subjects or cell_id in emitted_comb_ids
            for cell_id in duplicate_emitted_ids
        )
        or inventory_failures
        or owner_registry.binding_error is not None
        or raw_inventory_issues
        or uncovered_cell_binding_issues
    )
    counts = {
        "combs_expected": len(expected_ids),
        "combs_checked": len(checked_ids),
        "expected_comb_ids": expected_ids,
        "checked_comb_ids": checked_ids,
        "emitted_comb_ids": emitted_comb_ids,
        "unexpected_emitted_comb_ids": unexpected_emitted_ids,
        "duplicate_layout_comb_ids": duplicate_layout_ids,
        "duplicate_emitted_cell_ids": duplicate_emitted_ids,
        "raw_live_comb_issues": len(raw_inventory_issues),
        "emitted_cell_binding_issues": len(all_cell_binding_issues),
        "inventory_complete": inventory_complete,
        "layout_mismatches": layout_mismatches,
        "layout_unevaluable": layout_unevaluable,
        "owner_certificates_valid": owner_certificates_valid,
        "owner_certificates_invalid": owner_certificates_invalid,
        "source_u_frame_evaluable": source_u_frame_evaluable,
        "source_certified_unframed_evaluable": (
            source_certified_unframed_evaluable),
        "emission_behind_layout": stale_emission,
        "emission_invalid": emission_invalid,
        "decided_by_review": decided_by_review,
        "decided_by_review_subjects": decided_by_review_subjects,
    }
    if offenders:
        return broken(
            f"{len(offenders)} comb subject(s) fail source/layout/emission "
            "agreement or inventory binding",
            offenders,
            offender_limit=None,
            **counts,
        )
    return held(**counts)


# --------------------------------------------------------------------------
# assertion 3 -- every printed money box has an input
# --------------------------------------------------------------------------


def preprinted_width_coverage(index: InkIndex | None,
                              runs: Sequence[dict[str, Any]],
                              cell: dict[str, Any]) -> float:
    """Fraction of the cell's width covered by pre-printed glyph ink.

    emit.py's PrePrintedInk.coverage, mirrored over the audit's own glyph
    index: a run counts against the cell only when at least half of the run's
    own height lies inside it (the line above dipping 0.4pt into a box is not
    text printed in that box), and coverage is the union of the qualifying
    glyphs' x extents clipped to the cell, as a fraction of the cell's width.
    """
    if index is None:
        return 0.0
    x0, y0 = float(cell["x0"]), float(cell["y0"])
    x1, y1 = float(cell["x1"]), float(cell["y1"])
    width = x1 - x0
    if width <= 0:
        return 0.0
    spans: list[tuple[float, float]] = []
    for box, run_index in index.hits((x0, y0, x1, y1)):
        if not isinstance(run_index, int) or not 0 <= run_index < len(runs):
            continue
        run = runs[run_index]
        run_y0, run_y1 = float(run["y0"]), float(run["y1"])
        overlap = min(y1, run_y1) - max(y0, run_y0)
        if run_y1 <= run_y0 or overlap < 0.5 * (run_y1 - run_y0):
            continue
        low, high = max(x0, float(box[0])), min(x1, float(box[2]))
        if high > low:
            spans.append((low, high))
    if not spans:
        return 0.0
    spans.sort()
    covered = 0.0
    low, high = spans[0]
    for span_x0, span_x1 in spans[1:]:
        if span_x0 > high:
            covered += high - low
            low, high = span_x0, span_x1
        else:
            high = max(high, span_x1)
    covered += high - low
    return covered / width


def check_money_boxes_have_inputs(b: Bundle) -> dict[str, Any]:
    """C4: 2000-DST's entire page-1 money grid was unfillable.

    A box is fillable-by-construction if the source drew a comb in it -- a comb
    exists only to receive typed characters -- or if it is an enclosed empty
    rectangle. Either way it must carry an input.

    A comb slot that already holds printed ink is excluded, which is what keeps
    this from contradicting assertion 1, and a comb whose *every* slot is inked
    is not a money box at all: those are the container cells where a header ran
    across a run of ticks, and demanding inputs there would put a field over the
    heading. 49 cells in this corpus are excluded that way, and the count is
    reported so the exclusion cannot hide anything.

    A plain enclosed box the layout calls empty but whose width is mostly
    covered by printed glyph ink is excluded the same way: the layout says
    empty because the straddling run was assigned to a neighbour, while the
    ink is measurably in the box, and the emitter's field_verdict is right to
    refuse an input there -- typing over a printed ATC code is the exact C6
    hazard. The same measured rule the emitter applies (half the run's own
    height inside the cell, ink coverage above half the cell's width) is used
    here, and the exclusion is published as ``boxes_preprinted`` so it cannot
    hide anything.
    """
    if b.form_html is None:
        return broken("no emitted form document to check")
    if b.layout is None:
        return broken("no layout to identify printed boxes from")
    binding_issues = emitted_cell_binding_issues(b)
    if binding_issues:
        return broken(
            f"{len(binding_issues)} emitted cell binding issue(s)",
            binding_issues,
            offender_limit=None,
            boxes_checked=0,
            combs_fully_inked=0,
            boxes_preprinted=0,
            emitted_cell_binding_issues=len(binding_issues),
        )
    offenders, checked, fully_inked, preprinted = [], 0, 0, 0
    bureau_reserved = 0
    decoration = 0
    # Rows keyed exactly as the emitter keys them, per page, so both sides of
    # the rider group the same neighbours.
    rows_by_page: dict[int, dict] = {}
    for page_index, layout_page in b.layout_pages.items():
        page_rows = rows_by_page.setdefault(int(page_index), {})
        for lc in layout_page.get("cells") or ():
            key = (round(float(lc["y0"]), 1), round(float(lc["y1"]), 1))
            page_rows.setdefault(key, []).append(lc)
        for row in page_rows.values():
            row.sort(key=lambda item: float(item["x0"]))
    min_glyph_by_page: dict[int, float] = {}
    for page_index, page_ir in b.pages.items():
        heights = [float(r["y1"]) - float(r["y0"])
                   for r in page_ir.get("text_runs") or ()
                   if float(r["y1"]) - float(r["y0"]) > 0.5]
        if heights:
            min_glyph_by_page[page_index] = min(heights)
    # Read from the pinned PDF's own text operators, never from the layout
    # this assertion's population comes from, so a lattice or emitter mistake
    # cannot manufacture its own excuse. A page whose captions cannot be read
    # yields none, and a box with no reservation is reported exactly as
    # before: the failure direction is to report, never to excuse.
    bureau_captions: dict[int, tuple[Rect, ...]] = {
        page_index: source_bureau_reservations(glyphs)
        for page_index, glyphs in (b.source_glyphs or {}).items()
    }
    by_id = {cell.id: cell for cell in b.cells}
    for cell_id, layout_cell in b.layout_cells.items():
        if cell_id in b.relocated_cells:
            continue
        cell = by_id.get(cell_id)
        if cell is None:
            continue
        index = b.ink.get(cell.page)
        captions = bureau_captions.get(cell.page, ())
        comb = layout_cell.get("comb")
        if comb:
            slots = slot_boxes(cell)
            if not slots:
                checked += 1
                offenders.append({"cell": cell_id, "page": cell.page,
                                  "why": "comb printed, no slots emitted",
                                  "printed_slots": comb["cells"]})
                continue
            free = [(i, box, has) for i, box, has in slots
                    if index is None or index.any_hit(box) is None]
            if not free:
                fully_inked += 1
                continue
            checked += 1
            # A compartment the sheet's own caption reserves for the Bureau is
            # not a money box. 2200-A/C/P's bottom band is one wide rectangle
            # the lattice reads as a two-slot comb, and each half carries its
            # own heading -- "Machine Validation", "Stamp of Receiving
            # Office/AAB" -- printed inside the compartment it governs.
            missing = [i for i, box, has in free
                       if not has and not bureau_reserved_box(box, captions)]
            reserved = [i for i, box, has in free
                        if not has and bureau_reserved_box(box, captions)]
            bureau_reserved += len(reserved)
            if missing:
                offenders.append({"cell": cell_id, "page": cell.page,
                                  "why": "comb slots with no input",
                                  "slots": len(slots), "ink_free": len(free),
                                  "without_input": missing[:8]})
            continue
        border = layout_cell.get("border") or {}
        enclosed = all(border.get(side) for side in ("top", "bottom", "left", "right"))
        # kind used to be redundant here: an enclosed empty cell always
        # classified as a field.  It no longer is -- the lattice demotes an
        # empty bordered strip whose paper cannot hold one glyph of the
        # form's smallest printed line to "blank" (a ruled gap, not a
        # writing surface), and demanding an input inside a 0.79pt gap
        # would re-create the sliver inputs that fix removed.
        if (layout_cell.get("kind") == "field" and enclosed
                and layout_cell.get("is_empty")
                and layout_cell.get("rectangular")):
            runs = b.pages.get(cell.page, {}).get("text_runs") or ()
            if (preprinted_width_coverage(index, runs, layout_cell)
                    > PREPRINTED_COVERAGE):
                preprinted += 1
                continue
            decoration_kind = printed_decoration_reason(
                cell_id, rows_by_page.get(cell.page, {}), layout_cell,
                b.pages.get(cell.page, {}).get("area_fills") or (),
                runs, min_glyph_by_page.get(cell.page))
            if decoration_kind is not None:
                # RIDER: the sheet decorates this cell (its own fill between
                # two combs, its printed ATC constant, or a strip shorter
                # than its own smallest glyph). Counted, never silent.
                decoration += 1
                continue
            checked += 1
            if not input_boxes(cell):
                box = (float(layout_cell["x0"]), float(layout_cell["y0"]),
                       float(layout_cell["x1"]), float(layout_cell["y1"]))
                # Still counted in `checked`: the box was examined and the
                # sheet answered for it. Only the demand for an input is
                # dropped, and `boxes_bureau_reserved` publishes how often.
                if bureau_reserved_box(box, captions):
                    bureau_reserved += 1
                    continue
                offenders.append({"cell": cell_id, "page": cell.page,
                                  "why": "enclosed empty box, no input"})
    if offenders:
        return broken(f"{len(offenders)} of {checked} printed boxes are not fillable",
                      offenders, boxes_decoration=decoration,
                      boxes_checked=checked, combs_fully_inked=fully_inked,
                      boxes_preprinted=preprinted,
                      boxes_bureau_reserved=bureau_reserved)
    return held(
        boxes_decoration=decoration,
        boxes_checked=checked,
        combs_fully_inked=fully_inked,
        boxes_preprinted=preprinted,
        boxes_bureau_reserved=bureau_reserved,
        emitted_cell_binding_issues=0,
    )


# --------------------------------------------------------------------------
# assertion 4 -- nothing form-side below the guide cut
# --------------------------------------------------------------------------


def check_rules_below_guide_cut(b: Bundle) -> dict[str, Any]:
    """C7: an orphaned frame down two-thirds of a page.

    Awarding a straddling rule to the form was chosen so a cut could never lose
    one. The cost is 1600-PT keeping `v85` and `v148`, each 1.44 x 461.33pt,
    with the table they framed now in the guide. Both the IR's form side and the
    emitted SVG are checked: the IR says what we decided to keep, the SVG says
    what a taxpayer sees.
    """
    if not b.regions:
        return held(reason="", cuts=0)
    if b.form_html is None:
        return broken("guide plan cuts pages but no form document to check")
    chunks = page_chunks(b.form_html)
    offenders, fills_below = [], 0
    for region in b.regions:
        page_index, cut = region["page"], float(region["cut_y_pt"])
        claimed = set(region.get("rule_ids") or ())
        for rule in b.pages.get(page_index, {}).get("rules", ()):
            if rule["id"] in claimed:
                continue
            if rule["y1"] > cut + CUT_EPS_PT:
                offenders.append({"page": page_index, "rule": rule["id"],
                                  "y1": rule["y1"], "cut_y": cut, "where": "ir"})
        for match in SVG_RECT_RE.finditer(chunks.get(page_index, "")):
            attrs = attrs_of(match.group(1))
            try:
                bottom = float(attrs["y"]) + float(attrs["height"])
            except (KeyError, ValueError):
                continue
            if bottom <= cut + CUT_EPS_PT:
                continue
            if "data-rule-id" in attrs:
                offenders.append({"page": page_index, "rule": attrs["data-rule-id"],
                                  "y1": round(bottom, 2), "cut_y": cut,
                                  "where": "emitted"})
            else:
                fills_below += 1
    if offenders:
        return broken(f"{len(offenders)} form-side rule(s) cross the guide cut",
                      offenders, cuts=len(b.regions), area_fills_below_cut=fills_below)
    return held(cuts=len(b.regions), area_fills_below_cut=fills_below)


# --------------------------------------------------------------------------
# assertion 5 -- emitted colour equals the IR's
# --------------------------------------------------------------------------


def check_run_colour_matches_ir(b: Bundle) -> dict[str, Any]:
    """C8: 1600-PT and 1600-VT publish 25 white runs each, in black.

    They are BIR reviewer initials, invisible on the official paper. Rendering
    them black turns something the source hid into something that reads as ATC
    data, so this is a disclosure defect and not a styling one.

    The form document is checked run by run against the IR. The guide can only be
    checked by containment -- its reflow merges runs into table cells and drops
    the run ids -- so the test there is that every non-black colour a relocated
    run carries appears somewhere in the guide's markup. That is weaker, and it
    is enough to catch a document that declares no colour at all, which is the
    defect.
    """
    if b.form_html is None:
        return broken("no emitted form document to check")
    offenders = []
    checked = 0
    for page_index, page in sorted(b.pages.items()):
        for index, run in enumerate(page.get("text_runs") or ()):
            key = (page_index, index)
            style = b.emitted_runs.get(key)
            if style is None:
                if key not in b.relocated_runs:
                    offenders.append({"page": page_index, "run": index,
                                      "why": "neither emitted nor relocated",
                                      "text": run["text"][:40]})
                continue
            checked += 1
            got = COLOR_RE.search(style)
            want = int(run.get("color") or 0)
            if got is None:
                offenders.append({"page": page_index, "run": index,
                                  "why": "no colour declared",
                                  "ir_color": f"#{want:06x}"})
            elif int(got.group(1), 16) != want:
                offenders.append({"page": page_index, "run": index,
                                  "why": "colour differs",
                                  "emitted": f"#{int(got.group(1), 16):06x}",
                                  "ir_color": f"#{want:06x}"})
    guide_colours = {int(b.pages[page]["text_runs"][index].get("color") or 0)
                     for page, index in b.relocated_runs
                     if page in b.pages
                     and index < len(b.pages[page]["text_runs"])}
    guide_colours.discard(0)
    if guide_colours:
        markup = (b.guide_html or "").lower()
        for colour in sorted(guide_colours):
            if f"#{colour:06x}" not in markup:
                offenders.append({"why": "relocated run's colour absent from guide",
                                  "ir_color": f"#{colour:06x}",
                                  "runs": sum(1 for p, i in b.relocated_runs
                                              if int(b.pages[p]["text_runs"][i]
                                                     .get("color") or 0) == colour)})
    if offenders:
        return broken(f"{len(offenders)} run colour(s) do not match the IR",
                      offenders, runs_checked=checked)
    return held(runs_checked=checked)


# --------------------------------------------------------------------------
# assertion 6 -- a relocated rate row keeps its description
# --------------------------------------------------------------------------


def check_reflow_rate_without_description(b: Bundle) -> dict[str, Any]:
    """C9: the only *correctness* hazard in the review.

    1600-PT's guide shows a two-line ATC description, then a row holding only
    "3% | WB 050". A reader can attach that rate to the wrong nature of payment,
    which on a withholding return is a wrong remittance. The signature is
    machine-checkable and content-shaped: a row whose only non-empty cells are
    bare rate/code values, with no descriptive text anywhere on the row. A
    continuation row of a two-line description carries descriptive text and is
    not that hazard.

    A rate table reflowed as prose fails too: flattening into running text
    destroys the column relationship outright -- there are no rows left to
    check, and reporting that as "no bad rows found" is exactly the blindness
    this file exists to remove. A page may legitimately hold the rate table
    *and* trailing prose (2551M's ATC band is followed by its Guidelines), so
    the section's data-flow is a comma-separated list and the requirement is
    that a table section exists and its gl-table rows are checked.
    """
    tables = [r for r in b.regions if r.get("marker_pattern") in RATE_TABLE_MARKERS]
    if not tables:
        return held(rate_tables=0)
    if b.guide_html is None:
        return broken(f"{len(tables)} rate table(s) relocated but no guide document")
    sections = {}
    for match in SECTION_RE.finditer(b.guide_html):
        attrs = attrs_of(match.group(1))
        if "data-page" in attrs:
            sections[int(attrs["data-page"])] = (attrs.get("data-flow"), match.group(2))
    offenders, rows_checked = [], 0
    for region in tables:
        page_index = region["page"]
        section = sections.get(page_index)
        if section is None:
            offenders.append({"page": page_index, "why": "no guide section for page"})
            continue
        flow, body = section
        flows = tuple(
            item.strip() for item in (flow or "").split(",") if item.strip())
        if not any(item in {"table", "lattice"} for item in flows):
            offenders.append({"page": page_index, "why": f"rate table reflowed as {flow}; "
                                                         "row structure not recoverable",
                              "marker": region.get("marker", "")[:50]})
            continue
        table_bodies = GL_TABLE_RE.findall(body)
        if not table_bodies:
            offenders.append({"page": page_index,
                              "why": f"section flow declares {flow} but the guide "
                                     "emits no table markup; rows unverifiable",
                              "marker": region.get("marker", "")[:50]})
            continue
        for table_body in table_bodies:
            for row in ROW_RE.finditer(table_body):
                cells = [TAG_RE.sub("", c).replace("&amp;", "&").strip()
                         for c in TD_RE.findall(row.group(1))]
                if len(cells) < 2:
                    continue
                rows_checked += 1
                filled = [c for c in cells if c]
                if not filled:
                    continue
                bare_values = [
                    c for c in filled
                    if RATE_VALUE_RE.fullmatch(c)
                    or ATC_CODE_VALUE_RE.fullmatch(c)
                ]
                if (len(bare_values) == len(filled)
                        and any(RATE_VALUE_RE.fullmatch(c) for c in filled)):
                    offenders.append({"page": page_index,
                                      "why": "rate without description",
                                      "row": [c[:24] for c in cells]})
    if offenders:
        return broken(f"{len(offenders)} relocated rate row(s)/table(s) lost their "
                      f"description", offenders, rate_tables=len(tables),
                      rows_checked=rows_checked)
    return held(rate_tables=len(tables), rows_checked=rows_checked)


# --------------------------------------------------------------------------
# assertion 7 -- a non-upright image is emitted with its transform
# --------------------------------------------------------------------------


def check_image_transform_applied(b: Bundle) -> dict[str, Any]:
    """C3: 2550M's seal prints upside-down, rim lettering bottom-to-top.

    The placement matrix is read from the source PDF rather than from the IR, so
    the assertion holds its own evidence and stays evaluable across an IR schema
    change. Orientation signatures are compared as multisets per page: pairing
    individual placements would need a hash the emitter is free to change, while
    "this page draws one y-flipped image and the SVG flips none" is decidable
    from either document alone.

    A placement the guide plan relocated is subtracted from the source's
    expectation before the comparison: the reflowed guide drops images by
    documented design (emit.py says so with a warning), so the form document
    legitimately emits nothing for it, and demanding it there fails 1702Q for
    following the plan. The subtraction is keyed to the plan's own
    ``image_indices`` claims -- never to "page absent from the form document",
    which would blind the assertion to a genuinely dropped page -- decrements
    only a signature the source actually places, and is published as
    ``relocated_placements`` so a flipped-then-relocated image is recorded,
    never silently skipped.
    """
    if b.doc is None:
        return broken("source PDF not resolved; placement matrices unknown")
    if b.form_html is None:
        return broken("no emitted form document to check")
    claimed: dict[int, list[tuple[int, int, bool]]] = {}
    for region in b.regions:
        page_index = region.get("page")
        images = b.pages.get(page_index, {}).get("images") or ()
        for index in region.get("image_indices") or ():
            if isinstance(index, int) and 0 <= index < len(images):
                claimed.setdefault(page_index, []).append(
                    transform_signature(images[index]["transform"]))
    chunks = page_chunks(b.form_html)
    offenders = []
    placements = 0
    relocated = 0
    for page_index in sorted(b.pages):
        want: collections.Counter = collections.Counter()
        for info in b.doc[page_index - 1].get_image_info(xrefs=True):
            placements += 1
            want[transform_signature(info["transform"])] += 1
        for signature in claimed.get(page_index, ()):
            # A claim without a matching source placement stays in `want`
            # and fails the comparison: fail closed on a plan/source split.
            if want[signature] > 0:
                want[signature] -= 1
                relocated += 1
        want = +want  # drop zeroed signatures; Counter equality below is exact
        got: collections.Counter = collections.Counter()
        for match in SVG_IMAGE_RE.finditer(chunks.get(page_index, "")):
            got[svg_signature(attrs_of(match.group(1)).get("transform"))] += 1
        if want == got:
            continue
        for signature in sorted(set(want) | set(got)):
            if want[signature] == got[signature]:
                continue
            offenders.append({"page": page_index,
                              "orientation": {"x_sign": signature[0],
                                              "y_sign": signature[1],
                                              "sheared": signature[2]},
                              "source_placements": want[signature],
                              "emitted": got[signature]})
    if offenders:
        return broken("emitted image orientation differs from the source's",
                      offenders, placements=placements,
                      relocated_placements=relocated)
    return held(placements=placements, relocated_placements=relocated)


# --------------------------------------------------------------------------
# assertion 8 -- no invented codepoints
# --------------------------------------------------------------------------


INVENTED_SUSPECTS = "?§"


def check_no_invented_codepoints(b: Bundle) -> dict[str, Any]:
    """C1: a character that looks like content but is not in the source.

    Both known lies are covered. `?` is what a dropped glyph used to become, and
    a `?` inside a checkbox makes lattice.py classify the cell as a label, so the
    taxpayer cannot tick it. `§` is subtler and worse: on 2550M page 4 and 2553
    page 2 seven glyphs come from a Wingdings face with no ToUnicode CMap, and
    rawdict reports SECTION SIGN -- the WinAnsi meaning of a byte the font does
    not use. Nothing downstream can tell that apart from a real section sign.

    Checked per glyph origin against `get_texttrace()`, which reports U+FFFD
    rather than guessing, so this names the exact character. A `?` the source
    really does state passes: 2200S's checkbox glyph is drawn from codepoint
    U+003F and the assertion says so, which is the honest answer even though the
    glyph is a Wingdings box.
    """
    if b.doc is None:
        return broken("source PDF not resolved; drawn codepoints unknown")
    offenders, examined = [], 0
    for page_index, page in sorted(b.pages.items()):
        runs = page.get("text_runs") or ()
        suspect = [(i, r) for i, r in enumerate(runs)
                   if any(ch in r["text"] for ch in INVENTED_SUSPECTS)]
        if not suspect:
            continue
        drawn = drawn_codepoints(b.doc[page_index - 1])
        for index, run in suspect:
            offsets = run.get("char_origin_offsets_pt") or ()
            baseline = run.get("baseline_y")
            for position, char in enumerate(run["text"]):
                if char not in INVENTED_SUSPECTS:
                    continue
                examined += 1
                if baseline is None or position >= len(offsets):
                    offenders.append({"page": page_index, "run": index,
                                      "char_index": position, "char": char,
                                      "why": "run has no per-glyph origin to check"})
                    continue
                key = (round(run.get("origin_x", run["x0"]) + offsets[position], 2),
                       round(baseline, 2))
                codepoints = drawn.get(key)
                if codepoints is None:
                    offenders.append({"page": page_index, "run": index,
                                      "char_index": position, "char": char,
                                      "why": "no glyph drawn at this origin"})
                elif ord(char) not in codepoints:
                    offenders.append({"page": page_index, "run": index,
                                      "char_index": position, "char": char,
                                      "why": "source draws a different codepoint",
                                      "source_codepoints": [f"U+{c:04X}"
                                                            for c in sorted(codepoints)],
                                      "font": run.get("font"),
                                      "text": run["text"][:40]})
    if offenders:
        return broken(f"{len(offenders)} character(s) the source does not state",
                      offenders, characters_examined=examined)
    return held(characters_examined=examined)


# --------------------------------------------------------------------------
# assertion 9 -- no input spans a printed compartment divider
# --------------------------------------------------------------------------


def check_inputs_span_no_printed_divider(b: Bundle) -> dict[str, Any]:
    """C5's other half: one wide box where the sheet printed several.

    `comb_slots_match_printed` cannot see this, and the reason is structural
    rather than a tuning miss: its inventory is the LAYOUT's comb subjects, so
    a printed comb the lattice never recognised as one is not in the
    population at all.  This assertion has no inventory.  It walks the
    emitted inputs and asks the pinned PDF whether it drew a compartment
    divider inside any of them -- 2550Q's `p1c41` is a single 437pt input
    lying across 30 printed compartments, and nothing in the layout says so.

    A divider is counted only when its x centre is more than
    DIVIDER_INTERIOR_PT inside BOTH of the input's edges, so an input drawn
    edge-to-edge over its own printed frame is never reported against its own
    walls, and only when it shares at least DIVIDER_MIN_Y_OVERLAP_PT of the
    input's height, so the tick of the row above does not count.

    The offender is the INPUT, not the divider: one box across thirty
    compartments is one defect, and publishing thirty would bury it.
    """
    if b.form_html is None:
        return broken("no emitted form document to check",
                      inputs_checked=0, printed_dividers_detected=0)
    if b.doc is None:
        return broken(
            "source PDF not resolved; printed compartment dividers unknown",
            inputs_checked=0, printed_dividers_detected=0)
    binding_issues = emitted_cell_binding_issues(b)
    if binding_issues:
        return broken(
            f"{len(binding_issues)} emitted cell binding issue(s)",
            binding_issues,
            offender_limit=None,
            inputs_checked=0,
            printed_dividers_detected=0,
            emitted_cell_binding_issues=len(binding_issues),
        )
    per_page: dict[int, tuple[tuple[tuple[float, VectorPaint], ...],
                              list[float]]] = {}
    detected = 0
    checked = 0
    offenders: list[dict[str, Any]] = []
    for cell in b.cells:
        page = b.vector_pages.get(cell.page)
        if page is None:
            continue
        if cell.page not in per_page:
            dividers = source_printed_dividers(page, PointToneIndex(page.paints))
            per_page[cell.page] = (dividers, [x for x, _ in dividers])
            detected += len(dividers)
        dividers, centres = per_page[cell.page]
        for box in input_boxes(cell):
            checked += 1
            low = bisect.bisect_left(centres, box[0] + DIVIDER_INTERIOR_PT)
            high = bisect.bisect_right(centres, box[2] - DIVIDER_INTERIOR_PT)
            spanned = [
                round(centre, 2) for centre, paint in dividers[low:high]
                if min(box[3], paint.y1) - max(box[1], paint.y0)
                >= DIVIDER_MIN_Y_OVERLAP_PT
            ]
            if spanned:
                offenders.append({
                    "cell": cell.id,
                    "page": cell.page,
                    "input": [round(value, 2) for value in box],
                    "printed_dividers_spanned": len(spanned),
                    "divider_x": spanned[:8],
                })
    if offenders:
        return broken(
            f"{len(offenders)} input(s) span a printed compartment divider",
            offenders,
            inputs_checked=checked,
            printed_dividers_detected=detected,
            emitted_cell_binding_issues=0,
        )
    return held(
        inputs_checked=checked,
        printed_dividers_detected=detected,
        emitted_cell_binding_issues=0,
    )


# --------------------------------------------------------------------------
# assertion 10 -- a printed box whose row peers are fillable must be fillable
# --------------------------------------------------------------------------


def check_printed_box_peers_all_fillable(b: Bundle) -> dict[str, Any]:
    """C4/G03: the box the lattice called a label, so no input was ever made.

    `money_boxes_have_inputs` cannot see this one either, and again for a
    structural reason: it enumerates candidates from `b.layout_cells` and
    accepts only `layout_cell["kind"] == "field"`, so a printed box the
    lattice mis-classified as `label` never enters its population.  A `field`
    cell with zero inputs occurs 0 times in 9,971 -- the population is clean
    because the mistake removes its members from it.

    So the population here comes from the source instead
    (`source_printed_boxes`), and the expectation comes from the source's own
    layout: if a row of the sheet draws several identical blank boxes -- same
    top edge, same bottom edge -- and some of them carry an emitted input,
    then a taxpayer is plainly meant to be able to write in all of them.  A
    row where NONE is fillable proves nothing and is not reported: it may
    legitimately be Bureau-only, and this assertion refuses to guess.  That
    self-denial is what makes the ones it does report unambiguous -- the two
    that opened this class, 0619-E and 0620's Amended-Return YES box, are the
    single offender on their whole sheet.
    """
    if b.form_html is None:
        return broken("no emitted form document to check",
                      printed_boxes_checked=0, peer_rows_checked=0)
    if b.doc is None:
        return broken("source PDF not resolved; printed boxes unknown",
                      printed_boxes_checked=0, peer_rows_checked=0)
    binding_issues = emitted_cell_binding_issues(b)
    if binding_issues:
        return broken(
            f"{len(binding_issues)} emitted cell binding issue(s)",
            binding_issues,
            offender_limit=None,
            printed_boxes_checked=0,
            peer_rows_checked=0,
            boxes_unevaluable=0,
            emitted_cell_binding_issues=len(binding_issues),
        )
    inputs_by_page: dict[int, list[tuple[str, Rect]]] = (
        collections.defaultdict(list))
    for cell in b.cells:
        for box in input_boxes(cell):
            inputs_by_page[cell.page].append((cell.id, box))
    offenders: list[dict[str, Any]] = []
    checked = 0
    peer_rows = 0
    unevaluable = 0
    bureau_reserved = 0
    for page_index in sorted(b.pages):
        page = b.vector_pages.get(page_index)
        if page is None:
            continue
        glyphs = b.source_glyphs.get(page_index)
        if glyphs is None:
            return broken(
                f"page {page_index} draws text this audit cannot place; "
                "printed boxes unknown",
                printed_boxes_checked=checked,
                peer_rows_checked=peer_rows,
                boxes_unevaluable=unevaluable,
            )
        captions = source_bureau_reservations(glyphs)
        boxes, page_unevaluable = source_printed_boxes(
            page, glyphs, PointToneIndex(page.paints))
        unevaluable += page_unevaluable
        checked += len(boxes)
        emitted = inputs_by_page.get(page_index, ())
        filled_by: dict[Rect, str | None] = {}
        for box in boxes:
            box_area = (box[2] - box[0]) * (box[3] - box[1])
            owner: str | None = None
            for cell_id, rect in emitted:
                width = min(box[2], rect[2]) - max(box[0], rect[0])
                height = min(box[3], rect[3]) - max(box[1], rect[1])
                if width <= 0 or height <= 0:
                    continue
                smaller = min(box_area,
                              (rect[2] - rect[0]) * (rect[3] - rect[1]))
                if (smaller > 0
                        and width * height
                        >= PRINTED_BOX_FILL_MIN_FRACTION * smaller):
                    owner = cell_id
                    break
            filled_by[box] = owner
        rows: dict[tuple[float, float], list[Rect]] = (
            collections.defaultdict(list))
        for box in boxes:
            rows[(round(box[1], 2), round(box[3], 2))].append(box)
        for key in sorted(rows):
            group = rows[key]
            if len(group) < 2:
                continue
            peer_rows += 1
            filled = [box for box in group if filled_by[box] is not None]
            if not filled:
                continue
            for box in group:
                if filled_by[box] is not None:
                    continue
                # This assertion's premise is that the SHEET has already said
                # these boxes are the same kind of thing. Where the sheet
                # itself says one of them is the Bureau's, the premise is
                # false for that box: 0605's BCS blank shares its row with
                # four taxpayer boxes and is captioned "(To be filled up by
                # the BIR)" in the source's own text operators. Removing it is
                # not a relaxation -- an input there is finding F147, a
                # blocker. The removals are counted and published, so the
                # exclusion can never be silent.
                if bureau_reserved_box(box, captions):
                    bureau_reserved += 1
                    continue
                offenders.append({
                    "page": page_index,
                    "box": [round(value, 2) for value in box],
                    "row_peers": len(group),
                    "row_peers_with_input": len(filled),
                    "peer_with_input": filled_by[filled[0]],
                })
    if offenders:
        return broken(
            f"{len(offenders)} printed box(es) have no input while a row peer "
            "does",
            offenders,
            printed_boxes_checked=checked,
            peer_rows_checked=peer_rows,
            boxes_unevaluable=unevaluable,
            boxes_bureau_reserved=bureau_reserved,
            emitted_cell_binding_issues=0,
        )
    return held(
        printed_boxes_checked=checked,
        peer_rows_checked=peer_rows,
        boxes_unevaluable=unevaluable,
        boxes_bureau_reserved=bureau_reserved,
        emitted_cell_binding_issues=0,
    )


CHECKS = {
    "inputs_over_printed_text": check_inputs_over_printed_text,
    "comb_slots_match_printed": check_comb_slots_match_printed,
    "money_boxes_have_inputs": check_money_boxes_have_inputs,
    "rules_below_guide_cut": check_rules_below_guide_cut,
    "run_colour_matches_ir": check_run_colour_matches_ir,
    "reflow_rate_without_description": check_reflow_rate_without_description,
    "image_transform_applied": check_image_transform_applied,
    "no_invented_codepoints": check_no_invented_codepoints,
    "inputs_span_no_printed_divider": check_inputs_span_no_printed_divider,
    "printed_box_peers_all_fillable": check_printed_box_peers_all_fillable,
}
assert tuple(CHECKS) == ASSERTION_KEYS, (
    "GOAL.md names the first eight, in this order; the two field-layer "
    "assertions follow them")


def evaluate_assertions(bundle: Bundle) -> dict[str, Any]:
    """Run every assertion and flatten it into the per-form record.

    A raising check is a failing check. It cannot be a passing one: an assertion
    that throws has not looked at the form, and "we did not look" is the exact
    reading of the audit that let 137 defects through.
    """
    details: dict[str, Any] = {}
    flat: dict[str, Any] = {}
    for key, check in CHECKS.items():
        try:
            detail = check(bundle)
        except Exception as exc:  # noqa: BLE001 - see docstring
            detail = broken(f"{type(exc).__name__}: {exc}",
                            trace=traceback.format_exc(limit=2))
        details[key] = detail
        flat[key] = bool(detail["holds"])
    flat["assertions"] = details
    flat["assertions_held"] = sum(1 for key in ASSERTION_KEYS if flat[key])
    return flat


def load_bundle(slug: str, ir_dir: pathlib.Path, html_dir: pathlib.Path,
                layout_dir: pathlib.Path, guide_dir: pathlib.Path | None,
                source_root: str,
                input_snapshot: InputSnapshot | None = None) -> Bundle:
    snapshot = input_snapshot or snapshot_inputs(
        slug, ir_dir, html_dir, layout_dir, guide_dir, source_root)
    if snapshot.missing_required:
        raise FileNotFoundError(
            "required audit input(s) missing: "
            + ", ".join(snapshot.missing_required))

    def text(role: str) -> str:
        payload = snapshot.contents[role]
        if payload is None:
            raise FileNotFoundError(f"required audit input missing: {role}")
        return payload.decode("utf-8")

    ir = json.loads(text("ir"))
    layout_payload = snapshot.contents["layout"]
    if layout_payload is None:
        raise FileNotFoundError("required audit input missing: layout")
    layout_sha256 = (
        snapshot.manifest.get("inputs", {}).get("layout", {}).get("sha256")
    )
    guide_html = snapshot.contents["guide_html"]
    return Bundle(
        slug=slug,
        ir=ir,
        layout=json.loads(layout_payload.decode("utf-8")),
        plan=json.loads(text("guide")),
        form_html=text("html"),
        guide_html=guide_html.decode("utf-8") if guide_html is not None else None,
        pdf=snapshot.contents["source_pdf"],
        form_html_bytes=snapshot.contents["html"],
        render_assets=dict(snapshot.render_assets),
        render_entrypoint=snapshot.render_entrypoint,
        layout_payload=layout_payload,
        layout_sha256=(
            str(layout_sha256) if layout_sha256 is not None else None),
    )


def form_side(reference: dict, plan: dict | None) -> tuple[dict, dict]:
    """Drop everything the guide plan moved out, from the reference IR.

    The form document no longer contains the guide's rules and strings, so
    scoring it against the whole source IR counts correctly-relocated content as
    missing. That is how a corpus at 100% rules came to read 42/51: nothing had
    moved on the sheet, the denominator was simply the wrong one.

    Indices are removed high-to-low so earlier removals cannot shift later ones.
    """
    if not plan or not plan.get("inline"):
        return reference, {"rules": 0, "text_runs": 0, "images": 0}

    filtered = copy.deepcopy(reference)
    removed = {"rules": 0, "text_runs": 0, "images": 0}
    by_page = {region["page"]: region for region in plan["inline"]}

    for page in filtered["pages"]:
        region = by_page.get(page["index"])
        if region is None:
            continue

        claimed_rules = set(region.get("rule_ids") or ())
        if claimed_rules:
            before = len(page["rules"])
            page["rules"] = [r for r in page["rules"] if r["id"] not in claimed_rules]
            removed["rules"] += before - len(page["rules"])

        for index in sorted(region.get("text_run_indices") or (), reverse=True):
            if 0 <= index < len(page["text_runs"]):
                del page["text_runs"][index]
                removed["text_runs"] += 1

        for index in sorted(region.get("image_indices") or (), reverse=True):
            if 0 <= index < len(page["images"]):
                del page["images"][index]
                removed["images"] += 1

        # Fills and paths are relocated too, and leaving them behind is what made
        # four pages look non-empty to the reference while emit.py correctly
        # dropped them: 0605 page 2 held 44 orphan fills and 532 orphan paths
        # after every rule, run and image on it had moved to the guide.
        for key, bucket in (("area_fill_indices", "area_fills"),
                            ("path_indices", "paths")):
            claimed = region.get(key) or ()
            items = page.get(bucket) or []
            for index in sorted(claimed, reverse=True):
                if 0 <= index < len(items):
                    del items[index]
                    removed[bucket] = removed.get(bucket, 0) + 1

        # stats are what the rule denominator is read from, so they must follow.
        page["stats"]["rules_structural"] = sum(
            1 for r in page["rules"] if r["role"] == "structural")

    # A page whose every element was relocated is not printed by the form at
    # all -- emit.py drops it rather than emitting a blank sheet. The reference
    # has to drop it too, or the page counts disagree and verify calls that a
    # paper mismatch: five forms failed exactly this way (0605, 1702Q, 2200P,
    # 2550M, 2553), all with identical page dimensions and rotations.
    kept = [p for p in filtered["pages"]
            if p["rules"] or p["text_runs"] or p["images"]
            or p.get("area_fills") or p.get("paths")]
    removed["pages"] = len(filtered["pages"]) - len(kept)
    if removed["pages"]:
        filtered["pages"] = kept
        for index, page in enumerate(kept, 1):
            page["index"] = index
        filtered["source"] = dict(filtered.get("source") or {})
        filtered["source"]["page_count"] = len(kept)

    return filtered, removed


@dataclasses.dataclass(frozen=True)
class MaterializedRenderTree:
    root: pathlib.Path
    entrypoint: pathlib.Path
    expected: dict[str, bytes]


def _validate_materialized_render_tree(
        tree: MaterializedRenderTree,
        phase: str,
        ) -> None:
    actual = {
        path.relative_to(tree.root).as_posix()
        for path in tree.root.rglob("*")
        if path.is_file() or path.is_symlink()
    }
    expected = set(tree.expected)
    if actual != expected:
        raise RuntimeError(
            f"isolated render tree changed {phase}: "
            f"missing={sorted(expected - actual)}, "
            f"unexpected={sorted(actual - expected)}")
    for logical, payload in sorted(tree.expected.items()):
        path = tree.root / pathlib.PurePosixPath(logical)
        if path.is_symlink() or not path.is_file():
            raise RuntimeError(
                f"isolated render dependency changed {phase}: {logical}")
        if _stable_read(path) != payload:
            raise RuntimeError(
                f"isolated render dependency bytes changed {phase}: "
                f"{logical}")


@contextlib.contextmanager
def materialized_form_snapshot(
        bundle: Bundle, _legacy_html_dir: pathlib.Path | None = None,
        ) -> Iterable[MaterializedRenderTree]:
    """Build a private tree containing only snapshotted browser inputs."""
    if bundle.form_html is None:
        raise FileNotFoundError("required audit input missing: html")
    html_payload = (
        bundle.form_html_bytes
        if bundle.form_html_bytes is not None
        else bundle.form_html.encode("utf-8")
    )
    entrypoint = bundle.render_entrypoint or f"{bundle.slug}.html"
    expected = {entrypoint: html_payload, **bundle.render_assets}
    with tempfile.TemporaryDirectory(
            prefix=f"formgen-{bundle.slug}-render-") as temporary:
        root = pathlib.Path(temporary)
        root.chmod(0o700)
        for logical, payload in sorted(expected.items()):
            path = root / pathlib.PurePosixPath(logical)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.parent.chmod(0o700)
            path.write_bytes(payload)
            path.chmod(0o400)
        tree = MaterializedRenderTree(
            root=root,
            entrypoint=root / pathlib.PurePosixPath(entrypoint),
            expected=expected,
        )
        _validate_materialized_render_tree(tree, "before Chromium")
        try:
            yield tree
        finally:
            _validate_materialized_render_tree(tree, "after Chromium")


SYNTHETIC_RENDER_ORIGIN = "https://formgen.invalid"
RENDER_REQUEST_POLICY = "formgen-snapshot-only-v1"
RENDER_WORKER_SCHEMA = "formgen-isolated-render-worker-v1"
RENDER_HARD_TIMEOUT_SECONDS = 60.0
RENDER_WORKER_KILL_GRACE_SECONDS = 2.0
CHROMIUM_PDF_DATE_RE = re.compile(
    rb"/(?P<key>CreationDate|ModDate)\s*"
    rb"\(D:\d{14}[+-]\d{2}'\d{2}'\)")


def _render_request_path(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    if (parsed.scheme != "https"
            or parsed.netloc != "formgen.invalid"
            or parsed.query):
        raise ValueError("request is outside the synthetic snapshot origin")
    decoded = urllib.parse.unquote(parsed.path).lstrip("/")
    if not decoded:
        raise ValueError("request has no snapshot path")
    logical = posixpath.normpath(decoded)
    if (logical.startswith("../") or logical in {"", ".", ".."}
            or "\\" in logical or "\x00" in logical):
        raise ValueError("request path escapes the snapshot")
    return logical


def _retained_request_payload(
        url: str,
        method: str,
        expected: dict[str, bytes],
        ) -> tuple[str, bytes]:
    if method != "GET":
        raise ValueError(
            "only GET is allowed by retained snapshot policy")
    logical = _render_request_path(url)
    payload = expected.get(logical)
    if payload is None:
        raise ValueError("request is absent from retained closure")
    return logical, payload


class RenderDeadlineExceeded(RuntimeError):
    def __init__(self, deadline_seconds: float) -> None:
        self.deadline_seconds = deadline_seconds
        super().__init__(
            "Chromium render exceeded its deterministic hard deadline "
            f"of {deadline_seconds:g} seconds")


def _render_deadline_evidence(
        error: RenderDeadlineExceeded,
        ) -> dict[str, Any]:
    return {
        "measured": False,
        "hard_failure": "render-hard-deadline-exceeded",
        "roundtrip_liveness": {
            "status": "unevaluable",
            "hard_failure": "render-hard-deadline-exceeded",
            "hard_deadline_seconds": error.deadline_seconds,
            "cleanup_policy": "kill-worker-and-chromium-process-group",
        },
    }


def _render_snapshotted_tree_in_process(
        expected: dict[str, bytes],
        entrypoint: str,
        width_pt: float,
        height_pt: float,
        operation_timeout_ms: int,
        ) -> tuple[bytes, dict[str, Any], dict[str, Any]]:
    """Worker-side render through one exact, deny-by-default session."""
    requested: list[str] = []
    blocked: list[dict[str, str]] = []
    launch_args = [
        "--disable-background-networking",
        "--disable-component-update",
        "--disable-default-apps",
        "--disable-sync",
        "--metrics-recording-only",
        "--no-first-run",
    ]
    zero = {"top": "0", "bottom": "0", "left": "0", "right": "0"}
    with _bound_playwright_runtime() as runtime:
        browser = runtime.playwright.chromium.launch(
            executable_path=str(runtime.chromium_path),
            args=launch_args,
        )
        live_version = browser.version
        context = browser.new_context(
            service_workers="block",
            offline=True,
        )
        context.set_default_timeout(operation_timeout_ms)
        context.set_default_navigation_timeout(operation_timeout_ms)

        def route_request(route: Any) -> None:
            url = route.request.url
            try:
                logical, payload = _retained_request_payload(
                    url, route.request.method, expected)
            except ValueError as exc:
                blocked.append({"url": url, "reason": str(exc)})
                route.abort()
                return
            requested.append(logical)
            content_type = mimetypes.guess_type(logical)[0]
            if logical == entrypoint:
                content_type = "text/html; charset=utf-8"
            route.fulfill(
                status=200,
                body=payload,
                content_type=content_type or "application/octet-stream",
                headers={
                    "Cache-Control": "no-store",
                    "X-Content-Type-Options": "nosniff",
                },
            )

        context.route("**/*", route_request)
        blocked_websockets: list[str] = []
        if not hasattr(context, "route_web_socket"):
            raise RuntimeError(
                "bound Playwright runtime cannot enforce WebSocket policy")

        def route_websocket(websocket: Any) -> None:
            blocked_websockets.append(websocket.url)
            # Do not call a sync Playwright method from this callback.
            # `close()` re-enters the sync event loop and deadlocks. A
            # WebSocketRoute left unconnected performs no network I/O;
            # the post-load policy check rejects the render.

        context.route_web_socket("**/*", route_websocket)

        def reject_blocked_requests() -> None:
            if blocked or blocked_websockets:
                raise RuntimeError(
                    "Chromium requested resources outside the retained "
                    "snapshot: "
                    f"{json.dumps(blocked, sort_keys=True)}; "
                    "websockets="
                    f"{json.dumps(sorted(blocked_websockets))}")

        page = context.new_page()
        try:
            page.goto(
                f"{SYNTHETIC_RENDER_ORIGIN}/{entrypoint}",
                wait_until="load",
            )
            page.evaluate(
                "() => document.fonts.ready.then(() => true)")
            page.wait_for_load_state("networkidle")
            reject_blocked_requests()
            pdf_payload = page.pdf(
                width=f"{width_pt / 72.0:.6f}in",
                height=f"{height_pt / 72.0:.6f}in",
                margin=zero,
                print_background=True,
                prefer_css_page_size=False,
                scale=1.0,
            )
        finally:
            context.close()
            browser.close()
        reject_blocked_requests()
        provenance = copy.deepcopy(runtime.provenance)
        provenance.update({
            "live_browser_version": live_version,
            "explicit_executable_path_used": True,
            "launch_args": launch_args,
            "service_workers": "block",
            "browser_context_offline": True,
            "websocket_policy": "record-and-leave-unconnected",
            "request_policy": RENDER_REQUEST_POLICY,
            "playwright_operation_timeout_ms": operation_timeout_ms,
        })
    request_record = {
        "policy": RENDER_REQUEST_POLICY,
        "synthetic_origin": SYNTHETIC_RENDER_ORIGIN,
        "fulfilled": sorted(set(requested)),
        "fulfilled_requests": len(requested),
        "blocked": blocked,
        "blocked_requests": len(blocked),
        "blocked_websockets": sorted(blocked_websockets),
        "all_requests_from_retained_closure": (
            not blocked and not blocked_websockets),
    }
    return bytes(pdf_payload), provenance, request_record


def _render_worker_job(
        tree: MaterializedRenderTree,
        width_pt: float,
        height_pt: float,
        deadline_seconds: float,
        ) -> bytes:
    entrypoint = tree.entrypoint.relative_to(tree.root).as_posix()
    resources = []
    for logical, payload in sorted(tree.expected.items()):
        resources.append({
            "path": logical,
            "bytes": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
            "payload_base64": base64.b64encode(payload).decode("ascii"),
        })
    job = {
        "schema": RENDER_WORKER_SCHEMA,
        "producer_sha256": hashlib.sha256(
            _AUDIT_SOURCE_PAYLOAD).hexdigest(),
        "entrypoint": entrypoint,
        "width_pt": float(width_pt),
        "height_pt": float(height_pt),
        "hard_deadline_seconds": float(deadline_seconds),
        "resources": resources,
    }
    return json.dumps(
        job, sort_keys=True, separators=(",", ":"),
        ensure_ascii=True).encode("ascii")


def _decode_render_worker_job(
        payload: bytes,
        ) -> tuple[dict[str, bytes], str, float, float, float]:
    try:
        job = json.loads(payload.decode("ascii"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("isolated render worker job is not canonical JSON") from exc
    if not isinstance(job, dict) or job.get("schema") != RENDER_WORKER_SCHEMA:
        raise ValueError("isolated render worker job has an invalid schema")
    producer_sha = hashlib.sha256(_AUDIT_SOURCE_PAYLOAD).hexdigest()
    if job.get("producer_sha256") != producer_sha:
        raise ValueError(
            "isolated render worker job names a different audit producer")
    entrypoint = job.get("entrypoint")
    resources = job.get("resources")
    if not isinstance(entrypoint, str) or not isinstance(resources, list):
        raise ValueError("isolated render worker job is incomplete")
    expected: dict[str, bytes] = {}
    observed_paths: list[str] = []
    for index, item in enumerate(resources):
        if not isinstance(item, dict):
            raise ValueError(
                f"isolated render resource {index} is not an object")
        logical = item.get("path")
        if not isinstance(logical, str):
            raise ValueError(
                f"isolated render resource {index} has no path")
        normalized = posixpath.normpath(logical)
        if (normalized != logical
                or logical in {"", ".", ".."}
                or logical.startswith("../")
                or posixpath.isabs(logical)
                or "\\" in logical
                or "\x00" in logical):
            raise ValueError(
                f"isolated render resource path is invalid: {logical!r}")
        try:
            decoded = base64.b64decode(
                item.get("payload_base64", ""), validate=True)
        except (binascii.Error, TypeError, ValueError) as exc:
            raise ValueError(
                f"isolated render resource is not base64: {logical}") from exc
        if (item.get("bytes") != len(decoded)
                or item.get("sha256")
                != hashlib.sha256(decoded).hexdigest()):
            raise ValueError(
                f"isolated render resource identity mismatch: {logical}")
        if logical in expected:
            raise ValueError(
                f"duplicate isolated render resource: {logical}")
        expected[logical] = decoded
        observed_paths.append(logical)
    if observed_paths != sorted(observed_paths):
        raise ValueError(
            "isolated render resources are not canonically ordered")
    if entrypoint not in expected:
        raise ValueError(
            "isolated render entrypoint is absent from retained resources")
    try:
        width_pt = float(job["width_pt"])
        height_pt = float(job["height_pt"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(
            "isolated render worker has invalid paper dimensions") from exc
    if (not math.isfinite(width_pt) or width_pt <= 0
            or not math.isfinite(height_pt) or height_pt <= 0):
        raise ValueError(
            "isolated render worker paper dimensions must be positive")
    try:
        deadline_seconds = float(job["hard_deadline_seconds"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(
            "isolated render worker has an invalid hard deadline") from exc
    if (not math.isfinite(deadline_seconds)
            or deadline_seconds <= 0):
        raise ValueError(
            "isolated render worker hard deadline must be positive")
    return expected, entrypoint, width_pt, height_pt, deadline_seconds


def _run_render_worker() -> int:
    """Execute only inside the process-isolated Chromium worker."""
    response: dict[str, Any]
    return_code = 0
    try:
        validate_trusted_producer_sources()
        validate_base_runtime()
        expected, entrypoint, width_pt, height_pt, deadline_seconds = (
            _decode_render_worker_job(sys.stdin.buffer.read()))
        operation_timeout_ms = max(
            1000, math.ceil(deadline_seconds * 2000.0))
        pdf_payload, provenance, requests = (
            _render_snapshotted_tree_in_process(
                expected, entrypoint, width_pt, height_pt,
                operation_timeout_ms))
        validate_trusted_producer_sources()
        validate_base_runtime()
        response = {
            "schema": RENDER_WORKER_SCHEMA,
            "ok": True,
            "producer_sha256": hashlib.sha256(
                _AUDIT_SOURCE_PAYLOAD).hexdigest(),
            "pdf": {
                "bytes": len(pdf_payload),
                "sha256": hashlib.sha256(pdf_payload).hexdigest(),
                "payload_base64": base64.b64encode(
                    pdf_payload).decode("ascii"),
            },
            "provenance": provenance,
            "requests": requests,
        }
    except BaseException as exc:  # noqa: BLE001 - cross-process error packet
        return_code = 1
        response = {
            "schema": RENDER_WORKER_SCHEMA,
            "ok": False,
            "producer_sha256": hashlib.sha256(
                _AUDIT_SOURCE_PAYLOAD).hexdigest(),
            "error_type": type(exc).__name__,
            "error": str(exc),
        }
    sys.stdout.write(json.dumps(
        response, sort_keys=True, separators=(",", ":"),
        ensure_ascii=True))
    sys.stdout.flush()
    return return_code


def _kill_render_worker(process: subprocess.Popen[bytes]) -> None:
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGKILL)
        elif process.poll() is None:
            process.kill()
    except ProcessLookupError:
        pass
    try:
        process.communicate(timeout=RENDER_WORKER_KILL_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        try:
            process.kill()
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=RENDER_WORKER_KILL_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            # Never trade the authoritative render deadline for an unbounded
            # reap. The process group has already received SIGKILL.
            pass


def _render_snapshotted_tree(
        tree: MaterializedRenderTree,
        width_pt: float,
        height_pt: float,
        *,
        deadline_seconds: float = RENDER_HARD_TIMEOUT_SECONDS,
        ) -> tuple[bytes, dict[str, Any], dict[str, Any]]:
    """Render in a killable worker under one wall-clock hard deadline."""
    if (not math.isfinite(deadline_seconds)
            or deadline_seconds <= 0):
        raise ValueError("render hard deadline must be positive and finite")
    _validate_materialized_render_tree(
        tree, "before isolated Chromium worker")
    worker_job = _render_worker_job(
        tree, width_pt, height_pt, deadline_seconds)
    command = [
        sys.executable,
        "-E",
        "-B",
        str(_AUDIT_SOURCE_PATH),
        "--render-worker",
    ]
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    environment.pop("PYTHONHOME", None)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    process = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=environment,
        start_new_session=(os.name == "posix"),
    )
    try:
        try:
            stdout, _stderr = process.communicate(
                input=worker_job,
                timeout=deadline_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            _kill_render_worker(process)
            raise RenderDeadlineExceeded(deadline_seconds) from exc
    finally:
        _validate_materialized_render_tree(
            tree, "after isolated Chromium worker")
    try:
        response = json.loads(stdout.decode("ascii"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            "isolated Chromium worker returned no valid result") from exc
    if (not isinstance(response, dict)
            or response.get("schema") != RENDER_WORKER_SCHEMA
            or response.get("producer_sha256")
            != hashlib.sha256(_AUDIT_SOURCE_PAYLOAD).hexdigest()):
        raise RuntimeError(
            "isolated Chromium worker result has invalid provenance")
    if not response.get("ok"):
        error_type = str(response.get("error_type") or "RuntimeError")
        error = str(response.get("error") or "unknown render failure")
        raise RuntimeError(
            f"isolated Chromium worker failed: {error_type}: {error}")
    if process.returncode != 0:
        raise RuntimeError(
            "isolated Chromium worker exited unsuccessfully")
    pdf = response.get("pdf")
    provenance = response.get("provenance")
    requests = response.get("requests")
    if (not isinstance(pdf, dict)
            or not isinstance(provenance, dict)
            or not isinstance(requests, dict)):
        raise RuntimeError(
            "isolated Chromium worker result is incomplete")
    try:
        pdf_payload = base64.b64decode(
            pdf.get("payload_base64", ""), validate=True)
    except (binascii.Error, TypeError, ValueError) as exc:
        raise RuntimeError(
            "isolated Chromium worker PDF is not base64") from exc
    if (pdf.get("bytes") != len(pdf_payload)
            or pdf.get("sha256")
            != hashlib.sha256(pdf_payload).hexdigest()):
        raise RuntimeError(
            "isolated Chromium worker PDF identity mismatch")
    provenance = copy.deepcopy(provenance)
    provenance.update({
        "hard_deadline_seconds": deadline_seconds,
        "hard_deadline_enforced_by": (
            "isolated-render-worker-process-v1"),
        "deadline_cleanup_policy": (
            "kill-worker-and-chromium-process-group"),
    })
    return pdf_payload, provenance, copy.deepcopy(requests)


def _canonicalize_chromium_pdf(payload: bytes) -> tuple[bytes, dict[str, Any]]:
    """Replace only fixed-width volatile PDF metadata before retention."""
    replacement_date = b"D:19700101000000+00'00'"

    def replace(match: re.Match[bytes]) -> bytes:
        replacement = (
            b"/" + match.group("key") + b" (" + replacement_date + b")")
        if len(replacement) != len(match.group(0)):
            raise RuntimeError(
                "Chromium PDF date normalization would move xref offsets")
        return replacement

    canonical, count = CHROMIUM_PDF_DATE_RE.subn(replace, payload)
    if count != 2:
        raise RuntimeError(
            "Chromium PDF did not expose exactly CreationDate and ModDate "
            f"for deterministic normalization (found {count})")
    return canonical, {
        "algorithm": "fixed-width-creation-modification-date-v1",
        "fields_normalized": count,
        "replacement": replacement_date.decode("ascii"),
        "xref_offsets_preserved": len(canonical) == len(payload),
    }


def _canonical_candidate_ir_digest(candidate: dict[str, Any]) -> str:
    canonical = copy.deepcopy(candidate)
    canonical.pop("source", None)
    canonical.pop("generator", None)
    payload = json.dumps(
        canonical,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("ascii")
    return hashlib.sha256(payload).hexdigest()


def _extract_retained_candidate(
        payload: bytes,
        form_code: str,
        revision: str,
        *,
        extractor: Any | None = None,
        ) -> tuple[dict[str, Any], dict[str, Any]]:
    """Extract only a private rematerialization of retained candidate bytes."""
    extractor = extractor or extract.extract
    digest = hashlib.sha256(payload).hexdigest()
    with tempfile.TemporaryDirectory(
            prefix="formgen-candidate-extract-") as temporary:
        root = pathlib.Path(temporary)
        root.chmod(0o700)
        named_path = root / "candidate.pdf"
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        write_fd = os.open(named_path, flags, 0o400)
        try:
            view = memoryview(payload)
            written = 0
            while written < len(view):
                written += os.write(write_fd, view[written:])
            os.fsync(write_fd)
        finally:
            os.close(write_fd)
        read_flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            read_flags |= os.O_NOFOLLOW
        read_fd = os.open(named_path, read_flags)
        named_path.unlink()
        fd_path = pathlib.Path(f"/dev/fd/{read_fd}")
        if not fd_path.exists():
            os.close(read_fd)
            raise RuntimeError(
                "platform has no retained-descriptor path for extraction")

        def retained_fd_bytes() -> bytes:
            if not hasattr(os, "pread"):
                raise RuntimeError(
                    "platform cannot validate retained descriptor without "
                    "changing extractor-visible file position")
            chunks = []
            offset = 0
            while True:
                chunk = os.pread(read_fd, 1 << 20, offset)
                if not chunk:
                    break
                chunks.append(chunk)
                offset += len(chunk)
            return b"".join(chunks)

        extraction_error: BaseException | None = None
        candidate: dict[str, Any] | None = None
        try:
            if retained_fd_bytes() != payload:
                raise RuntimeError(
                    "retained candidate changed before extraction")
            try:
                candidate = extractor(
                    fd_path, form_code, revision, digest)
            except BaseException as exc:  # preserve SystemExit hash failures
                extraction_error = exc
            if retained_fd_bytes() != payload:
                raise RuntimeError(
                    "retained candidate changed during extraction")
        finally:
            os.close(read_fd)
        if extraction_error is not None:
            raise extraction_error
        if not isinstance(candidate, dict):
            raise RuntimeError("candidate extractor returned no IR object")
        candidate_source = candidate.get("source") or {}
        if (candidate_source.get("sha256") != digest
                or candidate_source.get("bytes") != len(payload)):
            raise RuntimeError(
                "candidate extractor did not publish retained PDF identity")
    return candidate, {
        "materialization": (
            "private-0700-o_excl-o_nofollow-fsynced-unlinked-read-fd"),
        "expected_sha256_passed_to_extractor": True,
        "validated_before_after_extraction": True,
        "candidate_ir_sha256": _canonical_candidate_ir_digest(candidate),
        "candidate_ir_digest_scope": "source-and-generator-removed",
    }


_ROUND_TRIP_TOTAL_KEYS = frozenset({
    "rules_missing", "rules_extra", "rules_thickness_violations",
    "text_missing", "text_extra", "text_mismatched",
    "images_missing", "images_extra", "images_placement_violations",
})
_ROUND_TRIP_PAPER_KEYS = frozenset({
    "reference", "candidate", "problems", "ok",
})


def _validated_verifier_report(
        report: Any,
        ) -> tuple[bool, str | None, dict[str, int]]:
    """Validate the exact report shape emitted by verify.diff_ir()."""
    if not isinstance(report, dict):
        raise RuntimeError("verifier returned no report object")
    paper = report.get("paper")
    if (not isinstance(paper, dict)
            or set(paper) != _ROUND_TRIP_PAPER_KEYS
            or type(paper.get("ok")) is not bool
            or not isinstance(paper.get("problems"), list)
            or any(not isinstance(item, str)
                   for item in paper.get("problems", []))):
        raise RuntimeError("verifier paper evidence is missing or malformed")
    for side in ("reference", "candidate"):
        value = paper.get(side)
        if (not isinstance(value, dict)
                or set(value) != {"width_pt", "height_pt", "page_count"}):
            raise RuntimeError(
                f"verifier paper {side} evidence is missing or malformed")
    paper_ok = paper["ok"]
    hard_failure = report.get("hard_failure")
    if (hard_failure is not None
            and (not isinstance(hard_failure, str) or not hard_failure)):
        raise RuntimeError("verifier hard-failure evidence is malformed")
    if (paper_ok and hard_failure is not None) or (
            not paper_ok and hard_failure != "paper mismatch"):
        raise RuntimeError("verifier paper/hard-failure relation is false")
    totals = report.get("totals")
    if (not isinstance(totals, dict)
            or set(totals) != _ROUND_TRIP_TOTAL_KEYS):
        raise RuntimeError("verifier totals are missing or unsupported")
    if any(type(value) is not int or value < 0
           for value in totals.values()):
        raise RuntimeError("verifier totals contain a nonnegative-int violation")
    return paper_ok, hard_failure, dict(totals)


def round_trip(bundle: Bundle, html_dir: pathlib.Path,
               work: pathlib.Path) -> dict[str, Any]:
    """Print with Chromium, re-extract, diff against the source IR."""
    reference, relocated = form_side(bundle.ir, bundle.plan)
    paper = reference["paper"]
    with materialized_form_snapshot(bundle) as tree:
        raw_pdf, runtime, requests = _render_snapshotted_tree(
            tree, paper["width_pt"], paper["height_pt"])
    retained_pdf, normalization = _canonicalize_chromium_pdf(raw_pdf)
    candidate, extraction_record = _extract_retained_candidate(
        retained_pdf,
        reference["form"]["code"],
        reference["form"]["revision"],
    )
    record: dict[str, Any] = {
        "guide_relocated": relocated,
        "roundtrip_runtime": runtime,
        "render_requests": requests,
        "candidate_pdf": {
            "bytes": len(retained_pdf),
            "sha256": hashlib.sha256(retained_pdf).hexdigest(),
            "retained_exact_bytes": True,
            "chromium_returned_in_memory": True,
            "normalization": normalization,
            **extraction_record,
        },
    }
    report = verify.diff_ir(reference, candidate, verify.Tolerances(),
                            roles=["structural"])
    paper_ok, hard_failure, totals = _validated_verifier_report(report)

    # Denominators come from the source IR, so a percentage always answers
    # "of what the official form contains, how much did we reproduce".
    rules_ref = sum(p["stats"]["rules_structural"] for p in reference["pages"])
    text_ref = sum(len(p["text_runs"]) for p in reference["pages"])
    rules_missing = totals["rules_missing"]
    text_missing = totals["text_missing"]

    # verify.py short-circuits on a paper mismatch and never walks the pages, so
    # every total comes back 0. Zero missing rules is indistinguishable from a
    # perfect form unless the record says which it is -- and reading the first
    # as the second is precisely the failure this project keeps paying for. The
    # gate treats `measured: false` as unevaluable, which counts as a failure.
    measured = hard_failure is None

    record.update({
        "measured": measured,
        "hard_failure": hard_failure,
        "paper_ok": paper_ok,
        "rules_ref": rules_ref,
        "rules_missing": totals["rules_missing"],
        "rules_extra": totals["rules_extra"],
        "rules_thickness_violations": totals["rules_thickness_violations"],
        "rules_pct": round(100.0 * (rules_ref - rules_missing) / rules_ref, 2) if rules_ref else None,
        "text_ref": text_ref,
        "text_missing": text_missing,
        "text_extra": totals["text_extra"],
        "text_pct": round(100.0 * (text_ref - text_missing) / text_ref, 2) if text_ref else None,
        "images_missing": totals["images_missing"],
        "images_placement_violations": totals["images_placement_violations"],
    })
    return record


def score(slug: str, ir_dir: pathlib.Path, html_dir: pathlib.Path,
          layout_dir: pathlib.Path, guide_dir: pathlib.Path | None,
          work: pathlib.Path, source_root: str,
          roundtrip: bool = True) -> dict:
    """One form's record: the eight assertions, then the round-trip score.

    The assertions run first and are kept whatever the round trip does. A
    Chromium failure must not also erase the checks that do not need Chromium --
    losing them is how the record would come to say nothing while looking
    complete.
    """
    record: dict = {
        "slug": slug,
        "status": "error",
        "error": None,
        "input_manifest": empty_input_manifest(),
        "provenance_validation": {
            "validated_before": False,
            "validated_after": False,
            "error": None,
        },
    }
    bundle = None
    try:
        validate_trusted_producer_sources()
        validate_base_runtime()
        record["provenance_validation"]["validated_before"] = True
        snapshot = snapshot_inputs(
            slug, ir_dir, html_dir, layout_dir, guide_dir, source_root)
        record["input_manifest"] = snapshot.manifest
        bundle = load_bundle(slug, ir_dir, html_dir, layout_dir, guide_dir,
                             source_root, input_snapshot=snapshot)
        record.update(evaluate_assertions(bundle))
    except Exception as exc:  # noqa: BLE001 - one bad form must not stop the sweep
        reason = f"{type(exc).__name__}: {exc}"
        record.update({key: False for key in ASSERTION_KEYS})
        record["assertions"] = {key: broken(reason) for key in ASSERTION_KEYS}
        record["assertions_held"] = 0
        record["error"] = reason

    try:
        if bundle is None:
            raise RuntimeError(record["error"] or "bundle not loaded")
        if not roundtrip:
            record["status"] = "ok"
            record["roundtrip"] = "skipped"
        else:
            record.update(round_trip(bundle, html_dir, work))
            record["status"] = "ok"
    except Exception as exc:  # noqa: BLE001
        record["error"] = f"{type(exc).__name__}: {exc}"
        record["trace"] = traceback.format_exc(limit=3)
        if isinstance(exc, RenderDeadlineExceeded):
            record.update(_render_deadline_evidence(exc))
    finally:
        if bundle is not None:
            bundle.close()
        try:
            validate_trusted_producer_sources()
            validate_base_runtime()
            record["provenance_validation"]["validated_after"] = True
        except Exception as exc:  # noqa: BLE001 - invalidates whole record
            reason = f"{type(exc).__name__}: {exc}"
            record["status"] = "error"
            record["error"] = reason
            record["provenance_validation"]["error"] = reason
    input_manifest = record.get("input_manifest") or {}
    producer = input_manifest.get("producer") or {}
    base_runtime = input_manifest.get("runtime") or {}
    application_closure = base_runtime.get("application_closure") or {}
    roundtrip_runtime = record.get("roundtrip_runtime") or {}
    validated_before_after = bool(
        record["provenance_validation"]["validated_before"]
        and record["provenance_validation"]["validated_after"])
    roundtrip_closure_published = bool(
        roundtrip_runtime.get("dependency_closure")
        and roundtrip_runtime.get("chromium"))
    # Two lists, because they answer two different questions.  `reasons` is
    # why this attestation is not complete and must be empty when it is;
    # `boundaries` is what the attestation deliberately never covered, is
    # never empty, and is republished on every record no matter how green it
    # is.  Collapsing them is how a permanent scope boundary would come to
    # read as a temporary defect, or a temporary defect as a boundary.
    reasons: list[str] = []
    if not input_manifest.get("inputs_complete"):
        reasons.append("one or more required inputs are missing")
    if not application_closure.get("complete"):
        reasons.append(
            "application runtime closure does not account for every loaded "
            "module: "
            + ", ".join(application_closure.get("unbound_modules") or ())
        )
    if not validated_before_after:
        reasons.append(
            "producer and runtime provenance were not revalidated before and "
            "after this record")
    if roundtrip and not roundtrip_closure_published:
        reasons.append(
            "round trip ran without publishing its Playwright/Chromium "
            "closure")
    boundaries = [
        producer.get(
            "incomplete_reason", "producer execution is not fully bound"),
        base_runtime.get(
            "incomplete_reason", "base runtime scope is incomplete"),
    ]
    if roundtrip:
        boundaries.append(roundtrip_runtime.get(
            "incomplete_reason", "roundtrip runtime scope is incomplete"))
    record["attestation"] = {
        "inputs_complete": bool(input_manifest.get("inputs_complete")),
        "producer_execution_bound": bool(
            producer.get("assertion_producer_bound")),
        "base_runtime_scope_complete": bool(
            base_runtime.get("scope_complete")),
        "roundtrip_runtime_scope_complete": (
            bool(roundtrip_runtime.get("scope_complete"))
            if roundtrip else None
        ),
        "application_closure_complete": bool(
            application_closure.get("complete")),
        "validated_before_after": validated_before_after,
        "complete": not reasons,
        "enforceable": not reasons,
        "incomplete_reasons": reasons,
        "declared_out_of_scope": boundaries,
        "future_gate_required": (
            "clean audit.py git-blob/bootstrap and native host/runtime binding"),
    }
    return record


def self_test() -> int:
    """Prove each assertion can fail, and that absence of evidence is failure.

    An assertion that cannot report a violation is decoration, so every one is
    fed a bundle it must reject. The fixtures are tiny by design: the corpus
    proves the assertions find real defects, this proves they would still find
    one if the corpus were clean.
    """
    failures: list[str] = []

    def check(name: str, condition: bool) -> None:
        if not condition:
            failures.append(name)

    verifier_totals = {
        key: 0 for key in sorted(_ROUND_TRIP_TOTAL_KEYS)
    }
    verifier_paper = {
        "reference": {"width_pt": 100.0, "height_pt": 100.0,
                      "page_count": 1},
        "candidate": {"width_pt": 100.0, "height_pt": 100.0,
                      "page_count": 1},
        "problems": [],
        "ok": True,
    }
    verifier_fixture = {
        "paper": verifier_paper,
        "totals": verifier_totals,
    }
    try:
        verifier_result = _validated_verifier_report(verifier_fixture)
    except Exception as error:  # noqa: BLE001 - the fixture must be complete
        verifier_result = None
        failures.append(
            "complete verifier report must validate: "
            f"{type(error).__name__}: {error}")
    check(
        "complete verifier report preserves paper and totals",
        verifier_result is not None
        and verifier_result[0] is True
        and verifier_result[1] is None
        and verifier_result[2] == verifier_totals,
    )
    verifier_malformed = [
        ("missing paper", lambda value: value.pop("paper")),
        ("bool paper verdict", lambda value: value["paper"].update({"ok": 1})),
        ("missing total", lambda value: value["totals"].pop("text_missing")),
        ("bool total", lambda value: value["totals"].update({"rules_missing": True})),
        ("paper failure without hard failure", lambda value: value["paper"].update({"ok": False})),
        ("paper success with hard failure", lambda value: value.update({"hard_failure": "paper mismatch"})),
    ]
    for label, mutator in verifier_malformed:
        malformed = copy.deepcopy(verifier_fixture)
        mutator(malformed)
        try:
            _validated_verifier_report(malformed)
        except RuntimeError:
            pass
        except Exception as error:  # noqa: BLE001 - malformed must not crash
            failures.append(
                f"malformed verifier {label} raised {type(error).__name__}: {error}")
        else:
            failures.append(f"malformed verifier {label} must fail closed")
    verifier_paper_failure = copy.deepcopy(verifier_fixture)
    verifier_paper_failure["paper"]["ok"] = False
    verifier_paper_failure["paper"]["problems"] = ["paper width mismatch"]
    verifier_paper_failure["hard_failure"] = "paper mismatch"
    try:
        paper_failure_result = _validated_verifier_report(verifier_paper_failure)
    except Exception as error:  # noqa: BLE001 - valid hard failure fixture
        paper_failure_result = None
        failures.append(
            "verifier paper mismatch must validate as measured=false: "
            f"{type(error).__name__}: {error}")
    check(
        "verifier paper mismatch retains complete zero totals",
        paper_failure_result is not None
        and paper_failure_result[0] is False
        and paper_failure_result[1] == "paper mismatch",
    )

    ir = {
        "form": {"code": "TEST", "revision": "0000"},
        "source": {"file": "external:none.pdf", "sha256": "0" * 64},
        "paper": {"width_pt": 100.0, "height_pt": 100.0},
        "pages": [{
            "index": 1, "width_pt": 100.0, "height_pt": 100.0, "rotation": 0,
            "rules": [{"id": "h0", "axis": "h", "x0": 0.0, "y0": 90.0, "x1": 50.0,
                       "y1": 90.24, "thickness_pt": 0.24, "role": "structural"}],
            "area_fills": [], "images": [],
            "text_runs": [{
                "text": "Rate?", "font": "Arial", "size_pt": 8.0, "color": 16777215,
                "x0": 10.0, "y0": 10.0, "x1": 30.0, "y1": 18.0, "origin_x": 10.0,
                "baseline_y": 16.0,
                "char_origin_offsets_pt": [0.0, 4.0, 8.0, 12.0, 16.0],
                "char_widths_pt": [4.0, 4.0, 4.0, 4.0, 4.0],
            }],
            "stats": {"rules_structural": 1},
        }],
    }
    html = (
        '<div class="page page-1" id="page-1" style="width:100pt;height:100pt">'
        '<svg class="rl"><rect x="0" y="90" width="50" height="0.24" '
        'fill="#000000" data-rule-id="h0"/></svg>'
        '<div class="layer-text"><div class="t" id="p1t0" style="left:10pt;top:10pt;'
        'color:#000000">Rate?</div></div>'
        '<div id="p1c0" class="c f" data-cell-kind="field" data-field-kind="text" '
        'style="left:8pt;top:8pt;width:30pt;height:12pt">'
        '<input type="text" class="fi" id="p1c0-i" name="p1c0" '
        'style="inset:0pt 0pt 0pt 0pt"></div>'
        '<div id="p1c1" class="c" data-cell-kind="mixed" data-comb-slots="2" '
        'style="left:50pt;top:50pt;width:20pt;height:10pt">'
        '<div class="s" data-slot="0" style="left:0pt;top:0pt;width:10pt;height:10pt">'
        '</div><div class="s" data-slot="1" style="left:10pt;top:0pt;width:10pt;'
        'height:10pt"></div></div>'
        '</div>')
    layout = {"pages": [{"index": 1, "cells": [
        {"id": "p1c0", "x0": 8.0, "y0": 8.0, "x1": 38.0, "y1": 20.0,
         "border": {"top": {}, "bottom": {}, "left": {}, "right": {}},
         "is_empty": False, "rectangular": True, "kind": "field", "text_run_ids": []},
        {"id": "p1c1", "x0": 50.0, "y0": 50.0, "x1": 70.0, "y1": 60.0,
         "border": {"top": {}, "bottom": {}, "left": {}, "right": {}},
         "is_empty": True, "rectangular": True, "kind": "mixed", "text_run_ids": [],
         "comb": {"cells": 2, "divider_x": [60.0], "slot_x": [50.0, 60.0, 70.0],
                  "y0": 56.0, "y1": 60.0}},
    ]}]}
    plan = {"inline": [{"page": 1, "cut_y_pt": 40.0, "rule_ids": [],
                        "text_run_indices": [0], "cell_ids": [],
                        "marker_pattern": "table-n", "marker": "Table 1"}]}
    guide_html = ('<section class="gl-page" data-page="1" data-flow="table">'
                  '<table class="gl-table"><tr><td></td><td>3%</td></tr></table>'
                  '</section>')

    b = Bundle(slug="test", ir=ir, layout=layout, plan=plan, form_html=html,
               guide_html=guide_html, pdf=None)
    results = evaluate_assertions(b)
    check("assertion evidence contains no wall-clock timing fields",
          '"seconds"' not in json.dumps(results, sort_keys=True))

    # 1: the input at 8..38 x 8..20 covers the glyphs of "Rate?" at 10..30.
    check("inputs_over_printed_text must fail on an input over glyph ink",
          results["inputs_over_printed_text"] is False)
    # 3: p1c1 prints two comb slots, neither carrying an input.
    check("money_boxes_have_inputs must fail on a comb with no inputs",
          results["money_boxes_have_inputs"] is False)
    # 4: h0 ends at y 90.24, the cut is at 40.
    check("rules_below_guide_cut must fail on a rule past the cut",
          results["rules_below_guide_cut"] is False)
    # 5: the run is white in the IR and black in the markup. It is also the
    # relocated run, so the guide-containment half must fire as well.
    check("run_colour_matches_ir must fail on a white run emitted black",
          results["run_colour_matches_ir"] is False)
    # 6: the guide's only row has an empty description and a 3% rate.
    check("reflow_rate_without_description must fail on a rate with no description",
          results["reflow_rate_without_description"] is False)
    # 2, 7, 8, 9 and 10 need the source PDF, which this fixture deliberately
    # lacks: unevaluable must read as failure, not as a pass.
    for key in ("comb_slots_match_printed", "image_transform_applied",
                "no_invented_codepoints", "inputs_span_no_printed_divider",
                "printed_box_peers_all_fillable"):
        check(f"{key} must fail when the source PDF cannot be resolved",
              results[key] is False and "not resolved" in results["assertions"][key]["reason"])
    check("every assertion must name offenders or a reason",
          all(results["assertions"][k]["reason"] or results["assertions"][k]["offenders"]
              for k in ASSERTION_KEYS if not results[k]))

    # A guide-cut clipped straddler is emitted at the straddler record's form
    # rect; the binding comparison must expect that rect, not the unclipped
    # layout rect -- and must still fail any other drift on the same cell.
    def clipped_straddler_fixture(y1: float) -> Any:
        return types.SimpleNamespace(
            layout={"pages": [{"index": 1, "cells": []}]},
            layout_pages={1: {"index": 1, "cells": [
                {"id": "p1c0", "x0": 0.0, "y0": 0.0, "x1": 10.0, "y1": 20.0},
            ]}},
            cells=[Cell(
                id="p1c0", page=1, classes="c f",
                attrs=(f' style="left:0pt;top:0pt;width:10pt;height:{y1}pt"'),
                rect=(0.0, 0.0, 10.0, y1), inner="")],
            relocated_cells=set(),
            form_html=None,
            regions=[{
                "page": 1, "cut_y_pt": 12.0,
                "straddlers": [{
                    "kind": "cell", "ref": "p1c0",
                    "disposition": "clipped",
                    "form": {"x0": 0.0, "y0": 0.0, "x1": 10.0, "y1": 12.0},
                    "guide": {"x0": 0.0, "y0": 12.0, "x1": 10.0, "y1": 20.0},
                }],
            }],
        )

    clipped_ok = emitted_cell_binding_issues(clipped_straddler_fixture(12.0))
    check(
        "a clipped straddler emitted at its guide-cut extent binds cleanly",
        clipped_ok == [],
    )
    unclipped_emission = emitted_cell_binding_issues(
        clipped_straddler_fixture(20.0))
    check(
        "a clipped straddler emitted at the unclipped layout rect still fails",
        len(unclipped_emission) == 1
        and unclipped_emission[0].get("clipped_by_guide_cut") is True
        and unclipped_emission[0].get("expected_rect")
        == [0.0, 0.0, 10.0, 12.0]
        and unclipped_emission[0].get("unclipped_layout_rect")
        == [0.0, 0.0, 10.0, 20.0]
        and "emitted-cell-geometry-mismatch"
        in unclipped_emission[0]["failure_kinds"],
    )
    unrecorded_clip = clipped_straddler_fixture(12.0)
    unrecorded_clip.regions[0]["straddlers"][0]["disposition"] = "relocated"
    unrecorded_short = emitted_cell_binding_issues(unrecorded_clip)
    check(
        "a short emission without a clipped straddler record still fails",
        len(unrecorded_short) == 1
        and unrecorded_short[0].get("clipped_by_guide_cut") is False
        and "emitted-cell-geometry-mismatch"
        in unrecorded_short[0]["failure_kinds"],
    )

    # The reflow assertion reads data-flow as a comma-separated list: a rate
    # table followed by prose on the same guide page is intact when its
    # gl-table rows pair codes and rates with descriptive text.
    paired_guide = (
        '<section class="gl-page" data-page="1" data-flow="table,prose">'
        '<table class="gl-table">'
        "<tr><td>PT 060</td><td>Franchises on electric utilities</td>"
        "<td>5%</td></tr>"
        "<tr><td></td><td>and gas utilities, two-line continuation</td>"
        "<td></td></tr>"
        "</table><p>Guidelines prose</p></section>")
    paired_bundle = Bundle(
        slug="test-reflow", ir=ir, layout=layout, plan=plan,
        form_html=html, guide_html=paired_guide, pdf=None)
    paired_result = check_reflow_rate_without_description(paired_bundle)
    check(
        "a table-plus-prose guide page with paired rate rows holds",
        paired_result["holds"] is True
        and paired_result["rows_checked"] == 2,
    )
    orphan_guide = paired_guide.replace(
        "<td>PT 060</td><td>Franchises on electric utilities</td>",
        "<td>PT 060</td><td></td>")
    orphan_bundle = Bundle(
        slug="test-reflow-orphan", ir=ir, layout=layout, plan=plan,
        form_html=html, guide_html=orphan_guide, pdf=None)
    orphan_result = check_reflow_rate_without_description(orphan_bundle)
    check(
        "a bare code-and-rate row with no description still fails",
        orphan_result["holds"] is False
        and any(offender.get("why") == "rate without description"
                for offender in orphan_result["offenders"]),
    )
    prose_guide = (
        '<section class="gl-page" data-page="1" data-flow="prose">'
        "<p>PT 060 Franchises 5% flattened prose</p></section>")
    prose_bundle = Bundle(
        slug="test-reflow-prose", ir=ir, layout=layout, plan=plan,
        form_html=html, guide_html=prose_guide, pdf=None)
    prose_result = check_reflow_rate_without_description(prose_bundle)
    check(
        "a rate table flattened into prose still fails",
        prose_result["holds"] is False
        and any("reflowed as prose" in str(offender.get("why"))
                for offender in prose_result["offenders"]),
    )
    tableless_guide = (
        '<section class="gl-page" data-page="1" data-flow="table">'
        "<p>no table markup at all</p></section>")
    tableless_bundle = Bundle(
        slug="test-reflow-tableless", ir=ir, layout=layout, plan=plan,
        form_html=html, guide_html=tableless_guide, pdf=None)
    tableless_result = check_reflow_rate_without_description(tableless_bundle)
    check(
        "a declared table flow with no table markup fails closed",
        tableless_result["holds"] is False
        and any("emits no table markup" in str(offender.get("why"))
                for offender in tableless_result["offenders"]),
    )

    # Every record binds the exact bytes it evaluated. Hashes are content-based
    # (not path- or timestamp-based), and a missing required guide plan prevents
    # the form from becoming an `ok` record.
    with tempfile.TemporaryDirectory(prefix="formgen-audit-inputs-") as tmp:
        root = pathlib.Path(tmp)
        ir_dir = root / "ir"
        html_dir = root / "html"
        layout_dir = root / "layout"
        guide_dir = root / "guides"
        source_dir = root / "sources"
        for directory in (
                ir_dir, html_dir, layout_dir, guide_dir, source_dir):
            directory.mkdir()

        slug = "test-bound"
        import fitz
        source_doc = fitz.open()
        source_doc.new_page(width=100.0, height=100.0)
        source_payload = source_doc.tobytes()
        source_doc.close()
        source_path = source_dir / f"{slug}.pdf"
        source_path.write_bytes(source_payload)
        bound_ir = copy.deepcopy(ir)
        bound_ir["source"] = {
            "file": f"external:{source_path.name}",
            "sha256": hashlib.sha256(source_payload).hexdigest(),
            "page_count": 1,
        }
        (ir_dir / f"{slug}.ir.json").write_text(
            json.dumps(bound_ir), encoding="utf-8")
        (layout_dir / f"{slug}.layout.json").write_text(
            json.dumps(layout), encoding="utf-8")
        (html_dir / "fonts").mkdir()
        (html_dir / "assets").mkdir()
        font_payload = b"retained test font bytes"
        image_payload = b"retained test image bytes"
        css_payload = (
            b'@font-face{font-family:"Bound";'
            b'src:url("../fonts/test.woff2")}')
        font_path = html_dir / "fonts" / "test.woff2"
        image_path = html_dir / "assets" / "test.png"
        css_path = html_dir / "assets" / "test.css"
        font_path.write_bytes(font_payload)
        image_path.write_bytes(image_payload)
        css_path.write_bytes(css_payload)
        bound_html = (
            '<link rel="stylesheet" href="assets/test.css">'
            '<img src="assets/test.png">'
            + html
        )
        html_path = html_dir / f"{slug}.html"
        html_path.write_text(bound_html, encoding="utf-8")
        guide_path = guide_dir / f"{slug}.guide.json"
        guide_path.write_text(json.dumps(plan), encoding="utf-8")
        (html_dir / f"{slug}.guide.html").write_text(
            guide_html, encoding="utf-8")

        first = snapshot_inputs(
            slug, ir_dir, html_dir, layout_dir, guide_dir, str(source_dir))
        first_closure = first.manifest["runtime"]["application_closure"]
        check("input manifest binds every required byte source",
              first.manifest["inputs_complete"] is True
              and first_closure["complete"] is True
              and first.manifest["complete"] is True
              and first.manifest["attestation_complete"] is True
              and first.manifest["enforceable"] is True
              and first.manifest["missing_required"] == []
              and set(first.manifest["inputs"]) == {
                  "ir", "layout", "html", "guide", "guide_html",
                  "source_pdf"}
              and all(first.manifest["inputs"][role]["present"]
                      and first.manifest["inputs"][role]["sha256"]
                      for role in REQUIRED_INPUT_ROLES))
        check("render manifest retains every fetched font and image byte",
              first.render_assets == {
                  "assets/test.css": css_payload,
                  "assets/test.png": image_payload,
                  "fonts/test.woff2": font_payload,
              }
              and [
                  item["path"]
                  for item in first.manifest["render"]["dependencies"]
              ] == [
                  "assets/test.css",
                  "assets/test.png",
                  "fonts/test.woff2",
              ]
              and first.manifest["render"]["complete"] is True)
        check("source PDF manifest binds logical path and exact immutable bytes",
              first.manifest["inputs"]["source_pdf"] == {
                  "file": source_path.name,
                  "logical_identity": f"external:{source_path.name}",
                  "path": source_path.name,
                  "required": True,
                  "present": True,
                  "bytes": len(source_payload),
                  "sha256": hashlib.sha256(source_payload).hexdigest(),
                  "expected_sha256": hashlib.sha256(source_payload).hexdigest(),
              }
              and first.contents["source_pdf"] == source_payload)
        check("input manifest binds the exact audit producer bytes",
              first.manifest["producer"] == producer_fingerprint()
              and first.manifest["producer"]["dependency_execution_bound"]
              is True
              and first.manifest["producer"]["audit_execution_bound"]
              is False
              and first.manifest["producer"]["assertion_producer_bound"]
              is False)
        dependency_probe = root / "dependency-probe.py"
        dependency_probe.write_bytes(b"first dependency payload")
        dependency_before = file_fingerprint(
            dependency_probe, "dependency-probe.py")
        dependency_probe.write_bytes(b"stale dependency payload")
        dependency_after = file_fingerprint(
            dependency_probe, "dependency-probe.py")
        check("dependency fingerprint detects stale producer bytes",
              dependency_before["sha256"] != dependency_after["sha256"])
        check("input manifest publishes deterministic runtime provenance",
              first.manifest["runtime"] == runtime_provenance()
              and first.manifest["runtime"]["python"]["implementation"]
              and first.manifest["runtime"]["python"]["version"]
              and first.manifest["runtime"]["pymupdf"]["package_version"]
              and first.manifest["runtime"]["pymupdf"]["version_bind"])
        check("application closure names each package tree independently",
              [item["logical_root"] for item in first_closure["packages"]]
              == list(APPLICATION_PACKAGE_NAMES)
              and all(re.fullmatch(r"[0-9a-f]{64}", item["tree_sha256"])
                      and item["files"] > 0 and item["bytes"] > 0
                      and item["algorithm"] == TREE_CLOSURE_ALGORITHM
                      for item in first_closure["packages"])
              and first_closure["bytecode_caches_excluded"] is True
              and first_closure["unbound_modules"] == [])
        check("application closure hashes the bundled native libraries",
              first_closure["native_libraries"] != []
              and all(
                  item["file"].endswith(NATIVE_LIBRARY_SUFFIXES)
                  and item["bytes"] > 0
                  and re.fullmatch(r"[0-9a-f]{64}", item["sha256"])
                  for item in first_closure["native_libraries"]))
        check("every loaded application module is bound inside a package tree",
              {item["module"] for item in first_closure["modules"]}
              == {logical[len("module/"):]
                  for logical, _path, _size, _sha in _base_runtime_snapshot()
                  if logical.startswith("module/")}
              and all(
                  item["file"].split("/", 1)[0] in APPLICATION_PACKAGE_NAMES
                  for item in first_closure["modules"]))
        # The completeness claim has to be able to collapse, or publishing it
        # would be decoration. A module loaded from outside every resolved
        # package tree is exactly the case the tree cannot account for.
        real_base_runtime_snapshot = _base_runtime_snapshot
        globals()["_base_runtime_snapshot"] = lambda: (
            *real_base_runtime_snapshot(),
            ("module/pymupdf.injected",
             pathlib.Path("/nonexistent/injected/pymupdf.py"), 7, "0" * 64),
        )
        try:
            injected_closure = _application_closure_manifest()
        finally:
            globals()["_base_runtime_snapshot"] = real_base_runtime_snapshot
        check("an out-of-tree application module withdraws the closure claim",
              injected_closure["complete"] is False
              and injected_closure["unbound_modules"]
              == ["module/pymupdf.injected"]
              and _application_closure_manifest()["complete"] is True)

        snapshotted_bundle = load_bundle(
            slug, ir_dir, html_dir, layout_dir, guide_dir, str(source_dir),
            input_snapshot=first)
        source_path.write_bytes(b"mutated after source snapshot")
        check("PDF assertions open the snapshotted bytes after path mutation",
              isinstance(snapshotted_bundle.pdf, bytes)
              and snapshotted_bundle.pdf == source_payload
              and snapshotted_bundle.doc.page_count == 1)
        snapshotted_bundle.close()
        stale_source = snapshot_inputs(
            slug, ir_dir, html_dir, layout_dir, guide_dir, str(source_dir))
        check("a newly observed stale source path fails hash resolution",
              stale_source.manifest["complete"] is False
              and stale_source.manifest["missing_required"] == ["source_pdf"]
              and stale_source.contents["source_pdf"] is None)
        source_path.write_bytes(source_payload)

        changed_html = bound_html.replace("Rate?", "Rote?", 1)
        html_path.write_text(changed_html, encoding="utf-8")
        second = snapshot_inputs(
            slug, ir_dir, html_dir, layout_dir, guide_dir, str(source_dir))
        check("input hash changes when exact bytes change",
              first.manifest["inputs"]["html"]["sha256"]
              != second.manifest["inputs"]["html"]["sha256"]
              and second.manifest["inputs"]["html"]["sha256"]
              == hashlib.sha256(changed_html.encode("utf-8")).hexdigest()
              and all(first.manifest["inputs"][role]["sha256"]
                      == second.manifest["inputs"][role]["sha256"]
                      for role in ("ir", "layout", "guide", "source_pdf"))
              and first.manifest["producer"] == second.manifest["producer"])

        html_snapshot_bundle = load_bundle(
            slug, ir_dir, html_dir, layout_dir, guide_dir, str(source_dir),
            input_snapshot=second)
        html_path.write_text("mutated after HTML snapshot", encoding="utf-8")
        font_path.write_bytes(b"mutated after dependency snapshot")
        with materialized_form_snapshot(
                html_snapshot_bundle, html_dir) as materialized:
            check("round trip prints snapshotted HTML after source path mutation",
                  materialized.entrypoint.read_bytes()
                  == second.contents["html"]
                  and materialized.entrypoint.read_bytes()
                  != html_path.read_bytes()
                  and (
                      materialized.root / "fonts" / "test.woff2"
                  ).read_bytes() == font_payload
                  and (
                      materialized.root / "fonts" / "test.woff2"
                  ).read_bytes() != font_path.read_bytes())
        render_mutation_failed = False
        try:
            with materialized_form_snapshot(
                    html_snapshot_bundle, html_dir) as materialized:
                materialized.entrypoint.chmod(0o600)
                materialized.entrypoint.write_bytes(
                    b"mutated isolated render tree")
        except RuntimeError as exc:
            render_mutation_failed = (
                "isolated render dependency bytes changed" in str(exc))
        check("isolated render tree mutation fails its after-use validation",
              render_mutation_failed)
        html_snapshot_bundle.close()
        html_path.write_text(changed_html, encoding="utf-8")
        font_path.write_bytes(font_payload)

        bound = score(slug, ir_dir, html_dir, layout_dir, guide_dir,
                      root / "work", str(source_dir), roundtrip=False)
        check("successful record publishes the exact input manifest",
              bound["status"] == "ok"
              and bound["input_manifest"] == second.manifest)

        guide_path.unlink()
        missing = score(slug, ir_dir, html_dir, layout_dir, guide_dir,
                        root / "work", str(source_dir), roundtrip=False)
        check("missing required guide input fails closed",
              missing["status"] == "error"
              and missing["input_manifest"]["complete"] is False
              and missing["input_manifest"]["missing_required"] == ["guide"]
              and all(missing.get(key) is False for key in ASSERTION_KEYS))

    with tempfile.TemporaryDirectory(
            prefix="formgen-audit-adversarial-") as tmp:
        adversarial_root = pathlib.Path(tmp)
        (adversarial_root / "present.png").write_bytes(b"present")
        _payloads, _entries, remote_errors = discover_render_dependencies(
            b'<img src="https://example.invalid/tracker.png">',
            "form.html",
            adversarial_root,
        )
        _payloads, _entries, missing_errors = discover_render_dependencies(
            b'<script src="missing.js"></script>',
            "form.html",
            adversarial_root,
        )
        _payloads, _entries, query_errors = discover_render_dependencies(
            b'<img src="present.png?v=mutable">',
            "form.html",
            adversarial_root,
        )
        symlink = adversarial_root / "linked.png"
        symlink.symlink_to(adversarial_root / "present.png")
        _payloads, _entries, symlink_errors = discover_render_dependencies(
            b'<img src="linked.png">',
            "form.html",
            adversarial_root,
        )
        check("remote render dependencies fail closed before Chromium",
              any("external or absolute" in item
                  for item in remote_errors))
        check("missing render dependencies fail closed before Chromium",
              any("unresolved render dependency" in item
                  for item in missing_errors))
        check("query-bearing render dependencies fail closed as ambiguous",
              any("query-bearing" in item for item in query_errors))
        check("symlinked render dependencies fail closed",
              any("symlinked dependency" in item
                  for item in symlink_errors))
        retained_policy = {"form.html": b"<html></html>"}
        policy_path, policy_payload = _retained_request_payload(
            f"{SYNTHETIC_RENDER_ORIGIN}/form.html",
            "GET",
            retained_policy,
        )
        check("synthetic-origin GET resolves only retained bytes",
              policy_path == "form.html"
              and policy_payload == retained_policy["form.html"])
        for label, url, method, reason in (
                ("remote", "https://example.invalid/form.html", "GET",
                 "outside the synthetic"),
                ("unknown", f"{SYNTHETIC_RENDER_ORIGIN}/missing.js", "GET",
                 "absent from retained"),
                ("write method", f"{SYNTHETIC_RENDER_ORIGIN}/form.html",
                 "POST", "only GET")):
            try:
                _retained_request_payload(
                    url, method, retained_policy)
            except ValueError as exc:
                request_failed = reason in str(exc)
            else:
                request_failed = False
            check(f"{label} browser request fails retained-byte policy",
                  request_failed)

        browser_fixture_prefix = (
            "<!doctype html><meta charset=\"utf-8\">"
            "<style>@page{size:72pt 72pt;margin:0}"
            "html,body{margin:0;width:72pt;height:72pt}</style>"
        )
        # Two budgets, because the fixtures below assert opposite things. The
        # hang fixtures must exceed their deadline, so theirs stays tight. The
        # control must finish inside its own, and it was sharing the tight one.
        #
        # That mattered because _bound_playwright_runtime SHA-256s the entire
        # ~873 MiB Playwright tree before and after every render -- roughly
        # 1.75 GiB of hashing inside the budget. Warm, it is CPU-bound off the
        # page cache and fits; cold, it is I/O-bound and does not. So on a cold
        # runner the control died with an uncaught RenderDeadlineExceeded while
        # the three hang fixtures passed VACUOUSLY: the closure hashing alone
        # exhausted the deadline they exist to prove is enforced. Three real
        # assertions quietly became no-ops, which is worse than the red one.
        hang_deadline = 8.0
        control_deadline = 60.0

        # Prime the page cache once, outside any timed section, so the first
        # render is not paying for the whole tree's first read.
        _snapshot_tree(_playwright_package_root())

        def run_browser_fixture(
                label: str,
                body: str,
                deadline: float = hang_deadline,
                ) -> tuple[bytes, dict[str, Any], dict[str, Any]]:
            fixture_root = adversarial_root / f"browser-{label}"
            fixture_root.mkdir()
            entrypoint = fixture_root / "form.html"
            payload = (browser_fixture_prefix + body).encode("utf-8")
            entrypoint.write_bytes(payload)
            entrypoint.chmod(0o400)
            tree = MaterializedRenderTree(
                root=fixture_root,
                entrypoint=entrypoint,
                expected={"form.html": payload},
            )
            return _render_snapshotted_tree(
                tree, 72.0, 72.0,
                deadline_seconds=deadline)

        # A deadline the control blows is a finding, not a traceback: it must
        # land in the failure count like every other assertion, or a cold runner
        # reports a crash where it should report a result.
        try:
            control_pdf, control_runtime, control_requests = (
                run_browser_fixture(
                    "control", "<body>bounded control</body>",
                    deadline=control_deadline))
        except RenderDeadlineExceeded as exc:
            check(f"actual-browser control completes inside its hard deadline "
                  f"({exc})", False)
            control_pdf, control_runtime, control_requests = b"", {}, {}
        check("actual-browser control completes inside its hard deadline",
              control_pdf.startswith(b"%PDF-")
              and control_runtime["hard_deadline_seconds"]
              == control_deadline
              and control_runtime["hard_deadline_enforced_by"]
              == "isolated-render-worker-process-v1"
              and control_requests["fulfilled"] == ["form.html"]
              and control_requests["blocked_requests"] == 0)

        try:
            run_browser_fixture(
                "websocket",
                "<script>try{new WebSocket("
                "\"wss://example.invalid/socket\")}catch(error){}</script>"
                "<body>websocket probe</body>",
            )
        except RuntimeError as exc:
            websocket_rejected = (
                not isinstance(exc, RenderDeadlineExceeded)
                and "websockets" in str(exc)
                and "wss://example.invalid/socket" in str(exc)
            )
        else:
            websocket_rejected = False
        check("actual-browser WebSocket is rejected without callback deadlock",
              websocket_rejected)

        def expect_browser_deadline(label: str, body: str) -> None:
            try:
                run_browser_fixture(label, body)
            except RenderDeadlineExceeded as exc:
                deadline_held = (
                    exc.deadline_seconds == hang_deadline
                    and "deterministic hard deadline" in str(exc))
            else:
                deadline_held = False
            check(
                f"actual-browser {label} cannot outlive render deadline",
                deadline_held,
            )

        expect_browser_deadline(
            "never-font",
            "<script>Object.defineProperty(document,'fonts',{"
            "configurable:true,value:{ready:new Promise(()=>{})}})</script>"
            "<body>font promise probe</body>",
        )
        expect_browser_deadline(
            "never-fetch",
            "<script>"
            "let fetchSettled=false;"
            "Object.defineProperty(globalThis,'fetch',{"
            "value:()=>new Promise(()=>{})});"
            "fetch('https://formgen.invalid/never').finally("
            "()=>{fetchSettled=true});"
            "while(!fetchSettled){}"
            "</script><body>fetch promise probe</body>",
        )
        expect_browser_deadline(
            "never-script",
            "<script>while(true){}</script>"
            "<body>blocked page script probe</body>",
        )
        deadline_evidence = _render_deadline_evidence(
            RenderDeadlineExceeded(hang_deadline))
        check("render deadline publishes explicit unevaluable evidence",
              deadline_evidence == {
                  "measured": False,
                  "hard_failure": "render-hard-deadline-exceeded",
                  "roundtrip_liveness": {
                      "status": "unevaluable",
                      "hard_failure": "render-hard-deadline-exceeded",
                      "hard_deadline_seconds": hang_deadline,
                      "cleanup_policy": (
                          "kill-worker-and-chromium-process-group"),
                  },
              })

        probe_extract = adversarial_root / "extract.py"
        probe_verify = adversarial_root / "verify.py"
        probe_extract.write_text("VALUE = 'retained'\n", encoding="utf-8")
        probe_verify.write_text(
            "import extract\nVALUE = extract.VALUE\n",
            encoding="utf-8",
        )
        decoy = types.ModuleType("extract")
        decoy.VALUE = "substituted"
        prior_extract = sys.modules.get("extract")
        sys.modules["extract"] = decoy
        try:
            loaded_extract, loaded_verify = _load_trusted_formgen_modules(
                probe_extract, probe_verify)
            binding_restored = sys.modules.get("extract") is decoy
        finally:
            if prior_extract is None:
                sys.modules.pop("extract", None)
            else:
                sys.modules["extract"] = prior_extract
        probe_extract.write_text("VALUE = 'mutated'\n", encoding="utf-8")
        check("trusted loader ignores a preseeded sys.modules substitution",
              loaded_extract.module.VALUE == "retained"
              and loaded_verify.module.VALUE == "retained"
              and loaded_verify.module.extract is loaded_extract.module
              and binding_restored)
        check("trusted loader fingerprints retained executed source bytes",
              loaded_extract.sha256
              == hashlib.sha256(loaded_extract.payload).hexdigest()
              and loaded_extract.module.VALUE == "retained"
              and _stable_read(probe_extract) != loaded_extract.payload)

        prior_extract = sys.modules.get("extract")
        sys.modules["extract"] = decoy
        try:
            try:
                validate_trusted_producer_sources()
            except RuntimeError as exc:
                substituted_binding_failed = (
                    "module binding was substituted" in str(exc))
            else:
                substituted_binding_failed = False
        finally:
            if prior_extract is None:
                sys.modules.pop("extract", None)
            else:
                sys.modules["extract"] = prior_extract
        check("post-load producer module substitution fails validation",
              substituted_binding_failed)

        runtime_tree = adversarial_root / "runtime"
        runtime_tree.mkdir()
        runtime_member = runtime_tree / "driver"
        runtime_member.write_bytes(b"retained runtime")
        runtime_closure = _snapshot_tree(runtime_tree)
        runtime_member.write_bytes(b"mutated runtime")
        try:
            _validate_tree_closure(runtime_closure, "after adversary")
        except RuntimeError as exc:
            runtime_mutation_failed = (
                "runtime dependency closure changed" in str(exc))
        else:
            runtime_mutation_failed = False
        check("runtime dependency mutation fails closure validation",
              runtime_mutation_failed)

        bound_playwright = types.ModuleType("playwright")
        expected_playwright = {"playwright": id(bound_playwright)}
        _validate_playwright_module_bindings(
            {"playwright": bound_playwright}, expected_playwright)
        for label, loaded, expected, reason in (
                (
                    "preloaded",
                    {"playwright": bound_playwright},
                    None,
                    "imported before",
                ),
                (
                    "expanded",
                    {
                        "playwright": bound_playwright,
                        "playwright.injected": types.ModuleType(
                            "playwright.injected"),
                    },
                    expected_playwright,
                    "module set changed",
                ),
                (
                    "substituted",
                    {"playwright": types.ModuleType("playwright")},
                    expected_playwright,
                    "was substituted",
                ),
        ):
            try:
                _validate_playwright_module_bindings(loaded, expected)
            except RuntimeError as exc:
                playwright_binding_failed = reason in str(exc)
            else:
                playwright_binding_failed = False
            check(
                f"{label} Playwright module binding fails closed",
                playwright_binding_failed,
            )

    synthetic_pdf = (
        b"%PDF-1.7\n"
        b"/CreationDate (D:20260731123456+00'00')\n"
        b"/ModDate (D:20260731123456+00'00')\n"
        b"%%EOF\n"
    )
    canonical_pdf, canonicalization = _canonicalize_chromium_pdf(
        synthetic_pdf)
    check("Chromium PDF volatile dates normalize without moving offsets",
          len(canonical_pdf) == len(synthetic_pdf)
          and b"20260731123456" not in canonical_pdf
          and canonicalization["fields_normalized"] == 2
          and canonicalization["xref_offsets_preserved"] is True)

    def retained_candidate_probe(
            path: pathlib.Path, code: str, revision: str,
            expected_sha: str,
            ) -> dict[str, Any]:
        payload = path.read_bytes()
        if hashlib.sha256(payload).hexdigest() != expected_sha:
            raise AssertionError("candidate hash was not passed to extractor")
        return {
            "source": {
                "file": f"external:{path.name}",
                "sha256": expected_sha,
                "bytes": len(payload),
            },
            "generator": {"producer": "probe"},
            "form": {"code": code, "revision": revision},
            "pages": [],
        }

    _candidate, candidate_record = _extract_retained_candidate(
        canonical_pdf, "TEST", "0000",
        extractor=retained_candidate_probe)
    check("candidate extraction binds retained bytes and canonical IR",
          candidate_record["expected_sha256_passed_to_extractor"] is True
          and candidate_record["validated_before_after_extraction"] is True
          and len(candidate_record["candidate_ir_sha256"]) == 64)

    mutation_attempt = {"prevented": False}

    def mutating_candidate_probe(
            path: pathlib.Path, code: str, revision: str,
            expected_sha: str,
            ) -> dict[str, Any]:
        try:
            path.chmod(0o600)
            path.write_bytes(b"mutated during extraction")
        except PermissionError:
            mutation_attempt["prevented"] = True
        return {
            "source": {
                "file": f"external:{path.name}",
                "sha256": expected_sha,
                "bytes": len(canonical_pdf),
            },
            "generator": {"producer": "probe"},
            "form": {"code": code, "revision": revision},
            "pages": [],
        }

    # The invariant is "a candidate mutated during extraction is never
    # silently used" -- and the two platforms enforce it differently. On
    # macOS a write through the unlinked read-only descriptor path raises
    # PermissionError: prevention. On Linux, /proc/self/fd re-opens the
    # inode, the write LANDS, and the before/after digest guard raises
    # instead: detection. Demanding prevention specifically made the first
    # real Linux run die on a guard doing exactly its job. Either outcome
    # satisfies the invariant; a mutation that lands undetected fails.
    mutation_detected = False
    try:
        _extract_retained_candidate(
            canonical_pdf, "TEST", "0000",
            extractor=mutating_candidate_probe)
    except RuntimeError as exc:
        mutation_detected = "changed during extraction" in str(exc)
    check("a candidate mutated during extraction is prevented or detected",
          mutation_attempt["prevented"] or mutation_detected)

    # The clean side: the same page with the input moved off the ink, the comb
    # filled, the cut below everything, the colour right and the row complete.
    clean_html = html.replace('style="left:8pt;top:8pt;width:30pt;height:12pt"',
                              'style="left:40pt;top:30pt;width:30pt;height:12pt"')
    clean_html = clean_html.replace('color:#000000', 'color:#ffffff')
    clean_html = clean_html.replace(
        '<div class="s" data-slot="0" style="left:0pt;top:0pt;width:10pt;height:10pt">'
        '</div>',
        '<div class="s" data-slot="0" style="left:0pt;top:0pt;width:10pt;height:10pt">'
        '<input type="text" class="fi fc" data-slot-index="0"></div>')
    clean_html = clean_html.replace(
        '<div class="s" data-slot="1" style="left:10pt;top:0pt;width:10pt;height:10pt">'
        '</div>',
        '<div class="s" data-slot="1" style="left:10pt;top:0pt;width:10pt;height:10pt">'
        '<input type="text" class="fi fc" data-slot-index="1"></div>')
    clean_layout = copy.deepcopy(layout)
    clean_layout["pages"][0]["cells"][0]["x0"] = 40.0
    clean_layout["pages"][0]["cells"][0]["x1"] = 70.0
    clean_layout["pages"][0]["cells"][0]["y0"] = 30.0
    clean_layout["pages"][0]["cells"][0]["y1"] = 42.0
    clean_plan = copy.deepcopy(plan)
    clean_plan["inline"][0]["cut_y_pt"] = 95.0
    clean_guide = guide_html.replace("<td></td>", "<td>Franchise tax</td>")
    clean_guide += '<span style="color:#ffffff">initials</span>'
    clean = Bundle(slug="test", ir=ir, layout=clean_layout, plan=clean_plan,
                   form_html=clean_html, guide_html=clean_guide, pdf=None)
    ok = evaluate_assertions(clean)
    for key in ("inputs_over_printed_text", "money_boxes_have_inputs",
                "rules_below_guide_cut", "run_colour_matches_ir",
                "reflow_rate_without_description"):
        check(f"{key} must hold on the corrected fixture: "
              f"{ok['assertions'][key]['reason']}", ok[key] is True)

    # A run assigned to the same lattice cell is still printed ink. Ownership
    # explains the collision; it does not make a live input over that ink safe.
    owned_layout = copy.deepcopy(layout)
    owned_layout["pages"][0]["cells"][0]["text_run_ids"] = ["p1t0"]
    owned = Bundle(slug="owned", ir=ir, layout=owned_layout, plan=None,
                   form_html=html, guide_html=None, pdf=None)
    check("an input over its own cell's printed run still fails",
          check_inputs_over_printed_text(owned)["holds"] is False)

    # The ink band, end to end. One caption set 10pt on a baseline at y=60 in
    # a face whose ascender is 0.9 and descender -0.2, so its LINE box runs
    # 51..62 while its capitals stop inking at 60. An input whose top edge is
    # at 61 sits in the 1pt of blank paper between the two, which is the shape
    # of 15 of the 147 offenders this assertion used to publish.
    def ink_band_bundle(text: str, input_top: float,
                        **run_over: Any) -> Bundle:
        run = {
            "text": text, "font": "Arial", "family": "Arial", "size_pt": 10.0,
            "color": 0, "x0": 10.0, "y0": 51.0,
            "x1": 10.0 + 3.0 * len(text), "y1": 62.0,
            "origin_x": 10.0, "baseline_y": 60.0,
            "ascender": 0.9, "descender": -0.2, "rotated": False,
            "bold": False, "italic": False,
            "char_origin_offsets_pt": [3.0 * i for i in range(len(text))],
            "char_widths_pt": [3.0] * len(text),
        }
        run.update(run_over)
        ink_ir = {
            "form": {"code": "INK", "revision": "0000"},
            "source": {"file": "external:none.pdf", "sha256": "0" * 64},
            "paper": {"width_pt": 100.0, "height_pt": 100.0},
            "pages": [{
                "index": 1, "width_pt": 100.0, "height_pt": 100.0,
                "rotation": 0, "rules": [], "area_fills": [], "images": [],
                "text_runs": [run], "stats": {},
            }],
        }
        ink_html = (
            '<div class="page page-1" id="page-1" '
            'style="width:100pt;height:100pt">'
            '<div class="layer-text"><div class="t" id="p1t0" '
            'style="left:10pt;top:51pt;color:#000000">'
            + text.replace("&", "&amp;").replace("<", "&lt;") +
            '</div></div>'
            '<div id="p1c0" class="c f" data-cell-kind="field" '
            'data-field-kind="text" '
            f'style="left:8pt;top:{input_top}pt;width:30pt;height:8pt">'
            '<input type="text" class="fi" id="p1c0-i" name="p1c0" '
            'style="inset:0pt 0pt 0pt 0pt"></div></div>')
        return Bundle(slug="ink", ir=ink_ir, layout=None, plan=None,
                      form_html=ink_html, guide_html=None, pdf=None)

    def ink_holds(text: str, input_top: float, **run_over: Any) -> bool:
        return check_inputs_over_printed_text(
            ink_band_bundle(text, input_top, **run_over))["holds"]

    check("an input in the blank band below capitals is not a collision",
          ink_holds("DN", 61.0) is True)
    check("an input on the cap ink of the same caption is a collision",
          ink_holds("DN", 55.0) is False)
    check("a descender in the caption reaches the same blank band",
          ink_holds("Dg", 61.0) is False)
    check("a descender is caught through its own glyph, not its neighbours'",
          ink_holds("g", 61.0) is False and ink_holds("D", 61.0) is True)
    check("an unmeasured character is never assumed to clear an input",
          ink_holds("\uf0a7", 61.0) is False)
    check("a symbol-encoded face is never read through the character table",
          ink_holds("DN", 61.0, family="Wingdings") is False)
    check("a rotated run is never seated on a horizontal baseline",
          ink_holds("DN", 61.0, rotated=True) is False)
    check("an italic f still reaches below the baseline",
          ink_holds("f", 61.0) is True
          and ink_holds("f", 61.0, italic=True) is False)
    # The overshoot allowance is load-bearing: round letters ink below the
    # baseline, and an input butted straight onto it is still a collision.
    check("a round letter's baseline overshoot is still ink",
          ink_holds("o", 60.1) is False)

    # Mutation test. Each entry weakens the ink band in one way; the fixture
    # named beside it must stop failing, which is what proves the fixture --
    # and therefore the rule -- is doing the work. A mutation that changes
    # nothing means the check above is decoration.
    _measured_ink_bottom = glyph_ink_bottom

    def _ink_bottom_without_rotation_guard(
            run: dict, char: str) -> float | None:
        return _measured_ink_bottom({**run, "rotated": False}, char)

    ink_mutations = (
        ("descenders read as baseline-seated",
         {"DESCENDING_INK": frozenset(),
          "BASELINE_SEATED_INK": BASELINE_SEATED_INK | DESCENDING_INK},
         lambda: ink_holds("Dg", 61.0)),
        ("unmeasured characters read as baseline-seated",
         {"BASELINE_SEATED_INK": BASELINE_SEATED_INK | {"\uf0a7"}},
         lambda: ink_holds("\uf0a7", 61.0)),
        ("symbol-encoded faces read through the character table",
         {"SYMBOL_ENCODED_INK_FAMILIES": frozenset()},
         lambda: ink_holds("DN", 61.0, family="Wingdings")),
        ("rotated runs seated on a horizontal baseline",
         {"glyph_ink_bottom": _ink_bottom_without_rotation_guard},
         lambda: ink_holds("DN", 61.0, rotated=True)),
        ("baseline overshoot removed",
         {"GLYPH_BASELINE_OVERSHOOT_EM": 0.0},
         lambda: ink_holds("o", 60.1)),
        ("italic f read as upright",
         {"ITALIC_ONLY_DESCENDING_INK": frozenset()},
         lambda: ink_holds("f", 61.0, italic=True)),
    )
    module = globals()
    for label, patches, probe in ink_mutations:
        restore = {name: module[name] for name in patches}
        module.update(patches)
        try:
            weakened = probe()
        finally:
            module.update(restore)
        check(f"weakening the ink band ({label}) is caught by the suite",
              weakened is True)
    check("the ink band is restored after the mutation sweep",
          ink_holds("Dg", 61.0) is False
          and ink_holds("DN", 61.0) is True)

    # The live page's last cell is immediately followed by inert band template
    # markup. An input inside that template must not be attributed to the cell.
    template_html = (
        '<div class="page page-1" id="page-1" style="width:100pt;height:100pt">'
        '<div class="t" id="p1t0" style="left:10pt;top:10pt">Rate?</div>'
        '<div id="p1c9" class="c" data-cell-kind="label" '
        'style="left:8pt;top:8pt;width:30pt;height:12pt"></div></div>'
        '<template id="band-template-p1g0"><input type="text" class="fi" '
        'style="inset:0pt 0pt 0pt 0pt"></template><script></script>')
    template_cells = parse_cells(template_html)
    template_bundle = Bundle(slug="template", ir=ir, layout=None, plan=None,
                             form_html=template_html, guide_html=None, pdf=None)
    check("template inputs do not belong to the preceding live cell",
          len(template_cells) == 1
          and not input_boxes(template_cells[0])
          and check_inputs_over_printed_text(template_bundle)["holds"] is True)

    enclosed_plain_layout = {"pages": [{"index": 1, "cells": [{
        "id": "p1c0",
        "x0": 8.0, "y0": 8.0, "x1": 38.0, "y1": 20.0,
        "border": {
            "top": {"gray": 0.0}, "bottom": {"gray": 0.0},
            "left": {"gray": 0.0}, "right": {"gray": 0.0},
        },
        "is_empty": True,
        "rectangular": True,
        "kind": "field",
        "text_run_ids": [],
    }]}]}
    plain_input = (
        '<input type="text" class="fi" '
        'style="inset:0pt 0pt 0pt 0pt">')
    for inert_label, inert_markup in (
            ("comment", f"<!--{plain_input}-->"),
            ("script", f"<script>{plain_input}</script>"),
            ("template", f"<template>{plain_input}</template>")):
        inert_plain_html = (
            '<div class="page page-1">'
            '<div id="p1c0" class="c f" data-cell-kind="field" '
            'data-field-kind="text" '
            'style="left:8pt;top:8pt;width:30pt;height:12pt">'
            + inert_markup
            + "</div></div>"
        )
        inert_plain_bundle = Bundle(
            slug=f"inert-plain-{inert_label}",
            ir=ir,
            layout=enclosed_plain_layout,
            plan=None,
            form_html=inert_plain_html,
            guide_html=None,
            pdf=None,
        )
        inert_plain_result = check_money_boxes_have_inputs(
            inert_plain_bundle)
        check(
            f"{inert_label} plain input does not make an empty box fillable",
            inert_plain_result["holds"] is False
            and inert_plain_result["offenders"][0]["why"]
            == "enclosed empty box, no input"
            and not input_boxes(inert_plain_bundle.cells[0]),
        )

    # ------------------------------------------------------------------
    # A blank the SHEET reserves for the Bureau, read from the source's own
    # glyph stream. Driven at the predicate rather than through a bundle,
    # because what has to hold is a property of the paper: the phrase, its
    # rectangle, and which blank that rectangle governs.
    # ------------------------------------------------------------------
    def _glyph_line(text: str, x0: float, y0: float,
                    advance: float = 4.0, height: float = 8.07):
        """One printed line, one glyph per visible character.

        Spaces are omitted exactly as `drawn_glyph_boxes` omits them, which is
        why the match phrases carry no spaces either.
        """
        out, pen = [], x0
        for character in text:
            if character == " ":
                pen += advance
                continue
            out.append(SourceGlyph(character, pen, y0,
                                   pen + advance, y0 + height))
            pen += advance
        return out

    # 0605's real shape: two captions on ONE baseline, the Bureau's second.
    bcs_line = _glyph_line(
        "Return Period (MM/DD/YYYY)     BCS No./Item No. "
        "(To be filled up by the BIR)", 200.0, 173.23)
    bcs_captions = source_bureau_reservations(bcs_line)
    check(
        "the reservation's rectangle is the phrase's, not its whole line",
        len(bcs_captions) == 1
        and bcs_captions[0][0] > bcs_line[0].x0
        and bcs_captions[0][2] <= bcs_line[-1].x1 + OVERLAP_EPS_PT,
    )
    reserved_box = (bcs_captions[0][0] - 4.0, 186.6,
                    bcs_captions[0][2] + 40.0, 205.56)
    taxpayer_box = (bcs_line[0].x0, 186.6, bcs_line[0].x0 + 60.0, 205.56)
    check(
        "the blank under the reservation is reserved and its row peer is not",
        bureau_reserved_box(reserved_box, bcs_captions)
        and not bureau_reserved_box(taxpayer_box, bcs_captions),
    )
    check(
        "a blank a full line further down is not claimed by that caption",
        not bureau_reserved_box(
            (reserved_box[0], 200.0, reserved_box[2], 218.0), bcs_captions),
    )
    # The bottom-of-sheet band: the heading is printed INSIDE the compartment
    # it governs, and the compartment beside it is a different box.
    band_captions = source_bureau_reservations(
        _glyph_line("Machine Validation", 20.0, 848.0))
    check(
        "a heading printed inside its own compartment reserves it",
        bureau_reserved_box((16.32, 847.08, 392.71, 897.72), band_captions)
        and not bureau_reserved_box(
            (392.71, 847.08, 595.32, 897.72), band_captions),
    )
    # Prose about those boxes is not a reservation. The guide sentence is the
    # exact text this rule exists to refuse.
    check(
        "guide prose that merely mentions the band reserves nothing",
        source_bureau_reservations(_glyph_line(
            "The machine validation shall reflect the date of payment",
            40.0, 400.0)) == ()
        and source_bureau_reservations(_glyph_line(
            "Machine Validation/Revenue Official Receipt Details",
            20.0, 400.0)) != (),
    )
    check(
        "the sheet's own missing 'by' is matched as the sheet prints it",
        source_bureau_reservations(
            _glyph_line("(To be filled up the BIR)", 10.0, 10.0)) != ()
        and source_bureau_reservations(
            _glyph_line("(To be filled up by the taxpayer)", 10.0, 10.0)) == (),
    )

    moved_plain_ir = copy.deepcopy(ir)
    moved_page = copy.deepcopy(ir["pages"][0])
    moved_page["index"] = 2
    moved_plain_ir["pages"] = [moved_page]
    moved_plain_html = (
        '<div class="page page-2">'
        '<div class="t" id="p2t0" '
        'style="left:10pt;top:10pt;color:#000000">Rate?</div>'
        '<div id="p1c0" class="c f" data-cell-kind="field" '
        'data-field-kind="text" '
        'style="left:8pt;top:8pt;width:30pt;height:12pt">'
        + plain_input
        + "</div></div>"
    )
    moved_plain_bundle = Bundle(
        slug="moved-plain",
        ir=moved_plain_ir,
        layout=enclosed_plain_layout,
        plan=None,
        form_html=moved_plain_html,
        guide_html=None,
        pdf=None,
    )
    moved_plain_overlap = check_inputs_over_printed_text(moved_plain_bundle)
    moved_plain_money = check_money_boxes_have_inputs(moved_plain_bundle)
    check(
        "plain field id page cannot substitute for its enclosing DOM page",
        moved_plain_overlap["holds"] is False
        and moved_plain_money["holds"] is False
        and moved_plain_overlap["offenders"][0]["emitted_id_page"] == 1
        and moved_plain_overlap["offenders"][0]["emitted_dom_page"] == 2
        and moved_plain_overlap["offenders"][0]["layout_page"] == 1
        and "emitted-cell-page-mismatch"
        in moved_plain_overlap["offenders"][0]["failure_kinds"],
    )

    # A plain enclosed "empty" box whose width is mostly covered by printed
    # glyph ink is the emitter's pre-printed refusal, not a missing input.
    # The exclusion demands measured coverage -- half the run's own height
    # inside the cell, over half the cell's width -- and is published, so a
    # box a neighbouring line merely grazes keeps its fillability check.
    def preprinted_box_fixture(run_y0: float, run_y1: float) -> Bundle:
        covered_ir = copy.deepcopy(ir)
        covered_ir["pages"][0]["text_runs"] = [{
            "text": "Wages", "font": "Arial", "size_pt": 8.0, "color": 0,
            "x0": 9.0, "y0": run_y0, "x1": 34.0, "y1": run_y1,
            "origin_x": 9.0, "baseline_y": run_y1 - 2.0,
            "char_origin_offsets_pt": [0.0, 5.0, 10.0, 15.0, 20.0],
            "char_widths_pt": [5.0, 5.0, 5.0, 5.0, 5.0],
        }]
        covered_html = (
            '<div class="page page-1" id="page-1" '
            'style="width:100pt;height:100pt">'
            f'<div class="t" id="p1t0" style="left:9pt;top:{run_y0}pt;'
            'color:#000000">XP010</div>'
            '<div id="p1c0" class="c f" data-cell-kind="field" '
            'style="left:8pt;top:8pt;width:30pt;height:12pt"></div></div>')
        return Bundle(
            slug="preprinted-box", ir=covered_ir,
            layout=enclosed_plain_layout, plan=None,
            form_html=covered_html, guide_html=None, pdf=None)

    covered_money = check_money_boxes_have_inputs(
        preprinted_box_fixture(8.0, 18.0))
    check(
        "a mostly-inked empty box is excluded and the exclusion published",
        covered_money["holds"] is True
        and covered_money["boxes_preprinted"] == 1
        and covered_money["boxes_checked"] == 0,
    )
    grazed_money = check_money_boxes_have_inputs(
        preprinted_box_fixture(2.0, 10.0))
    check(
        "a box a neighbouring line grazes keeps its fillability check",
        grazed_money["holds"] is False
        and grazed_money["boxes_preprinted"] == 0
        and grazed_money["offenders"][0]["why"]
        == "enclosed empty box, no input",
    )

    # ---- RIDER (F235/F237, user-approved): decoration exclusions ---------
    #
    # The generic-graze principle above holds for ordinary text ("Wages").
    # A printed ATC-format constant is different: on the whole corpus it
    # intersects exactly ONE input cell (1800's DN 010 sliver, cut by the
    # row mosaic from the constant's own undivided box), and the three
    # legitimate ATC write-ins sit BELOW their constants with no overlap.
    # The exclusion is published (`boxes_decoration`), never silent.
    def rider_fixture(*, run_text=None, cell_height=12.0, fill=None,
                      combs=False) -> Bundle:
        rider_ir = copy.deepcopy(ir)
        runs = []
        if run_text is not None:
            runs.append({
                "text": run_text, "font": "Arial", "size_pt": 8.0,
                "color": 0, "x0": 9.0, "y0": 2.0, "x1": 34.0, "y1": 10.0,
                "origin_x": 9.0, "baseline_y": 8.0,
                "char_origin_offsets_pt": [0.0, 5.0, 10.0, 15.0, 20.0],
                "char_widths_pt": [5.0, 5.0, 5.0, 5.0, 5.0],
            })
        rider_ir["pages"][0]["text_runs"] = runs
        if fill is not None:
            rider_ir["pages"][0]["area_fills"] = [dict(
                x0=8.0, y0=8.0, x1=38.0, y1=8.0 + cell_height, **fill)]
        rider_layout = copy.deepcopy(enclosed_plain_layout)
        target = rider_layout["pages"][0]["cells"][0]
        target["y1"] = target["y0"] + cell_height
        if combs:
            comb = {"cells": 2, "divider_x": [3.0],
                    "slot_x": [0.0, 3.0, 6.0], "pitch_pt": 3.0}
            rider_layout["pages"][0]["cells"] += [
                {"id": "p1c90", "subject_key": "p1@0,8,8,20", "x0": 0.0,
                 "y0": target["y0"], "x1": 8.0, "y1": target["y1"],
                 "kind": "field", "comb": dict(comb)},
                {"id": "p1c91", "subject_key": "p1@38,8,44,20", "x0": 38.0,
                 "y0": target["y0"], "x1": 44.0, "y1": target["y1"],
                 "kind": "field", "comb": dict(comb)},
            ]
        rider_html = (
            '<div class="page page-1" id="page-1" '
            'style="width:100pt;height:100pt">'
            '<div id="p1c0" class="c f" data-cell-kind="field" '
            f'style="left:8pt;top:8pt;width:30pt;height:{cell_height}pt">'
            '</div></div>')
        return Bundle(
            slug="rider-fixture", ir=rider_ir, layout=rider_layout,
            plan=None, form_html=rider_html, guide_html=None, pdf=None)

    atc_money = check_money_boxes_have_inputs(rider_fixture(run_text="DN 010"))
    check(
        "an ATC constant intersecting the box excludes it, published",
        atc_money["holds"] is True and atc_money["boxes_decoration"] == 1
        and atc_money["boxes_checked"] == 0,
    )
    sep_money = check_money_boxes_have_inputs(rider_fixture(
        fill={"gray": None, "rgb": [1.0, 0.8, 0.6]}, combs=True))
    check(
        "a dedicated non-white fill between two combs excludes it, published",
        sep_money["holds"] is True and sep_money["boxes_decoration"] == 1,
    )
    unflanked_money = check_money_boxes_have_inputs(rider_fixture(
        fill={"gray": None, "rgb": [1.0, 0.8, 0.6]}, combs=False))
    check(
        "the same fill WITHOUT comb neighbours keeps its fillability check",
        unflanked_money["holds"] is False,
    )
    white_money = check_money_boxes_have_inputs(rider_fixture(
        fill={"gray": 1.0, "rgb": [1.0, 1.0, 1.0]}, combs=True))
    check(
        "a WHITE dedicated fill (a writing knockout) is never decoration",
        white_money["holds"] is False,
    )
    # sub-glyph: the fixture IR prints runs ~8pt tall; a 2pt box is shorter
    # than anything the document prints, so it is the sheet's own framing.
    tiny_money = check_money_boxes_have_inputs(rider_fixture(
        run_text="Wages", cell_height=2.0))
    check(
        "a box shorter than the document's smallest glyph is decoration",
        tiny_money["holds"] is True and tiny_money["boxes_decoration"] == 1,
    )

    # A placement the guide plan relocated is subtracted from the source's
    # expectation -- the reflowed guide drops images by documented design --
    # and the subtraction is published.  Without the plan's claim, or with a
    # claim whose signature the source never places, the assertion fails.
    import fitz as _fitz_images
    image_doc = _fitz_images.open()
    image_page = image_doc.new_page(width=100.0, height=100.0)
    seal = _fitz_images.Pixmap(_fitz_images.csRGB, _fitz_images.IRect(0, 0, 2, 2))
    seal.clear_with(255)
    image_page.insert_image(_fitz_images.Rect(10, 10, 40, 30), pixmap=seal)
    image_pdf = image_doc.tobytes()
    image_doc.close()
    image_ir = copy.deepcopy(ir)
    image_ir["pages"][0]["images"] = [{
        "transform": [30.0, 0.0, 0.0, 20.0, 10.0, 10.0]}]
    imageless_html = (
        '<div class="page page-1" id="page-1" '
        'style="width:100pt;height:100pt"></div>')
    relocated_plan = {"inline": [{
        "page": 1, "cut_y_pt": 0.0, "rule_ids": [], "text_run_indices": [],
        "cell_ids": [], "image_indices": [0],
        "marker_pattern": "unfillable-page", "marker": ""}]}
    relocated_result = check_image_transform_applied(Bundle(
        slug="relocated-image", ir=image_ir, layout=None,
        plan=relocated_plan, form_html=imageless_html, guide_html=None,
        pdf=image_pdf))
    check(
        "a guide-relocated placement is subtracted and published",
        relocated_result["holds"] is True
        and relocated_result["placements"] == 1
        and relocated_result["relocated_placements"] == 1,
    )
    unclaimed_result = check_image_transform_applied(Bundle(
        slug="dropped-image", ir=image_ir, layout=None, plan=None,
        form_html=imageless_html, guide_html=None, pdf=image_pdf))
    check(
        "an unclaimed dropped placement still fails",
        unclaimed_result["holds"] is False
        and unclaimed_result["relocated_placements"] == 0
        and unclaimed_result["offenders"][0]["source_placements"] == 1
        and unclaimed_result["offenders"][0]["emitted"] == 0,
    )
    flipped_claim_ir = copy.deepcopy(image_ir)
    flipped_claim_ir["pages"][0]["images"][0]["transform"] = [
        30.0, 0.0, 0.0, -20.0, 10.0, 30.0]
    mismatched_claim = check_image_transform_applied(Bundle(
        slug="mismatched-claim", ir=flipped_claim_ir, layout=None,
        plan=relocated_plan, form_html=imageless_html, guide_html=None,
        pdf=image_pdf))
    check(
        "a plan claim whose signature the source never places fails closed",
        mismatched_claim["holds"] is False
        and mismatched_claim["relocated_placements"] == 0,
    )

    # A malformed comb can put a slot outside its parent. The real `.f` clips
    # that slot, so glyph ink in the clipped-away area is not under an input.
    off_cell_html = (
        '<div class="page page-1" id="page-1" style="width:100pt;height:100pt">'
        '<div class="t" id="p1t0" style="left:10pt;top:10pt">Rate?</div>'
        '<div id="p1c0" class="c f" data-cell-kind="mixed" '
        'style="left:8pt;top:20pt;width:30pt;height:12pt">'
        '<div class="s" data-slot="0" '
        'style="left:0pt;top:-12pt;width:30pt;height:8pt">'
        '<input type="text" class="fi fc" data-slot-index="0"></div></div></div>')
    off_cell = Bundle(slug="off-cell", ir=ir, layout=None, plan=None,
                      form_html=off_cell_html, guide_html=None, pdf=None)
    check("comb input geometry clipped outside its parent cannot collide",
          check_inputs_over_printed_text(off_cell)["holds"] is True)

    # Most assertion details are bounded previews, and say exactly what they
    # omit. The comb assertion is the referee's evidence packet, so every
    # offender must survive publication -- including the first one beyond the
    # old twelve-item preview.
    preview = broken("preview", list(range(MAX_OFFENDERS + 1)))
    check("bounded offender previews state that one record was omitted",
          len(preview["offenders"]) == MAX_OFFENDERS
          and preview["offenders_published"] == MAX_OFFENDERS
          and preview["offenders_omitted"] == 1
          and preview["offenders_complete"] is False)

    class CombPublicationFixture:
        layout = {"pages": []}
        doc = object()
        cells: list[Cell] = []
        relocated_cells: set[str] = set()
        layout_pages = {1: {"cells": [
            {"id": f"p1c{index}", "x0": 0.0, "y0": 0.0,
             "x1": 40.0, "y1": 10.0,
             "comb": {"cells": 1, "y0": 2.0, "y1": 8.0,
                      "divider_gray": 0.0}}
            for index in range(MAX_OFFENDERS + 1)
        ]}}
        vector_pages = {1: VectorPage((
            VectorPaint(19.88, 2.0, 20.12, 8.0, 0.0, 1.0, 0, "test"),
        ), ())}

    complete = check_comb_slots_match_printed(CombPublicationFixture())
    check("comb publication keeps the offender beyond the old preview limit",
          complete["offender_count"] == MAX_OFFENDERS + 2
          and complete["offenders_published"] == MAX_OFFENDERS + 2
          and complete["offenders_omitted"] == 0
          and complete["offenders_complete"] is True
          and len(complete["offenders"]) == MAX_OFFENDERS + 2
          and complete["offenders"][0]["cell"]
          == "<comb-owner-registry>"
          and complete["offenders"][-1]["cell"] == f"p1c{MAX_OFFENDERS}")

    class CombEmissionFixture:
        layout = {"pages": []}
        doc = object()
        relocated_cells: set[str] = set()

        def _snapshot_layout(self) -> None:
            self.layout = {
                "pages": [
                    self.layout_pages[index]
                    for index in sorted(self.layout_pages)
                ],
            }
            self.layout_payload = json.dumps(
                self.layout, sort_keys=True, separators=(",", ":"),
            ).encode("utf-8")
            self.layout_sha256 = hashlib.sha256(
                self.layout_payload).hexdigest()

        def _bind_owner_registry(self) -> None:
            for page_index, page in sorted(self.layout_pages.items()):
                page["index"] = page_index
                subjects = []
                for cell in page["cells"]:
                    bbox = [
                        cell[name] for name in ("x0", "y0", "x1", "y1")
                    ]
                    decimal_bbox = [
                        _canonical_decimal(value) for value in bbox
                    ]
                    if any(value is None for value in decimal_bbox):
                        raise AssertionError("comb fixture bbox is not exact")
                    subject_key = (
                        f"p{page_index}@"
                        + ",".join(
                            _decimal_identity(value)
                            for value in decimal_bbox
                            if value is not None
                        )
                    )
                    cell["subject_key"] = subject_key
                    if not isinstance(cell.get("comb"), dict):
                        continue
                    # Every real layout publishes the horizontal writing
                    # surface beside `slot_x`. A fixture that omitted it would
                    # be testing the absence branch by accident in every other
                    # assertion, so the fail-closed value -- the rails' own
                    # centres, which is what a comb whose rail ink could not be
                    # measured publishes -- is filled in here unless the fixture
                    # states one deliberately.
                    comb = cell["comb"]
                    comb.setdefault("writing_x0", float(comb["slot_x"][0]))
                    comb.setdefault("writing_x1", float(comb["slot_x"][-1]))
                    subjects.append({
                        "subject_key": subject_key,
                        "legacy_cell_id": cell["id"],
                        "legacy_bbox": bbox,
                        "cell_id": cell["id"],
                        "mapped_partition_cell_ids": [cell["id"]],
                        "state": "active_unresolved",
                        "reason_codes": ["competing-endpoint-topologies"],
                        "cells": cell["comb"].get("cells"),
                        "blocks_gate": True,
                    })
                page["comb_subjects"] = subjects
            self._snapshot_layout()

        def __init__(self, cells: Sequence[Cell], count: int = 3) -> None:
            self.cells = list(cells)
            width = float(count * 10)
            self.layout_pages = {1: {"cells": [{
                "id": "p1c0", "x0": 0.0, "y0": 0.0,
                "x1": width, "y1": 10.0,
                "comb": {
                    "cells": count,
                    "divider_x": [
                        float(index * 10)
                        for index in range(1, count)
                    ],
                    "slot_x": [
                        float(index * 10)
                        for index in range(count + 1)
                    ],
                },
            }]}}
            dividers = [
                VectorPaint(
                    10.0 * index - 0.12, 2.0,
                    10.0 * index + 0.12, 8.0,
                    0.0, 1.0, index, "test",
                )
                for index in range(count + 1)
            ]
            dividers.append(VectorPaint(
                0.0, 7.88, width, 8.12,
                0.0, 1.0, count + 1, "test-baseline",
            ))
            self.vector_pages = {1: VectorPage(tuple(dividers), ())}
            self._bind_owner_registry()

    def emitted_comb_cell(
            cell_id: str = "p1c0",
            slot_indexes: Sequence[int] = (0, 1, 2),
            input_indexes: Sequence[int | None] | None = None,
            declared: int | None = None,
            geometry: Sequence[tuple[float, float, float, float]] | None = None,
            ) -> Cell:
        if input_indexes is None:
            input_indexes = slot_indexes
        if len(input_indexes) != len(slot_indexes):
            raise AssertionError("slot/input fixture lengths differ")
        if geometry is None:
            geometry = [
                (ordinal * 10.0, 0.0, 10.0, 10.0)
                for ordinal in range(len(slot_indexes))
            ]
        if len(geometry) != len(slot_indexes):
            raise AssertionError("slot/geometry fixture lengths differ")
        container_width = max(
            (left + width for left, _top, width, _height in geometry),
            default=10.0,
        )
        slots = []
        for slot_index, input_index, box in zip(
                slot_indexes, input_indexes, geometry):
            input_attr = (
                f' data-slot-index="{input_index}"'
                if input_index is not None else ""
            )
            left, top, width, height = box
            slots.append(
                f'<div class="s" data-slot="{slot_index}" '
                f'style="left:{left}pt;top:{top}pt;'
                f'width:{width}pt;height:{height}pt">'
                f'<input type="text" class="fi fc"{input_attr}></div>'
            )
        declared_value = len(slot_indexes) if declared is None else declared
        return Cell(
            id=cell_id,
            page=1,
            classes="c f",
            attrs=(
                f' data-comb-slots="{declared_value}" '
                f'style="left:0pt;top:0pt;'
                f'width:{container_width}pt;height:10pt"'
            ),
            rect=(0.0, 0.0, container_width, 10.0),
            inner="".join(slots),
        )

    missing_cell = check_comb_slots_match_printed(CombEmissionFixture([]))
    missing_markup_cell = Cell(
        id="p1c0", page=1, classes="c f",
        attrs=' style="left:0pt;top:0pt;width:40pt;height:10pt"',
        rect=(0.0, 0.0, 40.0, 10.0), inner="")
    missing_markup = check_comb_slots_match_printed(
        CombEmissionFixture([missing_markup_cell]))
    zero_slot_cell = dataclasses.replace(
        missing_markup_cell,
        attrs=(' data-comb-slots="0" '
               'style="left:0pt;top:0pt;width:40pt;height:10pt"'),
    )
    zero_slots = check_comb_slots_match_printed(
        CombEmissionFixture([zero_slot_cell]))
    for label, result, state, slots in (
            ("missing emitted cell", missing_cell,
             "missing-emitted-cell", None),
            ("missing emitted comb markup", missing_markup,
             "missing-comb-markup", None),
            ("zero physical emitted slots", zero_slots,
             "zero-physical-slots", 0)):
        offender = result["offenders"][0] if result["offenders"] else {}
        check(
            f"{label} fails closed without substituting the lattice count",
            result["holds"] is False
            and result["offender_count"] == 1
            and result["offenders_complete"] is True
            and result["emission_behind_layout"] == 1
            and result["layout_mismatches"] == 0
            and offender.get("printed") == 3
            and offender.get("latticed") == 3
            and offender.get("slots") == slots
            and offender.get("emission_state") == state
            and offender.get("layout_relation") == "match"
            and offender.get("emission_relation") == "invalid"
            and "invalid-emission" in offender.get("failure_kinds", ()),
        )

    valid_three = emitted_comb_cell()
    relocated_container = check_comb_slots_match_printed(
        CombEmissionFixture([
            dataclasses.replace(
                valid_three, rect=(100.0, 100.0, 130.0, 110.0))
        ]))
    relocated_offender = relocated_container["offenders"][0]
    check(
        "equal counts cannot hide a relocated emitted comb container",
        relocated_container["holds"] is False
        and relocated_container["emission_invalid"] == 1
        and relocated_offender["emission_container_binding"]["rect_matches"]
        is False
        and "emission-container-geometry-mismatch"
        in relocated_offender["failure_kinds"],
    )

    resized_container = check_comb_slots_match_printed(
        CombEmissionFixture([
            dataclasses.replace(
                valid_three, rect=(0.0, 0.0, 30.0, 11.0))
        ]))
    check(
        "equal counts cannot hide a resized emitted comb container",
        resized_container["holds"] is False
        and "emission-container-geometry-mismatch"
        in resized_container["offenders"][0]["failure_kinds"],
    )

    uneven_slots = check_comb_slots_match_printed(
        CombEmissionFixture([
            emitted_comb_cell(
                geometry=((0.0, 0.0, 1.0, 10.0),
                          (1.0, 0.0, 28.0, 10.0),
                          (29.0, 0.0, 1.0, 10.0)))
        ]))
    uneven_offender = uneven_slots["offenders"][0]
    check(
        "equal counts cannot hide physical slot edges at wrong positions",
        uneven_slots["holds"] is False
        and uneven_offender["emission_layout_position"]["matches"] is False
        and uneven_offender["emission_source_position"]["matches"] is False
        and "emission-layout-position-mismatch"
        in uneven_offender["failure_kinds"]
        and "emission-source-position-mismatch"
        in uneven_offender["failure_kinds"],
    )

    def precision_fixture_with_divider(
            center_x: float) -> CombEmissionFixture:
        fixture = CombEmissionFixture(
            [emitted_comb_cell(slot_indexes=(0, 1))], count=2)
        fixture.vector_pages = {1: VectorPage((
            VectorPaint(
                -0.12, 2.0, 0.12, 8.0,
                0.0, 1.0, 0, "precision-left-rail"),
            VectorPaint(
                center_x - 0.12, 2.0, center_x + 0.12, 8.0,
                0.0, 1.0, 1, "precision-adversary"),
            VectorPaint(
                19.88, 2.0, 20.12, 8.0,
                0.0, 1.0, 2, "precision-right-rail"),
            VectorPaint(
                0.0, 7.88, 20.0, 8.12,
                0.0, 1.0, 3, "precision-baseline"),
        ), ())}
        return fixture

    # Cross-representation source comparisons are bound by the comb referee's
    # adjudicated POSITION_TOL_PT, not the emitted four-decimal epsilon: a
    # sub-centipoint float-noise delta between raw source coordinates and
    # 0.01pt-quantised emitted geometry is not a displacement.
    rounding_noise = check_comb_slots_match_printed(
        precision_fixture_with_divider(10.004))
    check(
        "sub-centipoint source rounding noise is inside the referee bound",
        rounding_noise["holds"] is True,
    )
    displaced_result = check_comb_slots_match_printed(
        precision_fixture_with_divider(10.30))
    displaced_offender = (
        displaced_result["offenders"][0]
        if displaced_result["offenders"] else {})
    displaced_position = displaced_offender.get(
        "emission_source_position", {})
    check(
        "a source divider displaced past the referee bound still fails",
        displaced_result["holds"] is False
        and displaced_position.get("tolerance_pt") == POSITION_TOL_PT
        and displaced_offender.get("emission_layout_position", {})
        .get("matches") is True
        and displaced_position.get("expected_internal_edges_x") == [10.3]
        and displaced_position.get("deltas_pt") == [-0.3]
        and "emission-source-position-mismatch"
        in displaced_offender.get("failure_kinds", ()),
    )

    def source_frame_binding_fixture(
            slot_edges: Sequence[float],
            ) -> CombEmissionFixture:
        geometry = [
            (
                float(left), 0.0,
                float(right - left), 10.0,
            )
            for left, right in zip(slot_edges, slot_edges[1:])
        ]
        emitted = emitted_comb_cell(
            slot_indexes=tuple(range(len(geometry))),
            geometry=geometry,
        )
        emitted = dataclasses.replace(
            emitted,
            attrs=(
                f' data-comb-slots="{len(geometry)}" '
                'style="left:0pt;top:0pt;width:40pt;height:10pt"'
            ),
            rect=(0.0, 0.0, 40.0, 10.0),
        )
        fixture = CombEmissionFixture([emitted], count=len(geometry))
        fixture.layout_pages = {1: {"cells": [{
            "id": "p1c0",
            "x0": 0.0, "y0": 0.0, "x1": 40.0, "y1": 10.0,
            "comb": {
                "cells": len(geometry),
                "divider_x": [
                    float(value) for value in slot_edges[1:-1]],
                "slot_x": [float(value) for value in slot_edges],
            },
        }]}}
        fixture._bind_owner_registry()
        rail_and_dividers = [
            VectorPaint(
                value - 0.12, 2.0, value + 0.12, 8.0,
                0.0, 1.0, index, "outer-binding-frame",
            )
            for index, value in enumerate(
                (5.0, 10.0, 15.0, 20.0, 25.0, 30.0, 35.0))
        ]
        rail_and_dividers.append(VectorPaint(
            5.0, 7.88, 35.0, 8.12,
            0.0, 1.0, 20, "outer-binding-baseline",
        ))
        fixture.vector_pages = {
            1: VectorPage(tuple(rail_and_dividers), ())}
        return fixture

    inset_frame = check_comb_slots_match_printed(
        source_frame_binding_fixture(
            (5.0, 10.0, 15.0, 20.0, 25.0, 30.0, 35.0)))
    check(
        "an inset physical comb binds its outer edges to source rails",
        inset_frame["holds"] is True,
    )
    for label, edges in (
            ("blank-margin expansion",
             (0.0, 10.0, 15.0, 20.0, 25.0, 30.0, 40.0)),
            ("symmetric shrink",
             (6.0, 10.0, 15.0, 20.0, 25.0, 30.0, 34.0)),
            ("relocation",
             (6.0, 11.0, 16.0, 21.0, 26.0, 31.0, 36.0))):
        frame_binding = check_comb_slots_match_printed(
            source_frame_binding_fixture(edges))
        frame_offender = frame_binding["offenders"][0]
        left_geometry = frame_offender[
            "source_frame_geometry"]["left_rail"]
        right_geometry = frame_offender[
            "source_frame_geometry"]["right_rail"]
        check(
            f"source rail binding rejects {label}",
            frame_binding["holds"] is False
            and {
                key: left_geometry[key]
                for key in ("center_x", "ink_x0", "ink_x1")
            } == {"center_x": 5.0, "ink_x0": 4.88, "ink_x1": 5.12}
            and {
                key: right_geometry[key]
                for key in ("center_x", "ink_x0", "ink_x1")
            } == {"center_x": 35.0, "ink_x0": 34.88, "ink_x1": 35.12}
            and left_geometry["contact_intervals_x"] == [[5.0, 5.12]]
            and right_geometry["contact_intervals_x"] == [[34.88, 35.0]]
            and "emission-source-outer-position-mismatch"
            in frame_offender["failure_kinds"]
            and "layout-source-outer-position-mismatch"
            in frame_offender["failure_kinds"],
        )

    # ---- the outer edges are the rails' INK, never their centres (F208) ----
    #
    # A rail is a painted stroke, so "where the rail is" and "where the paper
    # a taxpayer may write on starts" are half a stroke apart. The rails above
    # are 0.24pt wide, where the two answers are 0.12pt apart and the referee
    # bound cannot tell them apart; these are 1.2pt, where they are 0.6pt apart
    # and it must. On this sheet the outer compartments run 5.60..14.40 and
    # 25.60..34.40 between rails CENTRED on 5.0 and 35.0.
    THICK = 0.6

    def writing_edge_fixture(
            *,
            emitted_outer: tuple[float, float] = (5.6, 34.4),
            layout_writing: tuple[float, float] | None = (5.6, 34.4),
            ) -> CombEmissionFixture:
        dividers = (15.0, 25.0)
        edges = (emitted_outer[0], *dividers, emitted_outer[1])
        geometry = [
            (float(left), 0.0, float(right - left), 10.0)
            for left, right in zip(edges, edges[1:])
        ]
        emitted = dataclasses.replace(
            emitted_comb_cell(
                slot_indexes=tuple(range(len(geometry))),
                geometry=geometry,
            ),
            attrs=(
                f' data-comb-slots="{len(geometry)}" '
                'style="left:0pt;top:0pt;width:40pt;height:10pt"'
            ),
            rect=(0.0, 0.0, 40.0, 10.0),
        )
        fixture = CombEmissionFixture([emitted], count=len(geometry))
        comb: dict[str, Any] = {
            "cells": len(geometry),
            "divider_x": [float(value) for value in dividers],
            # `slot_x` stays the RAILS' own centres, which is what it means.
            "slot_x": [5.0, *(float(value) for value in dividers), 35.0],
        }
        if layout_writing is not None:
            comb["writing_x0"] = float(layout_writing[0])
            comb["writing_x1"] = float(layout_writing[1])
        fixture.layout_pages = {1: {"cells": [{
            "id": "p1c0",
            "x0": 0.0, "y0": 0.0, "x1": 40.0, "y1": 10.0,
            "comb": comb,
        }]}}
        fixture._bind_owner_registry()
        if layout_writing is None:
            # `_bind_owner_registry` fills the fail-closed value in for every
            # other fixture; this one is testing that its ABSENCE is a failure,
            # so the keys are removed after binding.
            for page in fixture.layout["pages"]:
                for cell in page["cells"]:
                    cell["comb"].pop("writing_x0", None)
                    cell["comb"].pop("writing_x1", None)
            fixture._snapshot_layout()
        paints = [
            VectorPaint(
                value - THICK, 2.0, value + THICK, 8.0,
                0.0, 1.0, index, "writing-edge-frame",
            )
            for index, value in enumerate((5.0, 35.0))
        ]
        paints.extend(
            VectorPaint(
                value - 0.12, 2.0, value + 0.12, 8.0,
                0.0, 1.0, 10 + index, "writing-edge-divider",
            )
            for index, value in enumerate(dividers)
        )
        paints.append(VectorPaint(
            5.0, 7.88, 35.0, 8.12,
            0.0, 1.0, 20, "writing-edge-baseline",
        ))
        fixture.vector_pages = {1: VectorPage(tuple(paints), ())}
        return fixture

    on_the_ink = check_comb_slots_match_printed(writing_edge_fixture())
    check(
        "outer compartments laid on the rails' ink edges are accepted",
        on_the_ink["holds"] is True,
    )
    # The discriminating mutation: the SAME comb laid on the rails' centres,
    # which is exactly what every comb in this corpus used to be laid on. Both
    # outer relations must reject it -- the emitted document against the layout
    # it claims to render, and the emitted document against the sheet itself.
    on_the_centres = check_comb_slots_match_printed(
        writing_edge_fixture(emitted_outer=(5.0, 35.0)))
    centres_offender = (
        on_the_centres["offenders"][0] if on_the_centres["offenders"] else {})
    check(
        "an outer compartment laid on the rail's centre sits on printed ink",
        on_the_centres["holds"] is False
        and centres_offender.get(
            "emission_layout_outer_position", {}).get("deltas_pt")
        == [-0.6, 0.6]
        and centres_offender.get(
            "emission_source_outer_position", {}).get("deltas_pt")
        == [-0.6, 0.6]
        and "emission-layout-outer-position-mismatch"
        in centres_offender.get("failure_kinds", ())
        and "emission-source-outer-position-mismatch"
        in centres_offender.get("failure_kinds", ()),
    )
    # A layout that states no horizontal writing surface cannot be scored on
    # one, and an unscoreable comb is an offender rather than a pass.
    no_writing_surface = check_comb_slots_match_printed(
        writing_edge_fixture(layout_writing=None))
    absent_offender = (
        no_writing_surface["offenders"][0]
        if no_writing_surface["offenders"] else {})
    check(
        "a comb with no published writing surface fails closed, by name",
        no_writing_surface["holds"] is False
        and absent_offender.get(
            "emission_layout_outer_position", {}).get("matches") is False
        and absent_offender.get(
            "layout_source_outer_position", {}).get("matches") is False
        and "writing_x0/writing_x1" in (absent_offender.get(
            "emission_layout_outer_position", {}).get("unavailable_reason")
            or "")
        and "emission-layout-outer-position-mismatch"
        in absent_offender.get("failure_kinds", ())
        and "layout-source-outer-position-mismatch"
        in absent_offender.get("failure_kinds", ()),
    )
    # ...and a layout whose writing surface is not on the sheet's own rail ink
    # is caught even when the emitter renders it faithfully.
    displaced_writing = check_comb_slots_match_printed(
        writing_edge_fixture(emitted_outer=(5.9, 34.4),
                             layout_writing=(5.9, 34.4)))
    displaced_writing_offender = (
        displaced_writing["offenders"][0]
        if displaced_writing["offenders"] else {})
    check(
        "a writing surface off the sheet's own rail ink is caught in the layout",
        displaced_writing["holds"] is False
        and displaced_writing_offender.get(
            "emission_layout_outer_position", {}).get("matches") is True
        and displaced_writing_offender.get(
            "layout_source_outer_position", {}).get("deltas_pt")
        == [0.3, 0.0]
        and "layout-source-outer-position-mismatch"
        in displaced_writing_offender.get("failure_kinds", ()),
    )

    # ---- the judge reads the INPUT's own inset, not only its slot (F208) ----
    #
    # Every comb input in this corpus fills its slot exactly, and nothing said
    # so. Scoring the slot div made any producer-side move inside a slot
    # invisible: an input inset off printed ink would score as though it were
    # still on it, and an input widened back out over that ink would score as
    # though it were not.
    inset_slot_cell = parse_cells(
        '<div class="page page-1">'
        '<div id="p1c0" class="c f" data-cell-kind="field" '
        'data-comb-slots="1" style="left:0pt;top:0pt;width:20pt;height:10pt">'
        '<div class="s" data-slot="0" '
        'style="left:0pt;top:0pt;width:20pt;height:10pt">'
        '<input type="text" class="fi fc" data-slot-index="0" '
        'style="inset:1pt 2pt 3pt 4pt"></div></div>'
        "</div>")[0]
    flush_slot_cell = parse_cells(
        '<div class="page page-1">'
        '<div id="p1c0" class="c f" data-cell-kind="field" '
        'data-comb-slots="1" style="left:0pt;top:0pt;width:20pt;height:10pt">'
        '<div class="s" data-slot="0" '
        'style="left:0pt;top:0pt;width:20pt;height:10pt">'
        '<input type="text" class="fi fc" data-slot-index="0"></div></div>'
        "</div>")[0]
    check(
        "a comb input's own inset moves the box the judge scores",
        input_boxes(inset_slot_cell) == [(4.0, 1.0, 18.0, 7.0)]
        and input_boxes(flush_slot_cell) == [(0.0, 0.0, 20.0, 10.0)],
    )

    def emitted_cell_markup(cell: Cell) -> str:
        return (
            f'<div id="{cell.id}" class="{cell.classes}"{cell.attrs}>'
            f"{cell.inner}</div>")

    wrong_dom_page_html = (
        '<div class="page page-2">'
        + emitted_cell_markup(valid_three)
        + "</div>"
    )
    wrong_dom_page_fixture = CombEmissionFixture(
        parse_cells(wrong_dom_page_html))
    wrong_dom_page_fixture.form_html = wrong_dom_page_html
    wrong_dom_page = check_comb_slots_match_printed(wrong_dom_page_fixture)
    wrong_dom_page_offender = wrong_dom_page["offenders"][0]
    check(
        "cell id page cannot substitute for the actual enclosing DOM page",
        wrong_dom_page["holds"] is False
        and wrong_dom_page_offender["emission_container_binding"]
        ["emitted_id_page"] == 1
        and wrong_dom_page_offender["emission_container_binding"]
        ["emitted_dom_page"] == 2
        and "emission-container-page-mismatch"
        in wrong_dom_page_offender["failure_kinds"],
    )

    anonymous_slots = "".join(
        f'<div class="s" data-slot="{index}" '
        f'style="left:{index * 10}pt;top:0pt;width:10pt;height:10pt">'
        f'<input data-slot-index="{index}"></div>'
        for index in range(3)
    )
    anonymous_comb = (
        '<div class="c f" data-field-kind="comb" data-comb-slots="3" '
        'style="left:0pt;top:0pt;width:30pt;height:10pt">'
        + anonymous_slots
        + "</div>"
    )
    anonymous_html = (
        '<div class="page page-1">'
        + anonymous_comb
        + emitted_cell_markup(valid_three)
        + "</div>"
    )
    anonymous_fixture = CombEmissionFixture(parse_cells(anonymous_html))
    anonymous_fixture.form_html = anonymous_html
    anonymous_result = check_comb_slots_match_printed(anonymous_fixture)
    anonymous_offenders = [
        item for item in anonymous_result["offenders"]
        if "unowned-live-comb-markup" in item["failure_kinds"]
    ]
    check(
        "raw DOM inventory rejects an anonymous live comb before a valid cell",
        anonymous_result["holds"] is False
        and anonymous_result["raw_live_comb_issues"] == 1
        and anonymous_result["inventory_complete"] is False
        and len(anonymous_offenders) == 1
        and anonymous_offenders[0]["raw_dom_evidence"]["slot_count"] == 3,
    )

    scoped_html = (
        '<div class="page page-1">'
        '<template>' + anonymous_comb + "</template>"
        '<section class="gl-page"><section>'
        + anonymous_comb
        + "</section></section>"
        + emitted_cell_markup(valid_three)
        + "</div>"
    )
    scoped_fixture = CombEmissionFixture(parse_cells(scoped_html))
    scoped_fixture.form_html = scoped_html
    scoped_result = check_comb_slots_match_printed(scoped_fixture)
    check(
        "explicit template and guide combs are excluded from live inventory",
        scoped_result["holds"] is True
        and scoped_result["raw_live_comb_issues"] == 0
        and scoped_result["inventory_complete"] is True,
    )

    live_two_slots = "".join(
        f'<div class="s" data-slot="{index}" '
        f'style="left:{index * 10}pt;top:0pt;width:10pt;height:10pt">'
        f'<input data-slot-index="{index}"></div>'
        for index in range(2)
    )
    inert_third_slot = (
        '<div class="s" data-slot="2" '
        'style="left:20pt;top:0pt;width:10pt;height:10pt">'
        '<input data-slot-index="2"></div>'
    )
    for inert_label, inert_markup in (
            ("comment", f"<!--{inert_third_slot}-->"),
            ("script", f"<script>{inert_third_slot}</script>"),
            ("template", f"<template>{inert_third_slot}</template>")):
        inert_slot_cell = dataclasses.replace(
            valid_three, inner=live_two_slots + inert_markup)
        inert_slot_html = (
            '<div class="page page-1">'
            + emitted_cell_markup(inert_slot_cell)
            + "</div>"
        )
        inert_slot_fixture = CombEmissionFixture(
            parse_cells(inert_slot_html))
        inert_slot_fixture.form_html = inert_slot_html
        inert_slot_result = check_comb_slots_match_printed(
            inert_slot_fixture)
        inert_slot_offender = inert_slot_result["offenders"][0]
        check(
            f"{inert_label} slot markup is not a live physical slot",
            inert_slot_result["holds"] is False
            and inert_slot_offender["physical_slots"] == 2
            and inert_slot_offender["slots"] == 2
            and inert_slot_offender["emission_state"]
            in {"invalid-slot-geometry",
                "declared-physical-slot-mismatch"},
        )

    def inert_input_slots(wrapper: str) -> str:
        slots: list[str] = []
        for index in range(3):
            input_markup = f'<input data-slot-index="{index}">'
            if wrapper == "comment":
                input_markup = f"<!--{input_markup}-->"
            elif wrapper == "script":
                input_markup = f"<script>{input_markup}</script>"
            elif wrapper == "template":
                input_markup = f"<template>{input_markup}</template>"
            slots.append(
                f'<div class="s" data-slot="{index}" '
                f'style="left:{index * 10}pt;top:0pt;'
                f'width:10pt;height:10pt">{input_markup}</div>')
        return "".join(slots)

    for inert_label in ("comment", "script", "template"):
        inert_input_cell = dataclasses.replace(
            valid_three, inner=inert_input_slots(inert_label))
        inert_input_html = (
            '<div class="page page-1">'
            + emitted_cell_markup(inert_input_cell)
            + "</div>"
        )
        inert_input_cells = parse_cells(inert_input_html)
        inert_input_fixture = CombEmissionFixture(inert_input_cells)
        inert_input_fixture.form_html = inert_input_html
        inert_input_comb = check_comb_slots_match_printed(
            inert_input_fixture)
        layout_cell = copy.deepcopy(
            inert_input_fixture.layout_pages[1]["cells"][0])
        inert_input_bundle = Bundle(
            slug=f"inert-{inert_label}",
            ir=ir,
            layout={"pages": [{"index": 1, "cells": [layout_cell]}]},
            plan=None,
            form_html=inert_input_html,
            guide_html=None,
            pdf=None,
        )
        inert_input_money = check_money_boxes_have_inputs(
            inert_input_bundle)
        check(
            f"{inert_label} inputs do not make an editable comb live",
            inert_input_comb["holds"] is False
            and inert_input_comb["offenders"][0]["emission_state"]
            == "slot-input-index-mismatch"
            and inert_input_money["holds"] is False
            and inert_input_money["offenders"][0]["without_input"]
            == [0, 1, 2],
        )

    # A compartment the SOURCE already filled in is emitted with no input on
    # purpose -- the statutory ATC codes, the century, the TIN branch code --
    # and the assertion re-derives that from the source file's own operators.
    # Every fixture below decides the same emitted markup (slot 1 carries no
    # input) purely on what the source is holding under it, which is the whole
    # point: the emitter's own verdict is never consulted.
    def first_offender(result: dict[str, Any]) -> dict[str, Any]:
        """The first published offender, or an empty record if none.

        A verdict that publishes no offender at all is the failure these
        fixtures exist to catch, and it has to read as one rather than as an
        IndexError three frames away from the assertion that caught it.
        """
        offenders = result.get("offenders") or ()
        return offenders[0] if offenders else {}

    def source_filled_fixture(
            *,
            omit: Sequence[int] = (),
            glyphs: Sequence[SourceGlyph] = (),
            extra_paints: Sequence[VectorPaint] = (),
            publish_glyphs: bool = True,
            paints: Sequence[VectorPaint] | None = None,
            slot_top: float = 0.0,
            slot_height: float = 10.0,
            ) -> CombEmissionFixture:
        # `slot_top`/`slot_height` are the WRITING rectangle, which emit sizes
        # from the comb's typography and which is shorter than the cell on
        # every real comb in the corpus. The default keeps them equal so the
        # fixtures that are not about that distinction stay unchanged.
        markup = "".join(
            f'<div class="s" data-slot="{index}" '
            f'style="left:{index * 10}pt;top:{slot_top}pt;'
            f'width:10pt;height:{slot_height}pt">'
            + ("" if index in omit
               else '<input type="text" class="fi fc" '
                    f'data-slot-index="{index}">')
            + "</div>"
            for index in range(3)
        )
        filled_html = (
            '<div class="page page-1">'
            + emitted_cell_markup(
                dataclasses.replace(valid_three, inner=markup))
            + "</div>"
        )
        fixture = CombEmissionFixture(parse_cells(filled_html))
        fixture.form_html = filled_html
        if paints is not None:
            fixture.vector_pages = {1: VectorPage(tuple(paints), ())}
        elif extra_paints:
            fixture.vector_pages = {1: VectorPage(
                (*fixture.vector_pages[1].paints, *extra_paints), ())}
        if publish_glyphs:
            fixture.source_glyphs = {1: tuple(glyphs)}
        return fixture

    def source_oracle_for(
            fixture: CombEmissionFixture) -> SourceSlotOracle:
        published = getattr(fixture, "source_glyphs", None)
        page = fixture.vector_pages.get(1)
        return SourceSlotOracle(
            glyphs=published.get(1) if isinstance(published, dict) else None,
            paints=page.paints if page is not None else None,
            unavailable_reason=(
                None if isinstance(published, dict) and page is not None
                else "fixture publishes no source evidence"),
        )

    # The second compartment of `0 0 0`: one alphanumeric glyph, at the comb's
    # own pitch, wholly inside that compartment's walls.
    printed_constant = source_filled_fixture(
        omit=(1,), glyphs=(SourceGlyph("0", 13.0, 2.0, 17.0, 8.0),))
    printed_constant_result = check_comb_slots_match_printed(printed_constant)
    printed_constant_evidence = emitted_comb_evidence(
        printed_constant.cells, source_oracle_for(printed_constant))
    check(
        "a compartment the source printed a constant into is legitimately "
        "inputless",
        printed_constant_result["holds"] is True
        and printed_constant_result["emission_invalid"] == 0
        and printed_constant_evidence["valid"] is True
        and printed_constant_evidence["state"] == "physical-slots"
        and printed_constant_evidence["source_filled_slots"]
        == {1: {"kind": "printed-constant", "text": "0"}},
    )

    # The rule this whole change must not become. Same markup, blank paper.
    blank_compartment = source_filled_fixture(omit=(1,))
    blank_result = check_comb_slots_match_printed(blank_compartment)
    blank_offender = first_offender(blank_result)
    check(
        "a compartment with neither an input nor source occupancy still fails",
        blank_result["holds"] is False
        and blank_result["emission_invalid"] == 1
        and blank_offender["emission_state"] == "slot-input-index-mismatch"
        and "invalid-emission" in blank_offender["failure_kinds"]
        and "no live input element" in blank_offender["why"]
        and "prints no glyph or shading" in blank_offender["why"]
        and emitted_comb_evidence(
            blank_compartment.cells,
            source_oracle_for(blank_compartment),
        )["source_filled_slots"] == {},
    )

    # Three ways a compartment can hold source ink and still not be occupied by
    # it. Each is a population the corpus separates on; none of them is a
    # character class.
    for ink_label, ink_glyphs in (
            ("a swallowed caption is a segmentation fault, not occupancy",
             (SourceGlyph("Z", 11.0, 2.0, 13.0, 8.0),
              SourceGlyph("I", 13.5, 2.0, 15.0, 8.0))),
            ("a glyph clipped across the compartment wall is not occupancy",
             (SourceGlyph("0", 9.0, 2.0, 13.0, 8.0),)),
            ("a glyph printed outside the compartment's row is not occupancy",
             (SourceGlyph("0", 13.0, 12.0, 17.0, 18.0),))):
        ink_result = check_comb_slots_match_printed(
            source_filled_fixture(omit=(1,), glyphs=ink_glyphs))
        check(
            ink_label,
            ink_result["holds"] is False
            and first_offender(ink_result)["emission_state"]
            == "slot-input-index-mismatch",
        )

    # The character class is NOT one of them, and this is the control that used
    # to say the opposite ("a decimal point is decoration, not a constant").
    # It was refuted by measuring the population it was reasoning about: the
    # money bullet is not drawn inside a digit box, it holds a compartment of
    # its own -- 92 of them corpus-wide, every one the third from the right of
    # a 14-, 29- or 33-compartment money comb with the two centavos
    # compartments to its right. A compartment is one character wide and the
    # source has already spent it.
    for mark_label, mark_text in (
            ("the money bullet occupies its own compartment", "●"),
            ("a printed per-cent sign occupies its own compartment", "%"),
            ("a printed group separator occupies its own compartment", "-")):
        mark_fixture = source_filled_fixture(
            omit=(1,), glyphs=(SourceGlyph(mark_text, 13.0, 2.0, 17.0, 8.0),))
        mark_result = check_comb_slots_match_printed(mark_fixture)
        check(
            mark_label + " and is not a live typing surface",
            mark_result["holds"] is True
            and mark_result["emission_invalid"] == 0
            and emitted_comb_evidence(
                mark_fixture.cells, source_oracle_for(mark_fixture),
            )["source_filled_slots"]
            == {1: {"kind": "printed-mark", "text": mark_text}},
        )

    # What actually protects C4 -- a money comb with no way to enter an amount
    # at all -- is that only an OCCUPIED compartment is ever excused. One
    # printed mark cannot carry the compartments either side of it, so an
    # emitter that empties a whole comb still fails on both of them.
    emptied_comb = source_filled_fixture(
        omit=(0, 1, 2), glyphs=(SourceGlyph("●", 13.0, 2.0, 17.0, 8.0),))
    emptied_offender = first_offender(
        check_comb_slots_match_printed(emptied_comb))
    emptied_evidence = emitted_comb_evidence(
        emptied_comb.cells, source_oracle_for(emptied_comb))
    check(
        "one printed mark does not excuse the compartments beside it",
        emptied_offender.get("emission_state") == "slot-input-index-mismatch"
        and "invalid-emission" in emptied_offender.get("failure_kinds", ())
        and "'slot': 0" in emptied_offender.get("why", "")
        and "'slot': 2" in emptied_offender.get("why", "")
        and "'slot': 1" not in emptied_offender.get("why", "")
        and emitted_comb_evidence(
            emptied_comb.cells, source_oracle_for(emptied_comb),
        )["source_filled_slots"]
        == {1: {"kind": "printed-mark", "text": "●"}}
        and emptied_evidence["source_filled_slots"].keys() == {1},
    )

    # The rectangle the glyph question is asked of is the compartment's printed
    # ROW, never the writing rectangle emit sized. Here the writing rectangle
    # is 8pt inside a 10pt cell -- the corpus shape -- and the bullet's descent
    # falls 0.18pt below its floor, which is exactly what separated 85 corpus
    # money bullets from their 7 identical twins. Asking over the writing
    # rectangle instead reports this compartment as blank paper.
    descended = source_filled_fixture(
        omit=(1,), slot_top=0.72, slot_height=8.0,
        glyphs=(SourceGlyph("●", 13.0, 3.0, 17.0, 8.9),))
    descended_result = check_comb_slots_match_printed(descended)
    check(
        "a glyph descending past the writing rectangle is still printed in "
        "its row",
        descended_result["holds"] is True
        and descended_result["emission_invalid"] == 0
        and emitted_comb_evidence(
            descended.cells, source_oracle_for(descended),
        )["source_filled_slots"]
        == {1: {"kind": "printed-mark", "text": "●"}},
    )

    # And the row is a rectangle the SOURCE is responsible for, so it does not
    # reach past the cell: the same glyph moved below the cell's own floor is
    # a neighbour's, and excuses nothing.
    for row_label, row_glyph in (
            ("above", SourceGlyph("●", 13.0, -6.0, 17.0, -0.1)),
            ("below", SourceGlyph("●", 13.0, 10.1, 17.0, 16.0))):
        outside_row = check_comb_slots_match_printed(source_filled_fixture(
            omit=(1,), slot_top=0.72, slot_height=8.0, glyphs=(row_glyph,)))
        check(
            f"a glyph printed {row_label} the cell is not this row's ink",
            outside_row["holds"] is False
            and first_offender(outside_row)["emission_state"]
            == "slot-input-index-mismatch",
        )

    # A caller that cannot say which row a compartment belongs to gets no glyph
    # evidence at all, rather than a fallback onto the emitted rectangle.
    check(
        "the oracle refuses to answer the glyph question without a row",
        SourceSlotOracle(
            glyphs=(SourceGlyph("0", 13.0, 2.0, 17.0, 8.0),),
            paints=(),
        ).occupancy((10.0, 0.0, 20.0, 10.0)) is None,
    )

    # Tone says the same thing glyphs do -- BIR shades a box to state that no
    # entry applies -- and it is read the same way: topmost covering fill.
    shading_paint = VectorPaint(
        10.0, 0.0, 20.0, 10.0, 0.8509, 1.0, 900, "fill-region")
    shaded = source_filled_fixture(omit=(1,), extra_paints=(shading_paint,))
    shaded_result = check_comb_slots_match_printed(shaded)
    check(
        "a compartment the source shaded is legitimately inputless",
        shaded_result["holds"] is True
        and emitted_comb_evidence(
            shaded.cells, source_oracle_for(shaded),
        )["source_filled_slots"]
        == {1: {"kind": "decorative-shading", "tone": 0.8509}},
    )
    for tone_label, tone_paints in (
            ("a structural rule covering a compartment is ink, not shading",
             (dataclasses.replace(shading_paint, tone=0.0),)),
            ("a knockout painted back over the band leaves a real blank",
             (shading_paint,
              VectorPaint(10.0, 0.0, 20.0, 10.0, 0.9899, 1.0, 901,
                          "fill-region"))),
            ("a translucent fill is not the measured tone",
             (dataclasses.replace(shading_paint, opacity=0.5),)),
            ("shading short of the coverage bound does not fill a "
             "compartment",
             (dataclasses.replace(shading_paint, x0=16.0),))):
        tone_result = check_comb_slots_match_printed(
            source_filled_fixture(omit=(1,), extra_paints=tone_paints))
        check(
            tone_label,
            tone_result["holds"] is False
            and first_offender(tone_result)["emission_state"]
            == "slot-input-index-mismatch",
        )

    # "We could not look" is not "the source filled it in".
    unreadable = source_filled_fixture(
        omit=(1,), glyphs=(SourceGlyph("0", 13.0, 2.0, 17.0, 8.0),),
        publish_glyphs=False)
    unreadable_result = check_comb_slots_match_printed(unreadable)
    check(
        "an unevaluable source excuses no compartment",
        unreadable_result["holds"] is False
        and first_offender(unreadable_result)["emission_state"]
        == "slot-input-index-mismatch"
        and "unevaluable" in first_offender(unreadable_result).get("why", ""),
    )

    # The population this change must leave exactly where it was: a comb that
    # fails for a source-topology reason keeps failing for that reason, with
    # its emission valid and its state `physical-slots`, whether or not one of
    # its compartments is excused. Fourteen corpus offenders have this shape.
    for topology_label, topology_omit in (
            ("with every compartment editable", ()),
            ("with one compartment the source filled", (1,))):
        topology_result = check_comb_slots_match_printed(
            source_filled_fixture(
                omit=topology_omit,
                glyphs=(SourceGlyph("0", 13.0, 2.0, 17.0, 8.0),),
                paints=(),
            ))
        topology_offender = first_offender(topology_result)
        check(
            "an unevaluable source topology still fails as itself "
            + topology_label,
            topology_result["holds"] is False
            and topology_result["emission_invalid"] == 0
            and topology_offender["emission_state"] == "physical-slots"
            and topology_offender["failure_kinds"]
            == ["source-topology-unevaluable"],
        )

    # An excused compartment changes nothing about a comb that has its inputs:
    # the oracle is asked only where an input is missing, so a constant printed
    # under a live input is not this assertion's business and is reported by
    # `inputs_over_printed_text` instead.
    inked_live = source_filled_fixture(
        glyphs=(SourceGlyph("0", 13.0, 2.0, 17.0, 8.0),))
    inked_live_result = check_comb_slots_match_printed(inked_live)
    check(
        "a source constant under a live input leaves the emission valid",
        inked_live_result["holds"] is True
        and emitted_comb_evidence(
            inked_live.cells, source_oracle_for(inked_live),
        )["source_filled_slots"] == {},
    )

    duplicate_cells = check_comb_slots_match_printed(
        CombEmissionFixture([valid_three, valid_three]))
    duplicate_offender = duplicate_cells["offenders"][0]
    check(
        "duplicate emitted cell ids fail even when the last copy matches",
        duplicate_cells["holds"] is False
        and duplicate_cells["layout_mismatches"] == 0
        and duplicate_cells["duplicate_emitted_cell_ids"] == ["p1c0"]
        and duplicate_offender["emission_state"] == "duplicate-emitted-cell"
        and duplicate_offender["emitted_occurrences"] == 2,
    )

    duplicate_layout_fixture = CombEmissionFixture([valid_three])
    duplicate_layout_cell = copy.deepcopy(
        duplicate_layout_fixture.layout_pages[1]["cells"][0])
    duplicate_layout_fixture.layout_pages[1]["cells"].append(
        duplicate_layout_cell)
    duplicate_layout = check_comb_slots_match_printed(
        duplicate_layout_fixture)
    duplicate_layout_offender = duplicate_layout["offenders"][0]
    check(
        "duplicate layout subjects publish and count an invalid certificate",
        duplicate_layout["holds"] is False
        and duplicate_layout["combs_checked"] == 1
        and duplicate_layout["owner_certificates_valid"] == 0
        and duplicate_layout["owner_certificates_invalid"] == 1
        and (duplicate_layout["owner_certificates_valid"]
             + duplicate_layout["owner_certificates_invalid"])
        == duplicate_layout["combs_checked"]
        and duplicate_layout_offender["source_owner_certificate"]["valid"]
        is False
        and duplicate_layout_offender["source_owner_certificate"]
        ["supplies_topology"] is False,
    )

    malformed_retained_fixture = CombEmissionFixture([valid_three])
    malformed_retained_page = malformed_retained_fixture.layout_pages[1]
    malformed_retained_page["cells"].append({
        "id": "p1c1",
        "subject_key": "p1@40,0,50,10",
        "x0": 40.0, "y0": 0.0, "x1": 50.0, "y1": 10.0,
    })
    malformed_retained_page["comb_subjects"].append({
        "subject_key": "p1@40,0,50,10",
        "legacy_cell_id": "p1c1",
        "legacy_bbox": [40.0, 0.0, 50.0, 10.0],
        "cell_id": None,
        "mapped_partition_cell_ids": ["p1c1"],
        "mapped_partition_subject_keys": ["p1@40,0,50,10"],
        "state": "retained_unresolved",
        # Deliberately corrupt: one malformed retained record must invalidate
        # the otherwise valid p1c0 owner and block its complete U-frame.
        "emission": "emitted",
        "reason_codes": ["emission-suppressed-no-final-visible-band"],
        "legacy_comb": {},
        "requires_independent_evidence": True,
        "permitted_transitions": [
            "active_composite", "retired_proven_false"],
        "blocks_gate": True,
    })
    malformed_retained_fixture._snapshot_layout()
    malformed_retained_assertion = check_comb_slots_match_printed(
        malformed_retained_fixture)
    malformed_retained_registry_offender = next(
        item for item in malformed_retained_assertion["offenders"]
        if item["cell"] == "<comb-owner-registry>")
    malformed_retained_offender = next(
        item for item in malformed_retained_assertion["offenders"]
        if item["cell"] == "p1c0")
    check(
        "global retained-ledger corruption makes every active comb offending",
        malformed_retained_assertion["holds"] is False
        and malformed_retained_assertion["combs_checked"] == 1
        and malformed_retained_assertion["owner_certificates_valid"] == 0
        and malformed_retained_assertion["owner_certificates_invalid"] == 1
        and malformed_retained_assertion["layout_unevaluable"] == 1
        and malformed_retained_assertion["source_u_frame_evaluable"] == 0
        and malformed_retained_assertion["offender_count"] == 2
        and malformed_retained_assertion["offenders_complete"] is True
        and malformed_retained_registry_offender["failure_kinds"]
        == ["comb-owner-registry-invalid"]
        and malformed_retained_offender["layout_relation"] == "unevaluable"
        and malformed_retained_offender["failure_kinds"]
        == ["source-topology-unevaluable"]
        and malformed_retained_offender["source_owner_certificate"]["valid"]
        is False
        and "invalid reviewed source owner certificate"
        in malformed_retained_offender["why"],
    )

    undeclared_slots_cell = dataclasses.replace(
        valid_three,
        attrs=re.sub(
            r'\s*data-comb-slots="\d+"', "", valid_three.attrs),
    )
    undeclared_slots = check_comb_slots_match_printed(
        CombEmissionFixture([undeclared_slots_cell]))
    check(
        "physical slots without an emitted count declaration fail closed",
        undeclared_slots["holds"] is False
        and undeclared_slots["layout_mismatches"] == 0
        and undeclared_slots["offenders"][0]["emission_state"]
        == "missing-declared-slot-count",
    )

    duplicate_slot_identity = check_comb_slots_match_printed(
        CombEmissionFixture(
            [emitted_comb_cell(
                slot_indexes=(0, 0), input_indexes=(0, 0), declared=2)],
            count=2,
        ))
    duplicate_slot_offender = duplicate_slot_identity["offenders"][0]
    check(
        "equal slot counts cannot hide duplicate div and input identities",
        duplicate_slot_identity["holds"] is False
        and duplicate_slot_identity["layout_mismatches"] == 0
        and duplicate_slot_offender["printed"] == 2
        and duplicate_slot_offender["slots"] == 2
        and duplicate_slot_offender["emission_state"] == "duplicate-slot-index",
    )

    unordered_slots = check_comb_slots_match_printed(
        CombEmissionFixture(
            [emitted_comb_cell(
                slot_indexes=(1, 0), input_indexes=(1, 0), declared=2)],
            count=2,
        ))
    check(
        "physical slot indexes must be ordered exactly zero through N minus one",
        unordered_slots["holds"] is False
        and unordered_slots["offenders"][0]["emission_state"]
        == "invalid-slot-index-sequence",
    )

    wrong_owner = check_comb_slots_match_printed(
        CombEmissionFixture(
            [emitted_comb_cell(
                slot_indexes=(0, 1), input_indexes=(0, 0), declared=2)],
            count=2,
        ))
    check(
        "each emitted input index must identify its owning physical slot",
        wrong_owner["holds"] is False
        and wrong_owner["offenders"][0]["emission_state"]
        == "slot-input-index-mismatch",
    )

    out_of_range_owner = check_comb_slots_match_printed(
        CombEmissionFixture(
            [emitted_comb_cell(
                slot_indexes=(0, 1), input_indexes=(0, 7), declared=2)],
            count=2,
        ))
    check(
        "an input index outside the comb's own compartments fails",
        out_of_range_owner["holds"] is False
        and out_of_range_owner["offenders"][0]["emission_state"]
        == "slot-input-index-mismatch"
        and "'input_slot_index': 7"
        in out_of_range_owner["offenders"][0]["why"],
    )

    # The assertion's first question, and the one an excused compartment must
    # never be able to answer for the comb: a comb that emits fewer boxes than
    # the sheet prints fails on the count, whatever is or is not inside them.
    short_comb = check_comb_slots_match_printed(
        CombEmissionFixture(
            [emitted_comb_cell(
                slot_indexes=(0, 1), declared=2,
                geometry=((0.0, 0.0, 15.0, 10.0),
                          (15.0, 0.0, 15.0, 10.0)))],
            count=3,
        ))
    short_offender = first_offender(short_comb)
    check(
        "a comb short of the printed compartment count still fails on the "
        "count",
        short_comb["holds"] is False
        and short_offender["printed"] == 3
        and short_offender["slots"] == 2
        and "emission-printed-mismatch" in short_offender["failure_kinds"]
        and "emission-layout-mismatch" in short_offender["failure_kinds"],
    )

    zero_width_slot = check_comb_slots_match_printed(
        CombEmissionFixture(
            [emitted_comb_cell(
                slot_indexes=(0, 1),
                geometry=((0.0, 0.0, 0.0, 10.0),
                          (0.0, 0.0, 10.0, 10.0)))],
            count=2,
        ))
    check(
        "a physical slot count cannot hide a zero-width slot",
        zero_width_slot["holds"] is False
        and zero_width_slot["layout_mismatches"] == 0
        and zero_width_slot["offenders"][0]["emission_state"]
        == "invalid-slot-geometry",
    )

    overlapping_slots = check_comb_slots_match_printed(
        CombEmissionFixture(
            [emitted_comb_cell(
                slot_indexes=(0, 1),
                geometry=((0.0, 0.0, 10.0, 10.0),
                          (0.0, 0.0, 10.0, 10.0)))],
            count=2,
        ))
    check(
        "distinct indexes cannot occupy the same physical slot box",
        overlapping_slots["holds"] is False
        and overlapping_slots["layout_mismatches"] == 0
        and overlapping_slots["offenders"][0]["emission_state"]
        == "invalid-slot-geometry",
    )

    unexpected = check_comb_slots_match_printed(
        CombEmissionFixture([
            valid_three,
            emitted_comb_cell("p1c9"),
        ]))
    unexpected_offender = next(
        item for item in unexpected["offenders"] if item["cell"] == "p1c9")
    check(
        "comb-marked emitted cells require a non-relocated layout owner",
        unexpected["holds"] is False
        and unexpected["expected_comb_ids"] == ["p1c0"]
        and unexpected["checked_comb_ids"] == ["p1c0"]
        and unexpected["emitted_comb_ids"] == ["p1c0", "p1c9"]
        and unexpected["unexpected_emitted_comb_ids"] == ["p1c9"]
        and unexpected_offender["layout_relation"] == "not-owned"
        and unexpected_offender["failure_kinds"]
        == ["unexpected-emitted-comb"],
    )

    class EmptyCombInventoryFixture:
        doc = object()
        relocated_cells: set[str] = set()
        vector_pages: dict[int, VectorPage] = {}

        def __init__(self, cells: Sequence[Cell], *,
                     stats_comb_cells: int | None = None) -> None:
            self.cells = list(cells)
            page: dict[str, Any] = {
                "index": 1,
                "cells": [],
                "comb_subjects": [],
            }
            if stats_comb_cells is not None:
                page["stats"] = {"comb_cells": stats_comb_cells}
            self.layout_pages = {1: page}
            self.layout = {"pages": [page]}
            self.layout_payload = json.dumps(
                self.layout, sort_keys=True, separators=(",", ":"),
            ).encode("utf-8")
            self.layout_sha256 = hashlib.sha256(
                self.layout_payload).hexdigest()

    valid_pure_empty = check_comb_slots_match_printed(
        EmptyCombInventoryFixture([]))
    check(
        "a valid hash-bound pure-empty comb inventory remains held",
        valid_pure_empty["holds"] is True
        and valid_pure_empty["inventory_complete"] is True
        and valid_pure_empty["combs_expected"] == 0
        and valid_pure_empty["combs_checked"] == 0
        and valid_pure_empty["owner_certificates_valid"] == 0
        and valid_pure_empty["owner_certificates_invalid"] == 0
        and valid_pure_empty["offenders"] == [],
    )

    emission_only_inventory = check_comb_slots_match_printed(
        EmptyCombInventoryFixture([emitted_comb_cell()]))
    check(
        "an empty layout inventory cannot vacuously own an emitted comb",
        emission_only_inventory["holds"] is False
        and emission_only_inventory["expected_comb_ids"] == []
        and emission_only_inventory["checked_comb_ids"] == []
        and emission_only_inventory["emitted_comb_ids"] == ["p1c0"]
        and emission_only_inventory["offenders"][0]["layout_relation"]
        == "not-owned",
    )
    stats_only_inventory = check_comb_slots_match_printed(
        EmptyCombInventoryFixture([], stats_comb_cells=1))
    check(
        "positive layout statistics make an empty comb inventory fail closed",
        stats_only_inventory["holds"] is False
        and stats_only_inventory["offender_count"] == 1
        and stats_only_inventory["offenders"][0]["cell"]
        == "<comb-inventory>"
        and stats_only_inventory["inventory_complete"] is False,
    )

    def bound_comb_inventory_fixture(
            layout: dict[str, Any],
            *,
            cells: Sequence[Cell] = (),
            relocated_cells: Iterable[str] = (),
            ) -> Any:
        payload = json.dumps(
            layout, sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")
        return types.SimpleNamespace(
            layout=layout,
            layout_payload=payload,
            layout_sha256=hashlib.sha256(payload).hexdigest(),
            layout_pages={page["index"]: page for page in layout["pages"]},
            doc=object(),
            cells=list(cells),
            relocated_cells=set(relocated_cells),
            vector_pages={},
        )

    def retained_subject(*, corrupt_emission: bool) -> dict[str, Any]:
        return {
            "subject_key": "p1@40,0,50,10",
            "legacy_cell_id": "p1c1",
            "legacy_bbox": [40.0, 0.0, 50.0, 10.0],
            "cell_id": None,
            "mapped_partition_cell_ids": ["p1c1"],
            "mapped_partition_subject_keys": ["p1@40,0,50,10"],
            "state": "retained_unresolved",
            "emission": "emitted" if corrupt_emission else "suppressed",
            "reason_codes": [
                "emission-suppressed-no-final-visible-band"],
            "legacy_comb": {},
            "requires_independent_evidence": True,
            "permitted_transitions": [
                "active_composite", "retired_proven_false"],
            "blocks_gate": True,
        }

    retained_cell = {
        "id": "p1c1",
        "subject_key": "p1@40,0,50,10",
        "x0": 40.0, "y0": 0.0, "x1": 50.0, "y1": 10.0,
    }
    corrupt_pure_empty_layout = {"pages": [{
        "index": 1,
        "cells": [copy.deepcopy(retained_cell)],
        "comb_subjects": [retained_subject(corrupt_emission=True)],
    }]}
    corrupt_pure_empty = check_comb_slots_match_printed(
        bound_comb_inventory_fixture(corrupt_pure_empty_layout))
    corrupt_registry_offender = corrupt_pure_empty["offenders"][0]
    check(
        "corrupt retained-only inventory fails with complete registry evidence",
        corrupt_pure_empty["holds"] is False
        and corrupt_pure_empty["inventory_complete"] is False
        and corrupt_pure_empty["combs_expected"] == 0
        and corrupt_pure_empty["combs_checked"] == 0
        and corrupt_pure_empty["owner_certificates_valid"] == 0
        and corrupt_pure_empty["owner_certificates_invalid"] == 0
        and corrupt_pure_empty["offender_count"] == 1
        and corrupt_pure_empty["offenders_complete"] is True
        and corrupt_registry_offender["cell"] == "<comb-owner-registry>"
        and corrupt_registry_offender["failure_kinds"]
        == ["comb-owner-registry-invalid"]
        and corrupt_registry_offender["source_owner_certificate"]["valid"]
        is False
        and "suppression/blocking/transition"
        in corrupt_registry_offender["source_owner_certificate"]["reason"],
    )

    active_relocated_cell = {
            "id": "p1c0", "x0": 0.0, "y0": 0.0,
            "x1": 40.0, "y1": 10.0,
            "subject_key": "p1@0,0,40,10",
            "comb": {"cells": 3},
    }
    active_relocated_subject = {
        "subject_key": "p1@0,0,40,10",
        "legacy_cell_id": "p1c0",
        "legacy_bbox": [0.0, 0.0, 40.0, 10.0],
        "cell_id": "p1c0",
        "mapped_partition_cell_ids": ["p1c0"],
        "state": "active_unresolved",
        "reason_codes": ["competing-endpoint-topologies"],
        "cells": 3,
        "blocks_gate": True,
    }
    valid_relocated_layout = {"pages": [{
        "index": 1,
        "cells": [copy.deepcopy(active_relocated_cell)],
        "comb_subjects": [copy.deepcopy(active_relocated_subject)],
    }]}

    relocated_live = check_comb_slots_match_printed(
        bound_comb_inventory_fixture(
            valid_relocated_layout,
            cells=[valid_three],
            relocated_cells={"p1c0"},
        ))
    check(
        "a relocated comb left live in form HTML fails as stale markup",
        relocated_live["holds"] is False
        and relocated_live["expected_comb_ids"] == []
        and relocated_live["unexpected_emitted_comb_ids"] == ["p1c0"]
        and relocated_live["offenders"][0]["failure_kinds"]
        == ["unexpected-emitted-comb"],
    )

    corrupt_relocated_layout = copy.deepcopy(valid_relocated_layout)
    corrupt_relocated_page = corrupt_relocated_layout["pages"][0]
    corrupt_relocated_page["cells"].append(copy.deepcopy(retained_cell))
    corrupt_relocated_page["comb_subjects"].append(
        retained_subject(corrupt_emission=True))
    corrupt_relocated = check_comb_slots_match_printed(
        bound_comb_inventory_fixture(
            corrupt_relocated_layout,
            relocated_cells={"p1c0"},
        ))
    check(
        "relocated active comb cannot hide a corrupt retained ledger tail",
        corrupt_relocated["holds"] is False
        and corrupt_relocated["expected_comb_ids"] == []
        and corrupt_relocated["checked_comb_ids"] == []
        and corrupt_relocated["owner_certificates_valid"] == 0
        and corrupt_relocated["owner_certificates_invalid"] == 0
        and corrupt_relocated["inventory_complete"] is False
        and corrupt_relocated["offender_count"] == 1
        and corrupt_relocated["offenders"][0]["cell"]
        == "<comb-owner-registry>"
        and corrupt_relocated["offenders"][0]["failure_kinds"]
        == ["comb-owner-registry-invalid"],
    )

    # W8: the reviewed-topology registry. Every check above already proves
    # "no entry -> stays unevaluable exactly as today", because none of them
    # registers anything in `REVIEWED_COMB_TOPOLOGY`, which ships EMPTY. These
    # tests inject synthetic entries directly and remove them again in a
    # `finally`, so the module returns to its shipped-empty state once
    # self-test finishes, whether a check below passes or fails.
    review_fixture = source_filled_fixture(
        glyphs=(SourceGlyph("0", 13.0, 2.0, 17.0, 8.0),), paints=())
    review_fixture.slug = "w8-review-fixture"
    review_fixture.ir = {"source": {"sha256": "b" * 64}}
    review_key = ("w8-review-fixture", 1, "p1c0")
    review_bbox = [0.0, 0.0, 30.0, 10.0]

    def complete_review_entry(**over: Any) -> dict[str, Any]:
        entry = {
            "compartments": 3,
            "source_sha256": "b" * 64,
            "page": 1,
            "cell_id": "p1c0",
            "bbox": list(review_bbox),
            "reviewer": "uriah",
            "date": "2026-08-13",
            "citation": "W8 self-test synthetic fixture, not a real review",
        }
        entry.update(over)
        return entry

    REVIEWED_COMB_TOPOLOGY[review_key] = complete_review_entry()
    try:
        decided_result = check_comb_slots_match_printed(review_fixture)
    finally:
        del REVIEWED_COMB_TOPOLOGY[review_key]
    decided_subject = (
        decided_result["decided_by_review_subjects"][0]
        if decided_result.get("decided_by_review_subjects") else {}
    )
    check(
        "a valid reviewed entry decides an otherwise-unevaluable subject",
        decided_result["holds"] is True
        and decided_result["layout_unevaluable"] == 0
        and decided_result["decided_by_review"] == 1
        and decided_subject.get("cell") == "p1c0"
        and decided_subject.get("printed") == 3
        and decided_subject.get("latticed") == 3
        and decided_subject.get("reviewed_comb_topology", {}).get(
            "compartments") == 3
        and decided_subject.get("reviewed_comb_topology", {}).get(
            "reviewer") == "uriah",
    )

    decidable_fixture = CombEmissionFixture([valid_three])
    decidable_fixture.slug = "w8-decidable-fixture"
    decidable_key = ("w8-decidable-fixture", 1, "p1c0")
    REVIEWED_COMB_TOPOLOGY[decidable_key] = complete_review_entry()
    try:
        decidable_result = check_comb_slots_match_printed(decidable_fixture)
    finally:
        del REVIEWED_COMB_TOPOLOGY[decidable_key]
    decidable_offender = first_offender(decidable_result)
    check(
        "a reviewed entry for a subject the audit can already decide is an "
        "ERROR -- the load-bearing guard against overruling a real "
        "disagreement",
        decidable_result["holds"] is False
        and "reviewed-comb-topology-invalid"
        in decidable_offender.get("failure_kinds", ())
        and "independently decidable" in decidable_offender.get("why", ""),
    )

    REVIEWED_COMB_TOPOLOGY[review_key] = complete_review_entry(
        source_sha256="c" * 64)
    try:
        mismatched_sha_result = check_comb_slots_match_printed(review_fixture)
    finally:
        del REVIEWED_COMB_TOPOLOGY[review_key]
    mismatched_sha_offender = first_offender(mismatched_sha_result)
    check(
        "a reviewed entry whose sha256 does not match the current source is "
        "an ERROR, not a stale-but-usable fact",
        mismatched_sha_result["holds"] is False
        and mismatched_sha_result["decided_by_review"] == 0
        and "reviewed-comb-topology-invalid"
        in mismatched_sha_offender.get("failure_kinds", ())
        and "source-topology-unevaluable"
        in mismatched_sha_offender.get("failure_kinds", ())
        and "does not match the current IR's source.sha256"
        in mismatched_sha_offender.get("why", ""),
    )

    incomplete_entry = complete_review_entry()
    del incomplete_entry["citation"]
    REVIEWED_COMB_TOPOLOGY[review_key] = incomplete_entry
    try:
        missing_field_result = check_comb_slots_match_printed(review_fixture)
    finally:
        del REVIEWED_COMB_TOPOLOGY[review_key]
    missing_field_offender = first_offender(missing_field_result)
    check(
        "a reviewed entry missing a required field is an ERROR, not a "
        "skipped entry",
        missing_field_result["holds"] is False
        and missing_field_result["decided_by_review"] == 0
        and "reviewed-comb-topology-invalid"
        in missing_field_offender.get("failure_kinds", ())
        and "missing required field(s): citation"
        in missing_field_offender.get("why", ""),
    )

    unregistered_result = check_comb_slots_match_printed(review_fixture)
    unregistered_offender = first_offender(unregistered_result)
    check(
        "no reviewed entry leaves a genuinely unevaluable subject exactly "
        "where it was",
        review_key not in REVIEWED_COMB_TOPOLOGY
        and unregistered_result["holds"] is False
        and unregistered_result["decided_by_review"] == 0
        and unregistered_offender.get("failure_kinds")
        == ["source-topology-unevaluable"],
    )

    # Geometry helpers, where an off-by-one epsilon would silently disable an
    # assertion rather than break it.
    check("touching edges do not overlap", not overlaps((0, 0, 10, 10), (10, 0, 20, 10)))
    check("shared area overlaps", overlaps((0, 0, 10, 10), (9, 0, 20, 10)))
    check("whitespace carries no ink",
          len(glyph_boxes({"text": "a b", "x0": 0.0, "y0": 0.0, "x1": 9.0, "y1": 8.0,
                           "origin_x": 0.0,
                           "char_origin_offsets_pt": [0.0, 3.0, 6.0],
                           "char_widths_pt": [3.0, 3.0, 3.0]})) == 2)

    # The ink band. A run of "Dg" set 10pt on a baseline at y=100 in a face
    # whose ascender is 0.9 and descender -0.2: the line box is 91..102, the
    # 'D' stops on the baseline plus the measured overshoot, and the 'g'
    # keeps the whole line box because its ink really does go there.
    def ink_run(text: str, **over: Any) -> dict:
        offsets = [3.0 * i for i in range(len(text))]
        run = {
            "text": text, "family": "Arial", "size_pt": 10.0,
            "x0": 0.0, "y0": 91.0, "x1": 3.0 * len(text), "y1": 102.0,
            "origin_x": 0.0, "baseline_y": 100.0,
            "ascender": 0.9, "descender": -0.2, "rotated": False,
            "bold": False, "italic": False,
            "char_origin_offsets_pt": offsets,
            "char_widths_pt": [3.0] * len(text),
        }
        run.update(over)
        return run

    seated = 100.0 + GLYPH_BASELINE_OVERSHOOT_EM * 10.0
    mixed = glyph_boxes(ink_run("Dg"))
    check("a baseline-seated glyph's ink stops at the baseline, not the descent",
          len(mixed) == 2 and abs(mixed[0][3] - seated) < 1e-9)
    check("a descending glyph in the same run keeps the whole line box",
          len(mixed) == 2 and mixed[1][3] == 102.0)
    check("the ink band never rises above the run's own ascent line",
          all(box[1] == 91.0 for box in mixed))
    check("a character with no measured ink depth keeps the line box",
          glyph_boxes(ink_run("\uf0a7"))[0][3] == 102.0
          and glyph_boxes(ink_run("\ufffd"))[0][3] == 102.0)
    check("a symbol-encoded face cannot be read through a character table",
          glyph_boxes(ink_run("D", family="Wingdings"))[0][3] == 102.0)
    check("a rotated run has no horizontal baseline to seat ink on",
          glyph_boxes(ink_run("D", rotated=True))[0][3] == 102.0)
    check("a run without a baseline or size falls back to the line box",
          glyph_boxes(ink_run("D", baseline_y=None))[0][3] == 102.0
          and glyph_boxes(ink_run("D", size_pt=0.0))[0][3] == 102.0)
    check("f descends in an italic face and not in an upright one",
          glyph_boxes(ink_run("f"))[0][3] == seated
          and glyph_boxes(ink_run("f", italic=True))[0][3] == 102.0)
    check("a baseline overshoot is carried, not rounded away",
          GLYPH_BASELINE_OVERSHOOT_EM * 10.0 > OVERLAP_EPS_PT)

    # The measured path. The same run, now with the source's own outline for
    # `D`: Helvetica states 0.077..0.723 em across and 0.0..0.729 em up, so at
    # 10pt on a baseline of 100 the ink is x 0.77..7.23 and y 92.71..100.0 --
    # inside the 0..3 advance box on neither edge, and 8.31pt short of the
    # 102.0 the line box charged it with.
    measured = glyph_boxes(ink_run("D", glyph_ink_em={
        "D": [0.077, 0.0, 0.723, 0.729]}))
    check("a stated outline is used on all four edges",
          len(measured) == 1
          and all(abs(got - want) < 1e-9 for got, want
                  in zip(measured[0], (0.77, 92.71, 7.23, 100.0))))
    check("a glyph the run states no outline for keeps the advance box",
          glyph_boxes(ink_run("Dg", glyph_ink_em={
              "D": [0.077, 0.0, 0.723, 0.729]}))[1] == (3.0, 91.0, 6.0, 102.0))
    check("an outline is never applied to a run whose placement is not published",
          glyph_boxes(ink_run("D", rotated=True, glyph_ink_em={
              "D": [0.077, 0.0, 0.723, 0.729]}))[0] == (0.0, 91.0, 3.0, 102.0)
          and glyph_boxes(ink_run("D", size_pt=0.0, glyph_ink_em={
              "D": [0.077, 0.0, 0.723, 0.729]}))[0] == (0.0, 91.0, 3.0, 102.0)
          and glyph_boxes(ink_run("D", baseline_y=None, glyph_ink_em={
              "D": [0.077, 0.0, 0.723, 0.729]}))[0] == (0.0, 91.0, 3.0, 102.0))
    check("a malformed outline is refused rather than read as no collision",
          all(glyph_boxes(ink_run("D", glyph_ink_em=table))[0]
              == (0.0, 91.0, 3.0, seated)
              for table in ({"D": [0.7, 0.0, 0.7, 0.729]},
                            {"D": [0.077, 0.729, 0.723, 0.0]},
                            {"D": [0.077, 0.0, 0.723]},
                            {"D": [0.077, 0.0, 0.723, float("inf")]},
                            {"D": [0.077, 0.0, 0.723, True]},
                            {"D": "0.077 0 0.723 0.729"},
                            {"D": None})))
    check("an outline table that is not a table is not read",
          glyph_boxes(ink_run("D", glyph_ink_em=[0.077, 0.0, 0.723, 0.729]))[0]
          == (0.0, 91.0, 3.0, seated))
    check("a stated outline overrules the character table, both ways",
          # `g` descends, so the fallback would give it the line box; the
          # source says its ink stops 0.218 em under the baseline instead.
          abs(glyph_boxes(ink_run("g", glyph_ink_em={
              "g": [0.035, -0.218, 0.481, 0.539]}))[0][3] - 102.18) < 1e-9
          # and `D` is baseline-seated, so the fallback would stop it at the
          # overshoot; the source says it reaches 0.06 em under.
          and abs(glyph_boxes(ink_run("D", glyph_ink_em={
              "D": [0.077, -0.06, 0.723, 0.729]}))[0][3] - 100.6) < 1e-9)
    check("every character measured as baseline-seated is measured only once",
          not (DESCENDING_INK & BASELINE_SEATED_INK)
          and ITALIC_ONLY_DESCENDING_INK <= BASELINE_SEATED_INK
          and not (ITALIC_ONLY_DESCENDING_INK & DESCENDING_INK))
    check("an upright placement needs no SVG transform",
          transform_signature((10.0, 0.0, 0.0, 10.0, 0.0, 0.0)) == svg_signature(None))
    check("a y-flipped placement needs a negative y scale",
          transform_signature((10.0, 0.0, -0.0, -10.0, 0.0, 0.0))
          == svg_signature("translate(5,5) scale(1,-1)"))
    check("arithmetic noise is not a shear",
          transform_signature((41.0, 5.3e-05, 1.7e-06, 33.8, 0.0, 0.0))
          == (1, 1, False))
    check(
        "topology subset matching cannot reuse one superset divider twice",
        not _topology_subset((10.0, 10.1), (10.0, 20.0, 30.0)),
    )
    check(
        "topology subset matching accepts near-identical one-to-one dividers",
        _topology_subset((10.1, 20.1), (10.0, 20.0, 30.0)),
    )

    def source_paint(x: float, a: float = 2.0, b: float = 8.0,
                     *, width: float = 0.24, tone: float = 0.0,
                     order: int = 0) -> VectorPaint:
        return VectorPaint(x - width / 2, a, x + width / 2, b,
                           tone, 1.0, order, "test")

    def source_page(
            *paints: VectorPaint, framed: bool = True,
            ) -> VectorPage:
        paint_list = list(paints)
        if framed:
            vertical_paints = [
                paint for paint in paint_list if _is_comb_vertical(paint)]
            centers = sorted({
                round((paint.x0 + paint.x1) / 2.0, 6)
                for paint in vertical_paints
            })
            pitches = [
                right - left for left, right in zip(centers, centers[1:])
                if right - left > COMB_MERGE_PT
            ]
            pitch = statistics.median(pitches) if pitches else 10.0
            right_rail = max(
                40.0,
                (centers[-1] + 2.0 * pitch) if centers else 40.0,
            )
            tone_counts = collections.Counter(
                round(paint.tone, 6) for paint in vertical_paints)
            frame_tone = (
                min(
                    tone for tone, count in tone_counts.items()
                    if count == max(tone_counts.values()))
                if tone_counts else 0.0
            )
            frame_verticals = [
                paint for paint in vertical_paints
                if abs(paint.tone - frame_tone) <= SOURCE_COORD_EPS_PT
            ]
            longest = max(
                frame_verticals,
                key=lambda paint: paint.y1 - paint.y0,
                default=None,
            )
            frame_y0 = longest.y0 if longest is not None else 2.0
            frame_y1 = longest.y1 if longest is not None else 8.0
            last_order = max((paint.order for paint in paint_list), default=0)
            paint_list.extend((
                source_paint(
                    0.0, a=frame_y0, b=frame_y1,
                    order=last_order + 1, tone=frame_tone),
                source_paint(
                    right_rail, a=frame_y0, b=frame_y1,
                    order=last_order + 2, tone=frame_tone),
                VectorPaint(
                    0.0, frame_y1 - 0.12, right_rail, frame_y1 + 0.12,
                    frame_tone, 1.0, last_order + 3, "test-source-frame"),
            ))
        ordered = sorted(
            paint_list,
            key=lambda paint: (
                paint.order, paint.kind, paint.x0, paint.y0,
                paint.x1, paint.y1),
        )
        return VectorPage(tuple(
            dataclasses.replace(paint, operation=index)
            if paint.operation < 0 else paint
            for index, paint in enumerate(ordered)
        ), ())

    def owned_test_page(page: VectorPage) -> VectorPage:
        framed = source_page(*page.paints)
        return VectorPage(framed.paints, page.unsupported)

    class PoisonComb:
        def __getitem__(self, key: str) -> Any:
            raise AssertionError(f"source oracle read poisoned comb key {key!r}")

        def get(self, key: str, default: Any = None) -> Any:
            raise AssertionError(f"source oracle read poisoned comb key {key!r}")

    def comb_subject(*, x0: float = 0.0, x1: float = 40.0,
                     cell_y0: float = 0.0, cell_y1: float = 10.0,
                     ) -> dict[str, Any]:
        return {
            "id": "p1c0", "x0": x0, "y0": cell_y0,
            "x1": x1, "y1": cell_y1,
            "subject_key": (
                f"p1@{x0:.2f},{cell_y0:.2f},{x1:.2f},{cell_y1:.2f}"),
            # Every field -- cells, divider_x, y0, y1 and divider_gray -- is
            # poison. The source oracle must never inspect this mapping.
            "comb": PoisonComb(),
        }

    def owner_registry_fixture(
            cell: dict[str, Any] | None = None,
            *,
            subject_updates: dict[str, Any] | None = None,
            missing: bool = False,
            duplicate: bool = False,
            stale_parsed_layout: bool = False,
            stale_digest: bool = False,
            layout_mutator: Any = None,
            ) -> tuple[
                CombOwnerRegistry,
                dict[str, Any],
                CombOwnerCertificate | None,
                str | None,
            ]:
        source_cell = cell or comb_subject()
        identity_cell = {
            "id": source_cell["id"],
            "subject_key": source_cell["subject_key"],
            **{key: source_cell[key] for key in ("x0", "y0", "x1", "y1")},
            # Deliberately false topology: a valid identity certificate must
            # still let the source's two dividers, not this value, decide.
            "comb": {
                "cells": 999,
                "divider_x": [-999.0],
                "slot_x": [-1000.0, 1000.0],
                "y0": -999.0,
                "y1": 999.0,
                "divider_gray": 1.0,
            },
        }
        subject = {
            "subject_key": identity_cell["subject_key"],
            "legacy_cell_id": identity_cell["id"],
            "legacy_bbox": [
                identity_cell[key] for key in ("x0", "y0", "x1", "y1")
            ],
            "cell_id": identity_cell["id"],
            "mapped_partition_cell_ids": [identity_cell["id"]],
            "state": "active_unresolved",
            "reason_codes": ["competing-endpoint-topologies"],
            "cells": 999,
            "blocks_gate": True,
        }
        if subject_updates:
            subject.update(copy.deepcopy(subject_updates))
        subjects = [] if missing else [subject]
        if duplicate:
            subjects.append(copy.deepcopy(subject))
        layout_fixture = {
            "pages": [{
                "index": 1,
                "cells": [identity_cell],
                "comb_subjects": subjects,
            }],
        }
        if layout_mutator is not None:
            layout_mutator(layout_fixture)
        payload = json.dumps(
            layout_fixture, sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")
        parsed_layout = copy.deepcopy(layout_fixture)
        if stale_parsed_layout:
            parsed_layout["pages"][0]["cells"][0]["x1"] += 1.0
        digest = (
            "0" * 64 if stale_digest else hashlib.sha256(payload).hexdigest()
        )
        registry = reviewed_comb_owner_registry(types.SimpleNamespace(
            layout=parsed_layout,
            layout_payload=payload,
            layout_sha256=digest,
        ))
        certificate, reason = registry.resolve(1, identity_cell)
        return registry, identity_cell, certificate, reason

    valid_registry, valid_owner_cell, valid_owner, valid_owner_reason = (
        owner_registry_fixture())
    check(
        "one exact reviewed hash-bound comb_subject certifies owner identity",
        valid_owner is not None
        and valid_owner_reason is None
        and valid_registry.binding_error is None,
    )
    if valid_owner is not None:
        certificate_evidence = valid_owner.evidence()
        certified_unframed = printed_compartments(
            source_page(
                source_paint(10), source_paint(20), framed=False),
            valid_owner_cell,
            include_frame=True,
            owner_certificate=valid_owner,
        )
        check(
            "reviewed ownership admits only the unanimous source topology",
            certified_unframed == (3, [10.0, 20.0], None),
        )
        check(
            "ownership certificate publishes deterministic identity-only evidence",
            certificate_evidence == valid_owner.evidence()
            and json.dumps(certificate_evidence, sort_keys=True)
            == json.dumps(valid_owner.evidence(), sort_keys=True)
            and certificate_evidence["supplies_topology"] is False
            and not ({"cells", "comb", "divider_x", "slot_x", "y0", "y1",
                      "divider_gray"} & set(certificate_evidence)),
        )

    missing_registry = owner_registry_fixture(missing=True)
    duplicate_registry = owner_registry_fixture(duplicate=True)
    bbox_registry = owner_registry_fixture(subject_updates={
        "legacy_bbox": [0.0, 0.0, 41.0, 10.0],
    })
    malformed_retained_state_registry = owner_registry_fixture(subject_updates={
        "state": "retained_unresolved",
        "cell_id": None,
    })
    stale_layout_registry = owner_registry_fixture(stale_parsed_layout=True)
    stale_digest_registry = owner_registry_fixture(stale_digest=True)
    for label, fixture, phrase in (
            ("missing", missing_registry, "no reviewed active"),
            ("duplicate", duplicate_registry, "duplicate comb_subject"),
            ("mismatched bbox", bbox_registry, "exact bbox"),
            ("mismatched state", malformed_retained_state_registry,
             "schema is malformed"),
            ("stale parsed layout", stale_layout_registry, "stale"),
            ("stale layout digest", stale_digest_registry, "SHA-256")):
        _registry, _cell, certificate, reason = fixture
        check(
            f"{label} comb_subject ownership certificate fails closed",
            certificate is None
            and reason is not None
            and phrase in reason,
        )

    def append_orphan_active(layout_fixture: dict[str, Any]) -> None:
        layout_fixture["pages"][0]["comb_subjects"].append({
            "subject_key": "p1@50.00,0.00,60.00,10.00",
            "legacy_cell_id": "p1c9",
            "legacy_bbox": [50.0, 0.0, 60.0, 10.0],
            "cell_id": "p1c9",
            "mapped_partition_cell_ids": ["p1c9"],
            "state": "active_unresolved",
            "reason_codes": ["competing-endpoint-topologies"],
            "blocks_gate": True,
        })

    def append_competing_active(layout_fixture: dict[str, Any]) -> None:
        layout_fixture["pages"][0]["comb_subjects"].append({
            "subject_key": "p1@1.00,0.00,39.00,10.00",
            "legacy_cell_id": "p1c9",
            "legacy_bbox": [1.0, 0.0, 39.0, 10.0],
            "cell_id": "p1c0",
            "mapped_partition_cell_ids": ["p1c0"],
            "state": "active_resolved",
        })

    orphan_active_registry = owner_registry_fixture(
        layout_mutator=append_orphan_active)
    competing_active_registry = owner_registry_fixture(
        layout_mutator=append_competing_active)
    unknown_state_registry = owner_registry_fixture(subject_updates={
        "state": "active_future",
    })
    bool_page_registry = owner_registry_fixture(
        layout_mutator=lambda value: value["pages"][0].__setitem__(
            "index", True))
    bool_coordinate_registry = owner_registry_fixture(
        layout_mutator=lambda value: value["pages"][0]["cells"][0].__setitem__(
            "x0", True))
    for label, fixture, phrase in (
            ("orphan active", orphan_active_registry, "orphaned"),
            ("competing active bbox/subject", competing_active_registry,
             "mapping is not unique"),
            ("unknown active state", unknown_state_registry, "unknown state"),
            ("boolean page", bool_page_registry, "exhaustive and ordered"),
            ("boolean coordinate", bool_coordinate_registry, "four-number")):
        _registry, _cell, certificate, reason = fixture
        check(
            f"{label} invalidates the exhaustive ownership registry",
            certificate is None and reason is not None and phrase in reason,
        )
    _DROP = object()

    def append_valid_retained(layout_fixture: dict[str, Any]) -> None:
        page = layout_fixture["pages"][0]
        retained_cell = {
            "id": "p1c1",
            "subject_key": "p1@50.00,0.00,60.00,10.00",
            "x0": 50.0, "y0": 0.0, "x1": 60.0, "y1": 10.0,
        }
        page["cells"].append(retained_cell)
        page["comb_subjects"].append({
            "subject_key": retained_cell["subject_key"],
            "legacy_cell_id": retained_cell["id"],
            "legacy_bbox": [50.0, 0.0, 60.0, 10.0],
            "cell_id": None,
            "mapped_partition_cell_ids": [retained_cell["id"]],
            "mapped_partition_subject_keys": [retained_cell["subject_key"]],
            "state": "retained_unresolved",
            "emission": "suppressed",
            "reason_codes": [
                "emission-suppressed-no-final-visible-band"],
            # Presence is schema evidence only; the ownership registry never
            # reads retained topology.
            "legacy_comb": {},
            "requires_independent_evidence": True,
            "permitted_transitions": [
                "active_composite", "retired_proven_false"],
            "blocks_gate": True,
        })

    retained_registry_fixture = owner_registry_fixture(
        layout_mutator=append_valid_retained)
    retained_registry = retained_registry_fixture[0]
    retained_cell = {
        "id": "p1c1",
        "subject_key": "p1@50.00,0.00,60.00,10.00",
        "x0": 50.0, "y0": 0.0, "x1": 60.0, "y1": 10.0,
    }
    retained_certificate, retained_reason = retained_registry.resolve(
        1, retained_cell)
    check(
        "valid retained_unresolved evidence does not invalidate active owners",
        retained_registry_fixture[2] is not None
        and retained_registry.binding_error is None,
    )
    check(
        "retained_unresolved subject is allowed but cannot certify a cell",
        retained_certificate is None
        and retained_reason is not None
        and "no exact unique reviewed" in retained_reason,
    )

    # ---- REVIEW BUNDLE: the composite arrival, and its own corruptions ----
    #
    # A composite is the retained shape plus a reviewer's certificate, minus
    # the blocking. Both differences are asserted here, and each is proven
    # load-bearing by its own corruption below -- without these fixtures the
    # certificate requirement and the certificate schema were dead code that
    # could be deleted with no test noticing, which is exactly what the
    # neuter-proof reported before they were written.
    COMPOSITE_CERT = {
        "criterion": "reviewed-ledger-transition-v1",
        "registry_key": ["fixture-1999", 1, "p1c1"],
        "transition": "active_composite",
        "suppression_criterion": "source-partition-edge-in-final-picture-v1",
        "reviewer": "fixture-reviewer", "date": "2026-08-15",
    }

    def append_valid_composite(layout_fixture: dict[str, Any]) -> None:
        append_valid_retained(layout_fixture)
        subject = layout_fixture["pages"][0]["comb_subjects"][-1]
        subject["state"] = "active_composite"
        subject["blocks_gate"] = False
        subject["transition_certificate"] = dict(COMPOSITE_CERT)

    composite_fixture = owner_registry_fixture(
        layout_mutator=append_valid_composite)
    composite_registry = composite_fixture[0]
    composite_certificate, composite_reason = composite_registry.resolve(
        1, retained_cell)
    check(
        "a reviewed composite does not invalidate the ownership registry",
        composite_fixture[2] is not None
        and composite_registry.binding_error is None,
    )
    check(
        "a composite subject still cannot certify a cell of its own",
        composite_certificate is None
        and composite_reason is not None
        and "no exact unique reviewed" in composite_reason,
    )

    def corrupt_composite(field: str, value: Any) -> Any:
        def mutate(layout_fixture: dict[str, Any]) -> None:
            append_valid_composite(layout_fixture)
            subject = layout_fixture["pages"][0]["comb_subjects"][-1]
            if value is _DROP:
                subject.pop(field, None)
            else:
                subject[field] = value
        return mutate

    for label, mutator, phrase in (
        ("a composite with NO certificate",
         corrupt_composite("transition_certificate", _DROP),
         "schema is malformed"),
        ("a composite whose certificate is not a dict",
         corrupt_composite("transition_certificate", "reviewed"),
         "transition certificate is malformed"),
        ("a composite whose certificate has an unknown field",
         corrupt_composite("transition_certificate",
                           {**COMPOSITE_CERT, "approved": True}),
         "transition certificate is malformed"),
        ("a composite whose certificate is missing a field",
         corrupt_composite("transition_certificate",
                           {k: v for k, v in COMPOSITE_CERT.items()
                            if k != "reviewer"}),
         "transition certificate is malformed"),
        ("a composite whose certificate names another transition",
         corrupt_composite("transition_certificate",
                           {**COMPOSITE_CERT,
                            "transition": "retired_proven_false"}),
         "transition certificate is malformed"),
        ("a composite whose certificate has an empty reviewer",
         corrupt_composite("transition_certificate",
                           {**COMPOSITE_CERT, "reviewer": ""}),
         "transition certificate is malformed"),
        ("a composite that still claims to block the gate",
         corrupt_composite("blocks_gate", True),
         "suppression/blocking/transition"),
        ("a composite that grew an active cell id",
         corrupt_composite("cell_id", "p1c1"),
         "suppression/blocking/transition"),
    ):
        registry, _cell, certificate, reason = owner_registry_fixture(
            layout_mutator=mutator)
        check(
            f"{label} invalidates the exhaustive ownership registry",
            certificate is None and reason is not None and phrase in reason,
        )

    def corrupt_retained(
            field: str, value: Any,
            ) -> Any:
        def mutate(layout_fixture: dict[str, Any]) -> None:
            append_valid_retained(layout_fixture)
            layout_fixture["pages"][0]["comb_subjects"][-1][field] = value
        return mutate

    retained_corruptions = (
        (
            "reverse partition mapping",
            owner_registry_fixture(layout_mutator=corrupt_retained(
                "mapped_partition_subject_keys",
                ["p1@0.00,0.00,40.00,10.00"])),
            "reverse subject_key mapping",
        ),
        (
            "suppression emission",
            owner_registry_fixture(layout_mutator=corrupt_retained(
                "emission", "emitted")),
            "suppression/blocking/transition",
        ),
        (
            "blocking evidence",
            owner_registry_fixture(layout_mutator=corrupt_retained(
                "blocks_gate", False)),
            "suppression/blocking/transition",
        ),
        (
            "permitted transition evidence",
            owner_registry_fixture(layout_mutator=corrupt_retained(
                "permitted_transitions",
                ["retired_proven_false", "active_composite"])),
            "suppression/blocking/transition",
        ),
    )
    for label, fixture, phrase in retained_corruptions:
        check(
            f"malformed retained {label} invalidates every active certificate",
            fixture[2] is None
            and fixture[3] is not None
            and phrase in fixture[3],
        )

    # The third retained shape: a comb the cell's own printed text refutes.
    # It is admitted through the SAME identity branch as the no-band shape, so
    # both directions are asserted here -- an unrecognised reason code fails
    # the whole form's registry rather than its own record, which is why the
    # tuple has to be named, and an identity mapping is what it has to be.
    refuted_registry = owner_registry_fixture(
        layout_mutator=corrupt_retained(
            "reason_codes",
            ["emission-suppressed-caption-block-not-character-cells"]))
    check(
        "a refuted caption block is a retained shape the registry knows",
        refuted_registry[2] is not None
        and refuted_registry[0].binding_error is None,
    )

    def corrupt_refuted(field: str, value: Any) -> Any:
        def mutate(layout_fixture: dict[str, Any]) -> None:
            append_valid_retained(layout_fixture)
            subject = layout_fixture["pages"][0]["comb_subjects"][-1]
            subject["reason_codes"] = [
                "emission-suppressed-caption-block-not-character-cells"]
            subject[field] = value
        return mutate

    def refuted_mapped_elsewhere(layout_fixture: dict[str, Any]) -> None:
        """A refuted subject mapped onto a cell that is not its own.

        Both halves of the mapping move together, onto the fixture's other
        cell, so the pair stays internally consistent and the record reaches
        the identity branch instead of failing the reverse-mapping check
        first. That is what makes this a test OF the identity branch.
        """
        append_valid_retained(layout_fixture)
        page = layout_fixture["pages"][0]
        other = page["cells"][0]
        subject = page["comb_subjects"][-1]
        subject["reason_codes"] = [
            "emission-suppressed-caption-block-not-character-cells"]
        subject["mapped_partition_cell_ids"] = [other["id"]]
        subject["mapped_partition_subject_keys"] = [other["subject_key"]]

    refuted_corruptions = (
        (
            "identity mapping",
            owner_registry_fixture(layout_mutator=refuted_mapped_elsewhere),
            "identity mapping is stale",
        ),
        (
            "suppression evidence",
            owner_registry_fixture(layout_mutator=corrupt_refuted(
                "emission", "emitted")),
            "suppression/blocking/transition",
        ),
    )
    for label, fixture, phrase in refuted_corruptions:
        check(
            f"a refuted caption block with a broken {label} is rejected",
            fixture[2] is None
            and fixture[3] is not None
            and phrase in fixture[3],
        )
    check(
        "an unnamed retained reason code fails the registry, never passes it",
        owner_registry_fixture(layout_mutator=corrupt_retained(
            "reason_codes", ["emission-suppressed-invented"]))[3]
        is not None,
    )

    def append_noncontiguous_page(layout_fixture: dict[str, Any]) -> None:
        append_valid_retained(layout_fixture)
        layout_fixture["pages"].append({
            "index": 3, "cells": [], "comb_subjects": []})

    noncontiguous_registry = owner_registry_fixture(
        layout_mutator=append_noncontiguous_page)
    check(
        "noncontiguous retained layout pages invalidate active certificates",
        noncontiguous_registry[2] is None
        and noncontiguous_registry[3] is not None
        and "exhaustive and ordered" in noncontiguous_registry[3],
    )
    check(
        "exact ownership number equality rejects JSON booleans",
        not _exact_number_vector([True, 0.0], [1.0, 0.0])
        and not _exact_number_vector([1.0, 0.0], [True, 0.0]),
    )
    check(
        "exact ownership numbers do not collapse integers above 2^53",
        not _exact_number_vector(
            [9_007_199_254_740_993], [9_007_199_254_740_992]),
    )
    check(
        "exact ownership numbers preserve distinct decimal identities",
        not _exact_number_vector(
            [Decimal("0.1")], [Decimal("0.10000000000000001")])
        and not _exact_json_equal(
            [Decimal("0.10000000000000001")], [0.1]),
    )
    high_precision_certificate = CombOwnerCertificate(
        page=1,
        cell_id="p1c0",
        legacy_cell_id="p1c0",
        subject_key="p1@0.10000000000000001,0,1,1",
        bbox=(
            Decimal("0.10000000000000001"), Decimal("0"),
            Decimal("1"), Decimal("1"),
        ),
        state="active_unresolved",
        layout_sha256="0" * 64,
    )
    check(
        "certificate matching rejects a float-rounded decimal bbox",
        not high_precision_certificate.matches(1, {
            "id": "p1c0",
            "subject_key": "p1@0.10000000000000001,0,1,1",
            "x0": 0.1, "y0": 0, "x1": 1, "y1": 1,
        })
        and high_precision_certificate.evidence()["legacy_bbox"][0]
        == "0.10000000000000001",
    )

    if valid_owner is not None:
        try:
            printed_compartments(
                source_page(
                    source_paint(10), source_paint(20), framed=False),
                comb_subject(x0=5.0, x1=35.0),
                owner_certificate=valid_owner,
            )
        except ValueError as exc:
            arbitrary_owner_failed = "does not bind this exact cell" in str(exc)
        else:
            arbitrary_owner_failed = False
        check(
            "a reviewed certificate cannot be reused for an arbitrary bbox",
            arbitrary_owner_failed,
        )

    basic = printed_compartments(
        source_page(source_paint(10), source_paint(20)),
        comb_subject(),
    )
    check(f"two final black dividers make three compartments (got {basic})",
          basic == (3, [10.0, 20.0]))

    # White decorative rectangles in a white slot have no final tone boundary.
    # This is the 2200S p1c141 failure mechanism: seven such rectangles used to
    # be interleaved with its six real black dividers and double the count.
    decorative = printed_compartments(
        source_page(
            source_paint(10), source_paint(20),
            source_paint(15, width=2.4, tone=1.0),
        ),
        comb_subject(),
    )
    check(f"same-tone white decoration is not a divider (got {decorative})",
          decorative == (3, [10.0, 20.0]))

    # Content-stream order decides the final paper. A later white cell fill
    # erases an earlier black stub (0605); reversing the order repaints it.
    erased = printed_compartments(
        source_page(
            source_paint(10, order=0), source_paint(20, order=1),
            VectorPaint(9.0, 2.0, 11.0, 8.0, 1.0, 1.0, 2, "white-fill"),
        ),
        comb_subject(),
    )
    repainted = printed_compartments(
        source_page(
            VectorPaint(9.0, 2.0, 11.0, 8.0, 1.0, 1.0, 0, "white-fill"),
            source_paint(10, order=1), source_paint(20, order=2),
        ),
        comb_subject(),
    )
    check(f"later white overpaint erases the black divider (got {erased})",
          erased == (2, [20.0]))
    check(f"later black repaint restores the divider (got {repainted})",
          repainted == (3, [10.0, 20.0]))

    # A broad same-tone overpaint leaves black pixels, but no narrow vertical
    # boundary. It must not preserve the buried candidate merely by colour.
    broad_black = printed_compartments(
        source_page(
            source_paint(10, order=0), source_paint(20, order=1),
            VectorPaint(7.0, 2.0, 13.0, 8.0, 0.0, 1.0, 2, "broad-black"),
        ),
        comb_subject(),
    )
    check(f"broad same-tone paint removes narrow topology (got {broad_black})",
          broad_black == (2, [20.0]))

    # Never union unlike paints through y. The 2200A/C/P false positives are a
    # short black cap followed by a long white stem at the same x.
    cap_and_stem = printed_compartments(
        source_page(
            source_paint(10, a=2.0, b=2.48, order=0),
            source_paint(10, a=2.48, b=8.0, tone=1.0, order=1),
            source_paint(20, order=2),
        ),
        comb_subject(),
    )
    check(f"black cap and white stem do not stitch (got {cap_and_stem})",
          cap_and_stem == (2, [20.0]))

    try:
        printed_compartments(
            source_page(
                source_paint(10, tone=0.5), source_paint(20),
                framed=False),
            comb_subject(),
        )
    except ValueError as exc:
        grey_ambiguous = "band/tone choices disagree" in str(exc)
    else:
        grey_ambiguous = False
    check("competing source-derived grey and black tones fail closed",
          grey_ambiguous)

    # The source-painted band can cross the owning cell edge. 2550M's real
    # dividers begin at the cell's bottom edge and continue below it.
    outside_cell = printed_compartments(
        source_page(source_paint(10), source_paint(20)),
        comb_subject(cell_y0=0.0, cell_y1=2.0),
    )
    check(f"source paint crossing the cell edge owns dividers (got {outside_cell})",
          outside_cell == (3, [10.0, 20.0]))

    fragmented = printed_compartments(
        source_page(
            source_paint(10.0, a=2.0, b=5.0),
            source_paint(10.6, a=5.0, b=8.0),
            source_paint(20.0),
        ),
        comb_subject(),
    )
    check(f"two pieces within the existing merge bound count once (got {fragmented})",
          fragmented[0] == 3 and len(fragmented[1]) == 2)

    ambiguous_page = source_page(
        source_paint(10, a=2.0, b=5.0),
        source_paint(20, a=5.0, b=8.0),
        framed=False,
    )
    try:
        printed_compartments(ambiguous_page, comb_subject())
    except ValueError as exc:
        ambiguous_failed = "band/tone choices disagree" in str(exc)
    else:
        ambiguous_failed = False
    check("equal competing final-paint topologies fail closed", ambiguous_failed)
    if valid_owner is not None:
        try:
            printed_compartments(
                ambiguous_page,
                valid_owner_cell,
                owner_certificate=valid_owner,
            )
        except CombTopologyError as exc:
            certified_competition_failed = (
                exc.evidence.get("criterion")
                == "unanimous-source-derived-topology-required"
                and exc.evidence.get("unframed_compartment_counts") == [2]
                and exc.evidence.get("owner_certificate")
                == valid_owner.evidence()
            )
        else:
            certified_competition_failed = False
        check(
            "reviewed ownership never chooses between competing source topology",
            certified_competition_failed,
        )

        # The comb referee's proven thick-group-separator rule: a richer
        # topology that contains every other slab within POSITION_TOL_PT and
        # occupies a strict majority of the measured band is dominant, not
        # competing.  Divider 10 spans y 2..8; divider 20 only y 3.2..8, so
        # the bands are (2,8) with (10,) and (3.2,8) with (10,20): the richer
        # slab covers 4.8pt of the 6pt union.
        dominant_page = source_page(
            source_paint(10, a=2.0, b=8.0, order=0),
            source_paint(20, a=3.2, b=8.0, order=1),
            framed=False,
        )
        dominant_result = printed_compartments(
            dominant_page,
            valid_owner_cell,
            include_frame=True,
            owner_certificate=valid_owner,
        )
        check(
            "a richer strict-majority slab is dominant, not competing",
            dominant_result == (3, [10.0, 20.0], None),
        )
        # The same richer topology confined to a minority cap (y 6.5..8,
        # 1.5pt of the 6pt union) stays competing: coverage, not richness
        # alone, is what the referee proved.
        minority_page = source_page(
            source_paint(10, a=2.0, b=8.0, order=0),
            source_paint(20, a=6.5, b=8.0, order=1),
            framed=False,
        )
        try:
            printed_compartments(
                minority_page,
                valid_owner_cell,
                owner_certificate=valid_owner,
            )
        except CombTopologyError as exc:
            minority_failed = (
                exc.evidence.get("criterion")
                == "unanimous-source-derived-topology-required"
                and exc.evidence.get("unframed_compartment_counts") == [2, 3]
                and any(
                    relation.get("proper")
                    for relation in exc.evidence.get(
                        "topology_superset_relations", ())
                )
            )
        else:
            minority_failed = False
        check(
            "a minority richer slab stays competing and publishes relations",
            minority_failed,
        )

        unsupported_owner_page = source_page(
            source_paint(10), source_paint(20), framed=False)
        unsupported_owner_page = VectorPage(
            unsupported_owner_page.paints,
            (UnsupportedVectorPaint(
                (9.0, 2.0, 11.0, 8.0),
                99,
                "unsupported test source paint",
            ),),
        )
        try:
            printed_compartments(
                unsupported_owner_page,
                valid_owner_cell,
                owner_certificate=valid_owner,
            )
        except ValueError as exc:
            certified_unsupported_failed = (
                "unsupported test source paint" in str(exc))
        else:
            certified_unsupported_failed = False
        check(
            "reviewed ownership cannot bypass unsupported source paint",
            certified_unsupported_failed,
        )

        for framed_image_owner in (False, True):
            for image_order in (-100, 100):
                between_divider_image_page = source_page(
                    source_paint(10), source_paint(20),
                    framed=framed_image_owner)
                between_divider_image_page = VectorPage(
                    between_divider_image_page.paints,
                    (UnsupportedVectorPaint(
                        (14.0, 2.0, 16.0, 8.0),
                        image_order,
                        "unmodeled source fill-image paint",
                    ),),
                )
                try:
                    printed_compartments(
                        between_divider_image_page,
                        valid_owner_cell,
                        owner_certificate=valid_owner,
                    )
                except CombTopologyError as exc:
                    between_divider_image_failed = (
                        exc.evidence.get("criterion")
                        == "source-comb-band-image-free-required"
                        and exc.evidence.get("image_paint") == [{
                            "order": image_order,
                            "rect": [14.0, 2.0, 16.0, 8.0],
                        }]
                    )
                else:
                    between_divider_image_failed = False
                check(
                    "between-divider fill-image blocks source topology for "
                    f"framed={framed_image_owner} regardless of source order "
                    f"{image_order}",
                    between_divider_image_failed,
                )

        # W5 mechanism 1: a chromatic fill is refused only when it has no
        # exact rectilinear regions to offer, never merely for its colour.
        chromatic_regions = (((35.0, 2.0, 38.0, 8.0), 1),)
        chromatic_tone = round(_perceptual_luminance((0.2, 0.4, 0.9)), 4)
        away_from_divider_page = source_page(
            source_paint(10), source_paint(20), framed=False)
        away_from_divider_page = VectorPage(
            away_from_divider_page.paints,
            (UnsupportedVectorPaint(
                (35.0, 2.0, 38.0, 8.0), 99, "chromatic vector fill",
                tone=chromatic_tone, opacity=1.0,
                exact_regions=chromatic_regions, fill_rule="nonzero"),),
        )
        try:
            chromatic_resolved = printed_compartments(
                away_from_divider_page, valid_owner_cell,
                owner_certificate=valid_owner)
        except Exception:  # noqa: BLE001 - must not raise at all
            chromatic_resolved = None
        check(
            "a rectilinear chromatic fill away from any divider does not "
            "block (W5 mechanism 1)",
            chromatic_resolved == (3, [10.0, 20.0]),
        )
        unparseable_chromatic_page = source_page(
            source_paint(10), source_paint(20), framed=False)
        unparseable_chromatic_page = VectorPage(
            unparseable_chromatic_page.paints,
            (UnsupportedVectorPaint(
                (35.0, 2.0, 38.0, 8.0), 99, "chromatic vector fill"),),
        )
        try:
            printed_compartments(
                unparseable_chromatic_page, valid_owner_cell,
                owner_certificate=valid_owner)
        except ValueError as exc:
            unparseable_chromatic_failed = (
                "chromatic vector fill" in str(exc))
        else:
            unparseable_chromatic_failed = False
        check(
            "a chromatic fill with no exact rectilinear regions still "
            "blocks (W5 mechanism 1 boundary)",
            unparseable_chromatic_failed,
        )

        # W5 mechanism 2: a non-rectilinear stroke's own bounding geometry
        # decides whether it can be a divider, never its colour or shape
        # otherwise.
        stroke_away_page = source_page(
            source_paint(10), source_paint(20), framed=False)
        stroke_away_page = VectorPage(
            stroke_away_page.paints,
            (UnsupportedVectorPaint(
                (35.0, 2.0, 38.0, 8.0), 99, "non-rectilinear vector stroke"),),
        )
        try:
            stroke_resolved = printed_compartments(
                stroke_away_page, valid_owner_cell,
                owner_certificate=valid_owner)
        except Exception:  # noqa: BLE001 - must not raise at all
            stroke_resolved = None
        check(
            "a non-rectilinear stroke away from any divider does not block "
            "(W5 mechanism 2)",
            stroke_resolved == (3, [10.0, 20.0]),
        )
        stroke_on_divider_page = source_page(
            source_paint(10), source_paint(20), framed=False)
        stroke_on_divider_page = VectorPage(
            stroke_on_divider_page.paints,
            (UnsupportedVectorPaint(
                (9.0, 2.0, 11.0, 8.0), 99, "non-rectilinear vector stroke"),),
        )
        try:
            printed_compartments(
                stroke_on_divider_page, valid_owner_cell,
                owner_certificate=valid_owner)
        except ValueError as exc:
            stroke_on_divider_failed = (
                "non-rectilinear vector stroke" in str(exc))
        else:
            stroke_on_divider_failed = False
        check(
            "a non-rectilinear stroke straddling a divider still blocks "
            "(W5 mechanism 2 boundary)",
            stroke_on_divider_failed,
        )

        # W5 mechanism 4: F064 lets a candidate band own the full painted
        # extent of a shared vertical mark, so its strict-majority test can
        # fail for a row that is only a fraction of that extent even though
        # the row's own slice is unambiguous. One continuous divider at
        # x=20 runs y 0..200 -- far beyond the claimed owner's own row
        # (y 90..100) -- interrupted every 10pt by a differently-toned
        # crossing EXCEPT across the owner's own row, which stays clean.
        def crossed_divider_page(
                extra_interruptions: tuple[float, ...] = (),
                ) -> VectorPage:
            divider = VectorPaint(
                19.88, 0.0, 20.12, 200.0, 0.0, 1.0, 0, "test")
            interrupters = []
            order = 1
            y = 9.85
            while y < 200.0:
                if not (85.0 < y < 100.0):
                    interrupters.append(VectorPaint(
                        15.0, y, 25.0, y + 0.3, 0.35, 1.0, order, "test"))
                    order += 1
                y += 10.0
            for extra_y in extra_interruptions:
                interrupters.append(VectorPaint(
                    15.0, extra_y, 25.0, extra_y + 0.3, 0.35, 1.0,
                    order, "test"))
                order += 1
            rails = (
                VectorPaint(-0.12, 0.0, 0.12, 200.0, 0.0, 1.0, 900, "test"),
                VectorPaint(39.88, 0.0, 40.12, 200.0, 0.0, 1.0, 901, "test"),
            )
            return VectorPage(tuple(sorted(
                (divider, *interrupters, *rails),
                key=lambda paint: paint.order,
            )), ())

        crossed_owner_cell = comb_subject(cell_y0=90.0, cell_y1=100.0)
        _, _, crossed_owner_certificate, crossed_owner_reason = (
            owner_registry_fixture(crossed_owner_cell))
        if crossed_owner_certificate is not None:
            no_majority_page = crossed_divider_page()
            no_majority_bands, _ = _source_band_candidates(
                no_majority_page, (0.0, 90.0, 40.0, 100.0))
            no_majority_full_band_empty = all(
                not _band_topologies(no_majority_page, 0.0, 40.0, b0, b1)
                for b0, b1 in no_majority_bands
                if (b0, b1) == (0.0, 200.0)
            )
            check(
                "the unclipped full-extent band alone finds no majority "
                "(fixture sanity check)",
                any((b0, b1) == (0.0, 200.0) for b0, b1 in no_majority_bands)
                and no_majority_full_band_empty,
            )
            clipped_retry_result = printed_compartments(
                no_majority_page, crossed_owner_cell,
                owner_certificate=crossed_owner_certificate)
            check(
                "a band clipped to the claimed owner's own row decides the "
                "divider a page-wide band could not (W5 mechanism 4)",
                clipped_retry_result == (2, [20.0]),
            )
            # Two further interruptions squarely inside the owner's own row
            # fragment it below strict majority there too -- the fallback
            # must not force an answer when the row itself is genuinely
            # undecided.
            still_undecided_page = crossed_divider_page((93.0, 96.5))
            try:
                printed_compartments(
                    still_undecided_page, crossed_owner_cell,
                    owner_certificate=crossed_owner_certificate)
            except CombTopologyError as exc:
                still_undecided_failed = (
                    "no strict-majority topology" in str(exc))
            else:
                still_undecided_failed = False
            check(
                "a row genuinely fragmented even after clipping stays "
                "unevaluable (W5 mechanism 4 boundary)",
                still_undecided_failed,
            )
        check(
            "the W5 mechanism 4 fixture certifies its own owner",
            crossed_owner_certificate is not None
            and crossed_owner_reason is None,
        )

    check(
        "perceptual luminance uses the standard BT.601 coefficients",
        abs(_perceptual_luminance((1.0, 0.0, 0.0)) - 0.299) < 1e-9
        and abs(_perceptual_luminance((0.0, 1.0, 0.0)) - 0.587) < 1e-9
        and abs(_perceptual_luminance((0.0, 0.0, 1.0)) - 0.114) < 1e-9
        and abs(_perceptual_luminance((1.0, 1.0, 1.0)) - 1.0) < 1e-9
        and _perceptual_luminance((0.0, 0.0, 0.0)) == 0.0,
    )

    unframed_expansion_page = source_page(
        source_paint(10), source_paint(20), source_paint(45),
        framed=False,
    )
    for left, right in ((0.0, 30.0), (0.0, 50.0), (5.0, 35.0)):
        try:
            printed_compartments(
                unframed_expansion_page,
                comb_subject(x0=left, x1=right),
            )
        except CombTopologyError as exc:
            unframed_owner_failed = (
                exc.evidence.get("criterion")
                == "independent-complete-source-u-frame-required"
            )
        else:
            unframed_owner_failed = False
        check(
            "unframed source ink cannot be owned by bbox "
            f"{left:g}..{right:g}",
            unframed_owner_failed,
        )

    maximal_frame_page = source_page(
        *(
            source_paint(x, order=index)
            for index, x in enumerate((5, 10, 15, 20, 25, 30, 35))
        ),
        VectorPaint(5.0, 7.88, 35.0, 8.12,
                    0.0, 1.0, 20, "maximal-frame-baseline"),
        framed=False,
    )
    maximal_frame = printed_compartments(
        maximal_frame_page, comb_subject())
    check(
        "an untrimmed maximal source U-frame owns its five dividers",
        maximal_frame == (6, [10.0, 15.0, 20.0, 25.0, 30.0]),
    )
    for left, right in ((10.0, 30.0), (15.0, 25.0)):
        try:
            printed_compartments(
                maximal_frame_page,
                comb_subject(x0=left, x1=right),
            )
        except CombTopologyError as exc:
            cropped_frame_failed = (
                "crops a wider source U-frame" in str(exc)
                and exc.evidence["frame"]["left_rail"] == 5.0
                and exc.evidence["frame"]["right_rail"] == 35.0
            )
        else:
            cropped_frame_failed = False
        check(
            f"a {left:g}..{right:g} bbox cannot manufacture inner frame rails",
            cropped_frame_failed,
        )
    (_cropped_registry, cropped_owner_cell, cropped_owner,
     _cropped_reason) = owner_registry_fixture(
         comb_subject(x0=10.0, x1=30.0))
    if cropped_owner is not None:
        try:
            printed_compartments(
                maximal_frame_page,
                cropped_owner_cell,
                owner_certificate=cropped_owner,
            )
        except CombTopologyError as exc:
            certified_cropped_frame_failed = (
                "crops a wider source U-frame" in str(exc)
                and exc.evidence.get("cropped_sides") == ["left", "right"]
            )
        else:
            certified_cropped_frame_failed = False
        check(
            "reviewed ownership cannot crop a wider source U-frame",
            certified_cropped_frame_failed,
        )

    disconnected_baseline_page = source_page(
        *(
            source_paint(x, order=index)
            for index, x in enumerate((5, 10, 15, 20, 25, 30, 35))
        ),
        VectorPaint(
            7.4, 7.88, 32.6, 8.12,
            0.0, 1.0, 20, "disconnected-frame-baseline"),
        framed=False,
    )
    try:
        printed_compartments(
            disconnected_baseline_page, comb_subject())
    except CombTopologyError as exc:
        disconnected_baseline_failed = (
            exc.evidence.get("criterion")
            == "independent-complete-source-u-frame-required")
    else:
        disconnected_baseline_failed = False
    check(
        "baseline endpoints must touch actual rail ink, not nearby centres",
        disconnected_baseline_failed,
    )

    y_gap_frame_page = source_page(
        *(
            source_paint(x, a=2.0, b=7.6, order=index)
            for index, x in enumerate((5, 10, 15, 20, 25, 30, 35))
        ),
        VectorPaint(
            5.0, 7.88, 35.0, 8.12,
            0.0, 1.0, 20, "y-gap-frame-baseline"),
        framed=False,
    )
    try:
        printed_compartments(y_gap_frame_page, comb_subject())
    except CombTopologyError as exc:
        y_gap_frame_failed = (
            exc.evidence.get("criterion")
            == "independent-complete-source-u-frame-required")
    else:
        y_gap_frame_failed = False
    check(
        "verticals separated from the baseline by paper cannot form a U-frame",
        y_gap_frame_failed,
    )

    y_touch_frame_page = source_page(
        *(
            source_paint(x, a=2.0, b=7.88, order=index)
            for index, x in enumerate((5, 10, 15, 20, 25, 30, 35))
        ),
        VectorPaint(
            5.0, 7.88, 35.0, 8.12,
            0.0, 1.0, 20, "y-touch-frame-baseline"),
        framed=False,
    )
    y_touch_frame = printed_compartments(
        y_touch_frame_page, comb_subject(), include_frame=True)
    check(
        "exact y-touch between every vertical and baseline forms a U-frame",
        y_touch_frame[:2]
        == (6, [10.0, 15.0, 20.0, 25.0, 30.0])
        and y_touch_frame[2]["left_rail"]["contact_intervals_x"]
        == [[5.0, 5.12]]
        and y_touch_frame[2]["right_rail"]["contact_intervals_x"]
        == [[34.88, 35.0]],
    )

    mixed_height_frame_page = source_page(
        source_paint(5, a=2.0, b=8.0, order=0),
        source_paint(20, a=2.0, b=8.75, order=1),
        source_paint(35, a=2.0, b=8.0, order=2),
        VectorPaint(
            5.0, 8.0, 35.0, 8.75,
            0.0, 1.0, 20, "mixed-height-frame-baseline"),
        framed=False,
    )
    mixed_height_frame = printed_compartments(
        mixed_height_frame_page,
        comb_subject(x0=5.0, x1=35.0),
        include_frame=True,
    )
    check(
        "rails ending at baseline start survive an interior divider "
        "crossing baseline thickness",
        mixed_height_frame[:2] == (2, [20.0])
        and mixed_height_frame[2]["left_rail"]["ink_y1"] == 8.0
        and mixed_height_frame[2]["right_rail"]["ink_y1"] == 8.0,
    )
    check(
        "equivalent ordinary and segmented discovery stays deterministic",
        printed_compartments(
            mixed_height_frame_page,
            comb_subject(x0=5.0, x1=35.0),
            include_frame=True,
        ) == mixed_height_frame,
    )

    late_start_frame_page = source_page(
        source_paint(5, a=2.3, b=8.0, order=0),
        source_paint(20, a=2.0, b=8.75, order=1),
        source_paint(35, a=2.3, b=8.0, order=2),
        VectorPaint(
            5.0, 8.0, 35.0, 8.75,
            0.0, 1.0, 20, "late-start-frame-baseline"),
        framed=False,
    )
    late_start_baseline = next(
        baseline for baseline in _baseline_spans(
            late_start_frame_page, 8.0, 0.0)
        if baseline.left == 5.0 and baseline.right == 35.0
    )
    late_start_left_rail = _source_vertical_ink_geometry(
        late_start_frame_page, 5.0, 2.0, 8.75, 0.0)
    check(
        "a connected ordinary rail may begin inside existing leading slack",
        5.0 in _stable_source_verticals(
            late_start_frame_page, 2.5, 37.5, 2.0, 8.0, 0.0)
        and _baseline_coordinate_contacts_vertical(
            late_start_frame_page, 0.0, 5.0,
            late_start_left_rail, late_start_baseline)
        and _connected_vertical_baseline_contact(
            late_start_frame_page, 0.0, late_start_left_rail,
            2.0, 8.0, 5.0, late_start_baseline),
    )
    late_start_frame = printed_compartments(
        late_start_frame_page,
        comb_subject(x0=5.0, x1=35.0),
    )
    check(
        "leading slack does not erase a continuous source U-frame",
        late_start_frame == (2, [20.0]),
    )

    mixed_height_gap_page = source_page(
        source_paint(5, a=2.0, b=7.7, order=0),
        source_paint(20, a=2.0, b=8.75, order=1),
        source_paint(35, a=2.0, b=7.7, order=2),
        VectorPaint(
            5.0, 8.0, 35.0, 8.75,
            0.0, 1.0, 20, "mixed-height-gap-baseline"),
        framed=False,
    )
    try:
        printed_compartments(
            mixed_height_gap_page,
            comb_subject(x0=5.0, x1=35.0),
        )
    except CombTopologyError as exc:
        mixed_height_gap_failed = (
            exc.evidence.get("criterion")
            == "independent-complete-source-u-frame-required"
        )
    else:
        mixed_height_gap_failed = False
    check(
        "an interior divider crossing baseline thickness cannot bridge "
        "paper gaps under the side rails",
        mixed_height_gap_failed,
    )

    disconnected_contact_page = source_page(
        source_paint(5, a=2.0, b=7.5, order=0),
        source_paint(20, a=2.0, b=8.85, order=1),
        source_paint(35, a=2.0, b=7.5, order=2),
        source_paint(5, a=7.8, b=8.85, order=3),
        source_paint(35, a=7.8, b=8.85, order=4),
        VectorPaint(
            5.0, 8.0, 35.0, 8.75,
            0.0, 1.0, 20, "disconnected-contact-baseline"),
        framed=False,
    )
    disconnected_baseline = next(
        baseline for baseline in _baseline_spans(
            disconnected_contact_page, 8.0, 0.0)
        if baseline.left == 5.0 and baseline.right == 35.0
    )
    disconnected_left_rail = _source_vertical_ink_geometry(
        disconnected_contact_page, 5.0, 2.0, 8.75, 0.0)
    check(
        "disconnected ordinary fixture reaches the formerly independent "
        "stable-span and exact-contact predicates",
        5.0 in _stable_source_verticals(
            disconnected_contact_page, 2.5, 37.5, 2.0, 8.0, 0.0)
        and _baseline_coordinate_contacts_vertical(
            disconnected_contact_page, 0.0, 5.0,
            disconnected_left_rail, disconnected_baseline)
        and not _connected_vertical_baseline_contact(
            disconnected_contact_page, 0.0, disconnected_left_rail,
            2.0, 8.0, 5.0, disconnected_baseline),
    )
    try:
        printed_compartments(
            disconnected_contact_page,
            comb_subject(x0=5.0, x1=35.0),
        )
    except CombTopologyError as exc:
        disconnected_contact_failed = (
            exc.evidence.get("criterion")
            == "independent-complete-source-u-frame-required"
        )
    else:
        disconnected_contact_failed = False
    check(
        "a separate same-x baseline-contact fragment cannot bridge "
        "0.3pt of paper in an ordinary frame",
        disconnected_contact_failed,
    )

    disconnected_interior_page = source_page(
        source_paint(5, a=2.0, b=8.0, order=0),
        source_paint(20, a=2.0, b=7.5, order=1),
        source_paint(35, a=2.0, b=8.0, order=2),
        source_paint(20, a=7.8, b=8.85, order=3),
        VectorPaint(
            5.0, 8.0, 35.0, 8.75,
            0.0, 1.0, 20, "disconnected-interior-baseline"),
        framed=False,
    )
    disconnected_interior_baseline = next(
        baseline for baseline in _baseline_spans(
            disconnected_interior_page, 8.0, 0.0)
        if baseline.left == 5.0 and baseline.right == 35.0
    )
    disconnected_interior_geometry = _source_vertical_ink_geometry(
        disconnected_interior_page, 20.0, 2.0, 8.75, 0.0)
    check(
        "disconnected ordinary interior reaches aggregate stable/contact "
        "evidence but not one segment-bound path",
        20.0 in _stable_source_verticals(
            disconnected_interior_page, 2.5, 37.5, 2.0, 8.0, 0.0)
        and _baseline_coordinate_contacts_vertical(
            disconnected_interior_page, 0.0, 20.0,
            disconnected_interior_geometry, disconnected_interior_baseline)
        and not _vertical_has_connected_baseline_contact(
            disconnected_interior_page, 0.0,
            disconnected_interior_geometry, 2.0, 20.0,
            disconnected_interior_baseline),
    )
    try:
        printed_compartments(
            disconnected_interior_page,
            comb_subject(x0=5.0, x1=35.0),
        )
    except CombTopologyError as exc:
        disconnected_interior_failed = (
            exc.evidence.get("criterion")
            == "independent-complete-source-u-frame-required"
        )
    else:
        disconnected_interior_failed = False
    check(
        "an ordinary interior cannot borrow detached baseline-contact ink "
        "across 0.3pt of paper",
        disconnected_interior_failed,
    )

    canceled_interior_page = source_page(
        source_paint(5, a=2.0, b=8.0, order=0),
        VectorPaint(
            19.88, 2.0, 20.12, 8.0,
            0.0, 1.0, 2, "evenodd-full-vertical",
            operation=500, fill_rule="evenodd"),
        VectorPaint(
            19.88, 7.6, 20.12, 7.9,
            0.0, 1.0, 2, "evenodd-canceling-strip",
            operation=500, fill_rule="evenodd"),
        source_paint(35, a=2.0, b=8.0, order=3),
        VectorPaint(
            0.0, 7.6, 40.0, 7.9,
            0.0, 1.0, 10, "unrelated-broad-black-repaint"),
        VectorPaint(
            5.0, 8.0, 35.0, 8.75,
            0.0, 1.0, 20, "canceled-interior-baseline"),
        framed=False,
    )
    canceled_interior_baseline = next(
        baseline for baseline in _baseline_spans(
            canceled_interior_page, 8.0, 0.0)
        if baseline.left == 5.0 and baseline.right == 35.0
    )
    canceled_interior_geometry = _source_vertical_ink_geometry(
        canceled_interior_page, 20.0, 2.0, 8.75, 0.0)
    canceled_operation = [
        paint for paint in canceled_interior_page.paints
        if (paint.order, paint.operation) == (2, 500)
    ]
    check(
        "broad final black cannot back an even-odd-canceled vertical operation",
        20.0 in _stable_source_verticals(
            canceled_interior_page, 2.5, 37.5, 2.0, 8.0, 0.0)
        and _final_tone(
            [
                paint for paint in canceled_interior_page.paints
                if paint.y0 <= 7.75 <= paint.y1
            ],
            20.0,
            7.75,
        ) == 0.0
        and not _operation_covers(
            canceled_operation, 20.0, 7.75)
        and not _vertical_has_connected_baseline_contact(
            canceled_interior_page, 0.0,
            canceled_interior_geometry, 2.0, 20.0,
            canceled_interior_baseline),
    )
    try:
        printed_compartments(
            canceled_interior_page,
            comb_subject(x0=5.0, x1=35.0),
        )
    except CombTopologyError as exc:
        canceled_interior_failed = (
            exc.evidence.get("criterion")
            == "independent-complete-source-u-frame-required"
        )
    else:
        canceled_interior_failed = False
    check(
        "ordinary U-frame rejects canceled interior ink hidden by broad repaint",
        canceled_interior_failed,
    )

    genuine_repaint_page = source_page(
        source_paint(5, a=2.0, b=8.0, order=0),
        VectorPaint(
            19.88, 2.0, 20.12, 8.0,
            0.0, 1.0, 2, "evenodd-genuine-vertical",
            operation=501, fill_rule="evenodd"),
        source_paint(35, a=2.0, b=8.0, order=3),
        VectorPaint(
            0.0, 7.6, 40.0, 7.9,
            0.0, 1.0, 10, "same-tone-broad-repaint"),
        VectorPaint(
            5.0, 8.0, 35.0, 8.75,
            0.0, 1.0, 20, "genuine-repaint-baseline"),
        framed=False,
    )
    genuine_repaint_baseline = next(
        baseline for baseline in _baseline_spans(
            genuine_repaint_page, 8.0, 0.0)
        if baseline.left == 5.0 and baseline.right == 35.0
    )
    genuine_repaint_geometry = _source_vertical_ink_geometry(
        genuine_repaint_page, 20.0, 2.0, 8.75, 0.0)
    check(
        "same-tone repaint preserves a genuinely painted vertical operation",
        _vertical_has_connected_baseline_contact(
            genuine_repaint_page, 0.0,
            genuine_repaint_geometry, 2.0, 20.0,
            genuine_repaint_baseline)
        and printed_compartments(
            genuine_repaint_page,
            comb_subject(x0=5.0, x1=35.0),
        ) == (2, [20.0]),
    )

    split_rail_frame_page = source_page(
        source_paint(4.7, order=0),
        source_paint(5.3, order=1),
        *(
            source_paint(x, order=index + 2)
            for index, x in enumerate((10, 15, 20, 25, 30))
        ),
        source_paint(34.7, order=10),
        source_paint(35.3, order=11),
        VectorPaint(
            5.0, 7.88, 35.0, 8.12,
            0.0, 1.0, 20, "split-rail-frame-baseline"),
        framed=False,
    )
    try:
        printed_compartments(split_rail_frame_page, comb_subject())
    except CombTopologyError as exc:
        split_rail_frame_failed = (
            exc.evidence.get("criterion")
            == "independent-complete-source-u-frame-required")
    else:
        split_rail_frame_failed = False
    check(
        "a baseline endpoint in the gap between split rail paints is not contact",
        split_rail_frame_failed,
    )

    segmented_frame_page = source_page(
        *(
            source_paint(x, order=index)
            for index, x in enumerate((5, 10, 15, 20, 25, 30, 35))
        ),
        *(
            VectorPaint(
                left, 7.88, left + 5.0, 8.12,
                0.0, 1.0, 20 + index, "segmented-frame-baseline",
            )
            for index, left in enumerate((5, 10, 15, 20, 25, 30))
        ),
        framed=False,
    )
    segmented_full = printed_compartments(
        segmented_frame_page, comb_subject())
    check(
        "six explicit baseline operations form one maximal source frame",
        segmented_full == (6, [10.0, 15.0, 20.0, 25.0, 30.0]),
    )

    mixed_segmentation_page = source_page(
        *(
            source_paint(x, a=2.0, b=8.0, order=index)
            for index, x in enumerate((5, 10, 15, 20, 25, 30, 35))
        ),
        VectorPaint(
            5.0, 8.0, 20.0, 8.75,
            0.0, 1.0, 20, "mixed-segmentation-wide-baseline"),
        *(
            VectorPaint(
                left, 8.0, left + 5.0, 8.75,
                0.0, 1.0, 21 + index,
                "mixed-segmentation-short-baseline",
            )
            for index, left in enumerate((20, 25, 30))
        ),
        framed=False,
    )
    mixed_segmentation_baselines = _baseline_spans(
        mixed_segmentation_page, 8.0, 0.0)
    mixed_segmentation_short = next(
        baseline for baseline in mixed_segmentation_baselines
        if baseline.left == 5.0 and baseline.right == 20.0
    )
    mixed_segmentation_short_verticals = _stable_source_verticals(
        mixed_segmentation_page, 2.5, 22.5, 2.0, 8.0, 0.0)
    check(
        "mixed segmentation contains a valid short ordinary U-frame",
        all(
            source_x in mixed_segmentation_short_verticals
            and _vertical_has_connected_baseline_contact(
                mixed_segmentation_page,
                0.0,
                _source_vertical_ink_geometry(
                    mixed_segmentation_page,
                    source_x,
                    2.0,
                    8.0,
                    0.0,
                ),
                2.0,
                contact_x,
                mixed_segmentation_short,
            )
            for source_x, contact_x in (
                (5.0, 5.0),
                (10.0, 10.0),
                (15.0, 15.0),
                (20.0, 20.0),
            )
        ),
    )
    mixed_segmentation_wide_candidates = _segmented_u_frame_candidates(
        mixed_segmentation_page,
        mixed_segmentation_baselines,
        2.0,
        8.0,
        0.0,
        (10.0, 15.0),
    )
    check(
        "wider segmented discovery participates beside the short ordinary "
        "candidate",
        any(
            candidate[0] == 5.0
            and candidate[1] == 35.0
            and candidate[2] == (10.0, 15.0)
            for candidate in mixed_segmentation_wide_candidates
        ),
    )
    try:
        printed_compartments(
            mixed_segmentation_page,
            comb_subject(x0=5.0, x1=20.0),
        )
    except CombTopologyError as exc:
        mixed_segmentation_crop_failed = (
            exc.evidence.get("criterion")
            == "maximal-source-u-frame-owner"
            and exc.evidence["frame"]["left_rail"] == 5.0
            and exc.evidence["frame"]["right_rail"] == 35.0
            and exc.evidence["cropped_sides"] == ["right"]
        )
    else:
        mixed_segmentation_crop_failed = False
    check(
        "short ordinary owner is rejected as a crop of the wider frame",
        mixed_segmentation_crop_failed,
    )
    mixed_segmentation_full_a = printed_compartments(
        mixed_segmentation_page,
        comb_subject(x0=5.0, x1=35.0),
        include_frame=True,
    )
    mixed_segmentation_full_b = printed_compartments(
        mixed_segmentation_page,
        comb_subject(x0=5.0, x1=35.0),
        include_frame=True,
    )
    check(
        "full mixed-segmentation frame accepts six stable compartments",
        mixed_segmentation_full_a[:2]
        == (6, [10.0, 15.0, 20.0, 25.0, 30.0]),
    )
    check(
        "mixed ordinary/segmented discovery has no duplicate instability",
        mixed_segmentation_full_b == mixed_segmentation_full_a
        and len(
            mixed_segmentation_full_a[2]["baseline_operations"]
        ) == 4,
    )

    mixed_height_segmented_page = source_page(
        source_paint(5, a=2.0, b=8.0, order=0),
        *(
            source_paint(x, a=2.0, b=8.75, order=index + 1)
            for index, x in enumerate((10, 15, 20, 25, 30))
        ),
        source_paint(35, a=2.0, b=8.0, order=10),
        *(
            VectorPaint(
                left, 8.0, left + 5.0, 8.75,
                0.0, 1.0, 20 + index,
                "mixed-height-segmented-baseline",
            )
            for index, left in enumerate((5, 10, 15, 20, 25, 30))
        ),
        framed=False,
    )
    mixed_height_segmented = printed_compartments(
        mixed_height_segmented_page,
        comb_subject(x0=5.0, x1=35.0),
        include_frame=True,
    )
    check(
        "segmented baseline accepts side rails ending at its start while "
        "interior dividers cross its thickness",
        mixed_height_segmented[:2]
        == (6, [10.0, 15.0, 20.0, 25.0, 30.0])
        and len(
            mixed_height_segmented[2]["baseline_operations"]
        ) == 6,
    )

    mixed_height_segmented_gap_page = source_page(
        source_paint(5, a=2.0, b=7.7, order=0),
        *(
            source_paint(x, a=2.0, b=8.75, order=index + 1)
            for index, x in enumerate((10, 15, 20, 25, 30))
        ),
        source_paint(35, a=2.0, b=7.7, order=10),
        *(
            VectorPaint(
                left, 8.0, left + 5.0, 8.75,
                0.0, 1.0, 20 + index,
                "mixed-height-segmented-gap-baseline",
            )
            for index, left in enumerate((5, 10, 15, 20, 25, 30))
        ),
        framed=False,
    )
    try:
        printed_compartments(
            mixed_height_segmented_gap_page,
            comb_subject(x0=5.0, x1=35.0),
        )
    except CombTopologyError as exc:
        mixed_height_segmented_gap_failed = (
            exc.evidence.get("criterion")
            == "independent-complete-source-u-frame-required"
        )
    else:
        mixed_height_segmented_gap_failed = False
    check(
        "segmented baselines cannot bridge paper gaps under their side rails",
        mixed_height_segmented_gap_failed,
    )

    disconnected_segmented_page = source_page(
        source_paint(5, a=2.0, b=7.5, order=0),
        *(
            source_paint(x, a=2.0, b=8.85, order=index + 1)
            for index, x in enumerate((10, 15, 20, 25, 30))
        ),
        source_paint(35, a=2.0, b=7.5, order=10),
        source_paint(5, a=7.8, b=8.85, order=11),
        source_paint(35, a=7.8, b=8.85, order=12),
        *(
            VectorPaint(
                left, 8.0, left + 5.0, 8.75,
                0.0, 1.0, 20 + index,
                "disconnected-segmented-baseline",
            )
            for index, left in enumerate((5, 10, 15, 20, 25, 30))
        ),
        framed=False,
    )
    disconnected_segmented_baselines = _baseline_spans(
        disconnected_segmented_page, 8.0, 0.0)
    disconnected_segmented_verticals = _stable_source_verticals(
        disconnected_segmented_page, 2.5, 37.5, 2.0, 8.0, 0.0)
    check(
        "disconnected segmented fixture reaches the former stable six-segment "
        "candidate path before connected-rail qualification",
        len(disconnected_segmented_baselines) == 6
        and all(
            source_x in disconnected_segmented_verticals
            for source_x in (5.0, 10.0, 15.0, 20.0, 25.0, 30.0, 35.0)
        )
        and not _segmented_u_frame_candidates(
            disconnected_segmented_page,
            disconnected_segmented_baselines,
            2.0,
            8.0,
            0.0,
            (10.0, 15.0, 20.0, 25.0, 30.0),
        ),
    )
    try:
        printed_compartments(
            disconnected_segmented_page,
            comb_subject(x0=5.0, x1=35.0),
        )
    except CombTopologyError as exc:
        disconnected_segmented_failed = (
            exc.evidence.get("criterion")
            == "independent-complete-source-u-frame-required"
        )
    else:
        disconnected_segmented_failed = False
    check(
        "separate same-x contact fragments cannot bridge 0.3pt of paper "
        "into a segmented source frame",
        disconnected_segmented_failed,
    )

    disconnected_segmented_interior_page = source_page(
        *(
            source_paint(x, a=2.0, b=8.75, order=index)
            for index, x in enumerate((5, 10, 15))
        ),
        source_paint(20, a=2.0, b=7.5, order=4),
        *(
            source_paint(x, a=2.0, b=8.75, order=index + 5)
            for index, x in enumerate((25, 30, 35))
        ),
        source_paint(20, a=7.8, b=8.85, order=10),
        *(
            VectorPaint(
                left, 8.0, left + 5.0, 8.75,
                0.0, 1.0, 20 + index,
                "disconnected-segmented-interior-baseline",
            )
            for index, left in enumerate((5, 10, 15, 20, 25, 30))
        ),
        framed=False,
    )
    disconnected_segmented_interior_baselines = _baseline_spans(
        disconnected_segmented_interior_page, 8.0, 0.0)
    disconnected_segmented_interior_contacts = tuple(
        baseline
        for baseline in disconnected_segmented_interior_baselines
        if 20.0 >= baseline.left - SOURCE_COORD_EPS_PT
        and 20.0 <= baseline.right + SOURCE_COORD_EPS_PT
    )
    disconnected_segmented_interior_geometry = (
        _source_vertical_ink_geometry(
            disconnected_segmented_interior_page,
            20.0,
            2.0,
            8.75,
            0.0,
        )
    )
    check(
        "disconnected segmented interior reaches junction aggregate contact "
        "but no connected segment-level witness",
        20.0 in _stable_source_verticals(
            disconnected_segmented_interior_page,
            2.5,
            37.5,
            2.0,
            8.0,
            0.0,
        )
        and disconnected_segmented_interior_contacts
        and any(
            _baseline_coordinate_contacts_vertical(
                disconnected_segmented_interior_page,
                0.0,
                20.0,
                disconnected_segmented_interior_geometry,
                contact,
            )
            for contact in disconnected_segmented_interior_contacts
        )
        and all(
            not _vertical_has_connected_baseline_contact(
                disconnected_segmented_interior_page,
                0.0,
                disconnected_segmented_interior_geometry,
                2.0,
                20.0,
                contact,
            )
            for contact in disconnected_segmented_interior_contacts
        ),
    )
    check(
        "segmented candidate rejects a detached interior contact fragment",
        not _segmented_u_frame_candidates(
            disconnected_segmented_interior_page,
            disconnected_segmented_interior_baselines,
            2.0,
            8.0,
            0.0,
            (10.0, 15.0, 20.0, 25.0, 30.0),
        ),
    )
    try:
        printed_compartments(
            disconnected_segmented_interior_page,
            comb_subject(x0=5.0, x1=35.0),
        )
    except CombTopologyError as exc:
        disconnected_segmented_interior_failed = (
            exc.evidence.get("criterion")
            == "independent-complete-source-u-frame-required"
        )
    else:
        disconnected_segmented_interior_failed = False
    check(
        "a segmented interior cannot borrow detached baseline-contact ink "
        "across 0.3pt of paper",
        disconnected_segmented_interior_failed,
    )

    canceled_segmented_interior_page = source_page(
        *(
            source_paint(x, a=2.0, b=8.75, order=index)
            for index, x in enumerate((5, 10, 15))
        ),
        VectorPaint(
            19.88, 2.0, 20.12, 8.75,
            0.0, 1.0, 4, "segmented-evenodd-full-vertical",
            operation=600, fill_rule="evenodd"),
        VectorPaint(
            19.88, 7.6, 20.12, 7.9,
            0.0, 1.0, 4, "segmented-evenodd-canceling-strip",
            operation=600, fill_rule="evenodd"),
        *(
            source_paint(x, a=2.0, b=8.75, order=index + 5)
            for index, x in enumerate((25, 30, 35))
        ),
        VectorPaint(
            0.0, 7.6, 40.0, 7.9,
            0.0, 1.0, 10, "segmented-unrelated-broad-repaint"),
        *(
            VectorPaint(
                left, 8.0, left + 5.0, 8.75,
                0.0, 1.0, 20 + index,
                "canceled-segmented-interior-baseline",
            )
            for index, left in enumerate((5, 10, 15, 20, 25, 30))
        ),
        framed=False,
    )
    canceled_segmented_baselines = _baseline_spans(
        canceled_segmented_interior_page, 8.0, 0.0)
    canceled_segmented_geometry = _source_vertical_ink_geometry(
        canceled_segmented_interior_page, 20.0, 2.0, 8.75, 0.0)
    canceled_segmented_contacts = tuple(
        contact for contact in canceled_segmented_baselines
        if abs(contact.y0 - 8.0) <= SOURCE_COORD_EPS_PT
        and contact.left >= 5.0 - SOURCE_COORD_EPS_PT
        and contact.right <= 35.0 + SOURCE_COORD_EPS_PT
        and 20.0 >= contact.left - SOURCE_COORD_EPS_PT
        and 20.0 <= contact.right + SOURCE_COORD_EPS_PT
    )
    check(
        "canceled segmented interior remains inside stable-span slack",
        20.0 in _stable_source_verticals(
            canceled_segmented_interior_page,
            2.5,
            37.5,
            2.0,
            8.0,
            0.0,
        ),
    )
    check(
        "segmented junction contacts exist but lack operation-backed paths",
        bool(canceled_segmented_contacts)
        and all(
            not _vertical_has_connected_baseline_contact(
                canceled_segmented_interior_page,
                0.0,
                canceled_segmented_geometry,
                2.0,
                20.0,
                contact,
            )
            for contact in canceled_segmented_contacts
        ),
    )
    check(
        "segmented candidate rejects the operation-canceled interior",
        not _segmented_u_frame_candidates(
            canceled_segmented_interior_page,
            canceled_segmented_baselines,
            2.0,
            8.0,
            0.0,
            (10.0, 15.0, 20.0, 25.0, 30.0),
        ),
    )
    try:
        printed_compartments(
            canceled_segmented_interior_page,
            comb_subject(x0=5.0, x1=35.0),
        )
    except CombTopologyError as exc:
        canceled_segmented_interior_failed = (
            exc.evidence.get("criterion")
            == "independent-complete-source-u-frame-required"
        )
    else:
        canceled_segmented_interior_failed = False
    check(
        "segmented U-frame rejects canceled interior ink hidden by broad repaint",
        canceled_segmented_interior_failed,
    )

    mixed_level_disconnected_page = source_page(
        source_paint(5, a=2.0, b=8.0, order=0),
        *(
            source_paint(x, a=2.0, b=9.45, order=index + 1)
            for index, x in enumerate((10, 15, 20, 25, 30))
        ),
        source_paint(35, a=2.0, b=8.0, order=10),
        source_paint(35, a=8.4, b=9.45, order=11),
        *(
            VectorPaint(
                left,
                8.0 if index % 2 == 0 else 8.4,
                left + 5.0,
                8.4 if index % 2 == 0 else 8.8,
                0.0, 1.0, 20 + index,
                "mixed-level-disconnected-baseline",
            )
            for index, left in enumerate((5, 10, 15, 20, 25, 30))
        ),
        framed=False,
    )
    mixed_level_baselines = _baseline_spans(
        mixed_level_disconnected_page, 8.8, 0.0)
    mixed_level_right_baseline = next(
        baseline for baseline in mixed_level_baselines
        if baseline.right == 35.0
    )
    mixed_level_right_rail = _source_vertical_ink_geometry(
        mixed_level_disconnected_page, 35.0, 2.0, 8.8, 0.0)
    check(
        "mixed-level fixture isolates component-minimum over-admission from "
        "the actual right endpoint segment level",
        35.0 in _stable_source_verticals(
            mixed_level_disconnected_page,
            2.5,
            37.5,
            2.0,
            8.0,
            0.0,
        )
        and _connected_vertical_baseline_contact(
            mixed_level_disconnected_page, 0.0, mixed_level_right_rail,
            2.0, 8.0, 35.0, mixed_level_right_baseline)
        and not _connected_vertical_baseline_contact(
            mixed_level_disconnected_page, 0.0, mixed_level_right_rail,
            2.0, mixed_level_right_baseline.y0,
            35.0, mixed_level_right_baseline),
    )
    check(
        "segmented endpoint qualification rejects a detached rail at the "
        "later right baseline level",
        not _segmented_u_frame_candidates(
            mixed_level_disconnected_page,
            mixed_level_baselines,
            2.0,
            8.8,
            0.0,
            (10.0, 15.0, 20.0, 25.0, 30.0),
        ),
    )
    try:
        printed_compartments(
            mixed_level_disconnected_page,
            comb_subject(x0=5.0, x1=35.0),
        )
    except CombTopologyError as exc:
        mixed_level_disconnected_failed = (
            exc.evidence.get("criterion")
            == "independent-complete-source-u-frame-required"
        )
    else:
        mixed_level_disconnected_failed = False
    check(
        "a later segmented endpoint cannot borrow detached contact ink "
        "above an internal paper gap",
        mixed_level_disconnected_failed,
    )

    for left, right in ((10.0, 30.0), (15.0, 25.0)):
        try:
            printed_compartments(
                segmented_frame_page,
                comb_subject(x0=left, x1=right),
            )
        except CombTopologyError as exc:
            segmented_crop_failed = (
                "crops a wider source U-frame" in str(exc)
                and exc.evidence["frame"]["left_rail"] == 5.0
                and exc.evidence["frame"]["right_rail"] == 35.0
                and len(exc.evidence["frame"]["baseline_operations"]) == 6
            )
        else:
            segmented_crop_failed = False
        check(
            f"segmented baseline rejects a {left:g}..{right:g} bbox crop",
            segmented_crop_failed,
        )

    alternating_segment_page = source_page(
        *(
            source_paint(x, a=2.0, b=8.32, order=index)
            for index, x in enumerate((5, 10, 15, 20, 25, 30, 35))
        ),
        *(
            VectorPaint(
                left,
                7.68 if index % 2 == 0 else 8.08,
                left + 5.0,
                7.92 if index % 2 == 0 else 8.32,
                0.0, 1.0, 20 + index,
                "alternating-level-segmented-baseline",
            )
            for index, left in enumerate((5, 10, 15, 20, 25, 30))
        ),
        framed=False,
    )
    try:
        printed_compartments(alternating_segment_page, comb_subject())
    except CombTopologyError as exc:
        alternating_segment_failed = (
            exc.evidence.get("criterion")
            == "independent-complete-source-u-frame-required")
    else:
        alternating_segment_failed = False
    check(
        "alternating baseline levels separated by paper do not merge",
        alternating_segment_failed,
    )

    touching_segment_page = source_page(
        *(
            source_paint(x, a=2.0, b=8.16, order=index)
            for index, x in enumerate((5, 10, 15, 20, 25, 30, 35))
        ),
        *(
            VectorPaint(
                left,
                7.68 if index % 2 == 0 else 7.92,
                left + 5.0,
                7.92 if index % 2 == 0 else 8.16,
                0.0, 1.0, 20 + index,
                "touching-level-segmented-baseline",
            )
            for index, left in enumerate((5, 10, 15, 20, 25, 30))
        ),
        framed=False,
    )
    touching_segment = printed_compartments(
        touching_segment_page, comb_subject())
    check(
        "piecewise baseline segments that truly y-touch remain connected",
        touching_segment == (
            6, [10.0, 15.0, 20.0, 25.0, 30.0]),
    )

    buried_baseline_page = source_page(
        *(
            source_paint(x, order=index)
            for index, x in enumerate((5, 10, 15, 20, 25, 30, 35))
        ),
        VectorPaint(
            5.0, 7.88, 35.0, 8.12,
            0.0, 1.0, 20, "buried-narrow-baseline"),
        VectorPaint(
            0.0, 7.5, 40.0, 10.5,
            0.0, 1.0, 21, "later-broad-same-tone-fill"),
        framed=False,
    )
    try:
        printed_compartments(buried_baseline_page, comb_subject())
    except CombTopologyError as exc:
        buried_baseline_failed = (
            exc.evidence.get("criterion")
            == "independent-complete-source-u-frame-required")
    else:
        buried_baseline_failed = False
    check(
        "broad same-tone overpaint cannot own a buried narrow baseline",
        buried_baseline_failed,
    )

    repainted_baseline_page = source_page(
        *buried_baseline_page.paints,
        VectorPaint(
            5.0, 7.88, 35.0, 8.12,
            0.0, 1.0, 22, "final-narrow-baseline-repaint"),
        framed=False,
    )
    repainted_baseline = printed_compartments(
        repainted_baseline_page, comb_subject())
    check(
        "a final narrow baseline repaint restores source ownership",
        repainted_baseline == (
            6, [10.0, 15.0, 20.0, 25.0, 30.0]),
    )

    # ---------------------------------------------------------------- #
    # Rule chains, junction blocks and the source's own walls.
    #
    # Five relations that all answer one question the sheets forced: what
    # counts as ONE printed comb when the ink that proves it is emitted in
    # dozens of pieces and shared with the table around it. Each has its own
    # fixture below and its own mutation in the sweep that follows, and the
    # plain comb is carried through every mutation as a control.
    # ---------------------------------------------------------------- #

    def chain_comb_page(*segments: tuple[float, float, float, float],
                        order: int = 40) -> VectorPage:
        """A 6-slot comb whose baseline is drawn as the given ink pieces."""
        return source_page(
            *(
                source_paint(x, order=index)
                for index, x in enumerate((5, 10, 15, 20, 25, 30, 35))
            ),
            *(
                VectorPaint(
                    left, y0, right, y1, 0.0, 1.0, order + index,
                    "chain-baseline-piece")
                for index, (left, y0, right, y1) in enumerate(segments)
            ),
            framed=False,
        )

    def block_chain(block_width: float, *, overlap: float = 0.0,
                    ) -> tuple[tuple[float, float, float, float], ...]:
        """One rule chain: a junction block per divider, ink between them.

        The chain runs rail centre to rail centre, so its own ends are the
        frame's rails and only the interior junctions are blocks. `overlap`
        pushes each run a hair into the block that follows it, which is what
        1707 does at 0.004pt.
        """
        edges: list[tuple[float, float]] = [(5.0, 5.0)]
        for divider in (10.0, 15.0, 20.0, 25.0, 30.0):
            edges.append((
                divider - block_width / 2.0, divider + block_width / 2.0))
        edges.append((35.0, 35.0))
        pieces = [
            (left, 8.0, right, 8.75) for left, right in edges[1:-1]
        ]
        for index, ((_left, run_start), (run_end, _right)) in enumerate(
                zip(edges, edges[1:])):
            reach = 0.0 if index == len(edges) - 2 else overlap
            pieces.append((run_start, 8.0, run_end + reach, 8.75))
        return tuple(pieces)

    def frame_resolves(page: VectorPage, expected: tuple[int, list[float]],
                       subject: dict[str, Any] | None = None) -> bool:
        try:
            resolved = printed_compartments(
                page, comb_subject() if subject is None else subject)
        except ValueError:
            return False
        return resolved == expected

    plain_comb_page = source_page(
        *(
            source_paint(x, order=index)
            for index, x in enumerate((5, 10, 15, 20, 25, 30, 35))
        ),
        VectorPaint(5.0, 8.0, 35.0, 8.75, 0.0, 1.0, 40, "plain-baseline"),
        framed=False,
    )
    six_slots = (6, [10.0, 15.0, 20.0, 25.0, 30.0])
    check(
        "one unsegmented baseline still frames a plain comb",
        frame_resolves(plain_comb_page, six_slots),
    )

    # 1707 overlaps consecutive pieces of one chain by 0.004pt. The junction
    # block owning that sliver is neither wider than tall nor long enough to
    # read as a divider, so it used to hide the 13pt piece beside it.
    noisy_overlap_chain_page = chain_comb_page(
        *block_chain(0.752, overlap=0.004))
    check(
        "a junction block does not hide the chain piece it overlaps",
        frame_resolves(noisy_overlap_chain_page, six_slots),
    )

    # 2000-DST paints the same block at two x with widths either side of its
    # own height. Both are junctions; neither is a stroke across the rule.
    square_block_chain_page = chain_comb_page(*block_chain(0.75))
    check(
        "an exactly square junction block belongs to its rule chain",
        frame_resolves(square_block_chain_page, six_slots),
    )

    # 2000-DST and 2200-A each leave one 0.006pt gap in an otherwise exact
    # chain. A break narrower than the rule is thick cannot print as a break.
    hairline_gap_chain_page = chain_comb_page(
        (5.0, 8.0, 10.0, 8.75),
        (10.01, 8.0, 35.0, 8.75),
    )
    check(
        "a gap narrower than the rule is thick does not cut its chain",
        frame_resolves(hairline_gap_chain_page, six_slots),
    )
    wide_gap_chain_page = chain_comb_page(
        (5.0, 8.0, 10.0, 8.75),
        (10.75, 8.0, 35.0, 8.75),
    )
    check(
        "a gap as wide as the rule is thick still cuts its chain",
        not frame_resolves(wide_gap_chain_page, six_slots),
    )

    # 1600WP draws MM|DD|YYYY as one stroked rectangle carrying two
    # full-height interior walls. The maximal frame is the rectangle; the comb
    # is the cell of it between two walls.
    nested_cell_page = source_page(
        *(source_paint(x, order=index) for index, x in enumerate((10, 20, 30))),
        source_paint(5, a=0.5, b=8.0, order=10),
        source_paint(15, a=0.5, b=8.0, order=11),
        source_paint(25, a=0.5, b=8.0, order=12),
        source_paint(35, a=0.5, b=8.0, order=13),
        VectorPaint(5.0, 0.5, 35.0, 0.75, 0.0, 1.0, 20, "nested-top-rule"),
        VectorPaint(5.0, 8.0, 35.0, 8.75, 0.0, 1.0, 21, "nested-baseline"),
        framed=False,
    )
    nested_subject = comb_subject(x0=5.0, x1=15.0)
    check(
        "a comb inside a walled rectangle is the cell its owner claims",
        frame_resolves(nested_cell_page, (2, [10.0]), nested_subject),
    )
    check(
        "an owner spanning two walled cells keeps its wider-frame verdict",
        not frame_resolves(
            nested_cell_page, (3, [10.0, 20.0]),
            comb_subject(x0=5.0, x1=25.0)),
    )

    # 1700 draws each date-box wall as two operations that miss each other by
    # 0.006pt exactly where the comb band starts.
    split_wall_page = source_page(
        *(source_paint(x, order=index) for index, x in enumerate((10, 20, 30))),
        *(
            paint
            for index, x in enumerate((5.0, 15.0, 25.0, 35.0))
            for paint in (
                source_paint(x, a=0.5, b=1.994, order=10 + 2 * index),
                source_paint(x, a=2.0, b=8.0, order=11 + 2 * index),
            )
        ),
        VectorPaint(5.0, 0.5, 35.0, 0.75, 0.0, 1.0, 20, "split-wall-top-rule"),
        VectorPaint(5.0, 8.0, 35.0, 8.75, 0.0, 1.0, 21, "split-wall-baseline"),
        framed=False,
    )
    check(
        "a wall drawn as two operations a hair apart is still one wall",
        frame_resolves(split_wall_page, (2, [10.0]), nested_subject),
    )

    # 2200-A's comb sits on the bottom rule of a full-width table row, so the
    # chain's own ends are page furniture and the rails are interior to it.
    row_rule_page = source_page(
        *(
            source_paint(x, order=index)
            for index, x in enumerate((10, 15, 20, 25, 30))
        ),
        source_paint(5, a=0.5, b=8.0, order=10),
        source_paint(35, a=0.5, b=8.0, order=11),
        VectorPaint(0.0, 0.5, 50.0, 0.75, 0.0, 1.0, 20, "row-top-rule"),
        VectorPaint(0.0, 8.0, 50.0, 8.75, 0.0, 1.0, 21, "row-bottom-rule"),
        framed=False,
    )
    row_subject = comb_subject(x0=5.0, x1=35.0, cell_y1=9.0)
    check(
        "a comb standing on a row rule is framed by its own walls",
        frame_resolves(row_rule_page, six_slots, row_subject),
    )

    # 2200-A knocks the rule above its comb out with a white rectangle as
    # thick as the stroke it erases. A divider whose upper half is cut off
    # that way is a divider, whatever stands above the cut.
    erased_wall_page = source_page(
        *(
            source_paint(x, order=index)
            for index, x in enumerate((10, 15, 20, 25, 30))
        ),
        source_paint(5, a=0.5, b=8.0, order=10),
        source_paint(35, a=0.5, b=8.0, order=11),
        source_paint(20, a=0.5, b=1.9, order=12),
        VectorPaint(
            19.88, 1.9, 20.12, 2.0, 1.0, 1.0, 13, "erased-wall-knockout"),
        VectorPaint(0.0, 0.5, 50.0, 0.75, 0.0, 1.0, 20, "erased-wall-top-rule"),
        VectorPaint(0.0, 8.0, 50.0, 8.75, 0.0, 1.0, 21, "erased-wall-baseline"),
        framed=False,
    )
    check(
        "a divider cut off from the rule above it is not a wall",
        frame_resolves(erased_wall_page, six_slots, row_subject),
    )

    # 2000-DST and 2200-C stop the wall BETWEEN two comb boxes the same way,
    # with a white rectangle cut to the wall's own width a point and a half
    # above the band. Ink above the cut therefore proves nothing either way and
    # the sheets separate the two cases on how the strokes are drawn: the wall
    # is twice the weight of the dividers beside it and stands ten times higher,
    # while 2200-A's erased divider is the same weight as its neighbours. Both
    # axes, always, and the dividers -- never the band -- fix the height a
    # divider reaches: see `_stroke_stands_at_border_weight` for the measured
    # separation on all five sheets.
    #
    # Every page below carries the sheets' own rule chain, one junction block
    # per vertical, because a comb whose baseline is a single unbroken paint
    # never asks this question: its rails are found directly and the frame
    # never spans more than the owner claims.
    BOX_TOP, BOX_BOTTOM = 1.0, 20.0      # the walled box's own rules
    BAND_TOP, DIVIDER_TOP = 12.5, 11.4   # the comb band, and how high a
    CUT_TOP = 11.0                       # divider stands; the wall's erasure

    def rule_chain(left_end: float, right_end: float, *verticals: float,
                   y0: float = BOX_BOTTOM) -> tuple[VectorPaint, ...]:
        """One rule drawn as official sheets draw it: a junction block per
        vertical with a run of rule between them."""
        edges = [(left_end, left_end)]
        edges.extend((x - 0.376, x + 0.376) for x in verticals)
        edges.append((right_end, right_end))
        pieces = list(edges[1:-1])
        pieces.extend(
            (run_start, run_end)
            for (_left, run_start), (run_end, _right) in zip(edges, edges[1:])
        )
        return tuple(
            VectorPaint(left, y0, right, y0 + 0.75, 0.0, 1.0, 70 + index,
                        "rule-chain-piece")
            for index, (left, right) in enumerate(sorted(pieces))
        )

    def walled_box(*interior: VectorPaint) -> VectorPage:
        """One stroked rectangle 5..35 with border-weight sides."""
        return source_page(
            VectorPaint(5.0, 0.5, 35.0, BOX_TOP, 0.0, 1.0, 60, "box-top-rule"),
            *rule_chain(5.0, 35.0, 10.0, 15.0, 20.0, 25.0, 30.0),
            source_paint(5.0, a=BOX_TOP, b=BOX_BOTTOM, width=0.48, order=1),
            source_paint(35.0, a=BOX_TOP, b=BOX_BOTTOM, width=0.48, order=2),
            *interior,
            framed=False,
        )

    def box_dividers(*xs: float, width: float = 0.24, order: int = 10,
                     flush: bool = False) -> tuple[VectorPaint, ...]:
        """Strokes hanging from the box's baseline, stopping inside the box.

        They stand `DIVIDER_TOP` above the band as 2000-DST's and 2200-C's do,
        unless `flush`, which is 2200-C's money row: dividers that stop dead at
        the band's own top edge and so say nothing about a divider's height.
        """
        return tuple(
            paint
            for index, x in enumerate(xs)
            for paint in (
                (source_paint(x, a=DIVIDER_TOP, b=BAND_TOP, width=width,
                              order=order + 2 * index),)
                if not flush else ()
            ) + (
                source_paint(x, a=BAND_TOP, b=BOX_BOTTOM, width=width,
                             order=order + 2 * index + 1),
            )
        )

    def cut_stroke(x: float, width: float,
                   order: int) -> tuple[VectorPaint, ...]:
        """A stroke spanning the whole box, erased at its own width just above
        the band."""
        return (
            source_paint(x, a=BOX_TOP, b=CUT_TOP, width=width, order=order),
            VectorPaint(x - width / 2, CUT_TOP, x + width / 2, BAND_TOP,
                        1.0, 1.0, order + 1, "stroke-knockout"),
            source_paint(x, a=BAND_TOP, b=BOX_BOTTOM, width=width,
                         order=order + 2),
        )

    left_cell = comb_subject(x0=5.0, x1=20.0, cell_y1=21.0)
    right_cell = comb_subject(x0=20.0, x1=35.0, cell_y1=21.0)

    knocked_out_wall_page = walled_box(
        *box_dividers(10.0, 15.0, 25.0, 30.0), *cut_stroke(20.0, 0.48, 30))
    check(
        "a border-weight wall the sheet cut off still partitions its frame",
        frame_resolves(knocked_out_wall_page, (3, [10.0, 15.0]), left_cell)
        and frame_resolves(
            knocked_out_wall_page, (3, [25.0, 30.0]), right_cell),
    )
    check(
        "an owner spanning a cut-off wall keeps its wider-frame verdict",
        not frame_resolves(
            knocked_out_wall_page, (5, [10.0, 15.0, 20.0, 25.0]),
            comb_subject(x0=5.0, x1=30.0, cell_y1=21.0)),
    )

    # A money comb's thousands separator is thicker than its digit dividers and
    # hangs from the same baseline to the same height. Weight alone would make
    # it a box wall and cut the comb in two.
    heavy_divider_page = walled_box(
        *box_dividers(10.0, 25.0, 30.0),
        *box_dividers(15.0, width=0.48, order=40),
        *cut_stroke(20.0, 0.48, 30))
    check(
        "a heavier stroke standing no higher than its neighbours is a divider",
        frame_resolves(heavy_divider_page, (3, [10.0, 15.0]), left_cell),
    )

    # The same wall, its break covered by a page-wide knockout rather than an
    # erasure cut to the stroke. Paint crossing this x on its way somewhere
    # else says nothing about what stands above.
    broad_overpaint_page = walled_box(
        *box_dividers(10.0, 15.0, 25.0, 30.0),
        source_paint(20.0, a=BOX_TOP, b=CUT_TOP, width=0.48, order=30),
        VectorPaint(5.0, CUT_TOP, 35.0, BAND_TOP, 1.0, 1.0, 31,
                    "broad-overpaint"),
        source_paint(20.0, a=BAND_TOP, b=BOX_BOTTOM, width=0.48, order=32),
    )
    check(
        "a break the sheet did not erase at this stroke leaves it unjoined",
        not frame_resolves(
            broad_overpaint_page, (3, [10.0, 15.0]), left_cell),
    )

    # 2200-A's erased divider restated inside a walled box: divider weight, cut
    # off from the rule above exactly as the wall beside it is.
    cut_divider_page = walled_box(
        *box_dividers(10.0, 25.0, 30.0),
        source_paint(20.0, a=BOX_TOP, b=BOX_BOTTOM, width=0.48, order=30),
        *cut_stroke(15.0, 0.24, 40),
    )
    check(
        "a divider-weight stroke standing above the band is not a wall",
        frame_resolves(cut_divider_page, (3, [10.0, 15.0]), left_cell),
    )

    # A divider under a grey band with an unrelated stroke of its own width
    # above it. The band is not this stroke's erasure, so the two do not join
    # and the divider does not drag the frame's wall detection down with it.
    bridged_divider_page = walled_box(
        *box_dividers(10.0, 15.0, 25.0, 30.0), *cut_stroke(20.0, 0.48, 30),
        source_paint(25.0, a=BOX_TOP, b=CUT_TOP, order=50),
        VectorPaint(5.0, CUT_TOP, 35.0, DIVIDER_TOP, 0.8509, 1.0, 51,
                    "grey-band"),
    )
    check(
        "a grey band does not join a divider to the stroke above it",
        frame_resolves(bridged_divider_page, (3, [10.0, 15.0]), left_cell),
    )

    # 2200-C's money rows: real column walls bounding the sheet's grey N/A
    # blocks, beside digit dividers that stop dead at the band's top edge. The
    # comb is the whole row and must stay whole, so the wall relation has to
    # refuse rather than guess when no divider stands above the band.
    whole_row = comb_subject(x0=5.0, x1=35.0, cell_y1=21.0)
    flush_divider_page = source_page(
        VectorPaint(0.0, 0.5, 50.0, BOX_TOP, 0.0, 1.0, 60, "row-top-rule"),
        *rule_chain(0.0, 50.0, 5.0, 10.0, 15.0, 20.0, 25.0, 30.0, 35.0),
        source_paint(5.0, a=BOX_TOP, b=BOX_BOTTOM, width=0.48, order=1),
        source_paint(35.0, a=BOX_TOP, b=BOX_BOTTOM, width=0.48, order=2),
        *box_dividers(10.0, 15.0, 25.0, 30.0, flush=True),
        *cut_stroke(20.0, 0.48, 30),
        framed=False,
    )
    check(
        "dividers flush with the band leave a heavier stroke unproven",
        frame_resolves(
            flush_divider_page, (6, [10.0, 15.0, 20.0, 25.0, 30.0]),
            whole_row),
    )

    # 2200-A's rails stop 3e-5pt short of the baseline they stand on. The
    # midpoint of that non-overlap is inside neither rectangle.
    touching_rail_page = source_page(
        *(
            source_paint(x, b=8.0 - 0.0001, order=index)
            for index, x in enumerate((5, 10, 15, 20, 25, 30, 35))
        ),
        VectorPaint(5.0, 8.0, 35.0, 8.75, 0.0, 1.0, 40, "touching-baseline"),
        framed=False,
    )
    check(
        "a rail that stops a hair short of its baseline still meets it",
        frame_resolves(touching_rail_page, six_slots),
    )

    # Mutation test, in the style of the ink-band sweep above: each entry
    # restores the behaviour the fixture beside it was written to refute, and
    # that fixture must stop resolving. The plain comb is re-measured under
    # every mutation, so a mutation that simply breaks comb framing outright
    # is not mistaken for a mutation the fixture caught.
    _measured_rule_shaped = _is_rule_shaped
    _measured_junction_sample = _junction_sample_y

    def _shape_without_noise_allowance(paint: VectorPaint) -> bool:
        return paint.x1 - paint.x0 > paint.y1 - paint.y0

    def _meets_only_on_touch(
            left_x0: float, left_x1: float, left_y0: float, left_y1: float,
            right_x0: float, right_x1: float,
            right_y0: float, right_y1: float) -> bool:
        return max(right_x0 - left_x1, left_x0 - right_x1, 0.0) \
            <= SOURCE_COORD_EPS_PT

    def _sample_intersection_midpoint(
            member_y0: float, member_y1: float,
            base_y0: float, base_y1: float) -> float:
        return (max(member_y0, base_y0) + min(member_y1, base_y1)) / 2.0

    frame_mutations = (
        ("a junction block hides the chain piece it overlaps",
         {"_adds_no_ink_outside_rule": lambda owner, rule: False},
         lambda: frame_resolves(noisy_overlap_chain_page, six_slots)),
        ("a square junction block reads as a stroke across its rule",
         {"_is_rule_shaped": _shape_without_noise_allowance},
         lambda: frame_resolves(square_block_chain_page, six_slots)),
        ("any gap at all cuts a rule chain",
         {"_rule_ink_meets": _meets_only_on_touch},
         lambda: frame_resolves(hairline_gap_chain_page, six_slots)),
        ("no vertical is ever a wall",
         {"_carries_band_into_rule_above":
          lambda page, tone, rail, band_y0: False},
         lambda: frame_resolves(nested_cell_page, (2, [10.0]),
                                nested_subject)),
        ("a chain is never cut into the cells its walls make",
         {"_components_cut_at_source_walls":
          lambda page, tone, band_y0, band_y1, components: list(components)},
         lambda: frame_resolves(row_rule_page, six_slots, row_subject)),
        ("a frame is never reduced to the cell its owner claims",
         {"_frame_cut_at_source_walls":
          lambda page, tone, band_y0, band_y1, x0, x1, topology, candidate:
          candidate},
         lambda: frame_resolves(nested_cell_page, (2, [10.0]),
                                nested_subject)),
        ("a junction is measured between two rectangles that only touch",
         {"_junction_sample_y": _sample_intersection_midpoint},
         lambda: frame_resolves(touching_rail_page, six_slots)),
        ("any break at all ends a stroke's run",
         {"_stroke_break_ends_run":
          lambda gap, reachable: gap > SOURCE_COORD_EPS_PT},
         lambda: frame_resolves(split_wall_page, (2, [10.0]),
                                nested_subject)),
        ("a painted break in a stroke reads as unclaimed paper",
         {"_erasure_ends_run": lambda owners: False},
         lambda: frame_resolves(erased_wall_page, six_slots, row_subject)),
        ("a wall the sheet cut off is never re-read from its own strokes",
         {"_stroke_stands_at_border_weight":
          lambda page, tone, rail, band_y0, divider_weight, divider_reach:
          False},
         lambda: frame_resolves(
             knocked_out_wall_page, (3, [10.0, 15.0]), left_cell)),
        ("a stroke column stops at every break, erased or not",
         {"_stroke_column_break_is_erased":
          lambda page, ink_x0, ink_x1, gap_y0, gap_y1: False},
         lambda: frame_resolves(
             knocked_out_wall_page, (3, [10.0, 15.0]), left_cell)),
        ("any paint at all bridges a break in a stroke column",
         {"_stroke_column_break_is_erased":
          lambda page, ink_x0, ink_x1, gap_y0, gap_y1: True},
         lambda: frame_resolves(
             bridged_divider_page, (3, [10.0, 15.0]), left_cell)),
        ("border weight alone makes a wall",
         {"_stroke_stands_at_border_weight":
          lambda page, tone, rail, band_y0, divider_weight, divider_reach: (
              rail is not None
              and float(rail["ink_x1"]) - float(rail["ink_x0"])
              > divider_weight + SOURCE_COORD_EPS_PT)},
         lambda: frame_resolves(
             heavy_divider_page, (3, [10.0, 15.0]), left_cell)),
        ("standing above the band alone makes a wall",
         {"_stroke_stands_at_border_weight":
          lambda page, tone, rail, band_y0, divider_weight, divider_reach: (
              _source_stroke_column_reach(page, tone, rail, band_y0)
              < band_y0 - SOURCE_COORD_EPS_PT)},
         lambda: frame_resolves(
             cut_divider_page, (3, [10.0, 15.0]), left_cell)),
        ("the band supplies the scale the dividers did not",
         {"_stroke_stands_at_border_weight":
          lambda page, tone, rail, band_y0, divider_weight, divider_reach: (
              rail is not None
              and float(rail["ink_x1"]) - float(rail["ink_x0"])
              > divider_weight + SOURCE_COORD_EPS_PT
              and _source_stroke_column_reach(page, tone, rail, band_y0)
              < min(divider_reach, band_y0) - SOURCE_COORD_EPS_PT)},
         lambda: frame_resolves(
             flush_divider_page, (6, [10.0, 15.0, 20.0, 25.0, 30.0]),
             whole_row)),
    )
    frame_module = globals()
    for label, patches, probe in frame_mutations:
        restore = {name: frame_module[name] for name in patches}
        frame_module.update(patches)
        try:
            still_resolves = probe()
            control_resolves = frame_resolves(plain_comb_page, six_slots)
        finally:
            frame_module.update(restore)
        check(
            f"weakening source comb framing ({label}) is caught by the suite",
            still_resolves is False,
        )
        check(
            f"weakening source comb framing ({label}) leaves the plain comb",
            control_resolves is True,
        )
    check(
        "source comb framing is restored after the mutation sweep",
        frame_resolves(plain_comb_page, six_slots)
        and frame_resolves(noisy_overlap_chain_page, six_slots)
        and frame_resolves(square_block_chain_page, six_slots)
        and frame_resolves(hairline_gap_chain_page, six_slots)
        and frame_resolves(touching_rail_page, six_slots)
        and frame_resolves(nested_cell_page, (2, [10.0]), nested_subject)
        and frame_resolves(split_wall_page, (2, [10.0]), nested_subject)
        and frame_resolves(row_rule_page, six_slots, row_subject)
        and frame_resolves(erased_wall_page, six_slots, row_subject)
        and frame_resolves(
            knocked_out_wall_page, (3, [10.0, 15.0]), left_cell)
        and frame_resolves(
            knocked_out_wall_page, (3, [25.0, 30.0]), right_cell)
        and frame_resolves(heavy_divider_page, (3, [10.0, 15.0]), left_cell)
        and frame_resolves(cut_divider_page, (3, [10.0, 15.0]), left_cell)
        and frame_resolves(
            bridged_divider_page, (3, [10.0, 15.0]), left_cell)
        and frame_resolves(
            flush_divider_page, (6, [10.0, 15.0, 20.0, 25.0, 30.0]),
            whole_row),
    )
    _ = (_measured_rule_shaped, _measured_junction_sample)

    expanded_frame_page = source_page(
        *maximal_frame_page.paints,
        source_paint(45, order=30),
        framed=False,
    )
    try:
        printed_compartments(
            expanded_frame_page, comb_subject(x1=50.0))
    except CombTopologyError as exc:
        expanded_frame_failed = (
            "absorbs unframed source corridors" in str(exc)
            and exc.evidence["unframed_corridors"] == [45.0]
        )
    else:
        expanded_frame_failed = False
    check(
        "an expanded owner cannot absorb a corridor outside its source frame",
        expanded_frame_failed,
    )
    (_expanded_registry, expanded_owner_cell, expanded_owner,
     _expanded_reason) = owner_registry_fixture(comb_subject(x1=50.0))
    if expanded_owner is not None:
        try:
            printed_compartments(
                expanded_frame_page,
                expanded_owner_cell,
                owner_certificate=expanded_owner,
            )
        except CombTopologyError as exc:
            certified_expanded_frame_failed = (
                "absorbs unframed source corridors" in str(exc)
                and exc.evidence.get("unframed_corridors") == [45.0]
            )
        else:
            certified_expanded_frame_failed = False
        check(
            "reviewed ownership cannot absorb unframed source corridors",
            certified_expanded_frame_failed,
        )

    hanging_frame_page = source_page(
        source_paint(5, order=0),
        source_paint(10, order=1),
        source_paint(15, a=2.0, b=7.0, order=2),
        source_paint(20, order=3),
        source_paint(35, order=4),
        VectorPaint(5.0, 7.88, 35.0, 8.12,
                    0.0, 1.0, 20, "hanging-frame-baseline"),
        framed=False,
    )
    hanging_frame = printed_compartments(
        hanging_frame_page, comb_subject())
    check(
        "a majority-height divider hanging above the baseline stays unframed",
        hanging_frame == (3, [10.0, 20.0]),
    )

    dense = printed_compartments(
        source_page(
            source_paint(30, a=2.0, b=5.0),
            source_paint(35, a=2.0, b=5.0),
            source_paint(5, a=5.0, b=8.0),
            source_paint(10, a=5.0, b=8.0),
            source_paint(15, a=5.0, b=8.0),
            source_paint(20, a=5.0, b=8.0),
            source_paint(25, a=5.0, b=8.0),
            VectorPaint(5.0, 7.88, 25.0, 8.12,
                        0.0, 1.0, 10, "bottom-frame"),
            framed=False,
        ),
        comb_subject(),
    )
    check("a complete inset source U-frame owns one composite-cell band",
          dense == (4, [10.0, 15.0, 20.0]))

    continued_frame = printed_compartments(
        source_page(
            source_paint(5, a=2.0, b=8.0),
            source_paint(20, a=2.0, b=8.0),
            source_paint(25, a=2.0, b=8.0),
            source_paint(10, a=5.0, b=8.0),
            source_paint(15, a=5.0, b=8.0),
            VectorPaint(5.0, 7.88, 25.0, 8.12,
                        0.0, 1.0, 10, "bottom-frame"),
            framed=False,
        ),
        comb_subject(),
    )
    check(
        "one physical U-frame retains every source corridor meeting its baseline",
        continued_frame == (4, [10.0, 15.0, 20.0]),
    )

    try:
        printed_compartments(
            source_page(
                source_paint(30, a=2.0, b=5.0),
                source_paint(35, a=2.0, b=5.0),
                source_paint(10, a=5.0, b=8.0),
                source_paint(15, a=5.0, b=8.0),
                source_paint(20, a=5.0, b=8.0),
                source_paint(25, a=5.0, b=8.0),
                framed=False,
            ),
            comb_subject(),
        )
    except ValueError as exc:
        unframed_competition = (
            "without one complete source U-frame owner" in str(exc)
        )
    else:
        unframed_competition = False
    check(
        "a denser competing band without source frame ownership fails closed",
        unframed_competition,
    )

    dual_frame_page = source_page(
        VectorPaint(2.0, 1.0, 38.0, 4.0,
                    0.5, 1.0, 0, "upper-grey-band"),
        source_paint(4, a=1.0, b=4.0, tone=1.0, order=1),
        source_paint(15, a=1.0, b=4.0, tone=1.0, order=2),
        source_paint(30, a=1.0, b=4.0, tone=1.0, order=3),
        VectorPaint(4.0, 3.88, 30.0, 4.12,
                    1.0, 1.0, 4, "upper-white-baseline"),
        source_paint(5, a=6.0, b=9.0, order=5),
        source_paint(10, a=6.0, b=9.0, order=6),
        source_paint(20, a=6.0, b=9.0, order=7),
        source_paint(30, a=6.0, b=9.0, order=8),
        VectorPaint(5.0, 8.88, 30.0, 9.12,
                    0.0, 1.0, 9, "lower-black-baseline"),
        framed=False,
    )
    try:
        printed_compartments(dual_frame_page, comb_subject())
    except ValueError as exc:
        dual_frames_failed = "multiple complete source U-frames" in str(exc)
    else:
        dual_frames_failed = False
    check(
        "explicit white and black U-frames remain competing source owners",
        dual_frames_failed,
    )

    microscopic_seam = printed_compartments(
        source_page(
            VectorPaint(0.0, 2.0, 9.9999695, 8.0,
                        0.5, 1.0, 0, "left-grey"),
            VectorPaint(10.0000305, 2.0, 40.0, 8.0,
                        0.5, 1.0, 1, "right-grey"),
            source_paint(20, order=2),
        ),
        comb_subject(),
    )
    check(
        "a microscopic unpainted paper seam is never a sole divider corridor",
        microscopic_seam == (2, [20.0]),
    )

    interrupted_page = source_page(
        source_paint(10, a=2.0, b=8.0, order=0),
        source_paint(20, a=2.0, b=8.0, order=1),
        VectorPaint(0.0, 3.0, 40.0, 4.0,
                    1.0, 1.0, 2, "first-horizontal-interruptor"),
        VectorPaint(0.0, 5.0, 40.0, 6.0,
                    1.0, 1.0, 3, "second-horizontal-interruptor"),
        framed=False,
    )
    try:
        printed_compartments(interrupted_page, comb_subject())
    except CombTopologyError as exc:
        interrupted_evidence = exc.evidence
    else:
        interrupted_evidence = None
    interrupted_lineages = (
        interrupted_evidence["bands"][0]["vertical_lineages"]
        if interrupted_evidence else []
    )
    check(
        "orthogonally interrupted source lineages fail closed with exact runs",
        len(interrupted_lineages) == 2
        and all(
            lineage["continuous_runs"]
            == [[2.0, 3.0], [4.0, 5.0], [6.0, 8.0]]
            and lineage["interruptions"] == [[3.0, 4.0], [5.0, 6.0]]
            and lineage["strict_majority"] is False
            and all(
                segment["last_owners"][0]["orientation"] == "horizontal"
                for segment in lineage["interruption_segments"]
            )
            for lineage in interrupted_lineages
        ),
    )

    square_page = source_page(
        source_paint(10, a=2.0, b=5.0),
        source_paint(20, a=2.0, b=5.0),
        source_paint(30, a=2.25, b=4.75, width=2.5),
    )
    square_result = printed_compartments(square_page, comb_subject())
    check(
        "a strict-majority square is not promoted as a vertical divider",
        square_result == (3, [10.0, 20.0]),
    )

    near_square_page = source_page(
        source_paint(10, a=2.0, b=5.0),
        source_paint(20, a=2.0, b=5.0),
        source_paint(30, a=2.25, b=4.75, width=2.49),
    )
    near_square_result = printed_compartments(
        near_square_page, comb_subject())
    check(
        "epsilon-only aspect does not turn near-square decoration vertical",
        near_square_result == (3, [10.0, 20.0]),
    )

    white_knockout_failed = False
    try:
        white_knockout_result = printed_compartments(
            source_page(
                VectorPaint(0.0, 2.0, 40.0, 8.0,
                            0.5, 1.0, 0, "grey-band"),
                source_paint(10, order=1),
                source_paint(20, tone=1.0, order=2),
                framed=False,
            ),
            comb_subject(),
        )
    except ValueError:
        white_knockout_failed = True
        white_knockout_result = None
    check(
        "a white knockout on non-white paper is counted or fails closed",
        white_knockout_failed
        or white_knockout_result == (3, [10.0, 20.0]),
    )

    # Adversarial PDF-operator fixtures for the four fail-closed boundaries of
    # the source compositor. They deliberately exercise `ordered_vector_paints`
    # instead of constructing its output by hand.
    import fitz

    class FakeSourcePage:
        def __init__(self, drawings: Sequence[dict[str, Any]],
                     bboxlog: Sequence[tuple[str, Any]],
                     texttrace: Sequence[dict[str, Any]] = ()) -> None:
            self.drawings = list(drawings)
            self.bboxlog = list(bboxlog)
            self.texttrace = list(texttrace)

        def get_drawings(self, extended: bool = False) -> list[dict[str, Any]]:
            if not extended:
                raise AssertionError("source compositor omitted extended clips")
            return copy.deepcopy(self.drawings)

        def get_bboxlog(self) -> list[tuple[str, Any]]:
            return list(self.bboxlog)

        def get_texttrace(self) -> list[dict[str, Any]]:
            return copy.deepcopy(self.texttrace)

    def fake_fill(seqno: int, rect: Any, *,
                  colour: tuple[float, float, float] = (0.0, 0.0, 0.0),
                  opacity: float = 1.0,
                  items: Sequence[tuple[Any, ...]] | None = None,
                  even_odd: bool = True,
                  level: int = 0) -> dict[str, Any]:
        return {
            "type": "f", "seqno": seqno, "level": level,
            "items": list(items) if items is not None else [("re", rect, 1)],
            "even_odd": even_odd, "fill_opacity": opacity,
            "fill": colour, "rect": rect, "closePath": None,
            "color": None, "width": None, "lineCap": None,
            "lineJoin": None, "dashes": None, "stroke_opacity": None,
        }

    def fake_stroke(seqno: int, x: float, *,
                    a: float = 2.0, b: float = 8.0,
                    width: float = 0.24, level: int = 0
                    ) -> dict[str, Any]:
        return {
            "type": "s", "seqno": seqno, "level": level,
            "items": [("l", fitz.Point(x, a), fitz.Point(x, b))],
            "rect": fitz.Rect(x, a, x, b),
            "fill": None, "fill_opacity": None, "even_odd": None,
            "color": (0.0, 0.0, 0.0), "width": width,
            "lineCap": (0, 0, 0), "lineJoin": 0,
            "dashes": "[] 0", "stroke_opacity": 1.0,
            "closePath": False,
        }

    root_rect = fitz.Rect(0.0, 0.0, 40.0, 10.0)
    clipped_rect = fitz.Rect(9.88, 2.0, 10.12, 8.0)
    clipped_page = ordered_vector_paints(FakeSourcePage(
        [
            {
                "type": "group", "level": 0, "rect": root_rect,
                "isolated": True, "knockout": False,
                "blendmode": "Normal", "opacity": 1.0,
            },
            {
                "type": "clip", "level": 1, "even_odd": True,
                "items": [("re", root_rect, 1)], "scissor": root_rect,
            },
            {
                "type": "clip", "level": 2, "even_odd": True,
                "items": [("re", fitz.Rect(15.0, 0.0, 40.0, 10.0), 1)],
                "scissor": fitz.Rect(15.0, 0.0, 40.0, 10.0),
            },
            fake_fill(0, clipped_rect, level=3),
        ],
        [("fill-path", clipped_rect)],
    ))
    try:
        printed_compartments(clipped_page, comb_subject())
    except ValueError as exc:
        clipped_failed = "no plausible source-derived comb band" in str(exc)
    else:
        clipped_failed = False
    check("nested even-odd rectangular scissors remove a clipped-away divider",
          clipped_failed and not clipped_page.paints)

    stroked_clip_page = ordered_vector_paints(FakeSourcePage(
        [
            {
                "type": "clip", "level": 0, "even_odd": True,
                "items": [("re", root_rect, 1)], "scissor": root_rect,
            },
            fake_stroke(0, 10.0, level=1),
            fake_stroke(1, 20.0, level=0),
        ],
        [
            ("stroke-path", fitz.Rect(9.88, 1.88, 10.12, 8.12)),
            ("stroke-path", fitz.Rect(19.88, 1.88, 20.12, 8.12)),
        ],
    ))
    stroked_clip_page = owned_test_page(stroked_clip_page)
    stroked_clip = printed_compartments(
        stroked_clip_page, comb_subject())
    check(
        "zero-width line paths use stroked extent for clip inclusion",
        stroked_clip == (3, [10.0, 20.0]),
    )

    transparent_group_page = ordered_vector_paints(FakeSourcePage(
        [
            {
                "type": "group", "level": 0, "rect": root_rect,
                "isolated": True, "knockout": False,
                "blendmode": "Normal", "opacity": 0.0,
            },
            fake_stroke(0, 10.0, level=1),
            fake_stroke(1, 20.0, level=0),
        ],
        [
            ("stroke-path", fitz.Rect(9.88, 1.88, 10.12, 8.12)),
            ("stroke-path", fitz.Rect(19.88, 1.88, 20.12, 8.12)),
        ],
    ))
    try:
        printed_compartments(transparent_group_page, comb_subject())
    except ValueError as exc:
        transparent_group_failed = "transparency group" in str(exc)
    else:
        transparent_group_failed = False
    check(
        "nested zero-area line paint inherits its non-normal group",
        transparent_group_failed,
    )

    complex_clip_page = ordered_vector_paints(FakeSourcePage(
        [
            {
                "type": "clip", "level": 0, "even_odd": True,
                "items": [
                    ("re", root_rect, 1),
                    ("re", fitz.Rect(9.0, 1.0, 11.0, 9.0), 1),
                ],
                "scissor": root_rect,
            },
            fake_fill(0, clipped_rect, level=1),
        ],
        [("fill-path", clipped_rect)],
    ))
    try:
        printed_compartments(complex_clip_page, comb_subject())
    except ValueError as exc:
        complex_clip_failed = "compound or non-rectilinear source clip" in str(exc)
    else:
        complex_clip_failed = False
    check("compound even-odd clip topology is conservatively unevaluable",
          complex_clip_failed)

    divider_rect = fitz.Rect(9.88, 2.0, 10.12, 8.0)
    covering_rect = fitz.Rect(8.0, 2.0, 12.0, 8.0)
    image_page = ordered_vector_paints(FakeSourcePage(
        [fake_fill(0, divider_rect)],
        [("fill-path", divider_rect), ("fill-image", covering_rect)],
    ))
    text_page = ordered_vector_paints(FakeSourcePage(
        [fake_fill(0, divider_rect)],
        [("fill-path", divider_rect), ("fill-text", covering_rect)],
    ))
    for label, foreign_page, foreign_kind in (
            ("image", image_page, "fill-image"),
            ("text", text_page, "fill-text")):
        try:
            printed_compartments(foreign_page, comb_subject())
        except ValueError as exc:
            foreign_failed = foreign_kind in str(exc)
        else:
            foreign_failed = False
        check(f"later {label} paint intersecting the source band fails closed",
              foreign_failed)

    def fake_texttrace(
            seqno: int, rect: Any, colour: tuple[float, float, float],
            *, linewidth: float | None = None, text_type: int = 0
            ) -> dict[str, Any]:
        return {
            "seqno": seqno, "color": colour, "opacity": 1.0,
            "linewidth": linewidth, "type": text_type,
            "chars": ((65, 1, (rect.x0, rect.y1), tuple(rect)),),
        }

    same_tone_text_page = ordered_vector_paints(FakeSourcePage(
        [fake_fill(0, divider_rect)],
        [("fill-path", divider_rect), ("fill-text", covering_rect)],
        [fake_texttrace(1, covering_rect, (0.0, 0.0, 0.0))],
    ))
    try:
        printed_compartments(same_tone_text_page, comb_subject())
    except ValueError as exc:
        broad_same_tone_failed = "fill-text" in str(exc)
    else:
        broad_same_tone_failed = False
    check(
        "same-tone glyph bounds crossing a divider fail closed",
        broad_same_tone_failed,
    )

    separate_trace = fitz.Rect(3.0, 2.0, 4.0, 8.0)
    broad_text_bbox = fitz.Rect(3.0, 2.0, 12.0, 8.0)
    separate_same_tone_page = ordered_vector_paints(FakeSourcePage(
        [fake_fill(0, divider_rect)],
        [("fill-path", divider_rect), ("fill-text", broad_text_bbox)],
        [fake_texttrace(1, separate_trace, (0.0, 0.0, 0.0))],
    ))
    separate_same_tone_page = owned_test_page(separate_same_tone_page)
    separate_same_tone = printed_compartments(
        separate_same_tone_page, comb_subject())
    check(
        "correlated same-tone glyphs safely separated from dividers are allowed",
        separate_same_tone == (2, [10.0]),
    )

    erasing_text_page = ordered_vector_paints(FakeSourcePage(
        [fake_fill(0, divider_rect)],
        [("fill-path", divider_rect), ("fill-text", covering_rect)],
        [fake_texttrace(1, covering_rect, (1.0, 1.0, 1.0))],
    ))
    try:
        printed_compartments(erasing_text_page, comb_subject())
    except ValueError as exc:
        erasing_text_failed = "fill-text" in str(exc)
    else:
        erasing_text_failed = False
    check("later text of another tone crossing a divider fails closed",
          erasing_text_failed)

    # The bbox log is the conservative paint envelope. A tighter traced glyph
    # may localise same-tone text, but must never shrink different/unknown-tone
    # blocking away from a divider.
    mismatched_bbox = fitz.Rect(9.0, 2.0, 11.0, 8.0)
    mismatched_trace = fitz.Rect(8.0, 2.0, 9.9, 8.0)
    mismatched_text_page = ordered_vector_paints(FakeSourcePage(
        [fake_fill(0, divider_rect)],
        [("fill-path", divider_rect), ("fill-text", mismatched_bbox)],
        [fake_texttrace(1, mismatched_trace, (1.0, 1.0, 1.0))],
    ))
    try:
        printed_compartments(mismatched_text_page, comb_subject())
    except ValueError as exc:
        mismatched_bbox_failed = "fill-text" in str(exc)
    else:
        mismatched_bbox_failed = False
    check("different-tone text keeps its full bboxlog paint envelope",
          mismatched_bbox_failed)

    stroke_bbox = fitz.Rect(8.8, 2.0, 9.2, 8.0)
    stroke_text_page = ordered_vector_paints(FakeSourcePage(
        [fake_fill(0, divider_rect)],
        [("fill-path", divider_rect), ("stroke-text", stroke_bbox)],
        [fake_texttrace(
            1, stroke_bbox, (1.0, 1.0, 1.0),
            linewidth=2.0, text_type=1)],
    ))
    try:
        printed_compartments(stroke_text_page, comb_subject())
    except ValueError as exc:
        stroke_text_failed = "stroke-text" in str(exc)
    else:
        stroke_text_failed = False
    check("stroke-text bboxlog envelope includes its traced line width",
          stroke_text_failed)

    cancelled_rect = fitz.Rect(9.88, 2.0, 10.12, 8.0)
    surviving_rect = fitz.Rect(19.88, 2.0, 20.12, 8.0)
    cancelled_page = ordered_vector_paints(FakeSourcePage(
        [
            fake_fill(
                0, cancelled_rect, even_odd=True,
                items=[
                    ("re", cancelled_rect, 1),
                    ("re", cancelled_rect, 1),
                ],
            ),
            fake_fill(1, surviving_rect),
        ],
        [("fill-path", cancelled_rect), ("fill-path", surviving_rect)],
    ))
    cancelled_page = owned_test_page(cancelled_page)
    cancelled = printed_compartments(cancelled_page, comb_subject())
    check("overlapping regions of one even-odd fill cancel exactly",
          cancelled == (2, [20.0]))

    translucent_rect = fitz.Rect(9.88, 2.0, 10.12, 8.0)
    grey_rect = fitz.Rect(19.88, 2.0, 20.12, 8.0)
    translucent_page = ordered_vector_paints(FakeSourcePage(
        [
            fake_fill(
                0, translucent_rect, opacity=0.5, even_odd=False,
                items=[
                    ("re", translucent_rect, 1),
                    ("re", translucent_rect, 1),
                ],
            ),
            fake_fill(1, grey_rect, colour=(0.5, 0.5, 0.5)),
        ],
        [("fill-path", translucent_rect), ("fill-path", grey_rect)],
    ))
    translucent_page = owned_test_page(translucent_page)
    try:
        translucent = printed_compartments(
            translucent_page, comb_subject())
    except CombTopologyError:
        translucent = None
    check("one compound fill applies opacity once across overlapping regions",
          translucent is None or translucent == (3, [10.0, 20.0]))

    many = tuple(source_paint(float(x)) for x in range(4, 84, 4))
    many_count, many_xs = printed_compartments(
        source_page(*many), comb_subject(x1=88.0))
    check("printed divider evidence is exhaustive beyond sixteen entries",
          many_count == 21 and many_xs == [float(x) for x in range(4, 84, 4)])

    # ----------------------------------------------------------------------
    # assertions 9 and 10 -- the field layer.
    #
    # These two fixtures are built as a REAL PDF and run through the real
    # `ordered_vector_paints` / `drawn_glyph_boxes` / `input_boxes` path,
    # because the whole point of both assertions is that their expectation
    # comes from the source file's own operators.  A hand-built VectorPage
    # would let the fixture agree with the assertion about what the source
    # says, which is the defect class this file keeps finding.
    #
    # The page draws, at y 20..40:
    #   A  10..60   an enclosed blank box with ONE printed compartment
    #               divider at x=30
    #   B  80..110  an enclosed blank box, no divider
    #   C  130..160 an enclosed blank box whose divider at x=145 is then
    #               painted over in white -- present in the operator stream,
    #               absent from the page
    # and at y 60..80:
    #   D  10..60   an enclosed box with the glyphs "TOTAL" inside it
    #   E  80..110  an enclosed box filled mid grey
    # A, B and C are row peers.  D and E are the false-positive guards for
    # the inventory: a caption box and an official "no entry applies" band
    # must never be claimed as boxes a taxpayer should be able to type in.
    field_doc = fitz.open()
    field_page = field_doc.new_page(width=200, height=100)
    field_shape = field_page.new_shape()
    for frame in (fitz.Rect(10, 20, 60, 40), fitz.Rect(80, 20, 110, 40),
                  fitz.Rect(130, 20, 160, 40), fitz.Rect(10, 60, 60, 80),
                  fitz.Rect(80, 60, 110, 80)):
        field_shape.draw_rect(frame)
    field_shape.finish(color=(0.0, 0.0, 0.0), width=0.5)
    field_shape.draw_line(fitz.Point(30, 32), fitz.Point(30, 40))
    field_shape.draw_line(fitz.Point(145, 32), fitz.Point(145, 40))
    field_shape.finish(color=(0.0, 0.0, 0.0), width=0.72)
    field_shape.draw_rect(fitz.Rect(131, 21, 159, 39))
    field_shape.finish(color=None, fill=(1.0, 1.0, 1.0))
    field_shape.draw_rect(fitz.Rect(81, 61, 109, 79))
    field_shape.finish(color=None, fill=(0.6, 0.6, 0.6))
    # A 1.4 x 3.0pt dark speck inside box B. Dark and narrow, but not
    # materially taller than it is wide, so it is not a compartment divider.
    # 109 paints across 15 corpus forms are excluded by exactly this clause.
    field_shape.draw_rect(fitz.Rect(94.3, 28.0, 95.7, 31.0))
    field_shape.finish(color=None, fill=(0.0, 0.0, 0.0))
    field_shape.commit()
    field_page.insert_text(fitz.Point(20, 74), "TOTAL", fontsize=8)
    field_pdf = field_doc.tobytes()
    field_doc.close()

    def field_ir() -> dict[str, Any]:
        return {
            "form": {"code": "FIELD", "revision": "0000"},
            "source": {"file": "external:none.pdf", "sha256": "0" * 64},
            "paper": {"width_pt": 200.0, "height_pt": 100.0},
            "pages": [{
                "index": 1, "width_pt": 200.0, "height_pt": 100.0,
                "rotation": 0, "rules": [], "area_fills": [], "images": [],
                "text_runs": [], "stats": {},
            }],
        }

    def field_cell(cell_id: str, rect: Rect, inner: str) -> str:
        left, top, right, bottom = rect
        return (
            f'<div id="{cell_id}" class="c f" data-cell-kind="field" '
            f'data-field-kind="text" style="left:{left}pt;top:{top}pt;'
            f'width:{right - left}pt;height:{bottom - top}pt">{inner}</div>')

    def field_input(cell_id: str, rect: Rect, inset: Rect) -> str:
        top, right, bottom, left = inset
        return field_cell(cell_id, rect, (
            f'<input type="text" class="fi" id="{cell_id}-i" name="{cell_id}" '
            f'style="inset:{top}pt {right}pt {bottom}pt {left}pt">'))

    def field_bundle(cells: Sequence[tuple[str, Rect, str]]) -> Bundle:
        return Bundle(
            slug="field-fixture", ir=field_ir(),
            layout={"pages": [{"index": 1, "cells": [
                {"id": cell_id, "x0": rect[0], "y0": rect[1],
                 "x1": rect[2], "y1": rect[3],
                 "border": {"top": {}, "bottom": {}, "left": {}, "right": {}},
                 "is_empty": True, "rectangular": True, "kind": "field",
                 "text_run_ids": []}
                for cell_id, rect, _ in cells]}]},
            plan={"inline": []},
            form_html=(
                '<div class="page page-1" id="page-1" '
                'style="width:200pt;height:100pt">'
                + "".join(markup for _, _, markup in cells)
                + '</div>'),
            guide_html=None, pdf=field_pdf)

    # The three peers, each with its own input.  A1 must hold (no input
    # reaches inside A's divider), and A2 must hold (every peer is fillable).
    clean_cells = [
        ("p1c0", (10.0, 20.0, 29.4, 40.0),
         field_input("p1c0", (10.0, 20.0, 29.4, 40.0), (2.0, 1.0, 2.0, 1.0))),
        ("p1c1", (30.6, 20.0, 60.0, 40.0),
         field_input("p1c1", (30.6, 20.0, 60.0, 40.0), (2.0, 1.0, 2.0, 1.0))),
        ("p1c2", (80.0, 20.0, 110.0, 40.0),
         field_input("p1c2", (80.0, 20.0, 110.0, 40.0), (0.0, 0.0, 0.0, 0.0))),
        ("p1c3", (130.0, 20.0, 160.0, 40.0),
         field_input("p1c3", (130.0, 20.0, 160.0, 40.0), (0.0, 0.0, 0.0, 0.0))),
    ]
    clean_bundle = field_bundle(clean_cells)
    clean_divider = check_inputs_span_no_printed_divider(clean_bundle)
    clean_peers = check_printed_box_peers_all_fillable(clean_bundle)
    check(
        "a comb split at its printed divider holds, and the divider is seen",
        clean_divider["holds"] is True
        and clean_divider["inputs_checked"] == 4
        and clean_divider["printed_dividers_detected"] >= 3,
    )
    # p1c2's input is drawn edge to edge over box B's own printed frame and
    # over the speck inside it, and p1c3's over box C's white-knocked-out
    # divider.  None of the three may be reported: an input is not guilty of
    # its own walls, a blob is not a divider, and a divider the page does not
    # show is not a divider.
    check(
        "an input over its own printed frame is not its own offender",
        clean_divider["holds"] is True,
    )
    check(
        "three fillable printed peers hold, and captions and grey bands are "
        "not in the inventory",
        clean_peers["holds"] is True
        and clean_peers["printed_boxes_checked"] == 3
        and clean_peers["peer_rows_checked"] == 1
        and clean_peers["boxes_unevaluable"] == 0,
    )

    # One input across the whole of box A: it spans A's printed divider.
    spanning_cells = list(clean_cells)
    spanning_cells[0:2] = [(
        "p1c0", (10.0, 20.0, 60.0, 40.0),
        field_input("p1c0", (10.0, 20.0, 60.0, 40.0), (2.0, 2.0, 2.0, 2.0)))]
    spanning = check_inputs_span_no_printed_divider(field_bundle(spanning_cells))
    check(
        "inputs_span_no_printed_divider must fail on one box over two "
        "printed compartments",
        spanning["holds"] is False
        and spanning["offender_count"] == 1
        and spanning["offenders"][0]["cell"] == "p1c0"
        and spanning["offenders"][0]["printed_dividers_spanned"] == 1
        and spanning["offenders"][0]["divider_x"] == [30.0],
    )

    # Box B loses its own input and keeps only the neighbouring cell's, which
    # clips 2 of its 30pt.  An input next door is not an input in this box --
    # if it were, the assertion would report nothing on any crowded sheet.
    unfilled_cells = [row for row in clean_cells if row[0] != "p1c2"]
    unfilled_cells.append((
        "p1c4", (74.0, 20.0, 82.0, 40.0),
        field_input("p1c4", (74.0, 20.0, 82.0, 40.0), (0.0, 0.0, 0.0, 0.0))))
    unfilled = check_printed_box_peers_all_fillable(
        field_bundle(unfilled_cells))
    check(
        "printed_box_peers_all_fillable must fail on a printed peer with no "
        "input",
        unfilled["holds"] is False
        and unfilled["offender_count"] == 1
        and unfilled["offenders"][0]["box"] == [80.0, 20.0, 110.0, 40.0]
        and unfilled["offenders"][0]["row_peers"] == 3
        and unfilled["offenders"][0]["row_peers_with_input"] == 2,
    )

    # The false-positive guard that decides whether this assertion can be
    # trusted at all: a row where NOTHING is fillable proves nothing about
    # the Bureau's intent, and must be silent rather than opinionated.
    silent = check_printed_box_peers_all_fillable(field_bundle([]))
    check(
        "a printed row with no fillable peer at all is not an offence",
        silent["holds"] is True
        and silent["printed_boxes_checked"] == 3
        and silent["peer_rows_checked"] == 1,
    )

    for name in failures:
        print(f"FAIL {name}", file=sys.stderr)
    print(f"audit self-test: {len(failures)} failure(s)", file=sys.stderr)
    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ir-dir", type=pathlib.Path, default=pathlib.Path("build/ir"))
    parser.add_argument("--html-dir", type=pathlib.Path, default=pathlib.Path("build/html"))
    parser.add_argument("--layout-dir", type=pathlib.Path,
                        default=pathlib.Path("build/layout"),
                        help="Lattice output; the assertions read cell and comb geometry.")
    parser.add_argument("--work", type=pathlib.Path, default=pathlib.Path("build/audit"))
    parser.add_argument("--guide-dir", type=pathlib.Path, default=pathlib.Path("build/guides"),
                        help="Guide plans; content moved to guide.html leaves the form denominator.")
    parser.add_argument("--source-root", type=pathlib.Path,
                        default=pathlib.Path.home() / "Downloads/forms",
                        help="Where the pinned official PDFs live. Three assertions "
                             "read the source's own operators rather than our IR.")
    parser.add_argument("--out", type=pathlib.Path, default=pathlib.Path("build/audit.json"))
    parser.add_argument("--only", action="append", default=None)
    parser.add_argument("--assertions-only", action="store_true",
                        help="Skip the Chromium round trip. Useful while iterating on "
                             "an assertion; not the audit.")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument(
        "--render-worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()

    if args.render_worker:
        return _run_render_worker()
    if args.self_test:
        return self_test()

    slugs = sorted(p.name[: -len(".ir.json")] for p in args.ir_dir.glob("*.ir.json"))
    if args.only:
        wanted = {s.lower() for s in args.only}
        slugs = [s for s in slugs if any(w in s for w in wanted)]

    records = []
    for i, slug in enumerate(slugs, 1):
        html = args.html_dir / f"{slug}.html"
        if not html.is_file():
            print(f"[{i:>2}/{len(slugs)}] {slug:<26} no html", file=sys.stderr)
            continue
        record = score(slug, args.ir_dir, args.html_dir, args.layout_dir,
                       args.guide_dir if args.guide_dir.is_dir() else None,
                       args.work, str(args.source_root),
                       roundtrip=not args.assertions_only)
        records.append(record)
        failed = [k for k in ASSERTION_KEYS if not record.get(k)]
        assertions = f"assertions {record.get('assertions_held', 0)}/8"
        if record["status"] == "ok" and record.get("rules_pct") is not None:
            print(f"[{i:>2}/{len(slugs)}] {slug:<26} "
                  f"rules {record['rules_pct']:>6}%  text {record['text_pct']:>6}%  "
                  f"{assertions}  {','.join(failed)}", file=sys.stderr)
        elif record["status"] == "ok":
            print(f"[{i:>2}/{len(slugs)}] {slug:<26} {assertions}  "
                  f"{','.join(failed)}", file=sys.stderr)
        else:
            print(f"[{i:>2}/{len(slugs)}] {slug:<26} {assertions}  "
                  f"ERROR {str(record['error'])[:60]}", file=sys.stderr)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(records, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
