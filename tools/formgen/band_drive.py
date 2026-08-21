#!/usr/bin/env python3
"""Drive every growable band in the corpus at 1, capacity and capacity+4 rows.

The band renderer is the one part of the emitted document that builds geometry
at run time instead of shipping it, so it is the one part a static audit cannot
score. Until now exactly one of the corpus's 39 bands had ever been driven, on
one form, at three row counts. Everything else was asserted only by the code
comment that says it works.

What this checks, and why each one is the interesting question:

  1 row          a band must SHRINK. A band whose rules are really a picture of
                 six rows looks perfect at capacity and wrong at one.
  capacity       re-rendering the official row count must reproduce, cell for
                 cell and rule for rule, the geometry the emitter had already
                 written into the static HTML. This is the load-bearing claim:
                 rows inside capacity come from their MEASURED row_y, not from
                 y0 + i*pitch. Several bands are regular but not uniform (the
                 2551Q's last row is 18.27pt against 18.24pt), so an arithmetic
                 renderer disagrees with the sheet by a hundredth of a point --
                 far below a device pixel, which is exactly why it would never
                 have been caught by looking.
  capacity+4     the sheet holds `capacity`. Overflow belongs on a continuation
                 page, so the renderer must REPORT it, not silently truncate to
                 capacity and not silently draw past the sheet. Both branches
                 are exercised: the default (report) and allowOverflow (draw).

Authored `style` strings are read rather than getBoundingClientRect, because
the deviations under test are sub-device-pixel and a snapped rect cannot see
them. The comparison is still DOM-observed: it is what the renderer wrote.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
from typing import Any

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parent.parent

# Two authored pt values are the same value when they agree to this. The static
# sheet's numbers are decimal literals and the renderer's are the result of a
# double subtraction, so a spanning rule's height comes back as 109.95000000000005
# against 109.95. That is IEEE-754 noise at 5e-14pt, twelve orders below the
# 0.05pt thickness tolerance; rounding here compares values, not bit patterns.
DECIMALS = 6
EPSILON_PT = 1e-6

# Rows to add past capacity when testing overflow.
OVERFLOW_ROWS = 4


def bundle_dir(forms_root: pathlib.Path, slug: str) -> pathlib.Path | None:
    for candidate in (forms_root / slug, forms_root / "extra" / slug):
        if (candidate / "index.html").is_file():
            return candidate
    return None


def bands_of(layout: dict[str, Any]) -> list[tuple[int, dict[str, Any]]]:
    return [(page["index"], band)
            for page in layout["pages"]
            for band in page.get("growable", [])]


# The page-side probe. Returned as authored numbers so the Python side can
# compare exactly; nothing here measures, it only reads back what was written.
PROBE = r"""
(bandId) => {
  const rules = document.getElementById("band-rules-" + bandId);
  const content = document.getElementById("band-content-" + bandId);
  if (!rules || !content) { return null; }
  const num = (el, prop) => {
    const v = el.style[prop];
    return v ? parseFloat(v) : null;
  };
  const rects = [];
  rules.querySelectorAll("rect,.r").forEach((el) => {
    rects.push(el.tagName === "rect"
      ? [parseFloat(el.getAttribute("x")), parseFloat(el.getAttribute("y")),
         parseFloat(el.getAttribute("width")), parseFloat(el.getAttribute("height"))]
      : [num(el, "left"), num(el, "top"), num(el, "width"), num(el, "height")]);
  });
  const cells = [];
  content.querySelectorAll("[data-cell-kind]").forEach((el) => {
    cells.push({ id: el.id, row: el.getAttribute("data-row"),
                 x: num(el, "left"), y: num(el, "top"),
                 w: num(el, "width"), h: num(el, "height"),
                 slots: el.querySelectorAll(".s").length,
                 inputs: el.querySelectorAll("input.fi").length });
  });
  const texts = [];
  content.querySelectorAll(".t").forEach((el) => {
    texts.push([el.id, el.textContent, num(el, "top")]);
  });
  return {
    rects: rects,
    cells: cells,
    texts: texts,
    rendered: content.getAttribute("data-rendered-rows"),
    overflow: content.getAttribute("data-overflow-rows"),
  };
}
"""


def rounded(values: Any) -> Any:
    return tuple(round(v, DECIMALS) if v is not None else None for v in values)


def row_tops(snapshot: dict[str, Any], pitch: float) -> list[float]:
    """Where each rendered row actually sits, top-first.

    Rows are clustered by authored top rather than read off `data-row`, because
    `data-row` is not a usable row identity here: rows inside capacity carry
    their lattice row index while every overflow row carries the *template
    row's* index, so four distinct overflow rows all answer the same number.
    Clustering asks the geometry instead, which is what is under test anyway.
    A cluster is narrower than half a pitch; cells inside one row differ by
    fractions of a point, consecutive rows by a whole pitch.
    """
    tops = sorted(round(c["y"], DECIMALS) for c in snapshot["cells"] if c["y"] is not None)
    if not tops:
        return []
    rows = [tops[0]]
    for value in tops[1:]:
        if value - rows[-1] > pitch / 2.0:
            rows.append(value)
    return rows


def duplicate_row_labels(snapshot: dict[str, Any], pitch: float) -> int:
    """How many rendered rows share a `data-row` value with an earlier row."""
    seen: dict[str, float] = {}
    collisions = 0
    for row_top in row_tops(snapshot, pitch):
        labels = {c["row"] for c in snapshot["cells"]
                  if c["y"] is not None and abs(round(c["y"], DECIMALS) - row_top) <= pitch / 2.0}
        for label in labels:
            if label in seen and seen[label] != row_top:
                collisions += 1
                break
            seen[label] = row_top
    return collisions


def horizontal_rules(snapshot: dict[str, Any]) -> int:
    """Rules wider than they are tall: the row separators, if the band owns any."""
    return sum(1 for x, y, w, h in snapshot["rects"] if w > h)


def no_separators(snapshot: dict[str, Any], failures: list[str], case: str) -> bool:
    """Report a band whose row separators are not its own.

    build_band_plan only claims rules lying inside the band's own x-extent. On
    a sheet whose row separators run the full page width -- wider than the band
    they divide -- none of them are claimed, so the band owns its column rules
    and nothing else. It still grows: the columns stretch and the cells repeat.
    What it cannot do is rule the rows it grew, because the lines between them
    were left in the static page layer at the sheet's original three or seven
    positions. Growing produces an unruled block below the last static line;
    shrinking leaves static lines ruling rows that are no longer there.
    """
    if horizontal_rules(snapshot):
        return True
    failures.append(
        f"{case}: the band owns {len(snapshot['rects'])} rules and every one of them is "
        f"a column rule -- it has no row separators of its own, so its rules cannot "
        f"scale with the row count. The separators are full-width page rules outside "
        f"the band's x-extent, left static at the sheet's original row positions")
    return False


def check_shrink(one: dict[str, Any], full: dict[str, Any], pitch: float,
                 capacity: int, failures: list[str]) -> bool:
    """One row must be strictly less document than `capacity` rows."""
    ok = no_separators(one, failures, "1 row")
    if int(one["rendered"]) != 1:
        failures.append(f"1 row: rendered {one['rendered']}, expected 1")
        ok = False
    if int(one["overflow"]) != 0:
        failures.append(f"1 row: overflow {one['overflow']}, expected 0")
        ok = False
    if capacity > 1:
        if ok and len(one["rects"]) >= len(full["rects"]):
            failures.append(f"1 row: {len(one['rects'])} rules, not fewer than "
                            f"{len(full['rects'])} at capacity {capacity}")
            ok = False
        if len(one["cells"]) >= len(full["cells"]):
            failures.append(f"1 row: {len(one['cells'])} cells, not fewer than "
                            f"{len(full['cells'])} at capacity {capacity}")
            ok = False
    rows = row_tops(one, pitch)
    if len(rows) != 1:
        failures.append(f"1 row: cells span {len(rows)} rendered rows, expected 1")
        ok = False
    return ok


def check_capacity(static: dict[str, Any], full: dict[str, Any],
                   band: dict[str, Any], failures: list[str]) -> bool:
    """Re-rendering at capacity must reproduce the shipped sheet exactly."""
    ok = True
    capacity = int(band["capacity"])
    if int(full["rendered"]) != capacity:
        failures.append(f"capacity: rendered {full['rendered']}, expected {capacity}")
        ok = False
    if int(full["overflow"]) != 0:
        failures.append(f"capacity: overflow {full['overflow']}, expected 0")
        ok = False

    static_rects = sorted(rounded(r) for r in static["rects"])
    full_rects = sorted(rounded(r) for r in full["rects"])
    if static_rects != full_rects:
        differing = [(a, b) for a, b in zip(static_rects, full_rects) if a != b]
        failures.append(f"capacity: rules differ from the static sheet "
                        f"({len(static_rects)} static vs {len(full_rects)} rendered, "
                        f"{len(differing)} differing"
                        + (f", e.g. {differing[0][0]} -> {differing[0][1]}"
                           if differing else "") + ")")
        ok = False

    static_cells = {c["id"]: rounded((c["x"], c["y"], c["w"], c["h"])) + (c["slots"], c["inputs"])
                    for c in static["cells"]}
    full_cells = {c["id"]: rounded((c["x"], c["y"], c["w"], c["h"])) + (c["slots"], c["inputs"])
                  for c in full["cells"]}
    if static_cells != full_cells:
        missing = sorted(set(static_cells) - set(full_cells))
        moved = sorted(k for k in set(static_cells) & set(full_cells)
                       if static_cells[k] != full_cells[k])
        failures.append(f"capacity: cells differ from the static sheet "
                        f"(missing {len(missing)}, changed {len(moved)}"
                        + (f", e.g. {moved[0]} {static_cells[moved[0]]} -> "
                           f"{full_cells[moved[0]]}" if moved else "")
                        + (f", e.g. missing {missing[0]}" if missing else "") + ")")
        ok = False

    static_texts = sorted((t[0], t[1], round(t[2], DECIMALS) if t[2] is not None else None)
                          for t in static["texts"])
    full_texts = sorted((t[0], t[1], round(t[2], DECIMALS) if t[2] is not None else None)
                        for t in full["texts"])
    if static_texts != full_texts:
        failures.append(f"capacity: text differs from the static sheet "
                        f"({len(static_texts)} static vs {len(full_texts)} rendered)")
        ok = False

    # The point of the whole exercise: measured, not arithmetic. Compare where
    # each row landed against row_y, and against what a y0+i*pitch renderer
    # would have produced. Only rows whose two predictions differ can decide it.
    row_y = [float(v) for v in band["row_y"]]
    pitch = float(band["row_pitch_pt"])
    tops = row_tops(full, pitch)
    static_tops = row_tops(static, pitch)
    if len(tops) != capacity:
        failures.append(f"capacity: cells span {len(tops)} rendered rows, expected {capacity}")
        return False
    if tops != static_tops:
        failures.append(f"capacity: row tops {tops} differ from the static sheet's {static_tops}")
        ok = False
    for index in range(capacity):
        arithmetic = row_y[0] + index * pitch
        if abs(row_y[index] - arithmetic) <= EPSILON_PT:
            continue
        # This row is where a measured renderer and an arithmetic one disagree,
        # so it is the only kind of row that can decide which one this is. The
        # rendered top must move with the measured edge, by the same constant
        # offset every row has between its top edge and its first cell.
        offset = tops[index] - row_y[index]
        base = tops[0] - row_y[0]
        if abs(offset - base) > EPSILON_PT:
            failures.append(
                f"capacity: row {index} at {tops[index]}pt tracks the nominal pitch "
                f"({round(arithmetic + base, 4)}pt) rather than the measured edge "
                f"({round(row_y[index] + base, 4)}pt)")
            ok = False
    return ok


def check_overflow(reported: dict[str, Any], drawn: dict[str, Any],
                   full: dict[str, Any], band: dict[str, Any],
                   failures: list[str]) -> bool:
    """capacity+4 must be reported, and drawable only when explicitly asked."""
    ok = True
    capacity = int(band["capacity"])
    want = capacity + OVERFLOW_ROWS

    if int(reported["rendered"]) != capacity:
        failures.append(f"overflow: drew {reported['rendered']} rows, the sheet holds {capacity}")
        ok = False
    if int(reported["overflow"]) != OVERFLOW_ROWS:
        failures.append(f"overflow: reported {reported['overflow']} overflow rows, "
                        f"expected {OVERFLOW_ROWS}")
        ok = False
    if len(reported["cells"]) != len(full["cells"]):
        failures.append(f"overflow: {len(reported['cells'])} cells, expected the "
                        f"capacity sheet's {len(full['cells'])}")
        ok = False

    if int(drawn["rendered"]) != want:
        failures.append(f"allowOverflow: rendered {drawn['rendered']}, expected {want}")
        ok = False
    if int(drawn["overflow"]) != 0:
        failures.append(f"allowOverflow: overflow {drawn['overflow']}, expected 0")
        ok = False
    if not no_separators(drawn, failures, "allowOverflow"):
        ok = False
    elif len(drawn["rects"]) <= len(full["rects"]):
        failures.append(f"allowOverflow: {len(drawn['rects'])} rules, not more than "
                        f"the capacity sheet's {len(full['rects'])}")
        ok = False
    if len(drawn["cells"]) <= len(full["cells"]):
        failures.append(f"allowOverflow: {len(drawn['cells'])} cells, not more than "
                        f"the capacity sheet's {len(full['cells'])}")
        ok = False

    # Rows past capacity have no measurement, so they are the one place the
    # nominal pitch is correct -- and it must be applied from the last measured
    # edge, not from y0.
    row_y = [float(v) for v in band["row_y"]]
    pitch = float(band["row_pitch_pt"])
    tops = row_tops(drawn, pitch)
    if len(tops) == want:
        base = tops[0] - row_y[0]
        for step in range(OVERFLOW_ROWS):
            index = capacity + step
            expected = row_y[-1] + (index - (len(row_y) - 1)) * pitch + base
            if abs(tops[index] - expected) > 0.005:
                failures.append(f"allowOverflow: row {index} at {tops[index]}pt, "
                                f"expected {round(expected, 4)}pt "
                                f"(last measured edge + {step + 1} pitches)")
                ok = False
                break
    else:
        failures.append(f"allowOverflow: cells span {len(tops)} rendered rows, expected {want}")
        ok = False
    return ok


def drive(page: Any, slug: str, page_index: int, band: dict[str, Any]) -> dict[str, Any]:
    band_id = band["id"]
    capacity = int(band["capacity"])
    failures: list[str] = []

    static = page.evaluate(PROBE, band_id)
    if static is None:
        return {"slug": slug, "page": page_index, "band": band_id, "capacity": capacity,
                "one": False, "at_capacity": False, "overflow": False,
                "failures": [f"no band containers in the document for {band_id}"]}

    results = {}
    for name, rows, options in (("one", 1, None),
                                ("full", capacity, None),
                                ("reported", capacity + OVERFLOW_ROWS, None),
                                ("drawn", capacity + OVERFLOW_ROWS, {"allowOverflow": True})):
        outcome = page.evaluate(
            "([id, rows, options]) => window.formgen.setBandRows(id, rows, options)",
            [band_id, rows, options])
        results[name] = page.evaluate(PROBE, band_id)
        results[name + "_return"] = outcome

    pitch = float(band["row_pitch_pt"])
    one_ok = check_shrink(results["one"], results["full"], pitch, capacity, failures)
    capacity_ok = check_capacity(static, results["full"], band, failures)
    overflow_ok = check_overflow(results["reported"], results["drawn"],
                                 results["full"], band, failures)

    # Reported, not failed: the geometry of an overflow row is right, but its
    # `data-row` is the template row's, so the DOM cannot say which sheet row
    # an overflow cell is. Filling addresses cells by id, which stays unique.
    notes = []
    collisions = duplicate_row_labels(results["drawn"], pitch)
    if collisions:
        notes.append(f"{collisions} overflow row(s) repeat an earlier row's data-row value")

    return {"slug": slug, "page": page_index, "band": band_id, "capacity": capacity,
            "notes": notes,
            "columns": len(band["column_x"]),
            "pitch_pt": band["row_pitch_pt"],
            "irregular": band["row_pitch_min_pt"] != band["row_pitch_max_pt"],
            "static_rules": len(static["rects"]), "static_cells": len(static["cells"]),
            "one_rules": len(results["one"]["rects"]), "one_cells": len(results["one"]["cells"]),
            "drawn_rules": len(results["drawn"]["rects"]),
            "drawn_cells": len(results["drawn"]["cells"]),
            "overflow_reported": results["reported_return"]["overflow"],
            "one": one_ok, "at_capacity": capacity_ok, "overflow": overflow_ok,
            "failures": failures}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--forms", type=pathlib.Path, default=REPO / "forms")
    parser.add_argument("--layout-dir", type=pathlib.Path, default=REPO / "build/layout")
    parser.add_argument("--only", action="append", default=None)
    parser.add_argument("--json", type=pathlib.Path, default=None)
    args = parser.parse_args(argv)

    from playwright.sync_api import sync_playwright

    work = []
    for path in sorted(args.layout_dir.glob("*.layout.json")):
        slug = path.name[:-len(".layout.json")]
        if args.only and slug not in args.only:
            continue
        layout = json.loads(path.read_text(encoding="utf-8"))
        bands = bands_of(layout)
        if bands:
            work.append((slug, bands))

    total = sum(len(b) for _, b in work)
    print(f"{total} growable bands across {len(work)} forms", file=sys.stderr)

    records: list[dict[str, Any]] = []
    with sync_playwright() as engine:
        browser = engine.chromium.launch()
        context = browser.new_context()
        for slug, bands in work:
            bundle = bundle_dir(args.forms, slug)
            if bundle is None:
                for page_index, band in bands:
                    records.append({"slug": slug, "page": page_index, "band": band["id"],
                                    "capacity": band["capacity"], "one": False,
                                    "at_capacity": False, "overflow": False,
                                    "failures": ["no generated bundle"]})
                continue
            tab = context.new_page()
            errors: list[str] = []
            tab.on("pageerror", lambda exc: errors.append(str(exc)))
            tab.goto((bundle / "index.html").resolve().as_uri())
            for page_index, band in bands:
                record = drive(tab, slug, page_index, band)
                if errors:
                    record["failures"] = record["failures"] + [f"page error: {errors[0]}"]
                    record["one"] = record["at_capacity"] = record["overflow"] = False
                records.append(record)
                mark = "".join("." if record[k] else "X"
                               for k in ("one", "at_capacity", "overflow"))
                print(f"  {slug:<26} p{page_index} {record['band']:<6} "
                      f"cap {record['capacity']:>2}  {mark}", file=sys.stderr)
                for failure in record["failures"]:
                    print(f"      FAIL {failure}", file=sys.stderr)
                for note in record.get("notes") or ():
                    print(f"      note {note}", file=sys.stderr)
            tab.close()
        browser.close()

    clean = sum(1 for r in records if r["one"] and r["at_capacity"] and r["overflow"])
    print(f"\n{clean}/{len(records)} bands passed all three cases", file=sys.stderr)
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(records, indent=2) + "\n", encoding="utf-8")
    return 0 if clean == len(records) else 1


if __name__ == "__main__":
    raise SystemExit(main())
