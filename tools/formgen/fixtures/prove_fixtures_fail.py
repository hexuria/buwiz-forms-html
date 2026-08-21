#!/usr/bin/env python3
"""Break the fixture corpus at the source and watch extract.py's checks trip.

A fixture that cannot fail is a decoration. `extract.py --self-test` already
proves each check can fail by mutating the *evidence* -- the extracted IR, in
memory. That answers "is this check wired to its subject" but not "does the
fixture actually contain the structure the check is about": a corpus missing the
structure entirely would extract to nothing, the check would find nothing to
disagree with, and it would pass.

So this mutates the fixture PDFs instead. For each case it patches one primitive
in make_fixtures.py, rebuilds the whole corpus into a temporary directory,
re-pins it to its own new digests (so the sha256 mismatch cannot stand in for
the real result), and runs every check. Exactly one check must trip, and it must
be the expected one -- a mutation that trips two means a check is standing in
for another, and a mutation that trips none means the fixture never carried the
property.

Eight cases reach past make_fixtures.py, and say so. Their subjects are not
forms but five pages extract.py writes itself, one per question no PDF in either
corpus states: ten painting ops with three of them drawn outside their own
scissor; seven strokes stating every case of the `J` operator at once, because
no fixture draws a round or projecting cap; a page of underscore runs, because
the corpus carries no two-underscore punctuation beside a blank and no
unresolvable face; a page whose text operators put two baselines inside one
rawdict span; and a page that sets one string five times -- including once from
a font program it EMBEDS -- so that the face, the stated advances and the text
matrix are the only things that can decide whether a glyph's outline is
measurable. Nothing in the corpus feeds those checks, so no fixture can break
them -- and a fixture that carried a clip, a cap, a sub-floor blank, a doubled
baseline, an unresolvable face or an embedded program would be ink no check
reads, which is the decoration this file exists to catch. Those cases patch the
probe pages' own source instead. Each is still a mutation of the PDF a check
measures rather than of the evidence that PDF produced, which is the property
the rest of this file rests on.

Six of extract.py's checks are deliberately NOT reachable this way; see
CONTRACT_ONLY. They are statements about the extractor's own output contract
rather than about corpus content, and no PDF can violate them. Naming them here
is what stops this file from looking like complete coverage: the case table and
that list must between them account for every check extract.py declares, and
this exits non-zero if they do not.

`prove_row_number` (F151, P2's row-number rule) runs the identical method one
stage past extract.py: a `label` cell sharing its row with a `field` cell,
holding only a short numeral, earns the paper beside it when that blank clears
the form's own `line_width_pt` at 1.0x -- a `lattice.py`/`emit.py` decision
with no new extract-level primitive, so it is proven by mutating the source and
running `lattice.build_layout` + `emit.RowNumberWriting` over it, outside
`prove()`'s own CASES/CONTRACT_ONLY accounting (which is specifically about
`extract.SELF_TEST_CHECKS`, and would misname this if folded in).

Usage:
    python3 tools/formgen/fixtures/prove_fixtures_fail.py
"""

from __future__ import annotations

import argparse
import hashlib
import pathlib
import sys
import tempfile
from typing import Any, Callable, Sequence

FIXTURE_ROOT = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(FIXTURE_ROOT.parent))

try:
    import fitz  # PyMuPDF  # noqa: F401 - imported for the same guard as below
except ImportError:  # pragma: no cover - environment guard
    sys.exit("PyMuPDF is required: pip install pymupdf")

import extract  # noqa: E402 - the path has to be set up first
import lattice  # noqa: E402
import emit  # noqa: E402
import make_fixtures as fixtures  # noqa: E402

# Checks whose subject is the extractor's output contract, not the corpus. No
# PDF can make a rule forget its own paint_seq or make two extractions of one
# file differ, so these have no source-level mutation and are proven only by
# extract.py's own in-memory probes.
CONTRACT_ONLY = {
    "determinism": "two extractions of one file; no PDF can make them differ",
    "paint-seq": "every emitted item carries an ordinal, by construction",
    "paint-spans": "the contributor contract is emitted, not read from the PDF",
    "interval-provenance": "measured on a synthetic interval list, not a file",
    "paint-order-reconciliation": "probes a deliberately desynced argument",
    "glyph-ink-fail-closed":
        "the residue after the ink probe took the embedded-face case: every "
        "face MuPDF loads from a buffer answers glyph_bbox with its own "
        "Font.bbox, so two embedded faces CANNOT disagree about a glyph's "
        "outline and two unembedded resources naming one BaseFont resolve to "
        "the same base-14 face -- no PDF can state a disagreement or an empty "
        "outline for a claimed character. Nor can make_fixtures.py or any PDF "
        "make a run publish a box for a character it does not set",
}


# ---------------------------------------------------------------------------
# The source-level mutations
# ---------------------------------------------------------------------------


def mutate_paper() -> None:
    """Print every fixture on Letter instead of folio."""
    fixtures.PAGE_HEIGHT_PT = 792.0


def mutate_paths() -> None:
    """Never draw the first "write here" marker, so one triangle is missing."""
    original = fixtures.right_triangle
    drawn = [0]

    def one_fewer(*args: Any, **kwargs: Any) -> None:
        drawn[0] += 1
        if drawn[0] == 1:
            return
        original(*args, **kwargs)

    fixtures.right_triangle = one_fewer


def mutate_soft_masks() -> None:
    """Build every fixture image without alpha, so no soft mask is written."""
    original = fixtures.checkerboard

    def flattened(width: int, height: int, rgb: tuple[int, int, int],
                  alpha: Callable[[int, int], int]) -> Any:
        return fitz.Pixmap(original(width, height, rgb, alpha), 0)

    fixtures.checkerboard = flattened


def mutate_transforms() -> None:
    """Leave the seal on insert_image()'s own positive-diagonal placement."""
    fixtures.flip_placement = lambda doc, page, box: None


def mutate_codepoints() -> None:
    """Emit no unmappable glyph, so the honesty field has nothing to record."""
    fixtures.insert_unmappable_glyph = lambda doc, page: None


def mutate_tone() -> None:
    """Paint both decorative greys black -- the documented past failure."""
    fixtures.GREY_LIGHT = 0.0
    fixtures.GREY_MID = 0.0


def mutate_checkbox_square() -> None:
    """Slide the checkbox square's knockout 3pt off its frame's centreline.

    No rule's tone moves -- the frame stays exactly as decorative as it was
    -- so `check_tone`'s corpus-wide census is untouched and only
    `checkbox-square` trips. What breaks is the geometric fact
    `checkbox_square_boxes` (emit.py) actually reads: the knockout fill sits
    on the frame's own centreline, within half the frame's own thickness.
    3pt clears that tolerance by an order of magnitude, so the fixture's
    checkbox stops being a checkbox square by the same measure real evidence
    would -- a knockout printed somewhere the frame does not bound it.
    """
    fixtures.CHECKBOX_SQUARE_KNOCKOUT_DRIFT_PT = 3.0


def mutate_signature_box() -> None:
    """Push the in-box caption 10pt down its own box, across the top-40% line.

    Nothing about the BOX moves -- its rules, thickness and role are
    untouched, so `check_checkbox_square` (a different box on the same page)
    and `check_tone`'s corpus-wide census see nothing -- only the caption
    text's own baseline does, and only enough to clear
    `emit.SIGNATURE_BOX_CAPTION_BAND`'s own top 40% of the box's 40pt height
    by a clear margin (the caption's measured y1 sits 1.31pt inside the line
    today, so 10pt is an order of magnitude past it, not a graze).
    """
    fixtures.SIGNATURE_BOX_CAPTION_DRIFT_PT = 10.0


def mutate_signature_line() -> None:
    """Pull the caption below the box 10pt up, past its own divider rule.

    Nothing about the box's bottom rule moves -- only the caption text's own
    baseline does, and only enough to cross to the near side of it (the
    caption's measured y0 sits 2.57pt below the rule today, so 10pt clears
    it by a clear margin). What breaks is the fact `emit.
    SignatureLineBinding` reads: the caption sits on the far side of the
    wall the box above it also bounds.
    """
    fixtures.SIGNATURE_LINE_CAPTION_DRIFT_PT = -10.0


def mutate_row_number() -> None:
    """Narrow the row-number label cell from 60pt to 20pt.

    Nothing about the numeral's own ink moves, no rule's tone changes, and
    the field cell beside it keeps its own width -- only the label cell's
    own width does, and only enough that the paper trailing the numeral
    (roughly 10.76pt of that 20pt, the rest being the numeral's own ink and
    left margin) drops to about 9.2pt, under HALF of the fixture's own
    18pt `line_width_pt`. What breaks is the geometric fact `row_number_band`
    (emit.py) actually reads: a bare row number's own trailing blank clears
    the form's own two-glyph line width at 1.0x. See `prove_row_number`,
    which is where this trips -- unlike every other case in `CASES`, this
    is not one of `extract.py`'s own checks (row-number is a lattice/emit
    decision with no new extract-level primitive of its own), so it runs
    the pipeline this module already imports rather than
    `extract.SELF_TEST_CHECKS`.
    """
    fixtures.ROW_NUMBER_LABEL_WIDTH_PT = 20.0


def mutate_bar_like() -> None:
    """Draw the separators exactly vertical, so none of them leans at all."""
    fixtures.LEAN_OFFSET_PT = 0.0


def mutate_clips() -> None:
    """Build the probe page with no scissor: every `W n` becomes a plain `n`.

    The path is still constructed and still discarded, so the page paints the
    same ten ops in the same order; only the clipping is gone. The three ops it
    draws outside their scissors are then ink like any other, which is exactly
    what the extractor did before it read the clip stack at all -- 1701 page 1's
    fill at x 602.16, beyond a clip ending at 598.32, printed as a black bar
    where the official sheet is blank.

    Counted rather than replaced blind: a pattern that stopped matching would
    mutate nothing, and a case that mutates nothing proves nothing.
    """
    stream = extract.CLIP_PROBE_STREAM
    found = stream.count(b" re W n")
    if found != PROBE_SCISSORS:
        raise SystemExit(f"the clip probe states {found} scissors, "
                         f"expected {PROBE_SCISSORS}")
    extract.CLIP_PROBE_STREAM = stream.replace(b" re W n", b" re n")


# The probe page's two nested scissors. Named so the mutation above fails loudly
# if the page is rewritten, rather than quietly patching a stream it no longer
# understands.
PROBE_SCISSORS = 2


def mutate_stroke_caps() -> None:
    """Build the cap probe butt-capped: every `1 J` and `2 J` becomes `0 J`.

    The same seven strokes are painted in the same order at the same declared
    endpoints; only the cap style is gone. The extractor then reads every bar
    as stopping exactly at its endpoints and measures every extension as 0.0,
    which is precisely the pre-cap-model reading -- the one that filed 2550M's
    round-capped comb ticks short of their rail, so lattice.split_verticals
    took them for box borders and four year boxes reached the taxpayer as one
    wide input. The check must refuse it on both fronts: the published
    geometry no longer matches the table, and the measured extensions are 0.0
    where the page's `J` operators say 0.6.

    Counted rather than replaced blind, for the same reason as mutate_clips: a
    pattern that stopped matching would mutate nothing, and a case that
    mutates nothing proves nothing.
    """
    stream = extract.CAP_PROBE_STREAM
    found = (stream.count(b"\n1 J\n"), stream.count(b"\n2 J\n"))
    if found != (PROBE_ROUND_CAPS, PROBE_PROJECTING_CAPS):
        raise SystemExit(
            f"the cap probe states {found[0]} round and {found[1]} projecting "
            f"caps, expected {PROBE_ROUND_CAPS} and {PROBE_PROJECTING_CAPS}")
    extract.CAP_PROBE_STREAM = (stream
                                .replace(b"\n1 J\n", b"\n0 J\n")
                                .replace(b"\n2 J\n", b"\n0 J\n"))


# The cap probe's non-butt cap operators: five round (`1 J`), one projecting
# (`2 J`). Named for the same reason as PROBE_SCISSORS -- a rewritten probe
# page must fail this case loudly, not be half-patched in silence.
PROBE_ROUND_CAPS = 5
PROBE_PROJECTING_CAPS = 1


def _one_operator(stream: bytes, operator: bytes, what: str) -> None:
    """Refuse to patch a probe stream that does not state `operator` exactly once.

    Same reason mutate_clips counts its scissors: a pattern that stopped
    matching would mutate nothing, and a case that mutates nothing proves
    nothing. A pattern that started matching twice is worse -- it would mutate
    a second operator nobody described.
    """
    found = stream.count(operator)
    if found != 1:
        raise SystemExit(f"the {what} states {operator!r} {found} time(s), "
                         f"expected exactly 1")


def _text_op(text: str) -> bytes:
    """The probe stream's own way of writing one literal-string show operator."""
    return b"(" + text.encode("ascii") + b") Tj"


def mutate_ruled_blank_split() -> None:
    """Set the mixed run as its blank alone: `(AB ____ CD)` becomes `(____)`.

    The blank is still drawn, by the same face at the same size on the same
    baseline, and still becomes a rule -- so the page keeps its two groups, its
    one publication and its one refusal, and the floor and fail-closed checks
    see exactly the census they pin. What the page no longer carries is the
    SPLIT: there is no text either side of the blank for the run to be cut into,
    so neither fragment the check names is on the page, and the rule the blank
    publishes runs 19.78 -> 42.46 rather than 35.9 -> 58.58, because its first
    glyph is now the run's first glyph.

    That is what the check is named for. `AB ` and ` CD` each end at their OWN
    outermost glyph rather than at the span's box, and a run that is nothing but
    its blank states neither of them. A corpus that only ever set blanks alone
    would leave the split unproven while every rule it published still landed
    correctly -- which is the reading this file exists to refuse.

    Derived from the pinned text rather than spelled out again, so a rewritten
    probe fails here loudly instead of being patched by a stale literal.
    """
    text = extract.RULED_BLANK_PROBE_MIXED_TEXT
    groups = extract.ruled_blank_groups(text)
    if len(groups) != 1:
        raise SystemExit(f"the ruled-blank probe's mixed run {text!r} holds "
                         f"{len(groups)} blank(s), expected exactly 1")
    start, end = groups[0]
    stream = extract.RULED_BLANK_PROBE_STREAM
    _one_operator(stream, _text_op(text), "ruled-blank probe")
    extract.RULED_BLANK_PROBE_STREAM = stream.replace(
        _text_op(text), _text_op(text[start:end]))


def mutate_ruled_blank_floor() -> None:
    """Punctuate the sub-floor run with hyphens: `XY __ Z` becomes `XY -- Z`.

    Two underscores are the whole structure this check is about, and this page
    is the only place either corpus states them. With hyphens in their place the
    run is still there, still text and still verbatim -- and it is no longer the
    run the check pins, which is the reading a corpus carrying no sub-floor
    blank at all would leave.

    It does not reach the group census, and cannot. RULED_BLANK_PROBE_GROUPS is
    2, and the two are the mixed run's blank and the unresolvable face's, so any
    mutation that moves the count has to add or remove a group: a group added is
    a rule the split check's table does not name, and a group removed is a
    publication or a refusal the fail-closed check counts. That assertion is
    jointly owned by all three checks, and extract.py's own in-memory probe is
    what proves it wired. What is proven here is the thing no in-memory probe
    can prove -- that the sub-floor run is in the source at all.
    """
    text = extract.RULED_BLANK_PROBE_BELOW_FLOOR
    if extract.ruled_blank_groups(text):
        raise SystemExit(f"the ruled-blank probe's sub-floor run {text!r} "
                         f"already reads as a blank")
    stream = extract.RULED_BLANK_PROBE_STREAM
    _one_operator(stream, _text_op(text), "ruled-blank probe")
    extract.RULED_BLANK_PROBE_STREAM = stream.replace(
        _text_op(text),
        _text_op(text.replace(extract.RULED_BLANK_CHARACTER, PROBE_PUNCTUATION)))


def mutate_ruled_blank_fail_closed() -> None:
    """Draw the unresolvable blank with the resolvable face instead, set 40pt.

    The probe's second font is unembedded and is not base-14, so MuPDF draws
    something and no face this module can name states that glyph's outline --
    1707's shape, and the only unresolvable face in either corpus. Setting the
    run in /F1 takes it away: the band becomes derivable, and the refusal the
    check pins does not happen.

    The size travels with the face, and has to. A resolvable blank at the
    probe's own 10pt is PUBLISHED, and a published blank is a rule the split
    check's table does not name -- that mutation trips split too, for a reason
    of split's own, and would prove nothing about either check. The probe's
    pinned blank is 0.5pt thick at 10pt, so this face's underscore is 0.05 em:
    at 40pt it draws 2.0pt, over MAX_RULE_THICKNESS_PT, and the group is refused
    on its geometry and kept as text. Same two groups, same one publication,
    same one retained run. Only the REASON moves -- from a face that cannot be
    named to a band too thick to be a rule -- and the reason is what this check
    pins.
    """
    stream = extract.RULED_BLANK_PROBE_STREAM
    _one_operator(stream, PROBE_UNRESOLVABLE_FACE_OP, "ruled-blank probe")
    extract.RULED_BLANK_PROBE_STREAM = stream.replace(
        PROBE_UNRESOLVABLE_FACE_OP, PROBE_THICK_RESOLVED_FACE_OP)


# The character the sub-floor run's underscores are punctuated with. Anything
# RULED_BLANK_CHARACTER is not would do; a hyphen is the fill character the
# floor exists to spare, so the mutated run stays a run the check would have to
# leave alone for a reason of its own.
PROBE_PUNCTUATION = "-"

# The ruled-blank probe's third text operator, and what mutate_ruled_blank_
# fail_closed puts in its place. /F2 is the unembedded non-base-14 face; /F1 is
# the resolvable one, and 40pt is the size at which its underscores draw a
# 2.0pt band -- over extract.MAX_RULE_THICKNESS_PT, so the blank is refused
# rather than published. Named here for the same reason as PROBE_SCISSORS: a
# probe page rewritten around a different font or size must fail this case
# loudly rather than be patched in silence.
PROBE_UNRESOLVABLE_FACE_OP = b"/F2 10 Tf"
PROBE_THICK_RESOLVED_FACE_OP = b"/F1 40 Tf"


def mutate_ruled_blank_embedded_subset_tag() -> None:
    """Give /F1's embedded program a malformed six-character subset tag.

    F065's own fix (`extract.SUBSET_TAG_RE`) strips exactly the PDF spec's
    subset prefix -- six UPPERCASE LETTERS then '+' (ISO 32000-1 9.6.4) --
    never a looser pattern. MuPDF's own rawdict stripping is looser: measured
    directly, it strips a lowercase six-character prefix too (`span["font"]`
    still comes back `"ProbeSubsetGood"`). Lowercasing /F1's own tag
    therefore reproduces F065's exact key mismatch for a tag that is NOT
    spec-shaped -- `substitutable_faces` registers only the exact key, no
    span ever asks for it by that exact key, and the group that published
    with the real tag correctly refuses with this one. If `SUBSET_TAG_RE`
    were ever loosened to match this too, this mutation would stop tripping
    anything -- which is the property it exists to guard.
    """
    name = extract.RULED_BLANK_EMBEDDED_PROBE_GOOD_NAME
    if extract.SUBSET_TAG_RE.match(name.decode("ascii")) is None:
        raise SystemExit(f"the subset-embedded probe's good font name "
                         f"{name!r} does not carry a spec-shaped tag to begin "
                         f"with")
    malformed = name[:6].lower() + name[6:]
    if extract.SUBSET_TAG_RE.match(malformed.decode("ascii")) is not None:
        raise SystemExit(f"{malformed!r} still reads as a spec-shaped tag; "
                         f"this mutation proves nothing")
    extract.RULED_BLANK_EMBEDDED_PROBE_GOOD_NAME = malformed


def mutate_ruled_blank_embedded_program() -> None:
    """Un-corrupt /F2's embedded program: its own `glyf` table becomes /F1's.

    /F2's key resolves exactly as /F1's does -- its own tag is spec-shaped
    too -- so the ONLY reason its group stays text is that its `glyf` table
    cannot state the underscore glyph's outline (`extract.
    embedded_glyph_outline` returns None on it). Replacing its program with
    /F1's own bytes removes that single defect without touching either
    font's key, name or tag, so the group that refused with the broken
    program now publishes with a working one -- proving the refusal was
    genuinely about the program's own bytes, not a font this check would
    have refused regardless of what it embedded.
    """
    good = extract.RULED_BLANK_EMBEDDED_PROBE_GOOD_TTF
    broken = extract.RULED_BLANK_EMBEDDED_PROBE_BROKEN_TTF
    if good == broken:
        raise SystemExit("the subset-embedded probe's good and broken "
                         "programs are already identical")
    extract.RULED_BLANK_EMBEDDED_PROBE_BROKEN_TTF = good


def mutate_rule_origin() -> None:
    """Never draw the merge-partner rect, so the underscore run stays isolated.

    Its blank is still drawn, by the same face at the same size on the same
    baseline, and still becomes a rule -- so the page still carries all three
    of the probe's shapes and the isolated vector bar and the isolated
    underscore run are both untouched. What is gone is the ONE STROKE ON
    PAPER the merge case exists to recognise: without the abutting vector
    fragment, the run at IR y 41.26 publishes alone, at its own extent
    (19.78 -> 42.46) rather than the merged (19.78 -> 60.46), and at its own
    true origin, RULE_ORIGIN_TEXT_UNDERSCORE -- not the mixed-provenance
    RULE_ORIGIN_VECTOR the pinned table names for that band. Both the lost
    merged rule and the unnamed isolated one are the SAME check's evidence.

    Counted rather than replaced blind, for the same reason as mutate_clips: a
    pattern that stopped matching would mutate nothing, and a case that
    mutates nothing proves nothing.
    """
    stream = extract.RULE_ORIGIN_PROBE_STREAM
    partner = RULE_ORIGIN_PROBE_MERGE_PARTNER_OP
    _one_operator(stream, partner, "rule-origin probe")
    extract.RULE_ORIGIN_PROBE_STREAM = stream.replace(partner, b"")


# The rule-origin probe's merge-partner rect: a vector-drawn bar placed at the
# underscore run's own measured ink band so it merges into one rule with it.
# Named for the same reason as PROBE_SCISSORS: a probe page rewritten around a
# different band or offset must fail this case loudly rather than being
# half-patched in silence.
RULE_ORIGIN_PROBE_MERGE_PARTNER_OP = b"42.46 158.24 18.0 0.5 re f\n"


def mutate_glyph_ink() -> None:
    """Set the unresolvable run in the resolvable face: `/F2 10 Tf` -> `/F1`.

    The probe's second font is unembedded and is not base-14, so MuPDF draws
    something and no face this module can name states those glyphs' outlines --
    62,010 glyphs of the official corpus, and 1707's whole shape. In /F1 the
    same four characters at the same size on the same baseline become
    measurable, so the page publishes two outline tables where it stated one
    and its refusal census loses the reason this half of the check pins.

    It cannot be confused with the rotated or contradicted-advance operators:
    both keep their own font operator, and neither is `/F2 10 Tf`. The
    replacement is written as the operator the probe's own resolvable runs use,
    so a probe page rewritten around a different face or size fails here loudly
    rather than being patched onto a font it no longer declares.
    """
    stream = extract.GLYPH_INK_PROBE_STREAM
    _one_operator(stream, PROBE_UNRESOLVABLE_INK_OP, "glyph-ink probe")
    if stream.count(PROBE_RESOLVABLE_INK_OP) != PROBE_RESOLVABLE_INK_OPS:
        raise SystemExit(
            f"the glyph-ink probe states {stream.count(PROBE_RESOLVABLE_INK_OP)} "
            f"resolvable font operator(s), expected {PROBE_RESOLVABLE_INK_OPS}")
    extract.GLYPH_INK_PROBE_STREAM = stream.replace(
        PROBE_UNRESOLVABLE_INK_OP, PROBE_RESOLVABLE_INK_OP)


def mutate_glyph_ink_embedded() -> None:
    """Set the embedded-program run in the unembedded face: `/F4` -> `/F1`.

    /F4 is the only operator on either corpus's written-here pages that draws
    from a font program the file EMBEDS, and every face MuPDF loads from a
    buffer answers `glyph_bbox` with its own `Font.bbox` -- which is why 2551Q
    page 1's captions, and 9,217 glyphs on 48 forms, have no derivable outline
    at all. Drawn in /F1 the same four characters at the same size on the same
    baseline become measurable, so the page publishes two outline tables where
    it stated one and loses the refusal this half of the check pins.

    The embedded font object, its descriptor and its 33KB program stay in the
    file; only the operator that shows glyphs with it moves. That is what makes
    this a statement about the DRAWN face rather than about the PDF's
    furniture: a page carrying an embedded program nothing sets type in would
    be exactly the decoration this file exists to catch.
    """
    stream = extract.GLYPH_INK_PROBE_STREAM
    _one_operator(stream, PROBE_EMBEDDED_INK_OP, "glyph-ink probe")
    if stream.count(PROBE_RESOLVABLE_INK_OP) != PROBE_RESOLVABLE_INK_OPS:
        raise SystemExit(
            f"the glyph-ink probe states {stream.count(PROBE_RESOLVABLE_INK_OP)} "
            f"resolvable font operator(s), expected {PROBE_RESOLVABLE_INK_OPS}")
    extract.GLYPH_INK_PROBE_STREAM = stream.replace(
        PROBE_EMBEDDED_INK_OP, PROBE_RESOLVABLE_INK_OP)


# The glyph-ink probe's unresolvable and embedded font operators, the resolvable
# one both are replaced with, and how many times that resolvable one is already
# stated (the measured run and the rotated one). Named for the same reason as
# PROBE_UNRESOLVABLE_FACE_OP: a rewritten probe must fail loudly.
PROBE_UNRESOLVABLE_INK_OP = b"/F2 10 Tf"
PROBE_EMBEDDED_INK_OP = b"/F4 10 Tf"
PROBE_RESOLVABLE_INK_OP = b"/F1 10 Tf"
PROBE_RESOLVABLE_INK_OPS = 2


def mutate_baseline_split() -> None:
    """Put each second text operator on its partner's own Td baseline.

    `Z` is set 1pt under `XY`, and `PQ` 4pt under the space that positions it.
    Those two drops are the only reason MuPDF's line builder hands this module a
    span carrying two baselines. Levelled, the page still paints the same five
    operators with the same glyphs at the same x, in the same order, at the same
    size -- and carries no two-baseline span at all.

    BASELINE_PROBE_SPANS then trips first, which is exactly what that assertion
    is for: it is asserted ahead of the run table so that a page which stopped
    provoking the merge -- a reader change, a different line tolerance, or this
    mutation -- fails loudly instead of passing a run table that measures
    nothing. Both pairs are levelled because levelling one leaves the other
    still provoking it, and one two-baseline span is all the check needs.
    """
    stream = extract.BASELINE_PROBE_STREAM
    for placed, levelled in PROBE_SECOND_BASELINES:
        _one_operator(stream, placed, "baseline probe")
        stream = stream.replace(placed, levelled)
    extract.BASELINE_PROBE_STREAM = stream


# The baseline probe's two second operators, as placed and as levelled onto the
# baseline of the operator each shares a span with. Named rather than computed
# so a rewritten probe page fails this case loudly instead of being levelled
# onto coordinates nobody stated.
PROBE_SECOND_BASELINES = ((b"33.34 159 Td", b"33.34 160 Td"),
                          (b"26 126 Td", b"26 130 Td"))


# (the check that must trip, what was done to the source, how)
CASES: tuple[tuple[str, str, Callable[[], None]], ...] = (
    ("paper", "every sheet is built Letter-height", mutate_paper),
    ("paths", "one filled triangle is never drawn", mutate_paths),
    ("soft-masks", "the images are built without alpha", mutate_soft_masks),
    ("transforms", "the seal is placed unflipped", mutate_transforms),
    ("codepoints", "the unmappable glyph is not emitted", mutate_codepoints),
    ("tone", "both decorative greys are painted black", mutate_tone),
    ("checkbox-square", "the checkbox square's knockout drifts 3pt off its "
     "frame's centreline", mutate_checkbox_square),
    ("signature-box", "the in-box caption is pushed 10pt down, across the "
     "top-40% line", mutate_signature_box),
    ("signature-line", "the caption below is pulled 10pt up, above its own "
     "divider rule", mutate_signature_line),
    ("is-bar-like", "the separators are drawn exactly vertical", mutate_bar_like),
    ("clips", "the probe page's scissors are never established", mutate_clips),
    ("stroke-caps", "the cap probe's every stroke is butt-capped",
     mutate_stroke_caps),
    ("ruled-blank-split", "the blank probe's mixed run is set as its blank alone",
     mutate_ruled_blank_split),
    ("ruled-blank-floor", "the blank probe's sub-floor run is punctuated with "
     "hyphens", mutate_ruled_blank_floor),
    ("ruled-blank-fail-closed", "the blank probe's unresolvable face is the "
     "resolvable one at 40pt", mutate_ruled_blank_fail_closed),
    ("ruled-blank-embedded-subset", "the subset-embedded probe's good font "
     "gets a malformed, non-spec-shaped subset tag",
     mutate_ruled_blank_embedded_subset_tag),
    ("ruled-blank-embedded-subset", "the subset-embedded probe's broken "
     "program becomes the good one", mutate_ruled_blank_embedded_program),
    ("rule-origin", "the origin probe's merge-partner rect is never drawn",
     mutate_rule_origin),
    ("glyph-ink", "the ink probe's unresolvable face is the resolvable one",
     mutate_glyph_ink),
    ("glyph-ink", "the ink probe's embedded program is never set type in",
     mutate_glyph_ink_embedded),
    ("baseline-split", "the baseline probe's every span is levelled onto one "
     "baseline", mutate_baseline_split),
)

# Everything a mutation is allowed to reach into, as (module, attribute),
# captured before the first one runs and restored before each. Patching a module
# global and forgetting to put it back would leak into the next case and
# misattribute its result -- and three cases patch one stream, so a leak here
# would read as a check standing in for another. The probe streams are on this
# list for that reason and no other: they are the six subjects that do not live
# in the corpus.
PATCHABLE = ((fixtures, "PAGE_HEIGHT_PT"), (fixtures, "LEAN_OFFSET_PT"),
             (fixtures, "GREY_LIGHT"), (fixtures, "GREY_MID"),
             (fixtures, "CHECKBOX_SQUARE_KNOCKOUT_DRIFT_PT"),
             (fixtures, "SIGNATURE_BOX_CAPTION_DRIFT_PT"),
             (fixtures, "SIGNATURE_LINE_CAPTION_DRIFT_PT"),
             (fixtures, "right_triangle"), (fixtures, "checkerboard"),
             (fixtures, "flip_placement"), (fixtures, "insert_unmappable_glyph"),
             (extract, "CLIP_PROBE_STREAM"), (extract, "CAP_PROBE_STREAM"),
             (extract, "RULED_BLANK_PROBE_STREAM"),
             (extract, "RULED_BLANK_EMBEDDED_PROBE_GOOD_NAME"),
             (extract, "RULED_BLANK_EMBEDDED_PROBE_BROKEN_TTF"),
             (extract, "RULE_ORIGIN_PROBE_STREAM"),
             (extract, "BASELINE_PROBE_STREAM"),
             (extract, "GLYPH_INK_PROBE_STREAM"))


def profile_over(root: pathlib.Path) -> extract.SelfTestProfile:
    """The fixture profile, re-pinned to whatever now sits under `root`.

    Re-pinning is the point. Leaving the tracked digests in place would make
    every mutation fail at the hash check, and a hash failure is not evidence
    that the check under test noticed anything.
    """
    base = extract.FIXTURE_PROFILE
    fixtures_table = {
        code: (relative, revision,
               hashlib.sha256((root / relative).read_bytes()).hexdigest())
        for code, (relative, revision, _digest) in base.fixtures.items()
    }
    return extract.SelfTestProfile(
        name="mutated fixtures", source_root=root, fixtures=fixtures_table,
        paper=base.paper, determinism_form=base.determinism_form,
        masked=base.masked, flipped=base.flipped, paths_form=base.paths_form,
        triangles=base.triangles, decimal_points=base.decimal_points,
        tones=base.tones, retexted_glyphs=base.retexted_glyphs,
        retexted_glyph_id=base.retexted_glyph_id,
        retexted_rawdict_codepoint=base.retexted_rawdict_codepoint,
        bar_like_form=base.bar_like_form, leaning_bars=base.leaning_bars,
        checkbox_square=base.checkbox_square,
        signature_box=base.signature_box, signature_line=base.signature_line,
        is_evidence=False)


def tripped_checks(root: pathlib.Path) -> list[str]:
    """Which of extract.py's checks disagree with the corpus under `root`."""
    profile = profile_over(root)
    evidence = extract.gather_evidence(profile, root)
    return sorted(name for name, check in extract.SELF_TEST_CHECKS
                  if check(evidence))


def prove(stream: Any) -> int:
    declared = {name for name, _check in extract.SELF_TEST_CHECKS}
    accounted = {name for name, _why, _mutate in CASES} | set(CONTRACT_ONLY)
    failures: list[str] = []
    if accounted != declared:
        failures.append(
            f"every check needs a source-level mutation or a stated reason it "
            f"cannot have one; unaccounted={sorted(declared - accounted)} "
            f"invented={sorted(accounted - declared)}")

    pristine = [(module, name, getattr(module, name))
                for module, name in PATCHABLE]
    with tempfile.TemporaryDirectory() as scratch:
        root = pathlib.Path(scratch)
        fixtures.build_all(root / "clean")
        clean = tripped_checks(root / "clean")
        if clean:
            failures.append(f"the unmutated corpus already trips {clean}")
        print(f"  {'unmutated':<12} {'OK' if not clean else 'BROKEN':<5} "
              f"nothing trips", file=stream)

        for expected, description, mutate in CASES:
            for module, name, value in pristine:
                setattr(module, name, value)
            mutate()
            out = root / expected
            fixtures.build_all(out)
            tripped = tripped_checks(out)
            good = tripped == [expected]
            if not good:
                failures.append(
                    f"'{description}' should have tripped exactly [{expected!r}], "
                    f"tripped {tripped}")
            print(f"  {expected:<12} {'OK' if good else 'WEAK':<5} "
                  f"{description}", file=stream)
        for module, name, value in pristine:
            setattr(module, name, value)

    for name in sorted(CONTRACT_ONLY):
        print(f"  {name:<12} n/a   not reachable from corpus content: "
              f"{CONTRACT_ONLY[name]}", file=stream)
    for message in failures:
        print(f"    FAIL {message}", file=stream)
    print(f"prove-fixtures-fail: "
          f"{'PASS' if not failures else f'{len(failures)} FAILURE(S)'} over "
          f"{len(CASES)} source-level mutations, {len(CONTRACT_ONLY)} checks "
          f"stated as contract-only", file=stream)
    return 1 if failures else 0


def fmt(value: float) -> str:
    return f"{value:g}"


def _row_number_cell(page: dict[str, Any], ir_page: dict[str, Any],
                     ) -> dict[str, Any] | None:
    """The fixture's own row-number label cell, found by content, not id.

    A cell id is a position in lattice.py's own numbering of the whole page,
    and nothing here needs it to be stable -- the subject is "the `label`
    cell holding only `make_fixtures.ROW_NUMBER_TEXT`", found the same way a
    reader would, so a page rewritten above this shape still locates it.
    """
    page_index = int(page["index"])
    runs_by_id = {emit.run_id(page_index, i): run
                  for i, run in enumerate(ir_page["text_runs"])}
    for cell in page["cells"]:
        if cell["kind"] != "label":
            continue
        texts = [runs_by_id[rid]["text"] for rid in cell.get("text_run_ids") or ()
                 if rid in runs_by_id]
        if texts == [fixtures.ROW_NUMBER_TEXT]:
            return cell
    return None


def _row_number_claimed(root: pathlib.Path) -> bool | None:
    """Whether `emit.RowNumberWriting` claims the fixture's own row-number
    cell under `root`'s corpus, or None if the fixture does not carry the
    subject at all (a mutation that deleted the cell rather than narrowing
    it, which would be a different failure than the one this proves)."""
    ir = extract.extract(root / "rules.pdf", "FIXTURE-RULES", "0001", None)
    layout = lattice.build_layout(ir)
    metrics = emit._min_fillable_line_metrics(ir)
    page = next(p for p in layout["pages"] if int(p["index"]) == 2)
    ir_page = next(p for p in ir["pages"] if int(p["index"]) == 2)
    cell = _row_number_cell(page, ir_page)
    if cell is None:
        return None
    row_numbers = emit.RowNumberWriting(
        page["cells"], 2, ir_page["text_runs"], metrics)
    return row_numbers.for_cell(cell["id"]) is not None


def prove_row_number(stream: Any) -> int:
    """Prove F151's row-number rule (P2) can fail, via a real PDF mutation.

    Not one of `extract.py`'s own checks, and deliberately run outside
    `prove()`'s CASES/CONTRACT_ONLY accounting: row-number is entirely a
    `lattice.py`/`emit.py` decision (a `label` cell shares its row with a
    `field` cell, holds only a short numeral, and its own trailing blank
    clears the form's own `line_width_pt` at 1.0x -- `emit.RowNumberWriting`)
    with no new extract-level primitive of its own, so folding it into that
    table would misname what it tests: `extract.py` extracts this fixture's
    rules, fills and text runs identically whether the mutation below has
    run or not, and every one of its own checks agrees. What changes is a
    later-stage geometric fact neither `extract.SELF_TEST_CHECKS` nor
    `CONTRACT_ONLY` is about.

    Same method as every case in `CASES` -- mutate the source PDF, rebuild,
    observe -- carried one stage further than `extract.gather_evidence`
    reaches: `lattice.build_layout` and `emit.RowNumberWriting` run over the
    rebuilt IR too, the same two calls `batch.py` chains after extract.py for
    every real bundle.
    """
    failures: list[str] = []
    with tempfile.TemporaryDirectory() as scratch:
        root = pathlib.Path(scratch)
        fixtures.build_all(root)
        claimed = _row_number_claimed(root)
        if claimed is not True:
            failures.append(
                f"the unmutated row-number fixture's claim is {claimed!r}, "
                f"not True; the fixture never carried the property")
        print(f"  {'unmutated':<12} {'OK' if claimed is True else 'BROKEN':<5} "
              f"row-number claims the fixture's own label cell", file=stream)

    previous = fixtures.ROW_NUMBER_LABEL_WIDTH_PT
    try:
        mutate_row_number()
        with tempfile.TemporaryDirectory() as scratch:
            root = pathlib.Path(scratch)
            fixtures.build_all(root)
            claimed = _row_number_claimed(root)
            good = claimed is False
            if not good:
                failures.append(
                    f"the narrowed row-number fixture's claim is {claimed!r}, "
                    f"not False; the mutation did not clear the bound")
            print(f"  {'row-number':<12} {'OK' if good else 'WEAK':<5} "
                  f"the label cell's own width narrows from "
                  f"{fmt(previous)}pt to {fmt(fixtures.ROW_NUMBER_LABEL_WIDTH_PT)}pt, "
                  f"the trailing blank drops under half of line_width_pt", file=stream)
    finally:
        fixtures.ROW_NUMBER_LABEL_WIDTH_PT = previous

    for message in failures:
        print(f"    FAIL {message}", file=stream)
    print(f"prove-row-number: "
          f"{'PASS' if not failures else f'{len(failures)} FAILURE(S)'} over "
          f"1 source-level mutation, run outside extract.py's own "
          f"CASES/CONTRACT_ONLY accounting (see prove_row_number)",
          file=stream)
    return 1 if failures else 0


def mutate_comb_band_reunification() -> None:
    """Draw the comb-band-reunification row's own notch box.

    Nothing about the row's outer border, the comb's own rail or its
    dividers moves. A small bordered box appears left of the rail, entirely
    inside the row -- the row's own DSU component stops being fully
    occupied (the box's own interior is a hole neither its own borders nor
    the row's outer ones reach), `no-rectangular-owner` appears (F064's own
    ledger state), and `lattice._reunify_comb_band` has to decide whether
    it may still absorb the row into one comb-owning cell. It may not: the
    notch's own two internal walls match none of the comb's own divider
    positions. See `prove_comb_band_reunification`, which is where this
    trips -- like `mutate_row_number`, not one of `extract.py`'s own checks
    (comb-band-reunification is a `lattice.py` decision with no new
    extract-level primitive of its own), so it runs the pipeline this
    module already imports rather than `extract.SELF_TEST_CHECKS`.
    """
    fixtures.COMB_BAND_REUNIFICATION_NOTCH_SIZE_PT = 12.0


def _comb_band_reunification_subject(
        root: pathlib.Path) -> dict[str, Any] | None:
    """The comb-band-reunification row's own comb subject, or None if the
    fixture does not carry the subject at all (a mutation that deleted the
    row's own comb rather than adding the notch, which would be a
    different failure than the one this proves)."""
    ir = extract.extract(root / "rules.pdf", "FIXTURE-RULES", "0001", None)
    layout = lattice.build_layout(ir)
    page = next(p for p in layout["pages"] if int(p["index"]) == 2)
    for subject in page["comb_subjects"]:
        bbox = subject["legacy_bbox"]
        if abs(float(bbox[0]) - 48.24) <= 1.0 and 335 < float(bbox[1]) < 385:
            return subject
    return None


def prove_comb_band_reunification(stream: Any) -> int:
    """Prove F064's comb-band-reunification mechanism (W3) can fail, via a
    real PDF mutation.

    Not one of `extract.py`'s own checks, and deliberately run outside
    `prove()`'s CASES/CONTRACT_ONLY accounting: comb-band-reunification is
    entirely a `lattice.py` decision (`lattice._reunify_comb_band`, given a
    legacy comb subject with no CURRENT rectangular owner, absorbs or trims
    every current cell its own rails and rows bound, refusing outright the
    moment an internal wall does not match the comb's own dividers) with no
    new extract-level primitive of its own, so folding it into that table
    would misname what it tests: `extract.py` extracts this fixture's
    rules and fills identically whether the mutation below has run or not,
    and every one of its own checks agrees. What changes is a later-stage
    geometric fact neither `extract.SELF_TEST_CHECKS` nor `CONTRACT_ONLY`
    is about.

    Same method as every case in `CASES` -- mutate the source PDF, rebuild,
    observe -- carried one stage further than `extract.gather_evidence`
    reaches: `lattice.build_layout` runs over the rebuilt IR too, the same
    call `batch.py` chains after extract.py for every real bundle.
    """
    failures: list[str] = []
    with tempfile.TemporaryDirectory() as scratch:
        root = pathlib.Path(scratch)
        fixtures.build_all(root)
        subject = _comb_band_reunification_subject(root)
        healthy = (
            subject is not None and subject.get("state") == "active_resolved"
            and "no-rectangular-owner"
            not in " ".join(subject.get("reason_codes") or ()))
        if not healthy:
            failures.append(
                f"the unmutated comb-band-reunification fixture's subject "
                f"is {subject!r}, not a healthy active_resolved comb; the "
                f"fixture never carried the property")
        print(f"  {'unmutated':<12} {'OK' if healthy else 'BROKEN':<5} "
              f"the row's own comb resolves through the ordinary path, "
              f"no retained subject at all", file=stream)

    previous = fixtures.COMB_BAND_REUNIFICATION_NOTCH_SIZE_PT
    try:
        mutate_comb_band_reunification()
        with tempfile.TemporaryDirectory() as scratch:
            root = pathlib.Path(scratch)
            fixtures.build_all(root)
            subject = _comb_band_reunification_subject(root)
            reason_codes = (
                subject.get("reason_codes") if subject is not None else None)
            declined_safely = (
                subject is not None
                and subject.get("state") == "retained_unresolved"
                and reason_codes is not None
                and "emission-suppressed-no-rectangular-owner"
                in reason_codes)
            if not declined_safely:
                failures.append(
                    f"the notched comb-band-reunification fixture's "
                    f"subject is {subject!r}, not a safely-declined "
                    f"no-rectangular-owner retention; the mutation did not "
                    f"reproduce F064's own ledger state")
            print(f"  {'notched':<12} "
                  f"{'OK' if declined_safely else 'WEAK':<5} "
                  f"a bordered notch appears inside the row, "
                  f"COMB_BAND_REUNIFICATION_NOTCH_SIZE_PT 0.0pt -> "
                  f"{fmt(fixtures.COMB_BAND_REUNIFICATION_NOTCH_SIZE_PT)}pt, "
                  f"the row's own component stops being fully occupied and "
                  f"reunification correctly declines to absorb it",
                  file=stream)
    finally:
        fixtures.COMB_BAND_REUNIFICATION_NOTCH_SIZE_PT = previous

    for message in failures:
        print(f"    FAIL {message}", file=stream)
    print(f"prove-comb-band-reunification: "
          f"{'PASS' if not failures else f'{len(failures)} FAILURE(S)'} over "
          f"1 source-level mutation, run outside extract.py's own "
          f"CASES/CONTRACT_ONLY accounting "
          f"(see prove_comb_band_reunification)",
          file=stream)
    return 1 if failures else 0


def _signature_rule_cell(page: dict[str, Any], ir_page: dict[str, Any],
                         ) -> dict[str, Any] | None:
    """The fixture's own signature-rule cell, found by content, not id.

    A cell id is a position in lattice.py's own numbering of the whole page,
    and nothing here needs it to be stable -- the subject is "the `label`
    cell holding only `make_fixtures.SIGNATURE_RULE_ITEM_TEXT`", found the
    same way a reader would, so a page rewritten above this shape still
    locates it (`_row_number_cell`'s own precedent).
    """
    page_index = int(page["index"])
    runs_by_id = {emit.run_id(page_index, i): run
                  for i, run in enumerate(ir_page["text_runs"])}
    for cell in page["cells"]:
        if cell["kind"] != "label":
            continue
        texts = [runs_by_id[rid]["text"] for rid in cell.get("text_run_ids") or ()
                 if rid in runs_by_id]
        if texts == [fixtures.SIGNATURE_RULE_ITEM_TEXT]:
            return cell
    return None


def _signature_rule_claimed(root: pathlib.Path) -> bool | None:
    """Whether `emit.SignatureRuleWriting` claims the fixture's own
    signature-rule cell under `root`'s corpus, or None if the fixture does
    not carry the subject at all (a mutation that deleted the cell rather
    than changing its caption, which would be a different failure than the
    one this proves)."""
    ir = extract.extract(root / "rules.pdf", "FIXTURE-RULES", "0001", None)
    layout = lattice.build_layout(ir)
    page = next(p for p in layout["pages"] if int(p["index"]) == 2)
    ir_page = next(p for p in ir["pages"] if int(p["index"]) == 2)
    cell = _signature_rule_cell(page, ir_page)
    if cell is None:
        return None
    signature_rules = emit.SignatureRuleWriting(
        page["cells"], 2, ir_page["rules"], ir_page["text_runs"])
    return bool(signature_rules.for_cell(cell["id"]))


def mutate_signature_rule() -> None:
    """Change the caption below the signature line to one that claims
    nothing writable.

    Nothing about either cell's own geometry moves -- not the wall they
    share, not the vector bar straddling it, not the item number's own ink
    -- only the caption text's own words do. What breaks is the fact
    `emit.SignatureRuleWriting` reads: a `label` cell's own vector rule
    earns it an input only when the caption directly below it names what
    belongs ON the line -- a signature caption or, since the user's
    2026-08-16 decision, a signatory-detail caption ("Title/Position of
    Signatory" and kin, 0605-1999's own real third line, which this
    mutation USED as its residue back when that caption was refused; it
    claims now, so it can no longer serve). "Details of Payment" is
    0605-1999's own next printed caption down the same page: a section
    header that names no signature, no printed name and no signatory
    detail, and never earns the line an input.
    """
    fixtures.SIGNATURE_RULE_CAPTION_TEXT = "Details of Payment"


def prove_signature_rule(stream: Any) -> int:
    """Prove F221 case 1's signature-rule mechanism can fail, via a real PDF
    mutation.

    Not one of `extract.py`'s own checks, and deliberately run outside
    `prove()`'s CASES/CONTRACT_ONLY accounting, the identical shape
    `prove_row_number` and `prove_comb_band_reunification` already give
    their own findings: `emit.SignatureRuleWriting` is entirely a
    `lattice.py`/`emit.py` decision (a `label` cell's own vector rule,
    straddling the wall it shares with a cell below naming it "Signature
    over Printed Name...") with no new extract-level primitive of its own,
    so folding it into that table would misname what it tests: `extract.py`
    extracts this fixture's rules and text runs identically whether the
    mutation below has run or not, and every one of its own checks agrees.
    What changes is a later-stage geometric-and-textual fact neither
    `extract.SELF_TEST_CHECKS` nor `CONTRACT_ONLY` is about.

    Same method as every case in `CASES` -- mutate the source PDF, rebuild,
    observe -- carried one stage further than `extract.gather_evidence`
    reaches: `lattice.build_layout` and `emit.SignatureRuleWriting` run over
    the rebuilt IR too, the same two calls `batch.py` chains after
    extract.py for every real bundle.
    """
    failures: list[str] = []
    with tempfile.TemporaryDirectory() as scratch:
        root = pathlib.Path(scratch)
        fixtures.build_all(root)
        claimed = _signature_rule_claimed(root)
        if claimed is not True:
            failures.append(
                f"the unmutated signature-rule fixture's claim is "
                f"{claimed!r}, not True; the fixture never carried the "
                f"property")
        print(f"  {'unmutated':<12} {'OK' if claimed is True else 'BROKEN':<5} "
              f"signature-rule claims the fixture's own vector-drawn line",
              file=stream)

    previous = fixtures.SIGNATURE_RULE_CAPTION_TEXT
    try:
        mutate_signature_rule()
        with tempfile.TemporaryDirectory() as scratch:
            root = pathlib.Path(scratch)
            fixtures.build_all(root)
            claimed = _signature_rule_claimed(root)
            good = claimed is False
            if not good:
                failures.append(
                    f"the retitled signature-rule fixture's claim is "
                    f"{claimed!r}, not False; the mutation did not clear "
                    f"the caption gate")
            print(f"  {'signature-rule':<12} {'OK' if good else 'WEAK':<5} "
                  f"the caption below the line changes from "
                  f"{previous!r} to "
                  f"{fixtures.SIGNATURE_RULE_CAPTION_TEXT!r} -- 0605-1999's "
                  f"own real \"Title/Position of Signatory\" residue -- and "
                  f"names no signature", file=stream)
    finally:
        fixtures.SIGNATURE_RULE_CAPTION_TEXT = previous

    for message in failures:
        print(f"    FAIL {message}", file=stream)
    print(f"prove-signature-rule: "
          f"{'PASS' if not failures else f'{len(failures)} FAILURE(S)'} over "
          f"1 source-level mutation, run outside extract.py's own "
          f"CASES/CONTRACT_ONLY accounting (see prove_signature_rule)",
          file=stream)
    return 1 if failures else 0


def mutate_ink_trim_comb() -> None:
    """Lift the ink-trim comb's own caption clear of the row below it.

    Nothing about the comb moves -- not its walls, not its divider ticks,
    not its slot count -- only the caption's own baseline does, raised
    `INK_TRIM_CAPTION_DRIFT_PT` further above the row's own top wall than
    the default 1.0pt gap `ink_trim_comb_row`'s own docstring measures. At
    that clearance no glyph's own measured outline reaches the comb's
    writing top any more -- the identical fail-closed fact
    `emit.py`'s own self-test already proves at the Python level ("lines
    seated just above the strip graze it and do not take it"), proven here
    instead from a real rebuilt PDF, one stage past where that self-test
    starts.
    """
    fixtures.INK_TRIM_CAPTION_DRIFT_PT = 2.0


def _ink_trim_comb_top_clear(root: pathlib.Path) -> float | None:
    """The ink-trim comb's own measured top clearance under `root`'s
    corpus, or None if the fixture does not carry a comb there at all (a
    mutation that deleted the comb rather than moving the caption, which
    would be a different failure than the one this proves)."""
    ir = extract.extract(root / "rules.pdf", "FIXTURE-RULES", "0001", None)
    layout = lattice.build_layout(ir)
    page = next(p for p in layout["pages"] if int(p["index"]) == 2)
    ir_page = next(p for p in ir["pages"] if int(p["index"]) == 2)
    cell = next((c for c in page["cells"]
                if c.get("comb") and abs(float(c["x0"]) - 48.0) <= 1.0
                and 470.0 < float(c["y0"]) < 510.0), None)
    if cell is None:
        return None
    comb = cell["comb"]
    write_top, height = emit.comb_writing_rect(cell, comb)
    ink = emit.PrePrintedInk(ir_page["text_runs"])
    return emit.comb_writing_top_clear_of_printed_ink(comb, write_top, height, ink)


def prove_ink_trim_comb(stream: Any) -> int:
    """Prove F227's comb-offering mechanism can fail, via a real PDF mutation.

    Not one of extract.py's own checks, and deliberately run outside
    prove()'s CASES/CONTRACT_ONLY accounting, the identical shape
    prove_row_number/prove_comb_band_reunification/prove_signature_rule
    already give their own findings: `emit.comb_writing_top_clear_of_
    printed_ink` is entirely a `lattice.py`/`emit.py` decision (a comb's
    own shared writing top, trimmed against printed ink the identical way
    a plain field's already was, which the comb branch of `field_box`
    never did before this session) with no new extract-level primitive of
    its own, so folding it into that table would misname what it tests:
    `extract.py` extracts this fixture's rules and text runs identically
    whether the mutation below has run or not, and every one of its own
    checks agrees. What changes is a later-stage geometric fact neither
    `extract.SELF_TEST_CHECKS` nor `CONTRACT_ONLY` is about.

    Same method as every case in CASES -- mutate the source PDF, rebuild,
    observe -- carried one stage further than `extract.gather_evidence`
    reaches: `lattice.build_layout` and
    `emit.comb_writing_top_clear_of_printed_ink` run over the rebuilt IR
    too, the same two calls (through `field_box`) `batch.py` chains after
    extract.py for every real bundle.
    """
    failures: list[str] = []
    with tempfile.TemporaryDirectory() as scratch:
        root = pathlib.Path(scratch)
        fixtures.build_all(root)
        top_clear = _ink_trim_comb_top_clear(root)
        real = top_clear is not None and top_clear > 0.0
        if not real:
            failures.append(
                f"the unmutated ink-trim comb fixture's own top clearance "
                f"is {top_clear!r}, not a positive amount; the fixture "
                f"never carried the property")
        print(f"  {'unmutated':<12} {'OK' if real else 'BROKEN':<5} "
              f"the comb's own writing top trims {top_clear!r}pt off the "
              f"caption's own descender", file=stream)

    previous = fixtures.INK_TRIM_CAPTION_DRIFT_PT
    try:
        mutate_ink_trim_comb()
        with tempfile.TemporaryDirectory() as scratch:
            root = pathlib.Path(scratch)
            fixtures.build_all(root)
            top_clear = _ink_trim_comb_top_clear(root)
            good = top_clear == 0.0
            if not good:
                failures.append(
                    f"the lifted ink-trim comb fixture's own top clearance "
                    f"is {top_clear!r}, not 0.0; the mutation did not clear "
                    f"the caption's own reach")
            print(f"  {'ink-trim-comb':<12} {'OK' if good else 'WEAK':<5} "
                  f"the caption's own baseline lifts to a "
                  f"{fmt(fixtures.INK_TRIM_CAPTION_DRIFT_PT)}pt drift from "
                  f"{fmt(previous)}pt, and no glyph's own measured outline "
                  f"reaches the comb's writing top any more", file=stream)
    finally:
        fixtures.INK_TRIM_CAPTION_DRIFT_PT = previous

    for message in failures:
        print(f"    FAIL {message}", file=stream)
    print(f"prove-ink-trim-comb: "
          f"{'PASS' if not failures else f'{len(failures)} FAILURE(S)'} over "
          f"1 source-level mutation, run outside extract.py's own "
          f"CASES/CONTRACT_ONLY accounting (see prove_ink_trim_comb)",
          file=stream)
    return 1 if failures else 0


def _signature_rule_gap_cell(page: dict[str, Any], ir_page: dict[str, Any],
                             ) -> dict[str, Any] | None:
    """The fixture's own sliver-gap signature-rule cell, found by content,
    not id -- `_signature_rule_cell`'s own precedent, so a page rewritten
    above this shape still locates it."""
    page_index = int(page["index"])
    runs_by_id = {emit.run_id(page_index, i): run
                  for i, run in enumerate(ir_page["text_runs"])}
    for cell in page["cells"]:
        if cell["kind"] != "label":
            continue
        texts = [runs_by_id[rid]["text"] for rid in cell.get("text_run_ids") or ()
                 if rid in runs_by_id]
        if texts == [fixtures.SIGNATURE_RULE_GAP_ITEM_TEXT]:
            return cell
    return None


def _signature_rule_gap_claimed(root: pathlib.Path) -> bool | None:
    """Whether `emit.SignatureRuleWriting`'s sliver-gap extension claims the
    fixture's own gap cell under `root`'s corpus, or None if the fixture
    does not carry the subject at all (a mutation that deleted the cell
    rather than moving the sliver, which would be a different failure than
    the one this proves).

    Unlike `_signature_rule_claimed`, this passes the IR's own
    `_min_fillable_line_metrics` -- the extension is unreachable without it,
    exactly as `field_verdict`'s own real callers always supply it.
    """
    ir = extract.extract(root / "rules.pdf", "FIXTURE-RULES", "0001", None)
    layout = lattice.build_layout(ir)
    page = next(p for p in layout["pages"] if int(p["index"]) == 2)
    ir_page = next(p for p in ir["pages"] if int(p["index"]) == 2)
    cell = _signature_rule_gap_cell(page, ir_page)
    if cell is None:
        return None
    metrics = emit._min_fillable_line_metrics(ir)
    signature_rules = emit.SignatureRuleWriting(
        page["cells"], 2, ir_page["rules"], ir_page["text_runs"], metrics)
    return bool(signature_rules.for_cell(cell["id"]))


def mutate_signature_rule_gap() -> None:
    """Widen the sliver-gap fixture's own blank sliver past the fixture's
    own `glyph_height_pt` (9.675pt, measured over the whole synthetic
    corpus).

    Nothing about either cell's own bordering wall moves relative to ITS
    caption/item ink, and the vector rule stays exactly where it is,
    straddling the rule-owner's own bottom wall -- only the sliver's own
    height does, from 3.0pt (comfortably under the metric, 2316-2021's own
    1.32pt and 0.54pt gaps at this fixture's own scale) to a gap far past
    it. This is a real wall move, `make_fixtures.
    COMB_BAND_REUNIFICATION_NOTCH_SIZE_PT`'s own precedent (0.0pt -> 12pt)
    for mutating a fixture's geometry rather than a caption's own text --
    what breaks is the fact `emit.SignatureRuleWriting`'s own sliver-gap
    extension reads: a genuine gap is bridged only when it is smaller than
    the form's own `glyph_height_pt`, exactly `p1c322`'s own h178/`p1c327`
    refusal (22.8pt over a 4.65pt metric) in miniature.
    """
    fixtures.SIGNATURE_RULE_GAP_SLIVER_HEIGHT_PT = 15.0


def prove_signature_rule_gap(stream: Any) -> int:
    """Prove F226's sliver-gap extension can fail, via a real PDF mutation.

    Not one of extract.py's own checks, and deliberately run outside
    prove()'s CASES/CONTRACT_ONLY accounting, the identical shape
    prove_row_number/prove_comb_band_reunification/prove_signature_rule/
    prove_ink_trim_comb already give their own findings:
    `emit.SignatureRuleWriting`'s sliver-gap extension is entirely a
    `lattice.py`/`emit.py` decision (a rule-owning `label` cell's own
    caption sitting one row down, across a genuinely blank sliver, bridged
    only under the form's own `glyph_height_pt`) with no new extract-level
    primitive of its own, so folding it into that table would misname what
    it tests: `extract.py` extracts this fixture's rules and text runs
    identically whether the mutation below has run or not, and every one
    of its own checks agrees. What changes is a later-stage geometric fact
    neither `extract.SELF_TEST_CHECKS` nor `CONTRACT_ONLY` is about.

    Same method as every case in CASES -- mutate the source PDF, rebuild,
    observe -- carried one stage further than `extract.gather_evidence`
    reaches: `lattice.build_layout` and `emit.SignatureRuleWriting` (with
    its own `_min_fillable_line_metrics`) run over the rebuilt IR too, the
    same calls `batch.py` chains after extract.py for every real bundle.
    """
    failures: list[str] = []
    with tempfile.TemporaryDirectory() as scratch:
        root = pathlib.Path(scratch)
        fixtures.build_all(root)
        claimed = _signature_rule_gap_claimed(root)
        if claimed is not True:
            failures.append(
                f"the unmutated sliver-gap fixture's claim is {claimed!r}, "
                f"not True; the fixture never carried the property")
        print(f"  {'unmutated':<12} {'OK' if claimed is True else 'BROKEN':<5} "
              f"the sliver-gap extension bridges the fixture's own 3.0pt "
              f"blank sliver to claim its rule", file=stream)

    previous = fixtures.SIGNATURE_RULE_GAP_SLIVER_HEIGHT_PT
    try:
        mutate_signature_rule_gap()
        with tempfile.TemporaryDirectory() as scratch:
            root = pathlib.Path(scratch)
            fixtures.build_all(root)
            claimed = _signature_rule_gap_claimed(root)
            good = claimed is False
            if not good:
                failures.append(
                    f"the widened sliver-gap fixture's claim is {claimed!r}, "
                    f"not False; the mutation did not clear the height gate")
            print(f"  {'signature-rule-gap':<12} {'OK' if good else 'WEAK':<5} "
                  f"the sliver widens from {fmt(previous)}pt to "
                  f"{fmt(fixtures.SIGNATURE_RULE_GAP_SLIVER_HEIGHT_PT)}pt, "
                  f"past the fixture's own glyph_height_pt, and the "
                  f"extension refuses to bridge it", file=stream)
    finally:
        fixtures.SIGNATURE_RULE_GAP_SLIVER_HEIGHT_PT = previous

    for message in failures:
        print(f"    FAIL {message}", file=stream)
    print(f"prove-signature-rule-gap: "
          f"{'PASS' if not failures else f'{len(failures)} FAILURE(S)'} over "
          f"1 source-level mutation, run outside extract.py's own "
          f"CASES/CONTRACT_ONLY accounting (see prove_signature_rule_gap)",
          file=stream)
    return 1 if failures else 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.parse_args(argv)
    extract_result = prove(sys.stderr)
    row_number_result = prove_row_number(sys.stderr)
    comb_band_reunification_result = prove_comb_band_reunification(sys.stderr)
    signature_rule_result = prove_signature_rule(sys.stderr)
    ink_trim_comb_result = prove_ink_trim_comb(sys.stderr)
    signature_rule_gap_result = prove_signature_rule_gap(sys.stderr)
    return 1 if (extract_result or row_number_result
                or comb_band_reunification_result
                or signature_rule_result or ink_trim_comb_result
                or signature_rule_gap_result) else 0


if __name__ == "__main__":
    raise SystemExit(main())
