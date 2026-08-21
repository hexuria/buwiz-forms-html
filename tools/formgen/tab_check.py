#!/usr/bin/env python3
"""Walk every input on a generated form with the Tab key and grade the order.

A user hand-verified two forms this way: start at the first input, press Tab
until nothing more gets focused, and note which boxes were never reached. That
is not a thing anybody should keep doing by hand, and it is not a thing an
agent can self-verify a form against without automating it. This is that
automation.

The walk, per form, mirrors exactly what a person did:

  1. Load the document (`wait_until="load"` is enough -- the documents have
     three inline `<script>` tags and a JSON blob, no `defer`/`async`, no
     `DOMContentLoaded` handler, and bands are pre-rendered at capacity into
     the static HTML, so there is no post-load DOM mutation to race).
  2. Focus the first input.
  3. Press Tab, over and over, recording who got focused, until focus lands
     on `<body>` (Chromium hands focus back to the document before wrapping)
     or a hard cap is hit.
  4. Compare the reached sequence against the *expected* reading order.

The expected order is read off the live DOM, not off `build/layout/*.json`.
Every fact the layout has that this walk needs -- a field cell's row, its
left edge, its comb slots or writing regions, in reading order -- is already
in the rendered document as `data-row`, `style.left` and DOM child order
(emit.py copies them there verbatim; verified against the layout for several
forms while this module was built). Reading it from the DOM rather than a
side-channel JSON file means the walk measures the artifact it is standing
in, the same discipline band_drive.py's PROBE follows for authored style
values, and it is what lets this run in CI, where `build/` -- gitignored,
regenerated from six PDFs this repo will never commit -- does not exist but
the tracked `forms/` tree does.

Row identity comes from `data-row`, not from a `row_tops()`-style pitch/2
reclustering of raw y-coordinates. `data-row` already *is* that clustering,
done exactly rather than approximately: it is the cell's index into the
layout's own y_lattice (verified directly -- `0605-1999` page 1's
`y_lattice[18:20]` is `[309.37, 310.08]`, matching `p1c37`'s and `p1c38`'s
`y0` to the point, and their `row` values are 18 and 19: two different rows
0.7pt apart, exactly the sub-point-drift case `row_tops()`'s comment warns a
fixed epsilon gets wrong in both directions). Reusing the field the lattice
already computed is more precise than re-deriving an approximation of it, and
it needs no page-level `row_pitch_pt`, which does not exist outside a
growable band's own blob.

LIMITATION, stated here because it has to be stated somewhere plain: this
walk can only judge inputs that EXIST. A place on the form where an input is
MISSING cannot be red here, because there is nothing to be red -- it looks
identical to correct paper. Missing-field detection is the job of the
`?debug=fields` overlay's "printed box with no input" census -- which is why
(T4-addendum) this module also calls that SAME census, `window.
formgenFieldCensus()`, once per form after the walk, and burns its tone-aware
"vacant" boxes into the same page-<N>.png as blue, alongside the green/red
tab verdicts. One image answers both questions; nobody has to tab AND
separately eyeball the sheet.

Usage:
    python3 tools/formgen/tab_check.py                  # every form in forms/
    python3 tools/formgen/tab_check.py 2551q-2018        # one form
    python3 tools/formgen/tab_check.py --self-test
"""

from __future__ import annotations

import argparse
import html
import json
import pathlib
import re
import sys
import tempfile
import time
from typing import Any

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parent.parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import band_drive  # noqa: E402  - sibling module, imported not shelled out; bundle_dir() only
import emit  # noqa: E402  - sibling module; only FIELD_DEBUG_JS, for the self-test fixture

# Bumped for T4-addendum: `report["vacant"]` and `report["totals"]["vacant"]`
# are new, burned from the SAME tone-aware census `?debug=fields` paints
# (window.formgenFieldCensus(), emit.py), so a consumer holding schema 1
# knows the field is not there rather than silently reading `None` as zero.
SCHEMA_VERSION = 2

LIMITATION = (
    "This walk can only judge inputs that EXIST. A place on the form where an "
    "input is MISSING cannot be red here, because there is nothing to be red "
    "-- it looks identical to correct paper. Missing-field detection is the "
    "job of the ?debug=fields overlay's \"printed box with no input\" census "
    "and the findings ledger, not of tabbing -- which is why this module also "
    "burns that SAME census (window.formgenFieldCensus(), one source of "
    "truth, not a second implementation) into every page-<N>.png as blue, "
    "alongside the green/red tab verdicts."
)

GREEN = "#0a8a3e"
RED = "#d32f2f"
BLUE = "#1565c0"

# The census FIELD_DEBUG_JS exposes unconditionally (before its own
# ?debug=fields token gate -- see emit.py's FIELD_DEBUG_JS for why that is
# safe: it only reads the document, never mutates it). Calling it needs no
# query string on the page this module already loaded for the tab walk.
CENSUS_JS = "() => window.formgenFieldCensus()"

PAGE_ID_RE = re.compile(r"^page-(\d+)$")

# ---------------------------------------------------------------------------
# In-page probes
# ---------------------------------------------------------------------------

# Everything the expected order and the artifacts need, read in one round
# trip: per page, per field-bearing cell, per input inside it (in DOM order --
# left-to-right for a comb's slots, left-to-right for a plain field's writing
# regions, because that is the order emit.py appends them in). Geometry is
# converted to page-relative pt right here, from the ratio of the page's own
# measured pixel box to its authored pt size, so it holds regardless of
# device-pixel-ratio or default viewport -- the same reasoning band_drive.py
# uses for reading authored `style` values instead of trusting a bare
# `getBoundingClientRect()`, adapted for artifacts that only need to be
# legible rather than sub-device-pixel exact.
DISCOVER_JS = r"""
() => {
  const pages = [];
  document.querySelectorAll(".page").forEach((pageEl) => {
    const m = /^page-(\d+)$/.exec(pageEl.id || "");
    if (!m) { return; }
    const widthPt = parseFloat(pageEl.style.width);
    const heightPt = parseFloat(pageEl.style.height);
    const pageRect = pageEl.getBoundingClientRect();
    const sx = pageRect.width / widthPt;
    const sy = pageRect.height / heightPt;
    // `.f`, not `[data-cell-kind="field"]`: emit.py's own cell_markup sets
    // `classes = "c f"` whenever a cell carries a writing surface, kind-
    // independent, and a comb overlaid on a structurally "mixed" cell (part
    // pre-printed label, part real slots -- e.g. 1604C's p1c4) is exactly
    // such a case. Selecting on `data-cell-kind="field"` alone silently
    // dropped every one of those from the expected order while their inputs
    // still got tabbed to for real, which is what
    // `unexpected_focus_targets` caught while this module was built.
    const cells = [];
    pageEl.querySelectorAll(".f").forEach((cellEl) => {
      const inputs = [];
      cellEl.querySelectorAll("input.fi").forEach((inp) => {
        const box = inp.closest(".s") || inp;
        const r = box.getBoundingClientRect();
        inputs.push({
          id: inp.id,
          x: (r.x - pageRect.x) / sx,
          y: (r.y - pageRect.y) / sy,
          w: r.width / sx,
          h: r.height / sy,
        });
      });
      if (inputs.length) {
        cells.push({
          row: parseInt(cellEl.getAttribute("data-row"), 10),
          left: parseFloat(cellEl.style.left),
          inputs: inputs,
        });
      }
    });
    pages.push({index: parseInt(m[1], 10), width_pt: widthPt, height_pt: heightPt, cells: cells});
  });
  return pages;
}
"""

# One listener for the whole walk -- not an `evaluate()` per keypress, which
# is what would blow the runtime budget across 45,333 corpus-wide inputs
# (2,868 on 1701-2018 alone). `capture: true` so a focusin on a nested target
# (a comb slot's own input) is still seen at the document.
INIT_JS = r"""
() => {
  window.__tabCheckSeq = [];
  document.addEventListener("focusin", (ev) => {
    if (ev.target && ev.target.id) { window.__tabCheckSeq.push(ev.target.id); }
  }, true);
  const first = document.querySelector("input.fi");
  if (first) { first.focus(); }
}
"""

AT_BODY_JS = "() => document.activeElement === document.body"
READ_SEQUENCE_JS = "() => window.__tabCheckSeq"
BLUR_JS = "() => { const el = document.activeElement; if (el && el.blur) { el.blur(); } }"

MARKER_LAYER_ID = "tab-check-markers"

# Burns the verdict onto the page: a solid outline (not a hairline -- the
# artifact is read on a phone), a translucent fill so the underlying ink is
# still visible, and a filled label chip carrying the tab sequence number (or
# "×" for a box no press ever reached). A vacant (blue) marker carries no
# sequence number -- it is not an input at all -- so an empty/falsy label
# skips the chip rather than painting an empty one.
MARK_JS = r"""
(spec) => {
  const pageEl = document.getElementById("page-" + spec.page);
  if (!pageEl) { return; }
  const layer = document.createElement("div");
  layer.id = "%(layer_id)s";
  layer.style.cssText = "position:absolute;left:0;top:0;width:100%%;height:100%%;"
    + "pointer-events:none;z-index:2147483647";
  for (const m of spec.markers) {
    const box = document.createElement("div");
    box.style.cssText = "position:absolute;left:" + m.x + "pt;top:" + m.y + "pt;"
      + "width:" + m.w + "pt;height:" + m.h + "pt;box-sizing:border-box;"
      + "border:1.5pt solid " + m.color + ";background:" + m.color + "26";
    layer.appendChild(box);
    if (m.label) {
      const chip = document.createElement("div");
      chip.textContent = m.label;
      const chipTop = m.y > 7 ? m.y - 7 : m.y;
      chip.style.cssText = "position:absolute;left:" + m.x + "pt;top:" + chipTop + "pt;"
        + "background:" + m.color + ";color:#fff;font:bold 6.5pt/1.4 Arial,Helvetica,sans-serif;"
        + "padding:0.5pt 2pt;border-radius:1.5pt;white-space:nowrap";
      layer.appendChild(chip);
    }
  }
  pageEl.appendChild(layer);
}
""" % {"layer_id": MARKER_LAYER_ID}

UNMARK_JS = (
    '() => { const el = document.getElementById("%s"); '
    "if (el && el.parentNode) { el.parentNode.removeChild(el); } }" % MARKER_LAYER_ID
)


# ---------------------------------------------------------------------------
# Corpus discovery
# ---------------------------------------------------------------------------


def discover_slugs(forms_root: pathlib.Path, layout_dir: pathlib.Path) -> list[str]:
    """Every slug a run should walk.

    Prefers `build/layout/*.layout.json`, matching band_drive.py and
    fill_check.py, because a local run against a freshly regenerated corpus
    should walk exactly the forms that were regenerated. `build/` is
    gitignored and does not exist on a hosted runner (see
    .github/workflows/formgen.yml's own explanation of why), so the fallback
    enumerates the tracked `forms/` tree directly -- the same tree
    validate_tree.py checks with no PDF and no `build/` in sight. Either way
    this only decides which bundles to open; the walk itself reads nothing
    but the live DOM.
    """
    if layout_dir.is_dir():
        slugs = sorted(p.name[: -len(".layout.json")] for p in layout_dir.glob("*.layout.json"))
        if slugs:
            return slugs
    slugs: set[str] = set()
    for base in (forms_root, forms_root / "extra"):
        if not base.is_dir():
            continue
        for child in sorted(base.iterdir()):
            if child.is_dir() and (child / "index.html").is_file():
                slugs.add(child.name)
    return sorted(slugs)


# ---------------------------------------------------------------------------
# Expected order and the walk itself
# ---------------------------------------------------------------------------


class DuplicateInputIdError(RuntimeError):
    """Two physically distinct inputs share one DOM id (F215).

    `build_expected` and `compute_verdicts` both key everything by id, in
    plain dicts, with no uniqueness assertion of their own -- so a duplicate
    id would silently collapse two real fields into one verdict slot and drop
    one of them out of grading entirely: a genuine, silent false green, not a
    red marker a reviewer could ever see. Raised here, at the one place the
    id list is actually built, so neither downstream dict can ever see a
    duplicate to begin with; `compute_verdicts`'s own `rank = {id: i for
    i, id in enumerate(expected_order)}` is therefore safe by construction,
    not by a second check repeating this one.
    """


def build_expected(pages_raw: list[dict[str, Any]]
                   ) -> tuple[list[str], dict[str, dict[str, Any]], dict[int, dict[str, float]]]:
    """The reading-order sequence every input is graded against.

    Cells are ordered (row, left) -- row from the lattice, exact, not a
    reclustered y0 -- and within a cell, inputs keep the DOM order the
    document already put them in: slot 0..capacity-1 for a comb, writing
    region 0..N for a plain field the source rules into several boxes.

    Raises `DuplicateInputIdError` (F215) the moment a second input claims an
    id `input_meta` already holds -- a hard failure, not a silent overwrite.
    """
    expected_order: list[str] = []
    input_meta: dict[str, dict[str, Any]] = {}
    page_meta: dict[int, dict[str, float]] = {}
    for page in sorted(pages_raw, key=lambda p: p["index"]):
        index = int(page["index"])
        page_meta[index] = {"width_pt": page["width_pt"], "height_pt": page["height_pt"]}
        for cell in sorted(page["cells"], key=lambda c: (c["row"], c["left"])):
            for inp in cell["inputs"]:
                input_id = inp["id"]
                if input_id in input_meta:
                    raise DuplicateInputIdError(
                        f"duplicate input id {input_id!r} on page {index}: two "
                        "physically distinct inputs would collapse into one "
                        "verdict slot and one would be silently dropped from "
                        "grading (F215)")
                expected_order.append(input_id)
                input_meta[input_id] = {
                    "page": index,
                    "x_pt": round(inp["x"], 2), "y_pt": round(inp["y"], 2),
                    "w_pt": round(inp["w"], 2), "h_pt": round(inp["h"], 2),
                }
    return expected_order, input_meta, page_meta


def run_walk(tab: Any, input_count: int) -> tuple[list[str], int, int, str]:
    """Tab from the first input until body, recording who got focused.

    Every `input.fi` in the corpus carries no `tabindex` and is never
    disabled (checked corpus-wide while this module was built), so a forward
    walk starting at the first field visits each of them exactly once before
    reaching `<body>` -- the one other focusable element in the document, the
    "Guidelines and Instructions" link, sits *before* the first field and so
    is never reached walking forward from it. Body is therefore expected at
    exactly `input_count` presses. The first stretch of presses skips the
    per-press `evaluate()` round trip on that assumption (it is what keeps a
    2,868-input form inside budget); a small margin before the expected
    landing point, and everything after it, is checked for real, so a form
    that violates the assumption still terminates correctly rather than
    silently overrunning.
    """
    cap = 2 * max(input_count, 0) + 50
    tab.evaluate(INIT_JS)
    presses = 0
    terminated_by = "cap"
    checked_from = max(input_count - 3, 0)
    while presses < cap:
        tab.keyboard.press("Tab")
        presses += 1
        if presses < checked_from:
            continue
        if tab.evaluate(AT_BODY_JS):
            terminated_by = "body"
            break
    sequence = tab.evaluate(READ_SEQUENCE_JS)
    return sequence, presses, cap, terminated_by


def compute_verdicts(expected_order: list[str], sequence: list[str]
                     ) -> tuple[dict[str, str], dict[str, int], int, list[str]]:
    """green / red-skipped / red-order, plus the reached-at sequence numbers.

    An input is `green` if, at the moment it is first reached, its expected
    rank (row-cluster then x, folded into one index by `build_expected`'s
    sort) is not behind anything already reached -- i.e. reading the walk in
    the order it actually happened, the sequence of expected-ranks so far is
    non-decreasing. The first press that breaks that is `red-order`: an input
    whose row-cluster (or, tied, whose x) is strictly above one already
    reached. Never-reached inputs are `red-skipped`.

    F214: the VERY FIRST input the walk reaches is graded against a stricter
    rule than the rest. A real Tab walk starts wherever `document.
    querySelector("input.fi")` lands -- DOM order, not reading order -- and
    the honest question is whether that DOM-first element IS the
    reading-order-first one (rank 0). Grading it the same way later entries
    are graded ("not behind anything already reached") is vacuous: nothing
    has been reached yet, so `r >= running_max`'s sentinel is satisfied by
    every rank, and the misplaced field that STARTS the walk always comes
    back green while the fields it pre-empted -- which are themselves in
    correct relative order -- come back red-order instead: with DOM order
    [p2 (rank 1), p1 (rank 0)], the old rule graded p2 green and p1
    red-order, the inverse of the truth. Requiring rank 0 specifically for
    this one entry, and leaving `running_max` at its sentinel when that
    check fails -- exactly as a later red-order entry already leaves it
    unchanged -- fixes that: p2 is graded red-order (it IS the defect) and
    p1 is graded on its own merits again (green, since nothing before it in
    the walk has been accepted yet). Every entry after the first keeps the
    original rule unchanged; this is scoped to the first counted entry only,
    matching F214's own evidence, not a general reordering of the walk.
    """
    rank = {input_id: i for i, input_id in enumerate(expected_order)}
    seen: set[str] = set()
    reached_at: dict[str, int] = {}
    order_ok: dict[str, bool] = {}
    running_max = -1
    duplicates = 0
    unexpected: list[str] = []
    seq_num = 0
    for entry_id in sequence:
        if entry_id not in rank:
            unexpected.append(entry_id)
            continue
        if entry_id in seen:
            duplicates += 1
            continue
        seen.add(entry_id)
        seq_num += 1
        reached_at[entry_id] = seq_num
        r = rank[entry_id]
        in_order = r == 0 if seq_num == 1 else r >= running_max
        if in_order:
            order_ok[entry_id] = True
            running_max = r
        else:
            order_ok[entry_id] = False

    verdicts: dict[str, str] = {}
    for input_id in expected_order:
        if input_id not in seen:
            verdicts[input_id] = "red-skipped"
        elif order_ok[input_id]:
            verdicts[input_id] = "green"
        else:
            verdicts[input_id] = "red-order"
    return verdicts, reached_at, duplicates, unexpected


# ---------------------------------------------------------------------------
# Artifacts: one PNG per page, markers burned in, then removed
# ---------------------------------------------------------------------------


def collect_vacant(census_pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The tone-aware `vacant` marks out of `window.formgenFieldCensus()`.

    Read straight from the SAME computation `?debug=fields` paints blue --
    `emit.py`'s `pageCensus()`, called once via `window.formgenFieldCensus()`
    -- so a candidate box already excludes tint-band decoration (F213) before
    this function ever sees it. This is a re-shaping of that result into
    tab.json's page-relative-pt convention, not a second verdict.
    """
    vacant: list[dict[str, Any]] = []
    for page_result in census_pages or ():
        match = PAGE_ID_RE.match(str(page_result.get("page", "")))
        if not match:
            continue
        page_index = int(match.group(1))
        for mark in page_result.get("marks", ()):
            if mark.get("state") != "vacant":
                continue
            vacant.append({
                "page": page_index,
                "x_pt": round(mark["L"], 2), "y_pt": round(mark["T"], 2),
                "w_pt": round(mark["R"] - mark["L"], 2),
                "h_pt": round(mark["B"] - mark["T"], 2),
            })
    return vacant


def render_artifacts(tab: Any, slug: str, review_dir: pathlib.Path,
                     page_meta: dict[int, dict[str, float]],
                     input_meta: dict[str, dict[str, Any]],
                     verdicts: dict[str, str], reached_at: dict[str, int],
                     vacant: list[dict[str, Any]]) -> list[int]:
    """One `page-<N>.png` per page, the verdict burned into the pixels --
    green/red tab verdicts AND the blue tone-aware vacant census together, so
    one image answers both "does tab order hold" and "is anything printed
    still missing its input"."""
    tab.evaluate(BLUR_JS)  # a lingering :focus highlight is not a verdict
    # The Tab walk scrolls the focused input into view as it goes, so by the
    # time this runs the document is not sitting at its loaded scroll
    # position any more. `getBoundingClientRect()` is viewport-relative, and
    # `page.screenshot(clip=...)` expects document-relative coordinates, so a
    # clip rect read after the walk without resetting scroll silently grabs
    # the wrong region -- proven while this module was built: page 2 of a
    # 771-input form came back showing item 13-17 where the clip math said
    # item 6's growable band should be, off by exactly the walk's scroll
    # distance.
    tab.evaluate("() => window.scrollTo(0, 0)")
    out_dir = review_dir / slug
    out_dir.mkdir(parents=True, exist_ok=True)

    by_page: dict[int, list[str]] = {}
    for input_id, meta in input_meta.items():
        by_page.setdefault(meta["page"], []).append(input_id)
    vacant_by_page: dict[int, list[dict[str, Any]]] = {}
    for entry in vacant:
        vacant_by_page.setdefault(entry["page"], []).append(entry)

    written: list[int] = []
    for page_index in sorted(page_meta):
        markers = []
        for input_id in by_page.get(page_index, []):
            meta = input_meta[input_id]
            verdict = verdicts[input_id]
            color = GREEN if verdict == "green" else RED
            label = str(reached_at[input_id]) if input_id in reached_at else "×"
            markers.append({"x": meta["x_pt"], "y": meta["y_pt"],
                            "w": meta["w_pt"], "h": meta["h_pt"],
                            "color": color, "label": label})
        for entry in vacant_by_page.get(page_index, []):
            markers.append({"x": entry["x_pt"], "y": entry["y_pt"],
                            "w": entry["w_pt"], "h": entry["h_pt"],
                            "color": BLUE, "label": ""})
        tab.evaluate(MARK_JS, {"page": page_index, "markers": markers})
        page_rect = tab.eval_on_selector(f"#page-{page_index}", "el => el.getBoundingClientRect()")
        out_path = out_dir / f"page-{page_index}.png"
        tab.screenshot(path=str(out_path), full_page=True, clip={
            "x": page_rect["x"], "y": page_rect["y"],
            "width": page_rect["width"], "height": page_rect["height"]})
        tab.evaluate(UNMARK_JS)
        written.append(page_index)
    return written


# ---------------------------------------------------------------------------
# One form
# ---------------------------------------------------------------------------


def walk_form(tab: Any, slug: str, bundle: pathlib.Path, review_dir: pathlib.Path) -> dict[str, Any]:
    """Load one form, walk it, grade it, write its artifacts, return the report."""
    errors: list[str] = []
    tab.on("pageerror", lambda exc: errors.append(str(exc)))
    tab.goto((bundle / "index.html").resolve().as_uri(), wait_until="load")

    pages_raw = tab.evaluate(DISCOVER_JS)
    if not pages_raw:
        return {
            "schema_version": SCHEMA_VERSION, "slug": slug, "limitation": LIMITATION,
            "ok": False, "failures": ["no .page elements found in the document"],
            "totals": {"green": 0, "red-skipped": 0, "red-order": 0, "total": 0, "vacant": 0},
        }

    try:
        expected_order, input_meta, page_meta = build_expected(pages_raw)
    except DuplicateInputIdError as exc:
        # F215: a hard per-form failure, not a silent collapse -- the walk
        # cannot grade a form whose own id space is not one-to-one with its
        # inputs, and pretending otherwise is exactly the false green this
        # guard exists to end.
        return {
            "schema_version": SCHEMA_VERSION, "slug": slug, "limitation": LIMITATION,
            "ok": False, "failures": [str(exc)],
            "totals": {"green": 0, "red-skipped": 0, "red-order": 0, "total": 0, "vacant": 0},
        }
    input_count = len(expected_order)

    sequence, presses, cap, terminated_by = run_walk(tab, input_count)
    cap_hit = terminated_by != "body"

    verdicts, reached_at, duplicates, unexpected = compute_verdicts(expected_order, sequence)

    # One evaluate, after the walk: the SAME census `?debug=fields` paints
    # blue, called through the callable emit.py exposes for exactly this
    # (window.formgenFieldCensus) -- not a second implementation of "what is
    # a printed box with no input". Needs no query string: that callable is
    # reachable on every generated form, walked or not.
    census_pages = tab.evaluate(CENSUS_JS)
    vacant = collect_vacant(census_pages)

    pages_written = render_artifacts(tab, slug, review_dir, page_meta, input_meta,
                                     verdicts, reached_at, vacant)

    counts = {"green": 0, "red-skipped": 0, "red-order": 0}
    per_page_counts = {p: {"green": 0, "red-skipped": 0, "red-order": 0} for p in page_meta}
    for input_id, verdict in verdicts.items():
        counts[verdict] += 1
        per_page_counts[input_meta[input_id]["page"]][verdict] += 1
    vacant_by_page: dict[int, int] = {}
    for entry in vacant:
        vacant_by_page[entry["page"]] = vacant_by_page.get(entry["page"], 0) + 1

    inputs_out = []
    for input_id in expected_order:
        meta = input_meta[input_id]
        inputs_out.append({
            "id": input_id, "page": meta["page"],
            "x_pt": meta["x_pt"], "y_pt": meta["y_pt"], "w_pt": meta["w_pt"], "h_pt": meta["h_pt"],
            "reached_at": reached_at.get(input_id),
            "verdict": verdicts[input_id],
        })

    pages_out = [
        {"index": p, "width_pt": page_meta[p]["width_pt"], "height_pt": page_meta[p]["height_pt"],
         "image": f"page-{p}.png" if p in pages_written else None,
         **per_page_counts[p], "vacant": vacant_by_page.get(p, 0)}
        for p in sorted(page_meta)
    ]

    ok = (not cap_hit and not errors and not unexpected
          and counts["red-skipped"] == 0 and counts["red-order"] == 0)

    report = {
        "schema_version": SCHEMA_VERSION,
        "slug": slug,
        "limitation": LIMITATION,
        "ok": ok,
        "walk": {
            "input_count": input_count, "presses": presses, "cap": cap,
            "terminated_by": terminated_by, "cap_hit": cap_hit,
            "sequence": sequence,
            "duplicate_focus_events": duplicates,
            "unexpected_focus_targets": unexpected,
        },
        "pages": pages_out,
        "inputs": inputs_out,
        "vacant": vacant,
        "totals": {**counts, "total": input_count, "vacant": len(vacant)},
        "page_errors": errors,
    }

    out_dir = review_dir / slug
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "tab.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


# ---------------------------------------------------------------------------
# Corpus summary page
# ---------------------------------------------------------------------------

INDEX_STYLE = """\
body{font:14px/1.5 system-ui,sans-serif;margin:2rem;max-width:1100px;color:#111}
h1{font-size:1.3rem;margin:0 0 .3rem} h2{font-size:1rem;margin:1.6rem 0 .3rem}
p.lim{color:#555;font-size:12px;background:#f4f4f4;border-radius:6px;padding:.6rem .9rem;max-width:70ch}
.b{display:inline-block;padding:1px 8px;border-radius:9px;font-size:11px;font-weight:600;white-space:nowrap}
.g{background:#d7f5dd;color:#0a5528} .r2{background:#fbd5d5;color:#7a1010}
.form{border-top:1px solid #e8e8e8;padding-top:.8rem;margin-top:.8rem}
.form h2{display:inline-block;margin-right:.6rem} .counts{color:#555;font-size:12px}
.pages{display:flex;flex-wrap:wrap;gap:.6rem;margin-top:.5rem}
.pages a{display:block} .pages img{max-width:220px;border:1px solid #ccc;border-radius:3px;display:block}
.pages .cap{font-size:11px;color:#555;text-align:center;margin-top:2px}
@media(prefers-color-scheme:dark){body{background:#111;color:#eee}p.lim{background:#1c1c1c;color:#bbb}
.g{background:#123d22;color:#7ee2a0}.r2{background:#3d1616;color:#e88f8f}.form{border-color:#333}
.pages img{border-color:#444}}
"""


def write_index_html(review_dir: pathlib.Path, reports: list[dict[str, Any]]) -> None:
    """One file, no server: every form's pages, verdict counts, and this
    package's own limitation, so the whole corpus can be reviewed from a
    phone by opening a single file."""
    parts = [
        "<!doctype html><html><head><meta charset=\"utf-8\">",
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">",
        "<title>tab_check review</title><style>", INDEX_STYLE, "</style></head><body>",
        "<h1>tab_check review</h1>",
        f"<p class=\"lim\">{html.escape(LIMITATION)}</p>",
    ]
    clean = sum(1 for r in reports if r.get("ok"))
    parts.append(f"<p>{clean}/{len(reports)} forms fully green "
                 f"(green = reached in expected order; red = skipped or reached out of order).</p>")
    for report in sorted(reports, key=lambda r: r["slug"]):
        slug = report["slug"]
        totals = report.get("totals", {"green": 0, "red-skipped": 0, "red-order": 0,
                                       "total": 0, "vacant": 0})
        badge_ok = report.get("ok")
        badge = ("<span class=\"b g\">clean</span>" if badge_ok
                 else "<span class=\"b r2\">red</span>")
        parts.append('<div class="form">')
        parts.append(f'<h2>{html.escape(slug)}</h2>{badge}')
        parts.append(f'<div class="counts">green {totals.get("green", 0)} &middot; '
                     f'red-skipped {totals.get("red-skipped", 0)} &middot; '
                     f'red-order {totals.get("red-order", 0)} &middot; '
                     f'total {totals.get("total", 0)} &middot; '
                     f'vacant (blue) {totals.get("vacant", 0)}</div>')
        for failure in report.get("failures") or ():
            parts.append(f'<div class="counts">{html.escape(str(failure))}</div>')
        parts.append('<div class="pages">')
        for page in report.get("pages") or ():
            if not page.get("image"):
                continue
            href = f'{slug}/{page["image"]}'
            parts.append(
                f'<a href="{href}"><img src="{href}" loading="lazy"></a>'
                f'<div class="cap">p{page["index"]}: g{page.get("green", 0)} '
                f'/ rs{page.get("red-skipped", 0)} / ro{page.get("red-order", 0)} '
                f'/ vacant {page.get("vacant", 0)}</div>')
        parts.append("</div></div>")
    parts.append("</body></html>")
    review_dir.mkdir(parents=True, exist_ok=True)
    (review_dir / "index.html").write_text("".join(parts), encoding="utf-8")


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

# Three inputs. p1c1 (row 0, left 10) and p1c2 (row 0, left 100) are on one
# visual row; p1c3 (row 1, left 10) is on the next. In the DOM, p1c3 is placed
# *before* p1c2 -- exactly the shape of F209 (a later-in-reading-order block
# rendered earlier in reading order gets tabbed to too early; here it is
# p1c2's turn to arrive too late, since it is what the walk finds *after*
# something that reading order says should follow it). p1c1 and p1c3 must
# grade green; p1c2, the one the walk reaches after something below it, must
# grade red-order; nothing is red-skipped.
#
# The `.rl` block is the deliberate T4-addendum fixture: four black
# (structural) rects closing a 20x20pt box at x 130..150 / y 130..150, no
# input and no `.t` text inside it -- printed paper with nothing to write in.
# It must come back from window.formgenFieldCensus() as exactly one vacant
# mark, and that mark must reach report["vacant"] and get painted blue.
# emit.FIELD_DEBUG_JS is embedded VERBATIM (not a hand-rolled duplicate of
# its census logic) so this proves the real shipped bytes, the same as
# emit.py's own field_debug_tone_mutation_assertions does for the JS side of
# this. Its query-string gate means `census()` is the only thing reachable
# here (this page carries no `?debug=fields`); that is the point -- it is
# what T4-addendum needs to be true.
SELF_TEST_HTML = ("""<!doctype html>
<html><head><meta charset="utf-8"><style>
* { margin:0; padding:0; box-sizing:border-box }
.page { position:relative; background:#fff }
.c { position:absolute }
.fi { position:absolute; inset:0; border:0.5pt solid #999 }
</style></head><body>
<div class="page page-1" id="page-1" style="width:200pt;height:200pt">
  <svg class="rl" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 200"
       preserveAspectRatio="none"
       style="position:absolute;left:0;top:0;width:200pt;height:200pt">
    <rect x="129" y="129" width="1" height="22" fill="#000000"/>
    <rect x="150" y="129" width="1" height="22" fill="#000000"/>
    <rect x="129" y="129" width="22" height="1" fill="#000000"/>
    <rect x="129" y="150" width="22" height="1" fill="#000000"/>
  </svg>
  <div id="p1c1" class="c f" data-cell-kind="field" data-row="0" data-col="0"
       style="left:10pt;top:10pt;width:20pt;height:20pt"><input class="fi" id="p1c1-i"></div>
  <div id="p1c3" class="c f" data-cell-kind="field" data-row="1" data-col="0"
       style="left:10pt;top:60pt;width:20pt;height:20pt"><input class="fi" id="p1c3-i"></div>
  <div id="p1c2" class="c f" data-cell-kind="field" data-row="0" data-col="1"
       style="left:100pt;top:10pt;width:20pt;height:20pt"><input class="fi" id="p1c2-i"></div>
</div>
<script>
""" + emit.FIELD_DEBUG_JS + """
</script>
</body></html>
""")

# F214's own fixture: the DOM-first input is NOT the reading-order-first one.
# p2 (row 0, left 100, true rank 1) is placed BEFORE p1 (row 0, left 10, true
# rank 0) in the DOM, so a real Tab walk -- which starts at `document.
# querySelector("input.fi")`, DOM order -- reaches p2 first. Before F214's
# fix this graded p2 green (the sentinel `running_max=-1` accepts any first
# rank unconditionally) and p1 red-order, blaming the correctly-placed field
# for the misplaced one's own defect. The fix must reverse that: p2 (DOM
# first, not rank 0) is the actual defect and must be red-order; p1 (true
# rank 0, merely reached second because something jumped ahead of it) must
# be green.
SELF_TEST_HTML_DOM_FIRST = ("""<!doctype html>
<html><head><meta charset="utf-8"><style>
* { margin:0; padding:0; box-sizing:border-box }
.page { position:relative; background:#fff }
.c { position:absolute }
.fi { position:absolute; inset:0; border:0.5pt solid #999 }
</style></head><body>
<div class="page page-1" id="page-1" style="width:200pt;height:200pt">
  <div id="p2" class="c f" data-cell-kind="field" data-row="0" data-col="1"
       style="left:100pt;top:10pt;width:20pt;height:20pt"><input class="fi" id="p2-i"></div>
  <div id="p1" class="c f" data-cell-kind="field" data-row="0" data-col="0"
       style="left:10pt;top:10pt;width:20pt;height:20pt"><input class="fi" id="p1-i"></div>
</div>
<script>
""" + emit.FIELD_DEBUG_JS + """
</script>
</body></html>
""")

# F214's second gap: `terminated_by="cap"` is never exercised, though "never
# silently truncated" is a central claim of this module. A merely-unfocusable
# input (`tabindex="-1"`) does not do it -- Tab simply skips it and the walk
# still reaches `<body>` in a bounded number of presses, checked and proven
# by the DOM-first fixture above and by every real corpus form with a
# growable band's overflow markers. The one honest way `terminated_by="cap"`
# can happen is a genuine focus trap: a runtime that intercepts Tab and never
# releases it, so `document.activeElement` never becomes `<body>` at all. p1's
# own keydown listener is exactly that, and p2 (`tabindex="-1"`, so it could
# never be reached even if the trap were bypassed) is never focused as a
# result: this fixture proves BOTH that the cap saves the walk from hanging
# (`terminated_by == "cap"`, `presses == cap`) and that a field the trap
# prevented from ever being reached is reported red-skipped rather than
# silently dropped.
SELF_TEST_HTML_CAP_TRAP = ("""<!doctype html>
<html><head><meta charset="utf-8"><style>
* { margin:0; padding:0; box-sizing:border-box }
.page { position:relative; background:#fff }
.c { position:absolute }
.fi { position:absolute; inset:0; border:0.5pt solid #999 }
</style></head><body>
<div class="page page-1" id="page-1" style="width:200pt;height:200pt">
  <div id="p1" class="c f" data-cell-kind="field" data-row="0" data-col="0"
       style="left:10pt;top:10pt;width:20pt;height:20pt"><input class="fi" id="p1-i"></div>
  <div id="p2" class="c f" data-cell-kind="field" data-row="0" data-col="1"
       style="left:100pt;top:10pt;width:20pt;height:20pt">
    <input class="fi" id="p2-i" tabindex="-1"></div>
</div>
<script>
""" + emit.FIELD_DEBUG_JS + """
</script>
<script>
document.getElementById("p1-i").addEventListener("keydown", (ev) => {
  if (ev.key === "Tab") { ev.preventDefault(); }
});
</script>
</body></html>
""")


def _run_self_test_form(engine: Any, context: Any, slug: str, html: str,
                        review_dir: pathlib.Path) -> dict[str, Any]:
    """Write one synthetic fixture, walk it for real, return its report."""
    tmp_path = review_dir.parent
    bundle = tmp_path / f"bundle-{slug}"
    bundle.mkdir()
    (bundle / "index.html").write_text(html, encoding="utf-8")
    tab = context.new_page()
    report = walk_form(tab, slug, bundle, review_dir)
    tab.close()
    return report


def self_test() -> int:
    """Prove the ordering check, the vacant census, and both termination
    paths (`body`, `cap`) -- three synthetic pages, one browser context with
    the same `device_scale_factor=2` the corpus run in `main()` uses (F214:
    the self-test previously ran at the default DPR, proving nothing about
    the DPR-2 documents this module actually walks)."""
    from playwright.sync_api import sync_playwright

    failures = 0

    # emit.py writes each layer-cells run in this order. If it drifts,
    # tab_check grades the live DOM red-order (Stage 1 corpus: 21/53).
    shuffled = [
        {"id": "p1c9", "row": 2, "x0": 522.34},
        {"id": "p1c10", "row": 3, "x0": 20.0},
        {"id": "p1c8", "row": 2, "x0": 100.0},
    ]
    got = [cell["id"] for cell in emit.cells_in_tab_order(shuffled)]
    ok = got == ["p1c8", "p1c9", "p1c10"]
    failures += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  cells_in_tab_order is (row, left, id) "
          f"(got {got})", file=sys.stderr)

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = pathlib.Path(tmp)
        review_dir = tmp_path / "review"
        reports: list[dict[str, Any]] = []

        with sync_playwright() as engine:
            browser = engine.chromium.launch()
            context = browser.new_context(device_scale_factor=2)

            report = _run_self_test_form(engine, context, "selftest", SELF_TEST_HTML, review_dir)
            reports.append(report)

            verdicts = {entry["id"]: entry["verdict"] for entry in report["inputs"]}
            expected = {"p1c1-i": "green", "p1c3-i": "green", "p1c2-i": "red-order"}
            for input_id, want in expected.items():
                got = verdicts.get(input_id)
                ok = got == want
                failures += not ok
                print(f"  {'PASS' if ok else 'FAIL'}  {input_id} expected {want}, got {got}",
                      file=sys.stderr)

            ok = report["totals"]["red-skipped"] == 0
            failures += not ok
            print(f"  {'PASS' if ok else 'FAIL'}  nothing is red-skipped "
                  f"(got {report['totals']['red-skipped']})", file=sys.stderr)

            ok = not report["walk"]["cap_hit"]
            failures += not ok
            print(f"  {'PASS' if ok else 'FAIL'}  the walk did not hit the hard cap on 3 inputs",
                  file=sys.stderr)

            ok = report["walk"]["input_count"] == 3 and report["walk"]["presses"] == 3
            failures += not ok
            print(f"  {'PASS' if ok else 'FAIL'}  3 inputs, 3 presses to reach body "
                  f"(got input_count={report['walk']['input_count']}, presses={report['walk']['presses']})",
                  file=sys.stderr)

            image = review_dir / "selftest" / "page-1.png"
            ok = image.is_file() and image.stat().st_size > 0
            failures += not ok
            print(f"  {'PASS' if ok else 'FAIL'}  page-1.png artifact written to {image}", file=sys.stderr)

            tab_json = review_dir / "selftest" / "tab.json"
            ok = tab_json.is_file()
            failures += not ok
            print(f"  {'PASS' if ok else 'FAIL'}  tab.json written to {tab_json}", file=sys.stderr)

            # T4-addendum: the deliberate vacant box (SVG rect, x/y 130..150)
            # must come back as exactly one tone-aware "vacant" mark, distinct
            # from the three tabbed inputs, at the geometry the fixture prints.
            vacant = report.get("vacant") or []
            ok = len(vacant) == 1
            failures += not ok
            print(f"  {'PASS' if ok else 'FAIL'}  exactly one vacant box census'd "
                  f"(got {len(vacant)}: {vacant})", file=sys.stderr)
            if vacant:
                entry = vacant[0]
                geometry_ok = (entry["page"] == 1
                              and abs(entry["x_pt"] - 130) < 0.1
                              and abs(entry["y_pt"] - 130) < 0.1
                              and abs(entry["w_pt"] - 20) < 0.1
                              and abs(entry["h_pt"] - 20) < 0.1)
                failures += not geometry_ok
                print(f"  {'PASS' if geometry_ok else 'FAIL'}  the vacant box's "
                      f"geometry matches the printed fixture (got {entry})",
                      file=sys.stderr)
            ok = report["totals"].get("vacant") == 1
            failures += not ok
            print(f"  {'PASS' if ok else 'FAIL'}  totals.vacant is 1 "
                  f"(got {report['totals'].get('vacant')})", file=sys.stderr)
            ok = report["pages"] and report["pages"][0].get("vacant") == 1
            failures += not ok
            print(f"  {'PASS' if ok else 'FAIL'}  the page's own vacant count is 1 "
                  f"(got {report['pages'][0].get('vacant') if report['pages'] else None})",
                  file=sys.stderr)
            ok = tab_json.is_file() and json.loads(
                tab_json.read_text(encoding="utf-8")).get("vacant") == vacant
            failures += not ok
            print(f"  {'PASS' if ok else 'FAIL'}  tab.json on disk carries the "
                  f"same vacant list", file=sys.stderr)

            # F214: the DOM-first-is-misplaced fixture. The walk's own
            # sequence is [p2-i, p1-i] (DOM order); rank(p1-i)=0, rank(p2-i)=1.
            # p2-i started the walk despite not being rank 0 -- IT is the
            # defect and must be the one graded red. p1-i is reached second
            # but is in correct relative order on its own merits and must be
            # green, the inverse of what the unfixed code produced.
            dom_first_report = _run_self_test_form(
                engine, context, "selftest-domfirst", SELF_TEST_HTML_DOM_FIRST, review_dir)
            reports.append(dom_first_report)
            dom_first_verdicts = {entry["id"]: entry["verdict"]
                                  for entry in dom_first_report["inputs"]}
            expected_dom_first = {"p2-i": "red-order", "p1-i": "green"}
            for input_id, want in expected_dom_first.items():
                got = dom_first_verdicts.get(input_id)
                ok = got == want
                failures += not ok
                print(f"  {'PASS' if ok else 'FAIL'}  (F214, DOM-first misplaced) "
                      f"{input_id} expected {want}, got {got}", file=sys.stderr)
            ok = dom_first_report["totals"]["red-skipped"] == 0
            failures += not ok
            print(f"  {'PASS' if ok else 'FAIL'}  (F214, DOM-first misplaced) "
                  f"nothing is red-skipped (got {dom_first_report['totals']['red-skipped']})",
                  file=sys.stderr)
            ok = dom_first_report["walk"]["sequence"][:1] == ["p2-i"]
            failures += not ok
            print(f"  {'PASS' if ok else 'FAIL'}  (F214, DOM-first misplaced) "
                  f"the walk really did reach p2-i first "
                  f"(got {dom_first_report['walk']['sequence']})", file=sys.stderr)

            # F214: the cap-hit fixture. p1's own keydown listener traps Tab
            # forever, so `document.activeElement` never becomes <body> and
            # the walk must exhaust its hard cap rather than hang or silently
            # report success. p2 (never reached) must be red-skipped, not
            # dropped -- "never silently truncated" proven on a case that
            # actually truncates.
            cap_report = _run_self_test_form(
                engine, context, "selftest-captrap", SELF_TEST_HTML_CAP_TRAP, review_dir)
            reports.append(cap_report)
            ok = cap_report["walk"]["terminated_by"] == "cap"
            failures += not ok
            print(f"  {'PASS' if ok else 'FAIL'}  (F214, cap trap) "
                  f"terminated_by is \"cap\" (got {cap_report['walk']['terminated_by']!r})",
                  file=sys.stderr)
            ok = cap_report["walk"]["cap_hit"] is True
            failures += not ok
            print(f"  {'PASS' if ok else 'FAIL'}  (F214, cap trap) cap_hit is True "
                  f"(got {cap_report['walk']['cap_hit']!r})", file=sys.stderr)
            ok = cap_report["walk"]["presses"] == cap_report["walk"]["cap"]
            failures += not ok
            print(f"  {'PASS' if ok else 'FAIL'}  (F214, cap trap) the walk ran "
                  f"exactly to its own cap, not beyond and not short of it "
                  f"(presses={cap_report['walk']['presses']}, cap={cap_report['walk']['cap']})",
                  file=sys.stderr)
            cap_verdicts = {entry["id"]: entry["verdict"] for entry in cap_report["inputs"]}
            ok = cap_verdicts.get("p1-i") == "green" and cap_verdicts.get("p2-i") == "red-skipped"
            failures += not ok
            print(f"  {'PASS' if ok else 'FAIL'}  (F214, cap trap) the trapped field is "
                  f"green (reached), the never-reached one is red-skipped, not silently "
                  f"dropped (got {cap_verdicts})", file=sys.stderr)
            ok = cap_report["ok"] is False
            failures += not ok
            print(f"  {'PASS' if ok else 'FAIL'}  (F214, cap trap) a capped walk never "
                  f"reports ok (got {cap_report['ok']!r})", file=sys.stderr)

            write_index_html(review_dir, reports)
            browser.close()

        index_html = review_dir / "index.html"
        ok = index_html.is_file() and "red-order" in index_html.read_text(encoding="utf-8")
        failures += not ok
        print(f"  {'PASS' if ok else 'FAIL'}  forms/review/index.html generated", file=sys.stderr)
        ok = index_html.is_file() and "vacant (blue) 1" in index_html.read_text(encoding="utf-8")
        failures += not ok
        print(f"  {'PASS' if ok else 'FAIL'}  the index page reports the vacant count too",
              file=sys.stderr)

    print(f"self-test: {failures} failure(s)", file=sys.stderr)
    return 1 if failures else 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("slug", nargs="?", default=None,
                        help="Walk one form and stop; omit to walk the whole corpus.")
    parser.add_argument("--forms", type=pathlib.Path, default=REPO / "forms")
    parser.add_argument("--layout-dir", type=pathlib.Path, default=REPO / "build/layout")
    parser.add_argument("--review-dir", type=pathlib.Path, default=REPO / "forms/review")
    parser.add_argument("--only", action="append", default=None)
    parser.add_argument("--json", type=pathlib.Path, default=None,
                        help="Write the combined per-form reports here too.")
    parser.add_argument("--self-test", action="store_true",
                        help="Prove the ordering check on a synthetic page, then stop.")
    args = parser.parse_args(argv)

    if args.self_test:
        return self_test()

    only = list(args.only or [])
    if args.slug:
        only.append(args.slug)

    slugs = discover_slugs(args.forms, args.layout_dir)
    if only:
        wanted = set(only)
        missing = wanted - set(slugs)
        for slug in sorted(missing):
            slugs.append(slug)  # let bundle_dir() report "no generated bundle" for a bad slug
        slugs = [s for s in slugs if s in wanted]

    print(f"{len(slugs)} form(s) to walk", file=sys.stderr)

    from playwright.sync_api import sync_playwright

    reports: list[dict[str, Any]] = []
    t_start = time.monotonic()
    with sync_playwright() as engine:
        browser = engine.chromium.launch()
        context = browser.new_context(device_scale_factor=2)
        for slug in slugs:
            bundle = band_drive.bundle_dir(args.forms, slug)
            if bundle is None:
                reports.append({
                    "schema_version": SCHEMA_VERSION, "slug": slug, "limitation": LIMITATION,
                    "ok": False, "failures": ["no generated bundle"],
                    "totals": {"green": 0, "red-skipped": 0, "red-order": 0, "total": 0, "vacant": 0},
                })
                print(f"  {slug:<26} NO BUNDLE", file=sys.stderr)
                continue
            tab = context.new_page()
            t0 = time.monotonic()
            report = walk_form(tab, slug, bundle, args.review_dir)
            report["duration_s"] = round(time.monotonic() - t0, 2)
            tab.close()
            reports.append(report)
            totals = report.get("totals", {})
            mark = "PASS" if report.get("ok") else "FAIL"
            print(f"  {slug:<26} {mark}  green={totals.get('green', 0):<5} "
                  f"red-skipped={totals.get('red-skipped', 0):<4} "
                  f"red-order={totals.get('red-order', 0):<4} ({report['duration_s']}s)",
                  file=sys.stderr)
        browser.close()
    total_s = round(time.monotonic() - t_start, 1)

    write_index_html(args.review_dir, reports)

    red = [r for r in reports if not r.get("ok")]
    print(f"\n{len(reports) - len(red)}/{len(reports)} forms fully green in {total_s}s",
          file=sys.stderr)
    if red:
        print(f"forms with red: {', '.join(sorted(r['slug'] for r in red))}", file=sys.stderr)
    print(LIMITATION, file=sys.stderr)
    print(f"artifacts: {args.review_dir}", file=sys.stderr)

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(reports, indent=2) + "\n", encoding="utf-8")

    return 0 if not red else 1


if __name__ == "__main__":
    raise SystemExit(main())
