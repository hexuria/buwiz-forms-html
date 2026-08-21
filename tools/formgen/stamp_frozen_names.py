#!/usr/bin/env python3
"""Stamp frozen HTML input name= from catalog keys that exist in fields.json.

Fail-closed. The only joins that may write name="frm…" are catalog records
for the given bundle_slug whose official_field_key is a non-empty harvest
key present in that form's fields.json. This never invents a key, never
copies leftover uniqueness, never writes forms/ or forms-corrected/, and
never commits saveXML.

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
from map_tin import NAME_ATTR_RE, _is_unwritable_mixed, rewrite_input_names  # noqa: E402

REPO = HERE.parent.parent

# (html-frozen slug, rules/forms/<id>)
STAMPED_BUNDLES: tuple[tuple[str, str], ...] = (
    ("2551q-2018", "2551q-v2018"),
    ("0619e-2018", "0619e-v2018"),
    ("0619f-2018", "0619f-v2018"),
    ("0605-1999", "0605-v2003"),
    ("1601c-2018", "1601c-v2018"),
    ("1701q-2018", "1701q-v2018"),
    ("2550q-2024", "2550q-v2024"),
    ("1701-2018", "1701-v2018"),
    ("1702rt-2018c", "1702rt-v2018c"),
    ("1702mx-2018c", "1702mx-v2018c"),
)

# Writer emits collapsed payment blanks txt25-28. The inventory names the
# four payment columns separately. Do not invent a join onto those cells.
WRITER_FRM_KEYS_ABSENT_FROM_FIELDS: dict[str, tuple[str, ...]] = {
    "2551q-v2018": (
        "frm2551Qv2018:txt25",
        "frm2551Qv2018:txt26",
        "frm2551Qv2018:txt27",
        "frm2551Qv2018:txt28",
    ),
}

WRITER_ABSENT_REASON: dict[str, str] = {
    "2551q-v2018": (
        "form_2551q_xml.rs emits collapsed frm2551Qv2018:txt25..txt28 "
        "blanks; fields.json inventories the payment columns as "
        "txtAgency/txtAmount/txtDate/txtNumber instead. The writer is "
        "not reminted in this freeze."
    ),
}


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


def writer_prefix_from_keys(keys: list[str]) -> str:
    if not keys:
        raise StampError("no catalog joins to derive a writer prefix")
    prefixes = set()
    for key in keys:
        colon = key.find(":")
        if colon < 1:
            raise StampError(f"official_field_key {key!r} has no frm prefix")
        prefixes.add(key[: colon + 1])
    if len(prefixes) != 1:
        raise StampError(
            "catalog joins do not share one writer prefix",
            sorted(prefixes),
        )
    return next(iter(prefixes))


def catalog_joins(catalog: dict, bundle_slug: str) -> list[dict[str, str]]:
    joins: list[dict[str, str]] = []
    seen_keys: dict[str, str] = {}
    seen_cells: dict[str, str] = {}
    for record in catalog.get("records", []):
        if record.get("bundle_slug") != bundle_slug:
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


def writable_joins(
    html: str, joins: list[dict[str, str]]
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    writable: list[dict[str, str]] = []
    unwritable: list[dict[str, str]] = []
    for item in joins:
        if _is_unwritable_mixed(html, item["html_id"]):
            unwritable.append(item)
        else:
            writable.append(item)
    return writable, unwritable


def stamp_html(
    html: str,
    joins: list[dict[str, str]],
    allowed_keys: set[str],
    writer_prefix: str,
) -> tuple[str, list[dict[str, object]], list[dict[str, str]]]:
    writable, unwritable = writable_joins(html, joins)
    errors: list[str] = []
    stamped: list[dict[str, object]] = []
    for item in writable:
        key = item["official_field_key"]
        cell = item["html_id"]
        if key not in allowed_keys:
            errors.append(
                f"{item['id']}: official_field_key {key!r} is not in fields.json"
            )
            continue
        if not key.startswith(writer_prefix):
            errors.append(
                f"{item['id']}: official_field_key {key!r} is not a {writer_prefix} writer key"
            )
            continue
        html, stats = rewrite_input_names(html, cell, key)
        if stats["rewritten"] == 0 and stats["already"] == 0:
            errors.append(
                f"{item['id']}: no input name={cell!r} (or already {key!r}) in HTML"
            )
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
        raise StampError("refused name stamp", errors)
    return html, stamped, unwritable


def html_frm_names(html: str, writer_prefix: str) -> set[str]:
    names = set()
    for match in NAME_ATTR_RE.finditer(html):
        value = match.group(1)
        if value.startswith(writer_prefix):
            names.add(value)
    return names


def writer_frm_keys_from_fields(allowed_keys: set[str], writer_prefix: str) -> set[str]:
    return {key for key in allowed_keys if key.startswith(writer_prefix)}


def check_frozen(
    html_path: pathlib.Path,
    fields_path: pathlib.Path,
    catalog: dict,
    bundle_slug: str,
    form_id: str,
    gaps_path: pathlib.Path | None = None,
) -> dict[str, object]:
    allowed = fields_serialized_keys(fields_path)
    joins = catalog_joins(catalog, bundle_slug)
    writer_prefix = writer_prefix_from_keys([item["official_field_key"] for item in joins])
    html = html_path.read_text(encoding="utf-8")
    writable, unwritable = writable_joins(html, joins)
    stamped_names = {item["official_field_key"] for item in writable}
    present = html_frm_names(html, writer_prefix)
    missing = sorted(stamped_names - present)
    extra = sorted(present - allowed)
    if missing:
        raise StampError("frozen HTML is missing stamped names", missing)
    if extra:
        raise StampError("frozen HTML name= not in fields.json", extra)
    if present != stamped_names:
        raise StampError(
            "frozen HTML frm names must equal the writable catalog 1:1 joins",
            sorted(present.symmetric_difference(stamped_names)),
        )
    report = {
        "stamped": sorted(stamped_names),
        "unwritable_mixed": [item["id"] for item in unwritable],
        "html_path": str(html_path),
    }
    if gaps_path is not None and gaps_path.is_file():
        gaps = json.loads(gaps_path.read_text(encoding="utf-8"))
        listed = set(gaps.get("stamped_names") or [])
        if listed != stamped_names:
            raise StampError(
                "name-gaps.json stamped_names drifted",
                sorted(listed.symmetric_difference(stamped_names)),
            )
        expected_absent = WRITER_FRM_KEYS_ABSENT_FROM_FIELDS.get(form_id, ())
        absent = tuple(gaps.get("writer_frm_keys_absent_from_fields_json") or [])
        if absent != expected_absent:
            raise StampError("pinned writer/fields gap drifted", list(absent))
        report["gaps_path"] = str(gaps_path)
    return report


def write_gaps(
    path: pathlib.Path,
    joins: list[dict[str, str]],
    unwritable: list[dict[str, str]],
    allowed_keys: set[str],
    writer_prefix: str,
    form_id: str,
    bundle_slug: str,
) -> None:
    stamped = [item["official_field_key"] for item in joins if item not in unwritable]
    writer_in_fields = sorted(writer_frm_keys_from_fields(allowed_keys, writer_prefix))
    unstamped = [key for key in writer_in_fields if key not in set(stamped)]
    absent = list(WRITER_FRM_KEYS_ABSENT_FROM_FIELDS.get(form_id, ()))
    payload = {
        "form_id": form_id,
        "html_slug": bundle_slug,
        "rule": (
            "Stamp name= only where the identity catalog has a 1:1 "
            "official_field_key that exists in fields.json. Remaining writer "
            "keys stay on cell-id name=. Do not invent a join. Mixed combs "
            "with no empty input are catalog identities but unwritable."
        ),
        "stamped": [
            {
                "catalog_id": item["id"],
                "html_id": item["html_id"],
                "name": item["official_field_key"],
            }
            for item in joins
            if item not in unwritable
        ],
        "stamped_names": stamped,
        "unwritable_mixed": [
            {
                "catalog_id": item["id"],
                "html_id": item["html_id"],
                "official_field_key": item["official_field_key"],
            }
            for item in unwritable
        ],
        "unstamped_writer_keys_in_fields_json": unstamped,
        "writer_frm_keys_absent_from_fields_json": absent,
        "writer_frm_keys_absent_from_fields_json_reason": WRITER_ABSENT_REASON.get(
            form_id, ""
        ),
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")


def stamp_tree(
    html_path: pathlib.Path,
    fields_path: pathlib.Path,
    catalog: dict,
    bundle_slug: str,
    form_id: str,
    *,
    write: bool,
) -> dict[str, object]:
    if html_path.resolve().parts[-2:] != (bundle_slug, "index.html"):
        raise StampError(
            f"refused to stamp {html_path}; expected html-frozen/{bundle_slug}/index.html"
        )
    resolved = html_path.resolve()
    if "forms-corrected" in resolved.parts or resolved.parts[-3:-1] == ("forms", bundle_slug):
        raise StampError(f"refused to stamp generator tree {html_path}")
    allowed = fields_serialized_keys(fields_path)
    joins = catalog_joins(catalog, bundle_slug)
    writer_prefix = writer_prefix_from_keys([item["official_field_key"] for item in joins])
    html = html_path.read_text(encoding="utf-8")
    html, stamped, unwritable = stamp_html(html, joins, allowed, writer_prefix)
    gaps_path = html_path.parent / "name-gaps.json"
    if write:
        html_path.write_text(html, encoding="utf-8")
        write_gaps(
            gaps_path, joins, unwritable, allowed, writer_prefix, form_id, bundle_slug
        )
        inventory_path = html_path.parent.parent / "inventory.json"
        if inventory_path.is_file():
            inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
            digest = hashlib.sha256(html.encode("utf-8")).hexdigest()
            for bundle in inventory.get("bundles", []):
                if bundle.get("form_id") == form_id or bundle.get("frozen_slug") == bundle_slug:
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
    out, stamped, unwritable = stamp_html(html, joins, allowed, "frm2551Qv2018:")
    assert not unwritable
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
            "frm2551Qv2018:",
        )
    except StampError as error:
        assert "not in fields.json" in error.errors[0]
    else:
        raise AssertionError("invented key must be refused")

    catalog = {
        "records": [
            {
                "id": "2551q-2018/p1/tin-1",
                "bundle_slug": "2551q-2018",
                "official_field_key": "frm2551Qv2018:txtTIN1",
                "html_id_hint": "p1c20",
            },
            {
                "id": "2551q-2018/p1/other",
                "bundle_slug": "2551q-2018",
                "official_field_key": None,
                "official_field_key_gap": "no-unique-leftover",
                "html_id_hint": "p1c99",
            },
        ]
    }
    joins = catalog_joins(catalog, "2551q-2018")
    assert [item["html_id"] for item in joins] == ["p1c20"]

    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp) / "html-frozen" / "2551q-2018"
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
        result = stamp_tree(
            target, fields, catalog, "2551q-2018", "2551q-v2018", write=True
        )
        assert result["wrote"] is True
        stamped_html = target.read_text(encoding="utf-8")
        check_frozen(
            target,
            fields,
            catalog,
            "2551q-2018",
            "2551q-v2018",
            root / "name-gaps.json",
        )
        assert html_frm_names(stamped_html, "frm2551Qv2018:") == {
            "frm2551Qv2018:txtTIN1"
        }


def check_all(
    html_root: pathlib.Path,
    rules_root: pathlib.Path,
    catalog: dict,
) -> None:
    for slug, form_id in STAMPED_BUNDLES:
        html_path = html_root / slug / "index.html"
        fields_path = rules_root / form_id / "fields.json"
        report = check_frozen(
            html_path,
            fields_path,
            catalog,
            slug,
            form_id,
            html_path.parent / "name-gaps.json",
        )
        print(f"OK    {slug} stamped={len(report['stamped'])}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--slug", default="2551q-2018")
    parser.add_argument("--form-id", default="2551q-v2018")
    parser.add_argument("--html", type=pathlib.Path)
    parser.add_argument("--fields", type=pathlib.Path)
    parser.add_argument(
        "--catalog",
        type=pathlib.Path,
        default=REPO / "tools" / "formgen" / "identity" / "catalog.json",
    )
    parser.add_argument("--html-root", type=pathlib.Path, default=REPO / "html-frozen")
    parser.add_argument("--rules-root", type=pathlib.Path)
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--check-all", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        print("OK    self-test")
        return 0
    catalog = json.loads(args.catalog.read_text(encoding="utf-8"))
    try:
        if args.check_all:
            rules_root = args.rules_root
            if rules_root is None:
                raise StampError("--rules-root is required with --check-all")
            check_all(args.html_root, rules_root, catalog)
            return 0
        html_path = args.html or (args.html_root / args.slug / "index.html")
        fields_path = args.fields
        if fields_path is None:
            raise StampError("--fields is required unless --check-all or --self-test")
        joins = catalog_joins(catalog, args.slug)
        if not joins:
            raise StampError(f"no catalog 1:1 joins for {args.slug}")
        if args.check:
            report = check_frozen(
                html_path,
                fields_path,
                catalog,
                args.slug,
                args.form_id,
                html_path.parent / "name-gaps.json",
            )
            print(f"OK    check stamped={len(report['stamped'])}")
            return 0
        result = stamp_tree(
            html_path,
            fields_path,
            catalog,
            args.slug,
            args.form_id,
            write=args.write,
        )
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
