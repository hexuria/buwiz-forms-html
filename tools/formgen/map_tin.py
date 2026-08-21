#!/usr/bin/env python3
"""Stage 3 TIN mapper: copy R1 ``official_field_key`` onto input ``name=``.

Fail-closed. The only join that may write ``name="frm…"`` is R1: a catalog
record whose ``official_field_key`` is a non-empty unique harvest key.
Nothing here invents a key, maps a gapped record, or writes onto ``forms/``.
Resolution is ``field_identity.resolve_record``; any status other than
``resolved`` aborts before a byte is written.

``emit.py`` stamps ``name="<cell id>"`` on every live input of that cell
(comb slots share the cell name; ``id`` carries ``-sN`` / ``-iN``). This
mapper rewrites that ``name=`` to the harvested key and leaves ``id=`` and
``data-field-name`` alone. ``data-field-name="p1c13"`` contains the
substring ``name="p1c13"``; a global replace would corrupt the div.

G11 mixed combs (pre-printed branch digits, no empty slots) resolve as
the identity but have no input. They are counted, not rewritten, and
pinned at ``EXPECTED_UNWRITABLE_MIXED``. A non-mixed field with no input
is still a refusal.

``correct.py --verify`` must run on the *unmapped* Stage 2 tree. Mapping
overlays ``name=`` and the Stage 2 byte contract will fail until the
tree is rebuilt. Rebuild, verify, then map.

Usage:
    python3 tools/formgen/map_tin.py --self-test
    python3 tools/formgen/map_tin.py --tree forms-corrected
    python3 tools/formgen/map_tin.py --tree forms-corrected --write
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
import tempfile


HERE = pathlib.Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import field_identity as fi  # noqa: E402
import join_census as jc  # noqa: E402

REPO = HERE.parent.parent
DEFAULT_CATALOG = fi.DEFAULT_CATALOG
DEFAULT_TREE = REPO / "forms-corrected"
CORRECTED_TREE_NAME = "forms-corrected"
STAGE1_TREE_NAME = "forms"
FORBIDDEN_KEY_CHARS = frozenset("\"<>&")
# Pin, not a census. Must equal join_census.ACCEPTANCE["R1_keyed_1to1"].
EXPECTED_R1 = 167
# G11: sheet pre-prints the branch digits, emit keeps a mixed comb and
# refuses empty slots. Those cells resolve as the identity but have no
# input to name. A field (not mixed) with zero inputs is still an error.
# 1701Q spouse branch (p1c72) is the same G11 mixed case as taxpayer branch.
EXPECTED_UNWRITABLE_MIXED = 16
EXPECTED_WRITABLE_R1 = EXPECTED_R1 - EXPECTED_UNWRITABLE_MIXED

INPUT_TAG_RE = re.compile(r"<input\b[^>]*>")
# Negative lookbehind so data-field-name="p1c13" is not a name= hit.
NAME_ATTR_RE = re.compile(r'(?<![A-Za-z0-9:_-])name="([^"]*)"')


class MapError(Exception):
    """A refused map. Nothing has been written to the tree."""

    def __init__(self, code: str, detail: str, errors: list[str] | None = None) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.errors = list(errors) if errors is not None else [detail]


def r1_records(catalog: dict) -> list[dict]:
    return [record for record in catalog.get("records", []) if jc.claimed_key(record)]


def unique_key_errors(records: list[dict]) -> list[str]:
    seen: dict[str, str] = {}
    errors: list[str] = []
    for record in records:
        key = jc.claimed_key(record)
        if key is None:
            continue
        ident = str(record.get("id"))
        if any(char in key for char in FORBIDDEN_KEY_CHARS):
            errors.append(f"{ident}: official_field_key {key!r} contains a forbidden character")
            continue
        previous = seen.get(key)
        if previous is not None:
            errors.append(
                f"{ident}: official_field_key {key!r} already owned by {previous}"
            )
            continue
        seen[key] = ident
    return errors


def tree_is_stage1(tree: pathlib.Path) -> bool:
    try:
        resolved = tree.resolve()
    except OSError:
        return tree.name == STAGE1_TREE_NAME
    forms = REPO / STAGE1_TREE_NAME
    try:
        if resolved == forms.resolve():
            return True
    except OSError:
        pass
    return resolved.name == STAGE1_TREE_NAME


def assert_bindable_tree(tree: pathlib.Path, *, require_corrected_name: bool) -> None:
    if not tree.is_dir():
        raise MapError("missing-tree", f"tree {tree} is not a directory")
    if tree_is_stage1(tree):
        raise MapError(
            "stage1-tree",
            "Stage 3 binds forms-corrected/ only; refused "
            f"{tree}",
        )
    if require_corrected_name and tree.resolve().name != CORRECTED_TREE_NAME:
        raise MapError(
            "unbound-tree",
            f"Stage 3 binds {CORRECTED_TREE_NAME}/ only; refused {tree}",
        )


def rewrite_input_names(html: str, cell_id: str, key: str) -> tuple[str, dict[str, int]]:
    """Rewrite input name=cell_id to name=key. Leaves id= and data-field-name."""
    stats = {"rewritten": 0, "already": 0}

    def repl(match: re.Match[str]) -> str:
        tag = match.group(0)
        name_match = NAME_ATTR_RE.search(tag)
        if name_match is None:
            return tag
        current = name_match.group(1)
        if current == cell_id:
            stats["rewritten"] += 1
            start, end = name_match.span(1)
            return tag[:start] + key + tag[end:]
        if current == key:
            stats["already"] += 1
            return tag
        return tag

    return INPUT_TAG_RE.sub(repl, html), stats


def _html_path(tree: pathlib.Path, slug: str) -> pathlib.Path:
    return tree / slug / "index.html"


def _is_unwritable_mixed(html: str, html_id: str) -> bool:
    for field in fi.collect_fields(html):
        if field["id"] != html_id:
            continue
        return field["cell_kind"] == "mixed" and field["field_kind"] == "comb"
    return False


def map_tree(
    catalog: dict,
    tree: pathlib.Path,
    *,
    write: bool,
    require_corrected_name: bool = True,
    pin_unwritable: int | None = None,
) -> dict[str, object]:
    """Resolve every R1 record and rewrite its input name=. Never partial."""
    assert_bindable_tree(tree, require_corrected_name=require_corrected_name)
    records = r1_records(catalog)
    if len(records) != EXPECTED_R1:
        raise MapError(
            "r1-count",
            f"R1 count {len(records)} != pin {EXPECTED_R1}",
        )
    key_errors = unique_key_errors(records)
    if key_errors:
        raise MapError("duplicate-key", key_errors[0], key_errors)

    errors: list[str] = []
    plan: list[dict[str, object]] = []
    claimed_cells: dict[tuple[str, str], str] = {}
    for record in records:
        ident = str(record["id"])
        key = jc.claimed_key(record)
        assert key is not None
        resolved = fi.resolve_record(record, tree)
        status = resolved["status"]
        if status != "resolved":
            errors.append(f"{ident}: {status}: {resolved['reason']}")
            continue
        html_id = str(resolved["resolved_html_id"])
        cell_key = (str(record["bundle_slug"]), html_id)
        previous = claimed_cells.get(cell_key)
        if previous is not None:
            errors.append(
                f"{ident}: cell {html_id} already claimed by {previous}"
            )
            continue
        claimed_cells[cell_key] = ident
        plan.append({
            "id": ident,
            "bundle_slug": record["bundle_slug"],
            "html_id": html_id,
            "official_field_key": key,
        })

    if errors:
        raise MapError("unresolved", errors[0], errors)

    pending: dict[pathlib.Path, str] = {}
    dirty: set[pathlib.Path] = set()
    mapped: list[dict[str, object]] = []
    rewrite_errors: list[str] = []
    for item in plan:
        path = _html_path(tree, str(item["bundle_slug"]))
        if path not in pending:
            try:
                pending[path] = path.read_text(encoding="utf-8")
            except OSError as exc:
                rewrite_errors.append(f"{item['id']}: cannot read {path}: {exc}")
                continue
        html, stats = rewrite_input_names(
            pending[path], str(item["html_id"]), str(item["official_field_key"]),
        )
        rewritten = int(stats["rewritten"])
        already = int(stats["already"])
        if rewritten and already:
            rewrite_errors.append(
                f"{item['id']}: mixed mapped/unmapped inputs on {item['html_id']}"
            )
            continue
        if rewritten + already == 0:
            if _is_unwritable_mixed(pending[path], str(item["html_id"])):
                mapped.append({
                    **item,
                    "inputs": 0,
                    "state": "unwritable-mixed",
                })
                continue
            rewrite_errors.append(
                f"{item['id']}: no input name={item['html_id']!r} or "
                f"name={item['official_field_key']!r}"
            )
            continue
        pending[path] = html
        if rewritten:
            dirty.add(path)
        mapped.append({
            **item,
            "inputs": rewritten + already,
            "state": "already" if already else "rewritten",
        })

    if rewrite_errors:
        raise MapError("no-inputs", rewrite_errors[0], rewrite_errors)

    unwritable = sum(1 for item in mapped if item["state"] == "unwritable-mixed")
    writable = sum(1 for item in mapped if item["state"] in ("rewritten", "already"))
    if pin_unwritable is not None and unwritable != pin_unwritable:
        raise MapError(
            "unwritable-count",
            f"unwritable-mixed {unwritable} != pin {pin_unwritable}",
        )

    if write:
        for path in dirty:
            path.write_text(pending[path], encoding="utf-8")

    inputs = sum(int(item["inputs"]) for item in mapped)  # type: ignore[arg-type]
    return {
        "tree": str(tree),
        "wrote": write,
        "r1": len(mapped),
        "writable": writable,
        "unwritable_mixed": unwritable,
        "files_touched": len(dirty),
        "inputs": inputs,
        "records": mapped,
    }


def dump_json(payload: object) -> str:
    return json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=True) + "\n"


def attach_batch_binding(report: dict[str, object], tree: pathlib.Path) -> dict[str, object]:
    """Copy source_batch / source_commit off the Stage 2 manifest when present."""
    manifest_path = tree.parent / f"{tree.name}.manifest.json"
    if not manifest_path.is_file():
        return report
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return report
    if not isinstance(manifest, dict):
        return report
    attached = dict(report)
    if "source_batch" in manifest:
        attached["source_batch"] = manifest["source_batch"]
    if "source_commit" in manifest:
        attached["source_commit"] = manifest["source_commit"]
    return attached


def print_report(report: dict[str, object]) -> None:
    print(f"OK    R1 {report['r1']}")
    print(f"OK    writable {report['writable']}")
    print(f"OK    unwritable-mixed {report['unwritable_mixed']}")
    print(f"OK    files {report['files_touched']}")
    print(f"OK    inputs {report['inputs']}")
    print(f"OK    wrote {report['wrote']}")


# ---------------------------------------------------------------------------
# self-test
# ---------------------------------------------------------------------------


def _write_html(path: pathlib.Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "<!DOCTYPE html><html><body>" + body + "</body></html>",
        encoding="utf-8",
    )


def _comb(
    html_id: str,
    left: float,
    top: float,
    width: float,
    height: float,
    slots: int,
    *,
    name: str | None = None,
) -> str:
    name_value = html_id if name is None else name
    inner = []
    for index in range(slots):
        inner.append(
            f'<div class="s" data-slot="{index}">'
            f'<input type="text" class="fi fc" id="{html_id}-s{index}" '
            f'name="{name_value}" data-slot-index="{index}" maxlength="1"></div>'
        )
    return (
        f'<div id="{html_id}" class="c f" data-cell-kind="field" '
        f'data-field-kind="comb" data-field-name="{html_id}" '
        f'style="left:{left}pt;top:{top}pt;width:{width}pt;height:{height}pt">'
        + "".join(inner)
        + "</div>"
    )


def _mixed_comb_empty(
    html_id: str,
    left: float,
    top: float,
    width: float,
    height: float,
) -> str:
    return (
        f'<div id="{html_id}" class="c f" data-cell-kind="mixed" '
        f'data-field-kind="comb" data-field-name="{html_id}" data-comb-slots="5" '
        f'style="left:{left}pt;top:{top}pt;width:{width}pt;height:{height}pt"></div>'
    )


def _text_regions(
    html_id: str,
    left: float,
    top: float,
    width: float,
    height: float,
    regions: int,
) -> str:
    inner = []
    for index in range(regions):
        inner.append(
            f'<input type="text" class="fi" id="{html_id}-i{index}" '
            f'name="{html_id}" maxlength="1">'
        )
    return (
        f'<div id="{html_id}" class="c f" data-cell-kind="field" '
        f'data-field-kind="text" data-field-name="{html_id}" '
        f'style="left:{left}pt;top:{top}pt;width:{width}pt;height:{height}pt">'
        + "".join(inner)
        + "</div>"
    )


def _sample_record(**overrides: object) -> dict:
    record: dict[str, object] = {
        "id": "fixture-form/p1/tin-branch",
        "bundle_slug": "fixture-form",
        "page": 1,
        "role": "tin-branch",
        "source_printed_box_pt": [180.24, 118.8, 213.12, 134.4],
        "official_field_key": "frmFixture:txtBranchCode",
        "official_field_key_gap": "",
        "html_id_hint": "p1c13",
        "match": {"kind": "field", "tolerance_pt": 0.25, "cardinality": "exactly-one"},
        "correction_id": None,
    }
    record.update(overrides)
    return record


def _catalog(records: list[dict]) -> dict:
    # Pad to EXPECTED_R1 with unique synthetic R1 records so the live pin
    # is exercised, not waived, on every fixture.
    padded = list(records)
    used_ids = {str(record["id"]) for record in padded}
    used_keys = {jc.claimed_key(record) for record in padded}
    index = 0
    while len([record for record in padded if jc.claimed_key(record)]) < EXPECTED_R1:
        slug = f"pad-{index}"
        ident = f"{slug}/p1/tin-1"
        key = f"frmPad:txtTIN{index}"
        index += 1
        if ident in used_ids or key in used_keys:
            continue
        used_ids.add(ident)
        used_keys.add(key)
        padded.append(_sample_record(
            id=ident,
            bundle_slug=slug,
            html_id_hint="p1c1",
            official_field_key=key,
            source_printed_box_pt=[10.0, 10.0, 20.0, 20.0],
        ))
    return {"schema_version": "1.0.0-provisional", "records": padded}


def _pad_html(tree: pathlib.Path, catalog: dict) -> None:
    """Emit a one-slot comb per padded R1 record so resolve+rewrite can run."""
    for record in catalog["records"]:
        if not str(record["bundle_slug"]).startswith("pad-"):
            continue
        hint = str(record["html_id_hint"])
        box = record["source_printed_box_pt"]
        x0, y0, x1, y1 = (float(v) for v in box)  # type: ignore[misc]
        _write_html(
            tree / str(record["bundle_slug"]) / "index.html",
            _comb(hint, x0, y0, x1 - x0, y1 - y0, 1),
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

    check(
        "EXPECTED_R1 matches join_census pin",
        EXPECTED_R1 == jc.ACCEPTANCE["R1_keyed_1to1"],
        f"{EXPECTED_R1} vs {jc.ACCEPTANCE['R1_keyed_1to1']}",
    )
    check(
        "writable + unwritable-mixed pins sum to R1",
        EXPECTED_WRITABLE_R1 + EXPECTED_UNWRITABLE_MIXED == EXPECTED_R1
        and EXPECTED_UNWRITABLE_MIXED == 16,
        f"{EXPECTED_WRITABLE_R1}+{EXPECTED_UNWRITABLE_MIXED}",
    )

    shipped, catalog_errors = fi.load_catalog(DEFAULT_CATALOG)
    check("shipped catalog is well formed", not catalog_errors, "; ".join(catalog_errors[:3]))
    if shipped:
        keyed = r1_records(shipped)
        keys = [jc.claimed_key(record) for record in keyed]
        check(
            f"shipped catalog R1 count is {EXPECTED_R1}",
            len(keyed) == EXPECTED_R1,
            str(len(keyed)),
        )
        check(
            "shipped R1 keys are unique",
            len(keys) == len(set(keys)) and not unique_key_errors(keyed),
        )
        check(
            "every shipped R1 key is a harvested frm… key, not invented empty",
            all(isinstance(key, str) and key.startswith("frm") and ":" in key for key in keys),
        )

    printed = (180.24, 118.8, 213.12, 134.4)
    x0, y0, x1, y1 = printed
    record = _sample_record()
    gapped = _sample_record(
        id="fixture-form/p1/text-1",
        role="text-1",
        official_field_key=None,
        official_field_key_gap=jc.GAP_NO_UNIQUE,
        html_id_hint="p1c99",
        source_printed_box_pt=[300.0, 118.8, 340.0, 134.4],
    )

    with tempfile.TemporaryDirectory() as tmp:
        tree = pathlib.Path(tmp) / "forms-corrected"
        neighbour = _comb("p1c99", 300.0, y0, 40.0, y1 - y0, 3)
        target = _comb("p1c13", x0, y0, x1 - x0, y1 - y0, 5)
        _write_html(tree / "fixture-form" / "index.html", neighbour + target)
        catalog = _catalog([record, gapped])
        _pad_html(tree, catalog)

        report = map_tree(catalog, tree, write=True, require_corrected_name=True)
        html = (tree / "fixture-form" / "index.html").read_text(encoding="utf-8")
        target_names = []
        neighbour_names = []
        for tag in INPUT_TAG_RE.findall(html):
            name_match = NAME_ATTR_RE.search(tag)
            if name_match is None:
                continue
            if 'id="p1c13-' in tag:
                target_names.append(name_match.group(1))
            if 'id="p1c99-' in tag:
                neighbour_names.append(name_match.group(1))
        check(
            "R1 comb slots share the harvested name, keep -sN ids",
            target_names == ["frmFixture:txtBranchCode"] * 5
            and html.count('id="p1c13-s0"') == 1
            and html.count('id="p1c13-s4"') == 1,
            str(target_names),
        )
        check(
            "data-field-name keeps the cell id (substring trap)",
            'data-field-name="p1c13"' in html
            and 'data-field-name="frmFixture:txtBranchCode"' not in html,
        )
        check(
            "gapped neighbour is not mapped",
            neighbour_names == ["p1c99"] * 3,
            str(neighbour_names),
        )
        check("report counts this R1 record", report["r1"] == EXPECTED_R1, str(report["r1"]))

        again = map_tree(catalog, tree, write=True, require_corrected_name=True)
        check(
            "a second run is idempotent (already mapped)",
            again["r1"] == EXPECTED_R1
            and all(item["state"] == "already"  # type: ignore[index]
                    for item in again["records"]  # type: ignore[union-attr]
                    if item["id"] == record["id"]),
        )

        forms = pathlib.Path(tmp) / "forms"
        _write_html(forms / "fixture-form" / "index.html", target)
        try:
            map_tree(catalog, forms, write=False, require_corrected_name=False)
            refused = None
        except MapError as exc:
            refused = exc
        check(
            "refuses a tree named forms/",
            refused is not None and refused.code == "stage1-tree",
            "did not refuse" if refused is None else f"{refused.code}: {refused.detail}",
        )

        other = pathlib.Path(tmp) / "other-tree"
        _write_html(other / "fixture-form" / "index.html", target)
        _pad_html(other, catalog)
        try:
            map_tree(catalog, other, write=False, require_corrected_name=True)
            unbound = None
        except MapError as exc:
            unbound = exc
        check(
            "refuses a tree not named forms-corrected/",
            unbound is not None and unbound.code == "unbound-tree",
            "did not refuse" if unbound is None else f"{unbound.code}: {unbound.detail}",
        )

        repo_forms = REPO / "forms"
        if repo_forms.is_dir() and shipped:
            try:
                map_tree(shipped, repo_forms, write=False, require_corrected_name=True)
                repo_refused = None
            except MapError as exc:
                repo_refused = exc
            check(
                "refuses the repository forms/ tree",
                repo_refused is not None and repo_refused.code == "stage1-tree",
                "did not refuse" if repo_refused is None else
                f"{repo_refused.code}: {repo_refused.detail}",
            )

        # Region inputs (C01 tin-1 style) + substring trap on the wrapping div.
        region_tree = pathlib.Path(tmp) / "region-root" / "forms-corrected"
        tin1 = _sample_record(
            id="fixture-form/p1/tin-1",
            role="tin-1",
            html_id_hint="p1c127",
            official_field_key="frmFixture:txtTIN1",
            source_printed_box_pt=[66.0, 118.8, 99.84, 133.68],
        )
        region_catalog = _catalog([tin1])
        _write_html(
            region_tree / "fixture-form" / "index.html",
            _text_regions("p1c127", 66.0, 118.8, 33.84, 14.88, 3),
        )
        _pad_html(region_tree, region_catalog)
        region_report = map_tree(
            region_catalog, region_tree, write=True, require_corrected_name=True,
        )
        region_html = (region_tree / "fixture-form" / "index.html").read_text(encoding="utf-8")
        check(
            "region inputs rewrite name= and keep -iN ids",
            region_html.count('name="frmFixture:txtTIN1"') == 3
            and 'id="p1c127-i0"' in region_html
            and 'id="p1c127-i2"' in region_html
            and 'data-field-name="p1c127"' in region_html,
            region_html[region_html.find("p1c127"):region_html.find("p1c127") + 350]
            if "p1c127" in region_html else "missing",
        )
        check("region map counted 3 inputs on the live record",
              any(item["id"] == tin1["id"] and item["inputs"] == 3  # type: ignore[operator]
                  for item in region_report["records"]),  # type: ignore[union-attr]
              str(region_report["records"][:1]))

        # Unresolved identity: printed box empty.
        missing_tree = pathlib.Path(tmp) / "forms-corrected"
        _write_html(missing_tree / "fixture-form" / "index.html", neighbour)
        missing_catalog = _catalog([record])
        _pad_html(missing_tree, missing_catalog)
        try:
            map_tree(missing_catalog, missing_tree, write=False)
            missing_exc = None
        except MapError as exc:
            missing_exc = exc
        check(
            "unresolved identity refuses the map",
            missing_exc is not None and missing_exc.code == "unresolved",
            "did not refuse" if missing_exc is None else
            f"{missing_exc.code}: {missing_exc.detail}",
        )
        check(
            "unresolved refusal writes nothing",
            'name="frmFixture:txtBranchCode"' not in
            (missing_tree / "fixture-form" / "index.html").read_text(encoding="utf-8"),
        )

        stale = dict(record)
        stale["html_id_hint"] = "p1c99"
        stale_tree = pathlib.Path(tmp) / "stale-root" / "forms-corrected"
        _write_html(stale_tree / "fixture-form" / "index.html", target)
        stale_catalog = _catalog([stale])
        _pad_html(stale_tree, stale_catalog)
        try:
            map_tree(stale_catalog, stale_tree, write=False)
            stale_exc = None
        except MapError as exc:
            stale_exc = exc
        check(
            "html_id_hint_stale refuses the map (no silent remap)",
            stale_exc is not None and stale_exc.code == "unresolved"
            and "html_id_hint_stale" in stale_exc.detail,
            "did not refuse" if stale_exc is None else
            f"{stale_exc.code}: {stale_exc.detail}",
        )

        mixed_tree = pathlib.Path(tmp) / "mixed-root" / "forms-corrected"
        mixed_body = _comb("p1c13", x0, y0, x1 - x0, y1 - y0, 2, name="p1c13")
        mixed_body = mixed_body.replace(
            'id="p1c13-s1" name="p1c13"',
            'id="p1c13-s1" name="frmFixture:txtBranchCode"',
            1,
        )
        _write_html(mixed_tree / "fixture-form" / "index.html", mixed_body)
        mixed_catalog = _catalog([record])
        _pad_html(mixed_tree, mixed_catalog)
        try:
            map_tree(mixed_catalog, mixed_tree, write=False)
            mixed_exc = None
        except MapError as exc:
            mixed_exc = exc
        check(
            "mixed mapped/unmapped inputs on one cell refuse",
            mixed_exc is not None and mixed_exc.code == "no-inputs"
            and "mixed" in mixed_exc.detail,
            "did not refuse" if mixed_exc is None else
            f"{mixed_exc.code}: {mixed_exc.detail}",
        )

        empty_tree = pathlib.Path(tmp) / "empty-root" / "forms-corrected"
        empty_cell = (
            f'<div id="p1c13" class="c f" data-cell-kind="field" '
            f'data-field-kind="comb" data-field-name="p1c13" '
            f'style="left:{x0}pt;top:{y0}pt;width:{x1 - x0}pt;height:{y1 - y0}pt">'
            f"</div>"
        )
        _write_html(empty_tree / "fixture-form" / "index.html", empty_cell)
        empty_catalog = _catalog([record])
        _pad_html(empty_tree, empty_catalog)
        try:
            map_tree(empty_catalog, empty_tree, write=False)
            empty_exc = None
        except MapError as exc:
            empty_exc = exc
        check(
            "a resolved field cell with no inputs refuses",
            empty_exc is not None and empty_exc.code == "no-inputs",
            "did not refuse" if empty_exc is None else
            f"{empty_exc.code}: {empty_exc.detail}",
        )

        mixed_empty_tree = pathlib.Path(tmp) / "g11-root" / "forms-corrected"
        _write_html(
            mixed_empty_tree / "fixture-form" / "index.html",
            _mixed_comb_empty("p1c13", x0, y0, x1 - x0, y1 - y0),
        )
        mixed_empty_catalog = _catalog([record])
        _pad_html(mixed_empty_tree, mixed_empty_catalog)
        mixed_empty_report = map_tree(
            mixed_empty_catalog, mixed_empty_tree, write=True, pin_unwritable=1,
        )
        mixed_empty_html = (
            mixed_empty_tree / "fixture-form" / "index.html"
        ).read_text(encoding="utf-8")
        check(
            "G11 mixed comb with no inputs is unwritable-mixed, not a rewrite",
            mixed_empty_report["unwritable_mixed"] == 1
            and mixed_empty_report["writable"] == EXPECTED_R1 - 1
            and any(
                item["id"] == record["id"]
                and item["state"] == "unwritable-mixed"
                for item in mixed_empty_report["records"]  # type: ignore[union-attr]
            )
            and 'data-field-name="p1c13"' in mixed_empty_html
            and 'name="frmFixture:txtBranchCode"' not in mixed_empty_html,
            str({k: mixed_empty_report[k] for k in
                 ("writable", "unwritable_mixed", "r1")}),
        )
        try:
            map_tree(
                mixed_empty_catalog, mixed_empty_tree, write=False, pin_unwritable=0,
            )
            pin_exc = None
        except MapError as exc:
            pin_exc = exc
        check(
            "unwritable-mixed count off the pin refuses",
            pin_exc is not None and pin_exc.code == "unwritable-count",
            "did not refuse" if pin_exc is None else
            f"{pin_exc.code}: {pin_exc.detail}",
        )

        dup = _sample_record(
            id="fixture-form/p1/tin-1",
            html_id_hint="p1c11",
            official_field_key="frmFixture:txtBranchCode",
            source_printed_box_pt=[132.0, 118.8, 165.0, 134.4],
        )
        check(
            "duplicate official_field_key is refused before any write",
            bool(unique_key_errors([record, dup])),
        )
        dup_tree = pathlib.Path(tmp) / "dup-root" / "forms-corrected"
        _write_html(
            dup_tree / "fixture-form" / "index.html",
            target + _comb("p1c11", 132.0, y0, 33.0, y1 - y0, 3),
        )
        dup_catalog = _catalog([record, dup])
        _pad_html(dup_tree, dup_catalog)
        try:
            map_tree(dup_catalog, dup_tree, write=False)
            dup_exc = None
        except MapError as exc:
            dup_exc = exc
        check(
            "two R1 records sharing a key refuse the map",
            dup_exc is not None and dup_exc.code == "duplicate-key",
            "did not refuse" if dup_exc is None else
            f"{dup_exc.code}: {dup_exc.detail}",
        )

        collide = _sample_record(
            id="fixture-form/p1/tin-1",
            html_id_hint="p1c13",
            official_field_key="frmFixture:txtTIN1",
            source_printed_box_pt=list(printed),
        )
        collide_tree = pathlib.Path(tmp) / "collide-root" / "forms-corrected"
        _write_html(collide_tree / "fixture-form" / "index.html", target)
        collide_catalog = _catalog([record, collide])
        _pad_html(collide_tree, collide_catalog)
        try:
            map_tree(collide_catalog, collide_tree, write=False)
            collide_exc = None
        except MapError as exc:
            collide_exc = exc
        check(
            "two R1 records resolving to the same cell refuse",
            collide_exc is not None and collide_exc.code == "unresolved"
            and "already claimed" in collide_exc.detail,
            "did not refuse" if collide_exc is None else
            f"{collide_exc.code}: {collide_exc.detail}",
        )

        short_catalog = {"schema_version": "1.0.0-provisional", "records": [record]}
        short_tree = pathlib.Path(tmp) / "short-root" / "forms-corrected"
        _write_html(short_tree / "fixture-form" / "index.html", target)
        try:
            map_tree(short_catalog, short_tree, write=False)
            short_exc = None
        except MapError as exc:
            short_exc = exc
        check(
            "R1 count off the pin refuses (does not map a partial set)",
            short_exc is not None and short_exc.code == "r1-count",
            "did not refuse" if short_exc is None else
            f"{short_exc.code}: {short_exc.detail}",
        )

        quoted = _sample_record(official_field_key='frmFixture:txt"TIN')
        check(
            "a key containing a quote is refused",
            bool(unique_key_errors([quoted])),
        )

        # Dry-run computes the rewrite but does not persist it.
        dry_tree = pathlib.Path(tmp) / "dry-root" / "forms-corrected"
        _write_html(dry_tree / "fixture-form" / "index.html", target)
        dry_catalog = _catalog([record])
        _pad_html(dry_tree, dry_catalog)
        dry_report = map_tree(dry_catalog, dry_tree, write=False)
        dry_html = (dry_tree / "fixture-form" / "index.html").read_text(encoding="utf-8")
        dry_input_names = [
            match.group(1)
            for tag in INPUT_TAG_RE.findall(dry_html)
            if "p1c13-" in tag
            for match in [NAME_ATTR_RE.search(tag)]
            if match
        ]
        check(
            "dry-run reports the rewrite and writes nothing",
            dry_report["wrote"] is False
            and dry_report["r1"] == EXPECTED_R1
            and dry_input_names == ["p1c13"] * 5
            and 'data-field-name="p1c13"' in dry_html,
            str({"wrote": dry_report["wrote"], "r1": dry_report["r1"],
                 "names": dry_input_names}),
        )

        # A non-R1 record must never produce a name="frm…" even if we tried.
        check(
            "gapped records are excluded from r1_records",
            r1_records({"records": [record, gapped]}) == [record]
            and r1_records({"records": [gapped]}) == [],
        )

    print("FAIL" if failed else "OK",
          f"{failed} self-test(s) failed" if failed else "self-test")
    return 1 if failed else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--catalog", type=pathlib.Path, default=DEFAULT_CATALOG)
    parser.add_argument("--tree", type=pathlib.Path, default=DEFAULT_TREE)
    parser.add_argument(
        "--write",
        action="store_true",
        help="persist name= rewrites; default is a dry-run",
    )
    parser.add_argument("--out", type=pathlib.Path, default=None)
    args = parser.parse_args()
    if args.self_test:
        return self_test()
    catalog, errors = fi.load_catalog(args.catalog)
    if errors:
        for error in errors:
            print(f"FAIL  {error}")
        print(f"{len(errors)} catalog error(s)")
        return 1
    tree = args.tree
    if not tree.is_absolute():
        tree = (pathlib.Path.cwd() / tree).resolve()
    try:
        report = map_tree(
            catalog, tree, write=args.write,
            pin_unwritable=EXPECTED_UNWRITABLE_MIXED,
        )
    except MapError as exc:
        for error in exc.errors:
            print(f"FAIL  {error}")
        print(f"{len(exc.errors)} map error(s) [{exc.code}]")
        return 1
    print_report(report)
    report = attach_batch_binding(report, tree)
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(dump_json(report), encoding="utf-8")
        print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
