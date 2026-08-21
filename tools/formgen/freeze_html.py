#!/usr/bin/env python3
"""Snapshot the 43 inventory HTML bundles into html-frozen/.

Source is the last Stage 2 tree (forms-corrected/), not forms/. Later layout
edits belong in html-frozen/ as direct HTML commits.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import re
import shutil
import sys

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parent.parent
ASSET_RE = re.compile(r"\.\./(?:\.\./)?assets/([0-9a-f]{64}\.png)")

# rules/index.json form_id -> forms-corrected slug (census-table inventory column).
# 2000-v2018 has no census inventory row; the DST sheet is the in-tree artwork.
INVENTORY_SLUGS: dict[str, str] = {
    "0605-v2003": "0605-1999",
    "0619e-v2018": "0619e-2018",
    "0619f-v2018": "0619f-2018",
    "1600pt-v2018": "1600-pt-2018",
    "1600vt-v2018": "1600-vt-2018",
    "1601c-v2018": "1601c-2018",
    "1601eq-v2018": "1601eq-2019",
    "1601fq-v2018": "1601-fq-2020",
    "1602q-v2018": "1602q-2019",
    "1603q-v2018": "1603q-2018",
    "1604c-v2018": "1604c-2018",
    "1604e-v2018": "extra/1604e-2018",
    "1604f-v2018": "1604f-2018",
    "1606-v2018": "1606-2018",
    "1600wp-v2010": "extra/1600wp-2010",
    "1700-v2013": "extra/1700-2018",
    "1701-v2018": "1701-2018",
    "1701a-v2018": "1701a-2018",
    "1701ms-v2024": "1701ms-2024",
    "1701q-v2018": "1701q-2018",
    "1702ex-v2018c": "1702ex-2018",
    "1702mx-v2018c": "1702mx-2018c",
    "1702q-v2018c": "1702q-2018",
    "1702rt-v2018c": "1702rt-2018c",
    "1706-v2018": "extra/1706-2018",
    "1707-v2021": "extra/1707-2021",
    "1707a-v2021": "1707a-2021",
    "1800-v2018": "extra/1800-2018",
    "1801-v2018": "extra/1801-2018",
    "2000-v2018": "2000-dst-2018",
    "2000ot-v2018": "2000-ot-2018",
    "2200a-v2020": "extra/2200a-2020",
    "2200an-v2018": "extra/2200an-2018",
    "2200c-v2018": "2200c-2018",
    "2200m-v2018": "2200m-2018",
    "2200p-v2020": "extra/2200p-2020",
    "2200s-v2018": "extra/2200s-2018",
    "2200t-v2020": "extra/2200t-2022",
    "2550m-v2007": "2550m-2007",
    "2550q-v2024": "2550q-2024",
    "2551q-v2018": "2551q-2018",
    "2552-v2018": "extra/2552-2018",
    "2553-v1999": "extra/2553-1999",
}

COPY_NAMES = ("index.html", "form.css", "guide.css", "guide.html", "provenance.json")
TEXT_SUFFIXES = {".html", ".css", ".json", ".md"}


def sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def flatten_extra_paths(text: str) -> str:
    """Rewrite extra/ ../../ relatives so a flattened bundle still finds shared files."""
    text = text.replace("../../assets/", "../assets/")
    text = text.replace("../../fonts/", "../fonts/")
    text = text.replace("../../base.css", "../base.css")
    return text


def copy_bundle(src: pathlib.Path, dest: pathlib.Path, *, from_extra: bool) -> tuple[list[str], list[str]]:
    dest.mkdir(parents=True, exist_ok=True)
    copied: list[str] = []
    for name in COPY_NAMES:
        item = src / name
        if not item.is_file():
            continue
        target = dest / name
        if from_extra and item.suffix.lower() in TEXT_SUFFIXES:
            text = flatten_extra_paths(item.read_text(encoding="utf-8"))
            target.write_text(text, encoding="utf-8")
        else:
            shutil.copy2(item, target)
        copied.append(name)
    html = dest / "index.html"
    assets: list[str] = []
    if html.is_file():
        assets = sorted(set(ASSET_RE.findall(html.read_text(encoding="utf-8"))))
    return copied, assets


def load_form_ids(index_path: pathlib.Path) -> list[str]:
    index = json.loads(index_path.read_text(encoding="utf-8"))
    form_ids = [row["form_id"] for row in index["forms"]]
    if len(form_ids) != 43:
        raise SystemExit(f"error: expected 43 inventory forms, got {len(form_ids)}")
    missing_map = [fid for fid in form_ids if fid not in INVENTORY_SLUGS]
    extra_map = [fid for fid in INVENTORY_SLUGS if fid not in form_ids]
    if missing_map or extra_map:
        raise SystemExit(
            f"error: slug map mismatch missing={missing_map} extra={extra_map}"
        )
    return form_ids


def verify(out: pathlib.Path, index_path: pathlib.Path) -> int:
    """Check the committed freeze without regenerating from forms-corrected/."""
    form_ids = load_form_ids(index_path)
    inventory_path = out / "inventory.json"
    if not inventory_path.is_file():
        print(f"error: missing {inventory_path}", file=sys.stderr)
        return 1
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    bundles = inventory.get("bundles") or []
    if inventory.get("form_count") != 43 or len(bundles) != 43:
        print(
            f"error: inventory form_count={inventory.get('form_count')} "
            f"bundles={len(bundles)}",
            file=sys.stderr,
        )
        return 1
    listed = [row["form_id"] for row in bundles]
    if listed != form_ids:
        print("error: inventory.json form_id order does not match rules/index.json", file=sys.stderr)
        return 1
    missing = []
    for row in bundles:
        slug = row["frozen_slug"]
        html = out / slug / "index.html"
        keys = out / "keys" / f"{row['form_id']}.json"
        if not html.is_file():
            missing.append(str(html))
        if not keys.is_file():
            missing.append(str(keys))
    for required in ("README.md", "base.css"):
        if not (out / required).is_file():
            missing.append(str(out / required))
    fonts = out / "fonts"
    if not fonts.is_dir() or not any(fonts.iterdir()):
        missing.append(str(fonts))
    if missing:
        print("error: freeze incomplete:\n  " + "\n  ".join(missing), file=sys.stderr)
        return 1
    print(f"OK    verify {len(bundles)} bundles at {out}")
    return 0


def freeze(source: pathlib.Path, out: pathlib.Path, index_path: pathlib.Path) -> int:
    form_ids = load_form_ids(index_path)
    if not source.is_dir():
        print(f"error: missing source tree {source}", file=sys.stderr)
        return 1

    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    (out / "keys").mkdir()
    (out / "assets").mkdir()

    fonts_src = source / "fonts"
    if fonts_src.is_dir():
        shutil.copytree(fonts_src, out / "fonts")
    base_css = source / "base.css"
    if base_css.is_file():
        shutil.copy2(base_css, out / "base.css")

    manifest = {
        "schema": "html-frozen-v1",
        "freeze_tag": "v1-frozen",
        "source_tree": str(source.relative_to(REPO)) if source.is_relative_to(REPO) else str(source),
        "form_count": 43,
        "note": (
            "hexuria/buwiz-forms-html could not be created from this agent "
            "(GitHub createRepository denied). This tree is the product HTML freeze."
        ),
        "bundles": [],
    }
    needed_assets: set[str] = set()
    for form_id in form_ids:
        slug = INVENTORY_SLUGS[form_id]
        src = source / slug
        html = src / "index.html"
        if not html.is_file():
            print(f"error: missing {html}", file=sys.stderr)
            return 1
        from_extra = slug.startswith("extra/")
        dest_slug = slug.split("/", 1)[-1] if from_extra else slug
        dest = out / dest_slug
        copied, assets = copy_bundle(src, dest, from_extra=from_extra)
        needed_assets.update(assets)
        keys_src = REPO / "rules" / "forms" / form_id / "fields.json"
        key_list = []
        if keys_src.is_file():
            fields = json.loads(keys_src.read_text(encoding="utf-8"))
            key_list = [
                row["serialized_key"]
                for row in fields.get("fields", [])
                if row.get("serialized_key")
            ]
            (out / "keys" / f"{form_id}.json").write_text(
                json.dumps(
                    {
                        "form_id": form_id,
                        "html_slug": dest_slug,
                        "serialized_keys": key_list,
                    },
                    indent=2,
                    ensure_ascii=True,
                )
                + "\n",
                encoding="utf-8",
            )
        manifest["bundles"].append(
            {
                "form_id": form_id,
                "source_slug": slug,
                "frozen_slug": dest_slug,
                "files": copied,
                "serialized_key_count": len(key_list),
                "index_sha256": sha256_file(dest / "index.html"),
            }
        )

    asset_root = source / "assets"
    copied_assets = 0
    for name in sorted(needed_assets):
        src_asset = asset_root / name
        if src_asset.is_file():
            shutil.copy2(src_asset, out / "assets" / name)
            copied_assets += 1
        else:
            print(f"error: missing asset {src_asset}", file=sys.stderr)
            return 1

    (out / "README.md").write_text(
        "# Frozen BIR HTML\n\n"
        "Product fill/print sheets for the 43 inventory forms. No extractor, "
        "no gate.py, no generator history.\n\n"
        "Layout edits are direct HTML commits here. XML `name=` stamps are "
        "fail-closed joins from `rules/forms/*/fields.json` and the identity "
        "catalog; never invent a key.\n\n"
        "Intended home is `hexuria/buwiz-forms-html` (tag `v1-frozen`). This "
        "directory is the in-repo freeze until that repository exists.\n",
        encoding="utf-8",
    )
    manifest["assets_copied"] = copied_assets
    (out / "inventory.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )
    print(f"OK    bundles {len(manifest['bundles'])}")
    print(f"OK    assets {copied_assets}")
    print(f"OK    out {out}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=pathlib.Path, default=REPO / "forms-corrected")
    parser.add_argument("--out", type=pathlib.Path, default=REPO / "html-frozen")
    parser.add_argument("--index", type=pathlib.Path, default=REPO / "rules" / "index.json")
    parser.add_argument(
        "--verify",
        action="store_true",
        help="Check a committed html-frozen/ tree; do not regenerate.",
    )
    args = parser.parse_args()
    if args.verify:
        return verify(args.out, args.index)
    return freeze(args.source, args.out, args.index)


if __name__ == "__main__":
    raise SystemExit(main())
