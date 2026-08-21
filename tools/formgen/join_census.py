#!/usr/bin/env python3
"""Read-only join census: catalog identities vs harvested fields.json keys.

This is not a mapper. It never writes HTML, never invents an
``official_field_key``, and never emits ``name="frm…"``. Inventory is
resolved by file existence, not by ``mint_fillables.inventory_path_for_slug``.

Usage:
    python3 tools/formgen/join_census.py --self-test
    python3 tools/formgen/join_census.py --tree forms-corrected \
        --out tools/formgen/corrections/evidence/join-census-20260819-remint.json
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
import tempfile


HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parent.parent
DEFAULT_CATALOG = HERE / "identity" / "catalog.json"
DEFAULT_RULES = REPO / "rules" / "forms"
DEFAULT_OVERLAY = HERE / "inventories"
DEFAULT_OUT = HERE / "corrections" / "evidence" / "join-census-20260819-remint.json"

SLUG_RE = re.compile(r"^(.*?)-(v?)((19|20)\d\d)([a-z]?)$")
OCCURRENCE_RE = re.compile(r"_\d+$")

GAP_NO_UNIQUE = "no unique fields.json key for this box"
GAP_NO_HARVEST = "no harvested fields.json in this checkout"
GAP_AGENT_TIN = "no harvested agent-TIN field_key in this checkout"
GAP_MIXED_PREFIX = "lattice mixed cell covers caption plus tin-1"

CLASS_R1 = "R1_keyed_1to1"
CLASS_R2 = "R2_no_unique_key"
CLASS_R2_AGENT = "R2_agent_tin"
CLASS_R4 = "R4_mixed_cell"
CLASS_R5 = "R5_no_inventory"
CLASS_FALSE_NEGATIVE = "FALSE_NEGATIVE"
RECORD_CLASSES = (
    CLASS_R1,
    CLASS_R2,
    CLASS_R2_AGENT,
    CLASS_R4,
    CLASS_R5,
    CLASS_FALSE_NEGATIVE,
)

ACCEPTANCE = {
    "FALSE_NEGATIVE": 0,
    "R1_keyed_1to1": 167,
    "R2_agent_tin": 4,
    "R2_no_unique_key": 7693,
    "R4_mixed_cell": 1,
    "R5_plus_FALSE_NEGATIVE": 2125,
    "bundles": 53,
    "inventory_files": 44,
    "inventory_null_keys": 972,
    "inventory_rows": 9754,
    "keyed_in_bundles_with_inventory": 167,
    "keyed_in_bundles_without_inventory": 0,
    "records_classified": 9990,
    "resolution_absent": 10,
    "resolution_exact": 37,
    "resolution_skew": 6,
}
ACCEPTANCE_FALSE_NEGATIVE_BUNDLES = ()
ACCEPTANCE_SKEW_BUNDLES = (
    "0605-1999",
    "1601-fq-2020",
    "1601eq-2019",
    "1602q-2019",
    "extra/1700-2018",
    "extra/2200t-2022",
)


def strip_extra_prefix(slug: str) -> str:
    return slug[6:] if slug.startswith("extra/") else slug


def parse_slug(name: str) -> tuple[str, str, str] | None:
    leaf = strip_extra_prefix(name)
    match = SLUG_RE.match(leaf)
    if not match:
        return None
    stem, _marker, year, _century, suffix = match.groups()
    return stem.replace("-", "").lower(), year, suffix


def field_rows(payload: object) -> list[object]:
    if isinstance(payload, dict):
        rows = payload.get("fields")
        return rows if isinstance(rows, list) else []
    if isinstance(payload, list):
        return payload
    return []


def serialized_key(row: object) -> str | None:
    if not isinstance(row, dict):
        return None
    value = row.get("serialized_key")
    if value is None:
        return None
    text = str(value)
    return text if text else None


def extra_inventory_dirs(rules_dir: pathlib.Path) -> list[pathlib.Path]:
    """Overlay inventories that must not join the locked 43-form v1 corpus.

    Catalog slug ``2000-dst-2018`` parses to stem ``2000dst``. Live
    ``rules/forms/2000-v2018`` is stem ``2000``, so the overlay exists so the
    slug can resolve without stealing 2000 or becoming a 44th v1 form.
    """
    try:
        if rules_dir.resolve() != DEFAULT_RULES.resolve():
            return []
    except OSError:
        return []
    if DEFAULT_OVERLAY.is_dir():
        return [DEFAULT_OVERLAY]
    return []


def load_inventories(rules_dir: pathlib.Path) -> dict[str, dict[str, object]]:
    found: dict[str, dict[str, object]] = {}
    roots = [rules_dir, *extra_inventory_dirs(rules_dir)]
    for root in roots:
        if not root.is_dir():
            continue
        for path in sorted(root.glob("*/fields.json")):
            name = path.parent.name
            if name in found:
                raise ValueError(
                    f"duplicate inventory directory {name!r} at {path} "
                    f"and {found[name]['path']}"
                )
            payload = json.loads(path.read_text(encoding="utf-8"))
            rows = field_rows(payload)
            keys: list[str] = []
            nulls = 0
            for row in rows:
                key = serialized_key(row)
                if key is None:
                    nulls += 1
                else:
                    keys.append(key)
            found[name] = {
                "dir": name,
                "keys": keys,
                "nulls": nulls,
                "parsed": parse_slug(name),
                "path": path,
                "rows": len(rows),
            }
    return found


def index_inventories_by_stem(
    inventories: dict[str, dict[str, object]],
) -> dict[str, list[str]]:
    by_stem: dict[str, list[str]] = {}
    for name, inventory in inventories.items():
        parsed = inventory["parsed"]
        if parsed is None:
            continue
        by_stem.setdefault(parsed[0], []).append(name)
    for names in by_stem.values():
        names.sort()
    return by_stem


def resolve_slug(
    slug: str,
    inventories: dict[str, dict[str, object]],
    by_stem: dict[str, list[str]],
) -> dict[str, object]:
    parsed = parse_slug(slug)
    if parsed is None:
        return {"kind": "absent", "inventory": None, "parsed": None}
    stem, year, _suffix = parsed
    candidates = list(by_stem.get(stem, []))
    if not candidates:
        return {"kind": "absent", "inventory": None, "parsed": parsed}
    year_hits = [
        name
        for name in candidates
        if inventories[name]["parsed"] is not None
        and inventories[name]["parsed"][1] == year
    ]
    if year_hits:
        return {"kind": "exact", "inventory": year_hits[0], "parsed": parsed}
    return {"kind": "skew", "inventory": candidates[0], "parsed": parsed}


def claimed_key(record: dict) -> str | None:
    value = record.get("official_field_key")
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text if text else None


def classify_record(record: dict, resolution_kind: str) -> str:
    if claimed_key(record) is not None:
        return CLASS_R1
    gap = str(record.get("official_field_key_gap") or "")
    if gap == GAP_NO_UNIQUE:
        return CLASS_R2
    if gap.startswith(GAP_MIXED_PREFIX):
        return CLASS_R4
    if gap == GAP_NO_HARVEST and resolution_kind == "absent":
        return CLASS_R5
    if gap == GAP_NO_HARVEST and resolution_kind in {"exact", "skew"}:
        return CLASS_FALSE_NEGATIVE
    if gap == GAP_AGENT_TIN:
        return CLASS_R2_AGENT
    raise ValueError(
        f"{record.get('id')}: unclassified official_field_key_gap {gap!r} "
        f"with resolution {resolution_kind!r}"
    )


def unique_key_ownership_errors(
    record: dict,
    inventory_keys: list[str],
) -> list[str]:
    """Reject a claimed key the box does not uniquely own.

    Live classification does not call this: the shipped R1 keys are
    catalog-reported. The self-test does, and must fail the fixture.
    """
    key = claimed_key(record)
    if key is None:
        return []
    hits = [item for item in inventory_keys if item == key]
    if len(hits) == 1:
        return []
    ident = record.get("id")
    return [
        f"{ident}: official_field_key {key!r} is not uniquely owned "
        f"by this box ({len(hits)} inventory hits)"
    ]


def dump_json(payload: object) -> str:
    return json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=True) + "\n"


def load_catalog(path: pathlib.Path) -> list[dict]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    records = payload.get("records") if isinstance(payload, dict) else None
    if not isinstance(records, list):
        raise ValueError(f"{path}: catalog records must be a list")
    return records


def build_census(
    records: list[dict],
    inventories: dict[str, dict[str, object]],
    tree: str,
) -> dict[str, object]:
    by_stem = index_inventories_by_stem(inventories)
    resolutions: dict[str, dict[str, object]] = {}
    for record in records:
        slug = str(record["bundle_slug"])
        if slug not in resolutions:
            resolutions[slug] = resolve_slug(slug, inventories, by_stem)

    classified: list[dict[str, object]] = []
    class_counts = {name: 0 for name in RECORD_CLASSES}
    for record in sorted(records, key=lambda item: str(item["id"])):
        slug = str(record["bundle_slug"])
        kind = str(resolutions[slug]["kind"])
        klass = classify_record(record, kind)
        class_counts[klass] += 1
        classified.append({
            "class": klass,
            "id": record["id"],
            "official_field_key": claimed_key(record),
            "official_field_key_gap": record.get("official_field_key_gap") or "",
            "resolution": kind,
        })

    bundles: list[dict[str, object]] = []
    keyed_with = 0
    keyed_without = 0
    resolution_counts = {"absent": 0, "exact": 0, "skew": 0}
    false_negative_bundles: list[dict[str, object]] = []
    records_by_slug: dict[str, list[dict]] = {}
    for record in records:
        records_by_slug.setdefault(str(record["bundle_slug"]), []).append(record)

    for slug in sorted(records_by_slug):
        resolved = resolutions[slug]
        kind = str(resolved["kind"])
        resolution_counts[kind] += 1
        inventory_name = resolved["inventory"]
        inventory = inventories.get(str(inventory_name)) if inventory_name else None
        rows = int(inventory["rows"]) if inventory else 0
        nulls = int(inventory["nulls"]) if inventory else 0
        keys = list(inventory["keys"]) if inventory else []
        group = records_by_slug[slug]
        identities = len(group)
        keyed = sum(1 for record in group if claimed_key(record) is not None)
        if kind == "absent":
            keyed_without += keyed
        else:
            keyed_with += keyed
        false_negatives = sum(
            1
            for record in group
            if classify_record(record, kind) == CLASS_FALSE_NEGATIVE
        )
        occurrence = sum(1 for key in keys if OCCURRENCE_RE.search(key))
        bundles.append({
            "excess_identities": max(0, identities - rows),
            "false_negative_records": false_negatives,
            "identities": identities,
            "inventory": inventory_name,
            "keyed": keyed,
            "leftover_keys": max(0, rows - identities),
            "nulls": nulls,
            "occurrence_suffixed_keys": occurrence,
            "resolution": kind,
            "rows": rows,
            "slug": slug,
        })
        if false_negatives:
            false_negative_bundles.append({
                "records": false_negatives,
                "slug": slug,
            })

    return {
        "acceptance": {
            **ACCEPTANCE,
            "false_negative_bundles": [
                {"records": count, "slug": slug}
                for slug, count in ACCEPTANCE_FALSE_NEGATIVE_BUNDLES
            ],
            "skew_bundles": list(ACCEPTANCE_SKEW_BUNDLES),
        },
        "bundles": bundles,
        "false_negative_bundles": false_negative_bundles,
        "inventory": {
            "files": len(inventories),
            "null_keys": sum(int(item["nulls"]) for item in inventories.values()),
            "rows": sum(int(item["rows"]) for item in inventories.values()),
        },
        "keyed": {
            "in_bundles_with_inventory": keyed_with,
            "in_bundles_without_inventory": keyed_without,
        },
        "records": classified,
        "resolution": resolution_counts,
        "summary": {
            "bundles": len(bundles),
            "classes": class_counts,
            "records_classified": len(classified),
        },
        "tree": tree,
    }


def acceptance_errors(census: dict[str, object]) -> list[str]:
    errors: list[str] = []
    summary = census["summary"]
    classes = summary["classes"]
    resolution = census["resolution"]
    inventory = census["inventory"]
    keyed = census["keyed"]
    measured = {
        "FALSE_NEGATIVE": classes[CLASS_FALSE_NEGATIVE],
        "R1_keyed_1to1": classes[CLASS_R1],
        "R2_agent_tin": classes[CLASS_R2_AGENT],
        "R2_no_unique_key": classes[CLASS_R2],
        "R4_mixed_cell": classes[CLASS_R4],
        "R5_plus_FALSE_NEGATIVE": (
            classes[CLASS_R5] + classes[CLASS_FALSE_NEGATIVE]
        ),
        "bundles": summary["bundles"],
        "inventory_files": inventory["files"],
        "inventory_null_keys": inventory["null_keys"],
        "inventory_rows": inventory["rows"],
        "keyed_in_bundles_with_inventory": keyed["in_bundles_with_inventory"],
        "keyed_in_bundles_without_inventory": keyed["in_bundles_without_inventory"],
        "records_classified": summary["records_classified"],
        "resolution_absent": resolution["absent"],
        "resolution_exact": resolution["exact"],
        "resolution_skew": resolution["skew"],
    }
    for name, expected in ACCEPTANCE.items():
        got = measured[name]
        if got != expected:
            errors.append(f"{name}: expected {expected}, got {got}")

    got_fn = [
        (str(row["slug"]), int(row["records"]))
        for row in census["false_negative_bundles"]
    ]
    expected_fn = list(ACCEPTANCE_FALSE_NEGATIVE_BUNDLES)
    if got_fn != expected_fn:
        errors.append(
            "false_negative_bundles: expected "
            f"{expected_fn}, got {got_fn}"
        )

    got_skew = [
        str(row["slug"])
        for row in census["bundles"]
        if row["resolution"] == "skew"
    ]
    expected_skew = list(ACCEPTANCE_SKEW_BUNDLES)
    if got_skew != expected_skew:
        errors.append(
            f"skew_bundles: expected {expected_skew}, got {got_skew}"
        )
    return errors


def print_census(census: dict[str, object]) -> None:
    summary = census["summary"]
    classes = summary["classes"]
    resolution = census["resolution"]
    inventory = census["inventory"]
    keyed = census["keyed"]
    print(f"OK    records classified {summary['records_classified']}")
    print(f"OK    {CLASS_R1} {classes[CLASS_R1]}")
    print(f"OK    {CLASS_R2} {classes[CLASS_R2]}")
    print(f"OK    {CLASS_R2_AGENT} {classes[CLASS_R2_AGENT]}")
    print(f"OK    {CLASS_R4} {classes[CLASS_R4]}")
    print(
        f"OK    {CLASS_R5}+{CLASS_FALSE_NEGATIVE} "
        f"{classes[CLASS_R5] + classes[CLASS_FALSE_NEGATIVE]}"
    )
    print(f"OK    {CLASS_FALSE_NEGATIVE} {classes[CLASS_FALSE_NEGATIVE]}")
    print(f"OK    bundles {summary['bundles']}")
    print(
        "OK    resolution "
        f"{resolution['exact']} / {resolution['skew']} / {resolution['absent']}"
    )
    print(
        "OK    inventory "
        f"{inventory['files']} / {inventory['rows']} / {inventory['null_keys']}"
    )
    print(
        "OK    keyed with/without inventory "
        f"{keyed['in_bundles_with_inventory']} / "
        f"{keyed['in_bundles_without_inventory']}"
    )
    for row in census["false_negative_bundles"]:
        print(f"OK    false-negative {row['slug']} {row['records']}")


def write_fields(root: pathlib.Path, dirname: str, keys: list[str | None]) -> None:
    path = root / dirname
    path.mkdir(parents=True, exist_ok=True)
    rows = [{"serialized_key": key} for key in keys]
    (path / "fields.json").write_text(
        json.dumps({"fields": rows}, indent=2) + "\n",
        encoding="utf-8",
    )


def self_test() -> int:
    failed = 0

    def check(name: str, held: bool, detail: str = "") -> None:
        nonlocal failed
        if held:
            print(f"OK    {name}")
        else:
            failed += 1
            print(f"FAIL  {name}" + (f" — {detail}" if detail else ""))

    with tempfile.TemporaryDirectory() as tmp:
        rules = pathlib.Path(tmp) / "rules"
        write_fields(rules, "1702ex-v2018c", ["frm1702EX:txtTIN1"])
        write_fields(rules, "2200t-v2020", ["frm2200T:txtTIN1"])
        write_fields(
            rules,
            "0605-v2003",
            ["frm0605:itemFiscalStartMonth:_1", "plain", None],
        )
        write_fields(
            rules,
            "fixture-v2018",
            ["frmFixture:txtTIN1", "frmFixture:txtTIN1"],
        )
        inventories = load_inventories(rules)
        by_stem = index_inventories_by_stem(inventories)

        unique_record = {
            "id": "fixture/p1/tin-1",
            "bundle_slug": "fixture-2018",
            "official_field_key": "frmFixture:txtTIN1",
            "official_field_key_gap": "",
        }
        unique_errors = unique_key_ownership_errors(
            unique_record, ["frmFixture:txtTIN1"]
        )
        check(
            "unique claimed key is accepted",
            not unique_errors,
            "; ".join(unique_errors),
        )

        bad_record = {
            "id": "fixture/p1/tin-1",
            "bundle_slug": "fixture-2018",
            "official_field_key": "frmFixture:txtTIN1",
            "official_field_key_gap": "",
        }
        fixture_keys = list(inventories["fixture-v2018"]["keys"])
        bad_errors = unique_key_ownership_errors(bad_record, fixture_keys)
        check(
            "non-unique claimed key is rejected",
            any("not uniquely owned" in error for error in bad_errors),
            "census accepted a key its box does not uniquely own"
            if not bad_errors
            else "; ".join(bad_errors),
        )

        exact = resolve_slug("1702ex-2018", inventories, by_stem)
        check(
            "1702ex-2018 vs 1702ex-v2018c is exact",
            exact["kind"] == "exact" and exact["inventory"] == "1702ex-v2018c",
            str(exact),
        )
        skew = resolve_slug("extra/2200t-2022", inventories, by_stem)
        check(
            "extra/2200t-2022 vs 2200t-v2020 is skew",
            skew["kind"] == "skew" and skew["inventory"] == "2200t-v2020",
            str(skew),
        )
        absent_unparsed = resolve_slug(
            "1701-2018-attachment", inventories, by_stem
        )
        check(
            "unparsed attachment slug is absent",
            absent_unparsed["kind"] == "absent"
            and absent_unparsed["inventory"] is None,
            str(absent_unparsed),
        )
        absent_stem = resolve_slug("2000-dst-2018", inventories, by_stem)
        check(
            "2000-dst-2018 does not steal 2000-v2018",
            absent_stem["kind"] == "absent"
            and absent_stem["inventory"] is None,
            str(absent_stem),
        )

        overlay_rules = pathlib.Path(tmp) / "live-forms"
        overlay_rules.mkdir()
        write_fields(overlay_rules, "2000-v2018", ["frm2000:txtTIN1"])
        overlay_root = pathlib.Path(tmp) / "overlay"
        overlay_root.mkdir()
        write_fields(overlay_root, "2000-dst-v2018", ["frm2000:txtTIN1"])
        saved_rules, saved_overlay = DEFAULT_RULES, DEFAULT_OVERLAY
        try:
            globals()["DEFAULT_RULES"] = overlay_rules
            globals()["DEFAULT_OVERLAY"] = overlay_root
            live_plus_overlay = load_inventories(overlay_rules)
            overlay_by_stem = index_inventories_by_stem(live_plus_overlay)
            overlay_hit = resolve_slug(
                "2000-dst-2018", live_plus_overlay, overlay_by_stem
            )
            live_only = resolve_slug("2000-v2018", live_plus_overlay, overlay_by_stem)
            check(
                "overlay 2000-dst-v2018 is exact for 2000-dst-2018",
                overlay_hit["kind"] == "exact"
                and overlay_hit["inventory"] == "2000-dst-v2018",
                str(overlay_hit),
            )
            check(
                "overlay does not steal live 2000-v2018",
                live_only["kind"] == "exact"
                and live_only["inventory"] == "2000-v2018",
                str(live_only),
            )
        finally:
            globals()["DEFAULT_RULES"] = saved_rules
            globals()["DEFAULT_OVERLAY"] = saved_overlay

        check(
            "no-harvest + exact is FALSE_NEGATIVE",
            classify_record(
                {
                    "id": "1702ex-2018/p1/text-1",
                    "official_field_key": None,
                    "official_field_key_gap": GAP_NO_HARVEST,
                },
                "exact",
            )
            == CLASS_FALSE_NEGATIVE,
        )
        check(
            "no-harvest + skew is FALSE_NEGATIVE",
            classify_record(
                {
                    "id": "extra/2200t-2022/p1/text-1",
                    "official_field_key": None,
                    "official_field_key_gap": GAP_NO_HARVEST,
                },
                "skew",
            )
            == CLASS_FALSE_NEGATIVE,
        )
        check(
            "no-harvest + absent is R5_no_inventory",
            classify_record(
                {
                    "id": "0620-2019/p1/text-1",
                    "official_field_key": None,
                    "official_field_key_gap": GAP_NO_HARVEST,
                },
                "absent",
            )
            == CLASS_R5,
        )
        check(
            "no-unique-key gap is R2_no_unique_key",
            classify_record(
                {
                    "id": "2550m-2007/p1/text-1",
                    "official_field_key": None,
                    "official_field_key_gap": GAP_NO_UNIQUE,
                },
                "exact",
            )
            == CLASS_R2,
        )
        check(
            "mixed-cell gap is R4_mixed_cell",
            classify_record(
                {
                    "id": "extra/1801-2018/p1/tin-strip",
                    "official_field_key": None,
                    "official_field_key_gap": (
                        "lattice mixed cell covers caption plus tin-1; "
                        "four HTA keys do not collapse onto this cell — "
                        "tin-2/3/branch are separate"
                    ),
                },
                "exact",
            )
            == CLASS_R4,
        )
        check(
            "agent-TIN gap is R2_agent_tin",
            classify_record(
                {
                    "id": "extra/1600wp-2010/p1/tin-agent-1",
                    "official_field_key": None,
                    "official_field_key_gap": GAP_AGENT_TIN,
                },
                "exact",
            )
            == CLASS_R2_AGENT,
        )

        residue_records = [
            {
                "id": "0605-1999/p1/tin-1",
                "bundle_slug": "0605-1999",
                "official_field_key": "frm0605:txtTIN1",
                "official_field_key_gap": "",
            },
            {
                "id": "0605-1999/p1/text-1",
                "bundle_slug": "0605-1999",
                "official_field_key": None,
                "official_field_key_gap": GAP_NO_HARVEST,
            },
            {
                "id": "0605-1999/p1/text-2",
                "bundle_slug": "0605-1999",
                "official_field_key": None,
                "official_field_key_gap": GAP_NO_HARVEST,
            },
            {
                "id": "0605-1999/p1/text-3",
                "bundle_slug": "0605-1999",
                "official_field_key": None,
                "official_field_key_gap": GAP_NO_HARVEST,
            },
            {
                "id": "0605-1999/p1/text-4",
                "bundle_slug": "0605-1999",
                "official_field_key": None,
                "official_field_key_gap": GAP_NO_HARVEST,
            },
        ]
        residue = build_census(residue_records, inventories, "forms-corrected")
        bundle = residue["bundles"][0]
        check(
            "bundle leftover_keys is max(0, rows - identities)",
            bundle["leftover_keys"] == 0 and bundle["rows"] == 3
            and bundle["identities"] == 5,
            str(bundle),
        )
        check(
            "bundle excess_identities is max(0, identities - rows)",
            bundle["excess_identities"] == 2,
            str(bundle),
        )
        check(
            "occurrence_suffixed_keys counts _\\d+$ keys",
            bundle["occurrence_suffixed_keys"] == 1,
            str(bundle),
        )
        dumped = dump_json(residue)
        check(
            "JSON dump is byte-identical across two dumps",
            dumped == dump_json(residue),
        )

    print(
        "FAIL" if failed else "OK",
        f"{failed} self-test(s) failed" if failed else "self-test",
    )
    return 1 if failed else 0


def run_census(
    catalog: pathlib.Path,
    rules: pathlib.Path,
    tree: str,
    out: pathlib.Path | None,
) -> int:
    try:
        records = load_catalog(catalog)
        inventories = load_inventories(rules)
        census = build_census(records, inventories, tree)
    except ValueError as exc:
        print(f"FAIL  {exc}")
        return 1
    errors = acceptance_errors(census)
    if errors:
        for error in errors:
            print(f"FAIL  {error}")
        print(f"{len(errors)} acceptance error(s)")
        return 1
    print_census(census)
    payload = dump_json(census)
    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(payload, encoding="utf-8")
        print(f"wrote {out}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--catalog", type=pathlib.Path, default=DEFAULT_CATALOG)
    parser.add_argument("--rules", type=pathlib.Path, default=DEFAULT_RULES)
    parser.add_argument(
        "--tree",
        default="forms-corrected",
        help="recorded binding only; the census does not read HTML",
    )
    parser.add_argument("--out", type=pathlib.Path, default=None)
    args = parser.parse_args()
    if args.self_test:
        return self_test()
    if args.out is None:
        args.out = DEFAULT_OUT
    return run_census(args.catalog, args.rules, args.tree, args.out)


if __name__ == "__main__":
    sys.exit(main())
