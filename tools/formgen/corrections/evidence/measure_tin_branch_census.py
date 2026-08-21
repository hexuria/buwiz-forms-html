#!/usr/bin/env python3
"""Measure, from the PINNED PDFs alone, what each bundle's artwork PRINTS for
the TIN comb chain -- and in particular how many compartments it prints for the
branch code.

This is EVIDENCE tooling for a stage-2 correction record.  It is not a
producer, not the applier and not the verifier.  Nothing it writes is consumed
by the pipeline.

Independence
    Nothing produced by the pipeline is read for the measurement: not the
    layout, not the IR, not the emitted HTML.  The only thing taken from the
    repository is the sha256 recorded in build/layout/<slug>.layout.json, used
    to LOCATE the pinned PDF under the official-forms root and to prove it is
    that exact artefact.  (It shares PyMuPDF with extract.py; the two 2550M
    readings this evidence rests on were additionally re-derived with
    `mutool draw -F trace` and Poppler's `pdftocairo -svg` and agree to the
    hundredth of a point.  See the record's evidence block.)

Method
  1. Find a "TIN" caption on the page (text search).
  2. Collect every VERTICAL ink mark within 16pt of that caption.  Three
     encodings occur in this corpus and all three are read:
       - stroked segments (`l` ops)                    -- 1999-2010 sheets;
       - the two sides of a STROKED rectangle          -- how the older sheets
         draw a comb group's box;
       - thin filled rectangles (<= 1.6pt wide)        -- how the 2018-era
         sheets paint both walls and ticks.
     Marks within 0.6pt of one x are one mark.
  3. Collinear pieces of one mark are stitched: a 2018-era wall is painted as
     two rectangles with the ruled row line between them (a 0.48pt gap), while
     adjacent rows lie more than 1pt apart and never join.
  4. Candidate row bands are the mark y-spans 8-30pt tall in that window, tried
     nearest-the-caption first.  The caption is sometimes in the row ABOVE its
     comb (1700), so a band is not required to contain it.
  5. Inside a band: marks as tall as the band are group WALLS, shorter marks
     are comb TICKS.  Boxes are the spans between consecutive walls;
     compartments = ticks strictly inside + 1.
  6. Accept the first chain  group,sep,group,sep,group,sep,box  whose first
     three groups measure 3 compartments each -- 000-000-000, the invariant
     part of a Philippine TIN.  A `sep` is a tick-less box <= 20pt wide: the
     dash BIR paints between TIN groups.  The fourth box is the branch code.

Anything not matching that shape is reported `not-measurable` rather than
guessed.  A guessed count in a tax form is worse than a missing one.

Usage
    python3 measure_tin_branch_census.py --forms-root ~/Downloads/forms \
        [--repo <checkout>] [--out tin-branch-census.json]
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import pathlib
import sys

try:
    import fitz  # PyMuPDF
except ImportError:  # pragma: no cover - evidence tooling only
    sys.exit("PyMuPDF is required: pip install pymupdf")


def sha_index(root: str) -> dict[str, list[str]]:
    index: dict[str, list[str]] = {}
    for base, _dirs, files in os.walk(root):
        for name in files:
            if not name.lower().endswith(".pdf"):
                continue
            path = os.path.join(base, name)
            digest = hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()
            index.setdefault(digest, []).append(path)
    return index


def vertical_marks(page) -> list[dict[str, float]]:
    raw: set[tuple[float, float, float]] = set()
    for drawing in page.get_drawings():
        stroked = "s" in drawing["type"]
        for item in drawing["items"]:
            if item[0] == "l":
                p, q = item[1], item[2]
                if abs(p.x - q.x) > 0.06:
                    continue
                candidates = [(p.x, min(p.y, q.y), max(p.y, q.y))]
            elif item[0] == "re":
                rect = item[1]
                width, height = rect.x1 - rect.x0, rect.y1 - rect.y0
                if height < 2.0:
                    continue
                if width <= 1.6 and height > width:
                    candidates = [((rect.x0 + rect.x1) / 2.0, rect.y0, rect.y1)]
                elif stroked and height <= 40:
                    candidates = [(rect.x0, rect.y0, rect.y1),
                                  (rect.x1, rect.y0, rect.y1)]
                else:
                    continue
            else:
                continue
            for x, y0, y1 in candidates:
                if y1 - y0 >= 1.0:
                    raw.add((round(x, 2), round(y0, 2), round(y1, 2)))
    return [{"x": x, "y0": y0, "y1": y1} for (x, y0, y1) in sorted(raw)]


def stitch(marks: list[dict[str, float]]) -> list[dict[str, float]]:
    by_x: dict[float, list[dict[str, float]]] = {}
    out: list[dict[str, float]] = []
    for mark in marks:
        by_x.setdefault(round(mark["x"], 2), []).append(mark)
    for _x, group in by_x.items():
        group.sort(key=lambda m: m["y0"])
        current = dict(group[0])
        for mark in group[1:]:
            if mark["y0"] - current["y1"] <= 1.0:
                current["y1"] = max(current["y1"], mark["y1"])
            else:
                out.append(current)
                current = dict(mark)
        out.append(current)
    return out


def merge_x(marks: list[dict[str, float]]) -> list[dict[str, float]]:
    out: list[dict[str, float]] = []
    for mark in sorted(marks, key=lambda m: m["x"]):
        if out and mark["x"] - out[-1]["x"] <= 0.6:
            out[-1]["y0"] = min(out[-1]["y0"], mark["y0"])
            out[-1]["y1"] = max(out[-1]["y1"], mark["y1"])
            continue
        out.append(dict(mark))
    return out


def chain_in_band(band_marks, band_y0: float, band_y1: float):
    band = merge_x(band_marks)
    height = band_y1 - band_y0
    walls = [m for m in band if (m["y1"] - m["y0"]) >= 0.85 * height]
    ticks = [m for m in band if (m["y1"] - m["y0"]) < 0.85 * height]
    boxes = []
    for left, right in zip(walls, walls[1:]):
        inner = [t for t in ticks if left["x"] + 0.8 < t["x"] < right["x"] - 0.8]
        boxes.append({"x0": left["x"], "x1": right["x"],
                      "ticks": [round(t["x"], 2) for t in inner],
                      "compartments": len(inner) + 1})
    for i in range(max(0, len(boxes) - 6)):
        g1, s1, g2, s2, g3, s3, branch = boxes[i:i + 7]
        if [g1["compartments"], g2["compartments"], g3["compartments"]] != [3, 3, 3]:
            continue
        if any(s["compartments"] != 1 or (s["x1"] - s["x0"]) > 20 for s in (s1, s2, s3)):
            continue
        return {
            "pattern": "3+3+3+%d" % branch["compartments"],
            "band_y": [round(band_y0, 2), round(band_y1, 2)],
            "groups_x": [[round(g["x0"], 2), round(g["x1"], 2), g["compartments"]]
                         for g in (g1, g2, g3, branch)],
            "separator_widths_pt": [round(s["x1"] - s["x0"], 2) for s in (s1, s2, s3)],
            "branch_box_x": [round(branch["x0"], 2), round(branch["x1"], 2)],
            "branch_ticks_x": branch["ticks"],
            "branch_compartments": branch["compartments"],
        }
    return None


def analyse(page, caption):
    centre = (caption.y0 + caption.y1) / 2.0
    near = stitch([m for m in vertical_marks(page)
                   if m["y1"] >= caption.y0 - 16 and m["y0"] <= caption.y1 + 16])
    spans: dict[tuple[float, float], int] = {}
    for mark in near:
        height = mark["y1"] - mark["y0"]
        if not (8.0 <= height <= 30.0):
            continue
        key = (round(mark["y0"], 2), round(mark["y1"], 2))
        spans[key] = spans.get(key, 0) + 1

    def rank(item):
        (y0, y1), count = item
        distance = 0.0 if y0 <= centre <= y1 else min(abs(y0 - centre), abs(y1 - centre))
        return (round(distance, 1), -count)

    for (y0, y1), _count in sorted(spans.items(), key=rank):
        selected = [m for m in near if m["y0"] >= y0 - 1.0 and m["y1"] <= y1 + 1.0]
        result = chain_in_band(selected, y0, y1)
        if result:
            return result
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--forms-root", required=True,
                        help="directory holding the pinned official PDFs (read-only)")
    parser.add_argument("--repo", default=str(pathlib.Path(__file__).resolve().parents[3].parent),
                        help="repository checkout holding build/layout/*.layout.json")
    parser.add_argument("--out", default="tin-branch-census.json")
    args = parser.parse_args()

    index = sha_index(os.path.expanduser(args.forms_root))
    layouts = sorted(glob.glob(os.path.join(args.repo, "build/layout/*.layout.json")))
    if not layouts:
        sys.exit(f"no layouts under {args.repo}/build/layout")

    report: dict[str, dict] = {}
    for layout_path in layouts:
        layout = json.load(open(layout_path))
        slug = os.path.basename(layout_path).replace(".layout.json", "")
        digest = layout["source"]["sha256"]
        matches = index.get(digest)
        if not matches:
            report[slug] = {"measured": False,
                            "reason": "pinned pdf not found by sha256",
                            "pdf_sha256": digest}
            continue
        document = fitz.open(matches[0])
        found = None
        for page_number in range(document.page_count):
            page = document[page_number]
            for caption in page.search_for("TIN"):
                result = analyse(page, caption)
                if result:
                    result["measured"] = True
                    result["page"] = page_number + 1
                    result["caption_rect"] = [round(v, 2) for v in
                                              (caption.x0, caption.y0, caption.x1, caption.y1)]
                    result["pdf"] = os.path.basename(matches[0])
                    result["pdf_sha256"] = digest
                    found = result
                    break
            if found:
                break
        report[slug] = found or {"measured": False,
                                 "reason": "no 3+3+3+N TIN chain measurable by this method",
                                 "pdf": os.path.basename(matches[0]),
                                 "pdf_sha256": digest}
        document.close()

    tally: dict[str, int] = {}
    for value in report.values():
        key = value.get("pattern", "not-measurable")
        tally[key] = tally.get(key, 0) + 1
    payload = {
        "method": "see module docstring of measure_tin_branch_census.py",
        "bundles": len(report),
        "tally": dict(sorted(tally.items())),
        "forms": dict(sorted(report.items())),
    }
    pathlib.Path(args.out).write_text(json.dumps(payload, indent=1, sort_keys=False) + "\n")
    print(f"bundles: {len(report)}")
    for key, count in sorted(tally.items()):
        print(f"  {key:16} {count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
