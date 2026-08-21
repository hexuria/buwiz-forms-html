#!/usr/bin/env python3
"""Durable field identity: a name that survives `p1cN` renumbering.

    STAGE 1  GENERATE   forms/                 (batch-versioned)
    STAGE 2  CORRECT    forms-corrected/
    IDENTITY            tools/formgen/identity  (this file)
    STAGE 3  MAP        identity → XML key      (not this file)

`p1c13` is a reading-order counter. `p1@180.24,118.80,213.12,134.40` is a
quantised bbox. Both move when the lattice changes (PLAN.md risk R2). A field
identity is a published id such as `2550m-2007/p1/tin-branch`, bound to the
printed box on the pinned PDF. The HTML cell id is a hint.

Resolution puts the center of an emitted fillable field inside that
printed box. Fillable means `data-cell-kind` is `field` or `mixed` and
`data-field-kind` is set (comb or text). G11 mixed combs are the branch
identity when the sheet pre-prints `000` and emit refuses empty slots.
Exactly one hit, whose `id` equals the hint, is success. A unique hit
with a different `id` is `html_id_hint_stale` — update the catalog in the
same commit as the batch; do not mint a new identity. Zero or two hits
cannot be resolved and must not be guessed. Raw overlap is not the test:
Stage 2 reflow makes neighbouring TIN groups nick each other's old edges.

This is not Stage 3: nothing writes `name="frm2550m:txtBranchCode"`.
This is not verification of C01–C07: overlap is not `expected_effect`.

Usage:
    python3 tools/formgen/field_identity.py --self-test
    python3 tools/formgen/field_identity.py check --tree forms-corrected
    python3 tools/formgen/field_identity.py check --tree forms
    python3 tools/formgen/field_identity.py coverage --tree forms
    python3 tools/formgen/field_identity.py ledger-check --tree forms
    python3 tools/formgen/field_identity.py ledger-rewrite --tree forms --write
"""

from __future__ import annotations

import argparse
import html.parser
import json
import pathlib
import re
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
IDENTITY_DIR = HERE / "identity"
DEFAULT_CATALOG = IDENTITY_DIR / "catalog.json"
REPO = HERE.parent.parent

REQUIRED_RECORD_KEYS = (
    "id",
    "bundle_slug",
    "page",
    "role",
    "source_printed_box_pt",
    "official_field_key",
    "official_field_key_gap",
    "html_id_hint",
    "match",
    "correction_id",
)
REQUIRED_MATCH_KEYS = ("kind", "tolerance_pt", "cardinality")
HTML_ID_HINT_RE = re.compile(r"^p[0-9]+c[0-9]+$")
CELL_ID_RE = re.compile(r"\bp[0-9]+c[0-9]+\b")
FORM_FIELD_SPLIT_RE = re.compile(r"[,;/]+")
PARENTHETICAL_RE = re.compile(r"\([^)]*\)")
SKIP_TREE_SLUGS = frozenset({".", "review"})
FINDINGS_PATH = HERE / "review-findings.json"
LEDGER_TEXT_KEYS = ("where", "what")


MATCH_KINDS = frozenset({"comb", "field"})
EXPECTED_FILLABLE_CELLS = 9990
EXPECTED_UNCATALOGUED_FILLABLES = 0


class FieldCollector(html.parser.HTMLParser):
    """Every fillable field box. Ignores knockouts, separators, and labels.

    G11 mixed combs (`data-cell-kind=mixed` + `data-field-kind`) are fillable:
    the sheet pre-printed `000` in the branch, so emit keeps the mixed cell
    instead of five empty slots. That cell is the identity.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.fields: list[dict[str, object]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "div":
            return
        data = {key: value or "" for key, value in attrs}
        if data.get("data-cell-kind") not in ("field", "mixed"):
            return
        # TIN dash separators are field cells with no field-kind. After the
        # even reflow their centers sit inside the previous group's printed
        # box (C04 peach, C07 grey). They are not identities. Mixed cells
        # without a field-kind are labels-with-ink, not the comb.
        if not data.get("data-field-kind"):
            return
        box = parse_style_box(data.get("style", ""))
        html_id = data.get("id", "")
        if box is None or not html_id:
            return
        self.fields.append({
            "id": html_id,
            "box": box,
            "page_prefix": _page_prefix(html_id),
            "field_kind": data.get("data-field-kind", ""),
            "comb_slots": data.get("data-comb-slots", ""),
            "cell_kind": data.get("data-cell-kind", ""),
        })


def _page_prefix(html_id: str) -> str:
    if html_id.startswith("p") and "c" in html_id:
        return html_id.split("c", 1)[0]
    return ""


def parse_style_box(style: str) -> tuple[float, float, float, float] | None:
    props: dict[str, float] = {}
    for part in style.split(";"):
        if ":" not in part:
            continue
        key, value = part.split(":", 1)
        key, value = key.strip(), value.strip()
        if not value.endswith("pt"):
            continue
        try:
            props[key] = float(value[:-2])
        except ValueError:
            continue
    if not all(name in props for name in ("left", "top", "width", "height")):
        return None
    x0, y0 = props["left"], props["top"]
    return (x0, y0, x0 + props["width"], y0 + props["height"])


def expand_box(box: tuple[float, float, float, float],
               tolerance: float) -> tuple[float, float, float, float]:
    x0, y0, x1, y1 = box
    return (x0 - tolerance, y0 - tolerance, x1 + tolerance, y1 + tolerance)


def box_center(box: tuple[float, float, float, float]) -> tuple[float, float]:
    return ((box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0)


def center_in_printed(printed: tuple[float, float, float, float],
                      emitted: tuple[float, float, float, float],
                      tolerance_pt: float) -> bool:
    """Stage 2 reflow expands the branch left, so neighbouring groups overlap
    by a fraction of a point. The emitted box whose *center* still sits in
    the printed box is the same field; a neighbour that only nicks the edge
    is not.
    """
    cx, cy = box_center(emitted)
    x0, y0, x1, y1 = expand_box(printed, tolerance_pt)
    return x0 <= cx <= x1 and y0 <= cy <= y1


def load_catalog(path: pathlib.Path) -> tuple[dict, list[str]]:
    try:
        catalog = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {}, [f"{path}: {exc}"]
    return catalog, check_catalog(catalog, path)


def check_catalog(catalog: object, path: pathlib.Path) -> list[str]:
    errors: list[str] = []
    if not isinstance(catalog, dict):
        return [f"{path.name}: catalog is not an object"]
    extra = set(catalog) - {"schema_version", "records"}
    if extra:
        errors.append(f"{path.name}: unknown keys {sorted(extra)}")
    version = catalog.get("schema_version")
    if not isinstance(version, str) or not version:
        errors.append(f"{path.name}: schema_version missing")
    records = catalog.get("records")
    if not isinstance(records, list) or not records:
        errors.append(f"{path.name}: records must be a non-empty array")
        return errors
    seen: set[str] = set()
    for index, record in enumerate(records):
        errors.extend(check_record(record, f"{path.name}[{index}]", seen))
    return errors


def check_record(record: object, path: str, seen: set[str]) -> list[str]:
    errors: list[str] = []
    if not isinstance(record, dict):
        return [f"{path}: expected object"]
    extra = set(record) - set(REQUIRED_RECORD_KEYS)
    if extra:
        errors.append(f"{path}: unknown keys {sorted(extra)}")
    missing = [key for key in REQUIRED_RECORD_KEYS if key not in record]
    if missing:
        errors.append(f"{path}: missing {missing}")
        return errors
    ident = record["id"]
    if not isinstance(ident, str) or len(ident) < 8:
        errors.append(f"{path}: id is too short")
    elif "@" in ident:
        errors.append(f"{path}: id {ident!r} looks like geometry_subject_key; "
                      "identity is not a quantised bbox")
    elif ident in seen:
        errors.append(f"{path}: duplicate id {ident!r}")
    else:
        seen.add(ident)
    if not isinstance(record["bundle_slug"], str) or not record["bundle_slug"]:
        errors.append(f"{path}: bundle_slug empty")
    if not isinstance(record["page"], int) or isinstance(record["page"], bool) or record["page"] < 1:
        errors.append(f"{path}: page must be a positive integer")
    box = record["source_printed_box_pt"]
    if (not isinstance(box, list) or len(box) != 4
            or not all(isinstance(value, (int, float)) and not isinstance(value, bool)
                       for value in box)):
        errors.append(f"{path}: source_printed_box_pt must be four numbers")
    elif not (box[0] < box[2] and box[1] < box[3]):
        errors.append(f"{path}: source_printed_box_pt is not ordered x0<x1, y0<y1")
    key = record["official_field_key"]
    gap = record["official_field_key_gap"]
    if key is None:
        if not isinstance(gap, str) or not gap.strip():
            errors.append(f"{path}: official_field_key is null; official_field_key_gap must state the gap")
    elif isinstance(key, str) and key.strip():
        if gap != "":
            errors.append(f"{path}: official_field_key is set; official_field_key_gap must be empty")
    else:
        errors.append(f"{path}: official_field_key must be a non-empty string or null")
    hint = record["html_id_hint"]
    if not isinstance(hint, str) or not HTML_ID_HINT_RE.match(hint):
        errors.append(f"{path}: html_id_hint must look like p1c13")
    match = record["match"]
    if not isinstance(match, dict):
        errors.append(f"{path}: match must be an object")
    else:
        extra_match = set(match) - set(REQUIRED_MATCH_KEYS)
        if extra_match:
            errors.append(f"{path}: match unknown keys {sorted(extra_match)}")
        if match.get("kind") not in MATCH_KINDS:
            errors.append(f"{path}: match.kind must be one of {sorted(MATCH_KINDS)}")
        if match.get("cardinality") != "exactly-one":
            errors.append(f"{path}: match.cardinality must be 'exactly-one'")
        tolerance = match.get("tolerance_pt")
        if not isinstance(tolerance, (int, float)) or isinstance(tolerance, bool) or tolerance <= 0:
            errors.append(f"{path}: match.tolerance_pt must exceed 0")
    correction = record["correction_id"]
    if correction is not None and not (isinstance(correction, str) and correction.strip()):
        errors.append(f"{path}: correction_id must be a string or null")
    return errors


def collect_fields(html_text: str) -> list[dict[str, object]]:
    parser = FieldCollector()
    parser.feed(html_text)
    parser.close()
    return parser.fields


def _kind_wanted(match_kind: str, field_kind: str) -> bool:
    if match_kind == "comb":
        return field_kind == "comb"
    return True


def resolve_record(record: dict, tree: pathlib.Path) -> dict[str, object]:
    ident = record["id"]
    html_path = tree / record["bundle_slug"] / "index.html"
    result: dict[str, object] = {
        "id": ident,
        "bundle_slug": record["bundle_slug"],
        "html_id_hint": record["html_id_hint"],
        "html_path": str(html_path),
        "status": "unresolved",
        "resolved_html_id": None,
        "candidates": [],
        "reason": "",
    }
    if not html_path.is_file():
        result["reason"] = f"missing {html_path}"
        return result
    printed = tuple(float(value) for value in record["source_printed_box_pt"])
    tolerance = float(record["match"]["tolerance_pt"])
    match_kind = str(record["match"]["kind"])
    page_prefix = f"p{record['page']}"
    try:
        html_text = html_path.read_text(encoding="utf-8")
    except OSError as exc:
        result["reason"] = f"cannot read {html_path}: {exc}"
        return result
    hits = []
    for field in collect_fields(html_text):
        if field["page_prefix"] != page_prefix:
            continue
        if not _kind_wanted(match_kind, str(field["field_kind"])):
            continue
        if center_in_printed(printed, field["box"], tolerance):  # type: ignore[arg-type]
            hits.append(field)
    result["candidates"] = [field["id"] for field in hits]
    if not hits:
        result["status"] = "unresolved"
        result["reason"] = "no fillable field has its center in source_printed_box_pt"
        return result
    if len(hits) > 1:
        result["status"] = "ambiguous"
        result["reason"] = "more than one field center sits in source_printed_box_pt: " + ", ".join(
            str(field["id"]) for field in hits)
        return result
    resolved = str(hits[0]["id"])
    result["resolved_html_id"] = resolved
    if resolved != record["html_id_hint"]:
        result["status"] = "html_id_hint_stale"
        result["reason"] = (
            f"unique center hit is {resolved}, catalog hint is {record['html_id_hint']}; "
            "update the hint in this commit, do not mint a new identity"
        )
        return result
    result["status"] = "resolved"
    result["reason"] = "exactly one field center in the printed box; html_id_hint agrees"
    return result


def check_tree(catalog: dict, tree: pathlib.Path) -> tuple[list[dict[str, object]], int]:
    results = [resolve_record(record, tree) for record in catalog["records"]]
    failed = sum(1 for item in results if item["status"] != "resolved")
    return results, failed


def iter_bundles(tree: pathlib.Path) -> list[tuple[str, pathlib.Path]]:
    """Form bundles under a tree. Skips the corpus index and review helpers."""
    found: list[tuple[str, pathlib.Path]] = []
    if not tree.is_dir():
        return found
    for html in sorted(tree.glob("**/index.html")):
        slug = html.parent.relative_to(tree).as_posix()
        if slug in SKIP_TREE_SLUGS:
            continue
        found.append((slug, html))
    return found


def records_for_slug(catalog: dict, slug: str) -> list[dict]:
    return [record for record in catalog.get("records", []) if record["bundle_slug"] == slug]


def identities_claiming(field: dict, records: list[dict]) -> list[str]:
    hits: list[str] = []
    page_prefix = str(field["page_prefix"])
    box = field["box"]
    field_kind = str(field["field_kind"])
    for record in records:
        if page_prefix != f"p{record['page']}":
            continue
        match = record.get("match") or {}
        if not _kind_wanted(str(match.get("kind") or "field"), field_kind):
            continue
        printed = tuple(float(value) for value in record["source_printed_box_pt"])
        tolerance = float(match.get("tolerance_pt") or 0.25)
        if center_in_printed(printed, box, tolerance):  # type: ignore[arg-type]
            hits.append(str(record["id"]))
    return hits


def coverage_tree(catalog: dict, tree: pathlib.Path) -> dict[str, object]:
    uncatalogued: list[dict[str, object]] = []
    ambiguous: list[dict[str, object]] = []
    fillable = 0
    covered = 0
    for slug, html_path in iter_bundles(tree):
        try:
            fields = collect_fields(html_path.read_text(encoding="utf-8"))
        except OSError as exc:
            uncatalogued.append({"bundle_slug": slug, "html_id": None, "reason": str(exc)})
            continue
        records = records_for_slug(catalog, slug)
        for field in fields:
            fillable += 1
            hits = identities_claiming(field, records)
            if not hits:
                uncatalogued.append({
                    "bundle_slug": slug,
                    "html_id": field["id"],
                    "page_prefix": field["page_prefix"],
                    "field_kind": field["field_kind"],
                    "box": [round(float(v), 4) for v in field["box"]],  # type: ignore[union-attr]
                })
            elif len(hits) > 1:
                ambiguous.append({
                    "bundle_slug": slug,
                    "html_id": field["id"],
                    "identities": hits,
                })
            else:
                covered += 1
    return {
        "tree": str(tree),
        "fillable": fillable,
        "covered": covered,
        "uncatalogued": uncatalogued,
        "ambiguous": ambiguous,
    }


def print_coverage(report: dict[str, object]) -> None:
    uncatalogued = report["uncatalogued"]
    ambiguous = report["ambiguous"]
    fillable = int(report["fillable"])  # type: ignore[arg-type]
    covered = int(report["covered"])  # type: ignore[arg-type]
    print(f"{covered}/{fillable} fillable cells claimed by exactly one identity in {report['tree']}")
    if uncatalogued:
        print(f"{len(uncatalogued)} uncatalogued:")
        for item in uncatalogued[:40]:  # type: ignore[index]
            print(f"  {item['bundle_slug']}  {item.get('html_id')}")
        if len(uncatalogued) > 40:  # type: ignore[arg-type]
            print(f"  … {len(uncatalogued) - 40} more")
    if ambiguous:
        print(f"{len(ambiguous)} claimed by two or more identities:")
        for item in ambiguous[:20]:  # type: ignore[index]
            print(f"  {item['bundle_slug']}  {item['html_id']} -> {item['identities']}")
        if len(ambiguous) > 20:  # type: ignore[arg-type]
            print(f"  … {len(ambiguous) - 20} more")


class ElementCollector(html.parser.HTMLParser):
    """Every element whose id looks like p1c13, fillable or not.

    Ledger citations name triangles, labels, and blanks as well as fields.
    Those cells are not identities; they still have to exist in the HTML.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.ids: set[str] = set()
        self.fillable: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        data = {key: value or "" for key, value in attrs}
        html_id = data.get("id", "")
        if not HTML_ID_HINT_RE.match(html_id):
            return
        self.ids.add(html_id)
        if data.get("data-cell-kind") in ("field", "mixed") and data.get("data-field-kind"):
            self.fillable.add(html_id)


def collect_element_ids(html_text: str) -> tuple[set[str], set[str]]:
    parser = ElementCollector()
    parser.feed(html_text)
    parser.close()
    return parser.ids, parser.fillable


def bundle_slug_for_finding_form(form: str, tree: pathlib.Path) -> str | None:
    if (tree / form / "index.html").is_file():
        return form
    extra = f"extra/{form}"
    if (tree / extra / "index.html").is_file():
        return extra
    return None


def html_ids_in_tree_bundle(tree: pathlib.Path, slug: str) -> set[str]:
    html_path = tree / slug / "index.html"
    if not html_path.is_file():
        return set()
    return {str(field["id"]) for field in collect_fields(html_path.read_text(encoding="utf-8"))}


def known_bundle_slugs(tree: pathlib.Path) -> list[str]:
    return [slug for slug, _ in iter_bundles(tree)]


def _slug_aliases(slug: str) -> tuple[str, ...]:
    tail = slug.split("/")[-1]
    code = tail.split("-")[0]
    aliases = [slug, tail]
    if len(code) >= 4:
        aliases.append(code)
    return tuple(aliases)


def _bounded_rfind(window: str, alias: str) -> int:
    lowered = window.lower()
    needle = alias.lower()
    pos = len(window)
    while pos > 0:
        pos = lowered.rfind(needle, 0, pos)
        if pos < 0:
            return -1
        end = pos + len(alias)
        left_ok = pos == 0 or not (window[pos - 1].isalnum() or window[pos - 1] in "/-_")
        right_ok = end == len(window) or not (window[end].isalnum() or window[end] in "/-_")
        if left_ok and right_ok:
            return pos
    return -1


def match_token_to_slug(token: str, known: list[str]) -> str | None:
    token = token.strip()
    if not token:
        return None
    lowered = token.lower()
    exact: list[str] = []
    code_hits: list[str] = []
    for slug in known:
        tail = slug.split("/")[-1]
        code = tail.split("-")[0]
        if token in (slug, tail) or lowered == tail.lower():
            exact.append(slug)
        elif lowered == code.lower() or lowered == f"extra/{tail}".lower():
            code_hits.append(slug)
        elif lowered.replace("-", "") == tail.replace("-", "").lower():
            exact.append(slug)
    if exact:
        in_corpus = [slug for slug in exact if not slug.startswith("extra/")]
        return (in_corpus or exact)[0]
    if len(code_hits) == 1:
        return code_hits[0]
    in_corpus = [slug for slug in code_hits if not slug.startswith("extra/")]
    if len(in_corpus) == 1:
        return in_corpus[0]
    return None


def form_field_tokens(form: str) -> list[str]:
    cleaned = PARENTHETICAL_RE.sub(" ", form)
    return [part.strip() for part in FORM_FIELD_SPLIT_RE.split(cleaned) if part.strip()]


def slugs_for_finding(form: str, text: str, known: list[str]) -> list[str]:
    found: list[str] = []
    for token in form_field_tokens(form):
        hit = match_token_to_slug(token, known)
        if hit:
            found.append(hit)
    pairs: list[tuple[str, str]] = []
    for slug in known:
        for alias in _slug_aliases(slug):
            pairs.append((alias, slug))
    pairs.sort(key=lambda item: len(item[0]), reverse=True)
    occupied = [False] * len(text)
    lowered = text.lower()
    for alias, slug in pairs:
        start = 0
        needle = alias.lower()
        while True:
            pos = lowered.find(needle, start)
            if pos < 0:
                break
            end = pos + len(alias)
            left_ok = pos == 0 or not (text[pos - 1].isalnum() or text[pos - 1] in "/-_")
            right_ok = end == len(text) or not (text[end].isalnum() or text[end] in "/-_")
            if left_ok and right_ok and not any(occupied[pos:end]):
                found.append(slug)
                for index in range(pos, end):
                    occupied[index] = True
            start = pos + 1
    ordered: list[str] = []
    seen: set[str] = set()
    for slug in found:
        if slug not in seen:
            seen.add(slug)
            ordered.append(slug)
    return ordered


def nearest_slug(text: str, index: int, slugs: list[str]) -> str | None:
    window = text[:index]
    best_pos = -1
    best_alias_len = -1
    best: str | None = None
    for slug in slugs:
        for alias in _slug_aliases(slug):
            if len(alias) < 4:
                continue
            pos = _bounded_rfind(window, alias)
            if pos < 0:
                continue
            if pos > best_pos or (pos == best_pos and len(alias) > best_alias_len):
                best_pos = pos
                best_alias_len = len(alias)
                best = slug
    if best is not None:
        return best
    return slugs[0] if slugs else None


def catalog_records_by_id(catalog: dict) -> dict[str, dict]:
    return {str(record["id"]): record for record in catalog.get("records", [])}


def catalog_ids_longest(catalog: dict) -> list[str]:
    return sorted(catalog_records_by_id(catalog), key=len, reverse=True)


def hint_to_identity(catalog: dict) -> dict[tuple[str, str], str]:
    mapping: dict[tuple[str, str], str] = {}
    for record in catalog.get("records", []):
        mapping[(str(record["bundle_slug"]), str(record["html_id_hint"]))] = str(record["id"])
    return mapping


def catalog_ids_in_text(text: str, identities: list[str]) -> list[str]:
    occupied = [False] * len(text)
    hits: list[str] = []
    for ident in identities:
        start = 0
        while True:
            pos = text.find(ident, start)
            if pos < 0:
                break
            end = pos + len(ident)
            nxt = text[end] if end < len(text) else " "
            if (not any(occupied[pos:end])
                    and not (nxt.isalnum() or nxt in "-/")):
                hits.append(ident)
                for index in range(pos, end):
                    occupied[index] = True
            start = pos + 1
    return hits


def load_bundle_element_ids(
        tree: pathlib.Path, slug: str,
        cache: dict[tuple[str, str], tuple[set[str], set[str]]],
) -> tuple[set[str], set[str]]:
    key = (str(tree), slug)
    if key not in cache:
        html_path = tree / slug / "index.html"
        if not html_path.is_file():
            cache[key] = (set(), set())
        else:
            cache[key] = collect_element_ids(html_path.read_text(encoding="utf-8"))
    return cache[key]


def catalog_id_resolves(record: dict, tree: pathlib.Path,
                        cache: dict[tuple[str, str], tuple[set[str], set[str]]],
                        ) -> bool:
    ids, fillable = load_bundle_element_ids(tree, str(record["bundle_slug"]), cache)
    return str(record["html_id_hint"]) in fillable and str(record["html_id_hint"]) in ids


def ledger_check(catalog: dict, tree: pathlib.Path,
                 findings_path: pathlib.Path,
                 statuses: set[str] | None = None) -> dict[str, object]:
    try:
        payload = json.loads(findings_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"errors": [f"{findings_path}: {exc}"], "dead": [], "ok": 0, "checked": 0}
    findings = payload.get("findings") if isinstance(payload, dict) else None
    if not isinstance(findings, list):
        return {"errors": [f"{findings_path}: findings is not an array"], "dead": [], "ok": 0, "checked": 0}
    known = known_bundle_slugs(tree)
    identities = catalog_ids_longest(catalog)
    by_id = catalog_records_by_id(catalog)
    cache: dict[tuple[str, str], tuple[set[str], set[str]]] = {}
    dead: list[dict[str, object]] = []
    checked = 0
    ok = 0
    for finding in findings:
        if not isinstance(finding, dict):
            continue
        status = str(finding.get("status") or "")
        if statuses is not None and status not in statuses:
            continue
        form = str(finding.get("form") or "")
        text = " ".join(str(finding.get(key) or "") for key in LEDGER_TEXT_KEYS)
        slugs = slugs_for_finding(form, text, known)
        cited_ids = catalog_ids_in_text(text, identities)
        if not cited_ids and not CELL_ID_RE.search(text):
            continue
        for ident in cited_ids:
            checked += 1
            record = by_id.get(ident)
            if record is not None and catalog_id_resolves(record, tree, cache):
                ok += 1
                continue
            dead.append({
                "finding": finding.get("id"),
                "form": form,
                "bundle_slug": None if record is None else record.get("bundle_slug"),
                "status": status,
                "cell": ident,
            })
        for match in CELL_ID_RE.finditer(text):
            cell = match.group(0)
            checked += 1
            search = list(slugs)
            nearest = nearest_slug(text, match.start(), slugs)
            if nearest:
                search = [nearest] + [slug for slug in slugs if slug != nearest]
            live = False
            hit_slug: str | None = None
            for slug in search:
                ids, _fillable = load_bundle_element_ids(tree, slug, cache)
                if cell in ids:
                    live = True
                    hit_slug = slug
                    break
            if live:
                ok += 1
                continue
            dead.append({
                "finding": finding.get("id"),
                "form": form,
                "bundle_slug": hit_slug or (search[0] if search else None),
                "status": status,
                "cell": cell,
            })
    return {
        "errors": [],
        "checked": checked,
        "ok": ok,
        "dead": dead,
        "path": str(findings_path),
        "tree": str(tree),
    }


def rewrite_ledger_text(text: str, form: str, catalog: dict, tree: pathlib.Path,
                        known: list[str],
                        hints: dict[tuple[str, str], str],
                        cache: dict[tuple[str, str], tuple[set[str], set[str]]],
                        ) -> str:
    slugs = slugs_for_finding(form, text, known)

    def replace(match: re.Match[str]) -> str:
        cell = match.group(0)
        nearest = nearest_slug(text, match.start(), slugs)
        search = list(slugs)
        if nearest:
            search = [nearest] + [slug for slug in slugs if slug != nearest]

        def lookup(slug: str) -> str | None:
            ids, fillable = load_bundle_element_ids(tree, slug, cache)
            if cell in fillable:
                ident = hints.get((slug, cell))
                if ident:
                    return ident
            if cell in ids:
                return cell
            return None

        if nearest:
            hit = lookup(nearest)
            if hit is not None:
                return hit
        for slug in search:
            if slug == nearest:
                continue
            hit = lookup(slug)
            if hit is not None:
                return hit
        return f"former_{cell}"

    return CELL_ID_RE.sub(replace, text)


def rewrite_findings_ledger(catalog: dict, tree: pathlib.Path,
                            findings_path: pathlib.Path) -> dict[str, object]:
    payload = json.loads(findings_path.read_text(encoding="utf-8"))
    findings = payload.get("findings")
    if not isinstance(findings, list):
        raise ValueError(f"{findings_path}: findings is not an array")
    known = known_bundle_slugs(tree)
    hints = hint_to_identity(catalog)
    cache: dict[tuple[str, str], tuple[set[str], set[str]]] = {}
    changed = 0
    for finding in findings:
        if not isinstance(finding, dict):
            continue
        form = str(finding.get("form") or "")
        for key in LEDGER_TEXT_KEYS:
            original = str(finding.get(key) or "")
            rewritten = rewrite_ledger_text(
                original, form, catalog, tree, known, hints, cache)
            if rewritten != original:
                finding[key] = rewritten
                changed += 1
    return {"payload": payload, "changed_fields": changed}


def print_ledger(report: dict[str, object]) -> None:
    if report.get("errors"):
        for error in report["errors"]:  # type: ignore[union-attr]
            print(f"FAIL  {error}")
        return
    dead = report["dead"]
    print(f"{report['ok']}/{report['checked']} cited cells resolve in {report['tree']}")
    if dead:
        print(f"{len(dead)} dead citations:")
        for item in dead[:40]:  # type: ignore[index]
            print(f"  {item['finding']}  {item['form']}  {item['cell']}")
        if len(dead) > 40:  # type: ignore[arg-type]
            print(f"  … {len(dead) - 40} more")


def print_results(results: list[dict[str, object]], tree: pathlib.Path) -> None:
    for item in results:
        status = item["status"]
        ident = item["id"]
        mark = "OK" if status == "resolved" else "FAIL"
        extra = f" -> {item['resolved_html_id']}" if item["resolved_html_id"] else ""
        print(f"{mark}  {ident}  [{status}]{extra}")
        if status != "resolved":
            print(f"     {item['reason']}")
    failed = sum(1 for item in results if item["status"] != "resolved")
    print(f"{len(results) - failed}/{len(results)} identit"
          f"{'y' if len(results) == 1 else 'ies'} resolved in {tree}")


def _write_html(path: pathlib.Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "<!DOCTYPE html><html><body>" + body + "</body></html>",
        encoding="utf-8",
    )


def _comb(html_id: str, left: float, top: float, width: float, height: float) -> str:
    return (
        f'<div id="{html_id}" class="c f" data-cell-kind="field" '
        f'data-field-kind="comb" data-field-name="{html_id}" '
        f'style="left:{left}pt;top:{top}pt;width:{width}pt;height:{height}pt"></div>'
    )


def _text(html_id: str, left: float, top: float, width: float, height: float) -> str:
    return (
        f'<div id="{html_id}" class="c f" data-cell-kind="field" '
        f'data-field-kind="text" data-field-name="{html_id}" '
        f'style="left:{left}pt;top:{top}pt;width:{width}pt;height:{height}pt"></div>'
    )


def _mixed_comb(html_id: str, left: float, top: float, width: float, height: float) -> str:
    return (
        f'<div id="{html_id}" class="c" data-cell-kind="mixed" '
        f'data-field-kind="comb" data-comb-slots="5" '
        f'style="left:{left}pt;top:{top}pt;width:{width}pt;height:{height}pt"></div>'
    )


def _sample_record(**overrides: object) -> dict:
    record: dict[str, object] = {
        "id": "fixture/p1/tin-branch",
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


def self_test() -> int:
    failed = 0

    def check(name: str, held: bool, detail: str = "") -> None:
        nonlocal failed
        if held:
            print(f"OK    {name}")
        else:
            failed += 1
            print(f"FAIL  {name}" + (f" — {detail}" if detail else ""))

    catalog, errors = load_catalog(DEFAULT_CATALOG)
    check("shipped catalog is well formed", not errors, "; ".join(errors[:3]))
    if catalog:
        records = catalog.get("records", [])
        seed = [record for record in records if record.get("correction_id")]
        check("C01–C07 seed identities are still present (28)",
              len(seed) == 28
              and {record["correction_id"] for record in seed}
              == {"C01", "C02", "C03", "C04", "C05", "C06", "C07"},
              str(len(seed)))
        check("catalog covers every measured fillable cell (9990)",
              len(records) == EXPECTED_FILLABLE_CELLS,
              str(len(records)))
        forms = REPO / "forms"
        if forms.is_dir():
            cover = coverage_tree(catalog, forms)
            check("forms/ fillable coverage is 0 uncatalogued",
                  int(cover["fillable"]) == EXPECTED_FILLABLE_CELLS
                  and int(cover["covered"]) == EXPECTED_FILLABLE_CELLS
                  and len(cover["uncatalogued"]) == EXPECTED_UNCATALOGUED_FILLABLES
                  and not cover["ambiguous"],
                  f"{cover['covered']}/{cover['fillable']} uncat={len(cover['uncatalogued'])}")
        ids = [record["id"] for record in records]
        check("shipped identity ids are unique", len(ids) == len(set(ids)))
        check("no identity id is a bbox key",
              all("@" not in ident for ident in ids))
        check("no identity id is a bare p1cN",
              all(not HTML_ID_HINT_RE.match(ident) for ident in ids))

    bad = _sample_record(official_field_key=None, official_field_key_gap="")
    gap_errors = check_record(bad, "gap", set())
    check("null official key without a gap is refused",
          any("official_field_key_gap" in error for error in gap_errors))

    duplicate_errors = check_catalog(
        {"schema_version": "1.0.0-provisional",
         "records": [_sample_record(), _sample_record()]},
        pathlib.Path("dup.json"),
    )
    check("duplicate identity ids are refused",
          any("duplicate id" in error for error in duplicate_errors))

    bbox_errors = check_record(
        _sample_record(id="p1@180.24,118.80,213.12,134.40"), "bbox", set())
    check("a geometry_subject_key is refused as an identity id",
          any("quantised bbox" in error for error in bbox_errors),
          "; ".join(bbox_errors))

    with tempfile.TemporaryDirectory() as tmp:
        tree = pathlib.Path(tmp) / "forms"
        printed = (180.24, 118.8, 213.12, 134.4)
        x0, y0, x1, y1 = printed
        record = _sample_record()
        knockout = (
            f'<div class="c" data-cell-kind="blank" '
            f'style="left:66pt;top:{y0}pt;width:147.12pt;height:{y1 - y0}pt"></div>'
        )
        neighbour = _comb("p1c11", 132.34, y0, 28.49, y1 - y0)
        target = _comb("p1c13", 165.63, y0, x1 - 165.63, y1 - y0)
        _write_html(tree / "fixture-form" / "index.html", knockout + neighbour + target)
        result = resolve_record(record, tree)
        check("knockout covering the strip is ignored",
              result["status"] == "resolved" and result["resolved_html_id"] == "p1c13",
              str(result))

        stale = dict(record)
        stale["html_id_hint"] = "p1c99"
        stale_result = resolve_record(stale, tree)
        check("unique overlap with a stale hint is html_id_hint_stale, not a new identity",
              stale_result["status"] == "html_id_hint_stale"
              and stale_result["resolved_html_id"] == "p1c13",
              str(stale_result))

        _write_html(tree / "fixture-form" / "index.html", neighbour)
        missing = resolve_record(record, tree)
        check("no field center in the printed box is unresolved",
              missing["status"] == "unresolved", str(missing))

        twin = _comb("p1c99", 180.0, y0, 40.0, y1 - y0)
        _write_html(tree / "fixture-form" / "index.html", target + twin)
        ambi = resolve_record(record, tree)
        check("two field centers in the printed box are ambiguous",
              ambi["status"] == "ambiguous", str(ambi))

        _write_html(tree / "fixture-form" / "index.html",
                    _comb("p2c13", 165.63, y0, x1 - 165.63, y1 - y0))
        other_page = resolve_record(record, tree)
        check("a field on another page does not resolve this identity",
              other_page["status"] == "unresolved", str(other_page))

        # C01 tin-1 after even reflow: neighbour nicks the old right edge,
        # but its center is in tin-2. Must not go ambiguous.
        tin1 = _sample_record(
            id="fixture/p1/tin-1", role="tin-1", html_id_hint="p1c127",
            source_printed_box_pt=[66.0, 118.8, 99.84, 133.68])
        reflowed = (
            _text("p1c127", 66.0, 118.8, 28.49, 14.88)
            + _comb("p1c9", 99.29, 118.8, 28.49, 15.6)
        )
        _write_html(tree / "fixture-form" / "index.html", reflowed)
        tin1_result = resolve_record(tin1, tree)
        check("a neighbour that nicks the printed edge does not steal the identity",
              tin1_result["status"] == "resolved"
              and tin1_result["resolved_html_id"] == "p1c127",
              str(tin1_result))
        text_only = _sample_record(
            id="fixture/p1/tin-text", role="tin-1", html_id_hint="p1c127",
            source_printed_box_pt=[66.0, 118.8, 99.84, 133.68])
        _write_html(tree / "fixture-form" / "index.html",
                    _text("p1c127", 66.0, 118.8, 33.84, 14.88))
        text_result = resolve_record(text_only, tree)
        check("a stage-1 text field (not comb) still resolves kind=field",
              text_result["status"] == "resolved"
              and text_result["resolved_html_id"] == "p1c127",
              str(text_result))

        sep = (
            _comb("p1c18", 57.84, 189.36, 28.6, 19.44)
            + '<div id="p1c19" class="c" data-cell-kind="field" '
            'style="left:86.44pt;top:189.36pt;width:4.8pt;height:19.44pt"></div>'
        )
        tin_c04 = _sample_record(
            id="fixture/p1/tin-peach", role="tin-1", html_id_hint="p1c18",
            source_printed_box_pt=[57.84, 189.36, 91.8, 208.8])
        _write_html(tree / "fixture-form" / "index.html", sep)
        sep_result = resolve_record(tin_c04, tree)
        check("a dash separator with no field-kind does not steal the identity",
              sep_result["status"] == "resolved"
              and sep_result["resolved_html_id"] == "p1c18",
              str(sep_result))

        # G11: pre-printed 000 in the branch box. emit keeps a mixed comb,
        # not an empty field. That mixed cell is the identity.
        _write_html(tree / "fixture-form" / "index.html",
                    _mixed_comb("p1c19", x0, y0, x1 - x0, y1 - y0))
        mixed_record = _sample_record(html_id_hint="p1c19")
        mixed_result = resolve_record(mixed_record, tree)
        check("a G11 mixed comb still resolves kind=field",
              mixed_result["status"] == "resolved"
              and mixed_result["resolved_html_id"] == "p1c19",
              str(mixed_result))

        cover_tree = pathlib.Path(tmp) / "cover"
        _write_html(
            cover_tree / "fixture-form" / "index.html",
            _comb("p1c13", 165.63, y0, x1 - 165.63, y1 - y0)
            + _text("p1c99", 300.0, y0, 40.0, y1 - y0),
        )
        cover_catalog = {
            "schema_version": "1.0.0-provisional",
            "records": [record],
        }
        cover_report = coverage_tree(cover_catalog, cover_tree)
        check("coverage reports an uncatalogued sibling fillable",
              int(cover_report["fillable"]) == 2  # type: ignore[arg-type]
              and int(cover_report["covered"]) == 1  # type: ignore[arg-type]
              and len(cover_report["uncatalogued"]) == 1  # type: ignore[arg-type]
              and cover_report["uncatalogued"][0]["html_id"] == "p1c99",  # type: ignore[index]
              str(cover_report))

        label = (
            f'<div id="p1c1" class="c" data-cell-kind="label" '
            f'style="left:10pt;top:{y0}pt;width:12pt;height:{y1 - y0}pt"></div>'
        )
        _write_html(tree / "fixture-form" / "index.html", target + label)
        findings_path = pathlib.Path(tmp) / "findings.json"
        findings_path.write_text(json.dumps({
            "findings": [
                {"id": "T001", "form": "fixture-form", "status": "open",
                 "where": "fillable p1c13 and label p1c1", "what": "ok"},
                {"id": "T002", "form": "fixture-form", "status": "open",
                 "where": "dead p1c99", "what": "gone"},
                {"id": "T003", "form": "fixture-form", "status": "open",
                 "where": "catalog fixture/p1/tin-branch", "what": "id"},
                {"id": "T004",
                 "form": "fixture-form, missing-form",
                 "status": "open",
                 "where": "fixture-form p1c13", "what": "split"},
            ]
        }), encoding="utf-8")
        live_report = ledger_check(cover_catalog, tree, findings_path, {"open"})
        dead_cells = [item["cell"] for item in live_report["dead"]]  # type: ignore[index]
        check("ledger-check accepts a live label id and a resolving catalog id",
              live_report["errors"] == []
              and "p1c99" in dead_cells
              and "p1c13" not in dead_cells
              and "p1c1" not in dead_cells
              and "fixture/p1/tin-branch" not in dead_cells,
              str(live_report))
        rewritten = rewrite_ledger_text(
            "fillable p1c13 and dead p1c99 and label p1c1",
            "fixture-form", cover_catalog, tree, ["fixture-form"],
            {("fixture-form", "p1c13"): "fixture/p1/tin-branch"},
            {},
        )
        check("ledger rewrite maps fillable cells to catalog ids and marks dead ones",
              rewritten == "fillable fixture/p1/tin-branch and dead former_p1c99 and label p1c1",
              rewritten)

    print("FAIL" if failed else "OK",
          f"{failed} self-test(s) failed" if failed else "self-test")
    return 1 if failed else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--catalog", type=pathlib.Path, default=DEFAULT_CATALOG)
    sub = parser.add_subparsers(dest="command")
    check_cmd = sub.add_parser("check", help="resolve every catalog record against a tree")
    check_cmd.add_argument("--tree", type=pathlib.Path, required=True)
    cover_cmd = sub.add_parser("coverage", help="list fillable cells no catalog record claims")
    cover_cmd.add_argument("--tree", type=pathlib.Path, required=True)
    ledger_cmd = sub.add_parser("ledger-check", help="cited pXcN in review-findings vs a tree")
    ledger_cmd.add_argument("--tree", type=pathlib.Path, required=True)
    ledger_cmd.add_argument("--findings", type=pathlib.Path, default=FINDINGS_PATH)
    ledger_cmd.add_argument(
        "--status",
        action="append",
        dest="statuses",
        help="repeatable; default is open findings only",
    )
    rewrite_cmd = sub.add_parser(
        "ledger-rewrite",
        help="replace fillable pXcN in where/what with catalog ids; mark dead ids",
    )
    rewrite_cmd.add_argument("--tree", type=pathlib.Path, required=True)
    rewrite_cmd.add_argument("--findings", type=pathlib.Path, default=FINDINGS_PATH)
    rewrite_cmd.add_argument("--write", action="store_true",
                             help="write the rewritten ledger back to --findings")
    args = parser.parse_args()
    if args.self_test:
        return self_test()
    if args.command not in ("check", "coverage", "ledger-check", "ledger-rewrite"):
        parser.print_help()
        return 2
    catalog, errors = load_catalog(args.catalog)
    if errors:
        for error in errors:
            print(f"FAIL  {error}")
        print(f"{len(errors)} catalog error(s)")
        return 1
    tree = args.tree
    if not tree.is_absolute():
        tree = (pathlib.Path.cwd() / tree).resolve()
    if not tree.is_dir():
        print(f"FAIL  tree {tree} is not a directory")
        return 1
    if args.command == "coverage":
        report = coverage_tree(catalog, tree)
        print_coverage(report)
        failed = bool(report["uncatalogued"] or report["ambiguous"])
        return 1 if failed else 0
    if args.command == "ledger-check":
        statuses = set(args.statuses) if args.statuses else {"open"}
        report = ledger_check(catalog, tree, args.findings, statuses)
        print_ledger(report)
        if report.get("errors") or report.get("dead"):
            return 1
        return 0
    if args.command == "ledger-rewrite":
        result = rewrite_findings_ledger(catalog, tree, args.findings)
        payload = result["payload"]
        print(f"{result['changed_fields']} where/what field(s) rewritten")
        if args.write:
            args.findings.write_text(
                json.dumps(payload, indent=1, ensure_ascii=True) + "\n",
                encoding="utf-8",
            )
        return 0
    results, failed = check_tree(catalog, tree)
    print_results(results, tree)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
