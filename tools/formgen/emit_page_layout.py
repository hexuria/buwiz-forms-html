#!/usr/bin/env python3
"""Structurally derive `<script type="application/json" id="formgen-page-layout">`
(version 1) for frozen BIR HTML sheets, plus attach `unid_svg_indices` to each
row in `<script id="formgen-bands">`.

Bands are derived strictly from the sheet's own structure:
  - full-width horizontal rules in `layer-structural`,
  - full-width grey caption bars in `layer-decorative`,
  - existing `formgen-bands` growable tables,
  - `.c` cell geometry and `.t` text spans.

Metadata only: every visible element of the page is left byte-for-byte untouched.
A strict per-page self-check verifies contiguity, full page coverage, zero cell
straddling, exact 1-to-1 element partitioning, elastic-box safety, and valid
references. Any page failing the self-check is omitted from `pages` and reported.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import statistics
import sys
from typing import Any

# Confirmed Rule 6 inline "(specify)" fields: slug -> set of writer_key patterns or exact keys.
# Only inline text-in-label-column specify boxes sharing a row with an amount cell.
RULE6_CONFIRMED: dict[str, set[str]] = {
    "2551q-2018": {"frm2551Qv2018:txt17Specify"},
    "2550q-2024": {
        "frm2550qv2024:addSpecifyNo19",
        "frm2550qv2024:addSpecifyNo42",
        "frm2550qv2024:addSpecifyNo47",
        "frm2550qv2024:addSpecifyNo56",
    },
    "1700-2018": {
        "frm1700:txtPg2I45Desc",
        "frm1700:txtPg2I49Desc",
        "frm1700:txtPg2I51Desc",
        "frm1700:txtPg2I57Desc",
    },
    "1701a-2018": {
        "frm1701A:txt63Desc",
        "frm1701A:txt41Desc",
        "frm1701A:txt42Desc",
        "frm1701A:txt50Desc",
        "frm1701A:txt51Desc",
    },
    "1701q-2018": {
        "frm1701q:txt43Desc",
        "frm1701q:txt48Desc",
        "frm1701q:txt61Desc",
    },
    "1701ms-2024": {
        "derived:foreign_tax_credits_description",
        "derived:other_credits_description",
    },
    "1701-2018": {
        "frm1701:txtPg4IPart7_9Specify",
        "frm1701:txtPg2IShed3_19Desc",
        "frm1701:txtPg2IShed3_20Desc",
        "frm1701:txtPg3IShed3_27Desc",
    },
    "1702mx-2018c": {
        "frm1702MX:txtPg2Sc3It30",
        "frm1702MX:txtPg2Sc3It31",
    },
    "1601c-2018": {
        "frm1601c:txt20Other",
        "frm1601c:txt29Other",
    },
    "1603q-2018": {
        "frm1603Q:txt16Other",
    },
    "1706-2018": {
        "frm1706:txtOtherss30F",
    },
    "1606-2018": {
        "frm1606:txtOtherss28E",
    },
    "2000-ot-2018": {
        "frm2000OT:Sched2:txtOthers",
    },
}

PAGE_RE = re.compile(
    r'<div class="page[^"]*" id="page-(\d+)" style="width:([\d.]+)pt;height:([\d.]+)pt">'
)
CELL_OPEN_RE = re.compile(r'<div\s+([^>]*class="c[^"]*"[^>]*)>')
TEXT_DIV_RE = re.compile(
    r'<div\s+([^>]*class="t[^"]*"[^>]*)>(.*?)</div>', re.S
)
ATTR_RE = re.compile(r'([\w-]+)="([^"]*)"')
STYLE_BOX_RE = re.compile(
    r"left:([\d.]+)pt;top:([\d.]+)pt;width:([\d.]+)pt;height:([\d.]+)pt"
)
STYLE_POS_RE = re.compile(r"left:([\d.]+)pt;top:([\d.]+)pt")
SVG_BLOCK_RE = re.compile(r"<svg\b[^>]*>(.*?)</svg>", re.S)
SVG_TOKEN_RE = re.compile(
    r"<(/?)(g|rect|line|path|polygon|polyline|circle|ellipse|image|text)\b([^>]*)>"
)
NUM_RE = re.compile(r"-?\d+(?:\.\d+)?")

STAMP_KEYWORDS = (
    "machine validation",
    "revenue official receipt",
    "stamp of receiving",
)
SECTION_TITLE_RE = re.compile(
    r"^(?:part\s+[ivx0-9]+|schedule\s+[ivx0-9a-z.]+|\(continuation\s+of\s+schedule)",
    re.I,
)
TOTAL_KEYWORDS = ("total", "sub-total", "subtotal")


def _parse_attrs(s: str) -> dict[str, str]:
    return dict(ATTR_RE.findall(s))


def _path_y_bounds(d_str: str) -> tuple[float, float, float, float]:
    """Approximate bounding box (x0, y0, x1, y1) of an SVG path d string."""
    # Parse absolute M/L/H/V/C/Q/Z commands (all frozen paths are absolute).
    tokens = re.findall(r"[A-Za-z]|-?\d+(?:\.\d+)?", d_str)
    xs: list[float] = []
    ys: list[float] = []
    cx = cy = 0.0
    i = 0
    cmd = ""
    while i < len(tokens):
        tok = tokens[i]
        if tok.isalpha():
            cmd = tok
            i += 1
            continue
        if cmd in ("M", "L", "T"):
            cx, cy = float(tokens[i]), float(tokens[i + 1])
            xs.append(cx)
            ys.append(cy)
            i += 2
        elif cmd in ("m", "l", "t"):
            cx += float(tokens[i])
            cy += float(tokens[i + 1])
            xs.append(cx)
            ys.append(cy)
            i += 2
        elif cmd == "H":
            cx = float(tokens[i])
            xs.append(cx)
            i += 1
        elif cmd == "h":
            cx += float(tokens[i])
            xs.append(cx)
            i += 1
        elif cmd == "V":
            cy = float(tokens[i])
            ys.append(cy)
            i += 1
        elif cmd == "v":
            cy += float(tokens[i])
            ys.append(cy)
            i += 1
        elif cmd == "C":
            for k in range(0, 6, 2):
                xs.append(float(tokens[i + k]))
                ys.append(float(tokens[i + k + 1]))
            cx, cy = float(tokens[i + 4]), float(tokens[i + 5])
            i += 6
        elif cmd == "c":
            for k in range(0, 6, 2):
                xs.append(cx + float(tokens[i + k]))
                ys.append(cy + float(tokens[i + k + 1]))
            cx += float(tokens[i + 4])
            cy += float(tokens[i + 5])
            i += 6
        elif cmd in ("S", "Q"):
            for k in range(0, 4, 2):
                xs.append(float(tokens[i + k]))
                ys.append(float(tokens[i + k + 1]))
            cx, cy = float(tokens[i + 2]), float(tokens[i + 3])
            i += 4
        elif cmd in ("s", "q"):
            for k in range(0, 4, 2):
                xs.append(cx + float(tokens[i + k]))
                ys.append(cy + float(tokens[i + k + 1]))
            cx += float(tokens[i + 2])
            cy += float(tokens[i + 3])
            i += 4
        else:
            i += 1
    if not xs or not ys:
        return (0.0, 0.0, 0.0, 0.0)
    return (min(xs), min(ys), max(xs), max(ys))


def parse_svg_nodes(phtml: str) -> list[dict[str, Any]]:
    """Return every graphic element inside `<svg>` in document order (0-based index)."""
    m = SVG_BLOCK_RE.search(phtml)
    if not m:
        return []
    svg_body = m.group(1)
    nodes: list[dict[str, Any]] = []
    layer_stack: list[str] = []
    idx = 0
    for tm in SVG_TOKEN_RE.finditer(svg_body):
        closing, tag, raw_attrs = tm.groups()
        if tag == "g":
            if closing:
                if layer_stack:
                    layer_stack.pop()
            else:
                attrs = _parse_attrs(raw_attrs)
                cls = attrs.get("class", "")
                if not raw_attrs.rstrip().endswith("/"):
                    layer_stack.append(cls)
            continue
        if closing:
            continue
        attrs = _parse_attrs(raw_attrs)
        layer = next((c for c in reversed(layer_stack) if c), "")
        x0 = y0 = x1 = y1 = 0.0
        fill = attrs.get("fill", "").lower()
        if tag in ("rect", "image"):
            x0 = float(attrs.get("x", 0.0))
            y0 = float(attrs.get("y", 0.0))
            x1 = x0 + float(attrs.get("width", 0.0))
            y1 = y0 + float(attrs.get("height", 0.0))
        elif tag == "line":
            ax, ay = float(attrs.get("x1", 0.0)), float(attrs.get("y1", 0.0))
            bx, by = float(attrs.get("x2", 0.0)), float(attrs.get("y2", 0.0))
            x0, x1 = min(ax, bx), max(ax, bx)
            y0, y1 = min(ay, by), max(ay, by)
        elif tag == "path":
            x0, y0, x1, y1 = _path_y_bounds(attrs.get("d", ""))
        elif tag in ("polygon", "polyline"):
            nums = [float(v) for v in NUM_RE.findall(attrs.get("points", ""))]
            xs, ys = nums[0::2], nums[1::2]
            if xs and ys:
                x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
        elif tag == "circle":
            cx, cy, r = (
                float(attrs.get("cx", 0.0)),
                float(attrs.get("cy", 0.0)),
                float(attrs.get("r", 0.0)),
            )
            x0, y0, x1, y1 = cx - r, cy - r, cx + r, cy + r
        nodes.append(
            {
                "idx": idx,
                "tag": tag,
                "rule_id": attrs.get("data-rule-id"),
                "layer": layer,
                "fill": fill,
                "x0": x0,
                "y0": y0,
                "x1": x1,
                "y1": y1,
                "w": x1 - x0,
                "h": y1 - y0,
            }
        )
        idx += 1
    return nodes


def parse_cells(phtml: str) -> list[dict[str, Any]]:
    cells: list[dict[str, Any]] = []
    for m in CELL_OPEN_RE.finditer(phtml):
        attrs = _parse_attrs(m.group(1))
        cid = attrs.get("id")
        if not cid:
            continue
        sm = STYLE_BOX_RE.search(attrs.get("style", ""))
        if not sm:
            continue
        x, y, w, h = (float(v) for v in sm.groups())
        cells.append(
            {
                "id": cid,
                "x0": x,
                "y0": y,
                "x1": x + w,
                "y1": y + h,
                "w": w,
                "h": h,
                "cell_kind": attrs.get("data-cell-kind", ""),
                "field_kind": attrs.get("data-field-kind", ""),
                "row": int(attrs["data-row"]) if "data-row" in attrs else -1,
                "col": int(attrs["data-col"]) if "data-col" in attrs else -1,
            }
        )
    return cells


def parse_texts(phtml: str) -> list[dict[str, Any]]:
    texts: list[dict[str, Any]] = []
    for m in TEXT_DIV_RE.finditer(phtml):
        attrs = _parse_attrs(m.group(1))
        tid = attrs.get("id")
        if not tid:
            continue
        sm = STYLE_POS_RE.search(attrs.get("style", ""))
        if not sm:
            continue
        x, y = float(sm.group(1)), float(sm.group(2))
        txt = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", m.group(2))).strip()
        # Estimate single-line text height (~7.5pt)
        fs_m = re.search(r"font-size:([\d.]+)pt", attrs.get("style", ""))
        h_est = float(fs_m.group(1)) * 1.15 if fs_m else 7.5
        texts.append(
            {
                "id": tid,
                "x": x,
                "y": y,
                "y1": y + h_est,
                "text": txt,
            }
        )
    return texts


def dedup_cuts(cuts: list[float], tol: float = 1.5) -> list[float]:
    if not cuts:
        return []
    cuts_sorted = sorted(cuts)
    out = [cuts_sorted[0]]
    for y in cuts_sorted[1:]:
        if y - out[-1] > tol:
            out.append(y)
    return out


def derive_page_layout(
    slug: str,
    pnum: int,
    pw: float,
    ph: float,
    phtml: str,
    formgen_bands: list[dict[str, Any]],
    wc: dict[str, Any],
) -> tuple[dict[str, Any] | None, list[str]]:
    """Derive one page's band layout and run the strict self-check."""
    svg_nodes = parse_svg_nodes(phtml)
    cells = parse_cells(phtml)
    texts = parse_texts(phtml)

    # 1. Table frame X bounds from full-width horizontal structural rules
    full_h = [
        n
        for n in svg_nodes
        if n["tag"] == "rect" and n["h"] <= 4.0 and n["w"] >= pw * 0.70
    ]
    if not full_h:
        return None, [f"p{pnum}: no full-width horizontal rules found"]
    table_x0 = round(statistics.median(n["x0"] for n in full_h), 2)
    table_x1 = round(statistics.median(n["x1"] for n in full_h), 2)
    table_w = table_x1 - table_x0

    # 2. Last drawn Y (excluding full-page background rects)
    max_y = 0.0
    for n in svg_nodes:
        if n["h"] < ph * 0.96 and n["w"] < pw * 0.99:
            max_y = max(max_y, n["y1"])
    for c in cells:
        if c["h"] < ph * 0.96:
            max_y = max(max_y, c["y1"])
    for t in texts:
        max_y = max(max_y, t["y1"])
    last_drawn_y = min(ph, round(max_y, 2))

    # 3. Growable groups on this page from formgen-bands
    page_growables = [g for g in formgen_bands if g.get("page") == pnum and g.get("rows")]
    # Map each growable to its [y0, y1] envelope
    grow_ranges: list[dict[str, Any]] = []
    for g in page_growables:
        rows = g["rows"]
        # Determine exact top and bottom of the growable block from its rows' cells
        row_y0s = []
        row_y1s = []
        for r in rows:
            for rc in r.get("cells", []):
                row_y0s.append(float(rc["y"]))
                row_y1s.append(float(rc["y"]) + float(rc["h"]))
        if not row_y0s:
            continue
        gy0 = min(row_y0s)
        gy1 = max(row_y1s)
        grow_ranges.append(
            {
                "id": g["id"],
                "y0": gy0,
                "y1": gy1,
                "schedule": g.get("schedule"),
                "table_group": g["id"],
            }
        )
    grow_ranges.sort(key=lambda r: r["y0"])

    # 4. Detect paired half-tables (e.g., 1707 Schedule 2 upper half above "(Continuation of Schedule 2)")
    # If a text span just above a growable says "(Continuation of Schedule N)", pair it with the
    # matching upper table under "Schedule N" on the same page.
    half_table_ranges: list[dict[str, Any]] = []
    for gr in grow_ranges:
        cont_texts = [
            t
            for t in texts
            if gr["y0"] - 45.0 <= t["y"] <= gr["y0"]
            and re.search(r"(?i)continuation\s+of\s+(schedule\s+[0-9a-z.]+)", t["text"])
        ]
        if not cont_texts:
            continue
        m_sched = re.search(r"(?i)continuation\s+of\s+(schedule\s+[0-9a-z.]+)", cont_texts[0]["text"])
        sched_name = m_sched.group(1).strip()
        group_name = f"p{pnum}_{re.sub(r'[^a-z0-9]+', '_', sched_name.lower()).strip('_')}"
        gr["table_group"] = group_name
        # Find the upper Schedule N heading above cont_texts[0]["y"]
        upper_hdrs = [
            t
            for t in texts
            if t["y"] < cont_texts[0]["y"] - 20.0
            and t["text"].lower().startswith(sched_name.lower())
        ]
        if upper_hdrs:
            uh_y = upper_hdrs[-1]["y"]
            # Find the field cells between uh_y and cont_texts[0]["y"]
            upper_fields = [
                c
                for c in cells
                if c["cell_kind"] == "field"
                and uh_y + 10.0 <= c["y0"]
                and c["y1"] <= cont_texts[0]["y"] + 2.0
            ]
            if upper_fields:
                uy0 = min(c["y0"] for c in upper_fields)
                uy1 = max(c["y1"] for c in upper_fields)
                # Ensure it has >= 2 distinct rows
                distinct_rows = len({round(c["y0"], 0) for c in upper_fields})
                if distinct_rows >= 2:
                    half_table_ranges.append(
                        {
                            "id": None,
                            "y0": uy0,
                            "y1": uy1,
                            "schedule": sched_name,
                            "table_group": group_name,
                        }
                    )

    all_row_templates = sorted(grow_ranges + half_table_ranges, key=lambda r: r["y0"])

    # 5. Collect structural horizontal cut candidates
    raw_cuts: list[float] = [0.0, last_drawn_y]
    if ph - last_drawn_y > 1.0:
        raw_cuts.append(ph)

    # Full-width horizontal structural rules
    for n in full_h:
        if n["w"] >= table_w * 0.85:
            raw_cuts.append(round(n["y0"], 2))

    # Full-width grey decorative bars
    grey_bars: list[tuple[float, float]] = []
    for n in svg_nodes:
        if (
            n["tag"] == "rect"
            and "layer-decorative" in n["layer"]
            and n["w"] >= table_w * 0.85
            and 7.0 <= n["h"] <= 35.0
            and n["fill"] not in ("#ffffff", "#fff", "white", "#000000", "#000", "none", "")
        ):
            grey_bars.append((round(n["y0"], 2), round(n["y1"], 2)))
            raw_cuts.append(round(n["y0"], 2))
            raw_cuts.append(round(n["y1"], 2))

    # Force cuts at row_template boundaries (snapped to nearest full-width rule if within 2pt)
    for rt in all_row_templates:
        raw_cuts.append(round(rt["y0"], 2))
        raw_cuts.append(round(rt["y1"], 2))

    cuts = dedup_cuts(raw_cuts, tol=1.5)

    # 6. Drop any internal cut that straddles a .c cell interior OR falls inside a row_template block
    def straddles_cell(y: float) -> bool:
        for c in cells:
            if c["y0"] + 2.0 < y < c["y1"] - 2.0:
                return True
        return False

    def inside_row_template(y: float) -> bool:
        for rt in all_row_templates:
            if rt["y0"] + 2.0 < y < rt["y1"] - 2.0:
                return True
        return False

    clean_cuts: list[float] = [0.0]
    for y in cuts[1:-1]:
        if inside_row_template(y):
            continue
        if straddles_cell(y):
            continue
        clean_cuts.append(y)
    if cuts[-1] > clean_cuts[-1]:
        clean_cuts.append(cuts[-1])

    # Ensure page ends at exact ph
    if clean_cuts[-1] < ph - 0.5:
        clean_cuts.append(ph)
    else:
        clean_cuts[-1] = ph

    # 7. Merge top bands into a single `page_header` band
    # Page header ends at the first section title ("Part I", "Part IV", "Schedule 1", etc.)
    # or first grey bar below y=85pt.
    header_end_y: float | None = None
    for y in clean_cuts[1:]:
        if y < 75.0:
            continue
        # Check if the slice starting at `y` is a Part/Schedule title or grey section bar
        next_idx = clean_cuts.index(y)
        if next_idx + 1 >= len(clean_cuts):
            break
        ny = clean_cuts[next_idx + 1]
        slice_texts = [t["text"] for t in texts if y - 2.0 <= t["y"] < ny - 1.0]
        if any(SECTION_TITLE_RE.match(st) for st in slice_texts):
            header_end_y = y
            break
        # On page 2+, the TIN/Name header box usually ends around y=115..140 right before a grey bar
        if pnum >= 2 and any(abs(gb[0] - y) <= 2.5 for gb in grey_bars) and y >= 95.0:
            header_end_y = y
            break
    if header_end_y is not None:
        clean_cuts = [0.0] + [y for y in clean_cuts if y >= header_end_y]

    # 8. Build initial raw bands from clean_cuts
    bands: list[dict[str, Any]] = []
    for bi in range(len(clean_cuts) - 1):
        y0, y1 = round(clean_cuts[bi], 2), round(clean_cuts[bi + 1], 2)
        if y1 <= y0:
            continue
        bands.append(
            {
                "id": f"p{pnum}b{len(bands)}",
                "kind": "plain_block",
                "y0": y0,
                "y1": y1,
                "keep_with_next": False,
                "keep_with_prev": False,
                "repeat_on_break": False,
                "min_height_pt": None,
                "page_label_text_id": None,
                "growable_id": None,
                "table_group": None,
                "svg_ids": [],
                "svg_indices": [],
                "text_ids": [],
                "cell_ids": [],
                "spanning_svg_ids": [],
            }
        )

    if not bands:
        return None, [f"p{pnum}: produced 0 bands"]

    # 9. Assign elements to bands (exact 1-to-1 partition)
    # If the last band is trailing whitespace (starts at >= last_drawn_y - 1.5),
    # keep any bottom-border rule at band[-1].y0 inside band[-2] so band[-1] is pure trailing_space.
    has_trailing = len(bands) >= 2 and (ph - bands[-1]["y0"]) >= 1.0 and bands[-1]["y0"] >= last_drawn_y - 2.0

    def band_index_for_y(ymid: float) -> int:
        limit = len(bands) - 1 if has_trailing else len(bands)
        if has_trailing and ymid <= bands[-1]["y0"] + 1.5:
            # Assign to the last content band or earlier
            for i in range(limit):
                b = bands[i]
                if i == limit - 1:
                    if b["y0"] - 0.5 <= ymid <= b["y1"] + 1.5:
                        return i
                elif b["y0"] - 0.5 <= ymid < b["y1"] - 0.5:
                    return i
        for i, b in enumerate(bands):
            if i == len(bands) - 1:
                if b["y0"] - 0.5 <= ymid <= b["y1"] + 0.5:
                    return i
            else:
                if b["y0"] - 0.5 <= ymid < b["y1"] - 0.5:
                    return i
        return min(range(len(bands)), key=lambda i: abs((bands[i]["y0"] + bands[i]["y1"]) / 2 - ymid))

    # Cells
    cell_band: dict[str, int] = {}
    for c in cells:
        ymid = (c["y0"] + c["y1"]) / 2.0
        bi = band_index_for_y(ymid)
        bands[bi]["cell_ids"].append(c["id"])
        cell_band[c["id"]] = bi

    # Texts
    text_by_id: dict[str, dict[str, Any]] = {t["id"]: t for t in texts}
    for t in texts:
        ymid = (t["y"] + t["y1"]) / 2.0
        bi = band_index_for_y(ymid)
        bands[bi]["text_ids"].append(t["id"])

    # SVG nodes: detect vertical rules spanning multiple bands
    spanning_indices_by_band: dict[int, list[int]] = {i: [] for i in range(len(bands))}
    for n in svg_nodes:
        is_vertical_rule = n["tag"] == "rect" and n["w"] <= 4.0 and n["h"] >= 10.0
        crossed = []
        if is_vertical_rule:
            for i, b in enumerate(bands):
                if n["y0"] < b["y1"] - 1.5 and n["y1"] > b["y0"] + 1.5:
                    crossed.append(i)
        if is_vertical_rule and len(crossed) >= 2 and n["rule_id"]:
            for i in crossed:
                bands[i]["spanning_svg_ids"].append(n["rule_id"])
        else:
            ymid = (n["y0"] + n["y1"]) / 2.0
            bi = band_index_for_y(ymid)
            if n["rule_id"]:
                bands[bi]["svg_ids"].append(n["rule_id"])
            else:
                bands[bi]["svg_indices"].append(n["idx"])

    # Merge tiny empty hairline-gap slivers (h <= 3.5pt, 0 cells, 0 texts) into the preceding band
    merged_bands: list[dict[str, Any]] = []
    for b in bands:
        if (
            merged_bands
            and (b["y1"] - b["y0"]) <= 3.5
            and not b["cell_ids"]
            and not b["text_ids"]
        ):
            prev = merged_bands[-1]
            prev["y1"] = b["y1"]
            prev["svg_ids"].extend(b["svg_ids"])
            prev["svg_indices"].extend(b["svg_indices"])
            for sid in b["spanning_svg_ids"]:
                if sid not in prev["spanning_svg_ids"]:
                    prev["spanning_svg_ids"].append(sid)
        else:
            b["id"] = f"p{pnum}b{len(merged_bands)}"
            merged_bands.append(b)
    bands = merged_bands
    # Rebuild cell_band map after any sliver merge
    cell_band = {cid: i for i, b in enumerate(bands) for cid in b["cell_ids"]}

    # 10. Classify band kinds structurally
    cell_by_id = {c["id"]: c for c in cells}

    # (a) page_header (band 0)
    bands[0]["kind"] = "page_header"
    bands[0]["repeat_on_break"] = True
    for tid in bands[0]["text_ids"]:
        if re.search(r"(?i)^page\s+\d+", text_by_id[tid]["text"]):
            bands[0]["page_label_text_id"] = tid
            break

    # (b) trailing_space (last band if empty of cells/texts/structural rules)
    last_b = bands[-1]
    if (
        len(bands) >= 2
        and not last_b["cell_ids"]
        and not last_b["text_ids"]
        and not last_b["svg_ids"]
        and not last_b["svg_indices"]
    ):
        last_b["kind"] = "trailing_space"

    # (c) row_template bands (matched from formgen-bands; paired half-tables without their own
    # formgen-bands entry keep table_group as plain_block so row_template <=> growable_id != null)
    row_template_indices: set[int] = set()
    for i, b in enumerate(bands):
        if i == 0 or b["kind"] == "trailing_space":
            continue
        bmid = (b["y0"] + b["y1"]) / 2.0
        for rt in all_row_templates:
            if rt["y0"] - 2.0 <= bmid <= rt["y1"] + 2.0:
                if rt["id"] is not None:
                    b["kind"] = "row_template"
                    b["growable_id"] = rt["id"]
                b["table_group"] = rt["table_group"]
                row_template_indices.add(i)
                break

    # (d) table_head immediately above each row_template (if non-field band and not a section_title)
    for rti in sorted(row_template_indices):
        if rti - 1 >= 1:
            prev_b = bands[rti - 1]
            prev_fields = [c for cid in prev_b["cell_ids"] if cell_by_id[cid]["cell_kind"] == "field"]
            prev_texts = [text_by_id[tid]["text"] for tid in prev_b["text_ids"]]
            is_sec_title = any(SECTION_TITLE_RE.match(t) for t in prev_texts)
            if not prev_fields and not is_sec_title and prev_b["kind"] == "plain_block":
                prev_b["kind"] = "table_head"
                prev_b["keep_with_next"] = True
                prev_b["repeat_on_break"] = True
                prev_b["table_group"] = bands[rti]["table_group"]

    # (e) total_row immediately below each row_template (if text mentions Total/Sub-total)
    for rti in sorted(row_template_indices):
        if rti + 1 < len(bands):
            next_b = bands[rti + 1]
            next_texts = " ".join(text_by_id[tid]["text"].lower() for tid in next_b["text_ids"])
            if next_b["kind"] == "plain_block" and any(k in next_texts for k in TOTAL_KEYWORDS):
                next_b["kind"] = "total_row"
                next_b["keep_with_prev"] = True
                next_b["table_group"] = bands[rti]["table_group"]

    # (f) section_title (grey caption bars / Part / Schedule title bars with 0 field cells)
    for i, b in enumerate(bands):
        if b["kind"] != "plain_block":
            continue
        b_fields = [cid for cid in b["cell_ids"] if cell_by_id[cid]["cell_kind"] == "field"]
        if b_fields:
            continue
        b_texts = [text_by_id[tid]["text"] for tid in b["text_ids"]]
        bh = b["y1"] - b["y0"]
        if bh <= 32.0 and any(SECTION_TITLE_RE.match(t) for t in b_texts):
            b["kind"] = "section_title"
            b["keep_with_next"] = True
            # Propagate table_group if directly followed by a table_head or row_template
            if i + 1 < len(bands) and bands[i + 1]["table_group"]:
                b["table_group"] = bands[i + 1]["table_group"]

    # (g) elastic_box: ONLY empty stamp/validation boxes (0 editable field cells, stamp caption,
    # h >= 32pt, caption sits in the top portion leaving >= 8pt of actual empty shrinkable space).
    # Defensible min_height_pt rule: actual caption height from band.y0 + 12pt breathing room,
    # clamped to >= 24.0pt, and only marked elastic if natural height - min_height_pt >= 8.0pt.
    for b in bands:
        if b["kind"] != "plain_block":
            continue
        b_fields = [cid for cid in b["cell_ids"] if cell_by_id[cid]["cell_kind"] == "field"]
        if b_fields:
            continue
        bh = b["y1"] - b["y0"]
        if bh < 32.0:
            continue
        b_texts = [text_by_id[tid] for tid in b["text_ids"]]
        combined = " ".join(t["text"].lower() for t in b_texts)
        if any(k in combined for k in STAMP_KEYWORDS):
            caption_bottom = max((t["y1"] for t in b_texts), default=b["y0"] + 12.0)
            caption_h = max(0.0, caption_bottom - b["y0"])
            min_h = round(max(24.0, caption_h + 12.0), 1)
            if bh - min_h >= 8.0:
                b["kind"] = "elastic_box"
                b["min_height_pt"] = min_h

    # 11. Rule 6 fields on this page
    rule6_fields: list[dict[str, Any]] = []
    allowed_wks = RULE6_CONFIRMED.get(slug, set())
    if allowed_wks:
        for it in wc.get("joins", []):
            wk = it.get("writer_key", "")
            cid = it.get("html_id", "")
            if wk in allowed_wks and cid in cell_by_id:
                c = cell_by_id[cid]
                bi = cell_band[cid]
                rule6_fields.append(
                    {
                        "writer_key": wk,
                        "cell_id": cid,
                        "band_id": bands[bi]["id"],
                        "insert_after_y": round(c["y1"], 2),
                        "x0": table_x0,
                        "x1": table_x1,
                    }
                )

    page_obj = {
        "page": pnum,
        "width_pt": round(pw, 2),
        "height_pt": round(ph, 2),
        "content_bottom_y": round(ph - 18.0, 2),
        "table_x0": table_x0,
        "table_x1": table_x1,
        "bands": bands,
        "rule6_fields": rule6_fields,
    }

    # 12. STRICT SELF-CHECK
    errors = self_check_page(page_obj, svg_nodes, cells, texts, formgen_bands, wc)
    if errors:
        return None, errors
    return page_obj, []


def self_check_page(
    page_obj: dict[str, Any],
    svg_nodes: list[dict[str, Any]],
    cells: list[dict[str, Any]],
    texts: list[dict[str, Any]],
    formgen_bands: list[dict[str, Any]],
    wc: dict[str, Any],
) -> list[str]:
    errs: list[str] = []
    pnum = page_obj["page"]
    ph = page_obj["height_pt"]
    bands = page_obj["bands"]

    # 1. Contiguity & full page coverage
    if not bands:
        return [f"p{pnum}: empty bands"]
    if abs(bands[0]["y0"] - 0.0) > 0.01:
        errs.append(f"p{pnum}: band[0].y0={bands[0]['y0']} != 0.0")
    if abs(bands[-1]["y1"] - ph) > 0.01:
        errs.append(f"p{pnum}: band[-1].y1={bands[-1]['y1']} != height_pt={ph}")
    for i, b in enumerate(bands):
        if b["y1"] <= b["y0"]:
            errs.append(f"p{pnum}: {b['id']} non-positive height [{b['y0']}, {b['y1']}]")
        if i + 1 < len(bands) and abs(b["y1"] - bands[i + 1]["y0"]) > 0.01:
            errs.append(f"p{pnum}: gap between {b['id']}.y1={b['y1']} and {bands[i+1]['id']}.y0={bands[i+1]['y0']}")

    # 2. Cells: exact 1-to-1 assignment AND zero straddling (> 2.0pt)
    cell_by_id = {c["id"]: c for c in cells}
    seen_cells: list[str] = []
    for b in bands:
        for cid in b["cell_ids"]:
            seen_cells.append(cid)
            c = cell_by_id.get(cid)
            if not c:
                errs.append(f"p{pnum}: unknown cell {cid} in {b['id']}")
            else:
                if c["y0"] < b["y0"] - 2.5 or c["y1"] > b["y1"] + 2.5:
                    errs.append(
                        f"p{pnum}: cell {cid} [{c['y0']:.1f}..{c['y1']:.1f}] straddles {b['id']} [{b['y0']}..{b['y1']}]"
                    )
    if len(seen_cells) != len(set(seen_cells)):
        errs.append(f"p{pnum}: duplicate cell_ids across bands")
    if set(seen_cells) != set(cell_by_id.keys()):
        missing = set(cell_by_id.keys()) - set(seen_cells)
        errs.append(f"p{pnum}: missing cell_ids: {sorted(missing)[:5]}")

    # 3. Texts: exact 1-to-1 assignment
    text_ids_all = {t["id"] for t in texts}
    seen_texts: list[str] = []
    for b in bands:
        seen_texts.extend(b["text_ids"])
    if len(seen_texts) != len(set(seen_texts)):
        errs.append(f"p{pnum}: duplicate text_ids across bands")
    if set(seen_texts) != text_ids_all:
        errs.append(f"p{pnum}: text_ids mismatch ({len(seen_texts)} vs {len(text_ids_all)})")

    # 4. SVG nodes: exact partition between (single-band svg_ids + svg_indices) and (spanning_svg_ids + spanning_svg_indices)
    id_nodes = {n["rule_id"] for n in svg_nodes if n["rule_id"]}
    noid_nodes = {n["idx"] for n in svg_nodes if not n["rule_id"]}

    single_ids: list[str] = []
    single_idxs: list[int] = []
    span_ids: set[str] = set()
    span_idxs: set[int] = set()
    for b in bands:
        single_ids.extend(b["svg_ids"])
        single_idxs.extend(b["svg_indices"])
        span_ids.update(b["spanning_svg_ids"])
        span_idxs.update(b.get("spanning_svg_indices", []))

    if len(single_ids) != len(set(single_ids)):
        errs.append(f"p{pnum}: duplicate svg_ids across bands")
    if len(single_idxs) != len(set(single_idxs)):
        errs.append(f"p{pnum}: duplicate svg_indices across bands")
    if set(single_ids) & span_ids:
        errs.append(f"p{pnum}: svg_ids overlaps spanning_svg_ids")
    if set(single_idxs) & span_idxs:
        errs.append(f"p{pnum}: svg_indices overlaps spanning_svg_indices")
    if (set(single_ids) | span_ids) != id_nodes:
        errs.append(f"p{pnum}: ID'd SVG nodes not 100% covered ({len(set(single_ids) | span_ids)} vs {len(id_nodes)})")
    if (set(single_idxs) | span_idxs) != noid_nodes:
        errs.append(f"p{pnum}: un-ID'd SVG nodes not 100% covered ({len(set(single_idxs) | span_idxs)} vs {len(noid_nodes)})")

    # 5. Elastic box safety
    for b in bands:
        if b["kind"] == "elastic_box":
            fcount = sum(1 for cid in b["cell_ids"] if cell_by_id[cid]["cell_kind"] == "field")
            if fcount > 0:
                errs.append(f"p{pnum}: elastic_box {b['id']} contains {fcount} field cell(s)")
            mh = b["min_height_pt"]
            if mh is None or mh < 24.0 or mh > (b["y1"] - b["y0"]) + 0.01:
                errs.append(f"p{pnum}: elastic_box {b['id']} invalid min_height_pt={mh}")

    # 6. Growable references (BAND-FORMAT.md: row_template <=> non-null valid growable_id)
    valid_gids = {g["id"] for g in formgen_bands if g.get("page") == pnum}
    for b in bands:
        if b["kind"] == "row_template" and (b["growable_id"] is None or b["growable_id"] not in valid_gids):
            errs.append(f"p{pnum}: row_template {b['id']} missing/unknown growable_id={b['growable_id']}")
        if b["kind"] != "row_template" and b["growable_id"] is not None:
            errs.append(f"p{pnum}: non-row_template {b['id']} has growable_id={b['growable_id']}")

    # 7. Rule 6 references
    band_ids = {b["id"] for b in bands}
    valid_wks = {it.get("writer_key") for it in wc.get("joins", [])}
    for r6 in page_obj["rule6_fields"]:
        if r6["band_id"] not in band_ids or r6["cell_id"] not in cell_by_id or r6["writer_key"] not in valid_wks:
            errs.append(f"p{pnum}: invalid rule6_field {r6}")

    return errs


def attach_unid_svg_indices_to_formgen_bands(
    html: str, formgen_bands: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], int]:
    """Add `structural_rects` (BAND-FORMAT.md Section 6) to each row in `formgen-bands`."""
    if not formgen_bands:
        return formgen_bands, 0
    page_matches = list(PAGE_RE.finditer(html))
    svg_by_page: dict[int, list[dict[str, Any]]] = {}
    for i, pm in enumerate(page_matches):
        pnum = int(pm.group(1))
        phtml = html[pm.start() : (page_matches[i + 1].start() if i + 1 < len(page_matches) else len(html))]
        svg_by_page[pnum] = parse_svg_nodes(phtml)

    updated_rows = 0
    for g in formgen_bands:
        pnum = g.get("page")
        nodes = svg_by_page.get(pnum, [])
        for r in g.get("rows", []):
            r.pop("unid_svg_indices", None)
            rcells = r.get("cells", [])
            if not rcells:
                r["structural_rects"] = []
                continue
            ry0 = min(float(c["y"]) for c in rcells)
            ry1 = max(float(c["y"]) + float(c["h"]) for c in rcells)
            rx0 = min(float(c["x"]) for c in rcells) - 2.0
            rx1 = max(float(c["x"]) + float(c["w"]) for c in rcells) + 2.0
            srects = [
                {
                    "x": round(n["x0"], 2),
                    "y": round(n["y0"], 2),
                    "w": round(n["w"], 2),
                    "h": round(n["h"], 2),
                    "fill": n["fill"] or "#000000",
                }
                for n in nodes
                if not n["rule_id"]
                and n["tag"] == "rect"
                and n["y0"] >= ry0 - 1.5
                and n["y1"] <= ry1 + 1.5
                and n["x0"] >= rx0
                and n["x1"] <= rx1
                and n["h"] <= (ry1 - ry0) + 3.0
            ]
            r["structural_rects"] = srects
            updated_rows += 1
    return formgen_bands, updated_rows


def process_form(sdir: pathlib.Path, write: bool = False) -> dict[str, Any]:
    slug = sdir.name
    index_path = sdir / "index.html"
    wc_path = sdir / "writer-cells.json"
    html = index_path.read_text()
    wc = json.loads(wc_path.read_text()) if wc_path.exists() else {}

    # Parse existing formgen-bands
    fb_re = re.compile(r'(<script[^>]*id="formgen-bands"[^>]*>)(.*?)(</script>)', re.S)
    fb_m = fb_re.search(html)
    formgen_bands = json.loads(fb_m.group(2)) if fb_m else []

    # Attach un-ID'd structural rects to formgen-bands rows
    formgen_bands, updated_rows = attach_unid_svg_indices_to_formgen_bands(html, formgen_bands)

    page_matches = list(PAGE_RE.finditer(html))
    pages_out: list[dict[str, Any]] = []
    failed_pages: list[tuple[int, list[str]]] = []

    for i, pm in enumerate(page_matches):
        pnum = int(pm.group(1))
        pw, ph = float(pm.group(2)), float(pm.group(3))
        phtml = html[pm.start() : (page_matches[i + 1].start() if i + 1 < len(page_matches) else len(html))]
        page_obj, errs = derive_page_layout(slug, pnum, pw, ph, phtml, formgen_bands, wc)
        if errs or page_obj is None:
            failed_pages.append((pnum, errs))
        else:
            pages_out.append(page_obj)

    layout = {"version": 1, "pages": pages_out}

    if write:
        new_html = html
        # 1. Update formgen-bands in place (if present)
        if fb_m and updated_rows > 0:
            fb_json = json.dumps(formgen_bands, separators=(",", ":"))
            new_html = fb_re.sub(lambda m: f"{m.group(1)}{fb_json}{m.group(3)}", new_html, count=1)

        # 2. Insert or replace <script type="application/json" id="formgen-page-layout">
        layout_json = json.dumps(layout, separators=(",", ":"))
        layout_tag = f'<script type="application/json" id="formgen-page-layout">{layout_json}</script>'
        pl_re = re.compile(r'<script[^>]*id="formgen-page-layout"[^>]*>.*?</script>', re.S)
        if pl_re.search(new_html):
            new_html = pl_re.sub(lambda _: layout_tag, new_html, count=1)
        elif fb_m:
            new_html = fb_re.sub(lambda m: f"{m.group(0)}\n{layout_tag}", new_html, count=1)
        else:
            new_html = new_html.replace("</body>", f"{layout_tag}\n</body>", 1)

        # Verify visible DOM (everything outside the two metadata scripts) is byte-for-byte identical
        strip = lambda s: re.sub(
            r'\n?<script[^>]*id="formgen-(?:bands|page-layout)"[^>]*>.*?</script>', "", s, flags=re.S
        )
        assert strip(html) == strip(new_html), f"{slug}: visible HTML mutated!"
        index_path.write_text(new_html)

    return {
        "slug": slug,
        "total_pages": len(page_matches),
        "passed_pages": len(pages_out),
        "failed_pages": failed_pages,
        "updated_band_rows": updated_rows,
        "layout": layout,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("html_frozen_root", type=pathlib.Path)
    ap.add_argument("--slugs", nargs="*", help="Optional subset of slugs")
    ap.add_argument("--write", action="store_true", help="Write metadata into index.html")
    ap.add_argument("--dump", action="store_true", help="Print compact per-band summary")
    args = ap.parse_args()

    slugs = args.slugs or sorted(
        p.name for p in args.html_frozen_root.iterdir() if p.is_dir() and (p / "index.html").exists()
    )
    tot_p = tot_ok = 0
    for slug in slugs:
        res = process_form(args.html_frozen_root / slug, write=args.write)
        tot_p += res["total_pages"]
        tot_ok += res["passed_pages"]
        status = "OK" if not res["failed_pages"] else f"PARTIAL({res['passed_pages']}/{res['total_pages']})"
        print(
            f"{slug:15s} | {status:14s} | rows_unid={res['updated_band_rows']:2d} | "
            + ", ".join(
                f"p{p['page']}:{len(p['bands'])}b(r6={len(p['rule6_fields'])})"
                for p in res["layout"]["pages"]
            )
        )
        for fp, errs in res["failed_pages"]:
            for e in errs[:5]:
                print(f"    FAIL p{fp}: {e}")
        if args.dump:
            for p in res["layout"]["pages"]:
                print(f"  --- {slug} page {p['page']} (x={p['table_x0']}..{p['table_x1']}, bottom={p['content_bottom_y']}) ---")
                for b in p["bands"]:
                    flags = []
                    if b["keep_with_next"]:
                        flags.append("kwn")
                    if b["keep_with_prev"]:
                        flags.append("kwp")
                    if b["repeat_on_break"]:
                        flags.append("rep")
                    if b["min_height_pt"] is not None:
                        flags.append(f"min={b['min_height_pt']}")
                    if b["growable_id"]:
                        flags.append(f"grow={b['growable_id']}")
                    if b["table_group"]:
                        flags.append(f"grp={b['table_group']}")
                    if b["page_label_text_id"]:
                        flags.append(f"plbl={b['page_label_text_id']}")
                    print(
                        f"    {b['id']:5s} {b['kind']:14s} [{b['y0']:6.1f}..{b['y1']:6.1f}] "
                        f"h={b['y1']-b['y0']:5.1f} c={len(b['cell_ids']):2d} t={len(b['text_ids']):2d} "
                        f"svg={len(b['svg_ids'])}+{len(b['svg_indices'])} span={len(b['spanning_svg_ids'])} "
                        f"{' '.join(flags)}"
                    )
                if p["rule6_fields"]:
                    print(f"    rule6_fields: {p['rule6_fields']}")
    print(f"\nTOTAL: {tot_ok}/{tot_p} pages passed strict self-check across {len(slugs)} forms.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
