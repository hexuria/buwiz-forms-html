#!/usr/bin/env python3
"""Stamp frozen 2551Q input name= from catalog keys that exist in fields.json.

Fail-closed. The only joins that may write name="frm2551Qv2018:…" are catalog
records for 2551q-2018 whose official_field_key is a non-empty harvest key
present in rules/forms/2551q-v2018/fields.json. This never invents a key,
never copies leftover uniqueness, never writes forms/ or forms-corrected/,
and never commits saveXML.

id= and data-field-name stay as cell ids. Remaining writer keys without a
1:1 catalog join stay on name=<cell id> and are listed in name-gaps.json.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import join_census as jc  # noqa: E402
from map_tin import NAME_ATTR_RE, rewrite_input_names  # noqa: E402

REPO = HERE.parent.parent
BUNDLE_SLUG = "2551q-2018"
FORM_ID = "2551q-v2018"
WRITER_PREFIX = "frm2551Qv2018:"
# Writer emits collapsed payment blanks txt25-28. The inventory names the
# four payment columns separately (txtAgency25, txtAmount25, …). Do not
# invent a join onto those cells.
WRITER_FRM_KEYS_ABSENT_FROM_FIELDS = (
    "frm2551Qv2018:txt25",
    "frm2551Qv2018:txt26",
    "frm2551Qv2018:txt27",
    "frm2551Qv2018:txt28",
)


class StampError(Exception):
    def __init__(self, detail: str, errors: list[str] | None = None) -> None:
        super().__init__(detail)
        self.detail = detail
        self.errors = list(errors) if errors is not None else [detail]


def fields_serialized_keys(fields_path: pathlib.Path) -> set[str]:
    payload = json.loads(fields_path.read_text(encoding="utf-8"))
    return {
        row["serialized_key"]
        for row in payload.get("fields", [])
        if row.get("serialized_key")
    }


def catalog_2551q_joins(catalog: dict) -> list[dict[str, str]]:
    joins: list[dict[str, str]] = []
    seen_keys: dict[str, str] = {}
    seen_cells: dict[str, str] = {}
    for record in catalog.get("records", []):
        if record.get("bundle_slug") != BUNDLE_SLUG:
            continue
        key = jc.claimed_key(record)
        if key is None:
            continue
        ident = str(record.get("id"))
        cell = str(record.get("html_id_hint") or "")
        if not cell:
            raise StampError(f"{ident}: catalog html_id_hint is empty")
        if key in seen_keys:
            raise StampError(
                f"{ident}: official_field_key {key!r} already owned by {seen_keys[key]}"
            )
        if cell in seen_cells:
            raise StampError(
                f"{ident}: cell {cell} already claimed by {seen_cells[cell]}"
            )
        seen_keys[key] = ident
        seen_cells[cell] = ident
        joins.append(
            {
                "id": ident,
                "html_id": cell,
                "official_field_key": key,
            }
        )
    return joins


def stamp_html(html: str, joins: list[dict[str, str]], allowed_keys: set[str]) -> tuple[str, list[dict[str, object]]]:
    errors: list[str] = []
    stamped: list[dict[str, object]] = []
    for item in joins:
        key = item["official_field_key"]
        cell = item["html_id"]
        if key not in allowed_keys:
            errors.append(
                f"{item['id']}: official_field_key {key!r} is not in fields.json"
            )
            continue
        if not key.startswith(WRITER_PREFIX):
            errors.append(
                f"{item['id']}: official_field_key {key!r} is not a 2551Q writer key"
            )
            continue
        html, stats = rewrite_input_names(html, cell, key)
        if stats["rewritten"] == 0 and stats["already"] == 0:
            errors.append(f"{item['id']}: no input name={cell!r} (or already {key!r}) in HTML")
            continue
        stamped.append(
            {
                "id": item["id"],
                "html_id": cell,
                "name": key,
                "rewritten": stats["rewritten"],
                "already": stats["already"],
            }
        )
    if errors:
        raise StampError("refused 2551Q name stamp", errors)
    return html, stamped


def html_frm_names(html: str) -> set[str]:
    names = set()
    for match in NAME_ATTR_RE.finditer(html):
        value = match.group(1)
        if value.startswith(WRITER_PREFIX):
            names.add(value)
    return names


def writer_frm_keys_from_fields(allowed_keys: set[str]) -> set[str]:
    return {key for key in allowed_keys if key.startswith(WRITER_PREFIX)}


def check_frozen(
    html_path: pathlib.Path,
    fields_path: pathlib.Path,
    catalog: dict,
    gaps_path: pathlib.Path | None = None,
) -> dict[str, object]:
    allowed = fields_serialized_keys(fields_path)
    joins = catalog_2551q_joins(catalog)
    html = html_path.read_text(encoding="utf-8")
    stamped_names = {item["official_field_key"] for item in joins}
    present = html_frm_names(html)
    missing = sorted(stamped_names - present)
    extra = sorted(present - allowed)
    if missing:
        raise StampError("frozen HTML is missing stamped names", missing)
    if extra:
        raise StampError("frozen HTML name= not in fields.json", extra)
    if present != stamped_names:
        raise StampError(
            "frozen HTML frm names must equal the catalog 1:1 joins",
            sorted(present.symmetric_difference(stamped_names)),
        )
    report = {
        "stamped": sorted(stamped_names),
        "html_path": str(html_path),
    }
    if gaps_path is not None and gaps_path.is_file():
        gaps = json.loads(gaps_path.read_text(encoding="utf-8"))
        listed = set(gaps.get("stamped_names") or [])
        if listed != stamped_names:
            raise StampError("name-gaps.json stamped_names drifted", sorted(listed.symmetric_difference(stamped_names)))
        absent = tuple(gaps.get("writer_frm_keys_absent_from_fields_json") or [])
        if absent != WRITER_FRM_KEYS_ABSENT_FROM_FIELDS:
            raise StampError("pinned writer/fields gap drifted", list(absent))
        report["gaps_path"] = str(gaps_path)
    return report


def write_gaps(
    path: pathlib.Path,
    joins: list[dict[str, str]],
    allowed_keys: set[str],
) -> None:
    stamped = [item["official_field_key"] for item in joins]
    writer_in_fields = sorted(writer_frm_keys_from_fields(allowed_keys))
    unstamped = [key for key in writer_in_fields if key not in set(stamped)]
    payload = {
        "form_id": FORM_ID,
        "html_slug": BUNDLE_SLUG,
        "rule": (
            "Stamp name= only where the identity catalog has a 1:1 "
            "official_field_key that exists in fields.json. Remaining writer "
            "keys stay on cell-id name=. Do not invent a join."
        ),
        "stamped": [
            {
                "catalog_id": item["id"],
                "html_id": item["html_id"],
                "name": item["official_field_key"],
            }
            for item in joins
        ],
        "stamped_names": stamped,
        "unstamped_writer_keys_in_fields_json": unstamped,
        "writer_frm_keys_absent_from_fields_json": list(WRITER_FRM_KEYS_ABSENT_FROM_FIELDS),
        "writer_frm_keys_absent_from_fields_json_reason": (
            "form_2551q_xml.rs emits collapsed frm2551Qv2018:txt25..txt28 "
            "blanks; fields.json inventories the payment columns as "
            "txtAgency/txtAmount/txtDate/txtNumber instead. The writer is "
            "not reminted in this freeze."
        ),
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")


def stamp_tree(
    html_path: pathlib.Path,
    fields_path: pathlib.Path,
    catalog: dict,
    *,
    write: bool,
) -> dict[str, object]:
    if html_path.resolve().parts[-2:] != (BUNDLE_SLUG, "index.html"):
        raise StampError(f"refused to stamp {html_path}; expected html-frozen/{BUNDLE_SLUG}/index.html")
    if "forms-corrected" in html_path.resolve().parts or html_path.resolve().parts[-3:-1] == ("forms", BUNDLE_SLUG):
        raise StampError(f"refused to stamp generator tree {html_path}")
    allowed = fields_serialized_keys(fields_path)
    joins = catalog_2551q_joins(catalog)
    html = html_path.read_text(encoding="utf-8")
    html, stamped = stamp_html(html, joins, allowed)
    gaps_path = html_path.parent / "name-gaps.json"
    if write:
        html_path.write_text(html, encoding="utf-8")
        write_gaps(gaps_path, joins, allowed)
        inventory_path = html_path.parent.parent / "inventory.json"
        if inventory_path.is_file():
            inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
            digest = hashlib.sha256(html.encode("utf-8")).hexdigest()
            for bundle in inventory.get("bundles", []):
                if bundle.get("form_id") == FORM_ID:
                    bundle["index_sha256"] = digest
                    bundle["name_stamped"] = True
            inventory_path.write_text(
                json.dumps(inventory, indent=2, ensure_ascii=True) + "\n",
                encoding="utf-8",
            )
    return {
        "stamped": stamped,
        "wrote": write,
        "gaps_path": str(gaps_path),
    }


def self_test() -> None:
    html = (
        '<div data-field-name="p1c20">'
        '<input id="p1c20-s0" name="p1c20" data-slot-index="0" maxlength="1">'
        '<input id="p1c20-s1" name="p1c20" data-slot-index="1" maxlength="1">'
        "</div>"
        '<input id="p1c99" name="p1c99">'
    )
    allowed = {"frm2551Qv2018:txtTIN1"}
    joins = [
        {
            "id": "2551q-2018/p1/tin-1",
            "html_id": "p1c20",
            "official_field_key": "frm2551Qv2018:txtTIN1",
        }
    ]
    out, stamped = stamp_html(html, joins, allowed)
    assert stamped[0]["rewritten"] == 2
    assert 'name="frm2551Qv2018:txtTIN1"' in out
    assert 'id="p1c20-s0"' in out
    assert 'data-field-name="p1c20"' in out
    assert 'name="p1c99"' in out

    try:
        stamp_html(
            html,
            [
                {
                    "id": "2551q-2018/p1/invented",
                    "html_id": "p1c99",
                    "official_field_key": "frm2551Qv2018:invented",
                }
            ],
            allowed,
        )
    except StampError as error:
        assert "not in fields.json" in error.errors[0]
    else:
        raise AssertionError("invented key must be refused")

    catalog = {
        "records": [
            {
                "id": "2551q-2018/p1/tin-1",
                "bundle_slug": BUNDLE_SLUG,
                "official_field_key": "frm2551Qv2018:txtTIN1",
                "html_id_hint": "p1c20",
            },
            {
                "id": "2551q-2018/p1/other",
                "bundle_slug": BUNDLE_SLUG,
                "official_field_key": None,
                "official_field_key_gap": "no-unique-leftover",
                "html_id_hint": "p1c99",
            },
        ]
    }
    joins = catalog_2551q_joins(catalog)
    assert [item["html_id"] for item in joins] == ["p1c20"]

    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp) / "html-frozen" / BUNDLE_SLUG
        root.mkdir(parents=True)
        target = root / "index.html"
        target.write_text(html, encoding="utf-8")
        fields = pathlib.Path(tmp) / "fields.json"
        fields.write_text(
            json.dumps(
                {
                    "fields": [
                        {"serialized_key": "frm2551Qv2018:txtTIN1"},
                        {"serialized_key": "frm2551Qv2018:txtYear"},
                    ]
                }
            ),
            encoding="utf-8",
        )
        result = stamp_tree(target, fields, catalog, write=True)
        assert result["wrote"] is True
        stamped_html = target.read_text(encoding="utf-8")
        check_frozen(target, fields, catalog, root / "name-gaps.json")
        assert html_frm_names(stamped_html) == {"frm2551Qv2018:txtTIN1"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--html",
        type=pathlib.Path,
        default=REPO / "html-frozen" / BUNDLE_SLUG / "index.html",
    )
    parser.add_argument(
        "--fields",
        type=pathlib.Path,
        default=REPO / "rules" / "forms" / FORM_ID / "fields.json",
    )
    parser.add_argument(
        "--catalog",
        type=pathlib.Path,
        default=REPO / "tools" / "formgen" / "identity" / "catalog.json",
    )
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        print("OK    self-test")
        return 0
    catalog = json.loads(args.catalog.read_text(encoding="utf-8"))
    try:
        joins = catalog_2551q_joins(catalog)
        live_html = (REPO / "html-frozen" / BUNDLE_SLUG / "index.html").resolve()
        if args.html.resolve() == live_html and len(joins) != 4:
            raise StampError(
                f"expected 4 catalog TIN joins for {BUNDLE_SLUG}, got {len(joins)}",
                [item["id"] for item in joins],
            )
        if args.check:
            report = check_frozen(
                args.html,
                args.fields,
                catalog,
                args.html.parent / "name-gaps.json",
            )
            print(f"OK    check stamped={len(report['stamped'])}")
            return 0
        result = stamp_tree(args.html, args.fields, catalog, write=args.write)
        print(f"OK    stamped {len(result['stamped'])} joins write={args.write}")
        for item in result["stamped"]:
            print(
                f"      {item['html_id']} -> {item['name']} "
                f"rewritten={item['rewritten']} already={item['already']}"
            )
        return 0
    except StampError as error:
        print(f"FAIL  {error.detail}", file=sys.stderr)
        for item in error.errors:
            print(f"      {item}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
