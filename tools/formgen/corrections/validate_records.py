#!/usr/bin/env python3
"""Shape-check the EVIDENCE records under `evidence/`, and cross-check each one
against the applier-dialect ledger entry it backs.

Two files describe one correction, on purpose:

  * `C<nn>-<slug>.json` at the root of this directory is what
    `tools/formgen/correct.py` reads.  Its schema is CLOSED by that tool so no
    record can express a suppression, which also means it cannot carry
    structured evidence.
  * `evidence/C<nn>-evidence.json` carries the measurements, their sources and
    their re-derivation steps, against `schema/correction-record.schema.json`.

This file checks the second and proves the two agree.  Subdirectories are
invisible to the applier's ledger loader, so nothing here can be mistaken for a
record it should apply.

WHAT THIS IS NOT, stated because the distinction is the whole point:

  * It is NOT the applier.  It writes nothing and reads no form.
  * It is NOT the verifier.  It cannot tell you whether a correction's
    expected_effect held; only an independent producer running over the
    corrected tree can, and this file is not that (ARCHITECTURE.md rule 4).
  * Passing it means the record is well formed and internally consistent.  It
    says nothing about whether the correction is TRUE.

Beyond JSON Schema, it enforces the invariants a schema cannot express:
  - the filename matches the record id;
  - divergence.never_hidden is true and the divergence text names the record id
    and quotes authority.statement verbatim;
  - no record may be `verified` while any of its declared checks is unproven;
  - a record whose authority names no regulation must say so in the exact
    wording the project agreed on, rather than leaving the field empty;
  - every expected_effect assertion carries a re-derivation source, and no
    assertion may claim `held` while the record is still `declared`.

Usage:  python3 tools/formgen/corrections/validate_records.py [--dir DIR]
Exit 0 when every record passes, 1 otherwise.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys

HONEST_GAP = "regulation not identified in-repo"


def load_schema(directory: pathlib.Path) -> dict:
    return json.loads((directory / "schema" / "correction-record.schema.json").read_text())


def check_against_schema(schema: dict, record: dict, path: str = "$") -> list[str]:
    """A deliberately small subset of JSON Schema: object required/properties,
    additionalProperties, type, enum, const, pattern, minItems/minLength and
    array items.  Enough for this schema, and with no third-party dependency in
    a tree whose gate hashes its own inputs."""
    errors: list[str] = []
    kind = schema.get("type")
    if "const" in schema and record != schema["const"]:
        errors.append(f"{path}: expected const {schema['const']!r}, got {record!r}")
    if "enum" in schema and record not in schema["enum"]:
        errors.append(f"{path}: {record!r} is not one of {schema['enum']}")
    if kind == "object":
        if not isinstance(record, dict):
            return errors + [f"{path}: expected object, got {type(record).__name__}"]
        for key in schema.get("required", []):
            if key not in record:
                errors.append(f"{path}: missing required key {key!r}")
        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            for key in record:
                if key not in properties:
                    errors.append(f"{path}: unexpected key {key!r}")
        for key, value in record.items():
            if key in properties:
                errors += check_against_schema(properties[key], value, f"{path}.{key}")
    elif kind == "array":
        if not isinstance(record, list):
            return errors + [f"{path}: expected array, got {type(record).__name__}"]
        if len(record) < schema.get("minItems", 0):
            errors.append(f"{path}: needs at least {schema['minItems']} item(s)")
        if "maxItems" in schema and len(record) > schema["maxItems"]:
            errors.append(f"{path}: allows at most {schema['maxItems']} item(s)")
        item_schema = schema.get("items")
        if item_schema:
            for i, item in enumerate(record):
                errors += check_against_schema(item_schema, item, f"{path}[{i}]")
    elif kind == "string":
        if not isinstance(record, str):
            errors.append(f"{path}: expected string, got {type(record).__name__}")
        else:
            if "pattern" in schema and not re.search(schema["pattern"], record):
                errors.append(f"{path}: {record!r} does not match /{schema['pattern']}/")
            if len(record) < schema.get("minLength", 0):
                errors.append(f"{path}: shorter than minLength {schema['minLength']}")
    elif kind == "integer":
        if not isinstance(record, int) or isinstance(record, bool):
            errors.append(f"{path}: expected integer")
        elif "minimum" in schema and record < schema["minimum"]:
            errors.append(f"{path}: below minimum {schema['minimum']}")
    elif kind == "number":
        if not isinstance(record, (int, float)) or isinstance(record, bool):
            errors.append(f"{path}: expected number")
        elif "exclusiveMinimum" in schema and record <= schema["exclusiveMinimum"]:
            errors.append(f"{path}: must exceed {schema['exclusiveMinimum']}")
    elif kind == "boolean":
        if not isinstance(record, bool):
            errors.append(f"{path}: expected boolean")
    return errors


def check_companion(record: dict, directory: pathlib.Path) -> list[str]:
    """The evidence record and the applier-dialect ledger entry must agree.

    Two files, one correction: if they can drift apart, the reviewed evidence
    stops describing what is actually applied -- which is the same defect as a
    census pin that no longer matches its producer.
    """
    errors: list[str] = []
    record_id = record.get("id", "")
    candidates = sorted(p for p in directory.glob(f"{record_id}-*.json"))
    if not candidates:
        return [f"{record_id}: no applier-dialect ledger entry {record_id}-*.json beside this evidence"]
    if len(candidates) > 1:
        return [f"{record_id}: {len(candidates)} ledger entries claim this id: "
                + ", ".join(p.name for p in candidates)]
    entry = json.loads(candidates[0].read_text())
    name = candidates[0].name
    if entry.get("id") != record_id:
        errors.append(f"{name}: id {entry.get('id')!r} != evidence id {record_id!r}")
    slug = record.get("form", {}).get("bundle_slug")
    if entry.get("form") != slug:
        errors.append(f"{name}: form {entry.get('form')!r} != evidence bundle_slug {slug!r}")
    statement = record.get("authority", {}).get("statement", "").rstrip(".")
    if statement and statement not in entry.get("authority", ""):
        errors.append(f"{name}: authority does not quote the evidence record's statement verbatim")
    if HONEST_GAP in statement and HONEST_GAP not in entry.get("authority", ""):
        errors.append(f"{name}: the honest-gap wording is missing from the applied record")
    # Key names only -- never a substring scan of the prose. The record is
    # SUPPOSED to say "must not be allowlisted, excluded or waived"; a checker
    # that reads that as a suppression is the same class of instrument error
    # as a raster localiser reading a grey decoration as a missing rule.
    for key in entry:
        if any(banned in key.lower()
               for banned in ("suppress", "waive", "allowlist", "expected_failure", "ignore")):
            errors.append(f"{name}: key {key!r} names a suppression; "
                          "an override may not express one")
    if not entry.get("diverges_from", "").strip():
        errors.append(f"{name}: diverges_from is empty -- the check it still fails must be named")
    return errors


def check_invariants(record: dict, path: pathlib.Path) -> list[str]:
    errors: list[str] = []
    record_id = record.get("id", "")
    if not path.name.startswith(record_id + "-"):
        errors.append(f"filename {path.name} does not start with the record id {record_id!r}")

    divergence = record.get("divergence", {})
    text = divergence.get("report_text", "")
    if divergence.get("never_hidden") is not True:
        errors.append("divergence.never_hidden must be true — a correction never hides a divergence")
    if record_id and record_id not in text:
        errors.append("divergence.report_text must name the record id")
    statement = record.get("authority", {}).get("statement", "")
    if statement and statement.rstrip(".") not in text:
        errors.append("divergence.report_text must quote authority.statement verbatim")

    regulation = record.get("authority", {}).get("regulation_reference", "")
    if not regulation.strip():
        errors.append("authority.regulation_reference is empty; say "
                      f"{HONEST_GAP!r} rather than leaving a gap silent")

    checks = divergence.get("checks_that_must_report_it", [])
    unproven = [c.get("check") for c in checks if not c.get("proven")]
    if record.get("status") == "verified" and unproven:
        errors.append("status is 'verified' while these checks are unproven: "
                      + ", ".join(map(str, unproven)))
    if record.get("status") == "verified" and "not yet verified" in record.get("verified_by", {}).get("status", ""):
        errors.append("status is 'verified' but verified_by.status still says it is not")

    for assertion in record.get("expected_effect", {}).get("assertions", []):
        if not assertion.get("re_derived_from", "").strip():
            errors.append(f"expected_effect {assertion.get('id')}: no re-derivation source")
        if record.get("status") == "declared" and assertion.get("status") == "held":
            errors.append(f"expected_effect {assertion.get('id')}: cannot be 'held' "
                          "while the record itself is only 'declared'")

    for item in record.get("evidence", []):
        if not item.get("reproduce", "").strip():
            errors.append(f"evidence {item.get('claim','')[:40]!r}: no reproduce step")

    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dir", default=str(pathlib.Path(__file__).resolve().parent))
    args = parser.parse_args()
    directory = pathlib.Path(args.dir)
    schema = load_schema(directory)

    records = sorted(p for p in (directory / "evidence").glob("C*-evidence.json"))
    if not records:
        print("no evidence records found under evidence/")
        return 1

    failed = 0
    for path in records:
        try:
            record = json.loads(path.read_text())
        except json.JSONDecodeError as exc:
            print(f"FAIL {path.name}: not valid JSON: {exc}")
            failed += 1
            continue
        errors = (check_against_schema(schema, record)
                  + check_invariants(record, path)
                  + check_companion(record, directory))
        if errors:
            failed += 1
            print(f"FAIL {path.name}")
            for error in errors:
                print(f"     {error}")
        else:
            print(f"OK   {path.name}  [{record['id']}] {record['form']['code']} "
                  f"{record['form']['revision']} — status {record['status']}")
    print(f"{len(records) - failed}/{len(records)} record(s) well formed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
