#!/usr/bin/env python3
"""Read-only leftover-key census.

A leftover is a harvested ``serialized_key`` that no catalog record claims
as ``official_field_key``. Unique leftovers are inventory facts, not joins:
appearing once in ``fields.json`` does not attribute the key to a box.

This module never writes HTML, never writes ``official_field_key``, never
emits ``name="frm…"``, and never invents a harvest rule. Inventory is
resolved by file existence via ``join_census``.

Windows follow-up (dummy Save / saveXML, no remint):
``tools/formgen/HANDOFF-WINDOWS-EBIRFORMS.md``.

Usage:
    python3 tools/formgen/leftover_keys.py --self-test
    python3 tools/formgen/leftover_keys.py --tree forms-corrected \
        --out tools/formgen/corrections/evidence/leftover-keys-20260820.json
"""

from __future__ import annotations

import argparse
import pathlib
import sys
import tempfile
from collections import Counter


HERE = pathlib.Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import join_census as jc  # noqa: E402

REPO = HERE.parent.parent
DEFAULT_CATALOG = jc.DEFAULT_CATALOG
DEFAULT_RULES = jc.DEFAULT_RULES
DEFAULT_OUT = HERE / "corrections" / "evidence" / "leftover-keys-20260820.json"

CLASS_CLAIMED_UNIQUE = "claimed_unique"
CLASS_CLAIMED_DUPLICATE = "claimed_duplicate"
CLASS_CLAIMED_ABSENT = "claimed_absent"
CLASS_LEFTOVER_UNIQUE = "leftover_unique"
CLASS_LEFTOVER_DUPLICATE = "leftover_duplicate"
KEY_CLASSES = (
    CLASS_CLAIMED_UNIQUE,
    CLASS_CLAIMED_DUPLICATE,
    CLASS_CLAIMED_ABSENT,
    CLASS_LEFTOVER_UNIQUE,
    CLASS_LEFTOVER_DUPLICATE,
)

# Pins measured 2026-08-21 after 1701Q spouse TIN named harvest (printed
# "Spouse's TIN"). Uniqueness in inventory is not a join.
ACCEPTANCE = {
    "bundles": 53,
    "bundles_with_leftover_unique": 43,
    "claimed": 167,
    "claimed_absent": 0,
    "claimed_duplicate": 4,
    "claimed_unique": 163,
    "inventory_files": 44,
    "inventory_null_keys": 972,
    "inventory_rows": 9754,
    "leftover_duplicate": 5,
    "leftover_unique": 8432,
}
# Named-suffix leftovers that look like harvest_tin's shape (one key per
# bundle matching a leaf). Facts, not joins. A future harvest needs a
# named rule AND a box role; this pin only detects silent disappearance.
LEAF_UNIQUE_ACCEPTANCE = {
    "ebirOnlineUsername": 36,
    "txtAddress": 19,
    "txtEmail": 29,
    "txtFinalFlag": 36,
    "txtRDOCode": 22,
    "txtTaxpayerName": 13,
    "txtZipCode": 20,
}
ACCEPTANCE_CLAIMED_DUPLICATE = (
    ("extra/2200a-2020", "frm2200Av2020:branchCode", 3),
    ("extra/2200a-2020", "frm2200Av2020:tinA", 3),
    ("extra/2200a-2020", "frm2200Av2020:tinB", 3),
    ("extra/2200a-2020", "frm2200Av2020:tinC", 3),
)
ACCEPTANCE_CLAIMED_ABSENT_BUNDLES = ()
ACCEPTANCE_LEFTOVER_DUPLICATE = (
    ("1702q-2018", "frm1702q:txtTelNum", 2),
    ("1707a-2021", "frm1707Av2021:txtI11Email", 2),
    ("2000-dst-2018", "frm2000:modLabel", 4),
    ("2551q-2018", "txtEmail", 2),
    ("extra/2200a-2020", "frm2200Av2020:registeredName", 3),
)


def leaf_of(key: str) -> str:
    return key.rsplit(":", 1)[-1]


def unique_inventory_ownership_errors(key: str, inventory_keys: list[str]) -> list[str]:
    """Refuse to treat a key as uniquely owned when inventory disagrees.

    Live classification reports claimed_duplicate instead of aborting:
    four shipped R1 keys on extra/2200a-2020 appear three times each.
    The self-test must still be able to reject that claim.
    """
    hits = [item for item in inventory_keys if item == key]
    if len(hits) == 1:
        return []
    return [
        f"serialized_key {key!r} is not uniquely owned "
        f"({len(hits)} inventory hits)"
    ]


def claimed_by_slug(records: list[dict]) -> dict[str, set[str]]:
    found: dict[str, set[str]] = {}
    for record in records:
        key = jc.claimed_key(record)
        if key is None:
            continue
        found.setdefault(str(record["bundle_slug"]), set()).add(key)
    return found


def classify_key(key: str, count: int, claimed: bool) -> str:
    if claimed:
        if count == 1:
            return CLASS_CLAIMED_UNIQUE
        if count == 0:
            return CLASS_CLAIMED_ABSENT
        return CLASS_CLAIMED_DUPLICATE
    if count == 1:
        return CLASS_LEFTOVER_UNIQUE
    if count > 1:
        return CLASS_LEFTOVER_DUPLICATE
    raise ValueError(f"unclassified leftover {key!r} count={count} claimed={claimed}")


def build_census(
    records: list[dict],
    inventories: dict[str, dict[str, object]],
    tree: str,
) -> dict[str, object]:
    by_stem = jc.index_inventories_by_stem(inventories)
    claimed_map = claimed_by_slug(records)
    slugs = sorted({str(record["bundle_slug"]) for record in records})

    class_counts = {name: 0 for name in KEY_CLASSES}
    leaf_unique: Counter[str] = Counter()
    bundles: list[dict[str, object]] = []
    claimed_duplicate_keys: list[dict[str, object]] = []
    claimed_absent_keys: list[dict[str, object]] = []
    leftover_duplicate_keys: list[dict[str, object]] = []
    claimed_total = sum(len(keys) for keys in claimed_map.values())

    for slug in slugs:
        resolved = jc.resolve_slug(slug, inventories, by_stem)
        kind = str(resolved["kind"])
        inventory_name = resolved["inventory"]
        inventory = inventories.get(str(inventory_name)) if inventory_name else None
        keys = list(inventory["keys"]) if inventory else []
        counts = Counter(keys)
        claimed = claimed_map.get(slug, set())
        leftover_unique_keys: list[str] = []
        bundle_duplicate_leftovers: list[dict[str, object]] = []
        bundle_claimed_duplicate: list[dict[str, object]] = []
        bundle_claimed_absent: list[str] = []
        bundle_classes = {name: 0 for name in KEY_CLASSES}

        for key, count in sorted(counts.items()):
            klass = classify_key(key, count, key in claimed)
            class_counts[klass] += 1
            bundle_classes[klass] += 1
            row = {"count": count, "key": key, "slug": slug}
            if klass == CLASS_LEFTOVER_UNIQUE:
                leftover_unique_keys.append(key)
                leaf_unique[leaf_of(key)] += 1
            elif klass == CLASS_LEFTOVER_DUPLICATE:
                bundle_duplicate_leftovers.append(row)
                leftover_duplicate_keys.append(row)
            elif klass == CLASS_CLAIMED_DUPLICATE:
                bundle_claimed_duplicate.append(row)
                claimed_duplicate_keys.append(row)

        for key in sorted(claimed - set(counts)):
            class_counts[CLASS_CLAIMED_ABSENT] += 1
            bundle_classes[CLASS_CLAIMED_ABSENT] += 1
            bundle_claimed_absent.append(key)
            claimed_absent_keys.append({
                "inventory": inventory_name,
                "key": key,
                "resolution": kind,
                "slug": slug,
            })

        bundles.append({
            "claimed": len(claimed),
            "claimed_absent": bundle_classes[CLASS_CLAIMED_ABSENT],
            "claimed_absent_keys": bundle_claimed_absent,
            "claimed_duplicate": bundle_classes[CLASS_CLAIMED_DUPLICATE],
            "claimed_duplicate_keys": bundle_claimed_duplicate,
            "claimed_unique": bundle_classes[CLASS_CLAIMED_UNIQUE],
            "inventory": inventory_name,
            "leftover_duplicate": bundle_classes[CLASS_LEFTOVER_DUPLICATE],
            "leftover_duplicate_keys": bundle_duplicate_leftovers,
            "leftover_unique": bundle_classes[CLASS_LEFTOVER_UNIQUE],
            "leftover_unique_keys": leftover_unique_keys,
            "nulls": int(inventory["nulls"]) if inventory else 0,
            "resolution": kind,
            "rows": int(inventory["rows"]) if inventory else 0,
            "slug": slug,
        })

    return {
        "acceptance": {
            **ACCEPTANCE,
            "claimed_absent_bundles": list(ACCEPTANCE_CLAIMED_ABSENT_BUNDLES),
            "claimed_duplicate_keys": [
                {"count": count, "key": key, "slug": slug}
                for slug, key, count in ACCEPTANCE_CLAIMED_DUPLICATE
            ],
            "leaf_unique": dict(LEAF_UNIQUE_ACCEPTANCE),
            "leftover_duplicate_keys": [
                {"count": count, "key": key, "slug": slug}
                for slug, key, count in ACCEPTANCE_LEFTOVER_DUPLICATE
            ],
        },
        "bundles": bundles,
        "claimed_absent_keys": claimed_absent_keys,
        "claimed_duplicate_keys": claimed_duplicate_keys,
        "inventory": {
            "files": len(inventories),
            "null_keys": sum(int(item["nulls"]) for item in inventories.values()),
            "rows": sum(int(item["rows"]) for item in inventories.values()),
        },
        "leaf_histogram_leftover_unique": dict(sorted(leaf_unique.items())),
        "leftover_duplicate_keys": leftover_duplicate_keys,
        "note": (
            "leftover_unique is an inventory fact, not a join. Do not copy "
            "these keys onto official_field_key or name= without a named "
            "harvest rule and a box role. claimed_duplicate keys are shipped "
            "R1 records whose inventory count is not 1."
        ),
        "summary": {
            "bundles": len(bundles),
            "bundles_with_leftover_unique": sum(
                1 for row in bundles if int(row["leftover_unique"]) > 0
            ),
            "claimed": claimed_total,
            "classes": class_counts,
        },
        "tree": tree,
    }


def acceptance_errors(census: dict[str, object]) -> list[str]:
    errors: list[str] = []
    summary = census["summary"]
    classes = summary["classes"]
    inventory = census["inventory"]
    measured = {
        "bundles": summary["bundles"],
        "bundles_with_leftover_unique": summary["bundles_with_leftover_unique"],
        "claimed": summary["claimed"],
        "claimed_absent": classes[CLASS_CLAIMED_ABSENT],
        "claimed_duplicate": classes[CLASS_CLAIMED_DUPLICATE],
        "claimed_unique": classes[CLASS_CLAIMED_UNIQUE],
        "inventory_files": inventory["files"],
        "inventory_null_keys": inventory["null_keys"],
        "inventory_rows": inventory["rows"],
        "leftover_duplicate": classes[CLASS_LEFTOVER_DUPLICATE],
        "leftover_unique": classes[CLASS_LEFTOVER_UNIQUE],
    }
    for name, expected in ACCEPTANCE.items():
        got = measured[name]
        if got != expected:
            errors.append(f"{name}: expected {expected}, got {got}")

    histogram = census["leaf_histogram_leftover_unique"]
    for leaf, expected in LEAF_UNIQUE_ACCEPTANCE.items():
        got = int(histogram.get(leaf, 0))
        if got != expected:
            errors.append(f"leaf {leaf}: expected {expected}, got {got}")

    got_dup = [
        (str(row["slug"]), str(row["key"]), int(row["count"]))
        for row in census["claimed_duplicate_keys"]
    ]
    expected_dup = list(ACCEPTANCE_CLAIMED_DUPLICATE)
    if got_dup != expected_dup:
        errors.append(
            f"claimed_duplicate_keys: expected {expected_dup}, got {got_dup}"
        )

    got_absent = sorted({str(row["slug"]) for row in census["claimed_absent_keys"]})
    expected_absent = list(ACCEPTANCE_CLAIMED_ABSENT_BUNDLES)
    if got_absent != expected_absent:
        errors.append(
            f"claimed_absent_bundles: expected {expected_absent}, got {got_absent}"
        )

    got_leftover_dup = [
        (str(row["slug"]), str(row["key"]), int(row["count"]))
        for row in census["leftover_duplicate_keys"]
    ]
    expected_leftover_dup = list(ACCEPTANCE_LEFTOVER_DUPLICATE)
    if got_leftover_dup != expected_leftover_dup:
        errors.append(
            "leftover_duplicate_keys: expected "
            f"{expected_leftover_dup}, got {got_leftover_dup}"
        )
    return errors


def leftover_join_errors(census: dict[str, object]) -> list[str]:
    """Leftover keys must not carry an identity id. That would be a join."""
    errors: list[str] = []
    for bundle in census["bundles"]:
        slug = bundle["slug"]
        for key in bundle["leftover_unique_keys"]:
            if not isinstance(key, str):
                errors.append(
                    f"{slug}: leftover_unique entry {key!r} is not a bare key"
                )
        for row in bundle["leftover_duplicate_keys"]:
            extra = set(row) - {"count", "key", "slug"}
            if extra:
                errors.append(
                    f"{slug}: leftover_duplicate extra fields {sorted(extra)}"
                )
    return errors


def print_census(census: dict[str, object]) -> None:
    summary = census["summary"]
    classes = summary["classes"]
    inventory = census["inventory"]
    print(f"OK    leftover_unique {classes[CLASS_LEFTOVER_UNIQUE]}")
    print(f"OK    leftover_duplicate {classes[CLASS_LEFTOVER_DUPLICATE]}")
    print(f"OK    claimed_unique {classes[CLASS_CLAIMED_UNIQUE]}")
    print(f"OK    claimed_duplicate {classes[CLASS_CLAIMED_DUPLICATE]}")
    print(f"OK    claimed_absent {classes[CLASS_CLAIMED_ABSENT]}")
    print(f"OK    claimed {summary['claimed']}")
    print(
        "OK    bundles "
        f"{summary['bundles']} / with leftover_unique "
        f"{summary['bundles_with_leftover_unique']}"
    )
    print(
        "OK    inventory "
        f"{inventory['files']} / {inventory['rows']} / {inventory['null_keys']}"
    )
    histogram = census["leaf_histogram_leftover_unique"]
    for leaf in LEAF_UNIQUE_ACCEPTANCE:
        print(f"OK    leaf {leaf} {histogram.get(leaf, 0)}")
    for row in census["claimed_duplicate_keys"]:
        print(
            f"OK    claimed_duplicate {row['slug']} {row['key']} "
            f"x{row['count']}"
        )
    for slug in ACCEPTANCE_CLAIMED_ABSENT_BUNDLES:
        n = sum(1 for item in census["claimed_absent_keys"] if item["slug"] == slug)
        print(f"OK    claimed_absent {slug} {n}")


def self_test() -> int:
    failed = 0

    def check(name: str, held: bool, detail: str = "") -> None:
        nonlocal failed
        if held:
            print(f"OK    {name}")
        else:
            failed += 1
            print(f"FAIL  {name}" + (f" — {detail}" if detail else ""))

    check(
        "claimed unique+duplicate+absent pins sum to R1",
        (
            ACCEPTANCE["claimed_unique"]
            + ACCEPTANCE["claimed_duplicate"]
            + ACCEPTANCE["claimed_absent"]
            == ACCEPTANCE["claimed"]
            == jc.ACCEPTANCE["R1_keyed_1to1"]
        ),
        str(ACCEPTANCE["claimed"]),
    )

    with tempfile.TemporaryDirectory() as tmp:
        rules = pathlib.Path(tmp) / "rules"
        jc.write_fields(rules, "fixture-v2018", [
            "frmFixture:txtTIN1",
            "frmFixture:txtEmail",
            "frmFixture:txtEmail",
            "frmFixture:txtZipCode",
            None,
        ])
        jc.write_fields(rules, "dup-v2018", [
            "frmDup:tinA",
            "frmDup:tinA",
            "frmDup:tinA",
        ])
        inventories = jc.load_inventories(rules)

        unique_errors = unique_inventory_ownership_errors(
            "frmFixture:txtTIN1", inventories["fixture-v2018"]["keys"],
        )
        check("unique inventory key is accepted", not unique_errors, "; ".join(unique_errors))

        dup_errors = unique_inventory_ownership_errors(
            "frmFixture:txtEmail", inventories["fixture-v2018"]["keys"],
        )
        check(
            "non-unique inventory key is rejected",
            any("not uniquely owned" in error for error in dup_errors),
            "census accepted a leftover its inventory does not uniquely own"
            if not dup_errors else "; ".join(dup_errors),
        )

        records = [
            {
                "id": "fixture-2018/p1/tin-1",
                "bundle_slug": "fixture-2018",
                "official_field_key": "frmFixture:txtTIN1",
                "official_field_key_gap": "",
            },
            {
                "id": "fixture-2018/p1/text-1",
                "bundle_slug": "fixture-2018",
                "official_field_key": None,
                "official_field_key_gap": jc.GAP_NO_UNIQUE,
            },
            {
                "id": "dup-2018/p1/tin-1",
                "bundle_slug": "dup-2018",
                "official_field_key": "frmDup:tinA",
                "official_field_key_gap": "",
            },
            {
                "id": "absent-2018/p1/tin-1",
                "bundle_slug": "absent-2018",
                "official_field_key": "frmAbsent:txtTIN1",
                "official_field_key_gap": "",
            },
        ]
        census = build_census(records, inventories, "forms-corrected")
        classes = census["summary"]["classes"]
        fixture = next(row for row in census["bundles"] if row["slug"] == "fixture-2018")
        dup = next(row for row in census["bundles"] if row["slug"] == "dup-2018")
        absent = next(row for row in census["bundles"] if row["slug"] == "absent-2018")

        check(
            "unclaimed unique key is leftover_unique, not a join",
            fixture["leftover_unique_keys"] == ["frmFixture:txtZipCode"]
            and fixture["leftover_unique"] == 1
            and all(isinstance(key, str) for key in fixture["leftover_unique_keys"]),
            str(fixture["leftover_unique_keys"]),
        )
        check(
            "unclaimed duplicate key is leftover_duplicate",
            fixture["leftover_duplicate"] == 1
            and fixture["leftover_duplicate_keys"][0]["key"] == "frmFixture:txtEmail"
            and fixture["leftover_duplicate_keys"][0]["count"] == 2,
            str(fixture["leftover_duplicate_keys"]),
        )
        check(
            "claimed unique key is claimed_unique",
            fixture["claimed_unique"] == 1
            and fixture["claimed"] == 1
            and "frmFixture:txtTIN1" not in fixture["leftover_unique_keys"],
        )
        check(
            "claimed key with three inventory hits is claimed_duplicate",
            dup["claimed_duplicate"] == 1
            and dup["claimed_duplicate_keys"][0]["count"] == 3
            and dup["leftover_unique"] == 0,
            str(dup),
        )
        check(
            "claimed key with no inventory is claimed_absent",
            absent["claimed_absent"] == 1
            and absent["claimed_absent_keys"] == ["frmAbsent:txtTIN1"]
            and absent["resolution"] == "absent",
            str(absent),
        )
        check(
            "leftover entries carry no identity id",
            leftover_join_errors(census) == [],
            "; ".join(leftover_join_errors(census)),
        )
        dumped = jc.dump_json(census)
        check(
            "JSON dump is byte-identical across two dumps",
            dumped == jc.dump_json(census),
        )
        check(
            "null serialized_key is not a leftover",
            "None" not in fixture["leftover_unique_keys"]
            and fixture["nulls"] == 1,
        )
        check(
            "fixture classes do not promote leftovers to claimed_unique",
            classes[CLASS_CLAIMED_UNIQUE] == 1
            and classes[CLASS_LEFTOVER_UNIQUE] == 1
            and classes[CLASS_LEFTOVER_DUPLICATE] == 1
            and classes[CLASS_CLAIMED_DUPLICATE] == 1
            and classes[CLASS_CLAIMED_ABSENT] == 1,
            str(classes),
        )

    print("FAIL" if failed else "OK",
          f"{failed} self-test(s) failed" if failed else "self-test")
    return 1 if failed else 0


def run_census(
    catalog: pathlib.Path,
    rules: pathlib.Path,
    tree: str,
    out: pathlib.Path | None,
) -> int:
    try:
        records = jc.load_catalog(catalog)
        inventories = jc.load_inventories(rules)
        census = build_census(records, inventories, tree)
    except ValueError as exc:
        print(f"FAIL  {exc}")
        return 1
    errors = acceptance_errors(census) + leftover_join_errors(census)
    if errors:
        for error in errors:
            print(f"FAIL  {error}")
        print(f"{len(errors)} acceptance error(s)")
        return 1
    print_census(census)
    payload = jc.dump_json(census)
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
