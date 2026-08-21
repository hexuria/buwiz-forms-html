#!/usr/bin/env python3
"""Rewrite C01–C07 so the whole TIN strip is even 3-3-3-5.

Uriah 2026-08-17: do not squeeze five digits into the old last box. Take every
fill-in box of the old 3-3-3-3 (or 3-3-3-4) chain, including the separator
gaps, and re-divide that same outer span so every digit cell is the same
width. A white knockout covers the old printed boxes so the new cells read
cleanly. Digit groups keep one outer 0.72pt frame; interior ticks are
short bottom hairs like the official charbox (not a full-height wall on
every digit). Dash separators keep their dedicated grey/peach fill and
a black frame, and stay non-tabbable.

The branch group is locked to 00000 ONLY where the official artwork already
pre-prints 000 in the branch box — C01 (2550M) and C07 (1604CF). Everywhere the
official branch boxes are blank (C02 0605, C03 2551M, C04 2553, C05/C06
1600WP), the branch stays five EDITABLE cells: those are payment and
withholding sheets where the filer must be able to enter a real branch code,
and painting 00000 over a blank official box would credit every remittance to
the head office. `lock_branch` on each record carries that distinction.

This script rewrites the ledger find/replace (and expected_effect) against
HEAD `forms/`. It does not apply. Run correct.py afterwards.
"""
from __future__ import annotations

import json
import pathlib
import re
import sys

REPO = pathlib.Path(__file__).resolve().parents[3]
FORMS = REPO / "forms"
LEDGER = REPO / "tools" / "formgen" / "corrections"

CHAINS = [
    {
        "id": "C01",
        "file": "C01-2550m-tin-branch-code.json",
        "form": "2550m-2007",
        "ids": ["p1c127", "p1c8", "p1c9", "p1c10", "p1c11", "p1c12", "p1c13"],
        "branch_id": "p1c13",
        # Official artwork pre-prints 000 inside the branch box.
        "lock_branch": True,
    },
    {
        "id": "C02",
        "file": "C02-0605-tin-branch-code.json",
        "form": "0605-1999",
        "ids": ["p1c23", "p1c24", "p1c25", "p1c26", "p1c27", "p1c28", "p1c29"],
        "branch_id": "p1c29",
        # Official branch box is blank (two ticks, no 000) and 0605 is the
        # payment form: the filer must be able to type a real branch code.
        "lock_branch": False,
    },
    {
        "id": "C03",
        "file": "C03-2551m-tin-branch-code.json",
        "form": "2551m-2002",
        "ids": ["p1c13", "p1c14", "p1c15", "p1c16", "p1c17", "p1c18", "p1c19"],
        "branch_id": "p1c19",
        # Official prints two ticks and no 000.
        "lock_branch": False,
    },
    {
        "id": "C04",
        "file": "C04-2553-tin-branch-code.json",
        "form": "extra/2553-1999",
        "ids": ["p1c18", "p1c19", "p1c20", "p1c21", "p1c22", "p1c23", "p1c24"],
        "branch_id": "p1c24",
        # Official branch box is empty, no 000.
        "lock_branch": False,
    },
    {
        "id": "C05",
        "file": "C05-1600wp-tin-branch-code.json",
        "form": "extra/1600wp-2010",
        "ids": ["p1c10", "p1c11", "p1c12", "p1c13", "p1c14", "p1c15", "p1c16"],
        "branch_id": "p1c16",
        # Official prints four compartments with no pre-printed 000.
        "lock_branch": False,
    },
    {
        "id": "C06",
        "file": "C06-1600wp-agent-tin-branch-code.json",
        "form": "extra/1600wp-2010",
        "ids": ["p1c68", "p1c69", "p1c70", "p1c71", "p1c72", "p1c73", "p1c74"],
        "branch_id": "p1c74",
        # Official prints four compartments with no ink inside.
        "lock_branch": False,
    },
    {
        "id": "C07",
        "file": "C07-1604cf-tin-branch-code.json",
        "form": "extra/1604cf-2008",
        "ids": ["p1c7", "p1c8", "p1c9", "p1c10", "p1c11", "p1c12", "p1c13"],
        "branch_id": "p1c13",
        # Official pre-prints 000 in the first three of four compartments.
        "lock_branch": True,
    },
]


def fmt_pt(n: float) -> str:
    n = round(float(n) + 0.0, 2)
    if abs(n) < 1e-9:
        return "0"
    text = f"{n:.2f}".rstrip("0").rstrip(".")
    return text


def equal_split(total: float, n: int) -> list[float]:
    total = round(total, 2)
    edges = [round(i * total / n, 2) for i in range(n + 1)]
    edges[-1] = total
    widths = [round(edges[i + 1] - edges[i], 2) for i in range(n)]
    drift = round(total - sum(widths), 2)
    if drift:
        widths[-1] = round(widths[-1] + drift, 2)
    return widths


def split_divs(html: str) -> list[str]:
    out: list[str] = []
    i = 0
    while i < len(html):
        if html[i] in " \n\t":
            i += 1
            continue
        if not html.startswith("<div", i):
            raise ValueError(f"expected <div at {i}: {html[i:i + 40]!r}")
        start = i
        depth = 0
        j = i
        while j < len(html):
            if html.startswith("<div", j):
                depth += 1
                j += 4
                continue
            if html.startswith("</div>", j):
                depth -= 1
                j += 6
                if depth == 0:
                    out.append(html[start:j])
                    i = j
                    break
                continue
            j += 1
        else:
            raise ValueError("unclosed div")
    return out


def parse_open(div: str) -> tuple[str, dict[str, str], str]:
    match = re.match(r"<div([^>]*)>(.*)</div>\s*$", div, re.DOTALL)
    if not match:
        raise ValueError(f"cannot parse div: {div[:80]!r}")
    raw_attrs, inner = match.group(1), match.group(2)
    attrs: dict[str, str] = {}
    for item in re.finditer(r'([:\w-]+)="([^"]*)"', raw_attrs):
        attrs[item.group(1)] = item.group(2)
    return raw_attrs, attrs, inner


def style_prop(style: str, prop: str) -> float:
    match = re.search(rf"{prop}:(-?[\d.]+)pt", style)
    if not match:
        raise ValueError(f"no {prop} in {style!r}")
    return float(match.group(1))


def extract_chain(html: str, ids: list[str]) -> tuple[str, int, int]:
    starts = []
    for ident in ids:
        needle = f'id="{ident}"'
        pos = html.find(needle)
        if pos < 0:
            raise ValueError(f"{ident} not found")
        starts.append(html.rfind("<div", 0, pos))
    first = min(starts)
    last_id = ids[-1]
    pos = html.find(f'id="{last_id}"')
    start = html.rfind("<div", 0, pos)
    depth = 0
    j = start
    while j < len(html):
        if html.startswith("<div", j):
            depth += 1
            j += 4
            continue
        if html.startswith("</div>", j):
            depth -= 1
            j += 6
            if depth == 0:
                return html[first:j], first, j
        else:
            j += 1
    raise ValueError("could not close last element")


def input_class(inner: str) -> str:
    match = re.search(r"<input[^>]*class=\"([^\"]+)\"", inner)
    if not match:
        return "fi fc"
    cls = match.group(1)
    if "fc" not in cls.split():
        cls = f"{cls} fc"
    return cls


def slot_metrics(inner: str, height: float) -> tuple[float, float]:
    match = re.search(r'data-slot="0" style="([^"]+)"', inner)
    if match:
        style = match.group(1)
        return style_prop(style, "top"), style_prop(style, "height")
    # text-field insets: top is the first inset value
    inset = re.search(r"style=\"inset:([\d.]+)pt", inner)
    top = float(inset.group(1)) if inset else 0.72
    return top, round(height - top, 2)


# Interior comb ticks on official sheets are short bottom hairs, not a
# full-height wall: 2550M item-1 month v172 is 0.72×4.32pt in a 13.20pt box
# (~33% of the box, sitting on the floor). Only the group's outer frame is a
# complete rectangle. Painting `inset -0.72pt 0 0 0` on every slot made each
# digit look like its own bordered box and is the sitting defect.
HAIR_TICK_HEIGHT_FRAC = 0.33


def hair_tick_style(slot_height: float) -> str:
    hair_h = max(round(slot_height * HAIR_TICK_HEIGHT_FRAC, 2), 3.0)
    return (f";background:linear-gradient(#000,#000) right bottom / "
            f"0.72pt {fmt_pt(hair_h)}pt no-repeat")


def dedicated_fill(html: str, left: float, top: float, width: float,
                   height: float) -> str:
    """The SVG fill whose rectangle IS this separator, not a page-sized band."""
    best = None
    best_area_delta = None
    cell_area = width * height
    for attrs in re.findall(r"<rect([^>]+)>", html):
        def _g(name: str, src: str = attrs) -> str | None:
            match = re.search(rf'\b{name}="([^"]+)"', src)
            return match.group(1) if match else None
        fill, x, y, w, h = (_g("fill"), _g("x"), _g("y"), _g("width"),
                            _g("height"))
        if not all((fill, x, y, w, h)):
            continue
        if fill.lower() in {"#000", "#000000", "black", "#fff", "#ffffff",
                            "white"}:
            continue
        rx, ry, rw, rh = float(x), float(y), float(w), float(h)
        if abs(rw - width) > 1.5 or abs(rh - height) > 1.5:
            continue
        if (min(left + width, rx + rw) - max(left, rx) <= 0.5
                or min(top + height, ry + rh) - max(top, ry) <= 0.5):
            continue
        delta = abs(rw * rh - cell_area)
        if best_area_delta is None or delta < best_area_delta:
            best_area_delta = delta
            best = fill
    return best or "#c0c0c0"


def emit_comb(*, ident: str, attrs: dict[str, str], left: float, width: float,
              n_slots: int, slot_widths: list[float], top: float, height: float,
              slot_top: float, slot_height: float, cls: str,
              locked: bool) -> str:
    pitch = slot_widths[0]
    style = (f"left:{fmt_pt(left)}pt;top:{fmt_pt(top)}pt;"
             f"width:{fmt_pt(width)}pt;height:{fmt_pt(height)}pt;"
             "background:#fff;box-shadow:inset 0 0 0 0.72pt #000")
    parts = [
        f'<div id="{ident}" class="{attrs.get("class", "c f")}" '
        f'data-cell-kind="field" data-row="{attrs["data-row"]}" '
        f'data-col="{attrs["data-col"]}" data-field-kind="comb" '
        f'data-field-name="{ident}" data-comb-capacity="{n_slots}" '
        f'data-comb-slots="{n_slots}" data-comb-pitch="{fmt_pt(pitch)}" '
        f'style="{style}">'
    ]
    cursor = 0.0
    lock = ' value="0" readonly tabindex="-1"' if locked else ""
    for i, slot_w in enumerate(slot_widths):
        tick = "" if i == n_slots - 1 else hair_tick_style(slot_height)
        parts.append(
            f'<div class="s" data-slot="{i}" style="left:{fmt_pt(cursor)}pt;'
            f'top:{fmt_pt(slot_top)}pt;width:{fmt_pt(slot_w)}pt;'
            f'height:{fmt_pt(slot_height)}pt{tick}">'
            f'<input type="text" class="{cls}" id="{ident}-s{i}" name="{ident}" '
            f'data-slot-index="{i}" maxlength="1" autocomplete="off" '
            f'spellcheck="false"{lock}></div>'
        )
        cursor = round(cursor + slot_w, 2)
    parts.append("</div>")
    return "".join(parts)


def emit_sep(div: str, left: float, width: float, fill: str) -> str:
    _raw, attrs, _inner = parse_open(div)
    style = attrs["style"]
    top = style_prop(style, "top")
    height = style_prop(style, "height")
    new_style = (f"left:{fmt_pt(left)}pt;top:{fmt_pt(top)}pt;"
                 f"width:{fmt_pt(width)}pt;height:{fmt_pt(height)}pt;"
                 f"background:{fill};box-shadow:inset 0 0 0 0.72pt #000")
    rebuilt = []
    for match in re.finditer(r'([:\w-]+)="([^"]*)"',
                             re.match(r"<div([^>]*)>", div).group(1)):
        key, val = match.group(1), match.group(2)
        if key == "style":
            val = new_style
        rebuilt.append(f'{key}="{val}"')
    return "<div " + " ".join(rebuilt) + "></div>"


def reflow_chain(find_html: str, ids: list[str], branch_id: str,
                 lock_branch: bool, form_html: str) -> tuple[str, dict]:
    """Reflow the seven named TIN boxes; drop any 0.72pt hairlines in the span.

    P2 reading order can insert the top/bottom rule of the TIN row into the
    HTML between the fill-in boxes (C01 p1c128/p1c129, C03/C04 six 0.72pt
    slivers). The find string stays a contiguous span so the applier can
    match it; those slivers are not fillable and the knockout covers their
    ink, so they are not emitted.
    """
    divs = split_divs(find_html)
    by_id: dict[str, tuple[str, dict[str, str], str]] = {}
    span_boxes: list[tuple[float, float, float, float]] = []
    for div in divs:
        _raw, attrs, inner = parse_open(div)
        style = attrs["style"]
        span_boxes.append((
            style_prop(style, "left"), style_prop(style, "top"),
            style_prop(style, "width"), style_prop(style, "height"),
        ))
        cid = attrs.get("id")
        if cid:
            by_id[cid] = (div, attrs, inner)
    missing = [ident for ident in ids if ident not in by_id]
    if missing:
        raise ValueError(f"missing {missing} in TIN span")
    pieces = []
    for ident in ids:
        div, attrs, inner = by_id[ident]
        style = attrs["style"]
        pieces.append({
            "id": ident,
            "div": div,
            "attrs": attrs,
            "inner": inner,
            "left": style_prop(style, "left"),
            "top": style_prop(style, "top"),
            "width": style_prop(style, "width"),
            "height": style_prop(style, "height"),
            "is_group": ident in ids[0::2],
        })
    groups = [p for p in pieces if p["is_group"]]
    seps = [p for p in pieces if not p["is_group"]]
    x0 = pieces[0]["left"]
    x1 = round(pieces[-1]["left"] + pieces[-1]["width"], 2)
    total = round(x1 - x0, 2)
    sep_total = round(sum(p["width"] for p in seps), 2)
    digit_total = round(total - sep_total, 2)
    digit_n = 3 + 3 + 3 + 5
    digit_widths = equal_split(digit_total, digit_n)
    sep_widths = [p["width"] for p in seps]
    group_ns = [3, 3, 3, 5]
    group_slices = []
    cursor = 0
    for n in group_ns:
        group_slices.append(digit_widths[cursor:cursor + n])
        cursor += n

    # Slot metrics from the first comb-like inner, else from the branch.
    sample = next((p for p in groups if 'data-slot="' in p["inner"]), groups[-1])
    slot_top, slot_height = slot_metrics(sample["inner"], sample["height"])

    y0 = min(top for _l, top, _w, _h in span_boxes)
    y1 = max(top + height for _l, top, _w, height in span_boxes)
    knockout = (
        f'<div class="c" data-cell-kind="blank" '
        f'style="left:{fmt_pt(x0)}pt;top:{fmt_pt(y0)}pt;'
        f'width:{fmt_pt(total)}pt;height:{fmt_pt(round(y1 - y0, 2))}pt;'
        f'background:#fff"></div>'
    )

    out = [knockout]
    x = x0
    gi = 0
    si = 0
    new_groups = []
    for piece in pieces:
        if piece["is_group"]:
            slots = group_slices[gi]
            width = round(sum(slots), 2)
            locked = lock_branch and piece["id"] == branch_id
            cls = input_class(piece["inner"])
            html = emit_comb(
                ident=piece["id"], attrs=piece["attrs"], left=x, width=width,
                n_slots=len(slots), slot_widths=slots, top=piece["top"],
                height=piece["height"], slot_top=slot_top,
                slot_height=min(slot_height, round(piece["height"] - slot_top, 2)),
                cls=cls, locked=locked,
            )
            out.append(html)
            new_groups.append({
                "id": piece["id"],
                "left": x,
                "width": width,
                "slots": slots,
                "locked": locked,
            })
            x = round(x + width, 2)
            gi += 1
        else:
            width = sep_widths[si]
            fill = dedicated_fill(form_html, piece["left"], piece["top"],
                                  piece["width"], piece["height"])
            out.append(emit_sep(piece["div"], x, width, fill))
            x = round(x + width, 2)
            si += 1
    if abs(x - x1) > 0.02:
        raise ValueError(f"reflow did not land on original right edge: {x} vs {x1}")
    meta = {
        "x0": x0, "x1": x1, "total": total, "sep_total": sep_total,
        "digit_total": digit_total, "digit_widths": digit_widths,
        "y0": y0, "y1": y1, "groups": new_groups,
        "old_groups": [{"id": p["id"], "left": p["left"], "width": p["width"]}
                       for p in groups],
    }
    return "".join(out), meta


def count(html: str, text: str) -> int:
    return html.count(text)


def branch_sentence(branch_id: str, lock_branch: bool) -> str:
    if lock_branch:
        return (
            f"The branch group {branch_id} is five locked cells showing 00000 "
            f"(each input value=\"0\" readonly), because the official artwork "
            f"already pre-prints 000 in that box: the sheet declares the "
            f"head office and the corrected form must not invite a filer to "
            f"type over BIR's own printed constant."
        )
    return (
        f"The branch group {branch_id} is five EDITABLE cells (one blank "
        f"maxlength-1 input each, no value, no readonly), because the "
        f"official artwork prints an EMPTY branch box — no 000, no ink of "
        f"any kind inside it. Painting a locked 00000 over a blank official "
        f"box would silently declare the head office on a sheet whose filer "
        f"has to name a real branch."
    )


def describe(meta: dict, branch_id: str, lock_branch: bool) -> str:
    groups = meta["groups"]
    branch = next(g for g in groups if g["id"] == branch_id)
    gdesc = "; ".join(
        f"{g['id']} left {fmt_pt(g['left'])} width {fmt_pt(g['width'])} "
        f"({len(g['slots'])} x {', '.join(fmt_pt(w) for w in g['slots'])})"
        for g in groups
    )
    return (
        f"The whole TIN strip — four groups plus the three separator boxes "
        f"between them — keeps the same outer span {fmt_pt(meta['x0'])}-"
        f"{fmt_pt(meta['x1'])} pt ({fmt_pt(meta['total'])}pt). Separator "
        f"widths are unchanged (they only move). The remaining "
        f"{fmt_pt(meta['digit_total'])}pt is split equally across 14 digit "
        f"cells (3+3+3+5) by left(i)=round(i*width/14, 2). {gdesc}. "
        f"A white knockout covers that same outer rectangle so the official "
        f"3- or 4-cell artwork does not show through the new even cells. "
        f"Each digit group keeps ONE outer 0.72pt frame; interior slot "
        f"dividers are short bottom hairs (~33% of slot height), matching "
        f"how the official sheet draws a charbox tick (2550M item-1 month "
        f"v172 is 0.72×4.32pt in a 13.20pt box), not a full-height wall on "
        f"every digit. Separator boxes keep their dedicated SVG fill and a "
        f"0.72pt black frame, and stay non-tabbable. "
        f"{branch_sentence(branch_id, lock_branch)} First/last writing-"
        f"surface insets are not re-applied: even cell width across the strip "
        f"is the layout rule. Branch pitch {fmt_pt(branch['slots'][0])}."
    )


def unique_occurs(replace: str, branch_id: str) -> str:
    match = re.search(
        rf'<div id="{branch_id}"[^>]*data-comb-capacity="5" data-comb-slots="5" '
        rf'data-comb-pitch="[^"]+" style="left:[^"]+"',
        replace,
    )
    if not match:
        raise ValueError(f"cannot find unique 5-slot signature for {branch_id}")
    return match.group(0)


def update_evidence(record_id: str, meta: dict, lock_branch: bool) -> None:
    path = LEDGER / "evidence" / f"{record_id}-evidence.json"
    data = json.loads(path.read_text())
    policy = data.setdefault("what", {}).setdefault("layout_policy", {})
    policy["writing_box"] = (
        f"outer TIN strip unchanged {meta['x0']}-{meta['x1']} pt; the four "
        f"groups and three separators are reflowed inside it so every digit "
        f"cell is the same width (3-3-3-5). The official printed boxes are "
        f"knocked out with a white rectangle of that same outer span."
    )
    policy["division"] = (
        f"separators keep their printed widths (sum {meta['sep_total']}pt) "
        f"and move with the groups; digit cells equal-split the remaining "
        f"{meta['digit_total']}pt into 14: {meta['digit_widths']}"
    )
    policy["slot_inset"] = (
        "none — even digit-cell width across the whole strip is the rule; "
        "re-applying per-group 0.36pt insets would squeeze the edges of each "
        "group and make the last five cells look different from the first nine"
    )
    policy["chrome"] = (
        "each digit group: one outer 0.72pt frame, white knockout inside. "
        "Interior ticks are short bottom hairs (~33% of slot height), matching "
        "the official charbox (2550M month v172), not a full-height wall on "
        "every digit. Separators: dedicated SVG fill + 0.72pt black frame, "
        "no input, not in tab order."
    )
    if lock_branch:
        policy["per_slot_input"] = (
            "TIN1/2/3: one editable maxlength-1 input per cell. Branch: five "
            "inputs, each value 0, readonly, so the printed branch is 00000 "
            "and cannot be typed over. The lock is authorised by the official "
            "artwork's own pre-printed 000 in this box and by nothing else."
        )
        policy["branch_lock"] = (
            "locked — the official artwork pre-prints 000 inside the branch "
            "box, so the corrected form reproduces that printed constant as "
            "five readonly zeros rather than offering a typing surface over "
            "BIR's own ink."
        )
    else:
        policy["per_slot_input"] = (
            "TIN1/2/3: one editable maxlength-1 input per cell. Branch: five "
            "editable maxlength-1 inputs, each blank — no value attribute, no "
            "readonly, no tabindex override. The branch code is a value the "
            "filer supplies on this sheet."
        )
        policy["branch_lock"] = (
            "NOT locked — the official branch box prints no 000 and no ink of "
            "any kind inside it. A locked 00000 here would declare the head "
            "office on a form whose filer must name a real branch, so the "
            "five cells stay editable. Widening 3 (or 4) compartments to 5 is "
            "the whole change; nothing is pre-filled."
        )
    data["what"]["from"] = data["what"].get("from", 3)
    data["what"]["to"] = 5
    data["what"]["operation"] = (
        "reflow_tin_chain_to_3_3_3_5_lock_branch" if lock_branch
        else "reflow_tin_chain_to_3_3_3_5"
    )
    # Keep printed_box_pt as the ORIGINAL branch box (PDF identity).
    desc = data.get("subject", {}).get("description", "")
    extra = (
        " After the 2026-08-17 sitting the whole chain (not only this box) is "
        "reflowed to even 3-3-3-5 inside the original outer span; this printed "
        "box remains the PDF identity of the branch group."
    )
    if "whole chain" not in desc:
        data["subject"]["description"] = desc.rstrip(".") + "." + extra
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def main() -> int:
    report = []
    for spec in CHAINS:
        html_path = FORMS / spec["form"] / "index.html"
        html = html_path.read_text()
        find, _a, _b = extract_chain(html, spec["ids"])
        if find.count(f'id="{spec["branch_id"]}"') != 1:
            raise SystemExit(f"{spec['id']}: branch id not unique in span")
        lock_branch = spec["lock_branch"]
        replace, meta = reflow_chain(find, spec["ids"], spec["branch_id"],
                                     lock_branch, html)
        after_html = html.replace(find, replace, 1)
        if after_html.count(replace) != 1:
            raise SystemExit(f"{spec['id']}: replace is not unique")
        ledger_path = LEDGER / spec["file"]
        record = json.loads(ledger_path.read_text())
        branch = spec["branch_id"]
        before_slots = count(html, f'id="{branch}-s')
        after_slots = count(after_html, f'id="{branch}-s')
        before_name = count(html, f'name="{branch}"')
        after_name = count(after_html, f'name="{branch}"')
        signature = unique_occurs(replace, branch)
        absent = record["edits"][0]["find"][:80]
        # Keep a short unique absent of the OLD branch opening if present in find.
        old_open = re.search(
            rf'<div id="{branch}"[^>]*data-comb-slots="[34]"[^>]*style="[^"]+"',
            find,
        )
        if old_open:
            absent = old_open.group(0)
        record["edits"] = [{
            "file": "index.html",
            "find": find,
            "replace": replace,
            "occurrences": 1,
        }]
        record["what"] = describe(meta, branch, lock_branch)
        record["expected_effect"] = [
            {
                "kind": "count_delta",
                "file": "index.html",
                "text": f'id="{branch}-s',
                "before": before_slots,
                "after": after_slots,
            },
            {
                "kind": "count_delta",
                "file": "index.html",
                "text": f'name="{branch}"',
                "before": before_name,
                "after": after_name,
            },
            {
                "kind": "occurs",
                "file": "index.html",
                "text": signature,
                "count": 1,
            },
            {
                "kind": "absent",
                "file": "index.html",
                "text": absent,
            },
        ]
        branch_note = (
            "the branch group stays LOCKED at 00000 because the official "
            "artwork pre-prints 000 in that box"
            if lock_branch else
            "the branch group is five EDITABLE cells: the official branch box "
            "prints no 000, and a filer on this sheet must be able to enter a "
            "real branch code, so nothing is pre-filled and nothing is readonly"
        )
        note = (
            " Sitting 2026-08-17: Uriah approved the intent and required the "
            "whole TIN strip (groups + separators) to be re-divided into even "
            "3-3-3-5 cells. The squeezed-last-box layout is retired. Per his "
            f"lock rule, {branch_note}. Status stays declared until he "
            "confirms the new sitting."
        )
        # Re-derive rather than append: an earlier run of this script wrote a
        # note claiming every branch was locked 00000, and that claim is wrong
        # for the five sites whose official branch box is blank.
        base = re.split(r"\s*Sitting 2026-08-17:", record.get("notes", ""))[0]
        record["notes"] = base.rstrip() + note
        ledger_path.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n")
        update_evidence(spec["id"], meta, lock_branch)
        report.append({
            "id": spec["id"],
            "form": spec["form"],
            "box": [meta["x0"], meta["y0"], meta["x1"], meta["y1"]],
            "digit_cell": meta["digit_widths"][0],
            "branch": next(g for g in meta["groups"] if g["id"] == branch),
            "lock_branch": lock_branch,
            "find_len": len(find),
            "replace_len": len(replace),
        })
        print(f"{spec['id']} {spec['form']}: span {meta['x0']}-{meta['x1']} "
              f"digit~{meta['digit_widths'][0]}pt "
              f"branch {branch} w={next(g['width'] for g in meta['groups'] if g['id']==branch)} "
              f"{'LOCKED 00000' if lock_branch else 'EDITABLE'} "
              f"find {len(find)} -> {len(replace)}")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
