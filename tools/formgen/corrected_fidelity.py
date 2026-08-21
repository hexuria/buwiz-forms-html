#!/usr/bin/env python3
"""Publish the divergences a stage-2 correction deliberately LEAVES IN PLACE.

ARCHITECTURE.md rule 1: "A correction never hides a divergence. Fidelity checks
still compare against the official PDF and still FAIL on a corrected field --
the report says `diverges by declared override <id>, authorised by
<authority>`." `gate.check_corrected_tree` enforces the half of that rule it
can see from outside: every sentence `correct.build_manifest` generated must
appear in `build/corrected-fidelity.json`. This script is what writes that
file, and the ONLY thing it is allowed to do is refuse or publish.

    python3 tools/formgen/corrected_fidelity.py \
        --tree forms-corrected \
        --manifest forms-corrected.manifest.json \
        --records tools/formgen/corrections \
        --out build/corrected-fidelity.json

What this is NOT
    It is not a copy of the manifest with the honesty flags flipped on. That
    shape is the `?debug=fields` defect this project already shipped once: an
    overlay that compared inputs against their own geometry and reported
    233/233 OK on a page a human could see was wrong. Copying the manifest's
    `divergences[].report` into a report file would produce a green
    `corrected-tree` check for a corrected tree in which NOTHING diverges --
    the exact silent override rule 1 exists to forbid, arrived at from the
    opposite direction.

    So a sentence is emitted only after the divergence it describes has been
    SEEN, in this run, in two independent measurements that share no source:

      1. `comb_slots_match_printed` -- the corrected document's compartment
         count, counted from its own element structure, differs from the count
         the pinned artwork prints. If they are EQUAL the run refuses: a
         correction that changed nothing visible has no divergence to declare
         and must not be reported as though it had one.
      2. `inputs_span_no_printed_divider` -- at least one printed compartment
         tick falls strictly inside an emitted slot's page-space x range. If no
         tick does, the offender the record predicted was not observed and the
         run refuses.

    Both are stated in C01's `diverges_from` as the checks that MUST gain
    offenders. Neither is weakened, allowlisted or waived anywhere here; this
    file cannot suppress a finding because it has no suppression path to
    express one in.

Independence (C01-evidence.json `verified_by.forbidden_sources`)
    * The emitted count comes from the corrected HTML parsed by the stdlib
      `html.parser`. `emit.py` is not imported and none of its helpers run. The
      count is the number of slot containers that actually carry an input, NOT
      the value of `data-comb-slots`; the attribute is read only so that a
      document contradicting itself is a refusal rather than a number.
    * The printed count comes from the pinned PDF's drawing operators via
      PyMuPDF. `extract.py` is not imported. `build/layout/*.json`, `build/ir/`
      and the applier's manifest are never consulted for it. PyMuPDF is also
      extract.py's library, so it is not a fully independent READER: the two
      readings this rests on were re-derived while the record was authored by
      `mutool draw -F trace` and by Poppler `pdftocairo -svg`, agreeing to
      0.01pt (C01-evidence.json, evidence[1].independence). Those two are
      recorded in the report as `authoring_readers` and are not re-run here --
      naming them is a provenance statement, not a claim this run made them.
    * The manifest is read for WHICH records were applied and for the exact
      sentence each declares. Every number it contains is re-derived; the
      sentence itself is regenerated from the ledger record on disk and the
      manifest's copy is refused if the two differ.

Nothing here promotes anything. The report carries `proves_parity: false` and
`is_non_regression_gate: true`, and says in its own text that a corrected tree
diverges from the official artwork BY DESIGN.
"""

from __future__ import annotations

import argparse
import hashlib
import html.parser
import json
import os
import pathlib
import re
import sys
from typing import Any, Iterable, Sequence

TOOL = "tools/formgen/corrected_fidelity.py"
REPORT_VERSION = 1

# The evidence record's `subject.match.tolerance_pt`. Used to decide that an
# emitted element IS the printed box, and that a full-height mark IS a wall.
MATCH_TOLERANCE_PT = 0.25

# How far inside a slot a printed tick must fall before the slot counts as
# spanning it. This is a STRICTNESS margin, not a tolerance: raising it makes
# the offender harder to observe and the run more likely to refuse. It exists
# because the UNCORRECTED emission puts slot edges exactly on the printed
# ticks (0/11.04/21.84pt against ticks at 191.28/202.08), and an edge is not a
# span. Lowering it would let that arrangement report an offender it does not
# have, so it never moves down.
INSIDE_MARGIN_PT = 0.25

# A mark this tall relative to the box is a group WALL, not a compartment tick
# (measure_tin_branch_census.py uses the same split, independently derived).
WALL_HEIGHT_FRACTION = 0.85

VOID_TAGS = frozenset({
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link",
    "meta", "param", "source", "track", "wbr",
})


class Refusal(Exception):
    """A stated reason to write nothing. Never downgraded to a warning."""

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(f"{kind}: {message}")
        self.kind = kind
        self.message = message


# --------------------------------------------------------------------------
# The corrected document, read as bytes by a parser that knows nothing of emit.py
# --------------------------------------------------------------------------


class Element:
    __slots__ = ("tag", "attrs", "children")

    def __init__(self, tag: str, attrs: dict[str, str]) -> None:
        self.tag = tag
        self.attrs = attrs
        self.children: list[Element] = []


class DocumentParser(html.parser.HTMLParser):
    """A minimal element tree. No markers, no classes, no conventions."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Element("#document", {})
        self._stack: list[Element] = [self.root]

    def _open(self, tag: str, attrs: list[tuple[str, str | None]]) -> Element:
        element = Element(tag, {key: (value or "") for key, value in attrs})
        self._stack[-1].children.append(element)
        return element

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        element = self._open(tag, attrs)
        if tag not in VOID_TAGS:
            self._stack.append(element)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._open(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        for index in range(len(self._stack) - 1, 0, -1):
            if self._stack[index].tag == tag:
                del self._stack[index:]
                return


def parse_document(text: str) -> Element:
    parser = DocumentParser()
    parser.feed(text)
    parser.close()
    return parser.root


def style_length(style: str, prop: str) -> float | None:
    match = re.search(
        rf"(?:^|;)\s*{prop}\s*:\s*(-?\d+(?:\.\d+)?)\s*pt\s*(?:;|$)", style)
    return float(match.group(1)) if match else None


def element_box(element: Element) -> tuple[float, float, float, float] | None:
    """(left, top, width, height) in pt, or None if the element is not placed."""
    style = element.attrs.get("style", "")
    if not style:
        return None
    values = [style_length(style, prop)
              for prop in ("left", "top", "width", "height")]
    if any(value is None for value in values):
        return None
    return (values[0], values[1], values[2], values[3])  # type: ignore[return-value]


def walk(element: Element) -> Iterable[Element]:
    for child in element.children:
        yield child
        yield from walk(child)


def count_inputs(element: Element) -> int:
    return sum(1 for node in walk(element) if node.tag == "input")


class EmittedComb:
    __slots__ = ("element_id", "box", "declared_slots", "slots")

    def __init__(self, element_id: str, box: tuple[float, float, float, float],
                 declared_slots: int | None,
                 slots: list[tuple[float, float]]) -> None:
        self.element_id = element_id
        self.box = box
        self.declared_slots = declared_slots
        self.slots = slots


def emitted_comb_from_element(element: Element, where: str) -> EmittedComb:
    """Read compartments from one placed element's own children.

    The count is the structure (one input per placed child), never the
    `data-comb-slots` attribute. The attribute is only read so a document
    that contradicts itself is a refusal.
    """
    box = element_box(element)
    if box is None:
        raise Refusal(
            "subject-has-no-compartments",
            f"{where}: the subject element is not placed, so nothing was "
            f"emitted to compare")

    slots: list[tuple[float, float]] = []
    for child in element.children:
        child_box = element_box(child)
        if child_box is None:
            continue
        inputs = count_inputs(child)
        if inputs != 1:
            raise Refusal(
                "slot-input-count",
                f"{where}: a compartment of the subject element holds "
                f"{inputs} input(s); a compartment a taxpayer can type one "
                f"character into holds exactly one")
        slots.append((child_box[0], child_box[2]))
    if not slots:
        raise Refusal(
            "subject-has-no-compartments",
            f"{where}: the subject element holds no placed compartment "
            f"carrying an input, so nothing was emitted to compare")

    declared_raw = element.attrs.get("data-comb-slots")
    declared: int | None = None
    if declared_raw is not None:
        try:
            declared = int(declared_raw)
        except ValueError as error:
            raise Refusal(
                "declared-slots-unreadable",
                f"{where}: data-comb-slots is {declared_raw!r}") from error
        if declared != len(slots):
            raise Refusal(
                "declared-slots-disagree-with-structure",
                f"{where}: the subject element declares {declared} "
                f"compartment(s) and contains {len(slots)}")
    return EmittedComb(element.attrs.get("id", ""), box, declared, slots)


def find_printed_box_element(root: Element,
                             printed_box: tuple[float, float, float, float],
                             where: str,
                             cell_id: str | None = None) -> EmittedComb:
    """The comb this record rewrote.

    Identity is the printed box when the emitted element still sits on it.
    A chain reflow moves the branch comb (same outer TIN strip, even 3-3-3-5
    cells) so the official box and the emitted box are no longer the same
    rectangle. Then, and only then, the cell id hint is used to find the
    moved comb. The PDF is still measured at the original printed box; the
    id is never how the artwork is identified.
    """
    x0, y0, x1, y1 = printed_box
    want = (x0, y0, x1 - x0, y1 - y0)
    matches = [element for element in walk(root)
               if (box := element_box(element)) is not None
               and all(abs(box[i] - want[i]) <= MATCH_TOLERANCE_PT
                       for i in range(4))]
    if len(matches) == 1:
        return emitted_comb_from_element(matches[0], where)
    if len(matches) > 1:
        raise Refusal(
            "subject-cardinality",
            f"{where}: {len(matches)} element(s) carry the printed box "
            f"x {x0}-{x1}, y {y0}-{y1} pt within {MATCH_TOLERANCE_PT}pt; "
            f"exactly one is required")
    if not cell_id:
        raise Refusal(
            "subject-cardinality",
            f"{where}: {len(matches)} element(s) carry the printed box "
            f"x {x0}-{x1}, y {y0}-{y1} pt within {MATCH_TOLERANCE_PT}pt; "
            f"exactly one is required")
    by_id = [element for element in walk(root)
             if element.attrs.get("id") == cell_id]
    if len(by_id) != 1:
        raise Refusal(
            "subject-cardinality",
            f"{where}: the printed box x {x0}-{x1}, y {y0}-{y1} pt matches "
            f"no element (the comb was reflowed) and cell id {cell_id!r} "
            f"matches {len(by_id)}; exactly one is required")
    return emitted_comb_from_element(by_id[0], where)


def slot_page_ranges(comb: EmittedComb) -> list[tuple[float, float]]:
    """Each compartment's x range in PAGE space, so a printed tick can be
    tested against it. Slot offsets are relative to the comb's own box."""
    left = comb.box[0]
    return [(round(left + offset, 4), round(left + offset + width, 4))
            for offset, width in comb.slots]


# --------------------------------------------------------------------------
# The pinned artwork, read by drawing operators only
# --------------------------------------------------------------------------


def _import_fitz() -> Any:
    try:
        import fitz  # PyMuPDF
    except ImportError as error:      # pragma: no cover - environment probe
        raise Refusal(
            "pymupdf-missing",
            "PyMuPDF is required to re-derive the printed compartment count; "
            "an unmeasurable artwork is not a measured one") from error
    return fitz


def vertical_marks(page: Any, x_lo: float, x_hi: float,
                   y_lo: float, y_hi: float) -> list[dict[str, float]]:
    """Vertical ink inside a window, in the three encodings this corpus uses.

    Stroked segments (`l`), the two sides of a stroked rectangle, and thin
    filled rectangles. Bezier ink -- which is how BIR pre-prints the '000' in
    2550M's branch box -- is not vertical ink and is not collected, so the
    pre-printed digits cannot be mistaken for compartment dividers.
    """
    raw: set[tuple[float, float, float]] = set()
    for drawing in page.get_drawings():
        stroked = "s" in drawing["type"]
        for item in drawing["items"]:
            candidates: list[tuple[float, float, float]] = []
            if item[0] == "l":
                start, end = item[1], item[2]
                if abs(start.x - end.x) <= 0.06:
                    candidates = [(start.x, min(start.y, end.y),
                                   max(start.y, end.y))]
            elif item[0] == "re":
                rect = item[1]
                width, height = rect.x1 - rect.x0, rect.y1 - rect.y0
                if height < 2.0:
                    continue
                if width <= 1.6 and height > width:
                    candidates = [((rect.x0 + rect.x1) / 2.0, rect.y0, rect.y1)]
                elif stroked and height <= 40.0:
                    candidates = [(rect.x0, rect.y0, rect.y1),
                                  (rect.x1, rect.y0, rect.y1)]
            for x, top, bottom in candidates:
                if bottom - top < 1.0:
                    continue
                if not (x_lo <= x <= x_hi):
                    continue
                if bottom < y_lo or top > y_hi:
                    continue
                raw.add((round(x, 2), round(top, 2), round(bottom, 2)))

    merged: list[dict[str, float]] = []
    for x, top, bottom in sorted(raw):
        if merged and x - merged[-1]["x"] <= 0.6:
            merged[-1]["y0"] = min(merged[-1]["y0"], top)
            merged[-1]["y1"] = max(merged[-1]["y1"], bottom)
            continue
        merged.append({"x": x, "y0": top, "y1": bottom})
    return merged


def measure_printed_box(page: Any, printed_box: tuple[float, float, float, float],
                        where: str) -> dict[str, Any]:
    """Printed compartments = internal ticks + 1, from the paint stream alone.

    The two group WALLS are required to be present. That is not decoration: it
    proves the box the record names is really printed on the page the record
    names, so a mis-parsed subject fails here instead of quietly measuring
    blank paper and reporting one compartment.
    """
    x0, y0, x1, y1 = printed_box
    height = y1 - y0
    marks = vertical_marks(page, x0 - 1.0, x1 + 1.0, y0 - 1.0, y1 + 1.0)
    tall = [mark for mark in marks
            if (mark["y1"] - mark["y0"]) >= WALL_HEIGHT_FRACTION * height]
    left_wall = [mark for mark in tall if abs(mark["x"] - x0) <= MATCH_TOLERANCE_PT]
    right_wall = [mark for mark in tall if abs(mark["x"] - x1) <= MATCH_TOLERANCE_PT]
    if not left_wall or not right_wall:
        raise Refusal(
            "printed-box-not-found",
            f"{where}: the pinned artwork does not print both walls of the box "
            f"x {x0}-{x1}, y {y0}-{y1} pt "
            f"(left {len(left_wall)}, right {len(right_wall)} found)")
    ticks = [mark for mark in marks
             if x0 + 0.8 < mark["x"] < x1 - 0.8
             and (mark["y1"] - mark["y0"]) < WALL_HEIGHT_FRACTION * height]
    return {
        "walls_x_pt": [round(left_wall[0]["x"], 2), round(right_wall[0]["x"], 2)],
        "internal_ticks_x_pt": [round(mark["x"], 2) for mark in ticks],
        "tick_extents_pt": [[round(mark["y0"], 2), round(mark["y1"], 2)]
                            for mark in ticks],
        "compartments": len(ticks) + 1,
    }


# --------------------------------------------------------------------------
# The observer. Pure, so the self-test drives every refusal without a file.
# --------------------------------------------------------------------------


def observe_divergence(*, record_id: str, cell_id_hint: str,
                       emitted_slot_ranges: Sequence[tuple[float, float]],
                       printed_ticks_x: Sequence[float],
                       printed_compartments: int) -> dict[str, Any]:
    """Both offenders, or a refusal. There is no third outcome.

    This is the whole point of the file: the divergence sentence is a
    consequence of these two observations and cannot be reached without them.
    """
    emitted = len(emitted_slot_ranges)
    if emitted == printed_compartments:
        raise Refusal(
            "no-observed-divergence",
            f"{record_id}: the corrected document emits {emitted} compartment(s) "
            f"and the pinned artwork prints {printed_compartments}. A "
            f"correction that leaves nothing visibly divergent has no "
            f"divergence to declare, and declaring one anyway would be this "
            f"report inventing its own finding")

    spanned: list[dict[str, Any]] = []
    for tick in printed_ticks_x:
        for index, (low, high) in enumerate(emitted_slot_ranges):
            if low + INSIDE_MARGIN_PT < tick < high - INSIDE_MARGIN_PT:
                spanned.append({
                    "printed_tick_x_pt": round(tick, 2),
                    "inside_emitted_slot_index": index,
                    "emitted_slot_x_range_pt": [low, high],
                })
                break
    if not spanned:
        raise Refusal(
            "printed-divider-not-spanned",
            f"{record_id}: no printed compartment tick "
            f"({', '.join(f'{t:.2f}' for t in printed_ticks_x) or 'none printed'}) "
            f"falls at least {INSIDE_MARGIN_PT}pt inside any of the "
            f"{emitted} emitted compartment(s). The record predicts an "
            f"inputs_span_no_printed_divider offender here; it was not "
            f"observed, so nothing may be published as though it were")

    return {
        "comb_slots_match_printed": {
            "assertion": "Every comb's slot count equals its printed compartment count",
            "offender_observed": True,
            "offender": {
                "cell_id_hint": cell_id_hint,
                "emitted_slots": emitted,
                "printed_compartments": printed_compartments,
            },
            "detail": (f"the corrected document emits {emitted} compartment(s) "
                       f"where the pinned artwork prints "
                       f"{printed_compartments}"),
        },
        "inputs_span_no_printed_divider": {
            "assertion": ("No <input> spans a compartment divider the source "
                          "printed inside it"),
            "offender_observed": True,
            "offender": {
                "cell_id_hint": cell_id_hint,
                "spanned_dividers": spanned,
            },
            "detail": "; ".join(
                f"printed divider x {item['printed_tick_x_pt']} lies inside "
                f"emitted compartment {item['inside_emitted_slot_index']} "
                f"({item['emitted_slot_x_range_pt'][0]}-"
                f"{item['emitted_slot_x_range_pt'][1]}pt)"
                for item in spanned),
        },
    }


# --------------------------------------------------------------------------
# Ledger, manifest and pinned-PDF plumbing
# --------------------------------------------------------------------------


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_path(path: pathlib.Path) -> str:
    return sha256_bytes(path.read_bytes())


def load_ledger(records_dir: pathlib.Path) -> dict[str, dict[str, Any]]:
    """Root-level `*.json` only -- correct.py's rule, restated, not imported.

    `evidence/` and `schema/` deliberately sit below the root where the
    applier's loader never looks, so recursing here would read backing
    documents as though they were records.
    """
    if not records_dir.is_dir():
        raise Refusal("records-dir-missing",
                      f"{records_dir} is not a directory; an absent ledger is "
                      f"not an empty one")
    records: dict[str, dict[str, Any]] = {}
    for path in sorted(records_dir.iterdir()):
        if not path.is_file() or path.suffix != ".json" or path.name.startswith("."):
            continue
        parsed = json.loads(path.read_bytes().decode("utf-8"))
        for raw in (parsed if isinstance(parsed, list) else [parsed]):
            if not isinstance(raw, dict) or "id" not in raw:
                continue
            records[str(raw["id"])] = {
                "record": raw,
                "path": path,
                "sha256": sha256_path(path),
            }
    return records


_BOX_RE = re.compile(
    r"printed box\s+x\s*(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)\s*,\s*"
    r"y\s*(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)\s*pt", re.IGNORECASE)
_PAGE_RE = re.compile(r"\bpage\s+(\d+)\b", re.IGNORECASE)
_SHA_RE = re.compile(r"sha256\s+([0-9a-f]{64})", re.IGNORECASE)


def subject_facts(record: dict[str, Any], where: str) -> dict[str, Any]:
    """The printed box, page and artwork digest, parsed out of the subject.

    The subject is the record's own statement of WHAT it corrects, written
    before any applier existed. Parsing it rather than accepting a
    machine-friendly field is deliberate: the prose a reviewer read is the
    thing this run must be bound to.
    """
    subject = str(record.get("subject", ""))
    box_match = _BOX_RE.search(subject)
    page_match = _PAGE_RE.search(subject)
    sha_match = _SHA_RE.search(subject)
    if not box_match:
        raise Refusal(
            "subject-has-no-printed-box",
            f"{where}: the record's subject does not state a printed box in "
            f"the form 'printed box x <x0>-<x1>, y <y0>-<y1> pt', so this "
            f"producer cannot bind its measurement to what was reviewed")
    if not page_match:
        raise Refusal("subject-has-no-page",
                      f"{where}: the record's subject does not state a page")
    return {
        "printed_box_pt": tuple(round(float(box_match.group(i)), 2)
                                for i in (1, 3, 2, 4)),
        "page": int(page_match.group(1)),
        "pdf_sha256": sha_match.group(1).lower() if sha_match else None,
    }


def evidence_facts(records_dir: pathlib.Path, record_id: str) -> dict[str, Any]:
    path = records_dir / "evidence" / f"{record_id}-evidence.json"
    if not path.is_file():
        return {}
    parsed = json.loads(path.read_bytes().decode("utf-8"))
    subject = parsed.get("subject") or {}
    form = parsed.get("form") or {}
    out: dict[str, Any] = {"evidence_file": path.name}
    box = subject.get("printed_box_pt")
    if isinstance(box, list) and len(box) == 4:
        out["printed_box_pt"] = tuple(round(float(v), 2) for v in box)
    if isinstance(subject.get("page"), int):
        out["page"] = subject["page"]
    if isinstance(form.get("source_pdf_sha256"), str):
        out["pdf_sha256"] = form["source_pdf_sha256"].lower()
    hint = subject.get("cell_id_hint")
    if isinstance(hint, str):
        match = re.search(r"\b(p\d+c\d+)\b", hint)
        if match:
            out["cell_id"] = match.group(1)
    return out


def provenance_sha256(repo: pathlib.Path, slug: str) -> str | None:
    path = repo / "forms" / slug / "provenance.json"
    if not path.is_file():
        return None
    parsed = json.loads(path.read_bytes().decode("utf-8"))
    digest = parsed.get("sha256")
    return str(digest).lower() if isinstance(digest, str) else None


def agree(where: str, field: str, values: dict[str, Any]) -> Any:
    """One value from several sources, or a refusal that names the disagreement."""
    stated = {source: value for source, value in values.items() if value is not None}
    if not stated:
        raise Refusal(f"{field}-unstated",
                      f"{where}: no source states {field}")
    distinct = {json.dumps(value, sort_keys=True) for value in stated.values()}
    if len(distinct) != 1:
        raise Refusal(
            f"{field}-disagreement",
            f"{where}: sources disagree on {field}: " +
            ", ".join(f"{source}={value!r}" for source, value in sorted(stated.items())))
    return next(iter(stated.values()))


def locate_pdf(forms_root: pathlib.Path, digest: str, where: str) -> pathlib.Path:
    """Find the pinned artwork by content, read-only. Nothing is written here."""
    if not forms_root.is_dir():
        raise Refusal("forms-root-missing",
                      f"{where}: {forms_root} is not a directory, so the pinned "
                      f"artwork cannot be located")
    for base, _dirs, files in os.walk(forms_root):
        for name in sorted(files):
            if not name.lower().endswith(".pdf"):
                continue
            path = pathlib.Path(base) / name
            if sha256_path(path) == digest:
                return path
    raise Refusal("pinned-pdf-not-found",
                  f"{where}: no PDF under {forms_root} has sha256 {digest}")


def expected_sentence(record: dict[str, Any]) -> str:
    """`correct.build_manifest`'s sentence, regenerated from the record itself."""
    return (f"diverges by declared override {record['id']}, "
            f"authorised by {record['authority']}")


# --------------------------------------------------------------------------
# The run
# --------------------------------------------------------------------------


AUTHORING_READERS = {
    "statement": ("The printed geometry this report re-derives with PyMuPDF "
                  "drawing operators was re-derived independently while the "
                  "record was authored by two readers that share no code with "
                  "this pipeline; all three agree to 0.01pt."),
    "readers": [
        "mutool draw -F trace (MuPDF) -- stroke_path ops inside the printed box",
        "pdftocairo -svg (Poppler) -- same ticks, plus the pre-printed vector ink",
    ],
    "re_run_in_this_report": False,
    "why_named": ("PyMuPDF is also extract.py's library, so it is not a fully "
                  "independent reader. Naming the two that are is a provenance "
                  "statement about the record, not a claim this run made them."),
    "source": "tools/formgen/corrections/evidence/C01-evidence.json evidence[1].independence",
}


def measure_record(*, entry: dict[str, Any], ledger: dict[str, dict[str, Any]],
                   records_dir: pathlib.Path, tree: pathlib.Path,
                   repo: pathlib.Path, forms_root: pathlib.Path,
                   manifest_files: dict[str, str]) -> dict[str, Any]:
    record_id = str(entry.get("record_id"))
    where = f"record {record_id}"
    held = ledger.get(record_id)
    if held is None:
        raise Refusal("record-not-in-ledger",
                      f"{where}: the manifest declares a divergence for a "
                      f"record that is not in {records_dir}")
    record = held["record"]

    sentence = expected_sentence(record)
    if str(entry.get("report")) != sentence:
        raise Refusal(
            "sentence-not-re-derivable",
            f"{where}: the manifest's divergence sentence is not the one the "
            f"ledger record generates; the report would be republishing an "
            f"authority nobody wrote")

    slug = str(record["form"])
    facts = subject_facts(record, where)
    backing = evidence_facts(records_dir, record_id)
    printed_box = agree(where, "printed_box_pt", {
        "record.subject": facts["printed_box_pt"],
        "evidence": backing.get("printed_box_pt"),
    })
    page_number = agree(where, "page", {
        "record.subject": facts["page"],
        "evidence": backing.get("page"),
    })
    digest = agree(where, "pdf_sha256", {
        "record.subject": facts["pdf_sha256"],
        "evidence": backing.get("pdf_sha256"),
        "forms/provenance.json": provenance_sha256(repo, slug),
    })

    edits = record.get("edits") or []
    targets = sorted({str(edit["file"]) for edit in edits})
    if len(targets) != 1:
        raise Refusal(
            "record-edits-many-files",
            f"{where}: this producer measures one corrected document per "
            f"record and the record edits {len(targets)}")
    relpath = targets[0] if slug == "." else f"{slug}/{targets[0]}"
    document_path = tree / relpath
    if not document_path.is_file():
        raise Refusal("corrected-document-missing",
                      f"{where}: {relpath} is not in the corrected tree")
    document_sha = sha256_path(document_path)
    declared_sha = manifest_files.get(relpath)
    if declared_sha is not None and declared_sha != document_sha:
        raise Refusal(
            "corrected-document-not-the-manifest's",
            f"{where}: {relpath} on disk is sha256 {document_sha} and the "
            f"manifest binds {declared_sha}; the bytes measured here would "
            f"not be the bytes the manifest describes")

    root = parse_document(document_path.read_text(encoding="utf-8"))
    comb = find_printed_box_element(
        root, printed_box, where, cell_id=backing.get("cell_id"))
    ranges = slot_page_ranges(comb)

    fitz = _import_fitz()
    pdf_path = locate_pdf(forms_root, digest, where)
    document = fitz.open(pdf_path)
    try:
        if not 1 <= page_number <= document.page_count:
            raise Refusal("page-out-of-range",
                          f"{where}: the record names page {page_number} of a "
                          f"{document.page_count}-page artwork")
        printed = measure_printed_box(document[page_number - 1], printed_box, where)
    finally:
        document.close()

    observed = observe_divergence(
        record_id=record_id,
        cell_id_hint=comb.element_id,
        emitted_slot_ranges=ranges,
        printed_ticks_x=printed["internal_ticks_x_pt"],
        printed_compartments=printed["compartments"])

    return {
        "record_id": record_id,
        "form": slug,
        "record_file": held["path"].name,
        "record_sha256": held["sha256"],
        "corrected_file": relpath,
        "corrected_file_sha256": document_sha,
        "pinned_pdf": pdf_path.name,
        "pinned_pdf_sha256": digest,
        "page": page_number,
        "printed_box_pt": list(printed_box),
        "printed_walls_x_pt": printed["walls_x_pt"],
        "printed_ticks_x_pt": printed["internal_ticks_x_pt"],
        "printed_compartments": printed["compartments"],
        "emitted_slots": len(ranges),
        "emitted_slot_x_ranges_pt": [list(item) for item in ranges],
        "cell_id_hint": comb.element_id,
        "still_diverges_from_official": True,
        "checks_reporting_the_divergence": observed,
        "authority": str(record["authority"]),
        "report": sentence,
    }


def build_report(*, manifest: dict[str, Any], manifest_sha256: str,
                 tree_label: str, manifest_label: str, records_label: str,
                 measured: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "report_version": REPORT_VERSION,
        "tool": TOOL,
        # The honesty flags gate.py and the audit demand. None is a claim this
        # producer cannot support, and the first two are the whole disposition
        # of the file: it certifies that a declared divergence is STILL THERE.
        "proves_parity": False,
        "is_non_regression_gate": True,
        "measures_official_parity": False,
        "statement": ("Each record below was applied on purpose and the "
                      "corrected tree diverges from the official artwork "
                      "BY DESIGN. Every divergence named here was OBSERVED in "
                      "this run against the pinned PDF; none is waived, "
                      "allowlisted or excused, and no tolerance was moved to "
                      "reach it."),
        "sentences_are_published_only_after_observation": True,
        "reads_emit_py": False,
        "reads_build_layout_or_ir": False,
        "reads_applier_manifest_for_measurements": False,
        "html_reader": "python stdlib html.parser",
        "pdf_reader": "PyMuPDF drawing operators (page.get_drawings)",
        "authoring_readers": AUTHORING_READERS,
        "printed_compartment_rule": "internal ticks inside the printed box + 1",
        "match_tolerance_pt": MATCH_TOLERANCE_PT,
        "divider_inside_margin_pt": INSIDE_MARGIN_PT,
        "tree": tree_label,
        "manifest": manifest_label,
        "manifest_sha256": manifest_sha256,
        "records_dir": records_label,
        "source_batch": manifest.get("source_batch"),
        "source_commit": manifest.get("source_commit"),
        "divergence_count": len(measured),
        "records": measured,
        # Flat, so the substring search gate.fidelity_report_text performs has
        # the exact sentence to find whatever it does with the nesting above.
        "divergence_reports": [item["report"] for item in measured],
    }


def produce(*, tree: pathlib.Path, manifest_path: pathlib.Path,
            records_dir: pathlib.Path, repo: pathlib.Path,
            forms_root: pathlib.Path) -> dict[str, Any]:
    if not tree.is_dir():
        raise Refusal("tree-missing",
                      f"{tree} is not a directory; there is no corrected tree "
                      f"to measure")
    if not manifest_path.is_file():
        raise Refusal("manifest-missing", f"{manifest_path} is not a file")
    manifest_raw = manifest_path.read_bytes()
    manifest = json.loads(manifest_raw.decode("utf-8"))
    divergences = manifest.get("divergences")
    if not isinstance(divergences, list) or not divergences:
        raise Refusal(
            "manifest-declares-no-divergence",
            f"{manifest_path.name} declares no divergence, so there is nothing "
            f"for this report to publish and writing an empty one would give "
            f"the gate a file to be satisfied by")
    ledger = load_ledger(records_dir)
    manifest_files = {str(entry.get("path")): str(entry.get("output_sha256"))
                      for entry in (manifest.get("files") or [])
                      if isinstance(entry, dict)}

    measured = [measure_record(entry=entry, ledger=ledger, records_dir=records_dir,
                               tree=tree, repo=repo, forms_root=forms_root,
                               manifest_files=manifest_files)
                for entry in divergences]
    # Labels, never absolute paths: an operator's home directory in a report is
    # a difference between two runs of the same inputs.
    try:
        records_label = records_dir.relative_to(repo).as_posix()
    except ValueError:
        records_label = records_dir.name
    return build_report(
        manifest=manifest, manifest_sha256=sha256_bytes(manifest_raw),
        tree_label=tree.name, manifest_label=manifest_path.name,
        records_label=records_label, measured=measured)


# --------------------------------------------------------------------------
# Self-test
# --------------------------------------------------------------------------


def _fixture_html(slots: Sequence[tuple[float, float]], *,
                  declared: int | None = None,
                  left: float = 180.24, top: float = 118.8,
                  width: float = 32.88, height: float = 15.6,
                  duplicate: bool = False, inputs_per_slot: int = 1) -> str:
    attribute = "" if declared is None else f' data-comb-slots="{declared}"'
    inner = "".join(
        f'<div class="s" style="left:{offset}pt;top:0.72pt;'
        f'width:{span}pt;height:14.16pt">'
        + ('<input type="text" maxlength="1">' * inputs_per_slot)
        + "</div>"
        for offset, span in slots)
    cell = (f'<div id="p1c13"{attribute} style="left:{left}pt;top:{top}pt;'
            f'width:{width}pt;height:{height}pt">{inner}</div>')
    body = cell + (cell.replace('id="p1c13"', 'id="p1c99"') if duplicate else "")
    return f"<!doctype html><html><body><div class=\"p\">{body}</div></body></html>"


FIVE_SLOTS = ((0.0, 6.58), (6.58, 6.57), (13.15, 6.58),
              (19.73, 6.57), (26.3, 6.58))
THREE_SLOTS = ((0.0, 11.04), (11.04, 10.8), (21.84, 11.04))
PRINTED_BOX = (180.24, 118.8, 213.12, 134.4)
PRINTED_TICKS = (191.28, 202.08)


def self_test() -> int:
    failures: list[str] = []

    def refuses(label: str, kind: str, thunk: Any) -> None:
        try:
            thunk()
        except Refusal as refusal:
            if refusal.kind != kind:
                failures.append(
                    f"{label}: refused as {refusal.kind!r}, wanted {kind!r}")
        except Exception as error:      # noqa: BLE001 - a traceback is not a refusal
            failures.append(f"{label}: {type(error).__name__}: {error}")
        else:
            failures.append(f"{label}: did not refuse")

    # --- the parser, on bytes it has never seen a marker of ----------------
    corrected = find_printed_box_element(
        parse_document(_fixture_html(FIVE_SLOTS, declared=5)),
        PRINTED_BOX, "fixture")
    ranges = slot_page_ranges(corrected)
    if len(ranges) != 5:
        failures.append(f"five emitted compartments must be counted as 5, got "
                        f"{len(ranges)}")
    if [round(low, 2) for low, _high in ranges] != [180.24, 186.82, 193.39,
                                                    199.97, 206.54]:
        failures.append(f"compartment page-space ranges are wrong: {ranges}")
    if corrected.element_id != "p1c13":
        failures.append("the cell id hint must be carried through")

    # The count is the STRUCTURE, never the attribute. A document whose
    # attribute claims five compartments it does not contain is the shape a
    # copy-the-manifest report would be built on, and it refuses.
    refuses("an attribute that outruns the structure must refuse",
            "declared-slots-disagree-with-structure",
            lambda: find_printed_box_element(
                parse_document(_fixture_html(THREE_SLOTS, declared=5)),
                PRINTED_BOX, "fixture"))
    refuses("a compartment with two inputs must refuse", "slot-input-count",
            lambda: find_printed_box_element(
                parse_document(_fixture_html(FIVE_SLOTS, declared=5,
                                             inputs_per_slot=2)),
                PRINTED_BOX, "fixture"))
    refuses("two elements on the printed box must refuse", "subject-cardinality",
            lambda: find_printed_box_element(
                parse_document(_fixture_html(FIVE_SLOTS, declared=5,
                                             duplicate=True)),
                PRINTED_BOX, "fixture"))
    refuses("no element on the printed box must refuse", "subject-cardinality",
            lambda: find_printed_box_element(
                parse_document(_fixture_html(FIVE_SLOTS, declared=5, left=300.0)),
                PRINTED_BOX, "fixture"))
    # A reflowed comb no longer sits on the printed box. The cell id is how
    # the HTML is found; the PDF is still measured at the original box.
    reflowed = find_printed_box_element(
        parse_document(_fixture_html(FIVE_SLOTS, declared=5, left=160.0,
                                     width=50.0)),
        PRINTED_BOX, "fixture", cell_id="p1c13")
    if reflowed.element_id != "p1c13" or abs(reflowed.box[0] - 160.0) > 0.01:
        failures.append("a reflowed comb must be found by cell id when it "
                        "has left the printed box")
    if len(reflowed.slots) != 5:
        failures.append("a reflowed comb must still count structure, not the box")
    refuses("a reflowed comb with the wrong cell id must refuse",
            "subject-cardinality",
            lambda: find_printed_box_element(
                parse_document(_fixture_html(FIVE_SLOTS, declared=5, left=300.0)),
                PRINTED_BOX, "fixture", cell_id="p1c99"))
    # An attribute-free document still measures: the count never came from the
    # attribute, so removing it changes nothing.
    unattributed = find_printed_box_element(
        parse_document(_fixture_html(FIVE_SLOTS)), PRINTED_BOX, "fixture")
    if len(unattributed.slots) != 5:
        failures.append("the compartment count must not depend on data-comb-slots")

    # --- the observer ------------------------------------------------------
    observed = observe_divergence(
        record_id="C0T", cell_id_hint="p1c13",
        emitted_slot_ranges=ranges, printed_ticks_x=PRINTED_TICKS,
        printed_compartments=3)
    slots_check = observed["comb_slots_match_printed"]
    dividers_check = observed["inputs_span_no_printed_divider"]
    if not (slots_check["offender_observed"] and dividers_check["offender_observed"]):
        failures.append("5 emitted against 3 printed must observe both offenders")
    if slots_check["offender"]["emitted_slots"] != 5 or \
            slots_check["offender"]["printed_compartments"] != 3:
        failures.append("the offender must carry emitted and printed counts")
    spanned = dividers_check["offender"]["spanned_dividers"]
    if [item["inside_emitted_slot_index"] for item in spanned] != [1, 3]:
        failures.append(f"both printed ticks must land inside a compartment: "
                        f"{spanned}")

    refuses("an emitted count equal to the printed count must refuse",
            "no-observed-divergence",
            lambda: observe_divergence(
                record_id="C0T", cell_id_hint="p1c13",
                emitted_slot_ranges=ranges, printed_ticks_x=PRINTED_TICKS,
                printed_compartments=5))
    refuses("a printed tick outside every compartment must refuse",
            "printed-divider-not-spanned",
            lambda: observe_divergence(
                record_id="C0T", cell_id_hint="p1c13",
                emitted_slot_ranges=ranges, printed_ticks_x=(160.0, 260.0),
                printed_compartments=3))
    # The UNCORRECTED emission: three compartments whose edges sit exactly on
    # the printed ticks. An edge is not a span, so even with a count
    # disagreement forced, the second observation refuses.
    refuses("compartment edges resting on the printed ticks are not a span",
            "printed-divider-not-spanned",
            lambda: observe_divergence(
                record_id="C0T", cell_id_hint="p1c13",
                emitted_slot_ranges=slot_page_ranges(
                    find_printed_box_element(
                        parse_document(_fixture_html(THREE_SLOTS, declared=3)),
                        PRINTED_BOX, "fixture")),
                printed_ticks_x=PRINTED_TICKS, printed_compartments=4))
    refuses("an artwork printing no divider at all must refuse",
            "printed-divider-not-spanned",
            lambda: observe_divergence(
                record_id="C0T", cell_id_hint="p1c13",
                emitted_slot_ranges=ranges, printed_ticks_x=(),
                printed_compartments=1))

    # --- the pinned-artwork reader, on artwork built for this test ---------
    try:
        fitz = _import_fitz()
    except Refusal as refusal:
        failures.append(f"PyMuPDF is required for the self-test: {refusal}")
    else:
        page_box = PRINTED_BOX
        document = fitz.open()
        page = document.new_page(width=612, height=1008)
        for x in (page_box[0], page_box[2]):
            page.draw_line(fitz.Point(x, page_box[1]), fitz.Point(x, page_box[3]),
                           width=0.72)
        for x in PRINTED_TICKS:
            page.draw_line(fitz.Point(x, page_box[3] - 3.12),
                           fitz.Point(x, page_box[3]), width=0.72)
        reopened = fitz.open("pdf", document.tobytes())
        try:
            measured = measure_printed_box(reopened[0], page_box, "fixture")
            if measured["compartments"] != 3:
                failures.append(f"two internal ticks are three printed "
                                f"compartments, got {measured['compartments']}")
            if [round(x) for x in measured["internal_ticks_x_pt"]] != [191, 202]:
                failures.append(f"the ticks must be recovered from the paint "
                                f"stream: {measured['internal_ticks_x_pt']}")
            refuses("a box the artwork does not print must refuse",
                    "printed-box-not-found",
                    lambda: measure_printed_box(
                        reopened[0], (300.0, 118.8, 340.0, 134.4), "fixture"))
        finally:
            reopened.close()
            document.close()

    # --- what the gate will read ------------------------------------------
    sentence = expected_sentence({"id": "C01", "authority": "an authority"})
    if sentence != "diverges by declared override C01, authorised by an authority":
        failures.append(f"the divergence sentence must be re-derivable verbatim: "
                        f"{sentence}")
    report = build_report(
        manifest={"source_batch": "corpus/rT", "source_commit": "0" * 40},
        manifest_sha256="0" * 64, tree_label="forms-corrected",
        manifest_label="forms-corrected.manifest.json",
        records_label="tools/formgen/corrections",
        measured=[{"record_id": "C01", "still_diverges_from_official": True,
                   "checks_reporting_the_divergence": observed,
                   "report": sentence}])
    if report["proves_parity"] is not False or \
            report["is_non_regression_gate"] is not True:
        failures.append("the report must carry proves_parity false and "
                        "is_non_regression_gate true")
    if not all(item["still_diverges_from_official"] is True
               for item in report["records"]):
        failures.append("every record must be reported as still diverging")
    # gate.fidelity_report_text decodes every string of a JSON report and
    # searches the join. Reproduced here so an emitted sentence the gate could
    # not find is this file's failure, not a 60-minute discovery.
    strings: list[str] = []

    def collect(value: Any) -> None:
        if isinstance(value, str):
            strings.append(value)
        elif isinstance(value, dict):
            for key, child in value.items():
                strings.append(str(key))
                collect(child)
        elif isinstance(value, list):
            for child in value:
                collect(child)

    collect(json.loads(json.dumps(report)))
    if sentence not in "\n".join(strings):
        failures.append("the gate's substring search must find the exact "
                        "manifest sentence in the emitted report")

    for failure in failures:
        print(f"FAIL {failure}")
    print(f"corrected_fidelity self-test: {len(failures)} failure(s)")
    return 1 if failures else 0


def main(argv: Sequence[str] | None = None) -> int:
    here = pathlib.Path(__file__).resolve().parent
    repo = here.parent.parent
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--tree", default="forms-corrected")
    parser.add_argument("--manifest", default="forms-corrected.manifest.json")
    parser.add_argument("--records", default="tools/formgen/corrections")
    parser.add_argument("--out", default="build/corrected-fidelity.json")
    parser.add_argument("--repo", default=str(repo))
    parser.add_argument("--forms-root", default="~/Downloads/forms",
                        help="read-only directory holding the pinned official PDFs")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.self_test:
        return self_test()

    root = pathlib.Path(args.repo).resolve()

    def resolve(value: str) -> pathlib.Path:
        path = pathlib.Path(os.path.expanduser(value))
        return path if path.is_absolute() else root / path

    try:
        report = produce(tree=resolve(args.tree),
                         manifest_path=resolve(args.manifest),
                         records_dir=resolve(args.records),
                         repo=root,
                         forms_root=resolve(args.forms_root))
    except Refusal as refusal:
        print(f"REFUSED {refusal}", file=sys.stderr)
        return 2
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        print(f"REFUSED unreadable-input: {error}", file=sys.stderr)
        return 2

    out = resolve(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, sort_keys=False) + "\n",
                   encoding="utf-8")
    print(f"{out}: {report['divergence_count']} declared divergence(s) observed "
          f"and published")
    for item in report["records"]:
        print(f"  {item['record_id']} {item['form']}: "
              f"{item['emitted_slots']} emitted vs "
              f"{item['printed_compartments']} printed compartment(s); "
              f"{item['report']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
