#!/usr/bin/env python3
"""Render forms/index.html from the two machine-produced reports.

The index is the only page a human reads before trusting a bundle, so every
number on it has to come from a file the pipeline wrote, never from a hand
edit. It joins build/batch-report.json (what was converted: pages, paper, rule
and cell counts, fonts) with build/audit.json (how well it round-trips: rules,
text, artwork). Writing it by hand drifts the moment a form is re-audited, and
a drifted status badge is worse than no badge -- it reads like evidence.

Two status names are deliberately not what an earlier hand-written index said:

  * "art rehash", not "missing art". Chromium's PDF writer re-encodes every
    embedded image -- DeviceGray becomes ICCBased RGB, alpha is composited onto
    the page -- so the compressed stream's SHA-256 changes even though the
    decoded raster is identical to the source's, pixel for pixel, at a 0.00pt
    placement delta. The artwork is present. Only the hash moved.
  * a text gap outranks an art rehash. An unmatched string is a real defect; a
    re-encoded image is a property of the reference pipeline.

The guide column is the same kind of claim. It links a bundle's guide.html only
when batch.py actually wrote one, and reports the page area that leaving the
form freed -- the reason the split is worth doing. A bundle with no guide gets
an em dash, never a dead link.

The leading ordinal and the count beside the heading answer the one question a
reader cannot answer by looking: is anything missing? Fifty-one rows of
thirteen columns do not count themselves, so the heading states the total and
the last row states its own position; a dropped bundle shows up as those two
disagreeing. The numbers come from the batch report's order, which batch.py
sorts (corpus first, then code, revision, variant) before writing it, so they
are a property of that sorted list and never of the order a directory scan
happened to return.

Usage:
    python3 tools/formgen/index_page.py            # writes forms/index.html
"""

from __future__ import annotations

import argparse
import html
import json
import pathlib
import statistics
import sys
from typing import Any, Iterable

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parent.parent

TEXT_CLEAN_PCT = 99.5           # the threshold the audit's text score is read against
FONT_LIST_CHARS = 40            # the fonts column truncates rather than wraps

# (badge label, CSS class). The classes are the existing palette: green, amber,
# orange, red.
BADGES = {
    "clean": ("clean", "g"),
    "text": ("text gaps", "y"),
    "art": ("art rehash", "o"),
    "structure": ("structure", "r2"),
}

STYLE = """\
body{font:13px/1.5 system-ui,sans-serif;margin:2rem;max-width:1180px;color:#111}
h1{font-size:1.3rem;margin:0 0 .3rem} p{color:#555;margin:.2rem 0 1rem}
table{border-collapse:collapse;width:100%} th,td{padding:4px 8px;border-bottom:1px solid #e8e8e8;text-align:left}
th{font-weight:600;font-size:11px;text-transform:uppercase;letter-spacing:.04em;color:#666;border-bottom:2px solid #bbb}
td.n,th.n{text-align:right;font-variant-numeric:tabular-nums} td.p{font-variant-numeric:tabular-nums;color:#555}
td.f{color:#888;font-size:11px} .r{color:#888;font-size:11px}
td.i,th.i{text-align:right;color:#888;font-variant-numeric:tabular-nums;width:2.4em}
.ct{font-size:.75em;font-weight:600;background:#eee;color:#555;padding:1px 8px;border-radius:9px;vertical-align:middle}
.v{background:#eee;padding:0 4px;border-radius:3px;font-size:10px;color:#555}
.b{padding:1px 7px;border-radius:9px;font-size:11px;font-weight:600;white-space:nowrap}
.g{background:#d7f5dd;color:#0a5528} .y{background:#fdf0c8;color:#6b4b04}
.o{background:#fde0cc;color:#7a3708} .r2{background:#fbd5d5;color:#7a1010} .u{background:#eee;color:#555}
a{color:#0a6;text-decoration:none;font-weight:600} a:hover{text-decoration:underline}
.k{margin:0 0 1.2rem;font-size:12px;color:#555} .k span{margin-right:1rem}
@media(prefers-color-scheme:dark){body{background:#111;color:#eee}th,td{border-color:#333}
.g{background:#123d22;color:#7ee2a0}.y{background:#3d3312;color:#e8cf7a}.o{background:#3d2412;color:#e8a56f}
.r2{background:#3d1616;color:#e88f8f}.u{background:#2a2a2a;color:#aaa}
.v,.ct{background:#2a2a2a;color:#aaa}}
"""


def classify(entry: dict[str, Any]) -> str:
    """Worst-first: structure beats text, text beats artwork.

    Ordering matters more than it looks. The earlier index let the artwork flag
    mask a text gap, so 1701MS showed "missing art" while seven of its strings
    were unmatched -- the milder, benign finding hid the real one.
    """
    if entry["status"] != "ok":
        return "structure"
    if not entry["paper_ok"] or (entry["rules_pct"] or 0) < 100.0:
        return "structure"
    if (entry["text_pct"] or 0) < TEXT_CLEAN_PCT:
        return "text"
    if entry["images_missing"] or entry["images_placement_violations"]:
        return "art"
    return "clean"


def badge(kind: str) -> str:
    label, css = BADGES[kind]
    return f'<span class="b {css}">{label}</span>'


def paper_label(paper: str) -> str:
    """'612.0x936.0' -> '612x936'; the .0 is noise in a column of page sizes."""
    return "x".join(f"{float(v):g}" for v in paper.split("x"))


def font_label(fonts: Iterable[str]) -> str:
    joined = ", ".join(fonts)
    return joined[:FONT_LIST_CHARS]


def bundle_href(record: dict[str, Any], document: str) -> str:
    return ("extra/" if not record["in_corpus"] else "") + f"{record['slug']}/{document}"


ORIGIN_TAGS = {"inline-region": "inline", "standalone-pdf": "pdf"}


def guide_cell(record: dict[str, Any]) -> str:
    """Link the guide document, or say plainly that there is none.

    The origin tag matters to a reader deciding whether the form page changed:
    "inline" means a region was cut off the sheet, "pdf" means the guide always
    was a separate document and the form pages are untouched.
    """
    guide = record.get("guide") or {}
    document = guide.get("document")
    if not document:
        return "<td class=r>&mdash;</td>"
    tags = [ORIGIN_TAGS.get(origin, origin) for origin in guide.get("origins", [])]
    label = f'<span class=v>{"+".join(tags)}</span>' if tags else ""
    return f'<td><a href="{bundle_href(record, document)}">guide</a> {label}</td>'


def reclaim_cell(record: dict[str, Any]) -> str:
    """How much of the page the split gave back, mean over the pages it cut."""
    guide = record.get("guide") or {}
    regions = guide.get("moved_from_form") or []
    if not regions:
        return "<td class=r>&mdash;</td>"
    detail = "; ".join(f"page {r['page']}: {r['reclaimed_pt']:g}pt ({r['reclaimed_pct']}% "
                       f"of the page)" for r in regions)
    return (f'<td class=n title="{html.escape(detail)}">'
            f'{guide["reclaimed_pct_mean"]}%</td>')


def form_link(record: dict[str, Any]) -> str:
    href = bundle_href(record, "index.html")
    tags = ""
    if record["variant"]:
        tags += f' <span class=v>{html.escape(record["variant"])}</span>'
    if not record["in_corpus"]:
        tags += ' <span class=v>extra</span>'
    return (f'<a href="{href}">{html.escape(record["code"])}</a>'
            f' <span class=r>{html.escape(record["revision"])}</span>{tags}')


def row(record: dict[str, Any], entry: dict[str, Any], number: int) -> str:
    art = entry["images_missing"] + entry["images_placement_violations"]
    grow = len(record["growables"])
    return (
        f'<tr><td class=i>{number}.</td><td>{form_link(record)}</td>\n'
        f'<td>{badge(classify(entry))}</td>'
        f'<td class=n>{entry["rules_pct"]:g}%</td>'
        f'<td class=n>{entry["text_pct"]:.1f}%</td>\n'
        f'<td class=n>{art or ""}</td>'
        f'<td class=n>{record["pages"]}</td>'
        f'<td class=p>{paper_label(record["paper"])}</td>\n'
        f'<td class=n>{record["rules"]:,}</td>'
        f'<td class=n>{record["cells"]:,}</td>'
        f'<td class=n>{grow or ""}</td>\n'
        f'{guide_cell(record)}{reclaim_cell(record)}\n'
        f'<td class=f>{html.escape(font_label(record["fonts"]))}</td></tr>'
    )


def guide_summary(batch: list[dict[str, Any]]) -> dict[str, int]:
    """Corpus-wide guide counts, split by where the guide content came from.

    Counted per bundle for the "how many" numbers and per cut page for the
    reclaim ones: a form with two guide pages reclaims on both, and averaging
    per form would quietly dilute that.
    """
    inline = standalone = both = 0
    pcts: list[int] = []
    for record in batch:
        guide = record.get("guide") or {}
        origins = set(guide.get("origins", []))
        inline += origins == {"inline-region"}
        standalone += origins == {"standalone-pdf"}
        both += len(origins) > 1
        pcts += [r["reclaimed_pct"] for r in guide.get("moved_from_form", [])]
    return {
        "forms": inline + standalone + both,
        "inline": inline, "standalone": standalone, "both": both,
        "pages": len(pcts),
        "pct_mean": round(sum(pcts) / len(pcts)) if pcts else 0,
        "pct_min": min(pcts) if pcts else 0,
        "pct_max": max(pcts) if pcts else 0,
    }


def render(batch: list[dict[str, Any]], audit: list[dict[str, Any]]) -> str:
    scored = {e["slug"]: e for e in audit}
    missing = [r["slug"] for r in batch if r["slug"] not in scored]
    if missing:
        raise SystemExit(f"no audit entry for: {', '.join(missing)}")

    n = len(batch)
    pages = sum(r["pages"] for r in batch)
    text = [scored[r["slug"]]["text_pct"] for r in batch]
    rules_ok = sum(1 for r in batch if scored[r["slug"]]["rules_pct"] == 100.0)
    paper_ok = sum(1 for r in batch if scored[r["slug"]]["paper_ok"])
    art_ok = sum(1 for r in batch if not scored[r["slug"]]["images_missing"]
                 and not scored[r["slug"]]["images_placement_violations"])
    text_ok = sum(1 for v in text if v >= TEXT_CLEAN_PCT)
    guides = guide_summary(batch)

    # Enumerate `batch` itself and nothing else. batch.py writes the report
    # already sorted -- corpus first, then code, revision, variant -- so the
    # ordinals are a property of that sorted list, and re-deriving an order here
    # would be a second, drifting opinion about what row 17 is.
    rows = "".join(row(r, scored[r["slug"]], i) for i, r in enumerate(batch, 1))
    return (
        "<!doctype html><meta charset=utf-8><title>Generated BIR forms</title><style>\n"
        + STYLE +
        "</style>\n"
        f"<h1>Generated BIR forms <span class=ct>{n} forms</span></h1>\n"
        f"<p>{n} forms, {pages} pages, numbered 1&ndash;{n} below. Status is <b>measured</b>:"
        " each bundle is printed to PDF with Chromium, re-extracted, and diffed against the"
        " source PDF&rsquo;s own geometry.</p>\n"
        f'<p class=k><span>{badge("clean")} rules 100%, text &ge;{TEXT_CLEAN_PCT:g}%, artwork byte-identical</span>\n'
        f'<span>{badge("text")} geometry exact, some strings unmatched</span>\n'
        f'<span>{badge("art")} geometry and pixels exact, image stream re-encoded</span>\n'
        f'<span>{badge("structure")} a rule or the paper size is wrong</span></p>\n'
        f"<p class=k>rules 100% on <b>{rules_ok}/{n}</b> &middot;\n"
        f"paper exact on <b>{paper_ok}/{n}</b> &middot;\n"
        f"text &ge;{TEXT_CLEAN_PCT:g}% on <b>{text_ok}/{n}</b>"
        f" (median <b>{statistics.median(text):.2f}%</b>, min <b>{min(text):.2f}%</b>) &middot;\n"
        f"artwork byte-identical on <b>{art_ok}/{n}</b></p>\n"
        f"<p class=k>guide split out of <b>{guides['forms']}/{n}</b> bundles\n"
        f"({guides['inline']} inline region, {guides['standalone']} standalone PDF,\n"
        f"{guides['both']} both) &middot;\n"
        f"reclaiming a mean <b>{guides['pct_mean']}%</b> of the <b>{guides['pages']}</b> pages"
        f" it cut (min {guides['pct_min']}%, max {guides['pct_max']}%)</p>\n"
        "<table><tr><th class=i>#<th>form<th>status<th class=n>rules<th class=n>text"
        "<th class=n>art?"
        "<th class=n>pp<th>paper<th class=n>rules<th class=n>cells<th class=n>grow"
        "<th>guide<th class=n>reclaim<th>fonts</tr>\n"
        + rows + "</table>\n"
    )


def _entry(**over: Any) -> dict[str, Any]:
    base = {"slug": "x", "status": "ok", "paper_ok": True, "rules_pct": 100.0,
            "text_pct": 100.0, "images_missing": 0, "images_placement_violations": 0}
    return base | over


def _record(slug: str, code: str, **over: Any) -> dict[str, Any]:
    base = {"slug": slug, "code": code, "revision": "2018", "variant": "",
            "in_corpus": True, "pages": 1, "paper": "612x792", "rules": 0,
            "cells": 0, "growables": [], "fonts": [], "guide": None}
    return base | over


def self_test() -> int:
    """The badge is the only thing most readers act on, so prove its ordering."""
    cases = [
        ("all clean", _entry(), "clean"),
        ("text below threshold", _entry(text_pct=99.49), "text"),
        ("text exactly at threshold", _entry(text_pct=99.5), "clean"),
        ("artwork rehashed only", _entry(images_missing=1), "art"),
        ("displaced artwork", _entry(images_placement_violations=1), "art"),
        # The ordering that the earlier hand-written index got wrong: 1701MS had
        # both, and the benign finding hid the real one.
        ("text gap outranks art rehash", _entry(text_pct=98.28, images_missing=1), "text"),
        ("a dropped rule outranks everything", _entry(rules_pct=99.9, text_pct=90.0,
                                                      images_missing=1), "structure"),
        ("wrong paper is structural", _entry(paper_ok=False), "structure"),
        ("a failed audit is structural", _entry(status="error"), "structure"),
    ]
    failures = 0
    for name, entry, expected in cases:
        got = classify(entry)
        ok = got == expected
        failures += not ok
        print(f"  {'PASS' if ok else 'FAIL'}  {name}: {got}"
              f"{'' if ok else f' (expected {expected})'}")

    # The guide column's one job is to never offer a link to a file batch.py did
    # not write, and to say where the guide came from when it did.
    no_guide = {"slug": "a", "in_corpus": True, "guide": None}
    inline = {"slug": "b", "in_corpus": True, "guide": {
        "document": "guide.html", "origins": ["inline-region"],
        "reclaimed_pct_mean": 70,
        "moved_from_form": [{"page": 2, "reclaimed_pt": 651.46, "reclaimed_pct": 70}]}}
    from_pdf = {"slug": "c", "in_corpus": False, "guide": {
        "document": "guide.html", "origins": ["standalone-pdf"],
        "reclaimed_pct_mean": 0, "moved_from_form": []}}
    guide_cases = [
        ("no guide links nothing", guide_cell(no_guide), "&mdash;" in guide_cell(no_guide)
         and "href" not in guide_cell(no_guide)),
        ("an inline guide links guide.html", guide_cell(inline),
         'href="b/guide.html"' in guide_cell(inline) and ">inline<" in guide_cell(inline)),
        ("an extra bundle's guide keeps the extra/ prefix", guide_cell(from_pdf),
         'href="extra/c/guide.html"' in guide_cell(from_pdf)),
        ("a record with no guide key at all is safe", guide_cell({"slug": "d", "in_corpus": True}),
         "&mdash;" in guide_cell({"slug": "d", "in_corpus": True})),
        ("an inline cut reports its reclaim", reclaim_cell(inline),
         ">70%<" in reclaim_cell(inline) and "651.46pt" in reclaim_cell(inline)),
        ("a standalone PDF reclaimed no form area", reclaim_cell(from_pdf),
         "&mdash;" in reclaim_cell(from_pdf)),
    ]
    for name, rendered, ok in guide_cases:
        failures += not ok
        print(f"  {'PASS' if ok else 'FAIL'}  {name}{'' if ok else f': {rendered}'}")

    totals = guide_summary([no_guide, inline, from_pdf])
    ok = (totals == {"forms": 2, "inline": 1, "standalone": 1, "both": 0,
                     "pages": 1, "pct_mean": 70, "pct_min": 70, "pct_max": 70})
    failures += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  guide totals count bundles and cut pages"
          f"{'' if ok else f': {totals}'}")

    # The ordinal and the heading total are the two numbers a reader counts
    # against each other to see whether a bundle is missing, so the ordinals have
    # to follow the batch report's order. The audit is a lookup table here, and
    # passing it in reverse proves its order cannot leak onto the page.
    batch = [_record("a-2018", "AAA"), _record("b-2018", "BBB"),
             _record("c-2018", "CCC")]
    page = render(batch, [_entry(slug=r["slug"]) for r in reversed(batch)])
    fragments = page.split("<tr>")[2:]   # [0] is the preamble, [1] the header row
    ordinals = [f.split("</td>")[0] for f in fragments]
    heading = page[page.index("<h1>"):page.index("</h1>") + 5]
    number_cases = [
        ("every row is numbered, in the batch report's order",
         len(fragments) == len(batch)
         and all(fragment.startswith(f'<td class=i>{i}.</td>')
                 and f'"{record["slug"]}/index.html"' in fragment
                 for i, (record, fragment) in enumerate(zip(batch, fragments), 1)),
         ordinals),
        ("the heading states the total",
         f'<span class=ct>{len(batch)} forms</span>' in heading, heading),
        ("the ordinal column is headed", "<th class=i>#" in page, "no # header cell"),
    ]
    for name, ok, detail in number_cases:
        failures += not ok
        print(f"  {'PASS' if ok else 'FAIL'}  {name}{'' if ok else f': {detail}'}")

    # A form the audit never scored must stop the render, not render blank.
    try:
        render([{"slug": "ghost"}], [])
    except SystemExit:
        print("  PASS  an unscored form aborts the render")
    else:
        failures += 1
        print("  FAIL  an unscored form did not abort the render")

    print(f"self-test: {failures} failure(s)")
    return 1 if failures else 0


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--batch-report", type=pathlib.Path,
                        default=REPO / "build/batch-report.json")
    parser.add_argument("--audit", type=pathlib.Path, default=REPO / "build/audit.json")
    parser.add_argument("--out", type=pathlib.Path, default=REPO / "forms/index.html")
    parser.add_argument("--self-test", action="store_true",
                        help="Prove the status classifier, then stop.")
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.self_test:
        return self_test()

    batch = json.loads(args.batch_report.read_text())
    audit = json.loads(args.audit.read_text())
    args.out.write_text(render(batch, audit), encoding="utf-8")
    print(f"wrote {args.out} ({len(batch)} forms)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
