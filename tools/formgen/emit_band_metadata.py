#!/usr/bin/env python3
"""Emit metadata-only `<script id="formgen-bands">` and `provenance.json` growables
for forms whose bands are added via `lattice.py` rules (1600WP, 1707) or explicit
declarations (`tools/formgen/corrections/bands-declarations.json`: 1801, 2550Q, 1701A).

Leaves every other byte of each official sheet's DOM untouched.
"""
from __future__ import annotations

import collections
import hashlib
import json
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parent.parent
sys.path.insert(0, str(HERE))
import lattice  # noqa: E402

CELL_DIV_RE = re.compile(
    r'<div id="(p(\d+)c\d+)" class="([^"]+)" data-cell-kind="([^"]+)" '
    r'data-row="(\d+)" data-col="(\d+)"([^>]*)>(.*?)</div>',
    re.S,
)
STYLE_GEOM_RE = re.compile(
    r'style="left:([\d.]+)pt;top:([\d.]+)pt;width:([\d.]+)pt;height:([\d.]+)pt"'
)
RULE_RECT_RE = re.compile(
    r'<rect x="([\d.]+)" y="([\d.]+)" width="([\d.]+)" height="([\d.]+)" '
    r'fill="([^"]+)"(?: data-rule-id="([hv]\d+)")?'
)
TEXT_DIV_RE = re.compile(
    r'<div class="t" id="(p(\d+)t(\d+))" style="([^"]+)"[^>]*>(.*?)</div>'
)
FH_CSS_RE = re.compile(r'\.(fh\d+)\{([^}]+)\}')

# Target 5 forms: (frozen_slug, corrected_relpath, mode)
TARGETS = [
    ("1600wp-2010", "extra/1600wp-2010", "rule"),
    ("1707-2021", "extra/1707-2021", "rule"),
    ("1801-2018", "extra/1801-2018", "declared"),
    ("2550q-2024", "2550q-2024", "declared"),
    ("1701a-2018", "1701a-2018", "declared"),
]

# Rule-detected schedules on 1600WP & 1707 (row index lists matching lattice.detect_growables)
RULE_BANDS = {
    "1600wp-2010": [
        {"id": "p1g0", "page": 1, "schedule": "Schedule 1.1", "rows": [44, 45, 46, 47, 48, 49]},
    ],
    "1707-2021": [
        {"id": "p2g0", "page": 2, "schedule": "Schedule 2", "rows": [28, 30, 32, 34]},
        {"id": "p2g1", "page": 2, "schedule": "Schedule 3", "rows": [40, 42, 44, 46]},
    ],
}


def _parse_fh_classes(css_text: str) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for m in FH_CSS_RE.finditer(css_text):
        cls, body = m.groups()
        info: dict = {}
        fs = re.search(r'font-size:([\d.]+)pt', body)
        lh = re.search(r'line-height:([\d.]+)pt', body)
        ls = re.search(r'letter-spacing:(-?[\d.]+)pt', body)
        if fs:
            info["size_pt"] = float(fs.group(1))
        if lh:
            info["line_height_pt"] = float(lh.group(1))
        info["letter_spacing_pt"] = float(ls.group(1)) if ls else None
        out[cls] = info
    return out


def _parse_inset(style_str: str) -> list[float] | None:
    m = re.search(r'inset:([^;"]+)', style_str)
    if not m:
        return None
    parts = [float(x.replace("pt", "")) for x in m.group(1).strip().split()]
    if len(parts) == 1:
        return [parts[0]] * 4
    if len(parts) == 2:
        return [parts[0], parts[1], parts[0], parts[1]]
    if len(parts) == 4:
        return parts
    return None


def _parse_page(html_text: str, page_index: int, fh_classes: dict[str, dict]):
    pages = re.split(r'<div class="page page-\d+" id="page-(\d+)"', html_text)
    body = None
    for k in range(1, len(pages), 2):
        if int(pages[k]) == page_index:
            body = pages[k + 1]
            break
    if body is None:
        raise ValueError(f"Page {page_index} not found")

    cells = []
    for m in CELL_DIV_RE.finditer(body):
        cid, _p, cls, kind, r, c, attrs, inner = m.groups()
        gm = STYLE_GEOM_RE.search(attrs)
        if not gm:
            continue
        x, y, w, h = (float(v) for v in gm.groups())
        cell = {
            "id": cid,
            "kind": kind,
            "row": int(r),
            "col": int(c),
            "x": round(x, 4),
            "y": round(y, 4),
            "w": round(w, 4),
            "h": round(h, 4),
        }
        if kind == "field":
            fk_m = re.search(r'data-field-kind="([^"]+)"', attrs)
            fkind = fk_m.group(1) if fk_m else "text"
            inp_cls_m = re.search(r'class="[^"]*\b(fh\d+)\b', inner)
            fcls = inp_cls_m.group(1) if inp_cls_m else "fh0"
            fh = fh_classes.get(fcls, {})
            if fkind == "comb":
                # Parse comb slot_x from <span ... style="left:...pt;width:...pt"> or input
                slots_m = re.search(r'maxlength="(\d+)"', inner)
                cap = int(slots_m.group(1)) if slots_m else None
                # Extract comb slot positions from input style or data-comb
                inp_st = re.search(r'<input[^>]*style="([^"]+)"', inner)
                inp_style = inp_st.group(1) if inp_st else ""
                top_m = re.search(r'top:([\d.]+)pt', inp_style)
                h_m = re.search(r'height:([\d.]+)pt', inp_style)
                cy = float(top_m.group(1)) if top_m else 0.48
                ch = float(h_m.group(1)) if h_m else round(h - 0.96, 4)
                pitch = round(w / cap, 4) if cap else None
                slot_x = [round(i * (w / cap), 4) for i in range(cap + 1)] if cap else []
                cell["comb"] = {
                    "cells": cap,
                    "pitch_pt": pitch,
                    "slot_x": slot_x,
                    "y": round(cy, 4),
                    "h": round(ch, 4),
                }
                cell["field"] = {
                    "kind": "comb",
                    "class": fcls,
                    "size_pt": fh.get("size_pt", 9.0),
                    "line_height_pt": fh.get("line_height_pt", round(ch, 4)),
                    "letter_spacing_pt": fh.get("letter_spacing_pt"),
                    "capacity": cap,
                    "inset_trbl": None,
                }
            else:
                inp_st = re.search(r'<input[^>]*style="([^"]+)"', inner)
                inset = _parse_inset(inp_st.group(1)) if inp_st else [0.48, 0.48, 0.48, 0.96]
                cell["field"] = {
                    "kind": fkind,
                    "class": fcls,
                    "size_pt": fh.get("size_pt", 9.0),
                    "line_height_pt": fh.get("line_height_pt", round(h - 0.96, 4)),
                    "letter_spacing_pt": fh.get("letter_spacing_pt"),
                    "capacity": None,
                    "inset_trbl": inset,
                }
        cells.append(cell)

    # Parse all SVG <rect> rules (structural + knockout/decorative with rule-id)
    svg_m = re.search(r'<svg class="rl"[^>]*>(.*?)</svg>', body, re.S)
    rects = []
    if svg_m:
        for m in RULE_RECT_RE.finditer(svg_m.group(1)):
            rx, ry, rw, rh, fill, rid = m.groups()
            if not rid:
                continue
            rects.append({
                "x": round(float(rx), 4),
                "y": round(float(ry), 4),
                "w": round(float(rw), 4),
                "h": round(float(rh), 4),
                "fill": fill,
                "id": rid,
            })

    texts = []
    for m in TEXT_DIV_RE.finditer(body):
        tid, _p, _tidx, style, txt = m.groups()
        lm = re.search(r'left:([\d.]+)pt', style)
        tm = re.search(r'top:([\d.]+)pt', style)
        # strip left/top/transform from style for band text style
        clean_style = re.sub(r'(?:left|top):[^;]+;?', '', style).strip(';')
        clean_style = re.sub(r'transform:[^;]+;?', '', clean_style).strip(';')
        clean_style = re.sub(r'transform-origin:[^;]+;?', '', clean_style).strip(';')
        bo_m = re.search(r'transform-origin:0 ([\d.]+)pt', style)
        bo = float(bo_m.group(1)) if bo_m else 7.48
        tx = float(lm.group(1)) if lm else 0.0
        ty = float(tm.group(1)) if tm else 0.0
        texts.append({
            "id": tid,
            "x": round(tx, 4),
            "y": round(ty, 4),
            "baseline_y": round(ty + bo, 4),
            "baseline_offset_pt": round(bo, 4),
            "style": clean_style,
            "text": re.sub(r'<[^>]+>', '', txt),
            "scale_x": None,
        })
    return cells, rects, texts


def _modal_index(buckets: dict[int, list], count: int) -> int:
    counts = collections.Counter(len(buckets.get(i, [])) for i in range(count))
    if not counts:
        return 0
    best_len = max(counts.items(), key=lambda kv: (kv[1], -kv[0]))[0]
    for i in range(count):
        if len(buckets.get(i, [])) == best_len:
            return i
    return 0


def build_band_json(spec: dict, cells: list[dict], rects: list[dict], texts: list[dict]) -> dict:
    row_nums = spec["rows"]
    by_row = collections.defaultdict(list)
    for c in cells:
        if c["row"] in row_nums:
            by_row[c["row"]].append(c)
    for r in row_nums:
        if not by_row[r]:
            raise ValueError(f"Band {spec['id']} row {r} has no cells")

    row_y = [round(min(c["y"] for c in by_row[r]), 4) for r in row_nums]
    row_y.append(round(max(c["y"] + c["h"] for c in by_row[row_nums[-1]]), 4))
    deltas = [round(b - a, 4) for a, b in zip(row_y, row_y[1:])]
    pitch = min(collections.Counter(deltas).most_common(), key=lambda kv: (-kv[1], kv[0]))[0]

    band_x0 = min(c["x"] for r in row_nums for c in by_row[r])
    band_x1 = max(c["x"] + c["w"] for r in row_nums for c in by_row[r])
    top, bottom = row_y[0], row_y[-1]
    cap = len(row_nums)

    # Classify rects overlapping the band horizontally
    boundaries: dict[int, list[dict]] = collections.defaultdict(list)
    rules_by_row: dict[int, list[dict]] = collections.defaultdict(list)
    spanning: list[dict] = []

    for r in rects:
        rx0, ry0, rx1, ry1 = r["x"], r["y"], r["x"] + r["w"], r["y"] + r["h"]
        # Must overlap band x-span (with 2pt outer border margin)
        if rx1 < band_x0 - 2.0 or rx0 > band_x1 + 2.0:
            continue
        if r["id"].startswith("h"):
            rmid_y = (ry0 + ry1) / 2.0
            # Check if on a boundary
            bi = min(range(len(row_y)), key=lambda i: abs(row_y[i] - rmid_y))
            if abs(row_y[bi] - rmid_y) <= 1.5:
                boundaries[bi].append(r)
            elif top < rmid_y < bottom:
                for ri in range(cap):
                    if row_y[ri] < rmid_y < row_y[ri + 1]:
                        rules_by_row[ri].append(r)
                        break
        else:
            # Vertical rule: spanning if it crosses >=2 rows (or covers whole band height)
            if ry0 <= top + 1.5 and ry1 >= bottom - 2.5:
                spanning.append({
                    "rect": r,
                    "d_top": round(ry0 - top, 4),
                    "d_bottom": round(ry1 - bottom, 4),
                })
            elif ry0 >= top - 1.5 and ry1 <= bottom + 1.5:
                for ri in range(cap):
                    if ry0 >= row_y[ri] - 1.5 and ry1 <= row_y[ri + 1] + 1.5:
                        rules_by_row[ri].append(r)
                        break

    # Detect ordinal column (enumerated "1".."N" or "A".."D")
    ordinal_col = None
    first_row_cells = sorted(by_row[row_nums[0]], key=lambda c: (c["x"], c["id"]))
    for ci in range(len(first_row_cells)):
        col_cells = [sorted(by_row[r], key=lambda c: (c["x"], c["id"]))[ci] for r in row_nums if ci < len(by_row[r])]
        if len(col_cells) != cap:
            continue
        col_texts = []
        for cc in col_cells:
            ts = [t["text"] for t in texts if cc["x"] - 0.5 <= t["x"] <= cc["x"] + cc["w"] + 0.5 and cc["y"] - 0.5 <= t["y"] <= cc["y"] + cc["h"] + 0.5]
            col_texts.append("".join(ts).strip())
        if lattice.column_role(col_texts) == "enumerated":
            ordinal_col = [round(col_cells[0]["x"], 4), round(col_cells[0]["x"] + col_cells[0]["w"], 4)]
            break

    rows_out = []
    for ri, rnum in enumerate(row_nums):
        rcells = sorted(by_row[rnum], key=lambda c: (c["x"], c["id"]))
        rtexts = []
        for t in texts:
            if band_x0 - 0.5 <= t["x"] <= band_x1 + 0.5 and row_y[ri] - 0.5 <= t["y"] <= row_y[ri + 1] + 0.5:
                role = "enumerated" if (ordinal_col and ordinal_col[0] - 0.5 <= t["x"] <= ordinal_col[1] + 0.5) else None
                rtexts.append({
                    "id": t["id"],
                    "row": ri,
                    "role": role,
                    "text": t["text"],
                    "x": t["x"],
                    "baseline_y": t["baseline_y"],
                    "style": t["style"],
                    "baseline_offset_pt": t["baseline_offset_pt"],
                    "scale_x": t["scale_x"],
                })
        rows_out.append({
            "index": ri,
            "y": row_y[ri],
            "rules": rules_by_row.get(ri, []),
            "cells": rcells,
            "texts": rtexts,
        })

    return {
        "id": spec["id"],
        "page": spec["page"],
        "kind": "repeating_rows",
        "capacity": cap,
        "rendered_rows": cap,
        "row_pitch_pt": pitch,
        "row_y": row_y,
        "template_row": _modal_index(rules_by_row, cap),
        "template_boundary": _modal_index(boundaries, cap + 1),
        "ordinal_column": ordinal_col,
        "boundaries": [boundaries.get(i, []) for i in range(cap + 1)],
        "spanning": spanning,
        "rows": rows_out,
    }


def main() -> None:
    decl = json.loads((HERE / "corrections/bands-declarations.json").read_text())["forms"]
    inv_path = REPO / "html-frozen/inventory.json"
    inv = json.loads(inv_path.read_text())
    inv_by_slug = {b["frozen_slug"]: b for b in inv["bundles"]}

    for slug, corr_rel, mode in TARGETS:
        specs = RULE_BANDS[slug] if mode == "rule" else decl[slug]
        frozen_dir = REPO / "html-frozen" / slug
        corr_dir = REPO / "forms-corrected" / corr_rel
        css_text = (frozen_dir / "form.css").read_text()
        fh_classes = _parse_fh_classes(css_text)

        # Build bands from frozen index.html
        html_frozen = (frozen_dir / "index.html").read_text()
        bands_json = []
        prov_growables = []
        for spec in specs:
            cells, rects, texts = _parse_page(html_frozen, spec["page"], fh_classes)
            b = build_band_json(spec, cells, rects, texts)
            bands_json.append(b)
            prov_growables.append({
                "page": b["page"],
                "rows": b["capacity"],
                "capacity": b["capacity"],
                "pitch_pt": b["row_pitch_pt"],
            })

        band_str = json.dumps(bands_json, separators=(",", ":"))
        old_tag = '<script type="application/json" id="formgen-bands">\n[]\n</script>'
        new_tag = f'<script type="application/json" id="formgen-bands">\n{band_str}\n</script>'

        for label, d in (("frozen", frozen_dir), ("corrected", corr_dir)):
            idx_p = d / "index.html"
            h = idx_p.read_text()
            if old_tag in h:
                h_new = h.replace(old_tag, new_tag, 1)
            else:
                h_new, n = re.subn(
                    r'<script type="application/json" id="formgen-bands">\n.*?\n</script>',
                    lambda _: new_tag,
                    h,
                    count=1,
                    flags=re.S,
                )
                if n != 1:
                    raise RuntimeError(f"{slug} ({label}): could not find formgen-bands script tag")
            idx_p.write_text(h_new)

            prov_p = d / "provenance.json"
            prov = json.loads(prov_p.read_text())
            prov["growables"] = prov_growables
            prov_p.write_text(json.dumps(prov, indent=2) + "\n")

        sha = hashlib.sha256((frozen_dir / "index.html").read_bytes()).hexdigest()
        inv_by_slug[slug]["index_sha256"] = sha
        print(f"OK {slug} ({mode}): {len(bands_json)} bands -> {[(b['id'], b['capacity'], b['row_pitch_pt'], b['ordinal_column']) for b in bands_json]}")

    inv_path.write_text(json.dumps(inv, indent=2) + "\n")
    print("Updated html-frozen/inventory.json")


if __name__ == "__main__":
    main()
