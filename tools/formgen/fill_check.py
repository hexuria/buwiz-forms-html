#!/usr/bin/env python3
"""Fill a generated form in a browser, print it, and re-extract what was typed.

emit.py's field layer claims three things that markup alone cannot demonstrate:
a comb behaves as one field (typing advances between slots, backspace retreats),
a typed glyph lands in the slot the source drew, and a filled form still carries
every rule of an empty one. This checks all three the way the rest of the
pipeline checks everything else -- by printing to PDF and re-extracting, never
by looking at pixels.

    keyboard -> DOM               proves the comb advances and retreats
    fill everything -> print      produces a filled sheet
    re-extract -> IR              gives every typed glyph an exact origin
    glyph centre vs slot centre   proves the glyphs land in the slots
    diff_ir(structural)           proves the rules did not move

The slot centres come from the layout's measured `slot_x`, never from
index*pitch: 2551Q's comb pitch deviates by up to 0.12pt, so an arithmetic
centre would be checking the generator against the same wrong number twice.

The deviation gate is verify.py's own position tolerance. It is not a knob to
turn: a glyph that does not sit in its box is a form that prints a TIN across a
divider, and the answer to that is to fix the geometry.

Usage:
    python3 tools/formgen/fill_check.py \
        --ir build/ir/2551q-2018.ir.json \
        --layout build/layout/2551q-2018.layout.json \
        --html build/html/2551q-2018.html --json build/fill/2551q-2018.json
"""

from __future__ import annotations

import argparse
import json
import pathlib
import statistics
import sys
from typing import Any, Iterable, Sequence

_HERE = pathlib.Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import audit  # noqa: E402  - sibling modules, imported not shelled out
import emit  # noqa: E402
import extract  # noqa: E402
import verify  # noqa: E402

REPORT_VERSION = 1

# A glyph is expected on the centre of the box the source drew. The gate is
# verify.py's position tolerance, because "landed in its slot" is the same
# claim about the same units as "this rule is where the PDF put it".
DEFAULT_CENTRE_TOL_PT = verify.DEFAULT_POSITION_TOL_PT

# A digit per slot, cycling, so a glyph found one slot over is a *different*
# character and not a plausible near-miss. A single "0" goes in plain fields:
# one glyph has one origin, which is what the left-inset check needs.
COMB_ALPHABET = "0123456789"
PLAIN_VALUE = "0"


def field_cells(layout: dict[str, Any]) -> list[tuple[int, dict[str, Any]]]:
    """Every field cell, page-major, in the layout's own order."""
    return [(int(page["index"]), cell)
            for page in layout["pages"] for cell in page["cells"]
            if cell["kind"] == "field"]


def comb_value(capacity: int) -> str:
    return "".join(COMB_ALPHABET[i % len(COMB_ALPHABET)] for i in range(capacity))


def tin_cell(cells: Sequence[tuple[int, dict[str, Any]]]) -> dict[str, Any] | None:
    """The widest comb on the first page: on a BIR sheet that is the TIN.

    Chosen by capacity and then by reading order, so it is a property of the
    layout rather than a form-specific selector.
    """
    page = min((index for index, _ in cells), default=None)
    combs = [cell for index, cell in cells if index == page and cell.get("comb")]
    if not combs:
        return None
    return max(sorted(combs, key=lambda c: (c["y0"], c["x0"])),
               key=lambda c: int(c["comb"]["cells"]))


# Re-renders every growable band through the API a caller would use to add a
# row, then reports whether the rows that came back are fillable. The rows are
# rebuilt from the band blob and the <template>, so a field layer that only
# existed in the pre-rendered markup would come back empty here -- and the fill
# and landing checks that follow run over these rebuilt rows, not the originals.
BAND_PROBE_JS = """() => {
  const api = window.formgen;
  if (!api || !api.bands || !api.bands.length) { return []; }
  return api.bands.map((band) => {
    const result = api.setBandRows(band.id, band.capacity);
    const content = document.getElementById("band-content-" + band.id);
    const cells = content.querySelectorAll("[data-cell-kind='field']");
    const ids = new Set();
    let inputs = 0, named = 0;
    for (const cell of cells) {
      for (const input of cell.querySelectorAll("input.fi")) {
        inputs += 1;
        ids.add(input.id);
        if (input.name === cell.id && input.id.startsWith(cell.id)) { named += 1; }
      }
    }
    return {id: band.id, capacity: band.capacity, rendered: result.rendered,
            field_cells: cells.length, inputs: inputs,
            correctly_named: named, unique_ids: ids.size};
  });
}"""


TYPE_PROBE_JS = """(spec) => {
  const cell = document.getElementById(spec.id);
  const slots = cell.querySelectorAll("input.fi[data-slot-index]");
  return Array.from(slots, (input) => input.value);
}"""

# Reports what it found as well as what it set. Two things are not the field
# layer's to answer for and both are measured here rather than assumed:
#
#   * a form document does not carry every field in the layout, because
#     guides.py moves a claimed region to guide.html;
#   * a box that cannot show the character typed into it. Two ways: it is
#     narrower than the character, which is the browser's own measurement
#     (scrollWidth against clientWidth on the input), or it is so short that the
#     size fitted into it is below emit.FIELD_MIN_SIZE_PT -- the threshold the
#     generator already warns at, so this is not a second opinion. 1600WP has
#     149 cells 0.42pt tall. Recorded per input, because a comb can have one
#     degenerate slot (1600WP has a 0.55pt one) beside thirteen usable ones.
FILL_JS = """(spec) => {
  const present = [], absent = [], unshowable = [];
  let filled = 0;
  for (const entry of spec.entries) {
    const cell = document.getElementById(entry.id);
    if (!cell) { absent.push(entry.id); continue; }
    present.push(entry.id);
    const inputs = cell.querySelectorAll("input.fi");
    for (let i = 0; i < inputs.length; i++) {
      if (entry.set) { inputs[i].value = entry.value.charAt(i) || ""; filled += 1; }
      const size = parseFloat(getComputedStyle(inputs[i]).fontSize);
      if (inputs[i].scrollWidth > inputs[i].clientWidth || size < spec.minSizePx) {
        const slot = inputs[i].getAttribute("data-slot-index");
        unshowable.push([entry.id, slot === null ? -1 : parseInt(slot, 10)]);
      }
    }
  }
  return {present: present, absent: absent, unshowable: unshowable, filled: filled};
}"""


def keyboard_probe(page: Any, cell: dict[str, Any]) -> dict[str, Any]:
    """Type a TIN one keystroke at a time, then delete it one keystroke at a time.

    This is the only part of the check that exercises the field script rather
    than the markup: nothing is set through `value`, so the slot a character
    lands in is whatever the delegated handlers decided, and an advance that
    does not happen shows up as every digit piling into slot 0.
    """
    capacity = int(cell["comb"]["cells"])
    value = comb_value(capacity)
    page.focus(f'#{cell["id"]}-s0')
    page.keyboard.type(value)
    typed = page.evaluate(TYPE_PROBE_JS, {"id": cell["id"]})
    focused_after_typing = page.evaluate("() => document.activeElement.id")

    for _ in range(capacity):
        page.keyboard.press("Backspace")
    erased = page.evaluate(TYPE_PROBE_JS, {"id": cell["id"]})
    focused_after_erase = page.evaluate("() => document.activeElement.id")

    page.focus(f'#{cell["id"]}-s0')
    page.keyboard.type(value)
    retyped = page.evaluate(TYPE_PROBE_JS, {"id": cell["id"]})
    return {
        "cell": cell["id"],
        "capacity": capacity,
        "typed": "".join(typed),
        "expected": value,
        "advanced": "".join(typed) == value,
        "focus_after_typing": focused_after_typing,
        "erased": "".join(erased),
        "retreated": "".join(erased) == "",
        "focus_after_erase": focused_after_erase,
        "retyped_ok": "".join(retyped) == value,
    }


def fill_and_print(html: pathlib.Path, out_pdf: pathlib.Path, paper: dict[str, Any],
                   entries: list[dict[str, str]],
                   probe_cell: dict[str, Any] | None) -> dict[str, Any]:
    """Everything that needs a browser, in one session."""
    from playwright.sync_api import sync_playwright

    zero = {"top": "0", "bottom": "0", "left": "0", "right": "0"}
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            page = browser.new_page()
            page.goto(html.resolve().as_uri(), wait_until="load")
            bands = page.evaluate(BAND_PROBE_JS)
            probe = keyboard_probe(page, probe_cell) if probe_cell else None
            # The keyboard-typed comb keeps the value its keystrokes produced;
            # everything else is set directly, because forty thousand keystrokes
            # prove nothing the first forty did not.
            for entry in entries:
                entry["set"] = probe_cell is None or entry["id"] != probe_cell["id"]
            found = page.evaluate(FILL_JS, {
                "entries": entries,
                # emit.py's own threshold, in device px: a field it already
                # reports as under-sized is not one to demand a glyph from.
                "minSizePx": emit.FIELD_MIN_SIZE_PT / emit.DEVICE_PX_PT})
            # A browser print fires `beforeprint`; printToPDF does not, so it is
            # dispatched here. The focus is deliberately left in the comb that
            # was just typed, because that is the state a person prints from and
            # the state whose caret the round-trip reads as an extra rule.
            page.evaluate("() => window.dispatchEvent(new Event('beforeprint'))")
            page.emulate_media(media="print")
            page.pdf(path=str(out_pdf),
                     width=verify._inches(float(paper["width_pt"])),
                     height=verify._inches(float(paper["height_pt"])),
                     margin=zero, print_background=True,
                     prefer_css_page_size=False, scale=1.0)
        finally:
            browser.close()
    return {"probe": probe, "found": found, "bands": bands}


def glyphs_in(page_ir: dict[str, Any], x0: float, x1: float,
              y0: float, y1: float) -> list[tuple[str, float, float, float]]:
    """Every glyph whose centre falls inside a rect, as (char, x, width, baseline).

    Taken from `char_origin_offsets_pt`, which extract.py records exactly, so a
    run Chromium chose to merge is still read glyph by glyph. Whitespace is not
    a glyph: it puts no ink on the paper and its advance belongs to the run that
    carries it, so a space drifting into a slot is not a character in that slot.
    """
    found = []
    for run in page_ir["text_runs"]:
        if not (y0 - 1.0 <= float(run["baseline_y"]) <= y1 + 1.0):
            continue
        offsets = verify._origin_offsets(run)
        widths = [float(w) for w in run.get("char_widths_pt") or []]
        for index, char in enumerate(run["text"]):
            if index >= len(offsets) or index >= len(widths):
                break
            if char.isspace():
                continue
            origin = float(run["origin_x"]) + offsets[index]
            centre = origin + widths[index] / 2.0
            if x0 <= centre <= x1:
                found.append((char, origin, widths[index], float(run["baseline_y"])))
    return sorted(found, key=lambda g: g[1])


# Two glyphs of the same character within this of each other, in both axes, are
# the same glyph. Deliberately loose next to the 0.25pt position tolerance: the
# question it answers is "is this the sheet's own pre-printed ink or ours", and
# no field types a character within a point of a printed one. A point rather
# than a quarter of one because a printed baseline can legitimately sit a device
# pixel off -- 1600WP's bold runs come back 0.75pt low, which verify.py reports
# as a mismatch and not a miss.
PREPRINTED_TOL_PT = 1.0


def preprinted(reference: dict[str, Any]) -> dict[int, list[tuple[str, float, float]]]:
    """Every glyph the official form prints, per page, from the source IR.

    A comb the lattice drew across a heading -- 1600WP has one 88pt tall over
    the words "TAX BASE" -- otherwise reads the heading's letters as characters
    somebody typed into it. They are the source's own ink, and the source is
    exactly what this pipeline has a record of.
    """
    out: dict[int, list[tuple[str, float, float]]] = {}
    for page in reference["pages"]:
        glyphs: list[tuple[str, float, float]] = []
        for run in page["text_runs"]:
            offsets = verify._origin_offsets(run)
            baseline = float(run["baseline_y"])
            for index, char in enumerate(run["text"]):
                if index < len(offsets):
                    glyphs.append((char, float(run["origin_x"]) + offsets[index], baseline))
        out[int(page["index"])] = glyphs
    return out


def is_preprinted(glyph: tuple[str, float, float, float],
                  source: Sequence[tuple[str, float, float]]) -> bool:
    char, origin, _width, baseline = glyph
    return any(c == char and abs(x - origin) <= PREPRINTED_TOL_PT
               and abs(y - baseline) <= PREPRINTED_TOL_PT for c, x, y in source)


def owner_of(origin_x: float, baseline: float, page_cells: Sequence[dict[str, Any]],
             not_this: str) -> str | None:
    """The field box a glyph *starts* inside, if it is not the one being checked.

    A character is the field's when it begins inside it. That is what separates
    "our comb printed a digit in the wrong slot", which is a defect of this
    layer, from "the field next door bled into the slot", which is a defect of
    the cell the lattice drew -- 1600-VT classifies a 1.04pt-wide sliver as a
    field, and a digit does not fit in 1.04pt at any size the box can carry.
    """
    for cell in page_cells:
        if cell["id"] == not_this or cell["kind"] != "field":
            continue
        if (cell["x0"] - 0.05 <= origin_x <= cell["x1"] + 0.05
                and cell["y0"] - 0.05 <= baseline <= cell["y1"] + 0.05):
            return cell["id"]
    return None


def check_combs(candidate: dict[str, Any], cells: Sequence[tuple[int, dict[str, Any]]],
                present: set[str], unshowable: set[tuple[str, int]],
                source: dict[int, list[tuple[str, float, float]]],
                ) -> tuple[list[dict[str, Any]], list[float], list[dict[str, Any]]]:
    """Every filled slot: is the right glyph on the measured slot centre?

    A glyph belongs to the slot its *origin* starts in, not the slot its centre
    happens to fall in. Those differ exactly when a character is wider than the
    box it was typed into, which is the case worth telling apart: our own comb
    glyph is centred and therefore begins inside its own slot, while ink from a
    1.04pt-wide sliver next door begins outside every slot of this comb.
    """
    pages = {int(p["index"]): p for p in candidate["pages"]}
    by_page: dict[int, list[dict[str, Any]]] = {}
    for page_index, cell in cells:
        by_page.setdefault(page_index, []).append(cell)
    problems: list[dict[str, Any]] = []
    deviations: list[float] = []
    foreign: list[dict[str, Any]] = []
    for page_index, cell in cells:
        comb = cell.get("comb")
        page_ir = pages.get(page_index)
        if not comb or page_ir is None or cell["id"] not in present:
            continue
        slot_x = [float(v) for v in comb["slot_x"]]
        # Measure against the compartment a taxpayer actually types in. Since
        # the horizontal writing surface landed, `slot_x`'s OUTER values are no
        # longer where the first and last compartments are: they run wall-CENTRE
        # to wall-centre, while the emitted ends are inset to the rails' own
        # painted ink. `emit.comb_slot_edges` reads exactly this pair, and this
        # file must ask its question of the same rectangle.
        #
        # Left on `slot_x`, 2,046 of the corpus's 9,108 outer-compartment
        # centres exceed this file's own 0.25pt tolerance and get named as bad
        # slot landings that are not real. Interior dividers are untouched, so
        # only the two ends move; a comb whose rails were not derivable
        # publishes no writing surface and keeps `slot_x`, which is the same
        # fail-closed direction the producer takes.
        if "writing_x0" in comb and "writing_x1" in comb:
            slot_x = [float(comb["writing_x0"]), *slot_x[1:-1],
                      float(comb["writing_x1"])]
        band_y0 = float(comb.get("y0", cell["y0"]))
        band_y1 = float(comb.get("y1", cell["y1"]))
        expected = comb_value(int(comb["cells"]))

        assigned: dict[int, list[tuple[str, float, float, float]]] = {}
        for glyph in glyphs_in(page_ir, slot_x[0], slot_x[-1], band_y0, band_y1):
            if is_preprinted(glyph, source.get(page_index, ())):
                continue
            slot = next((i for i in range(len(expected))
                         if slot_x[i] <= glyph[1] < slot_x[i + 1]), None)
            if slot is None:
                foreign.append({"cell": cell["id"], "glyph": glyph[0],
                                "x": round(glyph[1], 3),
                                "from": owner_of(glyph[1], glyph[3],
                                                 by_page[page_index], cell["id"])})
            elif (cell["id"], slot) not in unshowable:
                assigned.setdefault(slot, []).append(glyph)

        for index, char in enumerate(expected):
            if (cell["id"], index) in unshowable:
                continue
            here = assigned.get(index, [])
            if len(here) != 1:
                problem = {"cell": cell["id"], "slot": index, "expected": char,
                           "found": "".join(g[0] for g in here) or None,
                           "why": "no glyph in the slot" if not here
                                  else "more than one glyph in the slot"}
                # Name the fields whose own box lies over this slot. Two inputs
                # on the same paper both print, so a slot receiving a second
                # character is a statement about the cells the lattice drew, and
                # the report should say which ones rather than leave a count.
                if len(here) > 1:
                    problem["fields_overlapping_the_slot"] = [
                        other["id"] for other in by_page[page_index]
                        if other["id"] != cell["id"]
                        and other["x0"] < slot_x[index + 1] and other["x1"] > slot_x[index]
                        and other["y0"] < band_y1 + 1.0 and other["y1"] > band_y0 - 1.0]
                problems.append(problem)
                continue
            glyph, origin, width, baseline = here[0]
            centre = (slot_x[index] + slot_x[index + 1]) / 2.0
            deviations.append(origin + width / 2.0 - centre)
            if glyph != char:
                problems.append({"cell": cell["id"], "slot": index, "expected": char,
                                 "found": glyph, "why": "wrong character in the slot"})
            elif not band_y0 - 1.0 <= baseline <= band_y1 + 1.0:
                problems.append({"cell": cell["id"], "slot": index, "expected": char,
                                 "found": glyph, "why": f"baseline {baseline} outside "
                                                        f"the comb band"})
    return problems, deviations, foreign


def check_plain(candidate: dict[str, Any], cells: Sequence[tuple[int, dict[str, Any]]],
                present: set[str], unshowable: set[tuple[str, int]],
                source: dict[int, list[tuple[str, float, float]]],
                ) -> tuple[list[dict[str, Any]], list[float]]:
    """Every filled plain field: its character starts inside it, off the rule.

    What is checked is a *bound*, not an equality: the glyph must begin at or
    after the cell's left edge and no further in than the thickness of the rule
    bounding that edge. That is exactly the guarantee the writing box gives --
    emit.py insets by the bounding rule's own thickness, and drops the inset on
    an axis where the cell is no bigger than its borders -- so pinning it to one
    of those two numbers would be checking the generator against a copy of
    itself.

    A field the browser measured as unable to show the character typed into it
    is excluded rather than failed: 2551Q classifies eight 0.72pt-wide slivers
    as fields, and no typography puts a digit in 0.72pt. They are still counted
    and named, because that is a lattice classification worth seeing.
    """
    pages = {int(p["index"]): p for p in candidate["pages"]}
    by_page: dict[int, list[dict[str, Any]]] = {}
    for page_index, cell in cells:
        by_page.setdefault(page_index, []).append(cell)
    problems: list[dict[str, Any]] = []
    deviations: list[float] = []
    for page_index, cell in cells:
        if cell.get("comb"):
            continue
        page_ir = pages.get(page_index)
        if (page_ir is None or cell["id"] not in present
                or (cell["id"], -1) in unshowable):
            continue
        found = glyphs_in(page_ir, cell["x0"], cell["x1"], cell["y0"], cell["y1"])
        # A glyph that *starts* left of this cell was typed into the field next
        # door and is only reaching in; the cell's own character begins inside
        # it. 1801 has 32 fields 0.48pt wide beside a 1.44pt rule, and their
        # overflow is what would otherwise be read as this field's ink.
        mine = [g for g in found
                if g[0] == PLAIN_VALUE and g[1] >= float(cell["x0"]) - 0.05
                and not is_preprinted(g, source.get(page_index, ()))]
        if not mine:
            # Name the neighbour whose character reached in, if there is one.
            # 1702Q puts a 0.48pt-wide field beside a 12.84pt one, and the two
            # characters land 0.95pt apart on the same baseline: the sheet gets
            # both, one on top of the other, and the extractor reports one.
            intruders = sorted({
                owner for glyph in found
                if glyph[1] < float(cell["x0"]) - 0.05
                and not is_preprinted(glyph, source.get(page_index, ()))
                for owner in [owner_of(glyph[1], glyph[3], by_page[page_index],
                                       cell["id"])] if owner})
            problem = {"cell": cell["id"], "why": "no typed glyph in the cell"}
            if intruders:
                problem["character_collides_with"] = intruders
            problems.append(problem)
            continue
        clearance = mine[0][1] - float(cell["x0"])
        deviations.append(clearance)
        limit = verify_border(cell, "left") + DEFAULT_CENTRE_TOL_PT
        if clearance > limit:
            problems.append({"cell": cell["id"], "why": f"the character starts "
                             f"{clearance:.2f}pt inside the cell, past the "
                             f"{limit:.2f}pt its own left rule allows"})
    return problems, deviations


def verify_border(cell: dict[str, Any], side: str) -> float:
    entry = (cell.get("border") or {}).get(side) or {}
    return float(entry.get("thickness_pt") or 0.0)


def summarise(values: Iterable[float]) -> dict[str, Any]:
    data = sorted(abs(v) for v in values)
    if not data:
        return {"count": 0}
    return {"count": len(data), "max_pt": round(data[-1], 4),
            "median_pt": round(statistics.median(data), 4),
            "p95_pt": round(data[min(len(data) - 1, int(0.95 * len(data)))], 4)}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--ir", type=pathlib.Path, required=True)
    parser.add_argument("--layout", type=pathlib.Path, required=True)
    parser.add_argument("--html", type=pathlib.Path, required=True)
    parser.add_argument("--guide-plan", type=pathlib.Path, default=None,
                        help="What guides.py moved to guide.html leaves the form's "
                             "denominator, exactly as in audit.py.")
    parser.add_argument("--pdf", type=pathlib.Path, default=None,
                        help="Where the filled PDF lands (default: beside the HTML).")
    parser.add_argument("--tol-centre", type=float, default=DEFAULT_CENTRE_TOL_PT,
                        help="Read the module docstring before touching this.")
    parser.add_argument("--json", type=pathlib.Path, default=None)
    args = parser.parse_args(argv)

    reference = json.loads(args.ir.read_text(encoding="utf-8"))
    layout = json.loads(args.layout.read_text(encoding="utf-8"))
    if reference["source"]["sha256"] != layout["source"]["sha256"]:
        print("layout was built from a different PDF than the IR", file=sys.stderr)
        return 2
    relocated = {"rules": 0, "text_runs": 0, "images": 0}
    if args.guide_plan is not None and args.guide_plan.is_file():
        reference, relocated = audit.form_side(
            reference, json.loads(args.guide_plan.read_text(encoding="utf-8")))

    cells = field_cells(layout)
    entries = [{"id": cell["id"], "comb": bool(cell.get("comb")),
                "value": (comb_value(int(cell["comb"]["cells"])) if cell.get("comb")
                          else PLAIN_VALUE)}
               for _page, cell in cells]
    pdf = args.pdf or args.html.with_suffix(".filled.pdf")
    pdf.parent.mkdir(parents=True, exist_ok=True)

    session = fill_and_print(args.html, pdf, reference["paper"], entries, tin_cell(cells))
    candidate = extract.extract(pdf, reference["form"]["code"],
                                reference["form"]["revision"], None)
    report = verify.diff_ir(reference, candidate, verify.Tolerances(),
                            roles=["structural"])
    totals = report.get("totals", {})

    found = session["found"]
    present = set(found["present"])
    unshowable = {(cell, int(slot)) for cell, slot in found["unshowable"]}
    source = preprinted(reference)
    comb_problems, comb_deviations, foreign = check_combs(
        candidate, cells, present, unshowable, source)
    plain_problems, plain_inset = check_plain(candidate, cells, present, unshowable,
                                              source)
    probe = session["probe"]

    slots = sum(int(c["comb"]["cells"]) for _p, c in cells
                if c.get("comb") and c["id"] in present)
    plain = sum(1 for _p, c in cells if not c.get("comb") and c["id"] in present)
    centre = summarise(comb_deviations)
    over = [d for d in comb_deviations if abs(d) > args.tol_centre]

    result = {
        "report_version": REPORT_VERSION,
        "form": reference["form"], "html": str(args.html), "pdf": str(pdf),
        # `absent` is the guide's share of the sheet: guides.py moves a claimed
        # region to guide.html, so the form document carries fewer fields than
        # the layout has, and that is the split working rather than a loss.
        "fields": {"in_layout": len(cells), "in_document": len(present),
                   "absent_from_document": len(found["absent"]),
                   "comb_slots": slots, "plain": plain,
                   "cannot_show_a_character": sorted(unshowable),
                   "inputs_set": found["filled"]},
        "keyboard": probe,
        "bands_rerendered": session["bands"],
        "slot_landing": {"checked": centre["count"], "deviation": centre,
                         "over_tolerance": len(over), "tolerance_pt": args.tol_centre,
                         "problems": comb_problems[:20],
                         "problem_count": len(comb_problems),
                         # Ink from a neighbouring field that reaches into a
                         # slot. Reported, not gated: the character began inside
                         # a box the lattice drew too small to hold it, which no
                         # choice this layer can make will fix.
                         "ink_from_another_field": foreign[:20],
                         "ink_from_another_field_count": len(foreign)},
        "plain_landing": {"checked": len(plain_inset),
                          "left_clearance": summarise(plain_inset),
                          "problems": plain_problems[:20],
                          "problem_count": len(plain_problems)},
        "structure": {"paper_ok": bool(report.get("paper", {}).get("ok", True)),
                      "guide_relocated": relocated,
                      "rules_ref": sum(p["stats"]["rules_structural"]
                                       for p in reference["pages"]),
                      "rules_missing": totals.get("rules_missing", 0),
                      "rules_extra": totals.get("rules_extra", 0),
                      "rules_thickness_violations":
                          totals.get("rules_thickness_violations", 0)},
    }
    structure = result["structure"]
    # `rules_extra` is gated as hard as `rules_missing`, and the printing is
    # deliberately left with the cursor in the comb it just typed: a caret
    # painted onto a printed sheet is a 1px black bar the round-trip cannot tell
    # from a rule, and finding it is the reason it is gated here.
    result["ok"] = bool(
        structure["paper_ok"] and not structure["rules_missing"]
        and not structure["rules_extra"]
        and not structure["rules_thickness_violations"]
        and not comb_problems and not plain_problems and not over
        and (probe is None or (probe["advanced"] and probe["retreated"]
                               and probe["retyped_ok"]))
        and all(b["rendered"] == b["capacity"] and b["inputs"] > 0
                and b["correctly_named"] == b["inputs"] == b["unique_ids"]
                for b in session["bands"]))

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    json.dump(result, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
