#!/usr/bin/env python3
"""Mint durable fillable identities. Not a mapper; writes catalog records only."""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
FORMGEN = HERE.parent
REPO = FORMGEN.parent.parent
sys.path.insert(0, str(FORMGEN))
import field_identity as fi  # noqa: E402
import join_census as jc  # noqa: E402

RULES_FORMS = REPO / "rules" / "forms"
OVERLAY_FORMS = FORMGEN / "inventories"
CENSUS_PATH = FORMGEN / "corrections" / "evidence" / "tin-branch-census-20260808.json"
EVIDENCE = FORMGEN / "corrections" / "evidence"
MATCH = {"kind": "field", "tolerance_pt": 0.25, "cardinality": "exactly-one"}
GAP_NO_HARVEST = "no harvested fields.json in this checkout"
GAP_NO_UNIQUE = "no unique fields.json key for this box"
EXPECTED_FALSE_NEGATIVE_REWRITES = 1334
EXPECTED_1701Q_SPOUSE_REWRITES = 4
TIN_EXTRA_ROLES = ("tin-extra-1", "tin-extra-2", "tin-extra-3", "tin-extra-branch")
TIN_SPOUSE_ROLES = ("tin-spouse-1", "tin-spouse-2", "tin-spouse-3", "tin-spouse-branch")
# Printed on html-frozen/1701q-2018/index.html (div.t), not inferred from y.
# Item 17 at y=391.50, x=26.76: "Spouse's TIN". Section at y=377.25:
# "PART II – BACKGROUND INFORMATION ON SPOUSE". Chain hints p1c66/68/70/72.
PRINTED_1701Q_SPOUSE_TIN = "Spouse's TIN"


def round_box(box: tuple[float, float, float, float]) -> list[float]:
    return [round(float(value), 4) for value in box]


def page_of(html_id: str) -> int:
    return int(html_id.split("c", 1)[0][1:])


def load_inventories() -> dict[str, pathlib.Path]:
    found: dict[str, pathlib.Path] = {}
    roots = [RULES_FORMS]
    if OVERLAY_FORMS.is_dir():
        roots.append(OVERLAY_FORMS)
    for root in roots:
        if not root.is_dir():
            continue
        for path in root.glob("*/fields.json"):
            name = path.parent.name
            found[name] = path
            payload = json.loads(path.read_text(encoding="utf-8"))
            form_id = str(payload.get("form_id") or "")
            if form_id:
                found[form_id] = path
    return found


def inventory_path_for_slug(slug: str, inventories: dict[str, pathlib.Path]) -> pathlib.Path | None:
    """Resolve by file existence. Same algorithm as join_census; never invent a key."""
    dirs: dict[str, dict[str, object]] = {}
    for name, path in inventories.items():
        if path.parent.name != name:
            continue
        dirs[name] = {
            "dir": name,
            "keys": [],
            "nulls": 0,
            "parsed": jc.parse_slug(name),
            "path": path,
            "rows": 0,
        }
    resolved = jc.resolve_slug(slug, dirs, jc.index_inventories_by_stem(dirs))
    inventory = resolved["inventory"]
    if inventory is None:
        return None
    return inventories.get(str(inventory))


def inventory_keys(path: pathlib.Path) -> list[dict]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    fields = payload.get("fields")
    return fields if isinstance(fields, list) else []


def make_record(ident: str, slug: str, page: int, role: str, box: list[float],
                hint: str, key: str | None, gap: str) -> dict:
    return {
        "id": ident,
        "bundle_slug": slug,
        "page": page,
        "role": role,
        "source_printed_box_pt": box,
        "official_field_key": key,
        "official_field_key_gap": "" if key else gap,
        "html_id_hint": hint,
        "match": dict(MATCH),
        "correction_id": None,
    }


def used_keys(catalog: dict, slug: str) -> set[str]:
    return {
        str(record["official_field_key"])
        for record in catalog["records"]
        if record["bundle_slug"] == slug and record.get("official_field_key")
    }


def harvest_tin(keys: list[dict], used: set[str], role: str, page: int) -> tuple[str | None, str]:
    suffix = {
        "tin-1": r"TIN1$",
        "tin-2": r"TIN2$",
        "tin-3": r"TIN3$",
        "tin-branch": r"(BranchCode|TIN4|TINBranchCode)$",
        "tin-p2-1": r"Pg2TIN1$",
        "tin-p2-2": r"Pg2TIN2$",
        "tin-p2-3": r"Pg2TIN3$",
        "tin-p2-branch": r"Pg2BranchCode$",
        "tin-spouse-1": r"SpouseTIN1$",
        "tin-spouse-2": r"SpouseTIN2$",
        "tin-spouse-3": r"SpouseTIN3$",
        "tin-spouse-branch": r"SpouseBranchCode$",
        "tin-strip": None,
    }.get(role)
    if role == "tin-strip":
        return None, (
            "lattice mixed cell covers caption plus tin-1; four HTA keys do not "
            "collapse onto this cell — tin-2/3/branch are separate"
        )
    if not suffix:
        return None, "no unique fields.json key for this box"
    hits = []
    for field in keys:
        sk = str(field.get("serialized_key") or field.get("field_key") or "")
        if not sk or sk in used:
            continue
        if re.search(suffix, sk, re.I) and "Shed" not in sk and "EMP" not in sk:
            field_page = field.get("page")
            if field_page in (None, page) or (role.startswith("tin-p2") and field_page in (None, 2)):
                hits.append(sk)
    # unique among hits
    if len(hits) == 1:
        return hits[0], ""
    if not hits:
        return None, "no unique fields.json key for this box"
    return None, f"multiple fields.json keys could name this box ({len(hits)})"


def no_inventory_gap(slug: str, inventories: dict[str, pathlib.Path]) -> str | None:
    if inventory_path_for_slug(slug, inventories) is None:
        return "no harvested fields.json in this checkout"
    return None


def default_harvest(slug: str, inventories: dict[str, pathlib.Path]) -> tuple[str | None, str]:
    gap = no_inventory_gap(slug, inventories)
    if gap:
        return None, gap
    return None, "no unique fields.json key for this box"


def claimed_ids(catalog: dict, tree: pathlib.Path, slug: str) -> set[str]:
    html_path = tree / slug / "index.html"
    if not html_path.is_file():
        return set()
    fields = fi.collect_fields(html_path.read_text(encoding="utf-8"))
    records = fi.records_for_slug(catalog, slug)
    claimed: set[str] = set()
    for field in fields:
        if fi.identities_claiming(field, records):
            claimed.add(str(field["id"]))
    return claimed


def slots_of(field: dict) -> int:
    raw = str(field.get("comb_slots") or "0")
    try:
        return int(raw)
    except ValueError:
        return 0


def detect_unclaimed_tin_chains(catalog: dict, tree: pathlib.Path) -> list[dict]:
    chains: list[dict] = []
    for slug, html_path in fi.iter_bundles(tree):
        fields = fi.collect_fields(html_path.read_text(encoding="utf-8"))
        records = fi.records_for_slug(catalog, slug)
        by_page: dict[str, list[dict]] = {}
        for field in fields:
            by_page.setdefault(str(field["page_prefix"]), []).append(field)
        for page_prefix, flist in by_page.items():
            combs = [field for field in flist if slots_of(field) > 0]
            combs.sort(key=lambda field: (field["box"][1], field["box"][0]))
            index = 0
            while index < len(combs) - 3:
                group = combs[index:index + 4]
                ys = [(field["box"][1] + field["box"][3]) / 2 for field in group]
                if max(ys) - min(ys) > 2.0:
                    index += 1
                    continue
                slot_counts = [slots_of(field) for field in group]
                if slot_counts[0] == 3 and slot_counts[1] == 3 and slot_counts[2] == 3 and slot_counts[3] in (3, 4, 5):
                    hits = [fi.identities_claiming(field, records) for field in group]
                    if not any(hits):
                        chains.append({
                            "bundle_slug": slug,
                            "page": page_of(str(group[0]["id"])),
                            "fields": group,
                            "slots": slot_counts,
                        })
                    index += 4
                    continue
                index += 1
    return chains


def classify_role(field: dict, counters: dict[str, int]) -> str:
    kind = str(field["field_kind"])
    box = field["box"]
    width = float(box[2] - box[0])
    height = float(box[3] - box[1])
    aspect = width / height if height else 99.0
    slots = slots_of(field)
    if kind == "text" and 4 <= width <= 20 and 4 <= height <= 20 and 0.70 <= aspect <= 1.45:
        prefix = "xbox"
    elif kind == "comb" and slots:
        pitch = width / slots if slots else 0
        if slots >= 10:
            prefix = f"money-{slots}"
        elif slots == 8 and 8 <= pitch <= 16:
            prefix = "date-full"
        elif slots == 6 and 8 <= pitch <= 16:
            prefix = "date-mmyyyy"
        elif slots == 4 and 8 <= pitch <= 16:
            prefix = "date-yyyy"
        else:
            prefix = f"comb-{slots}s"
    elif kind == "text":
        prefix = "text"
    else:
        prefix = "field"
    counters[prefix] = counters.get(prefix, 0) + 1
    return f"{prefix}-{counters[prefix]}"


def tin_roles_for_chain(slug: str, page: int) -> tuple[str, str, str, str]:
    if slug in ("extra/1700-2018", "1701q-2018") and page == 1:
        return TIN_SPOUSE_ROLES
    if page >= 2:
        return (f"tin-p{page}-1", f"tin-p{page}-2", f"tin-p{page}-3", f"tin-p{page}-branch")
    return TIN_EXTRA_ROLES


def mint_i1(catalog: dict, tree: pathlib.Path, inventories: dict[str, pathlib.Path]) -> list[dict]:
    new_records: list[dict] = []
    census = json.loads(CENSUS_PATH.read_text(encoding="utf-8"))
    form = (census.get("forms") or {}).get("1801-2018") or {}
    band = form.get("band_y") or [136.7, 155.54]
    groups = form.get("groups_x") or []
    slug = "extra/1801-2018"
    html_path = tree / slug / "index.html"
    fields = fi.collect_fields(html_path.read_text(encoding="utf-8"))
    p13 = next(field for field in fields if field["id"] == "p1c13")
    inv = inventory_path_for_slug(slug, inventories)
    keys = inventory_keys(inv) if inv else []
    used = used_keys(catalog, slug)
    # mixed caption+tin-1 strip
    key, gap = harvest_tin(keys, used, "tin-strip", 1)
    new_records.append(make_record(
        f"{slug}/p1/tin-strip", slug, 1, "tin-strip",
        round_box(p13["box"]), "p1c13", key, gap))
    used = used_keys({"records": catalog["records"] + new_records}, slug)
    roles = ("tin-2", "tin-3", "tin-branch")
    hints = ("p1c14", "p1c15", "p1c17")
    for index, role in enumerate(roles):
        x0, x1, _n = groups[index + 1]
        box = [float(x0), float(band[0]), float(x1), float(band[1])]
        key, gap = harvest_tin(keys, used, role, 1)
        if key:
            used.add(key)
        new_records.append(make_record(
            f"{slug}/p1/{role}", slug, 1, role, box, hints[index], key, gap))

    for chain in detect_unclaimed_tin_chains(catalog, tree):
        slug = chain["bundle_slug"]
        page = chain["page"]
        roles = tin_roles_for_chain(slug, page)
        inv = inventory_path_for_slug(slug, inventories)
        keys = inventory_keys(inv) if inv else []
        used = used_keys({"records": catalog["records"] + new_records}, slug)
        gap_no_inv = no_inventory_gap(slug, inventories)
        for role, field in zip(roles, chain["fields"]):
            if gap_no_inv:
                key, gap = None, gap_no_inv
            else:
                key, gap = harvest_tin(keys, used, role, page)
                if key:
                    used.add(key)
            ident = f"{slug}/p{page}/{role}"
            new_records.append(make_record(
                ident, slug, page, role, round_box(field["box"]),
                str(field["id"]), key, gap))
    return new_records


def mint_class(catalog: dict, tree: pathlib.Path, inventories: dict[str, pathlib.Path],
               wanted: str) -> list[dict]:
    """wanted is comb, xbox, text, or field."""
    new_records: list[dict] = []
    for slug, html_path in fi.iter_bundles(tree):
        fields = fi.collect_fields(html_path.read_text(encoding="utf-8"))
        records = fi.records_for_slug({"records": catalog["records"] + new_records}, slug)
        counters: dict[str, dict[str, int]] = {}
        gap_no_inv = no_inventory_gap(slug, inventories)
        for field in sorted(fields, key=lambda item: (item["page_prefix"], item["box"][1], item["box"][0])):
            if fi.identities_claiming(field, records):
                continue
            page = page_of(str(field["id"]))
            page_counters = counters.setdefault(str(page), {})
            role = classify_role(field, page_counters)
            prefix = role.rsplit("-", 1)[0]
            klass = "xbox" if prefix == "xbox" else (
                "text" if prefix == "text" else (
                    "field" if prefix == "field" else "comb"))
            if klass != wanted:
                # still consume counter? classify already incremented. For skipped
                # classes we must not increment. Revert.
                page_counters[prefix] -= 1
                if page_counters[prefix] == 0:
                    del page_counters[prefix]
                continue
            ident = f"{slug}/p{page}/{role}"
            key, gap = (None, gap_no_inv) if gap_no_inv else default_harvest(slug, inventories)
            new_records.append(make_record(
                ident, slug, page, role, round_box(field["box"]),
                str(field["id"]), key, gap))
            records.append(new_records[-1])
    return new_records


def write_catalog(catalog: dict) -> None:
    fi.DEFAULT_CATALOG.write_text(
        json.dumps(catalog, indent=2) + "\n", encoding="utf-8")


def write_evidence(name: str, payload: dict) -> None:
    path = EVIDENCE / name
    path.write_text(json.dumps(payload, indent=2) + chr(10), encoding="utf-8")


def remint_1701q_spouse(catalog: dict, inventories: dict[str, pathlib.Path]) -> dict:
    """Rewrite the four 1701Q tin-extra records to tin-spouse after a printed caption.

    Catalog size stays 9990. Keys come from harvest_tin against fields.json.
    2316-2021 and 2550-ds-2025 tin-extra records are left gapped.
    """
    slug = "1701q-2018"
    by_role = {
        str(record["role"]): record
        for record in catalog["records"]
        if record.get("bundle_slug") == slug and record.get("role") in TIN_EXTRA_ROLES
    }
    if set(by_role) != set(TIN_EXTRA_ROLES):
        raise ValueError(
            "1701q spouse remint expected roles %s, got %s"
            % (list(TIN_EXTRA_ROLES), sorted(by_role))
        )
    inv = inventory_path_for_slug(slug, inventories)
    if inv is None:
        raise ValueError("1701q-2018 has no fields.json")
    keys = inventory_keys(inv)
    used = used_keys(catalog, slug)
    rewritten: list[dict[str, str]] = []
    for old_role, new_role in zip(TIN_EXTRA_ROLES, TIN_SPOUSE_ROLES):
        record = by_role[old_role]
        key, gap = harvest_tin(keys, used, new_role, 1)
        if not key:
            raise ValueError("%s: %s" % (new_role, gap))
        used.add(key)
        record["id"] = "%s/p1/%s" % (slug, new_role)
        record["role"] = new_role
        record["official_field_key"] = key
        record["official_field_key_gap"] = ""
        rewritten.append(
            {
                "html_id_hint": str(record["html_id_hint"]),
                "id": str(record["id"]),
                "official_field_key": key,
            }
        )
    if len(rewritten) != EXPECTED_1701Q_SPOUSE_REWRITES:
        raise ValueError(
            "1701q spouse remint expected %s rewrites, got %s"
            % (EXPECTED_1701Q_SPOUSE_REWRITES, len(rewritten))
        )
    leftover_extra = [
        str(record["id"])
        for record in catalog["records"]
        if record.get("bundle_slug") == slug and record.get("role") in TIN_EXTRA_ROLES
    ]
    if leftover_extra:
        raise ValueError("1701q tin-extra records remain: %s" % leftover_extra)
    return {
        "title": "1701Q spouse TIN named harvest",
        "date": "2026-08-21",
        "printed_caption": PRINTED_1701Q_SPOUSE_TIN,
        "printed_section": "PART II – BACKGROUND INFORMATION ON SPOUSE",
        "source": "html-frozen/1701q-2018/index.html div.t item 17",
        "rewritten_record_count": len(rewritten),
        "records": rewritten,
        "notes": [
            "tin_roles_for_chain(1701q-2018, 1) now returns tin-spouse-* after quoting Spouse's TIN.",
            "2316-2021 and 2550-ds-2025 tin-extra records stay gapped; they are not spouse.",
            "No official_field_key was invented. Catalog size stays 9990.",
        ],
    }


def remint_false_negatives(catalog: dict, inventories: dict[str, pathlib.Path]) -> dict:
    rewritten: list[str] = []
    by_slug: dict[str, int] = {}
    for record in catalog["records"]:
        if record.get("official_field_key"):
            continue
        if record.get("official_field_key_gap") != GAP_NO_HARVEST:
            continue
        slug = str(record["bundle_slug"])
        if inventory_path_for_slug(slug, inventories) is None:
            continue
        record["official_field_key_gap"] = GAP_NO_UNIQUE
        rewritten.append(str(record["id"]))
        by_slug[slug] = by_slug.get(slug, 0) + 1
    if len(rewritten) != EXPECTED_FALSE_NEGATIVE_REWRITES:
        raise ValueError(
            "false-negative remint expected %s rewrites, got %s"
            % (EXPECTED_FALSE_NEGATIVE_REWRITES, len(rewritten))
        )
    return {
        "title": "Mint-path false-negative remint",
        "source_census": "tools/formgen/corrections/evidence/join-census-20260819.json",
        "rewritten_record_count": len(rewritten),
        "bundles": [
            {"records": by_slug[slug], "slug": slug}
            for slug in sorted(by_slug)
        ],
        "notes": [
            "inventory_path_for_slug now uses join_census file-existence resolution.",
            "Records that claimed no harvested fields.json while inventory exists now carry no unique fields.json key for this box.",
            "No official_field_key was invented. Catalog size stays 9990. Not Stage 3.",
        ],
    }


def self_test() -> int:
    failed = 0

    def check(name: str, held: bool, detail: str = "") -> None:
        nonlocal failed
        if held:
            print("OK    " + name)
        else:
            failed += 1
            extra = (" — " + detail) if detail else ""
            print("FAIL  " + name + extra)

    inventories = load_inventories()
    check(
        "1702ex-2018 vs 1702ex-v2018c is exact",
        inventory_path_for_slug("1702ex-2018", inventories)
        == RULES_FORMS / "1702ex-v2018c" / "fields.json",
    )
    check(
        "extra/2200t-2022 vs 2200t-v2020 is skew",
        inventory_path_for_slug("extra/2200t-2022", inventories)
        == RULES_FORMS / "2200t-v2020" / "fields.json",
    )
    check(
        "2000-dst-2018 resolves overlay without stealing 2000-v2018",
        inventory_path_for_slug("2000-dst-2018", inventories)
        == OVERLAY_FORMS / "2000-dst-v2018" / "fields.json"
        and inventory_path_for_slug("2000-2018", inventories)
        == RULES_FORMS / "2000-v2018" / "fields.json",
    )
    check(
        "1601eq-2019 still resolves to 1601eq-v2018",
        inventory_path_for_slug("1601eq-2019", inventories)
        == RULES_FORMS / "1601eq-v2018" / "fields.json",
    )
    check(
        "1701-2018-attachment stays absent",
        inventory_path_for_slug("1701-2018-attachment", inventories) is None,
    )
    check(
        "1701q-2018 page 1 leftover TIN chain is spouse after printed caption",
        tin_roles_for_chain("1701q-2018", 1) == TIN_SPOUSE_ROLES,
        str(tin_roles_for_chain("1701q-2018", 1)),
    )
    check(
        "2316-2021 page 1 leftover TIN chain stays tin-extra",
        tin_roles_for_chain("2316-2021", 1) == TIN_EXTRA_ROLES,
        str(tin_roles_for_chain("2316-2021", 1)),
    )
    check(
        "2550-ds-2025 page 1 leftover TIN chain stays tin-extra",
        tin_roles_for_chain("2550-ds-2025", 1) == TIN_EXTRA_ROLES,
        str(tin_roles_for_chain("2550-ds-2025", 1)),
    )
    inv_1701q = inventory_path_for_slug("1701q-2018", inventories)
    keys_1701q = inventory_keys(inv_1701q) if inv_1701q else []
    spouse1, spouse1_gap = harvest_tin(keys_1701q, set(), "tin-spouse-1", 1)
    check(
        "1701q harvest_tin spouse-1 is unique frm1701q:txtSpouseTIN1",
        spouse1 == "frm1701q:txtSpouseTIN1" and not spouse1_gap,
        "%s %s" % (spouse1, spouse1_gap),
    )
    print(("FAIL" if failed else "OK"),
          ("%s self-test(s) failed" % failed) if failed else "self-test")
    return 1 if failed else 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--tree", type=pathlib.Path, default=REPO / "forms")
    parser.add_argument("--wave", choices=("i1", "comb", "xbox", "text", "field"))
    parser.add_argument(
        "--remint-false-negatives",
        action="store_true",
        help="rewrite mint-path false-negative gaps; do not invent keys",
    )
    parser.add_argument(
        "--remint-1701q-spouse",
        action="store_true",
        help="rewrite 1701Q tin-extra records to tin-spouse after printed Spouse's TIN",
    )
    args = parser.parse_args()
    if args.self_test:
        return self_test()
    if args.remint_false_negatives and args.remint_1701q_spouse:
        parser.error("choose one remint")
    if args.remint_false_negatives or args.remint_1701q_spouse:
        if args.wave:
            parser.error("remint flags do not take --wave")
        catalog, errors = fi.load_catalog(fi.DEFAULT_CATALOG)
        if errors:
            print(chr(10).join(errors))
            return 1
        before = len(catalog["records"])
        inventories = load_inventories()
        try:
            if args.remint_1701q_spouse:
                payload = remint_1701q_spouse(catalog, inventories)
                evidence_name = "tin-1701q-spouse-20260821.json"
                label = "remint-1701q-spouse"
            else:
                payload = remint_false_negatives(catalog, inventories)
                evidence_name = "join-false-negative-remint-20260819.json"
                label = "remint-false-negatives"
        except ValueError as exc:
            print("FAIL  %s" % exc)
            return 1
        errors = fi.check_catalog(catalog, fi.DEFAULT_CATALOG)
        if errors:
            print(chr(10).join(errors[:20]))
            print("%s catalog error(s)" % len(errors))
            return 1
        if len(catalog["records"]) != before:
            print("FAIL  remint changed catalog size")
            return 1
        write_catalog(catalog)
        write_evidence(evidence_name, payload)
        rewritten = payload["rewritten_record_count"]
        print("%s: %s records rewritten, catalog %s wrote %s" % (label, rewritten, before, evidence_name))
        return 0
    if not args.wave:
        parser.error("--wave is required unless --self-test or a remint flag")
    tree = args.tree if args.tree.is_absolute() else (pathlib.Path.cwd() / args.tree)
    catalog, errors = fi.load_catalog(fi.DEFAULT_CATALOG)
    if errors:
        print(chr(10).join(errors))
        return 1
    inventories = load_inventories()
    before = len(catalog["records"])
    if args.wave == "i1":
        added = mint_i1(catalog, tree, inventories)
        evidence_name = "tin-identity-leftovers-20260818.json"
        payload = {
            "title": "TIN leftover identities (I1)",
            "date": "2026-08-18",
            "new_record_count": len(added),
            "ids": [record["id"] for record in added],
            "notes": [
                "extra/1801-2018 tin-1 is a mixed caption+tin-1 cell; catalogued as tin-strip from the emitted box.",
                "tin-2/3/branch on 1801 uniquely resolve against the 2026-08-08 census boxes.",
                "Extra 3+3+3+N HTML chains that uniquely resolve are catalogued; eight PDF-unmeasurable bundles still emit no such chain.",
                "Not Stage 3. Nothing writes name= official keys.",
            ],
        }
    else:
        added = mint_class(catalog, tree, inventories, args.wave)
        evidence_name = "identity-%s-20260818.json" % args.wave
        payload = {
            "title": "Fillable identity class %s" % args.wave,
            "date": "2026-08-18",
            "new_record_count": len(added),
            "bundle_count": len({record["bundle_slug"] for record in added}),
            "notes": [
                "source_printed_box_pt is the emitted cell (C02 pattern).",
                "official_field_key harvested only when unique; otherwise an honest gap.",
                "Not Stage 3. Nothing writes name= official keys.",
            ],
        }
    catalog["records"].extend(added)
    errors = fi.check_catalog(catalog, fi.DEFAULT_CATALOG)
    if errors:
        print(chr(10).join(errors[:20]))
        print("%s catalog error(s)" % len(errors))
        return 1
    write_catalog(catalog)
    write_evidence(evidence_name, payload)
    after = len(catalog["records"])
    print("%s: %s -> %s (+%s) wrote %s" % (args.wave, before, after, len(added), evidence_name))
    return 0


if __name__ == "__main__":
    sys.exit(main())
