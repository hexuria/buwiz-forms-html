#!/usr/bin/env python3
"""The product app's frozen sheets are this repository's, byte for byte.

hexuria/buwiz-forms keeps a copy of html-frozen/ for fill and print and adds
its own metadata to some sheets: `<script type="application/json"
id="formgen-…">` blocks (bands, page layout) that its print engine reads.
Apart from those blocks, each product sheet must equal this repository's
html-frozen/<slug>/index.html exactly, so a re-sync in either direction can
neither undo a correction nor bring in a change the other side never had (a
name stamp, a moved cell).

    python3 tools/formgen/check_product_sync.py --out html-frozen \\
        --product /path/to/buwiz-forms/html-frozen
    python3 tools/formgen/check_product_sync.py --self-test
"""

from __future__ import annotations

import argparse
import difflib
import pathlib
import re
import sys

FORMGEN_SCRIPT = re.compile(
    r'<script type="application/json" id="formgen-[^"]*">.*?</script>\n?', re.S
)


def without_formgen_scripts(html: str) -> str:
    return FORMGEN_SCRIPT.sub("", html)


def first_difference(ours: str, theirs: str) -> str:
    a = ours.replace("><", ">\n<").splitlines()
    b = theirs.replace("><", ">\n<").splitlines()
    lines = [
        line
        for line in difflib.unified_diff(b, a, "product", "html-frozen", n=0, lineterm="")
        if line[:1] in "+-" and not line.startswith(("+++", "---"))
    ]
    return f"{len(lines)} differing lines, first: {lines[0][:160] if lines else ''}"


def compare(out: pathlib.Path, product: pathlib.Path) -> list[str]:
    problems = []
    slugs = sorted(p.parent.name for p in out.glob("*/index.html"))
    product_slugs = sorted(p.parent.name for p in product.glob("*/index.html"))
    for slug in sorted(set(product_slugs) - set(slugs)):
        problems.append(f"{slug}: in the product only")
    for slug in slugs:
        theirs_path = product / slug / "index.html"
        if not theirs_path.is_file():
            problems.append(f"{slug}: missing from the product")
            continue
        ours = without_formgen_scripts((out / slug / "index.html").read_text(encoding="utf-8"))
        theirs = without_formgen_scripts(theirs_path.read_text(encoding="utf-8"))
        if ours != theirs:
            problems.append(f"{slug}: {first_difference(ours, theirs)}")
    return problems


def self_test() -> int:
    sheet = '<div id="p1c13"><input name="p1c13"></div>\n'
    script = '<script type="application/json" id="formgen-bands">[1]</script>\n'
    assert without_formgen_scripts(sheet + script) == sheet
    assert without_formgen_scripts(sheet) == sheet
    stamped = sheet.replace('name="p1c13"', 'name="frm1800v2018:txtTIN1"')
    assert without_formgen_scripts(stamped) != without_formgen_scripts(sheet + script)
    print("OK    self-test")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=pathlib.Path, default=pathlib.Path("html-frozen"))
    parser.add_argument("--product", type=pathlib.Path, help="the product's html-frozen/")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        return self_test()
    if args.product is None:
        parser.error("--product is required")
    problems = compare(args.out, args.product)
    if problems:
        print("error: frozen sheets differ from the product's copies (formgen-* scripts aside):", file=sys.stderr)
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        return 1
    count = len(list(args.out.glob("*/index.html")))
    print(f"OK    {count} sheets equal the product's (formgen-* scripts aside)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
