#!/usr/bin/env python3
"""Turn the flat rule list in the IR into the box model the form is drawn on.

The BIR generator does not draw tables. It draws several hundred independent
filled bars, and every container the eye sees -- the outer box, a column, a
row, a character cell -- is an emergent property of where those bars happen to
line up. This module recovers the containers, because the containers are what
text gets laid out inside.

Three findings from the real 2551Q drive the whole design:

1. Tone, not thickness, says whether a bar is a border. `role == "decorative"`
   bars (gray 0.65-0.85) are grey ornament; painting them black is the exact
   mistake this project has made before, so they are carried on the page in
   their own list and never enter the lattice.

2. A vertical bar is a *comb divider* -- one tick between two character slots
   of a single field -- iff its bottom edge lands inside a structural
   horizontal that spans it while its top edge lands inside nothing. Combs
   hang from nothing; real column borders are supported at both ends. On the
   real 2551Q this splits 377/379 of page 1's 0.24pt verticals and 195/197 of
   page 2's as combs, and the handful it leaves as borders are exactly the
   "For BIR Use Only" panel dividers -- which are 0.24pt but genuinely
   structural. Thickness fails in both directions: money digit-group
   separators are combs at 0.96pt and 1.44pt.

3. Where a column border crosses a comb band it is *drawn thinner inside the
   band*, so the border arrives as three or four collinear fragments with the
   middle one classified as a comb. Span coverage is therefore tested against
   the union of all collinear structural ink at a lattice position, not
   against the borders alone. Without this the Tax Due column boundary at
   x=575.98 vanishes from all six Schedule 1 rows.

Comb fields are emitted as ONE cell carrying `comb: {cells: N, ...}`, never as
N cells: a 12-digit money comb is one field with twelve slots, not twelve
containers. Slot boundaries are carried through as measured x values because
the 14.16pt slot pitch is not uniform -- the content stream carries 14.04,
14.18, 14.28 among the 14.16s, and index*pitch would drift off the paper.

Finding 2 answers a different question from "where does a slot end", and
conflating the two cost 1886 slots. A comb *divider* is discovered by hanging
from nothing; a slot *boundary* is any black column crossing the band, whatever
the IR filed it as -- see `comb_boundary_candidates` and `endpoint_band`. 471 combs
reported fewer slots than the source prints, among them every TIN on the corpus
(1707 p2c5 read 11 slots for 14 boxes, three of them double width), because
their digit-group separators are drawn heavier than a character tick and land in
some other bucket. A typed character then centres on the black bar.

A fourth finding comes from the corpus rather than from 2551Q: a boundary is
not always one bar, and the inside of a boundary is never a cell. 119 places
draw a boundary as a *stack* -- a 0.14pt hairline on a 0.96pt bar (0605
y=232.4), a double rule of two 1.44pt bars around a 1.2pt white core (0619E
y=150.1, that core sometimes explicitly painted by a `knockout` rule), a
double hairline 0.65pt apart (1600WP x=357.0), a double 0.72pt box edge (2551M
x=284.2). Others draw one rule that *jogs*: the left page frame of 1606 steps
from x=26.64 to x=27.00 half way down, 1701 CONSO steps its right frame twice.
Centre clustering keeps the bars apart in every one of those, so the walk
emitted 1100 sub-2pt cells between them, 1092 of them classified `field` --
which is how a 0.36pt field input reached the page 36 times on one sheet.

Ink settles both. `fuse_boundaries` merges two clusters into one lattice line
when the paper between them, measured where the two actually run together, is
thinner than the bars drawing them. `encloses_paper` then refuses any cell
whose bounding lines leave no paper between their ink at all, which is what a
jog leaves. Neither is allowed to move a comb or a growable band; both counts
are unchanged across the corpus.

A fifth finding is that a boundary is not always a *rule*. `extract.py` files a
filled rectangle as a rule only up to 1.5pt and calls anything heavier an area
fill, so 2550M page 2's 1.92pt table sides never reached the lattice and its
Schedule 1 cells snapped to columns belonging to rows further down the sheet --
24.3% of column 1 and 47.2% of column 4 left as writing surface no input
covered. The 1.5pt cut is about how BIR *draws* a line, not about whether the
line *bounds* a cell. `comb_boundary_candidates` had always known this and read
those fills; the cell grid had not. `wall_boundaries` closes that asymmetry for
verticals and horizontals, and the shape test that keeps a character divider
out of the lattice is measured there.

A sixth finding is that the *tone of the paper* decides whether a region is a
writing surface at all, and this module used to ask only about ink. An empty
three-bordered region on a grey band satisfied "field" exactly as a white one
did, so 297 cells across 37 forms carried an *emitted input* on decoration --
on 2200T page 2 a taxpayer can type 999,999.00 into a row the official form
shades precisely to say NO RATE APPLIES. 347 cells on 40 forms were classified
`field` there, the difference being cells the emitter already refused on other
evidence. Nothing had to be inferred to prevent it:
`extract.classify_tone` stamps every fill structural / decorative / knockout
from the literal content-stream operand and the IR carries the stamp, but
`area_fills` reached the lattice only through `wall_boundaries` and
`comb_boundary_candidates`, which read `role == "structural"` for grid
geometry. `on_shaded_paper` consults the decorative population the box model
was discarding, and a cell it answers for becomes `kind == "shaded"`.

Usage:
    python3 tools/formgen/lattice.py --ir build/ir/2551q-2018.ir.json \\
        --out build/layout/2551q-2018.layout.json --summary
    python3 tools/formgen/lattice.py --self-test --ir build/ir/2551q-2018.ir.json
"""

from __future__ import annotations

import argparse
import collections
import json
import math
import pathlib
import sys
from typing import Any, Iterable, NamedTuple, Sequence

SCHEMA_VERSION = 1

# Coordinates are already quantised to 2dp by extract.py; keep that.
QUANT = 2

# Collinear bars of one logical line vary by up to a rule width. 0.24-1.44pt
# bars are drawn centred on nominal positions, so 0.3pt clusters them without
# ever merging two genuinely distinct lines (the tightest real pair on the
# 2551Q is 0.48pt apart).
CLUSTER_TOL_PT = 0.3
# A comb compartment is a character box, and a character box has a maximum
# width. Census over every compartment the corpus declares (39,471 in cells
# plus the 5 of the one suppressed inference, 2026-08-16): the widest
# legitimate compartment anywhere is 23.76pt (2551M p1c3's year box); the
# narrowest compartment that is demonstrably NOT a character box is 25.22pt
# (0605's "DD" table column, claimed by the suppressed inference). The bound
# is the midpoint of that separating interval, +-0.73pt from both walls.
# Exactly five compartments corpus-wide exceed it, and every one is a
# printed label box or a table column the detector mistook for a comb slot:
# 2551M p2c13 (70.80/156.72 -- a schedule cell crossed by a column rule),
# 1604CF p2c73 (68.64/26.64 -- the same), 1604F p1c25 slot 31 (72.75 --
# the "7A ZIP Code" label box between two runs of real boxes).
# User decision 2026-08-16 (Sitting 2, DECISION A) adopting the rule:
# a compartment wider than this is not a character box; it CUTS the slot
# run; and a surviving run of fewer than two compartments is not a comb.
# The minimum-run clause is load-bearing, not decoration: 0605's "MM"
# column measures 23.63pt -- UNDER the legitimate maximum, unreachable by
# any width test -- and dies only because cutting its neighbours leaves it
# alone (one box is not a row of boxes).
COMB_COMPARTMENT_MAX_PT = 24.5


def compartment_runs(boundaries: Sequence[float]) -> list[tuple[int, int]]:
    """Maximal slot-index runs after cutting over-wide compartments.

    Returns [(a, b)] meaning slots a..b-1 (boundaries[a..b]) survive as one
    comb run; runs shorter than two compartments are dropped. An empty list
    means nothing here is a comb. The identity result for an ordinary comb
    is [(0, len(boundaries) - 1)].
    """
    runs: list[tuple[int, int]] = []
    start = 0
    count = len(boundaries) - 1
    for index in range(count):
        if boundaries[index + 1] - boundaries[index] > COMB_COMPARTMENT_MAX_PT:
            if index - start >= 2:
                runs.append((start, index))
            start = index + 1
    if count - start >= 2:
        runs.append((start, count))
    return runs



# Two collinear fragments of one border count as continuous within this gap.
JOIN_EPSILON_PT = 0.05

# The extractor's interval-union contract is deliberately tighter than the
# lattice's paper-geometry join. Consumers must reproduce the producer's exact
# cluster rule; accepting a 0.02pt hole here would validate provenance that
# extract.py itself can never emit.
EXTRACT_JOIN_EPSILON_PT = 0.011

# Row pitch tolerance for growable detection. The Schedule 1 band is 18.24pt
# for rows 1-5 and 18.27pt for row 6 -- real drift, not rounding.
PITCH_TOL_PT = 0.3

MIN_GROWABLE_ROWS = 3

# A run of rows with only an outer box is a page margin, not a table.
MIN_GROWABLE_COLUMNS = 3


def q(value: float) -> float:
    return round(float(value) + 0.0, QUANT)


Interval = tuple[float, float]
Point = tuple[float, float]

# One bar reduced to what the paper-versus-ink test needs: near edge, far edge,
# and the weight it is drawn at.
InkSpan = tuple[float, float, float]


def union_intervals(intervals: Iterable[Interval]) -> list[Interval]:
    """Union 1-D intervals, joining anything within JOIN_EPSILON_PT."""
    items = sorted(intervals)
    if not items:
        return []
    merged: list[list[float]] = [list(items[0])]
    for start, end in items[1:]:
        if start <= merged[-1][1] + JOIN_EPSILON_PT:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [(a, b) for a, b in merged]


def intersect_intervals(left: Sequence[Interval],
                        right: Sequence[Interval]) -> list[Interval]:
    """Positive-width intersections of two ordered interval unions."""
    intersections: list[Interval] = []
    left_index = right_index = 0
    while left_index < len(left) and right_index < len(right):
        left_start, left_end = left[left_index]
        right_start, right_end = right[right_index]
        start, end = max(left_start, right_start), min(left_end, right_end)
        if end > start:
            intersections.append((start, end))
        if left_end < right_end:
            left_index += 1
        else:
            right_index += 1
    return union_intervals(intersections)


def covers(spans: Sequence[Interval], lo: float, hi: float) -> bool:
    return any(a <= lo + CLUSTER_TOL_PT and b >= hi - CLUSTER_TOL_PT for a, b in spans)


# ---------------------------------------------------------------------------
# Rule triage
# ---------------------------------------------------------------------------


def centre(rule: dict[str, Any]) -> float:
    """Centre of a rule across its thin axis."""
    if rule["axis"] == "h":
        return (rule["y0"] + rule["y1"]) / 2.0
    return (rule["x0"] + rule["x1"]) / 2.0


def tone_role(gray: float | None) -> str:
    """Apply extract.py's exact tone bands to a path paint layer."""
    if gray is None:
        return "chromatic"
    if gray <= 0.15:
        return "structural"
    if gray >= 0.98:
        return "knockout"
    return "decorative"


def supported_at(y: float, x: float, horizontals: Sequence[dict[str, Any]]) -> bool:
    """True when point (x, y) lies inside the ink of a horizontal spanning x."""
    return any(h["x0"] - CLUSTER_TOL_PT <= x <= h["x1"] + CLUSTER_TOL_PT
               and h["y0"] <= y <= h["y1"]
               for h in horizontals)


def bottom_guide_tick_baseline(rule: dict[str, Any],
                               verticals: Sequence[dict[str, Any]],
                               horizontals: Sequence[dict[str, Any]]
                               ) -> dict[str, Any] | None:
    """The baseline rule a short bottom guide tick lands on, or None.

    Official artwork draws compartment guides as deliberately short verticals
    at the FOOT of a walled writing box, and it does not always run the stroke
    into the baseline's ink: 2316's first TIN group stops 0.25pt above its box
    bottom (tick y1 137.62 against ink 137.87-138.32) and 1600WP item 5 stops
    0.345pt short, so `supported_at` -- which demands the endpoint inside the
    ink -- files both as borders supported at neither end, the group loses its
    compartments, and the TIN reaches the taxpayer as one unbounded input
    (F111, F178, F181).

    The recognition here is a PATTERN, not a widened tolerance -- a blanket
    y-tolerance was tried and refuted (it flipped 45 real combs).  Every clause
    is a physical statement about the bottom-guide-tick artwork:

      * the tick hangs from nothing: unsupported above, and not even a
        near-touch above (an almost-touching rule overhead makes it a broken
        column fragment, not a hanging guide);
      * it lands ON a baseline: the paper between the tick's end and the
        baseline's ink is thinner than the tick's own stroke -- the exact
        ink-versus-paper principle `distinct_boundary`/`band_ink` already use
        to decide when two marks are one boundary.  A 0.24pt hairline gets
        0.24pt of grace, never a corpus constant;
      * it sits in a walled band: on each side, inside the baseline's own
        span, one FULL-HEIGHT stroke lands on the same baseline, rises above
        the tick's top, and is itself supported at its own top.  The rise is
        what makes the tick SHORT -- a guide inside a taller box -- and the
        top support is what makes the wall a wall: without it, a neighbouring
        hanging divider could stand in as the "wall" and hide the gap
        disorder that refuses a stale mark (0605's knocked-out 3pt mark sits
        3.12pt from a genuine centavo divider that would otherwise wall it
        in);
      * sibling ticks landing on the same baseline between the same walls
        corroborate each other: either a sibling shares this tick's exact y
        extent (dividers of one comb are drawn by the same loop -- the same
        signature `comb_bands` groups on), or the landing siblings run at
        uniform pitch.  Wall-to-tick gaps are deliberately excluded from the
        pitch: the corpus prints oversized leading slots (G09), and 1600WP's
        branch-code box prints its three same-loop ticks at 14.15/15.00/12.59pt
        -- the loop signature is the ground truth there, not the spacing.

    Existing comb dividers cannot reach this function (their landing endpoint
    is already inside the baseline ink), so the comb->border flip direction is
    structurally empty: recognition only ever adds dividers.
    """
    x = centre(rule)
    y0, y1 = float(rule["y0"]), float(rule["y1"])
    thickness = float(rule["thickness_pt"])
    if thickness <= 0.0:
        return None
    spanning = [
        h for h in horizontals
        if h["x0"] - CLUSTER_TOL_PT <= x <= h["x1"] + CLUSTER_TOL_PT
    ]
    # A near-touch overhead is a broken border, not a hanging guide.
    if any(0.0 <= y0 - float(h["y1"]) <= thickness for h in spanning):
        return None
    baselines = sorted(
        (h for h in spanning if 0.0 < float(h["y0"]) - y1 <= thickness),
        key=lambda h: (float(h["y0"]), float(h["x0"])))
    for baseline in baselines:
        base_top = float(baseline["y0"])
        base_x0 = float(baseline["x0"]) - CLUSTER_TOL_PT
        base_x1 = float(baseline["x1"]) + CLUSTER_TOL_PT

        def lands(v: dict[str, Any]) -> bool:
            return (float(v["y1"])
                    >= base_top - float(v["thickness_pt"])
                    and float(v["y0"]) < base_top)

        members = [
            v for v in verticals
            if v is not rule and base_x0 <= centre(v) <= base_x1
        ]
        positions: list[list[dict[str, Any]]] = []
        for v in sorted(members, key=lambda v: (centre(v), v["y0"], v["y1"])):
            if positions and abs(centre(positions[-1][0]) - centre(v)) \
                    <= CLUSTER_TOL_PT:
                positions[-1].append(v)
            else:
                positions.append([v])
        # A wall is one full-height stroke: it reaches this baseline, rises
        # above the tick's top, and its own top hangs from a rule -- all in
        # the SAME member.  Anything weaker lets a neighbouring hanging
        # divider stand in as a "wall" (0605's centavo divider would wall in
        # the stale mark beside it and hide the gap disorder that refuses it).
        wall_xs = [
            centre(group[0]) for group in positions
            if any(
                lands(v)
                and float(v["y0"]) < y0 - CLUSTER_TOL_PT
                and supported_at(float(v["y0"]), centre(v), horizontals)
                for v in group
            )
        ]
        left_walls = [wx for wx in wall_xs if wx < x - CLUSTER_TOL_PT]
        right_walls = [wx for wx in wall_xs if wx > x + CLUSTER_TOL_PT]
        if not left_walls or not right_walls:
            continue
        wall_left, wall_right = max(left_walls), min(right_walls)
        landing = [
            v for group in positions for v in group
            if wall_left + CLUSTER_TOL_PT < centre(v)
            < wall_right - CLUSTER_TOL_PT
            and lands(v)
        ]
        same_loop = any(
            abs(float(v["y0"]) - y0) <= JOIN_EPSILON_PT
            and abs(float(v["y1"]) - y1) <= JOIN_EPSILON_PT
            for v in landing
        )
        # Gap-order corroboration applies from TWO interior positions up,
        # measured WALL TO WALL: every landing boundary between the walls
        # plus the walls themselves.  A stale mark beside a genuine divider
        # is only detectable as gap disorder when both are present -- 0605
        # prints a knocked-out 3pt mark 3.12pt from its centavo divider,
        # turning the run 14.16/3.12/10.32, and is refused -- whereas a
        # SINGLETON guide has no run to be disordered in: its corroboration
        # is the pattern itself (hangs, lands within its own ink, walled),
        # and demanding that it halve its box within the pitch tolerance
        # would refuse 1600WP's genuine item-2 guide over a 21.24/19.68
        # split.  Same-loop runs are already corroborated by their extent.
        interior = {q(centre(v)) for v in landing} | {q(x)}
        siblings = sorted(interior | {q(wall_left), q(wall_right)})
        gaps = [b - a for a, b in zip(siblings, siblings[1:])]
        if (not same_loop
                and len(interior) >= 2
                and gaps and max(gaps) - min(gaps) > PITCH_TOL_PT):
            continue
        return baseline
    return None


def split_verticals(verticals: Sequence[dict[str, Any]],
                    horizontals: Sequence[dict[str, Any]],
                    guide_tick_pool: Sequence[dict[str, Any]] | None = None,
                    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Partition structural verticals into (comb dividers, box borders).

    The discriminator is geometric support, never thickness. A comb divider
    hangs from nothing and lands on its row's baseline rule; anything else --
    supported at both ends, or a fragment of a border interrupted by a comb
    band -- is a border.  One exception is a recognition, not a tolerance: a
    rule supported at neither end can still be a BOTTOM GUIDE TICK whose
    stroke stops a hair short of the baseline it visibly sits on -- see
    `bottom_guide_tick_baseline` for the pattern and its evidence.

    ``guide_tick_pool`` supplies the page's full vertical inventory when the
    caller classifies a subset (`split_final_vertical_corridors` classifies
    one rule at a time); the tick pattern needs the surrounding walls.
    """
    pool = verticals if guide_tick_pool is None else guide_tick_pool
    combs: list[dict[str, Any]] = []
    borders: list[dict[str, Any]] = []
    for rule in verticals:
        x = centre(rule)
        top = supported_at(rule["y0"], x, horizontals)
        bottom = supported_at(rule["y1"], x, horizontals)
        if bottom and not top:
            combs.append(rule)
        elif (not top and not bottom
              and bottom_guide_tick_baseline(
                  rule, pool, horizontals) is not None):
            combs.append(rule)
        else:
            borders.append(rule)
    return combs, borders


def split_final_vertical_corridors(
        verticals: Sequence[dict[str, Any]],
        horizontals: Sequence[dict[str, Any]],
        final_visible_ids: set[str] | None = None,
        ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Classify final-visible composite verticals one paper row at a time.

    ``extract.merge_intervals`` deliberately joins touching collinear paints.
    A table column painted as one fragment per row can therefore arrive here as
    one tall rectangle whose source-order range crosses several horizontal
    rails.  Classifying only the rectangle's outer endpoints loses every
    ordinary column corridor inside it and can turn the whole merge into a
    character-comb divider.

    Direct, single-paint rules keep the established endpoint classifier.  A
    final-visible composite is partitioned only at horizontal ink that crosses
    its x centre.  Paper between two rails is a border when the vertical covers
    that complete open corridor (allowing only the existing source join
    epsilon).  A leading fragment that hangs from the lower rail remains a comb
    divider.  Paint wholly inside a horizontal rail owns no paper corridor and
    supplies neither role.

    Derived fragments retain the parent's source id and paint-order range.  The
    private corridor fields are diagnostic only; generated contracts continue
    to cite the exact source rule id and ordinals.
    """
    combs: list[dict[str, Any]] = []
    borders: list[dict[str, Any]] = []

    def old_role(rule: dict[str, Any]) -> None:
        old_combs, old_borders = split_verticals(
            [rule], horizontals, guide_tick_pool=verticals)
        combs.extend(old_combs)
        borders.extend(old_borders)

    for rule in verticals:
        rule_id = str(rule.get("id"))
        first = int(rule.get("paint_seq", -1))
        last = int(rule.get("paint_seq_max", first))
        if (first == last
                or (final_visible_ids is not None
                    and rule_id not in final_visible_ids)):
            old_role(rule)
            continue

        x = centre(rule)
        rails = union_intervals(
            (float(horizontal["y0"]), float(horizontal["y1"]))
            for horizontal in horizontals
            if (float(horizontal["x0"]) - CLUSTER_TOL_PT
                <= x
                <= float(horizontal["x1"]) + CLUSTER_TOL_PT)
        )
        if not rails:
            old_role(rule)
            continue

        y0, y1 = float(rule["y0"]), float(rule["y1"])
        relevant_rails = [
            (rail_y0, rail_y1) for rail_y0, rail_y1 in rails
            if rail_y1 >= y0 - JOIN_EPSILON_PT
            and rail_y0 <= y1 + JOIN_EPSILON_PT
        ]
        if not relevant_rails:
            old_role(rule)
            continue

        parent_combs, _parent_borders = split_verticals(
            [rule], horizontals, guide_tick_pool=verticals)
        parent_was_comb = bool(parent_combs)

        breakpoints = {y0, y1}
        for rail_y0, rail_y1 in relevant_rails:
            if y0 < rail_y0 < y1:
                breakpoints.add(rail_y0)
            if y0 < rail_y1 < y1:
                breakpoints.add(rail_y1)

        def supported_near(y: float) -> bool:
            return any(
                rail_y0 - JOIN_EPSILON_PT
                <= y
                <= rail_y1 + JOIN_EPSILON_PT
                for rail_y0, rail_y1 in relevant_rails)

        def supporting_x_span(y: float) -> tuple[float, float] | None:
            spans = union_intervals(
                (float(horizontal["x0"]), float(horizontal["x1"]))
                for horizontal in horizontals
                if (float(horizontal["y0"]) - JOIN_EPSILON_PT
                    <= y
                    <= float(horizontal["y1"]) + JOIN_EPSILON_PT)
                and (float(horizontal["x0"]) - CLUSTER_TOL_PT
                     <= x
                     <= float(horizontal["x1"]) + CLUSTER_TOL_PT)
            )
            matches = [
                span for span in spans
                if span[0] - CLUSTER_TOL_PT
                <= x
                <= span[1] + CLUSTER_TOL_PT
            ]
            if len(matches) != 1:
                return None
            return matches[0]

        fragments: list[tuple[str, dict[str, Any]]] = []
        ordered = sorted(breakpoints)
        for slab_y0, slab_y1 in zip(ordered, ordered[1:]):
            if slab_y1 - slab_y0 <= JOIN_EPSILON_PT:
                continue
            midpoint = (slab_y0 + slab_y1) / 2.0
            if any(rail_y0 <= midpoint <= rail_y1
                   for rail_y0, rail_y1 in relevant_rails):
                continue
            top = supported_near(slab_y0)
            bottom = supported_near(slab_y1)
            if top and bottom:
                role = "border"
            elif bottom:
                role = "comb"
            else:
                # An upper-anchored or floating partial does not prove a
                # rail-to-rail column and is not a lower-baseline comb tick.
                continue
            fragment = {
                **rule,
                "y0": q(slab_y0),
                "y1": q(slab_y1),
                "_corridor_parent_y": [q(y0), q(y1)],
                "_corridor_role": role,
            }
            if role == "border":
                top_span = supporting_x_span(slab_y0)
                bottom_span = supporting_x_span(slab_y1)
                if top_span is not None and bottom_span is not None:
                    frame_x0 = max(top_span[0], bottom_span[0])
                    frame_x1 = min(top_span[1], bottom_span[1])
                    if (frame_x0 + CLUSTER_TOL_PT
                            < x
                            < frame_x1 - CLUSTER_TOL_PT):
                        fragment["_corridor_frame_x"] = [
                            q(frame_x0), q(frame_x1),
                        ]
            fragments.append((role, fragment))

        # A composite confined to rail ink has no paper-facing geometry.  Do
        # not revive it through the old hull classifier merely because all of
        # its open slabs were correctly discarded above.
        for fragment_index, (role, fragment) in enumerate(fragments):
            fragment["_corridor_fragment_index"] = fragment_index
            fragment["_corridor_fragment_count"] = len(fragments)
            # The old hull remains continuity evidence for comb discovery. A
            # repeated character tick can fully bridge each row just like a
            # table seam; corridor geometry alone cannot revoke that role.
            # Keep local fragments of an old comb as comb candidates, while a
            # complete rail-to-rail fragment additionally defines a border.
            # Once the border splits a genuine table seam, the same x lies on
            # the child cells' edges and cannot be assigned as an interior comb.
            if role == "comb" or parent_was_comb:
                combs.append(fragment)
            if role == "border":
                borders.append(fragment)

    return combs, borders


def dense_comb_corridor(
        fragment: dict[str, Any],
        old_dividers: Sequence[dict[str, Any]],
        ) -> bool:
    """Whether a regular four-boundary comb run shares this paper slab."""
    return bool(dense_comb_run(fragment, old_dividers))


def dense_comb_run(
        fragment: dict[str, Any],
        old_dividers: Sequence[dict[str, Any]],
        ) -> list[dict[str, Any]]:
    """Old divider members in one regular four-position run at ``fragment``."""
    y0, y1 = float(fragment["y0"]), float(fragment["y1"])
    overlapping = [
        rule for rule in old_dividers
        if min(y1, float(rule["y1"]))
        - max(y0, float(rule["y0"])) > JOIN_EPSILON_PT
    ]
    centres = sorted({q(centre(rule)) for rule in overlapping})
    target = q(centre(fragment))
    for start in range(max(0, len(centres) - 3)):
        run = centres[start:start + 4]
        gaps = [right - left for left, right in zip(run, run[1:])]
        if (run[0] - CLUSTER_TOL_PT
                <= target
                <= run[-1] + CLUSTER_TOL_PT
                and max(gaps) - min(gaps) <= PITCH_TOL_PT):
            return [
                rule for rule in overlapping
                if any(abs(centre(rule) - value) <= CLUSTER_TOL_PT
                       for value in run)
            ]
    return []


def localized_comb_dividers(
        old_dividers: Sequence[dict[str, Any]],
        corridor_dividers: Sequence[dict[str, Any]],
        localized_source_ids: set[str],
        ) -> list[dict[str, Any]]:
    """Replace certified composite hulls with uniquely local comb evidence."""
    if not localized_source_ids:
        return list(old_dividers)
    fragments: list[dict[str, Any]] = []
    for fragment in corridor_dividers:
        if str(fragment.get("id")) not in localized_source_ids:
            continue
        if fragment.get("_corridor_role") == "comb":
            fragments.append(fragment)
            continue
        if fragment.get("_corridor_role") != "border":
            continue
        dense_members = dense_comb_run(fragment, old_dividers)
        if not dense_members:
            continue
        band_y0 = max(float(rule["y0"]) for rule in dense_members)
        band_y1 = min(float(rule["y1"]) for rule in dense_members)
        parent_y = fragment.get("_corridor_parent_y") or [
            fragment["y0"], fragment["y1"],
        ]
        band_y0 = max(band_y0, float(parent_y[0]))
        band_y1 = min(band_y1, float(parent_y[1]))
        if band_y1 - band_y0 <= JOIN_EPSILON_PT:
            continue
        fragments.append({
            **fragment,
            "y0": q(band_y0),
            "y1": q(band_y1),
            "_corridor_role": "comb",
            "_corridor_dense_clip": True,
        })
    selected = [
        rule for rule in old_dividers
        if str(rule.get("id")) not in localized_source_ids
    ] + fragments
    return sorted(selected, key=lambda rule: (
        centre(rule), float(rule["y0"]), float(rule["y1"]),
        str(rule.get("id")),
        int(rule.get("_corridor_fragment_index", -1))))


def corridor_border_promotions(
        old_dividers: Sequence[dict[str, Any]],
        old_borders: Sequence[dict[str, Any]],
        corridor_borders: Sequence[dict[str, Any]],
        _text_runs: Sequence[dict[str, Any]],
        ) -> set[str]:
    """Source ids whose repeated row corridors have geometry-only proof.

    Printed text is deliberately not evidence: a fixed character in each half
    of a sparse comb is indistinguishable from two table labels. A candidate
    must instead own at least two complete rail-to-rail corridors, and its
    enclosing vector frame must not be an equal-pitch comb partition. The
    remaining proof is either a dense-comb/header relationship on the same
    source or repeated table rows containing another independently classified
    internal border. Equal-pitch sparse combs and isolated two-column tables
    therefore remain unpromoted when geometry cannot distinguish them.

    This certificate is independent of whether the x position already exists;
    callers decide separately whether to add a position or merely localise its
    coverage.
    """
    old_divider_ids = {str(rule.get("id")) for rule in old_dividers}
    all_verticals = [*old_dividers, *old_borders]
    by_source: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
    for fragment in corridor_borders:
        by_source[str(fragment.get("id"))].append(fragment)

    def partition_profile(
            fragment: dict[str, Any],
            ) -> tuple[bool, bool] | None:
        frame = fragment.get("_corridor_frame_x")
        if (not isinstance(frame, list) or len(frame) != 2
                or not all(isinstance(value, (int, float))
                           and not isinstance(value, bool)
                           for value in frame)):
            return None
        frame_x0, frame_x1 = (float(value) for value in frame)
        x = centre(fragment)
        if not (frame_x0 < x < frame_x1):
            return None
        y0, y1 = float(fragment["y0"]), float(fragment["y1"])
        corridor_spanning = [
            rule for rule in all_verticals
            if float(rule["y0"]) <= y0 + JOIN_EPSILON_PT
            and float(rule["y1"]) >= y1 - JOIN_EPSILON_PT
        ]
        left_edges = sorted({
            q(centre(rule)) for rule in corridor_spanning
            if abs(float(rule["x0"]) - frame_x0) <= CLUSTER_TOL_PT
        })
        right_edges = sorted({
            q(centre(rule)) for rule in corridor_spanning
            if abs(float(rule["x1"]) - frame_x1) <= CLUSTER_TOL_PT
        })
        if not left_edges or not right_edges:
            return None
        boundary_x0, boundary_x1 = left_edges[0], right_edges[-1]
        spanning_borders = [
            rule for rule in old_borders
            if float(rule["y0"]) <= y0 + JOIN_EPSILON_PT
            and float(rule["y1"]) >= y1 - JOIN_EPSILON_PT
            and boundary_x0 - CLUSTER_TOL_PT
            <= centre(rule)
            <= boundary_x1 + CLUSTER_TOL_PT
        ]
        border_centres = sorted({q(centre(rule))
                                 for rule in spanning_borders})
        left_candidates = [value for value in border_centres
                           if value < x - CLUSTER_TOL_PT]
        right_candidates = [value for value in border_centres
                            if value > x + CLUSTER_TOL_PT]
        if not left_candidates or not right_candidates:
            return None
        local_x0, local_x1 = max(left_candidates), min(right_candidates)
        centres = sorted({
            q(centre(rule)) for rule in corridor_spanning
            if local_x0 - CLUSTER_TOL_PT
            <= centre(rule)
            <= local_x1 + CLUSTER_TOL_PT
        })
        if len(centres) < 3:
            return None
        gaps = [right - left for left, right in zip(centres, centres[1:])]
        if not gaps or min(gaps) <= CLUSTER_TOL_PT:
            return None
        equal_pitch = max(gaps) - min(gaps) <= PITCH_TOL_PT
        table_shaped = not equal_pitch
        has_broader_table_border = any(
            centre(rule) < local_x0 - CLUSTER_TOL_PT
            or centre(rule) > local_x1 + CLUSTER_TOL_PT
            for rule in spanning_borders
        )
        return table_shaped, has_broader_table_border

    provisional: set[str] = set()
    for source_id, fragments in by_source.items():
        if source_id not in old_divider_ids or len(fragments) < 2:
            continue
        dense = [
            fragment for fragment in fragments
            if dense_comb_corridor(fragment, old_dividers)
        ]
        profiles = [
            profile for fragment in fragments
            if not dense_comb_corridor(fragment, old_dividers)
            for profile in [partition_profile(fragment)]
            if profile is not None
        ]
        table_profiles = [profile for profile in profiles if profile[0]]
        if not table_profiles:
            continue
        header_over_dense_comb = bool(dense)
        repeated_table_rows = sum(
            broader for _table_shaped, broader in table_profiles
        ) >= 2
        if header_over_dense_comb or repeated_table_rows:
            provisional.add(source_id)

    # A cohort made entirely of already-defined x positions is not evidence
    # that any old hull is wrong; it is commonly a stack of real comb fields
    # sharing table rails.  Localise existing members only when a sibling with
    # the exact same row-corridor signature repairs a genuinely missing x.
    old_border_centres = [centre(rule) for rule in old_borders]

    def corridor_signature(source_id: str) -> tuple[Any, ...]:
        return tuple(sorted(
            (q(float(fragment["y0"])), q(float(fragment["y1"])),
             tuple(fragment.get("_corridor_frame_x") or ()))
            for fragment in by_source[source_id]
        ))

    by_signature: dict[tuple[Any, ...], set[str]] = collections.defaultdict(set)
    for source_id in provisional:
        by_signature[corridor_signature(source_id)].add(source_id)
    certified: set[str] = set()
    for source_ids in by_signature.values():
        has_missing_position = any(
            not any(
                abs(centre(fragment) - old_x) <= CLUSTER_TOL_PT
                for old_x in old_border_centres)
            for source_id in source_ids
            for fragment in by_source[source_id][:1]
        )
        if has_missing_position:
            certified.update(source_ids)
    return certified


def comb_boundary_candidates(verticals: Sequence[dict[str, Any]],
                             area_fills: Sequence[dict[str, Any]]
                             ) -> list[dict[str, Any]]:
    """Every black column on the page, as a candidate comb slot boundary.

    Three sources, because the IR has already split this ink three ways on
    distinctions a comb knows nothing about. Measured over the corpus, the
    boundaries recovered break down as:

      * 6051 verticals `split_verticals` called comb dividers, but which a
        *different* band claimed. 2551Q page 2 draws its TIN group separators
        1.44pt lower than its character ticks (y1 126.86 against 125.42), so
        grouping dividers on their exact y extent files the three separators as a
        band of their own and the TIN reports 11 slots for 14 printed boxes;
      * 256 verticals it called borders, correctly: 48 of them are supported at
        both ends because they run the full row height (2200C x=59.76, a 0.48pt
        bar spanning y 115.22-132.14 across a comb band of 126.50-132.14), and
        208 are supported at neither, which is what 1701MS's traced geometry
        does to a 1.5pt separator (x=370.43, y 160.72-165.82);
      * 274 `area_fills`, the filled rects extract.py judged too thick to be a
        rule at all -- its cut is 1.5pt and 1707's TIN separators are 2.16pt
        wide, so they never reach the rule list.

    Horizontals are not candidates: a band is bounded top and bottom by
    horizontal rules, and no horizontal in the corpus is thick enough to reach
    from one to the other.

    Sorted so that de-duplication inside a band is order-independent.
    """
    candidates = list(verticals)
    candidates += [{
        "axis": "v",
        "x0": f["x0"], "y0": f["y0"], "x1": f["x1"], "y1": f["y1"],
        "thickness_pt": q(f["x1"] - f["x0"]),
        "gray": f["gray"],
        "role": f["role"],
        # Final-paint visibility is part of comb ownership. Keep the source
        # ordinal on a thick separator instead of turning it into timeless ink.
        "paint_seq": f.get("paint_seq", -1),
        "paint_seq_max": f.get("paint_seq_max", f.get("paint_seq", -1)),
    } for f in area_fills if f["role"] == "structural"]
    candidates.sort(key=lambda r: (centre(r), r["y0"], r["y1"]))
    return candidates


# A wall's length over its own thickness. The two populations of vertical
# structural area fill are measured in `wall_boundaries`; this sits in the gap
# between them, not at either edge.
MIN_WALL_ASPECT = 5.0


def wall_boundaries(area_fills: Sequence[dict[str, Any]]
                    ) -> list[dict[str, Any]]:
    """Structural area fills that BOUND a cell, as lattice candidates.

    A 1.92pt painted wall is a wall. `extract.py` files a filled rectangle as a
    rule only when its short side is at most `MAX_RULE_THICKNESS_PT` (1.5) and
    calls anything heavier an area fill, but that cut is about how BIR *draws*
    a line, not about whether the line *bounds* a cell -- and only the second
    question builds a grid. The comb path has always known this:
    `comb_boundary_candidates` above ingests exactly these fills so that a
    thick separator can end a comb slot. The cell grid never did, and that
    asymmetry is the defect this closes.

    What it cost, measured: 2550M page 2 paints its table sides at x 20.16-22.08
    and 590.04-591.96 as 1.92pt rectangles. Neither reaches `page["rules"]`, so
    neither reaches `build_lattice`, so Schedule 1's cells snap to unrelated
    columns contributed by rows further down the sheet. Column 1 loses 54.96 of
    226.08pt, column 4 loses 66.84 of 141.72pt, and the lost strips hold no text
    run at all: they are pure writing surface that does nothing when clicked.
    22 cells on that page, 230 field cells across 22 forms. The same page paints
    Schedule 1's top rail as a 1.92pt *horizontal* fill, so the first data row
    had no top boundary and no input -- holding horizontals back left that
    row unclosed.

    Not every structural fill is a wall, and promoting the wrong one would cut a
    comb into separate cells. Measured over the 53-form corpus, the 997 vertical
    structural fills fall into two populations that do not touch on any of three
    independent measurements:

      * 944 dividers *inside* a field -- 2000-OT's TIN group separators at
        x 256.13/302.93/349.75, 1707's 2.16pt marks -- thickness 2.16-2.20,
        length 4.92-9.84, aspect 2.28-4.56;
      * 53 walls -- page frames, table sides, header column boundaries --
        thickness 1.68-1.92, length 10.56-891.84, aspect 5.50-514.27.

    Aspect decides it because aspect is the scale-free measurement: it asks
    about the mark's shape rather than about how big this particular sheet is
    drawn. Length and thickness happen to agree here, and are recorded above so
    a future disagreement is visible rather than silent. The same aspect cut
    applied on the other axis keeps a wide thin rail (a table's top/bottom)
    and rejects a short wide mark that is not a row boundary.

    A page-sized fill matches neither orientation: both sides are long, so
    neither aspect reaches MIN_WALL_ASPECT, and it stays out of the lattice.

    Sorted so that clustering inside `build_lattice` is order-independent.
    """
    walls: list[dict[str, Any]] = []
    for fill in area_fills:
        if fill["role"] != "structural":
            continue
        width = float(fill["x1"]) - float(fill["x0"])
        height = float(fill["y1"]) - float(fill["y0"])
        if width <= 0 or height <= 0:
            continue
        if height >= width * MIN_WALL_ASPECT:
            axis, thickness = "v", width
        elif width >= height * MIN_WALL_ASPECT:
            axis, thickness = "h", height
        else:
            continue
        walls.append({
            "axis": axis,
            "x0": fill["x0"], "y0": fill["y0"],
            "x1": fill["x1"], "y1": fill["y1"],
            "thickness_pt": q(thickness),
            "gray": fill["gray"],
            "role": fill["role"],
            # A wall painted before a knockout is not a boundary. Keep the
            # source ordinal so final-paint compositing can still say so,
            # instead of turning a thick separator into timeless ink.
            "paint_seq": fill.get("paint_seq", -1),
            "paint_seq_max": fill.get("paint_seq_max",
                                      fill.get("paint_seq", -1)),
        })
    walls.sort(key=lambda wall: (
        wall["axis"], centre(wall),
        wall["y0"] if wall["axis"] == "v" else wall["x0"],
        wall["y1"] if wall["axis"] == "v" else wall["x1"]))
    return walls


def split_wall_axes(walls: Sequence[dict[str, Any]]
                    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Partition painted walls into vertical (x-lattice) and horizontal (y-lattice)."""
    vertical = [wall for wall in walls if wall["axis"] == "v"]
    horizontal = [wall for wall in walls if wall["axis"] == "h"]
    return vertical, horizontal


def wall_run(wall: dict[str, Any]) -> tuple[float, float]:
    """Along-axis span a visibility test must cover for this wall."""
    if wall["axis"] == "h":
        return float(wall["x0"]), float(wall["x1"])
    return float(wall["y0"]), float(wall["y1"])


def h_wall_would_fuse(wall: dict[str, Any],
                      horizontals: Sequence[dict[str, Any]]) -> bool:
    """True when this h-wall is redundant ink of a y-line we already have.

    `fuse_boundaries` joins two horizontals when the paper between their ink
    is thinner than the two thicknesses. A 1.92pt footer rail 2.52pt below a
    0.72pt comb baseline (2551M y 822.72 vs 826.56) therefore fuses into one
    lattice line at 823.49, which drops the reviewed combs on that row.
    A missing table rail a full row away (2550M Schedule 1 at 108.24 vs the
    next rule at 118.32) does not fuse, and is the wall this path exists to
    admit. Skip the first; keep the second.
    """
    wy0, wy1 = float(wall["y0"]), float(wall["y1"])
    wt = float(wall["thickness_pt"])
    wx0, wx1 = float(wall["x0"]), float(wall["x1"])
    for rule in horizontals:
        rx0, rx1 = float(rule["x0"]), float(rule["x1"])
        if min(wx1, rx1) - max(wx0, rx0) <= 0:
            continue
        ry0, ry1 = float(rule["y0"]), float(rule["y1"])
        rt = float(rule.get("thickness_pt") or (ry1 - ry0))
        if wy1 < ry0:
            paper = ry0 - wy1
        elif ry1 < wy0:
            paper = wy0 - ry1
        else:
            paper = 0.0
        if paper < wt + rt:
            return True
    return False


def paint_ordinal(paint: dict[str, Any]) -> int:
    """Last source operation represented by one extracted paint rectangle."""
    return int(paint.get("paint_seq_max", paint.get("paint_seq", -1)))


def paint_ordinal_range(paint: dict[str, Any]) -> tuple[int, int]:
    """Inclusive source-order bounds represented by one extracted paint.

    ``extract.merge_intervals`` may merge collinear fragments painted at
    different points in the content stream.  The merged rectangle then carries
    only its first and last source ordinals; assigning the whole rectangle to
    the last ordinal can revive an earlier fragment through an intervening
    knockout.  Preserve that uncertainty here so the compositor can certify a
    role only when every potentially topmost layer has the same role.
    """
    first = int(paint.get("paint_seq", -1))
    last = int(paint.get("paint_seq_max", first))
    return min(first, last), max(first, last)


def exact_rule_paint_span_layers(
        paint: dict[str, Any],
        ) -> list[dict[str, Any]] | None:
    """Expand one merged rule into its exact source-painted fragments.

    ``extract.merge_intervals`` historically retained only the first and last
    source ordinal represented by a merged bar.  That range is deliberately
    ambiguous: a late repaint of the whole bar and a late repaint of one tiny
    fragment have the same envelope.  New extractor output carries every
    contributing long-axis span and its singleton ordinal in ``paint_spans``.

    Absence means legacy evidence and keeps the conservative range behaviour.
    A present but malformed list is a producer-contract failure, not evidence
    that may be ignored.  Raising here makes the extraction -> lattice caller
    fail closed before it can publish geometry from corrupted provenance.
    """
    if "paint_spans" not in paint:
        return None

    raw_spans = paint.get("paint_spans")
    axis = paint.get("axis")
    if axis not in ("h", "v"):
        raise ValueError("rule paint_spans require an h/v axis")
    if not isinstance(raw_spans, list) or not raw_spans:
        raise ValueError("rule paint_spans must be a non-empty list")

    coordinate_names = ("x0", "x1") if axis == "h" else ("y0", "y1")
    if not all(
            type(paint.get(name)) in (int, float)
            and math.isfinite(float(paint[name]))
            for name in coordinate_names):
        raise ValueError("rule paint_spans have invalid rule bounds")
    rule_start = q(float(paint[coordinate_names[0]]))
    rule_end = q(float(paint[coordinate_names[1]]))
    if (rule_start != float(paint[coordinate_names[0]])
            or rule_end != float(paint[coordinate_names[1]])):
        raise ValueError("rule paint_spans have unquantised rule bounds")
    if rule_end <= rule_start:
        raise ValueError("rule paint_spans have non-positive rule bounds")

    first = paint.get("paint_seq")
    last = paint.get("paint_seq_max", first)
    if (type(first) is not int or type(last) is not int
            or first < 0 or last < first):
        raise ValueError("rule paint_spans have invalid paint-order bounds")

    parsed: list[tuple[float, float, int]] = []
    expected_keys = {"start_pt", "end_pt", "paint_seq"}
    for index, item in enumerate(raw_spans):
        if not isinstance(item, dict) or set(item) != expected_keys:
            raise ValueError(
                f"rule paint_spans[{index}] has an invalid key set")
        start_raw = item.get("start_pt")
        end_raw = item.get("end_pt")
        sequence = item.get("paint_seq")
        if (type(start_raw) not in (int, float)
                or type(end_raw) not in (int, float)
                or not math.isfinite(float(start_raw))
                or not math.isfinite(float(end_raw))
                or type(sequence) is not int
                or sequence < 0):
            raise ValueError(
                f"rule paint_spans[{index}] has invalid values")
        start = float(start_raw)
        end = float(end_raw)
        if q(start) != start or q(end) != end or end <= start:
            raise ValueError(
                f"rule paint_spans[{index}] is not a positive quantised span")
        parsed.append((start, end, sequence))

    if parsed != sorted(parsed, key=lambda item: (item[0], item[1], item[2])):
        raise ValueError("rule paint_spans are not in canonical order")
    if min(sequence for _start, _end, sequence in parsed) != first:
        raise ValueError("rule paint_spans do not bind paint_seq")
    if max(sequence for _start, _end, sequence in parsed) != last:
        raise ValueError("rule paint_spans do not bind paint_seq_max")

    cluster_start, cluster_end, _sequence = parsed[0]
    cluster_count = 1
    for start, end, _sequence in parsed[1:]:
        if start > cluster_end + EXTRACT_JOIN_EPSILON_PT:
            cluster_count += 1
        cluster_end = max(cluster_end, end)
    if (cluster_count != 1
            or q(cluster_start) != rule_start
            or q(cluster_end) != rule_end):
        raise ValueError("rule paint_spans do not reproduce the merged rule")

    layers: list[dict[str, Any]] = []
    for start, end, sequence in parsed:
        layer = {key: value for key, value in paint.items()
                 if key != "paint_spans"}
        layer[coordinate_names[0]] = start
        layer[coordinate_names[1]] = end
        layer["paint_seq"] = sequence
        layer["paint_seq_max"] = sequence
        layer["_rule_paint_span"] = True
        layers.append(layer)
    return layers


def rule_paint_join_bridges(
        paint: dict[str, Any],
        layers: Sequence[dict[str, Any]],
        ) -> list[dict[str, Any]]:
    """Preserve the extractor's measured interval-join continuity.

    ``extract.merge_intervals`` treats contributor gaps of at most
    ``EXTRACT_JOIN_EPSILON_PT`` as one bar.  Expanding the contributors for
    paint-order evidence must not turn those accepted sub-cent gaps back into
    breaks in the lattice.  Add a bridge only over an actual positive join gap,
    with the two adjoining contributors' ordinal range.  A nonstructural paint
    between those ordinals therefore keeps the bridge ambiguous/finally erased;
    this does not revive a later knockout.
    """
    if not layers:
        return []
    axis = str(paint["axis"])
    coordinate_names = ("x0", "x1") if axis == "h" else ("y0", "y1")
    by_start: list[tuple[float, list[dict[str, Any]]]] = []
    for layer in layers:
        start = float(layer[coordinate_names[0]])
        if not by_start or by_start[-1][0] != start:
            by_start.append((start, [layer]))
        else:
            by_start[-1][1].append(layer)

    first_group = by_start[0][1]
    frontier_end = max(
        float(layer[coordinate_names[1]]) for layer in first_group)
    frontier_sequences = {
        paint_ordinal(layer) for layer in first_group
        if float(layer[coordinate_names[1]]) == frontier_end
    }
    bridges: list[dict[str, Any]] = []
    for start, group in by_start[1:]:
        group_end = max(
            float(layer[coordinate_names[1]]) for layer in group)
        group_frontier_sequences = {
            paint_ordinal(layer) for layer in group
            if float(layer[coordinate_names[1]]) == group_end
        }
        if start > frontier_end:
            bridge = {
                key: value for key, value in paint.items()
                if key != "paint_spans"
            }
            bridge[coordinate_names[0]] = frontier_end
            bridge[coordinate_names[1]] = start
            # Every contributor beginning on the far side can be the first
            # paint adjoining this join, even when canonical end-order puts a
            # shorter fragment first. Bind the complete same-start ordinal
            # set so an intervening nonstructural layer cannot be hidden.
            ordinals = [
                *frontier_sequences,
                *(paint_ordinal(layer) for layer in group),
            ]
            bridge["paint_seq"] = min(ordinals)
            bridge["paint_seq_max"] = max(ordinals)
            bridge["_rule_paint_join_bridge"] = True
            bridges.append(bridge)
            frontier_end = group_end
            frontier_sequences = group_frontier_sequences
        elif group_end > frontier_end:
            frontier_end = group_end
            frontier_sequences = group_frontier_sequences
        elif group_end == frontier_end:
            frontier_sequences.update(group_frontier_sequences)
    return bridges


def point_segment_distance(point: Point, start: Point, end: Point) -> float:
    """Euclidean distance from a point to one finite line segment."""
    px, py = point
    x0, y0 = start
    x1, y1 = end
    dx, dy = x1 - x0, y1 - y0
    length_sq = dx * dx + dy * dy
    if length_sq == 0:
        return math.hypot(px - x0, py - y0)
    along = max(0.0, min(1.0, ((px - x0) * dx + (py - y0) * dy) / length_sq))
    return math.hypot(px - (x0 + along * dx), py - (y0 + along * dy))


def flatten_cubic(start: Point, first: Point, second: Point, end: Point,
                  depth: int = 0) -> list[Point]:
    """Flatten one cubic at the existing source-coordinate join precision."""
    if (depth >= 16
            or max(point_segment_distance(first, start, end),
                   point_segment_distance(second, start, end))
            <= JOIN_EPSILON_PT):
        return [end]

    p01 = ((start[0] + first[0]) / 2.0, (start[1] + first[1]) / 2.0)
    p12 = ((first[0] + second[0]) / 2.0, (first[1] + second[1]) / 2.0)
    p23 = ((second[0] + end[0]) / 2.0, (second[1] + end[1]) / 2.0)
    p012 = ((p01[0] + p12[0]) / 2.0, (p01[1] + p12[1]) / 2.0)
    p123 = ((p12[0] + p23[0]) / 2.0, (p12[1] + p23[1]) / 2.0)
    middle = ((p012[0] + p123[0]) / 2.0, (p012[1] + p123[1]) / 2.0)
    return [
        *flatten_cubic(start, p01, p012, middle, depth + 1),
        *flatten_cubic(middle, p123, p23, end, depth + 1),
    ]


def flattened_subpaths(path: dict[str, Any]) -> list[tuple[list[Point], bool]]:
    """Reconstruct the actual line/cubic outline carried by one IR path."""
    flattened: list[tuple[list[Point], bool]] = []
    for subpath in path.get("subpaths") or ():
        start_raw = subpath.get("start") or ()
        if len(start_raw) != 2:
            continue
        start = (float(start_raw[0]), float(start_raw[1]))
        points = [start]
        cursor = start
        for operation in subpath.get("ops") or ():
            values = [float(value) for value in operation.get("points") or ()]
            if operation.get("op") == "l" and len(values) == 2:
                cursor = (values[0], values[1])
                points.append(cursor)
            elif operation.get("op") == "c" and len(values) == 6:
                first = (values[0], values[1])
                second = (values[2], values[3])
                end = (values[4], values[5])
                points.extend(flatten_cubic(cursor, first, second, end))
                cursor = end
            elif operation.get("op") == "re" and len(values) == 4:
                x0, y0, x1, y1 = values
                points = [(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)]
                cursor = points[-1]
            else:
                # extract.py rejects unknown operations. If a newer IR reaches
                # this older compositor, retain the bbox as uncertainty rather
                # than inventing path geometry.
                points = []
                break
        closed = bool(subpath.get("closed"))
        if points and closed and points[-1] != points[0]:
            points.append(points[0])
        if len(points) >= 2:
            flattened.append((points, closed))
    return flattened


def path_paint_layers(paint: dict[str, Any]) -> list[dict[str, Any]]:
    """Split a non-rectilinear fill+stroke into its true paint-order layers."""
    if "subpaths" not in paint:
        return [paint]
    flattened = flattened_subpaths(paint)
    layers: list[dict[str, Any]] = []
    if paint.get("fill") is not None:
        layers.append({
            **paint,
            "_path_layer": "fill",
            "_flattened": flattened,
            "role": tone_role(paint.get("fill_gray")),
            "paint_seq": int(paint.get("paint_seq", -1)),
            "paint_seq_max": int(paint.get("paint_seq", -1)),
        })
    if paint.get("stroke") is not None and float(paint.get("stroke_width_pt") or 0) > 0:
        layers.append({
            **paint,
            "_path_layer": "stroke",
            "_flattened": flattened,
            "role": tone_role(paint.get("stroke_gray")),
            "paint_seq": int(paint.get("paint_seq_max", paint.get("paint_seq", -1))),
            "paint_seq_max": int(paint.get("paint_seq_max", paint.get("paint_seq", -1))),
        })
    return layers


def path_segments(paint: dict[str, Any]) -> Iterable[tuple[Point, Point]]:
    for points, _closed in paint.get("_flattened") or ():
        yield from zip(points, points[1:])


def paint_bounds(paint: dict[str, Any]) -> tuple[float, float, float, float]:
    """Paint bbox, including the outside half of a path stroke."""
    half = (float(paint.get("stroke_width_pt") or 0) / 2.0
            if paint.get("_path_layer") == "stroke" else 0.0)
    return (
        float(paint["x0"]) - half,
        float(paint["y0"]) - half,
        float(paint["x1"]) + half,
        float(paint["y1"]) + half,
    )


def point_in_path(paint: dict[str, Any], point: Point) -> bool:
    """PDF nonzero/even-odd fill containment over flattened source subpaths."""
    px, py = point
    winding = 0
    crossings = 0
    for points, _closed in paint.get("_flattened") or ():
        polygon = points if points[-1] == points[0] else [*points, points[0]]
        for start, end in zip(polygon, polygon[1:]):
            if point_segment_distance(point, start, end) <= JOIN_EPSILON_PT:
                return True
            x0, y0 = start
            x1, y1 = end
            if (y0 > py) == (y1 > py):
                continue
            x_cross = x0 + (py - y0) * (x1 - x0) / (y1 - y0)
            if x_cross <= px:
                continue
            crossings += 1
            winding += 1 if y1 > y0 else -1
    return bool(crossings % 2) if paint.get("even_odd") else winding != 0


def exact_rectangular_path_fill_covers(
        paint: dict[str, Any],
        x0: float, y0: float, x1: float, y1: float) -> bool:
    """Whether one path fill is exactly one axis-aligned covering rectangle.

    Point samples cannot prove coverage for an arbitrary polygon: an even-odd
    compound path can cover every sampled corner and centre while leaving a
    small hole elsewhere. Keep complex fills unresolved. A single rectangular
    subpath has no hidden interior topology, so bbox containment is then exact.
    """
    if paint.get("_path_layer") != "fill":
        return False
    flattened = list(paint.get("_flattened") or ())
    if len(flattened) != 1:
        return False
    points = list(flattened[0][0])
    if points and points[-1] == points[0]:
        points.pop()
    simplified: list[Point] = []
    for point in points:
        if not simplified or point != simplified[-1]:
            simplified.append(point)
    if len(simplified) != 4:
        return False
    xs = sorted({point[0] for point in simplified})
    ys = sorted({point[1] for point in simplified})
    if len(xs) != 2 or len(ys) != 2:
        return False
    corners = {
        (xs[0], ys[0]), (xs[1], ys[0]),
        (xs[1], ys[1]), (xs[0], ys[1]),
    }
    if set(simplified) != corners:
        return False
    polygon = [*simplified, simplified[0]]
    if any(a[0] != b[0] and a[1] != b[1]
           for a, b in zip(polygon, polygon[1:])):
        return False
    return xs[0] <= x0 and xs[1] >= x1 and ys[0] <= y0 and ys[1] >= y1


def paint_covers_point(paint: dict[str, Any], point: Point) -> bool:
    """Whether one exact paint layer covers a point in the candidate slab."""
    x, y = point
    x0, y0, x1, y1 = paint_bounds(paint)
    if not (x0 <= x <= x1 and y0 <= y <= y1):
        return False
    layer = paint.get("_path_layer")
    if layer == "fill":
        return point_in_path(paint, point)
    if layer == "stroke":
        half = float(paint.get("stroke_width_pt") or 0) / 2.0
        return any(point_segment_distance(point, start, end) <= half
                   for start, end in path_segments(paint))
    return True


def path_x_edges(paint: dict[str, Any], y: float) -> list[float]:
    """Actual path crossings of a horizontal probe, used to split x slabs."""
    edges: list[float] = []
    half = (float(paint.get("stroke_width_pt") or 0) / 2.0
            if paint.get("_path_layer") == "stroke" else 0.0)
    for start, end in path_segments(paint):
        x0, y0 = start
        x1, y1 = end
        if y0 == y1:
            if abs(y - y0) <= half + JOIN_EPSILON_PT:
                edges.extend((min(x0, x1) - half, max(x0, x1) + half))
            continue
        if not (min(y0, y1) - half <= y <= max(y0, y1) + half):
            continue
        probe_y = max(min(y, max(y0, y1)), min(y0, y1))
        x_cross = x0 + (probe_y - y0) * (x1 - x0) / (y1 - y0)
        edges.extend((x_cross - half, x_cross + half))
    return edges


def path_y_edges(paint: dict[str, Any], x: float) -> list[float]:
    """Actual path crossings of a vertical probe, used to split y slabs."""
    edges: list[float] = []
    half = (float(paint.get("stroke_width_pt") or 0) / 2.0
            if paint.get("_path_layer") == "stroke" else 0.0)
    for start, end in path_segments(paint):
        x0, y0 = start
        x1, y1 = end
        if x0 == x1:
            if abs(x - x0) <= half + JOIN_EPSILON_PT:
                edges.extend((min(y0, y1) - half, max(y0, y1) + half))
            continue
        if not (min(x0, x1) - half <= x <= max(x0, x1) + half):
            continue
        probe_x = max(min(x, max(x0, x1)), min(x0, x1))
        y_cross = y0 + (probe_x - x0) * (y1 - y0) / (x1 - x0)
        edges.extend((y_cross - half, y_cross + half))
    return edges


def segments_intersect(first: tuple[Point, Point],
                       second: tuple[Point, Point]) -> bool:
    """Closed segment intersection without a fitted geometry tolerance."""
    (a, b), (c, d) = first, second

    def orientation(p: Point, q_: Point, r: Point) -> float:
        return ((q_[0] - p[0]) * (r[1] - p[1])
                - (q_[1] - p[1]) * (r[0] - p[0]))

    def within(p: Point, q_: Point, r: Point) -> bool:
        return (min(p[0], r[0]) <= q_[0] <= max(p[0], r[0])
                and min(p[1], r[1]) <= q_[1] <= max(p[1], r[1]))

    ab_c, ab_d = orientation(a, b, c), orientation(a, b, d)
    cd_a, cd_b = orientation(c, d, a), orientation(c, d, b)
    if ((ab_c > 0) != (ab_d > 0)) and ((cd_a > 0) != (cd_b > 0)):
        return True
    return ((ab_c == 0 and within(a, c, b))
            or (ab_d == 0 and within(a, d, b))
            or (cd_a == 0 and within(c, a, d))
            or (cd_b == 0 and within(c, b, d)))


def path_paint_intersects_rect(paint: dict[str, Any],
                               x0: float, y0: float,
                               x1: float, y1: float) -> bool:
    """Actual subpath/fill/stroke intersection with one divider corridor."""
    bx0, by0, bx1, by1 = paint_bounds(paint)
    if bx1 < x0 or bx0 > x1 or by1 < y0 or by0 > y1:
        return False
    half = (float(paint.get("stroke_width_pt") or 0) / 2.0
            if paint.get("_path_layer") == "stroke" else 0.0)
    rx0, ry0, rx1, ry1 = x0 - half, y0 - half, x1 + half, y1 + half
    corners = [(rx0, ry0), (rx1, ry0), (rx1, ry1), (rx0, ry1)]
    edges = list(zip(corners, [*corners[1:], corners[0]]))
    for start, end in path_segments(paint):
        if (rx0 <= start[0] <= rx1 and ry0 <= start[1] <= ry1
                or rx0 <= end[0] <= rx1 and ry0 <= end[1] <= ry1):
            return True
        if any(segments_intersect((start, end), edge) for edge in edges):
            return True
    return (paint.get("_path_layer") == "fill"
            and any(point_in_path(paint, corner) for corner in corners))


class FinalPaint:
    """Query final visible structural ink without reviving an overpainted mark.

    The IR keeps rectilinear rules and area fills in separate geometry lists,
    but both carry their exact content-stream ordinal. A divider that was later
    covered by a white knockout is still present in ``rules``; treating that
    stale record as a comb anchor creates a field the final page does not draw.

    Visibility is measured over endpoint slabs, not at one midpoint. For every
    positive-height y slab we partition the candidate's width at every paint
    edge and retain the x intervals whose every potentially topmost layer is
    structural.  We then follow common x coverage through consecutive slabs.
    Merely having some ink at unrelated x positions above and below a knockout
    is not a continuous divider.  The resulting y spans are cached because the
    same candidate is considered by its seed band and by neighbouring endpoint
    bands.
    """

    __slots__ = (
        "paints", "path_paints", "horizontal_rule_hulls", "_visible",
    )

    def __init__(self, paints: Sequence[dict[str, Any]]) -> None:
        expanded: list[dict[str, Any]] = []
        horizontal_rule_hulls: list[dict[str, Any]] = []
        for paint in paints:
            for layer in path_paint_layers(paint):
                exact_layers = exact_rule_paint_span_layers(layer)
                if (layer.get("axis") == "h"
                        and layer.get("role") == "structural"):
                    # Exact contributor expansion is required for paint-order
                    # compositing, but the producer's merged hull remains the
                    # source certificate that adjacent fragments belong to one
                    # horizontal rail.  Keep that hull only for rail
                    # candidacy; final visibility is still proven from the
                    # expanded paints by structural_rect_across().
                    horizontal_rule_hulls.append(layer)
                if exact_layers is None:
                    expanded.append(layer)
                else:
                    expanded.extend(exact_layers)
                    expanded.extend(rule_paint_join_bridges(
                        layer, exact_layers))
        self.paints = tuple(expanded)
        self.path_paints = tuple(paint for paint in expanded if "_path_layer" in paint)
        self.horizontal_rule_hulls = tuple(horizontal_rule_hulls)
        self._visible: dict[
            tuple[str, float, float, float, float], list[Interval]
        ] = {}

    def visible_intervals(self, ink: dict[str, Any]) -> list[Interval]:
        """Final-visible y spans of a vertical candidate."""
        return self.visible_spans(ink, "v")

    def visible_spans(self, ink: dict[str, Any], axis: str) -> list[Interval]:
        """Final-visible long-axis spans with one common thin-axis witness."""
        if axis not in ("h", "v"):
            raise ValueError(f"unsupported paint visibility axis: {axis}")
        x0 = float(ink["x0"])
        y0 = float(ink["y0"])
        x1 = float(ink["x1"])
        y1 = float(ink["y1"])
        key = (axis, x0, y0, x1, y1)
        cached = self._visible.get(key)
        if cached is not None:
            return cached

        relevant = [
            (index, paint) for index, paint in enumerate(self.paints)
            if paint_bounds(paint)[2] > x0 and paint_bounds(paint)[0] < x1
            and paint_bounds(paint)[3] > y0 and paint_bounds(paint)[1] < y1
        ]
        primary0, primary1 = ((y0, y1) if axis == "v" else (x0, x1))
        cross0, cross1 = ((x0, x1) if axis == "v" else (y0, y1))
        endpoints = {primary0, primary1}
        for _, paint in relevant:
            px0, py0, px1, py1 = paint_bounds(paint)
            paint_primary0, paint_primary1 = (
                (py0, py1) if axis == "v" else (px0, px1))
            endpoints.update((
                max(primary0, paint_primary0),
                min(primary1, paint_primary1),
            ))
            for points, _closed in paint.get("_flattened") or ():
                coordinate = 1 if axis == "v" else 0
                endpoints.update(
                    point[coordinate] for point in points
                    if primary0 < point[coordinate] < primary1)

        slab_visibility: list[tuple[float, float, list[Interval]]] = []
        ordered_primary = sorted(endpoints)
        for a, b in zip(ordered_primary, ordered_primary[1:]):
            if b <= a:
                continue
            primary_centre = (a + b) / 2.0
            active = [
                (index, paint) for index, paint in relevant
                if (
                    paint_bounds(paint)[1] <= primary_centre
                    <= paint_bounds(paint)[3]
                    if axis == "v"
                    else paint_bounds(paint)[0] <= primary_centre
                    <= paint_bounds(paint)[2]
                )
            ]
            cross_edges = {cross0, cross1}
            for _, paint in active:
                if "_path_layer" in paint:
                    path_edges = (
                        path_x_edges(paint, primary_centre)
                        if axis == "v"
                        else path_y_edges(paint, primary_centre)
                    )
                    cross_edges.update(
                        max(cross0, min(cross1, edge))
                        for edge in path_edges)
                    if paint.get("role") != "structural":
                        # A nonrect knockout/decorative path can sweep across
                        # the thin axis within one primary slab. Its midpoint
                        # section is not a whole-slab witness, so conservatively
                        # include its complete cross-axis bbox as a mask.
                        px0, py0, px1, py1 = paint_bounds(paint)
                        path_cross0, path_cross1 = (
                            (px0, px1) if axis == "v" else (py0, py1))
                        cross_edges.update((
                            max(cross0, path_cross0),
                            min(cross1, path_cross1),
                        ))
                else:
                    px0, py0, px1, py1 = paint_bounds(paint)
                    paint_cross0, paint_cross1 = (
                        (px0, px1) if axis == "v" else (py0, py1))
                    cross_edges.update((
                        max(cross0, paint_cross0),
                        min(cross1, paint_cross1),
                    ))

            visible_cross: list[Interval] = []
            ordered_cross = sorted(cross_edges)
            for left, right in zip(ordered_cross, ordered_cross[1:]):
                if right <= left:
                    continue
                cross_centre = (left + right) / 2.0
                point = (
                    (cross_centre, primary_centre)
                    if axis == "v"
                    else (primary_centre, cross_centre)
                )
                def covers_witness(paint: dict[str, Any]) -> bool:
                    if ("_path_layer" in paint
                            and paint.get("role") != "structural"):
                        px0, py0, px1, py1 = paint_bounds(paint)
                        return (
                            px0 <= point[0] <= px1
                            and py0 <= point[1] <= py1
                        )
                    return paint_covers_point(paint, point)

                covering = [
                    (paint_ordinal(paint), index, paint)
                    for index, paint in active
                    if covers_witness(paint)
                ]
                if not covering:
                    continue
                # A merged paint may represent fragments from a source-order
                # range.  A layer can still be topmost unless another layer's
                # earliest possible ordinal is later than its latest possible
                # ordinal.  Mixed roles among those candidates are ambiguous,
                # so fail closed instead of globally ordering the merge by its
                # final fragment.
                ranged = [
                    (*paint_ordinal_range(paint), index, paint)
                    for _ordinal, index, paint in covering
                ]
                latest_floor = max(first for first, _last, _index, _paint in ranged)
                potentially_topmost = [
                    paint for _first, last, _index, paint in ranged
                    if last >= latest_floor
                ]
                if (potentially_topmost
                        and all(paint.get("role") == "structural"
                                for paint in potentially_topmost)
                        and any("_path_layer" not in paint
                                for paint in potentially_topmost)):
                    visible_cross.append((left, right))
            slab_visibility.append((a, b, union_intervals(visible_cross)))

        # Track every distinct fixed-x witness through successive y slabs.
        # Starting a fresh track on every slab matters: a second x corridor can
        # appear while an older one remains, then become the only survivor.
        # Tracks with identical common coverage are equivalent; retaining the
        # earliest start bounds the state without losing a possible witness.
        tracks: list[tuple[float, float, list[Interval]]] = []
        completed: list[Interval] = []
        for a, b, visible_x in slab_visibility:
            next_tracks: list[tuple[float, float, list[Interval]]] = []
            for start, _end, common_x in tracks:
                overlap = intersect_intervals(common_x, visible_x)
                if overlap:
                    next_tracks.append((start, b, overlap))
                else:
                    completed.append((start, a))
            if visible_x:
                next_tracks.append((a, b, visible_x))

            deduplicated: dict[tuple[Interval, ...],
                               tuple[float, float, list[Interval]]] = {}
            for track in next_tracks:
                identity = tuple(track[2])
                prior = deduplicated.get(identity)
                if prior is None or track[0] < prior[0]:
                    deduplicated[identity] = track
            tracks = list(deduplicated.values())
        completed.extend((start, end) for start, end, _common_x in tracks)

        # Do not union adjacent spans: adjacency without a common x witness is
        # precisely the stale-divider false positive this compositor prevents.
        unique = sorted(set(completed))
        result = [
            span for span in unique
            if span[1] > span[0]
            and not any(
                other != span
                and other[0] <= span[0] + JOIN_EPSILON_PT
                and other[1] >= span[1] - JOIN_EPSILON_PT
                for other in unique
            )
        ]
        self._visible[key] = result
        return result

    def structural_across(self, ink: dict[str, Any], y0: float, y1: float) -> bool:
        """Whether final structural ink survives across the whole open band."""
        return any(a <= y0 + JOIN_EPSILON_PT and b >= y1 - JOIN_EPSILON_PT
                   for a, b in self.visible_intervals(ink))

    def structural_across_axis(self, ink: dict[str, Any],
                               lo: float, hi: float, axis: str) -> bool:
        """Whether final structural ink survives one full horizontal/vertical run."""
        return any(
            start <= lo + JOIN_EPSILON_PT
            and end >= hi - JOIN_EPSILON_PT
            for start, end in self.visible_spans(ink, axis)
        )

    def structural_rect_across(self, x0: float, y0: float,
                               x1: float, y1: float) -> bool:
        """Whether final structural ink covers every open slab of a rectangle.

        ``visible_spans(..., "h")`` proves one common y witness across x. That
        is sufficient for a rule run, but not for proving that a horizontal
        rail leaves no paper anywhere through its thickness. Partition the
        thickness at every relevant paint/path boundary and require the
        existing composited witness proof independently in every y slab. A
        nonstructural path remains conservatively represented by its complete
        bbox inside ``visible_spans``, so uncertainty rejects coverage.
        """
        if x1 <= x0 or y1 <= y0:
            return False
        endpoints = {y0, y1}
        for paint in self.paints:
            px0, py0, px1, py1 = paint_bounds(paint)
            if px1 <= x0 or px0 >= x1 or py1 <= y0 or py0 >= y1:
                continue
            endpoints.update((max(y0, py0), min(y1, py1)))
            for points, _closed in paint.get("_flattened") or ():
                endpoints.update(
                    point[1] for point in points
                    if y0 < point[1] < y1)

        ordered = sorted(endpoints)
        slabs = [
            (a, b) for a, b in zip(ordered, ordered[1:]) if b > a
        ]
        return bool(slabs) and all(
            self.structural_across_axis({
                "x0": x0, "y0": a, "x1": x1, "y1": b,
            }, x0, x1, "h")
            for a, b in slabs
        )

    def horizontal_rail_across(self, x0: float, x1: float,
                               y0: float, y1: float) -> bool:
        """Whether a slab is wholly inked by one final-visible horizontal rail.

        Inside such a slab there is no paper on which crossing black ink can
        prove a vertical slot boundary.  A vertical from the row above and a
        vertical from the row below otherwise appear to overlap by exactly the
        horizontal rule thickness and manufacture a combined endpoint
        topology.  The rail must cover the complete field width and survive
        final-paint compositing; a short cap or an erased rule proves nothing.
        """
        for paint in self.horizontal_rule_hulls:
            px0, py0, px1, py1 = paint_bounds(paint)
            if not (px0 <= x0 and px1 >= x1
                    and py0 <= y0 and py1 >= y1):
                continue
            if self.structural_rect_across(x0, y0, x1, y1):
                return True
        return False

    def definitely_erased(self, ink: dict[str, Any]) -> bool:
        """Whether one known-later nonstructural layer covers the whole bbox.

        False visibility can also mean source-order or moving-path uncertainty.
        Such geometry may remain as an explicitly unresolved lattice hint, but
        a single later rectangular knockout/decorative layer proven to cover the
        complete candidate is absent and must not define a cell. For path fills,
        only one exact axis-aligned rectangle is a coverage proof; samples of an
        arbitrary compound path cannot rule out a hole.
        """
        x0, y0, x1, y1 = (
            float(ink["x0"]), float(ink["y0"]),
            float(ink["x1"]), float(ink["y1"]),
        )
        _ink_first, ink_last = paint_ordinal_range(ink)
        for paint in self.paints:
            if paint.get("role") == "structural":
                continue
            paint_first, _paint_last = paint_ordinal_range(paint)
            if paint_first <= ink_last:
                continue
            px0, py0, px1, py1 = paint_bounds(paint)
            if not (px0 <= x0 and px1 >= x1 and py0 <= y0 and py1 >= y1):
                continue
            if "_path_layer" not in paint:
                return True
            if exact_rectangular_path_fill_covers(
                    paint, x0, y0, x1, y1):
                return True
        return False


# ---------------------------------------------------------------------------
# Lattice
# ---------------------------------------------------------------------------


class Lattice:
    """One axis of the page grid: clustered line positions plus their ink."""

    __slots__ = ("positions", "ink_lo", "ink_hi", "spans", "members")

    def __init__(self, positions: list[float], ink_lo: list[float], ink_hi: list[float],
                 spans: list[list[Interval]], members: list[list[dict[str, Any]]]) -> None:
        self.positions = positions   # clustered centres, ascending
        self.ink_lo = ink_lo         # near edge of the cluster's ink
        self.ink_hi = ink_hi         # far edge of the cluster's ink
        self.spans = spans           # unioned along-line extent of ALL collinear ink
        self.members = members       # the border rules that defined the position

    def __len__(self) -> int:
        return len(self.positions)


def cluster_collinear(defining: Sequence[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Chain rules whose centres lie within a rule width of each other."""
    groups: list[list[dict[str, Any]]] = []
    for rule in sorted(defining, key=centre):
        if groups and centre(rule) - centre(groups[-1][-1]) <= CLUSTER_TOL_PT:
            groups[-1].append(rule)
        else:
            groups.append([rule])
    return groups


def total_length(spans: Sequence[Interval]) -> float:
    return sum(b - a for a, b in spans)


def overlap_length(left: Sequence[Interval], right: Sequence[Interval]) -> float:
    """Length of paper where two along-axis extents coincide."""
    total = 0.0
    for a0, a1 in left:
        for b0, b1 in right:
            total += max(0.0, min(a1, b1) - max(a0, b0))
    return total


# A composite boundary's bars are drawn over the same run, so each must shadow
# this much of the other's length before they may fuse.
MIN_BOUNDARY_OVERLAP = 0.5


class Bar:
    """One drawn rule reduced to the four numbers the fusion test needs."""

    __slots__ = ("start", "end", "near", "far", "thickness")

    def __init__(self, rule: dict[str, Any], axis: str) -> None:
        near, far = ("y0", "y1") if axis == "h" else ("x0", "x1")
        along = ("x0", "x1") if axis == "h" else ("y0", "y1")
        self.start, self.end = rule[along[0]], rule[along[1]]
        self.near, self.far = rule[near], rule[far]
        self.thickness = rule["thickness_pt"]


class GroupGeometry:
    """One centre-cluster measured on both axes, before boundary fusion."""

    __slots__ = ("rules", "bars", "position", "ink_lo", "ink_hi", "span")

    def __init__(self, rules: list[dict[str, Any]], all_ink: Sequence[dict[str, Any]],
                 axis: str) -> None:
        near, far = ("y0", "y1") if axis == "h" else ("x0", "x1")
        along = ("x0", "x1") if axis == "h" else ("y0", "y1")
        self.rules = rules
        self.bars = [Bar(r, axis) for r in rules]
        self.position = q(sum(centre(r) for r in rules) / len(rules))
        self.ink_lo = q(min(r[near] for r in rules))
        self.ink_hi = q(max(r[far] for r in rules))
        # Coverage is measured against this cluster's own centre, so fusing two
        # clusters later cannot move the span of either one.
        #
        # A cluster's OWN rules count whatever their distance from that centre,
        # exactly as `line_thickness_gray` already counts them for weight and
        # tone. `cluster_collinear` chains by *adjacency*, so a cluster is wider
        # than the tolerance whenever three or more fragments step across it,
        # and its centre is a mean the outermost member need not sit within.
        # Filtering on distance alone then drops a defining rule from the
        # coverage of the very line it defines: on 0619-E the "Amended Return?
        # Yes" checkbox's left wall (x centre 275.64) is one of ten fragments in
        # the cluster at 275.99, so the column claimed no ink over the box's own
        # 12pt and the box merged leftward into the caption -- a printed box a
        # taxpayer must tick, with nowhere to tick it. A drawn rule is evidence
        # of a boundary where it was drawn; which side of a mean it falls on is
        # not a fact about the paper.
        own = union_intervals((r[along[0]], r[along[1]]) for r in rules)
        near_centre = union_intervals((r[along[0]], r[along[1]]) for r in all_ink
                                      if abs(centre(r) - self.position) <= CLUSTER_TOL_PT)
        self.span = union_intervals([*own, *near_centre])


def bars_over(bars: Sequence[Bar], where: Sequence[Interval]) -> list[Bar]:
    """The bars of a cluster that run where `where` runs."""
    return [b for b in bars if overlap_length([(b.start, b.end)], where) > 0]


def is_one_boundary(lower: GroupGeometry, upper: GroupGeometry) -> bool:
    """True when two collinear clusters are two bars of ONE drawn boundary.

    The comparison is local. A cluster gathers every collinear fragment on the
    page, so its ink extent and its length belong to no single place: the
    0.24pt hairline at 1600WP x=357.00 shares a cluster with three 0.76pt bars
    500pt further down the sheet. Each cluster is therefore cut down to the
    bars that actually run where the other one runs, and the test is applied to
    those.

    The test itself is ink against paper, never distance. A boundary drawn as a
    stack of bars -- a hairline lying on a bar, or the two rules of a double
    rule -- leaves either no paper at all between the bars or a white core
    thinner than the bars around it, and reads as one heavier line. A real pair
    of boundaries encloses more paper than its own ink: the narrowest genuine
    cell in the corpus is the 4.8pt dash gap between two TIN comb groups
    (2550M x=99.84), 4.08pt of paper inside two 0.72pt edges.

    Length decides the rest, because bars of one boundary are drawn over the
    same run. Where the bars physically overlap there is no paper between them
    wherever they coincide, so it is enough that the shorter one is shadowed --
    that is a 5pt corner tick sitting on a full-width rule (2553 y=39.4). Where
    paper separates them, both have to match, which stops a row of four 14pt
    field underlines being swallowed by the Part header bar 2.2pt below them
    (1601C y=124.4).

    Fragments of one rule that merely *follow* each other down the page never
    fuse here, however far their ink overlaps, because moving a page frame to
    the average of its own jogs drags every cell edge on that side with it.
    `encloses_paper` deals with those where they do damage.
    """
    here = bars_over(lower.bars, upper.span)
    there = bars_over(upper.bars, lower.span)
    if not here or not there:
        return False

    paper = min(b.near for b in there) - max(b.far for b in here)
    if paper >= max(b.thickness for b in here) + max(b.thickness for b in there):
        return False

    runs = (union_intervals((b.start, b.end) for b in here),
            union_intervals((b.start, b.end) for b in there))
    lengths = (total_length(runs[0]), total_length(runs[1]))
    if min(lengths) <= 0:
        return False
    shared = overlap_length(*runs)
    return shared >= MIN_BOUNDARY_OVERLAP * (min(lengths) if paper <= 0 else max(lengths))


def fuse_boundaries(groups: Sequence[GroupGeometry]) -> list[list[GroupGeometry]]:
    """Merge runs of clusters that together draw one boundary."""
    fused: list[list[GroupGeometry]] = []
    for group in groups:
        if fused and is_one_boundary(fused[-1][-1], group):
            fused[-1].append(group)
        else:
            fused.append([group])
    return fused


def build_lattice(defining: Sequence[dict[str, Any]], all_ink: Sequence[dict[str, Any]],
                  axis: str) -> Lattice:
    """Cluster `defining` rules into lattice lines, then measure coverage.

    Positions come only from `defining` (borders), so comb dividers never
    invent a column. Coverage comes from `all_ink` (borders + combs), because
    a border crossing a comb band is drawn thinner *inside* the band and would
    otherwise read as three disconnected fragments.

    Clustering happens twice, on two different questions. Centre clustering
    gathers the collinear fragments of one bar; boundary fusion then gathers
    the bars of one composite boundary, so that the paper inside a double rule
    never becomes a cell. A lattice line that survives fusion is byte-identical
    to what centre clustering alone produced.
    """
    if not defining:
        return Lattice([], [], [], [], [])

    groups = [GroupGeometry(g, all_ink, axis) for g in cluster_collinear(defining)]

    positions: list[float] = []
    ink_lo: list[float] = []
    ink_hi: list[float] = []
    spans: list[list[Interval]] = []
    members: list[list[dict[str, Any]]] = []
    for boundary in fuse_boundaries(groups):
        rules = [r for g in boundary for r in g.rules]
        positions.append(q(sum(centre(r) for r in rules) / len(rules)))
        ink_lo.append(min(g.ink_lo for g in boundary))
        ink_hi.append(max(g.ink_hi for g in boundary))
        spans.append(union_intervals(i for g in boundary for i in g.span))
        members.append(rules)
    return Lattice(positions, ink_lo, ink_hi, spans, members)


# A knockout in a lattice line's own ink is one of three shapes, and only the
# first is ours to close:
#
#   BITE     A knockout strictly interior to ONE collinear rail: black ink
#            reaches the frame on both sides of it, and the white fragment is
#            drawn on the SAME axis as the rail it interrupts (2200C p1 rail
#            x0=30.60: black 115.22-124.94, white 124.94-126.50, black
#            126.50-132.14 -- the white fragment IS the gap, edge for edge).
#            Bridge it: the rail is one writing wall with a printing defect
#            in the middle, not two rails and a doorway.
#
#   JUNCTION A knockout on the PERPENDICULAR axis meeting the rail's own gap
#            (2200A p1 x0=580.66: black 136.94-146.30 and 146.78-153.02, a
#            0.48pt gap; the only white there is horizontal rule h26,
#            y0=146.30-146.78, x-axis, marking where the sheet severed this
#            column into a comb divider). The sheet drew a doorway on
#            purpose. Same-axis is what tells a bite from a junction, so it
#            is never relaxed to "any nearby knockout".
#
#   DOORWAY  A knockout wide enough to be a real passage rather than a
#            printing defect -- bounded by the form's own smallest fillable
#            glyph height, because anything a glyph could not fit through is
#            not writable paper either way.
#
# A fourth failure mode looks like a bite but is not one: a witness that only
# ABUTS the gap, never covering it (1800-2018 p1 y=805.3: black h-fragments
# end at x=194.92/290.33/317.69/345.07 where full-height columns cross, and
# the "knockout" fragments beside them (h178-h181) mirror the SAME x-ranges
# as the black fragments below them rather than the gap between -- the paper
# there was never bitten, it is a column crossing a double rule). With
# +/-CLUSTER_TOL_PT slack a witness that merely touches a gap edge would be
# scored as covering it (0.3pt of tolerance swallows a 0.24pt gap outright);
# coverage here is therefore checked at the strictness epsilon (1e-6), which
# only a witness that IS the gap -- edge coincides with edge -- can satisfy.
# 1604e-2018 p1 y=383.6 carries the identical abutting shape and must stay
# unbridged for the same reason.
def bridge_knockout_bites(lattice: Lattice,
                          knockouts: Sequence[dict[str, Any]],
                          axis: str, max_gap_pt: float) -> int:
    """Rejoin a lattice line's spans across a same-axis knockout bite.

    `lattice.spans[i]` already unions every collinear fragment's along-line
    extent (`build_lattice`), so a rail bitten by a later white knockout
    survives as two disjoint spans either side of the bite. Left alone, the
    cell walk reads the bite as a real doorway and the wall dissolves into a
    blank sliver instead of a comb boundary (2200C p1 item 1: only DD was
    typeable, MM and YYYY were not).

    A gap is bridged only when ALL of these hold, each one refuting a
    measured negative case above:

    * the line has 2+ disjoint spans (nothing to bridge otherwise);
    * a knockout on THIS SAME axis is collinear with the line, within
      `CLUSTER_TOL_PT` of its clustered centre (excludes JUNCTION);
    * the gap is `0 < gap < max_gap_pt`, `max_gap_pt` coming from the form's
      own `min_fillable_line_metrics(ir)["glyph_height_pt"]` -- a gap no
      glyph could occupy is not a writable doorway either way, so caller
      passes `0.0` when metrics are unavailable and this function then
      bridges nothing (excludes DOORWAY);
    * some local witness's own along-axis extent covers the gap edge to
      edge, at the strictness epsilon `1e-6`, never `CLUSTER_TOL_PT`
      (excludes the abutting-witness case above).

    Witness THICKNESS is never compared: `line_thickness_gray` reports the
    page-wide cluster maximum, not the local fragment's own weight (2000-DST
    reports 0.96 for a rail whose local fragments are 0.48), so a thickness
    test would misfire on real bites.

    Mutates `lattice.spans` in place and returns the number of gaps bridged.
    """
    if max_gap_pt <= 0.0 or not knockouts:
        return 0
    al0, al1 = ("y0", "y1") if axis == "v" else ("x0", "x1")
    bridged = 0
    for i, spans in enumerate(lattice.spans):
        if len(spans) < 2:
            continue
        local = [k for k in knockouts
                 if k.get("axis") == axis
                 and abs(centre(k) - lattice.positions[i]) <= CLUSTER_TOL_PT]
        if not local:
            continue
        merged: list[list[float]] = [list(spans[0])]
        for a0, a1 in spans[1:]:
            prior_end = merged[-1][1]
            gap = a0 - prior_end
            witnessed = 0 < gap < max_gap_pt and any(
                k[al0] <= prior_end + 1e-6 and k[al1] >= a0 - 1e-6
                for k in local)
            if witnessed:
                merged[-1][1] = a1
                bridged += 1
            else:
                merged.append([a0, a1])
        lattice.spans[i] = [(lo, hi) for lo, hi in merged]
    return bridged


def line_thickness_gray(lattice: Lattice, index: int, all_ink: Sequence[dict[str, Any]],
                        lo: float, hi: float, axis: str
                        ) -> tuple[float, float, list[float],
                                   list[dict[str, Any]]]:
    """Weight and tone of the ink on lattice line `index` over span lo..hi.

    Thickness is the maximum: where a border thins to 0.24 crossing a comb
    band its real weight is the 0.48 it carries everywhere else. Tone is the
    darkest, because a border is as visible as its darkest segment.

    The line's own defining rules count whatever their distance from the
    clustered centre. On a fused composite boundary the centre sits in the
    white core of the double rule, further from either bar than the clustering
    tolerance, and a distance-only scan would report the boundary as absent.
    """
    a0, a1 = ("x0", "x1") if axis == "h" else ("y0", "y1")
    c0, c1 = ("y0", "y1") if axis == "h" else ("x0", "x1")
    position = lattice.positions[index]
    own = {id(r) for r in lattice.members[index]}
    hits = [r for r in all_ink
            if (abs(centre(r) - position) <= CLUSTER_TOL_PT or id(r) in own)
            and r[a1] > lo - CLUSTER_TOL_PT and r[a0] < hi + CLUSTER_TOL_PT]
    if not hits:
        hits = lattice.members[index]
    thicknesses = sorted({r["thickness_pt"] for r in hits})
    grays = [r["gray"] for r in hits if r["gray"] is not None]
    # The per-segment geometry, published beside the fused maximum so a comb
    # can later ask what stands over ITS OWN span (C1): each hit's extent
    # along the line and its ink band across it.  The fused thickness answers
    # "how heavy is this border"; the segments answer "which ink is where".
    segments = [
        {
            "a0": q(float(r[a0])), "a1": q(float(r[a1])),
            "c0": q(float(r[c0])), "c1": q(float(r[c1])),
            "thickness_pt": float(r["thickness_pt"]),
            "gray": r["gray"],
        }
        for r in sorted(hits, key=lambda r: (float(r[a0]), float(r[c0])))
    ]
    return (max(thicknesses), min(grays) if grays else 0.0, thicknesses,
            segments)


# ---------------------------------------------------------------------------
# Cells
# ---------------------------------------------------------------------------


def order_rects_reading_order(
        items: Sequence[dict[str, Any]],
        x0_of: Any, y0_of: Any, x1_of: Any, y1_of: Any,
        ) -> list[dict[str, Any]]:
    """Column-aware reading order for tab/DOM (F209).

    A global (y, x) sort zig-zags across side-by-side sections: 0605 items 17
    and 18 share some row y-values, so tab walked 17's first checkbox, then
    18's, then back to 17. Readers finish item 17, then item 18.

    A regular table must NOT become column-major. Table columns share the same
    row y-values; 17/18 do not. A full-height vertical cut whose two sides are
    each multi-row AND whose y0-sets differ is therefore a section split and
    is taken first. Otherwise a clean horizontal cut (stacked sections, table
    rows) is taken. Otherwise (y, x).
    """
    eps = CLUSTER_TOL_PT

    def rec(group: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if len(group) <= 1:
            return list(group)
        xs = sorted({q(x1_of(item)) for item in group}
                    | {q(x0_of(item)) for item in group})
        gx0 = min(x0_of(item) for item in group)
        gx1 = max(x1_of(item) for item in group)
        for cut in xs:
            if cut <= gx0 + eps or cut >= gx1 - eps:
                continue
            left = [item for item in group if x1_of(item) <= cut + eps]
            right = [item for item in group if x0_of(item) >= cut - eps]
            if (not left or not right
                    or len(left) + len(right) != len(group)):
                continue
            y_left = {q(y0_of(item)) for item in left}
            y_right = {q(y0_of(item)) for item in right}
            if (len(y_left) < 2 or len(y_right) < 2
                    or y_left == y_right):
                continue
            return rec(left) + rec(right)

        ys = sorted({q(y1_of(item)) for item in group}
                    | {q(y0_of(item)) for item in group})
        gy0 = min(y0_of(item) for item in group)
        gy1 = max(y1_of(item) for item in group)
        for cut in ys:
            if cut <= gy0 + eps or cut >= gy1 - eps:
                continue
            above = [item for item in group if y1_of(item) <= cut + eps]
            below = [item for item in group if y0_of(item) >= cut - eps]
            if (not above or not below
                    or len(above) + len(below) != len(group)):
                continue
            return rec(above) + rec(below)

        return sorted(group, key=lambda item: (y0_of(item), x0_of(item)))

    return rec(list(items))


class DisjointSet:
    def __init__(self, size: int) -> None:
        self.parent = list(range(size))

    def find(self, a: int) -> int:
        while self.parent[a] != a:
            self.parent[a] = self.parent[self.parent[a]]
            a = self.parent[a]
        return a

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            # Always keep the lower index as root so components are stable.
            self.parent[max(ra, rb)] = min(ra, rb)


def merge_grid(xl: Lattice, yl: Lattice) -> tuple[DisjointSet, list[list[bool]], list[list[bool]]]:
    """Fuse adjacent grid squares that no rule separates.

    A lattice line existing somewhere on the page says nothing about whether it
    bounds a given square, so every adjacency is decided by an explicit span
    coverage test.
    """
    nx, ny = len(xl) - 1, len(yl) - 1
    # v_at[i][j]: vertical lattice line i carries ink across grid row j.
    v_at = [[covers(xl.spans[i], yl.ink_hi[j], yl.ink_lo[j + 1]) for j in range(ny)]
            for i in range(len(xl))]
    h_at = [[covers(yl.spans[j], xl.ink_hi[i], xl.ink_lo[i + 1]) for i in range(nx)]
            for j in range(len(yl))]

    dsu = DisjointSet(max(nx * ny, 1))
    for j in range(ny):
        for i in range(nx):
            here = j * nx + i
            if i + 1 < nx and not v_at[i + 1][j]:
                dsu.union(here, here + 1)
            if j + 1 < ny and not h_at[j + 1][i]:
                dsu.union(here, here + nx)
    return dsu, v_at, h_at


def distinct_boundary(here: InkSpan, there: InkSpan) -> bool:
    """Whether paper survives between two bars, making two boundaries."""
    paper = max(here[0], there[0]) - min(here[1], there[1])
    return paper > here[2] + there[2] + JOIN_EPSILON_PT


def endpoint_band(seed: Sequence[dict[str, Any]],
                  extra: Sequence[dict[str, Any]],
                  x0: float, x1: float,
                  frame: Sequence[InkSpan],
                  final_paint: FinalPaint
                  ) -> tuple[list[dict[str, Any]], float, float,
                             list[dict[str, Any]], bool] | None:
    """Final-visible endpoint topology plus every competing topology.

    Heavy digit-group separators are often nested inside the thin character
    ticks rather than sharing both endpoints. On 2550Q, for example, the thin
    ticks run y=141.62..147.92 while each 2.20pt separator runs
    y=142.12..147.92. Requiring the heavy bar to contain the whole thin seed
    drops exactly three boundaries from every 12-slot money field.

    Partitioning at every endpoint exposes the common intersection directly:
    one 0.50pt slab has the eight thin ticks and the remaining 5.80pt slab has
    all eleven boundaries. Coverage is useful only to choose a deterministic
    representation; it cannot prove that one source topology owns the field.
    Every non-identical topology is therefore returned as evidence and makes
    the resulting comb unresolved. The returned y range is the longest
    continuous slab carrying the representative topology, so every reported
    divider really spans the reported band.

    A raw rule is not evidence by itself. Each slab is composited through
    ``FinalPaint`` first, which prevents a later white knockout from reviving a
    stale divider anchor.
    """
    if not seed:
        return None
    band_y0 = min(float(ink["y0"]) for ink in seed)
    band_y1 = max(float(ink["y1"]) for ink in seed)
    if band_y1 <= band_y0:
        return None

    # `extra` contains the vertical rule list as well as thick area fills, so
    # the seed objects occur twice. Geometry plus source ordinal is their stable
    # identity; no iteration-order accident may add the same boundary twice.
    pool: list[dict[str, Any]] = []
    seen: set[tuple[float, float, float, float, float, int, int]] = set()
    for ink in [*seed, *extra]:
        if ink["x0"] <= x0 + CLUSTER_TOL_PT or ink["x1"] >= x1 - CLUSTER_TOL_PT:
            continue
        if ink["y1"] <= band_y0 or ink["y0"] >= band_y1:
            continue
        first, last = paint_ordinal_range(ink)
        key = (float(ink["x0"]), float(ink["y0"]),
               float(ink["x1"]), float(ink["y1"]),
               float(ink["thickness_pt"]), first, last)
        if key in seen:
            continue
        seen.add(key)
        pool.append(ink)
    if not pool:
        return None

    seed_keys = set()
    for ink in seed:
        first, last = paint_ordinal_range(ink)
        seed_keys.add((
            float(ink["x0"]), float(ink["y0"]),
            float(ink["x1"]), float(ink["y1"]),
            float(ink["thickness_pt"]), first, last,
        ))
    endpoints = {band_y0, band_y1}
    for ink in pool:
        for a, b in final_paint.visible_intervals(ink):
            lo, hi = max(band_y0, a, float(ink["y0"])), min(
                band_y1, b, float(ink["y1"]))
            if hi > lo:
                endpoints.update((lo, hi))

    slab_records: list[tuple[float, float, tuple[float, ...],
                             list[dict[str, Any]]]] = []
    evidence_slab_records: list[tuple[
        float, float, tuple[float, ...], list[dict[str, Any]]
    ]] = []
    ordered = sorted(endpoints)
    for a, b in zip(ordered, ordered[1:]):
        if b <= a:
            continue
        horizontal_rail = final_paint.horizontal_rail_across(x0, x1, a, b)
        active = [
            ink for ink in pool
            if ink["y0"] <= a + JOIN_EPSILON_PT
            and ink["y1"] >= b - JOIN_EPSILON_PT
            # A fully overpainted vertical does not become a slot divider just
            # because an unrelated horizontal rail crosses its old bbox. Exact
            # contributor order lets ``definitely_erased`` distinguish that
            # stale source mark from a genuine late repaint at the same x.
            and not final_paint.definitely_erased(ink)
            and final_paint.structural_across(ink, a, b)
        ]
        if not any((
                float(ink["x0"]), float(ink["y0"]),
                float(ink["x1"]), float(ink["y1"]),
                float(ink["thickness_pt"]), *paint_ordinal_range(ink),
        ) in seed_keys for ink in active):
            continue

        # Later paint wins when coincident records describe one boundary; a
        # thicker bar then wins a source-order tie. Distinct x boundaries remain
        # sorted left-to-right regardless of input list order.
        active.sort(key=lambda ink: (
            centre(ink), -paint_ordinal(ink), -float(ink["thickness_pt"]),
            float(ink["y0"]), float(ink["y1"])))
        taken = list(frame)
        topology_ink: list[dict[str, Any]] = []
        for ink in active:
            here = (float(ink["x0"]), float(ink["x1"]),
                    float(ink["thickness_pt"]))
            if not all(distinct_boundary(here, other) for other in taken):
                continue
            taken.append(here)
            topology_ink.append(ink)
        topology = tuple(q(centre(ink)) for ink in topology_ink)
        if topology:
            record = (a, b, topology, topology_ink)
            evidence_slab_records.append(record)
            # A full-width horizontal rail contains no paper that can
            # establish the direction of crossing ink. Boundaries proved only
            # inside it stay in the conflict evidence, but cannot win topology
            # selection or certify a comb by themselves.
            if not horizontal_rail:
                slab_records.append(record)

    # Paper-bearing coverage per topology, measured before the rail-only
    # fallback below can substitute conflict evidence for selection slabs.
    # Published with each evidence entry so a consumer can tell a topology
    # that exists on paper from one proven only inside a horizontal rail.
    paper_coverage: dict[tuple[float, ...], float] = collections.defaultdict(float)
    for a, b, topology, _ in slab_records:
        paper_coverage[topology] += b - a

    horizontal_rail_only = not slab_records and bool(evidence_slab_records)
    if horizontal_rail_only:
        # Preserve an already-published subject as unresolved evidence.  It
        # cannot certify topology, but removing it here would silently change
        # subject identity before an independent transition adjudicates it.
        slab_records = list(evidence_slab_records)
    if not slab_records:
        return None

    coverage: dict[tuple[float, ...], float] = collections.defaultdict(float)
    for a, b, topology, _ in slab_records:
        coverage[topology] += b - a
    evidence_coverage: dict[tuple[float, ...], float] = (
        collections.defaultdict(float))
    for a, b, topology, _ in evidence_slab_records:
        evidence_coverage[topology] += b - a

    def continuous_runs(
            records: Sequence[tuple[float, float]],
            topology: tuple[float, ...],
            ) -> list[Interval]:
        """Join adjacent slabs only while every divider has one ink witness."""
        runs: list[Interval] = []
        for a, b in sorted(records):
            if not runs:
                runs.append((a, b))
                continue
            run_start, run_end = runs[-1]
            continuous = (
                a <= run_end + JOIN_EPSILON_PT
                and all(any(
                    q(centre(ink)) == divider_x
                    and float(ink["y0"]) <= run_start + JOIN_EPSILON_PT
                    and float(ink["y1"]) >= b - JOIN_EPSILON_PT
                    and final_paint.structural_across(ink, run_start, b)
                    for ink in pool
                ) for divider_x in topology)
            )
            if continuous:
                runs[-1] = (run_start, max(run_end, b))
            else:
                runs.append((a, b))
        return runs

    topology_runs: dict[tuple[float, ...], list[Interval]] = {}
    topology_evidence: list[dict[str, Any]] = []
    for topology in sorted(evidence_coverage):
        records = sorted(
            (a, b) for a, b, candidate, _inks in evidence_slab_records
            if candidate == topology)
        runs = continuous_runs(records, topology)
        topology_runs[topology] = runs
        hull_start, hull_end = records[0][0], records[-1][1]
        corridors_continuous = all(any(
            q(centre(ink)) == divider_x
            and float(ink["y0"]) <= hull_start + JOIN_EPSILON_PT
            and float(ink["y1"]) >= hull_end - JOIN_EPSILON_PT
            and final_paint.structural_across(ink, hull_start, hull_end)
            for ink in pool
        ) for divider_x in topology)
        topology_evidence.append({
            "divider_x": list(topology),
            "coverage_pt": q(evidence_coverage[topology]),
            "paper_coverage_pt": q(paper_coverage.get(topology, 0.0)),
            "runs": [[q(a), q(b)] for a, b in runs],
            "corridors_continuous": corridors_continuous,
        })
    maximal = [
        topology for topology in coverage
        if not any(
            set(topology) < set(other)
            for other in coverage
        )
    ]
    # Representation is not adjudication: when one topology contains every
    # competing topology, carry that complete measured divider set while the
    # resolution remains explicitly unresolved. If alternatives are
    # incomparable, retain the old deterministic display choice only.
    chosen = (
        maximal[0] if len(maximal) == 1
        else min(coverage, key=lambda topology: (
            -coverage[topology], -len(topology), topology))
    )
    # Selection deliberately excludes horizontal-rail-only slabs. Derive the
    # representative run from that same selection set; evidence runs may have
    # a longer disjoint rail segment with no selectable representative.
    runs = continuous_runs([
        (a, b) for a, b, topology, _inks in slab_records
        if topology == chosen
    ], chosen)
    chosen_y0, chosen_y1 = min(
        runs, key=lambda span: (-(span[1] - span[0]), span[0], span[1]))

    representatives = min(
        (inks for a, b, topology, inks in slab_records
         if topology == chosen
         and a < chosen_y1 and b > chosen_y0),
        key=lambda inks: tuple(
            (q(centre(ink)), -paint_ordinal(ink),
             -float(ink["thickness_pt"])) for ink in inks),
    )
    return (representatives, chosen_y0, chosen_y1, topology_evidence,
            horizontal_rail_only)


def band_ink(extra: Sequence[dict[str, Any]], x0: float, x1: float,
             band_y0: float, band_y1: float,
             claimed: Sequence[InkSpan]) -> list[dict[str, Any]]:
    """Legacy full-span query retained for focused callers and self-tests.

    A slot boundary is black ink drawn from the band's top edge to its bottom
    edge, inside the field. Nothing else about it matters, and in particular
    neither thickness nor end support does:

      * a digit-group separator that runs the whole row height is supported at
        both ends, so `split_verticals` correctly calls it a border -- 2200C
        x=59.76 is a 0.48pt bar spanning y 115.22-132.14 across the item-1 comb
        band at y 126.50-132.14, and it is one of that comb's slot boundaries;
      * above 1.5pt a bar is not a rule at all. 1707's TIN separators are
        2.16 x 6.96pt black rects, so extract.py files them as area fills and
        they never reach the vertical list. Fourteen printed TIN boxes became
        eleven slots and a typed character centred on top of the black bar.

    Thickness *ranks* a boundary -- 0.24 is a character tick, 0.96/1.44/2.16 a
    group separator -- and both ranks stay visible in
    `divider_thicknesses_pt`. It never decides whether the boundary exists.

    Containment, not the centre, is the x test here: an extra may be as wide as
    a slot, and a fill spanning the whole cell has its centre inside the cell
    while bounding nothing.

    Two pieces of ink are the same boundary when the paper between them is
    thinner than the ink drawing them -- `is_one_boundary`'s test, applied for
    the same reason one lattice line away. It settles both ends of the problem
    with no new constant:

      * 0605 x=226.0 draws one bar as a 0.14pt hairline overlapping a 0.96pt bar.
        Their centres are 0.42pt apart, further than any clustering tolerance,
        and they are still one line.
      * 1701A x=599.04 is the inner bar of the right page frame, 1.86pt of paper
        inside the 1.44pt bar the cell ends on. Counting it would add a 1.86pt
        slot no character fits in. A composite boundary is not a slot boundary
        twice.

    The narrowest genuine slot in the corpus survives this: the 4.08pt TIN dash
    gap at 2550M x=99.84 holds 4.08pt of paper inside two 0.72pt edges.
    """
    taken = list(claimed)
    found: list[dict[str, Any]] = []
    for ink in extra:
        if ink["x0"] <= x0 + CLUSTER_TOL_PT or ink["x1"] >= x1 - CLUSTER_TOL_PT:
            continue
        if ink["y0"] > band_y0 + CLUSTER_TOL_PT or ink["y1"] < band_y1 - CLUSTER_TOL_PT:
            continue
        here = (ink["x0"], ink["x1"], ink["thickness_pt"])
        if not all(distinct_boundary(here, other) for other in taken):
            continue
        taken.append(here)
        found.append(ink)
    return found


class CombOwnerPaper:
    """The paper one comb owner encloses, as its rails are measured against.

    ``y0``/``y1`` are the owner's own paper -- its top border's inner edge to
    its bottom border's inner edge, i.e. ``yl.ink_hi[j0]``/``yl.ink_lo[j1]``,
    the same numbers ``comb_band_owners`` binds ownership to. A boundary that
    crosses all of it is a WALL: it closes a box. A boundary that stops inside
    it is a guide TICK, which is what `comb_writing_surface` already says the
    source draws at the foot of a taller box.

    ``left``/``right`` are the ink of the owner's own vertical edges, which is
    not the same thing as the lattice line's position: fusion moves a line to
    the MEAN centre of every collinear fragment it gathered, and the fragment
    that actually crosses the comb band can be a third of a point away from
    that mean (1800 fuses 584.26/584.50/584.74x2 into 584.56 while the bar
    over the comb is the one at 584.26).
    """

    __slots__ = ("y0", "y1", "left", "right")

    def __init__(self, y0: float, y1: float,
                 left: Sequence[dict[str, Any]],
                 right: Sequence[dict[str, Any]]) -> None:
        self.y0 = float(y0)
        self.y1 = float(y1)
        self.left = list(left)
        self.right = list(right)


def owner_paper_from_lattice(xl: Lattice, yl: Lattice,
                             i0: int, i1: int, j0: int, j1: int,
                             all_ink: Sequence[dict[str, Any]],
                             ) -> CombOwnerPaper:
    """The paper and edge ink of the cell spanning lattice box (i0,j0)-(i1,j1).

    An edge's ink is the boundary's own defining bars PLUS anything else drawn
    inside that boundary's measured ink. Both halves are needed and neither is
    redundant: the defining list is the only place a bar the compositor
    proved erased survives (`build_page` retains it as a companion so the
    line's centre does not move), and the ink list is the only place a bar
    `split_verticals` filed as a comb divider appears -- 1801's item-24 money
    comb is closed on the right by a 0.48pt stroke that hangs from nothing.
    """
    def edge(index: int) -> list[dict[str, Any]]:
        lo = xl.ink_lo[index] - JOIN_EPSILON_PT
        hi = xl.ink_hi[index] + JOIN_EPSILON_PT
        seen: set[tuple[float, float, float, float, float]] = set()
        out: list[dict[str, Any]] = []
        for ink in [*xl.members[index], *all_ink]:
            if float(ink["x1"]) <= lo or float(ink["x0"]) >= hi:
                continue
            key = (float(ink["x0"]), float(ink["y0"]),
                   float(ink["x1"]), float(ink["y1"]),
                   float(ink["thickness_pt"]))
            if key in seen:
                continue
            seen.add(key)
            out.append(ink)
        return out

    return CombOwnerPaper(yl.ink_hi[j0], yl.ink_lo[j1], edge(i0), edge(i1))


def ink_runs(candidates: Sequence[dict[str, Any]],
             final_paint: FinalPaint | None,
             ) -> list[tuple[float, float, float]]:
    """The y runs one column of collinear ink covers, with their own weight.

    Fragments join where the paper between them is thinner than the ink either
    side of it -- `distinct_boundary`'s test, turned through a right angle,
    which is also how `is_one_boundary` decides that two bars draw one line.
    A wall regularly arrives in pieces: 1800 breaks the column closing its
    middle date box where that box's own top rule crosses it, leaving 0.24pt
    of paper between two strokes 0.48 and 0.24pt wide. A break that cannot be
    as wide as the stroke is thick does not print as a break.

    They do NOT join across an erased corridor, however narrow. A knockout
    painted over the junction is the source saying the stroke stops there --
    the same reading `source_owned_comb_frame.separated_top_rail_stub` already
    takes of the same evidence -- and it is exactly what separates two forms
    that draw the same shape: 2200-A erases the junction between its YYYY
    column and the row above, 1800 leaves 0.24pt of paper beside its knockout
    and the column carries through.

    `final_paint` is optional because the legacy continuity detector is
    deliberately raw: it measures the ink the source drew, without asking
    whether a later knockout removed it. Passing it here would give the
    continuity denominator a final-paint opinion it is defined not to have.
    """
    spans: list[tuple[float, float, float, dict[str, Any]]] = []
    for ink in candidates:
        weight = float(ink["thickness_pt"])
        if final_paint is None:
            spans.append((float(ink["y0"]), float(ink["y1"]), weight, ink))
        else:
            spans.extend(
                (lo, hi, weight, ink)
                for lo, hi in final_paint.visible_intervals(ink))

    def junction_erased(previous: dict[str, Any], ink: dict[str, Any],
                        gap_y0: float, gap_y1: float) -> bool:
        if final_paint is None or gap_y1 <= gap_y0:
            return False
        x0 = max(float(previous["x0"]), float(ink["x0"]))
        x1 = min(float(previous["x1"]), float(ink["x1"]))
        if x1 <= x0:
            x0 = min(float(previous["x0"]), float(ink["x0"]))
            x1 = max(float(previous["x1"]), float(ink["x1"]))
        # Later than the stroke it interrupts, not later than both: the sheet
        # draws the column, paints the slice out, and draws the rest below it.
        last = min(paint_ordinal_range(previous)[1],
                   paint_ordinal_range(ink)[1])
        return final_paint.definitely_erased({
            "x0": x0, "y0": gap_y0, "x1": x1, "y1": gap_y1,
            "paint_seq": last, "paint_seq_max": last,
        })

    runs: list[tuple[float, float, float, dict[str, Any]]] = []
    for lo, hi, weight, ink in sorted(spans, key=lambda item: item[:3]):
        if (runs
                and lo - runs[-1][1] <= runs[-1][2] + weight + JOIN_EPSILON_PT
                and not junction_erased(runs[-1][3], ink, runs[-1][1], lo)):
            runs[-1] = (runs[-1][0], max(runs[-1][1], hi),
                        max(runs[-1][2], weight), ink)
        else:
            runs.append((lo, hi, weight, ink))
    return [(lo, hi, weight) for lo, hi, weight, _ink in runs]


def edge_rail_bars(edge_ink: Sequence[dict[str, Any]], edge: InkSpan,
                   band_y0: float, band_y1: float) -> list[dict[str, Any]]:
    """The owner's own edge bars that rule this comb band, in drawn order.

    Split out from `edge_rail` because two different questions are asked of
    exactly the same bars and neither may be answered from a different
    membership: WHERE the rail is (their mean centre, `edge_rail`) and HOW WIDE
    IT IS PAINTED (their ink envelope, `comb_rails`). A rail whose position came
    from one set of bars and whose ink came from another would inset a comb off
    a stroke that is not the one bounding it.
    """
    return [
        ink for ink in edge_ink
        if float(ink["y0"]) <= band_y0 + JOIN_EPSILON_PT
        and float(ink["y1"]) >= band_y1 - JOIN_EPSILON_PT
        and not distinct_boundary(
            (float(ink["x0"]), float(ink["x1"]),
             float(ink["thickness_pt"])), edge)
    ]


def boundary_stack(boundaries: Sequence[dict[str, Any]],
                   position: float) -> list[dict[str, Any]]:
    """Every bar of one boundary, selected by the position it was reduced to.

    A boundary's published position is `q(centre(ink))`, and several bars can
    reduce to the same one -- which is precisely when the ink envelope differs
    from any single bar's rect. Selecting by the published value rather than by
    identity keeps the stack and the position two readings of one boundary.
    """
    return [ink for ink in boundaries if q(centre(ink)) == position]


def ink_envelope(inks: Sequence[dict[str, Any]]) -> Interval | None:
    """The x extent one boundary's drawn bars actually cover, or None.

    The envelope, not one bar's own rect: a boundary regularly arrives as a
    stack (2550M draws its date-box columns as two 0.72pt bars) and the paper a
    comb may be written on begins after ALL of that stack's ink, not after the
    first bar of it.
    """
    if not inks:
        return None
    return (q(min(float(ink["x0"]) for ink in inks)),
            q(max(float(ink["x1"]) for ink in inks)))


def edge_rail(edge_ink: Sequence[dict[str, Any]], edge: InkSpan,
              band_y0: float, band_y1: float) -> float | None:
    """Where the owner's own edge actually rules the comb band, or None.

    Two restrictions, and they are the whole difference from the lattice line
    the edge belongs to:

      * only the bars that CROSS THIS BAND count. A lattice line is fused from
        every collinear fragment on the page and sits at their mean centre, so
        it answers "where is this column", not "where is this comb's edge".
        1800's right column fuses bars at 584.26, 584.50 and 584.74 into a line
        at 584.56 while the bar ruling the comb band is the one at 584.26.
      * only ink the band-boundary test would already have refused as a slot
        divider counts -- `distinct_boundary` against the edge's own span, the
        same question `band_ink` asks -- so nothing that could have been a
        compartment boundary is quietly promoted to a rail.

    The position is then the mean centre of those bars, which is how a
    boundary drawn as a stack of bars has always been positioned here
    (`GroupGeometry.position`), applied to this band rather than to the page.

    Membership is deliberately the drawn stack, knocked-out companions
    included: `build_page` already retains every bar of a fused composite
    boundary once one of them survives, precisely so that a later knockout
    cannot move a published centre, and a rail measured on a different
    membership rule from the line it stands on would be a second answer to
    the same question. 2550M draws its date-box columns as two 0.72pt bars and
    covers the second one's shaft; the boundary is still the pair.
    """
    hits = edge_rail_bars(edge_ink, edge, band_y0, band_y1)
    if not hits:
        return None
    return q(sum(centre(ink) for ink in hits) / len(hits))


def divides_owner_paper(x: float, candidates: Sequence[dict[str, Any]],
                        paper_y0: float, paper_y1: float,
                        final_paint: FinalPaint | None) -> bool:
    """Whether the ink at one boundary crosses the whole of the owner's paper.

    The chain is assembled from every collinear fragment at that x by
    `ink_runs`, and meets the paper's own top and bottom rails on the same
    terms it joins its own fragments: a stroke that stops within its own width
    of the rule closing the box has met that rule (1800's column stops 0.12pt
    short of the 1.44pt rule above it). Where final paint is available only
    visible span counts: a wall a later knockout erased no longer closes
    anything.
    """
    return any(
        lo - paper_y0 <= weight + JOIN_EPSILON_PT
        and paper_y1 - hi <= weight + JOIN_EPSILON_PT
        for lo, hi, weight in ink_runs(
            [ink for ink in candidates
             if abs(centre(ink) - x) <= CLUSTER_TOL_PT],
            final_paint))


# How each outer rail came to be where it is, published beside the comb so that
# a rail the cell's own edge did NOT decide is inspectable rather than merely
# different from the cell.
RAIL_AT_OWNER_EDGE = "owner-edge"
RAIL_TRIMMED_TO_WALL = "interior-wall"
RAIL_TRIMMED_TO_UNGUIDED_OUTER_PAPER = "unguided-outer-paper"

# ...and, where the rail stayed on the owner's edge, WHICH clause kept it
# there. A refusal that does not name itself is indistinguishable from a
# question never asked, and this is exactly the population an adjudicator has
# to be able to re-examine: every one of them is a compartment we publish on
# the cell box rather than on measured ink.
RAIL_REFUSED_NO_OWNER_PAPER = "no-owner-paper"
RAIL_REFUSED_NO_TICK_RUN = "no-tick-run"
RAIL_REFUSED_NO_INTERIOR_PITCH = "comb-has-no-interior-pitch"
RAIL_REFUSED_WITHIN_PITCH = "outer-paper-within-comb-pitch"
RAIL_REFUSED_GUIDED = "guided-outer-paper"


def comb_interior_pitch(positions: Sequence[float]) -> float | None:
    """The pitch of one comb's GUIDE RUN, measured between its ticks only.

    Two exclusions, and each is the reason this is not `comb_bands`'s
    `pitch_pt`:

      * the rails are out, because this is the statistic the rails are DECIDED
        from (`outer_paper_unguided`) and a rail may not be an input to the
        measurement that places it;
      * the WALLS are out, because a wall is a box edge and not a member of
        the run. The paper the trim asks about is the paper outside the TICK
        RUN, so the run is what measures it: a comb with one tick and one wall
        has a gap, but that gap is a box, not a compartment pitch.

    Between the ticks every gap is a compartment the sheet drew two guides
    for, and the modal gap is chosen exactly the way `comb_bands` chooses the
    pitch it publishes -- ties to the smaller value, for determinism.

    None where the run has fewer than THREE marks, which is two gaps. One gap
    is a distance and not a pitch, and the raw legacy reading says so
    directly: 2550M `p1c89` draws its genuine divider at 260.40 and a stale
    mark the sheet later replaced at 263.52, and the pair "measures" a 3.12pt
    pitch that would condemn the 13.44pt of paper beside it -- the same shape
    `bottom_guide_tick_baseline` refuses on 0605, where a knocked-out mark
    sits 3.12pt from a genuine centavo divider. Two marks are a pair; a run
    needs a third before it has said anything twice.
    """
    xs = sorted({q(value) for value in positions})
    if len(xs) < 3:
        return None
    gaps = [q(b - a) for a, b in zip(xs, xs[1:])]
    pitch = min(collections.Counter(gaps).most_common(),
                key=lambda kv: (-kv[1], kv[0]))[0]
    return pitch if pitch > 0.0 else None


def outer_paper_unguided(rail: float, outermost: float,
                         ticks: Sequence[float],
                         extra: Sequence[dict[str, Any]],
                         band_y0: float, band_y1: float,
                         final_paint: FinalPaint | None,
                         ) -> dict[str, Any]:
    """Evidence that the paper outside a comb's outermost tick is not a slot.

    The wall trim above answers the same question where the sheet CLOSES the
    caption off: 1801 item 24 rules 366pt of caption before its money comb and
    the comb's first compartment starts at that rule.  Where the sheet closes
    nothing the old reading fell back on the cell's nominal edge and published
    the caption as a compartment -- 2200-A/C/P item 27 hands a 173.66pt "box"
    to a 14.52pt money comb, over the printed words "Tax Debit Memo", and 1801
    item 5 hands a 183.05pt one to a 14.16pt TIN comb over "Taxpayer
    Identification Number".  A rail has to be measured from the ink either way.

    Two things could still make that paper a compartment, and each gets its own
    clause; the rail moves only where both refuse:

      * it could be ONE compartment the sheet simply drew wider than the rest.
        So allow it one, of the guide run's own pitch, and ask whether MORE
        than another whole compartment of paper is still left over: the rail
        moves only where `width > 2 * pitch`.  It is the same shape as the
        test `comb_bands` already applies to an unequal pair ("a single
        divider cannot prove two character compartments when one side can
        hold at least two copies of the other"), it carries no new constant,
        and it is measured against this comb's OWN pitch and never a corpus
        figure.  The strictness at exactly two is not arbitrary: a comb whose
        outer paper is exactly two of its compartments is this module's own
        designed negative control (`hung_certificate` in the self-test -- an
        unrailed comb that owns its whole box), and the corpus separates the
        two real populations at 1.52 and 2.21 pitches, so no measured sheet
        rests on the boundary either way.
      * it could be SEVERAL compartments whose guides this band did not
        select.  Refused by the sheet's own ink: any structural vertical
        candidate whose final-visible ink reaches into that paper over this
        band means the sheet guided it, and the rail stays where it is.  The
        test is deliberately OVERLAP and not "crosses the whole band" -- a
        guide tick is short on purpose, so half a tick is still a guide -- and
        it is asked of `extra`, the page's whole black-column candidate pool,
        rather than of the boundaries this band happened to select.

    White marks are not ink for this purpose and cannot be: `extra` is built
    from `role == "structural"` verticals only (`comb_boundary_candidates`),
    and `final_paint` then drops whatever a later layer erased.  2200-A/C/P
    paint four `gray: 1.0` knockouts at 131.90/146.42/160.82/175.34 exactly
    where the missing guides would stand, over no black rule at all; they erase
    nothing and they guide nothing, and admitting them would republish the
    caption compartment they are being read as evidence against.

    Always returns the measurement, whichever way it came out, so that the
    caller can publish WHY the rail is where it is rather than only that it
    moved. A refusal that does not name its clause cannot be told apart from a
    question never asked, and the refusals are the population that still
    publishes a compartment on the cell box instead of on measured ink.
    """
    lo, hi = (rail, outermost) if rail <= outermost else (outermost, rail)
    width = q(hi - lo)
    measured: dict[str, Any] = {
        "from_x": q(rail), "to_x": q(outermost), "outer_paper_pt": width,
    }
    pitch = comb_interior_pitch(ticks)
    if pitch is None:
        return {**measured, "method": RAIL_AT_OWNER_EDGE,
                "refused": RAIL_REFUSED_NO_INTERIOR_PITCH}
    measured["comb_pitch_pt"] = pitch
    measured["outer_paper_pitches"] = q(width / pitch)
    if width <= 2.0 * pitch:
        return {**measured, "method": RAIL_AT_OWNER_EDGE,
                "refused": RAIL_REFUSED_WITHIN_PITCH}
    guides = sorted(
        q(centre(ink)) for ink in extra
        if lo + CLUSTER_TOL_PT < q(centre(ink)) < hi - CLUSTER_TOL_PT
        and float(ink["y0"]) < band_y1 and float(ink["y1"]) > band_y0
        and (final_paint is None
             or (not final_paint.definitely_erased(ink)
                 and any(a < band_y1 and b > band_y0
                         for a, b in final_paint.visible_intervals(ink))))
    )
    if guides:
        return {**measured, "method": RAIL_AT_OWNER_EDGE,
                "refused": RAIL_REFUSED_GUIDED, "guide_ink_x": guides}
    return {**measured, "method": RAIL_TRIMMED_TO_UNGUIDED_OUTER_PAPER,
            "guide_ink_x": guides}


class CombRails(NamedTuple):
    """One comb's measured outer rails: where each is, and where its ink is.

    `left_x`/`right_x` are the rails' POSITIONS -- their centres -- and they are
    what `slot_x` publishes, because a boundary has always been positioned here
    at the centre of the bars drawing it.  `left_ink`/`right_ink` are the x
    extent those same bars actually paint, and they are what the compartments
    may be WRITTEN on: a rail's position runs down the middle of its own stroke,
    so a compartment laid from it is laid across half the printed rule.  `None`
    on either side means no bar of that rail was measurable over this band, and
    a caller may not invent one.

    `left_trim`/`right_trim` are WHY that side is where it is: the measurement
    that moved it inward, or -- where it stayed on the owner's own edge -- the
    clause that kept it there and that clause's own numbers. Always present on
    both sides, because "the rail is the cell box" is a conclusion and not a
    default, and a reader that cannot tell a refusal from an unasked question
    cannot re-examine either.
    """

    left_x: float
    right_x: float
    enclosed: list[dict[str, Any]]
    left_ink: Interval | None
    right_ink: Interval | None
    left_trim: dict[str, Any]
    right_trim: dict[str, Any]


def comb_rails(boundaries: Sequence[dict[str, Any]],
               extra: Sequence[dict[str, Any]],
               x0: float, x1: float,
               band_y0: float, band_y1: float,
               edge_thickness: tuple[float, float],
               paper: CombOwnerPaper | None,
               final_paint: FinalPaint | None,
               ) -> CombRails:
    """One comb's own outer rails, and the boundaries they still enclose.

    A comb's outer edges are printed rails, not the lattice cell box, and the
    two differ in two ways that both put a typeable box where the sheet prints
    none:

      * the cell box is a FUSED lattice position -- the mean centre of every
        collinear fragment on that line -- while the rail is the one bar that
        crosses this band, up to 0.47pt away from that mean;
      * a cell may legitimately hold more than the comb. 1801 rules its TIN
        dash box and its "Contact Number" caption inside the same rectangle as
        the digit boxes, and 1801 item 24 rules 366pt of caption before its
        money comb starts. Bounding the comb by the cell then publishes a
        compartment the sheet does not print, lays a slot rectangle over the
        caption, and hands the field one more character of capacity than the
        artwork has boxes for.

    The rails are therefore measured: each side is the owner's own edge ink
    where it crosses the band, moved inward to the innermost WALL outside the
    tick run when the owner encloses one. A wall closes a box (it crosses the
    owner's whole paper); a tick is a guide mark hanging inside one. Boundaries
    outside the resulting rails are the neighbouring compartment's, not this
    comb's, and are dropped from it.

    Where the sheet closes the caption off with nothing at all there is no wall
    to trim at, and the fallback to the cell's nominal edge published the
    caption as a compartment anyway (2200-A/C/P item 27, 1801 item 5). So the
    second trim: outward of the tick run the rail moves to the outermost TICK
    when the sheet's own ink says no compartment is there --
    `outer_paper_unguided`, which refuses unless the paper both holds two of
    this comb's own compartments and carries none of its guide ink. It is the
    same reading as the wall trim, taken where the sheet drew no wall, and
    both are reported the same way in `left_trim`/`right_trim`.

    A comb drawn entirely from walls -- a row of full-height boxes -- still
    keeps every one of them: there is no tick run to sit outside of, so the
    cell's own edges stay the rails, and neither trim can reach it. That is
    what separates 1604CF `p2c73` and 2551M `p2c13` (two ruled table columns,
    68.64 and 156.72pt wide, each closed by a bar crossing the owner's whole
    paper -- both reviewed and confirmed as 2 compartments) from the four
    caption cells above, where the outermost mark is a 0.24pt guide tick that
    closes nothing.

    Each rail's own INK is reported beside its position, measured from exactly
    the bars that established that position and from nothing else. Where no bar
    established it -- an unowned band, or an edge no bar rules across -- the ink
    is `None` and stays `None`: the fused lattice edge times its nominal
    thickness is not a measurement of this rail, and inventing one there is how
    a comb would be inset off a stroke the sheet does not draw at that band.
    """
    left, right = edge_thickness
    left_edge = (x0 - left / 2.0, x0 + left / 2.0, left)
    right_edge = (x1 - right / 2.0, x1 + right / 2.0, right)
    left_rail = x0
    right_rail = x1
    left_ink: Interval | None = None
    right_ink: Interval | None = None
    left_trim: dict[str, Any] = {
        "method": RAIL_AT_OWNER_EDGE, "refused": RAIL_REFUSED_NO_OWNER_PAPER}
    right_trim: dict[str, Any] = dict(left_trim)
    if paper is not None:
        # The rail has to rule the band's PAPER, not the rule the band ends
        # inside. 2550M's raw date-box ticks run 0.72pt down into the row's
        # bottom rule, and requiring the rail to follow them there would reject
        # the bar that plainly rules the box (the two readings of one band then
        # measure two different outer edges, and the erased-divider certificate
        # correctly refuses to bridge them).
        rail_y0 = max(band_y0, paper.y0)
        rail_y1 = min(band_y1, paper.y1)
        left_bars = edge_rail_bars(paper.left, left_edge, rail_y0, rail_y1)
        right_bars = edge_rail_bars(paper.right, right_edge, rail_y0, rail_y1)
        measured_left = edge_rail(paper.left, left_edge, rail_y0, rail_y1)
        measured_right = edge_rail(paper.right, right_edge, rail_y0, rail_y1)
        # The rail's ink is the drawn stack's, whether or not its centre won the
        # comparison below. Where the centre falls outside the rectangle the
        # rectangle keeps the POSITION -- half the bar is the neighbour's -- but
        # the bar is still the stroke this comb's outer compartment is drawn
        # against, and the paper it may be written on still starts after it.
        left_ink = ink_envelope(left_bars)
        right_ink = ink_envelope(right_bars)
        # A measured rail moves the comb's edge INWARD off the fused mean, and
        # only inward. The rectangle is the paper this comb is emitted on and
        # nothing may be typed off it, so where the drawn bar's own centre
        # falls outside the rectangle the rectangle wins: half the bar is
        # already the neighbour's and the neighbour's comb measures the same
        # rail from its own side, where it lies inside.
        if measured_left is not None and measured_left > x0:
            left_rail = measured_left
        if measured_right is not None and measured_right < x1:
            right_rail = measured_right
        kinds = [
            divides_owner_paper(
                q(centre(ink)), extra, paper.y0, paper.y1, final_paint)
            for ink in boundaries
        ]
        ticks = [q(centre(ink))
                 for ink, wall in zip(boundaries, kinds) if not wall]
        if not ticks:
            # Every boundary closes a box, so there is no tick run for a rail
            # to sit outside of and neither trim has a subject. 1604CF `p2c73`
            # and 2551M `p2c13` are the corpus's two: table columns 2.58 and
            # 2.21 times their neighbour's width, each bounded by a bar that
            # crosses the owner's whole paper, and each a real writing box.
            left_trim = {"method": RAIL_AT_OWNER_EDGE,
                         "refused": RAIL_REFUSED_NO_TICK_RUN}
            right_trim = dict(left_trim)
        else:
            walls_left = [q(centre(ink))
                          for ink, wall in zip(boundaries, kinds)
                          if wall and q(centre(ink)) < ticks[0]]
            walls_right = [q(centre(ink))
                           for ink, wall in zip(boundaries, kinds)
                           if wall and q(centre(ink)) > ticks[-1]]
            # A rail trimmed to a boundary -- an interior wall, or the
            # outermost tick where the sheet closed the caption off with
            # nothing -- is a DIFFERENT stroke from the owner's edge, so its
            # ink is that boundary's and never the edge's.
            if walls_left:
                left_trim = {
                    "method": RAIL_TRIMMED_TO_WALL,
                    "from_x": q(left_rail), "to_x": q(max(walls_left)),
                }
                left_rail = max(walls_left)
                left_ink = ink_envelope(boundary_stack(boundaries, left_rail))
            else:
                left_trim = outer_paper_unguided(
                    left_rail, ticks[0], ticks, extra,
                    band_y0, band_y1, final_paint)
                if left_trim["method"] == RAIL_TRIMMED_TO_UNGUIDED_OUTER_PAPER:
                    left_rail = ticks[0]
                    left_ink = ink_envelope(
                        boundary_stack(boundaries, left_rail))
            if walls_right:
                right_trim = {
                    "method": RAIL_TRIMMED_TO_WALL,
                    "from_x": q(right_rail), "to_x": q(min(walls_right)),
                }
                right_rail = min(walls_right)
                right_ink = ink_envelope(
                    boundary_stack(boundaries, right_rail))
            else:
                right_trim = outer_paper_unguided(
                    right_rail, ticks[-1], ticks, extra,
                    band_y0, band_y1, final_paint)
                if right_trim["method"] == RAIL_TRIMMED_TO_UNGUIDED_OUTER_PAPER:
                    right_rail = ticks[-1]
                    right_ink = ink_envelope(
                        boundary_stack(boundaries, right_rail))
    enclosed = [ink for ink in boundaries
                if left_rail < q(centre(ink)) < right_rail]
    return CombRails(q(left_rail), q(right_rail), enclosed, left_ink,
                     right_ink, left_trim, right_trim)


def comb_bands(members: Sequence[dict[str, Any]], extra: Sequence[dict[str, Any]],
               x0: float, x1: float,
               edge_thickness: tuple[float, float],
               final_paint: FinalPaint,
               paper: CombOwnerPaper | None = None) -> list[dict[str, Any]]:
    """Group a cell's comb dividers into bands, one band per field.

    Dividers of one comb share a y extent exactly (they are drawn by the same
    loop), so grouping on the band extent is safe and needs no pitch assumption.
    The band a comb divider discovers is then filled in from `extra`, the black
    ink that spans a common final-visible endpoint slab without being a comb
    divider -- see `endpoint_band`.
    Where a field's ticks are not all drawn to the same length the field arrives
    as two overlapping bands, and the one carrying the complete set of
    boundaries is the shorter one -- the only band every boundary spans. The
    reported band is therefore the writing box the *shortest* tick measures,
    which on 449 cells is 0.48pt less than before. That is the price of the slot
    count being right, and it keeps `y0`/`y1` honest: every boundary listed in
    `slot_x` really does run the full height reported here.

    A divider landing on this cell's own left or right edge is not a slot
    divider: it is the thinned middle fragment of the column border crossing
    the comb band (page 2, x = 320.69 and 575.98), which the comb split has to
    classify as a comb because it really does hang from nothing. The test is
    deliberately local -- x = 221.57 is a real slot divider in the Schedule 1
    money comb even though an unrelated panel elsewhere on page 2 puts a
    lattice line at that same x.
    """
    inside = [d for d in members if x0 + CLUSTER_TOL_PT < centre(d) < x1 - CLUSTER_TOL_PT]
    if not inside:
        return []

    by_band: dict[tuple[float, float], list[dict[str, Any]]] = collections.defaultdict(list)
    for d in inside:
        by_band[(d["y0"], d["y1"])].append(d)

    # The cell's own edges are the outermost boundaries, so an extra has to be a
    # separate boundary from those too -- see `band_ink`.
    left, right = edge_thickness
    frame = [(x0 - left / 2.0, x0 + left / 2.0, left),
             (x1 - right / 2.0, x1 + right / 2.0, right)]

    bands: list[dict[str, Any]] = []
    for (_band_y0, _band_y1), seed in sorted(by_band.items()):
        selected = endpoint_band(seed, extra, x0, x1, frame, final_paint)
        if selected is None:
            continue
        (band, band_y0, band_y1, topology_evidence,
         horizontal_rail_only) = selected
        band = sorted(band, key=lambda ink: q(centre(ink)))
        rails = comb_rails(
            band, extra, x0, x1, band_y0, band_y1, edge_thickness,
            paper, final_paint)
        left_rail, right_rail, band = rails.left_x, rails.right_x, rails.enclosed
        if not band:
            # Every measured boundary belongs to a neighbouring compartment of
            # the same rectangle. There is no comb here to publish.
            continue
        xs = sorted(q(centre(d)) for d in band)
        boundaries = [left_rail, *xs, right_rail]
        deltas = [q(b - a) for a, b in zip(boundaries, boundaries[1:])]
        # A single divider cannot prove two character compartments when one
        # side can hold at least two copies of the other. Likewise, a large
        # *interior* gap splits two anchor runs rather than joining them into
        # one comb. These are topology warnings, not grounds for retiring an
        # existing subject: the source referee may be unable to distinguish a
        # deliberately unequal field from unrelated geometry. The comparison
        # is exactly two measured paper widths and is carried as unresolved
        # evidence for a gate/referee to adjudicate independently.
        #
        # This rejects page-header inner frames and side-by-side fields that a
        # broad cell had merged (address + ZIP is the recurring case), while an
        # edge label may still precede a run of at least three measured slots.
        smallest = min(deltas)
        incoherent_pair = len(deltas) == 2 and max(deltas) >= 2 * smallest
        split_anchor_runs = any(delta >= 2 * smallest for delta in deltas[1:-1])
        reason_codes: list[str] = []
        if incoherent_pair:
            reason_codes.append("unequal-two-slot-topology")
        if split_anchor_runs:
            reason_codes.append("split-anchor-run-topology")
        # A topology whose entire proof lies inside a full-width horizontal
        # rail can neither win selection nor certify a comb (`endpoint_band`),
        # so it cannot de-certify one either: inside the rail there is no
        # paper on which its extra boundary could print.  2550M's knocked-out
        # date-box tick survives only inside the row's bottom rule and would
        # otherwise keep a printed two-compartment box unresolved forever.
        # The rail-only topology stays published in the evidence below.  A
        # band with no paper-bearing topology at all keeps the conservative
        # comparison and is already unresolved via
        # `horizontal-rail-only-topology`.
        paper_topologies = [
            evidence for evidence in topology_evidence
            if float(evidence["paper_coverage_pt"]) > 0.0
        ]
        if len(paper_topologies or topology_evidence) > 1:
            reason_codes.append("competing-endpoint-topologies")
        if horizontal_rail_only:
            reason_codes.append("horizontal-rail-only-topology")
        if any(len(evidence["runs"]) > 1
               and not evidence["corridors_continuous"]
               for evidence in topology_evidence):
            reason_codes.append("disconnected-final-visible-corridor")
        ordered_band = sorted(band, key=lambda ink: (
            q(centre(ink)), -paint_ordinal(ink),
            -float(ink["thickness_pt"])))
        thicknesses = collections.Counter(d["thickness_pt"] for d in ordered_band)
        grays = sorted({d["gray"] for d in ordered_band if d["gray"] is not None})
        # DECISION A (2026-08-16): a band none of whose compartments form a
        # run of character boxes is not a comb at all -- the whole band is
        # refused here, exactly as a band with no candidate ink already is.
        # A band that still holds at least one run keeps its full measured
        # geometry (reshaping is the subject layer's act, not this one's)
        # and carries the runs as published evidence.
        rule_runs = compartment_runs(boundaries)
        if not rule_runs:
            continue
        bands.append({
            "compartment_runs": [list(run) for run in rule_runs],
            "cells": len(xs) + 1,
            "divider_count": len(xs),
            # Modal slot width. Ties break on the smaller value for determinism.
            "pitch_pt": min(collections.Counter(deltas).most_common(),
                            key=lambda kv: (-kv[1], kv[0]))[0],
            "pitch_min_pt": min(deltas),
            "pitch_max_pt": max(deltas),
            # Measured boundaries. Never synthesise slot x from index * pitch:
            # the real lattice carries 14.04-14.28 where 14.16 is nominal.
            "slot_x": boundaries,
            # Where each outer rail's own ink is painted, or None where no bar
            # of it could be measured over this band. `comb_on_writing_surface`
            # turns these into the horizontal writing surface; nothing else may
            # invent one from `slot_x`, which runs down the rails' centres.
            "left_rail_ink": (
                list(rails.left_ink) if rails.left_ink is not None else None),
            "right_rail_ink": (
                list(rails.right_ink) if rails.right_ink is not None else None),
            # WHY each rail is where it is: `owner-edge` where the cell's own
            # edge decided it, and otherwise the measurement `comb_rails` moved
            # it inward on. A rail that has left the cell box is then readable
            # as such, with its evidence, instead of having to be inferred from
            # the coordinate by whoever reads `slot_x` next.
            "outer_rail_trim": {
                "left": rails.left_trim, "right": rails.right_trim,
            },
            "divider_x": xs,
            "divider_thickness_pt": min(thicknesses.most_common(),
                                        key=lambda kv: (-kv[1], kv[0]))[0],
            # Thickness inside a comb encodes RANK, not membership: 0.24 is a
            # character divider, 0.96/1.44 a digit-group (thousands) separator.
            "divider_thicknesses_pt": sorted(thicknesses),
            "divider_gray": grays[0] if grays else None,
            "divider_paint_seq": [paint_ordinal(d) for d in ordered_band],
            "divider_paint_ranges": [
                list(paint_ordinal_range(d)) for d in ordered_band
            ],
            "y0": q(band_y0), "y1": q(band_y1),
            "height_pt": q(band_y1 - band_y0),
            "resolution": {
                "status": "unresolved" if reason_codes else "resolved",
                "method": "final-visible-endpoint-slab",
                "reason_codes": reason_codes,
            },
        })
        if (len(topology_evidence) > 1
                or "disconnected-final-visible-corridor" in reason_codes):
            bands[-1]["resolution"]["endpoint_topologies"] = topology_evidence
    # Different seed endpoint groups can converge on the same common slab (the
    # 2551Q TIN has long group bars and shorter character ticks). It is one
    # physical band, so retain it once.
    coalesced: list[dict[str, Any]] = []
    for band in sorted(
            bands,
            key=lambda value: (
                tuple(value["divider_x"]), value["y0"], value["y1"])):
        existing = next((
            value for value in coalesced
            if value["divider_x"] == band["divider_x"]
            and abs(float(value["y0"]) - float(band["y0"]))
            <= JOIN_EPSILON_PT
            and abs(float(value["y1"]) - float(band["y1"]))
            <= JOIN_EPSILON_PT
        ), None)
        if existing is None:
            coalesced.append(band)
            continue
        # Both endpoint groups prove the same divider topology. Their common
        # intersection is the band every representative actually spans.
        existing["y0"] = q(max(float(existing["y0"]), float(band["y0"])))
        existing["y1"] = q(min(float(existing["y1"]), float(band["y1"])))
        existing["height_pt"] = q(existing["y1"] - existing["y0"])
        resolution = existing["resolution"]
        other_resolution = band["resolution"]
        reasons = sorted({
            *(resolution.get("reason_codes") or ()),
            *(other_resolution.get("reason_codes") or ()),
        })
        if reasons:
            resolution["status"] = "unresolved"
            resolution["reason_codes"] = reasons
        evidence = [
            *(resolution.get("endpoint_topologies") or ()),
            *(other_resolution.get("endpoint_topologies") or ()),
        ]
        if evidence:
            unique_evidence = {
                (
                    tuple(item["divider_x"]),
                    tuple(tuple(run) for run in item["runs"]),
                ): item
                for item in evidence
            }
            resolution["endpoint_topologies"] = [
                unique_evidence[key] for key in sorted(unique_evidence)
            ]
    bands = coalesced
    bands.sort(key=lambda b: (b["y0"], -b["divider_count"]))
    return bands


def legacy_comb_bands(members: Sequence[dict[str, Any]],
                      extra: Sequence[dict[str, Any]],
                      x0: float, x1: float,
                      edge_thickness: tuple[float, float],
                      paper: CombOwnerPaper | None = None,
                      ) -> list[dict[str, Any]]:
    """Reconstruct the pre-partition subject ledger without promoting it.

    This is deliberately the old full-span detector. It is not a second answer
    to final-paint topology; it is the continuity denominator that prevents a
    row-run partition or a new compositor from silently deleting a published
    subject. A final-visible contract replaces it when that contract has at
    least as many measured boundaries. A reduction remains active-unresolved
    unless exact source-order evidence proves that every omitted legacy
    divider was fully erased; a nonrectangular owner remains retained and
    unresolved.

    It measures its comb's outer rails the same way `comb_bands` does, and for
    the same reason: where a comb's edge is printed is a fact about the sheet,
    not about which detector is reading it. Continuity is preserved by this,
    not weakened -- a denominator that kept counting a caption as a compartment
    would report every corrected comb as a REDUCTION and preserve the wrong
    contract in the name of continuity. What it still refuses to do is consult
    final paint, which is why it passes none: this is the raw reading.
    """
    inside = [d for d in members
              if x0 + CLUSTER_TOL_PT < centre(d) < x1 - CLUSTER_TOL_PT]
    if not inside:
        return []

    by_band: dict[tuple[float, float], list[dict[str, Any]]] = (
        collections.defaultdict(list))
    for divider in inside:
        by_band[(divider["y0"], divider["y1"])].append(divider)

    left, right = edge_thickness
    frame = [(x0 - left / 2.0, x0 + left / 2.0, left),
             (x1 - right / 2.0, x1 + right / 2.0, right)]
    bands: list[dict[str, Any]] = []
    for (band_y0, band_y1), seed in sorted(by_band.items()):
        ink = [
            *seed,
            *band_ink(extra, x0, x1, band_y0, band_y1, [
                *frame,
                *((d["x0"], d["x1"], d["thickness_pt"]) for d in seed),
            ]),
        ]
        ordered = sorted(ink, key=lambda divider: (
            q(centre(divider)), -paint_ordinal(divider),
            -float(divider["thickness_pt"])))
        rails = comb_rails(
            ordered, extra, x0, x1, band_y0, band_y1, edge_thickness,
            paper, None)
        left_rail, right_rail, ordered = (
            rails.left_x, rails.right_x, rails.enclosed)
        if not ordered:
            continue
        xs = [q(centre(divider)) for divider in ordered]
        boundaries = [left_rail, *xs, right_rail]
        deltas = [q(b - a) for a, b in zip(boundaries, boundaries[1:])]
        thicknesses = collections.Counter(d["thickness_pt"] for d in ordered)
        grays = sorted({d["gray"] for d in ordered if d["gray"] is not None})
        # DECISION A deliberately does NOT apply here. This is the legacy
        # pass, and it derives the reviewed subject denominator -- frozen
        # history. Census (2026-08-16): 28 of the corpus's 30
        # retained/composite subjects have legacy combs the compartment
        # rule refuses, because the retained/composite population IS the
        # legacy detector's false-positive population, and the reviewed
        # transition path is how each was already adjudicated. Refusing
        # them here would erase those 28 reviewed decisions retroactively
        # and stale every transition certificate (the fail-closed guard in
        # apply_reviewed_transitions fires on 0605 p1c54 within seconds of
        # trying). A false legacy comb is retired by review, never by a
        # detector edit.
        bands.append({
            "cells": len(xs) + 1,
            "divider_count": len(xs),
            "pitch_pt": min(collections.Counter(deltas).most_common(),
                            key=lambda item: (-item[1], item[0]))[0],
            "pitch_min_pt": min(deltas),
            "pitch_max_pt": max(deltas),
            "slot_x": boundaries,
            "left_rail_ink": (
                list(rails.left_ink) if rails.left_ink is not None else None),
            "right_rail_ink": (
                list(rails.right_ink) if rails.right_ink is not None else None),
            # Published here as well, and not only in `comb_bands`, because a
            # preserved legacy contract can BECOME the cell's published comb
            # (`final-visible-count-regression`). A published comb whose rails
            # carry no reason would be exactly the silent cell-box fallback
            # this key exists to make visible.
            "outer_rail_trim": {
                "left": rails.left_trim, "right": rails.right_trim,
            },
            "divider_x": xs,
            "divider_thickness_pt": min(
                thicknesses.most_common(),
                key=lambda item: (-item[1], item[0]))[0],
            "divider_thicknesses_pt": sorted(thicknesses),
            "divider_gray": grays[0] if grays else None,
            "divider_paint_seq": [paint_ordinal(d) for d in ordered],
            "divider_paint_ranges": [
                list(paint_ordinal_range(d)) for d in ordered
            ],
            # Private transition evidence. ``build_cells`` removes this before
            # publishing the legacy contract and retains the exact raw paints
            # only long enough to prove or reject a lower final-visible count.
            "_divider_witnesses": ordered,
            "y0": q(band_y0), "y1": q(band_y1),
            "height_pt": q(band_y1 - band_y0),
            "resolution": {
                "status": "unresolved",
                "method": "legacy-continuity",
                "reason_codes": ["legacy-continuity-only"],
            },
        })
    bands.sort(key=lambda band: (band["y0"], -band["divider_count"]))
    return bands


def encloses_paper(lattice: Lattice, first: int, last: int) -> bool:
    """True when unpainted paper survives between two lattice lines.

    Fusion catches the boundaries that are drawn as a stack of bars, but a
    frame rule may also *jog*: 1606 draws its left page frame as one chain of
    segments whose x centre steps from 26.64 to 27.00 half way down the sheet,
    and 1701 CONSO steps its right frame twice. Those fragments are too far
    apart to cluster and never coincide along the page, so both centres stand
    as lattice lines with the ink of one bar spanning both. A cell between them
    contains no paper -- nothing can be printed there and nobody can write
    there -- so it is a walk artefact, not a container.
    """
    return lattice.ink_lo[last] > lattice.ink_hi[first]


def min_fillable_line_metrics(ir: dict[str, Any]) -> dict[str, float] | None:
    """Measured metrics of the smallest body line this form prints.

    A ruled gap between a caption line and the next rule classifies as an
    empty bordered cell exactly like a real writing strip does, so 120 such
    slivers across 42 forms carried inputs nothing can be typed in (the
    2026-08 triage's population B2).  The minimum surface a text strip must
    offer is derived from the form's own typography -- measured quantities,
    never tuned constants:

    * ``glyph_height_pt`` -- the smallest body run's size scaled by its
      font's descriptor cap height (the source-declared ink height of an
      uppercase glyph), falling back to the run's measured ascender when the
      embedded descriptor carries no cap height.  Paper shorter than this
      cannot show one glyph of the smallest print on the sheet.
    * ``line_width_pt`` -- two em squares of the smallest body size.  A body
      line is at least two glyphs (the same bound that qualifies a run
      below); paper narrower than that is a mark box -- 2551Q's 6.9pt
      "Yes"/"No" checkboxes -- whose fillability is not a text question and
      which the height minimum must leave alone.

    A run qualifies as body text only with two or more non-whitespace
    glyphs: the corpus hides sub-point stray glyphs ("p ", "K ", ". " at
    0.96pt on 1606/1801/2552) and lone decoration marks (the 4pt money
    bullet) whose sizes are not the form's typography.
    """
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
    for page in ir["pages"]:
        for run in page["text_runs"]:
            if sum(1 for ch in run["text"] if not ch.isspace()) < 2:
                continue
            size = float(run["size_pt"])
            ratio = cap_ratio_by_font.get(
                str(run["font"]), float(run["ascender"]))
            height = size * ratio
            if glyph_height is None or height < glyph_height:
                glyph_height = height
            if line_width is None or 2.0 * size < line_width:
                line_width = 2.0 * size
    if glyph_height is None or line_width is None:
        return None
    return {"glyph_height_pt": glyph_height, "line_width_pt": line_width}


def cell_paper_gap(cell: dict[str, Any], xl: Lattice, yl: Lattice,
                   ) -> tuple[float, float]:
    """This cell's ink-to-ink paper extent, (width_pt, height_pt).

    The lattice's per-line ``ink_lo``/``ink_hi`` are page-wide cluster
    extents: a boundary fused from stacked bars elsewhere along the line
    must not shrink this cell's paper.  Only rules actually overlapping the
    cell's span bound its paper; a boundary with no overlapping member
    contributes its line position.
    """
    x0, y0 = float(cell["x0"]), float(cell["y0"])
    x1, y1 = float(cell["x1"]), float(cell["y1"])
    j0 = int(cell["row"])
    j1 = j0 + int(cell["row_span"])
    i0 = int(cell["col"])
    i1 = i0 + int(cell["col_span"])

    def edge(lattice: Lattice, index: int, lo: float, hi: float,
             span_keys: tuple[str, str], edge_keys: tuple[str, str],
             far: bool) -> float:
        start_key, end_key = span_keys
        members = [
            rule for rule in lattice.members[index]
            if float(rule[end_key]) > lo + JOIN_EPSILON_PT
            and float(rule[start_key]) < hi - JOIN_EPSILON_PT
        ]
        if not members:
            return lattice.positions[index]
        near_key, far_key = edge_keys
        if far:
            return max(float(rule[far_key]) for rule in members)
        return min(float(rule[near_key]) for rule in members)

    top = edge(yl, j0, x0, x1, ("x0", "x1"), ("y0", "y1"), True)
    bottom = edge(yl, j1, x0, x1, ("x0", "x1"), ("y0", "y1"), False)
    left = edge(xl, i0, y0, y1, ("y0", "y1"), ("x0", "x1"), True)
    right = edge(xl, i1, y0, y1, ("y0", "y1"), ("x0", "x1"), False)
    return right - left, bottom - top


# BIR's "this is not a writing surface" shading is `gray <= 0.8509`; that one
# population alone is 17,740 of the corpus's 26,027 decorative fills.  The
# whole decorative histogram is 0.502, 0.5882, 0.651, 0.7489, 0.7529, 0.8509 --
# and then, above 0.87 and with nothing in between, exactly 7 near-white fills.
# 5 of the cells those 7 cover are REAL fields, proved by their own labels:
# 1604cf-2008 p1c8/c10/c12 (0.8902, beside item "7" and "(Last Name, First
# Name, Middle Name)") and 2200an-2018 p2c247/c255 (0.9489, one labelled "(To
# Schedule 1C)").  `role == "decorative"` alone would take those 5 away from
# the taxpayer; 0.87 sits in the empty gap, spares them, and still moves 347
# cells across 40 forms out of `field`.  Coverage is asked of the cell, not of
# the fill: a tint band runs the width of a table and only the part under this
# cell is paper this cell could have offered.
SHADED_PAPER_MAX_GRAY = 0.87
SHADED_PAPER_MIN_COVERAGE = 0.70

# Published on the retained subject a refuted caption block leaves behind, and
# named for what was measured rather than for what was done about it. Declared
# here because it crosses a file boundary: `audit.validate_comb_owner_registry`
# admits a retained subject only on a reason-code tuple it knows.
REFUTED_CAPTION_BLOCK_REASON_CODE = (
    "emission-suppressed-caption-block-not-character-cells")


def _bridge_shading_seams(tone: list[list[float | None]],
                          bare: list[list[bool]],
                          xs: Sequence[float], ys: Sequence[float],
                          min_writable_pt: float) -> None:
    """Close bare-paper seams narrower than one character between equal tones.

    In place, on both axes, and only where the SAME tone stands on both sides:
    a seam between 0.8509 above and 0.7489 below is two bands meeting, not one
    band interrupted, and unioning them would invent a tone the source never
    painted. An atom any fill covers is never crossed, so a knockout separates
    however thin it is.
    """
    for axis in (0, 1):
        rows, columns = len(tone), len(tone[0]) if tone else 0
        outer = columns if axis == 0 else rows
        inner = rows if axis == 0 else columns
        edges = ys if axis == 0 else xs
        for fixed in range(outer):
            run = 0
            for moving in range(inner + 1):
                if moving < inner:
                    j, i = (moving, fixed) if axis == 0 else (fixed, moving)
                    if tone[j][i] is None and bare[j][i]:
                        run += 1
                        continue
                if run:
                    start = moving - run
                    span = edges[moving] - edges[start]
                    before = start - 1
                    if span < min_writable_pt and before >= 0 and moving < inner:
                        bj, bi = ((before, fixed) if axis == 0
                                  else (fixed, before))
                        aj, ai = (moving, fixed) if axis == 0 else (fixed, moving)
                        band = tone[bj][bi]
                        if band is not None and tone[aj][ai] == band:
                            for step in range(start, moving):
                                sj, si = ((step, fixed) if axis == 0
                                          else (fixed, step))
                                tone[sj][si] = band
                    run = 0


def covering_shading_band(cell: dict[str, Any],
                          area_fills: Sequence[dict[str, Any]],
                          needed: float,
                          min_writable_pt: float = 0.0) -> float:
    """Paper this cell loses to ONE connected band of a single shading tone.

    Asking whether one fill covers the cell is a false negative for any cell
    taller than one source shading strip, and the source draws shading in
    strips: 2550M p3c9 is grey on paper, and grey in the raster, because two
    7.8pt 0.7529 strips are painted back to back across a 15.6pt cell.  Neither
    reaches the coverage rule alone (0.508 and 0.500), so the cell kept a field
    and an input over shading that says NO ENTRY HERE.  What is fixed here is
    how coverage is *computed*; what counts as shaded -- `role == "decorative"`
    at or below `SHADED_PAPER_MAX_GRAY` -- is unchanged.

    Three properties the single-fill test had, kept exactly:

    * **Topmost, not any**, and topmost among ALL fills, not among shading.
      A tint painted early and anything opaque painted over it leave paper that
      is not that tint: a white knockout (2551Q), a *chromatic* fill with no
      gray at all (2553 p1c16/c18/c20, where a coloured box sits on the page's
      0.7529 band), or a second decorative tint above the cut (1604cf-2008
      p1c8/c10/c12, whose 0.8902 boxes are the REAL fields the cut exists to
      spare, painted at seq 283-287 over a seq-1 band).  Every point of the cell
      is resolved to the last fill painted there and counts only if that fill is
      the shading itself, which is `topmost_covering_fill` asked per point
      instead of per cell.
    * **Deterministic.**  Ties -- two fills at one ordinal -- break on source
      order, which `extract.extract_area_fills` fixes by ``(y0, x0)``.
    * **Coverage is asked of the cell**, not of the fill.

    Two properties that are new, and both are what makes a union safe:

    * **One tone.**  Strips of one band share the operand that drew them.
      Unioning 0.502 against 0.8509 would be inventing a band the source never
      painted, so tones accumulate separately and the largest wins.
    * **One connected region.**  Strips must touch.  Two tinted strips with
      white paper -- or a knockout -- between them are two bands with a writing
      surface in the middle, and their areas must not be added into a verdict
      about the middle.  Resolving each point against everything painted over
      it *before* the connectivity test is what stops a union reaching across a
      whited-out centre.

      "Touch" means the gap between them could not be written in, not that it
      is zero.  The source butts its strips against each other and leaves a
      seam: 0619F p1c8 is 82% grey by area, in two strips 0.48pt apart, and
      scored 0.495 -- exactly the upper strip alone -- so the cell kept a field
      and an input over paper that says NO ENTRY HERE.  A gap is a writing
      surface only if a character fits in it, so `min_writable_pt` is the
      form's OWN `min_fillable_line_metrics(...)["glyph_height_pt"]`, the same
      measured quantity the sliver rule spends, and not a constant.  Measured
      over every same-tone gap inside a field cell that carries an input: 360
      vertical gaps, none wider than 1.51pt, against a glyph height that is
      2.930pt on its smallest form -- a 1.94x separation with nothing between.
      Horizontally the corpus separates harder still: 65 of 67 gaps are 0.5pt
      or less and the other two are 32.88pt, so no bound in that window can
      change a verdict.

      A gap bridges ONLY across bare paper.  An atom that any fill covers --
      a knockout, a chromatic box, a tint above the cut -- keeps the strips
      apart however narrow it is, which is the whited-out-centre property
      above, unweakened: the bridge asks for the ABSENCE of paint, and a
      knockout is paint.

    Returns the largest such band's area in pt², or 0.0 when no band can reach
    `needed` -- the short-circuit is exact, since the clipped areas summed
    without regard to overlap bound every union from above.
    """
    x0, y0 = float(cell["x0"]), float(cell["y0"])
    x1, y1 = float(cell["x1"]), float(cell["y1"])
    if x1 <= x0 or y1 <= y0:
        return 0.0

    # (x0, y0, x1, y1, paint key, tone) per fill overlapping this cell's paper.
    # Tone is None for every fill that is not shading -- those are carried
    # because they can COVER shading, not because they can be it.
    layers: list[tuple[float, float, float, float,
                       tuple[int, int], float | None]] = []
    bound = 0.0
    floor: tuple[int, int] | None = None
    for index, fill in enumerate(area_fills):
        cx0 = max(float(fill["x0"]), x0)
        cx1 = min(float(fill["x1"]), x1)
        if cx1 <= cx0:
            continue
        cy0 = max(float(fill["y0"]), y0)
        cy1 = min(float(fill["y1"]), y1)
        if cy1 <= cy0:
            continue
        gray = fill.get("gray")
        tone_value: float | None = None
        if (fill["role"] == "decorative" and gray is not None
                and float(gray) <= SHADED_PAPER_MAX_GRAY):
            tone_value = float(gray)
        key = (paint_ordinal(fill), index)
        if tone_value is not None:
            bound += (cx1 - cx0) * (cy1 - cy0)
            if floor is None or key < floor:
                floor = key
        layers.append((cx0, cy0, cx1, cy1, key, tone_value))
    # `bound` sums the clipped shading areas and so bounds an unbridged union
    # from above, but a bridged one may claim bare paper as well, so the
    # short-circuit is exact only while nothing bridges.  No shading at all is
    # still an immediate no.
    if floor is None or (min_writable_pt <= 0.0 and bound < needed):
        return 0.0

    # A fill painted before every shading strip here is under all of them.
    # Where shading covers it, the shading wins anyway; where it does not, the
    # point is unshaded either way.  Dropping it is exact, and it keeps the
    # atom grid at the single strip the old test measured whenever one strip is
    # involved, so a cell already shaded by one fill keeps that fill's area.
    layers = [layer for layer in layers
              if layer[5] is not None or layer[4] > floor]

    xs = sorted({x0, x1} | {edge for r in layers for edge in (r[0], r[2])})
    ys = sorted({y0, y1} | {edge for r in layers for edge in (r[1], r[3])})
    columns, rows = len(xs) - 1, len(ys) - 1

    # Every atom lies wholly inside or wholly outside each clipped rectangle,
    # because the grid is cut on those rectangles' own edges; its midpoint
    # therefore decides containment for the whole atom.
    tone: list[list[float | None]] = []
    # Bare paper: no fill of any kind resolves over this atom.  Distinct from
    # `tone is None`, which also covers an atom a knockout or an above-the-cut
    # tint owns.  Only bare paper may be bridged.
    bare: list[list[bool]] = []
    for j in range(rows):
        my = (ys[j] + ys[j + 1]) / 2.0
        row: list[float | None] = []
        bare_row: list[bool] = []
        for i in range(columns):
            mx = (xs[i] + xs[i + 1]) / 2.0
            top: tuple[int, int] | None = None
            gray = None
            for cx0, cy0, cx1, cy1, key, value in layers:
                if cx0 < mx < cx1 and cy0 < my < cy1:
                    if top is None or key > top:
                        top, gray = key, value
            row.append(gray)
            bare_row.append(top is None)
        tone.append(row)
        bare.append(bare_row)

    if min_writable_pt > 0.0:
        _bridge_shading_seams(tone, bare, xs, ys, min_writable_pt)

    best = 0.0
    seen = [[False] * columns for _ in range(rows)]
    for j in range(rows):
        for i in range(columns):
            if seen[j][i] or tone[j][i] is None:
                continue
            band = tone[j][i]
            seen[j][i] = True
            stack = [(j, i)]
            area = 0.0
            while stack:
                cj, ci = stack.pop()
                area += (xs[ci + 1] - xs[ci]) * (ys[cj + 1] - ys[cj])
                for nj, ni in ((cj - 1, ci), (cj + 1, ci),
                               (cj, ci - 1), (cj, ci + 1)):
                    if (0 <= nj < rows and 0 <= ni < columns
                            and not seen[nj][ni] and tone[nj][ni] == band):
                        seen[nj][ni] = True
                        stack.append((nj, ni))
            if area > best:
                best = area
    return best


def on_shaded_paper(cell: dict[str, Any],
                    area_fills: Sequence[dict[str, Any]],
                    min_writable_pt: float = 0.0) -> bool:
    """Whether the source shaded this cell to say "do not write here".

    `extract.classify_tone` already decides structural / decorative / knockout
    from the literal content-stream operand, and the IR carries that decision
    on every fill.  The box model then threw it away: `classify_cell` was asked
    only whether a region was empty and bordered, so an empty three-bordered
    region on a grey band satisfied "field" exactly as a white one did, and
    `page["area_fills"]` reached this module only through `wall_boundaries` and
    `comb_boundary_candidates`, both of which read `role == "structural"` for
    grid geometry.  The decorative population -- the tint that means NO RATE
    APPLIES -- was never consulted by anything that decides where a taxpayer
    may type.  This is that consultation; the fact was computed correctly at
    extraction and is only being restored here.

    `emit.field_verdict`'s "a blank the source printed over is not a blank"
    protects statutory TEXT.  Shading is the same statement made with tone
    instead of glyphs, and no ink-coverage test can see it.
    """
    x0, y0 = float(cell["x0"]), float(cell["y0"])
    x1, y1 = float(cell["x1"]), float(cell["y1"])
    area = (x1 - x0) * (y1 - y0)
    if area <= 0.0:
        return False
    needed = area * SHADED_PAPER_MIN_COVERAGE
    return covering_shading_band(
        cell, area_fills, needed, min_writable_pt) >= needed


def printed_glyph_boxes(run: dict[str, Any]) -> list[Interval]:
    """One x box per non-blank character of the run: the glyph's own extent.

    `char_widths_pt` is the character's own bounding box, not its advance, so
    this is where the ink is rather than where the next glyph starts.
    `glyph_ink_spans` answers a different question -- "where does this whole run
    mark the paper" -- and unions its intervals, which is the wrong shape for a
    caller that COUNTS glyphs: a union of 176 characters is one interval.

    Where the per-character arrays are missing the whole run collapses to a
    single box, which is the conservative direction for every caller here --
    they refuse on MANY glyphs, so under-counting can only leave a comb alone.
    """
    origin = float(run.get("origin_x", run["x0"]))
    offsets = run.get("char_origin_offsets_pt") or ()
    widths = run.get("char_widths_pt") or ()
    text = run.get("text") or ""
    if len(offsets) != len(text) or len(widths) != len(text):
        return [(float(run["x0"]), float(run["x1"]))] if text.strip() else []
    return [(origin + float(o), origin + float(o) + float(w))
            for ch, o, w in zip(text, offsets, widths) if ch.strip()]


def comb_compartment_glyph_counts(
        comb: dict[str, Any],
        runs: Sequence[dict[str, Any]]) -> list[int]:
    """How many printed glyphs the cell's own text puts in each compartment.

    Each glyph is counted once, for the compartment its own box is centred in,
    so the counts partition the cell's printed characters rather than
    double-counting the one glyph that straddles a divider.  Measured over the
    53-form corpus this choice is inert: midpoint, any-overlap and
    overlap-beyond-0.01pt give character-for-character the same counts on every
    one of the 4,561 comb cells.
    """
    slot_x = [float(value) for value in comb["slot_x"]]
    counts = [0] * max(len(slot_x) - 1, 0)
    for run in runs:
        for x0, x1 in printed_glyph_boxes(run):
            midpoint = (x0 + x1) / 2.0
            for index in range(len(counts)):
                if slot_x[index] <= midpoint < slot_x[index + 1]:
                    counts[index] += 1
                    break
    return counts


def printed_caption_refutes_comb(comb: dict[str, Any],
                                 runs: Sequence[dict[str, Any]]) -> bool:
    """Whether the cell's own printed text says these are not character cells.

    A comb compartment IS a character cell: one box, one typed character. That
    is what makes `classify_cell`'s "mixed" verdict sound -- the ink the source
    puts inside a comb is per-character decoration, "the % glyph, the money
    decimal point, the TIN group dashes", and a taxpayer still types in the
    boxes around it.  The verdict was reached from `has_comb` alone, so it was
    also given to every cell where a single stray vertical inside a printed
    caption block was read as a 2-compartment comb, and there the whole caption
    became a typing surface.  Shipping at the time of writing: a 566 x 106pt
    input pair over 1606 page 2's entire statutory rate table (Exempt / 1.5% /
    3.0% / 5.0% / 6.0%), and one over each of four excise mastheads.

    So ask the source what it printed IN the compartments.  Decoration in a
    character cell is at most one glyph per cell, because the cell is one
    character wide; running prose in every compartment is a caption, and the
    vertical that made those compartments is a column border the segmentation
    should have cut on, not a character tick.

    The corpus separates the two populations with nothing in between.  Over all
    4,561 comb cells (53 forms, `build/ir` + regenerated layouts, 2026-08-08),
    the minimum per-compartment glyph count of the cell's OWN assigned text is:

        0 glyphs   4,524 cells   (nothing printed in at least one compartment)
        1 glyph       26 cells   `I I 0 1 1`, `X C 0 1 0`, `W I 1 6 5`,
                                 `0 0 0 0 0`, `0 %` -- exactly the decoration
                                 the "mixed" comment names
        29+           11 cells   the whole selected population

    There is no cell between 1 and 29, so the test is "more than one" and
    carries no tuned constant.  It is deliberately EVERY compartment and never
    SOME: 2200A `p1c111` (and its 2200C/2200P twins) is a real 29-compartment
    money comb whose first compartment has swallowed the caption "27 Tax Debit
    Memo"; its other 28 compartments are empty, so the minimum is 0 and the
    comb keeps its money boxes.  That cell's first compartment is a separate,
    unfixed segmentation defect and is reported as one rather than folded in
    here.

    The 6 real combs that a WRITING-SURFACE overlap test would catch -- 2316
    `p1c37`/`p1c38` and 2550M `p1c83`/`p1c84`/`p1c89`/`p1c90`, whose 11.89 to
    13.44pt compartments merely sit under a neighbouring caption's descender
    pad -- are not at risk here and never were: the runs are assigned to the
    caption's own cell, so all six are `is_empty` and `classify_cell` calls
    them `field`.  Asking the cell's assigned text, which is the same evidence
    `is_empty` is computed from, is what keeps them out of the population.
    """
    counts = comb_compartment_glyph_counts(comb, runs)
    return bool(counts) and min(counts) > 1


def classify_cell(is_empty: bool, border_count: int, has_comb: bool,
                  is_sliver_text_strip: bool = False,
                  is_shaded_paper: bool = False) -> str:
    if is_empty and border_count >= 3:
        # An empty bordered strip whose paper cannot hold one glyph of the
        # smallest body line the form prints is a ruled gap -- the leading
        # below a caption -- not a writing surface.  Verified against the
        # 2026-08 triage census: the 120 sliver offenders and no wide real
        # field move.  Mark boxes (narrower than the smallest two-glyph
        # line) and comb owners are exempt by construction.
        if is_sliver_text_strip:
            return "blank"
        # Shaded paper is a separate kind, not "blank": "blank" asserts there
        # is no ink, and there IS ink -- the tint the source painted to say
        # this row takes no entry.  Naming it keeps the reason inspectable
        # downstream (`emit.field_verdict` publishes the kind as its refusal
        # reason) instead of hiding 347 cells inside an unrelated bucket.
        return "shaded" if is_shaded_paper else "field"
    if is_empty:
        return "blank"
    # Pre-printed text sitting in a comb -- the "%" glyph, the money decimal
    # point, the TIN group dashes -- is decoration on a fillable field.  That
    # holds because a compartment is one character wide, and it is checked
    # rather than assumed: `printed_caption_refutes_comb` has already taken the
    # comb off any cell whose every compartment carries running prose, so
    # `has_comb` here means a comb the source's own printing agrees with.
    return "mixed" if has_comb else "label"


def rectangular_row_runs(squares: Sequence[tuple[int, int]],
                         v_at: Sequence[Sequence[bool]],
                         h_at: Sequence[Sequence[bool]]
                         ) -> list[dict[str, int | bool]]:
    """Partition one connected grid component into painted-edge rectangles.

    A DSU component need not be rectangular. A missing edge can connect two
    broad regions through one narrow opening, producing an L, T, or frame-shaped
    component. Emitting its bounding box claims all the squares in the holes and
    overlaps real neighbouring cells; the largest examples cover most of a
    page and steal unrelated comb anchors by midpoint.

    The partition is deterministic and respects every visible internal edge:

    1. split each row into maximal horizontal runs, stopping at a vertical rule
       even if the squares reconnect elsewhere in the component;
    2. stack equal runs through consecutive rows only when their entire shared
       horizontal seam is open.

    Every returned rectangle is therefore made only of the component's squares
    and crosses no painted internal boundary. It is a partition, not a new
    geometry inference.
    """
    by_row: dict[int, list[int]] = collections.defaultdict(list)
    for j, i in squares:
        by_row[j].append(i)

    runs_by_row: dict[int, list[tuple[int, int]]] = {}
    for j, columns in by_row.items():
        ordered = sorted(set(columns))
        if not ordered:
            continue
        runs: list[tuple[int, int]] = []
        start = previous = ordered[0]
        for i in ordered[1:]:
            separated = i != previous + 1 or v_at[i][j]
            if separated:
                runs.append((start, previous + 1))
                start = i
            previous = i
        runs.append((start, previous + 1))
        runs_by_row[j] = runs

    rectangles: list[dict[str, int | bool]] = []
    active: dict[tuple[int, int], dict[str, int | bool]] = {}
    previous_row: int | None = None
    for j in sorted(runs_by_row):
        if previous_row is None or j != previous_row + 1:
            rectangles.extend(active.values())
            active = {}

        next_active: dict[tuple[int, int], dict[str, int | bool]] = {}
        for i0, i1 in runs_by_row[j]:
            span = (i0, i1)
            prior = active.get(span)
            seam_open = (
                prior is not None
                and int(prior["j1"]) == j
                and all(not h_at[j][i] for i in range(i0, i1))
            )
            if seam_open:
                prior["j1"] = j + 1
                next_active[span] = prior
            else:
                next_active[span] = {
                    "j0": j, "j1": j + 1,
                    "i0": i0, "i1": i1,
                    "rectangular": True,
                }

        rectangles.extend(rect for span, rect in active.items()
                          if span not in next_active or next_active[span] is not rect)
        active = next_active
        previous_row = j
    rectangles.extend(active.values())
    rectangles.sort(key=lambda box: (
        int(box["j0"]), int(box["i0"]),
        int(box["j1"]), int(box["i1"])))
    return rectangles


def crosses_painted_internal_edge(box: dict[str, int | bool],
                                  v_at: Sequence[Sequence[bool]],
                                  h_at: Sequence[Sequence[bool]]) -> bool:
    """Whether an emitted rectangle spans any visible internal separator."""
    j0, j1 = int(box["j0"]), int(box["j1"])
    i0, i1 = int(box["i0"]), int(box["i1"])
    return (
        any(v_at[i][j]
            for i in range(i0 + 1, i1)
            for j in range(j0, j1))
        or any(h_at[j][i]
               for j in range(j0 + 1, j1)
               for i in range(i0, i1))
    )


def assign_comb_anchors(cells: Sequence[dict[str, Any]],
                        dividers: Sequence[dict[str, Any]],
                        xl: Lattice, yl: Lattice,
                        final_paint: FinalPaint
                        ) -> tuple[list[list[dict[str, Any]]],
                                   list[dict[str, Any]],
                                   list[dict[str, Any]]]:
    """Give a final-visible divider band to exactly one rectangular subject.

    Midpoint ownership is unsafe: the bounding box of a non-rectangular DSU
    component can contain a divider whose actual band lies in another cell, and
    a later knockout can erase a raw mark while leaving its midpoint record
    behind. Ownership now requires the divider's full final-visible band to fit
    within the cell's painted outer ink bounds.

    Adjacent cells share their boundary ink. A band wholly inside that shared
    strip can therefore have two owners; that is ambiguous and is left
    unassigned rather than awarded by list order.
    """
    buckets: list[list[dict[str, Any]]] = [[] for _ in cells]
    unplaced: list[dict[str, Any]] = []
    ambiguous: list[dict[str, Any]] = []

    for divider in dividers:
        if not final_paint.structural_across(
                divider, float(divider["y0"]), float(divider["y1"])):
            unplaced.append(divider)
            continue
        cx = centre(divider)
        owners: list[int] = []
        for n, cell in enumerate(cells):
            i0, i1 = int(cell["col"]), int(cell["col"] + cell["col_span"])
            j0, j1 = int(cell["row"]), int(cell["row"] + cell["row_span"])
            if not (cell["x0"] + CLUSTER_TOL_PT
                    < cx
                    < cell["x1"] - CLUSTER_TOL_PT):
                continue
            if (divider["y0"] >= yl.ink_lo[j0] - CLUSTER_TOL_PT
                    and divider["y1"] <= yl.ink_hi[j1] + CLUSTER_TOL_PT
                    and divider["x0"] >= xl.ink_lo[i0] - CLUSTER_TOL_PT
                    and divider["x1"] <= xl.ink_hi[i1] + CLUSTER_TOL_PT):
                owners.append(n)
        if len(owners) == 1:
            buckets[owners[0]].append(divider)
        elif owners:
            ambiguous.append(divider)
        else:
            unplaced.append(divider)
    return buckets, unplaced, ambiguous


def comb_band_owners(cells: Sequence[dict[str, Any]],
                     x0: float, x1: float, y0: float, y1: float,
                     xl: Lattice, yl: Lattice) -> list[int]:
    """Cells whose painted outer bounds wholly contain one selected comb band."""
    owners: list[int] = []
    for n, cell in enumerate(cells):
        i0, i1 = int(cell["col"]), int(cell["col"] + cell["col_span"])
        j0, j1 = int(cell["row"]), int(cell["row"] + cell["row_span"])
        if (x0 >= xl.ink_lo[i0] - CLUSTER_TOL_PT
                and x1 <= xl.ink_hi[i1] + CLUSTER_TOL_PT
                and y0 >= yl.ink_lo[j0] - CLUSTER_TOL_PT
                and y1 <= yl.ink_hi[j1] + CLUSTER_TOL_PT):
            owners.append(n)
    return owners


def path_endpoint_conflicts(final_paint: FinalPaint,
                            band: dict[str, Any]) -> list[str]:
    """Later nonrect paint actually intersecting a divider's open endpoint.

    Bbox overlap is not ownership. Each path is reconstructed from its line and
    cubic subpaths, split into fill/stroke paint-order layers, and intersected
    with the narrow endpoint slab of each measured divider. A later intersecting
    layer makes the topology unresolved; it never deletes the subject.
    """
    conflicts: set[str] = set()
    sequences = list(band.get("divider_paint_seq") or ())
    ranges = list(band.get("divider_paint_ranges") or ())
    thicknesses = list(band.get("divider_thicknesses_pt") or ())
    default_thickness = max((float(value) for value in thicknesses), default=0.0)
    for index, divider_x in enumerate(band.get("divider_x") or ()):
        if index < len(ranges) and len(ranges[index]) == 2:
            divider_first = int(ranges[index][0])
        else:
            divider_first = (
                int(sequences[index]) if index < len(sequences) else -1)
        half = default_thickness / 2.0
        x0, x1 = float(divider_x) - half, float(divider_x) + half
        y0 = float(band["y0"]) - JOIN_EPSILON_PT
        y1 = float(band["y0"]) + JOIN_EPSILON_PT
        for path in final_paint.path_paints:
            _path_first, path_last = paint_ordinal_range(path)
            # Only a path known to finish no later than the divider begins is
            # safely earlier. Overlapping source-order ranges may put the path
            # on top at this endpoint and therefore remain unresolved.
            if path_last <= divider_first:
                continue
            if path_paint_intersects_rect(path, x0, y0, x1, y1):
                conflicts.add(
                    f"{path.get('id', 'path')}:{path.get('_path_layer', 'path')}")
    return sorted(conflicts)


def mark_comb_unresolved(comb: dict[str, Any], *reason_codes: str,
                         method: str | None = None) -> dict[str, Any]:
    """Copy a comb contract and append machine-readable uncertainty."""
    marked = dict(comb)
    previous = dict(marked.get("resolution") or {})
    reasons = set(previous.get("reason_codes") or ())
    reasons.update(reason for reason in reason_codes if reason)
    # Once ownership proves that no final-visible band exists, the narrower
    # rail-only observation is redundant.  Keep one stable root cause in the
    # published contract while retaining the rail check for otherwise owned
    # candidates.
    if "no-final-visible-owned-band" in reasons:
        reasons.discard("horizontal-rail-only-topology")
    previous.update({
        "status": "unresolved",
        "method": method or previous.get("method") or "unresolved",
        "reason_codes": sorted(reasons),
    })
    marked["resolution"] = previous
    return marked


def geometry_subject_key(page_index: int, bbox: Sequence[float]) -> str:
    """Stable subject identity independent of sequential cell enumeration."""
    return "p{}@{}".format(
        page_index,
        ",".join(f"{q(value):.{QUANT}f}" for value in bbox),
    )


def same_boundary_topology(left: Sequence[float],
                           right: Sequence[float]) -> bool:
    """One-to-one equality at the established lattice clustering tolerance."""
    return (
        len(left) == len(right)
        # Coordinates are decimal-quantised, but subtracting (for example)
        # 10.30 - 10.00 can be a binary float just above 0.30. The epsilon is
        # representation-only and far below the 0.01pt source precision.
        and all(abs(a - b) <= CLUSTER_TOL_PT + 1e-9
                for a, b in zip(sorted(left), sorted(right)))
    )


def retained_replacement_covers_inference(
        subjects: Sequence[dict[str, Any]],
        cell: dict[str, Any],
        inferred_comb: dict[str, Any],
        ) -> bool:
    """Whether one retained blocker already records this exact inference.

    An erased legacy edge can expand a current rectangle and thereby change its
    geometry subject key. The retained legacy entry records that replacement
    candidate explicitly and remains blocking pending independent evidence. If
    the current-only pass also publishes the same candidate as a new inference,
    one physical uncertainty is counted twice. Suppress only an exact, uniquely
    represented candidate; stale or ambiguous evidence stays blocking.
    """
    # ``combs`` is emitted only when this rectangle carries more than one band.
    # The retained candidate can cover the selected band, but it cannot account
    # for any additional band; keep the current-only inference blocking instead
    # of deleting the complete band inventory below.
    if "combs" in cell:
        return False

    matches: list[dict[str, Any]] = []
    for subject in subjects:
        if (subject.get("state") != "retained_unresolved"
                or subject.get("blocks_gate") is not True
                or subject.get("requires_independent_evidence") is not True):
            continue
        replacements = subject.get("erased_edge_replacement_candidates")
        if not isinstance(replacements, list):
            continue
        subject_matches = [
            candidate for candidate in replacements
            if (isinstance(candidate, dict)
                and candidate.get("new_subject_key")
                == cell.get("subject_key")
                and candidate.get("cell_id") == cell.get("id"))
        ]
        if subject_matches and len(replacements) != 1:
            return False
        matches.extend(subject_matches)
    if len(matches) != 1:
        return False

    candidate = matches[0]
    blockers = candidate.get("activation_blockers")
    if (candidate.get("blocks_gate") is not True
            or candidate.get("one_to_one_geometry_candidate") is not True
            or not isinstance(blockers, list)
            or not blockers
            or any(not isinstance(blocker, str) or not blocker.strip()
                   for blocker in blockers)
            or type(candidate.get("cells")) is not int
            or type(inferred_comb.get("cells")) is not int
            or type(inferred_comb.get("divider_count")) is not int
            or candidate["cells"] < 2
            or candidate["cells"] != inferred_comb["cells"]
            or inferred_comb["divider_count"] != candidate["cells"] - 1):
        return False

    def quantized_coordinate(value: Any) -> bool:
        return (type(value) in (int, float)
                and math.isfinite(float(value))
                and q(float(value)) == float(value))

    def exact_coordinates(value: Any, expected: Sequence[float]) -> bool:
        return (isinstance(value, list)
                and len(value) == len(expected)
                and all(quantized_coordinate(item)
                        and quantized_coordinate(wanted)
                        and q(float(item)) == q(float(wanted))
                        for item, wanted in zip(value, expected)))

    bbox = [cell.get(name) for name in ("x0", "y0", "x1", "y1")]
    band_y = [inferred_comb.get("y0"), inferred_comb.get("y1")]
    divider_x = inferred_comb.get("divider_x")
    slot_x = inferred_comb.get("slot_x")
    if not (isinstance(divider_x, list)
            and isinstance(slot_x, list)
            and len(divider_x) == candidate["cells"] - 1
            and len(slot_x) == candidate["cells"] + 1
            and exact_coordinates(candidate.get("new_bbox"), bbox)
            and exact_coordinates(candidate.get("band_y"), band_y)
            and exact_coordinates(candidate.get("divider_x"), divider_x)
            and exact_coordinates(candidate.get("new_slot_x"), slot_x)):
        return False

    x0, y0, x1, y1 = (float(value) for value in bbox)
    band_y0, band_y1 = (float(value) for value in band_y)
    dividers = [float(value) for value in divider_x]
    slots = [float(value) for value in slot_x]
    # The outer slots are the comb's own measured rails (`comb_rails`), so they
    # are not required to be the rectangle's edges: a rectangle may rule a
    # caption beside the comb, and its x is a fused mean the rail need not sit
    # on. What must hold is that every COMPARTMENT is this rectangle's, which
    # is exactly that its centre is inside the rectangle -- a compartment
    # centred outside belongs to the subject next door, however the rails that
    # bound it were derived.
    return (
        x0 < x1
        and y0 < y1
        and y0 <= band_y0 < band_y1 <= y1
        and all(x0 < (left + right) / 2.0 < x1
                for left, right in zip(slots, slots[1:]))
        and slots[1:-1] == dividers
        and all(left < right for left, right in zip(slots, slots[1:]))
    )


def boundary_topology_subset(left: Sequence[float],
                             right: Sequence[float]) -> bool:
    """Strict monotone one-to-one subset at the clustering tolerance.

    ``all(any())`` is insufficient here: two close values on the left could
    otherwise reuse one value on the right and manufacture a subset. Both
    topologies are ordered physical boundaries, so a monotone scan is the
    deterministic matching certificate.
    """
    if len(left) >= len(right):
        return False
    available = iter(sorted(float(value) for value in right))
    candidate = next(available, None)
    for wanted in sorted(float(value) for value in left):
        while candidate is not None and candidate < wanted - CLUSTER_TOL_PT:
            candidate = next(available, None)
        if candidate is None or candidate > wanted + CLUSTER_TOL_PT:
            return False
        candidate = next(available, None)
    return True


def erased_witness_rail_residue(
        final_paint: FinalPaint, witness: dict[str, Any],
        ) -> list[Interval] | None:
    """Rail-covered residue completing a partial knockout's erasure proof.

    ``FinalPaint.definitely_erased`` demands one known-later nonstructural
    layer covering the witness's complete bbox.  2550M paints its Schedule
    date-box knockout only down to the middle of the row's bottom rule, so a
    0.36pt sliver of the stale tick survives the knockout -- wholly inside
    the final-visible horizontal rail, where there is no paper on which the
    sliver could print (the same no-paper contract
    ``horizontal_rail_across`` documents).  On the composited page the mark
    is then exactly as absent as a fully covered one.

    Returns the rail-covered y intervals when one known-later nonstructural
    rectangular layer covers everything else of the witness's own bbox, or
    None when any part of it is neither covered nor inside a final-visible
    horizontal rail across the witness's full width.  The single-layer
    requirement, the strict paint-order requirement and the exact
    rectangular-path restriction are ``definitely_erased``'s own; only the
    no-paper residue is new, and every excused interval is returned so the
    caller publishes what the rail absorbed.
    """
    x0, y0, x1, y1 = (
        float(witness["x0"]), float(witness["y0"]),
        float(witness["x1"]), float(witness["y1"]),
    )
    if x1 <= x0 or y1 <= y0:
        return None
    _ink_first, ink_last = paint_ordinal_range(witness)
    best: list[Interval] | None = None
    for paint in final_paint.paints:
        if paint.get("role") == "structural":
            continue
        paint_first, _paint_last = paint_ordinal_range(paint)
        if paint_first <= ink_last:
            continue
        px0, py0, px1, py1 = paint_bounds(paint)
        if not (px0 <= x0 and px1 >= x1):
            continue
        cov0, cov1 = max(y0, py0), min(y1, py1)
        if cov1 <= cov0:
            continue
        if "_path_layer" in paint and not exact_rectangular_path_fill_covers(
                paint, x0, cov0, x1, cov1):
            continue
        residue = [
            (lo, hi) for lo, hi in ((y0, cov0), (cov1, y1))
            if hi - lo > JOIN_EPSILON_PT
        ]
        if not all(
                final_paint.horizontal_rail_across(x0, x1, lo, hi)
                for lo, hi in residue):
            continue
        if best is None or sum(hi - lo for lo, hi in residue) < sum(
                hi - lo for lo, hi in best):
            best = residue
    return best


def erased_legacy_divider_reduction_certificate(
        legacy_comb: dict[str, Any],
        final_comb: dict[str, Any],
        legacy_witnesses: Sequence[dict[str, Any]],
        final_paint: FinalPaint,
        ) -> dict[str, Any] | None:
    """Prove that a smaller final comb omitted only fully erased raw ink.

    The legacy comb is a denominator, not final-paint truth.  It deliberately
    remembers raw source marks so an algorithm change cannot silently delete a
    reviewed subject.  That continuity becomes stale when the PDF paints a
    black tick, covers it with a later white rectangle, then paints the actual
    divider elsewhere.  ``FinalPaint`` already rejects the stale tick; this
    certificate is the narrow bridge that lets the current topology replace
    the larger historical count.

    Every surviving final divider must be a one-to-one subset of the legacy
    positions at the established clustering tolerance.  Every omitted position
    must have its exact full-band raw witness, that witness must be covered by a
    known-later nonstructural layer -- except where the uncovered remainder
    lies wholly inside a final-visible horizontal rail, which carries no paper
    (``erased_witness_rail_residue``; 2550M's knockout stops at the middle of
    the row's bottom rule) -- and no final structural corridor may have been
    repainted at the same position.  Anything partial, path-shaped,
    source-order-ranged, multiply matched, or newly positioned fails closed.
    """
    raw_legacy_x = legacy_comb.get("divider_x") or ()
    raw_final_x = final_comb.get("divider_x") or ()
    if (not isinstance(raw_legacy_x, (list, tuple))
            or not isinstance(raw_final_x, (list, tuple))
            or not all(type(value) in (int, float)
                       and math.isfinite(float(value))
                       for value in [*raw_legacy_x, *raw_final_x])):
        return None
    if any(q(float(value)) != float(value)
           for value in [*raw_legacy_x, *raw_final_x]):
        return None
    legacy_x = sorted(float(value) for value in raw_legacy_x)
    final_x = sorted(float(value) for value in raw_final_x)
    legacy_cells = legacy_comb.get("cells")
    final_cells = final_comb.get("cells")
    if (not legacy_x
            or len(final_x) >= len(legacy_x)
            or type(legacy_cells) is not int
            or type(final_cells) is not int
            or legacy_cells != len(legacy_x) + 1
            or final_cells != len(final_x) + 1
            or len(legacy_witnesses) != len(legacy_x)):
        return None
    if (any(right - left <= CLUSTER_TOL_PT + 1e-9
            for left, right in zip(legacy_x, legacy_x[1:]))
            or any(right - left <= CLUSTER_TOL_PT + 1e-9
                   for left, right in zip(final_x, final_x[1:]))):
        return None

    if not all(
            type(comb.get(name)) in (int, float)
            and math.isfinite(float(comb[name]))
            for comb in (legacy_comb, final_comb)
            for name in ("y0", "y1")):
        return None
    legacy_y0 = float(legacy_comb["y0"])
    legacy_y1 = float(legacy_comb["y1"])
    final_y0 = float(final_comb["y0"])
    final_y1 = float(final_comb["y1"])
    if (final_y0 < legacy_y0 - JOIN_EPSILON_PT - 1e-9
            or final_y1 > legacy_y1 + JOIN_EPSILON_PT + 1e-9):
        return None

    raw_legacy_slots = legacy_comb.get("slot_x") or ()
    raw_final_slots = final_comb.get("slot_x") or ()
    if (not isinstance(raw_legacy_slots, (list, tuple))
            or not isinstance(raw_final_slots, (list, tuple))
            or len(raw_legacy_slots) != legacy_cells + 1
            or len(raw_final_slots) != final_cells + 1
            or not all(type(value) in (int, float)
                       and math.isfinite(float(value))
                       for value in [*raw_legacy_slots, *raw_final_slots])):
        return None
    if any(q(float(value)) != float(value)
           for value in [*raw_legacy_slots, *raw_final_slots]):
        return None
    if (any(float(left) >= float(right)
            for left, right in zip(raw_legacy_slots, raw_legacy_slots[1:]))
            or any(float(left) >= float(right)
                   for left, right in zip(raw_final_slots, raw_final_slots[1:]))
            or [q(value) for value in raw_legacy_slots[1:-1]]
            != [q(value) for value in legacy_x]
            or [q(value) for value in raw_final_slots[1:-1]]
            != [q(value) for value in final_x]):
        return None
    legacy_outer = (
        float(raw_legacy_slots[0]), float(raw_legacy_slots[-1]))
    final_outer = (float(raw_final_slots[0]), float(raw_final_slots[-1]))
    if any(q(old) != q(new)
           for old, new in zip(legacy_outer, final_outer)):
        return None

    rail_trims: list[dict[str, Any]] = []
    for edge, trim_y0, trim_y1 in (
            ("top", legacy_y0, final_y0),
            ("bottom", final_y1, legacy_y1)):
        if trim_y1 - trim_y0 <= JOIN_EPSILON_PT + 1e-9:
            continue
        if not final_paint.horizontal_rail_across(
                legacy_outer[0], legacy_outer[1], trim_y0, trim_y1):
            return None
        rail_trims.append({
            "edge": edge,
            "y0": q(trim_y0),
            "y1": q(trim_y1),
        })

    if not all(
            isinstance(witness, dict)
            and all(type(witness.get(name)) in (int, float)
                    and math.isfinite(float(witness[name]))
                    for name in ("x0", "x1", "y0", "y1", "thickness_pt"))
            for witness in legacy_witnesses):
        return None
    ordered_witnesses = sorted(
        legacy_witnesses,
        key=lambda witness: (
            q(centre(witness)), -paint_ordinal(witness),
            -float(witness["thickness_pt"])),
    )
    for position, witness in zip(legacy_x, ordered_witnesses):
        if abs(q(centre(witness)) - q(position)) > 1e-9:
            return None

    unmatched = set(range(len(legacy_x)))
    for position in final_x:
        matches = [
            index for index in sorted(unmatched)
            if abs(legacy_x[index] - position)
            <= CLUSTER_TOL_PT + 1e-9
        ]
        if len(matches) != 1:
            return None
        unmatched.remove(matches[0])
    if not unmatched:
        return None

    band_y0 = float(legacy_comb.get("y0", math.nan))
    band_y1 = float(legacy_comb.get("y1", math.nan))
    if not (math.isfinite(band_y0) and math.isfinite(band_y1)
            and band_y1 > band_y0):
        return None

    erased: list[dict[str, Any]] = []
    for index in sorted(unmatched):
        witness = ordered_witnesses[index]
        witness_first, _witness_last = paint_ordinal_range(witness)
        later_structural_paths = [
            path for path in final_paint.path_paints
            if path.get("role") == "structural"
            and paint_ordinal_range(path)[1] > witness_first
            and path_paint_intersects_rect(
                path,
                float(witness["x0"]), band_y0,
                float(witness["x1"]), band_y1)
        ]
        if (float(witness["y0"]) > band_y0 + 1e-9
                or float(witness["y1"]) < band_y1 - 1e-9
                or later_structural_paths
                or final_paint.structural_across_axis(
                    witness, band_y0, band_y1, "v")):
            return None
        # The complete-coverage proof, or the same proof with the un-covered
        # remainder shown to lie wholly inside a final-visible horizontal
        # rail -- where there is no paper the remainder could print on.  Any
        # un-covered, un-railed portion fails closed exactly as before.
        rail_residue: list[Interval] | None = (
            [] if final_paint.definitely_erased(witness)
            else erased_witness_rail_residue(final_paint, witness))
        if rail_residue is None:
            return None
        witness_id = witness.get("id")
        if not isinstance(witness_id, str) or not witness_id:
            return None
        entry: dict[str, Any] = {
            "divider_x": q(legacy_x[index]),
            "rule_id": witness_id,
            "paint_range": list(paint_ordinal_range(witness)),
            "band_y": [q(band_y0), q(band_y1)],
        }
        if rail_residue:
            entry["rail_covered_residue_y"] = [
                [q(lo), q(hi)] for lo, hi in rail_residue
            ]
        erased.append(entry)

    return {
        "criterion": "final-visible-erased-legacy-divider-reduction-v1",
        "legacy_cells": len(legacy_x) + 1,
        "final_cells": len(final_x) + 1,
        "legacy_band_y": [q(legacy_y0), q(legacy_y1)],
        "final_paper_band_y": [q(final_y0), q(final_y1)],
        "horizontal_rail_trims": rail_trims,
        "retained_divider_x": [q(value) for value in final_x],
        "erased_dividers": erased,
    }


def certify_erased_legacy_reduction(
        comb: dict[str, Any], certificate: dict[str, Any],
        ) -> dict[str, Any]:
    """Attach an auditable erased-divider transition to a current comb."""
    certified = dict(comb)
    resolution = dict(certified.get("resolution") or {})
    resolution["legacy_count_reduction"] = certificate
    certified["resolution"] = resolution
    return certified


def comb_owner_failure_reason(cell: dict[str, Any],
                              comb: dict[str, Any]) -> str | None:
    """Why a comb band lacks paper owned by this emitted rectangle.

    Bands may legitimately straddle a shared horizontal rule, so full vertical
    containment is too strict.  They must, however, have positive vertical
    intersection with the cell and keep every physical slot boundary inside
    its horizontal extent.  A band traversing both the cell's top and bottom
    rails also needs one direct paint record for every divider.  A composite
    paint-order range can be the hull of collinear fragments from adjacent
    rows; it cannot prove that the intervening corridor belongs to this cell.
    A merely touching or unproved multi-row band therefore cannot be emitted
    with negative or entirely out-of-cell offsets.
    """
    raw_slot_x = comb.get("slot_x")
    if not isinstance(raw_slot_x, (list, tuple)) or len(raw_slot_x) < 2:
        return "invalid-comb-owner-contract"
    if not all(
            type(value) in (int, float) and math.isfinite(float(value))
            for value in raw_slot_x):
        return "invalid-comb-owner-contract"
    slot_x = [float(value) for value in raw_slot_x]
    if any(left >= right for left, right in zip(slot_x, slot_x[1:])):
        return "invalid-comb-owner-contract"
    if not all(
            type(comb.get(name)) in (int, float)
            and math.isfinite(float(comb[name]))
            for name in ("y0", "y1")):
        return "invalid-comb-owner-contract"
    vertical_overlap = (
        min(float(cell["y1"]), float(comb["y1"]))
        - max(float(cell["y0"]), float(comb["y0"]))
    )
    if vertical_overlap <= 0.0:
        return "no-vertical-cell-overlap"
    if (slot_x[0] < float(cell["x0"]) - CLUSTER_TOL_PT
            or slot_x[-1] > float(cell["x1"]) + CLUSTER_TOL_PT):
        return "slot-boundary-outside-cell"
    traverses_both_rails = (
        float(comb["y0"]) < float(cell["y0"]) - CLUSTER_TOL_PT
        and float(comb["y1"]) > float(cell["y1"]) + CLUSTER_TOL_PT
    )
    if not traverses_both_rails:
        return None

    raw_divider_x = comb.get("divider_x")
    paint_sequences = comb.get("divider_paint_seq")
    paint_ranges = comb.get("divider_paint_ranges")
    if (not isinstance(raw_divider_x, (list, tuple))
            or not isinstance(paint_sequences, (list, tuple))
            or not isinstance(paint_ranges, (list, tuple))
            or len(raw_divider_x) != len(slot_x) - 2
            or len(paint_sequences) != len(raw_divider_x)
            or len(paint_ranges) != len(raw_divider_x)):
        return "unproved-multi-row-divider-corridor"
    if not all(
            type(value) in (int, float)
            and math.isfinite(float(value))
            for value in raw_divider_x):
        return "unproved-multi-row-divider-corridor"
    if any(float(value) != slot_x[index + 1]
           for index, value in enumerate(raw_divider_x)):
        return "unproved-multi-row-divider-corridor"
    if not all(
            type(paint_sequence) is int
            and paint_sequence >= 0
            and isinstance(paint_range, (list, tuple))
            and len(paint_range) == 2
            and type(paint_range[0]) is int
            and type(paint_range[1]) is int
            and paint_range[0] >= 0
            and paint_range[0] == paint_range[1]
            and paint_sequence == paint_range[0]
            for paint_sequence, paint_range in zip(
                paint_sequences, paint_ranges)):
        return "unproved-multi-row-divider-corridor"
    return None


def comb_has_cell_owner(cell: dict[str, Any],
                        comb: dict[str, Any]) -> bool:
    """Whether a comb band has paper owned by this emitted rectangle."""
    return comb_owner_failure_reason(cell, comb) is None


def partition_ink(page: dict[str, Any]) -> list[dict[str, Any]]:
    """Every structural mark on one page, as a candidate compartment divider.

    Rules and area fills together and neither axis filtered out, because the
    geometry decides and no classification here can: the reader
    (`printed_partitions`) keeps a mark only when it overlaps the box it would
    divide by more than its own WIDTH, and a horizontal rail's overlap can
    never exceed its own height, which is the smaller number.  Filing a mark by
    axis first would be a second opinion about the same fact, and the two would
    have to be kept in step forever.

    `extract.py`'s 1.5pt cut between a rule and an area fill is likewise not
    consulted, for the reason `comb_boundary_candidates` gives one screen up:
    that cut is about how BIR DRAWS a line, and this asks whether the line
    divides a box.  Measured over the corpus the two views agree here anyway --
    no candidate this admits is wider than 1.6pt.
    """
    out: list[dict[str, Any]] = []
    for rule in page["rules"]:
        if rule.get("role") == "structural":
            out.append(rule)
    for index, fill in enumerate(page["area_fills"]):
        if fill.get("role") != "structural":
            continue
        out.append({
            "axis": "v",
            "x0": fill["x0"], "y0": fill["y0"],
            "x1": fill["x1"], "y1": fill["y1"],
            "thickness_pt": q(float(fill["x1"]) - float(fill["x0"])),
            "gray": fill.get("gray"),
            "role": fill["role"],
            "id": f"fill{index}",
            "paint_seq": fill.get("paint_seq", -1),
            "paint_seq_max": fill.get("paint_seq_max",
                                      fill.get("paint_seq", -1)),
        })
    out.sort(key=lambda ink: (float(ink["x0"]), float(ink["y0"]),
                              float(ink["x1"]), float(ink["y1"]),
                              str(ink.get("id"))))
    return out


def printed_partitions(cell: dict[str, Any],
                       candidates: Sequence[dict[str, Any]],
                       final_paint: FinalPaint) -> list[dict[str, float]]:
    """The strokes the source prints INSIDE one cell, as ink rectangles.

    A cell is one box to the grid and can still be several writing regions on
    the paper: 1604CF page 2 rules "ADDRESS OF PAYEES" off "* STATUS" with a
    column border the whole table long, 2551M page 2 does the same between
    "Period Covered" and "Name of Withholding Agent", and 2316 and 2550M print
    a bottom guide tick in the middle of a date box.  Each of those is one
    lattice cell carrying one wide input laid straight across a printed rule --
    the defect `audit.check_inputs_span_no_printed_divider` names.

    What is published is the INK, not a verdict: this cannot know where the
    writing box inside the cell will be, because the box is inset by the cell's
    own rules and then moved off whatever pre-printed glyph ink the sheet lays
    into it (`emit.field_box`).  The reader applies the two geometric tests to
    the box it is actually laying out.  What only this side can answer is what
    the composited page still SHOWS, which needs `FinalPaint`: 2550M draws a
    comb tick and paints a white rectangle over it, and dividing a box at a
    mark the sheet erased would invent a compartment.

    A comb cell is excluded and says so: its compartments ARE its partition,
    stated by `comb["slot_x"]`, and its slot rectangles are bound to the
    source's own rails by `comb_referee`. Two partitions of one cell would be
    two answers to one question.

    Published only when non-empty, so the layout of a cell the source draws
    nothing inside is byte-identical to before: 39 of the corpus's 9,971 cells
    carry one.
    """
    if cell.get("comb"):
        return []
    x0, y0 = float(cell["x0"]), float(cell["y0"])
    x1, y1 = float(cell["x1"]), float(cell["y1"])
    out: list[dict[str, float]] = []
    for ink in candidates:
        ix0, iy0 = float(ink["x0"]), float(ink["y0"])
        ix1, iy1 = float(ink["x1"]), float(ink["y1"])
        if ix0 < x0 or ix1 > x1 or iy1 <= y0 or iy0 >= y1:
            continue
        # The composited page's verdict, and only in the direction it can
        # prove: a mark one later opaque layer completely covers is not on the
        # paper. Anything weaker -- an uncertain overpaint, a partial knockout
        # like 1800's, which leaves 0.36 of a 0.48pt stroke showing -- is still
        # a printed divider, and the failure direction matters here: refusing
        # to divide lays a taxpayer's box over a rule the sheet prints, while
        # dividing at a mark that is really there costs nothing.
        if final_paint.definitely_erased(ink):
            continue
        out.append({"x0": q(ix0), "y0": q(iy0), "x1": q(ix1), "y1": q(iy1)})
    out.sort(key=lambda span: (span["x0"], span["y0"], span["x1"], span["y1"]))
    deduplicated: list[dict[str, float]] = []
    for span in out:
        if not deduplicated or span != deduplicated[-1]:
            deduplicated.append(span)
    return deduplicated


def comb_rail_span(comb: dict[str, Any]) -> Interval:
    """The outer rails one comb contract claims."""
    slot_x = comb.get("slot_x") or ()
    return (float(slot_x[0]), float(slot_x[-1]))


def dividers_within(comb: dict[str, Any], rails: Interval) -> int:
    """How many of one comb's boundaries fall strictly inside a rail pair.

    Two readings of the same field can now bound it differently -- one detector
    finds the printed rail, the other still ends on the cell -- so comparing
    their raw slot COUNTS compares different questions. A boundary outside a
    comb's rails was never one of that comb's compartment boundaries, and
    counting it as one is how a rail correction reads as a lost divider (and
    is then "preserved" by restoring the reading that was wrong).
    """
    lo, hi = rails
    return sum(
        1 for value in comb.get("divider_x") or ()
        if lo < float(value) < hi
    )


def comb_writing_surface(cell: dict[str, Any],
                         comb: dict[str, Any] | None = None,
                         ) -> tuple[float, float] | None:
    """The vertical paper a comb's compartments are written on.

    A divider tick is a GUIDE MARK, not a wall.  `comb_bands` measures the band
    the ticks span -- deliberately the shortest of them, because that is the one
    every listed boundary really crosses -- and that band is the right answer to
    "where are the compartment boundaries".  It is the wrong answer to "how tall
    is the writing box", and the official artwork says so directly: on 2550M the
    item-4 TIN cell is walled top and bottom at y 118.80-134.40 (15.60pt) at
    x = 65.64, 99.48, 104.28, 137.40, 141.96, 175.08, 179.88 and 212.76, while
    the digit separators between those walls are 3.12pt stubs at y 131.28-134.40.
    The stubs sit at the FOOT of a full-height box.  Reporting the stub as the
    band left a 3.12pt slot in a 15.60pt cell -- a 2.81pt face, unusable -- and
    the same reading put every comb in the corpus under `FIELD_MIN_SIZE_PT`.

    So the box is the cell's own printed walls, inset by the horizontal borders
    exactly as `emit.field_box` insets a plain text field, so that a comb and a
    plain field in the same row are written at the same height.  The inset is
    the cell's own measured thickness, not a constant, and it is surrendered
    whole when the cell is no taller than its own two borders -- the same
    concession `emit.field_box` makes, for the same reason: clearance is the
    optional part, the box is not.

    `slot_x`, the compartment count and the pitch are untouched by this: they
    are what the ticks measured, and the ticks measured them correctly.

    None when the cell has no positive vertical extent at all.  Callers fail
    closed on that rather than inventing a height.
    """
    y0 = float(cell["y0"])
    y1 = float(cell["y1"])
    border = cell.get("border") or {}

    def edge_weight(record: dict[str, Any] | None, edge_y: float) -> float:
        """The wall weight the COMB's own paper stands under (C1).

        The border record's fused `thickness_pt` is the right weight for
        DRAWING the border -- "where a border thins to 0.24 crossing a comb
        band its real weight is the 0.48 it carries everywhere else" -- but
        the writing surface is paper, and paper only cares about the ink
        actually over it.  Two scopes, both physical, neither a tolerance:

          * along the line, only segments overlapping the comb's own span
            (`slot_x[0]..slot_x[-1]`) count -- 1701-MS draws its top border
            0.5pt over the caption stretch and 0.2pt over the comb, and the
            comb's writing surface sits under the 0.2;
          * across the line, a segment must REACH this cell's edge within
            the clustering tolerance -- 2316 fuses the row above's 0.84pt
            rule into the boundary line although its ink stops 0.63pt short
            of this cell, and an inset borrowed from it pushes the writing
            surface 0.39pt below the wall that is actually there.

        Falls back to the fused thickness when the comb or the segment
        geometry is absent (plain fields, legacy layouts), which is exactly
        the pre-C1 behaviour.
        """
        if not record:
            return 0.0
        segments = record.get("segments")
        if comb is None or not isinstance(segments, list):
            return float(record.get("thickness_pt") or 0.0)
        slot_x = [float(value) for value in comb["slot_x"]]
        midpoints = [
            (left + right) / 2 for left, right in zip(slot_x, slot_x[1:])
        ]
        # The NEAREST segment to the edge decides, exactly the referee's own
        # qualifying rule (`source_wall_thickness` takes the run nearest the
        # edge by separation) -- a fixed reach distance was tried first and
        # broke every DOUBLED rule in the corpus, whose bars legitimately sit
        # half a white core away from the boundary (0619-E's date boxes:
        # 1.44pt bars 0.60pt each side of the edge).  Producer and referee
        # must measure the same relation or the corroboration compares two
        # different physical quantities; that mismatch was C1's entire
        # population.  A segment qualifies only where it SPANS one of the
        # comb's own compartment midpoints -- the same rays the referee
        # measures on -- so the two sides qualify identical ink by
        # construction.  A span-overlap tolerance was tried instead and
        # REVERTED: a heavier stretch nicking the span's first sliver bounds
        # no compartment, and counting it forced span-end probe rays into the
        # referee that refused 249 cells at shared-boundary junctions and
        # moved the human-reviewed 2551Q control tuples.
        nearest: float | None = None
        best = 0.0
        cell_y0, cell_y1 = float(cell["y0"]), float(cell["y1"])
        for segment in segments:
            if not any(segment["a0"] <= x <= segment["a1"]
                       for x in midpoints):
                continue
            c0, c1 = float(segment["c0"]), float(segment["c1"])
            # The referee's own candidate rule, mirrored: a run must overlap
            # the CELL's band within the coincidence tolerance.  2316 p1c40's
            # bottom border line carries the row below's 0.84pt rule, wholly
            # outside this cell with 0.43pt of paper between -- the referee
            # never considers it, and a nearest-by-separation pick without
            # this filter chose it over the 0.45pt wall actually standing at
            # the edge, leaving 0.23pt of that wall's ink inside the writing
            # band.
            if c0 >= cell_y1 + 0.25 or c1 <= cell_y0 - 0.25:
                continue
            separation = (0.0 if c0 <= edge_y <= c1
                          else min(abs(c0 - edge_y), abs(c1 - edge_y)))
            thickness = float(segment["thickness_pt"])
            if nearest is None or separation < nearest - 1e-9:
                nearest, best = separation, thickness
            elif abs(separation - nearest) <= 1e-9:
                best = max(best, thickness)
        return best

    top = edge_weight(border.get("top"), y0)
    bottom = edge_weight(border.get("bottom"), y1)
    if top + bottom >= y1 - y0:
        top = bottom = 0.0
    surface_y0, surface_y1 = q(y0 + top), q(y1 - bottom)
    if surface_y1 - surface_y0 <= 0.0:
        return None
    return surface_y0, surface_y1


# What the horizontal writing surface was derived from, per side, published so
# that a comb which could not be inset is COUNTED rather than indistinguishable
# from one that needed no inset.
RAIL_INK_WRITING_EDGE = "rail-ink"
RAIL_INK_UNMEASURED = "no-measured-rail-ink"
RAIL_INK_SURRENDERED = "surrendered-degenerate-writing-width"


def comb_writing_edges(comb: dict[str, Any],
                       ) -> tuple[float, float, str, str]:
    """The horizontal paper a comb's compartments are written on.

    The exact twin of `comb_writing_surface`, on the other axis and from a
    different measurement, because the two axes are bounded by different
    things: the top and bottom of a comb are the OWNING CELL's borders, while
    its left and right are the comb's own printed RAILS, which `comb_rails`
    has already measured and which need not be the cell's edges at all.

    `slot_x` runs rail CENTRE to rail CENTRE, exactly as every boundary in this
    module is positioned. That is the right answer to "where is this rail" and
    the wrong answer to "where may a character be written", and the sheet says
    so at the same place the vertical twin does: 2551M paints the wall left of
    item 28C over x 238.92-239.64 and prints the caption's own `C` up to
    239.5176, so a compartment starting at the rail's centre of 239.28 starts
    0.36pt INSIDE the printed rule, on top of the label, with no blank paper
    anywhere between the two. The writing surface begins at 239.64.

    Returns the two edges and, per side, what derived it. Nothing is guessed:
    where `comb_rails` measured no ink for a rail that side keeps `slot_x`'s
    own value -- today's behaviour, reported as `no-measured-rail-ink` -- and
    where insetting would leave no compartment to write in, BOTH insets are
    surrendered whole, the same concession `comb_writing_surface` makes when a
    cell is no taller than its own two borders.
    """
    slot_x = [float(value) for value in comb["slot_x"]]
    left_ink = comb.get("left_rail_ink")
    right_ink = comb.get("right_rail_ink")
    writing_x0, left_source = slot_x[0], RAIL_INK_UNMEASURED
    writing_x1, right_source = slot_x[-1], RAIL_INK_UNMEASURED
    if left_ink is not None:
        # The rail's INNER edge, and never further in than the rail's own
        # position: where the drawn bar lies entirely outside the rectangle the
        # comb is emitted on, the rectangle still bounds what may be typed.
        writing_x0 = q(max(slot_x[0], float(left_ink[1])))
        left_source = RAIL_INK_WRITING_EDGE
    if right_ink is not None:
        writing_x1 = q(min(slot_x[-1], float(right_ink[0])))
        right_source = RAIL_INK_WRITING_EDGE
    if (writing_x1 - writing_x0 <= 0.0
            or writing_x0 >= slot_x[1]
            or writing_x1 <= slot_x[-2]):
        return (slot_x[0], slot_x[-1],
                RAIL_INK_SURRENDERED, RAIL_INK_SURRENDERED)
    return writing_x0, writing_x1, left_source, right_source


def comb_on_writing_surface(comb: dict[str, Any],
                            surface: tuple[float, float]) -> dict[str, Any]:
    """Attach the writing surface to a comb contract, beside the measured band.

    `y0`/`y1`/`height_pt` are the SOURCE DIVIDER BAND -- the vertical extent
    the measured boundaries actually span.  That is the meaning every
    adjudicator of this contract binds to: `comb_referee.classify_band` seeds
    its open-compartment search from these two keys and demands that the
    source topology occupy a strict majority of that band, and its
    human-reviewed control (`REVIEWED_2551Q_EXPLICIT_COMPARTMENTS`) was signed
    against exactly that reading -- 2551Q p2c5 measures 14 compartments over
    its 6.96pt tick band, and can never measure anything over the 17.70pt
    writing box a previous revision published here (6.96 <= 17.70/2, so the
    majority rule correctly refuses the wider claim).  Restating the box into
    `y0`/`y1`, as that revision did, made 4,417 of 4,522 active combs
    source-unevaluable and failed the reviewed control.

    The writing box is still a real fact the emitter needs -- a guide tick is
    short ON PURPOSE and the taxpayer writes in the walled box above it -- so
    it is published beside the band as `writing_y0`/`writing_y1`/
    `writing_height_pt`, computed by `comb_writing_surface` from the cell's
    own walls with `emit.field_box`'s inset.  Two questions, two sets of keys;
    neither reading overwrites the other.

    The same distinction, and the same two sets of keys, on the horizontal:
    `slot_x` stays the measured boundary POSITIONS, every one of them, and
    `writing_x0`/`writing_x1` are where the outer compartments may be written
    -- `slot_x`'s outer values inset off the rails' own ink by
    `comb_writing_edges`. The INTERNAL boundaries are untouched by this, on
    both axes and for the same reason: a divider is one stroke between two
    compartments and both of them are drawn against its centre.
    """
    surface_y0, surface_y1 = surface
    resolved = dict(comb)
    resolved["writing_y0"] = surface_y0
    resolved["writing_y1"] = surface_y1
    resolved["writing_height_pt"] = q(surface_y1 - surface_y0)
    writing_x0, writing_x1, left_source, right_source = comb_writing_edges(
        resolved)
    resolved["writing_x0"] = writing_x0
    resolved["writing_x1"] = writing_x1
    resolved["writing_width_pt"] = q(writing_x1 - writing_x0)
    resolved["writing_x_rails"] = {"left": left_source, "right": right_source}
    return resolved


def source_owned_comb_frame(
        box: dict[str, Any],
        xl: Lattice, yl: Lattice,
        v_at: Sequence[Sequence[bool]],
        h_at: Sequence[Sequence[bool]],
        dividers: Sequence[dict[str, Any]],
        extra_ink: Sequence[dict[str, Any]],
        final_paint: FinalPaint,
        ) -> dict[str, Any] | None:
    """Prove that partial internal verticals belong to one framed comb field.

    Some official fields place a comb against the bottom stroke of a taller
    framed label cell. Collinear fragments from elsewhere make a subset of its
    comb ticks lattice positions, so a generic painted-edge partition would
    shatter the field. Preserve the broad cell only when final paint proves the
    complete outer frame and one resolved, bottom-frame-owned same-band divider
    topology. This is geometry-owned, never form-specific.
    """
    j0, j1 = int(box["j0"]), int(box["j1"])
    i0, i1 = int(box["i0"]), int(box["i1"])
    internal_verticals = [
        (i, j)
        for i in range(i0 + 1, i1)
        for j in range(j0, j1)
        if v_at[i][j]
    ]
    internal_horizontals = [
        (j, i)
        for j in range(j0 + 1, j1)
        for i in range(i0, i1)
        if h_at[j][i]
    ]
    # A crossed edge makes a certificate necessary for component preservation.
    # An ordinary four-sided cell can still need the same source-owned frame
    # proof to resolve one uniquely maximal nested endpoint topology.  Run the
    # proof for both cases, but avoid adding certificates to ordinary cells
    # whose topology is already resolved or remains incomparable.
    has_internal_edges = bool(internal_verticals or internal_horizontals)
    if not (
        all(v_at[i0][j] and v_at[i1][j] for j in range(j0, j1))
        and all(h_at[j0][i] and h_at[j1][i] for i in range(i0, i1))
    ):
        return None

    x0, x1 = xl.positions[i0], xl.positions[i1]
    y0, y1 = yl.positions[j0], yl.positions[j1]
    members = [
        divider for divider in dividers
        if x0 + CLUSTER_TOL_PT < centre(divider) < x1 - CLUSTER_TOL_PT
        and y0 <= (float(divider["y0"]) + float(divider["y1"])) / 2.0 <= y1
    ]
    if not members:
        return None
    edge_thickness = (
        max((float(rule["thickness_pt"]) for rule in xl.members[i0]),
            default=q(xl.ink_hi[i0] - xl.ink_lo[i0])),
        max((float(rule["thickness_pt"]) for rule in xl.members[i1]),
            default=q(xl.ink_hi[i1] - xl.ink_lo[i1])),
    )
    bands = comb_bands(
        members, extra_ink, x0, x1, edge_thickness, final_paint,
        owner_paper_from_lattice(xl, yl, i0, i1, j0, j1, extra_ink))
    bands = [
        band for band in bands
        if float(band["y0"]) >= y0 - CLUSTER_TOL_PT
        and float(band["y1"]) <= y1 + CLUSTER_TOL_PT
    ]
    if len(bands) != 1:
        return None
    band = bands[0]
    resolution = band.get("resolution") or {}
    reason_codes = set(resolution.get("reason_codes") or ())
    frame_resolves_competition = False
    if reason_codes == {"competing-endpoint-topologies"}:
        topologies = [
            tuple(float(value) for value in evidence["divider_x"])
            for evidence in resolution.get("endpoint_topologies") or ()
        ]
        maximal = [
            topology for topology in topologies
            if not any(boundary_topology_subset(topology, other)
                       for other in topologies)
        ]
        # The published evidence is untrimmed -- `endpoint_band` measures every
        # competing topology across the whole rectangle -- while the band has
        # since been bounded by its own rails. Compare the part of the winning
        # topology that lies inside those rails, or a comb whose rails cut off
        # a boundary would look like a different topology from the one that won.
        rail_lo, rail_hi = comb_rail_span(band)
        frame_resolves_competition = (
            len(maximal) == 1
            and same_boundary_topology(
                tuple(float(value) for value in band["divider_x"]),
                tuple(value for value in maximal[0]
                      if rail_lo < value < rail_hi))
        )
    if not has_internal_edges and not frame_resolves_competition:
        return None
    if ((resolution.get("status") != "resolved"
         and not frame_resolves_competition)
            or band["y0"] < y0 - CLUSTER_TOL_PT
            or band["y1"] > y1 + CLUSTER_TOL_PT):
        return None

    # The complete outer frame must survive, not only the lower writing band.
    if not (covers(xl.spans[i0], yl.ink_hi[j0], yl.ink_lo[j1])
            and covers(xl.spans[i1], yl.ink_hi[j0], yl.ink_lo[j1])
            and covers(yl.spans[j0], xl.ink_hi[i0], xl.ink_lo[i1])
            and covers(yl.spans[j1], xl.ink_hi[i0], xl.ink_lo[i1])):
        return None

    # One final-visible horizontal lattice boundary must be the connected
    # baseline spanning the paper between both rails.
    baseline_index = j1
    if not (
        yl.ink_lo[baseline_index] - JOIN_EPSILON_PT
        <= float(band["y1"])
        <= yl.ink_hi[baseline_index] + JOIN_EPSILON_PT
        and covers(
            yl.spans[baseline_index], xl.ink_hi[i0], xl.ink_lo[i1])
    ):
        return None

    def separated_top_rail_stub(ink: dict[str, Any],
                                start: float, end: float) -> bool:
        """A frame-hung stub whose continuation into the band is knocked out.

        2000-DST hangs a label separator from the outer top rail directly
        above its date comb and then paints the corridor between that stub
        and the band top white (the erased junction is the source's own
        statement that the stroke terminates before the comb).  Such a stub
        divides only the label zone of the framed field; it is not a
        through-going cell border.  Accept it only on source proof: the ink
        must start inside the outer top rail, stop strictly above the band,
        and the whole remaining corridor down to the band top must be
        definitely erased by later nonstructural paint.  A genuine column
        separator keeps its junction painted and still refuses the frame.
        """
        band_top = float(band["y0"])
        if start > yl.ink_hi[j0] + JOIN_EPSILON_PT:
            return False
        if end > band_top - JOIN_EPSILON_PT:
            return False
        first, last = paint_ordinal_range(ink)
        probe = {
            "x0": float(ink["x0"]), "y0": end,
            "x1": float(ink["x1"]), "y1": band_top,
            "paint_seq": first, "paint_seq_max": last,
        }
        return final_paint.definitely_erased(probe)

    divider_x = [float(value) for value in band["divider_x"]]
    divider_corridors: list[tuple[float, float]] = []
    for value in divider_x:
        matches = [
            ink for ink in [*members, *extra_ink]
            if abs(centre(ink) - value) <= CLUSTER_TOL_PT
            and float(ink["y0"]) <= float(band["y0"]) + CLUSTER_TOL_PT
            and float(ink["y1"]) >= float(band["y1"]) - CLUSTER_TOL_PT
        ]
        if not matches:
            return None
        divider_corridors.append((
            min(float(ink["x0"]) for ink in matches),
            max(float(ink["x1"]) for ink in matches),
        ))

    # A thick group divider can contribute a short horizontal cap at the band
    # endpoint. Such an edge is owned by the comb only when the whole crossed
    # adjacency lies inside one certified divider corridor. A row separator (or
    # the synthetic 2x2 reconnect seam) cannot satisfy this.
    for j, i in internal_horizontals:
        at_band_endpoint = (
            yl.ink_lo[j] - JOIN_EPSILON_PT
            <= float(band["y0"])
            <= yl.ink_hi[j] + JOIN_EPSILON_PT
            or yl.ink_lo[j] - JOIN_EPSILON_PT
            <= float(band["y1"])
            <= yl.ink_hi[j] + JOIN_EPSILON_PT
        )
        if not at_band_endpoint:
            return None
        if not any(
            corridor_x0 - CLUSTER_TOL_PT <= xl.positions[i]
            and xl.positions[i + 1] <= corridor_x1 + CLUSTER_TOL_PT
            for corridor_x0, corridor_x1 in divider_corridors
        ):
            return None

    for i, j in internal_verticals:
        if not any(abs(xl.positions[i] - value) <= CLUSTER_TOL_PT
                   for value in divider_x):
            return None

        # Attribute the *actual final-visible ink*, not merely the grid row in
        # which coverage was observed. A long same-x separator can overlap a
        # short comb tick near its endpoint while extending far into the label
        # above; testing only the row interval would then preserve a component
        # across real structural ink. The selected band plus the bottom frame
        # stroke is the complete allowed vertical corridor. A divider may cross
        # that baseline stroke, but no matching ink may escape above the band.
        band_y0 = float(band["y0"])
        endpoint_boundaries = [
            boundary
            for boundary in range(j0, j1 + 1)
            if (yl.ink_lo[boundary] - JOIN_EPSILON_PT
                <= band_y0
                <= yl.ink_hi[boundary] + JOIN_EPSILON_PT)
        ]
        allowed_y0 = min(
            [band_y0, *(yl.ink_lo[boundary]
                        for boundary in endpoint_boundaries)])
        allowed_y1 = yl.ink_hi[j1]
        # The band's own endpoint-slab resolution measured every competing
        # same-band topology as continuous divider corridors.  Ink lying in a
        # corridor of a topology that includes this divider position is comb
        # ink of that measured topology -- the 1.08pt tick overhang above the
        # 2000-DST band top is the losing topology's own corridor -- never a
        # cell border.  A long separator reaches far outside every measured
        # corridor and still refuses the frame.
        evidence_corridors = [
            (max(y0, float(run[0])), min(y1, float(run[1])))
            for topology in (resolution.get("endpoint_topologies") or ())
            if any(abs(float(value) - xl.positions[i]) <= CLUSTER_TOL_PT
                   for value in (topology.get("divider_x") or ()))
            for run in (topology.get("runs") or ())
            if len(run) == 2
        ]
        allowed_vertical_ink = union_intervals([
            # Collinear ink from the preceding field can enter the outer top
            # stroke by a rounding sliver without separating this cell's paper.
            (y0, min(y1, yl.ink_hi[j0])),
            (max(y0, allowed_y0), min(y1, allowed_y1)),
            *evidence_corridors,
        ])
        relevant: list[dict[str, Any]] = []
        seen_relevant: set[int] = set()
        for ink in [*members, *extra_ink, *xl.members[i]]:
            if id(ink) in seen_relevant:
                continue
            seen_relevant.add(id(ink))
            if abs(centre(ink) - xl.positions[i]) > CLUSTER_TOL_PT:
                continue
            # Only ink with positive area inside this component can constrain
            # it. A rule ending in the outer frame just above the component is
            # not an uncertain internal separator.
            if (float(ink["y1"]) <= y0
                    or float(ink["y0"]) >= y1):
                continue
            relevant.append(ink)
        if not relevant:
            return None

        for ink in relevant:
            visible = [
                (max(y0, start), min(y1, end))
                for start, end in final_paint.visible_intervals(ink)
                if min(y1, end) > max(y0, start)
            ]
            if not visible:
                # An uncertain same-x source rule cannot be assumed absent when
                # that assumption is what makes the broad frame rectangular.
                if not final_paint.definitely_erased(ink):
                    return None
                continue
            # Rectangular divider bars in the source can have one square-cap
            # overhang before the common endpoint slab. Attribute at most that
            # bar's own measured weight, contiguous with the selected band.
            # This admits the 0.48pt ragged endpoint present in several forms;
            # it cannot hide a long same-x separator.
            cap_floor = max(
                y0,
                band_y0 - float(ink["thickness_pt"]) - JOIN_EPSILON_PT,
            )
            ink_allowed = union_intervals([
                *allowed_vertical_ink,
                (cap_floor, min(y1, band_y0)),
            ])
            for start, end in visible:
                if covers(ink_allowed, start, end):
                    continue
                if not separated_top_rail_stub(ink, start, end):
                    return None

    return {
        "method": "final-visible-framed-comb",
        # The band's own measured rails, not the component box: `comb_rails`
        # has already established which printed bars bound this comb, and a
        # certificate that restated the box would be claiming a frame the
        # source does not draw.
        "outer_rail_x": [
            q(float(band["slot_x"][0])), q(float(band["slot_x"][-1]))],
        "baseline_y": q(yl.positions[baseline_index]),
        "band_y": [q(band["y0"]), q(band["y1"])],
        "divider_x": list(band["divider_x"]),
        "resolved_competing_topologies": frame_resolves_competition,
        "internal_lattice_x": sorted({
            q(xl.positions[i]) for i, _j in internal_verticals
        }),
        "internal_cap_edges": [
            [q(yl.positions[j]), q(xl.positions[i]), q(xl.positions[i + 1])]
            for j, i in internal_horizontals
        ],
    }


def build_cells(page_index: int, xl: Lattice, yl: Lattice,
                dsu: DisjointSet, v_at: list[list[bool]], h_at: list[list[bool]],
                v_ink: Sequence[dict[str, Any]], h_ink: Sequence[dict[str, Any]],
                dividers: Sequence[dict[str, Any]],
                extra_ink: Sequence[dict[str, Any]],
                final_paint: FinalPaint,
                text_runs: Sequence[dict[str, Any]],
                legacy_dividers: Sequence[dict[str, Any]] | None = None,
                legacy_extra_ink: Sequence[dict[str, Any]] | None = None,
                final_supported_divider_ids: set[str] | None = None,
                frame_dividers: Sequence[dict[str, Any]] | None = None,
                legacy_xl: Lattice | None = None,
                legacy_yl: Lattice | None = None,
                legacy_dsu: DisjointSet | None = None,
                legacy_v_at: list[list[bool]] | None = None,
                legacy_h_at: list[list[bool]] | None = None,
                legacy_v_ink: Sequence[dict[str, Any]] | None = None,
                legacy_h_ink: Sequence[dict[str, Any]] | None = None,
                uncertain_geometry_ids: set[str] | None = None,
                fillable_metrics: dict[str, float] | None = None,
                area_fills: Sequence[dict[str, Any]] | None = None,
                ) -> tuple[list[dict[str, Any]], list[str],
                           list[dict[str, Any]], list[dict[str, Any]]]:
    nx, ny = len(xl) - 1, len(yl) - 1
    components: dict[int, list[tuple[int, int]]] = collections.defaultdict(list)
    for j in range(ny):
        for i in range(nx):
            components[dsu.find(j * nx + i)].append((j, i))

    certificate_dividers = (
        dividers if frame_dividers is None else frame_dividers)
    certificate_extra = (
        extra_ink if legacy_extra_ink is None else legacy_extra_ink)
    boxes: list[dict[str, Any]] = []
    for root, squares in components.items():
        js = [j for j, _ in squares]
        is_ = [i for _, i in squares]
        component_box: dict[str, Any] = {
            "j0": min(js), "j1": max(js) + 1,
            "i0": min(is_), "i1": max(is_) + 1,
            "component_root": root,
        }
        component_is_rectangular = (
            len(squares)
            == (int(component_box["j1"]) - int(component_box["j0"]))
            * (int(component_box["i1"]) - int(component_box["i0"]))
        )
        component_box["rectangular"] = component_is_rectangular
        # Occupancy alone does not make a safe rectangle. A fully occupied
        # component can reconnect around a partial internal rule (a 2x2 block
        # split only in its top row is the minimal example). Partition every
        # component on painted row runs; keep the broad component_box above
        # solely for the legacy subject/ID continuity ledger.
        comb_frame = (
            source_owned_comb_frame(
                component_box, xl, yl, v_at, h_at,
                certificate_dividers, certificate_extra, final_paint)
            if component_is_rectangular else None
        )
        partitions = (
            [dict(component_box)] if comb_frame is not None
            else rectangular_row_runs(squares, v_at, h_at)
        )
        for partition in partitions:
            partition["component_root"] = root
            if comb_frame is not None:
                partition["comb_frame_certificate"] = comb_frame
            elif crosses_painted_internal_edge(partition, v_at, h_at):
                raise ValueError(
                    "row-run partition crosses a painted internal edge")
        for box in partitions:
            j0, j1 = int(box["j0"]), int(box["j1"])
            i0, i1 = int(box["i0"]), int(box["i1"])
            if not encloses_paper(xl, i0, i1) or not encloses_paper(yl, j0, j1):
                continue
            boxes.append(box)

    continuity_xl = xl if legacy_xl is None else legacy_xl
    continuity_yl = yl if legacy_yl is None else legacy_yl
    continuity_dsu = dsu if legacy_dsu is None else legacy_dsu
    continuity_v_at = v_at if legacy_v_at is None else legacy_v_at
    continuity_h_at = h_at if legacy_h_at is None else legacy_h_at
    continuity_v_ink = v_ink if legacy_v_ink is None else legacy_v_ink
    continuity_h_ink = h_ink if legacy_h_ink is None else legacy_h_ink
    unresolved_geometry_ids = (
        set() if uncertain_geometry_ids is None else uncertain_geometry_ids)
    legacy_components: dict[int, list[tuple[int, int]]] = (
        collections.defaultdict(list))
    legacy_nx, legacy_ny = len(continuity_xl) - 1, len(continuity_yl) - 1
    for j in range(legacy_ny):
        for i in range(legacy_nx):
            legacy_components[
                continuity_dsu.find(j * legacy_nx + i)
            ].append((j, i))

    legacy_boxes: list[dict[str, Any]] = []
    for root, squares in legacy_components.items():
        js = [j for j, _i in squares]
        is_ = [i for _j, i in squares]
        box: dict[str, Any] = {
            "j0": min(js), "j1": max(js) + 1,
            "i0": min(is_), "i1": max(is_) + 1,
            "component_root": root,
        }
        box["rectangular"] = (
            len(squares)
            == (int(box["j1"]) - int(box["j0"]))
            * (int(box["i1"]) - int(box["i0"]))
        )
        if (encloses_paper(
                continuity_xl, int(box["i0"]), int(box["i1"]))
                and encloses_paper(
                    continuity_yl, int(box["j0"]), int(box["j1"]))):
            legacy_boxes.append(box)

    legacy_boxes.sort(
        key=lambda box: (
            continuity_yl.positions[box["j0"]],
            continuity_xl.positions[box["i0"]],
        ))
    for index, box in enumerate(legacy_boxes):
        box["legacy_index"] = index

    def materialise_cell(
            box: dict[str, Any], identifier: str,
            cell_xl: Lattice = xl, cell_yl: Lattice = yl,
            cell_v_at: Sequence[Sequence[bool]] = v_at,
            cell_h_at: Sequence[Sequence[bool]] = h_at,
            cell_v_ink: Sequence[dict[str, Any]] = v_ink,
            cell_h_ink: Sequence[dict[str, Any]] = h_ink,
            track_geometry_uncertainty: bool = True,
            ) -> dict[str, Any]:
        j0, j1 = int(box["j0"]), int(box["j1"])
        i0, i1 = int(box["i0"]), int(box["i1"])
        x0, x1 = cell_xl.positions[i0], cell_xl.positions[i1]
        y0, y1 = cell_yl.positions[j0], cell_yl.positions[j1]

        border: dict[str, Any] = {}
        uncertain_border_ids: set[str] = set()
        for side, (lat, index, ink, lo, hi, present) in {
            "top": (
                cell_yl, j0, cell_h_ink,
                cell_xl.ink_hi[i0], cell_xl.ink_lo[i1],
                all(cell_h_at[j0][i] for i in range(i0, i1))),
            "bottom": (
                cell_yl, j1, cell_h_ink,
                cell_xl.ink_hi[i0], cell_xl.ink_lo[i1],
                all(cell_h_at[j1][i] for i in range(i0, i1))),
            "left": (
                cell_xl, i0, cell_v_ink,
                cell_yl.ink_hi[j0], cell_yl.ink_lo[j1],
                all(cell_v_at[i0][j] for j in range(j0, j1))),
            "right": (
                cell_xl, i1, cell_v_ink,
                cell_yl.ink_hi[j0], cell_yl.ink_lo[j1],
                all(cell_v_at[i1][j] for j in range(j0, j1))),
        }.items():
            if not present:
                border[side] = None
                continue
            thickness, gray, all_t, segments = line_thickness_gray(
                lat, index, ink, lo, hi, "h" if side in ("top", "bottom") else "v")
            border[side] = {"thickness_pt": thickness, "gray": gray,
                            "thicknesses_pt": all_t,
                            "segments": segments}
            if track_geometry_uncertainty and unresolved_geometry_ids:
                own = {id(rule) for rule in lat.members[index]}
                a0, a1 = (
                    ("x0", "x1") if side in ("top", "bottom")
                    else ("y0", "y1"))
                uncertain_border_ids.update(
                    str(rule.get("id"))
                    for rule in ink
                    if str(rule.get("id")) in unresolved_geometry_ids
                    and (abs(centre(rule) - lat.positions[index])
                         <= CLUSTER_TOL_PT
                         or id(rule) in own)
                    and float(rule[a1]) > lo - CLUSTER_TOL_PT
                    and float(rule[a0]) < hi + CLUSTER_TOL_PT
                )

        border_count = sum(1 for b in border.values() if b is not None)
        cell = {
            "id": identifier,
            "subject_key": geometry_subject_key(page_index, (x0, y0, x1, y1)),
            "x0": x0, "y0": y0, "x1": x1, "y1": y1,
            "row": j0, "col": i0, "row_span": j1 - j0, "col_span": i1 - i0,
            "rectangular": bool(box["rectangular"]),
            "border": border,
            "border_count": border_count,
            "text_run_ids": [],
            "is_empty": True,
            "kind": "blank",
            "_component_root": int(box["component_root"]),
        }
        if "comb_frame_certificate" in box:
            cell["comb_frame_certificate"] = box["comb_frame_certificate"]
        if uncertain_border_ids:
            cell["geometry_resolution"] = {
                "status": "unresolved",
                "reason_codes": ["uncertain-final-paint-boundary"],
                "rule_ids": sorted(uncertain_border_ids),
            }
        return cell

    # A component this whole-file's OWN certificate machinery already had
    # the chance to certify -- `source_owned_comb_frame` runs on every
    # FULLY OCCUPIED (rectangular) component -- must never be touched here.
    # `source_owned_comb_frame` returning None on one is an explicit
    # refusal (a divider landing on an internal seam rather than the outer
    # frame baseline, an incomplete rail, ...), and reunification exists
    # only for the shape that certificate cannot even reach: a component
    # that is NOT fully occupied, so no full-frame proof was ever
    # attempted. Trusting a legacy-discovered comb's divider positions
    # alone cannot tell the two apart -- both a real corpus comb and this
    # self-test's own "lands on the seam, not the baseline" probe agree on
    # divider count and position -- so the gate has to be this component
    # fact, not the divider geometry.
    rectangular_component_roots = {
        root for root, squares in components.items()
        if len(squares) == (
            (max(j for j, _i in squares) - min(j for j, _i in squares) + 1)
            * (max(i for _j, i in squares) - min(i for _j, i in squares) + 1)
        )
    }

    def _rail_local_span(x_position: float) -> list[Interval]:
        """Same-x rule ink joined by the fused-boundary physical test.

        `is_one_boundary` already treats a paper gap smaller than the sum of
        two bars' own thicknesses as one drawn stroke interrupted by a
        printing seam, not a doorway -- a table border transitioning to a
        thinner in-band comb rail at the identical x (1707-2021's v255/v256:
        0.48pt border ending at 339.65, 0.24pt comb rail starting 340.13,
        0.48pt of paper against a 0.72pt thickness sum). Applied along one
        rail instead of across a fused pair, the same test tells a real
        break in a wall from a seam within it, without touching the
        page-wide `near_centre` coverage `GroupGeometry` already computes.
        """
        near = sorted(
            (rule for rule in v_ink
             if abs(centre(rule) - x_position) <= CLUSTER_TOL_PT),
            key=lambda rule: (float(rule["y0"]), float(rule["y1"])))
        merged: list[list[float]] = []
        for rule in near:
            y0, y1 = float(rule["y0"]), float(rule["y1"])
            thickness = float(rule["thickness_pt"])
            if merged and y0 - merged[-1][1] <= merged[-1][2] + thickness:
                merged[-1][1] = max(merged[-1][1], y1)
                merged[-1][2] = max(merged[-1][2], thickness)
            else:
                merged.append([y0, y1, thickness])
        return [(a, b) for a, b, _c in merged]

    def _reunify_comb_band(subject: dict[str, Any]) -> None:
        """F064 / Route B: let a registered comb band own the paper it spans.

        A comb band the source draws as one printed feature can end up with
        no owning CURRENT rectangle when a narrow, elsewhere-located rule's
        own row boundary happens to fall inside the band's own height -- a
        false cut the band's own ink never asked for (1707-2021 item 8A: the
        cut sits at y=343.44, induced by two Yes/No checkbox bottom edges
        whose own ink is x 132.96-145.32 and 190.80-203.16, both entirely
        left of the comb's own rail).  Reworking the general lattice walk so
        no line ever over-reaches (Route A) was measured and refused: even
        at its most permissive tested bound it still fragments this exact
        band, still regresses an unrelated field on the SAME page, and moves
        166-751 cells across 13-24 of the 53 forms depending on the bound --
        not reviewable.  This is the narrow alternative Route A's own
        finding names: the comb is already correctly recognised (rails,
        pitch, divider count all measured), so give it a rectangle rather
        than reworking how every rectangle in the corpus is found.

        The candidate rectangle is bounded only by lattice positions that
        already exist elsewhere on the page -- no new column or row is
        invented, and every side is the nearest existing position outside
        the comb's own divider run, never past the legacy (pre-refinement)
        cell that originally discovered this comb.  Every current cell the
        rectangle would touch is either absorbed whole (and must already be
        empty paper) or trimmed on exactly one side (never split into an
        L-shape); an internal wall survives inside it only when that wall is
        one of the comb's own dividers.  Printed text is checked directly
        against `text_runs`, because runs are not yet bucketed onto `cells`
        at this point in the pipeline.  Any failed check leaves `cells`
        untouched and the subject falls through to the existing
        retained/suppressed path exactly as before.
        """
        legacy_comb = subject["comb"]
        raw_divider_x = legacy_comb.get("divider_x") or ()
        if len(raw_divider_x) < 1:
            return
        divider_x = [float(value) for value in raw_divider_x]
        comb_y0, comb_y1 = float(legacy_comb["y0"]), float(legacy_comb["y1"])
        if comb_y1 <= comb_y0:
            return
        bx0, bx1 = min(divider_x), max(divider_x)
        sx0, sy0, sx1, sy1 = (float(value) for value in subject["legacy_bbox"])

        # Only a subject the ordinary path cannot already own needs this at
        # all: if some current cell already carries this exact legacy bbox,
        # the standard reconciliation below finds it directly and nothing
        # here may touch `cells` -- every other legacy subject on the page,
        # already-resolved or not, must reach the comb-discovery loop with
        # the SAME cells it would have had without this mechanism.
        legacy_key = geometry_subject_key(page_index, (sx0, sy0, sx1, sy1))
        if any(str(cell["subject_key"]) == legacy_key for cell in cells):
            return

        # The nearest existing position outside the divider run is not
        # necessarily a rail of THIS band: a page-wide position can sit
        # between the run and its true rail purely by x/y coincidence with
        # unrelated ink elsewhere (Route A's own finding, e.g. 1707-2021's
        # x=246.0, an item-elsewhere checkbox edge 2.09pt inside this band's
        # own first divider).  Walk outward from the divider run and accept
        # the first candidate whose OWN locally-joined ink -- never ink
        # merely sharing its x from anywhere else on the page -- actually
        # covers the band's height.  A row boundary needs no such proof: it
        # is never required to be a complete wall (a cell's own border is
        # already allowed to go unrecorded when its ink is a seam), and
        # picking the nearest one still leaves the rectangle containing the
        # band, only ever with a tighter or looser margin above/below it.
        # Strictly outside the divider run: the run's own outermost divider
        # can itself be an xl position (the same page-wide x-coincidence
        # this whole mechanism exists to see past, 1707a-2021's own 24th
        # divider at x=579.34), and that divider's own ink trivially
        # "covers" the band's height in the rail-coverage walk below --
        # it IS the band. `<=`/`>=` here would accept it as its own rail
        # and produce a rectangle one compartment too narrow.
        left_pool = sorted(
            (p for p in xl.positions if sx0 - CLUSTER_TOL_PT <= p < bx0),
            reverse=True)
        right_pool = sorted(
            p for p in xl.positions if bx1 < p <= sx1 + CLUSTER_TOL_PT)
        top_candidates = [
            p for p in yl.positions if sy0 - CLUSTER_TOL_PT <= p <= comb_y0]
        bottom_candidates = [
            p for p in yl.positions if comb_y1 <= p <= sy1 + CLUSTER_TOL_PT]
        if not (left_pool and right_pool
                and top_candidates and bottom_candidates):
            return
        rx0 = next(
            (p for p in left_pool
             if covers(_rail_local_span(p), comb_y0, comb_y1)),
            None)
        rx1 = next(
            (p for p in right_pool
             if covers(_rail_local_span(p), comb_y0, comb_y1)),
            None)
        if rx0 is None or rx1 is None:
            return
        ry0, ry1 = max(top_candidates), min(bottom_candidates)
        if rx1 - rx0 <= 0.0 or ry1 - ry0 <= 0.0:
            return

        i0, i1 = xl.positions.index(rx0), xl.positions.index(rx1)
        j0, j1 = yl.positions.index(ry0), yl.positions.index(ry1)
        if i1 <= i0 or j1 <= j0:
            return

        # No wall may survive inside the rectangle unless it is one of the
        # comb's own dividers (`source_owned_comb_frame`'s own admission
        # test, applied here to a box that function never sees because its
        # component is not rectangular).
        for i in range(i0 + 1, i1):
            for j in range(j0, j1):
                if v_at[i][j] and not any(
                        abs(xl.positions[i] - value) <= CLUSTER_TOL_PT
                        for value in divider_x):
                    return
        for j in range(j0 + 1, j1):
            for i in range(i0, i1):
                if h_at[j][i]:
                    return

        # A rectangle that will not end up owning this subject's OWN full
        # divider topology must never touch `cells` at all -- otherwise a
        # cell some OTHER, already-working comb owns (attachment has not
        # run yet, so `cell.get("comb")` cannot see it here) gets resized
        # for no gain the moment this band's rectangle happens to overlap
        # it (2200c-2018 p1c13: a genuine, already-correct 2-slot comb one
        # step of this band's own divider run away from a DIFFERENT,
        # 4-slot legacy subject).  Ask the current pass's own divider
        # recognition directly, the same evidence `assign_comb_anchors`
        # will use, rather than trust that recognition to undo an
        # unhelpful mutation after the fact.
        band_divider_x = sorted(
            centre(divider) for divider in dividers
            if rx0 + JOIN_EPSILON_PT < centre(divider) < rx1 - JOIN_EPSILON_PT
            and comb_y0 - CLUSTER_TOL_PT
            <= (float(divider["y0"]) + float(divider["y1"])) / 2.0
            <= comb_y1 + CLUSTER_TOL_PT
        )
        if not same_boundary_topology(band_divider_x, divider_x):
            return

        absorbed: list[dict[str, Any]] = []
        trims: list[tuple[dict[str, Any], tuple[int, int, int, int]]] = []
        for cell in cells:
            cx0, cy0 = float(cell["x0"]), float(cell["y0"])
            cx1, cy1 = float(cell["x1"]), float(cell["y1"])
            if (cx1 <= rx0 + JOIN_EPSILON_PT or cx0 >= rx1 - JOIN_EPSILON_PT
                    or cy1 <= ry0 + JOIN_EPSILON_PT
                    or cy0 >= ry1 - JOIN_EPSILON_PT):
                continue
            if cell.get("comb") is not None:
                return
            if int(cell["_component_root"]) in rectangular_component_roots:
                # This cell's own DSU component was fully occupied, so
                # `source_owned_comb_frame` already had its chance to own
                # this exact band and either did (a certificate, refused
                # just above) or explicitly declined it (a divider that
                # only looks like the comb's own, e.g. one landing on an
                # internal seam rather than the outer frame baseline).
                # Reunification exists for the shape that certificate
                # cannot reach at all -- a NON-rectangular component --
                # never for one it already ruled on.
                return
            if cell.get("comb_frame_certificate") is not None:
                # A cell the row-run walk already certified as owning its
                # OWN framed comb (`source_owned_comb_frame`) is never
                # paper this mechanism may absorb, whole or in part --
                # every other check here is about proving this rectangle
                # is safe to claim, and that proof already exists, for a
                # different band, on this cell.
                return
            left_out = cx0 < rx0 - JOIN_EPSILON_PT
            right_out = cx1 > rx1 + JOIN_EPSILON_PT
            top_out = cy0 < ry0 - JOIN_EPSILON_PT
            bottom_out = cy1 > ry1 + JOIN_EPSILON_PT
            diffs = sum((left_out, right_out, top_out, bottom_out))
            if diffs == 0:
                absorbed.append(cell)
                continue
            if diffs != 1:
                return
            ci0 = int(cell["col"])
            ci1 = int(cell["col"] + cell["col_span"])
            cj0 = int(cell["row"])
            cj1 = int(cell["row"] + cell["row_span"])
            if left_out:
                remainder = (cj0, cj1, ci0, i0)
            elif right_out:
                remainder = (cj0, cj1, i1, ci1)
            elif top_out:
                remainder = (cj0, j0, ci0, ci1)
            else:
                remainder = (j1, cj1, ci0, ci1)
            trims.append((cell, remainder))

        if not absorbed and not trims:
            return

        # No printed ink may be swallowed.  Text is not yet bucketed onto
        # `cells` at this point in the pipeline, so `text_runs` is checked
        # directly rather than through a cell's own `text_run_ids`.
        for run in text_runs:
            run_x0, run_x1 = float(run["x0"]), float(run["x1"])
            run_y0, run_y1 = float(run["y0"]), float(run["y1"])
            run_cx = (run_x0 + run_x1) / 2.0
            run_cy = (run_y0 + run_y1) / 2.0
            if not (rx0 - JOIN_EPSILON_PT < run_cx < rx1 + JOIN_EPSILON_PT
                    and ry0 - JOIN_EPSILON_PT < run_cy < ry1 + JOIN_EPSILON_PT):
                continue
            if (run_x1 > rx0 + JOIN_EPSILON_PT
                    and run_x0 < rx1 - JOIN_EPSILON_PT
                    and run_y1 > ry0 + JOIN_EPSILON_PT
                    and run_y0 < ry1 - JOIN_EPSILON_PT):
                return

        # Tile check: the absorbed cells plus every trim's own rectangle-side
        # slice must exactly cover the rectangle's area -- proof that this
        # never claims paper another cell still owns and never leaves a hole
        # the new cell cannot see.
        area = (rx1 - rx0) * (ry1 - ry0)
        covered = sum(
            (cell["x1"] - cell["x0"]) * (cell["y1"] - cell["y0"])
            for cell in absorbed
        )
        for cell, _remainder in trims:
            ix0 = max(float(cell["x0"]), rx0)
            iy0 = max(float(cell["y0"]), ry0)
            ix1 = min(float(cell["x1"]), rx1)
            iy1 = min(float(cell["y1"]), ry1)
            covered += (ix1 - ix0) * (iy1 - iy0)
        if abs(covered - area) > JOIN_EPSILON_PT:
            return

        nonlocal next_partition_id
        # Replace by identity, never by dict equality: two blank cells can
        # otherwise compare equal enough for `list.index`/`list.remove` to
        # pick the wrong one.
        absorbed_ids = {id(cell) for cell in absorbed}
        trim_map = {id(cell): remainder for cell, remainder in trims}
        replaced: list[dict[str, Any]] = []
        for cell in cells:
            if id(cell) in absorbed_ids:
                continue
            if id(cell) in trim_map:
                rj0, rj1, ri0, ri1 = trim_map[id(cell)]
                trimmed_box = {
                    "j0": rj0, "j1": rj1, "i0": ri0, "i1": ri1,
                    "rectangular": True,
                    "component_root": cell["_component_root"],
                }
                replaced.append(materialise_cell(trimmed_box, cell["id"]))
                continue
            replaced.append(cell)
        cells[:] = replaced

        new_id = f"p{page_index}c{next_partition_id}"
        next_partition_id += 1
        new_box = {
            "j0": j0, "j1": j1, "i0": i0, "i1": i1,
            "rectangular": True,
            "component_root": -1,
        }
        new_cell = materialise_cell(new_box, new_id)
        new_cell["comb_band_reunification"] = {
            "legacy_subject_key": subject["subject_key"],
            "rect": [q(rx0), q(ry0), q(rx1), q(ry1)],
            "absorbed_cell_ids": sorted(cell["id"] for cell in absorbed),
            "trimmed_cell_ids": sorted(cell["id"] for cell, _r in trims),
        }
        # `cells` is reading order -- `order_rects_reading_order` is the same
        # key the final cell stream is sorted by -- and DOM order IS focus
        # order (F209), so appending at the end would tab this band in dead
        # last regardless of where on the page it prints. Insert at the
        # position that key already puts it at.
        insert_at = len(cells)
        new_key = (new_cell["y0"], new_cell["x0"])
        for index, existing in enumerate(cells):
            if (existing["y0"], existing["x0"]) > new_key:
                insert_at = index
                break
        cells.insert(insert_at, new_cell)

    legacy_cells = [
        materialise_cell(
            box, f"p{page_index}c{box['legacy_index']}",
            continuity_xl, continuity_yl,
            continuity_v_at, continuity_h_at,
            continuity_v_ink, continuity_h_ink,
            track_geometry_uncertainty=False)
        for box in legacy_boxes
    ]
    legacy_id_by_subject = {
        str(cell["subject_key"]): str(cell["id"])
        for cell in legacy_cells
    }

    cells: list[dict[str, Any]] = []
    next_partition_id = len(legacy_boxes)
    for box in boxes:
        cell = materialise_cell(box, "")
        identifier = legacy_id_by_subject.get(str(cell["subject_key"]))
        if identifier is None:
            identifier = f"p{page_index}c{next_partition_id}"
            next_partition_id += 1
        cell["id"] = identifier
        cells.append(cell)

    # The legacy cells are subject-discovery geometry only. They reproduce the
    # published denominator and ids, but a nonrectangular one is never emitted.
    continuity_dividers = (
        dividers if legacy_dividers is None else legacy_dividers)
    continuity_extra = (
        extra_ink if legacy_extra_ink is None else legacy_extra_ink)
    supported_ids = (
        {str(divider.get("id")) for divider in dividers}
        if final_supported_divider_ids is None
        else final_supported_divider_ids)
    legacy_anchor_buckets, _ = assign_points(
        legacy_cells,
        [(centre(divider), (divider["y0"] + divider["y1"]) / 2.0, divider)
         for divider in continuity_dividers],
    )
    legacy_subjects: list[dict[str, Any]] = []
    for cell, members in zip(legacy_cells, legacy_anchor_buckets):
        edges = tuple(
            0.0 if cell["border"][side] is None
            else cell["border"][side]["thickness_pt"]
            for side in ("left", "right"))
        legacy_paper = owner_paper_from_lattice(
            continuity_xl, continuity_yl,
            int(cell["col"]), int(cell["col"] + cell["col_span"]),
            int(cell["row"]), int(cell["row"] + cell["row_span"]),
            continuity_extra)
        bands = legacy_comb_bands(
            members, continuity_extra, cell["x0"], cell["x1"], edges,
            legacy_paper)
        if not bands:
            continue
        selected = max(
            bands, key=lambda band: (band["divider_count"], -band["y0"]))
        legacy_divider_witnesses = list(
            selected.pop("_divider_witnesses", ()))
        supported_members = [
            member for member in members
            if str(member.get("id")) in supported_ids
        ]
        left, right = edges
        support_frame = [
            (cell["x0"] - left / 2.0, cell["x0"] + left / 2.0, left),
            (cell["x1"] - right / 2.0, cell["x1"] + right / 2.0, right),
        ]
        has_distinct_final_support = any(
            all(distinct_boundary((
                float(member["x0"]), float(member["x1"]),
                float(member["thickness_pt"])), edge)
                for edge in support_frame)
            for member in supported_members
        )
        final_candidates = comb_bands(
            supported_members,
            continuity_extra, cell["x0"], cell["x1"], edges, final_paint,
            legacy_paper)
        final_candidate = (
            max(final_candidates,
                key=lambda band: (band["divider_count"], -band["y0"]))
            if final_candidates else None
        )
        legacy_subjects.append({
            "subject_key": cell["subject_key"],
            "legacy_cell_id": cell["id"],
            "legacy_bbox": [
                cell["x0"], cell["y0"], cell["x1"], cell["y1"],
            ],
            "legacy_cell": cell,
            "legacy_rectangular": bool(cell["rectangular"]),
            "component_root": cell["_component_root"],
            "comb": selected,
            "legacy_divider_witnesses": legacy_divider_witnesses,
            "final_candidate": final_candidate,
            "has_final_support": has_distinct_final_support,
        })

    for subject in legacy_subjects:
        _reunify_comb_band(subject)

    anchor_buckets, _unplaced_anchors, _ambiguous_anchors = assign_comb_anchors(
        cells, dividers, xl, yl, final_paint)
    for cell_index, (cell, members) in enumerate(zip(cells, anchor_buckets)):
        edges = tuple(0.0 if cell["border"][side] is None
                      else cell["border"][side]["thickness_pt"]
                      for side in ("left", "right"))
        bands = comb_bands(
            members, extra_ink, cell["x0"], cell["x1"], edges, final_paint,
            owner_paper_from_lattice(
                xl, yl,
                int(cell["col"]), int(cell["col"] + cell["col_span"]),
                int(cell["row"]), int(cell["row"] + cell["row_span"]),
                extra_ink))
        retained_bands: list[dict[str, Any]] = []
        rejected_owner_bands: list[dict[str, Any]] = []
        for band in bands:
            owner_failure = comb_owner_failure_reason(cell, band)
            if owner_failure is not None:
                rejected_owner_bands.append(mark_comb_unresolved(
                    band, owner_failure))
                continue
            owners = comb_band_owners(
                cells, cell["x0"], cell["x1"],
                band["y0"], band["y1"], xl, yl)
            if owners and cell_index not in owners:
                continue
            if owners != [cell_index]:
                reason = ("ambiguous-band-ownership" if owners
                          else "no-full-band-owner")
                band = mark_comb_unresolved(band, reason)
            conflicts = path_endpoint_conflicts(final_paint, band)
            if conflicts:
                band = mark_comb_unresolved(
                    band, "later-nonrect-path-endpoint-paint")
                band["resolution"]["path_conflicts"] = conflicts
            retained_bands.append(band)
        bands = retained_bands
        if bands:
            chosen_band = max(
                bands, key=lambda b: (b["divider_count"], -b["y0"]))
            certificate = cell.get("comb_frame_certificate") or {}
            chosen_resolution = chosen_band.get("resolution") or {}
            if (certificate.get("resolved_competing_topologies")
                    and set(chosen_resolution.get("reason_codes") or ())
                    == {"competing-endpoint-topologies"}):
                chosen_band = dict(chosen_band)
                chosen_band["resolution"] = {
                    **chosen_resolution,
                    "status": "resolved",
                    "method": "final-visible-framed-comb",
                    "reason_codes": [],
                }
            if cell.get("geometry_resolution"):
                chosen_band = mark_comb_unresolved(
                    chosen_band, "uncertain-final-paint-boundary")
            cell["comb"] = chosen_band
            if len(bands) > 1:
                cell["combs"] = bands
        elif rejected_owner_bands:
            # A partition-only topology is not allowed to disappear merely
            # because it cannot own this rectangle.  Keep the strongest
            # rejected candidate until the inference ledger below publishes
            # it as explicit, suppressed, and gate-blocking evidence.  A
            # reviewed legacy subject uses its own retained-subject path.
            cell["_suppressed_comb_inference"] = max(
                rejected_owner_bands,
                key=lambda band: (band["divider_count"], -band["y0"]),
            )

    output_by_subject = {cell["subject_key"]: cell for cell in cells}
    output_index_by_subject = {
        cell["subject_key"]: index for index, cell in enumerate(cells)
    }

    def boundary_rule_evidence(
            legacy_cell: dict[str, Any],
            candidate: dict[str, Any],
            changed_side: int,
            ) -> dict[str, Any]:
        """Source-paint state for one erased-edge replacement candidate."""
        vertical = changed_side in (0, 2)
        if vertical:
            old_index = (
                int(legacy_cell["col"])
                if changed_side == 0
                else int(legacy_cell["col"] + legacy_cell["col_span"])
            )
            new_index = (
                int(candidate["col"])
                if changed_side == 0
                else int(candidate["col"] + candidate["col_span"])
            )
            old_lattice, new_lattice, axis = continuity_xl, xl, "v"
            old_select = (float(legacy_cell["y0"]), float(legacy_cell["y1"]))
            new_select = (float(candidate["y0"]), float(candidate["y1"]))
            old_open = (
                continuity_yl.ink_hi[int(legacy_cell["row"])],
                continuity_yl.ink_lo[
                    int(legacy_cell["row"] + legacy_cell["row_span"])],
            )
            new_open = (
                yl.ink_hi[int(candidate["row"])],
                yl.ink_lo[int(candidate["row"] + candidate["row_span"])],
            )
            span_keys = ("y0", "y1")
        else:
            old_index = (
                int(legacy_cell["row"])
                if changed_side == 1
                else int(legacy_cell["row"] + legacy_cell["row_span"])
            )
            new_index = (
                int(candidate["row"])
                if changed_side == 1
                else int(candidate["row"] + candidate["row_span"])
            )
            old_lattice, new_lattice, axis = continuity_yl, yl, "h"
            old_select = (float(legacy_cell["x0"]), float(legacy_cell["x1"]))
            new_select = (float(candidate["x0"]), float(candidate["x1"]))
            old_open = (
                continuity_xl.ink_hi[int(legacy_cell["col"])],
                continuity_xl.ink_lo[
                    int(legacy_cell["col"] + legacy_cell["col_span"])],
            )
            new_open = (
                xl.ink_hi[int(candidate["col"])],
                xl.ink_lo[int(candidate["col"] + candidate["col_span"])],
            )
            span_keys = ("x0", "x1")

        def relevant_rules(lattice: Lattice, index: int,
                           span: tuple[float, float]
                           ) -> list[dict[str, Any]]:
            start_key, end_key = span_keys
            return [
                rule for rule in lattice.members[index]
                if float(rule[end_key]) > span[0]
                and float(rule[start_key]) < span[1]
            ]

        def state(rules: Sequence[dict[str, Any]],
                  open_span: tuple[float, float]) -> str:
            if any(final_paint.structural_across_axis(
                    rule, open_span[0], open_span[1], axis)
                   for rule in rules):
                return "final_visible"
            if rules and all(final_paint.definitely_erased(rule)
                             for rule in rules):
                return "definitely_erased"
            return "unresolved"

        old_rules = relevant_rules(old_lattice, old_index, old_select)
        new_rules = relevant_rules(new_lattice, new_index, new_select)
        return {
            "changed_side": ("left", "top", "right", "bottom")[changed_side],
            "old_boundary_position": q(old_lattice.positions[old_index]),
            "replacement_boundary_position": q(
                new_lattice.positions[new_index]),
            "old_rule_ids": sorted(str(rule.get("id")) for rule in old_rules),
            "replacement_rule_ids": sorted(
                str(rule.get("id")) for rule in new_rules),
            "old_boundary_final_state": state(old_rules, old_open),
            "replacement_boundary_final_state": state(new_rules, new_open),
        }

    def erased_edge_replacement_candidates(
            subject: dict[str, Any]) -> list[dict[str, Any]]:
        """Unique current rectangles that expand across one erased old edge.

        This is transition evidence, not an activation shortcut. In particular,
        an unresolved replacement rail leaves the legacy subject retained and
        blocking even when the geometric candidate is otherwise one-to-one.
        """
        if not subject["legacy_rectangular"]:
            return []
        legacy_cell = subject["legacy_cell"]
        legacy_bbox = [float(value) for value in subject["legacy_bbox"]]
        legacy_comb = subject["comb"]
        candidates: list[dict[str, Any]] = []
        for candidate in cells:
            current_bbox = [
                float(candidate["x0"]), float(candidate["y0"]),
                float(candidate["x1"]), float(candidate["y1"]),
            ]
            changed = [
                index for index, (old, new) in enumerate(
                    zip(legacy_bbox, current_bbox))
                if q(old) != q(new)
            ]
            if len(changed) != 1:
                continue
            if not (
                current_bbox[0] <= legacy_bbox[0]
                and current_bbox[1] <= legacy_bbox[1]
                and current_bbox[2] >= legacy_bbox[2]
                and current_bbox[3] >= legacy_bbox[3]
            ):
                continue
            if candidate["border_count"] != 4:
                continue
            if not (
                current_bbox[0] < min(legacy_comb["divider_x"])
                and current_bbox[2] > max(legacy_comb["divider_x"])
                and current_bbox[1] <= float(legacy_comb["y0"])
                and current_bbox[3] >= float(legacy_comb["y1"])
            ):
                continue
            current_comb = candidate.get("comb")
            if (current_comb is not None
                    and (int(current_comb["cells"])
                         != int(legacy_comb["cells"])
                         or not same_boundary_topology(
                             current_comb["divider_x"],
                             legacy_comb["divider_x"]))):
                continue
            paint = boundary_rule_evidence(
                legacy_cell, candidate, changed[0])
            if paint["old_boundary_final_state"] != "definitely_erased":
                continue
            blockers = []
            if paint["replacement_boundary_final_state"] != "final_visible":
                blockers.append("replacement-boundary-not-final-visible")
            if current_comb is None:
                blockers.append("no-final-visible-owned-band")
            if candidate.get("geometry_resolution"):
                blockers.extend(
                    candidate["geometry_resolution"].get("reason_codes") or ())
            if not blockers:
                blockers.append("independent-evidence-not-attested")
            candidates.append({
                "cell_id": candidate["id"],
                "old_subject_key": subject["subject_key"],
                "new_subject_key": candidate["subject_key"],
                "old_bbox": subject["legacy_bbox"],
                "new_bbox": current_bbox,
                "cells": int(legacy_comb["cells"]),
                "band_y": [
                    float(legacy_comb["y0"]), float(legacy_comb["y1"]),
                ],
                "divider_x": list(legacy_comb["divider_x"]),
                "old_slot_x": list(legacy_comb["slot_x"]),
                "new_slot_x": [
                    current_bbox[0], *legacy_comb["divider_x"],
                    current_bbox[2],
                ],
                "source_paint_evidence": paint,
                "activation_blockers": sorted(set(blockers)),
                "blocks_gate": True,
            })
        candidates.sort(key=lambda item: (
            item["new_bbox"], item["new_subject_key"]))
        for candidate in candidates:
            candidate["one_to_one_geometry_candidate"] = (
                len(candidates) == 1)
        return candidates

    def source_certified_replacement_owner(
            replacements: Sequence[dict[str, Any]],
            ) -> dict[str, Any] | None:
        """The unique replacement cell whose activation is fully source-proven.

        ``erased_edge_replacement_candidates`` collects every blocker that
        keeps a candidate from activating; the sentinel remains alone only
        when the source paint itself proves the whole transition: the old
        boundary is definitely erased, the replacement rail is final-visible,
        the candidate is geometrically one-to-one, and the current pass
        independently RESOLVED a final-visible comb of the identical count
        and topology inside the replacement rectangle.  That is the same
        source-paint epistemology that already certifies erased legacy
        divider reductions (``certify_erased_legacy_reduction``); suppressing
        the resolved comb here would leave a printed comb with no input.  Any
        weaker candidate -- an unresolved band, an unresolved replacement
        rail, competing candidates -- keeps the subject retained and
        blocking.
        """
        if len(replacements) != 1:
            return None
        candidate = replacements[0]
        if (candidate.get("one_to_one_geometry_candidate") is not True
                or candidate.get("activation_blockers")
                != ["independent-evidence-not-attested"]):
            return None
        owner = output_by_subject.get(str(candidate["new_subject_key"]))
        if owner is None or owner.get("id") != candidate["cell_id"]:
            return None
        if str(owner["subject_key"]) in legacy_keys:
            return None
        comb = owner.get("comb")
        if comb is None:
            return None
        if (comb.get("resolution") or {}).get("status") != "resolved":
            return None
        return owner

    def comb_band_reunification_owner(
            subject: dict[str, Any]) -> dict[str, Any] | None:
        """The reunified cell this subject's own comb band now owns, if any.

        `_reunify_comb_band` mutates `cells` before this reconciliation
        runs, but a reunified cell's own bbox is the comb band's own
        rectangle, not the legacy subject's wider mixed-cell bbox, so
        `output_by_subject` cannot find it by identity.  Find it instead by
        the same kind of evidence `source_certified_replacement_owner`
        already requires for a different-identity substitute: a resolved
        comb, inside the legacy subject's own bbox, whose divider topology
        is this subject's own.
        """
        legacy_comb = subject["comb"]
        legacy_divider_x = [
            float(value) for value in legacy_comb.get("divider_x") or ()]
        if not legacy_divider_x:
            return None
        sx0, sy0, sx1, sy1 = (
            float(value) for value in subject["legacy_bbox"])
        candidates = [
            candidate for candidate in cells
            if candidate.get("comb") is not None
            and (candidate["comb"].get("resolution") or {}).get("status")
            == "resolved"
            and str(candidate["subject_key"]) not in legacy_keys
            and float(candidate["x0"]) >= sx0 - CLUSTER_TOL_PT
            and float(candidate["x1"]) <= sx1 + CLUSTER_TOL_PT
            and float(candidate["y0"]) >= sy0 - CLUSTER_TOL_PT
            and float(candidate["y1"]) <= sy1 + CLUSTER_TOL_PT
            and same_boundary_topology(
                [float(value)
                 for value in candidate["comb"].get("divider_x") or ()],
                legacy_divider_x)
        ]
        if len(candidates) != 1:
            return None
        return candidates[0]

    subject_ledger: list[dict[str, Any]] = []
    inference_ledger: list[dict[str, Any]] = []
    legacy_keys = {
        str(subject["subject_key"]) for subject in legacy_subjects
    }
    for subject in legacy_subjects:
        subject_key = str(subject["subject_key"])
        cell = output_by_subject.get(subject_key)
        legacy_comb = subject["comb"]
        final_candidate = subject["final_candidate"]
        resolved = None if cell is None else cell.get("comb")
        if (cell is not None and resolved is not None
                and not comb_has_cell_owner(cell, resolved)):
            raise ValueError(
                f"{cell['id']}: current comb has no owning cell paper")
        legacy_owned = bool(
            cell is not None and comb_has_cell_owner(cell, legacy_comb))
        final_candidate_owned = (
            final_candidate
            if cell is not None
            and final_candidate is not None
            and comb_has_cell_owner(cell, final_candidate)
            else None
        )
        final_candidate_owner_indexes: list[int] = []
        final_candidate_path_conflicts: list[str] = []
        if final_candidate_owned is not None:
            final_candidate_owner_indexes = comb_band_owners(
                cells,
                float(cell["x0"]), float(cell["x1"]),
                float(final_candidate_owned["y0"]),
                float(final_candidate_owned["y1"]),
                xl, yl,
            )
            final_candidate_path_conflicts = path_endpoint_conflicts(
                final_paint, final_candidate_owned)
        final_candidate_has_unique_owner = (
            cell is not None
            and final_candidate_owner_indexes
            == [output_index_by_subject[subject_key]]
        )
        no_owned_band = bool(
            cell is not None
            and resolved is None
            and not legacy_owned
            and final_candidate_owned is None
        )
        candidate_owner_failures = (
            [] if cell is None else [
                failure
                for candidate in (legacy_comb, final_candidate)
                if candidate is not None
                for failure in [comb_owner_failure_reason(cell, candidate)]
                if failure is not None
            ]
        )
        if (cell is None
                or (cell.get("comb") is None
                    and final_candidate is None
                    and not subject["has_final_support"])
                or no_owned_band):
            replacements = erased_edge_replacement_candidates(subject)
            owner = (
                source_certified_replacement_owner(replacements)
                if cell is None else None)
            if owner is not None:
                candidate = replacements[0]
                owner_comb = owner["comb"]
                owner_comb["resolution"]["erased_edge_replacement"] = {
                    "old_subject_key": candidate["old_subject_key"],
                    "old_bbox": list(candidate["old_bbox"]),
                    "old_slot_x": list(candidate["old_slot_x"]),
                    "source_paint_evidence": dict(
                        candidate["source_paint_evidence"]),
                }
                # The replacement rectangle is now this reviewed subject's
                # owner; keep its resolved comb out of the unreviewed
                # inference suppression below.
                legacy_keys.add(str(owner["subject_key"]))
                subject_ledger.append({
                    "subject_key": owner["subject_key"],
                    "legacy_cell_id": owner["id"],
                    "legacy_bbox": [
                        owner["x0"], owner["y0"], owner["x1"], owner["y1"],
                    ],
                    "cell_id": owner["id"],
                    "mapped_partition_cell_ids": [owner["id"]],
                    "state": "active_resolved",
                    "reason_codes": [],
                    "cells": int(owner_comb["cells"]),
                    "blocks_gate": False,
                })
                continue
            reunified_owner = (
                comb_band_reunification_owner(subject)
                if cell is None else None)
            if reunified_owner is not None:
                reunified_comb = reunified_owner["comb"]
                legacy_keys.add(str(reunified_owner["subject_key"]))
                subject_ledger.append({
                    "subject_key": reunified_owner["subject_key"],
                    "legacy_cell_id": reunified_owner["id"],
                    "legacy_bbox": [
                        reunified_owner["x0"], reunified_owner["y0"],
                        reunified_owner["x1"], reunified_owner["y1"],
                    ],
                    "cell_id": reunified_owner["id"],
                    "mapped_partition_cell_ids": [reunified_owner["id"]],
                    "state": "active_resolved",
                    "reason_codes": [],
                    "cells": int(reunified_comb["cells"]),
                    "blocks_gate": False,
                })
                continue
            if cell is None:
                sx0, sy0, sx1, sy1 = (
                    float(value) for value in subject["legacy_bbox"])
                mapped = [
                    candidate for candidate in cells
                    if candidate["x0"] >= sx0 - CLUSTER_TOL_PT
                    and candidate["x1"] <= sx1 + CLUSTER_TOL_PT
                    and candidate["y0"] >= sy0 - CLUSTER_TOL_PT
                    and candidate["y1"] <= sy1 + CLUSTER_TOL_PT
                ]
            else:
                mapped = [cell]
            if cell is None:
                retained_reason_codes = [
                    "emission-suppressed-no-rectangular-owner",
                    "painted-edge-partition",
                ]
            elif "unproved-multi-row-divider-corridor" in (
                    candidate_owner_failures):
                retained_reason_codes = [
                    "emission-suppressed-unproved-multi-row-divider-corridor",
                ]
            else:
                retained_reason_codes = [
                    "emission-suppressed-no-final-visible-band",
                ]
            retained = {
                "subject_key": subject_key,
                "legacy_cell_id": subject["legacy_cell_id"],
                "legacy_bbox": subject["legacy_bbox"],
                "cell_id": None,
                "mapped_partition_cell_ids": [cell["id"] for cell in mapped],
                "mapped_partition_subject_keys": [
                    cell["subject_key"] for cell in mapped
                ],
                "state": "retained_unresolved",
                "emission": "suppressed",
                "reason_codes": retained_reason_codes,
                "legacy_comb": legacy_comb,
                "requires_independent_evidence": True,
                "permitted_transitions": [
                    "active_composite",
                    "retired_proven_false",
                ],
                "blocks_gate": True,
            }
            if replacements:
                retained["erased_edge_replacement_candidates"] = replacements
            subject_ledger.append(retained)
            continue

        final_candidate = final_candidate_owned
        erased_reduction_certificate = None
        if (legacy_owned
                and final_candidate is not None
                and final_candidate_has_unique_owner
                and not final_candidate_path_conflicts
                and (final_candidate.get("resolution") or {}).get("status")
                == "resolved"):
            erased_reduction_certificate = (
                erased_legacy_divider_reduction_certificate(
                    legacy_comb, final_candidate,
                    subject["legacy_divider_witnesses"], final_paint))
        # Counts are compared inside the candidate's own rails, never as raw
        # slot totals: `dividers_within` says why.
        if (resolved is not None and final_candidate is not None
                and int(final_candidate["divider_count"])
                > dividers_within(
                    resolved, comb_rail_span(final_candidate))):
            if final_candidate_has_unique_owner:
                cell["comb"] = final_candidate
                if final_candidate_path_conflicts:
                    cell["comb"] = mark_comb_unresolved(
                        cell["comb"],
                        "later-nonrect-path-endpoint-paint")
                    cell["comb"]["resolution"]["path_conflicts"] = (
                        final_candidate_path_conflicts)
            else:
                cell["comb"] = mark_comb_unresolved(
                    resolved, "anchor-ownership-disagreement")
            resolved = cell["comb"]
        if resolved is None:
            if final_candidate is None:
                cell["comb"] = mark_comb_unresolved(
                    legacy_comb, "no-final-visible-band",
                    method="legacy-continuity")
            elif (int(final_candidate["divider_count"])
                  < dividers_within(
                      legacy_comb, comb_rail_span(final_candidate))):
                if erased_reduction_certificate is not None:
                    cell["comb"] = certify_erased_legacy_reduction(
                        final_candidate, erased_reduction_certificate)
                else:
                    cell["comb"] = mark_comb_unresolved(
                        legacy_comb, "final-visible-count-regression",
                        "no-final-visible-owned-band",
                        method="legacy-continuity")
                    cell["comb"]["resolution"][
                        "final_visible_candidate_cells"] = int(
                            final_candidate["cells"])
            else:
                if final_candidate_has_unique_owner:
                    cell["comb"] = final_candidate
                    if final_candidate_path_conflicts:
                        cell["comb"] = mark_comb_unresolved(
                            cell["comb"],
                            "later-nonrect-path-endpoint-paint")
                        cell["comb"]["resolution"]["path_conflicts"] = (
                            final_candidate_path_conflicts)
                else:
                    cell["comb"] = mark_comb_unresolved(
                        final_candidate, "no-final-visible-owned-band")
        elif (int(resolved["divider_count"])
              < dividers_within(legacy_comb, comb_rail_span(resolved))):
            reduction_matches_current = (
                erased_reduction_certificate is not None
                and final_candidate is not None
                and int(resolved["cells"]) == int(final_candidate["cells"])
                and same_boundary_topology(
                    resolved["divider_x"], final_candidate["divider_x"]))
            if reduction_matches_current:
                cell["comb"] = certify_erased_legacy_reduction(
                    resolved, erased_reduction_certificate)
            elif legacy_owned:
                preserved = mark_comb_unresolved(
                    legacy_comb, "final-visible-count-regression",
                    method="legacy-continuity")
                preserved["resolution"]["final_visible_candidate_cells"] = int(
                    resolved["cells"])
                cell["comb"] = preserved
            else:
                cell["comb"] = mark_comb_unresolved(
                    resolved, "anchor-ownership-disagreement")

        if not legacy_owned:
            cell["comb"] = mark_comb_unresolved(
                cell["comb"], "anchor-ownership-disagreement")

        if cell.get("geometry_resolution"):
            cell["comb"] = mark_comb_unresolved(
                cell["comb"], "uncertain-final-paint-boundary")

        topology_transition: dict[str, Any] | None = None
        if int(cell["comb"]["cells"]) == int(legacy_comb["cells"]):
            old_divider_x = sorted(
                q(float(value)) for value in legacy_comb["divider_x"])
            new_divider_x = sorted(
                q(float(value)) for value in cell["comb"]["divider_x"])
            if not same_boundary_topology(old_divider_x, new_divider_x):
                # Equal slot counts do not make two physical combs the same
                # subject. A resolved current detector is not independent
                # evidence for moving the reviewed legacy boundaries, so this
                # transition remains blocking until a referee certifies it.
                topology_transition = {
                    "old_divider_x": old_divider_x,
                    "new_divider_x": new_divider_x,
                    "comparison_tolerance_pt": CLUSTER_TOL_PT,
                    "independently_certified": False,
                }
                cell["comb"] = mark_comb_unresolved(
                    cell["comb"],
                    "same-count-boundary-topology-change")
                cell["comb"]["resolution"]["boundary_topology_transition"] = (
                    dict(topology_transition))

        resolution = cell["comb"].get("resolution") or {}
        unresolved = resolution.get("status") != "resolved"
        ledger_entry = {
            "subject_key": subject_key,
            "legacy_cell_id": subject["legacy_cell_id"],
            "legacy_bbox": subject["legacy_bbox"],
            "cell_id": cell["id"],
            "mapped_partition_cell_ids": [cell["id"]],
            "state": "active_unresolved" if unresolved else "active_resolved",
            "reason_codes": list(resolution.get("reason_codes") or ()),
            "cells": int(cell["comb"]["cells"]),
            "blocks_gate": unresolved,
        }
        if topology_transition is not None:
            ledger_entry["old_divider_x"] = list(
                topology_transition["old_divider_x"])
            ledger_entry["new_divider_x"] = list(
                topology_transition["new_divider_x"])
            ledger_entry["boundary_topology_transition"] = (
                dict(topology_transition))
        subject_ledger.append(ledger_entry)

    # A partition-only inferred subject has no reviewed predecessor. Suppress it
    # explicitly instead of silently changing the reviewed 4,442 denominator.
    for cell in cells:
        rejected_inference = cell.pop("_suppressed_comb_inference", None)
        if cell["subject_key"] in legacy_keys:
            continue
        inferred_comb = cell.get("comb") or rejected_inference
        if inferred_comb is None:
            continue
        if retained_replacement_covers_inference(
                subject_ledger, cell, inferred_comb):
            cell.pop("comb", None)
            cell.pop("combs", None)
            continue
        inference_reasons = ["no-legacy-subject"]
        if rejected_inference is not None:
            owner_reasons = list(
                (rejected_inference.get("resolution") or {}).get(
                    "reason_codes") or ())
            inference_reasons.extend(
                f"emission-suppressed-{reason}"
                for reason in owner_reasons
                if reason not in {"no-legacy-subject"}
            )
        inference_ledger.append({
            "subject_key": cell["subject_key"],
            "cell_id": cell["id"],
            "bbox": [cell["x0"], cell["y0"], cell["x1"], cell["y1"]],
            "state": "suppressed_unreviewed_inference",
            "reason_codes": sorted(set(inference_reasons)),
            "inferred_comb": inferred_comb,
            "requires_independent_evidence": True,
            "permitted_transitions": ["active_reviewed"],
            "blocks_gate": True,
        })
        cell.pop("comb", None)
        cell.pop("combs", None)

    # Ownership is proved against the band the ticks measured -- that is the
    # evidence that this rectangle owns those boundaries.  Only once it holds
    # does the contract get to state the writing surface, so that widening the
    # extent can never be what makes a comb look owned.
    for cell in cells:
        comb = cell.get("comb")
        if comb is None:
            continue
        if not comb_has_cell_owner(cell, comb):
            raise ValueError(
                f"{cell['id']}: active comb has no owning cell paper")
        surface = comb_writing_surface(cell)
        if surface is None:
            raise ValueError(
                f"{cell['id']}: comb owner has no writing surface")
        # The ledger can replace the selected band after the inventory was
        # taken, so the selection is not always one of `combs`.  Restate the
        # inventory once and reuse the same object for the selection when it
        # is there, so the two keys never disagree about one physical band.
        # Each band gets ITS OWN surface: the walls over one band's span can
        # differ from another's, and from the cell-wide fused weight (C1).
        def band_surface(band: dict[str, Any]) -> tuple[float, float]:
            band_value = comb_writing_surface(cell, band)
            if band_value is None:
                raise ValueError(
                    f"{cell['id']}: comb owner has no writing surface")
            return band_value

        restated = {
            id(band): comb_on_writing_surface(band, band_surface(band))
            for band in cell.get("combs") or ()
        }
        selected = restated.get(id(comb))
        cell["comb"] = (comb_on_writing_surface(comb, band_surface(comb))
                        if selected is None else selected)
        if "combs" in cell:
            cell["combs"] = [restated[id(band)] for band in cell["combs"]]

    assigned, unplaced = assign_points(
        cells, [((r["x0"] + r["x1"]) / 2.0, (r["y0"] + r["y1"]) / 2.0, index)
                for index, r in enumerate(text_runs)],
        [glyph_ink_spans(r) for r in text_runs])
    for cell, members in zip(cells, assigned):
        cell["text_run_ids"] = [f"p{page_index}t{i}" for i in sorted(members)]
    unassigned = [f"p{page_index}t{i}" for i in sorted(unplaced)]

    # The last word on whether a claimed comb is a comb, taken once, after
    # every band has been selected, owned and restated, and on the same
    # evidence `is_empty` is about to be computed from.  It has to be last:
    # the legacy reconciliation above re-attaches a band to a cell that has
    # none, so a refusal made earlier would simply come back.
    #
    # `emit.field_verdict` gives an input to any cell carrying `comb`,
    # whatever kind the lattice assigned -- deliberately, because a comb IS
    # the field.  A refutation therefore has to take the comb off the cell;
    # returning a different kind beside a comb the emitter still honours would
    # leave the layout and the markup contradicting each other, which is worse
    # than either verdict alone.
    #
    # The subject does NOT leave the ledger.  That is the ledger's whole
    # purpose -- "a comb that stops being a writing surface does not leave the
    # ledger" -- so the reviewed subject denominator is untouched and the
    # subject moves to the retained half, published, emitting nothing, blocking
    # the gate, and carrying `retired_proven_false` among its permitted
    # transitions for whoever reviews it.  Nothing here retires it: a producer
    # does not certify its own promotion, and this is the fail-closed side of
    # that rule, not an exception to it.
    refuted: dict[str, dict[str, Any]] = {}
    for cell in cells:
        comb = cell.get("comb")
        if comb is None:
            continue
        runs = [text_runs[int(run_id.split("t")[1])]
                for run_id in cell["text_run_ids"]]
        if not printed_caption_refutes_comb(comb, runs):
            continue
        cell.pop("comb", None)
        cell.pop("combs", None)
        refuted[str(cell["subject_key"])] = {
            "cell": cell,
            "comb": comb,
            "compartment_glyph_counts": comb_compartment_glyph_counts(
                comb, runs),
        }
    for index, subject in enumerate(subject_ledger):
        evidence = refuted.pop(str(subject["subject_key"]), None)
        if evidence is None:
            continue
        cell = evidence["cell"]
        # The retained shape is an IDENTITY mapping: one legacy subject, one
        # layout rectangle, same bbox.  A refuted caption block is always that
        # -- the band was selected on this rectangle's own anchors -- and if it
        # ever is not, the ledger would be claiming a partition it cannot
        # support, so fail closed rather than publish it.
        if (subject.get("cell_id") != cell["id"]
                or subject.get("legacy_cell_id") != cell["id"]
                or [q(float(value)) for value in subject["legacy_bbox"]]
                != [q(float(cell[name])) for name in ("x0", "y0", "x1", "y1")]):
            raise ValueError(
                f"{cell['id']}: refuted comb subject is not an identity "
                f"mapping onto its own rectangle")
        subject_ledger[index] = {
            "subject_key": subject["subject_key"],
            "legacy_cell_id": subject["legacy_cell_id"],
            "legacy_bbox": subject["legacy_bbox"],
            "cell_id": None,
            "mapped_partition_cell_ids": [subject["legacy_cell_id"]],
            "mapped_partition_subject_keys": [subject["subject_key"]],
            "state": "retained_unresolved",
            "emission": "suppressed",
            "reason_codes": [REFUTED_CAPTION_BLOCK_REASON_CODE],
            "legacy_comb": evidence["comb"],
            "requires_independent_evidence": True,
            "permitted_transitions": [
                "active_composite",
                "retired_proven_false",
            ],
            "blocks_gate": True,
        }
        cell["comb_refutation"] = {
            "reason_codes": [REFUTED_CAPTION_BLOCK_REASON_CODE],
            "compartment_glyph_counts": evidence["compartment_glyph_counts"],
            "refused_slot_x": [q(float(value))
                               for value in evidence["comb"]["slot_x"]],
        }
    if refuted:
        # A refuted band that no subject published would be a comb this module
        # emitted with no ledger entry at all -- exactly the unreviewed
        # inference the suppression path below exists to prevent.
        raise ValueError(
            "refuted comb has no ledger subject: "
            + ", ".join(sorted(refuted)))

    # DECISION A (2026-08-16), the compartment-rule sweep -- deliberately
    # AFTER the caption refutation above and in its exact shape. The two
    # passes catch the same disease in different tissue: a false comb whose
    # compartments hold PRINTED TEXT is refuted by the caption pass with its
    # richer glyph-count evidence and keeps its reviewed caption-block
    # reason; a false comb whose compartments are EMPTY paper (2551M p2c13's
    # column-rule pair, 1604CF p2c73's grid cells -- nothing printed for the
    # caption pass to read) is caught here by width alone: no run of
    # character-box compartments survives the rule, so the cell's published
    # comb comes off and the subject retains, emitting nothing, blocking,
    # awaiting review. Order is precedence: an earlier attempt routed these
    # in the subject loop and stole eleven caption-block subjects on eight
    # forms from the pass above, restamping reviewed reasons and mismatching
    # every one of their certificates.
    rule_retained: dict[str, dict[str, Any]] = {}
    for cell in cells:
        comb = cell.get("comb")
        if comb is None:
            continue
        slot_x = [float(value) for value in (comb.get("slot_x") or ())]
        if len(slot_x) < 2 or compartment_runs(slot_x):
            continue
        cell.pop("comb", None)
        cell.pop("combs", None)
        rule_retained[str(cell["subject_key"])] = {
            "cell": cell, "comb": comb}
    for index, subject in enumerate(subject_ledger):
        evidence = rule_retained.pop(str(subject["subject_key"]), None)
        if evidence is None:
            continue
        cell = evidence["cell"]
        if (subject.get("cell_id") != cell["id"]
                or subject.get("legacy_cell_id") != cell["id"]
                or [q(float(value)) for value in subject["legacy_bbox"]]
                != [q(float(cell[name])) for name in ("x0", "y0", "x1", "y1")]):
            raise ValueError(
                f"{cell['id']}: rule-refused comb subject is not an "
                f"identity mapping onto its own rectangle")
        subject_ledger[index] = {
            "subject_key": subject["subject_key"],
            "legacy_cell_id": subject["legacy_cell_id"],
            "legacy_bbox": subject["legacy_bbox"],
            "cell_id": None,
            "mapped_partition_cell_ids": [subject["legacy_cell_id"]],
            "mapped_partition_subject_keys": [subject["subject_key"]],
            "state": "retained_unresolved",
            "emission": "suppressed",
            "reason_codes": ["emission-suppressed-compartment-rule"],
            "legacy_comb": evidence["comb"],
            "requires_independent_evidence": True,
            "permitted_transitions": [
                "active_composite",
                "retired_proven_false",
            ],
            "blocks_gate": True,
        }
        cell["comb_refutation"] = {
            "reason_codes": ["emission-suppressed-compartment-rule"],
            "refused_slot_x": [q(float(value))
                               for value in evidence["comb"]["slot_x"]],
        }
    if rule_retained:
        raise ValueError(
            "rule-refused comb has no ledger subject: "
            + ", ".join(sorted(rule_retained)))

    page_area_fills = () if area_fills is None else area_fills
    for cell in cells:
        cell["is_empty"] = not cell["text_run_ids"]
        sliver = False
        shaded = False
        if (cell["is_empty"]
                and cell["border_count"] >= 3
                and "comb" not in cell):
            if fillable_metrics is not None:
                paper_width, paper_height = cell_paper_gap(cell, xl, yl)
                sliver = (
                    paper_height < fillable_metrics["glyph_height_pt"]
                    and paper_width >= fillable_metrics["line_width_pt"]
                )
            # Comb owners are exempt, as they are from the sliver rule, and
            # for a stronger reason: printed character ticks are the source
            # itself drawing N boxes to receive typing, and `emit.field_verdict`
            # gives a comb an input whatever the kind says.  Demoting one here
            # would leave the layout claiming "not a writing surface" over an
            # emitted input -- worse than either verdict alone.  On the 53-form
            # corpus this exempts nothing: no comb owner is covered by
            # decorative shading, so the rule is stated, not measured into.
            shaded = on_shaded_paper(
                cell, page_area_fills,
                0.0 if fillable_metrics is None
                else float(fillable_metrics["glyph_height_pt"]))
        cell["kind"] = classify_cell(
            cell["is_empty"], cell["border_count"], "comb" in cell, sliver,
            shaded)
        cell.pop("_component_root")
    resolve_retained_partition_overlaps(subject_ledger)
    # DOM order IS focus order (F209). Reunification inserts by (y, x); put
    # the finished stream back into column-aware reading order so side-by-side
    # sections tab as groups.
    cells[:] = order_rects_reading_order(
        cells,
        lambda cell: float(cell["x0"]),
        lambda cell: float(cell["y0"]),
        lambda cell: float(cell["x1"]),
        lambda cell: float(cell["y1"]),
    )
    # The gate binds the reviewed active-owner registry to the exact order of
    # the current layout cell stream.  Legacy subjects are discovered in
    # legacy-bbox order, but a repaired lattice can split/reuse those subjects
    # such that that order no longer matches the emitted cells.  Keep retained
    # subjects deterministic after the active owners without changing their
    # identity or topology evidence.
    current_cell_order = {
        str(cell["id"]): index for index, cell in enumerate(cells)
    }
    subject_ledger.sort(key=lambda subject: (
        0 if subject.get("cell_id") in current_cell_order else 1,
        current_cell_order.get(str(subject.get("cell_id")), len(cells)),
        subject["legacy_bbox"] is None,
        subject["legacy_bbox"] or (),
        subject["subject_key"],
    ))
    inference_ledger.sort(key=lambda inference: (
        inference["bbox"], inference["subject_key"]))
    return cells, unassigned, subject_ledger, inference_ledger


def resolve_retained_partition_overlaps(
        subject_ledger: list[dict[str, Any]]) -> None:
    """A suppressed subject's partition mapping is a partition, not a cover.

    A `painted-edge-partition` subject records which of today's cells its area
    became, and `audit.validate_comb_owner_registry` requires each such cell to
    be claimed exactly once: a cell owned by two suppressed subjects is not a
    partition of either, and the audit invalidates the whole form's registry
    rather than guess.  Nothing enforced that here, because until a legacy comb
    subject nested inside another one lost its rectangular owner too, no two
    partition subjects ever overlapped.  2550M page 1 now has exactly that --
    `p1c7` (66.00, 118.80, 99.84, 134.40) sits wholly inside the row band
    `p1c6` (28.80, 117.12, 582.72, 136.32), and both enumerated `p1c116`,
    `p1c122` and `p1c123`.

    The contested cell goes to the SMALLEST claiming area, which is the most
    specific claim on it: those three cells are what `p1c7` became, and `p1c6`
    still records the nineteen that are its own.  Ties cannot occur between
    distinct subjects with the same bbox -- the registry rejects duplicate
    identities before it gets here -- but the order is fully stated anyway so
    the ledger cannot depend on dict insertion.  A subject is never emptied:
    that would be a subject whose every cell lies inside strictly smaller
    subjects, and the audit refuses an empty mapping, so it must fail closed
    rather than be papered over here.  Measured over the 53-form corpus at r20:
    3 cells contested, on one page of one form, and no mapping emptied.
    """
    claims: dict[str, list[int]] = {}
    for index, subject in enumerate(subject_ledger):
        if subject.get("state") != "retained_unresolved":
            continue
        if "painted-edge-partition" not in (subject.get("reason_codes") or ()):
            continue
        for cell_id in subject.get("mapped_partition_cell_ids") or ():
            claims.setdefault(str(cell_id), []).append(index)

    def specificity(index: int) -> tuple[float, tuple[float, ...], str]:
        subject = subject_ledger[index]
        x0, y0, x1, y1 = (float(value) for value in subject["legacy_bbox"])
        return ((x1 - x0) * (y1 - y0),
                tuple(float(value) for value in subject["legacy_bbox"]),
                str(subject["subject_key"]))

    drop: dict[int, set[str]] = {}
    for cell_id, owners in claims.items():
        if len(owners) < 2:
            continue
        keeper = min(owners, key=specificity)
        for index in owners:
            if index != keeper:
                drop.setdefault(index, set()).add(cell_id)
    for index, dropped in drop.items():
        subject = subject_ledger[index]
        kept = [(cell_id, key) for cell_id, key
                in zip(subject["mapped_partition_cell_ids"],
                       subject["mapped_partition_subject_keys"])
                if str(cell_id) not in dropped]
        if not kept:
            continue
        subject["mapped_partition_cell_ids"] = [cell_id for cell_id, _ in kept]
        subject["mapped_partition_subject_keys"] = [key for _, key in kept]


def glyph_ink_spans(run: dict[str, Any]) -> list[Interval]:
    """The x extents the run's non-blank characters actually mark.

    A text run's bounding box is its *advance*, not its ink: the run
    "Calendar        Fiscal" reserves the paper between the two words, and a
    caption laid out that way with a checkbox drawn in the gap owns none of the
    paper the box encloses. Every run in the corpus carries per-character
    origins and advances, so the marked spans are read off the source rather
    than estimated.
    """
    origin = float(run.get("origin_x", run["x0"]))
    offsets = run.get("char_origin_offsets_pt") or ()
    advances = run.get("char_advances_pt") or ()
    text = run.get("text") or ""
    if len(offsets) != len(text) or len(advances) != len(text):
        return [(float(run["x0"]), float(run["x1"]))]
    marked = [(origin + float(o), origin + float(o) + float(a))
              for ch, o, a in zip(text, offsets, advances) if ch.strip()]
    if not marked:
        return []
    return union_intervals(marked)


def assign_points(cells: Sequence[dict[str, Any]],
                  points: Sequence[tuple[float, float, Any]],
                  ink: Sequence[Sequence[Interval]] | None = None
                  ) -> tuple[list[list[Any]], list[Any]]:
    """Give each point to exactly one cell -- the smallest one containing it.

    Cells partition the lattice, so containment is normally unambiguous. It is
    not for the handful of L-shaped merged cells, whose emitted bounding box
    necessarily overlaps a neighbour; without the smallest-area rule those
    overlaps double-count, which is how a page reported more comb slots than it
    had dividers. Area then reading order makes the choice deterministic.

    `ink` states, per point, where the thing being placed actually marks the
    paper. The point stays the anchor -- a run whose home cell holds any of its
    ink does not move -- but a home cell holding NONE of it is not the run's
    cell at all, only the cell its advance happens to be centred over. The run
    then goes to the cell carrying the most of its ink on that line. Nine of the
    fourteen `printed_box_peers_all_fillable` offenders are this: a two-word
    caption ("Calendar    Fiscal", "Yes      No", " 2nd      3rd") whose gap is
    exactly where the source drew the box to be ticked, so the box counted as
    printed text and was classified `label` -- unfillable -- on ink it does not
    contain. Without `ink` the placement is unchanged.
    """
    order = sorted(range(len(cells)),
                   key=lambda n: ((cells[n]["x1"] - cells[n]["x0"])
                                  * (cells[n]["y1"] - cells[n]["y0"]),
                                  cells[n]["y0"], cells[n]["x0"]))
    buckets: list[list[Any]] = [[] for _ in cells]
    unplaced: list[Any] = []
    for index, (cx, cy, payload) in enumerate(points):
        spans = list(ink[index]) if ink is not None else []
        home = next((n for n in order
                     if cells[n]["x0"] <= cx <= cells[n]["x1"]
                     and cells[n]["y0"] <= cy <= cells[n]["y1"]), None)
        if home is None:
            unplaced.append(payload)
            continue
        if spans and overlap_length([(cells[home]["x0"], cells[home]["x1"])],
                                    spans) <= 0:
            best = None
            for n in order:
                cell = cells[n]
                if not (cell["y0"] <= cy <= cell["y1"]):
                    continue
                held = overlap_length([(cell["x0"], cell["x1"])], spans)
                if held <= 0:
                    continue
                key = (-held, (cell["x1"] - cell["x0"]) * (cell["y1"] - cell["y0"]),
                       cell["y0"], cell["x0"], n)
                if best is None or key < best[0]:
                    best = (key, n)
            if best is not None:
                home = best[1]
        buckets[home].append(payload)
    return buckets, unplaced


# ---------------------------------------------------------------------------
# Growable bands
# ---------------------------------------------------------------------------


def row_signature(v_at: list[list[bool]], row: int, columns: int) -> tuple[int, ...]:
    return tuple(i for i in range(columns) if v_at[i][row])


def column_role(texts: Sequence[str]) -> str | None:
    """How one column of a candidate band varies down the rows.

    "constant" -- every row carries the same pre-printed text (or none): the
    money decimal point, the "%" glyph, an empty comb.
    "enumerated" -- the rows carry consecutive integers: the pre-printed row
    numbers "1".."6" of Schedule 1.
    None -- the rows carry different prose, which means these are distinct
    numbered items that merely happen to be drawn on a regular pitch. Part II
    of the 2551Q (items 15-19) is exactly that shape and must NOT be growable.
    """
    stripped = [t.strip() for t in texts]
    if len(set(stripped)) == 1:
        return "constant"
    try:
        numbers = [int(t) for t in stripped]
    except ValueError:
        return None
    if all(b - a == 1 for a, b in zip(numbers, numbers[1:])):
        return "enumerated"
    return None


def uniform_pitch_subruns(run: Sequence[int], yl: Lattice) -> list[list[int]]:
    """Maximal contiguous sub-runs whose pitch stays within PITCH_TOL_PT.

    A header row sharing a column signature with the data rows below it must
    not poison the band: 2550M Schedule 1 is four equal data rows under a
    taller header, and rejecting the whole signature-run left the first data
    row outside the growable.
    """
    if len(run) < MIN_GROWABLE_ROWS:
        return []
    edges = [yl.positions[j] for j in (*run, run[-1] + 1)]
    deltas = [q(b - a) for a, b in zip(edges, edges[1:])]
    if max(deltas) - min(deltas) <= PITCH_TOL_PT:
        return [list(run)]
    subruns: list[list[int]] = []
    start = 0
    n = len(run)
    while start < n:
        end = start + 1
        while (end < n
               and max(deltas[start:end + 1]) - min(deltas[start:end + 1])
               <= PITCH_TOL_PT):
            end += 1
        if end - start >= MIN_GROWABLE_ROWS:
            subruns.append(list(run[start:end]))
            start = end
        else:
            start += 1
    return subruns


def detect_growables(page_index: int, xl: Lattice, yl: Lattice,
                     v_at: list[list[bool]], h_at: list[list[bool]],
                     cells: Sequence[dict[str, Any]],
                     text_runs: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Maximal runs of >=3 consecutive rows that are genuinely interchangeable.

    A repeating row band is the only place on a BIR form where a filer may need
    more space than the sheet gives, so this is what a generator has to be able
    to grow -- and what the on-sheet capacity is measured against.

    Identical geometry alone is not enough. Several fixed sections are drawn on
    a perfectly regular pitch with a perfectly regular column structure and are
    still not repeatable, because each row carries its own pre-printed caption.
    The content test below is what separates the two.
    """
    ny = len(yl) - 1
    signatures = [row_signature(v_at, j, len(xl)) for j in range(ny)]
    run_text = {f"p{page_index}t{i}": r["text"] for i, r in enumerate(text_runs)}
    by_position = {(c["row"], c["col"]): c for c in cells}

    growables: list[dict[str, Any]] = []
    start = 0
    while start < ny:
        signature = signatures[start]
        end = start + 1
        while end < ny and signatures[end] == signature:
            end += 1
        run = list(range(start, end))
        start = end

        if len(run) < MIN_GROWABLE_ROWS or len(signature) < MIN_GROWABLE_COLUMNS:
            continue
        i0, i1 = signature[0], signature[-1]

        for sub in uniform_pitch_subruns(run, yl):
            # Every row must be closed top and bottom across the band's width,
            # otherwise this is a column of free space, not a stack of rows.
            if not all(h_at[j][i] for j in (*sub, sub[-1] + 1)
                       for i in range(i0, i1)):
                continue

            edges = [yl.positions[j] for j in (*sub, sub[-1] + 1)]
            deltas = [q(b - a) for a, b in zip(edges, edges[1:])]

            roles: dict[int, str] = {}
            for column in signature[:-1]:
                column_cells = [by_position.get((j, column)) for j in sub]
                if any(c is None for c in column_cells):
                    roles = {}
                    break
                texts = ["".join(run_text[t] for t in c["text_run_ids"])
                         for c in column_cells]
                role = column_role(texts)
                if role is None:
                    roles = {}
                    break
                roles[column] = role
            if not roles:
                continue

            band_x0, band_x1 = xl.positions[i0], xl.positions[i1]
            band_y0, band_y1 = edges[0], edges[-1]
            in_band = [c for c in cells
                       if c["x0"] >= band_x0 - CLUSTER_TOL_PT
                       and c["x1"] <= band_x1 + CLUSTER_TOL_PT
                       and c["y0"] >= band_y0 - CLUSTER_TOL_PT
                       and c["y1"] <= band_y1 + CLUSTER_TOL_PT]
            template = [c["id"] for c in in_band
                        if abs(c["y0"] - edges[0]) <= CLUSTER_TOL_PT]
            header = [c["id"] for c in cells
                      if abs(c["y1"] - band_y0) <= CLUSTER_TOL_PT
                      and c["x0"] >= band_x0 - CLUSTER_TOL_PT
                      and c["x1"] <= band_x1 + CLUSTER_TOL_PT]

            growables.append({
                "id": f"p{page_index}g{len(growables)}",
                "kind": "repeating_rows",
                "x0": band_x0, "y0": band_y0, "x1": band_x1, "y1": band_y1,
                # Modal pitch. The band is NOT perfectly regular -- on the 2551Q
                # Schedule 1 rows 1-5 are 18.24pt and row 6 is 18.27pt -- so a
                # generator must use row_y, not index * pitch.
                "row_pitch_pt": min(collections.Counter(deltas).most_common(),
                                    key=lambda kv: (-kv[1], kv[0]))[0],
                "row_pitch_min_pt": min(deltas),
                "row_pitch_max_pt": max(deltas),
                "row_count": len(sub),
                "row_y": edges,
                "column_x": [xl.positions[i] for i in signature],
                "column_index": list(signature),
                "column_roles": [roles[i] for i in signature[:-1]],
                "header_cell_ids": sorted(header),
                "template_cell_ids": sorted(template),
                "cell_ids": sorted(c["id"] for c in in_band),
                # On-sheet capacity. Overflow beyond this is a continuation sheet,
                # not a taller table.
                "capacity": len(sub),
            })
    return growables


# ---------------------------------------------------------------------------
# Regions
# ---------------------------------------------------------------------------


def detect_regions(page_index: int, xl: Lattice, yl: Lattice, v_at: list[list[bool]],
                   cells: Sequence[dict[str, Any]],
                   growables: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Maximal runs of rows sharing one left/right enclosure.

    This recovers the boxes a reader sees as units -- a titled schedule, a
    Part -- because a BIR box keeps the same outer verticals for its whole
    height and the gap before the next box carries none.
    """
    ny = len(yl) - 1
    extents: list[tuple[int, int] | None] = []
    for j in range(ny):
        signature = row_signature(v_at, j, len(xl))
        extents.append((signature[0], signature[-1]) if len(signature) >= 2 else None)

    regions: list[dict[str, Any]] = []
    start = 0
    while start < ny:
        extent = extents[start]
        end = start + 1
        while end < ny and extents[end] == extent:
            end += 1
        run_start, run_end, start = start, end, end
        if extent is None:
            continue

        x0, x1 = xl.positions[extent[0]], xl.positions[extent[1]]
        y0, y1 = yl.positions[run_start], yl.positions[run_end]
        in_region = [c for c in cells
                     if c["y0"] >= y0 - CLUSTER_TOL_PT and c["y1"] <= y1 + CLUSTER_TOL_PT
                     and c["x0"] >= x0 - CLUSTER_TOL_PT and c["x1"] <= x1 + CLUSTER_TOL_PT]
        if not in_region:
            continue
        holds_growable = any(g["y0"] >= y0 - CLUSTER_TOL_PT and g["y1"] <= y1 + CLUSTER_TOL_PT
                             for g in growables)
        kind = "table" if holds_growable else ("band" if run_end - run_start == 1 else "block")
        regions.append({
            "id": f"p{page_index}r{len(regions)}",
            "kind": kind,
            "x0": x0, "y0": y0, "x1": x1, "y1": y1,
            "row_count": run_end - run_start,
            "cell_ids": sorted(c["id"] for c in in_region),
        })
    return regions


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------


def build_page(page: dict[str, Any],
               fillable_metrics: dict[str, float] | None = None,
               ) -> dict[str, Any]:
    index = page["index"]
    rules = page["rules"]
    final_paint = FinalPaint([*rules, *page["area_fills"], *page["paths"]])
    raw_structural = [rule for rule in rules if rule["role"] == "structural"]
    raw_horizontals = sorted(
        (rule for rule in raw_structural if rule["axis"] == "h"),
        key=lambda rule: (rule["y0"], rule["x0"]))
    raw_verticals = sorted(
        (rule for rule in raw_structural if rule["axis"] == "v"),
        key=lambda rule: (rule["x0"], rule["y0"]))
    raw_dividers, _raw_borders = split_verticals(
        raw_verticals, raw_horizontals)
    raw_extra_ink = comb_boundary_candidates(
        raw_verticals, page["area_fills"])
    # Painted walls join the x lattice on both counts: `defining`, because the
    # wall is what draws the boundary, and `all_ink`, because the same wall is
    # that boundary's coverage and weight. The raw/legacy stream takes them
    # unfiltered, exactly as `raw_extra_ink` above takes its fills unfiltered.
    raw_walls = wall_boundaries(page["area_fills"])
    raw_v_walls, _raw_h_walls = split_wall_axes(raw_walls)
    raw_xl = build_lattice(
        [*_raw_borders, *raw_v_walls], [*raw_verticals, *raw_v_walls], "v")
    # Horizontal walls belong on the current y-lattice (Schedule 1's first
    # data row has no other top rail). They must not join the continuity
    # lattice: extra y-lines remumber every later legacy_index, and reviewed
    # comb transitions bind (slug, page, p1cN) plus that cell's subject_key.
    raw_yl = build_lattice(raw_horizontals, raw_horizontals, "h")
    # F201 / P1b: the legacy view is bridged on exactly the same evidence as
    # the current one. A rail the source always drew, interrupted only by a
    # white bite strictly inside it, is one wall in both readings -- so the
    # healed boxes can register comb subjects through the legacy discovery
    # flow instead of emitting free-text regions.
    legacy_knockout_v = [r for r in page["rules"] if r.get("axis") == "v"
                         and tone_role(r.get("gray")) == "knockout"]
    legacy_knockout_h = [r for r in page["rules"] if r.get("axis") == "h"
                         and tone_role(r.get("gray")) == "knockout"]
    legacy_bite_bound = (0.0 if fillable_metrics is None
                         else float(fillable_metrics["glyph_height_pt"]))
    bridge_knockout_bites(raw_xl, legacy_knockout_v, "v", legacy_bite_bound)
    bridge_knockout_bites(raw_yl, legacy_knockout_h, "h", legacy_bite_bound)
    if len(raw_xl) >= 2 and len(raw_yl) >= 2:
        raw_dsu, raw_v_at, raw_h_at = merge_grid(raw_xl, raw_yl)
    else:
        raw_dsu = DisjointSet(1)
        raw_v_at = raw_h_at = []
    proven_structural = [
        rule for rule in rules
        if rule["role"] == "structural"
        and final_paint.structural_across_axis(
            rule,
            float(rule["y0"] if rule["axis"] == "v" else rule["x0"]),
            float(rule["y1"] if rule["axis"] == "v" else rule["x1"]),
            str(rule["axis"]),
        )
    ]
    proven_ids = {str(rule.get("id")) for rule in proven_structural}
    uncertain_structural = [
        rule for rule in raw_structural
        if str(rule.get("id")) not in proven_ids
        and not final_paint.definitely_erased(rule)
    ]
    exact_erased_ids = {
        str(rule.get("id"))
        for rule in raw_structural
        if "paint_spans" in rule and final_paint.definitely_erased(rule)
    }
    surviving_ids = {
        str(rule.get("id"))
        for rule in [*proven_structural, *uncertain_structural]
    }
    # If one bar of a fused composite boundary survives, retain the complete
    # measured stack so its published centre/IDs do not shift. Exact contributor
    # provenance can prove that one companion was wholly erased; that companion
    # remains continuity geometry but no longer contaminates the boundary's
    # final-paint uncertainty when a surviving mate defines the fused line.
    companion_ids: set[str] = set()
    for defining, all_ink, axis in (
        (_raw_borders, raw_verticals, "v"),
        (raw_horizontals, raw_horizontals, "h"),
    ):
        groups = [
            GroupGeometry(group, all_ink, axis)
            for group in cluster_collinear(defining)
        ]
        for boundary in fuse_boundaries(groups):
            boundary_ids = {
                str(rule.get("id"))
                for group in boundary
                for rule in group.rules
            }
            if boundary_ids & surviving_ids:
                companion_ids.update(boundary_ids)
    geometry_ids = surviving_ids | companion_ids
    geometry_structural = [
        rule for rule in raw_structural
        if str(rule.get("id")) in geometry_ids
    ]
    uncertain_ids = (
        geometry_ids - proven_ids - (exact_erased_ids & companion_ids))
    # Grey ornament. It must be painted, but never as a black border -- the
    # raster-era mistake this project has already paid for once.
    decorative = [r for r in rules if r["role"] == "decorative"]

    horizontals = sorted(
        (rule for rule in proven_structural if rule["axis"] == "h"),
        key=lambda rule: (rule["y0"], rule["x0"]))
    verticals = sorted(
        (rule for rule in proven_structural if rule["axis"] == "v"),
        key=lambda rule: (rule["x0"], rule["y0"]))
    geometry_horizontals = sorted(
        (rule for rule in geometry_structural if rule["axis"] == "h"),
        key=lambda rule: (rule["y0"], rule["x0"]))
    geometry_verticals = sorted(
        (rule for rule in geometry_structural if rule["axis"] == "v"),
        key=lambda rule: (rule["x0"], rule["y0"]))
    old_dividers, old_proven_borders = split_verticals(
        verticals, horizontals)
    corridor_dividers, corridor_proven_borders = (
        split_final_vertical_corridors(verticals, horizontals))
    # Certification is about local source ownership, not whether another rule
    # already defines the same x.  Existing positions still need their old hull
    # removed from coverage; only position creation is suppressed below.
    localized_corridor_ids = corridor_border_promotions(
        old_dividers, old_proven_borders, corridor_proven_borders,
        page["text_runs"])
    # The raw/legacy stream below retains the reviewed full hull. Current comb
    # ownership uses only local lower-baseline fragments, plus a dense fragment
    # clipped to its independently repeated comb band.
    dividers = localized_comb_dividers(
        old_dividers, corridor_dividers, localized_corridor_ids)

    old_geometry_dividers, old_geometry_borders = split_verticals(
        geometry_verticals, horizontals)
    corridor_geometry_dividers, corridor_geometry_borders = (
        split_final_vertical_corridors(
            geometry_verticals, horizontals, proven_ids))
    _geometry_dividers = localized_comb_dividers(
        old_geometry_dividers, corridor_geometry_dividers,
        localized_corridor_ids)

    old_support_dividers, old_unsupported_verticals = split_verticals(
        raw_verticals, geometry_horizontals)
    corridor_support_dividers, _corridor_unsupported_verticals = (
        split_final_vertical_corridors(
            raw_verticals, geometry_horizontals, proven_ids))
    support_dividers = localized_comb_dividers(
        old_support_dividers, corridor_support_dividers,
        localized_corridor_ids)
    _unsupported_verticals = old_unsupported_verticals
    final_supported_divider_ids = {
        str(divider.get("id")) for divider in support_dividers
    }
    final_area_fills = [
        fill for fill in page["area_fills"]
        if fill["role"] == "structural"
        and final_paint.structural_across_axis(
            fill, float(fill["y0"]), float(fill["y1"]), "v")
    ]
    extra_ink = comb_boundary_candidates(verticals, final_area_fills)
    # The same visibility test `final_area_fills` applies, but asked on the
    # wall's own axis rather than a hard-coded "v": a wall that a later
    # knockout paints over must not define a column or a row.
    final_walls = [
        wall for wall in raw_walls
        if final_paint.structural_across_axis(
            wall, *wall_run(wall), str(wall["axis"]))
    ]
    final_v_walls, final_h_walls = split_wall_axes(final_walls)
    final_h_walls = [
        wall for wall in final_h_walls
        if not h_wall_would_fuse(wall, geometry_horizontals)
    ]

    # Once a composite vertical has been partitioned into paper corridors, a
    # character tick in one row must not become lattice coverage merely because
    # the same source merge is a table border in another row. Replace only
    # those decomposed composites with their border fragments. Direct rules
    # retain the established all-ink coverage: a thin direct segment can be the
    # continuation of a heavier column boundary already defining this x. The
    # raw lattice above intentionally keeps every old hull for subject
    # continuity.
    localized_border_fragments = [
        rule for rule in corridor_geometry_borders
        if str(rule.get("id")) in localized_corridor_ids
        and rule.get("_corridor_role") == "border"
        and not dense_comb_corridor(rule, old_dividers)
    ]
    border_coverage = [
        rule for rule in geometry_verticals
        if str(rule.get("id")) not in localized_corridor_ids
    ] + localized_border_fragments
    old_border_centres = [centre(rule) for rule in old_geometry_borders]
    position_promoted_ids = {
        source_id for source_id in localized_corridor_ids
        if any(
            str(fragment.get("id")) == source_id
            and not any(abs(centre(fragment) - old_x)
                        <= CLUSTER_TOL_PT
                        for old_x in old_border_centres)
            for fragment in localized_border_fragments)
    }
    # Existing border members remain the only defining witnesses at their x.
    # A genuinely missing position receives one fragment, irrespective of how
    # many row corridors that source supplied.
    border_defining: list[dict[str, Any]] = list(old_geometry_borders)
    seen_promoted: set[str] = set()
    for rule in localized_border_fragments:
        rule_id = str(rule.get("id"))
        if rule_id not in position_promoted_ids or rule_id in seen_promoted:
            continue
        seen_promoted.add(rule_id)
        border_defining.append(rule)
    border_defining.sort(key=lambda rule: (
        centre(rule), float(rule["y0"]), float(rule["y1"]),
        str(rule.get("id"))))
    # `borders` stays rules-only: it is the published border inventory and its
    # members are counted by source id, which a painted wall does not carry.
    borders = border_defining
    xl = build_lattice(
        [*border_defining, *final_v_walls], [*border_coverage, *final_v_walls], "v")
    yl = build_lattice(
        [*geometry_horizontals, *final_h_walls],
        [*geometry_horizontals, *final_h_walls], "h")

    # A knockout strictly interior to one rail (F097) leaks a comb wall into
    # a blank sliver; rejoin those spans before the cell walk sees the gap.
    # Bounded by this form's own smallest fillable glyph height so a real
    # doorway is never bridged (see bridge_knockout_bites's docstring).
    knockout_v = [r for r in page["rules"] if r.get("axis") == "v"
                  and tone_role(r.get("gray")) == "knockout"]
    knockout_h = [r for r in page["rules"] if r.get("axis") == "h"
                  and tone_role(r.get("gray")) == "knockout"]
    bite_bound = (0.0 if fillable_metrics is None
                  else float(fillable_metrics["glyph_height_pt"]))
    bridge_knockout_bites(xl, knockout_v, "v", bite_bound)
    bridge_knockout_bites(yl, knockout_h, "h", bite_bound)

    if len(xl) < 2 or len(yl) < 2:
        cells: list[dict[str, Any]] = []
        unassigned = [f"p{index}t{i}" for i in range(len(page["text_runs"]))]
        growables: list[dict[str, Any]] = []
        regions: list[dict[str, Any]] = []
        comb_subjects: list[dict[str, Any]] = []
        comb_inferences: list[dict[str, Any]] = []
        v_at = h_at = []
    else:
        dsu, v_at, h_at = merge_grid(xl, yl)
        cells, unassigned, comb_subjects, comb_inferences = build_cells(
            index, xl, yl, dsu, v_at, h_at, geometry_verticals,
            [*geometry_horizontals, *final_h_walls], dividers, extra_ink, final_paint,
            page["text_runs"],
            legacy_dividers=raw_dividers,
            legacy_extra_ink=raw_extra_ink,
            final_supported_divider_ids=final_supported_divider_ids,
            frame_dividers=support_dividers,
            legacy_xl=raw_xl,
            legacy_yl=raw_yl,
            legacy_dsu=raw_dsu,
            legacy_v_at=raw_v_at,
            legacy_h_at=raw_h_at,
            legacy_v_ink=raw_verticals,
            legacy_h_ink=raw_horizontals,
            uncertain_geometry_ids=uncertain_ids,
            fillable_metrics=fillable_metrics,
            area_fills=page["area_fills"])
        growables = detect_growables(index, xl, yl, v_at, h_at, cells, page["text_runs"])
        regions = detect_regions(index, xl, yl, v_at, cells, growables)
        partition_candidates = partition_ink(page)
        for cell in cells:
            partitions = printed_partitions(
                cell, partition_candidates, final_paint)
            if partitions:
                cell["printed_partitions"] = partitions

    comb_cells = [c for c in cells if "comb" in c]
    return {
        "index": index,
        "width_pt": page["width_pt"],
        "height_pt": page["height_pt"],
        "rotation": page["rotation"],
        "x_lattice": xl.positions,
        "y_lattice": yl.positions,
        "cells": cells,
        "comb_subjects": comb_subjects,
        "comb_inferences": comb_inferences,
        "regions": regions,
        "growable": growables,
        "decorative_rules": decorative,
        # Support classification is final-visible even when the divider's own
        # merged paint range is uncertain. Keep the established ID inventory;
        # the fully final-visible subset is explicit beside it.
        "comb_divider_ids": list(dict.fromkeys(
            d["id"] for d in support_dividers)),
        "comb_divider_final_visible_ids": list(dict.fromkeys(
            d["id"] for d in dividers)),
        "unassigned_text_run_ids": unassigned,
        "stats": {
            "x_lattice": len(xl),
            "y_lattice": len(yl),
            "cells": len(cells),
            "cells_non_rectangular": sum(1 for c in cells if not c["rectangular"]),
            "cells_geometry_unresolved": sum(
                bool(c.get("geometry_resolution")) for c in cells),
            "regions": len(regions),
            "growables": len(growables),
            "comb_cells": len(comb_cells),
            "comb_subjects": len(comb_subjects),
            "comb_subjects_active": sum(
                subject["state"].startswith("active_")
                for subject in comb_subjects),
            "comb_subjects_active_resolved": sum(
                subject["state"] == "active_resolved"
                for subject in comb_subjects),
            "comb_subjects_active_unresolved": sum(
                subject["state"] == "active_unresolved"
                for subject in comb_subjects),
            "comb_subjects_retained_unresolved": sum(
                subject["state"] == "retained_unresolved"
                for subject in comb_subjects),
            "comb_subjects_retired": 0,
            "comb_subjects_blocking": sum(
                bool(subject.get("blocks_gate"))
                for subject in comb_subjects),
            "comb_inferences_suppressed": len(comb_inferences),
            "comb_inferences_blocking": sum(
                bool(inference.get("blocks_gate"))
                for inference in comb_inferences),
            "comb_evidence_blocking": (
                sum(bool(subject.get("blocks_gate"))
                    for subject in comb_subjects)
                + sum(bool(inference.get("blocks_gate"))
                      for inference in comb_inferences)
            ),
            "comb_slots": sum(c["comb"]["cells"] for c in comb_cells),
            "comb_dividers": len({d["id"] for d in support_dividers}),
            "comb_dividers_final_visible": len({d["id"] for d in dividers}),
            "border_verticals": len({d["id"] for d in borders}),
            "decorative_rules": len(decorative),
            "text_runs": len(page["text_runs"]),
            "text_runs_unassigned": len(unassigned),
            "cell_kinds": dict(sorted(collections.Counter(c["kind"] for c in cells).items())),
        },
    }


def _load_review_registry():
    """Load the reviewed-ledger registries by explicit pinned path.

    Same isolation-proof pattern as comb_referee.py: the module beside this
    file is the only trusted source, importable identically at a shell and
    under an isolated interpreter.
    """
    import importlib.util
    path = pathlib.Path(__file__).resolve().parent / "review_registry.py"
    spec = importlib.util.spec_from_file_location("review_registry", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


review_registry = _load_review_registry()


def apply_reviewed_transitions(
        layout: dict[str, Any],
        transitions: dict[tuple[str, int, str], dict[str, Any]] | None = None,
        ) -> dict[str, Any]:
    """Apply user-reviewed retained-subject transitions to a built layout.

    The producer half of review_registry's doctrine: an entry is consumed
    here and published as `active_composite` WITH a certificate naming it;
    comb_referee.py independently validates that certificate against the
    registry and against its own source corroboration.  Everything about
    this pass fails closed:

      * a registry whose shape validation reports any defect is refused
        whole -- a partially trusted registry is not a registry;
      * an entry must bind the exact subject (subject_key) on the exact
        source bytes (source_sha256) with a transition the subject itself
        permits, or the build errors rather than skipping it;
      * `retired_proven_false` is permitted BY THE SUBJECT but expected to
        stay unused; consuming one here is an error until a package
        deliberately implements retirement, because silently accepting it
        would drop a ledger subject with no machinery behind it;
      * an entry that matches no retained subject on its slug is stale and
        errors -- a review of bytes that no longer exist certifies nothing;
      * an entry pointing at a subject that is not retained is an error:
        resolutions of active subjects live in REVIEWED_LEDGER_RESOLUTIONS,
        never here.

    With the shipped empty registry this pass is a proven no-op: the layout
    returns byte-identical.
    """
    if transitions is None:
        transitions = review_registry.REVIEWED_LEDGER_TRANSITIONS
        shape_errors = review_registry.registry_errors()
    else:
        shape_errors = review_registry.registry_errors({}, transitions)
    if shape_errors:
        raise ValueError(
            "reviewed-transition registry is malformed: "
            + "; ".join(shape_errors[:3]))
    form = layout["form"]
    slug = f"{str(form['code']).lower()}-{form['revision']}"
    source_sha = layout["source"]["sha256"]
    # A form CODE plus revision does not identify a DOCUMENT: 1701's main
    # sheet, its attachment and its consolidation all publish
    # {"code": "1701", "revision": "2018"} and collapse to one slug.  What
    # does identify the region is the SUBJECT KEY, which carries the
    # rectangle's own coordinates -- so an entry belongs to this document
    # only when a subject here answers to both its key and its subject_key.
    # Anything else is a sibling document's entry and is passed over in
    # silence; a mismatch on the SAME subject is still an error, so a
    # re-pinned PDF cannot quietly drop its reviewed decisions.
    mine: set[tuple[str, int, str]] = set()
    applied: set[tuple[str, int, str]] = set()
    for page in layout["pages"]:
        page_index = int(page["index"])
        for subject in page.get("comb_subjects", ()):
            key = (slug, page_index, str(subject["legacy_cell_id"]))
            entry = transitions.get(key)
            if entry is None or entry["source_sha256"] != source_sha:
                # No entry, or one reviewed on DIFFERENT SOURCE BYTES -- a
                # sibling document sharing this slug (1701's attachment and
                # consolidation both publish code 1701 revision 2018, so all
                # three collapse to one slug).  Its entries are not ours.
                # Silent here by necessity: one layout cannot tell a sibling
                # from a re-pinned PDF.  That distinction is made at CORPUS
                # level, where every document is visible and the gate proves
                # each registry entry was applied exactly once.
                continue
            mine.add(key)
            if entry["subject_key"] != subject["subject_key"]:
                raise ValueError(
                    f"{key}: reviewed transition subject_key does not bind "
                    "this subject")
            if subject.get("state") != "retained_unresolved":
                raise ValueError(
                    f"{key}: a reviewed transition names a subject whose "
                    f"state is {subject.get('state')!r}, not retained")
            if entry["transition"] not in (
                    subject.get("permitted_transitions") or ()):
                raise ValueError(
                    f"{key}: transition {entry['transition']!r} is not "
                    "permitted by the subject")
            if entry["transition"] != "active_composite":
                raise ValueError(
                    f"{key}: transition {entry['transition']!r} has no "
                    "producer machinery; retirement is deliberately unbuilt")
            subject["state"] = "active_composite"
            subject["blocks_gate"] = False
            subject["transition_certificate"] = {
                "criterion": review_registry.TRANSITION_CRITERION,
                "registry_key": [slug, page_index,
                                 str(subject["legacy_cell_id"])],
                "transition": entry["transition"],
                "suppression_criterion": entry["suppression_criterion"],
                "reviewer": entry["reviewer"],
                "date": entry["date"],
            }
            applied.add(key)
    # An entry carrying THIS document's own source sha provably belongs to
    # this document, so if no subject answered to it, it is stale and that is
    # an error here.  Sibling entries (other sha) are excluded and are proven
    # applied at corpus level instead.
    stale = [key for key, entry in transitions.items()
             if key[0] == slug and entry["source_sha256"] == source_sha
             and key not in applied]
    if stale:
        raise ValueError(
            f"reviewed transitions match no retained subject: {stale[:3]}")
    # The pass that changes a state owns the summary of that state: the
    # page stats were computed while the page was built, before any decision
    # could apply, and a summary that disagrees with what it summarises is
    # simply wrong. Refreshing HERE rather than in the caller means no future
    # caller can forget to.
    return refresh_comb_subject_stats(layout)


def apply_reviewed_resolutions(
        layout: dict[str, Any],
        resolutions: dict[tuple[str, int, str], dict[str, Any]] | None = None,
        ) -> dict[str, Any]:
    """Apply user-reviewed resolutions of ACTIVE_UNRESOLVED subjects.

    The sibling of `apply_reviewed_transitions`, for the other designed review
    path.  A subject whose four independent measurements already agree is
    still not self-promoting: `eligible-for-reviewed-resolution` says only
    that a person may now look.  This consumes that person's decision and
    publishes `active_resolved` WITH a certificate, which comb_referee.py
    re-validates against the registry AND against its own current-run
    four-way evidence -- review cannot overrule the paper.

    A resolution touches TWO records, because the referee cross-checks them:
    the subject's state/reason_codes, and the owning cell's comb resolution
    (`status` and `reason_codes`), whose contract is that reasons are
    non-empty exactly when the status is `unresolved`.  The measured
    evidence already on the resolution record (endpoint topologies and the
    like) is KEPT: it is why the decision was reviewable, and erasing it
    would destroy the audit trail the review is supposed to create.

    Fail-closed on every edge, same doctrine as the transition path.
    """
    if resolutions is None:
        resolutions = review_registry.REVIEWED_LEDGER_RESOLUTIONS
        shape_errors = review_registry.registry_errors()
    else:
        shape_errors = review_registry.registry_errors(resolutions, {})
    if shape_errors:
        raise ValueError(
            "reviewed-resolution registry is malformed: "
            + "; ".join(shape_errors[:3]))
    form = layout["form"]
    slug = f"{str(form['code']).lower()}-{form['revision']}"
    source_sha = layout["source"]["sha256"]
    # Identified by SUBJECT KEY, not by slug -- see the same guard in
    # `apply_reviewed_transitions` for why a code+revision slug is not a
    # document identity.
    mine: set[tuple[str, int, str]] = set()
    applied: set[tuple[str, int, str]] = set()
    for page in layout["pages"]:
        page_index = int(page["index"])
        cells_by_id = {str(cell["id"]): cell for cell in page.get("cells", ())}
        for subject in page.get("comb_subjects", ()):
            cell_id = subject.get("cell_id")
            if cell_id is None:
                continue
            key = (slug, page_index, str(cell_id))
            entry = resolutions.get(key)
            if entry is None or entry["source_sha256"] != source_sha:
                # No entry, or one reviewed on DIFFERENT SOURCE BYTES -- a
                # sibling document sharing this slug (1701's attachment and
                # consolidation both publish code 1701 revision 2018, so all
                # three collapse to one slug).  Its entries are not ours.
                # Silent here by necessity: one layout cannot tell a sibling
                # from a re-pinned PDF.  That distinction is made at CORPUS
                # level, where every document is visible and the gate proves
                # each registry entry was applied exactly once.
                continue
            mine.add(key)
            if entry["subject_key"] != subject["subject_key"]:
                raise ValueError(
                    f"{key}: reviewed resolution subject_key does not bind "
                    "this subject")
            if subject.get("state") != "active_unresolved":
                raise ValueError(
                    f"{key}: a reviewed resolution names a subject whose "
                    f"state is {subject.get('state')!r}, not active_unresolved")
            cell = cells_by_id.get(str(cell_id))
            comb = (cell or {}).get("comb")
            resolution = (comb or {}).get("resolution")
            if not isinstance(resolution, dict):
                raise ValueError(
                    f"{key}: reviewed resolution names a cell with no comb "
                    "resolution record")
            if resolution.get("status") != "unresolved":
                raise ValueError(
                    f"{key}: the cell's comb is not unresolved")
            # The producer can only vouch for ITS OWN count; the other three
            # measurements are the referee's to confirm.  Checking this one
            # here stops a stale entry from riding a regenerated corpus.
            if int(entry["four_way"]["lattice"]) != int(comb["cells"]):
                raise ValueError(
                    f"{key}: reviewed four-way lattice count "
                    f"{entry['four_way']['lattice']} is not this comb's "
                    f"{comb['cells']}")
            certificate = {
                "criterion": review_registry.RESOLUTION_CRITERION,
                "registry_key": [slug, page_index, str(cell_id)],
                "four_way": {
                    name: int(entry["four_way"][name])
                    for name in ("lattice", "audit", "emitted", "referee")
                },
                "resolved_reason_codes": list(subject["reason_codes"]),
                "reviewer": entry["reviewer"],
                "date": entry["date"],
            }
            subject["state"] = "active_resolved"
            subject["blocks_gate"] = False
            subject["reason_codes"] = []
            subject["resolution_certificate"] = certificate
            resolution["status"] = "resolved"
            resolution["reason_codes"] = []
            resolution["review_certificate"] = certificate
            applied.add(key)
    stale = [key for key, entry in resolutions.items()
             if key[0] == slug and entry["source_sha256"] == source_sha
             and key not in applied]
    if stale:
        raise ValueError(
            f"reviewed resolutions match no eligible subject: {stale[:3]}")
    # The pass that changes a state owns the summary of that state: the
    # page stats were computed while the page was built, before any decision
    # could apply, and a summary that disagrees with what it summarises is
    # simply wrong. Refreshing HERE rather than in the caller means no future
    # caller can forget to.
    return refresh_comb_subject_stats(layout)


def refresh_comb_subject_stats(layout: dict[str, Any]) -> dict[str, Any]:
    """Recount the per-page subject stats after reviewed decisions land.

    `build_page` publishes its stats while it builds the page, which is
    BEFORE `apply_reviewed_transitions` and `apply_reviewed_resolutions` can
    change any state -- they operate on the assembled layout.  Every applied
    decision therefore left the page's own counters describing the ledger as
    it stood a moment earlier, and comb_referee.py, which re-derives those
    counters from the subjects themselves, refused 27 of 53 forms with
    "ledger stat ... is N, expected N+1".  The stats are not evidence in
    their own right -- they are a published summary of the subjects, and a
    summary that disagrees with what it summarises is simply wrong.

    Recounted from the final subjects, so the summary cannot drift from them
    again whatever future pass mutates a state.
    """
    for page in layout["pages"]:
        subjects = page.get("comb_subjects") or ()
        inferences = page.get("comb_inferences") or ()
        stats = page.get("stats")
        if not isinstance(stats, dict):
            continue
        resolved = sum(1 for s in subjects if s["state"] == "active_resolved")
        unresolved = sum(
            1 for s in subjects if s["state"] == "active_unresolved")
        composite = sum(1 for s in subjects if s["state"] == "active_composite")
        retained = sum(
            1 for s in subjects if s["state"] == "retained_unresolved")
        subject_blockers = sum(1 for s in subjects if s.get("blocks_gate"))
        inference_blockers = sum(
            1 for item in inferences if item.get("blocks_gate"))
        stats["comb_subjects_active"] = resolved + unresolved + composite
        stats["comb_subjects_active_resolved"] = resolved
        stats["comb_subjects_active_unresolved"] = unresolved
        stats["comb_subjects_retained_unresolved"] = retained
        stats["comb_subjects_blocking"] = subject_blockers
        stats["comb_evidence_blocking"] = subject_blockers + inference_blockers
    return layout


def build_layout(ir: dict[str, Any]) -> dict[str, Any]:
    fillable_metrics = min_fillable_line_metrics(ir)
    layout = {
        "schema_version": SCHEMA_VERSION,
        "form": ir["form"],
        "source": ir["source"],
        "generator": {
            "producer": "tools/formgen/lattice.py",
            "schema_version": SCHEMA_VERSION,
            "consumes_ir_schema_version": ir["schema_version"],
            "cluster_tolerance_pt": CLUSTER_TOL_PT,
            "pitch_tolerance_pt": PITCH_TOL_PT,
        },
        "paper": ir["paper"],
        "pages": [
            build_page(p, fillable_metrics) for p in ir["pages"]
        ],
    }
    return apply_reviewed_resolutions(apply_reviewed_transitions(layout))


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------


def self_test(ir_path: pathlib.Path) -> int:
    """Assert against the real 2551Q, not against a synthetic fixture."""
    ir = json.loads(ir_path.read_text(encoding="utf-8"))
    layout = build_layout(ir)
    failures: list[str] = []

    def check(condition: bool, message: str) -> None:
        if not condition:
            failures.append(message)

    # ---- DECISION A: the compartment rule ---------------------------------
    #
    # Census-pinned boundary cases, each the real geometry of the cell that
    # motivated it. The rule must cut every defect, split the merged row,
    # and keep the three widest LEGITIMATE combs in the corpus untouched.
    # Deleting the rule (or moving its bound) fails these directly, and the
    # constructor case below proves the band builders actually consult it.
    check(compartment_runs([0.0, 70.8, 227.52]) == [],
          "2551M p2c13's two table-cell halves are not a comb")
    check(compartment_runs([0.0, 68.64, 95.28]) == [],
          "1604CF p2c73's two table-cell halves are not a comb")
    check(compartment_runs(
        [0.0, 106.15, 205.38, 229.01, 254.23, 306.18]) == [],
          "0605's Details-of-Payment header is never a comb: three columns "
          "die by width and MM alone dies by the minimum-run clause")
    p1c25 = [0.0]
    for width in [13.8] * 31 + [72.75, 14.52, 14.52, 15.12, 15.48]:
        p1c25.append(p1c25[-1] + width)
    check(compartment_runs(p1c25) == [(0, 31), (32, 36)],
          "1604F p1c25 cuts at the ZIP label box into a 31-run and a 4-run")
    check(compartment_runs([0.0, 18.96, 42.72]) == [(0, 2)],
          "2551M p1c3, the widest legitimate compartment (23.76pt), is kept")
    check(compartment_runs([0.0, 21.12, 42.72]) == [(0, 2)],
          "2553 p1c6, the next widest legitimate comb, is kept")
    check(compartment_runs([0.0, 24.5, 49.0]) == [(0, 2)],
          "a compartment exactly AT the bound is kept -- the rule is "
          "strictly wider-than, so the bound itself is not a cliff edge")
    # The band builders consult the rule: a two-slab band whose compartments
    # are table-cell halves must not survive construction, and the same
    # geometry scaled down to character pitch must. Geometry mirrors 2551M
    # p2c13 -- one full-height divider inside a wide cell band -- built with
    # the same synthetic shapes the fixtures below use (defined here early
    # because this block runs first).
    def _rule_vertical(x: float, thickness: float, seq: int) -> dict[str, Any]:
        return {
            "axis": "v",
            "x0": q(x - thickness / 2), "x1": q(x + thickness / 2),
            "y0": 0.0, "y1": 12.0,
            "thickness_pt": q(thickness),
            "gray": 0.0, "role": "structural",
            "paint_seq": seq, "paint_seq_max": seq,
        }
    rule_divider = _rule_vertical(70.8, 0.96, 10)
    rule_paint = FinalPaint([rule_divider])
    refused_bands = comb_bands(
        [rule_divider], [rule_divider], 0.0, 227.52, (0.96, 0.96),
        rule_paint)
    check(refused_bands == [],
          "comb_bands refuses the table-cell band whole -- no run of "
          "character boxes survives the compartment rule")
    kept_divider = _rule_vertical(14.4, 0.96, 10)
    kept_paint = FinalPaint([kept_divider])
    kept_bands = comb_bands(
        [kept_divider], [kept_divider], 0.0, 28.8, (0.96, 0.96), kept_paint)
    check(bool(kept_bands)
          and kept_bands[0]["cells"] == 2
          and kept_bands[0]["compartment_runs"] == [[0, 2]],
          "the identical band at character pitch survives and publishes "
          "its identity run")

    # ---- C3-A: reviewed retained-subject transitions -----------------------
    #
    # The producer half of review_registry's doctrine, proven on a synthetic
    # one-subject ledger: a valid entry publishes `active_composite` with the
    # exact certificate, and every guard that keeps this fail-closed is shown
    # able to fire.  The shipped registry is empty, so the corpus pass is a
    # no-op -- asserted against the real layout built above.
    def c3_layout():
        return {
            "form": {"code": "9999X", "revision": "2099"},
            "source": {"sha256": "ab" * 32},
            "pages": [{
                "index": 1,
                "comb_subjects": [{
                    "subject_key": "p1@9,9",
                    "legacy_cell_id": "p1c9",
                    "state": "retained_unresolved",
                    "blocks_gate": True,
                    "permitted_transitions": [
                        "active_composite", "retired_proven_false"],
                }],
            }],
        }

    def c3_entry(**overrides):
        entry = {
            "subject_key": "p1@9,9",
            "source_sha256": "ab" * 32,
            "transition": "active_composite",
            "suppression_criterion": (
                "source-partition-edge-in-final-picture-v1"),
            "reviewer": "self-test", "date": "2026-08-15",
            "citation": "self-test",
        }
        entry.update(overrides)
        return {("9999x-2099", 1, "p1c9"): entry}

    applied = apply_reviewed_transitions(c3_layout(), c3_entry())
    c3_subject = applied["pages"][0]["comb_subjects"][0]
    check(c3_subject["state"] == "active_composite"
          and c3_subject["blocks_gate"] is False
          and c3_subject["transition_certificate"] == {
              "criterion": "reviewed-ledger-transition-v1",
              "registry_key": ["9999x-2099", 1, "p1c9"],
              "transition": "active_composite",
              "suppression_criterion": (
                  "source-partition-edge-in-final-picture-v1"),
              "reviewer": "self-test", "date": "2026-08-15",
          }, "C3: a reviewed transition publishes the exact certificate")
    check(apply_reviewed_transitions(c3_layout(), {})["pages"][0]
          ["comb_subjects"][0]["state"] == "retained_unresolved",
          "C3: no entry, no transition")

    def c3_refused(transitions, layout=None) -> bool:
        try:
            apply_reviewed_transitions(layout or c3_layout(), transitions)
        except ValueError:
            return True
        return False

    check(c3_refused(c3_entry(subject_key="p1@0,0")),
          "C3: an entry binding a different subject_key is refused")
    check(apply_reviewed_transitions(
              c3_layout(), c3_entry(source_sha256="cd" * 32))["pages"][0]
          ["comb_subjects"][0]["state"] == "retained_unresolved",
          "C3: an entry reviewed on other source bytes belongs to a sibling "
          "document and is passed over, not applied")
    check(c3_refused(c3_entry(transition="retired_proven_false")),
          "C3: retirement is deliberately unbuilt and refused")
    check(c3_refused(c3_entry(reviewer="")),
          "C3: a shape-invalid registry is refused whole")
    wrong_cell = c3_layout()
    wrong_cell["pages"][0]["comb_subjects"][0]["legacy_cell_id"] = "p1c8"
    check(c3_refused(c3_entry(), wrong_cell),
          "C3: an entry matching no retained subject is stale and refused")
    active = c3_layout()
    active["pages"][0]["comb_subjects"][0]["state"] = "active_unresolved"
    check(c3_refused(c3_entry(), active),
          "C3: an entry naming a non-retained subject is refused")
    import copy as c3_copy
    check(apply_reviewed_transitions(
              c3_copy.deepcopy(layout)) == layout,
          "C3: the shipped empty registry is a byte-identical no-op")

    # ---- C4a: reviewed resolutions of active_unresolved subjects ----------
    def c4_layout():
        return {
            "form": {"code": "9999X", "revision": "2099"},
            "source": {"sha256": "ab" * 32},
            "pages": [{
                "index": 1,
                "cells": [{
                    "id": "p1c7",
                    "comb": {"cells": 4, "resolution": {
                        "status": "unresolved",
                        "method": "final-visible-endpoint-slab",
                        "reason_codes": ["competing-endpoint-topologies"],
                        "endpoint_topologies": [{"divider_x": [1.0]}],
                    }},
                }],
                "comb_subjects": [{
                    "subject_key": "p1@7,7",
                    "legacy_cell_id": "p1c7",
                    "cell_id": "p1c7",
                    "state": "active_unresolved",
                    "blocks_gate": True,
                    "reason_codes": ["competing-endpoint-topologies"],
                }],
            }],
        }

    def c4_entry(**overrides):
        entry = {
            "subject_key": "p1@7,7",
            "source_sha256": "ab" * 32,
            "four_way": {"lattice": 4, "audit": 4, "emitted": 4, "referee": 4},
            "reviewer": "self-test", "date": "2026-08-15",
            "citation": "self-test",
        }
        entry.update(overrides)
        return {("9999x-2099", 1, "p1c7"): entry}

    c4_applied = apply_reviewed_resolutions(c4_layout(), c4_entry())
    c4_subject = c4_applied["pages"][0]["comb_subjects"][0]
    c4_resolution = c4_applied["pages"][0]["cells"][0]["comb"]["resolution"]
    check(c4_subject["state"] == "active_resolved"
          and c4_subject["blocks_gate"] is False
          and c4_subject["reason_codes"] == [],
          "C4a: a reviewed resolution resolves the subject")
    check(c4_resolution["status"] == "resolved"
          and c4_resolution["reason_codes"] == [],
          "C4a: the cell's comb resolution transitions with it")
    check(c4_resolution["endpoint_topologies"] == [{"divider_x": [1.0]}],
          "C4a: the measured evidence that made it reviewable is KEPT")
    check(c4_subject["resolution_certificate"]["resolved_reason_codes"]
          == ["competing-endpoint-topologies"],
          "C4a: the certificate records what was resolved")
    check(c4_subject["resolution_certificate"]
          == c4_resolution["review_certificate"],
          "C4a: subject and cell carry the same certificate")

    def c4_refused(entries, layout_value=None) -> bool:
        try:
            apply_reviewed_resolutions(layout_value or c4_layout(), entries)
        except ValueError:
            return True
        return False

    check(c4_refused(c4_entry(subject_key="p1@0,0")),
          "C4a: an entry binding a different subject_key is refused")
    check(apply_reviewed_resolutions(
              c4_layout(), c4_entry(source_sha256="cd" * 32))["pages"][0]
          ["comb_subjects"][0]["state"] == "active_unresolved",
          "C4a: an entry reviewed on other source bytes belongs to a sibling "
          "document and is passed over, not applied")
    check(c4_refused(c4_entry(four_way={
              "lattice": 9, "audit": 9, "emitted": 9, "referee": 9})),
          "C4a: a four-way lattice count that is not this comb's is refused")
    check(c4_refused(c4_entry(reviewer="")),
          "C4a: a shape-invalid registry is refused whole")
    c4_resolved = c4_layout()
    c4_resolved["pages"][0]["comb_subjects"][0]["state"] = "active_resolved"
    check(c4_refused(c4_entry(), c4_resolved),
          "C4a: an entry naming an already-resolved subject is refused")
    c4_stale = c4_layout()
    c4_stale["pages"][0]["comb_subjects"][0]["cell_id"] = "p1c8"
    c4_stale["pages"][0]["cells"][0]["id"] = "p1c8"
    check(c4_refused(c4_entry(), c4_stale),
          "C4a: an entry matching no eligible subject is stale and refused")
    check(apply_reviewed_resolutions(c3_copy.deepcopy(layout)) == layout,
          "C4a: the shipped empty registry is a byte-identical no-op")

    # A published stat that disagrees with the subjects it summarises is
    # wrong, and the referee re-derives every one of them. This is the exact
    # shape that refused 27 of 53 forms: the page stats were computed while
    # the page was built, before any reviewed decision could be applied.
    drift = c4_layout()
    drift["pages"][0]["stats"] = {
        "comb_subjects_active": 0, "comb_subjects_active_resolved": 0,
        "comb_subjects_active_unresolved": 1,
        "comb_subjects_retained_unresolved": 0,
        "comb_subjects_blocking": 1, "comb_evidence_blocking": 1}
    fixed = apply_reviewed_resolutions(drift, c4_entry())["pages"][0]["stats"]
    check(fixed["comb_subjects_active_resolved"] == 1
          and fixed["comb_subjects_active_unresolved"] == 0
          and fixed["comb_subjects_active"] == 1
          and fixed["comb_subjects_blocking"] == 0
          and fixed["comb_evidence_blocking"] == 0,
          "C4a: page stats are recounted after a reviewed resolution lands")
    drift_t = c3_layout()
    drift_t["pages"][0]["stats"] = {
        "comb_subjects_active": 0, "comb_subjects_active_resolved": 0,
        "comb_subjects_active_unresolved": 0,
        "comb_subjects_retained_unresolved": 1,
        "comb_subjects_blocking": 1, "comb_evidence_blocking": 1}
    fixed_t = apply_reviewed_transitions(drift_t, c3_entry())["pages"][0]["stats"]
    check(fixed_t["comb_subjects_active"] == 1
          and fixed_t["comb_subjects_retained_unresolved"] == 0
          and fixed_t["comb_subjects_blocking"] == 0,
          "C3: page stats are recounted after a reviewed transition lands")

    # ---- MULTI-PART FORMS: a code+revision slug is NOT a document identity --
    #
    # 1701's main sheet, its attachment and its consolidation all publish
    # {"code": "1701", "revision": "2018"}, so all three derive the same slug
    # while being three different PDFs.  Before this was scoped by the pinned
    # source bytes, registering the main sheet's decisions made the attachment
    # inherit them, match none, and FAIL@lattice -- which is exactly what
    # happened on the first ingestion of the real review.
    sibling = c4_layout()
    sibling["source"]["sha256"] = "cd" * 32          # same slug, other document
    sibling["pages"][0]["cells"][0]["id"] = "p1c99"
    sibling["pages"][0]["comb_subjects"][0]["cell_id"] = "p1c99"
    sibling["pages"][0]["comb_subjects"][0]["legacy_cell_id"] = "p1c99"
    check(apply_reviewed_resolutions(sibling, c4_entry())["pages"][0]
          ["comb_subjects"][0]["state"] == "active_unresolved",
          "C4a: a sibling document sharing the slug is untouched, not failed")
    sibling_t = c3_layout()
    sibling_t["source"]["sha256"] = "cd" * 32
    sibling_t["pages"][0]["comb_subjects"][0]["legacy_cell_id"] = "p1c99"
    check(apply_reviewed_transitions(sibling_t, c3_entry())["pages"][0]
          ["comb_subjects"][0]["state"] == "retained_unresolved",
          "C3: a sibling document sharing the slug is untouched, not failed")
    # ...and the staleness guard still fires for the document that DOES own
    # the entry, so scoping did not soften it.
    owner_missing = c4_layout()
    owner_missing["pages"][0]["comb_subjects"][0]["cell_id"] = "p1c8"
    owner_missing["pages"][0]["cells"][0]["id"] = "p1c8"
    check(c4_refused(c4_entry(), owner_missing),
          "C4a: staleness still fires on the document the entry names")

    # ---- C1: the comb writing surface's span-scoped edge weight ------------
    #
    # Each clause is a physical statement proven here against synthetic
    # segment geometry, and each was forced by a real corpus shape named in
    # the assertion.  The weights come from `border[edge]["segments"]`; the
    # relation is the referee's own qualifying rule (nearest run by
    # separation, heavier claim on ties), so both measurers of one relation
    # qualify the same ink.
    def c1_cell(segments, thickness=1.0, comb_span=(0.0, 40.0)):
        return ({
            "y0": 0.0, "y1": 20.0,
            "border": {
                "top": {"thickness_pt": thickness, "gray": 0.0,
                        "thicknesses_pt": [thickness],
                        "segments": segments},
                "bottom": None,
            },
        }, {"slot_x": [comb_span[0], sum(comb_span) / 2, comb_span[1]]})

    def c1_top_inset(segments, **kwargs):
        cell, comb = c1_cell(segments, **kwargs)
        surface = comb_writing_surface(cell, comb)
        assert surface is not None
        return surface[0]

    # 2316's shape: the row above's 0.84pt rule is fused into the boundary
    # line but its ink stops 0.63pt short of this cell; the 0.45pt wall at
    # the edge decides.
    check(c1_top_inset([
        {"a0": 0.0, "a1": 40.0, "c0": -1.47, "c1": -0.63,
         "thickness_pt": 0.84, "gray": 0.0},
        {"a0": 0.0, "a1": 40.0, "c0": -0.22, "c1": 0.23,
         "thickness_pt": 0.45, "gray": 0.0},
    ]) == q(0.45), "C1: a nearer wall must out-rank a farther heavier one")

    # A doubled rule: two 1.44pt bars 0.60pt each side of the edge, equally
    # near -- the full bar weight stands (0619-E's date boxes).
    check(c1_top_inset([
        {"a0": 0.0, "a1": 40.0, "c0": -2.04, "c1": -0.6,
         "thickness_pt": 1.44, "gray": 0.0},
        {"a0": 0.0, "a1": 40.0, "c0": 0.6, "c1": 2.04,
         "thickness_pt": 1.44, "gray": 0.0},
    ]) == q(1.44), "C1: a doubled rule keeps its bar weight")

    # Equal separations of DIFFERENT weight resolve to the heavier claim.
    check(c1_top_inset([
        {"a0": 0.0, "a1": 40.0, "c0": -1.5, "c1": 0.0,
         "thickness_pt": 1.5, "gray": 0.0},
        {"a0": 0.0, "a1": 40.0, "c0": 0.0, "c1": 1.0,
         "thickness_pt": 1.0, "gray": 0.0},
    ]) == q(1.5), "C1: equal separations take the heavier segment")

    # 1701-MS's shape: a heavier stretch that spans NO compartment midpoint
    # bounds no compartment and is out, however far it reaches into the span;
    # one that covers a midpoint counts.  The midpoints are the same rays the
    # referee measures on -- the two sides qualify identical ink by
    # construction (comb span 0..40, midpoints 10 and 30).
    check(c1_top_inset([
        {"a0": -10.0, "a1": 9.0, "c0": -0.25, "c1": 0.25,
         "thickness_pt": 0.5, "gray": 0.0},
        {"a0": 9.0, "a1": 40.0, "c0": -0.1, "c1": 0.1,
         "thickness_pt": 0.2, "gray": 0.0},
    ]) == q(0.2), "C1: a segment spanning no compartment midpoint is out")
    check(c1_top_inset([
        {"a0": -10.0, "a1": 10.5, "c0": -0.25, "c1": 0.25,
         "thickness_pt": 0.5, "gray": 0.0},
        {"a0": 10.5, "a1": 40.0, "c0": -0.1, "c1": 0.1,
         "thickness_pt": 0.2, "gray": 0.0},
    ]) == q(0.5), "C1: a segment covering a midpoint counts")

    # 2316 p1c40's shape: a heavier rule wholly OUTSIDE the cell band (the
    # row below's), nearer to the edge than the true wall by paper distance,
    # is not a candidate at all -- the referee's own overlap rule, mirrored.
    check(c1_top_inset([
        {"a0": 0.0, "a1": 40.0, "c0": -1.27, "c1": -0.43,
         "thickness_pt": 0.84, "gray": 0.0},
        {"a0": 0.0, "a1": 40.0, "c0": 0.62, "c1": 1.07,
         "thickness_pt": 0.45, "gray": 0.0},
    ]) == q(0.45), "C1: a run outside the cell band is no candidate")

    # No segment geometry (legacy layouts, plain callers): the fused record
    # thickness stands, which is exactly the pre-C1 behaviour.
    legacy_cell, legacy_comb = c1_cell(None, thickness=0.75)
    legacy_cell["border"]["top"].pop("segments")
    legacy_surface = comb_writing_surface(legacy_cell, legacy_comb)
    check(legacy_surface is not None and legacy_surface[0] == q(0.75),
          "C1: absent segment geometry falls back to the fused thickness")

    # A null border insets nothing, comb or no comb.
    null_cell, null_comb = c1_cell([])
    null_cell["border"]["top"] = None
    null_surface = comb_writing_surface(null_cell, null_comb)
    check(null_surface is not None and null_surface[0] == q(0.0),
          "C1: a null border insets nothing")

    def synthetic_vertical(x: float, y0: float, y1: float,
                           thickness: float, sequence: int,
                           role: str = "structural") -> dict[str, Any]:
        return {
            "axis": "v",
            "x0": q(x - thickness / 2), "x1": q(x + thickness / 2),
            "y0": q(y0), "y1": q(y1),
            "thickness_pt": q(thickness),
            "gray": 0.0 if role == "structural" else 1.0,
            "role": role,
            "paint_seq": sequence, "paint_seq_max": sequence,
        }

    def synthetic_horizontal(y: float, x0: float, x1: float,
                             thickness: float, sequence: int,
                             role: str = "structural") -> dict[str, Any]:
        return {
            "axis": "h",
            "x0": q(x0), "x1": q(x1),
            "y0": q(y - thickness / 2), "y1": q(y + thickness / 2),
            "thickness_pt": q(thickness),
            "gray": 0.0 if role == "structural" else 1.0,
            "role": role,
            "paint_seq": sequence, "paint_seq_max": sequence,
        }

    # Endpoint slabs: three heavy group separators are slightly shorter than
    # the thin seed ticks, and a fourth raw mark is knocked out later. The
    # common final-visible slab must include the former and exclude the latter.
    thin_a = synthetic_vertical(10, 0, 10, 0.2, 1)
    thin_b = synthetic_vertical(20, 0, 10, 0.2, 2)
    heavy = synthetic_vertical(15, 0.5, 10, 2.2, 3)
    stale = synthetic_vertical(25, 0.5, 10, 0.2, 4)
    knockout = {
        **stale,
        "role": "knockout", "gray": 1.0,
        "paint_seq": 5, "paint_seq_max": 5,
    }
    synthetic_paint = FinalPaint([thin_a, thin_b, heavy, stale, knockout])
    endpoint = endpoint_band(
        [thin_a, thin_b], [thin_a, thin_b, heavy, stale],
        0, 30, [(-0.5, 0.5, 1.0), (29.5, 30.5, 1.0)],
        synthetic_paint)
    check(endpoint is not None, "endpoint-slab comb topology was not found")
    if endpoint is not None:
        (endpoint_ink, endpoint_y0, endpoint_y1, _topologies,
         _horizontal_rail_only) = endpoint
        check([q(centre(ink)) for ink in endpoint_ink] == [10.0, 15.0, 20.0],
              "endpoint-slab topology did not add heavy/drop stale boundaries")
        check(q(endpoint_y0) == 0.5 and q(endpoint_y1) == 10.0,
              f"endpoint-slab common intersection {endpoint_y0}..{endpoint_y1}")

    # Ink inside a full-width horizontal rail cannot prove that verticals from
    # opposite rows share a comb.  Preserve the topology that exists on paper,
    # while retaining genuine ticks and thick group separators that continue
    # to the rail from that paper-bearing band.
    endpoint_rail = synthetic_horizontal(9.75, 0, 30, 0.5, 20)
    upper_tick = synthetic_vertical(15, 0, 10, 0.2, 21)
    lower_left = synthetic_vertical(10, 9.5, 15, 0.2, 22)
    lower_right = synthetic_vertical(20, 9.5, 15, 0.2, 23)
    rail_paint = FinalPaint([
        upper_tick, lower_left, lower_right, endpoint_rail,
    ])
    rail_endpoint = endpoint_band(
        [upper_tick], [upper_tick, lower_left, lower_right],
        0, 30, [(-0.5, 0.5, 1.0), (29.5, 30.5, 1.0)],
        rail_paint)
    check(rail_endpoint is not None,
          "paper-bearing topology beside a horizontal rail was lost")
    if rail_endpoint is not None:
        check([q(centre(ink)) for ink in rail_endpoint[0]] == [15.0],
              "opposite-row verticals were joined inside a horizontal rail")

    genuine_left = synthetic_vertical(10, 0, 10, 0.2, 24)
    genuine_right = synthetic_vertical(20, 0, 10, 0.2, 25)
    foreign_lower = synthetic_vertical(5, 9.5, 15, 0.2, 26)
    genuine_paint = FinalPaint([
        genuine_left, genuine_right, foreign_lower, endpoint_rail,
    ])
    genuine_endpoint = endpoint_band(
        [genuine_left, genuine_right],
        [genuine_left, genuine_right, foreign_lower],
        0, 30, [(-0.5, 0.5, 1.0), (29.5, 30.5, 1.0)],
        genuine_paint)
    check(genuine_endpoint is not None,
          "genuine ticks reaching a horizontal rail were lost")
    if genuine_endpoint is not None:
        check([q(centre(ink)) for ink in genuine_endpoint[0]] == [10.0, 20.0],
              "a rail-only foreign vertical displaced genuine ticks")

    rail_heavy = synthetic_vertical(15, 0.5, 10, 2.2, 27)
    heavy_rail_paint = FinalPaint([
        genuine_left, genuine_right, rail_heavy, endpoint_rail,
    ])
    heavy_rail_endpoint = endpoint_band(
        [genuine_left, genuine_right],
        [genuine_left, genuine_right, rail_heavy],
        0, 30, [(-0.5, 0.5, 1.0), (29.5, 30.5, 1.0)],
        heavy_rail_paint)
    check(heavy_rail_endpoint is not None,
          "grouped topology beside a horizontal rail was lost")
    if heavy_rail_endpoint is not None:
        check([q(centre(ink)) for ink in heavy_rail_endpoint[0]]
              == [10.0, 15.0, 20.0],
              "a heavy group separator was mistaken for rail-only ink")

    # Evidence can contain a longer disjoint rail-only run of the topology
    # chosen from paper. The reported band and its representative inks must
    # both come from the selectable paper run, never from the evidence-only
    # rail run.
    mixed_rail = synthetic_horizontal(10, 0, 30, 4, 30)
    mixed_seed = synthetic_vertical(10, 0, 12, 0.2, 31)
    mixed_paper_extra = synthetic_vertical(20, 0, 2, 0.2, 32)
    mixed_rail_extra = synthetic_vertical(20, 8, 12, 0.2, 33)
    mixed_paint = FinalPaint([
        mixed_rail, mixed_seed, mixed_paper_extra, mixed_rail_extra,
    ])
    check(mixed_paint.horizontal_rail_across(0, 30, 8, 12),
          "a fully final-visible thick rail was not recognised")
    mixed_endpoint = endpoint_band(
        [mixed_seed],
        [mixed_seed, mixed_paper_extra, mixed_rail_extra],
        0, 30, [(-0.5, 0.5, 1.0), (29.5, 30.5, 1.0)],
        mixed_paint)
    check(mixed_endpoint is not None,
          "mixed evidence/selection runs produced no topology")
    if mixed_endpoint is not None:
        check([q(centre(ink)) for ink in mixed_endpoint[0]] == [10.0, 20.0]
              and q(mixed_endpoint[1]) == 0.0
              and q(mixed_endpoint[2]) == 2.0,
              "rail evidence selected a run with no paper representative")

    # One common y witness across x does not prove the whole rail thickness is
    # black. A later knockout over half the thickness leaves paper, even when
    # later verticals repaint narrow corridors inside that half.
    partial_rail = synthetic_horizontal(10, 0, 30, 4, 40)
    partial_knockout = synthetic_horizontal(
        9, 0, 30, 2, 41, role="knockout")
    partial_seed = synthetic_vertical(10, 0, 12, 0.2, 42)
    partial_extra = synthetic_vertical(20, 8, 12, 0.2, 43)
    partial_paint = FinalPaint([
        partial_rail, partial_knockout, partial_seed, partial_extra,
    ])
    check(not partial_paint.horizontal_rail_across(0, 30, 8, 12),
          "a partially erased rail was mistaken for fully inked paper")
    partial_endpoint = endpoint_band(
        [partial_seed], [partial_seed, partial_extra],
        0, 30, [(-0.5, 0.5, 1.0), (29.5, 30.5, 1.0)],
        partial_paint)
    check(partial_endpoint is not None,
          "paper exposed by a partial rail knockout lost its topology")
    if partial_endpoint is not None:
        check([q(centre(ink)) for ink in partial_endpoint[0]] == [10.0, 20.0]
              and q(partial_endpoint[1]) == 8.0
              and q(partial_endpoint[2]) == 12.0,
              "partial-thickness paper did not retain genuine dividers: "
              f"{[q(centre(ink)) for ink in partial_endpoint[0]]} "
              f"at {partial_endpoint[1]}..{partial_endpoint[2]}")

    rail_only_tick = synthetic_vertical(10, 9.5, 10, 0.2, 28)
    rail_only_paint = FinalPaint([rail_only_tick, endpoint_rail])
    rail_only_bands = comb_bands(
        [rail_only_tick], [rail_only_tick], 0, 30, (1.0, 1.0),
        rail_only_paint)
    check(bool(rail_only_bands)
          and rail_only_bands[0]["resolution"]["status"] == "unresolved"
          and "horizontal-rail-only-topology"
          in rail_only_bands[0]["resolution"]["reason_codes"],
          "a rail-only vertical emitted a certifying comb band")

    # Y coverage is not enough to prove a divider. Erase the seed, then paint
    # disjoint left/right strips in opposite half-bands: there is structural
    # ink in every y slab, but no x corridor survives through the full height.
    corridor_seed = synthetic_vertical(10, 0, 10, 0.2, 1)
    corridor_knockout = {
        **corridor_seed,
        "role": "knockout", "gray": 1.0,
        "paint_seq": 2, "paint_seq_max": 2,
    }
    left_strip = {
        **corridor_seed,
        "x0": 9.9, "x1": 9.98, "y0": 0.0, "y1": 5.0,
        "thickness_pt": 0.08,
        "paint_seq": 3, "paint_seq_max": 3,
    }
    right_strip = {
        **corridor_seed,
        "x0": 10.02, "x1": 10.1, "y0": 5.0, "y1": 10.0,
        "thickness_pt": 0.08,
        "paint_seq": 4, "paint_seq_max": 4,
    }
    corridor_paint = FinalPaint([
        corridor_seed, corridor_knockout, left_strip, right_strip,
    ])
    check(corridor_paint.visible_intervals(corridor_seed)
          == [(0.0, 5.0), (5.0, 10.0)],
          "disjoint x corridors were merged into one visible y interval")
    check(not corridor_paint.structural_across(corridor_seed, 0.0, 10.0),
          "disjoint x corridors certified a continuous divider")
    corridor_bands = comb_bands(
        [corridor_seed], [corridor_seed], 0.0, 20.0, (0.2, 0.2),
        corridor_paint)
    check(not any(
        band["cells"] == 2
        and band["resolution"]["status"] == "resolved"
        for band in corridor_bands),
        "disjoint x corridors emitted a resolved two-cell comb")

    # A merged rule spanning source ordinals 1..100 does not prove that all of
    # its geometry was painted after an intervening seq-50 knockout.
    ranged_seed = {
        **corridor_seed,
        "paint_seq": 1, "paint_seq_max": 100,
    }
    middle_knockout = {
        **corridor_knockout,
        "paint_seq": 50, "paint_seq_max": 50,
    }
    ranged_paint = FinalPaint([ranged_seed, middle_knockout])
    check(paint_ordinal_range(ranged_seed) == (1, 100),
          "merged paint source-order range was not preserved")
    check(not ranged_paint.visible_intervals(ranged_seed),
          "max-only paint ordering revived an uncertain merged rule")
    check(not ranged_paint.structural_across(ranged_seed, 0.0, 10.0),
          "interleaved paint range certified a continuous divider")
    ranged_bands = comb_bands(
        [ranged_seed], [ranged_seed], 0.0, 20.0, (0.2, 0.2),
        ranged_paint)
    check(not any(
        band["cells"] == 2
        and band["resolution"]["status"] == "resolved"
        for band in ranged_bands),
        "interleaved paint range emitted a resolved two-cell comb")

    # Exact contributor spans disambiguate that same ordinal envelope without
    # assigning a late fragment's order to the whole merged bar. Two complete
    # paints around the knockout certify the final black rule; a one-point-high
    # late repaint exposes only that one point-high interval.
    exact_repainted_seed = {
        **ranged_seed,
        "paint_spans": [
            {"start_pt": 0.0, "end_pt": 10.0, "paint_seq": 1},
            {"start_pt": 0.0, "end_pt": 10.0, "paint_seq": 100},
        ],
    }
    exact_repainted_layers = exact_rule_paint_span_layers(
        exact_repainted_seed)
    check(
        exact_repainted_layers is not None
        and len(exact_repainted_layers) == 2
        and [paint_ordinal_range(layer)
             for layer in exact_repainted_layers] == [(1, 1), (100, 100)],
        "duplicate complete rule paints lost their singleton source order",
    )
    exact_repainted_paint = FinalPaint([
        exact_repainted_seed, middle_knockout,
    ])
    check(
        exact_repainted_paint.visible_intervals(exact_repainted_seed)
        == [(0.0, 10.0)]
        and exact_repainted_paint.structural_across(
            exact_repainted_seed, 0.0, 10.0),
        "a complete late source repaint did not restore the exact rule",
    )

    partial_repainted_seed = {
        **ranged_seed,
        "paint_spans": [
            {"start_pt": 0.0, "end_pt": 1.0, "paint_seq": 100},
            {"start_pt": 0.0, "end_pt": 10.0, "paint_seq": 1},
        ],
    }
    partial_repainted_paint = FinalPaint([
        partial_repainted_seed, middle_knockout,
    ])
    check(
        partial_repainted_paint.visible_intervals(partial_repainted_seed)
        == [(0.0, 1.0)]
        and not partial_repainted_paint.structural_across(
            partial_repainted_seed, 0.0, 10.0),
        "a tiny late fragment masqueraded as a complete source repaint",
    )

    exact_horizontal = {
        **synthetic_horizontal(5.0, 0.0, 10.0, 0.2, 7),
        "paint_spans": [
            {"start_pt": 0.0, "end_pt": 5.0, "paint_seq": 7},
            {"start_pt": 5.0, "end_pt": 10.0, "paint_seq": 7},
        ],
    }
    horizontal_layers = exact_rule_paint_span_layers(exact_horizontal)
    exact_horizontal_paint = FinalPaint([exact_horizontal])
    check(
        horizontal_layers is not None
        and [(layer["x0"], layer["x1"])
             for layer in horizontal_layers] == [(0.0, 5.0), (5.0, 10.0)]
        and all((layer["y0"], layer["y1"])
                == (exact_horizontal["y0"], exact_horizontal["y1"])
                for layer in horizontal_layers)
        and exact_horizontal_paint.structural_rect_across(
            0.0, exact_horizontal["y0"], 10.0, exact_horizontal["y1"])
        and exact_horizontal_paint.horizontal_rail_across(
            0.0, 10.0, exact_horizontal["y0"], exact_horizontal["y1"]),
        "split horizontal paint spans lost their full source rail",
    )

    joined_horizontal = {
        **synthetic_horizontal(5.0, 0.0, 10.0, 0.2, 1),
        "paint_seq_max": 3,
        "paint_spans": [
            {"start_pt": 0.0, "end_pt": 4.0, "paint_seq": 1},
            {"start_pt": 4.01, "end_pt": 10.0, "paint_seq": 3},
        ],
    }
    joined_horizontal_paint = FinalPaint([joined_horizontal])
    check(
        joined_horizontal_paint.structural_across_axis(
            joined_horizontal, 0.0, 10.0, "h"),
        "an extractor-joined 0.01pt contributor gap broke final continuity",
    )
    joined_gap_knockout = {
        **synthetic_horizontal(5.0, 4.0, 4.01, 0.2, 2,
                               role="knockout"),
    }
    check(
        not FinalPaint([
            joined_horizontal, joined_gap_knockout,
        ]).structural_across_axis(joined_horizontal, 0.0, 10.0, "h"),
        "an intervening knockout was hidden by an extractor join bridge",
    )
    same_start_join = {
        **synthetic_horizontal(5.0, 0.0, 10.0, 0.2, 2),
        "paint_seq_max": 100,
        "paint_spans": [
            {"start_pt": 0.0, "end_pt": 4.0, "paint_seq": 50},
            {"start_pt": 4.01, "end_pt": 5.0, "paint_seq": 100},
            {"start_pt": 4.01, "end_pt": 10.0, "paint_seq": 2},
        ],
    }
    same_start_knockout = {
        **synthetic_horizontal(5.0, 4.0, 4.01, 0.2, 25,
                               role="knockout"),
    }
    check(
        not FinalPaint([
            same_start_join, same_start_knockout,
        ]).structural_across_axis(same_start_join, 0.0, 10.0, "h"),
        "a same-start contributor was omitted from a join bridge ordinal range",
    )

    # Painted walls. 2551Q draws none, so the discriminator is asserted on the
    # exact corpus shapes it has to separate: 2550M page 2's 1.92 x 840.96 left
    # table side, its 1.92 x 10.56 shortest sibling on 1600WP, and 2000-OT's
    # 2.16 x 9.84 TIN group separator -- which must stay out of the x lattice,
    # because promoting it would cut its own comb into four cells.
    def synthetic_fill(x0: float, y0: float, x1: float, y1: float,
                       role: str = "structural") -> dict[str, Any]:
        return {"x0": q(x0), "y0": q(y0), "x1": q(x1), "y1": q(y1),
                "gray": 0.0 if role == "structural" else 0.75,
                "role": role, "paint_seq": 1, "paint_seq_max": 1}

    wall_2550m = synthetic_fill(20.16, 77.04, 22.08, 918.00)
    wall_shortest = synthetic_fill(340.87, 745.80, 342.79, 756.36)
    divider_2000ot = synthetic_fill(256.13, 167.66, 258.29, 177.50)
    grey_band = synthetic_fill(20.16, 77.04, 22.08, 918.00, role="decorative")
    walls = wall_boundaries([
        divider_2000ot, wall_2550m, grey_band, wall_shortest,
    ])
    check(
        [q(centre(wall)) for wall in walls] == [21.12, 341.83],
        "wall_boundaries did not keep exactly the two painted walls",
    )
    check(
        all(wall["axis"] == "v" and wall["thickness_pt"] == 1.92
            for wall in walls),
        "a painted wall reached the lattice without its axis or thickness",
    )
    check(
        not wall_boundaries([divider_2000ot]),
        "a comb group separator was promoted to a cell-grid boundary",
    )
    check(
        not wall_boundaries([grey_band]),
        "a decorative band was promoted to a cell-grid boundary",
    )
    horizontal_rail = synthetic_fill(0.0, 0.0, 600.0, 1.92)
    h_walls = wall_boundaries([horizontal_rail])
    check(
        len(h_walls) == 1 and h_walls[0]["axis"] == "h"
        and h_walls[0]["thickness_pt"] == 1.92,
        "a horizontal painted wall did not enter the y lattice",
    )
    check(
        not [wall for wall in h_walls if wall["axis"] == "v"],
        "a horizontal painted wall entered the vertical lattice",
    )
    v_split, h_split = split_wall_axes(wall_boundaries([
        wall_2550m, horizontal_rail, divider_2000ot,
    ]))
    check(
        len(v_split) == 1 and len(h_split) == 1
        and v_split[0]["axis"] == "v" and h_split[0]["axis"] == "h",
        "split_wall_axes did not partition mixed walls by axis",
    )
    page_fill = synthetic_fill(0.0, 0.0, 600.0, 800.0)
    check(
        not wall_boundaries([page_fill]),
        "a page-sized fill was promoted to a lattice wall",
    )
    footer_rail = wall_boundaries([synthetic_fill(23.5, 825.60, 590.4, 827.52)])[0]
    comb_baseline = {"x0": 85.6, "y0": 822.36, "x1": 141.5, "y1": 823.08,
                     "thickness_pt": 0.72, "axis": "h"}
    check(
        h_wall_would_fuse(footer_rail, [comb_baseline]),
        "a footer rail 2.52pt below a comb baseline was admitted as a new row",
    )
    schedule_rail = wall_boundaries([synthetic_fill(22.1, 107.28, 592.0, 109.20)])[0]
    next_row = {"x0": 22.1, "y0": 117.84, "x1": 590.0, "y1": 118.80,
                "thickness_pt": 0.96, "axis": "h"}
    check(
        not h_wall_would_fuse(schedule_rail, [next_row]),
        "Schedule 1's missing top rail was skipped as a fused neighbour",
    )

    # Column-aware reading order. A table whose columns share row y-values
    # stays left-to-right, top-to-bottom. Side-by-side sections whose rows
    # do not share y-values (0605 items 17 and 18) finish the left group
    # before the right.
    def rect(x0: float, y0: float, x1: float, y1: float, name: str
             ) -> dict[str, Any]:
        return {"x0": x0, "y0": y0, "x1": x1, "y1": y1, "id": name}

    table = [
        rect(0, 0, 10, 10, "a"), rect(10, 0, 20, 10, "b"),
        rect(0, 10, 10, 20, "c"), rect(10, 10, 20, 20, "d"),
    ]
    table_order = [item["id"] for item in order_rects_reading_order(
        table,
        lambda item: item["x0"], lambda item: item["y0"],
        lambda item: item["x1"], lambda item: item["y1"],
    )]
    check(
        table_order == ["a", "b", "c", "d"],
        "an aligned table was tabbed column-major",
    )
    sections = [
        rect(0, 0, 40, 12, "17a"), rect(50, 2, 90, 14, "18a"),
        rect(0, 12, 40, 24, "17b"), rect(50, 16, 90, 28, "18b"),
        rect(0, 24, 40, 36, "17c"), rect(50, 30, 90, 42, "18c"),
    ]
    section_order = [item["id"] for item in order_rects_reading_order(
        sections,
        lambda item: item["x0"], lambda item: item["y0"],
        lambda item: item["x1"], lambda item: item["y1"],
    )]
    check(
        section_order == ["17a", "17b", "17c", "18a", "18b", "18c"],
        "side-by-side sections were tabbed as a zig-zag",
    )

    # Shaded paper. 2551Q shades none of its cells, so the tone cut is asserted
    # on the exact corpus tones it has to separate: BIR's 0.8509 "no entry here"
    # band against the two near-white fills that cover REAL fields -- 1604cf-2008
    # p1c8/c10/c12 at 0.8902 and 2200an-2018 p2c247/c255 at 0.9489. Those five
    # must keep their inputs, which is why the rule is a tone and not merely
    # `role == "decorative"`.
    def toned_fill(x0: float, y0: float, x1: float, y1: float,
                   gray: float, seq: int = 1) -> dict[str, Any]:
        return {"x0": q(x0), "y0": q(y0), "x1": q(x1), "y1": q(y1),
                "gray": gray, "role": tone_role(gray),
                "paint_seq": seq, "paint_seq_max": seq}

    shaded_cell = {"x0": 100.0, "y0": 200.0, "x1": 200.0, "y1": 210.0}
    bir_shading = toned_fill(90.0, 198.0, 260.0, 212.0, 0.8509)
    check(
        on_shaded_paper(shaded_cell, [bir_shading]),
        "BIR's 0.8509 no-entry shading did not reach the cell it covers",
    )
    check(
        classify_cell(True, 4, False, False,
                      on_shaded_paper(shaded_cell, [bir_shading])) == "shaded",
        "an empty bordered cell on decorative shading was called a field",
    )
    for gray, where in ((0.8902, "1604cf-2008 p1c8/c10/c12"),
                        (0.9489, "2200an-2018 p2c247/c255")):
        near_white = toned_fill(90.0, 198.0, 260.0, 212.0, gray)
        check(
            not on_shaded_paper(shaded_cell, [near_white]),
            f"a near-white {gray} fill took the real fields at {where}",
        )
        check(
            classify_cell(True, 4, False, False,
                          on_shaded_paper(shaded_cell, [near_white])) == "field",
            f"a real field at {where} lost its input to its own tint",
        )
    check(
        not on_shaded_paper(
            shaded_cell, [toned_fill(90.0, 198.0, 150.0, 212.0, 0.8509)]),
        "shading over less than 70% of a cell shaded the whole cell",
    )

    # A seam between two strips of one tone is not a writing surface. 0619F
    # p1c8 is 82% grey in two strips 0.48pt apart and scored 0.495 -- the upper
    # strip alone -- so it kept an input over paper that says NO ENTRY HERE.
    # The gap here is 0.5pt against a 3.0pt character; the corpus's own worst
    # case is 1.51pt against 2.930pt.
    seam_upper = toned_fill(90.0, 198.0, 260.0, 204.5, 0.8509)
    seam_lower = toned_fill(90.0, 205.0, 260.0, 212.0, 0.8509, seq=2)
    check(
        not on_shaded_paper(shaded_cell, [seam_upper, seam_lower]),
        "two strips split by a seam were joined without a character bound",
    )
    check(
        on_shaded_paper(shaded_cell, [seam_upper, seam_lower], 3.0),
        "a 0.5pt seam between two strips of one tone was read as writable",
    )
    check(
        not on_shaded_paper(shaded_cell, [seam_upper, seam_lower], 0.4),
        "a seam wider than a character was bridged anyway",
    )
    # Only the ABSENCE of paint bridges. A knockout in the seam is the sheet
    # restoring paper, and it separates however thin it is -- the whited-out
    # centre property, unweakened.
    check(
        not on_shaded_paper(shaded_cell, [
            seam_upper, seam_lower,
            toned_fill(90.0, 204.5, 260.0, 205.0, 1.0, seq=9)], 3.0),
        "a knockout in the seam was bridged as though it were bare paper",
    )
    # Two tones meeting are two bands, not one interrupted band.
    check(
        not on_shaded_paper(shaded_cell, [
            seam_upper, toned_fill(90.0, 205.0, 260.0, 212.0, 0.651, seq=2)],
            3.0),
        "a seam joined 0.8509 to 0.651 and invented an unpainted tone",
    )
    # The seam rule must not manufacture a band where there is no shading.
    check(
        not on_shaded_paper(shaded_cell, [], 3.0),
        "bare paper with no shading at all was called shaded",
    )
    check(
        not on_shaded_paper(shaded_cell, [
            bir_shading, toned_fill(90.0, 198.0, 260.0, 212.0, 1.0, seq=9)]),
        "paper a later white knockout restored was still read as shaded",
    )
    check(
        on_shaded_paper(shaded_cell, [
            toned_fill(90.0, 198.0, 260.0, 212.0, 1.0, seq=1),
            toned_fill(90.0, 198.0, 260.0, 212.0, 0.8509, seq=9)]),
        "shading painted over a knockout was hidden by the earlier white",
    )
    check(
        not on_shaded_paper(
            shaded_cell, [toned_fill(90.0, 198.0, 260.0, 212.0, 0.0)]),
        "a black structural fill was reported as decorative shading",
    )
    check(
        classify_cell(True, 4, False, True, True) == "blank",
        "a ruled-gap sliver lost its established kind to the shading rule",
    )

    # Strip unions. Asserted on 2550M p3c9's exact source geometry: a 15.6pt
    # cell shaded by two 7.8pt 0.7529 strips covering 0.508 and 0.500, which no
    # single-fill coverage test can see. The negatives beside it are the three
    # ways a union would be wrong -- two tones are not one band, two strips with
    # paper between them leave that paper writable, and a knockout through the
    # middle restores it -- and each is the SAME two strips with one fact
    # changed, so a union that passes them cannot be passing by luck.
    strip_cell = {"x0": 537.36, "y0": 98.52, "x1": 591.00, "y1": 114.12}
    upper_strip = toned_fill(537.36, 98.52, 591.12, 106.44, 0.7529, seq=4)
    lower_strip = toned_fill(537.36, 106.32, 591.12, 114.24, 0.7529, seq=6)
    check(
        not on_shaded_paper(strip_cell, [upper_strip]),
        "one 0.508-coverage strip shaded a cell on its own",
    )
    check(
        on_shaded_paper(strip_cell, [upper_strip, lower_strip]),
        "2550M p3c9's two abutting 0.7529 strips did not shade the cell",
    )
    check(
        classify_cell(True, 4, False, False,
                      on_shaded_paper(strip_cell,
                                      [upper_strip, lower_strip])) == "shaded",
        "a cell shaded by a strip pair kept the input the raster refutes",
    )
    check(
        not on_shaded_paper(strip_cell, [
            upper_strip,
            toned_fill(537.36, 106.32, 591.12, 114.24, 0.8509, seq=6)]),
        "two different tones were unioned into one band the source never drew",
    )
    check(
        not on_shaded_paper(strip_cell, [
            toned_fill(537.36, 98.52, 591.12, 103.00, 0.7529, seq=4),
            toned_fill(537.36, 109.00, 591.12, 114.24, 0.7529, seq=6)]),
        "a union reached across white paper between two separated strips",
    )
    check(
        not on_shaded_paper(strip_cell, [
            upper_strip, lower_strip,
            toned_fill(537.36, 104.00, 591.12, 108.00, 1.0, seq=9)]),
        "a union reached across a knockout that whited out the middle",
    )
    check(
        on_shaded_paper(strip_cell, [
            toned_fill(537.36, 104.00, 591.12, 108.00, 1.0, seq=1),
            upper_strip, lower_strip]),
        "a knockout painted UNDER the strips broke a band drawn over it",
    )

    # White is not the only thing that covers a tint, and a per-point rule that
    # only subtracts knockouts is a NEW way to lose a real field. Both shapes
    # below are corpus geometry over a page-wide band: 2553 p1c16/c18/c20 paint
    # a chromatic box (no gray at all) over the 0.7529 sheet band, and
    # 1604cf-2008 p1c8/c10/c12 paint their 0.8902 boxes -- the five near-white
    # fields the tone cut exists to spare -- over it at seq 283-287.
    band_cell = {"x0": 91.80, "y0": 189.36, "x1": 96.60, "y1": 208.80}
    sheet_band = toned_fill(20.64, 131.76, 591.60, 652.56, 0.7529, seq=1)
    check(
        on_shaded_paper(band_cell, [sheet_band]),
        "the page-wide 0.7529 sheet band did not shade a cell inside it",
    )
    chromatic = {"x0": q(91.68), "y0": q(189.36), "x1": q(96.72),
                 "y1": q(208.80), "gray": None, "role": tone_role(None),
                 "paint_seq": 303, "paint_seq_max": 303}
    check(
        not on_shaded_paper(band_cell, [sheet_band, chromatic]),
        "a chromatic box painted over the band was read through as shading",
    )
    check(
        not on_shaded_paper(band_cell, [
            sheet_band,
            toned_fill(91.68, 189.36, 96.72, 208.80, 0.8902, seq=283)]),
        "a near-white 0.8902 field box painted over the band lost its input",
    )
    check(
        on_shaded_paper(band_cell, [
            {**chromatic, "paint_seq": 0, "paint_seq_max": 0}, sheet_band]),
        "a chromatic box painted UNDER the band suppressed the band above it",
    )

    # A comb compartment is a character cell, and a caption block is not.  Both
    # sides are the corpus's own numbers: the refused one is 2200S `p1c0`, the
    # masthead whose single 0.48pt vertical at x = 115.70 gave a taxpayer two
    # live inputs over "BIR Form No. / 2200-S" and "EXCISE TAX RETURN"; the
    # spared one is 1800 `p1c68`, a 2-compartment comb the source fills with the
    # printed rate `0 %`, one glyph per compartment, which is exactly the
    # decoration `classify_cell`'s "mixed" verdict is FOR.  The pair differ only
    # in how many glyphs each compartment carries, so a rule that passed both by
    # luck would have to be measuring something neither of them varies.
    def printed_run(text: str, x0: float, advance: float) -> dict[str, Any]:
        return {
            "text": text, "origin_x": q(x0),
            "x0": q(x0), "x1": q(x0 + advance * len(text)),
            "char_origin_offsets_pt": [q(advance * i)
                                       for i in range(len(text))],
            "char_widths_pt": [q(advance)] * len(text),
        }

    masthead_comb = {"cells": 2, "slot_x": [23.16, 115.70, 430.51]}
    masthead_runs = [
        printed_run("BIR Form No.", 44.88, 4.26),
        printed_run("2200-S", 37.08, 11.67),
        printed_run("EXCISE TAX RETURN", 189.98, 10.04),
    ]
    check(
        comb_compartment_glyph_counts(masthead_comb, masthead_runs)
        == [16, 15],
        "the masthead's own glyphs were not counted into its two compartments",
    )
    check(
        printed_caption_refutes_comb(masthead_comb, masthead_runs),
        "2200S p1c0's masthead caption still read as a 2-box comb, so a "
        "taxpayer keeps two inputs over the form's own title",
    )
    printed_rate_comb = {"cells": 2, "slot_x": [520.44, 534.06, 547.68]}
    printed_rate_runs = [printed_run("0", 524.0, 6.6),
                         printed_run("%", 537.5, 6.6)]
    check(
        comb_compartment_glyph_counts(printed_rate_comb, printed_rate_runs)
        == [1, 1],
        "the printed rate `0 %` was not one glyph per compartment",
    )
    check(
        not printed_caption_refutes_comb(printed_rate_comb, printed_rate_runs),
        "1800 p1c68's printed rate comb was refused as a caption block",
    )
    # The money comb the refusal must NOT take: 2200A `p1c111` as it was read
    # before `outer_paper_unguided` -- its first compartment had swallowed the
    # caption "27 Tax Debit Memo" and its other 28 were empty, so SOME
    # compartment is multi-glyph and EVERY one is not. The rule is stated over
    # every compartment precisely so this keeps its money boxes. The swallowed
    # compartment is a SEGMENTATION defect and is fixed in the rail
    # derivation, where the sheet's own ink can be measured (`comb_rails`,
    # section 3b below); the reading is kept here deliberately, because
    # `printed_caption_refutes_comb` must never be the thing that fixes it --
    # its cure is to delete every compartment, and 28 of these are real.
    debit_memo_comb = {
        "cells": 29,
        "slot_x": [16.32, 189.98] + [q(189.98 + 14.52 * i)
                                     for i in range(1, 29)],
    }
    debit_memo_runs = [printed_run("27 Tax Debit Memo", 18.36, 5.03)]
    counts = comb_compartment_glyph_counts(debit_memo_comb, debit_memo_runs)
    check(
        counts[0] > 1 and min(counts) == 0,
        "2200A p1c111's swallowed caption did not land in its first "
        "compartment alone",
    )
    check(
        not printed_caption_refutes_comb(debit_memo_comb, debit_memo_runs),
        "a 29-box money comb lost every box to one swallowed caption",
    )
    check(
        not printed_caption_refutes_comb(masthead_comb, []),
        "a comb with no printed text of its own was refused",
    )
    # The two ends of the refusal, asserted over whichever form was given
    # rather than over the one it happens to be: no published comb may fail
    # the test, and a refused one must have left the cell AND stayed in the
    # ledger as a suppressed, blocking, reviewable subject.  On 2551Q both
    # halves are vacuous by construction -- it prints no caption block of this
    # shape, so nothing here can move the pinned counts above -- and on a form
    # that does they are the whole contract.
    ir_pages = {int(page["index"]): page for page in ir["pages"]}
    for page in layout["pages"]:
        runs_by_id = {f"p{page['index']}t{index}": run
                      for index, run in enumerate(
                          ir_pages[int(page["index"])]["text_runs"])}
        retained_by_key = {
            str(subject["subject_key"]): subject
            for subject in page["comb_subjects"]
            if subject["state"] == "retained_unresolved"
        }
        for cell in page["cells"]:
            comb = cell.get("comb")
            check(
                comb is None or not printed_caption_refutes_comb(
                    comb, [runs_by_id[run_id]
                           for run_id in cell["text_run_ids"]]),
                f"{cell['id']}: a published comb fails the caption-block test",
            )
            refutation = cell.get("comb_refutation")
            if refutation is None:
                continue
            subject = retained_by_key.get(str(cell["subject_key"]))
            check(
                comb is None and cell["kind"] != "mixed",
                f"{cell['id']}: a refuted caption block kept its comb or its "
                f"fillable kind",
            )
            check(
                subject is not None
                and subject["reason_codes"]
                == [REFUTED_CAPTION_BLOCK_REASON_CODE]
                and subject["cell_id"] is None
                and subject["emission"] == "suppressed"
                and subject["blocks_gate"] is True,
                f"{cell['id']}: a refuted comb left the ledger instead of "
                f"being retained as suppressed, blocking evidence",
            )

    malformed_span_contracts: list[tuple[str, dict[str, Any]]] = [
        ("empty", {**exact_repainted_seed, "paint_spans": []}),
        ("wrong-container",
         {**exact_repainted_seed, "paint_spans": {}}),
        ("wrong-item",
         {**exact_repainted_seed, "paint_spans": [1]}),
        ("extra-key", {
            **exact_repainted_seed,
            "paint_spans": [
                {"start_pt": 0.0, "end_pt": 10.0,
                 "paint_seq": 1, "extra": True},
                {"start_pt": 0.0, "end_pt": 10.0, "paint_seq": 100},
            ],
        }),
        ("boolean-coordinate", {
            **exact_repainted_seed,
            "paint_spans": [
                {"start_pt": False, "end_pt": 10.0, "paint_seq": 1},
                {"start_pt": 0.0, "end_pt": 10.0, "paint_seq": 100},
            ],
        }),
        ("boolean-sequence", {
            **exact_repainted_seed,
            "paint_spans": [
                {"start_pt": 0.0, "end_pt": 10.0, "paint_seq": True},
                {"start_pt": 0.0, "end_pt": 10.0, "paint_seq": 100},
            ],
        }),
        ("negative-sequence", {
            **exact_repainted_seed,
            "paint_seq": -1,
            "paint_spans": [
                {"start_pt": 0.0, "end_pt": 10.0, "paint_seq": -1},
                {"start_pt": 0.0, "end_pt": 10.0, "paint_seq": 100},
            ],
        }),
        ("non-finite", {
            **exact_repainted_seed,
            "paint_spans": [
                {"start_pt": 0.0, "end_pt": math.inf, "paint_seq": 1},
                {"start_pt": 0.0, "end_pt": 10.0, "paint_seq": 100},
            ],
        }),
        ("unquantised", {
            **exact_repainted_seed,
            "paint_spans": [
                {"start_pt": 0.0, "end_pt": 9.999, "paint_seq": 1},
                {"start_pt": 0.0, "end_pt": 10.0, "paint_seq": 100},
            ],
        }),
        ("non-positive", {
            **exact_repainted_seed,
            "paint_spans": [
                {"start_pt": 0.0, "end_pt": 0.0, "paint_seq": 1},
                {"start_pt": 0.0, "end_pt": 10.0, "paint_seq": 100},
            ],
        }),
        ("unsorted", {
            **exact_repainted_seed,
            "paint_spans": [
                {"start_pt": 0.0, "end_pt": 10.0, "paint_seq": 100},
                {"start_pt": 0.0, "end_pt": 10.0, "paint_seq": 1},
            ],
        }),
        ("union-gap", {
            **exact_repainted_seed,
            "paint_spans": [
                {"start_pt": 0.0, "end_pt": 4.0, "paint_seq": 1},
                {"start_pt": 6.0, "end_pt": 10.0, "paint_seq": 100},
            ],
        }),
        ("producer-only-gap", {
            **exact_repainted_seed,
            "paint_spans": [
                {"start_pt": 0.0, "end_pt": 4.99, "paint_seq": 1},
                {"start_pt": 5.01, "end_pt": 10.0, "paint_seq": 100},
            ],
        }),
        ("unquantised-parent-start", {
            **exact_repainted_seed,
            "y0": 0.004,
        }),
        ("unquantised-parent-end", {
            **exact_repainted_seed,
            "y1": 10.004,
        }),
        ("minimum-mismatch", {
            **exact_repainted_seed,
            "paint_seq": 0,
        }),
        ("maximum-mismatch", {
            **exact_repainted_seed,
            "paint_seq_max": 101,
        }),
        ("invalid-axis", {
            **exact_repainted_seed,
            "axis": "x",
        }),
    ]
    for label, hostile in malformed_span_contracts:
        try:
            FinalPaint([hostile])
        except ValueError:
            continue
        check(False, f"malformed rule paint spans were accepted: {label}")

    # A smaller final-visible comb may replace its raw continuity count only
    # when every omitted source mark has one exact full-band witness and a
    # known-later complete erasure.  This is the paint/erase/repaint sequence
    # used by the official date boxes; width, pitch and form identity are not
    # evidence.
    reduction_retained = {
        **synthetic_vertical(10, 5, 10, 0.2, 3),
        "id": "reduction-retained",
    }
    reduction_stale = {
        **synthetic_vertical(20, 5, 10, 0.2, 1),
        "id": "reduction-stale",
    }
    reduction_knockout = {
        **reduction_stale,
        "id": "reduction-knockout",
        "role": "knockout", "gray": 1.0,
        "paint_seq": 2, "paint_seq_max": 2,
    }
    reduction_legacy = {
        "cells": 3, "divider_x": [10.0, 20.0],
        "slot_x": [0.0, 10.0, 20.0, 30.0],
        "y0": 5.0, "y1": 10.0,
    }
    reduction_final = {
        "cells": 2, "divider_x": [10.0],
        "slot_x": [0.0, 10.0, 30.0],
        "y0": 5.0, "y1": 10.0,
        "resolution": {"status": "resolved"},
    }
    reduction_certificate = erased_legacy_divider_reduction_certificate(
        reduction_legacy, reduction_final,
        [reduction_retained, reduction_stale],
        FinalPaint([
            reduction_stale, reduction_knockout, reduction_retained,
        ]))
    check(
        reduction_certificate == {
            "criterion": "final-visible-erased-legacy-divider-reduction-v1",
            "legacy_cells": 3,
            "final_cells": 2,
            "legacy_band_y": [5.0, 10.0],
            "final_paper_band_y": [5.0, 10.0],
            "horizontal_rail_trims": [],
            "retained_divider_x": [10.0],
            "erased_dividers": [{
                "divider_x": 20.0,
                "rule_id": "reduction-stale",
                "paint_range": [1, 1],
                "band_y": [5.0, 10.0],
            }],
        },
        f"complete source-order erasure was not certified: "
        f"{reduction_certificate}",
    )

    reduction_bottom_rail = synthetic_horizontal(
        9.5, 0.0, 30.0, 1.0, 4)
    rail_trimmed_final = {
        **reduction_final,
        "y1": 9.0,
    }
    rail_trimmed_paint = FinalPaint([
        reduction_stale, reduction_knockout, reduction_retained,
        reduction_bottom_rail,
    ])
    rail_trimmed_certificate = erased_legacy_divider_reduction_certificate(
        reduction_legacy, rail_trimmed_final,
        [reduction_retained, reduction_stale], rail_trimmed_paint)
    check(
        rail_trimmed_certificate is not None
        and rail_trimmed_certificate.get("horizontal_rail_trims") == [{
            "edge": "bottom", "y0": 9.0, "y1": 10.0,
        }],
        "a final paper band trimmed by its full-width baseline was rejected",
    )
    rail_endpoint = endpoint_band(
        [reduction_retained], [reduction_retained, reduction_stale],
        0.0, 30.0, [(-0.1, 0.1, 0.2), (29.9, 30.1, 0.2)],
        rail_trimmed_paint)
    check(
        rail_endpoint is not None
        and [q(centre(ink)) for ink in rail_endpoint[0]] == [10.0]
        and all(evidence.get("divider_x") == [10.0]
                for evidence in rail_endpoint[3]),
        "an erased vertical revived as a competing topology inside a rail",
    )
    check(erased_legacy_divider_reduction_certificate(
        reduction_legacy, rail_trimmed_final,
        [reduction_retained, reduction_stale],
        FinalPaint([
            reduction_stale, reduction_knockout, reduction_retained,
        ])) is None,
        "a shortened final band was accepted without a source rail")
    partial_bottom_rail = {
        **reduction_bottom_rail,
        "x1": 29.0,
    }
    check(erased_legacy_divider_reduction_certificate(
        reduction_legacy, rail_trimmed_final,
        [reduction_retained, reduction_stale],
        FinalPaint([
            reduction_stale, reduction_knockout, reduction_retained,
            partial_bottom_rail,
        ])) is None,
        "a partial-width rail certified a shortened final band")

    malformed_reduction_slots = [
        {**reduction_final, "slot_x": [0.0, 30.0]},
        {**reduction_final, "slot_x": [0.0, 11.0, 30.0]},
        {**reduction_final, "slot_x": [0.0, True, 30.0]},
        {**reduction_final, "slot_x": [0.0, 10.0, 10.0]},
        {**reduction_final, "slot_x": [0.0, 10.004, 30.0]},
        {**reduction_final, "slot_x": [0.04, 10.0, 30.04]},
    ]
    for hostile_slots in malformed_reduction_slots:
        check(erased_legacy_divider_reduction_certificate(
            reduction_legacy, hostile_slots,
            [reduction_retained, reduction_stale],
            FinalPaint([
                reduction_stale, reduction_knockout, reduction_retained,
            ])) is None,
            f"malformed reduction slot geometry was certified: "
            f"{hostile_slots['slot_x']}")

    earlier_reduction_knockout = {
        **reduction_knockout,
        "paint_seq": 0, "paint_seq_max": 0,
    }
    check(erased_legacy_divider_reduction_certificate(
        reduction_legacy, reduction_final,
        [reduction_retained, reduction_stale],
        FinalPaint([
            earlier_reduction_knockout, reduction_stale,
            reduction_retained,
        ])) is None,
        "an earlier knockout certified a later stale divider as erased")

    partial_reduction_knockout = {
        **reduction_knockout,
        "y1": 9.0,
    }
    check(erased_legacy_divider_reduction_certificate(
        reduction_legacy, reduction_final,
        [reduction_retained, reduction_stale],
        FinalPaint([
            reduction_stale, partial_reduction_knockout,
            reduction_retained,
        ])) is None,
        "partial knockout coverage certified a count reduction")

    partial_width_knockout = {
        **reduction_knockout,
        "x0": 19.95,
    }
    check(erased_legacy_divider_reduction_certificate(
        reduction_legacy, reduction_final,
        [reduction_retained, reduction_stale],
        FinalPaint([
            reduction_stale, partial_width_knockout,
            reduction_retained,
        ])) is None,
        "partial-width knockout coverage certified a count reduction")

    # 2550M's mechanism: the knockout stops inside the row's bottom rule, so
    # the stale tick's last sliver survives only where there is no paper.
    # The proof must carry the rail-absorbed residue; without the rail, or
    # with a residue reaching past the rail, it must fail closed exactly as
    # the plain partial knockout does.
    railed_partial_knockout = {
        **reduction_knockout,
        "y1": 9.4,
    }
    railed_partial_certificate = erased_legacy_divider_reduction_certificate(
        reduction_legacy, reduction_final,
        [reduction_retained, reduction_stale],
        FinalPaint([
            reduction_stale, railed_partial_knockout,
            reduction_retained, reduction_bottom_rail,
        ]))
    check(
        railed_partial_certificate is not None
        and railed_partial_certificate.get("erased_dividers") == [{
            "divider_x": 20.0,
            "rule_id": "reduction-stale",
            "paint_range": [1, 1],
            "band_y": [5.0, 10.0],
            "rail_covered_residue_y": [[9.4, 10.0]],
        }],
        f"a knockout stopping inside the bottom rail was not certified: "
        f"{railed_partial_certificate}",
    )
    unrailed_residue_knockout = {
        **reduction_knockout,
        "y1": 8.5,
    }
    check(erased_legacy_divider_reduction_certificate(
        reduction_legacy, reduction_final,
        [reduction_retained, reduction_stale],
        FinalPaint([
            reduction_stale, unrailed_residue_knockout,
            reduction_retained, reduction_bottom_rail,
        ])) is None,
        "a residue reaching past the bottom rail certified a count reduction")

    ranged_reduction_stale = {
        **reduction_stale,
        "paint_seq_max": 3,
    }
    check(erased_legacy_divider_reduction_certificate(
        reduction_legacy, reduction_final,
        [reduction_retained, ranged_reduction_stale],
        FinalPaint([
            ranged_reduction_stale, reduction_knockout,
            reduction_retained,
        ])) is None,
        "a source-order range straddling a knockout certified erasure")

    reduction_repaint = {
        **reduction_stale,
        "id": "reduction-repaint",
        "paint_seq": 4, "paint_seq_max": 4,
    }
    check(erased_legacy_divider_reduction_certificate(
        reduction_legacy, reduction_final,
        [reduction_retained, reduction_stale],
        FinalPaint([
            reduction_stale, reduction_knockout,
            reduction_retained, reduction_repaint,
        ])) is None,
        "a later structural repaint at an omitted x certified erasure")

    tolerance_final = {
        **reduction_final,
        "divider_x": [10.30],
        "slot_x": [0.0, 10.30, 30.0],
    }
    check(erased_legacy_divider_reduction_certificate(
        reduction_legacy, tolerance_final,
        [reduction_retained, reduction_stale],
        FinalPaint([
            reduction_stale, reduction_knockout, reduction_retained,
        ])) is not None,
        "the established 0.30pt boundary tolerance was narrowed")
    outside_tolerance_final = {
        **reduction_final,
        "divider_x": [10.31],
        "slot_x": [0.0, 10.31, 30.0],
    }
    check(erased_legacy_divider_reduction_certificate(
        reduction_legacy, outside_tolerance_final,
        [reduction_retained, reduction_stale],
        FinalPaint([
            reduction_stale, reduction_knockout, reduction_retained,
        ])) is None,
        "a 0.31pt boundary move was accepted by the 0.30pt tolerance")

    close_stale = {
        **synthetic_vertical(10.20, 5, 10, 0.2, 1),
        "id": "reduction-close-stale",
    }
    close_legacy = {
        **reduction_legacy,
        "cells": 4,
        "divider_x": [10.0, 10.20, 20.0],
        "slot_x": [0.0, 10.0, 10.20, 20.0, 30.0],
    }
    check(erased_legacy_divider_reduction_certificate(
        close_legacy, reduction_final,
        [reduction_retained, close_stale, reduction_stale],
        FinalPaint([
            close_stale, reduction_stale, reduction_knockout,
            reduction_retained,
        ])) is None,
        "two legacy anchors inside one clustering tolerance were certified")

    # Support and frame geometry must also be final-visible. A vertical tick
    # cannot become a comb merely because its erased raw baseline still exists
    # in the IR, and that erased baseline cannot enter the y lattice.
    erased_baseline = synthetic_horizontal(10, 0, 20, 0.2, 1)
    baseline_knockout = {
        **erased_baseline,
        "role": "knockout", "gray": 1.0,
        "paint_seq": 2, "paint_seq_max": 2,
    }
    unsupported_tick = synthetic_vertical(10, 0, 10, 0.2, 3)
    support_paint = FinalPaint([
        erased_baseline, baseline_knockout, unsupported_tick,
    ])
    check(not support_paint.structural_across_axis(
        erased_baseline, 0.0, 20.0, "h"),
        "an erased horizontal remained final-visible")
    final_support = [
        horizontal for horizontal in [erased_baseline]
        if support_paint.structural_across_axis(
            horizontal, horizontal["x0"], horizontal["x1"], "h")
    ]
    supported_ticks, unsupported_borders = split_verticals(
        [unsupported_tick], final_support)
    check(not final_support and not supported_ticks
          and unsupported_borders == [unsupported_tick],
          "raw erased baseline still classified a comb divider")
    unsupported_bands = comb_bands(
        supported_ticks, [unsupported_tick],
        0.0, 20.0, (0.2, 0.2), support_paint)
    check(not unsupported_bands,
          "tick with no final-visible support emitted a comb")

    # Collinear source fragments may be merged across several rows.  Only a
    # final-visible composite is decomposed: its leading lower-anchored piece
    # remains a comb, while complete rail-to-rail paper corridors become local
    # lattice borders.  Horizontal-rail joints themselves own no paper.
    corridor_rails = [
        synthetic_horizontal(y, 0, 20, 0.2, sequence)
        for sequence, y in enumerate((10.0, 20.0, 30.0), 200)
    ]
    composite_corridor = {
        **synthetic_vertical(10, 0, 30, 0.2, 210),
        "id": "v-composite",
        "paint_seq_max": 218,
    }
    corridor_combs, corridor_borders = split_final_vertical_corridors(
        [composite_corridor], corridor_rails,
        {"v-composite"})
    check([(item["y0"], item["y1"])
           for item in corridor_combs]
          == [(0.0, 9.9), (10.1, 19.9), (20.1, 29.9)],
          "a composite parent lost its local comb continuity")
    check([(item["y0"], item["y1"])
           for item in corridor_borders]
          == [(10.1, 19.9), (20.1, 29.9)],
          "composite row corridors were not promoted to local borders")
    corridor_fragments = [*corridor_combs, *corridor_borders]
    check(all(
        item["id"] == "v-composite"
        and item["paint_seq"] == 210
        and item["paint_seq_max"] == 218
        and item["_corridor_parent_y"] == [0.0, 30.0]
        and item["_corridor_fragment_count"] == 3
        for item in corridor_fragments),
        "corridor decomposition lost parent source provenance")
    corridor_xl = build_lattice(
        corridor_borders, corridor_borders, "v")
    check(len(corridor_xl) == 1
          and not covers(corridor_xl.spans[0], 0.0, 9.9)
          and covers(corridor_xl.spans[0], 10.1, 19.9)
          and covers(corridor_xl.spans[0], 20.1, 29.9),
          "comb-only corridor leaked into lattice border coverage")

    singleton_corridor = {
        **synthetic_vertical(10, 0, 30, 0.2, 219),
        "id": "v-singleton",
    }
    singleton_combs, singleton_borders = split_final_vertical_corridors(
        [singleton_corridor], corridor_rails,
        {"v-singleton"})
    check(singleton_combs == [singleton_corridor]
          and not singleton_borders,
          "a direct singleton divider was changed by corridor decomposition")

    rail_joint = {
        **synthetic_vertical(10, 9.9, 10.1, 0.2, 220),
        "id": "v-rail-joint",
        "paint_seq_max": 221,
    }
    joint_combs, joint_borders = split_final_vertical_corridors(
        [rail_joint], corridor_rails,
        {"v-rail-joint"})
    check(not joint_combs and not joint_borders,
          "rail-joint-only composite paint invented a paper fragment")

    upper_hanger = {
        **synthetic_vertical(10, 10.1, 15.0, 0.2, 221),
        "id": "v-upper-hanger",
        "paint_seq_max": 222,
    }
    hanger_combs, hanger_borders = split_final_vertical_corridors(
        [upper_hanger], corridor_rails, {"v-upper-hanger"})
    check(not hanger_combs and not hanger_borders,
          "an upper-anchored partial certified a rail-to-rail border")

    unproved_composite = {
        **composite_corridor,
        "id": "v-unproved-composite",
    }
    unproved_combs, unproved_borders = split_final_vertical_corridors(
        [unproved_composite], corridor_rails,
        set())
    check(unproved_combs == [unproved_composite]
          and not unproved_borders,
          "non-final composite geometry bypassed the legacy classifier")

    promotion_outer_borders = [
        {
            **synthetic_vertical(x, 0, 30, 0.2, 222 + index),
            "id": f"v-promotion-edge-{index}",
        }
        for index, x in enumerate((0.0, 20.0))
    ]
    promotion_text = [
        {"text": "Left", "x0": 1.0, "y0": 11.0,
         "x1": 9.0, "y1": 18.0},
        {"text": "Right", "x0": 11.0, "y0": 11.0,
         "x1": 19.0, "y1": 18.0},
    ]
    check(not corridor_border_promotions(
        [composite_corridor], promotion_outer_borders,
        corridor_borders, promotion_text),
        "a printed sparse stacked comb self-promoted to table columns")
    check(not corridor_border_promotions(
        [composite_corridor], promotion_outer_borders,
        corridor_borders, []),
        "an empty stacked sparse comb self-promoted to table columns")
    floating_old_border = {
        **synthetic_vertical(15, 10.5, 29.5, 0.2, 223),
        "id": "v-floating-old-border",
    }
    check(not corridor_border_promotions(
        [composite_corridor],
        [*promotion_outer_borders, floating_old_border],
        corridor_borders, promotion_text),
        "an unsupported floating border certified a sparse stacked comb")
    supported_sibling = {
        **synthetic_vertical(7.5, 0, 30, 0.2, 224),
        "id": "v-supported-sibling-comb",
        "paint_seq_max": 232,
    }
    _supported_combs, supported_borders = split_final_vertical_corridors(
        [supported_sibling], corridor_rails,
        {"v-supported-sibling-comb"})
    supported_frame_borders = [
        {
            **synthetic_vertical(x, 0, 30, 0.2, 233 + index),
            "id": f"v-supported-frame-{index}",
        }
        for index, x in enumerate((0.0, 15.0, 20.0))
    ]
    check(not corridor_border_promotions(
        [supported_sibling], supported_frame_borders,
        supported_borders, promotion_text),
        "an adjacent narrower table column distorted a two-slot comb pitch")
    wide_comb_rails = [
        synthetic_horizontal(y, 0, 40, 0.2, 236 + index)
        for index, y in enumerate((10.0, 20.0, 30.0))
    ]
    wide_comb = {
        **synthetic_vertical(15, 0, 30, 0.2, 239),
        "id": "v-wide-two-slot-comb",
        "paint_seq_max": 247,
    }
    _wide_combs, wide_corridor_borders = split_final_vertical_corridors(
        [wide_comb], wide_comb_rails, {"v-wide-two-slot-comb"})
    wide_frame_borders = [
        {
            **synthetic_vertical(x, 0, 30, 0.2, 248 + index),
            "id": f"v-wide-frame-{index}",
        }
        for index, x in enumerate((0.0, 30.0, 40.0))
    ]
    check(not corridor_border_promotions(
        [wide_comb], wide_frame_borders,
        wide_corridor_borders, promotion_text),
        "an equal wide comb was inferred as a two-column table")

    three_slot_rails = [
        synthetic_horizontal(y, 0, 22, 0.2, 236 + index)
        for index, y in enumerate((10.0, 20.0, 30.0))
    ]
    three_slot_dividers = [
        {
            **synthetic_vertical(x, 0, 30, 0.2, 239 + index),
            "id": f"v-three-slot-{index}",
            "paint_seq_max": 247 + index,
        }
        for index, x in enumerate((5.0, 10.0))
    ]
    three_slot_corridors = [
        fragment
        for divider in three_slot_dividers
        for fragment in split_final_vertical_corridors(
            [divider], three_slot_rails, {str(divider["id"])})[1]
    ]
    three_slot_frame = [
        {
            **synthetic_vertical(x, 0, 30, 0.2, 249 + index),
            "id": f"v-three-slot-frame-{index}",
        }
        for index, x in enumerate((0.0, 15.0, 22.0))
    ]
    check(not corridor_border_promotions(
        three_slot_dividers, three_slot_frame,
        three_slot_corridors, promotion_text),
        "an adjacent table column distorted a three-slot comb pitch")

    table_rails = [
        synthetic_horizontal(y, 0, 30, 0.2, sequence)
        for sequence, y in enumerate((10.0, 20.0, 30.0), 223)
    ]
    table_composite = {
        **synthetic_vertical(10, 0, 30, 0.2, 226),
        "id": "v-table-composite",
        "paint_seq_max": 234,
    }
    table_corridor_combs, table_corridor_borders = (
        split_final_vertical_corridors(
            [table_composite], table_rails, {"v-table-composite"}))
    table_old_borders = [
        {
            **synthetic_vertical(x, 0, 30, 0.2, 235 + index),
            "id": f"v-table-border-{index}",
        }
        for index, x in enumerate((0.0, 18.0, 30.0))
    ]
    check(corridor_border_promotions(
        [table_composite], table_old_borders,
        table_corridor_borders, []) == {"v-table-composite"},
        "repeated irregular table corridors lacked geometry-only proof")
    equal_comb_sibling = {
        **synthetic_vertical(24, 0, 30, 0.2, 239),
        "id": "v-equal-comb-sibling",
        "paint_seq_max": 247,
    }
    _equal_sibling_combs, equal_sibling_borders = (
        split_final_vertical_corridors(
            [equal_comb_sibling], table_rails,
            {"v-equal-comb-sibling"}))
    check(corridor_border_promotions(
        [table_composite, equal_comb_sibling], table_old_borders,
        [*table_corridor_borders, *equal_sibling_borders], [])
        == {"v-table-composite"},
        "an equal comb inherited an unrelated table sibling's proof")
    check(not corridor_border_promotions(
        [{**table_composite, "id": "v-one-row"}],
        table_old_borders,
        [{**table_corridor_borders[0], "id": "v-one-row"}],
        promotion_text),
        "one ambiguous row corridor invented a lattice position")
    existing_table_border = {
        **synthetic_vertical(10, 10.1, 19.9, 0.2, 239),
        "id": "v-existing-table-border",
    }
    missing_table_sibling = {
        **synthetic_vertical(22, 0, 30, 0.2, 240),
        "id": "v-missing-table-sibling",
        "paint_seq_max": 248,
    }
    _sibling_combs, sibling_borders = split_final_vertical_corridors(
        [missing_table_sibling], table_rails,
        {"v-missing-table-sibling"})
    check(corridor_border_promotions(
        [table_composite, missing_table_sibling],
        [*table_old_borders, existing_table_border],
        [*table_corridor_borders, *sibling_borders], [])
        == {"v-table-composite", "v-missing-table-sibling"},
        "an existing x suppressed independent corridor localisation")
    localized_table_coverage = [
        *table_old_borders, existing_table_border,
        *table_corridor_borders,
    ]
    existing_table_xl = build_lattice(
        [*table_old_borders, existing_table_border],
        localized_table_coverage, "v")
    existing_x_index = min(
        range(len(existing_table_xl.positions)),
        key=lambda index: abs(existing_table_xl.positions[index] - 10.0))
    check(not covers(existing_table_xl.spans[existing_x_index], 0.0, 9.9)
          and covers(existing_table_xl.spans[existing_x_index], 10.1, 19.9)
          and covers(existing_table_xl.spans[existing_x_index], 20.1, 29.9),
          "an existing x revived the certified composite's full hull")
    localized_leading = localized_comb_dividers(
        [table_composite], table_corridor_combs, {"v-table-composite"})
    check([(rule["y0"], rule["y1"]) for rule in localized_leading]
          == [(0.0, 9.9)],
          "a uniquely local leading comb fragment was discarded")
    dense_old_dividers = [
        {
            **synthetic_vertical(x, 0, 30, 0.2, 230 + index),
            "id": f"v-dense-{index}",
        }
        for index, x in enumerate((5.0, 10.0, 15.0, 20.0))
    ]
    dense_corridor_borders = [
        {**item, "id": "v-dense-1"} for item in corridor_borders
    ]
    check(not corridor_border_promotions(
        dense_old_dividers, promotion_outer_borders,
        dense_corridor_borders, promotion_text),
        "a dense equal-pitch character grid became table columns")

    near_join = {
        **synthetic_vertical(10, 10.11, 30, 0.2, 240),
        "id": "v-near-join",
        "paint_seq_max": 241,
    }
    _near_combs, near_borders = split_final_vertical_corridors(
        [near_join], corridor_rails, {"v-near-join"})
    check([(item["y0"], item["y1"]) for item in near_borders]
          == [(10.11, 19.9), (20.1, 29.9)],
          "a source join inside JOIN_EPSILON_PT lost a row border")
    far_join = {
        **near_join,
        "id": "v-far-join",
        "y0": 10.16,
    }
    _far_combs, far_borders = split_final_vertical_corridors(
        [far_join], corridor_rails, {"v-far-join"})
    check([(item["y0"], item["y1"]) for item in far_borders]
          == [(20.1, 29.9)],
          "a source gap beyond JOIN_EPSILON_PT certified a full row border")

    # Coverage/density is not source ownership. The shorter lower divider makes
    # a second plausible topology; retain both as evidence and fail closed.
    topology_left = synthetic_vertical(10, 0, 10, 0.2, 10)
    topology_right = synthetic_vertical(20, 0, 10, 0.2, 11)
    topology_middle = synthetic_vertical(15, 6, 10, 0.2, 12)
    topology_paint = FinalPaint([
        topology_left, topology_right, topology_middle,
    ])
    topology_bands = comb_bands(
        [topology_left, topology_right],
        [topology_left, topology_right, topology_middle],
        0.0, 30.0, (0.2, 0.2), topology_paint)
    check(bool(topology_bands),
          "competing endpoint topologies produced no retained subject")
    if topology_bands:
        topology_resolution = topology_bands[0]["resolution"]
        carried = {
            tuple(item["divider_x"])
            for item in topology_resolution.get("endpoint_topologies") or ()
        }
        check(topology_resolution["status"] == "unresolved"
              and "competing-endpoint-topologies"
              in topology_resolution["reason_codes"],
              "coverage winner silently resolved competing endpoint topologies")
        check(carried == {(10.0, 20.0), (10.0, 15.0, 20.0)},
              f"competing endpoint topology evidence was lost: {carried}")

    # A competitor proven only inside a full-width horizontal rail exists on
    # no paper, so it stays published as evidence without de-certifying the
    # paper topology.  Give the same competitor one sliver of paper and the
    # band must fall back to unresolved competing evidence -- the paper
    # coverage test, not the evidence count, is what gates.
    rail_competitor_rail = synthetic_horizontal(9.75, 0.0, 30.0, 0.5, 16)
    rail_competitor_seed = synthetic_vertical(15, 0, 10, 0.2, 13)
    rail_competitor_stale = synthetic_vertical(20, 0, 10, 0.2, 14)
    railed_competitor_bands = comb_bands(
        [rail_competitor_seed],
        [rail_competitor_seed, rail_competitor_stale],
        0.0, 30.0, (0.2, 0.2),
        FinalPaint([
            rail_competitor_seed, rail_competitor_stale,
            {
                **rail_competitor_stale,
                "role": "knockout", "gray": 1.0,
                "paint_seq": 15, "paint_seq_max": 15,
                "y1": 9.5,
            },
            rail_competitor_rail,
        ]))
    check(bool(railed_competitor_bands)
          and railed_competitor_bands[0]["divider_x"] == [15.0]
          and railed_competitor_bands[0]["resolution"]["status"] == "resolved"
          and "competing-endpoint-topologies"
          not in railed_competitor_bands[0]["resolution"]["reason_codes"],
          f"a rail-only competitor de-certified the paper topology: "
          f"{railed_competitor_bands}")
    if railed_competitor_bands:
        railed_evidence = {
            tuple(item["divider_x"]): float(item["paper_coverage_pt"])
            for item in railed_competitor_bands[0]["resolution"].get(
                "endpoint_topologies") or ()
        }
        check(railed_evidence.get((15.0,), 0.0) > 0.0
              and railed_evidence.get((15.0, 20.0)) == 0.0,
              f"rail-only evidence was not published with its paper "
              f"coverage: {railed_evidence}")
    paper_competitor_bands = comb_bands(
        [rail_competitor_seed],
        [rail_competitor_seed, rail_competitor_stale],
        0.0, 30.0, (0.2, 0.2),
        FinalPaint([
            rail_competitor_seed, rail_competitor_stale,
            {
                **rail_competitor_stale,
                "role": "knockout", "gray": 1.0,
                "paint_seq": 15, "paint_seq_max": 15,
                "y1": 9.0,
            },
            rail_competitor_rail,
        ]))
    check(bool(paper_competitor_bands)
          and paper_competitor_bands[0]["resolution"]["status"] == "unresolved"
          and "competing-endpoint-topologies"
          in paper_competitor_bands[0]["resolution"]["reason_codes"],
          "a competitor holding one sliver of paper was silently resolved")
    check(boundary_topology_subset(
        [10.2, 20.0], [10.0, 20.0, 30.0]),
        "near-identical physical boundaries were not matched as a subset")
    check(not boundary_topology_subset(
        [10.0, 10.1], [10.0, 20.0, 30.0]),
        "two topology values reused one physical boundary")
    check(same_boundary_topology(
        [10.0, 20.0], [10.3, 19.7]),
        "exact clustering-tolerance boundary drift was not treated as equal")
    check(not same_boundary_topology(
        [10.0, 20.0], [10.31, 19.7]),
        "outside-tolerance boundary drift was treated as equal")

    owner_cell = {"x0": 0.0, "y0": 0.0, "x1": 30.0, "y1": 10.0}
    owner_comb = {
        "slot_x": [0.0, 10.0, 20.0, 30.0],
        "y0": 5.0, "y1": 10.0,
    }
    check(comb_has_cell_owner(owner_cell, owner_comb),
          "a contained comb band lost its cell owner")
    check(comb_has_cell_owner(
        owner_cell, {**owner_comb, "y0": -5.0, "y1": 0.1}),
        "a positively overlapping shared-edge comb lost its cell owner")
    check(not comb_has_cell_owner(
        owner_cell, {**owner_comb, "y0": -5.0, "y1": 15.0}),
        "an unproved multi-row comb inherited a cell owner")
    check(comb_has_cell_owner(
        owner_cell, {
            **owner_comb,
            "divider_x": [10.0, 20.0],
            "divider_paint_seq": [1, 2],
            "divider_paint_ranges": [[1, 1], [2, 2]],
            "y0": -5.0,
            "y1": 15.0,
        }), "direct multi-row divider corridors lost their cell owner")
    missing_sequence = {
        **owner_comb,
        "divider_x": [10.0, 20.0],
        "divider_paint_ranges": [[1, 1], [2, 2]],
        "y0": -5.0,
        "y1": 15.0,
    }
    check(not comb_has_cell_owner(owner_cell, missing_sequence),
          "a direct corridor without paint-sequence evidence gained an owner")
    hostile_owner_contracts = [
        {"slot_x": 1},
        {"divider_x": 1, "divider_paint_ranges": [[1, 1], [2, 2]]},
        {"divider_x": [10.0], "divider_paint_ranges": [[1, 1]]},
        {"divider_paint_seq": 1},
        {"divider_paint_seq": [101, 102]},
        {"divider_paint_seq": [True, 2]},
        {"divider_x": [10.0, 20.0], "divider_paint_ranges": 1},
        {"divider_x": [10.0, 20.0],
         "divider_paint_ranges": [[None, None], [2, 2]]},
        {"divider_x": [10.0, 20.0],
         "divider_paint_ranges": [["x", "x"], [2, 2]]},
        {"divider_x": [10.0, 20.0],
         "divider_paint_ranges": [[True, 1], [2, 2]]},
        {"divider_x": [10.0, 20.0],
         "divider_paint_ranges": [[-1, -1], [2, 2]]},
        {"divider_x": [10.0, 20.0],
         "divider_paint_ranges": [[[1], [1]], [2, 2]]},
    ]
    for hostile in hostile_owner_contracts:
        candidate = {
            **owner_comb,
            "divider_x": [10.0, 20.0],
            "divider_paint_seq": [1, 2],
            "divider_paint_ranges": [[1, 1], [2, 2]],
            "y0": -5.0,
            "y1": 15.0,
            **hostile,
        }
        check(not comb_has_cell_owner(owner_cell, candidate),
              f"a malformed direct-corridor contract gained an owner: "
              f"{hostile}")
    check(not comb_has_cell_owner(
        owner_cell, {**owner_comb, "y0": -5.0, "y1": 0.0}),
        "an above-cell comb inherited the adjacent row")
    check(not comb_has_cell_owner(
        owner_cell, {**owner_comb, "y0": 10.0, "y1": 15.0}),
        "a below-cell comb inherited the adjacent row")
    check(not comb_has_cell_owner(
        owner_cell, {**owner_comb, "slot_x": [-1.0, 10.0, 30.0]}),
        "a horizontally unowned comb inherited the cell")

    # A normal four-sided comb has no crossed internal lattice edge, but its
    # final-visible frame can still prove which of two nested endpoint
    # topologies owns the writing band.  That certificate is topology proof;
    # it must not depend on component-preservation geometry.
    frame_left = synthetic_vertical(0, 0, 10, 0.2, 20)
    frame_right = synthetic_vertical(30, 0, 10, 0.2, 21)
    frame_top = synthetic_horizontal(0, 0, 30, 0.2, 22)
    frame_bottom = synthetic_horizontal(10, 0, 30, 0.2, 23)
    ordinary_frame_x = Lattice(
        [0.0, 30.0], [-0.1, 29.9], [0.1, 30.1],
        [[(0.0, 10.0)], [(0.0, 10.0)]],
        [[frame_left], [frame_right]],
    )
    ordinary_frame_y = Lattice(
        [0.0, 10.0], [-0.1, 9.9], [0.1, 10.1],
        [[(0.0, 30.0)], [(0.0, 30.0)]],
        [[frame_top], [frame_bottom]],
    )
    ordinary_frame_box = {
        "j0": 0, "j1": 1, "i0": 0, "i1": 1,
        "component_root": 0, "rectangular": True,
    }
    ordinary_frame_v_at = [[True], [True]]
    ordinary_frame_h_at = [[True], [True]]
    ordinary_frame_paint = FinalPaint([
        frame_left, frame_right, frame_top, frame_bottom,
        topology_left, topology_right, topology_middle,
    ])
    ordinary_certificate = source_owned_comb_frame(
        ordinary_frame_box, ordinary_frame_x, ordinary_frame_y,
        ordinary_frame_v_at, ordinary_frame_h_at,
        [topology_left, topology_right],
        [topology_left, topology_right, topology_middle],
        ordinary_frame_paint,
    )
    # The two outer boundaries here run the frame's full height while the
    # middle one hangs inside it, so they are this comb's printed RAILS and it
    # owns one compartment boundary, not three. The certificate must publish
    # the rails it measured rather than restating the component box, and it
    # must still resolve the competition -- against the winning topology's own
    # enclosed part, since the published evidence is untrimmed.
    check(
        ordinary_certificate is not None
        and ordinary_certificate.get("resolved_competing_topologies") is True
        and ordinary_certificate.get("divider_x") == [15.0]
        and ordinary_certificate.get("outer_rail_x") == [10.0, 20.0],
        "an ordinary framed comb did not certify its unique maximal topology",
    )
    # Same frame, same evidence, with the outer boundaries stopping inside the
    # paper: nothing is a rail, so every boundary stays a compartment divider
    # and the comb is the whole box. This is the pair that makes the rule above
    # a measurement rather than a preference.
    hung_left = synthetic_vertical(10, 3, 10, 0.2, 25)
    hung_right = synthetic_vertical(20, 3, 10, 0.2, 26)
    hung_certificate = source_owned_comb_frame(
        ordinary_frame_box, ordinary_frame_x, ordinary_frame_y,
        ordinary_frame_v_at, ordinary_frame_h_at,
        [hung_left, hung_right],
        [hung_left, hung_right, topology_middle],
        FinalPaint([
            frame_left, frame_right, frame_top, frame_bottom,
            hung_left, hung_right, topology_middle,
        ]),
    )
    check(
        hung_certificate is not None
        and hung_certificate.get("divider_x") == [10.0, 15.0, 20.0]
        and hung_certificate.get("outer_rail_x") == [0.0, 30.0],
        "a comb of hanging ticks was bounded by one of its own dividers",
    )

    # ---- the rail derivation itself, on the two questions it asks ----
    #
    # (1) WHERE the owner's edge rules this band. The lattice line here is a
    # composite: two bars, 0.2 apart, only the left one crossing the band. The
    # line's own position is their mean, and taking it would put the comb's
    # edge on paper the source rules nothing at.
    rail_band = (4.0, 10.0)
    rail_edge_near = synthetic_vertical(30.0, 0, 10, 0.2, 60)
    rail_edge_far = synthetic_vertical(30.2, -5, 3, 0.2, 61)
    rail_edge_paint = FinalPaint([rail_edge_near, rail_edge_far])
    check(
        edge_rail([rail_edge_near, rail_edge_far],
                  (30.0, 30.2, 0.2), *rail_band) == 30.0,
        "a comb rail was measured on a bar that does not cross its band",
    )
    check(
        edge_rail([rail_edge_far], (30.0, 30.2, 0.2), *rail_band) is None,
        "an edge that rules nothing across the band still produced a rail",
    )
    # A bar far enough from the edge to be its own boundary is a compartment
    # divider, and a rail measured on it would be a comb eating its neighbour.
    rail_separate = synthetic_vertical(28.0, 0, 10, 0.2, 62)
    check(
        edge_rail([rail_separate], (30.0, 30.2, 0.2), *rail_band) is None,
        "a distinct boundary beside the edge was promoted to a rail",
    )
    #
    # (2) WHETHER a boundary closes a box. The wall below is drawn in two
    # pieces meeting where the band's own top rule crosses it; the same two
    # pieces with the junction painted out are the source saying the stroke
    # stops there, and then it divides nothing.
    wall_upper = synthetic_vertical(15, 0.1, 4.0, 0.4, 63)
    wall_lower = synthetic_vertical(15, 4.2, 9.9, 0.2, 64)
    wall_paint = FinalPaint([wall_upper, wall_lower])
    check(
        divides_owner_paper(
            15.0, [wall_upper, wall_lower], 0.1, 9.9, wall_paint),
        "a wall broken by its own crossing rule stopped dividing the paper",
    )
    knockout_junction = {
        **synthetic_horizontal(4.1, 0.0, 30.0, 0.2, 65, role="knockout"),
        "paint_seq": 65, "paint_seq_max": 65,
    }
    check(
        not divides_owner_paper(
            15.0, [wall_upper, wall_lower], 0.1, 9.9,
            FinalPaint([wall_upper, knockout_junction, wall_lower])),
        "a stroke the source painted out at the junction still closed a box",
    )
    # The break itself has to be narrower than the ink either side of it.
    wide_break_lower = synthetic_vertical(15, 5.0, 9.9, 0.2, 66)
    check(
        not divides_owner_paper(
            15.0, [wall_upper, wide_break_lower], 0.1, 9.9,
            FinalPaint([wall_upper, wide_break_lower])),
        "two strokes with paper between them were joined into one wall",
    )
    check(
        not divides_owner_paper(
            15.0, [wall_lower], 0.1, 9.9, FinalPaint([wall_lower])),
        "a tick hanging inside the paper was read as a wall",
    )
    #
    # (3) HOW WIDE the rail is painted, which is a different question from
    # where it is and is answered from exactly the same bars. The rail's ink is
    # the drawn stack's envelope -- the bar that does not cross the band is not
    # part of this rail and contributes none of its ink -- and the compartments
    # may be written only on the paper after all of it.
    check(
        ink_envelope(edge_rail_bars(
            [rail_edge_near, rail_edge_far], (30.0, 30.2, 0.2), *rail_band))
        == (29.9, 30.1)
        and ink_envelope(edge_rail_bars(
            [rail_edge_far], (30.0, 30.2, 0.2), *rail_band)) is None,
        "a comb rail's ink was measured on bars that do not rule its band",
    )
    # ...and `comb_rails` has to WIRE that measurement to the same bars its
    # position came from. The right edge below is the composite from (1): the
    # bar at 30.0 rules the band and the one at 30.2 does not, so the rail is
    # at 30.0 and its ink is 29.9..30.1, not the line's whole 29.9..30.3.
    rail_ink_left_edge = synthetic_vertical(0.0, 0, 10, 0.2, 67)
    rail_ink_left_far = synthetic_vertical(-0.2, -5, 3, 0.2, 70)
    rail_ink_tick = synthetic_vertical(15.0, 6, 10, 0.2, 68)
    rail_ink_paper = CombOwnerPaper(
        0.0, 10.0, [rail_ink_left_edge, rail_ink_left_far],
        [rail_edge_near, rail_edge_far])
    measured_rails = comb_rails(
        [rail_ink_tick], [rail_ink_left_edge, rail_ink_left_far,
                          rail_ink_tick, rail_edge_near, rail_edge_far],
        0.0, 30.0, *rail_band, (0.2, 0.2), rail_ink_paper,
        FinalPaint([rail_ink_left_edge, rail_ink_left_far, rail_ink_tick,
                    rail_edge_near, rail_edge_far]))
    check(
        (measured_rails.left_x, measured_rails.right_x) == (0.0, 30.0)
        and measured_rails.left_ink == (-0.1, 0.1)
        and measured_rails.right_ink == (29.9, 30.1),
        f"comb_rails reported ink from bars that do not rule the band: "
        f"{measured_rails.left_ink} {measured_rails.right_ink}",
    )
    # A rail trimmed inward to an interior WALL is a different stroke, and its
    # ink is that wall's. Reporting the owner's edge ink there would inset the
    # comb off a rule 10pt away from the one bounding it.
    rail_ink_wall = synthetic_vertical(10.0, 0, 10, 0.4, 69)
    walled_rails = comb_rails(
        [rail_ink_wall, rail_ink_tick],
        [rail_ink_left_edge, rail_ink_left_far, rail_ink_wall, rail_ink_tick,
         rail_edge_near, rail_edge_far],
        0.0, 30.0, *rail_band, (0.2, 0.2), rail_ink_paper,
        FinalPaint([rail_ink_left_edge, rail_ink_left_far, rail_ink_wall,
                    rail_ink_tick, rail_edge_near, rail_edge_far]))
    check(
        walled_rails.left_x == 10.0
        and walled_rails.left_ink == (9.8, 10.2)
        and walled_rails.right_ink == (29.9, 30.1),
        f"a rail trimmed to a wall kept the owner edge's ink: "
        f"{walled_rails.left_x} {walled_rails.left_ink}",
    )
    # No owning paper is no measurement, on both sides, and never the fused
    # lattice edge times a nominal thickness.
    unowned_rails = comb_rails(
        [rail_ink_tick], [rail_ink_tick], 0.0, 30.0, *rail_band,
        (0.2, 0.2), None, None)
    check(
        unowned_rails.left_ink is None and unowned_rails.right_ink is None,
        "an unowned band invented rail ink from its nominal edges",
    )
    check(
        unowned_rails.left_trim["refused"] == RAIL_REFUSED_NO_OWNER_PAPER
        and unowned_rails.right_trim["refused"] == RAIL_REFUSED_NO_OWNER_PAPER,
        "an unmeasurable rail did not name the clause that kept it on the "
        "cell box",
    )
    #
    # (3b) WHETHER the paper outside the outermost tick is a compartment at
    # all, which is the same question the wall trim asks where the sheet drew
    # no wall. The fixture is 2200-C item 27 at the coordinates the sheet
    # paints it: the row runs 16.32..595.32 between rules at 805.78 and
    # 821.64, its bottom guide row is 815.52..821.64, and the row's first
    # guide stands at 189.98 -- 173.66pt of caption ("27 Tax Debit Memo") in
    # front of a comb whose own pitch is 14.52. Nothing closes that paper off,
    # so the rail used to fall back to the cell box and the caption was
    # published as a 29th compartment, unfillable, over the printed words.
    caption_paper = (805.78, 821.64)
    caption_band = (815.52, 821.64)
    caption_left_edge = synthetic_vertical(16.32, 747.82, 898.32, 1.44, 4989)
    caption_right_edge = synthetic_vertical(595.32, 747.82, 898.80, 1.44, 4993)
    caption_ticks = [
        synthetic_vertical(189.98, 815.52, 822.12, 0.48, 5730),
        *(synthetic_vertical(x, 815.52, 821.64, 0.24, 5731 + index)
          for index, x in enumerate((
              204.53, 219.05, 233.45, 247.97, 262.37, 276.89, 291.41,
              305.81, 320.33, 334.75, 349.27, 363.67, 378.19, 392.71,
              407.23, 421.63, 436.15, 450.55, 465.10, 479.50, 494.02,
              508.54, 522.94, 537.46, 551.86, 566.38, 580.90))),
    ]
    # The upper half of the same column, and the 0.48pt white strip the sheet
    # paints across the junction between them. The strip is what makes 189.98
    # a guide TICK: without it the column closes the row's paper and is a
    # wall.
    caption_upper = synthetic_vertical(189.98, 799.18, 815.04, 0.48, 5468)
    caption_seam = synthetic_horizontal(
        815.28, 117.62, 594.60, 0.48, 5631, role="knockout")
    # The four marks the sheet paints where the missing guides would stand:
    # gray 1.0, over no black rule at all. They erase nothing and they guide
    # nothing, and reading them as compartment evidence would republish the
    # caption box they are evidence against.
    caption_knockouts = [
        synthetic_vertical(x, 815.04, 821.64, 0.48, 5632 + index,
                           role="knockout")
        for index, x in enumerate((132.14, 146.66, 161.06, 175.58))
    ]
    caption_owner = CombOwnerPaper(
        *caption_paper, [caption_left_edge], [caption_right_edge])

    def caption_rails(boundaries: Sequence[dict[str, Any]],
                      *added: dict[str, Any]) -> CombRails:
        pool = [caption_left_edge, caption_right_edge, caption_upper,
                caption_seam, *caption_knockouts, *boundaries, *added]
        return comb_rails(
            list(boundaries), pool, 16.32, 595.32, *caption_band,
            (1.44, 1.44), caption_owner, FinalPaint(pool))

    caption_trimmed = caption_rails(caption_ticks)
    check(
        caption_trimmed.left_x == 189.98
        and caption_trimmed.left_ink == (189.74, 190.22)
        and len(caption_trimmed.enclosed) == len(caption_ticks) - 1,
        f"the caption paper in front of a money comb was still published as "
        f"a compartment: {caption_trimmed.left_x} "
        f"{caption_trimmed.left_ink}",
    )
    check(
        caption_trimmed.left_trim["method"]
        == RAIL_TRIMMED_TO_UNGUIDED_OUTER_PAPER
        and caption_trimmed.left_trim["outer_paper_pt"] == 173.66
        and caption_trimmed.left_trim["comb_pitch_pt"] == 14.52
        and caption_trimmed.left_trim["guide_ink_x"] == [],
        f"the trimmed rail did not report what moved it: "
        f"{caption_trimmed.left_trim}",
    )
    # The knockouts are in that pool and they are the only marks in the outer
    # paper. If white ink counted, this comb keeps its phantom compartment.
    check(
        caption_trimmed.left_trim["guide_ink_x"] == [],
        "the sheet's white marks were counted as compartment guides",
    )
    # The opposite side of the same comb: 14.42pt of paper against a 14.52pt
    # pitch is one compartment, and nothing may move that rail.
    check(
        caption_trimmed.right_x == 595.32
        and caption_trimmed.right_trim["refused"] == RAIL_REFUSED_WITHIN_PITCH,
        f"a comb's own last compartment was trimmed away: "
        f"{caption_trimmed.right_x} {caption_trimmed.right_trim}",
    )
    # One real black guide anywhere in that paper and the rail stays: the
    # sheet has marked a compartment there, whatever else is printed over it.
    # (x = 103.82 is where the caption's own last glyph ends, so it is inside
    # the printed words and outside every mark the sheet actually paints.)
    caption_guided = caption_rails(
        caption_ticks,
        synthetic_vertical(103.82, 815.52, 821.64, 0.24, 5464))
    check(
        caption_guided.left_x == 16.32
        and caption_guided.left_trim["refused"] == RAIL_REFUSED_GUIDED
        and caption_guided.left_trim["guide_ink_x"] == [103.82],
        f"a comb guided in its own outer paper was trimmed anyway: "
        f"{caption_guided.left_x} {caption_guided.left_trim}",
    )
    # ...and the same mark, painted out by a later layer, guides nothing. This
    # is the shape the sheet itself draws four times over at
    # 132.14/146.66/161.06/175.58, except that there the black stroke was
    # never laid down at all.
    caption_erased = caption_rails(
        caption_ticks,
        synthetic_vertical(103.82, 815.52, 821.64, 0.24, 5464),
        synthetic_vertical(103.82, 815.04, 822.12, 0.72, 5700,
                           role="knockout"))
    check(
        caption_erased.left_x == 189.98,
        "a guide the sheet painted out still held a phantom compartment open",
    )
    # More than two compartments' worth of paper is the threshold, and it is
    # measured against the comb's OWN pitch. Below it the paper may be one
    # wide compartment and the rail may not move: the same ink with the cell
    # edge brought in to 175.58 leaves 14.4pt, one compartment, and stays.
    caption_narrow_edge = synthetic_vertical(
        175.58, 747.82, 898.32, 1.44, 4989)
    caption_narrow_pool = [
        caption_narrow_edge, caption_right_edge, caption_upper, caption_seam,
        *caption_ticks]
    caption_narrow = comb_rails(
        list(caption_ticks), caption_narrow_pool, 175.58, 595.32,
        *caption_band, (1.44, 1.44),
        CombOwnerPaper(*caption_paper, [caption_narrow_edge],
                       [caption_right_edge]),
        FinalPaint(caption_narrow_pool))
    check(
        caption_narrow.left_x == 175.58
        and caption_narrow.left_trim["refused"] == RAIL_REFUSED_WITHIN_PITCH
        and caption_narrow.left_trim["outer_paper_pitches"] == 0.99,
        f"a single wide compartment was trimmed off its own comb: "
        f"{caption_narrow.left_x} {caption_narrow.left_trim}",
    )
    # The SAME sheet without the seam knockout: the column at 189.98 now
    # closes the row's paper, so it is a wall, and the wall trim -- which
    # predates this one -- puts the rail in exactly the same place. Two
    # clauses, one reading of the paper.
    caption_walled_pool = [caption_left_edge, caption_right_edge,
                           caption_upper, *caption_ticks]
    caption_walled = comb_rails(
        list(caption_ticks), caption_walled_pool, 16.32, 595.32,
        *caption_band, (1.44, 1.44), caption_owner,
        FinalPaint(caption_walled_pool))
    check(
        caption_walled.left_x == 189.98
        and caption_walled.left_trim["method"] == RAIL_TRIMMED_TO_WALL,
        f"the wall trim and the unguided-paper trim disagreed about one "
        f"sheet: {caption_walled.left_x} {caption_walled.left_trim}",
    )
    # A comb whose every boundary closes a box has no tick run for a rail to
    # sit outside of, and neither trim may reach it. This is 1604CF `p2c73`
    # and 2551M `p2c13`: two ruled table columns, the wide one 2.58 and 2.21
    # times its neighbour, each a real writing box the sheet closed on both
    # sides -- and each reviewed and confirmed as 2 compartments.
    column_left = synthetic_vertical(174.48, 93.36, 405.60, 0.96, 15)
    column_middle = synthetic_vertical(243.12, 54.12, 405.00, 0.72, 90)
    column_right = synthetic_vertical(269.76, 55.80, 406.44, 0.72, 91)
    column_pool = [column_left, column_middle, column_right]
    column_rails = comb_rails(
        [column_middle], column_pool, 174.48, 269.76, 220.80, 236.64,
        (0.96, 0.72),
        CombOwnerPaper(220.80, 236.64, [column_left], [column_right]),
        FinalPaint(column_pool))
    check(
        (column_rails.left_x, column_rails.right_x) == (174.48, 269.76)
        and column_rails.left_trim["refused"] == RAIL_REFUSED_NO_TICK_RUN
        and column_rails.right_trim["refused"] == RAIL_REFUSED_NO_TICK_RUN,
        f"a row of full-height boxes lost a column to the tick-run trim: "
        f"{column_rails.left_x} {column_rails.right_x} "
        f"{column_rails.left_trim}",
    )
    # A pair of marks measures one distance, and one distance is not a pitch.
    # 2550M `p1c89`'s raw legacy reading is the case: a genuine divider at
    # 260.40 and a stale mark at 263.52 that the sheet replaced, whose 3.12pt
    # "pitch" would condemn the 13.44pt of paper beside them and take the
    # whole subject out of the ledger with it.
    check(
        comb_interior_pitch([260.40]) is None
        and comb_interior_pitch([260.40, 263.52]) is None
        and comb_interior_pitch([260.40, 263.52, 273.00]) == 3.12
        and comb_interior_pitch([q(centre(tick)) for tick in caption_ticks])
        == 14.52,
        "a comb's own interior pitch was measured from fewer than two gaps",
    )
    stale_pair = [
        synthetic_vertical(260.40, 838.68, 842.28, 0.72, 321),
        synthetic_vertical(263.52, 838.68, 842.28, 0.72, 244),
    ]
    stale_pair_edges = [
        synthetic_vertical(246.96, 828.96, 841.92, 0.72, 216),
        synthetic_vertical(273.84, 828.96, 841.92, 0.72, 216),
    ]
    stale_pair_pool = [*stale_pair_edges, *stale_pair]
    stale_pair_rails = comb_rails(
        stale_pair, stale_pair_pool, 246.96, 273.84, 838.68, 842.28,
        (0.72, 0.72),
        CombOwnerPaper(829.32, 841.56, [stale_pair_edges[0]],
                       [stale_pair_edges[1]]),
        FinalPaint(stale_pair_pool))
    check(
        stale_pair_rails.left_x == 246.96
        and stale_pair_rails.left_trim["refused"]
        == RAIL_REFUSED_NO_INTERIOR_PITCH,
        f"a stale mark beside a divider measured a pitch and ate the row: "
        f"{stale_pair_rails.left_x} {stale_pair_rails.left_trim}",
    )
    #
    # (3c) The same derivation over the CORPUS, because a clause that never
    # fires on a real sheet is not a clause. The subject is the layout tree
    # this producer wrote beside the IR it was handed -- `batch.py` writes
    # both in one pass, and the gate regenerates before it scores -- and it is
    # scored on three things: every published rail says which clause put it
    # there, every trimmed rail's evidence lands on the coordinate the comb
    # actually publishes, and the trim fires somewhere. An absent or comb-less
    # tree is a FAILURE and never a skip; unevaluable is not a pass here.
    layout_dir = ir_path.resolve().parent.parent / "layout"
    published = (sorted(layout_dir.glob("*.layout.json"))
                 if layout_dir.is_dir() else [])
    corpus_combs = 0
    corpus_trimmed: list[str] = []
    corpus_unnamed: list[str] = []
    corpus_unbound: list[str] = []
    corpus_contradictory: list[str] = []
    for layout_path in published:
        document = json.loads(layout_path.read_text(encoding="utf-8"))
        for corpus_page in document["pages"]:
            for corpus_cell in corpus_page["cells"]:
                corpus_comb = corpus_cell.get("comb")
                if not corpus_comb:
                    continue
                corpus_combs += 1
                slot_x = [float(value) for value in corpus_comb["slot_x"]]
                trims = corpus_comb.get("outer_rail_trim") or {}
                for side, rail_x in (("left", slot_x[0]),
                                     ("right", slot_x[-1])):
                    where = f"{layout_path.stem}:{corpus_cell['id']}:{side}"
                    record = trims.get(side)
                    if not isinstance(record, dict) or "method" not in record:
                        corpus_unnamed.append(where)
                        continue
                    if (record["method"] == RAIL_AT_OWNER_EDGE
                            and "refused" not in record):
                        corpus_unnamed.append(where)
                        continue
                    trimmed_here = record["method"] != RAIL_AT_OWNER_EDGE
                    if record["method"] == (
                            RAIL_TRIMMED_TO_UNGUIDED_OUTER_PAPER):
                        corpus_trimmed.append(where)
                        if not (float(record["outer_paper_pt"])
                                > 2.0 * float(record["comb_pitch_pt"])
                                and record["guide_ink_x"] == []):
                            corpus_contradictory.append(where)
                    if (record.get("refused") == RAIL_REFUSED_WITHIN_PITCH
                            and float(record["outer_paper_pt"])
                            > 2.0 * float(record["comb_pitch_pt"])):
                        corpus_contradictory.append(where)
                    bound = record.get("to_x" if trimmed_here else "from_x")
                    if bound is not None and q(float(bound)) != q(rail_x):
                        corpus_unbound.append(where)
    check(bool(published) and corpus_combs > 0,
          f"no published comb to score the rail derivation on: {layout_dir} "
          f"holds {len(published)} layouts and {corpus_combs} combs")
    check(not corpus_unnamed,
          f"{len(corpus_unnamed)} published rails do not say which clause put "
          f"them where they are: {corpus_unnamed[:5]}")
    check(not corpus_unbound,
          f"{len(corpus_unbound)} published rails carry evidence for a "
          f"coordinate the comb does not publish: {corpus_unbound[:5]}")
    check(not corpus_contradictory,
          f"{len(corpus_contradictory)} published rails contradict their own "
          f"measurement: {corpus_contradictory[:5]}")
    check(bool(corpus_trimmed),
          f"the unguided-outer-paper trim fired on none of {corpus_combs} "
          f"published combs, so nothing in this corpus exercises it")
    #
    # (4) The horizontal writing surface itself: `slot_x` runs rail CENTRE to
    # rail centre, so the outer compartments are inset to those rails' ink.
    # 2551M is the sheet that forces it -- the wall left of item 28C is painted
    # 238.92-239.64 and the caption's `C` inks to 239.5176, under the rule --
    # and the numbers below are that cell's.
    written_comb = {
        "slot_x": [239.28, 246.0, 253.0, 260.76],
        "left_rail_ink": [238.92, 239.64],
        "right_rail_ink": [260.28, 261.24],
    }
    check(
        comb_writing_edges(written_comb)
        == (239.64, 260.28, RAIL_INK_WRITING_EDGE, RAIL_INK_WRITING_EDGE),
        "the outer writing edges were not taken from the rails' own ink",
    )
    # Per side, and never invented: a rail whose ink could not be measured
    # keeps `slot_x`'s own value and SAYS SO, so that a comb which could not be
    # inset is counted rather than mistaken for one that needed no inset.
    check(
        comb_writing_edges({**written_comb, "left_rail_ink": None})
        == (239.28, 260.28, RAIL_INK_UNMEASURED, RAIL_INK_WRITING_EDGE)
        and comb_writing_edges({**written_comb, "right_rail_ink": None})
        == (239.64, 260.76, RAIL_INK_WRITING_EDGE, RAIL_INK_UNMEASURED)
        and comb_writing_edges({
            "slot_x": [239.28, 246.0, 253.0, 260.76]})
        == (239.28, 260.76, RAIL_INK_UNMEASURED, RAIL_INK_UNMEASURED),
        "an unmeasurable rail did not fail closed to its published position",
    )
    # A rail drawn entirely OUTSIDE the rectangle the comb is emitted on moves
    # nothing: the rectangle still bounds what may be typed, and half that bar
    # is the neighbour's.
    check(
        comb_writing_edges({
            **written_comb, "left_rail_ink": [238.0, 239.0],
            "right_rail_ink": [261.0, 262.0]})
        == (239.28, 260.76, RAIL_INK_WRITING_EDGE, RAIL_INK_WRITING_EDGE),
        "a rail painted off the comb's own rectangle moved the writing edge",
    )
    # The concession, and it is `comb_writing_surface`'s: where insetting would
    # leave no outer compartment to write in, BOTH insets are surrendered whole
    # rather than one side quietly eating a compartment.
    check(
        comb_writing_edges({
            **written_comb, "left_rail_ink": [238.92, 247.0]})
        == (239.28, 260.76, RAIL_INK_SURRENDERED, RAIL_INK_SURRENDERED)
        and comb_writing_edges({
            **written_comb, "right_rail_ink": [252.0, 261.24]})
        == (239.28, 260.76, RAIL_INK_SURRENDERED, RAIL_INK_SURRENDERED),
        "an inset that swallows its own compartment was published anyway",
    )
    # Published beside the vertical band, never over `slot_x`: the compartment
    # BOUNDARIES stay what the source drew and only the writing surface is
    # inset. Both are needed and neither may overwrite the other.
    written_surface = comb_on_writing_surface(
        {**written_comb, "y0": 8.0, "y1": 10.0}, (1.0, 9.0))
    check(
        written_surface["slot_x"] == [239.28, 246.0, 253.0, 260.76]
        and written_surface["writing_x0"] == 239.64
        and written_surface["writing_x1"] == 260.28
        and written_surface["writing_width_pt"] == q(260.28 - 239.64)
        and written_surface["writing_x_rails"] == {
            "left": RAIL_INK_WRITING_EDGE, "right": RAIL_INK_WRITING_EDGE}
        and written_surface["writing_y0"] == 1.0
        and written_surface["writing_y1"] == 9.0
        and (written_surface["y0"], written_surface["y1"]) == (8.0, 10.0),
        "the writing surface overwrote the boundaries the source drew",
    )

    off_baseline_middle = synthetic_vertical(15, 5, 9, 0.2, 24)
    off_baseline_paint = FinalPaint([
        frame_left, frame_right, frame_top, frame_bottom,
        topology_left, topology_right, off_baseline_middle,
    ])
    check(source_owned_comb_frame(
        ordinary_frame_box, ordinary_frame_x, ordinary_frame_y,
        ordinary_frame_v_at, ordinary_frame_h_at,
        [topology_left, topology_right],
        [topology_left, topology_right, off_baseline_middle],
        off_baseline_paint,
    ) is None, "an off-baseline topology received a frame certificate")

    incomparable_endpoint = synthetic_vertical(25, 0, 4, 0.2, 25)
    incomparable_paint = FinalPaint([
        frame_left, frame_right, frame_top, frame_bottom,
        topology_left, topology_right, topology_middle,
        incomparable_endpoint,
    ])
    check(source_owned_comb_frame(
        ordinary_frame_box, ordinary_frame_x, ordinary_frame_y,
        ordinary_frame_v_at, ordinary_frame_h_at,
        [topology_left, topology_right],
        [topology_left, topology_right, topology_middle,
         incomparable_endpoint],
        incomparable_paint,
    ) is None, "incomparable endpoint topologies received a frame certificate")

    incomplete_frame_y = Lattice(
        [0.0, 10.0], [-0.1, 9.9], [0.1, 10.1],
        [[(0.0, 29.0)], [(0.0, 30.0)]],
        [[frame_top], [frame_bottom]],
    )
    check(source_owned_comb_frame(
        ordinary_frame_box, ordinary_frame_x, incomplete_frame_y,
        ordinary_frame_v_at, ordinary_frame_h_at,
        [topology_left, topology_right],
        [topology_left, topology_right, topology_middle],
        ordinary_frame_paint,
    ) is None, "an incomplete outer frame received a comb certificate")

    # Slot count alone cannot activate a changed legacy subject. Exercise the
    # exact build_cells transition path: both combs have three slots, but the
    # reviewed dividers at 10/20 moved to 8/22 in the current detector.
    ledger_left = {
        **synthetic_vertical(0, 0, 10, 0.2, 40),
        "id": "ledger-left",
    }
    ledger_right = {
        **synthetic_vertical(30, 0, 10, 0.2, 41),
        "id": "ledger-right",
    }
    ledger_top = {
        **synthetic_horizontal(0, 0, 30, 0.2, 42),
        "id": "ledger-top",
    }
    ledger_bottom = {
        **synthetic_horizontal(10, 0, 30, 0.2, 43),
        "id": "ledger-bottom",
    }
    current_ledger_dividers = [
        {
            **synthetic_vertical(x, 5, 10, 0.2, 44 + index),
            "id": f"current-ledger-divider-{index}",
        }
        for index, x in enumerate((8.0, 22.0))
    ]
    legacy_ledger_dividers = [
        {
            **synthetic_vertical(x, 5, 10, 0.2, 46 + index),
            "id": f"legacy-ledger-divider-{index}",
        }
        for index, x in enumerate((10.0, 20.0))
    ]
    ledger_x = Lattice(
        [0.0, 30.0], [-0.1, 29.9], [0.1, 30.1],
        [[(0.0, 10.0)], [(0.0, 10.0)]],
        [[ledger_left], [ledger_right]],
    )
    ledger_y = Lattice(
        [0.0, 10.0], [-0.1, 9.9], [0.1, 10.1],
        [[(0.0, 30.0)], [(0.0, 30.0)]],
        [[ledger_top], [ledger_bottom]],
    )
    ledger_cells, _ledger_text, ledger_subjects, _ledger_inferences = (
        build_cells(
            1, ledger_x, ledger_y, DisjointSet(1),
            [[True], [True]], [[True], [True]],
            [ledger_left, ledger_right], [ledger_top, ledger_bottom],
            current_ledger_dividers, current_ledger_dividers,
            FinalPaint([
                ledger_left, ledger_right, ledger_top, ledger_bottom,
                *current_ledger_dividers,
            ]),
            [],
            legacy_dividers=legacy_ledger_dividers,
            legacy_extra_ink=legacy_ledger_dividers,
        )
    )
    expected_topology_transition = {
        "old_divider_x": [10.0, 20.0],
        "new_divider_x": [8.0, 22.0],
        "comparison_tolerance_pt": CLUSTER_TOL_PT,
        "independently_certified": False,
    }
    ledger_comb_resolution = (
        ledger_cells[0].get("comb", {}).get("resolution", {})
        if len(ledger_cells) == 1 else {}
    )
    check(
        len(ledger_cells) == 1
        and ledger_comb_resolution.get("status") == "unresolved"
        and "same-count-boundary-topology-change"
        in ledger_comb_resolution.get("reason_codes", [])
        and ledger_comb_resolution.get("boundary_topology_transition")
        == expected_topology_transition,
        "same-count topology drift remained a resolved current comb",
    )
    check(
        len(ledger_subjects) == 1
        and ledger_subjects[0].get("state") == "active_unresolved"
        and ledger_subjects[0].get("blocks_gate") is True
        and ledger_subjects[0].get("old_divider_x") == [10.0, 20.0]
        and ledger_subjects[0].get("new_divider_x") == [8.0, 22.0]
        and ledger_subjects[0].get("boundary_topology_transition")
        == expected_topology_transition,
        f"same-count topology drift did not block the subject ledger: "
        f"{ledger_subjects}",
    )

    # Exercise the lower-count transition through the complete subject ledger,
    # not only through its pure certificate.  A fully erased legacy tick is
    # removed while the subject identity stays active; an ordinal range that
    # straddles the knockout keeps the larger legacy count blocking.
    def erased_reduction_ledger_case(
            label: str, stale_range_end: int,
            extra_paints: Sequence[dict[str, Any]] = (),
            ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        retained = {
            **synthetic_vertical(15.0, 5.0, 10.0, 0.2, 46),
            "id": f"{label}-retained",
        }
        stale_member = {
            **synthetic_vertical(20.0, 5.0, 10.0, 0.2, 44),
            "id": f"{label}-stale",
            "paint_seq_max": stale_range_end,
        }
        stale_knockout = {
            **stale_member,
            "id": f"{label}-knockout",
            "role": "knockout", "gray": 1.0,
            "paint_seq": 45, "paint_seq_max": 45,
        }
        case_cells, _case_text, case_subjects, _case_inferences = (
            build_cells(
                1, ledger_x, ledger_y, DisjointSet(1),
                [[True], [True]], [[True], [True]],
                [ledger_left, ledger_right], [ledger_top, ledger_bottom],
                [retained], [retained],
                FinalPaint([
                    ledger_left, ledger_right, ledger_top, ledger_bottom,
                    stale_member, stale_knockout, retained, *extra_paints,
                ]),
                [],
                legacy_dividers=[retained, stale_member],
                legacy_extra_ink=[retained, stale_member],
                final_supported_divider_ids={str(retained["id"])},
            )
        )
        return case_cells, case_subjects

    reduced_cells, reduced_subjects = erased_reduction_ledger_case(
        "complete-erasure", 44)
    reduced_comb = (
        reduced_cells[0].get("comb") if len(reduced_cells) == 1 else None)
    reduced_certificate = (
        (reduced_comb.get("resolution") or {}).get(
            "legacy_count_reduction")
        if reduced_comb is not None else None)
    check(
        reduced_comb is not None
        and reduced_comb.get("cells") == 2
        and reduced_comb.get("divider_x") == [15.0]
        and (reduced_comb.get("resolution") or {}).get("status") == "resolved"
        and reduced_certificate is not None
        and len(reduced_subjects) == 1
        and reduced_subjects[0].get("state") == "active_resolved"
        and reduced_subjects[0].get("cells") == 2
        and reduced_subjects[0].get("blocks_gate") is False,
        f"fully erased legacy divider did not reduce through the ledger: "
        f"{reduced_cells}, {reduced_subjects}",
    )

    ranged_cells, ranged_subjects = erased_reduction_ledger_case(
        "ranged-erasure", 47)
    ranged_comb = (
        ranged_cells[0].get("comb") if len(ranged_cells) == 1 else None)
    check(
        ranged_comb is not None
        and ranged_comb.get("cells") == 3
        and "final-visible-count-regression"
        in (ranged_comb.get("resolution") or {}).get("reason_codes", [])
        and len(ranged_subjects) == 1
        and ranged_subjects[0].get("state") == "active_unresolved"
        and ranged_subjects[0].get("blocks_gate") is True,
        "source-order-ranged erasure did not preserve the blocking legacy count",
    )

    # Legacy reconciliation must not attach the richer endpoint topology from
    # an adjacent row merely because its long seed dividers cross this cell.
    # Exercise both directions, with and without a valid current-owned band.
    def inherited_endpoint_case(
            label: str,
            long_y: tuple[float, float],
            extra_y: tuple[float, float],
            with_current_band: bool,
            composite_corridor: bool = False,
            ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        legacy_lines = [
            {
                **synthetic_vertical(
                    x, long_y[0], long_y[1], 0.2, 50 + index),
                "id": f"{label}-legacy-{index}",
            }
            for index, x in enumerate((10.0, 20.0))
        ]
        if composite_corridor:
            for line in legacy_lines:
                line["paint_seq_max"] = int(line["paint_seq"]) + 100
        adjacent_extra = {
            **synthetic_vertical(
                15.0, extra_y[0], extra_y[1], 0.2, 52),
            "id": f"{label}-adjacent-extra",
        }
        current_lines = [
            {
                **synthetic_vertical(x, 5.0, 10.0, 0.2, 53 + index),
                "id": f"{label}-current-{index}",
            }
            for index, x in enumerate((10.0, 20.0))
        ]
        active_lines = current_lines if with_current_band else legacy_lines
        active_extra = (
            current_lines if with_current_band
            else [*legacy_lines, adjacent_extra]
        )
        cells_result, _text_result, subjects_result, _inferences_result = (
            build_cells(
                1, ledger_x, ledger_y, DisjointSet(1),
                [[True], [True]], [[True], [True]],
                [ledger_left, ledger_right], [ledger_top, ledger_bottom],
                active_lines, active_extra,
                FinalPaint([
                    ledger_left, ledger_right, ledger_top, ledger_bottom,
                    *legacy_lines, adjacent_extra, *current_lines,
                ]),
                [],
                legacy_dividers=legacy_lines,
                legacy_extra_ink=[*legacy_lines, adjacent_extra],
                final_supported_divider_ids=(
                    {str(line["id"]) for line in legacy_lines}
                    if with_current_band else None
                ),
            )
        )
        return cells_result, subjects_result

    for direction, long_y, extra_y in (
            ("above", (-5.0, 10.0), (-5.0, 0.0)),
            ("below", (0.0, 15.0), (10.0, 15.0))):
        for with_current_band in (False, True):
            inherited_cells, inherited_subjects = inherited_endpoint_case(
                f"{direction}-{'current' if with_current_band else 'legacy'}",
                long_y, extra_y, with_current_band)
            inherited_comb = (
                inherited_cells[0].get("comb")
                if len(inherited_cells) == 1 else None
            )
            check(
                inherited_comb is not None
                and inherited_comb.get("cells") == 3
                and comb_has_cell_owner(inherited_cells[0], inherited_comb)
                and len(inherited_subjects) == 1,
                f"{direction}-cell endpoint topology inherited an adjacent band",
            )
            if inherited_comb is not None:
                check(
                    (inherited_comb.get("resolution") or {}).get("status")
                    == ("resolved" if with_current_band else "unresolved"),
                    f"{direction}-cell current-band precedence is wrong",
                )

    traversing_cells, traversing_subjects = inherited_endpoint_case(
        "both-rails", (-5.0, 15.0), (-5.0, 15.0), False, True)
    traversing_cell_comb = (
        traversing_cells[0].get("comb")
        if len(traversing_cells) == 1 else None
    )
    traversing_subject = (
        traversing_subjects[0]
        if len(traversing_subjects) == 1 else {}
    )
    check(
        traversing_cell_comb is None
        and traversing_subject.get("state") == "retained_unresolved"
        and traversing_subject.get("emission") == "suppressed"
        and traversing_subject.get("cell_id") is None
        and (traversing_subject.get("legacy_comb") or {}).get("cells") == 4,
        "a both-rails legacy subject was emitted instead of retained: "
        f"{traversing_subjects}",
    )

    # A current-only topology rejected by the same owner proof has no reviewed
    # legacy subject, but it is still evidence.  It must remain in the explicit
    # suppressed-inference ledger and keep blocking the gate.
    inference_lines = [
        {
            **synthetic_vertical(x, -0.5, 10.5, 0.2, 60 + index),
            "id": f"unowned-inference-{index}",
            "paint_seq_max": 160 + index,
        }
        for index, x in enumerate((10.0, 20.0))
    ]
    inference_top = {
        **synthetic_horizontal(0.0, 0.0, 30.0, 2.0, 58),
        "id": "unowned-inference-top",
    }
    inference_bottom = {
        **synthetic_horizontal(10.0, 0.0, 30.0, 2.0, 59),
        "id": "unowned-inference-bottom",
    }
    inference_y = Lattice(
        [0.0, 10.0], [-1.0, 9.0], [1.0, 11.0],
        [[(0.0, 30.0)], [(0.0, 30.0)]],
        [[inference_top], [inference_bottom]],
    )
    (inference_cells, _inference_text, inference_subjects,
     inference_ledger) = build_cells(
        1, ledger_x, inference_y, DisjointSet(1),
        [[True], [True]], [[True], [True]],
        [ledger_left, ledger_right], [inference_top, inference_bottom],
        inference_lines, inference_lines,
        FinalPaint([
            ledger_left, ledger_right, inference_top, inference_bottom,
            *inference_lines,
        ]),
        [],
        legacy_dividers=[],
        legacy_extra_ink=[],
    )
    inference = inference_ledger[0] if len(inference_ledger) == 1 else {}
    check(
        len(inference_cells) == 1
        and "comb" not in inference_cells[0]
        and not inference_subjects
        and inference.get("state") == "suppressed_unreviewed_inference"
        and inference.get("blocks_gate") is True
        and inference.get("reason_codes") == [
            "emission-suppressed-unproved-multi-row-divider-corridor",
            "no-legacy-subject",
        ]
        and (inference.get("inferred_comb") or {}).get("cells") == 3,
        "an unowned partition-only inference vanished or became nonblocking: "
        f"{inference_ledger}",
    )

    represented_cell = {
        "id": "p1c9",
        "subject_key": "p1@0.00,0.00,30.00,10.00",
        "x0": 0.0,
        "y0": 0.0,
        "x1": 30.0,
        "y1": 10.0,
    }
    represented_comb = {
        "cells": 3,
        "divider_count": 2,
        "divider_x": [10.0, 20.0],
        "slot_x": [0.0, 10.0, 20.0, 30.0],
        "y0": 2.0,
        "y1": 4.0,
    }
    represented_candidate = {
        "cell_id": "p1c9",
        "new_subject_key": "p1@0.00,0.00,30.00,10.00",
        "new_bbox": [0.0, 0.0, 30.0, 10.0],
        "cells": 3,
        "band_y": [2.0, 4.0],
        "divider_x": [10.0, 20.0],
        "new_slot_x": [0.0, 10.0, 20.0, 30.0],
        "activation_blockers": ["independent-evidence-not-attested"],
        "one_to_one_geometry_candidate": True,
        "blocks_gate": True,
    }

    def represented_subject_with(**candidate_updates: Any) -> dict[str, Any]:
        return {
            "state": "retained_unresolved",
            "blocks_gate": True,
            "requires_independent_evidence": True,
            "erased_edge_replacement_candidates": [{
                **represented_candidate,
                **candidate_updates,
            }],
        }

    represented_subject = represented_subject_with()
    check(
        retained_replacement_covers_inference(
            [represented_subject], represented_cell, represented_comb),
        "an exact retained replacement did not cover its duplicate inference",
    )
    check(
        not retained_replacement_covers_inference(
            [represented_subject], {
                **represented_cell,
                "combs": [
                    represented_comb,
                    {**represented_comb, "y0": 6.0, "y1": 8.0},
                ],
            }, represented_comb),
        "a retained replacement suppressed a second comb band",
    )
    check(
        not retained_replacement_covers_inference(
            [represented_subject, represented_subject],
            represented_cell, represented_comb),
        "ambiguous retained replacements suppressed an inference",
    )
    ambiguous_subject = {
        **represented_subject,
        "erased_edge_replacement_candidates": [
            represented_candidate,
            {
                **represented_candidate,
                "cell_id": "p1c10",
                "new_subject_key": "p1@30.00,0.00,60.00,10.00",
            },
        ],
    }
    check(
        not retained_replacement_covers_inference(
            [ambiguous_subject], represented_cell, represented_comb),
        "a stale one-to-one flag hid another replacement candidate",
    )
    check(
        not retained_replacement_covers_inference(
            [represented_subject_with(activation_blockers=[])],
            represented_cell, represented_comb),
        "a nonblocking retained replacement suppressed an inference",
    )
    check(
        not retained_replacement_covers_inference(
            [represented_subject_with(activation_blockers=[" "])],
            represented_cell, represented_comb),
        "a blank retained blocker suppressed an inference",
    )
    check(
        not retained_replacement_covers_inference(
            [represented_subject_with()], represented_cell, {
                **represented_comb,
                "y0": 6.0,
                "y1": 8.0,
            }),
        "a disjoint comb band was covered by a retained replacement",
    )
    check(
        not retained_replacement_covers_inference(
            [represented_subject_with(new_slot_x=[0.0, 10.0, 19.0, 30.0])],
            represented_cell, represented_comb),
        "stale retained slot evidence suppressed an inference",
    )
    check(
        not retained_replacement_covers_inference(
            [represented_subject_with(divider_x=[10.004, 20.0])],
            represented_cell, represented_comb),
        "off-grid retained coordinates suppressed an inference",
    )
    check(
        not retained_replacement_covers_inference(
            [represented_subject_with(band_y=[2.004, 4.0])],
            represented_cell, represented_comb),
        "an off-grid retained band suppressed an inference",
    )
    # An outer slot INSIDE the rectangle is the comb's own measured rail: the
    # rectangle rules a caption beside the comb, and the comb starts where its
    # rail is drawn. That is evidence, not malformation, and it is covered.
    railed_outer_comb = {
        **represented_comb,
        "divider_x": [10.0, 20.0],
        "slot_x": [1.0, 10.0, 20.0, 30.4],
    }
    check(
        retained_replacement_covers_inference(
            [represented_subject_with(
                new_slot_x=railed_outer_comb["slot_x"])],
            represented_cell, railed_outer_comb),
        "a rail-bounded inference was refused by its own retained blocker",
    )
    # A whole compartment outside the rectangle is not a rail on any reading:
    # it is a comb claiming paper the subject next door owns.
    malformed_outer_comb = {
        **represented_comb,
        "divider_x": [10.0, 20.0],
        "slot_x": [-11.0, 10.0, 20.0, 30.0],
    }
    assert (-11.0 + 10.0) / 2.0 < 0.0
    check(
        not retained_replacement_covers_inference(
            [represented_subject_with(
                divider_x=malformed_outer_comb["divider_x"],
                new_slot_x=malformed_outer_comb["slot_x"])],
            represented_cell, malformed_outer_comb),
        "mutually malformed outer-slot evidence suppressed an inference",
    )
    malformed_order_comb = {
        **represented_comb,
        "divider_x": [20.0, 10.0],
        "slot_x": [0.0, 20.0, 10.0, 30.0],
    }
    check(
        not retained_replacement_covers_inference(
            [represented_subject_with(
                divider_x=malformed_order_comb["divider_x"],
                new_slot_x=malformed_order_comb["slot_x"])],
            represented_cell, malformed_order_comb),
        "mutually descending slot evidence suppressed an inference",
    )
    malformed_duplicate_comb = {
        **represented_comb,
        "divider_x": [10.0, 10.0],
        "slot_x": [0.0, 10.0, 10.0, 30.0],
    }
    check(
        not retained_replacement_covers_inference(
            [represented_subject_with(
                divider_x=malformed_duplicate_comb["divider_x"],
                new_slot_x=malformed_duplicate_comb["slot_x"])],
            represented_cell, malformed_duplicate_comb),
        "mutually duplicate slot evidence suppressed an inference",
    )
    check(
        not retained_replacement_covers_inference(
            [represented_subject_with(cells=True)],
            represented_cell, represented_comb),
        "a boolean retained cell count suppressed an inference",
    )
    check(
        not retained_replacement_covers_inference(
            [represented_subject_with(new_bbox=[False, 0.0, 30.0, 10.0])],
            represented_cell, represented_comb),
        "a boolean retained coordinate suppressed an inference",
    )

    owned_band_cells, owned_band_subjects = inherited_endpoint_case(
        "owned-endpoint-band", (-5.0, 10.0), (5.0, 10.0), False)
    owned_band_comb = (
        owned_band_cells[0].get("comb")
        if len(owned_band_cells) == 1 else None
    )
    # Two slots, not four: the boundaries at 10 and 20 run past the cell's own
    # rails and are this band's outer edges, so the band owns the one tick
    # between them. What the case is about is the ownership verdict, and that
    # must still be clean.
    check(
        owned_band_comb is not None
        and owned_band_comb.get("cells") == 2
        and owned_band_comb.get("slot_x") == [10.0, 15.0, 20.0]
        and comb_has_cell_owner(owned_band_cells[0], owned_band_comb)
        and "no-final-visible-owned-band" not in (
            owned_band_comb.get("resolution") or {}).get("reason_codes", [])
        and len(owned_band_subjects) == 1,
        "a uniquely owned endpoint band retained a raw-anchor ownership block",
    )

    richer_current_cells, richer_current_subjects = inherited_endpoint_case(
        "owned-richer-current", (-5.0, 10.0), (5.0, 10.0), True)
    richer_current_comb = (
        richer_current_cells[0].get("comb")
        if len(richer_current_cells) == 1 else None
    )
    check(
        richer_current_comb is not None
        and richer_current_comb.get("cells") == 2
        and richer_current_comb.get("slot_x") == [10.0, 15.0, 20.0]
        and "anchor-ownership-disagreement" not in (
            richer_current_comb.get("resolution") or {}).get(
                "reason_codes", [])
        and len(richer_current_subjects) == 1,
        "a uniquely owned richer final band retained an anchor-owner block",
    )

    # Prove the clean acceptance path too: raw full extents cross the cell and
    # are rejected as current anchors, while later white paint leaves one
    # complete, uniquely owned four-slot band inside the cell.
    clipped_final_lines = [
        {
            **synthetic_vertical(x, -5.0, 10.0, 0.2, 90 + index),
            "id": f"clipped-final-{index}",
        }
        for index, x in enumerate((7.5, 15.0, 22.5))
    ]
    clipped_final_knockouts = [
        {
            **synthetic_vertical(
                x, -5.0, 5.0, 0.4, 93 + index, role="knockout"),
            "id": f"clipped-final-knockout-{index}",
        }
        for index, x in enumerate((7.5, 15.0, 22.5))
    ]
    clean_owner_cells, _clean_text, clean_owner_subjects, _clean_inferences = (
        build_cells(
            1, ledger_x, ledger_y, DisjointSet(1),
            [[True], [True]], [[True], [True]],
            [ledger_left, ledger_right], [ledger_top, ledger_bottom],
            clipped_final_lines, clipped_final_lines,
            FinalPaint([
                ledger_left, ledger_right, ledger_top, ledger_bottom,
                *clipped_final_lines, *clipped_final_knockouts,
            ]),
            [],
            legacy_dividers=clipped_final_lines,
            legacy_extra_ink=clipped_final_lines,
        )
    )
    check(
        len(clean_owner_cells) == 1
        and (clean_owner_cells[0].get("comb") or {}).get("cells") == 4
        and len(clean_owner_subjects) == 1
        and clean_owner_subjects[0].get("state") == "active_resolved",
        "a clean uniquely owned final band did not become active-resolved",
    )

    # A thick group divider can paint a short horizontal endpoint cap without
    # producing an internal vertical lattice edge. That cap is safe only when
    # its complete crossed adjacency lies inside the divider's ink corridor.
    cap_left = synthetic_vertical(0, 0, 10, 0.2, 60)
    cap_right = synthetic_vertical(30, 0, 10, 0.2, 61)
    cap_top = synthetic_horizontal(0, 0, 30, 0.2, 62)
    cap_bottom = synthetic_horizontal(10, 0, 30, 0.2, 63)
    cap_divider = synthetic_vertical(15, 5, 10, 2.0, 64)
    cap_rule = synthetic_horizontal(5, 14, 16, 0.2, 65)
    cap_x = Lattice(
        [0.0, 14.0, 16.0, 30.0],
        [-0.1, 13.9, 15.9, 29.9],
        [0.1, 14.1, 16.1, 30.1],
        [[(0.0, 10.0)], [], [], [(0.0, 10.0)]],
        [[cap_left], [], [], [cap_right]],
    )
    cap_y = Lattice(
        [0.0, 5.0, 10.0],
        [-0.1, 4.9, 9.9],
        [0.1, 5.1, 10.1],
        [[(0.0, 30.0)], [(14.0, 16.0)], [(0.0, 30.0)]],
        [[cap_top], [cap_rule], [cap_bottom]],
    )
    cap_box = {
        "j0": 0, "j1": 2, "i0": 0, "i1": 3,
        "component_root": 0, "rectangular": True,
    }
    cap_v_at = [
        [True, True], [False, False], [False, False], [True, True],
    ]
    cap_h_at = [
        [True, True, True], [False, True, False], [True, True, True],
    ]
    cap_paint = FinalPaint([
        cap_left, cap_right, cap_top, cap_bottom, cap_divider, cap_rule,
    ])
    cap_certificate = source_owned_comb_frame(
        cap_box, cap_x, cap_y, cap_v_at, cap_h_at,
        [cap_divider], [cap_divider], cap_paint)
    check(cap_certificate is not None,
          "a fully divider-owned horizontal endpoint cap was not certified")
    cap_h_at[1] = [True, True, False]
    check(source_owned_comb_frame(
        cap_box, cap_x, cap_y, cap_v_at, cap_h_at,
        [cap_divider], [cap_divider], cap_paint) is None,
        "a horizontal edge spanning slot paper was accepted as a comb cap")

    # A short comb tick sharing x with a longer separator does not own the long
    # ink. The grid row carrying the edge overlaps the band, so the old
    # row-interval test passed; the final-visible extent must reject it.
    extent_left = synthetic_vertical(0, 0, 10, 0.2, 70)
    extent_right = synthetic_vertical(30, 0, 10, 0.2, 71)
    extent_top = synthetic_horizontal(0, 0, 30, 0.2, 72)
    extent_bottom = synthetic_horizontal(10, 0, 30, 0.2, 73)
    long_separator = synthetic_vertical(15, 0, 8, 0.2, 74)
    short_tick = synthetic_vertical(15, 7, 10, 0.2, 75)
    extent_x = Lattice(
        [0.0, 15.0, 30.0],
        [-0.1, 14.9, 29.9], [0.1, 15.1, 30.1],
        [[(0.0, 10.0)], [(0.0, 10.0)], [(0.0, 10.0)]],
        [[extent_left], [long_separator, short_tick], [extent_right]],
    )
    extent_y = Lattice(
        [0.0, 7.0, 9.0, 10.0],
        [-0.1, 6.9, 8.0, 9.9], [0.1, 7.1, 10.0, 10.1],
        [[(0.0, 30.0)], [], [], [(0.0, 30.0)]],
        [[extent_top], [], [], [extent_bottom]],
    )
    extent_v_at = [
        [True, True, True], [False, True, False], [True, True, True],
    ]
    extent_h_at = [
        [True, True], [False, False], [False, False], [True, True],
    ]
    extent_paint = FinalPaint([
        extent_left, extent_right, extent_top, extent_bottom,
        long_separator, short_tick,
    ])
    check(source_owned_comb_frame(
        {
            "j0": 0, "j1": 3, "i0": 0, "i1": 2,
            "component_root": 0, "rectangular": True,
        },
        extent_x, extent_y, extent_v_at, extent_h_at,
        [short_tick], [long_separator, short_tick], extent_paint) is None,
        "same-x long separator escaped the certified comb band")
    short_overhang = synthetic_vertical(15, 6.8, 8, 0.2, 76)
    overhang_x = Lattice(
        [0.0, 15.0, 30.0],
        [-0.1, 14.9, 29.9], [0.1, 15.1, 30.1],
        [[(0.0, 10.0)], [(6.8, 10.0)], [(0.0, 10.0)]],
        [[extent_left], [short_overhang, short_tick], [extent_right]],
    )
    overhang_paint = FinalPaint([
        extent_left, extent_right, extent_top, extent_bottom,
        short_overhang, short_tick,
    ])
    check(source_owned_comb_frame(
        {
            "j0": 0, "j1": 3, "i0": 0, "i1": 2,
            "component_root": 0, "rectangular": True,
        },
        overhang_x, extent_y, extent_v_at, extent_h_at,
        [short_tick], [short_overhang, short_tick],
        overhang_paint) is not None,
        "one-weight square divider cap was not attributed to its comb")

    # A non-rectangular component is a partition of row runs, never its broad
    # bounding box. A visible internal vertical also splits a single row even
    # when the DSU component reconnects elsewhere.
    empty_v = [[False, False] for _ in range(4)]
    empty_h = [[False, False, False] for _ in range(3)]
    l_shape = rectangular_row_runs(
        [(0, 0), (0, 1), (0, 2), (1, 0)], empty_v, empty_h)
    check(l_shape == [
        {"j0": 0, "j1": 1, "i0": 0, "i1": 3, "rectangular": True},
        {"j0": 1, "j1": 2, "i0": 0, "i1": 1, "rectangular": True},
    ], f"non-rectangular row-run partition is wrong: {l_shape}")
    split_v = [[False], [True], [False]]
    split_row = rectangular_row_runs([(0, 0), (0, 1)], split_v,
                                     [[False, False], [False, False]])
    check(split_row == [
        {"j0": 0, "j1": 1, "i0": 0, "i1": 1, "rectangular": True},
        {"j0": 0, "j1": 1, "i0": 1, "i1": 2, "rectangular": True},
    ], f"row-run partition crossed a painted vertical: {split_row}")

    # A component can occupy a full rectangular 2x2 bbox and still reconnect
    # around a painted partial separator. build_cells itself must partition it.
    test_verticals = [
        synthetic_vertical(x, 0, 20, 0.2, 100 + index)
        for index, x in enumerate((0.0, 10.0, 20.0))
    ]
    test_horizontals = [
        synthetic_horizontal(y, 0, 20, 0.2, 110 + index)
        for index, y in enumerate((0.0, 10.0, 20.0))
    ]
    partition_x = Lattice(
        [0.0, 10.0, 20.0], [-0.1, 9.9, 19.9], [0.1, 10.1, 20.1],
        [[(0.0, 20.0)] for _ in range(3)],
        [[rule] for rule in test_verticals])
    partition_y = Lattice(
        [0.0, 10.0, 20.0], [-0.1, 9.9, 19.9], [0.1, 10.1, 20.1],
        [[(0.0, 20.0)] for _ in range(3)],
        [[rule] for rule in test_horizontals])
    partition_v_at = [
        [True, True],
        [True, False],
        [True, True],
    ]
    partition_h_at = [
        [True, True],
        [False, False],
        [True, True],
    ]
    partition_dsu = DisjointSet(4)
    partition_dsu.union(0, 2)
    partition_dsu.union(2, 3)
    partition_dsu.union(3, 1)
    # This looks like a divider candidate, but it lands on the internal seam
    # rather than the outer frame baseline. It must exercise and fail the
    # framed-comb preservation certificate, not bypass it via dividers=[].
    incomplete_frame_divider = synthetic_vertical(10, 0, 10, 0.2, 120)
    partition_cells, _texts, _subjects, _inferences = build_cells(
        1, partition_x, partition_y, partition_dsu,
        partition_v_at, partition_h_at,
        test_verticals, test_horizontals,
        [incomplete_frame_divider], [incomplete_frame_divider],
        FinalPaint([
            *test_verticals, *test_horizontals, incomplete_frame_divider,
        ]), [])
    check([
        (cell["x0"], cell["y0"], cell["x1"], cell["y1"])
        for cell in partition_cells
    ] == [
        (0.0, 0.0, 10.0, 10.0),
        (10.0, 0.0, 20.0, 10.0),
        (0.0, 10.0, 20.0, 20.0),
    ], f"build_cells crossed a partial painted separator: {partition_cells}")
    check(not any(crosses_painted_internal_edge({
        "j0": cell["row"], "j1": cell["row"] + cell["row_span"],
        "i0": cell["col"], "i1": cell["col"] + cell["col_span"],
    }, partition_v_at, partition_h_at) for cell in partition_cells),
          "an emitted build_cells rectangle crosses painted internal ink")

    # Painted-bound ownership admits a baseline-crossing tick to its row but
    # refuses a band contained wholly in the shared boundary ink of two rows.
    test_x = Lattice([0.0, 30.0], [-0.5, 29.5], [0.5, 30.5],
                     [[], []], [[], []])
    test_y = Lattice([0.0, 10.0, 20.0], [-0.5, 9.5, 19.5],
                     [0.5, 10.5, 20.5], [[], [], []], [[], [], []])
    owner_cells = [
        {"x0": 0.0, "y0": 0.0, "x1": 30.0, "y1": 10.0,
         "row": 0, "col": 0, "row_span": 1, "col_span": 1},
        {"x0": 0.0, "y0": 10.0, "x1": 30.0, "y1": 20.0,
         "row": 1, "col": 0, "row_span": 1, "col_span": 1},
    ]
    baseline_tick = synthetic_vertical(15, 5, 10.5, 0.2, 10)
    shared_tick = synthetic_vertical(15, 9.6, 10.4, 0.2, 11)
    owner_paint = FinalPaint([baseline_tick, shared_tick])
    buckets, _unplaced, ambiguous = assign_comb_anchors(
        owner_cells, [baseline_tick, shared_tick], test_x, test_y, owner_paint)
    check(buckets == [[baseline_tick], []],
          f"painted-bound anchor ownership is wrong: {buckets}")
    check(ambiguous == [shared_tick],
          "shared-boundary anchor was guessed instead of left ambiguous")
    check(comb_band_owners(
        owner_cells, 0.0, 30.0, 9.6, 10.4, test_x, test_y) == [0, 1],
        "shared final-visible band did not retain both possible owners")

    # Outlined glyphs can contribute rectilinear stems that look exactly like
    # hanging ticks. Their curved path continuing above the tick disqualifies
    # the subject without knowing a form code or a glyph.
    glyph_band = {
        "y0": 7.0, "y1": 10.0,
        "divider_x": [15.0], "divider_thicknesses_pt": [0.2],
        "divider_paint_seq": [10],
    }
    glyph_path = {
        "id": "glyph-path",
        "x0": 14.95, "x1": 15.05, "y0": 2.0, "y1": 7.05,
        "fill": [0.0, 0.0, 0.0], "fill_gray": 0.0,
        "stroke": None, "stroke_gray": None, "stroke_width_pt": 0.0,
        "even_odd": False, "role": "structural",
        "paint_seq": 11, "paint_seq_max": 11,
        "subpaths": [{
            "start": [14.95, 2.0], "closed": True,
            "ops": [
                {"op": "l", "points": [15.05, 2.0]},
                {"op": "l", "points": [15.05, 7.05]},
                {"op": "l", "points": [14.95, 7.05]},
                {"op": "l", "points": [14.95, 2.0]},
            ],
        }],
    }
    check(path_endpoint_conflicts(FinalPaint([glyph_path]), glyph_band),
          "non-rectilinear glyph continuation was not flagged unresolved")
    earlier_path = {
        **glyph_path,
        "paint_seq": 9,
        "paint_seq_max": 9,
    }
    check(not path_endpoint_conflicts(FinalPaint([earlier_path]), glyph_band),
          "an earlier path incorrectly overruled a later divider")
    compartment_path = {
        **glyph_path,
        "id": "compartment-path",
        # Its bbox reaches the divider corridor, but the actual triangle stays
        # to the right. A bbox-only ownership test gets this wrong.
        "x0": 14.9, "x1": 20.0,
        "subpaths": [{
            "start": [16.0, 2.0], "closed": True,
            "ops": [
                {"op": "l", "points": [20.0, 2.0]},
                {"op": "l", "points": [20.0, 7.05]},
                {"op": "l", "points": [16.0, 2.0]},
            ],
        }],
    }
    check(not path_endpoint_conflicts(
        FinalPaint([compartment_path]), glyph_band),
        "outlined content inside a slot was mistaken for a divider continuation")
    reduction_conflict_path = {
        **glyph_path,
        "id": "reduction-conflict-path",
        "paint_seq": 47, "paint_seq_max": 47,
    }
    conflict_cells, conflict_subjects = erased_reduction_ledger_case(
        "path-conflict-erasure", 44, [reduction_conflict_path])
    conflict_comb = (
        conflict_cells[0].get("comb") if len(conflict_cells) == 1 else None)
    check(
        conflict_comb is not None
        and conflict_comb.get("cells") == 3
        and "final-visible-count-regression"
        in (conflict_comb.get("resolution") or {}).get("reason_codes", [])
        and len(conflict_subjects) == 1
        and conflict_subjects[0].get("state") == "active_unresolved",
        "a later nonrect endpoint conflict certified a count reduction",
    )
    omitted_repaint_path = {
        "id": "omitted-path-repaint",
        "x0": 19.9, "x1": 20.1, "y0": 5.0, "y1": 10.0,
        "fill": [0.0, 0.0, 0.0], "fill_gray": 0.0,
        "stroke": None, "stroke_gray": None, "stroke_width_pt": 0.0,
        "even_odd": False, "role": "structural",
        "paint_seq": 47, "paint_seq_max": 47,
        "subpaths": [{
            "start": [19.9, 5.0], "closed": True,
            "ops": [{"op": "re", "points": [19.9, 5.0, 20.1, 10.0]}],
        }],
    }
    omitted_conflict_cells, omitted_conflict_subjects = (
        erased_reduction_ledger_case(
            "omitted-path-conflict-erasure", 44,
            [omitted_repaint_path]))
    omitted_conflict_comb = (
        omitted_conflict_cells[0].get("comb")
        if len(omitted_conflict_cells) == 1 else None)
    check(
        omitted_conflict_comb is not None
        and omitted_conflict_comb.get("cells") == 3
        and "final-visible-count-regression"
        in (omitted_conflict_comb.get("resolution") or {}).get(
            "reason_codes", [])
        and len(omitted_conflict_subjects) == 1
        and omitted_conflict_subjects[0].get("state") == "active_unresolved",
        "a later structural path at an omitted x certified a count reduction",
    )
    path_knockout = {
        **glyph_path,
        "id": "path-knockout",
        "fill": [1.0, 1.0, 1.0],
        "fill_gray": 1.0,
        "role": "knockout",
        "paint_seq": 12,
        "paint_seq_max": 12,
    }
    path_target = synthetic_vertical(15, 2, 7, 0.1, 1)
    check(not FinalPaint([path_target, path_knockout]).visible_intervals(path_target),
          "a later nonrect path knockout did not remove stale structural ink")
    hole_target = synthetic_vertical(5, 0, 10, 0.8, 1)
    compound_knockout = {
        "id": "compound-knockout",
        "x0": 0.0, "x1": 10.0, "y0": 0.0, "y1": 10.0,
        "fill": [1.0, 1.0, 1.0], "fill_gray": 1.0,
        "stroke": None, "stroke_gray": None, "stroke_width_pt": 0.0,
        "even_odd": True, "role": "knockout",
        "paint_seq": 2, "paint_seq_max": 2,
        "subpaths": [
            {
                "start": [0.0, 0.0], "closed": True,
                "ops": [{"op": "re", "points": [0.0, 0.0, 10.0, 10.0]}],
            },
            {
                "start": [4.8, 1.0], "closed": True,
                "ops": [{"op": "re", "points": [4.8, 1.0, 5.2, 3.0]}],
            },
        ],
    }
    compound_paint = FinalPaint([hole_target, compound_knockout])
    compound_layer = compound_paint.path_paints[0]
    old_samples = [
        (4.6, 0.0), (5.4, 0.0), (5.4, 10.0), (4.6, 10.0),
        (5.0, 5.0),
    ]
    check(all(point_in_path(compound_layer, point)
              for point in old_samples)
          and not point_in_path(compound_layer, (5.0, 2.0)),
          "compound knockout fixture does not expose the unsampled hole")
    check(not compound_paint.definitely_erased(hole_target),
          "five sampled path points falsely proved full erasure over a hole")
    rectangular_knockout = {
        **compound_knockout,
        "id": "rectangular-knockout",
        "even_odd": False,
        "subpaths": [compound_knockout["subpaths"][0]],
    }
    check(FinalPaint([
        hole_target, rectangular_knockout,
    ]).definitely_erased(hole_target),
        "one exact covering rectangle did not prove path erasure")
    hole_witness = {**hole_target, "id": "hole-target"}
    hole_retained = {
        **synthetic_vertical(8.0, 0.0, 10.0, 0.2, 3),
        "id": "hole-retained",
    }
    hole_legacy = {
        "cells": 3, "divider_x": [5.0, 8.0],
        "slot_x": [0.0, 5.0, 8.0, 10.0],
        "y0": 0.0, "y1": 10.0,
    }
    hole_final = {
        "cells": 2, "divider_x": [8.0],
        "slot_x": [0.0, 8.0, 10.0],
        "y0": 0.0, "y1": 10.0,
        "resolution": {"status": "resolved"},
    }
    check(erased_legacy_divider_reduction_certificate(
        hole_legacy, hole_final, [hole_witness, hole_retained],
        FinalPaint([
            hole_witness, compound_knockout, hole_retained,
        ])) is None,
        "a compound path bbox with a hole certified a count reduction")
    check(erased_legacy_divider_reduction_certificate(
        hole_legacy, hole_final, [hole_witness, hole_retained],
        FinalPaint([
            hole_witness, rectangular_knockout, hole_retained,
        ])) is not None,
        "an exact rectangular path erasure failed the count certificate")
    swept_target = {
        **synthetic_vertical(0.5, 0, 10, 1.0, 1),
        "x0": 0.0, "x1": 1.0,
    }
    diagonal_knockout = {
        "id": "diagonal-knockout",
        "x0": -0.1, "x1": 1.1, "y0": 0.0, "y1": 10.0,
        "fill": [1.0, 1.0, 1.0], "fill_gray": 1.0,
        "stroke": None, "stroke_gray": None, "stroke_width_pt": 0.0,
        "even_odd": False, "role": "knockout",
        "paint_seq": 2, "paint_seq_max": 2,
        "subpaths": [{
            "start": [-0.1, 0.0], "closed": True,
            "ops": [
                {"op": "l", "points": [0.1, 0.0]},
                {"op": "l", "points": [1.1, 10.0]},
                {"op": "l", "points": [0.9, 10.0]},
                {"op": "l", "points": [-0.1, 0.0]},
            ],
        }],
    }
    swept_paint = FinalPaint([swept_target, diagonal_knockout])
    check(not swept_paint.visible_intervals(swept_target)
          and not swept_paint.structural_across(swept_target, 0.0, 10.0),
          "moving nonrect knockout was certified from one midpoint section")

    # A lone unequal split and two anchor runs separated by an interior
    # multi-slot gap are not coherent single combs. They remain present but
    # explicitly unresolved until an independent referee adjudicates them --
    # PROVIDED every compartment is still character-box sized. This fixture
    # originally split 95/5 in a 100pt band; DECISION A (2026-08-16) now
    # refuses that outright at construction (a 95pt compartment is not a
    # character box -- the compartment-rule fixtures at the top assert it),
    # so the retained-unresolved contract is asserted just under the bound:
    # 20/10 trips the same unequal-two-slot relation with both compartments
    # legitimate widths.
    unequal = synthetic_vertical(20, 0, 10, 0.2, 20)
    unequal_bands = comb_bands(
        [unequal], [unequal], 0, 30, (1.0, 1.0),
        FinalPaint([unequal]))
    check(bool(unequal_bands)
          and unequal_bands[0]["resolution"]["status"] == "unresolved"
          and "unequal-two-slot-topology"
          in unequal_bands[0]["resolution"]["reason_codes"],
          "one unequal divider was not retained as unresolved")
    split_run = [synthetic_vertical(x, 0, 10, 0.2, 30 + n)
                 for n, x in enumerate((10, 20, 80, 90))]
    split_bands = comb_bands(
        split_run, split_run, 0, 100, (1.0, 1.0),
        FinalPaint(split_run))
    check(bool(split_bands)
          and split_bands[0]["resolution"]["status"] == "unresolved"
          and "split-anchor-run-topology"
          in split_bands[0]["resolution"]["reason_codes"],
          "two separated anchor runs were not retained as unresolved")

    check(layout["form"]["code"] == "2551Q", "form code is not 2551Q")
    check(len(layout["pages"]) == 2, "expected 2 pages")

    for page in layout["pages"]:
        n = page["index"]
        check(bool(page["cells"]), f"page {n} produced no cells")
        check(bool(page["regions"]), f"page {n} produced no regions")
        check(any("comb" in c for c in page["cells"]), f"page {n} found no comb cell")
        check(not any(not c["rectangular"] for c in page["cells"]),
              f"page {n} retained a non-rectangular cell")
        check(len({cell["id"] for cell in page["cells"]}) == len(page["cells"]),
              f"page {n} contains duplicate stable cell ids")
        check(all(
            cell["subject_key"] == geometry_subject_key(
                n, (cell["x0"], cell["y0"], cell["x1"], cell["y1"]))
            for cell in page["cells"]),
            f"page {n} contains a geometry subject-key mismatch")
        check(page["stats"]["comb_subjects"]
              == (page["stats"]["comb_subjects_active"]
                  + page["stats"]["comb_subjects_retained_unresolved"]),
              f"page {n} subject ledger does not reconcile")
        check(page["stats"]["comb_subjects_active"]
              == (page["stats"]["comb_subjects_active_resolved"]
                  + page["stats"]["comb_subjects_active_unresolved"]),
              f"page {n} active subject states do not reconcile")
        check(page["stats"]["comb_subjects_retired"] == 0,
              f"page {n} silently retired a comb subject")
        check(page["stats"]["comb_evidence_blocking"]
              == (page["stats"]["comb_subjects_blocking"]
                  + page["stats"]["comb_inferences_blocking"]),
              f"page {n} blocking evidence ledger does not reconcile")
        check(page["stats"]["cells_geometry_unresolved"]
              == sum(bool(cell.get("geometry_resolution"))
                     for cell in page["cells"]),
              f"page {n} geometry uncertainty count does not reconcile")

        # Every cell coordinate must be a lattice position, exactly.
        xs, ys = set(page["x_lattice"]), set(page["y_lattice"])
        off = [c["id"] for c in page["cells"]
               if c["x0"] not in xs or c["x1"] not in xs
               or c["y0"] not in ys or c["y1"] not in ys]
        check(not off, f"page {n} cells off the lattice: {off[:5]}")

        # A comb must never have been shattered into per-character cells.
        for cell in page["cells"]:
            comb = cell.get("comb")
            if comb:
                check(comb["cells"] == comb["divider_count"] + 1,
                      f"{cell['id']} comb slot count disagrees with its dividers")
                check(comb["slot_x"] == sorted(comb["slot_x"]),
                      f"{cell['id']} comb slot boundaries are not ascending")

    # The comb discriminator is geometric, so its split is reproducible to the
    # rule. These are the counts the recon pass measured on this exact PDF.
    page1, page2 = layout["pages"]
    ir1, ir2 = ir["pages"]
    check(page1["stats"]["comb_slots"] == 489,
          f"page 1: expected 489 comb slots, got {page1['stats']['comb_slots']}")
    check(page2["stats"]["comb_slots"] == 264,
          f"page 2: expected 264 comb slots, got {page2['stats']['comb_slots']}")

    framed = [
        cell for cell in page1["cells"]
        if [cell["x0"], cell["y0"], cell["x1"], cell["y1"]]
        == [362.71, 283.97, 590.16, 303.29]
    ]
    check(len(framed) == 1,
          "page 1: framed 16-slot composite was partitioned or duplicated")
    if framed:
        certificate = framed[0].get("comb_frame_certificate") or {}
        expected_dividers = [
            376.99, 391.15, 405.31, 419.59, 433.75,
            447.91, 462.1, 476.38, 490.54, 504.7,
            518.98, 533.14, 547.42, 561.7, 575.74,
        ]
        check(certificate.get("method") == "final-visible-framed-comb"
              and certificate.get("band_y") == [295.73, 303.05]
              and certificate.get("divider_x") == expected_dividers
              and (framed[0].get("comb") or {}).get("cells") == 16,
              f"page 1: invalid framed-comb certificate {certificate}")

    def thin_combs(page: dict[str, Any], ir_page: dict[str, Any]) -> int:
        ids = set(page["comb_divider_ids"])
        return sum(1 for r in ir_page["rules"] if r["id"] in ids and r["thickness_pt"] == 0.24)

    check(thin_combs(page1, ir1) == 377,
          f"page 1: expected 377 0.24pt comb dividers, got {thin_combs(page1, ir1)}")
    check(thin_combs(page2, ir2) == 195,
          f"page 2: expected 195 0.24pt comb dividers, got {thin_combs(page2, ir2)}")
    # Thickness inside a comb is rank, not membership: the money combs mix
    # 0.24pt character dividers with 1.44pt thousands separators in one field.
    check(any(max(c["comb"]["divider_thicknesses_pt"]) > 0.24
              for c in page2["cells"] if "comb" in c),
          "page 2: no comb with digit-group separators heavier than 0.24pt")

    # Schedule 1 -- Computation of Tax. The recon pass established that this
    # band is on PAGE 2, not page 1: page 1 is masthead + Parts I-III, all of
    # them fixed-height, and it must carry no growable at all.
    check(not page1["growable"], f"page 1 should have no growable, got {page1['growable']}")

    atc = [g for g in page2["growable"] if abs(g["y0"] - 162.26) <= CLUSTER_TOL_PT]
    check(bool(atc), "page 2: Schedule 1 growable band not found at y=162.26")
    if atc:
        band = atc[0]
        check(band["row_count"] == 6, f"Schedule 1 row count {band['row_count']} != 6")
        check(band["capacity"] == 6, f"Schedule 1 capacity {band['capacity']} != 6")
        check(band["row_pitch_pt"] == 18.24,
              f"Schedule 1 pitch {band['row_pitch_pt']} != 18.24")
        # Row 6 is 18.27pt: the band is regular but not uniform.
        check(band["row_pitch_max_pt"] == 18.27,
              f"Schedule 1 max pitch {band['row_pitch_max_pt']} != 18.27")
        check(band["row_y"][0] == 162.26 and band["row_y"][-1] == 271.73,
              f"Schedule 1 row_y endpoints wrong: {band['row_y'][0]}..{band['row_y'][-1]}")
        expected_columns = [23.04, 37.2, 108.14, 278.21, 292.37, 320.69,
                            349.15, 363.31, 533.5, 547.54, 575.98, 590.14]
        check(band["column_x"] == expected_columns,
              f"Schedule 1 columns {band['column_x']} != {expected_columns}")
        check(len(band["template_cell_ids"]) == 11,
              f"Schedule 1 template row has {len(band['template_cell_ids'])} cells, expected 11")
        # The 12-slot money combs are the whole point: one cell, twelve slots.
        combs = [c["comb"]["cells"] for c in page2["cells"]
                 if c["id"] in set(band["template_cell_ids"]) and "comb" in c]
        check(sorted(combs) == [2, 2, 2, 5, 12, 12],
              f"Schedule 1 template comb shapes {sorted(combs)} != [2, 2, 2, 5, 12, 12]")

    # `printed_partitions`: the strokes the source rules ACROSS a cell, which
    # emit.py cuts a writing box at. Four mutations of one fixture, each
    # tripping exactly one clause -- the whole cell is 0..100 x 0..20, the
    # divider is a 0.5pt stroke down the middle, and every variant below
    # changes ONE thing about it.
    partition_cell = {"id": "p0c0", "kind": "field",
                      "x0": 0.0, "y0": 0.0, "x1": 100.0, "y1": 20.0}
    inside = synthetic_vertical(50.0, 12.0, 20.0, 0.5, 1)
    on_the_edge = synthetic_vertical(100.0, 12.0, 20.0, 0.5, 2)
    outside = synthetic_vertical(50.0, 30.0, 40.0, 0.5, 3)
    over_it = {
        **inside,
        "x0": q(inside["x0"] - 2.0), "x1": q(inside["x1"] + 2.0),
        "y0": q(inside["y0"] - 2.0), "y1": q(inside["y1"] + 2.0),
        "role": "knockout", "gray": 1.0,
        "paint_seq": 9, "paint_seq_max": 9,
    }
    partition_paint = FinalPaint(
        [inside, on_the_edge, outside, over_it])
    intact_paint = FinalPaint([inside, on_the_edge, outside])
    intact = printed_partitions(
        partition_cell, [inside, on_the_edge, outside], intact_paint)
    check(intact == [{"x0": q(49.75), "y0": 12.0, "x1": q(50.25), "y1": 20.0}],
          f"a stroke inside the cell is the cell's partition, and nothing "
          f"else is: {intact}")
    erased = printed_partitions(
        partition_cell, [inside, on_the_edge, outside], partition_paint)
    check(erased == [],
          f"a stroke one later opaque layer covers whole is off the paper "
          f"and partitions nothing: {erased}")
    walled = printed_partitions(partition_cell, [on_the_edge], intact_paint)
    shifted_in = synthetic_vertical(99.5, 12.0, 20.0, 0.5, 2)
    walled_inside = printed_partitions(
        partition_cell, [shifted_in], intact_paint)
    check(walled == [] and len(walled_inside) == 1,
          f"a stroke whose ink crosses the cell edge is the cell's own wall, "
          f"not a partition -- the same stroke 0.25pt further in is one: "
          f"{walled} against {walled_inside}")
    combed = printed_partitions(
        {**partition_cell, "comb": {"cells": 2, "slot_x": [0.0, 50.0, 100.0]}},
        [inside], intact_paint)
    check(combed == [],
          f"a comb states its compartments in slot_x and gets no second "
          f"answer here: {combed}")

    # bridge_knockout_bites: F097's knockout-bitten walls (2200C p1 item 1
    # "Date (MM/DD/YYYY)", 2000-DST p1). Each fixture is one of the three
    # shapes bridge_knockout_bites's docstring names, at the corpus's own
    # measured geometry, plus the size bound.
    def bite_lattice(spans: list[Interval]) -> Lattice:
        return Lattice([50.0], [0.0], [20.0], [list(spans)], [[]])

    bite_witness = synthetic_vertical(50.0, 10.0, 11.5, 0.48, 90,
                                      role="knockout")
    positive = bite_lattice([(0.0, 10.0), (11.5, 20.0)])
    positive_count = bridge_knockout_bites(positive, [bite_witness], "v", 3.0)
    check(positive_count == 1,
          f"a same-axis knockout exactly filling a 1.5pt gap (2200C p1's "
          f"own gap) was not bridged: count {positive_count}")
    check(covers(positive.spans[0], 0.0, 20.0),
          f"a bridged rail did not cover its own joint band: "
          f"{positive.spans[0]}")

    bare_paper = bite_lattice([(0.0, 10.0), (11.5, 20.0)])
    check(bridge_knockout_bites(bare_paper, [], "v", 3.0) == 0,
          "a gap with no knockout evidence at all was bridged")

    # 2200A p1 x0=580.66: the only white at the gap is the PERPENDICULAR
    # horizontal rule h26, which severs the column into a comb divider on
    # purpose. Same-axis is what tells this from a bite.
    junction_gap = bite_lattice([(0.0, 10.0), (10.48, 20.0)])
    junction_witness = synthetic_horizontal(10.24, 40.0, 60.0, 0.48, 91,
                                            role="knockout")
    check(bridge_knockout_bites(junction_gap, [junction_witness], "v", 3.0)
          == 0,
          "a perpendicular-axis knockout bridged a same-line gap")

    # 1800-2018 p1 y=805.3 / 1604e-2018 p1 y=383.6: the knockout beside the
    # gap mirrors the black fragment's OWN range and ends at the gap's edge
    # instead of covering it -- an abutting witness, not a filling one.
    abutting_gap = bite_lattice([(0.0, 10.0), (10.24, 20.0)])
    abutting_witness = synthetic_vertical(50.0, 10.24, 20.0, 0.24, 92,
                                          role="knockout")
    check(bridge_knockout_bites(abutting_gap, [abutting_witness], "v", 3.0)
          == 0,
          "a knockout that only abuts a gap edge was scored as covering it")

    # A 5.0pt gap under a 3.0pt glyph-height bound is a doorway, not a bite,
    # even with an exact covering witness.
    doorway_gap = bite_lattice([(0.0, 10.0), (15.0, 20.0)])
    doorway_witness = synthetic_vertical(50.0, 10.0, 15.0, 0.48, 93,
                                         role="knockout")
    check(bridge_knockout_bites(doorway_gap, [doorway_witness], "v", 3.0)
          == 0,
          "a 5.0pt doorway was bridged under a 3.0pt glyph-height bound")

    zero_bound = bite_lattice([(0.0, 10.0), (11.5, 20.0)])
    check(bridge_knockout_bites(zero_bound, [bite_witness], "v", 0.0) == 0,
          "a zero glyph-height bound (metrics unavailable) still bridged")

    # Determinism: the same IR must serialise byte-identically.
    again = json.dumps(build_layout(ir), sort_keys=False, ensure_ascii=False)
    check(again == json.dumps(layout, sort_keys=False, ensure_ascii=False),
          "layout is not deterministic across two builds")

    for message in failures:
        print(f"FAIL {message}", file=sys.stderr)
    print_summary(layout, sys.stderr)
    print(f"self-test: {'PASS' if not failures else f'{len(failures)} FAILURE(S)'}",
          file=sys.stderr)
    return 1 if failures else 0


def print_summary(layout: dict[str, Any], stream: Any) -> None:
    form = layout["form"]
    print(f"{form['code']} rev {form['revision']}  layout schema {layout['schema_version']}",
          file=stream)
    for page in layout["pages"]:
        s = page["stats"]
        print(f"  page {page['index']}: lattice {s['x_lattice']}x{s['y_lattice']}  "
              f"cells {s['cells']} ({s['cells_non_rectangular']} non-rect)  "
              f"regions {s['regions']}  growables {s['growables']}  "
              f"comb cells {s['comb_cells']} ({s['comb_slots']} slots from "
              f"{s['comb_dividers']} dividers)", file=stream)
        print(f"           kinds {s['cell_kinds']}  "
              f"decorative {s['decorative_rules']}  "
              f"text {s['text_runs']} ({s['text_runs_unassigned']} outside every cell)",
              file=stream)
        for g in page["growable"]:
            print(f"           growable {g['id']}: {g['row_count']} rows x "
                  f"{len(g['column_x'])} columns, pitch {g['row_pitch_pt']}pt "
                  f"({g['row_pitch_min_pt']}-{g['row_pitch_max_pt']}), "
                  f"y {g['y0']}->{g['y1']}, capacity {g['capacity']}", file=stream)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--ir", required=True, type=pathlib.Path,
                        help="IR JSON produced by tools/formgen/extract.py")
    parser.add_argument("--out", type=pathlib.Path, default=None,
                        help="Write layout JSON here (default: stdout).")
    parser.add_argument("--summary", action="store_true",
                        help="Print a per-page summary to stderr.")
    parser.add_argument("--self-test", action="store_true",
                        help="Run assertions against the given IR and exit non-zero on failure.")
    args = parser.parse_args(argv)

    if not args.ir.is_file():
        print(f"no such IR: {args.ir}", file=sys.stderr)
        return 2

    if args.self_test:
        return self_test(args.ir)

    ir = json.loads(args.ir.read_text(encoding="utf-8"))
    layout = build_layout(ir)
    payload = json.dumps(layout, indent=2, sort_keys=False, ensure_ascii=False) + "\n"

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(payload, encoding="utf-8")
    else:
        sys.stdout.write(payload)

    if args.summary:
        print_summary(layout, sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
